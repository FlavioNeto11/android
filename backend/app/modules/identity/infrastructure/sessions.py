"""O que a composição entrega a quem fabrica um provedor de sessão (fase K1).

Um app com conta gerenciada declara, no manifesto, uma FÁBRICA do seu provedor de sessão (`SessionProviderFactory`).
Ela recebe estas dependências — as mesmas que o `InstagramAuthenticator` sempre recebeu no `AppState.__init__` — e
devolve algo que cumpra `application.ports.SessionProvider`. É o que deixa um app novo trazer o próprio provedor
sem que a composição saiba o nome dele.

Mora na infraestrutura porque os tipos são do legado (config, gerenciador de aparelhos, repositório social, cofre,
canal sensível, barramento de eventos), que as camadas puras não enxergam (D2/D3).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.modules.identity.application.ports import SessionProvider

if TYPE_CHECKING:  # pragma: no cover - só para o verificador de tipos
    from app.config import Config
    from app.devices.manager import DeviceManager
    from app.events import EventBus
    from app.security.secret_store import SecretStore
    from app.security.sensitive_input import SensitiveInputChannel
    from app.social.repository import SocialRepository


@dataclass(frozen=True, slots=True)
class SessionDeps:
    """As dependências de um provedor de sessão: o aparelho (`devices`), o perfil e a sessão dele (`repo`), a senha
    no cofre (`secrets`) e o único caminho por onde ela pode ser digitada (`sensitive_input`, ADR-025)."""

    cfg: Config
    devices: DeviceManager
    repo: SocialRepository
    secrets: SecretStore
    sensitive_input: SensitiveInputChannel
    bus: EventBus


#: A fábrica que o manifesto de um app declara. Chamada uma vez por composição (`SessionProviders` guarda o provedor).
SessionProviderFactory = Callable[[SessionDeps], SessionProvider]
