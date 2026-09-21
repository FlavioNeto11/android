"""Registro de workers: identidade, inscrição de uso único, batida, cerca e despacho.

Estes testes cobrem a LÓGICA do canal com um agente falso. O transporte WebSocket em si é fino e é validado
contra o worker de verdade — está dito assim de propósito, para não confundir "testado com simulação" com
"provado em infraestrutura real".
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.db import Database
from app.workers.protocol import Dispatch, Heartbeat, Hello, Result, WorkerDevice, WorkerResources
from app.workers.registry import HEARTBEAT_S, WorkerError, WorkerRegistry
from .conftest import make_config


def _registro(tmp_path: Path) -> WorkerRegistry:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_path)
    db.migrate()
    return WorkerRegistry(db)


def _hello(**kw: Any) -> Hello:
    base = dict(worker_id="worker-lan-01", name="Notebook da LAN", agent_version="0.1.0", os="windows",
                max_slots=6, verbs=["start", "stop", "hibernate", "reset", "home"],
                devices=[WorkerDevice(serial="emulator-5554", avd_name="worker-01", state="online", adb_port=5555)],
                resources=WorkerResources(cpu_count=12, ram_total_mb=65273, ram_free_mb=46367))
    return Hello(**{**base, **kw})


class AgenteFalso:
    """Registra o que o central mandou e responde como um worker responderia."""

    def __init__(self) -> None:
        self.enviados: list[dict[str, Any]] = []

    async def send(self, payload: dict[str, Any]) -> None:
        self.enviados.append(payload)


# ---------------------------------------------------------------- inscrição
def test_inscricao_e_de_uso_unico_e_devolve_credencial_uma_vez(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    token = reg.criar_inscricao("notebook da sala")
    # O token em claro existe uma vez; o banco guarda só o hash.
    assert reg.db.scalar("SELECT COUNT(*) FROM worker_enrollments WHERE token_hash=?", (token,)) == 0

    credencial = reg.autenticar(_hello(), token=None, enrollment=token)
    assert credencial and len(credencial) > 20
    # Segunda vez com o MESMO token: recusado, e com o motivo.
    with pytest.raises(WorkerError) as exc:
        reg.autenticar(_hello(worker_id="worker-lan-02"), token=None, enrollment=token)
    assert exc.value.code == "enrollment_used"


def test_credencial_certa_entra_e_errada_nao(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    credencial = reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    assert reg.autenticar(_hello(), token=credencial, enrollment=None) == ""
    with pytest.raises(WorkerError) as exc:
        reg.autenticar(_hello(), token="credencial-errada", enrollment=None)
    assert exc.value.code == "bad_credential"


def test_worker_desconhecido_explica_o_caminho(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    with pytest.raises(WorkerError) as exc:
        reg.autenticar(_hello(), token="qualquer", enrollment=None)
    assert exc.value.code == "not_enrolled"
    assert "Gere um token de inscrição no painel" in exc.value.message


def test_protocolo_do_agente_mais_novo_que_o_servidor_e_recusado(tmp_path: Path) -> None:
    """Melhor recusar do que agir com mensagens que não se entende."""
    reg = _registro(tmp_path)
    with pytest.raises(WorkerError) as exc:
        reg.autenticar(_hello(protocol=99), token=None, enrollment=reg.criar_inscricao())
    assert exc.value.code == "protocol_too_new"


# ---------------------------------------------------------------- declaração e saúde
def test_o_worker_declara_o_que_e_e_isso_aparece_no_dto(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    reg.autenticar(_hello(appium_mode="local", appium_url="http://127.0.0.1:4723"), token=None,
                   enrollment=reg.criar_inscricao())
    dto = reg.dtos()[0]
    assert dto.id == "worker-lan-01" and dto.name == "Notebook da LAN"
    assert dto.appium_mode == "local" and dto.max_slots == 6
    assert "hibernate" in dto.verbs and dto.resources.ram_total_mb == 65273
    assert [d.serial for d in dto.devices] == ["emulator-5554"]
    assert dto.connected is False                      # declarou-se, mas o canal ainda não está instalado


def test_socket_caindo_nao_marca_offline_mas_a_falta_de_batida_marca(tmp_path: Path) -> None:
    """Socket cai por rede piscando. Tratar as duas coisas como a mesma é o defeito que o STF arrastou por anos."""
    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    reg.attach("worker-lan-01", AgenteFalso().send)
    reg.detach("worker-lan-01", "cabo solto")
    assert reg.dtos()[0].observed_state == "online"     # ainda dentro do prazo da batida
    assert reg.dtos()[0].state_detail == "cabo solto"

    # Envelhece a última batida além do limite e ceifa.
    reg.db.execute("UPDATE workers SET last_seen_at='2020-01-01T00:00:00.000Z' WHERE id=?", ("worker-lan-01",))
    assert reg.reap() == ["worker-lan-01"]
    assert reg.dtos()[0].state == "offline"


def test_manutencao_e_decisao_e_nao_esconde_o_estado_observado(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    reg.attach("worker-lan-01", AgenteFalso().send)
    reg.set_maintenance("worker-lan-01", True)
    dto = reg.dtos()[0]
    assert dto.state == "maintenance" and dto.observed_state == "online" and dto.maintenance is True
    assert "manutenção" in (reg.aceita_trabalho("worker-lan-01") or "")
    reg.set_maintenance("worker-lan-01", False)
    assert reg.aceita_trabalho("worker-lan-01") is None


def test_worker_desconectado_nao_aceita_trabalho(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    assert "não está conectado" in (reg.aceita_trabalho("worker-lan-01") or "")


def test_batida_atualiza_recursos_e_inventario(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    reg.on_heartbeat("worker-lan-01", Heartbeat(
        resources=WorkerResources(ram_free_mb=40000, cpu_percent=42.0),
        devices=[WorkerDevice(serial="emulator-5554", state="online"),
                 WorkerDevice(serial="emulator-5556", state="booting")]))
    dto = reg.dtos()[0]
    assert dto.resources.ram_free_mb == 40000 and dto.resources.cpu_percent == 42.0
    assert len(dto.devices) == 2


# ---------------------------------------------------------------- despacho
async def _pronto(tmp_path: Path) -> tuple[WorkerRegistry, AgenteFalso]:
    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    agente = AgenteFalso()
    reg.attach("worker-lan-01", agente.send)
    return reg, agente


async def test_despacho_espera_o_desfecho_do_worker(tmp_path: Path) -> None:
    reg, agente = await _pronto(tmp_path)
    msg = Dispatch(command_id="c-1", fence=1, verb="stop", instance_id="android-09", serial="emulator-5554")

    async def responde() -> None:
        await asyncio.sleep(0.02)
        assert reg.on_ack("worker-lan-01", "c-1") is True
        assert reg.on_result("worker-lan-01", Result(command_id="c-1", outcome="succeeded"), fence=1) is True

    tarefa = asyncio.create_task(responde())
    r = await reg.dispatch("worker-lan-01", msg)
    await tarefa
    assert r.outcome == "succeeded"
    assert agente.enviados[0]["verb"] == "stop" and agente.enviados[0]["fence"] == 1


async def test_worker_que_nao_responde_no_prazo_deixa_o_resultado_INCERTO(tmp_path: Path) -> None:
    """Nunca falha por silêncio: o worker pode ter agido e não conseguido responder."""
    reg, _ = await _pronto(tmp_path)
    msg = Dispatch(command_id="c-2", fence=1, verb="reset", instance_id="android-09", serial="emulator-5554",
                   timeout_s=0.05)
    r = await reg.dispatch("worker-lan-01", msg)
    assert r.outcome == "uncertain" and "não respondeu" in (r.reason or "")


async def test_resultado_com_cerca_velha_e_recusado(tmp_path: Path) -> None:
    """Worker que voltou do limbo não sobrescreve o presente."""
    reg, _ = await _pronto(tmp_path)
    msg = Dispatch(command_id="c-3", fence=7, verb="stop", instance_id="android-09", serial="emulator-5554",
                   timeout_s=0.3)

    async def responde_velho() -> None:
        await asyncio.sleep(0.02)
        assert reg.on_result("worker-lan-01", Result(command_id="c-3", outcome="succeeded"), fence=3) is False

    tarefa = asyncio.create_task(responde_velho())
    r = await reg.dispatch("worker-lan-01", msg)
    await tarefa
    assert r.outcome == "uncertain"                     # o resultado velho foi descartado; o prazo decidiu


async def test_queda_do_canal_deixa_o_que_estava_em_voo_INCERTO(tmp_path: Path) -> None:
    reg, _ = await _pronto(tmp_path)
    msg = Dispatch(command_id="c-4", fence=1, verb="stop", instance_id="android-09", serial="emulator-5554",
                   timeout_s=5)

    async def cai() -> None:
        await asyncio.sleep(0.02)
        reg.detach("worker-lan-01", "socket fechou")

    tarefa = asyncio.create_task(cai())
    r = await reg.dispatch("worker-lan-01", msg)
    await tarefa
    assert r.outcome == "uncertain" and "socket fechou" in (r.reason or "")


async def test_conexao_nova_do_mesmo_worker_derruba_a_anterior(tmp_path: Path) -> None:
    """Um dono por worker, sempre — senão dois canais disputariam o mesmo aparelho."""
    reg, _ = await _pronto(tmp_path)
    msg = Dispatch(command_id="c-5", fence=1, verb="stop", instance_id="android-09", serial="emulator-5554",
                   timeout_s=5)

    async def reconecta() -> None:
        await asyncio.sleep(0.02)
        reg.attach("worker-lan-01", AgenteFalso().send)

    tarefa = asyncio.create_task(reconecta())
    r = await reg.dispatch("worker-lan-01", msg)
    await tarefa
    assert r.outcome == "uncertain" and "substituiu" in (r.reason or "")


async def test_despacho_para_worker_offline_falha_na_hora(tmp_path: Path) -> None:
    """Falha ao ENVIAR é o único caso em que se pode afirmar que nada aconteceu."""
    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    with pytest.raises(WorkerError) as exc:
        await reg.dispatch("worker-lan-01", Dispatch(command_id="c-6", fence=1, verb="stop",
                                                    instance_id="android-09", serial="emulator-5554"))
    assert exc.value.code == "worker_offline"


def test_prazo_da_batida_e_coerente_com_o_ceifador() -> None:
    """Se o ceifador rodasse mais rápido que a batida, todo worker saudável seria marcado offline."""
    assert HEARTBEAT_S > 0


# ---------------------------------------------------------------- roteamento: painel → agente
class AgenteQueResponde(AgenteFalso):
    """Agente falso que responde ao despacho como o agente real responderia."""

    def __init__(self, registry: WorkerRegistry, worker_id: str, outcome: str = "succeeded",
                 reason: str | None = None) -> None:
        super().__init__()
        self.reg, self.worker_id, self.outcome, self.reason = registry, worker_id, outcome, reason

    async def send(self, payload: dict[str, Any]) -> None:
        await super().send(payload)
        if payload.get("type") != "dispatch":
            return
        cid, cerca = payload["command_id"], payload["fence"]
        self.reg.on_ack(self.worker_id, cid)
        self.reg.on_result(self.worker_id, Result(command_id=cid, outcome=self.outcome, reason=self.reason,
                                                 data={"started": True}), fence=cerca)


async def _harness_com_worker(tmp_path: Path, outcome: str = "succeeded", reason: str | None = None):
    """Harness com `android-03` hospedado num worker conectado, como ficaria em produção."""
    import httpx

    from app.main import create_app
    from .conftest import Harness

    h = Harness(tmp_path, 3, external={"android-03": "127.0.0.1:15555"})
    await h.boot()
    assert h.state is not None
    reg = h.state.workers
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    agente = AgenteQueResponde(reg, "worker-lan-01", outcome, reason)
    reg.attach("worker-lan-01", agente.send)
    # Amarra a instância ao worker, como o cadastro do servidor faria.
    h.state.db.execute("UPDATE instances SET worker_id=? WHERE id=?", ("worker-lan-01", "android-03"))
    rt = h.state.devices.get("android-03")
    rt.worker_id = "worker-lan-01"
    h.state.devices.bind_worker("worker-lan-01", list(_hello().verbs))

    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    cliente = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return h, reg, agente, cliente


async def test_com_worker_o_aparelho_remoto_ganha_ciclo_de_vida(tmp_path: Path) -> None:
    """É a correção de fundo: antes, `stop` num aparelho de outra máquina era recusado porque não havia agente."""
    h, _reg, agente, cliente = await _harness_com_worker(tmp_path)
    try:
        async with cliente as c:
            inst = next(i for i in (await c.get("/api/instances")).json() if i["id"] == "android-03")
            # Com o agente declarando ciclo de vida, o aparelho passa a aceitar o que antes era recusado.
            assert {"stop", "hibernate", "reset", "start"} <= set(inst["supported_verbs"])

            r = await c.post("/api/instances/android-03/actions/stop", json={"idempotency_key": "w-stop-0001"})
            assert r.status_code == 202, r.text
            cid = r.json()["command_id"]
            await h.wait(lambda: h.state.commands.get(cid)["state"] == "succeeded",  # type: ignore[union-attr]
                         what="comando concluído pelo worker")
            registro = h.state.commands.get(cid)          # type: ignore[union-attr]
            assert registro["worker_id"] == "worker-lan-01"
            # O despacho levou a cerca, e o agente a devolveu.
            despacho = next(p for p in agente.enviados if p.get("type") == "dispatch")
            assert despacho["verb"] == "stop" and despacho["instance_id"] == "android-03"
            assert despacho["fence"] == registro["fence"]
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_desfecho_incerto_do_worker_chega_como_incerto_ao_painel(tmp_path: Path) -> None:
    h, _reg, _a, cliente = await _harness_com_worker(tmp_path, outcome="uncertain",
                                                     reason="o aparelho não completou o boot em 480 s")
    try:
        async with cliente as c:
            r = await c.post("/api/instances/android-03/actions/start", json={"idempotency_key": "w-start-0001"})
            cid = r.json()["command_id"]
            await h.wait(lambda: h.state.commands.get(cid)["state"] == "uncertain",  # type: ignore[union-attr]
                         what="comando incerto")
            assert "não completou o boot" in h.state.commands.get(cid)["reason"]   # type: ignore[union-attr]
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_worker_que_cai_deixa_o_aparelho_sem_ciclo_de_vida_de_novo(tmp_path: Path) -> None:
    """Sem agente do outro lado, o painel para de oferecer botão que não faria nada."""
    h, reg, _a, cliente = await _harness_com_worker(tmp_path)
    try:
        async with cliente as c:
            reg.detach("worker-lan-01", "socket fechou")
            h.state.devices.bind_worker("worker-lan-01", None)    # type: ignore[union-attr]
            inst = next(i for i in (await c.get("/api/instances")).json() if i["id"] == "android-03")
            assert "stop" not in inst["supported_verbs"] and "reset" not in inst["supported_verbs"]
            r = await c.post("/api/instances/android-03/actions/stop", json={"idempotency_key": "w-stop-0002"})
            assert r.status_code == 409
            assert "outra máquina" in r.json()["detail"]["message"]
    finally:
        if h.state is not None:
            await h.state.stop()
