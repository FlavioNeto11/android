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
    tree, package = await observe()
    # "Salvar dados de login?" aparece logo depois de entrar e é um modal: cobre a barra de perfil, então a conta não
    # tem como ser lida enquanto ele estiver na frente. Dispensa em "Agora não" (não salva na nuvem) e relê.
    dispensar = navigation.save_login_dismiss(tree, locale)
    if dispensar is not None:
        await tap(*dispensar.center)
        await asyncio.sleep(settle_s)
        tree, package = await observe()

    # Identidade vem SÓ do cabeçalho de perfil: o feed exibe o @ de reels e stories de outras contas, e tomar um
    # desses pela conta própria dispara "conta errada" falso (visto no aparelho real: leu @kpop_glam_cam do feed).
    achado = navigation.header_username(tree)
    if achado:
        return _check(achado, expected)

    alvo = _profile_tab(tree)
    if alvo is None:
        return AccountCheck(None, False, "não foi possível localizar a aba de perfil para ler a conta")
    x, y = alvo
    await tap(x, y)
    await asyncio.sleep(settle_s)
    tree, package = await observe()
    achado = navigation.header_username(tree)
    if achado:
        return _check(achado, expected)
    classificacao = navigation.classify(tree, package=package, locale=locale)
    return AccountCheck(None, False, f"a conta não pôde ser lida na tela ({classificacao.reason})")


def _check(observed: str, expected: str) -> AccountCheck:
    ok = observed.lower() == expected.lower()
    return AccountCheck(observed, ok,
                        f"@{observed} confirmado na tela" if ok
                        else f"a conta aberta é @{observed}, e a esperada é @{expected}")


def _profile_tab(tree: UiTree) -> tuple[int, int] | None:
    """Aba de perfil: por descrição, quando houver; senão, o item mais à direita da barra inferior."""
    for e in tree.elements:
        rotulo = f"{e.text} {e.desc}".strip().lower()
        alvo = e.resource_id.rsplit("/", 1)[-1].lower()
        if e.clickable and (any(h in rotulo for h in PROFILE_TAB_HINTS) or alvo.startswith("profile_tab")):
            return e.center
    if not tree.elements:
        return None
    base = max(e.bounds[3] for e in tree.elements)
    barra = [e for e in tree.elements if e.clickable and e.bounds[3] >= base * 0.88]
    if len(barra) < 3:                       # barra de navegação tem vários itens; menos que isso não é barra
        return None
    return max(barra, key=lambda e: e.bounds[0]).center


def is_logged_in(screen: Screen) -> bool:
    return screen in (Screen.FEED, Screen.PROFILE, Screen.INBOX, Screen.SAVE_LOGIN_PROMPT, Screen.ACCOUNT_SWITCHER)
