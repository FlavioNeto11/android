"""O registro da conversa do Telegram (migração 085, item 28.15), em SQLite e PostgreSQL.

- `telegram_mensagens` é a fonte do offset. A update é gravada ANTES de o `getUpdates` seguinte confirmá-la
  (`offset = MAX(update_id) + 1`): uma queda entre receber e gravar faz o Telegram reentregar, e o `UNIQUE (update_id)`
  faz a releitura cair no `ON CONFLICT DO NOTHING`. Linha `recebida` que sobrou de uma queda no meio do tratamento é
  tratada de novo na volta seguinte; a ação dela é idempotente (a chave `telegram:<update_id>` na criação, o estado
  na aprovação e na resposta).
- `telegram_enviadas` é o que a Central mandou. É o que liga o reply ao fato do aviso, e o que separa o reply à
  Central do reply à orquestradora.

Nada de chat_id aqui (a v1 aceita só o do `.env`); o texto de outro chat e o que parece credencial nem chegam a ser
gravados (`texto` NULL).
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

from app.db import Database
from app.util import to_iso

MAX_CURTO = 1000
MAX_ERRO = 300


def _curto(texto: str | None, n: int = MAX_CURTO) -> str | None:
    return None if texto is None else texto.strip()[:n]


class MensagensDoTelegram:
    def __init__(self, db: Database, *, relogio: Callable[[], datetime] | None = None):
        self.db = db
        self.relogio: Callable[[], datetime] = relogio if relogio is not None else db.agora

    def _agora(self) -> str:
        return to_iso(self.relogio())

    # ------------------------------------------------------------------ entrada
    def proximo_offset(self) -> int:
        """O `offset` do próximo `getUpdates`: tudo abaixo dele já está gravado. Banco vazio: 0 (o que o Telegram
        tiver guardado, que ninguém confirmou)."""
        ultimo = self.db.scalar("SELECT MAX(update_id) FROM telegram_mensagens")
        return int(ultimo) + 1 if ultimo is not None else 0

    def gravar(self, *, update_id: int, tipo: str, do_chat: bool, message_id: int | None, responde_a: int | None,
               texto: str | None, tamanho: int, estado: str = "recebida", erro: str | None = None) -> bool:
        """Grava a update. Devolve se a linha é nova. `estado` final já na gravação para o que não se trata (outro
        chat, credencial): o texto destes nunca é gravado, então não há o que tratar depois."""
        agora = self._agora()
        tratada = None if estado == "recebida" else agora
        cur = self.db.execute(
            "INSERT INTO telegram_mensagens(update_id, tipo, do_chat, message_id, responde_a, texto, tamanho, estado,"
            " erro, recebida_em, tratada_em) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (update_id) DO NOTHING",
            (int(update_id), tipo, 1 if do_chat else 0, message_id, responde_a, texto, int(tamanho), estado,
             _curto(erro, MAX_ERRO), agora, tratada))
        return (cur.rowcount or 0) == 1

    def a_tratar(self, limite: int = 50) -> list[dict[str, object]]:
        """As linhas `recebida`, na ordem do Telegram (inclusive as que uma queda deixou no meio)."""
        return [dict(r) for r in self.db.query(
            "SELECT * FROM telegram_mensagens WHERE estado='recebida' ORDER BY update_id LIMIT ?", (limite,))]

    def linha(self, ident: int) -> dict[str, object] | None:
        r = self.db.one("SELECT * FROM telegram_mensagens WHERE id=?", (int(ident),))
        return dict(r) if r is not None else None

    def marcar(self, ident: int, estado: str, *, intencao: str | None = None, destino: str | None = None,
               alvo: str | None = None, previa: Mapping[str, object] | None = None, run_id: str | None = None,
               resposta: str | None = None, erro: str | None = None, de: tuple[str, ...] = ()) -> bool:
        """Muda o estado (e o que a ação deixou). `de`: só muda se o estado atual for um destes (o segundo toque no
        mesmo botão perde aqui). Campos `None` não apagam o que já estava gravado."""
        sets = ["estado=?", "tratada_em=?"]
        args: list[object] = [estado, self._agora()]
        for coluna, valor in (("intencao", intencao), ("destino", destino), ("alvo", alvo), ("run_id", run_id),
                              ("resposta", _curto(resposta)), ("erro", _curto(erro, MAX_ERRO)),
                              ("previa", json.dumps(previa, ensure_ascii=False) if previa is not None else None)):
            if valor is not None:
                sets.append(f"{coluna}=?")
                args.append(valor)
        sql = f"UPDATE telegram_mensagens SET {', '.join(sets)} WHERE id=?"  # colunas fixas acima, nada vem de fora
        args.append(int(ident))
        if de:
            sql += f" AND estado IN ({','.join('?' * len(de))})"
            args.extend(de)
        return (self.db.execute(sql, tuple(args)).rowcount or 0) == 1

    def do_chat_na_janela(self, segundos: float, *, ate_id: int) -> int:
        """Mensagens do chat configurado nos últimos `segundos`, até a linha `ate_id` inclusive (o limite de taxa).
        O lote inteiro é gravado antes de tratar: sem o `ate_id`, as primeiras de um lote grande seriam contadas
        com as que vieram depois delas."""
        desde = to_iso(self.relogio() - timedelta(seconds=segundos))
        return int(self.db.scalar(
            "SELECT COUNT(*) FROM telegram_mensagens WHERE do_chat=1 AND tipo='mensagem' AND recebida_em >= ?"
            " AND id <= ?", (desde, int(ate_id))) or 0)

    # ------------------------------------------------------------------ o que a Central mandou
    def registrar_enviada(self, message_id: int | None, origem: str, *, fato: str | None = None,
                          mensagem_id: int | None = None) -> None:
        if message_id is None:
            return
        self.db.execute(
            "INSERT INTO telegram_enviadas(message_id, origem, fato, mensagem_id, enviada_em) VALUES (?,?,?,?,?)"
            " ON CONFLICT (message_id) DO NOTHING", (int(message_id), origem, fato, mensagem_id, self._agora()))

    def enviada(self, message_id: int) -> dict[str, object] | None:
        r = self.db.one("SELECT * FROM telegram_enviadas WHERE message_id=?", (int(message_id),))
        return dict(r) if r is not None else None

    def da_pessoa(self, message_id: int) -> bool:
        """`message_id` é de uma mensagem que a PESSOA mandou (reply a ela não é reply ao bot)."""
        return self.db.one("SELECT 1 AS x FROM telegram_mensagens WHERE message_id=? AND tipo='mensagem' AND do_chat=1",
                           (int(message_id),)) is not None

    def ajuda_ja_enviada(self) -> bool:
        return self.db.one("SELECT 1 AS x FROM telegram_enviadas WHERE origem='ajuda' LIMIT 1") is not None

    # ------------------------------------------------------------------ desfecho na conversa
    def esperando_desfecho(self, limite: int = 20) -> list[dict[str, object]]:
        return [dict(r) for r in self.db.query(
            "SELECT id, message_id, run_id FROM telegram_mensagens WHERE run_id IS NOT NULL AND resultado_em IS NULL"
            " AND estado='feita' ORDER BY id LIMIT ?", (limite,))]

    def marcar_desfecho(self, ident: int) -> None:
        self.db.execute("UPDATE telegram_mensagens SET resultado_em=? WHERE id=?", (self._agora(), int(ident)))

    # ------------------------------------------------------------------ leitura (saúde e orquestradora)
    def contagens(self) -> dict[str, int]:
        linhas = self.db.query("SELECT estado, COUNT(*) AS n FROM telegram_mensagens GROUP BY estado")
        return {str(r["estado"]): int(r["n"]) for r in linhas}
