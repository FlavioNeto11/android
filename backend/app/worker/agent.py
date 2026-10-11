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
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import psutil
import websockets

from ..contracts.worker.protocol import (FECHAMENTO_MOTIVO_DEFASADO, FEATURE_COMANDO_REMOTO, FEATURE_OBSERVACAO_LOCAL,
                                         FEATURE_RESERVA_DE_BOOT,
                                         RECUSA_CERCA_NAO_MAIOR,
                                         Ack, Dispatch, Exec, Heartbeat, Hello, Limits, ObserveImage, ObserveResult,
                                         Progress, Result, WorkerDevice, WorkerResources)
from ..contracts.worker.verbos import sem_hibernacao
from ..devices.avd import capacidades_do_avd
from ..util import now, now_iso, parse_iso
from . import AGENT_CODE, AGENT_VERSION
from .comando import ComandoRemoto
from .diario import DiarioDoAgente
from .executor import EFEITO_INICIADO, VERBS, VerbFailed, VerbRefused, VerbUncertain, WorkerExecutor
from .observacao import FEATURES_DE_OBSERVACAO, ObservacaoNaOrigem
from .settings import KVM, DeviceSpec, WorkerSettings, aceleracao_do_host, host_os

#: Comando que a TAREFA atual está executando. ContextVar e não atributo: `_executar` roda como tarefa própria e
#: cada tarefa recebe uma cópia do contexto, então o progresso de um comando nunca é atribuído a outro. Com um
#: atributo único (o que havia), dois comandos simultâneos mandavam progresso com o id errado.
COMANDO_ATUAL: ContextVar[str | None] = ContextVar("comando_atual", default=None)

log = logging.getLogger("poc.worker")

#: O que este agente implementa (`Hello.features`, C7). `boot_reservations`: a guarda de RAM do boot reserva o
#: custo do aparelho antes de subir o emulador (`executor.ReservasDeRam`) e a batida declara o total em
#: `reserved_mb` — então, deste agente, `reserved_mb: 0` quer dizer zero, e não "não se sabe". `observe_local`
#: (`worker/observacao.py`): a imagem da tela é capturada e codificada NESTA máquina — só com Pillow no venv.
FEATURES = (FEATURE_RESERVA_DE_BOOT, *FEATURES_DE_OBSERVACAO)

#: Espera entre tentativas de reconexão. Cresce até um teto: martelar o central não ajuda ninguém.
RECONEXAO_MIN_S = 2.0
RECONEXAO_MAX_S = 60.0
#: Item 29.73: o central que AVISA que está reiniciando (fechamento 1012 "service restart", ou 1001 "going away")
#: volta em segundos ou num minuto (o deploy com backup). Com a escada de sempre, a tentativa de 32 s caía antes da
#: volta e a seguinte só vinha 60 s depois: o worker ficava até um minuto fora depois do central de pé (deploy 28,
#: 04/10). Nessa janela a espera fica curta e fixa; passada ela, volta à escada.
RECONEXAO_RAJADA_S = 3.0
RECONEXAO_RAJADA_JANELA_S = 180.0
FECHAMENTO_DE_REINICIO = frozenset({1001, 1012})
#: 29.76: o central fecha com 4409 o canal ANTIGO quando outra conexão deste mesmo worker assume
#: (`workers/registry.py::attach`). Um processo só tem um canal por vez, então receber o 4409 quer dizer que há outro
#: agente com o mesmo `worker_id` (partida manual mais a tarefa, processo velho que sobrou de uma atualização).
#: Tentar de novo faria os dois se derrubarem a cada espera, e na janela curta do 29.73 isso seria a cada 3 s. Sair
#: também não resolve: a tarefa `farm-agente` religa o processo em 1 min (RestartCount 999), e os dois lançadores se
#: derrubariam a cada minuto (revisão do #303). Este agente cede o canal e só tenta de novo depois de
#: `ESPERA_NO_CONFLITO_S`: se o outro sumiu (o processo velho morreu), volta sozinho; se não, a troca vira uma a cada
#: 10 min, com o motivo no log. A recusa de credencial por fechamento (4401/4403) segue na escada: parar o worker
#: remoto por um erro passageiro do central custa mais do que insistir.
FECHAMENTO_DE_CONFLITO = 4409
ESPERA_NO_CONFLITO_S = 600.0
#: Validade da marca `agente-cedido.json` (29.76): passado isso, o agente marcado volta a tentar.
VALIDADE_DA_MARCA_S = 24 * 3600.0


def motivo_do_fechamento(exc: BaseException) -> str:
    rcvd = getattr(exc, "rcvd", None)
    return str(getattr(rcvd, "reason", "") or "")


def codigo_do_fechamento(exc: BaseException) -> int | None:
    """O código do fechamento que o CENTRAL mandou (`ConnectionClosed.rcvd`); `None` quando não houve fechamento
    recebido (queda de rede, recusa de conexão)."""
    rcvd = getattr(exc, "rcvd", None)
    codigo = getattr(rcvd, "code", None)
    return int(codigo) if isinstance(codigo, int) else None


class EsperaDeReconexao:
    """Quanto esperar antes da próxima tentativa. Puro (o relógio vem de fora), para o teste não depender de rede."""

    def __init__(self) -> None:
        self.espera = RECONEXAO_MIN_S
        self.rajada_ate = 0.0

    def sessao_viveu(self) -> None:
        self.espera = RECONEXAO_MIN_S

    def depois_da_queda(self, exc: BaseException, agora: float) -> float:
        """A espera desta vez. O fechamento de reinício abre a janela curta; dentro dela a espera não cresce."""
        if codigo_do_fechamento(exc) in FECHAMENTO_DE_REINICIO:
            self.rajada_ate = agora + RECONEXAO_RAJADA_JANELA_S
        if agora < self.rajada_ate:
            return RECONEXAO_RAJADA_S
        esta = self.espera
        self.espera = min(RECONEXAO_MAX_S, self.espera * 2)
        return esta


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
        #: Das `FEATURES` anunciadas, as que o central aceitou NESTA conexão (`Welcome.accepted_features`, C7).
        #: Vazio com central antigo. Nesta onda nada muda de comportamento por ela: é o lugar em que a próxima
        #: onda pergunta antes de mandar mensagem nova.
        self.features_aceitas: set[str] = set()
        #: Observação na origem (`observe_local`): uma tarefa por pedido, guardada para não ser coletada no meio.
        self.observacao = ObservacaoNaOrigem(settings, adb_de=self.executor.adb_for, enviar=self._send)
        self._observacoes: set[asyncio.Task[Any]] = set()
        #: Comando remoto (`remote_exec`, 29.154): desligado de fábrica; as tarefas ficam guardadas para o laço de
        #: recepção não esperar um comando e para ninguém coletá-las no meio.
        self.comando = ComandoRemoto(settings.work_dir, habilitado=settings.comando_remoto, enviar=self._send,
                                     aceitas=lambda: self.features_aceitas)
        self._comandos: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------ declaração
    def _recursos(self) -> WorkerResources:
        """Recursos EFETIVOS (adendo v0.20, C6): o limite do cgroup quando há, a pressão de memória, e a RAM já
        prometida a boots em andamento (`reserved_mb`) — o que a memória disponível ainda não mostra.

        Pela MESMA medição que a guarda do boot usa (`executor.medir_recursos`): o central decide com o número
        que o agente decide, e não com um segundo número lido de outro jeito."""
        rec = self.executor.medir_recursos()
        disco = psutil.disk_usage(self.settings.work_dir)
        return WorkerResources(
            cpu_percent=psutil.cpu_percent(interval=None), cpu_count=rec.cpu_count,
            ram_total_mb=rec.ram_total_mb, ram_free_mb=rec.ram_free_mb,
            disk_free_gb=round(disco.free / (1024 ** 3), 1),
            disk_total_gb=round(disco.total / (1024 ** 3), 1),
            mem_limit_mb=rec.mem_limit_mb, mem_available_mb=rec.mem_available_mb, cpu_effective=rec.cpu_effective,
            swap_used_pct=rec.swap_used_pct, mem_pressure=rec.mem_pressure,
            reserved_mb=self.executor.reservas.total_mb(), measured_at=rec.measured_at)

    def _declarados(self) -> list[WorkerDevice]:
        """Só o que está na configuração e no disco: nenhuma sondagem, nenhum `adb`, nenhum tempo imprevisível."""
        return [WorkerDevice(serial=d.serial, avd_name=d.avd_name, state="unknown", adb_port=d.adb_port,
                             instance_id=d.instance_id, **self._capacidades(d), **self._renderizador(d, no_ar=False))
                for d in self.settings.devices]

    def _renderizador(self, spec: DeviceSpec, *, no_ar: bool) -> dict[str, str | None]:
        """O renderizador do emulador deste aparelho (29.11): o `gpu_mode` pedido e, com ele no ar, o que o emulador
        SELECIONOU — lido do log desta máquina, que o central não alcança. No `hello` vai só o pedido (`no_ar=False`:
        nenhuma sondagem ali); o selecionado chega na batida. Aparelho não gerido não é emulador deste agente."""
        if not spec.managed:
            return {}
        try:
            return self.executor.renderizador(spec, self.executor.pid_do_avd(spec.avd_name) if no_ar else None)
        except Exception:  # noqa: BLE001 - declarar o renderizador nunca pode derrubar o `hello` nem a batida
            log.exception("renderizador do AVD %s", spec.avd_name)
            return {}

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
                                          **self._capacidades(spec),
                                          **self._renderizador(spec, no_ar=estado == "running")))
            return saida

        return await asyncio.to_thread(sondar)

    def _hello(self) -> Hello:
        sistema, versao = host_os()
        aceleracao = aceleracao_do_host()
        if aceleracao is not None and aceleracao != KVM:
            # Alto de propósito: sem KVM o emulador não sobe (ou sobe em emulação de software, e um boot de 2 min
            # vira dezenas). Avisar aqui é o que põe a causa no log do worker antes do primeiro `start` estourar
            # prazo do outro lado da rede.
            log.warning("sem aceleração utilizável nesta máquina (%s): o emulador não vai subir em tempo útil. "
                        "Confira /dev/kvm e se a conta que roda o agente está no grupo 'kvm'.", aceleracao)
        return Hello(worker_id=self.settings.worker_id, name=self.settings.name, agent_version=AGENT_VERSION,
                     agent_code=AGENT_CODE, os=sistema, os_version=versao, accel=aceleracao, appium_mode=self.settings.appium,
                     appium_url=self.settings.appium_url,
                     # Declara o do ARQUIVO, não o efetivo: o central guarda a decisão do dono à parte e precisa
                     # saber para onde "voltar ao da máquina" retorna.
                     max_slots=self.executor.do_arquivo["max_slots"],
                     boot_parallelism=self.executor.do_arquivo["boot_parallelism"],
                     min_free_ram_mb=self.executor.do_arquivo["min_free_ram_mb"],
                     verbs=sem_hibernacao(VERBS, bool(self.cfg.file.android.hibernation)),
                     devices=self._declarados(), resources=self._recursos(),
                     hibernation=bool(self.cfg.file.android.hibernation),
                     inflight=list(self._tarefas),
                     # A maior cerca executada por aparelho: é o que deixa o central se recuperar sozinho de um
                     # banco restaurado, em vez de emitir cerca que este agente recusaria (K-004).
                     fences=dict(self._diario.cercas),
                     # O que este agente implementa e confere (C7). O central só usa o que ACEITAR no `welcome`.
                     features=list(self._features()))

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

    async def _recusar(self, msg: Dispatch, motivo: str, dados: dict[str, Any] | None = None) -> None:
        """Recebido e recusado SEM tocar no aparelho — o central pode afirmar que nada aconteceu."""
        await self._send(Ack(command_id=msg.command_id).model_dump())
        await self._resultado(msg, "failed", motivo, dados)

    async def _reenviar_pendentes(self) -> None:
        """Tudo o que o diário guarda volta a sair assim que há canal. Reenvio é seguro: o central trata o
        resultado tardio por `command_id` e confirma mesmo quando já não há nada a mudar."""
        for corpo in self._diario.pendentes():
            log.info("reenviando o desfecho de %s guardado no diário", corpo.get("command_id"))
            await self._send(corpo)
        for corpo in self.comando.pendentes():
            log.info("reenviando o desfecho do comando remoto %s", corpo.get("exec_id"))
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
            # As contagens do executor vão como delta; batida que não saiu as DEVOLVE, para a próxima levar.
            contagens = self.executor.tirar_contagens()
            enviada = await self._send(Heartbeat(resources=self._recursos(), devices=inventario,
                                                 clock_offset_s=self._clock_offset_s,
                                                 # relógio local na saída: o central re-mede o desvio a cada
                                                 # batida (central antigo ignora e usa o `clock_offset_s`)
                                                 sent_at=now_iso(),
                                                 metricas=contagens).model_dump())
            if not enviada:
                self.executor.devolver_contagens(contagens)

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
            # Central antigo não manda a chave: nada aceito, e o agente segue o caminho anterior (C7). Só o que
            # este agente ANUNCIOU entra — o central não liga, por aqui, o que o agente não sabe fazer.
            aceitas = resposta.get("accepted_features")
            self.features_aceitas = ({str(f) for f in aceitas if str(f) in self._features()}
                                     if isinstance(aceitas, list) else set())
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
                # `gather(return_exceptions=True)` e não `suppress(CancelledError)`: este deixa o cancelamento do PRÓPRIO agente, se chegar
                # enquanto espera a batida morrer (a sessão acabou de cair), ser engolido, e o agente seguia reconectando para sempre
                # (a suíte inteira sob carga, 11/10: `_encerrar` estourou os 10 s). Aqui o cancelamento de fora sobe; o da batida, que é
                # o esperado, vira só um valor.
                await asyncio.gather(batida, return_exceptions=True)
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
        elif tipo == "observe_image":
            self._observar(bruto)
        elif tipo == "exec":
            self._comandar(bruto)
        elif tipo == "exec_cancel":
            self.comando.cancelar(str(bruto.get("exec_id")))
        elif tipo == "exec_result_ack":
            self.comando.confirmar(str(bruto.get("exec_id")))
        elif tipo == "limits":
            msg = Limits.model_validate(bruto)
            efetivo = await self.executor.aplicar_limites(msg.max_slots, msg.boot_parallelism, msg.min_free_ram_mb)
            log.info("limites do painel aplicados: %d vaga(s), %d boot(s) por vez, piso de RAM %d MB",
                     efetivo["max_slots"], efetivo["boot_parallelism"], efetivo["min_free_ram_mb"])

    def _features(self) -> tuple[str, ...]:
        """O que este agente anuncia. `remote_exec` só com `comando_remoto: true` no `worker.yaml`: sem isso o
        central nem sabe que existe, e o comando remoto fica fora do alcance mesmo com o interruptor dele ligado."""
        return (*FEATURES, FEATURE_COMANDO_REMOTO) if self.settings.comando_remoto else FEATURES

    def _comandar(self, bruto: dict[str, object]) -> None:
        """Pedido de comando remoto. Roda à parte, como a observação: o laço de recepção não espera um comando."""
        try:
            msg = Exec.model_validate(bruto)
        except Exception:  # noqa: BLE001 - pedido malformado não derruba o canal (e a linha crua não vai ao log)
            log.warning("pedido de comando remoto inválido (%s)", str(bruto.get("exec_id"))[:64])
            return
        tarefa = asyncio.create_task(self.comando.atender(msg))
        self._comandos.add(tarefa)
        tarefa.add_done_callback(self._comandos.discard)

    def _observar(self, bruto: dict[str, Any]) -> None:
        """Pedido de imagem (`observe_local`). Roda à parte: o laço de recepção não espera um screencap, e o
        verbo em execução não espera a imagem — a exclusividade do aparelho é do executor do CENTRAL, que só pede
        a imagem dentro dele."""
        try:
            msg = ObserveImage.model_validate(bruto)
        except Exception:  # noqa: BLE001 - pedido malformado não derruba o canal
            log.warning("pedido de observação inválido: %s", str(bruto)[:200])
            return
        if FEATURE_OBSERVACAO_LOCAL not in self.features_aceitas:
            # Fora do contrato (C7): o central só pede a quem aceitou. Responde a falha em vez de ficar calado,
            # para quem pediu não esperar o prazo inteiro.
            resposta = ObserveResult(request_id=msg.request_id, ok=False,
                                     error="observe_local não foi negociado nesta conexão").model_dump()
            tarefa = asyncio.create_task(self._send(resposta))
        else:
            tarefa = asyncio.create_task(self.observacao.atender(msg))
        self._observacoes.add(tarefa)
        tarefa.add_done_callback(self._observacoes.discard)

    async def _despachar(self, msg: Dispatch) -> None:
        """As três guardas que faltavam, na ordem em que custam menos.

        1. **Reentrega.** Comando que já está rodando aqui, ou cujo desfecho está no diário (pendente OU já
           confirmado), não é executado de novo: é a mesma ordem chegando duas vezes — reconexão, `ack_wait` do
           broker, central que despachou duas vezes. Vai o MESMO desfecho, que é a verdade sobre aquele comando.
        2. **Cerca.** A cerca é estritamente crescente por aparelho, então despacho com cerca que NÃO é maior que
           a última executada é ordem vencida ou reentrega de um comando cujo desfecho já saiu do diário. Era
           `<`: a reentrega do próprio último comando (mesma cerca) depois do `result_ack` passava e o verbo
           rodava de novo. O recurso é quem recusa, que é para isso que a cerca existe.
        3. **Um aparelho, uma operação.** Enquanto um comando ocupa o aparelho, o próximo é recusado — sem
           tocar em nada, então o central pode afirmar que o aparelho ficou intacto.
        """
        if msg.command_id in self._tarefas:
            log.info("comando %s já está em execução aqui; reentrega ignorada", msg.command_id)
            return
        if (guardado := self._diario.desfecho(msg.command_id)) is not None:
            await self._send(guardado)          # já executado: devolve o mesmo desfecho, não age de novo
            return
        maior = self._diario.cerca(msg.instance_id)
        if msg.fence <= maior:
            # `failed` porque é a verdade sobre ESTE despacho (nada foi executado agora), e a marca diz ao
            # central novo que não é o desfecho de um efeito; o antigo a lê como a recusa de sempre.
            motivo = (f"cerca {msg.fence} é anterior à última executada neste aparelho ({maior}); ordem vencida"
                      if msg.fence < maior else
                      f"cerca {msg.fence} já foi executada neste aparelho; reentrega de um comando já concluído "
                      "ou ordem duplicada")
            await self._recusar(msg, f"{motivo}, nada foi executado",
                                {"refused": RECUSA_CERCA_NAO_MAIOR, "last_fence": maior})
            return
        if (dono := self._ocupados.get(msg.instance_id)) is not None:
            await self._recusar(msg, f"{msg.instance_id} já está executando o comando {dono} neste worker; "
                                     "nada foi executado")
            return
        self._diario.registrar_cerca(msg.instance_id, msg.fence)
        self._ocupados[msg.instance_id] = msg.command_id
        tarefa = asyncio.create_task(self._executar(msg))
        self._tarefas[msg.command_id] = tarefa
        tarefa.add_done_callback(lambda t: self._ao_terminar(msg, t))

    def _ao_terminar(self, msg: Dispatch, tarefa: asyncio.Task[None]) -> None:
        """Rede de segurança do 29.76 (revisão do #303): o cancelamento que chega ANTES do `try` de `_executar` (tarefa
        recém-criada que nem começou, ou o `await` do Ack) não grava desfecho nem roda o `finally`. Sem isto, o
        comando ficava em `_tarefas`/`_ocupados` para sempre: o Hello o declarava em voo e todo comando novo àquele
        aparelho era recusado até o processo reiniciar. Nada foi tocado nesse ponto, então o desfecho é `cancelled`."""
        self._tarefas.pop(msg.command_id, None)
        if self._ocupados.get(msg.instance_id) == msg.command_id:
            self._ocupados.pop(msg.instance_id, None)
        if tarefa.cancelled() and self._diario.desfecho(msg.command_id) is None:
            corpo = Result(command_id=msg.command_id, outcome="cancelled", fence=msg.fence,
                           reason="cancelado antes de começar; nada foi tocado no aparelho").model_dump()
            self._diario.guardar(msg.command_id, corpo)
            if self._ws is not None:                 # sem canal, sai na volta (`_reenviar_pendentes`)
                with contextlib.suppress(RuntimeError):
                    asyncio.get_running_loop().create_task(self._send(corpo))

    async def _ceder_o_canal(self) -> None:
        """29.76: quem perdeu o canal não age em aparelho. O que estava em voo é cancelado; cada verbo para no próximo
        `ponto_seguro` e o desfecho (`cancelled` antes de tocar, `uncertain` depois) vai para o diário, que sai na
        volta com a cerca dele. A reserva de RAM segue a regra do executor: solta se nada foi iniciado; órfã até o
        processo sumir se o emulador já subiu (`executor._v_start`). Uma thread de `to_thread` que já começou não para
        no meio (nenhuma para): o que ela faz é o último passo, e o desfecho diz `uncertain`."""
        tarefas = list(self._tarefas.values())
        for t in tarefas:
            t.cancel()
        if tarefas:
            await asyncio.gather(*tarefas, return_exceptions=True)

    def _marca_de_cedido(self) -> Path:
        return Path(self.settings.work_dir) / "agente-cedido.json"

    def _marcar_cedido(self) -> None:
        with contextlib.suppress(OSError):
            self._marca_de_cedido().write_text(json.dumps({"agent_code": AGENT_CODE, "em": now_iso()}), encoding="utf-8")

    def _cedido_antes(self) -> bool:
        """A marca vale só para o MESMO código e por `VALIDADE_DA_MARCA_S` (24 h): a atualização troca o código, e o
        `worker-install` apaga a marca. A validade cobre o que a marca não sabe distinguir (revisão do #303): um
        rollback que devolve o mesmo código marcado, ou o agente da tarefa deslocado por um agente manual de código
        novo que depois foi fechado; nos dois, o worker ficaria sem agente para sempre."""
        try:
            marca = json.loads(self._marca_de_cedido().read_text(encoding="utf-8"))
            quando = parse_iso(str(marca.get("em") or ""))
        except (OSError, ValueError, AttributeError, TypeError):
            return False
        if marca.get("agent_code") != AGENT_CODE or quando is None:
            return False
        # Idade negativa = marca "do futuro" (relógio que andou para trás): não vale, senão barraria o agente por 24 h
        # mais o erro do relógio.
        return 0 <= (now() - quando).total_seconds() < VALIDADE_DA_MARCA_S

    async def run_forever(self) -> None:
        if AGENT_CODE and self._cedido_antes():
            log.error("este código de agente (%s) já cedeu o canal a um agente com o código do central: não conecta "
                      "(apague %s depois de conferir que não há outro agente, ou atualize)", AGENT_CODE,
                      self._marca_de_cedido())
            return
        reconexao = EsperaDeReconexao()
        while True:
            espera = RECONEXAO_MIN_S
            try:
                await self._sessao()
                reconexao.sessao_viveu()          # sessão que viveu: a próxima tentativa volta a ser rápida
            except RuntimeError as exc:
                # Recusa explicada (credencial errada, não inscrito): insistir não resolve, e o texto é para humano.
                log.error("%s", exc)
                return
            except Exception as exc:  # noqa: BLE001 - rede: tenta de novo, sempre
                if codigo_do_fechamento(exc) == FECHAMENTO_DE_CONFLITO:
                    await self._ceder_o_canal()
                    if motivo_do_fechamento(exc) == FECHAMENTO_MOTIVO_DEFASADO:
                        self._marcar_cedido()
                        log.error("outra conexão deste worker assumiu o canal (4409) com o código do central, e este "
                                  "agente roda código antigo (%s): sai e não volta até ser atualizado", AGENT_CODE)
                        return
                    espera = ESPERA_NO_CONFLITO_S
                    log.error("outra conexão deste worker assumiu o canal (4409): há outro agente com o mesmo "
                              "worker_id; este cancelou o que tinha em voo, cede o canal e tenta de novo em %.0f s "
                              "(sair não adianta: a tarefa farm-agente religaria em 1 min)", espera)
                else:
                    espera = reconexao.depois_da_queda(exc, asyncio.get_running_loop().time())
                    log.warning("conexão caiu (%s); nova tentativa em %.0f s", exc, espera)
            await asyncio.sleep(espera)
