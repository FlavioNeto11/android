"""Quem criou o pedido (item 28.31, F2a; migração 106): decidido UMA vez, na criação, pelo que a requisição traz de certo.

    frente        a `idempotency_key` começa com `lote:` (`lote:<frente>:<id>`), com ou sem operador: o lote é da frente;
    dono          o operador está na lista DECLARADA do dono (`pedidos.operadores_do_dono`, por instalação), ou é
                  `trello:<membro_dono>`. Lista vazia: ninguém é o dono, e o aviso sai pelo id curto;
    convidado     há operador e ele não é o dono: o `POST /api/login` aceita qualquer nome (no loopback sem token; de fora,
                  com o token compartilhado), então uma sessão das frentes, ou a pessoa de confiança, cai aqui;
    desconhecido  sem operador e sem `lote:`.

`ia` é do vocabulário para quando houver caminho que a crie; hoje nenhum cria. O título do pedido só vai ao canal de fora
no `dono` (contrato da orquestradora, 04/10 20:27Z).

Puro: stdlib e a marca de lote do contrato.
"""
from __future__ import annotations

from collections.abc import Iterable

from app.contracts.origem import PREFIXO_LOTE

DONO, CONVIDADO, FRENTE, IA, DESCONHECIDO = "dono", "convidado", "frente", "ia", "desconhecido"
TIPOS = (DONO, CONVIDADO, FRENTE, IA, DESCONHECIDO)
#: O operador da conversa do dono no Trello (`ConversaDoTrello`): `trello:<idMember>` do autor da action.
PREFIXO_DO_TRELLO = "trello:"


def _normal(nome: str | None) -> str:
    """O nome da sessão como se compara: sem espaço sobrando e sem diferença de caixa."""
    return " ".join((nome or "").split()).casefold()


def operadores_do_dono(declarados: Iterable[str], membro_trello: str | None) -> frozenset[str]:
    """Os operadores que são o dono: a lista declarada da instalação e o `trello:<membro_dono>`, já normalizados."""
    donos = {_normal(n) for n in declarados if _normal(n)}
    if _normal(membro_trello):
        donos.add(_normal(PREFIXO_DO_TRELLO + str(membro_trello)))
    return frozenset(donos)


def lote_da_chave(idempotency_key: str | None) -> str | None:
    chave = (idempotency_key or "").strip()
    return chave if chave.startswith(PREFIXO_LOTE) else None


def autor_da_criacao(operador: str | None, idempotency_key: str | None, donos: frozenset[str] = frozenset()) -> str:
    """`donos` vem de `operadores_do_dono`. Sem ela, ninguém é o dono."""
    if lote_da_chave(idempotency_key):
        return FRENTE
    quem = _normal(operador)
    if not quem:
        return DESCONHECIDO
    return DONO if quem in donos else CONVIDADO
