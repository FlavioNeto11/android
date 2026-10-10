"""Política por perfil.

A política define o nível de autorização de uma ação: `AUTONOMOUS` roda sozinha; `APPROVAL_REQUIRED` espera uma pessoa
aprovar o conteúdo; `MANUAL_ONLY` e `DISABLED` não rodam por automação de jeito nenhum.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable

from ..db import loads
from ..models import InteractionStatus, InteractionType
from ..planning.capabilities import Capability
from ..planning.exploracao import chaves_da_politica
from ..modules.applications.infrastructure.registry import pacote_ancora
from .repository import SocialRepository

# Padrões de limite (vazios: sem controle de taxa).
DEFAULT_LIMITS: dict[str, int] = {}
BUCKET_TYPES: dict[str, tuple[str, ...]] = {}
CONTAM = (InteractionStatus.pending.value, InteractionStatus.confirmed.value, InteractionStatus.uncertain.value)
TODOS_OS_BALDES: tuple[str, ...] = ()
UMA_CONTA_POR_ALVO: frozenset[str] = frozenset()
UMA_VEZ_POR_ALVO: frozenset[str] = frozenset()
_ROTULO_DO_BALDE = {"follows": "seguir", "dms": "mensagem direta", "comments": "comentário", "likes": "curtida",
                   "posts": "publicação"}
MOTIVO_CITA_A_FAMILIA = ""


@dataclass(slots=True)
class Verdict:
    allowed: bool = True
    reason: str = ""
    retry_at: str | None = None
    policy: str = "autonomous"
    needs_approval: bool = False
    hint: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    excecao: str | None = None

    @property
    def is_wait(self) -> bool:
        return not self.allowed and self.retry_at is not None


@dataclass(frozen=True, slots=True)
class ContextoDoPedido:
    raiz: str
    familia: frozenset[str] = frozenset()
    porta_vozes: frozenset[str] = frozenset()


# ---------------------------------------------------------------- política POR APP (23.10)
CHAVE_POR_APP = "por_app"


def _e_do_ancora(package: str | None) -> bool:
    return package is None or package == pacote_ancora()


def _por_app(caps: Mapping[str, object]) -> dict[str, object]:
    bruto = caps.get(CHAVE_POR_APP)
    return dict(bruto) if isinstance(bruto, Mapping) else {}


def politicas_do_app(caps: Mapping[str, object] | None, package: str | None) -> dict[str, object]:
    caps = caps or {}
    if package is None or _e_do_ancora(package):
        return {k: v for k, v in caps.items() if k != CHAVE_POR_APP}
    do_app = _por_app(caps).get(package)
    return dict(do_app) if isinstance(do_app, Mapping) else {}


def com_politicas_do_app(caps: Mapping[str, object] | None, package: str | None,
                         novas: Mapping[str, object]) -> dict[str, object]:
    resultado = dict(caps or {})
    por_app = _por_app(resultado)
    if package is None or _e_do_ancora(package):
        resultado = dict(novas)
    elif novas:
        por_app[package] = dict(novas)
    else:
        por_app.pop(package, None)
    resultado.pop(CHAVE_POR_APP, None)
    if por_app:
        resultado[CHAVE_POR_APP] = por_app
    return resultado


_POLICY_RANK = {"disabled": 0, "manual_only": 1, "approval_required": 2, "autonomous": 3}


def _valida(politica: Any) -> bool:
    return politica in _POLICY_RANK


def _limite_valido(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool) and valor >= 0


class PolicyEngine:
    def __init__(self, repo: SocialRepository, settings_getter: Callable[[], Any] | None = None, *,
                 jitter: Callable[[float, float], float] = lambda a, b: 0):
        self.repo = repo
        self._settings = settings_getter
        self._jitter = jitter

    def _own(self, profile_id: str) -> dict[str, Any]:
        row = self.repo.profile_row(profile_id)
        return loads(row["automation_policy"], {}) or {} if row else {}

    def _group(self, profile_id: str) -> dict[str, Any]:
        row = self.repo.profile_row(profile_id)
        gid = row["policy_group_id"] if row else None
        grupo = self.repo.policy_group_row(gid) if gid else None
        if grupo is None:
            return {}
        return {"capabilities": loads(grupo["capabilities"], {}) or {}, "limits": loads(grupo["limits"], {}) or {}}

    def _escolha(self, camada: Mapping[str, object], cap: Capability, package: str | None) -> str | None:
        """A política que a camada (perfil ou grupo) escolheu para a ação. 31.297: a exploração de efeito tem duas chaves, a
        do pedido e a genérica `explorar_efeito`; a mais específica vence. Ação do catálogo tem uma chave só."""
        do_app = politicas_do_app(camada.get("capabilities"), package)
        return next((str(do_app[k]) for k in chaves_da_politica(cap.key) if _valida(do_app.get(k))), None)

    def origin_for(self, profile_id: str, cap: Capability, package: str | None = None) -> str:
        if _valida(self._escolha(self._own(profile_id), cap, package)):
            return "own"
        if _valida(self._escolha(self._group(profile_id), cap, package)):
            return "group"
        return "default"

    def policy_for(self, profile_id: str, cap: Capability, package: str | None = None) -> str:
        for camada in (self._own(profile_id), self._group(profile_id)):
            escolhido = self._escolha(camada, cap, package)
            if _valida(escolhido):
                return escolhido
        return cap.default_policy

    @staticmethod
    def is_loosened(cap: Capability, politica: str) -> bool:
        return _POLICY_RANK.get(politica, 3) > _POLICY_RANK.get(cap.default_policy, 3)

    def limits_for(self, profile_id: str) -> dict[str, int]:
        return dict(DEFAULT_LIMITS)

    def limits_origin(self, profile_id: str) -> dict[str, str]:
        return {}

    def mensagem_repetida(self, profile_id: str, cap: Capability, bindings: Mapping[str, object] | None, *,
                          app_id: str | None = None, step_id: str | None = None,
                          desde: Any = None) -> str | None:
        return None

    def mesmo_objeto_na_familia(self, profile_id: str, cap: Capability, bindings: Mapping[str, object] | None, *,
                                counterparty: str | None, app_id: str | None, step_id: str | None,
                                pedido: ContextoDoPedido | None) -> tuple[bool, str, str] | None:
        return None

    def cita_a_familia(self, profile_id: str, cap: Capability, bindings: Mapping[str, object] | None,
                       pedido: ContextoDoPedido | None) -> str | None:
        return None

    def _sem_aprovacao_pelo_grupo(self, profile_id: str, politica: str, nota: str) -> tuple[str, str]:
        """28.61: a persona do grupo de `LimitsCfg.grupo_sem_aprovacao` não passa pela aprovação de POLÍTICA. Só troca
        `approval_required` por `autonomous`: a ação desligada ou manual já foi recusada antes, e nada mais passa por
        aqui. Restaurado depois do refactor do ADR-083, que tirou o leitor junto com os tetos por engano: o pedido do
        dono de 06/10 ("tudo liberado para todas as personas") era sobre aprovação, e o refactor era sobre taxa."""
        if politica != "approval_required" or self._settings is None:
            return politica, nota
        grupo = str(getattr(self._settings(), "grupo_sem_aprovacao", "") or "").strip()
        linha = self.repo.profile_row(profile_id) if grupo else None
        if linha is None or linha["policy_group_id"] != grupo:
            return politica, nota
        dispensa = "aprovação dispensada: a persona está no grupo de política sem aprovação (28.61)"
        return "autonomous", "; ".join(t for t in (nota, dispensa) if t)

    def tem_conversa(self, profile_id: str, counterparty: str | None, app_id: str | None = None) -> bool:
        return False

    def check(self, profile_id: str, cap: Capability, *, run_id: str | None = None,
              counterparty: str | None = None, app_id: str | None = None, package: str | None = None,
              step_id: str | None = None, pedido: ContextoDoPedido | None = None,
              bindings: Mapping[str, object] | None = None) -> Verdict:
        politica = self.policy_for(profile_id, cap, package)
        if politica == "disabled":
            return Verdict(allowed=False, policy=politica,
                           reason=f"a ação {cap.key} está desligada para este perfil",
                           hint="Ligue esta ação nas configurações do perfil, se for mesmo para ela acontecer.")
        if politica == "manual_only":
            return Verdict(allowed=False, policy=politica,
                           reason=f"a ação {cap.key} é manual neste perfil",
                           hint="Faça esta ação pelo controle manual do aparelho, ou mude a política do perfil.")
        politica, nota = self._sem_aprovacao_pelo_grupo(profile_id, politica, "")
        return Verdict(policy=politica, needs_approval=politica == "approval_required", reason=nota)
