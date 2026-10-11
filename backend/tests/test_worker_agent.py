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
from typing import Any, Callable

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
        # Com prazo: em 27/09 a suíte inteira ficou 5 h parada neste arquivo (não reproduziu isolado nem na
        # repetição). Sem prazo, um fechamento que não termina pendura tudo; com prazo, reprova dizendo onde.
        await asyncio.wait_for(self._servidor.wait_closed(), timeout=10)

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
        # Com prazo pelo mesmo motivo do `__aexit__`: agente que não atende o cancelamento reprova, não pendura.
        await asyncio.wait_for(tarefa, timeout=10)


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


async def test_cancelar_o_agente_enquanto_a_batida_morre_nao_e_engolido(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """A corrida por trás do flake de 11/10 (`test_inscricao_grava_a_credencial_e_a_reconexao_usa_ela`, 3/3 verde isolado e vermelho na PG
    inteira): a sessão acaba e o agente espera a tarefa da batida morrer; um `cancel()` do agente que chega NESSA janela era engolido
    pelo `suppress(CancelledError)` que cercava a espera, e o agente reconectava para sempre (o teste estourava os 10 s de `_encerrar`).
    Aqui a janela é alargada de propósito: a batida demora 0,3 s para morrer e o cancelamento chega no meio."""
    monkeypatch.setattr(agent_mod, "RECONEXAO_MIN_S", 0.02)
    morrendo = asyncio.Event()
    lenta = {"ligada": True}

    async def batida_lenta_para_morrer(_intervalo: float) -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            morrendo.set()
            if lenta["ligada"]:
                await asyncio.sleep(0.3)          # a janela: o agente está em `await batida`
            raise

    async with CentralFalso(fechar_apos_welcome=True) as central:
        agente = Agent(_settings(tmp_path, central.url), enrollment="token-de-inscricao-de-teste")
        monkeypatch.setattr(agente.executor, "estado", lambda _spec: ("stopped", None))
        monkeypatch.setattr(agente, "_bater", batida_lenta_para_morrer)
        tarefa = asyncio.create_task(agente.run_forever())
        try:
            await asyncio.wait_for(morrendo.wait(), timeout=5)
            tarefa.cancel()                       # chega com o agente esperando a batida morrer
            # `asyncio.wait` e não `wait_for`: este cancelaria a tarefa de novo, e com o defeito esse segundo cancel também seria engolido.
            await asyncio.wait({tarefa}, timeout=3)
            assert tarefa.done(), "o cancelamento do agente foi engolido enquanto a batida morria (o agente seguiu reconectando)"
            assert tarefa.cancelled()
        finally:
            lenta["ligada"] = False               # com o defeito, desfaz o estrago: a janela fecha e o cancel passa
            for _ in range(50):
                if tarefa.done():
                    break
                tarefa.cancel()
                await asyncio.wait({tarefa}, timeout=0.2)


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
        # `boot_reservations` sempre; `observe_local` também, quando o venv tem Pillow (o desta suíte tem) — o
        # anúncio condicional está em `test_observacao_na_origem.py`.
        assert hello["features"] == list(agent_mod.FEATURES) and FEATURE_RESERVA_DE_BOOT in hello["features"]
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


async def test_toda_batida_leva_o_relogio_local_e_mantem_o_desvio_da_conexao(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A9: `sent_at` (relógio local na saída) vai em CADA batida — é com ele que o central re-mede o desvio —, e o
    `clock_offset_s` da conexão continua indo, para o central antigo, que ignora o campo novo."""
    async with CentralFalso(heartbeat_s=0.05) as central:
        agente, tarefa = await _conectar(central, tmp_path, monkeypatch, _nada)
        await _esperar(lambda: len(central.de_tipo("heartbeat")) >= 2, "duas batidas")
        batidas = central.de_tipo("heartbeat")
        assert all(b.get("sent_at") for b in batidas)
        assert len({b["sent_at"] for b in batidas}) == len(batidas)            # medido de novo, não repetido
        assert all("clock_offset_s" in b for b in batidas)
        assert (agent_mod.parse_iso(batidas[-1]["sent_at"]) - agent_mod.now()).total_seconds() < 5
        await _encerrar(tarefa)


# ---------------------------------------------------------------- reentrega DEPOIS do ack (frente F4, item A3)
async def test_reentrega_depois_do_result_ack_devolve_o_mesmo_desfecho_e_nao_executa(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """O `result_ack` apagava o desfecho do diário, e a guarda da cerca era `<`: a reentrega do MESMO comando (a
    mesma cerca) depois do ack — a do JetStream por `ack_wait`, ou a de um central que despachou duas vezes —
    era executada de novo. Num `reset`, dois wipes."""
    execucoes = []

    async def executar(*_a: Any, **_k: Any) -> dict[str, Any]:
        execucoes.append(1)
        return {"reset": True}

    async with CentralFalso() as central:
        agente, tarefa = await _conectar(central, tmp_path, monkeypatch, executar)
        await central.enviar(_dispatch(verb="reset"))
        await _esperar(lambda: central.de_tipo("result"), "o primeiro desfecho")
        await central.enviar({"type": "result_ack", "command_id": "c-1"})
        await _esperar(lambda: "c-1" not in agente._diario.resultados, "o ack tirar o desfecho da fila de reenvio")
        await central.enviar(_dispatch(verb="reset"))
        await _esperar(lambda: len(central.de_tipo("result")) >= 2, "a resposta à reentrega")
        await _encerrar(tarefa)

    assert execucoes == [1], "a reentrega depois do ack executou o verbo de novo"
    primeiro, segundo = central.de_tipo("result")
    assert segundo == primeiro and segundo["outcome"] == "succeeded"
    # O desfecho confirmado sobrevive ao reinício do agente: é o disco que responde à reentrega.
    from app.worker.diario import DiarioDoAgente
    assert DiarioDoAgente(agente.settings.work_dir).desfecho("c-1") == primeiro


async def test_outro_comando_com_a_cerca_ja_executada_e_recusado_sem_executar(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cerca IGUAL à última executada e `command_id` desconhecido: reentrega cujo desfecho já saiu do diário, ou
    ordem duplicada. Recusa explícita, `failed` ("nada foi executado agora"), nunca sucesso de efeito novo."""
    from app.workers.protocol import RECUSA_CERCA_NAO_MAIOR, parse_upstream

    execucoes = []

    async def executar(*_a: Any, **_k: Any) -> dict[str, Any]:
        execucoes.append(1)
        return {"stopped": True}

    async with CentralFalso() as central:
        _agente, tarefa = await _conectar(central, tmp_path, monkeypatch, executar)
        await central.enviar(_dispatch(verb="stop", command_id="c-1", fence=4))
        await _esperar(lambda: central.de_tipo("result"), "o primeiro desfecho")
        await central.enviar({"type": "result_ack", "command_id": "c-1"})
        await central.enviar(_dispatch(verb="reset", command_id="c-2", fence=4))
        await _esperar(lambda: len(central.de_tipo("result")) >= 2, "a recusa")
        await _encerrar(tarefa)

    assert execucoes == [1]
    recusa = central.de_tipo("result")[1]
    assert recusa["command_id"] == "c-2" and recusa["outcome"] == "failed" and recusa["fence"] == 4
    assert "nada foi executado" in recusa["reason"]
    assert recusa["data"] == {"refused": RECUSA_CERCA_NAO_MAIOR, "last_fence": 4}
    # Central antigo: é um `Result` do contrato de sempre (o desfecho existe no `Literal` dele).
    assert parse_upstream(recusa).outcome == "failed"


def test_diario_guarda_poucos_confirmados_e_nao_os_reenvia(tmp_path: Path) -> None:
    """Confirmado não volta para a fila de reenvio, e o arquivo não cresce sem limite."""
    from app.worker.diario import LIMITE_CONFIRMADOS, DiarioDoAgente

    diario = DiarioDoAgente(tmp_path)
    for i in range(LIMITE_CONFIRMADOS + 5):
        diario.guardar(f"c-{i}", {"type": "result", "command_id": f"c-{i}", "outcome": "succeeded", "fence": i})
        assert diario.confirmar(f"c-{i}") is True
    assert diario.pendentes() == []
    relido = DiarioDoAgente(tmp_path)
    assert len(relido.confirmados) == LIMITE_CONFIRMADOS
    assert relido.desfecho("c-0") is None, "o mais antigo sai primeiro"
    assert relido.desfecho(f"c-{LIMITE_CONFIRMADOS + 4}")["fence"] == LIMITE_CONFIRMADOS + 4


def test_diario_de_agente_anterior_continua_legivel_nos_dois_sentidos(tmp_path: Path) -> None:
    """Agente novo lendo diário antigo (sem `confirmados`), e agente antigo lendo o novo: ele só procura
    `resultados` e `cercas`, que seguem com o mesmo formato — e sem os confirmados, que ele reenviaria."""
    from app.worker.diario import ARQUIVO, DiarioDoAgente

    (tmp_path / ARQUIVO).write_text(json.dumps({"resultados": {"c-9": {"command_id": "c-9"}},
                                                "cercas": {"android-03": 9}}), encoding="utf-8")
    diario = DiarioDoAgente(tmp_path)
    assert diario.cerca("android-03") == 9 and diario.confirmados == {}
    diario.guardar("c-10", {"command_id": "c-10"})
    diario.confirmar("c-10")
    bruto = json.loads((tmp_path / ARQUIVO).read_text(encoding="utf-8"))
    assert bruto["resultados"] == {"c-9": {"command_id": "c-9"}} and bruto["cercas"] == {"android-03": 9}
    assert "c-10" in bruto["confirmados"]


# ---------------------------------------------------------------- 29.73: reconexão rápida depois do reinício do central
def _fechado(codigo: int | None) -> Exception:
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close
    if codigo is None:
        return OSError("connection refused")
    return ConnectionClosedError(Close(codigo, "service restart"), Close(codigo, "service restart"), True)


def test_reinicio_do_central_abre_a_janela_curta_e_a_escada_volta_depois() -> None:
    """Deploy 28 (04/10): o central avisou 1012, ficou ~76 s fora, e a escada (2, 4, 8, 16, 32, 60 s) deixou o
    worker um minuto fora depois de o central voltar. Com o aviso de reinício, a espera fica curta e fixa por 3 min;
    a queda sem aviso segue a escada de sempre."""
    r = agent_mod.EsperaDeReconexao()
    assert r.depois_da_queda(_fechado(1012), 0.0) == agent_mod.RECONEXAO_RAJADA_S
    # Dentro da janela, as recusas de conexão (o central ainda subindo) não fazem a espera crescer.
    esperas = [r.depois_da_queda(_fechado(None), t) for t in (3.0, 6.0, 9.0, 60.0, 120.0, 179.0)]
    assert esperas == [agent_mod.RECONEXAO_RAJADA_S] * 6
    # Passada a janela, a escada retoma do ponto em que estava.
    assert r.depois_da_queda(_fechado(None), 181.0) == agent_mod.RECONEXAO_MIN_S
    assert r.depois_da_queda(_fechado(None), 183.0) == agent_mod.RECONEXAO_MIN_S * 2


def test_queda_sem_aviso_de_reinicio_segue_a_escada() -> None:
    r = agent_mod.EsperaDeReconexao()
    esperas = [r.depois_da_queda(_fechado(c), float(i)) for i, c in enumerate((None, 1006, None, None, None, None, None))]
    assert esperas == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
    assert agent_mod.codigo_do_fechamento(_fechado(1001)) == 1001 and agent_mod.codigo_do_fechamento(OSError()) is None
    r.sessao_viveu()
    assert r.depois_da_queda(_fechado(None), 100.0) == agent_mod.RECONEXAO_MIN_S


# ---------------------------------------------------------------- 29.76: o 4409 (conflito) cede o canal por 10 min
async def test_conflito_4409_cede_o_canal_pela_espera_longa_e_a_recusa_por_fechamento_segue_a_escada(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Revisão do deploy 30 (achado 10): com o 4409 (outra conexão deste worker assumiu) o agente caía no `except` e os
    dois se derrubavam a cada espera da escada. Sair também não resolve (revisão do #303): a tarefa `farm-agente` religa
    em 1 min. Agora o 4409 usa `ESPERA_NO_CONFLITO_S` (10 min) e o agente segue vivo; o 4401 segue a escada."""
    monkeypatch.setattr(agent_mod, "RECONEXAO_MIN_S", 30.0)          # a escada, se usada, passaria do prazo do teste
    monkeypatch.setattr(agent_mod, "ESPERA_NO_CONFLITO_S", 0.01)
    agente = Agent(_settings(tmp_path, "http://127.0.0.1:9"))
    chamadas: list[int] = []

    async def sessao(codigo: int) -> None:
        chamadas.append(codigo)
        raise _fechado(codigo)

    async def rodar_ate(n: int) -> None:
        tarefa = asyncio.create_task(agente.run_forever())
        try:
            await asyncio.wait_for(_ate_que(lambda: len(chamadas) >= n), timeout=5)
        finally:
            tarefa.cancel()
            with pytest.raises(asyncio.CancelledError):
                await tarefa

    monkeypatch.setattr(agente, "_sessao", lambda: sessao(4409))
    await rodar_ate(3)                       # três tentativas em ~0,02 s: a espera foi a do conflito, e ele não saiu
    assert set(chamadas) == {4409}

    chamadas.clear()
    monkeypatch.setattr(agent_mod, "RECONEXAO_MIN_S", 0.01)
    monkeypatch.setattr(agent_mod, "ESPERA_NO_CONFLITO_S", 30.0)    # a do conflito, se usada, passaria do prazo
    monkeypatch.setattr(agente, "_sessao", lambda: sessao(4401))
    await rodar_ate(3)
    assert set(chamadas) == {4401}


async def _ate_que(condicao: Callable[[], bool]) -> None:
    while not condicao():
        await asyncio.sleep(0.005)


async def test_deslocado_cancela_o_verbo_em_voo_e_nao_age_mais_no_aparelho(tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Revisão do #303: quem perdeu o canal não age em aparelho. Com um verbo em voo, o 4409 o cancela no próximo
    ponto seguro e nenhum passo seguinte acontece, nem durante a espera de 10 min."""
    monkeypatch.setattr(agent_mod, "ESPERA_NO_CONFLITO_S", 30.0)
    agente = Agent(_settings(tmp_path, "http://127.0.0.1:9"))
    passos: list[str] = []

    async def verbo() -> None:
        passos.append("antes")
        await asyncio.sleep(0.05)              # o ponto seguro entre duas ações no aparelho
        passos.append("depois")

    async def sessao() -> None:
        agente._tarefas["c-1"] = asyncio.create_task(verbo())
        await asyncio.sleep(0)                 # o verbo começa
        raise _fechado(4409)

    monkeypatch.setattr(agente, "_sessao", sessao)
    tarefa = asyncio.create_task(agente.run_forever())
    await asyncio.sleep(0.2)                   # já passou do tempo em que o verbo daria o passo seguinte
    tarefa.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarefa
    assert passos == ["antes"]
    assert agente._tarefas["c-1"].cancelled()


async def test_copia_defasada_sai_e_nao_volta_ate_atualizar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Revisão do #303: se a cópia velha não for o processo da tarefa (ou o Stop falhar calado), a velha e a nova se
    revezariam a cada 10 min. Com o motivo `agente_defasado`, a velha sai e uma marca com o código dela impede a
    volta (a tarefa a religa e ela não conecta); outro código (o atualizado) sobe normalmente."""
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close
    from app.contracts.worker.protocol import FECHAMENTO_MOTIVO_DEFASADO
    monkeypatch.setattr(agent_mod, "AGENT_CODE", "codigo-velho")
    agente = Agent(_settings(tmp_path, "http://127.0.0.1:9"))
    chamadas: list[str] = []

    async def sessao() -> None:
        chamadas.append("sessao")
        fechamento = Close(4409, FECHAMENTO_MOTIVO_DEFASADO)
        raise ConnectionClosedError(fechamento, fechamento, True)

    monkeypatch.setattr(agente, "_sessao", sessao)
    await asyncio.wait_for(agente.run_forever(), timeout=5)
    await asyncio.wait_for(agente.run_forever(), timeout=5)      # a tarefa religou: a marca impede a conexão
    assert chamadas == ["sessao"]
    monkeypatch.setattr(agent_mod, "AGENT_CODE", "codigo-novo")  # atualizado: a marca não vale para ele
    monkeypatch.setattr(agente, "_sessao", lambda: _sair())
    await asyncio.wait_for(agente.run_forever(), timeout=5)


async def _sair() -> None:
    raise RuntimeError("recusa explicada: encerra o run_forever do teste")


def test_motivo_do_conflito_so_diz_defasado_com_certeza() -> None:
    from app.contracts.worker.protocol import FECHAMENTO_MOTIVO_DEFASADO
    from app.workers.registry import motivo_do_conflito
    assert motivo_do_conflito("novo", "velho", "novo") == FECHAMENTO_MOTIVO_DEFASADO
    for central, deslocado, novo in (("novo", "novo", "novo"), ("novo", "velho", "velho"), ("novo", "velho", "outro"),
                                     (None, "velho", "novo"), ("novo", None, "novo"), ("novo", "velho", None)):
        assert motivo_do_conflito(central, deslocado, novo) == "", (central, deslocado, novo)


async def test_cancelamento_antes_de_comecar_grava_cancelled_e_solta_o_aparelho(tmp_path: Path) -> None:
    """Revisão do #303: cancelada antes do `try` (aqui, antes de a tarefa sequer começar), a execução não gravava
    desfecho nem soltava `_tarefas`/`_ocupados`, e o aparelho ficava recusando comandos até o processo reiniciar."""
    from app.contracts.worker.protocol import Dispatch
    agente = Agent(_settings(tmp_path, "http://127.0.0.1:9"))
    msg = Dispatch(command_id="c-antes", instance_id="android-09", serial="emulator-5554", verb="stop", params={}, fence=7)
    await agente._despachar(msg)
    agente._tarefas["c-antes"].cancel()          # antes do primeiro passo da corrotina
    await asyncio.sleep(0.05)
    assert "c-antes" not in agente._tarefas and "android-09" not in agente._ocupados
    desfecho = agente._diario.desfecho("c-antes")
    assert desfecho is not None and desfecho["outcome"] == "cancelled" and desfecho["fence"] == 7


async def test_marca_de_cedido_vence_em_24_horas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Revisão do #303: a marca barraria um agente legítimo para sempre (rollback ao mesmo código; agente manual
    que deslocou o da tarefa e depois fechou). Com 24 h de validade, o agente volta a tentar."""
    import json as _json
    from datetime import timedelta
    from app.util import now
    monkeypatch.setattr(agent_mod, "AGENT_CODE", "codigo-x")
    agente = Agent(_settings(tmp_path, "http://127.0.0.1:9"))
    marca = agente._marca_de_cedido()
    marca.write_text(_json.dumps({"agent_code": "codigo-x", "em": (now() - timedelta(hours=1)).isoformat()}),
                     encoding="utf-8")
    assert agente._cedido_antes()
    marca.write_text(_json.dumps({"agent_code": "codigo-x", "em": (now() - timedelta(hours=25)).isoformat()}),
                     encoding="utf-8")
    assert not agente._cedido_antes()
    # Marca "do futuro" (relógio que andou para trás depois de gravá-la): não vale.
    marca.write_text(_json.dumps({"agent_code": "codigo-x", "em": (now() + timedelta(hours=2)).isoformat()}),
                     encoding="utf-8")
    assert not agente._cedido_antes()


def test_a_instalacao_apaga_a_marca_de_cedido() -> None:
    raiz = Path(__file__).resolve().parents[2]
    assert "agente-cedido.json" in (raiz / "scripts" / "worker-install.ps1").read_text(encoding="utf-8")
    assert "agente-cedido.json" in (raiz / "scripts" / "worker-install.sh").read_text(encoding="utf-8")

