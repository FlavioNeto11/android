"""Interface do provedor de IA. Para trocar de provedor, implemente `AIProvider` e registre em `build_provider`."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config import Config
from ..models import AiStatus, DeliveryLevel, PersonaDraft, Plan, SocialDraftDTO
# O pedido de geração de persona mora no domínio de identidade (prompt e regras do rascunho ficam juntos, sem
# provedor); reexportado daqui para os provedores o importarem como importam `SocialRequest`.
from ..modules.identity.domain.persona_generation import PersonaGenerationRequest  # noqa: F401
from ..modules.identity.domain.available_data import AvailableDatum

if TYPE_CHECKING:
    from ..config import ResolvedRole
    from .capabilities import CapabilityCatalog


#: O que o painel PROMETE ao operador sobre o que sai desta máquina. Uma frase só, usada por todo provedor —
#: achado #127: cada provedor tinha a sua cópia, e todas diziam "telas com campo de senha", que deixou de ser o
#: critério. Aviso que descreve outra coisa que não o código é pior do que aviso nenhum.
AVISO_TELA_SENSIVEL = ("Telas sensíveis nunca são enviadas: campo de senha, desafio de verificação/2FA, telas "
                       "declaradas por app em `sensitive_screens` e todas as do aparelho-loja.")

#: Rótulo de cada função na frase "Modelos por função" do aviso de `/api/ai`, na ordem de sempre.
_ROTULOS_DAS_FUNCOES: Final = (("plan", "plano"), ("decide", "ação"), ("verify", "verificação"),
                               ("escalation", "escalonamento"), ("social", "social"), ("persona", "persona"))


def frase_dos_modelos(models: Mapping[str, str]) -> str | None:
    """A frase "Modelos por função — plano: X · ação: Y · …." do aviso; `None` se falta alguma função.

    O hub a refaz com a resolução EFETIVA de cada função (`ai.roles`), a mesma de `roles[]` e `models`: a instância do ator
    só conhece o próprio papel roteado e diria o `.env` nos demais (validação do deploy 8: o aviso dizia o Opus no plano
    enquanto o plano estava roteado ao Sonnet)."""
    if any(papel not in models for papel, _ in _ROTULOS_DAS_FUNCOES):
        return None
    return "Modelos por função — " + " · ".join(f"{rotulo}: {models[papel]}" for papel, rotulo in _ROTULOS_DAS_FUNCOES) + "."


#: Quem pediu a chamada de IA (item 31.2; migração 073, `ai_calls.origem`). Vocabulário FECHADO: o gasto por origem
#: alimenta a fatia do teto do dia (`_budget`) e o relatório do Livro, então uma grafia solta viraria gasto sem dono.
#: `execucao` = o laço da execução (há `run_id`); as demais nascem do portal ou de uma rotina e não têm `run_id`.
OrigemDeIA = Literal["execucao", "ensino", "orquestracao", "assistente", "social", "persona", "curador",
                     "decisao_fechada", "leitura"]
ORIGENS_DE_IA: tuple[str, ...] = ("execucao", "ensino", "orquestracao", "assistente", "social", "persona",
                                  "curador", "decisao_fechada", "leitura")

#: Qual régua de gasto barrou (item 31.6, decisão P6): o painel, o aviso e a 30.13 leem o MOTIVO, nunca a frase.
#: Só existe quando `kind="budget"`. `saldo` é o saldo da conta (ADR-051) e `kind="balance"` continua sendo o que o
#: `_saldo` levanta hoje; o valor fica no vocabulário para a etapa que o unificar, sem mudar o contrato de novo.
MotivoDeOrcamento = Literal["saldo", "dia", "fatia_curador", "fatia_jev", "fatia_leitura", "execucao", "pedido"]
MOTIVOS_DE_ORCAMENTO: tuple[str, ...] = ("saldo", "dia", "fatia_curador", "fatia_jev", "fatia_leitura", "execucao",
                                         "pedido")


class AIError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, kind: str = "error",
                 status: int | None = None, model: str = "", motivo: MotivoDeOrcamento | None = None):
        super().__init__(message)
        if motivo is not None and kind != "budget":
            raise ValueError("`motivo` só existe quando kind='budget'")
        if motivo is not None and motivo not in MOTIVOS_DE_ORCAMENTO:
            raise ValueError(f"motivo de orçamento desconhecido: {motivo!r}")
        self.motivo = motivo      # qual régua barrou; só com kind='budget' (ver MOTIVOS_DE_ORCAMENTO)
        self.retryable = retryable
        self.kind = kind          # error | not_configured | refusal | budget | invalid_output | billing | balance
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
    cache_write_1h_tokens: int = 0            # 31.31: parte de `cache_write_tokens` gravada por 1 h (cobrada a 2×)
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
    # Item 31.2 (migração 073): quem pediu a chamada (`ORIGENS_DE_IA`) e o id do item de origem, quando há um. Quem
    # preenche é o hub (`AIRouter._call`); `add_usage` grava. `None` = chamada que não passou pelo hub.
    origem: str | None = None
    ref: str | None = None
    # RA-10 (migração 080): o desfecho, por que subiu de modelo, para que foi feita e por que a imagem foi (ou não).
    # Quem preenche é o executor (`_ai`, com a `MarcaDaChamada`); os vocabulários são os de baixo.
    verdict: str | None = None
    escalate: str | None = None
    motivo: str | None = None
    image_reason: str | None = None
    # Item 31.24 (migração 088): quando a chamada foi entregue ao provedor (`ai_calls.started_at`, hora de parede; `ms`
    # continua só a ida e volta), a espera pela vaga de IA antes disso e o preparo da decisão do ator. Quem preenche é o
    # executor (`_ai`); fora dele, NULOS.
    started_at: str | None = None
    vaga_ms: int | None = None
    preparo: "PreparoDaDecisao | None" = None


#: RA-10 (migração 080): POR QUE a chamada foi ao modelo de escalonamento (`ai_calls.escalate`; NULO = não foi).
#: `decide`, na ordem do executor: efeito (política do efeito externo) · nova_tentativa · erros_seguidos ·
#: piso (alvo inexistente, item 7.8) · bloqueio (o modelo de ação relatou bloqueio e a mesma tela sobe uma vez, 17.10) ·
#: ciclo (ação repetida na mesma tela). `verify`: nivel (o barato recusou vendo um nível que já atende, 7.10) ·
#: sim_com_efeito (o "sim" do barato em etapa com efeito é conferido, 17.10).
MotivoDeEscalonamento = Literal["efeito", "nova_tentativa", "erros_seguidos", "piso", "bloqueio", "ciclo", "nivel",
                                "sim_com_efeito"]
#: RA-10: PARA QUE a chamada foi feita dentro do papel (`ai_calls.motivo`). São os grupos de `/api/usage`:
#: `verify` julgamento · rejulgamento · vazio (a coleta sem item, 12.4); `decide` decisao · cascata (a decisão no
#: modelo forte depois do bloqueio do barato); `plan` plano · refinamento (o assistente do comando, que também é `plan`
#: e grava na execução respondida); `leitura` leitura (a transcrição do recorte, 12.5).
MotivoDaChamada = Literal["julgamento", "rejulgamento", "vazio", "decisao", "cascata", "plano", "refinamento",
                          "leitura"]
#: RA-10: por que a imagem foi, ou não, junto (`ai_calls.image_reason`), na ordem de `StepExecutor._motivo_da_imagem`.
#: Sem imagem: sensivel · politica_nunca · arvore_rica. Com imagem: politica_sempre · pedida · problema ·
#: primeira_julgada · arvore_pobre. A coluna `with_image` diz se ela de fato foi (a captura pode falhar).
MotivoDaImagem = Literal["sensivel", "politica_nunca", "politica_sempre", "pedida", "problema", "primeira_julgada",
                         "arvore_pobre", "arvore_rica"]
MOTIVOS_DE_ESCALONAMENTO: Final[tuple[str, ...]] = get_args(MotivoDeEscalonamento)
MOTIVOS_DA_CHAMADA: Final[tuple[str, ...]] = get_args(MotivoDaChamada)
MOTIVOS_DA_IMAGEM: Final[tuple[str, ...]] = get_args(MotivoDaImagem)


@dataclass(slots=True)
class PreparoDaDecisao:
    """Item 31.24 (C-2 e C-1, migração 088): o que o executor gastou para chegar a UMA decisão do ator, em ms de
    `time.monotonic`, e a linha de `ai_calls` que a chamada gravou. `arvore_ms` e `imagem_ms` são partes de
    `observacao_ms`. O executor preenche os tempos antes de `_ai`; `_ai` devolve aqui o id da linha da resposta
    (`ai_call_id`), que a ação escolhida grava em `actions.ai_call_id`. Mutável de propósito: é um objeto por decisão,
    de ida e volta. Os limites de cada parte estão na migração 088."""
    settle_ms: int | None = None
    observacao_ms: int | None = None
    arvore_ms: int | None = None
    imagem_ms: int | None = None
    prompt_ms: int | None = None
    ai_call_id: int | None = None


@dataclass(frozen=True, slots=True)
class MarcaDaChamada:
    """RA-10: o que o EXECUTOR sabe de uma chamada e o provedor não — por que subiu de modelo, para que foi feita e por
    que a imagem foi. `StepExecutor._ai` a carimba no `Usage` da chamada (e na linha de erro ou de orçamento recusado,
    que também é desta chamada) antes de gravar; o veredito sai do resultado."""
    motivo: MotivoDaChamada | None = None
    escalate: MotivoDeEscalonamento | None = None
    image_reason: MotivoDaImagem | None = None


@dataclass(slots=True)
class AppContext:
    id: str | None
    name: str | None
    package: str | None
    activity: str | None
    nav_hints: str | None
    known_selectors: dict[str, str] | None
    #: `apps.category`. Só o executor lê (app de prova, item 29.31); o planejador monta o seu sem ela, e `None` = sem categoria.
    category: str | None = None
    #: `apps.builtin` (o app embutido da instalação). Junto com `category='qa'` define o app de prova (item 29.31).
    builtin: bool = False


@dataclass(slots=True)
class PlanRequest:
    command: str
    run_id: str
    instances: list[dict[str, Any]]           # [{instance_id, account_label, app_id}]
    apps: list[AppContext]
    # Catálogo do app alvo, quando ele tem um (`CapabilityCatalog`). Com catálogo, o planejador escolhe AÇÕES
    # nomeadas e o backend monta as etapas; sem catálogo, o planejamento livre de sempre.
    catalog: Any = None
    #: Planejamento ENTRE APPS (item 24.1, ADR-058): `{app_id: CapabilityCatalog}` dos apps candidatos que têm catálogo,
    #: quando o comando pode atravessar apps (cita outro app ou pede um site). Preenchido, ele manda: cada etapa diz o
    #: app dela, é AÇÃO do catálogo nos apps daqui e etapa LIVRE nos demais de `apps` (que então traz só os apps que
    #: o plano pode usar). Vazio = `catalog` ou o livre, exatamente como antes.
    catalogs: dict[str, CapabilityCatalog] = field(default_factory=dict)
    #: Dados da persona disponíveis ao plano (ADR-040): a LISTA (nome, rótulo, tipo, sigiloso, app) comum a todos os
    #: aparelhos da execução. Não sigiloso vira variável `{perfil_email}`; sigiloso só existe como nome para
    #: `type_secret` — o valor nunca vai ao modelo.
    available_data: list[AvailableDatum] = field(default_factory=list)
    #: Lições medidas do planejador (ADR-054, decisão 5): texto de modelo fechado, já cortado no teto do papel. É
    #: contexto, nunca ordem; sem lição, o pedido é o de antes. Quem monta é a costura `licoes_para` (A2); quem põe no
    #: prompt é o A7. O provedor que não as conhece as ignora.
    lessons: list[str] = field(default_factory=list)


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
    #: Lições medidas do ator (ADR-054, decisão 5), pedidas uma vez por tentativa. Aqui e NÃO em `StepContext`: o
    #: verificador recebe o mesmo `StepContext` (`VerifyRequest.ctx`), e lição no juiz o empurraria a aceitar
    #: (ADR-024). Vazio = o pedido de antes.
    lessons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Decision:
    tool: str
    args: dict[str, Any]
    raw_text: str | None = None


class Verdict(BaseModel):
    satisfied: Literal["yes", "no", "uncertain", "unprovable"]   # unprovable = defeito do plano (não é estado de tela)
    evidence: str
    delivery_level: DeliveryLevel | None = None
    #: 29.58 (C): quantas cópias do efeito DESTA execução a tela mostra (a mesma mensagem enviada duas vezes agora → 2).
    #: Interno: decide o fechamento `uncertain` "efeito repetido"; fora da serialização quando nulo.
    copias: int | None = Field(default=None, exclude_if=lambda v: v is None)


@dataclass(slots=True)
class VerifyRequest:
    ctx: StepContext
    screen: ScreenInput
    facts: list[str] = field(default_factory=list)   # `ferramenta(args) → resultado` registrados pelo executor nesta tentativa
    #: Rejulgamento pelo modelo de ESCALONAMENTO (item 7.10): só quando o verificador barato recusou com um nível de
    #: entrega que já atende ao exigido — o erro que a bateria de 25/09 mediu. Mesmo prompt, outro modelo.
    escalate: bool = False


#: Teto do que um leitor devolve (item 12.5): uma linha de lista de e-mail tem poucas linhas curtas; passar disto é
#: o modelo narrando em vez de transcrever, e vira recusa em vez de ser cortado calado.
TRANSCRICAO_LINHAS_MAX = 12
TRANSCRICAO_TEXTO_MAX = 400
#: Quantas saídas um pedido de leitura pode ter e o alfabeto do nome (o mesmo de `models.SAIDA_NOME_RE`).
LEITURA_SAIDAS_MAX = 20
_NOME_DE_SAIDA = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class Transcricao(BaseModel):
    """A leitura cega de um recorte (item 12.5, ADR-070): o que o segundo leitor VIU, sem conhecer o valor do ator.

    - `linhas`: o texto do recorte, linha a linha, como está escrito;
    - `campos`: para cada saída pedida, o trecho que a responde (`None` = não achei);
    - `legivel`: `False` quando não deu para ler (nada mais vale);
    - `truncado`: `True` quando o texto aparece cortado ("…", reticências, palavra pela metade na borda).
    O executor confere o valor do ator contra isto; quem transcreve nunca recebe o valor, o comando nem os fatos."""

    linhas: list[str] = []
    campos: dict[str, str | None] = {}
    legivel: bool = True
    truncado: bool = False

    def __repr__(self) -> str:
        """O texto transcrito é DADO DE TERCEIRO (o corpo de um e-mail pode trazer injeção de prompt ou um código): nunca
        entra num log, num evento nem numa mensagem de erro por acidente."""
        return f"Transcricao(linhas={len(self.linhas)}, legivel={self.legivel}, truncado={self.truncado}, conteudo=oculto)"

    __str__ = __repr__


class _CampoLido(BaseModel):
    model_config = ConfigDict(extra="forbid")      # chave fora do esquema recusa a resposta inteira

    nome: str
    valor: str | None = None


class TranscricaoWire(BaseModel):
    """O formato PEDIDO ao modelo: `campos` como lista de pares, porque um mapa de chaves livres não cabe na gramática
    estrita (`additionalProperties: false`). `para_transcricao` o converte no que o executor consome."""

    model_config = ConfigDict(extra="forbid")

    linhas: list[str]
    campos: list[_CampoLido]
    legivel: bool
    truncado: bool

    def para_transcricao(self) -> Transcricao:
        return Transcricao(linhas=self.linhas, campos={c.nome: c.valor for c in self.campos},
                           legivel=self.legivel, truncado=self.truncado)


@dataclass(slots=True)
class LeituraRequest:
    """Pedido ao papel `leitura`: SÓ o recorte (JPEG) e os nomes e descrições das saídas pedidas.

    De propósito não há valor do ator, comando, fatos, histórico nem `run_id` de conteúdo: o `run_id` existe só para o
    teto e a contabilidade do hub (`ai_calls`), e o provedor não o põe no prompt."""

    recorte: bytes
    saidas: dict[str, str]                    # nome → descrição ("remetente" → "quem enviou a mensagem")
    run_id: str | None = None

    def __post_init__(self) -> None:
        """Os nomes pedidos vêm do plano e vão ao prompt: só o alfabeto de saída e no máximo `LEITURA_SAIDAS_MAX`."""
        if not self.saidas or len(self.saidas) > LEITURA_SAIDAS_MAX or not all(
                _NOME_DE_SAIDA.fullmatch(n) for n in self.saidas):
            raise ValueError("saídas do pedido de leitura inválidas (nomes a-z0-9_ e no máximo "
                             f"{LEITURA_SAIDAS_MAX})")


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
    # Segunda tentativa porque a primeira atribuía fala, intenção ou recado a um terceiro ("seu marido mandou um oi",
    # r-20260919220216-7cfa59) — ADR-055. O prompt diz o que corrigir; a trava é `social/conteudo.py`.
    attribution_retry: bool = False


class AIProvider(Protocol):
    name: str
    model: str
    simulated: bool

    def status(self) -> AiStatus: ...
    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]: ...
    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]: ...
    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]: ...
    async def generate_social_response(self, req: SocialRequest) -> tuple[SocialDraftDTO, Usage]: ...
    async def generate_persona(self, req: PersonaGenerationRequest) -> tuple[PersonaDraft, Usage]: ...
    async def transcribe(self, req: LeituraRequest) -> tuple[Transcricao, Usage]: ...


def modelo_do_papel_leitura(cfg: Config, role: ResolvedRole | None) -> str:
    """O modelo do papel `leitura` (item 12.5), SEM fallback para o do ator: `AIError(not_configured)` quando a instância
    não é a do papel (o executor recusa com `sem_leitor`). Com `role` (dentro do hub), é o da instância e ela precisa ser
    do papel `leitura`; sem `role` (provedor único), é o de `ai.roles.leitura`. O recorte é um dado de terceiros que só o
    segundo leitor, de modelo declarado e independente do ator, pode receber (ADR-070)."""
    escolhido = role if role is not None else cfg.ai_leitura()
    if escolhido is None or escolhido.role != "leitura" or not escolhido.model:
        raise AIError("Não há modelo declarado para o papel leitura (ai.roles.leitura): a leitura visual não cai no "
                      "modelo do ator.", kind="not_configured")
    return escolhido.model


def transcricao_from_json(raw: str, pedidas: Sequence[str]) -> Transcricao:
    """JSON do modelo → `Transcricao`, com parse ESTRITO (item 12.5; requisitos do hub de IA).

    - JSON inválido, chave fora do esquema (`extra="forbid"`), tipo errado ou campo pedido repetido com valores diferentes →
      `AIError(kind="invalid_output")` (sem encadear a exceção do pydantic: ela traz o texto do modelo): nunca
      `legivel=False` (isso é o MODELO afirmando que não leu) e nunca sucesso;
    - só os campos PEDIDOS entram em `campos`; o pedido que não veio fica `None`;
    - teto de linhas e de caracteres: o excesso é CORTADO e marca `truncado=True` (nunca passa calado, e um valor cortado
      não concorda com o do ator)."""
    texto = (raw or "").strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```[a-zA-Z]*\s*", "", texto)
        texto = re.sub(r"\s*```$", "", texto).strip()
    fio: TranscricaoWire | None = None
    try:
        fio = TranscricaoWire.model_validate(json.loads(texto))
    except (ValueError, ValidationError):
        pass
    if fio is None:
        # Levantado FORA do `except`, de propósito: a `ValidationError` do pydantic carrega `input_value=` com o texto que o
        # MODELO devolveu (o corpo de um e-mail, um código) e o executor loga esta falha com `exc_info=True`. Dentro do
        # `except`, mesmo `from None` deixa a falha em `__context__` (um serializador que o percorra vazaria o valor); aqui
        # `__cause__` e `__context__` ficam `None`.
        raise AIError("A transcrição devolvida pelo leitor não tem o formato pedido.", kind="invalid_output")
    # Um nome pedido que vem duas vezes com valores diferentes é resposta incoerente: o mapa guardaria só a última e a
    # conferência passaria a depender da ordem. A mensagem não cita nome nem valor.
    vistos: dict[str, str | None] = {}
    for c in fio.campos:
        if c.nome in pedidas and c.nome in vistos and vistos[c.nome] != c.valor:
            raise AIError("A transcrição devolvida pelo leitor repete um campo pedido com valores diferentes.",
                          kind="invalid_output")
        vistos[c.nome] = c.valor
    t = fio.para_transcricao()
    cortou = len(t.linhas) > TRANSCRICAO_LINHAS_MAX or any(len(x) > TRANSCRICAO_TEXTO_MAX for x in t.linhas)
    linhas = [x[:TRANSCRICAO_TEXTO_MAX] for x in t.linhas[:TRANSCRICAO_LINHAS_MAX]]
    campos: dict[str, str | None] = {}
    for n in pedidas:
        valor = (t.campos.get(n) or None) if n in t.campos else None
        if valor is not None and len(valor) > TRANSCRICAO_TEXTO_MAX:
            valor, cortou = valor[:TRANSCRICAO_TEXTO_MAX], True
        campos[n] = valor
    return Transcricao(linhas=linhas, campos=campos, legivel=t.legivel, truncado=t.truncado or cortou)


def persona_draft_from_json(raw: str) -> PersonaDraft:
    """JSON do modelo → `PersonaDraft`, revalidado por Pydantic: a gramática do provedor acelera, não garante. Um
    servidor sem `json_schema` costuma embrulhar em cerca de código; desembrulhar é mais barato que pedir de novo.
    Mora aqui (e não em `parsing.py`) para não abrir mais um import tardio no ciclo do pacote."""
    texto = (raw or "").strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```[a-zA-Z]*\s*", "", texto)
        texto = re.sub(r"\s*```$", "", texto).strip()
    try:
        dados = json.loads(texto)
    except ValueError as exc:
        raise AIError(f"Rascunho de persona não é JSON: {exc}", kind="invalid_output") from exc
    try:
        return PersonaDraft.model_validate(_vazio_e_nulo(dados))
    except ValidationError as exc:
        raise AIError(f"Rascunho de persona inválido devolvido pelo modelo: {exc}", kind="invalid_output") from exc


def _vazio_e_nulo(valor: object) -> object:
    """`""` num campo de objeto vira `None`: a gramática do provedor pede string onde o modelo aceita `str | None`
    (`automation/tools.py::strings_anulaveis_como_vazias`), e "não sei" chega como `""`. Listas ficam como vieram."""
    if isinstance(valor, dict):
        return {k: (None if v == "" else _vazio_e_nulo(v)) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_vazio_e_nulo(v) for v in valor]
    return valor


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
