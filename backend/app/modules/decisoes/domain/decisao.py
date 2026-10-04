"""Uma decisão da plataforma, como o painel e o desfazer a leem (item 28.25). Domínio puro: valores simples.

O prazo do desfazer é regra do domínio: passados `dias` do fato, a decisão vale como decidida e o dono precisa pedir o
efeito inverso pela tela da fila dona (se ela tiver). O relógio é argumento — quem chama passa o do banco (item 5.3).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.shared.decisoes import Escalar


@dataclass(frozen=True, slots=True)
class Decisao:
    id: int
    fila: str
    item_ref: str
    origem_ref: str
    regra: str
    efeito: str
    fatos: Mapping[str, Escalar]
    decidida_em: str
    resumida_em: str | None = None
    desfeita_em: str | None = None
    desfeita_por: str | None = None
    motivo_do_desfazer: str | None = None

    @property
    def desfeita(self) -> bool:
        return self.desfeita_em is not None


def prazo_expirou(decidida_em: datetime, agora: datetime, dias: float) -> bool:
    """O prazo de desfazer (`avisos.decisoes_automaticas.desfazer_dias`) já passou? No limite exato ainda vale."""
    return agora - decidida_em > timedelta(days=dias)


def prazo_ate(decidida_em: datetime, dias: float) -> datetime:
    return decidida_em + timedelta(days=dias)
