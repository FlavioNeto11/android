"""Os contatos de um canal que não são o dono (item 28.18, migração 090): o estado de cada chat e o histórico do que
aconteceu com ele. Só guarda e lê; quem decide o que fazer é `convidados.ConvidadosDoTelegram`."""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.db import Database
from app.util import to_iso

#: Teto do texto que fica no histórico (já redigido): o resto da mensagem não é guardado.
MAX_DETALHE = 500
ESTADOS = frozenset({"aguardando_nome", "aguardando_dono", "autorizado", "recusado"})


@dataclass(frozen=True)
class Contato:
    chat_id: str
    estado: str
    nome_informado: str | None
    nome_pedido: bool


class ContatosDoCanal:
    def __init__(self, db: Database, *, canal: str = "telegram", relogio: Callable[[], datetime] | None = None):
        self.db = db
        self.canal = canal
        self.relogio: Callable[[], datetime] = relogio if relogio is not None else db.agora

    def _agora(self) -> str:
        return to_iso(self.relogio())

    def obter(self, chat_id: str) -> Contato | None:
        r = self.db.one("SELECT chat_id, estado, nome_informado, nome_pedido_em FROM canal_contatos"
                        " WHERE canal=? AND chat_id=?", (self.canal, chat_id))
        if r is None:
            return None
        return Contato(str(r["chat_id"]), str(r["estado"]), r["nome_informado"], r["nome_pedido_em"] is not None)

    def criar(self, chat_id: str, perfil: Mapping[str, object] | None) -> bool:
        """O chat novo entra em `aguardando_nome`. Devolve se a linha é nova (a releitura não cria outra)."""
        agora = self._agora()
        cur = self.db.execute(
            "INSERT INTO canal_contatos(canal, chat_id, estado, perfil, primeira_em, ultima_em)"
            " VALUES (?,?,'aguardando_nome',?,?,?) ON CONFLICT (canal, chat_id) DO NOTHING",
            (self.canal, chat_id, _perfil(perfil), agora, agora))
        return (cur.rowcount or 0) == 1

    def tocar(self, chat_id: str, perfil: Mapping[str, object] | None) -> None:
        """A última vez que o chat falou, e o perfil mais recente que o Telegram deu (a pessoa pode trocar o @)."""
        self.db.execute("UPDATE canal_contatos SET ultima_em=?, perfil=COALESCE(?, perfil) WHERE canal=? AND chat_id=?",
                        (self._agora(), _perfil(perfil), self.canal, chat_id))

    def marcar_nome_pedido(self, chat_id: str) -> None:
        self.db.execute("UPDATE canal_contatos SET nome_pedido_em=? WHERE canal=? AND chat_id=?",
                        (self._agora(), self.canal, chat_id))

    def gravar_nome(self, chat_id: str, nome: str) -> bool:
        """`aguardando_nome` → `aguardando_dono`. Devolve se mudou (a mesma mensagem relida não muda nada)."""
        cur = self.db.execute(
            "UPDATE canal_contatos SET nome_informado=?, estado='aguardando_dono'"
            " WHERE canal=? AND chat_id=? AND estado='aguardando_nome'", (nome, self.canal, chat_id))
        return (cur.rowcount or 0) == 1

    def decidir(self, chat_id: str, estado: str) -> bool:
        """A decisão do dono: `autorizado` ou `recusado`. Vale sobre qualquer estado depois do nome (o dono pode mudar de
        ideia respondendo de novo ao mesmo aviso). Devolve se mudou."""
        assert estado in ("autorizado", "recusado")
        cur = self.db.execute(
            "UPDATE canal_contatos SET estado=?, decidido_em=? WHERE canal=? AND chat_id=?"
            " AND estado IN ('aguardando_dono', 'autorizado', 'recusado') AND estado<>?",
            (estado, self._agora(), self.canal, chat_id, estado))
        return (cur.rowcount or 0) == 1

    def evento(self, chat_id: str, tipo: str, detalhe: str | None = None, tamanho: int = 0) -> None:
        self.db.execute(
            "INSERT INTO canal_contato_eventos(canal, chat_id, tipo, detalhe, tamanho, em) VALUES (?,?,?,?,?,?)",
            (self.canal, chat_id, tipo, None if detalhe is None else detalhe.strip()[:MAX_DETALHE], int(tamanho),
             self._agora()))

    def eventos_desde(self, chat_id: str, tipo: str, segundos: float, *, detalhe: str | None = None) -> int:
        limite = to_iso(self.relogio() - timedelta(seconds=segundos))
        sql = "SELECT COUNT(*) FROM canal_contato_eventos WHERE canal=? AND chat_id=? AND tipo=? AND em > ?"
        params: tuple[object, ...] = (self.canal, chat_id, tipo, limite)
        if detalhe is not None:
            sql, params = sql + " AND detalhe=?", (*params, detalhe)
        return int(self.db.scalar(sql, params) or 0)

    def novos_desde(self, segundos: float) -> int:
        limite = to_iso(self.relogio() - timedelta(seconds=segundos))
        return int(self.db.scalar("SELECT COUNT(*) FROM canal_contatos WHERE canal=? AND primeira_em > ?",
                                  (self.canal, limite)) or 0)


def _perfil(perfil: Mapping[str, object] | None) -> str | None:
    """Só os campos de identidade que o Telegram dá no `from`; nada do conteúdo da mensagem."""
    if not perfil:
        return None
    campos = ("id", "first_name", "last_name", "username", "language_code", "is_bot", "is_premium")
    return json.dumps({k: perfil[k] for k in campos if k in perfil}, ensure_ascii=False, sort_keys=True)
