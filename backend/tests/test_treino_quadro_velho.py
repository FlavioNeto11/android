"""31.85: gravando, cada entrada lê a hierarquia ANTES de agir e deixa o aparelho lento; o quadro que a pessoa vê
(o mais recente do backend) passava da idade máxima e as teclas seguintes eram recusadas em série (15 de 15, 05/10).
Gravando, o quadro MAIS RECENTE é aceito acima da idade; com um quadro mais novo disponível, ou fora da gravação, a
guarda contra clique em imagem velha continua como era."""
from __future__ import annotations

import time

import pytest

from app.devices.manager import ControlError
from app.models import ControlOwner, ManualInput

from .conftest import Harness


async def _no_controle(h: Harness, *, gravando: bool) -> tuple[object, object, str, str]:
    st = h.state
    assert st is not None
    rt = st.devices.get("android-01")
    await h.wait(lambda: rt.state.value == "online", what="aparelho online")
    status, lease = st.devices.request_control(rt)
    assert status == "granted" and rt.control == ControlOwner.user
    if gravando:
        st.training.start("android-01", intent="x", lease_id=lease)
        assert rt.training_session_id
    frame = (await st.devices.observe(rt, timeout=5)).frame_id
    return st, rt, lease, frame


def _envelhecer(rt: object, frame: str, segundos: float = 3600) -> None:
    _, w, h = rt.recent_frames[frame]                          # type: ignore[attr-defined]
    rt.recent_frames[frame] = (time.monotonic() - segundos, w, h)  # type: ignore[attr-defined]


def _toque(lease: str, frame: str) -> ManualInput:
    return ManualInput(lease_id=lease, frame_id=frame, type="tap", x=10, y=10)


async def test_gravando_o_quadro_mais_recente_acima_da_idade_e_aceito(harness: Harness) -> None:
    st, rt, lease, frame = await _no_controle(harness, gravando=True)
    assert rt.frame.info.id == frame
    _envelhecer(rt, frame, 20)                       # acima do limite (6 s), como o aparelho lento da gravação
    await st.devices.manual_input(rt, _toque(lease, frame))
    await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, type="key", key="delete"))


async def test_gravando_o_quadro_antigo_com_um_mais_novo_disponivel_segue_recusado(harness: Harness) -> None:
    st, rt, lease, frame = await _no_controle(harness, gravando=True)
    atual = rt.frame.info.id
    _, w, h = rt.recent_frames[atual]
    rt.recent_frames["f-antigo"] = (time.monotonic() - 3600, w, h)       # a pessoa clicou olhando uma imagem antiga
    assert rt.frame.info.id != "f-antigo"
    with pytest.raises(ControlError) as velho:
        await st.devices.manual_input(rt, _toque(lease, "f-antigo"))
    assert velho.value.code == "stale_frame"


async def test_fora_da_gravacao_o_quadro_acima_da_idade_segue_recusado(harness: Harness) -> None:
    st, rt, lease, frame = await _no_controle(harness, gravando=False)
    assert rt.training_session_id is None and rt.frame.info.id == frame
    _envelhecer(rt, frame, 20)
    with pytest.raises(ControlError) as velho:
        await st.devices.manual_input(rt, _toque(lease, frame))
    assert velho.value.code == "stale_frame"


async def test_gravando_o_quadro_congelado_ha_muito_tempo_segue_recusado(harness: Harness) -> None:
    st, rt, lease, frame = await _no_controle(harness, gravando=True)
    _envelhecer(rt, frame, 3600)                     # a captura travou (não é só a lentidão da gravação)
    with pytest.raises(ControlError) as velho:
        await st.devices.manual_input(rt, _toque(lease, frame))
    assert velho.value.code == "stale_frame"
