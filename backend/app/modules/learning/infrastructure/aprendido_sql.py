"""A leitura do "Aprendizado desta execução" (`LeituraDoAprendido`) e a composição do serviço que o monta.

Só LEITURA, e só do que já é gravado: o que a execução usou e aprendeu sai das mesmas colunas do voto do D2
(`runs.flow_id`, `flows.source_run_id`, `attempts.recipe_id`, `recipes.learned_from_step`); o que ela mudou no livro, da
trilha e da evidência com o `run_id` dela (`learning_transitions`, `learning_evidence`); o que ela usou das lições, de
`learning_exposures`; e a falha, de `attempts.failure_kind` (o legado sem tipo vai com o erro e o status para o domínio
classificar na leitura, sem gravar). O erro não sai daqui para o painel: só o tipo. Da trilha vêm também o
`decided_by` e o `reason`, só para o domínio dizer se a transição foi do sistema, do voto de uma pessoa ou de uma
pessoa; nenhum dos dois sai no bloco. No nascimento da preferência, o item diz em quantas execuções estava a evidência
que fechou o limiar (`provenance.limiar`): só o número sai, no papel.

SQL portável (SQLite e PostgreSQL): agregados com todas as colunas não agregadas no `GROUP BY`, e o `IN (...)` das
receitas aprendidas em lotes (`receitas_aprendidas`, o mesmo do voto).
"""
from __future__ import annotations

from app.db import Database, Row
from app.modules.learning.application.aprendido import AprendizadoDaExecucao
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.aprendido import (EvidenciaDaExecucao, ExposicaoDaExecucao, FatosDaExecucao,
                                                   TentativaDaExecucao, TransicaoDaExecucao)
from app.modules.learning.domain.vocabulario import LivroKind
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
            "SELECT a.recipe_id, a.failure_kind, a.status, a.error, a.recovery FROM attempts a JOIN steps s ON s.id = a.step_id"
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
                por=linhas.texto(r, "decided_by"), motivo=linhas.texto(r, "reason"), entre=_entre(r, run_id))
                for r in self._db.query(
                    "SELECT t.item_ref, t.item_kind, t.from_state, t.to_state, t.decided_by, t.reason, i.provenance"
                    " FROM learning_transitions t LEFT JOIN learning_items i ON i.id = t.item_ref"
                    " WHERE t.run_id=? ORDER BY t.id", (run_id,))),
            evidencias=tuple(EvidenciaDaExecucao(
                item_ref=linhas.texto(r, "item_ref"), item_kind=linhas.texto_ou_nulo(r, "item_kind"),
                posicao=linhas.texto(r, "stance"), n=linhas.inteiro(r, "n"),
                detalhe=linhas.texto_ou_nulo(r, "detalhe"))
                for r in self._db.query(
                    "SELECT e.item_ref, e.stance, i.kind AS item_kind, COUNT(*) AS n, MIN(e.id) AS primeira,"
                    " MIN(e.detail) AS detalhe"
                    " FROM learning_evidence e LEFT JOIN learning_items i ON i.id = e.item_ref WHERE e.run_id=?"
                    # 30.36/30.42: o `against` que a forma corrigiu e o `for`/`against` que a `invalida` corrigiu saem;
                    # a forma e a `invalida` aparecem com o rótulo delas
                    f" AND (e.stance NOT IN ('for', 'against') OR {linhas.favor_efetivo('e')}"
                    f" OR {linhas.contra_efetivo('e')})"
                    # 30.39: a reprodução da receita não é o que a execução ensinou (os contadores já a contam)
                    f" AND {linhas.fora_da_reproducao('e')}"
                    " GROUP BY e.item_ref, e.stance, i.kind ORDER BY primeira", (run_id,))),
            exposicoes=tuple(ExposicaoDaExecucao(
                item_id=linhas.texto(r, "item_id"), papel=linhas.texto(r, "role"), braco=linhas.texto(r, "arm"))
                for r in self._db.query("SELECT item_id, role, arm, MIN(created_at) AS primeira FROM learning_exposures"
                                        " WHERE run_id=? GROUP BY item_id, role, arm ORDER BY primeira, item_id, role",
                                        (run_id,))),
            tentativas=tuple(TentativaDaExecucao(
                failure_kind=linhas.texto_ou_nulo(t, "failure_kind"), status=linhas.texto_ou_nulo(t, "status"),
                erro=linhas.texto_ou_nulo(t, "error"), recovery=linhas.texto_ou_nulo(t, "recovery"))
                for t in tentativas))


def _entre(r: Row, run_id: str) -> int | None:
    """No nascimento da preferência, em quantas execuções estava a evidência que fechou o limiar
    (`provenance.limiar`, gravado por `preferencias._nascer`) — só se foi ESTA a execução que o fechou. Fora disso,
    ou ilegível, `None`: o papel diz "e de outras", sem número inventado."""
    if linhas.texto_ou_nulo(r, "from_state") is not None or linhas.texto(r, "item_kind") != LivroKind.PREFERENCIA.value:
        return None
    limiar = linhas.json_objeto(r, "provenance").get("limiar")
    if not isinstance(limiar, dict) or limiar.get("run_id") != run_id:
        return None
    n = limiar.get("execucoes")
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 1 else None


def montar_aprendizado(db: object, servico: LearningService) -> AprendizadoDaExecucao | None:
    """O serviço do bloco sobre o banco do central, ou `None` quando o banco ainda não está composto."""
    if not isinstance(db, Database):
        return None
    return AprendizadoDaExecucao(servico, LeituraDoAprendidoSql(db), TriagemDeCredencial())


__all__ = ["LeituraDoAprendidoSql", "montar_aprendizado"]
