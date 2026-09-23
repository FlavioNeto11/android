"""Onde a execução rodou: servidor, serial e backend gravados no objetivo (item 4.1; achado #176).

O defeito, do jeito que doía: `android-09` é um APELIDO. O vínculo id lógico → aparelho físico muda por
configuração (`instances.external`, `instances.worker_id`), e o relatório de uma execução antiga em `android-09`
não dizia se aquilo tinha sido o emulador desta máquina em 17/09 ou o aparelho do notebook em 19/09 — os dois
existem no banco com o mesmo id.

O que estes testes protegem:

* a fotografia é tirada no plano e RE-tirada no despacho (o aparelho pode trocar de dono no meio);
* o que não se sabe não é inventado: sem fotografia, o relatório diz "não registrado", nunca "esta máquina";
* o histórico não é reescrito quando o aparelho muda de servidor DEPOIS;
* a lista de execuções é paginada e filtrável por aparelho/servidor — sem isso as antigas não tinham caminho.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest_asyncio

from app.main import create_app
from app.models import RunCreate
from app.workers.protocol import Hello, WorkerDevice, WorkerResources

from .conftest import COMMAND, Harness

EXTERNO = "android-03"
OUTRO_SERVIDOR = "worker-lan-01"


def inscrever_servidor(h: Harness, worker_id: str = OUTRO_SERVIDOR) -> None:
    """Deixa o servidor INSCRITO antes de amarrar aparelho a ele: aparelho preso a um servidor que não existe é
    recusado pelo pré-voo, e o que se quer provar aqui é a fotografia de onde a execução rodou, não a recusa."""
    assert h.state is not None
    reg = h.state.workers
    reg.autenticar(Hello(worker_id=worker_id, name="Notebook da LAN", agent_version="0.1.0", os="windows",
                         max_slots=6, verbs=["start", "stop", "hibernate", "reset"],
                         devices=[WorkerDevice(serial="127.0.0.1:15555", avd_name="w-01", state="online",
                                               adb_port=5555)],
                         resources=WorkerResources(cpu_count=8, ram_total_mb=32000, ram_free_mb=16000)),
                   token=None, enrollment=reg.criar_inscricao("servidor de teste"))


@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> AsyncIterator[Harness]:
    """android-03 é de outra máquina: é dele que a pergunta "onde isto rodou" tem resposta interessante."""
    h = Harness(tmp_path, 3, external={EXTERNO: "127.0.0.1:15555"})
    await h.boot()
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


def planejar(h: Harness, ids: list[str], *, key: str) -> str:
    """Cria a execução em modo PLANO: materializa objetivos e etapas sem despachar nada em aparelho nenhum."""
    assert h.state is not None
    return h.state.runs.create(RunCreate(command=COMMAND, instance_ids=ids, mode="plan", idempotency_key=key)).id


def objetivo(h: Harness, run_id: str, iid: str) -> Any:
    assert h.state is not None
    return h.state.db.one("SELECT * FROM objectives WHERE id=?", (f"{run_id}:{iid}",))


async def test_materializar_fotografa_servidor_serial_e_backend(parque: Harness) -> None:
    h = parque
    assert h.state is not None
    inscrever_servidor(h)
    rt = h.state.devices.devices[EXTERNO]
    rt.worker_id = OUTRO_SERVIDOR
    rt.physical_id = "notebook/Pixel_6_API_34"

    run_id = planejar(h, [EXTERNO], key="onde-rodou-0001")
    await h.wait(lambda: objetivo(h, run_id, EXTERNO) is not None, what="objetivo materializado")

    o = objetivo(h, run_id, EXTERNO)
    assert o["worker_id"] == OUTRO_SERVIDOR
    assert o["device_serial"] == "127.0.0.1:15555"
    assert o["physical_id"] == "notebook/Pixel_6_API_34"
    assert o["hosted_by"] == h.cfg.owner_id          # o backend que despachou é o dono de `steps.claimed_by`


async def test_mudar_o_aparelho_de_servidor_depois_nao_reescreve_o_historico(parque: Harness) -> None:
    """É exatamente o caso do achado: o id lógico muda de máquina e o relatório de ontem continua verdadeiro."""
    h = parque
    assert h.state is not None
    inscrever_servidor(h)
    rt = h.state.devices.devices[EXTERNO]
    rt.worker_id = OUTRO_SERVIDOR

    run_id = planejar(h, [EXTERNO], key="onde-rodou-0002")
    await h.wait(lambda: objetivo(h, run_id, EXTERNO) is not None, what="objetivo materializado")

    rt.worker_id = "worker-outro-02"                 # o aparelho mudou de dono DEPOIS
    rt.serial = "127.0.0.1:19999"
    assert objetivo(h, run_id, EXTERNO)["worker_id"] == OUTRO_SERVIDOR
    assert objetivo(h, run_id, EXTERNO)["device_serial"] == "127.0.0.1:15555"


async def test_refotografa_no_despacho(parque: Harness) -> None:
    """Entre materializar e despachar, o aparelho pode ter trocado de dono: quem conta a verdade é o despacho."""
    h = parque
    assert h.state is not None
    rt = h.state.devices.devices["android-01"]
    rt.worker_id = None

    run_id = planejar(h, ["android-01"], key="onde-rodou-0003")
    await h.wait(lambda: objetivo(h, run_id, "android-01") is not None, what="objetivo materializado")
    assert objetivo(h, run_id, "android-01")["worker_id"] is None      # fotografia do PLANO: sem dono

    # O aparelho ganha dono DEPOIS do plano e antes do despacho — aqui o worker DESTE servidor, que é quem
    # aceita trabalho no harness. A execução roda até o fim no aparelho falso e a fotografia é refeita.
    rt.worker_id = h.cfg.owner_id
    rt.physical_id = "esta-maquina/Pixel_6_API_34"
    h.state.runs.start(run_id)
    await h.wait_run(run_id)
    o = objetivo(h, run_id, "android-01")
    assert o["worker_id"] == h.cfg.owner_id and o["physical_id"] == "esta-maquina/Pixel_6_API_34"


async def test_relatorio_diz_onde_rodou_e_nao_inventa_quando_nao_sabe(parque: Harness) -> None:
    h = parque
    assert h.state is not None
    inscrever_servidor(h)
    rt = h.state.devices.devices[EXTERNO]
    rt.worker_id = OUTRO_SERVIDOR

    run_id = planejar(h, [EXTERNO], key="onde-rodou-0004")
    await h.wait(lambda: objetivo(h, run_id, EXTERNO) is not None, what="objetivo materializado")

    rel = h.state.runs.report(run_id)
    linha = next(p for p in rel["per_instance"] if p["instance_id"] == EXTERNO)
    assert linha["worker_id"] == OUTRO_SERVIDOR and linha["device_serial"] == "127.0.0.1:15555"
    assert "Onde rodou (servidor/serial)" in rel["markdown"]
    assert f"{OUTRO_SERVIDOR} · 127.0.0.1:15555" in rel["markdown"]

    # Objetivo sem fotografia (execução de antes desta migração): o relatório diz que não sabe, e não chuta.
    h.state.db.execute("UPDATE objectives SET worker_id=NULL, hosted_by=NULL, device_serial=NULL WHERE id=?",
                       (f"{run_id}:{EXTERNO}",))
    assert "| não registrado |" in h.state.runs.report(run_id)["markdown"]


async def test_dto_de_objetivo_e_de_etapa_levam_onde_rodou_ao_painel(parque: Harness) -> None:
    h = parque
    assert h.state is not None
    inscrever_servidor(h)
    h.state.devices.devices[EXTERNO].worker_id = OUTRO_SERVIDOR
    run_id = planejar(h, [EXTERNO], key="onde-rodou-0005")
    await h.wait(lambda: objetivo(h, run_id, EXTERNO) is not None, what="objetivo materializado")

    detail = h.state.repo.run_detail(run_id)
    assert detail is not None
    assert detail.objectives[0].worker_id == OUTRO_SERVIDOR
    assert detail.objectives[0].device_serial == "127.0.0.1:15555"
    assert detail.objectives[0].hosted_by == h.cfg.owner_id
    # `steps.claimed_by` existe desde a migração 016 e não aparecia em DTO nenhum: sem ele o painel não sabe
    # dizer qual backend está com aquela etapa na mão.
    assert detail.steps and detail.steps[0].claimed_by is None


async def test_comando_de_adb_em_aparelho_remoto_registra_onde(parque: Harness) -> None:
    """`worker_id` do comando só é carimbado no ENVIO ao agente; verbo de ADB sai daqui e nunca o tinha."""
    h = parque
    assert h.state is not None
    inscrever_servidor(h)
    h.state.devices.devices[EXTERNO].worker_id = OUTRO_SERVIDOR
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(f"/api/instances/{EXTERNO}/actions/home", json={"idempotency_key": "onde-adb-00001"})
        assert r.status_code in (202, 409)          # aparelho offline recusa; o registro do comando é o que importa
        row = h.state.db.one("SELECT * FROM commands WHERE idempotency_key=?", ("onde-adb-00001",))
        assert row is not None and row["host_worker_id"] == OUTRO_SERVIDOR


async def test_lista_de_execucoes_pagina_e_filtra_por_onde(parque: Harness) -> None:
    h = parque
    assert h.state is not None
    inscrever_servidor(h)
    h.state.devices.devices[EXTERNO].worker_id = OUTRO_SERVIDOR
    r1 = planejar(h, ["android-01"], key="onde-pagina-0001")
    r2 = planejar(h, [EXTERNO], key="onde-pagina-0002")
    await h.wait(lambda: objetivo(h, r2, EXTERNO) is not None, what="objetivos materializados")

    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        pagina = (await c.get("/api/runs", params={"limit": 1, "offset": 0})).json()
        assert pagina["total"] == 2 and len(pagina["runs"]) == 1
        segunda = (await c.get("/api/runs", params={"limit": 1, "offset": 1})).json()
        assert segunda["runs"][0]["id"] != pagina["runs"][0]["id"]        # a segunda página traz a OUTRA

        por_servidor = (await c.get("/api/runs", params={"worker_id": OUTRO_SERVIDOR})).json()
        assert [r["id"] for r in por_servidor["runs"]] == [r2]
        por_aparelho = (await c.get("/api/runs", params={"instance_id": "android-01"})).json()
        assert [r["id"] for r in por_aparelho["runs"]] == [r1]
        assert (await c.get("/api/runs", params={"worker_id": "worker-que-nao-existe"})).json()["total"] == 0
