"""Pacote A2 do ADR-054 (a preencher): liga as costuras tipadas no-op dos arquivos quentes (executor, repository,
scheduler, service, assistente, manager) ao `LearningService` — `failure_kind` gravado, `ao_fechar_tentativa`,
`licoes_para`, `ao_resolver`/`ao_repetir`, tomada de controle e respostas do assistente. Toda costura em try/except."""
from __future__ import annotations

from app.modules.learning.application.servico import LearningService


def ligar(servico: LearningService) -> None:
    """Sem efeito até o A2."""
