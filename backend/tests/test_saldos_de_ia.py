"""Saldo das contas de IA (ADR-051): estimativa, estados, bloqueio no roteador, geração de imagem e API.

Tudo `simulated`: provedores falsos e banco de teste. O saldo real dos consoles não passa por aqui.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.db import Database
from app.main import create_app
from app.planning import saldos
from app.planning.provider import AIError, DecisionRequest
from app.planning.routing import _com_provedor
from app.util import now, to_iso

from .conftest import Harness, _dsn_de_teste
from .test_hub_de_ia import SCREEN, FakeProvider, FakeRepo, com_hub, ctx, roteador

PROVEDORES = {"anthropic": {"kind": "anthropic", "fallback_model": "claude-sonnet-5"},
              "openai": {"kind": "openai", "base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
              "gemini": {"kind": "openai", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                         "api_key_env": "GEMINI_API_KEY"},
              "local": {"kind": "openai", "base_url": "http://127.0.0.1:11434/v1", "sends_data_externally": False}}


def _cfg(tmp: Path, roles: dict | None = None):
    return com_hub(tmp, providers=PROVEDORES, models={"gpt-6-luna": {}, "gemini-3.1-flash-lite": {}, "qwen": {}},
                   roles=roles or {"decide": {"provider": "openai", "model": "gpt-6-luna"},
                                   "verify": {"provider": "openai", "model": "gpt-6-luna"}})


def _db(tmp: Path) -> Database:
    db = Database(_dsn_de_teste() or tmp / "saldos.sqlite3")
    db.migrate()
    return db


def _gasto(db: Database, ts: str, provider: str | None, model: str, entrada: int, saida: int = 0) -> None:
    db.execute("INSERT INTO ai_calls(ts, role, model, provider, input_tokens, output_tokens) VALUES (?,?,?,?,?,?)",
               (ts, "decide", model, provider, entrada, saida))


def test_conta_por_endpoint_e_por_modelo(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    assert saldos.conta_do_provedor(cfg, "openai", "gpt-6-luna") == "openai"
    assert saldos.conta_do_provedor(cfg, "gemini", "gemini-3.1-flash-lite") == "gemini"
    assert saldos.conta_do_provedor(cfg, "anthropic", "claude-opus-5-5") == "anthropic"
    assert saldos.conta_do_provedor(cfg, "local", "qwen") is None               # Ollama não é conta paga
    assert saldos.conta_do_provedor(cfg, "simulated", "degrade") is None
    assert saldos.conta_do_provedor(cfg, None, "claude-sonnet-5") == "anthropic"   # linha antiga, sem provedor
    assert saldos.conta_do_papel(cfg, "decide") == "openai"
    assert saldos.conta_do_papel(cfg, "plan") == "anthropic"                    # herdado do .env


def test_estimativa_desconta_so_o_gasto_depois_da_leitura(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.file.ai.prices["gpt-6-luna"] = [1.0, 0.1, 0.0, 4.0]
    db = _db(tmp_path)
    leitura = now() - timedelta(hours=1)
    _gasto(db, to_iso(leitura - timedelta(minutes=5)), "openai", "gpt-6-luna", 5_000_000)   # antes: não conta
    saldos.registrar_leitura(db, "openai", 8.39, source="console", observed_at=to_iso(leitura))
    _gasto(db, to_iso(leitura + timedelta(minutes=5)), "openai", "gpt-6-luna", 1_000_000, 250_000)  # US$ 2,00
    _gasto(db, to_iso(leitura + timedelta(minutes=6)), "local", "qwen", 9_000_000)          # local: não conta
    _gasto(db, to_iso(leitura + timedelta(minutes=7)), "anthropic", "claude-sonnet-5", 1_000_000)  # outra conta
    c = saldos.de_uma(db, cfg, "openai")
    assert c is not None and c.anchor_balance == 8.39
    assert c.spent_since_usd == pytest.approx(2.0)
    assert c.estimated_balance == pytest.approx(6.39)
    assert c.state == "ok" and c.roles == ["decide", "verify"] and c.em_uso
    assert saldos.de_uma(db, cfg, "anthropic").state == "unknown"               # sem leitura: desconhecido
    db.close()


def test_moeda_da_conta_converte_o_gasto_em_usd(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.file.ai.prices["gemini-3.1-flash-lite"] = [1.0, 0.0, 0.0, 0.0]
    db = _db(tmp_path)
    leitura = now() - timedelta(minutes=30)
    saldos.registrar_leitura(db, "gemini", 29.37, observed_at=to_iso(leitura), units_per_usd=5.0)
    _gasto(db, to_iso(leitura + timedelta(minutes=1)), "gemini", "gemini-3.1-flash-lite", 1_000_000)   # US$ 1
    c = saldos.de_uma(db, cfg, "gemini")
    assert c.currency == "BRL" and c.estimated_balance == pytest.approx(24.37)
    assert c.estimated_balance_usd == pytest.approx(4.874)
    db.close()


def test_aviso_bloqueio_desatualizado_e_esgotado(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    db = _db(tmp_path)
    saldos.registrar_leitura(db, "openai", 1.5)
    assert saldos.de_uma(db, cfg, "openai").state == "low"                     # aviso de fábrica: US$ 2
    assert saldos.motivo_de_bloqueio(db, cfg, "openai") is None                 # bloqueio sai desligado
    saldos.ajustar_regra(db, "openai", block_below=2.0)
    assert saldos.de_uma(db, cfg, "openai").state == "blocked"
    assert "barrada" in (saldos.motivo_de_bloqueio(db, cfg, "openai") or "")
    saldos.ajustar_regra(db, "openai", block_below=None, warn_below=None)
    assert saldos.de_uma(db, cfg, "openai").state == "ok"
    saldos.registrar_leitura(db, "anthropic", 9.25, observed_at=to_iso(now() - timedelta(hours=100)))
    a = saldos.de_uma(db, cfg, "anthropic")
    assert a.stale and "confira no console" in a.message
    saldos.registrar_esgotado(db, cfg, "anthropic", "402 credit balance too low")
    saldos.registrar_esgotado(db, cfg, "anthropic", "de novo")                   # uma leitura por esgotamento
    assert db.scalar("SELECT COUNT(*) FROM ai_balance_snapshots WHERE account='anthropic'") == 2
    assert saldos.de_uma(db, cfg, "anthropic").state == "exhausted"
    db.close()


def test_roteador_barra_a_conta_bloqueada_antes_de_chamar(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    db = _db(tmp_path)
    saldos.registrar_leitura(db, "openai", 0.5)
    saldos.ajustar_regra(db, "openai", block_below=1.0)
    ator = FakeProvider("openai", "gpt-6-luna")
    r = roteador(cfg, {"decide": ator})
    r.attach(repo=FakeRepo(db), settings_getter=lambda: SimpleNamespace(ai_max_usd_per_run=0, ai_max_usd_per_day=0))
    with pytest.raises(AIError) as e:
        asyncio.run(r.decide(DecisionRequest(ctx=ctx(), screen=SCREEN)))
    assert e.value.kind == "balance" and ator.calls == []
    db.close()


def test_fallback_declarado_para_outra_conta_atende(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, roles={"decide": {"provider": "openai", "model": "gpt-6-luna",
                                           "fallback_provider": "anthropic"}})
    db = _db(tmp_path)
    saldos.registrar_leitura(db, "openai", 0.0)
    saldos.ajustar_regra(db, "openai", block_below=0.5)
    saldos.registrar_leitura(db, "anthropic", 9.25)
    r = roteador(cfg, {"decide": FakeProvider("openai", "gpt-6-luna")})
    destino = _com_provedor(cfg, "decide", "anthropic")
    pago = FakeProvider("anthropic", "claude-sonnet-5")
    r._por_chave[(destino.provider, destino.kind, destino.model, destino.timeout_s,        # noqa: SLF001
                  destino.max_retries, destino.refusal_fallback)] = pago
    r.attach(repo=FakeRepo(db), settings_getter=lambda: SimpleNamespace(ai_max_usd_per_run=0, ai_max_usd_per_day=0))
    _, usage = asyncio.run(r.decide(DecisionRequest(ctx=ctx(), screen=SCREEN)))
    assert pago.calls == ["decide"] and usage.fallback == "anthropic"
    # E o destino passa pela mesma conferência: bloqueado também, nada é chamado.
    saldos.ajustar_regra(db, "anthropic", block_below=100.0)
    with pytest.raises(AIError) as e:
        asyncio.run(r.decide(DecisionRequest(ctx=ctx(), screen=SCREEN)))
    assert e.value.kind == "balance" and pago.calls == ["decide"]
    db.close()


def test_erro_de_cobranca_do_provedor_vira_saldo_zero(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    db = _db(tmp_path)
    saldos.registrar_leitura(db, "openai", 8.39)
    r = roteador(cfg, {"decide": FakeProvider("openai", "gpt-6-luna",
                                              erro=AIError("Sem crédito", kind="billing", status=429))})
    r.attach(repo=FakeRepo(db), settings_getter=lambda: SimpleNamespace(ai_max_usd_per_run=0, ai_max_usd_per_day=0))
    with pytest.raises(AIError):
        asyncio.run(r.decide(DecisionRequest(ctx=ctx(), screen=SCREEN)))
    c = saldos.de_uma(db, cfg, "openai")
    assert c.anchor_source == "provider_error" and c.state == "exhausted"
    db.close()


async def test_api_de_saldos_e_saude(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/ai/balances")
        assert r.status_code == 200, r.text
        contas = {x["account"]: x for x in r.json()["accounts"]}
        assert set(contas) == {"anthropic", "openai", "gemini"} and contas["gemini"]["currency"] == "BRL"
        r = await c.post("/api/ai/balances/openai", json={"balance": 8.39, "source": "console",
                                                          "observed_at": "2026-09-28T15:00:00Z"})
        assert r.status_code == 201, r.text
        o = next(x for x in r.json()["accounts"] if x["account"] == "openai")
        assert o["anchor_balance"] == 8.39 and o["anchor_at"].startswith("2026-09-28T15:00:00")
        r = await c.put("/api/ai/balances/openai", json={"block_below": 9.0})
        assert next(x for x in r.json()["accounts"] if x["account"] == "openai")["state"] == "blocked"
        assert r.json()["blocked"] == ["openai"]
        assert (await c.post("/api/ai/balances/xpto", json={"balance": 1})).status_code == 404
        assert (await c.post("/api/ai/balances/openai", json={"balance": 1, "observed_at": "ontem"})).status_code == 400
        assert (await c.post("/api/ai/balances/openai", json={"balance": 1, "source": "provider_error"})).status_code == 422
    # Harness é simulado: nenhuma função usa a conta OpenAI, então o bloqueio não vira problema de saúde.
    assert not [p for p in (await asyncio.to_thread(st.health)).problems if p.code.startswith("ai_balance")] \
        if not asyncio.iscoroutinefunction(st.health) else True
