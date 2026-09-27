"""Regras de link de perfil por app, para o tipo `handle` (fase I): a borda declara, o domínio só aplica.

O domínio (`domain/intent.py::handle_from_link`) não sabe de app nenhum. Qual domínio é de qual app, e que primeiro
segmento do caminho não é perfil, é conhecimento do app — mora aqui, na infraestrutura, junto do pacote do catálogo,
e é indexado pelo PACOTE (a chave do catálogo), não pelo id do app, que é configuração de cada instalação.

Só o Instagram tem regra hoje. Um link de outro domínio, ou de um app sem regra, não vira nome de usuário: vira
pergunta.
"""
from __future__ import annotations

from app.modules.skills.domain.intent import ProfileLinkRule
from app.planning.catalog.instagram import PACKAGE as INSTAGRAM_PACKAGE

#: `instagram.com/<usuario>` é perfil; `/p/<código>` é post, `/reel/`, `/stories/`, `/explore/`... não são.
INSTAGRAM_PROFILE_LINKS = ProfileLinkRule(
    hosts=frozenset({"instagram.com", "instagr.am"}),
    reserved=frozenset({"p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct", "about", "legal",
                        "developer", "web", "s", "ar", "challenge", "emails", "session", "oauth", "api"}))

_POR_PACOTE: dict[str, tuple[ProfileLinkRule, ...]] = {INSTAGRAM_PACKAGE: (INSTAGRAM_PROFILE_LINKS,)}


def profile_links_for(package: str | None) -> tuple[ProfileLinkRule, ...]:
    return _POR_PACOTE.get(package, ()) if package else ()
