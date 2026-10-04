"""Item 6.2 — instalar, canário, rollback, distribuir, verificar, loja e sessão como comandos rastreáveis.

O que cada teste protege, em uma frase:

* a rota devolve `command_id` e o comando anda até um desfecho honesto (sucesso, falha ou `uncertain`);
* timeout do adb NÃO é falha: o aparelho é relido antes de qualquer decisão;
* a leitura que falha DEPOIS de uma instalação bem-sucedida deixa "a reler", nunca `install_failed`;
* instalação interrompida por reinício sai de `verifying` sozinha, sem ninguém fazer curl;
* toda mudança de estado do app por aparelho vira evento para a tela.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest
import pytest_asyncio

from app.db import loads
from app.devices.adb import AdbTimeout
from app.models import CommandState, InstalledAppState, InstanceState
from app.releases.service import InstalacaoIncerta

from .conftest import Harness
from .test_app_releases import (INSTAGRAM, QA_APK, FakeAdbDevice, FakeRt, StubInspector, build_service,
                                instalador, part)


# ==================================================================== incerteza no pipeline de instalação
class AdbQueEngasga(FakeAdbDevice):
    """Aparelho que instala e depois não responde — o caso REAL dos remotos (adb shell excedeu 30 s)."""

    def __init__(self, *, falha_na_instalacao: bool = False, instala_mesmo_assim: bool = True,
                 falha_na_leitura: bool = False, **kw: Any):
        super().__init__(**kw)
        self.falha_na_instalacao = falha_na_instalacao
        self.instala_mesmo_assim = instala_mesmo_assim
        self.falha_na_leitura = falha_na_leitura
        self.leituras = 0

    def install_multiple(self, paths: list[str], *, timeout: float = 0, allow_downgrade: bool = False) -> str:
        return self._talvez(paths, timeout=timeout, allow_downgrade=allow_downgrade, multi=True)

    def install(self, path: str, *, timeout: float = 0, allow_downgrade: bool = False) -> str:
        return self._talvez([path], timeout=timeout, allow_downgrade=allow_downgrade, multi=False)

    def _talvez(self, paths: list[str], *, timeout: float, allow_downgrade: bool, multi: bool) -> str:
        if not self.falha_na_instalacao:
            return (super().install_multiple(paths, timeout=timeout, allow_downgrade=allow_downgrade) if multi
                    else super().install(paths[0], timeout=timeout, allow_downgrade=allow_downgrade))
        if self.instala_mesmo_assim:
            # O `pm` terminou; quem desistiu foi a LEITURA da resposta. É exatamente o que acontece sob disputa
            # de CPU no worker.
            self._put(paths, allow_downgrade=allow_downgrade)
        raise AdbTimeout(f"adb install excedeu {timeout or 600}s em {self.serial if hasattr(self, 'serial') else 'x'}")

    def package_info(self, package: str) -> dict[str, Any] | None:
        self.leituras += 1
        if self.falha_na_leitura:
            raise AdbTimeout("adb shell excedeu 30s")
        return super().package_info(package)


def _release(tmp_path: Path) -> tuple[Any, Any, Any, str]:
    """Um conjunto catalogado, com assinatura aprovada e pronto para instalar — pelo caminho de sempre."""
    svc, repo, db = build_service(tmp_path, StubInspector({"base.apk": part()}))
    inbox = svc.cfg.apk_inbox
    inbox.mkdir(parents=True, exist_ok=True)
    shutil.copy2(QA_APK, inbox / "base.apk")
    saidas = svc.import_inbox()
    assert len(saidas) == 1 and saidas[0].ok, (saidas[0].reason if saidas else "nada importado")
    svc.approve_signature(saidas[0].release_id)
    return svc, repo, db, str(saidas[0].release_id)


async def test_timeout_na_instalacao_relê_o_aparelho_antes_de_decretar_falha(tmp_path: Path) -> None:
    """O `pm` concluiu e a leitura da resposta estourou: o aparelho prova que instalou, e a entrega segue."""
    adb = AdbQueEngasga(falha_na_instalacao=True, instala_mesmo_assim=True)
    svc, repo, db, rid = _release(tmp_path)
    try:
        estado = await svc.install_on(FakeRt(adb), rid, instalador())
        assert estado["state"] == InstalledAppState.ready.value
        assert repo.app_state("android-01", INSTAGRAM)["installed_release_id"] == rid
    finally:
        db.close()


async def test_timeout_com_o_aparelho_provando_que_nao_instalou_e_falha_de_verdade(tmp_path: Path) -> None:
    """A releitura respondeu e a versão não está lá: aí `install_failed` é a verdade, e continua sendo."""
    from app.releases.catalog import ReleaseValidationError

    adb = AdbQueEngasga(falha_na_instalacao=True, instala_mesmo_assim=False)
    svc, repo, db, rid = _release(tmp_path)
    try:
        with pytest.raises(ReleaseValidationError):
            await svc.install_on(FakeRt(adb), rid, instalador())
        linha = repo.app_state("android-01", INSTAGRAM)
        assert linha["state"] == InstalledAppState.install_failed.value and linha["pending_op"] is None
    finally:
        db.close()


async def test_leitura_que_falha_depois_da_instalacao_fica_a_reler_e_nao_falhada(tmp_path: Path) -> None:
    """O caso registrado em campo: app instalado e funcionando, painel exigindo 'Distribuir de novo'."""
    adb = AdbQueEngasga(falha_na_leitura=True)
    svc, repo, db, rid = _release(tmp_path)
    try:
        with pytest.raises(InstalacaoIncerta):
            await svc.install_on(FakeRt(adb), rid, instalador())
        linha = repo.app_state("android-01", INSTAGRAM)
        assert linha["state"] == InstalledAppState.verifying.value
        assert linha["pending_op"] is None                      # sem dono: é o estado que TEM saída
        assert "relido" in (linha["detail"] or "")
    finally:
        db.close()


async def test_erro_do_pm_continua_sendo_falha_e_nao_incerteza(tmp_path: Path) -> None:
    """Assinatura, ABI ou espaço em disco não são incerteza: o aparelho respondeu, e respondeu 'não'."""
    from app.devices.installer import InstallError

    adb = FakeAdbDevice()
    adb.install_error = "INSTALL_FAILED_INSUFFICIENT_STORAGE"
    svc, repo, db, rid = _release(tmp_path)
    try:
        with pytest.raises(InstallError):
            await svc.install_on(FakeRt(adb), rid, instalador())
        assert repo.app_state("android-01", INSTAGRAM)["state"] == InstalledAppState.install_failed.value
    finally:
        db.close()


async def test_estado_do_app_vira_evento_a_cada_mudanca(tmp_path: Path) -> None:
    """Sem isto a tela de Aplicativos só sabia o que tinha carregado ao montar."""
    adb = FakeAdbDevice()
    svc, repo, db, rid = _release(tmp_path)
    vistos: list[Any] = []
    repo.on_app_state_changed = vistos.append
    try:
        await svc.install_on(FakeRt(adb), rid, instalador())
        assert [v.state.value for v in vistos][-1] == InstalledAppState.ready.value
        assert {v.instance_id for v in vistos} == {"android-01"}
    finally:
        db.close()


# ==================================================================== o comando como entidade
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


async def test_buscar_da_loja_devolve_comando_e_ele_chega_a_um_desfecho(parque: Harness) -> None:
    s = parque.state
    assert s is not None
    chamadas: list[str] = []

    async def copiar(rt: Any, package: str, installer: Any) -> dict[str, Any]:
        chamadas.append(package)
        return {"ok": True}

    s.releases.sync_from_store = copiar  # type: ignore[assignment]
    async with _cliente(parque) as c:
        r = await c.post("/api/store/sync", json={"package": INSTAGRAM, "idempotency_key": "loja-0001"})
        assert r.status_code == 202
        cid = r.json()["command_id"]
        assert r.json()["state"] == CommandState.dispatched.value

        # Reenviar a MESMA requisição não copia duas vezes.
        de_novo = await c.post("/api/store/sync", json={"package": INSTAGRAM, "idempotency_key": "loja-0001"})
        assert de_novo.json()["command_id"] == cid and de_novo.json()["deduplicated"] is True

        await parque.wait(lambda: s.commands.get(cid)["state"] == CommandState.succeeded.value,
                          what="a busca na loja fechar o comando")
        assert chamadas == [INSTAGRAM]
        assert (await c.get(f"/api/commands/{cid}")).json()["verb"] == "store.sync"


async def test_falha_do_trabalho_fecha_o_comando_como_falha_e_timeout_como_incerto(parque: Harness) -> None:
    """Os dois desfechos que o pipeline de app não sabia distinguir: 'falhou' e 'não se sabe'."""
    s = parque.state
    assert s is not None
    rt = s.devices.devices["android-01"]
    rt.state = InstanceState.online
    from app.api import _despachar_trabalho

    async def explode() -> None:
        raise RuntimeError("o pm recusou o pacote")

    resposta = _despachar_trabalho(s, rt, "app.install", explode, label="instalação de APK")
    await parque.wait(lambda: s.commands.get(resposta["command_id"])["state"] == CommandState.failed.value,
                      what="o comando fechar como falha")

    async def some() -> None:
        raise InstalacaoIncerta("a leitura logo depois da instalação não respondeu")

    erros_antes = s.db.scalar("SELECT COUNT(*) FROM events WHERE kind='log' AND level='error'")
    incerto = _despachar_trabalho(s, rt, "app.install", some, label="instalação de APK")
    await parque.wait(lambda: s.commands.get(incerto["command_id"])["state"] == CommandState.uncertain.value,
                      what="o comando ficar incerto")
    assert s.commands.get(incerto["command_id"])["finished_at"]      # incerto também fecha o relógio
    # E o operador NÃO recebe um toast vermelho dizendo que a instalação falhou: seria a contradição exata que
    # este item existe para eliminar — comando incerto, app "a reler" e a tela afirmando falha.
    assert s.db.scalar("SELECT COUNT(*) FROM events WHERE kind='log' AND level='error'") == erros_antes
    incertos = s.db.query("SELECT message FROM events WHERE kind='log' AND level='warn'")
    assert any("resultado incerto" in e["message"] for e in incertos)


async def test_aparelho_ocupado_recusa_sem_tocar_no_aparelho(parque: Harness) -> None:
    s = parque.state
    assert s is not None
    rt = s.devices.devices["android-01"]
    rt.state = InstanceState.online
    from app.api import _despachar_trabalho

    async def demorado() -> None:
        await asyncio.sleep(5)

    primeiro = _despachar_trabalho(s, rt, "app.install", demorado, label="instalação de APK")
    with pytest.raises(Exception) as exc:
        _despachar_trabalho(s, rt, "app.install", demorado, label="instalação de APK")
    assert "device_busy" in str(getattr(exc.value, "detail", exc.value))
    assert s.commands.get(primeiro["command_id"])["state"] in (CommandState.dispatched.value,
                                                               CommandState.running.value)
    # O segundo comando fica no histórico como recusado: nada foi tocado no aparelho.
    recusados = [c for c in s.commands.recent("android-01") if c["state"] == CommandState.rejected.value]
    assert recusados and "ocupado" in (recusados[0]["reason"] or "")


async def test_recusa_do_despacho_vira_a_mesma_resposta_http_de_antes() -> None:
    """O despacho saiu de `api.py` e deixou de levantar `HTTPException`: a borda traduz `DespachoRecusado`. Quem
    chama a API não pode perceber a mudança — mesmo status e o MESMO corpo, byte a byte, que `err()` produzia."""
    from fastapi.exception_handlers import http_exception_handler

    from app.api import err, recusa_do_despacho
    from app.commands.despacho import DespachoRecusado

    recusa = DespachoRecusado(409, "device_busy", "android-01 já tem o comando 'start' em andamento",
                              command_id="cmd-1")
    nova = await recusa_do_despacho(None, recusa)  # type: ignore[arg-type]
    antiga = await http_exception_handler(None, err(409, "device_busy", "android-01 já tem o comando 'start' em "
                                                    "andamento", command_id="cmd-1"))  # type: ignore[arg-type]
    assert (nova.status_code, bytes(nova.body)) == (antiga.status_code, bytes(antiga.body))
    assert recusa.detail == {"code": "device_busy", "message": "android-01 já tem o comando 'start' em andamento",
                             "command_id": "cmd-1"}


# ==================================================================== instalação interrompida por reinício
async def test_instalacao_interrompida_por_reinicio_sai_de_verifying_sozinha(tmp_path: Path) -> None:
    """Antes: `verifying` dizia 'o estado será relido do aparelho' e não havia quem relesse — o aparelho ficava
    bloqueado para tarefas daquele app até alguém chamar a rota à mão."""
    h = Harness(tmp_path, 2)
    s = await h.boot()
    try:
        s.release_repo.upsert_app_state("android-01", INSTAGRAM, state=InstalledAppState.installing.value,
                                        pending_op="install", pending_op_at="2026-09-21T10:00:00Z")
        marca = int(s.db.scalar("SELECT COALESCE(MAX(id), 0) FROM events") or 0)
        await h.crash()
        s = await h.boot()

        # 29.78: o harness só entrega o backend depois das faxinas da subida, e a releitura automática (pelo aparelho
        # falso, que não tem o pacote) já aconteceu nesse meio-tempo. A passagem por `verifying` fica nos eventos.
        do_app = [d["app_state"] for d in (loads(r["data"]) for r in s.db.query(
            "SELECT data FROM events WHERE kind='app_state.updated' AND id>? ORDER BY id", (marca,)))
                  if d["app_state"]["instance_id"] == "android-01" and d["app_state"]["package_name"] == INSTAGRAM]
        assert [a["state"] for a in do_app][:1] == [InstalledAppState.verifying.value], "o reinício põe em verifying"
        assert do_app[0]["pending_op"] is None
        await h.wait(lambda: s.release_repo.app_state("android-01", INSTAGRAM)["state"]
                     != InstalledAppState.verifying.value, what="a releitura automática da subida")
        assert s.release_repo.app_state("android-01", INSTAGRAM)["state"] == InstalledAppState.missing.value
        assert s.pacotes_sem_desfecho("android-01") == []

        # A releitura também vale depois da subida: a mesma dívida, agora criada à mão, é paga por
        # `_reverificar_interrompidas` sem rota manual.
        s.release_repo.upsert_app_state("android-01", INSTAGRAM, state=InstalledAppState.verifying.value,
                                        pending_op=None, detail="Operação interrompida por reinício.")
        assert s.pacotes_sem_desfecho("android-01") == [INSTAGRAM]

        relidos: list[str] = []

        async def reler(rt: Any, package: str, installer: Any) -> dict[str, Any]:
            relidos.append(package)
            s.release_repo.upsert_app_state(rt.id, package, state=InstalledAppState.missing.value,
                                            detail="o aparelho não tem este pacote")
            return {}

        s.releases.verify_on = reler  # type: ignore[assignment]
        s.devices.devices["android-01"].state = InstanceState.online
        assert s._reverificar_interrompidas() == 1
        await h.wait(lambda: INSTAGRAM in relidos, what="a releitura automática acontecer")
        assert s.release_repo.app_state("android-01", INSTAGRAM)["state"] == InstalledAppState.missing.value
        assert s.pacotes_sem_desfecho("android-01") == []
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_backend_que_sobe_nao_declara_interrompida_a_instalacao_do_outro(tmp_path: Path) -> None:
    """Dois backends no MESMO banco: `reconcile_after_restart` mexia em tudo o que tivesse operação pendente.

    A instalação viva do outro backend virava `verifying` — ela continuava acontecendo no aparelho, e o banco
    passava a dizer outra coisa.
    """
    from app.releases.repository import ReleaseRepository

    svc, repo, db, rid = _release(tmp_path)
    del rid
    meu = ReleaseRepository(db, owner_id="central-A")
    outro = ReleaseRepository(db, owner_id="central-B")
    meu.upsert_app_state("android-01", INSTAGRAM, pending_op="install",
                         state=InstalledAppState.installing.value)
    outro.upsert_app_state("android-02", INSTAGRAM, pending_op="install",
                           state=InstalledAppState.installing.value)
    try:
        assert [r["instance_id"] for r in meu.pending_operations()] == ["android-01"]
        assert [r["instance_id"] for r in outro.pending_operations()] == ["android-02"]

        svc.repo = meu
        assert svc.reconcile_after_restart() == 1
        assert meu.app_state("android-01", INSTAGRAM)["state"] == InstalledAppState.verifying.value
        # A do outro backend continua exatamente onde estava.
        viva = meu.app_state("android-02", INSTAGRAM)
        assert viva["state"] == InstalledAppState.installing.value and viva["pending_op"] == "install"
    finally:
        db.close()


async def test_fechar_a_operacao_solta_o_dono(tmp_path: Path) -> None:
    """Sem soltar, a linha ficaria para sempre reivindicada por um backend que já terminou o trabalho."""
    from app.releases.repository import ReleaseRepository

    svc, repo, db, rid = _release(tmp_path)
    del svc, repo, rid
    meu = ReleaseRepository(db, owner_id="central-A")
    try:
        meu.upsert_app_state("android-01", INSTAGRAM, pending_op="install")
        assert meu.app_state("android-01", INSTAGRAM)["claimed_by"] == "central-A"
        meu.upsert_app_state("android-01", INSTAGRAM, pending_op=None, state=InstalledAppState.ready.value)
        assert meu.app_state("android-01", INSTAGRAM)["claimed_by"] is None
        assert meu.pending_operations() == []
    finally:
        db.close()


async def test_app_sem_desfecho_manda_reler_em_vez_de_bloquear_o_item(tmp_path: Path) -> None:
    """A outra metade do achado #85: a linha saía de `verifying`, mas o ITEM já estava parado esperando alguém.

    A porta do app bloqueava o objetivo com "não está pronto (estado: verifying)" — `waiting_user`, que ninguém
    retoma quando a releitura, mais tarde, resolve a linha para `ready`.
    """
    h = Harness(tmp_path, 2)
    s = await h.boot()
    try:
        rt = s.devices.devices["android-01"]
        rt.state = InstanceState.online
        pacote = "com.pocqa.messenger"
        s.release_repo.upsert_app_state("android-01", pacote, state=InstalledAppState.verifying.value,
                                        pending_op=None, detail="operação interrompida por reinício")
        porta = s._app_resolver(rt, pacote, {"status": "pending", "id": "obj-1"})
        assert porta is not None and porta[1] is not None          # há trabalho: relê o aparelho
        assert "relido do aparelho" in porta[0]

        relidos: list[str] = []

        async def reler(r: Any, package: str, installer: Any) -> dict[str, Any]:
            relidos.append(package)
            return {}

        s.releases.verify_on = reler  # type: ignore[assignment]
        await porta[1]()
        assert relidos == [pacote]

        # Operação COM dono continua intocada: ali alguém ainda está trabalhando.
        s.release_repo.upsert_app_state("android-01", pacote, state=InstalledAppState.verifying.value,
                                        pending_op="verify")
        assert s._app_resolver(rt, pacote, {"status": "pending", "id": "obj-1"}) is None
    finally:
        if h.state is not None:
            await h.state.stop()
