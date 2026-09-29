"""Pacote A9 do ADR-054 (a preencher, opcional): `GET /api/aprendizado/voz/previa?profile_id=` — os pares de voz
publicados pelo dono para aquele perfil, sem IA."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/aprendizado")
