"""Fase G (G2): as peças que ligam o registro de habilidades à execução, sem aparelho.

- `DslDocumentValidator`: a porta `DocumentValidator` de verdade (schema `automation/v1alpha1` + compilador);
- `LockedVersions`: a trava de composição lida das versões gravadas;
- `SkillRunPlanner`: RESOLVE + COMPILE, o que `_plan`, `apps_exigidos` e `/api/flows/match` perguntam;
- um só hash canônico (documento, trava, IR e plano);
- as guardas da adoção e do religamento de fluxo.

Nível de prova: `simulated` (banco de teste, catálogo em código). Nenhum aparelho, nenhuma IA.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.db import Database
from app.modules.skills.application.ports import DocumentValidator
from app.modules.skills.application.registry import CompositeSkillRegistry
from app.modules.skills.domain import document, ir
from app.modules.skills.domain.errors import Code
from app.modules.skills.domain.lifecycle import SYSTEM_ACTOR, InvalidDocument, SkillState
from app.modules.skills.domain.refs import SkillRef
from app.modules.skills.domain.validation import CaseKind, Outcome, Proof
from app.modules.skills.domain.versions import Provenance, SourceKind
from app.modules.skills.infrastructure.document_validator import DslDocumentValidator, LockedVersions
from app.modules.skills.infrastructure.legacy_flows import LegacyFlowAdapter
from app.modules.skills.infrastructure.run_planning import SkillRunPlanner
from app.modules.skills.infrastructure.sql_repository import SkillsDisabled, SqlSkillRepository
from app.planning.catalog.instagram import PACKAGE
from app.taskqueue.flows import FlowStore

from .fake_skills import Relogio, banco, fluxo

FIXTURES = Path(__file__).parent / "fixtures" / "dsl" / "v1alpha1" / "validos"
PESSOA = "painel:flavio"
ABRIR, LER = "ig.abrir_conversa", "ig.ler_conversa"


def carregar(nome: str) -> dict[str, Any]:
    dado = yaml.safe_load((FIXTURES / f"{nome}.yaml").read_text(encoding="utf-8"))
    assert isinstance(dado, dict)
    return dado


class Mundo:
    """O que o `AppState` compõe (fase G), sobre um banco de teste: repositório com o validador de verdade, a trava
    de composição lendo o próprio repositório, o registro com os dois backends e o planejador da execução."""

    def __init__(self, db: Database, *, skills: bool = True, flows: bool = False) -> None:
        self.db = db
        self.skills_on, self.flows_on = skills, flows
        if db.one("SELECT id FROM apps WHERE id='instagram'") is None:
            db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',?,0)", (PACKAGE,))
        travas = LockedVersions(lambda ref: self.repo.get(ref))
        self.validador = DslDocumentValidator(self.pacote, travas)
        self.repo = SqlSkillRepository(db, self.validador, clock=Relogio(), adoption_enabled=lambda: self.skills_on)
        self.registro = CompositeSkillRegistry(self.repo, LegacyFlowAdapter(db), skills_enabled=lambda: self.skills_on,
                                               flows_enabled=lambda: self.flows_on)
        self.planejador = SkillRunPlanner(self.registro, self.pacote, travas)

    def pacote(self, app_id: str) -> str | None:
        return self.db.scalar("SELECT package FROM apps WHERE id=?", (app_id,))

    def publicar(self, nome: str, *, manual: bool = False) -> SkillRef:
        """draft → candidate → validated → published, como o domínio exige (P4, ver `test_fatia_abrir_conversa`)."""
        v = self.repo.create_draft(nome, carregar(nome), source=Provenance(SourceKind.MANUAL), by=PESSOA)
        self.repo.transition(v.ref, SkillState.CANDIDATE, by=PESSOA, reason="submetida")
        if manual:
            self.repo.transition(v.ref, SkillState.VALIDATED, by=PESSOA, reason="validação manual do dono", manual=True)
        else:
            self.repo.add_case(nome, f"{nome}:sim", name="simulado", kind=CaseKind.SIMULATED, expected={})
            self.repo.record_result(v.ref, f"{nome}:sim", proof=Proof.SIMULATED, outcome=Outcome.PASSED)
            self.repo.transition(v.ref, SkillState.VALIDATED, by=SYSTEM_ACTOR, reason="")
        self.repo.transition(v.ref, SkillState.PUBLISHED, by=PESSOA, reason="publicar")
        return v.ref


@pytest.fixture
def db(tmp_path: Path) -> Any:
    d = banco(tmp_path)
    yield d
    d.close()


# ================================================================== um só hash
def test_um_so_hash_canonico_para_documento_trava_ir_e_plano() -> None:
    assert ir.content_hash is document.content_hash and ir.canonical_json is document.canonical_json
    doc = carregar(ABRIR)
    assert ir.content_hash(doc) == document.content_hash(doc)


# ================================================================== validador de verdade
def test_o_validador_cumpre_a_porta() -> None:
    esperado = [(p.name, p.kind, p.default) for p in inspect.signature(DocumentValidator.inspect).parameters.values()]
    real = [(p.name, p.kind, p.default) for p in inspect.signature(DslDocumentValidator.inspect).parameters.values()]
    assert real == esperado


def test_o_validador_le_os_fatos_do_documento(db: Database) -> None:
    fatos = Mundo(db).validador.inspect(carregar(ABRIR))
    assert (fatos.skill_id, fatos.name, fatos.app_id) == (ABRIR, "Abrir conversa no Instagram", "instagram")
    assert fatos.command_template == "abra a conversa com {username} no instagram"
    assert fatos.app_ids == ("instagram",) and fatos.errors == () and fatos.schema_version == 1


def test_rascunho_com_erro_se_salva_mas_nao_submete(db: Database) -> None:
    m = Mundo(db)
    doc = carregar(ABRIR)
    doc["spec"]["nodes"][1]["capability"] = "NAO_EXISTE"
    fatos = m.validador.inspect(doc)
    assert fatos.skill_id == ABRIR and fatos.errors and fatos.errors[0].startswith("E_UNKNOWN_CAPABILITY")
    v = m.repo.create_draft(ABRIR, doc, source=Provenance(SourceKind.MANUAL), by=PESSOA)     # trabalho em andamento
    with pytest.raises(InvalidDocument) as exc:
        m.repo.transition(v.ref, SkillState.CANDIDATE, by=PESSOA, reason="submeter")
    assert any("E_UNKNOWN_CAPABILITY" in e for e in exc.value.errors)
    assert m.repo.get(v.ref).state is SkillState.DRAFT


def test_composta_so_submete_com_a_filha_congelada(db: Database) -> None:
    """`uses:` trava a versão EXATA; filha em rascunho não se compõe (o conteúdo ainda muda)."""
    m = Mundo(db)
    filha = m.repo.create_draft(ABRIR, carregar(ABRIR), source=Provenance(SourceKind.MANUAL), by=PESSOA)
    composta = m.repo.create_draft(LER, carregar(LER), source=Provenance(SourceKind.MANUAL), by=PESSOA)
    with pytest.raises(InvalidDocument) as exc:
        m.repo.transition(composta.ref, SkillState.CANDIDATE, by=PESSOA, reason="submeter")
    assert any(e.startswith(Code.E_SKILL_NOT_FOUND.value) for e in exc.value.errors)
    m.repo.transition(filha.ref, SkillState.CANDIDATE, by=PESSOA, reason="submeter")
    assert m.repo.transition(composta.ref, SkillState.CANDIDATE, by=PESSOA, reason="submeter").state \
        is SkillState.CANDIDATE
    trava = LockedVersions(m.repo.get).locked(ABRIR, 1)
    assert trava is not None and trava.content_hash == m.repo.get(filha.ref).content_hash
    assert LockedVersions(m.repo.get).locked("flow:x", 1) is None                 # fluxo legado não se compõe


# ================================================================== RESOLVE + COMPILE
def test_skill_publicada_vira_o_plano_compilado_com_os_valores_do_comando(db: Database) -> None:
    m = Mundo(db)
    ref = m.publicar(ABRIR)
    rp = m.planejador.for_command("abra a conversa com @ana no instagram", None)
    assert rp is not None and rp.ok and rp.plan is not None
    assert (rp.skill_id, rp.skill_version, rp.legacy_flow_id) == (ABRIR, 1, None)
    assert rp.skill_hash == m.repo.get(ref).content_hash
    plano = rp.plan
    assert plano.planner.provider == "skill" and plano.planner.model == "skill:ig.abrir_conversa@1"
    assert plano.parameters == {"username": "@ana"} and plano.required_apps == ["instagram"]
    assert [(s.key, s.capability, s.depends_on) for s in plano.steps] == [
        ("abrir_inbox", "OPEN_INBOX", []), ("abrir_conversa", "OPEN_THREAD", ["abrir_inbox"])]
    assert all(s.origin is not None and (s.origin.skill_id, s.origin.skill_version, s.origin.node_id)
               == (ABRIR, 1, s.key) for s in plano.steps)
    # com o interruptor desligado, a mesma skill publicada não casa (P1)
    m.skills_on = False
    assert m.planejador.for_command("abra a conversa com @ana no instagram", None) is None


def test_composta_liga_a_leitura_a_conversa_aberta_pelo_filho(db: Database) -> None:
    m = Mundo(db)
    m.publicar(ABRIR)
    m.publicar(LER, manual=True)
    rp = m.planejador.for_command("leia a conversa com @ana no instagram", None)
    assert rp is not None and rp.plan is not None
    passos = {s.key: s for s in rp.plan.steps}
    assert list(passos) == ["abrir_abrir_inbox", "abrir_abrir_conversa", "ler"]
    assert passos["ler"].depends_on == ["abrir_abrir_conversa"]
    assert passos["abrir_abrir_conversa"].bindings == {"username": "{contato}"}
    assert rp.plan.parameters == {"contato": "@ana"}
    assert {k: (s.origin.skill_id if s.origin else None) for k, s in passos.items()} == {
        "abrir_abrir_inbox": ABRIR, "abrir_abrir_conversa": ABRIR, "ler": LER}
    assert rp.skill_id == LER


def test_filha_desabilitada_depois_de_publicada_para_a_composta_sem_plano_parcial(db: Database) -> None:
    """`disabled` é a parada de emergência: vale também para quem compõe. A execução recebe os erros, nunca um
    plano pela metade."""
    m = Mundo(db)
    filha = m.publicar(ABRIR)
    m.publicar(LER, manual=True)
    m.repo.transition(filha, SkillState.DISABLED, by=PESSOA, reason="parada de emergência")
    rp = m.planejador.for_command("leia a conversa com @ana no instagram", None)
    assert rp is not None and not rp.ok and rp.plan is None
    assert [i.code for i in rp.issues] == [Code.E_SKILL_NOT_FOUND]
    assert set(rp.issues[0].as_dict()) == {"code", "message", "path", "severity"}


def test_fluxo_legado_pelo_planejador_e_o_mesmo_plano_do_flowstore(db: Database) -> None:
    m = Mundo(db, skills=False, flows=True)
    fluxo(db, "curtir", "curtir o post de {perfil}")
    comando = "curtir o post de @nasa"
    rp = m.planejador.for_command(comando, None)
    casado = FlowStore(db).match(comando)
    assert rp is not None and rp.plan is not None and casado is not None
    assert rp.plan == casado[1]
    assert (rp.skill_id, rp.skill_version, rp.legacy_flow_id) == (None, None, "curtir")
    assert rp.skill_hash == LegacyFlowAdapter(db).get(SkillRef.legacy("curtir")).content_hash
    m.flows_on = False
    assert m.planejador.for_command(comando, None) is None


# ================================================================== guardas (G2, apontadas pela fase D)
def test_adotar_fluxo_com_as_habilidades_desligadas_e_recusado(db: Database) -> None:
    m = Mundo(db, skills=False, flows=True)
    fluxo(db, "curtir", "curtir o post de {perfil}")
    with pytest.raises(SkillsDisabled):
        m.repo.adopt_flow("curtir", skill_id="ig.curtir", by=PESSOA)
    assert db.scalar("SELECT status FROM flows WHERE id='curtir'") == "active"         # nada mudou
    assert m.repo.definition("ig.curtir") is None
    m.skills_on = True
    m.repo.adopt_flow("curtir", skill_id="ig.curtir", by=PESSOA)
    adotante = m.repo.published_adopter("curtir")
    assert adotante is not None and str(adotante.ref) == "ig.curtir@1"
    m.repo.release_flow("ig.curtir", by=PESSOA, reason="voltar ao fluxo")
    assert m.repo.published_adopter("curtir") is None
