"""31.31 (migração 092): a gravação de cache de 1 h à parte em `ai_calls`, cobrada a 2x a entrada.

Desde o 31.30, o prefixo do plano da execução vai ao cache com `ttl: 1h`. A gravação de 1 h custa 2x a entrada base, e
a de 5 min, 1,25x (o 3º preço de `ai.prices`). Com uma coluna só, a estimativa ao vivo ficava abaixo do real nos planos
frios. Nível de prova: `simulated` (resposta falsa do provedor, banco de teste). Prova-se que:
- a migração cria a coluna;
- o provedor lê `usage.cache_creation.ephemeral_1h_input_tokens` e o repositório o grava (0 vira NULO);
- `spent_usd`, `usd_por`, o saldo por conta e o `/api/usage` cobram a parte de 1 h a 2x a entrada;
- a linha sem a coluna (consulta antiga, legado NULO) custa o mesmo de antes.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.planning import costs
from app.planning.provider import Usage

from .conftest import Harness
from .test_anthropic_provider import APP, provider
from .test_observabilidade_das_chamadas import _db, _usage

#: [entrada, cache lido, cache gravado (5 min), saída] por milhão: a entrada a US$ 2 faz a gravação de 1 h custar US$ 4.
PRECOS = {"m-a": [2.0, 0.2, 2.5, 10.0]}


def test_migracao_092_cria_a_coluna(tmp_path: Path) -> None:
    assert "cache_write_1h" in _db(tmp_path).columns("ai_calls")


def test_extra_de_1h_e_a_diferenca_sobre_o_preco_de_5_min() -> None:
    # 1 M gravado por 1 h: 2 × 2,0 = US$ 4,00, dos quais US$ 2,50 já saem pelo preço de 5 min.
    assert costs.extra_1h(PRECOS, "m-a", 1_000_000) == pytest.approx(1.5)
    assert costs.extra_1h(PRECOS, "m-a", 0) == 0.0 and costs.extra_1h(PRECOS, "m-a", None) == 0.0
    # modelo sem preço paga a tarifa mais cara, como em `usd`
    assert costs.extra_1h(PRECOS, "sem-preco", 1_000_000) == pytest.approx(1.5)
    # linha sem a coluna: o custo de antes
    linha = {"model": "m-a", "input_tokens": 0, "cache_read": 0, "cache_write": 1_000_000, "output_tokens": 0}
    assert costs.row_usd(PRECOS, _Linha(linha)) == pytest.approx(2.5)
    assert costs.row_usd(PRECOS, _Linha({**linha, "cache_write_1h": 1_000_000})) == pytest.approx(4.0)


class _Linha(dict[str, Any]):
    """Como a linha do banco: `keys()` e acesso por nome."""


def test_spent_usd_usd_por_e_saldo_cobram_a_parte_de_1h(tmp_path: Path) -> None:
    db = _db(tmp_path)
    # 1 M gravado no total, 400 mil deles por 1 h; uma linha legada com a coluna NULA.
    db.execute("INSERT INTO ai_calls(ts, role, model, provider, input_tokens, cache_write, cache_write_1h, origem)"
               " VALUES ('2026-10-04T09:00:00Z','plan','m-a','anthropic',0,1000000,400000,'execucao')")
    db.execute("INSERT INTO ai_calls(ts, role, model, provider, input_tokens, cache_write, origem)"
               " VALUES ('2026-10-04T09:00:01Z','decide','m-a','anthropic',0,1000000,'execucao')")
    esperado = 2.5 + 0.4 * 1.5 + 2.5                       # 5 min de tudo + a diferença sobre os 400 mil + o legado
    assert costs.spent_usd(db, PRECOS, since="2026-10-04T00:00:00Z") == pytest.approx(esperado)
    assert costs.usd_por(db, PRECOS, "role", "1=1", ()) == {"plan": (1, pytest.approx(3.1)),
                                                            "decide": (1, pytest.approx(2.5))}


async def test_provedor_le_a_parte_de_1h_e_o_repositorio_grava(tmp_path: Path, harness: Harness) -> None:
    plano = json.dumps({"summary": "nada", "app_id": "qa-messenger", "parameters": [], "success_criteria": [],
                        "missing": [], "steps": []})
    resp = SimpleNamespace(content=[SimpleNamespace(type="text", text=plano)], stop_reason="end_turn",
                           model="claude-sonnet-5-5",
                           usage=SimpleNamespace(input_tokens=1800, output_tokens=200, cache_read_input_tokens=0,
                                                 cache_creation_input_tokens=6700,
                                                 cache_creation=SimpleNamespace(ephemeral_5m_input_tokens=0,
                                                                                ephemeral_1h_input_tokens=6700)))
    p, _fake = provider(tmp_path, [resp])
    from app.planning.provider import PlanRequest
    _plano, uso = await p.plan(PlanRequest(command="abra o app", run_id="r1", apps=[APP], instances=[
        {"instance_id": "android-01", "account_label": "qa-user-01", "app_id": "qa-messenger"}]))
    assert (uso.cache_write_tokens, uso.cache_write_1h_tokens) == (6700, 6700)

    repo = harness.state.repo                                                            # type: ignore[union-attr]
    repo.add_usage(None, None, uso)
    repo.add_usage(None, None, Usage(calls=1, role="decide", model="m", provider="local", cache_write_tokens=10))
    linhas = [(r["role"], r["cache_write"], r["cache_write_1h"]) for r in repo.db.query("SELECT role, cache_write, cache_write_1h FROM ai_calls ORDER BY id")]
    assert linhas == [("plan", 6700, 6700), ("decide", 10, None)]   # sem 1 h: NULO, como no legado


async def test_api_usage_cobra_a_parte_de_1h(harness: Harness) -> None:
    db = harness.state.db                                                                # type: ignore[union-attr]
    harness.cfg.file.ai.prices = dict(PRECOS)
    db.execute("INSERT INTO ai_calls(ts, role, model, provider, input_tokens, cache_write, cache_write_1h, ok)"
               " VALUES (?, 'plan','m-a','anthropic',0,1000000,1000000,1)", (_agora(),))
    rel = await _usage(harness)
    plano = next(g for g in rel["groups"] if g["role"] == "plan" and g["model"] == "m-a")
    assert plano["usd"] == pytest.approx(4.0)


def _agora() -> str:
    from app.util import now_iso
    return now_iso()
