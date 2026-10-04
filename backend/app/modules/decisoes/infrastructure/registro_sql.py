"""O livro das decisões automáticas (migração 102), em SQLite e PostgreSQL (item 28.25).

- `registrar` é idempotente pela `origem_ref` (`ON CONFLICT DO NOTHING`, e não `except IntegrityError`: no PostgreSQL o
  erro abortaria a transação de quem chama). Duas réplicas, ou o mesmo evento relido, produzem UMA linha.
- `marcar_desfeita` é um CAS em `desfeita_em IS NULL`: desfazer duas vezes não muda quem desfez nem o motivo.
- O tempo é o relógio injetado (o do banco, por padrão), como na fila de avisos (item 5.3).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Literal

from app.db import Database, dumps, loads
from app.modules.decisoes.domain.decisao import Decisao
from app.shared.decisoes import NovaDecisao, decisao_valida
from app.util import to_iso

FiltroDeDesfeitas = Literal["todas", "sim", "nao"]
LIMITE_PADRAO = 200
LIMITE_MAX = 500


def _decisao(r: object) -> Decisao:
    fatos = loads(r["fatos"], {})  # type: ignore[index]
    return Decisao(
        id=int(r["id"]), fila=str(r["fila"]), item_ref=str(r["item_ref"]), origem_ref=str(r["origem_ref"]),  # type: ignore[index]
        regra=str(r["regra"]), efeito=str(r["efeito"]), fatos=fatos if isinstance(fatos, dict) else {},  # type: ignore[index]
        decidida_em=str(r["decidida_em"]), resumida_em=r["resumida_em"], desfeita_em=r["desfeita_em"],  # type: ignore[index]
        desfeita_por=r["desfeita_por"], motivo_do_desfazer=r["motivo_do_desfazer"])  # type: ignore[index]


class RegistroSql:
    def __init__(self, db: Database, *, relogio: Callable[[], datetime] | None = None):
        self.db = db
        self.relogio: Callable[[], datetime] = relogio if relogio is not None else db.agora

    def agora(self) -> datetime:
        return self.relogio()

    # ------------------------------------------------------------------ escrita
    def registrar(self, decisao: NovaDecisao) -> bool:
        d = decisao_valida(decisao)
        cur = self.db.execute(
            "INSERT INTO decisoes_automaticas(fila, item_ref, origem_ref, regra, efeito, fatos, decidida_em)"
            " VALUES (?,?,?,?,?,?,?) ON CONFLICT (origem_ref) DO NOTHING",
            (d.fila, d.item_ref, d.origem_ref, d.regra, d.efeito, dumps(dict(d.fatos)),
             d.decidida_em or to_iso(self.relogio())))
        return (cur.rowcount or 0) == 1

    def marcar_desfeita(self, decisao_id: int, *, por: str, motivo: str | None) -> bool:
        """Grava o desfazer. Devolve se foi ESTA chamada que desfez (`False` = já estava desfeita, nada mudou)."""
        cur = self.db.execute(
            "UPDATE decisoes_automaticas SET desfeita_em=?, desfeita_por=?, motivo_do_desfazer=?"
            " WHERE id=? AND desfeita_em IS NULL",
            (to_iso(self.relogio()), por, (motivo or "").strip()[:300] or None, decisao_id))
        return (cur.rowcount or 0) == 1

    # ------------------------------------------------------------------ leitura
    def obter(self, decisao_id: int) -> Decisao | None:
        r = self.db.one("SELECT * FROM decisoes_automaticas WHERE id=?", (decisao_id,))
        return _decisao(r) if r is not None else None

    def listar(self, *, regra: str | None = None, fila: str | None = None, desde: str | None = None,
               ate: str | None = None, desfeitas: FiltroDeDesfeitas = "todas",
               limite: int = LIMITE_PADRAO) -> list[Decisao]:
        """As mais novas primeiro. `desde`/`ate` são UTC ISO sobre `decidida_em` (`desde` inclusivo, `ate` exclusivo)."""
        onde, params = ["1=1"], []
        if regra:
            onde.append("regra=?")
            params.append(regra)
        if fila:
            onde.append("fila=?")
            params.append(fila)
        if desde:
            onde.append("decidida_em >= ?")
            params.append(desde)
        if ate:
            onde.append("decidida_em < ?")
            params.append(ate)
        if desfeitas == "sim":
            onde.append("desfeita_em IS NOT NULL")
        elif desfeitas == "nao":
            onde.append("desfeita_em IS NULL")
        params.append(max(1, min(int(limite), LIMITE_MAX)))
        return [_decisao(r) for r in self.db.query(
            f"SELECT * FROM decisoes_automaticas WHERE {' AND '.join(onde)} ORDER BY decidida_em DESC, id DESC LIMIT ?",
            tuple(params))]

    def regras(self) -> list[str]:
        """As regras que já decidiram alguma coisa (para o filtro do painel)."""
        return [str(r["regra"]) for r in self.db.query(
            "SELECT DISTINCT regra FROM decisoes_automaticas ORDER BY regra")]
