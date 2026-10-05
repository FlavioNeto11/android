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


def test_rotulo_de_um_digito_sai_do_alvo_e_dos_filhos_e_leva_os_unique_dele() -> None:
    """S1 (defesa em profundidade: o toque em tecla já nem grava alvo, 31.94; isto vale para o que escapar dele)."""
    from app.training.recorder import _alvo_sem_segredo
    alvo = {"text": "4", "desc": "8", "resource_id": "r:id/tecla", "class_name": "x.View",
            "unique": ["rid+text", "rid+desc", "rid", "text", "desc"],
            "filhos": [{"text": "7", "desc": "", "resource_id": "r:id/digito", "class_name": "x.View",
                        "unique": ["rid+text", "rid", "text"]}]}
    limpo = _alvo_sem_segredo(alvo)
    assert limpo["text"] == "" and limpo["desc"] == "" and limpo["unique"] == ["rid"]
    assert limpo["filhos"][0]["text"] == "" and limpo["filhos"][0]["unique"] == ["rid"]


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


# ---- 31.94: teclado de PIN desenhado e toque em tela sensível sem identificador -------------------------------------
def _tecla(i: str = "e1", **campos) -> UiElement:
    return _el(i, bounds=(0, 0, 200, 200), clickable=True, **campos)


async def _toque_unico(harness: Harness, elemento: UiElement, *, sensivel: bool = False):
    st = harness.state
    rt = st.devices.get("android-01")
    sid = st.training.active_for("android-01")
    if sid is None:
        st, rt, sid = await _gravando(harness)
    tela = UiTree(elements=[elemento], packages=["com.pocqa.messenger"], sensitive=sensivel)
    st.training.record(rt, {"type": "tap", "x": 100, "y": 100}, tela)
    return st.training.get(sid)["inputs"][-1]


def _nao_gravado(entrada: dict) -> bool:
    return (entrada["target"] is None and entrada["x"] is None and entrada["y"] is None and entrada["sensitive"] is True)


async def test_tecla_com_rotulo_de_digito_e_rid_com_digito_nao_grava_alvo_nem_coordenada(harness: Harness) -> None:
    assert _nao_gravado(await _toque_unico(harness, _tecla(text="4", rid="com.android.systemui:id/key4")))


async def test_tecla_telefonica_com_letras_nao_grava_alvo_nem_coordenada(harness: Harness) -> None:
    for rotulo in ("2,ABC", "2 ABC", "2ABC"):
        assert _nao_gravado(await _toque_unico(harness, _tecla(text=rotulo))), rotulo


async def test_tecla_sem_rotulo_com_rid_terminado_em_digito_nao_grava_alvo_nem_coordenada(harness: Harness) -> None:
    assert _nao_gravado(await _toque_unico(harness, _tecla(rid="com.pocqa.messenger:id/digit_7")))


async def test_rotulo_de_digito_so_na_desc_tambem_e_tecla(harness: Harness) -> None:
    assert _nao_gravado(await _toque_unico(harness, _tecla(desc="1", rid="com.pocqa.messenger:id/botao")))


async def test_botao_de_dialogo_com_rotulo_termina_em_digito_mas_segue_gravado(harness: Harness) -> None:
    for rid, rotulo in (("android:id/button1", "OK"), ("android:id/button2", "Cancelar")):
        entrada = await _toque_unico(harness, _tecla(text=rotulo, rid=rid))
        assert entrada["target"]["text"] == rotulo and entrada["target"]["resource_id"] == rid, rid
        assert entrada["x"] == 100 and entrada["y"] == 100 and entrada["sensitive"] is False, rid


async def test_teclado_desenhado_num_view_so_pelo_nome_do_id_ou_da_classe(harness: Harness) -> None:
    assert _nao_gravado(await _toque_unico(harness, _tecla(rid="com.pocqa.messenger:id/pin_pad")))
    assert _nao_gravado(await _toque_unico(harness, _tecla(classe="com.pocqa.ui.PinKeypadView")))
    for rid in ("com.pocqa.messenger:id/shopping_cart", "com.pocqa.messenger:id/spinner_cidade"):   # "pin" dentro de palavra
        entrada = await _toque_unico(harness, _tecla(rid=rid))
        assert entrada["target"]["resource_id"] == rid and entrada["x"] == 100 and entrada["sensitive"] is False, rid


async def test_tela_sensivel_toque_sem_identificador_sai_sem_coordenada_e_com_id_segue_estrutural(harness: Harness) -> None:
    sem_id = await _toque_unico(harness, _tecla(text="Entrar"), sensivel=True)
    assert _nao_gravado(sem_id)
    com_id = await _toque_unico(harness, _tecla(text="Continuar", rid="com.pocqa.messenger:id/continuar"), sensivel=True)
    assert com_id["target"]["text"] == "" and com_id["target"]["resource_id"].endswith("continuar")
    assert com_id["x"] == 100 and com_id["sensitive"] is True


async def test_a_destilacao_recusa_a_etapa_com_tecla_nao_gravada_sem_excecao(harness: Harness) -> None:
    from app.taskqueue.recipes import distill_training
    entrada = await _toque_unico(harness, _tecla(text="4", rid="com.android.systemui:id/key4"))
    receita, motivo = distill_training([entrada], {}, side_effect=False)
    assert receita is None and "sem elemento identificado" in motivo, motivo


async def test_a_linha_da_entrada_de_tecla_nao_gravada_nao_traz_none_coordenada_nem_id(harness: Harness) -> None:
    from app.planning.training import linha_da_entrada
    entrada = await _toque_unico(harness, _tecla(text="4", rid="com.android.systemui:id/key4"))
    linha = linha_da_entrada(entrada)
    assert "toque em teclado ou tela sensível (não gravado)" in linha, linha
    assert "None" not in linha and "ponto" not in linha and "key4" not in linha and "elemento" not in linha, linha
    # o toque comum sem alvo segue como antes, com a coordenada
    comum = {"seq": 2, "type": "tap", "x": 10, "y": 20, "target": None, "sensitive": False}
    assert linha_da_entrada(comum) == "#2 tap | ponto=(10,20) sem elemento identificado"
    # sem x e sem a marca (entrada antiga): nunca "ponto=(None,None)"
    assert "None" not in linha_da_entrada({"seq": 3, "type": "tap", "x": None, "y": None, "target": None})


async def test_toque_sem_seletor_utilizavel_sai_sem_coordenada_mas_nao_e_sensivel(harness: Harness) -> None:
    from app.planning.training import linha_da_entrada
    # contêiner sem id, sem rótulo e sem filho rotulado (Flutter/SurfaceView): a posição seria o dígito
    entrada = await _toque_unico(harness, _tecla(classe="io.flutter.embedding.android.FlutterView"))
    assert entrada["x"] is None and entrada["y"] is None and entrada["sensitive"] is False
    assert "None" not in linha_da_entrada(entrada) and "ponto" not in linha_da_entrada(entrada)
    # nem alvo nenhum (toque fora de qualquer elemento): a linha não traz ponto, só "sem elemento identificado"
    st, rt, sid = harness.state, harness.state.devices.get("android-01"), harness.state.training.active_for("android-01")
    tela = UiTree(elements=[_tecla()], packages=["com.pocqa.messenger"], sensitive=False)
    st.training.record(rt, {"type": "tap", "x": 900, "y": 900}, tela)
    fora = st.training.get(sid)["inputs"][-1]
    assert fora["target"] is None and fora["x"] is None and fora["sensitive"] is False
    assert linha_da_entrada(fora).endswith("| sem elemento identificado") and "None" not in linha_da_entrada(fora)
    # com `unique` o toque segue como sempre, com x/y
    com_id = await _toque_unico(harness, _tecla(text="Enviar", rid="com.pocqa.messenger:id/enviar"))
    assert com_id["x"] == 100 and com_id["y"] == 100 and com_id["target"]["unique"]


async def test_nomes_de_teclado_novos_caem_e_os_parecidos_seguem_gravados(harness: Harness) -> None:
    for nome in ("pin_code", "PinView", "pin_entry", "pin_lock", "number_pad", "NumberPad", "pattern_lock", "lock_view",
                 "dial_pad", "DialPad"):
        assert _nao_gravado(await _toque_unico(harness, _tecla(rid=f"com.pocqa.messenger:id/{nome}"))), nome
    for nome in ("pinned_post", "OpinionView", "Pinterest"):
        entrada = await _toque_unico(harness, _tecla(rid=f"com.pocqa.messenger:id/{nome}"))
        assert entrada["target"]["resource_id"].endswith(nome) and entrada["x"] == 100, nome


async def test_tecla_telefonica_so_com_as_letras_daquele_digito(harness: Harness) -> None:
    for rotulo in ("5G", "2FA", "4K", "3D", "1A", "5 ABC"):          # não são teclas: seguem gravados
        entrada = await _toque_unico(harness, _tecla(text=rotulo, rid="com.pocqa.messenger:id/chip"))
        assert entrada["x"] == 100 and entrada["sensitive"] is False and entrada["target"]["resource_id"], rotulo
    for rotulo in ("5 JKL", "7 PQRS", "9,WXYZ", "0 +", "2ABC"):
        assert _nao_gravado(await _toque_unico(harness, _tecla(text=rotulo, rid="com.pocqa.messenger:id/chip"))), rotulo
