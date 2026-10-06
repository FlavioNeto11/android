"""30.34: quantos pedidos de prova nasceram do parecer `observar` da classe B (no app de prova), por estado.

Persistido (tabela `settings`, uma linha JSON), como o contador do ensino v2: o deploy reinicia o processo, e a medida do
efeito (a leitura de 12/10 do 30.72) precisa da conta desde a ligação. Nunca derruba o parecer que conta: a falha só vai
para o log. Sem item, sem comando e sem aparelho: só o estado do pedido (`pendente` ou o motivo da recusa).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.db import Database
from app.util import now_iso

log = logging.getLogger(__name__)
CHAVE = "aprendizado.validacao.pelo_observar_b"


@dataclass
class _Medida:
    total: int = 0
    desde: str | None = None
    ultima: str | None = None
    por_estado: dict[str, int] = field(default_factory=dict)

    def como_dict(self) -> dict[str, object]:
        return {"total": self.total, "desde": self.desde, "ultima": self.ultima, "por_estado": dict(self.por_estado)}


class ContadorPeloObservar:
    def __init__(self, db: Database) -> None:
        self.db = db

    def registrar(self, estado: str) -> None:
        try:
            agora = now_iso()
            with self.db.tx():
                m = self._ler()
                m.total += 1
                m.desde = m.desde or agora
                m.ultima = agora
                m.por_estado[estado] = m.por_estado.get(estado, 0) + 1
                self.db.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET"
                                " value=excluded.value", (CHAVE, json.dumps(m.como_dict())))
        except Exception:  # noqa: BLE001 - contar nunca derruba o parecer
            log.warning("contador do observar B: não consegui registrar o pedido", exc_info=True)

    def resumo(self) -> dict[str, object]:
        """`{total, desde, ultima, por_estado}`; antes do primeiro pedido, `total: 0` e o resto vazio."""
        try:
            return self._ler().como_dict()
        except Exception:  # noqa: BLE001 - saúde nunca falha por causa de um enfeite dela
            return _Medida().como_dict()

    def _ler(self) -> _Medida:
        bruto = self.db.scalar("SELECT value FROM settings WHERE key=?", (CHAVE,))
        valor = json.loads(bruto) if bruto else None
        if not isinstance(valor, dict):
            return _Medida()
        total, desde, ultima, por = valor.get("total"), valor.get("desde"), valor.get("ultima"), valor.get("por_estado")
        return _Medida(total=total if isinstance(total, int) else 0,
                       desde=desde if isinstance(desde, str) else None, ultima=ultima if isinstance(ultima, str) else None,
                       por_estado={str(k): v for k, v in por.items() if isinstance(v, int)}
                       if isinstance(por, dict) else {})


__all__ = ["CHAVE", "ContadorPeloObservar"]
