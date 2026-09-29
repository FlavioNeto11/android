"""Provisionamento de aparelho pela plataforma, no servidor local (segunda evolução, onda D; migração 050).

O que estes testes travam:

- `POST /api/instances` cria a INSTÂNCIA agora (linha `origin='dynamic'`, runtime vivo, evento) e o AVD pelo
  comando `create` de sempre, que fecha `succeeded` no falso; a sobreposição Android pedida (`system_image`,
  `ram_mb`) é a que chega à criação do AVD, e só naquela instância;
- ids e portas nunca colidem com os do YAML, e a instância sobrevive a um reinício do backend (novo `AppState`
  no mesmo banco);
- a mesma `idempotency_key` devolve a mesma instância e o mesmo comando, sem linha nova;
- recusas explicadas, ANTES de qualquer efeito: teto de aparelhos do servidor, disco insuficiente ou desconhecido,
  worker remoto (o protocolo do agente não cria aparelho), corpo inválido;
- `start: true` só liga DEPOIS de o `create` fechar `succeeded`, pelo mesmo caminho do rodízio;
- o teto de APARELHOS por servidor (`worker_limits.max_devices`) entra e sai pela tela Limites como os outros, sem
  ir para o agente (o esquema do fio está congelado, ADR-031).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.commands.states import COMMAND_OPEN
from app.config import AndroidCfg
from app.main import create_app
from app.metricas import metricas
from app.models import CommandState, InstanceState
from app.workers.protocol import Heartbeat, Limits, WorkerResources

from .conftest import Harness
from .test_inventario_do_parque import _inscrever
from .test_rotation_worker import WORKER, _com_worker

IMAGEM = "system-images;android-33;google_apis;x86_64"
ABERTOS = {e.value for e in COMMAND_OPEN}


async def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _avd_falso(h: Harness) -> list[AndroidCfg]:
    """`avdmanager` não existe na suíte: criar o AVD vira escrever os dois arquivos que `AvdManager.exists` confere,
    e registrar a configuração Android recebida — é assim que se prova que a sobreposição chegou à criação."""
    assert h.state is not None
    criados: list[AndroidCfg] = []
    home = h.cfg.avd_home

    def create(name: str, android: AndroidCfg) -> None:
        criados.append(android)
        (home / f"{name}.avd").mkdir(parents=True, exist_ok=True)
        (home / f"{name}.avd" / "config.ini").write_text(
            f"image.sysdir.1={android.system_image.replace(';', '/')}/\nhw.ramSize={android.ram_efetiva()}\n",
            encoding="utf-8")
        (home / f"{name}.ini").write_text(f"path={home / (name + '.avd')}\n", encoding="utf-8")

    h.state.devices.avd.create = create  # type: ignore[method-assign]
    return criados


def _batida_do_host(h: Harness, *, disco_gb: float | None) -> None:
    """A batida do worker LOCAL é de onde a rota lê o disco livre: a última medição, nunca uma leitura ad hoc."""
    assert h.state is not None
    h.state.workers.on_heartbeat(h.cfg.owner_id, Heartbeat(resources=WorkerResources(
        cpu_count=12, ram_total_mb=65273, ram_free_mb=46367, disk_free_gb=disco_gb)), h.state.local_worker.link)


def _comandos(h: Harness, instance_id: str, verbo: str) -> list[Any]:
    assert h.state is not None
    return h.state.db.query("SELECT * FROM commands WHERE instance_id=? AND verb=? ORDER BY created_at",
                            (instance_id, verbo))


# ---------------------------------------------------------------------- POST /api/instances
async def test_provisionar_cria_instancia_dinamica_fecha_o_create_e_sobrevive_ao_reinicio(tmp_path: Path) -> None:
    metricas.limpar()
    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        s = h.state
        assert s is not None
        host = h.cfg.owner_id
        criados = _avd_falso(h)
        _batida_do_host(h, disco_gb=400.0)
        async with await _cliente(h) as c:
            r = await c.post("/api/instances", json={"system_image": IMAGEM, "ram_mb": 3072, "app_id": "qa-messenger",
                                                     "idempotency_key": "prov-0001"})
            assert r.status_code == 202, r.text
            corpo = r.json()
            iid, cid = corpo["instance_id"], corpo["command_id"]
            assert iid == "android-04" and iid not in s.cfg.instance_ids(), "a instância nova não está no YAML"
            assert corpo["instance"]["origin"] == "dynamic" and corpo["instance"]["kind"] == "emulator"
            assert corpo["instance"]["worker_id"] == host and corpo["start"] == "not_requested"
            assert corpo["deduplicated"] is False and corpo["command_state"] in ("created", "dispatched")
            linha = s.db.one("SELECT * FROM instances WHERE id=?", (iid,))
            assert linha["origin"] == "dynamic" and linha["hosted_by"] == host and linha["worker_id"] == host
            assert linha["app_id"] == "qa-messenger" and linha["retired_at"] is None
            assert json.loads(linha["android_overrides"]) == {"system_image": IMAGEM, "ram_mb": 3072}
            # Portas ALÉM das do YAML (três instâncias em 5640/5642/5644): idx 4 → 5646; nada colide.
            assert linha["idx"] == 4 and linha["console_port"] == 5646
            portas = [x["console_port"] for x in s.db.query("SELECT console_port FROM instances")]
            assert len(portas) == len(set(portas)) == 4

            # O `create` fecha `succeeded` no falso, e o AVD foi criado COM a sobreposição pedida.
            await h.wait(lambda: s.commands.get(cid)["state"] not in ABERTOS, what="o create fechar")
            assert s.commands.get(cid)["state"] == CommandState.succeeded.value
            assert [a.system_image for a in criados] == [IMAGEM] and criados[0].ram_mb == 3072
            rt = s.devices.get(iid)
            assert rt.state == InstanceState.stopped and s.devices.avd.exists(rt.avd_name)
            assert s.devices.android_de(rt).system_image == IMAGEM
            # A sobreposição é POR instância: a do YAML segue no padrão.
            assert s.devices.android_de(s.devices.get("android-01")).system_image == h.cfg.file.android.system_image
            assert any(i["id"] == iid for i in (await c.get("/api/instances")).json())

            # Idempotência: a mesma chave devolve a mesma instância e o mesmo comando; nenhuma linha nova.
            r2 = await c.post("/api/instances", json={"system_image": IMAGEM, "idempotency_key": "prov-0001"})
            assert r2.status_code == 202 and r2.json()["deduplicated"] is True
            assert (r2.json()["instance_id"], r2.json()["command_id"]) == (iid, cid)
            assert s.db.scalar("SELECT COUNT(*) FROM instances WHERE origin='dynamic'") == 1
            assert metricas.valor("provisionamento.pedido", resultado="aceito") == 1

        # Reinício: um novo `AppState` no mesmo banco reencontra a instância, com a sobreposição, sem colidir.
        await h.crash()
        await h.boot()
        assert h.state is not None
        revivido = h.state.devices.devices.get(iid)
        assert revivido is not None, "instância provisionada tem de voltar no arranque"
        assert revivido.origin == "dynamic" and revivido.console_port == 5646 and not revivido.external
        assert revivido.android_overrides == {"system_image": IMAGEM, "ram_mb": 3072}
        assert h.state.devices.android_de(revivido).system_image == IMAGEM
        assert set(h.state.devices.devices) == {"android-01", "android-02", "android-03", iid}
        # O próximo provisionamento não reutiliza id nem idx.
        assert h.state.devices._proximo_id_dinamico() == "android-05"
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_teto_de_aparelhos_do_servidor_recusa_antes_de_criar(tmp_path: Path) -> None:
    metricas.limpar()
    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        s = h.state
        assert s is not None
        host = h.cfg.owner_id
        _batida_do_host(h, disco_gb=400.0)
        async with await _cliente(h) as c:
            assert (await c.put(f"/api/servers/{host}/limits", json={"max_devices": 3})).status_code == 200
            r = await c.post("/api/instances", json={"idempotency_key": "prov-teto-01"})
            assert r.status_code == 409, r.text
            detalhe = r.json()["detail"]
            assert detalhe["code"] == "teto_de_aparelhos" and (detalhe["devices"], detalhe["max_devices"]) == (3, 3)
            assert s.db.scalar("SELECT COUNT(*) FROM instances") == 3
            assert _comandos(h, "android-04", "create") == []
            assert metricas.valor("provisionamento.pedido", resultado="recusado", motivo="teto_de_aparelhos") == 1

            # Sobe o teto em um: cabe exatamente mais um (aqui sem `create`, só a instância: 201, sem comando).
            assert (await c.put(f"/api/servers/{host}/limits", json={"max_devices": 4})).status_code == 200
            r = await c.post("/api/instances", json={"create": False})
            assert r.status_code == 201, r.text
            assert r.json()["command_id"] is None and r.json()["instance_id"] == "android-04"
            assert s.devices.get("android-04").state == InstanceState.absent
            assert _comandos(h, "android-04", "create") == []
            r = await c.post("/api/instances", json={"create": False})
            assert r.status_code == 409 and r.json()["detail"]["code"] == "teto_de_aparelhos"
            # Sem teto (`null`), volta a aceitar.
            assert (await c.put(f"/api/servers/{host}/limits", json={"max_devices": None})).status_code == 200
            assert (await c.post("/api/instances", json={"create": False})).status_code == 201
    finally:
        await h.state.stop()


async def test_disco_insuficiente_ou_desconhecido_recusa(tmp_path: Path) -> None:
    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        s = h.state
        assert s is not None
        assert h.cfg.file.provisioning.min_free_disk_gb == 10
        async with await _cliente(h) as c:
            _batida_do_host(h, disco_gb=3.5)
            r = await c.post("/api/instances", json={})
            assert r.status_code == 409, r.text
            detalhe = r.json()["detail"]
            assert detalhe["code"] == "disco_insuficiente"
            assert (detalhe["disk_free_gb"], detalhe["min_free_disk_gb"]) == (3.5, 10.0)
            _batida_do_host(h, disco_gb=None)
            r = await c.post("/api/instances", json={})
            assert r.status_code == 409 and r.json()["detail"]["code"] == "disco_desconhecido"
            assert s.db.scalar("SELECT COUNT(*) FROM instances") == 3
            _batida_do_host(h, disco_gb=10.0)
            assert (await c.post("/api/instances", json={"create": False})).status_code == 201
    finally:
        await h.state.stop()


async def test_worker_remoto_e_corpo_invalido_sao_recusados_com_o_motivo(tmp_path: Path) -> None:
    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        s = h.state
        assert s is not None
        _inscrever(h)
        _batida_do_host(h, disco_gb=400.0)
        async with await _cliente(h) as c:
            r = await c.post("/api/instances", json={"worker_id": WORKER})
            assert r.status_code == 409, r.text
            assert r.json()["detail"]["code"] == "provisionamento_remoto_indisponivel"
            assert "worker.yaml" in r.json()["detail"]["message"]
            r = await c.post("/api/instances", json={"worker_id": "nao-existe"})
            assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"
            # Corpo: imagem fora do formato do SDK, RAM fora da faixa, chave desconhecida → 422 (pydantic).
            for corpo in ({"system_image": "android-33"}, {"ram_mb": 100}, {"window": True}, {"profile_id": "p1"}):
                assert (await c.post("/api/instances", json=corpo)).status_code == 422, corpo
            r = await c.post("/api/instances", json={"create": False, "idempotency_key": "prov-sem-create"})
            assert r.status_code == 400 and r.json()["detail"]["code"] == "idempotency_key_sem_comando"
            r = await c.post("/api/instances", json={"create": False, "start": True})
            assert r.status_code == 400 and r.json()["detail"]["code"] == "start_sem_create"
            r = await c.post("/api/instances", json={"app_id": "nao-cadastrado"})
            assert r.status_code == 400 and r.json()["detail"]["code"] == "unknown_app"
            # `worker_id` explícito igual ao hospedeiro é o mesmo que nulo.
            r = await c.post("/api/instances", json={"worker_id": h.cfg.owner_id, "create": False})
            assert r.status_code == 201, r.text
            assert s.db.scalar("SELECT COUNT(*) FROM instances") == 4
        # A regra da sobreposição é a do YAML: só chaves de `AndroidCfg`, com tipo e faixa válidos.
        with pytest.raises(ValueError, match="desconhecida"):
            s.devices.conferir_sobreposicao_android({"sytem_image": IMAGEM})
        with pytest.raises(ValueError, match="inválida"):
            s.devices.conferir_sobreposicao_android({"cores": "muitos"})
        assert s.devices.conferir_sobreposicao_android({"ram_mb": None, "window": True}) == {"window": True}
    finally:
        await h.state.stop()


async def test_start_so_e_pedido_depois_de_o_create_fechar_succeeded(tmp_path: Path) -> None:
    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        s = h.state
        assert s is not None
        _avd_falso(h)
        _batida_do_host(h, disco_gb=400.0)
        async with await _cliente(h) as c:
            r = await c.post("/api/instances", json={"start": True, "idempotency_key": "prov-start-01"})
            assert r.status_code == 202, r.text
            iid, cid = r.json()["instance_id"], r.json()["command_id"]
            assert r.json()["start"] == "after_create"
            # Nada de `start` antes de o `create` fechar: os dois disputariam o aparelho.
            assert _comandos(h, iid, "start") == []
            await h.wait(lambda: s.commands.get(cid)["state"] not in ABERTOS, what="o create fechar")
            assert s.commands.get(cid)["state"] == CommandState.succeeded.value
            await h.wait(lambda: any(x["state"] == CommandState.succeeded.value for x in _comandos(h, iid, "start")),
                         what="o start encadeado fechar")
            partida = _comandos(h, iid, "start")
            assert len(partida) == 1 and partida[0]["created_at"] >= s.commands.get(cid)["created_at"]
            assert s.devices.get(iid).state == InstanceState.online
            assert s.devices.get(iid).pid is not None
    finally:
        await h.state.stop()


# ---------------------------------------------------------------------- DELETE /api/instances/{id}
async def _provisionada(h: Harness, c: httpx.AsyncClient, *, chave: str) -> tuple[str, str]:
    """Provisiona com `create` e espera o AVD (falso) existir. Devolve `(instance_id, command_id)`."""
    assert h.state is not None
    r = await c.post("/api/instances", json={"idempotency_key": chave})
    assert r.status_code == 202, r.text
    iid, cid = r.json()["instance_id"], r.json()["command_id"]
    s = h.state
    await h.wait(lambda: s.commands.get(cid)["state"] not in ABERTOS, what="o create fechar")
    assert s.commands.get(cid)["state"] == CommandState.succeeded.value
    return iid, cid


async def test_aposentar_apaga_o_avd_some_das_listas_e_nao_volta_no_reinicio(tmp_path: Path) -> None:
    metricas.limpar()
    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        s = h.state
        assert s is not None
        _avd_falso(h)
        _batida_do_host(h, disco_gb=400.0)
        async with await _cliente(h) as c:
            iid, _ = await _provisionada(h, c, chave="prov-apos-01")
            rt = s.devices.get(iid)
            pasta, ini = h.cfg.avd_home / f"{iid}.avd", h.cfg.avd_home / f"{iid}.ini"
            assert pasta.exists() and ini.exists()
            s.db.execute("UPDATE instances SET app_id='qa-messenger' WHERE id=?", (iid,))

            r = await c.delete(f"/api/instances/{iid}")
            assert r.status_code == 200, r.text
            assert r.json()["instance_id"] == iid and r.json()["avd_removed"] is True
            retired_at = r.json()["retired_at"]
            # O AVD sumiu do disco; a linha ficou, aposentada e sem app; o aparelho saiu do parque vivo.
            assert not pasta.exists() and not ini.exists()
            linha = s.db.one("SELECT * FROM instances WHERE id=?", (iid,))
            assert linha["retired_at"] == retired_at and linha["app_id"] is None and linha["origin"] == "dynamic"
            assert iid not in s.devices.devices
            assert all(i["id"] != iid for i in (await c.get("/api/instances")).json())
            assert (await c.get(f"/api/instances/{iid}/packages")).status_code == 404
            limites = next(x for x in (await c.get("/api/servers/limits")).json() if x["is_host"])
            assert limites["devices"] == 3
            assert (await c.delete(f"/api/instances/{iid}")).status_code == 404
            assert rt.executor._pool._shutdown  # noqa: SLF001 - a trilha do aparelho aposentado não fica viva
            assert metricas.valor("provisionamento.aposentadoria", resultado="aceita") == 1
            assert any(e["kind"] == "instance.retired" for e in s.db.query(
                "SELECT kind FROM events WHERE instance_id=?", (iid,)))

            # Id e portas NÃO são reaproveitados: a próxima provisionada é a seguinte, com portas novas.
            r = await c.post("/api/instances", json={"create": False})
            assert r.status_code == 201 and r.json()["instance_id"] == "android-05"
            assert s.devices.get("android-05").console_port == 5648
            # A chave do aparelho aposentado não serve para outro: o `create` novo seria deduplicado para o antigo.
            r = await c.post("/api/instances", json={"idempotency_key": "prov-apos-01"})
            assert r.status_code == 409 and r.json()["detail"]["code"] == "chave_ja_usada"
            assert r.json()["detail"]["instance_id"] == iid

        # Reinício: a aposentada não volta; a viva, sim.
        await h.crash()
        await h.boot()
        assert h.state is not None
        assert iid not in h.state.devices.devices and "android-05" in h.state.devices.devices
        assert h.state.db.scalar("SELECT COUNT(*) FROM instances") == 5
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_aposentar_recusa_yaml_ligado_objetivo_vinculo_e_comando_em_voo(tmp_path: Path) -> None:
    metricas.limpar()
    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        s = h.state
        assert s is not None
        _avd_falso(h)
        _batida_do_host(h, disco_gb=400.0)
        agora = "2026-09-27T12:00:00Z"
        async with await _cliente(h) as c:
            r = await c.delete("/api/instances/android-01")
            assert r.status_code == 409 and r.json()["detail"]["code"] == "instancia_da_configuracao"
            assert "android-01" in s.devices.devices

            # Adotado de um worker também é `dynamic`, mas o AVD dele vive na outra máquina: fica para o remoto.
            _inscrever(h)
            adotado = s.devices.adotar_aparelho(WORKER, serial="emulator-5554", adb_port=5555, avd_name="worker-01")
            r = await c.delete(f"/api/instances/{adotado.id}")
            assert r.status_code == 409 and r.json()["detail"]["code"] == "aparelho_de_worker"
            assert adotado.id in s.devices.devices
            assert s.db.scalar("SELECT retired_at FROM instances WHERE id=?", (adotado.id,)) is None
            assert s.devices.mapa_do_tunel(WORKER) == f"{adotado.tunnel_port}:5555"

            iid, _ = await _provisionada(h, c, chave="prov-apos-02")
            rt = s.devices.get(iid)

            # Ligado: desligue antes.
            rt.state = InstanceState.online
            r = await c.delete(f"/api/instances/{iid}")
            assert r.status_code == 409 and r.json()["detail"]["code"] == "aparelho_ligado"
            rt.state = InstanceState.stopped

            # Objetivo aberto (K-029: só os tipos do esquema).
            s.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at)"
                         " VALUES ('r-apos','k-apos','abra o app','execute','running',?,?)", (json.dumps([iid]), agora))
            s.db.execute("INSERT INTO objectives(id, run_id, instance_id, status) VALUES (?,?,?,?)",
                         (f"r-apos:{iid}", "r-apos", iid, "running"))
            r = await c.delete(f"/api/instances/{iid}")
            assert r.status_code == 409, r.text
            assert r.json()["detail"]["code"] == "objetivo_aberto" and r.json()["detail"]["objective_id"] == f"r-apos:{iid}"
            s.db.execute("UPDATE objectives SET status='cancelled' WHERE run_id='r-apos'")

            # Vínculo ativo de perfil: a sessão dele vive no AVD.
            s.db.execute("INSERT INTO instagram_profiles(id, username, status, created_at, updated_at)"
                         " VALUES ('ig-apos','apos','active',?,?)", (agora, agora))
            s.db.execute("INSERT INTO device_profile_bindings(profile_id, instance_id, active, bound_at)"
                         " VALUES ('ig-apos',?,1,?)", (iid, agora))
            r = await c.delete(f"/api/instances/{iid}")
            assert r.status_code == 409 and r.json()["detail"]["code"] == "vinculo_ativo"
            assert r.json()["detail"]["profile_id"] == "ig-apos"
            s.db.execute("UPDATE device_profile_bindings SET active=0, unbound_at=? WHERE profile_id='ig-apos'", (agora,))

            # Comando em voo.
            comando, _ = s.commands.create(command_id="cmd-apos-1", instance_id=iid, verb="start",
                                           idempotency_key="k-cmd-apos-1")
            r = await c.delete(f"/api/instances/{iid}")
            assert r.status_code == 409 and r.json()["detail"]["code"] == "comando_em_voo"
            assert r.json()["detail"]["command_id"] == comando["id"]
            s.commands.transition(comando["id"], CommandState.failed, reason="teste")

            # Nada foi tocado em nenhuma recusa: AVD no disco, linha ativa, aparelho no parque.
            assert (h.cfg.avd_home / f"{iid}.avd").exists()
            assert s.db.scalar("SELECT retired_at FROM instances WHERE id=?", (iid,)) is None
            assert iid in s.devices.devices
            recusas = {m: metricas.valor("provisionamento.aposentadoria", resultado="recusada", motivo=m)
                       for m in ("instancia_da_configuracao", "aparelho_ligado", "objetivo_aberto", "vinculo_ativo",
                                 "comando_em_voo")}
            assert recusas == dict.fromkeys(recusas, 1)

            # Sem impedimento, aposenta.
            assert (await c.delete(f"/api/instances/{iid}")).status_code == 200
    finally:
        await h.state.stop()


# ---------------------------------------------------------------------- limites: max_devices
async def test_teto_de_aparelhos_entra_e_sai_pela_tela_limites_sem_ir_para_o_agente(tmp_path: Path) -> None:
    h, reg, agente = await _com_worker(tmp_path, remotos=["android-03"], count=3)
    try:
        assert h.state is not None
        host = h.cfg.owner_id
        async with await _cliente(h) as c:
            lista = (await c.get("/api/servers/limits")).json()
            for linha in lista:
                # Ninguém declara teto de aparelhos; sem decisão, não há teto.
                assert linha["declared"]["max_devices"] is None and linha["effective"]["max_devices"] is None

            r = await c.put(f"/api/servers/{host}/limits", json={"max_devices": 12})
            assert r.status_code == 200, r.text
            assert r.json()["decided"]["max_devices"] == 12 and r.json()["effective"]["max_devices"] == 12
            assert reg.limites_definidos(host) == {"max_devices": 12}

            antes = len(agente.enviados)
            r = await c.put(f"/api/servers/{WORKER}/limits", json={"max_devices": 4, "max_working": 2})
            assert r.status_code == 200, r.text
            assert r.json()["effective"]["max_devices"] == 4
            # A mensagem `limits` que vai para o agente não ganhou campo: ele não cria aparelho.
            assert reg.mensagem_de_limites(WORKER) == Limits(max_slots=None, boot_parallelism=None,
                                                             min_free_ram_mb=None)
            for m in agente.enviados[antes:]:
                assert "max_devices" not in m

            r = await c.put(f"/api/servers/{WORKER}/limits", json={"max_devices": None})
            assert r.json()["effective"]["max_devices"] is None and r.json()["effective"]["max_working"] == 2
            assert (await c.put(f"/api/servers/{host}/limits", json={"max_devices": 0})).status_code == 422
    finally:
        await h.crash()



def test_apagar_avd_com_arquivo_somente_leitura(tmp_path: Path) -> None:
    """29/09, android-17: o `DELETE /api/instances/android-17` recusou com `avd_nao_apagado` (WinError 5) porque o
    emulador deixou `data/misc/pstore/pstore.bin` somente-leitura. O apagar libera a escrita e termina."""
    import os
    import stat

    from app.devices.avd import AvdManager

    class _Cfg:
        avd_home = tmp_path

    pasta = tmp_path / "android-99.avd" / "data" / "misc" / "pstore"
    pasta.mkdir(parents=True)
    preso = pasta / "pstore.bin"
    preso.write_bytes(b"x")
    os.chmod(preso, stat.S_IREAD)
    (tmp_path / "android-99.avd" / "config.ini").write_text("x", encoding="utf-8")
    ini = tmp_path / "android-99.ini"
    ini.write_text("x", encoding="utf-8")
    os.chmod(ini, stat.S_IREAD)
    gerente = AvdManager.__new__(AvdManager)
    gerente.cfg = _Cfg()  # type: ignore[assignment]
    assert gerente.delete("android-99") is True
    assert not (tmp_path / "android-99.avd").exists() and not ini.exists()
