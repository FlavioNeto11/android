"""RA-15 (plano-100 29.34): o relógio do wake (`wake_timeout_s`) começa no snapshot CARREGADO, não no spawn.

Medido em 03/10/2026: 4 de 23 wakes passavam de 90 s só carregando 2 GB sob CPU alta, e o prazo contado do spawn os
derrubava para o boot a frio (de minutos). Aqui o relógio de `manager` é injetável (nenhum sono real longo) e as
sondas/o veredito do log são dublês; o laço de `_wait_boot`, a prontidão e a medição são os de produção. `simulated`.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.devices import manager as manager_mod
from app.models import InstanceState

from .conftest import Harness
from .test_wait_boot_sondas import _RelogioInjetavel

WAKE_S = 90
BOOT_S = 400


def _preparar(harness: Harness, monkeypatch: pytest.MonkeyPatch, relogio: _RelogioInjetavel, *,
              carregado_em: float | None, boot_ok_em: float, ui: Any) -> tuple[Any, dict[str, int]]:
    """`carregado_em`: instante (do relógio injetável) em que o log passa a dizer "Successfully loaded" (None = nunca).
    `boot_ok_em`: instante a partir do qual `boot_completed` é verdadeiro. Cada sonda lenta custa 30 s de relógio."""
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    chamadas = {"ui_ready": 0}
    monkeypatch.setattr(manager_mod, "time", relogio)
    harness.cfg.file.instances.overrides["android-01"] = {"wake_timeout_s": WAKE_S, "boot_timeout_s": BOOT_S}

    def boot_completed(*_a: Any, **_k: Any) -> bool:
        relogio.agora += 30.0
        return relogio.agora >= boot_ok_em

    def ui_ready(*_a: Any, **_k: Any) -> bool:
        chamadas["ui_ready"] += 1
        relogio.agora += 30.0
        return ui(relogio.agora)

    s.devices._set_state(rt, InstanceState.booting, "acordando do snapshot…")
    rt.pid = 4242
    monkeypatch.setattr(manager_mod.emu, "is_our_emulator", lambda *_a, **_k: True)
    monkeypatch.setattr(manager_mod, "RESPOSTA_POS_BOOT_S", 0.05)
    monkeypatch.setattr(manager_mod, "RESPOSTA_MIN_S", 0.05)
    monkeypatch.setattr(rt.adb, "boot_completed", boot_completed)
    monkeypatch.setattr(rt.adb, "ui_ready", ui_ready)
    monkeypatch.setattr(rt.adb, "prepare_for_automation", lambda *a, **k: None)
    monkeypatch.setattr(s.devices, "_snapshot_verdict",
                        lambda _rt: True if carregado_em is not None and relogio.agora >= carregado_em else None)
    monkeypatch.setattr(s.devices, "_start_online_tasks", lambda _rt: None)
    s.cfg.file.limits.boot_poll_s = 0.01
    return rt, chamadas


async def test_snapshot_que_carrega_depois_do_prazo_do_spawn_ainda_acorda(harness: Harness,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """O log só diz "carregado" 120 s depois do spawn (> 90 s), mas a interface fica pronta 30 s depois da carga:
    acorda (warm, snapshot mantido). Com o relógio no spawn, o laço devolvia `False` na volta em que o veredito chegava."""
    relogio = _RelogioInjetavel()
    rt, chamadas = _preparar(harness, monkeypatch, relogio, carregado_em=relogio.agora + 120,
                             boot_ok_em=relogio.agora + 120, ui=lambda _agora: True)
    s = harness.state
    assert s is not None
    ok = await s.devices._wait_boot(rt, relogio.monotonic(), warm=True)     # noqa: SLF001

    assert ok is True and rt.state == InstanceState.online
    assert chamadas["ui_ready"] == 1
    assert rt.boot_seconds is not None and rt.boot_seconds >= 120, "boot_seconds segue sendo spawn→online"


async def test_carregado_e_sem_interface_no_prazo_do_wake_devolve_falso(harness: Harness,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshot carregado de imediato, `boot_completed` ok e a interface nunca fica pronta: passados `wake_timeout_s`
    DESDE A CARGA, `_wait_boot` devolve `False` (quem chamou descarta o snapshot e tenta a frio), como antes."""
    relogio = _RelogioInjetavel()
    rt, chamadas = _preparar(harness, monkeypatch, relogio, carregado_em=relogio.agora, boot_ok_em=relogio.agora,
                             ui=lambda _agora: False)
    s = harness.state
    assert s is not None
    ok = await s.devices._wait_boot(rt, relogio.monotonic(), warm=True)     # noqa: SLF001

    assert ok is False and rt.state != InstanceState.online
    assert chamadas["ui_ready"] == 3, "sondas aos 0, 30 e 60 s da carga; no laço seguinte (120 s > 90 s) desiste"


async def test_sem_veredito_do_log_o_prazo_e_o_de_boot_a_frio(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """O log nunca diz nada: antes do veredito vale `boot_timeout_s` desde o spawn (não os 90 s do wake). A interface
    pronta aos 150 s desde o spawn acorda, e a medição fica sem `load_ms` (o veredito não veio)."""
    relogio = _RelogioInjetavel()
    rt, _ = _preparar(harness, monkeypatch, relogio, carregado_em=None, boot_ok_em=relogio.agora + 120,
                      ui=lambda _agora: True)
    s = harness.state
    assert s is not None
    assert await s.devices._wait_boot(rt, relogio.monotonic(), warm=True) is True      # noqa: SLF001
    dados = json.loads(s.db.query("SELECT data FROM measurements WHERE kind='boot'")[-1]["data"])
    assert dados["kind"] == "warm" and "load_ms" in dados and dados["load_ms"] is None


async def test_medicao_do_boot_warm_tem_load_ms(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """`load_ms` = spawn → hora em que o veredito foi lido (inteiro, ms); os campos antigos não mudam."""
    relogio = _RelogioInjetavel()
    rt, _ = _preparar(harness, monkeypatch, relogio, carregado_em=relogio.agora + 120,
                      boot_ok_em=relogio.agora + 120, ui=lambda _agora: True)
    s = harness.state
    assert s is not None
    assert await s.devices._wait_boot(rt, relogio.monotonic(), warm=True) is True      # noqa: SLF001
    dados = json.loads(s.db.query("SELECT data FROM measurements WHERE kind='boot'")[-1]["data"])

    assert dados["kind"] == "warm" and isinstance(dados["load_ms"], int) and dados["load_ms"] == 120_000
    assert {"instance_id", "boot_seconds", "online_after", "mem_available_gb", "image"} <= set(dados)


async def test_boot_a_frio_nao_tem_load_ms(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    relogio = _RelogioInjetavel()
    rt, _ = _preparar(harness, monkeypatch, relogio, carregado_em=None, boot_ok_em=relogio.agora,
                      ui=lambda _agora: True)
    s = harness.state
    assert s is not None
    assert await s.devices._wait_boot(rt, relogio.monotonic(), warm=False) is True     # noqa: SLF001
    dados = json.loads(s.db.query("SELECT data FROM measurements WHERE kind='boot'")[-1]["data"])
    assert dados["kind"] == "cold" and "load_ms" not in dados
