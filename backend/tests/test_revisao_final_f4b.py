"""Revisão independente FINAL (F8) da evolução de desempenho — F4 fase B e as correções da revisão anterior.

Cada teste fixa um cenário do comportamento INTEGRADO. Os seis primeiros provavam, em `xfail(strict=True)`, um
defeito presente no SHA revisado (`815e35c`); corrigidos na F4 (fase B, rodada final), o marcador saiu e eles valem
como regressão. Os demais são prova de cobertura de um cenário que a revisão conferiu e não achou defeito.

Prova `simulated`: aparelho falso do harness, agente de verdade (`ObservacaoNaOrigem`) com ADB falso, portão e
canal de mídia pelo `TestClient`. Nada aqui toca emulador, túnel, worker real ou IA paga.
"""
from __future__ import annotations

import asyncio
import functools
import json
import time
import types
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api import _tratar_mensagem_do_worker
from app.automation.driver import DriverError
from app.devices import codificacao
from app.main import create_worker_app
from app.models import InstanceResources, InstanceState
from app.worker import observacao as observacao_do_agente
from app.workers.captura import ErroDeCaptura
from app.workers.portao import PENDENTES_POR_IP
from app.workers.protocol import FEATURE_OBSERVACAO_LOCAL, ContadorAgregado, Heartbeat

from .conftest import Harness
from .test_canal_de_midia import HOST, WID, _corpo, _enviar, _pedido_enviado, _pedir, _preparar
from .test_canal_do_worker import PAR_LOCAL
from .test_canal_do_worker import _hello as _hello_bruto
from .test_observacao_remota import _parar, _remoto
from .test_reserva_central import _dois_parados, _ram_para_um
from .test_workers import AgenteFalso, _hello


# ================================================================ defeitos (xfail estrito)
def test_corpo_em_texto_falha_o_pedido_na_hora(harness: Harness) -> None:
    """`CapturaNaOrigem.receber` promete: corpo inválido FALHA o pedido na hora. O ramo do corpo que não é binário
    (`api.worker_midia`, `not isinstance(corpo, bytes)`) levanta `ErroDeMidia` sem passar por `receber`, então o
    futuro de quem pediu só termina no prazo — com `observe`/prévia, o executor do aparelho fica preso até lá."""
    reg, agente = _preparar(harness, [FEATURE_OBSERVACAO_LOCAL])
    with TestClient(create_worker_app(harness.state), client=PAR_LOCAL) as cliente:
        futuro = _pedir(cliente, reg, lado_max=512, timeout=6.0)
        pedido = _pedido_enviado(agente)
        assert _enviar(cliente, pedido["request_id"], pedido["upload_token"], "texto no lugar da imagem")["code"] \
            == "bad_media"
        fim = time.monotonic() + 2.0
        while not futuro.done() and time.monotonic() < fim:
            time.sleep(0.02)
        assert futuro.done(), "corpo recusado e o pedido segue esperando o prazo (6 s) em vez de falhar na hora"
        with pytest.raises(ErroDeCaptura):
            futuro.result(timeout=0)


async def test_contador_malformado_nao_derruba_a_batida(harness: Harness) -> None:
    """`ContadorAgregado` é sem validação de propósito ("um contador malformado não pode reprovar a BATIDA
    inteira") e `somar_metricas_do_agente` promete "medir nunca derruba a batida". Mas `v not in frozenset` com
    valor lista levanta `TypeError`; `_tratar_mensagem_do_worker` engole a exceção e o `last_seen_at` não anda.
    Três batidas assim e o `reap` marca o worker indisponível (e a admissão de boot fica suspensa por batida velha)."""
    s = harness.state
    assert s is not None
    reg = s.workers
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    link = reg.attach(WID, AgenteFalso().send)
    velho = "2000-01-01T00:00:00+00:00"
    s.db.execute("UPDATE workers SET last_seen_at=? WHERE id=?", (velho, WID))
    batida = Heartbeat(metricas=[ContadorAgregado(nome="capacidade.reserva", rotulos={"resultado": ["concedida"]},
                                                  valor=1)])
    await _tratar_mensagem_do_worker(s, WID, link, batida)
    assert s.db.scalar("SELECT last_seen_at FROM workers WHERE id=?", (WID,)) != velho, (
        "a batida com um contador malformado não foi registrada")


async def test_reserva_orfa_de_emulador_em_erro_nao_conta_duas_vezes(harness: Harness) -> None:
    """Boot que estourou o prazo: `_wait_boot` põe o aparelho em `error` e o emulador segue vivo (PID vivo) — a
    reserva fica órfã (`_soltar_reserva`). A guarda promete "reserva menos o RSS que o processo já tem, que a RAM
    livre já descontou. Uma conta só". Mas `_metrics_loop` só mede RSS de `online`/`booting` e ZERA `resources` dos
    outros: o órfão em `error`, que já ocupa a RAM que `free_mb` mostra, é descontado de novo pelo custo inteiro —
    enquanto o processo viver (reparo em ≥600 s). O lado do agente vence a órfã no prazo do boot; o central não.
    Efeito: boot recusado com RAM de sobra e a frase "reservados para boots em andamento" sem boot nenhum."""
    devs, um, dois, a = await _dois_parados(harness)
    est = a.est_ram_host_mb()
    _ram_para_um(harness, a)
    assert devs._recusa_por_capacidade(um, a) is None                        # noqa: SLF001 - admitido: reserva
    um.state, um.pid, um.attention = InstanceState.booting, 4242, None
    um.resources = InstanceResources(rss_mb=float(est), cpu_percent=1.0)       # medido enquanto bootava
    devs._soltar_reserva(um)                                                  # noqa: SLF001 - prazo com PID vivo
    assert um.id in devs._reservas_orfas                                      # noqa: SLF001
    um.state = InstanceState.error                                            # "Boot excedeu Ns"
    # O laço de métricas do próprio DeviceManager (já rodando no harness) passa uma vez.
    fim = time.monotonic() + 8
    while um.resources is not None and time.monotonic() < fim:
        await asyncio.sleep(0.1)
    assert um.resources is None, "o laço de métricas não passou"
    # O emulador órfão JÁ alocou o custo dele: a RAM livre que resta comporta exatamente mais um boot.
    harness.emulator.free_mb = float(est + a.min_free_ram_mb_after_boot + 10)
    assert devs._recusa_por_capacidade(dois, a) is None, (                    # noqa: SLF001
        f"recusado: {dois.state_detail!r} — a RAM do órfão foi contada duas vezes")


async def test_midia_sem_a_parte_pedida_nao_conta_como_captura(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """`desempacotar_midia` confere que cada parte é JPEG, mas ninguém confere que a parte PEDIDA veio. Um agente
    fora do contrato (versão diferente de `codificacao`, defeito) responde a um pedido de prévia só com `modelo`: o
    central publica `_publicar_imagem(..., b"", b"")` — frame não-sensível sem imagem —, conta `captura.total{ok}`
    e zera `capture_failures`. Falha virando sucesso. O mesmo vale para `observe` com `lado_max` sem `modelo`
    (Observation com `jpeg=None`, `image_omitted=None` e `image_at` preenchido)."""
    real = codificacao.codificar

    def so_modelo(png: bytes, *, previa: bool, cheia: bool, lado_max: int | None) -> codificacao.Codificado:
        return real(png, previa=False, cheia=False, lado_max=256)

    monkeypatch.setattr(observacao_do_agente, "codificacao",
                        types.SimpleNamespace(codificar=so_modelo, tamanho_png=codificacao.tamanho_png))
    r = await _remoto(tmp_path)
    try:
        devs = r.h.state.devices  # type: ignore[union-attr]
        devs.registrar_interesse("aba", ["android-03"], None, 20)
        r.rt.capture_failures = 2
        try:
            resultado = await devs._ciclo_de_previa(r.rt)                      # noqa: SLF001
        except DriverError:
            resultado = "falha"
        assert r.pedidos and r.pedidos[-1]["previa"] is True
        assert resultado != "capturada", "a prévia pedida chegou sem cheia/miniatura e contou como capturada"
    finally:
        await _parar(r)


async def test_evidencia_tardia_de_geracao_anterior_e_descartada(tmp_path: Path) -> None:
    """`_observar_imagem` levanta e `_publicar_imagem` descarta quando a geração muda durante a captura. A evidência
    tardia (`imagem_tardia`) não confere: o aparelho sai do ar no meio (`_nova_geracao`, que também apaga a
    classificação — e por isso `_previa_sensivel` passa a dizer "não sensível"), e a imagem do Android de antes é
    gravada como evidência da etapa com o horário de agora. Vale para a captura local também."""
    r = await _remoto(tmp_path)
    try:
        devs = r.h.state.devices  # type: ignore[union-attr]
        devs.arvore(r.rt, r.fake.page_source())                              # classificação fresca
        r.antes_de_entregar = lambda: devs._nova_geracao(r.rt)               # noqa: SLF001 - saiu do ar
        imagem = await devs.imagem_tardia(r.rt, timeout=10)
        assert imagem is None, "imagem de uma geração anterior virou evidência"
    finally:
        await _parar(r)


async def test_evidencia_tardia_local_de_geracao_anterior_e_descartada(harness: Harness) -> None:
    """O mesmo de #6 na captura LOCAL (ADB): o aparelho sai do ar enquanto o screencap corre."""
    s = harness.state
    assert s is not None
    devs = s.devices
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    devs.arvore(rt, fake.page_source())                                       # classificação fresca
    original = fake.screenshot_png

    def screencap_e_sai_do_ar() -> bytes:
        png = original()
        rt.geracao += 1                                                       # saiu do ar e voltou no meio
        return png

    fake.screenshot_png = screencap_e_sai_do_ar  # type: ignore[method-assign]
    assert await devs.imagem_tardia(rt, timeout=10) is None, "imagem de uma geração anterior virou evidência"


# ================================================================ cenários conferidos sem defeito
def test_portao_ainda_barra_o_quinto_handshake_nao_autenticado(harness: Harness) -> None:
    """O portão passou a contar HANDSHAKE e não sessão (e9bd11f). Abuso pelo túnel: quatro sockets de mídia abertos
    sem token seguram as quatro vagas do IP; o quinto — de mídia OU de comando — é fechado antes do `accept()`
    (4429). Liberados, o portão volta a zero."""
    s = harness.state
    assert s is not None
    with TestClient(create_worker_app(s), client=PAR_LOCAL) as cliente:
        abertos = []
        try:
            for _ in range(PENDENTES_POR_IP):
                ws = cliente.websocket_connect("/api/worker/midia", headers=HOST)
                abertos.append(ws.__enter__())
            assert s.workers.portao.pendentes == {PAR_LOCAL[0]: PENDENTES_POR_IP}
            for rota in ("/api/worker/midia", "/api/worker/ws"):
                with pytest.raises(WebSocketDisconnect) as saida:
                    with cliente.websocket_connect(rota, headers=HOST) as ws:
                        ws.receive_text()
                assert saida.value.code == 4429, rota
        finally:
            for ws in abertos:
                ws.send_text("{}")                                            # envio inválido: 4400
                with pytest.raises(WebSocketDisconnect):
                    ws.receive_text()
                ws.__exit__(None, None, None)
    assert s.workers.portao.pendentes == {}


def test_midia_pendente_nao_segura_a_batida_no_canal_de_comando(harness: Harness) -> None:
    """Canal de mídia autorizado e o corpo ainda não veio (mídia lenta): a batida pelo canal de comando é
    processada nesse meio-tempo, e depois o corpo resolve o pedido. `welcome` traz a feature negociada."""
    s = harness.state
    assert s is not None
    reg = s.workers
    with TestClient(create_worker_app(s), client=PAR_LOCAL) as cliente:
        with cliente.websocket_connect("/api/worker/ws", headers=HOST) as comando:
            comando.send_text(json.dumps({"hello": {**_hello_bruto(), "features": [FEATURE_OBSERVACAO_LOCAL]},
                                          "enrollment_token": reg.criar_inscricao()}))
            bem_vindo = comando.receive_json()
            assert bem_vindo["type"] == "welcome" and bem_vindo["accepted_features"] == [FEATURE_OBSERVACAO_LOCAL]
            futuro = cliente.portal.start_task_soon(functools.partial(
                reg.captura.capturar, WID, "android-03", "emulator-5554", timeout=20.0, lado_max=512))
            while (pedido := comando.receive_json())["type"] != "observe_image":
                pass
            with cliente.websocket_connect("/api/worker/midia", headers=HOST) as midia:
                midia.send_text(json.dumps({"request_id": pedido["request_id"],
                                            "upload_token": pedido["upload_token"]}))
                velho = "2000-01-01T00:00:00+00:00"
                s.db.execute("UPDATE workers SET last_seen_at=? WHERE id=?", (velho, WID))
                comando.send_text(json.dumps({"type": "heartbeat"}))
                fim = time.monotonic() + 5
                while s.db.scalar("SELECT last_seen_at FROM workers WHERE id=?", (WID,)) == velho:
                    assert time.monotonic() < fim, "a batida esperou a mídia pendente"
                    time.sleep(0.02)
                assert not futuro.done()
                midia.send_bytes(_corpo())
                assert midia.receive_json() == {"ok": True}
            assert futuro.result(timeout=5).modelo is not None


async def test_worker_que_reconecta_com_agente_antigo_volta_ao_adb(tmp_path: Path) -> None:
    """Agente novo × central novo, depois o MESMO worker reconecta com agente antigo (sem `features`): a captura
    seguinte vai pelo ADB do túnel e nenhuma mensagem nova (`observe_image`) sai para o canal novo."""
    r = await _remoto(tmp_path)
    try:
        s = r.h.state
        assert s is not None
        devs, reg = s.devices, s.workers
        await devs.observe(r.rt, timeout=10, imagem=lambda _t: True, lado_max=768)
        assert len(r.pedidos) == 1 and r.screencaps_pelo_tunel() == 0
        reg.anunciadas[WID] = frozenset()                                     # o `hello` do agente antigo
        antigo = AgenteFalso()
        reg.attach(WID, antigo.send)
        obs = await devs.observe(r.rt, timeout=10, imagem=lambda _t: True, lado_max=768)
        assert obs.source == "central_adb" and r.screencaps_pelo_tunel() == 1
        assert len(r.pedidos) == 1 and not [p for p in antigo.enviados if p.get("type") == "observe_image"]
    finally:
        await _parar(r)
