"""Verificação: o veredito de uma observação sobre uma etapa (design §7, §14.1 VERIFY).

Quatro valores. `not_proved` é a tela DESMENTINDO a etapa (uma marca de falha visível); `unknown` é não dar para
afirmar nada — quem julga, então, é o verificador de sempre. `pending` é o efeito ainda a caminho ("Sending…" na
tela): não desmente, mas também não deixa ninguém afirmar — nem a prova local nem o modelo, que nem deve ser
perguntado; quem verifica espera a marca sumir (ADR-055: em 19/09 a DM da ciclana foi dada por enviada com
"Sending…" congelado). Falha ou incerteza nunca viram sucesso: só `proved` fecha a etapa sem o modelo.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .definition import CapabilityRef


class VerifyOutcome(StrEnum):
    proved = "proved"
    not_proved = "not_proved"
    unknown = "unknown"
    pending = "pending"


@dataclass(frozen=True, slots=True)
class VerifyResult:
    outcome: VerifyOutcome
    #: Em português, para a trilha da etapa: por que provou, por que desmentiu ou por que não deu para afirmar.
    detail: str


@dataclass(frozen=True, slots=True)
class StepView:
    """O que um provider enxerga da etapa em execução: já materializada (bindings e guardas com os valores reais)."""

    node_id: str
    capability: CapabilityRef | None
    bindings: tuple[tuple[str, str], ...] = ()
    band_guard: tuple[str, ...] = ()
    #: Nível de entrega exigido (`sent`, `delivered`…). Quando há, a prova local não basta: "enviado" não prova
    #: "entregue". É a mesma regra do executor (`_verify`).
    required_delivery_level: str | None = None
    #: 31.59: quantas mensagens com o texto IGUAL ao `content` havia na tela de ANTES do efeito (linha de base do
    #: executor). `None` = sem linha de base: a prova `sent_text` não afirma nada.
    mensagens_antes: int | None = None


@dataclass(frozen=True, slots=True)
class Observation:
    """Uma leitura da tela.

    `screen` é opaco de propósito: o modelo de tela de hoje (`UiTree`, em `automation/hierarchy.py`) é legado, e o
    domínio não o vê (regra D2). Quando ele for para o kernel `shared` (§4), este campo ganha o tipo de verdade; até
    lá, quem lê a tela (a infraestrutura) confere o tipo antes de usar e, se não reconhecer, não afirma nada.
    """

    screen: object
    package: str | None = None
