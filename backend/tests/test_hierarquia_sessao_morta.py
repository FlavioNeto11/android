"""Hierarquia lida de uma sessão UiAutomator2 morta recria a sessão; reboot feito pelo worker a descarta.

android-09, 01/10/2026: um restart pelo worker matou a instrumentation e o central, que não viu o aparelho cair, seguiu
com a sessão "pronta" — `GET /hierarchy` deu 503 por horas, porque só o executor invalidava a sessão. Tudo aqui é
simulado (aparelho e driver falsos): prova a decisão do `DeviceManager`, não o Appium real."""
from __future__ import annotations

from typing import Any

import pytest

from app.automation.driver import DriverBusy, DriverError
from app.devices.manager import FALHAS_DE_SESSAO_PARA_DEGRADAR, AutomationInfo
from app.models import InstanceState

from .conftest import Harness


def _espiar(devices: Any) -> list[str]:
    vistos: list[str] = []
    original = devices.invalidate_automation

    def espiao(rt: Any, why: str) -> None:
        vistos.append(why)
        original(rt, why)

    devices.invalidate_automation = espiao
    return vistos


async def test_sessao_morta_invalida_recria_uma_vez_e_rele(harness: Harness) -> None:
    d = harness.state.devices
    rt, fake = d.get("android-01"), harness.fakes["android-01"]
    recriacoes = _espiar(d)
    fake.session_lost_reads = 1
    arvore = await d.hierarchy(rt)
    assert fake.session_lost_reads == 0 and arvore.elements          # a releitura trouxe a árvore
    assert len(recriacoes) == 1 and "session" in recriacoes[0].lower()


async def test_sessao_que_continua_morta_tenta_uma_vez_so(harness: Harness) -> None:
    d = harness.state.devices
    rt, fake = d.get("android-01"), harness.fakes["android-01"]
    recriacoes = _espiar(d)
    fake.session_lost_reads = 5
    with pytest.raises(DriverError):
        await d.hierarchy(rt)
    assert fake.session_lost_reads == 3                             # a leitura + UMA releitura, sem laço
    assert len(recriacoes) == 1


async def test_instrumentacao_morta_tambem_recria(harness: Harness) -> None:
    d = harness.state.devices
    rt, fake = d.get("android-01"), harness.fakes["android-01"]
    recriacoes = _espiar(d)
    fake.instrumentacao_morta_reads = 1
    assert (await d.hierarchy(rt)).elements
    assert len(recriacoes) == 1 and "instrumentation process is not running" in recriacoes[0]


async def test_ui_ocupada_nao_derruba_a_sessao(harness: Harness) -> None:
    d = harness.state.devices
    rt, fake = d.get("android-01"), harness.fakes["android-01"]
    recriacoes = _espiar(d)
    fake.busy_reads = 1
    with pytest.raises(DriverBusy):
        await d.hierarchy(rt)
    assert recriacoes == []


async def test_sessao_ja_em_erro_fica_com_o_monitor(harness: Harness) -> None:
    """Só quem ainda acredita "pronta" recria na leitura; em `error` quem retenta é o monitor, espaçado."""
    d = harness.state.devices
    rt, fake = d.get("android-01"), harness.fakes["android-01"]
    recriacoes = _espiar(d)
    rt.automation = AutomationInfo(state="error", detail="appium caiu")
    fake.session_lost_reads = 1
    with pytest.raises(DriverError):
        await d.hierarchy(rt)
    assert recriacoes == []


async def test_uma_leitura_nao_alcanca_o_teto_que_degrada(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    d = harness.state.devices
    rt, fake = d.get("android-01"), harness.fakes["android-01"]
    monkeypatch.setattr(d, "io_factory", None)                       # `ensure_automation` de verdade (o `io` do rt segue falso)
    monkeypatch.setattr(d.appium, "is_up", lambda *_a, **_k: True)
    monkeypatch.setattr(type(rt.session), "connected", property(lambda _self: True))
    monkeypatch.setattr(rt.session, "close", lambda: None)
    monkeypatch.setattr(rt.session, "delete_stale", lambda *_a: None)
    monkeypatch.setattr(rt.adb, "remove_forward", lambda *_a: None)

    def nao_abre() -> None:
        raise RuntimeError("instrumentation process is not running")

    monkeypatch.setattr(rt.session, "connect", nao_abre)
    rt.automation = AutomationInfo(state="ready", detail="systemPort")
    fake.session_lost_reads = 1
    with pytest.raises(DriverError):
        await d.hierarchy(rt)
    assert rt.automation_failures == 1 < FALHAS_DE_SESSAO_PARA_DEGRADAR
    assert rt.state == InstanceState.online and rt.attention is None   # nenhuma degradação por uma leitura


@pytest.mark.parametrize("verbo", ["restart", "reset", "start", "wake"])
async def test_desfecho_remoto_bem_sucedido_descarta_a_sessao_de_antes_do_boot(harness: Harness, verbo: str) -> None:
    d = harness.state.devices
    rt = d.get("android-01")
    recriacoes = _espiar(d)
    await d.readotar_depois_do_worker(rt, verbo, "succeeded")
    assert len(recriacoes) == 1 and "reiniciou" in recriacoes[0]


@pytest.mark.parametrize("verbo,desfecho", [("restart", "failed"), ("restart", "uncertain"), ("stop", "succeeded")])
async def test_o_que_nao_reiniciou_nao_mexe_na_sessao(harness: Harness, verbo: str, desfecho: str) -> None:
    d = harness.state.devices
    rt = d.get("android-01")
    recriacoes = _espiar(d)
    await d.readotar_depois_do_worker(rt, verbo, desfecho)
    assert recriacoes == []
