"""29.79: imagem realista gerada por IA publicada pela automação SEMPRE com o rótulo de IA do Instagram (regra do dono,
03/10). A central grava `rotulo_ia` na etapa pela origem da imagem (o planejador não decide), a chave da aprovação o
inclui, a prévia e a aprovação o dizem, e o executor não toca no Share sem o interruptor ligado na tela. E a imagem de
OUTRA persona não se aprova nem fecha chave. Tudo sem aparelho: hierarquia falsa e banco do harness."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.automation.hierarchy import UiElement, UiTree
from app.planning.capabilities import Capability, CapabilityCatalog, capability_of
from app.porta_do_plano import previa_da_porta
from app.social.chave_da_aprovacao import (chave_da_aprovacao, imagem_de_outra_persona, midia_da_etapa,
                                           rotulo_ia_da_imagem, rotulo_ia_exigido)
from app.taskqueue.executor import interruptor_ligado, rejeicao_do_interruptor

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
        texto: str = "") -> UiElement:
    return UiElement(id=i, text=texto, desc="", resource_id="", class_name="android.view.View", package=IG,
                     bounds=(x[0], y[0], x[1], y[1]), clickable=clicavel, enabled=True, focused=False,
                     scrollable=False, editable=False, checked=checked, password=False)


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
