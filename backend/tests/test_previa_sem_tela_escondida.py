"""A plataforma não esconde tela de ninguém (decisão do dono, 10/10/2026, ADR-089).

Antes (contrato C4 do adendo v0.20) a tela classificada como sensível — campo de senha, desafio, a VM-loja, a
digitação de uma credencial — virava marcador: sem imagem na prévia, `GET /frame` com 404, observação da IA sem
`jpeg`. Aqui o laço de captura fica PARADO (a tarefa é cancelada) e cada ciclo é chamado à mão (`_ciclo_de_previa`),
como no teste que esta regra substituiu.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest_asyncio

from app.main import create_app
from app.models import ManualInput
from app.security.sensitive_input import SensitiveInputChannel

from .conftest import Harness

LOJA = "android-03"


def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _sem_laco(rt: Any) -> None:
    t = rt.tasks.get("capture")
    if t is not None and not t.done():
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> AsyncIterator[Harness]:
    h = Harness(tmp_path, 3, store=LOJA)
    await h.boot()
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_tela_de_senha_aparece_na_previa_na_rota_e_na_observacao(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    rt, fake = s.devices.get("android-01"), harness.fakes["android-01"]
    await _sem_laco(rt)
    fake.screen = "login"                                     # campo de senha na tela
    obs = await s.devices.observe(rt, timeout=5)
    assert obs.jpeg and obs.image_omitted is None
    assert await s.devices._ciclo_de_previa(rt) == "capturada"
    assert rt.frame is not None and rt.frame.jpeg_full and rt.frame.jpeg_thumb
    async with _cliente(harness) as c:
        for modo in ("thumb", "full"):
            r = await c.get(f"/api/instances/android-01/frame?mode={modo}")
            assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content, r.text


async def test_hierarquia_de_tela_de_senha_nao_tira_a_imagem_do_ar(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    rt, fake = s.devices.get("android-01"), harness.fakes["android-01"]
    await _sem_laco(rt)
    fake.screen = "home"
    await s.devices.observe(rt, timeout=5)
    fake.screen = "login"
    s.devices.arvore(rt, fake.page_source())                  # a IA relê a tela: a imagem continua servível
    assert rt.frame is not None and rt.frame.jpeg_full


async def test_captura_de_previa_nao_pausa_durante_type_secret(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    devs = s.devices
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    await _sem_laco(rt)
    fake.screen, fake.focused = "login", None
    vistos: list[str] = []

    async def observar() -> Any:
        vistos.append(await devs._ciclo_de_previa(rt))
        return devs.arvore(rt, await rt.executor.run(rt.io.page_source, timeout=5, label="hierarquia"))

    canal = SensitiveInputChannel(lambda: True)
    recibo = await canal.fill(call=rt.executor.run, io=rt.io, observe=observar,
                              locate=lambda t: next((e for e in t.elements if e.password), None),
                              secret=lambda: "valor-de-teste")
    assert recibo.sensitive_input_completed
    assert vistos and set(vistos) == {"capturada"}


async def test_loja_aparece_na_previa_como_qualquer_aparelho(parque: Harness) -> None:
    s = parque.state
    assert s is not None
    devs = s.devices
    rt, fake = devs.get(LOJA), parque.fakes[LOJA]
    await _sem_laco(rt)
    fake.screen = "home"
    assert await devs._ciclo_de_previa(rt) == "capturada"
    assert rt.frame is not None and rt.frame.jpeg_full
    async with _cliente(parque) as c:
        r = await c.get(f"/api/instances/{LOJA}/frame")
        assert r.status_code == 200 and r.content
    obs = await devs.observe(rt, timeout=5)
    assert obs.jpeg
    _, lease = devs.request_control(rt)
    await devs.manual_input(rt, ManualInput(lease_id=lease, frame_id=obs.frame_id, type="key", key="back"))
    devs.release_control(rt, lease)
