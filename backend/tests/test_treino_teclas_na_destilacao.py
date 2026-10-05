"""31.84: as teclas que a pessoa aperta ao ensinar (Apagar antes de digitar, Enter ao fim do texto) não derrubam mais a
receita da etapa quando são ruído ou já estão no `type_text`; o resto segue recusando. Unitário sobre
`distill_training` (entradas já gravadas) mais o `clear_first` chegando ao digitador em `manual_input`."""
from __future__ import annotations

import pytest

from app.devices.manager import ControlError
from app.models import ControlOwner, ManualInput
from app.taskqueue.recipes import distill_training

from .conftest import Harness

VARS = {"mensagem": "Olá, tudo certo?"}
ALVO = {"resource_id": "app:id/campo", "text": "Mensagem", "unique": ["rid+text"]}


def _toque() -> dict:
    return {"type": "tap", "x": 10, "y": 20, "target": dict(ALVO)}


def _texto(t: str = "Olá, tudo certo?", *, limpando: bool = False) -> dict:
    """`limpando`: o texto foi enviado com `clear_first` (o gravador marca em `key_name`, sem coluna nova)."""
    return {"type": "text", "text": t, "key_name": "clear_first" if limpando else None}


def _tecla(nome: str) -> dict:
    return {"type": "key", "key_name": nome}


def _destila(entradas: list[dict]):
    return distill_training(entradas, VARS, side_effect=False)


def test_apagar_antes_do_texto_e_ruido() -> None:
    acoes, motivo = _destila([_toque(), _tecla("delete"), _tecla("delete"), _tecla("delete"), _texto(limpando=True)])
    assert motivo == "ok" and acoes is not None
    assert [a["tool"] for a in acoes] == ["tap", "type_text"]
    assert acoes[1]["args"] == {"text": "{mensagem}", "clear_first": True, "press_enter": False}


def test_enter_logo_apos_o_texto_vira_press_enter() -> None:
    acoes, motivo = _destila([_toque(), _texto(), _tecla("enter")])
    assert motivo == "ok" and acoes is not None
    assert [a["tool"] for a in acoes] == ["tap", "type_text"]
    assert acoes[1]["args"]["press_enter"] is True


def test_apagar_so_e_ruido_quando_colado_ao_texto() -> None:
    motivo_delete = "tecla delete depende do estado de quem ensinou"
    # (a) o apagar foi no campo A e o texto é do campo B: a receita só limparia o B
    acoes, motivo = _destila([_toque(), _tecla("delete"), _tecla("delete"), _tecla("delete"), _toque(), _texto()])
    assert acoes is None and motivo == motivo_delete
    # (b) colado ao texto segue valendo
    acoes, motivo = _destila([_toque(), _tecla("delete"), _tecla("delete"), _tecla("delete"), _texto(limpando=True)])
    assert motivo == "ok" and [a["tool"] for a in acoes] == ["tap", "type_text"]
    # (c) outra tecla no meio: recusa (pelo próprio delete, que vem primeiro)
    acoes, motivo = _destila([_tecla("delete"), _tecla("enter"), _texto()])
    assert acoes is None and motivo == motivo_delete
    # arraste no meio também separa
    acoes, motivo = _destila([_toque(), _tecla("delete"), {"type": "swipe", "x": 1, "y": 9, "x2": 1, "y2": 1}, _texto()])
    assert acoes is None and motivo == motivo_delete


def test_apagar_parcial_colado_ao_texto_sem_clear_first_recusa() -> None:
    # campo "Olá Maria", apaga 5 e digita o nome: a receita limparia TUDO e divergiria do ensinado
    acoes, motivo = _destila([_toque(), _tecla("delete"), _tecla("delete"), _texto(limpando=False)])
    assert acoes is None and motivo == "tecla delete depende do estado de quem ensinou"


def test_texto_com_clear_first_sem_apagar_segue_ok() -> None:
    acoes, motivo = _destila([_toque(), _texto(limpando=True)])
    assert motivo == "ok" and acoes is not None and acoes[1]["args"]["clear_first"] is True


def test_apagar_depois_do_ultimo_texto_recusa() -> None:
    acoes, motivo = _destila([_toque(), _texto(), _tecla("delete")])
    assert acoes is None and motivo == "tecla delete depende do estado de quem ensinou"


def test_apagar_numa_etapa_sem_texto_recusa() -> None:
    acoes, motivo = _destila([_toque(), _tecla("delete")])
    assert acoes is None and motivo == "tecla delete depende do estado de quem ensinou"


def test_enter_solto_recusa() -> None:
    acoes, motivo = _destila([_toque(), _tecla("enter")])
    assert acoes is None and motivo == "tecla enter depende do estado de quem ensinou"
    # nem o Apagar entre o texto e o Enter o cola ao texto
    acoes, motivo = _destila([_toque(), _texto(), _tecla("delete"), _tecla("enter")])
    assert acoes is None and "delete" in motivo


@pytest.mark.parametrize("tecla", ["back", "home", "recents"])
def test_navegacao_segue_recusando(tecla: str) -> None:
    acoes, motivo = _destila([_toque(), _texto(), _tecla(tecla)])
    assert acoes is None and motivo == f"tecla {tecla} depende do estado de quem ensinou"
    # mesmo com texto depois: só o Apagar é ruído
    acoes, motivo = _destila([_tecla(tecla), _texto()])
    assert acoes is None and tecla in motivo


def test_casos_que_ja_valiam() -> None:
    acoes, motivo = _destila([_toque(), _texto("texto livre que nenhum parâmetro cobre")])
    assert acoes is None and "100 % coberto" in motivo
    acoes, motivo = _destila([{"type": "tap", "x": 1, "y": 2, "target": None}])
    assert acoes is None and "sem elemento identificado" in motivo


async def test_manual_input_repassa_clear_first_ao_digitador(harness: Harness) -> None:
    st = harness.state
    rt = st.devices.get("android-01")
    await harness.wait(lambda: rt.state.value == "online", what="aparelho online")
    status, lease = st.devices.request_control(rt)
    assert status == "granted" and rt.control == ControlOwner.user
    fake = harness.fakes["android-01"]
    chamadas: list[bool] = []
    original = fake.type_text

    def espia(text: str, *, clear_first: bool) -> None:
        chamadas.append(clear_first)
        original(text, clear_first=clear_first)

    fake.type_text = espia
    frame = (await st.devices.observe(rt, timeout=5)).frame_id
    await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, type="text", text="a", clear_first=True))
    await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, type="text", text="b"))
    assert chamadas == [True, False]


async def test_o_gravador_marca_o_texto_enviado_com_clear_first(harness: Harness) -> None:
    st = harness.state
    rt = st.devices.get("android-01")
    await harness.wait(lambda: rt.state.value == "online", what="aparelho online")
    _, lease = st.devices.request_control(rt)
    sessao = st.training.start("android-01", intent="x", lease_id=lease)
    frame = (await st.devices.observe(rt, timeout=5)).frame_id
    await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, type="text", text="limpo", clear_first=True))
    await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, type="text", text="acrescenta"))
    marcas = [e["key_name"] for e in st.training.get(sessao["id"])["inputs"]]
    assert marcas == ["clear_first", None]


async def test_clear_first_pelo_adb_puro_recusa_bad_input_e_nao_digita(harness: Harness) -> None:
    st = harness.state
    rt = st.devices.get("android-01")
    await harness.wait(lambda: rt.state.value == "online", what="aparelho online")
    _, lease = st.devices.request_control(rt)
    frame = (await st.devices.observe(rt, timeout=5)).frame_id
    digitado: list[str] = []
    fabrica = st.devices.io_factory
    assert not rt.session.connected                     # sem sessão do Appium: o que sobra é o ADB puro
    rt.adb.input_text_ascii = lambda texto: digitado.append(texto)      # o adb real nunca é tocado
    st.devices.io_factory = None
    try:
        with pytest.raises(ControlError) as recusa:
            await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, type="text", text="x",
                                                          clear_first=True))
        assert recusa.value.code == "bad_input" and digitado == []
    finally:
        st.devices.io_factory = fabrica


def test_clear_first_so_vale_para_texto() -> None:
    with pytest.raises(ValueError):
        ManualInput(lease_id="l", frame_id="f", type="tap", x=1, y=1, clear_first=True)
    assert ManualInput(lease_id="l", frame_id="f", type="text", text="a", clear_first=True).clear_first is True
