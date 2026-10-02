"""Os eventos `command.updated` e `worker.updated` (item T.2, achado #166).

São os dois eventos de que o painel vive: o primeiro conta cada passo de um comando de aparelho (e é o que a aba
Registros da Infraestrutura lista); o segundo é o que mantém o cartão do servidor honesto. Antes só havia teste
indireto — a asserção de um outro assunto que, de passagem, lia a tabela `events`. Aqui o contrato é o assunto:

- QUEM emite: `publicar_comando` (comandos/store.py), `_anunciar_inflight` (reconexão do agente) e
  `AppState._publish_worker` (chamado por todo `on_change` do registro de workers);
- O PAYLOAD: `data.command` é o `CommandDTO`; `data.worker` é o `WorkerDTO`; `instance_id` vai no envelope;
- O NÍVEL: falha e recusa são `error`, incerteza é `warn`, o resto `info`; worker offline ou degradado é `warn`;
- QUANDO: toda mudança de estado do comando, e só a mudança OBSERVÁVEL do worker (a batida igual não gera nada).

Prova `simulated`: banco SQLite temporário e agente falso, sem emulador, rede nem IA. O harness de `conftest.py`
usa `base_console_port: 5640`, fora das portas dos emuladores reais.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.commands.despacho import _anunciar_inflight
from app.commands.store import publicar_comando
from app.api import _tratar_mensagem_do_worker
from app.models import CommandState
from app.util import iso_in
from app.workers.protocol import Ack, Heartbeat, Progress, Result, WorkerDevice, WorkerResources
from .conftest import Harness
from .test_marcas_de_tempo import _hello, _parque_com_worker

C = CommandState


def _eventos(h: Harness, kind: str, depois: int = 0, **filtro: Any) -> list[tuple[Any, dict[str, Any]]]:
    """Eventos PERSISTIDOS daquele tipo (linha + `data` já decodificado), do mais antigo ao mais novo."""
    assert h.state is not None
    linhas = h.state.db.query("SELECT * FROM events WHERE kind=? AND id>? ORDER BY id", (kind, depois))
    saida = []
    for linha in linhas:
        dados = json.loads(linha["data"]) if linha["data"] else {}
        if all(linha[k] == v for k, v in filtro.items()):
            saida.append((linha, dados))
    return saida


def _ultimo_id(h: Harness) -> int:
    assert h.state is not None
    return int(h.state.db.scalar("SELECT COALESCE(MAX(id), 0) FROM events") or 0)


def _novo_comando(h: Harness, instance_id: str, chave: str, *caminho: CommandState) -> Any:
    """Cria um comando e o leva, transição a transição, pelo `caminho`. Devolve a linha final."""
    assert h.state is not None
    row, repetido = h.state.commands.create(command_id=f"cmd-{chave}", instance_id=instance_id, verb="restart",
                                           idempotency_key=chave)
    assert not repetido
    for alvo in caminho:
        row = h.state.commands.transition(row["id"], alvo, reason=f"motivo {alvo.value}")
    return row


# ---------------------------------------------------------------- command.updated: payload e nível
@pytest.mark.parametrize(("caminho", "nivel"), [
    ((C.rejected,), "error"),                       # recusado antes de despachar: nada foi tocado
    ((C.failed,), "error"),
    ((C.dispatched, C.running), "info"),
    ((C.dispatched, C.succeeded), "info"),
    ((C.dispatched, C.uncertain), "warn"),          # o estado que o painel mais precisa destacar
])
async def test_publicar_comando_leva_o_comando_inteiro_e_o_nivel_do_estado(harness: Harness, caminho: tuple[CommandState, ...],
                                                                         nivel: str) -> None:
    s = harness.state
    assert s is not None
    marca = _ultimo_id(harness)
    chave = "nivel-" + "-".join(e.value for e in caminho)
    row = _novo_comando(harness, "android-01", chave, *caminho)
    fila = s.bus.subscribe()
    try:
        assert publicar_comando(s.bus, row) is row         # devolve a mesma linha: quem chama encadeia
    finally:
        s.bus.unsubscribe(fila)

    [(evento, dados)] = _eventos(harness, "command.updated", marca)
    estado = caminho[-1].value
    assert evento["level"] == nivel
    assert evento["instance_id"] == "android-01"
    assert evento["message"].startswith(f"android-01: restart — {estado}")
    assert f"(motivo {estado})" in evento["message"], "o motivo vai na mensagem"
    comando = dados["command"]
    assert comando["id"] == row["id"] and comando["instance_id"] == "android-01"
    assert comando["verb"] == "restart" and comando["state"] == estado
    assert comando["reason"] == f"motivo {estado}" and comando["fence"] == row["fence"]
    # Quem está com o painel aberto recebe o MESMO evento pelo barramento, com id (é persistido).
    entregue = fila.get_nowait()
    assert entregue.kind == "command.updated" and entregue.id == evento["id"]


async def test_comando_sem_motivo_nao_leva_parenteses_na_mensagem(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    marca = _ultimo_id(harness)
    row, _ = s.commands.create(command_id="cmd-sem-motivo", instance_id="android-02", verb="stop",
                               idempotency_key="sem-motivo-0001")
    publicar_comando(s.bus, row)
    [(evento, dados)] = _eventos(harness, "command.updated", marca)
    assert evento["message"] == "android-02: stop — created"
    assert dados["command"]["reason"] is None


async def test_command_updated_nao_e_efemero(harness: Harness) -> None:
    """Sobrevive à reconexão do painel: o replay por `since` precisa devolver a trilha do comando."""
    from app.events import EPHEMERAL_KINDS
    assert "command.updated" not in EPHEMERAL_KINDS and "worker.updated" not in EPHEMERAL_KINDS
    assert "worker.metrics" in EPHEMERAL_KINDS


async def test_o_aviso_de_comando_em_voo_marca_inflight_e_ignora_o_que_nao_e_do_worker(harness: Harness) -> None:
    """O agente reconectou dizendo o que AINDA executa: o estado do comando não muda, mas o painel é avisado."""
    s = harness.state
    assert s is not None
    marca = _ultimo_id(harness)
    uncertain = _novo_comando(harness, "android-01", "voo-0001", C.dispatched, C.uncertain)
    alheio = _novo_comando(harness, "android-02", "voo-0002")
    s.db.execute("UPDATE commands SET worker_id=? WHERE id=?", ("worker-outro", alheio["id"]))

    _anunciar_inflight(s, "worker-lan-01", [uncertain["id"], alheio["id"], "cmd-que-nao-existe"])

    [(evento, dados)] = _eventos(harness, "command.updated", marca)
    assert evento["level"] == "warn" and evento["instance_id"] == "android-01"
    assert dados["inflight"] is True and dados["command"]["id"] == uncertain["id"]
    assert dados["command"]["state"] == C.uncertain.value, "avisar não decide: continua incerto"
    assert s.commands.get(uncertain["id"])["state"] == C.uncertain.value


# ---------------------------------------------------------------- command.updated: cada mudança de estado emite
async def test_cada_passo_de_um_comando_remoto_vira_um_command_updated(tmp_path: Path) -> None:
    """Pelo caminho real (HTTP → despacho → ack → progresso → desfecho do agente), a trilha do painel tem um
    evento por estado, na ordem — e o progresso do worker entra como `command.updated` do MESMO comando."""
    h, agente, link, cliente = await _parque_com_worker(tmp_path)
    s = h.state
    assert s is not None
    try:
        async with cliente as c:
            r = await c.post("/api/instances/android-03/actions/stop", json={"idempotency_key": "evt-0001"})
            assert r.status_code == 202, r.text
            cid = r.json()["command_id"]
            await h.wait(lambda: agente.despacho() is not None, what="despacho chegar ao agente")
            agente.porteiro.set()
            await h.wait(lambda: s.commands.get(cid)["state"] == C.dispatched.value, what="despachado")
            await _tratar_mensagem_do_worker(s, "worker-lan-01", link, Ack(command_id=cid))
            await _tratar_mensagem_do_worker(s, "worker-lan-01", link,
                                             Progress(command_id=cid, message="parando worker-03"))
            fence = int(s.commands.get(cid)["fence"])
            await _tratar_mensagem_do_worker(s, "worker-lan-01", link,
                                             Result(command_id=cid, outcome="succeeded", fence=fence))
            await h.wait(lambda: s.commands.get(cid)["state"] == C.succeeded.value, what="comando concluído")

        trilha = [(e, d) for e, d in _eventos(h, "command.updated", instance_id="android-03")
                  if d["command"]["id"] == cid]
        estados = [d["command"]["state"] for _, d in trilha]
        # `created` NÃO vira evento (a resposta 202 já o carrega); cada marco seguinte, sim, na ordem em que o
        # produz — e o progresso do worker é um `running` a mais, com a frase em `data.progress`.
        assert estados == [C.dispatched.value, C.acked.value, C.running.value, C.running.value,
                           C.succeeded.value], estados
        assert ["progress" in d for _, d in trilha] == [False, False, False, True, False]
        progresso = [d for _, d in trilha if "progress" in d]
        assert [p["progress"] for p in progresso] == ["parando worker-03"]
        ids = [e["id"] for e, _ in trilha]
        assert ids == sorted(ids), "a ordem dos ids é a ordem dos fatos"
    finally:
        if h.state is not None:
            await h.state.stop()


# ---------------------------------------------------------------- worker.updated
async def _registrar_worker(h: Harness) -> tuple[Any, Any]:
    assert h.state is not None
    reg = h.state.workers
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    # `attach` já é uma mudança observável: o canal sobe.
    link = reg.attach("worker-lan-01", _agente_mudo)
    return reg, link


async def _agente_mudo(_payload: dict[str, Any]) -> None:
    await asyncio.sleep(0)


async def test_canal_que_sobe_e_cai_vira_worker_updated_com_o_dto_do_worker(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    marca = _ultimo_id(harness)
    reg, link = await _registrar_worker(harness)

    [(evento, dados)] = _eventos(harness, "worker.updated", marca)
    worker = dados["worker"]
    assert evento["level"] == "info" and evento["instance_id"] is None
    assert evento["message"] == "worker Notebook da LAN: online"
    assert worker["id"] == "worker-lan-01" and worker["name"] == "Notebook da LAN"
    assert worker["state"] == "online" and worker["connected"] is True and worker["maintenance"] is False
    assert worker["max_slots"] == 6 and "restart" in worker["verbs"]

    # O canal cai: o estado não vira offline na hora (a rede pisca), mas o motivo vai ao painel.
    depois = _ultimo_id(harness)
    assert reg.detach("worker-lan-01", "socket fechado pelo teste", link) is True
    [(evento, dados)] = _eventos(harness, "worker.updated", depois)
    assert dados["worker"]["connected"] is False
    assert dados["worker"]["state_detail"] == "socket fechado pelo teste"
    assert evento["message"].endswith("— socket fechado pelo teste")


async def test_sem_batida_o_worker_fica_offline_e_o_evento_e_aviso(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    reg, link = await _registrar_worker(harness)
    s.db.execute("UPDATE workers SET last_seen_at=? WHERE id=?", (iso_in(-3600), "worker-lan-01"))
    marca = _ultimo_id(harness)

    assert reg.reap() == ["worker-lan-01"]

    [(evento, dados)] = _eventos(harness, "worker.updated", marca)
    assert evento["level"] == "warn", "offline é aviso, não informação"
    assert dados["worker"]["state"] == "offline" and dados["worker"]["connected"] is False
    assert "sem batida" in dados["worker"]["state_detail"]
    # Quem já está offline não reemite a cada varredura.
    reg.reap()
    assert len(_eventos(harness, "worker.updated", marca)) == 1


async def test_manutencao_ganha_do_estado_observado_no_evento(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    reg, _ = await _registrar_worker(harness)
    marca = _ultimo_id(harness)

    reg.set_maintenance("worker-lan-01", True)
    reg.set_maintenance("worker-lan-01", False)

    ligou, desligou = (d["worker"] for _, d in _eventos(harness, "worker.updated", marca))
    assert ligou["state"] == "maintenance" and ligou["observed_state"] == "online" and ligou["maintenance"] is True
    assert desligou["state"] == "online" and desligou["maintenance"] is False


async def test_batida_igual_so_gera_metrics_e_inventario_novo_gera_worker_updated(harness: Harness) -> None:
    """O contrato de #17/#143 visto de ponta a ponta: o recurso vai em `worker.metrics` (efêmero, não persiste),
    e só estado, detalhe ou inventário de aparelhos viram `worker.updated` persistido."""
    s = harness.state
    assert s is not None
    reg, link = await _registrar_worker(harness)
    fila = s.bus.subscribe()
    try:
        marca = _ultimo_id(harness)
        base = [WorkerDevice(serial="emulator-5554", avd_name="worker-01", state="online", adb_port=5555)]
        for _ in range(3):
            reg.on_heartbeat("worker-lan-01", Heartbeat(resources=WorkerResources(ram_free_mb=40000, cpu_percent=10.0),
                                                        devices=base), link)
        primeira = _eventos(harness, "worker.updated", marca)
        # A primeira batida pode mudar o que o `hello` declarou (devices/recursos); as seguintes, iguais, não.
        assert len(primeira) == 0, primeira

        depois = _ultimo_id(harness)
        reg.on_heartbeat("worker-lan-01", Heartbeat(
            resources=WorkerResources(ram_free_mb=39000),
            devices=base + [WorkerDevice(serial="emulator-5556", state="booting")]), link)
        [(_, dados)] = _eventos(harness, "worker.updated", depois)
        assert {d["serial"] for d in dados["worker"]["devices"]} == {"emulator-5554", "emulator-5556"}

        tipos = []
        while not fila.empty():
            tipos.append(fila.get_nowait())
        metrics = [e for e in tipos if e.kind == "worker.metrics"]
        assert len(metrics) == 4, "o recurso chega a CADA batida"
        assert all(e.id is None for e in metrics), "e não é persistido"
        assert not s.db.query("SELECT 1 FROM events WHERE kind='worker.metrics'")
    finally:
        s.bus.unsubscribe(fila)


async def test_remover_e_girar_credencial_tambem_publicam_o_worker(harness: Harness) -> None:
    """Toda porta que muda o worker passa por `on_change`: sem isto a tela continuaria mostrando o que foi."""
    s = harness.state
    assert s is not None
    reg, link = await _registrar_worker(harness)
    reg.detach("worker-lan-01", "fim do teste", link)
    marca = _ultimo_id(harness)

    reg.rotate_credential("worker-lan-01")
    [(_, dados)] = _eventos(harness, "worker.updated", marca)
    assert dados["worker"]["id"] == "worker-lan-01"
