"""Regras de link de perfil por app, para o tipo `handle` (fase I): a borda declara, o domínio só aplica.

O domínio (`domain/intent.py::handle_from_link`) não sabe de app nenhum. Qual domínio é de qual app, e que primeiro
segmento do caminho não é perfil, é conhecimento do app: desde o ADR-052 (fatia 4) mora no `app.yaml` do pacote
(`links_de_perfil`), e chega aqui pela definição do app no registro, indexada pelo PACOTE (o id do app é
configuração de cada instalação).

Um link de outro domínio, ou de um app sem regra, não vira nome de usuário: vira pergunta.
"""
from __future__ import annotations

from app.modules.applications.infrastructure.registry import definition_of
from app.modules.skills.domain.intent import ProfileLinkRule


def profile_links_for(package: str | None) -> tuple[ProfileLinkRule, ...]:
    if not package:
        return ()
    d = definition_of(package)
    if not d.profile_link_hosts:
        return ()
    return (ProfileLinkRule(hosts=frozenset(d.profile_link_hosts), reserved=frozenset(d.profile_link_reserved)),)
