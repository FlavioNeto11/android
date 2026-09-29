"""O primeiro escritor real de `skill_validation_results` (043; ADR-054, pacote A5): toda execução de versão de
habilidade vira observação no digest, pela porta das habilidades.

- execução real grava `proof=real` num caso `device` da própria versão (`<skill>:execucao-real-v<n>`, faixa `n..n`,
  `source_kind='run'`), criado sozinho na primeira execução; simulada grava `proof=simulated`;
- execução `completed` com etapa confirmada à mão (`verified=false`) grava `uncertain`, nunca `passed`, e não valida;
- o sistema faz `candidate → validated` quando os casos da versão passam, e nunca publica (o ciclo das habilidades
  exige pessoa); publicada segue publicada, com o resultado gravado;
- idempotente pela execução; cancelada não é veredito; fluxo legado (`flow:<id>`) e rascunho não recebem prova;
- o caso de uma versão não trava a validação da seguinte.

Nível de prova: `simulated` (banco de teste migrado pela fábrica da suíte; nenhuma execução real acontece aqui — a
execução "real" é uma linha de `runs` com `simulated=0`).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.db import Database
from app.models import StepResult
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.infrastructure import ligar_nativos
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.infrastructure.validacao_de_skills import caso_da_execucao
from app.modules.skills.domain.lifecycle import SkillState
from app.modules.skills.domain.refs import SkillRef
from app.modules.skills.domain.validation import CaseKind, Outcome, Proof
from app.modules.skills.domain.versions import Provenance, SourceKind
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository
from app.util import now, now_iso

from .fake_skills import banco as banco_migrado
from .fake_skills import documento, repositorio

DONO = "painel:dono"
SKILL = "ig.abrir_perfil"


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.habilidades: SqlSkillRepository = repositorio(db)[0]
        self.decisoes: list[tuple[str, str]] = []
        repo = SqlLearningRepository(db, precos=dict)
        self.servico = LearningService(repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes, relogio=now,
                                       retencao_de_logs_dias=lambda: 14)
        ligar_nativos.ligar(self.servico, repo, db, habilidades=self.habilidades,
                            decidir=lambda texto, run_id: self.decisoes.append((run_id, texto)))

    def versao(self, nota: str = "v1", *, submeter: bool = True) -> SkillRef:
        v = self.habilidades.create_draft(SKILL, documento(SKILL, "abrir o perfil de {username}", nota=nota),
                                          source=Provenance(SourceKind.MANUAL), by=DONO)
        if submeter:
            self.habilidades.transition(v.ref, SkillState.CANDIDATE, by=DONO, reason="submetida")
        return v.ref

    def execucao(self, run_id: str, ref: SkillRef | str, *, status: str = "completed", simulada: bool = False,
                 etapas: tuple[bool, ...] = ()) -> None:
        """`etapas`: o `verified` de cada etapa `succeeded` gravada (`False` = "Confirmar concluído" da pessoa)."""
        skill_id, versao = (ref.skill_id, ref.version) if isinstance(ref, SkillRef) else (ref, 1)
        self.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids,"
                        " created_at, skill_id, skill_version) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (run_id, f"k-{run_id}", "abrir o perfil de @nasa", "execute", status, int(simulada),
                         json.dumps(["android-06"]), now_iso(), skill_id, versao))
        if etapas:
            objetivo = f"{run_id}:android-06"
            self.db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,?)",
                            (objetivo, run_id, "android-06", "succeeded", 1))
            for seq, verificada in enumerate(etapas, start=1):
                texto = "postcondição vista na tela" if verificada else "Confirmado manualmente"
                self.db.execute(
                    "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                    " postcondition, timeout_s, max_attempts, status, result) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"{objetivo}:v1:e{seq}", run_id, objetivo, "android-06", 1, seq, f"e{seq}", "abrir", "abrir",
                     "{}", 60, 3, "succeeded",
                     StepResult(verified=verificada, evidence_text=texto).model_dump_json()))
        relatorio = self.servico.digerir_execucao(run_id)
        assert not relatorio.falhas, relatorio

    def estado(self, ref: SkillRef) -> SkillState:
        return self.habilidades.get(ref).state


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    db = banco_migrado(tmp_path, "nativos.sqlite3")
    yield Mundo(db)
    db.close()


def test_execucao_real_grava_prova_real_num_caso_device_criado_sozinho(mundo: Mundo) -> None:
    ref = mundo.versao()
    mundo.execucao("r-1", ref)
    [caso] = mundo.habilidades.cases(SKILL)
    assert (caso.id, caso.kind, caso.source_kind, caso.source_ref) == (
        caso_da_execucao(SKILL, 1), CaseKind.DEVICE, "run", "r-1")
    assert (caso.since_version, caso.until_version) == (1, 1)
    [r] = mundo.habilidades.results(ref)
    assert (r.proof, r.outcome, r.run_id, r.instance_id, r.observed_by) == (
        Proof.REAL, Outcome.PASSED, "r-1", "android-06", "sistema")
    # o único caso passou: o sistema valida — e para aí (publicar é de uma pessoa)
    assert mundo.estado(ref) is SkillState.VALIDATED
    ultima = mundo.habilidades.history(ref)[-1]
    assert (ultima.to_state, ultima.decided_by) == (SkillState.VALIDATED, "sistema")
    assert any(run == "r-1" and "validada pelo sistema" in t for run, t in mundo.decisoes)
    mundo.execucao("r-2", ref)
    assert mundo.estado(ref) is SkillState.VALIDATED
    assert mundo.habilidades.published(SKILL) is None


def test_execucao_simulada_grava_prova_simulada_e_nao_valida(mundo: Mundo) -> None:
    ref = mundo.versao()
    mundo.execucao("r-1", ref, simulada=True)
    [r] = mundo.habilidades.results(ref)
    assert (r.proof, r.outcome) == (Proof.SIMULATED, Outcome.PASSED)
    assert mundo.estado(ref) is SkillState.CANDIDATE          # caso `device` só conta com prova real (P4)


def test_com_outro_caso_pendente_a_versao_segue_candidata(mundo: Mundo) -> None:
    ref = mundo.versao()
    mundo.habilidades.add_case(SKILL, f"{SKILL}:replay", name="replay", kind=CaseKind.REPLAY, expected={})
    mundo.execucao("r-1", ref)
    assert mundo.estado(ref) is SkillState.CANDIDATE
    assert not any("validada" in t for _, t in mundo.decisoes)


def test_falha_grava_failed_e_cancelada_nao_e_veredito(mundo: Mundo) -> None:
    ref = mundo.versao()
    mundo.execucao("r-1", ref, status="failed")
    mundo.execucao("r-2", ref, status="cancelled")
    mundo.execucao("r-3", ref, status="completed_with_issues")
    assert [(r.run_id, r.outcome) for r in mundo.habilidades.results(ref)] == [
        ("r-1", Outcome.FAILED), ("r-3", Outcome.UNCERTAIN)]
    assert mundo.estado(ref) is SkillState.CANDIDATE


def test_etapa_confirmada_a_mao_nao_e_prova_positiva(mundo: Mundo) -> None:
    """"Confirmar concluído" fecha a execução `completed` com a etapa `verified=false`: a pessoa decidiu, a tela não
    comprovou. Incerteza nunca conta como sucesso — a observação real fica gravada como `uncertain` e a versão segue
    candidata; só a execução seguinte, com toda etapa comprovada pela tela, a valida."""
    ref = mundo.versao()
    mundo.execucao("r-1", ref, etapas=(True, False))
    [r] = mundo.habilidades.results(ref)
    assert (r.proof, r.outcome, r.run_id) == (Proof.REAL, Outcome.UNCERTAIN, "r-1")
    assert "confirmada à mão" in (r.detail or "")
    assert mundo.estado(ref) is SkillState.CANDIDATE
    assert not any("validada" in t for _, t in mundo.decisoes)
    mundo.execucao("r-2", ref, etapas=(True, True))
    assert [(r.run_id, r.outcome) for r in mundo.habilidades.results(ref)] == [
        ("r-1", Outcome.UNCERTAIN), ("r-2", Outcome.PASSED)]
    assert mundo.estado(ref) is SkillState.VALIDATED


def test_etapa_confirmada_a_mao_nao_muda_falha_nem_prova_simulada(mundo: Mundo) -> None:
    """A regra só tira o sucesso: falha com etapa confirmada à mão segue `failed`, e a simulada segue `simulated`."""
    ref = mundo.versao()
    mundo.execucao("r-1", ref, status="failed", etapas=(False,))
    mundo.execucao("r-2", ref, simulada=True, etapas=(False,))
    assert [(r.run_id, r.proof, r.outcome) for r in mundo.habilidades.results(ref)] == [
        ("r-1", Proof.REAL, Outcome.FAILED), ("r-2", Proof.SIMULATED, Outcome.UNCERTAIN)]
    assert mundo.estado(ref) is SkillState.CANDIDATE


def test_idempotente_legado_e_rascunho_nao_recebem_prova(mundo: Mundo) -> None:
    ref = mundo.versao()
    mundo.execucao("r-1", ref, simulada=True)
    mundo.servico.digerir_execucao("r-1")                      # o digest repetido da mesma execução
    assert len(mundo.habilidades.results(ref)) == 1
    mundo.execucao("r-2", "flow:abrir-perfil")                 # fluxo legado: não é versão de `skill_versions`
    rascunho = mundo.versao("v2", submeter=False)
    mundo.execucao("r-3", rascunho)                            # rascunho não recebe prova (o conteúdo ainda muda)
    assert mundo.habilidades.results(rascunho) == []
    assert mundo.db.scalar("SELECT COUNT(*) FROM skill_validation_results") == 1


def test_publicada_segue_publicada_e_o_caso_de_uma_versao_nao_trava_a_seguinte(mundo: Mundo) -> None:
    v1 = mundo.versao()
    mundo.execucao("r-1", v1)
    mundo.habilidades.transition(v1, SkillState.PUBLISHED, by=DONO, reason="aprovada")
    mundo.execucao("r-2", v1, status="failed")                 # "deu errado" vira resultado, nunca desliga sozinho
    assert mundo.estado(v1) is SkillState.PUBLISHED
    assert [r.outcome for r in mundo.habilidades.results(v1)] == [Outcome.PASSED, Outcome.FAILED]
    # a v2 tem o caso dela; o da v1 (faixa 1..1) não vale para ela
    v2 = mundo.versao("v2")
    mundo.execucao("r-3", v2)
    assert mundo.estado(v2) is SkillState.VALIDATED
    assert {c.id for c in mundo.habilidades.cases(SKILL)} == {caso_da_execucao(SKILL, 1), caso_da_execucao(SKILL, 2)}
