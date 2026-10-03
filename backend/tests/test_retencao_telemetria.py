"""RA-11 (parte Jev, reavaliação de 03/10): a classe de retenção da telemetria do parque, a purga em lotes, a memória
vencida que saía só da leitura e o `PRAGMA optimize` ao fim da volta.

Medido no central em 03/10 (só leitura): `instance.updated` tinha 32 mil linhas e 48 MiB em 14 dias, todas sem
execução, e 27 mil já passavam de 48 h. A testemunha da purga do relatório do Aprendizado (o evento sem execução mais
antigo) não muda: há eventos sem execução de outros tipos todo dia.

Nível de prova: `simulated` (harness na porta 5640, banco de teste).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import pytest

from app.events import TELEMETRIA_KINDS, TELEMETRIA_RETENCAO_H
from app.util import now, now_iso, to_iso

from .conftest import Harness


def _evento(state: Any, kind: str, ts: str, *, run_id: str | None = None) -> int:
    return int(state.db.inserted_id(
        "INSERT INTO events(ts, kind, level, run_id, instance_id, message) VALUES (?,?,'info',?,'android-01','x')",
        (ts, kind, run_id)))


def _ha(horas: float) -> str:
    return to_iso(now() - timedelta(hours=horas))


def _kinds(state: Any) -> list[tuple[str, str | None]]:
    return [(r["kind"], r["run_id"]) for r in state.db.query("SELECT kind, run_id FROM events WHERE message='x'"
                                                             " ORDER BY id")]


def test_premissa_a_classe_e_instance_updated_em_48_h() -> None:
    assert TELEMETRIA_KINDS == ("instance.updated",) and TELEMETRIA_RETENCAO_H == 48


async def test_telemetria_vence_em_48_h_e_o_resto_do_log_fica(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    state = harness.state
    assert state is not None
    state.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
                     " finished_at) VALUES ('r-fechada','k-f','x','execute','completed',1,'[]',?,?)", (_ha(80), _ha(79)))
    _evento(state, "instance.updated", _ha(72))                       # sai: telemetria velha
    _evento(state, "instance.updated", _ha(1))                        # fica: dentro das 48 h
    _evento(state, "instance.updated", _ha(72), run_id="r-fechada")   # fica: tem execução (a linha do tempo dela)
    _evento(state, "control.changed", _ha(72))                        # fica: o resto do log vai a 14 dias
    caplog.set_level(logging.INFO)

    assert await state._retencao_uma_vez() is True                    # noqa: SLF001

    assert _kinds(state) == [("instance.updated", None), ("instance.updated", "r-fechada"), ("control.changed", None)]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]   # nem a purga nem o optimize falharam


async def test_purga_em_lotes_leva_tudo_e_poupa_execucao_aberta(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    state.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
                     " VALUES ('r-aberta','k-a','x','execute','running',1,'[]',?)", (_ha(500),))
    for _ in range(7):
        _evento(state, "log", "2020-01-01T00:00:00Z")
    _evento(state, "decision", "2020-01-01T00:00:00Z", run_id="r-aberta")

    removidos = state.bus.purge_older_than("2021-01-01T00:00:00Z", lote=3)    # 3 + 3 + 1: três comandos

    assert removidos == 7
    assert _kinds(state) == [("decision", "r-aberta")]


async def test_memoria_vencida_sai_do_banco(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    db = state.db
    db.execute("INSERT INTO instagram_profiles(id, username, created_at, updated_at) VALUES ('p1','ana',?,?)",
               (now_iso(), now_iso()))
    for mid, expira in (("m-vencida", "2020-01-01T00:00:00Z"), ("m-futura", "2999-01-01T00:00:00Z"), ("m-sem-prazo", None)):
        db.execute("INSERT INTO memory_items(id, profile_id, subject, content, source, fingerprint, expires_at, created_at,"
                   " updated_at) VALUES (?,?,?,?,?,?,?,?,?)", (mid, "p1", "@bia", "gosta de café", "operator", mid,
                                                              expira, now_iso(), now_iso()))

    removidos = state._purgar_demais_tabelas("2000-01-01T00:00:00Z")   # noqa: SLF001

    assert removidos >= 1
    assert {r["id"] for r in db.query("SELECT id FROM memory_items")} == {"m-futura", "m-sem-prazo"}
