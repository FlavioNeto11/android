"""Despacho de comandos de aparelho: do pedido (painel, rodízio, remediação, worker) ao desfecho gravado.

Morava em `api.py`, no arquivo das rotas, sem ser HTTP: toda função aqui tem a forma `(s: AppState, …)` e é
chamada tanto pelas rotas quanto pelo próprio central (`state.py`, `devices/proxy.py`, `workers/local.py`). Como
`api` importa `state`, esses chamadores só alcançavam o despacho por import tardio — era esse o ciclo
`api ↔ state` que contaminava o grafo inteiro (fase A da evolução arquitetural; a régua é
`tests/test_arquitetura.py`). Aqui o despacho não importa `api` nem `state` (o `AppState` só como anotação), então
os chamadores o importam no topo.

O que fica na API: o transporte (os WebSockets do worker, que dependem do FastAPI) e a tradução de
`DespachoRecusado` para a resposta HTTP (`api.recusa_do_despacho`, registrada em `main.create_app`).

Movido sem mudar comportamento. As únicas trocas: `err()`/`HTTPException` → `DespachoRecusado`, e `quem()` (que lê
o `Request`) → `_autor_sem_requisicao()`, que é o que `quem()` devolvia chamado sem argumento.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

from ..automation.driver import DriverError, DriverTimeout
from ..db import Row
from ..devices.adb import AdbError, AdbTimeout
from ..devices.avd import AvdError
from ..devices.compatibilidade import capacidades_de, motivo_incompativel, requisitos_de_release
from ..devices.manager import DESEJO_DO_VERBO, DeviceRuntime, InstanceBusy
from ..devices.verbs import (PRAZO_PADRAO_S, PRAZO_POR_VERBO, SO_ADB, VERBOS_QUE_ESPERAM_O_BOOT,
                             motivo_nao_suportado)
from ..models import CommandState, InstanceActionBody, InstanceState, ReleaseChannel, ReleaseState
from ..releases.catalog import InstalacaoIncerta
from ..security.sessions import operador_atual
from ..social.repository import frase_da_quarentena
from ..util import new_command_id, new_token, now, parse_iso
from ..workers.protocol import (MARCA_DE_FILA, Ack, Dispatch, Heartbeat, Hello, ObserveResult, Progress, Result,
                               ResultAck)
from ..workers.registry import WorkerError, WorkerLink
from .outbox import PENDING as OUTBOX_PENDING
from .reconciler import reconciliar_incertos
from .states import COMMAND_OPEN, InvalidCommandTransition
from .store import command_dto, publicar_comando

if TYPE_CHECKING:  # pragma: no cover - só para o verificador de tipos
    from ..state import AppState

log = logging.getLogger("poc.despacho")


class DespachoRecusado(Exception):
    """O despacho recusou o pedido ANTES de tocar no aparelho: status, `code` e mensagem da recusa.

    Existe para o despacho não depender do FastAPI (a infraestrutura HTTP mora só em `api`/`main`). A borda HTTP
    traduz para exatamente o que `api.err()` produzia: o mesmo status e `{"detail": {"code", "message", **extra}}`.
    Quem chama de fora da API (o executor da ação) captura esta exceção no lugar do `HTTPException` de antes.
    """

    def __init__(self, status: int, code: str, message: str, **extra: object) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    @property
    def detail(self) -> dict[str, object]:
        """O corpo da recusa, no formato que o `detail` da resposta HTTP sempre teve (e que quem capturava o
        `HTTPException` de antes lia)."""
        return {"code": self.code, "message": self.message, **self.extra}


def _autor_sem_requisicao() -> str:
    """O `api.quem()` chamado sem `Request`: o operador da sessão (o `ContextVar` que o `main.guarda` preenche) ou
    `panel`. Os comandos abertos aqui não têm o `Request` na mão; a sessão chega pelo contexto."""
    return operador_atual() or "panel"


LIFECYCLE_ACTIONS = {"create", "start", "stop", "hibernate", "wake", "restart", "reset", "install_apk", "open_app",
                     "home", "back", "recents"}

#: Verbos que DISPUTAM o aparelho: enquanto um deles estiver aberto, o próximo é recusado com 409 `device_busy`.
#: Teclas (`home`, `back`, `recents`) ficam de fora de propósito — apertar duas teclas seguidas não é duas
#: operações concorrentes no aparelho, e é do ciclo de vida que o aceite 9 trata.
VERBOS_EXCLUSIVOS = {"create", "start", "stop", "hibernate", "wake", "restart", "reset", "install_apk", "open_app"}

#: Verbos que MEXEM no aparelho de um jeito que não se desfaz olhando. Com o inventário divergente (item 4.5),
#: qualquer um deles pode agir no aparelho errado — é exatamente o dano que o achado #47 descreve como latente.
VERBOS_DESTRUTIVOS = {"reset", "stop", "restart", "hibernate", "install_apk", "create"}

#: Quarentena (ADR-055): com conta travada logada no aparelho, só estes verbos de ciclo de vida passam sem a
#: confirmação explícita da pessoa (`confirm_locked_account`). Parar e hibernar não abrem o app nem apagam nada;
#: todo o resto abre o app de uma conta morta (open_app, teclas, restart, start) ou apaga a prova (reset).
VERBOS_DA_QUARENTENA = {"stop", "hibernate"}


def _release_pronta_para(s: AppState, rt: DeviceRuntime, release_id: str) -> Row:
    """Confere que ESTA versão pode ir para ESTE aparelho, ou levanta o 404/409 com o motivo.

    Um lugar só, porque há dois chamadores: a rota de instalação por destino e o verbo `install_apk` do painel.
    Enquanto o verbo tinha caminho próprio (`adb install` do `apps.apk_path`), o mesmo pacote podia entrar por um
    caminho com assinatura aprovada, canário e estado observado e por outro sem nada disso — e o painel continuava
    dizendo `verifying` para um aparelho que já tinha outra versão instalada por fora (#83).
    """
    release = s.release_repo.release_row(release_id)
    if release is None:
        raise DespachoRecusado(404, "not_found", "Release não encontrada.")
    if release["status"] != ReleaseState.installable.value:
        raise DespachoRecusado(409, "release_not_installable",
                               f"A release está em '{release['status']}' e não pode ser instalada."
                               + (f" {release['detail']}" if release["detail"] else ""))
    if (porque := motivo_incompativel(requisitos_de_release(release), capacidades_de(rt), aparelho=rt.id)):
        # A limitação é explicada ANTES de agendar, que é a regra do pedido — e não no meio, como
        # `INSTALL_FAILED_NO_MATCHING_ABIS` num 202 que já tinha dito "aceito".
        raise DespachoRecusado(409, "app_incompativel", f"{porque}.")
    if release["channel"] == ReleaseChannel.quarantined.value:
        # A quarentena é o outro eixo: o arquivo está íntegro, a VERSÃO é que já falhou a prova. A recusa tem de
        # acontecer aqui, junto da de status, senão o 202 esconderia o motivo de quem chamou.
        raise DespachoRecusado(409, "release_quarantined",
                               "Esta versão está em quarentena porque já falhou a prova num aparelho."
                               + (f" {release['channel_detail']}" if release["channel_detail"] else "")
                               + " Para tentar de novo, coloque-a em canário de propósito.")
    return release


async def _instalar_versao_promovida(s: AppState, rt: DeviceRuntime, app: Row) -> None:
    """O verbo `install_apk` do painel, agora dentro da camada de releases.

    Antes ele era `adb install` do `apps.apk_path`: sem hash conferido, sem assinatura aprovada, sem canário, sem
    gravar `device_app_state` e sem prova de abertura. Dois caminhos para o mesmo pacote, um deles invisível para
    a camada que o painel exibe (#83). Agora há um só: a versão PROMOVIDA do pacote, com as mesmas recusas da
    instalação por destino. Sem release promovida, o verbo recusa e diz o que fazer — nunca cai no caminho velho.
    """
    promovida = s.releases.promoted_release(app["package"])
    if promovida is None:
        raise DespachoRecusado(409, "sem_versao_promovida",
                               f"{app['name']}: nenhuma versão de {app['package']} foi promovida ainda. Importe o "
                               "APK em Aplicativos, coloque-o em prova num aparelho e promova-o — instalar por fora "
                               "da camada de releases deixaria este aparelho com uma versão que o painel não sabe "
                               "descrever.")
    _release_pronta_para(s, rt, promovida.id)
    rt.app_versions.clear()
    await s.releases.install_on(rt, promovida.id, s.installer)


def _app_for(s: AppState, rt: DeviceRuntime, app_id: str | None) -> Row:
    chosen = app_id or s.db.scalar("SELECT app_id FROM instances WHERE id=?", (rt.id,))
    row = s.db.one("SELECT * FROM apps WHERE id=?", (chosen,)) if chosen else None
    if row is None:
        raise DespachoRecusado(400, "no_app", f"{rt.id}: nenhum app associado. Associe um app em Configuração → "
                                              "Instâncias e contas.")
    return row


def _publish_command(s: AppState, row: Row) -> None:
    """Todo estado de comando vai para a interface. A regra mora em `commands/store.py` porque quem fecha um
    comando não é só este handler: a reconciliação por sonda e a decisão humana publicam pelo mesmo caminho."""
    publicar_comando(s.bus, row)


# ====================================================================== app e sessão como comandos
#: Verbos do pipeline de APLICATIVO e de SESSÃO. Ficam fora de `LIFECYCLE_ACTIONS` de propósito: não entram por
#: `/instances/{id}/actions/{verbo}` (não são ciclo de vida do aparelho) mas vivem na MESMA tabela `commands`,
#: com a mesma máquina de estados e a mesma reconciliação de reinício.
#:
#: Existem porque o E1 transformou em entidade só a ação de instância: instalar, provar (canário), voltar de
#: versão, distribuir, verificar, buscar da loja e conectar/verificar/sair continuavam devolvendo
#: `202 {"accepted": true}` sem id — não havia como distinguir criado/enviado/iniciado/concluído/falhou/
#: desconhecido, nem estado `uncertain` para o timeout que não prova nada.
APP_COMMAND_VERBS = {"app.install", "app.verify", "app.canary", "app.rollback", "app.distribute", "store.sync",
                     "session.connect", "session.verify", "session.logout", "device.proxy"}
#: Na quarentena (ADR-055), os verbos de app e de sessão que continuam: ler o que está instalado (`app.verify` é
#: inspeção por adb, não abre o app) e a cópia da loja (a loja nunca tem conta de tarefa). Instalar, provar e voltar
#: de versão terminam na prova de ABERTURA do app; os de sessão abrem a conta travada.
VERBOS_DE_APP_NA_QUARENTENA = {"app.verify", "store.sync"}


def _abrir_comando_de_app(s: AppState, instance_id: str, verb: str, *, params: dict[str, object] | None = None,
                          idempotency_key: str | None = None,
                          requested_by: str | None = None) -> tuple[Row, bool]:
    rt = s.devices.devices.get(instance_id)
    chave = idempotency_key or f"{instance_id}:{verb}:{new_token()}"
    return s.commands.create(command_id=new_command_id(), instance_id=instance_id, verb=verb,
                             idempotency_key=chave, requested_by=requested_by or _autor_sem_requisicao(),
                             host_worker_id=(rt.worker_id if rt is not None else None) or s.cfg.owner_id,
                             params=params)


def _despachar_trabalho(s: AppState, rt: DeviceRuntime, verb: str, factory: Callable[[], Awaitable[object]], *,
                        label: str, params: dict[str, object] | None = None, idempotency_key: str | None = None,
                        ocupado: str | None = None, requested_by: str | None = None,
                        recusar_ocupado: bool = True) -> dict[str, object]:
    """Abre um comando, despacha o trabalho pela fila do aparelho e faz o desfecho REAL fechar o comando.

    Estados: `created` → `dispatched` (a tarefa foi agendada aqui) → `running` (o trabalho começou) →
    `succeeded` / `failed` / `uncertain`. `rejected` quando o aparelho está ocupado — e aí nada foi tocado.

    `uncertain` vem de `InstalacaoIncerta`: timeout do adb não prova que a operação falhou, e chamar isso de
    falha era o que criava o estado pegajoso que só saía reinstalando. Quem cai aqui deixa o app em `verifying`
    sem operação pendente, e a releitura automática resolve.

    Síncrona de propósito: entre `run_device_job` e o carimbo de `dispatched` não pode haver `await`, senão a
    tarefa já estaria tentando ir para `running` antes de o comando sair de `created`.
    """
    row, repetido = _abrir_comando_de_app(s, rt.id, verb, params=params, idempotency_key=idempotency_key,
                                          requested_by=requested_by)
    if repetido:
        # Mesma chave: devolve o comando ORIGINAL. Reenviar não instala duas vezes.
        return {"accepted": True, "command_id": row["id"], "state": row["state"], "deduplicated": True,
                "instance_id": rt.id}
    command_id = str(row["id"])
    if (verb not in VERBOS_DE_APP_NA_QUARENTENA
            and (marcador := s.social_repo.conta_travada_no_aparelho(rt.id)) is not None):
        # Quarentena (ADR-055): a recusa fica no histórico do aparelho com o motivo, e nada foi tocado. Quem
        # distribui para o parque recebe o "não" como item, como o ocupado — o resto do parque segue.
        motivo = frase_da_quarentena(marcador)
        _publish_command(s, s.commands.transition(command_id, CommandState.rejected, reason=motivo))
        if not recusar_ocupado:
            return {"accepted": False, "command_id": command_id, "state": CommandState.rejected.value,
                    "deduplicated": False, "instance_id": rt.id, "reason": motivo}
        raise DespachoRecusado(409, "locked_account", f"{motivo}.", command_id=command_id)

    async def envolvido() -> object:
        try:
            _publish_command(s, s.commands.transition(command_id, CommandState.running))
        except Exception:  # noqa: BLE001 - marcar o início nunca pode impedir o trabalho de acontecer
            log.exception("comando %s: falha ao marcar início", command_id)
        try:
            resultado = await factory()
        except InstalacaoIncerta as exc:
            _publish_command(s, s.commands.transition(command_id, CommandState.uncertain, reason=str(exc)))
            raise
        except Exception as exc:  # noqa: BLE001 - o desfecho negativo é registrado e repropagado
            _publish_command(s, s.commands.transition(command_id, CommandState.failed, reason=str(exc)))
            raise
        corpo = resultado if isinstance(resultado, dict) else None
        _publish_command(s, s.commands.transition(command_id, CommandState.succeeded,
                                                  result={"outcome": corpo} if corpo else None))
        return resultado

    if not s.scheduler.run_device_job(rt, envolvido, label=label):
        motivo = ocupado or "O aparelho está ocupado; tente novamente em instantes."
        _publish_command(s, s.commands.transition(command_id, CommandState.rejected, reason=motivo))
        if not recusar_ocupado:
            # Quem distribui para o parque INTEIRO não pode falhar por um aparelho ocupado: ali "ocupado" é
            # pendência, não recusa do pedido. O comando fica registrado como `rejected` naquele aparelho.
            return {"accepted": False, "command_id": command_id, "state": CommandState.rejected.value,
                    "deduplicated": False, "instance_id": rt.id, "reason": motivo}
        raise DespachoRecusado(409, "device_busy", motivo, command_id=command_id)
    _publish_command(s, s.commands.transition(command_id, CommandState.dispatched))
    return {"accepted": True, "command_id": command_id, "state": CommandState.dispatched.value,
            "deduplicated": False, "instance_id": rt.id}


def pedir_trabalho_de_app(s: AppState, rt: DeviceRuntime, verb: str, factory: Callable[[], Awaitable[object]], *,
                          label: str, params: dict[str, object], idempotency_key: str,
                          requested_by: str) -> dict[str, object]:
    """`_despachar_trabalho` para quem pede de DENTRO do central (o `apply` dos recursos declarativos, fase H).

    O mesmo comando, a mesma fila do aparelho e o mesmo desfecho da rota HTTP; a única diferença é que o aparelho
    ocupado volta como `accepted: False` (o comando fica `rejected` no histórico), e não como exceção — quem chama
    não é uma requisição que precise do 409.
    """
    if verb not in APP_COMMAND_VERBS:
        raise ValueError(f"'{verb}' não é verbo de app nem de sessão")
    return _despachar_trabalho(s, rt, verb, factory, label=label, params=params, idempotency_key=idempotency_key,
                               requested_by=requested_by, recusar_ocupado=False)


def conferir_release_para(s: AppState, rt: DeviceRuntime, release_id: str) -> Row:
    """A pré-condição da rota de instalação (`_release_pronta_para`), para quem instala de dentro do central: a
    versão existe, é instalável, não está em quarentena e roda neste aparelho — ou `DespachoRecusado` com o motivo."""
    return _release_pronta_para(s, rt, release_id)


def _cancelamento_pedido(s: AppState, command_id: str) -> bool:
    """Alguém pediu o cancelamento DESTE comando enquanto ele corria? Lido do banco, e não de memória, porque
    quem pede (a rota HTTP) e quem executa (a tarefa) são dois caminhos que só se encontram no estado."""
    linha = s.commands.get(command_id)
    return linha is not None and linha["state"] == CommandState.cancel_requested.value


def _fechar_cancelado(s: AppState, command_id: str, motivo: str) -> None:
    """Confirma o cancelamento. Só é chamado onde se pode AFIRMAR que o efeito não aconteceu — `cancelled` é
    cancelamento confirmado (migrations/013_commands.sql), nunca "desisti de esperar"."""
    try:
        _publish_command(s, s.commands.transition(command_id, CommandState.cancelled, reason=motivo))
    except (InvalidCommandTransition, KeyError):
        # O desfecho real chegou primeiro: ele vale, e o pedido de cancelamento simplesmente perdeu a corrida.
        log.info("comando %s já tinha desfecho quando o cancelamento foi confirmar", command_id)
        return
    # A entrega deixou de ser devida. Sem isto, um comando cancelado ANTES de o transporte aceitá-lo (broker
    # fora do ar, ou queda entre aceitar e publicar) continuaria pendente e o dreno de partida o ressuscitaria,
    # executando no aparelho um verbo que já foi confirmado como cancelado.
    s.outbox.discard(command_id)


def _para_worker(rt: DeviceRuntime, action: str) -> bool:
    """O verbo vai para um worker? Uma pergunta, uma resposta, usada pelo handler HTTP e pelo executor — antes
    cada um decidia por conta própria e as marcas de tempo dependiam de quem chegasse primeiro.

    Depois do `LocalWorker`, o central TAMBÉM é um worker: os aparelhos desta máquina têm `worker_id =
    OWNER_ID`, e o ciclo de vida deles sai pelo mesmo despacho do agente remoto. O que continua fora é o verbo de
    ADB puro (`SO_ADB`), que sempre sai daqui pelo túnel, more o aparelho onde morar.
    """
    return bool(rt.worker_id and rt.worker_verbs and action in rt.worker_verbs and action not in SO_ADB)


async def _do_action_no_worker(s: AppState, rt: DeviceRuntime, action: str, body: InstanceActionBody,
                               command_id: str) -> None:
    """Despacha o verbo para o agente da outra máquina e traduz o desfecho dele em estado de comando.

    A cerca (`fence`) vai no despacho e volta no resultado: worker que perdeu a autorização e voltou do limbo tem
    o resultado recusado, em vez de sobrescrever o presente.

    Marcas de tempo de verdade: `dispatched` (com o `worker_id`) é gravado no instante em que o comando SAI pelo
    socket — não dentro da requisição HTTP que só agendou a tarefa. `acked` e `running` chegam do próprio worker
    (`Ack` e o primeiro `Progress`), tratados em `_tratar_mensagem_do_worker`.
    """
    linha = s.commands.get(command_id)
    if linha is None or linha["state"] != CommandState.created.value:
        if (linha is not None and linha["state"] == CommandState.cancel_requested.value
                and not linha["dispatched_at"]):
            # Cancelado entre o agendamento e o envio: o worker nunca soube deste comando, e é exatamente isso
            # que `cancelled` significa. Nada saiu pelo socket. `dispatched_at` vazio é a prova: com ele
            # preenchido, quem está aqui é uma entrega repetida de outro processo, e a primeira já despachou.
            _fechar_cancelado(s, command_id, "cancelado antes do envio; nada foi enviado ao worker")
            return
        # Recusado/cancelado entre o agendamento e aqui: não se despacha o que já tem desfecho. Com a rota
        # decidida uma única vez, chegar aqui com outro estado é sinal de caminho não previsto — logue.
        log.warning("comando %s não foi despachado ao worker: estado %s", command_id,
                    linha["state"] if linha else "inexistente")
        return
    cerca = int(linha["fence"])
    # Banco restaurado (K-004): o agente guarda no diário a maior cerca que já executou e a declara no `hello`.
    # Despachar abaixo dela seria recusado como ordem vencida, então a cerca sobe antes de sair daqui.
    if cerca <= (piso := s.workers.piso_de_cerca(rt.worker_id, rt.id)):
        cerca = s.commands.elevar_cerca(command_id, piso)
    prazo = PRAZO_POR_VERBO.get(action, PRAZO_PADRAO_S)
    # A DECISÃO é do central, não da máquina do worker, e é gravada ANTES do despacho: quem manda parar um
    # aparelho remoto quer que ele continue parado mesmo se o backend reiniciar, e quem manda ligar autoriza o
    # monitor a readotá-lo. Sem isto, `desired_state` ficava nulo em todo aparelho de worker e o "Parar" remoto
    # era desfeito pela readoção automática ≤30 s depois.
    if (desejo := DESEJO_DO_VERBO.get(action)) is not None:
        s.devices.set_desired_state(rt, desejo)
    msg = Dispatch(command_id=command_id, fence=cerca, verb=action, instance_id=rt.id, serial=rt.serial,
                   params={k: v for k, v in (body.model_dump() or {}).items() if v is not None
                           and k not in ("idempotency_key",)},
                   timeout_s=prazo)

    def marcar_despachado() -> None:
        _publish_command(s, s.commands.transition(command_id, CommandState.dispatched, worker_id=rt.worker_id))
        # Cinto: se o ACK já tiver chegado (transporte que entrega a resposta dentro do próprio `send`), ele
        # encontraria o comando ainda em `created` e seria descartado. Aqui ele é recuperado do registro.
        link = s.workers.live.get(rt.worker_id or "")
        if link is not None and command_id in link.confirmados:
            _mudar_estado_do_worker(s, command_id, rt.worker_id or "", CommandState.acked,
                                    de={CommandState.dispatched})

    # O mesmo cadeado que serializa o ciclo de vida local passa a valer no caminho do worker: sem ele, o
    # rodízio/IA podia mexer no aparelho remoto no meio de um `reset` da outra máquina.
    #
    # Para o worker LOCAL ele não é tomado aqui: quem executa é o `DeviceManager`, e ele já se serializa neste
    # mesmo cadeado dentro de `create`/`_boot`/`stop_instance`. Tomá-lo aqui seria esperar, de dentro do
    # despacho, por um cadeado que só o próprio despacho pode soltar — um impasse, não uma proteção.
    local = rt.worker_id == s.cfg.owner_id
    cadeado = contextlib.nullcontext() if local else rt.op_lock
    try:
        async with cadeado:
            # Última conferência ANTES do envio, DENTRO do cadeado: esperar o cadeado pode levar minutos (o
            # comando anterior daquele aparelho), e um cancelamento pedido nessa espera não pode terminar em
            # despacho assim mesmo. Daqui até o `send` não há suspensão, então a conferência vale.
            #
            # E o estado é RELIDO, não só o pedido de cancelamento: `created` foi conferido antes do cadeado, e
            # outra entrega do mesmo comando (outro processo, ou a mesma ordem republicada) pode ter despachado ou
            # fechado o comando nessa espera. Despachar de novo mandaria ao worker um comando já em voo — ou já
            # `cancelled`, o que executaria o efeito depois do cancelamento confirmado.
            atual = s.commands.get(command_id)
            estado = atual["state"] if atual is not None else None
            if estado == CommandState.cancel_requested.value and not atual["dispatched_at"]:
                _fechar_cancelado(s, command_id, "cancelado antes do envio; nada foi enviado ao worker")
                return
            if estado != CommandState.created.value:
                log.warning("comando %s não foi despachado ao worker: já está em %s (outra entrega chegou antes)",
                            command_id, estado or "inexistente")
                return
            resultado = await s.workers.dispatch(rt.worker_id or "", msg, marcar_despachado)
        alvo = {"succeeded": CommandState.succeeded, "failed": CommandState.failed,
                "uncertain": CommandState.uncertain, "cancelled": CommandState.cancelled}[resultado.outcome]
        motivo, dados = resultado.reason, resultado.data
    except WorkerError as exc:
        # Falha ao ENVIAR é o único caso em que se pode afirmar que nada aconteceu no aparelho.
        alvo, motivo, dados = CommandState.failed, exc.message, None
    except Exception as exc:  # noqa: BLE001 - quebrou no meio do despacho: não se sabe se o worker agiu
        log.exception("despacho de %s para o worker %s", command_id, rt.worker_id)
        alvo, motivo, dados = CommandState.uncertain, f"erro no despacho ao worker: {exc}", None
    if alvo is CommandState.cancelled and DESEJO_DO_VERBO.get(action) == InstanceState.online.value:
        # A DECISÃO foi gravada antes do despacho ("eu quero este aparelho no ar"). Um `start` cancelado não pode
        # deixá-la de pé: o monitor readotaria em ≤30 s o aparelho que alguém acabou de mandar não subir.
        s.devices.set_desired_state(rt, InstanceState.stopped.value)
    # O efeito no CENTRAL vem antes de publicar o desfecho: quem lê o `command.updated` (painel) e quem lê o
    # estado do aparelho no mesmo instante precisam ver a mesma coisa. Falhar aqui não muda o desfecho do
    # comando — o agente já agiu, e mentir sobre isso seria pior do que um estado desatualizado.
    #
    # Só o caminho REMOTO precisa disso: `aplicar_desfecho_remoto` traduz o que outra máquina fez em estado
    # daqui. O worker local É o `DeviceManager` — o efeito já aconteceu nele, e repeti-lo soltaria do painel um
    # aparelho que acabou de ser adotado.
    try:
        if not local:
            await s.devices.aplicar_desfecho_remoto(rt, action, alvo.value, dados)
    except Exception:  # noqa: BLE001
        log.exception("efeitos no central do comando %s (%s em %s)", command_id, action, rt.id)
    try:
        _publish_command(s, s.commands.transition(command_id, alvo, reason=motivo, result=dados,
                                                  worker_id=rt.worker_id))
    except Exception:  # noqa: BLE001 - comando já encerrado por outra via (resultado tardio) não é erro
        log.warning("comando %s já tinha desfecho ao voltar do worker", command_id)
    # Só agora: readotar fala com o aparelho pelo túnel e pode demorar. O comando já está fechado, e o aparelho
    # já está destrancado para o próximo pedido — a readoção acontece por trás, como faria o monitor.
    try:
        if not local:
            await s.devices.readotar_depois_do_worker(rt, action, alvo.value)
    except Exception:  # noqa: BLE001 - readoção é observação: falhar aqui não muda o desfecho do comando
        log.exception("readoção de %s depois do comando %s", rt.id, command_id)


async def _do_action(s: AppState, rt: DeviceRuntime, action: str, body: InstanceActionBody, command_id: str,
                     remoto: bool) -> None:
    """Uma execução por comando neste processo; o resto é `_executar_acao`.

    A entrega é AO MENOS uma vez (outbox, JetStream): uma segunda entrega do mesmo comando pode chegar com a
    primeira ainda viva — esperando o cadeado do aparelho, ou no meio do verbo. As duas passavam: no caminho do
    worker as duas viam `created` antes do cadeado e despachavam em sequência (a segunda sobrescrevia
    `link.pendentes`); no caminho local a segunda falhava ao entrar em `running`, via `cancel_requested` e fechava
    como `cancelled` um comando que a primeira ainda executava. A segunda entrega agora não faz nada: quem fecha o
    comando é a primeira.
    """
    em_execucao = s.commands.em_execucao
    if command_id in em_execucao:
        log.warning("comando %s já está em execução neste processo; entrega repetida ignorada", command_id)
        return
    em_execucao.add(command_id)
    try:
        await _executar_acao(s, rt, action, body, command_id, remoto)
    finally:
        em_execucao.discard(command_id)


async def _executar_acao(s: AppState, rt: DeviceRuntime, action: str, body: InstanceActionBody, command_id: str,
                         remoto: bool) -> None:
    """Executa a ação dirigindo os estados do comando.

    O que mudou: antes toda exceção era engolida e virava um evento `log` que o frontend nem tratava — a ação
    recusada no fundo era indistinguível de sucesso. Agora cada desfecho é gravado no comando, e `uncertain` é
    reservado para o caso honesto: timeout, em que o efeito pode ter acontecido e ninguém sabe.

    `remoto` vem DECIDIDO de quem gravou a marca de entrega, e não é reavaliado aqui de propósito: entre o
    handler e esta tarefa o worker pode cair (`bind_worker(..., None)` zera `rt.worker_verbs`), e as duas metades
    discordarem deixaria o comando preso — para sempre, agora que comando aberto tranca o aparelho. Se o worker
    sumiu, o despacho falha com `worker_offline` e o comando vira `failed`, que é a resposta verdadeira.

    **Um ramo só para o CICLO DE VIDA.** Criar, ligar, acordar, parar, hibernar, reiniciar e resetar saem sempre
    por `_do_action_no_worker` — para o agente da outra máquina ou para o `LocalWorker` deste servidor
    (`workers/local.py`). Não há mais duas implementações do mesmo verbo com semânticas diferentes: um despacho,
    uma cerca, um prazo, um mapa de desfechos.

    O que continua saindo DAQUI é o verbo de ADB puro (`verbs.SO_ADB`: instalar APK, abrir app, teclas), e isso
    vale igualmente para aparelho local e remoto — aquele caminho passa pelo túnel e está provado em campo, e
    mandar o catálogo de APK para cada máquina seria trocar um problema resolvido por um novo.
    """
    d = s.devices
    # A ordem importa: NADA de carimbar `running` antes de saber quem executa. No caminho do worker, `running`
    # significa "quem hospeda o aparelho começou a agir" e só ele pode dizer isso.
    if remoto:
        await _do_action_no_worker(s, rt, action, body, command_id)
        return
    if action not in SO_ADB:
        # Ciclo de vida que não achou worker. Não há mais uma segunda implementação a chamar: ou o worker local
        # não subiu, ou o do aparelho caiu entre a entrega e esta tarefa. A resposta verdadeira é dizer isso —
        # o aparelho ficou intacto, e `failed` é o desfecho que não manda ninguém desconfiar do estado dele.
        _publish_command(s, s.commands.transition(
            command_id, CommandState.failed,
            reason=f"nenhum worker está disponível para executar '{action}' em {rt.id}; "
                   "confira a Infraestrutura"))
        return
    try:
        _publish_command(s, s.commands.transition(command_id, CommandState.running))
    except InvalidCommandTransition:
        # O único caminho previsto até aqui: cancelamento pedido entre a entrega e o começo da execução. O verbo
        # não chegou a rodar, então o aparelho ficou intacto — e é isso que fica registrado. `started_at` vazio é
        # a prova de que ele não rodou: com ele preenchido, quem está aqui é uma entrega repetida, e a primeira
        # ainda executa (fechar como `cancelled` seria afirmar o que não aconteceu).
        atual = s.commands.get(command_id)
        if (atual is not None and atual["state"] == CommandState.cancel_requested.value
                and not atual["started_at"]):
            _fechar_cancelado(s, command_id, "cancelado antes de começar; nada foi executado neste aparelho")
        else:
            log.warning("comando %s não pôde entrar em running", command_id)
        return
    except Exception:  # noqa: BLE001 - falha ao registrar não deixa a tarefa agir às escondidas
        log.exception("comando %s não pôde entrar em running", command_id)
        return
    try:
        if action == "install_apk":
            await _instalar_versao_promovida(s, rt, _app_for(s, rt, body.app_id))
        elif action == "open_app":
            abriu, detalhe = await d.open_app(rt, _app_for(s, rt, body.app_id))
            if not abriu:
                # O `am start` volta positivo mesmo quando o app cai na abertura. Sem a janela em foco não há
                # prova de que abriu — e "não sei" é `uncertain`, não `succeeded`.
                _publish_command(s, s.commands.transition(command_id, CommandState.uncertain, reason=detalhe))
                return
        else:
            await d.quick_key(rt, action)
    except InstalacaoIncerta as exc:
        # Mesma regra do caminho por release: timeout do adb não prova que a instalação falhou, e gravar `failed`
        # aqui recriaria o estado pegajoso que só saía reinstalando por cima.
        _publish_command(s, s.commands.transition(command_id, CommandState.uncertain, reason=str(exc)))
        return
    except (DriverTimeout, AdbTimeout) as exc:
        # Não sabemos se o aparelho obedeceu: o comando não é repetido sozinho, e quem olhar vê "incerto".
        # `AdbTimeout` entra aqui ANTES de `AdbError` de propósito: prazo estourado num `adb install`/`am start`
        # é efeito possível (o `pm install` continua no aparelho), e gravar `failed` fazia o usuário reinstalar
        # por cima — ou concluir que falhou o que funcionou. Em remoto sob carga isso já aconteceu em campo.
        _publish_command(s, s.commands.transition(command_id, CommandState.uncertain, reason=str(exc)))
        return
    except DespachoRecusado as exc:
        _publish_command(s, s.commands.transition(command_id, CommandState.failed, reason=exc.message))
        return
    except (InstanceBusy, ValueError, AdbError, AvdError, DriverError) as exc:
        # `AvdError` entra aqui porque `create` agora deixa a falha subir: AVD que não foi criado não vira
        # `succeeded` com o aparelho em `absent`.
        _publish_command(s, s.commands.transition(command_id, CommandState.failed, reason=str(exc)))
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("ação %s em %s", action, rt.id)
        _publish_command(s, s.commands.transition(command_id, CommandState.failed, reason=str(exc)))
        return
    # `start`/`hibernate` e a espera pelo boot saíram daqui: eles são ciclo de vida, e ciclo de vida agora tem um
    # executor só (`workers/local.py` para os aparelhos desta máquina, o agente para os das outras). O que sobra
    # neste caminho é ADB puro, que termina quando o comando de ADB volta.
    _publish_command(s, s.commands.transition(command_id, CommandState.succeeded))


def s_android_hibernation(rt: DeviceRuntime) -> bool:
    return bool(rt.cfg.instance_android(rt.id).hibernation)


def _hiberna_o_hospedeiro(s: AppState, rt: DeviceRuntime) -> tuple[bool, str]:
    """Quem decide se hibernar é possível é a máquina que HOSPEDA o aparelho. Devolve `(pode, por que não)`.

    Para o aparelho de OUTRA máquina vale a declaração do agente no `Hello`: consultar o `config.yaml` deste
    servidor fazia o painel oferecer "Hibernar" para uma máquina que sobe tudo a frio, e o `wake` seguinte seria
    um boot a frio disfarçado.

    Para o aparelho DESTA máquina vale a configuração por instância. O `LocalWorker` também declara hibernação no
    registro, mas lá ela é um `bool` por máquina, e aqui `android.hibernation` aceita sobreposição por aparelho —
    então a resposta fina continua vindo da configuração, que é onde ela é fina.
    """
    if rt.worker_id and rt.worker_id != s.cfg.owner_id:
        return s.workers.hiberna(rt.worker_id), ("o worker que hospeda este aparelho não salva snapshot "
                                                 "(android.hibernation desligado na máquina dele)")
    return s_android_hibernation(rt), "hibernação desligada na configuração (android.hibernation)"


def _precheck(s: AppState, rt: DeviceRuntime, action: str, body: InstanceActionBody,
              command_id: str | None = None) -> tuple[str, str] | None:
    """`None` quando pode seguir; senão `(código, motivo)`. O código vira o `code` do 409 — `device_busy` precisa
    ser distinguível de `rejected` por quem chama a API."""
    if action not in LIFECYCLE_ACTIONS:
        return "rejected", "ação desconhecida"
    # Quarentena (ADR-055) antes de tudo: conta travada logada. Em 27/09 01:47Z um `open_app` chegou ao android-04
    # com o felipe já bloqueado e o desafio na tela. Só parar e hibernar passam; o resto exige a confirmação
    # explícita da pessoa — que o pedido automático (remediação, rodízio, saúde, reconciliação) nunca manda.
    if action not in VERBOS_DA_QUARENTENA and (marcador := s.social_repo.conta_travada_no_aparelho(rt.id)) is not None:
        if not body.confirm_locked_account:
            return "locked_account", frase_da_quarentena(marcador, action)
        s.bus.emit("log", f"{rt.id}: '{action}' confirmado explicitamente apesar da quarentena da conta "
                          f"@{marcador['handle']} (confirm_locked_account; pedido por {_autor_sem_requisicao()})",
                   level="warn", instance_id=rt.id, data={"command_id": command_id, "verb": action})
    if action == "reset" and not body.confirm:
        return "rejected", "o reset apaga dados e sessão do aparelho; envie confirm=true"
    # Inventário divergente (item 4.5; achado #47): as fontes discordam sobre QUAL aparelho está por trás deste
    # id. Verbo destrutivo aqui apagaria o aparelho errado em silêncio — era o dano latente do "três lugares sem
    # conferência". Os verbos de leitura e de tela seguem: quem diagnostica precisa deles.
    if action in VERBOS_DESTRUTIVOS and rt.inventory_state == "divergent":
        return "rejected", (f"o inventário deste aparelho está divergente ({rt.inventory_detail}); resolva o "
                            f"vínculo antes de '{action}'")
    # UM APARELHO, UMA OPERAÇÃO. Vale para os dois caminhos e para os dois clientes (painel e API): enquanto
    # houver comando aberto naquele aparelho, o próximo é recusado ANTES de tocar em qualquer coisa. Sem isto,
    # dois `start`/`reset` concorrentes chegavam juntos ao agente remoto e se intercalavam. O comando recém-criado
    # está ele mesmo aberto, por isso ele é excluído da consulta.
    if action in VERBOS_EXCLUSIVOS and \
            (aberto := s.commands.open_for_instance(rt.id, exclude=command_id, verbs=VERBOS_EXCLUSIVOS)) is not None:
        return "device_busy", (f"{rt.id} já tem o comando '{aberto['verb']}' em andamento "
                               f"({aberto['id']}, {aberto['state']}); espere o desfecho")
    # Manutenção do worker: comando de painel para um aparelho hospedado por worker em manutenção é recusado antes
    # de qualquer outra checagem — a pessoa que ligou a manutenção espera que nada novo seja despachado. Só a
    # manutenção intercepta aqui; "não conectado"/"não inscrito" seguem para a checagem de capacidade abaixo, que
    # já tem mensagem própria (verbos declarados pelo worker via `rt.worker_verbs`).
    if rt.worker_id and (porque := s.workers.motivo_manutencao(rt.worker_id)) is not None:
        return "rejected", porque
    # Capacidade primeiro: o que o aparelho NÃO consegue fazer é recusado com a explicação, antes de agendar.
    # Cobre a loja e o aparelho de outra máquina no mesmo lugar, para ação única e lote.
    if (porque := motivo_nao_suportado(rt, action)) is not None:
        return "rejected", porque
    # Quem decide se hibernar é possível é a máquina que HOSPEDA o aparelho — uma pergunta só, para os dois
    # caminhos (ver `_hiberna_o_hospedeiro`).
    if action == "hibernate" and not (resposta := _hiberna_o_hospedeiro(s, rt))[0]:
        return "rejected", resposta[1]
    # Acordar é subir A PARTIR do snapshot. Sem snapshot não existe o que acordar: o que aconteceria é um boot a
    # frio com nome de "Acordar" — recusa explicada, e "Iniciar" continua ali para quem quer ligar a frio.
    if action == "wake" and not rt.snapshot_valid:
        return "rejected", "não há snapshot salvo deste aparelho; use 'Iniciar' para ligar a frio"
    if action in ("stop", "hibernate", "restart", "reset", "install_apk", "open_app", "home", "back", "recents") \
            and rt.control.value == "ai":
        return "device_busy", "a IA está executando neste aparelho; pause/cancele a execução ou assuma o controle"
    if action in ("install_apk", "open_app", "home", "back", "recents") and rt.state != InstanceState.online:
        return "rejected", "a instância precisa estar online"
    return None


async def _despachar(s: AppState, command_id: str) -> None:
    """Publica a ordem do outbox no transporte e só então a marca como saída (item 5.6).

    A ordem das duas coisas é o item inteiro. Marcar antes de publicar tornaria a queda entre as duas linhas
    uma perda silenciosa — comando `sent` que nunca saiu —, que é exatamente o defeito do achado #30 num lugar
    novo. Publicando primeiro, a mesma queda deixa a linha `pending` e o dreno de partida publica de novo: ao
    menos uma vez, com a recusa de reentrega ficando por conta da máquina de estados do comando (aqui) e do
    diário do agente (lá).
    """
    if command_id in s.outbox.publicando:
        return                                  # este processo já está publicando esta ordem
    s.outbox.publicando.add(command_id)
    try:
        row = s.outbox.get(command_id)
        if row is None or row["state"] != OUTBOX_PENDING:
            return                              # já saiu, ou a entrega deixou de ser devida
        # `hosted_by`: a réplica que segura o canal do worker e o túnel do aparelho — é ela que o transporte NATS
        # endereça. Lido na hora de publicar, e não ao aceitar: é quem hospeda AGORA que vai executar.
        envelope = {"command_id": command_id, "instance_id": row["instance_id"], "worker_id": row["worker_id"],
                    "hosted_by": s.db.scalar("SELECT hosted_by FROM instances WHERE id=?", (row["instance_id"],)),
                    "verb": row["verb"], **s.outbox.payload_of(row)}
        try:
            await s.transport.publish(envelope)
        except Exception as exc:                # noqa: BLE001 - transporte fora do ar não é comando perdido
            # A linha CONTINUA pendente, e é isso que salva o comando: o laço de repetição publica de novo. Não
            # transforme isto em erro do painel — quem clicou já teve o pedido aceito e gravado.
            log.warning("comando %s: o transporte não aceitou a publicação (%s); segue na fila", command_id, exc)
            return
        s.outbox.mark_sent(command_id)
    finally:
        s.outbox.publicando.discard(command_id)


async def executar_envelope(s: AppState, envelope: dict[str, Any]) -> None:
    """Ponta consumidora do transporte: transforma o envelope de volta em execução.

    É o MESMO caminho do despacho de sempre (`_do_action`), e é de propósito: o transporte troca por onde a
    ordem viaja, nunca o que ela faz nem quem fecha o comando.
    """
    command_id = str(envelope.get("command_id") or "")
    instance_id = str(envelope.get("instance_id") or "")
    verb = str(envelope.get("verb") or "")
    rt = s.devices.devices.get(instance_id)
    if rt is None:
        # Aparelho que saiu da configuração entre aceitar e entregar. Dizer isso é verdadeiro e fecha o comando;
        # deixá-lo aberto trancaria o aparelho (que nem existe) para sempre.
        log.warning("comando %s: %s não está neste backend; nada foi executado", command_id, instance_id)
        if (linha := s.commands.get(command_id)) is not None and CommandState(linha["state"]) in COMMAND_OPEN:
            _publish_command(s, s.commands.transition(
                command_id, CommandState.failed,
                reason=f"{instance_id} não está neste backend; nada foi executado"))
        return
    body = InstanceActionBody(**(envelope.get("body") or {}))
    await _do_action(s, rt, verb, body, command_id, bool(envelope.get("remoto")))


def _marcar_entregue(s: AppState, rt: DeviceRuntime, action: str, command_id: str,
                     body: InstanceActionBody) -> tuple[str, bool]:
    """Carimba `dispatched` SÓ quando a entrega já aconteceu. Devolve `(estado, vai_para_o_worker)`.

    O segundo valor existe para a rota ser decidida UMA vez: quem grava a marca e quem executa precisam
    concordar, senão uma queda do worker entre os dois deixaria o comando sem ninguém para fechá-lo.

    No caminho local, entregar é agendar a tarefa que vai executar aqui mesmo — a marca vale. No caminho do
    worker, entregar é o comando SAIR pelo socket, e isso ainda não aconteceu: o comando continua `created` até
    `_do_action_no_worker` conseguir enviar. Era este o carimbo mentiroso do achado #7 (`dispatched_at` gravado
    dentro da requisição HTTP, antes de qualquer envio) — e é ele que torna `created` → `failed` na reconciliação
    uma afirmação verdadeira: o que nunca saiu não tocou no aparelho.

    **A linha do outbox é gravada aqui, na MESMA transação** (item 5.6). Aqui, e não em `CommandStore.create`,
    porque é aqui que o comando passou no pré-voo e a entrega passou a ser devida: o que é recusado no pré-voo
    nunca chega a ter entrega pendente, e portanto o dreno de partida não o ressuscita.
    """
    remoto = _para_worker(rt, action)
    # O corpo COMO FOI ACEITO vai para o outbox: `commands.params` guarda só o `app_id`, e `confirm` — a
    # autorização humana que separa um `reset` pedido de um `reset` acidental — se perderia num reenvio.
    payload = {"body": body.model_dump(mode="json"), "remoto": remoto}
    with s.db.tx():
        s.outbox.enqueue(command_id=command_id, instance_id=rt.id, verb=action,
                         worker_id=(rt.worker_id if remoto else None), payload=payload)
        if remoto:
            return CommandState.created.value, True
        row = s.commands.transition(command_id, CommandState.dispatched)
    _publish_command(s, row)
    return CommandState.dispatched.value, False


def _abrir_comando(s: AppState, instance_id: str, action: str, params: InstanceActionBody,
                   requested_by: str | None = None) -> tuple[Row, bool]:
    chave = params.idempotency_key or f"{instance_id}:{action}:{new_token()}"
    rt = s.devices.devices.get(instance_id)
    # ONDE o aparelho morava quando o comando foi aberto. Vale para TODO verbo, inclusive os de ADB puro, que saem
    # daqui pelo túnel e por isso nunca carimbam `worker_id` — `GET /api/commands` mostrava `worker_id=None` num
    # `open_app` que aconteceu na outra máquina, e o histórico não tinha como dizer onde.
    return s.commands.create(command_id=new_command_id(), instance_id=instance_id, verb=action,
                             idempotency_key=chave, requested_by=requested_by or _autor_sem_requisicao(),
                             host_worker_id=(rt.worker_id if rt is not None else None) or s.cfg.owner_id,
                             params={"app_id": params.app_id} if params.app_id else None)


def pedir_ciclo_de_vida(s: AppState, instance_id: str, verb: str, motivo: str, *, requested_by: str,
                        nivel: str = "info", idempotency_key: str | None = None) -> str | None:
    """O CENTRAL pede um verbo de ciclo de vida por conta própria, como se uma pessoa tivesse clicado. Devolve o
    id do comando aberto, ou `None` quando não há o que fazer.

    Um caminho só para os pedidos automáticos que existem — a remediação (`restart`), o rodízio
    (`start`/`wake`/`stop`/`hibernate` num aparelho de outra máquina) e o `apply` de `device.state` (fase H). Passa
    pelo mesmo `_precheck` e pelo mesmo despacho do painel de propósito: comando aberto no aparelho, worker em
    manutenção, verbo não suportado e IA no controle recusam aqui exatamente como recusariam lá — e a recusa fica no
    histórico do aparelho com o motivo, em vez de sumir num log.

    `idempotency_key`: sem ela (remediação e rodízio), cada pedido é um comando novo, como sempre foi. Com ela, a
    mesma chave devolve o comando original (`CommandStore.create`) — é o que torna o `apply` de um recurso seguro de
    repetir.
    """
    rt = s.devices.devices.get(instance_id)
    if rt is None or verb not in (rt.worker_verbs or []):
        # Sem worker que saiba executar o verbo neste aparelho não existe pedido automático: dizer isso no
        # cartão é mais honesto do que abrir um comando que ninguém pode executar.
        return None
    params = InstanceActionBody(confirm=True, idempotency_key=idempotency_key)
    row, repetido = _abrir_comando(s, instance_id, verb, params, requested_by=requested_by)
    if repetido:
        return str(row["id"])
    if (recusa := _precheck(s, rt, verb, params, row["id"])) is not None:
        _publish_command(s, s.commands.transition(row["id"], CommandState.rejected, reason=recusa[1]))
        return None
    s.bus.emit("log", f"{instance_id}: '{verb}' pedido automaticamente — {motivo}", level=nivel,
               instance_id=instance_id, data={"command_id": row["id"]})
    _marcar_entregue(s, rt, verb, row["id"], params)
    # Chamador SÍNCRONO (gancho do gerenciador de aparelhos): a publicação vira tarefa. Perder essa tarefa não
    # perde mais o comando — a linha do outbox já está gravada, e o dreno de partida a publica.
    asyncio.create_task(_despachar(s, row["id"]))
    return str(row["id"])


async def abrir_e_despachar(s: AppState, rt: DeviceRuntime, action: str, params: InstanceActionBody, *,
                            requested_by: str | None = None) -> tuple[Row, str, bool]:
    """Abre um verbo de ciclo de vida COMO O PAINEL abriria e o entrega: mesma cerca, mesmo pré-voo, mesmo outbox.

    Devolve `(comando, estado, repetido)`. A recusa do pré-voo vira `DespachoRecusado` 409 com o `command_id`, e o
    comando fica `rejected` no histórico do aparelho, como no clique. É a sequência da rota de ações
    (`api.instance_action`) sem o HTTP, para quem cria a instância e já abre o `create` no mesmo pedido
    (`POST /api/instances`) — sem duplicar a sequência nem entrar por `pedir_ciclo_de_vida`, que engole a recusa
    porque o pedido dela é automático e ninguém está esperando resposta.
    """
    row, repetido = _abrir_comando(s, rt.id, action, params, requested_by=requested_by)
    if repetido:
        return row, str(row["state"]), True
    if (recusa := _precheck(s, rt, action, params, row["id"])) is not None:
        codigo, porque = recusa
        _publish_command(s, s.commands.transition(row["id"], CommandState.rejected, reason=porque))
        raise DespachoRecusado(409, codigo, f"{rt.id}: {porque}.", command_id=row["id"])
    estado, _ = _marcar_entregue(s, rt, action, row["id"], params)
    await _despachar(s, row["id"])
    return row, estado, False


#: Estados declarados por um agente que descrevem um aparelho que NÃO está no ar. `unknown` fica de fora de
#: propósito: "não sei" não autoriza ligar nada.
FORA_DO_AR = {"stopped", "absent"}


def reconciliar_estado_desejado(s: AppState, worker_id: str, devices: Sequence[object]) -> list[str]:
    """Worker (re)conectou: os aparelhos dele que estavam para ficar no ar voltam a subir. Devolve os ids pedidos.

    Achados #137 e #38, o buraco do aceite 7 do lado de lá. Reiniciar o notebook do parque deixava os seis
    aparelhos fora: o agente voltava, declarava tudo `stopped` — e ninguém fazia nada com isso. O `desired_state`
    já existia e já era gravado para aparelho de worker (`set_desired_state` no `start`/`stop` remoto); o que
    faltava era alguém CONFRONTAR o que o worker declara com o que o central quer.

    Central-side de propósito, e não "o agente religa o que ele mesmo tinha ligado": quem é dono do estado
    desejado e da cerca é o central. Um agente que religasse por conta própria ligaria aparelho que uma pessoa
    tinha acabado de parar daqui, e sem cerca nenhuma na decisão.

    Uma vez por CONEXÃO (ver `_reconciliar_uma_vez`), nunca a cada batida: repetido de 10 em 10 s, isto viraria
    um pedido novo enquanto o aparelho ainda estivesse subindo. Todas as outras recusas (comando aberto, worker
    em manutenção, verbo não suportado, IA no controle) já são de `pedir_ciclo_de_vida`, e ficam no histórico do
    aparelho com o motivo.
    """
    pedidos: list[str] = []
    for d in devices:
        instance_id = getattr(d, "instance_id", None)
        if not instance_id or getattr(d, "state", None) not in FORA_DO_AR:
            continue
        rt = s.devices.devices.get(instance_id)
        if rt is None or rt.worker_id != worker_id:
            continue
        if rt.desired_state != InstanceState.online.value:
            continue                     # ninguém pediu este aparelho no ar: ligá-lo seria decisão nossa
        # `requested_by` próprio, e não `system`: `system` é a memória da escada de reparo
        # (`CommandStore.remediacoes_recentes`), e um religar de reconciliação contado como degrau aproximaria o
        # `reset` — que apaga a conta real logada no aparelho.
        if pedir_ciclo_de_vida(s, instance_id, "start", f"o worker {worker_id} voltou com o aparelho "
                               f"{getattr(d, 'state', '?')} e o estado desejado é online",
                               requested_by=REQUESTED_BY_RECONCILIACAO, nivel="warn") is not None:
            pedidos.append(instance_id)
    return pedidos


def _reconciliar_uma_vez(s: AppState, worker_id: str, link: WorkerLink, devices: Sequence[object]) -> None:
    """Dispara a reconciliação na PRIMEIRA batida que traz estado de verdade, e só nela.

    A marca vive no `link`, que morre com o socket: worker que reconecta ganha uma reconciliação nova, e worker
    que só está batendo não ganha nenhuma. `unknown` não conta — é o que o agente manda antes de a sondagem
    dele terminar, e agir sobre "não sei" ligaria aparelho que já está no ar.
    """
    if link.reconciliado or not any(getattr(d, "state", "unknown") != "unknown" for d in devices):
        return
    link.reconciliado = True
    if (voltando := reconciliar_estado_desejado(s, worker_id, devices)):
        s.bus.emit("log", f"Worker {worker_id}: religando {len(voltando)} aparelho(s) pelo estado desejado "
                          f"({', '.join(voltando)}).", level="warn")


#: `commands.requested_by` dos pedidos automáticos que NÃO são degrau da escada de reparo. Só a remediação usa
#: `system` (`remediar`); o resto tem nome próprio, como os recursos (`shared/convergence.REQUESTED_BY`) e o rodízio
#: (`scheduler`). Um reinício de saúde contado como degrau levaria, com mais dois defeitos, ao `reset`.
REQUESTED_BY_RECONCILIACAO = "reconciliacao"
REQUESTED_BY_SAUDE = "saude"

#: A escada de reparo automático: quantos `restart` antes de `reset`, e quanto esperar depois de esgotar.
DEGRAUS_DE_RESTART = 2
RETENTATIVA_APOS_ESCADA_S = 6 * 3600
JANELA_DA_ESCADA_H = 24.0
#: Quanto o reparo automático espera quando a máquina está sobrecarregada (o teto é `instances.remediation_host_cpu_max`).
ADIAMENTO_POR_HOSPEDEIRO_S = 600


def remediar(s: AppState, instance_id: str, motivo: str) -> str | None:
    """O aparelho degradou com `desired_state=online`: decide o DEGRAU e abre um comando RASTREÁVEL.

    Degraus, contados no histórico de comandos (`requested_by='system'`, 24 h): 1º e 2º `restart`; 3º `reset`
    (apaga os dados do AVD e sobe limpo — decisão do dono em 23/09, para todo aparelho que declare o verbo);
    esgotada a escada, o aparelho ganha `attention` "precisa de gente" e volta a ser tentado (`restart`) a cada
    6 h — nunca fica esquecido. Cada degrau vira evento `instance.remediation`, para o painel e o relatório de uso
    não confundirem reparo com comando manual.

    Antes: 2 restarts em memória e "a decisão é de uma pessoa" — para sempre, e zerado num reinício do backend.

    ADR-055: o `reset` automático NUNCA acontece em aparelho com conta — vínculo ativo ou marcador de conta travada.
    Em 24/09 o 3º degrau apagou a sessão do andre (conta real, viva); reset automático só vale para aparelho sem
    conta nenhuma. Com vínculo, o 3º degrau é "precisa do dono" + `stop` (parar não apaga nada, e o estado desejado
    vira `stopped`: a escada não volta sozinha). Com conta travada logada (quarentena) nem os `restart` acontecem —
    reiniciar religaria o app de uma conta morta —: é direto o "precisa do dono" + `stop`.
    """
    rt = s.devices.devices.get(instance_id)
    if rt is None:
        return None
    # Hospedeiro sobrecarregado: o convidado "degradou" porque a MÁQUINA não tem CPU, não porque o Android dele
    # adoeceu. Subir de degrau aí só piora (reiniciar é o momento mais pesado de um convidado) e, com a escada, chega
    # ao reset. Em 29/09 02:05–02:15Z o android-01 (lucas) levou restart e reset nessa situação, com a máquina
    # saturada por testes e o boot de outro aparelho (ADR-055). Espera a máquina aliviar e reconfere.
    metricas = s.devices.last_metrics
    local = not rt.worker_id or rt.worker_id == s.cfg.owner_id
    teto_cpu = float(s.cfg.file.instances.remediation_host_cpu_max)
    if local and metricas is not None and metricas.cpu_percent >= teto_cpu:
        s.devices.marcar_atencao(rt, f"Reparo adiado: esta máquina está com {metricas.cpu_percent:.0f}% de CPU; "
                                     f"reiniciar o aparelho agora não resolveria ({motivo}). Nova conferência em "
                                     f"{ADIAMENTO_POR_HOSPEDEIRO_S // 60} min.")
        s.devices.adiar_reparo(rt, ADIAMENTO_POR_HOSPEDEIRO_S)
        return None
    historico = s.commands.remediacoes_recentes(instance_id, janela_h=JANELA_DA_ESCADA_H)
    restarts = sum(1 for c in historico if c["verb"] == "restart")
    resets = sum(1 for c in historico if c["verb"] == "reset")
    paradas = sum(1 for c in historico if c["verb"] == "stop")
    marcador = s.social_repo.conta_travada_no_aparelho(instance_id)
    com_conta = marcador is not None or bool(s.social_repo.profiles_of_instance(instance_id))
    pode_resetar = "reset" in (rt.worker_verbs or []) and not rt.store and not com_conta
    degrau = len(historico) + 1
    if com_conta and (marcador is not None or restarts >= DEGRAUS_DE_RESTART):
        conta = (f"a conta @{marcador['handle']} está travada e logada nele (quarentena)" if marcador is not None
                 else "há conta vinculada nele, e o reset apagaria a sessão dela")
        s.devices.marcar_atencao(rt, f"Precisa do dono: {motivo}. O reparo automático parou aqui porque {conta}; "
                                     "nenhum reset automático acontece em aparelho com conta (ADR-055).")
        if paradas or "stop" not in (rt.worker_verbs or []):
            s.devices.adiar_reparo(rt, RETENTATIVA_APOS_ESCADA_S)
            return None
        verbo = "stop"
    elif restarts < DEGRAUS_DE_RESTART:
        verbo = "restart"
    elif pode_resetar and resets == 0:
        verbo = "reset"
    else:
        ultimo = historico[-1] if historico else None
        quando = parse_iso(ultimo["created_at"]) if ultimo else None
        if quando is not None and (now() - quando).total_seconds() < RETENTATIVA_APOS_ESCADA_S:
            s.devices.marcar_atencao(rt, f"Precisa de gente: {len(historico)} reparo(s) automático(s) em 24 h não "
                                         f"resolveram ({motivo}). Nova tentativa automática em até 6 h.")
            s.devices.adiar_reparo(rt, RETENTATIVA_APOS_ESCADA_S)
            return None
        verbo = "restart"                # nova rodada depois do prazo
    cid = pedir_ciclo_de_vida(s, instance_id, verbo, motivo, requested_by="system", nivel="warn")
    if cid is None:
        return None
    s.bus.emit("instance.remediation", f"{instance_id}: reparo automático, {degrau}º degrau ({verbo}) — {motivo}",
               level="warn", instance_id=instance_id,
               data={"degrau": degrau, "verb": verbo, "command_id": cid, "motivo": motivo})
    return cid


def remediar_reiniciando(s: AppState, instance_id: str, motivo: str) -> str | None:
    """Nome antigo do gancho `on_remediation_needed`; a decisão do degrau é de `remediar`."""
    return remediar(s, instance_id, motivo)


async def _entregar_cancelamento(s: AppState, row: Row) -> tuple[bool, str]:
    """Leva o pedido a QUEM ESTÁ EXECUTANDO, nos dois caminhos. Devolve `(interrompeu algo, o que dizer)`.

    O estado já mudou antes desta função: entregar é o segundo passo, e falhar aqui não desfaz o pedido. O
    comando fica em `cancel_requested` até o desfecho de verdade chegar — pedir não é ter cancelado.
    """
    command_id, verbo = row["id"], row["verb"]
    rt = s.devices.devices.get(row["instance_id"])
    # `worker_id` só é carimbado no envio: um cancelamento que chega ANTES disso ainda precisa achar o agente,
    # por isso o dono do aparelho também vale. Cancel de id desconhecido é ignorado pelo agente, sem efeito.
    worker_id = row["worker_id"] or (rt.worker_id if rt is not None else None)
    remoto = bool(row["worker_id"]) or (rt is not None and _para_worker(rt, verbo))
    if remoto and worker_id:
        try:
            if await s.workers.cancel(worker_id, command_id):
                if worker_id == s.cfg.owner_id:
                    # O worker local recebe o pedido pelo mesmo contrato, e o ponto seguro de cancelamento desta
                    # máquina continua sendo `rt.tasks["boot"]` — cancelar só a espera deixaria o emulador
                    # subindo às escondidas depois de o painel dizer "cancelado".
                    return True, ("o pedido foi entregue ao executor deste servidor: um boot em andamento nesta "
                                  "máquina é interrompido, e o desfecho continua vindo de quem executa")
                return True, "o pedido foi enviado ao worker; o desfecho continua vindo dele"
        except Exception:  # noqa: BLE001 - canal caindo no meio do envio não desfaz o pedido registrado
            log.exception("envio do cancelamento de %s ao worker %s", command_id, worker_id)
        return False, ("o worker não está conectado: o pedido fica registrado e o comando só fecha quando o "
                       "desfecho chegar")
    if rt is not None and verbo in VERBOS_QUE_ESPERAM_O_BOOT:
        tarefa = rt.tasks.get("boot")
        if tarefa is not None and not tarefa.done():
            # O boot roda em tarefa própria (`devices/manager.py`), e é ELA que precisa parar — cancelar a
            # tarefa do comando só abandonaria a espera, deixando o emulador subindo às escondidas.
            tarefa.cancel()
            return True, "o boot em andamento nesta máquina foi interrompido"
    return False, ("este verbo não tem ponto seguro de cancelamento: o pedido fica registrado e o comando fecha "
                   "como cancelado se ainda não tiver começado a agir; senão vale o desfecho real")


def _anunciar_inflight(s: AppState, worker_id: str, inflight: list[str]) -> None:
    """O agente reconectou dizendo o que AINDA está executando. Queda de canal não cancela trabalho, então um
    comando que o central marcou `uncertain` pode estar vivo do outro lado — e quem olha o painel precisa saber
    disso antes de decidir repetir. O estado não muda (`uncertain` só sai por desfecho de verdade)."""
    for command_id in inflight[:50]:
        try:
            row = s.commands.get(command_id)
            if row is None or row["worker_id"] not in (None, worker_id):
                continue
            dto = command_dto(row)
            s.bus.emit("command.updated",
                       f"{dto.instance_id}: {dto.verb} — o worker reconectou e ainda está executando este comando",
                       level="warn", instance_id=dto.instance_id,
                       data={"command": dto.model_dump(), "inflight": True})
        except Exception:  # noqa: BLE001 - aviso nunca derruba o canal
            log.exception("inflight do comando %s", command_id)


async def _tratar_mensagem_do_worker(s: AppState, worker_id: str, link: WorkerLink,
                                     msg: Heartbeat | Ack | Progress | Result | Hello | ObserveResult) -> None:
    """Uma mensagem do worker, traduzida em estado persistido. Nada aqui pode escapar: exceção neste ponto cairia
    no `except` de fora e derrubaria o canal do worker por causa de um erro de banco."""
    try:
        if isinstance(msg, Heartbeat):
            s.workers.on_heartbeat(worker_id, msg, link)
            # A batida traz também o que o agente DECLARA sobre cada aparelho (imagem, nível de API, ABIs, GMS).
            # É o que permite ao pré-voo recusar com explicação antes de agendar, em vez de descobrir no meio.
            s.devices.capacidades_do_worker(worker_id, msg.devices)
            s.devices.conferir_inventario(worker_id, msg.devices)
            # A batida traz o estado de cada aparelho daquela máquina: é o instante em que chega informação nova
            # capaz de fechar um comando incerto. Era exatamente o caso vivo — `start` incerto por prazo de boot
            # com o aparelho relatado `running` na batida seguinte, e o comando ficando incerto para sempre.
            reconciliar_incertos(s)
            # E é também o instante em que dá para saber o que aquela máquina PERDEU num reboot. No `hello` não
            # dava: `agent._declarados()` manda tudo como `unknown` de propósito (sondar seis aparelhos custa
            # ~28 s e o handshake morreria antes). Então a reconciliação espera a primeira batida que traz
            # estado de verdade, e acontece UMA vez por conexão — na batida seguinte o aparelho está `booting` e
            # um segundo pedido só disputaria com o primeiro.
            _reconciliar_uma_vez(s, worker_id, link, msg.devices)
        elif isinstance(msg, Ack):
            # O ACK deixa de morrer num `set` em memória: "o worker RECEBEU" vira estado no banco, com hora. É o
            # que separa, numa queda, "não sabemos se chegou" de "chegou e não sabemos o efeito".
            s.workers.on_ack(worker_id, msg.command_id)
            _mudar_estado_do_worker(s, msg.command_id, worker_id, CommandState.acked,
                                    de={CommandState.dispatched})
        elif isinstance(msg, Progress):
            _progresso_do_worker(s, msg.command_id, worker_id, msg.message)
        elif isinstance(msg, Result):
            await _desfecho_do_worker(s, worker_id, link, msg)
        elif isinstance(msg, ObserveResult):
            # Falha da captura na origem, ou as dimensões de um pedido `so_dimensoes`. A imagem vem pelo canal de
            # mídia, nunca por aqui.
            s.workers.captura.on_resultado(worker_id, msg)
        elif isinstance(msg, Hello):
            # Re-declaração: o worker mudou de inventário ou de capacidade sem reconectar. Vale como batida,
            # e não repete autenticação — quem já está dentro do canal não se reautentica a cada mensagem.
            s.workers.on_heartbeat(worker_id, Heartbeat(devices=msg.devices, resources=msg.resources), link)
            s.devices.capacidades_do_worker(worker_id, msg.devices)
            s.devices.conferir_inventario(worker_id, msg.devices)
    except Exception:  # noqa: BLE001 - erro ao registrar não pode custar a conexão do worker
        log.exception("mensagem %s do worker %s", type(msg).__name__, worker_id)


def _mudar_estado_do_worker(s: AppState, command_id: str, worker_id: str, alvo: CommandState,
                            de: set[CommandState]) -> Row | None:
    """Transição pedida pelo worker, só a partir dos estados em que ela faz sentido. Fora deles não é erro: é
    mensagem fora de ordem, ou comando já encerrado por outra via."""
    row = s.commands.get(command_id)
    if row is None or row["worker_id"] not in (None, worker_id) or CommandState(row["state"]) not in de:
        return None
    try:
        novo = s.commands.transition(command_id, alvo, worker_id=worker_id)
    except InvalidCommandTransition:
        return None
    _publish_command(s, novo)
    return novo


def _progresso_do_worker(s: AppState, command_id: str, worker_id: str, mensagem: str) -> None:
    """Primeiro progresso do worker = `running`. Antes de existir isto, `running` era gravado no central ANTES de
    o comando sair, e `started_at` ficava a menos de 1 ms de `dispatched_at` em todos os comandos reais.

    E o progresso deixa de ser um evento `log` solto: vai como `command.updated`, com a instância e o comando,
    que é o que a interface sabe mostrar.
    """
    # Esperar vaga na fila de boot do worker NÃO é executar: o comando continua `dispatched`/`acked` (carimbar
    # `running` aqui seria o mesmo carimbo falso que saiu do despacho), e o prazo é empurrado — porque contar a
    # espera na fila como tempo de boot transformava a proteção contra ANR em "resultado incerto".
    na_fila = MARCA_DE_FILA in mensagem
    if na_fila:
        linha = s.commands.get(command_id)
        verbo = linha["verb"] if linha is not None else ""
        s.workers.adiar(worker_id, command_id, PRAZO_POR_VERBO.get(verbo, PRAZO_PADRAO_S))
    row = None if na_fila else _mudar_estado_do_worker(s, command_id, worker_id, CommandState.running,
                                                       de={CommandState.dispatched, CommandState.acked})
    row = row or s.commands.get(command_id)
    if row is None:
        s.bus.emit("log", f"{command_id}: {mensagem}")
        return
    dto = command_dto(row)
    s.bus.emit("command.updated", f"{dto.instance_id}: {dto.verb} — {mensagem}", level="info",
               instance_id=dto.instance_id, data={"command": dto.model_dump(), "progress": mensagem})


#: Desfecho do worker → estado de comando. Um mapa, não um `if` espalhado por dois arquivos.
DESFECHO = {"succeeded": CommandState.succeeded, "failed": CommandState.failed,
            "uncertain": CommandState.uncertain, "cancelled": CommandState.cancelled}


async def _desfecho_do_worker(s: AppState, worker_id: str, link: WorkerLink, msg: Result) -> None:
    """O resultado do worker, inclusive o TARDIO.

    Enquanto o comando está em voo, quem trata é `on_result` (o futuro que `dispatch` espera). Se o canal caiu no
    meio, o central já marcou `uncertain` e não havia mais ninguém esperando: o resultado que o agente produziu
    depois era descartado, e "a rede piscou" continuava significando "a ação falhou". Agora o comando é
    procurado no BANCO e a transição `uncertain → succeeded/failed` — que a tabela de estados sempre permitiu —
    é aplicada, conferindo cerca e worker.

    O `result_ack` sai SEMPRE que a mensagem foi tratada, mesmo quando não mudou nada (comando já terminal, cerca
    velha): senão o agente reenviaria o mesmo resultado para sempre.
    """
    tratado = s.workers.on_result(worker_id, msg, fence=msg.fence)
    if not tratado:
        tratado = _resultado_tardio(s, worker_id, msg)
    with contextlib.suppress(Exception):
        await link.send(ResultAck(command_id=msg.command_id).model_dump())


def _resultado_tardio(s: AppState, worker_id: str, msg: Result) -> bool:
    row = s.commands.get(msg.command_id)
    if row is None:
        return False
    if row["worker_id"] != worker_id:
        log.warning("resultado tardio de %s para comando de %s: recusado", worker_id, row["worker_id"])
        return False
    if msg.fence is None or int(row["fence"]) != int(msg.fence):
        log.warning("resultado tardio de %s com cerca %s (esperada %s): recusado", worker_id, msg.fence,
                    row["fence"])
        return False
    alvo = DESFECHO.get(msg.outcome)
    if alvo is None or CommandState(row["state"]) is alvo:
        return True                     # nada a fazer, mas a mensagem foi tratada: confirme e deixe o agente em paz
    try:
        motivo = msg.reason or "desfecho recebido do worker depois da reconexão"
        novo = s.commands.transition(msg.command_id, alvo, reason=motivo, result=msg.data, worker_id=worker_id)
    except InvalidCommandTransition:
        log.info("resultado tardio de %s: comando %s já estava em %s", worker_id, msg.command_id, row["state"])
        return True
    _publish_command(s, novo)
    s.bus.emit("log", f"{novo['instance_id']}: o worker reconectou e entregou o desfecho de {msg.command_id} "
                      f"({alvo.value}).")
    return True
