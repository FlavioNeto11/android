"""29.123: a readoção não lê o histórico do log do emulador (o falso positivo que parou o 03 e o 06 em 05/10)."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.devices import emulator as emu
from app.models import InstanceState

from .conftest import Harness
from .test_prontidao_subsistemas import _readocao_local


def _log(harness: Harness, rt: Any) -> Any:
    s = harness.state
    assert s is not None
    caminho = s.cfg.logs_dir / f"emulator-{rt.avd_name}.log"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    return caminho


async def test_crashdialog_de_boot_antigo_nao_para_o_aparelho_readotado(harness: Harness,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """O incidente: a readoção cai no `_wait_boot` (a sonda não fecha com o host saturado), e o log tem um
    "Showing crashdialog" de uma subida de dias antes. Antes do 29.123, o detector lia do byte 0 e parava o aparelho."""
    s = harness.state
    assert s is not None
    rt = _readocao_local(harness, monkeypatch)
    monkeypatch.setattr(rt.adb, "boot_completed", lambda *a, **k: False)        # cai no `_wait_boot`
    s.cfg.file.limits.boot_poll_s = 0.01
    log = _log(harness, rt)
    log.write_text("INFO | subida antiga\n" + emu.LINHA_DO_DIALOGO_DE_CRASH + "\nINFO | Boot completed\n" * 3,
                   encoding="utf-8")
    paradas: list[str] = []

    async def parou(rt_: Any) -> None:
        paradas.append(rt_.id)
    monkeypatch.setattr(s.devices, "_parou_no_dialogo_de_crash", parou)
    try:
        await s.devices._adopt(rt)                                                # noqa: SLF001
        assert rt.boot_log_offset == log.stat().st_size
        await asyncio.sleep(0.3)
        assert paradas == [], "o diálogo de um boot antigo parou o aparelho readotado"
        assert rt.state == InstanceState.booting
        # O detector continua valendo para o que o emulador escrever DEPOIS da readoção.
        with log.open("a", encoding="utf-8") as fh:
            fh.write(emu.LINHA_DO_DIALOGO_DE_CRASH + "\n")
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
    log = _log(harness, rt)
    log.unlink(missing_ok=True)
    rt.boot_log_offset = 12345
    await s.devices._adopt(rt)                                                    # noqa: SLF001
    assert rt.boot_log_offset == 0
