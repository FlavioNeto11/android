"""Política e limites por perfil.

Duas perguntas, respondidas antes de a etapa ser assumida:

1. **Esta ação é permitida para este perfil?** `AUTONOMOUS` roda sozinha; `APPROVAL_REQUIRED` espera uma pessoa
   aprovar o conteúdo; `MANUAL_ONLY` e `DISABLED` não rodam por automação de jeito nenhum.
2. **Ela cabe agora?** Curtir, comentar, seguir e mandar mensagem têm teto por hora e intervalo mínimo entre ações.
   O limite não existe para contornar nada do Instagram: existe para o sistema não agir como robô e derrubar a
   própria conta.

Represar NÃO é falhar: a etapa volta para `retry_wait` com hora marcada, sem gastar tentativa e sem chamar modelo.
O tempo parado é descontado do prazo do objetivo — esperar não é demorar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from ..db import loads
from ..models import InteractionStatus, InteractionType
from ..planning.capabilities import Capability
from ..util import now, parse_iso, to_iso
from .repository import SocialRepository

# Padrões conservadores. O perfil pode ajustar em `instagram_profiles.automation_policy`.
DEFAULT_LIMITS: dict[str, int] = {
    "likes_per_hour": 30,
    "comments_per_hour": 8,
    "follows_per_hour": 8,
    "dms_per_hour": 15,
    "actions_per_run": 20,
    "cooldown_between_external_actions_s": 45,
}

# Que interações contam em cada balde. É o histórico que conta — não um contador à parte que poderia divergir dele.
BUCKET_TYPES: dict[str, tuple[str, ...]] = {
    "likes": (InteractionType.post_liked.value, InteractionType.post_unliked.value,
              InteractionType.comment_liked.value),
    "comments": (InteractionType.comment_replied.value, InteractionType.comment_received.value),
    "follows": (InteractionType.followed.value, InteractionType.unfollowed.value,
                InteractionType.follow_request_accepted.value, InteractionType.follow_request_declined.value),
    "dms": (InteractionType.dm_sent.value,),
}

# Tentativa e efeito confirmado contam igual: uma ação que talvez tenha saído já mexeu com a conta.
CONTAM = (InteractionStatus.pending.value, InteractionStatus.confirmed.value, InteractionStatus.uncertain.value)


@dataclass(slots=True)
class Verdict:
    allowed: bool = True
    reason: str = ""
    retry_at: str | None = None          # preenchido quando é só esperar
    policy: str = "autonomous"
    needs_approval: bool = False
    hint: str = ""                       # o que a pessoa faz para destravar, quando depende dela
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def is_wait(self) -> bool:
        return not self.allowed and self.retry_at is not None


class PolicyEngine:
    def __init__(self, repo: SocialRepository):
        self.repo = repo

    # ------------------------------------------------------------------ configuração do perfil
    def _config(self, profile_id: str) -> dict[str, Any]:
        row = self.repo.profile_row(profile_id)
        return loads(row["automation_policy"], {}) if row else {}

    def policy_for(self, profile_id: str, cap: Capability) -> str:
        """Política desta ação para este perfil. O perfil pode ENDURECER o padrão do catálogo, e é o que vale."""
        escolhido = (self._config(profile_id).get("capabilities") or {}).get(cap.key)
        return escolhido if escolhido in ("autonomous", "approval_required", "manual_only", "disabled") \
            else cap.default_policy

    def limits_for(self, profile_id: str) -> dict[str, int]:
        limites = dict(DEFAULT_LIMITS)
        for chave, valor in (self._config(profile_id).get("limits") or {}).items():
            if chave in limites and isinstance(valor, int) and valor >= 0:
                limites[chave] = valor
        return limites

    # ------------------------------------------------------------------ decisão
    def check(self, profile_id: str, cap: Capability, *, run_id: str | None = None) -> Verdict:
        politica = self.policy_for(profile_id, cap)
        if politica == "disabled":
            return Verdict(allowed=False, policy=politica,
                           reason=f"a ação {cap.key} está desligada para este perfil",
                           hint="Ligue esta ação nas configurações do perfil, se for mesmo para ela acontecer.")
        if politica == "manual_only":
            return Verdict(allowed=False, policy=politica,
                           reason=f"a ação {cap.key} é manual neste perfil",
                           hint="Faça esta ação pelo controle manual do aparelho, ou mude a política do perfil.")
        if not cap.side_effect or not cap.limit_bucket:
            return Verdict(policy=politica, needs_approval=politica == "approval_required")

        limites = self.limits_for(profile_id)
        agora = now()
        tipos = BUCKET_TYPES.get(cap.limit_bucket, ())
        teto = limites.get(f"{cap.limit_bucket}_per_hour", 0)
        janela = to_iso(agora - timedelta(hours=1))
        feitas = self.repo.count_interactions_since(profile_id, janela, types=tipos, statuses=CONTAM)
        contagem = {cap.limit_bucket: feitas}

        if teto and feitas >= teto:
            mais_antiga = self.repo.oldest_interaction_since(profile_id, janela, types=tipos, statuses=CONTAM)
            libera = (parse_iso(mais_antiga) + timedelta(hours=1)) if mais_antiga else (agora + timedelta(minutes=10))
            return Verdict(allowed=False, policy=politica, counts=contagem, retry_at=to_iso(libera),
                           reason=f"limite de {teto} {cap.limit_bucket} por hora atingido neste perfil ({feitas})")

        por_execucao = limites.get("actions_per_run", 0)
        if run_id and por_execucao:
            nesta = self.repo.count_run_interactions(profile_id, run_id, statuses=CONTAM)
            contagem["run"] = nesta
            if nesta >= por_execucao:
                return Verdict(allowed=False, policy=politica, counts=contagem,
                               reason=f"esta execução já fez {nesta} ações com efeito para este perfil "
                                      f"(limite {por_execucao})",
                               hint="Aumente o limite do perfil ou crie outra execução; nada foi perdido.")

        espera = limites.get("cooldown_between_external_actions_s", 0)
        ultima = self.repo.last_external_interaction_at(profile_id, statuses=CONTAM)
        if espera and ultima:
            livre = parse_iso(ultima) + timedelta(seconds=espera)
            if livre > agora:
                return Verdict(allowed=False, policy=politica, counts=contagem, retry_at=to_iso(livre),
                               reason=f"intervalo mínimo de {espera}s entre ações com efeito ainda não passou")
        return Verdict(policy=politica, needs_approval=politica == "approval_required", counts=contagem)
