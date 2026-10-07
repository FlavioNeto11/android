"""Estratégias de execução: o *como* de uma capability, separado do *quê* (ADR-032, proposto; design §14.3).

Moram no domínio de capabilities, e não no de execução, por causa do DAG de contextos (§9, D5): execução enxerga
capabilities, nunca o contrário. O provider de capability (infraestrutura deste contexto) cumpre as portas de
`modules/execution/application/ports.py` por tipagem estrutural, e para isso os tipos das assinaturas precisam
estar aqui embaixo.

Nenhuma estratégia decide sucesso: quem decide é o VERIFY. Por isso `StrategyResult` diz o que a estratégia FEZ
(agiu, divergiu, disparou o efeito), nunca se a etapa valeu.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StrategyKind(StrEnum):
    """Vocabulário fechado de estratégias (§5). A ordem de uso vem do nó (`origin.strategies`), não daqui."""

    deterministic = "deterministic"   # código que resolve sozinho: capability `internal` (fora do laço da etapa) e, desde
                                      # o LT-6 (29.45), o `open_app` do executor na etapa `app_foreground`, antes do ator
    recipe = "recipe"                 # receita gravada, reproduzida pelo `Replayer`
    app_provider = "app_provider"     # provider de sessão do app (`SessaoDeclarada`, ADR-052; fora da v1alpha1)
    ui_generic = "ui_generic"         # heurística de UI sem IA (reservado)
    ai_actor = "ai_actor"             # laço de decisão com o modelo
    human = "human"                   # desfecho `waiting_user`


#: O que existe de verdade como estratégia de nó na v1alpha1. `app_provider` não tem provider que rode dentro da etapa;
#: `deterministic` roda nela só por decisão do executor (o `open_app` do LT-6), não por pedido do nó; `ui_generic` é só
#: reserva (§14.3). Pedir um deles num nó é `E_STRATEGY_UNAVAILABLE`.
STRATEGIES_OF_A_NODE: tuple[StrategyKind, ...] = (StrategyKind.recipe, StrategyKind.ai_actor, StrategyKind.human)

#: Padrão quando o nó não diz: receita primeiro (sem custo de IA), depois o ator — a ordem fixa de hoje
#: (`executor.py`, receita antes do laço de decisão).
DEFAULT_STRATEGIES: tuple[StrategyKind, ...] = (StrategyKind.recipe, StrategyKind.ai_actor)


class StrategyStatus(StrEnum):
    """Como a estratégia terminou. Nenhum valor quer dizer "a etapa valeu"."""

    acted = "acted"                   # cumpriu o seu roteiro; o VERIFY diz se a pós-condição vale
    diverged = "diverged"             # a tela não era a esperada (`RecipeDiverged`): a próxima estratégia assume
    waiting_user = "waiting_user"     # só uma pessoa resolve
    failed = "failed"                 # não conseguiu agir


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """O que uma estratégia sabe da tentativa em curso. Referências, nunca o aparelho nem o driver."""

    run_id: str
    objective_id: str
    instance_id: str
    attempt_id: str | None
    app_package: str | None
    #: R2 (§14.5): depois de disparado o efeito, só VERIFY e RECONCILE. Estratégia nenhuma age de novo.
    effect_fired: bool = False


@dataclass(frozen=True, slots=True)
class StrategyResult:
    strategy: StrategyKind
    status: StrategyStatus
    #: O efeito externo foi disparado. Falha depois disso é `uncertain`, nunca nova tentativa (R3).
    fired: bool = False
    detail: str | None = None
    #: Etapa de coleta: os itens lidos (o scheduler expande o bloco `for_each` com eles).
    items: tuple[str, ...] = ()
