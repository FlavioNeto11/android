"""O mesmo verbo, os mesmos efeitos — no aparelho daqui e no aparelho da outra máquina.

O que estes testes trancam é a diferença que existia entre os dois caminhos: o caminho do worker despachava o
comando e não aplicava NADA no central. Depois de um `stop` remoto bem-sucedido o cartão acusava "sumiu do ADB"
(alarme falso, com attention) e o monitor tentava readotar a cada 30 s um aparelho desligado de propósito;
`hibernate` nunca virava `hibernated`, então o painel oferecia "Iniciar" (boot a frio) e o snapshot de ~1,5 GB
salvo na máquina do worker nunca era usado; e `reset` apagava os dados sem invalidar sessão, evidência de conta
nem o estado do app — o objetivo seguinte era despachado para um aparelho vazio.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.models import InstalledAppState, InstanceState
from app.workers.protocol import Result
from app.workers.registry import WorkerRegistry

from .conftest import Harness
from .test_workers import AgenteFalso, _hello

#: Um worker que declara o ciclo de vida inteiro — é o caso que o aceite 1 descreve (mesmos verbos aqui e lá).
VERBOS = ["create", "start", "stop", "hibernate", "wake", "restart", "reset"]


class AgenteComDados(AgenteFalso):
    """Agente falso que responde ao despacho com o corpo que o agente real devolveria naquele verbo."""

    def __init__(self, registry: WorkerRegistry, worker_id: str, *, outcome: str = "succeeded",
                 data: dict[str, Any] | None = None, reason: str | None = None) -> None:
        super().__init__()
        self.reg, self.worker_id, self.outcome, self.data = registry, worker_id, outcome, data
        self.reason = reason

    async def send(self, payload: dict[str, Any]) -> None:
        await super().send(payload)
        if payload.get("type") != "dispatch":
            return
        cid, cerca = payload["command_id"], payload["fence"]
        self.reg.on_ack(self.worker_id, cid)
        self.reg.on_result(self.worker_id, Result(command_id=cid, outcome=self.outcome, reason=self.reason,
                                                  data=self.data), fence=cerca)


async def _parque_com_worker(tmp_path: Path, *, outcome: str = "succeeded", data: dict[str, Any] | None = None,
                             reason: str | None = None, hibernation: bool = True):
    """`android-03` hospedado num worker conectado, como ficaria em produção."""
    from app.main import create_app

    h = Harness(tmp_path, 3, external={"android-03": "127.0.0.1:15555"})
    await h.boot()
    assert h.state is not None
    reg = h.state.workers
    hello = _hello(hibernation=hibernation, verbs=VERBOS)
    reg.autenticar(hello, token=None, enrollment=reg.criar_inscricao())
    agente = AgenteComDados(reg, "worker-lan-01", outcome=outcome, data=data, reason=reason)
    reg.attach("worker-lan-01", agente.send)
    h.state.db.execute("UPDATE instances SET worker_id=? WHERE id=?", ("worker-lan-01", "android-03"))
    rt = h.state.devices.get("android-03")
    rt.worker_id = "worker-lan-01"
    h.state.devices.bind_worker("worker-lan-01", list(VERBOS))
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    cliente = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return h, rt, agente, cliente


async def _esperar_desfecho(h: Harness, cid: str, estado: str = "succeeded") -> None:
    await h.wait(lambda: h.state.commands.get(cid)["state"] == estado,  # type: ignore[union-attr]
                 what=f"comando em {estado}")


async def test_parar_remoto_registra_a_decisao_e_nao_inventa_erro(tmp_path: Path) -> None:
    h, rt, _a, cliente = await _parque_com_worker(tmp_path)
    try:
        fila = h.state.bus.subscribe()          # type: ignore[union-attr]
        async with cliente as c:
            r = await c.post("/api/instances/android-03/actions/stop", json={"idempotency_key": "ef-stop-1"})
            assert r.status_code == 202, r.text
            await _esperar_desfecho(h, r.json()["command_id"])
        # A DECISÃO ficou gravada: é ela que impede o monitor de religar/readotar o que alguém mandou parar.
        assert rt.desired_state == InstanceState.stopped.value
        assert h.state.db.scalar("SELECT desired_state FROM instances WHERE id=?",  # type: ignore[union-attr]
                                 ("android-03",)) == "stopped"
        # E o cartão diz por quê, em vez de "sumiu do ADB" cinco segundos depois do sucesso.
        assert rt.state == InstanceState.stopped
        assert "a pedido" in (rt.state_detail or "")
        erros = []
        while not fila.empty():
            ev = fila.get_nowait()
            if ev.level == "error":
                erros.append(ev.message)
        assert not [m for m in erros if "sumiu do ADB" in m], erros
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_hibernar_remoto_vira_hibernada_no_central_e_libera_acordar(tmp_path: Path) -> None:
    h, rt, _a, cliente = await _parque_com_worker(tmp_path, data={"hibernated": True, "snapshot": "poc_hib"})
    try:
        async with cliente as c:
            r = await c.post("/api/instances/android-03/actions/hibernate", json={"idempotency_key": "ef-hib-1"})
            assert r.status_code == 202, r.text
            await _esperar_desfecho(h, r.json()["command_id"])
            assert rt.state == InstanceState.hibernated and rt.snapshot_valid is True
            # Com o estado certo, "Acordar" deixa de ser recusado — antes o painel só oferecia "Iniciar" (frio).
            r2 = await c.post("/api/instances/android-03/actions/wake", json={"idempotency_key": "ef-wake-1"})
            assert r2.status_code == 202, r2.text
            await _esperar_desfecho(h, r2.json()["command_id"])
            assert rt.state == InstanceState.online       # readoção imediata, sem esperar o monitor
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_acordar_sem_snapshot_e_recusado_em_vez_de_virar_boot_a_frio(tmp_path: Path) -> None:
    h, _rt, agente, cliente = await _parque_com_worker(tmp_path)
    try:
        async with cliente as c:
            r = await c.post("/api/instances/android-03/actions/wake", json={"idempotency_key": "ef-wake-2"})
        assert r.status_code == 409
        assert "snapshot" in r.json()["detail"]["message"]
        # Recusa é antes de agir: nada foi despachado ao worker.
        assert not [p for p in agente.enviados if p.get("type") == "dispatch"]
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_hibernar_em_worker_que_nao_salva_snapshot_e_recusado_com_o_motivo(tmp_path: Path) -> None:
    """A capacidade é da máquina que HOSPEDA o aparelho: antes o pré-voo consultava o config deste servidor."""
    h, _rt, agente, cliente = await _parque_com_worker(tmp_path, hibernation=False)
    try:
        async with cliente as c:
            r = await c.post("/api/instances/android-03/actions/hibernate", json={"idempotency_key": "ef-hib-2"})
        assert r.status_code == 409
        assert "worker" in r.json()["detail"]["message"]
        assert not [p for p in agente.enviados if p.get("type") == "dispatch"]
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_reset_remoto_esquece_sessao_evidencia_e_app_instalado(tmp_path: Path) -> None:
    h, rt, _a, cliente = await _parque_com_worker(tmp_path, data={"reset": True})
    try:
        assert h.state is not None
        h.state.db.execute("UPDATE instances SET account_evidence=?, account_evidence_ts=? WHERE id=?",
                           ("qa-user-03", "2026-09-21T00:00:00Z", "android-03"))
        h.state.release_repo.upsert_app_state("android-03", "com.instagram.android",
                                      state=InstalledAppState.ready, installed_release_id=None,
                                      observed_version_name="385.0")
        invalidadas: list[tuple[str, str]] = []
        anterior = h.state.devices.on_session_invalidated          # type: ignore[attr-defined]
        h.state.devices.on_session_invalidated = lambda iid, motivo: (   # type: ignore[attr-defined]
            invalidadas.append((iid, motivo)), anterior(iid, motivo))[1]
        async with cliente as c:
            r = await c.post("/api/instances/android-03/actions/reset",
                             json={"confirm": True, "idempotency_key": "ef-reset-1"})
            assert r.status_code == 202, r.text
            await _esperar_desfecho(h, r.json()["command_id"])
        assert [i for i, _m in invalidadas] == ["android-03"]
        assert h.state.db.scalar("SELECT account_evidence FROM instances WHERE id=?", ("android-03",)) is None
        linha = h.state.release_repo.app_state("android-03", "com.instagram.android")
        assert linha is not None and linha["state"] == InstalledAppState.missing.value
        assert linha["observed_version_name"] is None
        assert rt.snapshot_valid is False
    finally:
        if h.state is not None:
            await h.state.stop()


@pytest.mark.parametrize("desfecho", ["failed", "uncertain"])
async def test_desfecho_sem_sucesso_nao_aplica_efeito_nenhum(tmp_path: Path, desfecho: str) -> None:
    """`failed`/`uncertain` não autorizam afirmar nada sobre o aparelho: quem descreve o estado é a observação."""
    h, rt, _a, cliente = await _parque_com_worker(tmp_path, outcome=desfecho)
    try:
        estado_antes = rt.state
        async with cliente as c:
            r = await c.post("/api/instances/android-03/actions/stop", json={"idempotency_key": f"ef-{desfecho}"})
            await _esperar_desfecho(h, r.json()["command_id"], desfecho)
        assert rt.state == estado_antes
        # A decisão, essa, vale desde o despacho: ela é do central, e não depende de o agente ter conseguido.
        assert rt.desired_state == InstanceState.stopped.value
    finally:
        if h.state is not None:
            await h.state.stop()
