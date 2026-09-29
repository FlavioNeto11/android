"""A porta das habilidades para o primeiro escritor real de `skill_validation_results` (043; ADR-054, pacote A5).

Toda escrita passa pelo `SqlSkillRepository` — o livro de aprendizado nunca toca as tabelas das habilidades — e o
ciclo delas vale inteiro: rascunho não recebe prova (`FrozenVersion`), `candidate → validated` pelo sistema só com os
casos da versão aprovados (`ValidationPending` senão) e publicar é sempre de uma pessoa.

O caso é um por VERSÃO (`<skill>:execucao-real-v<n>`, `kind='device'`, `source_kind='run'`, faixa `n..n`), criado na
primeira execução dela: um caso sem faixa passaria a exigir prova real de toda versão futura, e uma versão candidata
quase nunca roda no parque. Dentro da faixa, vale o ÚLTIMO resultado real (a regra P4 de `validation_verdict`). A
observação é idempotente pela execução: o digest da mesma execução, repetido, não grava duas vezes.
"""
from __future__ import annotations

import logging

from app.db import INTEGRITY_ERRORS
from app.modules.learning.application.nativos import ObservacaoDeHabilidade
from app.modules.skills.domain.lifecycle import SYSTEM_ACTOR, SkillError, SkillState
from app.modules.skills.domain.refs import InvalidSkillRef, SkillRef
from app.modules.skills.domain.validation import CaseKind
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository

log = logging.getLogger("poc.aprendizado")


def caso_da_execucao(skill_id: str, versao: int) -> str:
    return f"{skill_id}:execucao-real-v{versao}"


class ValidacaoDeHabilidadesSql:
    def __init__(self, habilidades: SqlSkillRepository) -> None:
        self._habilidades = habilidades

    def registrar(self, observacao: ObservacaoDeHabilidade) -> bool:
        o = observacao
        caso = caso_da_execucao(o.skill_id, o.versao)
        try:
            ref = SkillRef(o.skill_id, o.versao)
            if all(c.id != caso for c in self._habilidades.cases(o.skill_id)):
                try:
                    self._habilidades.add_case(
                        o.skill_id, caso, name=f"execução real da v{o.versao}", kind=CaseKind.DEVICE,
                        expected={"run_status": "completed"}, since_version=o.versao, until_version=o.versao,
                        source_kind="run", source_ref=o.run_id, by=SYSTEM_ACTOR)
                except INTEGRITY_ERRORS:
                    pass                                  # outro digest criou o mesmo caso ao mesmo tempo
            if any(r.case_id == caso and r.run_id == o.run_id for r in self._habilidades.results(ref)):
                return False
            self._habilidades.record_result(ref, caso, proof=o.prova, outcome=o.desfecho, run_id=o.run_id,
                                            instance_id=o.aparelho, detail=o.detalhe, by=SYSTEM_ACTOR)
        except (SkillError, InvalidSkillRef) as exc:
            log.info("aprendizado: execução %s não vira prova de %s@%s: %s", o.run_id, o.skill_id, o.versao, exc)
            return False
        return True

    def validar(self, skill_id: str, versao: int, motivo: str) -> bool:
        try:
            ref = SkillRef(skill_id, versao)
            if self._habilidades.get(ref).state is not SkillState.CANDIDATE:
                return False
            self._habilidades.transition(ref, SkillState.VALIDATED, by=SYSTEM_ACTOR, reason=motivo)
        except (SkillError, InvalidSkillRef):
            return False                                  # ainda falta prova (ValidationPending) ou a versão mudou
        return True


__all__ = ["ValidacaoDeHabilidadesSql", "caso_da_execucao"]
