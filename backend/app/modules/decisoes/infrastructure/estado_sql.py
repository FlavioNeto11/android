"""O que o adaptador e o resumo lembram entre voltas e reinícios (`decisoes_automaticas_estado`, migração 102)."""
from __future__ import annotations

from app.db import Database


class EstadoDasDecisoes:
    def __init__(self, db: Database):
        self.db = db

    def texto(self, chave: str) -> str | None:
        r = self.db.one("SELECT valor FROM decisoes_automaticas_estado WHERE chave=?", (chave,))
        return str(r["valor"]) if r is not None else None

    def gravar(self, chave: str, valor: str) -> None:
        self.db.execute(
            "INSERT INTO decisoes_automaticas_estado(chave, valor) VALUES (?,?)"
            " ON CONFLICT (chave) DO UPDATE SET valor=excluded.valor", (chave, valor))

    def inteiro(self, chave: str) -> int:
        try:
            return int(self.texto(chave) or 0)
        except ValueError:
            return 0

    def gravar_inteiro(self, chave: str, valor: int) -> None:
        self.gravar(chave, str(valor))
