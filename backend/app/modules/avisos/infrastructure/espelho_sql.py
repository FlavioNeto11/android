"""`trello_cartoes` (migração 087, item 32.2): o elo entre um FATO da Central e o cartão dele no Trello.

O espelho grava aqui DEPOIS de a chamada ao Trello dar certo, nunca antes: uma falha no meio deixa a linha como estava
e a volta seguinte repete a operação. Em SQLite e PostgreSQL.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.db import Database, Row
from app.util import to_iso

ATIVO, ARQUIVADO = "ativo", "arquivado"


class CartoesDoTrello:
    def __init__(self, db: Database, relogio: Callable[[], datetime]):
        self.db = db
        self._relogio = relogio

    def todos(self) -> dict[str, Row]:
        return {str(r["chave"]): r for r in self.db.query("SELECT * FROM trello_cartoes ORDER BY chave")}

    def um(self, chave: str) -> Row | None:
        return self.db.one("SELECT * FROM trello_cartoes WHERE chave=?", (chave,))

    def gravar(self, chave: str, card_id: str, quadro: str, lista: str, hash_: str) -> None:
        """Cartão novo, ou o fato que voltou depois de arquivado (cartão novo, mesma chave)."""
        em = to_iso(self._relogio())
        self.db.execute(
            "INSERT INTO trello_cartoes(chave, card_id, quadro, lista, hash, estado, criado_em, atualizado_em)"
            " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (chave) DO UPDATE SET card_id=excluded.card_id,"
            " quadro=excluded.quadro, lista=excluded.lista, hash=excluded.hash, estado=excluded.estado,"
            " criado_em=excluded.criado_em, atualizado_em=excluded.atualizado_em",
            (chave, card_id, quadro, lista, hash_, ATIVO, em, em))

    def novo_hash(self, chave: str, hash_: str) -> None:
        self.db.execute("UPDATE trello_cartoes SET hash=?, atualizado_em=? WHERE chave=?",
                        (hash_, to_iso(self._relogio()), chave))

    def arquivar(self, chave: str) -> None:
        self.db.execute("UPDATE trello_cartoes SET estado=?, atualizado_em=? WHERE chave=?",
                        (ARQUIVADO, to_iso(self._relogio()), chave))
