"""Política e limites por perfil.

Duas perguntas, respondidas antes de a etapa ser assumida:

1. **Esta ação é permitida para este perfil?** `AUTONOMOUS` roda sozinha; `APPROVAL_REQUIRED` espera uma pessoa
   aprovar o conteúdo; `MANUAL_ONLY` e `DISABLED` não rodam por automação de jeito nenhum.
2. **Ela cabe agora?** Curtir, comentar, seguir e mandar mensagem têm teto por hora E por dia, mais aquecimento
   para conta recém-cadastrada; e um alvo (`@fulano`) só recebe ações de um número limitado de contas da frota
   numa janela, espaçadas entre si (achado #114) — sem isto, 8 perfis seguindo ou mandando DM à mesma pessoa em
   poucos minutos é exatamente o padrão coordenado que faz o Instagram pedir verificação humana. O limite não
   existe para contornar nada do Instagram: existe para o sistema não agir como robô e derrubar a própria conta
   (nem a de ninguém que ela mexa).

Represar NÃO é falhar: a etapa volta para `retry_wait` com hora marcada, sem gastar tentativa e sem chamar modelo.
O tempo parado é descontado do prazo do objetivo — esperar não é demorar.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable

from ..db import loads
from ..models import InteractionStatus, InteractionType
from ..planning.capabilities import Capability
from ..util import now, parse_iso, to_iso
from .repository import SocialRepository

# Padrões conservadores. O perfil pode ENDURECER (nunca afrouxar sozinho os tetos de frota — esses moram em
# `LimitsCfg`, fora do alcance de `instagram_profiles.automation_policy`) em `instagram_profiles.automation_policy`.
DEFAULT_LIMITS: dict[str, int] = {
    "likes_per_hour": 30,
    "comments_per_hour": 8,
    "follows_per_hour": 8,
    "dms_per_hour": 15,
    # Tetos DIÁRIOS (achado #114): o teto por hora sozinho deixava passar um volume alto ao longo do dia, desde
    # que espaçado — que é justamente o padrão "devagar e sempre" mais difícil de perceber olhando só a hora.
    "likes_per_day": 150,
    "comments_per_day": 40,
    "follows_per_day": 40,
    "dms_per_day": 60,
    "actions_per_run": 20,
    "cooldown_between_external_actions_s": 45,
    # Aquecimento (achado #114): nos primeiros `warmup_days` de VIDA DO PERFIL NESTE SISTEMA (não a idade da
    # conta no Instagram, que não se sabe) — os tetos acima de hora/dia valem só `warmup_percent`% do normal.
    # `warmup_days=0` desliga.
    "warmup_days": 3,
    "warmup_percent": 34,
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


#: Ordem de rigor das políticas, da mais restritiva à mais livre — usada só para saber se um valor escolhido é
#: mais FROUXO que o padrão do catálogo (achado #114); não decide nada sozinha.
_POLICY_RANK = {"disabled": 0, "manual_only": 1, "approval_required": 2, "autonomous": 3}


def _valida(politica: Any) -> bool:
    return politica in _POLICY_RANK


def _limite_valido(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool) and valor >= 0


class PolicyEngine:
    def __init__(self, repo: SocialRepository, settings_getter: Callable[[], Any] | None = None, *,
                jitter: Callable[[float, float], float] = random.uniform):
        self.repo = repo
        # `None` (ex.: a instância que só monta o DTO em `SocialService`) desliga a coordenação de frota: ela só
        # importa no caminho de despacho de verdade (`AppState._policy_gate`), que sempre injeta o getter.
        self._settings = settings_getter
        self._jitter = jitter

    # ------------------------------------------------------------------ configuração do perfil
    # Três camadas, nesta ordem (migração 036): o que o PERFIL mudou deliberadamente (chave presente no
    # `automation_policy` dele) → o GRUPO de acesso a que ele pertence → o padrão do catálogo / `DEFAULT_LIMITS`.
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

    def origin_for(self, profile_id: str, cap: Capability) -> str:
        """De onde vem a política desta ação: `own` (o perfil mudou), `group` (herdada do grupo) ou `default`."""
        if _valida((self._own(profile_id).get("capabilities") or {}).get(cap.key)):
            return "own"
        if _valida((self._group(profile_id).get("capabilities") or {}).get(cap.key)):
            return "group"
        return "default"

    def policy_for(self, profile_id: str, cap: Capability) -> str:
        """Política desta ação para este perfil: a escolha própria, senão a do grupo, senão o padrão do catálogo.

        O perfil (e o grupo) pode escolher QUALQUER política válida — inclusive uma mais FROUXA que o padrão do
        catálogo (achado #114). Para uma ação de risco alto (`cap.risk == "high"`), afrouxar abaixo do padrão fica
        marcado — ver `ProfilePolicyDTO.loosened` e os avisos de `SocialService.set_policy`/grupos — em vez de ser
        silencioso.
        """
        for camada in (self._own(profile_id), self._group(profile_id)):
            escolhido = (camada.get("capabilities") or {}).get(cap.key)
            if _valida(escolhido):
                return escolhido
        return cap.default_policy

    @staticmethod
    def is_loosened(cap: Capability, politica: str) -> bool:
        """`True` quando a política EFETIVA do perfil é mais permissiva que o padrão do catálogo para esta ação."""
        return _POLICY_RANK.get(politica, 3) > _POLICY_RANK.get(cap.default_policy, 3)

    def limits_for(self, profile_id: str) -> dict[str, int]:
        limites = dict(DEFAULT_LIMITS)
        for camada in (self._group(profile_id), self._own(profile_id)):      # o próprio vem por último: sobrepõe
            for chave, valor in (camada.get("limits") or {}).items():
                if chave in limites and isinstance(valor, int) and valor >= 0:
                    limites[chave] = valor
        return limites

    def limits_origin(self, profile_id: str) -> dict[str, str]:
        proprio = self._own(profile_id).get("limits") or {}
        do_grupo = self._group(profile_id).get("limits") or {}
        return {k: "own" if _limite_valido(proprio.get(k)) else "group" if _limite_valido(do_grupo.get(k)) else "default"
                for k in DEFAULT_LIMITS}

    def _aquecendo(self, profile_id: str, limites: dict[str, int], agora: Any) -> bool:
        dias = limites.get("warmup_days", 0)
        if dias <= 0:
            return False
        linha = self.repo.profile_row(profile_id)
        criado = parse_iso(linha["created_at"]) if linha and linha["created_at"] else None
        return bool(criado) and (agora - criado) < timedelta(days=dias)

    def _teto_com_aquecimento(self, teto: int, limites: dict[str, int], aquecendo: bool) -> int:
        if not teto or not aquecendo:
            return teto
        pct = max(1, min(100, limites.get("warmup_percent", 100)))
        return max(1, teto * pct // 100)

    # ------------------------------------------------------------------ coordenação de frota (achado #114)
    def _fleet_gate(self, profile_id: str, cap: Capability, counterparty: str | None,
                    agora: Any) -> tuple[str, str] | None:
        """Quantas OUTRAS contas da frota mexeram com este mesmo alvo, e há pouco? `None` libera.

        Sem `settings_getter` (a instância de `SocialService` que só monta o DTO) ou sem alvo conhecido, não há
        o que coordenar — devolve `None` como sempre. Os tetos vêm de `LimitsCfg`, não do perfil: é regra da
        operação, não algo que uma conta afrouxa para si.
        """
        if self._settings is None or not counterparty or not cap.limit_bucket:
            return None
        s = self._settings()
        janela_s = int(getattr(s, "fleet_target_window_s", 0) or 0)
        max_contas = int(getattr(s, "fleet_max_accounts_per_target", 0) or 0)
        espaco_s = int(getattr(s, "fleet_min_spacing_between_accounts_s", 0) or 0)
        jitter_s = int(getattr(s, "fleet_spacing_jitter_s", 0) or 0)
        if not janela_s or not max_contas:
            return None
        tipos = BUCKET_TYPES.get(cap.limit_bucket, ())
        since = to_iso(agora - timedelta(seconds=janela_s))
        outras, ultima = self.repo.fleet_targeting(counterparty, since, types=tipos, statuses=CONTAM,
                                                    exclude_profile_id=profile_id)
        if outras >= max_contas:
            libera = to_iso(agora + timedelta(seconds=janela_s))
            return (f"{outras} outra(s) conta(s) da frota já mexeram com @{counterparty} na última "
                    f"{janela_s // 60} min (teto {max_contas}); coordenação entre contas sobre o mesmo alvo",
                    libera)
        if ultima and (espaco_s or jitter_s):
            espera = espaco_s + (self._jitter(0, jitter_s) if jitter_s else 0)
            livre = parse_iso(ultima) + timedelta(seconds=espera)
            if livre > agora:
                return (f"outra conta da frota mexeu com @{counterparty} há pouco; espaçando ações entre "
                        "contas sobre o mesmo alvo", to_iso(livre))
        return None

    # ------------------------------------------------------------------ decisão
    def check(self, profile_id: str, cap: Capability, *, run_id: str | None = None,
              counterparty: str | None = None) -> Verdict:
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
        aquecendo = self._aquecendo(profile_id, limites, agora)
        contagem: dict[str, int] = {}
        # `direction="outbound"`: limite é sobre o que ESTA conta faz. Desde que ler uma conversa passou a gravar o
        # que a contraparte disse, contar só por tipo faria a caixa de entrada consumir a cota de envio.
        for unidade, delta, rotulo in (("hour", timedelta(hours=1), cap.limit_bucket),
                                       ("day", timedelta(days=1), f"{cap.limit_bucket}_dia")):
            teto = self._teto_com_aquecimento(limites.get(f"{cap.limit_bucket}_per_{unidade}", 0), limites, aquecendo)
            janela = to_iso(agora - delta)
            feitas = self.repo.count_interactions_since(profile_id, janela, types=tipos, statuses=CONTAM,
                                                        direction="outbound")
            contagem[rotulo] = feitas
            if teto and feitas >= teto:
                mais_antiga = self.repo.oldest_interaction_since(profile_id, janela, types=tipos, statuses=CONTAM,
                                                                 direction="outbound")
                libera = (parse_iso(mais_antiga) + delta) if mais_antiga else (agora + timedelta(minutes=10))
                unidade_pt = "hora" if unidade == "hour" else "dia"
                aquecimento_txt = " (perfil em aquecimento)" if aquecendo else ""
                return Verdict(allowed=False, policy=politica, counts=contagem, retry_at=to_iso(libera),
                               reason=f"limite de {teto} {cap.limit_bucket} por {unidade_pt} atingido neste perfil "
                                      f"({feitas}){aquecimento_txt}")

        if (parado := self._fleet_gate(profile_id, cap, counterparty, agora)) is not None:
            return Verdict(allowed=False, policy=politica, counts=contagem, retry_at=parado[1],
                           reason=parado[0])

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
