"""Provedores: fake (6 modos), real inerte, validação de resposta, redação no envio."""
from __future__ import annotations

import json
import socket

import pytest

import provider as pv
from fake_provider import FakeJevProvider, FakeMode, choice_confidence

# Valores montados em tempo de execução: o arquivo não contém nenhum literal com formato de segredo.
FAKE_API_KEY = "sk" + "-" + "ab" * 20

CANDS = {"c1": "def pedir_reinicio(): restart the device", "c2": "def unrelated(): pass", "c3": "class Foo: x = 1"}
Q = "Onde pedir_reinicio é chamado para restart?"


def test_fake_high_confidence_ranks_and_is_sharp():
    r = FakeJevProvider(FakeMode.HIGH_CONFIDENCE).retrieve(Q, CANDS)
    assert r.ranked[0][0] == "c1"
    assert r.confidence > 0.8
    assert abs(sum(p for _, p in r.ranked) - 1.0) < 1e-6
    assert r.exists is not None and r.requests == 1


def test_fake_low_confidence_is_flat():
    r = FakeJevProvider(FakeMode.LOW_CONFIDENCE).retrieve(Q, CANDS)
    assert r.confidence < 0.1
    assert r.ranked[0][1] < 0.5


def test_fake_timeout():
    with pytest.raises(pv.ProviderTimeout):
        FakeJevProvider(FakeMode.TIMEOUT).retrieve(Q, CANDS)


def test_fake_error():
    with pytest.raises(pv.ProviderError) as ei:
        FakeJevProvider(FakeMode.ERROR).retrieve(Q, CANDS)
    assert ei.value.kind == "error"


def test_fake_invalid_response():
    with pytest.raises(pv.InvalidResponse):
        FakeJevProvider(FakeMode.INVALID_RESPONSE).retrieve(Q, CANDS)


def test_fake_unknown_choice_is_rejected_not_executed():
    with pytest.raises(pv.UnknownChoice):
        FakeJevProvider(FakeMode.UNKNOWN_CHOICE).choose({"x": 1}, "pick", {"A01": None, "A02": None})


def test_choose_validates_closed_set_even_for_probabilities():
    bad = {"answers": {"q": {"type": "choice", "choice": "A01", "probabilities": {"A01": 0.5, "A99": 0.5},
                              "confidence": 0.9}}}
    with pytest.raises(pv.UnknownChoice):
        pv.parse_choice(bad["answers"]["q"], {"A01", "A02"})


def test_confidence_formula_matches_public_doc():
    assert choice_confidence([0.5, 0.5]) == 0.0
    assert choice_confidence([1.0, 0.0, 0.0]) == 1.0
    assert abs(choice_confidence([0.88, 0.12, 0.0]) - 0.82) < 0.02       # exemplo da doc oficial: 0.81


def test_request_body_is_official_shape():
    state, qs = pv.retrieval_request(Q, CANDS)
    body = pv.request_body(state, qs)
    assert set(body) == {"state", "model", "questions"}
    assert body["model"] == "jev-1.13.0"          # versão fixada, não o alias
    assert body["questions"]["best"]["type"] == "choice"
    assert set(body["questions"]["best"]["criteria"]) == set(CANDS)
    assert body["questions"]["exists"]["type"] == "noul"


# --------------------------------------------------------------------------- real: inerte
def _boom_transport(*_a, **_k):                     # qualquer chamada de transporte é falha do teste
    raise AssertionError("o transporte NÃO deveria ter sido chamado")


def test_real_disabled_makes_no_request(monkeypatch):
    p = pv.RealJevProvider(enabled=False, env={pv.KEY_ENV: "k" * 20}, transport=_boom_transport)
    with pytest.raises(pv.ProviderNotEnabled):
        p.retrieve(Q, CANDS)
    assert p.calls == 0


def test_real_missing_key_makes_no_request():
    p = pv.RealJevProvider(enabled=True, env={}, transport=_boom_transport)
    with pytest.raises(pv.MissingKey):
        p.retrieve(Q, CANDS)
    assert p.calls == 0


def test_real_never_opens_a_socket_without_enable(monkeypatch):
    def deny(*a, **k):
        raise AssertionError("socket aberto")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    p = pv.RealJevProvider(enabled=False, env={pv.KEY_ENV: "x" * 20})      # transporte padrão (urllib)
    with pytest.raises(pv.ProviderNotEnabled):
        p.evaluate({"a": 1}, {})


def _ok_body(ids):
    probs = {i: (0.9 if k == 0 else 0.1 / max(1, len(ids) - 1)) for k, i in enumerate(ids)}
    return json.dumps({"model": "jev-1.13.0", "usage": {"input_tokens": 100, "output_tokens": 5}, "answers": {
        "best": {"type": "choice", "choice": ids[0], "probabilities": probs, "confidence": 0.85},
        "exists": {"type": "noul", "noul": 0.9}}}).encode()


def test_real_happy_path_with_fake_transport_and_headers():
    seen = {}

    def transport(url, headers, body, timeout):
        seen.update(url=url, headers=headers, body=json.loads(body))
        return 200, _ok_body(list(CANDS))
    p = pv.RealJevProvider(enabled=True, env={pv.KEY_ENV: "secret-key-0123456789"}, transport=transport)
    r = p.retrieve(Q, CANDS)
    assert r.ranked[0][0] == "c1" and r.usage.input_tokens == 100
    assert seen["url"] == pv.API_URL
    assert seen["headers"]["Authorization"].startswith("Bearer ")
    assert set(seen["body"]) == {"state", "model", "questions"}


def test_real_retries_429_then_succeeds_and_hard_fails_401():
    calls = []

    def transport(url, headers, body, timeout):
        calls.append(1)
        return (429, b"{}") if len(calls) < 3 else (200, _ok_body(list(CANDS)))
    sleeps = []
    p = pv.RealJevProvider(enabled=True, env={pv.KEY_ENV: "k" * 20}, transport=transport, sleep=sleeps.append)
    p.retrieve(Q, CANDS)
    assert len(calls) == 3 and sleeps == [1.0, 2.0]

    p2 = pv.RealJevProvider(enabled=True, env={pv.KEY_ENV: "k" * 20}, transport=lambda *a: (401, b"{}"))
    with pytest.raises(pv.ProviderError) as ei:
        p2.retrieve(Q, CANDS)
    assert "401" in str(ei.value) and "k" * 20 not in str(ei.value)


def test_real_max_calls_ceiling():
    p = pv.RealJevProvider(enabled=True, max_calls=1, env={pv.KEY_ENV: "k" * 20},
                           transport=lambda *a: (200, _ok_body(list(CANDS))))
    p.retrieve(Q, CANDS)
    with pytest.raises(pv.ProviderNotEnabled):
        p.retrieve(Q, CANDS)


def test_real_blocks_hard_secret_and_never_sends():
    leaked = dict(CANDS, c2="API_KEY = 'x'; token " + FAKE_API_KEY)
    p = pv.RealJevProvider(enabled=True, env={pv.KEY_ENV: "k" * 20}, transport=_boom_transport)
    with pytest.raises(pv.PrivacyBlock):
        p.retrieve(Q, leaked)
    assert p.calls == 0
    assert p.redactions.get("api_key") == 1


def test_real_soft_redaction_still_sends_without_value():
    sent = {}

    def transport(url, headers, body, timeout):
        sent["body"] = body.decode()
        return 200, _ok_body(list(CANDS))
    cands = dict(CANDS, c2="contato: fulano@example.com senha = 'hunter2hunter2'")
    p = pv.RealJevProvider(enabled=True, env={pv.KEY_ENV: "k" * 20}, transport=transport)
    p.retrieve(Q, cands)
    assert "fulano@example.com" not in sent["body"] and "hunter2hunter2" not in sent["body"]
    assert p.redactions.get("email") == 1 and p.redactions.get("secret_assign") == 1
    assert "k" * 20 not in sent["body"]               # a chave nunca vai no corpo


def test_importing_provider_modules_opens_no_connection(monkeypatch):
    import importlib

    def deny(*a, **k):
        raise AssertionError("conexão aberta no import")
    monkeypatch.setattr(socket.socket, "connect", deny)
    importlib.reload(pv)
    import fake_provider
    importlib.reload(fake_provider)
