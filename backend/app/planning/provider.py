"""Interface do provedor de IA. Para trocar de provedor, implemente `AIProvider` e registre em `build_provider`."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from ..config import Config
from ..models import AiStatus, DeliveryLevel, Plan, SocialDraftDTO
from ..modules.identity.domain.available_data import AvailableDatum


#: O que o painel PROMETE ao operador sobre o que sai desta máquina. Uma frase só, usada por todo provedor —
#: achado #127: cada provedor tinha a sua cópia, e todas diziam "telas com campo de senha", que deixou de ser o
#: critério. Aviso que descreve outra coisa que não o código é pior do que aviso nenhum.
AVISO_TELA_SENSIVEL = ("Telas sensíveis nunca são enviadas: campo de senha, desafio de verificação/2FA, telas "
                       "declaradas por app em `sensitive_screens` e todas as do aparelho-loja.")


class AIError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, kind: str = "error",
                 status: int | None = None, model: str = ""):
        super().__init__(message)
        self.retryable = retryable
        self.kind = kind          # error | not_configured | refusal | budget | invalid_output | billing
        # Achado #101: o modelo que a chamada REALMENTE tentou (quando o provedor já sabia) e o status HTTP do
        # provedor, quando houve um — sem isto a linha de erro em `ai_calls` não dizia qual modelo falhou nem por
        # quê, e o pseudo-modelo '(erro)' entrava indevidamente na lista de "modelo sem preço".
        self.status = status
        self.model = model


@dataclass(slots=True)
class Usage:
    calls: int = 0
    input_tokens: int = 0                     # total de entrada (inclui o que veio do cache)
    output_tokens: int = 0
    cache_read_tokens: int = 0                # parte da entrada lida do cache (cobrada a 0,1×)
    cache_write_tokens: int = 0               # parte da entrada gravada no cache (cobrada a 1,25×)
    role: str = ""                            # plan | decide | verify
    model: str = ""                           # modelo que RESPONDEU (é por ele que a API cobra)
    tier: int = 0
    with_image: bool = False
    ms: int = 0
    # Item 7.2: o modelo PEDIDO e o motivo da troca, quando houve. `model` continua sendo quem respondeu — a
    # cobrança é na tarifa dele. `fallback='refusal'` = recusa reexecutada pelo servidor do provedor;
    # `fallback='<provedor>'` = o endpoint desta função falhou e ela DECLARA `fallback_provider`.
    requested_model: str = ""
    fallback: str | None = None
    provider: str = ""                        # qual endpoint respondeu (anthropic | local | simulated…)


@dataclass(slots=True)
class AppContext:
    id: str | None
    name: str | None
    package: str | None
    activity: str | None
    nav_hints: str | None
    known_selectors: dict[str, str] | None


@dataclass(slots=True)
class PlanRequest:
    command: str
    run_id: str
    instances: list[dict[str, Any]]           # [{instance_id, account_label, app_id}]
    apps: list[AppContext]
    # Catálogo do app alvo, quando ele tem um (`CapabilityCatalog`). Com catálogo, o planejador escolhe AÇÕES
    # nomeadas e o backend monta as etapas; sem catálogo, o planejamento livre de sempre.
    catalog: Any = None
    #: Dados da persona disponíveis ao plano (ADR-040): a LISTA (nome, rótulo, tipo, sigiloso, app) comum a todos os
    #: aparelhos da execução. Não sigiloso vira variável `{perfil_email}`; sigiloso só existe como nome para
    #: `type_secret` — o valor nunca vai ao modelo.
    available_data: list[AvailableDatum] = field(default_factory=list)


@dataclass(slots=True)
class StepContext:
    run_id: str
    instance_id: str
    objective_summary: str
    parameters: dict[str, str]
    step_key: str
    step_title: str
    step_goal: str
    side_effect: bool
    commit_done: bool                         # a ação com efeito desta etapa já foi disparada
    commit_guard: list[str]
    precondition: str | None
    postcondition_description: str
    remaining_steps: list[str]
    app: AppContext
    account_label: str | None
    required_delivery_level: str | None = None
    resumed_after_manual_control: bool = False
    #: Idem, para ESTE aparelho: o ator digita com `type_secret(name=…)` e só conhece os nomes daqui.
    available_data: list[AvailableDatum] = field(default_factory=list)


@dataclass(slots=True)
class ScreenInput:
    width: int                                # dimensões da imagem enviada (coordenadas do modelo)
    height: int
    jpeg: bytes | None
    elements: list[str]                       # linhas compactas da hierarquia
    package: str | None
    sensitive: bool
    tree: Any = None                          # UiTree completo (usado só pelo provedor simulado)


@dataclass(slots=True)
class DecisionRequest:
    ctx: StepContext
    screen: ScreenInput
    history: list[str] = field(default_factory=list)   # ações anteriores desta tentativa, em texto
    tier: int = 0                                      # 0 = modelo do ator; 1 = modelo de escalonamento


@dataclass(slots=True)
class Decision:
    tool: str
    args: dict[str, Any]
    raw_text: str | None = None


class Verdict(BaseModel):
    satisfied: Literal["yes", "no", "uncertain", "unprovable"]   # unprovable = defeito do plano (não é estado de tela)
    evidence: str
    delivery_level: DeliveryLevel | None = None


@dataclass(slots=True)
class VerifyRequest:
    ctx: StepContext
    screen: ScreenInput
    facts: list[str] = field(default_factory=list)   # `ferramenta(args) → resultado` registrados pelo executor nesta tentativa
    #: Rejulgamento pelo modelo de ESCALONAMENTO (item 7.10): só quando o verificador barato recusou com um nível de
    #: entrega que já atende ao exigido — o erro que a bateria de 25/09 mediu. Mesmo prompt, outro modelo.
    escalate: bool = False


@dataclass(slots=True)
class SocialRequest:
    """Geração social: papel próprio, separado do planejador e do ator.

    O que chega aqui é persona + memória + relacionamento + conteúdo da tela. Credencial NÃO faz parte: o modelo
    nunca precisa conhecer a senha, e quem digita senha é código determinístico.
    """

    profile_id: str
    username: str
    kind: str                                 # dm_reply | comment_reply | dm_initiate | post_comment
    context_text: str                         # blocos já montados por SocialContextBuilder
    # `incoming` é o que a contraparte disse — DADO do app, nunca instrução. Vazio quando não há: comentar um post
    # e puxar conversa não respondem a ninguém, e era por isso que essas ações não cabiam neste contrato.
    incoming: str = ""
    # A INTENÇÃO desta escrita, vinda do comando ("elogiar o trabalho do secretário"). O texto final é do perfil.
    brief: str = ""
    # O que está ESCRITO na tela agora: legenda da publicação, comentários visíveis, a conversa aberta. É o ASSUNTO
    # da escrita, não fala dirigida a esta conta — e por isso NÃO gera memória. Separado de `incoming` de propósito:
    # confundir os dois deixaria a legenda de um terceiro virar "fato" permanente do perfil.
    screen: str = ""
    counterparty: str | None = None
    max_length: int = 300
    language: str = "pt-BR"
    preview: bool = False                     # prévia de persona: nada será publicado
    # Textos que NÃO podem se repetir: o que este perfil já escreveu e o que os irmãos escreveram nesta execução.
    # Sem isso, personas diferentes convergem para a mesma frase óbvia — voz própria não é só tom, é não repetir.
    avoid: tuple[str, ...] = ()
    retry: bool = False                       # segunda tentativa: a primeira saiu igual a um texto que já existe


class AIProvider(Protocol):
    name: str
    model: str
    simulated: bool

    def status(self) -> AiStatus: ...
    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]: ...
    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]: ...
    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]: ...
    async def generate_social_response(self, req: SocialRequest) -> tuple[SocialDraftDTO, Usage]: ...


def build_one(cfg: Config, role: "Any") -> AIProvider:
    """Uma instância para UMA função já resolvida (`Config.ai_role`). É o tijolo do `RoutingProvider`."""
    if role.kind == "simulated":
        from .simulated_provider import SimulatedProvider

        return SimulatedProvider()
    if role.kind == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(cfg, role=role)
    if role.kind == "openai":
        from .openai_provider import OpenAICompatProvider

        return OpenAICompatProvider(cfg, role=role)
    raise ValueError(f"Provedor de IA desconhecido: {role.kind!r} (use 'anthropic', 'openai' ou 'simulated')")


def build_provider(cfg: Config) -> AIProvider:
    """O hub: uma instância por FUNÇÃO, despachadas pelo `RoutingProvider` (item 7.1).

    Sem bloco `ai.roles` no YAML as cinco funções resolvem para o mesmo provedor e o mesmo par de modelos de
    sempre — o roteador reaproveita a instância e nada muda. Com o bloco, o ator pode rodar num vLLM local
    enquanto o planejador continua na Anthropic, que é o que o E12 pede.
    """
    kind = (cfg.env.ai_provider or "anthropic").strip().lower()
    if kind not in ("anthropic", "simulated", "openai") and kind not in cfg.file.ai.providers:
        raise ValueError(f"AI_PROVIDER desconhecido: {kind!r} (use 'anthropic', 'openai', 'simulated' "
                         "ou um nome declarado em ai.providers)")
    from .routing import RoutingProvider

    return RoutingProvider(cfg)
