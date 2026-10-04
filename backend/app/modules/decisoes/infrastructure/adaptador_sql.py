"""O adaptador do 28.25: recolhe, nos eventos e na trilha do aprendizado, o que a plataforma decidiu sozinha (item 28.25).

Idempotente em dois níveis: o cursor de cada fonte evita reler, e a `origem_ref` única do registro faz a releitura (cursor
perdido, duas réplicas) cair em `DO NOTHING`. Por isso roda em qualquer backend; o resumo é que é do líder.

Os eventos têm retenção, e a tabela é grande: a consulta filtra por `kind` e por um `LIKE` no texto do `data` (só o
vencimento do 31.43 tem a chave `"vencimento"`), e o cursor avança até o maior id lido mesmo quando nada casou, para a
varredura seguinte não repassar o mesmo trecho.

Janela de releitura (28.29, revisão independente do deploy 30): no PostgreSQL o id de sequência fica VISÍVEL fora de
ordem. A transação longa que pegou o id N pode confirmar depois de outra ter confirmado o N+1, quando a varredura já
passou de N; sem releitura, a decisão N nunca entraria no registro. Cada volta relê os últimos `RELEITURA_*` ids antes do
cursor: o `origem_ref` único faz a releitura cair em `DO NOTHING`. No SQLite (um escritor só) a janela é inofensiva.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from app.db import Database, loads
from app.modules.decisoes.domain.leitura import PLATAFORMA, decisao_de_evento, decisao_de_transicao
from app.modules.decisoes.infrastructure.estado_sql import EstadoDasDecisoes
from app.modules.decisoes.infrastructure.registro_sql import RegistroSql

log = logging.getLogger("poc.decisoes")

LOTE = 200
#: Quantos ids antes do cursor cada volta relê (a transação que confirma atrasada cabe aí com folga).
RELEITURA_EVENTOS = 2000
RELEITURA_APRENDIZADO = 500
CURSOR_EVENTOS = "cursor:eventos"
CURSOR_APRENDIZADO = "cursor:aprendizado"
KINDS_DE_VENCIMENTO = ("run.updated", "objective.updated")
CHAVE_NO_DATA = '%"vencimento"%'


class AdaptadorDeDecisoes:
    def __init__(self, db: Database, registro: RegistroSql, estado: EstadoDasDecisoes, *,
                 redigir: Callable[[str], str] | None = None):
        self.db = db
        self.registro = registro
        self.estado = estado
        self._redigir = redigir

    def varrer(self) -> int:
        """Uma volta nas duas fontes. Devolve quantas linhas NOVAS entraram. Falha de uma fonte não impede a outra."""
        novas = 0
        for fonte in (self._eventos, self._aprendizado):
            try:
                novas += fonte()
            except Exception:  # noqa: BLE001 - o adaptador nunca derruba o laço; a próxima volta relê
                log.exception("decisoes: falha ao recolher %s", fonte.__name__)
        return novas

    def _eventos(self) -> int:
        novas = 0
        cursor = self.estado.inteiro(CURSOR_EVENTOS)
        teto = int(self.db.scalar("SELECT COALESCE(MAX(id), 0) FROM events") or 0)
        desde = max(0, min(cursor, teto) - RELEITURA_EVENTOS)       # a janela vale mesmo sem evento novo
        marcas = ",".join("?" for _ in KINDS_DE_VENCIMENTO)
        while True:
            linhas = self.db.query(
                "SELECT id, ts, kind, run_id, objective_id, data FROM events WHERE id > ? AND id <= ?"
                f" AND kind IN ({marcas}) AND data LIKE ? ORDER BY id LIMIT ?",
                (desde, teto, *KINDS_DE_VENCIMENTO, CHAVE_NO_DATA, LOTE))
            for r in linhas:
                d = decisao_de_evento(str(r["kind"]), loads(r["data"], {}), ts=str(r["ts"]), run_id=r["run_id"],
                                      objective_id=r["objective_id"])
                if d is not None and self.registro.registrar(d):
                    novas += 1
            if len(linhas) < LOTE:
                break
            desde = int(linhas[-1]["id"])
            if desde > cursor:
                self.estado.gravar_inteiro(CURSOR_EVENTOS, desde)    # o progresso de um lote cheio sobrevive à queda
        if teto > cursor:
            self.estado.gravar_inteiro(CURSOR_EVENTOS, teto)
        return novas

    def _aprendizado(self) -> int:
        novas = 0
        cursor = self.estado.inteiro(CURSOR_APRENDIZADO)
        desde = max(0, cursor - RELEITURA_APRENDIZADO)
        while True:
            linhas = self.db.query(
                "SELECT id, item_ref, item_kind, from_state, to_state, reason, decided_by, decided_at"
                " FROM learning_transitions WHERE id > ? AND decided_by = ? ORDER BY id LIMIT ?",
                (desde, PLATAFORMA, LOTE))
            for r in linhas:
                d = decisao_de_transicao(
                    transicao_id=int(r["id"]), item_ref=str(r["item_ref"]), item_kind=str(r["item_kind"]),
                    de=r["from_state"], para=str(r["to_state"]), reason=str(r["reason"]), decided_by=str(r["decided_by"]),
                    decided_at=str(r["decided_at"]), redigir=self._redigir)
                if d is not None and self.registro.registrar(d):
                    novas += 1
            if linhas:
                desde = int(linhas[-1]["id"])
                if desde > cursor:
                    cursor = desde
                    self.estado.gravar_inteiro(CURSOR_APRENDIZADO, cursor)
            if len(linhas) < LOTE:
                return novas
