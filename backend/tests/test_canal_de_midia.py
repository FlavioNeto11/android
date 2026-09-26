"""Canal de mídia do worker e o pedido de captura na origem, do lado do central (`observe_local`; frente F4).

O central pede a imagem pelo canal de comando (`observe_image`, só a quem aceitou a feature) e ela chega por uma
conexão PRÓPRIA (`/api/worker/midia`) com token de uso único. Estes testes travam as duas pontas do lado de cá:
o pedido (quem recebe, o que acontece com falha, prazo, queda do canal) e a porta de mídia (o que ela recusa).

O app do worker roda no `TestClient` (portal próprio); o pedido é feito DENTRO do mesmo portal, como seria no
processo de verdade. O agente é o `AgenteFalso` de `test_workers.py`: só registra o que o central mandou.
"""
from __future__ import annotations

import functools
import json
import time
from typing import Any

import pytest
from starlette.testclient import TestClient

from app.api import _tratar_mensagem_do_worker
from app.devices import codificacao
from app.main import create_worker_app
from app.workers.captura import ErroDeCaptura, SemCapturaNaOrigem
from app.workers.protocol import (FEATURE_OBSERVACAO_LOCAL, FEATURE_RESERVA_DE_BOOT, ObserveResult,
                                  empacotar_midia)

from .conftest import Harness
from .test_canal_do_worker import PAR_LOCAL
from .test_observacao_na_origem import _png
from .test_workers import AgenteFalso, _hello

WID = "worker-lan-01"
HOST = {"host": "127.0.0.1:18000"}


def _preparar(harness: Harness, features: list[str]) -> tuple[Any, AgenteFalso]:
    reg = harness.state.workers  # type: ignore[union-attr]
    reg.autenticar(_hello(features=features), token=None, enrollment=reg.criar_inscricao())
    agente = AgenteFalso()
    reg.attach(WID, agente.send)
    return reg, agente


def _pedir(cliente: TestClient, reg: Any, **kw: Any) -> Any:
    """Dispara `capturar` no laço do app e devolve o futuro (concurrent) com o desfecho."""
    kw.setdefault("timeout", 5.0)
    return cliente.portal.start_task_soon(functools.partial(reg.captura.capturar, WID, "android-03",
                                                            "emulator-5554", **kw))


def _pedido_enviado(agente: AgenteFalso, n: int = 1) -> dict[str, Any]:
    fim = time.monotonic() + 5
    while sum(1 for p in agente.enviados if p.get("type") == "observe_image") < n:
        assert time.monotonic() < fim, "o pedido de imagem não saiu pelo canal de comando"
        time.sleep(0.01)
    return [p for p in agente.enviados if p.get("type") == "observe_image"][n - 1]


def _enviar(cliente: TestClient, request_id: str, token: str, corpo: bytes | str) -> dict[str, Any]:
    with cliente.websocket_connect("/api/worker/midia", headers=HOST) as ws:
        ws.send_text(json.dumps({"request_id": request_id, "upload_token": token}))
        if isinstance(corpo, bytes):
            ws.send_bytes(corpo)
        else:
            ws.send_text(corpo)
        return ws.receive_json()


def _corpo(lado_max: int = 512) -> bytes:
    cod = codificacao.codificar(_png(), previa=False, cheia=False, lado_max=lado_max)
    return empacotar_midia(cod.largura, cod.altura, {"modelo": cod.modelo}, captura_ms=12.5)


# ---------------------------------------------------------------- o caminho feliz
def test_imagem_pedida_chega_pelo_canal_de_midia_e_resolve_o_pedido(harness: Harness) -> None:
    reg, agente = _preparar(harness, [FEATURE_OBSERVACAO_LOCAL])
    with TestClient(create_worker_app(harness.state), client=PAR_LOCAL) as cliente:
        futuro = _pedir(cliente, reg, lado_max=512)
        pedido = _pedido_enviado(agente)
        assert (pedido["instance_id"], pedido["lado_max"], pedido["previa"], pedido["cheia"]) == (
            "android-03", 512, False, False)
        assert _enviar(cliente, pedido["request_id"], pedido["upload_token"], _corpo()) == {"ok": True}
        midia = futuro.result(timeout=5)
    assert (midia.largura, midia.altura) == (540, 1200)
    assert midia.modelo is not None and midia.cheia is None and midia.captura_ms == 12.5
    assert reg.captura.pedidos == {}, "pedido resolvido tem de sair da lista"
    # O token é de uso único: nem o próprio agente consegue mandar de novo.
    assert pedido["upload_token"] not in json.dumps(list(reg.captura.pedidos))


def test_tela_sensivel_so_dimensoes_resolve_pelo_canal_de_comando(harness: Harness) -> None:
    reg, agente = _preparar(harness, [FEATURE_OBSERVACAO_LOCAL])
    with TestClient(create_worker_app(harness.state), client=PAR_LOCAL) as cliente:
        futuro = _pedir(cliente, reg, so_dimensoes=True)
        pedido = _pedido_enviado(agente)
        assert pedido["so_dimensoes"] is True
        link = reg.live[WID]
        cliente.portal.call(_tratar_mensagem_do_worker, harness.state, WID, link,
                            ObserveResult(request_id=pedido["request_id"], ok=True, largura=1080, altura=2400))
        midia = futuro.result(timeout=5)
        # E nenhuma imagem é aceita para esse pedido, nem com o token certo.
        assert (midia.largura, midia.altura, midia.modelo, midia.cheia) == (1080, 2400, None, None)


# ---------------------------------------------------------------- C7: só para quem aceitou
@pytest.mark.parametrize("features", [[], [FEATURE_RESERVA_DE_BOOT]])
def test_worker_sem_observe_local_nao_recebe_pedido(harness: Harness, features: list[str]) -> None:
    reg, agente = _preparar(harness, features)
    with TestClient(create_worker_app(harness.state), client=PAR_LOCAL) as cliente:
        with pytest.raises(SemCapturaNaOrigem):
            _pedir(cliente, reg).result(timeout=5)
    assert not [p for p in agente.enviados if p.get("type") == "observe_image"], "mensagem nova para quem não aceitou"
    assert not reg.captura.disponivel(WID)


# ---------------------------------------------------------------- falhas honestas
def test_falha_do_agente_chega_como_erro_de_captura(harness: Harness) -> None:
    reg, agente = _preparar(harness, [FEATURE_OBSERVACAO_LOCAL])
    with TestClient(create_worker_app(harness.state), client=PAR_LOCAL) as cliente:
        futuro = _pedir(cliente, reg, cheia=True)
        pedido = _pedido_enviado(agente)
        cliente.portal.call(_tratar_mensagem_do_worker, harness.state, WID, reg.live[WID],
                            ObserveResult(request_id=pedido["request_id"], ok=False, error="screencap falhou"))
        with pytest.raises(ErroDeCaptura, match="screencap falhou"):
            futuro.result(timeout=5)


def test_prazo_sem_imagem_e_erro_de_captura(harness: Harness) -> None:
    reg, agente = _preparar(harness, [FEATURE_OBSERVACAO_LOCAL])
    with TestClient(create_worker_app(harness.state), client=PAR_LOCAL) as cliente:
        futuro = _pedir(cliente, reg, cheia=True, timeout=0.2)
        _pedido_enviado(agente)
        with pytest.raises(ErroDeCaptura, match="não entregou"):
            futuro.result(timeout=5)
    assert reg.captura.pedidos == {}


def test_canal_que_cai_leva_junto_o_pedido(harness: Harness) -> None:
    reg, agente = _preparar(harness, [FEATURE_OBSERVACAO_LOCAL])
    with TestClient(create_worker_app(harness.state), client=PAR_LOCAL) as cliente:
        futuro = _pedir(cliente, reg, cheia=True, timeout=30)
        pedido = _pedido_enviado(agente)
        cliente.portal.call(functools.partial(_detach, reg))
        with pytest.raises(ErroDeCaptura, match="canal do worker caiu"):
            futuro.result(timeout=5)                          # na hora, não nos 30 s do prazo
        # E o envio que chegar depois não tem a quem entregar.
        assert _enviar(cliente, pedido["request_id"], pedido["upload_token"], _corpo())["ok"] is False


async def _detach(reg: Any) -> None:
    reg.detach(WID, "conexão encerrada", reg.live[WID])


# ---------------------------------------------------------------- a porta de mídia
def test_porta_de_midia_recusa_token_errado_reuso_e_corpo_invalido(harness: Harness) -> None:
    reg, agente = _preparar(harness, [FEATURE_OBSERVACAO_LOCAL])
    with TestClient(create_worker_app(harness.state), client=PAR_LOCAL) as cliente:
        assert _enviar(cliente, "pedido-que-nao-existe", "x" * 32, _corpo())["code"] == "unknown_request"

        futuro = _pedir(cliente, reg, lado_max=512)
        pedido = _pedido_enviado(agente)
        assert _enviar(cliente, pedido["request_id"], "y" * 43, _corpo())["code"] == "bad_token"
        assert not futuro.done(), "token errado não pode encerrar o pedido de quem tem o certo"
        # Corpo que não é imagem: recusado, e o pedido falha NA HORA (quem pediu não espera o prazo).
        nao_jpeg = empacotar_midia(540, 1200, {"modelo": b"\x89PNG disfarcado de JPEG"})
        assert _enviar(cliente, pedido["request_id"], pedido["upload_token"], nao_jpeg)["code"] == "bad_media"
        with pytest.raises(ErroDeCaptura, match="imagem inválida"):
            futuro.result(timeout=5)
        # Uso único: o token já foi gasto, mesmo que o corpo tenha sido recusado.
        assert _enviar(cliente, pedido["request_id"], pedido["upload_token"], _corpo())["code"] in (
            "already_used", "unknown_request")

        futuro = _pedir(cliente, reg, lado_max=512, max_bytes=2048)
        pedido = _pedido_enviado(agente, 2)
        assert _enviar(cliente, pedido["request_id"], pedido["upload_token"], _corpo())["code"] == "too_large"
        with pytest.raises(ErroDeCaptura):
            futuro.result(timeout=5)

        futuro = _pedir(cliente, reg, lado_max=512)
        pedido = _pedido_enviado(agente, 3)
        assert _enviar(cliente, pedido["request_id"], pedido["upload_token"], "texto no lugar da imagem")["code"] \
            == "bad_media"
        with pytest.raises(ErroDeCaptura):
            futuro.result(timeout=5)


def test_porta_de_midia_confere_host_e_primeira_mensagem(harness: Harness) -> None:
    from starlette.websockets import WebSocketDisconnect

    with TestClient(create_worker_app(harness.state), client=PAR_LOCAL) as cliente:
        with pytest.raises(WebSocketDisconnect) as saida:
            with cliente.websocket_connect("/api/worker/midia", headers={"host": "evil.example:18000"}) as ws:
                ws.receive_text()
        assert saida.value.code == 4403
        with pytest.raises(WebSocketDisconnect) as saida:
            with cliente.websocket_connect("/api/worker/midia", headers=HOST) as ws:
                ws.send_text("x" * 5000)
                ws.receive_text()
        assert saida.value.code == 4400
    assert harness.state.workers.portao.pendentes == {}, "o portão tem de ser liberado em toda saída"  # type: ignore[union-attr]


# ---------------------------------------------------------------- o portão com o canal de comando aberto
def test_canal_de_comando_aberto_nao_ocupa_vaga_do_portao_para_a_midia(harness: Harness) -> None:
    """O portão conta HANDSHAKE, não sessão. Segurando a vaga a sessão inteira, o socket de comando deixava três
    vagas para o canal de mídia do mesmo IP — e pelo túnel todo worker é 127.0.0.1: o quarto aparelho observando
    ao mesmo tempo levava 4429 sem nada ter falhado."""
    from app.workers.portao import PENDENTES_POR_IP

    from .test_canal_do_worker import _hello as _hello_bruto

    s = harness.state
    assert s is not None
    with TestClient(create_worker_app(s), client=PAR_LOCAL) as cliente:
        with cliente.websocket_connect("/api/worker/ws", headers=HOST) as comando:
            comando.send_text(json.dumps({"hello": _hello_bruto(), "enrollment_token": s.workers.criar_inscricao()}))
            assert comando.receive_json()["type"] == "welcome"
            assert s.workers.portao.pendentes == {}, "sessão autenticada não pode ocupar vaga de handshake"
            abertos = []
            try:
                for _ in range(PENDENTES_POR_IP):        # handshakes de mídia simultâneos, ainda sem token
                    ws = cliente.websocket_connect("/api/worker/midia", headers=HOST)
                    abertos.append(ws.__enter__())
                assert s.workers.portao.pendentes == {PAR_LOCAL[0]: PENDENTES_POR_IP}
            finally:
                for ws in abertos:
                    ws.send_text(json.dumps({"request_id": "pedido-inexistente", "upload_token": "z" * 32}))
                    assert ws.receive_json()["code"] == "unknown_request"
                    ws.__exit__(None, None, None)
    assert s.workers.portao.pendentes == {}


def test_prazo_acima_do_teto_do_contrato_e_limitado_e_nao_quebra_o_pedido(harness: Harness) -> None:
    from app.workers.protocol import PRAZO_MAX_OBSERVACAO_S

    reg, agente = _preparar(harness, [FEATURE_OBSERVACAO_LOCAL])
    with TestClient(create_worker_app(harness.state), client=PAR_LOCAL) as cliente:
        futuro = _pedir(cliente, reg, cheia=True, timeout=PRAZO_MAX_OBSERVACAO_S * 5)
        pedido = _pedido_enviado(agente)
        assert pedido["timeout_s"] == PRAZO_MAX_OBSERVACAO_S
        cliente.portal.call(_tratar_mensagem_do_worker, harness.state, WID, reg.live[WID],
                            ObserveResult(request_id=pedido["request_id"], ok=False, error="teste"))
        with pytest.raises(ErroDeCaptura):
            futuro.result(timeout=5)


def test_envio_autorizado_que_fecha_sem_corpo_falha_o_pedido_na_hora(harness: Harness) -> None:
    reg, agente = _preparar(harness, [FEATURE_OBSERVACAO_LOCAL])
    with TestClient(create_worker_app(harness.state), client=PAR_LOCAL) as cliente:
        futuro = _pedir(cliente, reg, cheia=True, timeout=30)
        pedido = _pedido_enviado(agente)
        with cliente.websocket_connect("/api/worker/midia", headers=HOST) as ws:
            ws.send_text(json.dumps({"request_id": pedido["request_id"], "upload_token": pedido["upload_token"]}))
        with pytest.raises(ErroDeCaptura, match="antes de mandar a imagem"):
            futuro.result(timeout=5)                         # na hora, não nos 30 s do prazo
