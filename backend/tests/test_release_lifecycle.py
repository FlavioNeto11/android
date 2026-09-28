"""Fase 6 — canário, promoção, quarentena, rollback e a matriz de invalidação.

O que estes testes protegem, em uma frase cada:

* quem promove uma versão é uma PROVA REGISTRADA num aparelho, não a lembrança de quem clicou;
* uma versão que falhou a prova não é instalada de novo por engano, mas uma pessoa pode mandar tentar outra vez;
* mexer no app **sempre** invalida a sessão observada — e "invalidada" quer dizer *observar antes de pedir a senha*,
  não *pedir a senha*;
* voltar de versão preservando os dados pode ser recusado pelo Android, e a saída dessa recusa é decisão de pessoa.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.db import Database
from app.devices.installer import AppInstaller, DeviceProfile, DowngradeRefused, select_splits
from app.events import EventBus
from app.integrations.app_declarado.conhecimento import do_app
from app.integrations.app_declarado.sessao import SessaoDeclarada
from app.models import InstalledAppState, ProfileCreate, ReleaseChannel, ReleaseState, SessionStatus
from app.planning.catalog import session_provider_of
from app.releases.catalog import ReleaseValidationError
from app.releases.repository import ReleaseRepository
from app.releases.service import ReleaseService
from app.security.secret_store import MemoryKeyProvider, SecretStore
from app.security.sensitive_input import SensitiveInputChannel
from app.social.repository import SocialRepository
from app.social.service import SocialService

from .conftest import make_config
from .fake_instagram import PKG, FakeInstagram
from .test_app_releases import (INSTAGRAM, LAUNCHER, QA_APK, FakeAdbDevice, FakeExecutor, FakeRt, StubInspector,
                                part)
from .test_instagram_auth import SENHA, USUARIO, FakeDevices


# ==================================================================== escolha de splits (função pura)
def perfil(abis: list[str] | None = None, density: int = 320, locale: str = "en-US") -> DeviceProfile:
    return DeviceProfile(abi=(abis or ["x86_64"])[0], abis=abis or ["x86_64", "arm64-v8a"], sdk=34, locale=locale,
                         density=density)


def test_split_de_outra_densidade_e_de_outro_idioma_fica_de_fora() -> None:
    escolha = select_splits(["config.xhdpi", "config.xxhdpi", "config.en", "config.pt"], perfil())
    assert escolha.chosen == ["config.xhdpi", "config.en"]
    assert escolha.skipped == ["config.xxhdpi", "config.pt"]


def test_split_de_abi_segue_a_lista_do_aparelho_e_nao_so_a_abi_principal() -> None:
    """O emulador anuncia `x86_64,arm64-v8a`: o split arm64 serve, por tradução."""
    escolha = select_splits(["config.arm64_v8a", "config.armeabi_v7a"], perfil())
    assert escolha.chosen == ["config.arm64_v8a"] and escolha.skipped == ["config.armeabi_v7a"]


def test_split_que_nao_e_de_configuracao_nunca_e_descartado() -> None:
    """Split de funcionalidade e nome desconhecido entram sempre: descartar por engano quebra o app."""
    escolha = select_splits(["stories", "config.nodpi", "config.xxhdpi", "modulo.config.xhdpi"], perfil())
    assert escolha.chosen == ["stories", "config.nodpi", "modulo.config.xhdpi"]
    assert escolha.skipped == ["config.xxhdpi"]


def test_quando_nenhum_split_da_categoria_serve_entram_todos() -> None:
    """Melhor instalar demais do que instalar um app sem strings ou sem biblioteca nativa."""
    escolha = select_splits(["config.de", "config.fr"], perfil(locale="pt-BR"))
    assert escolha.chosen == ["config.de", "config.fr"] and not escolha.skipped
    assert "nenhum split de lang" in escolha.reason


def test_aparelho_sem_idioma_lido_nao_autoriza_descartar_idioma() -> None:
    escolha = select_splits(["config.en", "config.pt"], perfil(locale=""))
    assert escolha.chosen == ["config.en", "config.pt"] and not escolha.skipped


def test_densidade_vira_faixa_antes_de_comparar() -> None:
    assert select_splits(["config.hdpi", "config.xhdpi"], perfil(density=240)).chosen == ["config.hdpi"]


# ---------------------------------------------------------------- item 6.6: o descarte com um conjunto COMPLETO
#: O conjunto que um bundle do Instagram publica — o que a Play Store recorta antes de entregar a um aparelho.
#: Até aqui o filtro nunca tinha descartado nada em produção, porque a loja já entrega à VM só os splits dela: o
#: conjunto real passava pela função sem exercitar o caminho do DESCARTE.
CONJUNTO_DE_LOJA = [
    "config.arm64_v8a", "config.armeabi_v7a", "config.x86", "config.x86_64",
    "config.ldpi", "config.mdpi", "config.hdpi", "config.xhdpi", "config.xxhdpi", "config.xxxhdpi",
    "config.en", "config.pt", "config.es", "config.hi", "config.ar",
    "config.nodpi", "stories", "reels.config.xhdpi",
]


def test_conjunto_completo_de_loja_descarta_o_que_nao_serve() -> None:
    """Um aparelho x86_64/320 dpi/en-US fica com um split de cada categoria — e mais nada de configuração."""
    escolha = select_splits(CONJUNTO_DE_LOJA, perfil(abis=["x86_64"]))
    assert escolha.chosen == ["config.x86_64", "config.xhdpi", "config.en",
                              "config.nodpi", "stories", "reels.config.xhdpi"]
    assert escolha.skipped == ["config.arm64_v8a", "config.armeabi_v7a", "config.x86",
                               "config.ldpi", "config.mdpi", "config.hdpi", "config.xxhdpi", "config.xxxhdpi",
                               "config.pt", "config.es", "config.hi", "config.ar"]
    assert escolha.filtered and "12 split(s)" in escolha.reason


def test_conjunto_completo_num_celular_arm64_de_outra_densidade_e_idioma() -> None:
    """O mesmo conjunto, outro aparelho: a escolha muda inteira. É o que prova que o filtro OLHA o aparelho."""
    escolha = select_splits(CONJUNTO_DE_LOJA, perfil(abis=["arm64-v8a"], density=480, locale="pt-BR"))
    assert escolha.chosen[:3] == ["config.arm64_v8a", "config.xxhdpi", "config.pt"]
    assert "config.x86_64" in escolha.skipped and "config.xhdpi" in escolha.skipped


def test_uma_tabela_de_densidade_so_no_projeto() -> None:
    """Havia duas, com limites diferentes: 190 dpi era `mdpi` para o split e `hdpi` para a variante de receita."""
    from app.devices.installer import density_bucket
    from app.devices.manager import _density_bucket

    for dpi in (0, 120, 160, 190, 240, 320, 400, 420, 480, 560, 640):
        esperado = density_bucket(dpi, desconhecida="desconhecida")
        assert _density_bucket(dpi) == esperado, dpi
    assert density_bucket(190) == "mdpi" and density_bucket(400) == "xhdpi"


def test_release_diz_a_quem_o_conjunto_serve() -> None:
    """"Serve a: x86_64 / xhdpi" — o conjunto copiado da loja é o da VM-loja, e isso não estava em lugar nenhum."""
    from app.releases.repository import serve_a

    assert serve_a(["x86_64"], ["config.xhdpi"]) == ["x86_64", "xhdpi"]
    assert serve_a(["arm64-v8a"], ["config.xxhdpi", "config.en"]) == ["arm64-v8a", "xxhdpi"]
    assert serve_a([], []) == []


# ==================================================================== ambiente: release + Instagram no mesmo banco
@dataclass(slots=True)
class Ambiente:
    """O serviço de release e o domínio social sobre o MESMO banco, ligados pelo gancho que o AppState liga.

    É a única forma honesta de provar a matriz de invalidação: se o teste ligasse os dois na mão de um jeito
    diferente do que a aplicação faz, ele provaria o teste, não o sistema.
    """

    svc: ReleaseService
    repo: ReleaseRepository
    social: SocialService
    social_repo: SocialRepository
    auth: SessaoDeclarada
    app: FakeInstagram
    db: Database


def montar(tmp_path: Path, app: FakeInstagram | None = None) -> Ambiente:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    ajustes = cfg.file.contas.ajustes("com.instagram.android")
    ajustes.settle_s = 0.01
    ajustes.open_timeout_s = 0.5
    ajustes.submit_wait_s = 6
    ajustes.auth_cooldown_s = 0        # o intervalo entre tentativas tem teste próprio na Fase 2
    # `db_dsn` e nao `db_path`: assim o teste segue `TEST_DATABASE_URL` e roda de verdade no PostgreSQL
    # quando a suite e apontada para la. Com `db_path` ele abriria SQLite mesmo dentro da corrida do
    # outro banco — cobertura que parece existir e nao existe.
    db = Database(cfg.db_dsn)
    db.migrate()
    bus = EventBus(db)
    repo = ReleaseRepository(db)
    svc = ReleaseService(cfg, repo, StubInspector({}), bus)  # type: ignore[arg-type]
    social_repo = SocialRepository(db)
    secrets = SecretStore(db, MemoryKeyProvider())
    social = SocialService(social_repo, secrets, bus, known_instances=lambda: ["android-01", "android-02"])
    tela = app or FakeInstagram(stored_password=SENHA)
    auth = SessaoDeclarada(do_app(PKG), cfg, FakeDevices(tela), social_repo, secrets,
                           SensitiveInputChannel(lambda: True), bus)
    auth.focus_poll_s = 0.01
    # Exatamente a ligação do AppState: instalar, atualizar ou voltar de versão invalida a sessão observada — e
    # só do app que TEM sessão, que é o que o registro de aplicativos responde.
    svc.on_app_changed = lambda iid, pkg, motivo: (
        social_repo.invalidate_sessions_of_instance(iid, reason=motivo)
        if session_provider_of(pkg) == "instagram" else 0)
    return Ambiente(svc=svc, repo=repo, social=social, social_repo=social_repo, auth=auth, app=tela, db=db)


class AdbComTela(FakeAdbDevice):
    """Um aparelho só: o que instala o APK é o mesmo que mostra a tela do Instagram.

    Sem isso, "atualizar o app" e "a sessão continuou aberta" seriam dois universos separados e o teste não provaria
    nada sobre a matriz de invalidação.
    """

    def __init__(self, app: FakeInstagram, **kw: Any):
        super().__init__(**kw)
        self.app = app

    def start_app(self, package: str, activity: Any = None) -> None:
        if self.launch_dies:
            self.app.force_stop(package)      # abre e cai: a tela volta para o launcher
            return
        self.app.start_app(package, activity)

    def current_focus(self) -> tuple[str | None, str | None]:
        atual = self.app.current_package()
        return (atual, ".MainActivity") if atual else LAUNCHER

    def uninstall(self, package: str, *, timeout: float = 0) -> None:
        super().uninstall(package, timeout=timeout)
        self.app.clear_data(package)          # desinstalar leva os dados do app junto — e com eles a sessão


class Aparelho:
    def __init__(self, app: FakeInstagram, instance_id: str = "android-02", **kw: Any):
        self.id = instance_id
        self.io = app
        self.adb = AdbComTela(app, **kw)
        self.executor = FakeExecutor()
        self.app_versions: dict[str, str] = {}


def importar(env: Ambiente, codigo: int, *, splits: tuple[str, ...] = ()) -> str:
    """Importa e libera uma versão. Os bytes mudam com a versão para o hash — e o id da release — mudarem também."""
    nomes = {"base.apk": part(version_code=codigo, version_name=f"{codigo}.0.0")}
    for s in splits:
        nomes[f"{s}.apk"] = part(version_code=codigo, version_name=f"{codigo}.0.0", split_name=s)
    env.svc.inspector = StubInspector(nomes)  # type: ignore[assignment]
    inbox = env.svc.cfg.apk_inbox
    inbox.mkdir(parents=True, exist_ok=True)
    for i, nome in enumerate(nomes):
        alvo = inbox / nome
        shutil.copy2(QA_APK, alvo)
        with open(alvo, "ab") as fh:
            fh.write(bytes(codigo % 97 + i + 1))
    saidas = env.svc.import_inbox()
    assert len(saidas) == 1 and saidas[0].ok, (saidas[0].reason if saidas else "nada importado")
    env.svc.approve_signature(saidas[0].release_id)
    return str(saidas[0].release_id)


def instalador() -> AppInstaller:
    return AppInstaller(None, launch_deadline_s=0.3, launch_settle_s=0.02, launch_poll_s=0.01)


# ==================================================================== canário, quarentena e promoção
async def test_canario_que_falha_vai_para_quarentena_e_bloqueia_novas_instalacoes(tmp_path: Path) -> None:
    env = montar(tmp_path)
    try:
        rel = importar(env, 447)
        rt = Aparelho(env.app, "android-01")
        rt.adb.launch_dies = True                                   # instala, mas o app não fica em pé
        with pytest.raises(ReleaseValidationError, match="prova de abertura"):
            await env.svc.start_canary(rt, rel, instalador())

        dto = env.repo.release_dto(env.repo.release_row(rel))
        assert dto.channel is ReleaseChannel.quarantined
        assert "canário android-01 falhou" in (dto.channel_detail or "")
        # A prova ficou registrada com aparelho e etapa: instalar deu certo, abrir não.
        assert [(v.stage, v.ok) for v in dto.validations] == [("install", True), ("launch", False)]

        outro = Aparelho(FakeInstagram(), "android-02")
        with pytest.raises(ReleaseValidationError, match="quarentena"):
            await env.svc.install_on(outro, rel, instalador())
        assert outro.adb.installed is None                          # nem chegou a tocar no aparelho
    finally:
        env.db.close()


async def test_canario_que_passa_permite_promover_e_a_promocao_exige_prova(tmp_path: Path) -> None:
    env = montar(tmp_path)
    try:
        rel = importar(env, 447)
        rt = Aparelho(env.app, "android-01")

        with pytest.raises(ReleaseValidationError, match="Só promove quem está em canário"):
            env.svc.promote(rel)

        estado = await env.svc.start_canary(rt, rel, instalador())
        assert estado["state"] == InstalledAppState.ready.value
        assert env.repo.release_row(rel)["channel"] == ReleaseChannel.canary.value

        promovida = env.svc.promote(rel, note="ok no canário")
        assert promovida.channel is ReleaseChannel.promoted
        assert promovida.canary_instance_id == "android-01"          # promover não apaga onde a prova aconteceu
        assert env.svc.promoted_release(INSTAGRAM).id == rel
    finally:
        env.db.close()


async def test_promocao_sem_prova_de_abertura_e_recusada(tmp_path: Path) -> None:
    """O caminho perigoso: marcar como canário e promover sem nunca ter rodado. Tem de ser recusado."""
    env = montar(tmp_path)
    try:
        rel = importar(env, 447)
        env.repo.set_channel(rel, ReleaseChannel.canary, canary_instance_id="android-01")
        with pytest.raises(ReleaseValidationError, match="Falta a prova de instalar"):
            env.svc.promote(rel)

        env.repo.record_validation(rel, "android-01", stage="install", ok=True, detail=None)
        with pytest.raises(ReleaseValidationError, match="Falta a prova de abrir"):
            env.svc.promote(rel)

        env.repo.record_validation(rel, "android-01", stage="launch", ok=False, detail="não abriu")
        with pytest.raises(ReleaseValidationError, match="última prova de abrir .* falhou"):
            env.svc.promote(rel)
    finally:
        env.db.close()


async def test_uma_prova_antiga_que_passou_nao_promove_depois_de_uma_que_falhou(tmp_path: Path) -> None:
    env = montar(tmp_path)
    try:
        rel = importar(env, 447)
        env.repo.set_channel(rel, ReleaseChannel.canary, canary_instance_id="android-01")
        env.repo.record_validation(rel, "android-01", stage="install", ok=True, detail=None)
        env.repo.record_validation(rel, "android-01", stage="launch", ok=True, detail="abriu")
        env.repo.record_validation(rel, "android-01", stage="launch", ok=False, detail="parou de abrir")
        with pytest.raises(ReleaseValidationError, match="parou de abrir"):
            env.svc.promote(rel)
    finally:
        env.db.close()


async def test_sair_da_quarentena_exige_novo_canario_de_proposito(tmp_path: Path) -> None:
    """Quarentena bloqueia a instalação de rotina; ela não é uma prisão perpétua, mas a saída é explícita."""
    env = montar(tmp_path)
    try:
        rel = importar(env, 447)
        env.svc.quarantine(rel, reason="suspeita de travar")
        rt = Aparelho(env.app, "android-01")
        with pytest.raises(ReleaseValidationError, match="quarentena"):
            await env.svc.install_on(rt, rel, instalador())

        await env.svc.start_canary(rt, rel, instalador())            # decisão explícita de quem opera
        dto = env.repo.release_dto(env.repo.release_row(rel))
        assert dto.channel is ReleaseChannel.canary and "depois da quarentena" in (dto.channel_detail or "")
        assert env.svc.promote(rel).channel is ReleaseChannel.promoted
    finally:
        env.db.close()


# ==================================================================== parque em versões diferentes
async def test_dois_aparelhos_ficam_em_versoes_diferentes_sem_um_mexer_no_outro(tmp_path: Path) -> None:
    env = montar(tmp_path)
    try:
        v447 = importar(env, 447)
        um = FakeRt(FakeAdbDevice(), "android-01")
        await env.svc.install_on(um, v447, instalador())

        v448 = importar(env, 448)
        dois = FakeRt(FakeAdbDevice(), "android-02")
        await env.svc.install_on(dois, v448, instalador())

        por_aparelho = {e.instance_id: e for e in env.repo.list_app_state(INSTAGRAM)}
        assert por_aparelho["android-01"].observed_version_code == 447
        assert por_aparelho["android-02"].observed_version_code == 448
        assert por_aparelho["android-01"].installed_release_id == v447
        assert por_aparelho["android-02"].installed_release_id == v448

        # Cada release lista só o aparelho que realmente a tem — é o que a tela de aplicativos mostra.
        catalogo = {r.version_code: r.devices for r in env.svc.list_releases(INSTAGRAM)}
        assert catalogo[447] == ["android-01"] and catalogo[448] == ["android-02"]

        # Promover a 448 não instala nada sozinho: o parque só muda quando alguém manda.
        env.repo.set_channel(v448, ReleaseChannel.canary, canary_instance_id="android-02")
        env.svc.promote(v448)
        assert env.repo.app_state("android-01", INSTAGRAM)["observed_version_code"] == 447
    finally:
        env.db.close()


async def test_a_assinatura_do_aparelho_vem_da_release_que_ele_tem_instalada(tmp_path: Path) -> None:
    """É esta consulta que separa receitas entre aparelhos: versão igual com assinatura diferente não é o mesmo app."""
    env = montar(tmp_path)
    try:
        v447 = importar(env, 447)
        rt = FakeRt(FakeAdbDevice(), "android-01")
        await env.svc.install_on(rt, v447, instalador())
        assinatura = env.db.scalar(
            "SELECT r.signature_sha256 FROM device_app_state d JOIN app_releases r ON r.id = d.installed_release_id"
            " WHERE d.instance_id=? AND d.package_name=?", ("android-01", INSTAGRAM))
        assert assinatura == env.repo.release_row(v447)["signature_sha256"]
        assert env.db.scalar(
            "SELECT r.signature_sha256 FROM device_app_state d JOIN app_releases r ON r.id = d.installed_release_id"
            " WHERE d.instance_id=? AND d.package_name=?", ("android-02", INSTAGRAM)) is None
    finally:
        env.db.close()


# ==================================================================== splits escolhidos por aparelho
async def test_split_de_outra_configuracao_nao_e_instalado_nem_vira_divergencia(tmp_path: Path) -> None:
    """A parte delicada: filtrar splits sem que a verificação passe a cobrar o que não foi instalado."""
    env = montar(tmp_path)
    try:
        rel = importar(env, 447, splits=("config.xhdpi", "config.xxhdpi"))
        rt = FakeRt(FakeAdbDevice(density=320), "android-01")        # 320 = xhdpi
        estado = await env.svc.install_on(rt, rel, instalador())
        assert estado["state"] == InstalledAppState.ready.value
        assert estado["drift_kind"] is None
        assert sorted(estado["observed_splits"]) == ["base", "config.xhdpi"]
        assert not any("xxhdpi" in p for p in rt.adb.paths_installed)
    finally:
        env.db.close()


async def test_verificar_depois_de_instalar_filtrado_nao_cobra_o_split_que_ficou_de_fora(tmp_path: Path) -> None:
    """A armadilha da atomicidade: filtrar splits sem registrar a escolha faria toda verificação seguinte acusar
    divergência — e a porta do app bloquearia o aparelho para sempre, por uma decisão que foi nossa."""
    env = montar(tmp_path)
    try:
        rel = importar(env, 447, splits=("config.xhdpi", "config.xxhdpi"))
        rt = FakeRt(FakeAdbDevice(density=320), "android-01")
        await env.svc.install_on(rt, rel, instalador())
        estado = env.repo.app_state_dto(env.repo.app_state("android-01", INSTAGRAM))
        assert estado.expected_splits == ["base", "config.xhdpi"]      # a escolha ficou registrada

        depois = await env.svc.verify_on(rt, INSTAGRAM, instalador())
        assert depois["state"] == InstalledAppState.ready.value
        assert depois["drift_kind"] is None
    finally:
        env.db.close()


async def test_desligar_a_escolha_de_splits_instala_o_conjunto_inteiro(tmp_path: Path) -> None:
    env = montar(tmp_path)
    try:
        rel = importar(env, 447, splits=("config.xhdpi", "config.xxhdpi"))
        rt = FakeRt(FakeAdbDevice(density=320), "android-01")
        estado = await env.svc.install_on(rt, rel, instalador(), select_for_device=False)
        assert sorted(estado["observed_splits"]) == ["base", "config.xhdpi", "config.xxhdpi"]
    finally:
        env.db.close()


# ==================================================================== rollback
async def test_rollback_preservando_dados_volta_a_versao_anterior(tmp_path: Path) -> None:
    env = montar(tmp_path)
    try:
        v447 = importar(env, 447)
        rt = Aparelho(env.app, "android-01")
        await env.svc.install_on(rt, v447, instalador())
        v448 = importar(env, 448)
        await env.svc.install_on(rt, v448, instalador())

        estado = env.repo.app_state("android-01", INSTAGRAM)
        assert estado["last_operation"] == "upgrade"
        assert estado["previous_release_id"] == v447                 # o alvo do rollback foi guardado ANTES

        depois = await env.svc.rollback(rt, INSTAGRAM, instalador())
        assert depois["observed_version_code"] == 447
        assert depois["installed_release_id"] == v447
        assert depois["last_operation"] == "rollback"
        assert "install -d" in rt.adb.calls                          # voltar exige o sinalizador explícito
        assert env.repo.release_row(v448)["channel"] == ReleaseChannel.rolled_back.value
        # E dá para voltar ao ponto de partida: o alvo agora é a versão que acabou de sair.
        assert depois["previous_release_id"] == v448
    finally:
        env.db.close()


async def test_rollback_recusado_pelo_android_nao_destroi_nada_e_fica_nomeado(tmp_path: Path) -> None:
    env = montar(tmp_path)
    try:
        v447 = importar(env, 447)
        rt = Aparelho(env.app, "android-01")
        await env.svc.install_on(rt, v447, instalador())
        v448 = importar(env, 448)
        await env.svc.install_on(rt, v448, instalador())

        rt.adb.refuse_downgrade = True                               # build que recusa mesmo com `-d`
        with pytest.raises(DowngradeRefused):
            await env.svc.rollback(rt, INSTAGRAM, instalador())

        estado = env.repo.app_state("android-01", INSTAGRAM)
        assert estado["drift_kind"] == "downgrade_refused"
        assert "apaga a sessão" in estado["detail"]
        assert rt.adb.installed["version_code"] == 448               # o aparelho continua com o que tinha
    finally:
        env.db.close()


async def test_rollback_sem_preservar_dados_desinstala_antes_e_a_sessao_se_perde(tmp_path: Path) -> None:
    env = montar(tmp_path, FakeInstagram(stored_password=SENHA))
    try:
        rt = Aparelho(env.app, "android-02")
        v447 = importar(env, 447)
        await env.svc.install_on(rt, v447, instalador())
        pid = env.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id="android-02")).id
        assert (await env.auth.ensure_session(rt, pid)).ready
        v448 = importar(env, 448)
        await env.svc.install_on(rt, v448, instalador())

        rt.adb.refuse_downgrade = True
        with pytest.raises(DowngradeRefused):
            await env.svc.rollback(rt, INSTAGRAM, instalador())

        depois = await env.svc.rollback(rt, INSTAGRAM, instalador(), preserve=False)
        assert depois["observed_version_code"] == 447
        assert env.app.account is None                               # os dados do app foram apagados de verdade
        sessao = env.social_repo.session_row(pid)
        assert sessao["status"] == SessionStatus.unknown.value
        assert "apagados" in sessao["detail"]
    finally:
        env.db.close()


async def test_rollback_sem_versao_anterior_nao_inventa_um_alvo(tmp_path: Path) -> None:
    env = montar(tmp_path)
    try:
        rel = importar(env, 447)
        rt = Aparelho(env.app, "android-01")
        await env.svc.install_on(rt, rel, instalador())
        with pytest.raises(ReleaseValidationError, match="não há para onde voltar"):
            await env.svc.rollback(rt, INSTAGRAM, instalador())
    finally:
        env.db.close()


# ==================================================================== matriz de invalidação: sessão após upgrade
async def test_sessao_preservada_depois_do_upgrade_nao_pede_senha_de_novo(tmp_path: Path) -> None:
    """O caso que mais importa: o Instagram manteve o login, então NINGUÉM digita senha.

    A sessão vira "não verificada" — não "perdida" —, e a próxima verificação resolve só observando a tela.
    """
    env = montar(tmp_path, FakeInstagram(stored_password=SENHA))
    try:
        rt = Aparelho(env.app, "android-02")
        v447 = importar(env, 447)
        await env.svc.install_on(rt, v447, instalador())
        pid = env.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id="android-02")).id
        assert (await env.auth.ensure_session(rt, pid)).ready
        digitado = len(env.app.typed)
        assert digitado > 0                                          # o primeiro login digitou, claro

        v448 = importar(env, 448)
        await env.svc.install_on(rt, v448, instalador())

        sessao = env.social_repo.session_row(pid)
        assert sessao["status"] == SessionStatus.unknown.value
        assert "447 → 448" in sessao["detail"] and "observada de novo" in sessao["detail"]

        r = await env.auth.ensure_session(rt, pid, automatic=True)
        assert r.ready and r.observed_username == USUARIO
        assert len(env.app.typed) == digitado                        # nada foi digitado: a sessão estava de pé
        assert env.social_repo.session_row(pid)["status"] == SessionStatus.session_ready.value
    finally:
        env.db.close()


async def test_sessao_perdida_depois_do_upgrade_autentica_de_novo(tmp_path: Path) -> None:
    """O outro lado: o app atualizou e derrubou o login. Aí sim autentica — depois de observar, nunca antes."""
    env = montar(tmp_path, FakeInstagram(stored_password=SENHA))
    try:
        rt = Aparelho(env.app, "android-02")
        v447 = importar(env, 447)
        await env.svc.install_on(rt, v447, instalador())
        pid = env.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id="android-02")).id
        assert (await env.auth.ensure_session(rt, pid)).ready
        digitado = len(env.app.typed)

        v448 = importar(env, 448)
        await env.svc.install_on(rt, v448, instalador())
        env.app.account = None                                       # a atualização derrubou o login

        r = await env.auth.ensure_session(rt, pid, automatic=True)
        assert r.ready and r.observed_username == USUARIO
        assert len(env.app.typed) > digitado                         # precisou autenticar de novo
        assert env.app.account == USUARIO
    finally:
        env.db.close()


async def test_reinstalar_a_mesma_versao_tambem_deixa_a_sessao_nao_verificada(tmp_path: Path) -> None:
    """Linha da matriz que é fácil esquecer: reinstalar não muda a versão, mas mexe no disco."""
    env = montar(tmp_path, FakeInstagram(stored_password=SENHA))
    try:
        rt = Aparelho(env.app, "android-02")
        rel = importar(env, 447)
        await env.svc.install_on(rt, rel, instalador())
        pid = env.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id="android-02")).id
        assert (await env.auth.ensure_session(rt, pid)).ready

        await env.svc.install_on(rt, rel, instalador())
        estado = env.repo.app_state("android-02", INSTAGRAM)
        assert estado["last_operation"] == "reinstall"
        assert estado["previous_release_id"] is None                 # reinstalar não cria alvo de rollback
        sessao = env.social_repo.session_row(pid)
        assert sessao["status"] == SessionStatus.unknown.value and "reinstalado" in sessao["detail"]
    finally:
        env.db.close()


async def test_instalacao_que_falha_ao_abrir_tambem_invalida_a_sessao(tmp_path: Path) -> None:
    """O disco mudou mesmo com a prova de abertura falhando — a sessão antiga não vale mais de qualquer jeito."""
    env = montar(tmp_path, FakeInstagram(stored_password=SENHA))
    try:
        rt = Aparelho(env.app, "android-02")
        v447 = importar(env, 447)
        await env.svc.install_on(rt, v447, instalador())
        pid = env.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id="android-02")).id
        assert (await env.auth.ensure_session(rt, pid)).ready

        v448 = importar(env, 448)
        rt.adb.launch_dies = True
        with pytest.raises(ReleaseValidationError):
            await env.svc.install_on(rt, v448, instalador())
        assert env.social_repo.session_row(pid)["status"] == SessionStatus.unknown.value
    finally:
        env.db.close()


async def test_sem_gancho_ligado_a_instalacao_segue_funcionando(tmp_path: Path) -> None:
    """O domínio de release não depende do social: sem o gancho do AppState, instalar continua sendo instalar."""
    env = montar(tmp_path)
    try:
        env.svc.on_app_changed = None  # type: ignore[assignment]
        rel = importar(env, 447)
        rt = FakeRt(FakeAdbDevice(), "android-01")
        estado = await env.svc.install_on(rt, rel, instalador())
        assert estado["state"] == InstalledAppState.ready.value
    finally:
        env.db.close()


# ==================================================================== rotas
def catalogar(state: Any, release_id: str, codigo: int) -> None:
    state.release_repo.save_release(
        release_id=release_id, package_name=INSTAGRAM, version_name=f"{codigo}.0.0", version_code=codigo,
        artifact_type="single", signature_sha256="f" * 64, min_sdk=28, target_sdk=35, abis=["x86_64"],
        catalog_dir=f"apks/{release_id}", source_type="inbox", source_reference=None,
        status=ReleaseState.installable, detail=None, files=[])


async def test_api_de_ciclo_de_vida_recusa_o_que_nao_da_para_fazer(harness: Any) -> None:
    """Toda recusa acontece ANTES de aceitar: o trabalho roda em segundo plano e um 202 esconderia o problema."""
    import httpx

    from app.main import create_app

    state = harness.state
    catalogar(state, "rel-x", 447)
    app = create_app(harness.cfg, state=state)
    app.state.poc = state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/releases/nao-existe/lifecycle", json={"verb": "promote"})
        assert r.status_code == 404

        r = await c.post("/api/releases/rel-x/lifecycle", json={"verb": "promote"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "lifecycle_refused"
        assert "canário" in r.json()["detail"]["message"]

        r = await c.post("/api/releases/rel-x/lifecycle", json={"verb": "canary"})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "instance_required"

        r = await c.post("/api/releases/rel-x/lifecycle", json={"verb": "rollback", "instance_id": "android-01"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "no_previous_release"

        r = await c.post("/api/releases/rel-x/lifecycle", json={"verb": "apagar", "instance_id": "android-01"})
        assert r.status_code == 422                    # verbo inventado não existe

        # Canário numa versão já promovida é recusado AQUI, não lá dentro: o trabalho roda em segundo plano, e
        # uma recusa no worker devolveria 202 sem dizer o motivo a quem chamou.
        state.release_repo.set_channel("rel-x", ReleaseChannel.promoted, detail="provada")
        r = await c.post("/api/releases/rel-x/lifecycle", json={"verb": "canary", "instance_id": "android-01"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "lifecycle_refused"
        assert "quarentena antes" in r.json()["detail"]["message"]


async def test_quarentena_pela_api_bloqueia_a_instalacao_e_fica_visivel(harness: Any) -> None:
    import httpx

    from app.main import create_app

    state = harness.state
    catalogar(state, "rel-y", 448)
    app = create_app(harness.cfg, state=state)
    app.state.poc = state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/releases/rel-y/lifecycle", json={"verb": "quarantine", "note": "travou no meu teste"})
        assert r.status_code == 200 and r.json()["release"]["channel"] == "quarantined"

        alvo = next(x for x in (await c.get("/api/releases")).json() if x["id"] == "rel-y")
        assert alvo["channel"] == "quarantined" and "travou" in alvo["channel_detail"]

        # A release continua `installable` — o arquivo não ficou pior. Quem barra é o outro eixo, o canal.
        assert alvo["status"] == "installable"
        r = await c.post("/api/instances/android-01/app/install", json={"release_id": "rel-y"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "release_quarantined"
        assert "travou no meu teste" in r.json()["detail"]["message"]


async def test_divergencia_de_versao_tambem_invalida_a_sessao(tmp_path: Path) -> None:
    """O disco mudou no instante em que o `install` voltou — não no fim do caminho feliz.

    Se a invalidação esperasse a sonda de abertura, uma divergência de versão interromperia o método antes dela e a
    sessão continuaria dizendo "verificada" sobre um app que já foi substituído.
    """
    env = montar(tmp_path, FakeInstagram(stored_password=SENHA))
    try:
        rt = Aparelho(env.app, "android-02")
        v447 = importar(env, 447)
        await env.svc.install_on(rt, v447, instalador())
        pid = env.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id="android-02")).id
        assert (await env.auth.ensure_session(rt, pid)).ready

        v448 = importar(env, 448, splits=("config.xhdpi",))
        # O aparelho aceita a instalação mas relata um conjunto diferente do que foi enviado.
        rt.adb.props["forjar_split"] = "1"
        original = rt.adb._put

        def mentir(paths: list[str], *, allow_downgrade: bool = False) -> None:
            original(paths, allow_downgrade=allow_downgrade)
            assert rt.adb.installed is not None
            rt.adb.installed["splits"] = ["base"]            # o split some entre instalar e observar

        rt.adb._put = mentir  # type: ignore[method-assign]
        with pytest.raises(ReleaseValidationError, match="Faltam splits"):
            await env.svc.install_on(rt, v448, instalador())

        assert env.repo.app_state("android-02", INSTAGRAM)["drift_kind"] == "split_mismatch"
        assert env.social_repo.session_row(pid)["status"] == SessionStatus.unknown.value
    finally:
        env.db.close()


async def test_janela_entre_desinstalar_e_instalar_fica_reconciliavel(tmp_path: Path) -> None:
    """Se o backend cair entre a desinstalação e a instalação, a linha não pode ficar dizendo "pronto" sobre um
    aparelho sem o app. A marca de operação pendente é o que a reconciliação de partida enxerga."""
    env = montar(tmp_path)
    try:
        v447 = importar(env, 447)
        rt = Aparelho(env.app, "android-01")
        await env.svc.install_on(rt, v447, instalador())
        v448 = importar(env, 448)
        await env.svc.install_on(rt, v448, instalador())

        visto: list[str | None] = []
        original = rt.adb.uninstall

        def cair_depois_de_desinstalar(package: str, *, timeout: float = 0) -> None:
            original(package, timeout=timeout)
            visto.append(env.repo.app_state("android-01", INSTAGRAM)["pending_op"])
            raise RuntimeError("o backend caiu logo depois de desinstalar")

        rt.adb.uninstall = cair_depois_de_desinstalar  # type: ignore[method-assign]
        with pytest.raises(Exception, match="caiu logo depois"):
            await env.svc.rollback(rt, INSTAGRAM, instalador(), preserve=False)

        assert visto == ["uninstall"]                   # a marca existia ANTES de o aparelho perder o app
        assert env.repo.pending_operations()            # e a reconciliação de partida a encontra
        env.svc.reconcile_after_restart()
        linha = env.repo.app_state("android-01", INSTAGRAM)
        assert linha["pending_op"] is None and linha["state"] == "verifying"
    finally:
        env.db.close()


async def test_rollback_recusado_nao_marca_a_versao_atual_como_substituida(tmp_path: Path) -> None:
    """Ela continua instalada e funcionando: anunciá-la como substituída seria mentira no painel."""
    env = montar(tmp_path)
    try:
        v447 = importar(env, 447)
        rt = Aparelho(env.app, "android-01")
        await env.svc.install_on(rt, v447, instalador())
        v448 = importar(env, 448)
        await env.svc.install_on(rt, v448, instalador())

        rt.adb.refuse_downgrade = True
        with pytest.raises(DowngradeRefused):
            await env.svc.rollback(rt, INSTAGRAM, instalador())
        assert env.repo.release_row(v448)["channel"] == ReleaseChannel.candidate.value

        rt.adb.refuse_downgrade = False
        await env.svc.rollback(rt, INSTAGRAM, instalador())
        assert env.repo.release_row(v448)["channel"] == ReleaseChannel.rolled_back.value
    finally:
        env.db.close()


async def test_provar_de_novo_uma_versao_promovida_exige_desfazer_a_promocao_antes(tmp_path: Path) -> None:
    """Senão um canário num segundo aparelho rebaixaria em silêncio a versão que já tinha provado."""
    env = montar(tmp_path)
    try:
        rel = importar(env, 447)
        um = Aparelho(env.app, "android-01")
        await env.svc.start_canary(um, rel, instalador())
        env.svc.promote(rel)

        dois = Aparelho(FakeInstagram(), "android-02")
        with pytest.raises(ReleaseValidationError, match="já foi promovida"):
            await env.svc.start_canary(dois, rel, instalador())
        assert env.repo.release_row(rel)["channel"] == ReleaseChannel.promoted.value

        # Instalar normalmente num segundo aparelho continua valendo — é o caminho certo para uma versão provada.
        estado = await env.svc.install_on(dois, rel, instalador())
        assert estado["state"] == InstalledAppState.ready.value
    finally:
        env.db.close()
