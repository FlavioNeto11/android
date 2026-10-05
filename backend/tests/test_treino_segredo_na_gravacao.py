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
        clickable: bool = False, focused: bool = False, password: bool = False,
        classe: str | None = None) -> UiElement:
    return UiElement(id=i, text=text, desc=desc, resource_id=rid, class_name=classe or (
        "android.widget.EditText" if editable else "android.widget.TextView"), package="com.pocqa.messenger",
        bounds=bounds, clickable=clickable, enabled=True, focused=focused, scrollable=False, editable=editable,
        checked=False, password=password)


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
    campo = _el("e1", rid="com.pocqa.messenger:id/message_input", editable=True, focused=True)
    st.training.record(rt, {"type": "text", "text": "bom dia"}, _arvore(_el("e0", text="Conversa"), campo))   # controle
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


async def test_editavel_so_com_texto_fica_sem_texto_e_sem_seletor(harness: Harness) -> None:
    st, rt, sid = await _gravando(harness)
    campo = _el("e1", text=CONTEUDO, bounds=(0, 1100, 600, 1300), editable=True, clickable=True)   # sem id nem rótulo
    st.training.record(rt, {"type": "tap", "x": 100, "y": 1200}, _arvore(campo))
    entrada = st.training.get(sid)["inputs"][0]
    alvo = entrada["target"]
    assert alvo["text"] == "", alvo
    assert alvo["unique"] == [], alvo
    assert CONTEUDO not in json.dumps(entrada, ensure_ascii=False)
    assert build_selectors(alvo, {}) == []


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


async def test_com_arvore_o_texto_so_e_guardado_com_campo_editavel_que_nao_e_senha_em_foco(harness: Harness) -> None:
    st, rt, sid = await _gravando(harness)
    casos = {
        "sem foco": _arvore(_el("e1", editable=True)),                                    # foco ainda não refletido
        "foco que não é campo": _arvore(_el("e1", text="Conversa", focused=True)),         # WebView, rótulo
        "foco em senha": _arvore(_el("e1", editable=True, focused=True, password=True)),
        "campo editável normal": _arvore(_el("e1", editable=True, focused=True)),
    }
    for tela in casos.values():
        st.training.record(rt, {"type": "text", "text": "abc"}, tela)
    por_caso = dict(zip(casos, st.training.get(sid)["inputs"], strict=True))
    for nome in ("sem foco", "foco que não é campo", "foco em senha"):
        assert por_caso[nome]["text"] is None and por_caso[nome]["has_text"] and por_caso[nome]["text_len"] == 3, nome
    assert por_caso["campo editável normal"]["text"] == "abc"


async def test_filho_de_campo_de_texto_perde_o_conteudo_e_os_seletores_que_dependiam_dele(harness: Harness) -> None:
    st, rt, sid = await _gravando(harness)
    linha = _el("e1", bounds=(0, 0, 600, 200), clickable=True, rid="")
    filho = _el("e2", text=CONTEUDO, rid="com.pocqa.messenger:id/busca", bounds=(10, 10, 500, 100),
                classe="androidx.appcompat.widget.AppCompatAutoCompleteTextView", editable=True)
    st.training.record(rt, {"type": "tap", "x": 550, "y": 150}, _arvore(linha, filho))
    entrada = st.training.get(sid)["inputs"][0]
    filhos = entrada["target"].get("filhos") or []
    assert filhos, entrada["target"]                       # o filho segue como identificador, por resource_id
    assert filhos[0]["text"] == "" and "rid+text" not in filhos[0]["unique"] and "text" not in filhos[0]["unique"]
    assert CONTEUDO not in json.dumps(entrada, ensure_ascii=False)


async def test_codigo_com_espaco_ou_hifen_nao_e_gravado_na_linha_nem_no_alvo(harness: Harness) -> None:
    st, rt, sid = await _gravando(harness)
    tela = _arvore(
        _el("e0", text="123 456", bounds=(0, 0, 600, 100)),
        _el("e1", text="8845-12", rid="com.pocqa.messenger:id/chip", bounds=(0, 100, 600, 200), clickable=True),
        _el("e2", text="QA-001", bounds=(0, 300, 600, 400)))
    st.training.record(rt, {"type": "tap", "x": 50, "y": 150}, tela)
    entrada = st.training.get(sid)["inputs"][0]
    assert entrada["screen_lines"] == ["QA-001"], entrada["screen_lines"]
    assert entrada["target"]["text"] == "" and entrada["target"]["resource_id"].endswith("chip")


async def test_alvo_em_tela_sensivel_guarda_so_o_estrutural(harness: Harness) -> None:
    st, rt, sid = await _gravando(harness)
    linha = _el("e1", bounds=(0, 0, 600, 200), clickable=True)       # contêiner sem identidade: o alvo vai pelo filho
    filho = _el("e2", text="Entrar", rid="com.pocqa.messenger:id/entrar", bounds=(10, 10, 500, 100))
    botao = _el("e3", text="Continuar", desc="Continuar o cadastro", rid="com.pocqa.messenger:id/continuar",
                bounds=(0, 300, 600, 400), clickable=True)
    tela = UiTree(elements=[linha, filho, botao], packages=["com.pocqa.messenger"], sensitive=True)
    st.training.record(rt, {"type": "tap", "x": 100, "y": 350}, tela)
    st.training.record(rt, {"type": "tap", "x": 550, "y": 150}, tela)
    por_id, por_filho = st.training.get(sid)["inputs"]
    alvo = por_id["target"]
    assert alvo["text"] == "" and alvo["desc"] == "" and alvo["resource_id"].endswith("continuar"), alvo
    assert alvo["unique"] == ["rid"], alvo["unique"]
    assert build_selectors(alvo, {}) == [{"kind": "rid", "rid": "com.pocqa.messenger:id/continuar"}]
    assert por_id["sensitive"] is True and por_id["screen_lines"] == []
    filhos = por_filho["target"].get("filhos") or []
    assert filhos and filhos[0]["text"] == "" and filhos[0]["desc"] == "", por_filho["target"]
    assert "Entrar" not in json.dumps(por_filho, ensure_ascii=False)


async def test_tecla_de_pin_desenhada_nao_guarda_o_digito_no_alvo_nem_nos_filhos(harness: Harness) -> None:
    st, rt, sid = await _gravando(harness)
    teclas = _arvore(
        _el("e1", text="4", rid="com.pocqa.messenger:id/tecla", bounds=(0, 0, 100, 100), clickable=True),
        _el("e2", desc="8", rid="com.pocqa.messenger:id/tecla_desc", bounds=(100, 0, 200, 100), clickable=True),
        _el("e3", bounds=(200, 0, 300, 100), clickable=True),                                  # contêiner sem identidade
        _el("e4", text="7", rid="com.pocqa.messenger:id/digito", bounds=(210, 10, 290, 90)))   # filho rotulado
    for x, y in ((50, 50), (150, 50), (205, 5)):
        st.training.record(rt, {"type": "tap", "x": x, "y": y}, teclas)
    alvos = [e["target"] for e in st.training.get(sid)["inputs"]]
    assert alvos[0]["text"] == "" and alvos[0]["unique"] == ["rid"] and alvos[0]["resource_id"].endswith("tecla")
    assert alvos[1]["desc"] == "" and alvos[1]["unique"] == ["rid"]
    filhos = alvos[2].get("filhos") or []
    assert filhos and filhos[0]["text"] == "" and "text" not in filhos[0]["unique"], alvos[2]
    assert not any(d in json.dumps(alvos) for d in ('"text": "4"', '"desc": "8"', '"text": "7"'))


async def test_formatos_de_codigo_reais_nao_vao_para_linhas_titulo_nem_alvo(harness: Harness) -> None:
    st, rt, sid = await _gravando(harness)
    ruins = ["123 456", "4821", "48213", "G-123456 is your Google verification code",
             "Use 123 456 to verify your Instagram account.",
             "Entre com 4821 agora"]            # sem palavra-chave: só o token de 4 dígitos denuncia
    tela = _arvore(
        _el("e0", text="Use 123 456 to verify your Instagram account.", rid="com.pocqa.messenger:id/action_bar_title",
            bounds=(0, 0, 600, 50)),
        *[_el(f"l{i}", text=t, bounds=(0, 60 + 40 * i, 600, 90 + 40 * i)) for i, t in enumerate(ruins)],
        _el("ok", text="Conversa", bounds=(0, 600, 600, 640)),
        _el("alvo", text="G-123456 is your Google verification code", rid="com.pocqa.messenger:id/sms",
            bounds=(0, 700, 600, 760), clickable=True))
    st.training.record(rt, {"type": "tap", "x": 50, "y": 720}, tela)
    entrada = st.training.get(sid)["inputs"][0]
    assert entrada["screen_lines"] == ["Conversa"], entrada["screen_lines"]
    assert entrada["screen_title"] is None
    assert entrada["target"]["text"] == "" and entrada["target"]["resource_id"].endswith("sms")
    assert "123" not in json.dumps(entrada, ensure_ascii=False)


def test_os_campos_de_cada_seletor_acompanham_o_selector_rank_das_receitas() -> None:
    """N4: se alguém acrescentar um seletor novo baseado em texto/desc, o filtro do alvo precisa saber dele."""
    from app.taskqueue.recipes import SELECTOR_RANK, _combo
    from app.training.recorder import _CAMPOS_DO_SELETOR
    assert set(_CAMPOS_DO_SELETOR) == set(SELECTOR_RANK)
    alvo = {"resource_id": "r", "text": "t", "desc": "d"}
    for kind in SELECTOR_RANK:
        rid, text, desc = _combo(kind, alvo)
        usados = tuple(c for c, v in (("resource_id", rid), ("text", text), ("desc", desc)) if v is not None)
        assert _CAMPOS_DO_SELETOR[kind] == usados, kind
