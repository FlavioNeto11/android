"""Pacote A8 do ADR-054 (a preencher): `GET /api/aprendizado/export?kind=tela&app=<pacote>` — o fragmento YAML da
tela aprendida, com a proveniência, para uma sessão de desenvolvimento commitar no repositório."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/aprendizado")
