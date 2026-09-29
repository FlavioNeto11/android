"""Operações de execução pedidas pelo painel: criar/planejar, iniciar, pausar, continuar, cancelar,
retomar itens elegíveis, resolver bloqueios e gerar o relatório final."""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..db import Row, dumps, loads
from ..devices.compatibilidade import capacidades_de, motivo_incompativel, requisitos_de_release
from ..devices.manager import DeviceManager
from ..devices.verbs import verbos_suportados
from ..modules.execution.application.alvos import (AlvoPedido, DicasDoTexto, Mundo, PedidoDeAlvos, RecusaDeAlvo,
                                                   Resolucao, Vinculo, resolver_alvos)
from ..modules.execution.application.resources import ResourceConvergence
from ..modules.execution.application.target_extractor import (CatalogoDeDestinos, DestinosNoTexto, PersonaNomeavel,
                                                              TargetExtractor)
from ..modules.execution.domain.plan_report import spec_from_decl
from ..modules.execution.infrastructure.providers import resource_providers
from ..modules.identity.application.available_data import common_data, missing_secrets, profile_variables
from ..modules.identity.infrastructure.profile_data import SqlProfileDataStore
from ..modules.skills.infrastructure.run_planning import RunPlan, SkillRunPlanner
from ..models import (RUN_TERMINAL, DistributeSpec, DistributionPick, DistributionPreview, InstanceState,
                      ObjectiveDTO, ObjectiveStatus, Plan, ResolveBody, ResolvedTargetDTO, RunCreate, RunStatus,
                      RunSummary, RunTarget, RunTargetsPreview, RunTargetsResolveBody, SessionStatus, StepResult,
                      StepStatus)
from ..planning.capabilities import load_catalog
from ..planning.catalog import capabilities_of, session_provider_of
from ..planning.provider import AIError, AIProvider, AppContext, PlanRequest
from ..security.redaction import redact
from ..shared.resources import Target
from ..util import now_iso
from .balanceamento import distribuir
from .projecao import HistoricoDeAcoes, projetar, resumo
from .repository import Repository
from .scheduler import WAKEABLE, Scheduler

log = logging.getLogger("poc.runs")

#: Estados de onde, com o rodízio LIGADO, o aparelho volta ao ar sozinho: os que ele acorda (`WAKEABLE`) mais o
#: desligamento em voo, que termina num deles. Com o rodízio desligado, nenhum destes volta sem uma pessoa.
_VOLTAM_COM_RODIZIO = WAKEABLE | {InstanceState.stopping}


#: Pedido de navegador no comando: o alvo não é o app da conta do aparelho.
_PEDE_NAVEGADOR = re.compile(r"\b(chrome|navegador|browser)\b", re.IGNORECASE)
#: Pedido de ABRIR um endereço: verbo de navegação e, logo adiante, URL, site ou portal. Uma URL solta não basta:
#: "envie o link https://… para @fulano" continua sendo tarefa do Instagram. "Página" fica de fora: no Instagram ela
#: é perfil ("curta o post da página X").
_ABRIR_ENDERECO = re.compile(r"\b(abr\w*|acess\w*|entr\w*|naveg\w*|visit\w*|v[aá])\b[^.\n]{0,40}?"
                             r"(https?://|\bsite\b|\bportal\b)", re.IGNORECASE)
#: Texto citado é CONTEÚDO (a mensagem a enviar, o comentário a escrever), não o pedido.
_CITACAO = re.compile(r"\"[^\"]*\"|“[^”]*”|'[^']*'")


def pede_outro_alvo(command: str, apps: list[AppContext], pacotes_dos_aparelhos: set[str]) -> bool:
    """O comando pede um site/navegador ou nomeia um app registrado que não é o da conta dos aparelhos?"""
    pedido = _CITACAO.sub(" ", command)
    if _PEDE_NAVEGADOR.search(pedido) or _ABRIR_ENDERECO.search(pedido):
        return True
    texto = pedido.casefold()
    return any(a.package not in pacotes_dos_aparelhos and a.name
               and re.search(rf"\b{re.escape(a.name.casefold())}\b", texto) for a in apps)


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
                 profiles: Any = None, secrets: Any = None, *, skills: SkillRunPlanner):
        self.flows = scheduler.flows
        #: RESOLVE + COMPILE (fase G): o registro de habilidades (skill publicada → fluxo ativo → nada) e o compilador.
        #: É a MESMA porta que `apps_exigidos` e `GET /api/flows/match` usam (decisão P2).
        self.skills = skills
        #: Cofre (`SecretStore`). A execução não guarda mais credencial (ADR-040: a senha é da conta da persona);
        #: fica injetado para quem ainda pergunta se ele está pronto.
        self.secrets = secrets
        self.repo = repo
        #: Os dados da persona de cada aparelho (nomes, nunca valor de segredo): a lista que o planejador recebe, as
        #: variáveis da materialização e o pré-voo de `requires.secrets` (ADR-040).
        self.dados = SqlProfileDataStore(repo.db, tem_provedor_de_sessao=lambda pacote: session_provider_of(pacote)
                                         is not None)
        self.scheduler = scheduler
        self._historico: HistoricoDeAcoes | None = None
        self.devices = devices
        self.provider = provider
        # Serviço social (opcional): resolve perfil ↔ aparelho. Sem ele, só execução por aparelho.
        self.profiles = profiles
        self._planning: dict[str, asyncio.Task[None]] = {}

    # ------------------------------------------------------------------ criar + planejar
    def create(self, req: RunCreate) -> RunSummary:
        # ADR-025 (22d65f): senha escrita NO comando ia em claro para `runs.command`, para a API de execuções, para o
        # prompt do planejador e para o histórico do navegador. Recusa antes de qualquer gravação, apontando o lugar
        # certo (ADR-040: a conta da persona). A detecção é por formato (`senha: …`, `password=…`), a mesma da
        # redação dos eventos.
        if redact(req.command) != req.command:
            raise RunError("credencial_no_comando",
                           "O comando contém uma credencial (ex.: \"Senha: …\"). O texto do comando vai ao provedor de "
                           "IA e fica no histórico: tire a senha dele. A senha fica guardada na conta da persona (aba "
                           "Contas do perfil), com o seu consentimento, e a automação a digita de lá sem passar pela IA.",
                           409)
        pedido = {"instance_ids": list(req.instance_ids), "profile_ids": list(req.profile_ids),
                  "targets": [t.model_dump() for t in req.targets]}
        if req.distribute is not None:
            req = req.model_copy(update={"instance_ids": self._distribuir(req.distribute, req.only_ready)})
        # PARA QUEM e ONDE (onda C): a mesma resolução da prévia. A distribuição já escolheu os aparelhos, então o
        # texto não é lido como destino ali (e o comando segue inteiro).
        resolucao, comando = self._resolver(req.command, req.instance_ids, req.profile_ids, req.targets,
                                            req.device_policy, com_texto=req.distribute is None)
        # Destino deduzido do texto NUNCA executa sem ter sido mostrado (§7.6, risco R13): quem chama ecoa em
        # `targets` o que a prévia devolveu, e aí a origem passa a ser a interface.
        nao_confirmados = [a for a in resolucao.alvos if a.origem == "texto"]
        if nao_confirmados:
            raise RunError("alvos_nao_confirmados",
                           "O comando cita destinos que ainda não foram confirmados: "
                           + ", ".join(f"{a.instance_id}" + (f" ({a.profile_id})" if a.profile_id else "")
                                       for a in nao_confirmados)
                           + ". Confira a prévia (POST /api/runs/targets/resolve) e envie os alvos em `targets`.",
                           409, {"targets": [a.as_dict() for a in nao_confirmados], "command_sem_destinos": comando})
        req = req.model_copy(update={"instance_ids": resolucao.instance_ids})
        if resolucao.perguntas:
            return self._criar_com_perguntas(req, resolucao, comando, pedido)
        perfis = {a.instance_id: a.profile_id for a in resolucao.alvos}
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
        self._exigir_apps_do_fluxo(req, comando)
        # PRÉ-VOO, antes de chamar o planejador: a recusa explicada já existia para o comando do painel e não
        # existia para a execução — a tarefa era aceita, planejada (gastando chamada ao planejador) e só então
        # bloqueava no aparelho. Aqui ela para antes, com o motivo e o que fazer, por aparelho.
        req = self._exigir_pre_voo(req, comando, perfis)
        status = self.provider.status()
        if not status.configured:
            raise RunError("ai_not_configured", status.notice, 503)
        # ADR-040: nada de cofre aqui. A credencial é da conta da persona de cada aparelho; o que a execução carrega
        # são NOMES, montados no planejamento, e o consentimento já foi dado na conta.
        row, created = self.repo.create_run(req, simulated=self.provider.simulated,
                                            targets=self._foto(req, resolucao, comando, pedido))
        if created:
            self._spawn_planning(row["id"])
        return self.repo.run_summary(self.repo.run_row(row["id"]), deduplicated=not created)

    # ------------------------------------------------------------------ alvos: persona × aparelho (onda C)
    def previa_de_alvos(self, body: RunTargetsResolveBody) -> RunTargetsPreview:
        """`POST /runs/targets/resolve`: os alvos que `create` usaria, com a origem de cada um, as perguntas e o
        comando sem destinos — sem gravar nada e sem chamar o planejador. É o que o painel mostra antes de
        Executar, e o que se ecoa em `targets` para confirmar destino tirado do texto."""
        resolucao, comando = self._resolver(body.command, body.instance_ids, body.profile_ids, body.targets,
                                            body.device_policy)
        avisos: list[str] = []
        por_persona: dict[str, int] = {}
        for a in resolucao.alvos:
            if a.profile_id:
                por_persona[a.profile_id] = por_persona.get(a.profile_id, 0) + 1
        repetidas = sorted(p for p, n in por_persona.items() if n > 1)
        if repetidas:
            avisos.append(f"A mesma persona age em mais de um aparelho ({', '.join(repetidas)}): o que a tarefa faz "
                          "(curtir, comentar, mandar mensagem) acontece uma vez em CADA um.")
        if any(a.origem == "texto" for a in resolucao.alvos):
            avisos.append("Há destino tirado do texto do comando: confira antes de executar.")
        return RunTargetsPreview(
            targets=[ResolvedTargetDTO(instance_id=a.instance_id, profile_id=a.profile_id, app_id=a.app_id,
                                       origem=a.origem) for a in resolucao.alvos],
            questions=[p.as_dict() for p in resolucao.perguntas], command_sem_destinos=comando, warnings=avisos)

    def _resolver(self, command: str, instance_ids: Sequence[str], profile_ids: Sequence[str],
                  targets: Sequence[RunTarget], politica: str, *, com_texto: bool = True) -> tuple[Resolucao, str]:
        """O texto (`TargetExtractor`) e a seleção passam pelo MESMO `resolver_alvos` na prévia, na criação e no
        planejamento de execução antiga. Recusa vira `RunError` com o código do resolvedor."""
        if (profile_ids or targets) and self.profiles is None:
            raise RunError("profiles_unavailable", "Execução por perfil indisponível nesta instalação.", 400)
        destinos = (TargetExtractor(self._catalogo()).extrair(command) if com_texto
                    else DestinosNoTexto(DicasDoTexto(), command))
        app = self._app_do_comando(destinos.command_sem_destinos, targets)
        pedido = PedidoDeAlvos(
            tuple(instance_ids), tuple(profile_ids),
            tuple(AlvoPedido(t.profile_id, tuple(t.instance_ids), t.app_id) for t in targets),
            "all" if politica == "all" else "primary" if politica == "primary" else "one", app)
        try:
            return resolver_alvos(pedido, destinos.dicas, self._mundo(app)), destinos.command_sem_destinos
        except RecusaDeAlvo as exc:
            raise RunError(exc.code, exc.message, exc.status) from exc

    def sem_destinos(self, command: str) -> str:
        """O comando como a RESOLVE o vê na execução: sem os trechos de destino ("com a persona André"). As prévias
        de casamento (`/flows/match`, `/skills/resolve`) passam por aqui para não casarem diferente da execução."""
        return TargetExtractor(self._catalogo()).extrair(command).command_sem_destinos

    def _app_do_comando(self, comando: str, targets: Sequence[RunTarget]) -> str | None:
        """O app que a tarefa usa: o dos alvos explícitos, quando é um só; senão o que a habilidade casada exige."""
        dos_alvos = {t.app_id for t in targets if t.app_id}
        if len(dos_alvos) == 1:
            return dos_alvos.pop()
        exigidos = self.apps_exigidos(comando)
        return str(exigidos[0]["id"]) if len(exigidos) == 1 else None

    def _catalogo(self) -> CatalogoDeDestinos:
        """Como cada persona pode ser citada (nomes e @ de todas as contas) e os ids dos aparelhos."""
        db = self.repo.db
        handles: dict[str, list[str]] = {}
        for r in db.query("SELECT profile_id, handle FROM profile_accounts WHERE handle IS NOT NULL AND handle <> ''"):
            handles.setdefault(str(r["profile_id"]), []).append(str(r["handle"]))
        personas: list[PersonaNomeavel] = []
        for r in db.query("SELECT id, username, first_name, last_name, display_name FROM instagram_profiles"):
            completo = " ".join(x for x in (r["first_name"], r["last_name"]) if x)
            nomes = tuple(dict.fromkeys(n for n in (r["first_name"], completo, r["display_name"]) if n))
            arrobas = tuple(dict.fromkeys([*([r["username"]] if r["username"] else []), *handles.get(str(r["id"]), [])]))
            personas.append(PersonaNomeavel(str(r["id"]), nomes, arrobas))
        return CatalogoDeDestinos(tuple(personas), tuple(self.devices.devices))

    def _mundo(self, app_id: str | None) -> Mundo:
        """O parque como `resolver_alvos` o vê: vínculos ativos (com os apps que cada um serve), os aparelhos aptos
        agora (fora da loja, servidor disponível, app pronto quando se sabe o app) e as sessões `session_ready` por
        (persona, aparelho) em `account_sessions`. Só leitura."""
        db = self.repo.db
        contas: dict[str, set[str]] = {}
        for r in db.query("SELECT profile_id, app_id FROM profile_accounts"):
            contas.setdefault(str(r["profile_id"]), set()).add(str(r["app_id"]))
        vinculos = tuple(
            Vinculo(str(v["profile_id"]), str(v["instance_id"]),
                    frozenset({str(v["app_id"])} if v["app_id"] else contas.get(str(v["profile_id"]), set())),
                    bool(v["is_primary"]))
            for v in db.query("SELECT profile_id, instance_id, app_id, is_primary FROM device_profile_bindings"
                              " WHERE active=1 ORDER BY is_primary DESC, id"))
        pacote = db.scalar("SELECT package FROM apps WHERE id=?", (app_id,)) if app_id else None
        # App que se SABE não pronto (linha em `device_app_state` fora de pronto) tira o aparelho dos aptos; o que
        # nunca foi observado não fecha a porta (a mesma regra do pré-voo).
        sem_app = {str(r["instance_id"]) for r in db.query(
            "SELECT instance_id FROM device_app_state WHERE package_name=? AND state NOT IN (?,?)",
            (pacote, *self._APP_PRONTO))} if pacote else set()
        servidores = self.scheduler.servidores()
        aptos = frozenset(
            iid for iid, rt in self.devices.devices.items()
            if not rt.store and iid not in sem_app
            and (srv := servidores.get(self.scheduler.servidor_de(rt))) is not None and srv.disponivel)
        sql = ("SELECT a.profile_id, s.instance_id FROM account_sessions s JOIN profile_accounts a ON a.id = s.account_id"
               " WHERE s.status=?")
        params: tuple[object, ...] = (SessionStatus.session_ready.value,)
        if app_id:
            sql += " AND a.app_id=?"
            params += (app_id,)
        prontas = frozenset((str(r["profile_id"]), str(r["instance_id"])) for r in db.query(sql, params))
        nomes = tuple(
            (str(r["id"]), str(r["display_name"] or " ".join(x for x in (r["first_name"], r["last_name"]) if x)
                               or (f"@{r['username']}" if r["username"] else r["id"])))
            for r in db.query("SELECT id, username, first_name, last_name, display_name FROM instagram_profiles"))
        return Mundo(vinculos, aptos, prontas, self._desempatar, nomes)

    def _desempatar(self, candidatos: Sequence[str]) -> str | None:
        """Entre aparelhos igualmente bons de uma persona: o balanceamento de sempre (carga do servidor, ligado
        antes de desligado), pedindo UM."""
        d = distribuir(1, self.scheduler.candidatos_de(candidatos), self.scheduler.servidores())
        return d.escolhidos[0].instance_id if d.escolhidos else None

    @staticmethod
    def _foto(req: RunCreate, resolucao: Resolucao, comando: str, pedido: Mapping[str, object]) -> str:
        """`runs.targets` (051): os alvos que ficaram (depois do "só os aptos"), a origem de cada um, a política, o
        pedido como veio e o comando sem destinos — o planejamento, inclusive o retomado após reinício, lê daqui."""
        ficam = set(req.instance_ids)
        return dumps({"alvos": [a.as_dict() for a in resolucao.alvos if a.instance_id in ficam],
                      "command_sem_destinos": comando, "device_policy": req.device_policy, "pedido": dict(pedido)})

    def _criar_com_perguntas(self, req: RunCreate, resolucao: Resolucao, comando: str,
                             pedido: Mapping[str, object]) -> RunSummary:
        """Contradição ou ambiguidade de destino: a execução nasce em `needs_input` com as perguntas estruturadas
        (o mesmo formato das da RESOLVE), sem plano — nem parcial — e sem chamar o planejador."""
        row, created = self.repo.create_run(req, simulated=self.provider.simulated,
                                            targets=self._foto(req, resolucao, comando, pedido))
        if created:
            self._pedir_resposta(row["id"], [p.as_dict() for p in resolucao.perguntas])
        return self.repo.run_summary(self.repo.run_row(row["id"]), deduplicated=not created)

    def _pedir_resposta(self, run_id: str, perguntas: list[dict[str, object]]) -> None:
        texto = " | ".join(str(p["question"]) for p in perguntas)
        self.repo.bus.emit("log", f"Execução {run_id}: os destinos precisam de resposta antes de planejar",
                           level="warn", run_id=run_id, data={"questions": perguntas})
        self.repo.set_run_status(run_id, RunStatus.needs_input, texto, level="warn",
                                 message=f"Execução {run_id}: faltam informações — {texto}")

    def _perfis_da_execucao(self, run: Mapping[str, object]
                            ) -> tuple[dict[str, str | None], str, list[dict[str, object]]]:
        """`{aparelho: persona}`, o comando sem destinos e as perguntas pendentes de uma execução. Da foto
        (`runs.targets`) quando existe; execução anterior à 051 re-resolve pelo aparelho, no MESMO resolvedor."""
        foto = loads(str(run["targets"]), None) if run["targets"] else None
        if isinstance(foto, dict) and isinstance(foto.get("alvos"), list):
            perfis = {str(a["instance_id"]): (str(a["profile_id"]) if a.get("profile_id") else None)
                      for a in foto["alvos"]}
            return perfis, str(foto.get("command_sem_destinos") or run["command"]), []
        ids = [str(i) for i in loads(str(run["instance_ids"]), [])]
        if not ids:
            return {}, str(run["command"]), []
        resolucao = resolver_alvos(PedidoDeAlvos(instance_ids=tuple(ids)), DicasDoTexto(), self._mundo(None))
        return ({a.instance_id: a.profile_id for a in resolucao.alvos}, str(run["command"]),
                [p.as_dict() for p in resolucao.perguntas])

    # ------------------------------------------------------------------ credenciais das contas (ADR-040)
    def _segredos_exigidos(self, command: str) -> tuple[str, ...]:
        """Os `requires.secrets` da skill que este comando casa (nomes lógicos, ex.: `conta_chrome_senha`). Vazio
        quando nada casa ou o fluxo é legado. É o que o pré-voo confere contra as contas da persona de cada
        aparelho, ANTES de planejar — a mesma porta de `apps_exigidos`."""
        casado = self.skills.for_command(command, None)
        return tuple(casado.secrets) if casado is not None else ()

    # ------------------------------------------------------------------ distribuir entre servidores
    def previa_de_distribuicao(self, spec: DistributeSpec) -> DistributionPreview:
        """Quem seria escolhido AGORA, sem criar nada — é o que o painel mostra antes de Executar."""
        servidores = self.scheduler.servidores()
        candidatos = self.scheduler.candidatos_do_app(spec.app_id)
        d = distribuir(spec.count, candidatos, servidores)
        motivos = list(d.faltas)
        if d.faltaram and self.scheduler.app_exige_conta(spec.app_id):
            # O filtro que mais corta num app com conta é o do perfil — dito primeiro, com o número.
            motivos = [m for m in motivos if not m.startswith("o parque só tem")]
            motivos.insert(0, f"este app exige conta: só {len(candidatos)} aparelho(s) têm perfil ativo vinculado")
        teto = int(self.scheduler.get_settings().max_active_devices)
        livres_no_geral = teto - len(self.scheduler.workers)
        if len(d.escolhidos) > livres_no_geral:
            motivos.append(f"o teto geral do parque ({teto} aparelhos trabalhando) segura "
                           f"{len(d.escolhidos) - max(0, livres_no_geral)} deles na fila até liberar vaga")
        return DistributionPreview(
            requested=spec.count,
            picks=[DistributionPick(instance_id=e.instance_id, server_id=e.servidor,
                                    server_name=servidores[e.servidor].nome, needs_start=e.precisa_ligar)
                   for e in d.escolhidos],
            per_server={servidores[k].nome: v for k, v in d.por_servidor().items()},
            missing=d.faltaram, reasons=motivos)

    def _distribuir(self, spec: DistributeSpec, parcial_ok: bool) -> list[str]:
        """Os aparelhos da execução distribuída. Faltando aparelho, recusa com o motivo — a menos que a pessoa
        tenha pedido "seguir só com os aptos" (`only_ready`), que aqui quer dizer "com os que houver"."""
        previa = self.previa_de_distribuicao(spec)
        ids = [p.instance_id for p in previa.picks]
        detalhes = {"distribution": previa.model_dump()}
        if not ids:
            raise RunError("distribution_empty", "Nenhum aparelho disponível para distribuir este comando: "
                           + ("; ".join(previa.reasons) or "sem aparelho livre deste app") + ".", 409, detalhes)
        if previa.missing and not parcial_ok:
            raise RunError("distribution_short",
                           f"Só {len(ids)} de {spec.count} aparelhos estão disponíveis agora"
                           + (": " + "; ".join(previa.reasons) if previa.reasons else "") + ".", 409, detalhes)
        return ids

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
        """Os apps que a skill ou o fluxo casado por este comando exige. Vazio quando nada casa.

        A pergunta é feita à MESMA porta que o planejamento usa (`self.skills`: skill publicada → fluxo ativo, cada um
        atrás do seu interruptor): se o comando casa, o plano (e com ele a lista de apps exigidos) já existe antes de
        agendar — que é exatamente quando dá para explicar a pendência. Skill que não compila, ou comando que a
        RESOLVE devolve como pergunta (parâmetro vazio ou inválido, empate), não exige nada aqui: a execução para em
        `needs_input` com o motivo, que é mais útil que uma recusa por app.
        """
        casado = self.skills.for_command(command, None)
        if casado is None or casado.plan is None:
            return []
        plan = casado.plan
        ids = plan.required_apps or ([plan.app_id] if plan.app_id else [])
        if not ids:
            return []
        marcas = ",".join("?" for _ in ids)
        return self.repo.db.query(f"SELECT id, name, package FROM apps WHERE id IN ({marcas}) ORDER BY name",
                                  tuple(ids))

    def _exigir_apps_do_fluxo(self, req: RunCreate, comando: str | None = None) -> None:
        """Recusa, ANTES de agendar, a execução cujo fluxo exige um app que ainda não está no aparelho.

        Sem isto, a pendência só aparecia depois — etapa que falha ou item bloqueado no meio da execução, sem
        ação clara. Aqui a recusa traz o que fazer, por aparelho: "distribua X em android-12".

        Só o que se SABE fecha a porta: aparelho cujo pacote nunca foi observado (sem linha em
        `device_app_state`) não é recusa — é a mesma regra das capacidades declaradas, e o app pode ser entregue
        pela porta do despacho antes da tarefa.
        """
        exigidos = self.apps_exigidos(req.command if comando is None else comando)
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
    def pre_voo(self, instance_ids: list[str], *, ao_iniciar: bool = False, secret_names: Sequence[str] = (),
                perfis: Mapping[str, str | None] | None = None) -> dict[str, dict[str, str]]:
        """Por que a tarefa NÃO pode acontecer em cada um destes aparelhos, conferido antes de agendar.

        Uma pergunta, uma resposta, com três usos: a recusa de `create` (antes de gastar o planejador), o motivo
        específico que `start` grava no item, e a lista que o painel mostra para quem escolheu os aparelhos.

        `ao_iniciar` acrescenta o que ESPERAR resolve e criar não: um aparelho DESTA máquina que está parado com o
        rodízio desligado não é motivo para recusar a criação — quem pediu a tarefa pode ir ligá-lo, e a execução
        continua valendo. No início, sim: ali o item para com o motivo, como sempre parou.

        Só entra aqui o que se SABE. Aparelho cujo app nunca foi observado, worker em manutenção, estado
        desconhecido: nada disso vira recusa — o que não se sabe nunca fecha a porta (é a mesma regra das
        capacidades declaradas). Devolve `{aparelho: {code, motivo, acao}}`; ausente = apto.

        `secret_names`: os `requires.secrets` da skill casada (ADR-040). A persona do aparelho precisa ter cada um
        como senha UTILIZÁVEL — guardada, consentida, não recusada, de app sem login gerenciado; o que falta em um
        aparelho recusa aquele aparelho, com o que fazer.
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
            if secret_names:
                # A persona DO ALVO (onda C): com duas no aparelho, "a do aparelho" não existe.
                pid = perfis.get(iid) if perfis is not None else self._perfil_unico(iid)
                faltam = missing_secrets(self.dados, pid, secret_names)
                if faltam:
                    impedidos[iid] = {
                        "code": "missing_credential",
                        "motivo": (f"a habilidade exige a(s) credencial(is) {', '.join(faltam)} e "
                                   + ("o aparelho não tem perfil vinculado." if pid is None else
                                      "a persona deste aparelho não a(s) tem guardada com consentimento.")),
                        "acao": ("Vincule um perfil ao aparelho e repita." if pid is None else
                                 "Guarde a senha na conta da persona (guia Contas e acesso da persona), marcando o consentimento, "
                                 "e repita.")}
                    continue
            if self.scheduler.app_preflight is not None:
                if (recusa := self.scheduler.app_preflight(rt)) is not None:
                    impedidos[iid] = recusa
        return impedidos

    def _exigir_pre_voo(self, req: RunCreate, comando: str, perfis: Mapping[str, str | None]) -> RunCreate:
        """Aplica o pré-voo: recusa com a lista por aparelho, ou segue só com os aptos quando foi isso que se pediu."""
        impedidos = self.pre_voo(req.instance_ids, secret_names=self._segredos_exigidos(comando), perfis=perfis)
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

    def _perfil_unico(self, instance_id: str) -> str | None:
        """A persona do aparelho quando há UMA (quem pergunta sem alvo resolvido: a lista de pré-voo do painel)."""
        ids = {str(r["profile_id"]) for r in self.repo.db.query(
            "SELECT profile_id FROM device_profile_bindings WHERE instance_id=? AND active=1", (instance_id,))}
        return ids.pop() if len(ids) == 1 else None

    def _spawn_planning(self, run_id: str) -> None:
        if run_id in self._planning and not self._planning[run_id].done():
            return
        # Quem está planejando (migração 027). É o que impede o outro backend de disparar um SEGUNDO
        # planejamento pago da mesma execução ao subir com ela ainda em `planning`.
        self.repo.db.execute("UPDATE runs SET planned_by=? WHERE id=?", (self.repo.owner_id, run_id))
        self._planning[run_id] = asyncio.create_task(self._plan(run_id), name=f"plan-{run_id}")

    def resume_planning_after_restart(self) -> None:
        """Retoma o planejamento que EU deixei pela metade — nunca o que outro backend está planejando agora.

        Sem `planned_by` (migração 027) isto disparava um segundo planejamento PAGO da mesma execução assim que um
        segundo backend subisse (item 5.1, achado #26). Execução antiga, sem dono registrado, continua sendo
        retomada por quem subir: é o comportamento de antes, e com um backend só nada muda.
        """
        meu = self.repo.owner_id
        for r in self.repo.db.query("SELECT id FROM runs WHERE status='planning'"
                                    " AND (planned_by IS NULL OR planned_by=?)", (meu,)):
            self.repo.bus.emit("log", f"Execução {r['id']}: planejamento interrompido pelo reinício; replanejando.",
                               level="warn", run_id=r["id"])
            self._spawn_planning(r["id"])

    async def _plan(self, run_id: str) -> None:
        repo = self.repo
        run = repo.run_row(run_id)
        assert run is not None
        ids: list[str] = loads(run["instance_ids"], [])
        # A persona de cada aparelho vem da foto dos alvos (onda C), não de "quem está vinculado ao aparelho": com
        # duas personas num aparelho, só a resolução sabe por qual a execução foi pedida.
        perfis, comando, perguntas = self._perfis_da_execucao(run)
        if perguntas:
            self._pedir_resposta(run_id, perguntas)
            return
        instances = []
        for iid in ids:
            r = repo.db.one("SELECT app_id, account_label FROM instances WHERE id=?", (iid,))
            rt = self.devices.devices.get(iid)
            pid = perfis.get(iid)
            instances.append({"instance_id": iid, "account_label": r["account_label"] if r else None,
                              "app_id": r["app_id"] if r else None,
                              # perfil FOTOGRAFADO agora: se o vínculo mudar no meio, o histórico não muda de dono
                              "profile_id": pid,
                              # os dados NÃO sigilosos da persona deste aparelho, fotografados com o perfil: são as
                              # variáveis `{perfil_email}`… que a materialização resolve (ADR-040)
                              "variables": profile_variables(self.dados, pid),
                              # ONDE isto vai rodar, fotografado pelo mesmo motivo: o id lógico é um apelido que
                              # muda de aparelho por configuração, e sem isto o relatório de amanhã fala de um
                              # "android-09" que ninguém consegue reencontrar. Re-fotografado no despacho.
                              **self.scheduler.onde_roda(rt)})
        apps = [AppContext(a["id"], a["name"], a["package"], a["activity"], a["nav_hints"], loads(a["known_selectors"]))
                for a in repo.db.query("SELECT * FROM apps ORDER BY name")]
        try:
            # RESOLVE + COMPILE (design §14.1): skill publicada → fluxo ativo → nada, cada backend atrás do seu
            # interruptor (`skills.enabled`, `ai.flows`). Casou: o plano já existe e o planejador não é chamado.
            known = self.skills.for_command(comando, [i.get("profile_id") for i in instances])
            if known is not None and known.plan is None:
                self._skill_sem_plano(run_id, known)
                return
            if known is not None and known.plan is not None:
                plan = known.plan
                self._registrar_resolucao(run_id, known)
            else:
                # App alvo conhecido e com catálogo: o planejador escolhe ações nomeadas em vez de escrever
                # etapas livres. Aparelhos com apps diferentes (ou sem app definido) seguem no caminho livre.
                pacotes = {a.package for a in apps if a.id in {i.get("app_id") for i in instances}}
                # O app da CONTA do aparelho não é o alvo quando o comando pede outro app ou um site (22d65f: "abra o
                # Chrome e entre no site…" num aparelho da conta Instagram recebeu só as ações do Instagram e
                # voltou sem etapas). Nesse caso o plano é livre.
                catalog = (load_catalog(pacotes.pop())
                           if len(pacotes) == 1 and not pede_outro_alvo(comando, apps, pacotes) else None)
                # O planejamento passa pelo MESMO laço das demais chamadas de IA (achado #96, item 4): antes ele
                # chamava `provider.plan` direto — entrava no limite de concorrência e em nada mais, ficando fora
                # da repetição com espera, do disjuntor de conta e de qualquer conferência de orçamento.
                # `objective_id=None`: é uso da execução, e ainda não há objetivo nenhum para contar chamada.
                plan = await self.scheduler.executor._ai(          # noqa: SLF001 - ponto único de chamada de IA
                    run_id, None,
                    # A lista de dados da persona COMUM a todos os aparelhos (ADR-040): nomes, nunca valores.
                    lambda: self.provider.plan(PlanRequest(
                        command=comando, run_id=run_id, instances=instances, apps=apps, catalog=catalog,
                        available_data=list(common_data(self.dados, [i["profile_id"] for i in instances])))),
                    role="plan")
        except AIError as exc:
            if exc.kind == "refusal":
                # Recusa do provedor não é falha da execução: repetir o MESMO comando tende a dar a mesma
                # recusa. `needs_input` pede que a pessoa reescreva o comando, em vez de derrubar a execução
                # inteira como se fosse um defeito nosso (achado #93, ponto 3).
                repo.set_run_status(run_id, RunStatus.needs_input,
                                    f"O provedor de IA recusou este planejamento: {exc}", level="warn",
                                    message=f"Execução {run_id}: planejamento recusado pelo provedor — reescreva "
                                            "o comando e tente de novo.")
                return
            repo.set_run_status(run_id, RunStatus.failed, f"Planejamento falhou: {exc}", level="error")
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("planejamento %s", run_id)
            repo.set_run_status(run_id, RunStatus.failed, f"Erro interno no planejamento: {exc}", level="error")
            return
        repo.save_plan(run_id, plan)
        repo.decision(f"Plano ({'SIMULADO' if plan.planner.simulated else plan.planner.model}): {plan.summary} — "
                      f"{len(plan.steps)} etapa(s): " + " → ".join(s.title for s in plan.steps), run_id=run_id)
        self._anunciar_projecao(run_id, plan)
        if plan.missing or not plan.steps:
            questions = " | ".join(m.question for m in plan.missing) or "O plano veio sem etapas."
            repo.set_run_status(run_id, RunStatus.needs_input, questions, level="warn",
                                message=f"Execução {run_id}: faltam informações — {questions}")
            return
        repo.materialize(run_id, plan, instances)      # persistido ANTES de executar
        self._fotografar_recursos(run_id, known, instances)
        run = repo.run_row(run_id)
        if run and run["cancel_requested"]:
            repo.set_run_status(run_id, RunStatus.cancelled, "Cancelada durante o planejamento")
            return
        if run and run["mode"] == "execute":
            self.start(run_id)
        else:
            repo.set_run_status(run_id, RunStatus.planned, "Plano pronto para inspeção",
                                message=f"Execução {run_id}: plano pronto; aguardando início")

    def projecao(self, plan: Plan) -> dict[str, object]:
        """Item 18.3: o normal medido de cada etapa do plano (chamadas de IA, tempo, US$ — mediana e p90 por ação),
        somado. É o que a pessoa vê ANTES de iniciar; etapa sem base própria vem marcada, nunca inventada."""
        if self._historico is None:
            cfg = self.scheduler.cfg
            self._historico = HistoricoDeAcoes(
                self.repo.db, lambda: cfg.file.ai.prices, janela_dias=cfg.file.ai.step_budget.window_days,
                retencao_dias=lambda: int(self.scheduler.get_settings().log_retention_days))
        passos = [(p.key, p.title, p.app_id or plan.app_id or "*", p.capability or "*") for p in plan.steps]
        return projetar(passos, self._historico, minimo=self.scheduler.cfg.file.ai.step_budget.min_samples)

    def _anunciar_projecao(self, run_id: str, plan: Plan) -> None:
        if not plan.steps:
            return
        try:
            self.repo.decision(f"Projeção pelo histórico (normal medido por ação): {resumo(self.projecao(plan))}",
                               run_id=run_id)
        except Exception:  # noqa: BLE001 - a projeção informa; nunca derruba o planejamento
            log.exception("projeção da execução %s", run_id)

    def _registrar_resolucao(self, run_id: str, resolvida: RunPlan) -> None:
        """Trilha da 045 e, para o plano de um fluxo — o legado, ou a versão que o adotou (fase J) —, o que se
        gravava antes (`flow_id`, `flows.used`)."""
        repo = self.repo
        repo.note_run_skill(run_id, skill_id=resolvida.skill_id, skill_version=resolvida.skill_version,
                            skill_hash=resolvida.skill_hash)
        if resolvida.flow_id is not None:
            repo.db.execute("UPDATE runs SET flow_id=? WHERE id=?", (resolvida.flow_id, run_id))
            self.flows.used(resolvida.flow_id)
        if resolvida.legacy_flow_id is not None:
            repo.decision(f"Plano reaproveitado do fluxo “{resolvida.name}” (sem chamada ao planejador)", run_id=run_id)
            return
        avisos = "".join(f" [{i.code.value}]" for i in resolvida.issues)
        repo.decision(f"Plano da habilidade {resolvida.ref} “{resolvida.name}” (sem chamada ao planejador)"
                      + avisos, run_id=run_id)

    def _skill_sem_plano(self, run_id: str, resolvida: RunPlan) -> None:
        """A skill casou e não virou plano para ESTE comando: a RESOLVE precisa de resposta (parâmetro vazio ou que
        não serve para o tipo, ou duas habilidades empatadas — fase I), ou a compilação falhou (filha desabilitada,
        capability que saiu do catálogo). Nunca plano parcial, e nunca o planejador por fora: a pessoa vê a pergunta
        ou os erros e decide."""
        repo = self.repo
        if resolvida.resolved is not None:        # no empate não há UMA habilidade para a trilha
            repo.note_run_skill(run_id, skill_id=resolvida.skill_id, skill_version=resolvida.skill_version,
                                skill_hash=resolvida.skill_hash)
        if resolvida.questions:
            perguntas = [q.as_dict() for q in resolvida.questions]
            texto = " | ".join(q.question for q in resolvida.questions)
            repo.bus.emit("log", f"Execução {run_id}: o comando casou com "
                                 f"{resolvida.ref or 'mais de uma habilidade'} e precisa de resposta antes de planejar",
                          level="warn", run_id=run_id,
                          data={"skill": str(resolvida.ref) if resolvida.ref else None, "questions": perguntas,
                                "candidates": [str(c.ref) for c in resolvida.resolution.candidates]})
            repo.set_run_status(run_id, RunStatus.needs_input, texto, level="warn",
                                message=f"Execução {run_id}: faltam informações — {texto}")
            return
        problemas = [i.as_dict() for i in resolvida.issues]
        texto = "; ".join(f"{p['code']}: {p['message']}" for p in problemas) or "sem detalhe"
        repo.bus.emit("log", f"Execução {run_id}: a habilidade {resolvida.ref} casou com o comando e não compilou",
                      level="warn", run_id=run_id, data={"skill": str(resolvida.ref), "issues": problemas})
        repo.set_run_status(run_id, RunStatus.needs_input,
                            f"A habilidade {resolvida.ref} não compilou para este comando: {texto}", level="warn",
                            message=f"Execução {run_id}: a habilidade {resolvida.ref} não compilou — corrija o comando "
                                    "ou a habilidade e tente de novo.")

    # ------------------------------------------------------------------ recursos declarativos (fase H)
    def _recursos(self) -> ResourceConvergence:
        """Os quatro providers só de LEITURA (sem `CommandBus`): o relatório e a foto não pedem comando nenhum."""
        return ResourceConvergence(resource_providers(
            self.repo.db, self.devices.devices,
            session_max_age_s=int(self.scheduler.cfg.file.contas.session_max_age_s),
            unknown_retry_cap=int(self.scheduler.get_settings().session_unknown_retry_cap)))

    def _alvos(self, run: Mapping[str, object]) -> tuple[list[Target], str]:
        """Onde os recursos se resolvem — o aparelho e a persona DO ALVO, como `_plan` fotografa — e o comando sem
        destinos, que é o que casa com a habilidade."""
        perfis, comando, _perguntas = self._perfis_da_execucao(run)
        return [Target(str(iid), perfis.get(str(iid))) for iid in loads(str(run["instance_ids"]), [])], comando

    def relatorio_de_recursos(self, run_id: str) -> dict[str, object]:
        """O `PlanReport` dos recursos (design §14.2) de uma execução: o que está certo, o que diverge, o que seria
        feito, os riscos e o que só uma pessoa resolve — SEM aplicar nada.

        É o que `POST /api/runs` com `mode=plan` devolve junto do resumo. A execução ainda está planejando quando a
        resposta sai (`_plan` roda em segundo plano), então a habilidade é resolvida de novo aqui: a RESOLVE e a
        COMPILE são puras (leitura do registro, compilação sem IA), e a leitura dos recursos é `SELECT` e memória.

        `source` diz de onde vieram os recursos: `skill` (declarados em `spec.resources`), `legacy_flow` (fluxo
        legado, que não declara recurso), `needs_input` (a habilidade casou e precisa de resposta, ou não compilou) e
        `planner` (nada casou: o planejador escreve as etapas, e nenhum recurso é declarado). Nos três últimos, a
        lista de recursos vem vazia — e `ready_to_run` só fala dos recursos declarados.
        """
        run = self._run(run_id)
        alvos, comando = self._alvos(run)
        resolvida = self.skills.for_command(comando, [a.profile_id for a in alvos])
        if resolvida is None:
            origem = "planner"
        elif resolvida.plan is None:
            origem = "needs_input"
        else:
            origem = "legacy_flow" if resolvida.legacy_flow_id is not None else "skill"
        specs = [spec_from_decl(d) for d in resolvida.resources] if resolvida is not None and resolvida.plan else []
        relatorio = self._recursos().report(specs, alvos, skill=resolvida.ref if resolvida else None,
                                            skill_hash=resolvida.skill_hash if resolvida else None)
        return {"source": origem, **relatorio.canonical(), "content_hash": relatorio.content_hash()}

    def _fotografar_recursos(self, run_id: str, resolvida: RunPlan | None,
                             instances: Sequence[Mapping[str, object]]) -> None:
        """`objectives.resource_plan` (045): o spec que a skill declarou, resolvido para CADA aparelho, gravado no
        `materialize` — pelo mesmo motivo de `worker_id`/`physical_id` (022): se a skill ou o vínculo mudarem depois, o
        histórico continua dizendo o que esta execução pediu, e de quem. Sem recurso declarado, nada é gravado."""
        if resolvida is None or not resolvida.resources:
            return
        ref = resolvida.ref
        skill = {"id": ref.skill_id if ref else None, "version": ref.version if ref else None,
                 "content_hash": resolvida.skill_hash}
        declarados = [d.canonical() for d in resolvida.resources]
        quando = now_iso()
        with self.repo.db.tx():
            for inst in instances:
                foto = {"skill": skill, "instance_id": inst["instance_id"], "profile_id": inst.get("profile_id"),
                        "resources": declarados, "resolved_at": quando}
                self.repo.db.execute("UPDATE objectives SET resource_plan=? WHERE run_id=? AND instance_id=?",
                                     (dumps(foto), run_id, inst["instance_id"]))

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
        _perfis, comando, _perguntas = self._perfis_da_execucao(run)
        impedidos = self.pre_voo([o["instance_id"] for o in alvos], ao_iniciar=True,
                                 secret_names=self._segredos_exigidos(comando),
                                 perfis={o["instance_id"]: o["profile_id"] for o in alvos})
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
        versao = self.repo.revise_plan(obj["id"], reason, steps)
        # O mesmo que a recuperação automática faz depois de revisar: o texto escrito (e talvez aprovado) mora na
        # LINHA da etapa, não em `runs.plan`. Sem herdá-lo, a etapa renascia sem `content`, sem a guarda e sem a
        # marca de rascunho — a porta escrevia outro texto, pago, e a aprovação já dada não valia para ele.
        self.scheduler.herdar_textos(obj["id"], versao)
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

    def _print_da_confirmacao(self, obj: Row, etapa: Row, evidence_id: int | None) -> Row | None:
        """O print em que a pessoa se baseou para o "confirmar concluído"; `None` quando nenhum foi citado.

        ADR-055: em 19/09 o verificador de DM errou nos dois sentidos, e a saída de pessoa era uma nota livre que não
        dizia QUE tela foi vista (a DM da beatriz estava com "Sending…" congelado). Etapa com efeito externo só se
        confirma citando uma evidência `screenshot` com imagem, desta execução e deste aparelho — a nota continua
        valendo como comentário. Sem efeito externo (navegação parada), o print é opcional: nada saiu da máquina."""
        if evidence_id is None:
            if etapa["side_effect"]:
                raise RunError("evidence_required", "Esta etapa tem efeito externo: para confirmar, indique o print "
                                                    "(evidência) que mostra o resultado, não só uma nota.", 422)
            return None
        ev = self.repo.db.one("SELECT * FROM evidence WHERE id=?", (evidence_id,))
        if (ev is None or ev["run_id"] != obj["run_id"] or ev["instance_id"] != obj["instance_id"]
                or ev["kind"] != "screenshot" or ev["redacted"] or not ev["path"]):
            raise RunError("invalid_evidence", f"A evidência #{evidence_id} não é um print deste item (desta execução, "
                                               f"de {obj['instance_id']}, com imagem).", 422)
        return ev

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
            evidencia = self._print_da_confirmacao(obj, blocking, body.evidence_id)
            confirmado = "Confirmado manualmente pelo usuário" + (
                f", com base na evidência #{evidencia['id']} (print de {evidencia['ts']})" if evidencia else "") + "." + note
            self.repo.transition_step(blocking["id"], StepStatus.succeeded, detail=confirmado,
                                      result=StepResult(verified=False, evidence_text=confirmado,
                                                        evidence_id=evidencia["id"] if evidencia else None))
            # A etapa vira feita, mas quem "vira fato" no histórico do perfil é a confirmação da INTERAÇÃO:
            # sem isto, relacionamento, conversa e memória seguiriam sem a mensagem que o usuário viu sair.
            if self.profiles is not None:
                self.profiles.confirm_effects_of_step(obj["profile_id"], blocking["id"], evidence=confirmado)
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
