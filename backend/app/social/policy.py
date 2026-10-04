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
from ..planning.capabilities import Capability, capability_of, normalizar_alvo, objeto_da_acao
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

#: Item 30.56: efeitos que ESTA conta faz uma vez só por pessoa na janela da frota (`fleet_target_window_days`). Fazer de
#: novo é duplicata, não outro gesto: em 04/10 o dono aprovou uma segunda resposta do lucas ao mesmo comentário do bruno
#: (a primeira era de 03/10) e só o ator, já na tela, recusou. A chave é (perfil, alvo, tipo, janela): a resposta não
#: grava a publicação (`thread_key` e `target` ficam nulos), então outra resposta à mesma pessoa noutro post também
#: espera a janela. Por AÇÃO, não por balde nem por tipo: `CREATE_COMMENT` grava o mesmo `comment_replied`, e comentar
#: em vários posts da mesma pessoa segue valendo — o histórico separa as duas pela etapa que gravou a interação.
#: Seguir fica de fora: o segundo FOLLOW é alternância (deixar de seguir), não duplicata.
UMA_VEZ_POR_ALVO: frozenset[str] = frozenset({"REPLY_COMMENT"})

def _balde(acao: str, package: str | None) -> str | None:
    """O balde de limite de uma ação pelo catálogo do app (`None` = o âncora); `None` se o catálogo não a tem."""
    cap = capability_of(package or pacote_ancora(), acao)
    return cap.limit_bucket if cap else None


#: 30.64: baldes em que o objeto é uma coisa na tela (post, comentário), não a pessoa: só a pessoa não diz QUAL.
_BALDES_DE_OBJETO_NA_TELA = frozenset({"likes", "comments"})


#: 30.64: a garantia do seletor exato só vale quando provada com a árvore de uma tela REAL gravada (anonimizada) de post
#: já curtido. Hoje a prova é do executor com o aparelho falso (`test_alvo_por_legenda.py`), que não prova o ambiente
#: real: a curtida ambígua vai para aprovação (decisão da orquestradora, 04/10 19:44Z). Destrava com a árvore real.
_SELETOR_DE_ESTADO_PROVADO_EM_TELA_REAL = False


def _seletor_de_estado(cap: Capability) -> bool:
    """O commit casa um ESTADO exato (`desc==Like`) que deixa de existir depois do efeito: o coração já curtido não
    casa, então repetir não alterna nem duplica. Seguraria a duplicata quando o objeto é ambíguo (30.64), mas só vale
    depois de provado com tela real (`_SELETOR_DE_ESTADO_PROVADO_EM_TELA_REAL`)."""
    seletor = (cap.commit_selector or "").strip()
    return _SELETOR_DE_ESTADO_PROVADO_EM_TELA_REAL and cap.limit_bucket == "likes" and seletor.startswith("desc==")


def _texto_normalizado(texto: object) -> str | None:
    """30.64: o texto para comparar repetição de mensagem: sem caixa e com os espaços colapsados; `None` sem texto."""
    limpo = " ".join(str(texto or "").split()).casefold()
    return limpo or None


def _data(iso: str) -> str:
    """`2026-10-03T05:51:53.472Z` → `03/10/2026 05:51Z`, para o motivo que a pessoa lê."""
    quando = parse_iso(iso)
    return quando.strftime("%d/%m/%Y %H:%MZ") if quando else iso


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


@dataclass(frozen=True, slots=True)
class ContextoDoPedido:
    """30.62 (28.10 F5): as personas de UM pedido entre personas (o raiz e os filhos) contam como uma conta só.

    `familia` são ids de `instagram_profiles` (o `profile_id` da porta). `porta_vozes`: vazio = nenhum declarado (o
    primeiro da família que tocou o alvo fica com ele); com um ou mais, SÓ eles tocam um alvo, e entre eles continua
    valendo uma conta por alvo. Um conjunto, e não "um ou nenhum": com dois porta-vozes, `None` caía no caso MENOS
    restrito (revisão da fila, 04/10). Quem monta é o laço dos pedidos (`contexto_do_pedido(run_id)`); sem contexto, a
    porta decide como sempre."""

    raiz: str
    familia: frozenset[str] = frozenset()
    porta_vozes: frozenset[str] = frozenset()


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

    def _um_so_da_familia(self, profile_id: str, counterparty: str | None, agora: datetime, app_id: str | None,
                          pedido: ContextoDoPedido) -> tuple[str, str] | None:
        """30.62 (a): uma conta por alvo no pedido INTEIRO, em todos os baldes (curtir também, que na frota aceita mais de
        uma). Com porta-vozes, só eles tocam o alvo; sem eles, a primeira persona da família que mexeu com o alvo (ou tem pedido
        em aberto sobre ele) na janela da frota fica com ele. `None` libera; senão `(motivo, dica)` de uma RECUSA.

        Alvo desconhecido não é daqui: a regra da frota (ADR-055) já recusa."""
        alvo = normalizar_alvo(counterparty)
        if alvo is None:
            return None
        if pedido.porta_vozes and profile_id not in pedido.porta_vozes:
            return ("neste pedido entre personas só o porta-voz toca o alvo; esta persona não age sobre "
                    f"{alvo} (30.62) — recusado, não adiado",
                    "Deixe a ação com o porta-voz do pedido; as outras personas só conversam entre si.")
        if not (pedido.familia - {profile_id}):
            return None
        dias = max(1, int(getattr(self._settings() if self._settings is not None else None,
                                  "fleet_target_window_days", 30) or 30))
        outras, _ultima = self.repo.fleet_targeting(alvo, to_iso(agora - timedelta(days=dias)), types=TODOS_OS_BALDES,
                                                    statuses=CONTAM, exclude_profile_id=profile_id, app_id=app_id,
                                                    only_profile_ids=pedido.familia)
        if outras:
            return (f"outra persona deste pedido já mexeu com {alvo} ou tem pedido em aberto para ele: no pedido inteiro "
                    "vale uma conta por alvo (30.62) — recusado, não adiado",
                    "O alvo fica com a persona do pedido que já mexeu com ele.")
        return None

    # ------------------------------------------------------------------ decisão
    def _ja_feito(self, profile_id: str, cap: Capability, counterparty: str | None, agora: datetime,
                  app_id: str | None, step_id: str | None) -> tuple[str, str] | None:
        """30.56: esta conta já fez este efeito com esta pessoa na janela, ou tem pedido dele em aberto? `None` libera;
        senão `(motivo, dica)` de uma RECUSA, antes do rascunho e da aprovação. A interação e o pedido da PRÓPRIA etapa
        (`step_id`) não contam: a porta roda de novo quando a etapa aprovada é retomada."""
        if cap.key not in UMA_VEZ_POR_ALVO or not cap.interaction_type or self._settings is None:
            return None
        alvo = normalizar_alvo(counterparty)
        if alvo is None:
            return None
        dias = max(1, int(getattr(self._settings(), "fleet_target_window_days", 30) or 30))
        since = to_iso(agora - timedelta(days=dias))
        dica = ("A resposta já está publicada; responder de novo publicaria uma segunda. Se for mesmo outro comentário "
                "da mesma pessoa, uma pessoa responde à mão, fora da automação.")
        feita = self.repo.ultima_saida_para(profile_id, alvo, types=(cap.interaction_type,), statuses=CONTAM,
                                            since=since, app_id=app_id, capability=cap.key, exclude_step_id=step_id)
        if feita is not None:
            interacao, quando = feita
            return (f"esta conta já respondeu a {alvo} em {_data(quando)} ({interacao}); em resposta a comentário vale "
                    f"uma vez por pessoa em {dias} dias (30.56) — recusado antes da aprovação", dica)
        pedido = self.repo.pedido_em_aberto_para(profile_id, cap.key, alvo, since=since, exclude_step_id=step_id)
        if pedido is not None:
            return (f"esta conta já tem um pedido de resposta a {alvo} em aberto ({pedido}); um segundo publicaria duas "
                    "respostas (30.56) — recusado antes da aprovação", "Decida o pedido que já está em Pendências.")
        return None

    def _repetido(self, profile_id: str, cap: Capability, bindings: Mapping[str, object] | None, agora: datetime,
                  app_id: str | None, step_id: str | None) -> tuple[bool, str, str] | None:
        """30.64: o MESMO perfil já fez, ou tem pedido em aberto de outra etapa (outra execução, inclusive), desta ação
        sobre o MESMO objeto (`objeto_alvo` do catálogo: o post, o comentário, a conversa, a mídia)? `None` segue;
        senão `(recusa, motivo, dica)`; `recusa=False` é "passa por aprovação".

        - Mensagem direta não se recusa (conversa continua): pede confirmação só com o MESMO texto (igualdade
          normalizada) já enviado ao alvo na janela, ou aprovado e ainda não enviado noutra etapa. Texto diferente segue
          a política do perfil, e o texto ainda por gerar se compara depois do rascunho (`mensagem_repetida`).
        - Objeto inequívoco (seguir, aceitar/recusar pedido, publicar a mesma imagem, curtir ou comentar o post com a
          legenda dita): RECUSA — faria o efeito duas vezes, ou o desfaria num toque que alterna.
        - Objeto AMBÍGUO (argumento declarado vazio, como a legenda do "post mais recente", ou só a pessoa numa ação sobre
          post ou comentário) nunca recusa: dois "comente no post mais recente de @ana" podem ser o mesmo post ou dois.
          Pede aprovação, salvo quando o seletor de commit EXATO de estado já impede a duplicata (`desc==Like` não casa
          com o coração já curtido): aí passa, e é o seletor que segura (revisão da fila da suíte 32, item 4).
        - Ação com efeito sem `objeto_alvo` declarado falha fechado: aprovação (a carga já exige a declaração).

        Uma saída sem etapa conhecida só conta quando o objeto é a própria pessoa (DM, seguir): de uma curtida antiga
        sem etapa não se sabe QUAL post foi."""
        if cap.key in UMA_VEZ_POR_ALVO or not cap.side_effect or not cap.interaction_type or self._settings is None:
            return None
        if not cap.objeto_alvo:
            return (False, f"{cap.key} tem efeito e não declara sobre o quê age (objeto_alvo): passa por aprovação "
                           "(30.64)", "")
        objeto = objeto_da_acao(cap, bindings)
        if objeto is None:
            return None                      # argumento por resolver (`{item}`): a porta roda de novo com ele
        so_a_pessoa = tuple(cap.objeto_alvo) == (cap.counterparty,)
        conversa = cap.limit_bucket == "dms" or cap.interaction_type == InteractionType.dm_sent.value
        ambiguo = not conversa and (any(not v for v in objeto.values())
                                    or (so_a_pessoa and cap.limit_bucket in _BALDES_DE_OBJETO_NA_TELA))
        if ambiguo and _seletor_de_estado(cap):
            return None                      # o commit exato não casa com o já feito: o seletor segura a duplicata
        texto = _texto_normalizado((bindings or {}).get("content"))
        if conversa and texto is None:
            return None                      # o texto ainda vai ser escrito: compara-se depois do rascunho

        def do_registro(argumentos: Mapping[str, object] | None, quem: str | None) -> dict[str, str] | None:
            if argumentos is not None:
                return objeto_da_acao(cap, argumentos)
            return {str(cap.counterparty): normalizar_alvo(quem) or ""} if so_a_pessoa else None

        def ambiguo_pede(onde: str) -> tuple[bool, str, str]:
            return (False, f"{cap.key} sobre {qual} sem dizer QUAL (objeto não identificado) e esta conta já {onde}: "
                           "pode ser o mesmo, passa por aprovação (30.64)", "")

        dias = max(1, int(getattr(self._settings(), "fleet_target_window_days", 30) or 30))
        since = to_iso(agora - timedelta(days=dias))
        qual = ", ".join(f"{k} {v}" for k, v in objeto.items() if v) or "o mesmo objeto"
        for interacao, quando, quem, argumentos, enviado in self.repo.saidas_da_acao(
                profile_id, cap.key, types=(cap.interaction_type,), statuses=CONTAM, since=since, app_id=app_id,
                exclude_step_id=step_id):
            if do_registro(argumentos, quem) != objeto:
                continue
            if conversa:
                if _texto_normalizado(enviado) != texto:
                    continue
                return (False, f"esta conta já mandou ESTA mensagem a {qual} em {_data(quando)} ({interacao}): a "
                               "repetição passa por confirmação (30.64)", "")
            if ambiguo:
                return ambiguo_pede(f"fez isso em {_data(quando)} ({interacao})")
            return (True, f"esta conta já fez {cap.key} sobre {qual} em {_data(quando)} ({interacao}); repetir faria o "
                          f"efeito duas vezes, ou o desfaria num toque que alterna (30.64) — recusado antes da aprovação",
                    "Se for mesmo outro item, diga no comando o que o distingue (a legenda do post, por exemplo) e refaça "
                    "o plano.")
        for pedido, quando, quem, argumentos, status, a_digitar in self.repo.pedidos_da_acao(
                profile_id, cap.key, since=since, app_id=app_id, exclude_step_id=step_id):
            if do_registro(argumentos, quem) != objeto:
                continue
            if conversa:
                if status not in ("approved", "edited") or _texto_normalizado(a_digitar) != texto:
                    continue
                return (False, f"esta mesma mensagem a {qual} já foi aprovada em {_data(quando)} ({pedido}), noutra "
                               "etapa, e ainda não saiu: a repetição passa por confirmação (30.64)", "")
            if ambiguo:
                return ambiguo_pede(f"tem pedido disso em aberto ({pedido})")
            return (True, f"esta conta já tem um pedido de {cap.key} sobre {qual} em aberto ({pedido}), noutra etapa; um "
                          "segundo faria o efeito duas vezes (30.64) — recusado antes da aprovação",
                    "Decida o pedido que já está em Pendências.")
        return None

    def mensagem_repetida(self, profile_id: str, cap: Capability, bindings: Mapping[str, object] | None, *,
                          app_id: str | None = None, step_id: str | None = None) -> str | None:
        """30.64 (revisão da fila, item 5): o `check` roda ANTES do rascunho, e a DM com texto gerado chegava sem texto
        a comparar. A porta chama isto DEPOIS do `_draft_gate`, com a etapa relida: o motivo quando o texto agora
        conhecido repete uma mensagem já enviada (ou aprovada e não enviada) ao mesmo alvo; `None` senão. Só lê."""
        if not (cap.limit_bucket == "dms" or cap.interaction_type == InteractionType.dm_sent.value):
            return None
        repetido = self._repetido(profile_id, cap, bindings, now(), app_id, step_id)
        return repetido[1] if repetido is not None and not repetido[0] else None

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
        # 30.60 (N4): publicação no feed tem o mesmo piso. O `default_policy: approval_required` do CREATE_POST era só o
        # padrão: um grupo `autonomous` publicava sem ninguém ver. Afrouxa só `LimitsCfg.publicar_sem_aprovacao`.
        configuracao = self._settings() if self._settings is not None else None
        if cap.limit_bucket == "posts" and cap.side_effect and politica == "autonomous" \
                and not getattr(configuracao, "publicar_sem_aprovacao", False):
            politica = "approval_required"
            nota = "; ".join(t for t in (nota, f"{cap.key} publica no feed: passa por aprovação mesmo com o perfil "
                                         "autônomo (30.60)") if t)
        # 30.62 (b): num pedido entre personas, efeito sobre pessoa real (não conta nossa) que ainda não conversa com esta
        # conta passa por uma pessoa, em qualquer ação, não só DM. Só endurece; sem pedido, nada muda.
        alvo_real = normalizar_alvo(counterparty) if pedido is not None and cap.side_effect else None
        if alvo_real is not None and politica == "autonomous" and not eh_conta_nossa(self.repo.db, alvo_real) \
                and not self.tem_conversa(profile_id, counterparty, app_id):
            politica = "approval_required"
            nota = "; ".join(t for t in (nota, f"{cap.key} para {alvo_real}, pessoa real sem conversa prévia com esta conta, "
                                         "dentro de um pedido entre personas: passa por aprovação (30.62)") if t)
        if not cap.side_effect or not cap.limit_bucket:
            return Verdict(policy=politica, needs_approval=politica == "approval_required", reason=nota)

        agora = now()
        # Recusa antes dos tetos: estes só ADIAM (`retry_at`), e o repetido não sai nem depois.
        if (feito := self._ja_feito(profile_id, cap, counterparty, agora, app_id, step_id)) is not None:
            return Verdict(allowed=False, policy=politica, reason=feito[0], hint=feito[1])
        if (repetido := self._repetido(profile_id, cap, bindings, agora, app_id, step_id)) is not None:
            recusa, motivo, dica = repetido
            if recusa:
                return Verdict(allowed=False, policy=politica, reason=motivo, hint=dica)
            if politica == "autonomous":
                politica = "approval_required"
            nota = "; ".join(t for t in (nota, motivo) if t)
        if pedido is not None and (da_familia := self._um_so_da_familia(profile_id, counterparty, agora, app_id,
                                                                         pedido)) is not None:
            return Verdict(allowed=False, policy=politica, reason=da_familia[0], hint=da_familia[1])
        limites = self.limits_for(profile_id)
        tipos = BUCKET_TYPES.get(cap.limit_bucket, ())
        aquecendo = self._aquecendo(profile_id, limites, agora)
        contagem: dict[str, int] = {}
        # 30.57: o pedido de aprovação ainda sem interação também ocupa o teto. Sem isto, os 5 itens de um `for_each`
        # viravam 5 pedidos ao dono com `comments_per_hour` 3: a porta só via o que já tinha saído, e nada sai antes
        # do sim. O da própria etapa não conta (a porta roda de novo na retomada).
        na_fila = [quando for acao, quando in self.repo.pedidos_em_aberto_desde(
            profile_id, to_iso(agora - timedelta(days=1)), exclude_step_id=step_id)
            if _balde(acao, package) == cap.limit_bucket]
        # `direction="outbound"`: limite é sobre o que ESTA conta faz. Desde que ler uma conversa passou a gravar o
        # que a contraparte disse, contar só por tipo faria a caixa de entrada consumir a cota de envio.
        for unidade, delta, rotulo in (("hour", timedelta(hours=1), cap.limit_bucket),
                                       ("day", timedelta(days=1), f"{cap.limit_bucket}_dia")):
            teto = self._teto_com_aquecimento(limites.get(f"{cap.limit_bucket}_per_{unidade}", 0), limites, aquecendo)
            janela = to_iso(agora - delta)
            saidas = self.repo.count_interactions_since(profile_id, janela, types=tipos, statuses=CONTAM,
                                                        direction="outbound")
            pedidos = sum(1 for quando in na_fila if quando >= janela)
            feitas = saidas + pedidos
            contagem[rotulo] = feitas
            if teto and feitas >= teto:
                mais_antiga = self.repo.oldest_interaction_since(profile_id, janela, types=tipos, statuses=CONTAM,
                                                                 direction="outbound")
                libera = (parse_iso(mais_antiga) + delta) if mais_antiga else (agora + timedelta(minutes=10))
                unidade_pt = "hora" if unidade == "hour" else "dia"
                aquecimento_txt = " (perfil em aquecimento)" if aquecendo else ""
                fila_txt = f", {pedidos} deles pedido(s) na fila de aprovação" if pedidos else ""
                return Verdict(allowed=False, policy=politica, counts=contagem, retry_at=to_iso(libera),
                               reason=f"limite de {teto} {cap.limit_bucket} por {unidade_pt} atingido neste perfil "
                                      f"({feitas}{fila_txt}){aquecimento_txt}")

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
