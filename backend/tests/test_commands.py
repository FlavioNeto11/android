"""Comando do painel como entidade: a interface não pode chamar de sucesso o que só foi aceito.

O defeito que estes testes travam: `POST /instances/{id}/actions/{action}` respondia `202 {"accepted": true}` e
engolia qualquer exceção num evento `log` que o frontend nem tratava. "Resetar dados" recusado no fundo era
indistinguível de sucesso.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.commands.states import InvalidCommandTransition, check_transition
from app.main import create_app
from app.models import CommandState
from .conftest import Harness

EXTERNO = {"android-03": "127.0.0.1:15555"}


async def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_acao_devolve_id_de_comando_e_chega_a_estado_terminal(harness: Harness) -> None:
    async with await _cliente(harness) as c:
        r = await c.post("/api/instances/android-01/actions/home", json={"idempotency_key": "cmd-home-0001"})
        assert r.status_code == 202
        corpo = r.json()
        # O que mudou: antes era `{"accepted": true}` e nada mais. Agora há o que acompanhar.
        assert corpo["command_id"].startswith("c-") and corpo["deduplicated"] is False
        assert corpo["state"] == CommandState.dispatched.value

        await harness.wait(
            lambda: (harness.state.commands.get(corpo["command_id"])["state"]  # type: ignore[union-attr]
                     in (CommandState.succeeded.value, CommandState.failed.value, CommandState.uncertain.value)),
            what="comando atingir estado terminal")
        final = (await c.get(f"/api/commands/{corpo['command_id']}")).json()
        assert final["state"] == CommandState.succeeded.value
        # As marcas de tempo contam a história sem depender de narração.
        assert final["dispatched_at"] and final["started_at"] and final["finished_at"]


async def test_recusa_no_pre_voo_vira_comando_rejeitado_com_motivo(harness: Harness) -> None:
    """`rejected` é a prova de que NADA aconteceu no aparelho — e o motivo fica no histórico, não num log escondido."""
    async with await _cliente(harness) as c:
        r = await c.post("/api/instances/android-01/actions/reset", json={"idempotency_key": "cmd-reset-sem-confirm"})
        assert r.status_code == 409
        cid = r.json()["detail"]["command_id"]
        registro = (await c.get(f"/api/commands/{cid}")).json()
        assert registro["state"] == CommandState.rejected.value
        assert "confirm=true" in registro["reason"]
        # Recusado nunca foi despachado: sem isto não seria possível afirmar que o aparelho ficou intacto.
        assert registro["dispatched_at"] is None and registro["started_at"] is None
        assert registro["finished_at"]


async def test_mesma_chave_nao_age_duas_vezes(harness: Harness) -> None:
    async with await _cliente(harness) as c:
        corpo = {"idempotency_key": "cmd-repetido-0001"}
        r1 = await c.post("/api/instances/android-01/actions/home", json=corpo)
        r2 = await c.post("/api/instances/android-01/actions/home", json=corpo)
        assert r1.status_code == r2.status_code == 202
        assert r1.json()["command_id"] == r2.json()["command_id"]
        assert r2.json()["deduplicated"] is True
        assert len((await c.get("/api/commands", params={"instance_id": "android-01"})).json()) == 1


async def test_verbo_desconhecido_nao_gera_registro(harness: Harness) -> None:
    """Chamada malformada não é tentativa de operar o aparelho: não merece linha no histórico."""
    async with await _cliente(harness) as c:
        assert (await c.post("/api/instances/android-01/actions/voar", json={})).status_code == 400
        assert (await c.get("/api/commands", params={"instance_id": "android-01"})).json() == []


async def test_lote_registra_um_comando_por_aparelho_e_preserva_o_contrato(harness: Harness) -> None:
    async with await _cliente(harness) as c:
        r = await c.post("/api/instances/bulk", json={"ids": ["android-01", "android-02"], "action": "home",
                                                     "params": {"idempotency_key": "cmd-lote-0001"}})
        assert r.status_code == 202
        corpo = r.json()
        # `accepted` continua sendo lista de ids — o contrato antigo não foi quebrado.
        assert corpo["accepted"] == ["android-01", "android-02"]
        assert {c0["id"] for c0 in corpo["commands"]} == {"android-01", "android-02"}
        assert len({c0["command_id"] for c0 in corpo["commands"]}) == 2


async def test_reinicio_deixa_desfecho_honesto_em_comando_em_voo(tmp_path: Path) -> None:
    """Em voo na hora da queda = `uncertain`; ainda não despachado = `failed`. Nada é repetido sozinho."""
    h = Harness(tmp_path, 3)
    await h.boot()
    assert h.state is not None
    loja = h.state.commands
    em_voo, _ = loja.create(command_id="c-emvoo", instance_id="android-01", verb="stop",
                            idempotency_key="k-emvoo")
    loja.transition(em_voo["id"], CommandState.dispatched)
    loja.create(command_id="c-parado", instance_id="android-02", verb="stop", idempotency_key="k-parado")
    await h.crash()

    await h.boot()
    assert h.state is not None
    depois = h.state.commands
    assert depois.get("c-emvoo")["state"] == CommandState.uncertain.value
    assert "resultado desconhecido" in depois.get("c-emvoo")["reason"]
    assert depois.get("c-parado")["state"] == CommandState.failed.value
    assert "nada foi executado" in depois.get("c-parado")["reason"]
    await h.state.stop()


def test_transicao_invalida_e_recusada() -> None:
    check_transition(CommandState.created, CommandState.dispatched)
    check_transition(CommandState.dispatched, CommandState.uncertain)
    # Recusado é terminal: nada ressuscita um comando que nunca saiu.
    with pytest.raises(InvalidCommandTransition):
        check_transition(CommandState.rejected, CommandState.running)
    # Pedir cancelamento não é ter cancelado — e `cancelled` só vem do pedido, nunca direto de `running`.
    check_transition(CommandState.running, CommandState.cancel_requested)
    check_transition(CommandState.cancel_requested, CommandState.cancelled)
    with pytest.raises(InvalidCommandTransition):
        check_transition(CommandState.running, CommandState.cancelled)
