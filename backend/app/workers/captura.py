"""Captura na origem, do lado do central (feature `observe_local`, adendo v0.20).

Quem pede é o central; quem captura e codifica é o agente, perto do aparelho (`worker/observacao.py`); a imagem
volta por um canal PRÓPRIO (`/api/worker/midia`), nunca pelo WebSocket de comando. Este módulo guarda o que liga
as três pontas: o pedido em voo, o token de uso único do envio e o futuro que quem pediu está esperando.

O token é o que autentica o envio: 32 bytes aleatórios por pedido, guardados só como hash, comparados com
`compare_digest`, válidos uma vez e até o prazo do pedido, e amarrados ao worker E ao canal que o pediu — um
envio que chega depois de o worker reconectar (canal novo) é recusado, porque a imagem foi pedida a outro canal.

Nada aqui sobe para o painel nem vai ao banco: é transporte. Quem decide o que fazer com a imagem (tela sensível,
prévia, modelo) continua sendo o `DeviceManager`, pelo mesmo executor do aparelho.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .protocol import (FEATURE_OBSERVACAO_LOCAL, MIDIA_MAX_BYTES, PRAZO_MAX_OBSERVACAO_S, EnvioDeMidia,
                       ObserveImage, ObserveResult, desempacotar_midia)

if TYPE_CHECKING:
    from .registry import WorkerLink, WorkerRegistry

log = logging.getLogger("poc.workers")



class SemCapturaNaOrigem(RuntimeError):
    """O worker não está conectado ou não negociou `observe_local`: quem pediu segue pelo caminho de antes."""


class ErroDeCaptura(RuntimeError):
    """A captura na origem foi tentada e não deu imagem (falha no agente, prazo, canal caído, corpo inválido)."""


class ErroDeMidia(RuntimeError):
    """Envio recusado no canal de mídia. `code` vai para o agente; `close` é o código de fechamento do socket."""

    def __init__(self, code: str, close: int, message: str) -> None:
        super().__init__(message)
        self.code, self.close = code, close


@dataclass(slots=True)
class MidiaDaOrigem:
    """O que o agente mandou, já conferido. `largura`/`altura` são as da TELA (é com elas que a coordenada do
    modelo volta ao aparelho); `modelo` é a imagem no `lado_max` pedido."""

    largura: int
    altura: int
    cheia: bytes | None = None
    miniatura: bytes | None = None
    modelo: bytes | None = None
    captura_ms: float | None = None
    ms: dict[str, float] = field(default_factory=dict)
    bytes_recebidos: int = 0


@dataclass(slots=True)
class _Pedido:
    worker_id: str
    link: Any
    token_hash: str
    futuro: asyncio.Future[MidiaDaOrigem]
    fim: float
    max_bytes: int
    so_dimensoes: bool
    usado: bool = False


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class CapturaNaOrigem:
    def __init__(self, registry: "WorkerRegistry") -> None:
        self.registry = registry
        self.pedidos: dict[str, _Pedido] = {}

    def disponivel(self, worker_id: str | None) -> bool:
        """O canal VIVO deste worker negociou `observe_local`? É a pergunta de quem escolhe o caminho."""
        return bool(worker_id) and self.registry.aceitou(worker_id or "", FEATURE_OBSERVACAO_LOCAL)

    async def capturar(self, worker_id: str, instance_id: str, serial: str, *, timeout: float,
                       previa: bool = False, cheia: bool = False, lado_max: int | None = None,
                       so_dimensoes: bool = False, max_bytes: int = MIDIA_MAX_BYTES) -> MidiaDaOrigem:
        """Pede a imagem ao agente e espera ela chegar pelo canal de mídia (ou a falha, pelo de comando).

        `SemCapturaNaOrigem` ANTES de mandar qualquer coisa quando o worker não negociou a feature — a mensagem de
        tipo novo só vai para quem a aceitou (C7). Depois de mandada, toda falha é `ErroDeCaptura`, inclusive o
        prazo: não há volta silenciosa para o ADB do túnel no meio de um pedido, que dobraria a espera e
        esconderia um agente quebrado."""
        link: WorkerLink | None = self.registry.live.get(worker_id)
        if link is None or FEATURE_OBSERVACAO_LOCAL not in link.features_aceitas:
            raise SemCapturaNaOrigem(f"worker '{worker_id}' não negociou {FEATURE_OBSERVACAO_LOCAL} nesta conexão")
        # O contrato limita o prazo do pedido (`ObserveImage.timeout_s`); acima dele o modelo recusaria com
        # `ValidationError`, que não é nenhum dos dois desfechos que quem chama sabe tratar.
        timeout = min(float(timeout), PRAZO_MAX_OBSERVACAO_S)
        laco = asyncio.get_running_loop()
        request_id, token = secrets.token_urlsafe(12), secrets.token_urlsafe(32)
        futuro: asyncio.Future[MidiaDaOrigem] = laco.create_future()
        self.pedidos[request_id] = _Pedido(worker_id, link, _hash(token), futuro, laco.time() + timeout, max_bytes,
                                           so_dimensoes)
        link.capturas[request_id] = futuro
        try:
            try:
                await link.send(ObserveImage(request_id=request_id, instance_id=instance_id, serial=serial,
                                             previa=previa, cheia=cheia, lado_max=lado_max,
                                             so_dimensoes=so_dimensoes, upload_token=token, timeout_s=timeout,
                                             max_bytes=max_bytes).model_dump())
            except Exception as exc:  # noqa: BLE001 - canal caindo no envio: a captura falhou, e é isso que se diz
                raise ErroDeCaptura(f"não foi possível pedir a imagem ao worker: {exc}") from exc
            try:
                return await asyncio.wait_for(asyncio.shield(futuro), timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise ErroDeCaptura(f"o worker não entregou a imagem em {timeout:.0f} s") from exc
        finally:
            self.pedidos.pop(request_id, None)
            link.capturas.pop(request_id, None)
            if not futuro.done():
                futuro.cancel()

    # ------------------------------------------------------------------ o que volta
    def on_resultado(self, worker_id: str, msg: ObserveResult) -> bool:
        """`ObserveResult` pelo canal de comando: a falha, ou as dimensões de um pedido `so_dimensoes`. Sucesso de
        pedido com imagem não vem por aqui — só o envio no canal de mídia resolve esse."""
        p = self.pedidos.get(msg.request_id)
        if p is None or p.worker_id != worker_id or p.futuro.done():
            return False
        if not msg.ok:
            p.futuro.set_exception(ErroDeCaptura(msg.error or "o worker não conseguiu capturar a tela"))
            return True
        if p.so_dimensoes and msg.largura and msg.altura:
            p.futuro.set_result(MidiaDaOrigem(largura=msg.largura, altura=msg.altura))
            return True
        return False

    def autorizar(self, envio: EnvioDeMidia) -> _Pedido:
        """Confere o token do envio. Uso único: autorizado, o pedido não aceita outro envio."""
        p = self.pedidos.get(envio.request_id)
        if p is None:
            raise ErroDeMidia("unknown_request", 4404, "pedido de imagem desconhecido ou já encerrado")
        if not secrets.compare_digest(p.token_hash, _hash(envio.upload_token)):
            raise ErroDeMidia("bad_token", 4401, "token de envio inválido")
        if p.usado:
            raise ErroDeMidia("already_used", 4409, "este pedido já recebeu um envio")
        if asyncio.get_running_loop().time() > p.fim:
            raise ErroDeMidia("expired", 4410, "o prazo do pedido acabou")
        if p.so_dimensoes:
            raise ErroDeMidia("no_media_expected", 4400, "este pedido não aceita imagem")
        if self.registry.live.get(p.worker_id) is not p.link:
            raise ErroDeMidia("stale_link", 4409, "o worker reconectou depois do pedido")
        p.usado = True
        return p

    def abandonar(self, p: _Pedido, motivo: str) -> None:
        """O envio autorizado não trouxe o corpo (conexão fechada): o pedido falha agora, não no prazo."""
        if not p.futuro.done():
            p.futuro.set_exception(ErroDeCaptura(motivo))

    def receber(self, p: _Pedido, corpo: bytes) -> MidiaDaOrigem:
        """O corpo do envio, conferido. Corpo inválido FALHA o pedido na hora — quem pediu não espera o prazo."""
        try:
            if len(corpo) > p.max_bytes:
                raise ErroDeMidia("too_large", 4413, f"a imagem tem {len(corpo)} bytes; o teto é {p.max_bytes}")
            try:
                cab, partes = desempacotar_midia(corpo)
            except ValueError as exc:
                raise ErroDeMidia("bad_media", 4400, f"imagem inválida: {exc}") from exc
        except ErroDeMidia as exc:
            if not p.futuro.done():
                p.futuro.set_exception(ErroDeCaptura(str(exc)))
            raise
        modelo = partes.get("modelo") or (partes.get("cheia") if cab.get("modelo_e_cheia") is True else None)
        ms = cab.get("ms") if isinstance(cab.get("ms"), dict) else {}
        midia = MidiaDaOrigem(largura=cab["largura"], altura=cab["altura"], cheia=partes.get("cheia"),
                              miniatura=partes.get("miniatura"), modelo=modelo,
                              captura_ms=_numero(cab.get("captura_ms")),
                              ms={str(k): v for k, v in ms.items() if isinstance(v, (int, float))},
                              bytes_recebidos=len(corpo))
        if not p.futuro.done():
            p.futuro.set_result(midia)
        return midia


def _numero(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
