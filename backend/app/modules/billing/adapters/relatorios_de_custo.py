"""Relatório de custo da organização nos provedores de IA (ADR-051), por HTTP com chave de ADMINISTRADOR.

Adaptador puro: recebe o cliente, a chave e a janela e devolve o que o provedor informa — tokens por hora e modelo
na Anthropic, US$ por dia na OpenAI. Quem decide quando buscar e o que fazer com o número é
`app.planning.conciliacao`. A chave vai só no cabeçalho do próprio provedor.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

OPENAI_URL = "https://api.openai.com/v1/organization/costs"
#: Os dois relatórios devolvem no máximo 31 baldes diários por página.
DIAS_POR_PAGINA = 31
PAGINAS_MAX = 10


USO_ANTHROPIC_URL = "https://api.anthropic.com/v1/organizations/usage_report/messages"


@dataclass(frozen=True)
class UsoPorModelo:
    """Tokens de um modelo num balde do relatório de uso da Anthropic."""
    model: str
    uncached_input: int
    cache_read: int
    cache_write_5m: int
    cache_write_1h: int
    output: int
#: Baldes de 1 h por página no relatório de uso (máximo da API: 168).
HORAS_POR_PAGINA = 168


def cobertura_anthropic(agora: datetime) -> datetime:
    """Até onde o relatório de uso da Anthropic cobre: a ÚLTIMA HORA CHEIA (medido em 28/09/2026, 18:09 UTC: o balde
    17:00–18:00 já vinha; o da hora corrente, não)."""
    return agora.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


async def uso_anthropic(client: httpx.AsyncClient, chave: str, desde: datetime, ate: datetime) -> list[UsoPorModelo]:
    """`GET /v1/organizations/usage_report/messages`, baldes de 1 h em [desde, ate), por modelo. Devolve uma linha
    por (hora, modelo) com os tokens — o preço é da plataforma (`ai.prices`), a mesma tabela do custo local."""
    if ate <= desde:
        return []
    linhas: list[UsoPorModelo] = []
    pagina = None
    for _ in range(PAGINAS_MAX):
        params: dict[str, str | int] = {"starting_at": desde.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                        "ending_at": ate.strftime("%Y-%m-%dT%H:%M:%SZ"), "bucket_width": "1h",
                                        "group_by[]": "model", "limit": HORAS_POR_PAGINA}
        if pagina:
            params["page"] = pagina
        r = await client.get(USO_ANTHROPIC_URL, params=params,
                             headers={"x-api-key": chave, "anthropic-version": "2023-06-01"})
        r.raise_for_status()
        corpo = r.json()
        for balde in corpo.get("data", []):
            for item in balde.get("results", []):
                criacao = item.get("cache_creation") or {}
                linhas.append(UsoPorModelo(
                    model=str(item.get("model") or ""),
                    uncached_input=int(item.get("uncached_input_tokens") or 0),
                    cache_read=int(item.get("cache_read_input_tokens") or 0),
                    cache_write_5m=int(criacao.get("ephemeral_5m_input_tokens") or 0),
                    cache_write_1h=int(criacao.get("ephemeral_1h_input_tokens") or 0),
                    output=int(item.get("output_tokens") or 0)))
        pagina = corpo.get("next_page")
        if not corpo.get("has_more") or not pagina:
            break
    return linhas


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


#: O cliente HTTP que as buscas recebem (o teste passa um com transporte falso).
Cliente = httpx.AsyncClient


def novo_cliente(prazo_s: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=prazo_s)
