"""Smoke sintético: plano, estimativa offline, travas, simulação da rodada com transporte falso e prova de que o corpus
sintético não copia o repositório privado."""
from __future__ import annotations

import ast
import json
import re
import socket
from pathlib import Path

import pytest

import provider as pv
import smoke as sm
from corpus import list_files, read_text, repo_root
from fake_provider import FakeJevProvider
from redact import redact

SYN = Path(sm.SYNTHETIC)


def test_plan_has_six_calls_and_covers_the_protocol():
    plan = sm.build_plan()
    assert len(plan) == 6 == sm.MAX_CALLS
    types = {q["type"] for p in plan for q in p.questions.values() if "type" in q}
    assert {"noul", "choice", "score"} <= types
    assert [p.expect for p in plan].count("http_422") == 1 and [p.expect for p in plan].count("timeout_or_ok") == 1


def test_estimate_is_offline_small_and_labelled_proxy(monkeypatch):
    def deny(*a, **k):
        raise AssertionError("rede aberta pela estimativa")
    monkeypatch.setattr(socket.socket, "connect", deny)
    est = sm.estimate(sm.build_plan())
    assert est["ESTIMATED_CALLS"] == 6 and 0 < est["ESTIMATED_INPUT_BYTES"] < 20_000
    assert "UNKNOWN" in est["ESTIMATED_TOKENS"] and "PROXY" in est["ESTIMATED_TOKENS"]
    assert "US$" in est["ESTIMATED_COST"] and "retry" in est["not_validated_by_real_calls"][0]


def test_cli_default_is_estimate_only_without_connection(monkeypatch, tmp_path):
    def deny(*a, **k):
        raise AssertionError("rede")
    monkeypatch.setattr(socket.socket, "connect", deny)
    assert sm.main(["--out", str(tmp_path)]) == 0
    assert list(tmp_path.glob("*-estimate.json"))


def test_real_run_is_locked_in_three_ways(monkeypatch, tmp_path):
    monkeypatch.setenv(pv.KEY_ENV, "k" * 24)
    with pytest.raises(SystemExit) as e1:
        sm.main(["--run", "--out", str(tmp_path)])
    assert "--confirm-synthetic-only" in str(e1.value)
    assert sm.SMOKE_RUN_AUTHORIZED is False
    with pytest.raises(SystemExit) as e2:
        sm.main(["--run", "--confirm-synthetic-only", "--out", str(tmp_path)])
    assert "BLOCKED_AUTHORIZATION" in str(e2.value)
    monkeypatch.setattr(sm, "SMOKE_RUN_AUTHORIZED", True)
    monkeypatch.delenv(pv.KEY_ENV)
    with pytest.raises(SystemExit) as e3:
        sm.main(["--run", "--confirm-synthetic-only", "--out", str(tmp_path)])
    assert pv.KEY_ENV in str(e3.value)
    assert not list(tmp_path.glob("*.json"))


def _wire(state, questions):
    """Servidor falso fiel ao formato oficial para os 3 tipos."""
    fake = FakeJevProvider("HIGH_CONFIDENCE")
    answers = {}
    for qid, q in questions.items():
        if q["type"] == "score":
            n = len(q["criteria"])
            if n < 2:
                return 422, b'{"detail":"score needs at least two levels"}'
            probs = {str(i): (0.9 if i == 1 else 0.1 / (n - 1)) for i in range(n)}
            answers[qid] = {"type": "score", "score": 1.0, "legend": {str(i): str(c) for i, c in enumerate(q["criteria"])},
                            "probabilities": probs, "confidence": 0.8}
        else:
            answers[qid] = fake.evaluate(state, {qid: q})["answers"][qid]
    return 200, json.dumps({"model": "jev-1.13.0", "answers": answers,
                            "usage": {"input_tokens": 300, "output_tokens": 20}}).encode()


def test_simulated_real_run_with_fake_transport_follows_the_plan():
    seen = []

    def transport(url, headers, body, timeout):
        seen.append((url, timeout, headers["Authorization"][:7]))
        b = json.loads(body)
        if timeout == sm.TIMEOUT_PROBE_S:
            raise pv.ProviderTimeout("simulado")
        return _wire(b["state"], b["questions"])
    p = pv.RealJevProvider(enabled=True, max_calls=6, max_retries=0, env={pv.KEY_ENV: "super-secret-key-123456"},
                           transport=transport)
    rep = sm.run_real(sm.build_plan(), p)
    outcomes = {r["name"]: r["outcome"] for r in rep["results"]}
    assert outcomes == {"noul_auth_latency": "ok", "choice_closed_set": "ok", "score_three_levels": "ok",
                        "rerank_shape": "ok", "error_422": "error", "timeout_probe": "timeout"}
    assert rep["calls_used"] == 6 and len(seen) == 6 and all(s[0] == pv.API_URL for s in seen)
    assert all(r.get("validated") for r in rep["results"] if r["outcome"] == "ok")
    err = next(r for r in rep["results"] if r["name"] == "error_422")
    assert "two levels" in err["error_body"] and "retry" not in err["error"]
    assert "super-secret-key-123456" not in json.dumps(rep)
    assert rep["total_real_cost_usd"] == pytest.approx(4 * 300 * 0.042 / 1e6)


def test_run_never_exceeds_six_network_attempts_even_with_retries():
    n = {"c": 0}

    def transport(*a):
        n["c"] += 1
        return 429, b"{}"
    p = pv.RealJevProvider(enabled=True, max_calls=6, max_retries=2, env={pv.KEY_ENV: "k" * 20}, transport=transport,
                           sleep=lambda s: None)
    sm.run_real(sm.build_plan(), p)
    assert n["c"] == 6


# --------------------------------------------------------------------------- o corpus sintético NÃO vem do repositório
def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\sA-Za-z0-9_]", text.lower())


_BOILERPLATE = {"self", "def", "return", "none", "str", "int", "float", "bool", "if", "else", "elif", "not", "in", "is",
                "for", "import", "from", "class", "the", "and", "or", "pass", "raise", "true", "false", "dict", "list"}


def _informative(window: list[str]) -> bool:
    """Janela com >= 5 palavras distintas que não são boilerplate do Python: ignora `def check(self) -> str:` etc."""
    words = {w for w in window if re.fullmatch(r"[a-z_][a-z0-9_]*", w) and w not in _BOILERPLATE}
    return len(words) >= 5


def test_synthetic_corpus_is_clean_and_self_contained():
    files = sorted(SYN.rglob("*.py"))
    assert len(files) >= 5
    for f in files:
        r = redact(f.read_text(encoding="utf-8"))
        assert not r.hard and not r.soft, f
    for p in sm.build_plan():
        blob = json.dumps([p.state, p.questions])
        assert "backend/" not in blob and "C:\\" not in blob and "/Users/" not in blob


def test_synthetic_corpus_shares_no_private_identifier_or_sequence():
    root = repo_root()
    repo_files = list_files(root, ["backend/app", "backend/tests"], [".py"])
    repo_idents: set[str] = set()
    repo_ngrams: set[int] = set()
    N = 12
    for rel in repo_files:
        text = read_text(root, rel)
        try:
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    repo_idents.add(node.name.lower())
        except SyntaxError:
            pass
        toks = _tokens(text)
        for i in range(len(toks) - N + 1):
            if _informative(toks[i:i + N]):
                repo_ngrams.add(hash(tuple(toks[i:i + N])))
    generic = {"__init__", "add", "check", "record", "resolve", "main", "run", "get", "set", "delay_for"}
    for f in sorted(SYN.rglob("*.py")):
        text = f.read_text(encoding="utf-8")
        own = {n.name.lower() for n in ast.walk(ast.parse(text))
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        shared = (own & repo_idents) - generic
        assert not shared, f"{f.name}: nomes iguais aos do repositório privado: {sorted(shared)}"
        toks = _tokens(text)
        dup = [i for i in range(len(toks) - N + 1)
               if _informative(toks[i:i + N]) and hash(tuple(toks[i:i + N])) in repo_ngrams]
        assert not dup, f"{f.name}: {len(dup)} sequências informativas de {N} tokens idênticas às do repositório privado"
