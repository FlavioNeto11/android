"""Pacote A4 do ADR-054 (a preencher): `POST|GET /api/runs/{run_id}/feedback` (o botão do D2, 409
`note_looks_secret` sem gravar) e `GET /api/aprendizado/sinais`."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api")
