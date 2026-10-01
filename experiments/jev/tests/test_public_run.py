"""Instrumentação da rodada pública com transporte FALSO (sem rede, sem chave real): a rodada tem de gravar tudo o que é preciso
para avaliar correção (seleção do Jev, regiões, requisições, tokens), parar em 401, nunca passar do teto e nunca enviar um
payload fora do corpus público."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import benchmark as bm
import golden as gd
import provider as pv
import repomap
from corpus import repo_root

PUB = Path(bm.__file__).resolve().parent / "public_bench"
CHECKOUT = repo_root() / "data" / "jev-pilot" / "public" / "scrapy"
needs_checkout = pytest.mark.skipif(not (CHECKOUT / ".git").exists(), reason="checkout público ausente (rode public_bench/prepare.py)")


def _meta_items():
    return gd.load(PUB / "golden.json")


def _wire_factory(counter: dict, status: int = 200):
    """Servidor falso fiel ao formato de fio: responde como o provedor falso (HIGH_CONFIDENCE) a cada requisição."""
    from fake_provider import FakeJevProvider
    fake = FakeJevProvider("HIGH_CONFIDENCE")

    def transport(url, headers, body, timeout):
        counter["n"] = counter.get("n", 0) + 1
        if status != 200:
            return status, b'{"detail":"x"}'
        req = json.loads(body)
        raw = fake.evaluate(req["state"], req["questions"])
        return 200, json.dumps(raw).encode("utf-8")
    return transport


def _patch_real(monkeypatch, transport, max_calls=60):
    def make(args, meta=None, public=False, n_items=0):
        return pv.RealJevProvider(enabled=True, max_calls=max_calls, max_retries=0, transport=transport,
                                  env={pv.KEY_ENV: "k" * 24})
    monkeypatch.setattr(bm, "make_provider", make)


def _run(tmp_path):
    return bm.main(["--golden", str(PUB / "golden.json"), "--corpus-root", str(CHECKOUT), "--provider", "jev",
                    "--confirm-external-send", "--out", str(tmp_path)])


@needs_checkout
def test_full_public_run_with_wired_transport_captures_everything_needed(monkeypatch, tmp_path):
    counter: dict = {}
    _patch_real(monkeypatch, _wire_factory(counter))
    assert _run(tmp_path) == 0
    res = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert counter["n"] == 60 == res["meta"]["network_attempts"] and res["meta"]["aborted"] is None
    strategies = [r["strategy"] for r in res["rows"] if r["strategy"].startswith("jev_")]
    assert strategies == ["jev_rerank"] * 20 + ["jev_map"] * 20            # ordem: primeiro todos os jev_rerank
    need = {"id", "variant", "expected_files", "expected_regions", "selected_files", "selected_regions", "confidence",
            "p1", "p3", "r1", "r3", "r5", "input_bytes", "context_bytes_returned", "latency_ms", "calls", "parse_ok",
            "error_class", "http_status", "jev_input_tokens", "jev_output_tokens"}
    for r in (r for r in res["rows"] if r["strategy"].startswith("jev_")):
        assert need <= set(r), need - set(r)
        assert r["selected_files"] and r["selected_regions"] and r["input_bytes"] > 0 and r["parse_ok"] is True
        for c in r["calls"]:
            assert c["HTTP_STATUS"] == 200 and c["PARSE_OK"] and c["ERROR_CLASS"] is None and c["INPUT_BYTES"] > 0
            assert c["input_tokens"] is not None and c["top"] and c["LATENCY_MS"] >= 0
        assert len(r["calls"]) == (1 if r["strategy"] == "jev_rerank" else 2)
    ver = json.loads((tmp_path / "verdict.json").read_text(encoding="utf-8"))
    assert set(ver["detail"]) == {f"T{i}" for i in range(1, 9)} and ver["headline_strategy"] == "jev_map"
    assert all(v["status"] in ("PASS", "FAIL", "NOT_APPLICABLE") for v in ver["detail"].values())
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Comparação principal" in md and "Rede e custo" in md and "Limiares pré-registrados" in md
    assert res["summary"]["jev_map"]["semantic_only"]["RECALL_AT_1"] >= 0
    assert res["meta"]["redactions"].get("private_key", 0) == 0 and res["meta"].get("checkout_verified") is True


@needs_checkout
def test_run_stops_on_401_without_retry_and_keeps_partial_results(monkeypatch, tmp_path):
    counter: dict = {}
    _patch_real(monkeypatch, _wire_factory(counter, status=401))
    assert _run(tmp_path) == 0
    res = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert counter["n"] == 1 and res["meta"]["aborted"] and "401" in res["meta"]["aborted"]
    row = next(r for r in res["rows"] if r["strategy"] == "jev_rerank")
    assert row["failure"] == "error" and row["calls"][0]["HTTP_STATUS"] == 401 and row["calls"][0]["PARSE_OK"] is False
    assert json.loads((tmp_path / "verdict.json").read_text(encoding="utf-8"))["verdict"] == "INSUFFICIENT_EVIDENCE"


@needs_checkout
def test_run_never_sends_beyond_the_cap(monkeypatch, tmp_path):
    counter: dict = {}
    _patch_real(monkeypatch, _wire_factory(counter), max_calls=7)
    _run(tmp_path)
    res = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert counter["n"] == 7 and res["meta"]["aborted"] and "not_enabled" in res["meta"]["aborted"]


@needs_checkout
def test_payload_outside_the_public_corpus_is_refused_before_any_attempt(monkeypatch):
    from fake_provider import FakeJevProvider
    meta, items = _meta_items()
    files, chunks, index = bm._corpus_index(str(CHECKOUT), tuple(meta["corpus"]["roots"]), tuple(meta["corpus"]["extensions"]))
    fm = repomap.build_map(CHECKOUT, files)
    bm.index_root = CHECKOUT
    prov = FakeJevProvider("HIGH_CONFIDENCE")
    monkeypatch.setattr(bm, "allowed_files", {"scrapy/only_this.py"})
    with pytest.raises(bm.RunAborted):
        bm.jev_map(index, fm, prov, items[8].question, meta["corpus"]["roots"])
    with pytest.raises(bm.RunAborted):
        bm.jev_rerank(index, prov, items[0].question, meta["corpus"]["roots"])
    assert prov.calls == 0


def test_verify_public_checkout_flags_symlinks_and_bad_license(tmp_path):
    d = tmp_path / "repo"
    (d / "pkg").mkdir(parents=True)
    (d / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (d / "LICENSE").write_text("Redistribution and use in source and binary forms ... Neither the name of Scrapy nor",
                               encoding="utf-8")

    def git(*a):
        return subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@example.com", *a],
                              check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    git("remote", "add", "origin", "https://github.com/scrapy/scrapy.git")
    git("add", "-A")
    git("commit", "-q", "-m", "x")
    meta = {"visibility": "public", "source_sha_full": git("rev-parse", "HEAD"),
            "source_url": "https://github.com/scrapy/scrapy.git", "corpus": {"roots": ["pkg"]}}
    assert bm.verify_public_checkout(d, meta) == []
    outside = tmp_path / "outside.py"
    outside.write_text("secret = 1\n", encoding="utf-8")
    try:
        (d / "pkg" / "link.py").symlink_to(outside)
    except OSError:
        pytest.skip("sem permissão para criar symlink neste ambiente")
    assert any("symlink" in p for p in bm.verify_public_checkout(d, meta))
    (d / "pkg" / "link.py").unlink()
    (d / "LICENSE").write_text("outra licença", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "y")
    meta["source_sha_full"] = git("rev-parse", "HEAD")
    assert any("LICENSE" in p for p in bm.verify_public_checkout(d, meta))
