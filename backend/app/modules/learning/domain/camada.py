"""A camada de uso em runtime (aprendizado vivo §3.3, 30.1): o que cada conhecimento FAZ hoje, derivado do tipo, do
estado e dos modos vigentes. É uma função pura e só no backend; o painel exibe o rótulo e o "porquê".

Os modos são os do pacote: `aprendizado.licoes.modo` e `aprendizado.telas.modo` com o override de `por_app` (§8.10,
30.20; `ModosDeUso.do_pacote`); `ai.recipes`, `ai.flows` e `skills.enabled` só existem globais. Um modo que a
composição não soube ler vem `None`, e a camada que depende dele
sai `desconhecida`: nunca um palpite.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum

from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.modo_por_app import modo_efetivo
from app.modules.learning.domain.vocabulario import KINDS_DE_ITEM, LivroKind, Modo, ModoDeTelas, absorvida_em


class Camada(StrEnum):
    DECIDE_SEM_IA = "decide_sem_ia"
    VAI_AO_PROMPT = "vai_ao_prompt"
    CLASSIFICA_TELA = "classifica_tela"
    LOGIN_FORA_DA_IA = "login_fora_da_ia"
    PRE_PREENCHE = "pre_preenche"
    CONTEXTO_DA_PERSONA = "contexto_da_persona"
    MEDIDO_NAO_USADO = "medido_nao_usado"
    NAO_MEDIDO = "nao_medido"
    INERTE = "inerte"
    DESCONHECIDA = "desconhecida"


class OrigemNaVisao(StrEnum):
    """§3.2: de onde vem a linha na visão de um app."""

    DECLARADO = "declarado"
    APRENDIDO = "aprendido"
    ABSORVIDO = "absorvido"


class Existencia(StrEnum):
    """§3.1: por que o app está na lista."""

    DECLARADO = "declarado"             # pasta no repositório (registro de apps)
    LOJA = "loja"                       # só na tabela `apps`
    SO_APRENDIDO = "so_aprendido"       # só aparece em linhas do livro


class ArquivoDeclarado(StrEnum):
    APP = "app"
    CATALOGO = "catalogo"
    TELAS = "telas"
    SESSAO = "sessao"
    LOJA = "loja"                       # `apps.nav_hints` e `apps.known_selectors`


@dataclass(frozen=True, slots=True)
class Uso:
    camada: Camada
    porque: str


@dataclass(frozen=True, slots=True)
class ModosDeRuntime:
    """Os modos que moram FORA do bloco `aprendizado:` do config. `None` = a composição não soube ler."""

    receitas: str | None = None         # off | shadow | replay (`ai.recipes`)
    fluxos: bool | None = None          # `ai.flows`
    habilidades: bool | None = None     # `skills.enabled`


@dataclass(frozen=True, slots=True)
class ModosDeUso:
    receitas: str | None = None
    fluxos: bool | None = None
    habilidades: bool | None = None
    licoes: Modo | None = None
    telas: ModoDeTelas | None = None
    #: `aprendizado.licoes.por_app` e `aprendizado.telas.por_app` (§8.10): o override de cada pacote.
    licoes_por_app: Mapping[str, Modo] = field(default_factory=dict)
    telas_por_app: Mapping[str, ModoDeTelas] = field(default_factory=dict)

    def do_pacote(self, pacote: str) -> ModosDeUso:
        """Os mesmos modos com lições e telas no modo EFETIVO do pacote (o override, senão o global); um modo global
        que a composição não soube ler (`None`) continua desconhecido: o override não o adivinha."""
        return replace(
            self, licoes=None if self.licoes is None else modo_efetivo(self.licoes, self.licoes_por_app, pacote),
            telas=None if self.telas is None else modo_efetivo(self.telas, self.telas_por_app, pacote))


def _desconhecida(modo: str) -> Uso:
    return Uso(Camada.DESCONHECIDA, f"não foi possível ler {modo} neste processo")


def _inerte_do_estado(estado: SkillState | None) -> Uso:
    return Uso(Camada.INERTE, f"estado '{estado.value if estado else '-'}': fora de circulação ou ainda sem prova")


def _espera_o_dono() -> Uso:
    return Uso(Camada.INERTE, "inerte: validada, espera o dono publicar (tem efeito externo, D1)")


def _receita(estado: SkillState | None, m: ModosDeUso) -> Uso:
    if estado is SkillState.VALIDATED:
        return _espera_o_dono()
    if estado not in (SkillState.PUBLISHED, SkillState.CANDIDATE):
        return _inerte_do_estado(estado)
    if m.receitas is None:
        return _desconhecida("ai.recipes")
    if m.receitas == "off":
        return Uso(Camada.INERTE, "ai.recipes=off: nenhuma receita é gravada, comparada nem reproduzida")
    if estado is SkillState.CANDIDATE:
        return Uso(Camada.MEDIDO_NAO_USADO, "roda em sombra; promove com `recipes_promote_after` concordâncias")
    if m.receitas == "replay":
        return Uso(Camada.DECIDE_SEM_IA, "reproduz por seletor; se diverge, a IA assume (ai.recipes=replay)")
    return Uso(Camada.MEDIDO_NAO_USADO, "ai.recipes=shadow: compara com a IA, mas não age")


def _fluxo(estado: SkillState | None, m: ModosDeUso) -> Uso:
    if estado is SkillState.VALIDATED:
        return _espera_o_dono()
    if estado is SkillState.CANDIDATE:
        return Uso(Camada.MEDIDO_NAO_USADO, "sombra dos fluxos: mede a concordância antes de agir")
    if estado is not SkillState.PUBLISHED:
        return _inerte_do_estado(estado)
    if m.fluxos is None:
        return _desconhecida("ai.flows")
    if m.fluxos:
        return Uso(Camada.DECIDE_SEM_IA, "substitui o planejador para o comando (ai.flows)")
    return Uso(Camada.INERTE, "ai.flows desligado: o planejador decide")


def _habilidade(estado: SkillState | None, m: ModosDeUso) -> Uso:
    if estado is SkillState.VALIDATED:
        return _espera_o_dono()
    if estado is not SkillState.PUBLISHED:
        return _inerte_do_estado(estado)
    if m.habilidades is None:
        return _desconhecida("skills.enabled")
    if m.habilidades:
        return Uso(Camada.DECIDE_SEM_IA, "resolve o comando antes do fluxo (skills.enabled)")
    return Uso(Camada.INERTE, "skills.enabled desligado: a habilidade não resolve comando")


def _licao(estado: SkillState | None, m: ModosDeUso) -> Uso:
    if estado is not SkillState.PUBLISHED:
        return _inerte_do_estado(estado)
    if m.licoes is None:
        return _desconhecida("aprendizado.licoes.modo")
    if m.licoes is Modo.ON:
        return Uso(Camada.VAI_AO_PROMPT, "entra no prompt do ator e do planejador, com braço de controle e teto de "
                                         "tokens")
    if m.licoes is Modo.SHADOW:
        return Uso(Camada.NAO_MEDIDO, "modo shadow não grava exposição: nem a medida existe")
    return Uso(Camada.INERTE, "aprendizado.licoes.modo=off")


def _tela(estado: SkillState | None, m: ModosDeUso) -> Uso:
    if estado not in (SkillState.PUBLISHED, SkillState.CANDIDATE, SkillState.VALIDATED):
        return _inerte_do_estado(estado)
    if m.telas is None:
        return _desconhecida("aprendizado.telas.modo")
    if m.telas is ModoDeTelas.OFF:
        return Uso(Camada.INERTE, "aprendizado.telas.modo=off")
    if estado is SkillState.PUBLISHED and m.telas is ModoDeTelas.ON:
        return Uso(Camada.CLASSIFICA_TELA, "a sessão a consome ao classificar a tela (sem prompt)")
    return Uso(Camada.MEDIDO_NAO_USADO, "grava, minera e valida; a sessão não a consome")


def uso_do_item(kind: LivroKind, estado: SkillState | None, modos: ModosDeUso, *, detalhe: str | None = None) -> Uso:
    """O que este conhecimento aprendido faz hoje. `detalhe` é o `state_detail` (item absorvido não decide mais:
    a regra declarada que o absorveu decide)."""
    if kind in KINDS_DE_ITEM and absorvida_em(detalhe) is not None:
        return Uso(Camada.INERTE, "absorvido: a regra declarada no repositório decide")
    if kind is LivroKind.MEMORIA:
        return Uso(Camada.CONTEXTO_DA_PERSONA, "contexto da persona; onde entra no prompt social: a conferir")
    if kind is LivroKind.RECEITA:
        return _receita(estado, modos)
    if kind is LivroKind.FLUXO:
        return _fluxo(estado, modos)
    if kind is LivroKind.HABILIDADE:
        return _habilidade(estado, modos)
    if kind is LivroKind.LICAO:
        return _licao(estado, modos)
    if kind is LivroKind.TELA:
        return _tela(estado, modos)
    if estado is not SkillState.PUBLISHED:
        return _inerte_do_estado(estado)
    if kind is LivroKind.PREFERENCIA:
        return Uso(Camada.PRE_PREENCHE, "só decide sozinha numa etapa sem efeito externo")
    return Uso(Camada.CONTEXTO_DA_PERSONA, "contexto da persona; onde entra no prompt social: a conferir")


def uso_do_declarado(arquivo: ArquivoDeclarado, *, com_conteudo: bool = True) -> Uso:
    """O que o arquivo declarado faz. `com_conteudo=False` é a loja sem `nav_hints` nem `known_selectors`."""
    if arquivo is ArquivoDeclarado.APP:
        return Uso(Camada.DECIDE_SEM_IA, "o núcleo pergunta ao registro (porta de sessão, compatibilidade de "
                                         "renderizador); sem IA")
    if arquivo is ArquivoDeclarado.CATALOGO:
        return Uso(Camada.VAI_AO_PROMPT, "vai ao prompt do planejador, à porta de política e à prova local")
    if arquivo is ArquivoDeclarado.TELAS:
        return Uso(Camada.CLASSIFICA_TELA, "classifica a tela e detecta bloqueio, sem prompt")
    if arquivo is ArquivoDeclarado.SESSAO:
        return Uso(Camada.LOGIN_FORA_DA_IA, "o motor da sessão declarada entra na conta, fora da IA")
    if com_conteudo:
        return Uso(Camada.VAI_AO_PROMPT, "`nav_hints` e `known_selectors` vão ao prompt do ator (AppContext)")
    return Uso(Camada.INERTE, "a loja não traz `nav_hints` nem `known_selectors` deste app")


def origem_do_aprendido(kind: LivroKind, detalhe: str | None) -> OrigemNaVisao:
    """`absorvido` só existe nos itens do livro (tela, lição...): é o `state_detail` `absorvida:<commit>`."""
    if kind in KINDS_DE_ITEM and absorvida_em(detalhe) is not None:
        return OrigemNaVisao.ABSORVIDO
    return OrigemNaVisao.APRENDIDO
