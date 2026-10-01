"""PRIVATE_CODE_GATE: o gate de privacidade nunca está em PASS sem evidência escrita e aceitação do dono, e as travas do código seguem fechadas.
Sem rede, sem Jev."""
from __future__ import annotations

import json
from pathlib import Path

import benchmark as bm
from corpus import repo_root

GATE = Path(bm.__file__).resolve().parent / "private_gate.json"
CLAIM_MARKS = ("OFFICIAL_EXPLICIT", "OFFICIAL_AMBIGUOUS", "NOT_DOCUMENTED")


def _gate():
    return json.loads(GATE.read_text(encoding="utf-8"))


def test_gate_is_blocked_and_the_code_locks_are_closed():
    g = _gate()
    assert g["state"] == "BLOCKED" and g["state"] in g["states"]
    assert g["locks"] == {"PRIVATE_CODE_SEND_APPROVED": False, "PRIVATE_CODE_BENCHMARK_STATUS": "BLOCKED_PRIVACY", "STANDARD_API_RETENTION": "UNKNOWN"}
    assert bm.PRIVATE_CODE_SEND_APPROVED is False and bm.PUBLIC_BENCHMARK_AUTHORIZED is False


def test_gate_pass_requires_written_evidence_and_owner_acceptance():
    g = _gate()
    if g["state"] == "PASS":                       # nunca automático: precisa de evidência escrita e aceitação registrada
        assert g["required_for_pass"]["written_vendor_evidence"] and g["required_for_pass"]["owner_risk_acceptance"]
        assert g["evidence"]
    assert g["states"] == ["BLOCKED", "WAITING_VENDOR", "ZDR_AVAILABLE", "ZDR_CONFIRMED", "OWNER_RISK_ACCEPTANCE_REQUIRED", "PASS"]
    t = g["transitions"]
    assert t["ZDR_AVAILABLE"]["to"] == "ZDR_CONFIRMED" and "abrangência escrita" in t["ZDR_AVAILABLE"]["when"]      # ZDR_AVAILABLE != PASS
    assert t["OWNER_RISK_ACCEPTANCE_REQUIRED"]["to"] == "PASS" and "EXPLÍCITA" in t["OWNER_RISK_ACCEPTANCE_REQUIRED"]["when"]
    assert not any(v["to"] == "PASS" for k, v in t.items() if k != "OWNER_RISK_ACCEPTANCE_REQUIRED")


def test_gate_claims_use_only_the_three_official_marks_and_the_vendor_message_is_unsent():
    g = _gate()
    for key, val in g["claims"].items():
        assert val.startswith(CLAIM_MARKS), (key, val)
    assert g["vendor_message"]["sent"] is False
    msg = (repo_root() / g["vendor_message"]["path"]).read_text(encoding="utf-8")
    assert "NÃO ENVIADO" in msg and msg.count("\n1. ") == 1 and "\n12. " in msg                      # as 12 perguntas do dono estão lá
    for n in range(1, 13):
        assert f"\n{n}. " in msg, n


def test_gate_message_has_no_secret_or_key_material():
    msg = (repo_root() / _gate()["vendor_message"]["path"]).read_text(encoding="utf-8")
    assert "TYPESAFE_API_KEY" not in msg and "Bearer" not in msg
