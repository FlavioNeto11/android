"""Relatório de custo da organização nos provedores de IA (ADR-051), por HTTP com chave de ADMINISTRADOR.

Adaptador puro: recebe o cliente, a chave e o início da janela e devolve US$. Quem decide quando buscar e o que
fazer com o número é `app.planning.conciliacao`. A chave vai só no cabeçalho do próprio provedor.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

ANTHROPIC_URL = "https://api.anthropic.com/v1/organizations/cost_report"
OPENAI_URL = "https://api.openai.com/v1/organization/costs"
#: Os dois relatórios devolvem no máximo 31 baldes diários por página.
DIAS_POR_PAGINA = 31
PAGINAS_MAX = 10


def cobertura_anthropic(agora: datetime) -> datetime:
    """Até onde o relatório da Anthropic cobre: só DIAS FECHADOS (medido em 28/09/2026 — o balde de hoje não sai, e
    `starting_at` hoje responde 400 "ending date must be after starting date"). Então: meia-noite UTC de hoje."""
    return agora.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


async def custo_anthropic(client: httpx.AsyncClient, chave: str, desde: datetime, ate: datetime) -> float:
    """`GET /v1/organizations/cost_report`, baldes de 1 dia em [desde, ate). `amount` vem em CENTAVOS."""
    if ate <= desde:
        return 0.0                          # janela ainda sem dia fechado: nada a perguntar (a API daria 400)
    total, pagina = 0.0, None
    for _ in range(PAGINAS_MAX):
        params: dict[str, str | int] = {"starting_at": desde.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                        "ending_at": ate.strftime("%Y-%m-%dT%H:%M:%SZ"), "bucket_width": "1d",
                                        "limit": DIAS_POR_PAGINA}
        if pagina:
            params["page"] = pagina
        r = await client.get(ANTHROPIC_URL, params=params,
                             headers={"x-api-key": chave, "anthropic-version": "2023-06-01"})
        r.raise_for_status()
        corpo = r.json()
        for balde in corpo.get("data", []):
            for item in balde.get("results", []):
                if str(item.get("currency", "USD")).upper() == "USD":
                    total += float(item.get("amount") or 0) / 100
        pagina = corpo.get("next_page")
        if not corpo.get("has_more") or not pagina:
            break
    return round(total, 6)


def cobertura_openai(agora: datetime) -> datetime:
    """O relatório da OpenAI traz o dia corrente (parcial, com algum atraso): cobre até agora."""
    return agora


async def custo_openai(client: httpx.AsyncClient, chave: str, desde: datetime, ate: datetime) -> float:
    """`GET /v1/organization/costs`, baldes de 1 dia a partir de `desde`. `amount.value` já vem em DÓLARES."""
    total, pagina = 0.0, None
    for _ in range(PAGINAS_MAX):
        params: dict[str, str | int] = {"start_time": int(desde.timestamp()), "bucket_width": "1d",
                                        "limit": DIAS_POR_PAGINA}
        if pagina:
            params["page"] = pagina
        r = await client.get(OPENAI_URL, params=params, headers={"Authorization": f"Bearer {chave}"})
        r.raise_for_status()
        corpo = r.json()
        for balde in corpo.get("data", []):
            for item in balde.get("results", []):
                valor = item.get("amount") or {}
                if str(valor.get("currency", "usd")).lower() == "usd":
                    total += float(valor.get("value") or 0)
        pagina = corpo.get("next_page")
        if not corpo.get("has_more") or not pagina:
            break
    return round(total, 6)


def erro_legivel(exc: Exception) -> str:
    """Só o status e o tipo — nunca o corpo, que pode ecoar parte da requisição."""
    if isinstance(exc, httpx.HTTPStatusError):
        codigo = exc.response.status_code
        if codigo in (401, 403):
            return f"chave de administrador recusada ({codigo})"
        return f"o provedor respondeu {codigo}"
    return type(exc).__name__


def novo_cliente(prazo_s: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=prazo_s)
