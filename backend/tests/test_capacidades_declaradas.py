"""Capacidades declaradas: o que o aparelho É, e não só que verbo ele aceita.

O que havia: o modelo respondia a uma pergunta só — "este aparelho aceita este verbo de ciclo de vida?". O
pedido fala em capacidades modeladas e em explicar a limitação ANTES de agendar, e a incompatibilidade só
aparecia no meio do caminho, como `INSTALL_FAILED_NO_MATCHING_ABIS` ou um app que abre e morre porque a imagem é
AOSP. O instalador já LIA o perfil do aparelho a cada instalação (ABI, SDK, idioma, densidade) e jogava fora.

O outro achado deste item (#12): um worker que declarasse `appium: local` era aceito, aparecia na Infraestrutura
com esse modo — e continuava sendo dirigido pelo Appium central pelo túnel. Declaração sem efeito.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.automation.appium_driver import AppiumSession
from app.config import AppiumCfg
from app.devices.avd import capacidades_da_imagem, capacidades_do_avd
from app.devices.compatibilidade import (Capacidades, Requisitos, capacidades_de, motivo_incompativel,
                                         requisitos_de_release)
from app.releases.inspector import exige_gms
from app.workers.protocol import Hello, WorkerDevice

from .conftest import Harness
from .test_workers import _hello

IMAGEM_AOSP = "system-images;android-34;default;x86_64"
IMAGEM_GOOGLE = "system-images;android-34;google_apis_playstore;x86_64"


# ---------------------------------------------------------------- a regra, sozinha
def test_nivel_de_api_menor_que_o_exigido_e_recusado_com_a_frase() -> None:
    porque = motivo_incompativel(Requisitos(min_sdk=34, rotulo="o app"), Capacidades(api_level=30),
                                 aparelho="android-01")
    assert porque and "34" in porque and "30" in porque and "android-01" in porque


def test_abi_que_o_aparelho_nao_executa_e_recusada() -> None:
    porque = motivo_incompativel(Requisitos(abis=["arm64-v8a"], rotulo="o app"),
                                 Capacidades(abis=["x86_64", "x86"]))
    assert porque and "arm64-v8a" in porque and "x86_64" in porque


def test_abi_traduzida_passa_porque_o_aparelho_a_executa() -> None:
    """Estar na lista de ABIs do aparelho já é "ele executa" — inclusive por tradução. A prova final continua
    sendo abrir o app, e recusar aqui proibiria o que funciona."""
    assert motivo_incompativel(Requisitos(abis=["arm64-v8a"]),
                               Capacidades(abis=["x86_64", "arm64-v8a"])) is None


def test_fluxo_que_precisa_de_gms_nao_vai_para_imagem_aosp() -> None:
    porque = motivo_incompativel(Requisitos(requires_gms=True, rotulo="o app"),
                                 Capacidades(play_store=False, system_image=IMAGEM_AOSP))
    assert porque and "Play Services" in porque and IMAGEM_AOSP in porque


def test_capacidade_desconhecida_nunca_vira_recusa() -> None:
    """`None` é "não se sabe", que é diferente de "não tem". Recusar o desconhecido seria a mesma afirmação vaga
    que esta fase existe para eliminar — e proibiria todo aparelho que ainda não subiu."""
    exigente = Requisitos(min_sdk=34, abis=["arm64-v8a"], requires_gms=True)
    assert motivo_incompativel(exigente, Capacidades()) is None


# ---------------------------------------------------------------- de onde a capacidade vem
def test_a_imagem_do_sdk_ja_diz_api_abi_e_gms() -> None:
    assert capacidades_da_imagem(IMAGEM_GOOGLE) == {"api_level": 34, "abis": ["x86_64"], "play_store": True}
    assert capacidades_da_imagem(IMAGEM_AOSP)["play_store"] is False
    assert capacidades_da_imagem("lixo") == {}


def test_config_ini_do_avd_e_lido_sem_adb_e_sem_emulador(tmp_path: Path) -> None:
    """É isto que permite ao worker DECLARAR capacidade no `hello`, antes de qualquer aparelho subir."""
    avd = tmp_path / "worker-01.avd"
    avd.mkdir(parents=True)
    (avd / "config.ini").write_text(
        "avd.ini.encoding=UTF-8\n"
        "image.sysdir.1=system-images\\android-33\\google_apis\\x86_64\\\n"
        "hw.ramSize=2048\n", encoding="utf-8")
    saida = capacidades_do_avd(tmp_path, "worker-01")
    assert saida["api_level"] == 33 and saida["abis"] == ["x86_64"]
    assert saida["play_store"] is True and saida["kind"] == "emulator"
    assert capacidades_do_avd(tmp_path, "avd-inexistente") == {}


def test_o_inspetor_reconhece_dependencia_de_play_services() -> None:
    """A incompatibilidade que nenhuma verificação de ABI pega: numa imagem AOSP o app instala, abre e morre."""
    com_gms = ("package: name='com.x'\n"
               "uses-permission: name='com.google.android.c2dm.permission.RECEIVE'\n")
    sem_gms = ("package: name='com.x'\n"
               "uses-permission: name='android.permission.INTERNET'\n")
    # Quase todo app com SDK de anúncios declara AD_ID, e ela NÃO é dependência de Play Services: o app roda
    # numa imagem AOSP sem ela. Recusar por causa disto proibiria o que funciona.
    so_ad_id = ("package: name='com.x'\n"
                "uses-permission: name='com.google.android.gms.permission.AD_ID'\n")
    assert exige_gms(com_gms) is True
    assert exige_gms(sem_gms) is False
    assert exige_gms(so_ad_id) is False


async def test_o_parque_local_declara_capacidade_com_tudo_desligado(harness: Harness) -> None:
    """O estado normal de quem vai agendar uma execução é o parque desligado — e é aí que a recusa explicada
    precisa existir. A imagem configurada basta, sem ligar nada."""
    rt = harness.state.devices.get("android-01")            # type: ignore[union-attr]
    assert rt.system_image and rt.api_level and rt.abis
    linha = harness.state.db.one("SELECT * FROM instances WHERE id=?", ("android-01",))  # type: ignore[union-attr]
    assert linha["api_level"] == rt.api_level and linha["capabilities_at"]
    dto = harness.state.devices.dto(rt)                     # type: ignore[union-attr]
    assert dto.api_level == rt.api_level and dto.abis == rt.abis


async def test_o_worker_declara_as_capacidades_dos_aparelhos_dele(tmp_path: Path) -> None:
    """A metade que faltava para o aparelho de OUTRA máquina: sem ela o pré-voo não tinha sobre o que decidir."""
    h = Harness(tmp_path, 3, external={"android-03": "127.0.0.1:15555"})
    await h.boot()
    assert h.state is not None
    try:
        reg = h.state.workers
        hello = _hello(verbs=["start", "stop"])
        hello = Hello.model_validate({**hello.model_dump(), "devices": [WorkerDevice(
            serial="emulator-5554", avd_name="w-03", instance_id="android-03", kind="emulator",
            system_image=IMAGEM_AOSP, api_level=30, abis=["arm64-v8a"], play_store=False).model_dump()]})
        reg.autenticar(hello, token=None, enrollment=reg.criar_inscricao())
        h.state.db.execute("UPDATE instances SET worker_id=? WHERE id=?", ("worker-lan-01", "android-03"))
        rt = h.state.devices.get("android-03")
        rt.worker_id = "worker-lan-01"
        h.state.devices.capacidades_do_worker("worker-lan-01", hello.devices)

        assert rt.api_level == 30 and rt.abis == ["arm64-v8a"] and rt.play_store is False
        assert h.state.db.scalar("SELECT api_level FROM instances WHERE id=?", ("android-03",)) == 30
        # E a recusa explicada passa a valer sobre isso, antes de agendar qualquer coisa.
        _gravar_release(h, min_sdk=34)
        rel = h.state.db.one("SELECT * FROM app_releases WHERE id=?", ("rel-teste",))
        assert motivo_incompativel(requisitos_de_release(rel), capacidades_de(rt), aparelho=rt.id) is not None
    finally:
        await h.state.stop()


# ---------------------------------------------------------------- o pré-voo
async def test_distribuir_recusa_o_aparelho_incompativel_e_diz_por_que(tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Mandar instalar o que não roda ali deixaria o aparelho em falha permanente de entrega, e o operador sem
    saber por quê. A versão desejada NEM é gravada."""
    h = Harness(tmp_path, 3)
    await h.boot()
    assert h.state is not None
    try:
        rt = h.state.devices.get("android-01")
        h.state.devices.registrar_capacidades(rt, {"api_level": 28, "abis": ["x86_64"]}, fonte="teste")
        rel = _release_falsa(h, min_sdk=34)
        monkeypatch.setattr(h.state.release_repo, "release_row", lambda _rid: rel)
        saida = h.state.distribute("rel-teste")
        linha = next(x for x in saida if x["id"] == "android-01")
        assert linha["outcome"] == "incompatible" and "28" in linha["reason"]
        assert h.state.release_repo.app_state("android-01", "com.pocqa.messenger") is None
    finally:
        await h.state.stop()


async def test_criar_execucao_recusa_antes_de_agendar(tmp_path: Path) -> None:
    """A regra do pedido: a limitação é explicada ANTES de agendar, não no meio de uma etapa."""
    from app.models import RunCreate
    from app.taskqueue.service import RunError

    h = Harness(tmp_path, 3)
    await h.boot()
    assert h.state is not None
    try:
        rt = h.state.devices.get("android-02")
        h.state.devices.registrar_capacidades(rt, {"api_level": 28, "abis": ["x86_64"], "play_store": False},
                                              fonte="teste")
        _gravar_release(h, min_sdk=34)
        h.state.release_repo.upsert_app_state("android-02", "com.pocqa.messenger",
                                              desired_release_id="rel-teste")
        with pytest.raises(RunError) as saida:
            h.state.runs.create(RunCreate(command="Abra o app", instance_ids=["android-02"],  # type: ignore[arg-type]
                                          mode="execute", idempotency_key="capacidade-1"))
        assert saida.value.code == "app_incompativel"
        assert "android-02" in str(saida.value)
    finally:
        await h.state.stop()


# ---------------------------------------------------------------- appium: local (achado #12)
def test_appium_local_troca_o_servidor_e_o_udid() -> None:
    """O `udid` vem junto de propósito: o serial que o central usa é o do TÚNEL, e o Appium da máquina do worker
    não enxerga esse endereço — lá o aparelho é `emulator-55xx`."""
    ses = AppiumSession(AppiumCfg(), "127.0.0.1:15555", 8200, 7810, 8000)
    assert ses.server_url.startswith("http://127.0.0.1:4723")

    assert ses.apontar_para("http://192.168.0.9:4723", "emulator-5554") is True
    assert ses.server_url == "http://192.168.0.9:4723"
    assert ses.capabilities()["appium:udid"] == "emulator-5554"
    assert ses.apontar_para("http://192.168.0.9:4723", "emulator-5554") is False   # idempotente

    assert ses.apontar_para(None, "127.0.0.1:15555") is True
    assert ses.server_url.startswith("http://127.0.0.1:4723")


async def test_worker_com_appium_local_passa_a_dirigir_os_aparelhos_dele(tmp_path: Path) -> None:
    h = Harness(tmp_path, 3, external={"android-03": "127.0.0.1:15555"})
    await h.boot()
    assert h.state is not None
    try:
        rt = h.state.devices.get("android-03")
        rt.worker_id = "worker-lan-01"
        devices = [WorkerDevice(serial="emulator-5560", avd_name="w-03", instance_id="android-03")]
        mudados = h.state.devices.bind_worker_appium(
            "worker-lan-01", appium_mode="local", appium_url="http://192.168.0.9:4723", devices=devices)
        assert mudados == ["android-03"]
        assert rt.session.server_url == "http://192.168.0.9:4723"
        assert rt.session.capabilities()["appium:udid"] == "emulator-5560"

        # O worker cai: o aparelho volta ao Appium DESTE servidor, que é o caminho provado.
        h.state.devices.bind_worker_appium("worker-lan-01", appium_mode="central", appium_url=None, devices=[])
        assert rt.session.server_url.startswith("http://127.0.0.1:4723")
        assert rt.session.capabilities()["appium:udid"] == rt.serial
    finally:
        await h.state.stop()


async def test_appium_local_sem_serial_do_worker_continua_no_central(tmp_path: Path) -> None:
    """Declarar `appium: local` sem dizer por qual serial ELE enxerga o aparelho não dá sessão nenhuma: mandar o
    Appium dele procurar o serial do túnel seria pedir o que não existe naquela máquina."""
    h = Harness(tmp_path, 3, external={"android-03": "127.0.0.1:15555"})
    await h.boot()
    assert h.state is not None
    try:
        rt = h.state.devices.get("android-03")
        rt.worker_id = "worker-lan-01"
        h.state.devices.bind_worker_appium("worker-lan-01", appium_mode="local",
                                           appium_url="http://192.168.0.9:4723", devices=[])
        assert rt.session.server_url.startswith("http://127.0.0.1:4723")
    finally:
        await h.state.stop()


# ---------------------------------------------------------------- utilidades
def _release_falsa(h: Harness, *, min_sdk: int) -> Any:
    _gravar_release(h, min_sdk=min_sdk)
    return h.state.db.one("SELECT * FROM app_releases WHERE id=?", ("rel-teste",))  # type: ignore[union-attr]


def _gravar_release(h: Harness, *, min_sdk: int, requires_gms: int = 0) -> None:
    h.state.db.execute(  # type: ignore[union-attr]
        "INSERT INTO app_releases(id, package_name, version_name, version_code, artifact_type, signature_sha256,"
        " min_sdk, target_sdk, supported_abis, catalog_dir, source_type, imported_at, status, channel,"
        " requires_gms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("rel-teste", "com.pocqa.messenger", "9.9", 999, "single", "a" * 64, min_sdk, min_sdk, '["x86_64"]',
         "apks/x", "inbox", "2026-09-22T00:00:00Z", "installable", "promoted", requires_gms))
