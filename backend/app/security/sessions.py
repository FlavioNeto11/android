"""Sessão do painel: quem está operando, e como o navegador prova isso sem um cabeçalho.

Existe por dois defeitos que são o mesmo defeito visto de dois lados.

**Do lado da auditoria**, `commands.requested_by` dizia `panel` em toda linha — aprovação, cancelamento,
resolução à mão, reset de dados. "Alguém decidiu" sem dizer quem é exatamente o tipo de afirmação vaga que as
fases anteriores existiram para eliminar, e o `API_TOKEN` não ajuda: segredo compartilhado prova conhecimento,
não identidade.

**Do lado do acesso**, o token só viajava em `Authorization`. A API `WebSocket` do navegador não deixa definir
cabeçalho, e `<img src=...>` muito menos — então frame, evidência e avatar não tinham como autenticar. O painel
ficava preso à máquina central, que é o limite que `docs/worker.md` declarava.

Um cookie `HttpOnly` resolve os dois: viaja sozinho em `fetch`, em `<img>` e no handshake do WebSocket, carrega
um nome, e o JavaScript da página não o lê (então um XSS não leva o segredo embora). `SameSite=Strict` é o que
substitui o CSRF token: navegador nenhum manda o cookie num pedido vindo de outro site.

O que guardamos é o **SHA-256** do token, nunca o token: um dump do banco não vira um passe. É a mesma regra do
`secret_store`, pelo mesmo motivo.
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
from contextvars import ContextVar
from datetime import timedelta
from typing import TYPE_CHECKING

from ..util import now, now_iso, parse_iso, to_iso

if TYPE_CHECKING:                                    # pragma: no cover - só para o verificador de tipos
    from ..db import Database

log = logging.getLogger("poc.sessao")

#: Nome do cookie. `__Host-` seria melhor (fixa `Path=/` e exige `Secure`), mas o parque roda em `http://` no
#: loopback por desenho, e ali o prefixo tornaria o cookie inválido — o painel local pararia de funcionar.
COOKIE = "parque_sessao"

#: Validade da sessão. Longa de propósito: o custo de pedir o nome de novo é a pessoa digitar o token outra vez,
#: e o que protege de verdade é o `revoked_at` do "Sair", não um prazo curto que ninguém aguenta.
VALIDADE_S = 30 * 24 * 3600

#: De quanto em quanto tempo `last_seen_at` é reescrito. Sem isto, TODA requisição do painel (e são muitas: frame
#: a cada segundo) viraria um UPDATE no banco só para anotar um instante que ninguém lê com essa precisão.
RENOVA_APOS_S = 300

#: Quem está pedindo ESTA requisição. `ContextVar` e não um global: o backend atende várias requisições no mesmo
#: laço de eventos, e um atributo único faria o nome de uma pessoa aparecer no comando de outra — o mesmo defeito
#: que `worker.agent.COMANDO_ATUAL` já existe para evitar. O middleware o preenche; quem grava auditoria o lê,
#: e por isso nenhuma das dezenas de funções que abrem comando precisou ganhar um parâmetro novo.
OPERADOR: ContextVar[str | None] = ContextVar("operador_do_painel", default=None)


def operador_atual() -> str | None:
    """O nome de quem está pedindo, ou `None` fora de uma requisição com sessão (scheduler, worker, teste)."""
    return OPERADOR.get()


#: Nome do operador: o que aparece na trilha. Sem caractere de controle (ele estragaria o log de uma linha por
#: evento) e curto o bastante para caber numa coluna que alguém vai ler.
_NOME_INVALIDO = re.compile(r"[\x00-\x1f\x7f]")
NOME_MAX = 60


class NomeInvalido(ValueError):
    """O nome recebido não serve para identificar ninguém numa trilha de auditoria."""


def normalizar_nome(bruto: str | None) -> str:
    """`  Ana   Ribeiro ` → `Ana Ribeiro`. Levanta `NomeInvalido` no que não serviria de identidade."""
    nome = " ".join((bruto or "").split())
    if len(nome) < 2:
        raise NomeInvalido("Diga um nome com pelo menos 2 caracteres: é ele que vai aparecer na auditoria.")
    if len(nome) > NOME_MAX:
        raise NomeInvalido(f"O nome do operador não pode passar de {NOME_MAX} caracteres.")
    if _NOME_INVALIDO.search(nome):
        raise NomeInvalido("O nome do operador não pode conter caracteres de controle.")
    return nome


def impressao(token: str) -> str:
    """O que vai para o banco. Função nomeada porque o teste precisa afirmar que o token NÃO está lá."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class PanelSessions:
    """As sessões abertas do painel. Uma linha por navegador que fez login."""

    def __init__(self, db: "Database", *, validade_s: int = VALIDADE_S) -> None:
        self.db = db
        self.validade_s = validade_s

    # ------------------------------------------------------------------ escrita
    def abrir(self, operador: str) -> tuple[str, str]:
        """Cria a sessão e devolve `(token, expira_em)`. O token só existe AQUI e no cookie — nunca no banco."""
        nome = normalizar_nome(operador)
        token = secrets.token_urlsafe(32)
        agora = now()
        expira = to_iso(agora + timedelta(seconds=self.validade_s))
        self.db.execute(
            "INSERT INTO panel_sessions(token_hash, operator, created_at, last_seen_at, expires_at)"
            " VALUES (?,?,?,?,?)",
            (impressao(token), nome, to_iso(agora), to_iso(agora), expira))
        log.info("sessão do painel aberta para %s", nome)
        return token, expira

    def encerrar(self, token: str | None) -> bool:
        """Revoga a sessão do cookie recebido. `False` quando não havia sessão viva — sair duas vezes não é erro."""
        if not token:
            return False
        linha = self.db.one("SELECT operator, revoked_at FROM panel_sessions WHERE token_hash=?", (impressao(token),))
        if linha is None or linha["revoked_at"]:
            return False
        self.db.execute("UPDATE panel_sessions SET revoked_at=? WHERE token_hash=?", (now_iso(), impressao(token)))
        log.info("sessão do painel encerrada por %s", linha["operator"])
        return True

    # ------------------------------------------------------------------ leitura
    def operador_de(self, token: str | None) -> str | None:
        """O nome de quem está por trás do cookie, ou `None` quando o cookie não vale mais.

        Renova a validade (janela deslizante) em no máximo uma escrita a cada `RENOVA_APOS_S`: quem usa o painel
        todo dia nunca é deslogado, quem sumiu por um mês precisa dizer o nome de novo.
        """
        if not token:
            return None
        linha = self.db.one("SELECT * FROM panel_sessions WHERE token_hash=?", (impressao(token),))
        if linha is None or linha["revoked_at"]:
            return None
        agora = now()
        try:
            expira = parse_iso(linha["expires_at"])
        except ValueError:                           # linha corrompida vale como sessão morta, nunca como válida
            return None
        if expira is None or expira <= agora:
            return None
        try:
            visto = parse_iso(linha["last_seen_at"])
        except ValueError:
            visto = None
        if visto is None or (agora - visto).total_seconds() >= RENOVA_APOS_S:
            self.db.execute("UPDATE panel_sessions SET last_seen_at=?, expires_at=? WHERE token_hash=?",
                            (to_iso(agora), to_iso(agora + timedelta(seconds=self.validade_s)), linha["token_hash"]))
        return str(linha["operator"])

    def atual(self, token: str | None) -> dict[str, str] | None:
        """`{operator, expires_at}` da sessão deste cookie, sem renovar nada. Para `GET /api/session` responder
        "quem sou eu" sem que a pergunta, sozinha, estenda a validade."""
        if not token:
            return None
        linha = self.db.one("SELECT operator, expires_at, revoked_at FROM panel_sessions WHERE token_hash=?",
                            (impressao(token),))
        if linha is None or linha["revoked_at"]:
            return None
        try:
            expira = parse_iso(linha["expires_at"])
        except ValueError:
            return None
        if expira is None or expira <= now():
            return None
        return {"operator": str(linha["operator"]), "expires_at": str(linha["expires_at"])}


class PortaoDeLogin:
    """Trava de força bruta do `POST /api/login` e do Bearer em `/api/*`, na memória do processo, POR CLIENTE.

    Na memória e não no banco de propósito: o que ela protege é o `API_TOKEN`, e quem tenta adivinhá-lo fala com
    UM processo. Persistir isso traria escrita no banco a cada tentativa errada — que é exatamente o que um
    atacante consegue provocar de graça.

    Por cliente (29.56): a trava era uma só para o processo, e pelo túnel todo par é `127.0.0.1`; oito chutes de
    qualquer pessoa na internet trancavam o login de fora do dono. A chave vem de `security.access.cliente_de`.
    Sem teto global de propósito: ele devolveria o mesmo defeito por outra porta (quem tem dez IPs tranca todos),
    e contra quem tem muitos IPs a defesa é a entropia do `API_TOKEN` e o limite de taxa da borda.
    """

    #: Quantos clientes o portão acompanha. Um atacante que troca de IP a cada chute não pode crescer a memória sem
    #: limite: passou disto, saem primeiro as chaves sem bloqueio vigente, das mais antigas para as mais novas.
    MAX_CLIENTES = 4096

    def __init__(self, *, limite: int = 8, janela_s: float = 60.0, bloqueio_s: float = 60.0) -> None:
        self.limite = limite
        self.janela_s = janela_s
        self.bloqueio_s = bloqueio_s
        # cliente → (instantes das falhas na janela, bloqueado até). A ordem de inserção é a da última falha.
        self._clientes: dict[str, tuple[list[float], float]] = {}

    def segundos_de_espera(self, agora: float, cliente: str = "") -> float:
        """Quanto falta para ESTE cliente poder tentar de novo. `0` quando o portão está aberto para ele."""
        estado = self._clientes.get(cliente)
        return max(0.0, estado[1] - agora) if estado else 0.0

    def registrar_falha(self, agora: float, cliente: str = "") -> None:
        falhas, ate = self._clientes.pop(cliente, ([], 0.0))
        falhas = [t for t in falhas if agora - t < self.janela_s]
        falhas.append(agora)
        if len(falhas) >= self.limite:
            ate = agora + self.bloqueio_s
            falhas = []
            # O cliente vai no log (é um IP, não um segredo): é o que diz ao dono de onde vieram os chutes.
            log.warning("login do painel bloqueado por %.0f s para %s depois de %d tentativas inválidas",
                        self.bloqueio_s, cliente or "o processo", self.limite)
        self._clientes[cliente] = (falhas, ate)
        if len(self._clientes) > self.MAX_CLIENTES:
            self._podar(agora)

    def registrar_acerto(self, cliente: str = "") -> None:
        self._clientes.pop(cliente, None)

    def _podar(self, agora: float) -> None:
        for chave in [c for c, (_, ate) in self._clientes.items() if ate <= agora]:
            if len(self._clientes) <= self.MAX_CLIENTES:
                return
            del self._clientes[chave]
        while len(self._clientes) > self.MAX_CLIENTES:
            del self._clientes[next(iter(self._clientes))]
