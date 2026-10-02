"""Pausa do reparo automático POR APARELHO (quase-acidente de 02/10/2026, W8).

Durante o estágio 1 do W8 a escada de reparo do central pediu um `restart` do android-09 15 s depois do restart do
experimento; só não entrou porque o comando do experimento ainda estava aberto (`device_busy`). A pausa é o mecanismo
mínimo que faltava: UM aparelho, com prazo obrigatório, desligada por padrão, que segura SÓ o reparo automático
(escada e reinício por saúde) — comando de pessoa, o `restart` da rede (o produto) e os outros aparelhos seguem.

Prova: `simulated` (harness com aparelhos falsos; o comando de ciclo de vida é trocado por um falso)."""
from __future__ import annotations

import dataclasses
from datetime import timedelta
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from app.commands import despacho
from app.commands.despacho import REPARO_AUTOMATICO, pedir_ciclo_de_vida, remediar
from app.main import create_app
from app.models import InstanceState
from app.util import now

from .conftest import Harness


@pytest_asyncio.fixture
async def h(tmp_path: Path) -> AsyncIterator[Harness]:
    harness = Harness(tmp_path, 2)
    await harness.boot()
    try:
        yield harness
    finally:
        if harness.state is not None:
            await harness.state.stop()


@pytest.fixture(autouse=True)
def _sem_comando_de_verdade(monkeypatch: pytest.MonkeyPatch) -> None:
    async def falso(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(despacho, "_do_action", falso)


async def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _comandos(h: Harness, iid: str) -> list[dict[str, object]]:
    return h.state.db.query("SELECT id, verb, state, requested_by FROM commands WHERE instance_id=?", (iid,))


def _pausar(h: Harness, iid: str = "android-01", ttl: int = 300) -> None:
    rt = h.state.devices.get(iid)
    h.state.devices.set_desired_state(rt, InstanceState.online.value)
    h.state.devices.pausar_reparo(rt, ttl, "experimento W8", "teste")


async def test_desligada_por_padrao_a_escada_age(h: Harness) -> None:
    assert h.state.devices.pausa_de_reparo(h.state.devices.get("android-01")) is None
    cid = remediar(h.state, "android-01", "o system_server caiu")
    assert cid is not None and h.state.commands.get(cid)["verb"] == "restart"


async def test_pausado_a_escada_nao_emite_nem_conta_degrau_e_volta_depois_do_prazo(h: Harness) -> None:
    s = h.state
    _pausar(h)
    rt = s.devices.get("android-01")
    assert remediar(s, "android-01", "o system_server caiu") is None
    assert _comandos(h, "android-01") == []                          # nenhum comando, nem rejeitado: não vira degrau
    assert rt.restart_backoff_until > 0                              # reavaliado só depois do fim da pausa
    # o prazo vence (sem esperar o monitor limpar): a escada recomeça no 1º degrau
    rt.repair_pause = dataclasses.replace(rt.repair_pause, until=now() - timedelta(seconds=1))
    assert s.devices.pausa_de_reparo(rt) is None
    cid = remediar(s, "android-01", "o system_server caiu")
    assert cid is not None and s.commands.get(cid)["verb"] == "restart"
    assert [c["verb"] for c in _comandos(h, "android-01")] == ["restart"]


async def test_a_pausa_e_so_do_aparelho_marcado(h: Harness) -> None:
    s = h.state
    _pausar(h, "android-01")
    s.devices.set_desired_state(s.devices.get("android-02"), InstanceState.online.value)
    assert remediar(s, "android-01", "x") is None
    cid = remediar(s, "android-02", "x")
    assert cid is not None and s.commands.get(cid)["verb"] == "restart"
    assert s.devices.pausa_de_reparo(s.devices.get("android-02")) is None


async def test_so_o_reparo_automatico_e_segurado(h: Harness) -> None:
    """Escada (`system`) e reinício por saúde (`saude`) param; pessoa, o produto (`rede`) e o rodízio passam."""
    s = h.state
    _pausar(h)
    assert REPARO_AUTOMATICO == {"system", "saude"}
    for quem in ("system", "saude"):
        assert pedir_ciclo_de_vida(s, "android-01", "restart", "x", requested_by=quem) is None
        assert pedir_ciclo_de_vida(s, "android-01", "reset", "x", requested_by=quem) is None
    assert _comandos(h, "android-01") == []
    for quem in ("panel", "rede", "scheduler", "reconciliacao"):
        cid = pedir_ciclo_de_vida(s, "android-01", "restart", f"pedido de {quem}", requested_by=quem)
        assert cid is not None, quem
        s.db.execute("UPDATE commands SET state='failed', finished_at=? WHERE id=?", ("2026-10-02T12:00:00.000Z", cid))
    assert len(_comandos(h, "android-01")) == 4


async def test_pausa_nao_segura_o_que_nao_e_restart_nem_reset(h: Harness) -> None:
    """A escada com conta vinculada termina em `stop` (parar não apaga nada): a pausa não o impede."""
    _pausar(h)
    assert pedir_ciclo_de_vida(h.state, "android-01", "stop", "x", requested_by="system") is not None


async def test_o_monitor_limpa_a_pausa_vencida_e_avisa_uma_vez(h: Harness) -> None:
    s = h.state
    _pausar(h)
    rt = s.devices.get("android-01")
    rt.repair_pause = dataclasses.replace(rt.repair_pause, until=now() - timedelta(seconds=1))
    antes = len(s.db.query("SELECT id FROM events WHERE kind='instance.repair_pause'"))
    s.devices._expirar_pausa_de_reparo(rt)
    s.devices._expirar_pausa_de_reparo(rt)                           # idempotente: o 2º não avisa de novo
    assert rt.repair_pause is None
    eventos = s.db.query("SELECT message FROM events WHERE kind='instance.repair_pause' ORDER BY id")
    assert len(eventos) == antes + 1 and "prazo da pausa venceu" in eventos[-1]["message"]


async def test_api_exige_prazo_valida_limites_e_aparece_no_status_e_na_saude(h: Harness) -> None:
    async with await _cliente(h) as c:
        for corpo in ({"reason": "experimento"}, {"ttl_s": 30, "reason": "experimento"}, {"ttl_s": 20_000, "reason": "experimento"},
                      {"ttl_s": 600}, {"ttl_s": 600, "reason": "x"}):
            r = await c.put("/api/instances/android-01/repair-pause", json=corpo)
            assert r.status_code == 422, (corpo, r.text)
        assert (await c.put("/api/instances/android-99/repair-pause", json={"ttl_s": 600, "reason": "experimento"})).status_code == 404
        assert (await c.get("/api/health")).json()["features"]["repair_pause"] == {}          # desligada por padrão

        r = await c.put("/api/instances/android-01/repair-pause", json={"ttl_s": 600, "reason": "experimento W8"})
        assert r.status_code == 200
        corpo = r.json()
        assert corpo["reason"] == "experimento W8" and 590 <= corpo["remaining_s"] <= 600 and corpo["until"].endswith("Z")

        inst = {i["id"]: i for i in (await c.get("/api/snapshot")).json()["instances"]}
        assert inst["android-01"]["repair_pause"]["reason"] == "experimento W8"
        assert inst["android-02"]["repair_pause"] is None                                      # só o marcado
        saude = (await c.get("/api/health")).json()
        assert list(saude["features"]["repair_pause"]) == ["android-01"]
        assert saude["status"] in ("ok", "degraded")
        assert not any(p["code"].startswith("repair") for p in saude["problems"])               # informativo, não é problema

        assert (await c.delete("/api/instances/android-01/repair-pause")).json() == {"status": "resumed"}
        assert (await c.delete("/api/instances/android-01/repair-pause")).status_code == 404
        assert (await c.get("/api/health")).json()["features"]["repair_pause"] == {}


async def test_repetir_o_put_renova_o_prazo(h: Harness) -> None:
    async with await _cliente(h) as c:
        await c.put("/api/instances/android-01/repair-pause", json={"ttl_s": 120, "reason": "primeira"})
        r = await c.put("/api/instances/android-01/repair-pause", json={"ttl_s": 3600, "reason": "segunda"})
        assert r.json()["reason"] == "segunda" and r.json()["remaining_s"] > 3500
