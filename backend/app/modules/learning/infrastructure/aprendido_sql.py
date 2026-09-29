"""A leitura do "Aprendizado desta execução" (`LeituraDoAprendido`) e a composição do serviço que o monta.

Só LEITURA, e só do que já é gravado: o que a execução usou e aprendeu sai das mesmas colunas do voto do D2
(`runs.flow_id`, `flows.source_run_id`, `attempts.recipe_id`, `recipes.learned_from_step`); o que ela mudou no livro, da
trilha e da evidência com o `run_id` dela (`learning_transitions`, `learning_evidence`); o que ela usou das lições, de
`learning_exposures`; e a falha, de `attempts.failure_kind` (o legado sem tipo vai com o erro e o status para o domínio
classificar na leitura, sem gravar). O erro não sai daqui para o painel: só o tipo. Da trilha vêm também o
`decided_by` e o `reason`, só para o domínio dizer se a transição foi do sistema, do voto de uma pessoa ou de uma
pessoa; nenhum dos dois sai no bloco.

SQL portável (SQLite e PostgreSQL): agregados com todas as colunas não agregadas no `GROUP BY`, e o `IN (...)` das
receitas aprendidas em lotes (`receitas_aprendidas`, o mesmo do voto).
"""
from __future__ import annotations

from app.db import Database
from app.modules.learning.application.aprendido import AprendizadoDaExecucao
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.aprendido import (EvidenciaDaExecucao, ExposicaoDaExecucao, FatosDaExecucao,
                                                   TentativaDaExecucao, TransicaoDaExecucao)
from app.modules.learning.infrastructure import linhas
from app.modules.learning.infrastructure.feedback_sql import receitas_aprendidas
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial


class LeituraDoAprendidoSql:
    def __init__(self, db: Database) -> None:
        self._db = db

    def fatos(self, run_id: str) -> FatosDaExecucao | None:
        run = self._db.one("SELECT id, flow_id FROM runs WHERE id=?", (run_id,))
        if run is None:
            return None
        etapas = [linhas.texto(r, "id") for r in self._db.query(
            "SELECT id FROM steps WHERE run_id=? ORDER BY objective_id, plan_version, seq", (run_id,))]
        tentativas = self._db.query(
            "SELECT a.recipe_id, a.failure_kind, a.status, a.error FROM attempts a JOIN steps s ON s.id = a.step_id"
            " WHERE s.run_id=? ORDER BY a.step_id, a.number", (run_id,))
        usadas: list[str] = []
        for t in tentativas:
            receita = linhas.inteiro_ou_nulo(t, "recipe_id")
            if receita is not None and str(receita) not in usadas:
                usadas.append(str(receita))
        return FatosDaExecucao(
            fluxo_usado=linhas.texto_ou_nulo(run, "flow_id"),
            fluxos_aprendidos=tuple(linhas.texto(r, "id") for r in self._db.query(
                "SELECT id FROM flows WHERE source_run_id=? ORDER BY created_at, id", (run_id,))),
            receitas_usadas=tuple(usadas),
            receitas_aprendidas=tuple(str(rid) for rid in receitas_aprendidas(self._db, etapas)),
            transicoes=tuple(TransicaoDaExecucao(
                item_ref=linhas.texto(r, "item_ref"), item_kind=linhas.texto(r, "item_kind"),
                de=linhas.texto_ou_nulo(r, "from_state"), para=linhas.texto(r, "to_state"),
                por=linhas.texto(r, "decided_by"), motivo=linhas.texto(r, "reason"))
                for r in self._db.query("SELECT item_ref, item_kind, from_state, to_state, decided_by, reason"
                                        " FROM learning_transitions WHERE run_id=? ORDER BY id", (run_id,))),
            evidencias=tuple(EvidenciaDaExecucao(
                item_ref=linhas.texto(r, "item_ref"), item_kind=linhas.texto_ou_nulo(r, "item_kind"),
                posicao=linhas.texto(r, "stance"), n=linhas.inteiro(r, "n"))
                for r in self._db.query(
                    "SELECT e.item_ref, e.stance, i.kind AS item_kind, COUNT(*) AS n, MIN(e.id) AS primeira"
                    " FROM learning_evidence e LEFT JOIN learning_items i ON i.id = e.item_ref WHERE e.run_id=?"
                    " GROUP BY e.item_ref, e.stance, i.kind ORDER BY primeira", (run_id,))),
            exposicoes=tuple(ExposicaoDaExecucao(
                item_id=linhas.texto(r, "item_id"), papel=linhas.texto(r, "role"), braco=linhas.texto(r, "arm"))
                for r in self._db.query("SELECT item_id, role, arm, MIN(created_at) AS primeira FROM learning_exposures"
                                        " WHERE run_id=? GROUP BY item_id, role, arm ORDER BY primeira, item_id, role",
                                        (run_id,))),
            tentativas=tuple(TentativaDaExecucao(
                failure_kind=linhas.texto_ou_nulo(t, "failure_kind"), status=linhas.texto_ou_nulo(t, "status"),
                erro=linhas.texto_ou_nulo(t, "error")) for t in tentativas))


def montar_aprendizado(db: object, servico: LearningService) -> AprendizadoDaExecucao | None:
    """O serviço do bloco sobre o banco do central, ou `None` quando o banco ainda não está composto."""
    if not isinstance(db, Database):
        return None
    return AprendizadoDaExecucao(servico, LeituraDoAprendidoSql(db), TriagemDeCredencial())


__all__ = ["LeituraDoAprendidoSql", "montar_aprendizado"]
