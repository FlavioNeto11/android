"""Modo Automático do Comando (ADR-050): quem faz e onde, a partir do pedido.

`POST /api/runs/targets/suggest` não cria nada. Devolve os alvos que a execução usaria (com a origem de cada um e o
MOTIVO de cada persona), as descartadas, as que não dá para avaliar sem enriquecer, e o alerta de conduta quando o
pedido não deve ser roteado. O painel mostra isso e, confirmado, cria a execução ecoando os alvos em `targets` —
o mesmo caminho da prévia por persona (ADR-044).

Caminhos, do mais barato ao pago:

1. **o texto já diz quem ou onde** ("peça para o Fulano…", "no android-03"): a prévia de sempre, sem IA;
2. **nenhuma persona serve aos apps** (ex.: QA Messenger, que não usa conta): distribuição pela carga dos servidores
   (`balanceamento`), sem IA;
3. **há personas candidatas**: uma chamada do papel `plan` escolhe quais e quantas pelo perfil (orquestração, no
   domínio) e o `resolver_alvos` põe cada uma no aparelho dela (sessão pronta → principal → balanceamento).

Os apps do pedido são um CONJUNTO (item 24.5): candidata é a persona que tem, no mesmo aparelho, conta em todos os
apps de conta dele ("leia no Outlook e curta no Instagram" pede as duas contas).
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import ResolvedTargetDTO, RunTargetsResolveBody, alinhar_app_ids
from ..modules.execution.application.alvos import Mundo, Resolucao
from ..modules.execution.application.target_extractor import TargetExtractor
from ..modules.execution.domain.orquestracao import (MAX_CANDIDATAS, CartaoDePersona, OrquestracaoInvalida,
                                                     OrquestracaoOut, PedidoDeOrquestracao, normalizar)
from ..modules.identity.domain.persona import CRENCAS_MINIMAS, lacunas_da_biografia, valor_no_caminho
from ..planning.catalog import capabilities_of
from ..planning.provider import AIError
from ..security.redaction import redact
from ..social.context import linhas_de_crencas, persona_dto
from ..social.repository import SocialRepository
from .service import RunError, RunService

log = logging.getLogger(__name__)

MENSAGEM_CREDENCIAL = ("O comando contém uma credencial (ex.: \"Senha: …\"). Ele iria ao provedor de IA: tire a senha. "
                       "Ela fica guardada na conta da persona, e a automação a digita de lá sem passar pela IA.")

#: ADR-085: o que a automação NÃO resolve sozinha e por isso descarta, com o motivo dito e o que fazer. Sem nome de
#: persona nem dado de conta: o painel mostra o nome ao lado.
MOTIVO_CONTA_BLOQUEADA = "conta bloqueada: ela saiu da plataforma e não recebe tarefa"
MOTIVO_SEM_SENHA = ("senha não guardada com consentimento: guarde a senha e dê o consentimento na ficha da persona "
                    "(a automação digita sem passar pela IA)")
MOTIVO_SEM_CONSENTIMENTO = ("senha guardada sem consentimento: dê o consentimento na ficha da persona para a automação "
                            "poder digitá-la")
MOTIVO_SENHA_RECUSADA = "a senha guardada foi recusada pelo app: atualize a senha na ficha da persona"

_APARELHOS_NO_TEXTO = re.compile(r"\b(\d{1,2})\s+(?:aparelhos?|celulares?|dispositivos?|emuladores?)\b", re.I)
#: O que do perfil vai ao cartão (caminho na biografia → rótulo), além de idade, gênero, resumo e voz.
_BIO_DO_CARTAO: tuple[tuple[str, str], ...] = (
    ("home.city", "cidade"), ("work.profession", "profissão"), ("work.education", "formação"),
    ("tastes.hobbies", "hobbies"), ("tastes.interests", "interesses"))
_VOZ_DO_CARTAO: tuple[tuple[str, str], ...] = (("personality", "personalidade"), ("tone", "tom"),
                                               ("interests", "interesses"))


class RunTargetsSuggestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=3, max_length=4000)
    #: O teto de verdade é o da instalação (`LimitsCfg.orquestracao_max_escolhidas`, J1), aplicado em `normalizar`;
    #: 64 é só o limite do formato (o mesmo dos alvos de `RunCreate`).
    max_personas: int = Field(default=3, ge=1, le=64)


class PersonaEscolhida(BaseModel):
    profile_id: str
    nome: str
    motivo: str
    aderencia: Literal["alta", "media", "baixa"]
    instance_id: str | None = None
    servidor: str | None = None
    #: O aviso de atenção do aparelho escolhido (convidado sob pressão…), para a pessoa ver ANTES de confirmar.
    atencao: str | None = None
    #: ADR-085: o que a AUTOMAÇÃO fará antes de agir ("vai ligar o aparelho android-02", "vai conferir a sessão no
    #: preparo"). Aviso do que vai acontecer, nunca pedido à pessoa. Vazio = nada a preparar.
    preparo: list[str] = Field(default_factory=list)


class PersonaDescartada(BaseModel):
    profile_id: str
    nome: str
    motivo: str


class PersonaNaoAvaliavel(BaseModel):
    profile_id: str
    nome: str
    falta: str


class RunTargetsSuggestion(BaseModel):
    """A sugestão do modo Automático. `modo`: `ia` (escolha pelo perfil), `texto` (o comando já dizia), `distribuir`
    (app sem conta, pela carga) ou `nenhuma` (não há o que sugerir — ver `perguntas`/`warnings`)."""
    modo: Literal["ia", "texto", "distribuir", "nenhuma"]
    app_id: str | None = None
    #: Contrato C5: o conjunto de apps do comando; `app_id` segue sendo o primeiro (`alinhar_app_ids`).
    app_ids: list[str] = Field(default_factory=list)
    targets: list[ResolvedTargetDTO] = Field(default_factory=list)
    escolhidas: list[PersonaEscolhida] = Field(default_factory=list)
    descartadas: list[PersonaDescartada] = Field(default_factory=list)
    nao_avaliaveis: list[PersonaNaoAvaliavel] = Field(default_factory=list)
    perguntas: list[str] = Field(default_factory=list)
    #: As perguntas do resolvedor (persona num aparelho com duas…), no formato da prévia.
    questions: list[dict[str, object]] = Field(default_factory=list)
    command_sem_destinos: str = ""
    resumo: str = ""
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _app_ids(self) -> "RunTargetsSuggestion":
        self.app_id, self.app_ids = alinhar_app_ids(self.app_id, self.app_ids)
        return self


class Orquestrador:
    def __init__(self, runs: RunService, social: SocialRepository):
        self.runs = runs
        self.social = social

    async def sugerir(self, body: RunTargetsSuggestBody) -> RunTargetsSuggestion:
        runs = self.runs
        comando = body.command.strip()
        if redact(comando) != comando:
            raise RunError("credencial_no_comando", MENSAGEM_CREDENCIAL, 409)
        # 1. O texto já diz quem ou onde: a prévia de sempre decide (e obriga a confirmação), sem IA.
        destinos = TargetExtractor(runs._catalogo()).extrair(comando)  # noqa: SLF001
        if not destinos.dicas.vazias:
            previa = runs.previa_de_alvos(RunTargetsResolveBody(command=comando))
            return RunTargetsSuggestion(
                modo="texto", targets=previa.targets, questions=previa.questions,
                app_ids=list(dict.fromkeys(a for t in previa.targets for a in t.app_ids)),
                command_sem_destinos=previa.command_sem_destinos,
                warnings=[*previa.warnings, *self._avisos_de_saude(previa.targets)],
                resumo="O próprio comando diz quem faz ou onde: confira os destinos antes de executar.",
                escolhidas=self._escolhidas_do_resolvedor(previa.targets, "citada no comando"))
        texto = destinos.command_sem_destinos
        # O CONJUNTO de apps do pedido (item 24.5): candidata é a persona com conta em todos os apps de conta dele.
        # Item 29.70: o app SEM conta citado sozinho ("No QA Messenger, leia …") também entra. Sem ele, os apps vinham
        # vazios, toda persona com aparelho apto virava candidata e a IA mandava a leitura de QA para o aparelho de
        # uma conta real. Com ele, nenhuma persona serve (ninguém tem conta no app de QA) e o caminho é o 2. O comando
        # que cita app de conta E app sem conta já vinha inteiro de `_app_do_comando`: segue por persona.
        apps = runs._app_do_comando(texto, []) or runs._apps_citados(texto)  # noqa: SLF001
        mundo = runs._mundo(apps)  # noqa: SLF001
        candidatas = self._candidatas(mundo, apps)
        # ADR-085: só o impossível sai, por código e com o motivo dito (não é opinião do modelo).
        impossiveis = self._impossiveis(candidatas, mundo, apps)
        if not candidatas:
            vazia = self._sem_persona(texto, apps, mundo)
            vazia.descartadas = self._descartadas_por_codigo(impossiveis, mundo)
            return vazia
        status = runs.provider.status()
        if not status.configured:
            raise RunError("ai_not_configured", status.notice, 503)
        limites = runs.scheduler.get_settings()
        max_candidatas = int(limites.orquestracao_max_candidatas)
        cartoes = self._cartoes(candidatas, mundo, apps, max_candidatas=max_candidatas)
        pedido = PedidoDeOrquestracao(command=texto, app=", ".join(apps) or None, cartoes=cartoes,
                                      max_personas=body.max_personas, max_candidatas=max_candidatas,
                                      teto_escolhidas=int(limites.orquestracao_max_escolhidas))
        try:
            bruto, usage = await runs.provider.orchestrate_targets(pedido)  # type: ignore[attr-defined]
        except AIError as exc:
            raise RunError("ai_error", f"Não foi possível escolher quem faz agora: {exc}", 503,
                           {"kind": exc.kind, "retryable": exc.retryable}) from exc
        try:
            runs.repo.add_usage(None, None, usage)
        except Exception:  # noqa: BLE001 - contabilizar nunca derruba a resposta que já custou
            log.exception("não foi possível registrar o custo da orquestração")
        try:
            out = normalizar(bruto, pedido)
        except OrquestracaoInvalida as exc:
            raise RunError("ai_error", str(exc), 503, {"kind": "invalid_output", "retryable": True}) from exc
        return self._montar(out, texto, apps, mundo, impossiveis)

    # ------------------------------------------------------------------ montagem
    def _montar(self, out: OrquestracaoOut, texto: str, apps: list[str], mundo: Mundo,
                impossiveis: dict[str, str] | None = None) -> RunTargetsSuggestion:
        nome = mundo.nome
        base = RunTargetsSuggestion(
            modo="ia", app_ids=apps, command_sem_destinos=texto, resumo=out.resumo, perguntas=out.perguntas,
            descartadas=[*self._descartadas_por_codigo(impossiveis or {}, mundo),
                         *(PersonaDescartada(profile_id=d.profile_id, nome=nome(d.profile_id), motivo=d.motivo)
                           for d in out.descartadas)],
            nao_avaliaveis=[PersonaNaoAvaliavel(profile_id=n.profile_id, nome=nome(n.profile_id), falta=n.falta)
                            for n in out.nao_avaliaveis])
        if not out.escolhidas:
            return base
        # 3b. ONDE: cada escolhida no aparelho dela, pelo resolvedor de sempre (a IA não escolhe aparelho).
        ids = [e.profile_id for e in out.escolhidas]
        resolucao, _ = self.runs._resolver(texto, [], ids, [], "one", com_texto=False)  # noqa: SLF001
        onde = self._onde(resolucao)
        base.targets = [ResolvedTargetDTO(instance_id=a.instance_id, profile_id=a.profile_id, app_id=a.app_id,
                                          app_ids=list(a.app_ids), origem=a.origem, motivo=a.motivo)
                        for a in resolucao.alvos]
        base.questions = [p.as_dict() for p in resolucao.perguntas]
        base.escolhidas = [PersonaEscolhida(profile_id=e.profile_id, nome=nome(e.profile_id), motivo=e.motivo,
                                            aderencia=e.aderencia,  # type: ignore[arg-type]
                                            instance_id=onde.get(e.profile_id, (None, None))[0],
                                            servidor=onde.get(e.profile_id, (None, None))[1],
                                            atencao=self._atencao(onde.get(e.profile_id, (None, None))[0]),
                                            preparo=self._preparo(e.profile_id, onde.get(e.profile_id, (None, None))[0],
                                                                  mundo, apps))
                           for e in out.escolhidas]
        base.warnings = self._avisos_de_saude(base.targets, nome)
        return base

    # ------------------------------------------------------------------ o que a automação resolve e o que não
    @staticmethod
    def _descartadas_por_codigo(impossiveis: dict[str, str], mundo: Mundo) -> list[PersonaDescartada]:
        return [PersonaDescartada(profile_id=pid, nome=mundo.nome(pid), motivo=motivo)
                for pid, motivo in impossiveis.items()]

    def _motivo_de_login(self, profile_id: str, app_id: str) -> str | None:
        """Por que a automação NÃO consegue entrar nesta conta (ADR-040), ou `None` se consegue: a senha tem de estar
        guardada, com o consentimento, e não recusada pelo app."""
        conta = self.social.account_by_app(profile_id, app_id)
        cred = self.social.account_credential_row(profile_id, str(conta["id"])) if conta is not None else None
        if cred is None:
            return MOTIVO_SEM_SENHA
        if not cred["consent_at"]:
            return MOTIVO_SEM_CONSENTIMENTO
        return MOTIVO_SENHA_RECUSADA if str(cred["status"] or "active") == "invalid" else None

    def _impossiveis(self, candidatas: dict[str, list[str]], mundo: Mundo, apps: list[str]) -> dict[str, str]:
        """ADR-085: tira de `candidatas` (no lugar) SÓ o que a automação não resolve sozinha, e devolve `{persona:
        motivo}`: conta bloqueada e app de login gerenciado sem senha guardada com consentimento onde a sessão ainda não
        está pronta. Aparelho desligado, sessão não conferida e app fechado NÃO entram: a execução os prepara."""
        motivos: dict[str, str] = {}
        pacote = self.runs._pacote_por_app(apps)  # noqa: SLF001
        gerenciados = [a for a in apps if capabilities_of(pacote.get(a)).session_provider]
        for pid in list(candidatas):
            linha = self.social.persona_row(pid)
            if linha is not None and str(linha["status"]) == "blocked":
                motivos[pid] = MOTIVO_CONTA_BLOQUEADA
                del candidatas[pid]
                continue
            falhas = [m for a in gerenciados if (m := self._motivo_de_login(pid, a))]
            if not falhas:
                continue
            # Sem como entrar sozinha, só serve o aparelho em que a sessão desse par já está pronta.
            prontos = [iid for iid in candidatas[pid] if (pid, iid) in mundo.sessoes_prontas]
            if prontos:
                candidatas[pid] = prontos
            else:
                motivos[pid] = falhas[0]
                del candidatas[pid]
        return motivos

    def _preparo(self, profile_id: str, instance_id: str | None, mundo: Mundo, apps: list[str]) -> list[str]:
        """As frases do que a automação fará antes de agir neste aparelho (ADR-085). Só descreve o estado real: aparelho
        fora de `online` e sessão do par ainda não pronta. Sem religamento automático, diz o contrário em vez de
        prometer."""
        if not instance_id:
            return []
        sched = self.runs.scheduler
        frases: list[str] = []
        cands = sched.candidatos_de([instance_id])
        cand = cands[0] if cands else None
        if cand is not None and not cand.ligado:
            if not cand.acordavel:
                frases.append(f"o aparelho {instance_id} está desligado e o religamento automático não o liga: ligue-o")
            else:
                servidor = sched.servidores().get(cand.servidor)
                if servidor is not None and servidor.vagas_livres <= 0:
                    frases.append(f"vai esperar vaga para ligar o aparelho {instance_id}")
                else:
                    frases.append(f"vai ligar o aparelho {instance_id}")
        pacote = self.runs._pacote_por_app(apps)  # noqa: SLF001
        gerenciado = any(capabilities_of(pacote.get(a)).session_provider for a in apps)
        if gerenciado and (profile_id, instance_id) not in mundo.sessoes_prontas:
            frases.append("vai conferir a sessão no preparo")
        return frases

    def _atencao(self, instance_id: str | None) -> str | None:
        """O aviso do cartão do aparelho, numa linha e com teto (vai ao modelo e à tela da sugestão)."""
        rt = self.runs.devices.devices.get(instance_id) if instance_id else None
        return _curto(rt.attention, 400) if rt is not None and rt.attention else None

    def _avisos_de_saude(self, alvos: Sequence[ResolvedTargetDTO],
                         nome: Callable[[str], str] | None = None) -> list[str]:
        """Um aviso por aparelho sugerido que está com atenção. O painel já mostra `warnings`: a pessoa vê a
        pressão do convidado ANTES de confirmar (r-20260928165254-e31953 foi para o android-06 sem esse aviso)."""
        avisos: list[str] = []
        for a in alvos:
            texto = self._atencao(a.instance_id)
            if texto is None:
                continue
            if nome is None:
                nome = self.runs._mundo().nome  # noqa: SLF001 - só monta o mundo quando há aviso
            quem = f" ({nome(a.profile_id)})" if a.profile_id else ""
            aviso = f"{a.instance_id}{quem}: {texto}"
            if aviso not in avisos:
                avisos.append(aviso)
        return avisos

    def _onde(self, resolucao: Resolucao) -> dict[str, tuple[str, str | None]]:
        sched = self.runs.scheduler
        servidores = sched.servidores()
        saida: dict[str, tuple[str, str | None]] = {}
        for a in resolucao.alvos:
            rt = self.runs.devices.devices.get(a.instance_id)
            srv = servidores.get(sched.servidor_de(rt)) if rt is not None else None
            if a.profile_id and a.profile_id not in saida:
                saida[a.profile_id] = (a.instance_id, srv.nome if srv is not None else None)
        return saida

    def _escolhidas_do_resolvedor(self, alvos: Sequence[ResolvedTargetDTO], motivo: str) -> list[PersonaEscolhida]:
        mundo_nomes = dict(self.runs._mundo().nomes)  # noqa: SLF001
        return [PersonaEscolhida(profile_id=a.profile_id, nome=mundo_nomes.get(a.profile_id, a.profile_id),
                                 motivo=motivo, aderencia="media", instance_id=a.instance_id,
                                 atencao=self._atencao(a.instance_id))
                for a in alvos if a.profile_id]

    def _sem_persona(self, texto: str, apps: list[str], mundo: Mundo) -> RunTargetsSuggestion:
        """2. Nenhuma persona serve: tarefa só de apps sem conta vai pela carga dos servidores; o resto pede decisão.

        Entre apps (item 24.5), "sem conta" é o conjunto inteiro: basta um app de conta (o Instagram ao lado do
        Chrome) para a tarefa precisar de uma persona. A distribuição de apps sem conta é a `previa_sem_conta` (item
        29.70): pelos aparelhos que têm os apps (principal ou sabidamente pronto), sem conta real logada antes; os
        alvos levam o conjunto."""
        de_conta = [a for a in apps if a not in mundo.sem_conta]
        if apps and not de_conta:
            m = _APARELHOS_NO_TEXTO.search(texto)
            quantos = max(1, min(64, int(m.group(1)))) if m else 1
            # 29.70: pelos aparelhos que têm o app, sem conta real logada antes (a prévia diz quando não deu).
            previa = self.runs.previa_sem_conta(quantos, apps)
            alvos = [ResolvedTargetDTO(instance_id=p.instance_id, app_ids=apps, origem="balanceamento")
                     for p in previa.picks]
            return RunTargetsSuggestion(
                modo="distribuir", app_ids=apps, command_sem_destinos=texto, targets=alvos,
                warnings=[*previa.reasons, *self._avisos_de_saude(alvos)],
                resumo=(f"Tarefa sem conta: {len(previa.picks)} aparelho(s) escolhido(s) pela carga dos servidores "
                        f"({', '.join(f'{k}: {v}' for k, v in previa.per_server.items()) or 'nenhum disponível'})."))
        if not apps:
            return RunTargetsSuggestion(
                modo="nenhuma", command_sem_destinos=texto,
                perguntas=["O sistema não identificou o app da tarefa e nenhuma persona tem aparelho disponível. Diga "
                           "o app no comando (ou use “Refinar com IA”), ou escolha manualmente."])
        if len(de_conta) == 1:
            aviso = (f"O app {de_conta[0]} exige conta, e nenhuma persona com conta nele tem aparelho disponível "
                     "agora.")
        else:
            aviso = (f"Os apps {', '.join(de_conta)} exigem conta, e nenhuma persona com conta em todos eles tem "
                     "aparelho disponível agora (o mesmo aparelho precisa servir às contas de todos).")
        return RunTargetsSuggestion(
            modo="nenhuma", app_ids=apps, command_sem_destinos=texto,
            warnings=[f"{aviso} Vincule uma persona a um aparelho (Personas → a pessoa → Aparelhos) ou aguarde "
                      "liberar."])

    # ------------------------------------------------------------------ candidatas e cartões
    @staticmethod
    def _candidatas(mundo: Mundo, apps: list[str]) -> dict[str, list[str]]:
        """Persona → aparelhos APTOS dela que servem ao pedido (sem app identificado, todos os aptos dela).

        Item 24.5: servir é ter, NAQUELE aparelho, conta em todos os apps de conta do pedido (`Mundo.serve`, pela
        união dos vínculos do par). A persona só com Instagram não é candidata a "leia no Outlook e curta no
        Instagram"; app sem conta (Chrome) não tira ninguém."""
        saida: dict[str, list[str]] = {}
        for v in mundo.vinculos:
            if v.profile_id in mundo.de_teste:
                continue                      # 31.314: a persona de TESTE nunca é sugerida; só quem a cita pelo nome a usa
            if v.instance_id not in mundo.aptos or (apps and not mundo.serve(v.profile_id, v.instance_id, apps)):
                continue
            lista = saida.setdefault(v.profile_id, [])
            if v.instance_id not in lista:
                lista.append(v.instance_id)
        return saida

    def _fila_por_persona(self) -> dict[str, int]:
        """Tarefas abertas por persona: só execução em andamento ou pausada. A planejada que ninguém iniciou não é
        fila — em r-20260928195344-02ee9e, `planned` de dias antes (fc383a, bd3d3f, e84d7c) contavam como trabalho e
        empurravam a persona para trás. Não se expira nada aqui: sugerir não muda estado."""
        return {str(r["profile_id"]): int(r["n"]) for r in self.runs.repo.db.query(
            "SELECT o.profile_id, COUNT(*) AS n FROM objectives o JOIN runs r ON r.id=o.run_id"
            " WHERE r.status IN ('running','paused') AND o.status IN ('pending','running')"
            " AND o.profile_id IS NOT NULL GROUP BY o.profile_id")}

    def _cartoes(self, candidatas: dict[str, list[str]], mundo: Mundo, apps: list[str], *,
                 max_candidatas: int = MAX_CANDIDATAS) -> list[CartaoDePersona]:
        sched = self.runs.scheduler
        com_trabalho = self.runs.repo.instances_with_open_work() | set(sched.workers)
        fila = self._fila_por_persona()
        cartoes: list[CartaoDePersona] = []
        for pid, aparelhos in candidatas.items():
            linha = self.social.persona_row(pid)
            if linha is None:
                continue
            pessoa = persona_dto(linha)
            bio = pessoa.biography.model_dump(exclude_none=True)
            voz = pessoa.traits.model_dump()
            perfil: list[str] = []
            ident = ", ".join(x for x in (f"{pessoa.age} anos" if pessoa.age else None, pessoa.gender) if x)
            if ident:
                perfil.append(f"identidade: {ident}")
            perfil += [f"{rotulo}: {_curto(valor_no_caminho(bio, caminho))}" for caminho, rotulo in _BIO_DO_CARTAO
                       if valor_no_caminho(bio, caminho)]
            perfil += [f"{rotulo}: {_curto(voz.get(campo))}" for campo, rotulo in _VOZ_DO_CARTAO if voz.get(campo)]
            if pessoa.summary:
                perfil.append(f"resumo: {_curto(pessoa.summary, 240)}")
            # As crenças como o modelo social as lê, sem a linha de conduta (ela vai uma vez, no sistema).
            perfil += [c for c in linhas_de_crencas(pessoa.biography.beliefs) if not c.startswith("conduta")]
            cands = sched.candidatos_de(aparelhos, com_trabalho=com_trabalho)
            ocupados = sum(1 for c in cands if c.ocupado)
            prontas = sum(1 for iid in aparelhos if (pid, iid) in mundo.sessoes_prontas)
            n_fila = fila.get(pid, 0)
            livre = ocupados < len(cands) and n_fila == 0
            # Saúde dos aparelhos aptos (o aviso do cartão: convidado sob pressão, sem internet…). A sugestão de
            # r-20260928165254-e31953 escolheu o android-06 saturado sem ver o aviso que o cartão dele já mostrava.
            atencoes = {iid: texto for iid in aparelhos if (texto := self._atencao(iid))}
            partes = [f"{len(aparelhos)} aparelho(s) com " + ("os apps" if len(apps) > 1 else "o app") if apps
                      else f"{len(aparelhos)} aparelho(s)",
                      "a automação liga o aparelho e confere a sessão se precisar"]
            if atencoes:
                partes.insert(0, f"atenção em {', '.join(atencoes)}")
            if n_fila:
                partes.append(f"{n_fila} tarefa(s) na fila")
            if ocupados:
                partes.append(f"{ocupados} ocupado(s)")
            cartoes.append(CartaoDePersona(
                profile_id=pid, nome=mundo.nome(pid), perfil=tuple(perfil),
                disponibilidade=("livre" if livre else "ocupada") + " · " + ", ".join(partes),
                livre=livre, sessao_pronta=prontas > 0, tarefas_na_fila=n_fila,
                sem_crencas=bool(lacunas_da_biografia(bio, CRENCAS_MINIMAS)),
                aparelho_saudavel=not atencoes,
                atencao=_curto("; ".join(f"{iid}: {texto}" for iid, texto in atencoes.items()), 600)))
        # A saúde ORDENA, não filtra: a de aparelho com aviso continua candidata, e a preferência é do orquestrador,
        # pelo cartão. Fica depois de `livre` para não mudar quem cabe no teto de candidatas por um aviso só.
        # `sessao_pronta` e `livre` só desempatam a ordem (ADR-085): nunca tiram ninguém.
        cartoes.sort(key=lambda c: (not c.livre, not c.aparelho_saudavel, not c.sessao_pronta, c.tarefas_na_fila,
                                    c.nome))
        return cartoes[:max_candidatas]


def _curto(valor: object, limite: int = 160) -> str:
    """Uma linha, sem quebra (uma quebra forjaria um campo novo no cartão) e com teto."""
    if isinstance(valor, (list, tuple)):
        valor = ", ".join(str(v) for v in valor)
    return " ".join(str(valor).split())[:limite]
