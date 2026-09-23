"""Buscar da loja: o aparelho com Play Store é a FONTE oficial do aplicativo.

O usuário instala o app ali pela loja, com a conta dele. O backend só copia, por adb, o que o Android já tem em
disco — nada é baixado da rede — e o conjunto entra no pipeline de sempre: inspeção pelo conteúdo, catálogo imutável,
assinatura aprovada de propósito. O que estes testes protegem:

* a origem fica registrada (`store`) sem nenhum dado da conta usada na loja;
* buscar duas vezes a mesma versão não copia nada;
* a busca importa SÓ o que veio da loja — o que estiver solto na inbox não é importado de carona nem apagado;
* o `pull` só aceita APK instalado: não é uma porta genérica para tirar arquivo do aparelho.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from app.devices.adb import Adb, AdbError
from app.devices.installer import AppInstaller
from app.models import InstalledAppState, ReleaseState
from app.releases.catalog import ReleaseValidationError

from .conftest import Harness
from .test_app_releases import INSTAGRAM, QA_APK, FakeAdbDevice, FakeRt, StubInspector, build_service, part

LOJA = "android-03"
RAIZ = "/data/app/~~Zx9==/com.instagram.android-Qw3=="


class AdbDaLoja(FakeAdbDevice):
    """A loja: o app JÁ está instalado (pela Play Store) e os arquivos saem por `pull`."""

    def __init__(self, version_code: int, arquivos: list[str], splits: list[str]):
        super().__init__()
        self.version_code = version_code
        self.installed = {"version_name": f"{version_code}.0.0", "version_code": version_code,
                          "splits": ["base", *splits], "first_install_time": "2026-09-18 09:00:00",
                          "last_update_time": "2026-09-18 09:00:00", "paths": [f"{RAIZ}/{a}" for a in arquivos]}
        self.pulled: list[str] = []

    def pull(self, remote: str, local: str, *, timeout: float = 0) -> None:
        self.pulled.append(remote)
        shutil.copy2(QA_APK, local)
        with open(local, "ab") as fh:                              # bytes próprios por versão e por arquivo
            fh.write(bytes(self.version_code % 89 + len(self.pulled)))


def inspetor(version_code: int, splits: dict[str, str]) -> StubInspector:
    """Arquivo copiado do aparelho → o que o aapt2 leria dele. O nome no aparelho (`split_config.xhdpi.apk`) não é o
    nome do split (`config.xhdpi`): quem manda é o manifest."""
    nomes = {"base.apk": part(version_code=version_code, version_name=f"{version_code}.0.0", abis=["x86_64"])}
    for arquivo, split in splits.items():
        nomes[arquivo] = part(version_code=version_code, version_name=f"{version_code}.0.0", split_name=split,
                              abis=["x86_64"])
    return StubInspector(nomes)


SPLITS = {"split_config.xhdpi.apk": "config.xhdpi", "split_config.en.apk": "config.en"}


def loja_com(version_code: int) -> tuple[FakeRt, AdbDaLoja]:
    adb = AdbDaLoja(version_code, ["base.apk", *SPLITS], list(SPLITS.values()))
    return FakeRt(adb, LOJA), adb


# ==================================================================== o serviço
async def test_buscar_da_loja_cataloga_o_conjunto_com_origem_store(tmp_path: Path) -> None:
    svc, repo, db = build_service(tmp_path, inspetor(500, SPLITS))
    try:
        rt, adb = loja_com(500)
        r = await svc.sync_from_store(rt, INSTAGRAM, AppInstaller(None))
        assert r["outcome"] == "imported" and r["version_code"] == 500
        assert r["status"] == ReleaseState.validated.value           # primeira do pacote: assinatura ainda por aprovar

        rel = svc.list_releases(INSTAGRAM)[0]
        assert rel.source_type == "store"
        assert rel.source_reference is not None and rel.source_reference.startswith(f"Play Store via {LOJA} em 20")
        assert "@" not in rel.source_reference                       # nenhum dado da conta usada na loja
        assert rel.artifact_type == "split_set"
        assert sorted(f.split_name for f in rel.files if f.role == "split") == ["config.en", "config.xhdpi"]
        assert len(adb.pulled) == 3 and all(p.startswith("/data/app/") for p in adb.pulled)

        # a cópia não fica na inbox, importada ou não
        assert not list(svc.cfg.apk_inbox.glob("loja-*"))
        # e o que a loja tem fica registrado, sem release associada: ela é fonte, não destino
        linha = repo.app_state(LOJA, INSTAGRAM)
        assert linha["observed_version_code"] == 500 and linha["state"] == InstalledAppState.installed.value
        assert linha["installed_release_id"] is None and linha["desired_release_id"] is None
    finally:
        db.close()


async def test_buscar_sem_versao_nova_nao_copia_nada(tmp_path: Path) -> None:
    svc, repo, db = build_service(tmp_path, inspetor(500, SPLITS))
    try:
        rt, adb = loja_com(500)
        primeira = await svc.sync_from_store(rt, INSTAGRAM, AppInstaller(None))
        copiados = len(adb.pulled)
        segunda = await svc.sync_from_store(rt, INSTAGRAM, AppInstaller(None))
        assert segunda["outcome"] == "unchanged" and segunda["release_id"] == primeira["release_id"]
        assert len(adb.pulled) == copiados                           # nenhum byte saiu do aparelho de novo
        assert len(svc.list_releases(INSTAGRAM)) == 1
    finally:
        db.close()


async def test_versao_nova_na_loja_vira_release_nova_e_a_antiga_continua(tmp_path: Path) -> None:
    svc, repo, db = build_service(tmp_path, inspetor(500, SPLITS))
    try:
        rt, _ = loja_com(500)
        r500 = await svc.sync_from_store(rt, INSTAGRAM, AppInstaller(None))
        svc.approve_signature(r500["release_id"])

        svc.inspector = inspetor(501, SPLITS)                        # type: ignore[assignment]  # a Play Store atualizou o app
        rt2, _ = loja_com(501)
        r501 = await svc.sync_from_store(rt2, INSTAGRAM, AppInstaller(None))
        assert r501["outcome"] == "imported" and r501["release_id"] != r500["release_id"]
        assert r501["status"] == ReleaseState.installable.value      # mesma assinatura já aprovada: entra liberada
        assert sorted(x.version_code for x in svc.list_releases(INSTAGRAM)) == [500, 501]

        estado = svc.store_status(LOJA, INSTAGRAM)
        assert estado["store_version_code"] == 501 and estado["catalog_version_code"] == 501
        assert estado["update_available"] is False
    finally:
        db.close()


async def test_loja_a_frente_do_catalogo_avisa_que_ha_versao_nova(tmp_path: Path) -> None:
    svc, repo, db = build_service(tmp_path, inspetor(500, SPLITS))
    try:
        rt, _ = loja_com(500)
        await svc.sync_from_store(rt, INSTAGRAM, AppInstaller(None))
        # a loja atualizou sozinha e alguém só releu o estado dela
        repo.upsert_app_state(LOJA, INSTAGRAM, observed_version_code=502, observed_version_name="502.0.0")
        estado = svc.store_status(LOJA, INSTAGRAM)
        assert estado["update_available"] is True and estado["catalog_version_code"] == 500
    finally:
        db.close()


async def test_buscar_importa_so_a_subpasta_e_nao_mexe_nos_soltos_da_inbox(tmp_path: Path) -> None:
    svc, repo, db = build_service(tmp_path, inspetor(500, SPLITS))
    try:
        solto = svc.cfg.apk_inbox / "deixado-pelo-usuario.apk"
        solto.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(QA_APK, solto)

        rt, _ = loja_com(500)
        await svc.sync_from_store(rt, INSTAGRAM, AppInstaller(None))
        assert solto.is_file()                                        # nem importado de carona, nem apagado
        assert [r.package_name for r in svc.list_releases()] == [INSTAGRAM]
    finally:
        db.close()


async def test_app_que_nao_esta_na_loja_da_erro_que_diz_o_que_fazer(tmp_path: Path) -> None:
    svc, repo, db = build_service(tmp_path, inspetor(500, SPLITS))
    try:
        rt = FakeRt(FakeAdbDevice(), LOJA)                            # nada instalado
        with pytest.raises(ReleaseValidationError, match="toque em Instalar"):
            await svc.sync_from_store(rt, INSTAGRAM, AppInstaller(None))
        assert repo.app_state(LOJA, INSTAGRAM)["state"] == InstalledAppState.missing.value
        assert svc.list_releases() == []
    finally:
        db.close()


async def test_nome_de_arquivo_inesperado_vindo_do_aparelho_e_recusado(tmp_path: Path) -> None:
    svc, repo, db = build_service(tmp_path, inspetor(500, {}))
    try:
        adb = AdbDaLoja(500, ["base.apk", "nome com espaco.apk"], [])
        with pytest.raises(ReleaseValidationError, match="nome de arquivo inesperado"):
            await svc.sync_from_store(FakeRt(adb, LOJA), INSTAGRAM, AppInstaller(None))
        assert not list(svc.cfg.apk_inbox.glob("loja-*"))             # a cópia parcial não fica para trás
    finally:
        db.close()


# ==================================================================== o adb
@pytest.mark.parametrize("remoto", [
    "/sdcard/DCIM/foto.jpg",                                          # armazenamento do usuário
    "/data/data/com.instagram.android/databases/direct.db",           # dados do app
    "/data/app/../data/com.instagram.android/shared_prefs/x.apk",     # travessia
    "/data/app/~~a==/pkg-b==/base.apk; rm -rf /",                     # injeção
    "base.apk",
])
def test_pull_so_copia_apk_instalado(remoto: str) -> None:
    """Não é um `adb pull` genérico: a única coisa que o projeto tem motivo para tirar de um aparelho é o pacote."""
    adb = Adb.__new__(Adb)
    with pytest.raises(AdbError, match="só se copia APK instalado"):
        adb.pull(remoto, "qualquer.apk")


# ==================================================================== as rotas
@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> AsyncIterator[Harness]:
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


async def test_sem_loja_configurada_as_rotas_dizem_isso(harness: Harness) -> None:
    async with _cliente(harness) as c:
        r = await c.post("/api/store/sync", json={"package": INSTAGRAM})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "no_store"
        estado = (await c.get("/api/store", params={"package": INSTAGRAM})).json()
        assert estado["configured"] is False and estado["instance_id"] is None


async def test_a_loja_nao_assume_um_aplicativo_por_omissao(parque: Harness) -> None:
    """Item 6.1: sem `package`, a loja recusa em vez de cair no Instagram — pelo painel ela só sabia buscar um app."""
    async with _cliente(parque) as c:
        for rota in ("/api/store/sync", "/api/store/open-listing"):
            r = await c.post(rota, json={})
            assert r.status_code == 400 and r.json()["detail"]["code"] == "package_required", rota
        r = await c.get("/api/store")
        assert r.status_code == 400 and r.json()["detail"]["code"] == "package_required"


async def test_buscar_com_a_loja_desligada_ou_sob_controle_manual_da_409_com_motivo(parque: Harness) -> None:
    devs = parque.state.devices                                       # type: ignore[union-attr]
    rt = devs.get(LOJA)
    async with _cliente(parque) as c:
        _, lease = devs.request_control(rt)                           # o usuário está navegando na loja pelo painel
        r = await c.post("/api/store/sync", json={"package": INSTAGRAM})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "device_busy"
        assert "controle manual" in r.json()["detail"]["message"]
        devs.release_control(rt, lease)

        await devs.stop_instance(rt)
        for rota in ("/api/store/sync", "/api/store/open-listing"):
            r = await c.post(rota, json={"package": INSTAGRAM})
            assert r.status_code == 409 and r.json()["detail"]["code"] == "not_online", rota


async def test_estado_da_loja_e_release_de_origem_store_nao_quebram_a_listagem(parque: Harness) -> None:
    st = parque.state
    assert st is not None
    st.release_repo.save_release(
        release_id="rel-da-loja", package_name=INSTAGRAM, version_name="500.0.0", version_code=500,
        artifact_type="split_set", signature_sha256="f" * 64, min_sdk=28, target_sdk=35, abis=["x86_64"],
        catalog_dir="apks/x", source_type="store", source_reference=f"Play Store via {LOJA} em 2026-09-18",
        status=ReleaseState.validated, detail=None, files=[])
    st.release_repo.upsert_app_state(LOJA, INSTAGRAM, observed_version_code=500, observed_version_name="500.0.0",
                                     state="installed")
    async with _cliente(parque) as c:
        lista = await c.get("/api/releases")
        assert lista.status_code == 200 and lista.json()[0]["source_type"] == "store"
        estado = (await c.get("/api/store", params={"package": INSTAGRAM})).json()
        assert estado == {**estado, "configured": True, "instance_id": LOJA, "package": INSTAGRAM,
                          "store_version_code": 500, "catalog_version_code": 500, "update_available": False}
        assert estado["state"] == "online"

