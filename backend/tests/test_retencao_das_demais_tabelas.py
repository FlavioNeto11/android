"""Item 10.2 — achados #39/#143: até aqui só `events` e `evidence` venciam; `commands`, `ai_calls`,
`measurements` e `worker_enrollments` usados/vencidos cresciam para sempre.
"""
from __future__ import annotations

import os
import time
from typing import Any

from .conftest import Harness


def _inserir_command(state: Any, *, id_: str, state_: str, finished_at: str | None) -> None:
    state.db.execute(
        "INSERT INTO commands(id, instance_id, verb, idempotency_key, state, requested_by, created_at, finished_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (id_, "android-01", "start", id_, state_, "panel", "2020-01-01T00:00:00Z", finished_at))


async def test_comandos_terminais_vencidos_saem_e_comando_aberto_fica(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    _inserir_command(state, id_="c-velho-ok", state_="succeeded", finished_at="2020-01-01T00:00:00Z")
    _inserir_command(state, id_="c-velho-rejeitado", state_="rejected", finished_at="2020-01-01T00:00:00Z")
    _inserir_command(state, id_="c-recente", state_="succeeded", finished_at="2099-01-01T00:00:00Z")
    _inserir_command(state, id_="c-aberto-velho", state_="dispatched", finished_at=None)  # nunca vence: não é terminal

    removidos = state._purgar_demais_tabelas("2026-01-01T00:00:00Z")

    ids_restantes = {r["id"] for r in state.db.query("SELECT id FROM commands")}
    assert "c-velho-ok" not in ids_restantes
    assert "c-velho-rejeitado" not in ids_restantes
    assert "c-recente" in ids_restantes
    assert "c-aberto-velho" in ids_restantes          # aberto sobrevive mesmo com created_at de 2020
    assert removidos >= 2


async def test_ai_calls_e_measurements_vencidos_saem_pelo_ts(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    state.db.execute("INSERT INTO ai_calls(ts, role, model) VALUES (?,?,?)", ("2020-01-01T00:00:00Z", "plan", "m"))
    state.db.execute("INSERT INTO ai_calls(ts, role, model) VALUES (?,?,?)", ("2099-01-01T00:00:00Z", "plan", "m"))
    state.db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)", ("2020-01-01T00:00:00Z", "capacity", "{}"))
    state.db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)", ("2099-01-01T00:00:00Z", "capacity", "{}"))

    state._purgar_demais_tabelas("2026-01-01T00:00:00Z")

    assert state.db.scalar("SELECT COUNT(*) FROM ai_calls") == 1
    assert state.db.scalar("SELECT ts FROM ai_calls") == "2099-01-01T00:00:00Z"
    assert state.db.scalar("SELECT COUNT(*) FROM measurements") == 1
    assert state.db.scalar("SELECT ts FROM measurements") == "2099-01-01T00:00:00Z"


async def test_inscricao_usada_ou_vencida_ha_mais_de_7_dias_sai_a_nao_usada_fica(harness: Harness) -> None:
    """Achado #39: `worker_enrollments` usados/vencidos ficavam para sempre. O prazo é fixo em 7 dias (o
    achado pede exatamente isto), não `log_retention_days` — o token não tem função nenhuma depois de
    consumido ou vencido; sete dias é só folga para suporte olhar inscrições recentes."""
    state = harness.state
    assert state is not None
    state.db.execute(
        "INSERT INTO worker_enrollments(token_hash, created_at, expires_at, used_at, used_by) VALUES (?,?,?,?,?)",
        ("hash-usado-velho", "2020-01-01T00:00:00Z", "2020-01-01T01:00:00Z", "2020-01-01T00:30:00Z", "worker-x"))
    state.db.execute(
        "INSERT INTO worker_enrollments(token_hash, created_at, expires_at) VALUES (?,?,?)",
        ("hash-vencido-velho", "2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z"))  # nunca usado, mas vencido há muito
    state.db.execute(
        "INSERT INTO worker_enrollments(token_hash, created_at, expires_at) VALUES (?,?,?)",
        ("hash-vivo", "2020-01-01T00:00:00Z", "2099-01-01T00:00:00Z"))          # nem usado nem vencido: fica

    state._purgar_demais_tabelas("2026-01-01T00:00:00Z")

    restantes = {r["token_hash"] for r in state.db.query("SELECT token_hash FROM worker_enrollments")}
    assert restantes == {"hash-vivo"}


# ------------------------------------------------------------------ arquivos (achado #144)
def _envelhecer(caminho: Any, dias: float) -> None:
    velho = time.time() - dias * 86400
    os.utime(caminho, (velho, velho))


async def test_log_1_vencido_sai_e_o_recente_fica(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    logs = state.cfg.logs_dir
    logs.mkdir(parents=True, exist_ok=True)
    velho = logs / "appium.log.1"
    velho.write_text("rotação antiga", encoding="utf-8")
    _envelhecer(velho, 30)
    recente = logs / "emulator-worker-01.log.1"
    recente.write_text("rotação de agora", encoding="utf-8")
    _envelhecer(recente, 1)

    removidos = state._purgar_arquivos_vencidos(14)

    assert not velho.exists()
    assert recente.exists()
    assert removidos == 1


async def test_sobra_de_sonda_de_imagem_vence_probe_e_avd_probe(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    logs = state.cfg.logs_dir
    logs.mkdir(parents=True, exist_ok=True)
    probe = logs / "probe-a30-540p-lowram.log"
    probe.write_text("sonda antiga", encoding="utf-8")
    _envelhecer(probe, 30)
    avd_probe = state.cfg.data_dir / "avd-probe"
    avd_dir = avd_probe / "probe-a30-540p-lowram.avd"
    avd_dir.mkdir(parents=True, exist_ok=True)
    (avd_dir / "userdata-qemu.img").write_bytes(b"x" * 1000)
    _envelhecer(avd_dir, 30)

    removidos = state._purgar_arquivos_vencidos(14)

    assert not probe.exists()
    assert not avd_dir.exists()
    assert removidos == 2


async def test_laco_de_retencao_chama_a_purga_das_demais_tabelas(harness: Harness) -> None:
    """O laço (`_retention_loop`) de verdade passa por `_purgar_demais_tabelas` a cada volta — sem isto o método
    existe mas nunca roda em produção."""
    state = harness.state
    assert state is not None
    chamados: list[str] = []
    original = state._purgar_demais_tabelas

    def espiao(cutoff: str) -> int:
        chamados.append(cutoff)
        return original(cutoff)

    state._purgar_demais_tabelas = espiao  # type: ignore[method-assign]
    import asyncio

    task = asyncio.ensure_future(state._retention_loop())
    try:
        await harness.wait(lambda: len(chamados) >= 1, timeout=5.0, what="primeira volta da retenção")
    finally:
        task.cancel()
