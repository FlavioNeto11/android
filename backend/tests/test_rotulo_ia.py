"""29.79: imagem realista gerada por IA publicada pela automação SEMPRE com o rótulo de IA do Instagram (regra do dono,
03/10). A central grava `rotulo_ia` na etapa pela origem da imagem (o planejador não decide), a chave da aprovação o
inclui, a prévia e a aprovação o dizem, e o executor não toca no Share sem o interruptor ligado na tela. E a imagem de
OUTRA persona não se aprova nem fecha chave. Tudo sem aparelho: hierarquia falsa e banco do harness."""
from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from app.automation.hierarchy import UiElement, UiTree, parse_hierarchy
from app.planning.capabilities import Capability, CapabilityCatalog, capability_of
from app.porta_do_plano import previa_da_porta
from app.social.chave_da_aprovacao import (chave_da_aprovacao, imagem_de_outra_persona, midia_da_etapa,
                                           rotulo_ia_da_imagem, rotulo_ia_exigido)
from app.taskqueue.executor import (interruptor_ligado, marca_junto_da_conta, marcas_exigidas,
                                    rejeicao_do_interruptor)

from .test_capabilities import IG
from .test_porta_do_plano import _plano, _por_chave

SWITCH = ("rotulo_ia:text==Add AI label",)


def _el(i: int, texto: str, y: tuple[int, int], *, checked: bool = False, classe: str = "android.widget.TextView") -> UiElement:
    return UiElement(id=f"e{i}", text=texto, desc="", resource_id="", class_name=classe, package=IG,
                     bounds=(0, y[0], 1080, y[1]), clickable=True, enabled=True, focused=False, scrollable=False,
                     editable=False, checked=checked, password=False)


def _tela(ligado: bool, *, mesma_linha: bool = True) -> UiTree:
    interruptor_y = (900, 960) if mesma_linha else (1400, 1460)
    return UiTree(elements=[_no("e1", (104, 282), (900, 960), clicavel=False, texto="Add AI label"),
                            _no("e2", (584, 688), interruptor_y, checked=ligado),
                            _el(3, "Share", (100, 160))], packages=[IG], sensitive=False)


def test_o_interruptor_do_rotulo_de_ia_tem_de_estar_ligado_na_mesma_linha() -> None:
    pede = {"rotulo_ia": "true"}
    assert rejeicao_do_interruptor(SWITCH, pede, _tela(True)) is None
    motivo = rejeicao_do_interruptor(SWITCH, pede, _tela(False))
    assert motivo is not None and "Add AI label" in motivo and "LIGADO" in motivo
    # Ligado, mas em OUTRA linha (outro interruptor da tela): não conta.
    assert rejeicao_do_interruptor(SWITCH, pede, _tela(True, mesma_linha=False)) is not None
    # Sem o rótulo na tela: recusa (o texto do interruptor ainda é NÃO MEDIDO; falha fechado).
    vazia = UiTree(elements=[_el(3, "Share", (100, 160))], packages=[IG], sensitive=False)
    assert rejeicao_do_interruptor(SWITCH, pede, vazia) is not None
    # O próprio elemento marcado também vale.
    assert interruptor_ligado(UiTree(elements=[_el(1, "Add AI label", (0, 50), checked=True)], packages=[IG],
                                     sensitive=False), "text==Add AI label")


def test_a_tela_medida_no_8_3_com_o_rotulo_ligado_e_desligado() -> None:
    """As duas capturas reais da tela da legenda (android-01, 03/10 04:43Z), reduzidas ao que a guarda lê: o texto
    "Add AI label" (104,505)-(282,543) e o interruptor `android.view.View` (584,495)-(688,591), cujo centro (543) cai na
    borda de baixo do texto."""
    def tela(ligado: bool) -> UiTree:
        texto = UiElement(id="e26", text="Add AI label", desc="", resource_id="", class_name="android.widget.TextView",
                          package=IG, bounds=(104, 505, 282, 543), clickable=False, enabled=True, focused=False,
                          scrollable=False, editable=False, checked=False, password=False)
        chave = UiElement(id="e29", text="", desc="", resource_id="", class_name="android.view.View", package=IG,
                          bounds=(584, 495, 688, 591), clickable=True, enabled=True, focused=False, scrollable=False,
                          editable=False, checked=ligado, password=False)
        return UiTree(elements=[texto, chave], packages=[IG], sensitive=False)
    assert rejeicao_do_interruptor(SWITCH, {"rotulo_ia": "true"}, tela(True)) is None
    assert rejeicao_do_interruptor(SWITCH, {"rotulo_ia": "true"}, tela(False)) is not None


def _no(i: str, x: tuple[int, int], y: tuple[int, int], *, checked: bool = False, clicavel: bool = True,
        texto: str = "", marcavel: bool = False) -> UiElement:
    return UiElement(id=i, text=texto, desc="", resource_id="", class_name="android.view.View", package=IG,
                     bounds=(x[0], y[0], x[1], y[1]), clickable=clicavel, enabled=True, focused=False,
                     scrollable=False, editable=False, checked=checked, password=False, checkable=marcavel)


def test_o_interruptor_nao_clicavel_da_linha_clicavel_ainda_e_o_candidato() -> None:
    """Revisão C1: a LINHA inteira recebe o toque e o interruptor dela é `clickable=false`, `checkable=true` e
    desligado; o interruptor LIGADO da linha vizinha encosta na faixa do texto. Sem o `checkable`, o vizinho era o único
    candidato e o Share saía sem rótulo. Agora o dele (mais perto) decide: desligado, e o Share é recusado."""
    texto = _no("t", (104, 282), (505, 543), clicavel=False, texto="Add AI label")
    linha = _no("linha", (0, 720), (480, 600))                                   # começa à esquerda: não é candidato
    o_dele = _no("dele", (584, 688), (495, 591), clicavel=False, marcavel=True)  # centro 543 (o do texto: 524)
    vizinho_ligado = _no("vizinho", (584, 688), (420, 510), checked=True)        # centro 465, encosta na faixa
    tela = UiTree(elements=[texto, linha, o_dele, vizinho_ligado], packages=[IG], sensitive=False)
    assert not interruptor_ligado(tela, "text==Add AI label")
    assert rejeicao_do_interruptor(SWITCH, {"rotulo_ia": "true"}, tela) is not None
    ligado = UiTree(elements=[texto, linha, _no("dele", (584, 688), (495, 591), clicavel=False, marcavel=True,
                                                checked=True), vizinho_ligado], packages=[IG], sensitive=False)
    assert interruptor_ligado(ligado, "text==Add AI label")


def test_o_vizinho_que_so_encosta_nao_e_candidato() -> None:
    """Revisão C1b: o interruptor da linha nem clicável nem marcável (o uiautomator não o expõe) e desligado; o LIGADO
    da linha vizinha encosta na faixa do texto. Com o centro dele fora da faixa alargada em meia altura, não é
    candidato: sem candidato, desligado, e o Share é recusado."""
    texto = _no("t", (104, 282), (505, 543), clicavel=False, texto="Add AI label")
    vizinho_ligado = _no("vizinho", (584, 688), (420, 510), checked=True)        # centro 465; a faixa é 486..562
    tela = UiTree(elements=[texto, _no("dele", (584, 688), (495, 591), clicavel=False), vizinho_ligado],
                  packages=[IG], sensitive=False)
    assert not interruptor_ligado(tela, "text==Add AI label")
    assert rejeicao_do_interruptor(SWITCH, {"rotulo_ia": "true"}, tela) is not None
    medido = UiTree(elements=[texto, _no("sw", (584, 688), (495, 591), checked=True)], packages=[IG], sensitive=False)
    assert interruptor_ligado(medido, "text==Add AI label")                       # o 8.3: centro 543, dentro


def test_o_empate_diz_interruptor_ambiguo() -> None:
    """Interruptor dentro de um contêiner também à direita: empate, falha fechado, e o motivo diz "ambíguo" para quem
    atende o `waiting_user` (não "ligue o interruptor", que a pessoa veria ligado)."""
    texto = _no("t", (104, 282), (505, 543), clicavel=False, texto="Add AI label")
    tela = UiTree(elements=[texto, _no("caixa", (560, 710), (490, 596)),
                            _no("sw", (584, 688), (495, 591), checked=True)], packages=[IG], sensitive=False)
    motivo = rejeicao_do_interruptor(SWITCH, {"rotulo_ia": "true"}, tela)
    assert motivo is not None and motivo.startswith("interruptor ambíguo")
    desligado = UiTree(elements=[texto, _no("sw", (584, 688), (495, 591))], packages=[IG], sensitive=False)
    assert "LIGADO" in (rejeicao_do_interruptor(SWITCH, {"rotulo_ia": "true"}, desligado) or "")


def test_o_parser_le_o_checkable() -> None:
    """Revisão C1: o `checkable` do uiautomator chega ao `UiElement` (e só aparece no dicionário quando é verdadeiro)."""
    xml = ('<hierarchy><node class="android.widget.FrameLayout" package="com.instagram.android" bounds="[0,0][720,1280]">'
           '<node class="android.view.View" package="com.instagram.android" text="" resource-id="" clickable="false" '
           'checkable="true" checked="false" enabled="true" bounds="[584,495][688,591]"/>'
           '<node class="android.widget.TextView" package="com.instagram.android" text="Add AI label" clickable="false" '
           'enabled="true" bounds="[104,505][282,543]"/></node></hierarchy>')
    arvore = parse_hierarchy(xml)
    sw = next(e for e in arvore.elements if e.class_name == "android.view.View")
    assert sw.checkable and not sw.clickable
    assert sw.to_dict()["checkable"] is True
    rotulo = next(e for e in arvore.elements if e.text == "Add AI label")
    assert "checkable" not in rotulo.to_dict()


def test_so_um_candidato_conta_o_da_direita_mais_proximo() -> None:
    """Revisão (c): o interruptor ligado da linha de CIMA (encostando na faixa do texto, como com fonte maior) não vale
    pelo da linha do rótulo; dois candidatos quase empatados são dúvida e recusam; à esquerda do texto não conta."""
    texto = _no("t", (104, 282), (505, 543), clicavel=False, texto="Add AI label")
    de_cima_ligado = _no("cima", (584, 688), (420, 510), checked=True)       # encosta na faixa, centro em 465
    o_dele_desligado = _no("dele", (584, 688), (495, 591))                    # centro em 543 (o do texto: 524)
    tela = UiTree(elements=[texto, de_cima_ligado, o_dele_desligado], packages=[IG], sensitive=False)
    assert not interruptor_ligado(tela, "text==Add AI label")
    empatados = UiTree(elements=[texto, _no("a", (584, 688), (500, 548), checked=True), _no("b", (700, 720), (502, 550))],
                       packages=[IG], sensitive=False)
    assert not interruptor_ligado(empatados, "text==Add AI label")
    a_esquerda = UiTree(elements=[texto, _no("esq", (0, 90), (505, 543), checked=True)], packages=[IG], sensitive=False)
    assert not interruptor_ligado(a_esquerda, "text==Add AI label")


def test_sem_rotulo_pedido_a_guarda_nao_age() -> None:
    assert rejeicao_do_interruptor(SWITCH, {"rotulo_ia": "false"}, _tela(False)) is None
    assert rejeicao_do_interruptor(SWITCH, {}, _tela(False)) is None


def test_o_catalogo_do_instagram_declara_a_guarda_no_create_post() -> None:
    cap = capability_of(IG, "CREATE_POST")
    assert cap.commit_switch == SWITCH and "rotulo_ia" in cap.optional_bindings
    assert cap.commit_selector == "id=share_footer_button"          # medido no 8.3: o botão, não o TextView filho
    with pytest.raises(ValueError, match="commit_switch"):
        CapabilityCatalog("x", [Capability(key="P", title="p", goal="g", post_kind="model_judged", post_value="v",
                                           post_description="d", side_effect=True, commit_selector="text==Share",
                                           commit_switch=("nao_declarado:text==X",))])


MARCA = "id=secondary_label|text==AI info"


def _cabecalho(conta: str, y: int, *, marca: bool = True, i: str = "a") -> list[UiElement]:
    """O cabeçalho de um post no feed como o 8.3 mediu (android-01, 03/10): o nome da conta em (98,y)-(632,y+51) e, com
    rótulo, "AI info" (`secondary_label`) colado logo abaixo, em (98,y+50)-(632,y+104)."""
    nome = UiElement(id=f"{i}-nome", text=conta, desc=conta, resource_id=f"{IG}:id/row_feed_photo_profile_name",
                     class_name="android.widget.Button", package=IG, bounds=(98, y, 632, y + 51), clickable=True,
                     enabled=True, focused=False, scrollable=False, editable=False, checked=False, password=False)
    rotulo = UiElement(id=f"{i}-ai", text="AI info", desc="AI info", resource_id=f"{IG}:id/secondary_label",
                       class_name="android.widget.Button", package=IG, bounds=(98, y + 50, 632, y + 104),
                       clickable=False, enabled=True, focused=False, scrollable=False, editable=False, checked=False,
                       password=False)
    return [nome, rotulo] if marca else [nome]


def test_a_marca_de_ia_so_conta_colada_no_nome_da_nossa_conta() -> None:
    """29.79 (d): "AI info" logo abaixo do nome da conta da etapa prova o rótulo; a de OUTRO perfil do feed não prova, e
    sem a conta conhecida é dúvida."""
    medida = UiTree(elements=_cabecalho("tadeu.quintela4821", 395), packages=[IG], sensitive=False)
    assert marca_junto_da_conta(medida, MARCA, "tadeu.quintela4821")
    assert marca_junto_da_conta(medida, MARCA, "@tadeu.quintela4821")              # a arroba é notação nossa
    assert not marca_junto_da_conta(medida, MARCA, "outra.conta")
    assert not marca_junto_da_conta(medida, MARCA, None)
    # o nosso post sem rótulo e, mais abaixo no feed, o post de IA de outro perfil
    feed = UiTree(elements=[*_cabecalho("tadeu.quintela4821", 395, marca=False),
                            *_cabecalho("outra.conta", 1200, i="b")], packages=[IG], sensitive=False)
    assert not marca_junto_da_conta(feed, MARCA, "tadeu.quintela4821")
    # "AI info" solto longe do nome (outro lugar da tela) não é a marca do nosso post
    longe = UiTree(elements=[_cabecalho("tadeu.quintela4821", 395, marca=False)[0],
                             _cabecalho("x", 900, i="c")[1]], packages=[IG], sensitive=False)
    assert not marca_junto_da_conta(longe, MARCA, "tadeu.quintela4821")
    # Revisão D2: o post NOVO (no topo) sem marca e um ANTIGO nosso, mais abaixo, com marca: não confirma
    antigo = UiTree(elements=[*_cabecalho("tadeu.quintela4821", 395, marca=False),
                              *_cabecalho("tadeu.quintela4821", 1000, i="velho")], packages=[IG], sensitive=False)
    assert not marca_junto_da_conta(antigo, MARCA, "tadeu.quintela4821")
    # ... e mesmo com o cabeçalho do topo de outro tipo (sem o `resource_id` do antigo): o nome mais alto é que decide
    sem_rid = [dataclasses.replace(e, resource_id="") for e in _cabecalho("tadeu.quintela4821", 395, marca=False)]
    antigo2 = UiTree(elements=[*sem_rid, *_cabecalho("tadeu.quintela4821", 1000, i="velho")], packages=[IG],
                     sensitive=False)
    assert not marca_junto_da_conta(antigo2, MARCA, "tadeu.quintela4821")
    # o cartão do topo é de outro perfil (o post novo não está onde devia) e o nosso, com marca, vem abaixo: dúvida
    outro_no_topo = UiTree(elements=[*_cabecalho("outra.conta", 395, marca=False, i="b"),
                                     *_cabecalho("tadeu.quintela4821", 1000)], packages=[IG], sensitive=False)
    assert not marca_junto_da_conta(outro_no_topo, MARCA, "tadeu.quintela4821")
    # o texto "AI info" sem o `secondary_label` medido: dúvida, não conta
    sem_id = UiTree(elements=[_cabecalho("tadeu.quintela4821", 395, marca=False)[0],
                              dataclasses.replace(_cabecalho("tadeu.quintela4821", 395)[1], resource_id="")],
                    packages=[IG], sensitive=False)
    assert not marca_junto_da_conta(sem_id, MARCA, "tadeu.quintela4821")


def test_a_marca_so_e_exigida_com_o_argumento_true() -> None:
    marcas = ("rotulo_ia:" + MARCA,)
    assert marcas_exigidas(marcas, {"rotulo_ia": "true"}) == [MARCA]
    assert marcas_exigidas(marcas, {"rotulo_ia": "false"}) == []
    assert marcas_exigidas(marcas, {}) == []
    cap = capability_of(IG, "CREATE_POST")
    assert cap.commit_switch_mark == ("rotulo_ia:" + MARCA,)
    with pytest.raises(ValueError, match="commit_switch_mark exige commit_switch"):
        CapabilityCatalog("x", [Capability(key="P", title="p", goal="g", post_kind="model_judged", post_value="v",
                                           post_description="d", side_effect=True, commit_selector="text==Share",
                                           optional_bindings=("rotulo_ia",),
                                           commit_switch_mark=("rotulo_ia:text==AI info",))])


def _imagem(state: Any, iid: str, persona: str, source: str = "generated", sha: str = "c" * 64) -> None:
    state.db.execute("INSERT INTO persona_images(id, persona_id, source, status, is_primary, created_at, bytes_sha256)"
                     " VALUES (?, ?, ?, 'ready', 0, '2026-10-04T10:00:00Z', ?)", (iid, persona, source, sha))


def _outro_perfil(state: Any) -> str:
    """Um perfil de outra persona, em outro aparelho (a FK de `persona_images` exige o perfil)."""
    return _plano(state, [], aparelho="android-02", run_id="run-q")


async def test_em_duvida_o_share_exige_o_rotulo(harness: Any) -> None:
    """Revisão R1: o que a guarda do Share lê vem da ORIGEM da imagem resolvida. Etapa sem o argumento (criada antes do
    29.79, ou com a imagem resolvida depois por `resolver_saidas`), imagem inexistente, por resolver ou ausente: exige.
    Só o upload conhecido dispensa — e nem ele se a etapa gravou "true"; "false" forjado em imagem gerada não dispensa."""
    state = harness.state
    p1 = _outro_perfil(state)
    _imagem(state, "gerada", p1, "generated")
    _imagem(state, "enviada", p1, "upload")
    assert rotulo_ia_exigido(state.db, {"image_id": "gerada"})                         # etapa antiga / resolvida depois
    assert rotulo_ia_exigido(state.db, {"image_id": "gerada", "rotulo_ia": "false"})   # forjado
    assert rotulo_ia_exigido(state.db, {"image_id": "nao-existe"})
    assert rotulo_ia_exigido(state.db, {"image_id": "{{saida:img}}"})
    assert rotulo_ia_exigido(state.db, {})
    assert not rotulo_ia_exigido(state.db, {"image_id": "enviada"})
    assert rotulo_ia_exigido(state.db, {"image_id": "enviada", "rotulo_ia": "true"})


async def test_etapa_com_imagem_e_sem_o_rotulo_gravado_nao_fecha_chave(harness: Any) -> None:
    """Revisão R1, na porta: imagem literal, pronta e da persona, mas a etapa sem `rotulo_ia` (criada antes do 29.79):
    sem chave, o item fica para a execução — o sim do plano não cobre publicação cujo rótulo ninguém decidiu."""
    state = harness.state
    pid = _plano(state, [{"key": "antiga", "cap": "CREATE_POST",
                          "bindings": {"image_id": "img-a", "content": "praia", "content_verbatim": "true"}}])
    _imagem(state, "img-a", pid)
    item = _por_chave(previa_da_porta(state, "run-p"))["antiga"]
    assert item["chave"] is None and item["selo"] == "na_execucao" and item["rotulo_ia"] is None
    cap = capability_of(IG, "CREATE_POST")
    tem, sha = midia_da_etapa(state.db, {"image_id": "img-a"}, perfil=pid)
    assert tem and sha
    assert chave_da_aprovacao({"image_id": "img-a", "content": "praia", "content_verbatim": "true"}, cap, perfil=pid,
                              aparelho="android-01", pacote=IG, run_id="r", objective_id="o", tem_imagem=tem,
                              midia_sha256=sha) is None


async def test_a_origem_da_imagem_decide_o_rotulo(harness: Any) -> None:
    state = harness.state
    p1 = _outro_perfil(state)
    _imagem(state, "g", p1, "generated")
    _imagem(state, "l", p1, "imported_legacy")
    _imagem(state, "u", p1, "upload")
    assert [rotulo_ia_da_imagem(state.db, i) for i in ("g", "l", "u", "nao-existe")] == ["true", "true", "false", None]


async def test_a_central_grava_o_rotulo_na_etapa_por_cima_do_plano(harness: Any) -> None:
    state = harness.state
    _imagem(state, "img-g", _outro_perfil(state), "generated")
    repo = state.repo
    assert repo._com_rotulo_ia({"image_id": "img-g", "rotulo_ia": "false"})["rotulo_ia"] == "true"  # noqa: SLF001
    assert "rotulo_ia" not in repo._com_rotulo_ia({"image_id": "{{saida:x}}", "rotulo_ia": "false"})  # noqa: SLF001
    assert repo._com_rotulo_ia({"content": "oi"}) == {"content": "oi"}  # noqa: SLF001


async def test_publicar_sem_o_rotulo_e_outro_item_na_chave(harness: Any) -> None:
    state = harness.state
    p1 = _outro_perfil(state)
    _imagem(state, "img-1", p1)
    cap = capability_of(IG, "CREATE_POST")
    base = {"image_id": "img-1", "content": "praia", "content_verbatim": "true"}
    tem, sha = midia_da_etapa(state.db, base, perfil=p1)
    args = dict(perfil=p1, aparelho="android-01", pacote=IG, run_id="r", objective_id="o", tem_imagem=tem,
                midia_sha256=sha)
    com = chave_da_aprovacao({**base, "rotulo_ia": "true"}, cap, **args)
    sem = chave_da_aprovacao({**base, "rotulo_ia": "false"}, cap, **args)
    assert com and sem and com != sem


async def test_imagem_de_outra_persona_nao_fecha_chave_nem_se_aprova(harness: Any) -> None:
    state = harness.state
    pid = _plano(state, [{"key": "pub", "cap": "CREATE_POST",
                          "bindings": {"image_id": "img-x", "content": "praia", "content_verbatim": "true"}},
                         {"key": "minha", "cap": "CREATE_POST",
                          "bindings": {"image_id": "img-m", "content": "praia 2", "content_verbatim": "true"}}])
    _imagem(state, "img-x", _outro_perfil(state))
    _imagem(state, "img-m", pid)
    for chave in ("pub", "minha"):           # o `_plano` do teste não passa pelo materialize: grava como a central
        linha = state.db.scalar("SELECT bindings FROM steps WHERE key=?", (chave,))
        state.db.execute("UPDATE steps SET bindings=? WHERE key=?",
                         (json.dumps(state.repo._com_rotulo_ia(json.loads(linha))), chave))  # noqa: SLF001
    assert imagem_de_outra_persona(state.db, {"image_id": "img-x"}, pid)
    assert midia_da_etapa(state.db, {"image_id": "img-x"}, perfil=pid) == (True, None)
    itens = _por_chave(previa_da_porta(state, "run-p"))
    assert itens["pub"]["selo"] == "recusado" and "outra persona" in itens["pub"]["motivo"]
    assert itens["pub"]["chave"] is None and itens["pub"]["image_id"] is None
    assert itens["minha"]["selo"] == "aprovacao" and itens["minha"]["chave"]
    assert itens["minha"]["rotulo_ia"] is True and itens["minha"]["image_id"] == "img-m"


async def test_o_upload_do_dono_diz_sem_rotulo_e_a_imagem_por_resolver_nao_diz_nada(harness: Any) -> None:
    """Orquestradora, 22:36Z: o upload nasce `rotulo_ia` falso e o item diz "sem rótulo de IA"; sem o argumento gravado
    (imagem por resolver) o campo é `None`, nunca um "sem rótulo" que ninguém decidiu."""
    state = harness.state
    pid = _plano(state, [{"key": "up", "cap": "CREATE_POST",
                          "bindings": {"image_id": "img-u", "content": "praia", "content_verbatim": "true"}},
                         {"key": "solta", "cap": "CREATE_POST",
                          "bindings": {"image_id": "img-z", "content": "outra", "content_verbatim": "true"}}])
    _imagem(state, "img-u", pid, "upload")
    linha = state.db.scalar("SELECT bindings FROM steps WHERE key='up'")
    state.db.execute("UPDATE steps SET bindings=? WHERE key='up'",
                     (json.dumps(state.repo._com_rotulo_ia(json.loads(linha))),))  # noqa: SLF001
    itens = _por_chave(previa_da_porta(state, "run-p"))
    assert itens["up"]["rotulo_ia"] is False
    assert itens["solta"]["tem_imagem"] is True and itens["solta"]["rotulo_ia"] is None


async def test_o_sha_da_foto_do_canal_e_o_mesmo_da_previa_da_porta(harness: Any) -> None:
    """28.46: a foto que a Canais manda ao dono confere o sha256 lido da Central por `sha_da_imagem_na_porta`. Ele tem de
    ser, item a item, o `imagem_sha256` que a prévia da porta mostra, inclusive o `None` da imagem de outra persona."""
    from app.modules.avisos.infrastructure.portas_da_central import sha_da_imagem_na_porta

    state = harness.state
    pid = _plano(state, [{"key": "pub", "cap": "CREATE_POST",
                          "bindings": {"image_id": "img-x", "content": "praia", "content_verbatim": "true"}},
                         {"key": "minha", "cap": "CREATE_POST",
                          "bindings": {"image_id": "img-m", "content": "praia 2", "content_verbatim": "true"}}])
    _imagem(state, "img-x", _outro_perfil(state))
    _imagem(state, "img-m", pid)
    itens = _por_chave(previa_da_porta(state, "run-p"))
    for chave in ("pub", "minha"):
        assert sha_da_imagem_na_porta(state.db, "run-p", str(itens[chave]["step_id"])) == itens[chave]["imagem_sha256"]
    assert itens["minha"]["imagem_sha256"] and itens["pub"]["imagem_sha256"] is None
    assert sha_da_imagem_na_porta(state.db, "run-p", "etapa-que-nao-existe") is None


async def test_o_sha_aprovado_no_plano_e_a_ancora_da_foto(harness: Any, monkeypatch: Any) -> None:
    """28.48 (leitura do #417): depois do sim na prévia da porta, `sha_da_imagem_aprovada` devolve o `midia_sha256`
    congelado no sim, igual ao `imagem_sha256` que o dono viu. A etapa sem sim não tem âncora."""
    from app.modules.avisos.infrastructure.portas_da_central import sha_da_imagem_aprovada
    from app.porta_do_plano import AprovarPlanoBody, ItemAprovado, aprovar_plano
    from app.util import now_iso

    from .test_porta_do_plano import _sem_iniciar

    state = harness.state
    _sem_iniciar(state, monkeypatch)
    pid = _plano(state, [{"key": "minha", "cap": "CREATE_POST",
                          "bindings": {"image_id": "img-m", "content": "praia", "content_verbatim": "true"}},
                         {"key": "outra", "cap": "CREATE_POST",
                          "bindings": {"image_id": "img-n", "content": "praia 2", "content_verbatim": "true"}}])
    _imagem(state, "img-m", pid)
    _imagem(state, "img-n", pid)
    for chave in ("minha", "outra"):         # o `_plano` do teste não passa pelo materialize: grava como a central
        linha = state.db.scalar("SELECT bindings FROM steps WHERE key=?", (chave,))
        state.db.execute("UPDATE steps SET bindings=? WHERE key=?",
                         (json.dumps(state.repo._com_rotulo_ia(json.loads(linha))), chave))  # noqa: SLF001
    itens = _por_chave(previa_da_porta(state, "run-p"))
    minha, outra = itens["minha"], itens["outra"]
    assert minha["selo"] == "aprovacao" and minha["chave"] and minha["imagem_sha256"]
    assert sha_da_imagem_aprovada(state.db, "run-p", str(minha["step_id"])) is None      # antes do sim, sem âncora
    aprovar_plano(state, "run-p", AprovarPlanoBody(vista_em=now_iso(), aprovar=[
        ItemAprovado(step_id=str(minha["step_id"]), chave=str(minha["chave"]))]), por="flavio")
    assert sha_da_imagem_aprovada(state.db, "run-p", str(minha["step_id"])) == minha["imagem_sha256"]
    assert sha_da_imagem_aprovada(state.db, "run-p", str(outra["step_id"])) is None      # sem sim, sem âncora
