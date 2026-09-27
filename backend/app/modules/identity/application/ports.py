"""Portas que o contexto de identidade CONSOME (design §3: a porta pertence a quem consome; §7).

Quem implementa não importa estes `Protocol`s: `SocialRepository` e `EventBus` cumprem as duas primeiras por
estrutura, e o `bootstrap` (hoje `AppState`) liga as partes.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class ProfileStore(Protocol):
    """O pedaço do repositório de perfis que as regras de sessão usam (`social.repository.SocialRepository`)."""

    def profile_row(self, profile_id: str) -> Mapping[str, object] | None: ...

    def update_profile(self, profile_id: str, fields: dict[str, object]) -> None: ...


class EventSink(Protocol):
    """`events.EventBus.emit`, só com os argumentos que a identidade usa."""

    def emit(self, kind: str, message: str, *, level: str = "info", instance_id: str | None = None,
             data: dict[str, object] | None = None) -> object: ...
