"""Operações de execução pedidas pelo painel: criar/planejar, iniciar, pausar, continuar, cancelar,
retomar itens elegíveis, resolver bloqueios e gerar o relatório final."""
from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
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
from ..modules.applications.infrastructure.registry import nomes_e_apelidos
from ..modules.execution.domain.plan_report import spec_from_decl
from ..modules.execution.infrastructure.providers import resource_providers
from ..modules.identity.application.available_data import common_data, missing_secrets, profile_variables
from ..modules.identity.infrastructure.profile_data import SqlProfileDataStore
from ..modules.skills.infrastructure.run_planning import RunPlan, SkillRunPlanner
from ..models import (RUN_SEM_TRABALHO, RUN_TERMINAL, DistributeSpec, DistributionPick, DistributionPreview, InstanceState, MissingInfo,
                      ObjectiveDTO, ObjectiveStatus, Plan, PlanStep, ResolveBody, ResolvedTargetDTO, RunCreate, RunStatus,
                      RunSummary, RunTarget, RunTargetsPreview, RunTargetsResolveBody, SessionStatus, StepResult,
                      StepStatus)
from ..planning.apps_do_comando import apps_citados, pede_site
from ..planning.capabilities import (CapabilityCatalog, atualizar_pos_condicoes, efeito_fora_do_catalogo,
                                     load_catalog)
from ..planning.decisao_fechada.entidades import registrar_fonte_dos_apps
from ..planning.catalog import capabilities_of, session_provider_of
from ..planning.parsing import apps_do_plano, texto_fora_do_catalogo
from ..planning.provider import AIError, AIProvider, AppContext, MarcaDaChamada, PlanRequest
from ..security.redaction import redact
from ..shared.costuras import SISTEMA
from ..shared.resources import Target
from ..util import now_iso, parse_iso, to_iso
from .balanceamento import Candidato, Distribuicao, Servidor, distribuir
from .costuras import (SEM_COSTURAS, CancelamentoDeExecucao, CosturasDeAprendizado, PedidoDeLicoes,
                       RepeticaoDeExecucao, ResolucaoDeItem, avisar, pedir_licoes)
from .dado_da_persona import DadoDaPersonaAusente, faltas_por_aparelho, perguntas as perguntas_do_dado
from .perguntas import mensagem_da_palavra_solta, pergunta_sensivel_aberta, tipo_sensivel
from .projecao import HistoricoDeAcoes, projetar, resumo
from .repository import Repository
from .scheduler import WAKEABLE, Scheduler
from .sombra_intencao import SombraDaIntencao

log = logging.getLogger("poc.runs")

# O filtro da sombra da intenção conhece os apps da plataforma pelo `app.yaml` (nome, rótulo e apelidos; ADR-052) sem
# importar a camada de módulos: a fila, que já monta os nomes do catálogo de destinos para a sombra (`dados_da_sombra`),
# registra a fonte na subida. Inversão de dependência no lugar do import tardio (catraca de `test_arquitetura`, suíte 9).
registrar_fonte_dos_apps(nomes_e_apelidos)

#: Estados de onde, com o rodízio LIGADO, o aparelho volta ao ar sozinho: os que ele acorda (`WAKEABLE`) mais o
#: desligamento em voo, que termina num deles. Com o rodízio desligado, nenhum destes volta sem uma pessoa.
_VOLTAM_COM_RODIZIO = WAKEABLE | {InstanceState.stopping}
#: 29.50: a pergunta (`needs_input`) sem resposta por este tempo expira PELO SISTEMA (`RunService.expirar_sem_resposta`).
#: Antes, a execução esperava para sempre, e só um cancelamento pela rota a tirava do ar, o que é um sinal de PESSOA
#: (`cancelou_execucao`, ADR-054) que ninguém deu. É o PADRÃO: o prazo de verdade é `execucao.pergunta_vence_h` (31.43).
NEEDS_INPUT_EXPIRA_H = 24
#: 31.50: a marca (em `settings`) de quando o vencimento foi visto ligado; a carência conta dela (`_ligado_desde`).
CHAVE_LIGADO_DESDE = "vencimento_ligado_desde"
def _etapa_segura(etapa: Mapping[str, object] | None) -> str | None:
    """31.50 (revisão do #313): a chave da etapa só vai no lembrete quando é a da ação de catálogo (`open_mail_inbox`,
    ou a cópia `open_mail_inbox_i2` do for_each). Num plano livre a chave é escrita pela IA e pode levar um nome
    (`send_dm_<nome>`)."""
    if not etapa or not etapa["capability"]:
        return None
    chave = str(etapa["key"])
    return chave if re.fullmatch(re.escape(str(etapa["capability"]).lower()) + r"(_i\d+)?", chave) else None


#: 31.50: o lembrete sai quando faltam estas horas para o vencimento (`RunService.lembrar_antes_de_vencer`).
LEMBRETE_ANTES_H = 2
#: 31.50: o evento do lembrete. Quem escreve o texto ao dono é o montador dos avisos (28.31); aqui só vão os dados.
EVENTO_DO_LEMBRETE = "pendencia.vence_em"
#: 31.43: a marca para máquina de todo vencimento do sistema (pergunta ou bloqueio), no `data` do evento.
REGRA_DO_VENCIMENTO = "31.43"
MOTIVO_VENCIDO = "vencido_sem_resposta"


def _horas(h: float) -> int | float:
    """24.0 vira 24 (texto e evento sem casa decimal à toa); 0.5 continua 0.5."""
    return int(h) if float(h).is_integer() else h


def pede_outro_alvo(command: str, apps: list[AppContext], pacotes_dos_aparelhos: set[str]) -> bool:
    """O comando pede um site/navegador ou nomeia um app registrado que não é o da conta dos aparelhos?

    Desde o 24.1 isto não derruba mais o catálogo: é o que põe o planejamento no modo ENTRE APPS (`_catalogos`)."""
    return pede_site(command) or any(a.package not in pacotes_dos_aparelhos for a in apps_citados(command, apps))


class RunError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        # O que o painel precisa para OFERECER a saída em vez de só mostrar a recusa (ex.: a lista por aparelho
        # do pré-voo, e quais seguem aptos). Vazio = a mensagem já diz tudo.
        self.details = details or {}


def _celula(texto: str | None) -> str:
    """Texto livre numa célula de tabela Markdown: uma linha, sem a barra que abriria outra coluna."""
    return " ".join(str(texto or "").split()).replace("|", "/")


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


#: Os motivos do compilador que têm frase própria para o dono (o resto vai como o compilador escreveu, sem o id).
_MOTIVO_PARA_O_DONO = (("não dá valor a todos os parâmetros do plano", "faltam valores para os parâmetros do plano"),)


def _motivo_para_o_dono(mensagem: str, ref: str) -> str:
    """A mensagem do compilador sem o id da habilidade na frente (que podia vir repetido) e sem o ponto final."""
    texto = mensagem.strip()
    while ref and texto.startswith(ref + ":"):
        texto = texto[len(ref) + 1:].strip()
    for trecho, frase in _MOTIVO_PARA_O_DONO:
        if trecho in texto:
            return frase
    return texto.rstrip(".")


class RunService:
    def __init__(self, repo: Repository, scheduler: Scheduler, devices: DeviceManager, provider: AIProvider,
                 profiles: Any = None, secrets: Any = None, *, skills: SkillRunPlanner):
        self.flows = scheduler.flows
        #: RESOLVE + COMPILE (fase G): o registro de habilidades (skill publicada → fluxo ativo → nada) e o compilador.
        #: É a MESMA porta que `apps_exigidos` e `POST /api/flows/match` usam (decisão P2).
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
        repo.prazo_do_vencimento = self._prazo_para_os_dtos     # 31.50: o `vence_em` dos DTOs
        self._marca_lida: str | None = None
        self._historico: HistoricoDeAcoes | None = None
        self.devices = devices
        self.provider = provider
        # Serviço social (opcional): resolve perfil ↔ aparelho. Sem ele, só execução por aparelho.
        self.profiles = profiles
        self._planning: dict[str, asyncio.Task[None]] = {}
        #: Costuras do aprendizado (ADR-054, A2), injetadas pelo AppState: as lições do planejador e o aviso dos gestos
        #: de uma pessoa (resolver um item, repetir itens). No-op por padrão; nunca mudam o que o gesto faz.
        self.costuras: CosturasDeAprendizado = SEM_COSTURAS
        #: Sombra da intenção (31.9, ADR-069), injetada pelo AppState. `None` = não observa. Só lê; nunca muda o plano.
        self.sombra_intencao: SombraDaIntencao | None = None
        #: Execuções cujo plano o provedor RECUSOU (`needs_input` por `refusal`): a sombra não as observa (31.9, revisão).
        self._sem_sombra: set[str] = set()
        #: Sorteio do canário de IA (item 17.7), em [0, 1). Injetável: o teste fixa o valor em vez de depender da sorte.
        self.sorteio: Callable[[], float] = random.random

    # ------------------------------------------------------------------ criar + planejar
    @staticmethod
    def _recusar_credencial(command: str) -> None:
        """ADR-025 (22d65f): senha escrita NO comando ia em claro para `runs.command`, para a API de execuções, para o
        prompt do planejador e para o histórico do navegador. Recusa antes de qualquer gravação, apontando o lugar
        certo (ADR-040: a conta da persona). A detecção é por formato (`senha: …`, `password=…`), a mesma da
        redação dos eventos. A prévia da distribuição pelo comando (24.6) passa por aqui também."""
        if redact(command) != command:
            raise RunError("credencial_no_comando",
                           "O comando contém uma credencial (ex.: \"Senha: …\"). O texto do comando vai ao provedor de "
                           "IA e fica no histórico: tire a senha dele. A senha fica guardada na conta da persona (aba "
                           "Contas do perfil), com o seu consentimento, e a automação a digita de lá sem passar pela IA.",
                           409)

    def _recusar_palavra_solta(self, req: RunCreate) -> None:
        """29.52: com uma pergunta de senha ou código aberta para estes aparelhos, um pedido novo de UMA palavra é,
        quase sempre, a resposta mandada no lugar errado (a senha não tem formato que a redação pegue). Sem aparelho
        explícito no pedido (persona, distribuição), qualquer pergunta aberta conta: na dúvida, recusa. O laço de
        pedidos (`origem`) não passa aqui: ninguém digitou aquele texto agora."""
        palavra = req.command.strip()
        if not palavra or any(c.isspace() for c in palavra):
            return
        tipo = pergunta_sensivel_aberta(self.repo.db, req.instance_ids)
        if tipo is not None:
            raise RunError("credencial_na_resposta", mensagem_da_palavra_solta(tipo), 409, {"tipo": tipo})

    def _perfil_de_ia(self, req: RunCreate) -> tuple[str, str] | None:
        """Perfil de IA da execução (item 17.7): o pedido manda; sem pedido, o canário sorteia; senão, o padrão.

        O perfil desconhecido é recusado ANTES de criar a execução: aceitar e rodar no padrão faria a execução dizer
        "rodei no candidato" sem ter rodado, e a comparação A/B mediria o padrão contra ele mesmo."""
        ai = self.scheduler.cfg.file.ai
        if req.ai_profile is not None:
            if req.ai_profile not in ai.profiles:
                conhecidos = ", ".join(sorted(ai.profiles)) or "nenhum declarado"
                raise RunError("ai_profile_desconhecido",
                               f"Perfil de IA '{req.ai_profile}' não está em ai.profiles (perfis: {conhecidos}).", 422)
            return req.ai_profile, "explicit"
        canario = ai.canary
        if canario.profile and canario.fraction > 0 and self.sorteio() < canario.fraction:
            return canario.profile, "canary"
        return None

    def create(self, req: RunCreate, *, origem: tuple[str, str] | None = None, prioridade: int = 0,
               prova: str | None = None) -> RunSummary:
        """Cria a execução. `origem` = `(pedido_id, ocorrencia_id)` é só do laço de pedidos (28.4, D3): vai no mesmo
        `INSERT` de `runs` e não existe na API pública (`RunCreate` recusa campo extra). `prioridade` (28.6) idem:
        interna, 0 por padrão; maior passa na frente no despacho do escalonador. `prova` (30.37) idem: o fluxo que esta
        EXECUÇÃO DE PROVA prova, do despachante da validação; o plano é o do fluxo, não o do planejador."""
        self._recusar_credencial(req.command)
        if origem is None:
            self._recusar_palavra_solta(req)
        perfil_de_ia = self._perfil_de_ia(req)
        pedido = {"instance_ids": list(req.instance_ids), "profile_ids": list(req.profile_ids),
                  "targets": [t.model_dump() for t in req.targets]}
        if req.distribute is not None:
            req = req.model_copy(update={"instance_ids": self._distribuir(req.distribute, req.only_ready,
                                                                          req.command)})
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
            return self._criar_com_perguntas(req, resolucao, comando, pedido, perfil_de_ia, origem, prioridade)
        perfis = {a.instance_id: a.profile_id for a in resolucao.alvos}
        unknown = [i for i in req.instance_ids if i not in self.devices.devices]
        if unknown:
            raise RunError("unknown_instance", f"Instância(s) desconhecida(s): {', '.join(unknown)}", 400)
        loja = [i for i in req.instance_ids if self.devices.devices[i].store]
        if loja:
            raise RunError("store_instance", f"{', '.join(loja)} é a loja (Play Store): ela só guarda o aplicativo "
                                             "oficial e não executa tarefas. Escolha aparelhos do parque.", 400)
        # O CONJUNTO de apps do comando (item 24.5), o mesmo com que os alvos foram resolvidos: as checagens abaixo
        # valem para cada app que a tarefa usa, não só para o app principal do aparelho.
        apps = resolucao.app_ids
        if (impedidos := self._incompativeis(req.instance_ids, apps)):
            # A regra do pedido: a limitação é explicada ANTES de agendar. Sem isto, o objetivo era despachado,
            # a versão do app não instalava (ou instalava e não abria) e o operador só descobria no meio, como
            # `INSTALL_FAILED_NO_MATCHING_ABIS` ou `app_incompatible` no fundo de uma etapa.
            raise RunError("app_incompativel",
                           "Estes aparelhos não conseguem rodar a versão destinada a eles: "
                           + "; ".join(impedidos) + ".", 409)
        if (mistura := self._mistura_de_apps(req.instance_ids, apps)) is not None:
            raise RunError(*mistura)
        self._exigir_apps_do_fluxo(req, comando)
        # PRÉ-VOO, antes de chamar o planejador: a recusa explicada já existia para o comando do painel e não
        # existia para a execução — a tarefa era aceita, planejada (gastando chamada ao planejador) e só então
        # bloqueava no aparelho. Aqui ela para antes, com o motivo e o que fazer, por aparelho.
        req = self._exigir_pre_voo(req, comando, perfis, apps)
        status = self.provider.status()
        if not status.configured:
            raise RunError("ai_not_configured", status.notice, 503)
        # ADR-040: nada de cofre aqui. A credencial é da conta da persona de cada aparelho; o que a execução carrega
        # são NOMES, montados no planejamento, e o consentimento já foi dado na conta.
        row, created = self.repo.create_run(req, simulated=self.provider.simulated,
                                            targets=self._foto(req, resolucao, comando, pedido),
                                            ai_profile=perfil_de_ia, origem=origem, prioridade=prioridade, prova=prova)
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
                                       app_ids=list(a.app_ids), origem=a.origem, motivo=a.motivo)
                     for a in resolucao.alvos],
            questions=[p.as_dict() for p in resolucao.perguntas], command_sem_destinos=comando, warnings=avisos)

    def _resolver(self, command: str, instance_ids: Sequence[str], profile_ids: Sequence[str],
                  targets: Sequence[RunTarget], politica: str, *, com_texto: bool = True) -> tuple[Resolucao, str]:
        """O texto (`TargetExtractor`) e a seleção passam pelo MESMO `resolver_alvos` na prévia, na criação e no
        planejamento de execução antiga. Recusa vira `RunError` com o código do resolvedor."""
        if (profile_ids or targets) and self.profiles is None:
            raise RunError("profiles_unavailable", "Execução por perfil indisponível nesta instalação.", 400)
        destinos = (TargetExtractor(self._catalogo()).extrair(command) if com_texto
                    else DestinosNoTexto(DicasDoTexto(), command))
        apps = self._app_do_comando(destinos.command_sem_destinos, targets)
        pedido = PedidoDeAlvos(
            tuple(instance_ids), tuple(profile_ids),
            tuple(AlvoPedido(t.profile_id, tuple(t.instance_ids), t.app_id) for t in targets),
            "all" if politica == "all" else "primary" if politica == "primary" else "one", app_ids=tuple(apps))
        try:
            return resolver_alvos(pedido, destinos.dicas, self._mundo(apps)), destinos.command_sem_destinos
        except RecusaDeAlvo as exc:
            raise RunError(exc.code, exc.message, exc.status) from exc

    def sem_destinos(self, command: str) -> str:
        """O comando como a RESOLVE o vê na execução: sem os trechos de destino ("com a persona Beltrano"). As prévias
        de casamento (`/flows/match`, `/skills/resolve`) passam por aqui para não casarem diferente da execução."""
        return TargetExtractor(self._catalogo()).extrair(command).command_sem_destinos

    def _app_do_comando(self, comando: str, targets: Sequence[RunTarget]) -> list[str]:
        """O CONJUNTO de apps que a tarefa usa (item 24.5, ADR-058), na ordem; o primeiro é o `app_id` dos alvos.

        1. os apps que a interface disse nos alvos explícitos (a conta que a tarefa usa);
        2. a habilidade ou o fluxo casado: os apps do plano dele, o do plano primeiro e depois os das etapas, na ordem
           do plano (não `apps_exigidos`, que ordena pelo nome e trocaria o principal);
        3. sem nada casado, os apps que o comando CITA (`apps_citados`, a mesma leitura do 24.1): todos, quando cita
           dois ou mais ("leia o e-mail no Outlook e curta o post no Instagram"); quando cita um só, ele entra se for
           app de CONTA ("leia o e-mail no Outlook e curta o post de @x" diz a conta do Outlook, e o Instagram é o do
           aparelho). Um app SEM conta citado sozinho ("abra o QA Messenger") não diz conta nenhuma: o comando é o de
           um app de sempre, o app do aparelho decide, e a persona num aparelho com duas continua sendo pergunta.

        Antes era UM app — o dos alvos, quando havia um só, ou o da habilidade que exigia um só — e o comando entre
        apps, sem nenhum dos dois, roteava sem app: persona, sessão pronta e aparelho eram escolhidos às cegas (R5).
        Vazio = não se sabe; o app do aparelho decide depois, como sempre.
        """
        dos_alvos = [t.app_id for t in targets if t.app_id]
        casado = self.skills.for_command(comando, None)
        if casado is not None and casado.plan is not None:
            do_comando = [casado.plan.app_id, *casado.plan.required_apps]
        else:
            citados = [str(a.id) for a in apps_citados(comando, self._apps_configurados()) if a.id]
            do_comando = citados if len(citados) > 1 else [a for a in citados if a in self._apps_de_conta(citados)]
        return [a for a in dict.fromkeys([*dos_alvos, *do_comando]) if a]

    def _apps_de_conta(self, app_ids: Sequence[str]) -> set[str]:
        """Dos apps dados, os que usam a CONTA da persona: o manifesto pede perfil vinculado (`precisa_de_perfil`,
        o Outlook) ou declara login gerenciado (provedor de sessão, o Instagram). Chrome e QA Messenger não usam."""
        pacote = self._pacote_por_app(app_ids)
        return {a for a in app_ids
                if (d := capabilities_of(pacote.get(a))).needs_profile or d.session_provider}

    def _apps_configurados(self) -> list[AppContext]:
        """Os apps da instalação (`apps`), como o planejador e a leitura de apps citados os veem."""
        return [AppContext(a["id"], a["name"], a["package"], a["activity"], a["nav_hints"], loads(a["known_selectors"]))
                for a in self.repo.db.query("SELECT * FROM apps ORDER BY name")]

    def _pacote_por_app(self, app_ids: Sequence[str]) -> dict[str, str]:
        """`{id do app: pacote}` dos apps cadastrados (a tabela `apps` é por instalação)."""
        if not app_ids:
            return {}
        marcas = ",".join("?" for _ in app_ids)
        return {str(r["id"]): str(r["package"]) for r in self.repo.db.query(
            f"SELECT id, package FROM apps WHERE id IN ({marcas})", tuple(app_ids)) if r["package"]}

    def _pacotes_dos_apps(self, app_ids: Sequence[str]) -> list[str]:
        """Id de app → pacote, na ordem dos ids; id sem cadastro fica de fora."""
        pacote = self._pacote_por_app(app_ids)
        return [p for p in dict.fromkeys(pacote.get(a) for a in app_ids) if p]

    def _fora_de_pronto(self, app_ids: Sequence[str]) -> dict[str, set[str]]:
        """`{app: aparelhos em que ele se SABE fora de pronto}` (linha em `device_app_state` fora de pronto). O que
        nunca foi observado não entra: é a mesma regra do pré-voo, e a mesma leitura do roteamento e da distribuição."""
        pacote = self._pacote_por_app(app_ids)
        fora: dict[str, set[str]] = {}
        for a in dict.fromkeys(app_ids):
            if (pkg := pacote.get(a)):
                fora[a] = {str(r["instance_id"]) for r in self.repo.db.query(
                    "SELECT instance_id FROM device_app_state WHERE package_name=? AND state NOT IN (?,?)",
                    (pkg, *self._APP_PRONTO))}
        return fora

    def _nomes_dos_apps(self, app_ids: Sequence[str]) -> str:
        """Os apps pelo nome do cadastro, na ordem dada ("QA Messenger, Notas"); id sem cadastro vai como está."""
        if not app_ids:
            return ""
        marcas = ",".join("?" for _ in app_ids)
        nome = {str(r["id"]): str(r["name"]) for r in self.repo.db.query(
            f"SELECT id, name FROM apps WHERE id IN ({marcas})", tuple(app_ids)) if r["name"]}
        return ", ".join(nome.get(a, a) for a in app_ids)

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

    def _mundo(self, app_ids: Sequence[str] = ()) -> Mundo:
        """O parque como `resolver_alvos` o vê: vínculos ativos (com os apps que cada um serve), os aparelhos aptos
        agora (fora da loja, servidor disponível, apps prontos quando se sabem os apps) e as sessões `session_ready`
        por (persona, aparelho) em `account_sessions`. Só leitura.

        Item 24.5: por CONJUNTO de apps. Apto é o aparelho sem nenhum app do conjunto sabidamente fora de pronto; a
        sessão pronta de um par é a de TODOS os apps de login gerenciado do conjunto, abertos naquele aparelho. App sem
        conta (Chrome) vai em `Mundo.sem_conta`: não tem vínculo nem sessão, e não tira ninguém de `serve`.
        """
        db = self.repo.db
        apps = [a for a in dict.fromkeys(app_ids) if a]
        contas: dict[str, set[str]] = {}
        for r in db.query("SELECT profile_id, app_id FROM profile_accounts"):
            contas.setdefault(str(r["profile_id"]), set()).add(str(r["app_id"]))
        vinculos = tuple(
            Vinculo(str(v["profile_id"]), str(v["instance_id"]),
                    frozenset({str(v["app_id"])} if v["app_id"] else contas.get(str(v["profile_id"]), set())),
                    bool(v["is_primary"]))
            for v in db.query("SELECT profile_id, instance_id, app_id, is_primary FROM device_profile_bindings"
                              " WHERE active=1 ORDER BY is_primary DESC, id"))
        # App que se SABE não pronto (linha em `device_app_state` fora de pronto) tira o aparelho dos aptos; o que
        # nunca foi observado não fecha a porta (a mesma regra do pré-voo). Entre apps, basta UM dos apps fora.
        pacote = self._pacote_por_app(apps)
        sem_app: set[str] = set().union(*self._fora_de_pronto(apps).values())
        servidores = self.scheduler.servidores()
        aptos = frozenset(
            iid for iid, rt in self.devices.devices.items()
            if not rt.store and iid not in sem_app
            and (srv := servidores.get(self.scheduler.servidor_de(rt))) is not None and srv.disponivel)
        de_conta = self._apps_de_conta(apps)
        sem_conta = frozenset(a for a in apps if a not in de_conta)
        # A sessão que conta é a dos apps de LOGIN GERENCIADO (provedor de sessão): só eles chegam a `session_ready`.
        # Um app de conta sem login gerenciado (o Outlook antes do 23.8) nunca fica pronto e zeraria a interseção.
        # Nenhum com login gerenciado: o próprio conjunto — a regra de antes para um app só, que filtrava por ele.
        relevantes = [a for a in apps if capabilities_of(pacote.get(a)).session_provider] or apps
        sql = ("SELECT a.profile_id, s.instance_id FROM account_sessions s JOIN profile_accounts a ON a.id = s.account_id"
               " WHERE s.status=?")
        pares: list[frozenset[tuple[str, str]]] = []
        for app_id in relevantes or [None]:
            params: tuple[object, ...] = (SessionStatus.session_ready.value,)
            filtro = ""
            if app_id:
                filtro, params = " AND a.app_id=?", params + (app_id,)
            pares.append(frozenset((str(r["profile_id"]), str(r["instance_id"]))
                                   for r in db.query(sql + filtro, params)))
        prontas = frozenset.intersection(*pares)
        nomes = tuple(
            (str(r["id"]), str(r["display_name"] or " ".join(x for x in (r["first_name"], r["last_name"]) if x)
                               or (f"@{r['username']}" if r["username"] else r["id"])))
            for r in db.query("SELECT id, username, first_name, last_name, display_name FROM instagram_profiles"))
        ligados = frozenset(iid for iid, rt in self.devices.devices.items() if rt.state == InstanceState.online)
        return Mundo(vinculos, aptos, prontas, self._desempatar, nomes, sem_conta, ligados)

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
                             pedido: Mapping[str, object], perfil_de_ia: tuple[str, str] | None = None,
                             origem: tuple[str, str] | None = None, prioridade: int = 0) -> RunSummary:
        """Contradição ou ambiguidade de destino: a execução nasce em `needs_input` com as perguntas estruturadas
        (o mesmo formato das da RESOLVE), sem plano — nem parcial — e sem chamar o planejador."""
        row, created = self.repo.create_run(req, simulated=self.provider.simulated,
                                            targets=self._foto(req, resolucao, comando, pedido),
                                            ai_profile=perfil_de_ia, origem=origem, prioridade=prioridade)
        if created:
            self._pedir_resposta(row["id"], [p.as_dict() for p in resolucao.perguntas])
        return self.repo.run_summary(self.repo.run_row(row["id"]), deduplicated=not created)

    def _pedir_resposta(self, run_id: str, perguntas: list[dict[str, object]],
                        assunto: str = "os destinos precisam de resposta antes de planejar") -> None:
        texto = " | ".join(str(p["question"]) for p in perguntas)
        self.repo.bus.emit("log", f"Execução {run_id}: {assunto}",
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
        resolucao = resolver_alvos(PedidoDeAlvos(instance_ids=tuple(ids)), DicasDoTexto(), self._mundo())
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
    #: A recusa da distribuição cujo comando não diz app nenhum (item 24.6): sem app escolhido, é o texto que diz.
    SEM_APP_NA_DISTRIBUICAO = ("o comando não diz o app: cite-o no texto (“… no <nome do app>”) ou escolha o app dos "
                               "aparelhos a distribuir")

    def _apps_da_distribuicao(self, spec: DistributeSpec, command: str | None,
                              app_ids: Sequence[str] = ()) -> list[str]:
        """Os apps cujos aparelhos a distribuição reparte (item 24.6, R9). O app escolhido pela pessoa, quando há;
        senão o conjunto já resolvido por quem chama (o modo Automático); senão o do COMANDO, pela mesma leitura do
        roteamento (`_app_do_comando`: habilidade casada, apps citados). Um app SEM conta citado sozinho ("abra o
        QA Messenger") não entra no roteamento, que não precisa dele para achar persona, mas aqui é ele que diz de
        quais aparelhos se trata: entra pela citação. Vazio = o comando não diz; quem chama explica."""
        if spec.app_id:
            return [spec.app_id]
        if app_ids:
            return [a for a in dict.fromkeys(app_ids) if a]
        if not command:
            return []
        return self._app_do_comando(command, []) or self._apps_citados(command)

    def _apps_citados(self, comando: str) -> list[str]:
        """Os apps que o comando cita, todos, inclusive o app SEM conta citado sozinho que `_app_do_comando` deixa
        de fora. É ele que diz de quais aparelhos se trata (a distribuição, o modo Automático: item 29.70)."""
        return [str(a.id) for a in apps_citados(comando, self._apps_configurados()) if a.id]

    def _candidatos_dos_apps(self, apps: Sequence[str]) -> tuple[list[Candidato], list[str]]:
        """Os candidatos do balanceamento para um CONJUNTO de apps, e o que cortou aparelho pelo caminho.

        Um app só: exatamente `Scheduler.candidatos_do_app`, a regra de sempre. Vários (item 24.6): a UNIÃO dos
        aparelhos cujo app principal é um deles — não os do primeiro citado, que dependeria da ordem do texto ("leia
        no Outlook e curta no Instagram" ficaria só com aparelhos do Outlook) —, com duas cercas. Se algum app do
        conjunto exige conta pela régua da distribuição (`app_exige_conta`, provedor de sessão), só aparelho com perfil
        ATIVO vinculado; a consulta repete a de `candidatos_do_app`, que o agendador não expõe separada. E sai o
        aparelho em que algum app do conjunto se SABE fora de pronto (`_fora_de_pronto`), dito app por app."""
        if len(apps) == 1:
            return self.scheduler.candidatos_do_app(apps[0]), []
        db = self.repo.db
        principal = {str(r["id"]): r["app_id"] for r in db.query("SELECT id, app_id FROM instances")}
        exige_conta = any(self.scheduler.app_exige_conta(a) for a in apps)
        com_perfil = {str(r["instance_id"]) for r in db.query(
            "SELECT b.instance_id FROM device_profile_bindings b JOIN instagram_profiles p ON p.id=b.profile_id"
            " WHERE b.active=1 AND COALESCE(p.status, 'active')='active'")} if exige_conta else set()
        fora = self._fora_de_pronto(apps)
        cortados: dict[str, int] = {}
        ids: list[str] = []
        for rt in self.devices.devices.values():
            if rt.store or principal.get(rt.id) not in apps or (exige_conta and rt.id not in com_perfil):
                continue
            sem = [a for a in apps if rt.id in fora.get(a, set())]
            for a in sem:
                cortados[a] = cortados.get(a, 0) + 1
            if not sem:
                ids.append(rt.id)
        cortes = [f"{n} aparelho(s) com o app {self._nomes_dos_apps([a])} fora de pronto" for a, n in cortados.items()]
        return self.scheduler.candidatos_de(ids), cortes

    def previa_de_distribuicao(self, spec: DistributeSpec, command: str | None = None, *,
                               app_ids: Sequence[str] = ()) -> DistributionPreview:
        """Quem seria escolhido AGORA, sem criar nada — é o que o painel mostra antes de Executar.

        Item 24.6: sem `spec.app_id`, os apps são os do comando (ou `app_ids`, já resolvidos por quem chama); o
        painel deixou de escolher um app pela pessoa. Comando que não diz app nenhum: prévia vazia, com o motivo."""
        servidores = self.scheduler.servidores()
        apps = self._apps_da_distribuicao(spec, command, app_ids)
        if not apps:
            return DistributionPreview(requested=spec.count, picks=[], per_server={}, missing=spec.count,
                                       reasons=[self.SEM_APP_NA_DISTRIBUICAO])
        candidatos, cortes = self._candidatos_dos_apps(apps)
        d = distribuir(spec.count, candidatos, servidores)
        motivos = list(d.faltas)
        if len(apps) > 1:
            # O balanceamento fala de "este app"; com vários, a frase diz quais — e o corte por app fora de pronto.
            lista = self._nomes_dos_apps(apps)
            motivos = [m.replace("vinculado a este app", f"vinculado a um dos apps do comando ({lista})")
                        .replace("deste app", f"dos apps do comando ({lista})") for m in motivos]
            if d.faltaram:
                motivos.extend(cortes)
        de_conta = [a for a in apps if self.scheduler.app_exige_conta(a)]
        if d.faltaram and de_conta:
            # O filtro que mais corta num app com conta é o do perfil — dito primeiro, com o número.
            motivos = [m for m in motivos if not m.startswith("o parque só tem")]
            quem = "este app exige" if len(apps) == 1 else (
                f"o app {self._nomes_dos_apps(de_conta)} exige" if len(de_conta) == 1
                else f"os apps {self._nomes_dos_apps(de_conta)} exigem")
            motivos.insert(0, f"{quem} conta: só {len(candidatos)} aparelho(s) têm perfil ativo vinculado")
        return self._previa(spec.count, d, servidores, motivos)

    def previa_sem_conta(self, quantos: int, app_ids: Sequence[str]) -> DistributionPreview:
        """A distribuição da tarefa SEM conta do modo Automático (item 29.70: "No QA Messenger, leia …").

        Candidato é o aparelho que tem os apps: o app principal dele é um dos do pedido, ou todos estão sabidamente
        prontos nele (`device_app_state`; o QA Messenger está instalado no parque inteiro, mas nenhum aparelho o tem
        como principal). Aparelho com conta real logada (perfil ATIVO vinculado) só entra se nenhum aparelho sem conta
        estiver apto, e a prévia diz isso nos dois sentidos: tarefa sem conta não tem por que gastar o convidado de
        uma conta real."""
        apps = [a for a in dict.fromkeys(app_ids) if a]
        servidores = self.scheduler.servidores()
        db = self.repo.db
        principal = {str(r["id"]): r["app_id"] for r in db.query("SELECT id, app_id FROM instances")}
        pacote = self._pacote_por_app(apps)
        pronto = {a: {str(r["instance_id"]) for r in db.query(
            "SELECT instance_id FROM device_app_state WHERE package_name=? AND state IN (?,?)",
            (pacote[a], *self._APP_PRONTO))} for a in apps if a in pacote}
        fora = self._fora_de_pronto(apps)
        com_conta = {str(r["instance_id"]) for r in db.query(
            "SELECT b.instance_id FROM device_profile_bindings b JOIN instagram_profiles p ON p.id=b.profile_id"
            " WHERE b.active=1 AND COALESCE(p.status, 'active')='active'")}
        sem: list[str] = []
        com: list[str] = []
        for rt in self.devices.devices.values():
            tem = principal.get(rt.id) in apps or all(rt.id in pronto.get(a, set()) for a in apps)
            if rt.store or not tem or any(rt.id in fora.get(a, set()) for a in apps):
                continue
            (com if rt.id in com_conta else sem).append(rt.id)
        d = distribuir(quantos, self.scheduler.candidatos_de(sem), servidores)
        motivos = list(d.faltas)
        if not d.escolhidos and com:
            d = distribuir(quantos, self.scheduler.candidatos_de(com), servidores)
            motivos = list(d.faltas)
            if d.escolhidos:
                motivos.insert(0, "nenhum aparelho sem conta está apto agora: a tarefa vai para aparelho com conta "
                                  f"real logada ({', '.join(e.instance_id for e in d.escolhidos)})")
        elif com:
            motivos.append(f"aparelho(s) com conta real logada ({', '.join(sorted(com))}) ficam de fora: há "
                           "aparelho sem conta apto")
        return self._previa(quantos, d, servidores, motivos)

    def _previa(self, pedidos: int, d: Distribuicao, servidores: Mapping[str, Servidor],
                motivos: list[str]) -> DistributionPreview:
        """A prévia que o painel mostra, com o aviso do teto geral do parque."""
        teto = int(self.scheduler.get_settings().max_active_devices)
        livres_no_geral = teto - len(self.scheduler.workers)
        if len(d.escolhidos) > livres_no_geral:
            motivos.append(f"o teto geral do parque ({teto} aparelhos trabalhando) segura "
                           f"{len(d.escolhidos) - max(0, livres_no_geral)} deles na fila até liberar vaga")
        return DistributionPreview(
            requested=pedidos,
            picks=[DistributionPick(instance_id=e.instance_id, server_id=e.servidor,
                                    server_name=servidores[e.servidor].nome, needs_start=e.precisa_ligar)
                   for e in d.escolhidos],
            per_server={servidores[k].nome: v for k, v in d.por_servidor().items()},
            missing=d.faltaram, reasons=motivos)

    def _distribuir(self, spec: DistributeSpec, parcial_ok: bool, command: str) -> list[str]:
        """Os aparelhos da execução distribuída. Faltando aparelho, recusa com o motivo — a menos que a pessoa
        tenha pedido "seguir só com os aptos" (`only_ready`), que aqui quer dizer "com os que houver"."""
        apps = self._apps_da_distribuicao(spec, command)
        if not apps:
            raise RunError("distribution_sem_app", f"Não dá para distribuir: {self.SEM_APP_NA_DISTRIBUICAO}.", 409)
        previa = self.previa_de_distribuicao(spec, app_ids=apps)
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

    def _mistura_de_apps(self, instance_ids: list[str], app_ids: Sequence[str] = ()) -> tuple[str, str, int] | None:
        """Recusa, ANTES de planejar, a execução que mistura aparelhos de apps diferentes quando algum tem catálogo.

        O catálogo de capabilities era escolhido POR EXECUÇÃO (`load_catalog(pacotes.pop()) if len(pacotes) == 1`):
        bastava a seleção ter aparelhos de dois apps para o planejador receber `catalog=None` e escrever etapas
        livres, sem `capability` — e a quarta porta (política, limite diário, aprovação e texto na voz de cada
        persona) deixava de opinar nas contas reais. O plano tem um `app_id` só, então a execução mista já era
        semanticamente de um app; o que ela fazia era desligar a porta em silêncio.

        A recusa acontece só quando ALGUM dos apps tem catálogo: misturar dois apps sem catálogo não perde nada,
        e recusar ali seria inventar limitação onde não há.

        Item 24.5: quando o COMANDO diz os apps (`app_ids`: os alvos, a habilidade casada ou os apps citados), o app
        principal de cada aparelho deixa de ser o app da tarefa. Todo aparelho roda as mesmas etapas, cada uma no app
        dela, com o catálogo dele (24.1) e a porta de política pelo app da etapa (24.2): não há guarda a apagar, e
        não é mistura. O que cada aparelho precisa ter, cada app do conjunto, é o pré-voo e a compatibilidade que
        conferem (`_app_preflight`, `_incompativeis`). Sem conjunto, "o app" é o de cada aparelho, e aí sim a
        seleção de apps diferentes é ambígua — a recusa de antes segue.
        """
        if app_ids:
            return None
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
                "política, limite e aprovação, e o comando não diz em que app a tarefa acontece. Refaça a execução "
                "com aparelhos de um app só, ou cite no comando o app de cada parte da tarefa — "
                f"{detalhe}.", 409)

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
                perfis: Mapping[str, str | None] | None = None,
                app_ids: Sequence[str] = ()) -> dict[str, dict[str, str]]:
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

        `app_ids` (item 24.5): os apps da tarefa — o conjunto do comando na criação, os `required_apps` do plano no
        início. O pré-voo do aplicativo confere cada um deles, além do principal do aparelho.
        """
        impedidos: dict[str, dict[str, str]] = {}
        liga_sozinho = self.scheduler.get_settings().auto_start_devices
        do_comando = self._pacotes_dos_apps(app_ids)
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
                if (recusa := self.scheduler.app_preflight(rt, do_comando)) is not None:
                    impedidos[iid] = recusa
        return impedidos

    def _exigir_pre_voo(self, req: RunCreate, comando: str, perfis: Mapping[str, str | None],
                        app_ids: Sequence[str] = ()) -> RunCreate:
        """Aplica o pré-voo: recusa com a lista por aparelho, ou segue só com os aptos quando foi isso que se pediu."""
        impedidos = self.pre_voo(req.instance_ids, secret_names=self._segredos_exigidos(comando), perfis=perfis,
                                 app_ids=app_ids)
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

    def _pacotes_da_tarefa(self, instance_ids: list[str], app_ids: Sequence[str]) -> dict[str, list[str]]:
        """`{aparelho: pacotes}` que a tarefa pode usar em cada aparelho (item 24.5): o app principal dele e cada app
        do conjunto do comando, sem repetir.

        O principal entra mesmo quando o comando cita outro app: ele é candidato do planejamento entre apps
        (`_catalogos`) — "leia o e-mail no Outlook e curta o post de @x" não nomeia o Instagram do aparelho e precisa
        dele. Sem conjunto, é só o principal, como antes.
        """
        principais = self.pacotes_da_selecao(instance_ids)
        do_comando = self._pacotes_dos_apps(app_ids)
        return {iid: [p for p in dict.fromkeys([principais.get(iid), *do_comando]) if p] for iid in instance_ids}

    def _incompativeis(self, instance_ids: list[str], app_ids: Sequence[str] = ()) -> list[str]:
        """Frases explicando quais aparelhos não rodam a versão DESEJADA dos apps da tarefa, na ordem pedida.

        A pergunta é feita sobre a versão que o parque mandou aquele aparelho ter (`device_app_state`), porque é
        ela que a execução vai instalar pela porta do app. Aparelho sem versão desejada não tem o que conferir —
        e capacidade desconhecida nunca vira recusa (ver `devices/compatibilidade.py`).

        Item 24.5: cada app que a tarefa usa naquele aparelho (`_pacotes_da_tarefa`), não só o principal. Uma frase
        por aparelho: a primeira impossibilidade já recusa.
        """
        motivos: list[str] = []
        pacotes = self._pacotes_da_tarefa(instance_ids, app_ids)
        for iid in instance_ids:
            rt = self.devices.devices.get(iid)
            if rt is None:
                continue
            # Amarrado aos APPS da tarefa: `device_app_state` guarda uma linha por PACOTE, e uma versão desejada de
            # um pacote que a tarefa não usa não tem por que impedir a execução.
            for pacote in pacotes.get(iid, []):
                linha = self.repo.db.one(
                    "SELECT r.* FROM device_app_state s JOIN app_releases r ON r.id = s.desired_release_id"
                    " WHERE s.instance_id=? AND s.package_name=?", (iid, pacote))
                if linha is None:
                    continue
                if (porque := motivo_incompativel(requisitos_de_release(linha), capacidades_de(rt),
                                                  aparelho=iid)) is not None:
                    motivos.append(porque)
                    break
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
        self._planning[run_id].add_done_callback(lambda t: self._depois_do_plano(t, run_id))   # 31.9: só observa

    def _depois_do_plano(self, tarefa: asyncio.Future[None], run_id: str) -> None:
        """Só o plano que terminou BEM vira sombra: tarefa cancelada (o `stop()` cancela o plano), com exceção ou recusada
        pelo provedor (o comando que o provedor recusou por conteúdo não vai a outro provedor) não observa nada."""
        recusado = run_id in self._sem_sombra
        self._sem_sombra.discard(run_id)
        if tarefa.cancelled():
            return
        erro = tarefa.exception()
        if erro is not None:
            # Ler `exception()` marca a exceção como recuperada: o asyncio não loga mais "Task exception was never
            # retrieved". Quem a lê aqui passa a ser o único a registrá-la.
            log.error("plano da execução %s falhou", run_id, exc_info=erro)
            return
        if recusado:
            return
        self._intencao_em_sombra(run_id)

    def _intencao_em_sombra(self, run_id: str) -> None:
        """Entrega a sombra da intenção (31.9, ADR-069) e esquece. Só observa: não decide, não grava na execução e uma falha
        aqui nunca vira falha do plano. Desligada, custa uma comparação: nada é lido nem resolvido."""
        sombra = self.sombra_intencao
        if sombra is None or not sombra.ativo():
            return
        sombra.agendar(run_id, lambda: self.dados_da_sombra(run_id))

    def dados_da_intencao(self, run_id: str) -> tuple[str, list[str | None], str | None] | None:
        """Os dados da execução para a intenção: comando sem destinos, personas por aparelho e app (ver `dados_da_sombra`).

        A assinatura é a de sempre: o rótulo de intenção do Aprendizado (30.25) lê a execução por aqui."""
        dados = self.dados_da_sombra(run_id)
        return None if dados is None else dados[:3]

    def dados_da_sombra(self, run_id: str) -> tuple[str, list[str | None], str | None, str, tuple[str, ...]] | None:
        """Roda na THREAD da sombra. `None` = não observar: execução sumida, que falhou ou foi cancelada, ou com pergunta.

        `dados_da_intencao` (público desde a reverificação B do 31.9; o rótulo de intenção do Aprendizado, 30.25, lê a
        execução por ele) é este sem o 4º item, para os dois medirem o mesmo comando sem destinos."""
        run = self.repo.run_row(run_id)
        if run is None or run["status"] in (RunStatus.failed.value, RunStatus.cancelled.value):
            return None
        perfis, comando, perguntas = self._perfis_da_execucao(run)
        if perguntas:
            return None
        apps = loads(str(run["app_ids"] or "[]"), [])
        # C3 (ADR-069): sem os destinos ANTES do `redact` e da remoção de entidades. A foto já traz o comando sem destinos;
        # os outros caminhos de `_perfis_da_execucao` devolvem o cru, e reaplicar é idempotente.
        # O 4º item é o comando ORIGINAL (rodada E do 31.9): a C7 é conferida também nele, porque tirar o destino pode partir
        # o par de usuário e senha. O 5º, os nomes do catálogo de destinos REAL (rodada F, F-B): "entre com o Fulano" é
        # destino, "entre com a girassol" não. Só a conferência os lê; nada deles vai ao pedido.
        catalogo = self._catalogo()
        nomes = tuple(dict.fromkeys([*(n for p in catalogo.personas for n in (*p.nomes, *p.handles)),
                                     *catalogo.aparelhos]))
        return (self.sem_destinos(comando), [perfis.get(str(i)) for i in loads(str(run["instance_ids"]), [])],
                str(apps[0]) if apps else None, str(run["command"] or ""), nomes)

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

    @staticmethod
    def _catalogos(comando: str, apps: list[AppContext], instances: Sequence[Mapping[str, object]]
                   ) -> tuple[CapabilityCatalog | None, dict[str, CapabilityCatalog], list[AppContext], str | None]:
        """O que vai ao planejador (item 24.1, ADR-058): `(catalog, catalogs, apps, pacote_das_licoes)`.

        Candidatos = os apps dos aparelhos mais os que o comando cita. Nenhum candidato com catálogo: plano livre, com
        todos os apps (e, com um candidato só e nenhum site, o pacote dele para as lições). Um candidato só, com catálogo, e nenhum site pedido: o planejamento por catálogo de sempre.
        Qualquer outro caso (o comando cita outro app ou pede um site, e algum candidato tem catálogo) é ENTRE APPS: os
        catálogos de todos os candidatos que têm um vão juntos, e os apps sem catálogo entram como apps de etapa livre.

        Antes, citar outro app ou um site punha o plano inteiro no caminho livre (22d65f: o Chrome num aparelho do
        Instagram), e a etapa com efeito no Instagram ficava sem ação do catálogo — a porta de política a recusava.

        O app do aparelho é candidato mesmo quando o comando cita outro: o comando que diz "leia o e-mail no Outlook e
        curta o post de @x" não nomeia o Instagram e precisa dele. O modelo só usa o que o pedido pede; os apps
        exigidos saem das etapas (`parsing.apps_do_plano`), não desta lista. App com catálogo que não é candidato
        fica fora da lista de apps do modo entre apps, para não virar app de etapa livre.
        """
        do_aparelho = {i.get("app_id") for i in instances}
        por_id = {a.id: a for a in apps if a.id}
        candidatos = [por_id[i] for i in dict.fromkeys(
            [a.id for a in apps_citados(comando, apps)] + [a.id for a in apps if a.id in do_aparelho]) if i in por_id]
        catalogos = {a.id: c for a in candidatos if a.id and (c := load_catalog(a.package)) is not None}
        # O pacote das lições do planejador independe de catálogo: com UM app no pedido e nenhum site, as lições são
        # daquele app mesmo no plano livre (antes do 24.1 era o app único dos aparelhos; sem ele, o planejador de
        # um app sem catálogo pedia lições de app nenhum e nunca recebia as dele).
        unico = candidatos[0] if len(candidatos) == 1 and not pede_site(comando) else None
        if not catalogos:
            return None, {}, apps, unico.package if unico else None
        if unico is not None:
            return catalogos[str(unico.id)], {}, apps, unico.package
        ofertados = [a for a in apps if a.id in catalogos or load_catalog(a.package) is None]
        return None, catalogos, ofertados, None

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
        apps = self._apps_configurados()
        prova: str | None = run["prova_fluxo_id"]
        try:
            # 30.37: a EXECUÇÃO DE PROVA roda o plano do próprio fluxo, sem RESOLVE e sem planejador. Não é reuso: nada
            # de `runs.flow_id`, `flows.used` nem `skill_hash` (`_registrar_resolucao`), e a sombra da intenção não a vê.
            known = None if prova else self.skills.for_command(comando, [i.get("profile_id") for i in instances])
            if prova:
                self._sem_sombra.add(run_id)
                da_prova = self.flows.plano_em_prova(prova, comando)
                if da_prova is None:
                    repo.set_run_status(run_id, RunStatus.failed,
                                        f"Prova de fluxo (validação): o fluxo {prova} foi desligado ou o comando de origem "
                                        "não cabe mais no molde dele.", level="warn")
                    return
                plan = da_prova
                self._pos_do_catalogo(run_id, plan)
                repo.decision(f"Prova de fluxo (validação): o plano é o do fluxo {prova}, com os parâmetros do comando "
                              "de origem; o planejador não é chamado.", run_id=run_id)
            elif known is not None and known.plan is None:
                self._skill_sem_plano(run_id, known)
                return
            elif known is not None and known.plan is not None:
                plan = known.plan
                self._registrar_resolucao(run_id, known)
                self._pos_do_catalogo(run_id, plan)
            else:
                # Livre, por catálogo ou ENTRE APPS (item 24.1): quem decide é `_catalogos`, pelo app dos aparelhos e
                # pelos apps que o comando cita.
                catalog, catalogos, ofertados, alvo = self._catalogos(comando, apps, instances)
                # Lições medidas do planejador (ADR-054): só quando o planejador é de fato chamado (skill ou fluxo
                # casados não pedem), uma vez por planejamento. Falha = nenhuma lição.
                licoes = pedir_licoes(self.costuras, PedidoDeLicoes(
                    papel="planner", unidade=f"plan:{run_id}", run_id=run_id, app_package=alvo or "",
                    capability="", step_hash="", simulated=bool(run["simulated"])))
                # O planejamento passa pelo MESMO laço das demais chamadas de IA (achado #96, item 4): antes ele
                # chamava `provider.plan` direto — entrava no limite de concorrência e em nada mais, ficando fora
                # da repetição com espera, do disjuntor de conta e de qualquer conferência de orçamento.
                # `objective_id=None`: é uso da execução, e ainda não há objetivo nenhum para contar chamada.
                plan = await self.scheduler.executor._ai(          # noqa: SLF001 - ponto único de chamada de IA
                    run_id, None,
                    # A lista de dados da persona COMUM a todos os aparelhos (ADR-040): nomes, nunca valores.
                    lambda: self.provider.plan(PlanRequest(
                        command=comando, run_id=run_id, instances=instances, apps=ofertados, catalog=catalog,
                        catalogs=catalogos,
                        available_data=list(common_data(self.dados, [i["profile_id"] for i in instances])),
                        lessons=list(licoes))),
                    role="plan", marca=MarcaDaChamada(motivo="plano"))
                # R6: todo plano do planejador declara os apps em que roda — os parsers já preenchem; isto cobre o
                # provedor que não preenche (um dublê, um provedor novo). Plano de skill traz os dele do compilador.
                if not plan.required_apps:
                    plan.required_apps = apps_do_plano(plan, instances)
        except AIError as exc:
            if exc.kind == "refusal":
                self._sem_sombra.add(run_id)
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
        # RA-7: a porta do item 13.2 também no PLANEJAMENTO, para todo plano (planejador, fluxo, skill), antes de
        # qualquer etapa existir. No despacho ela só recusava ao chegar na etapa com efeito, depois que os preparativos
        # (abrir, preencher destinatário, assunto…) já tinham gastado decisões.
        if plan.fora_do_catalogo:
            self._recusar_fora_do_catalogo(run_id, plan)
            return
        if self._teto_observar(run_id) and (com_efeito := [s for s in plan.steps if s.side_effect or s.commit_guard]):
            self._recusar_pelo_teto(run_id, plan, com_efeito)
            return
        recusadas = self._efeitos_fora_do_catalogo(plan, instances, apps)
        if recusadas:
            self._recusar_no_planejamento(run_id, plan, recusadas)
        repo.save_plan(run_id, plan)
        repo.decision(f"Plano ({'SIMULADO' if plan.planner.simulated else plan.planner.model}): {plan.summary} — "
                      f"{len(plan.steps)} etapa(s): " + " → ".join(s.title for s in plan.steps), run_id=run_id)
        self._anunciar_projecao(run_id, plan)
        if plan.missing or not plan.steps:
            questions = " | ".join(m.question for m in plan.missing) or "O plano veio sem etapas."
            self._medir_pergunta_sensivel(run_id, [{"field": m.field, "question": m.question} for m in plan.missing])
            repo.set_run_status(run_id, RunStatus.needs_input, questions, level="warn",
                                message=f"Execução {run_id}: faltam informações — {questions}")
            return
        # Item 31.87 (F1): `{perfil_*}` e `{conta_*_usuario}` que a persona de algum aparelho não resolve. Sem isto o
        # texto cru ia ao objetivo da etapa (receita diverge, a IA assume com `{perfil_sobrenome}` escrito). Nada é
        # materializado nem despachado: a pessoa cadastra o dado na persona e cria outra execução (a resposta por texto
        # é recusada: o valor dito no comando não alimenta o parâmetro de um fluxo reaproveitado). Só ids e rótulos.
        if faltas := faltas_por_aparelho(plan, instances):
            sem_persona = frozenset(str(i["instance_id"]) for i in instances if not i.get("profile_id"))
            self._pedir_resposta(run_id, perguntas_do_dado(faltas, sem_persona),
                                 "falta dado da persona para o plano; nada foi materializado")
            return
        try:
            repo.materialize(run_id, plan, instances)      # persistido ANTES de executar
        except DadoDaPersonaAusente as exc:
            # Rede de segurança: o pré-voo acima devia ter pedido a resposta. Sem este desvio a exceção sairia da tarefa
            # de planejamento e a execução ficaria em `planning` para sempre. `failed`, não `needs_input`: chegar aqui é
            # defeito nosso, e a mensagem só tem nomes de variável.
            repo.set_run_status(run_id, RunStatus.failed, f"Materialização recusada: {exc}", level="error")
            return
        self._fotografar_recursos(run_id, known, instances)
        run = repo.run_row(run_id)
        if run and run["cancel_requested"]:
            repo.set_run_status(run_id, RunStatus.cancelled, "Cancelada durante o planejamento")
            return
        if run and run["mode"] == "execute":
            try:
                self.start(run_id)
            except RunError as exc:
                # Corrida com um cancelamento (o `start` troca o estado por compare-and-set): o outro gesto venceu.
                log.info("execução %s não iniciou sozinha: %s", run_id, exc.message)
        else:
            repo.set_run_status(run_id, RunStatus.planned, "Plano pronto para inspeção",
                                message=f"Execução {run_id}: plano pronto; aguardando início")

    @staticmethod
    def _efeitos_fora_do_catalogo(plan: Plan, instances: Sequence[Mapping[str, object]],
                                  apps: Sequence[AppContext]) -> list[dict[str, object]]:
        """As etapas que a regra do item 13.2 recusaria no despacho, vistas no plano (RA-7).

        O app da etapa é o do despacho (`Scheduler._app_context`): o dela, senão o do plano, senão o do aparelho —
        por aparelho, porque dois aparelhos podem ter apps padrão diferentes e a etapa sem app roda no de cada um.
        Basta um aparelho em que a etapa seria recusada: o plano é o mesmo para todos."""
        por_id = {a.id: a for a in apps if a.id}
        recusadas: dict[str, dict[str, object]] = {}
        for passo in plan.steps:
            if not passo.side_effect:
                continue
            for inst in instances or ({},):
                app_id = passo.app_id or plan.app_id or inst.get("app_id")
                app = por_id.get(str(app_id)) if app_id else None
                pacote = app.package if app else plan.app_package
                motivo = efeito_fora_do_catalogo(True, passo.capability, pacote)
                if motivo:
                    recusadas[passo.key] = {"key": passo.key, "title": passo.title, "app_id": app.id if app else None,
                                            "app": (app.name or app.id) if app else pacote,
                                            "capability": passo.capability, "motivo": motivo}
                    break
        return list(recusadas.values())

    def _teto_observar(self, run_id: str) -> bool:
        """28.23: a execução nasceu com o teto `observar` (o pedido persistente que só observa)."""
        row = self.repo.run_row(run_id)
        return bool(row is not None and "teto_de_autonomia" in row.keys() and row["teto_de_autonomia"] == "observar")

    def _recusar_pelo_teto(self, run_id: str, plan: Plan, com_efeito: list[PlanStep]) -> None:
        """Item 28.23: com o teto `observar`, nenhuma etapa com efeito chega a existir. A execução termina recusada
        (`failed`, nunca `uncertain`: sem ação com efeito não há efeito possível), com o evento `plan.refused` de
        motivo `acima_da_autonomia` e só as chaves das etapas (nada do texto do pedido)."""
        chaves = [s.key for s in com_efeito]
        texto = (f"O teto de autonomia desta execução é observar: o plano tem {len(chaves)} etapa(s) com efeito fora do "
                 f"aparelho ({', '.join(chaves)}), e nenhuma pode rodar. Nada foi feito.")
        self.repo.save_plan(run_id, plan)
        self.repo.decision(f"Recusado no planejamento (item 28.23): {texto}", run_id=run_id)
        self.repo.bus.emit("plan.refused", texto, level="warn", run_id=run_id,
                           data={"motivo": "acima_da_autonomia", "teto": "observar", "etapas": chaves})
        self.repo.set_run_status(run_id, RunStatus.failed, texto, level="warn", message=f"Execução {run_id}: {texto}")

    def _recusar_fora_do_catalogo(self, run_id: str, plan: Plan) -> None:
        """Item 31.33: o comando pede o que nenhuma ação do catálogo do app faz. Antes virava `needs_input` com uma
        pergunta ("Como devo fazer isso?") que a pessoa não tinha como responder: não há resposta que crie a ação. A
        execução termina recusada, sem etapa e sem pergunta, com o texto montado dos dados do catálogo (ADR-052): vale
        para qualquer app declarado, sem nome de app nem ação no código."""
        texto = " ".join(texto_fora_do_catalogo(f.pedido, f.app, f.disponiveis) for f in plan.fora_do_catalogo)
        self.repo.save_plan(run_id, plan)
        self.repo.decision(f"Recusado no planejamento (item 31.33): {texto}", run_id=run_id)
        self.repo.bus.emit("plan.refused", texto, level="warn", run_id=run_id,
                           data={"motivo": "sem_acao_do_catalogo",
                                 "pedidos": [f.model_dump() for f in plan.fora_do_catalogo]})
        self.repo.set_run_status(run_id, RunStatus.failed, texto, level="warn", message=f"Execução {run_id}: {texto}")

    def _recusar_no_planejamento(self, run_id: str, plan: Plan, recusadas: list[dict[str, object]]) -> None:
        """O plano inteiro sai sem etapas, com uma pergunta por etapa recusada: o caminho do `missing` (a execução
        vai a `needs_input`, nada é materializado, nenhuma decisão é gasta). Recusar só a etapa com efeito deixaria os
        preparativos rodarem à toa; um plano meio montado é pior que nenhum (a regra do `compose` e do 24.1).

        O evento `plan.refused` leva o motivo FECHADO de cada etapa (`MotivoForaDoCatalogo`) para o painel e a
        medição; a linha do tempo leva a mesma frase da porta do despacho."""
        partes = []
        for r in recusadas:
            estranha = f" (a ação {r['capability']} não é do catálogo dele)" if r["capability"] else ""
            frase = (f"a etapa '{r['title']}' tem efeito externo em {r['app']} sem a ação do catálogo{estranha} — ela "
                     "passaria por fora da política e dos limites do perfil")
            partes.append(frase)
            plan.missing.append(MissingInfo(
                field="policy", question=f"Recusado no planejamento: {frase}. Peça só o que o catálogo do app faz, ou "
                                         "faça esta parte você mesmo."))
        plan.steps = []
        texto = "Porta de política no planejamento (item 13.2): " + "; ".join(partes) + "."
        self.repo.decision(texto, run_id=run_id)
        self.repo.bus.emit("plan.refused", texto, level="warn", run_id=run_id,
                           data={"motivo": "efeito_fora_do_catalogo", "etapas": recusadas})

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

    def _medir_pergunta_sensivel(self, run_id: str, perguntas: list[dict[str, object]]) -> None:
        """29.52: o planejador que pede senha ou código erra (ADR-040: a senha mora na conta da persona); conta-se cada
        vez. O evento leva só a execução e o tipo, nunca a pergunta. O painel o lê para trocar a caixa de resposta
        pela orientação."""
        tipo = tipo_sensivel(perguntas)
        if tipo is not None:
            self.repo.bus.emit("pergunta_sensivel", f"Execução {run_id}: a pergunta pede credencial ({tipo}), que não "
                               "se responde por texto", level="warn", run_id=run_id, data={"tipo": tipo})

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
            self._medir_pergunta_sensivel(run_id, perguntas)
            repo.set_run_status(run_id, RunStatus.needs_input, texto, level="warn",
                                message=f"Execução {run_id}: faltam informações — {texto}")
            return
        problemas = [i.as_dict() for i in resolvida.issues]
        # I4 da validação do deploy 7: o texto da execução é para o dono, com o NOME da habilidade e o motivo sem o id
        # nem o código (`E_PLAN_INVALID: flow:…@1: …` saía cru, duas vezes). Quem desenvolve acha os dois no evento abaixo.
        # Polimento do Chrome do deploy 12: a frase diz o que a pessoa faz agora (responder ou pedir de novo), e não
        # "corrija a habilidade", que ela não faz pelo painel; o fluxo salvo se chama assim, a skill publicada não.
        motivos = "; ".join(dict.fromkeys(_motivo_para_o_dono(i.message, str(resolvida.ref)) for i in resolvida.issues))
        quem = "O fluxo salvo" if str(resolvida.ref).startswith("flow:") else "A habilidade salva"
        nome = f" “{resolvida.name}”" if resolvida.name else ""
        repo.bus.emit("log", f"Execução {run_id}: a habilidade {resolvida.ref} casou com o comando e não compilou",
                      level="warn", run_id=run_id, data={"skill": str(resolvida.ref), "issues": problemas})
        # o código de cada problema vai num campo próprio do `run.updated` (`issue_codes`), não no texto da tela
        repo.set_run_status(run_id, RunStatus.needs_input,
                            f"{quem}{nome} não serviu para este pedido: {motivos or 'ele não compilou'}. Responda à "
                            "pergunta ou peça de novo pelo painel.", level="warn",
                            message=f"Execução {run_id}: a habilidade {resolvida.ref} não compilou — corrija o comando "
                                    "ou a habilidade e tente de novo.",
                            dados={"issue_codes": list(dict.fromkeys(str(p["code"]) for p in problemas))})

    # ------------------------------------------------------------------ recursos declarativos (fase H)
    def _recursos(self) -> ResourceConvergence:
        """Os quatro providers só de LEITURA (sem `CommandBus`): o relatório e a foto não pedem comando nenhum."""
        return ResourceConvergence(resource_providers(
            self.repo.db, self.devices.devices,
            session_max_age_s=int(self.scheduler.cfg.file.contas.session_max_age_s),
            unknown_retry_cap=int(self.scheduler.get_settings().session_unknown_retry_cap)))

    def _pos_do_catalogo(self, run_id: str, plan: Plan) -> None:
        """31.50 (a): o plano de fluxo salvo prova as etapas de catálogo com a pós-condição ATUAL do catálogo."""
        if plan.planner.provider != "fluxo":
            return
        if trocadas := atualizar_pos_condicoes(plan.steps, plan.app_package):
            self.repo.decision(f"Plano salvo: a prova de {', '.join(trocadas)} segue o catálogo atual do app "
                               "(31.50 a), não a pós-condição gravada no fluxo.", run_id=run_id)

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

    def start(self, run_id: str, *, por: str = SISTEMA) -> RunSummary:
        """`por`: quem iniciou, no `data.iniciada_por` do `run.updated` (P12, 03/10). A rota passa a pessoa da sessão (ou
        `panel`); o início automático do `mode=execute` depois do plano fica com `sistema`. A prévia (`mode=plan`) só
        executa por esse início explícito: medido em 03/10, as 11 prévias que gastaram decisões tinham sido iniciadas 12 a
        44 s depois de criadas, e sem o autor no evento não dava para separar isso de uma prévia que executasse sozinha."""
        run = self._run(run_id)
        if run["status"] not in (RunStatus.planned.value, RunStatus.planning.value):
            raise RunError("invalid_state", f"A execução está em '{run['status']}' e não pode ser iniciada.")
        if not self.repo.db.scalar("SELECT COUNT(*) FROM objectives WHERE run_id=?", (run_id,)):
            raise RunError("no_plan", "A execução ainda não tem plano materializado.")
        if not self.repo.set_run_status(run_id, RunStatus.running, None, message=f"Execução {run_id} iniciada",
                                        dados={"iniciada_por": por}, so_se=(RunStatus.planned, RunStatus.planning)):
            # Outro gesto chegou antes (o cancelamento do canal, ou outro início): a leitura acima já não vale.
            raise RunError("invalid_state", "A execução mudou de estado e não pode ser iniciada.")
        # O MESMO pré-voo da criação, agora item a item: um plano pronto pode ficar dias parado, e o que estava
        # apto na criação pode não estar mais. O motivo específico ("é de outra máquina e está stopped", "o
        # servidor está em manutenção", "a entrega do app falhou") substitui o antigo "Aparelho offline", que
        # mandava o operador ligar um aparelho quando o problema era outro.
        alvos = list(self.repo.db.query("SELECT * FROM objectives WHERE run_id=?", (run_id,)))
        _perfis, comando, _perguntas = self._perfis_da_execucao(run)
        # No início o plano existe: os apps da tarefa são os DELE (item 24.5), não uma leitura do texto.
        plano = Plan.model_validate_json(run["plan"]) if run["plan"] else None
        impedidos = self.pre_voo([o["instance_id"] for o in alvos], ao_iniciar=True,
                                 secret_names=self._segredos_exigidos(comando),
                                 perfis={o["instance_id"]: o["profile_id"] for o in alvos},
                                 app_ids=(plano.required_apps or apps_do_plano(plano)) if plano else ())
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

    def cancel(self, run_id: str, *, por: str | None = None, so_se_planejada: bool = False) -> RunSummary | None:
        """`por`: quem fez o GESTO (a rota passa o autor da sessão). Só ele vira o sinal `cancelou_execucao`
        (ADR-054); o cancelamento que a sucessora faz da execução respondida chama sem `por` — é consequência da
        resposta, que já tem o seu sinal.

        Um sinal por EPISÓDIO: só o gesto que abre o cancelamento o grava. O clique repetido — da mesma pessoa ou de
        outra — com o cancelamento já valendo não grava: a execução em `cancelling`, ou assentada em
        `completed_with_issues` com `cancel_requested` (o item falho ou incerto, que `_finish_cancel` não fecha).
        `running` ou `paused` com `cancel_requested` só existem depois de uma REABERTURA (resolver ou repetir um item
        de execução cancelada): cancelar ali é outro episódio.

        `so_se_planejada` (28.27, o canal que abandona a prévia): só cancela a execução que ainda está `planned`, e a
        conferência é o próprio `UPDATE` que marca o pedido (compare-and-set), não uma leitura antes. Se outro gesto já
        a iniciou, nada muda e devolve `None`; e o início que chega depois da marca é recusado (`start`, `so_se`)."""
        run = self._run(run_id)
        status = RunStatus(run["status"])
        if so_se_planejada:
            # Marca e fecho na MESMA transação: uma marca sem o fecho (queda no meio) deixaria `planned` com
            # `cancel_requested`, que nada além do Cancelar do painel destrava (o `start` e a porta recusam).
            with self.repo.db.tx():
                cur = self.repo.db.execute("UPDATE runs SET cancel_requested=1, pause_requested=0 "
                                           "WHERE id=? AND status=? AND cancel_requested=0",
                                           (run_id, RunStatus.planned.value))
                if cur.rowcount != 1:
                    return None
                self._cancelar_antes_de_iniciar(run_id, "Cancelada antes de iniciar")
            if por is not None:  # a marca provou `planned` sem pedido anterior: episódio novo
                avisar(self.costuras.cancelou_execucao, CancelamentoDeExecucao(
                    run_id=run_id, status_anterior=RunStatus.planned.value, antes_de_iniciar=True, quem=por,
                    em=now_iso()))
            return self.repo.run_summary(self._run(run_id))
        # `completed_with_issues` (item incerto) e `awaiting_person` (29.93, item esperando gesto) ainda têm o que fechar.
        if status in RUN_TERMINAL and status != RunStatus.completed_with_issues:
            raise RunError("invalid_state", "A execução já terminou.")
        episodio_novo = status != RunStatus.cancelling and (
            not run["cancel_requested"] or status in (RunStatus.running, RunStatus.paused))
        em = now_iso()
        self.repo.db.execute("UPDATE runs SET cancel_requested=1, pause_requested=0 WHERE id=?", (run_id,))
        antes_de_iniciar = status in (RunStatus.planned, RunStatus.needs_input, RunStatus.planning)
        if antes_de_iniciar:
            self._cancelar_antes_de_iniciar(run_id, "Cancelada antes de iniciar")
        else:
            self.repo.set_run_status(run_id, RunStatus.cancelling,
                                     "Cancelando: trabalho futuro interrompido; o que já foi feito permanece registrado",
                                     level="warn")
            self.scheduler.wake()
        if por is not None and episodio_novo:
            # Só depois de o gesto valer; o status é o de ANTES (o de agora já é consequência do pedido).
            avisar(self.costuras.cancelou_execucao, CancelamentoDeExecucao(
                run_id=run_id, status_anterior=status.value, antes_de_iniciar=antes_de_iniciar, quem=por, em=em))
        return self.repo.run_summary(self._run(run_id))

    def lembrar_antes_de_vencer(self, agora: datetime) -> list[str]:
        """31.50: UM lembrete por item que vence nas próximas `LEMBRETE_ANTES_H` horas: a execução em `needs_input` e
        o objetivo em `waiting_user` de execução terminada (as mesmas filas das duas varreduras acima). Antes, o item
        vencia sem aviso prévio e o dono só sabia depois.

        Sai o evento `pendencia.vence_em`; o texto é do montador dos avisos (28.31). Os `dados` dizem o que é
        (`aprovacao`, `objetivo` ou `execucao`), o aparelho, a ação de catálogo e a chave da etapa que espera, e o
        `vence_em`. Nunca o comando nem o título da etapa: os dois podem levar um nome ou um arroba. Devolve as chaves
        `vencimento:lembrete:<id>:<entrada na espera>` dos lembretes que saíram; a mesma espera não sai duas vezes (o
        evento é a marca)."""
        ligado, horas_cfg = self._vencimento()
        if not ligado:
            return []
        prazo, ligado_desde = timedelta(hours=horas_cfg), self._ligado_desde()
        janela = (to_iso(agora - prazo), to_iso(agora - prazo + timedelta(hours=LEMBRETE_ANTES_H)))
        db, saidos = self.repo.db, []
        itens: list[tuple[str, str, dict[str, object]]] = []
        for run in db.query("SELECT id, created_at, instance_ids FROM runs WHERE status=? ORDER BY created_at, id",
                            (RunStatus.needs_input.value,)):
            entrada = db.scalar("SELECT MAX(ts) FROM events WHERE run_id=? AND kind='run.updated'",
                                (run["id"],)) or run["created_at"]
            itens.append((str(run["id"]), max(str(entrada), ligado_desde),
                          {"o_que": "execucao", "run_id": str(run["id"]),
                           "aparelhos": loads(str(run["instance_ids"]), [])}))
        terminais = tuple(s.value for s in RUN_SEM_TRABALHO)      # 29.93: inclui a execução esperando a pessoa
        marcas = ",".join("?" for _ in terminais)
        for o in db.query("SELECT o.id, o.run_id, o.instance_id, o.blocked_kind, o.finished_at AS espera_desde, "
                          f"r.finished_at AS fim FROM objectives o JOIN runs r ON r.id=o.run_id WHERE o.status=? "
                          f"AND r.status IN ({marcas}) ORDER BY o.id", (ObjectiveStatus.waiting_user.value, *terminais)):
            etapa = db.one("SELECT key, capability FROM steps WHERE objective_id=? AND status=? "
                           "ORDER BY seq DESC, id DESC LIMIT 1", (o["id"], StepStatus.waiting_user.value))
            itens.append((str(o["id"]), max(str(o["espera_desde"] or ""), str(o["fim"] or ""), ligado_desde),
                          {"o_que": "aprovacao" if o["blocked_kind"] == "approval" else "objetivo",
                           "run_id": str(o["run_id"]), "objective_id": str(o["id"]), "aparelho": o["instance_id"],
                           "acao": etapa["capability"] if etapa else None, "etapa": _etapa_segura(etapa)}))
        for ref, desde, dados in itens:
            # Vence entre agora e agora + LEMBRETE_ANTES_H: `desde` dentro da janela correspondente.
            if not (janela[0] <= desde < janela[1]):
                continue
            # Uma vez por ESPERA, não por item: o objetivo retomado que volta a esperar ganha outro lembrete. A chave leva
            # a entrada na espera (`desde`), e só barra o lembrete emitido depois dela.
            chave = f"vencimento:lembrete:{ref}:{desde}"
            if db.scalar("SELECT 1 FROM events WHERE kind=? AND (objective_id=? OR (objective_id IS NULL AND run_id=?)) "
                         "AND ts >= ? LIMIT 1", (EVENTO_DO_LEMBRETE, ref, ref, desde)):
                continue
            vence_em = to_iso(parse_iso(desde) + prazo)
            self.repo.bus.emit(EVENTO_DO_LEMBRETE, f"Vence em {LEMBRETE_ANTES_H} h ou menos: {dados['o_que']} {ref}",
                               run_id=str(dados["run_id"]), objective_id=dados.get("objective_id"),  # type: ignore[arg-type]
                               instance_id=dados.get("aparelho"),  # type: ignore[arg-type]
                               data={**dados, "regra": "31.50", "chave": chave, "vence_em": vence_em,
                                     "acontece_se_vencer": "cancelado pelo sistema"})
            saidos.append(chave)
        return saidos

    def _prazo_para_os_dtos(self) -> tuple[float, str] | None:
        """31.50: o que o repositório precisa para o `vence_em`. Desligado, `None`. Só LÊ a marca: quem a grava é a
        volta do vencimento, para uma leitura de DTO nunca escrever no banco."""
        ligado, horas = self._vencimento()
        if not ligado:
            return None
        # A marca só muda quando a volta a grava ou apaga (neste processo, o líder): fica em memória, e uma lista com
        # N execuções não lê `settings` N vezes.
        # `""`: lida e ausente (a volta ainda não a gravou); o `vence_em` sai nulo, em vez de andar com o relógio.
        if self._marca_lida is None:
            marca = self.repo.db.scalar("SELECT value FROM settings WHERE key=?", (CHAVE_LIGADO_DESDE,))
            self._marca_lida = str(marca or "")
        return horas, self._marca_lida

    def _ligado_desde(self) -> str:
        """31.50, carência ao ligar: quando o vencimento foi visto ligado pela primeira vez (`settings`, durável entre
        reinícios). O relógio de cada espera conta a partir do mais tardio entre a entrada nela e esta marca. Antes, o
        relógio usava só marcas do passado, e ao ligar venceu de uma vez tudo o que já estava parado (21 objetivos na
        primeira volta do deploy 30, sem aviso). Desligar apaga a marca: ligar de novo dá a carência outra vez."""
        db = self.repo.db
        if not (marca := db.scalar("SELECT value FROM settings WHERE key=?", (CHAVE_LIGADO_DESDE,))):
            db.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                       (CHAVE_LIGADO_DESDE, now_iso()))
            marca = db.scalar("SELECT value FROM settings WHERE key=?", (CHAVE_LIGADO_DESDE,))
        self._marca_lida = str(marca)
        return str(marca)

    def _esquecer_ligado_desde(self) -> None:
        self.repo.db.execute("DELETE FROM settings WHERE key=?", (CHAVE_LIGADO_DESDE,))
        self._marca_lida = None

    def _cancelar_antes_de_iniciar(self, run_id: str, detalhe: str, *, message: str | None = None,
                                   dados: dict[str, object] | None = None) -> None:
        """Fecha como `cancelled` a execução que não começou: etapas abertas, objetivos e aprovações junto. Quem chama
        já gravou `cancel_requested`; o gesto da pessoa (`cancel`) e a expiração do sistema (29.50) diferem só no texto
        e no sinal."""
        self.repo.cancel_open_steps(run_id, reason="execução cancelada")
        for o in self.repo.db.query("SELECT id FROM objectives WHERE run_id=?", (run_id,)):
            self.repo.set_objective(o["id"], ObjectiveStatus.cancelled, detail="Cancelado antes de iniciar.")
            self.scheduler._expirar_aprovacoes(o["id"], "execução cancelada")  # noqa: SLF001
        self.repo.set_run_status(run_id, RunStatus.cancelled, detalhe, message=message, dados=dados)

    def _vencimento(self) -> tuple[bool, float]:
        """31.43: `execucao.vencimento_ligado` e `execucao.pergunta_vence_h`, lidos a cada volta (config editável)."""
        cfg = self.scheduler.cfg.file.execucao
        return cfg.vencimento_ligado, float(cfg.pergunta_vence_h)

    def expirar_sem_resposta(self, agora: datetime) -> list[str]:
        """29.50: cancela PELO SISTEMA a execução que espera resposta (`needs_input`) há `execucao.pergunta_vence_h` horas
        (padrão `NEEDS_INPUT_EXPIRA_H`). Devolve as execuções expiradas. Com `execucao.vencimento_ligado: false`, não faz nada.

        Não passa por `cancel`: sem `por`, sem o sinal `cancelou_execucao` (ninguém fez o gesto), e com o motivo humano
        no texto. A marca para máquina vai num campo próprio do `run.updated`, como os `issue_codes`: `expirada` (29.50, a
        que já existia) e `vencimento` (31.43, com a regra e o motivo `vencido_sem_resposta`).

        O relógio é a ENTRADA em `needs_input`, não a criação: o `run.updated` daquela transição. Ele não se perde,
        porque a retenção poupa todo evento de execução sem `finished_at`, e `needs_input` não tem `finished_at`. Como
        `needs_input` só sai para `cancelled`, o último `run.updated` é o da entrada; outro depois dele só atrasaria a
        expiração, nunca a adiantaria. Sem evento nenhum (execução anterior aos eventos), vale a criação."""
        ligado, horas_cfg = self._vencimento()
        if not ligado:
            self._esquecer_ligado_desde()
            return []
        horas = _horas(horas_cfg)
        limite = to_iso(agora - timedelta(hours=horas_cfg))
        if self._ligado_desde() >= limite:          # 31.50: ainda na carência de quando o vencimento foi ligado
            return []
        expiradas: list[str] = []
        # A entrada é depois da criação: quem nasceu depois do limite não pode ter vencido.
        for run in self.repo.db.query("SELECT id, created_at FROM runs WHERE status=? AND created_at < ? "
                                      "ORDER BY created_at", (RunStatus.needs_input.value, limite)):
            run_id = str(run["id"])
            entrada = self.repo.db.scalar("SELECT MAX(ts) FROM events WHERE run_id=? AND kind='run.updated'",
                                          (run_id,)) or run["created_at"]
            if str(entrada) >= limite:
                continue
            # Só se ainda espera: a resposta da pessoa (`ComandoAssistido.sucessora`) pode ter cancelado a execução
            # entre a leitura e aqui, e o "Respondida: continua na execução …" dela não pode virar "expirada".
            if not self.repo.db.execute("UPDATE runs SET cancel_requested=1, pause_requested=0 WHERE id=? AND status=?",
                                        (run_id, RunStatus.needs_input.value)).rowcount:
                continue
            self._cancelar_antes_de_iniciar(
                run_id, f"Sem resposta em {horas} h: a pergunta expirou e a execução foi encerrada pelo "
                        "sistema. Para seguir, faça o pedido de novo.",
                message=f"Execução {run_id}: ninguém respondeu às perguntas em {horas} h; encerrada "
                        "pelo sistema",
                dados={"expirada": {"motivo": "sem_resposta", "horas": horas, "desde": str(entrada)},
                       "vencimento": {"regra": REGRA_DO_VENCIMENTO, "motivo": MOTIVO_VENCIDO, "horas": horas,
                                      "desde": str(entrada)}})
            expiradas.append(run_id)
        return expiradas

    def vencer_objetivos_parados(self, agora: datetime) -> list[str]:
        """31.43: fecha PELO SISTEMA o objetivo em `waiting_user` de execução JÁ TERMINADA que ninguém retomou em
        `execucao.pergunta_vence_h` horas. Devolve os objetivos vencidos. Com `execucao.vencimento_ligado: false`, não faz nada.

        Antes, `recompute_run` levava a execução a `completed_with_issues` "para permitir retomada", e o objetivo ficava
        `waiting_user` para sempre (22 assim no banco central em 04/10): pergunta que ninguém responde não some da fila.
        Só muda estado, como a 29.50: não responde, não digita, não toca o aparelho e não chama IA. Pedido de senha,
        desafio ou CAPTCHA também só são encerrados; nada é digitado. O objetivo vira `cancelled`, as etapas abertas e as
        aprovações pendentes dele também, e o `objective.updated` leva `vencimento` com a regra, o motivo e o relógio. Depois
        `recompute_run` deriva o status da execução (fica `completed_with_issues`: não é cancelamento pela pessoa, e por
        isso também não há o sinal `cancelou_execucao`).

        O relógio é o MAIS TARDIO entre a entrada do objetivo em `waiting_user` (`objectives.finished_at`, gravado nessa
        transição) e o fim da execução (`runs.finished_at`): nunca adianta o vencimento, e uma retomada de outro item da
        execução (que reabre e refecha a execução) reinicia o prazo.

        Corrida: a pessoa pode retomar o item (ou a execução) entre a leitura e a escrita. A escrita é condicional ao
        objetivo ainda em `waiting_user` E à execução ainda terminal; do contrário, a resposta dela vale e nada é tocado."""
        ligado, horas_cfg = self._vencimento()
        if not ligado:
            self._esquecer_ligado_desde()
            return []
        horas = _horas(horas_cfg)
        limite = to_iso(agora - timedelta(hours=horas_cfg))
        if self._ligado_desde() >= limite:          # 31.50: ainda na carência de quando o vencimento foi ligado
            return []
        terminais = tuple(s.value for s in RUN_SEM_TRABALHO)      # 29.93: inclui a execução esperando a pessoa
        marcas = ",".join("?" for _ in terminais)
        vencidos: list[str] = []
        candidatos = self.repo.db.query(
            "SELECT o.id, o.run_id, o.instance_id, o.finished_at AS espera_desde, r.finished_at AS fim_da_execucao "
            f"FROM objectives o JOIN runs r ON r.id=o.run_id WHERE o.status=? AND r.status IN ({marcas}) "
            "AND COALESCE(r.finished_at, o.finished_at) < ? ORDER BY o.id",
            (ObjectiveStatus.waiting_user.value, *terminais, limite))
        for o in candidatos:
            # `or ""` só para comparar: sem a marca da espera, vale a do fim da execução (e vice-versa, pelo SQL).
            desde = max(str(o["espera_desde"] or ""), str(o["fim_da_execucao"] or ""))
            if not desde or desde >= limite:
                continue
            oid, run_id = str(o["id"]), str(o["run_id"])
            try:
                # 31.50 (b): a guarda e o cancelamento numa transação só. Em comandos separados, uma retomada entre a
                # guarda e o `set_objective` era atropelada, e uma falha no meio deixava as etapas canceladas com o
                # objetivo ainda em `waiting_user`. A `tx()` é reentrante: o que os métodos abrem por dentro entra nesta.
                with self.repo.db.tx():
                    # A guarda de corrida: nenhum valor muda (`status=status`), só o `rowcount` diz se o objetivo ainda
                    # espera e a execução ainda está terminal. Mudar o status aqui desligaria a conferência de transição e
                    # a conta da espera do `set_objective` logo abaixo.
                    if not self.repo.db.execute(
                            "UPDATE objectives SET status=status WHERE id=? AND status=? AND EXISTS "
                            f"(SELECT 1 FROM runs WHERE id=? AND status IN ({marcas}))",
                            (oid, ObjectiveStatus.waiting_user.value, run_id, *terminais)).rowcount:
                        continue
                    motivo = f"vencido sem resposta em {horas} h"
                    self.repo.cancel_open_steps(run_id, objective_id=oid, reason=f"pedido {motivo}")
                    self.repo.set_objective(
                        oid, ObjectiveStatus.cancelled,
                        detail=f"Sem resposta em {horas} h: o pedido venceu e foi encerrado pelo sistema. "
                               "Para seguir, faça o pedido de novo.",
                        message=f"{o['instance_id']}: pedido sem resposta em {horas} h; encerrado pelo sistema",
                        dados={"vencimento": {"regra": REGRA_DO_VENCIMENTO, "motivo": MOTIVO_VENCIDO, "horas": horas,
                                              "desde": desde}})
                    self.scheduler._expirar_aprovacoes(oid, f"pedido {motivo}")  # noqa: SLF001
                    self.repo.recompute_run(run_id)
                self._soltar_aviso_do_aparelho(str(o["instance_id"]))
                vencidos.append(oid)
            except Exception:  # noqa: BLE001 - um objetivo ruim não pode prender os outros
                log.exception("vencimento do objetivo %s", oid)
        return vencidos

    def _soltar_aviso_do_aparelho(self, instance_id: str) -> None:
        """Tira do aparelho o "Bloqueado: ..." que o objetivo vencido deixou, se NENHUM outro objetivo dele ainda espera
        uma pessoa. Outro aviso (pressão, conectividade, resultado incerto) não é tocado."""
        rt = self.devices.devices.get(instance_id)
        if rt is None or not str(rt.attention or "").startswith("Bloqueado:"):
            return
        if self.repo.db.scalar("SELECT 1 FROM objectives WHERE instance_id=? AND status=? LIMIT 1",
                               (instance_id, ObjectiveStatus.waiting_user.value)):
            return
        rt.attention = None
        self.devices.publish(rt)

    # ------------------------------------------------------------------ retomadas
    def _requeue(self, obj: Any, reason: str) -> None:
        run = self._run(obj["run_id"])
        steps = self.scheduler.recovery_steps(run, obj["id"])
        if not steps:
            raise RunError("nothing_to_retry", "Não há etapas pendentes para refazer neste item.")
        if faltam := self.repo.faltas_do_replano(obj["id"], steps):
            raise RunError("dado_da_persona_ausente", "O plano usa dado da persona que o aparelho não tem ("
                           + ", ".join(faltam) + "): cadastre o dado na persona e peça de novo.")
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

    def retry_failed(self, run_id: str, *, por: str | None = None) -> dict[str, Any]:
        """`por`: quem fez o gesto (a rota passa o autor da sessão), levado ao sinal `repetiu_execucao`."""
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
            # Sinal `repetiu_execucao` (ADR-054): um por gesto — a identidade é o que foi retomado, em que versão.
            versoes = {r["id"]: r["plan_version"] for r in self.repo.db.query(
                f"SELECT id, plan_version FROM objectives WHERE id IN ({','.join('?' * len(retried))})",
                tuple(retried))}
            avisar(self.costuras.ao_repetir,
                   RepeticaoDeExecucao(run_id, tuple(f"{oid}@{versoes.get(oid, 0)}" for oid in retried), quem=por))
        return {"retried": retried, "skipped": skipped}

    def _print_da_confirmacao(self, obj: Row, etapa: Row, evidence_id: int | None) -> Row | None:
        """O print em que a pessoa se baseou para o "confirmar concluído"; `None` quando nenhum foi citado.

        ADR-055: em 19/09 o verificador de DM errou nos dois sentidos, e a saída de pessoa era uma nota livre que não
        dizia QUE tela foi vista (a DM da ciclana estava com "Sending…" congelado). Etapa com efeito externo só se
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

    def resolve(self, run_id: str, objective_id: str, body: ResolveBody, *, por: str | None = None) -> ObjectiveDTO:
        """`por`: quem decidiu (a rota passa o autor da sessão), levado ao sinal do gesto (ADR-054)."""
        self._run(run_id)
        try:
            obj = self.repo.objective_row(objective_id)
        except KeyError:
            raise RunError("not_found", "Objetivo não encontrado.", 404) from None
        if obj["run_id"] != run_id or obj["status"] not in ("waiting_user", "uncertain", "failed"):
            raise RunError("invalid_state", "Este item não está aguardando decisão.")
        # A etapa que espera a decisão, lida ANTES de agir: "repetir" revisa o plano e ela deixa de ser a vigente.
        blocking = self.repo.db.one(
            "SELECT * FROM steps WHERE objective_id=? AND plan_version=? AND status IN ('uncertain','waiting_user','failed')"
            " ORDER BY seq LIMIT 1", (objective_id, obj["plan_version"]))
        note = f" Nota: {body.note}" if body.note else ""
        if body.resolution == "abandon":
            self.repo.cancel_open_steps(run_id, objective_id=objective_id, reason="abandonado pelo usuário")
            self.repo.set_objective(objective_id, ObjectiveStatus.failed, detail="Abandonado pelo usuário." + note,
                                    blocked_reason=obj["blocked_reason"])
            # Achado #109: abandonar o item que espera aprovação não pode deixar o pedido na fila "Aguardando aprovação".
            self.scheduler._expirar_aprovacoes(objective_id, "item abandonado pelo usuário")  # noqa: SLF001
        elif body.resolution == "retry":
            if blocking is not None and (loads(blocking["result"], {}) or {}).get("efeito_comprovado"):
                # 29.79 (d): o efeito saiu (comprovado) e só uma afirmação sobre ele ficou incerta: repetir faria o
                # efeito de novo (publicar duas vezes), que é pior do que a afirmação não confirmada.
                raise RunError("efeito_comprovado", "O efeito desta etapa já saiu e foi comprovado; repetir o faria de "
                                                    "novo. Confira no aparelho e confirme (com o print) ou abandone.")
            self._requeue(obj, "Usuário decidiu repetir este item." + note)
        elif obj["blocked_kind"] == "approval":
            # "Confirmar concluído" marcaria a etapa como feita SEM executar — e é justamente a etapa que espera
            # aprovação. A decisão aqui é outra: aprovar, editar ou rejeitar.
            raise RunError("needs_approval", "Este item aguarda aprovação: use Aprovar, Editar ou Rejeitar "
                                             "na tela de Aprovações.")
        else:  # confirm_done — vale como decisão do usuário, não como comprovação automática
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
        # Sinal do gesto (ADR-054): confirmar à mão, repetir ou abandonar, com a nota — só depois de o gesto valer.
        # `<versão do plano>.<seq>` da etapa que esperava: dois gestos no mesmo item nunca têm a mesma chave.
        avisar(self.costuras.ao_resolver, ResolucaoDeItem(
            run_id=run_id, objective_id=objective_id, resolucao=body.resolution,
            ordem=f"{obj['plan_version']}.{blocking['seq'] if blocking is not None else 0}", nota=body.note,
            step_id=str(blocking["id"]) if blocking is not None else None, quem=por))
        return self.repo.objective_dto(self.repo.objective_row(objective_id))

    # ------------------------------------------------------------------ relatório
    def report(self, run_id: str) -> dict[str, Any]:
        detail = self.repo.run_detail(run_id)
        if detail is None:
            raise RunError("not_found", "Execução não encontrada.", 404)
        per_instance = []
        # Item 24.3: o valor que uma etapa leu e outra usou, com a ORIGEM (etapa e app) — é o que diz de onde veio o
        # alvo de uma ação. Só dado comum: código, senha e token nunca chegam à tabela (triagem do executor).
        lidos: dict[str, list[dict[str, str | int | None]]] = {}
        for v in self.repo.saidas_da_execucao(run_id):
            lidos.setdefault(str(v["objective_id"]), []).append(
                {k: v[k] for k in ("name", "value", "value_kind", "step_title", "app", "read_at", "origem", "leitor",
                                   "frame_sha256", "evidence_id")})
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
                "ai_tokens": o.ai_input_tokens + o.ai_output_tokens, "values_read": lidos.get(o.id, [])})
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
        if any(p["values_read"] for p in per_instance):
            md += ["", "## Valores lidos entre etapas", "", "| Instância | Valor | Tipo | Lido na etapa | App |",
                   "|---|---|---|---|---|"]
            md += [f"| {p['instance_id']} | {v['name']} = {_celula(v['value'])} | {v['value_kind']} | "
                   f"{_celula(v['step_title'])} | {_celula(v['app'] or 'app do plano')} |"
                   for p in per_instance for v in p["values_read"]]
            # Item 12.5: o valor lido da IMAGEM diz como foi conferido, em frase fixa (sem o texto do recorte).
            visuais = [(p["instance_id"], v) for p in per_instance for v in p["values_read"] if v.get("origem") == "visual"]
            if visuais:
                md += [""] + [f"- {i} · {v['name']}: lido da imagem; conferido às cegas por {_celula(str(v['leitor']))} "
                              f"no recorte da captura {str(v['frame_sha256'] or '')[:8]}" for i, v in visuais]
        md += ["", "Somente itens com SUCESSO comprovado contam como concluídos. Itens bloqueados, incertos, "
                   "cancelados ou não iniciados NÃO contam como sucesso."]
        return {"run": RunSummary(**detail.model_dump(include=set(RunSummary.model_fields))).model_dump(mode="json"),
                "totals": totals, "per_instance": per_instance, "untested": untested, "markdown": "\n".join(md)}
