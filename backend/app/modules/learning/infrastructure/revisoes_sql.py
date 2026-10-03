"""`RegistroDeRevisoes` sobre `learning_reviews` (069) e a leitura da janela do orçamento do curador (30.11, §8.7).

Custo: a 069 tem `usd REAL NOT NULL DEFAULT 0`; a 075 acrescenta `ai_call_id`, a linha de `ai_calls` que o hub mediu
(30.12). Desde o 30.30 o `usd` medido pelo hub é gravado (a orquestradora derrubou a pendência da rubrica em 03/10: nada
soma `learning_reviews.usd` no `/api/usage` nem no teto do dia, que leem `ai_calls`, então não há dupla contagem). `usd = 0`
continua querendo dizer NÃO MEDIDO (as linhas de antes, a resposta simulada), nunca "de graça": só `usd > 0` entra como
custo medido (c̄ e mediana), e a revisão sem medida entra no gasto da curadoria pela ESTIMATIVA do tamanho do dossiê
gravado (a aplicação a calcula; aqui só se lê o tamanho). O `ai_call_id` liga a revisão à chamada paga, para a auditoria.

I3 (03/10): a revisão sem `usd` gravado e COM `ai_call_id` (as 46 anteriores ao 30.30 no central) tem o custo MEDIDO na
chamada ligada: `custos_das_chamadas` aplica a regra do `/api/usage` (`app.planning.costs`): o custo declarado onde há,
tokens × preço do modelo onde não, provedor simulado a US$ 0. Só sem chamada ligada a revisão fica sem medida (e entra
pela estimativa). Sem `precos` (quem constrói o registro só para gravar ou ler a fila), nada muda.

`G_W` é o gasto de IA da operação: `SUM(learning_daily.usd)` na janela, sem filtro de falha (o relatório filtra
`failure_kind <> ''` porque fala de falhas; o orçamento fala do gasto todo). A curadoria não entra nele: a régua
diária só soma `ai_calls` de tentativas.

O parecer diante da pessoa (30.17): a leitura das revisões do item e da fila, a decisão gravada com CAS
(`decisao_final IS NULL`: dois gestos sobre o mesmo parecer não gravam dois rótulos) e a linha da trilha que a
transição deixou, lida na MESMA transação de quem decide (`transacao`).

Todos os leitores daqui são do CURADOR e filtram `template_id = curador` (30.25): a mesma tabela guarda o rótulo de
intenção (`template_id = intencao`, provedor vazio, `usd = 0`), que não é parecer, não gastou IA e não pode entrar na
janela do orçamento (C_W), na fila do Revisar nem na salvaguarda (item, dossiê) do curador. O rótulo tem o seu
registro (`intencao_sql.py`).
"""
from __future__ import annotations

import json
import secrets
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from app.db import Database, Row
from app.modules.learning.application.curador import TEMPLATE_ID
from app.modules.learning.application.ports import LeituraDaJanela, NovaRevisao, PedidoGravado
from app.modules.learning.domain.parecer import RevisaoGravada, parecer_gravado
from app.modules.learning.domain.politica_de_risco import ClasseDeRisco
from app.modules.learning.domain.vocabulario import SignalKind
from app.modules.learning.infrastructure import linhas
from app.planning import costs
from app.util import to_iso

_COLUNAS = ("id, created_at, item_ref, item_kind, gatilho, validade, classe_de_risco, politica, simulated, provedor,"
            " modelo, dossie, saida, decisao_final, decidido_por, transicao_id, override, override_motivo,"
            " resultado_posterior, resultado_em")
_CLASSES = frozenset(c.value for c in ClasseDeRisco)
#: O filtro de todo leitor do curador (ver o docstring do módulo).
_DO_CURADOR = f"template_id='{TEMPLATE_ID}'"


def _estado_no_dossie(row: Row) -> str | None:
    item = linhas.json_objeto(row, "dossie").get("item")
    estado = item.get("estado") if isinstance(item, dict) else None
    return estado if isinstance(estado, str) else None


def _revisao(row: Row) -> RevisaoGravada:
    validade = linhas.texto(row, "validade")
    classe = linhas.texto_ou_nulo(row, "classe_de_risco")
    return RevisaoGravada(
        id=linhas.texto(row, "id"), criado_em=linhas.texto(row, "created_at"), item_ref=linhas.texto(row, "item_ref"),
        item_kind=linhas.texto(row, "item_kind"), gatilho=linhas.texto(row, "gatilho"), validade=validade,
        classe=ClasseDeRisco(classe) if classe in _CLASSES else None, politica=linhas.texto_ou_nulo(row, "politica"),
        simulated=bool(linhas.inteiro(row, "simulated")), provedor=linhas.texto(row, "provedor"),
        modelo=linhas.texto(row, "modelo"), estado_no_parecer=_estado_no_dossie(row),
        parecer=parecer_gravado(linhas.json_objeto(row, "saida")) if validade == "ok" else None,
        decisao_final=linhas.texto_ou_nulo(row, "decisao_final"),
        decidido_por=linhas.texto_ou_nulo(row, "decidido_por"),
        transicao_id=linhas.inteiro_ou_nulo(row, "transicao_id"), override=bool(linhas.inteiro(row, "override")),
        override_motivo=linhas.texto_ou_nulo(row, "override_motivo"),
        resultado_posterior=linhas.texto_ou_nulo(row, "resultado_posterior"),
        resultado_em=linhas.texto_ou_nulo(row, "resultado_em"))


def custos_das_chamadas(db: Database, ids: Iterable[int], precos: dict[str, list[float]]) -> dict[int, float]:
    """I3: o custo de cada chamada de `ai_calls` pela regra do `/api/usage` (`costs.spent_usd`, linha a linha)."""
    saida: dict[int, float] = {}
    for lote in linhas.lotes(sorted(set(ids))):
        for c in db.query("SELECT id, model, provider, usd, input_tokens, cache_read, cache_write, output_tokens"
                          f" FROM ai_calls WHERE id IN ({linhas.marcas(len(lote))})", tuple(lote)):
            if (linhas.texto_ou_nulo(c, "provider") or "") == "simulated":
                saida[linhas.inteiro(c, "id")] = 0.0
            elif c["usd"] is not None:
                saida[linhas.inteiro(c, "id")] = linhas.real(c, "usd")
            else:
                saida[linhas.inteiro(c, "id")] = costs.row_usd(precos, c)
    return saida


class RegistroDeRevisoesSql:
    def __init__(self, db: Database, *, precos: Callable[[], dict[str, list[float]]] | None = None) -> None:
        self._db = db
        self._precos = precos

    def custos(self, linhas_: Iterable[Row]) -> dict[int, float]:
        """I3: o custo medido pela chamada ligada das revisões SEM `usd` gravado (vazio sem `precos`)."""
        if self._precos is None:
            return {}
        ids = [i for r in linhas_ if linhas.real(r, "usd") <= 0 and (i := linhas.inteiro_ou_nulo(r, "ai_call_id"))]
        return custos_das_chamadas(self._db, ids, self._precos()) if ids else {}

    def usd(self, r: Row, custos: dict[int, float]) -> float:
        """O `usd` gravado; sem ele, o da chamada ligada (I3); sem os dois, 0 = não medido."""
        gravado = linhas.real(r, "usd")
        if gravado > 0:
            return gravado
        chamada = linhas.inteiro_ou_nulo(r, "ai_call_id")
        return custos.get(chamada, 0.0) if chamada is not None else 0.0

    def existe(self, item_ref: str, dossie_hash: str) -> bool:
        return self._db.one(f"SELECT 1 AS x FROM learning_reviews WHERE item_ref=? AND dossie_hash=?"
                            f" AND {_DO_CURADOR}", (item_ref, dossie_hash)) is not None

    def ultima(self, item_ref: str) -> str | None:
        r = self._db.one(f"SELECT MAX(created_at) AS em FROM learning_reviews WHERE item_ref=? AND {_DO_CURADOR}",
                         (item_ref,))
        return linhas.texto_ou_nulo(r, "em") if r is not None else None

    def gravar(self, nova: NovaRevisao, agora: datetime) -> str | None:
        rid = f"lr-{secrets.token_hex(8)}"
        # `ON CONFLICT DO NOTHING` e não `except IntegrityError`: no PostgreSQL o erro abortaria a transação de quem
        # chama. A corrida entre réplicas perde aqui, no INSERT, e não no gasto.
        cur = self._db.execute(
            "INSERT INTO learning_reviews(id, created_at, item_ref, item_kind, scope_app, gatilho, dossie_hash, dossie,"
            " template_id, template_versao, provedor, modelo, simulated, usd, saida, validade, classe_de_risco,"
            " politica, ai_call_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT (item_ref, dossie_hash) DO NOTHING",
            (rid, to_iso(agora), nova.item_ref, nova.item_kind, nova.scope_app, nova.gatilho, nova.dossie_hash,
             json.dumps(nova.dossie, ensure_ascii=False, sort_keys=True), nova.template_id, nova.template_versao,
             nova.provedor, nova.modelo, int(nova.simulated), float(nova.usd or 0.0),
             None if nova.saida is None else json.dumps(nova.saida, ensure_ascii=False, sort_keys=True),
             nova.validade, nova.classe_de_risco, nova.politica, nova.ai_call_id))
        return rid if (cur.rowcount or 0) == 1 else None

    def janela(self, agora: datetime, dias: int) -> LeituraDaJanela:
        inicio = agora - timedelta(days=dias)
        desde, hora, hoje = to_iso(inicio), to_iso(agora - timedelta(hours=1)), agora.strftime("%Y-%m-%d")
        g = self._db.one("SELECT SUM(usd) AS usd FROM learning_daily WHERE day >= ? AND day <= ?",
                         (inicio.strftime("%Y-%m-%d"), hoje))
        medidos: list[float] = []
        sem_medida: list[int] = []
        sem_medida_na_hora: list[int] = []
        gasto = gasto_hora = 0.0
        antes = de_hoje = 0
        # Só o que foi à IA (ou teria ido): a recusada por triagem ou custo não gastou nada.
        revisoes = self._db.query(
            "SELECT created_at, usd, ai_call_id, dossie FROM learning_reviews WHERE created_at >= ? AND provedor <> ''"
            f" AND {_DO_CURADOR}", (desde,))
        custos = self.custos(revisoes)
        for r in revisoes:
            em = linhas.texto(r, "created_at")
            usd = self.usd(r, custos)
            na_hora = em >= hora
            if usd > 0:
                medidos.append(usd)
                gasto += usd
                gasto_hora += usd if na_hora else 0.0
            else:
                tamanho = len(linhas.texto(r, "dossie").encode("utf-8"))
                sem_medida.append(tamanho)
                if na_hora:
                    sem_medida_na_hora.append(tamanho)
            if em[:10] == hoje:
                de_hoje += 1
            else:
                antes += 1
        return LeituraDaJanela(gasto_da_operacao=linhas.real(g, "usd") if g is not None else 0.0,
                               custos_medidos=tuple(medidos), tamanhos_sem_medida=tuple(sem_medida),
                               tamanhos_sem_medida_na_hora=tuple(sem_medida_na_hora), gasto_medido=gasto,
                               gasto_medido_na_hora=gasto_hora, revisoes_antes_de_hoje=antes, revisoes_de_hoje=de_hoje)

    # ------------------------------------------------------------------ o parecer diante da pessoa (30.17)
    @contextmanager
    def transacao(self) -> Iterator[None]:
        """A transição da pessoa e a decisão do parecer juntas, ou nenhuma (o `tx` do banco é reentrante)."""
        with self._db.tx():
            yield

    def uma(self, review_id: str) -> RevisaoGravada | None:
        r = self._db.one(f"SELECT {_COLUNAS} FROM learning_reviews WHERE id=? AND {_DO_CURADOR}", (review_id,))
        return None if r is None else _revisao(r)

    def do_item(self, item_ref: str, limite: int) -> list[RevisaoGravada]:
        """As revisões do item, da mais recente para a mais antiga."""
        return [_revisao(r) for r in self._db.query(
            f"SELECT {_COLUNAS} FROM learning_reviews WHERE item_ref=? AND {_DO_CURADOR}"
            " ORDER BY created_at DESC, id DESC LIMIT ?",
            (item_ref, limite))]

    def sem_decisao(self, item_refs: Iterable[str]) -> dict[str, list[RevisaoGravada]]:
        """As revisões válidas e ainda sem decisão de cada item pedido, da mais recente para a mais antiga (a fila lê
        de uma vez: `IN` em lotes, nunca uma leitura por linha)."""
        saida: dict[str, list[RevisaoGravada]] = {}
        for lote in linhas.lotes(sorted(set(item_refs))):
            for r in self._db.query(
                    f"SELECT {_COLUNAS} FROM learning_reviews WHERE validade='ok' AND decisao_final IS NULL"
                    f" AND {_DO_CURADOR} AND item_ref IN ({linhas.marcas(len(lote))})"
                    " ORDER BY created_at DESC, id DESC", tuple(lote)):
                rev = _revisao(r)
                saida.setdefault(rev.item_ref, []).append(rev)
        return saida

    def do_dossie(self, item_ref: str, dossie_hash: str) -> RevisaoGravada | None:
        r = self._db.one(f"SELECT {_COLUNAS} FROM learning_reviews WHERE item_ref=? AND dossie_hash=?"
                         f" AND {_DO_CURADOR}", (item_ref, dossie_hash))
        return None if r is None else _revisao(r)

    def decidir(self, review_id: str, *, decisao_final: str, decidido_por: str, transicao_id: int | None,
                override: bool, override_motivo: str | None) -> bool:
        """CAS: só a revisão ainda sem decisão. `False` = outro gesto decidiu primeiro (quem chama desfaz a transição)."""
        cur = self._db.execute(
            "UPDATE learning_reviews SET decisao_final=?, decidido_por=?, transicao_id=?, override=?,"
            f" override_motivo=? WHERE id=? AND decisao_final IS NULL AND {_DO_CURADOR}",
            (decisao_final, decidido_por, transicao_id, int(override), override_motivo, review_id))
        return (cur.rowcount or 0) == 1

    def decidir_transicao(self, review_id: str, transicao_id: int | None) -> None:
        """A linha da trilha do aceite, gravada depois da transição (a decisão veio antes, pelo CAS)."""
        self._db.execute("UPDATE learning_reviews SET transicao_id=? WHERE id=?", (transicao_id, review_id))

    def ultima_transicao(self, item_ref: str) -> int:
        """O maior id da trilha do item (0 sem trilha): lido ANTES da transição, marca de onde a dela começa."""
        r = self._db.one("SELECT MAX(id) AS n FROM learning_transitions WHERE item_ref=?", (item_ref,))
        return (linhas.inteiro_ou_nulo(r, "n") or 0) if r is not None else 0

    def transicao_depois(self, item_ref: str, depois_de: int) -> int | None:
        """A PRIMEIRA linha da trilha do item depois da marca: o passo que a pessoa deu a partir do estado que o parecer
        viu (a rota legada pode dar dois, `caminho_da_pessoa`). Na mesma transação, a linha é a de quem decidiu."""
        r = self._db.one("SELECT MIN(id) AS n FROM learning_transitions WHERE item_ref=? AND id>?",
                         (item_ref, depois_de))
        return linhas.inteiro_ou_nulo(r, "n") if r is not None else None

    def pedidos(self, desde: datetime) -> list[PedidoGravado]:
        """Os pedidos de revisão de pessoa (`pediu_revisao`) desde a data, do mais antigo para o mais recente."""
        saida: list[PedidoGravado] = []
        for r in self._db.query("SELECT data, created_at FROM learning_signals WHERE kind=? AND created_at>=?"
                                " ORDER BY created_at, id", (SignalKind.PEDIU_REVISAO.value, to_iso(desde))):
            dados = linhas.json_objeto(r, "data")
            kind, ref, item_ref = dados.get("kind"), dados.get("ref"), dados.get("item_ref")
            if isinstance(kind, str) and isinstance(ref, str) and isinstance(item_ref, str):
                saida.append(PedidoGravado(item_ref=item_ref, kind=kind, ref=ref, em=linhas.texto(r, "created_at")))
        return saida


__all__ = ["RegistroDeRevisoesSql"]
