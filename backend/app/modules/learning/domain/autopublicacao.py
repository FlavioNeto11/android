"""A autopublicação do fluxo de classe B (item 30.34; emenda de 03/10 à D1 do ADR-054, decisão do dono: "sim" à P2).

Até aqui, o fluxo com efeito externo espera o dono em `validated` (a D1). Com a emenda, o FLUXO de classe B publica
sozinho quando as três condições valem juntas:
- o parecer ATUAL do curador sugere `aprovar` com confiança `alta`, é real (não simulado) e ninguém o decidiu ainda;
- há ≥ 2 execuções reais distintas a favor;
- em ≥ 2 aparelhos distintos, e nenhuma evidência real contra (nem conflito).

A classe C segue item a item com o dono; a A segue pela D1 de sempre. Nada disto vale para outro tipo de item, nem
para o fluxo REAPRENDIDO depois de uma evidência inválida (30.23): esse volta ao dono por regra dele, mesmo sendo B. A
classe é a de AGORA (a aplicação reclassifica pelo dossiê na hora de agir e fica com a mais restritiva entre ela e a do
parecer).

Antes de publicar de verdade, a regra roda em SOMBRA: só registra o que publicaria (um CASO por item, contado uma vez).
O caso fecha em 7 dias. Regride se, nesse prazo, aparece evidência real contra ou conflito, o item é desligado (pelo
sistema ou por uma pessoa) ou uma pessoa recusa o parecer. O modo `on` só publica quando o balanço da sombra libera:
≥ 30 casos FECHADOS e ≥ 90 % deles sem regressão. Os limiares vêm daqui e do livro da sombra, nunca do config: o config
só diz o modo (`off`, o padrão; `shadow`; `on`), e `on` sem o balanço se comporta como `shadow`.

Puro: recebe os fatos já lidos e o `agora`; sem I/O, sem relógio.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.curador import Confianca, Decisao
from app.modules.learning.domain.politica_de_risco import ClasseDeRisco
from app.modules.learning.domain.promocao import Contadores

#: O único tipo que a emenda alcança.
KIND_DO_FLUXO = "fluxo"


class ModoDaAutopublicacao(StrEnum):
    OFF = "off"            # nada roda (o padrão)
    SHADOW = "shadow"      # registra o que publicaria; não publica
    ON = "on"              # publica, mas só com o balanço da sombra liberado; sem ele, igual a `shadow`


@dataclass(frozen=True, slots=True)
class ParametrosDaAutopublicacao:
    execucoes_min: int = 2
    aparelhos_min: int = 2
    contra_max: int = 0
    casos_min: int = 30
    taxa_sem_regressao_min: float = 0.9
    janela_de_regressao_dias: int = 7


@dataclass(frozen=True, slots=True)
class ParecerParaAutopublicar:
    """O parecer ATUAL do curador sobre o item, como a aplicação o leu."""

    decisao: Decisao | None        # `None`: revisão sem parecer (inválida, recusada)
    confianca: Confianca | None
    simulado: bool
    desatualizado: bool            # o item mudou de estado desde o parecer (a mesma regra do gesto)
    decidido: bool                 # uma pessoa já aceitou ou recusou


@dataclass(frozen=True, slots=True)
class FatosDaAutopublicacao:
    kind: str
    estado: SkillState | None
    requer_dono: bool              # a D1 o segura em `validated` (efeito externo ou texto de pessoa)
    reaprendido: bool              # renasceu depois de uma evidência inválida (30.23): volta ao dono
    classe: ClasseDeRisco | None   # a de agora, a mais restritiva entre o dossiê e o parecer
    parecer: ParecerParaAutopublicar | None
    contadores: Contadores         # só evidência real (`promocao.contadores`)


class MotivoDeFora(StrEnum):
    """Por que o item NÃO publicaria agora. Vocabulário fechado, na ordem em que a regra confere."""

    NAO_E_FLUXO = "nao_e_fluxo"
    NAO_ESPERA_O_DONO = "nao_espera_o_dono"        # não está em `validated` segurado pela D1
    REAPRENDIDO = "reaprendido"
    CLASSE_NAO_B = "classe_nao_b"
    SEM_PARECER = "sem_parecer"
    PARECER_SIMULADO = "parecer_simulado"
    PARECER_DESATUALIZADO = "parecer_desatualizado"
    PARECER_JA_DECIDIDO = "parecer_ja_decidido"
    NAO_SUGERE_APROVAR = "nao_sugere_aprovar"
    CONFIANCA_NAO_ALTA = "confianca_nao_alta"
    POUCAS_EXECUCOES = "poucas_execucoes"
    POUCOS_APARELHOS = "poucos_aparelhos"
    EVIDENCIA_CONTRA = "evidencia_contra"


@dataclass(frozen=True, slots=True)
class Avaliacao:
    motivos: tuple[MotivoDeFora, ...]

    @property
    def publicaria(self) -> bool:
        return not self.motivos


def avaliar(f: FatosDaAutopublicacao, p: ParametrosDaAutopublicacao = ParametrosDaAutopublicacao()) -> Avaliacao:
    """As três condições da emenda. Devolve TODOS os motivos de fora (não só o primeiro): o relatório da sombra diz o
    que falta a cada item, e o teste prova que cada condição recusa sozinha."""
    if f.kind != KIND_DO_FLUXO:
        return Avaliacao((MotivoDeFora.NAO_E_FLUXO,))
    motivos: list[MotivoDeFora] = []
    if f.estado is not SkillState.VALIDATED or not f.requer_dono:
        motivos.append(MotivoDeFora.NAO_ESPERA_O_DONO)
    if f.reaprendido:
        motivos.append(MotivoDeFora.REAPRENDIDO)
    if f.classe is not ClasseDeRisco.B:
        motivos.append(MotivoDeFora.CLASSE_NAO_B)
    x = f.parecer
    if x is None or x.decisao is None:
        motivos.append(MotivoDeFora.SEM_PARECER)
    else:
        if x.simulado:
            motivos.append(MotivoDeFora.PARECER_SIMULADO)
        if x.desatualizado:
            motivos.append(MotivoDeFora.PARECER_DESATUALIZADO)
        if x.decidido:
            motivos.append(MotivoDeFora.PARECER_JA_DECIDIDO)
        if x.decisao is not Decisao.APROVAR:
            motivos.append(MotivoDeFora.NAO_SUGERE_APROVAR)
        if x.confianca is not Confianca.ALTA:
            motivos.append(MotivoDeFora.CONFIANCA_NAO_ALTA)
    c = f.contadores
    if c.execucoes < p.execucoes_min:
        motivos.append(MotivoDeFora.POUCAS_EXECUCOES)
    if c.aparelhos < p.aparelhos_min:
        motivos.append(MotivoDeFora.POUCOS_APARELHOS)
    if c.contra > p.contra_max:
        motivos.append(MotivoDeFora.EVIDENCIA_CONTRA)
    return Avaliacao(tuple(motivos))


# ------------------------------------------------------------------ a sombra: casos, regressões e o balanço
class Regressao(StrEnum):
    """O que, dentro da janela do caso, conta como regressão (definição da orquestradora, 03/10)."""

    EVIDENCIA_CONTRA = "evidencia_contra"      # evidência REAL contra ou em conflito
    DESLIGADO = "desligado"                    # o item foi desligado, pelo sistema ou por uma pessoa
    PARECER_RECUSADO = "parecer_recusado"      # uma pessoa recusou o parecer


@dataclass(frozen=True, slots=True)
class EventoDoCaso:
    tipo: Regressao
    em: datetime


class Desfecho(StrEnum):
    ABERTO = "aberto"          # a janela ainda corre e nada regrediu
    LIMPO = "limpo"            # a janela fechou sem regressão
    REGREDIU = "regrediu"      # algo regrediu dentro da janela (fecha na hora)


@dataclass(frozen=True, slots=True)
class CasoDaSombra:
    """Um item que publicaria, marcado UMA vez (a primeira vez que a regra passou)."""

    item_ref: str
    marcado_em: datetime


def desfecho(caso: CasoDaSombra, eventos: Iterable[EventoDoCaso], agora: datetime,
             p: ParametrosDaAutopublicacao = ParametrosDaAutopublicacao()) -> Desfecho:
    """Só conta o evento DEPOIS da marca e dentro da janela: o que já existia antes não é regressão do caso (e teria
    impedido a marca)."""
    fim = caso.marcado_em + timedelta(days=p.janela_de_regressao_dias)
    if any(caso.marcado_em <= e.em <= fim for e in eventos):
        return Desfecho.REGREDIU
    return Desfecho.LIMPO if agora >= fim else Desfecho.ABERTO


@dataclass(frozen=True, slots=True)
class BalancoDaSombra:
    casos: int
    abertos: int
    limpos: int
    regrediram: int
    #: Limpos ÷ fechados; `None` sem caso fechado (ausente não é zero).
    taxa_sem_regressao: float | None
    libera: bool


def balanco(desfechos: Iterable[Desfecho],
            p: ParametrosDaAutopublicacao = ParametrosDaAutopublicacao()) -> BalancoDaSombra:
    """O `on` só publica com ≥ `casos_min` casos FECHADOS e a taxa ≥ `taxa_sem_regressao_min`: o caso aberto ainda pode
    regredir, então não conta a favor."""
    d = list(desfechos)
    limpos = sum(1 for x in d if x is Desfecho.LIMPO)
    regrediram = sum(1 for x in d if x is Desfecho.REGREDIU)
    fechados = limpos + regrediram
    taxa = round(limpos / fechados, 3) if fechados else None
    libera = fechados >= p.casos_min and taxa is not None and taxa >= p.taxa_sem_regressao_min
    return BalancoDaSombra(casos=len(d), abertos=len(d) - fechados, limpos=limpos, regrediram=regrediram,
                           taxa_sem_regressao=taxa, libera=libera)


class Acao(StrEnum):
    NADA = "nada"
    REGISTRAR = "registrar"    # marca o caso na sombra (se ainda não marcado)
    PUBLICAR = "publicar"      # transição validated → published pelo sistema, com o motivo da emenda na trilha


def acao(modo: ModoDaAutopublicacao, avaliacao: Avaliacao, b: BalancoDaSombra) -> Acao:
    """O modo é do config; o balanço é medido. `on` sem o balanço liberado continua só registrando."""
    if modo is ModoDaAutopublicacao.OFF or not avaliacao.publicaria:
        return Acao.NADA
    if modo is ModoDaAutopublicacao.ON and b.libera:
        return Acao.PUBLICAR
    return Acao.REGISTRAR


__all__ = ["Acao", "Avaliacao", "BalancoDaSombra", "CasoDaSombra", "Desfecho", "EventoDoCaso", "FatosDaAutopublicacao",
           "KIND_DO_FLUXO", "ModoDaAutopublicacao", "MotivoDeFora", "ParametrosDaAutopublicacao",
           "ParecerParaAutopublicar", "Regressao", "acao", "avaliar", "balanco", "desfecho"]
