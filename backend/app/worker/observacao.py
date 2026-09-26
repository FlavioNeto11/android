"""Observação na origem, do lado do agente (feature `observe_local`, adendo v0.20).

O defeito que isto ataca, medido na leitura do código (relatório de desempenho §3): o screencap de um aparelho do
worker atravessava o túnel como PNG cheio, pelo ADB do CENTRAL — mesmo com `appium: local` —, e era decodificado e
codificado lá. Aqui o screencap é feito NESTA máquina, pelo ADB local, e codificado aqui mesmo pela MESMA regra do
central (`devices/codificacao.py`): só o JPEG já no tamanho pedido atravessa o túnel.

Três limites de propósito:

* **Só a imagem.** A hierarquia continua pelo Appium do aparelho (`rt.io.page_source` no central): `uiautomator
  dump` concorre com a sessão UiAutomator2 — um cliente UiAutomation por vez — e a derrubaria. Com `appium: local`
  a árvore já é produzida na origem, e o XML precisa chegar ao central para a classificação de tela sensível.
* **Mídia fora do canal de comando.** A imagem vai por uma conexão PRÓPRIA (`/api/worker/midia`), com token de
  uso único que o central emitiu no pedido: uma imagem de centenas de KB nunca fica na frente de uma batida, de um
  `ack` ou de um desfecho no WebSocket de comando. Pelo canal de comando só volta `ObserveResult` (falha, ou as
  dimensões de um pedido `so_dimensoes` — o caso da tela sensível, em que nenhum pixel sai desta máquina).
* **Só anuncia o que consegue.** Sem Pillow no venv do agente (instalação anterior a esta feature), a feature não
  é anunciada e o central segue pelo caminho de antes (ADB pelo túnel).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse, urlunparse

import websockets

from ..devices.adb import AdbError
from ..workers.protocol import (FEATURE_OBSERVACAO_LOCAL, EnvioDeMidia, ObserveImage, ObserveResult,
                                empacotar_midia)

try:
    from ..devices import codificacao
except ImportError:              # pragma: no cover - depende do venv do worker (Pillow ausente)
    codificacao = None           # type: ignore[assignment]

log = logging.getLogger("poc.worker")

#: O que este agente pode anunciar sobre observação. Vazio sem Pillow: anunciar e não conseguir codificar seria
#: prometer ao central um caminho que falha em toda captura.
FEATURES_DE_OBSERVACAO: tuple[str, ...] = (FEATURE_OBSERVACAO_LOCAL,) if codificacao is not None else ()

#: Teto da resposta do central no canal de mídia (`{"ok": ...}`): não há motivo para aceitar mais.
RESPOSTA_MAX_BYTES = 4096


class ErroDeObservacao(RuntimeError):
    pass


def midia_url(server: str) -> str:
    """`http://host:8000` → `ws://host:8000/api/worker/midia` (e `https` → `wss`), como `agent.ws_url`."""
    p = urlparse(server if "://" in server else f"http://{server}")
    esquema = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}.get(p.scheme, "ws")
    return urlunparse((esquema, p.netloc, "/api/worker/midia", "", "", ""))


class ObservacaoNaOrigem:
    """Atende `observe_image`: screencap local, codificação local, envio pelo canal de mídia.

    `adb_de` é `WorkerExecutor.adb_for` — o mesmo ADB desta máquina que os verbos usam; nada de boot, reserva ou
    guarda do executor é tocado aqui. `enviar` é o `_send` do canal de comando (para `ObserveResult`).
    """

    def __init__(self, settings: Any, *, adb_de: Callable[[Any], Any],
                 enviar: Callable[[dict[str, Any]], Awaitable[bool]]) -> None:
        self.settings = settings
        self.adb_de = adb_de
        self.enviar = enviar

    async def atender(self, msg: ObserveImage) -> None:
        """Nada escapa daqui: toda falha vira `ObserveResult(ok=False)` com o motivo, que o central trata como
        falha de captura — honesta, igual à de um screencap pelo túnel que não respondeu."""
        fim = time.monotonic() + msg.timeout_s
        try:
            await self._atender(msg, fim)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a resposta é o desfecho; exceção solta seria silêncio
            motivo = str(exc) or type(exc).__name__
            if not isinstance(exc, (ErroDeObservacao, AdbError, asyncio.TimeoutError)):
                log.exception("observação %s em %s", msg.request_id, msg.instance_id)
            await self.enviar(ObserveResult(request_id=msg.request_id, ok=False,
                                            error=f"captura na origem falhou: {motivo}"[:300]).model_dump())

    async def _atender(self, msg: ObserveImage, fim: float) -> None:
        if codificacao is None:
            raise ErroDeObservacao("este agente não tem Pillow para codificar a imagem")
        spec = self.settings.device(msg.instance_id)
        if spec is None:
            raise ErroDeObservacao(f"este worker não hospeda {msg.instance_id}")
        adb = self.adb_de(spec)
        t0 = time.perf_counter()
        png = await asyncio.wait_for(asyncio.to_thread(adb.screencap_png, timeout=max(1.0, fim - time.monotonic())),
                                     timeout=_restante(fim))
        captura_ms = (time.perf_counter() - t0) * 1000
        if msg.so_dimensoes:
            # Tela sensível: o central só precisa do tamanho. Só o cabeçalho do PNG é lido, e nenhum pixel sai.
            w, h = codificacao.tamanho_png(png)
            await self.enviar(ObserveResult(request_id=msg.request_id, ok=True, largura=w, altura=h).model_dump())
            return
        cod = await asyncio.wait_for(asyncio.to_thread(codificacao.codificar, png, previa=msg.previa,
                                                       cheia=msg.cheia, lado_max=msg.lado_max),
                                     timeout=_restante(fim))
        partes: dict[str, bytes] = {}
        if cod.cheia is not None:
            partes["cheia"] = cod.cheia
        if cod.miniatura is not None:
            partes["miniatura"] = cod.miniatura
        # Quando a do modelo coincide com a cheia, os bytes são os mesmos: vão uma vez só, com a marca.
        modelo_e_cheia = cod.modelo is not None and cod.modelo is cod.cheia
        if cod.modelo is not None and not modelo_e_cheia:
            partes["modelo"] = cod.modelo
        if not partes:
            raise ErroDeObservacao("o pedido não pediu imagem nenhuma")
        corpo = empacotar_midia(cod.largura, cod.altura, partes, modelo_e_cheia=modelo_e_cheia,
                                captura_ms=round(captura_ms, 1), ms={k: round(v, 1) for k, v in cod.ms.items()},
                                png_bytes=len(png))
        if len(corpo) > msg.max_bytes:
            raise ErroDeObservacao(f"a imagem tem {len(corpo)} bytes e o teto do pedido é {msg.max_bytes}")
        await asyncio.wait_for(self._enviar_midia(msg, corpo, fim), timeout=_restante(fim))

    async def _enviar_midia(self, msg: ObserveImage, corpo: bytes, fim: float) -> None:
        """Uma conexão por imagem: token na primeira mensagem, corpo na segunda, `{"ok": true}` de volta."""
        async with websockets.connect(midia_url(self.settings.server), max_size=RESPOSTA_MAX_BYTES,
                                      open_timeout=_restante(fim), ssl=self.settings.ssl_context()) as ws:
            await ws.send(EnvioDeMidia(request_id=msg.request_id, upload_token=msg.upload_token).model_dump_json())
            await ws.send(corpo)
            resposta = json.loads(await ws.recv())
        if not isinstance(resposta, dict) or resposta.get("ok") is not True:
            codigo = resposta.get("code") if isinstance(resposta, dict) else None
            raise ErroDeObservacao(f"o central recusou a imagem ({codigo or 'sem código'})")


def _restante(fim: float) -> float:
    restante = fim - time.monotonic()
    if restante <= 0:
        raise asyncio.TimeoutError("prazo da observação esgotado")
    return restante
