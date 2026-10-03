"""`trello_cartoes` (migração 087, item 32.2): o elo entre um FATO da Central e o cartão dele no Trello.

O espelho grava aqui DEPOIS de a chamada ao Trello dar certo, nunca antes: uma falha no meio deixa a linha como estava
e a volta seguinte repete a operação. Em SQLite e PostgreSQL.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.db import Database, Row
from app.util import to_iso

ATIVO, ARQUIVADO, CRIANDO = "ativo", "arquivado", "criando"
#: `card_id` é NOT NULL e único (087, que não se edita): a linha de intenção leva um sentinela por chave, que nenhum id do
#: Trello (hexadecimal) pode ser. Vale só em `estado = 'criando'`; ao criar ou adotar o cartão, vira o id verdadeiro.
SENTINELA = "criando:"


class CartoesDoTrello:
    def __init__(self, db: Database, relogio: Callable[[], datetime]):
        self.db = db
        self._relogio = relogio

    def todos(self) -> dict[str, Row]:
        return {str(r["chave"]): r for r in self.db.query("SELECT * FROM trello_cartoes ORDER BY chave")}

    def um(self, chave: str) -> Row | None:
        return self.db.one("SELECT * FROM trello_cartoes WHERE chave=?", (chave,))

    def intencao(self, chave: str, quadro: str, lista: str) -> None:
        """Banco PRIMEIRO: antes de criar o cartão, a linha `criando` diz que a Central vai criá-lo. Se o processo cair
        depois de o Trello gravar e antes de o `card_id` chegar aqui, a volta seguinte acha o cartão pela marca da
        descrição e o adota, em vez de criar outro. Já `criando` (volta repetida): não muda nada."""
        em = to_iso(self._relogio())
        self.db.execute(
            "INSERT INTO trello_cartoes(chave, card_id, quadro, lista, hash, estado, criado_em, atualizado_em)"
            " VALUES (?,?,?,?,NULL,?,?,?) ON CONFLICT (chave) DO UPDATE SET card_id=excluded.card_id,"
            " quadro=excluded.quadro, lista=excluded.lista, hash=NULL, estado=excluded.estado,"
            " criado_em=excluded.criado_em, atualizado_em=excluded.atualizado_em WHERE trello_cartoes.estado <> ?",
            (chave, SENTINELA + chave, quadro, lista, CRIANDO, em, em, CRIANDO))

    def gravar(self, chave: str, card_id: str, quadro: str, lista: str, hash_: str) -> None:
        """O cartão existe no Trello (criado ou adotado): a linha de intenção, ou a de um fato que voltou depois de
        arquivado, passa a `ativo` com o id verdadeiro."""
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
