"""Aparelho-loja, o papel: o projeto GERE o ciclo de vida dela e NUNCA lhe despacha tarefa.

É o inverso do aparelho externo (que não ligamos nem desligamos, mas que recebe tarefa). Cada teste aqui fecha um
caminho pelo qual a Play Store poderia acabar sendo operada como se fosse aparelho do parque — ou pelo qual a conta
Google poderia passar pelo backend.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from app.devices.manager import ControlError
from app.models import InstanceState, ManualInput, ProfileCreate, ReleaseState
from app.social.service import SocialError
from app.taskqueue.service import RunError

from .conftest import Harness

LOJA = "android-03"


@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> AsyncIterator[Harness]:
    """Dois aparelhos de tarefa (android-01, android-02) e a loja (android-03)."""
    h = Harness(tmp_path, 3, store=LOJA)
    await h.boot()
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


def _cliente(h: Harness) -> httpx.AsyncClient:
    from app.main import create_app

    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_loja_nasce_sem_app_e_o_dto_diz_store(parque: Harness) -> None:
    devs = parque.state.devices                                     # type: ignore[union-attr]
    loja, tarefa = devs.dto(devs.get(LOJA)), devs.dto(devs.get("android-01"))
    assert loja.kind == "store" and loja.app_id is None and loja.account_label is None
    assert tarefa.kind == "emulator" and tarefa.app_id == "qa-messenger"          # o parque não muda


async def test_instancia_que_vira_loja_perde_o_app_que_tinha(tmp_path: Path) -> None:
    """`seed` só insere o que falta: sem a correção idempotente, a loja seguiria com o app de quando era de tarefa."""
    antes = Harness(tmp_path, 3)
    await antes.boot()
    assert antes.state.devices.dto(antes.state.devices.get(LOJA)).app_id == "qa-messenger"   # type: ignore[union-attr]
    await antes.state.stop()                                        # type: ignore[union-attr]

    depois = Harness(tmp_path, 3, store=LOJA)                       # mesmo banco, agora com a loja declarada
    await depois.boot()
    try:
        dto = depois.state.devices.dto(depois.state.devices.get(LOJA))            # type: ignore[union-attr]
        assert dto.kind == "store" and dto.app_id is None
    finally:
        await depois.state.stop()                                   # type: ignore[union-attr]


async def test_loja_nunca_recebe_tarefa_e_o_erro_e_nomeado(parque: Harness) -> None:
    with pytest.raises(RunError) as recusa:
        parque.run(["android-01", LOJA])
    assert recusa.value.code == "store_instance" and recusa.value.status == 400
    assert LOJA in recusa.value.message and "Play Store" in recusa.value.message
    assert parque.state.db.scalar("SELECT COUNT(*) FROM runs") == 0                # type: ignore[union-attr]


async def test_perfil_nao_pode_ser_vinculado_a_loja(parque: Harness) -> None:
    """Dizer "aparelho desconhecido" seria mentira; e um perfil ali mandaria execução para a Play Store."""
    with pytest.raises(SocialError) as recusa:
        parque.state.social.create_profile(                          # type: ignore[union-attr]
            ProfileCreate(username="teste.loja", password="senha-inventada-para-teste", instance_id=LOJA))
    assert recusa.value.code == "store_instance"
    assert parque.state.db.scalar("SELECT COUNT(*) FROM instagram_profiles") == 0  # type: ignore[union-attr]


async def test_rodizio_nunca_despeja_a_loja_e_ela_ocupa_uma_vaga(parque: Harness) -> None:
    st = parque.state
    devs = st.devices                                                # type: ignore[union-attr]
    st.settings.update({"auto_start_devices": True, "max_online_devices": 2, "min_online_dwell_s": 0})  # type: ignore[union-attr]
    await devs.stop_instance(devs.get("android-01"))
    loja, vizinho = devs.get(LOJA), devs.get("android-02")
    assert devs.slots_used() == 2                                    # vizinho + LOJA: ela conta como vaga
    # A loja é a mais ociosa de todas: se fosse despejável, seria a primeira escolhida.
    loja.last_activity_mono = 0.0
    vizinho.last_activity_mono = time.monotonic()

    run = parque.run(["android-01"])
    detail = await parque.wait_run(run.id, timeout=90)
    assert detail.status == "completed"
    assert loja.state == InstanceState.online                        # ninguém a desligou
    assert vizinho.state != InstanceState.online                     # a vaga saiu do vizinho: a loja contava


async def test_objetivo_apontando_para_a_loja_para_com_o_motivo(parque: Harness) -> None:
    """Defesa em profundidade. `RunService.create` já recusa a loja; este é o caso em que um objetivo JÁ existente
    aponta para ela (a instância virou loja depois, ou o banco foi mexido): ele para com o motivo, sem operar nada."""
    st = parque.state
    devs = st.devices                                                # type: ignore[union-attr]
    _, lease = devs.request_control(devs.get("android-01"))         # segura o despacho: o objetivo fica pendente
    run = parque.run(["android-01"])
    await parque.wait(
        lambda: st.db.scalar("SELECT COUNT(*) FROM objectives WHERE run_id=?", (run.id,)) == 1,  # type: ignore[union-attr]
        what="objetivo planejado")
    st.db.execute("UPDATE objectives SET instance_id=? WHERE run_id=?", (LOJA, run.id))  # type: ignore[union-attr]
    st.scheduler.wake()                                              # type: ignore[union-attr]

    def bloqueado() -> bool:
        row = st.db.one("SELECT status, blocked_reason FROM objectives WHERE run_id=?", (run.id,))  # type: ignore[union-attr]
        return bool(row) and row["status"] == "waiting_user" and "loja" in (row["blocked_reason"] or "")

    await parque.wait(bloqueado, timeout=30, what="objetivo da loja bloqueado com o motivo")
    assert not parque.fakes[LOJA].messages                           # nada foi operado na loja
    devs.release_control(devs.get("android-01"), lease)


async def test_na_loja_o_texto_passa_mas_a_senha_da_conta_google_nao(parque: Harness) -> None:
    """Decisão 4 do plano (dono, 24/09): a loja opera como os outros aparelhos e aceita texto pelo painel. O que
    continua sem passar pelo backend é a SENHA: campo de senha em foco recusa e manda para a janela do emulador."""
    devs = parque.state.devices                                      # type: ignore[union-attr]
    rt = devs.get(LOJA)
    fake = parque.fakes[LOJA]
    status, lease = devs.request_control(rt)
    assert status == "granted"
    frame = (await devs.observe(rt, timeout=5)).frame_id
    await devs.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, type="text", text="instagram"))
    assert any(c.startswith("type") for c in fake.calls)            # chegou ao aparelho (ex.: a busca da Play Store)

    fake.screen, fake.focused = "login", "login_pin"                 # campo de senha em foco
    frame = (await devs.observe(rt, timeout=5)).frame_id
    with pytest.raises(ControlError) as recusa:
        await devs.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, type="text", text="segredo-da-conta"))
    assert recusa.value.code == "store_password_blocked" and "janela do emulador" in recusa.value.message
    # Navegar pela loja continua possível.
    await devs.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, type="key", key="back"))

    # No parque, texto pelo painel segue funcionando como sempre.
    outro = devs.get("android-01")
    _, lease2 = devs.request_control(outro)
    frame2 = (await devs.observe(outro, timeout=5)).frame_id
    await devs.manual_input(outro, ManualInput(lease_id=lease2, frame_id=frame2, type="text", text="ola"))


async def test_acao_em_lote_rejeita_a_loja_com_motivo(parque: Harness) -> None:
    async with _cliente(parque) as c:
        r = await c.post("/api/instances/bulk", json={"ids": ["android-01", LOJA], "action": "home"})
        assert r.status_code == 202
        corpo = r.json()
        assert corpo["accepted"] == ["android-01"]
        assert corpo["rejected"] == [{"id": LOJA, "reason": "é a loja (Play Store): ações em lote não se aplicam a ela"}]
        # A tecla chegou ao aparelho FALSO — antes ela ia pelo adb real até o emulador com o mesmo serial.
        await parque.wait(lambda: parque.fakes["android-01"].screen == "launcher", 5, "HOME no aparelho falso")


async def test_canario_instalacao_e_rollback_recusam_a_loja_como_alvo(parque: Harness) -> None:
    parque.state.release_repo.save_release(                          # type: ignore[union-attr]
        release_id="rel-loja", package_name="com.instagram.android", version_name="1.0", version_code=1,
        artifact_type="single", signature_sha256="f" * 64, min_sdk=28, target_sdk=35, abis=["x86_64"],
        catalog_dir="apks/x", source_type="inbox", source_reference=None, status=ReleaseState.installable,
        detail=None, files=[])
    async with _cliente(parque) as c:
        for verbo in ("canary", "rollback"):
            r = await c.post("/api/releases/rel-loja/lifecycle", json={"verb": verbo, "instance_id": LOJA})
            assert r.status_code == 409 and r.json()["detail"]["code"] == "store_instance", verbo
        r = await c.post(f"/api/instances/{LOJA}/app/install", json={"release_id": "rel-loja"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "store_instance"
        assert "vem da própria loja" in r.json()["detail"]["message"]
        # Ler o que está instalado na loja continua permitido — é assim que se descobre versão nova.
        r = await c.post(f"/api/instances/{LOJA}/app/verify", json={"package": "com.instagram.android"})
        assert r.status_code == 202
