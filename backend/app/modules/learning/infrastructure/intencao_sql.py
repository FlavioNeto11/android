"""`RegistroDeRotulos` (30.25) sobre `learning_reviews` (069) e as leituras da execução que o rótulo precisa.

As linhas do rótulo são as de `template_id = intencao`; tudo aqui filtra por isso, como os leitores do curador filtram
`template_id = curador` (`revisoes_sql.py`). O INSERT é o mesmo do curador (`RegistroDeRevisoesSql.gravar`, com o
`ON CONFLICT` da chave única (item, dossiê)); "uma linha por execução" é conferido antes (`tem_rotulo`), porque o
catálogo pode mudar entre dois digests da mesma execução e o dossiê novo teria outro hash.
"""
from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime

from app.db import Database, Row
from app.modules.learning.application.intencao import RotuloGravado
from app.modules.learning.application.ports import NovaRevisao
from app.modules.learning.domain.intencao import (TEMPLATE_DO_ROTULO, DossieDoRotulo, FatosDaExecucao,
                                                  item_ref_da_execucao, run_id_do_item)
from app.modules.learning.infrastructure import linhas
from app.modules.learning.infrastructure.revisoes_sql import RegistroDeRevisoesSql

_COLUNAS = "id, created_at, item_ref, scope_app, dossie, decisao_final, decidido_por"
_DO_ROTULO = f"template_id='{TEMPLATE_DO_ROTULO}'"


def _rotulo(row: Row) -> RotuloGravado | None:
    run_id = run_id_do_item(linhas.texto(row, "item_ref"))
    if run_id is None:
        return None
    return RotuloGravado(id=linhas.texto(row, "id"), criado_em=linhas.texto(row, "created_at"), run_id=run_id,
                         app=linhas.texto(row, "scope_app"),
                         dossie=DossieDoRotulo.de_dados(linhas.json_objeto(row, "dossie")),
                         decisao_final=linhas.texto_ou_nulo(row, "decisao_final"),
                         decidido_por=linhas.texto_ou_nulo(row, "decidido_por"))


def _comprovada(row: Row) -> bool:
    """A etapa comprovada pela verificação (a regra de `licoes_sql._comprovada`): `succeeded` com `result.verified`.
    A confirmação à mão grava `verified=false` e não conta."""
    if linhas.texto(row, "status") != "succeeded":
        return False
    bruto = linhas.texto_ou_nulo(row, "result")
    try:
        resultado = json.loads(bruto) if bruto else None
    except ValueError:
        return False
    return isinstance(resultado, dict) and resultado.get("verified") is True


class RotulosSql:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._revisoes = RegistroDeRevisoesSql(db)

    @contextmanager
    def transacao(self) -> Iterator[None]:
        with self._db.tx():
            yield

    def fatos(self, run_id: str) -> FatosDaExecucao | None:
        run = self._db.one("SELECT status, simulated, skill_id FROM runs WHERE id=?", (run_id,))
        if run is None:
            return None
        etapas = self._db.query("SELECT status, result FROM steps WHERE run_id=?", (run_id,))
        return FatosDaExecucao(status=linhas.texto(run, "status"), simulated=bool(linhas.inteiro(run, "simulated")),
                               etapas=len(etapas), etapas_comprovadas=sum(1 for s in etapas if _comprovada(s)),
                               resolvida_no_plano=bool(linhas.texto_ou_nulo(run, "skill_id")))

    def tem_rotulo(self, run_id: str) -> bool:
        return self._db.one(f"SELECT 1 AS x FROM learning_reviews WHERE item_ref=? AND {_DO_ROTULO}",
                            (item_ref_da_execucao(run_id),)) is not None

    def gravar(self, nova: NovaRevisao, agora: datetime) -> str | None:
        return self._revisoes.gravar(nova, agora)

    def da_execucao(self, run_id: str) -> RotuloGravado | None:
        r = self._db.one(f"SELECT {_COLUNAS} FROM learning_reviews WHERE item_ref=? AND {_DO_ROTULO}"
                         " ORDER BY created_at, id LIMIT 1", (item_ref_da_execucao(run_id),))
        return None if r is None else _rotulo(r)

    def pendentes(self, limite: int) -> list[RotuloGravado]:
        saida: list[RotuloGravado] = []
        for r in self._db.query(f"SELECT {_COLUNAS} FROM learning_reviews WHERE {_DO_ROTULO}"
                                " AND decisao_final IS NULL ORDER BY created_at DESC, id DESC LIMIT ?", (limite,)):
            rotulo = _rotulo(r)
            if rotulo is not None and rotulo.dossie is not None:
                saida.append(rotulo)
        return saida

    def contar_pendentes(self) -> int:
        r = self._db.one(f"SELECT COUNT(*) AS n FROM learning_reviews WHERE {_DO_ROTULO} AND decisao_final IS NULL")
        return linhas.inteiro(r, "n") if r is not None else 0

    def execucoes(self, run_ids: Sequence[str]) -> dict[str, tuple[str | None, str | None]]:
        """`run_id → (comando, fim)`, lidos agora (o comando nunca vai ao dossiê). Em lotes, nunca uma leitura por
        linha."""
        saida: dict[str, tuple[str | None, str | None]] = {}
        for lote in linhas.lotes(sorted(set(run_ids))):
            for r in self._db.query("SELECT id, command, finished_at FROM runs"
                                    f" WHERE id IN ({linhas.marcas(len(lote))})", tuple(lote)):
                saida[linhas.texto(r, "id")] = (linhas.texto_ou_nulo(r, "command"),
                                                linhas.texto_ou_nulo(r, "finished_at"))
        return saida

    def decidir(self, review_id: str, *, decisao_final: str, decidido_por: str) -> bool:
        """CAS: só o rótulo ainda sem resposta."""
        cur = self._db.execute("UPDATE learning_reviews SET decisao_final=?, decidido_por=? WHERE id=?"
                               f" AND {_DO_ROTULO} AND decisao_final IS NULL", (decisao_final, decidido_por, review_id))
        return (cur.rowcount or 0) == 1


__all__ = ["RotulosSql"]
