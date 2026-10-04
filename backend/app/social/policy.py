"""Política e limites por perfil.

Duas perguntas, respondidas antes de a etapa ser assumida:

1. **Esta ação é permitida para este perfil?** `AUTONOMOUS` roda sozinha; `APPROVAL_REQUIRED` espera uma pessoa
   aprovar o conteúdo; `MANUAL_ONLY` e `DISABLED` não rodam por automação de jeito nenhum.
2. **Ela cabe agora?** Curtir, comentar, seguir e mandar mensagem têm teto por hora E por dia, mais aquecimento
   para conta recém-cadastrada; e um alvo (`@fulano`) só recebe ações de um número limitado de contas da frota
   (achado #114, endurecido pelo ADR-055) — sem isto, 8 perfis seguindo ou mandando DM à mesma pessoa em poucos
   minutos é exatamente o padrão coordenado que faz o Instagram pedir verificação humana. O limite não existe para
   contornar nada do Instagram: existe para o sistema não agir como robô e derrubar a própria conta (nem a de
   ninguém que ela mexa).

**Uma conta por alvo (ADR-055, 29/09).** Em 19/09 (r-20260919220216-7cfa59) sete contas mandaram DM à mesma pessoa
em oito minutos; cinco das oito contas estão bloqueadas hoje. A regra do dono: seguir, mandar mensagem e comentar são
de NO MÁXIMO UMA conta por alvo; curtir tem teto configurável (`LimitsCfg.fleet_max_accounts_per_target`). Conta-se
QUALQUER ação de saída das outras contas sobre o alvo (todos os baldes), numa janela de DIAS, mais os pedidos de
aprovação ainda em aberto delas. O excedente é RECUSADO, com o motivo — não adiado: esperar uma hora e mandar a
segunda DM era só o mesmo padrão, mais devagar. E mensagem para quem nunca escreveu a esta conta ("DM fria") sempre
passa por aprovação, seja qual for a política do perfil ou do grupo.

Represar por limite de hora/dia NÃO é falhar: a etapa volta para `retry_wait` com hora marcada, sem gastar tentativa e
sem chamar modelo. O tempo parado é descontado do prazo do objetivo — esperar não é demorar.
"""
from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from ..db import loads
from ..models import InteractionStatus, InteractionType
from ..planning.capabilities import Capability, normalizar_alvo
from ..planning.catalog import pacote_ancora
from ..util import now, parse_iso, to_iso
from .contas_nossas import eh_conta_nossa, foi_retirada
from .repository import SocialRepository

# Padrões conservadores. O perfil pode ENDURECER (nunca afrouxar sozinho os tetos de frota — esses moram em
# `LimitsCfg`, fora do alcance de `instagram_profiles.automation_policy`) em `instagram_profiles.automation_policy`.
DEFAULT_LIMITS: dict[str, int] = {
    "likes_per_hour": 30,
    "comments_per_hour": 8,
    "follows_per_hour": 8,
    "dms_per_hour": 15,
    # 29.30: publicar no PRÓPRIO feed. Um por hora e um por dia: o piso do aquecimento (`max(1, …)`) mantém 1 em 1.
    "posts_per_hour": 1,
    # Tetos DIÁRIOS (achado #114): o teto por hora sozinho deixava passar um volume alto ao longo do dia, desde
    # que espaçado — que é justamente o padrão "devagar e sempre" mais difícil de perceber olhando só a hora.
    "likes_per_day": 150,
    "comments_per_day": 40,
    "follows_per_day": 40,
    "dms_per_day": 60,
    "posts_per_day": 1,
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
    "posts": (InteractionType.post_published.value,),
}

# Tentativa e efeito confirmado contam igual: uma ação que talvez tenha saído já mexeu com a conta.
CONTAM = (InteractionStatus.pending.value, InteractionStatus.confirmed.value, InteractionStatus.uncertain.value)

#: Toda ação de saída sobre uma pessoa, de qualquer balde: é o que diz que uma conta "já mexeu" com ela (ADR-055). Antes
#: a coordenação contava só o balde da própria ação, e a conta que curtiu a publicação não contava para quem seguia.
TODOS_OS_BALDES: tuple[str, ...] = tuple(dict.fromkeys(t for tipos in BUCKET_TYPES.values() for t in tipos))

#: Baldes em que o alvo é de UMA conta só (ADR-055, decisão do dono em 29/09). É regra, não configuração: nem o
#: `LimitsCfg` afrouxa. Curtir fica de fora: o teto dela é `LimitsCfg.fleet_max_accounts_per_target`.
UMA_CONTA_POR_ALVO: frozenset[str] = frozenset({"follows", "dms", "comments"})
_ROTULO_DO_BALDE = {"follows": "seguir", "dms": "mensagem direta", "comments": "comentário", "likes": "curtida",
                   "posts": "publicação"}

#: A interação que diz que a pessoa JÁ conversa com esta conta: ela escreveu a esta conta por mensagem direta.
_FALA_DELA_NA_DM = (InteractionType.dm_received.value,)


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


# ---------------------------------------------------------------- política POR APP (23.10)
#: Onde mora, dentro de um dicionário `capabilities` (o do perfil em `automation_policy` e a coluna do grupo), a
#: política dos apps que NÃO são o âncora: `{"LIKE_POST": …, "por_app": {"<pacote>": {"SEND_MESSAGE": …}}}`. As chaves
#: de ação não têm namespace entre catálogos — o Instagram usa SEND_MESSAGE, e um catálogo do Outlook (T17: enviar
#: e-mail `manual_only`) provavelmente também —, então um dicionário plano por `cap.key` faria "desligar mensagem no
#: Outlook" desligar também a DM do Instagram. O nível de fora continua sendo o do âncora: é o formato de todo dado
#: gravado até aqui (só o Instagram tinha catálogo), e por isso não há migração. Chave em minúsculas: ação de catálogo
#: é sempre maiúscula (`CapabilityCatalog.get`), então as duas nunca colidem.
CHAVE_POR_APP = "por_app"


def _e_do_ancora(package: str | None) -> bool:
    return package is None or package == pacote_ancora()


def _por_app(caps: Mapping[str, object]) -> dict[str, object]:
    # O JSON gravado é do painel e de versões antigas: um `por_app` que não seja objeto vale como vazio.
    bruto = caps.get(CHAVE_POR_APP)
    return dict(bruto) if isinstance(bruto, Mapping) else {}


def politicas_do_app(caps: Mapping[str, object] | None, package: str | None) -> dict[str, object]:
    """O recorte de um dicionário `capabilities` (perfil ou grupo) que vale para `package` (`None` = o âncora).

    Única porta de LEITURA: quem ler o dicionário cru mistura os apps de novo."""
    caps = caps or {}
    if package is None or _e_do_ancora(package):
        return {k: v for k, v in caps.items() if k != CHAVE_POR_APP}
    do_app = _por_app(caps).get(package)
    return dict(do_app) if isinstance(do_app, Mapping) else {}


def com_politicas_do_app(caps: Mapping[str, object] | None, package: str | None,
                         novas: Mapping[str, object]) -> dict[str, object]:
    """`caps` com o recorte de `package` trocado por `novas`; o dos outros apps fica intacto. Única porta de ESCRITA."""
    resultado = dict(caps or {})
    por_app = _por_app(resultado)
    if package is None or _e_do_ancora(package):
        resultado = dict(novas)
    elif novas:
        por_app[package] = dict(novas)
    else:
        por_app.pop(package, None)                        # app sem nenhuma escolha: não deixa `{}` para trás
    resultado.pop(CHAVE_POR_APP, None)
    if por_app:
        resultado[CHAVE_POR_APP] = por_app
    return resultado


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

    def origin_for(self, profile_id: str, cap: Capability, package: str | None = None) -> str:
        """De onde vem a política desta ação: `own` (o perfil mudou), `group` (herdada do grupo) ou `default`.

        `package` é o app da ação (`None` = o âncora): a mesma chave em dois catálogos são duas escolhas."""
        if _valida(politicas_do_app(self._own(profile_id).get("capabilities"), package).get(cap.key)):
            return "own"
        if _valida(politicas_do_app(self._group(profile_id).get("capabilities"), package).get(cap.key)):
            return "group"
        return "default"

    def policy_for(self, profile_id: str, cap: Capability, package: str | None = None) -> str:
        """Política desta ação para este perfil: a escolha própria, senão a do grupo, senão o padrão do catálogo.

        O perfil (e o grupo) pode escolher QUALQUER política válida — inclusive uma mais FROUXA que o padrão do
        catálogo (achado #114). Para uma ação de risco alto (`cap.risk == "high"`), afrouxar abaixo do padrão fica
        marcado — ver `ProfilePolicyDTO.loosened` e os avisos de `SocialService.set_policy`/grupos — em vez de ser
        silencioso. `package` escolhe o recorte do app (`politicas_do_app`); `None` é o âncora.
        """
        for camada in (self._own(profile_id), self._group(profile_id)):
            escolhido = politicas_do_app(camada.get("capabilities"), package).get(cap.key)
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

    # ------------------------------------------------------------------ coordenação de frota (achado #114, ADR-055)
    @staticmethod
    def teto_de_contas(cap: Capability, settings: object) -> int:
        """Quantas contas da frota podem mexer com a mesma pessoa nesta ação: 1 em seguir, mensagem e comentário
        (regra do dono, ADR-055); o teto configurado nas curtidas."""
        if cap.limit_bucket in UMA_CONTA_POR_ALVO:
            return 1
        return max(1, int(getattr(settings, "fleet_max_accounts_per_target", 1) or 1))

    def _fleet_gate(self, profile_id: str, cap: Capability, counterparty: str | None,
                    agora: Any, app_id: str | None = None) -> tuple[str, str | None, str] | None:
        """Esta conta pode mexer com este alvo, dado o que as OUTRAS contas da frota já fizeram com ele?

        `None` libera. Senão `(motivo, retry_at, dica)`: `retry_at=None` é RECUSA (o teto de contas por alvo foi
        atingido, ou não se sabe quem é o alvo); com hora, é só o espaçamento entre contas abaixo do teto (curtidas).

        Sem `settings_getter` (a instância de `SocialService` que só monta o DTO) não há o que coordenar. Os tetos vêm
        de `LimitsCfg` e da regra do dono, não do perfil: nenhuma conta afrouxa para si o que protege as outras.
        """
        if self._settings is None or not cap.side_effect or not cap.limit_bucket:
            return None
        alvo = normalizar_alvo(counterparty)
        if alvo is None:
            if not cap.counterparty:
                return None                  # ação sem alvo declarado (catálogo de outro app): nada a coordenar
            # Sem saber quem recebe a ação, a regra de uma conta por alvo não tem o que conferir — e agir assim é
            # exatamente o buraco medido no central (curtidas e comentários com `counterparty` NULL). Não se age.
            return (f"não se sabe quem é o alvo desta ação ({cap.counterparty} vazio): sem ele, a regra de uma conta "
                    "por alvo não tem como ser conferida", None,
                    f"Diga no comando quem recebe a ação (o @ em `{cap.counterparty}`, por exemplo o de quem publicou) "
                    "e refaça o plano.")
        nossa_viva = False
        if eh_conta_nossa(self.repo.db, alvo):
            if foi_retirada(self.repo.db, alvo):
                # Conta nossa RETIRADA por bloqueio (29.23/ADR-068) segue recusada: o produto sabe que o @ foi nosso, mas a
                # conta saiu da plataforma e ninguém interage com ela.
                return ("o alvo é uma conta nossa que foi retirada da plataforma: nada se faz com ela (ADR-050)", None,
                        "Escolha outro alvo: uma conta retirada não recebe curtida, comentário, seguir nem mensagem.")
            # Conta nossa VIVA pode receber a interação de outra conta nossa (emenda do ADR-050, 29.28, decisão do dono de
            # 02/10): passa pelas demais regras (política do perfil, aprovação, tetos, uma conta por alvo) e por um
            # espaçamento maior entre gestos públicos desta conta, abaixo.
            nossa_viva = True
        s = self._settings()
        dias = max(1, int(getattr(s, "fleet_target_window_days", 30) or 30))
        teto = self.teto_de_contas(cap, s)
        espaco_s = int(getattr(s, "fleet_min_spacing_between_accounts_s", 0) or 0)
        jitter_s = int(getattr(s, "fleet_spacing_jitter_s", 0) or 0)
        since = to_iso(agora - timedelta(days=dias))
        outras, ultima = self.repo.fleet_targeting(alvo, since, types=TODOS_OS_BALDES, statuses=CONTAM,
                                                    app_id=app_id, exclude_profile_id=profile_id)
        if outras >= teto:
            regra = ("uma conta por alvo" if teto == 1 else f"no máximo {teto} contas por alvo")
            return (f"{outras} outra(s) conta(s) da frota já mexeram com {alvo} nos últimos {dias} dias ou têm pedido "
                    f"em aberto para ele; em {_ROTULO_DO_BALDE.get(cap.limit_bucket, cap.limit_bucket)} vale {regra} "
                    "(ADR-055) — recusado, não adiado", None,
                    "Este alvo fica com a conta que já mexeu com ele. Não repita o pedido por outra conta; se for "
                    "mesmo o caso, uma pessoa faz à mão, fora da automação.")
        if ultima and (espaco_s or jitter_s):
            espera = espaco_s + (self._jitter(0, jitter_s) if jitter_s else 0)
            livre = parse_iso(ultima) + timedelta(seconds=espera)
            if livre > agora:
                return (f"outra conta da frota mexeu com {alvo} há pouco; espaçando ações entre contas sobre o "
                        "mesmo alvo", to_iso(livre), "")
        if nossa_viva:
            return self._espaco_entre_contas_nossas(profile_id, s, agora)
        return None

    def _espaco_entre_contas_nossas(self, profile_id: str, settings: object,
                                    agora: datetime) -> tuple[str, str, str] | None:
        """Interação entre contas NOSSAS vivas (29.28, emenda do ADR-050): ritmo baixo. O gesto com efeito desta conta tem de
        estar a pelo menos `fleet_min_spacing_to_own_account_s` (padrão 600 s) do último gesto com efeito DELA — o maior entre
        esse valor e `cooldown_between_external_actions_s` do perfil. `None` libera; senão `(motivo, retry_at, dica)`."""
        espaco = int(getattr(settings, "fleet_min_spacing_to_own_account_s", 600) or 0)
        espera = max(espaco, int(self.limits_for(profile_id).get("cooldown_between_external_actions_s", 0) or 0))
        ultima = self.repo.last_external_interaction_at(profile_id, statuses=CONTAM)
        if not espera or not ultima:
            return None
        livre = parse_iso(ultima) + timedelta(seconds=espera)
        if livre <= agora:
            return None
        return (f"o alvo é uma conta nossa: ritmo baixo entre contas da frota, no mínimo {espera}s desde o último gesto com "
                "efeito desta conta (ADR-050, emenda de 02/10)", to_iso(livre),
                "Espere o horário indicado: uma interação por vez entre contas nossas, nada em lote nem em laço.")

    def tem_conversa(self, profile_id: str, counterparty: str | None, app_id: str | None = None) -> bool:
        """A pessoa já escreveu a ESTA conta por mensagem direta? É o que separa responder de puxar conversa (DM fria).

        Uma DM que esta conta mandou e ficou sem resposta não conta: senão a primeira mensagem fria, uma vez aprovada,
        liberaria as seguintes sem ninguém olhar."""
        alvo = normalizar_alvo(counterparty)
        return bool(alvo) and self.repo.has_inbound_from(profile_id, alvo, types=_FALA_DELA_NA_DM, app_id=app_id)

    # ------------------------------------------------------------------ decisão
    def check(self, profile_id: str, cap: Capability, *, run_id: str | None = None,
              counterparty: str | None = None, app_id: str | None = None, package: str | None = None) -> Verdict:
        politica = self.policy_for(profile_id, cap, package)
        if politica == "disabled":
            return Verdict(allowed=False, policy=politica,
                           reason=f"a ação {cap.key} está desligada para este perfil",
                           hint="Ligue esta ação nas configurações do perfil, se for mesmo para ela acontecer.")
        if politica == "manual_only":
            return Verdict(allowed=False, policy=politica,
                           reason=f"a ação {cap.key} é manual neste perfil",
                           hint="Faça esta ação pelo controle manual do aparelho, ou mude a política do perfil.")
        # DM fria (ADR-055): mensagem para quem nunca escreveu a esta conta passa SEMPRE por uma pessoa, seja qual for
        # a política do perfil ou do grupo. Em 29/09 o grupo "Operação" deixava SEND_MESSAGE autônomo — foi revertido
        # no banco, mas um grupo não pode ter esse poder. Só endurece: `disabled`/`manual_only` já pararam acima.
        nota = ""
        if (cap.limit_bucket == "dms" or cap.interaction_type == InteractionType.dm_sent.value) \
                and politica == "autonomous" and not self.tem_conversa(profile_id, counterparty, app_id):
            politica = "approval_required"
            alvo = normalizar_alvo(counterparty) or "esta pessoa"
            nota = (f"mensagem para {alvo}, que ainda não conversa com esta conta: DM sem conversa prévia sempre "
                    "passa por aprovação (ADR-055)")
        if not cap.side_effect or not cap.limit_bucket:
            return Verdict(policy=politica, needs_approval=politica == "approval_required", reason=nota)

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

        if (parado := self._fleet_gate(profile_id, cap, counterparty, agora, app_id)) is not None:
            return Verdict(allowed=False, policy=politica, counts=contagem, retry_at=parado[1],
                           reason=parado[0], hint=parado[2])

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
        # `reason` num veredito que LIBERA é o porquê da aprovação exigida (a DM fria): quem abre o pedido o mostra.
        return Verdict(policy=politica, needs_approval=politica == "approval_required", counts=contagem,
                       reason=nota)
