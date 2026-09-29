"""Pacote A3 do ADR-054 (a preencher): `GET /api/aprendizado/falhas?dias=&app=&camada=&formato=json|md` e
`GET|PATCH /api/aprendizado/backlog/{id}`. Toda rota nova com teste HTTP (`test_cobertura_de_rotas`)."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/aprendizado")
