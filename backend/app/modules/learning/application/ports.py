"""Portas do aprendizado. Pertencem a quem CONSOME (o serviço); a infraestrutura as cumpre por tipagem estrutural e a
composição (`infrastructure/montagem.py`) liga as partes.

- `RepositorioDeAprendizado`: as tabelas da 055, com CAS em toda transição e a recusa do D1 no próprio `UPDATE`;
- `FontesDoLivro`: a LEITURA das fontes nativas (receita, fluxo, habilidade, memória) já no formato do livro;
- `TriagemDeTexto`: a regra de credencial do central (`security/redaction.py`), que o domínio não enxerga;
- `Minerador` e `PassoDeCuradoria`: o que os pacotes seguintes (A3–A9) registram no digest e na curadoria.

Os ajustes chegam como dado (`Ajustes`), não como o `Config` do central: a aplicação não importa `app.config`.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.modules.learning.domain.ciclo import Desligamento, SkillState
from app.modules.learning.domain.efeito import Exposicao
from app.modules.learning.domain.espera import AvisoDeEspera, FatosDoCatalogo
from app.modules.learning.domain.livro import EntradaDoLivro, ItemDeAprendizado, NovoItem, Transicao
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
                            run_id: str | None = None) -> None: ...
    def trilha(self, item_ref: str) -> list[Transicao]: ...
    def desligamentos(self, content_hash: str, scope_key: str) -> list[Desligamento]: ...
    def refs_decididas_por_pessoa(self, kinds: Sequence[LivroKind]) -> frozenset[str]: ...
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


class CatalogoDeRisco(Protocol):
    """O que o catálogo de ações do app diz do risco, só em fatos (nada de texto de ação). Sem catálogo, `None`."""

    def tem_catalogo(self, app: str) -> bool: ...
    def da_capability(self, app: str, capability: str) -> FatosDoCatalogo | None: ...


class TitulosDoCatalogo(Protocol):
    """O `title` que o catálogo do app dá a uma capability (inclusive as internas), para o painel nomear o grupo em
    português. É texto do catálogo, não da execução. App sem catálogo ou capability desconhecida: `None`."""

    def titulo(self, app: str, capability: str) -> str | None: ...


class PassoDeCuradoria(Protocol):
    """Roda a cada passo da curadoria periódica. Idempotente (chaves únicas e CAS). Nunca chama IA."""

    nome: str

    def executar(self, agora: datetime) -> int: ...
