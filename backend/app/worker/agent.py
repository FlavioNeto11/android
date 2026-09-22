"""O laço do agente: liga para o central, declara o que é, bate o coração e obedece comandos.

**O worker liga para o central**, nunca o contrário. Atravessa NAT sem abrir porta na casa de ninguém, que é a
restrição de partida; e reconecta sozinho, porque queda de rede não é falha de execução.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

import psutil
import websockets

from ..util import now, parse_iso
from ..workers.protocol import (Ack, Dispatch, Heartbeat, Hello, Progress, Result, WorkerDevice, WorkerResources)
from . import AGENT_VERSION
from .executor import VERBS, VerbRefused, VerbUncertain, WorkerExecutor
from .settings import WorkerSettings, host_os

log = logging.getLogger("poc.worker")

#: Espera entre tentativas de reconexão. Cresce até um teto: martelar o central não ajuda ninguém.
RECONEXAO_MIN_S = 2.0
RECONEXAO_MAX_S = 60.0


def ws_url(server: str) -> str:
    """`http://host:8000` → `ws://host:8000/api/worker/ws` (e `https` → `wss`)."""
    p = urlparse(server if "://" in server else f"http://{server}")
    esquema = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}.get(p.scheme, "ws")
    return urlunparse((esquema, p.netloc, "/api/worker/ws", "", "", ""))


def clock_offset_seconds(server_time: str | None, local_now: datetime) -> float | None:
    """`Welcome.server_time` (ISO, relógio do central) menos `local_now`, em segundos.

    Positivo = relógio local está ATRASADO em relação ao central. `None` quando `server_time` está ausente ou
    ilegível — nunca derruba a conexão por causa disto. Função pura de propósito: achado #142 (relógios
    dessincronizados ~97 s entre central e worker, nenhum dos dois com fonte de hora) pede algo testável sem
    subir um WebSocket.
    """
    try:
        servidor = parse_iso(server_time) if server_time else None
    except ValueError:
        return None
    if servidor is None:
        return None
    return (servidor - local_now).total_seconds()


class Agent:
    def __init__(self, settings: WorkerSettings, *, enrollment: str | None = None):
        self.settings = settings
        self.cfg = settings.to_config()
        self.enrollment = enrollment
        self.executor = WorkerExecutor(settings, self.cfg, progress=self._progress)
        self._ws: Any = None
        self._command_atual: str | None = None
        self._tarefas: dict[str, asyncio.Task[None]] = {}
        #: Desvio de relógio contra o central, calculado uma vez por conexão (achado #142).
        self._clock_offset_s: float | None = None

    # ------------------------------------------------------------------ declaração
    def _recursos(self) -> WorkerResources:
        vm = psutil.virtual_memory()
        disco = psutil.disk_usage(self.settings.work_dir)
        return WorkerResources(
            cpu_percent=psutil.cpu_percent(interval=None), cpu_count=psutil.cpu_count(logical=True),
            ram_total_mb=int(vm.total / (1024 * 1024)), ram_free_mb=int(vm.available / (1024 * 1024)),
            disk_free_gb=round(disco.free / (1024 ** 3), 1))

    def _declarados(self) -> list[WorkerDevice]:
        """Só o que está na configuração: nenhuma sondagem, nenhum `adb`, nenhum tempo imprevisível."""
        return [WorkerDevice(serial=d.serial, avd_name=d.avd_name, state="unknown", adb_port=d.adb_port,
                             instance_id=d.instance_id) for d in self.settings.devices]

    async def _inventario(self) -> list[WorkerDevice]:
        """Estado observado de cada aparelho, fora do laço de eventos.

        Sondar custa `adb` de verdade — medido: 27,7 s para seis aparelhos nesta máquina. Fazer isso no laço
        travava o agente inteiro, e como o servidor só espera 20 s pela primeira mensagem, o handshake morria
        antes de nascer. Por isso o `hello` leva só o que está declarado, e o estado real chega na primeira batida.
        """
        def sondar() -> list[WorkerDevice]:
            saida = []
            for spec in self.settings.devices:
                try:
                    estado, detalhe = self.executor.estado(spec)
                except Exception as exc:  # noqa: BLE001 - inventário nunca derruba a batida
                    estado, detalhe = "unknown", f"falha ao sondar: {exc}"
                saida.append(WorkerDevice(serial=spec.serial, avd_name=spec.avd_name, state=estado, detail=detalhe,
                                          adb_port=spec.adb_port, instance_id=spec.instance_id))
            return saida

        return await asyncio.to_thread(sondar)

    def _hello(self) -> Hello:
        sistema, versao = host_os()
        return Hello(worker_id=self.settings.worker_id, name=self.settings.name, agent_version=AGENT_VERSION,
                     os=sistema, os_version=versao, appium_mode=self.settings.appium,
                     appium_url=self.settings.appium_url, max_slots=self.settings.max_slots,
                     verbs=list(VERBS), devices=self._declarados(), resources=self._recursos())

    # ------------------------------------------------------------------ envio
    async def _send(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            return
        await self._ws.send(json.dumps(payload))

    def _progress(self, message: str) -> None:
        if self._command_atual:
            asyncio.get_running_loop().create_task(
                self._send(Progress(command_id=self._command_atual, message=message).model_dump()))

    # ------------------------------------------------------------------ comando
    async def _executar(self, msg: Dispatch) -> None:
        """ACK primeiro, resultado depois. São coisas distintas: "chegou" não é "funcionou"."""
        await self._send(Ack(command_id=msg.command_id).model_dump())
        spec = self.settings.device(msg.instance_id)
        if spec is None:
            await self._resultado(msg, "failed", f"este worker não hospeda {msg.instance_id}")
            return
        anterior, self._command_atual = self._command_atual, msg.command_id
        try:
            dados = await self.executor.run(msg.verb, spec, msg.params)
            await self._resultado(msg, "succeeded", None, dados)
        except VerbRefused as exc:
            # Recusa antes de agir: o central pode afirmar que o aparelho ficou intacto.
            await self._resultado(msg, "failed", str(exc))
        except VerbUncertain as exc:
            await self._resultado(msg, "uncertain", str(exc))
        except asyncio.CancelledError:
            await self._resultado(msg, "cancelled", "cancelado a pedido do servidor")
            raise
        except Exception as exc:  # noqa: BLE001 - agiu e quebrou no meio: não se sabe o efeito
            log.exception("comando %s (%s)", msg.command_id, msg.verb)
            await self._resultado(msg, "uncertain", f"erro inesperado no worker: {exc}")
        finally:
            self._command_atual = anterior
            self._tarefas.pop(msg.command_id, None)

    async def _resultado(self, msg: Dispatch, outcome: str, reason: str | None,
                         data: dict[str, Any] | None = None) -> None:
        corpo = Result(command_id=msg.command_id, outcome=outcome, reason=reason, data=data).model_dump()
        corpo["fence"] = msg.fence          # a cerca volta como veio: o central recusa resultado de cerca velha
        await self._send(corpo)

    # ------------------------------------------------------------------ laços
    async def _bater(self, intervalo_s: float) -> None:
        """A batida leva recursos sempre, e o inventário quando ele estiver pronto.

        Sondar seis aparelhos custa dezenas de segundos; amarrar a batida a isso faria o worker parecer morto
        justamente quando está ocupado. Então a sondagem roda à parte e a batida usa o último resultado.
        """
        sondagem: asyncio.Task[list[WorkerDevice]] | None = None
        inventario = self._declarados()
        primeira = True
        while True:
            # A primeira batida sai logo depois do `welcome`, com o estado real já a caminho.
            await asyncio.sleep(0.5 if primeira else intervalo_s)
            primeira = False
            if sondagem is None or sondagem.done():
                if sondagem is not None and not sondagem.cancelled():
                    with contextlib.suppress(Exception):
                        inventario = sondagem.result()
                sondagem = asyncio.create_task(self._inventario())
            await self._send(Heartbeat(resources=self._recursos(), devices=inventario,
                                       clock_offset_s=self._clock_offset_s).model_dump())

    async def _sessao(self) -> None:
        url = ws_url(self.settings.server)
        log.info("conectando em %s como %s", url, self.settings.worker_id)
        async with websockets.connect(url, max_size=4 * 1024 * 1024, ping_interval=20) as ws:
            self._ws = ws
            credencial = self.settings.read_credential()
            abertura: dict[str, Any] = {"hello": self._hello().model_dump()}
            if credencial:
                abertura["token"] = credencial
            elif self.enrollment:
                abertura["enrollment_token"] = self.enrollment
            else:
                raise RuntimeError("sem credencial e sem token de inscrição: rode com --enroll <token> na "
                                   "primeira vez (gere o token no painel, em Infraestrutura)")
            await ws.send(json.dumps(abertura))
            resposta = json.loads(await ws.recv())
            if resposta.get("type") == "refused":
                raise RuntimeError(f"o servidor recusou: {resposta.get('code')} — {resposta.get('message')}")
            if nova := resposta.get("credential"):
                # Só chega uma vez, na inscrição. Guardar aqui é o que dispensa o token em toda partida seguinte.
                self.settings.write_credential(nova)
                self.enrollment = None
                log.info("credencial permanente gravada em %s", self.settings.credential_path())
            intervalo = float(resposta.get("heartbeat_s") or 10.0)
            self._clock_offset_s = clock_offset_seconds(resposta.get("server_time"), now())
            if self._clock_offset_s is not None and abs(self._clock_offset_s) > 5.0:
                log.warning("relógio local desalinhado em %.1f s em relação ao central", self._clock_offset_s)
            log.info("conectado; batendo a cada %.0f s", intervalo)
            batida = asyncio.create_task(self._bater(intervalo))
            try:
                async for bruto in ws:
                    await self._receber(json.loads(bruto))
            finally:
                batida.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await batida
                for t in list(self._tarefas.values()):
                    t.cancel()
                self._ws = None

    async def _receber(self, bruto: dict[str, Any]) -> None:
        tipo = bruto.get("type")
        if tipo == "dispatch":
            msg = Dispatch.model_validate(bruto)
            # Um comando por aparelho: o mesmo aparelho não recebe duas ordens ao mesmo tempo.
            self._tarefas[msg.command_id] = asyncio.create_task(self._executar(msg))
        elif tipo == "cancel":
            if (t := self._tarefas.get(str(bruto.get("command_id")))) is not None:
                t.cancel()
        elif tipo == "refused":
            log.error("servidor recusou: %s", bruto.get("message"))

    async def run_forever(self) -> None:
        espera = RECONEXAO_MIN_S
        while True:
            try:
                await self._sessao()
                espera = RECONEXAO_MIN_S          # sessão que viveu: a próxima tentativa volta a ser rápida
            except RuntimeError as exc:
                # Recusa explicada (credencial errada, não inscrito): insistir não resolve, e o texto é para humano.
                log.error("%s", exc)
                return
            except Exception as exc:  # noqa: BLE001 - rede: tenta de novo, sempre
                log.warning("conexão caiu (%s); nova tentativa em %.0f s", exc, espera)
            await asyncio.sleep(espera)
            espera = min(RECONEXAO_MAX_S, espera * 2)
