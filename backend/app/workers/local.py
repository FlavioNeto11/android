"""O central como worker: `LocalWorker`.

O plano previa UM contrato para workers locais e remotos, e até aqui havia dois. `api._do_action` chamava o
`DeviceManager` direto e `_do_action_no_worker` falava `Dispatch`/`Ack`/`Result` — duas semânticas para o mesmo
verbo, e a divergência já tinha produzido defeito visível (`desired_state` gravado só num dos lados, `start`
local respondendo `succeeded` antes de o aparelho ligar).

O que esta classe faz: embrulha o `DeviceManager` atrás do MESMO contrato. Ela se registra na tabela `workers`
(id = `OWNER_ID`), instala um `WorkerLink` em processo no `WorkerRegistry` e responde `ack` → `progress` →
`result` pelo mesmo caminho de entrada das mensagens do agente remoto (`api._tratar_mensagem_do_worker`). Quem
despacha não sabe — nem precisa saber — se o aparelho mora aqui ou no notebook.

**O que NÃO foi feito, de propósito.** `worker/executor.py` continua sendo o executor do AGENTE; ele não virou o
núcleo do caminho local. Trocar o `DeviceManager` por ele levaria junto monitor, Appium, rodízio, `desired_state`
e readoção, que são do central e não existem no agente. O que a fase pedia — uma implementação só do PONTO DE
VISTA de quem despacha — está feito: há um `dispatch` só, um `fence` só, um prazo só, um mapa de desfechos só.

**Credencial.** A linha do central em `workers` guarda o hash de um segredo aleatório que nunca é revelado a
ninguém: não há como um agente de fora se autenticar como o worker local. Remoção e rotação de credencial são
recusadas pelo registro (`registry.remove`/`rotate_credential`).
"""
from __future__ import annotations

import asyncio
import logging
import platform
import secrets
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import psutil

from ..automation.driver import DriverError, DriverTimeout
from ..db import dumps
from ..devices.adb import AdbError, AdbTimeout
from ..devices import emulator as emu
from ..devices.avd import AvdError
from ..devices.manager import DeviceRuntime, InstanceBusy
from ..devices.verbs import CICLO_DE_VIDA, VERBOS_QUE_ESPERAM_O_BOOT, prazo_de
from ..models import CommandState, InstanceState
from ..util import now_iso
from .protocol import PROTOCOL_VERSION, Ack, Dispatch, Heartbeat, Progress, Result, WorkerResources
from .registry import _hash

if TYPE_CHECKING:  # pragma: no cover - só para o verificador de tipos
    from ..state import AppState

log = logging.getLogger("poc.workers.local")

#: Os verbos que ESTA máquina executa, na mesma forma em que o agente os declara no `Hello`. Ordenado para a
#: linha do banco não mudar de conteúdo a cada subida só por causa da ordem de um `set`.
VERBOS = tuple(sorted(CICLO_DE_VIDA))


class LocalWorker:
    """O executor local visto pelo contrato de worker."""

    def __init__(self, state: "AppState") -> None:
        self.s = state
        self.worker_id = state.cfg.owner_id
        self.link: Any = None
        #: Comandos em execução aqui: id → tarefa. É o que o `cancel` do contrato encontra.
        self.tarefas: dict[str, asyncio.Task[None]] = {}
        #: Entrada das mensagens deste worker. É o MESMO handler do agente remoto — injetado em `conectar()`
        #: para o pacote `workers` não depender da API (o import é no sentido contrário).
        self.upstream: Callable[[Any], Awaitable[None]] | None = None

    # ------------------------------------------------------------------ registro
    def _recursos(self) -> WorkerResources:
        vm = psutil.virtual_memory()
        try:
            uso = psutil.disk_usage(str(self.s.cfg.data_dir))
            livre: float | None = uso.free / 2**30
            total: float | None = uso.total / 2**30
        except OSError:
            livre = total = None
        return WorkerResources(cpu_percent=psutil.cpu_percent(interval=None), cpu_count=psutil.cpu_count(),
                               ram_total_mb=int(vm.total / 2**20), ram_free_mb=int(vm.available / 2**20),
                               disk_free_gb=round(livre, 1) if livre is not None else None,
                               disk_total_gb=round(total, 1) if total is not None else None)

    def _instancias_locais(self) -> list[DeviceRuntime]:
        """Os aparelhos que ESTA máquina hospeda.

        `external` fica de fora de propósito: o aparelho de outra máquina sem agente é alcançado por ADB pelo
        túnel, mas o processo dele não é nosso — declarar ciclo de vida sobre ele devolveria ao painel os botões
        que `verbs.EXTERNO_SEM_WORKER` existe para tirar.
        """
        return [rt for rt in self.s.devices.devices.values() if not rt.external]

    def registrar(self) -> None:
        """Grava (ou atualiza) a linha do central em `workers`. Idempotente: o segredo só nasce uma vez."""
        # A MESMA versão que o agente declara (`app/version.py`): é ela que o registro compara para dizer
        # "agente defasado", e o central não pode aparecer defasado em relação a si mesmo.
        from ..version import agent_version  # noqa: PLC0415 - import tardio: nada de ciclo na subida

        agora = now_iso()
        db = self.s.db
        existente = db.one("SELECT token_hash, enrolled_at FROM workers WHERE id=?", (self.worker_id,))
        # Segredo que nunca sai daqui: a coluna é NOT NULL e o registro compara credencial com ela, então um
        # valor aleatório e não revelado é o que torna impossível alguém de fora se apresentar como este worker.
        token_hash = existente["token_hash"] if existente else _hash(secrets.token_urlsafe(32))
        nome = f"{self.worker_id} (este servidor)"
        db.execute(
            "INSERT INTO workers(id, name, os, os_version, agent_version, protocol, appium_mode, appium_url,"
            " max_slots, verbs, state, state_detail, resources, devices, enrolled_at, last_seen_at, token_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET name=excluded.name, os=excluded.os, os_version=excluded.os_version,"
            " agent_version=excluded.agent_version, protocol=excluded.protocol, appium_mode=excluded.appium_mode,"
            " max_slots=excluded.max_slots, verbs=excluded.verbs, state=excluded.state, state_detail=NULL,"
            " resources=excluded.resources, last_seen_at=excluded.last_seen_at",
            (self.worker_id, nome, platform.system().lower(), platform.release(), agent_version(), PROTOCOL_VERSION,
             "central", None, self._max_slots(), dumps(list(VERBOS)), "online", None,
             dumps(self._recursos().model_dump()), None, existente["enrolled_at"] if existente else agora,
             agora, token_hash))

    def _max_slots(self) -> int:
        """Quantos aparelhos este servidor aceita manter ligados. É o mesmo teto que o rodízio já respeita."""
        return max(1, int(self.s.settings.get().max_online_devices or 1))

    # ------------------------------------------------------------------ conexão
    async def conectar(self) -> None:
        """Instala o canal em processo e amarra os aparelhos locais a este worker."""
        from ..api import _tratar_mensagem_do_worker  # noqa: PLC0415 - a API importa `state`; o sentido é este

        self.registrar()
        link = self.s.workers.attach(self.worker_id, self._send)
        self.link = link
        self.upstream = lambda msg: _tratar_mensagem_do_worker(self.s, self.worker_id, link, msg)
        # Declaração de capacidade, no mesmo lugar em que a do agente remoto é guardada: o pré-voo pergunta ao
        # registro, não ao `config.yaml`. Aqui a hibernação é por INSTÂNCIA (`android.hibernation` aceita
        # sobreposição), então o worker declara "sei hibernar" quando alguma instância local a tem ligada, e a
        # recusa fina continua sendo feita por aparelho em `api._precheck`.
        self.s.workers.hibernacao[self.worker_id] = any(
            bool(self.s.cfg.instance_android(rt.id).hibernation) for rt in self._instancias_locais())
        ids = [rt.id for rt in self._instancias_locais()]
        for rt in self._instancias_locais():
            rt.worker_id = self.worker_id
        if ids:
            marcadores = ",".join("?" for _ in ids)
            self.s.db.execute(f"UPDATE instances SET worker_id=? WHERE id IN ({marcadores})",
                              (self.worker_id, *ids))
        self.s.devices.bind_worker(self.worker_id, list(VERBOS))

    def batida(self) -> None:
        """Batida do worker local. Sem ela o ceifador marcaria o central offline em 30 s e todo comando de
        ciclo de vida deste servidor passaria a ser recusado com "worker não está conectado".

        `devices=[]` de propósito: o inventário do worker existe para o central saber do PROCESSO na máquina do
        agente. Aqui ele já sabe — e sobrescrever o estado observado com um resumo seria criar uma segunda
        verdade sobre os aparelhos desta máquina.
        """
        if self.link is None:
            return
        self.s.workers.on_heartbeat(self.worker_id, Heartbeat(resources=self._recursos(), clock_offset_s=0.0),
                                    self.link)

    async def desconectar(self) -> None:
        for tarefa in list(self.tarefas.values()):
            tarefa.cancel()
        self.tarefas.clear()
        if self.link is not None:
            self.s.workers.detach(self.worker_id, "o servidor está encerrando", self.link)
            self.s.devices.bind_worker(self.worker_id, None)
            self.link = None

    # ------------------------------------------------------------------ o canal
    async def _send(self, payload: dict[str, Any]) -> None:
        """O que o central "manda pelo socket". Aqui o socket é uma chamada de função.

        Volta IMEDIATAMENTE, como um `send` de verdade: `registry.dispatch` grava `dispatched` logo depois do
        envio, sem `await` no meio, e executar o verbo aqui dentro faria a marca de despacho chegar depois do
        desfecho.
        """
        tipo = payload.get("type")
        if tipo == "dispatch":
            msg = Dispatch.model_validate(payload)
            self.tarefas[msg.command_id] = asyncio.create_task(
                self._executar(msg), name=f"local-worker-{msg.command_id}")
        elif tipo == "cancel":
            await self._cancelar(str(payload.get("command_id") or ""))
        # `welcome`/`result_ack` não têm o que fazer deste lado: quem os consumiria é o agente remoto.

    async def _cancelar(self, command_id: str) -> None:
        """O ponto seguro de cancelamento desta máquina: o boot em andamento.

        É o mesmo efeito que a rota de cancelamento produzia antes — cancelar `rt.tasks["boot"]` —, agora do lado
        de quem executa, que é onde ele pertence. Quem decide se o desfecho é `cancelled` ou `uncertain` é o
        verbo, ao voltar da espera: `cancelled` só quando o emulador nunca chegou a subir.
        """
        linha = self.s.commands.get(command_id)
        rt = self.s.devices.devices.get(linha["instance_id"]) if linha is not None else None
        if rt is None:
            return
        tarefa = rt.tasks.get("boot")
        if tarefa is not None and not tarefa.done():
            tarefa.cancel()

    async def _progresso(self, msg: Dispatch, texto: str) -> None:
        await self._upstream(Progress(command_id=msg.command_id, message=texto))

    async def _upstream(self, mensagem: Any) -> None:
        if self.upstream is None:
            return
        await self.upstream(mensagem)

    async def _executar(self, msg: Dispatch) -> None:
        """Um comando, do `ack` ao `result`. Nada aqui pode escapar: exceção vira `uncertain`, nunca silêncio."""
        try:
            await self._upstream(Ack(command_id=msg.command_id))
            rt = self.s.devices.devices.get(msg.instance_id)
            if rt is None:
                await self._resultado(msg, "failed", f"{msg.instance_id} não existe neste servidor")
                return
            await self._progresso(msg, f"executando '{msg.verb}' em {rt.id} nesta máquina")
            outcome, motivo, dados = await self._verbo(rt, msg)
            await self._resultado(msg, outcome, motivo, dados)
        except asyncio.CancelledError:
            # A tarefa foi cancelada por fora (encerramento do servidor). NÃO se responde aqui: quem está
            # esperando é o `dispatch`, e `link.encerrar()` já transforma o que estava em voo em `uncertain` —
            # que é a verdade. Tentar enviar um resultado dentro de um cancelamento só perderia a mensagem.
            raise
        except Exception as exc:  # noqa: BLE001 - qualquer falha aqui é desfecho, não exceção perdida
            log.exception("verbo %s em %s (comando %s)", msg.verb, msg.instance_id, msg.command_id)
            await self._resultado(msg, "uncertain", f"erro ao executar '{msg.verb}' nesta máquina: {exc}")
        finally:
            self.tarefas.pop(msg.command_id, None)

    async def _resultado(self, msg: Dispatch, outcome: str, motivo: str | None,
                         dados: dict[str, Any] | None = None) -> None:
        await self._upstream(Result(command_id=msg.command_id, outcome=outcome,  # type: ignore[arg-type]
                                    reason=motivo, data=dados, fence=msg.fence))

    # ------------------------------------------------------------------ os verbos
    async def _verbo(self, rt: DeviceRuntime, msg: Dispatch) -> tuple[str, str | None, dict[str, Any] | None]:
        """Executa o verbo e traduz o que aconteceu em desfecho do contrato."""
        d, verb = self.s.devices, msg.verb
        if verb not in CICLO_DE_VIDA:
            return "failed", f"este worker não executa '{verb}'", None
        try:
            if verb == "create":
                await d.create(rt)
            elif verb in ("start", "wake"):
                await d.start_instance(rt)
            elif verb == "stop":
                await d.stop_instance(rt)
            elif verb == "hibernate":
                await d.stop_instance(rt, hibernate=True)
            elif verb == "restart":
                await d.restart_instance(rt)
            elif verb == "reset":
                await d.reset_instance(rt)
        except (DriverTimeout, AdbTimeout) as exc:
            # Não se sabe se o aparelho obedeceu: prazo estourado num `adb` é efeito possível.
            return "uncertain", str(exc), None
        except (InstanceBusy, ValueError, AdbError, AvdError, DriverError) as exc:
            return "failed", str(exc), None
        if verb in VERBOS_QUE_ESPERAM_O_BOOT:
            return await self._esperar_o_boot(rt, msg)
        if verb == "hibernate" and rt.state != InstanceState.hibernated:
            # A mesma regra do agente remoto (`worker/executor.VerbFailed`): hibernar que não salvou snapshot é
            # `failed` com o motivo, nunca "Hibernada" — o aparelho desligou, e o próximo boot será a frio.
            return "failed", rt.state_detail or f"o aparelho ficou em '{rt.state.value}', e não hibernado", None
        return "succeeded", None, None

    async def _esperar_o_boot(self, rt: DeviceRuntime, msg: Dispatch) -> tuple[str, str | None,
                                                                              dict[str, Any] | None]:
        """`start`/`wake`/`restart`/`reset` só terminam com o Android no ar.

        `start_instance` apenas ENFILEIRA o boot: sem esta espera o comando virava `succeeded` em 2 ms, antes de
        a guarda de RAM recusar e antes de o emulador subir.

        A espera é um pouco mais curta que o prazo do despacho, pela mesma razão que no agente remoto (que
        espera 480 s por um despacho de 540 s): quem executa precisa ter tempo de RESPONDER antes de a paciência
        do central acabar. Sem essa folga os dois prazos empatam, o despacho vence a corrida e o comando fecha
        com "o worker não respondeu" em vez da frase que diz o que de fato aconteceu — que o boot continua em
        andamento nesta máquina e nada será repetido sozinho.
        """
        paciencia = msg.timeout_s or prazo_de(msg.verb)
        desfecho, detalhe = await self.s.devices.aguardar_boot(rt, paciencia - min(10.0, paciencia * 0.1))
        if desfecho == "online":
            return "succeeded", None, None
        # Boot que não deu certo leva a cauda do log do emulador, na MESMA forma que o agente remoto usa
        # (`worker/executor.cauda_do_log`): quem lê o desfecho de um `start` não precisa saber onde ele rodou.
        log_do_boot = await self._cauda_do_log(rt)
        if self._cancelamento_pedido(msg.command_id):
            # O boot foi interrompido a pedido. `cancelled` SÓ quando o emulador nunca chegou a subir; com
            # processo no ar a espera acabou, mas o efeito não — `uncertain`.
            if rt.pid is None:
                try:
                    # "Não quero mais que ligue" é decisão: sem isto o monitor religaria o aparelho em segundos,
                    # porque `start_instance` deixou `desired_state=online`.
                    await self.s.devices.stop_instance(rt)
                except Exception:  # noqa: BLE001 - desligar o que nem subiu nunca muda o desfecho
                    log.exception("parada depois do cancelamento de %s", msg.command_id)
                return "cancelled", "cancelado antes de o emulador subir; nada ficou no ar", None
            return "uncertain", ("cancelado depois de o emulador ser iniciado; o processo pode continuar no ar e "
                                 "o efeito é desconhecido"), log_do_boot
        return ("failed" if desfecho == "failed" else "uncertain"), detalhe, log_do_boot

    async def _cauda_do_log(self, rt: DeviceRuntime) -> dict[str, Any] | None:
        """O fim do log do emulador deste AVD, já sem o que parecer segredo. `None` para aparelho externo: o
        processo não é nosso e não há log desta máquina a mostrar."""
        if rt.external:
            return None
        try:
            return await asyncio.to_thread(emu.log_do_emulador, self.s.cfg.logs_dir, rt.avd_name)
        except Exception:  # noqa: BLE001 - ler log é conveniência: nunca muda o desfecho do comando
            log.exception("cauda do log de %s", rt.id)
            return None

    def _cancelamento_pedido(self, command_id: str) -> bool:
        linha = self.s.commands.get(command_id)
        return linha is not None and linha["state"] == CommandState.cancel_requested.value
