"""`app/desempenho.py::resumo` sobre o banco do harness com linhas sintéticas de horário controlado.

A janela fica em 01/01/2026, longe de "agora": o harness sobe o scheduler e o monitor de verdade, e o que eles
gravarem durante o teste (com horário atual) não pode entrar na conta. Os números esperados foram feitos à mão —
se a regra de percentil, de coorte ou de custo mudar, o teste diz qual.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.desempenho import resumo

from .conftest import Harness

DESDE, ATE = "2026-01-01T00:00:00.000Z", "2026-01-01T10:00:00.000Z"
PRECOS = {"claude-sonnet-5": [2.0, 0.2, 2.5, 10.0], "claude-opus-5": [5.0, 0.5, 6.25, 25.0],
          "claude-haiku-4-5": [1.0, 0.1, 1.25, 5.0]}


def T(h: int, m: int = 0, s: int = 0, ms: int = 0, dia: str = "2026-01-01") -> str:
    return f"{dia}T{h:02d}:{m:02d}:{s:02d}.{ms:03d}Z"


def _run(db: Any, rid: str, criado: str, inicio: str | None, *, simulado: int = 0) -> None:
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
               " started_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (rid, f"k-{rid}", "comando sintético", "execute", "completed", simulado, "[]", criado, inicio))


def _obj(db: Any, rid: str, inst: str, status: str, ini: str | None, fim: str | None, *, paused: int = 0,
         espera: str | None = None) -> None:
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, started_at, finished_at, paused_s, wait_reason)"
               " VALUES (?,?,?,?,?,?,?,?)", (f"{rid}:{inst}", rid, inst, status, ini, fim, paused, espera))


def _etapa(db: Any, rid: str, inst: str, key: str, status: str, ini: str | None, fim: str | None,
           driven: str | None, tentativas: int = 1) -> str:
    sid = f"{rid}:{inst}:v1:{key}"
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " postcondition, timeout_s, max_attempts, attempts, status, started_at, finished_at, driven_by)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (sid, rid, f"{rid}:{inst}", inst, 1, 1, key, key, key, "{}", 60, 3, tentativas, status, ini, fim, driven))
    return sid


def _ia(db: Any, ts: str, run: str | None, role: str, model: str, ms: int, *, provider: str | None = "anthropic",
        novo: int = 0, lido: int = 0, gravado: int = 0, saida: int = 0, ok: int = 1, erro: str | None = None,
        fallback: str | None = None, pedido: str | None = None) -> None:
    db.execute("INSERT INTO ai_calls(ts, run_id, role, model, input_tokens, cache_read, cache_write, output_tokens,"
               " ms, ok, error_kind, fallback, requested_model, provider) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (ts, run, role, model, novo, lido, gravado, saida, ms, ok, erro, fallback, pedido or model, provider))


def _cmd(db: Any, cid: str, verbo: str, estado: str, criado: str, despacho: str | None, ack: str | None,
         fim: str | None) -> None:
    db.execute("INSERT INTO commands(id, instance_id, verb, idempotency_key, state, requested_by, created_at,"
               " dispatched_at, acked_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (cid, "android-01", verbo, f"k-{cid}", estado, "system", criado, despacho, ack, fim))


def _medida(db: Any, ts: str, kind: str, data: dict[str, Any]) -> None:
    db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)", (ts, kind, json.dumps(data)))


@pytest.fixture
def db(harness: Harness) -> Any:
    d = harness.state.db  # type: ignore[union-attr]
    # Execuções: duas reais na janela, uma simulada na janela, uma real ANTES da janela (fora da coorte).
    _run(d, "r1", T(1), T(1, 0, 10))
    _run(d, "r2", T(2), T(2, 0, 30))
    _run(d, "rs", T(3), T(3, 0, 5), simulado=1)
    _run(d, "rold", T(23, dia="2025-12-31"), T(23, 0, 1, dia="2025-12-31"))
    # Objetivos (fila = início do objetivo − início da execução).
    _obj(d, "r1", "a1", "succeeded", T(1, 0, 20), T(1, 1, 20))        # fila 10 · 60 s
    _obj(d, "r1", "a2", "failed", T(1, 0, 40), T(1, 2, 40))           # fila 30 · 120 s
    _obj(d, "r2", "a1", "succeeded", T(2, 0, 30), T(2, 0, 50))        # fila 0 · 20 s
    _obj(d, "r2", "a2", "uncertain", T(2, 1, 0), T(2, 1, 30))         # fila 30 · 30 s
    _obj(d, "r2", "a3", "waiting_user", T(2, 0, 40), T(2, 5, 40), paused=100)   # fila 10 · 300 s
    _obj(d, "r2", "a4", "cancelled", None, T(2, 0, 31))               # nunca começou: sem fila, sem duração
    _obj(d, "r2", "a5", "pending", None, None, espera="device_slot")
    _obj(d, "rs", "a1", "succeeded", T(3, 0, 10), T(3, 0, 20))        # simulado
    _obj(d, "rold", "a1", "succeeded", T(23, 0, 5, dia="2025-12-31"), T(23, 1, dia="2025-12-31"))
    # Etapas e ações.
    s1 = _etapa(d, "r1", "a1", "abrir", "succeeded", T(1, 0, 20), T(1, 0, 30), None)          # ai, 10 s
    _etapa(d, "r1", "a1", "compor", "succeeded", T(1, 0, 30), T(1, 0, 32), "recipe")          # 2 s
    _etapa(d, "r1", "a2", "abrir", "failed", T(1, 0, 40), T(1, 2, 40), "ai", tentativas=3)
    _etapa(d, "r2", "a1", "abrir", "succeeded", T(2, 0, 30), T(2, 0, 34), "recipe")           # 4 s
    _etapa(d, "rs", "a1", "abrir", "succeeded", T(3, 0, 10), T(3, 0, 20), "ai")               # simulada
    d.execute("INSERT INTO attempts(id, step_id, number, status, started_at) VALUES (?,?,?,?,?)",
              (f"{s1}:a1", s1, 1, "succeeded", T(1, 0, 20)))
    for seq, (tool, st, ini, fim) in enumerate([("tap", "done", T(1, 0, 21), T(1, 0, 21, 500)),
                                                ("tap", "done", T(1, 0, 22), T(1, 0, 23)),
                                                ("type_text", "failed", T(1, 0, 24), T(1, 0, 24, 200))], start=1):
        d.execute("INSERT INTO actions(attempt_id, seq, tool, args, status, intent_at, done_at) VALUES (?,?,?,?,?,?,?)",
                  (f"{s1}:a1", seq, tool, "{}", st, ini, fim))
    # IA. r1: plano, 3 decisões, 1 verificação com erro, 1 decisão servida por fallback de recusa.
    _ia(d, T(1, 0, 5), "r1", "plan", "claude-sonnet-5", 3000, novo=1000, gravado=2000, saida=500)
    for ms in (1000, 2000, 4000):
        _ia(d, T(1, 0, 25), "r1", "decide", "claude-sonnet-5", ms, novo=100, lido=5000, saida=50)
    _ia(d, T(1, 0, 26), "r1", "verify", "claude-sonnet-5", 30000, ok=0, erro="timeout")
    _ia(d, T(1, 0, 27), "r1", "decide", "claude-opus-5", 5000, novo=200, saida=100, fallback="refusal",
        pedido="claude-sonnet-5")
    _ia(d, T(2, 0, 35), "r2", "decide", "modelo-sem-preco", 700, provider="local", novo=10)   # tarifa mais cara
    _ia(d, T(3, 0, 12), "rs", "decide", "simulado", 5, provider=None)                         # execução simulada
    _ia(d, T(4), None, "social", "claude-haiku-4-5", 1500, novo=1000, saida=100)              # fora de execução
    _ia(d, T(4, 0, 1), None, "decide", "simulado", 5, provider="simulated")                   # provedor simulado
    _ia(d, T(23, dia="2025-12-31"), "rold", "decide", "claude-sonnet-5", 99999, novo=10**6)   # fora da janela
    # Comandos.
    _cmd(d, "c1", "start", "succeeded", T(1), T(1, 0, 1), T(1, 0, 1, 500), T(1, 1))           # 60 s · ack 0,5 s
    _cmd(d, "c2", "start", "failed", T(2), T(2, 0, 2), T(2, 0, 3), T(2, 0, 30))               # 30 s · ack 1 s
    _cmd(d, "c3", "hibernate", "uncertain", T(3), T(3, 0, 1), None, T(3, 2))                  # sem ack
    _cmd(d, "c4", "stop", "succeeded", T(4, 0, 10), None, None, T(4))                         # relógio torto: −10 s
    _cmd(d, "cold", "start", "succeeded", T(23, dia="2025-12-31"), None, None, T(23, 1, dia="2025-12-31"))
    # Medições de boot/hibernação (e um `clock`, que não entra).
    for s in (90, 110, 100):
        _medida(d, T(5), "boot", {"instance_id": "android-01", "boot_seconds": s, "kind": "cold"})
    _medida(d, T(5), "boot", {"instance_id": "android-01", "boot_seconds": 20, "kind": "warm"})
    _medida(d, T(5), "hibernate", {"instance_id": "android-01", "saved": True, "save_seconds": 15})
    _medida(d, T(5), "hibernate", {"instance_id": "android-01", "saved": False, "save_seconds": 40})
    _medida(d, T(5), "clock", {"skew_s": 3})
    _medida(d, T(23, dia="2025-12-31"), "boot", {"boot_seconds": 999, "kind": "cold"})
    return d


def test_objetivos_taxas_separadas_fila_e_custo_por_concluido(db: Any) -> None:
    r = resumo(db, desde_iso=DESDE, ate_iso=ATE, precos=PRECOS)
    o = r["objetivos"]
    assert r["janela"]["horas"] == 10 and r["execucoes_simuladas_excluidas"] == 1
    assert o["total"] == 7                                  # r1+r2; rs (simulada) e rold (fora) não entram
    assert o["por_status"] == {"succeeded": 2, "failed": 1, "uncertain": 1, "waiting_user": 1, "cancelled": 1,
                               "pending": 1, "running": 0}
    assert o["taxas"]["succeeded"] == round(2 / 7, 4) and o["taxas"]["failed"] == round(1 / 7, 4)
    assert o["concluidos_por_hora"] == 0.2                  # só `succeeded` conta como concluído corretamente
    # fila: [0, 10, 10, 30, 30] → p50 = 10, p95 = 30; o cancelado que nunca começou não entra
    assert o["fila_s"] == {"n": 5, "p50": 10.0, "p95": 30.0, "max": 30.0, "media": 16.0}
    assert o["duracao_s"]["failed"]["p50"] == 120.0 and o["duracao_s"]["waiting_user"]["p50"] == 300.0
    assert o["duracao_s"]["cancelled"] == {"n": 0, "p50": None, "p95": None, "max": None, "media": None}
    assert o["espera_atual"] == {"device_slot": 1}
    # custo da coorte = TODAS as chamadas de r1 e r2 (plano, decisões, erro, fallback, modelo sem preço)
    esperado = (1000 * 2 + 2000 * 2.5 + 500 * 10 + 3 * (100 * 2 + 5000 * 0.2 + 50 * 10)
                + 200 * 5 + 100 * 25 + 10 * 5) / 1e6
    assert o["usd_coorte"] == pytest.approx(esperado) and o["usd_estimado"] is True
    assert o["usd_por_concluido"] == pytest.approx(esperado / 2)
    assert o["execucoes"]["total"] == 2 and o["execucoes"]["criada_ate_inicio_s"]["p95"] == 30.0


def test_ia_por_papel_e_modelo_executado_fallback_e_erros(db: Any) -> None:
    ia = resumo(db, desde_iso=DESDE, ate_iso=ATE, precos=PRECOS)["ia"]
    assert ia["chamadas"] == 8 and ia["simuladas"] == 2 and ia["erros"] == 1
    assert ia["erros_por_tipo"] == {"timeout": 1}
    assert ia["tokens"] == {"novo": 2510, "cache_lido": 15000, "cache_gravado": 2000, "saida": 850}
    # decisões OK: [700, 1000, 2000, 4000, 5000] — a do fallback conta no papel, pelo modelo que respondeu
    assert ia["ms_por_papel"]["decide"]["p50"] == 2000.0 and ia["ms_por_papel"]["decide"]["p95"] == 5000.0
    grupos = {(g["papel"], g["modelo"]): g for g in ia["por_papel_modelo"]}
    sonnet = grupos[("decide", "claude-sonnet-5")]
    assert sonnet["chamadas"] == 3 and sonnet["ms"]["p50"] == 2000.0 and sonnet["ms"]["p95"] == 4000.0
    assert grupos[("decide", "claude-opus-5")]["fallback"] == 1
    assert grupos[("verify", "claude-sonnet-5")]["ms"]["n"] == 0            # só erro: sem latência de sucesso
    assert grupos[("verify", "claude-sonnet-5")]["ms_erros"]["p50"] == 30000.0
    assert grupos[("decide", "modelo-sem-preco")]["preco_estimado"] is True
    assert ia["fallback"] == {"chamadas": 1, "por_motivo": [{"motivo": "refusal", "pedido": "claude-sonnet-5",
                                                              "respondeu": "claude-opus-5", "chamadas": 1}]}
    assert ia["usd_fora_de_execucao"] == pytest.approx((1000 * 1 + 100 * 5) / 1e6)
    assert ("decide", "simulado") not in grupos                             # simulado fora do padrão


def test_etapas_acoes_comandos_e_boot(db: Any) -> None:
    r = resumo(db, desde_iso=DESDE, ate_iso=ATE, precos=PRECOS)
    et = r["etapas"]
    assert et["total"] == 4 and et["por_status"] == {"succeeded": 3, "failed": 1}
    assert et["duracao_s_por_driven_by"]["ai"]["p50"] == 10.0           # driven_by NULL conta como ai
    assert et["duracao_s_por_driven_by"]["recipe"]["n"] == 2
    tap = r["acoes"]["por_ferramenta"]["tap"]
    assert tap["n"] == 2 and tap["ms"]["max"] == 1000.0 and tap["por_origem"] == {"ai": 2}
    assert r["acoes"]["por_ferramenta"]["type_text"]["por_status"] == {"failed": 1}
    cmd = r["comandos"]["por_verbo"]
    assert r["comandos"]["total"] == 4
    assert cmd["start"]["por_estado"] == {"succeeded": 1, "failed": 1}
    assert cmd["start"]["despacho_ate_ack_s"]["n"] == 2 and cmd["start"]["despacho_ate_ack_s"]["max"] == 1.0
    assert cmd["hibernate"]["despacho_ate_ack_s"]["n"] == 0                # entregue e sem ack: desconhecido
    assert cmd["stop"]["criado_ate_fim_s"] == {"n": 0, "p50": None, "p95": None, "max": None, "media": None,
                                               "negativos": 1}                     # contado, não escondido
    ap = r["aparelhos"]
    assert ap["boot_s"]["cold"]["n"] == 3 and ap["boot_s"]["cold"]["p50"] == 100.0
    assert ap["boot_s"]["warm"]["p50"] == 20.0
    assert ap["hibernar_s"] == {"salvo": {"n": 1, "p50": 15.0, "p95": 15.0, "max": 15.0, "media": 15.0},
                                "nao_salvo": {"n": 1, "p50": 40.0, "p95": 40.0, "max": 40.0, "media": 40.0}}
    json.dumps(r)                                                          # vai inteiro para a rota


def test_incluir_simulados_conta_mas_nunca_cobra(db: Any) -> None:
    r = resumo(db, desde_iso=DESDE, ate_iso=ATE, precos=PRECOS, incluir_simulados=True)
    assert r["objetivos"]["total"] == 8 and r["objetivos"]["por_status"]["succeeded"] == 3
    assert r["execucoes_simuladas_excluidas"] == 0
    padrao = resumo(db, desde_iso=DESDE, ate_iso=ATE, precos=PRECOS)
    # o objetivo simulado não barateia o custo por concluído, e a chamada simulada não vira dólar
    assert r["objetivos"]["usd_por_concluido"] == padrao["objetivos"]["usd_por_concluido"]
    assert r["ia"]["chamadas"] == 10 and r["ia"]["usd_total"] == padrao["ia"]["usd_total"]
    assert r["etapas"]["total"] == 5


def test_janela_vazia_e_desconhecido_nao_zero(harness: Harness) -> None:
    db = harness.state.db  # type: ignore[union-attr]
    r = resumo(db, desde_iso="2020-01-01T00:00:00.000Z", ate_iso="2020-01-02T00:00:00.000Z", precos=PRECOS)
    o = r["objetivos"]
    assert o["total"] == 0 and o["taxas"]["succeeded"] is None
    assert o["fila_s"]["p50"] is None and o["usd_coorte"] is None and o["usd_por_concluido"] is None
    assert r["ia"]["chamadas"] == 0 and r["ia"]["usd_total"] is None
    assert r["comandos"]["por_verbo"] == {} and r["aparelhos"] == {"boot_s": {}, "hibernar_s": {}}


def test_execucao_real_sem_concluido_tem_custo_mas_nao_custo_por_concluido(harness: Harness) -> None:
    db = harness.state.db  # type: ignore[union-attr]
    _run(db, "rf", T(1), T(1, 0, 1))
    _obj(db, "rf", "a1", "failed", T(1, 0, 2), T(1, 0, 9))
    _ia(db, T(1, 0, 3), "rf", "decide", "claude-sonnet-5", 900, novo=1000)
    o = resumo(db, desde_iso=DESDE, ate_iso=ATE, precos=PRECOS)["objetivos"]
    assert o["usd_coorte"] == pytest.approx(0.002) and o["usd_por_concluido"] is None
    assert o["concluidos_por_hora"] == 0.0                   # zero medido (houve janela e nenhum sucesso)
