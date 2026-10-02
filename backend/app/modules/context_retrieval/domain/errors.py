"""Falhas do provedor semântico. O retrieval as captura TODAS e cai no local (fail-open); a razão vira rótulo."""
from __future__ import annotations

from .model import FallbackReason


class ProviderError(Exception):
    """Falha de um provedor semântico. `reason` é o rótulo que vai a `fallback_reason`; `str(exc)` nunca carrega código."""

    reason: FallbackReason = FallbackReason.PROVIDER_ERROR

    def __init__(self, message: str = "", *, reason: FallbackReason | None = None) -> None:
        super().__init__(message)
        if reason is not None:
            self.reason = reason


class ProviderUnavailable(ProviderError):
    reason = FallbackReason.PROVIDER_UNAVAILABLE


class ProviderKeyMissing(ProviderUnavailable):
    reason = FallbackReason.KEY_MISSING


class ProviderTimeout(ProviderError):
    reason = FallbackReason.TIMEOUT


class ProviderRateLimited(ProviderError):
    reason = FallbackReason.RATE_LIMITED


class ProviderOverloaded(ProviderError):
    reason = FallbackReason.OVERLOADED


class ProviderOffline(ProviderError):
    reason = FallbackReason.PROVIDER_OFFLINE


class ProviderInvalidResponse(ProviderError):
    reason = FallbackReason.INVALID_RESPONSE


class ProviderOptionLimit(ProviderError):
    """Pergunta com mais opções do que o provedor aceita: recusada localmente, sem montar corpo nem tocar a rede."""

    reason = FallbackReason.PROVIDER_ERROR
