"""Operações de execução pedidas pelo painel: criar/planejar, iniciar, pausar, continuar, cancelar,
retomar itens elegíveis, resolver bloqueios e gerar o relatório final."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..db import loads
from ..devices.compatibilidade import capacidades_de, motivo_incompativel, requisitos_de_release
from ..devices.manager import DeviceManager
from ..devices.verbs import verbos_suportados
from ..models import (RUN_TERMINAL, InstanceState, ObjectiveDTO, ObjectiveStatus, ResolveBody, RunCreate, RunStatus,
                      RunSummary, StepResult, StepStatus)
from ..planning.capabilities import load_catalog
from ..planning.catalog import capabilities_of
from ..planning.provider import AIError, AIProvider, AppContext, PlanRequest
from .repository import Repository
from .scheduler import WAKEABLE, Scheduler

log = logging.getLogger("poc.runs")

#: Estados de onde, com o rodízio LIGADO, o aparelho volta ao ar sozinho: os que ele acorda (`WAKEABLE`) mais o
#: desligamento em voo, que termina num deles. Com o rodízio desligado, nenhum destes volta sem uma pessoa.
_VOLTAM_COM_RODIZIO = WAKEABLE | {InstanceState.stopping}


class RunError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        # O que o painel precisa para OFERECER a saída em vez de só mostrar a recusa (ex.: a lista por aparelho
        # do pré-voo, e quais seguem aptos). Vazio = a mensagem já diz tudo.
        self.details = details or {}


def _onde(o: ObjectiveDTO) -> str:
    """"Onde rodou", numa frase — para o relatório e para quem lê o histórico meses depois.

    Nunca inventa: objetivo materializado antes da migração 022 (ou nunca despachado) não tem fotografia, e o
    honesto é dizer que não se registrou, não chutar "esta máquina".
    """
    if not (o.worker_id or o.device_serial or o.hosted_by):
        return "não registrado"
    servidor = o.worker_id or o.hosted_by or "—"
    partes = [servidor if o.worker_id else f"{servidor} (backend)"]
    if o.device_serial:
        partes.append(o.device_serial)
    if o.hosted_by and o.worker_id and o.hosted_by != o.worker_id:
        partes.append(f"despachado por {o.hosted_by}")
    return " · ".join(partes)


class RunService:
    def __init__(self, repo: Repository, scheduler: Scheduler, devices: DeviceManager, provider: AIProvider,
                 profiles: Any = None):
        self.flows = scheduler.flows
        self.repo = repo
        self.scheduler = scheduler
        self.devices = devices
        self.provider = provider
        # Serviço social (opcional): resolve perfil ↔ aparelho. Sem ele, só execução por aparelho.
        self.profiles = profiles
        self._planning: dict[str, asyncio.Task[None]] = {}

    # ------------------------------------------------------------------ criar + planejar
    def create(self, req: RunCreate) -> RunSummary:
        if req.profile_ids:
            req = req.model_copy(update={"instance_ids": self._instances_of(req.profile_ids)})
        unknown = [i for i in req.instance_ids if i not in self.devices.devices]
        if unknown:
            raise RunError("unknown_instance", f"Instância(s) desconhecida(s): {', '.join(unknown)}", 400)
        loja = [i for i in req.instance_ids if self.devices.devices[i].store]
        if loja:
            raise RunError("store_instance", f"{', '.join(loja)} é a loja (Play Store): ela só guarda o aplicativo "
                                             "oficial e não executa tarefas. Escolha aparelhos do parque.", 400)
        if (impedidos := self._incompativeis(req.instance_ids)):
            # A regra do pedido: a limitação é explicada ANTES de agendar. Sem isto, o objetivo era despachado,
            # a versão do app não instalava (ou instalava e não abria) e o operador só descobria no meio, como
            # `INSTALL_FAILED_NO_MATCHING_ABIS` ou `app_incompatible` no fundo de uma etapa.
            raise RunError("app_incompativel",
                           "Estes aparelhos não conseguem rodar a versão destinada a eles: "
                           + "; ".join(impedidos) + ".", 409)
        if (mistura := self._mistura_de_apps(req.instance_ids)) is not None:
            raise RunError(*mistura)
        self._exigir_apps_do_fluxo(req)
        # PRÉ-VOO, antes de chamar o planejador: a recusa explicada já existia para o comando do painel e não
        # existia para a execução — a tarefa era aceita, planejada (gastando chamada ao planejador) e só então
        # bloqueava no aparelho. Aqui ela para antes, com o motivo e o que fazer, por aparelho.
        req = self._exigir_pre_voo(req)
        status = self.provider.status()
        if not status.configured:
            raise RunError("ai_not_configured", status.notice, 503)
        row, created = self.repo.create_run(req, simulated=self.provider.simulated)
        if created:
            self._spawn_planning(row["id"])
        return self.repo.run_summary(self.repo.run_row(row["id"]), deduplicated=not created)

    # ------------------------------------------------------------------ apps da seleção
    def pacotes_da_selecao(self, instance_ids: list[str]) -> dict[str, str]:
        """`{aparelho: pacote}` dos aparelhos escolhidos que têm app definido. Uma consulta, sem efeito nenhum."""
        if not instance_ids:
            return {}
        marcas = ",".join("?" for _ in instance_ids)
        linhas = self.repo.db.query(
            f"SELECT i.id AS instance_id, a.package FROM instances i JOIN apps a ON a.id = i.app_id"
            f" WHERE i.id IN ({marcas})", tuple(instance_ids))
        return {r["instance_id"]: r["package"] for r in linhas if r["package"]}

    def _mistura_de_apps(self, instance_ids: list[str]) -> tuple[str, str, int] | None:
        """Recusa, ANTES de planejar, a execução que mistura aparelhos de apps diferentes quando algum tem catálogo.

        O catálogo de capabilities era escolhido POR EXECUÇÃO (`load_catalog(pacotes.pop()) if len(pacotes) == 1`):
        bastava a seleção ter aparelhos de dois apps para o planejador receber `catalog=None` e escrever etapas
        livres, sem `capability` — e a quarta porta (política, limite diário, aprovação e texto na voz de cada
        persona) deixava de opinar nas contas reais. O plano tem um `app_id` só, então a execução mista já era
        semanticamente de um app; o que ela fazia era desligar a porta em silêncio.

        A recusa acontece só quando ALGUM dos apps tem catálogo: misturar dois apps sem catálogo não perde nada,
        e recusar ali seria inventar limitação onde não há.
        """
        pacotes = self.pacotes_da_selecao(instance_ids)
        distintos = sorted(set(pacotes.values()))
        if len(distintos) < 2:
            return None
        com_catalogo = [p for p in distintos if capabilities_of(p).has_catalog]
        if not com_catalogo:
            return None
        por_app = {p: sorted(i for i, pk in pacotes.items() if pk == p) for p in distintos}
        detalhe = "; ".join(f"{capabilities_of(p).label}: {', '.join(por_app[p])}" for p in distintos)
        return ("mixed_apps",
                "Esta seleção mistura aparelhos de aplicativos diferentes, e pelo menos um deles "
                f"({', '.join(capabilities_of(p).label for p in com_catalogo)}) tem catálogo de ações com "
                "política, limite e aprovação. Planejar os dois juntos apagaria essas guardas. "
                f"Refaça a execução com aparelhos de um app só — {detalhe}.", 409)

    #: Estados em que o app já está NO aparelho e serve para trabalhar.
    _APP_PRONTO = ("ready", "installed")

    def apps_exigidos(self, command: str) -> list[Any]:
        """Os apps que o fluxo casado por este comando exige. Vazio quando não há fluxo conhecido.

        A pergunta é feita ao MESMO `flows.match` que o planejamento usa: se o comando casa, o plano (e com ele a
        lista de apps exigidos) já existe antes de agendar — que é exatamente quando dá para explicar a pendência.
        """
        if not self.scheduler.cfg.file.ai.flows:
            return []
        casado = self.flows.match(command)
        if casado is None:
            return []
        _, plan = casado
        ids = plan.required_apps or ([plan.app_id] if plan.app_id else [])
        if not ids:
            return []
        marcas = ",".join("?" for _ in ids)
        return self.repo.db.query(f"SELECT id, name, package FROM apps WHERE id IN ({marcas}) ORDER BY name",
                                  tuple(ids))

    def _exigir_apps_do_fluxo(self, req: RunCreate) -> None:
        """Recusa, ANTES de agendar, a execução cujo fluxo exige um app que ainda não está no aparelho.

        Sem isto, a pendência só aparecia depois — etapa que falha ou item bloqueado no meio da execução, sem
        ação clara. Aqui a recusa traz o que fazer, por aparelho: "distribua X em android-12".

        Só o que se SABE fecha a porta: aparelho cujo pacote nunca foi observado (sem linha em
        `device_app_state`) não é recusa — é a mesma regra das capacidades declaradas, e o app pode ser entregue
        pela porta do despacho antes da tarefa.
        """
        exigidos = self.apps_exigidos(req.command)
        if not exigidos:
            return
        faltas: list[dict[str, str]] = []
        for app in exigidos:
            for iid in req.instance_ids:
                linha = self.repo.db.one(
                    "SELECT state, desired_release_id FROM device_app_state WHERE instance_id=? AND package_name=?",
                    (iid, app["package"]))
                if linha is None:
                    continue                      # nunca observado: o que não se sabe não fecha a porta
                if linha["state"] in self._APP_PRONTO or linha["desired_release_id"]:
                    continue                      # está lá, ou há versão desejada a caminho pela porta do app
                faltas.append({"instance_id": iid, "app": app["name"], "package": app["package"],
                               "estado": str(linha["state"])})
        if not faltas:
            return
        detalhe = "; ".join(f"{f['app']} em {f['instance_id']} (estado: {f['estado']})" for f in faltas)
        raise RunError(
            "missing_required_app",
            f"Este comando usa {', '.join(a['name'] for a in exigidos)}, e o aplicativo não está pronto em todos "
            f"os aparelhos escolhidos — {detalhe}.", 409,
            details={"missing": faltas,
                     "acao": "; ".join(f"Distribua {f['app']} em {f['instance_id']}"
                                       for f in faltas)})

    # ------------------------------------------------------------------ pré-voo
    def pre_voo(self, instance_ids: list[str], *, ao_iniciar: bool = False) -> dict[str, dict[str, str]]:
        """Por que a tarefa NÃO pode acontecer em cada um destes aparelhos, conferido antes de agendar.

        Uma pergunta, uma resposta, com três usos: a recusa de `create` (antes de gastar o planejador), o motivo
        específico que `start` grava no item, e a lista que o painel mostra para quem escolheu os aparelhos.

        `ao_iniciar` acrescenta o que ESPERAR resolve e criar não: um aparelho DESTA máquina que está parado com o
        rodízio desligado não é motivo para recusar a criação — quem pediu a tarefa pode ir ligá-lo, e a execução
        continua valendo. No início, sim: ali o item para com o motivo, como sempre parou.

        Só entra aqui o que se SABE. Aparelho cujo app nunca foi observado, worker em manutenção, estado
        desconhecido: nada disso vira recusa — o que não se sabe nunca fecha a porta (é a mesma regra das
        capacidades declaradas). Devolve `{aparelho: {code, motivo, acao}}`; ausente = apto.
        """
        impedidos: dict[str, dict[str, str]] = {}
        liga_sozinho = self.scheduler.get_settings().auto_start_devices
        for iid in instance_ids:
            rt = self.devices.devices.get(iid)
            if rt is None:
                continue                          # `create` já recusou o desconhecido; aqui não há o que dizer
            if rt.external and rt.state != InstanceState.online:
                # O rodízio passou a ligar aparelho de outra máquina PELO WORKER. Então a recusa aqui deixou de
                # ser sobre "ser de outra máquina" e passou a ser sobre não haver quem o ligue: worker que não
                # declara `start`, ou rodízio desligado. Prometer o que ninguém faz continua proibido — só que
                # agora, com agente conectado e rodízio ligado, alguém faz.
                pode_ligar = "start" in verbos_suportados(rt) and rt.worker_id is not None
                if not (pode_ligar and liga_sozinho and rt.state in _VOLTAM_COM_RODIZIO):
                    impedidos[iid] = {
                        "code": "remote_off",
                        "motivo": f"é um aparelho de outra máquina e está {rt.state.value}: " + (
                            "o rodízio está desligado, então ninguém o liga sozinho." if pode_ligar
                            else "nenhum servidor conectado sabe ligá-lo."),
                        "acao": ("Ligue-o pela Infraestrutura (o servidor que o hospeda sabe iniciá-lo), ou ligue o "
                                 "rodízio em Ajustes, e repita." if pode_ligar else
                                 "Ligue-o na máquina que o hospeda e confira a conexão do ADB; depois repita.")}
                    continue
            if (ao_iniciar and rt.state not in (InstanceState.online, InstanceState.booting)
                    and not (liga_sozinho and rt.state in _VOLTAM_COM_RODIZIO)):
                impedidos[iid] = {
                    "code": "device_off",
                    "motivo": f"o aparelho não estava online no início da execução (estado: {rt.state.value}) "
                              "e o rodízio está desligado.",
                    "acao": "Inicie a instância e retome este item — ou ligue o rodízio em Ajustes."}
                continue
            if rt.worker_id and not self.repo.db.one("SELECT id FROM workers WHERE id=?", (rt.worker_id,)):
                # Servidor NÃO INSCRITO é o único caso de worker que esperar não resolve: sem inscrição não há
                # canal, nem ciclo de vida, nem quem execute. Manutenção e queda de canal continuam sendo ESPERA
                # — é o que o scheduler já faz (`worker_gate` → `note_waiting`), e trocar isso por recusa mudaria
                # o significado da manutenção, que é "suspende novas atribuições", não "cancela o trabalho".
                impedidos[iid] = {
                    "code": "worker_unenrolled",
                    "motivo": f"o servidor '{rt.worker_id}' que hospeda este aparelho não está inscrito: "
                              "ninguém aqui consegue operá-lo.",
                    "acao": "Inscreva o servidor em Infraestrutura (ou devolva o aparelho a esta máquina) e repita."}
                continue
            if self.scheduler.app_preflight is not None:
                if (recusa := self.scheduler.app_preflight(rt)) is not None:
                    impedidos[iid] = recusa
        return impedidos

    def _exigir_pre_voo(self, req: RunCreate) -> RunCreate:
        """Aplica o pré-voo: recusa com a lista por aparelho, ou segue só com os aptos quando foi isso que se pediu."""
        impedidos = self.pre_voo(req.instance_ids)
        if not impedidos:
            return req
        aptos = [i for i in req.instance_ids if i not in impedidos]
        detalhes = {"devices": [{"instance_id": i, **impedidos[i]} for i in req.instance_ids if i in impedidos],
                    "ready": aptos}
        if req.only_ready and aptos:
            return req.model_copy(update={"instance_ids": aptos})
        frases = "; ".join(f"{i}: {impedidos[i]['motivo']}" for i in req.instance_ids if i in impedidos)
        if not aptos:
            raise RunError("preflight", f"Nenhum aparelho escolhido pode executar isto agora. {frases}", 409, detalhes)
        raise RunError("preflight",
                       f"{len(impedidos)} de {len(req.instance_ids)} aparelhos não podem executar isto agora. "
                       f"{frases} Você pode seguir só com os aptos: {', '.join(aptos)}.", 409, detalhes)

    def _incompativeis(self, instance_ids: list[str]) -> list[str]:
        """Frases explicando quais aparelhos não rodam a versão DESEJADA do app deles, na ordem pedida.

        A pergunta é feita sobre a versão que o parque mandou aquele aparelho ter (`device_app_state`), porque é
        ela que a execução vai instalar pela porta do app. Aparelho sem versão desejada não tem o que conferir —
        e capacidade desconhecida nunca vira recusa (ver `devices/compatibilidade.py`).
        """
        motivos: list[str] = []
        for iid in instance_ids:
            rt = self.devices.devices.get(iid)
            if rt is None:
                continue
            # Amarrado ao APP daquele aparelho: `device_app_state` guarda uma linha por PACOTE, e uma versão
            # desejada de um pacote que não é o app da tarefa não tem por que impedir a execução.
            linha = self.repo.db.one(
                "SELECT r.* FROM device_app_state s"
                " JOIN app_releases r ON r.id = s.desired_release_id"
                " JOIN instances i ON i.id = s.instance_id"
                " JOIN apps a ON a.id = i.app_id AND a.package = s.package_name"
                " WHERE s.instance_id=?", (iid,))
            if linha is None:
                continue
            if (porque := motivo_incompativel(requisitos_de_release(linha), capacidades_de(rt),
                                              aparelho=iid)) is not None:
                motivos.append(porque)
        return motivos

    def _instances_of(self, profile_ids: list[str]) -> list[str]:
        """Perfil sem aparelho vinculado não executa: o comando não teria onde acontecer."""
        if self.profiles is None:
            raise RunError("profiles_unavailable", "Execução por perfil indisponível nesta instalação.", 400)
        ids: list[str] = []
        for pid in profile_ids:
            iid = self.profiles.instance_of(pid)
            if not iid:
                raise RunError("no_binding", f"O perfil {pid} não está vinculado a nenhum aparelho.", 409)
            ids.append(iid)
        return list(dict.fromkeys(ids))

    def _spawn_planning(self, run_id: str) -> None:
        if run_id in self._planning and not self._planning[run_id].done():
            return
        self._planning[run_id] = asyncio.create_task(self._plan(run_id), name=f"plan-{run_id}")

    def resume_planning_after_restart(self) -> None:
        for r in self.repo.db.query("SELECT id FROM runs WHERE status='planning'"):
            self.repo.bus.emit("log", f"Execução {r['id']}: planejamento interrompido pelo reinício; replanejando.",
                               level="warn", run_id=r["id"])
            self._spawn_planning(r["id"])

    async def _plan(self, run_id: str) -> None:
        repo = self.repo
        run = repo.run_row(run_id)
        assert run is not None
        ids: list[str] = loads(run["instance_ids"], [])
        instances = []
        for iid in ids:
            r = repo.db.one("SELECT app_id, account_label FROM instances WHERE id=?", (iid,))
            rt = self.devices.devices.get(iid)
            instances.append({"instance_id": iid, "account_label": r["account_label"] if r else None,
                              "app_id": r["app_id"] if r else None,
                              # perfil FOTOGRAFADO agora: se o vínculo mudar no meio, o histórico não muda de dono
                              "profile_id": self.profiles.profile_of(iid) if self.profiles else None,
                              # ONDE isto vai rodar, fotografado pelo mesmo motivo: o id lógico é um apelido que
                              # muda de aparelho por configuração, e sem isto o relatório de amanhã fala de um
                              # "android-09" que ninguém consegue reencontrar. Re-fotografado no despacho.
                              **self.scheduler.onde_roda(rt)})
        apps = [AppContext(a["id"], a["name"], a["package"], a["activity"], a["nav_hints"], loads(a["known_selectors"]))
                for a in repo.db.query("SELECT * FROM apps ORDER BY name")]
        try:
            known = self.flows.match(run["command"]) if self.scheduler.cfg.file.ai.flows else None
            if known is not None:                      # comando repetido: o plano já existe, o planejador não é chamado
                flow, plan = known
                repo.db.execute("UPDATE runs SET flow_id=? WHERE id=?", (flow["id"], run_id))
                self.flows.used(flow["id"])
                repo.decision(f"Plano reaproveitado do fluxo “{flow['name']}” (sem chamada ao planejador)", run_id=run_id)
            else:
                # App alvo conhecido e com catálogo: o planejador escolhe ações nomeadas em vez de escrever
                # etapas livres. Aparelhos com apps diferentes (ou sem app definido) seguem no caminho livre.
                pacotes = {a.package for a in apps if a.id in {i.get("app_id") for i in instances}}
                catalog = load_catalog(pacotes.pop()) if len(pacotes) == 1 else None
                async with self.scheduler.ai_limiter:
                    plan, usage = await self.provider.plan(PlanRequest(command=run["command"], run_id=run_id,
                                                                       instances=instances, apps=apps,
                                                                       catalog=catalog))
                repo.add_usage(run_id, None, usage)
        except AIError as exc:
            repo.set_run_status(run_id, RunStatus.failed, f"Planejamento falhou: {exc}", level="error")
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("planejamento %s", run_id)
            repo.set_run_status(run_id, RunStatus.failed, f"Erro interno no planejamento: {exc}", level="error")
            return
        repo.save_plan(run_id, plan)
        repo.decision(f"Plano ({'SIMULADO' if plan.planner.simulated else plan.planner.model}): {plan.summary} — "
                      f"{len(plan.steps)} etapa(s): " + " → ".join(s.title for s in plan.steps), run_id=run_id)
        if plan.missing or not plan.steps:
            questions = " | ".join(m.question for m in plan.missing) or "O plano veio sem etapas."
            repo.set_run_status(run_id, RunStatus.needs_input, questions, level="warn",
                                message=f"Execução {run_id}: faltam informações — {questions}")
            return
        repo.materialize(run_id, plan, instances)      # persistido ANTES de executar
        run = repo.run_row(run_id)
        if run and run["cancel_requested"]:
            repo.set_run_status(run_id, RunStatus.cancelled, "Cancelada durante o planejamento")
            return
        if run and run["mode"] == "execute":
            self.start(run_id)
        else:
            repo.set_run_status(run_id, RunStatus.planned, "Plano pronto para inspeção",
                                message=f"Execução {run_id}: plano pronto; aguardando início")

    # ------------------------------------------------------------------ controles
    def _run(self, run_id: str) -> Any:
        run = self.repo.run_row(run_id)
        if run is None:
            raise RunError("not_found", "Execução não encontrada.", 404)
        return run

    def start(self, run_id: str) -> RunSummary:
        run = self._run(run_id)
        if run["status"] not in (RunStatus.planned.value, RunStatus.planning.value):
            raise RunError("invalid_state", f"A execução está em '{run['status']}' e não pode ser iniciada.")
        if not self.repo.db.scalar("SELECT COUNT(*) FROM objectives WHERE run_id=?", (run_id,)):
            raise RunError("no_plan", "A execução ainda não tem plano materializado.")
        self.repo.set_run_status(run_id, RunStatus.running, None, message=f"Execução {run_id} iniciada")
        # O MESMO pré-voo da criação, agora item a item: um plano pronto pode ficar dias parado, e o que estava
        # apto na criação pode não estar mais. O motivo específico ("é de outra máquina e está stopped", "o
        # servidor está em manutenção", "a entrega do app falhou") substitui o antigo "Aparelho offline", que
        # mandava o operador ligar um aparelho quando o problema era outro.
        alvos = list(self.repo.db.query("SELECT * FROM objectives WHERE run_id=?", (run_id,)))
        impedidos = self.pre_voo([o["instance_id"] for o in alvos], ao_iniciar=True)
        for o in alvos:
            recusa = impedidos.get(o["instance_id"])
            if recusa is None:
                continue
            self.repo.set_objective(o["id"], ObjectiveStatus.waiting_user, level="warn",
                                    detail=f"No início da execução, {recusa['motivo']}",
                                    blocked_reason=recusa["motivo"], needs=recusa["acao"])
        self.repo.recompute_run(run_id)
        self.scheduler.wake()
        return self.repo.run_summary(self._run(run_id))

    def pause(self, run_id: str) -> RunSummary:
        run = self._run(run_id)
        if run["status"] != RunStatus.running.value:
            raise RunError("invalid_state", "Só é possível pausar uma execução em andamento.")
        self.repo.db.execute("UPDATE runs SET pause_requested=1 WHERE id=?", (run_id,))
        self.repo.set_run_status(run_id, RunStatus.paused, "Pausada: nenhum novo despacho; ações em curso terminam no ponto seguro",
                                 message=f"Execução {run_id} pausada")
        return self.repo.run_summary(self._run(run_id))

    def resume(self, run_id: str) -> RunSummary:
        run = self._run(run_id)
        if run["status"] != RunStatus.paused.value:
            raise RunError("invalid_state", "A execução não está pausada.")
        self.repo.db.execute("UPDATE runs SET pause_requested=0 WHERE id=?", (run_id,))
        self.repo.set_run_status(run_id, RunStatus.running, None,
                                 message=f"Execução {run_id} retomada; cada aparelho reobserva a tela antes de agir")
        self.scheduler.executor.clear_ai_breaker(run_id)   # disjuntor de conta de IA: solta para esta execução
        self.scheduler.wake()
        return self.repo.run_summary(self._run(run_id))

    def cancel(self, run_id: str) -> RunSummary:
        run = self._run(run_id)
        status = RunStatus(run["status"])
        if status in RUN_TERMINAL and status != RunStatus.completed_with_issues:
            raise RunError("invalid_state", "A execução já terminou.")
        self.repo.db.execute("UPDATE runs SET cancel_requested=1, pause_requested=0 WHERE id=?", (run_id,))
        if status in (RunStatus.planned, RunStatus.needs_input, RunStatus.planning):
            self.repo.cancel_open_steps(run_id, reason="execução cancelada")
            for o in self.repo.db.query("SELECT id FROM objectives WHERE run_id=?", (run_id,)):
                self.repo.set_objective(o["id"], ObjectiveStatus.cancelled, detail="Cancelado antes de iniciar.")
            self.repo.set_run_status(run_id, RunStatus.cancelled, "Cancelada antes de iniciar")
        else:
            self.repo.set_run_status(run_id, RunStatus.cancelling,
                                     "Cancelando: trabalho futuro interrompido; o que já foi feito permanece registrado",
                                     level="warn")
            self.scheduler.wake()
        return self.repo.run_summary(self._run(run_id))

    # ------------------------------------------------------------------ retomadas
    def _requeue(self, obj: Any, reason: str) -> None:
        run = self._run(obj["run_id"])
        steps = self.scheduler.recovery_steps(run, obj["id"])
        if not steps:
            raise RunError("nothing_to_retry", "Não há etapas pendentes para refazer neste item.")
        self.repo.revise_plan(obj["id"], reason, steps)
        self.repo.db.execute("UPDATE objectives SET started_at=NULL, finished_at=NULL WHERE id=?", (obj["id"],))
        self.repo.set_objective(obj["id"], ObjectiveStatus.pending, detail=reason,
                                message=f"{obj['instance_id']}: item retomado — {reason}")
        rt = self.devices.devices.get(obj["instance_id"])
        if rt:
            rt.attention = None
            self.devices.publish(rt)

    def retry_failed(self, run_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        if run["cancel_requested"]:
            raise RunError("invalid_state", "Execução cancelada não pode ser retomada.")
        retried, skipped = [], []
        for o in self.repo.db.query("SELECT * FROM objectives WHERE run_id=? ORDER BY instance_id", (run_id,)):
            st = o["status"]
            if st in ("failed", "waiting_user"):
                rt = self.devices.devices.get(o["instance_id"])
                startable = self.scheduler.get_settings().auto_start_devices and rt is not None and rt.state in (
                    InstanceState.stopped, InstanceState.absent, InstanceState.booting, InstanceState.stopping,
                    InstanceState.hibernated)
                if rt is None or (rt.state != InstanceState.online and not startable):
                    skipped.append({"objective_id": o["id"], "reason": "aparelho não está online"})
                    continue
                try:
                    self._requeue(o, "Retomado pelo usuário (itens elegíveis)")
                    retried.append(o["id"])
                except RunError as exc:
                    skipped.append({"objective_id": o["id"], "reason": exc.message})
            elif st == "uncertain":
                skipped.append({"objective_id": o["id"], "reason": "resultado incerto exige decisão individual "
                                                                   "(confirmar, repetir ou abandonar)"})
            elif st == "cancelled":
                skipped.append({"objective_id": o["id"], "reason": "cancelado"})
        if retried:
            self.repo.db.execute("UPDATE runs SET pause_requested=0, finished_at=NULL WHERE id=?", (run_id,))
            self.scheduler.executor.clear_ai_breaker(run_id)   # disjuntor de conta de IA: solta para esta execução
            self.repo.recompute_run(run_id)
            self.scheduler.wake()
        return {"retried": retried, "skipped": skipped}

    def resolve(self, run_id: str, objective_id: str, body: ResolveBody) -> ObjectiveDTO:
        self._run(run_id)
        try:
            obj = self.repo.objective_row(objective_id)
        except KeyError:
            raise RunError("not_found", "Objetivo não encontrado.", 404) from None
        if obj["run_id"] != run_id or obj["status"] not in ("waiting_user", "uncertain", "failed"):
            raise RunError("invalid_state", "Este item não está aguardando decisão.")
        note = f" Nota: {body.note}" if body.note else ""
        if body.resolution == "abandon":
            self.repo.cancel_open_steps(run_id, objective_id=objective_id, reason="abandonado pelo usuário")
            self.repo.set_objective(objective_id, ObjectiveStatus.failed, detail="Abandonado pelo usuário." + note,
                                    blocked_reason=obj["blocked_reason"])
        elif body.resolution == "retry":
            self._requeue(obj, "Usuário decidiu repetir este item." + note)
        elif obj["blocked_kind"] == "approval":
            # "Confirmar concluído" marcaria a etapa como feita SEM executar — e é justamente a etapa que espera
            # aprovação. A decisão aqui é outra: aprovar, editar ou rejeitar.
            raise RunError("needs_approval", "Este item aguarda aprovação: use Aprovar, Editar ou Rejeitar "
                                             "na tela de Aprovações.")
        else:  # confirm_done — vale como decisão do usuário, não como comprovação automática
            blocking = self.repo.db.one(
                "SELECT * FROM steps WHERE objective_id=? AND plan_version=? AND status IN ('uncertain','waiting_user','failed')"
                " ORDER BY seq LIMIT 1", (objective_id, obj["plan_version"]))
            if blocking is None:
                raise RunError("invalid_state", "Não há etapa aguardando confirmação.")
            if blocking["status"] == "failed":
                raise RunError("invalid_state", "Uma etapa que falhou não pode ser confirmada; use repetir ou abandonar.")
            self.repo.transition_step(blocking["id"], StepStatus.succeeded, detail="Confirmado manualmente pelo usuário." + note,
                                      result=StepResult(verified=False, evidence_text="Confirmado manualmente pelo usuário." + note))
            # A etapa vira feita, mas quem "vira fato" no histórico do perfil é a confirmação da INTERAÇÃO:
            # sem isto, relacionamento, conversa e memória seguiriam sem a mensagem que o usuário viu sair.
            if self.profiles is not None:
                self.profiles.confirm_effects_of_step(
                    obj["profile_id"], blocking["id"], evidence="Confirmado manualmente pelo usuário." + note)
            self.repo.set_objective(objective_id, ObjectiveStatus.running,
                                    detail="Usuário confirmou a etapa; seguindo com as demais." + note)
            self.scheduler._maybe_complete(objective_id)  # noqa: SLF001
        self.repo.db.execute("UPDATE runs SET finished_at=NULL WHERE id=? AND cancel_requested=0", (run_id,))
        self.repo.recompute_run(run_id)
        self.scheduler.wake()
        rt = self.devices.devices.get(obj["instance_id"])
        if rt:
            rt.attention = None
            self.devices.publish(rt)
        return self.repo.objective_dto(self.repo.objective_row(objective_id))

    # ------------------------------------------------------------------ relatório
    def report(self, run_id: str) -> dict[str, Any]:
        detail = self.repo.run_detail(run_id)
        if detail is None:
            raise RunError("not_found", "Execução não encontrada.", 404)
        per_instance = []
        for o in detail.objectives:
            steps = [s for s in detail.steps if s.objective_id == o.id and s.plan_version == o.plan_version]
            manual = [s.title for s in detail.steps if s.objective_id == o.id and s.result and not s.result.verified]
            proven = [f"{s.title}: {s.result.evidence_text}" for s in detail.steps
                      if s.objective_id == o.id and s.status == StepStatus.succeeded and s.result and s.result.verified]
            per_instance.append({
                "instance_id": o.instance_id, "status": o.status.value, "detail": o.status_detail,
                # ONDE rodou: sem isto, um relatório de "android-09" não diz se foi o emulador desta máquina ou o
                # aparelho do notebook — os dois existem no histórico com o mesmo id lógico. Só servidor e serial
                # viram coluna; backend e identidade física ficam no objetivo, para a tabela continuar legível.
                "worker_id": o.worker_id, "device_serial": o.device_serial,
                "proven": o.status == ObjectiveStatus.succeeded and not manual, "delivery_level": o.delivery_level,
                "blocked_reason": o.blocked_reason, "needs": o.needs, "effects": o.effects,
                "proven_steps": proven, "manually_confirmed_steps": manual,
                "open_steps": [s.title for s in steps if s.status not in (StepStatus.succeeded,)],
                "plan_versions": o.plan_version, "ai_calls": o.ai_calls,
                "ai_tokens": o.ai_input_tokens + o.ai_output_tokens})
        totals = detail.counts.model_dump()
        o_por_id = {o.instance_id: o for o in detail.objectives}
        untested = [p["instance_id"] for p in per_instance if p["status"] in ("pending", "cancelled")]
        md = [f"# Relatório da execução {detail.id}" + (" (MODO SIMULADO — sem uso de IA)" if detail.simulated else ""),
              "", f"Comando: {detail.command}", f"Estado: {detail.status.value} — {detail.status_detail or ''}",
              f"Instâncias solicitadas: {detail.instances_requested} · utilizadas: {detail.instances_used}", "",
              "| Instância | Onde rodou (servidor/serial) | Resultado | Entrega | Detalhe |", "|---|---|---|---|---|"]
        label = {"succeeded": "SUCESSO comprovado", "failed": "FALHA", "waiting_user": "BLOQUEADO (aguarda usuário)",
                 "uncertain": "INCERTO (requer revisão)", "cancelled": "CANCELADO", "running": "em andamento",
                 "pending": "não iniciado"}
        for p in per_instance:
            res = label.get(p["status"], p["status"])
            if p["status"] == "succeeded" and p["manually_confirmed_steps"]:
                res = "SUCESSO com etapa confirmada manualmente"
            md.append(f"| {p['instance_id']} | {_onde(o_por_id[p['instance_id']]).replace('|', '/')} | {res} | "
                      f"{p['delivery_level'] or '—'} | "
                      f"{(p['blocked_reason'] or p['detail'] or '').replace('|', '/')} |")
        md += ["", "Somente itens com SUCESSO comprovado contam como concluídos. Itens bloqueados, incertos, "
                   "cancelados ou não iniciados NÃO contam como sucesso."]
        return {"run": RunSummary(**detail.model_dump(include=set(RunSummary.model_fields))).model_dump(mode="json"),
                "totals": totals, "per_instance": per_instance, "untested": untested, "markdown": "\n".join(md)}
