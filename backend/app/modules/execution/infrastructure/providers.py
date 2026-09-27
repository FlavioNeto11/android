"""Os quatro `ResourceProvider`s compostos sobre o que já existe (design §11, "Providers iniciais").

Um lugar só para quem monta — o `RunService` (relatório do `mode=plan`, foto do `materialize`) e os testes —, com a
mesma resposta para "qual app tem provedor de sessão": o do catálogo (`planning/catalog`), que identity não enxerga.
"""
from __future__ import annotations

from collections.abc import Mapping

from app.db import Database
from app.modules.applications.infrastructure.app_installation import AppInstallationProvider
from app.modules.execution.application.ports import ResourceProvider
from app.modules.fleet.application.ports import DeviceRuntimeView
from app.modules.fleet.infrastructure.device_state import DeviceStateProvider
from app.modules.identity.infrastructure.account_session import AccountBindingProvider, AppSessionProvider
from app.planning.catalog import session_provider_of
from app.shared.commands import CommandBus
from app.shared.resources import ResourceKind

#: O único provedor de sessão determinístico que existe (`InstagramAuthenticator`, a verdade em `instagram_sessions`)
#: e o único que o canal de comandos sabe acionar (`session.connect`/`session.verify`).
PROVEDOR_DE_SESSAO = "instagram"


def tem_provedor_de_sessao(package: str) -> bool:
    return session_provider_of(package) == PROVEDOR_DE_SESSAO


def resource_providers(db: Database, runtimes: Mapping[str, DeviceRuntimeView], *, session_max_age_s: int,
                       unknown_retry_cap: int,
                       bus: CommandBus | None = None) -> dict[ResourceKind, ResourceProvider]:
    """Sem `bus`, só leitura (é o que o `PlanReport` usa); com ele, também `apply` e `reconcile`."""
    return {
        ResourceKind.device_state: DeviceStateProvider(db, runtimes, bus=bus),
        ResourceKind.app_installation: AppInstallationProvider(db, bus=bus),
        ResourceKind.account_binding: AccountBindingProvider(db, bus=bus),
        ResourceKind.app_session: AppSessionProvider(db, tem_provedor_de_sessao=tem_provedor_de_sessao,
                                                     session_max_age_s=session_max_age_s,
                                                     unknown_retry_cap=unknown_retry_cap, bus=bus),
    }
