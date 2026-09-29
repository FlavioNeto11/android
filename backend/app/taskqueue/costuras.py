"""Costuras do aprendizado nos arquivos quentes (ADR-054, pacote A2).

O executor, o serviço de execução, o assistente do comando e o gerenciador de aparelhos AVISAM o aprendizado do que
acabou de acontecer — a tentativa que fechou, a decisão de uma pessoa sobre um item, a repetição e o cancelamento de uma
execução, a resposta a uma pergunta, a tomada de controle — e PEDEM as lições medidas antes de consultar o ator e o
planejador. Nada além disso: o aprendizado é fonte de decisão ou texto de contexto, nunca desfecho, verificação,
guarda, política ou custo máximo. A lição vai para `DecisionRequest.lessons` e `PlanRequest.lessons`; o verificador
não tem campo para ela (ADR-024).

Contrato tipado e no-op por padrão (`SEM_COSTURAS`): sem o `AppState` ligar o livro
(`modules/learning/infrastructure/ligar_costuras.py`), tudo segue exatamente como antes. E toda chamada passa por
`avisar` ou `pedir_licoes`, que engolem a exceção: o aprendizado nunca derruba uma etapa, um "resolver", uma
retomada, um cancelamento, uma sucessora nem uma tomada de controle.

O que NUNCA passa por aqui: texto de tela, valor de resposta, coordenada, credencial. A árvore da tentativa vai inteira
ao observador (é dele decidir o que aproveita, e a tela sensível ele pula); a resposta a uma pergunta vai só como
sha256; a tomada de controle, só com os ids da etapa.

As costuras de gesto que moram fora da fila (a tomada de controle, a resolução de um comando incerto, a correção do
ensino, o `avisar` e o no-op delas) estão no kernel (`app/shared/costuras.py`): quem as chama não pode importar
`taskqueue`. Este módulo as reexporta, e o `SemCosturas` daqui as herda.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Protocol

from ..automation.hierarchy import UiTree
from ..shared.costuras import (CorrecaoDeEnsino, CosturaDeComando, CosturaDeControle, CosturaDeEnsino,
                               ResolucaoDeComando, SemCosturasDeGesto, TomadaDeControle, autor_do_gesto, avisar)

log = logging.getLogger("poc.aprendizado")

#: Quem recebe a lição. O verificador não está aqui de propósito: lição empurraria o juiz a aceitar.
PapelDaLicao = Literal["actor", "planner"]
#: As três decisões de uma pessoa sobre um item (`ResolveBody.resolution`).
DecisaoSobreItem = Literal["confirm_done", "retry", "abandon"]
#: O desfecho da tentativa quando ela saiu por exceção (o scheduler a transforma em falha: nunca é sucesso).
SAIU_POR_EXCECAO = "erro"


@dataclass(frozen=True, slots=True)
class PedidoDeLicoes:
    """Um pedido por tentativa (ator) ou por planejamento (planejador)."""

    papel: PapelDaLicao
    #: A unidade do braço de controle: `step:<step_id>` no ator (as repetições da etapa não trocam de braço),
    #: `plan:<run_id>` no planejador.
    unidade: str
    run_id: str
    app_package: str                 # '' = sem app definido
    capability: str                  # '*' = etapa livre; '' no planejador
    step_hash: str                   # `steps.template_hash`; '' no planejador
    simulated: bool
    objective_id: str | None = None
    step_id: str | None = None
    attempt_id: str | None = None


@dataclass(frozen=True, slots=True)
class FechamentoDeTentativa:
    """A tentativa que o executor acabou de fechar, com o que já estava em memória (nenhuma leitura a mais).

    Chega ANTES de o scheduler gravar o desfecho (`finish_attempt`): quem precisa do tipo da falha o tira de `status`
    e do texto que o executor devolveu, nunca de `attempts.failure_kind`, ainda vazio neste instante."""

    attempt_id: str
    step_id: str
    run_id: str
    objective_id: str
    instance_id: str
    profile_id: str | None
    app_package: str | None          # o app da etapa
    capability: str | None
    template_hash: str | None        # nulo com as receitas desligadas (quem precisar lê de `steps`)
    #: O desfecho do executor (`succeeded`, `retry`, `failed`, `waiting_user`, `uncertain`, `yielded`, `cancelled`,
    #: `device_stuck`) ou `SAIU_POR_EXCECAO`.
    status: str
    #: Comprovada pela verificação. A confirmação à mão (`confirm_done`) nunca passa por aqui.
    verified: bool
    arvore: UiTree | None            # a última observação da tentativa (`rt.last_tree`)
    loja: bool                       # aparelho-loja: nada dele vira conhecimento
    simulated: bool


@dataclass(frozen=True, slots=True)
class ResolucaoDeItem:
    """Uma pessoa decidiu sobre um item parado (confirmar, repetir, abandonar)."""

    run_id: str
    objective_id: str
    resolucao: DecisaoSobreItem
    #: `<plan_version>.<seq>` da etapa que esperava a decisão: um gesto, uma chave — dois "repetir" no mesmo item
    #: caem em versões de plano diferentes e não se engolem.
    ordem: str
    nota: str | None                 # texto da pessoa; o livro redige e recusa o que parece credencial
    step_id: str | None              # a etapa que esperava a decisão (nula quando nenhuma esperava)


@dataclass(frozen=True, slots=True)
class RepeticaoDeExecucao:
    """"Repetir itens elegíveis" de uma execução (`retry_failed`), com o que de fato foi retomado."""

    run_id: str
    #: `<objective_id>@<plan_version nova>` de cada item retomado: a identidade do gesto.
    objetivos: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RespostaAPergunta:
    """A execução em `needs_input` foi respondida pelo assistente do comando (ADR-047). O VALOR não vem: só o sha256
    do comando respondido; quem precisar do texto o relê da sucessora."""

    run_id: str                      # a que esperava a resposta
    run_sucessora: str
    campos: tuple[str, ...]          # os campos perguntados, sem os de destino
    resposta_sha256: str


@dataclass(frozen=True, slots=True)
class CancelamentoDeExecucao:
    """Uma pessoa ABRIU um episódio de cancelamento pela rota (`POST /api/runs/{id}/cancel`): o gesto levou a execução
    a `cancelling`/`cancelled`. O clique repetido no mesmo episódio não chega aqui (`RunService.cancel` decide), nem o
    cancelamento que o assistente faz ao criar a sucessora — é consequência da resposta (`respondeu_pergunta`), não um
    gesto —, nem o `Outcome.cancelled` do escalonador, que é o efeito do pedido."""

    run_id: str
    #: O status no instante do gesto, lido ANTES de cancelar (depois, `cancelling` → `cancelled` é do escalonador).
    status_anterior: str
    #: Nada tinha rodado (`planning`, `needs_input`, `planned`): a execução saiu direto para `cancelled`.
    antes_de_iniciar: bool
    quem: str
    #: O instante da transição (`now_iso`): a marca do episódio. Uma execução reaberta (resolver ou repetir um item
    #: volta a `running`) e cancelada de novo é outro episódio, e a chave do sinal tem de ser outra.
    em: str


class CosturasDeAprendizado(Protocol):
    """O que o executor, o serviço de execução e o assistente chamam."""

    def licoes_para(self, pedido: PedidoDeLicoes) -> list[str]: ...
    def ao_fechar_tentativa(self, fechamento: FechamentoDeTentativa) -> None: ...
    def ao_resolver(self, resolucao: ResolucaoDeItem) -> None: ...
    def ao_repetir(self, repeticao: RepeticaoDeExecucao) -> None: ...
    def cancelou_execucao(self, cancelamento: CancelamentoDeExecucao) -> None: ...
    def respondeu_pergunta(self, resposta: RespostaAPergunta) -> None: ...


class SemCosturas(SemCosturasDeGesto):
    """O padrão: nenhuma lição, nenhum aviso — o comportamento de antes do ADR-054. As costuras de gesto (tomada de
    controle, comando incerto resolvido, correção do ensino) vêm no-op do kernel."""

    def licoes_para(self, pedido: PedidoDeLicoes) -> list[str]:
        return []

    def ao_fechar_tentativa(self, fechamento: FechamentoDeTentativa) -> None:
        return None

    def ao_resolver(self, resolucao: ResolucaoDeItem) -> None:
        return None

    def ao_repetir(self, repeticao: RepeticaoDeExecucao) -> None:
        return None

    def cancelou_execucao(self, cancelamento: CancelamentoDeExecucao) -> None:
        return None

    def respondeu_pergunta(self, resposta: RespostaAPergunta) -> None:
        return None


SEM_COSTURAS = SemCosturas()


def pedir_licoes(costuras: CosturasDeAprendizado, pedido: PedidoDeLicoes) -> list[str]:
    """As lições de um pedido; qualquer falha é "nenhuma lição" (o prompt sai como o de antes)."""
    try:
        licoes = costuras.licoes_para(pedido)
    except Exception:  # noqa: BLE001 - lição é contexto opcional: sem ela o ator decide como sempre decidiu
        log.exception("aprendizado: lições para %s indisponíveis (seguindo sem lição)", pedido.unidade)
        return []
    return [t for t in licoes if t.strip()]


# As costuras de gesto moram no kernel e seguem exportadas daqui (compatibilidade).
__all__ = ["SAIU_POR_EXCECAO", "SEM_COSTURAS", "CancelamentoDeExecucao", "CorrecaoDeEnsino", "CosturaDeComando",
           "CosturaDeControle", "CosturaDeEnsino", "CosturasDeAprendizado", "DecisaoSobreItem", "FechamentoDeTentativa",
           "PapelDaLicao", "PedidoDeLicoes", "RepeticaoDeExecucao", "ResolucaoDeComando", "ResolucaoDeItem",
           "RespostaAPergunta", "SemCosturas", "TomadaDeControle", "autor_do_gesto", "avisar", "pedir_licoes"]
