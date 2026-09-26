"""Observação na origem, do lado do agente e do contrato (feature `observe_local`; frente F4, fase B).

O screencap de um aparelho do worker atravessava o túnel como PNG cheio pelo ADB do central, e era codificado lá.
Com `observe_local` o agente captura e codifica NA MÁQUINA DELE, pela mesma regra do central
(`devices/codificacao.py`), e manda só o JPEG reduzido por um canal de mídia SEPARADO do de comando.

Aqui o central é falso (o `CentralFalso` de `test_worker_agent.py`, com um caminho de mídia acrescentado) e o ADB
é falso; o WebSocket é de verdade. Nada toca emulador, túnel ou worker real.
"""
from __future__ import annotations

import asyncio
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from app.devices import codificacao
from app.devices.adb import AdbError
from app.worker import agent as agent_mod
from app.worker.observacao import midia_url
from app.workers.protocol import (FEATURE_OBSERVACAO_LOCAL, FEATURE_RESERVA_DE_BOOT, EnvioDeMidia, ObserveImage,
                                  desempacotar_midia, empacotar_midia, parse_upstream)

from .test_worker_agent import CentralFalso, _conectar, _encerrar, _esperar, _nada

BACKEND = Path(__file__).resolve().parents[1]


def _png(largura: int = 540, altura: int = 1200) -> bytes:
    """Tela sintética com ruído (semente fixa): PNG de imagem lisa comprime melhor que JPEG e não representaria
    um screencap de verdade, que tem texto, foto e degradê."""
    import random

    img = Image.frombytes("RGB", (largura, altura), random.Random(7).randbytes(largura * altura * 3))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class CentralComMidia(CentralFalso):
    """O central falso de sempre no canal de comando, e o canal de mídia em `/api/worker/midia`."""

    def __init__(self, *, recusar_midia: bool = False, **kw: Any) -> None:
        super().__init__(**kw)
        self.recusar_midia = recusar_midia
        self.envios: list[tuple[dict[str, Any], bytes]] = []

    async def _atender(self, ws: Any) -> None:
        if ws.request.path != "/api/worker/midia":
            await super()._atender(ws)
            return
        cabecalho = json.loads(await ws.recv())
        EnvioDeMidia.model_validate(cabecalho)
        corpo = await ws.recv()
        assert isinstance(corpo, bytes), "a imagem tem de ir como binário, não como texto"
        self.envios.append((cabecalho, corpo))
        await ws.send(json.dumps({"ok": False, "code": "unknown_request"} if self.recusar_midia else {"ok": True}))


class AdbFalso:
    def __init__(self, png: bytes | None = None, erro: Exception | None = None) -> None:
        self.png, self.erro, self.chamadas = png or _png(), erro, 0

    def screencap_png(self, *, timeout: float = 20) -> bytes:
        del timeout
        self.chamadas += 1
        if self.erro is not None:
            raise self.erro
        return self.png


def _pedido(**kw: Any) -> dict[str, Any]:
    base = dict(request_id="pedido-0001", instance_id="android-03", serial="emulator-5554",
                upload_token="token-de-uso-unico-de-teste-000", timeout_s=5.0)
    return ObserveImage(**{**base, **kw}).model_dump()


async def _com_agente(central: CentralComMidia, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                      adb: AdbFalso) -> tuple[Any, asyncio.Task[None]]:
    agente, tarefa = await _conectar(central, tmp_path, monkeypatch, _nada)
    monkeypatch.setattr(agente.observacao, "adb_de", lambda _spec: adb)
    return agente, tarefa


# ---------------------------------------------------------------- a mesma codificação nas duas pontas
@pytest.mark.parametrize("previa,cheia,lado_max", [(True, False, None), (False, True, None), (False, False, 512),
                                                   (True, False, 512), (False, True, 4096)])
def test_origem_e_central_codificam_os_mesmos_bytes(previa: bool, cheia: bool, lado_max: int | None) -> None:
    """Se as duas pontas divergissem, a coordenada devolvida pelo modelo não bateria com a imagem que ele viu."""
    from app.devices import manager

    png = _png()
    a = codificacao.codificar(png, previa=previa, cheia=cheia, lado_max=lado_max)
    b = manager._codificar(png, previa=previa, cheia=cheia, lado_max=lado_max)
    assert (a.largura, a.altura, a.cheia, a.miniatura, a.modelo) == (b.largura, b.altura, b.cheia, b.miniatura,
                                                                       b.modelo)
    assert codificacao.dimensoes_do_modelo(1080, 2400, 1280) == manager.dimensoes_do_modelo(1080, 2400, 1280)
    assert codificacao.THUMB_WIDTH == manager.THUMB_WIDTH


# ---------------------------------------------------------------- o corpo da mídia
def test_corpo_da_midia_ida_e_volta_e_recusa_o_que_nao_e_jpeg() -> None:
    cod = codificacao.codificar(_png(), previa=True, cheia=False, lado_max=None)
    corpo = empacotar_midia(cod.largura, cod.altura, {"cheia": cod.cheia, "miniatura": cod.miniatura}, captura_ms=3)
    cab, partes = desempacotar_midia(corpo)
    assert (cab["largura"], cab["altura"], cab["captura_ms"]) == (540, 1200, 3)
    assert partes == {"cheia": cod.cheia, "miniatura": cod.miniatura}

    with pytest.raises(ValueError, match="não é JPEG"):
        desempacotar_midia(empacotar_midia(540, 1200, {"cheia": b"\x89PNG-disfarcado"}))
    with pytest.raises(ValueError, match="sobrando"):
        desempacotar_midia(corpo + b"x")
    with pytest.raises(ValueError, match="parte inválida"):
        desempacotar_midia(empacotar_midia(540, 1200, {"segredo": cod.cheia}))      # type: ignore[dict-item]
    with pytest.raises(ValueError, match="largura"):
        desempacotar_midia(empacotar_midia(0, 1200, {"cheia": cod.cheia}))
    with pytest.raises(ValueError, match="cabeçalho"):
        desempacotar_midia((10 ** 6).to_bytes(4, "big") + b"{}")


def test_resultado_de_observacao_e_mensagem_do_contrato() -> None:
    msg = parse_upstream({"type": "observe_result", "request_id": "pedido-0001", "ok": True, "largura": 1080,
                          "altura": 2400})
    assert msg.type == "observe_result" and msg.largura == 1080


# ---------------------------------------------------------------- anúncio da feature
async def test_agente_com_pillow_anuncia_observe_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with CentralComMidia(aceitas=[FEATURE_RESERVA_DE_BOOT, FEATURE_OBSERVACAO_LOCAL]) as central:
        agente, tarefa = await _conectar(central, tmp_path, monkeypatch, _nada)
        assert set(central.aberturas[0]["hello"]["features"]) == {FEATURE_RESERVA_DE_BOOT, FEATURE_OBSERVACAO_LOCAL}
        await _esperar(lambda: FEATURE_OBSERVACAO_LOCAL in agente.features_aceitas, "ler o welcome")
        await _encerrar(tarefa)


def test_agente_sem_pillow_nao_anuncia_e_continua_importando() -> None:
    """Venv de worker anterior a esta feature: sem Pillow, o agente sobe e só não promete a captura na origem."""
    codigo = ("import sys; sys.modules['PIL'] = None\n"
              "import app.worker.agent as a\n"
              "print(','.join(a.FEATURES))")
    saida = subprocess.run([sys.executable, "-c", codigo], cwd=BACKEND, capture_output=True, text=True, timeout=60)
    assert saida.returncode == 0, saida.stderr
    assert saida.stdout.strip() == FEATURE_RESERVA_DE_BOOT


# ---------------------------------------------------------------- o pedido
async def test_imagem_vai_pelo_canal_de_midia_ja_reduzida_na_origem(tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    adb = AdbFalso()
    async with CentralComMidia(aceitas=[FEATURE_OBSERVACAO_LOCAL]) as central:
        agente, tarefa = await _com_agente(central, tmp_path, monkeypatch, adb)
        await _esperar(lambda: agente.features_aceitas, "ler o welcome")
        await central.enviar(_pedido(lado_max=512))
        await _esperar(lambda: central.envios, "a imagem chegar pelo canal de mídia")
        await _encerrar(tarefa)

    cabecalho, corpo = central.envios[0]
    assert cabecalho == {"request_id": "pedido-0001", "upload_token": "token-de-uso-unico-de-teste-000"}
    cab, partes = desempacotar_midia(corpo)
    assert (cab["largura"], cab["altura"]) == (540, 1200), "a dimensão é a da tela, para a conta de coordenada"
    assert set(partes) == {"modelo"}, "o pedido era só do modelo: nada de JPEG cheio nem miniatura"
    assert Image.open(io.BytesIO(partes["modelo"])).size == codificacao.dimensoes_do_modelo(540, 1200, 512)[:2]
    assert partes["modelo"] == codificacao.codificar(adb.png, previa=False, cheia=False, lado_max=512).modelo
    assert len(corpo) < len(adb.png), "o que atravessa o túnel tem de ser menor que o PNG cheio"
    assert not central.de_tipo("observe_result"), "sucesso não passa pelo canal de comando"


async def test_modelo_igual_a_cheia_vai_uma_vez_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with CentralComMidia(aceitas=[FEATURE_OBSERVACAO_LOCAL]) as central:
        agente, tarefa = await _com_agente(central, tmp_path, monkeypatch, AdbFalso())
        await _esperar(lambda: agente.features_aceitas, "ler o welcome")
        await central.enviar(_pedido(cheia=True, lado_max=4096))
        await _esperar(lambda: central.envios, "a imagem")
        await _encerrar(tarefa)
    cab, partes = desempacotar_midia(central.envios[0][1])
    assert set(partes) == {"cheia"} and cab["modelo_e_cheia"] is True


async def test_tela_sensivel_so_dimensoes_nenhum_pixel_sai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with CentralComMidia(aceitas=[FEATURE_OBSERVACAO_LOCAL]) as central:
        agente, tarefa = await _com_agente(central, tmp_path, monkeypatch, AdbFalso())
        await _esperar(lambda: agente.features_aceitas, "ler o welcome")
        await central.enviar(_pedido(so_dimensoes=True))
        await _esperar(lambda: central.de_tipo("observe_result"), "as dimensões")
        await _encerrar(tarefa)
    resposta = central.de_tipo("observe_result")[0]
    assert (resposta["ok"], resposta["largura"], resposta["altura"]) == (True, 540, 1200)
    assert central.envios == [], "tela sensível não pode mandar imagem nenhuma"


async def test_sem_a_feature_aceita_o_agente_nao_captura(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Central antigo (sem `accepted_features`) não pede; se pedisse, a resposta é a falha — nunca a captura."""
    adb = AdbFalso()
    async with CentralComMidia() as central:
        _agente, tarefa = await _com_agente(central, tmp_path, monkeypatch, adb)
        await _esperar(lambda: central.de_tipo("heartbeat"), "a primeira batida")
        await central.enviar(_pedido())
        await _esperar(lambda: central.de_tipo("observe_result"), "a recusa")
        await _encerrar(tarefa)
    assert central.de_tipo("observe_result")[0]["ok"] is False
    assert adb.chamadas == 0 and central.envios == []


@pytest.mark.parametrize("caso", ["adb", "central_recusa"])
async def test_falha_na_origem_volta_como_falha_explicada(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                         caso: str) -> None:
    adb = AdbFalso(erro=AdbError("screencap falhou em emulator-5554") if caso == "adb" else None)
    async with CentralComMidia(aceitas=[FEATURE_OBSERVACAO_LOCAL], recusar_midia=caso == "central_recusa") as central:
        agente, tarefa = await _com_agente(central, tmp_path, monkeypatch, adb)
        await _esperar(lambda: agente.features_aceitas, "ler o welcome")
        await central.enviar(_pedido(cheia=True))
        await _esperar(lambda: central.de_tipo("observe_result"), "a falha")
        await _encerrar(tarefa)
    falha = central.de_tipo("observe_result")[0]
    assert falha["ok"] is False and falha["request_id"] == "pedido-0001"
    assert ("screencap falhou" if caso == "adb" else "unknown_request") in falha["error"]


def test_url_do_canal_de_midia_segue_a_do_central() -> None:
    assert midia_url("http://127.0.0.1:18000") == "ws://127.0.0.1:18000/api/worker/midia"
    assert midia_url("https://central.exemplo:8443") == "wss://central.exemplo:8443/api/worker/midia"
    assert agent_mod.ws_url("http://127.0.0.1:18000").rsplit("/", 1)[0] == midia_url(
        "http://127.0.0.1:18000").rsplit("/", 1)[0]
