"""Pacote A7 do ADR-054 (a preencher): `GET /api/aprendizado/licoes/previa?app=&acao=&papel=` — o bloco exato que iria
ao prompt e os tokens, sem IA. Registrada antes do livro (`router.py`)."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/aprendizado")
