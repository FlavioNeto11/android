"""O agente do worker contra um central de mentira, mas por um WebSocket de verdade (achados #158 e #37).

`grep app.worker` em `backend/tests` dava zero: nem o agente, nem o executor, nem o canal tinham teste. A peça que
resolve a causa de fundo do bug — o agente remoto — só tinha sido validada à mão, numa máquina, com um verbo
(`stop`). Inscrição, reconexão com espera crescente, parada em recusa explicada, ACK antes do resultado, cancel e
a tradução `VerbUncertain` → `uncertain` não tinham rede nenhuma: qualquer refatoração quebrava o caminho calada.

Aqui o transporte é real (`websockets.serve` em porta efêmera do loopback) e o que é falso é o CENTRAL e o
executor. Nada sobe emulador, nada fala `adb`, nada toca no parque.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import websockets

from app.worker import agent as agent_mod
from app.worker import executor as executor_mod
from app.worker.agent import Agent
from app.worker.executor import VerbUncertain
from app.worker.settings import DeviceSpec, WorkerSettings
from app.workers.protocol import FEATURE_RESERVA_DE_BOOT, Dispatch


class CentralFalso:
    """Um central que só faz o que o contrato manda: lê o `hello`, responde `welcome` e depois escuta."""

    def __init__(self, *, credencial: str | None = "credencial-permanente-de-teste",
                 recusa: dict[str, str] | None = None, fechar_apos_welcome: bool = False,
                 aceitas: list[str] | None = None, heartbeat_s: float = 30.0) -> None:
        self.credencial = credencial
        #: `Welcome.accepted_features` (C7). `None` = central antigo: a chave nem vai no `welcome`.
        self.aceitas = aceitas
        self.heartbeat_s = heartbeat_s
        self.recusa = recusa
        self.fechar_apos_welcome = fechar_apos_welcome
        self.aberturas: list[dict[str, Any]] = []
        self.recebidos: list[dict[str, Any]] = []
        self.conexoes = 0
        self.conectado = asyncio.Event()
        self._ws: Any = None
        self._servidor: Any = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._servidor.sockets[0].getsockname()[1]}"

    async def __aenter__(self) -> "CentralFalso":
        self._servidor = await websockets.serve(self._atender, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *_a: Any) -> None:
        self._servidor.close()
        await self._servidor.wait_closed()

    async def _atender(self, ws: Any) -> None:
        self.conexoes += 1
        self.aberturas.append(json.loads(await ws.recv()))
        if self.recusa is not None:
            await ws.send(json.dumps({"type": "refused", **self.recusa}))
            return
        bem_vindo: dict[str, Any] = {"type": "welcome", "heartbeat_s": self.heartbeat_s,
                                     "server_time": "2026-09-22T10:00:00.000Z"}
        if self.credencial and self.aberturas[-1].get("enrollment_token"):
            bem_vindo["credential"] = self.credencial     # só na inscrição, uma única vez
        if self.aceitas is not None:
            bem_vindo["accepted_features"] = self.aceitas
        await ws.send(json.dumps(bem_vindo))
        if self.fechar_apos_welcome:
            await ws.close()
            return
        self._ws = ws
        self.conectado.set()
        try:
            async for bruto in ws:
                self.recebidos.append(json.loads(bruto))
        except websockets.exceptions.ConnectionClosed:
            pass

    async def enviar(self, payload: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(payload))

    def de_tipo(self, tipo: str) -> list[dict[str, Any]]:
        return [m for m in self.recebidos if m.get("type") == tipo]

    def ordem(self, *tipos: str) -> list[str]:
        """Só os tipos que interessam, na ordem em que chegaram — a batida do coração entra no meio e não conta."""
        return [m["type"] for m in self.recebidos if m.get("type") in tipos]


def _settings(tmp_path: Path, servidor: str) -> WorkerSettings:
    return WorkerSettings(worker_id="worker-lan-01", name="Notebook da LAN", server=servidor,
                          work_dir=str(tmp_path / "farm"), sdk_root=str(tmp_path / "sdk-que-nao-existe"),
                          devices=[DeviceSpec(instance_id="android-03", avd_name="worker-01", console_port=5554)])


def _dispatch(verb: str = "start", command_id: str = "c-1", fence: int = 1) -> dict[str, Any]:
    return Dispatch(command_id=command_id, fence=fence, verb=verb, instance_id="android-03",
                    serial="emulator-5554", timeout_s=30.0).model_dump()


async def _esperar(condicao: Any, o_que: str, prazo: float = 5.0) -> None:
    t = 0.0
    while not condicao():
        await asyncio.sleep(0.01)
        t += 0.01
        assert t < prazo, f"tempo esgotado esperando {o_que}"


async def _conectar(central: CentralFalso, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                    executar: Any) -> tuple[Agent, asyncio.Task[None]]:
    agente = Agent(_settings(tmp_path, central.url), enrollment="token-de-inscricao-de-teste")
    monkeypatch.setattr(agente.executor, "run", executar)
    # A batida leva o inventário: sem isto ela varreria processos com psutil e tocaria o disco procurando AVD.
    monkeypatch.setattr(agente.executor, "estado", lambda _spec: ("stopped", None))
    tarefa = asyncio.create_task(agente.run_forever())
    await asyncio.wait_for(central.conectado.wait(), timeout=5)
    return agente, tarefa


async def _encerrar(tarefa: asyncio.Task[None]) -> None:
    tarefa.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarefa


# ---------------------------------------------------------------- inscrição e reconexão
async def test_inscricao_grava_a_credencial_e_a_reconexao_usa_ela(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """O token de inscrição vale UMA vez; o que sustenta toda partida seguinte é a credencial gravada. E queda de
    canal é rede, não falha: o agente volta sozinho, com espera crescente."""
    monkeypatch.setattr(agent_mod, "RECONEXAO_MIN_S", 0.02)
    async with CentralFalso(fechar_apos_welcome=True) as central:
        agente = Agent(_settings(tmp_path, central.url), enrollment="token-de-inscricao-de-teste")
        monkeypatch.setattr(agente.executor, "estado", lambda _spec: ("stopped", None))
        tarefa = asyncio.create_task(agente.run_forever())
        await _esperar(lambda: central.conexoes >= 2, "o agente reconectar depois da queda")
        await _encerrar(tarefa)

    assert central.aberturas[0]["enrollment_token"] == "token-de-inscricao-de-teste"
    assert "token" not in central.aberturas[0]
    # A segunda partida não repete o token de inscrição: ela apresenta a credencial permanente recebida.
    assert central.aberturas[1]["token"] == "credencial-permanente-de-teste"
    assert "enrollment_token" not in central.aberturas[1]
    assert agente.settings.read_credential() == "credencial-permanente-de-teste"


async def test_recusa_explicada_para_o_agente_em_vez_de_martelar_o_central(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """Credencial errada ou worker não inscrito não se resolve insistindo: o agente para e deixa o texto para
    quem administra. Reconectar em laço só encheria o log do central de recusa."""
    monkeypatch.setattr(agent_mod, "RECONEXAO_MIN_S", 0.02)
    async with CentralFalso(recusa={"code": "not_enrolled", "message": "worker não inscrito"}) as central:
        agente = Agent(_settings(tmp_path, central.url), enrollment="token-que-nao-vale")
        monkeypatch.setattr(agente.executor, "estado", lambda _spec: ("stopped", None))
        await asyncio.wait_for(agente.run_forever(), timeout=5)     # volta sozinho, sem precisar de cancel
        await asyncio.sleep(0.1)
        assert central.conexoes == 1, "o agente insistiu depois de uma recusa explicada"


# ---------------------------------------------------------------- um comando, de ponta a ponta
async def test_ack_vem_antes_do_progresso_e_do_resultado(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """"Chegou" e "funcionou" são coisas distintas, e é essa distinção que o central usa para separar, numa
    queda, "não sei se chegou" de "chegou e não sei o efeito"."""
    async def executar(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"started": True, "pid": 4242}

    async with CentralFalso() as central:
        _agente, tarefa = await _conectar(central, tmp_path, monkeypatch, executar)
        await central.enviar(_dispatch())
        await _esperar(lambda: central.de_tipo("result"), "o desfecho chegar")
        await _encerrar(tarefa)

    assert central.ordem("ack", "progress", "result")[:3] == ["ack", "progress", "result"]
    resultado = central.de_tipo("result")[0]
    assert resultado["outcome"] == "succeeded" and resultado["data"] == {"started": True, "pid": 4242}
    assert resultado["fence"] == 1, "a cerca volta como veio; é ela que barra ordem vinda do limbo"


async def test_verbo_incerto_chega_como_uncertain_e_nunca_vira_falha(tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    async def executar(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise VerbUncertain("o aparelho não completou o boot em 480 s; estado desconhecido")

    async with CentralFalso() as central:
        _agente, tarefa = await _conectar(central, tmp_path, monkeypatch, executar)
        await central.enviar(_dispatch())
        await _esperar(lambda: central.de_tipo("result"), "o desfecho chegar")
        await _encerrar(tarefa)

    resultado = central.de_tipo("result")[0]
    assert resultado["outcome"] == "uncertain"
    assert "não completou o boot" in resultado["reason"]


async def test_cancel_do_central_interrompe_o_verbo_e_o_desfecho_e_cancelled(tmp_path: Path,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    começou = asyncio.Event()

    async def executar(*_a: Any, **_k: Any) -> dict[str, Any]:
        começou.set()
        await asyncio.Event().wait()                      # verbo que só termina se for cancelado
        return {}

    async with CentralFalso() as central:
        _agente, tarefa = await _conectar(central, tmp_path, monkeypatch, executar)
        await central.enviar(_dispatch())
        await asyncio.wait_for(começou.wait(), timeout=5)
        await central.enviar({"type": "cancel", "command_id": "c-1"})
        await _esperar(lambda: central.de_tipo("result"), "o desfecho do cancelamento chegar")
        await _encerrar(tarefa)

    resultado = central.de_tipo("result")[0]
    assert resultado["outcome"] == "cancelled" and "cancelado" in resultado["reason"]
    assert "antes de tocar no aparelho" in resultado["reason"], \
        "`cancelled` só pode ser dito quando o aparelho ficou intacto, e o motivo precisa afirmar isso"


async def test_cancelar_depois_de_o_efeito_comecar_e_uncertain_e_nunca_cancelled(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A metade do achado #23 que faltava: `cancelled` é cancelamento CONFIRMADO (013_commands.sql:25). Com o
    emulador já iniciado, a `CancelledError` interrompe a ESPERA — a thread do `emu.start_process` continua o que
    estava fazendo. Dizer "cancelado" ali é afirmar que nada aconteceu sobre um aparelho que ligou."""
    começou = asyncio.Event()

    async def executar(*_a: Any, **_k: Any) -> dict[str, Any]:
        executor_mod.marcar_efeito("o emulador worker-01 já tinha sido iniciado nesta máquina")
        começou.set()
        await asyncio.Event().wait()
        return {}

    async with CentralFalso() as central:
        _agente, tarefa = await _conectar(central, tmp_path, monkeypatch, executar)
        await central.enviar(_dispatch())
        await asyncio.wait_for(começou.wait(), timeout=5)
        await central.enviar({"type": "cancel", "command_id": "c-1"})
        await _esperar(lambda: central.de_tipo("result"), "o desfecho do cancelamento chegar")
        await _encerrar(tarefa)

    resultado = central.de_tipo("result")[0]
    assert resultado["outcome"] == "uncertain", "o agente disse 'cancelado' sobre um efeito que já tinha começado"
    assert "já tinha sido iniciado" in resultado["reason"]


async def test_reentrega_do_mesmo_comando_nao_executa_duas_vezes(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """Reconexão faz o central reentregar. O agente devolve o MESMO desfecho, guardado no diário, em vez de
    resetar um aparelho de novo porque a rede piscou."""
    execucoes = []

    async def executar(*_a: Any, **_k: Any) -> dict[str, Any]:
        execucoes.append(1)
        return {"stopped": True}

    async with CentralFalso() as central:
        _agente, tarefa = await _conectar(central, tmp_path, monkeypatch, executar)
        await central.enviar(_dispatch(verb="stop"))
        await _esperar(lambda: central.de_tipo("result"), "o primeiro desfecho")
        await central.enviar(_dispatch(verb="stop"))
        await _esperar(lambda: len(central.de_tipo("result")) >= 2, "o desfecho repetido")
        await _encerrar(tarefa)

    assert execucoes == [1], "o verbo foi executado duas vezes por causa de uma reentrega"
    assert central.de_tipo("result")[0] == central.de_tipo("result")[1]
    assert len(central.de_tipo("ack")) == 1               # a reentrega nem chega a virar comando novo


# ---------------------------------------------------------------- recursos efetivos e features (adendo v0.20)
async def _nada(*_a: Any, **_k: Any) -> dict[str, Any]:
    return {}


async def test_hello_anuncia_a_reserva_de_boot_e_a_batida_leva_os_recursos_efetivos(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """C6/C7 no fio: o `hello` anuncia `boot_reservations` e a batida leva `reserved_mb` e `measured_at` — é com
    eles que o central deixa de admitir boot numa RAM que o agente já prometeu a outro."""
    async with CentralFalso(aceitas=[FEATURE_RESERVA_DE_BOOT, "coisa-que-este-agente-nao-conhece"],
                            heartbeat_s=0.05) as central:
        agente, tarefa = await _conectar(central, tmp_path, monkeypatch, _nada)
        hello = central.aberturas[0]["hello"]
        assert hello["features"] == [FEATURE_RESERVA_DE_BOOT]
        assert hello["resources"]["reserved_mb"] == 0 and hello["resources"]["measured_at"]
        assert hello["resources"]["mem_available_mb"] is not None
        # Só o que foi ANUNCIADO entra: o central não liga por aqui o que este agente não sabe fazer.
        await _esperar(lambda: agente.features_aceitas == {FEATURE_RESERVA_DE_BOOT}, "ler o welcome novo")

        reserva = agente.executor.reservas.tomar("android-03", 2700)
        try:
            await _esperar(lambda: any((b.get("resources") or {}).get("reserved_mb") == 2700
                                       for b in central.de_tipo("heartbeat")), "a batida levar a reserva")
        finally:
            agente.executor.reservas.liberar(reserva)
        assert agente._recursos().reserved_mb == 0
        await _encerrar(tarefa)


async def test_central_antigo_sem_accepted_features_nao_liga_nada(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Central antigo não manda a chave: o agente novo segue o caminho de antes, sem feature aceita."""
    async with CentralFalso() as central:
        agente, tarefa = await _conectar(central, tmp_path, monkeypatch, _nada)
        await _esperar(lambda: central.de_tipo("heartbeat"), "a primeira batida")
        assert agente.features_aceitas == set()
        await _encerrar(tarefa)
