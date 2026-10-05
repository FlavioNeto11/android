"""Distribuir uma versão promovida ao parque — e a porta do app passando a se resolver sozinha.

Só 4 aparelhos ficam ligados por vez (rodízio). Distribuir grava a versão DESEJADA em cada aparelho de tarefa: quem
está ligado e livre instala já; os demais recebem pela porta do app, ao pegar a próxima tarefa daquele pacote, antes
da tarefa. O que estes testes protegem:

* canário primeiro: sem prova registrada não há o que distribuir;
* instalar é mexer no disco do aparelho — uma entrega que falha NÃO se repete sozinha, fica nomeada e espera uma pessoa;
* um objetivo já em andamento nunca tem o app trocado no meio.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest
import pytest_asyncio

from app.models import InstanceState, ReleaseChannel, RunCreate
from app.releases.catalog import ReleaseValidationError
from app.taskqueue.service import RunError

from .conftest import COMMAND, Harness
from .test_app_releases import QA_APK, FakeAdbDevice, StubInspector, part

LOJA = "android-03"
PACOTE = "com.pocqa.messenger"          # o app das execuções do harness: é por ele que a porta do app é consultada


class AdbFalso(FakeAdbDevice):
    """O harness liga o aparelho de mentira no IO, mas `rt.adb` é o adb REAL. Aqui ele também vira mentira — senão
    o teste instalaria um APK nos emuladores desta máquina. O que o falso não conhece vira operação sem efeito."""

    def __getattr__(self, nome: str) -> Any:
        return lambda *a, **k: None


@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> AsyncIterator[Harness]:
    """android-01 e android-02 são de tarefa; android-03 é a loja."""
    h = Harness(tmp_path, 3, store=LOJA)
    await h.boot()
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


def falsificar_adb(h: Harness) -> dict[str, AdbFalso]:
    st = h.state
    assert st is not None
    falsos: dict[str, AdbFalso] = {}
    for rt in st.devices.devices.values():
        falsos[rt.id] = AdbFalso()
        rt.adb = falsos[rt.id]                                       # type: ignore[assignment]
    original = st.installer.launch_probe
    st.installer.launch_probe = lambda rt, package, **_k: original(rt, package, settle_s=0.01)  # type: ignore[method-assign]
    return falsos


def release(h: Harness, codigo: int = 7, *, promovida: bool = True) -> str:
    """Importa uma versão do app de QA pelo caminho de sempre e, se pedido, leva até `promoted` com prova registrada."""
    st = h.state
    assert st is not None
    st.releases.inspector = StubInspector(                           # type: ignore[assignment]
        {"base.apk": part(package_name=PACOTE, version_code=codigo, version_name=f"{codigo}.0", abis=[])})
    inbox = st.cfg.apk_inbox
    inbox.mkdir(parents=True, exist_ok=True)
    shutil.copy2(QA_APK, inbox / "base.apk")
    with open(inbox / "base.apk", "ab") as fh:
        fh.write(bytes(codigo))                                      # bytes próprios por versão → id próprio
    saida = st.releases.import_inbox()[0]
    assert saida.ok, saida.reason
    rid = str(saida.release_id)
    st.releases.approve_signature(rid)
    if promovida:
        st.release_repo.set_channel(rid, ReleaseChannel.canary, canary_instance_id="android-01")
        st.release_repo.record_validation(rid, "android-01", stage="install", ok=True, detail=None)
        st.release_repo.record_validation(rid, "android-01", stage="launch", ok=True, detail="abriu")
        st.releases.promote(rid)
    return rid


def estado(h: Harness, iid: str) -> Any:
    return h.state.release_repo.app_state(iid, PACOTE)               # type: ignore[union-attr]


async def pronto(h: Harness, iid: str, rid: str) -> None:
    def ok() -> bool:
        row = estado(h, iid)
        return bool(row) and row["state"] == "ready" and row["installed_release_id"] == rid
    await h.wait(ok, timeout=30, what=f"{iid} com a versão instalada")


# ==================================================================== canário primeiro
async def test_distribuir_exige_versao_promovida(parque: Harness) -> None:
    falsificar_adb(parque)
    rid = release(parque, promovida=False)
    with pytest.raises(ReleaseValidationError, match="Só se distribui versão PROMOVIDA"):
        parque.state.distribute(rid)                                 # type: ignore[union-attr]
    assert estado(parque, "android-01") is None                      # nada foi marcado em aparelho nenhum


# ==================================================================== quem está ligado recebe já; o resto, depois
async def test_distribuir_instala_ja_nos_livres_deixa_pendente_nos_desligados_e_pula_a_loja(parque: Harness) -> None:
    falsos = falsificar_adb(parque)
    devs = parque.state.devices                                      # type: ignore[union-attr]
    await devs.stop_instance(devs.get("android-02"))
    rid = release(parque)

    saida = {d["id"]: d for d in parque.state.distribute(rid)}       # type: ignore[union-attr]
    assert set(saida) == {"android-01", "android-02"}                # a loja é a FONTE: nem aparece
    assert saida["android-01"]["outcome"] == "started"
    assert saida["android-02"]["outcome"] == "pending" and "entrar em serviço" in saida["android-02"]["reason"]

    await pronto(parque, "android-01", rid)
    assert "install" in falsos["android-01"].calls
    pendente = estado(parque, "android-02")
    assert pendente["desired_release_id"] == rid and pendente["installed_release_id"] is None
    assert falsos["android-02"].calls == [] and falsos[LOJA].calls == []

    de_novo = {d["id"]: d["outcome"] for d in parque.state.distribute(rid)}       # type: ignore[union-attr]
    assert de_novo["android-01"] == "already"                        # pedir de novo não reinstala quem já tem


async def test_aparelho_pendente_instala_ao_entrar_em_servico_antes_da_tarefa(parque: Harness) -> None:
    """A consequência intencional: aparelho SEM linha de estado deixava a tarefa passar; com a versão desejada
    gravada, a porta instala primeiro. É o que entrega o app a quem estava desligado na hora de distribuir."""
    falsos = falsificar_adb(parque)
    devs = parque.state.devices                                      # type: ignore[union-attr]
    rt = devs.get("android-02")
    await devs.stop_instance(rt)
    assert estado(parque, "android-02") is None                      # nunca teve release gerenciada
    rid = release(parque)
    parque.state.distribute(rid)                                     # type: ignore[union-attr]

    mensagens_na_hora_de_instalar: list[int] = []
    instalar = falsos["android-02"].install

    def espiao(path: str, **kw: Any) -> str:
        mensagens_na_hora_de_instalar.append(len(parque.fakes["android-02"].messages))
        return instalar(path, **kw)

    falsos["android-02"].install = espiao                            # type: ignore[method-assign]
    await devs.start_instance(rt)
    await parque.wait(lambda: rt.state == InstanceState.online, what="android-02 ligado")

    run = parque.run(["android-02"])
    detail = await parque.wait_run(run.id, timeout=90)
    assert detail.status == "completed"
    assert mensagens_na_hora_de_instalar == [0]                      # instalou UMA vez, e antes de a tarefa agir
    assert len(parque.fakes["android-02"].messages) == 1
    await pronto(parque, "android-02", rid)


# ==================================================================== falha não vira laço
async def test_falha_na_entrega_nao_se_repete_sozinha_e_fica_nomeada(parque: Harness) -> None:
    falsos = falsificar_adb(parque)
    rid = release(parque)
    falsos["android-01"].install_error = "INSTALL_FAILED_INSUFFICIENT_STORAGE"

    # A execução é criada ANTES de a entrega falhar: é o caso em que o trabalho já está no sistema quando o
    # aplicativo vira problema. Depois da falha, a recusa acontece bem mais cedo — ver o final deste teste.
    run = parque.state.runs.create(RunCreate(command=COMMAND, instance_ids=["android-01"],   # type: ignore[union-attr]
                                             mode="plan", idempotency_key="entrega-falhou-0001"))
    await parque.wait(lambda: parque.state.repo.run_row(run.id)["status"] == "planned",      # type: ignore[union-attr]
                      what="plano pronto antes da falha")

    parque.state.distribute(rid)                                     # type: ignore[union-attr]
    await parque.wait(lambda: bool(estado(parque, "android-01")) and estado(parque, "android-01")["state"] == "install_failed",
                      what="entrega falhou e ficou registrada")

    # A tarefa começa: NENHUMA outra instalação é disparada — o item para com o motivo e espera uma pessoa.
    parque.state.runs.start(run.id)                                  # type: ignore[union-attr]
    detail = await parque.wait_run(run.id, statuses=("completed_with_issues", "failed", "awaiting_person", "completed"), timeout=60)
    obj = detail.objectives[0]
    assert obj.status == "waiting_user"
    assert "não é repetida sozinha" in (obj.blocked_reason or "")
    assert falsos["android-01"].calls.count("install") == 1          # UMA tentativa, nenhuma a mais
    assert not parque.fakes["android-01"].messages

    # E uma execução NOVA nem chega a ser planejada: o pré-voo recusa antes, com o mesmo motivo (#51).
    with pytest.raises(RunError) as recusa:
        parque.state.runs.create(RunCreate(command=COMMAND, instance_ids=["android-01"],     # type: ignore[union-attr]
                                           mode="execute", idempotency_key="entrega-falhou-0002"))
    assert recusa.value.code == "preflight"
    assert recusa.value.details["devices"][0]["code"] == "app_failed"

    # Pedir de novo é a nova tentativa, explícita — e agora o problema foi resolvido.
    falsos["android-01"].install_error = None
    saida = {d["id"]: d["outcome"] for d in parque.state.distribute(rid)}         # type: ignore[union-attr]
    assert saida["android-01"] == "started"
    await pronto(parque, "android-01", rid)


async def test_falha_antes_de_mudar_estado_tambem_para_na_primeira_tentativa(parque: Harness) -> None:
    """`install_on` pode levantar antes de tocar no estado (aqui: arquivo sumiu do catálogo). Sem o invólucro, o
    aparelho seguiria "pronto para tentar" e o mesmo job voltaria a cada tick, para sempre."""
    falsos = falsificar_adb(parque)
    rid = release(parque)
    st = parque.state
    assert st is not None
    catalogo = st.cfg.path(st.release_repo.release_row(rid)["catalog_dir"])
    (catalogo / "base.apk").unlink()

    st.distribute(rid)
    await parque.wait(lambda: bool(estado(parque, "android-01")) and estado(parque, "android-01")["state"] == "install_failed",
                      what="falha antes do estado virou estado")
    # A frase mudou com o storage de evidências e de APKs (item 5.7): o catálogo passou a poder viver em outro
    # servidor, então "ausente no catálogo" virou uma frase que distingue as duas causas — arquivo que não está
    # NESTE servidor, e artefato adulterado. O que o teste guarda é que a falha é NOMEADA e que o motivo diz que
    # o artefato não foi adulterado; o operador precisa saber que é questão de onde o arquivo está.
    detalhe = estado(parque, "android-01")["detail"]
    assert "Arquivo ausente NESTE servidor" in detalhe and "NÃO está adulterado" in detalhe
    assert falsos["android-01"].calls == []                          # nem chegou ao aparelho
    porta = st._app_resolver(st.devices.get("android-01"), PACOTE, {"status": "pending"})  # noqa: SLF001
    assert porta is not None and porta[1] is None                    # sem job: só uma pessoa destrava


# ==================================================================== o resolvedor, caso a caso
async def test_objetivo_em_andamento_nao_tem_o_app_trocado_no_meio(parque: Harness) -> None:
    falsificar_adb(parque)
    devs = parque.state.devices                                      # type: ignore[union-attr]
    rt = devs.get("android-02")
    await devs.stop_instance(rt)
    rid = release(parque)
    parque.state.distribute(rid)                                     # type: ignore[union-attr]
    resolver = parque.state._app_resolver                            # type: ignore[union-attr]  # noqa: SLF001

    assert resolver(rt, PACOTE, {"status": "running"}) is None       # termina na versão que tem
    pendente = resolver(rt, PACOTE, {"status": "pending"})
    assert pendente is not None and callable(pendente[1]) and "distribuída para o parque" in pendente[0]
    assert resolver(rt, "com.outro.app", {"status": "pending"}) is None           # a linha é por pacote


async def test_versao_distribuida_que_vai_para_quarentena_para_de_ser_entregue(parque: Harness) -> None:
    falsificar_adb(parque)
    devs = parque.state.devices                                      # type: ignore[union-attr]
    rt = devs.get("android-02")
    await devs.stop_instance(rt)
    rid = release(parque)
    parque.state.distribute(rid)                                     # type: ignore[union-attr]
    parque.state.releases.quarantine(rid, reason="travou no feed")   # type: ignore[union-attr]

    porta = parque.state._app_resolver(rt, PACOTE, {"status": "pending"})          # type: ignore[union-attr]  # noqa: SLF001
    assert porta is not None and porta[1] is None
    assert "não pode mais ser entregue" in porta[0] and "quarantined" in porta[0]


# ==================================================================== a rota
async def test_verbo_distribute_responde_aparelho_por_aparelho(parque: Harness) -> None:
    from app.main import create_app

    falsificar_adb(parque)
    candidata = release(parque, codigo=5, promovida=False)
    promovida = release(parque, codigo=6)
    app = create_app(parque.cfg, state=parque.state)
    app.state.poc = parque.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(f"/api/releases/{candidata}/lifecycle", json={"verb": "distribute"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "lifecycle_refused"
        assert "PROMOVIDA" in r.json()["detail"]["message"]

        r = await c.post(f"/api/releases/{promovida}/lifecycle", json={"verb": "distribute"})
        assert r.status_code == 200
        aparelhos = {d["id"]: d["outcome"] for d in r.json()["devices"]}
        assert aparelhos == {"android-01": "started", "android-02": "started"}     # a loja não entra
    await pronto(parque, "android-01", promovida)
    await pronto(parque, "android-02", promovida)


# ==================================================================== "instalar em todos agora"
IDS = ["android-01", "android-02", "android-03"]


async def _tudo_desligado(h: Harness, *, vagas: int) -> None:
    st = h.state
    assert st is not None
    for rt in st.devices.devices.values():
        await st.devices.stop_instance(rt)
    # `auto_start_devices` fica DESLIGADO de propósito: o botão é um pedido explícito, e tem de funcionar sozinho.
    st.settings.update({"max_online_devices": vagas, "min_online_dwell_s": 0})


async def test_instalar_em_todos_agora_percorre_o_parque_respeitando_as_vagas(harness: Harness) -> None:
    import asyncio

    falsos = falsificar_adb(harness)
    await _tudo_desligado(harness, vagas=1)
    rid = release(harness)
    devs = harness.state.devices                                     # type: ignore[union-attr]
    pico = 0

    async def vigia() -> None:
        nonlocal pico
        while True:
            pico = max(pico, devs.slots_used())
            await asyncio.sleep(0.01)

    v = asyncio.create_task(vigia())
    saida = harness.state.distribute(rid, eager=True)                # type: ignore[union-attr]
    assert {d["outcome"] for d in saida} == {"pending"} and "rodízio vai ligá-lo" in saida[0]["reason"]
    for iid in IDS:
        await pronto(harness, iid, rid)
    v.cancel()
    assert pico == 1                                                 # nunca mais de um aparelho ligado: a vaga é respeitada
    assert all(falsos[i].calls.count("install") == 1 for i in IDS)   # cada um instalou uma vez só
    await harness.wait(lambda: not harness.state._entrega_imediata,  # type: ignore[union-attr]  # noqa: SLF001
                       what="entrega imediata encerrada sozinha")


async def test_sem_pedir_agora_aparelho_desligado_continua_desligado(harness: Harness) -> None:
    """O modo padrão não liga ninguém: quem está desligado recebe quando pegar tarefa. Ligar o parque é só sob pedido."""
    import asyncio

    falsificar_adb(harness)
    await _tudo_desligado(harness, vagas=1)
    rid = release(harness)
    harness.state.distribute(rid)                                    # type: ignore[union-attr]
    await harness.ticks(3)                                           # o despacho olhou três vezes e não ligou ninguém
    devs = harness.state.devices                                     # type: ignore[union-attr]
    assert all(devs.get(i).state != InstanceState.online for i in IDS)
    assert all(estado(harness, i)["desired_release_id"] == rid for i in IDS)


async def test_falha_num_aparelho_nao_interrompe_a_entrega_aos_demais(harness: Harness) -> None:
    falsos = falsificar_adb(harness)
    await _tudo_desligado(harness, vagas=1)
    rid = release(harness)
    falsos["android-02"].install_error = "INSTALL_FAILED_INSUFFICIENT_STORAGE"

    harness.state.distribute(rid, eager=True)                        # type: ignore[union-attr]
    await pronto(harness, "android-01", rid)
    await pronto(harness, "android-03", rid)
    await harness.wait(lambda: not harness.state._entrega_imediata,  # type: ignore[union-attr]  # noqa: SLF001
                       what="entrega imediata encerrada")
    assert estado(harness, "android-02")["state"] == "install_failed"
    assert falsos["android-02"].calls.count("install") == 1          # falhou UMA vez e saiu da fila: espera uma pessoa


async def test_reinicio_no_meio_preserva_o_desejado_e_volta_ao_modo_padrao(tmp_path: Path) -> None:
    """A pressa vive em memória; a versão desejada, no banco. Cair no meio não perde nada — só deixa de ter pressa.

    Neste harness os aparelhos de mentira voltam LIGADOS a cada partida. É o cenário mais exigente para esta
    afirmação: ligados, livres e com versão desejada gravada — e mesmo assim nada se instala sozinho, porque sem
    pressa a entrega só acontece pela porta, quando chega uma tarefa.
    """
    import asyncio

    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        falsificar_adb(h)
        await _tudo_desligado(h, vagas=1)
        rid = release(h)
        h.state.scheduler.rollout_source = None                      # type: ignore[union-attr]  # congela: nada instala antes da queda
        h.state.distribute(rid, eager=True)                          # type: ignore[union-attr]
        assert h.state._entrega_imediata == {rid}                    # type: ignore[union-attr]  # noqa: SLF001
        await h.crash()

        await h.boot()
        falsos = falsificar_adb(h)
        assert h.state._entrega_imediata == set()                    # type: ignore[union-attr]  # noqa: SLF001
        await h.ticks(3)                                             # três voltas do despacho, nenhuma instalação
        assert all(falsos[i].calls == [] for i in IDS)               # ninguém instalou às pressas
        linhas = [h.state.release_repo.app_state(i, PACOTE) for i in IDS]          # type: ignore[union-attr]
        assert all(r["desired_release_id"] == rid and r["installed_release_id"] is None for r in linhas)

        # E o modo padrão segue de pé: chega uma tarefa, a porta instala antes dela.
        run = h.run(["android-01"])
        assert (await h.wait_run(run.id, timeout=90)).status == "completed"
        assert falsos["android-01"].calls.count("install") == 1 and falsos["android-02"].calls == []
    finally:
        if h.state is not None:
            await h.state.stop()


# ==================================================================== aparelho de OUTRA máquina, desligado
@pytest_asyncio.fixture
async def parque_com_remoto(tmp_path: Path) -> AsyncIterator[Harness]:
    """android-04 é de outro servidor: o rodízio daqui nunca o liga (`_rotate` exclui `rt.external`)."""
    h = Harness(tmp_path, 4, store=LOJA, external={"android-04": "127.0.0.1:15555"})
    await h.boot()
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_instalar_em_todos_agora_nao_promete_ligar_o_remoto_e_encerra_a_entrega(parque_com_remoto: Harness) -> None:
    """Dois defeitos no mesmo lugar (#44, #84): a resposta prometia o que o código não faz, e o conjunto da
    entrega imediata ficava aberto para sempre — duas consultas por tick, o aviso de encerramento nunca saindo."""
    h = parque_com_remoto
    assert h.state is not None
    falsificar_adb(h)
    remoto = h.state.devices.devices["android-04"]
    await h.state.devices.stop_instance(remoto)
    remoto.state = InstanceState.stopped                             # externo parado: ninguém aqui o liga
    rid = release(h)

    saida = {d["id"]: d for d in h.state.distribute(rid, eager=True)}
    assert "rodízio" not in saida["android-04"]["reason"]            # a promessa falsa saiu
    assert "outro servidor" in saida["android-04"]["reason"] and "quando ele voltar" in saida["android-04"]["reason"]
    # A versão desejada CONTINUA gravada nele: o caminho não-imediato (porta do app) segue valendo na volta.
    assert estado(h, "android-04")["desired_release_id"] == rid

    for iid in ("android-01", "android-02"):
        await pronto(h, iid, rid)
    # ...e a entrega imediata encerra sozinha, em vez de ficar presa esperando quem ninguém liga.
    await h.wait(lambda: rid not in h.state._entrega_imediata,       # type: ignore[union-attr]  # noqa: SLF001
                 timeout=30, what="entrega imediata encerrada mesmo com remoto parado")


async def test_remoto_online_continua_recebendo_a_entrega_imediata(parque_com_remoto: Harness) -> None:
    """A regra é "parado em outro servidor", não "é de outro servidor": o remoto NO AR recebe como qualquer um."""
    h = parque_com_remoto
    assert h.state is not None
    falsos = falsificar_adb(h)
    assert h.state.devices.devices["android-04"].state == InstanceState.online
    rid = release(h)

    saida = {d["id"]: d for d in h.state.distribute(rid, eager=True)}
    assert saida["android-04"]["outcome"] in ("started", "pending")
    await pronto(h, "android-04", rid)
    assert falsos["android-04"].calls.count("install") == 1
