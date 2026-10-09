"""Porta da caixa de entrada compartilhada. Quem a cumpre (IMAP, hoje) mora em `adapters/`."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class Mensagem:
    """Uma mensagem já decodificada. `recebida_em` é sempre com fuso (UTC) para comparar sem surpresa."""
    remetente: str
    destinatarios: tuple[str, ...]
    assunto: str
    corpo: str
    recebida_em: datetime


class LeitorCaixa(Protocol):
    """Lê a caixa compartilhada. `async` porque o adaptador de rede bloqueia: ele mesmo sai do laço do servidor
    (a camada de aplicação não importa `asyncio`)."""

    async def buscar(self, *, destinatario: str, remetente: str, desde: datetime) -> list[Mensagem]:
        """Mensagens para `destinatario` cujo remetente contém `remetente`, recebidas a partir de `desde`,
        da mais recente para a mais antiga."""
        ...
