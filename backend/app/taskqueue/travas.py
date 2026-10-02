"""Trava de líder dos laços periódicos (item 28.1; design `docs/design/pedidos-persistentes.md` §7.3).

O problema. A retenção, a curadoria do aprendizado e o livro-caixa das contas de IA rodam em todo backend que tem o
scheduler (`ROLE=all/scheduler`). Com dois desses no mesmo banco, cada um roda o seu: duas consultas ao relatório
pago do provedor e — o que não é só desperdício — dois "fechamentos do dia" na mesma conta. O papel do processo era a
única coisa que impedia o laço em dobro.

A forma. Uma linha por trava (`travas.nome`), no molde das vagas de IA (`ai_slots.py`): tomada por compare-and-swap

    UPDATE travas SET dono=?, …, token=token+1 WHERE nome=? AND (dono IS NULL OR expira_em < agora)

e nunca por "leia quem é o dono e decida" — em READ COMMITTED dois backends leriam o mesmo "livre". O prazo existe
para sobreviver à queda do líder (Kubernetes, Leases): sem renovação, a trava vence sozinha e outro assume. O tempo é
o relógio do BANCO (`Database.agora`, item 5.3), injetável nos testes: com o relógio de cada máquina, um backend
adiantado tomaria a trava viva alheia.

O token é a cerca (Kleppmann). Lease sozinho não basta: uma pausa longa (GC, máquina suspensa, thread presa) vence
a trava sem o dono saber, outro assume, e o primeiro acorda e escreve achando que ainda manda. Cada mandato novo
recebe `token + 1`; quem escreve algo que não é idempotente por construção faz a escrita dentro de `cercada()`, que
confere, NA MESMA transação da escrita, que o mandato ainda é o dele — e recusa com `TravaPerdida` se não for. Fora
dessa escrita a trava é por eficiência; a correção vem das chaves únicas e das escritas idempotentes.

Desvio consciente do §7.3: a renovação não mora no `Scheduler._manter_posse`, e sim num laço próprio do `AppState`
(`RENOVAR_TRAVA_S`, mesma cadência de 20 s e prazo de 120 s da posse de etapa). As travas são do `AppState`, que é
quem tem os laços; prendê-las ao tick do scheduler acoplaria a fila a laços que não são dela.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from ..db import Database
from ..util import to_iso

log = logging.getLogger("poc.travas")

#: Validade de um mandato sem renovação. Os números da posse de etapa, pelo mesmo motivo: errar para o lado de não
#: haver dois líderes. O preço de um prazo longo é o laço ficar parado até 2 min quando o líder cai sem sair.
TRAVA_TTL_S = 120.0
#: De quanto em quanto tempo o líder renova (e o seguidor tenta assumir a trava vencida).
RENOVAR_TRAVA_S = 20.0

#: As travas dos laços de `state.py` que não são idempotentes por construção (§7.3). Outbox (filtrado por
#: `hosted_by`), loja (por aparelho hospedado), saúde e métricas (por processo) não precisam.
SALDOS = "saldos"
CURADORIA = "curadoria"
RETENCAO = "retencao"
TRAVAS_DOS_LACOS = (SALDOS, CURADORIA, RETENCAO)


class TravaPerdida(RuntimeError):
    """A escrita cercada encontrou outro mandato (ou nenhum): quem a pediu deixou de ser o líder."""


class Lideranca:
    """As travas de líder deste backend, no banco. Um objeto por processo.

    `dono` é o `OWNER_ID` — a máquina, não o PID (`Config.owner_id`). Consequência aceita, a mesma das vagas de IA:
    um backend que reinicia com o mesmo `OWNER_ID` trata o que ficou no nome dele como resto da própria queda
    (`soltar_da_queda`) e não espera o prazo vencer.
    """

    def __init__(self, db: Database, *, dono: str, ttl_s: float = TRAVA_TTL_S,
                 relogio: Callable[[], datetime] | None = None):
        self.db = db
        self.dono = dono
        self.ttl_s = ttl_s
        #: O relógio do banco por padrão. Atributo (e não só parâmetro) para o teste trocar o relógio de um
        #: `AppState` já de pé — os dois "processos" do teste dividem o mesmo relógio falso, como dividiriam o banco.
        self.relogio: Callable[[], datetime] = relogio if relogio is not None else db.agora
        #: nome → token do mandato que ESTE processo acredita ter. A crença é conferida no banco a cada uso.
        self._tokens: dict[str, int] = {}

    def _instantes(self) -> tuple[str, str]:
        agora = self.relogio()
        return to_iso(agora), to_iso(agora + timedelta(seconds=self.ttl_s))

    # ------------------------------------------------------------------ tomada e renovação
    def tomar(self, nome: str) -> int | None:
        """Tenta ser o líder de `nome`. Devolve o token do mandato, ou `None` quando outro backend vivo o tem.

        Idempotente: quem já é o líder só renova o prazo e recebe o MESMO token — o token só cresce em mandato novo,
        senão a renovação invalidaria as escritas cercadas do próprio líder.
        """
        agora, vence = self._instantes()
        meu = self._tokens.get(nome)
        if meu is not None:
            # Renova mesmo vencido: se o token ainda é o meu, ninguém teve mandato no intervalo, e continuar é seguro.
            cur = self.db.execute("UPDATE travas SET expira_em=? WHERE nome=? AND dono=? AND token=?",
                                  (vence, nome, self.dono, meu))
            if (cur.rowcount or 0) == 1:
                return meu
            self._tokens.pop(nome, None)
            log.warning("trava %s: o mandato %d deste backend (%s) foi tomado por outro", nome, meu, self.dono)
        with self.db.tx():
            # O UPDATE e a leitura do token na mesma transação: no SQLite o `BEGIN IMMEDIATE` já segura a escrita; no
            # PostgreSQL a linha fica presa pelo UPDATE até o COMMIT. Ninguém assume entre um e outro.
            cur = self.db.execute(
                "UPDATE travas SET dono=?, expira_em=?, tomada_em=?, token=token+1"
                " WHERE nome=? AND (dono IS NULL OR expira_em IS NULL OR expira_em < ?)",
                (self.dono, vence, agora, nome, agora))
            if (cur.rowcount or 0) == 1:
                token = int(self.db.scalar("SELECT token FROM travas WHERE nome=?", (nome,)))
            else:
                # A linha nasce sob demanda (a Fase 28 acrescenta travas sem migração nova). `ON CONFLICT DO NOTHING`
                # e não `except IntegrityError`: no PostgreSQL o erro abortaria a transação inteira.
                cur = self.db.execute(
                    "INSERT INTO travas(nome, dono, token, expira_em, tomada_em) VALUES (?,?,1,?,?)"
                    " ON CONFLICT (nome) DO NOTHING", (nome, self.dono, vence, agora))
                if (cur.rowcount or 0) != 1:
                    return None            # a linha existe e o mandato é de outro backend vivo
                token = 1
        self._tokens[nome] = token
        log.info("trava %s: mandato %d assumido por %s", nome, token, self.dono)
        return token

    def manter(self, nomes: Iterable[str]) -> dict[str, int | None]:
        """Renova o que é meu e assume o que venceu. Chamado a cada `RENOVAR_TRAVA_S` pelo laço do `AppState`."""
        return {nome: self.tomar(nome) for nome in nomes}

    # ------------------------------------------------------------------ cerca
    def exigir(self, nome: str, token: int) -> None:
        """Confere que o mandato `token` ainda vale. Só é cerca DENTRO da transação da escrita (`cercada`).

        `FOR UPDATE` no PostgreSQL: prende a linha até o COMMIT, então uma tomada concorrente espera a escrita
        terminar em vez de acontecer no meio dela. No SQLite o `BEGIN IMMEDIATE` de `tx()` já serializa escritores.
        """
        agora, _ = self._instantes()
        sql = "SELECT token FROM travas WHERE nome=? AND dono=? AND token=? AND expira_em > ?"
        if self.db.dialect == "postgres":
            sql += " FOR UPDATE"
        if self.db.one(sql, (nome, self.dono, int(token), agora)) is None:
            raise TravaPerdida(f"trava {nome}: o mandato {token} de {self.dono} não vale mais")

    @contextmanager
    def cercada(self, nome: str, token: int) -> Iterator[None]:
        """Transação cuja escrita só acontece se o mandato `token` ainda for o vigente (fencing token)."""
        with self.db.tx():
            self.exigir(nome, token)
            yield

    # ------------------------------------------------------------------ devolução
    def soltar(self, nome: str) -> bool:
        """Saída limpa: devolve a trava sem esperar o prazo. NUNCA apaga a linha nem zera o token — o próximo
        mandato tem de receber um número maior que todos os anteriores, ou a cerca deixa o escritor velho passar."""
        meu = self._tokens.pop(nome, None)
        if meu is None:
            return False
        cur = self.db.execute("UPDATE travas SET dono=NULL, expira_em=NULL WHERE nome=? AND dono=? AND token=?",
                              (nome, self.dono, meu))
        return (cur.rowcount or 0) == 1

    def soltar_todas(self) -> int:
        return sum(1 for nome in list(self._tokens) if self.soltar(nome))

    def soltar_da_queda(self) -> int:
        """Na partida: devolve as travas que ficaram no nome deste backend antes de uma queda.

        Sem isto, o central que cai e volta (mesmo `OWNER_ID`) encontraria as próprias travas vivas por até
        `TRAVA_TTL_S` — e a primeira volta da retenção, que só se repete em 6 h, seria pulada. Só a partida chama:
        um processo vivo nunca solta o que acredita ter (`_tokens` vazio aqui).
        """
        self._tokens.clear()
        cur = self.db.execute("UPDATE travas SET dono=NULL, expira_em=NULL WHERE dono=?", (self.dono,))
        n = int(cur.rowcount or 0)
        if n:
            log.info("partida: %d trava(s) de líder soltas de uma execução anterior deste backend", n)
        return n
