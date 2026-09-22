"""Item 3.5 — achado #179: o túnel SSH vira um componente observável, sondado por TCP nas portas locais que
`scripts/worker-tunnel.ps1` encaminha. Recusa de conexão = túnel fora; conexão aceita = túnel de pé.
"""
from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

from .conftest import Harness, make_config


async def _instalar_worker(h: Harness, *, worker_id: str, serial: str) -> None:
    """Registra um worker no banco (como `_upsert` faria na inscrição) e amarra `android-01` a ele como
    aparelho `external`, sem passar pelo WebSocket — o que este item cobre é a sonda, não o protocolo."""
    s = h.state
    assert s is not None
    s.db.execute(
        "INSERT INTO workers(id, name, os, os_version, agent_version, protocol, appium_mode, max_slots, verbs,"
        " state, resources, devices, enrolled_at, last_seen_at, token_hash) VALUES"
        " (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (worker_id, "Worker de teste", "windows", None, "1.0", 1, "central", 1, "[]", "online", None, "[]",
         "2026-09-22T00:00:00Z", "2026-09-22T00:00:00Z", "x"))
    rt = s.devices.get("android-01")
    rt.external = True
    rt.worker_id = worker_id
    rt.serial = serial


def _porta_livre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_porta_recusando_conexao_marca_o_tunel_como_fora(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        porta = _porta_livre()                          # ninguém escuta: conexão é recusada
        await _instalar_worker(h, worker_id="worker-fora", serial=f"127.0.0.1:{porta}")
        h.state._probe_transport()
        dto = next(w for w in h.state.workers.dtos() if w.id == "worker-fora")
        assert dto.transport_state == "down"
        assert dto.transport_detail and "recusando" in dto.transport_detail
        assert h.state._transport_state_of("worker-fora") == "down"
    finally:
        await h.state.stop()


async def test_porta_aceitando_conexao_marca_o_tunel_como_de_pe(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        servidor.bind(("127.0.0.1", 0))
        servidor.listen(1)
        porta = servidor.getsockname()[1]
        await _instalar_worker(h, worker_id="worker-ok", serial=f"127.0.0.1:{porta}")
        h.state._probe_transport()
        dto = next(w for w in h.state.workers.dtos() if w.id == "worker-ok")
        assert dto.transport_state == "up"
        assert dto.transport_detail is None
    finally:
        servidor.close()
        await h.state.stop()


async def test_worker_sem_aparelho_externo_fica_sem_sonda(tmp_path: Path) -> None:
    """Sem porta local nenhuma para testar, o estado não é inventado — fica de fora (`unknown` na leitura)."""
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        s = h.state
        s.db.execute(
            "INSERT INTO workers(id, name, os, os_version, agent_version, protocol, appium_mode, max_slots,"
            " verbs, state, resources, devices, enrolled_at, last_seen_at, token_hash) VALUES"
            " (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("worker-sem-aparelho", "Sem aparelho", "windows", None, "1.0", 1, "central", 1, "[]", "online",
             None, "[]", "2026-09-22T00:00:00Z", "2026-09-22T00:00:00Z", "x"))
        s._probe_transport()
        dto = next(w for w in s.workers.dtos() if w.id == "worker-sem-aparelho")
        assert dto.transport_state is None
    finally:
        await h.state.stop()


async def test_health_reporta_problema_quando_o_tunel_esta_fora(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        porta = _porta_livre()
        await _instalar_worker(h, worker_id="worker-fora-2", serial=f"127.0.0.1:{porta}")
        h.state._probe_transport()
        problemas = {p.code for p in h.state.health().problems}
        assert "tunnel_down" in problemas
    finally:
        await h.state.stop()


async def test_device_manager_distingue_tunel_fora_de_adb_sem_resposta(tmp_path: Path) -> None:
    """O motivo de 'sem ADB' passa a citar o túnel quando ele é a causa (achado #179), em vez da mesma frase de
    sempre para túnel caído, emulador desligado e ADB que não responde."""
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        porta = _porta_livre()
        await _instalar_worker(h, worker_id="worker-fora-3", serial=f"127.0.0.1:{porta}")
        h.state._probe_transport()
        rt = h.state.devices.get("android-01")
        dica = h.state.devices._transport_hint(rt)
        assert "túnel" in dica and "worker-fora-3" in dica
    finally:
        await h.state.stop()
