"""As leituras das métricas do aprendizado (30.8) e da lista de revisões, nos dois dialetos.

Pelas regras de `relatorio_sql.py`: o SQL só filtra por texto ISO-8601, as contas ficam na aplicação e o simulado fica
fora. Das revisões, só as do curador (`template_id = curador`, 30.25): o rótulo de intenção mora na mesma tabela e
não é parecer. A evidência da execução marcada como evidência inválida (30.23) sai fora aqui, com a mesma regra do
detalhe e da saúde (`run_invalidada`, casamento exato do motivo).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime

from app.db import Database, Row
from app.modules.learning.application.curador import TEMPLATE_ID
from app.modules.learning.application.metricas import (ConducaoDaEtapa, EvidenciaLida, PaginaDeRevisoes, RevisaoLida,
                                                       RevisaoNaLista, TransicaoLida)
from app.modules.learning.application.ports import LeituraDaJanela
from app.modules.learning.domain.evidencia_invalida import PREFIXO, run_invalidada
from app.modules.learning.infrastructure import linhas
from app.modules.learning.infrastructure.revisoes_sql import _COLUNAS, RegistroDeRevisoesSql, _revisao
from app.taskqueue.projecao import app_da_etapa

_DO_CURADOR = f"template_id='{TEMPLATE_ID}'"
#: As conduções que o proxy de falhas compara, e os desfechos que contam (o `waiting_user` não é falha nem sucesso).
_CONDUCOES = ("recipe", "recipe+ai", "ai")
_DESFECHOS = ("succeeded", "failed", "uncertain")
_FALHA = frozenset({"failed", "uncertain"})
#: A lista de revisões é varrida em lotes: o filtro `decisao` mora na `saida` (JSON), não numa coluna.
_LOTE_DA_LISTA = 200


def _transicao(r: Row) -> TransicaoLida:
    return TransicaoLida(id=linhas.inteiro(r, "id"), item_ref=linhas.texto(r, "item_ref"),
                         from_state=linhas.texto_ou_nulo(r, "from_state"), to_state=linhas.texto(r, "to_state"),
                         decided_by=linhas.texto(r, "decided_by"), decided_at=linhas.texto(r, "decided_at"))


class FontesDeMetricasSql:
    def __init__(self, db: Database, *, precos: Callable[[], dict[str, list[float]]] | None = None) -> None:
        self._db = db
        self._registro = RegistroDeRevisoesSql(db, precos=precos)

    def transicoes(self, desde: str, ate: str) -> list[TransicaoLida]:
        return [_transicao(r) for r in self._db.query(
            "SELECT id, item_ref, from_state, to_state, decided_by, decided_at FROM learning_transitions"
            " WHERE decided_at >= ? AND decided_at < ? ORDER BY id", (desde, ate))]

    def trilhas(self, item_refs: Iterable[str], ate: str) -> dict[str, list[TransicaoLida]]:
        saida: dict[str, list[TransicaoLida]] = {}
        for lote in linhas.lotes(sorted(set(item_refs))):
            for r in self._db.query(
                    "SELECT id, item_ref, from_state, to_state, decided_by, decided_at FROM learning_transitions"
                    f" WHERE item_ref IN ({linhas.marcas(len(lote))}) AND decided_at < ? ORDER BY id", (*lote, ate)):
                t = _transicao(r)
                saida.setdefault(t.item_ref, []).append(t)
        return saida

    def evidencias(self, desde: str, ate: str) -> list[EvidenciaLida]:
        invalidas = {(linhas.texto(r, "item_ref"), run) for r in self._db.query(
            "SELECT item_ref, reason FROM learning_transitions WHERE reason LIKE ?", (PREFIXO + ":%",))
            if (run := run_invalidada(linhas.texto_ou_nulo(r, "reason"))) is not None}
        # 30.36: a forma não é a favor nem contra, e o `against` que ela corrigiu sai (`linhas.contra_efetivo`)
        return [EvidenciaLida(item_ref=linhas.texto(r, "item_ref"), stance=linhas.texto(r, "stance"),
                              observed_at=linhas.texto(r, "observed_at"))
                for r in self._db.query(
                    "SELECT e.item_ref, e.stance, e.run_id, e.observed_at FROM learning_evidence e WHERE e.observed_at >= ?"
                    f" AND e.observed_at < ? AND e.simulated = 0 AND {linhas.fora_da_reproducao('e')}"
                    f" AND (e.stance = 'for' OR {linhas.contra_efetivo('e')})",
                    (desde, ate))
                if (linhas.texto(r, "item_ref"), linhas.texto_ou_nulo(r, "run_id")) not in invalidas]

    def revisoes(self, desde: str, ate: str) -> list[RevisaoLida]:
        saida: list[RevisaoLida] = []
        revisoes = self._db.query(f"SELECT {_COLUNAS}, usd, ai_call_id, scope_app FROM learning_reviews"
                                  f" WHERE created_at >= ? AND created_at < ? AND {_DO_CURADOR}", (desde, ate))
        custos = self._registro.custos(revisoes)                 # I3: a revisão de antes do 30.30, pela chamada
        for r in revisoes:
            x = _revisao(r)
            saida.append(RevisaoLida(scope_app=linhas.texto(r, "scope_app"), validade=x.validade,
                                     simulated=x.simulated, usd=self._registro.usd(r, custos),
                                     decisao=x.parecer.decisao.value if x.parecer is not None else None,
                                     decisao_final=x.decisao_final, override=x.override, item_ref=x.item_ref))
        return saida

    def conducao(self, desde: str, ate: str) -> list[ConducaoDaEtapa]:
        pacotes = {linhas.texto(r, "id"): linhas.texto_ou_nulo(r, "package") or linhas.texto(r, "id")
                   for r in self._db.query("SELECT id, package FROM apps")}
        saida: list[ConducaoDaEtapa] = []
        for r in self._db.query(
                "SELECT s.template_hash, s.driven_by, s.status, s.app_id, r.app_ids, COUNT(*) AS n FROM steps s"
                " JOIN runs r ON r.id = s.run_id WHERE COALESCE(s.finished_at, s.started_at) >= ?"
                " AND COALESCE(s.finished_at, s.started_at) < ? AND r.simulated = 0 AND s.template_hash IS NOT NULL"
                f" AND s.driven_by IN ({linhas.marcas(len(_CONDUCOES))})"
                f" AND s.status IN ({linhas.marcas(len(_DESFECHOS))})"
                " GROUP BY s.template_hash, s.driven_by, s.status, s.app_id, r.app_ids",
                (desde, ate, *_CONDUCOES, *_DESFECHOS)):
            app = app_da_etapa(r["app_id"], r["app_ids"])
            saida.append(ConducaoDaEtapa(app=pacotes.get(app, app), template_hash=linhas.texto(r, "template_hash"),
                                         driven_by=linhas.texto(r, "driven_by"),
                                         falhou=linhas.texto(r, "status") in _FALHA, n=linhas.inteiro(r, "n")))
        return saida

    def janela_do_curador(self, agora: datetime, dias: int) -> LeituraDaJanela:
        return self._registro.janela(agora, dias)

    def lista_de_revisoes(self, *, app: str | None, decisao: str | None, desde: str | None, limite: int,
                          cursor: tuple[str, str] | None, tambem: Sequence[str] = ()) -> PaginaDeRevisoes:
        """As revisões do curador, da mais nova para a mais velha, `limite` por página. O cursor é (criada, id) da
        última linha devolvida. `tambem`: os itens multi-app que usam `app` sem ser o principal (30.33-C)."""
        filtros, args = [_DO_CURADOR], []
        if app is not None:
            extras = sorted(set(tambem))
            filtros.append(f"(scope_app = ? OR item_ref IN ({linhas.marcas(len(extras))}))" if extras
                           else "scope_app = ?")
            args += [app, *extras]
        if desde is not None:
            filtros.append("created_at >= ?")
            args.append(desde)
        achadas: list[RevisaoNaLista] = []
        while len(achadas) <= limite:
            onde = list(filtros)
            pagina_args = list(args)
            if cursor is not None:
                onde.append("(created_at < ? OR (created_at = ? AND id < ?))")
                pagina_args += [cursor[0], cursor[0], cursor[1]]
            lote = self._db.query(f"SELECT {_COLUNAS}, usd, ai_call_id, scope_app FROM learning_reviews"
                                  f" WHERE {' AND '.join(onde)} ORDER BY created_at DESC, id DESC"
                                  f" LIMIT {_LOTE_DA_LISTA}", tuple(pagina_args))
            custos = self._registro.custos(lote)                 # I3
            for r in lote:
                x = _revisao(r)
                cursor = (x.criado_em, x.id)
                if decisao is None or (x.parecer is not None and x.parecer.decisao.value == decisao):
                    achadas.append(RevisaoNaLista(x, app=linhas.texto(r, "scope_app"),
                                                  usd=self._registro.usd(r, custos)))
                    if len(achadas) > limite:
                        break
            if len(lote) < _LOTE_DA_LISTA:
                break
        proximo = achadas[limite - 1].revisao if len(achadas) > limite else None
        return PaginaDeRevisoes(tuple(achadas[:limite]),
                                (proximo.criado_em, proximo.id) if proximo is not None else None)


__all__ = ["FontesDeMetricasSql"]
