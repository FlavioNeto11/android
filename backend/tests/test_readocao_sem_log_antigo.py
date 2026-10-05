"""29.123: a readoção não lê o histórico do log do emulador (o falso positivo que parou o 03 e o 06 em 05/10)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.devices import emulator as emu
from app.devices import manager as manager_mod
from app.models import InstanceState

from .conftest import Harness
from .test_prontidao_subsistemas import _readocao_local

#: Uma subida no log, como o emulador escreve (as linhas `emuglConfig_init` no começo; o diálogo, se houver, depois).
_SUBIDA = "emuglConfig_init: gpu_mode_requested: host, no_window: 1\nINFO | emuglConfig_init: vulkan_mode_selected:host\n"
_DIALOGO = "INFO | " + emu.LINHA_DO_DIALOGO_DE_CRASH + " to get consent.\n"
_NO_AR = "INFO | Boot completed in 60447 ms\n"


def _log(harness: Harness, rt: Any) -> Path:
    s = harness.state
    assert s is not None
    caminho = s.cfg.logs_dir / f"emulator-{rt.avd_name}.log"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    return caminho


def _contar_paradas(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    s = harness.state
    assert s is not None
    paradas: list[str] = []

    async def parou(rt_: Any) -> None:
        paradas.append(rt_.id)
    monkeypatch.setattr(s.devices, "_parou_no_dialogo_de_crash", parou)
    return paradas


def _cair_no_wait_boot(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> Any:
    s = harness.state
    assert s is not None
    rt = _readocao_local(harness, monkeypatch)
    monkeypatch.setattr(rt.adb, "boot_completed", lambda *a, **k: False)        # a sonda não fecha: `_wait_boot`
    s.cfg.file.limits.boot_poll_s = 0.01
    return rt


async def test_crashdialog_de_boot_antigo_nao_para_o_aparelho_readotado(harness: Harness,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """O incidente: a readoção cai no `_wait_boot` (a sonda não fecha com o host saturado), e o log tem diálogos de
    subidas antigas, ANTES da subida em curso. Antes do 29.123, o detector lia do byte 0 e parava o aparelho."""
    s = harness.state
    assert s is not None
    rt = _cair_no_wait_boot(harness, monkeypatch)
    log = _log(harness, rt)
    antigas = (_SUBIDA + _DIALOGO) * 3
    log.write_bytes((antigas + _SUBIDA + _NO_AR).encode())
    paradas = _contar_paradas(harness, monkeypatch)
    try:
        await s.devices._adopt(rt)                                                # noqa: SLF001
        assert rt.boot_log_offset == len(antigas.encode()) + len(_SUBIDA.splitlines(keepends=True)[0].encode())
        await asyncio.sleep(0.3)
        assert paradas == [], "o diálogo de um boot antigo parou o aparelho readotado"
        assert rt.state == InstanceState.booting
        with log.open("ab") as fh:                                                # e o que vier depois ainda conta
            fh.write(_DIALOGO.encode())
        await asyncio.sleep(0.3)
        assert paradas == ["android-01"]
    finally:
        boot = rt.tasks.pop("boot", None)
        if boot:
            boot.cancel()


async def test_dialogo_da_subida_atual_escrito_antes_da_readocao_ainda_para(harness: Harness,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """M1 da leitura: o backend reiniciou com o emulador JÁ parado no diálogo desta subida. Com o offset no fim do
    arquivo ele seria perdido (espera até o `boot_timeout`, "Boot excedeu"); no começo da subida, ele é visto."""
    s = harness.state
    assert s is not None
    rt = _cair_no_wait_boot(harness, monkeypatch)
    _log(harness, rt).write_bytes((_SUBIDA + _NO_AR + _SUBIDA + _DIALOGO).encode())
    paradas = _contar_paradas(harness, monkeypatch)
    try:
        await s.devices._adopt(rt)                                                # noqa: SLF001
        await asyncio.sleep(0.3)
        assert paradas == ["android-01"]
    finally:
        boot = rt.tasks.pop("boot", None)
        if boot:
            boot.cancel()


async def test_readocao_sem_log_comeca_do_zero(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = _readocao_local(harness, monkeypatch)
    _log(harness, rt).unlink(missing_ok=True)
    rt.boot_log_offset = 12345
    try:
        await s.devices._adopt(rt)                                                # noqa: SLF001
        assert rt.boot_log_offset == 0
    finally:
        boot = rt.tasks.pop("boot", None)
        if boot:
            boot.cancel()


def test_log_sem_emugl_vai_ao_fim_e_o_que_some_no_meio_vira_zero(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem a linha da subida, só conta o que vier depois (o fim); o arquivo que some entre a busca e o `stat` (N2 da
    leitura) é 0, não uma exceção dentro do `gather` da partida."""
    log = tmp_path / "emulator-x.log"
    log.write_bytes(("INFO | sem a linha da subida\n" + _DIALOGO).encode())
    assert manager_mod.DeviceManager._offset_da_readocao(log) == log.stat().st_size   # noqa: SLF001
    monkeypatch.setattr(manager_mod.emu, "inicio_da_subida_atual", lambda _p: None)
    log.unlink()
    assert manager_mod.DeviceManager._offset_da_readocao(log) == 0                    # noqa: SLF001
