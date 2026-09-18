"""Fase 0B — release de APK como artefato: importar, inspecionar, validar, instalar e verificar.

Regra que estes testes protegem: o nome do arquivo não decide nada. Pacote, versão, splits e assinatura vêm do
próprio pacote, e o que está instalado é lido DO APARELHO, nunca assumido pelo banco.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import load_config
from app.db import Database
from app.devices.installer import AppInstaller, DeviceProfile, InstallError, compatibility, drift_of
from app.devices.sdk import SdkTools
from app.events import EventBus
from app.models import InstalledAppState, ReleaseState
from app.releases import catalog
from app.releases.catalog import CandidateSet, ReleaseValidationError
from app.releases.inspector import ApkInfo, ApkInspector, sha256_of
from app.releases.repository import ReleaseRepository
from app.releases.service import ReleaseService

from .conftest import make_config

QA_APK = Path(__file__).resolve().parents[2] / "qa-app" / "dist" / "qa-messenger.apk"
INSTAGRAM = "com.instagram.android"
LAUNCHER = ("com.google.android.apps.nexuslauncher", "com.google.android.apps.nexuslauncher.NexusLauncherActivity")


def instalador() -> AppInstaller:
    """Os mesmos passos da sonda de abertura, em escala de teste: o prazo real (90 s) só pesa quando o app falha."""
    return AppInstaller(None, launch_deadline_s=0.3, launch_settle_s=0.02, launch_poll_s=0.01)


# ---------------------------------------------------------------- inspeção real
def test_inspeciona_um_apk_de_verdade_sem_olhar_o_nome_do_arquivo(tmp_path: Path) -> None:
    """O APK do app de QA está versionado no repositório: dá para provar a inspeção com um pacote real."""
    tools = SdkTools(load_config())
    if not tools.can_inspect_apk():
        pytest.skip("aapt2/apksigner não instalados nesta máquina")
    enganoso = tmp_path / "instagram-latest-v999.apk"        # nome mentiroso de propósito
    shutil.copy2(QA_APK, enganoso)
    info = ApkInspector(tools).inspect(enganoso)
    assert info.package_name == "com.pocqa.messenger"        # veio do pacote, não do nome
    assert info.version_code == 1 and info.version_name == "1.0.0"
    assert info.min_sdk == 26 and info.target_sdk == 34
    assert len(info.signature_sha256) == 64
    assert info.sha256 == sha256_of(QA_APK)[0]
    assert info.is_base and info.role == "base"


def test_arquivo_que_nao_e_apk_e_recusado(tmp_path: Path) -> None:
    tools = SdkTools(load_config())
    if not tools.can_inspect_apk():
        pytest.skip("aapt2/apksigner não instalados nesta máquina")
    falso = tmp_path / "instagram.apk"
    falso.write_bytes(b"isto nao e um apk")
    with pytest.raises(Exception) as exc:
        ApkInspector(tools).inspect(falso)
    assert "apk" in str(exc.value).lower() or "pacote" in str(exc.value).lower()


# ---------------------------------------------------------------- leitura da assinatura
ATUAL, ANTIGA, CARIMBO = "a1" * 32, "b2" * 32, "c3" * 32

# Formato medido no Instagram real vindo da Play Store (18/09/2026): v3.1 com rotação de chave e Source Stamp.
SAIDA_ROTACAO = f"""Verifies
Verified using v3 scheme (APK Signature Scheme v3): true
Verified using v3.1 scheme (APK Signature Scheme v3.1): true
Verified for SourceStamp: true
Number of signers: 1
Signer (minSdkVersion=33, maxSdkVersion=2147483647) certificate DN: CN=Meta Platforms Inc.
Signer (minSdkVersion=33, maxSdkVersion=2147483647) certificate SHA-256 digest: {ATUAL}
Signer (minSdkVersion=33, maxSdkVersion=2147483647) public key SHA-256 digest: {"d4" * 32}
Signer (minSdkVersion=24, maxSdkVersion=32) certificate DN: CN=Kevin Systrom, O=Instagram Inc
Signer (minSdkVersion=24, maxSdkVersion=32) certificate SHA-256 digest: {ANTIGA}
Source Stamp Signer certificate DN: CN=Android, O=Google Inc.
Source Stamp Signer certificate SHA-256 digest: {CARIMBO}
INFO: SourceStamp: No digests are available in the source stamp for signature scheme: 31
"""


def test_assinatura_com_rotacao_de_chave_usa_a_chave_atual() -> None:
    """Antes, só `Signer #1` era entendido: o Instagram da Play Store era recusado como "assinatura ilegível"."""
    from app.releases.inspector import signer_sha256

    assert signer_sha256(SAIDA_ROTACAO) == ATUAL                      # a faixa mais nova, que o Android 13+ verifica


def test_carimbo_da_loja_nunca_e_confundido_com_quem_assinou() -> None:
    """O Source Stamp é a marca de QUEM DISTRIBUIU (a Play Store). Tomá-lo por assinatura faria todo app vindo da
    loja parecer do mesmo dono — e a trava de "assinatura diferente da aprovada" deixaria de valer."""
    from app.releases.inspector import signer_sha256

    so_carimbo = f"Verifies\nSource Stamp Signer certificate SHA-256 digest: {CARIMBO}\n"
    assert signer_sha256(so_carimbo) is None
    assert signer_sha256(SAIDA_ROTACAO) != CARIMBO


def test_formato_classico_continua_valendo() -> None:
    from app.releases.inspector import signer_sha256

    classico = f"Signer #1 certificate DN: CN=Android Debug\nSigner #1 certificate SHA-256 digest: {ATUAL.upper()}\n"
    assert signer_sha256(classico) == ATUAL                           # normalizado para minúsculas, como antes
    assert signer_sha256("DOES NOT VERIFY\nERROR: APK Signature Scheme v2 signer #1: Malformed") is None


# ---------------------------------------------------------------- validação do conjunto
def part(**over: Any) -> ApkInfo:
    base = dict(path=Path("base.apk"), package_name=INSTAGRAM, version_code=447, version_name="447.0.0",
                sha256="a" * 64, size_bytes=10, signature_sha256="f" * 64, split_name=None, min_sdk=28,
                target_sdk=35, abis=["arm64-v8a"], locales=["en"], densities=["320"])
    base.update(over)
    return ApkInfo(**base)  # type: ignore[arg-type]


class StubInspector:
    """Inspetor de mentira: devolve metadados combinados por arquivo, para exercitar a validação do conjunto."""

    def __init__(self, by_name: dict[str, ApkInfo]):
        self.by_name = by_name

    def available(self) -> bool:
        return True

    def inspect(self, path: Path) -> ApkInfo:
        return _with_path(self.by_name[path.name], path)


def _with_path(info: ApkInfo, path: Path) -> ApkInfo:
    """Metadados combinados no teste, mas hash e tamanho REAIS do arquivo: é o hash que garante a imutabilidade
    do catálogo, então inventá-lo tornaria o teste mentiroso."""
    digest, size = (sha256_of(path) if path.is_file() else (info.sha256, info.size_bytes))
    return ApkInfo(path=path, package_name=info.package_name, version_code=info.version_code,
                   version_name=info.version_name, sha256=digest, size_bytes=size,
                   signature_sha256=info.signature_sha256, split_name=info.split_name, min_sdk=info.min_sdk,
                   target_sdk=info.target_sdk, abis=info.abis, locales=info.locales, densities=info.densities)


def candidate(tmp_path: Path, names: list[str], *, container: bool = False) -> CandidateSet:
    files = []
    for n in names:
        p = tmp_path / n
        p.write_bytes(b"x")
        files.append(p)
    return CandidateSet(label="conjunto", files=files, container=container)


def test_conjunto_valido_de_splits_vira_uma_release(tmp_path: Path) -> None:
    names = {"base.apk": part(), "config.arm64_v8a.apk": part(split_name="config.arm64_v8a", sha256="b" * 64)}
    rel = catalog.validate(candidate(tmp_path, list(names)), StubInspector(names))  # type: ignore[arg-type]
    assert rel.artifact_type == "split_set" and rel.version_code == 447
    assert rel.release_id.startswith(f"{INSTAGRAM}-447-")
    assert len(rel.parts) == 2 and rel.parts[0].is_base          # o base vem primeiro


def test_conjunto_sem_base_e_recusado(tmp_path: Path) -> None:
    names = {"config.pt.apk": part(split_name="config.pt")}
    with pytest.raises(ReleaseValidationError, match="exatamente um base"):
        catalog.validate(candidate(tmp_path, list(names)), StubInspector(names))  # type: ignore[arg-type]


def test_split_repetido_e_recusado(tmp_path: Path) -> None:
    names = {"base.apk": part(), "a.apk": part(split_name="config.pt"), "b.apk": part(split_name="config.pt")}
    with pytest.raises(ReleaseValidationError, match="repetido"):
        catalog.validate(candidate(tmp_path, list(names)), StubInspector(names))  # type: ignore[arg-type]


def test_assinatura_divergente_entre_arquivos_reprova_o_conjunto(tmp_path: Path) -> None:
    names = {"base.apk": part(), "config.pt.apk": part(split_name="config.pt", signature_sha256="e" * 64)}
    with pytest.raises(ReleaseValidationError, match="assinaturas diferentes"):
        catalog.validate(candidate(tmp_path, list(names)), StubInspector(names))  # type: ignore[arg-type]


def test_version_code_divergente_reprova_o_conjunto(tmp_path: Path) -> None:
    names = {"base.apk": part(), "config.pt.apk": part(split_name="config.pt", version_code=448)}
    with pytest.raises(ReleaseValidationError, match="versionCode divergente"):
        catalog.validate(candidate(tmp_path, list(names)), StubInspector(names))  # type: ignore[arg-type]


def test_pacote_diferente_do_esperado_e_recusado(tmp_path: Path) -> None:
    names = {"base.apk": part(package_name="com.outro.app")}
    with pytest.raises(ReleaseValidationError, match="o pacote é"):
        catalog.validate(candidate(tmp_path, list(names)), StubInspector(names),  # type: ignore[arg-type]
                         expected_package=INSTAGRAM)


def test_conteiner_nasce_marcado_como_conjunto_nao_verificado(tmp_path: Path) -> None:
    names = {"base.apk": part(), "config.pt.apk": part(split_name="config.pt", sha256="c" * 64)}
    rel = catalog.validate(candidate(tmp_path, list(names), container=True), StubInspector(names))  # type: ignore[arg-type]
    assert rel.artifact_type == "unverified_split_set"


def test_identidade_do_conjunto_vem_do_conteudo(tmp_path: Path) -> None:
    a = catalog.set_hash([part(), part(split_name="config.pt", sha256="b" * 64)])
    b = catalog.set_hash([part(split_name="config.pt", sha256="b" * 64), part()])     # ordem diferente
    c = catalog.set_hash([part(), part(split_name="config.pt", sha256="d" * 64)])     # conteúdo diferente
    assert a == b and a != c


# ---------------------------------------------------------------- compatibilidade
def perfil(**over: Any) -> DeviceProfile:
    base = dict(abi="x86_64", abis=["x86_64", "arm64-v8a"], sdk=34, locale="en-US", density=320)
    base.update(over)
    return DeviceProfile(**base)  # type: ignore[arg-type]


def test_sdk_insuficiente_reprova_antes_de_tentar_instalar() -> None:
    v = compatibility(min_sdk=35, abis=["x86_64"], profile=perfil(sdk=34))
    assert v.blocked and "SDK 35" in v.reason


def test_abi_ausente_reprova() -> None:
    v = compatibility(min_sdk=28, abis=["armeabi-v7a"], profile=perfil())
    assert v.blocked


def test_abi_nativa_do_aparelho_e_compativel() -> None:
    assert compatibility(min_sdk=28, abis=["x86_64", "arm64-v8a"], profile=perfil()).verdict == "compatible"


def test_abi_traduzida_fica_incerta_ate_a_sonda_de_abertura() -> None:
    v = compatibility(min_sdk=28, abis=["arm64-v8a"], profile=perfil())
    assert v.verdict == "uncertain" and v.translated_abi == "arm64-v8a" and not v.blocked


def test_pacote_sem_biblioteca_nativa_serve_em_qualquer_abi() -> None:
    assert compatibility(min_sdk=26, abis=[], profile=perfil()).verdict == "compatible"


def test_faixa_de_densidade_vem_do_aparelho() -> None:
    assert perfil(density=320).density_bucket == "xhdpi"
    assert perfil(density=480).density_bucket == "xxhdpi"


# ---------------------------------------------------------------- divergência
def test_divergencia_de_versao_e_detectada() -> None:
    from app.devices.installer import InstalledApp

    state, drift, detail = drift_of(InstalledApp(present=True, version_code=448, version_name="448.0"),
                                    expected_version_code=447)
    assert state is InstalledAppState.version_drift and drift == "app_version_drift" and "448" in (detail or "")


def test_pacote_ausente_e_detectado() -> None:
    from app.devices.installer import InstalledApp

    state, drift, _ = drift_of(InstalledApp(present=False), expected_version_code=447)
    assert state is InstalledAppState.missing and drift == "app_missing"


def test_split_faltando_e_detectado() -> None:
    from app.devices.installer import InstalledApp

    state, drift, _ = drift_of(InstalledApp(present=True, version_code=447, splits=["base"]),
                               expected_version_code=447, expected_splits=["base", "config.pt"])
    assert state is InstalledAppState.version_drift and drift == "split_mismatch"


# ---------------------------------------------------------------- aparelho de mentira para instalar
class FakeExecutor:
    async def run(self, fn: Any, *args: Any, timeout: float = 0, label: str = "") -> Any:
        return fn(*args)


class FakeAdbDevice:
    """Aparelho de mentira que responde como o `adb`: guarda o que foi instalado e devolve o estado observado."""

    def __init__(self, density: int = 320, **props: Any):
        self.props = {"ro.product.cpu.abi": "x86_64", "ro.product.cpu.abilist": "x86_64,arm64-v8a",
                      "ro.build.version.sdk": "34", "ro.product.locale": "en-US", **props}
        self.density = density
        self.installed: dict[str, Any] | None = None
        self.paths_installed: list[str] = []
        self.install_error: str | None = None
        self.launch_dies = False
        # Build que recusa voltar de versão mesmo com `-d`. Existe porque o Android real varia nisso, e o
        # comportamento do sistema não pode depender de ter dado sorte.
        self.refuse_downgrade = False
        self.focus: tuple[str | None, str | None] = (None, None)
        self.calls: list[str] = []

    def getprop(self, name: str) -> str:
        return str(self.props.get(name, ""))

    def wm_density(self) -> int:
        return self.density

    @staticmethod
    def _meta(path: str) -> dict[str, Any] | None:
        """O aparelho de mentira lê o `metadata.json` do catálogo: versão vem do CONTEÚDO, como no aparelho real."""
        arquivo = Path(path).parent / "metadata.json"
        return json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.is_file() else None

    def _put(self, paths: list[str], *, allow_downgrade: bool = False) -> None:
        from app.devices.adb import AdbError

        if self.install_error:
            raise AdbError(self.install_error)
        meta = self._meta(paths[0])
        codigo = int(meta["versionCode"]) if meta else 447
        nome = str(meta["versionName"]) if meta else "447.0.0"
        if self.installed and codigo < self.installed["version_code"] and (not allow_downgrade or self.refuse_downgrade):
            raise AdbError("INSTALL_FAILED_VERSION_DOWNGRADE: o aparelho recusou instalar por cima uma versão "
                           "mais antiga.")
        self.paths_installed = list(paths)
        splits = ["base"] + [Path(p).stem for p in paths if Path(p).stem != "base"]
        self.installed = {"version_name": nome, "version_code": codigo, "splits": splits,
                          "first_install_time": "2026-09-17 10:00:00", "last_update_time": "2026-09-17 10:00:00",
                          "paths": list(paths)}

    def install(self, path: str, *, timeout: float = 0, allow_downgrade: bool = False) -> str:
        self.calls.append("install -d" if allow_downgrade else "install")
        self._put([path], allow_downgrade=allow_downgrade)
        return "Success"

    def install_multiple(self, paths: list[str], *, timeout: float = 0, allow_downgrade: bool = False) -> str:
        self.calls.append("install-multiple -d" if allow_downgrade else "install-multiple")
        self._put(paths, allow_downgrade=allow_downgrade)
        return "Success"

    def package_info(self, package: str) -> dict[str, Any] | None:
        return dict(self.installed) if self.installed else None

    def start_app(self, package: str, activity: Any = None) -> None:
        self.focus = LAUNCHER if self.launch_dies else (package, ".MainActivity")

    def current_focus(self) -> tuple[str | None, str | None]:
        """Mesmo contrato do `Adb.current_focus`: (pacote, atividade), ou (None, None) sem janela em foco."""
        return self.focus

    def is_installed(self, package: str) -> bool:
        return self.installed is not None

    def uninstall(self, package: str, *, timeout: float = 0) -> None:
        self.installed = None


class FakeRt:
    def __init__(self, adb: FakeAdbDevice, instance_id: str = "android-01"):
        self.id = instance_id
        self.adb = adb
        self.executor = FakeExecutor()
        self.app_versions: dict[str, str] = {"com.instagram.android": "velho(1)"}


def build_service(tmp_path: Path, inspector: Any) -> tuple[ReleaseService, ReleaseRepository, Database]:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_path)
    db.migrate()
    repo = ReleaseRepository(db)
    return ReleaseService(cfg, repo, inspector, EventBus(db)), repo, db


def put_in_inbox(inbox: Path, names: list[str]) -> None:
    """Arquivos reais na inbox (conteúdo do APK do QA), para o hash em streaming ser verdadeiro."""
    inbox.mkdir(parents=True, exist_ok=True)
    for i, n in enumerate(names):
        target = inbox / n
        shutil.copy2(QA_APK, target)
        with open(target, "ab") as fh:
            fh.write(b"\x00" * (i + 1))          # cada arquivo com hash diferente


# ---------------------------------------------------------------- importação ponta a ponta
async def test_importa_valida_cataloga_e_so_instala_depois_de_aprovar_a_assinatura(tmp_path: Path) -> None:
    names = {"base.apk": part(), "config.arm64_v8a.apk": part(split_name="config.arm64_v8a", sha256="b" * 64)}
    svc, repo, db = build_service(tmp_path, StubInspector(names))
    try:
        put_in_inbox(svc.cfg.apk_inbox, list(names))
        results = svc.import_inbox(source_reference="baixado à mão", expected_package=INSTAGRAM)
        # base.apk e config.*.apk largados soltos formam UM conjunto: o agrupamento é por pacote/versão/assinatura,
        # lidos do próprio pacote, não pelo nome do arquivo.
        assert len(results) == 1 and results[0].ok

        releases = svc.list_releases()
        assert len(releases) == 1
        rel = releases[0]
        assert rel.package_name == INSTAGRAM and rel.version_code == 447
        assert rel.status is ReleaseState.validated                  # assinatura ainda não aprovada
        assert "não aprovada" in (rel.detail or "")

        catalog_dir = svc.cfg.path(repo.release_row(rel.id)["catalog_dir"])
        assert (catalog_dir / "metadata.json").is_file() and (catalog_dir / "base.apk").is_file()
        assert str(rel.version_code) in catalog_dir.name             # caminho vem do conteúdo, não do nome original
        assert not list(svc.cfg.apk_inbox.glob("*.apk"))             # a inbox foi esvaziada

        rt = FakeRt(FakeAdbDevice())
        with pytest.raises(ReleaseValidationError, match="não pode ser instalada"):
            await svc.install_on(rt, rel.id, instalador())

        aprovada = svc.approve_signature(rel.id, note="primeira aprovação do operador")
        assert aprovada.status is ReleaseState.installable
    finally:
        db.close()


async def test_instalacao_de_conjunto_usa_install_multiple_e_le_a_versao_do_aparelho(tmp_path: Path) -> None:
    names = {"base.apk": part(), "config.arm64_v8a.apk": part(split_name="config.arm64_v8a", sha256="b" * 64)}
    svc, repo, db = build_service(tmp_path, StubInspector(names))
    try:
        put_in_inbox(svc.cfg.apk_inbox, list(names))
        rel = svc.import_inbox()[0]
        svc.approve_signature(rel.release_id)
        adb = FakeAdbDevice()
        rt = FakeRt(adb)
        state = await svc.install_on(rt, rel.release_id, instalador())
        assert "install-multiple" in adb.calls                       # conjunto é atômico
        assert state["state"] == InstalledAppState.ready.value
        assert state["observed_version_code"] == 447                 # lido DO APARELHO
        assert state["installed_release_id"] == rel.release_id
        assert rt.app_versions == {}                                 # cache da versão (chave das receitas) foi limpo
    finally:
        db.close()


async def test_apk_unico_usa_install_simples(tmp_path: Path) -> None:
    names = {"base.apk": part(abis=[])}
    svc, repo, db = build_service(tmp_path, StubInspector(names))
    try:
        put_in_inbox(svc.cfg.apk_inbox, list(names))
        rel = svc.import_inbox()[0]
        svc.approve_signature(rel.release_id)
        adb = FakeAdbDevice()
        await svc.install_on(FakeRt(adb), rel.release_id, instalador())
        assert adb.calls == ["install"]
    finally:
        db.close()


async def test_falha_de_instalacao_fica_registrada_e_nao_deixa_o_estado_preso(tmp_path: Path) -> None:
    names = {"base.apk": part(abis=[])}
    svc, repo, db = build_service(tmp_path, StubInspector(names))
    try:
        put_in_inbox(svc.cfg.apk_inbox, list(names))
        rel = svc.import_inbox()[0]
        svc.approve_signature(rel.release_id)
        adb = FakeAdbDevice()
        adb.install_error = "INSTALL_FAILED_NO_MATCHING_ABIS: sem biblioteca nativa para x86_64"
        with pytest.raises(InstallError):
            await svc.install_on(FakeRt(adb), rel.release_id, instalador())
        row = repo.app_state("android-01", INSTAGRAM)
        assert row["state"] == InstalledAppState.install_failed.value
        assert row["pending_op"] is None                             # não fica preso em "instalando"
    finally:
        db.close()


async def test_app_que_nao_sobrevive_ao_abrir_nao_fica_pronto(tmp_path: Path) -> None:
    """Instalar não prova que roda — especialmente com ABI traduzida. A sonda de abertura é quem decide."""
    names = {"base.apk": part(abis=["arm64-v8a"])}
    svc, repo, db = build_service(tmp_path, StubInspector(names))
    try:
        put_in_inbox(svc.cfg.apk_inbox, list(names))
        rel = svc.import_inbox()[0]
        svc.approve_signature(rel.release_id)
        adb = FakeAdbDevice()
        adb.launch_dies = True
        with pytest.raises(ReleaseValidationError, match="prova de abertura"):
            await svc.install_on(FakeRt(adb), rel.release_id, instalador())
        row = repo.app_state("android-01", INSTAGRAM)
        assert row["state"] == InstalledAppState.verify_failed.value
        assert row["installed_release_id"] is None                   # não conta como instalado e pronto
    finally:
        db.close()


# ---------------------------------------------------------------- sonda de abertura
NADA = (None, None)
IG_PRINCIPAL = (INSTAGRAM, "com.instagram.android.activity.MainTabActivity")
IG_LOGIN = (INSTAGRAM, "com.instagram.nux.activity.BloksSignedOutFragmentActivity")


class FocoRoteirizado:
    """Aparelho cujo foco segue um roteiro, uma leitura por quadro; o último quadro se repete."""

    def __init__(self, roteiro: list[tuple[str | None, str | None]]):
        self.roteiro = roteiro
        self.leituras = 0

    def start_app(self, package: str, activity: Any = None) -> None:
        pass

    def current_focus(self) -> tuple[str | None, str | None]:
        quadro = self.roteiro[min(self.leituras, len(self.roteiro) - 1)]
        self.leituras += 1
        return quadro

    def is_installed(self, package: str) -> bool:
        return True


def test_foco_lido_do_dumpsys_real() -> None:
    """O contrato que os aparelhos de mentira imitam, conferido contra linhas medidas no emulador (API 34)."""
    from app.devices.adb import Adb

    def com_saida(texto: str) -> Adb:
        adb = Adb.__new__(Adb)
        adb._run = lambda *_a, **_k: SimpleNamespace(stdout=texto)  # type: ignore[method-assign]
        return adb

    principal = ("  mCurrentFocus=Window{42268c3 u0 com.instagram.android/"
                 "com.instagram.android.activity.MainTabActivity}\n")
    assert com_saida(principal).current_focus() == IG_PRINCIPAL
    assert com_saida("  mCurrentFocus=null\n").current_focus() == NADA


async def test_sonda_espera_a_primeira_tela_em_vez_de_confundir_foco_nulo_com_falha() -> None:
    """Medido no Instagram real, a frio: segundos sem janela em foco, a tela principal, um instante sem foco na troca
    para a tela de login, e a tela de login. O launcher ainda na frente antes do primeiro quadro também é espera."""
    adb = FocoRoteirizado([NADA] * 5 + [LAUNCHER] * 2 + [IG_PRINCIPAL] * 2 + [NADA] * 2 + [IG_LOGIN])
    sonda = AppInstaller(None, launch_deadline_s=10, launch_settle_s=0.05, launch_poll_s=0.01)
    ok, why = await sonda.launch_probe(FakeRt(adb), INSTAGRAM)
    assert ok, why
    assert adb.leituras >= 12                                   # esperou o roteiro, não desistiu na primeira leitura


async def test_sonda_falha_rapido_quando_o_app_aparece_e_cai() -> None:
    adb = FocoRoteirizado([NADA, IG_PRINCIPAL, LAUNCHER])
    sonda = AppInstaller(None, launch_deadline_s=30, launch_settle_s=5, launch_poll_s=0.01)
    inicio = time.monotonic()
    ok, why = await sonda.launch_probe(FakeRt(adb), INSTAGRAM)
    assert not ok and "saiu do primeiro plano" in why and "nexuslauncher" in why
    assert time.monotonic() - inicio < 3                       # nem o prazo nem a estabilidade precisaram passar


async def test_sonda_desiste_no_prazo_e_diz_o_que_estava_em_foco() -> None:
    sonda = AppInstaller(None, launch_deadline_s=0.1, launch_settle_s=0.02, launch_poll_s=0.01)
    ok, why = await sonda.launch_probe(FakeRt(FocoRoteirizado([NADA])), INSTAGRAM)  # type: ignore[arg-type]
    assert not ok
    assert "não chegou ao primeiro plano" in why and "nenhuma janela" in why
    assert "None" not in why                                    # o defeito medido: a tupla crua virava "(None, None)"

    ok, why = await sonda.launch_probe(FakeRt(FocoRoteirizado([LAUNCHER])), INSTAGRAM)  # type: ignore[arg-type]
    assert not ok and "com.google.android.apps.nexuslauncher/" in why


async def test_sonda_que_ve_o_app_perto_do_prazo_ainda_prova_a_estabilidade() -> None:
    adb = FocoRoteirizado([NADA] * 3 + [IG_PRINCIPAL])
    sonda = AppInstaller(None, launch_deadline_s=0.5, launch_settle_s=1.0, launch_poll_s=0.01)
    ok, why = await sonda.launch_probe(FakeRt(adb), INSTAGRAM)  # type: ignore[arg-type]
    assert ok, why

    # Apareceu e sumiu sem outro app na frente: não se firmou, e o prazo estendido não vira espera sem fim.
    adb = FocoRoteirizado([IG_PRINCIPAL, NADA])
    sonda = AppInstaller(None, launch_deadline_s=0.01, launch_settle_s=0.05, launch_poll_s=0.01)
    ok, why = await sonda.launch_probe(FakeRt(adb), INSTAGRAM)  # type: ignore[arg-type]
    assert not ok and "não se firmou" in why


async def test_artefato_adulterado_no_disco_bloqueia_a_instalacao(tmp_path: Path) -> None:
    names = {"base.apk": part(abis=[])}
    svc, repo, db = build_service(tmp_path, StubInspector(names))
    try:
        put_in_inbox(svc.cfg.apk_inbox, list(names))
        rel = svc.import_inbox()[0]
        svc.approve_signature(rel.release_id)
        catalog_dir = svc.cfg.path(repo.release_row(rel.release_id)["catalog_dir"])
        with open(catalog_dir / "base.apk", "ab") as fh:
            fh.write(b"alteracao silenciosa")
        with pytest.raises(ReleaseValidationError, match="adulterado"):
            await svc.install_on(FakeRt(FakeAdbDevice()), rel.release_id, instalador())
        assert repo.release_row(rel.release_id)["status"] == ReleaseState.invalid.value
    finally:
        db.close()


async def test_divergencia_aparece_na_verificacao(tmp_path: Path) -> None:
    names = {"base.apk": part(abis=[])}
    svc, repo, db = build_service(tmp_path, StubInspector(names))
    try:
        put_in_inbox(svc.cfg.apk_inbox, list(names))
        rel = svc.import_inbox()[0]
        svc.approve_signature(rel.release_id)
        adb = FakeAdbDevice()
        rt = FakeRt(adb)
        await svc.install_on(rt, rel.release_id, instalador())
        adb.installed["version_code"] = 448                          # alguém atualizou o app por fora
        adb.installed["version_name"] = "448.0.0"
        state = await svc.verify_on(rt, INSTAGRAM, instalador())
        assert state["state"] == InstalledAppState.version_drift.value
        assert state["drift_kind"] == "app_version_drift"
    finally:
        db.close()


async def test_queda_durante_a_instalacao_vira_verificacao_e_nao_repeticao(tmp_path: Path) -> None:
    svc, repo, db = build_service(tmp_path, StubInspector({}))
    try:
        repo.upsert_app_state("android-01", INSTAGRAM, pending_op="install", pending_op_at="2026-09-17T10:00:00Z",
                              state=InstalledAppState.installing.value)
        assert svc.reconcile_after_restart() == 1
        row = repo.app_state("android-01", INSTAGRAM)
        assert row["pending_op"] is None
        assert row["state"] == InstalledAppState.verifying.value     # reobservar, nunca reinstalar às cegas
    finally:
        db.close()


async def test_assinatura_diferente_da_aprovada_e_bloqueada(tmp_path: Path) -> None:
    primeira = {"base.apk": part(abis=[])}
    svc, repo, db = build_service(tmp_path, StubInspector(primeira))
    try:
        put_in_inbox(svc.cfg.apk_inbox, ["base.apk"])
        rel = svc.import_inbox()[0]
        svc.approve_signature(rel.release_id)

        outra = {"base.apk": part(abis=[], version_code=448, signature_sha256="9" * 64)}
        svc.inspector = StubInspector(outra)
        put_in_inbox(svc.cfg.apk_inbox, ["base.apk"])
        nova = svc.import_inbox()[0]
        assert nova.status == ReleaseState.invalid.value
        assert "Assinatura diferente" in (nova.reason or "")
    finally:
        db.close()


async def test_arquivos_soltos_de_versoes_diferentes_viram_releases_separadas(tmp_path: Path) -> None:
    names = {"a.apk": part(abis=[]), "b.apk": part(abis=[], version_code=448, version_name="448.0.0")}
    svc, repo, db = build_service(tmp_path, StubInspector(names))
    try:
        put_in_inbox(svc.cfg.apk_inbox, list(names))
        results = svc.import_inbox()
        assert len(results) == 2 and all(r.ok for r in results)
        assert sorted(r.version_code for r in results) == [447, 448]
    finally:
        db.close()


async def test_porta_do_app_bloqueia_despacho_quando_o_aplicativo_nao_esta_pronto(harness: Any) -> None:
    """Segunda das três portas: aparelho pronto, app pronto, sessão pronta. Sem linha de estado, nada muda."""
    state = harness.state
    repo = state.release_repo
    repo.upsert_app_state("android-01", "com.pocqa.messenger", state="version_drift",
                          drift_kind="app_version_drift", detail="alguém atualizou o app por fora")
    run = harness.run(["android-01", "android-02"])
    def bloqueado_por_app() -> bool:
        row = state.db.one("SELECT status FROM objectives WHERE id=?", (f"{run.id}:android-01",))
        return bool(row) and row["status"] == "waiting_user"     # o objetivo só existe depois do planejamento

    await harness.wait(bloqueado_por_app, timeout=30, what="android-01 bloqueado pela porta do app")
    bloqueado = state.db.one("SELECT blocked_reason FROM objectives WHERE id=?", (f"{run.id}:android-01",))
    assert "não está pronto" in (bloqueado["blocked_reason"] or "")
    # o outro aparelho, sem linha de estado, segue normalmente
    await harness.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed"), timeout=90)
    assert harness.fakes["android-02"].messages
    assert not harness.fakes["android-01"].messages


async def test_api_recusa_instalar_release_nao_aprovada_com_erro_visivel(harness: Any, tmp_path: Path) -> None:
    """A recusa tem de chegar a quem chamou: o trabalho roda em segundo plano, então validar só lá dentro
    devolveria 202 e esconderia o problema."""
    import httpx

    from app.main import create_app

    state = harness.state
    state.release_repo.save_release(
        release_id="rel-teste", package_name=INSTAGRAM, version_name="447.0.0", version_code=447,
        artifact_type="single", signature_sha256="f" * 64, min_sdk=28, target_sdk=35, abis=[],
        catalog_dir="apks/x", source_type="inbox", source_reference=None, status=ReleaseState.validated,
        detail="Assinatura ainda não aprovada para este pacote.", files=[])
    app = create_app(harness.cfg, state=state)
    app.state.poc = state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/instances/android-01/app/install", json={"release_id": "rel-teste"})
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "release_not_installable"
        assert "não aprovada" in r.json()["detail"]["message"]
        ausente = await c.post("/api/instances/android-01/app/install", json={"release_id": "nao-existe"})
        assert ausente.status_code == 404
