"""31.97: o arraste (`swipe`) segue a regra do toque (31.94) pela ORIGEM. O padrão de bloqueio se desenha arrastando:
origem em teclado numérico, em vista de padrão de bloqueio ou em tela sensível sai sem as quatro coordenadas e marcada;
a rolagem comum numa lista segue com tudo. Os leitores (linha do modelo, generalizador, API, receita) aguentam o arraste
não gravado sem inventar um gesto."""
from __future__ import annotations

from app.automation.hierarchy import UiElement, UiTree
from app.models import ControlOwner
from app.training.recorder import _DIGITO_POR_EXTENSO, _rotulo_de_tecla

from .conftest import Harness

PACOTE = "com.pocqa.messenger"
TELA = (0, 0, 1000, 2000)


def _el(i: str, *, text: str = "", desc: str = "", rid: str = "", bounds=TELA, classe: str = "android.widget.TextView",
        clickable: bool = False, scrollable: bool = False) -> UiElement:
    return UiElement(id=i, text=text, desc=desc, resource_id=rid, class_name=classe, package=PACOTE, bounds=bounds,
                     clickable=clickable, enabled=True, focused=False, scrollable=scrollable, editable=False,
                     checked=False, password=False)


def _tela(*elementos: UiElement, sensivel: bool = False) -> UiTree:
    return UiTree(elements=list(elementos), packages=[PACOTE], sensitive=sensivel)


async def _sessao(harness: Harness):
    st = harness.state
    rt = st.devices.get("android-01")
    sid = st.training.active_for("android-01")
    if sid is None:
        await harness.wait(lambda: rt.state.value == "online", what="aparelho online")
        status, lease = st.devices.request_control(rt)
        assert status == "granted" and rt.control == ControlOwner.user
        sid = st.training.start("android-01", intent="Mandar mensagem", lease_id=lease)["id"]
    return st, rt, sid


async def _arrastar(harness: Harness, tela: UiTree | None, *, de=(100, 1500), para=(100, 500)) -> dict:
    st, rt, sid = await _sessao(harness)
    st.training.record(rt, {"type": "swipe", "x": de[0], "y": de[1], "x2": para[0], "y2": para[1]}, tela)
    return st.training.get(sid)["inputs"][-1]


def _nao_gravado(e: dict) -> bool:
    return (e["x"] is None and e["y"] is None and e["x2"] is None and e["y2"] is None and e["target"] is None
            and e["sensitive"] is True)


def _gravado(e: dict, de=(100, 1500), para=(100, 500)) -> bool:
    return (e["x"], e["y"], e["x2"], e["y2"]) == (*de, *para) and e["sensitive"] is False


# ---- (a) vista de padrão de bloqueio -----------------------------------------------------------------------------------
async def test_arraste_com_origem_na_vista_do_padrao_de_bloqueio_sai_sem_coordenadas_e_marcado(harness: Harness) -> None:
    for rid, classe in (("com.android.systemui:id/lockPatternView", "android.view.View"),
                        ("com.pocqa.messenger:id/pattern_lock", "android.view.View"),
                        ("com.pocqa.messenger:id/vista", "com.pocqa.ui.LockPatternView")):
        pad = _el("pad", rid=rid, classe=classe, bounds=(0, 800, 1000, 1800))
        assert _nao_gravado(await _arrastar(harness, _tela(_el("raiz"), pad), de=(100, 1000), para=(900, 1700))), rid


# ---- (b) teclado numérico ----------------------------------------------------------------------------------------------
async def test_arraste_com_origem_em_tecla_ou_dentro_do_teclado_numerico_sai_sem_coordenadas(harness: Harness) -> None:
    tecla = _el("t4", text="4", rid="com.android.systemui:id/key4", bounds=(0, 1000, 300, 1200), clickable=True)
    assert _nao_gravado(await _arrastar(harness, _tela(_el("raiz"), tecla), de=(100, 1100), para=(800, 1100)))
    # a tecla com rótulo comum, mas dentro do contêiner do teclado: a origem está no teclado
    pad = _el("pad", rid="com.pocqa.messenger:id/pin_pad", bounds=(0, 900, 1000, 1900))
    botao = _el("b", text="Entrar", rid="com.pocqa.messenger:id/entrar", bounds=(0, 1000, 300, 1200), clickable=True)
    assert _nao_gravado(await _arrastar(harness, _tela(_el("raiz"), pad, botao), de=(100, 1100), para=(800, 1100)))


# ---- (c) tela sensível -------------------------------------------------------------------------------------------------
async def test_arraste_em_tela_sensivel_sai_sem_coordenadas_e_marcado(harness: Harness) -> None:
    lista = _el("lista", rid="com.pocqa.messenger:id/lista", scrollable=True)
    assert _nao_gravado(await _arrastar(harness, _tela(lista, sensivel=True)))


# ---- (d) a rolagem comum NÃO perde as coordenadas (regressão do ensino) -------------------------------------------------
async def test_rolagem_comum_de_uma_lista_segue_com_as_quatro_coordenadas(harness: Harness) -> None:
    lista = _el("lista", rid="com.pocqa.messenger:id/lista", scrollable=True)
    assert _gravado(await _arrastar(harness, _tela(lista)))
    # origem sem seletor utilizável numa tela comum (contêiner sem id nem rótulo): ainda é rolagem
    anonima = _el("anonima", classe="io.flutter.embedding.android.FlutterView")
    assert _gravado(await _arrastar(harness, _tela(anonima)))
    # origem fora de qualquer elemento
    assert _gravado(await _arrastar(harness, _tela(_el("canto", bounds=(0, 0, 50, 50)))))
    # nomes parecidos com teclado não derrubam a coordenada
    for rid in ("com.pocqa.messenger:id/pinned_posts", "com.pocqa.messenger:id/spinner_cidade"):
        assert _gravado(await _arrastar(harness, _tela(_el("l", rid=rid, scrollable=True)))), rid


async def test_arraste_sem_arvore_segue_o_toque_e_perde_a_coordenada_sem_marca(harness: Harness) -> None:
    e = await _arrastar(harness, None)
    assert (e["x"], e["y"], e["x2"], e["y2"]) == (None, None, None, None) and e["sensitive"] is False


# ---- (e) os leitores ---------------------------------------------------------------------------------------------------
async def test_os_leitores_aguentam_o_arraste_nao_gravado_sem_inventar_gesto(harness: Harness) -> None:
    from app.modules.skills.domain.teaching import RecordedInput
    from app.planning.training import TrainingRequest, linha_da_entrada, proposta_simulada
    from app.taskqueue.recipes import distill_training
    from app.training.generalizer import _entrada_legada

    pad = _el("pad", rid="com.android.systemui:id/lockPatternView", bounds=(0, 800, 1000, 1800))
    nao = await _arrastar(harness, _tela(pad), de=(100, 1000), para=(900, 1700))
    assert _nao_gravado(nao)
    # a API (`inputs()`) devolve a entrada com as coordenadas nulas, sem quebrar
    st, _, sid = await _sessao(harness)
    api = st.training.inputs(sid)[-1]
    assert api["type"] == "swipe" and api["x"] is None and api["x2"] is None and api["sensitive"] is True
    # a linha do modelo: entrada não gravada, nunca "rolou" nem "None"
    linha = linha_da_entrada(nao)
    assert "arraste em teclado, padrão de bloqueio ou tela sensível (não gravado)" in linha, linha
    assert "rolou" not in linha and "None" not in linha, linha
    # o generalizador: a entrada legada carrega os nulos, e a linha continua a mesma
    gravada = RecordedInput(seq=nao["seq"], type="swipe", package=PACOTE, x=None, y=None, x2=None, y2=None, sensitive=True)
    legada = _entrada_legada(gravada)
    assert legada["x"] is None and legada["x2"] is None and legada["y2"] is None
    assert "não gravado" in linha_da_entrada(legada) and "rolou" not in linha_da_entrada(legada)
    # a proposta simulada descarta o arraste com o motivo, em vez de juntá-lo a uma etapa
    req = TrainingRequest(intent="Entrar", app_id=None, apps=[], inputs=[legada])
    proposta = proposta_simulada(req)
    assert any(d["seq"] == nao["seq"] and "arraste não gravado" in d["why"] for d in proposta["discarded"]), proposta
    # a receita/repetição recusa a etapa (nada de "rolar para cima" por padrão) ...
    toque = {"seq": 9, "type": "tap", "target": {"resource_id": f"{PACOTE}:id/enviar", "unique": ["rid"]}, "x": 1, "y": 2}
    receita, motivo = distill_training([nao, toque], {}, side_effect=False)
    assert receita is None and "arraste não gravado" in motivo, motivo
    # ... e a rolagem comum gravada continua virando a rolagem da receita (controle)
    comum = {"seq": 1, "type": "swipe", "x": 100, "y": 1500, "x2": 100, "y2": 500, "target": None, "sensitive": False}
    receita, motivo = distill_training([comum, toque], {}, side_effect=False)
    assert receita is not None and receita[0]["scroll"]["direction"] == "down", (receita, motivo)
    # entrada com coordenada: a linha segue como antes
    assert linha_da_entrada(comum) == "#1 swipe | rolou para baixo"


# ---- (f) dígito por extenso só dentro de teclado -----------------------------------------------------------------------
def test_rotulo_por_extenso_so_vale_dentro_de_teclado() -> None:
    for palavra in ("um", "Um", "ZERO", "dois", "três", "nove", "one", "Five", "nine"):
        assert _rotulo_de_tecla(palavra, True) is True, palavra
        assert _rotulo_de_tecla(palavra) is False, palavra            # fora de teclado: botão comum
    for palavra in ("ok", "um dia", "onetime", "dez", "Cancelar"):
        assert _rotulo_de_tecla(palavra, True) is False, palavra
    assert "um" in _DIGITO_POR_EXTENSO and len(_DIGITO_POR_EXTENSO) >= 18
    assert _rotulo_de_tecla("4") is True and _rotulo_de_tecla("2 ABC") is True       # as regras do 31.94 seguem


async def test_tecla_por_extenso_dentro_do_teclado_nao_grava_e_botao_um_fora_segue_gravado(harness: Harness) -> None:
    st, rt, sid = await _sessao(harness)
    pad = _el("pad", rid="com.pocqa.messenger:id/pin_pad", bounds=(0, 900, 1000, 1900))
    um = _el("um", text="Um", rid="com.pocqa.messenger:id/tecla", bounds=(0, 1000, 300, 1200), clickable=True)
    st.training.record(rt, {"type": "tap", "x": 100, "y": 1100}, _tela(_el("raiz"), pad, um))
    dentro = st.training.get(sid)["inputs"][-1]
    assert dentro["target"] is None and dentro["x"] is None and dentro["sensitive"] is True
    fora_do_teclado = _el("um", text="Um", rid="com.pocqa.messenger:id/plano_um", bounds=(0, 1000, 300, 1200), clickable=True)
    st.training.record(rt, {"type": "tap", "x": 100, "y": 1100}, _tela(_el("raiz"), fora_do_teclado))
    fora = st.training.get(sid)["inputs"][-1]
    assert fora["target"]["text"] == "Um" and fora["x"] == 100 and fora["sensitive"] is False
