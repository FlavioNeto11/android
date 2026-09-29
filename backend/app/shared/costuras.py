"""As costuras de gesto do aprendizado (ADR-054) que moram FORA da fila: o contrato, o no-op e o `avisar`.

Quem avisa daqui não conhece a fila nem o livro:
- o gerenciador de aparelhos (a tomada de controle) é do central, mas `devices` não importa `taskqueue` — era a dívida
  aceita na integração do A2, quando o contrato morava em `taskqueue/costuras.py`;
- o ensino das habilidades (a correção) é camada de aplicação: só enxerga o kernel, e o DAG é `learning → skills`;
- a rota que tira um comando de `uncertain` (`api.py`) é quem tem o gesto; `commands/` não conhece o livro.
O kernel é a raiz do grafo: qualquer pacote o enxerga, e ele não enxerga ninguém.

`taskqueue/costuras.py` reexporta tudo o que está aqui e estende o no-op (`SemCosturas`) com as costuras da fila;
quem cumpre as duas é o livro (`modules/learning/infrastructure/ligar_costuras.py`).

O que NUNCA passa por aqui: texto de tela, coordenada, credencial. A tomada de controle leva só os ids da etapa; a nota
de uma pessoa já chega triada por quem a recebeu (a rota de resolução de comando recusa com 409, o ensino recusa a
correção), e o livro a tria de novo antes de gravar.

`autor_do_gesto` é a regra única de QUEM fez um gesto pelo painel — a mesma das rotas do livro e das habilidades.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

log = logging.getLogger("poc.aprendizado")

#: Quem fez o gesto pelo painel quando ninguém se identificou (sem sessão).
PAINEL = "panel"
#: O ator de sistema das habilidades e do livro (`skills.domain.lifecycle.SYSTEM_ACTOR`), repetido porque o kernel não
#: vê as habilidades; o teste confere a igualdade.
SISTEMA = "sistema"

#: As saídas humanas de um comando incerto (`CommandResolveBody.outcome`).
DesfechoDoComando = Literal["succeeded", "failed", "cancelled"]


def autor_do_gesto(operador: object) -> str:
    """Quem fez o gesto: o operador da sessão do painel (`request.state.operador`), ou `panel`. Nunca o ator de
    sistema — pela rota decide sempre uma pessoa, e a régua diária só conta como negativo humano o que não é dele.

    O nome vem da SESSÃO, nunca do corpo da requisição (`requested_by`): aquele campo qualquer chamador escreve, e um
    `sistema` ali tiraria o gesto da conta das pessoas."""
    quem = operador if isinstance(operador, str) and operador.strip() else PAINEL
    return f"painel:{quem}" if quem == SISTEMA else quem


@dataclass(frozen=True, slots=True)
class TomadaDeControle:
    """Uma pessoa pediu o aparelho enquanto a IA conduzia uma etapa. Sem árvore, texto nem coordenada."""

    instance_id: str
    run_id: str
    objective_id: str | None
    step_id: str


@dataclass(frozen=True, slots=True)
class ResolucaoDeComando:
    """Uma pessoa tirou um comando de `uncertain` (`POST /api/commands/{id}/resolve`): o que nenhuma sonda provou.

    Só o id e a decisão: o aparelho, o verbo e a trilha (a execução, o app e o perfil que o pediram, quando o `params`
    do comando os traz) o livro relê do comando. `simulated` é o modo da instalação no instante do gesto e só vale
    quando o comando não aponta execução; com execução, vale a dela (`runs.simulated`)."""

    command_id: str
    resolucao: DesfechoDoComando
    nota: str | None
    quem: str
    simulated: bool


@dataclass(frozen=True, slots=True)
class CorrecaoDeEnsino:
    """Uma pessoa corrigiu, no ensino, a etapa em que a habilidade errou (fonte `correction`, §13.1: a etapa estava
    `failed` ou `uncertain`). A nota é o texto da pessoa, que o ensino já recusou se tinha cara de credencial."""

    teaching_id: str
    #: `teaching_turns.id` da correção: um gesto, uma chave (duas correções na mesma etapa não se engolem).
    turno: int
    skill_id: str
    run_id: str
    step_id: str
    nota: str
    quem: str | None                 # `None` = chamada sem pessoa identificada (o livro grava `panel`)


class CosturaDeControle(Protocol):
    """O que o gerenciador de aparelhos chama (a porta é dele: o gerenciador não conhece a fila nem o livro)."""

    def tomou_controle(self, tomada: TomadaDeControle) -> None: ...


class CosturaDeComando(Protocol):
    """O que a rota de resolução de comando chama."""

    def comando_incerto_resolvido(self, resolucao: ResolucaoDeComando) -> None: ...


class CosturaDeEnsino(Protocol):
    """O que o ensino das habilidades chama (a porta é dele: as habilidades nunca importam o aprendizado)."""

    def correcao_de_ensino(self, correcao: CorrecaoDeEnsino) -> None: ...


class SemCosturasDeGesto:
    """O padrão das costuras de gesto: nenhum aviso — o comportamento de antes do ADR-054."""

    def tomou_controle(self, tomada: TomadaDeControle) -> None:
        return None

    def comando_incerto_resolvido(self, resolucao: ResolucaoDeComando) -> None:
        return None

    def correcao_de_ensino(self, correcao: CorrecaoDeEnsino) -> None:
        return None


SEM_COSTURAS_DE_GESTO = SemCosturasDeGesto()

_T = TypeVar("_T")


def avisar(aviso: Callable[[_T], None], dado: _T) -> None:
    """Chama uma costura de aviso. A falha vira log: o aprendizado nunca derruba quem o avisou."""
    try:
        aviso(dado)
    except Exception:  # noqa: BLE001 - aprendizado é registro: nunca derruba a etapa, o gesto nem a execução
        log.exception("aprendizado: a costura %s falhou (a operação seguiu)", type(dado).__name__)


__all__ = ["PAINEL", "SEM_COSTURAS_DE_GESTO", "SISTEMA", "CorrecaoDeEnsino", "CosturaDeComando", "CosturaDeControle",
           "CosturaDeEnsino", "DesfechoDoComando", "ResolucaoDeComando", "SemCosturasDeGesto", "TomadaDeControle",
           "autor_do_gesto", "avisar"]
