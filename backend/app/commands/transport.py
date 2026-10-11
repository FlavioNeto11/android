"""Transporte do despacho: por onde a ordem do outbox SAI.

Dois transportes, uma interface, escolhidos por `COMMAND_TRANSPORT` no `.env`:

* **`websocket`** (padrão, o que roda hoje): a ordem é entregue DENTRO deste processo, ao mesmo
  `_do_action`/canal do worker de sempre. Nada muda no comportamento; o que muda é que agora a entrega vem do
  outbox, então uma queda entre aceitar e entregar não perde mais o comando.
* **`nats`** (atrás de bandeira): a ordem é publicada no assunto da RÉPLICA que hospeda aquele aparelho
  (`instances.hosted_by`) em NATS JetStream, e consumida por ela. É o que permite ao painel de uma réplica mandar
  num aparelho da outra sem que as duas precisem estar no mesmo processo.

**A bandeira fica desligada até um comando real atravessar.** Este repositório não tem broker: o transporte
NATS está escrito e não foi exercitado contra um servidor de verdade. Ligá-lo sem broker falha alto, na
partida, e não em silêncio no meio de um comando — é para isso que `start()` conecta antes de qualquer
despacho.

**Nenhum dos dois é exatamente-uma-vez.** JetStream entrega ao menos uma vez; quem impede o efeito duplo é o
diário do agente (`worker/diario.py`), que devolve o desfecho guardado de um `command_id` já visto em vez de
reexecutar o verbo. Ver `docs/parque-distribuido.md`.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Awaitable, Callable, Protocol

from ..devices.verbs import PRAZO_PADRAO_S, PRAZO_POR_VERBO

log = logging.getLogger("poc.commands")

#: Quem recebe a ordem publicada. Recebe o envelope do outbox e é responsável por executá-la até o desfecho.
Handler = Callable[[dict[str, Any]], Awaitable[None]]

WEBSOCKET = "websocket"
NATS = "nats"

#: Prazo do ack do JetStream: o maior prazo de verbo (600 s de `restart`/`reset`) mais folga. Abaixo disso, um
#: comando longo e saudável seria reentregue no meio — e o padrão do servidor NATS (30 s), que era o que valia
#: com `config=None`, reentregaria todo `start`. Não é o único cinto: ver `_manter_vivo`.
ACK_WAIT_S = max(max(PRAZO_POR_VERBO.values()), PRAZO_PADRAO_S) + 60.0


class CommandTransport(Protocol):
    name: str

    async def start(self, handler: Handler) -> None: ...

    async def publish(self, envelope: dict[str, Any]) -> None: ...

    async def close(self) -> None: ...


class LocalTransport:
    """Entrega em processo — o caminho de hoje, agora alimentado pelo outbox.

    `publish` agenda o handler e volta: quem despacha não espera o comando terminar, exatamente como antes. As
    tarefas ficam registradas para o desligamento poder esperá-las em vez de abandoná-las no meio.
    """

    name = WEBSOCKET

    def __init__(self) -> None:
        self._handler: Handler | None = None
        self._tarefas: set[asyncio.Task[None]] = set()

    async def start(self, handler: Handler) -> None:
        self._handler = handler

    async def publish(self, envelope: dict[str, Any]) -> None:
        handler = self._handler
        if handler is None:
            raise RuntimeError("transporte local usado antes de start()")
        tarefa = asyncio.create_task(handler(envelope), name=f"cmd:{envelope.get('command_id')}")
        self._tarefas.add(tarefa)
        tarefa.add_done_callback(self._tarefas.discard)

    async def close(self) -> None:
        for t in list(self._tarefas):
            t.cancel()
        self._tarefas.clear()
        self._handler = None


class NatsJetStreamTransport:
    """Assunto por RÉPLICA hospedeira em JetStream, com ack só depois do desfecho.

    O ack vai DEPOIS de o handler voltar: um processo que caia no meio de um comando deixa a mensagem sem ack, e
    o JetStream a reentrega. É o "ao menos uma vez" de que o diário do agente é o par obrigatório.

    **Quem consome é a réplica que hospeda o aparelho**, e não o worker: o assunto é `comandos.<hosted_by>`. O
    agente remoto não fala NATS — fala WebSocket com a réplica que segura o canal dele e o túnel do aparelho, e
    essa réplica é a `hosted_by`. Publicar em `comandos.<worker_id>` (o que havia) deixava todo comando de
    aparelho remoto sem consumidor: nenhuma réplica assina o assunto de um worker.
    """

    name = NATS

    def __init__(self, url: str, *, owner_id: str, stream: str = "COMANDOS", assunto: str = "comandos",
                 ack_wait_s: float = ACK_WAIT_S):
        self.url = url
        self.owner_id = owner_id
        self.stream = stream
        self.assunto = assunto
        self.ack_wait_s = ack_wait_s
        #: A cada quanto o consumidor avisa "ainda estou nisto" (`in_progress`). Um terço do prazo: duas batidas
        #: podem se perder antes de o JetStream desistir.
        self.intervalo_de_vida_s = ack_wait_s / 3
        self._nc: Any = None
        self._js: Any = None
        self._sub: Any = None

    def _meu_assunto(self) -> str:
        return f"{self.assunto}.{self.owner_id}"

    def destino(self, envelope: dict[str, Any]) -> str:
        """A réplica que deve executar a ordem: a que hospeda o aparelho.

        `hosted_by` nulo é aparelho que ninguém reivindicou (banco anterior à 027, ou um backend só) — e aí é de
        quem publica, a mesma regra de toda leitura por hospedeiro. O `worker_id` do envelope NUNCA é destino:
        ele diz em que máquina o emulador roda, não que processo segura o canal com ela.
        """
        return str(envelope.get("hosted_by") or self.owner_id)

    def _config_do_consumidor(self) -> Any:
        """`ack_wait` aplicado de verdade: estava declarado e nunca chegava ao consumidor (`config=None`).

        Em segundos: o `ConsumerConfig` do nats-py converte para nanossegundos ao serializar."""
        from nats.js.api import ConsumerConfig  # noqa: PLC0415 - dependência opcional, como em `start`
        return ConsumerConfig(ack_wait=self.ack_wait_s)

    async def _manter_vivo(self, msg: Any) -> None:
        """Enquanto o handler trabalha, adia a reentrega.

        O handler pode passar do `ack_wait` sem nada estar errado: o comando espera o cadeado do aparelho (um
        `reset` de 600 s na frente) ou o agente adia o prazo pela fila de boot. Uma reentrega nesse meio tempo
        encontraria o comando ainda em `created` e o despacharia DE NOVO. Só consumidor morto deixa de mandar
        `in_progress` — e é exatamente aí que a reentrega é devida.
        """
        while True:
            await asyncio.sleep(self.intervalo_de_vida_s)
            try:
                await msg.in_progress()
            except Exception:                 # noqa: BLE001 - conexão piscando: a próxima volta tenta de novo
                log.debug("in_progress falhou; nova tentativa na próxima volta", exc_info=True)

    async def start(self, handler: Handler) -> None:
        try:
            import nats                       # noqa: PLC0415 - dependência opcional: só quem liga a bandeira paga
        except ModuleNotFoundError as exc:    # pragma: no cover - depende do ambiente
            raise RuntimeError(
                "COMMAND_TRANSPORT=nats exige o pacote 'nats-py' instalado e um servidor NATS com JetStream "
                "no ar. Veja docs/parque-distribuido.md; para voltar ao transporte de sempre, apague a "
                "variável do .env.") from exc
        self._nc = await nats.connect(self.url, name=f"poc-{self.owner_id}")
        self._js = self._nc.jetstream()
        await self._js.add_stream(name=self.stream, subjects=[f"{self.assunto}.*"])

        async def _entregar(msg: Any) -> None:
            try:
                envelope = json.loads(msg.data.decode("utf-8"))
            except ValueError:
                log.exception("mensagem ilegível em %s; descartada", self._meu_assunto())
                await msg.term()
                return
            vivo = asyncio.create_task(self._manter_vivo(msg))
            try:
                await handler(envelope)
            except Exception:                 # noqa: BLE001 - sem ack, o JetStream reentrega
                log.exception("comando %s falhou no consumo; sem ack", envelope.get("command_id"))
                return
            finally:
                vivo.cancel()
                # Não `suppress(CancelledError)`: ele engolia o cancelamento da PRÓPRIA entrega se chegasse enquanto o aviso de vida morria, e o
                # `ack` abaixo confirmava uma mensagem cujo consumidor estava sendo derrubado (31.347; mesma corrida do `Agent._sessao`).
                await asyncio.gather(vivo, return_exceptions=True)
            await msg.ack()

        # Consumidor durável que JÁ existe no servidor não é reconfigurado por `subscribe`: mudar o `ack_wait` de
        # uma instalação que já ligou a bandeira exige apagar o consumidor `poc-<owner>` antes de subir.
        self._sub = await self._js.subscribe(
            self._meu_assunto(), durable=f"poc-{self.owner_id}".replace(".", "_"), cb=_entregar,
            manual_ack=True, config=self._config_do_consumidor())
        log.info("transporte NATS ligado em %s (assunto %s, ack_wait %.0f s)", self.url, self._meu_assunto(),
                 self.ack_wait_s)

    async def publish(self, envelope: dict[str, Any]) -> None:
        if self._js is None:
            raise RuntimeError("transporte NATS usado antes de start()")
        destino = self.destino(envelope)
        # `Nats-Msg-Id` = o comando: o outbox republica o que caiu entre publicar e marcar `sent`, e a janela de
        # duplicata do stream (2 min por padrão) descarta a segunda cópia antes de ela virar uma segunda entrega.
        cid = str(envelope.get("command_id") or "")
        ack = await self._js.publish(f"{self.assunto}.{destino}", json.dumps(envelope).encode("utf-8"),
                                     headers={"Nats-Msg-Id": cid} if cid else None)
        log.debug("comando %s publicado em %s (seq %s)", envelope.get("command_id"), destino,
                  getattr(ack, "seq", None))

    async def close(self) -> None:
        if self._sub is not None:
            try:
                await self._sub.unsubscribe()
            except Exception:                 # noqa: BLE001 - desligar nunca derruba o backend
                log.debug("falha ao cancelar a assinatura do NATS", exc_info=True)
            self._sub = None
        if self._nc is not None:
            try:
                await self._nc.drain()
            except Exception:                 # noqa: BLE001
                log.debug("falha ao drenar a conexão do NATS", exc_info=True)
            self._nc = None
            self._js = None


def build_transport(kind: str, *, owner_id: str, url: str | None) -> CommandTransport:
    """Escolhe o transporte pela configuração. Desconhecido é erro de partida, não conversão silenciosa."""
    if kind == WEBSOCKET:
        return LocalTransport()
    if kind == NATS:
        if not url:
            raise RuntimeError("COMMAND_TRANSPORT=nats exige NATS_URL (ex.: nats://127.0.0.1:4222).")
        return NatsJetStreamTransport(url, owner_id=owner_id)
    raise RuntimeError(f"COMMAND_TRANSPORT desconhecido: {kind!r} (use 'websocket' ou 'nats').")
