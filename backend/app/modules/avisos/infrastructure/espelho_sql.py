"""`trello_cartoes` e `trello_cursor` (migração 087, item 32.2): o elo entre um FATO da Central e o cartão dele no Trello, e
o ponto até onde o leitor já leu cada quadro.

O espelho grava aqui DEPOIS de a chamada ao Trello dar certo, nunca antes: uma falha no meio deixa a linha como estava
e a volta seguinte repete a operação. Em SQLite e PostgreSQL.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.db import Database, Row
from app.util import parse_iso, to_iso

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

    def chave_do_cartao(self, card_id: str) -> str | None:
        """A chave do fato que o cartão representa, ou `None` (cartão feito por pessoa, ou de um fato já arquivado). É o
        caminho de volta da action: o comentário do dono num cartão vira o fato sobre o qual ele age."""
        r = self.db.one("SELECT chave FROM trello_cartoes WHERE card_id=? AND estado=?", (card_id, ATIVO))
        return str(r["chave"]) if r is not None else None

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


class CursorDoTrello:
    """`trello_cursor` (087): por quadro, a última action lida pela reconciliação. A linha SÓ existe depois da 1ª leitura
    (é o que separa "1ª subida", em que o histórico é descartado, de "quadro vazio"). `atualizado_em` anda a cada leitura
    que deu certo, mesmo sem action nova: é o que a saúde compara para dizer que o leitor parou."""

    def __init__(self, db: Database, relogio: Callable[[], datetime]):
        self.db = db
        self._relogio = relogio

    def ler(self, quadro: str) -> Row | None:
        return self.db.one("SELECT * FROM trello_cursor WHERE quadro=?", (quadro,))

    def gravar(self, quadro: str, ultima_action: str | None, ultima_data: str | None) -> None:
        """Fixa o cursor. `None` NÃO apaga o que já estava (um quadro sem action nova mantém o marco anterior)."""
        em = to_iso(self._relogio())
        self.db.execute(
            "INSERT INTO trello_cursor(quadro, ultima_action, ultima_data, atualizado_em) VALUES (?,?,?,?)"
            " ON CONFLICT (quadro) DO UPDATE SET ultima_action=COALESCE(excluded.ultima_action, trello_cursor.ultima_action),"
            " ultima_data=COALESCE(excluded.ultima_data, trello_cursor.ultima_data), atualizado_em=excluded.atualizado_em",
            (quadro, ultima_action, ultima_data, em))

    def ultima_leitura(self) -> datetime | None:
        """A leitura mais recente que deu certo, em qualquer quadro; `None` antes da 1ª."""
        v = self.db.scalar("SELECT MAX(atualizado_em) FROM trello_cursor")
        return parse_iso(str(v)) if v else None
