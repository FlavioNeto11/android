"""Portas do aprendizado. Pertencem a quem CONSOME (o serviço); a infraestrutura as cumpre por tipagem estrutural e a
composição (`infrastructure/montagem.py`) liga as partes.

- `RepositorioDeAprendizado`: as tabelas da 055, com CAS em toda transição e a recusa do D1 no próprio `UPDATE`;
- `FontesDoLivro`: a LEITURA das fontes nativas (receita, fluxo, habilidade, memória) já no formato do livro;
- `TriagemDeTexto`: a regra de credencial do central (`security/redaction.py`), que o domínio não enxerga;
- `Minerador` e `PassoDeCuradoria`: o que os pacotes seguintes (A3–A9) registram no digest e na curadoria.

Os ajustes chegam como dado (`Ajustes`), não como o `Config` do central: a aplicação não importa `app.config`.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from app.modules.learning.domain.ciclo import Desligamento, SkillState
from app.modules.learning.domain.curador import Dossie
from app.modules.learning.domain.efeito import Exposicao
from app.modules.learning.domain.ensinado import AvisoDoEnsinado, DecisaoDoEnsinado, EsperaDoEnsinado
from app.modules.learning.domain.espera import AvisoDeEspera, FatosDoCatalogo
from app.modules.learning.domain.livro import EntradaDoLivro, ItemDeAprendizado, NovoItem, Transicao
from app.modules.learning.domain.parecer import RevisaoGravada
from app.modules.learning.domain.promocao import Evidencia
from app.modules.learning.domain.saude import LimiaresDeSaude
from app.modules.learning.domain.relacoes import Sucessora
from app.modules.learning.domain.versao import VersaoViva
from app.modules.learning.domain.vocabulario import (LivroKind, Modo, ModoDeTelas, Polaridade, Posicao, SignalKind)
from app.modules.skills.domain.document import JsonObject


# ------------------------------------------------------------------ ajustes (o bloco `aprendizado:` do config)
@dataclass(frozen=True, slots=True)
class Retencao:
    sinais_dias: int = 180
    feedback_dias: int = 365
    exposicoes_dias: int = 120
    evidencias_por_item: int = 200
    diario_dias: int = 400
    candidata_sem_evidencia_dias: int = 90


@dataclass(frozen=True, slots=True)
class Ajustes:
    enabled: bool = True
    curadoria_s: int = 900
    modo_licoes: Modo = Modo.SHADOW
    modo_telas: ModoDeTelas = ModoDeTelas.OBSERVE
    modo_voz: Modo = Modo.OFF
    modo_preferencias: Modo = Modo.OFF
    #: `aprendizado.licoes.por_app` / `aprendizado.telas.por_app` (§8.10): o modo de cada pacote que sobrescreve o
    #: global. Vazio = o global vale. O D1 do item lê o do pacote dele (`LearningService._modo_publica`).
    por_licoes: Mapping[str, Modo] = field(default_factory=dict)
    por_telas: Mapping[str, ModoDeTelas] = field(default_factory=dict)
    #: Dias recalculados em `learning_daily` a cada passo da curadoria (o dia de hoje e os anteriores).
    dias_recalculados: int = 3
    retencao: Retencao = field(default_factory=Retencao)
    #: Os limiares da saúde do item (30.4); o default é o do desenho (§5.3).
    saude: LimiaresDeSaude = field(default_factory=LimiaresDeSaude)


# ------------------------------------------------------------------ o que se grava
@dataclass(frozen=True, slots=True)
class NovoSinal:
    kind: SignalKind
    source_ref: str
    created_by: str
    polarity: Polaridade = Polaridade.NEUTRAL
    verdict: str | None = None
    reason: str | None = None
    note: str | None = None
    note_refused: bool = False
    run_id: str | None = None
    objective_id: str | None = None
    step_id: str | None = None
    attempt_id: str | None = None
    instance_id: str | None = None
    profile_id: str | None = None
    app_package: str = ""
    capability: str = ""
    step_hash: str | None = None
    failure_kind: str | None = None
    step_verified: bool | None = None
    data: JsonObject = field(default_factory=dict)
    simulated: bool = False


@dataclass(frozen=True, slots=True)
class NovaEvidencia:
    item_ref: str
    stance: Posicao
    origin_ref: str
    simulated: bool
    run_id: str | None = None
    instance_id: str | None = None
    app_version: str | None = None
    detail: str | None = None
    #: Quando o fato aconteceu, se não for agora (30.39: a retrocarga das reproduções datada pela tentativa, não pelo dia
    #: em que o passo rodou). `None`: o relógio do repositório.
    observed_at: str | None = None


@dataclass(frozen=True, slots=True)
class PrimeiraChamada:
    """A chamada de IA mais antiga que sobrou em `ai_calls`; `purgada_antes`: há tentativa real terminada antes dela,
    ou seja, a purga de `log_retention_days` já levou chamadas (a fronteira do que está inteiro é ela)."""

    ts: datetime
    purgada_antes: bool


@dataclass(frozen=True, slots=True)
class MudancaNativa:
    """Uma transição de status numa fonte nativa (receita ou fluxo), com o que a trilha precisa guardar."""

    kind: LivroKind
    ref: str
    de_status: str
    para_status: str
    de_estado: SkillState
    para_estado: SkillState
    content_hash: str | None
    scope_key: str
    app_version: str | None


# ------------------------------------------------------------------ portas
class RepositorioDeAprendizado(Protocol):
    def item(self, item_id: str) -> ItemDeAprendizado | None: ...
    def itens(self, *, kind: LivroKind | None = None, state: SkillState | None = None) -> list[ItemDeAprendizado]: ...
    def capabilities_dos_itens(self, item_ids: Sequence[str]) -> dict[str, str]:
        """A `scope_capability` de cada item pedido (id -> valor cru, vazio inclusive), em consulta em lote."""
        ...

    def item_vivo(self, novo: NovoItem) -> ItemDeAprendizado | None: ...
    def criar_item(self, novo: NovoItem, *, by: str, estado: SkillState, detalhe: str | None,
                   reason: str, run_id: str | None = None) -> ItemDeAprendizado: ...
    def transicionar_item(self, item: ItemDeAprendizado, para: SkillState, *, by: str, reason: str,
                          detalhe: str | None = None, run_id: str | None = None) -> ItemDeAprendizado: ...
    def mudar_detalhe(self, item: ItemDeAprendizado, detalhe: str, *, by: str, reason: str,
                      app_version: str | None = None, run_id: str | None = None) -> ItemDeAprendizado: ...
    def transicionar_nativo(self, mudanca: MudancaNativa, *, by: str, reason: str,
                            run_id: str | None = None, emenda_b: bool = False) -> None: ...
    def reclassificar_desligamento(self, mudanca: MudancaNativa, *, by: str, reason: str) -> None: ...
    def confirmar_que_fica(self, mudanca: MudancaNativa, *, by: str, reason: str) -> None: ...
    def trilha(self, item_ref: str) -> list[Transicao]: ...
    def desligamentos(self, content_hash: str, scope_key: str) -> list[Desligamento]: ...
    def trilha_do_escopo(self, scope_key: str) -> list[Transicao]: ...
    def trilhas_com_evidencia_invalida(self) -> dict[str, list[Transicao]]: ...
    def refs_decididas_por_pessoa(self, kinds: Sequence[LivroKind]) -> frozenset[str]: ...
    def ultimas_decisoes_da_pessoa(self, kinds: Sequence[LivroKind]) -> dict[str, Transicao]: ...
    def evidencias(self, item_ref: str, *, limite: int = 200) -> list[Evidencia]: ...
    def registrar_evidencia(self, nova: NovaEvidencia) -> bool: ...
    def exposicoes(self, item_id: str, *, desde: str | None = None, limite: int = 500) -> list[Exposicao]: ...
    def registrar_sinal(self, sinal: NovoSinal, *, substituir: bool = False,
                        um_por_evento: bool = False) -> int | None: ...
    def recalcular_diario(self, desde_dia: str, ate_dia: str) -> int: ...
    def dias_com_diario(self, desde_dia: str, ate_dia: str) -> frozenset[str]: ...
    def primeira_chamada(self) -> PrimeiraChamada | None: ...
    def aplicar_retencao(self, retencao: Retencao, agora: datetime) -> int: ...


class FontesDoLivro(Protocol):
    def receitas(self) -> list[EntradaDoLivro]: ...
    def fluxos(self) -> list[EntradaDoLivro]: ...
    def habilidades(self) -> list[EntradaDoLivro]: ...
    def memorias(self) -> list[EntradaDoLivro]: ...
    def receita(self, ref: str) -> EntradaDoLivro | None: ...
    def fluxo(self, ref: str) -> EntradaDoLivro | None: ...
    def habilidade(self, ref: str) -> EntradaDoLivro | None: ...
    def memoria(self, ref: str) -> EntradaDoLivro | None: ...
    def conteudo(self, kind: LivroKind, ref: str) -> JsonObject | None:
        """O conteúdo legível (30.3) de receita, fluxo ou habilidade, montado do que já está no banco; `None` nos
        outros tipos ou quando a linha sumiu."""
        ...

    def capabilities_das_receitas(self, refs: Sequence[str]) -> dict[str, str | None]:
        """A capability de cada receita pedida (ref -> nome ou `None`: sem fonte, ambígua ou etapa livre), pela MESMA
        regra do detalhe (`capability_da_receita`), em consultas em lote (nunca uma por receita)."""
        ...

    def versao(self, kind: LivroKind, ref: str) -> JsonObject | None:
        """O quadro de versão (30.6, `domain/versao.py`) de uma receita; `None` nos outros tipos ou sem a linha."""
        ...

    def vivas(self, app: str) -> tuple[VersaoViva, ...]:
        """As versões do app observadas hoje em aparelho ativo (o eixo de comparação do §7)."""
        ...

    def titulos_das_etapas(self, citadas: Sequence[tuple[str, int, str]]) -> dict[tuple[str, int, str], str]:
        """O título (texto do planejador, lido da execução na hora; nunca gravado no livro) de cada etapa citada por
        (execução, posição, chave). Acha a etapa por `steps.run_id` e `steps.key`, preferindo a mesma posição; sem a
        etapa (execução limpa pela retenção, chave que não existe mais) a citada fica fora do dicionário."""
        ...

    def pacotes_de_teste(self) -> frozenset[str]:
        """Os pacotes dos apps de teste (`apps.category='qa'`): a lista padrão do livro os esconde (RA-19)."""
        ...

    def sucessoras_da_habilidade(self, skill_id: str, versao: int) -> list[Sucessora]:
        """As versões da habilidade editadas a partir desta (`parent_version` = `versao`), para a relação
        `substituida_por` (30.7)."""
        ...


class TriagemDeTexto(Protocol):
    def recusa(self, texto: str) -> bool:
        """Formato OU assunto de credencial (e palavra com cara de senha ou código): não entra no livro."""
        ...

    def redigir(self, texto: str) -> str: ...


class Minerador(Protocol):
    """Roda no digest de UMA execução assentada. Devolve quantas linhas gravou. Nunca chama IA."""

    nome: str

    def minerar(self, run_id: str) -> int: ...


class PortaDeEventos(Protocol):
    """O que o Livro avisa ao mundo (30.21). A infraestrutura a cumpre sobre o barramento (`EventBus.emit`), e quem
    assina (o aviso do 28.11, a caixa de Pendências) não é conhecido daqui. Nunca levanta: avisar não derruba o gesto."""

    def esperando_a_pessoa(self, aviso: AvisoDeEspera) -> None: ...


class PortaDoEnsinado(Protocol):
    """30.80 B: o aviso do ensinado que o sistema tirou de uso (`learning.ensinado_rebaixado` ou
    `learning.ensinado_sem_receita`, pelo `AvisoDoEnsinado.tipo`). Porta separada da `PortaDeEventos` para os dublês
    do 30.21 não precisarem dela. A falha SOBE: cada chamador decide."""

    def ensinado_rebaixado(self, aviso: AvisoDoEnsinado) -> None: ...

    def ensinado_espera_decisao(self, aviso: EsperaDoEnsinado) -> None:
        """30.81: o fluxo ensinado que a prova automática não cobre espera a decisão de uma pessoa."""
        ...

    def ensinado_decidido(self, aviso: DecisaoDoEnsinado) -> None:
        """30.81: uma pessoa decidiu o ensinado que esperava (um por nascimento)."""
        ...


class LeitorDoEnsinado(Protocol):
    """30.80 B: o que só a fonte nativa sabe do ensinado. Lê DENTRO da transação de quem mudou o status (a loja ou o
    Livro): o que ele vê já é o estado novo."""

    def sessao_de_treino(self, kind: LivroKind, ref: str) -> str | None:
        """O id da sessão de treino que ensinou (`training:<id>` na fonte), ou `None`."""
        ...

    def tem_ativo_no_lugar(self, kind: LivroKind, ref: str) -> bool:
        """Outra receita ativa na mesma chave (pacote, versão, assinatura, variante, etapa), ou outro fluxo ativo no
        mesmo `match_key`."""
        ...

    def instante_da_transicao(self, kind: LivroKind, ref: str) -> str | None:
        """O `decided_at` da última linha da trilha do item: o instante da transição que acabou de ser gravada."""
        ...

    def espera_da_pessoa(self, kind: LivroKind, ref: str) -> str | None:
        """30.81: o fluxo ensinado, ativo, cuja prova automática passou à pessoa (o pedido `ensino:<sessão>` recusado
        com um motivo de `MOTIVOS_QUE_ESPERAM_A_PESSOA`) e que nenhuma pessoa decidiu desde o nascimento: o `desde`
        (o nascimento). `None` em todo o resto. Lido ANTES da decisão ser gravada."""
        ...

    def motivo_da_espera(self, kind: LivroKind, ref: str) -> str | None:
        """30.81: o motivo literal do pedido recusado que passou o ensinado à pessoa (o Livro o mostra ao lado do
        "Confirmar que fica"), nas mesmas condições de `espera_da_pessoa`."""
        ...

    def em_prova(self, kind: LivroKind, ref: str) -> dict[str, str | None] | None:
        """30.85: `{persona, sessao}` do fluxo ensinado que ainda espera a prova, pela MESMA regra do casamento e das
        outras respostas do 30.81 (`taskqueue.flows.ensinado_em_prova`); `None` em todo o resto."""
        ...


class CatalogoDeRisco(Protocol):
    """O que o catálogo de ações do app diz do risco, só em fatos (nada de texto de ação). Sem catálogo, `None`."""

    def tem_catalogo(self, app: str) -> bool: ...
    def da_capability(self, app: str, capability: str) -> FatosDoCatalogo | None: ...


class TitulosDoCatalogo(Protocol):
    """O `title` que o catálogo do app dá a uma capability (inclusive as internas), para o painel nomear o grupo em
    português. É texto do catálogo, não da execução. App sem catálogo ou capability desconhecida: `None`."""

    def titulo(self, app: str, capability: str) -> str | None: ...
    def apps_que_declaram(self, capability: str) -> tuple[str, ...]:
        """Os pacotes cujo catálogo declara a capability: o sinal sem app (a decisão de aprovação) acha o nome
        quando só um app a declara."""
        ...


# ------------------------------------------------------------------ o curador por IA (30.11, §8.5-8.8)
#: Tipos combinados com a frente Jev (o hub os espelha em `planning/schemas.py`). O hub valida só o JSON; a validação
#: do parecer (citação, vocabulário, classe) é do aprendizado (`domain/curador.validar_saida`).
ClasseDoPedido = Literal["A", "B", "C"]
ModeloSugerido = Literal["triagem", "escalada"]


@dataclass(frozen=True, slots=True)
class PedidoDeRevisao:
    dossie: JsonObject                      # `Dossie.como_dados()`: só fatos, já triado
    dossie_hash: str
    classe: ClasseDoPedido
    opcoes: dict[str, list[str]]            # `OPCOES_FECHADAS` + `opcoes_do_dossie` (citáveis e alvos)
    modelo_sugerido: ModeloSugerido


@dataclass(frozen=True, slots=True)
class RespostaDeRevisao:
    bruto: JsonObject                       # a escolha como veio; quem valida é `validar_saida`
    probabilidade: float | None             # a da escolha da decisão, medida pelo adaptador (nunca dita pela IA)
    modelo: str
    usd: float | None                       # o hub mede (30.12); aqui NUNCA se calcula custo à parte
    ai_call_id: int | None
    # Se ESTA resposta veio de provedor simulado. `None` = o adaptador não diz, e vale o `simulado` dele (o do hub o
    # atualiza por resposta; com revisões concorrentes, só este campo é seguro).
    simulado: bool | None = None
    # Quem respondeu ESTA revisão, pelo mesmo motivo do `simulado`: o `provedor` do adaptador é estado compartilhado entre
    # revisões concorrentes. `None` = o adaptador não diz, e vale o `provedor` dele.
    provedor: str | None = None


class RecusaDoProvedor(Exception):
    """O que o adaptador levanta quando o provedor não responde. O adaptador do hub (30.12) traduz o `AIError` dele
    nesta (a aplicação não importa `app.planning`). `kind == "budget"`: o hub cortou por orçamento, e o lote da volta
    para ali, sem nova tentativa em laço."""

    def __init__(self, mensagem: str, *, kind: str = "error") -> None:
        super().__init__(mensagem)
        self.kind = kind


class CuradorDeIA(Protocol):
    """Quem dá o PARECER sobre um item. Nunca decide nada: o parecer vai a `learning_reviews` e o aceite é da pessoa
    (`politica_de_risco.conferir_aceite`). `provedor` e `simulado` vão ao registro (`simulated = 1` nunca é prova)."""

    @property
    def provedor(self) -> str: ...
    @property
    def simulado(self) -> bool: ...

    def revisar(self, pedido: PedidoDeRevisao) -> RespostaDeRevisao: ...


@dataclass(frozen=True, slots=True)
class AjustesDoCurador:
    """`aprendizado.curador` do config, como dado."""

    modo: Modo = Modo.OFF
    intervalo_s: int = 3600
    cooldown_h: float = 24.0
    alfa: float = 0.10
    k: float = 1.5
    janela_dias: int = 7
    m_cmax: float = 4.0


@dataclass(frozen=True, slots=True)
class NovaRevisao:
    """Uma linha de `learning_reviews` (069 + `ai_call_id` da 075). `ai_call_id` e `usd` só vêm de chamada MEDIDA pelo
    hub (`RespostaDeRevisao.usd`); resposta simulada ou sem chamada grava 0. Até o 30.30 o `usd` não era gravado (saía
    0.0 nas linhas reais de 03/10, com +US$ 0,1182 no `/api/usage`) e o orçamento do curador estimava pelo dossiê."""

    item_ref: str
    item_kind: str
    scope_app: str
    gatilho: str
    dossie_hash: str
    dossie: JsonObject
    template_id: str
    template_versao: str
    provedor: str
    modelo: str
    simulated: bool
    validade: str
    saida: JsonObject | None
    classe_de_risco: str | None
    politica: str | None
    ai_call_id: int | None = None
    usd: float = 0.0


@dataclass(frozen=True, slots=True)
class LeituraDaJanela:
    """O que o orçamento precisa ler do banco (§8.7). A estimativa do gasto da curadoria sem medida é feita aqui pelo
    tamanho do dossiê GRAVADO (reprodutível) e só serve para decidir."""

    gasto_da_operacao: float
    custos_medidos: tuple[float, ...]
    tamanhos_sem_medida: tuple[int, ...]     # bytes do dossiê das revisões da janela sem `usd` medido
    tamanhos_sem_medida_na_hora: tuple[int, ...]
    gasto_medido: float
    gasto_medido_na_hora: float
    revisoes_antes_de_hoje: int
    revisoes_de_hoje: int


@dataclass(frozen=True, slots=True)
class PedidoGravado:
    """Um pedido de revisão de pessoa (sinal `pediu_revisao`, 30.17): o item e quando."""

    item_ref: str
    kind: str
    ref: str
    em: str


class RegistroDeRevisoes(Protocol):
    """`learning_reviews` (069): uma linha por revisão, nunca purgada."""

    def existe(self, item_ref: str, dossie_hash: str) -> bool: ...
    def ultima(self, item_ref: str) -> str | None:
        """`created_at` da revisão mais recente do item (para o cooldown); `None` se nunca revisado."""
        ...

    def gravar(self, nova: NovaRevisao, agora: datetime) -> str | None:
        """Grava e devolve o id (`lr-…`); `None` se (item, dossiê) já existia (corrida entre réplicas)."""
        ...

    def janela(self, agora: datetime, dias: int) -> LeituraDaJanela: ...
    def pedidos(self, desde: datetime) -> list[PedidoGravado]:
        """Os pedidos de revisão de pessoa desde a data (a fonte do gatilho `pedido_da_pessoa`)."""
        ...


class RegistroDePareceres(Protocol):
    """O lado de `learning_reviews` que a pessoa vê e decide (30.17). A transição e a decisão andam juntas
    (`transacao`); a decisão é CAS."""

    def transacao(self) -> AbstractContextManager[None]: ...
    def uma(self, review_id: str) -> RevisaoGravada | None: ...
    def do_item(self, item_ref: str, limite: int) -> list[RevisaoGravada]: ...
    def sem_decisao(self, item_refs: Sequence[str]) -> dict[str, list[RevisaoGravada]]: ...
    def do_dossie(self, item_ref: str, dossie_hash: str) -> RevisaoGravada | None: ...
    def decidir(self, review_id: str, *, decisao_final: str, decidido_por: str, transicao_id: int | None,
                override: bool, override_motivo: str | None) -> bool: ...
    def decidir_transicao(self, review_id: str, transicao_id: int | None) -> None: ...
    def ultima_transicao(self, item_ref: str) -> int: ...
    def transicao_depois(self, item_ref: str, depois_de: int) -> int | None: ...


class FonteDeDossies(Protocol):
    """Monta o dossiê (§8.2) de uma entrada do Livro, sem IA. `None` quando o item sumiu ou não tem dossiê (memória)."""

    def dossie(self, entrada: EntradaDoLivro, *, max_evidencias: int | None = None) -> Dossie | None: ...


class LacoPeriodico(Protocol):
    """Um laço do aprendizado que o `AppState` sobe à parte da curadoria, sob a trava de líder que ele passa."""

    nome: str

    async def laco(self, lider: Callable[[], int | None]) -> None: ...


class PassoDeCuradoria(Protocol):
    """Roda a cada passo da curadoria periódica. Idempotente (chaves únicas e CAS). Nunca chama IA."""

    nome: str

    def executar(self, agora: datetime) -> int: ...
