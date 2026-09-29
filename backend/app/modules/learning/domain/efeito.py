"""Medida de efeito das lições (ADR-054, decisão 5): o braço de controle, o veredito e a aposentadoria. Puro.

Uma lição publicada sem efeito externo não entra direto no prompt de todo mundo: ela entra EM PROVA, com um braço de
controle. A unidade é a ETAPA no ator (`step:<id>`; as repetições da mesma etapa não trocam de braço) e o
PLANEJAMENTO no planejador (`plan:<run_id>`); o braço sai de `sha1(item_id|unidade)`, determinístico e equilibrado —
nada de sorteio com estado, nada que dependa da ordem em que as etapas chegam.

- em prova: 50% com a lição (`with`), 50% sem (`holdout`). Uma lição em prova por (app, ação, papel) de cada vez; as
  outras esperam em `fila_de_prova` (`abrir_provas`);
- depois de "ajuda": 10% de controle (`holdout_publicada`), para a régua não morrer;
- o veredito só sai com pelo menos 8 unidades por braço (no máximo 20, as primeiras de cada um): "ajuda" com
  Δsucesso ≥ +10 pp, ou Δchamadas_p50 ≤ −1 com Δsucesso ≥ −5 pp; "atrapalha" com Δsucesso ≤ −10 pp, ou
  Δchamadas_p50 ≥ +2; "neutra" o resto, aos 20 por braço. Abaixo de 8, "faltam N" — o limiar não baixa (o volume é
  pequeno e o veredito leva semanas, ADR-054).

Quando as duas regras valem ao mesmo tempo (mais sucesso E mais chamadas), vence "atrapalha": rebaixar é automático e
seguro; promover não é. O histórico por ação (`HistoricoDeAcoes`) fica ao lado só como contexto — nunca decide.

Aposentadoria: "atrapalha" → `disabled`; "neutra" aos 20 → `deprecated`; 60 dias sem exposição → `deprecated`;
versão nova do app → volta a em prova; 2 refutações humanas (voto "deu errado" no braço `with`, A4) → `disabled`;
"ajuda" há 14 dias → proposta `promover_licao` no backlog; absorvida pelo repositório → `deprecated absorvida:<commit>`.
"""
from __future__ import annotations

import hashlib
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.livro import ItemDeAprendizado
from app.modules.learning.domain.vocabulario import Braco, DetalheDeEstado, absorvida
from app.modules.skills.domain.document import JsonObject

#: Desfechos que contam como sucesso da unidade: a etapa comprovada (ator) ou a execução concluída (planejador).
#: `completed_with_issues` não é sucesso: incerteza nunca conta como sucesso.
SUCESSO = frozenset({"succeeded", "completed"})
#: O desfecho da unidade que terminou sem comprovação: a etapa `succeeded` por confirmação à mão (`confirm_done`,
#: `verified=false`) e a execução `completed` com alguma etapa assim (o escalonador a conclui mesmo sem prova). Fica
#: NA amostra como não sucesso: fora dela, ou como sucesso, a lição que empurra etapas para a pessoa confirmar
#: esconderia o "atrapalha".
NAO_COMPROVADA = "unverified"
#: Fração do braço `with` enquanto a lição está em prova.
BRACO_EM_PROVA = 0.5
MINIMO_POR_BRACO = 8
MAXIMO_POR_BRACO = 20
SEM_EXPOSICAO_DIAS = 60
REFUTACOES_PARA_DESLIGAR = 2
AJUDA_PARA_PROMOVER_DIAS = 14
#: Os `state_detail` de uma lição publicada que vão ao prompt (as da fila esperam; as sem detalhe esperam a
#: curadoria dizer se entram em prova ou na fila).
EXPOSTAS = frozenset({DetalheDeEstado.EM_PROVA.value, DetalheDeEstado.MEDIDA_AJUDA.value})
#: Os que ainda disputam a prova do escopo.
NA_DISPUTA = frozenset({DetalheDeEstado.EM_PROVA.value, DetalheDeEstado.FILA_DE_PROVA.value})


# ------------------------------------------------------------------ braço
def fracao(item_id: str, unidade: str) -> float:
    """Um número em [0, 1) fixo para (lição, unidade)."""
    digest = hashlib.sha1(f"{item_id}|{unidade}".encode()).hexdigest()
    return int(digest[:8], 16) / 0x1_0000_0000


def braco(item_id: str, unidade: str, detalhe: str | None, *, holdout_publicada: float) -> Braco | None:
    """O braço desta unidade, ou `None` quando a lição não está exposta (fila, sem detalhe, medida neutra...)."""
    if detalhe == DetalheDeEstado.EM_PROVA.value:
        return Braco.WITH if fracao(item_id, unidade) < BRACO_EM_PROVA else Braco.HOLDOUT
    if detalhe == DetalheDeEstado.MEDIDA_AJUDA.value:
        return Braco.HOLDOUT if fracao(item_id, unidade) < holdout_publicada else Braco.WITH
    return None


# ------------------------------------------------------------------ exposição
@dataclass(frozen=True, slots=True)
class NovaExposicao:
    """A lição que foi ao prompt (`with`, com os tokens) ou ficou de fora de propósito (`holdout`, 0 token)."""

    item_id: str
    unit_id: str
    role: str
    arm: Braco
    tokens: int
    run_id: str
    objective_id: str | None
    app_package: str
    capability: str
    simulated: bool


@dataclass(frozen=True, slots=True)
class Exposicao:
    """Uma linha de `learning_exposures`. O desfecho (`outcome` em diante) é preenchido no digest da execução, antes da
    purga de `ai_calls`, e SÓ em execução real: sem `filled_at`, a unidade não entra em veredito nenhum."""

    item_id: str
    unit_id: str
    role: str
    arm: Braco
    tokens: int
    run_id: str | None
    objective_id: str | None
    app_package: str | None
    capability: str | None
    created_at: str
    outcome: str | None = None
    failure_kind: str | None = None
    ai_calls: int | None = None
    usd: float | None = None
    seconds: float | None = None
    replanned: bool | None = None
    filled_at: str | None = None

    @property
    def preenchida(self) -> bool:
        return self.filled_at is not None and self.outcome is not None


def exposicao_json(e: Exposicao) -> JsonObject:
    return {"unit_id": e.unit_id, "role": e.role, "arm": e.arm.value, "tokens": e.tokens, "run_id": e.run_id,
            "objective_id": e.objective_id, "created_at": e.created_at, "outcome": e.outcome,
            "failure_kind": e.failure_kind, "ai_calls": e.ai_calls, "usd": e.usd, "seconds": e.seconds,
            "replanned": e.replanned, "filled_at": e.filled_at}


# ------------------------------------------------------------------ veredito
class Efeito(StrEnum):
    AJUDA = "ajuda"
    NEUTRA = "neutra"
    ATRAPALHA = "atrapalha"


DETALHE_DO_EFEITO: dict[Efeito, DetalheDeEstado] = {
    Efeito.AJUDA: DetalheDeEstado.MEDIDA_AJUDA, Efeito.NEUTRA: DetalheDeEstado.MEDIDA_NEUTRA,
    Efeito.ATRAPALHA: DetalheDeEstado.MEDIDA_ATRAPALHA,
}


@dataclass(frozen=True, slots=True)
class Amostra:
    unidades: int
    sucessos: int
    p50_chamadas: float | None

    @property
    def taxa(self) -> float | None:
        return self.sucessos / self.unidades if self.unidades else None


@dataclass(frozen=True, slots=True)
class VereditoDeEfeito:
    efeito: Efeito | None
    com: Amostra
    sem: Amostra
    delta_sucesso_pp: float | None
    delta_p50: float | None
    #: Unidades que faltam no braço mais curto para haver veredito (0 quando já há amostra mínima).
    faltam: int

    def resumo(self) -> str:
        """A frase que fica na trilha (o veredito durável: as exposições são purgadas, a trilha não)."""
        def braco_(a: Amostra) -> str:
            p50 = "—" if a.p50_chamadas is None else f"{a.p50_chamadas:g}"
            return f"{a.sucessos}/{a.unidades} comprovadas, p50 {p50} chamadas"

        if self.efeito is None:
            base = f"sem veredito (faltam {self.faltam})" if self.faltam else "sem veredito ainda"
        else:
            base = f"efeito {self.efeito.value}"
        deltas = ""
        if self.delta_sucesso_pp is not None and self.delta_p50 is not None:
            deltas = f" — Δsucesso {self.delta_sucesso_pp:+.0f} pp, Δchamadas p50 {self.delta_p50:+g}"
        return f"{base}{deltas} (com: {braco_(self.com)}; sem: {braco_(self.sem)})"


def _amostra(unidades: Sequence[Exposicao]) -> Amostra:
    chamadas = [e.ai_calls or 0 for e in unidades]
    return Amostra(unidades=len(unidades), sucessos=sum(1 for e in unidades if e.outcome in SUCESSO),
                   p50_chamadas=float(statistics.median(chamadas)) if chamadas else None)


def veredito_de_efeito(exposicoes: Iterable[Exposicao], *, minimo: int = MINIMO_POR_BRACO,
                       maximo: int = MAXIMO_POR_BRACO) -> VereditoDeEfeito:
    """O efeito medido de UMA lição em prova. Só unidades preenchidas (execução real e assentada) contam, e de cada
    braço só as primeiras `maximo` pela ordem de criação — o veredito aos 20 não muda com o que chega depois."""
    preenchidas = sorted((e for e in exposicoes if e.preenchida), key=lambda e: (e.created_at, e.unit_id))
    com = [e for e in preenchidas if e.arm is Braco.WITH][:maximo]
    sem = [e for e in preenchidas if e.arm is Braco.HOLDOUT][:maximo]
    a, b = _amostra(com), _amostra(sem)
    faltam = max(0, minimo - min(a.unidades, b.unidades))
    if faltam or a.taxa is None or b.taxa is None or a.p50_chamadas is None or b.p50_chamadas is None:
        return VereditoDeEfeito(None, a, b, None, None, faltam)
    ds = (a.taxa - b.taxa) * 100
    dp = a.p50_chamadas - b.p50_chamadas
    efeito: Efeito | None = None
    if ds <= -10 or dp >= 2:
        efeito = Efeito.ATRAPALHA                        # rebaixar vence: é o sentido seguro
    elif ds >= 10 or (dp <= -1 and ds >= -5):
        efeito = Efeito.AJUDA
    elif a.unidades >= maximo and b.unidades >= maximo:
        efeito = Efeito.NEUTRA
    return VereditoDeEfeito(efeito, a, b, round(ds, 1), dp, 0)


# ------------------------------------------------------------------ fila de prova
def _chave_da_prova(item: ItemDeAprendizado) -> tuple[str, str, str]:
    return item.escopo.app, item.escopo.capability, item.escopo.role


def abrir_provas(publicadas: Sequence[ItemDeAprendizado]) -> list[tuple[ItemDeAprendizado, DetalheDeEstado]]:
    """Uma lição em prova por (app, ação, papel): o escopo sem prova aberta recebe a mais antiga da fila (ou a recém-
    publicada sem detalhe); as demais sem detalhe vão para a fila. Devolve só o que muda."""
    grupos: dict[tuple[str, str, str], list[ItemDeAprendizado]] = {}
    for item in publicadas:
        if item.state is SkillState.PUBLISHED and (item.state_detail is None or item.state_detail in NA_DISPUTA):
            grupos.setdefault(_chave_da_prova(item), []).append(item)
    mudancas: list[tuple[ItemDeAprendizado, DetalheDeEstado]] = []
    for itens in grupos.values():
        em_prova = any(i.state_detail == DetalheDeEstado.EM_PROVA.value for i in itens)
        espera = sorted((i for i in itens if i.state_detail != DetalheDeEstado.EM_PROVA.value),
                        key=lambda i: (i.state_at or i.created_at, i.id))
        for pos, item in enumerate(espera):
            novo = DetalheDeEstado.EM_PROVA if (not em_prova and pos == 0) else DetalheDeEstado.FILA_DE_PROVA
            if item.state_detail != novo.value:
                mudancas.append((item, novo))
    return mudancas


# ------------------------------------------------------------------ aposentadoria
@dataclass(frozen=True, slots=True)
class Aposentadoria:
    para: SkillState
    detalhe: str | None
    motivo: str


def aposentadoria(*, detalhe: str | None, desde: datetime | None, ultima_exposicao: datetime | None,
                  agora: datetime, refutacoes: int, absorvida_em: str | None,
                  sem_exposicao_dias: int | None = SEM_EXPOSICAO_DIAS) -> Aposentadoria | None:
    """Por que uma lição PUBLICADA sai de circulação agora, ou `None`. O veredito do efeito é à parte.
    `sem_exposicao_dias=None`: não aposenta por falta de exposição (modo `shadow`: sem prompt, não há exposição)."""
    if refutacoes >= REFUTACOES_PARA_DESLIGAR:
        return Aposentadoria(SkillState.DISABLED, detalhe, f"refutada {refutacoes}× por 'deu errado' no braço with")
    if absorvida_em:
        return Aposentadoria(SkillState.DEPRECATED, absorvida(absorvida_em),
                             f"absorvida pelo repositório no commit {absorvida_em}")
    if detalhe in EXPOSTAS and sem_exposicao_dias is not None:
        referencia = max((d for d in (ultima_exposicao, desde) if d is not None), default=None)
        if referencia is not None and agora - referencia >= timedelta(days=sem_exposicao_dias):
            dias = (agora - referencia).days
            return Aposentadoria(SkillState.DEPRECATED, detalhe, f"sem exposição há {dias} dias")
    return None


def volta_a_prova(detalhe: str | None, versao_do_item: str | None, versao_atual: str | None) -> bool:
    """Versão nova do app: a medida de antes não vale mais (a tela pode ser outra) — a lição volta a em prova."""
    return (detalhe in EXPOSTAS and versao_do_item is not None and versao_atual is not None
            and versao_do_item != versao_atual)


def propor_promocao(detalhe: str | None, desde: datetime | None, agora: datetime,
                    dias: int = AJUDA_PARA_PROMOVER_DIAS) -> bool:
    """'Ajuda' há pelo menos `dias`: vira proposta `promover_licao` no backlog (nav_hints, catalogo.yaml ou
    telas.yaml). Promover para o repositório é sempre de pessoa; o banco só propõe."""
    return (detalhe == DetalheDeEstado.MEDIDA_AJUDA.value and desde is not None
            and agora - desde >= timedelta(days=dias))


__all__ = ["AJUDA_PARA_PROMOVER_DIAS", "BRACO_EM_PROVA", "DETALHE_DO_EFEITO", "EXPOSTAS", "MAXIMO_POR_BRACO",
           "MINIMO_POR_BRACO", "NAO_COMPROVADA", "NA_DISPUTA", "REFUTACOES_PARA_DESLIGAR", "SEM_EXPOSICAO_DIAS",
           "SUCESSO", "Amostra", "Aposentadoria", "Efeito", "Exposicao", "NovaExposicao", "VereditoDeEfeito",
           "abrir_provas", "aposentadoria", "braco", "exposicao_json", "fracao", "propor_promocao",
           "veredito_de_efeito", "volta_a_prova"]
