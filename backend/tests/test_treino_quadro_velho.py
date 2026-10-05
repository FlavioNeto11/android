"""31.85: gravando, cada entrada lê a hierarquia ANTES de agir e deixa o aparelho lento; o quadro que a pessoa vê
(o mais recente do backend) passava da idade máxima e as teclas seguintes eram recusadas em série (15 de 15, 05/10).
Gravando, o quadro MAIS RECENTE é aceito acima da idade; com um quadro mais novo disponível, ou fora da gravação, a
guarda contra clique em imagem velha continua como era."""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace

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


# ---- a folga não vale às cegas para o que age no campo em foco (revisão de segredos do 31.85) -----------------------
def _arvore_falsa(*, sensivel: bool, foco_senha: bool) -> SimpleNamespace:
    return SimpleNamespace(sensitive=sensivel, elements=[SimpleNamespace(focused=True, password=foco_senha)],
                           packages=[])


def _espiar_digitacao(harness: Harness) -> list[str]:
    fake = harness.fakes["android-01"]
    digitado: list[str] = []
    original = fake.type_text

    def espia(text: str, *, clear_first: bool) -> None:
        digitado.append(text)
        original(text, clear_first=clear_first)

    fake.type_text = espia
    return digitado


def _arvore_lida(st: object, arvore: object) -> None:
    async def _lida(rt: object) -> object:
        return arvore

    st.devices._arvore_para_treino = _lida                      # type: ignore[attr-defined]


async def _folga(harness: Harness, arvore: object) -> tuple[object, object, str, str]:
    st, rt, lease, frame = await _no_controle(harness, gravando=True)
    _arvore_lida(st, arvore)
    _envelhecer(rt, frame, 20)                       # só passa pela folga da gravação
    return st, rt, lease, frame


async def test_folga_com_texto_e_tela_sensivel_recusa_e_nao_digita(harness: Harness) -> None:
    st, rt, lease, frame = await _folga(harness, _arvore_falsa(sensivel=True, foco_senha=False))
    digitado = _espiar_digitacao(harness)
    with pytest.raises(ControlError) as recusa:
        await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, type="text", text="oi"))
    assert recusa.value.code == "stale_frame" and digitado == []
    for tecla in ("enter", "delete"):
        with pytest.raises(ControlError) as r2:
            await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, type="key", key=tecla))
        assert r2.value.code == "stale_frame"


async def test_folga_com_texto_e_foco_em_senha_recusa(harness: Harness) -> None:
    st, rt, lease, frame = await _folga(harness, _arvore_falsa(sensivel=False, foco_senha=True))
    digitado = _espiar_digitacao(harness)
    with pytest.raises(ControlError) as recusa:
        await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, type="text", text="oi"))
    assert recusa.value.code == "stale_frame" and digitado == []


async def test_folga_sem_leitura_da_arvore_recusa_o_texto(harness: Harness) -> None:
    st, rt, lease, frame = await _folga(harness, None)
    with pytest.raises(ControlError) as recusa:
        await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, type="text", text="oi"))
    assert recusa.value.code == "stale_frame"


async def test_folga_com_texto_e_tela_comum_passa(harness: Harness) -> None:
    st, rt, lease, frame = await _folga(harness, _arvore_falsa(sensivel=False, foco_senha=False))
    digitado = _espiar_digitacao(harness)
    await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, type="text", text="oi"))
    assert digitado == ["oi"]


async def test_folga_com_toque_passa_mesmo_em_tela_sensivel(harness: Harness) -> None:
    st, rt, lease, frame = await _folga(harness, _arvore_falsa(sensivel=True, foco_senha=False))
    st.devices.on_training_input = None                          # a árvore falsa não serve ao gravador
    await st.devices.manual_input(rt, _toque(lease, frame))


async def test_falha_ao_gravar_a_entrada_nao_loga_o_texto_da_excecao(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    sentinela = "SENTINELA-DO-TEXTO-DIGITADO-9f3"
    st, rt, lease, frame = await _no_controle(harness, gravando=True)

    def explode(*_a: object, **_k: object) -> None:
        raise RuntimeError(f"DETAIL: Key (text)=({sentinela}) violates constraint")

    st.devices.on_training_input = explode
    with caplog.at_level(logging.DEBUG):
        await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, type="text", text="oi"))
    assert any("não gravada no treinamento" in r.getMessage() and "RuntimeError" in r.getMessage()
               and rt.training_session_id in r.getMessage() and r.levelno == logging.ERROR for r in caplog.records)
    assert sentinela not in caplog.text
    assert all(r.exc_info is None for r in caplog.records if "não gravada" in r.getMessage())
