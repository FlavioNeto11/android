"""Limites POR SERVIDOR e distribuição entre servidores (pedido do dono, 24/09).

O que estes testes travam:

- a tela Limites deixa de misturar o que é do parque com o que é de UMA máquina: cada servidor tem vagas, boots
  em paralelo, teto de "trabalhando ao mesmo tempo" e piso de RAM próprios, e o notebook deixa de depender de
  SSH no `worker.yaml` para mudar esses números;
- o que o dono decide chega ao agente (mensagem `limits`) e o agente aplica sem reiniciar — `None` volta ao
  valor do arquivo;
- o agendador respeita o teto de "trabalhando" de cada máquina sem deixar a outra ociosa;
- "distribuir entre servidores" escolhe os aparelhos pela carga relativa de cada máquina, prefere quem já está
  ligado e explica quando falta aparelho.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.models import InstanceState, RunCreate
from app.taskqueue.balanceamento import Candidato, Servidor, distribuir
from app.worker.executor import FilaDeBoot
from app.workers.protocol import Heartbeat, Limits, WorkerResources

from .test_rotation_worker import WORKER, _com_worker, _rodizio
from .test_workers import _hello


# ---------------------------------------------------------------------- balanceamento (puro)
def _srv(sid: str, *, cap: int = 4, teto: bool = False, trabalhando: int = 0, vagas: int = 4,
         cpu: float | None = 20.0, ram: int | None = 30_000, ok: bool = True) -> Servidor:
    return Servidor(id=sid, nome=sid, disponivel=ok, capacidade=cap, tem_teto_proprio=teto, trabalhando=trabalhando,
                    vagas_livres=vagas, cpu_percent=cpu, ram_free_mb=ram,
                    motivo_indisponivel=None if ok else f"worker “{sid}” não está conectado")


def _cands(sid: str, n: int, *, ligado: bool = True, prefixo: str | None = None) -> list[Candidato]:
    p = prefixo or sid
    return [Candidato(f"{p}-{i:02d}", sid, ligado=ligado, acordavel=not ligado, ocupado=False) for i in range(n)]


def test_espalha_pela_carga_relativa_das_maquinas() -> None:
    servidores = {"a": _srv("a", cap=8), "b": _srv("b", cap=4)}
    d = distribuir(6, _cands("a", 8) + _cands("b", 4), servidores)
    # Carga relativa: a máquina com o dobro de capacidade recebe o dobro de aparelhos.
    assert d.por_servidor() == {"a": 4, "b": 2}
    assert d.faltaram == 0 and d.faltas == []


def test_maquina_ja_ocupada_recebe_menos() -> None:
    servidores = {"a": _srv("a", cap=4, trabalhando=3), "b": _srv("b", cap=4)}
    d = distribuir(3, _cands("a", 4) + _cands("b", 4), servidores)
    assert d.por_servidor() == {"b": 3}


def test_teto_proprio_da_maquina_nao_e_ultrapassado() -> None:
    servidores = {"a": _srv("a", cap=2, teto=True, trabalhando=1), "b": _srv("b", cap=2, teto=True)}
    d = distribuir(5, _cands("a", 4) + _cands("b", 4), servidores)
    assert d.por_servidor() == {"a": 1, "b": 2}
    assert d.faltaram == 2
    assert any("teto de 2" in f for f in d.faltas)


def test_prefere_ligado_e_so_liga_quem_tem_vaga() -> None:
    servidores = {"a": _srv("a", cap=4, vagas=1)}
    cands = _cands("a", 1, ligado=True, prefixo="on") + _cands("a", 3, ligado=False, prefixo="off")
    d = distribuir(4, cands, servidores)
    assert [e.instance_id for e in d.escolhidos] == ["on-00", "off-00"]
    assert [e.precisa_ligar for e in d.escolhidos] == [False, True]
    assert d.faltaram == 2
    assert any("não têm vaga" in f for f in d.faltas)


def test_maquina_indisponivel_fica_de_fora_com_o_motivo() -> None:
    servidores = {"a": _srv("a"), "b": _srv("b", ok=False)}
    d = distribuir(4, _cands("a", 2) + _cands("b", 2), servidores)
    assert d.por_servidor() == {"a": 2}
    assert any("não está conectado" in f for f in d.faltas)


def test_empate_de_carga_vai_para_a_maquina_com_menos_cpu() -> None:
    servidores = {"a": _srv("a", cpu=80.0), "b": _srv("b", cpu=10.0)}
    d = distribuir(1, _cands("a", 2) + _cands("b", 2), servidores)
    assert d.escolhidos[0].servidor == "b"


def test_ocupado_nao_entra() -> None:
    servidores = {"a": _srv("a")}
    cands = [Candidato("x", "a", ligado=True, acordavel=False, ocupado=True)]
    d = distribuir(1, cands, servidores)
    assert d.escolhidos == [] and d.faltaram == 1
    assert d.faltas == ["1 aparelho(s) deste app já estão ocupados com outro trabalho"]


def test_acabaram_os_aparelhos_diz_quantos_ha() -> None:
    d = distribuir(5, _cands("a", 2), {"a": _srv("a")})
    assert d.faltaram == 3 and d.faltas == ["o parque só tem 2 aparelho(s) deste app"]


# ---------------------------------------------------------------------- agente: fila de boot e limites
async def test_fila_de_boot_redimensiona_sem_reiniciar() -> None:
    fila = FilaDeBoot(1)
    async with fila:
        assert fila.ocupada()
        await fila.definir(2)
        assert not fila.ocupada()
        async with fila:
            assert fila.ocupada()
    assert not fila.ocupada()


async def test_executor_aplica_limites_e_volta_ao_arquivo(tmp_path: Path) -> None:
    from app.worker.executor import WorkerExecutor
    from app.worker.settings import WorkerSettings

    ws = WorkerSettings(worker_id="w-1", name="w", work_dir=str(tmp_path), max_slots=6, boot_parallelism=1,
                        min_free_ram_mb=4096)
    ex = WorkerExecutor(ws, ws.to_config())
    efetivo = await ex.aplicar_limites(10, 2, 3000)
    assert efetivo == {"max_slots": 10, "boot_parallelism": 2, "min_free_ram_mb": 3000}
    assert (ws.max_slots, ws.min_free_ram_mb, ex._boot.limite) == (10, 3000, 2)
    # `None` = o valor do worker.yaml — que continua sendo o declarado.
    efetivo = await ex.aplicar_limites(None, None, None)
    assert efetivo == {"max_slots": 6, "boot_parallelism": 1, "min_free_ram_mb": 4096}
    assert ex.do_arquivo == {"max_slots": 6, "boot_parallelism": 1, "min_free_ram_mb": 4096}


# ---------------------------------------------------------------------- registro: decidido × declarado
async def test_decisao_do_painel_manda_na_capacidade_e_vai_para_o_agente(tmp_path: Path) -> None:
    h, reg, agente = await _com_worker(tmp_path, remotos=["android-03"], max_slots=6)
    try:
        assert reg.limites_declarados(WORKER)["max_slots"] == 6
        reg.definir_limites(WORKER, {"max_slots": 9, "max_working": 3, "min_free_ram_mb": 2500}, por="teste")
        cap = reg.capacidade(WORKER)
        assert (cap.max_slots, cap.max_working) == (9, 3)
        msg = reg.mensagem_de_limites(WORKER)
        # `max_working` não vai para o agente: quem despacha trabalho é o central.
        assert msg == Limits(max_slots=9, boot_parallelism=None, min_free_ram_mb=2500)
        assert await reg.enviar_limites(WORKER)
        assert agente.enviados[-1]["type"] == "limits" and agente.enviados[-1]["max_slots"] == 9
        # `None` volta ao declarado.
        reg.definir_limites(WORKER, {"max_slots": None})
        assert reg.capacidade(WORKER).max_slots == 6
    finally:
        await h.crash()


async def test_hello_grava_o_declarado_e_a_primeira_batida_leva_os_limites(tmp_path: Path) -> None:
    h, reg, agente = await _com_worker(tmp_path, remotos=["android-03"])
    try:
        # O mesmo worker se apresentando de novo (reconexão), agora declarando o worker.yaml inteiro.
        token_hash = h.state.db.scalar("SELECT token_hash FROM workers WHERE id=?", (WORKER,))
        reg._upsert(_hello(boot_parallelism=2, min_free_ram_mb=4096), token_hash=token_hash, enrolled=False)
        assert reg.limites_declarados(WORKER) == {"max_slots": 6, "boot_parallelism": 2, "min_free_ram_mb": 4096}
        reg.definir_limites(WORKER, {"boot_parallelism": 3})
        link = reg.attach(WORKER, agente.send)             # conexão NOVA: os limites ainda não foram
        antes = len(agente.enviados)
        reg.on_heartbeat(WORKER, Heartbeat(resources=WorkerResources(cpu_count=12, ram_free_mb=40_000,
                                                                     disk_free_gb=400.0)), link)
        await h.ticks(1)
        tipos = [m["type"] for m in agente.enviados[antes:]]
        assert tipos.count("limits") == 1
        reg.on_heartbeat(WORKER, Heartbeat(resources=WorkerResources(cpu_count=12, ram_free_mb=40_000,
                                                                     disk_free_gb=400.0)), link)
        await h.ticks(1)
        assert [m["type"] for m in agente.enviados[antes:]].count("limits") == 1   # uma vez por conexão
    finally:
        await h.crash()


# ---------------------------------------------------------------------- agendador: teto por servidor
async def test_teto_de_trabalhando_da_maquina_segura_so_ela(tmp_path: Path) -> None:
    h, reg, _agente = await _com_worker(tmp_path, remotos=["android-03", "android-04"], count=4)
    try:
        assert h.state is not None
        for iid in ("android-03", "android-04"):
            rt = h.state.devices.get(iid)
            rt.state = InstanceState.online
        reg.definir_limites(WORKER, {"max_working": 1})
        sched = h.state.scheduler
        remoto = h.state.devices.get("android-03")
        local = h.state.devices.get("android-01")
        assert sched.servidor_de(remoto) == WORKER and sched.servidor_de(local) == h.cfg.owner_id
        assert sched.servidor_lotado(remoto) is None
        sched.workers["android-04"] = None  # type: ignore[assignment]  - um aparelho do worker trabalhando
        try:
            frase = sched.servidor_lotado(remoto)
            assert frase is not None and "1 de 1" in frase
            # A outra máquina segue livre: o teto é da máquina, não do parque.
            assert sched.servidor_lotado(local) is None
        finally:
            sched.workers.pop("android-04", None)
    finally:
        await h.crash()


# ---------------------------------------------------------------------- API
async def _cliente(h: Any) -> httpx.AsyncClient:
    from app.main import create_app

    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_api_lista_e_muda_limites_do_host_e_do_worker(tmp_path: Path) -> None:
    h, reg, agente = await _com_worker(tmp_path, remotos=["android-03", "android-04"], count=4)
    try:
        assert h.state is not None
        host = h.cfg.owner_id
        async with await _cliente(h) as c:
            lista = (await c.get("/api/servers/limits")).json()
            assert [x["worker_id"] for x in lista][0] == host
            remoto = next(x for x in lista if x["worker_id"] == WORKER)
            assert remoto["declared"]["max_slots"] == 6 and remoto["decided"]["max_slots"] is None
            assert remoto["devices"] == 2

            r = await c.put(f"/api/servers/{WORKER}/limits", json={"max_slots": 8, "max_working": 2})
            assert r.status_code == 200, r.text
            assert r.json()["effective"]["max_slots"] == 8 and r.json()["effective"]["max_working"] == 2
            assert any(m["type"] == "limits" and m["max_slots"] == 8 for m in agente.enviados)

            # 29.82: o WorkerDTO traz as vagas que valem (o decidido) ao lado do declarado, e a mudança vira evento.
            dto = next(w for w in (await c.get("/api/workers")).json() if w["id"] == WORKER)
            assert (dto["max_slots"], dto["effective_max_slots"]) == (6, 8)
            do_worker = [d["worker"] for d in (json.loads(r["data"]) for r in h.state.db.query(
                "SELECT data FROM events WHERE kind='worker.updated' ORDER BY id")) if d["worker"]["id"] == WORKER]
            assert do_worker and do_worker[-1]["effective_max_slots"] == 8

            r = await c.put(f"/api/servers/{WORKER}/limits", json={"max_slots": None})
            assert r.json()["effective"]["max_slots"] == 6 and r.json()["decided"]["max_slots"] is None
            dto = next(w for w in (await c.get("/api/workers")).json() if w["id"] == WORKER)
            assert (dto["max_slots"], dto["effective_max_slots"]) == (6, 6)

            r = await c.put(f"/api/servers/{host}/limits", json={"max_slots": 5, "boot_parallelism": 3,
                                                                  "max_working": 4})
            assert r.status_code == 200, r.text
            assert h.state.settings.get().max_online_devices == 5
            assert h.state.settings.get().boot_parallelism == 3
            assert r.json()["effective"]["max_working"] == 4
            travado = await c.put(f"/api/servers/{host}/limits", json={"min_free_ram_mb": 1000})
            assert travado.status_code == 400 and travado.json()["detail"]["code"] == "locked_limit"
            assert (await c.put("/api/servers/nao-existe/limits", json={"max_slots": 2})).status_code == 404
    finally:
        await h.crash()


async def test_distribuicao_pela_api_previa_e_execucao(tmp_path: Path) -> None:
    h, _reg, _agente = await _com_worker(tmp_path, remotos=["android-03", "android-04"], count=4)
    try:
        assert h.state is not None
        _rodizio(h, max_online_devices=4)
        h.state.db.execute("UPDATE instances SET app_id=?", ("qa-messenger",))
        for iid in ("android-03", "android-04"):
            h.state.devices.get(iid).state = InstanceState.online
        async with await _cliente(h) as c:
            previa = (await c.post("/api/runs/distribution", json={"count": 4, "app_id": "qa-messenger"})).json()
            assert previa["missing"] == 0 and len(previa["picks"]) == 4
            # Duas máquinas com aparelho livre: a distribuição usa as duas.
            assert len(previa["per_server"]) == 2
            vazia = (await c.post("/api/runs/distribution", json={"count": 2, "app_id": "outro-app"})).json()
            assert vazia["picks"] == [] and "nenhum aparelho do parque está vinculado a este app" in vazia["reasons"]

        demais = RunCreate(command="abra o app", idempotency_key="dist-demais-1",
                           distribute={"count": 9, "app_id": "qa-messenger"})  # type: ignore[arg-type]
        from app.taskqueue.service import RunError

        with pytest.raises(RunError) as exc:
            h.state.runs.create(demais)
        assert exc.value.code == "distribution_short"
        ok = h.state.runs.create(RunCreate(command="abra o app", idempotency_key="dist-ok-1",
                                           distribute={"count": 2, "app_id": "qa-messenger"}))  # type: ignore[arg-type]
        assert ok.instances_requested == 2
    finally:
        await h.crash()


def test_distribute_nao_combina_com_lista_de_aparelhos() -> None:
    with pytest.raises(ValueError):
        RunCreate(command="abc", idempotency_key="12345678", instance_ids=["android-01"],
                  distribute={"count": 1, "app_id": "x"})  # type: ignore[arg-type]


async def test_app_com_conta_so_distribui_para_aparelho_com_perfil_ativo(tmp_path: Path) -> None:
    """Aparelho vinculado ao Instagram mas sem ninguém logado não entra na distribuição do Instagram."""
    h, _reg, _agente = await _com_worker(tmp_path, remotos=["android-03"], count=4)
    try:
        assert h.state is not None
        db = h.state.db
        ig = db.scalar("SELECT id FROM apps WHERE package='com.instagram.android'")
        if ig is None:
            pytest.skip("catálogo de teste sem o app do Instagram")
        db.execute("UPDATE instances SET app_id=?", (ig,))
        for rt in h.state.devices.devices.values():
            rt.state = InstanceState.online
        assert h.state.scheduler.candidatos_do_app(ig) == []
        # Vincula um perfil ativo a um aparelho: só ele passa a ser candidato.
        from app.util import now_iso
        agora = now_iso()
        db.execute("INSERT INTO instagram_profiles(id, username, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                   ("ig-teste", "teste", "active", agora, agora))
        db.execute("INSERT INTO device_profile_bindings(profile_id, instance_id, active, bound_at) VALUES (?,?,1,?)",
                   ("ig-teste", "android-02", agora))
        assert [c.instance_id for c in h.state.scheduler.candidatos_do_app(ig)] == ["android-02"]
    finally:
        await h.crash()


def test_teto_de_boots_e_o_mesmo_no_painel_na_configuracao_e_na_mensagem() -> None:
    """Backlog B3 (24/09): o painel recusava acima de 10 e a mensagem `limits` prometia até 16. Quem produz a
    mensagem é o central, a partir do que o painel aceitou; os três tetos têm de andar juntos."""
    from app.config import LimitsCfg
    from app.models import ServerLimitsPatch
    from app.workers.protocol import Limits

    def teto(modelo, campo: str) -> int:
        return next(m.le for m in modelo.model_fields[campo].metadata if getattr(m, "le", None) is not None)

    assert teto(ServerLimitsPatch, "boot_parallelism") == teto(Limits, "boot_parallelism") \
        == teto(LimitsCfg, "boot_parallelism") == 10
