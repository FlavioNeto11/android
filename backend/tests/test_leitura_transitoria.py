"""Leitura que falhou fora do Appium não é sessão morta; sessão morta de verdade continua sendo recriada.

Pacote "leitura" (rodada seguinte ao ADR-053). Na observação, um screencap pelo adb que falha (`AndroidDeviceIO.
screenshot_png`: o `AdbError` virava `DriverError`) ou a captura na origem que não entrega a imagem fazia o executor
recriar a sessão do Appium — ele recriava em QUALQUER `DriverError` que não fosse `DriverBusy`. A sessão nem tinha
participado da leitura, e num convidado saturado a recriação custa 27–80 s (DELETE + POST /session e a
reinstrumentação do UiAutomator2). Agora a leitura que falhou fora da sessão é relida com recuo, como a UI ocupada;
recria-se quando o erro veio do Appium — e, numa AÇÃO, quando ele diz que a sessão ou a instrumentação morreu
("'DELETE /' cannot be proxied to UiAutomator2 server because the instrumentation process is not running", visto no
appium.log do central).

Nível de prova: `simulated` — aparelho falso do harness (porta base 5640), com o erro de adb passando pela conversão
de verdade de `AndroidDeviceIO`; o Appium é um driver falso.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from selenium.common.exceptions import WebDriverException

from app.automation.appium_driver import AndroidDeviceIO, AppiumSession
from app.automation.driver import DriverBusy, DriverError, DriverUnavailable, FalhaDeLeitura, sessao_perdida
from app.config import AppiumCfg
from app.devices.adb import AdbError, AdbTimeout
from app.metricas import metricas
from app.taskqueue import executor as executor_mod

from .conftest import Harness
from .fake_device import CONTACTS, INSTRUMENTACAO_MORTA
from .test_observacao_remota import _parar, _remoto
from .test_ui_ocupada import MSG_UI_OCUPADA, _espiar_recriacao


@pytest.fixture(autouse=True)
def _pular_o_tempo(request: pytest.FixtureRequest) -> None:
    """T2: o tempo das ferramentas do aparelho falso é PULADO (relógio virtual), não esperado. Só nos testes com o
    harness; o que eles provam (ordem dos fatos, contagens, desfechos) é o mesmo."""
    if "harness" in request.fixturenames:
        request.getfixturevalue("harness").pular_o_tempo()


# ---------------------------------------------------------------- a origem do erro dá o tipo
class _Adb:
    """`Adb` falso: toda leitura levanta o erro dado — o que o convidado saturado devolve."""

    def __init__(self, erro: Exception) -> None:
        self.erro = erro

    def screencap_png(self, *, timeout: float = 20) -> bytes:
        raise self.erro

    def current_focus(self) -> tuple[str | None, str | None]:
        raise self.erro

    def app_deaths(self, package: str, *, within_s: float | None = None) -> list[Any]:
        raise self.erro


def test_leitura_pelo_adb_que_falha_e_falha_de_leitura_e_nao_toca_a_sessao() -> None:
    for erro in (AdbError("screencap falhou em emulator-5554"), AdbTimeout("screencap excedeu 20 s")):
        io = AndroidDeviceIO(_Adb(erro), None)  # type: ignore[arg-type]
        for ler in (io.screenshot_png, io.current_focus, lambda: io.app_deaths("com.instagram.android")):
            with pytest.raises(FalhaDeLeitura) as e:
                ler()
            assert e.value.effect_possible is False                # leitura: nada chegou ao aparelho
            assert not sessao_perdida(e.value)                     # e a sessão do Appium nem participou


async def test_captura_na_origem_que_falha_e_falha_de_leitura(tmp_path: Path) -> None:
    """A imagem pedida ao agente do worker (`_capturar_na_origem`): a falha lá também não é do Appium do aparelho."""
    r = await _remoto(tmp_path, erro=AdbError("screencap falhou em emulator-5554"))
    try:
        with pytest.raises(FalhaDeLeitura, match="captura na origem falhou"):
            await r.h.state.devices.observe(r.rt, timeout=10, imagem=lambda _t: True, lado_max=768)  # type: ignore[union-attr]
    finally:
        await _parar(r)


def _sessao(erro: Exception) -> AppiumSession:
    class _Drv:
        @property
        def page_source(self) -> str:
            raise erro

        def execute_script(self, script: str, args: dict[str, Any]) -> None:
            raise erro

    ses = AppiumSession(AppiumCfg(), "127.0.0.1:15555", 8200, 7810, 8000)
    ses._drv = _Drv()
    return ses


def test_sessao_perdida_reconhece_a_instrumentacao_morta_e_a_conexao_recusada() -> None:
    instrumentacao = WebDriverException(INSTRUMENTACAO_MORTA.split("Message: ", 1)[1])
    with pytest.raises(DriverError) as leitura:
        _sessao(instrumentacao).page_source()
    assert not isinstance(leitura.value, (DriverBusy, FalhaDeLeitura)) and sessao_perdida(leitura.value)
    with pytest.raises(DriverError) as toque:
        _sessao(instrumentacao).tap(10, 10)
    assert sessao_perdida(toque.value) and toque.value.effect_possible is True
    # A linha útil depois de 300 caracteres: a mensagem normalizada a corta, a causa encadeada ainda a traz.
    longa = WebDriverException("x" * 400 + " connect ECONNREFUSED 127.0.0.1:8200")
    with pytest.raises(DriverError) as recusada:
        _sessao(longa).page_source()
    assert "ECONNREFUSED" not in str(recusada.value) and sessao_perdida(recusada.value)
    assert sessao_perdida(DriverUnavailable("Sessão de automação (Appium) não está pronta neste aparelho."))
    assert sessao_perdida(DriverError("InvalidSessionIdException: A session is either terminated or not started"))
    # UI ocupada veio DA sessão viva; recusa de elemento é da tela, não da sessão.
    with pytest.raises(DriverBusy) as ocupada:
        _sessao(WebDriverException(MSG_UI_OCUPADA)).page_source()
    assert not sessao_perdida(ocupada.value)
    assert not sessao_perdida(DriverError("InvalidElementStateException: Unable to perform W3C actions"))
    # Da causa vale a mensagem, não a pilha Java do servidor: uma classe "Session" lá não é sessão morta.
    recusa = WebDriverException("Unable to perform W3C actions",
                                stacktrace=["at io.appium.uiautomator2.model.Session.getElement(Session.java:52)"])
    with pytest.raises(DriverError) as elemento:
        _sessao(recusa).tap(10, 10)
    assert "session" in str(recusa).lower() and not sessao_perdida(elemento.value)


# ---------------------------------------------------------------- o executor
async def test_screencap_que_falha_na_observacao_rele_sem_recriar_a_sessao(harness: Harness,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor_mod, "RECUO_UI_OCUPADA_S", 0.01, raising=False)
    metricas.limpar()
    recriacoes = _espiar_recriacao(harness)
    fake = harness.fakes["android-01"]
    fake.screenshot_falhas = 2                                     # duas capturas sem PNG; a terceira vem
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id)
    assert fake.screenshot_falhas == 0                             # o erro de adb chegou de fato ao executor
    assert recriacoes == []                                        # nenhuma sessão derrubada por um screencap
    assert metricas.valor("automacao.leitura_falhou", resultado="relida") == 1
    assert detail.status == "completed" and len(fake.messages) == 1


async def test_screencap_que_segue_falhando_conta_erro_mas_nao_derruba_a_sessao(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor_mod, "RECUO_UI_OCUPADA_S", 0.01, raising=False)
    metricas.limpar()
    recriacoes = _espiar_recriacao(harness)
    fake = harness.fakes["android-01"]
    fake.screenshot_falhas = executor_mod.RELEITURAS_UI_OCUPADA + 1    # esgota as releituras uma vez
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id)
    assert fake.screenshot_falhas == 0
    assert metricas.valor("automacao.leitura_falhou", resultado="persistiu") == 1
    assert recriacoes == []
    assert detail.status == "completed" and len(fake.messages) == 1


async def test_instrumentacao_morta_na_observacao_recria_a_sessao(harness: Harness) -> None:
    recriacoes = _espiar_recriacao(harness)
    fake = harness.fakes["android-01"]
    fake.instrumentacao_morta_reads = 1
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id)
    assert fake.instrumentacao_morta_reads == 0
    assert any("instrumentation process is not running" in r for r in recriacoes)
    assert detail.status == "completed" and len(fake.messages) == 1


async def test_toque_com_instrumentacao_morta_recria_a_sessao(harness: Harness) -> None:
    """Numa ação só se recria com sessão morta de verdade — e a instrumentação morta é isso, mesmo sem a palavra
    "session" na mensagem (o critério de antes)."""
    recriacoes = _espiar_recriacao(harness)
    fake = harness.fakes["android-01"]
    fake.tap_instrumentacao_morta = "QA-001"                       # o toque na conversa volta sem UiAutomator2
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id)
    assert fake.tap_instrumentacao_morta is None
    assert any("instrumentation process is not running" in r for r in recriacoes)
    y = 200 + CONTACTS.index("QA-001") * 120 + 30
    assert sum(1 for c in fake.calls if c == f"tap:360,{y}") == 2  # o toque que não chegou e o que chegou
    assert detail.status == "completed" and len(fake.messages) == 1
