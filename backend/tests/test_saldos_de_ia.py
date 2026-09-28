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
    # Leitura velha SEM chave de administrador não é "desatualizada": o livro-caixa segue pelo consumo medido.
    saldos.registrar_leitura(db, "anthropic", 9.25, observed_at=to_iso(now() - timedelta(hours=100)))
    assert not saldos.de_uma(db, cfg, "anthropic").stale
    # Com chave de administrador, desatualizada = sem conciliação recente (ou com erro).
    from pydantic import SecretStr
    cfg.env.anthropic_admin_key = SecretStr("chave-falsa-de-teste")
    assert not saldos.de_uma(db, cfg, "anthropic").stale            # âncora nova: o laço ainda vai consultar
    depois = now() + timedelta(minutes=saldos.CONCILIACAO_VELHA_MIN + 1)
    a = saldos.estado(db, cfg, agora=depois, so="anthropic")[0]
    assert a.stale and "aguardando a primeira consulta" in a.message
    ancora = int(db.scalar("SELECT MAX(id) FROM ai_balance_snapshots WHERE account='anthropic'"))
    saldos.CONCILIACOES["anthropic"] = saldos.Conciliacao(
        account="anthropic", snapshot_id=ancora, window_start="", window_end="", provider_usd=None, local_usd=0.0,
        fetched_at=to_iso(now()), error="chave de administrador recusada (401)")
    assert "recusada (401)" in saldos.de_uma(db, cfg, "anthropic").message
    saldos.CONCILIACOES["anthropic"] = saldos.Conciliacao(
        account="anthropic", snapshot_id=ancora, window_start="", window_end="", provider_usd=0.0, local_usd=0.0,
        fetched_at=to_iso(now()))
    assert not saldos.de_uma(db, cfg, "anthropic").stale
    saldos.CONCILIACOES.clear()
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


# ====================================================================== conciliação pelo relatório do provedor
def _transporte(respostas: dict[str, httpx.Response], vistos: list[httpx.Request]) -> httpx.MockTransport:
    def responde(req: httpx.Request) -> httpx.Response:
        vistos.append(req)
        return respostas[req.url.host]
    return httpx.MockTransport(responde)


async def test_conciliacao_desconta_so_o_gasto_de_fora_depois_da_leitura(tmp_path: Path) -> None:
    from pydantic import SecretStr

    from app.planning import conciliacao

    saldos.CONCILIACOES.clear()
    cfg = _cfg(tmp_path)
    cfg.file.ai.prices["gpt-6-luna"] = [1.0, 0.0, 0.0, 0.0]
    cfg.env.anthropic_admin_key = SecretStr("chave-falsa-de-teste")
    cfg.env.openai_admin_key = SecretStr("chave-falsa-de-teste")
    db = _db(tmp_path)
    cfg.file.ai.prices["claude-sonnet-5"] = [2.0, 0.2, 2.5, 10.0]
    leitura = now() - timedelta(hours=5)
    saldos.registrar_leitura(db, "openai", 8.39, observed_at=to_iso(leitura))
    saldos.registrar_leitura(db, "anthropic", 9.25, observed_at=to_iso(leitura))
    vistos: list[httpx.Request] = []

    def relatorio(openai_usd: float, anthropic_entrada: int, anthropic_cache_1h: int) -> dict[str, httpx.Response]:
        return {
            # OpenAI: `organization/costs`, `amount.value` em dólares.
            "api.openai.com": httpx.Response(200, json={"data": [{"results": [
                {"amount": {"value": openai_usd, "currency": "usd"}}]}], "has_more": False, "next_page": None}),
            # Anthropic: `usage_report/messages` por hora e modelo — TOKENS, que a plataforma precifica.
            "api.anthropic.com": httpx.Response(200, json={"data": [{"starting_at": "", "ending_at": "", "results": [
                {"model": "claude-sonnet-5", "uncached_input_tokens": anthropic_entrada, "cache_read_input_tokens": 0,
                 "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": anthropic_cache_1h},
                 "output_tokens": 0}]}], "has_more": False, "next_page": None}),
        }

    # 1ª conciliação = no instante da leitura: o que já estava no relatório (US$ 1,00 na OpenAI; US$ 2,00 na
    # Anthropic) o console já tinha descontado → vira linha de base, nada sai do saldo.
    async with httpx.AsyncClient(transport=_transporte(relatorio(1.0, 1_000_000, 0), vistos)) as client:
        await conciliacao.atualizar(db, cfg, forcar=True, client=client)
    o = saldos.de_uma(db, cfg, "openai")
    assert o.external_usd == 0.0 and o.estimated_balance == pytest.approx(8.39)
    base = db.one("SELECT provider_baseline_usd, local_baseline_usd FROM ai_balance_snapshots WHERE account='openai'")
    assert base["provider_baseline_usd"] == pytest.approx(1.0) and base["local_baseline_usd"] == 0.0
    assert saldos.de_uma(db, cfg, "anthropic").estimated_balance == pytest.approx(9.25)

    # Depois: US$ 1 pela plataforma na OpenAI e o relatório sobe para 3,50 → 1,50 de fora DEPOIS da leitura.
    # Na Anthropic, +500 mil tokens de cache de 1 h (2× a entrada = US$ 2,00), nada disso pela plataforma.
    _gasto(db, to_iso(leitura + timedelta(minutes=1)), "openai", "gpt-6-luna", 1_000_000)
    async with httpx.AsyncClient(transport=_transporte(relatorio(3.5, 1_000_000, 500_000), vistos)) as client:
        await conciliacao.atualizar(db, cfg, forcar=True, client=client)
    o = saldos.de_uma(db, cfg, "openai")
    assert o.provider_usd == pytest.approx(3.5) and o.external_usd == pytest.approx(1.5)
    assert o.estimated_balance == pytest.approx(8.39 - 1.0 - 1.5) and "fora da plataforma" in o.message
    a = saldos.de_uma(db, cfg, "anthropic")
    assert a.provider_usd == pytest.approx(4.0) and a.external_usd == pytest.approx(2.0)
    assert a.estimated_balance == pytest.approx(9.25 - 2.0)
    por_host = {r.url.host: r for r in vistos}
    anth = por_host["api.anthropic.com"]
    assert anth.url.path == "/v1/organizations/usage_report/messages"
    assert anth.headers["x-api-key"] == "chave-falsa-de-teste" and "authorization" not in anth.headers
    assert por_host["api.openai.com"].headers["authorization"] == "Bearer chave-falsa-de-teste"
    # Janela da Anthropic: da hora cheia da leitura até a última hora cheia, baldes de 1 h, por modelo.
    assert anth.url.params["starting_at"] == leitura.strftime("%Y-%m-%dT%H:00:00Z")
    assert anth.url.params["ending_at"] == now().strftime("%Y-%m-%dT%H:00:00Z")
    assert anth.url.params["bucket_width"] == "1h" and anth.url.params["group_by[]"] == "model"
    # Uma leitura NOVA troca a âncora: a conciliação antiga deixa de valer até a próxima busca.
    saldos.registrar_leitura(db, "openai", 5.0)
    assert saldos.de_uma(db, cfg, "openai").external_usd == 0.0
    saldos.CONCILIACOES.clear()
    db.close()


async def test_conciliacao_com_chave_recusada_nao_derruba_e_nao_vaza(tmp_path: Path) -> None:
    from pydantic import SecretStr

    from app.planning import conciliacao

    saldos.CONCILIACOES.clear()
    cfg = _cfg(tmp_path)
    cfg.env.openai_admin_key = SecretStr("chave-falsa-de-teste")
    db = _db(tmp_path)
    saldos.registrar_leitura(db, "openai", 8.39)
    saldos.registrar_leitura(db, "anthropic", 9.25)          # sem chave de admin da Anthropic: não busca
    vistos: list[httpx.Request] = []
    respostas = {"api.openai.com": httpx.Response(401, text="invalid key chave-falsa-de-teste")}
    async with httpx.AsyncClient(transport=_transporte(respostas, vistos)) as client:
        await conciliacao.atualizar(db, cfg, forcar=True, client=client)
    assert [r.url.host for r in vistos] == ["api.openai.com"]
    o = saldos.de_uma(db, cfg, "openai")
    assert o.reconcile_error == "chave de administrador recusada (401)" and o.external_usd == 0.0
    assert o.estimated_balance == pytest.approx(8.39) and o.admin_key_configured
    assert not saldos.de_uma(db, cfg, "anthropic").admin_key_configured
    saldos.CONCILIACOES.clear()
    db.close()


async def test_anthropic_com_leitura_nesta_hora_nao_pergunta(tmp_path: Path) -> None:
    """O relatório de uso da Anthropic cobre até a última hora cheia: com a leitura desta hora, não há balde fechado a
    perguntar. O externo fica zero e o saldo segue pelo gasto local — sem erro no painel."""
    from pydantic import SecretStr

    from app.planning import conciliacao

    saldos.CONCILIACOES.clear()
    cfg = _cfg(tmp_path)
    cfg.env.anthropic_admin_key = SecretStr("chave-falsa-de-teste")
    db = _db(tmp_path)
    saldos.registrar_leitura(db, "anthropic", 9.25)
    vistos: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=_transporte({}, vistos)) as client:
        await conciliacao.atualizar(db, cfg, forcar=True, client=client)
    a = saldos.de_uma(db, cfg, "anthropic")
    assert vistos == [] and a.provider_usd == 0.0 and a.reconcile_error is None and a.estimated_balance == 9.25
    saldos.CONCILIACOES.clear()
    db.close()


async def test_uso_separa_o_custo_por_conta(harness: Harness) -> None:
    """GET /api/usage traz `by_account`: de qual saldo o custo saiu, na janela e por execução (ADR-051)."""
    st = harness.state
    assert st is not None
    st.cfg.file.ai.prices.update({"gpt-6-luna": [1.0, 0, 0, 0], "gemini-3.1-flash-lite": [1.0, 0, 0, 0],
                                  "claude-sonnet-5": [2.0, 0.2, 2.5, 10.0]})
    agora = to_iso(now())
    for run, prov, modelo in (("r-a", "openai", "gpt-6-luna"), ("r-a", None, "gemini-3.1-flash-lite"),
                              ("r-b", "anthropic", "claude-sonnet-5"), ("r-b", "simulated", "simulado")):
        st.db.execute("INSERT INTO ai_calls(ts, run_id, role, model, provider, input_tokens, output_tokens)"
                      " VALUES (?,?,?,?,?,?,?)", (agora, run, "decide", modelo, prov, 1_000_000, 0))
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        semana = (await c.get("/api/usage", params={"days": 7})).json()["by_account"]
        assert semana == {"openai": 1.0, "gemini": 1.0, "anthropic": 2.0}      # o simulado não é conta de ninguém
        assert (await c.get("/api/usage", params={"run_id": "r-a"})).json()["by_account"] == {"openai": 1.0,
                                                                                               "gemini": 1.0}


def test_leitura_em_outra_moeda_vira_a_moeda_da_conta(tmp_path: Path) -> None:
    """O AI Studio pode mostrar em US$ (navegador em inglês) numa conta em R$: a âncora fica sempre na moeda da conta."""
    cfg = _cfg(tmp_path)
    db = _db(tmp_path)
    saldos.registrar_leitura(db, "gemini", 5.0, currency="USD", source="console")        # câmbio padrão 5,2
    g = saldos.de_uma(db, cfg, "gemini")
    assert g.currency == "BRL" and g.anchor_balance == pytest.approx(26.0)
    with pytest.raises(ValueError, match="não tem câmbio"):
        saldos.registrar_leitura(db, "openai", 52.0, currency="BRL")
    db.close()


def test_recarga_soma_ao_saldo_de_agora_e_sem_credito_recomeca_de_zero(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.file.ai.prices["gpt-6-luna"] = [1.0, 0.0, 0.0, 0.0]
    db = _db(tmp_path)
    with pytest.raises(LookupError, match="saldo atual do console"):
        saldos.registrar_recarga(db, cfg, "openai", 10.0)                  # sem âncora nenhuma: não inventa saldo
    leitura = now() - timedelta(hours=1)
    saldos.registrar_leitura(db, "openai", 8.25, observed_at=to_iso(leitura))
    _gasto(db, to_iso(leitura + timedelta(minutes=1)), "openai", "gpt-6-luna", 250_000)      # US$ 0,25
    saldos.registrar_recarga(db, cfg, "openai", 10.0)
    o = saldos.de_uma(db, cfg, "openai")
    assert o.anchor_source == "recarga" and o.anchor_balance == pytest.approx(18.0)       # 8,25 − 0,25 + 10
    assert "recarga de US$ 10,00" in (o.anchor_note or "")
    # Gemini em R$: recarga em US$ entra pelo câmbio da conta; R$ numa conta em US$ é recusado.
    saldos.registrar_leitura(db, "gemini", 29.37)
    saldos.registrar_recarga(db, cfg, "gemini", 10.0, currency="USD")                     # câmbio padrão 5,2
    assert saldos.de_uma(db, cfg, "gemini").anchor_balance == pytest.approx(81.37)
    with pytest.raises(ValueError, match="não tem câmbio"):
        saldos.registrar_recarga(db, cfg, "openai", 50.0, currency="BRL")
    # Sem crédito (erro de cobrança): a recarga recomeça de zero, e a trava sai.
    saldos.registrar_esgotado(db, cfg, "openai", "402")
    assert saldos.de_uma(db, cfg, "openai").state == "exhausted"
    saldos.registrar_recarga(db, cfg, "openai", 5.0)
    o = saldos.de_uma(db, cfg, "openai")
    assert o.anchor_balance == pytest.approx(5.0) and o.state == "ok"
    db.close()


def test_fechamento_diario_vira_ancora_sem_perder_saldo(tmp_path: Path) -> None:
    from pydantic import SecretStr

    cfg = _cfg(tmp_path)
    cfg.file.ai.prices["gpt-6-luna"] = [1.0, 0.0, 0.0, 0.0]
    db = _db(tmp_path)
    antiga = now() - timedelta(hours=30)
    saldos.registrar_leitura(db, "openai", 8.25, observed_at=to_iso(antiga))
    saldos.registrar_leitura(db, "gemini", 29.37, observed_at=to_iso(now() - timedelta(hours=2)))   # nova: fica
    _gasto(db, to_iso(antiga + timedelta(hours=1)), "openai", "gpt-6-luna", 1_000_000)             # US$ 1
    assert saldos.fechar_dia(db, cfg) == ["openai"]
    o = saldos.de_uma(db, cfg, "openai")
    assert o.anchor_source == "fechamento" and o.anchor_balance == pytest.approx(7.25)
    assert o.estimated_balance == pytest.approx(7.25)                    # o gasto antigo não sai duas vezes
    assert saldos.fechar_dia(db, cfg) == []                              # âncora nova: nada a fechar
    # Não fecha conta sem crédito nem conta com conciliação falhando (congelaria o saldo sem o gasto de fora).
    saldos.registrar_leitura(db, "anthropic", 9.0, observed_at=to_iso(antiga))
    cfg.env.anthropic_admin_key = SecretStr("chave-falsa-de-teste")
    tarde = now() + timedelta(minutes=saldos.CONCILIACAO_VELHA_MIN + 1)
    assert saldos.fechar_dia(db, cfg, agora=tarde) == []                 # sem conciliação: desatualizada
    saldos.registrar_esgotado(db, cfg, "openai", "402")
    db.execute("UPDATE ai_balance_snapshots SET observed_at=? WHERE source='provider_error'", (to_iso(antiga),))
    assert "openai" not in saldos.fechar_dia(db, cfg)
    db.close()


async def test_api_de_recarga(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/ai/balances/openai/recharge", json={"amount": 10})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "no_initial_balance"
        await c.post("/api/ai/balances/openai", json={"balance": 8.25, "source": "console"})
        r = await c.post("/api/ai/balances/openai/recharge", json={"amount": 10, "note": "cartão final 1366"})
        assert r.status_code == 201, r.text
        o = next(x for x in r.json()["accounts"] if x["account"] == "openai")
        assert o["anchor_source"] == "recarga" and o["anchor_balance"] == pytest.approx(18.25)
        assert (await c.post("/api/ai/balances/openai/recharge", json={"amount": 0})).status_code == 422
        assert (await c.post("/api/ai/balances/openai/recharge", json={"amount": 5, "currency": "BRL"})).status_code == 400

