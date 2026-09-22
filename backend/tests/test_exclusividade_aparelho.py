"""Um aparelho, uma operação (achados #4, #28, #31, #57, #25, #125).

Quatro camadas, porque uma só não segura:

1. **Pré-voo do central**: comando de ciclo de vida para aparelho que já tem comando aberto é recusado com 409
   `device_busy`, antes de tocar em qualquer coisa.
2. **Agente**: trava por `instance_id` e cerca — o RECURSO recusa a ordem de quem perdeu a autorização, que é
   para isso que a cerca existe. Antes ela só voltava para quem a emitiu.
3. **Banco**: índice único parcial em etapa ativa por aparelho, porque a contagem antes do UPDATE não segura
   dois backends no PostgreSQL.
4. **Canal**: socket velho que termina depois da reconexão não derruba o link novo.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.db import Database, INTEGRITY_ERRORS
from app.main import create_app
from app.models import CommandState
from app.workers.protocol import Dispatch, Heartbeat, Hello, Result, WorkerDevice, WorkerResources
from app.workers.registry import WorkerRegistry
from .conftest import Harness, make_config


def _hello(**kw: Any) -> Hello:
    base = dict(worker_id="worker-lan-01", name="Notebook da LAN", agent_version="0.1.0", os="windows",
                max_slots=6, verbs=["start", "stop", "hibernate", "reset"],
                devices=[WorkerDevice(serial="emulator-5554", avd_name="worker-01", state="online", adb_port=5555)],
                resources=WorkerResources(cpu_count=12, ram_total_mb=65273, ram_free_mb=46367))
    return Hello(**{**base, **kw})


class AgenteFalso:
    def __init__(self) -> None:
        self.enviados: list[dict[str, Any]] = []
        self.fechado = False

    async def send(self, payload: dict[str, Any]) -> None:
        self.enviados.append(payload)

    async def fechar(self) -> None:
        self.fechado = True


# ---------------------------------------------------------------- 1) pré-voo do central
async def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_segundo_comando_no_mesmo_aparelho_e_recusado_com_device_busy(harness: Harness) -> None:
    """O aceite 9, do lado do central: dois comandos simultâneos no mesmo aparelho — o segundo recusado."""
    s = harness.state
    assert s is not None
    s.commands.create(command_id="c-em-voo", instance_id="android-01", verb="start",
                      idempotency_key="k-em-voo")
    s.commands.transition("c-em-voo", CommandState.dispatched)
    async with await _cliente(harness) as c:
        r = await c.post("/api/instances/android-01/actions/stop", json={"idempotency_key": "segundo-0001"})
        assert r.status_code == 409, r.text
        detalhe = r.json()["detail"]
        assert detalhe["code"] == "device_busy"
        assert "c-em-voo" in detalhe["message"] and "start" in detalhe["message"]
        # A recusa fica no histórico com o motivo — e prova que NADA foi executado.
        recusado = s.commands.get(detalhe["command_id"])
        assert recusado["state"] == CommandState.rejected.value
        assert recusado["dispatched_at"] is None

        # Terminado o primeiro, o aparelho volta a aceitar comando.
        s.commands.transition("c-em-voo", CommandState.running)
        s.commands.transition("c-em-voo", CommandState.succeeded)
        r = await c.post("/api/instances/android-01/actions/stop", json={"idempotency_key": "segundo-0002"})
        assert r.status_code == 202, r.text


async def test_lote_recusa_so_o_aparelho_ocupado(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    s.commands.create(command_id="c-ocupa-02", instance_id="android-02", verb="reset",
                      idempotency_key="k-ocupa-02")
    s.commands.transition("c-ocupa-02", CommandState.dispatched)
    async with await _cliente(harness) as c:
        r = await c.post("/api/instances/bulk", json={"ids": ["android-01", "android-02"], "action": "stop",
                                                      "params": {"idempotency_key": "lote-ocupado"}})
        assert r.status_code == 202, r.text
        corpo = r.json()
        assert corpo["accepted"] == ["android-01"]
        assert [x["id"] for x in corpo["rejected"]] == ["android-02"]
        assert corpo["rejected"][0]["code"] == "device_busy"


# ---------------------------------------------------------------- 2) o agente
def _agente(tmp_path: Path):
    from app.worker.agent import Agent
    from app.worker.settings import DeviceSpec, WorkerSettings

    settings = WorkerSettings(worker_id="worker-lan-01", name="Notebook", work_dir=str(tmp_path / "farm"),
                              devices=[DeviceSpec(instance_id="android-03", avd_name="worker-01",
                                                  console_port=5554)])
    return Agent(settings)


class WsFalso:
    def __init__(self) -> None:
        self.enviados: list[dict[str, Any]] = []

    async def send(self, bruto: str) -> None:
        import json
        self.enviados.append(json.loads(bruto))

    def resultados(self) -> list[dict[str, Any]]:
        return [p for p in self.enviados if p.get("type") == "result"]


def _despacho(command_id: str, fence: int, verb: str = "stop") -> Dispatch:
    return Dispatch(command_id=command_id, fence=fence, verb=verb, instance_id="android-03",
                    serial="emulator-5554")


async def test_agente_recusa_segundo_comando_no_mesmo_aparelho(tmp_path: Path) -> None:
    """O comentário dizia "um comando por aparelho"; a trava não existia — cada despacho virava uma tarefa e os
    dois se intercalavam no mesmo AVD."""
    agente = _agente(tmp_path)
    ws = WsFalso()
    agente._ws = ws
    solta = asyncio.Event()

    async def run_lento(*_a: Any, **_k: Any) -> dict[str, Any]:
        await solta.wait()
        return {"ok": True}

    agente.executor.run = run_lento                                    # type: ignore[assignment]
    await agente._despachar(_despacho("c-1", 1))
    await agente._despachar(_despacho("c-2", 2, verb="reset"))

    recusa = next(p for p in ws.resultados() if p["command_id"] == "c-2")
    assert recusa["outcome"] == "failed"
    assert "já está executando o comando c-1" in recusa["reason"]
    assert recusa["fence"] == 2
    assert "c-1" in agente._tarefas and "c-2" not in agente._tarefas

    solta.set()
    await asyncio.gather(*agente._tarefas.values())
    assert agente._ocupados == {}                                      # liberou o aparelho ao terminar


async def test_agente_recusa_cerca_menor_que_a_ultima_executada(tmp_path: Path) -> None:
    """A cerca só protegia quem a emitiu: `registry` a comparava com a que ELE mesmo guardara para AQUELE
    comando. Quem tem de recusar é o RECURSO — o agente."""
    agente = _agente(tmp_path)
    ws = WsFalso()
    agente._ws = ws
    agente.executor.run = lambda *_a, **_k: _pronto()                  # type: ignore[assignment]

    await agente._despachar(_despacho("c-novo", 7))
    await asyncio.gather(*list(agente._tarefas.values()))
    assert agente._diario.cerca("android-03") == 7

    await agente._despachar(_despacho("c-velho", 3))
    recusa = next(p for p in ws.resultados() if p["command_id"] == "c-velho")
    assert recusa["outcome"] == "failed"
    assert "cerca 3 é anterior" in recusa["reason"] and "nada foi executado" in recusa["reason"]
    # E a cerca sobrevive ao reinício do agente: o diário está em disco.
    from app.worker.diario import DiarioDoAgente
    assert DiarioDoAgente(agente.settings.work_dir).cerca("android-03") == 7


async def _pronto() -> dict[str, Any]:
    return {"ok": True}


async def test_agente_ignora_reentrega_do_mesmo_comando(tmp_path: Path) -> None:
    """Reconexão pode reentregar um despacho. Executar de novo seria um segundo `reset` no mesmo aparelho."""
    agente = _agente(tmp_path)
    agente._ws = WsFalso()
    solta = asyncio.Event()

    async def run_lento(*_a: Any, **_k: Any) -> dict[str, Any]:
        await solta.wait()
        return {}

    agente.executor.run = run_lento                                    # type: ignore[assignment]
    await agente._despachar(_despacho("c-1", 1))
    await agente._despachar(_despacho("c-1", 1))                       # a MESMA ordem, de novo
    assert len(agente._tarefas) == 1
    solta.set()
    await asyncio.gather(*agente._tarefas.values())


# ---------------------------------------------------------------- 3) o banco
def _repo(tmp_path: Path):
    from app.events import EventBus
    from app.taskqueue.repository import Repository

    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    return Repository(db, EventBus(db), cfg.evidence_dir, owner_id="servidor-central"), db


def _duas_etapas(db: Database) -> tuple[str, str]:
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-1','k1','responda','execute','running',1,'[]','2026-09-21T10:00:00Z')")
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters)"
               " VALUES ('run-1:android-01','run-1','android-01','running',1,'{}')")
    ids = []
    for seq, key in ((1, "send_1"), (2, "send_2")):
        sid = f"run-1:android-01:v1:{key}"
        db.execute(
            "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
            " depends_on, side_effect, commit_guard, postcondition, timeout_s, max_attempts, status)"
            " VALUES (?, 'run-1','run-1:android-01','android-01',1,?,?,'Enviar','enviar','[]',1,'[]',"
            "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready')",
            (sid, seq, key))
        ids.append(sid)
    return ids[0], ids[1]


def test_banco_recusa_duas_etapas_ativas_no_mesmo_aparelho(tmp_path: Path) -> None:
    """A promessa de "dono único do executor" era só uma leitura: `SELECT COUNT(*)` seguido de `UPDATE`, sem
    índice nenhum. Agora é o banco que recusa — vale para qualquer backend, no SQLite e no PostgreSQL."""
    repo, db = _repo(tmp_path)
    a, b = _duas_etapas(db)
    assert repo.claim_step(a) is not None
    with pytest.raises(INTEGRITY_ERRORS):
        db.execute("UPDATE steps SET status='running' WHERE id=?", (b,))
    with pytest.raises(INTEGRITY_ERRORS):
        db.execute("UPDATE steps SET status='verifying' WHERE id=?", (b,))
    # Estados não-ativos continuam livres: um aparelho tem dezenas de etapas prontas e concluídas.
    db.execute("UPDATE steps SET status='succeeded' WHERE id=?", (b,))


def test_corrida_perdida_no_claim_nao_estoura_erro(tmp_path: Path, monkeypatch: Any) -> None:
    """Dois backends passam pela MESMA contagem (READ COMMITTED). Quem perde a corrida tem de simplesmente não
    assumir — não abortar com erro de integridade."""
    repo, db = _repo(tmp_path)
    a, b = _duas_etapas(db)
    assert repo.claim_step(a) is not None

    original = db.scalar

    def cego(sql: str, params: tuple | dict = ()) -> Any:
        if "status IN ('running','verifying')" in sql:
            return 0            # a contagem não enxerga o que o outro backend acabou de gravar
        return original(sql, params)

    monkeypatch.setattr(db, "scalar", cego)
    assert repo.claim_step(b) is None
    assert db.one("SELECT status FROM steps WHERE id=?", (b,))["status"] == "ready"


# ---------------------------------------------------------------- 4) o canal
def _registro(tmp_path: Path) -> WorkerRegistry:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    reg = WorkerRegistry(db)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    return reg


async def test_socket_velho_que_termina_depois_nao_derruba_a_conexao_nova(tmp_path: Path) -> None:
    """O defeito: o `finally` de TODA conexão fazia `live.pop(worker_id)` sem conferir de quem era o link. Se a
    conexão nova entrasse antes de o handler da velha terminar, o link NOVO era apagado — o agente seguia batendo
    e o painel mostrava o worker online, enquanto todo despacho respondia `worker_offline`."""
    reg = _registro(tmp_path)
    velho, novo = AgenteFalso(), AgenteFalso()
    link_velho = reg.attach("worker-lan-01", velho.send, velho.fechar)
    link_novo = reg.attach("worker-lan-01", novo.send, novo.fechar)
    await asyncio.sleep(0)
    assert velho.fechado is True, "a conexão anterior tem de ser fechada, não só substituída"

    # O handler do socket velho só agora chega ao `finally`.
    assert reg.detach("worker-lan-01", "conexão encerrada", link_velho) is False
    assert reg.live.get("worker-lan-01") is link_novo

    # E o despacho continua funcionando pelo canal novo.
    async def responde() -> None:
        await asyncio.sleep(0.02)
        reg.on_result("worker-lan-01", Result(command_id="c-9", outcome="succeeded", fence=1), fence=1)

    tarefa = asyncio.create_task(responde())
    r = await reg.dispatch("worker-lan-01", _despacho("c-9", 1))
    await tarefa
    assert r.outcome == "succeeded"
    assert [p["command_id"] for p in novo.enviados if p.get("type") == "dispatch"] == ["c-9"]

    # E o dono de verdade ainda consegue se desligar.
    assert reg.detach("worker-lan-01", "conexão encerrada", link_novo) is True
    assert "worker-lan-01" not in reg.live


async def test_batida_de_socket_orfao_e_ignorada(tmp_path: Path) -> None:
    """Senão o worker duplicado manteria `state='online'` por um canal que o central já não usa para despachar."""
    reg = _registro(tmp_path)
    link_velho = reg.attach("worker-lan-01", AgenteFalso().send)
    reg.attach("worker-lan-01", AgenteFalso().send)
    reg.on_heartbeat("worker-lan-01", Heartbeat(clock_offset_s=-97.0), link_velho)
    assert reg.dtos()[0].state == "online"                  # a batida órfã não degradou nada
    assert reg.dtos()[0].state_detail is None


async def test_resultado_sem_cerca_e_recusado(tmp_path: Path) -> None:
    """`Result` não tinha campo `fence`: `registry` só recusava `if fence is not None and fence != cerca`, então
    quem omitisse a cerca passava direto e a proteção era opcional."""
    reg = _registro(tmp_path)
    reg.attach("worker-lan-01", AgenteFalso().send)
    msg = _despacho("c-sem-cerca", 4)
    msg = msg.model_copy(update={"timeout_s": 0.3})

    async def responde_sem_cerca() -> None:
        await asyncio.sleep(0.02)
        assert reg.on_result("worker-lan-01", Result(command_id="c-sem-cerca", outcome="succeeded")) is False

    tarefa = asyncio.create_task(responde_sem_cerca())
    r = await reg.dispatch("worker-lan-01", msg)
    await tarefa
    assert r.outcome == "uncertain"                         # o resultado sem cerca foi descartado


async def test_worker_sem_batida_perde_o_canal_e_nao_segura_o_despacho(tmp_path: Path) -> None:
    """Socket meio-aberto (notebook suspenso, troca de IP): o link ficava em `live` e o despacho esperava o prazo
    inteiro por alguém que já não estava lá."""
    reg = _registro(tmp_path)
    agente = AgenteFalso()
    reg.attach("worker-lan-01", agente.send, agente.fechar)
    reg.db.execute("UPDATE workers SET last_seen_at='2020-01-01T00:00:00.000Z' WHERE id=?", ("worker-lan-01",))
    assert reg.reap() == ["worker-lan-01"]
    assert "worker-lan-01" not in reg.live
    await asyncio.sleep(0)
    assert agente.fechado is True
