"""Qual conta está aberta neste aparelho.

Ler o username é o que separa "o app abriu" de "a conta certa está aberta". Nenhuma ação social acontece sem isso
confirmado — conta errada nunca continua em silêncio.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from ...automation.hierarchy import UiTree
from . import navigation
from .navigation import Screen

log = logging.getLogger("poc.instagram")

# Descrições do botão de perfil na barra inferior, por idioma. Estrutura primeiro: o último item da barra.
PROFILE_TAB_HINTS = ("profile", "perfil")
# Quantas telas dispensar/atravessar até chegar ao perfil. Teto: abre caminho sem virar laço.
PASSOS_ATE_O_PERFIL = 6


@dataclass(slots=True)
class AccountCheck:
    observed: str | None
    matches: bool
    detail: str


async def read_account(observe: Any, tap: Any, *, expected: str, locale: str | None,
                       settle_s: float = 2.0) -> AccountCheck:
    """Tenta ler o username na tela atual; se não achar, abre a aba de perfil e tenta de novo.

    `observe` devolve `(UiTree, package)`; `tap` recebe (x, y). Nada aqui digita nem toca em nada além da aba de
    perfil, que é navegação sem efeito externo.
    """
    # Depois de entrar, o Instagram empilha telas na frente do app: "Salvar dados de login?", dicas ("Got it") e
    # passos de onboarding ("Skip"). Nenhuma delas tem barra de perfil, então não adianta procurar a conta ali.
    # O laço abre caminho: dispensa o que estiver na frente, vai até a aba de perfil e só então lê.
    #
    # A identidade sai SÓ da tela de perfil. No feed, o mesmo campo de cabeçalho (`action_bar_title`) vira o autor
    # do reel em foco — no aparelho real lia @kpop_glam_cam e acusava "conta errada" na conta certa.
    tree: UiTree | None = None
    package: str | None = None
    for _ in range(PASSOS_ATE_O_PERFIL):
        tree, package = await observe()
        dispensar = navigation.save_login_dismiss(tree, locale) or navigation.dismiss_button(tree)
        if dispensar is not None:
            await tap(*dispensar.center)
            await asyncio.sleep(settle_s)
            continue

        # A conta só é lida DEPOIS de tocar na NOSSA aba de perfil. Ler o perfil em que se caiu não serve: o perfil
        # de outra pessoa tem o mesmo cabeçalho, e foi assim que @vinijr virou "conta errada" no aparelho de @felipe.
        alvo = _profile_tab(tree)
        if alvo is None:
            break
        await tap(*alvo)
        await asyncio.sleep(settle_s)
        tree, package = await observe()
        if navigation.classify(tree, package=package, locale=locale).screen is Screen.PROFILE:
            achado = navigation.header_username(tree)
            if achado:
                return _check(achado, expected)

    motivo = "tela desconhecida"
    if tree is not None:
        motivo = navigation.classify(tree, package=package, locale=locale).reason
    return AccountCheck(None, False, f"a conta não pôde ser lida na tela ({motivo})")


def _check(observed: str, expected: str) -> AccountCheck:
    ok = observed.lower() == expected.lower()
    return AccountCheck(observed, ok,
                        f"@{observed} confirmado na tela" if ok
                        else f"a conta aberta é @{observed}, e a esperada é @{expected}")


def _profile_tab(tree: UiTree) -> tuple[int, int] | None:
    """Aba de perfil da barra inferior. O id do tab é o sinal mais forte; senão, um item DA BARRA com rótulo exato
    'profile'/'perfil'; por último, o item mais à direita da barra.

    O casamento é exato e restrito à barra de baixo de propósito: 'profile' como substring casava com o botão
    "Edit profile" no meio da tela de perfil, e o toque abria a edição — onde a conta não tem como ser lida.
    """
    for e in tree.elements:
        if e.clickable and e.resource_id.rsplit("/", 1)[-1].lower().startswith("profile_tab"):
            return e.center
    if not tree.elements:
        return None
    base = max(e.bounds[3] for e in tree.elements)
    for e in tree.elements:
        if e.clickable and e.bounds[3] >= base * 0.88 and f"{e.text} {e.desc}".strip().lower() in PROFILE_TAB_HINTS:
            return e.center
    # Sem o tab identificado, NÃO se chuta. O palpite antigo ("o clicável mais à direita lá embaixo") pegava um
    # carrossel de mídia ou um perfil sugerido na tela de onboarding: o toque abria o perfil de OUTRA pessoa e a
    # conta lida virava a dela (@vinijr num aparelho de @felipe).
    return None


def is_logged_in(screen: Screen) -> bool:
    """Pelo conhecimento declarado (`autenticada: true`): feed, perfil, caixa, os intersticiais de depois de entrar e,
    desde a fatia 1 do ADR-052, conversa, post, comentários e busca."""
    return navigation.autenticada(screen)
