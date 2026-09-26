"""Contrato C4 do adendo v0.20: a prévia NUNCA mostra tela classificada como sensível.

O defeito (H6, reconhecimento da evolução de desempenho): `observe()` publicava o frame ANTES de classificar a
hierarquia, o laço de prévia não classificava nada, `GET /frame` servia `rt.frame` sem conferir, a digitação da
credencial (`type_secret`) não pausava a captura e a VM-loja aparecia na prévia. A IA já não recebia a imagem
sensível — quem a recebia era qualquer painel aberto.

Aqui o laço de captura fica PARADO (a tarefa é cancelada) e cada ciclo é chamado à mão (`_ciclo_de_previa`): o
teste prova a regra sem depender do relógio nem disputar o aparelho com o laço de verdade.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest_asyncio

from app.main import create_app
from app.models import InstanceState, ManualInput
from app.security.sensitive_input import SensitiveInputChannel

from .conftest import Harness

LOJA = "android-03"


def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _sem_laco(rt: Any) -> None:
    """Cancela o laço de captura do aparelho: o ciclo passa a ser chamado só pelo teste."""
    t = rt.tasks.get("capture")
    if t is not None and not t.done():
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


def _screencaps(fake: Any, desde: int) -> int:
    return sum(1 for c in fake.calls[desde:] if c == "screenshot")


@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> AsyncIterator[Harness]:
    h = Harness(tmp_path, 3, store=LOJA)
    await h.boot()
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_tela_sensivel_da_observacao_vira_marcador_e_frame_responde_404(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    rt, fake = s.devices.get("android-01"), harness.fakes["android-01"]
    await _sem_laco(rt)
    fila = s.bus.subscribe()
    fake.screen = "login"                                     # campo de senha na tela
    obs = await s.devices.observe(rt, timeout=5)
    assert obs.sensitive and obs.jpeg is None
    assert rt.frame is not None and rt.frame.sensitive and rt.frame.jpeg_full == b"" and rt.frame.jpeg_thumb == b""
    assert rt.frame.info.sensitive and (rt.frame.info.width, rt.frame.info.height) == (720, 1280)
    eventos = []
    while not fila.empty():
        eventos.append(fila.get_nowait())
    s.bus.unsubscribe(fila)
    quadros = [e for e in eventos if e.kind == "frame"]
    assert quadros and quadros[-1].data["frame"]["sensitive"] is True, "o painel precisa saber que a imagem saiu"
    async with _cliente(harness) as c:
        for modo in ("thumb", "full"):
            r = await c.get(f"/api/instances/android-01/frame?mode={modo}")
            assert r.status_code == 404 and r.json()["detail"]["code"] == "sensitive_screen", r.text
        fake.screen = "home"                                  # saiu da tela sensível: a prévia volta
        obs = await s.devices.observe(rt, timeout=5)
        assert not obs.sensitive and obs.jpeg
        r = await c.get("/api/instances/android-01/frame")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content


async def test_imagem_publicada_sai_do_ar_quando_uma_hierarquia_diz_sensivel(harness: Harness) -> None:
    """A imagem publicada antes pode já mostrar a tela sensível: qualquer leitura de hierarquia que classifique a
    tela como sensível a tira do ar na hora, sem esperar a próxima captura."""
    s = harness.state
    assert s is not None
    rt, fake = s.devices.get("android-01"), harness.fakes["android-01"]
    await _sem_laco(rt)
    fake.screen = "home"
    await s.devices.observe(rt, timeout=5)
    assert rt.frame is not None and not rt.frame.sensitive
    fake.screen = "login"
    s.devices.arvore(rt, fake.page_source())                  # ex.: a ferramenta da IA relendo a tela
    assert rt.frame.sensitive, "a imagem anterior continuava servível depois de a tela virar sensível"


async def test_laco_de_previa_usa_a_ultima_classificacao_e_rele_para_voltar(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    devs = s.devices
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    await _sem_laco(rt)
    fake.screen = "login"
    await devs.observe(rt, timeout=5)
    antes = len(fake.calls)
    # Sensível na última leitura: a prévia relê a hierarquia (a pessoa pode ter resolvido na janela do emulador) e,
    # confirmada a tela sensível, renova o marcador SEM screencap — nada de imagem para classificar depois.
    assert await devs._ciclo_de_previa(rt) == "sensivel"
    assert fake.calls[antes:] == ["page_source"] and rt.frame is not None and rt.frame.sensitive
    fake.screen = "home"
    assert await devs._ciclo_de_previa(rt) == "capturada"
    assert rt.frame is not None and not rt.frame.sensitive and rt.frame.jpeg_full
    antes = len(fake.calls)
    assert await devs._ciclo_de_previa(rt) == "capturada"     # classificação limpa: só screencap, sem hierarquia
    assert fake.calls[antes:] == ["screenshot"]


async def test_classificacao_de_outra_geracao_nao_vale(harness: Harness) -> None:
    """A tela de senha de antes do reinício não diz nada do Android de agora."""
    s = harness.state
    assert s is not None
    devs = s.devices
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    await _sem_laco(rt)
    fake.screen = "login"
    await devs.observe(rt, timeout=5)
    assert devs._previa_sensivel(rt)
    devs._set_state(rt, InstanceState.stopped)
    devs._set_state(rt, InstanceState.online)                 # nova geração
    assert rt.classificacao is None and not devs._previa_sensivel(rt)


async def test_captura_de_previa_pausa_durante_type_secret(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    devs = s.devices
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    await _sem_laco(rt)
    fake.screen, fake.focused = "login", None
    vistos: list[str] = []

    async def observar() -> Any:
        # Dentro do canal sensível, a prévia não entra: nem screencap.
        antes = _screencaps(fake, 0)
        vistos.append(await devs._ciclo_de_previa(rt))
        assert _screencaps(fake, 0) == antes
        return devs.arvore(rt, await rt.executor.run(rt.io.page_source, timeout=5, label="hierarquia"))

    canal = SensitiveInputChannel(lambda: True)
    recibo = await canal.fill(call=rt.executor.run, io=rt.io, observe=observar,
                              locate=lambda t: next((e for e in t.elements if e.password), None),
                              secret=lambda: "valor-de-teste")
    assert recibo.sensitive_input_completed
    assert vistos and set(vistos) == {"pausada"}
    assert not rt.executor.em_trecho_sensivel                 # a marca sai com o canal, mesmo sem erro


async def test_screencap_que_cruza_o_inicio_da_digitacao_e_descartado(harness: Harness) -> None:
    """O screencap enfileirado ANTES de a digitação começar pode rodar ENTRE os passos dela: descartado."""
    s = harness.state
    assert s is not None
    devs = s.devices
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    await _sem_laco(rt)
    fake.screen = "home"
    await devs.observe(rt, timeout=5)
    anterior = rt.frame
    original = fake.screenshot_png

    def screencap_no_meio_da_digitacao() -> bytes:
        with rt.executor.trecho_sensivel():                   # a digitação começou enquanto ele esperava
            return original()

    fake.screenshot_png = screencap_no_meio_da_digitacao      # type: ignore[method-assign]
    try:
        assert await devs._ciclo_de_previa(rt) == "descartada"
    finally:
        fake.screenshot_png = original                        # type: ignore[method-assign]
    assert rt.frame is anterior


async def test_loja_nunca_aparece_na_previa_mas_segue_operavel_por_frame_id(parque: Harness) -> None:
    """A VM-loja segue a regra (C4): toda tela dela é sensível (`MOTIVO_LOJA`), mesmo sem hierarquia — sem sessão de
    automação ela quase nunca tem uma. A decisão 4 do dono (texto pelo painel na loja) continua aceita pelo
    `frame_id` do marcador, às cegas; a senha da conta Google segue recusada."""
    s = parque.state
    assert s is not None
    devs = s.devices
    rt, fake = devs.get(LOJA), parque.fakes[LOJA]
    await _sem_laco(rt)
    fake.screen = "home"                                      # tela SEM campo de senha: ainda assim, loja
    assert await devs._ciclo_de_previa(rt) == "sensivel"
    assert rt.frame is not None and rt.frame.sensitive and rt.frame.jpeg_full == b""
    async with _cliente(parque) as c:
        r = await c.get(f"/api/instances/{LOJA}/frame")
        assert r.status_code == 404 and r.json()["detail"]["code"] == "sensitive_screen"
    obs = await devs.observe(rt, timeout=5)
    assert obs.jpeg is None and rt.frame.sensitive
    _, lease = devs.request_control(rt)
    await devs.manual_input(rt, ManualInput(lease_id=lease, frame_id=obs.frame_id, type="key", key="back"))
    devs.release_control(rt, lease)
