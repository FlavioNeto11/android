"""Prontidão por subsistema: servicemanager → system_server → display (`devices/prontidao.py`).

Análise forense do wake do android-09 (25/09/2026): adb `device`, `boot_completed=1`, o `prepare_for_automation`
do worker ficou 40 s mudo (`adb shell excedeu 40s`) e o wake fechou `succeeded` 0,3 s depois. O `service check`
do PR #5 teria passado também — ele só pergunta ao `servicemanager`. A captura de tela nunca respondeu.
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from app.devices import prontidao
from app.devices.adb import AdbError, AdbTimeout
from app.models import InstanceState
from app.worker import executor as executor_mod
from app.worker.executor import VerbUncertain

from .conftest import Harness
from .test_worker_executor import AdbFalso, _estado_falso, _executor, _sem_emulador, _sem_guarda_de_ram


class Alvo:
    """Alvo mínimo da escada: cada subsistema responde, trava (sleep + timeout) ou nega."""

    def __init__(self, **modo: str) -> None:
        self.modo = {"service_manager": "ok", "system_server": "ok", "display": "ok", **modo}
        self.prazos: dict[str, float] = {}

    def _sonda(self, sub: str, timeout: float) -> bool:
        self.prazos[sub] = timeout
        m = self.modo[sub]
        if m == "trava":
            time.sleep(min(timeout, 0.05))
            raise AdbTimeout(f"{sub} excedeu {timeout}s")
        if m == "lento":                              # dorme o prazo inteiro: prova do orçamento
            time.sleep(timeout)
            raise AdbTimeout(f"{sub} excedeu {timeout}s")
        return m == "ok"

    def framework_alive(self, *, timeout: float = 25) -> bool:
        return self._sonda("service_manager", timeout)

    def system_server_alive(self, *, timeout: float = 8) -> bool:
        return self._sonda("system_server", timeout)

    def display_alive(self, *, timeout: float = 12) -> bool:
        return self._sonda("display", timeout)


# ---------------------------------------------------------------- 1, 2, 5: a escada
def test_1_servicemanager_ok_e_system_server_travado_nao_e_pronto() -> None:
    p = prontidao.avaliar(Alvo(system_server="trava"))
    assert not p.pronto and p.estado == "mudo" and p.falta == "system_server"
    assert p.respondeu == ("service_manager",) and "aguardando system_server" in p.detalhe()


def test_2_system_server_ok_e_display_travado_nao_e_pronto() -> None:
    p = prontidao.avaliar(Alvo(display="trava"))
    assert not p.pronto and p.falta == "display" and p.respondeu == ("service_manager", "system_server")
    assert "system_server respondeu; aguardando display" in p.detalhe()


def test_5_tres_subsistemas_respondendo_e_pronto() -> None:
    assert prontidao.avaliar(Alvo()).pronto


def test_servicemanager_negando_e_morto_e_os_outros_negando_sao_mudos() -> None:
    assert prontidao.avaliar(Alvo(service_manager="nega")).estado == "morto"
    assert prontidao.avaliar(Alvo(display="nega")).estado == "mudo"


# ---------------------------------------------------------------- 8: orçamento global
def test_8_orcamento_corta_cada_prazo_e_a_rodada_nao_estoura() -> None:
    alvo = Alvo(service_manager="lento")
    t0 = time.monotonic()
    p = prontidao.avaliar(alvo, restante_s=1.5)
    gasto = time.monotonic() - t0
    assert not p.pronto and alvo.prazos["service_manager"] <= 1.5
    assert gasto < 2.5, "a rodada respeita o que resta do prazo de boot/wake"
    alvo2 = Alvo()
    prontidao.avaliar(alvo2, restante_s=4.0)
    assert all(v <= 4.0 for v in alvo2.prazos.values())
    assert prontidao.prazo_da_rodada() <= 30, "três sondas em série nunca somam minutos"


# ---------------------------------------------------------------- 3, 4, 5, 6, 7, 8: worker
def _worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adb: AdbFalso, *, hibernacao: bool = False) -> Any:
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    ex = _executor(tmp_path)
    _sem_emulador(monkeypatch)
    _sem_guarda_de_ram(ex, monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    monkeypatch.setattr(ex, "adb_for", lambda _spec: adb)
    if hibernacao:
        com_hib = ex._android().model_copy(update={"hibernation": True})
        monkeypatch.setattr(ex, "_android", lambda: com_hib)
        monkeypatch.setattr(ex, "snapshot_existe", lambda _spec: True)
    return ex


def _preparo(adb: AdbFalso, erro: Exception) -> None:
    def falha() -> None:
        raise erro
    adb.prepare_for_automation = falha               # type: ignore[method-assign]


async def test_3_preparo_estourado_com_service_check_ok_nao_fecha_succeeded(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """A lacuna exata de 25/09: preparo 40 s mudo, `service check` ok — e antes disso virava `succeeded`."""
    adb = AdbFalso(pronto_depois_de=1)
    _preparo(adb, AdbTimeout("adb shell excedeu 40s"))
    adb.system_server_mudo = True                    # type: ignore[attr-defined]
    ex = _worker(tmp_path, monkeypatch, adb)
    with pytest.raises(VerbUncertain) as saida:
        await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 0.3})
    assert "aguardando system_server" in str(saida.value)


async def test_3b_preparo_estourado_mas_escada_toda_respondendo_fica_pronto(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """O timeout do preparo não basta para condenar: se os três subsistemas respondem depois, o Android voltou."""
    adb = AdbFalso(pronto_depois_de=1)
    _preparo(adb, AdbTimeout("adb shell excedeu 40s"))
    ex = _worker(tmp_path, monkeypatch, adb)
    assert (await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 5}))["started"] is True


async def test_4_preparo_com_erro_rapido_e_escada_ok_segue_succeeded(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    adb = AdbFalso(pronto_depois_de=1)
    _preparo(adb, AdbError("dispensar diálogo falhou"))
    ex = _worker(tmp_path, monkeypatch, adb)
    assert (await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 5}))["started"] is True


async def test_5_worker_com_os_tres_subsistemas_fecha_succeeded(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    adb = AdbFalso(pronto_depois_de=2)
    ex = _worker(tmp_path, monkeypatch, adb)
    assert (await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 5}))["started"] is True
    assert {s for s, _ in adb.prazos} == {"system_server", "display"}


async def test_6_boot_normal_servicemanager_antes_dos_outros_espera_e_nao_falha(tmp_path: Path,
                                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """Boot normal: serviços registrados antes de o `system_server` e o display responderem. É espera, não erro."""
    adb = AdbFalso(pronto_depois_de=1)
    adb.system_server_mudo = True                    # type: ignore[attr-defined]
    ex = _worker(tmp_path, monkeypatch, adb)

    async def liberar() -> None:
        await asyncio.sleep(0.15)
        adb.system_server_mudo = False               # type: ignore[attr-defined]
    liberador = asyncio.create_task(liberar())
    saida = await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 5})
    await liberador
    assert saida["started"] is True


async def test_7_wake_com_display_mudo_e_uncertain_nunca_succeeded(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    adb = AdbFalso(pronto_depois_de=1)
    adb.display_mudo = True                          # type: ignore[attr-defined]
    ex = _worker(tmp_path, monkeypatch, adb, hibernacao=True)
    with pytest.raises(VerbUncertain) as saida:
        await ex.run("wake", ex.settings.devices[0], {"boot_timeout_s": 0.3})
    assert "aguardando display" in str(saida.value)


async def test_8_worker_respeita_o_prazo_do_verbo_com_sondas_lentas(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    adb = AdbFalso(pronto_depois_de=1)
    adb.framework_alive = lambda *, timeout=25: (time.sleep(timeout), False)[1]   # type: ignore[method-assign]
    ex = _worker(tmp_path, monkeypatch, adb)
    t0 = time.monotonic()
    with pytest.raises(VerbUncertain):
        await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 1.0})
    assert time.monotonic() - t0 < 4.0, "sonda lenta não estica o prazo do verbo"


# ---------------------------------------------------------------- 1, 2, 6, 9, 10: central
def _externo(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> Any:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    monkeypatch.setattr(rt, "external", True)
    s.devices._set_state(rt, InstanceState.stopped, "emulador desligado no worker")
    monkeypatch.setattr(rt.adb, "connect", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(rt.adb, "state", lambda *a, **k: "device")
    monkeypatch.setattr(rt.adb, "boot_completed", lambda *a, **k: True)
    monkeypatch.setattr(rt.adb, "prepare_for_automation", lambda *a, **k: None)
    return rt


@pytest.mark.parametrize(("sub", "falta"), [("system_server_mudo", "system_server"), ("display_mudo", "display")])
async def test_central_nao_libera_com_subsistema_mudo(harness: Harness, monkeypatch: pytest.MonkeyPatch,
                                                     sub: str, falta: str) -> None:
    s = harness.state
    assert s is not None
    rt = _externo(harness, monkeypatch)
    setattr(harness.fakes["android-01"], sub, True)
    await s.devices._adopt_external(rt)
    dto = s.devices.dto(rt)
    assert rt.state == InstanceState.booting and dto.readiness.phase == "boot_completed"
    assert f"aguardando {prontidao.ROTULO[falta]}" in dto.readiness.detail


async def test_9_central_e_worker_decidem_igual(harness: Harness, monkeypatch: pytest.MonkeyPatch,
                                                tmp_path: Path) -> None:
    """Mesmas respostas dos subsistemas → mesma decisão: `succeeded` no worker ⇔ `online` no central."""
    s = harness.state
    assert s is not None
    for mudo in (None, "framework_mudo", "system_server_mudo", "display_mudo"):
        adb = AdbFalso(pronto_depois_de=1)
        if mudo:
            setattr(adb, mudo, True)
        ex = _worker(tmp_path / (mudo or "ok"), monkeypatch, adb)
        try:
            await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 0.3})
            worker_pronto = True
        except VerbUncertain:
            worker_pronto = False
        rt = _externo(harness, monkeypatch)
        fake = harness.fakes["android-01"]
        fake.guest_mudo = mudo == "framework_mudo"
        fake.system_server_mudo = mudo == "system_server_mudo"   # type: ignore[attr-defined]
        fake.display_mudo = mudo == "display_mudo"               # type: ignore[attr-defined]
        rt.boot_externo_desde = 0.0
        await s.devices._adopt_external(rt)
        assert worker_pronto == (rt.state == InstanceState.online), mudo


async def test_10_sonda_de_display_nao_passa_pela_captura(harness: Harness) -> None:
    """A prontidão roda na trilha de sonda: com a fila da captura travada ela conclui, e não mexe no stream."""
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    s.devices._set_state(rt, InstanceState.online, "teste")
    antes = (rt.capture_failures, rt.frame)
    solta = threading.Event()
    asyncio.get_running_loop().create_task(rt.executor.run(lambda: solta.wait(10), timeout=15, label="screencap"))
    await asyncio.sleep(0.05)
    try:
        assert rt.executor.queue_depth > 0
        p = await asyncio.wait_for(s.devices._sondar_prontidao(rt), 5)
        assert p.pronto and (rt.capture_failures, rt.frame) == antes
    finally:
        solta.set()
