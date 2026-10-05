"""Modo treinamento (itens 13.1–13.3): a pessoa ensina fazendo, a IA transforma a gravação em habilidade."""
from __future__ import annotations

import json

import pytest

from app.models import ControlOwner, ManualInput
from app.training.recorder import TrainingError

from .conftest import Harness

SEGREDO = "minhaSenha#2026!"


async def _no_controle(harness: Harness):
    st = harness.state
    rt = st.devices.get("android-01")
    await harness.wait(lambda: rt.state.value == "online", what="aparelho online")
    status, lease = st.devices.request_control(rt)
    assert status == "granted" and rt.control == ControlOwner.user
    return st, rt, lease


async def _entrada(st, rt, lease, **campos):
    frame = (await st.devices.observe(rt, timeout=5)).frame_id
    await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, **campos))


async def test_grava_o_elemento_tocado_o_texto_e_nunca_o_segredo(harness: Harness) -> None:
    st, rt, lease = await _no_controle(harness)
    fake = harness.fakes["android-01"]
    with pytest.raises(TrainingError) as sem_controle:
        st.training.start("android-01", intent="responder", lease_id="outro")
    assert sem_controle.value.code == "control_required"

    sessao = st.training.start("android-01", intent="Mandar mensagem para um contato do QA", lease_id=lease,
                               app_id="qa-messenger")
    assert sessao["status"] == "recording" and rt.training_session_id == sessao["id"]
    with pytest.raises(TrainingError):
        st.training.start("android-01", intent="outra", lease_id=lease)       # uma gravação por aparelho

    st.training.record(rt, {"type": "open_app", "app_id": "qa-messenger"}, None)   # "abrir app" (outro caminho)
    fake.screen = "home"
    await _entrada(st, rt, lease, type="tap", x=100, y=200 + 3 * 120 + 30)      # QA-001 na lista
    await _entrada(st, rt, lease, type="tap", x=100, y=1200)                    # campo de mensagem
    await _entrada(st, rt, lease, type="text", text="Olá, tudo certo?")
    await _entrada(st, rt, lease, type="text", text=SEGREDO)                    # parece segredo: não grava
    await _entrada(st, rt, lease, type="key", key="back")

    g = st.training.get(sessao["id"])
    tipos = [e["type"] for e in g["inputs"]]
    assert tipos == ["open_app", "tap", "tap", "text", "text", "key"]
    toque = g["inputs"][1]
    assert toque["target"]["text"] == "QA-001" and "conversation_name" in toque["target"]["resource_id"]
    assert toque["target"]["unique"], "o alvo precisa dos seletores que o identificam sozinho"
    assert "id" not in toque["target"]                                            # id efêmero da observação
    assert "QA-001" in toque["screen_lines"]
    assert g["inputs"][3]["text"] == "Olá, tudo certo?"
    assert g["inputs"][4]["text"] is None and g["inputs"][4]["has_text"] and g["inputs"][4]["text_len"] == len(SEGREDO)
    assert SEGREDO not in json.dumps(g, ensure_ascii=False)
    assert SEGREDO not in json.dumps([dict(r) for r in st.db.query("SELECT * FROM events")], ensure_ascii=False)

    # devolver o controle encerra a gravação
    st.devices.release_control(rt, lease)
    assert st.training.get(sessao["id"])["status"] == "recorded" and rt.training_session_id is None
    assert [s["id"] for s in st.training.list(instance_id="android-01")] == [sessao["id"]]
    assert st.training.list()[0]["input_count"] == 6


async def test_sem_gravacao_a_entrada_manual_nao_deixa_rastro(harness: Harness) -> None:
    st, rt, lease = await _no_controle(harness)
    harness.fakes["android-01"].screen = "home"
    await _entrada(st, rt, lease, type="key", key="back")
    assert st.db.scalar("SELECT COUNT(*) FROM training_inputs") == 0
    s = st.training.start("android-01", intent="x", lease_id=lease)
    st.training.stop(s["id"], discard=True, lease_id=lease)
    assert st.training.get(s["id"])["status"] == "discarded" and rt.training_session_id is None


def test_senha_e_codigo_digitados_nao_sao_gravados_mas_frase_sim() -> None:
    from app.security.redaction import parece_senha_ou_codigo
    for segredo in (SEGREDO, "Abc12345", "482913", "S3nh@forte"):
        assert parece_senha_ou_codigo(segredo), segredo
    for normal in ("Olá, tudo certo?", "nasa", "QA-001", "bom dia", "tadeu.quintela4821", "2026"):
        assert not parece_senha_ou_codigo(normal), normal


async def _gravar_mensagem(st, rt, lease, fake) -> str:
    s = st.training.start("android-01", intent="Mandar mensagem no QA Messenger para", lease_id=lease,
                          app_id="qa-messenger")
    st.training.record(rt, {"type": "open_app", "app_id": "qa-messenger"}, None)
    fake.screen = "home"
    await _entrada(st, rt, lease, type="tap", x=100, y=200 + 3 * 120 + 30)      # QA-001
    await _entrada(st, rt, lease, type="tap", x=100, y=1200)                    # campo
    await _entrada(st, rt, lease, type="text", text="Olá, tudo certo?")
    await _entrada(st, rt, lease, type="tap", x=640, y=1200)                    # Enviar
    await _entrada(st, rt, lease, type="key", key="back")                       # sobra de quem ensinou
    st.training.stop(s["id"], lease_id=lease)
    return s["id"]


async def test_da_gravacao_a_habilidade_com_escopo_e_receitas(harness: Harness) -> None:
    from app.models import PolicyGroupCreate, ProfileCreate
    st, rt, lease = await _no_controle(harness)
    fake = harness.fakes["android-01"]
    pa = st.social.create_profile(ProfileCreate(username="aluno.um", instance_id="android-01")).id
    pb = st.social.create_profile(ProfileCreate(username="aluno.dois", instance_id="android-02")).id
    sid = await _gravar_mensagem(st, rt, lease, fake)

    prop = (await st.skills.propose(sid))["proposal"]
    assert st.training.get(sid)["status"] == "proposed"
    chaves = [s["key"] for s in prop["steps"]]
    assert chaves == ["abrir_app", "abrir_conversa", "escrever", "enviar"]
    assert {p["name"]: p["example"] for p in prop["parameters"]} == {"contato": "QA-001", "mensagem": "Olá, tudo certo?"}
    assert [d["seq"] for d in prop["discarded"]] == [6]                        # o "voltar" não é parte da tarefa
    assert prop["steps"][1]["inputs"] == [2, 3] and prop["steps"][3]["side_effect"]

    g = st.social.create_policy_group(PolicyGroupCreate(name="Alunos"))
    salvo = await st.skills.save(sid, proposal=None, profile_ids=[pa], group_ids=[g.id])
    flow_id = salvo["flow_id"]
    assert st.training.get(sid)["status"] == "saved"
    linha = st.db.one("SELECT * FROM flows WHERE id=?", (flow_id,))
    assert linha["source"] == f"training:{sid}" and "{contato}" in linha["command_template"]
    receitas = {r["key"]: r for r in salvo["steps"]}
    assert receitas["abrir_conversa"]["recipe"] and receitas["escrever"]["recipe"] and receitas["enviar"]["recipe"], receitas
    assert st.db.scalar("SELECT COUNT(*) FROM recipes WHERE learned_from_step=?", (f"training:{sid}",)) >= 3

    # escopo: casa para o perfil treinado, não para outro; a prévia (sem aparelhos) casa
    comando = linha["command_template"].replace("{contato}", "QA-002").replace("{mensagem}", "Bom dia!")
    fl = st.scheduler.flows
    assert fl.match(comando, [pa]) is not None and fl.match(comando, [pb]) is None and fl.match(comando) is not None
    st.social.update_policy_group(g.id, __import__("app.models", fromlist=["PolicyGroupPatch"]).PolicyGroupPatch(profile_ids=[pb]))
    assert fl.match(comando, [pb]) is not None                                   # entrou no grupo → recebeu

    # o mesmo comando, pelo aparelho do perfil treinado, reaproveita a habilidade (sem planejador)
    st.devices.release_control(rt, lease)
    fake.screen = "launcher"
    st.cfg.file.ai.flows = True                                                  # como em produção
    st.cfg.file.ai.recipes = "replay"
    planos_antes = harness.ai.count("plan")
    from app.models import RunCreate
    run = st.runs.create(RunCreate(command=comando, instance_ids=["android-01"], idempotency_key="treino-uso-1"))
    det = await harness.wait_run(run.id, timeout=60)
    assert st.repo.run_row(run.id)["flow_id"] == flow_id and harness.ai.count("plan") == planos_antes
    assert det.status == "completed", (det.status, det.status_detail)
    origem = {r["key"]: r["driven_by"] for r in st.db.query("SELECT key, driven_by FROM steps WHERE run_id=?", (run.id,))}
    assert origem["abrir_conversa"] == "recipe" and origem["enviar"] == "recipe", origem
    assert any(m.body == "Bom dia!" and m.contact == "QA-002" for m in fake.messages)


async def test_efeito_no_app_com_catalogo_exige_a_acao_do_catalogo(harness: Harness) -> None:
    st, rt, lease = await _no_controle(harness)
    s = st.training.start("android-01", intent="curtir", lease_id=lease, app_id="instagram")
    st.training.record(rt, {"type": "open_app", "app_id": "instagram"}, None)
    st.training.stop(s["id"], lease_id=lease)
    proposta = {"summary": "curtir", "command_template": "curta a publicação de {perfil}", "app_id": "instagram",
                "parameters": [{"name": "perfil", "example": "nasa", "description": ""}], "discarded": [], "questions": [],
                "steps": [{"key": "curtir", "title": "Curtir", "goal": "curtir", "inputs": [1], "side_effect": True,
                           "capability": None, "bindings": [], "app_id": None,
                           "postcondition": {"kind": "model_judged", "value": "curtido", "description": "curtido"}}]}
    with pytest.raises(TrainingError) as sem_acao:
        await st.skills.save(s["id"], proposal=proposta, profile_ids=[], group_ids=[])
    assert sem_acao.value.code == "capability_required"
    proposta["steps"][0].update({"capability": "LIKE_POST"})
    salvo = await st.skills.save(s["id"], proposal=proposta, profile_ids=[], group_ids=[])
    plano = st.db.one("SELECT plan FROM flows WHERE id=?", (salvo["flow_id"],))["plan"]
    assert '"capability":"LIKE_POST"' in plano.replace(" ", "")               # a etapa é a do catálogo


async def test_portao_recusa_efeito_sem_acao_num_app_com_catalogo(harness: Harness) -> None:
    """Defesa em profundidade: mesmo que uma etapa com efeito chegue sem ação do catálogo num app que TEM catálogo
    (plano livre que atravessa apps, habilidade antiga), o portão de política não a deixa passar calada."""
    from app.models import Plan, PlannerInfo, PlanStep, Postcondition, RunCreate
    st = harness.state
    run = st.runs.create(RunCreate(command="curtir e enviar", instance_ids=["android-01"], idempotency_key="portao-1", mode="plan"))
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "completed", "completed_with_issues", "failed"))
    post = Postcondition(kind="model_judged", value="ok", description="d")
    plano = Plan(summary="s", app_id="qa-messenger", app_package="com.pocqa.messenger",
                 steps=[PlanStep(key="curtir", title="Curtir", goal="g", postcondition=post, side_effect=True,
                                 app_id="instagram"),
                        PlanStep(key="enviar_qa", title="Enviar", goal="g", postcondition=post, side_effect=True)],
                 planner=PlannerInfo(provider="t", model="t", simulated=True))
    st.db.execute("DELETE FROM steps WHERE run_id=?", (run.id,))
    st.db.execute("DELETE FROM objectives WHERE run_id=?", (run.id,))
    st.repo.save_plan(run.id, plano)
    st.repo.materialize(run.id, plano, [{"instance_id": "android-01"}])
    obj = st.repo.objective_row(f"{run.id}:android-01")
    run_row = st.repo.run_row(run.id)
    no_insta = st.repo.step_row(f"{run.id}:android-01:v1:curtir")
    no_qa = st.repo.step_row(f"{run.id}:android-01:v1:enviar_qa")
    veredito = await st._policy_gate(obj, no_insta, run_row)                           # noqa: SLF001
    assert veredito is not None and not veredito.allowed and "catálogo" in veredito.reason
    assert await st._policy_gate(obj, no_qa, run_row) is None                          # noqa: SLF001 - app sem catálogo


async def test_rotas_http_do_treinamento(harness: Harness) -> None:
    from .test_perfil_bloqueado_e_capacidades import _cliente
    st, rt, lease = await _no_controle(harness)
    fake = harness.fakes["android-01"]
    async with _cliente(harness) as c:
        assert (await c.post("/api/instances/android-01/training", json={"intent": "x", "lease_id": "errado"})).status_code == 409
        r = await c.post("/api/instances/android-01/training", json={"intent": "Mandar mensagem", "lease_id": lease,
                                                                     "app_id": "qa-messenger"})
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        fake.screen = "home"
        await _entrada(st, rt, lease, type="tap", x=100, y=200 + 3 * 120 + 30)
        assert (await c.post(f"/api/training/{sid}/propose")).status_code == 409          # ainda gravando
        assert (await c.post(f"/api/training/{sid}/stop", json={"lease_id": lease})).json()["status"] == "recorded"
        assert len((await c.get(f"/api/training/{sid}")).json()["inputs"]) == 1
        assert (await c.get("/api/training?instance_id=android-01")).json()[0]["id"] == sid
        prop = (await c.post(f"/api/training/{sid}/propose")).json()["proposal"]
        assert prop["steps"]
        r = await c.post(f"/api/training/{sid}/save", json={"profile_ids": ["nao-existe"]})
        assert r.status_code == 400
        r = await c.post(f"/api/training/{sid}/save", json={})
        assert r.status_code == 200, r.text
        assert r.json()["flow_id"]
        outro = (await c.post("/api/instances/android-01/training", json={"intent": "y", "lease_id": lease})).json()["id"]
        assert (await c.post(f"/api/training/{outro}/discard", json={"lease_id": lease})).json()["status"] == "discarded"
        assert (await c.get("/api/training/nao-existe")).status_code == 404
