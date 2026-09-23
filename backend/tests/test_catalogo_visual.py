"""Fase 6 — itens 6.3 (catálogo visual de aplicativos, E11) e 6.5 (VM da loja remota).

O que cada bloco protege, em uma frase:

* nome e ícone saem do PRÓPRIO APK, na mesma leitura que já dava pacote e versão — e um ícone adaptativo (XML)
  vira "sem ícone", nunca uma imagem quebrada servida ao navegador;
* o ícone mora na pasta imutável da release e NÃO entra no conjunto instalável: se entrasse, o
  `install-multiple` receberia um PNG e a instalação inteira falharia;
* a interface consegue perguntar "para onde esta versão pode ir?" e recebe a resposta do MESMO julgamento que
  recusa a instalação — nunca de uma segunda regra escrita em TypeScript;
* há uma entrada de APK para quem não tem acesso ao disco do servidor, e o arquivo enviado passa pela mesma
  inspeção da pasta de entrada (enviar não instala, e não aprova assinatura nenhuma);
* existe UM caminho de instalação: o verbo `install_apk` do painel resolve a versão promovida e cai na camada
  de releases, ou recusa dizendo o que fazer — nunca mais `adb install` do `apps.apk_path`;
* a loja PODE ser um aparelho de worker (a recusa de construção saiu da configuração), e o agente aceita
  imagem/RAM/janela por aparelho, que é o que uma VM de Play Store exige.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

import httpx
import pytest
import pytest_asyncio

from app.models import InstanceState, ReleaseChannel, ReleaseState
from app.releases import catalog
from app.releases.inspector import ApkInspector, melhor_icone
from app.worker.executor import WorkerExecutor
from app.worker.settings import DeviceSpec, WorkerSettings

from .conftest import Harness, make_config
from .test_app_releases import INSTAGRAM, build_service, part

PNG = bytes.fromhex("89504e470d0a1a0a") + b"pixel de mentira, mas bytes de verdade"

BADGING = """package: name='com.instagram.android' versionCode='447' versionName='447.0.0' platformBuildVersionName='14'
application-label:'Instagram'
application-label-pt-BR:'Instagram (pt)'
application-icon-160:'res/mipmap-mdpi-v4/ic_launcher.png'
application-icon-640:'res/mipmap-xxxhdpi-v4/ic_launcher.png'
application-icon-65534:'res/mipmap-anydpi-v26/ic_launcher.xml'
application: label='Instagram' icon='res/mipmap-anydpi-v26/ic_launcher.xml'
sdkVersion:'28'
targetSdkVersion:'34'
native-code: 'arm64-v8a'
densities: '320'
locales: 'pt-BR'
"""


class ToolsFalsas:
    """`SdkTools` de mentira: devolve a saída combinada do `aapt2`/`apksigner` sem tocar no SDK."""

    root = Path("sdk-que-nao-existe")
    aapt2 = "aapt2"
    apksigner = "apksigner"

    def __init__(self, badging: str) -> None:
        self.badging = badging

    def can_inspect_apk(self) -> bool:
        return True

    def run(self, cmd: list[str], timeout: float = 0) -> Any:  # noqa: ARG002 - assinatura do SdkTools real
        if "badging" in cmd:
            return SimpleNamespace(returncode=0, stdout=self.badging, stderr="")
        return SimpleNamespace(returncode=0, stdout=f"Signer #1 certificate SHA-256 digest: {'f' * 64}\n", stderr="")


# ==================================================================== o APK diz o próprio nome e o próprio ícone
def test_inspecao_le_rotulo_e_o_melhor_icone_raster(tmp_path: Path) -> None:
    apk = tmp_path / "qualquer-nome.apk"
    apk.write_bytes(b"conteudo")
    info = ApkInspector(ToolsFalsas(BADGING)).inspect(apk)  # type: ignore[arg-type]
    assert info.label == "Instagram", "o rótulo traduzido não pode vencer o padrão: escolher idioma é do usuário"
    # 640 dpi é a MAIOR imagem declarada. A entrada `65534` é o ícone adaptativo em XML: servi-lo como imagem
    # devolveria XML binário ao navegador, que é pior do que ícone nenhum.
    assert info.icon_entry == "res/mipmap-xxxhdpi-v4/ic_launcher.png"


def test_so_icone_adaptativo_vira_sem_icone() -> None:
    assert melhor_icone("application-icon-65534:'res/mipmap-anydpi-v26/ic_launcher.xml'\n") is None
    assert melhor_icone("sem ícone nenhum aqui") is None


def _release_com_icone(tmp_path: Path, entrada: str, *, gravar: bool = True) -> Any:
    """Uma release validada cujo base.apk é um ZIP de verdade — `extract_icon` abre o arquivo, não o banco."""
    apk = tmp_path / "base.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("AndroidManifest.xml", b"nao importa")
        if gravar:
            z.writestr(entrada, PNG)
    base = part(label="Instagram", icon_entry=entrada)
    base.path = apk
    return catalog.ValidatedRelease(
        package_name=INSTAGRAM, version_code=447, version_name="447.0.0", signature_sha256="f" * 64,
        artifact_type="single", min_sdk=28, target_sdk=34, abis=["arm64-v8a"], requires_gms=False,
        parts=[base], set_hash="a" * 64, label="Instagram", icon_entry=entrada)


def test_icone_e_extraido_para_a_pasta_da_release(tmp_path: Path) -> None:
    rel = _release_com_icone(tmp_path, "res/mipmap-xxxhdpi-v4/ic_launcher.png")
    destino = tmp_path / "catalogo"
    nome = catalog.extract_icon(rel, destino)
    assert nome == "icon.png"
    assert (destino / "icon.png").read_bytes() == PNG


def test_entrada_de_icone_que_nao_existe_no_apk_nao_derruba_a_importacao(tmp_path: Path) -> None:
    """Ícone é acréscimo. Um APK que declara uma entrada e não a traz continua sendo release válida."""
    rel = _release_com_icone(tmp_path, "res/mipmap-xxxhdpi-v4/ic_launcher.png", gravar=False)
    assert catalog.extract_icon(rel, tmp_path / "catalogo") is None


def test_formato_de_icone_desconhecido_e_ignorado(tmp_path: Path) -> None:
    rel = _release_com_icone(tmp_path, "res/drawable/ic_launcher.qmg")
    assert catalog.extract_icon(rel, tmp_path / "catalogo") is None


# ==================================================================== importação: nome, ícone e origem no catálogo
class InspetorComIcone:
    """Inspetor que devolve os metadados combinados, inclusive rótulo e ícone, para o arquivo dado."""

    def __init__(self, entrada: str | None) -> None:
        self.entrada = entrada

    def available(self) -> bool:
        return True

    def inspect(self, path: Path) -> Any:
        from app.releases.inspector import sha256_of

        info = part(label="Instagram", icon_entry=self.entrada)
        digest, size = sha256_of(path)
        info.path, info.sha256, info.size_bytes = path, digest, size
        return info


def _apk_na_inbox(inbox: Path, entrada: str | None) -> None:
    inbox.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(inbox / "base.apk", "w") as z:
        z.writestr("AndroidManifest.xml", b"nao importa")
        if entrada:
            z.writestr(entrada, PNG)


def test_importacao_grava_nome_e_icone_e_o_icone_fica_fora_do_conjunto_instalavel(tmp_path: Path) -> None:
    entrada = "res/mipmap-xxxhdpi-v4/ic_launcher.png"
    svc, repo, db = build_service(tmp_path, InspetorComIcone(entrada))
    try:
        _apk_na_inbox(svc.cfg.apk_inbox, entrada)
        resultados = svc.import_inbox(source_reference="teste")
        assert len(resultados) == 1 and resultados[0].ok, resultados[0].reason

        dto = svc.list_releases()[0]
        assert dto.label == "Instagram" and dto.has_icon is True
        assert svc.icon_bytes(dto.id) == (PNG, "image/png")

        # O ícone NÃO pode entrar na lista de arquivos: `files_for_install` a entrega ao `install-multiple`, e um
        # PNG ali faria a instalação inteira falhar por causa de uma imagem.
        assert [f.file_name for f in dto.files] == ["base.apk"]
        _, caminhos = svc.files_for_install(dto.id)
        assert all(p.suffix == ".apk" for p in caminhos)
        assert repo.release_row(dto.id)["icon_file"] == "icon.png"
    finally:
        db.close()


def test_release_sem_icone_servivel_nao_promete_imagem(tmp_path: Path) -> None:
    svc, _repo, db = build_service(tmp_path, InspetorComIcone(None))
    try:
        _apk_na_inbox(svc.cfg.apk_inbox, None)
        assert svc.import_inbox()[0].ok
        dto = svc.list_releases()[0]
        assert dto.has_icon is False
        assert svc.icon_bytes(dto.id) is None, "sem ícone a rota tem de responder 404, não servir lixo"
    finally:
        db.close()


def test_nome_de_icone_vindo_do_banco_nunca_vira_leitura_livre_de_arquivo(tmp_path: Path) -> None:
    """Defesa de fundo: se alguém gravar um caminho na coluna, `icon_bytes` recusa em vez de ler o disco."""
    svc, repo, db = build_service(tmp_path, InspetorComIcone("res/mipmap-xxxhdpi-v4/ic_launcher.png"))
    try:
        _apk_na_inbox(svc.cfg.apk_inbox, "res/mipmap-xxxhdpi-v4/ic_launcher.png")
        assert svc.import_inbox()[0].ok
        rid = svc.list_releases()[0].id
        segredo = tmp_path / "segredo.png"
        segredo.write_bytes(b"nao deveria sair daqui")
        repo.db.execute("UPDATE app_releases SET icon_file=? WHERE id=?", ("../../segredo.png", rid))
        assert svc.icon_bytes(rid) is None
    finally:
        db.close()


# ==================================================================== destinos, upload e um caminho de instalação
@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> AsyncIterator[Harness]:
    h = Harness(tmp_path, 3, store="android-03")
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


def _release_no_banco(h: Harness, *, abis: list[str], min_sdk: int = 28) -> str:
    """Uma release instalável direto no banco: aqui o que se exercita é a ROTA, não a importação."""
    s = h.state
    assert s is not None
    rid = f"{INSTAGRAM}-447-teste"
    s.release_repo.save_release(
        release_id=rid, package_name=INSTAGRAM, version_name="447.0.0", version_code=447,
        artifact_type="single", signature_sha256="f" * 64, min_sdk=min_sdk, target_sdk=34, abis=abis,
        catalog_dir="apks/x", source_type="inbox", source_reference="teste",
        status=ReleaseState.installable, detail=None, label="Instagram",
        files=[{"role": "base", "name": "base.apk", "sha256": "a" * 64, "sizeBytes": 10}])
    return rid


async def test_destinos_trazem_a_incompatibilidade_explicada_antes_de_enviar(parque: Harness) -> None:
    """A tela precisa dizer "não roda aqui, e por isto" ANTES do envio — e o motivo tem de vir do backend, que é
    quem recusa a instalação. Uma segunda regra em TypeScript ficaria velha na primeira mudança."""
    rid = _release_no_banco(parque, abis=["riscv64"])      # nenhuma ABI que o parque de teste tenha
    async with _cliente(parque) as c:
        r = await c.get(f"/api/releases/{rid}/targets")
        assert r.status_code == 200
        corpo = r.json()
        alvos = corpo["targets"]
        # A loja não entra: ela é a FONTE do aplicativo, nunca o destino.
        assert "android-03" not in [a["id"] for a in alvos]
        assert {a["id"] for a in alvos} == {"android-01", "android-02"}
        assert all(a["compatible"] is False and a["reason"] for a in alvos)
        assert (await c.get("/api/releases/nao-existe/targets")).status_code == 404


async def test_destino_compativel_aparece_selecionavel_com_o_estado_do_aparelho(parque: Harness) -> None:
    rid = _release_no_banco(parque, abis=[])               # sem código nativo: serve em qualquer ABI
    async with _cliente(parque) as c:
        alvos = (await c.get(f"/api/releases/{rid}/targets")).json()["targets"]
    assert all(a["compatible"] is True and a["reason"] is None for a in alvos)
    assert all(a["already"] is False and a["installed_release_id"] is None for a in alvos)
    assert all("state" in a and "worker_id" in a for a in alvos), "a tela agrupa por servidor: o campo é do contrato"


async def test_upload_de_apk_passa_pela_mesma_inspecao_da_pasta_de_entrada(parque: Harness) -> None:
    """Quem abre o painel de fora do servidor não tinha entrada de APK nenhuma. Enviar NÃO instala e NÃO aprova
    assinatura: o arquivo cai na mesma inspeção, e um arquivo que não é APK é recusado com o motivo."""
    s = parque.state
    assert s is not None
    async with _cliente(parque) as c:
        r = await c.post("/api/releases/upload?filename=base.apk&set_id=t1&final=false", content=b"nao e um apk")
        assert r.status_code == 201 and r.json()["imported"] is None and r.json()["size_bytes"] == 12
        # Ainda não importou: o conjunto de splits é uma unidade, e importar a cada arquivo reprovaria por
        # "falta o base.apk".
        assert s.releases.list_releases() == []

        r = await c.post("/api/releases/upload?filename=split.apk&set_id=t1&final=true", content=b"nem este")
        assert r.status_code in (201, 400)
        assert s.releases.list_releases() == [], "um arquivo que não é APK não pode virar release"

        # Nome fora do formato nunca vira caminho no disco.
        assert (await c.post("/api/releases/upload?filename=../../evil.apk&set_id=t1", content=b"x")
                ).status_code == 400
        assert (await c.post("/api/releases/upload?filename=x.apk&set_id=../t", content=b"x")).status_code == 400
        assert (await c.post("/api/releases/upload?filename=x.apk&set_id=t2", content=b"")).status_code == 400
        # A pasta do conjunto final é limpa: cópia recusada não fica na inbox esperando ser reimportada.
        assert not (s.cfg.apk_inbox / "upload-t1").exists()


async def test_instalar_apk_pelo_painel_exige_versao_promovida(parque: Harness) -> None:
    """#83: o verbo tinha caminho próprio (`adb install` de `apps.apk_path`), invisível para a camada de
    releases. Agora ele resolve a versão PROMOVIDA — e, sem nenhuma, RECUSA dizendo o que fazer."""
    s = parque.state
    assert s is not None
    rt = s.devices.devices["android-01"]
    rt.state = InstanceState.online
    s.db.execute("UPDATE instances SET app_id='instagram' WHERE id='android-01'")
    async with _cliente(parque) as c:
        r = await c.post("/api/instances/android-01/actions/install_apk", json={"confirm": True})
        cid = r.json()["command_id"]
        await parque.wait(lambda: s.commands.get(cid)["state"] in ("failed", "succeeded", "uncertain"),
                          what="o verbo fechar o comando")
        linha = s.commands.get(cid)
        assert linha["state"] == "failed"
        assert "promovida" in (linha["reason"] or ""), linha["reason"]

    # E o caminho velho não existe mais: nada no gerenciador de aparelhos instala APK por fora.
    assert not hasattr(s.devices, "install_apk")


async def test_instalar_apk_pelo_painel_usa_a_release_promovida(parque: Harness) -> None:
    s = parque.state
    assert s is not None
    rt = s.devices.devices["android-01"]
    rt.state = InstanceState.online
    s.db.execute("UPDATE instances SET app_id='instagram' WHERE id='android-01'")
    rid = _release_no_banco(parque, abis=[])
    s.release_repo.set_channel(rid, ReleaseChannel.promoted)
    pedidos: list[tuple[str, str]] = []

    async def instalar(rt_: Any, release_id: str, _installer: Any, **_kw: Any) -> dict[str, Any]:
        pedidos.append((rt_.id, release_id))
        return {"ok": True}

    s.releases.install_on = instalar  # type: ignore[assignment]
    async with _cliente(parque) as c:
        r = await c.post("/api/instances/android-01/actions/install_apk", json={"confirm": True})
        cid = r.json()["command_id"]
        await parque.wait(lambda: s.commands.get(cid)["state"] == "succeeded", what="o verbo concluir")
    assert pedidos == [("android-01", rid)], "o verbo do painel tem de cair na camada de releases, e em nenhum outro lugar"


# ==================================================================== 6.5 — a VM da loja pode ser remota
def test_loja_pode_ser_aparelho_de_worker(tmp_path: Path) -> None:
    """A recusa da configuração era o bloqueio DE CONSTRUÇÃO: enquanto ela existia, a VM da conta Google não
    podia sair da máquina central — não por decisão, por impossibilidade."""
    cfg = make_config(tmp_path, 3, store="android-03", external={"android-03": "worker-lan-01"})
    assert cfg.store_id == "android-03"
    assert "android-03" in cfg.file.instances.external


def test_loja_que_nao_existe_continua_recusada(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make_config(tmp_path, 3, store="android-99")


def _worker(tmp_path: Path, spec: DeviceSpec, **kw: Any) -> WorkerExecutor:
    settings = WorkerSettings(worker_id="worker-lan-01", name="Notebook", work_dir=str(tmp_path / "farm"),
                              sdk_root=str(tmp_path / "sdk"), devices=[spec], **kw)
    return WorkerExecutor(settings, settings.to_config())


def test_agente_aceita_imagem_janela_e_ram_por_aparelho(tmp_path: Path) -> None:
    """Uma VM de loja precisa de imagem com Play Store e de JANELA (a conta Google é digitada na janela do
    emulador). Enquanto o agente aplicava a configuração global a todos os seus aparelhos, ela não cabia aqui."""
    loja = DeviceSpec(instance_id="android-03", avd_name="loja", console_port=5560,
                      system_image="system-images;android-34;google_apis_playstore;x86_64",
                      ram_mb=4096, window=True)
    comum = DeviceSpec(instance_id="android-01", avd_name="w01", console_port=5554)
    ex = _worker(tmp_path, loja)
    ex.settings.devices.append(comum)

    da_loja = ex._android_de(loja)
    assert da_loja.system_image.endswith("google_apis_playstore;x86_64")
    assert da_loja.ram_mb == 4096 and da_loja.window is True
    # Sem override declarado, nada muda: é a própria configuração global de antes.
    assert ex._android_de(comum) is ex._android()
    assert ex._android().window is False, "declarar janela num aparelho não pode ligar janela no parque inteiro"


def test_guarda_de_ram_cobra_do_aparelho_o_que_ele_declara(tmp_path: Path) -> None:
    """Cobrar de uma VM de 4 GB a conta do aparelho padrão aprovaria um boot que o host não aguenta."""
    loja = DeviceSpec(instance_id="android-03", avd_name="loja", console_port=5560, ram_mb=4096)
    comum = DeviceSpec(instance_id="android-01", avd_name="w01", console_port=5554)
    ex = _worker(tmp_path, loja, ram_per_device_mb=1800)
    # Sem `ram_mb` explícito a conta do aparelho comum é a MEDIDA do perfil da imagem (2700 MB), não o chute
    # de `ram_per_device_mb`; com número explícito, a regra antiga continua (ver test_worker_executor).
    assert ex._custo_de_ram(comum) == ex._android().est_ram_host_mb() == 2700
    # 4096 + a sobrecarga medida do processo (1800 − 2048 é negativo ⇒ sobrecarga 0 com o perfil padrão).
    assert ex._custo_de_ram(loja) == 4096 + max(0, 1800 - ex._android().ram_efetiva())
    assert ex._custo_de_ram(loja) > ex._custo_de_ram(comum)


# ==================================================================== prova com o SDK de verdade
def test_nome_e_icone_de_um_apk_real_saem_do_proprio_pacote(tmp_path: Path) -> None:
    """A prova que um `badging` escrito à mão não dá: a saída REAL do `aapt2` desta máquina.

    O APK do app de QA está versionado no repositório, então o caminho inteiro — regex do rótulo, escolha da
    melhor densidade e extração do arquivo de dentro do ZIP — roda contra um pacote de verdade, sem aparelho.
    """
    from app.config import load_config
    from app.devices.sdk import SdkTools
    from app.releases.inspector import sha256_of
    from .test_app_releases import QA_APK

    tools = SdkTools(load_config())
    if not tools.can_inspect_apk():
        pytest.skip("aapt2/apksigner não instalados nesta máquina")
    enganoso = tmp_path / "instagram-latest-v999.apk"          # o nome do arquivo não decide nada, nem aqui
    enganoso.write_bytes(QA_APK.read_bytes())
    info = ApkInspector(tools).inspect(enganoso)
    assert info.package_name == "com.pocqa.messenger"
    assert info.label and info.label.strip(), "o rótulo tem de sair do badging real, não do nome do arquivo"
    assert info.sha256 == sha256_of(QA_APK)[0]

    rel = catalog.ValidatedRelease(
        package_name=info.package_name, version_code=info.version_code, version_name=info.version_name,
        signature_sha256=info.signature_sha256, artifact_type="single", min_sdk=info.min_sdk,
        target_sdk=info.target_sdk, abis=info.abis, requires_gms=info.requires_gms, parts=[info],
        set_hash="b" * 64, label=info.label, icon_entry=info.icon_entry)
    nome = catalog.extract_icon(rel, tmp_path / "catalogo")
    if info.icon_entry is None:
        # Ícone adaptativo (XML) ou APK sem ícone: a resposta honesta é "sem ícone", nunca um arquivo quebrado.
        assert nome is None
    else:
        assert nome in catalog.ICONE_ACEITO.values()
        assert (tmp_path / "catalogo" / nome).stat().st_size > 0


# ==================================================================== 6.5 — a loja remota em funcionamento
@pytest_asyncio.fixture
async def parque_com_loja_remota(tmp_path: Path) -> AsyncIterator[Harness]:
    # `external` mapeia o id lógico para o ENDEREÇO de ADB do aparelho na outra máquina (o vínculo com o worker
    # é feito na inscrição dele, não aqui). O que importa neste teste é que loja e externo convivem.
    h = Harness(tmp_path, 3, store="android-03", external={"android-03": "127.0.0.1:15561"})
    await h.boot()
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_loja_num_worker_sobe_e_continua_sendo_loja(parque_com_loja_remota: Harness) -> None:
    """Não basta a configuração aceitar: o parque tem de subir com a loja num worker e continuar tratando-a como
    loja — fonte do aplicativo, nunca destino — e recusando texto pelo painel."""
    s = parque_com_loja_remota.state
    assert s is not None
    rt = s.devices.devices["android-03"]
    assert rt.store and rt.external, "os dois papéis passam a conviver: loja E aparelho de outra máquina"
    assert s.devices.dto(rt).kind == "store"
    assert s.devices.dto(rt).serial == "127.0.0.1:15561", "o ADB dela aponta para a outra máquina"
    async with _cliente(parque_com_loja_remota) as c:
        assert (await c.get("/api/store", params={"package": INSTAGRAM})).json()["instance_id"] == "android-03"
        # E ela continua fora da lista de destinos: o julgamento é o mesmo, esteja ela aqui ou lá.
        rid = _release_no_banco(parque_com_loja_remota, abis=[])
        alvos = (await c.get(f"/api/releases/{rid}/targets")).json()["targets"]
        assert "android-03" not in [a["id"] for a in alvos]
