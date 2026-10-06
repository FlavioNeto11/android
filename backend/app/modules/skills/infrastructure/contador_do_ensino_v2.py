"""31.91 T1 (ADR-078): quantas vezes alguém chamou uma rota OBSOLETA do ensino v2 (`/teaching-sessions*`, `/skill-candidates*`).

Persistido (tabela `settings`, uma linha JSON) porque o deploy reinicia o processo e a regra do T2 é "14 dias com zero chamadas":
um contador em memória zeraria a cada subida. Nunca derruba a rota que conta: a falha do contador só vai para o log. Sem corpo,
sem operador e sem id de recurso: só o método e o molde da rota.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.db import Database
from app.util import now_iso

log = logging.getLogger(__name__)
CHAVE = "ensino_v2.chamadas"


@dataclass
class _Medida:
    total: int = 0
    desde: str | None = None
    ultima: str | None = None
    por_rota: dict[str, int] = field(default_factory=dict)

    def como_dict(self) -> dict[str, object]:
        return {"total": self.total, "desde": self.desde, "ultima": self.ultima, "por_rota": dict(self.por_rota)}


class ContadorDoEnsinoV2:
    def __init__(self, db: Database) -> None:
        self.db = db

    def registrar(self, metodo: str, molde: str) -> None:
        try:
            agora = now_iso()
            with self.db.tx():
                m = self._ler()
                m.total += 1
                m.desde = m.desde or agora
                m.ultima = agora
                rotulo = f"{metodo} {molde}"
                m.por_rota[rotulo] = m.por_rota.get(rotulo, 0) + 1
                self.db.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (CHAVE, json.dumps(m.como_dict())))
        except Exception:  # noqa: BLE001 - contar nunca derruba a rota
            log.warning("contador do ensino v2: não consegui registrar a chamada", exc_info=True)

    def resumo(self) -> dict[str, object]:
        """`{total, desde, ultima, por_rota}`; antes da primeira chamada, `total: 0` e o resto vazio."""
        try:
            return self._ler().como_dict()
        except Exception:  # noqa: BLE001 - saúde nunca falha por causa de um enfeite dela
            return _Medida().como_dict()

    def _ler(self) -> _Medida:
        bruto = self.db.scalar("SELECT value FROM settings WHERE key=?", (CHAVE,))
        valor = json.loads(bruto) if bruto else None
        if not isinstance(valor, dict):
            return _Medida()
        total, desde, ultima, por_rota = valor.get("total"), valor.get("desde"), valor.get("ultima"), valor.get("por_rota")
        return _Medida(total=total if isinstance(total, int) else 0,
                       desde=desde if isinstance(desde, str) else None, ultima=ultima if isinstance(ultima, str) else None,
                       por_rota={str(k): v for k, v in por_rota.items() if isinstance(v, int)}
                       if isinstance(por_rota, dict) else {})
