"""31.76: a falha SÓ da imagem (o screencap), com a árvore já lida, não derruba a observação do laço do ator.

Medida real de 05/10/2026 (aparelho sobrecarregado, load 18 a 20): "DriverTimeout: screencap (na origem) excedeu 25s"
numa página comum, com a árvore saindo (47 elementos). Antes, o `DriverTimeout` da imagem ia a `_stuck` (dreno de até
180 s, aparelho retido) e a `FalhaDeLeitura` contava erro seguido até "A leitura da tela seguiu falhando".

Agora `observe(tolerar_falha_da_imagem=True)` — ligado só pelo laço do ator — devolve a árvore, `jpeg=None` e
`image_omitted="capture_failed"`, desde que o tamanho da tela se saiba sem a imagem; o executor decide pela árvore (e,
no `DriverTimeout`, só depois de o executor do aparelho ficar livre). Captura que falhou nunca é prova.

Nível de prova: `simulated` — aparelho falso do harness (porta base 5640) com o screencap quebrado por injeção.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

import pytest

from app.automation.driver import DriverBusy, DriverTimeout, FalhaDeLeitura
from app.metricas import metricas
from app.planning.provider import Decision, Usage
from app.taskqueue import executor as executor_mod
from app.taskqueue.executor import StepExecutor

from .conftest import Harness
from .fake_device import PNG
from .test_observacao_arvore_primeiro import _pronto

ERRO = "screencap falhou em emulator-5554"


def _screencap_que_falha(fake: Any, *, depois: int = 1, vezes: int | None = None, travar_s: float = 0.0,
                         enquanto: Callable[[], bool] | None = None,
                         nas_chamadas: set[int] | None = None) -> dict[str, int]:
    """Troca o `screenshot_png` do aparelho falso: as `depois` primeiras capturas saem, e as `vezes` seguintes (todas,
    se `None`) falham — com `FalhaDeLeitura` ou, com `travar_s`, ficando presas na thread do aparelho por esse tempo
    (o executor estoura o prazo antes e o screencap vira zumbi). A primeira que sai é o que ensina o tamanho da tela.
    `enquanto`: só falha enquanto for verdadeiro (ex.: antes de a mensagem sair, para a verificação final ter imagem).
    `nas_chamadas`: falha exatamente nas chamadas de número N (1 = a primeira) e sai nas outras; ignora o resto."""
    original = fake.screenshot_png
    contagem = {"chamadas": 0, "falhas": 0}

    def screenshot_png() -> bytes:
        contagem["chamadas"] += 1
        if nas_chamadas is not None:
            falha = contagem["chamadas"] in nas_chamadas
        else:
            falha = (contagem["chamadas"] > depois and (vezes is None or contagem["falhas"] < vezes)
                     and (enquanto is None or enquanto()))
        if falha:
            contagem["falhas"] += 1
            fake._enter("screenshot")
            try:
                if travar_s:
                    time.sleep(travar_s)
                    return PNG
            finally:
                fake._leave()
            raise FalhaDeLeitura(ERRO)
        return original()

    fake.screenshot_png = screenshot_png
    return contagem


# ---------------------------------------------------------------- observe (manager)
async def test_observe_tolerante_devolve_a_arvore_sem_imagem_quando_o_screencap_falha(harness: Harness) -> None:
    devs, rt, fake = await _pronto(harness)
    fake.screen = "home"
    await devs.observe(rt, timeout=5)                         # um frame desta geração: 720x1280
    _screencap_que_falha(fake, depois=0)
    obs = await devs.observe(rt, timeout=5, imagem=True, tolerar_falha_da_imagem=True)
    assert obs.jpeg is None and obs.image_omitted == "capture_failed" and obs.image_at is None
    assert (obs.width, obs.height) == (720, 1280) and obs.tree.elements and obs.tree_at
    assert obs.captura_falha is not None and obs.captura_falha.startswith("FalhaDeLeitura")
    assert obs.captura_excedeu_prazo is False and not obs.sensitive
    assert metricas.valor("captura.total", origem="observacao", resultado="falha") == 1
    assert rt.capture_failures == 0                           # a série da PRÉVIA do painel não é da observação


async def test_observe_tolerante_marca_o_prazo_estourado_quando_o_screencap_trava(harness: Harness) -> None:
    devs, rt, fake = await _pronto(harness)
    fake.screen = "home"
    await devs.observe(rt, timeout=5)
    _screencap_que_falha(fake, depois=0, vezes=1, travar_s=1.5)
    obs = await devs.observe(rt, timeout=1, imagem=True, tolerar_falha_da_imagem=True)
    assert obs.image_omitted == "capture_failed" and obs.jpeg is None and obs.tree.elements
    assert obs.captura_falha is not None and obs.captura_falha.startswith("DriverTimeout")
    assert obs.captura_excedeu_prazo is True
    assert rt.executor.has_zombie                             # o screencap segue preso na thread do aparelho
    assert await rt.executor.drain(max_wait_s=10)             # e só depois dele a próxima leitura vale


async def test_observe_sem_o_parametro_a_excecao_sobe_como_antes(harness: Harness) -> None:
    devs, rt, fake = await _pronto(harness)
    fake.screen = "home"
    await devs.observe(rt, timeout=5)
    _screencap_que_falha(fake, depois=0, vezes=1)
    with pytest.raises(FalhaDeLeitura):
        await devs.observe(rt, timeout=5, imagem=True)
    _screencap_que_falha(fake, depois=0, vezes=1, travar_s=1.5)
    with pytest.raises(DriverTimeout):
        await devs.observe(rt, timeout=1, imagem=True)
    assert await rt.executor.drain(max_wait_s=10)


async def test_observe_tolerante_sem_dimensoes_conhecidas_a_excecao_sobe(harness: Harness) -> None:
    devs, rt, fake = await _pronto(harness)
    fake.screen = "home"
    rt.dimensoes, rt.dimensoes_geracao = {}, -1               # nenhum frame desta geração: o tamanho só vem da imagem
    _screencap_que_falha(fake, depois=0, vezes=1)
    with pytest.raises(FalhaDeLeitura):
        await devs.observe(rt, timeout=5, imagem=True, tolerar_falha_da_imagem=True)
    assert metricas.valor("captura.total", origem="observacao", resultado="falha") == 0


async def test_observe_tolerante_com_a_arvore_falhando_a_excecao_sobe(harness: Harness) -> None:
    """A tolerância é só da IMAGEM: a árvore que não sai segue como sempre."""
    devs, rt, fake = await _pronto(harness)
    fake.screen = "home"
    await devs.observe(rt, timeout=5)
    fake.busy_reads = 1
    with pytest.raises(DriverBusy):
        await devs.observe(rt, timeout=5, imagem=True, tolerar_falha_da_imagem=True)


async def test_completar_imagem_tenta_de_novo_a_captura_que_falhou(harness: Harness) -> None:
    """Quem julga pela imagem (verificador) não aceita a falha tolerada do laço do ator: a captura é refeita."""
    devs, rt, fake = await _pronto(harness)
    fake.screen = "home"
    await devs.observe(rt, timeout=5)
    _screencap_que_falha(fake, depois=0, vezes=1)
    obs = await devs.observe(rt, timeout=5, imagem=True, tolerar_falha_da_imagem=True)
    assert obs.image_omitted == "capture_failed"
    completa = await devs.completar_imagem(rt, obs, timeout=5)
    assert completa.jpeg is not None and completa.image_omitted is None


# ---------------------------------------------------------------- o laço do ator
@pytest.fixture
def _rapido(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor_mod, "RECUO_UI_OCUPADA_S", 0.01, raising=False)
    metricas.limpar()


def _espiar_stuck(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    original = StepExecutor._stuck
    vistos: list[str] = []

    async def espiao(self: StepExecutor, rt: Any, step: Any, fired: bool, detail: str) -> Any:
        vistos.append(detail)
        return await original(self, rt, step, fired, detail)

    monkeypatch.setattr(StepExecutor, "_stuck", espiao)
    return vistos


def _espiar_observe(harness: Harness) -> list[Any]:
    """Na ordem, (foi só árvore, `image_omitted` devolvido) de cada `observe` do manager."""
    devs = harness.state.devices                                           # type: ignore[union-attr]
    original = devs.observe
    registro: list[Any] = []

    async def espiao(rt: Any, **kw: Any) -> Any:
        obs = await original(rt, **kw)
        registro.append((kw.get("imagem") is False, obs.image_omitted))
        return obs

    devs.observe = espiao                                                  # type: ignore[method-assign]
    return registro


def _detalhes(detail: Any) -> str:
    return " | ".join(str(s.status_detail or "") for s in detail.steps)


async def test_timeout_so_da_imagem_nao_vai_a_stuck_e_decide_pela_arvore_relida(
        harness: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, _rapido: None) -> None:
    stuck = _espiar_stuck(monkeypatch)
    harness.pular_o_tempo()
    harness.state.settings.update({"driver_call_timeout_s": 5})            # type: ignore[union-attr]
    fake = harness.fakes["android-01"]
    rt = harness.state.devices.get("android-01")                           # type: ignore[union-attr]
    contagem = _screencap_que_falha(fake, depois=1, vezes=1, travar_s=6.5)
    observes = _espiar_observe(harness)
    drenos: list[bool] = []
    drain = rt.executor.drain

    async def drain_espiado(*a: Any, **kw: Any) -> bool:
        drenos.append(rt.executor.has_zombie)                              # havia screencap preso quando se esperou
        return await drain(*a, **kw)

    rt.executor.drain = drain_espiado                                      # type: ignore[method-assign]
    with caplog.at_level(logging.INFO, logger=executor_mod.log.name):
        run = harness.run(["android-01"])
        detail = await harness.wait_run(run.id, timeout=60)
    assert contagem["falhas"] == 1                                         # o screencap preso chegou de fato ao executor
    assert stuck == [] and True in drenos                                  # nada de `_stuck`; esperou o executor livre
    assert "a captura da tela falhou" in caplog.text and "DriverTimeout" in caplog.text
    # A árvore da decisão foi lida DEPOIS do screencap preso: logo após a observação que falhou, uma só de árvore.
    j = next(n for n, e in enumerate(observes) if e[1] == "capture_failed")
    # (a receita decide esta etapa; sem a releitura, a 2ª leitura só de árvore seria a do loop seguinte, com imagem)
    assert observes[j + 1:j + 3] == [(True, "policy"), (True, "policy")]
    assert metricas.valor("captura.total", origem="observacao", resultado="falha") == 1
    assert detail.status == "completed" and len(fake.messages) == 1, _detalhes(detail)


async def test_falha_de_leitura_so_da_imagem_tres_vezes_nao_falha_a_etapa(
        harness: Harness, monkeypatch: pytest.MonkeyPatch, _rapido: None) -> None:
    stuck = _espiar_stuck(monkeypatch)
    harness.pular_o_tempo()
    fake = harness.fakes["android-01"]
    # A árvore sai em todas e o screencap falha em todas até a mensagem sair (a verificação final tem imagem). Antes,
    # 3 observações seguidas esgotando as releituras (1 + R tentativas cada) falhavam a etapa.
    contagem = _screencap_que_falha(fake, depois=1, enquanto=lambda: not fake.messages)
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id)
    assert contagem["falhas"] >= 3, contagem                               # três seguidas ou mais chegaram ao executor
    assert "leitura da tela" not in _detalhes(detail) and stuck == []
    assert detail.status == "completed" and len(fake.messages) == 1, _detalhes(detail)


async def test_arvore_falhando_segue_o_caminho_de_antes(harness: Harness, _rapido: None) -> None:
    """A tolerância é só da imagem: a UI ocupada na ÁRVORE continua contando erro e relendo, como em test_ui_ocupada."""
    harness.pular_o_tempo()
    fake = harness.fakes["android-01"]
    fake.busy_reads = 2 * (executor_mod.RELEITURAS_UI_OCUPADA + 1)         # esgota as releituras duas vezes seguidas
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id)
    assert fake.busy_reads == 0
    assert metricas.valor("automacao.ui_ocupada", resultado="persistiu") == 2
    assert metricas.valor("captura.total", origem="observacao", resultado="falha") == 0
    assert detail.status == "completed" and len(fake.messages) == 1


def _ator_pede_a_imagem(harness: Harness, vezes: int, *, need_image: bool = True) -> list[int]:
    """As `vezes` primeiras decisões do ator são `observe_screen(need_image=...)`: com `True`, o ator PEDIU a imagem.
    Devolve o `tier` de cada uma dessas decisões, na ordem (o escalonamento por erros seguidos sobe para 1)."""
    inner = harness.ai.inner
    original = inner.decide
    restantes = {"n": vezes}
    tiers: list[int] = []

    async def decide(req: Any) -> Any:
        if restantes["n"] > 0:
            restantes["n"] -= 1
            tiers.append(req.tier)
            return Decision(tool="observe_screen", args={"rationale": "[teste] olhar de novo",
                                                         "need_image": need_image}), Usage()
        return await original(req)

    inner.decide = decide
    return tiers


async def test_ator_pede_a_imagem_e_a_captura_falha_duas_vezes_cai_no_caminho_de_antes(
        harness: Harness, caplog: pytest.LogCaptureFixture, _rapido: None) -> None:
    harness.pular_o_tempo()
    fake = harness.fakes["android-01"]
    _ator_pede_a_imagem(harness, 2)
    contagem = _screencap_que_falha(fake, depois=1)                        # a 1ª sai (ensina o tamanho); as outras falham
    with caplog.at_level(logging.WARNING):
        run = harness.run(["android-01"])
        detail = await harness.wait_run(run.id)
    assert contagem["falhas"] >= 2
    # O desfecho de sempre da `fail_or_retry`, com o texto dizendo que a captura seguiu falhando (a etapa vai a nova
    # tentativa; a captura segue quebrando e a execução termina incerta, nunca com sucesso comprovado).
    assert "retry_wait — A captura da tela seguiu falhando" in caplog.text, caplog.text
    assert detail.status != "completed", _detalhes(detail)


async def test_evidencia_da_captura_que_falhou_nao_diz_tela_sensivel(
        harness: Harness, _rapido: None) -> None:
    """Leitura do 31.76: imagem AUSENTE não é tela sensível. A evidência da `fail_or_retry` depois de duas capturas
    falhas (com a imagem pedida) é texto, não `redacted`, e diz o que houve; nenhuma evidência da execução diz
    "tela sensível". Aqui a captura tardia da evidência também falha, e a nota diz isso."""
    harness.pular_o_tempo()
    fake = harness.fakes["android-01"]
    _ator_pede_a_imagem(harness, 2)
    _screencap_que_falha(fake, depois=1)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id)
    assert harness.state is not None
    linhas = harness.state.db.query("SELECT kind, note, redacted FROM evidence WHERE run_id=? ORDER BY id", (run.id,))
    assert not [r["note"] for r in linhas if "tela sensível" in (r["note"] or "")]
    falhas = [r for r in linhas if (r["note"] or "").startswith("Falha: A captura da tela seguiu falhando")]
    assert falhas, [r["note"] for r in linhas]
    for r in falhas:
        assert r["kind"] == "text" and not r["redacted"]
        assert ("(imagem não adquirida: FalhaDeLeitura)" in r["note"]
                or "(captura da tela falhou)" in r["note"]), r["note"]


async def test_tardia_que_devolve_none_vira_imagem_ausente_no_caminho_policy(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Leitura do delta do 31.76, ajustada ao ADR-089 (a plataforma não esconde tela de ninguém): `imagem_tardia` devolve
    `None` quando a geração trocou. No caminho `policy` (observação só de árvore), esse `None` vira "imagem ausente",
    SEM evidência de "tela sensível" e SEM `redacted` por sensibilidade; a evidência sem imagem não guarda arquivo."""
    from app.devices.manager import DeviceManager

    async def tardia_none(self: Any, rt: Any, *, timeout: float) -> None:
        return None

    monkeypatch.setattr(DeviceManager, "imagem_tardia", tardia_none)
    harness.cfg.file.ai.image_policy = "auto"
    harness.cfg.file.ai.rich_tree_min_elements = 3
    harness.encurtar_verificacao()
    harness.pular_o_tempo()
    harness.fakes["android-01"].send_fault = "error_lost"
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, timeout=90)
    assert harness.state is not None
    linhas = harness.state.db.query("SELECT note, path, redacted FROM evidence WHERE run_id=? ORDER BY id", (run.id,))
    assert not [r["note"] for r in linhas if "tela sensível" in (r["note"] or "")], [r["note"] for r in linhas]
    ausentes = [r for r in linhas if "(imagem ausente)" in (r["note"] or "")]
    assert ausentes and all(not r["redacted"] and not r["path"] for r in ausentes), [r["note"] for r in linhas]


async def test_ator_pede_a_imagem_e_a_captura_falha_uma_vez_segue_pela_arvore(
        harness: Harness, _rapido: None) -> None:
    harness.pular_o_tempo()
    fake = harness.fakes["android-01"]
    _ator_pede_a_imagem(harness, 1)
    contagem = _screencap_que_falha(fake, depois=1, vezes=1)
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id)
    assert contagem["falhas"] == 1
    assert detail.status == "completed" and len(fake.messages) == 1, _detalhes(detail)


async def test_falhas_toleradas_sem_imagem_pedida_nao_contam_como_erro_seguido(
        harness: Harness, caplog: pytest.LogCaptureFixture, _rapido: None) -> None:
    """A falha tolerada não entra em `errors_in_row`: 5 decisões seguidas só de `observe_screen` (que não zera o
    contador), cada uma depois de uma captura que falhou, seguem no modelo barato e a etapa não falha por erro seguido
    (o escalonamento por `errors_in_row >= 2` e as falhas por 3 ou 4 erros seguidos são o que contaria)."""
    harness.pular_o_tempo()
    fake = harness.fakes["android-01"]
    tiers = _ator_pede_a_imagem(harness, 5, need_image=False)
    contagem = _screencap_que_falha(fake, depois=1, enquanto=lambda: not fake.messages)
    with caplog.at_level(logging.WARNING):
        run = harness.run(["android-01"])
        detail = await harness.wait_run(run.id)
    assert contagem["falhas"] >= 5, contagem                               # mais que o limite de erros seguidos (4)
    assert tiers == [0] * 5, tiers                                         # nenhum escalonamento por erro seguido
    assert "seguiu falhando" not in caplog.text and "leitura da tela" not in _detalhes(detail)
    assert detail.status == "completed" and len(fake.messages) == 1, _detalhes(detail)


async def test_contador_de_falhas_zera_quando_a_observacao_volta_com_imagem(
        harness: Harness, caplog: pytest.LogCaptureFixture, _rapido: None) -> None:
    """Falha, imagem ok, falha COM imagem pedida: o contador zerou na imagem ok, então a 2ª falha é a primeira da série
    nova e não cai no caminho das duas falhas (o ator pediu a imagem nas três voltas)."""
    harness.pular_o_tempo()
    fake = harness.fakes["android-01"]
    _ator_pede_a_imagem(harness, 3)
    contagem = _screencap_que_falha(fake, nas_chamadas={2, 4})            # 1ª sai (tamanho); falha, ok, falha, ok...
    with caplog.at_level(logging.WARNING):
        run = harness.run(["android-01"])
        detail = await harness.wait_run(run.id)
    assert contagem["falhas"] == 2, contagem
    assert "A captura da tela seguiu falhando" not in caplog.text
    assert detail.status == "completed" and len(fake.messages) == 1, _detalhes(detail)
