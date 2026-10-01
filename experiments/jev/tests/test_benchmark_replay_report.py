"""Benchmark ponta a ponta sem rede, baselines, Piloto B (replay) e relatório."""
from __future__ import annotations

import json
import socket
import sqlite3
from pathlib import Path

import pytest

import baselines as bl
import benchmark as bm
import replay as rp
import report as rpt
from corpus import Chunk, chunk_file, repo_root
from fake_provider import FakeJevProvider, FakeMode
from netguard import NetworkBlocked, no_network


# --------------------------------------------------------------------------- baselines em repositório sintético
@pytest.fixture()
def tiny(tmp_path: Path):
    app = tmp_path / "backend" / "app"
    tests = tmp_path / "backend" / "tests"
    app.mkdir(parents=True)
    tests.mkdir(parents=True)
    (app / "a.py").write_text("def pedir_reinicio():\n    return 'restart'\n\ndef outra():\n    pass\n", encoding="utf-8")
    (app / "b.py").write_text("def manutencao(worker):\n    # worker em manutenção recusa comando\n    return worker\n", encoding="utf-8")
    (tests / "test_a.py").write_text("from a import pedir_reinicio\n\ndef test_x():\n    pedir_reinicio()\n", encoding="utf-8")
    return tmp_path


def test_ripgrep_python_engine_matches_and_ranks(tiny, monkeypatch):
    monkeypatch.setattr(bl, "rg_command", lambda: None)
    r = bl.ripgrep(tiny, ["backend/app", "backend/tests"], "Onde `pedir_reinicio` é chamado?")
    assert r.notes["engine"] == "python"
    assert r.ranked_files()[0] in ("backend/app/a.py", "backend/tests/test_a.py")
    assert set(r.ranked_files()) == {"backend/app/a.py", "backend/tests/test_a.py"}


def test_ripgrep_scope_excludes_tests_when_asked(tiny, monkeypatch):
    monkeypatch.setattr(bl, "rg_command", lambda: None)
    r = bl.ripgrep(tiny, ["backend/app", "backend/tests"], "Em qual código de produção (fora dos testes) está `pedir_reinicio`?")
    assert r.ranked_files() == ["backend/app/a.py"]


@pytest.mark.skipif(not bl.rg_available(), reason="ripgrep indisponível")
def test_real_ripgrep_agrees_with_python_engine(tiny, monkeypatch):
    q = "Onde `pedir_reinicio` é chamado?"
    real = bl.ripgrep(tiny, ["backend/app", "backend/tests"], q)
    monkeypatch.setattr(bl, "rg_command", lambda: None)
    py = bl.ripgrep(tiny, ["backend/app", "backend/tests"], q)
    assert real.notes["engine"] == "ripgrep" and set(real.ranked_files()) == set(py.ranked_files())


def test_bm25_finds_semantic_match_and_respects_prefix(tiny):
    chunks = []
    for rel in ("backend/app/a.py", "backend/app/b.py", "backend/tests/test_a.py"):
        chunks += chunk_file(rel, (tiny / rel).read_text(encoding="utf-8"))
    idx = bl.Bm25Index(chunks)
    r = bl.code_search(idx, "worker em manutencao recusa comando", roots=["backend/app", "backend/tests"])
    assert r.spans[0].file == "backend/app/b.py"
    only_tests = bl.code_search(idx, "pedir reinicio", roots=["backend/app", "backend/tests"])
    assert only_tests.spans
    scoped = idx.search("Quais testes cobrem pedir_reinicio?", 5, ["backend/tests"])
    assert all(idx.chunks[i].file.startswith("backend/tests/") for i, _ in scoped)


def test_deliver_caps_spans_and_bytes():
    spans = [bl.Span("f.py", i, i, "x" * 6000) for i in range(10)]
    got = bl.deliver(spans)
    assert len(got) == 2                           # 16 KiB / 6000 B
    assert len(bl.deliver(spans, max_spans=1, max_bytes=10**9)) == 1


# --------------------------------------------------------------------------- benchmark ponta a ponta
def _args(**kw):
    import argparse
    base = dict(provider="none", fake_mode="HIGH_CONFIDENCE", golden=str(Path(bm.HERE) / "golden.json"),
                estimate_only=False, confirm_external_send=False, max_calls=60, out=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_default_benchmark_opens_no_connection(monkeypatch, tmp_path):
    """O comando padrão (baselines) não pode abrir NENHUMA conexão — guarda extra além do no_network interno."""
    def deny(*a, **k):
        raise AssertionError("conexão aberta pelo benchmark padrão")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    assert bm.main(["--out", str(tmp_path / "o")]) == 0
    res = json.loads((tmp_path / "o" / "results.json").read_text(encoding="utf-8"))
    assert res["meta"]["provider"] == "none"
    assert {r["strategy"] for r in res["rows"]} == {"ripgrep", "code_search"}
    assert len(res["rows"]) == 36 and all(r["jev_requests"] == 0 for r in res["rows"])
    verdict = json.loads((tmp_path / "o" / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["verdict"] == "NOT_EVALUATED"


def test_fake_benchmark_runs_pipeline_and_never_gives_a_verdict(tmp_path):
    assert bm.main(["--provider", "fake", "--out", str(tmp_path / "o")]) == 0
    res = json.loads((tmp_path / "o" / "results.json").read_text(encoding="utf-8"))
    jev = [r for r in res["rows"] if r["strategy"] == "jev_rerank"]
    assert len(jev) == 18 and sum(r["jev_requests"] for r in jev) == 18
    assert json.loads((tmp_path / "o" / "verdict.json").read_text(encoding="utf-8"))["verdict"] == "NOT_EVALUATED"


@pytest.mark.parametrize("mode,failure", [("TIMEOUT", "timeout"), ("ERROR", "error"),
                                          ("INVALID_RESPONSE", "invalid_response"), ("UNKNOWN_CHOICE", "unknown_choice")])
def test_pipeline_survives_provider_failures_with_fallback(mode, failure, tmp_path):
    assert bm.main(["--provider", "fake", "--fake-mode", mode, "--out", str(tmp_path / "o")]) == 0
    res = json.loads((tmp_path / "o" / "results.json").read_text(encoding="utf-8"))
    jev = [r for r in res["rows"] if r["strategy"] == "jev_rerank"]
    cs = {r["id"]: r for r in res["rows"] if r["strategy"] == "code_search"}
    assert all(r["failure"] == failure and r["fallback"] for r in jev)
    assert all(r["ranked_files_top10"] == cs[r["id"]]["ranked_files_top10"] for r in jev)   # cai no BM25
    assert res["summary"]["jev_rerank"]["all"]["FAILURES"] == 18


def test_jev_provider_requires_explicit_flag_and_key(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(SystemExit) as e1:
        bm.main(["--provider", "jev", "--out", str(tmp_path / "o")])
    assert "--confirm-external-send" in str(e1.value)
    with pytest.raises(SystemExit) as e2:
        bm.main(["--provider", "jev", "--confirm-external-send", "--out", str(tmp_path / "o")])
    assert "TYPESAFE_API_KEY" in str(e2.value)
    assert not (tmp_path / "o").exists()


def test_estimate_only_is_offline_and_labels_proxy(tmp_path):
    assert bm.main(["--estimate-only", "--out", str(tmp_path / "o")]) == 0
    res = json.loads((tmp_path / "o" / "results.json").read_text(encoding="utf-8"))
    e = res["estimate"]
    assert e["ESTIMATED_JEV_CALLS"] == 18 and e["ESTIMATED_INPUT_BYTES"] > 0
    assert "UNKNOWN" in e["ESTIMATED_INPUT_TOKENS"] and "PROXY" in e["ESTIMATED_INPUT_TOKENS"]
    assert e["max_payload_bytes"] <= 110_000


def test_network_guard_blocks_connect_and_restores():
    orig = socket.socket.connect
    with no_network():
        with pytest.raises(NetworkBlocked):
            socket.create_connection(("example.com", 80))
        with pytest.raises(NetworkBlocked):
            socket.getaddrinfo("example.com", 80)
    assert socket.socket.connect is orig


# --------------------------------------------------------------------------- Piloto B
def _mkdb(path: Path):
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE steps (id TEXT, side_effect INT, capability TEXT, driven_by TEXT, status TEXT, attempts INT,
      strategy TEXT, failure_kind TEXT, app_id TEXT, started_at TEXT, finished_at TEXT,
      goal TEXT, title TEXT, status_detail TEXT, result TEXT, bindings TEXT, variables TEXT);
    CREATE TABLE ai_calls (id INTEGER PRIMARY KEY, step_id TEXT, role TEXT, tier INT, model TEXT, provider TEXT,
      ok INT, ms INT, input_tokens INT, output_tokens INT, with_image INT, usd REAL, error_kind TEXT,
      error_message TEXT);
    """)
    secret = "SEGREDO-MENSAGEM-REAL-JOAO"
    rows = [  # id, side_effect, cap, driven_by, status, attempts
        ("s1", 0, None, "recipe", "succeeded", 1), ("s2", 0, None, "ai", "succeeded", 1),
        ("s3", 1, "SEND_MESSAGE", "ai", "succeeded", 2), ("s4", 1, "FOLLOW", "ai", "succeeded", 1),
        ("s5", 1, "SEND_MESSAGE", "ai", "waiting_user", 1), ("s6", 0, None, "ai", "failed", 3),
        ("s7", 0, None, "ai", "cancelled", 1)]
    for r in rows:
        con.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,NULL,NULL,'com.x','t0','t1',?,?,?,?,?,?)",
                    (*r, secret, secret, secret, secret, secret, secret))
    for sid, tier in (("s2", 0), ("s3", 1), ("s4", 1)):
        con.execute("INSERT INTO ai_calls(step_id,role,tier,model,provider,ok,ms,input_tokens,output_tokens,with_image,usd,"
                    "error_kind,error_message) VALUES (?,?,?,?,?,1,100,10,2,0,0.001,NULL,?)",
                    (sid, "decide", tier, "m", "anthropic", secret))
    con.commit()
    con.close()
    return secret


def test_replay_extract_labels_and_never_reads_free_text(tmp_path):
    db = tmp_path / "t.sqlite3"
    secret = _mkdb(db)
    cases = rp.extract(db, {"SEND_MESSAGE": "high", "FOLLOW": "medium"})
    blob = json.dumps(cases, ensure_ascii=False)
    assert secret not in blob and "s1" not in [c["case"] for c in cases]       # sem texto livre, ids pseudonimizados
    assert len(cases) == 6                                                       # 'cancelled' fora do conjunto final
    labels = {c["case"]: rp.derive_label(c) for c in cases}
    by = {rp.pseudonym(k): v for k, v in
          {"s1": "DETERMINISTIC", "s2": "CHEAP_AI", "s3": "STRONG_AI", "s4": "STRONG_AI_UNPROVEN",
           "s5": "HUMAN", "s6": "UNLABELED"}.items()}
    assert labels == by


def test_safe_select_rejects_forbidden_columns():
    for bad in ("args", "rationale", "error", "title", "goal", "result", "status_detail", "failure_screen",
                "bindings", "variables", "observed_result", "saidas"):
        with pytest.raises(ValueError):
            rp.safe_select("steps", [bad])
    for bad in ("error_message", "prompt", "response"):
        with pytest.raises(ValueError):
            rp.safe_select("ai_calls", [bad])
    assert "SELECT" in rp.safe_select("steps", ["id", "status"])


def test_replay_source_has_a_single_sql_builder():
    src = Path(rp.__file__).read_text(encoding="utf-8")
    assert sum(1 for ln in src.splitlines() if "SELECT" in ln and 'f"SELECT' in ln) == 1   # só `safe_select` monta SQL
    assert "SELECT *" not in src


def test_replay_evaluate_flags_unsafe_under_escalation(tmp_path):
    db = tmp_path / "t.sqlite3"
    _mkdb(db)
    cases = rp.extract(db, {"SEND_MESSAGE": "high", "FOLLOW": "medium"})
    ev = rp.evaluate(cases, rp.always_cheap)
    assert ev["n_pool"] == 3                                  # s2 CHEAP, s3 STRONG, s5 HUMAN
    assert ev["unsafe_under_escalation_rate"] == pytest.approx(2 / 3)
    assert rp.evaluate(cases, lambda c: ("STRONG_AI", 1.0))["unsafe_under_escalation_rate"] == 0.0


def test_jev_router_fails_safe_to_strong_and_escalates_on_low_confidence(tmp_path):
    case = {"side_effect": True, "risk": "high", "capability": "FOLLOW", "driven_by": "ai", "app_id": "x"}
    assert rp.JevRouter(FakeJevProvider(FakeMode.TIMEOUT))(case) == ("STRONG_AI", 0.0)
    r = rp.JevRouter(FakeJevProvider(FakeMode.LOW_CONFIDENCE))
    route, conf = r(case)
    assert conf < 0.5 and route in ("STRONG_AI", "HUMAN")           # nunca desce de degrau com pouca confiança
    assert rp.JevRouter(FakeJevProvider(FakeMode.UNKNOWN_CHOICE))(case) == ("STRONG_AI", 0.0)


def test_risk_map_reads_catalogs():
    rm = rp.load_risk_map(repo_root())
    assert rm.get("SEND_MESSAGE") in ("high", "medium") and len(rm) >= 5


# --------------------------------------------------------------------------- relatório / limiares
def _fake_jev_result(good: bool):
    def agg(**kw):
        base = {"n": 18, "RECALL_AT_3": 0.5, "CONTEXT_BYTES_RETURNED_MEDIAN": 10_000, "NAIVE_READ_BYTES_MEDIAN": 100_000,
                "LATENCY_MS_P50": 400, "LATENCY_MS_P95": 900, "FAILURES": 0, "JEV_REQUESTS": 18, "JEV_COST_USD_PROXY": 0.01}
        base.update(kw)
        return base
    jev_sem = 0.8 if good else 0.55
    s = {"jev_rerank": {"all": agg(), "grep_friendly": agg(RECALL_AT_3=0.7), "semantic_only": agg(RECALL_AT_3=jev_sem)},
         "ripgrep": {"all": agg(), "grep_friendly": agg(RECALL_AT_3=0.7), "semantic_only": agg(RECALL_AT_3=0.5)},
         "code_search": {"all": agg(CONTEXT_BYTES_RETURNED_MEDIAN=13_000), "grep_friendly": agg(RECALL_AT_3=0.6),
                         "semantic_only": agg(RECALL_AT_3=0.6)}}
    rows = [{"strategy": "jev_rerank", "id": "G1", "r3": 1.0}, {"strategy": "code_search", "id": "G1", "r3": 1.0}]
    return {"meta": {"provider": "jev", "redactions": {}}, "summary": s, "rows": rows}


def test_evaluate_go_and_partial_and_no_go():
    assert rpt.evaluate(_fake_jev_result(True))["verdict"] == "GO"
    r = _fake_jev_result(False)                                   # T2 falha, T3 passa ⇒ PARTIAL_GO
    assert rpt.evaluate(r)["verdict"] == "PARTIAL_GO"
    r["summary"]["jev_rerank"]["all"]["CONTEXT_BYTES_RETURNED_MEDIAN"] = 90_000      # T2 e T3 falham ⇒ NO_GO
    assert rpt.evaluate(r)["verdict"] == "NO_GO"
    r2 = _fake_jev_result(True)
    r2["summary"]["jev_rerank"]["all"]["JEV_REQUESTS"] = 3
    assert rpt.evaluate(r2)["verdict"] == "INSUFFICIENT_EVIDENCE"
    r3 = _fake_jev_result(True)
    r3["meta"]["redactions"] = {"api_key": 1}
    assert rpt.evaluate(r3)["verdict"] == "NO_GO"


def test_evaluate_refuses_to_judge_fake_runs():
    r = _fake_jev_result(True)
    r["meta"]["provider"] = "fake"
    assert rpt.evaluate(r)["verdict"] == "NOT_EVALUATED"


def test_thresholds_file_declares_no_post_hoc_change():
    th = json.loads(Path(rpt.THRESHOLDS).read_text(encoding="utf-8"))
    assert th["registered_before_any_real_jev_result"] is True and th["THRESHOLD_CHANGED_AFTER_RESULTS"] == "NO"
    assert {"T1_exact_no_regression", "T2_semantic_gain", "T3_context_reduction", "T4_latency", "T5_reliability",
            "T6_cost", "T7_privacy_gate", "T8_critical_misses"} <= set(th["pilot_a_claude_code_retrieval"])


def test_report_serialization_roundtrip(tmp_path):
    res = _fake_jev_result(True)
    res["meta"].update(golden_version=1, source_sha="3eba639", corpus_files=1, corpus_chunks=1, corpus_bytes=1,
                       deliver_max_spans=8, deliver_max_bytes=16384, rg_context_lines=5)
    v = rpt.write_outputs(tmp_path, res)
    assert v["verdict"] == "GO"
    assert json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))["meta"]["provider"] == "jev"
    assert "PROXY_METRIC" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_adaptive_k_rule():
    assert bm.adaptive_k([0.9, 0.05, 0.05]) == 2                 # piso kmin
    assert bm.adaptive_k([0.3, 0.3, 0.3, 0.05, 0.05]) == 3       # 0.9 acumulado em 3
    assert bm.adaptive_k([0.1] * 10) == bm.ADAPTIVE_KMAX         # nunca passa do teto
    assert bm.adaptive_k([]) == bm.ADAPTIVE_KMAX
