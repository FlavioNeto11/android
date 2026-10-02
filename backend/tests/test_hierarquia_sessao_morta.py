"""Hierarquia lida de uma sessão UiAutomator2 morta recria a sessão; reboot feito pelo worker a descarta.

android-09, 01/10/2026: um restart pelo worker matou a instrumentation e o central, que não viu o aparelho cair, seguiu
com a sessão "pronta" — `GET /hierarchy` deu 503 por horas, porque só o executor invalidava a sessão. Tudo aqui é
simulado (aparelho e driver falsos): prova a decisão do `DeviceManager`, não o Appium real."""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import httpx
import pytest

from app.automation.driver import DriverBusy, DriverError
from app.devices.manager import FALHAS_DE_SESSAO_PARA_DEGRADAR, AutomationInfo
from app.main import create_app
from app.models import InstanceState

from .conftest import Harness
from .fake_device import SESSAO_PERDIDA


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


def _sessao_real(d: Any, rt: Any, monkeypatch: pytest.MonkeyPatch, connect: Any) -> dict[str, int]:
    """Sai do desvio de teste de `ensure_automation` (o `io` do `rt` segue sendo o falso) e conta o que a abertura faz."""
    n = {"close": 0, "delete_stale": 0, "forward": 0, "connect": 0, "ativos": 0, "max_ativos": 0}
    trava = threading.Lock()

    def contar(chave: str) -> Any:
        def f(*_a: Any) -> None:
            n[chave] += 1
        return f

    def conectar() -> None:
        with trava:
            n["connect"] += 1
            n["ativos"] += 1
            n["max_ativos"] = max(n["max_ativos"], n["ativos"])
        try:
            connect()
        finally:
            with trava:
                n["ativos"] -= 1

    monkeypatch.setattr(d, "io_factory", None)
    monkeypatch.setattr(d.appium, "is_up", lambda *_a, **_k: True)
    monkeypatch.setattr(type(rt.session), "connected", property(lambda _self: True))
    monkeypatch.setattr(rt.session, "close", contar("close"))
    monkeypatch.setattr(rt.session, "delete_stale", contar("delete_stale"))
    monkeypatch.setattr(rt.adb, "remove_forward", contar("forward"))
    monkeypatch.setattr(rt.session, "connect", conectar)
    return n


async def test_leitura_com_sessao_trocada_no_meio_nao_derruba_a_nova(harness: Harness) -> None:
    """A leitura A começou na sessão antiga; enquanto esperava, outra coroutine abriu a sessão nova. O erro de A é da
    antiga: invalidar agora derrubaria a nova, que está boa."""
    d = harness.state.devices
    rt, fake = d.get("android-01"), harness.fakes["android-01"]
    recriacoes = _espiar(d)

    def lida_na_sessao_antiga() -> str:
        rt.automation = AutomationInfo(state="ready", detail="sessão nova, aberta por outro")
        raise DriverError(SESSAO_PERDIDA, effect_possible=False)

    fake.page_source = lida_na_sessao_antiga                        # type: ignore[method-assign]
    with pytest.raises(DriverError):
        await d.hierarchy(rt)
    assert recriacoes == [] and rt.automation.detail.startswith("sessão nova")


async def test_recriacao_que_funciona_e_segunda_leitura_que_falha_nao_contam_falha(harness: Harness,
                                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    d = harness.state.devices
    rt, fake = d.get("android-01"), harness.fakes["android-01"]
    n = _sessao_real(d, rt, monkeypatch, lambda: None)
    rt.automation = AutomationInfo(state="ready", detail="systemPort")
    fake.session_lost_reads = 2                                      # a sessão nova também responde "morta"
    with pytest.raises(DriverError):
        await d.hierarchy(rt)
    assert n["connect"] == 1 and fake.session_lost_reads == 0        # UMA recriação, UMA releitura
    assert rt.automation_failures == 0 and rt.state == InstanceState.online


async def test_uma_chamada_http_nao_degrada_mesmo_com_tudo_falhando(harness: Harness,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Sessão velha + recriação que falha + leitura que falharia de novo: UMA requisição soma no máximo 1 falha, e o
    aparelho segue `online` e sem atenção (o teto de 3 é o que dispara a escada de reparo)."""
    d = harness.state.devices
    rt, fake = d.get("android-01"), harness.fakes["android-01"]

    def nao_abre() -> None:
        raise RuntimeError("instrumentation process is not running")

    n = _sessao_real(d, rt, monkeypatch, nao_abre)
    rt.automation = AutomationInfo(state="ready", detail="systemPort")
    fake.session_lost_reads = 5
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/instances/android-01/hierarchy")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "automation_unavailable"
    assert n["connect"] == 1 and rt.automation_failures == 1 < FALHAS_DE_SESSAO_PARA_DEGRADAR
    assert rt.state == InstanceState.online and rt.attention is None


# ---------------------------------------------------------------- desfecho remoto: quais verbos invalidam
@pytest.mark.parametrize("verbo,dados,invalida", [
    ("restart", None, True),                       # `_v_restart`: stop + start a frio, sempre processo novo
    ("restart", {"restarted": True, "started": True}, True),
    ("reset", {"reset": True, "started": True}, True),
    ("start", {"started": True, "from_snapshot": False}, True),    # boot a frio
    ("wake", {"started": True, "from_snapshot": True}, True),      # volta do snapshot: o `_boot` local também descarta
    ("start", {"started": False, "detail": "o emulador já estava no ar"}, False),   # nada foi tocado: a sessão vale
    ("wake", {"started": False, "detail": "o emulador já estava no ar"}, False),
    ("start", None, False),                        # sem afirmação do agente, não se invalida o que ninguém provou morto
])
async def test_desfecho_remoto_so_invalida_quando_o_aparelho_subiu_de_novo(harness: Harness, verbo: str,
                                                                          dados: Any, invalida: bool) -> None:
    d = harness.state.devices
    rt = d.get("android-01")
    rt.automation_failures, rt.automation_last_error = 2, "falha de antes do boot"
    recriacoes = _espiar(d)
    entrada = rt.online_since_mono = rt.online_since_mono - 100.0
    await d.readotar_depois_do_worker(rt, verbo, "succeeded", dados)
    assert len(recriacoes) == (1 if invalida else 0)
    # O boot novo também renova o marco que a rede compara com a medição (29.22); sem boot novo, o marco não anda.
    assert (rt.online_since_mono > entrada) == invalida
    if invalida:
        assert "reiniciou" in recriacoes[0]
        assert (rt.automation_failures, rt.automation_last_error) == (0, None)     # contador é da vida anterior
    else:
        assert rt.automation_failures == 2


@pytest.mark.parametrize("verbo,desfecho", [("restart", "failed"), ("restart", "uncertain"), ("stop", "succeeded")])
async def test_o_que_nao_reiniciou_nao_mexe_na_sessao(harness: Harness, verbo: str, desfecho: str) -> None:
    d = harness.state.devices
    rt = d.get("android-01")
    recriacoes = _espiar(d)
    await d.readotar_depois_do_worker(rt, verbo, desfecho, {"started": True})
    assert recriacoes == []


# ---------------------------------------------------------------- corrida: readoção x leitura
@pytest.mark.parametrize("atraso_da_leitura_s", [0.0, 0.05, 0.3])
async def test_readocao_e_leitura_ao_mesmo_tempo_abrem_uma_sessao_so(harness: Harness, monkeypatch: pytest.MonkeyPatch,
                                                                     atraso_da_leitura_s: float) -> None:
    """Reinício pelo worker concluído (invalida e agenda a abertura) e um `GET /hierarchy` que chega junto, antes,
    durante a arrumação ou durante o `connect`: a exclusão é o estado `starting` de `ensure_automation` (marcado sem
    `await` entre o teste e a marca), então há UM `connect`, nunca dois ao mesmo tempo, nenhum `close` fora da abertura
    e as falhas não se duplicam."""
    d = harness.state.devices
    rt = d.get("android-01")
    n = _sessao_real(d, rt, monkeypatch, lambda: time.sleep(0.2))
    rt.automation = AutomationInfo(state="ready", detail="sessão de antes do boot")

    async def readotar_nada(_rt: Any) -> None:
        return None

    async def arrumar(_rt: Any) -> None:                             # a arrumação de entrada (diálogo, relógio)
        await asyncio.sleep(0.1)

    monkeypatch.setattr(d, "_readotar_agora", readotar_nada)
    monkeypatch.setattr(d, "_arrumar_depois_de_entrar", arrumar)

    async def leitura() -> Any:
        await asyncio.sleep(atraso_da_leitura_s)
        return await d.hierarchy(rt)

    _, arvore = await asyncio.gather(d.readotar_depois_do_worker(rt, "restart", "succeeded", None), leitura())
    tarefa = rt.tasks.get("automation")
    if tarefa is not None:
        assert await tarefa is True
    assert arvore.elements
    assert n["connect"] == 1 and n["max_ativos"] == 1
    assert n["close"] == 1 and n["delete_stale"] == 1               # só o `close` de DENTRO da abertura
    assert rt.automation.state == "ready" and rt.automation_failures == 0
