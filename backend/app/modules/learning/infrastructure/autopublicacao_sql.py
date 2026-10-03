"""O livro da sombra da autopublicação (30.34) sobre as tabelas que já existem, sem migração.

- O caso é um sinal `kind = autopublicaria`, `source_ref = autopublicaria:<item>`, `created_by = sistema`. O índice
  único `(kind, source_ref, created_by)` faz dele UMA linha por item: a segunda marca do mesmo item não grava nada.
- Os eventos depois da marca saem de onde já moram: a evidência REAL contra ou em conflito (`learning_evidence`), a
  trilha que desligou o item (`learning_transitions`) e o parecer que uma pessoa recusou (o sinal `parecer_decidido`,
  que guarda a data da decisão).

Nos dois dialetos: o `data` dos sinais é filtrado aqui, sem `json_extract` (os pareceres decididos são poucos).
"""
from __future__ import annotations

from datetime import datetime

from app.db import Database
from app.modules.learning.application.ports import NovoSinal, RepositorioDeAprendizado
from app.modules.learning.domain.autopublicacao import CasoDaSombra, EventoDoCaso, Regressao
from app.modules.learning.domain.ciclo import SYSTEM_ACTOR
from app.modules.learning.domain.vocabulario import Polaridade, SignalKind
from app.modules.learning.infrastructure import linhas
from app.util import parse_iso, to_iso

PREFIXO = "autopublicaria:"


class LivroDaSombraSql:
    def __init__(self, db: Database, repo: RepositorioDeAprendizado) -> None:
        self._db = db
        self._repo = repo

    def casos(self) -> list[CasoDaSombra]:
        saida: list[CasoDaSombra] = []
        for r in self._db.query("SELECT source_ref, created_at FROM learning_signals WHERE kind=? AND created_by=?"
                                " ORDER BY created_at, id", (SignalKind.AUTOPUBLICARIA.value, SYSTEM_ACTOR)):
            em = parse_iso(linhas.texto(r, "created_at"))
            if em is not None:
                saida.append(CasoDaSombra(linhas.texto(r, "source_ref").removeprefix(PREFIXO), em))
        return saida

    def marcar(self, item_ref: str, dados: dict[str, object], *, app: str | None) -> bool:
        novo = self._repo.registrar_sinal(NovoSinal(
            kind=SignalKind.AUTOPUBLICARIA, source_ref=f"{PREFIXO}{item_ref}", created_by=SYSTEM_ACTOR,
            polarity=Polaridade.NEUTRAL, app_package=app or "", data=dados))  # type: ignore[arg-type]
        return novo is not None

    def eventos(self, item_ref: str, desde: datetime) -> list[EventoDoCaso]:
        quando = to_iso(desde)
        eventos: list[EventoDoCaso] = []

        def somar(tipo: Regressao, texto: str | None) -> None:
            em = parse_iso(texto) if texto else None
            if em is not None:
                eventos.append(EventoDoCaso(tipo, em))

        for r in self._db.query("SELECT e.observed_at FROM learning_evidence e WHERE e.item_ref=? AND e.simulated=0"
                                f" AND {linhas.contra_efetivo('e')} AND e.observed_at >= ?", (item_ref, quando)):
            somar(Regressao.EVIDENCIA_CONTRA, linhas.texto_ou_nulo(r, "observed_at"))
        for r in self._db.query("SELECT decided_at FROM learning_transitions WHERE item_ref=? AND to_state='disabled'"
                                " AND (from_state IS NULL OR from_state <> 'disabled') AND decided_at >= ?",
                                (item_ref, quando)):
            somar(Regressao.DESLIGADO, linhas.texto_ou_nulo(r, "decided_at"))
        for r in self._db.query("SELECT created_at, data FROM learning_signals WHERE kind=? AND created_at >= ?",
                                (SignalKind.PARECER_DECIDIDO.value, quando)):
            d = linhas.json_objeto(r, "data")
            if d.get("item_ref") == item_ref and d.get("decisao_final") == "recusou":
                somar(Regressao.PARECER_RECUSADO, linhas.texto_ou_nulo(r, "created_at"))
        return eventos


__all__ = ["LivroDaSombraSql", "PREFIXO"]
