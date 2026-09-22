"""`uncertain` deixa de ser beco sem saída: a sonda fecha o que dá para provar, a pessoa decide o resto.

O caso que motivou este arquivo é real e ficou parado em produção: `c-20260921172322-6f7fdc`, um `start` remoto
que estourou o prazo de boot de 480 s e virou `uncertain` — com o aparelho subindo cinco minutos depois e
ficando `online`. O comando continuou "incerto" indefinidamente porque nada no sistema sabia fechá-lo: nem a
batida do worker (que informa `running` por aparelho), nem o estado do central, nem uma pessoa pelo painel.

A sonda é assimétrica de propósito: ver o aparelho no estado que o verbo prometia PROVA o sucesso; não ver não
prova o fracasso (podem tê-lo desligado depois). O que ela não fecha espera decisão humana, que é a segunda
porta — e é a única possível para `reset`/`install_apk`, cujo efeito nenhum estado de aparelho revela.
"""
from __future__ import annotations

from pathlib import Path

from app.commands.reconciler import reconciliar_incertos
from app.models import InstanceState
from app.workers.protocol import Heartbeat, WorkerDevice

from .test_efeitos_remotos import _esperar_desfecho, _parque_com_worker


async def _start_incerto(h, cliente, chave: str = "inc-start-1") -> str:
    """Um `start` remoto que terminou sem se saber o efeito — o caso vivo, reproduzido."""
    r = await cliente.post("/api/instances/android-03/actions/start", json={"idempotency_key": chave})
    assert r.status_code == 202, r.text
    cid = r.json()["command_id"]
    await _esperar_desfecho(h, cid, "uncertain")
    return cid


async def test_a_batida_do_worker_fecha_o_stop_incerto(tmp_path: Path) -> None:
    """A batida diz que não há processo daquele aparelho na máquina do worker: é prova de que o `stop` pegou.

    O inventário do worker só fecha AUSÊNCIA, nunca presença. `running` ali significa "o emulador está no ar",
    não "o Android subiu" — um emulador travado em ANR aparece `running`, e fechar um `start` com base nisso
    seria o sucesso falso que esta fase existe para eliminar. Quem fecha `start` é o `online` observado AQUI.
    """
    h, rt, _a, cliente = await _parque_com_worker(tmp_path, outcome="uncertain")
    try:
        async with cliente as c:
            r = await c.post("/api/instances/android-03/actions/stop", json={"idempotency_key": "inc-stop-3"})
            cid = r.json()["command_id"]
            await _esperar_desfecho(h, cid, "uncertain")
            rt.state = InstanceState.online           # o central ainda vê o aparelho no ar: sem prova nenhuma
            assert reconciliar_incertos(h.state) == []
            h.state.workers.on_heartbeat(  # type: ignore[union-attr]
                "worker-lan-01",
                Heartbeat(devices=[WorkerDevice(serial="emulator-5554", avd_name="worker-01", state="stopped",
                                                instance_id="android-03")]))
            fechados = reconciliar_incertos(h.state)
            assert [f["id"] for f in fechados] == [cid]
            registro = (await c.get(f"/api/commands/{cid}")).json()
        assert registro["state"] == "succeeded"
        assert "verificado pelo estado real" in registro["reason"]
        assert "batida do worker" in registro["reason"]
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_processo_no_ar_no_worker_nao_fecha_um_start_incerto(tmp_path: Path) -> None:
    """Um emulador travado em ANR aparece `running` para o worker. Isso não é "o aparelho ligou"."""
    h, rt, _a, cliente = await _parque_com_worker(tmp_path, outcome="uncertain")
    try:
        async with cliente as c:
            rt.state = InstanceState.stopped          # o central não vê o aparelho no ar
            cid = await _start_incerto(h, c)
            h.state.workers.on_heartbeat(  # type: ignore[union-attr]
                "worker-lan-01",
                Heartbeat(devices=[WorkerDevice(serial="emulator-5554", avd_name="worker-01", state="running",
                                                instance_id="android-03")]))
            assert reconciliar_incertos(h.state) == []
            assert h.state.commands.get(cid)["state"] == "uncertain"
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_verificar_agora_fecha_o_start_incerto_com_o_aparelho_no_ar(tmp_path: Path) -> None:
    """O botão do painel sobre o caso vivo: `start` incerto por prazo, aparelho `online`, comando fechado."""
    h, rt, _a, cliente = await _parque_com_worker(tmp_path, outcome="uncertain")
    try:
        async with cliente as c:
            cid = await _start_incerto(h, c, "inc-start-2")
            assert rt.state == InstanceState.online
            r = await c.post(f"/api/commands/{cid}/verify")
            assert r.status_code == 200, r.text
            corpo = r.json()
        assert corpo["changed"] is True and corpo["verifiable"] is True
        assert corpo["command"]["state"] == "succeeded"
        assert "está 'online'" in corpo["command"]["reason"]
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_sem_prova_o_comando_continua_incerto_e_a_rota_diz_isso(tmp_path: Path) -> None:
    """"Continua incerto" é resposta legítima, não erro: a ausência de prova nunca vira `failed` sozinha."""
    h, rt, _a, cliente = await _parque_com_worker(tmp_path, outcome="uncertain")
    try:
        async with cliente as c:
            r = await c.post("/api/instances/android-03/actions/stop", json={"idempotency_key": "inc-stop-1"})
            cid = r.json()["command_id"]
            await _esperar_desfecho(h, cid, "uncertain")
            assert rt.state == InstanceState.online       # o `stop` prometia o contrário: não há prova
            resposta = (await c.post(f"/api/commands/{cid}/verify")).json()
            assert resposta["changed"] is False and resposta["command"]["state"] == "uncertain"
            # E ele aparece na fila de quem ainda deve uma resposta.
            pendentes = (await c.get("/api/commands", params={"unsettled": "true"})).json()
            assert [p["id"] for p in pendentes] == [cid]
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_decisao_humana_tira_o_comando_de_incerto_e_registra_quem_decidiu(tmp_path: Path) -> None:
    """A porta que nenhuma sonda substitui. Quem resolveu e o que observou ficam gravados no comando."""
    h, rt, _a, cliente = await _parque_com_worker(
        tmp_path, outcome="uncertain", reason="o canal caiu depois do envio; resultado desconhecido")
    try:
        async with cliente as c:
            r = await c.post("/api/instances/android-03/actions/stop", json={"idempotency_key": "inc-stop-2"})
            cid = r.json()["command_id"]
            await _esperar_desfecho(h, cid, "uncertain")
            r2 = await c.post(f"/api/commands/{cid}/resolve",
                              json={"outcome": "failed", "note": "abri a máquina: o emulador continua no ar",
                                    "requested_by": "operador"})
            assert r2.status_code == 200, r2.text
            assert r2.json()["state"] == "failed"
            # Resolvido é resolvido: não sai mais da fila de pendentes nem aceita segunda decisão.
            assert (await c.get("/api/commands", params={"unsettled": "true"})).json() == []
            r3 = await c.post(f"/api/commands/{cid}/resolve", json={"outcome": "succeeded"})
            assert r3.status_code == 409 and r3.json()["detail"]["code"] == "not_unsettled"
        registro = h.state.commands.get(cid)              # type: ignore[union-attr]
        assert "operador" in registro["reason"] and "continua no ar" in registro["reason"]
        import json

        dados = json.loads(registro["result"])
        assert dados["resolved_by"] == "operador" and dados["resolution"] == "failed"
        assert dados["previous_reason"] == "o canal caiu depois do envio; resultado desconhecido"
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_resolver_comando_inexistente_nao_inventa_historia(tmp_path: Path) -> None:
    h, _rt, _a, cliente = await _parque_com_worker(tmp_path)
    try:
        async with cliente as c:
            r = await c.post("/api/commands/c-nao-existe/resolve", json={"outcome": "succeeded"})
            assert r.status_code == 404
            assert (await c.post("/api/commands/c-nao-existe/verify")).status_code == 404
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_o_snapshot_carrega_o_comando_sem_desfecho_para_a_tela_nao_esquecer(tmp_path: Path) -> None:
    """Recarregou, perdeu — era assim. O snapshot só trazia comando EM VOO, então um `uncertain` sumia da tela
    no primeiro F5 e continuava aberto no banco (o caso vivo ficou um dia inteiro invisível)."""
    h, _rt, _a, cliente = await _parque_com_worker(tmp_path, outcome="uncertain")
    try:
        async with cliente as c:
            cid = await _start_incerto(h, c, "inc-start-3")
            snap = (await c.get("/api/snapshot")).json()
            comandos = {cmd["instance_id"]: cmd for cmd in snap["commands"]}
            assert comandos["android-03"]["id"] == cid
            assert comandos["android-03"]["state"] == "uncertain"
            # Um por aparelho: o store guarda `lastCommand[instance_id]` e dois do mesmo aparelho fariam a
            # hidratação depender da ordem da lista.
            assert len(snap["commands"]) == len({c["instance_id"] for c in snap["commands"]})
    finally:
        if h.state is not None:
            await h.state.stop()
