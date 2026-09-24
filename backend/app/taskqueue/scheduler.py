"""Scheduler assíncrono (único processo): um worker por aparelho, concorrência entre aparelhos,
serialização dentro de cada um. Uma instância lenta ou bloqueada não segura as demais.

Limites independentes: `max_active_devices` (workers simultâneos) e `max_ai_concurrency` (chamadas ao modelo).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from ..config import Config
from ..db import dumps, loads
from ..devices.manager import DeviceManager, DeviceRuntime, Limiter
from ..models import (ActionStatus, AttemptStatus, ControlOwner, DeliveryLevel, InstanceCurrent, InstanceState,
                      ObjectiveStatus, Plan, PlanStep, RunStatus, StepStatus)
from ..planning.catalog import capabilities_of
from ..releases.service import InstalacaoIncerta
from ..planning.provider import AIProvider, AppContext
from ..util import iso_in, now, now_iso, parse_iso
from .ai_slots import VagasDeIA
from .balanceamento import Candidato, Servidor
from .executor import Outcome, StepExecutor, StepOutcome
from .flows import FlowStore
from .foreach import expand
from .repository import MOTIVO_REJEICAO, RENOVAR_POSSE_S, PosseDaEtapaPerdida, Repository

log = logging.getLogger("poc.scheduler")
MAX_PLAN_REVISIONS = 1
# estados que o rodízio pode ligar sob demanda
WAKEABLE = {InstanceState.stopped, InstanceState.absent, InstanceState.hibernated}
#: Quanto um objetivo ESPERA o worker que hospeda o aparelho dele voltar antes de parar para uma pessoa. Queda
#: de túnel e reinício de agente duram segundos; passado isto, alguém precisa olhar a outra máquina — e aí o
#: bloqueio traz o nome do worker e desde quando ele não dá notícia, em vez de "inicie a instância".
ESPERA_POR_WORKER_S = 300.0


class Scheduler:
    def __init__(self, cfg: Config, repo: Repository, devices: DeviceManager, provider: AIProvider,
                 settings_getter: Callable[[], Any]):
        self.cfg = cfg
        self.repo = repo
        self.devices = devices
        self.provider = provider
        self.get_settings = settings_getter
        # O teto de chamadas de IA é do SISTEMA, não deste processo (item 5.2): o semáforo local continua sendo a
        # fila justa daqui, e quem decide o teto global é o lease em `ai_slots`.
        self.ai_slots = VagasDeIA(repo.db, holder=cfg.owner_id)
        self.ai_limiter = Limiter(settings_getter().max_ai_concurrency, vagas=self.ai_slots)
        self.executor = StepExecutor(cfg, repo, devices, provider, self.ai_limiter, settings_getter)
        self.workers: dict[str, asyncio.Task[None]] = {}
        self.flows = FlowStore(repo.db)
        self._pathfinders: dict[str, tuple[str, float]] = {}     # execução → (aparelho que está aprendendo, desde)
        self._restart_app: dict[str, str] = {}                   # aparelho → package a encerrar antes da próxima etapa
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        # Achado #164: quantas voltas o laço já deu. É o que permite a um teste esperar "um tick passou e NADA
        # aconteceu" observando o laço, em vez de dormir 1,2 s e torcer — numa máquina carregada (esta roda
        # emuladores) dormir e torcer é falso-negativo garantido.
        self.ticks = 0
        self._manual_since: dict[str, float] = {}
        self._posse_renovada = 0.0               # monotonic da última renovação de posse
        # Terceira porta do despacho (aparelho pronto, app pronto, sessão pronta). Preenchida pelo AppState:
        # o scheduler não conhece o domínio de perfil, só a forma da porta.
        # A porta recebe o PACOTE do item: quem responde por sessão de conta é o provedor declarado daquele
        # app (`planning/catalog`), não o Instagram por omissão.
        self.session_gate: Callable[[DeviceRuntime, str | None],
                                    tuple[str, Callable[[], Any] | None] | None] | None = None
        # Resolvedor da porta do APP, no mesmo molde da de sessão: (aparelho, pacote, objetivo) → None quando não há
        # entrega pendente; `(motivo, trabalho)` quando dá para resolver instalando; `(motivo, None)` quando só uma
        # pessoa resolve. Injetado pelo AppState: o scheduler não conhece o domínio de release.
        self.app_resolver: Callable[[DeviceRuntime, str, Any], tuple[str, Callable[[], Any] | None] | None] | None = None
        # Pré-voo do APP, sem efeito nenhum: `{code, motivo, acao}` quando o aplicativo daquele aparelho impede a
        # tarefa e só uma pessoa resolve; `None` quando não impede — inclusive quando não se sabe. Serve à recusa
        # explicada ANTES de planejar, e por isso é síncrona e não toca em aparelho. Injetado pelo AppState.
        self.app_preflight: Callable[[DeviceRuntime], dict[str, str] | None] | None = None
        # Entrega imediata ("instalar em todos agora"): [(aparelho, trabalho)] ainda por entregar. É uma SEGUNDA fonte
        # de demanda para o MESMO rodízio e o MESMO dono por aparelho — não um mecanismo paralelo. Injetado pelo AppState.
        self.rollout_source: Callable[[], list[tuple[str, Callable[[], Any]]]] | None = None
        # Achado #109: uma aprovação pendente de uma etapa desta execução não pode sobreviver ao objetivo que a
        # pediu — cancelar ou abandonar o item sem expirar o pedido deixava a fila "Aguardando aprovação" com um
        # pedido órfão, que uma pessoa podia decidir mesmo depois de a etapa já ter morrido. Injetado pelo
        # AppState (que é quem conhece `ApprovalStore`; o scheduler não conhece o domínio de aprovação).
        self.expirar_aprovacoes_do_objetivo: Callable[..., int] | None = None
        # (objetivo, etapa, execução) → veredito de política/limite; None quando pode seguir. Injetado pelo AppState.
        # Assíncrona porque esta porta pode precisar ESCREVER o texto da etapa antes de liberá-la: a geração com a
        # persona do perfil é uma chamada de modelo. É o único ponto com o perfil resolvido e ainda nada digitado.
        self.policy_gate: Callable[[Any, Any, Any], Awaitable[Any]] | None = None
        # Execução saiu do ar (terminou ou foi cancelada): quem guarda estado POR execução limpa o seu aqui.
        self.on_run_settled: Callable[[str], Any] | None = None
        # Uma etapa de COLETA terminou: (objetivo, etapa, itens lidos). Quem sabe o que fazer com uma lista de
        # falas é o domínio social (gravar o que a contraparte disse), não a fila — daqui sai só o fato de que a
        # leitura aconteceu. Injetado pelo AppState.
        self.on_items_collected: Callable[[Any, Any, list[str]], None] | None = None
        # Manutenção do worker: `None` quando aceita; senão a frase do motivo. Injetado pelo AppState a partir de
        # WorkerRegistry.aceita_trabalho — o scheduler não conhece o registro de workers, só a forma da porta.
        self.worker_gate: Callable[[str], str | None] | None = None
        # Vagas e recursos DAQUELA máquina (`WorkerRegistry.capacidade`), para o rodízio decidir por worker em vez
        # de por um teto global que não crescia com worker novo nenhum. `None` quando o worker não está inscrito.
        self.worker_capacity: Callable[[str], Any] | None = None
        #: aparelho remoto → desde quando o worker dele não dá notícia. Chaveado por APARELHO (e não por objetivo)
        #: de propósito: a espera é do aparelho, e assim o dicionário é limitado pelo tamanho do parque.
        self._sem_worker: dict[str, float] = {}
        #: objetivos cujo motivo de espera o RODÍZIO já escreveu neste tick. Um motivo por objetivo por tick: sem
        #: isto o rodízio ("aguardando vaga no worker X") e o despacho ("aguardando o worker X ligar") se
        #: sobrescreveriam um ao outro a cada segundo, e cada troca emite evento.
        self._explicado: set[str] = set()
        devices.on_device_free = self.wake

    # ------------------------------------------------------------------ ciclo
    def onde_roda(self, rt: DeviceRuntime | None) -> dict[str, str | None]:
        """Onde este aparelho mora AGORA: worker que o hospeda, backend que despacha, serial e identidade física.

        Uma pergunta, uma resposta, usada pela fotografia do plano e pela re-fotografia do despacho — o id lógico
        (`android-09`) é um apelido que muda de aparelho por configuração, e é isto que deixa o histórico legível
        depois que ele muda.
        """
        return {"worker_id": rt.worker_id if rt is not None else None,
                "hosted_by": self.cfg.owner_id,
                "device_serial": rt.serial if rt is not None else None,
                "physical_id": rt.physical_id if rt is not None else None}

    def wake(self) -> None:
        self._wake.set()

    async def start(self) -> None:
        self.reconcile_after_restart()
        self._task = asyncio.create_task(self._loop(), name="scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        for t in list(self.workers.values()):
            t.cancel()
        await asyncio.gather(*self.workers.values(), return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - o scheduler nunca morre por um erro isolado
                log.exception("erro no tick do scheduler")
            self.ticks += 1                      # conta DEPOIS do tick: quem espera `ticks > n` sabe que n rodaram
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=float(self.get_settings().scheduler_tick_s))
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    def _tick(self) -> None:
        s = self.get_settings()
        self.ai_limiter.set_limit(s.max_ai_concurrency)
        self.devices.boot_limiter.set_limit(s.boot_parallelism)
        self._manter_posse()
        for run in self.repo.active_runs():
            if run["cancel_requested"]:
                self._finish_cancel(run)
                continue
            if not run["pause_requested"]:
                self.repo.promote(run["id"])
        entrega = self.rollout_source() if self.rollout_source else []
        self._explicado.clear()
        if s.auto_start_devices or entrega:
            # antes do despacho: o teto de workers não pode esconder quem espera vaga. Com o rodízio desligado, só a
            # entrega imediata — que uma pessoa pediu de propósito — liga aparelho; tarefa comum segue bloqueando.
            self._rotate(s, entrega=[iid for iid, _ in entrega], tarefas=s.auto_start_devices)
        taken: set[str] = set()
        for obj in self.repo.dispatchable_objectives():
            iid = obj["instance_id"]
            if iid in taken:
                continue
            taken.add(iid)                      # a execução mais antiga tem a vez naquele aparelho
            if iid in self.workers:
                continue
            if len(self.workers) >= s.max_active_devices:
                break
            try:
                rt = self.devices.get(iid)
            except KeyError:
                self._block(obj, "Instância não existe na configuração atual.", "Ajuste a configuração e retome o item.")
                continue
            if rt.store:
                # Defesa em profundidade: `RunService.create` já recusa a loja como alvo. Se um objetivo antigo apontar
                # para uma instância que DEPOIS virou loja, ele para aqui com o motivo, em vez de operar a Play Store.
                self._block(obj, "Este aparelho é a loja (Play Store): ele não executa tarefas.",
                            "Refaça a execução escolhendo um aparelho do parque.")
                continue
            if rt.state != InstanceState.online:
                waits = {InstanceState.booting} | (WAKEABLE | {InstanceState.stopping} if s.auto_start_devices else set())
                if rt.state not in waits:
                    self._block(obj, f"O aparelho não está online (estado: {rt.state.value}).",
                                "Inicie a instância e use “Tentar novamente” neste item.")
                    continue
                if rt.external and rt.state != InstanceState.booting:
                    # Aparelho de OUTRA máquina. Enquanto houver worker para ligá-lo, isto é espera — não
                    # decisão de ninguém: quem o liga é o rodízio, pelo agente. Bloquear na hora (o que se fazia
                    # aqui) mandava a pessoa "iniciar a instância e tentar de novo", e o tick seguinte a
                    # bloqueava outra vez, com o remoto ainda parado.
                    espera, bloqueio = self._espera_do_remoto(rt)
                    if bloqueio is not None:
                        self._block(obj, *bloqueio)
                    elif obj["id"] not in self._explicado:
                        self.repo.note_waiting(obj["id"], espera or "aguardando o aparelho ligar", wait_reason="device_slot")
                continue
            # Item 12.1: um item pode atravessar apps. As portas (app instalado, sessão entrada) valem para CADA app
            # das etapas que faltam, na ordem em que aparecem; o primeiro app que não está pronto segura o item.
            segurado = False
            for pacote_do_item in self._pacotes_do_objetivo(obj, rt) or [None]:
                if self._portas_do_app(obj, rt, pacote_do_item):
                    segurado = True
                    break
            if segurado:
                continue
            if self._waits_for_pathfinder(obj, iid):
                continue
            if rt.worker_id and self.worker_gate:
                motivo_worker = self.worker_gate(rt.worker_id)
                if motivo_worker is not None:
                    # O worker está em manutenção (ou não conectado): o objetivo espera, sem virar waiting_user —
                    # ninguém decide nada, só aguarda a manutenção terminar.
                    self.repo.note_waiting(obj["id"], motivo_worker, wait_reason="device_slot")
                    continue
            if (lotado := self.servidor_lotado(rt)) is not None:
                # Teto de "trabalhando ao mesmo tempo" DAQUELA máquina (Limites → Por servidor). `continue`, e não
                # `break` como o teto geral: a outra máquina pode ter folga, e o próximo objetivo pode ser dela.
                if obj["id"] not in self._explicado:
                    self.repo.note_waiting(obj["id"], lotado, wait_reason="device_slot")
                continue
            if not self.devices.ai_begin(rt):
                continue                        # usuário no controle ou chamada anterior ainda ocupando o aparelho
            self.workers[iid] = asyncio.create_task(self._work(obj["id"], rt), name=f"worker-{iid}")
        if entrega:
            # Aparelho com trabalho aberto recebe o app pela PORTA, antes do próximo objetivo pendente — nunca por aqui,
            # que poderia trocar o app no meio de um objetivo em andamento.
            com_trabalho = self.repo.instances_with_open_work()
            for iid, trabalho in entrega:
                if iid in self.workers or iid in com_trabalho or len(self.workers) >= s.max_active_devices:
                    continue
                rt = self.devices.devices.get(iid)
                if rt is not None and rt.state == InstanceState.online and self.servidor_lotado(rt) is None:
                    self.run_device_job(rt, trabalho, label="entrega do aplicativo")

    # ------------------------------------------------------------------ trabalho exclusivo fora do laço de etapas
    def run_device_job(self, rt: DeviceRuntime, factory: Callable[[], Any], *, label: str) -> bool:
        """Roda um trabalho que precisa do aparelho inteiro — instalar um APK, autenticar — com as MESMAS guardas do
        executor: registrado em `workers`, então o despacho não concorre, o rodízio não despeja e o encerramento
        cancela. Devolve False quando o aparelho já está ocupado."""
        if rt.id in self.workers:
            return False
        if rt.worker_id and self.worker_gate and self.worker_gate(rt.worker_id) is not None:
            # Mesma guarda do despacho de objetivos: instalação de app e autenticação também são trabalho, e um
            # worker em manutenção não recebe nada novo — nem isso.
            return False
        if not self.devices.ai_begin(rt):
            return False
        self.workers[rt.id] = asyncio.create_task(self._device_job(rt, factory, label), name=f"job-{rt.id}")
        return True

    async def _device_job(self, rt: DeviceRuntime, factory: Callable[[], Any], label: str) -> None:
        try:
            await factory()
        except asyncio.CancelledError:
            raise
        except InstalacaoIncerta as exc:
            # Resultado DESCONHECIDO não é falha. Sem este ramo, o operador via o comando `uncertain`, o app em
            # `verifying` — e um toast VERMELHO dizendo que a instalação falhou, que é exatamente a contradição
            # que este item existe para eliminar. `warn` também não vira toast: quem conta a história é o comando.
            log.info("%s em %s: resultado incerto — %s", label, rt.id, exc)
            self.repo.bus.emit("log", f"{rt.id}: {label} — resultado incerto: {exc}", level="warn",
                               instance_id=rt.id)
        except Exception as exc:  # noqa: BLE001 - o trabalho reporta o próprio erro; aqui só não pode derrubar o laço
            log.exception("%s em %s", label, rt.id)
            self.repo.bus.emit("log", f"{rt.id}: {label} falhou — {exc}", level="error", instance_id=rt.id)
        finally:
            self.workers.pop(rt.id, None)
            self.devices.ai_end(rt)
            self.wake()

    def _portas_do_app(self, obj: Any, rt: DeviceRuntime, pacote_do_item: str | None) -> bool:
        """Porta do app (instalado e pronto) e porta da sessão (conta entrada) para UM app do item.

        `True` = o item ficou segurado neste tick (bloqueado, ou esperando instalação/login); `False` = liberado.
        """
        porta_app = self._app_gate(obj, rt, pacote_do_item)
        if porta_app is not None:
            motivo_app, entrega = porta_app
            if entrega is None:
                self._block(obj, motivo_app,
                            "Resolva o aplicativo deste aparelho (instalar ou verificar) e retome o item.")
            elif self.run_device_job(rt, entrega, label="entrega do aplicativo"):
                self.repo.note_waiting(obj["id"], f"instalando o aplicativo antes da tarefa — {motivo_app}", wait_reason="device_slot")
            return True                       # este tick é da instalação; a tarefa espera o app ficar pronto
        # A porta de sessão é POR APP: quem a atende é o provedor de sessão daquele pacote, declarado no
        # registro de aplicativos. Sem o pacote, uma tarefa de QA Messenger num aparelho com perfil do
        # Instagram vinculado passava pela porta do Instagram — e ficava bloqueada por um desafio de
        # segurança de uma conta que a tarefa nem ia tocar.
        porta = self.session_gate(rt, pacote_do_item) if self.session_gate else None
        if porta is not None:
            motivo, trabalho = porta
            rotulo = capabilities_of(pacote_do_item).label
            if trabalho is None:
                # Só uma pessoa resolve (desafio de segurança, conta errada, credencial recusada).
                self._block(obj, motivo, "Resolva a sessão deste perfil no painel e retome o item.")
            elif self.run_device_job(rt, trabalho, label=f"autenticação — {rotulo}"):
                self.repo.note_waiting(obj["id"], f"verificando a sessão em {rotulo} — {motivo}", wait_reason="device_slot")
            return True                       # este tick é do login; a tarefa espera a sessão ficar pronta
        return False

    def _pacotes_do_objetivo(self, obj: Any, rt: DeviceRuntime) -> list[str]:
        """Os pacotes que as etapas que FALTAM deste item vão operar, na ordem em que aparecem (item 12.1).

        Sem etapa ainda (plano não materializado) ou sem app nas etapas, é o app do plano/aparelho, como antes.
        """
        run = self.repo.run_row(obj["run_id"])
        if run is None:
            return []
        linhas = self.repo.db.query(
            "SELECT app_id, MIN(seq) AS ordem FROM steps WHERE objective_id=? AND plan_version=? AND status NOT IN"
            " ('succeeded','skipped','cancelled') GROUP BY app_id ORDER BY ordem", (obj["id"], obj["plan_version"]))
        pacotes: list[str] = []
        for app_id in [r["app_id"] for r in linhas] or [None]:
            try:
                app, _ = self._app_context(run, rt, app_id)
            except KeyError:
                continue
            if app.package and app.package not in pacotes:
                pacotes.append(app.package)
        return pacotes

    def _pacote_do_objetivo(self, obj: Any, rt: DeviceRuntime) -> str | None:
        """O pacote do app que ESTE item vai operar neste aparelho. `None` quando não há app definido.

        Existe porque as portas do app e da sessão precisam da MESMA resposta: a porta de sessão que não sabia de
        que app era a tarefa acabava abrindo o Instagram antes de uma tarefa de outro aplicativo.
        """
        run = self.repo.run_row(obj["run_id"])
        if run is None:
            return None
        try:
            app, _ = self._app_context(run, rt)
        except KeyError:
            return None
        return app.package or None

    def _app_gate(self, obj: Any, rt: DeviceRuntime,
                  pacote: str | None = None) -> tuple[str, Callable[[], Any] | None] | None:
        """Segunda das três portas do despacho: aparelho pronto, **app pronto**, sessão pronta.

        A pergunta ao resolvedor vem primeiro, e só depois "tem linha?": sem linha em `device_app_state` mas com
        versão promovida do app daquele aparelho, o resolvedor adota a versão e a entrega acontece aqui. Sem
        release gerenciada nenhuma o resolvedor devolve `None` e o caminho antigo (app instalado à mão, como o de
        QA) segue valendo sem mudança.

        Devolve `None` quando pode despachar; `(motivo, trabalho)` quando há uma versão distribuída ainda por
        instalar neste aparelho — o trabalho instala e a tarefa espera; `(motivo, None)` quando só uma pessoa resolve.
        A entrega pendente é conferida ANTES de "está pronto": o app pronto pode ser justamente a versão antiga."""
        package = pacote if pacote is not None else self._pacote_do_objetivo(obj, rt)
        if not package:
            return None
        # A pergunta ao resolvedor vem ANTES de "tem linha?": um aparelho que entrou no parque depois da
        # distribuição não tem linha nenhuma, e era exatamente ele que recebia tarefa de um app que não está
        # instalado. Com versão promovida do app dele, o resolvedor adota a versão e entrega aqui.
        if self.app_resolver is not None:
            entrega = self.app_resolver(rt, package, obj)
            if entrega is not None:
                return entrega
        row = self.repo.db.one("SELECT state, detail FROM device_app_state WHERE instance_id=? AND package_name=?",
                               (rt.id, package))
        if row is None:
            return None
        if row["state"] in ("ready", "installed"):
            return None
        return (f"O aplicativo não está pronto neste aparelho (estado: {row['state']})." + (
            f" {row['detail']}" if row["detail"] else ""), None)

    def _waits_for_pathfinder(self, obj: Any, iid: str) -> bool:
        """Desbravador: numa execução com vários aparelhos, o primeiro aprende as receitas e os demais esperam por
        ele (até `ai.pathfinder_wait_s`) para repetir sem IA. Só vale para quem ainda não começou."""
        ai = self.cfg.file.ai
        if not ai.pathfinder_wait_s or ai.recipes != "replay" or obj["status"] != ObjectiveStatus.pending.value:
            return False
        lead = self._pathfinders.get(obj["run_id"])
        if lead is None:
            self._pathfinders[obj["run_id"]] = (iid, time.monotonic())
            return False
        return lead[0] != iid and lead[0] in self.workers and time.monotonic() - lead[1] < ai.pathfinder_wait_s

    # ------------------------------------------------------------------ carga por servidor
    def servidor_de(self, rt: DeviceRuntime) -> str:
        """Em que máquina este aparelho roda: o worker dele, ou ESTE servidor (`owner_id`)."""
        return rt.worker_id if (rt.external and rt.worker_id) else self.cfg.owner_id

    def trabalhando_por_servidor(self) -> dict[str, int]:
        """Quantos aparelhos estão com trabalho em execução (objetivo, instalação, autenticação) por máquina."""
        contagem: dict[str, int] = {}
        for iid in self.workers:
            rt = self.devices.devices.get(iid)
            if rt is not None:
                chave = self.servidor_de(rt)
                contagem[chave] = contagem.get(chave, 0) + 1
        return contagem

    def servidor_lotado(self, rt: DeviceRuntime) -> str | None:
        """A frase de espera quando a máquina deste aparelho já está no teto de "trabalhando ao mesmo tempo" que
        o dono definiu para ela; `None` quando cabe (ou quando ela não tem teto próprio — vale só o geral)."""
        servidor = self.servidor_de(rt)
        cap = self._capacidade(servidor)
        teto = getattr(cap, "max_working", None) if cap is not None else None
        if not teto:
            return None
        em_uso = self.trabalhando_por_servidor().get(servidor, 0)
        if em_uso < teto:
            return None
        nome = getattr(cap, "name", servidor)
        return f"aguardando vaga de trabalho em “{nome}” ({em_uso} de {teto} aparelhos trabalhando)"

    def servidores(self) -> dict[str, Servidor]:
        """A foto de cada máquina para o balanceamento e para a tela Limites: capacidade, carga e vagas."""
        s = self.get_settings()
        devs = self.devices
        ids = {self.cfg.owner_id} | {self.servidor_de(rt) for rt in devs.devices.values()}
        trabalhando = self.trabalhando_por_servidor()
        fotos: dict[str, Servidor] = {}
        for sid in sorted(ids):
            cap = self._capacidade(sid)
            host = sid == self.cfg.owner_id
            vagas_max = (cap.max_slots if cap is not None else int(s.max_online_devices)) if not host \
                else int(s.max_online_devices)
            usadas = devs.slots_used() if host else devs.slots_used_of(sid)
            motivo: str | None = None
            if cap is None and not host:
                motivo = f"worker “{sid}” não está inscrito"
            elif cap is not None and cap.maintenance:
                motivo = f"worker “{cap.name}” está em manutenção"
            elif cap is not None and not host and not cap.connected:
                motivo = f"worker “{cap.name}” não está conectado"
            sem = cap.sem_recurso() if (cap is not None and not host) else None
            teto = getattr(cap, "max_working", None) if cap is not None else None
            fotos[sid] = Servidor(
                id=sid, nome=(cap.name if cap is not None else sid), disponivel=motivo is None,
                capacidade=int(teto or vagas_max), tem_teto_proprio=bool(teto),
                trabalhando=trabalhando.get(sid, 0),
                vagas_livres=0 if sem else max(0, vagas_max - usadas),
                cpu_percent=getattr(cap, "cpu_percent", None), ram_free_mb=getattr(cap, "ram_free_mb", None),
                motivo_indisponivel=motivo)
        return fotos

    def app_exige_conta(self, app_id: str) -> bool:
        """O app só funciona com conta logada (o catálogo dele declara provedor de sessão)?"""
        pacote = self.repo.db.scalar("SELECT package FROM apps WHERE id=?", (app_id,))
        return bool(pacote) and bool(capabilities_of(pacote).session_provider)

    def candidatos_do_app(self, app_id: str) -> list[Candidato]:
        """Aparelhos vinculados ao app, com o que o balanceamento precisa saber de cada um."""
        s = self.get_settings()
        db = self.repo.db
        vinculo = {r["id"]: r["app_id"] for r in db.query("SELECT id, app_id FROM instances")}
        com_trabalho = self.repo.instances_with_open_work() | set(self.workers)
        # App que exige CONTA (o catálogo declara provedor de sessão — hoje, o Instagram): só serve aparelho com
        # perfil ATIVO vinculado. Sem isto, "distribuir 8 no Instagram" caía em aparelho vinculado ao app mas sem
        # ninguém logado, e a execução travava na porta de sessão de cada um.
        exige_conta = self.app_exige_conta(app_id)
        com_perfil = {r["instance_id"] for r in db.query(
            "SELECT b.instance_id FROM device_profile_bindings b JOIN instagram_profiles p ON p.id=b.profile_id"
            " WHERE b.active=1 AND COALESCE(p.status, 'active')='active'")} if exige_conta else set()
        devs = self.devices
        saida: list[Candidato] = []
        for rt in devs.devices.values():
            if rt.store or vinculo.get(rt.id) != app_id:
                continue
            if exige_conta and rt.id not in com_perfil:
                continue
            ligavel = (not rt.external) or devs.gerenciado_remoto(rt)
            saida.append(Candidato(
                instance_id=rt.id, servidor=self.servidor_de(rt), ligado=rt.state == InstanceState.online,
                acordavel=bool(s.auto_start_devices) and ligavel and rt.state in WAKEABLE,
                ocupado=rt.id in com_trabalho or rt.control != ControlOwner.none))
        return saida

    # ------------------------------------------------------------------ aparelho que mora em outra máquina
    def _capacidade(self, worker_id: str | None) -> Any:
        return self.worker_capacity(worker_id) if (worker_id and self.worker_capacity) else None

    def _operavel(self, worker_id: str) -> bool:
        """Dá para pedir ciclo de vida naquela máquina agora? Manutenção suspende NOVAS atribuições — e mandar
        desligar um aparelho lá é uma delas."""
        cap = self._capacidade(worker_id)
        return cap is not None and cap.connected and not cap.maintenance

    def _espera_do_remoto(self, rt: DeviceRuntime) -> tuple[str | None, tuple[str, str] | None]:
        """Aparelho remoto que não está no ar: `(frase da espera, None)` ou `(None, (motivo, o que fazer))`.

        Três realidades diferentes que o código antigo tratava como uma só ("externo: ninguém o liga"):
        aparelho ALHEIO (celular na mesa, sem worker) continua bloqueando na hora, porque de fato ninguém o liga
        daqui; worker presente é espera; worker sumido é espera COM PRAZO — a queda de túnel dura segundos, e só
        depois de `ESPERA_POR_WORKER_S` a pessoa é chamada, com o nome do worker e desde quando ele sumiu.
        """
        cap = self._capacidade(rt.worker_id)
        if cap is None:
            self._sem_worker.pop(rt.id, None)
            return None, (f"O aparelho não está online (estado: {rt.state.value}) e nenhum worker o gerencia.",
                          "Inicie a instância e use “Tentar novamente” neste item.")
        if cap.maintenance:
            self._sem_worker.pop(rt.id, None)
            return f"aguardando a manutenção do worker “{cap.name}” terminar", None
        if cap.connected and "start" in (rt.worker_verbs or []):
            self._sem_worker.pop(rt.id, None)
            if (sem := cap.sem_recurso()) is not None:
                return f"aguardando recurso na máquina do worker — {sem}", None
            return f"aguardando o worker “{cap.name}” ligar o aparelho", None
        desde = self._sem_worker.setdefault(rt.id, time.monotonic())
        visto = cap.last_seen_at or "a inscrição"
        if time.monotonic() - desde < ESPERA_POR_WORKER_S:
            return f"aguardando o worker “{cap.name}” voltar — sem contato desde {visto}", None
        return None, (f"O worker “{cap.name}”, que hospeda este aparelho, está sem contato desde {visto}.",
                      "Confira a máquina dele na Infraestrutura e use “Tentar novamente” neste item.")

    # ------------------------------------------------------------------ rodízio: N contas sobre K vagas de RAM
    def _rotate(self, s: Any, *, entrega: list[str] | None = None, tarefas: bool = True) -> None:
        """Liga aparelhos parados que têm tarefa na fila (FIFO) enquanto houver vaga; sem vaga, desliga UM aparelho
        ocioso por tick. `max_online_devices` é o contador de vagas; a guarda de RAM do boot continua valendo.

        `entrega` são aparelhos com instalação imediata pendente: contam como demanda do mesmo jeito (o item da fila
        deles é `None`), depois das tarefas. `tarefas=False` = rodízio desligado: só a entrega liga aparelho."""
        devs = self.devices
        now_m = time.monotonic()

        def rodiziavel(d: DeviceRuntime) -> bool:
            """Quem o rodízio pode ligar e desligar: aparelho desta máquina, ou remoto cujo worker declara ligar.
            A loja fica de fora nos dois casos (quem a ligou a desliga)."""
            return not d.store and (not d.external or devs.gerenciado_remoto(d))

        def pool(d: DeviceRuntime) -> str | None:
            """A que conjunto de vagas este aparelho pertence: a RAM deste host (`None`) ou a da outra máquina."""
            return d.worker_id if d.external else None

        demand: list[tuple[DeviceRuntime, Any]] = []
        for obj in (self.repo.dispatchable_objectives() if tarefas else []):
            rt = devs.devices.get(obj["instance_id"])
            if (rt is not None and rodiziavel(rt) and rt.state in WAKEABLE
                    and all(rt is not d for d, _ in demand)):
                demand.append((rt, obj))
        for iid in entrega or []:
            rt = devs.devices.get(iid)
            if (rt is not None and rodiziavel(rt) and rt.state in WAKEABLE
                    and all(rt is not d for d, _ in demand)):
                demand.append((rt, None))
        # Vagas por conjunto. O host continua com o seu teto (`max_online_devices`, que é o `max_slots` que o
        # worker local declara); cada outra máquina passa a ter o DELA, então capacidade cresce com worker novo
        # em vez de esbarrar num teto global de 10 que o código carregava.
        livres: dict[str | None, int] = {None: s.max_online_devices - devs.slots_used()}
        impedido: dict[str | None, str] = {}

        def vagas(p: str | None) -> int:
            if p in livres:
                return livres[p]
            cap = self._capacidade(p)
            if cap is None or not cap.connected or cap.maintenance:
                nome = cap.name if cap is not None else p
                impedido[p] = (f"worker “{nome}” está em manutenção" if cap is not None and cap.maintenance
                               else f"worker “{nome}” não está conectado")
                livres[p] = 0
            elif (sem := cap.sem_recurso()) is not None:
                # Piso de RAM/disco da ÚLTIMA batida. Não vale para o host: lá a guarda é mais fina (ela conhece
                # a RAM estimada da instância e os boots em voo) e mora dentro do próprio boot.
                impedido[p] = sem
                livres[p] = 0
            else:
                livres[p] = cap.max_slots - devs.slots_used_of(p or "")
            return livres[p]

        waiting: list[tuple[DeviceRuntime, Any]] = []
        for rt, obj in demand:
            porque = f"tarefa na fila ({obj['run_id'][-6:]})" if obj is not None else "entrega do aplicativo"
            p = pool(rt)
            if vagas(p) > 0 and devs.request_start(rt, porque):
                livres[p] -= 1
            else:
                waiting.append((rt, obj))
        busy = self.repo.instances_with_open_work() if (waiting or s.idle_stop_s) else set()
        busy |= set(entrega or [])             # quem acabou de ligar para receber o app não cede a vaga antes de recebê-lo
        pinned = self.repo.instances_needing_user() if (waiting or s.idle_stop_s) else set()

        def evictable(d: DeviceRuntime, idle_for: float, p: str | None) -> bool:
            return (d.state == InstanceState.online and d.id not in self.workers and d.control == ControlOwner.none
                    and rodiziavel(d) and pool(d) == p            # a loja é desligada por quem a ligou, nunca pelo rodízio:
                    # o usuário digita na JANELA do emulador, e daqui não se vê foco nem controle — ela cairia no meio do login
                    and not d.takeover_requested and not d.focused and not d.executor.has_zombie
                    and d.id not in busy and d.id not in pinned
                    and now_m - d.online_since_mono >= s.min_online_dwell_s and now_m - d.last_activity_mono >= idle_for)

        # Aparelho em `stopping` é uma vaga JÁ prometida: todo desligamento termina em `stopped` ou `hibernated`, e
        # os dois liberam vaga. Mas `slots_used()` conta `stopping` como ocupado e `evictable` só aceita `online`,
        # então, sem descontar as paradas em voo, o tick seguinte (1 s depois) não enxerga a vaga a caminho, escolhe
        # OUTRA vítima, e o rodízio esvazia o parque inteiro para atender UM aparelho na fila — cada vítima pagando
        # snapshot na saída e boot na volta. A conta é POR conjunto de vagas: uma parada em voo no notebook não é
        # vaga a caminho aqui.
        def em_voo_de(p: str | None) -> int:
            return sum(1 for d in devs.devices.values()
                       if rodiziavel(d) and pool(d) == p and d.state == InstanceState.stopping)

        espera_por_pool: dict[str | None, list[tuple[DeviceRuntime, Any]]] = {}
        for rt, obj in waiting:
            espera_por_pool.setdefault(pool(rt), []).append((rt, obj))
        for p, fila in espera_por_pool.items():
            em_voo, livre = em_voo_de(p), livres.get(p, 0)
            bloqueio = impedido.get(p)
            victims: list[DeviceRuntime] = []
            if bloqueio is None and livre + em_voo < len(fila):
                victims = sorted((d for d in devs.devices.values() if evictable(d, 0, p)),
                                 key=lambda d: d.last_activity_mono)
                if victims:
                    devs.request_stop(victims[0], f"vaga para {fila[0][0].id}")
            elif bloqueio is None:
                continue                       # há vaga (ou uma a caminho): quem espera só espera o boot começar
            ligados, teto = self._ocupacao(p, s)
            for rt, obj in fila:
                if bloqueio is not None:
                    why = f"aguardando o worker — {bloqueio}"
                else:
                    why = ("aguardando vaga" if victims else
                           "aguardando vaga — nenhum aparelho ligado pode ser desligado agora "
                           "(em uso, em foco no painel ou com item que precisa de você)")
                    cedendo = f", {em_voo} cedendo a vaga" if em_voo else ""
                    onde = f" no worker {p}" if p else ""
                    why = f"{why} ({ligados}/{teto} ligados{onde}{cedendo})"
                if obj is not None:
                    self.repo.note_waiting(obj["id"], why, wait_reason="device_slot")
                    self._explicado.add(obj["id"])
                # o cartão do aparelho desligado também mostra o motivo
                card = "tarefa na fila — aguardando vaga" if obj is not None else "entrega do aplicativo — aguardando vaga"
                if rt.state in WAKEABLE and rt.state_detail != card:
                    rt.state_detail = card
                    devs.publish(rt)
        if s.idle_stop_s:
            for p in {pool(d) for d in devs.devices.values() if rodiziavel(d)} - set(espera_por_pool):
                if p is not None and not self._operavel(p):
                    continue          # manutenção suspende NOVAS atribuições, e desligar por ociosidade é uma
                idle = [d for d in devs.devices.values() if evictable(d, float(s.idle_stop_s), p)]
                if idle:
                    devs.request_stop(min(idle, key=lambda d: d.last_activity_mono),
                                      f"ocioso há mais de {s.idle_stop_s}s")

    def _ocupacao(self, p: str | None, s: Any) -> tuple[int, int]:
        """`(ligados, teto)` daquele conjunto de vagas, para a frase da espera dizer de qual máquina se fala."""
        if p is None:
            return self.devices.slots_used(), s.max_online_devices
        cap = self._capacidade(p)
        return self.devices.slots_used_of(p), (cap.max_slots if cap is not None else 0)

    def _block(self, obj: Any, reason: str, needs: str) -> None:
        self.repo.set_objective(obj["id"], ObjectiveStatus.waiting_user, detail=reason, blocked_reason=reason, needs=needs,
                                level="warn")
        self.repo.recompute_run(obj["run_id"])

    # ------------------------------------------------------------------ worker por aparelho
    def _stop_reason(self, run_id: str, rt: DeviceRuntime) -> str | None:
        run = self.repo.run_row(run_id)
        if run is None or run["cancel_requested"]:
            return "cancel"
        if run["pause_requested"]:
            return "pause"
        if rt.takeover_requested:
            return "takeover"
        return None

    async def _work(self, objective_id: str, rt: DeviceRuntime) -> None:
        repo = self.repo
        obj = repo.objective_row(objective_id)
        run_id = obj["run_id"]
        # ONDE isto está rodando, re-fotografado no instante do despacho: entre materializar o plano e chegar aqui
        # o aparelho pode ter trocado de worker, de endereço de ADB ou de aparelho físico por trás do id lógico.
        # Quem conta a verdade sobre onde o trabalho aconteceu é o despacho, não o plano.
        repo.stamp_location(objective_id, **self.onde_roda(rt))     # type: ignore[arg-type]
        resumed = self._manual_since.pop(rt.id, None) is not None   # o usuário controlou este aparelho há pouco
        try:
            while True:
                obj = repo.objective_row(objective_id)
                run = repo.run_row(run_id)
                if run is None or self._stop_reason(run_id, rt):
                    break
                repo.promote(run_id)
                srow = repo.next_ready_step(objective_id)
                if srow is None:
                    break
                pkg = self._restart_app.pop(rt.id, None)
                if pkg:                                   # plano revisado: recomeça com o app fechado
                    await self.devices.force_stop_app(rt, pkg)
                started = parse_iso(obj["started_at"])
                n_items = sum(len(v) for v in (loads(obj["collected"], {}) or {}).values())
                parado = int(obj["paused_s"] or 0)        # tempo represado por limite não conta como demora
                if started and (now() - started).total_seconds() - parado > (
                        self.get_settings().objective_timeout_s + 240 * n_items):
                    self._fail_objective(obj, "Tempo total do objetivo esgotado.")
                    break
                porta = await self.policy_gate(obj, srow, run) if self.policy_gate else None
                if porta is not None:
                    # Antes de assumir a etapa: nenhuma tentativa consumida, nenhuma chamada de modelo gasta.
                    self._hold(obj, srow, porta)
                    break
                attempt = repo.claim_step(srow["id"])
                if attempt is None:
                    break
                if obj["status"] != ObjectiveStatus.running.value:
                    repo.set_objective(objective_id, ObjectiveStatus.running,
                                       message=f"{rt.id}: objetivo em execução")
                    repo.recompute_run(run_id)
                step = repo.step_dto(repo.step_row(srow["id"]))
                self._publish_current(rt, obj, step.id)
                outcome = await self._run_guarded(run, obj, step, attempt["id"], rt, resumed)
                resumed = False
                self._apply(outcome, obj, step, attempt["id"], rt)
                self._publish_current(rt, obj, step.id)
                if outcome.outcome != Outcome.succeeded:
                    break
            self._maybe_complete(objective_id)
        except asyncio.CancelledError:
            raise
        except PosseDaEtapaPerdida as perda:
            # Não é erro: é este backend descobrindo que já não manda neste aparelho (item 5.3). Quem adotou a
            # etapa é quem a reconcilia; aqui o certo é largar o trabalho imediatamente, sem gravar mais nada.
            log.warning("worker %s: posse perdida — %s", rt.id, perda)
            repo.bus.emit("log", f"{rt.id}: outro backend ({perda.dono}) assumiu esta etapa; este parou de operar "
                                 "o aparelho.", level="warn", instance_id=rt.id, run_id=run_id)
        except Exception:  # noqa: BLE001
            log.exception("worker %s", rt.id)
        finally:
            self.workers.pop(rt.id, None)
            rt.current = None
            if rt.takeover_requested:
                self._manual_since[rt.id] = time.monotonic()
            self.devices.ai_end(rt)
            repo.recompute_run(run_id)
            self._settle_run(run_id)
            self._learn_flow(run_id)
            self.wake()

    def _settle_run(self, run_id: str) -> None:
        """Execução terminou: solta o que era guardado só por causa dela."""
        run = self.repo.run_row(run_id)
        terminais = (RunStatus.completed.value, RunStatus.completed_with_issues.value, RunStatus.failed.value,
                     RunStatus.cancelled.value)
        if run is None or run["status"] not in terminais:
            return
        self._pathfinders.pop(run_id, None)
        if self.on_run_settled is not None:
            try:
                self.on_run_settled(run_id)
            except Exception:  # noqa: BLE001 - limpeza nunca derruba o fim da execução
                log.exception("limpeza de fim de execução %s", run_id)

    def _learn_flow(self, run_id: str) -> None:
        """Execução terminou com TODOS comprovados → o comando vira um fluxo reaproveitável (plano congelado)."""
        if not self.cfg.file.ai.flows:
            return
        run = self.repo.run_row(run_id)
        if run is None or run["status"] != RunStatus.completed.value:
            return
        self._pathfinders.pop(run_id, None)
        try:
            flow_id = self.flows.learn_from_run(run)
        except Exception:  # noqa: BLE001 - otimização: nunca afeta o resultado da execução
            log.exception("aprender fluxo de %s", run_id)
            return
        if flow_id:
            self.repo.decision(f"Fluxo “{flow_id}” salvo: comandos iguais (com outros valores) reaproveitam este plano "
                               "sem chamar o planejador", run_id=run_id)

    async def _run_guarded(self, run: Any, obj: Any, step: Any, attempt_id: str, rt: DeviceRuntime,
                           resumed: bool) -> StepOutcome:
        app, account = self._app_context(run, rt, getattr(step, "app_id", None))
        later = [r["title"] for r in self.repo.db.query(
            "SELECT title FROM steps WHERE objective_id=? AND plan_version=? AND seq>? ORDER BY seq",
            (obj["id"], step.plan_version, step.seq))]
        try:
            return await self.executor.run_step(run=run, objective=obj, step=step, attempt_id=attempt_id, rt=rt, app=app,
                                                account_label=account, remaining=later,
                                                stop_reason=lambda: self._stop_reason(run["id"], rt),
                                                resumed_after_manual=resumed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - erro inesperado: nunca conta como sucesso
            log.exception("etapa %s", step.id)
            fired, _ = self.repo.commit_state(step.id)
            return StepOutcome(Outcome.uncertain if (step.side_effect and fired) else Outcome.failed,
                               f"Erro interno ao executar a etapa: {type(exc).__name__}: {exc}")

    def _hold(self, obj: Any, srow: Any, veredito: Any) -> None:
        """Represa a etapa sem gastar tentativa: `retry_wait` com hora marcada, ou bloqueio para uma pessoa.

        O tempo de espera entra em `objectives.paused_s` na hora de represar, para o prazo do objetivo não correr
        contra quem está apenas respeitando o próprio limite.
        """
        if veredito.is_wait:
            espera = max(0, int(((parse_iso(veredito.retry_at) or now()) - now()).total_seconds()))
            self.repo.transition_step(srow["id"], StepStatus.retry_wait, detail=veredito.reason,
                                      next_retry_at=veredito.retry_at,
                                      message=f"Etapa '{srow['title']}': represada — {veredito.reason}")
            self.repo.db.execute("UPDATE objectives SET paused_s=paused_s+?, blocked_kind='limit' WHERE id=?",
                                 (espera, obj["id"]))
            self.repo.note_waiting(obj["id"], f"aguardando o limite do perfil — {veredito.reason}", wait_reason="profile_limit")
            return
        self.repo.db.execute("UPDATE objectives SET blocked_kind='policy' WHERE id=? AND blocked_kind IS DISTINCT FROM 'approval'",
                             (obj["id"],))
        self._block(obj, veredito.reason, veredito.hint or "Ajuste a política deste perfil e retome o item.")

    def _app_context(self, run: Any, rt: DeviceRuntime,
                     step_app_id: str | None = None) -> tuple[AppContext, str | None]:
        """O app de uma etapa: o dela (item 12.1), senão o do plano, senão o padrão do aparelho."""
        plan = Plan.model_validate_json(run["plan"]) if run["plan"] else None
        inst = self.repo.db.one("SELECT app_id, account_label FROM instances WHERE id=?", (rt.id,))
        app_id = step_app_id or (plan.app_id if plan else None) or (inst["app_id"] if inst else None)
        row = self.repo.db.one("SELECT * FROM apps WHERE id=?", (app_id,)) if app_id else None
        if row is None:
            return AppContext(None, None, plan.app_package if plan else None, None, None, None), inst["account_label"] if inst else None
        return (AppContext(row["id"], row["name"], row["package"], row["activity"], row["nav_hints"],
                           loads(row["known_selectors"])), inst["account_label"] if inst else None)

    def _publish_current(self, rt: DeviceRuntime, obj: Any, step_id: str | None) -> None:
        o = self.repo.objective_row(obj["id"])
        dto = self.repo.objective_dto(o)
        s = self.repo.step_row(step_id) if step_id else None
        rt.current = InstanceCurrent(run_id=o["run_id"], objective_id=o["id"], objective_status=dto.status,
                                     step_id=s["id"] if s else None, step_title=s["title"] if s else None,
                                     step_status=StepStatus(s["status"]) if s else None,
                                     steps_done=dto.steps_done, steps_total=dto.steps_total)
        self.devices.publish(rt, f"{rt.id}: {s['title'] if s else 'objetivo'}")

    # ------------------------------------------------------------------ aplicar o resultado da etapa
    def _apply(self, out: StepOutcome, obj: Any, step: Any, attempt_id: str, rt: DeviceRuntime) -> None:
        repo = self.repo
        oid, detail = obj["id"], out.detail
        o = out.outcome
        if o == Outcome.succeeded:
            if out.delivery_level:
                repo.db.execute("UPDATE objectives SET delivery_level=? WHERE id=?", (out.delivery_level.value, oid))
            if out.items is not None:
                self._expand_for_each(obj, step, out.items)
                if self.on_items_collected:
                    try:
                        self.on_items_collected(obj, step, out.items)
                    except Exception:  # noqa: BLE001 - gravar histórico nunca pode derrubar a etapa que deu certo
                        log.exception("registro do que foi lido na etapa %s", step.id)
            repo.emit_objective(oid)             # progresso ao vivo no painel
            return
        if o == Outcome.yielded:
            repo.refund_attempt(step.id)
            repo.finish_attempt(attempt_id, AttemptStatus.interrupted,
                                recovery={"pause": "Pausado pelo usuário num ponto seguro",
                                          "takeover": "Usuário assumiu o controle; a etapa será reobservada ao retomar"}
                                .get(detail or "", detail))
            repo.transition_step(step.id, StepStatus.ready, detail=f"interrompida ({detail}); será reobservada")
            return
        if o == Outcome.cancelled:
            repo.finish_attempt(attempt_id, AttemptStatus.cancelled, error="Cancelado pelo usuário")
            repo.transition_step(step.id, StepStatus.cancelled, detail="cancelada pelo usuário")
            return
        if o == Outcome.retry:
            repo.finish_attempt(attempt_id, AttemptStatus.failed, error=detail,
                                recovery=f"Nova tentativa automática (ação segura) em {self.get_settings().retry_backoff_s}s")
            repo.transition_step(step.id, StepStatus.retry_wait, detail=detail,
                                 next_retry_at=iso_in(self.get_settings().retry_backoff_s), level="warn")
            return
        if o == Outcome.waiting_user:
            repo.refund_attempt(step.id)
            repo.finish_attempt(attempt_id, AttemptStatus.interrupted, error=detail, recovery="Aguardando o usuário")
            repo.transition_step(step.id, StepStatus.waiting_user, detail=detail, level="warn")
            # `blocked_kind='ai'` (achado #93, ponto 4): distingue, na tela, "a IA está travando este item"
            # (chave ausente, sem crédito, recusa por política) de política do perfil, limite ou aprovação.
            repo.set_objective(oid, ObjectiveStatus.waiting_user, detail=detail, blocked_reason=detail, needs=out.needs,
                               level="warn", message=f"{rt.id}: bloqueado — {detail}",
                               blocked_kind="ai" if out.ai_blocked else None)
            rt.attention = f"Bloqueado: {detail}"
            return
        if o == Outcome.uncertain:
            repo.finish_attempt(attempt_id, AttemptStatus.uncertain, error=detail,
                                recovery="Reconciliação pela tela não comprovou o resultado; sem reenvio automático")
            repo.transition_step(step.id, StepStatus.uncertain, detail=detail, level="warn")
            repo.set_objective(oid, ObjectiveStatus.uncertain, detail=detail, blocked_reason=detail,
                               needs="Confira no aparelho se o efeito ocorreu e decida: confirmar, repetir ou abandonar. "
                                     "Nada será reenviado automaticamente.",
                               delivery_level=out.delivery_level, level="warn", message=f"{rt.id}: resultado INCERTO — {detail}")
            rt.attention = "Resultado incerto: requer revisão"
            return
        if o == Outcome.device_stuck:
            fired, _ = repo.commit_state(step.id)
            repo.finish_attempt(attempt_id, AttemptStatus.uncertain if fired else AttemptStatus.failed, error=detail)
            repo.transition_step(step.id, StepStatus.uncertain if (step.side_effect and fired) else StepStatus.failed,
                                 detail=detail, level="error")
            repo.set_objective(oid, ObjectiveStatus.uncertain if (step.side_effect and fired) else ObjectiveStatus.waiting_user,
                               detail=detail, blocked_reason=detail, needs=out.needs, level="error")
            rt.attention = "Aparelho retido: chamada anterior ainda não terminou"
            return
        # failed
        repo.finish_attempt(attempt_id, AttemptStatus.failed, error=detail)
        repo.transition_step(step.id, StepStatus.failed, detail=detail, level="error")
        if out.plan_defect:                  # refazer o MESMO plano falharia igual (e custaria igual) em todo aparelho
            self._fail_objective(obj, f"Etapa '{step.title}': {detail}")
            self._hold_siblings(obj, step)
            return
        if not self._try_recover(obj, step, detail or "falha"):
            if not self._skip_failed_item(obj, step, detail or "falha"):
                self._fail_objective(obj, f"Etapa '{step.title}' falhou: {detail}")

    def _hold_siblings(self, obj: Any, step: Any) -> None:
        """Defeito do plano visto por um aparelho: os que ainda NÃO começaram não gastam IA para falhar igual."""
        reason = (f"Não iniciado: em {obj['instance_id']} a etapa '{step.title}' mostrou um defeito do plano "
                  "(pós-condição não comprovável pela tela).")
        for o in self.repo.db.query("SELECT * FROM objectives WHERE run_id=? AND id<>? AND status=?",
                                    (obj["run_id"], obj["id"], ObjectiveStatus.pending.value)):
            if o["instance_id"] not in self.workers:
                self._block(o, reason, "Refaça o comando de forma mais específica (ex.: alvos nomeados). "
                                       "“Tentar novamente” executa este item mesmo assim.")

    # ------------------------------------------------------------------ repetição sobre lista lida da tela
    def _collected(self, objective_id: str) -> dict[str, list[str]]:
        return loads(self.repo.objective_row(objective_id)["collected"], {}) or {}

    def _expand_for_each(self, obj: Any, step: Any, items: list[str]) -> None:
        """A coleta terminou: as etapas-modelo `for_each` viram uma cópia por item (nova versão do plano)."""
        repo = self.repo
        collected = {**self._collected(obj["id"]), step.key: items}
        repo.db.execute("UPDATE objectives SET collected=? WHERE id=?", (dumps(collected), obj["id"]))
        plan = Plan.model_validate_json(repo.run_row(obj["run_id"])["plan"])
        done = {r["key"] for r in repo.db.query("SELECT key FROM steps WHERE objective_id=? AND status='succeeded'",
                                                (obj["id"],))}
        steps = [s for s in expand(plan.steps, collected) if s.key not in done]
        if steps:
            repo.revise_plan(obj["id"], f"Expandido para {len(items)} item(ns) lidos em '{step.title}'", steps)

    def _skip_failed_item(self, obj: Any, step: Any, detail: str) -> bool:
        """Etapa de UM item falhou de vez (sem efeito disparado): pula só o resto DESTE item; os demais seguem."""
        item = (step.variables or {}).get("item")
        idx = (step.variables or {}).get("item_index")
        if not item or not idx:
            return False
        repo = self.repo
        for r in repo.db.query("SELECT id, variables FROM steps WHERE objective_id=? AND plan_version=? AND status IN "
                               "('pending','ready','retry_wait')", (obj["id"], step.plan_version)):
            if (loads(r["variables"], {}) or {}).get("item_index") == idx:
                repo.transition_step(r["id"], StepStatus.cancelled, detail=f"item “{item}” falhou antes desta etapa")
        repo.decision(f"{obj['instance_id']}: item “{item}” falhou em '{step.title}' ({detail}); os demais itens seguem",
                      run_id=obj["run_id"], instance_id=obj["instance_id"], step_id=step.id)
        return True

    def _settle_items(self, o: Any) -> bool:
        """Objetivo com itens: quando não resta nada executável, fecha a conta. Falha parcial NUNCA vira sucesso."""
        repo = self.repo
        rows = repo.db.query("SELECT id, key, status, depends_on, variables FROM steps WHERE objective_id=? AND plan_version=?",
                             (o["id"], o["plan_version"]))
        if not any(r["variables"] for r in rows) or any(r["status"] in (
                "ready", "running", "verifying", "retry_wait", "waiting_user", "uncertain") for r in rows):
            return False
        dead = {r["key"] for r in rows if r["status"] in ("failed", "cancelled")}
        if not dead:
            return False
        changed = True
        while changed:                                # quem dependia de item que falhou também não roda
            changed = False
            for r in rows:
                if r["status"] == "pending" and r["key"] not in dead and set(loads(r["depends_on"], [])) & dead:
                    repo.transition_step(r["id"], StepStatus.cancelled, detail="dependia de item que falhou")
                    dead.add(r["key"])
                    changed = True
        if any(r["status"] == "pending" and r["key"] not in dead for r in rows):
            return False                              # ainda há etapa que pode ser promovida
        # a conta é por ITEM e atravessa versões do plano (uma recuperação recomeça só com o que faltava)
        names: dict[str, str] = {}
        keys: dict[str, set[str]] = {}
        proven: set[str] = set()
        for r in repo.db.query("SELECT key, status, variables FROM steps WHERE objective_id=? AND variables IS NOT NULL",
                               (o["id"],)):
            v = loads(r["variables"], {}) or {}
            if v.get("item_index"):
                names[v["item_index"]] = v.get("item", "?")
                keys.setdefault(v["item_index"], set()).add(r["key"])
                if r["status"] == "succeeded":
                    proven.add(r["key"])
        by_item = {i: (names[i], keys[i] <= proven) for i in names}
        bad = [name for name, ok in by_item.values() if not ok]
        detail = (f"{len(by_item) - len(bad)} de {len(by_item)} itens concluídos e comprovados; falharam: "
                  + ", ".join(bad)[:300] + ". “Tentar novamente” refaz só os que falharam.")
        repo.set_objective(o["id"], ObjectiveStatus.failed, detail=detail, blocked_reason=detail, level="error")
        return True

    def _fail_objective(self, obj: Any, detail: str) -> None:
        # O motivo real, e não "etapa anterior falhou": o objetivo também morre por prazo total esgotado, e aí
        # nenhuma etapa falhou. Quem abre a etapa cancelada precisa ler o que de fato aconteceu.
        self.repo.cancel_open_steps(obj["run_id"], objective_id=obj["id"], reason=detail)
        self.repo.set_objective(obj["id"], ObjectiveStatus.failed, detail=detail, blocked_reason=detail, level="error")
        self._expirar_aprovacoes(obj["id"], "objetivo abandonado: " + detail)

    def _expirar_aprovacoes(self, objective_id: str, reason: str) -> None:
        if self.expirar_aprovacoes_do_objetivo is not None:
            self.expirar_aprovacoes_do_objetivo(objective_id, reason=reason)

    def _maybe_complete(self, objective_id: str) -> None:
        o = self.repo.objective_row(objective_id)
        if o["status"] not in (ObjectiveStatus.running.value, ObjectiveStatus.pending.value):
            return
        if self._settle_items(o):
            return
        done, total = self.repo._step_progress(objective_id, o["plan_version"])  # noqa: SLF001
        if total and done == total:
            unverified = self.repo.db.scalar(
                "SELECT COUNT(*) FROM steps WHERE objective_id=? AND status='succeeded' AND result LIKE '%\"verified\":false%'",
                (objective_id,))
            detail = ("Todas as etapas concluídas" + (f" ({unverified} confirmada(s) manualmente pelo usuário)" if unverified
                                                      else " e comprovadas por observação da tela"))
            self.repo.set_objective(objective_id, ObjectiveStatus.succeeded, detail=detail,
                                    delivery_level=DeliveryLevel(o["delivery_level"]) if o["delivery_level"] else None,
                                    message=f"{o['instance_id']}: objetivo concluído — {detail}")

    # ------------------------------------------------------------------ recuperação / revisão de plano
    def recovery_steps(self, run: Any, objective_id: str) -> list[PlanStep]:
        """Etapas ainda não comprovadas + as dependências de navegação necessárias para refazê-las.
        Nunca atravessa (nem repete) uma etapa com efeito externo já comprovada."""
        plan = Plan.model_validate_json(run["plan"])
        plan = plan.model_copy(update={"steps": expand(plan.steps, self._collected(objective_id))})
        by_key = {s.key: s for s in plan.steps}
        proven = {r["key"]: bool(r["side_effect"]) for r in self.repo.db.query(
            "SELECT key, side_effect FROM steps WHERE objective_id=? AND status='succeeded'", (objective_id,))}
        # Rejeitada é DECISÃO, não lacuna: recriar a chave reabriria, com OUTRO texto, uma aprovação que alguém já
        # recusou — e a recusa, que a tela promete ser definitiva, não sobreviveria à primeira falha de qualquer
        # outra etapa do mesmo objetivo.
        # Só a rejeição, e não `cancelled` em geral: `_skip_failed_item` também cancela (o resto de um item que
        # falhou) e essas TÊM de voltar, senão “Tentar novamente” deixa de refazer justamente os itens que
        # falharam, que é o que ele promete. As chaves de um bloco `for_each` já vêm com o sufixo do item
        # (`_i1`, `_i2`), então fechar a conta de um alvo nunca fecha a dos irmãos.
        decidido = {r["key"] for r in self.repo.db.query(
            "SELECT key FROM steps WHERE objective_id=? AND status='cancelled' AND status_detail LIKE ?",
            (objective_id, MOTIVO_REJEICAO + "%"))} - set(proven)
        needed: set[str] = set()

        def visit(key: str) -> None:
            if key in needed or key not in by_key or key in decidido:
                return
            if proven.get(key):            # efeito externo já comprovado: fronteira
                return
            needed.add(key)
            for dep in by_key[key].depends_on:
                visit(dep)

        for s in plan.steps:
            if s.key not in proven and s.key not in decidido:
                visit(s.key)
        return [s.model_copy(update={"depends_on": [d for d in s.depends_on if d in needed]})
                for s in plan.steps if s.key in needed]

    def _try_recover(self, obj: Any, step: Any, detail: str) -> bool:
        fired, _ = self.repo.commit_state(step.id)
        if step.side_effect and fired:
            return False
        o = self.repo.objective_row(obj["id"])
        recoveries = self.repo.db.scalar("SELECT COUNT(*) FROM plan_versions WHERE objective_id=? AND reason LIKE ?",
                                         (obj["id"], "Recuperação automática%"))
        if recoveries >= MAX_PLAN_REVISIONS:          # expandir um for_each não conta como recuperação
            return False
        run = self.repo.run_row(obj["run_id"])
        steps = self.recovery_steps(run, obj["id"])
        if not steps:
            return False
        reason = f"Recuperação automática após falha em '{step.title}': {detail}"
        self.repo.revise_plan(obj["id"], reason, steps)
        try:
            app, _ = self._app_context(run, self.devices.get(obj["instance_id"]))
            if app.package:
                self._restart_app[obj["instance_id"]] = app.package
        except KeyError:
            pass
        self.repo.decision(f"{obj['instance_id']}: {reason}. Refazendo a navegação a partir de um estado conhecido; "
                           "etapas com efeito externo já comprovadas não serão repetidas.",
                           run_id=obj["run_id"], instance_id=obj["instance_id"])
        return True

    # ------------------------------------------------------------------ cancelamento
    def _finish_cancel(self, run: Any) -> None:
        run_id = run["id"]
        # Só os objetivos de aparelho que EU hospedo (item 5.1). Sem o filtro, o segundo backend cancelava o
        # objetivo que o primeiro estava executando naquele instante — e a execução ficava "cancelada" com uma
        # etapa viva no aparelho do outro. Cada um cancela o seu; `recompute_run` fecha a execução quando o
        # último terminar.
        for o in self.repo.db.query(
                "SELECT * FROM objectives WHERE run_id=? AND status IN ('pending','running','waiting_user')"
                + self.repo.so_meu("instance_id"), (run_id, self.repo.owner_id)):
            if o["instance_id"] in self.workers and o["status"] == "running":
                continue                    # o worker cancela no próximo ponto seguro
            self.repo.cancel_open_steps(run_id, objective_id=o["id"], reason="execução cancelada")
            effects = loads(o["effects"], [])
            self.repo.set_objective(o["id"], ObjectiveStatus.cancelled,
                                    detail="Cancelado. " + (f"Ações com efeito externo já realizadas: {len(effects)}."
                                                            if effects else "Nenhuma ação com efeito externo foi realizada."))
            self._expirar_aprovacoes(o["id"], "execução cancelada")
        self.repo.recompute_run(run_id)

    def _manter_posse(self) -> None:
        """Renova a posse do que é meu e adota o que outro backend abandonou.

        As duas coisas no mesmo relógio porque são o mesmo assunto visto dos dois lados: enquanto eu renovo,
        ninguém me adota; quando eu paro, alguém me adota. O `_tick` roda a cada segundo, e isto não — uma escrita
        por segundo por aparelho ativo seria desperdício num lease de `POSSE_TTL_S`.
        """
        agora = time.monotonic()
        if agora - self._posse_renovada < RENOVAR_POSSE_S:
            return
        self._posse_renovada = agora
        self.repo.renew_claims()
        # As vagas de IA vivem do mesmo fôlego: enquanto eu respiro, ninguém as toma; quando eu paro, elas
        # vencem sozinhas e o teto do sistema volta ao normal sem ninguém precisar limpá-las.
        self.ai_slots.renovar()
        try:
            self.adotar_abandonadas()
        except Exception:  # noqa: BLE001 - adoção falha não pode derrubar o tick
            log.exception("falha ao adotar etapas abandonadas")

    # ------------------------------------------------------------------ reinício do backend
    def reconcile_after_restart(self) -> None:
        """Etapas que estavam em execução quando o backend caiu: nada é reexecutado às cegas.

        - ações `intended` viram `unknown` (podem ter chegado ao aparelho);
        - a tentativa é marcada `interrupted` e não consome tentativa;
        - a etapa volta para `ready`: o executor reobserva a tela; se for etapa com efeito já disparado,
          ele SÓ verifica (reconciliação) e, sem prova, marca `uncertain`.
        """
        # Vaga de IA marcada como minha antes da queda: solto AGORA, senão o teto do sistema fica menor por
        # `VAGA_TTL_S` a cada reinício deste backend.
        self.ai_slots.soltar_todas()
        rows = self.repo.interrupted_steps()
        self._reconciliar(rows, "backend reiniciado")
        if rows:
            self.repo.bus.emit("log", f"Backend reiniciado: {len(rows)} etapa(s) interrompida(s) serão reconciliadas"
                                      " pela tela.", level="warn")
        self.wake()

    def adotar_abandonadas(self) -> int:
        """Etapas de outro backend cujo lease venceu: ele caiu, e o trabalho não pode ficar parado para sempre.

        Deliberadamente NÃO é o mesmo caminho do reinício. Aqui a única prova de que o dono morreu é ele ter parado
        de renovar por mais de `POSSE_TTL_S`, então a etapa é adotada em nome próprio ANTES de ser reconciliada —
        sem isso, dois backends poderiam reconciliar a mesma etapa ao mesmo tempo.
        """
        rows = self.repo.abandoned_steps()
        if not rows:
            return 0
        # Só reconcilia o que ele GANHOU: com dois backends vivos disputando a etapa de um terceiro que morreu, o
        # `take_over` é que decide, e o perdedor não pode reconciliar por cima de quem ganhou.
        adotadas = [s for s in rows if self.repo.take_over(s)]
        if not adotadas:
            return 0
        self._reconciliar(adotadas, "o backend que executava esta etapa parou de responder")
        self.repo.bus.emit("log", f"{len(adotadas)} etapa(s) abandonada(s) por outro servidor foram adotadas e serão"
                                  " reconciliadas pela tela.", level="warn")
        return len(adotadas)

    def _reconciliar(self, rows: list[Any], causa: str) -> None:
        """Nada é reexecutado às cegas: a etapa volta para `ready` e o executor REOBSERVA antes de agir."""
        repo = self.repo
        for s in rows:
            with repo.db.tx():
                for a in repo.db.query(
                        "SELECT a.id, a.tool FROM actions a JOIN attempts t ON t.id=a.attempt_id"
                        " WHERE t.step_id=? AND a.status='intended'", (s["id"],)):
                    repo.db.execute("UPDATE actions SET status=?, effect_possible=1, error=?, done_at=? WHERE id=?",
                                    (ActionStatus.unknown.value, f"{causa}; resultado da ação desconhecido",
                                     now_iso(), a["id"]))
                repo.db.execute(
                    "UPDATE attempts SET status=?, finished_at=?, error=COALESCE(error, ?), recovery=? WHERE step_id=? AND status='running'",
                    (AttemptStatus.interrupted.value, now_iso(), f"Tentativa interrompida: {causa}",
                     "Reconciliar pelo estado real da tela antes de continuar", s["id"]))
                repo.refund_attempt(s["id"])
            fired, _ = repo.commit_state(s["id"])
            repo.transition_step(s["id"], StepStatus.ready, level="warn",
                                 detail=f"{causa}: " + ("efeito externo possivelmente disparado — só verificar"
                                                        if fired else "reobservar a tela e continuar"))
