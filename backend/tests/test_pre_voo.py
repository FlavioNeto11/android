"""Pré-voo na criação da execução (item 4.3; achado #51).

O defeito, do jeito que doía: a recusa explicada ANTES de agendar existia para o comando do painel e não existia
para a execução. A tarefa era aceita, planejada — gastando chamada ao planejador — e só então bloqueava no
aparelho, ou falhava lá dentro. Um aparelho de outra máquina desligado aceitava a tarefa do mesmo jeito, e o
"rodízio liga quando houver vaga" nunca valeu para ele: `_rotate` exclui `rt.external`.

O que estes testes protegem:

* a recusa vem por APARELHO, com motivo e ação, e antes de o planejador ser chamado;
* quem escolheu vários aparelhos pode seguir só com os aptos — sem que o sistema decida isso sozinho;
* manutenção de worker continua sendo ESPERA, não recusa (é o que o scheduler já faz);
* o que NÃO se sabe nunca vira recusa: aparelho sem linha de aplicativo passa;
* `start` grava o motivo específico no item, em vez do genérico "Aparelho offline".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest
import pytest_asyncio

from app.main import create_app
from app.models import InstanceState, ReleaseChannel, ReleaseState, RunCreate
from app.taskqueue.service import RunError

from .conftest import COMMAND, Harness

EXTERNO = "android-03"
PACOTE = "com.pocqa.messenger"


@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> AsyncIterator[Harness]:
    h = Harness(tmp_path, 3, external={EXTERNO: "127.0.0.1:15555"})
    await h.boot()
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


def criar(h: Harness, ids: list[str], *, key: str, only_ready: bool = False) -> Any:
    assert h.state is not None
    return h.state.runs.create(RunCreate(command=COMMAND, instance_ids=ids, mode="plan",
                                         idempotency_key=key, only_ready=only_ready))


def desligar(h: Harness, iid: str, estado: InstanceState = InstanceState.stopped) -> Any:
    assert h.state is not None
    rt = h.state.devices.devices[iid]
    rt.state = estado
    return rt


async def test_remoto_desligado_e_recusado_antes_de_planejar(parque: Harness) -> None:
    h = parque
    assert h.state is not None
    desligar(h, EXTERNO)
    chamadas = len(h.ai.calls)

    with pytest.raises(RunError) as saida:
        criar(h, [EXTERNO], key="pre-voo-00001")
    assert saida.value.code == "preflight" and saida.value.status == 409
    recusa = saida.value.details["devices"][0]
    assert recusa["instance_id"] == EXTERNO and recusa["code"] == "remote_off"
    assert "outra máquina" in recusa["motivo"] and "stopped" in recusa["motivo"]
    # Sem worker inscrito, ninguém aqui o liga: a ação é a verdadeira, não "aguarde o rodízio".
    assert "máquina que o hospeda" in recusa["acao"]
    assert saida.value.details["ready"] == []
    assert len(h.ai.calls) == chamadas          # o planejador NÃO foi chamado
    assert h.state.db.scalar("SELECT COUNT(*) FROM runs") == 0


async def test_seguir_so_com_os_aptos(parque: Harness) -> None:
    h = parque
    assert h.state is not None
    desligar(h, EXTERNO)

    with pytest.raises(RunError) as saida:
        criar(h, ["android-01", EXTERNO], key="pre-voo-00002")
    assert saida.value.details["ready"] == ["android-01"]
    assert "seguir só com os aptos" in saida.value.message

    resumo = criar(h, ["android-01", EXTERNO], key="pre-voo-00003", only_ready=True)
    linha = h.state.repo.run_row(resumo.id)
    assert linha is not None and EXTERNO not in linha["instance_ids"] and "android-01" in linha["instance_ids"]


async def test_remoto_com_servidor_que_sabe_ligar_manda_para_a_infraestrutura(parque: Harness) -> None:
    """Aparelho de worker: existe QUEM o ligue, e a ação diz onde — sem prometer que o rodízio daqui o fará."""
    h = parque
    assert h.state is not None
    rt = desligar(h, EXTERNO)
    rt.worker_id = "worker-lan-01"
    recusa = h.state.runs.pre_voo([EXTERNO])[EXTERNO]
    assert recusa["code"] == "remote_off" and "Infraestrutura" in recusa["acao"]


async def test_sem_nenhum_apto_nao_cria_execucao_pela_metade(parque: Harness) -> None:
    """`only_ready` não vira "crie do nada": sem aparelho apto, a recusa continua sendo recusa."""
    h = parque
    desligar(h, EXTERNO)
    with pytest.raises(RunError) as saida:
        criar(h, [EXTERNO], key="pre-voo-00004", only_ready=True)
    assert saida.value.code == "preflight" and "Nenhum aparelho" in saida.value.message


async def test_aparelho_local_parado_nao_impede_criar_mas_para_o_item_no_inicio(parque: Harness) -> None:
    """A diferença entre recusar e esperar: este aparelho é DESTA máquina, e quem pediu a tarefa pode ir ligá-lo.

    Recusar a criação obrigaria a pessoa a ligar o aparelho antes de sequer registrar o trabalho. No INÍCIO, sim:
    ali o item para com o motivo, exatamente como parava antes.
    """
    h = parque
    assert h.state is not None
    assert not h.state.scheduler.get_settings().auto_start_devices
    desligar(h, "android-01")
    assert h.state.runs.pre_voo(["android-01"]) == {}                      # criar continua valendo
    recusa = h.state.runs.pre_voo(["android-01"], ao_iniciar=True)["android-01"]
    assert recusa["code"] == "device_off" and "retome este item" in recusa["acao"]


async def test_com_rodizio_ligado_aparelho_parado_nem_no_inicio_e_impedimento(parque: Harness) -> None:
    """Esperar vaga não é impedimento: o rodízio liga o aparelho local. Bloquear aqui seria mentira ao contrário."""
    h = parque
    assert h.state is not None
    h.state.settings.update({"auto_start_devices": True})
    desligar(h, "android-01")
    assert h.state.runs.pre_voo(["android-01"], ao_iniciar=True) == {}


async def test_servidor_nao_inscrito_e_recusa_mas_manutencao_e_espera(parque: Harness) -> None:
    h = parque
    assert h.state is not None
    rt = h.state.devices.devices["android-01"]
    rt.worker_id = "worker-que-ninguem-inscreveu"
    recusa = h.state.runs.pre_voo(["android-01"])["android-01"]
    assert recusa["code"] == "worker_unenrolled"

    # Manutenção do worker DESTE servidor: o scheduler já trata como espera ("suspende novas atribuições"), e o
    # pré-voo não pode transformar isso em recusa — seriam duas histórias diferentes para a mesma situação.
    rt.worker_id = h.cfg.owner_id
    h.state.workers.set_maintenance(h.cfg.owner_id, True)
    try:
        assert h.state.runs.pre_voo(["android-01"]) == {}
    finally:
        h.state.workers.set_maintenance(h.cfg.owner_id, False)


async def test_app_com_entrega_falhada_e_recusa_e_o_que_nao_se_sabe_nao_e(parque: Harness) -> None:
    h = parque
    assert h.state is not None
    # Sem linha nenhuma em `device_app_state`: "não se sabe" — e o que não se sabe nunca fecha a porta.
    assert h.state.release_repo.app_state("android-01", PACOTE) is None
    assert h.state.runs.pre_voo(["android-01"]) == {}

    # Entrega que falhou: só é "entrega" quando existe uma versão distribuída para este aparelho — senão o que
    # há é um aplicativo que não está pronto, e é assim que a porta do app já falava.
    h.state.release_repo.save_release(
        release_id="rel-pre-voo", package_name=PACOTE, version_name="9.0", version_code=9,
        artifact_type="single", signature_sha256="a" * 64, min_sdk=21, target_sdk=35, abis=[],
        catalog_dir="apks/x", source_type="inbox", source_reference=None,
        status=ReleaseState.installable, detail=None, files=[])
    h.state.release_repo.set_channel("rel-pre-voo", ReleaseChannel.promoted)
    h.state.release_repo.upsert_app_state("android-01", PACOTE, state="install_failed",
                                          desired_release_id="rel-pre-voo",
                                          detail="INSTALL_FAILED_NO_MATCHING_ABIS")
    recusa = h.state.runs.pre_voo(["android-01"])["android-01"]
    assert recusa["code"] == "app_failed" and "INSTALL_FAILED_NO_MATCHING_ABIS" in recusa["motivo"]

    # A mesma linha SEM versão distribuída é outra história, e outra frase: "não está pronto".
    h.state.release_repo.upsert_app_state("android-01", PACOTE, desired_release_id=None)
    assert h.state.runs.pre_voo(["android-01"])["android-01"]["code"] == "app_not_ready"

    # App observado como ausente (é o que a reobservação grava quando o `pm` diz que não está lá).
    h.state.release_repo.upsert_app_state("android-01", PACOTE, state="missing", detail=None,
                                          desired_release_id=None)
    assert h.state.runs.pre_voo(["android-01"])["android-01"]["code"] == "app_missing"

    h.state.release_repo.upsert_app_state("android-01", PACOTE, state="ready")
    assert h.state.runs.pre_voo(["android-01"]) == {}


async def test_app_nunca_observado_entra_na_releitura_quando_o_aparelho_sobe(parque: Harness) -> None:
    """"Apps instalados" só valia onde havia release gerenciada: sem linha, a porta do app não opinava."""
    h = parque
    assert h.state is not None
    assert h.state.release_repo.app_state("android-01", PACOTE) is None
    assert PACOTE in h.state.pacotes_com_dado_velho("android-01")

    h.state.release_repo.upsert_app_state("android-01", PACOTE, state="ready", verified_at="2099-01-01T00:00:00Z")
    assert PACOTE not in h.state.pacotes_com_dado_velho("android-01")       # observado e recente: nada a reler


async def test_start_grava_o_motivo_especifico_no_item(parque: Harness) -> None:
    """Antes: "Aparelho offline / Inicie a instância" — para um aparelho que esta máquina não liga."""
    h = parque
    assert h.state is not None
    resumo = criar(h, [EXTERNO], key="pre-voo-00006")            # criado com o aparelho AINDA online
    await h.wait(lambda: h.state.repo.run_row(resumo.id)["status"] == "planned",  # type: ignore[union-attr]
                 what="plano pronto")
    desligar(h, EXTERNO)                                          # o remoto cai entre planejar e iniciar
    h.state.runs.start(resumo.id)

    o = h.state.db.one("SELECT * FROM objectives WHERE id=?", (f"{resumo.id}:{EXTERNO}",))
    assert o["status"] == "waiting_user"
    assert "outra máquina" in o["blocked_reason"] and o["needs"]
    assert o["blocked_reason"] != "Aparelho offline"


async def test_api_devolve_409_com_a_lista_por_aparelho(parque: Harness) -> None:
    h = parque
    assert h.state is not None
    desligar(h, EXTERNO)
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        corpo = {"command": COMMAND, "instance_ids": ["android-01", EXTERNO], "mode": "plan",
                 "idempotency_key": "pre-voo-http-001"}
        r = await c.post("/api/runs", json=corpo)
        assert r.status_code == 409
        detalhe = r.json()["detail"]
        assert detalhe["code"] == "preflight"
        assert [d["instance_id"] for d in detalhe["devices"]] == [EXTERNO]
        assert detalhe["ready"] == ["android-01"]

        ok = await c.post("/api/runs", json={**corpo, "only_ready": True, "idempotency_key": "pre-voo-http-002"})
        assert ok.status_code == 200
