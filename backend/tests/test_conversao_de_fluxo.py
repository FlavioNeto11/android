"""Fase J: conversão fluxo → habilidade (adopt-on-write, design §15.2) e o caminho de volta, sem aparelho.

- converter = adotar (v1 publicada com o plano do fluxo, fluxo desligado) + rascunho v2 com o documento
  descompilado, numa transação; o que a ida e volta não reproduz recusa tudo e não deixa nada;
- desfazer devolve o fluxo EXATAMENTE como era (linha, escopo, apps exigidos) e apaga o rascunho da conversão;
- com `skills.enabled` desligado, converter é recusado (repositório e rota);
- a v1 → v2 de quem adotou antes da fase J;
- as rotas (`/api/flows/{id}/adopt`, `/release`, `/api/skills/{id}/versions/{n}/decompile`) com o `_http` do roteador.

Nível de prova: `simulated` (banco de teste, catálogo em código; Harness na porta 5640 para as rotas).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.db import Database
from app.modules.skills.domain.lifecycle import DuplicateCommand, InvalidDocument, SkillState, StateConflict
from app.modules.skills.domain.refs import SkillRef
from app.modules.skills.domain.versions import SCHEMA_DSL_V1, SCHEMA_LEGACY_PLAN, Provenance, SourceKind
from app.modules.skills.infrastructure.decompiler import PlanDecompiler
from app.modules.skills.infrastructure.document_validator import DslDocumentValidator, LockedVersions
from app.modules.skills.infrastructure.flow_conversion import FlowConverter
from app.modules.skills.infrastructure.legacy_flows import legacy_content, required_apps
from app.modules.skills.infrastructure.lowering import SkillPlanCompiler
from app.modules.skills.infrastructure.sql_repository import SkillsDisabled, SqlSkillRepository
from app.planning.capabilities import CapabilityNode
from app.taskqueue.flows import FlowStore

from .conftest import Harness
from .fake_skills import Relogio, banco
from .test_descompilador import abrir_conversa, aprendido, plano_por_catalogo, treino
from .test_habilidades_na_execucao import ABRIR, carregar

PACKAGE = "com.instagram.android"
DONO = "painel:flavio"
SKILL = "instagram.abrir-a-conversa-com-ana"


class Mundo:
    """O que o `AppState` compõe, sobre um banco de teste, mais o conversor que a rota monta."""

    def __init__(self, db: Database, *, skills: bool = True) -> None:
        self.db, self.skills_on = db, skills
        if db.one("SELECT id FROM apps WHERE id='instagram'") is None:
            db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',?,0)", (PACKAGE,))
        travas = LockedVersions(lambda ref: self.repo.get(ref))
        self.repo = SqlSkillRepository(db, DslDocumentValidator(self.pacote, travas), clock=Relogio(),
                                       adoption_enabled=lambda: self.skills_on)
        self.conversor = FlowConverter(self.repo, PlanDecompiler(SkillPlanCompiler(self.pacote, travas)))

    def pacote(self, app_id: str) -> str | None:
        return self.db.scalar("SELECT package FROM apps WHERE id=?", (app_id,))


@pytest.fixture
def db(tmp_path: Path) -> Any:
    d = banco(tmp_path)
    yield d
    d.close()


def fotografia(db: Database, flow_id: str) -> tuple[Any, ...]:
    """Tudo o que é do fluxo: a linha inteira, o escopo e os apps exigidos."""
    return (dict(db.one("SELECT * FROM flows WHERE id=?", (flow_id,)) or {}),
            sorted((r["profile_id"] or "", r["group_id"] or "") for r in db.query(
                "SELECT profile_id, group_id FROM flow_scope WHERE flow_id=?", (flow_id,))),
            required_apps(db, flow_id))


def literal(db: Database) -> str:
    """Fluxo com o argumento fixo e o texto em modelo (ver `test_descompilador`): não se converte sem mudar o plano."""
    plano = plano_por_catalogo([CapabilityNode(key="abrir_inbox", capability="OPEN_INBOX"),
                                CapabilityNode(key="abrir_conversa", capability="OPEN_THREAD",
                                               depends_on=["abrir_inbox"], bindings={"username": "@ana"})],
                               {"username": "@ana"}, "Abrir a conversa com @ana")
    return aprendido(db, plano, "abra a conversa com @ana no instagram")


# ================================================================== converter
def test_converter_adota_e_cria_o_rascunho_descompilado_numa_transacao(db: Database) -> None:
    m = Mundo(db)
    flow_id, _ = abrir_conversa(db)
    fluxo = db.one("SELECT * FROM flows WHERE id=?", (flow_id,))
    assert fluxo is not None
    assert m.conversor.skill_id_for(flow_id) == SKILL                     # `<app>.<fluxo>`, sempre o mesmo

    c = m.conversor.convert(flow_id, by=DONO)
    v1, v2 = c.published, c.draft
    assert (str(v1.ref), v1.state, v1.schema_version) == (f"{SKILL}@1", SkillState.PUBLISHED, SCHEMA_LEGACY_PLAN)
    assert v1.document() == legacy_content(fluxo, ["instagram"])           # a v1 É o plano do fluxo
    assert (str(v2.ref), v2.state, v2.schema_version, v2.parent_version) == (
        f"{SKILL}@2", SkillState.DRAFT, SCHEMA_DSL_V1, 1)
    assert (v2.provenance.kind, v2.provenance.ref, dict(v2.provenance.notes)) == (
        SourceKind.LEGACY_FLOW, flow_id, {"decompiled_from": f"{SKILL}@1"})
    assert v2.command_template == v1.command_template and v2.match_key == v1.match_key
    assert [n["id"] for n in v2.document()["spec"]["nodes"]] == ["abrir_inbox", "abrir_conversa"]
    assert db.scalar("SELECT status FROM flows WHERE id=?", (flow_id,)) == "disabled"
    assert m.repo.definition(SKILL) is not None and m.repo.definition(SKILL).legacy_flow_id == flow_id  # type: ignore[union-attr]
    # só avisos do compilador (sem exemplo, sem caso): o texto não derivou
    assert {w.code for w in c.warnings} == {"W_PARAMETER_NO_EXAMPLE", "W_NO_VALIDATION_CASE"}
    # o rascunho submete (compila sem erro): a v2 é editável e publicável pelo caminho de sempre
    assert m.repo.transition(v2.ref, SkillState.CANDIDATE, by=DONO, reason="submetida").state is SkillState.CANDIDATE


def test_conversao_recusada_pela_ida_e_volta_nao_deixa_nada(db: Database) -> None:
    m = Mundo(db)
    flow_id = literal(db)
    antes = fotografia(db, flow_id)
    with pytest.raises(InvalidDocument) as exc:
        m.conversor.convert(flow_id, by=DONO)
    assert any(e.startswith("E_ROUNDTRIP /steps/1/postcondition/value") for e in exc.value.errors)
    assert "Nada foi alterado" in str(exc.value)
    assert fotografia(db, flow_id) == antes                                    # o fluxo segue ativo, igual
    assert db.scalar("SELECT COUNT(*) FROM skill_definitions") == 0
    assert db.scalar("SELECT COUNT(*) FROM skill_versions") == 0


def test_converter_com_as_habilidades_desligadas_e_recusado(db: Database) -> None:
    m = Mundo(db, skills=False)
    flow_id, _ = abrir_conversa(db)
    antes = fotografia(db, flow_id)
    with pytest.raises(SkillsDisabled):
        m.conversor.convert(flow_id, by=DONO)
    assert fotografia(db, flow_id) == antes and db.scalar("SELECT COUNT(*) FROM skill_versions") == 0


# ================================================================== desfazer
def test_desfazer_devolve_o_fluxo_exatamente_como_era(db: Database) -> None:
    m = Mundo(db)
    flow_id, _ = abrir_conversa(db)
    db.execute("INSERT INTO flow_scope(flow_id, profile_id) VALUES (?, 'p1')", (flow_id,))
    antes = fotografia(db, flow_id)
    m.conversor.convert(flow_id, by=DONO)
    assert m.repo.scope(SKILL).profile_ids == ("p1",)                         # o escopo foi junto

    desfeito = m.conversor.undo(flow_id, by=DONO, reason="voltar ao fluxo")
    assert (desfeito.skill_id, [str(r) for r in desfeito.discarded_drafts]) == (SKILL, [f"{SKILL}@2"])
    assert fotografia(db, flow_id) == antes                                    # linha, escopo e apps, como eram
    v1 = m.repo.get(SkillRef(SKILL, 1))
    assert v1.state is SkillState.DISABLED and v1.state_detail == "voltar ao fluxo"
    assert [(t.from_state, t.to_state) for t in m.repo.history(v1.ref)] == [
        (None, SkillState.PUBLISHED), (SkillState.PUBLISHED, SkillState.DISABLED)]
    assert db.scalar("SELECT COUNT(*) FROM skill_versions WHERE skill_id=?", (SKILL,)) == 1   # o rascunho saiu
    # desfazer de novo: o fluxo já está ativo — recusa, sem mexer em nada
    with pytest.raises(StateConflict):
        m.conversor.undo(flow_id, by=DONO)
    assert fotografia(db, flow_id) == antes

    # converter de novo: a mesma habilidade, versões novas (a v1 desabilitada não volta: `disabled` é terminal; o
    # número do rascunho apagado é reusado, como em todo `discard_draft` — rascunho nunca executou nem foi validado)
    c = m.conversor.convert(flow_id, by=DONO)
    assert (str(c.published.ref), str(c.draft.ref), c.draft.parent_version) == (f"{SKILL}@2", f"{SKILL}@3", 2)


def test_desfazer_mantem_o_rascunho_que_ja_saiu_de_draft(db: Database) -> None:
    m = Mundo(db)
    flow_id, _ = abrir_conversa(db)
    c = m.conversor.convert(flow_id, by=DONO)
    m.repo.transition(c.draft.ref, SkillState.CANDIDATE, by=DONO, reason="submetida")
    desfeito = m.conversor.undo(flow_id, by=DONO)
    assert desfeito.discarded_drafts == ()
    assert m.repo.get(c.draft.ref).state is SkillState.CANDIDATE
    assert db.scalar("SELECT status FROM flows WHERE id=?", (flow_id,)) == "active"


def test_v1_para_v2_de_quem_adotou_antes_do_descompilador(db: Database) -> None:
    """A adoção da fase G criou só a v1; a rota v1 → v2 dá o rascunho descompilado dela."""
    m = Mundo(db)
    flow_id, _ = abrir_conversa(db)
    v1 = m.repo.adopt_flow(flow_id, skill_id="ig.conversa", by=DONO)
    rascunho, avisos = m.conversor.draft_from(v1.ref, by=DONO)
    assert (str(rascunho.ref), rascunho.parent_version, rascunho.provenance.ref) == ("ig.conversa@2", 1, flow_id)
    assert rascunho.document()["metadata"]["id"] == "ig.conversa"
    assert {w.code for w in avisos} == {"W_PARAMETER_NO_EXAMPLE", "W_NO_VALIDATION_CASE"}
    # e a v2 não é conteúdo legado: descompilar de novo não tem o que fazer
    with pytest.raises(InvalidDocument):
        m.conversor.draft_from(rascunho.ref, by=DONO)


# ================================================================== rotas
def cliente(h: Harness) -> httpx.AsyncClient:
    from app.main import create_app

    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_rotas_de_conversao_atras_do_interruptor_e_com_o_tratamento_de_erro(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        assert s is not None
        assert s.db.scalar("SELECT package FROM apps WHERE id='instagram'") == PACKAGE      # embutido no harness
        flow_id, _ = abrir_conversa(s.db)
        async with cliente(h) as c:
            # desligado: 404 explícito, e nada muda
            h.cfg.file.skills.enabled = False
            r = await c.post(f"/api/flows/{flow_id}/adopt", json={})
            assert r.status_code == 404 and r.json()["detail"]["code"] == "skills_disabled"
            assert s.db.scalar("SELECT status FROM flows WHERE id=?", (flow_id,)) == "active"

            h.cfg.file.skills.enabled = True
            r = await c.post(f"/api/flows/{flow_id}/adopt", json={"reason": "converter"})
            assert r.status_code == 201, r.text
            corpo = r.json()
            assert corpo["skill_id"] == SKILL and corpo["published"]["ref"] == f"{SKILL}@1"
            assert corpo["draft"]["state"] == "draft" and corpo["draft"]["parent_version"] == 1
            assert corpo["draft"]["content"]["spec"]["nodes"][1]["capability"] == "OPEN_THREAD"
            assert {w["code"] for w in corpo["warnings"]} == {"W_PARAMETER_NO_EXAMPLE", "W_NO_VALIDATION_CASE"}
            assert all(set(w) == {"code", "message", "path", "severity", "origin"} for w in corpo["warnings"])
            lista = (await c.get("/api/skills")).json()
            assert {(x["ref"], x["legacy_flow_id"]) for x in lista} == {(f"{SKILL}@1", flow_id),
                                                                       (f"{SKILL}@2", flow_id)}
            # converter de novo o mesmo fluxo (já desligado): 409 do domínio
            r = await c.post(f"/api/flows/{flow_id}/adopt", json={})
            assert r.status_code == 409 and r.json()["detail"]["code"] == "state_conflict"

            r = await c.post(f"/api/flows/{flow_id}/release", json={"reason": "voltar"})
            assert r.status_code == 200, r.text
            assert r.json()["discarded_drafts"] == [f"{SKILL}@2"] and r.json()["flow_status"] == "active"
            assert [v["state"] for v in r.json()["versions"]] == ["disabled"]
            r = await c.post(f"/api/flows/{flow_id}/release", json={})
            assert r.status_code == 409 and r.json()["detail"]["code"] == "state_conflict"
            r = await c.post("/api/flows/nao-existe/release", json={})
            assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"

            # fluxo que a ida e volta não reproduz: 422 com os erros, nada muda
            plano = plano_por_catalogo([CapabilityNode(key="abrir_perfil", capability="OPEN_PROFILE",
                                                       bindings={"username": "@nasa"})],
                                       {"username": "@nasa"}, "Abrir o perfil de @nasa")
            ruim = aprendido(s.db, plano, "abra o perfil de @nasa", run_id="r-ruim")
            r = await c.post(f"/api/flows/{ruim}/adopt", json={"skill_id": "ig.perfil"})
            assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_document"
            assert any("E_ROUNDTRIP" in e for e in r.json()["detail"]["errors"])
            assert s.db.scalar("SELECT status FROM flows WHERE id=?", (ruim,)) == "active"
            r = await c.post(f"/api/flows/{ruim}/adopt", json={"skill_id": "Id Invalido"})
            assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_ref"

            # readotar (v3 publicada, v4 rascunho) e a v1 → v2 pela rota: um rascunho novo a partir da v3
            r = await c.post(f"/api/flows/{flow_id}/adopt", json={})
            assert r.status_code == 201 and r.json()["published"]["ref"] == f"{SKILL}@2"
            r = await c.post(f"/api/skills/{SKILL}/versions/2/decompile")
            assert r.status_code == 201 and r.json()["draft"]["ref"] == f"{SKILL}@4"
            assert r.json()["draft"]["parent_version"] == 2
            r = await c.post(f"/api/skills/{SKILL}/versions/4/decompile")
            assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_document"
    finally:
        await h.state.stop()                                                    # type: ignore[union-attr]



# ================================================================== critério da fase: um comando, um backend
#: O critério da fase J (design §18): nenhum fluxo ativo e habilidade publicada com o mesmo comando. Não cabe no banco
#: (ADR-034: são duas tabelas); quem garante são as escritas. Cada teste abaixo tenta, por um caminho de escrita, pôr
#: os dois vivos — e confere a recusa e esta consulta vazia.
CONFLITOS = ("SELECT f.id AS fluxo, v.id AS versao FROM flows f JOIN skill_versions v ON v.match_key = f.match_key"
             " WHERE f.status = 'active' AND v.state = 'published'")


def validada_outra(m: Mundo) -> SkillRef:
    """Uma habilidade que NÃO é a conversão do fluxo, com o mesmo comando dele (a fixture do design, §12.4),
    validada à mão pelo dono (P4) e pronta para publicar."""
    v = m.repo.create_draft(ABRIR, carregar(ABRIR), source=Provenance(SourceKind.MANUAL), by=DONO)
    m.repo.transition(v.ref, SkillState.CANDIDATE, by=DONO, reason="submetida")
    return m.repo.transition(v.ref, SkillState.VALIDATED, by=DONO, manual=True, reason="critério da fase J").ref


def desligar(db: Database, flow_id: str) -> None:
    """O que `PUT /api/flows/{id}` com `disabled` faz (desligar nunca conflita)."""
    db.execute("UPDATE flows SET status='disabled' WHERE id=?", (flow_id,))


def test_publicar_com_o_fluxo_ativo_do_mesmo_comando_e_recusado(db: Database) -> None:
    m = Mundo(db)
    flow_id, _ = abrir_conversa(db)
    ref = validada_outra(m)
    with pytest.raises(DuplicateCommand):
        m.repo.transition(ref, SkillState.PUBLISHED, by=DONO, reason="publicar")
    assert m.repo.get(ref).state is SkillState.VALIDATED and db.query(CONFLITOS) == []


def test_rollback_com_o_fluxo_religado_e_recusado(db: Database) -> None:
    m = Mundo(db)
    flow_id, _ = abrir_conversa(db)
    desligar(db, flow_id)
    ref = validada_outra(m)
    m.repo.transition(ref, SkillState.PUBLISHED, by=DONO, reason="publicar")
    m.repo.transition(ref, SkillState.DEPRECATED, by=DONO, reason="recolher")
    db.execute("UPDATE flows SET status='active' WHERE id=?", (flow_id,))    # religar: nada publicado com o comando
    with pytest.raises(DuplicateCommand):
        m.repo.transition(ref, SkillState.PUBLISHED, by=DONO, reason="rollback")
    assert db.query(CONFLITOS) == []


def test_desfazer_a_conversao_com_outra_publicada_e_recusado_sem_mexer(db: Database) -> None:
    m = Mundo(db)
    flow_id, _ = abrir_conversa(db)
    c = m.conversor.convert(flow_id, by=DONO)
    m.repo.transition(c.published.ref, SkillState.DISABLED, by=DONO, reason="parada")
    outra = validada_outra(m)
    m.repo.transition(outra, SkillState.PUBLISHED, by=DONO, reason="publicar")   # o fluxo está desligado: pode
    with pytest.raises(DuplicateCommand):
        m.conversor.undo(flow_id, by=DONO)
    assert db.scalar("SELECT status FROM flows WHERE id=?", (flow_id,)) == "disabled"
    assert m.repo.get(c.draft.ref).state is SkillState.DRAFT                   # a transação voltou inteira
    assert db.query(CONFLITOS) == []


def test_aprender_fluxo_de_comando_publicado_nao_cria_fluxo(db: Database) -> None:
    """Os dois aprendizados: o do treino (`learn_from_plan`, aberto até a fase J) e o de execução (fase G)."""
    m = Mundo(db)
    ref = validada_outra(m)
    m.repo.transition(ref, SkillState.PUBLISHED, by=DONO, reason="publicar")
    plano = plano_por_catalogo([CapabilityNode(key="abrir_inbox", capability="OPEN_INBOX")], {"username": "@ana"},
                               "Abrir a conversa com @ana")
    assert FlowStore(db).learn_from_run({"id": "r-x", "plan": plano.model_dump_json(), "flow_id": None,
                                         "skill_id": None, "command": "abra a conversa com @ana no instagram"}) is None
    with pytest.raises(ValueError, match="publicada"):
        FlowStore(db).learn_from_plan(plano, "abra a conversa com {username} no instagram", source="training:t")
    assert db.scalar("SELECT COUNT(*) FROM flows") == 0 and db.query(CONFLITOS) == []
    assert treino(db)[0]                                                     # comando livre: o treino salva


async def test_religar_o_fluxo_pela_rota_com_outra_publicada_e_recusado(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        assert s is not None
        flow_id, _ = abrir_conversa(s.db)
        async with cliente(h) as c:
            assert (await c.put(f"/api/flows/{flow_id}", json={"status": "disabled"})).status_code == 200
            m = Mundo(s.db)
            ref = validada_outra(m)
            m.repo.transition(ref, SkillState.PUBLISHED, by=DONO, reason="publicar")
            r = await c.put(f"/api/flows/{flow_id}", json={"status": "active"})
            assert r.status_code == 409 and r.json()["detail"]["code"] == "command_published"
            assert str(ref) in r.json()["detail"]["message"]
            assert s.db.scalar("SELECT status FROM flows WHERE id=?", (flow_id,)) == "disabled"
            assert s.db.query(CONFLITOS) == []
            # desabilitada a habilidade, religar volta a valer
            m.repo.transition(ref, SkillState.DISABLED, by=DONO, reason="parada")
            assert (await c.put(f"/api/flows/{flow_id}", json={"status": "active"})).status_code == 200
    finally:
        await h.state.stop()                                                    # type: ignore[union-attr]
