"""O laço do agente: liga para o central, declara o que é, bate o coração e obedece comandos.

**O worker liga para o central**, nunca o contrário. Atravessa NAT sem abrir porta na casa de ninguém, que é a
restrição de partida; e reconecta sozinho, porque queda de rede não é falha de execução.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from contextvars import ContextVar
from datetime import datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

import psutil
import websockets

from ..devices.avd import capacidades_do_avd
from ..util import now, parse_iso
from ..workers.protocol import (Ack, Dispatch, Heartbeat, Hello, Progress, Result, WorkerDevice, WorkerResources)
from . import AGENT_VERSION
from .diario import DiarioDoAgente
from .executor import EFEITO_INICIADO, VERBS, VerbFailed, VerbRefused, VerbUncertain, WorkerExecutor
from .settings import WorkerSettings, host_os

#: Comando que a TAREFA atual está executando. ContextVar e não atributo: `_executar` roda como tarefa própria e
#: cada tarefa recebe uma cópia do contexto, então o progresso de um comando nunca é atribuído a outro. Com um
#: atributo único (o que havia), dois comandos simultâneos mandavam progresso com o id errado.
COMANDO_ATUAL: ContextVar[str | None] = ContextVar("comando_atual", default=None)

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
        self._tarefas: dict[str, asyncio.Task[None]] = {}
        #: Um aparelho, uma operação: `instance_id` → comando que o está ocupando. O comentário "um comando por
        #: aparelho" existia; a trava, não — dois despachos viravam duas tarefas e se intercalavam no mesmo AVD.
        self._ocupados: dict[str, str] = {}
        #: O que este agente fez e o central ainda não confirmou, mais a maior cerca por aparelho.
        self._diario = DiarioDoAgente(self.settings.work_dir)
        #: Desvio de relógio contra o central, calculado uma vez por conexão (achado #142).
        self._clock_offset_s: float | None = None

    # ------------------------------------------------------------------ declaração
    def _recursos(self) -> WorkerResources:
        vm = psutil.virtual_memory()
        disco = psutil.disk_usage(self.settings.work_dir)
        return WorkerResources(
            cpu_percent=psutil.cpu_percent(interval=None), cpu_count=psutil.cpu_count(logical=True),
            ram_total_mb=int(vm.total / (1024 * 1024)), ram_free_mb=int(vm.available / (1024 * 1024)),
            disk_free_gb=round(disco.free / (1024 ** 3), 1),
            disk_total_gb=round(disco.total / (1024 ** 3), 1))

    def _declarados(self) -> list[WorkerDevice]:
        """Só o que está na configuração e no disco: nenhuma sondagem, nenhum `adb`, nenhum tempo imprevisível."""
        return [WorkerDevice(serial=d.serial, avd_name=d.avd_name, state="unknown", adb_port=d.adb_port,
                             instance_id=d.instance_id, **self._capacidades(d)) for d in self.settings.devices]

    def _capacidades(self, spec: Any) -> dict[str, Any]:
        """Imagem, nível de API, ABI e GMS — lidos do `config.ini` do AVD, que é arquivo local e barato.

        É o que faz o pré-voo do central poder dizer "este pacote é só ARM e este aparelho não traduz" ANTES de
        agendar, em vez de descobrir no meio com `INSTALL_FAILED_NO_MATCHING_ABIS`. Aparelho que este agente não
        gere (físico, contêiner) não tem AVD: declara só o tipo, e o resto fica `None` — "não se sabe".
        """
        if not getattr(spec, "managed", True):
            return {"kind": "physical"}
        try:
            return capacidades_do_avd(self.cfg.avd_home, spec.avd_name)
        except Exception:  # noqa: BLE001 - declarar capacidade nunca pode impedir o agente de conectar
            log.exception("capacidades do AVD %s", spec.avd_name)
            return {}

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
                saida.append(WorkerDevice(serial=spec.serial, avd_name=spec.avd_name, state=estado,
                                          detail=detalhe, adb_port=spec.adb_port, instance_id=spec.instance_id,
                                          **self._capacidades(spec)))
            return saida

        return await asyncio.to_thread(sondar)

    def _hello(self) -> Hello:
        sistema, versao = host_os()
        return Hello(worker_id=self.settings.worker_id, name=self.settings.name, agent_version=AGENT_VERSION,
                     os=sistema, os_version=versao, appium_mode=self.settings.appium,
                     appium_url=self.settings.appium_url, max_slots=self.settings.max_slots,
                     verbs=list(VERBS), devices=self._declarados(), resources=self._recursos(),
                     hibernation=bool(self.cfg.file.android.hibernation),
                     inflight=list(self._tarefas))

    # ------------------------------------------------------------------ envio
    async def _send(self, payload: dict[str, Any]) -> bool:
        """`False` quando não havia canal. Quem manda coisa que não pode se perder (o desfecho) olha o retorno."""
        if self._ws is None:
            return False
        try:
            await self._ws.send(json.dumps(payload))
            return True
        except Exception as exc:  # noqa: BLE001 - socket morrendo no meio do envio é queda de rede, não falha
            log.debug("envio falhou (%s); o canal caiu", exc)
            return False

    def _progress(self, message: str) -> None:
        if (atual := COMANDO_ATUAL.get()) is not None:
            asyncio.get_running_loop().create_task(
                self._send(Progress(command_id=atual, message=message).model_dump()))

    # ------------------------------------------------------------------ comando
    async def _executar(self, msg: Dispatch) -> None:
        """ACK primeiro, resultado depois. São coisas distintas: "chegou" não é "funcionou"."""
        await self._send(Ack(command_id=msg.command_id).model_dump())
        COMANDO_ATUAL.set(msg.command_id)
        spec = self.settings.device(msg.instance_id)
        if spec is None:
            await self._resultado(msg, "failed", f"este worker não hospeda {msg.instance_id}")
            return
        try:
            # O central carimba `running` no PRIMEIRO progresso. Verbo curto (`stop`, `home`) pode não relatar
            # nada, então o começo da execução é anunciado aqui — é a hora em que o worker de fato começou.
            # `await self._send` e NÃO `self._progress`: aquele dispara uma tarefa solta, e num verbo rápido o
            # "iniciando" saía DEPOIS do resultado (medido no teste do canal falso: ack, result, progress). O
            # central então recebia progresso de um comando já encerrado e `started_at` ficava nulo. Na ordem do
            # socket: ACK → iniciado → desfecho.
            await self._send(Progress(command_id=msg.command_id,
                                      message=f"iniciando {msg.verb} em {msg.instance_id}").model_dump())
            dados = await self.executor.run(msg.verb, spec, msg.params)
            await self._resultado(msg, "succeeded", None, dados)
        except VerbRefused as exc:
            # Recusa antes de agir: o central pode afirmar que o aparelho ficou intacto.
            await self._resultado(msg, "failed", str(exc))
        except VerbFailed as exc:
            # Agiu, e o desfecho conhecido é negativo (hibernar sem snapshot). Não é recusa: o aparelho mudou.
            # `exc.dados` leva junto o que explica a falha — hoje a cauda do log do emulador, que mora nesta
            # máquina e sem isto nunca chegaria a quem opera o painel.
            await self._resultado(msg, "failed", str(exc), exc.dados)
        except VerbUncertain as exc:
            await self._resultado(msg, "uncertain", str(exc), exc.dados)
        except asyncio.CancelledError:
            # `cancelled` é cancelamento CONFIRMADO (013_commands.sql:25): só pode ser dito quando o aparelho
            # ficou intacto. Se o verbo já tinha tocado nele — AVD sendo criado, emulador iniciado, snapshot
            # sendo salvo —, a espera foi abandonada mas a thread continua agindo, e a verdade é `uncertain`.
            if (efeito := EFEITO_INICIADO.get()) is None:
                await self._resultado(msg, "cancelled",
                                      "cancelado a pedido do servidor antes de tocar no aparelho")
            else:
                await self._resultado(msg, "uncertain",
                                      f"cancelado a pedido do servidor, mas {efeito}; o efeito é desconhecido")
            raise
        except Exception as exc:  # noqa: BLE001 - agiu e quebrou no meio: não se sabe o efeito
            log.exception("comando %s (%s)", msg.command_id, msg.verb)
            await self._resultado(msg, "uncertain", f"erro inesperado no worker: {exc}")
        finally:
            self._tarefas.pop(msg.command_id, None)
            if self._ocupados.get(msg.instance_id) == msg.command_id:
                self._ocupados.pop(msg.instance_id, None)

    async def _resultado(self, msg: Dispatch, outcome: str, reason: str | None,
                         data: dict[str, Any] | None = None) -> None:
        """Grava no diário ANTES de tentar enviar. É a ordem que importa: se o canal estiver caído (ou cair no
        meio do envio), o desfecho continua existindo em disco e sai na reconexão, em vez de sumir com o socket.
        """
        # A cerca volta como veio: o central recusa resultado de cerca velha — e agora também o sem cerca.
        corpo = Result(command_id=msg.command_id, outcome=outcome, reason=reason, data=data,
                       fence=msg.fence).model_dump()
        self._diario.guardar(msg.command_id, corpo)
        await self._send(corpo)         # a confirmação (`result_ack`) é que apaga do diário, não o envio

    async def _recusar(self, msg: Dispatch, motivo: str) -> None:
        """Recebido e recusado SEM tocar no aparelho — o central pode afirmar que nada aconteceu."""
        await self._send(Ack(command_id=msg.command_id).model_dump())
        await self._resultado(msg, "failed", motivo)

    async def _reenviar_pendentes(self) -> None:
        """Tudo o que o diário guarda volta a sair assim que há canal. Reenvio é seguro: o central trata o
        resultado tardio por `command_id` e confirma mesmo quando já não há nada a mudar."""
        for corpo in self._diario.pendentes():
            log.info("reenviando o desfecho de %s guardado no diário", corpo.get("command_id"))
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
        # `ssl=` só é diferente de `None` em `wss://`. Sem ele, um central com certificado de CA própria — o
        # caso de um parque doméstico — seria recusado pela verificação padrão, e a mensagem não diria o que
        # fazer. O que NÃO existe aqui é uma opção de ignorar a verificação: ela seria o próprio ataque.
        async with websockets.connect(url, max_size=4 * 1024 * 1024, ping_interval=20,
                                      ssl=self.settings.ssl_context()) as ws:
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
            # Canal de pé: o que ficou no diário desde a última queda sai agora.
            await self._reenviar_pendentes()
            try:
                async for bruto in ws:
                    await self._receber(json.loads(bruto))
            finally:
                batida.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await batida
                # As TAREFAS NÃO SÃO CANCELADAS. Era isto que fazia a queda do canal virar falha de execução: um
                # `reset` parava entre o stop e o start, um `start` abandonava a espera de boot sem
                # `prepare_for_automation`. Agora o verbo termina, o desfecho vai para o diário, e sai na
                # reconexão. Perder a rede não é perder o trabalho.
                self._ws = None

    async def _receber(self, bruto: dict[str, Any]) -> None:
        tipo = bruto.get("type")
        if tipo == "dispatch":
            await self._despachar(Dispatch.model_validate(bruto))
        elif tipo == "cancel":
            if (t := self._tarefas.get(str(bruto.get("command_id")))) is not None:
                t.cancel()
        elif tipo == "result_ack":
            self._diario.confirmar(str(bruto.get("command_id")))
        elif tipo == "refused":
            log.error("servidor recusou: %s", bruto.get("message"))

    async def _despachar(self, msg: Dispatch) -> None:
        """As três guardas que faltavam, na ordem em que custam menos.

        1. **Reentrega.** Comando que já está rodando aqui, ou cujo desfecho está no diário, não é executado de
           novo: é a mesma ordem chegando duas vezes depois de uma reconexão.
        2. **Cerca.** Despacho com cerca MENOR que a maior já executada naquele aparelho é uma ordem que voltou
           do limbo; o recurso é quem a recusa, que é para isso que a cerca existe.
        3. **Um aparelho, uma operação.** Enquanto um comando ocupa o aparelho, o próximo é recusado — sem
           tocar em nada, então o central pode afirmar que o aparelho ficou intacto.
        """
        if msg.command_id in self._tarefas:
            log.info("comando %s já está em execução aqui; reentrega ignorada", msg.command_id)
            return
        if (guardado := self._diario.resultados.get(msg.command_id)) is not None:
            await self._send(guardado)          # já executado: devolve o mesmo desfecho, não age de novo
            return
        maior = self._diario.cerca(msg.instance_id)
        if msg.fence < maior:
            await self._recusar(msg, f"cerca {msg.fence} é anterior à última executada neste aparelho ({maior}); "
                                     "ordem vencida, nada foi executado")
            return
        if (dono := self._ocupados.get(msg.instance_id)) is not None:
            await self._recusar(msg, f"{msg.instance_id} já está executando o comando {dono} neste worker; "
                                     "nada foi executado")
            return
        self._diario.registrar_cerca(msg.instance_id, msg.fence)
        self._ocupados[msg.instance_id] = msg.command_id
        self._tarefas[msg.command_id] = asyncio.create_task(self._executar(msg))

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
