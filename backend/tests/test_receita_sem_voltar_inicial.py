"""31.230: a receita sem o "voltar" inicial, ancorada no estado conhecido do app.

Na onda 1 (07/10), a IA começou o `open_profile` por voltar (`press_back`), que a receita não reproduz, e nenhuma
receita nasceu. Agora o destilador descarta o prefixo de `press_back` de recuperação quando a 1ª ação gravada partiu do
estado conhecido DECLARADO do app (`telas.yaml`, `estado_conhecido`: no Instagram, `feed` e `profile`), anotado pelo
executor antes da decisão. Essa ação leva a âncora, e a reprodução confere a tela antes de agir.

O que estes testes protegem:
* `press_back` + toque + digitação vira receita sem o voltar, com a âncora na 1ª ação;
* sem a anotação (app sem estado conhecido), com a 1ª ação fora do estado conhecido, ou com `press_back` no MEIO do
  caminho, nada nasce (como antes);
* a reprodução da receita ancorada só age com a tela no estado conhecido: fora dele, ou sem quem confira, é "alvo
  ausente" (não se aplicou; a IA assume), nunca às cegas;
* o executor reconhece o estado conhecido pelo `telas.yaml` real do Instagram (feed sim; publicação aberta não; app sem
  conhecimento: não se sabe).

Nível de prova: `simulated` (árvores sintéticas; nenhuma IA; nenhum aparelho real).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.automation.hierarchy import parse_hierarchy
from app.taskqueue.recipes import ANCORA_ESTADO_CONHECIDO, AlvoAusente, Replayer, distill, unique_selectors

from .conftest import Harness

IG = "com.instagram.android"
FEED = ('<hierarchy>'
        f'<node package="{IG}" class="android.widget.FrameLayout" resource-id="{IG}:id/tab_bar" bounds="[0,1500][1080,1600]"/>'
        f'<node package="{IG}" class="android.widget.FrameLayout" resource-id="{IG}:id/search_tab" content-desc="Pesquisar"'
        ' clickable="true" bounds="[216,1500][432,1600]"/>'
        f'<node package="{IG}" class="android.widget.FrameLayout" resource-id="{IG}:id/feed_timeline" bounds="[0,0][1080,1500]"/>'
        '</hierarchy>')
POST = ('<hierarchy>'
        f'<node package="{IG}" class="android.widget.ImageView" resource-id="{IG}:id/row_feed_button_like"'
        ' clickable="true" bounds="[0,900][100,1000]"/>'
        '</hierarchy>')
VARIAVEIS = {"username": "pagina.publica"}


def _linhas(*, voltar_no_meio: bool = False) -> list[dict[str, Any]]:
    tree = parse_hierarchy(FEED)
    aba = next(e for e in tree.elements if e.resource_id.endswith("search_tab"))
    alvo = {**aba.to_dict(), "unique": unique_selectors(tree, aba)}
    base = {"status": "done", "source": "ai", "side_effect": 0, "rationale": "r", "target": None}
    voltar = {**base, "id": 1, "tool": "press_back", "args": "{}"}
    toque = {**base, "id": 2, "tool": "tap", "args": '{"element_id":"e1"}', "target": json.dumps(alvo)}
    digitar = {**base, "id": 3, "tool": "type_text", "args": json.dumps({"text": "pagina.publica", "element_id": None})}
    return [toque, {**voltar, "id": 5}, digitar] if voltar_no_meio else [voltar, toque, digitar]


def test_o_voltar_inicial_sai_e_a_primeira_acao_leva_a_ancora() -> None:
    acoes, motivo = distill(_linhas(), VARIAVEIS, em_casa_antes={2: True, 3: False})    # type: ignore[arg-type]
    assert acoes is not None, motivo
    assert [a["tool"] for a in acoes] == ["tap", "type_text"]
    assert acoes[0]["ancora"] == ANCORA_ESTADO_CONHECIDO and "ancora" not in acoes[1]
    assert acoes[1]["args"]["text"] == "{username}"


@pytest.mark.parametrize(("em_casa", "voltar_no_meio", "trecho"), [
    (None, False, "press_back depende"),                    # sem anotação (app sem estado conhecido): como antes
    ({2: False}, False, "não terminou no estado conhecido"),
    ({2: True}, True, "press_back depende"),                # o voltar no meio do caminho segue recusado
])
def test_sem_ancora_nada_nasce(em_casa: dict[int, bool] | None, voltar_no_meio: bool, trecho: str) -> None:
    acoes, motivo = distill(_linhas(voltar_no_meio=voltar_no_meio), VARIAVEIS,      # type: ignore[arg-type]
                            em_casa_antes=em_casa)
    assert acoes is None and trecho in motivo


def test_a_reproducao_confere_a_ancora_antes_de_agir() -> None:
    acoes, _ = distill(_linhas(), VARIAVEIS, em_casa_antes={2: True})                # type: ignore[arg-type]
    assert acoes is not None
    feed = parse_hierarchy(FEED)
    for conferidor in (None, lambda t: False):
        rep = Replayer(recipe_id=1, version=1, actions=acoes, variables=VARIAVEIS, em_casa=conferidor)
        with pytest.raises(AlvoAusente, match="estado conhecido"):
            rep.next(feed)
        assert rep.done_actions == 0
    rep = Replayer(recipe_id=1, version=1, actions=acoes, variables=VARIAVEIS, em_casa=lambda t: True)
    decisao = rep.next(feed)
    assert decisao is not None and decisao.tool == "tap" and decisao.args["element_id"]


def test_o_executor_reconhece_o_estado_conhecido_do_instagram(harness: Harness) -> None:
    executor = harness.state.scheduler.executor
    assert executor._em_casa(IG, parse_hierarchy(FEED)) is True                        # noqa: SLF001
    assert executor._em_casa(IG, parse_hierarchy(POST)) is False                       # noqa: SLF001
    assert executor._em_casa("com.app.sem.conhecimento", parse_hierarchy(FEED)) is None  # noqa: SLF001
