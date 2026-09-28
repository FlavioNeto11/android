"""Assistente do comando (ADR-047): refinar o texto com a IA e responder a uma execução em `needs_input`.
Nível de prova: `simulated` (planejador e refinador simulados, contados pelo `CountingProvider`; aparelhos falsos na
porta 5640).

O que se prova:
- o refinamento devolve o comando em blocos, pergunta o app quando falta e fica "pronto" quando respondido;
- credencial no comando OU numa resposta → 409 antes de qualquer chamada de IA; eco de credencial na saída é tirado;
- pergunta de destino (`profile_id`/`instance_id`) não é respondida por texto → 409 `pergunta_de_destino`;
- com `run_id`, as perguntas do planejador daquela execução vão ao refinador;
- a sucessora nasce com o comando respondido e o mesmo pedido de alvos, e a antiga é cancelada apontando para ela;
- duplo envio da mesma resposta não cria duas execuções; execução que não espera resposta → 409;
- a rota HTTP responde nos dois caminhos e a de sucessora não é engolida por `/runs/{id}/{op}`.
"""
from __future__ import annotations

import secrets as pysecrets

import httpx
import pytest

from app.db import dumps, loads
from app.main import create_app
from app.models import ProfileCreate, RunCreate, RunTarget
from app.modules.execution.domain.command_refinement import CommandRefinement, RefineQuestion, normalizar
from app.security.redaction import redact
from app.taskqueue.assistente import CommandRefineBody, ComandoAssistido, RefineAnswerIn, RunSuccessorBody
from app.taskqueue.service import RunError

from .conftest import COMMAND, Harness

pytestmark = pytest.mark.asyncio

INCOMPLETO = "Abra o QA Messenger e envie uma mensagem"


def _assistente(h: Harness) -> ComandoAssistido:
    assert h.state is not None
    return ComandoAssistido(h.state.runs)


async def _em_needs_input(h: Harness) -> str:
    run = h.run(["android-01"], command=INCOMPLETO, mode="plan")
    await h.wait_run(run.id, ("needs_input",))
    return run.id


async def test_refinar_estrutura_pergunta_o_app_e_fica_pronto_quando_respondido(harness: Harness) -> None:
    a = _assistente(harness)
    r = await a.refinar(CommandRefineBody(command="mande um oi pro suporte", instance_ids=["android-01"]))
    assert r.command.startswith("Objetivo:")
    assert [q.field for q in r.questions] == ["app"] and not r.ready
    assert "QA Messenger" in r.questions[0].options
    r2 = await a.refinar(CommandRefineBody(command="mande um oi pro suporte", instance_ids=["android-01"],
                                           answers=[RefineAnswerIn(field="app", question=r.questions[0].question,
                                                                   answer="QA Messenger")]))
    assert r2.ready and not r2.questions
    assert "App ou site: QA Messenger" in r2.command
    assert harness.ai.count("plan") == 2          # duas chamadas de refinamento, nenhuma de planejamento


async def test_credencial_no_comando_ou_na_resposta_e_recusada_sem_chamar_a_ia(harness: Harness) -> None:
    a = _assistente(harness)
    antes = len(harness.ai.calls)
    for body in (CommandRefineBody(command="entre no portal, Senha: segredo123", instance_ids=["android-01"]),
                 CommandRefineBody(command="entre no portal", instance_ids=["android-01"],
                                   answers=[RefineAnswerIn(field="login", answer="senha: segredo123")])):
        with pytest.raises(RunError) as exc:
            await a.refinar(body)
        assert exc.value.code == "credencial_no_comando" and exc.value.status == 409
    assert len(harness.ai.calls) == antes


async def test_eco_de_credencial_na_saida_do_modelo_e_retirado() -> None:
    r = normalizar(CommandRefinement(command="Objetivo: entrar\nDados: Senha: segredo123", ready=True), redact)
    assert "segredo123" not in r.command
    assert any("credencial" in n for n in r.notes)


async def test_pronto_com_pergunta_aberta_nao_vale() -> None:
    r = normalizar(CommandRefinement(command="Objetivo: x", ready=True,
                                     questions=[RefineQuestion(field="Destinatário X", question="Para quem?")]), redact)
    assert r.ready is False
    assert r.questions[0].field == "destinat_rio_x"             # campo normalizado para snake_case ASCII


async def test_pergunta_de_destino_nao_se_responde_por_texto(harness: Harness) -> None:
    with pytest.raises(RunError) as exc:
        await _assistente(harness).refinar(CommandRefineBody(
            command=COMMAND, instance_ids=["android-01"],
            answers=[RefineAnswerIn(field="profile_id", answer="p-123")]))
    assert exc.value.code == "pergunta_de_destino"


async def test_com_run_id_as_perguntas_do_planejador_vao_ao_refinador(harness: Harness) -> None:
    run_id = await _em_needs_input(harness)
    r = await _assistente(harness).refinar(CommandRefineBody(command=INCOMPLETO, run_id=run_id))
    chamada = [c for c in harness.ai.calls if c.get("kind") == "refine"][-1]
    assert chamada["pending"] == 2                 # destinatário e texto da mensagem
    assert {q.field for q in r.questions} >= {"recipient", "message"}
    # o custo do refinamento fica na execução que está sendo respondida
    assert harness.state is not None
    assert harness.state.repo.db.scalar("SELECT COUNT(*) FROM ai_calls WHERE run_id=?", (run_id,)) >= 1


async def test_sucessora_leva_o_pedido_e_cancela_a_antiga(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id = await _em_needs_input(harness)
    a = _assistente(harness)
    nova, criada = a.sucessora(run_id, RunSuccessorBody(command=COMMAND, mode="plan"))
    assert criada and nova.id != run_id and nova.command == COMMAND
    assert nova.instance_ids == ["android-01"]
    antiga = st.repo.run_row(run_id)
    assert antiga["status"] == "cancelled"
    assert nova.short_id in antiga["status_detail"]
    await harness.wait_run(nova.id, ("planned",))
    # duplo clique: a mesma resposta devolve a MESMA sucessora, sem criar outra
    de_novo, criada2 = a.sucessora(run_id, RunSuccessorBody(command=COMMAND, mode="plan"))
    assert de_novo.id == nova.id and not criada2
    assert st.repo.db.scalar("SELECT COUNT(*) FROM runs") == 2


async def test_sucessora_de_execucao_que_nao_espera_resposta_e_recusada(harness: Harness) -> None:
    run = harness.run(["android-01"], mode="plan")
    await harness.wait_run(run.id, ("planned",))
    with pytest.raises(RunError) as exc:
        _assistente(harness).sucessora(run.id, RunSuccessorBody(command=COMMAND + " agora"))
    assert exc.value.code == "invalid_state"


async def test_sucessora_preserva_personas_do_pedido(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id = await _em_needs_input(harness)
    # A foto guarda o pedido como veio; aqui simula-se um pedido por persona (o que o "Repetir" perderia).
    foto = loads(st.repo.run_row(run_id)["targets"], {})
    foto["pedido"] = {"instance_ids": ["android-01"], "profile_ids": [], "targets": []}
    st.repo.db.execute("UPDATE runs SET targets=? WHERE id=?", (dumps(foto), run_id))
    nova, _ = _assistente(harness).sucessora(run_id, RunSuccessorBody(command=COMMAND))
    assert nova.instance_ids == ["android-01"]


async def test_rotas_http(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id = await _em_needs_input(harness)
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/commands/refine", json={"command": INCOMPLETO, "run_id": run_id})
        assert r.status_code == 200, r.text
        assert r.json()["command"].startswith("Objetivo:")
        r = await c.post("/api/commands/refine", json={"command": "x Senha: abc12345", "instance_ids": ["android-01"]})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "credencial_no_comando"
        r = await c.post(f"/api/runs/{run_id}/successor",
                         json={"command": COMMAND, "mode": "plan"})
        assert r.status_code == 200, r.text
        assert r.json()["id"] != run_id
        r = await c.post(f"/api/runs/{run_id}/successor", json={"command": "abc", "mode": "nada"})
        assert r.status_code == 422



async def test_comando_que_cita_a_persona_nao_trava_a_sucessora(harness: Harness) -> None:
    """O texto guardado na execução é o INTEIRO ("peça para o Lucas…"); o destino dela está na foto (alvo ecoado).
    Refinar parte do texto sem destinos, e a sucessora não cai em `alvos_nao_confirmados` nem perde a persona."""
    st = harness.state
    assert st is not None
    lucas = st.social.create_profile(ProfileCreate(username=f"lucas.{pysecrets.token_hex(3)}",
                                                   instance_id="android-02", first_name="Lucas",
                                                   last_name="Teste")).id
    comando = "peça para o Lucas abrir o QA Messenger e enviar uma mensagem"
    run = st.runs.create(RunCreate(command=comando, mode="plan", idempotency_key=f"t-{pysecrets.token_hex(6)}",
                                   targets=[RunTarget(profile_id=lucas, instance_ids=["android-02"])]))
    await harness.wait_run(run.id, ("needs_input",))
    a = _assistente(harness)
    r = await a.refinar(CommandRefineBody(command=st.repo.run_row(run.id)["command"], run_id=run.id))
    assert "Lucas" not in r.command
    # Mesmo que a pessoa mande o texto com o destino, a sucessora nasce com o alvo da foto.
    nova, criada = a.sucessora(run.id, RunSuccessorBody(command=comando + " dizendo \"oi\" para QA-001"))
    assert criada and nova.instance_ids == ["android-02"]
    foto = loads(st.repo.run_row(nova.id)["targets"])
    assert [(x["profile_id"], x["instance_id"]) for x in foto["alvos"]] == [(lucas, "android-02")]
