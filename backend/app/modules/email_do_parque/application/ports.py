"""Porta da caixa de entrada compartilhada. Quem a cumpre (IMAP, hoje) mora em `adapters/`."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Mensagem:
    """Uma mensagem já decodificada. `recebida_em` é sempre com fuso (UTC) para comparar sem surpresa."""
    remetente: str
    destinatarios: tuple[str, ...]
    assunto: str
    corpo: str
    recebida_em: datetime


@dataclass(frozen=True)
class CabecalhoDeMensagem:
    """Só o cabeçalho de uma mensagem (31.336): nunca o corpo. `autenticacao` é o resultado SPF/DKIM/DMARC que o servidor de
    entrada anotou em `Authentication-Results` (`pass`, `fail`, `softfail`, `none`...), ou vazio quando ele não anotou."""
    remetente: str
    assunto: str
    recebida_em: datetime
    autenticacao: tuple[tuple[str, str], ...] = ()
    #: `True` para o aviso de falha de entrega (remetente `mailer-daemon`/`postmaster` ou assunto de devolução).
    devolucao: bool = False


@runtime_checkable
class LeitorDeCabecalhos(Protocol):
    """Lista só os cabeçalhos (`BODY.PEEK[HEADER.FIELDS ...]`) das mensagens para `destinatario`, da mais recente para a
    mais antiga. Porta à parte da `LeitorCaixa` para não obrigar quem só lê código."""

    async def listar_cabecalhos(self, *, destinatario: str, desde: datetime, limite: int) -> list[CabecalhoDeMensagem]: ...


class LeitorCaixa(Protocol):
    """Lê a caixa compartilhada. `async` porque o adaptador de rede bloqueia: ele mesmo sai do laço do servidor
    (a camada de aplicação não importa `asyncio`)."""

    async def buscar(self, *, destinatario: str, remetente: str, desde: datetime) -> list[Mensagem]:
        """Mensagens para `destinatario` cujo remetente contém `remetente`, recebidas a partir de `desde`,
        da mais recente para a mais antiga."""
        ...
