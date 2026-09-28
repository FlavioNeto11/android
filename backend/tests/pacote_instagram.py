"""O pacote de conhecimento do Instagram visto pelos testes (ADR-052).

O que era `app/integrations/instagram/navigation.py` virou dado em `app/conhecimento/apps/com.instagram.android/`, lido
pelos motores genéricos. Os testes podem conhecer o Instagram (o `fake_instagram.py` é o dublê dele); o `app/` não.
Daqui saem o conhecimento de sessão e de telas (o MESMO objeto que o provedor de sessão usa), o manifesto descoberto
e as leituras de tela declaradas.
"""
from __future__ import annotations

from app.automation.conhecimento_de_telas import TelaReconhecida
from app.automation.hierarchy import UiTree
from app.automation.leitura_de_tela import LeituraDeclarada
from app.integrations.app_declarado.conhecimento import do_app
from app.integrations.app_declarado.pacote import PASTA_DOS_APPS, manifesto_da_pasta

from .fake_instagram import PKG

SESSAO = do_app(PKG)
CONHECIMENTO = SESSAO.telas
MANIFESTO = manifesto_da_pasta(PASTA_DOS_APPS / PKG)
DEFINICAO = MANIFESTO.definition
assert isinstance(MANIFESTO.screen, LeituraDeclarada)
LEITURA: LeituraDeclarada = MANIFESTO.screen


def reconhecer(tree: UiTree, *, package: str | None, locale: str | None = None) -> TelaReconhecida:
    return SESSAO.reconhecer(tree, package=package, locale=locale)


def conteudo_visivel(tree: UiTree, *, limite: int = 600) -> str:
    return LEITURA.visible_content(tree, limite=limite)


def comentario_de(tree: UiTree, autor: str) -> str:
    return LEITURA.comment_of(tree, autor)


def mensagem_de(tree: UiTree, autor: str) -> str:
    return LEITURA.message_of(tree, autor)
