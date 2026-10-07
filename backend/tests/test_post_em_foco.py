"""31.260: o rascunho lê só o post EM FOCO no feed "Posts", não o cartão seguinte que aparece embaixo.

Rodada de 07/10 12:55Z (op-20261007125539-22ef67, android-06, r-20261007125539-542a75). O OPEN_POST tocou o 2º post da
grade e o feed "Posts" abriu com ele no alto e o cabeçalho do cartão seguinte à vista. `visible_content` levava as
linhas da tela inteira: o texto aprovado misturou os dois posts. O executor recusou comentar ("o post em foco não
corresponde ao texto aprovado"), duas vezes. Gastou US$ 0,357 e não publicou nada. Agora o app declara o cabeçalho de
cartão (`leitura.conteudo.cartao`), e o conteúdo é só o do cartão em foco.

A árvore de `fixtures/instagram_feed/feed_dois_cartoes.json` é a REAL do android-06, lida às 13:46:55Z de 07/10 por
`GET /api/instances/android-06/hierarchy` (só leitura, sem adb), com o feed "Posts" aberto. Os ids, classes e
coordenadas são os medidos; todo texto e descrição foi trocado por texto inventado (nenhum @, legenda ou nome real).

Nível de prova: `simulated` (funções puras sobre a árvore medida). `real`: na próxima rodada com post por posição.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.automation import leitura_de_tela
from app.automation.hierarchy import UiElement, UiTree
from app.modules.applications.infrastructure.registry import screen_reader_of

PKG = "com.instagram.android"
FIXTURE = Path(__file__).parent / "fixtures" / "instagram_feed" / "feed_dois_cartoes.json"
LEGENDA_A = "Uma legenda inventada do cartão A"
CABECALHO_B = "conta.b e mais 2 contas"
#: Os únicos textos da árvore (trocados): se aparecer outro, a fixture ganhou um dado real.
TEXTOS_INVENTADOS = {"", "1,000", "100K", "50K", "Artista · Faixa", "Follow", "Posts", "September 16 • See translation",
                     "conta.a Uma legenda inventada do cartão A, longa o bastante para contar como conteúdo… more",
                     "conta.a e mais 2 contas", CABECALHO_B, "Back", "Comment", "Imagem do cartão", "Like",
                     "More actions", "Save", "Share", "botão"}


def _arvore() -> UiTree:
    dados = json.loads(FIXTURE.read_text(encoding="utf-8"))
    campos = UiElement.__dataclass_fields__
    elementos = [UiElement(**{k: (tuple(v) if k == "bounds" else v) for k, v in e.items() if k in campos})
                 for e in dados["elements"]]
    return UiTree(elements=elementos, packages=[PKG], sensitive=False)


def _leitor() -> leitura_de_tela.LeituraDeclarada:
    leitor = screen_reader_of(PKG)
    assert isinstance(leitor, leitura_de_tela.LeituraDeclarada) and leitor.cartao == "row_feed_profile_header"
    return leitor


def test_a_fixture_so_tem_texto_inventado() -> None:
    dados = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert {e["text"] for e in dados["elements"]} | {e["desc"] for e in dados["elements"]} <= TEXTOS_INVENTADOS
    assert "@" not in FIXTURE.read_text(encoding="utf-8")


def test_o_rascunho_le_so_o_post_em_foco() -> None:
    arvore = _arvore()
    sem_cartao = leitura_de_tela.LeituraDeclarada(ignorar=_leitor().ignorar, minimo=_leitor().minimo, comentario=None,
                                                  mensagem=None)
    antes = sem_cartao.visible_content(arvore)
    assert LEGENDA_A in antes and CABECALHO_B in antes          # o defeito da rodada: os dois cartões no rascunho
    agora = _leitor().visible_content(arvore)
    assert LEGENDA_A in agora and CABECALHO_B not in agora


def test_um_cartao_so_vai_do_cabecalho_ao_fim_da_tela() -> None:
    arvore = _arvore()
    so_a = UiTree(elements=[e for e in arvore.elements if e.bounds[1] < 948], packages=[PKG], sensitive=False)
    assert LEGENDA_A in _leitor().visible_content(so_a)


def test_na_duvida_a_tela_inteira_como_antes() -> None:
    """Conteúdo ACIMA do primeiro cabeçalho: o cartão de cima rolou e o cabeçalho dele saiu da tela, e não dá para dizer
    qual é o foco. Sem cabeçalho nenhum (outra tela), também a tela inteira."""
    # A linha do cartão de cima fica entre a barra (até y=160) e o cabeçalho, que desce 60 px (captura das 13:02Z).
    base = _arvore()
    descidos = [replace(e, bounds=(e.bounds[0], e.bounds[1] + 60, e.bounds[2], e.bounds[3] + 60)) if e.bounds[1] >= 160
                else e for e in base.elements]
    resto_de_cima = replace(base.elements[-1], id="e-x", text="Uma legenda do cartão de cima que rolou para fora",
                            desc="", resource_id="", bounds=(24, 165, 720, 215))
    arvore = UiTree(elements=[*descidos, resto_de_cima], packages=[PKG], sensitive=False)
    tela = _leitor().visible_content(arvore)
    assert CABECALHO_B in tela and "cartão de cima" in tela
    sem_cabecalho = UiTree(elements=[e for e in _arvore().elements
                                     if not e.resource_id.endswith("row_feed_profile_header")],
                           packages=[PKG], sensitive=False)
    assert CABECALHO_B in _leitor().visible_content(sem_cabecalho)


@pytest.mark.parametrize("valor", ["", "id=row_feed_profile_header", "a|b", 3])
def test_cartao_invalido_e_recusado_na_carga(valor: object) -> None:
    with pytest.raises(leitura_de_tela.LeituraInvalida):
        leitura_de_tela.de_dados({"conteudo": {"cartao": valor}})


# ===================================================================== 31.260 (b): a identidade do post em foco
TRECHO_A = "Uma legenda inventada do cartão A,"


def test_o_trecho_do_post_em_foco_e_o_comeco_da_legenda_sem_o_autor() -> None:
    assert _leitor().trecho_em_foco(_arvore()) == TRECHO_A
    sem_cabecalho = UiTree(elements=[e for e in _arvore().elements
                                     if not e.resource_id.endswith("row_feed_profile_header")],
                           packages=[PKG], sensitive=False)
    assert _leitor().trecho_em_foco(sem_cabecalho) is None                  # na dúvida, post por posição como antes
    assert _leitor().trecho_em_foco(replace(_arvore(), sensitive=True)) is None


async def test_a_porta_do_rascunho_fixa_o_post_em_foco_e_o_commit_recusa_outro_post(harness: Any,
                                                                                   monkeypatch: Any) -> None:
    """O texto é escrito com o post em foco identificado: a etapa ganha `caption_contains` e a guarda de commit. Com o
    outro cartão no lugar (o que o android-06 viu na hora de comentar), o executor recusa pela guarda."""
    from app.planning.capabilities import capability_of
    from app.taskqueue.executor import textos_do_cartao_ausentes

    from .test_porta_do_plano import _plano
    state = harness.state
    _plano(state, [{"key": "comentar", "cap": "CREATE_COMMENT",
                    "bindings": {"content_brief": "elogie o post", "post_author": "@perfil.alvo"}}], run_id="run-foco")

    async def ler_tela(_rt: Any, _pacote: Any) -> UiTree:
        return _arvore()

    monkeypatch.setattr(state.portoes, "_ler_tela", ler_tela)
    telas: list[str] = []

    async def draft_response(_pid: str, **kw: Any) -> Any:
        telas.append(str(kw.get("screen") or ""))
        return SimpleNamespace(content="Que vista linda!", refused=False, refusal_reason=None, rationale="r",
                               memory_candidates=[]), None

    monkeypatch.setattr(state.social, "draft_response", draft_response)
    obj = state.db.one("SELECT * FROM objectives WHERE run_id='run-foco'")
    etapa = state.db.one("SELECT * FROM steps WHERE id='run-foco:android-01:v1:comentar'")
    cap = capability_of(PKG, "CREATE_COMMENT")
    assert await state.portoes._draft_gate(obj, etapa, cap, obj["profile_id"], pacote=PKG) is None  # noqa: SLF001
    linha = state.db.one("SELECT bindings, commit_guard FROM steps WHERE id='run-foco:android-01:v1:comentar'")
    assert json.loads(linha["bindings"])["caption_contains"] == TRECHO_A
    guardas = json.loads(linha["commit_guard"])
    assert TRECHO_A in guardas and "Que vista linda!" in guardas
    assert telas and CABECALHO_B not in telas[0] and LEGENDA_A in telas[0]
    # na hora de comentar: com o post A na tela, a guarda passa; com só o cartão B (o que o 06 viu), falta o trecho
    so_b = UiTree(elements=[e for e in _arvore().elements if e.bounds[1] >= 948], packages=[PKG], sensitive=False)
    assert textos_do_cartao_ausentes([TRECHO_A], _arvore()) == []
    assert textos_do_cartao_ausentes([TRECHO_A], so_b) == [TRECHO_A]
    # a legenda citada no pedido vence: a porta não a troca
    from app.social.approvals import fixar_post_em_foco
    fixar_post_em_foco(state.db, "run-foco:android-01:v1:comentar", "outro trecho qualquer aqui")
    assert json.loads(state.db.scalar("SELECT bindings FROM steps WHERE id='run-foco:android-01:v1:comentar'")
                      )["caption_contains"] == TRECHO_A
