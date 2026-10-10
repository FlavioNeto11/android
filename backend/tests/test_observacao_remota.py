"""A observação do aparelho de um worker com `observe_local`: a imagem vem da ORIGEM, pelas mesmas guardas.

O `DeviceManager` pede a imagem ao agente (`_capturar_na_origem`) quando o canal vivo do worker negociou a feature,
dentro do executor do aparelho; sem a feature, o screencap segue pelo ADB do túnel, como sempre. A imagem que vem
da origem passa pelas MESMAS guardas da local: trecho sensível, geração do runtime e classificação no instante de
usar ou publicar (revisão F8) — nunca por um atalho.

O agente aqui é o código de verdade (`worker/observacao.ObservacaoNaOrigem`) com o ADB falso e o envio de mídia
entregue direto ao `CapturaNaOrigem` do central (a porta de mídia tem os testes dela em `test_canal_de_midia.py`).
O aparelho é o falso do harness, que conta as chamadas: `"screenshot"` nele é o ADB do túnel sendo usado.
"""
from __future__ import annotations

import asyncio
import types
from pathlib import Path
from typing import Any, Callable

import pytest

from app.api import _tratar_mensagem_do_worker
from app.automation.driver import DriverError
from app.devices import codificacao
from app.devices.adb import AdbError
from app.metricas import metricas
from app.worker.observacao import ObservacaoNaOrigem
from app.workers.protocol import FEATURE_OBSERVACAO_LOCAL, EnvioDeMidia, ObserveImage, parse_upstream

from .conftest import Harness
from .test_contrato_de_worker import VERBOS, AgenteDeContrato
from .test_observacao_arvore_primeiro import _sem_laco
from .test_observacao_na_origem import AdbFalso, _png
from .test_workers import _hello

WID = "worker-lan-01"


class Remoto:
    def __init__(self, h: Harness, rt: Any, fake: Any, png: bytes) -> None:
        self.h, self.rt, self.fake, self.png = h, rt, fake, png
        self.pedidos: list[dict[str, Any]] = []
        #: Chamado no agente logo antes de a imagem ser entregue — é o "enquanto a captura corria".
        self.antes_de_entregar: Callable[[], None] | None = None

    def screencaps_pelo_tunel(self) -> int:
        return self.fake.calls.count("screenshot")


async def _remoto(tmp_path: Path, *, features: tuple[str, ...] = (FEATURE_OBSERVACAO_LOCAL,),
                  erro: Exception | None = None) -> Remoto:
    h = Harness(tmp_path, 3, external={"android-03": "127.0.0.1:15555"})
    await h.boot()
    s = h.state
    assert s is not None
    reg = s.workers
    reg.autenticar(_hello(verbs=VERBOS, features=list(features)), token=None, enrollment=reg.criar_inscricao())
    contrato = AgenteDeContrato(h, WID)
    png = _png(720, 1280)
    r = Remoto(h, s.devices.get("android-03"), h.fakes["android-03"], png)

    async def subir(payload: dict[str, Any]) -> bool:
        await _tratar_mensagem_do_worker(s, WID, contrato.link, parse_upstream(payload))
        return True

    origem = ObservacaoNaOrigem(types.SimpleNamespace(device=lambda _iid: object()),
                                adb_de=lambda _spec: AdbFalso(png, erro=erro), enviar=subir)

    async def entregar(msg: ObserveImage, corpo: bytes, _fim: float) -> None:
        if r.antes_de_entregar is not None:
            r.antes_de_entregar()
        pedido = reg.captura.autorizar(EnvioDeMidia(request_id=msg.request_id, upload_token=msg.upload_token))
        reg.captura.receber(pedido, corpo)

    origem._enviar_midia = entregar  # type: ignore[method-assign]

    async def send(payload: dict[str, Any]) -> None:
        if payload.get("type") == "observe_image":
            r.pedidos.append(payload)
            asyncio.get_running_loop().create_task(origem.atender(ObserveImage.model_validate(payload)))
            return
        await contrato.send(payload)

    contrato.link = reg.attach(WID, send)
    s.db.execute("UPDATE instances SET worker_id=? WHERE id=?", (WID, "android-03"))
    r.rt.worker_id = WID
    s.devices.bind_worker(WID, list(VERBOS))
    s.devices.bind_worker_captura(WID, reg.captura)       # o que `_worker_canal` faz na conexão
    await _sem_laco(r.rt)
    r.fake.screen = "home"
    metricas.limpar()
    return r


async def _parar(r: Remoto) -> None:
    await r.h.state.stop()  # type: ignore[union-attr]


# ---------------------------------------------------------------- o caminho
async def test_imagem_da_observacao_vem_da_origem_e_nao_do_adb_do_tunel(tmp_path: Path) -> None:
    r = await _remoto(tmp_path)
    try:
        obs = await r.h.state.devices.observe(r.rt, timeout=10, imagem=lambda _t: True, lado_max=768)  # type: ignore[union-attr]
        assert r.screencaps_pelo_tunel() == 0, "com observe_local o PNG cheio não atravessa o túnel"
        assert r.fake.calls.count("page_source") == 1, "a hierarquia continua pelo Appium (um cliente por vez)"
        assert [(p["lado_max"], p["cheia"], p["so_dimensoes"]) for p in r.pedidos] == [(768, False, False)]
        assert obs.jpeg == codificacao.codificar(r.png, previa=False, cheia=False, lado_max=768).modelo
        assert (obs.width, obs.height) == (720, 1280), "coordenada em pixels do aparelho"
        assert obs.source == "worker_local" and obs.image_omitted is None
        assert metricas.valor("captura.total", origem="observacao", resultado="ok") == 1
    finally:
        await _parar(r)


async def test_worker_sem_a_feature_segue_pelo_adb_como_sempre(tmp_path: Path) -> None:
    r = await _remoto(tmp_path, features=())
    try:
        obs = await r.h.state.devices.observe(r.rt, timeout=10, imagem=lambda _t: True, lado_max=768)  # type: ignore[union-attr]
        assert r.pedidos == [], "mensagem nova para quem não aceitou a feature"
        assert r.screencaps_pelo_tunel() == 1 and obs.jpeg and obs.source == "central_adb"
    finally:
        await _parar(r)


# ---------------------------------------------------------------- as mesmas guardas
async def test_geracao_que_muda_durante_a_captura_nao_entrega_imagem(tmp_path: Path) -> None:
    r = await _remoto(tmp_path)
    try:
        def saiu_e_voltou() -> None:
            r.rt.geracao += 1

        r.antes_de_entregar = saiu_e_voltou
        with pytest.raises(DriverError, match="saiu do ar e voltou"):
            await r.h.state.devices.observe(r.rt, timeout=10, imagem=lambda _t: True, lado_max=768)  # type: ignore[union-attr]
    finally:
        await _parar(r)


async def test_falha_na_origem_e_falha_de_captura_sem_volta_ao_adb(tmp_path: Path) -> None:
    r = await _remoto(tmp_path, erro=AdbError("screencap falhou em emulator-5554"))
    try:
        with pytest.raises(DriverError, match="captura na origem falhou"):
            await r.h.state.devices.observe(r.rt, timeout=10, imagem=lambda _t: True, lado_max=768)  # type: ignore[union-attr]
        assert r.screencaps_pelo_tunel() == 0, "falha na origem não pode virar um segundo screencap escondido"
    finally:
        await _parar(r)


# ---------------------------------------------------------------- prévia e evidência
async def test_previa_do_aparelho_remoto_vem_da_origem_pela_mesma_porta(tmp_path: Path) -> None:
    r = await _remoto(tmp_path)
    try:
        devs = r.h.state.devices  # type: ignore[union-attr]
        devs.registrar_interesse("aba", ["android-03"], None, 20)
        assert await devs._ciclo_de_previa(r.rt) == "capturada"
        esperado = codificacao.codificar(r.png, previa=True, cheia=False, lado_max=None)
        assert r.rt.frame is not None and r.rt.frame.jpeg_full == esperado.cheia
        assert r.rt.frame.jpeg_thumb == esperado.miniatura and r.screencaps_pelo_tunel() == 0
        assert r.pedidos[-1]["previa"] is True

        r.fake.screen = "login"                               # tela de senha: a origem manda os pixels como em qualquer outra
        devs.arvore(r.rt, r.fake.page_source())
        assert await devs._ciclo_de_previa(r.rt) == "capturada"
        assert r.rt.frame.jpeg_full and r.pedidos[-1]["so_dimensoes"] is False
    finally:
        await _parar(r)


async def test_evidencia_tardia_vem_da_origem(tmp_path: Path) -> None:
    r = await _remoto(tmp_path)
    try:
        devs = r.h.state.devices  # type: ignore[union-attr]
        devs.arvore(r.rt, r.fake.page_source())               # classificação fresca: não precisa reler
        imagem = await devs.imagem_tardia(r.rt, timeout=10)
        assert imagem is not None
        assert imagem[0] == codificacao.codificar(r.png, previa=False, cheia=True, lado_max=None).cheia
        assert [p["cheia"] for p in r.pedidos] == [True] and r.screencaps_pelo_tunel() == 0
    finally:
        await _parar(r)
