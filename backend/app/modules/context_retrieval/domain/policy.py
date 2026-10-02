"""Política de envio de contexto a provedor externo: UM lugar que responde `can_send_*`.

Nenhum `if provider == "jev"` espalhado pelo código: a política olha onde o provedor EXECUTA
(`ProviderLocality`) e que tipo de repositório é. Regras iniciais:

- provedor LOCAL: nada sai da máquina, tudo permitido;
- provedor FAKE (teste): o gate de caminho e de segredo vale como se fosse externo (para exercitar o pipeline), a
  classe do repositório não;
- provedor REMOTE: repositório PRIVADO → NEGADO; SINTÉTICO → NEGADO; PÚBLICO → só com `allow_public` explícito E com
  prova independente de que o repositório REAL é público (`RepositoryVisibilityVerifier`); sem prova (erro, dúvida, remoto
  ausente) a política falha FECHADA. Em todos, caminho sensível e segredo duro negam.

`PRIVATE_CODE_SEND_APPROVED` e `SYNTHETIC_REMOTE_SEND_APPROVED` são constantes de CÓDIGO, não de configuração, de propósito:
mudar um deles é decisão do dono, registrada num ADR, e não um `true` esquecido num YAML (ADR-063). A classe do repositório
vem do YAML, então ela sozinha nunca basta para autorizar envio externo: `synthetic` serve a fixtures e testes (provedor FAKE
ou LOCAL), não é autorização para mandar código a um serviço remoto, e um repositório privado marcado "synthetic" por engano
não contorna `PRIVATE_CODE_SEND_APPROVED`. Pela mesma razão `public` no YAML também não basta: um repositório privado marcado
"public" por engano é barrado pela verificação, que só roda quando o provedor é remoto e a configuração já pede o envio
(local, shadow-local e fake nunca fazem verificação nem rede).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from .model import FallbackReason
from .ports import ProviderLocality, RepositoryVisibilityVerifier, Visibility
from .sensitive import SensitivePathMatcher, default_matcher, hard_secret_kind, has_soft_secret

PRIVATE_CODE_SEND_APPROVED = False
#: `synthetic` + provedor REMOTE também é negado. Constante de código, sem leitura de configuração (ver o docstring do módulo).
SYNTHETIC_REMOTE_SEND_APPROVED = False


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
                 sensitive: SensitivePathMatcher | None = None,
                 verifier: RepositoryVisibilityVerifier | None = None) -> None:
        self.repository = repository
        self.locality = locality
        self.allow_public = allow_public
        self._sensitive = sensitive or default_matcher()
        self._verifier = verifier

    def fingerprint(self) -> str:
        """Identidade da política vigente. Entra na chave do cache da resposta semântica: mudar o que pode sair da máquina
        (classe do repositório, padrões sensíveis, provedor) nunca reaproveita uma resposta dada sob a política antiga."""
        return f"{self.repository.value}/{self.locality.value}/{int(self.allow_public)}/{self._sensitive.fingerprint()}"

    @property
    def _sai_da_maquina(self) -> bool:
        return self.locality is not ProviderLocality.LOCAL

    def configured_for_remote(self) -> Decision:
        """O que a CONFIGURAÇÃO permite a provedor remoto. Só configuração: não prova nada sobre o repositório real."""
        if self.locality is not ProviderLocality.REMOTE:
            return _ALLOW
        if self.repository is RepositoryClass.PRIVATE and not PRIVATE_CODE_SEND_APPROVED:
            return Decision(False, "private_repository", FallbackReason.PRIVACY_BLOCK)
        if self.repository is RepositoryClass.SYNTHETIC and not SYNTHETIC_REMOTE_SEND_APPROVED:
            return Decision(False, "synthetic_repository", FallbackReason.PRIVACY_BLOCK)
        if self.repository is RepositoryClass.PUBLIC and not self.allow_public:
            return Decision(False, "public_repository_not_enabled", FallbackReason.PRIVACY_BLOCK)
        return _ALLOW

    def can_send_repository(self) -> Decision:
        """Autoriza o envio do repositório: configuração E (se remoto) prova independente de visibilidade pública."""
        d = self.configured_for_remote()
        if not d.allowed or not self._exige_prova:
            return d
        return self._decidir_pela_prova(self._provar)

    def peek_repository(self) -> tuple[Decision, Visibility | None]:
        """Como `can_send_repository`, mas sem rede: usa só a prova que já está vigente. Para o endpoint de status."""
        d = self.configured_for_remote()
        if not d.allowed or not self._exige_prova:
            return d, None
        v = self._verifier.peek() if self._verifier is not None else Visibility.UNKNOWN
        return self._decidir_pela_prova(lambda: v), v

    @property
    def _exige_prova(self) -> bool:
        """A prova de visibilidade é o que sustenta a declaração `public`: só ela. Privado e sintético, quando o dono um dia
        liberar a constante de código, não têm o que provar (e nunca seriam públicos)."""
        return self.locality is ProviderLocality.REMOTE and self.repository is RepositoryClass.PUBLIC

    def _provar(self) -> Visibility:
        if self._verifier is None:
            return Visibility.UNKNOWN
        try:
            return self._verifier.verify()
        except Exception:  # noqa: BLE001 - o verificador é infraestrutura: qualquer falha dele é "não provado"
            return Visibility.UNKNOWN

    @staticmethod
    def _decidir_pela_prova(prova: "Callable[[], Visibility]") -> Decision:
        v = prova()
        if v is Visibility.PUBLIC:
            return _ALLOW
        if v is Visibility.PRIVATE:
            return Decision(False, "repository_not_public", FallbackReason.PRIVACY_BLOCK)
        return Decision(False, "repository_visibility_unverified", FallbackReason.PRIVACY_BLOCK)

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

    def can_send_map_entry(self, text: str) -> Decision:
        """Uma entrada do mapa da etapa A (caminho, linguagem, símbolos, resumo) já renderizada. Segredo duro derruba o pedido,
        segredo mole só omite a entrada. O mapa também é conteúdo derivado do repositório (docstring, título de markdown)."""
        if not self._sai_da_maquina:
            return _ALLOW
        duro = hard_secret_kind(text)
        if duro:
            return Decision(False, f"hard_secret:{duro}", FallbackReason.SECRET_BLOCK)
        if has_soft_secret(text):
            return Decision(False, "soft_secret", FallbackReason.SECRET_BLOCK, hard=False)
        return _ALLOW

    def can_send_payload(self, text: str) -> Decision:
        """Última conferência do corpo SERIALIZADO que vai ao provedor (mapa ou chunks já montados)."""
        if not self._sai_da_maquina:
            return _ALLOW
        duro = hard_secret_kind(text)
        if duro:
            return Decision(False, f"hard_secret:{duro}", FallbackReason.SECRET_BLOCK)
        return _ALLOW
