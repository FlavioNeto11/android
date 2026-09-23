"""Saúde do CONVIDADO: o Android de dentro, não o adb (item 3.1 do plano-100; achados #1, #112, #133).

O defeito, do jeito que doía: `online` significava "o `adb get-state` respondeu `device`". Com o `system_server`
morto — medido em 21/09/2026 em três dos quatro emuladores remotos ligados — o adb responde, `sys.boot_completed`
continua `1`, e nada funciona ali: nem Instagram, nem Appium, nem instalação. O painel dizia `online`, `attention`
era nulo, o escalonador despachava tarefa e a sessão de automação falhava a cada 90 s, para sempre, gravando ~150
eventos por aparelho por tarde sem que ninguém fosse avisado.

Agora: sonda além do `boot_completed`, N falhas seguidas de sessão viram `error` com `attention`, a nova tentativa
é espaçada, o evento repetido não é regravado, e com `desired_state=online` sai um `restart` rastreável.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from app.devices.adb import Adb, AdbError
from app.devices.manager import FALHAS_DE_SESSAO_PARA_DEGRADAR, MAX_REINICIOS_DE_REMEDIACAO
from app.models import ControlOwner, InstanceState
from .conftest import Harness


# ---------------------------------------------------------------- a sonda, no nível do adb
class _Tools:
    adb = "adb"

    def env(self) -> dict[str, str]:
        return {}


def _adb(monkeypatch: pytest.MonkeyPatch, saida: str, rc: int = 0) -> Adb:
    a = Adb(_Tools(), "127.0.0.1:15555")  # type: ignore[arg-type]

    class Res:
        returncode = rc
        stdout = saida
        stderr = ""

    monkeypatch.setattr(a, "_run", lambda *_a, **_k: Res())
    return a


def test_convidado_vivo_e_convidado_morto(monkeypatch: pytest.MonkeyPatch) -> None:
    vivo = "Service activity: found\nService package: found\nService settings: found\n"
    assert _adb(monkeypatch, vivo).framework_alive() is True
    # O que o parque respondeu de verdade nos aparelhos travados.
    morto = "Service activity: not found\nService package: not found\nService settings: not found\n"
    assert _adb(monkeypatch, morto).framework_alive() is False
    # Um serviço só já basta: sem `package` não se instala nem se lista nada.
    parcial = "Service activity: found\nService package: not found\nService settings: found\n"
    assert _adb(monkeypatch, parcial).framework_alive() is False


def test_nao_sei_nunca_vira_morto(monkeypatch: pytest.MonkeyPatch) -> None:
    """adb que não respondeu é falta de informação, não diagnóstico: levanta, e quem chama não degrada."""
    with pytest.raises(AdbError):
        _adb(monkeypatch, "", rc=1).framework_alive()
    with pytest.raises(AdbError):          # resposta pela metade (adb lento, saída truncada)
        _adb(monkeypatch, "Service activity: found\n").framework_alive()


def test_lista_de_pacotes_vazia_nao_passa_por_sucesso(monkeypatch: pytest.MonkeyPatch) -> None:
    """`pm` fora do ar devolvia HTTP 200 `{"packages": []}` — "não há nada instalado", com cara de sucesso."""
    a = _adb(monkeypatch, "cmd: Can't find service: package\n", rc=1)
    with pytest.raises(AdbError):
        a.list_packages()
    # Aparelho vivo e sem app de terceiro: lista vazia é a VERDADE, e continua sendo devolvida.
    assert _adb(monkeypatch, "").list_packages(third_party_only=True) == []


# ---------------------------------------------------------------- a sonda, no gerenciador
@pytest.mark.asyncio
async def test_convidado_morto_derruba_o_aparelho_com_motivo(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        rt = h.state.devices.get("android-01")
        assert rt.state == InstanceState.online
        h.fakes["android-01"].guest_dead = True
        motivo = await h.state.devices.conferir_saude(rt)
        assert motivo is not None and "system_server" in motivo
        h.state.devices._degradar(rt, motivo)
        assert rt.state == InstanceState.error
        assert rt.attention == motivo                       # antes: `online` com attention nulo
        assert rt.pid == rt.pid                              # degradar NÃO apaga o PID (o processo segue vivo)
        # E entra na saúde do sistema, que antes só falava do Appium.
        assert any(p.code == "devices_degraded" for p in h.state.health().problems)
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_adb_mudo_nao_degrada(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        rt = h.state.devices.get("android-01")
        h.fakes["android-01"].guest_mudo = True
        assert await h.state.devices.conferir_saude(rt) is None
        assert rt.state == InstanceState.online
    finally:
        await h.state.stop()


# ---------------------------------------------------------------- falhas seguidas de sessão
@pytest.mark.asyncio
async def test_n_falhas_de_sessao_viram_estado_e_atencao(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        d = h.state.devices
        rt = d.get("android-01")
        monkeypatch.setattr(d, "io_factory", None)           # sai do desvio de teste de `ensure_automation`
        monkeypatch.setattr(d.appium, "is_up", lambda *_a, **_k: True)

        def explode() -> None:
            raise RuntimeError("Error executing adbExec ... settings delete global hidden_api_policy ... code 20")

        monkeypatch.setattr(rt.session, "close", lambda: None)
        monkeypatch.setattr(rt.session, "delete_stale", lambda *_a: None)
        monkeypatch.setattr(rt.adb, "remove_forward", lambda *_a: None)
        monkeypatch.setattr(rt.session, "connect", explode)
        eventos: list[str] = []
        h.state.bus.subscribe_all(lambda e: eventos.append(e.message)) if hasattr(h.state.bus, "subscribe_all") else None

        for _ in range(FALHAS_DE_SESSAO_PARA_DEGRADAR):
            assert await d.ensure_automation(rt) is False
            rt.state = InstanceState.online                  # o degradado é conferido no fim; solta para repetir
        assert rt.automation_failures >= FALHAS_DE_SESSAO_PARA_DEGRADAR
        assert rt.attention is not None and "vezes seguidas" in rt.attention
        # A espera cresce: fixa em 90 s eram ~40 tentativas por hora, cada uma com evento persistido.
        assert d._espera_da_proxima_sessao(rt) > 90

        # Aparelho que VOLTA ao ar começa de novo. Sem este reset, o emulador reiniciado pela remediação chegava
        # com o contador cheio e a primeira recusa pós-boot — comum, por isso o executor tenta 3× — o derrubava
        # na hora, queimando o teto de reinícios até ficar em `error` para sempre.
        rt.state = InstanceState.stopped
        d._set_state(rt, InstanceState.online, "ligou")
        assert (rt.automation_failures, rt.health_failures, rt.automation_last_error) == (0, 0, None)
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_degradar_repetido_nao_vira_evento_novo(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        d = h.state.devices
        rt = d.get("android-01")
        publicados: list[str] = []
        d.publish = lambda rt_, message=None, level="info": publicados.append(message or "")  # type: ignore[assignment]
        d._degradar(rt, "morto")
        d._degradar(rt, "morto")
        d._degradar(rt, "morto")
        assert len(publicados) == 1
    finally:
        await h.state.stop()


# ---------------------------------------------------------------- remediação
@pytest.mark.asyncio
async def test_degradado_com_desired_online_pede_restart_com_teto(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        d = h.state.devices
        rt = d.get("android-01")
        pedidos: list[tuple[str, str]] = []
        d.on_remediation_needed = lambda iid, motivo: pedidos.append((iid, motivo))

        d.set_desired_state(rt, InstanceState.stopped.value)
        d._degradar(rt, "morto A")
        assert pedidos == []                                  # ninguém pediu este aparelho no ar

        d.set_desired_state(rt, InstanceState.online.value)
        rt.state, rt.attention = InstanceState.online, None
        d._degradar(rt, "morto B")
        assert [p[0] for p in pedidos] == ["android-01"]

        # O gerenciador só respeita o INTERVALO entre pedidos; o teto (a escada) é de `api.remediar`, contado no
        # histórico de comandos. Dentro do intervalo, nada; passado ele, pede de novo — quantas vezes for.
        rt.state, rt.attention = InstanceState.online, None
        d._degradar(rt, "morto dentro do intervalo")
        assert len(pedidos) == 1
        for i in range(MAX_REINICIOS_DE_REMEDIACAO + 3):
            rt.restart_backoff_until = 0.0
            rt.state, rt.attention = InstanceState.online, None
            d._degradar(rt, f"morto {i}")
        assert len(pedidos) == 1 + MAX_REINICIOS_DE_REMEDIACAO + 3

        # Com alguém no controle do aparelho, nada de reiniciar por baixo.
        rt.restart_attempts, rt.restart_backoff_until = 0, 0.0
        rt.control = ControlOwner.user
        rt.state, rt.attention = InstanceState.online, None
        antes = len(pedidos)
        d._degradar(rt, "morto sob controle manual")
        assert len(pedidos) == antes
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_restart_de_remediacao_e_um_comando_rastreavel(tmp_path: Path) -> None:
    """A remediação não é um efeito escondido: nasce como comando, com verbo, motivo e histórico."""
    from app.api import remediar_reiniciando

    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        rt = s.devices.get("android-01")
        # Sem worker que declare `restart`, não há remediação automática a pedir (e não se abre comando morto).
        verbos, rt.worker_verbs = rt.worker_verbs, ["start", "stop"]
        assert remediar_reiniciando(s, "android-01", "morto") is None
        assert s.commands.recent("android-01", 10) == []

        rt.worker_verbs = verbos or ["restart", "start", "stop"]
        enviados: list[Any] = []

        async def _falso(*args: Any, **_k: Any) -> None:
            enviados.append(args)

        import app.api as api_mod

        original, api_mod._do_action = api_mod._do_action, _falso
        try:
            cid = remediar_reiniciando(s, "android-01", "o system_server caiu")
            assert cid is not None
            await asyncio.sleep(0)
            linha = s.commands.get(cid)
            assert linha["verb"] == "restart" and linha["requested_by"] == "system"
        finally:
            api_mod._do_action = original
    finally:
        await h.state.stop()


# ---------------------------------------------------------------- adb mudo e pressão (android-12, 23/09)
@pytest.mark.asyncio
async def test_tres_sondas_mudas_seguidas_viram_doenca(tmp_path: Path) -> None:
    """Uma sonda muda é falta de informação; três seguidas num aparelho `online` são o convidado travado — o
    android-12 ficou assim, com a sonda devolvendo `None` para sempre e o aparelho "online" sem servir."""
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        d = h.state.devices
        rt = d.get("android-01")
        rt.io.guest_mudo = True
        assert await d.conferir_saude(rt) is None
        assert await d.conferir_saude(rt) is None
        doente = await d.conferir_saude(rt)
        assert doente is not None and "3 sondas" in doente
        rt.io.guest_mudo = False
        assert await d.conferir_saude(rt) is None and rt.health_failures == 0
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_pressao_do_convidado_vira_aviso_sem_degradar_e_some_quando_passa(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        d = h.state.devices
        rt = d.get("android-01")
        rt.state, rt.attention = InstanceState.online, None
        rt.io.pressure = {"load1": 22.0, "mem_total_mb": 1470.0, "mem_available_mb": 85.0, "ncpu": 2.0}
        assert await d.conferir_saude(rt) is None          # vivo: não degrada
        assert rt.attention is None                        # uma sonda só não basta
        assert await d.conferir_saude(rt) is None
        assert rt.attention and rt.attention.startswith("Convidado sob pressão")
        assert rt.state == InstanceState.online
        rt.io.pressure = None                              # folgado de novo
        assert await d.conferir_saude(rt) is None
        assert rt.attention is None
    finally:
        await h.state.stop()


# ---------------------------------------------------------------- a escada, contada no histórico
def _encerrar(s: Any, cid: str) -> None:
    """Fecha o comando direto no banco: o que se testa aqui é a CONTAGEM, não a máquina de estados do comando."""
    from app.util import now_iso
    s.db.execute("UPDATE commands SET state='failed', finished_at=? WHERE id=?", (now_iso(), cid))


@pytest.mark.asyncio
async def test_escada_restart_restart_reset_e_depois_espera_seis_horas(tmp_path: Path) -> None:
    """2 restarts, depois `reset` onde o verbo existe, depois "precisa de gente" com nova rodada em 6 h. Contado
    em `commands`, então um reinício do backend não zera nada."""
    import app.api as api_mod
    from app.api import remediar

    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        rt = s.devices.get("android-01")
        rt.worker_verbs = ["start", "stop", "restart", "reset"]
        d = s.devices
        d.set_desired_state(rt, InstanceState.online.value)

        enviados: list[Any] = []

        async def _falso(*args: Any, **_k: Any) -> None:
            enviados.append(args)

        original, api_mod._do_action = api_mod._do_action, _falso
        try:
            verbos: list[str] = []
            for _ in range(3):
                cid = remediar(s, "android-01", "morto")
                assert cid is not None
                verbos.append(s.commands.get(cid)["verb"])
                await asyncio.sleep(0)
                _encerrar(s, cid)                      # o degrau terminou (sem curar): abre espaço para o próximo
            assert verbos == ["restart", "restart", "reset"]
            # Esgotada a escada: nada é aberto, o cartão diz que precisa de gente, e o prazo é de 6 h.
            antes = time.monotonic()
            assert remediar(s, "android-01", "morto") is None
            assert rt.attention and rt.attention.startswith("Precisa de gente")
            assert rt.restart_backoff_until - antes > 5 * 3600
            # A contagem é do banco: outro DeviceManager (= backend reiniciado) vê os mesmos 3.
            assert len(s.commands.remediacoes_recentes("android-01")) == 3
            # Sem `reset` declarado, o terceiro degrau não existe: vai direto ao "precisa de gente".
            eventos = s.db.query("SELECT data FROM events WHERE kind='instance.remediation' ORDER BY id")
            assert [json.loads(e["data"])["verb"] for e in eventos] == ["restart", "restart", "reset"]
        finally:
            api_mod._do_action = original
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_sem_reset_declarado_a_escada_para_no_restart(tmp_path: Path) -> None:
    import app.api as api_mod
    from app.api import remediar

    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        rt = s.devices.get("android-01")
        rt.worker_verbs = ["start", "stop", "restart"]
        s.devices.set_desired_state(rt, InstanceState.online.value)

        async def _falso(*args: Any, **_k: Any) -> None: ...

        original, api_mod._do_action = api_mod._do_action, _falso
        try:
            verbos = []
            for _ in range(2):
                cid = remediar(s, "android-01", "m")
                assert cid is not None
                verbos.append(s.commands.get(cid)["verb"])
                _encerrar(s, cid)
            assert verbos == ["restart", "restart"]
            assert remediar(s, "android-01", "m") is None
            assert "Precisa de gente" in (rt.attention or "")
        finally:
            api_mod._do_action = original
    finally:
        await h.state.stop()
