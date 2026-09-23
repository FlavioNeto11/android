"""Servidor e aparelho novos sem editar YAML (item 4.5 do plano-100; achados #16, #151, #47, #13).

O defeito, do jeito que doía: o inventário aparelho↔máquina vivia em TRÊS lugares sem ninguém conferir —
`instances.external` (a porta do túnel), `instances.worker_id` (a máquina) e o `worker.yaml` da outra ponta (os
aparelhos que ela de fato hospeda). Os dados para detectar a divergência já trafegavam no `hello`
(`devices[].instance_id`, `.adb_port`) e eram DESCARTADOS: a discordância só aparecia na execução, como
"este worker não hospeda ...", e um `reset` podia agir num AVD com a tela em outro. E acrescentar um aparelho
exigia editar dois blocos de YAML, reinstalar a tarefa do túnel com o mapa novo e reiniciar o backend.

O que estes testes protegem:

* a conferência cruzada roda a cada `hello`/batida e nomeia a divergência, nos três formatos em que ela aparece;
* enquanto o inventário diverge, verbo destrutivo é RECUSADO — é o dano latente do achado #47;
* o aparelho anunciado por um worker vira instância na hora, com porta de túnel alocada aqui, sem YAML nem
  reinício, e já entra no dicionário vivo do gerenciador;
* o mapa do túnel passa a ter um lugar só, gravado em arquivo, que o script do túnel relê sem `-Instalar`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.main import create_app
from app.util import now, to_iso
from app.workers.protocol import WorkerDevice
from .conftest import Harness

WORKER = "worker-lan-01"


def _inscrever(h: Harness, worker_id: str = WORKER) -> None:
    h.state.db.execute(
        "INSERT INTO workers(id, name, state, appium_mode, max_slots, enrolled_at, token_hash)"
        " VALUES (?,?,?,?,?,?,?)",
        (worker_id, "Notebook da LAN", "online", "central", 6, to_iso(now()), "hash-de-teste"))


def _anunciar(h: Harness, devices: list[dict[str, Any]], worker_id: str = WORKER) -> None:
    """Grava o inventário como o `hello`/batida do agente o grava, para a rota de adoção poder lê-lo."""
    from app.db import dumps
    h.state.db.execute("UPDATE workers SET devices=? WHERE id=?", (dumps(devices), worker_id))


def _amarrar(h: Harness, instance_id: str, worker_id: str | None, *, remote_adb_port: int | None = None) -> Any:
    h.state.db.execute("UPDATE instances SET worker_id=?, remote_adb_port=? WHERE id=?",
                       (worker_id, remote_adb_port, instance_id))
    rt = h.state.devices.devices[instance_id]
    rt.worker_id = worker_id
    rt.remote_adb_port = remote_adb_port
    return rt


@pytest.mark.asyncio
async def test_worker_que_nao_declara_o_aparelho_amarrado_a_ele_vira_divergencia(tmp_path: Path) -> None:
    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        _inscrever(h)
        rt = _amarrar(h, "android-02", WORKER)

        problemas = h.state.devices.conferir_inventario(WORKER, [
            WorkerDevice(serial="emulator-5554", state="online", adb_port=5555, instance_id="android-03")])
        assert rt.inventory_state == "divergent"
        assert "não declara hospedar android-02" in rt.inventory_detail
        # O aparelho declarado como android-03, que está amarrado a outra máquina, também é acusado.
        assert any("android-03" in p for p in problemas)
        assert h.state.devices.devices["android-03"].inventory_state == "divergent"

        # Declarado corretamente, a divergência SOME — o painel não pode ficar com o alarme de ontem.
        h.state.devices.conferir_inventario(WORKER, [
            WorkerDevice(serial="emulator-5554", state="online", adb_port=5555, instance_id="android-02")])
        assert rt.inventory_state is None and rt.inventory_detail is None
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_porta_de_adb_declarada_diferente_da_encaminhada_vira_divergencia(tmp_path: Path) -> None:
    """O mapa do túnel apontando para outro aparelho é o caso que fazia `reset` agir no AVD errado."""
    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        _inscrever(h)
        rt = _amarrar(h, "android-02", WORKER, remote_adb_port=5555)

        h.state.devices.conferir_inventario(WORKER, [
            WorkerDevice(serial="emulator-5556", state="online", adb_port=5557, instance_id="android-02")])
        assert rt.inventory_state == "divergent"
        assert "5557" in rt.inventory_detail and "5555" in rt.inventory_detail
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_inventario_divergente_recusa_verbo_destrutivo(tmp_path: Path) -> None:
    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        s = h.state
        _inscrever(h)
        rt = _amarrar(h, "android-02", WORKER, remote_adb_port=5555)
        s.devices.conferir_inventario(WORKER, [
            WorkerDevice(serial="emulator-5556", state="online", adb_port=5557, instance_id="android-02")])
        assert rt.inventory_state == "divergent"

        app = create_app(h.cfg, state=s)
        app.state.poc = s
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/instances/android-02/actions/reset",
                             json={"confirm": True, "idempotency_key": "inv-reset-01"})
            assert r.status_code == 409, r.text
            assert "inventário deste aparelho está divergente" in r.json()["detail"]["message"]

            inst = next(i for i in (await c.get("/api/instances")).json() if i["id"] == "android-02")
            assert inst["inventory_state"] == "divergent"
            assert "5557" in inst["inventory_detail"]
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_aparelho_anunciado_vira_instancia_sem_yaml_nem_reinicio(tmp_path: Path) -> None:
    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        s = h.state
        _inscrever(h)
        _anunciar(h, [{"serial": "emulator-5554", "avd_name": "worker-01", "state": "online", "adb_port": 5555},
                      {"serial": "emulator-5556", "avd_name": "worker-02", "state": "online", "adb_port": 5557}])
        app = create_app(h.cfg, state=s)
        app.state.poc = s
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            livres = (await c.get("/api/workers/devices/unbound")).json()
            assert {d["serial"] for d in livres} == {"emulator-5554", "emulator-5556"}

            r = await c.post(f"/api/workers/{WORKER}/devices/adopt", json={"serial": "emulator-5554"})
            assert r.status_code == 201, r.text
            corpo = r.json()
            novo = corpo["instance"]["id"]
            assert novo not in s.cfg.instance_ids(), "a instância nova não está no YAML — é esse o ponto"
            assert corpo["instance"]["origin"] == "dynamic"
            assert corpo["instance"]["worker_id"] == WORKER
            assert corpo["instance"]["kind"] == "external"
            assert corpo["instance"]["remote_adb_port"] == 5555
            porta = corpo["instance"]["tunnel_port"]
            assert corpo["instance"]["serial"] == f"127.0.0.1:{porta}"

            # Aparece no painel SEM reiniciar o backend: o runtime entrou no dicionário vivo na mesma chamada.
            assert novo in s.devices.devices
            assert any(i["id"] == novo for i in (await c.get("/api/instances")).json())

            # O mapa do túnel vira arquivo — é o que o script relê sem reinstalar a tarefa agendada.
            assert corpo["tunnel_map"] == f"{porta}:5555"
            assert Path(corpo["tunnel_map_file"]).read_text(encoding="utf-8").strip() == f"{porta}:5555"

            # Segundo aparelho: porta seguinte, e o mapa passa a ter as duas linhas, em ordem de porta.
            r2 = await c.post(f"/api/workers/{WORKER}/devices/adopt",
                              json={"serial": "emulator-5556", "instance_id": "notebook-01"})
            assert r2.status_code == 201, r2.text
            assert r2.json()["instance"]["id"] == "notebook-01"
            assert r2.json()["tunnel_map"] == f"{porta}:5555,{r2.json()['instance']['tunnel_port']}:5557"

            # Aparelho que o worker nunca anunciou não é adotado por engano.
            r3 = await c.post(f"/api/workers/{WORKER}/devices/adopt", json={"serial": "emulator-9999"})
            assert r3.status_code == 404 and r3.json()["detail"]["code"] == "unknown_device"
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_instancia_dinamica_sobrevive_ao_reinicio_do_backend(tmp_path: Path) -> None:
    """Sem isto a adoção seria um aparelho que some no próximo `start` — `seed` só carregava o que o YAML lista."""
    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        _inscrever(h)
        _anunciar(h, [{"serial": "emulator-5554", "avd_name": "worker-01", "state": "online", "adb_port": 5555}])
        rt = h.state.devices.adotar_aparelho(WORKER, serial="emulator-5554", adb_port=5555, avd_name="worker-01")
        novo, porta = rt.id, rt.tunnel_port

        await h.crash()
        await h.boot()

        revivido = h.state.devices.devices.get(novo)
        assert revivido is not None, "instância dinâmica tem de voltar no arranque"
        assert revivido.external and revivido.serial == f"127.0.0.1:{porta}"
        assert revivido.origin == "dynamic" and revivido.worker_id == WORKER
        assert h.state.devices.mapa_do_tunel(WORKER) == f"{porta}:5555"
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_porta_de_tunel_nao_colide_com_a_do_config(tmp_path: Path) -> None:
    h = Harness(tmp_path, 3, external={"android-03": "127.0.0.1:15555"})
    await h.boot()
    try:
        _inscrever(h)
        assert 15555 in h.state.devices.portas_de_tunel_em_uso()
        rt = h.state.devices.adotar_aparelho(WORKER, serial="emulator-5554", adb_port=5555)
        assert rt.tunnel_port == 15556, "a porta já declarada no config.yaml continua valendo e é pulada"
        outro = h.state.devices.adotar_aparelho(WORKER, serial="emulator-5556", adb_port=5557)
        assert outro.tunnel_port == 15557
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_declaracao_vazia_nao_condena_aparelho_nenhum(tmp_path: Path) -> None:
    """O worker local bate o coração com `devices=[]` de propósito: sem esta guarda, a primeira batida do
    próprio central marcaria todos os aparelhos daqui como divergentes e recusaria `stop` em todos eles."""
    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        _inscrever(h)
        rt = _amarrar(h, "android-02", WORKER)
        assert h.state.devices.conferir_inventario(WORKER, []) == []
        assert rt.inventory_state is None

        local = h.state.workers.local_worker_id
        assert local is not None
        assert h.state.devices.conferir_inventario(local, []) == []
        assert all(r.inventory_state is None for r in h.state.devices.devices.values())
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_adocao_usa_o_id_que_o_agente_ja_declara(tmp_path: Path) -> None:
    """O agente resolve o despacho pelo `instance_id` do `worker.yaml` dele ("este worker não hospeda X").
    Criar a instância com outro nome faria todo comando falhar do outro lado — e a batida seguinte acusaria
    divergência no aparelho recém-adotado."""
    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        s = h.state
        _inscrever(h)
        _anunciar(h, [{"serial": "emulator-5560", "avd_name": "worker-07", "state": "online", "adb_port": 5561,
                       "instance_id": "notebook-07"}])
        app = create_app(h.cfg, state=s)
        app.state.poc = s
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(f"/api/workers/{WORKER}/devices/adopt", json={"serial": "emulator-5560"})
            assert r.status_code == 201, r.text
            assert r.json()["instance"]["id"] == "notebook-07"

        # E o aparelho nasce CONFERIDO: a batida seguinte não pode acusar o que acabou de ser adotado.
        assert s.devices.conferir_inventario(WORKER, [
            WorkerDevice(serial="emulator-5560", state="online", adb_port=5561, instance_id="notebook-07")]) == []
        assert s.devices.devices["notebook-07"].inventory_state is None
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_mapa_do_tunel_adota_o_que_ja_vinha_do_config(tmp_path: Path) -> None:
    """Sem isto, trocar a tarefa do túnel para `-MapaArquivo` derrubaria os aparelhos que já estavam de pé: o
    arquivo só teria os ADOTADOS, porque as instâncias do YAML nasceram sem `remote_adb_port`."""
    h = Harness(tmp_path, 3, external={"android-03": "127.0.0.1:15555"})
    await h.boot()
    try:
        s = h.state
        _inscrever(h)
        rt = _amarrar(h, "android-03", WORKER)
        assert rt.remote_adb_port is None and s.devices.mapa_do_tunel(WORKER) == ""

        s.devices.conferir_inventario(WORKER, [
            WorkerDevice(serial="emulator-5554", state="online", adb_port=5555, instance_id="android-03")])
        assert rt.remote_adb_port == 5555 and rt.tunnel_port == 15555
        assert s.devices.mapa_do_tunel(WORKER) == "15555:5555"
        arquivo = s.cfg.data_dir / "tunnel" / f"{WORKER}.map"
        assert arquivo.read_text(encoding="utf-8").strip() == "15555:5555"

        # E o aparelho adotado depois ENTRA no mesmo arquivo, junto com o que já estava lá.
        novo = s.devices.adotar_aparelho(WORKER, serial="emulator-5556", adb_port=5557)
        s.devices.escrever_mapa_do_tunel(WORKER)
        assert arquivo.read_text(encoding="utf-8").strip() == f"15555:5555,{novo.tunnel_port}:5557"
    finally:
        await h.state.stop()
