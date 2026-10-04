"""30.64: o OBJETO do efeito (o post, o comentário, a conversa, a mídia) declarado no catálogo.

A aprovação e o "já feito" identificavam o item só pela pessoa (`counterparty`): aprovar "comentar no post A de @ana"
valia para o post B dela. Toda ação com efeito declara em `objeto_alvo` os argumentos que dizem sobre o quê age; a que
não declara falha fechado (não se aprova antes nem se reconhece repetida). Prova `simulated`: catálogo e funções puras.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.modules.capabilities.infrastructure.catalog_registry import definicao
from app.planning.capabilities import (ARGUMENTOS_DE_TEXTO, Capability, CapabilityCatalog, carregar_catalogo,
                                       objeto_da_acao)

APPS = Path(__file__).resolve().parents[1] / "app" / "conhecimento" / "apps"


def _faltam_declarar() -> list[str]:
    """`pacote:AÇÃO` de toda ação com efeito, oferecida ao planejador, que não diz sobre o quê age."""
    faltam = []
    for arquivo in sorted(APPS.glob("*/catalogo.yaml")):
        catalogo = carregar_catalogo(arquivo)
        faltam += [f"{catalogo.package}:{c.key}" for c in catalogo.capabilities
                   if c.side_effect and not c.internal and not c.objeto_alvo]
    return faltam


def test_a_interna_e_a_manual_nao_precisam_declarar() -> None:
    CapabilityCatalog("com.exemplo.x", [_cap(default_policy="manual_only"), replace(_cap(key="PREPARAR"), internal=True)])


def test_toda_acao_com_efeito_declara_o_objeto_ou_e_so_manual() -> None:
    """O catálogo inteiro, de todos os apps: a lista das que faltam declarar tem de ser só de ação que a plataforma
    nunca faz sozinha (`manual_only`). Uma ação nova com efeito sem `objeto_alvo` quebra aqui, com o nome dela."""
    faltam = _faltam_declarar()
    so_manual = []
    for arquivo in sorted(APPS.glob("*/catalogo.yaml")):
        catalogo = carregar_catalogo(arquivo)
        so_manual += [f"{catalogo.package}:{c.key}" for c in catalogo.capabilities if c.default_policy == "manual_only"]
    assert [f for f in faltam if f not in so_manual] == [], f"declare objeto_alvo em: {faltam}"
    assert faltam == ["com.instagram.android:LOGOUT"]


def test_o_objeto_separa_dois_posts_da_mesma_pessoa() -> None:
    cap = carregar_catalogo(APPS / "com.instagram.android" / "catalogo.yaml").get("CREATE_COMMENT")
    assert cap is not None
    a = objeto_da_acao(cap, {"post_author": "@Ana", "caption_contains": "praia", "content": "Lindo!"})
    b = objeto_da_acao(cap, {"post_author": "ana", "caption_contains": "serra", "content": "Lindo!"})
    assert a == {"post_author": "@ana", "caption_contains": "praia"}       # o texto fica de fora; a pessoa normaliza
    assert a != b
    assert objeto_da_acao(cap, {"post_author": " ANA ", "caption_contains": "praia"}) == a


def test_sem_declaracao_ou_com_argumento_por_resolver_nao_ha_objeto() -> None:
    """`None` é o fechado: quem recebe não reaproveita aprovação nem dá o item por repetido."""
    catalogo = carregar_catalogo(APPS / "com.instagram.android" / "catalogo.yaml")
    assert objeto_da_acao(None, {"username": "ana"}) is None
    assert objeto_da_acao(catalogo.get("LOGOUT"), {}) is None
    dm = catalogo.get("SEND_MESSAGE")
    assert objeto_da_acao(dm, {"username": "{item}"}) is None
    assert objeto_da_acao(dm, {"username": "{{saida:autor}}"}) is None
    assert objeto_da_acao(dm, {"username": "@Ana"}) == {"username": "@ana"}
    assert objeto_da_acao(catalogo.get("CREATE_POST"), {"image_id": "img-1", "content": "x"}) == {"image_id": "img-1"}


def _cap(**campos: object) -> Capability:
    base = Capability(key="CURTIR", title="Curtir", goal="Curtir.", post_kind="model_judged", post_value="curtido",
                      post_description="Curtido.", side_effect=True, optional_bindings=("post_author", "content"))
    return replace(base, **campos)  # type: ignore[arg-type]


@pytest.mark.parametrize(("objeto", "motivo"), [
    (("autor",), "não é argumento da ação"),
    (("content",), "é o texto da ação"),
    (("post_author", "post_author"), "nome repetido"),
    ((), "precisa declarar objeto_alvo"),                  # revisão da fila, item 8: efeito sem declaração não carrega
])
def test_a_carga_recusa_objeto_mal_declarado(objeto: tuple[str, ...], motivo: str) -> None:
    with pytest.raises(ValueError, match=motivo):
        CapabilityCatalog("com.exemplo.x", [_cap(objeto_alvo=objeto)])


def test_o_texto_nunca_e_objeto_e_o_dominio_recebe_a_declaracao() -> None:
    catalogo = carregar_catalogo(APPS / "com.instagram.android" / "catalogo.yaml")
    for cap in catalogo.capabilities:
        assert not set(cap.objeto_alvo) & ARGUMENTOS_DE_TEXTO, cap.key
    cap = catalogo.get("LIKE_POST")
    assert cap is not None
    assert definicao(cap, catalogo.package).governance.objeto_alvo == ("post_author", "caption_contains")
