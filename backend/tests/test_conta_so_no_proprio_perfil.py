"""31.286: a conta aberta só se lê no perfil PRÓPRIO; a página de outra pessoa é indeterminada, nunca "conta errada".

Execução de 08/10 (android-06): o aparelho estava na página de @nasa (botões Follow/Message, `action_bar_title` = nasa) e
o leitor de sessão tomou o título como a conta logada ("a conta aberta é @nasa, e a esperada é …") e parou a etapa. O
botão de seguir/mensagem só existe no perfil alheio: nele a extração da conta devolve `None` e o laço de `ler_conta` volta
a tocar a aba do próprio perfil. Os ids do botão de seguir vêm de captura real do Instagram; a árvore daqui é montada com
eles (a hierarquia crua de hoje não foi gravada: o aparelho estava em uso pelo operador).

Nível de prova: `simulated` (árvores sintéticas com os ids reais, conhecimento declarado do Instagram). Nada real.
"""
from __future__ import annotations

from typing import Any

from app.automation.hierarchy import UiTree, parse_hierarchy
from app.integrations.app_declarado.conhecimento import do_app
from app.integrations.app_declarado.sessao import Outcome, _conferir_conta_depois_do_envio, ler_conta

from .fake_instagram import PKG

ESPERADA = "pessoa.teste4821"
_P = f"{PKG}:id/"


def _no(cls: str, rid: str, texto: str, caixa: tuple[int, int, int, int], *, clicavel: bool = False) -> str:
    x1, y1, x2, y2 = caixa
    return (f'<node class="{cls}" package="{PKG}" text="{texto}" resource-id="{_P}{rid}" content-desc="" '
            f'clickable="{str(clicavel).lower()}" enabled="true" focused="false" password="false" '
            f'scrollable="false" bounds="[{x1},{y1}][{x2},{y2}]" />')


def _abas() -> list[str]:
    return [_no("android.widget.ImageView", rid, "", (20 + 140 * i, 1180, 140 + 140 * i, 1260), clicavel=True)
            for i, rid in enumerate(["feed_tab", "search_tab", "clips_tab", "shopping_tab", "profile_tab"])]


def _arvore(nos: list[str]) -> UiTree:
    return parse_hierarchy('<hierarchy rotation="0">' + "".join(nos) + "</hierarchy>")


def perfil_de_terceiro(dono: str = "nasa", *, seguindo: bool = False) -> UiTree:
    botao = ("profile_header_unfollow_button", "Following") if seguindo else ("profile_header_follow_button", "Follow")
    return _arvore([
        _no("android.widget.TextView", "action_bar_title", dono, (40, 60, 400, 110)),
        _no("android.widget.Button", botao[0], botao[1], (40, 400, 340, 460), clicavel=True),
        _no("android.widget.Button", "profile_header_message_button", "Message", (360, 400, 680, 460), clicavel=True),
        *_abas()])


def perfil_proprio(conta: str = ESPERADA) -> UiTree:
    return _arvore([
        _no("android.widget.TextView", "row_profile_header_textview_username", conta, (40, 120, 400, 180)),
        _no("android.widget.Button", "profile_edit_button", "Edit profile", (40, 400, 340, 460), clicavel=True),
        _no("android.widget.Button", "profile_share_button", "Share profile", (360, 400, 680, 460), clicavel=True),
        *_abas()])


def test_a_extracao_da_conta_ignora_a_pagina_de_outra_pessoa_e_le_a_propria() -> None:
    k = do_app(PKG)
    for terceiro in (perfil_de_terceiro(), perfil_de_terceiro(seguindo=True)):
        assert k.conta_no_cabecalho(terceiro) is None
        assert k.conta_observada(terceiro) is None                 # nem pelo palpite do primeiro @ da tela
    assert k.conta_no_cabecalho(perfil_proprio()) == ESPERADA
    assert k.conta_observada(perfil_proprio()) == ESPERADA


def test_depois_do_envio_o_perfil_de_terceiro_pede_a_leitura_pela_aba_e_nao_acusa_conta_errada() -> None:
    k = do_app(PKG)
    arvore = perfil_de_terceiro()
    r = k.reconhecer(arvore, package=PKG, locale="en-US")
    v = _conferir_conta_depois_do_envio(k, arvore, r, ESPERADA)
    assert v.outcome is not Outcome.WRONG_ACCOUNT and v.conferir, (v.outcome, v.detail)


def _observador(arvores: list[UiTree]) -> tuple[Any, Any, list[tuple[int, int]]]:
    toques: list[tuple[int, int]] = []

    async def observe() -> tuple[UiTree, str]:
        return arvores[min(len(toques), len(arvores) - 1)], PKG

    async def tap(x: int, y: int) -> None:
        toques.append((x, y))

    return observe, tap, toques


async def test_ler_conta_na_pagina_de_terceiro_toca_a_aba_de_novo_e_le_a_propria() -> None:
    """A 1ª ida à aba ainda mostra @nasa (a página alheia segue empilhada): não é a conta; o 2º toque chega ao próprio."""
    observe, tap, toques = _observador([perfil_de_terceiro(), perfil_de_terceiro(), perfil_proprio()])
    r = await ler_conta(do_app(PKG), observe, tap, expected=ESPERADA, locale="en-US")
    assert r.matches and r.observed == ESPERADA, r
    assert len(toques) == 2                                          # só toques na aba do perfil, nada mais


async def test_ler_conta_que_nao_sai_da_pagina_de_terceiro_fica_indeterminada_nunca_conta_errada() -> None:
    observe, tap, _toques = _observador([perfil_de_terceiro()])
    r = await ler_conta(do_app(PKG), observe, tap, expected=ESPERADA, locale="en-US")
    assert r.observed is None and not r.matches, r
    assert "a conta aberta é" not in (r.detail or ""), r.detail


async def test_conta_realmente_errada_no_proprio_perfil_segue_sendo_conta_errada() -> None:
    """Contraprova: o perfil PRÓPRIO de outra conta nossa (sem Follow/Message) continua acusando conta errada."""
    observe, tap, _toques = _observador([perfil_proprio("outra.conta9")])
    r = await ler_conta(do_app(PKG), observe, tap, expected=ESPERADA, locale="en-US")
    assert r.observed == "outra.conta9" and not r.matches, r
