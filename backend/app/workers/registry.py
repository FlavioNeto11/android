"""Registro dos workers: identidade, capacidade, saúde e o canal por onde o comando chega até eles.

Três coisas que o desenho protege de propósito:

1. **Inscrição é de uso único e prazo curto.** O instalador cola um token, o agente o troca por credencial
   permanente, e o token morre. Ninguém digita segredo de longa duração numa máquina nova.
2. **Ausência de batida é o que marca indisponível — não o socket fechado.** Socket cai por rede piscando; isso
   não significa que o worker parou de trabalhar, e tratar as duas coisas como a mesma foi o defeito que o
   projeto de referência (STF) levou anos arrastando.
3. **Cerca (fencing).** Resultado com cerca velha é recusado: um worker que voltou do limbo não sobrescreve o
   estado atual.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from typing import Any, Awaitable, Callable

from ..commands.states import COMMAND_OPEN
from ..db import Database, Row, dumps, loads
from ..models import WorkerDTO
from ..util import iso_in, now, now_iso, parse_iso, truncate
from .portao import PortaoDoWorker
from .protocol import (PROTOCOL_MIN, PROTOCOL_VERSION, Dispatch, Heartbeat, Hello, Result, WorkerDevice,
                       WorkerResources, Welcome)
from ..version import DESCONHECIDO, agent_version

log = logging.getLogger("poc.workers")

#: Prazo padrão da batida e quantas perdidas toleram antes de marcar offline. Configurável no `welcome`.
HEARTBEAT_S = 10.0
BATIDAS_PERDIDAS = 3
#: Validade de um token de inscrição. Curto de propósito: ele existe para a janela da instalação, não para viver.
INSCRICAO_TTL_S = 3600.0
#: Acima disto, o desvio de relógio contra o central deixa de ser folga do lease (achado #142: 120 s de posse
#: cabem segundos de desvio, não minutos) e vira `degraded` visível na Infraestrutura.
CLOCK_OFFSET_LIMIT_S = 5.0
CLOCK_DRIFT_PREFIX = "relógio desalinhado"
#: Piso de recurso da máquina do worker. Abaixo dele, subir mais um aparelho lá é pedir que ela engasgue: o
#: próprio agente já recusa por RAM (`worker/executor._guarda_de_ram`), mas quem escolhe ONDE ligar é o central,
#: e mandar o pedido para receber a recusa custa um comando, uma ida e volta e uma linha de histórico por tick.
#: Disco entra pelo mesmo motivo e nunca tinha sido porta em lugar nenhum (achado #41).
PISO_RAM_MB = 2048
PISO_DISCO_GB = 10.0
RECURSO_BAIXO_PREFIX = "recurso no limite"
#: Batida mais velha que isto descreve um passado que ninguém confirmou: os recursos dela deixam de ser porta
#: (quem decide "indisponível" continua sendo `reap`, pela ausência de batida).
BATIDA_VELHA_S = HEARTBEAT_S * 3


class WorkerCapacity:
    """O que o central precisa saber para decidir se liga mais um aparelho NAQUELA máquina.

    Uma pergunta, uma resposta: vagas declaradas, recursos da última batida e a idade dela. `stale` existe
    porque recurso velho não é recurso — com a batida vencida, o central deixa de gatear por RAM/disco em vez
    de decidir sobre um número que descreve outro momento.
    """

    __slots__ = ("worker_id", "name", "connected", "maintenance", "max_slots", "ram_free_mb", "disk_free_gb",
                 "last_seen_at", "stale", "degraded_detail")

    def __init__(self, worker_id: str, name: str, *, connected: bool, maintenance: bool, max_slots: int,
                 ram_free_mb: int | None, disk_free_gb: float | None, last_seen_at: str | None, stale: bool,
                 degraded_detail: str | None) -> None:
        self.worker_id, self.name = worker_id, name
        self.connected, self.maintenance, self.max_slots = connected, maintenance, max_slots
        self.ram_free_mb, self.disk_free_gb = ram_free_mb, disk_free_gb
        self.last_seen_at, self.stale, self.degraded_detail = last_seen_at, stale, degraded_detail

    def sem_recurso(self) -> str | None:
        """`None` quando dá para subir mais um aparelho lá; senão, a frase que explica por que não.

        Batida velha devolve `None` de propósito: "não sei" não é "não pode".
        """
        if self.stale:
            return None
        if self.ram_free_mb is not None and self.ram_free_mb < PISO_RAM_MB:
            return (f"worker '{self.name}' está com {self.ram_free_mb} MB de RAM livre "
                    f"(piso: {PISO_RAM_MB} MB)")
        if self.disk_free_gb is not None and self.disk_free_gb < PISO_DISCO_GB:
            return (f"worker '{self.name}' está com {self.disk_free_gb:.1f} GB de disco livre "
                    f"(piso: {PISO_DISCO_GB:.0f} GB)")
        return None


def recurso_no_limite(res: WorkerResources | None) -> str | None:
    """A frase de `degraded` por recurso, ou `None`. Uma função só, usada pela batida e pela capacidade."""
    if res is None:
        return None
    if res.ram_free_mb is not None and res.ram_free_mb < PISO_RAM_MB:
        return f"{RECURSO_BAIXO_PREFIX}: {res.ram_free_mb} MB de RAM livre (piso: {PISO_RAM_MB} MB)"
    if res.disk_free_gb is not None and res.disk_free_gb < PISO_DISCO_GB:
        return f"{RECURSO_BAIXO_PREFIX}: {res.disk_free_gb:.1f} GB de disco livre (piso: {PISO_DISCO_GB:.0f} GB)"
    return None


def _hash(token: str) -> str:
    """SHA-256 basta: o token tem 32 bytes de entropia, então não há dicionário a proteger contra."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class WorkerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class WorkerLink:
    """Conexão viva com um worker. Some quando o socket cai; a linha no banco permanece."""

    def __init__(self, worker_id: str, send: Callable[[dict[str, Any]], Awaitable[None]],
                 fechar: Callable[[], Awaitable[None]] | None = None):
        self.worker_id = worker_id
        self.send = send
        #: Fecha o WebSocket DESTE link. `attach` usa para encerrar a conexão anterior do mesmo worker em vez de
        #: deixar dois canais vivos disputando o mesmo aparelho.
        self.fechar = fechar
        #: Comandos despachados e ainda sem desfecho: id → (cerca, futuro do resultado).
        self.pendentes: dict[str, tuple[int, asyncio.Future[Result]]] = {}
        #: Comandos cujo recebimento o worker confirmou.
        self.confirmados: set[str] = set()
        #: O estado desejado dos aparelhos deste worker já foi reconciliado NESTA conexão? Mora no link, e não
        #: no registro, porque é por conexão: worker que volta de um reboot ganha uma reconciliação nova, e
        #: worker que só está batendo não ganha nenhuma. Ver `api._reconciliar_uma_vez`.
        self.reconciliado = False
        #: Prazo de cada comando em voo (relógio monotônico do laço). É MUTÁVEL de propósito: o agente pode dizer
        #: que o aparelho está esperando vaga na fila de boot dele, e esperar na fila não é falhar.
        self.prazos: dict[str, float] = {}

    def encerrar(self, motivo: str) -> None:
        """Socket caiu. Quem estava em voo vira INCERTO, nunca falha: o worker pode ter agido."""
        for cid, (_, fut) in list(self.pendentes.items()):
            if not fut.done():
                fut.set_result(Result(command_id=cid, outcome="uncertain", reason=motivo))
        self.pendentes.clear()


class WorkerRegistry:
    def __init__(self, db: Database, *, on_change: Callable[[str], None] | None = None,
                 on_metrics: Callable[[str, WorkerResources | None], None] | None = None):
        self.db = db
        self.live: dict[str, WorkerLink] = {}
        #: Chamado com o worker_id quando algo observável muda (estado, detalhe, inventário de aparelhos), para
        #: o painel receber evento PERSISTIDO. Recurso (CPU/RAM/disco) não entra aqui — ver `on_metrics`
        #: (achados #17/#143: toda batida chamava isto, e 57% do log de eventos era só isto).
        self.on_change = on_change or (lambda _wid: None)
        #: Chamado a CADA batida com os recursos declarados, para o painel atualizar CPU/RAM/disco ao vivo sem
        #: gravar nada: quem chama publica como evento EFÊMERO (`worker.metrics`).
        self.on_metrics = on_metrics or (lambda _wid, _res: None)
        #: Freio do handshake ainda não autenticado. Um por processo; ver `workers/portao.py`.
        self.portao = PortaoDoWorker()
        #: Capacidade declarada por worker no `Hello`, viva enquanto ele estiver ligado. Fica em memória de
        #: propósito: é declaração da máquina dele, e vale enquanto ela está lá — a cada (re)conexão ele diz de
        #: novo. O pré-voo pergunta aqui em vez de consultar o `config.yaml` DESTE servidor.
        self.hibernacao: dict[str, bool] = {}
        #: Aceleração de virtualização declarada no `Hello` (`kvm`, `kvm-inacessivel`, `kvm-ausente`, ou ausente
        #: no Windows). Em memória pelo mesmo motivo do acima: é o estado da máquina dele agora.
        self.aceleracao: dict[str, str] = {}
        #: Id do worker que É este servidor (`workers/local.py`). Guardado aqui porque duas operações do painel
        #: não fazem sentido sobre ele: remover apagaria a linha do próprio central (e soltaria o `worker_id` de
        #: todos os aparelhos locais), e rotacionar credencial trocaria um segredo que ninguém usa.
        self.local_worker_id: str | None = None

    # ------------------------------------------------------------------ inscrição
    def criar_inscricao(self, label: str | None = None, ttl_s: float = INSCRICAO_TTL_S) -> str:
        """Devolve o token EM CLARO uma única vez. Só o hash é guardado."""
        token = secrets.token_urlsafe(32)
        self.db.execute(
            "INSERT INTO worker_enrollments(token_hash, label, created_at, expires_at) VALUES (?,?,?,?)",
            (_hash(token), truncate(label, 120), now_iso(), iso_in(ttl_s)))
        return token

    def _consumir_inscricao(self, token: str, worker_id: str) -> None:
        linha = self.db.one("SELECT * FROM worker_enrollments WHERE token_hash=?", (_hash(token),))
        if linha is None:
            raise WorkerError("unknown_enrollment", "Token de inscrição desconhecido.")
        if linha["used_at"]:
            raise WorkerError("enrollment_used", "Este token de inscrição já foi usado; gere outro no painel.")
        venceu = parse_iso(linha["expires_at"])
        if venceu is not None and venceu < now():
            raise WorkerError("enrollment_expired", "Token de inscrição vencido; gere outro no painel.")
        self.db.execute("UPDATE worker_enrollments SET used_at=?, used_by=? WHERE token_hash=?",
                        (now_iso(), worker_id, _hash(token)))

    # ------------------------------------------------------------------ conexão
    def autenticar(self, hello: Hello, *, token: str | None, enrollment: str | None) -> str:
        """Confere quem é o worker e devolve a credencial permanente quando ele acaba de se inscrever.

        Dois caminhos: **inscrição** (primeira vez, token de uso único → devolve credencial) e **credencial**
        (todas as vezes seguintes). Nunca os dois.
        """
        if self.local_worker_id is not None and hello.worker_id == self.local_worker_id:
            # A linha do central existe e tem o hash de um segredo que nunca foi revelado: o agente de fora
            # cairia em `bad_credential` de qualquer jeito. A recusa explícita evita que alguém conclua que é
            # problema de credencial e fique rotacionando token atrás de um id que nunca poderia ser usado.
            raise WorkerError("reserved_worker_id", f"'{hello.worker_id}' é o id deste próprio servidor "
                                                    "(OWNER_ID); escolha outro id para o agente.")
        if hello.protocol > PROTOCOL_VERSION:
            raise WorkerError("protocol_too_new", f"O agente fala o protocolo {hello.protocol} e este servidor "
                                                  f"fala {PROTOCOL_VERSION}; atualize o servidor.")
        if hello.protocol < PROTOCOL_MIN:
            raise WorkerError("protocol_too_old", f"O agente fala o protocolo {hello.protocol} e este servidor "
                                                  f"atende a partir do {PROTOCOL_MIN}; atualize o agente "
                                                  "(scripts/worker-install.ps1 ou worker-install.sh).")
        linha = self.db.one("SELECT * FROM workers WHERE id=?", (hello.worker_id,))
        if enrollment:
            if linha is not None:
                raise WorkerError("already_enrolled", f"O worker '{hello.worker_id}' já está inscrito; use a "
                                                      "credencial dele em vez de um token de inscrição.")
            self._consumir_inscricao(enrollment, hello.worker_id)
            credencial = secrets.token_urlsafe(32)
            self._upsert(hello, token_hash=_hash(credencial), enrolled=True)
            return credencial
        if linha is None:
            raise WorkerError("not_enrolled", f"O worker '{hello.worker_id}' não está inscrito. Gere um token de "
                                              "inscrição no painel e rode o instalador com ele.")
        if not token or not secrets.compare_digest(linha["token_hash"], _hash(token)):
            raise WorkerError("bad_credential", "Credencial de worker inválida.")
        self._upsert(hello, token_hash=linha["token_hash"], enrolled=False)
        return ""

    def _upsert(self, hello: Hello, *, token_hash: str, enrolled: bool) -> None:
        agora = now_iso()
        self.hibernacao[hello.worker_id] = bool(hello.hibernation)
        if hello.accel:
            self.aceleracao[hello.worker_id] = hello.accel
        else:
            self.aceleracao.pop(hello.worker_id, None)
        self.db.execute(
            "INSERT INTO workers(id, name, os, os_version, agent_version, protocol, appium_mode, appium_url,"
            " max_slots, verbs, state, state_detail, resources, devices, enrolled_at, last_seen_at, token_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET name=excluded.name, os=excluded.os, os_version=excluded.os_version,"
            " agent_version=excluded.agent_version, protocol=excluded.protocol, appium_mode=excluded.appium_mode,"
            " appium_url=excluded.appium_url, max_slots=excluded.max_slots, verbs=excluded.verbs,"
            " state=excluded.state, state_detail=NULL, resources=excluded.resources, devices=excluded.devices,"
            " last_seen_at=excluded.last_seen_at",
            (hello.worker_id, hello.name, hello.os, hello.os_version, hello.agent_version, hello.protocol,
             hello.appium_mode, hello.appium_url, hello.max_slots, dumps(hello.verbs), "online", None,
             dumps(hello.resources.model_dump()) if hello.resources else None,
             dumps([d.model_dump() for d in hello.devices]), agora, agora, token_hash))

    def attach(self, worker_id: str, send: Callable[[dict[str, Any]], Awaitable[None]],
               fechar: Callable[[], Awaitable[None]] | None = None) -> WorkerLink:
        """Instala o canal. Conexão nova do mesmo worker derruba a anterior: um dono por worker, sempre.

        E FECHA o socket anterior: sem isso, um agente duplicado (partida manual + tarefa agendada) seguiria
        batendo por um canal órfão que o central já não usa para despachar.
        """
        if (antigo := self.live.get(worker_id)) is not None:
            antigo.encerrar("uma conexão nova deste worker substituiu a anterior")
            self._fechar_em_segundo_plano(antigo)
        link = WorkerLink(worker_id, send, fechar)
        self.live[worker_id] = link
        self.on_change(worker_id)
        return link

    @staticmethod
    def _fechar_em_segundo_plano(link: WorkerLink) -> None:
        if link.fechar is None:
            return
        try:
            asyncio.get_running_loop().create_task(link.fechar())
        except RuntimeError:
            pass        # fora do laço (teste síncrono, encerramento): não há socket a fechar de forma útil

    def detach(self, worker_id: str, motivo: str, link: WorkerLink | None = None) -> bool:
        """Remove o canal. Devolve `True` quando ELE era o canal vivo — e `False` quando não era mais.

        O `link` é o que corrige o defeito: o `finally` de um socket que morreu TARDE apagava o link NOVO,
        marcava os comandos em voo dele como incertos e tirava os verbos do aparelho, deixando o worker "online"
        no painel e todo despacho recusando. Agora só remove quem ainda é o dono.
        """
        vivo = self.live.get(worker_id)
        if link is not None and vivo is not link:
            # Socket velho terminando depois da reconexão: encerra só o que era DELE e não encosta no link novo.
            link.encerrar(motivo)
            return False
        self.live.pop(worker_id, None)
        if vivo is not None:
            vivo.encerrar(motivo)
        # NÃO marca offline aqui: socket cai por rede piscando, e o worker pode voltar em segundos ainda dentro do
        # prazo da batida. Quem decide "indisponível" é `reap()`, pela ausência de batida.
        self.db.execute("UPDATE workers SET state_detail=? WHERE id=?", (truncate(motivo, 200), worker_id))
        self.on_change(worker_id)
        return True

    def welcome(self, esperados: dict[str, str]) -> Welcome:
        # O relógio que o agente compara com o dele é o do BANCO, não o desta máquina (item 5.3): é o mesmo
        # relógio que escreve e lê o vencimento dos leases, então o desvio que o agente reporta passa a ser o
        # desvio que de fato importa. No SQLite os dois são o mesmo, e nada muda.
        return Welcome(server_time=self.db.agora_iso(), heartbeat_s=HEARTBEAT_S, expected_devices=esperados)

    # ------------------------------------------------------------------ batida e saúde
    def on_heartbeat(self, worker_id: str, hb: Heartbeat, link: WorkerLink | None = None) -> None:
        """`degraded` por desvio de relógio (achado #142) é um estado à parte de manutenção e de offline: o
        worker segue batendo e aceitando comando, só o desvio contra o relógio do central passou do limite em que
        a folga do lease de posse (120 s, ver docs/banco.md) deixa de ser folga de verdade. Marcado e desmarcado
        aqui, a cada batida — sem coluna nova, sem migração: `state` já era texto livre (migrations/015).

        Batida de socket ÓRFÃO (o link não é mais o vivo) é ignorada: senão um agente duplicado manteria
        `state='online'` enquanto o despacho vai para outro canal, que é exatamente a mentira do achado #125."""
        if link is not None and self.live.get(worker_id) is not link:
            return
        # Achados #17/#143: comparado ANTES e DEPOIS do UPDATE, para só chamar `on_change` (evento persistido)
        # quando algo OBSERVÁVEL de fato mudou. Sem isto, toda batida — uma a cada 10 s por worker — virava
        # `worker.updated` gravado no banco: 57% do log de eventos era isto, e a janela de replay do painel
        # (5000 eventos) estourava em menos de 2 h com o parque ligado.
        antes = self.db.one("SELECT state, state_detail, devices FROM workers WHERE id=?", (worker_id,))
        desvio = hb.clock_offset_s
        relogio = (f"{CLOCK_DRIFT_PREFIX}: {desvio:+.1f} s em relação ao central"
                   if desvio is not None and abs(desvio) > CLOCK_OFFSET_LIMIT_S else None)
        # Segunda causa de `degraded`, no mesmo lugar e pela mesma regra (achado #41): a máquina no limite de RAM
        # ou de disco segue batendo e aceitando comando — o que ela não deve receber é mais um aparelho LIGADO, e
        # quem lê isso é o rodízio. Cada causa limpa só o próprio detalhe: sem isso, a batida seguinte de um
        # worker com pouca RAM apagava a frase do relógio desalinhado (e vice-versa).
        recurso = recurso_no_limite(hb.resources)
        detalhe = relogio or recurso
        # `CAST(? AS TEXT)` não é enfeite: um parâmetro sozinho num `IS NOT NULL` não tem de onde tirar tipo, e o
        # PostgreSQL recusa com "could not determine data type of parameter $3". O SQLite deixa passar, então isto
        # só apareceu quando a suíte passou a rodar de verdade no outro banco (item 5.4) — a batida de TODO worker
        # falharia em produção com PostgreSQL. O mesmo vale no `THEN`: ele decide o tipo do `CASE`.
        self.db.execute(
            "UPDATE workers SET last_seen_at=?, state=?,"
            " state_detail=CASE WHEN CAST(? AS TEXT) IS NOT NULL THEN CAST(? AS TEXT)"
            "                    WHEN state_detail LIKE ? OR state_detail LIKE ? THEN NULL"
            "                    ELSE state_detail END,"
            " resources=COALESCE(?, resources), devices=COALESCE(?, devices) WHERE id=?",
            (now_iso(), "degraded" if detalhe else "online", detalhe, detalhe, f"{CLOCK_DRIFT_PREFIX}%",
             f"{RECURSO_BAIXO_PREFIX}%",
             dumps(hb.resources.model_dump()) if hb.resources else None,
             dumps([d.model_dump() for d in hb.devices]) if hb.devices else None, worker_id))
        depois = self.db.one("SELECT state, state_detail, devices FROM workers WHERE id=?", (worker_id,))
        mudou = antes is None or depois is None or (
            antes["state"] != depois["state"] or antes["state_detail"] != depois["state_detail"]
            or antes["devices"] != depois["devices"])
        if mudou:
            self.on_change(worker_id)
        # Recurso sempre segue para a tela — só não vira linha no banco a cada 10 s (ver `on_metrics`).
        self.on_metrics(worker_id, hb.resources)

    def marcar_detalhe(self, worker_id: str, detalhe: str | None) -> None:
        """Escreve um porquê visível na Infraestrutura sem mexer no estado observado.

        `state` continua sendo o que se OBSERVA da conexão: um worker cujo Appium local não respondeu segue
        online e trabalhando (pelo Appium do central) — degradá-lo diria que ele parou, o que seria falso.
        """
        self.db.execute("UPDATE workers SET state_detail=? WHERE id=?", (truncate(detalhe, 200), worker_id))
        self.on_change(worker_id)

    def marcar_transporte(self, worker_id: str, state: str, detail: str | None) -> bool:
        """Grava o estado do túnel (achado #179). Devolve `True` só quando MUDOU — quem chama usa isso para
        não emitir `worker.updated` a cada sonda quando nada mudou (o túnel é sondado com frequência)."""
        atual = self.db.one("SELECT transport_state FROM workers WHERE id=?", (worker_id,))
        if atual is not None and atual["transport_state"] == state:
            self.db.execute("UPDATE workers SET transport_detail=? WHERE id=?", (truncate(detail, 200), worker_id))
            return False
        self.db.execute(
            "UPDATE workers SET transport_state=?, transport_detail=?, transport_since=? WHERE id=?",
            (state, truncate(detail, 200), now_iso(), worker_id))
        self.on_change(worker_id)
        return True

    def reap(self) -> list[str]:
        """Marca offline quem não bate há tempo demais. Devolve quem mudou, para virar evento."""
        limite = iso_in(-HEARTBEAT_S * BATIDAS_PERDIDAS)
        candidatos = self.db.query(
            "SELECT id FROM workers WHERE state<>'offline' AND (last_seen_at IS NULL OR last_seen_at < ?)",
            (limite,))
        mudados = []
        for linha in candidatos:
            self.db.execute("UPDATE workers SET state='offline', state_detail=? WHERE id=?",
                            (f"sem batida há mais de {int(HEARTBEAT_S * BATIDAS_PERDIDAS)} s", linha["id"]))
            # Worker que parou de bater também perde o CANAL: um socket meio-aberto (notebook suspenso, Wi-Fi
            # trocando de IP) continuava no `live`, e o despacho ficava esperando o prazo inteiro por alguém que
            # já não estava lá. Quem estava em voo vira incerto pelo caminho de sempre.
            if (link := self.live.get(linha["id"])) is not None:
                self.live.pop(linha["id"], None)
                link.encerrar(f"sem batida há mais de {int(HEARTBEAT_S * BATIDAS_PERDIDAS)} s")
                self._fechar_em_segundo_plano(link)
            mudados.append(linha["id"])
            self.on_change(linha["id"])
        return mudados

    def set_maintenance(self, worker_id: str, on: bool) -> Row:
        """Manutenção interrompe novas atribuições e **não** derruba o que já está em voo."""
        if self.db.one("SELECT id FROM workers WHERE id=?", (worker_id,)) is None:
            raise WorkerError("not_found", f"Worker '{worker_id}' não existe.")
        self.db.execute("UPDATE workers SET maintenance=? WHERE id=?", (int(on), worker_id))
        self.on_change(worker_id)
        linha = self.db.one("SELECT * FROM workers WHERE id=?", (worker_id,))
        assert linha is not None
        return linha

    # ------------------------------------------------------------------ remoção e rotação de credencial
    def remove(self, worker_id: str, *, force: bool = False) -> None:
        """Remove o registro do worker — o procedimento que `docs/worker.md` promete e, até aqui, não existia.

        Recusa com 409 se o worker está conectado ou tem comando em voo, a menos que `force`; nesses dois casos
        `force` desconecta o canal (o socket cai, quem estava em voo vira `uncertain`, do mesmo jeito que uma queda
        de rede) antes de apagar. Os aparelhos amarrados a ele NUNCA ficam presos a um worker fantasma: o vínculo
        é desfeito, não a instância.
        """
        linha = self.db.one("SELECT * FROM workers WHERE id=?", (worker_id,))
        if linha is None:
            raise WorkerError("not_found", f"Worker '{worker_id}' não existe.")
        self._recusar_se_for_local(worker_id, "removido")
        if not force and worker_id in self.live:
            raise WorkerError("connected", f"Worker '{linha['name']}' está conectado; desconecte-o antes ou "
                                          "remova com force.")
        marcadores = ",".join("?" for _ in COMMAND_OPEN)
        abertos = self.db.scalar(f"SELECT COUNT(*) FROM commands WHERE worker_id=? AND state IN ({marcadores})",
                                 (worker_id, *(s.value for s in COMMAND_OPEN)))
        if not force and abertos:
            raise WorkerError("open_commands", f"Worker '{linha['name']}' tem {abertos} comando(s) em voo; espere "
                                              "terminar ou remova com force.")
        if worker_id in self.live:
            self.detach(worker_id, "worker removido no painel")
        # O aparelho não é removido junto — só perde o dono. Continua existindo, agora sem ciclo de vida remoto,
        # até alguém amarrá-lo a outro worker ou reinscrever este.
        self.db.execute("UPDATE instances SET worker_id=NULL WHERE worker_id=?", (worker_id,))
        self.db.execute("DELETE FROM workers WHERE id=?", (worker_id,))
        self.on_change(worker_id)

    def _recusar_se_for_local(self, worker_id: str, o_que: str) -> None:
        """O central não se remove nem se reinscreve pelo painel. A linha dele nasce e morre com o processo."""
        if self.local_worker_id is not None and worker_id == self.local_worker_id:
            raise WorkerError("local_worker", f"'{worker_id}' é este próprio servidor e não pode ser {o_que}: "
                                              "ele se registra sozinho a cada subida.")

    def rotate_credential(self, worker_id: str) -> str:
        """Gera credencial nova e derruba a conexão viva NA HORA — a antiga para de servir imediatamente.

        Devolve o token em claro, uma única vez: só o hash fica gravado. Cobre a máquina comprometida (o arquivo
        de credencial hoje é legível por `BUILTIN\\Users` no notebook): quem suspeitar disso roda isto e a
        credencial vazada vira inútil, sem precisar apagar e reinscrever o worker do zero.
        """
        linha = self.db.one("SELECT * FROM workers WHERE id=?", (worker_id,))
        if linha is None:
            raise WorkerError("not_found", f"Worker '{worker_id}' não existe.")
        self._recusar_se_for_local(worker_id, "rotacionado")
        nova = secrets.token_urlsafe(32)
        self.db.execute("UPDATE workers SET token_hash=? WHERE id=?", (_hash(nova), worker_id))
        if worker_id in self.live:
            self.detach(worker_id, "credencial rotacionada no painel: reconecte com o token novo")
        self.on_change(worker_id)
        return nova

    def verbs_de(self, worker_id: str) -> list[str] | None:
        """Verbos que aquele worker declarou, se ele está CONECTADO. Desconectado devolve `None`.

        A conexão importa: capacidade declarada por um worker que não está lá não é capacidade — é promessa.
        """
        if worker_id not in self.live:
            return None
        linha = self.db.one("SELECT verbs FROM workers WHERE id=?", (worker_id,))
        return (loads(linha["verbs"]) or None) if linha is not None else None

    def processo_de(self, worker_id: str, instance_id: str) -> tuple[str, bool, str | None] | None:
        """`(nome do worker, está conectado?, estado do processo daquele aparelho)`; `None` se o worker sumiu.

        Diferente de `verbs_de`, aqui o worker DESCONECTADO também interessa: "servidor fora do ar, estado
        desconhecido" é uma resposta melhor do que "sem conexão ADB", que era o que o painel dizia nos três
        casos (achado #61). O estado do processo só vale com o worker conectado — o que ficou gravado da última
        batida descreve um passado que ninguém confirmou.
        """
        linha = self.db.one("SELECT name, devices FROM workers WHERE id=?", (worker_id,))
        if linha is None:
            return None
        conectado = worker_id in self.live
        if not conectado:
            return linha["name"], False, None
        for d in loads(linha["devices"]) or []:
            if d.get("instance_id") == instance_id:
                return linha["name"], True, d.get("state") or None
        return linha["name"], True, None

    def hiberna(self, worker_id: str) -> bool:
        """A máquina DELE salva snapshot? Só vale com o worker conectado, pela mesma razão de `verbs_de`:
        declaração de quem não está lá é promessa, não capacidade."""
        return worker_id in self.live and bool(self.hibernacao.get(worker_id))

    def aceita_trabalho(self, worker_id: str) -> str | None:
        """`None` quando aceita; senão, a frase que explica por que não."""
        linha = self.db.one("SELECT * FROM workers WHERE id=?", (worker_id,))
        if linha is None:
            return f"worker '{worker_id}' não está inscrito"
        if linha["maintenance"]:
            return f"worker '{linha['name']}' está em manutenção: novas atribuições estão suspensas"
        if worker_id not in self.live:
            return f"worker '{linha['name']}' não está conectado"
        return None

    def capacidade(self, worker_id: str) -> WorkerCapacity | None:
        """Vagas e recursos daquela máquina, ou `None` se ela não está inscrita.

        É a resposta que faltava: `max_slots` e os recursos da batida existiam só como exibição, e o rodízio
        decidia com um único teto global (`max_online_devices`) que não crescia com worker novo nenhum.
        """
        linha = self.db.one("SELECT * FROM workers WHERE id=?", (worker_id,))
        if linha is None:
            return None
        res = WorkerResources.model_validate(loads(linha["resources"]) or {})
        visto = parse_iso(linha["last_seen_at"]) if linha["last_seen_at"] else None
        idade = (now() - visto).total_seconds() if visto is not None else None
        return WorkerCapacity(
            worker_id, linha["name"], connected=worker_id in self.live, maintenance=bool(linha["maintenance"]),
            max_slots=max(1, int(linha["max_slots"] or 1)), ram_free_mb=res.ram_free_mb,
            disk_free_gb=res.disk_free_gb, last_seen_at=linha["last_seen_at"],
            stale=idade is None or idade > BATIDA_VELHA_S,
            degraded_detail=linha["state_detail"] if linha["state"] == "degraded" else None)

    def motivo_manutencao(self, worker_id: str) -> str | None:
        """Só o motivo de MANUTENÇÃO — nunca 'não conectado'/'não inscrito'. Esses dois casos já têm mensagem
        própria mais específica na checagem de capacidade do aparelho (verbos declarados pelo worker), então quem
        só quer saber "a manutenção está ligada?" chama isto, não `aceita_trabalho`."""
        linha = self.db.one("SELECT * FROM workers WHERE id=?", (worker_id,))
        if linha is not None and linha["maintenance"]:
            return f"worker '{linha['name']}' está em manutenção: novas atribuições estão suspensas"
        return None

    # ------------------------------------------------------------------ despacho
    async def dispatch(self, worker_id: str, msg: Dispatch, ao_enviar: Callable[[], None] | None = None) -> Result:
        """Manda o comando e espera o desfecho.

        Prazo estourado devolve `uncertain`, nunca falha: o worker pode ter agido e não conseguido responder. É a
        regra que o projeto já aplica às ações da IA, agora entre máquinas.

        `ao_enviar` é chamado SÍNCRONO logo depois do envio ter sucesso, sem `await` no meio — é assim que o
        central grava `dispatched` no instante do envio de verdade, e não dentro da requisição HTTP que apenas
        agendou a tarefa. Sem `await` entre o envio e a marca, nenhum `ack` pode ser processado antes dela.
        """
        link = self.live.get(worker_id)
        if link is None:
            raise WorkerError("worker_offline", f"Worker '{worker_id}' não está conectado.")
        fut: asyncio.Future[Result] = asyncio.get_running_loop().create_future()
        link.pendentes[msg.command_id] = (msg.fence, fut)
        try:
            await link.send(msg.model_dump())
        except Exception as exc:  # noqa: BLE001 - falha ao ENVIAR é o único caso em que nada aconteceu
            link.pendentes.pop(msg.command_id, None)
            raise WorkerError("send_failed", f"Não foi possível enviar o comando ao worker: {exc}") from exc
        if ao_enviar is not None:
            try:
                ao_enviar()
            except Exception:  # noqa: BLE001 - marcar a hora nunca derruba o comando que já saiu
                log.exception("marca de despacho do comando %s", msg.command_id)
        laco = asyncio.get_running_loop()
        inicio = laco.time()
        link.prazos[msg.command_id] = inicio + msg.timeout_s
        try:
            # O prazo é relido a cada volta porque `adiar()` pode empurrá-lo: enquanto o aparelho está NA FILA
            # do worker ele não está bootando, e contar essa espera como tempo de boot transformava fila em
            # "resultado incerto". Com `boot_parallelism=1`, um lote de 6 `start` fazia o 6º estourar sem que
            # nada tivesse falhado.
            while True:
                restante = link.prazos[msg.command_id] - laco.time()
                if restante <= 0:
                    break
                try:
                    return await asyncio.wait_for(asyncio.shield(fut), timeout=restante)
                except asyncio.TimeoutError:
                    continue                     # o prazo pode ter sido adiado no meio da espera: releia
            gasto = laco.time() - inicio
            return Result(command_id=msg.command_id, outcome="uncertain",
                          reason=f"o worker não respondeu em {gasto:.0f} s; resultado desconhecido")
        finally:
            link.pendentes.pop(msg.command_id, None)
            link.confirmados.discard(msg.command_id)
            link.prazos.pop(msg.command_id, None)

    def adiar(self, worker_id: str, command_id: str, segundos: float) -> bool:
        """Empurra o prazo de um comando em voo. Devolve `True` quando havia prazo a empurrar.

        Existe por causa da fila de boot do worker: o agente avisa que o aparelho está esperando vaga, e o
        central para de contar essa espera como tempo de boot. Sem isto, a proteção que evita ANR na máquina do
        worker produzia comandos "incertos" no painel — e o achado #50 media a conta: com `boot_parallelism=1` e
        boots de 104-192 s, o quarto `start` de um lote começaria depois de 312 s do prazo de 540 s.
        """
        link = self.live.get(worker_id)
        if link is None or command_id not in link.prazos:
            return False
        link.prazos[command_id] = max(link.prazos[command_id],
                                      asyncio.get_running_loop().time() + max(segundos, 0.0))
        return True

    def on_ack(self, worker_id: str, command_id: str) -> bool:
        link = self.live.get(worker_id)
        if link is None or command_id not in link.pendentes:
            return False
        link.confirmados.add(command_id)
        return True

    def on_result(self, worker_id: str, result: Result, *, fence: int | None = None) -> bool:
        """Entrega o desfecho. Cerca velha — ou ausente — é RECUSADA: worker antigo não sobrescreve o presente.

        Antes, `fence=None` passava direto: a proteção era opcional, e quem a omitisse (agente velho, processo
        forjado, mensagem montada à mão) escapava dela. Agora resultado sem cerca é recusado como cerca errada.
        """
        link = self.live.get(worker_id)
        if link is None:
            return False
        pendente = link.pendentes.get(result.command_id)
        if pendente is None:
            return False
        cerca, fut = pendente
        cerca_recebida = fence if fence is not None else result.fence
        if cerca_recebida != cerca:
            log.warning("resultado de %s com cerca %s (esperada %s): recusado", worker_id, cerca_recebida, cerca)
            return False
        if not fut.done():
            fut.set_result(result)
        return True

    async def cancel(self, worker_id: str, command_id: str) -> bool:
        link = self.live.get(worker_id)
        if link is None:
            return False
        await link.send({"type": "cancel", "command_id": command_id})
        return True

    # ------------------------------------------------------------------ leitura
    def rows(self) -> list[Row]:
        return self.db.query("SELECT * FROM workers ORDER BY name")

    def _defasado(self, row: Row) -> bool:
        """O agente daquela máquina roda um código diferente do que está NESTE servidor?

        Comparação de texto exato, e não de ordem: a versão é `0.1.0+<sha7>` e commits não se ordenam. O que
        importa para quem opera é binário — "o que está lá é o que está aqui?" —, e era a pergunta que não tinha
        resposta nenhuma: a cópia em `C:\\farm\\agent` não é checkout e as duas pontas diziam `0.1.0`.

        Três casos devolvem `False` de propósito: o próprio central (comparar-se consigo mesmo não informa nada),
        worker sem versão registrada (linha antiga: "não se sabe" não é "defasado") e servidor que não descobriu
        a versão dele mesmo — acusar defasagem apoiado num `+desconhecido` seria alarme sem fato.
        """
        if row["id"] == self.local_worker_id:
            return False
        esperada, dele = agent_version(), row["agent_version"]
        if not dele or esperada == DESCONHECIDO:
            return False
        return dele != esperada

    def dto(self, row: Row) -> WorkerDTO:
        conectado = row["id"] in self.live
        return WorkerDTO(
            id=row["id"], name=row["name"], os=row["os"], os_version=row["os_version"],
            agent_version=row["agent_version"], expected_agent_version=agent_version(),
            agent_outdated=self._defasado(row), accel=self.aceleracao.get(row["id"]),
            appium_mode=row["appium_mode"], appium_url=row["appium_url"],
            max_slots=row["max_slots"], verbs=loads(row["verbs"]) or [],
            # `maintenance` ganha do estado observado na EXIBIÇÃO, mas os dois ficam no DTO: um worker em
            # manutenção continua online, e esconder isso atrapalharia quem está diagnosticando.
            state="maintenance" if row["maintenance"] else row["state"],
            observed_state=row["state"], maintenance=bool(row["maintenance"]), state_detail=row["state_detail"],
            connected=conectado, local=row["id"] == self.local_worker_id,
            resources=WorkerResources.model_validate(loads(row["resources"]) or {}),
            devices=[WorkerDevice.model_validate(d) for d in (loads(row["devices"]) or [])],
            enrolled_at=row["enrolled_at"], last_seen_at=row["last_seen_at"],
            transport_state=row["transport_state"] if "transport_state" in row.keys() else None,
            transport_detail=row["transport_detail"] if "transport_detail" in row.keys() else None,
            transport_since=row["transport_since"] if "transport_since" in row.keys() else None)

    def dtos(self) -> list[WorkerDTO]:
        return [self.dto(r) for r in self.rows()]
