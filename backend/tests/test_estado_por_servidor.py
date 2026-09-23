"""Item 3.6 — achado #61: "aparelho externo X não está conectado ao ADB" era a única frase para três realidades
diferentes (emulador desligado de propósito no worker, servidor fora do ar, e ADB que não alcança um emulador
ligado). O motivo passa a sair do PROCESSO que o worker reporta, e cita o servidor pelo nome.
"""
from __future__ import annotations

import json
from pathlib import Path

from .conftest import Harness

WORKER = "worker-lan-01"
NOME = "Notebook da LAN"


def _inscrever(h: Harness, *, devices: list[dict[str, object]], conectado: bool) -> None:
    s = h.state
    s.db.execute(
        "INSERT INTO workers(id, name, os, os_version, agent_version, protocol, appium_mode, max_slots, verbs,"
        " state, resources, devices, enrolled_at, last_seen_at, token_hash) VALUES"
        " (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (WORKER, NOME, "windows", None, "1.0", 1, "local", 6, "[]", "online", None, json.dumps(devices),
         "2026-09-22T00:00:00Z", "2026-09-22T00:00:00Z", "x"))
    if conectado:
        s.workers.attach(WORKER, lambda _msg: _nada())


async def _nada() -> None:
    return None


def _amarrar(h: Harness) -> object:
    rt = h.state.devices.get("android-01")
    rt.external = True
    rt.worker_id = WORKER
    rt.serial = "127.0.0.1:15561"
    return rt


async def test_emulador_desligado_de_proposito_no_worker_nao_vira_problema_de_adb(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        _inscrever(h, devices=[{"serial": "emulator-5554", "state": "stopped", "instance_id": "android-01"}],
                   conectado=True)
        rt = _amarrar(h)
        motivo = h.state.devices._motivo_do_externo_parado(rt, None)
        assert motivo == f"emulador desligado em {NOME}"
        assert "ADB" not in motivo
    finally:
        await h.state.stop()


async def test_servidor_fora_do_ar_diz_que_o_estado_e_desconhecido(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        # Mesmo com o banco guardando "running" da última batida: declaração de quem não está lá não vale.
        _inscrever(h, devices=[{"serial": "emulator-5554", "state": "running", "instance_id": "android-01"}],
                   conectado=False)
        rt = _amarrar(h)
        motivo = h.state.devices._motivo_do_externo_parado(rt, None)
        assert motivo == f"servidor {NOME} fora do ar — o estado do emulador lá é desconhecido"
    finally:
        await h.state.stop()


async def test_emulador_ligado_no_worker_com_adb_inalcancavel_aponta_o_tunel(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        # "running" é o que o agente de fato emite (`executor.estado`); o teste dizia "online", que ele nunca emite.
        _inscrever(h, devices=[{"serial": "emulator-5554", "state": "running", "instance_id": "android-01"}],
                   conectado=True)
        rt = _amarrar(h)
        h.state.devices.transport_state_of = lambda _w: "down"
        motivo = h.state.devices._motivo_do_externo_parado(rt, "offline")
        assert f"emulador ligado em {NOME} (running)" in motivo
        assert "não responde ao ADB" in motivo
        assert "(adb: offline)" in motivo, "o estado que o adb devolveu é informação, não ruído"
        assert "túnel" in motivo
    finally:
        await h.state.stop()


async def test_worker_que_nao_reporta_o_aparelho_e_aparelho_sem_worker_seguem_distintos(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        _inscrever(h, devices=[], conectado=True)
        rt = _amarrar(h)
        assert h.state.devices._motivo_do_externo_parado(rt, None) == \
            f"servidor {NOME} está no ar, mas não reporta este aparelho"
        # Aparelho externo sem worker nenhum continua com o sintoma antigo — ali ele é a verdade.
        rt.worker_id = None
        motivo = h.state.devices._motivo_do_externo_parado(rt, "offline")
        assert motivo == "aparelho externo 127.0.0.1:15561 não está conectado ao ADB (estado: offline)"
    finally:
        await h.state.stop()


async def test_processo_de_so_confia_no_estado_com_o_worker_conectado(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        _inscrever(h, devices=[{"serial": "emulator-5554", "state": "online", "instance_id": "android-01"}],
                   conectado=True)
        assert h.state.workers.processo_de(WORKER, "android-01") == (NOME, True, "online")
        assert h.state.workers.processo_de(WORKER, "android-09") == (NOME, True, None)
        assert h.state.workers.processo_de("worker-que-nao-existe", "android-01") is None
    finally:
        await h.state.stop()


async def test_o_motivo_ACOMPANHA_o_worker_em_vez_de_congelar_na_primeira_frase(tmp_path: Path) -> None:
    """O aparelho fica `stopped` o tempo todo, mas a CAUSA muda: o servidor cai, o emulador de lá é ligado.

    A guarda antiga (`state != stopped or not state_detail`) congelava a primeira frase para sempre — e o
    cartão passava a exibir título e detalhe contraditórios, que é justamente o que o achado #61 descreve.
    """
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        _inscrever(h, devices=[{"serial": "emulator-5554", "state": "stopped", "instance_id": "android-01"}],
                   conectado=True)
        rt = h.state.devices.get("android-01")
        rt.external = True
        rt.worker_id = WORKER
        rt.serial = "127.0.0.1:15561"
        rt.adb.connect = lambda *_a, **_k: None          # type: ignore[method-assign]
        rt.adb.state = lambda *_a, **_k: "offline"       # type: ignore[method-assign]

        await h.state.devices._adopt_external(rt)
        assert rt.state_detail == f"emulador desligado em {NOME}"

        # O servidor cai: o painel não pode continuar afirmando que o emulador de lá está desligado.
        h.state.workers.detach(WORKER, "teste", h.state.workers.live[WORKER])
        await h.state.devices._adopt_external(rt)
        assert rt.state_detail == f"servidor {NOME} fora do ar — o estado do emulador lá é desconhecido"
    finally:
        await h.state.stop()


async def test_tunel_de_pe_e_o_android_de_la_mudo_nao_culpa_o_tunel(tmp_path: Path) -> None:
    """Medido em 23/09: android-12 e android-15 travados no próprio notebook (adb `offline` lá, túnel sondado
    `up`) apareciam como queda de túnel. A frase agora nomeia o culpado certo — e só cita o túnel quando a
    sonda diz `down`."""
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        _inscrever(h, devices=[{"serial": "emulator-5554", "state": "running", "instance_id": "android-01"}],
                   conectado=True)
        rt = _amarrar(h)
        h.state.devices.transport_state_of = lambda _w: "up"
        motivo = h.state.devices._motivo_do_externo_parado(rt, "offline")
        assert "túnel" not in motivo
        assert "não responde ao ADB daqui (adb: offline)" in motivo
    finally:
        await h.state.stop()


async def test_processo_desconhecido_nao_vira_emulador_ligado(tmp_path: Path) -> None:
    """`unknown` é o que o agente diz de aparelho que não gere (físico, contêiner) e de sonda que falhou. Não estava
    em PROCESSO_PARADO e caía no ramo "ligado … não alcança" — afirmação sobre algo que ninguém disse estar ligado."""
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        _inscrever(h, devices=[{"serial": "emulator-5554", "state": "unknown", "instance_id": "android-01"}],
                   conectado=True)
        rt = _amarrar(h)
        motivo = h.state.devices._motivo_do_externo_parado(rt, None)
        assert motivo == f"servidor {NOME} não sabe o estado do emulador deste aparelho"
    finally:
        await h.state.stop()
