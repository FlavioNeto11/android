"""29.105: o controle manual tem saída numa tela protegida contra captura (FLAG_SECURE: a aba anônima do Chrome).

Na medida do 31.72 (android-09, 05/10) o quadro congelou e TODO `/input` voltou `stale_frame`, inclusive o Voltar: sem
saída pelo painel. Agora a tecla não depende do quadro, e o quadro velho com a captura falhando diz o motivo e a saída.
"""
from __future__ import annotations

import time

import pytest

from app.devices.manager import ControlError
from app.models import ControlOwner, ManualInput

from .conftest import Harness


async def _no_controle(h: Harness) -> tuple[object, str, str]:
    st = h.state
    assert st is not None
    rt = st.devices.get("android-01")
    status, lease = st.devices.request_control(rt)
    await h.wait(lambda: rt.control == ControlOwner.user, what="controle concedido")
    frame = (await st.devices.observe(rt, timeout=5)).frame_id
    return rt, lease, frame


def _envelhecer(rt: object, frame: str) -> None:
    """O quadro para de se renovar (o que a tela protegida faz): o último fica velho no registro."""
    _, w, h = rt.recent_frames[frame]                        # type: ignore[attr-defined]
    rt.recent_frames[frame] = (time.monotonic() - 3600, w, h)  # type: ignore[attr-defined]


async def test_a_tecla_passa_com_o_quadro_congelado_e_ate_sem_quadro_conhecido(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    rt, lease, frame = await _no_controle(harness)
    _envelhecer(rt, frame)
    fake = harness.fakes["android-01"]
    antes = len(fake.calls)
    await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, type="key", key="back"))
    await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id="quadro-que-nao-existe", type="key",
                                                  key="home"))
    assert len(fake.calls) > antes                           # as duas teclas chegaram ao aparelho
    with pytest.raises(ControlError) as outro:               # o lease continua valendo para a tecla
        await st.devices.manual_input(rt, ManualInput(lease_id="outro", frame_id=frame, type="key", key="back"))
    assert outro.value.code == "not_controller"


async def test_a_tecla_passa_sem_quadro_nenhum_e_o_toque_diz_capture_failing(harness: Harness) -> None:
    """O painel manda a tecla com `frame_id: ''` quando nenhuma imagem chegou: o backend não pode depender do quadro."""
    st = harness.state
    assert st is not None
    rt, lease, _ = await _no_controle(harness)
    rt.frame, rt.capture_failures, rt.capture_error = None, 1, "DriverError: screencap falhou"  # type: ignore[attr-defined]
    await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id="", type="key", key="back"))
    with pytest.raises(ControlError) as recusa:
        await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id="", type="tap", x=10, y=10))
    assert recusa.value.code == "capture_failing"


async def test_toque_com_quadro_velho_e_captura_sa_segue_stale_frame(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    rt, lease, frame = await _no_controle(harness)
    _envelhecer(rt, frame)
    rt.capture_failures = 0
    with pytest.raises(ControlError) as velho:
        await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, type="tap", x=10, y=10))
    assert velho.value.code == "stale_frame"


async def test_toque_e_texto_com_a_captura_falhando_dizem_o_motivo_e_a_saida(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    rt, lease, frame = await _no_controle(harness)
    _envelhecer(rt, frame)
    rt.capture_failures, rt.capture_error = 4, "DriverError: screencap falhou"
    for entrada in (ManualInput(lease_id=lease, frame_id=frame, type="tap", x=10, y=10),
                    ManualInput(lease_id=lease, frame_id=frame, type="text", text="oi"),
                    ManualInput(lease_id=lease, frame_id="quadro-que-nao-existe", type="swipe", x=1, y=1, x2=5, y2=5)):
        with pytest.raises(ControlError) as recusa:
            await st.devices.manual_input(rt, entrada)
        assert recusa.value.code == "capture_failing"
        assert "screencap falhou" in recusa.value.message and "Voltar" in recusa.value.message


async def test_a_rota_devolve_409_capture_failing_e_aceita_a_tecla(harness: Harness) -> None:
    import httpx

    st = harness.state
    assert st is not None
    rt, lease, frame = await _no_controle(harness)
    _envelhecer(rt, frame)
    rt.capture_failures, rt.capture_error = 2, "DriverError: screencap falhou"
    from app.main import create_app

    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        toque = await c.post("/api/instances/android-01/input",
                             json={"lease_id": lease, "frame_id": frame, "type": "tap", "x": 10, "y": 10})
        assert toque.status_code == 409 and toque.json()["detail"]["code"] == "capture_failing"
        tecla = await c.post("/api/instances/android-01/input",
                             json={"lease_id": lease, "frame_id": frame, "type": "key", "key": "back"})
        assert tecla.status_code == 200
