"""A prévia do painel não disputa o aparelho com a IA; o `drain` da etapa espera só a etapa; o relógio não é acertado
no meio de um objetivo; o aviso de pressão diz qual recurso falta.

Diagnóstico das execuções reais r-20260928165254-e31953 e r-20260928195344-02ee9e (android-06, 2 vCPU): a IA ocupou
só 4,5 % do tempo. O resto era o aparelho saturado — e o nosso código transformando lentidão em falha:

- a prévia do foco (1 s) tirava screencap pela MESMA thread do aparelho e, "atrasada", entrava mesmo com a fila
  ocupada: 850–890 capturas em 15 min, cada uma na frente da próxima ação da IA;
- o `drain()` que a etapa faz depois de um timeout esperava também esses screencaps da prévia — que não paravam de
  chegar — até estourar o teto e virar `device_stuck`;
- o relógio do convidado era acertado (`cmd alarm set-time`, na fila do aparelho) com objetivo em execução: 100
  acertos no android-06 em 28/09;
- o aviso de pressão dizia "Mais RAM resolve" com load alto e RAM sobrando (o ramo de RAM disparou 0 vezes em ~2230
  avisos).

Prova `simulated`: aparelho falso do harness (`base_console_port` 5640) e dublês de `adb`; nenhum aparelho real.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from app.automation.driver import DriverTimeout
from app.devices import manager as manager_mod
from app.devices.executor import DeviceExecutor
from app.devices.manager import DeviceRuntime
from app.metricas import metricas
from app.models import ControlOwner, InstanceDTO, InstanceState

from .conftest import Harness
from .test_previa_sob_demanda import _caps, _preparar
from .test_prontidao_sem_efeito_atrasado import _relogio_no_central


async def _dimensoes_conhecidas(devs: Any, rt: Any, fake: Any) -> None:
    """Uma observação com imagem ensina o tamanho da tela desta geração (a observação só de árvore precisa dele) e
    deixa um frame no ar; o frame é envelhecido para a próxima observação não o achar fresco."""
    fake.screen = "home"
    await devs.observe(rt, timeout=5)
    assert rt.frame is not None
    rt.frame.mono -= 60


# ---------------------------------------------------------------- (a) a prévia cede a vez; a observação publica
async def test_sob_controle_da_ia_a_previa_nao_enfileira_screencap_proprio(harness: Harness) -> None:
    devs, _ = await _preparar(harness)
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    devs.registrar_interesse("aba", [], "android-01", 20)          # o dono está no Foco deste aparelho
    assert devs.ai_begin(rt) and devs.ia_no_controle(rt)
    antes = _caps(fake)
    for pedido in (False, True, False):
        resultado, _ = await devs._volta_da_previa(rt, pedido=pedido)
        assert resultado == "ia_no_controle"
    assert _caps(fake) == antes, "com a IA no controle, a prévia não põe screencap na fila do aparelho"
    assert metricas.valor("captura.evitada", motivo="ia_no_controle") == 3
    # Vale também no laço antigo (`preview_mode=always`): o ambiente central pode estar em qualquer um dos dois.
    harness.state.settings.update({"preview_mode": "always"})      # type: ignore[union-attr]
    assert (await devs._volta_da_previa(rt))[0] == "ia_no_controle" and _caps(fake) == antes
    harness.state.settings.update({"preview_mode": "on_demand"})   # type: ignore[union-attr]
    # A IA soltou o aparelho: a prévia volta a capturar por conta própria.
    devs.ai_end(rt)
    assert not devs.ia_no_controle(rt)
    assert (await devs._volta_da_previa(rt, pedido=True))[0] == "capturada" and _caps(fake) == antes + 1
    devs.soltar_interesse("aba")


async def test_sem_ninguem_olhando_segue_sem_interesse_mesmo_com_a_ia(harness: Harness) -> None:
    """`paused` continua sendo "ninguém olhando" — a IA no controle não muda o estado da tela para o painel."""
    devs, _ = await _preparar(harness)
    rt = devs.get("android-01")
    assert devs.ai_begin(rt)
    assert (await devs._volta_da_previa(rt))[0] == "sem_interesse"
    assert devs.dto(rt).stream.status == "paused"
    devs.ai_end(rt)


async def test_observacao_so_de_arvore_da_ia_publica_o_frame_para_o_painel(harness: Harness) -> None:
    """Com a prévia cedendo a vez, quem alimenta o Foco é a observação da IA. A de árvore (a maioria no Instagram:
    árvore rica, `_want_image` diz não) não tinha imagem nenhuma para publicar — agora ela adquire uma, logo depois
    da árvore, só para o painel. O modelo continua sem imagem."""
    devs, _ = await _preparar(harness)
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    await _dimensoes_conhecidas(devs, rt, fake)
    devs.registrar_interesse("aba", [], "android-01", 20)
    assert devs.ai_begin(rt)
    fila = harness.state.bus.subscribe()                           # type: ignore[union-attr]
    anterior = rt.frame.info.id
    antes = len(fake.calls)
    obs = await devs.observe(rt, timeout=5, imagem=False, lado_max=768)
    assert fake.calls[antes:] == ["page_source", "screenshot"], "árvore primeiro; a imagem da prévia logo depois"
    assert rt.frame is not None and rt.frame.info.id != anterior
    assert rt.frame.jpeg_full and rt.frame.jpeg_thumb
    assert obs.frame_id == rt.frame.info.id
    # Para o MODELO nada mudou: sem imagem, por política (tokens, evidência e `_screen` leem estes campos).
    assert obs.jpeg is None and obs.image_omitted == "policy" and obs.image_at is None
    eventos = []
    while not fila.empty():
        ev = fila.get_nowait()
        if ev.kind == "frame" and ev.instance_id == "android-01":
            eventos.append(ev.data["frame"]["id"])
    harness.state.bus.unsubscribe(fila)                            # type: ignore[union-attr]
    assert eventos == [rt.frame.info.id], "o painel recebe o frame pelo mesmo evento da prévia"
    # Frame fresco (dentro do intervalo do foco): a observação seguinte não captura de novo.
    antes = len(fake.calls)
    await devs.observe(rt, timeout=5, imagem=False)
    assert fake.calls[antes:] == ["page_source"]
    devs.ai_end(rt)
    devs.soltar_interesse("aba")


async def test_observacao_da_ia_sem_ninguem_olhando_nao_captura_para_a_previa(harness: Harness) -> None:
    devs, _ = await _preparar(harness)
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    await _dimensoes_conhecidas(devs, rt, fake)
    assert devs.ai_begin(rt)
    antes = len(fake.calls)
    await devs.observe(rt, timeout=5, imagem=False)
    assert fake.calls[antes:] == ["page_source"], "sem espectador, a observação de árvore segue sem screencap"
    devs.ai_end(rt)


async def test_fora_do_controle_da_ia_a_observacao_de_arvore_nao_duplica_a_previa(harness: Harness) -> None:
    """Sem a IA no controle quem captura é o laço da prévia; a observação não faz um screencap a mais por cima."""
    devs, _ = await _preparar(harness)
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    await _dimensoes_conhecidas(devs, rt, fake)
    devs.registrar_interesse("aba", [], "android-01", 20)
    antes = len(fake.calls)
    await devs.observe(rt, timeout=5, imagem=False)
    assert fake.calls[antes:] == ["page_source"]
    devs.soltar_interesse("aba")


async def test_screencap_da_previa_que_falha_nao_derruba_a_observacao_da_ia(harness: Harness) -> None:
    """A imagem era só para o painel: a falha dela é falha da PRÉVIA (registrada como a do laço), nunca da
    observação — a IA já tem a árvore de que precisa."""
    devs, _ = await _preparar(harness)
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    await _dimensoes_conhecidas(devs, rt, fake)
    devs.registrar_interesse("aba", [], "android-01", 20)
    assert devs.ai_begin(rt)
    original = fake.screenshot_png

    def quebrado() -> bytes:
        from app.automation.driver import DriverError
        raise DriverError("screencap falhou", effect_possible=False)
    fake.screenshot_png = quebrado                                 # type: ignore[method-assign]
    try:
        obs = await devs.observe(rt, timeout=5, imagem=False)
    finally:
        fake.screenshot_png = original                             # type: ignore[method-assign]
    assert obs.tree is not None and obs.image_omitted == "policy"
    assert rt.capture_failures == 1 and rt.capture_error and "screencap falhou" in rt.capture_error
    assert metricas.valor("captura.total", origem="previa", resultado="falha") == 1
    devs.ai_end(rt)
    devs.soltar_interesse("aba")


# ---------------------------------------------------------------- (a') o Foco no ritmo da IA não é "desatualizado"
def _instancia_publicada(harness: Harness, rt: DeviceRuntime) -> InstanceDTO:
    """O `instance.updated` que o painel recebe — o caminho que o Foco consome no meio da execução (troca de etapa,
    aviso de pressão, atenção), e não só `dto()` chamado à mão."""
    assert harness.state is not None
    bus = harness.state.bus
    fila = bus.subscribe()
    try:
        harness.state.devices.publish(rt)
        eventos: list[InstanceDTO] = []
        while not fila.empty():
            ev = fila.get_nowait()
            if ev.kind == "instance.updated" and ev.instance_id == rt.id:
                eventos.append(InstanceDTO.model_validate(ev.data["instance"]))
    finally:
        bus.unsubscribe(fila)
    assert len(eventos) == 1, eventos
    return eventos[0]


async def test_foco_no_ritmo_da_ia_nao_acusa_frame_desatualizado(harness: Harness) -> None:
    """Com a IA no controle o frame chega uma vez por ciclo dela (árvore, modelo, ação, assentamento) — em
    r-20260928195344-02ee9e e r-20260928165254-e31953 nunca menos de 5,8 s entre duas ações. O limite de 6 s do foco
    deixava a imagem cinza com "Desatualizado" durante boa parte da execução, e o texto culpava a captura ("captura
    atrasada ou executor ocupado") por um estado que é a prévia cedendo a vez à IA de propósito."""
    devs, _ = await _preparar(harness)
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    await _dimensoes_conhecidas(devs, rt, fake)
    devs.registrar_interesse("aba", [], "android-01", 20)          # o dono está no Foco
    assert devs.ai_begin(rt)
    await devs.observe(rt, timeout=5, imagem=False)                # a observação da IA publica o frame
    assert rt.frame is not None
    rt.frame.mono -= 10                                            # um ciclo da IA depois, nenhum frame novo
    inst = _instancia_publicada(harness, rt)
    assert inst.frame is not None and inst.frame.stale is False, "frame no ritmo da IA não é desatualizado"
    assert inst.stream is not None and inst.stream.status == "live" and "IA" in inst.stream.detail
    # Muito além do ciclo: aí sim, desatualizado — e o motivo é a IA sem olhar a tela, não a captura.
    rt.frame.mono -= manager_mod.FRAME_MAX_AGE_IA_S
    dto = devs.dto(rt)
    assert dto.frame is not None and dto.frame.stale
    assert dto.stream is not None and dto.stream.status == "stale"
    assert "IA" in dto.stream.detail and "executor ocupado" not in dto.stream.detail
    # A IA soltou: a prévia volta a capturar sozinha, e o limite volta ao de sempre.
    devs.ai_end(rt)
    rt.frame.mono = time.monotonic() - 10
    dto = devs.dto(rt)
    assert dto.frame is not None and dto.frame.stale
    assert dto.stream is not None and dto.stream.status == "stale" and "IA" not in dto.stream.detail
    devs.soltar_interesse("aba")


async def test_ia_no_controle_nao_esconde_falha_de_captura_nem_a_pausa(harness: Harness) -> None:
    """O prazo maior vale só para o ritmo: falha registrada da captura continua `capture_error`, e ninguém olhando
    continua `paused` (a IA no controle não inventa espectador)."""
    devs, _ = await _preparar(harness)
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    await _dimensoes_conhecidas(devs, rt, fake)
    assert devs.ai_begin(rt)
    stream = devs.dto(rt).stream
    assert stream is not None and stream.status == "paused"
    devs.registrar_interesse("aba", [], "android-01", 20)
    assert rt.frame is not None
    rt.frame.mono = time.monotonic() - manager_mod.FRAME_MAX_AGE_IA_S - 5
    devs._falha_de_captura(rt, "DriverTimeout: screencap")
    stream = devs.dto(rt).stream
    assert stream is not None and stream.status == "capture_error"
    devs.ai_end(rt)
    devs.soltar_interesse("aba")


def test_prazo_do_frame_com_a_ia_e_o_mesmo_no_painel() -> None:
    """O painel confere a idade do frame no cliente (um backend parado não avisa): a regra tem de ser a mesma dos
    dois lados, senão um diz "ao vivo" e o outro "desatualizado"."""
    import re
    from pathlib import Path
    fonte = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / "devices"
             / "DeviceCard.tsx").read_text(encoding="utf-8")
    achado = re.search(r"AI_FRAME_MAX_AGE_MS\s*=\s*([\d_]+)", fonte)
    assert achado, "DeviceCard.tsx perdeu AI_FRAME_MAX_AGE_MS"
    assert int(achado.group(1).replace("_", "")) == int(manager_mod.FRAME_MAX_AGE_IA_S * 1000)


# ---------------------------------------------------------------- (b) drain espera só a etapa
async def test_drain_da_etapa_nao_espera_o_screencap_da_previa(harness: Harness) -> None:
    """O cenário real: um screencap da prévia está na thread do aparelho (lento: convidado saturado) e a etapa
    chama `drain()` depois de um timeout. Antes, o `drain` esperava a prévia — e as seguintes — até o teto."""
    devs, _ = await _preparar(harness)
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    devs.registrar_interesse("aba", [], "android-01", 20)
    solta = threading.Event()
    original = fake.screenshot_png

    def lento() -> bytes:
        solta.wait(10)
        return original()
    fake.screenshot_png = lento                                    # type: ignore[method-assign]
    try:
        volta = asyncio.create_task(devs._volta_da_previa(rt, pedido=True))
        await harness.wait(lambda: rt.executor.queue_depth == 1, timeout=2, what="screencap da prévia na fila")
        t0 = time.monotonic()
        assert await rt.executor.drain(poll_s=0.01, max_wait_s=1.0) is True
        assert time.monotonic() - t0 < 0.5, "o drain da etapa não espera a prévia"
    finally:
        solta.set()
        fake.screenshot_png = original                             # type: ignore[method-assign]
    assert (await volta)[0] == "capturada"
    devs.soltar_interesse("aba")


async def test_drain_continua_esperando_a_chamada_da_etapa_e_o_zumbi_dela() -> None:
    ex = DeviceExecutor("teste")
    previa_solta, etapa_solta = threading.Event(), threading.Event()
    try:
        # Prévia rodando na thread, outra da prévia na fila atrás dela: nenhuma das duas segura o drain.
        rodando = asyncio.create_task(ex.run(previa_solta.wait, 10, timeout=10, label="screencap", previa=True))
        na_fila = asyncio.create_task(ex.run(lambda: None, timeout=10, label="screencap", previa=True))
        await asyncio.sleep(0.05)
        assert await ex.drain(poll_s=0.01, max_wait_s=0.2) is True
        # Uma chamada da ETAPA atrás delas: o drain espera por ela.
        acao = asyncio.create_task(ex.run(lambda: None, timeout=10, label="toque"))
        await asyncio.sleep(0.05)                                   # a tarefa chega a submeter a chamada
        assert ex.queue_depth == 3 and await ex.drain(poll_s=0.01, max_wait_s=0.2) is False
        previa_solta.set()
        await asyncio.gather(rodando, na_fila, acao)
        assert await ex.drain(poll_s=0.01, max_wait_s=0.2) is True
        # Zumbi da prévia (estourou o prazo e segue na thread): não segura o drain...
        previa_solta.clear()
        with pytest.raises(DriverTimeout):
            await ex.run(previa_solta.wait, 10, timeout=0.05, label="screencap", previa=True)
        assert ex.has_zombie and await ex.drain(poll_s=0.01, max_wait_s=0.2) is True
        previa_solta.set()
        await ex.drain(poll_s=0.01, max_wait_s=2)
        # ...o zumbi da ETAPA segura: o efeito dela pode estar em curso.
        with pytest.raises(DriverTimeout):
            await ex.run(etapa_solta.wait, 10, timeout=0.05, label="toque")
        assert await ex.drain(poll_s=0.01, max_wait_s=0.2) is False
        etapa_solta.set()
        assert await ex.drain(poll_s=0.01, max_wait_s=2) is True
    finally:
        previa_solta.set()
        etapa_solta.set()
        ex.shutdown()


# ---------------------------------------------------------------- (c) relógio: o acerto espera a IA soltar
async def test_relogio_nao_e_acertado_com_objetivo_em_execucao(harness: Harness,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt, trilhas = _relogio_no_central(harness, monkeypatch, [-30, -30])
    assert s.devices.ai_begin(rt) and rt.control == ControlOwner.ai
    await s.devices.conferir_relogio_do_convidado(rt)
    assert trilhas == ["medir"], "com objetivo em execução o relógio só é MEDIDO (trilha de sonda), não acertado"
    assert rt.clock_state == "adiado" and rt.clock_skew_s == -30
    assert rt.state == InstanceState.online and rt.readiness_phase == "ready"
    assert not (rt.attention or "").startswith(manager_mod.RELOGIO_PREFIXO), "adiar não é 'não convergiu'"
    # Pendente: a reconferência é a antecipada, não a de 5 min.
    assert s.devices._deve_conferir_relogio(rt, time.monotonic() + manager_mod.INTERVALO_DO_RELOGIO_PENDENTE_S + 1)
    # A IA soltou: o monitor reconfere na próxima volta — e agora acerta.
    s.devices.ai_end(rt)
    assert s.devices._deve_conferir_relogio(rt, time.monotonic())
    await s.devices.conferir_relogio_do_convidado(rt)
    assert trilhas == ["medir", "medir", "acertar"] and rt.clock_state == "ok" and rt.clock_skew_s == 0


async def test_relogio_muito_fora_e_acertado_mesmo_com_a_ia(harness: Harness,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Válvula: acima de `RELOGIO_ADIAVEL_MAX_S` o próprio desvio ameaça login e TLS mais que um acerto no meio da
    etapa — um aparelho com objetivos em sequência poderia nunca ter uma folga para o acerto adiado."""
    s = harness.state
    assert s is not None
    fora = -(manager_mod.RELOGIO_ADIAVEL_MAX_S + 30)
    rt, trilhas = _relogio_no_central(harness, monkeypatch, [fora, fora])
    assert s.devices.ai_begin(rt)
    await s.devices.conferir_relogio_do_convidado(rt)
    assert trilhas == ["medir", "acertar"] and rt.clock_state == "ok"
    s.devices.ai_end(rt)


# ---------------------------------------------------------------- (d) pressão: o texto pelo ramo que disparou
async def _pressionar(devs: Any, rt: Any, pressao: dict[str, float]) -> str:
    rt.state, rt.attention, rt.pressure_strikes = InstanceState.online, None, 0
    rt.io.pressure = pressao
    assert await devs.conferir_saude(rt) is None
    assert await devs.conferir_saude(rt) is None
    assert rt.attention and rt.attention.startswith(manager_mod.PRESSAO_PREFIXO), rt.attention
    return str(rt.attention)


async def test_pressao_de_cpu_com_ram_sobrando_nao_fala_em_ram(harness: Harness) -> None:
    """O android-06 real: load muito acima das 2 vCPU com RAM livre de sobra. "Mais RAM resolve" mandava o dono
    para o remédio errado."""
    devs = harness.state.devices                                   # type: ignore[union-attr]
    rt = devs.get("android-01")
    texto = await _pressionar(devs, rt, {"load1": 12.0, "mem_total_mb": 2048.0, "mem_available_mb": 1100.0,
                                         "ncpu": 2.0})
    assert "RAM" not in texto, texto
    assert "CPU" in texto.split(":", 1)[0], "o título do aviso diz qual recurso falta"
    rt.io.pressure = None


async def test_pressao_de_ram_continua_indicando_mais_ram(harness: Harness) -> None:
    devs = harness.state.devices                                   # type: ignore[union-attr]
    rt = devs.get("android-01")
    texto = await _pressionar(devs, rt, {"load1": 0.5, "mem_total_mb": 1470.0, "mem_available_mb": 85.0,
                                         "ncpu": 2.0})
    assert "Mais RAM" in texto, texto
    rt.io.pressure = None


async def test_pressao_de_cpu_e_ram_diz_as_duas(harness: Harness) -> None:
    devs = harness.state.devices                                   # type: ignore[union-attr]
    rt = devs.get("android-01")
    texto = await _pressionar(devs, rt, {"load1": 22.0, "mem_total_mb": 1470.0, "mem_available_mb": 85.0,
                                         "ncpu": 2.0})
    assert "Mais RAM" in texto and "vCPU" in texto, texto
    rt.io.pressure = None


# ---------------------------------------------------------------- (e) métricas por instância
async def test_captura_e_observacao_medidas_por_instancia(harness: Harness) -> None:
    """A lentidão era de UM convidado (android-06); sem o rótulo, a distribuição do parque inteiro a diluía."""
    devs, _ = await _preparar(harness)
    rt, fake = devs.get("android-02"), harness.fakes["android-02"]
    fake.screen = "home"
    await devs.observe(rt, timeout=5)
    distribuicoes = metricas.snapshot()["distribuicoes"]

    def rotulos(nome: str) -> list[dict[str, str]]:
        return [d["rotulos"] for d in distribuicoes if d["nome"] == nome]
    assert rotulos("captura.ms") and all(r.get("instancia") == "android-02" for r in rotulos("captura.ms"))
    assert {r.get("parte") for r in rotulos("observacao.ms")} == {"arvore", "imagem"}
    assert all(r.get("instancia") == "android-02" for r in rotulos("observacao.ms"))
