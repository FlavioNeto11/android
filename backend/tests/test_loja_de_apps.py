"""Loja de apps (pedido do dono, 26/09): para quem distribuir, prévia, apps secundários ao ligar, cadastro sozinho,
a vitrine e o proxy do aparelho.

Tudo aqui é `simulated`: aparelhos do harness (porta base 5640) com o adb trocado por um falso — nenhum APK é
instalado e nenhuma configuração é gravada num emulador de verdade. O que estes testes protegem:

* distribuir para os escolhidos NÃO toca nos outros, e a prévia não grava nem instala nada;
* "N aparelhos" escolhe quem recebe mais cedo e pula quem já está na versão;
* o app que não é o principal do aparelho (o Outlook num aparelho do app de QA) instala quando ele liga;
* a versão de um pacote que ninguém cadastrou cadastra o app, uma vez só;
* o proxy só vira `applied` quando o aparelho responde o que foi pedido, e falha não se repete sozinha.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest
import pytest_asyncio

from app.models import InstanceState, ReleaseChannel
from app.releases.catalog import ReleaseValidationError

from .conftest import Harness
from .test_app_releases import QA_APK, StubInspector, part
from .test_distribute import AdbFalso, PACOTE

LOJA = "android-04"
OUTLOOK = "com.microsoft.office.outlook"


class AdbPorPacote(AdbFalso):
    """O falso de sempre guarda UM app instalado; aqui cada pacote tem o seu, e o proxy global é uma string."""

    def __init__(self) -> None:
        super().__init__()
        self.apps: dict[str, Any] = {}
        self.proxy = ""
        self.responde_outro_proxy: str | None = None
        self.shell_calls: list[str] = []

    def _put(self, paths: list[str], *, allow_downgrade: bool = False) -> None:
        meta = self._meta(paths[0])
        pkg = str(meta["package"]) if meta else PACOTE
        self.installed = self.apps.get(pkg)
        super()._put(paths, allow_downgrade=allow_downgrade)
        self.apps[pkg] = self.installed

    def package_info(self, package: str) -> dict[str, Any] | None:
        return dict(self.apps[package]) if package in self.apps else None

    def is_installed(self, package: str) -> bool:
        return package in self.apps

    def shell(self, command: str, *, timeout: float = 30) -> str:
        self.shell_calls.append(command)
        if command.startswith("settings put global http_proxy "):
            self.proxy = command.rsplit(" ", 1)[1]
            return ""
        if command == "settings get global http_proxy":
            return self.responde_outro_proxy if self.responde_outro_proxy is not None else self.proxy
        return ""


@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> AsyncIterator[Harness]:
    """android-01..03 são de tarefa; android-04 é a loja."""
    h = Harness(tmp_path, 4, store=LOJA)
    await h.boot()
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


def falsificar(h: Harness) -> dict[str, AdbPorPacote]:
    st = h.state
    assert st is not None
    falsos: dict[str, AdbPorPacote] = {}
    for rt in st.devices.devices.values():
        falsos[rt.id] = AdbPorPacote()
        rt.adb = falsos[rt.id]                                       # type: ignore[assignment]
    original = st.installer.launch_probe
    st.installer.launch_probe = lambda rt, package, **_k: original(rt, package, settle_s=0.01)  # type: ignore[method-assign]
    return falsos


def versao(h: Harness, pacote: str = PACOTE, codigo: int = 7, *, promovida: bool = True,
           rotulo: str | None = None) -> str:
    """Importa uma versão pelo caminho de sempre e, se pedido, leva até `promoted` com prova registrada."""
    st = h.state
    assert st is not None
    st.releases.inspector = StubInspector(                           # type: ignore[assignment]
        {"base.apk": part(package_name=pacote, version_code=codigo, version_name=f"{codigo}.0", abis=[],
                          label=rotulo)})
    inbox = st.cfg.apk_inbox
    inbox.mkdir(parents=True, exist_ok=True)
    shutil.copy2(QA_APK, inbox / "base.apk")
    with open(inbox / "base.apk", "ab") as fh:
        fh.write(pacote.encode() + bytes(codigo))                    # bytes próprios por pacote e versão → id próprio
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


def estado(h: Harness, iid: str, pacote: str = PACOTE) -> Any:
    return h.state.release_repo.app_state(iid, pacote)              # type: ignore[union-attr]


async def pronto(h: Harness, iid: str, rid: str, pacote: str = PACOTE) -> None:
    def ok() -> bool:
        row = estado(h, iid, pacote)
        return bool(row) and row["state"] == "ready" and row["installed_release_id"] == rid
    await h.wait(ok, timeout=30, what=f"{iid} com {pacote} instalado")


async def ligar(h: Harness, rt: Any) -> None:
    """Liga e dispara o gancho de "entrou no ar". O boot FALSO do harness (`manager.py`, ramo de teste) não chama
    `on_device_online` — é o que `test_identidade_do_aparelho` também faz à mão. Sem isto o teste provaria o
    harness, não a entrega ao ligar."""
    await h.state.devices.start_instance(rt)                       # type: ignore[union-attr]
    await h.wait(lambda: rt.state == InstanceState.online, what=f"{rt.id} ligado")
    await h.wait(lambda: rt.id not in h.state.scheduler.workers, what=f"{rt.id} livre")  # type: ignore[union-attr]
    h.state._reobservar_se_velho(rt.id)                             # type: ignore[union-attr]


def comandos(h: Harness) -> int:
    return int(h.state.db.scalar("SELECT COUNT(*) FROM commands") or 0)  # type: ignore[union-attr]


# ==================================================================== para quem
async def test_distribuir_so_para_os_escolhidos_nao_toca_nos_outros(parque: Harness) -> None:
    falsos = falsificar(parque)
    rid = versao(parque)
    saida = parque.state.distribute(rid, instance_ids=["android-02"])  # type: ignore[union-attr]
    assert [d["id"] for d in saida] == ["android-02"] and saida[0]["outcome"] == "started"
    await pronto(parque, "android-02", rid)
    for outro in ("android-01", "android-03"):
        assert estado(parque, outro) is None                          # nem versão desejada gravada
        assert falsos[outro].calls == []


async def test_escolhidos_fora_do_parque_e_alvo_ambiguo_sao_recusados_com_o_motivo(parque: Harness) -> None:
    falsificar(parque)
    rid = versao(parque)
    st = parque.state
    with pytest.raises(ReleaseValidationError, match="é a loja"):
        st.distribute(rid, instance_ids=[LOJA])                      # type: ignore[union-attr]
    with pytest.raises(ReleaseValidationError, match="não é aparelho do parque"):
        st.distribute(rid, instance_ids=["android-99"])              # type: ignore[union-attr]
    with pytest.raises(ReleaseValidationError, match="OU a quantidade"):
        st.distribute(rid, instance_ids=["android-01"], count=1)     # type: ignore[union-attr]
    assert all(estado(parque, i) is None for i in ("android-01", "android-02", "android-03"))


async def test_n_aparelhos_prefere_os_ligados_e_pula_quem_ja_esta_na_versao(parque: Harness) -> None:
    falsificar(parque)
    devs = parque.state.devices                                      # type: ignore[union-attr]
    await devs.stop_instance(devs.get("android-01"))
    rid = versao(parque)

    saida = parque.state.distribute(rid, count=1)                    # type: ignore[union-attr]
    assert [(d["id"], d["outcome"]) for d in saida] == [("android-02", "started")]   # ligado antes de desligado
    await pronto(parque, "android-02", rid)

    previa = parque.state.distribute(rid, count=5, dry_run=True)     # type: ignore[union-attr]
    # android-02 já está na versão: não conta para "N aparelhos". Ligado (03) antes de desligado (01).
    assert [(d["id"], d["outcome"]) for d in previa] == [("android-03", "would_start"), ("android-01", "pending")]


async def test_previa_nao_grava_versao_nem_instala_nem_abre_comando(parque: Harness) -> None:
    falsos = falsificar(parque)
    devs = parque.state.devices                                      # type: ignore[union-attr]
    await devs.stop_instance(devs.get("android-03"))
    rid = versao(parque)
    antes = comandos(parque)

    previa = {d["id"]: d for d in parque.state.distribute(rid, dry_run=True, eager=True)}  # type: ignore[union-attr]
    assert set(previa) == {"android-01", "android-02", "android-03"}      # a loja não entra nem na prévia
    assert previa["android-01"]["outcome"] == "would_start"
    assert previa["android-03"]["outcome"] == "pending" and "rodízio" in previa["android-03"]["reason"]
    assert all(estado(parque, i) is None for i in previa)
    assert comandos(parque) == antes
    assert all(f.calls == [] for f in falsos.values())
    assert rid not in parque.state._entrega_imediata                # type: ignore[union-attr]


# ==================================================================== app secundário instala ao ligar
async def test_app_secundario_distribuido_instala_quando_o_aparelho_liga(parque: Harness) -> None:
    """O Outlook não é o app principal de nenhum aparelho do harness: nenhuma tarefa dele chega para acionar a porta
    do app. Antes, ficava pendente para sempre; agora instala quando o aparelho liga."""
    falsos = falsificar(parque)
    devs = parque.state.devices                                      # type: ignore[union-attr]
    rt = devs.get("android-02")
    await devs.stop_instance(rt)
    rid = versao(parque, OUTLOOK, 3)

    saida = parque.state.distribute(rid, instance_ids=["android-02"])  # type: ignore[union-attr]
    assert saida[0]["outcome"] == "pending"
    assert estado(parque, "android-02", OUTLOOK)["desired_release_id"] == rid
    assert falsos["android-02"].calls == []

    await ligar(parque, rt)
    await pronto(parque, "android-02", rid, OUTLOOK)
    assert OUTLOOK in falsos["android-02"].apps
    assert estado(parque, "android-03", OUTLOOK) is None             # quem não foi escolhido não recebe


async def test_entrega_secundaria_que_falhou_nao_se_repete_ao_ligar(parque: Harness) -> None:
    falsos = falsificar(parque)
    devs = parque.state.devices                                      # type: ignore[union-attr]
    rt = devs.get("android-02")
    rid = versao(parque, OUTLOOK, 3)
    falsos["android-02"].install_error = "INSTALL_FAILED_INSUFFICIENT_STORAGE"
    parque.state.distribute(rid, instance_ids=["android-02"])        # type: ignore[union-attr]
    await parque.wait(lambda: bool(estado(parque, "android-02", OUTLOOK))
                      and estado(parque, "android-02", OUTLOOK)["state"] == "install_failed", what="falhou")
    tentativas = falsos["android-02"].calls.count("install")

    await devs.stop_instance(rt)
    await ligar(parque, rt)
    await parque.wait(lambda: rt.id not in parque.state.scheduler.workers, what="trabalho do ligar encerrado")  # type: ignore[union-attr]
    assert falsos["android-02"].calls.count("install") == tentativas  # nenhuma tentativa às cegas
    assert estado(parque, "android-02", OUTLOOK)["state"] == "install_failed"


# ==================================================================== cadastro sozinho
async def test_versao_de_pacote_novo_cadastra_o_app_uma_vez(parque: Harness) -> None:
    falsificar(parque)
    db = parque.state.db                                             # type: ignore[union-attr]
    assert db.one("SELECT id FROM apps WHERE package=?", (OUTLOOK,)) is None
    versao(parque, OUTLOOK, 3, promovida=False, rotulo="Outlook")
    versao(parque, OUTLOOK, 4, promovida=False, rotulo="Outlook")
    linhas = db.query("SELECT * FROM apps WHERE package=?", (OUTLOOK,))
    assert len(linhas) == 1
    assert linhas[0]["id"] == "outlook" and linhas[0]["name"] == "Outlook" and linhas[0]["category"] is None


# ==================================================================== a vitrine
async def test_vitrine_conta_aparelhos_por_versao_e_quem_esta_atrasado(parque: Harness) -> None:
    falsificar(parque)
    from app.vitrine import vitrine

    v7 = versao(parque, codigo=7)
    parque.state.distribute(v7, instance_ids=["android-01", "android-02"])  # type: ignore[union-attr]
    await pronto(parque, "android-01", v7)
    await pronto(parque, "android-02", v7)
    v8 = versao(parque, codigo=8)
    parque.state.distribute(v8, instance_ids=["android-01"])         # type: ignore[union-attr]
    await pronto(parque, "android-01", v8)

    qa = next(a for a in vitrine(parque.state) if a["package"] == PACOTE)  # type: ignore[arg-type]
    assert qa["promoted"]["id"] == v8
    por_versao = {v["release_id"]: v["devices"] for v in qa["by_version"]}
    assert por_versao[v8] == 1 and por_versao[v7] == 1
    assert qa["outdated"] == 1                                       # android-02 ficou na 7
    assert qa["failed"] == 0 and qa["devices_with_app"] >= 2


# ==================================================================== as rotas
async def test_rotas_da_loja_cadastro_previa_e_vitrine(parque: Harness) -> None:
    from app.main import create_app

    falsificar(parque)
    rid = versao(parque)
    app = create_app(parque.cfg, state=parque.state)
    app.state.poc = parque.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/apps", json={"name": "TikTok", "package": "com.zhiliaoapp.musically",
                                            "category": "social"})
        assert r.status_code == 200 and r.json()["category"] == "social"
        r = await c.post("/api/apps", json={"name": "TikTok de novo", "package": "com.zhiliaoapp.musically"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "package_exists"
        r = await c.post("/api/apps", json={"name": "X", "package": "com.x.app", "category": "jogos"})
        assert r.status_code == 422                                  # categoria fora da lista do dono

        r = await c.post(f"/api/releases/{rid}/lifecycle",
                         json={"verb": "distribute", "instance_ids": ["android-01"], "dry_run": True})
        assert r.status_code == 200 and r.json()["accepted"] is False and r.json()["dry_run"] is True
        assert [d["id"] for d in r.json()["devices"]] == ["android-01"]
        assert estado(parque, "android-01") is None

        r = await c.post(f"/api/releases/{rid}/lifecycle", json={"verb": "distribute", "count": 1,
                                                                  "instance_ids": ["android-01"]})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "lifecycle_refused"

        r = await c.get("/api/app-store")
        assert r.status_code == 200
        tiktok = next(a for a in r.json() if a["package"] == "com.zhiliaoapp.musically")
        assert tiktok["category"] == "social" and tiktok["releases"] == 0 and tiktok["promoted"] is None


# ==================================================================== proxy do aparelho
async def test_proxy_aplica_nos_ligados_espera_os_desligados_e_prova_lendo_de_volta(parque: Harness) -> None:
    from app.main import create_app

    falsos = falsificar(parque)
    devs = parque.state.devices                                      # type: ignore[union-attr]
    rt2 = devs.get("android-02")
    await devs.stop_instance(rt2)
    app = create_app(parque.cfg, state=parque.state)
    app.state.poc = parque.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/proxies", json={"name": "Escritório", "host": "10.0.0.5;reboot", "port": 3128})
        assert r.status_code == 422                                  # nada de shell dentro do host
        r = await c.post("/api/proxies", json={"name": "Escritório", "host": "10.0.0.5", "port": 3128})
        assert r.status_code == 201
        pid = r.json()["id"]

        # O parque inteiro nunca é inferido: sem alvo, ou com os dois, recusa sem gravar nada.
        r = await c.post("/api/proxies/apply", json={"proxy_id": pid})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "target_required"
        r = await c.post("/api/proxies/apply", json={"proxy_id": pid, "all": True, "instance_ids": ["android-01"]})
        assert r.status_code == 400
        r = await c.post("/api/proxies/apply", json={"proxy_id": pid, "all": True, "dry_run": True})
        assert {d["id"] for d in r.json()["devices"]} == {"android-01", "android-02", "android-03"}
        assert parque.state.db.one("SELECT * FROM device_proxy_state") is None  # type: ignore[union-attr]

        r = await c.post("/api/proxies/apply", json={"proxy_id": pid, "instance_ids": ["android-01", "android-02"],
                                                     "dry_run": True})
        assert {d["id"]: d["outcome"] for d in r.json()["devices"]} == {"android-01": "would_start",
                                                                        "android-02": "pending"}
        assert falsos["android-01"].shell_calls == []
        assert parque.state.db.one("SELECT * FROM device_proxy_state") is None  # type: ignore[union-attr]

        r = await c.post("/api/proxies/apply", json={"proxy_id": pid, "instance_ids": ["android-01", "android-02"]})
        assert {d["id"]: d["outcome"] for d in r.json()["devices"]} == {"android-01": "started",
                                                                        "android-02": "pending"}

        def aplicado(iid: str) -> bool:
            row = parque.state.db.one("SELECT state FROM device_proxy_state WHERE instance_id=?", (iid,))  # type: ignore[union-attr]
            return row is not None and row["state"] == "applied"

        await parque.wait(lambda: aplicado("android-01"), what="proxy aplicado no android-01")
        assert falsos["android-01"].proxy == "10.0.0.5:3128"

        await ligar(parque, rt2)                                     # desligado recebe quando liga
        await parque.wait(lambda: aplicado("android-02"), timeout=30, what="proxy aplicado ao ligar")
        assert falsos["android-02"].proxy == "10.0.0.5:3128"

        r = await c.delete(f"/api/proxies/{pid}")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "proxy_in_use"

        r = await c.post("/api/proxies/apply", json={"proxy_id": None, "instance_ids": ["android-01", "android-02"]})
        await parque.wait(lambda: falsos["android-01"].proxy == ":0" and aplicado("android-01"), what="proxy tirado")
        await parque.wait(lambda: aplicado("android-02"), what="proxy tirado no 02")
        assert (await c.delete(f"/api/proxies/{pid}")).status_code == 204
        lista = (await c.get("/api/proxies")).json()
        assert lista["profiles"] == [] and LOJA not in {d["instance_id"] for d in lista["devices"]}


async def test_proxy_que_o_aparelho_nao_confirma_vira_falha_e_nao_se_repete(parque: Harness) -> None:
    from app.devices.proxy import ProxyApplyBody, ProxyInput, aplicar, criar

    falsos = falsificar(parque)
    st = parque.state
    pid = criar(st, ProxyInput(name="Lab", host="proxy.lab", port=8080), "teste")["id"]  # type: ignore[arg-type]
    falsos["android-01"].responde_outro_proxy = ""                   # o aparelho "esquece" o que foi gravado
    aplicar(st, ProxyApplyBody(proxy_id=pid, instance_ids=["android-01"]))  # type: ignore[arg-type]

    def linha() -> Any:
        return st.db.one("SELECT * FROM device_proxy_state WHERE instance_id='android-01'")  # type: ignore[union-attr]

    await parque.wait(lambda: linha() is not None and linha()["state"] == "failed", what="proxy recusado")
    assert "o aparelho responde" in linha()["detail"]
    gravacoes = sum(1 for c in falsos["android-01"].shell_calls if c.startswith("settings put"))

    devs = st.devices                                                # type: ignore[union-attr]
    rt = devs.get("android-01")
    await devs.stop_instance(rt)
    await ligar(parque, rt)
    await parque.wait(lambda: rt.id not in st.scheduler.workers, what="trabalho do ligar encerrado")  # type: ignore[union-attr]
    assert sum(1 for c in falsos["android-01"].shell_calls if c.startswith("settings put")) == gravacoes


async def test_xapk_ilegivel_enviado_pelo_painel_vira_recusa_com_motivo(parque: Harness) -> None:
    """Contêiner corrompido é recusa de validação como as outras: desfecho `ok=False` com o motivo, não 500."""
    from app.main import create_app

    parque.state.releases.inspector = StubInspector({})              # type: ignore[union-attr,assignment]
    app = create_app(parque.cfg, state=parque.state)
    app.state.poc = parque.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/releases/upload", params={"filename": "tiktok.xapk", "set_id": "xapk-ruim", "final": "true"},
                         content=b"isto nao e um zip")
    assert r.status_code == 201, r.text
    assert r.json()["imported"]["ok"] is False and "ilegível" in r.json()["imported"]["reason"]
    assert not (parque.state.cfg.apk_inbox / "upload-xapk-ruim").exists()  # type: ignore[union-attr]


async def test_aparelho_ocupado_na_hora_de_distribuir_recebe_quando_fica_livre(parque: Harness) -> None:
    """Ligado e ocupado não "entra no ar" de novo: sem a varredura, o app secundário ficava pendente até o aparelho
    ser desligado e religado. Ela entrega quando ele fica livre, sem ligar ninguém."""
    from app.vitrine import convergir_ligados

    falsos = falsificar(parque)
    st = parque.state
    rid = versao(parque, OUTLOOK, 3)
    ocupado = asyncio.get_running_loop().create_future()             # o aparelho está com outro trabalho
    st.scheduler.workers["android-02"] = ocupado                      # type: ignore[union-attr,assignment]
    saida = st.distribute(rid, instance_ids=["android-02"])          # type: ignore[union-attr]
    assert saida[0]["outcome"] == "pending"
    assert convergir_ligados(st) == []                               # type: ignore[arg-type]  # ainda ocupado
    assert falsos["android-02"].calls == []

    st.scheduler.workers.pop("android-02")                           # type: ignore[union-attr]
    ocupado.cancel()
    assert convergir_ligados(st) == ["android-02"]                   # type: ignore[arg-type]
    await pronto(parque, "android-02", rid, OUTLOOK)
    assert convergir_ligados(st) == []                               # type: ignore[arg-type]  # nada mais pendente


async def test_n_aparelhos_prefere_quem_ja_tem_o_app_numa_versao_antiga(parque: Harness) -> None:
    """ "Atualizar 1" atualiza quem está atrás, não instala em quem nunca teve — mesmo com id maior."""
    falsificar(parque)
    v7 = versao(parque, codigo=7)
    parque.state.distribute(v7, instance_ids=["android-03"])         # type: ignore[union-attr]
    await pronto(parque, "android-03", v7)
    v8 = versao(parque, codigo=8)
    previa = parque.state.distribute(v8, count=1, dry_run=True)      # type: ignore[union-attr]
    assert [d["id"] for d in previa] == ["android-03"]


async def test_editar_app_para_pacote_de_outro_e_recusado(parque: Harness) -> None:
    from app.main import create_app

    app = create_app(parque.cfg, state=parque.state)
    app.state.poc = parque.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.put("/api/apps/instagram", json={"package": PACOTE})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "package_exists"
        r = await c.put("/api/apps/instagram", json={"package": "com.instagram.android", "category": "social"})
        assert r.status_code == 200 and r.json()["category"] == "social"   # o próprio pacote não conta


def _zip_com_apks(destino: Path, entradas: dict[str, bytes]) -> Path:
    import zipfile

    with zipfile.ZipFile(destino, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for nome, dados in entradas.items():
            zf.writestr(nome, dados)
    return destino


def test_conteiner_que_extrai_alem_do_teto_e_recusado_sem_deixar_nada_no_disco(tmp_path: Path, monkeypatch) -> None:
    """Revisão do PR #10: o upload limita o tamanho COMPACTADO; uma bomba de zip de poucos KB extraía sem teto e
    podia encher o disco temporário antes da inspeção recusar. O teto vale pelo cabeçalho e pelos bytes reais."""
    import tempfile
    import zipfile

    from app.releases import catalog

    pastas: list[Path] = []

    def mkdtemp(prefix: str = "") -> str:
        p = tmp_path / f"{prefix}{len(pastas)}"
        p.mkdir()
        pastas.append(p)
        return str(p)

    monkeypatch.setattr(tempfile, "mkdtemp", mkdtemp)
    monkeypatch.setattr(catalog, "MAX_CONTAINER_EXTRACTED_BYTES", 1024 * 1024)
    bomba = _zip_com_apks(tmp_path / "bomba.xapk", {"base.apk": bytes(3 * 1024 * 1024)})   # 3 MB de zeros → ~3 KB
    assert bomba.stat().st_size < 64 * 1024

    # 1) pelo cabeçalho
    with pytest.raises(ReleaseValidationError, match="passaria de 1 MB"):
        catalog._extract_container(bomba)
    # 2) cabeçalho mentindo (file_size zerado para passar pelo teto declarado): o zipfile não lê além do declarado,
    #    o CRC não fecha e o contêiner é recusado como ilegível — sem extrair os 3 MB
    original = zipfile.ZipFile.infolist

    def mentir(self):  # type: ignore[no-untyped-def]
        infos = original(self)
        for i in infos:
            i.file_size = 0
        return infos

    monkeypatch.setattr(zipfile.ZipFile, "infolist", mentir)
    with pytest.raises(ReleaseValidationError, match="ileg"):
        catalog._extract_container(bomba)
    monkeypatch.setattr(zipfile.ZipFile, "infolist", original)
    # 3) contêiner com APKs demais também limpa o que criou (antes ficava a pasta temporária para trás)
    muitos = _zip_com_apks(tmp_path / "muitos.xapk", {f"s{i}.apk": b"x" for i in range(catalog.MAX_FILES_PER_SET + 1)})
    with pytest.raises(ReleaseValidationError, match="limite"):
        catalog._extract_container(muitos)
    assert pastas and not any(p.exists() for p in pastas)            # nenhuma extração parcial ficou no disco

    # Dentro do teto, extrai normalmente — e nome repetido em pastas diferentes não sobrescreve.
    ok = _zip_com_apks(tmp_path / "ok.xapk", {"a/base.apk": b"1", "b/base.apk": b"2"})
    conjunto = catalog._extract_container(ok)
    assert sorted(p.read_bytes() for p in conjunto.files) == [b"1", b"2"]


async def test_pedido_de_proxy_que_muda_durante_a_aplicacao_nao_fica_perdido(parque: Harness) -> None:
    """Revisão do PR #10: com o proxy A sendo aplicado, pedir o B grava B e fica `pending` (aparelho ocupado). O fim
    do trabalho do A gravava `applied` por cima — linha "aplicada" com B pedido e A no aparelho, e ninguém mais
    aplicava o B. Agora o fim do A devolve a linha a `pending`, e a próxima passada aplica o B."""
    from app.devices.proxy import ProxyApplyBody, ProxyInput, aplicar, criar
    from app.vitrine import convergir_ligados

    falsos = falsificar(parque)
    st = parque.state
    a = criar(st, ProxyInput(name="A", host="10.0.0.1", port=3128), "teste")["id"]  # type: ignore[arg-type]
    b = criar(st, ProxyInput(name="B", host="10.0.0.2", port=8080), "teste")["id"]  # type: ignore[arg-type]
    rt = st.devices.get("android-01")                                # type: ignore[union-attr]
    original = rt.executor.run
    pedidos: list[Any] = []

    async def run(fn: Any, *args: Any, **kw: Any) -> Any:
        r = await original(fn, *args, **kw)
        if args and str(args[0]).startswith("settings put global http_proxy 10.0.0.1") and not pedidos:
            # A pessoa pede o B no meio da aplicação do A: o aparelho está ocupado com o trabalho do A.
            pedidos.append(aplicar(st, ProxyApplyBody(proxy_id=b, instance_ids=["android-01"])))  # type: ignore[arg-type]
        return r

    rt.executor.run = run                                            # type: ignore[method-assign]

    def linha() -> Any:
        return st.db.one("SELECT * FROM device_proxy_state WHERE instance_id='android-01'")  # type: ignore[union-attr]

    aplicar(st, ProxyApplyBody(proxy_id=a, instance_ids=["android-01"]))  # type: ignore[arg-type]
    await parque.wait(lambda: bool(pedidos) and "android-01" not in st.scheduler.workers,  # type: ignore[union-attr]
                      what="trabalho do A encerrado")
    assert pedidos[0][0]["outcome"] == "pending"                     # o B não pôde começar: aparelho ocupado
    assert linha()["desired_proxy_id"] == b and linha()["state"] == "pending"
    assert falsos["android-01"].proxy == "10.0.0.1:3128"             # o aparelho ainda está no A

    assert convergir_ligados(st) == ["android-01"]                   # type: ignore[arg-type]
    await parque.wait(lambda: linha()["state"] == "applied", what="B aplicado")
    assert falsos["android-01"].proxy == "10.0.0.2:8080" and linha()["observed_value"] == "10.0.0.2:8080"
