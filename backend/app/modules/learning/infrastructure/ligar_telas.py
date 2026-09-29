"""Pacote A8 do ADR-054 (a preencher): liga as telas aprendidas ao `ConhecimentoDeTelas` unido
(`integrations/app_declarado/conhecimento.py::definir_regras_aprendidas`), só no modo `telas: on`, e nunca em tela
sensível, com senha ou de desafio."""
from __future__ import annotations

from app.modules.learning.application.servico import LearningService


def ligar(servico: LearningService) -> None:
    """Sem efeito até o A8."""
