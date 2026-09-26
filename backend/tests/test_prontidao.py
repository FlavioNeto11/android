"""Prontidão real e sondas que não passam fome — o que o wake remoto de 25/09/2026 revelou.

android-09 via worker-lan-01: snapshot restaurado, adb `device`, `boot_completed=1` — e o framework congelado
(`service check`, `dumpsys`, `screencap` travando, também pelo adb local do worker). O comando `wake` fechou
`succeeded`, o central marcou `online`, e as sondas de saúde e de internet nunca rodaram porque a captura mantinha
a fila do aparelho cheia. Três invariantes:

A. start/wake não fecham com o Android mudo (worker e central);
B. a sonda de saúde/internet não espera a fila da captura;
C. `online` exige o degrau ANDROID_RESPONSIVE; o caminho até lá é visível em `readiness`, sem virar `error` à toa.
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from app.devices import manager as manager_mod
from app.models import InstanceState
from app.worker import executor as executor_mod
from app.worker.executor import VerbUncertain

from .conftest import Harness
from .test_worker_executor import AdbFalso, _estado_falso, _executor, _sem_emulador, _sem_guarda_de_ram


# ---------------------------------------------------------------- A. worker: start/wake exigem o framework
def _worker_pronto_para_subir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adb: AdbFalso) -> Any:
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    ex = _executor(tmp_path)
    _sem_emulador(monkeypatch)
    _sem_guarda_de_ram(ex, monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    monkeypatch.setattr(ex, "adb_for", lambda _spec: adb)
    return ex


async def test_worker_nao_fecha_start_com_framework_mudo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adb = AdbFalso(pronto_depois_de=1)
    adb.framework_mudo = True                       # type: ignore[attr-defined]
    ex = _worker_pronto_para_subir(tmp_path, monkeypatch, adb)
    with pytest.raises(VerbUncertain) as saida:
        await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 0.2})
    assert "framework não respondeu" in str(saida.value), "adb + boot_completed não bastam: o Android estava mudo"


async def test_worker_preparo_que_falha_com_framework_vivo_continua_succeeded(tmp_path: Path,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from app.devices.adb import AdbError

    adb = AdbFalso(pronto_depois_de=1)

    def preparo_quebrado() -> None:
        raise AdbError("dispensar diálogo falhou")
    adb.prepare_for_automation = preparo_quebrado   # type: ignore[method-assign]
    ex = _worker_pronto_para_subir(tmp_path, monkeypatch, adb)
    saida = await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 5})
    assert saida["started"] is True, "falha de preparo com o Android respondendo é aviso, não falha"


async def test_worker_preparo_que_estoura_com_framework_mudo_nao_vira_pronto(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    from app.devices.adb import AdbTimeout

    adb = AdbFalso(pronto_depois_de=1)
    adb.framework_mudo = True                       # type: ignore[attr-defined]

    def preparo_travado() -> None:
        raise AdbTimeout("prepare excedeu 40s")
    adb.prepare_for_automation = preparo_travado    # type: ignore[method-assign]
    ex = _worker_pronto_para_subir(tmp_path, monkeypatch, adb)
    with pytest.raises(VerbUncertain):
        await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 0.2})


async def test_worker_aparelho_saudavel_segue_succeeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adb = AdbFalso(pronto_depois_de=2)
    ex = _worker_pronto_para_subir(tmp_path, monkeypatch, adb)
    saida = await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 5})
    assert saida["started"] is True and adb.preparou


# ---------------------------------------------------------------- A/C. central: adoção externa
def _externo(harness: Harness, monkeypatch: pytest.MonkeyPatch, *, boot: bool = True) -> Any:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    monkeypatch.setattr(rt, "external", True)
    s.devices._set_state(rt, InstanceState.stopped, "emulador desligado no worker")
    monkeypatch.setattr(rt.adb, "connect", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(rt.adb, "state", lambda *a, **k: "device")
    monkeypatch.setattr(rt.adb, "boot_completed", lambda *a, **k: boot)
    monkeypatch.setattr(rt.adb, "prepare_for_automation", lambda *a, **k: None)
    return rt


async def test_adocao_com_framework_mudo_e_booting_nao_online(harness: Harness,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = _externo(harness, monkeypatch)
    harness.fakes["android-01"].guest_mudo = True    # o wake congelado: adb e boot_completed ok, framework mudo
    await s.devices._adopt_external(rt)
    dto = s.devices.dto(rt)
    assert rt.state == InstanceState.booting, "mudo não é 'vivo por falta de prova'"
    assert dto.readiness.phase == "boot_completed" and "framework" in dto.readiness.detail


async def test_adocao_muda_alem_do_prazo_degrada_e_nao_oscila(harness: Harness,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = _externo(harness, monkeypatch)
    harness.fakes["android-01"].guest_mudo = True
    rt.boot_externo_desde = time.monotonic() - s.cfg.instance_android(rt.id).boot_timeout_s - 1
    await s.devices._adopt_external(rt)
    assert rt.state == InstanceState.error and rt.attention and "não respondeu" in rt.attention
    # A readoção seguinte (o monitor readota `error` externo) não pode recomeçar o prazo e voltar a `booting`.
    await s.devices._adopt_external(rt)
    assert rt.state == InstanceState.error and rt.state != InstanceState.booting


async def test_adocao_saudavel_entra_no_ar_com_readiness_ready(harness: Harness,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = _externo(harness, monkeypatch)
    await s.devices._adopt_external(rt)
    dto = s.devices.dto(rt)
    assert rt.state == InstanceState.online and dto.readiness.phase == "ready"
    assert rt.boot_externo_desde == 0.0


# ---------------------------------------------------------------- A/C. central: boot local
async def _boot_local(harness: Harness, monkeypatch: pytest.MonkeyPatch, *, warm: bool) -> tuple[Any, bool]:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    s.devices._set_state(rt, InstanceState.booting, "emulador iniciado")
    rt.pid = 4242
    monkeypatch.setattr(manager_mod.emu, "is_our_emulator", lambda *_a, **_k: True)
    monkeypatch.setattr(manager_mod, "RESPOSTA_POS_BOOT_S", 0.05)
    monkeypatch.setattr(rt.adb, "boot_completed", lambda *a, **k: True)
    monkeypatch.setattr(rt.adb, "ui_ready", lambda *a, **k: True)
    monkeypatch.setattr(rt.adb, "prepare_for_automation", lambda *a, **k: None)
    monkeypatch.setattr(rt.adb, "sync_clock", lambda *a, **k: (0, 0), raising=False)
    monkeypatch.setattr(s.devices, "_snapshot_verdict", lambda _rt: True)
    s.cfg.file.limits.boot_poll_s = 0.01
    harness.fakes["android-01"].guest_mudo = True
    ok = await s.devices._wait_boot(rt, time.monotonic(), warm=warm)
    return rt, ok


async def test_boot_local_a_frio_com_framework_mudo_nao_vira_online(harness: Harness,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    rt, ok = await _boot_local(harness, monkeypatch, warm=False)
    assert ok is False and rt.state == InstanceState.error
    assert rt.readiness_phase == "boot_completed" and "framework" in rt.readiness_detail


async def test_wake_local_com_framework_mudo_devolve_para_o_boot_a_frio(harness: Harness,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Mesmo desfecho do `wake_timeout_s`: quem chamou descarta o snapshot e tenta a frio — não `online`."""
    rt, ok = await _boot_local(harness, monkeypatch, warm=True)
    assert ok is False and rt.state != InstanceState.online


# ---------------------------------------------------------------- B. sondas com trilha própria
def _ocupar_executor(rt: Any) -> threading.Event:
    """Uma captura travada: ocupa a thread do aparelho até alguém soltar."""
    solta = threading.Event()
    loop = asyncio.get_running_loop()
    loop.create_task(rt.executor.run(lambda: solta.wait(10), timeout=15, label="screencap travado"))
    return solta


async def test_sonda_de_saude_roda_com_a_fila_da_captura_cheia(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    s.devices._set_state(rt, InstanceState.online, "teste")
    solta = _ocupar_executor(rt)
    await asyncio.sleep(0.05)
    try:
        assert rt.executor.queue_depth > 0
        assert s.devices._deve_sondar_saude(rt, time.monotonic() + 3600), "fila cheia não pode calar a saúde"
        assert await asyncio.wait_for(s.devices.conferir_saude(rt), 3) is None
    finally:
        solta.set()


async def test_sonda_de_internet_roda_com_a_fila_da_captura_cheia(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    s.devices._set_state(rt, InstanceState.online, "teste")
    solta = _ocupar_executor(rt)
    await asyncio.sleep(0.05)
    try:
        assert s.devices._deve_sondar_conectividade(rt, time.monotonic() + 3600)
        info = await asyncio.wait_for(s.devices.conferir_conectividade(rt), 3)
        assert info.state == "healthy"
    finally:
        solta.set()


async def test_aparelho_online_que_congela_e_degradado_pelas_sondas(harness: Harness) -> None:
    """A detecção do wake congelado: três sondas mudas seguidas (agora que elas rodam) degradam com o motivo."""
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    s.devices._set_state(rt, InstanceState.online, "teste")
    harness.fakes["android-01"].guest_mudo = True
    motivos = [await s.devices.conferir_saude(rt) for _ in range(manager_mod.SONDAS_MUDAS_PARA_DEGRADAR)]
    assert motivos[:-1] == [None] * (len(motivos) - 1)
    assert motivos[-1] and "não responde ao ADB" in motivos[-1]


async def test_captura_travada_com_android_saudavel_e_capture_error_nao_offline(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    s.devices._set_state(rt, InstanceState.online, "teste")
    for _ in range(3):
        s.devices._falha_de_captura(rt, "DriverTimeout: screencap excedeu 25s")
    dto = s.devices.dto(rt)
    assert dto.state == InstanceState.online and dto.stream is not None
    assert dto.stream.status in ("capture_error", "no_frame") and dto.stream.status != "device_offline"
    assert await s.devices.conferir_saude(rt) is None and dto.readiness.phase == "ready"
