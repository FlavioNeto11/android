"""Política de envio de contexto a provedor externo: UM lugar que responde `can_send_*`.

Nenhum `if provider == "jev"` espalhado pelo código: a política olha onde o provedor EXECUTA
(`ProviderLocality`) e que tipo de repositório é. Regras iniciais:

- provedor LOCAL: nada sai da máquina, tudo permitido;
- provedor FAKE (teste): o gate de caminho e de segredo vale como se fosse externo (para exercitar o pipeline), a
  classe do repositório não;
- provedor REMOTE: repositório PRIVADO → NEGADO; PÚBLICO → só se `allow_public`; SINTÉTICO → permitido. Em todos,
  caminho sensível e segredo duro negam.

`PRIVATE_CODE_SEND_APPROVED` é constante de CÓDIGO, não de configuração, de propósito: mudar este valor é decisão do
dono, registrada num ADR, e não um `true` esquecido num YAML (ADR-063).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .model import FallbackReason
from .ports import ProviderLocality
from .sensitive import SensitivePathMatcher, default_matcher, hard_secret_kind, has_soft_secret

PRIVATE_CODE_SEND_APPROVED = False


class RepositoryClass(str, Enum):
    PRIVATE = "private"
    PUBLIC = "public"
    SYNTHETIC = "synthetic"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    code: FallbackReason | None = None
    #: Duro = o PEDIDO inteiro cai (segredo, caminho sensível, repositório negado); mole = só o trecho sai do payload.
    hard: bool = True


_ALLOW = Decision(True, "allowed")


class ExternalContextPolicy:
    def __init__(self, *, repository: RepositoryClass = RepositoryClass.PRIVATE,
                 locality: ProviderLocality = ProviderLocality.REMOTE, allow_public: bool = False,
                 sensitive: SensitivePathMatcher | None = None) -> None:
        self.repository = repository
        self.locality = locality
        self.allow_public = allow_public
        self._sensitive = sensitive or default_matcher()

    @property
    def _sai_da_maquina(self) -> bool:
        return self.locality is not ProviderLocality.LOCAL

    def can_send_repository(self) -> Decision:
        if self.locality is not ProviderLocality.REMOTE:
            return _ALLOW
        if self.repository is RepositoryClass.PRIVATE and not PRIVATE_CODE_SEND_APPROVED:
            return Decision(False, "private_repository", FallbackReason.PRIVACY_BLOCK)
        if self.repository is RepositoryClass.PUBLIC and not self.allow_public:
            return Decision(False, "public_repository_not_enabled", FallbackReason.PRIVACY_BLOCK)
        return _ALLOW

    def can_send_file(self, path: str) -> Decision:
        if not self._sai_da_maquina:
            return _ALLOW
        motivo = self._sensitive.reason(path)
        if motivo:
            return Decision(False, motivo, FallbackReason.PRIVACY_BLOCK)
        return _ALLOW

    def can_send_chunk(self, path: str, text: str) -> Decision:
        if not self._sai_da_maquina:
            return _ALLOW
        arquivo = self.can_send_file(path)
        if not arquivo.allowed:
            return arquivo
        duro = hard_secret_kind(text)
        if duro:
            return Decision(False, f"hard_secret:{duro}", FallbackReason.SECRET_BLOCK)
        if has_soft_secret(text):
            return Decision(False, "soft_secret", FallbackReason.SECRET_BLOCK, hard=False)
        return _ALLOW

    def can_send_query(self, query: str) -> Decision:
        if not self._sai_da_maquina:
            return _ALLOW
        duro = hard_secret_kind(query)
        if duro:
            return Decision(False, f"hard_secret:{duro}", FallbackReason.SECRET_BLOCK)
        if has_soft_secret(query):
            return Decision(False, "soft_secret", FallbackReason.SECRET_BLOCK)
        return _ALLOW

    def can_send_payload(self, text: str) -> Decision:
        """Última conferência do corpo SERIALIZADO que vai ao provedor (mapa ou chunks já montados)."""
        if not self._sai_da_maquina:
            return _ALLOW
        duro = hard_secret_kind(text)
        if duro:
            return Decision(False, f"hard_secret:{duro}", FallbackReason.SECRET_BLOCK)
        return _ALLOW
