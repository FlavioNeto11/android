"""Entrega repetida do mesmo comando não despacha duas vezes nem inventa `cancelled` (frente F4, achado da fase A).

A entrega é AO MENOS uma vez (outbox, JetStream): uma segunda entrega do mesmo comando pode chegar com a primeira
ainda viva. Três defeitos saíam disso:

1. **Caminho do worker, as duas esperando o cadeado.** `_do_action_no_worker` conferia `created` ANTES do
   `rt.op_lock` e não reconferia dentro: as duas passavam, e a segunda despachava de novo um comando já
   concluído (e sobrescrevia `link.pendentes` se a primeira ainda esperasse o desfecho).
2. **O mesmo, com cancelamento pedido na espera.** A primeira fechava `cancelled` ("nada foi enviado"); a segunda,
   vendo `cancelled` e não `cancel_requested`, DESPACHAVA — o efeito depois do cancelamento confirmado.
3. **Caminho local.** A segunda falhava ao entrar em `running`, via `cancel_requested` e fechava como `cancelled`
   um comando que a primeira ainda executava.

A segunda entrega é simulada chamando `executar_envelope` com o mesmo envelope que o outbox publicou — é o que o
dreno ou o JetStream fariam. Cada teste roda duas vezes: com a guarda por processo (`em_execucao`) e SEM ela, que é
o caso de a segunda entrega chegar a outro processo — aí quem segura são as releituras do estado (dentro do
cadeado, e `started_at` no caminho local).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.api import executar_envelope
from app.commands.outbox import CommandOutbox
from app.models import CommandState

from .test_contrato_de_worker import _acao, _desfecho, _parque


GUARDAS = ["mesmo_processo", "outro_processo"]


class _ConjuntoCego(set):
    """A guarda por processo desligada: a segunda entrega chega a um processo que não viu a primeira."""

    def __contains__(self, _item: object) -> bool:
        return False


def _guarda(s: Any, guarda: str) -> None:
    if guarda == "outro_processo":
        s.commands.em_execucao = _ConjuntoCego()


def _envelope(s: Any, command_id: str) -> dict[str, Any]:
    """O envelope como `_despachar` o monta a partir da linha do outbox."""
    linha = s.outbox.get(command_id)
    return {"command_id": command_id, "instance_id": linha["instance_id"], "worker_id": linha["worker_id"],
            "verb": linha["verb"], **CommandOutbox.payload_of(linha)}


def _despachos(agente: Any, command_id: str) -> int:
    return sum(1 for p in agente.vistos if p.get("type") == "dispatch" and p.get("command_id") == command_id)


@pytest.mark.parametrize("guarda", GUARDAS)
async def test_duas_entregas_esperando_o_cadeado_despacham_uma_vez(tmp_path: Path, guarda: str) -> None:
    h, rt, cliente, worker_id = await _parque(tmp_path, "agente")
    s = h.state
    assert s is not None
    _guarda(s, guarda)
    try:
        agente = s.workers.live[worker_id].send.__self__  # type: ignore[attr-defined]
        await rt.op_lock.acquire()            # o comando anterior do aparelho ainda segura o cadeado
        try:
            async with cliente as c:
                cid = await _acao(c, rt, "stop", "repetida-1")
            await asyncio.sleep(0.05)         # a primeira entrega já está esperando o cadeado, em `created`
            segunda = asyncio.create_task(executar_envelope(s, _envelope(s, cid)))
            await asyncio.sleep(0.05)
        finally:
            rt.op_lock.release()
        await _desfecho(h, cid)
        await asyncio.wait_for(segunda, timeout=5)
        await asyncio.sleep(0.05)
        assert _despachos(agente, cid) == 1, "a segunda entrega despachou de novo um comando já concluído"
        assert s.commands.get(cid)["state"] == CommandState.succeeded.value
    finally:
        await s.stop()


@pytest.mark.parametrize("guarda", GUARDAS)
async def test_cancelado_na_espera_do_cadeado_nao_e_despachado_pela_segunda_entrega(tmp_path: Path, guarda: str) -> None:
    h, rt, cliente, worker_id = await _parque(tmp_path, "agente")
    s = h.state
    assert s is not None
    _guarda(s, guarda)
    try:
        agente = s.workers.live[worker_id].send.__self__  # type: ignore[attr-defined]
        await rt.op_lock.acquire()
        try:
            async with cliente as c:
                cid = await _acao(c, rt, "stop", "repetida-2")
                await asyncio.sleep(0.05)
                segunda = asyncio.create_task(executar_envelope(s, _envelope(s, cid)))
                await asyncio.sleep(0.05)
                r = await c.post(f"/api/commands/{cid}/cancel", json={})
                assert r.status_code == 200, r.text
        finally:
            rt.op_lock.release()
        await _desfecho(h, cid, (CommandState.cancelled.value,))
        await asyncio.wait_for(segunda, timeout=5)
        await asyncio.sleep(0.05)
        assert _despachos(agente, cid) == 0, "comando cancelado (nada enviado) foi despachado pela segunda entrega"
        assert s.commands.get(cid)["state"] == CommandState.cancelled.value
    finally:
        await s.stop()


@pytest.mark.parametrize("guarda", GUARDAS)
async def test_entrega_repetida_no_caminho_local_nao_fecha_como_cancelado_o_que_esta_rodando(
        tmp_path: Path, guarda: str) -> None:
    h, rt, cliente, _worker_id = await _parque(tmp_path, "local")
    s = h.state
    assert s is not None
    _guarda(s, guarda)
    solta = asyncio.Event()
    execucoes: list[str] = []

    async def tecla_lenta(_rt: Any, acao: str) -> None:
        execucoes.append(acao)
        await solta.wait()

    s.devices.quick_key = tecla_lenta  # type: ignore[method-assign]
    try:
        async with cliente as c:
            cid = await _acao(c, rt, "home", "repetida-3")
            await h.wait(lambda: s.commands.get(cid)["state"] == CommandState.running.value, what="o verbo rodar")
            r = await c.post(f"/api/commands/{cid}/cancel", json={})
            assert r.status_code == 200, r.text
            assert s.commands.get(cid)["state"] == CommandState.cancel_requested.value
            await executar_envelope(s, _envelope(s, cid))          # a reentrega, com a primeira ainda no verbo
            assert s.commands.get(cid)["state"] == CommandState.cancel_requested.value, (
                "a reentrega fechou como cancelado um comando que ainda está executando")
            solta.set()
            linha = await _desfecho(h, cid, (CommandState.succeeded.value, CommandState.cancelled.value))
        assert linha["state"] == CommandState.succeeded.value
        assert execucoes == ["home"]
    finally:
        solta.set()
        await s.stop()
