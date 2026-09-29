"""Pacote A9 do ADR-054 (a preencher, opcional): liga a voz aprendida ao `SocialContextBuilder` (até 2 pares do
mesmo perfil e da mesma ação, sempre publicados pelo dono) e a preferência ao desambiguador."""
from __future__ import annotations

from app.modules.learning.application.servico import LearningService


def ligar(servico: LearningService) -> None:
    """Sem efeito até o A9."""
