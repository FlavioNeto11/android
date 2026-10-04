"""Quem criou o pedido (item 28.31, F2a; migração 106): decidido UMA vez, na criação, pelo que a requisição traz de certo.

    dono          há operador: a sessão do painel, ou a conversa do dono no Telegram ou no Trello (o operador
                  `telegram:`/`trello:` só existe para quem a conversa reconheceu como dono; convidado não cria nada);
    frente        sem operador e com `idempotency_key` `lote:<frente>:<id>` (o caminho das sessões pelo loopback);
    desconhecido  sem operador e sem `lote:`.

`convidado` e `ia` são do vocabulário para quando houver caminho que os crie; hoje nenhum cria. O loopback sem sessão NÃO
é o dono: é por ele que as frentes chegam (decisão da orquestradora, 04/10 20:11Z).

Puro: stdlib e a marca de lote do contrato.
"""
from __future__ import annotations

from app.contracts.origem import PREFIXO_LOTE

DONO, CONVIDADO, FRENTE, IA, DESCONHECIDO = "dono", "convidado", "frente", "ia", "desconhecido"
TIPOS = (DONO, CONVIDADO, FRENTE, IA, DESCONHECIDO)


def lote_da_chave(idempotency_key: str | None) -> str | None:
    chave = (idempotency_key or "").strip()
    return chave if chave.startswith(PREFIXO_LOTE) else None


def autor_da_criacao(operador: str | None, idempotency_key: str | None) -> str:
    if (operador or "").strip():
        return DONO
    return FRENTE if lote_da_chave(idempotency_key) else DESCONHECIDO
