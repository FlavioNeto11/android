"""31.82: o que a gravação do modo treinamento NÃO guarda — texto sem árvore, conteúdo de campo editável no alvo e
linhas de tela que falam de código ou senha."""
from __future__ import annotations

import json

from app.automation.hierarchy import UiElement, UiTree
from app.models import ControlOwner
from app.taskqueue.recipes import build_selectors

from .conftest import Harness

CONTEUDO = "rascunho que a pessoa ja digitou"     # conteúdo comum: o ponto é o campo não ser identificado por ele


def _el(i: str, *, text: str = "", desc: str = "", rid: str = "", bounds=(0, 0, 100, 100), editable: bool = False,
        clickable: bool = False) -> UiElement:
    return UiElement(id=i, text=text, desc=desc, resource_id=rid, class_name="android.widget.EditText" if editable
                     else "android.widget.TextView", package="com.pocqa.messenger", bounds=bounds, clickable=clickable,
                     enabled=True, focused=False, scrollable=False, editable=editable, checked=False, password=False)


def _arvore(*elementos: UiElement) -> UiTree:
    return UiTree(elements=list(elementos), packages=["com.pocqa.messenger"], sensitive=False)


async def _gravando(harness: Harness):
    st = harness.state
    rt = st.devices.get("android-01")
    await harness.wait(lambda: rt.state.value == "online", what="aparelho online")
    status, lease = st.devices.request_control(rt)
    assert status == "granted" and rt.control == ControlOwner.user
    s = st.training.start("android-01", intent="Mandar mensagem", lease_id=lease)
    return st, rt, s["id"]


async def test_sem_arvore_o_texto_digitado_nao_e_gravado_mas_com_arvore_o_texto_comum_sim(harness: Harness) -> None:
    st, rt, sid = await _gravando(harness)
    st.training.record(rt, {"type": "text", "text": "abc"}, None)            # curto e minúsculo: nenhuma heurística pega
    st.training.record(rt, {"type": "text", "text": "bom dia"}, _arvore(_el("e1", text="Conversa")))   # controle
    sem, com = st.training.get(sid)["inputs"]
    assert sem["text"] is None and sem["has_text"] is True and sem["text_len"] == 3
    assert com["text"] == "bom dia" and com["has_text"] is True


async def test_toque_em_campo_editavel_nao_grava_o_conteudo_e_a_receita_segue_por_resource_id(harness: Harness) -> None:
    st, rt, sid = await _gravando(harness)
    campo = _el("e1", text=CONTEUDO, rid="com.pocqa.messenger:id/message_input", bounds=(0, 1100, 600, 1300),
                editable=True, clickable=True)
    st.training.record(rt, {"type": "tap", "x": 100, "y": 1200}, _arvore(_el("e0", text="QA-001"), campo))
    entrada = st.training.get(sid)["inputs"][0]
    alvo = entrada["target"]
    assert alvo["resource_id"].endswith("message_input") and alvo["editable"] is True
    assert alvo["text"] == "", alvo
    assert CONTEUDO not in json.dumps(entrada, ensure_ascii=False)
    assert "rid" in alvo["unique"] and "rid+text" not in alvo["unique"] and "text" not in alvo["unique"]
    assert build_selectors(alvo, {}) == [{"kind": "rid", "rid": "com.pocqa.messenger:id/message_input"}]


async def test_alvo_com_texto_que_parece_segredo_perde_o_texto(harness: Harness) -> None:
    st, rt, sid = await _gravando(harness)
    botao = _el("e1", text="Seu código é 123456", rid="com.pocqa.messenger:id/aviso", bounds=(0, 0, 600, 200),
                clickable=True)
    st.training.record(rt, {"type": "tap", "x": 50, "y": 50}, _arvore(botao))
    alvo = st.training.get(sid)["inputs"][0]["target"]
    assert alvo["text"] == "" and alvo["resource_id"].endswith("aviso")


async def test_tela_com_codigo_de_verificacao_nao_vai_para_as_linhas_nem_para_o_titulo(harness: Harness) -> None:
    st, rt, sid = await _gravando(harness)
    tela = _arvore(
        _el("e0", text="Código de verificação", rid="com.pocqa.messenger:id/action_bar_title", bounds=(0, 0, 600, 100)),
        _el("e1", text="Seu código é 123456", bounds=(0, 100, 600, 200)),
        _el("e2", text="Digite a senha para continuar", bounds=(0, 200, 600, 300)),
        _el("e3", text="QA-001", bounds=(0, 300, 600, 400)))
    st.training.record(rt, {"type": "key", "key": "back"}, tela)
    entrada = st.training.get(sid)["inputs"][0]
    assert entrada["screen_lines"] == ["QA-001"], entrada["screen_lines"]
    assert entrada["screen_title"] is None
    assert "123456" not in json.dumps(entrada, ensure_ascii=False)
