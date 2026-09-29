"""A decisão de quem aprova acompanha a etapa revisada; “Tentar novamente” leva o texto da versão anterior.

Pendência do ADR-053 (pacote 'recuperacao'): a recuperação automática recria a etapa com id novo e a porta de
aprovação procurava o pedido pelo id — um CREATE_COMMENT já aprovado na v1 abria pedido NOVO na v2 e o objetivo
voltava a `waiting_user`, esperando a mesma pessoa aprovar a mesma frase de novo. E o “Tentar novamente” da pessoa
(`RunService._requeue`) revisava o plano sem `herdar_textos`: a etapa nascia sem o texto, a porta de rascunho
escrevia outro (pago) e a aprovação dada não tinha como valer.

Provas SIMULADAS: provedor simulado, aparelho falso (`FakeQaDevice`), banco de teste.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db import Database, loads
from app.models import (Plan, PlannerInfo, PlanStep, PersonaCreate, PersonaTraits, Postcondition, ProfileCreate,
                        ProfilePatch, ResolveBody, StepDTO)
from app.social.approvals import Approval, ApprovalStore, definir_texto

from .conftest import Harness, make_config
from .test_capabilities import IG, SENHA
from .test_recuperacao_preserva_estado import PRAZO, _falhar

OID = "run-v:android-01"
V1 = f"{OID}:v1:c1"
V2 = f"{OID}:v2:c1"


async def _comentario_aprovado(harness: Harness, *, verbo: str = "approve", texto: str | None = None) -> Approval:
    """CREATE_COMMENT na v1: a porta escreve o texto na voz do perfil, pede aprovação, a pessoa decide e a etapa
    falha por PRAZO sem o efeito ter saído — o caso da e31953 com o app vivo. Devolve o pedido decidido.

    A execução fica com `pause_requested=1` de propósito: o despacho não pega o objetivo por conta própria, e a
    ordem das portas é a que o teste chama."""
    assert harness.state is not None
    state = harness.state
    db = state.db
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("UPDATE instances SET app_id='ig' WHERE id='android-01'")
    pid = state.social.create_profile(ProfileCreate(username="lucas.almeida9484", password=SENHA,
                                                    instance_id="android-01")).id
    persona = state.social.create_persona(PersonaCreate(name="lucas", traits=PersonaTraits(tone="Direto")))
    state.social.update_profile(pid, ProfilePatch(persona_id=persona.id))
    post = Postcondition(kind="model_judged", value="x", description="y")
    plano = Plan(summary="comentar", app_id="ig", planner=PlannerInfo(provider="fake", model="t", simulated=True),
                 steps=[PlanStep(key="c1", title="Comentar", goal="comentar", side_effect=True, postcondition=post,
                                 max_attempts=1, capability="CREATE_COMMENT", commit_selector="id=post",
                                 # o autor da publicação: sem ele a porta de frota recusa (ADR-055)
                                 bindings={"content_brief": "elogiar o post", "post_author": "@autora"})])
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
               " plan, pause_requested) VALUES ('run-v','kv','comentar','execute','running',1,'[\"android-01\"]',"
               "'2026-09-28T16:52:54Z',?,1)", (plano.model_dump_json(),))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
               " VALUES (?,'run-v','android-01','running',1,'{}',?)", (OID, pid))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
        " bindings) VALUES (?,'run-v',?,'android-01',1,1,'c1','Comentar','comentar','[]',1,'[]',"
        "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready','CREATE_COMMENT',"
        "'id=post','{\"content_brief\": \"elogiar o post\", \"post_author\": \"@autora\"}')", (V1, OID))
    run = state.repo.run_row("run-v")

    veredito = await state._policy_gate(state.repo.objective_row(OID), state.repo.step_row(V1), run)
    assert veredito is not None and veredito.policy == "approval_required"
    state.scheduler._hold(state.repo.objective_row(OID), state.repo.step_row(V1), veredito)  # noqa: SLF001
    pendentes = state.approval_service.list()
    assert len(pendentes) == 1 and pendentes[0]["content"]
    state.approval_service.decide(pendentes[0]["id"], verbo, content=texto)
    assert await state._policy_gate(state.repo.objective_row(OID), state.repo.step_row(V1), run) is None

    # A etapa roda e estoura o prazo sem disparar o efeito (nenhuma ação de commit registrada).
    db.execute("UPDATE steps SET status='failed' WHERE id=?", (V1,))
    db.execute("UPDATE objectives SET status='running' WHERE id=?", (OID,))
    decidido = state.approvals.get(pendentes[0]["id"])
    assert decidido is not None
    return decidido


async def _recuperar_da_tela_atual(harness: Harness) -> None:
    """O que `Scheduler._apply` faz com a falha: recuperação automática, app vivo na frente, falha de prazo."""
    assert harness.state is not None
    state = harness.state
    passo = state.repo.step_dto(state.repo.step_row(V1))
    rec = state.scheduler._try_recover(state.repo.objective_row(OID), passo, PRAZO, app_vivo=True)  # noqa: SLF001
    assert rec.revisou and rec.da_tela_atual
    assert state.repo.step_row(V2) is not None


async def test_aprovacao_da_v1_vale_para_a_etapa_revisada_com_o_mesmo_texto(harness: Harness) -> None:
    """A pessoa aprovou a frase na v1; a etapa estourou o prazo e a recuperação a recriou (v2) com o mesmo texto.
    Pedir de novo deixava o objetivo esperando alguém aprovar o que já estava aprovado."""
    assert harness.state is not None
    state = harness.state
    aprovado = await _comentario_aprovado(harness)
    await _recuperar_da_tela_atual(harness)

    v2 = state.repo.step_row(V2)
    assert (loads(v2["bindings"], {}) or {}).get("content") == aprovado.content      # herdou o texto aprovado
    veredito = await state._policy_gate(state.repo.objective_row(OID), v2, state.repo.run_row("run-v"))

    assert veredito is None, veredito                                  # segue sem voltar a esperar ninguém
    linhas = state.db.query("SELECT id, status, step_id FROM pending_approvals WHERE objective_id=?", (OID,))
    assert [(r["id"], r["status"], r["step_id"]) for r in linhas] == [(aprovado.id, "approved", V2)]
    assert state.approval_service.list() == []                         # nenhum cartão novo na tela
    assert state.repo.objective_row(OID)["blocked_kind"] != "approval"
    # O rastro diz qual decisão passou a valer para a etapa revisada.
    textos = [r["message"] for r in state.db.query(
        "SELECT message FROM events WHERE run_id='run-v' AND kind='decision'")]
    assert any(aprovado.id in t for t in textos), textos


async def test_texto_diferente_na_etapa_revisada_pede_nova_aprovacao(harness: Harness) -> None:
    """Aprovar uma frase não aprova outra: se a etapa revisada chega com texto diferente, é pedido novo — e a
    decisão antiga fica onde estava, na etapa da v1."""
    assert harness.state is not None
    state = harness.state
    aprovado = await _comentario_aprovado(harness)
    await _recuperar_da_tela_atual(harness)
    definir_texto(state.db, V2, "Outra frase, que ninguém leu.")

    veredito = await state._policy_gate(state.repo.objective_row(OID), state.repo.step_row(V2),
                                        state.repo.run_row("run-v"))

    assert veredito is not None and veredito.policy == "approval_required"
    pendentes = state.approval_service.list()
    assert [(p["step_id"], p["content"]) for p in pendentes] == [(V2, "Outra frase, que ninguém leu.")]
    antigo = state.approvals.get(aprovado.id)
    assert antigo is not None and antigo.status == "approved" and antigo.step_id == V1


async def test_texto_editado_na_aprovacao_acompanha_a_etapa_revisada(harness: Harness) -> None:
    """Editar é aprovar OUTRO texto: é ele que a etapa revisada herda, e é ele que a decisão cobre."""
    assert harness.state is not None
    state = harness.state
    editado = "Ficou muito bom, parabéns pelo trabalho."
    aprovado = await _comentario_aprovado(harness, verbo="edit", texto=editado)
    assert aprovado.status == "edited" and aprovado.content == editado
    await _recuperar_da_tela_atual(harness)

    v2 = state.repo.step_row(V2)
    veredito = await state._policy_gate(state.repo.objective_row(OID), v2, state.repo.run_row("run-v"))

    assert veredito is None, veredito
    assert (loads(state.repo.step_row(V2)["bindings"], {}) or {}).get("content") == editado
    depois = state.approvals.get(aprovado.id)
    assert depois is not None and depois.status == "edited" and depois.step_id == V2


# ---------------------------------------------------------------- a regra, direto no repositório de aprovações
def _banco(tmp_path: Path) -> tuple[Database, ApprovalStore]:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-u','ku','comentar','execute','running',1,'[]','2026-09-28T10:00:00Z')")
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters)"
               " VALUES ('run-u:android-02','run-u','android-02','running',2,'{}')")
    return db, ApprovalStore(db)


def _etapa(db: Database, versao: int, chave: str, texto: str) -> str:
    step_id = f"run-u:android-02:v{versao}:{chave}"
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, bindings)"
        " VALUES (?,'run-u','run-u:android-02','android-02',?,1,?,'Comentar','comentar','[]',1,?,"
        "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,?,'CREATE_COMMENT',?)",
        (step_id, versao, chave, json.dumps([texto]), "failed" if versao == 1 else "ready",
         json.dumps({"username": "@ana", "content": texto})))
    return step_id


def _decidida(store: ApprovalStore, step_id: str, texto: str, status: str = "approved") -> str:
    pedido = store.open(profile_id=None, capability="CREATE_COMMENT", summary="Comentar", target="@ana",
                        content=texto, run_id="run-u", objective_id="run-u:android-02", step_id=step_id)
    assert store.decide(pedido.id, status=status, decided_by="dono") is not None
    return pedido.id


def _acompanhar(store: ApprovalStore, step_id: str, texto: str, *, disparou: bool = False) -> Approval | None:
    return store.acompanhar_revisao(step_id, profile_id=None, capability="CREATE_COMMENT", target="@ana",
                                    content=texto, disparou=lambda _etapa: disparou)


def test_mesma_chave_mesmo_texto_acompanha_e_passa_a_apontar_para_a_etapa_revisada(tmp_path: Path) -> None:
    db, store = _banco(tmp_path)
    v1, v2 = _etapa(db, 1, "c1", "bom dia"), _etapa(db, 2, "c1", "bom dia")
    pedido = _decidida(store, v1, "bom dia")

    achado = _acompanhar(store, v2, "bom dia")

    assert achado is not None and achado.id == pedido and achado.step_id == v2 and achado.status == "approved"
    assert store.for_step(v1) is None                    # uma decisão, um cartão: não se copia


def test_efeito_ja_disparado_gasta_a_aprovacao(tmp_path: Path) -> None:
    """Aprovar é liberar UM envio. Se a etapa antiga chegou a disparar o efeito, repetir pede decisão de novo."""
    db, store = _banco(tmp_path)
    v1, v2 = _etapa(db, 1, "c1", "bom dia"), _etapa(db, 2, "c1", "bom dia")
    pedido = _decidida(store, v1, "bom dia")

    assert _acompanhar(store, v2, "bom dia", disparou=True) is None
    db.execute("UPDATE pending_approvals SET interaction_id='int-1' WHERE id=?", (pedido,))
    assert _acompanhar(store, v2, "bom dia") is None
    assert store.for_step(v1) is not None


def test_outra_etapa_com_o_mesmo_texto_e_o_mesmo_alvo_nao_herda(tmp_path: Path) -> None:
    """Mesmo texto e mesmo dono do post não fazem a mesma ação: podem ser dois posts. A identidade é a chave."""
    db, store = _banco(tmp_path)
    v1 = _etapa(db, 1, "c1", "bom dia")
    outra = _etapa(db, 2, "c2", "bom dia")
    _decidida(store, v1, "bom dia")

    assert _acompanhar(store, outra, "bom dia") is None


def test_rejeicao_posterior_encerra_o_assunto(tmp_path: Path) -> None:
    """Vale a decisão MAIS RECENTE sobre a chave: aprovada na v1 e rejeitada depois não volta a valer."""
    db, store = _banco(tmp_path)
    v1, v2 = _etapa(db, 1, "c1", "bom dia"), _etapa(db, 2, "c1", "bom dia")
    _decidida(store, v1, "bom dia")
    _decidida(store, v2, "bom dia", status="rejected")
    v3 = _etapa(db, 3, "c1", "bom dia")

    assert _acompanhar(store, v3, "bom dia") is None


# ---------------------------------------------------------------- “Tentar novamente” da pessoa
async def test_tentar_novamente_leva_o_texto_a_guarda_e_o_rascunho_da_versao_anterior(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """A recuperação automática herda o texto escrito (e talvez aprovado); o “Tentar novamente” da pessoa passa pela
    MESMA revisão (`recovery_steps`) e não herdava: a etapa renascia sem `content`, sem a guarda e sem a marca de
    rascunho, e a porta escrevia outro texto — pago, e que a aprovação já dada não cobre."""
    assert harness.state is not None
    db = harness.state.db
    conteudo = "Que foto linda!"

    def escrever(step: StepDTO) -> None:
        linha = db.one("SELECT bindings, commit_guard FROM steps WHERE id=?", (step.id,))
        bindings = {**(loads(linha["bindings"], {}) or {}), "content": conteudo}
        guardas = [*(loads(linha["commit_guard"], []) or []), conteudo]
        db.execute("UPDATE steps SET bindings=?, commit_guard=?, draft_meta=? WHERE id=?",
                   (json.dumps(bindings), json.dumps(guardas), json.dumps({"rationale": "elogio"}), step.id))

    _falhar(monkeypatch, harness, "send_message", "Pré-condições do efeito externo não foram atendidas.", vezes=2,
            antes=escrever)
    run = await harness.wait_run(harness.run(["android-01"]).id, timeout=60)
    oid = f"{run.id}:android-01"
    assert db.scalar("SELECT status FROM objectives WHERE id=?", (oid,)) == "failed"

    saida = harness.state.runs.retry_failed(run.id)

    assert saida["retried"] == [oid], saida
    linhas = {int(r["plan_version"]): r for r in db.query(
        "SELECT plan_version, bindings, commit_guard, draft_meta FROM steps WHERE objective_id=?"
        " AND key='send_message'", (oid,))}
    assert sorted(linhas) == [1, 2, 3], linhas
    antes, depois = linhas[2], linhas[3]
    assert (loads(depois["bindings"], {}) or {}).get("content") == conteudo
    assert loads(depois["commit_guard"], []) == loads(antes["commit_guard"], [])
    assert loads(depois["draft_meta"], {}) == {"rationale": "elogio"}          # a porta não reescreve o texto
    await harness.wait_run(run.id, timeout=60)


async def test_tentar_novamente_com_texto_aprovado_nao_pede_a_mesma_aprovacao(harness: Harness) -> None:
    """Ponta a ponta do caminho da pessoa: aprovado na v1, a etapa falhou, “repetir este item”. O texto aprovado
    renasce na v2 e a decisão vale para ele — nem texto novo pago, nem pedido repetido."""
    assert harness.state is not None
    state = harness.state
    aprovado = await _comentario_aprovado(harness)
    db = state.db
    db.execute("UPDATE objectives SET status='failed' WHERE id=?", (OID,))

    state.runs.resolve("run-v", OID, ResolveBody(resolution="retry"))

    v2 = state.repo.step_row(V2)
    assert v2 is not None
    assert (loads(v2["bindings"], {}) or {}).get("content") == aprovado.content
    veredito = await state._policy_gate(state.repo.objective_row(OID), v2, state.repo.run_row("run-v"))
    assert veredito is None, veredito
    assert state.approval_service.list() == []
    depois = state.approvals.get(aprovado.id)
    assert depois is not None and depois.step_id == V2
