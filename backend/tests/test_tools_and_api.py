"""Validação das ferramentas da IA, contrato HTTP e reconexão do painel."""
from __future__ import annotations

import httpx
import pytest

from app.automation.hierarchy import parse_hierarchy
from app.automation.tools import (TOOLS, Tap, ToolContext, ToolValidationError, execute_tool, looks_like_commit,
                                  tool_definitions, validate_call)
from app.automation.driver import DriverError
from app.main import create_app
from app.taskqueue.repository import resolve_templates
from app.workers.protocol import Hello, WorkerResources

from .conftest import Harness
from .fake_device import FakeQaDevice


def test_so_ferramentas_implementadas_e_argumentos_validos() -> None:
    with pytest.raises(ToolValidationError):
        validate_call("shell", {"cmd": "rm -rf /"})                       # texto da IA nunca vira comando
    with pytest.raises(ToolValidationError):
        validate_call("tap", {"rationale": "x", "element_id": "e1", "x": None, "y": None, "is_commit_action": False,
                              "extra": 1})                                # campo extra é rejeitado
    with pytest.raises(ToolValidationError):
        validate_call("scroll", {"rationale": "x", "direction": "diagonal", "element_id": None})
    ok = validate_call("tap", {"rationale": "abrir", "element_id": "e3", "x": None, "y": None, "is_commit_action": False})
    assert isinstance(ok, Tap)
    for t in tool_definitions():                                          # schema fechado para tool calling
        schema = t["input_schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"]) == set(TOOLS[t["name"]].model_fields)
        assert t.get("strict", False) is (t["name"] in {"tap", "long_press", "drag", "type_text", "step_done", "step_blocked"})
    assert set(TOOLS) >= {"observe_screen", "find_element", "tap", "long_press", "drag", "scroll", "type_text",
                          "press_back", "open_app", "wait_for", "verify_state", "step_done", "step_blocked"}


async def test_alvo_e_resolvido_antes_de_tocar_e_open_app_e_restrito() -> None:
    fake = FakeQaDevice(account="qa-user-01", screen="home")
    tree = parse_hierarchy(fake.page_source())

    async def call(fn, *a):
        return fn(*a)

    ctx = ToolContext(io=fake, call=call, tree=tree, width=720, height=1280, image_scale=1.0,
                      app_package="com.pocqa.messenger", app_activity=None, allowed_packages={"com.pocqa.messenger"})
    with pytest.raises(DriverError) as e1:
        await execute_tool(ctx, "tap", validate_call("tap", {"rationale": "x", "element_id": "e999", "x": None,
                                                              "y": None, "is_commit_action": False}))
    assert e1.value.effect_possible is False and not any(c.startswith("tap") for c in fake.calls)
    with pytest.raises(DriverError):
        await execute_tool(ctx, "tap", validate_call("tap", {"rationale": "x", "element_id": None, "x": 5000, "y": 10,
                                                              "is_commit_action": False}))
    with pytest.raises(DriverError) as e2:
        await execute_tool(ctx, "open_app", validate_call("open_app", {"rationale": "x", "package": "com.banco.app"}))
    assert "não está entre os apps configurados" in str(e2.value)
    row = next(e for e in tree.elements if e.text == "QA-001")
    await execute_tool(ctx, "tap", validate_call("tap", {"rationale": "abrir", "element_id": row.id, "x": None,
                                                          "y": None, "is_commit_action": False}))
    assert fake.screen == "chat" and fake.contact == "QA-001"
    chat = parse_hierarchy(fake.page_source())
    assert looks_like_commit(next(e for e in chat.elements if e.resource_id.endswith("send_button")))
    assert not looks_like_commit(next(e for e in chat.elements if e.resource_id.endswith("chat_back")))


def test_campo_de_senha_marca_tela_sensivel_e_mascara_valor() -> None:
    fake = FakeQaDevice(account="qa-user-01", screen="login")
    tree = parse_hierarchy(fake.page_source())
    assert tree.sensitive
    xml = ('<hierarchy><node class="android.widget.EditText" package="p" text="1234" resource-id="p:id/pin" '
           'content-desc="" clickable="true" enabled="true" focused="false" password="true" bounds="[0,0][10,10]"/></hierarchy>')
    assert parse_hierarchy(xml).elements[0].text == "••••"


def test_templates_so_resolvem_variaveis_conhecidas() -> None:
    out = resolve_templates("Teste {instance_id} {run_id} {desconhecida} {0} {__class__}", {"instance_id": "a1", "run_id": "r1"})
    assert out == "Teste a1 r1 {desconhecida} {0} {__class__}"


async def test_api_dedup_validacao_e_reconexao_por_snapshot(harness: Harness) -> None:
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        body = {"command": "Abra o QA Messenger e envie \"oi {instance_id}\" para o contato QA-002. Confirme que foi enviada.",
                "instance_ids": ["android-01"], "idempotency_key": "http-dupla-0003", "mode": "execute"}
        r1 = await c.post("/api/runs", json=body)
        r2 = await c.post("/api/runs", json=body)                          # repetição da mesma requisição HTTP
        assert r1.status_code == r2.status_code == 200
        assert r1.json()["id"] == r2.json()["id"] and r2.json()["deduplicated"] is True
        assert (await c.post("/api/runs", json={**body, "instance_ids": ["android-99"], "idempotency_key": "outra-chave-1"})
                ).status_code == 400
        assert (await c.post("/api/runs", json={**body, "idempotency_key": "x"})).status_code == 422
        await harness.wait_run(r1.json()["id"])

        snap = (await c.get("/api/snapshot")).json()                       # "reabrir o painel"
        assert snap["last_event_id"] > 0 and len(snap["instances"]) == 3
        assert [r["id"] for r in snap["runs"]] == [r1.json()["id"]]        # histórico preservado, nada reenfileirado
        events = (await c.get(f"/api/runs/{r1.json()['id']}/events", params={"after": 0})).json()
        assert events and events[-1]["id"] <= snap["last_event_id"]
        later = (await c.get(f"/api/runs/{r1.json()['id']}/events", params={"after": snap["last_event_id"]})).json()
        assert later == []
        rep = (await c.get(f"/api/runs/{r1.json()['id']}/report")).json()
        assert rep["per_instance"][0]["proven"] is True and "SIMULADO" in rep["markdown"]
        bad = await c.put("/api/settings", json={"max_active_devices": 99})
        assert bad.status_code == 400
        # CSRF / DNS rebinding: origem estranha em método que altera estado e host não-loopback são recusados
        evil = await c.post(f"/api/runs/{r1.json()['id']}/cancel", headers={"Origin": "https://evil.example"})
        assert evil.status_code == 403 and evil.json()["detail"]["code"] == "forbidden_origin"
        assert (await c.get("/api/health", headers={"Host": "attacker.example"})).status_code == 403
        ok = await c.post("/api/instances/android-01/control/take", headers={"Origin": "http://127.0.0.1:5173"})
        assert ok.status_code == 200
        assert (await c.post("/api/instances/android-01/actions/reset", json={})).status_code == 409   # exige confirm
        assert (await c.post("/api/apps", json={"name": "X", "package": "com.x.app", "apk_path": "C:/Windows/evil.apk"})
                ).status_code == 400                                        # APK fora dos diretórios permitidos
    assert len(harness.fakes["android-01"].messages) == 1
    assert harness.fakes["android-01"].messages[0].contact == "QA-002"


async def test_snapshot_traz_as_recentes_e_todas_as_em_andamento(harness: Harness) -> None:
    """RF-05: o painel conta as execuções em andamento, e uma `planned` de semanas atrás ficava fora das 20 mais
    recentes (o contador abria em 0). O snapshot leva as 20 recentes E todas as não terminais. D1: `needs_input` é
    pendência e entra também (a caixa de Pendências conta todas, desde a primeira carga)."""
    db = harness.state.db
    sql = ("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
           " VALUES (?, ?, 'x', 'execute', ?, 1, '[]', ?)")
    db.execute(sql, ("r-antiga-planejada", "k-antiga-1", "planned", "2026-08-01T10:00:00Z"))
    db.execute(sql, ("r-antiga-pausada", "k-antiga-2", "paused", "2026-08-02T10:00:00Z"))
    db.execute(sql, ("r-antiga-pendencia", "k-antiga-3", "needs_input", "2026-08-03T10:00:00Z"))
    db.execute(sql, ("r-antiga-concluida", "k-antiga-4", "completed", "2026-08-04T10:00:00Z"))
    for i in range(25):
        db.execute(sql, (f"r-recente-{i:02d}", f"k-recente-{i:02d}", "completed", f"2026-09-{i + 1:02d}T10:00:00Z"))
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        ids = [r["id"] for r in (await c.get("/api/snapshot")).json()["runs"]]
    assert len(ids) == 23 and len(set(ids)) == 23
    assert ids[:20] == [f"r-recente-{i:02d}" for i in range(24, 4, -1)]            # as 20 mais recentes, em ordem
    # + as não terminais, mais novas antes: a que espera resposta e as em andamento
    assert ids[20:] == ["r-antiga-pendencia", "r-antiga-pausada", "r-antiga-planejada"]
    assert "r-antiga-concluida" not in ids


async def test_manutencao_do_worker_recusa_comando_unico_e_lote(harness: Harness) -> None:
    """`aceita_trabalho` precisa ser CONSULTADO no despacho de comando — não só existir. Achados #9/#22/#42/#156."""
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    reg = harness.state.workers
    reg.autenticar(Hello(worker_id="worker-lan-01", name="Notebook da LAN", agent_version="0.1.0", os="windows",
                         max_slots=6, verbs=["stop"], devices=[],
                         resources=WorkerResources(cpu_count=4, ram_total_mb=8192, ram_free_mb=4096)),
                  token=None, enrollment=reg.criar_inscricao())
    async def _noop(payload: dict) -> None:
        return None
    reg.attach("worker-lan-01", _noop)                  # conectado, para não confundir com "não conectado"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        bound = await c.put("/api/instances/android-01", json={"worker_id": "worker-lan-01"})
        assert bound.status_code == 200

        reg.set_maintenance("worker-lan-01", True)
        recusa = await c.post("/api/instances/android-01/actions/stop", json={})
        assert recusa.status_code == 409
        assert "manutenção" in recusa.json()["detail"]["message"]

        lote = await c.post("/api/instances/bulk", json={"ids": ["android-01"], "action": "stop"})
        assert lote.status_code == 202
        assert lote.json()["accepted"] == []
        assert lote.json()["rejected"][0]["id"] == "android-01"
        assert "manutenção" in lote.json()["rejected"][0]["reason"]

        # Sai da manutenção: o mesmo comando volta a ser aceito.
        reg.set_maintenance("worker-lan-01", False)
        aceito = await c.post("/api/instances/android-01/actions/stop", json={})
        assert aceito.status_code == 202


async def test_remover_e_rotacionar_credencial_de_worker_pelo_http(harness: Harness) -> None:
    """Achado #168/#150: `docs/worker.md` manda 'remova o worker no painel' — a rota precisa existir de verdade."""
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    reg = harness.state.workers
    reg.autenticar(Hello(worker_id="worker-lan-01", name="Notebook da LAN", agent_version="0.1.0", os="windows",
                         max_slots=6, verbs=["stop"], devices=[],
                         resources=WorkerResources(cpu_count=4, ram_total_mb=8192, ram_free_mb=4096)),
                  token=None, enrollment=reg.criar_inscricao())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        # rotacionar: existe, muda o hash, some do live.
        rot = await c.post("/api/workers/worker-lan-01/rotate-credential")
        assert rot.status_code == 200
        assert isinstance(rot.json()["credential"], str) and len(rot.json()["credential"]) > 20
        assert (await c.post("/api/workers/worker-fantasma/rotate-credential")).status_code == 404

        # amarra um aparelho ao worker antes de remover
        assert (await c.put("/api/instances/android-01", json={"worker_id": "worker-lan-01"})).status_code == 200

        # conectado: recusa sem force
        async def _noop(payload: dict) -> None:
            return None
        reg.attach("worker-lan-01", _noop)
        recusa = await c.delete("/api/workers/worker-lan-01")
        assert recusa.status_code == 409 and recusa.json()["detail"]["code"] == "connected"

        ok = await c.request("DELETE", "/api/workers/worker-lan-01", json={"force": True})
        assert ok.status_code == 200 and ok.json()["worker_id"] == "worker-lan-01"
        # Sobra UM worker: este próprio servidor. Desde o `LocalWorker`, o central tem linha em `workers` como
        # qualquer outra máquina — e é por isso que a lista não fica vazia quando o notebook é removido.
        assert [w["id"] for w in (await c.get("/api/workers")).json()] == [harness.cfg.owner_id]
        # instância continua existindo, só sem dono
        inst = next(i for i in (await c.get("/api/instances")).json() if i["id"] == "android-01")
        assert inst["worker_id"] is None

        assert (await c.delete("/api/workers/worker-lan-01")).status_code == 404


def _contexto_de_chat(fake: FakeQaDevice) -> ToolContext:
    async def call(fn, *a):
        return fn(*a)

    async def observe():
        return parse_hierarchy(fake.page_source())

    return ToolContext(io=fake, call=call, tree=parse_hierarchy(fake.page_source()), width=720, height=1280,
                       image_scale=1.0, app_package="com.pocqa.messenger", app_activity=None,
                       allowed_packages={"com.pocqa.messenger"}, observe=observe)


async def test_digitacao_confere_o_campo_e_completa_o_que_foi_cortado() -> None:
    """Execução e31953: `mobile: type` cortou um comentário de 125 caracteres em 22 num aparelho lento, e o resultado
    dizia 125. Agora a ferramenta relê o campo, completa o que falta e devolve o que de fato entrou.

    A completação REAPLICA o texto inteiro substituindo o campo (pacote "digitacao", r-20260928195344-02ee9e), em
    vez de acrescentar só o resto: substituir é idempotente — se a primeira escrita chegar atrasada, o campo termina
    igual, sem duplicar —, e acrescentar não é. Por isso a segunda chamada é o texto inteiro, com limpeza."""
    fake = FakeQaDevice(account="qa-user-01", screen="chat", contact="QA-001")
    original = fake.type_text
    chamadas: list[str] = []
    limpezas: list[bool] = []

    def corta_a_primeira(text: str, *, clear_first: bool) -> None:
        chamadas.append(text)
        limpezas.append(clear_first)
        original(text[:22] if len(chamadas) == 1 else text, clear_first=clear_first)

    fake.type_text = corta_a_primeira                                     # type: ignore[method-assign]
    texto = "rapaz, que tema importante esse do Setembro Amarelo, parabéns pelo cuidado no post"
    saida = await execute_tool(_contexto_de_chat(fake), "type_text", validate_call("type_text", {
        "rationale": "comentar", "text": texto, "element_id": None, "clear_first": True, "press_enter": False,
        "is_commit_action": False}))
    assert fake.input_text == texto and chamadas == [texto, texto]      # completou reaplicando, sem duplicar
    assert limpezas == [True, True]                                       # substituiu o campo nas duas vezes
    assert saida.result["verified"] is True and saida.result["completed_after_cut"] == 1
    assert saida.result["typed_chars"] == len(texto)


async def test_digitacao_nao_duplica_texto_transformado_nem_confirma_com_enter() -> None:
    """Se o app transformou o texto (não aparece nem como começo), a ferramenta não digita de novo e não aperta
    Enter: num chat, isso mandaria a mensagem errada. O modelo recebe `verified: False` com o que falta."""
    fake = FakeQaDevice(account="qa-user-01", screen="chat", contact="QA-001")
    chamadas: list[str] = []

    def transforma(text: str, *, clear_first: bool) -> None:
        chamadas.append(text)
        fake.input_text = "texto que o app reescreveu"

    fake.type_text = transforma                                           # type: ignore[method-assign]
    saida = await execute_tool(_contexto_de_chat(fake), "type_text", validate_call("type_text", {
        "rationale": "responder", "text": "oi, tudo bem?", "element_id": None, "clear_first": True,
        "press_enter": True, "is_commit_action": False}))
    assert chamadas == ["oi, tudo bem?"] and not any(c.startswith("key:enter") for c in fake.calls)
    assert saida.result["verified"] is False and saida.result["enter"] is False
    assert saida.result["typed_chars"] == 0 and saida.result["missing"] == "oi, tudo bem?"


async def test_digitacao_completa_confirma_e_aperta_enter_quando_pedido() -> None:
    fake = FakeQaDevice(account="qa-user-01", screen="chat", contact="QA-001")
    saida = await execute_tool(_contexto_de_chat(fake), "type_text", validate_call("type_text", {
        "rationale": "responder", "text": "bom dia", "element_id": None, "clear_first": True, "press_enter": True,
        "is_commit_action": False}))
    assert saida.result["verified"] is True and saida.result["enter"] is True and "completed_after_cut" not in saida.result
