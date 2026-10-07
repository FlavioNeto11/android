"""Os portões do despacho (15.15 K, F5a): o que a etapa precisa passar ANTES de o gesto sair. Moravam em `AppState`
(`state.py`); aqui ficam juntos, sem mudar regra, motivo, evento nem ordem das chamadas:

* `_policy_gate`: a política e o limite da capability para o perfil (a quarta porta do despacho, a única que depende da
  etapa), com a exceção presa, o rascunho e o pedido de aprovação;
* `vereditos_da_porta`: a MESMA conta, só lendo (alimenta a prévia do plano em `porta_do_plano.py`);
* `_draft_gate` / `_ler_tela`: o texto escrito na voz do perfil e a leitura de tela que o alimenta;
* `_approval_gate` / `_sim_do_plano_nao_vale`: o pedido de aprovação e o consumo do sim do plano;
* `_registrar_leitura` e os auxiliares (`_mesmo_pedido_noutras_contas`, `_pacote_da_etapa`, `_alvo_da_conversa`).

O `AppState` guarda métodos finos com os mesmos nomes e assinaturas que delegam para cá (o scheduler recebe
`state._policy_gate`, a prévia chama `state.vereditos_da_porta`, e os testes chamam os dois). Este módulo não importa
`app.state` em tempo de execução (ciclo): o estado entra pelo construtor e `AppState` só aparece em `TYPE_CHECKING`.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from .db import Database, Row, loads
from .devices.manager import DeviceRuntime
from .modules.pedidos.domain import conhecimento_da_operacao as conhecimento_dominio
from .modules.pedidos.infrastructure.conhecimento_da_operacao import NAO_GRAVADOS, ConhecimentoDaOperacao, Fatos
from .modules.pedidos.infrastructure.contexto import contexto_do_pedido
from .modules.pedidos.infrastructure.pesquisa_da_operacao import PesquisaDaOperacao
from .planning.capabilities import (
    Capability,
    alvo_da_acao,
    capability_of,
    contraparte,
    efeito_fora_do_catalogo,
    texto_a_gerar,
)
from .planning.catalog import capabilities_of, screen_reader_of
from .social.approvals import (
    DICA_DA_RECUSA,
    Approval,
    definir_texto,
    guardar_rascunho,
    guardar_recusa,
    ler_rascunho,
    rascunho_fechado,
    textos_irmaos,
)
from .social.chave_da_aprovacao import chave_da_aprovacao, midia_da_etapa, texto_exato
from .social.policy import UMA_CONTA_POR_ALVO, Verdict
from .social.service import SocialError, thread_de_dm
from .util import now, parse_iso

if TYPE_CHECKING:
    from .automation.hierarchy import UiTree
    from .planning.capabilities import Capability
    from .planning.pesquisa import PesquisaBruta, PesquisaRequest
    from .state import AppState

log = logging.getLogger("poc")


# Teto para ler a tela antes de escrever. Curto porque é contexto opcional: a etapa seguinte observa a tela de
# qualquer jeito, e segurar o aparelho esperando uma sessão que está subindo custaria muito mais do que vale.
_TELA_TIMEOUT_S = 15.0


def _registrar_estagio(db: Database, run_id: str, estagio: str) -> None:
    """O estágio do agente na operação (`operacao_alvos.estagio`, 124, da Jev). Import tardio: a função é da camada de
    operações, e até ela chegar ao banco (ou fora de operação) não faz nada. Nunca derruba a escrita."""
    try:
        estagios = importlib.import_module("app.modules.operacoes.infrastructure.estagios")
    except ModuleNotFoundError:
        return
    try:
        estagios.registrar_estagio(db, run_id, estagio)
    except Exception:  # noqa: BLE001 - estágio é acompanhamento, não porta
        log.exception("o estágio %s da execução %s não foi registrado", estagio, run_id)


def _texto_ou_nada(valor: object) -> str | None:
    return None if valor is None else str(valor)


def _col_app(row: Any) -> str | None:
    """`steps.app_id` (item 12.1) quando existe na linha — a etapa pode rodar num app diferente do plano."""
    try:
        return row["app_id"]
    except (KeyError, IndexError, TypeError):
        return None


@dataclass(frozen=True, slots=True)
class PortaDaEtapa:
    """30.61: o que `AppState.vereditos_da_porta` decidiu sobre uma etapa, só lendo.

    `final`: a decisão já está tomada antes do `check` (`veredito` é a recusa, ou `None` = segue sem parar, etapa sem ação
    do catálogo). Senão `veredito` é o do `check` e os demais campos dizem o que a porta do despacho precisa para escrever
    (rascunho, exceção presa, pedido de aprovação) e o que a prévia mostra."""

    veredito: Verdict | None
    final: bool = True
    cap: Capability | None = None
    profile_id: str | None = None
    rt: DeviceRuntime | None = None
    pacote: str | None = None
    app_id: str | None = None
    #: O mesmo pedido desta execução a várias contas sobre o mesmo alvo: a confirmação exigida ('' quando não há).
    confirmacao: str = ""
    #: A porta do despacho grava a decisão da confirmação uma vez por etapa (sem pedido aberto ainda).
    registrar_confirmacao: bool = False
    teto: str | None = None

    @classmethod
    def fim(cls, veredito: Verdict | None) -> PortaDaEtapa:
        return cls(veredito=veredito, final=True)


class Portoes:
    """Os portões de uma instalação: leem e escrevem pelo estado que recebem (`repo`, `db`, `approvals`, `policies`...)."""

    def __init__(self, state: AppState):
        self._st = state
        self._conhecimento_cache: ConhecimentoDaOperacao | None = None
        self._pesquisa_cache: PesquisaDaOperacao | None = None
        self._tarefas_da_pesquisa: set[asyncio.Task[None]] = set()     # 31.169: referência viva até a tarefa acabar

    @property
    def _conhecimento(self) -> ConhecimentoDaOperacao:
        """prova30 A1: o conhecimento comum da operação. Fica aqui (e não no `AppState`) porque só a porta de escrita o
        usa; o banco é o mesmo do estado. Criado no primeiro uso."""
        if self._conhecimento_cache is None:
            self._conhecimento_cache = ConhecimentoDaOperacao(self._st.db)
        return self._conhecimento_cache

    @property
    def _pesquisa(self) -> PesquisaDaOperacao:
        """prova30 A2: a pesquisa externa da operação, com a configuração `ai.pesquisa` (desligada de fábrica)."""
        if self._pesquisa_cache is None:
            ai = self._st.cfg.file.ai
            self._pesquisa_cache = PesquisaDaOperacao(self._st.db, ai.pesquisa, ai.prices)
        return self._pesquisa_cache

    async def _policy_gate(self, obj: Any, srow: Any, run: Any) -> Any:
        """Quarta porta, e a única que depende da ETAPA: política e limite da capability para este perfil.

        Devolve `None` quando pode seguir. Etapa sem ação do catálogo num app SEM catálogo segue livre — o QA
        Messenger e o caminho livre seguem exatamente como antes, sozinhos ou num comando entre apps.

        Tudo aqui é pelo app DA ETAPA (item 24.2, ADR-058 decisão 2), o mesmo que a execução usa
        (`Scheduler._app_context`): num comando entre apps, a etapa do Outlook e a do Instagram são julgadas cada uma
        pelo seu app. A regra não muda; a pergunta passa a ser feita ao app certo.
        """
        porta = self.vereditos_da_porta(obj, srow, run)
        if porta.registrar_confirmacao:                      # uma vez por etapa, não a cada retomada
            self._st.repo.decision(f"{obj['instance_id']}: {porta.confirmacao}", run_id=obj["run_id"],
                               instance_id=obj["instance_id"], step_id=srow["id"])
        if porta.final:
            return porta.veredito
        veredito, cap, profile_id, rt = porta.veredito, porta.cap, porta.profile_id, porta.rt
        assert veredito is not None and cap is not None and profile_id is not None
        app_da_etapa_id, pacote, confirmacao, teto = porta.app_id, porta.pacote, porta.confirmacao, porta.teto
        self._st.excecoes.vencer()                    # 30.65: a exceção vencida sai com evento (o `check` já não a usa)
        if not veredito.allowed:
            return veredito
        if veredito.excecao is not None:
            # 30.65: o `check` só lê; quem escreve é a porta. A exceção fica presa a esta etapa até o efeito sair, e é
            # a única ligada a ela: é a que o executor reserva no commit. Se deixou de estar em aberto entre a leitura
            # e a escrita, não há exceção para a etapa, e a porta recusa.
            if not self._st.excecoes.prender(veredito.excecao, srow["id"]):
                return Verdict(allowed=False, policy=veredito.policy, counts=veredito.counts,
                               reason=f"a exceção {veredito.excecao} à regra de uma conta por alvo deixou de estar em "
                                      "aberto antes de ser presa a esta etapa (30.65): recusado, não adiado",
                               hint="Nada foi feito. Se ainda for o caso, crie outra exceção.")
        elif cap.side_effect:
            self._st.excecoes.soltar_da_etapa(srow["id"])    # 30.65: passou sem exceção; o commit não reserva nenhuma
        # O texto é escrito AQUI, com a persona deste perfil, antes de qualquer digitação e antes da aprovação —
        # senão a pessoa aprovaria um rascunho que não é o que vai ser enviado.
        parado = await self._draft_gate(obj, srow, cap, profile_id, rt=rt, pacote=pacote)
        if parado is not None:
            return parado
        srow = self._st.repo.step_row(srow["id"]) or srow          # relê: o texto pode ter acabado de entrar
        # 30.64 (revisão da fila, item 5): o `check` rodou antes do rascunho; a DM de texto gerado só agora tem o que
        # comparar. Repetir a mesma mensagem ao mesmo alvo passa por confirmação, mesmo com o perfil autônomo.
        repetida = self._st.policies.mensagem_repetida(profile_id, cap, self._st.repo.bindings_da_etapa(srow, profile_id),
                                                   app_id=app_da_etapa_id, step_id=srow["id"])
        # 31.53: com o texto escrito, a conta nossa que cita OUTRA conta do mesmo pedido entre personas passa por
        # aprovação. O texto literal o `check` já pegou (e o motivo está no `reason`); aqui é o texto gerado. Sem pedido,
        # `None` e nada muda.
        familia = contexto_do_pedido(self._st.db, obj["run_id"]) if cap.side_effect else None
        argumentos = self._st.repo.bindings_da_etapa(srow, profile_id)
        citada = self._st.policies.cita_a_familia(profile_id, cap, argumentos, familia)
        citada = citada if citada and citada not in (veredito.reason or "") else None
        # 31.53 (F2): a regra do objeto na família de novo, agora que o rascunho acabou. Daqui até o pedido gravado no
        # `_approval_gate` não há `await`: entre duas personas do pedido com a mesma imagem, a primeira a chegar aqui
        # grava o pedido e a segunda o vê e é recusada; nunca as duas publicam.
        mesmo = self._st.policies.mesmo_objeto_na_familia(profile_id, cap, argumentos,
                                                      counterparty=contraparte(cap, argumentos),
                                                      app_id=app_da_etapa_id, step_id=srow["id"], pedido=familia)
        if mesmo is not None and mesmo[0]:
            return Verdict(allowed=False, policy=veredito.policy, counts=veredito.counts, reason=mesmo[1], hint=mesmo[2])
        objeto_ambiguo = mesmo[1] if mesmo is not None and mesmo[1] not in (veredito.reason or "") else None
        # Aprovação por política, por DM fria (o porquê vem no `reason` do veredito que libera) ou pela confirmação
        # do mesmo pedido a várias contas — nenhum grupo nem perfil afrouxa as duas últimas.
        # 28.23: com o teto `preparar`, o efeito exige aprovação qualquer que seja a política da persona.
        pelo_teto = ("teto de autonomia preparar: o efeito precisa da sua aprovação"
                     if teto == "preparar" and cap.side_effect else "")
        if veredito.needs_approval or confirmacao or pelo_teto or repetida or citada or objeto_ambiguo:
            # O `check` já põe a repetição no `reason` quando o texto era conhecido antes do rascunho: sem este corte, o
            # cartão trazia a mesma frase duas vezes (revisão do 31.49).
            nova = repetida if repetida and repetida not in (veredito.reason or "") else ""
            motivo = "; ".join(m for m in (veredito.reason, confirmacao, pelo_teto, nova, citada, objeto_ambiguo) if m)
            # 30.65: a etapa que usa a exceção sempre pede decisão nova; o aprovado de outra versão não vale para ela.
            parada = self._approval_gate(obj, srow, cap, profile_id, motivo=motivo, excecao=veredito.excecao,
                                         pacote=pacote, app_id=app_da_etapa_id)
            if parada is not None:
                return parada
        if cap.side_effect:
            # 31.64 S1 (migração 110): a porta liberou o efeito. Na mesma passada sem `await` da regra do objeto na família
            # (acima): a irmã que chegar depois vê esta marca e é recusada, qualquer que seja a ordem das tomadas.
            self._st.social_repo.marcar_passou_a_porta(srow["id"])
        return None

    def vereditos_da_porta(self, obj: Row, srow: Row, run: Row) -> "PortaDaEtapa":
        """30.61: o que a porta do despacho decide sobre esta etapa, SÓ LENDO. É a mesma conta do `_policy_gate` (que a
        chama e depois escreve: decisão, exceção presa, rascunho, pedido de aprovação), e a da prévia do plano
        (`GET /runs/{id}/porta`), para as duas nunca divergirem. Não grava nada, não emite evento e não chama IA.

        Etapa sem ação do catálogo num app SEM catálogo segue livre — o QA Messenger e o caminho livre seguem exatamente
        como antes, sozinhos ou num comando entre apps. Tudo é pelo app DA ETAPA (item 24.2, ADR-058 decisão 2), o
        mesmo que a execução usa (`Scheduler._app_context`)."""
        capability = srow["capability"] if "capability" in srow.keys() else None
        rt = self._st.devices.devices.get(obj["instance_id"])
        app_da_etapa = self._st.scheduler._app_context(run, rt, _col_app(srow))[0] if rt else None  # noqa: SLF001
        pacote = app_da_etapa.package if app_da_etapa else None
        # Uma capability que o app DA ETAPA não tem conta como nenhuma. Antes, `cap is None` liberava a etapa com o
        # efeito que tivesse: uma chave inventada numa etapa do Instagram passava por fora de tudo abaixo.
        cap = capability_of(pacote, capability) if capability else None
        teto = run["teto_de_autonomia"] if "teto_de_autonomia" in run.keys() else None
        if teto == "preparar" and cap is None and (srow["side_effect"] or loads(srow["commit_guard"], [])):
            # 28.23: com o teto `preparar`, todo efeito pede aprovação; a etapa com efeito SEM ação do catálogo não tem
            # como pedir (a aprovação é da capability). Vale a mais restritiva: ela não roda sozinha.
            return PortaDaEtapa.fim(Verdict(
                allowed=False, policy="approval_required",
                reason="o teto de autonomia desta execução é preparar: o efeito precisa da sua aprovação, e esta etapa "
                       "não tem a ação do catálogo que a pediria",
                hint="Faça esta parte você mesmo, ou peça só o que o catálogo do app faz."))
        if cap is None:
            # Item 13.2: etapa com EFEITO externo sem ação do catálogo, num app que TEM catálogo, passaria por fora de
            # política, aprovação, limite e coordenação de frota (uma habilidade treinada, um plano livre que
            # atravessa apps ou a ação de outro app). Não passa: pede a ação do catálogo. A mesma regra recusa o plano
            # ANTES de ele virar etapa (RA-7, `RunService._plan`); aqui fica a trava do despacho.
            if efeito_fora_do_catalogo(bool(srow["side_effect"]), capability, pacote):
                nome = app_da_etapa.name if app_da_etapa and app_da_etapa.name else pacote
                estranha = f" (a ação {capability} não é do catálogo dele)" if capability else ""
                return PortaDaEtapa.fim(Verdict(
                    allowed=False, policy="manual_only",
                    reason=f"etapa com efeito externo em {nome} sem a ação do catálogo{estranha} — ela passaria por "
                           "fora da política e dos limites do perfil",
                    hint="Refaça a habilidade escolhendo a ação do catálogo desta etapa (ou replaneje)."))
            return PortaDaEtapa.fim(None)
        profile_id = obj["profile_id"] or self._st.social_repo.perfil_unico_da_instancia(obj["instance_id"])
        if not profile_id:
            # Sem perfil não há voz para escrever nem política para aprovar. Deixar passar seria pior do que
            # parecer: como o texto deixou de ser congelado no plano, a etapa chega ao ator SEM `content` e SEM a
            # guarda que dependia dele — o modelo inventaria a frase e publicaria, sem aval de ninguém. Antes
            # desta série o texto literal segurava esse caso; hoje quem segura é esta porta.
            if cap.needs_draft and texto_a_gerar(self._st.repo.bindings_da_etapa(srow)) is not None:
                return PortaDaEtapa.fim(Verdict(
                    allowed=False, policy=cap.default_policy,
                    reason="este aparelho não tem perfil vinculado: não há voz para escrever o texto desta etapa nem "
                           "política para aprová-lo",
                    hint="Vincule um perfil a este aparelho (ou peça o texto exato no comando, com “envie "
                         "exatamente…”) e retome o item."))
            return PortaDaEtapa.fim(None)
        # Alvo desta etapa, para a coordenação de frota (achado #114, ADR-055): o argumento que a AÇÃO declara no
        # catálogo (`Capability.counterparty`), normalizado. Antes era `username` cru — curtir e comentar não o têm,
        # e a porta de frota recebia `None` e liberava tudo; `@Ana` e `@ana` eram duas pessoas.
        bindings = self._st.repo.bindings_da_etapa(srow, profile_id)
        alvo = contraparte(cap, bindings)
        # O mesmo pedido, nesta execução, a outras contas sobre o mesmo alvo (o caso de 19/09: uma execução, sete
        # contas, uma pessoa). A porta de frota conta o que JÁ aconteceu; os objetivos irmãos chegam aqui juntos,
        # antes de qualquer um disparar, e passariam todos. Decide-se pela execução, de forma determinística.
        irmaos = self._mesmo_pedido_noutras_contas(obj, cap, profile_id, alvo)
        confirmacao = ""
        registrar_confirmacao = False
        if irmaos:
            contas = len({profile_id, *(dono for _o, _a, dono in irmaos)})
            escolhido_id, escolhido_aparelho = min([(obj["id"], obj["instance_id"]),
                                                    *((o, a) for o, a, _d in irmaos)])
            if cap.limit_bucket in UMA_CONTA_POR_ALVO and escolhido_id != obj["id"]:
                return PortaDaEtapa.fim(Verdict(
                    allowed=False, policy=cap.default_policy,
                    reason=(f"esta execução manda o mesmo pedido ({cap.key}) a {contas} contas sobre {alvo}; em "
                            "seguir, mensagem e comentário vale uma conta por alvo (ADR-055) — segue só a de "
                            f"{escolhido_aparelho}, e esta foi recusada"),
                    hint="Nada foi feito por esta conta. Para outro alvo, faça um pedido separado."))
            confirmacao = (f"confirmação exigida: esta execução manda o mesmo pedido ({cap.key}) a {contas} contas "
                           f"sobre {alvo}" + (" — só esta conta segue; as outras foram recusadas"
                                              if cap.limit_bucket in UMA_CONTA_POR_ALVO else ""))
            registrar_confirmacao = self._st.approvals.for_step(srow["id"]) is None
        # `package`: a política é do APP desta etapa (23.10) — SEND_MESSAGE do Instagram e o de outro catálogo são
        # escolhas diferentes do perfil.
        # 30.62: a execução que nasceu de um pedido entre personas leva a família dele; as personas da família contam
        # como UMA conta por alvo e a pessoa real sem conversa passa por aprovação. Sem pedido, `None` e nada muda.
        veredito = self._st.policies.check(profile_id, cap, run_id=obj["run_id"], counterparty=alvo,
                                       app_id=app_da_etapa.id if app_da_etapa else None, package=pacote,
                                       step_id=srow["id"],
                                       pedido=contexto_do_pedido(self._st.db, obj["run_id"]) if cap.side_effect else None,
                                       bindings=self._st.repo.bindings_da_etapa(srow, profile_id))
        return PortaDaEtapa(veredito=veredito, final=False, cap=cap, profile_id=profile_id, rt=rt, pacote=pacote,
                            app_id=app_da_etapa.id if app_da_etapa else None, confirmacao=confirmacao,
                            registrar_confirmacao=registrar_confirmacao, teto=teto)

    def _mesmo_pedido_noutras_contas(self, obj: Row, cap: Capability, profile_id: str,
                                     alvo: str | None) -> list[tuple[str, str, str]]:
        """`(objetivo, aparelho, perfil)` das OUTRAS contas desta execução com a mesma ação sobre o mesmo alvo.

        Só conta objetivo vivo (falhou ou foi cancelado não age mais), etapa da versão atual do plano dele e não
        cancelada, e alvo já concreto (uma cópia de `for_each` ainda com `{item}` não é alvo de ninguém). A mesma
        persona em dois aparelhos não é "outra conta": o aviso disso é da prévia de alvos."""
        if not alvo or not cap.side_effect or not cap.limit_bucket:
            return []
        linhas = self._st.db.query(
            "SELECT o.id AS objetivo, o.instance_id, o.profile_id, s.bindings FROM steps s"
            " JOIN objectives o ON o.id = s.objective_id"
            " WHERE s.run_id=? AND s.capability=? AND s.plan_version=o.plan_version AND s.status<>'cancelled'"
            " AND o.id<>? AND o.status NOT IN ('failed','cancelled')",
            (obj["run_id"], cap.key, obj["id"]))
        achados: dict[str, tuple[str, str]] = {}
        for r in linhas:
            dono = r["profile_id"] or self._st.social_repo.perfil_unico_da_instancia(r["instance_id"])
            # 31.113 F3: cada irmã com a persona do SEU objetivo; o mesmo marcador em duas personas não é o mesmo alvo.
            if dono and dono != profile_id and contraparte(cap, self._st.repo.bindings_da_etapa(r, str(dono))) == alvo:
                achados[str(r["objetivo"])] = (str(r["instance_id"]), str(dono))
        return sorted((o, a, d) for o, (a, d) in achados.items())

    def _registrar_leitura(self, obj: Any, step: Any, items: list[str]) -> None:
        """Uma etapa de leitura de conversa terminou: o que a outra pessoa disse entra no HISTÓRICO do perfil.

        É a metade que faltava do caminho de mensagem direta (achado #108). Antes desta porta, `READ_MESSAGES`
        lia a conversa, mostrava os itens na evidência da etapa e jogava tudo fora: nenhuma interação de entrada
        era gravada em perfil nenhum, e por isso `GET /api/instagram/profiles/{id}/memory` era `[]` nos oito.

        O alvo é quem a conversa ABRIU. `READ_MESSAGES` não tem `username` — a etapa que tem é a `OPEN_THREAD`
        de que ela depende, e é dali que o nome sai. Sem alvo não se grava nada: fala sem dono não tem a quem
        ser atribuída.

        Que capability é leitura de conversa é declaração do APP da etapa (`AppDefinition.conversation_reads`):
        a mesma chave noutro app não diz nada sobre fala de ninguém.
        """
        capability = getattr(step, "capability", None)
        if not capability or not items:
            return
        tipo = capabilities_of(self._pacote_da_etapa(obj, step)).conversation_read(capability)
        if not tipo:
            return
        profile_id = obj["profile_id"] or self._st.social_repo.perfil_unico_da_instancia(obj["instance_id"])
        if not profile_id:
            return
        alvo = self._alvo_da_conversa(obj, step)
        if not alvo:
            log.info("etapa %s leu %d mensagem(ns) sem alvo identificável: nada gravado", step.id, len(items))
            return
        gravadas = self._st.social.record_inbound(
            profile_id, texts=items, counterparty=alvo, type=tipo, run_id=obj["run_id"], objective_id=obj["id"],
            step_id=step.id, instance_id=obj["instance_id"],
            evidence=f"lida pela etapa '{step.title}' no aparelho {obj['instance_id']}")
        if gravadas:
            self._st.bus.emit("log", f"{obj['instance_id']}: {len(gravadas)} fala(s) de {alvo} entraram no histórico "
                                 f"do perfil", run_id=obj["run_id"], instance_id=obj["instance_id"],
                          objective_id=obj["id"])

    def _pacote_da_etapa(self, obj: Mapping[str, object], step: object) -> str | None:
        """O pacote do app desta etapa: o dela, senão o do plano, senão o do aparelho (`Scheduler._app_context`).
        Sem execução ou sem aparelho para perguntar, o app da conta do perfil — o único que grava fala hoje."""
        run = self._st.repo.run_row(str(obj["run_id"]))
        rt = self._st.devices.devices.get(str(obj["instance_id"]))
        if run is not None and rt is not None:
            try:
                app, _ = self._st.scheduler._app_context(run, rt, getattr(step, "app_id", None))  # noqa: SLF001
            except KeyError:
                app = None
            if app is not None and app.package:
                return app.package
        return self._st.social_repo.app_package

    def _alvo_da_conversa(self, obj: Any, step: Any) -> str | None:
        """De quem é a conversa que esta etapa leu: o `username` dela, ou o da etapa de que ela depende.

        A repetição sobre lista resolve `{item}` nos argumentos ANTES de a etapa rodar, então no banco o
        `username` já está concreto — não há `{item}` para expandir aqui.
        """
        proprio = (getattr(step, "bindings", None) or {}).get("username")
        if proprio:
            return str(proprio)
        for chave in (getattr(step, "depends_on", None) or []):
            row = self._st.db.one("SELECT objective_id, bindings FROM steps WHERE objective_id=? AND key=? ORDER BY"
                              " plan_version DESC LIMIT 1", (obj["id"], chave))
            alvo = self._st.repo.bindings_da_etapa(row).get("username") if row else None
            if alvo:
                return str(alvo)
        return None

    async def _draft_gate(self, obj: Any, srow: Any, cap: Any, profile_id: str, *, rt: Any = None,
                          pacote: str | None = None) -> Any:
        """Escreve o texto desta etapa na voz DESTE perfil, quando ele ainda não está fechado.

        O mesmo plano roda em vários aparelhos. Se o texto vier congelado do planejador, oito contas publicam a
        mesma frase — foi o que aconteceu em r-20260918181035-7bfa38. Aqui cada perfil escreve a sua versão a
        partir do briefing, com persona, memória e histórico próprios.
        """
        if not cap.needs_draft:
            return None
        bindings = self._st.repo.bindings_da_etapa(srow, profile_id)
        briefing = texto_a_gerar(bindings)
        if briefing is None:                                   # texto exato pedido no comando
            return None
        # Uma escrita por vez dentro desta execução: a lista de "não repita" é lida do que os irmãos JÁ
        # escreveram, e com todos gerando ao mesmo tempo todos leriam a lista vazia. O lock é por execução, então
        # aparelhos de execuções diferentes continuam escrevendo em paralelo — exceto numa operação (prova30 A1),
        # onde cada alvo é uma execução própria: ali a trava e a lista são da OPERAÇÃO.
        operacao_id = self._operacao_da_execucao(str(obj["run_id"]))
        async with self._st._draft_locks.setdefault(operacao_id or obj["run_id"], asyncio.Lock()):
            # Esta porta é atravessada de novo toda vez que o objetivo é retomado — e é exatamente o que acontece
            # depois de alguém aprovar. Sem esta marca, o gate reescrevia o texto: a pessoa lia e aprovava uma
            # frase, e o aparelho digitava outra, gerada depois. Rascunho guardado é rascunho fechado.
            #
            # O pedido de aprovação conta como a mesma prova, e é o que protege as etapas rascunhadas ANTES desta
            # coluna existir (`draft_meta` nulo): se existe pedido, aquele texto já foi mostrado a alguém.
            #
            # A conferência é feita DENTRO do lock: quem esperou na fila pode ter esperado justamente por si.
            #
            # 31.49: o sim dado no PLANO não conta como texto mostrado. Ele só existe para texto final (o briefing não
            # tem chave), então se a etapa chegou aqui com briefing, aquele sim não a cobre: pular a escrita deixaria
            # a etapa sem texto, e o pedido da execução sairia sem o que vai ser enviado.
            pedido = self._st.approvals.for_step(srow["id"])
            if rascunho_fechado(ler_rascunho(self._st.db, srow["id"])) or (pedido is not None
                                                                       and pedido.origem != "plano"):
                return None
            # O tipo de texto e as leituras de tela são do APP da etapa (o manifesto dele no registro de apps).
            tipo = capabilities_of(pacote).text_kind(cap.key) or "dm_initiate"
            leitor = screen_reader_of(pacote)
            # Quem recebe o texto é o alvo que a AÇÃO declara (ADR-055): no comentário, o autor da publicação.
            alvo = alvo_da_acao(cap, bindings)
            arvore = await self._ler_tela(rt, pacote)
            tela = leitor.visible_content(arvore) if leitor is not None and arvore is not None else ""
            fatos, leitura = await self._conhecimento_da_operacao(operacao_id, obj, srow, cap, arvore, tela, pacote,
                                                                  bindings)
            # Responder é diferente de comentar: aqui existe uma fala DIRIGIDA a esta conta, e é ela que fundamenta
            # tanto a resposta quanto o que o perfil passa a saber sobre a pessoa. Só deste bloco sai memória.
            recebido = (leitor.comment_of(arvore, alvo or "")
                        if leitor is not None and arvore is not None and tipo == "comment_reply" else "")
            fio = None
            if tipo == "dm_initiate":
                # O caminho de DM não tinha lado de "recebido": o que a pessoa respondia entrava como texto de
                # tela, não virava fala dela, e por isso nunca virava memória (achado #108). A fala vem de duas
                # fontes, nesta ordem de confiança: o que ESTÁ ESCRITO na conversa aberta com atribuição de autor,
                # e o que uma etapa de leitura já gravou neste fio e ainda não foi respondido.
                fio = thread_de_dm(alvo)
                recebido = (leitor.message_of(arvore, alvo or "")
                            if leitor is not None and arvore is not None else "") or \
                    self._st.social.last_incoming(profile_id, counterparty=alvo, thread_key=fio)
                if recebido:
                    tipo = "dm_reply"
            try:
                # `persist=False` de propósito: interação é TENTATIVA, e um rascunho não é. `pending` conta para o
                # limite ("uma ação que talvez tenha saído já mexeu com a conta"), então gravar aqui gastaria a
                # cota antes de digitar nada e contaria duas vezes o que fosse enviado — quem registra o efeito é
                # o commit.
                draft, _interacao = await self._st.social.draft_response(
                    profile_id, kind=tipo, brief=briefing, persist=False, incoming=recebido,
                    # O fio é o que traz a conversa ao prompt: sem ele, `<resumo_da_conversa>` nunca aparecia
                    # para quem estava escrevendo, por mais mensagens que já tivessem sido trocadas.
                    counterparty=alvo, screen=tela, thread_key=fio,
                    # A ação da etapa: é por ela (e pelo perfil) que a voz aprendida do dono entra (ADR-054).
                    capability=cap.key,
                    # Escrever é uma chamada de modelo DENTRO de uma execução: passa pelo mesmo caminho das
                    # outras, com limite de simultâneas, teto de orçamento conferido antes de gastar e custo
                    # lançado no objetivo certo.
                    runner=lambda f: self._st.scheduler.executor._ai(  # noqa: SLF001
                        obj["run_id"], obj["id"], f, step_id=srow["id"], role="social"),
                    avoid=textos_irmaos(self._st.db, obj["run_id"], srow["id"], operacao_id=operacao_id),
                    # O que a operação sabe em comum (a leitura do alvo e o que se consolidou): DADO citado, separado
                    # do contexto da persona. Vazio fora de operação.
                    fatos_da_operacao=fatos.texto if fatos is not None else "",
                    assunto_da_operacao=fatos.assunto if fatos is not None else "")
            except SocialError as exc:
                # Sem texto não se digita nada. Isso é espera por uma pessoa, não falha da etapa: o briefing
                # continua lá e uma nova tentativa pode gerar.
                # Cada motivo com a sua saída (achado #93, ponto 3): mandar conferir a chave quando o que acabou
                # foi o orçamento, ou quando o provedor RECUSOU por política (nada a ver com chave nem persona),
                # faria a pessoa procurar defeito onde não há e bater na mesma parede.
                dica = ("Aumente o orçamento de IA em Configuração (chamadas por objetivo ou tokens por execução) "
                       "e retome o item." if exc.code == "ai_budget" else
                       "O provedor recusou por política — repetir tende a dar o mesmo resultado. Reescreva a "
                       "intenção deste texto (ou o comando) e retome o item." if exc.code == "ai_refusal" else
                       "Confira o provedor de IA e a persona do perfil, e retome o item.")
                return Verdict(allowed=False, policy=cap.default_policy,
                               reason=f"não foi possível escrever o texto desta etapa: {exc}", hint=dica)
            if draft.refused or not (draft.content or "").strip():
                # O motivo da recusa vem antes da justificativa: é ele que diz o que mudar na intenção (ex.: o
                # texto atribuía um recado a um terceiro, ADR-055) — a justificativa só explica a escolha do texto.
                # 31.65: o motivo do modelo é texto livre dele e fica só na etapa; a dica viaja e é fixa.
                guardar_recusa(self._st.db, srow["id"], draft.refusal_reason or draft.rationale or "sem justificativa")
                return Verdict(allowed=False, policy=cap.default_policy,
                               reason="a persona se recusou a escrever este texto", hint=DICA_DA_RECUSA)
            # Texto e marca na MESMA transação: um crash entre os dois deixaria a etapa com texto novo e sem
            # marca, e a retomada geraria outro por cima — pago, e por cima do que já estava escrito.
            with self._st.db.tx():
                # 31.113 F3: o nome da persona no rascunho vira marcador só se a volta for exata (mesma caixa).
                definir_texto(self._st.db, srow["id"], self._st.repo.texto_reversivel(draft.content, obj["id"]) or draft.content)
                # O que o rascunho percebeu não cabe em `bindings` (que é prompt do ator) e morreria aqui.
                # Guardado na etapa, sobrevive à espera por aprovação e a um reinício, e o commit o anexa à
                # interação — é assim que `learn_from` finalmente tem o que aprender.
                guardar_rascunho(self._st.db, srow["id"], {
                    "memory_candidates": [c.model_dump() for c in draft.memory_candidates],
                    "rationale": draft.rationale or "",
                    # Fica registrado o que o perfil TINHA À VISTA ao escrever — é o que explica o texto depois,
                    # numa auditoria. Não vai para o histórico como fala de ninguém: é tela, não é conversa.
                    "screen_seen": tela[:400],
                    "incoming": recebido,
                    # Auditoria do conhecimento comum: quantos fatos entraram e como a leitura deste agente bateu com a
                    # da operação. Só contagem e palavra fixa: o texto dos fatos mora na memória da operação.
                    **({"fatos_da_operacao": {"quantos": fatos.quantos, "leitura": leitura,
                                              **({"assunto": True} if fatos.assunto else {}),
                                              **({"conhecimento_ids": NAO_GRAVADOS} if not fatos.ids_gravados
                                                 else {})}}
                       if fatos is not None else {}),
                })
        # 31.63: o evento diz SÓ que o texto foi escrito e o tamanho. O texto mora na etapa e no pedido de aprovação, onde
        # quem decide o vê; evento vai a painel, aviso e resumo, e rascunho não é dado de log.
        self._st.bus.emit("log", f"{obj['instance_id']}: texto escrito na voz do perfil ({len(draft.content)} caracteres)",
                      run_id=obj["run_id"], instance_id=obj["instance_id"], objective_id=obj["id"])
        return None

    def _operacao_da_execucao(self, run_id: str) -> str | None:
        try:
            return self._conhecimento.operacao_da_execucao(run_id)
        except Exception:  # noqa: BLE001 - sem saber a operação, escreve como execução avulsa (o caminho de antes)
            log.exception("não deu para ler a operação da execução %s", run_id)
            return None

    async def _conhecimento_da_operacao(self, operacao_id: str | None, obj: Row, srow: Row, cap: Capability,
                                        arvore: UiTree | None,
                                        tela: str, pacote: str | None,
                                        bindings: Mapping[str, object]) -> tuple[Fatos | None, str | None]:
        """A leitura do alvo entra na memória da operação (uma vez; as outras conferem), a pesquisa externa preenche a
        lacuna do assunto (uma vez por operação, prova30 A2) e os fatos voltam para o texto.

        Quem chama segura a trava da OPERAÇÃO: a 2ª execução já encontra a leitura e os fatos, e não pesquisa de novo.

        Conhecimento é contexto: qualquer falha aqui vira log e o texto sai como sairia sem operação. Tela sensível
        (campo de senha à vista) não é gravada como leitura."""
        if not operacao_id:
            return None, None
        run_id = str(obj["run_id"])
        leitura = None
        # O alvo é identificado pelo que a etapa procura (o autor e o trecho da legenda, herdados do OPEN_POST), não
        # pela tela inteira: a lista de comentários aberta muda a cada agente que comenta. E o que se guarda é o
        # recorte da publicação, nunca os comentários de terceiros.
        autor = str(bindings.get("post_author") or "") or None
        legenda = str(bindings.get("caption_contains") or "") or None
        legivel = bool(tela) and arvore is not None and not getattr(arvore, "sensitive", False)
        recorte = conhecimento_dominio.recorte_do_alvo(tela, autor=autor, legenda=legenda) if legivel else ""
        try:
            if recorte:
                leitura = self._conhecimento.registrar_leitura(
                    operacao_id, run_id=run_id, step_id=str(srow["id"]), agente=str(obj["id"]),
                    fonte=f"{pacote or 'app'} · {cap.key}", texto=recorte,
                    identidade=conhecimento_dominio.identidade_do_alvo(autor, legenda))
                if leitura is not None:
                    _registrar_estagio(self._st.db, run_id, "conteudo_lido")
            fatos = self._conhecimento.fatos(operacao_id, leitura=leitura)
        except Exception:  # noqa: BLE001 - ver acima
            log.exception("operação %s: o conhecimento comum não entrou no texto da execução %s", operacao_id, run_id)
            return None, leitura
        if fatos.quantos:
            _registrar_estagio(self._st.db, run_id, "conhecimento_recuperado")
            try:                     # `resultado.conhecimento_ids` do alvo (contrato da 124); nunca derruba a etapa
                self._conhecimento.marcar_conhecimento_usado(run_id, fatos.refs)
            except Exception:  # noqa: BLE001 - ver acima
                # Revisão do PR 480: o texto segue (a lista é auditoria, não o texto), mas a falha não fica só no log:
                # vai ao `draft_meta` da etapa, e o GET do aprendizado da operação a mostra em `avisos`.
                log.exception("operação %s: conhecimento_ids não gravados na execução %s", operacao_id, run_id)
                fatos = replace(fatos, ids_gravados=False)
        return fatos, leitura

    def agendar_pesquisa_da_operacao(self, operacao_id: str) -> asyncio.Task[None] | None:
        """31.169: a pesquisa externa da operação roda UMA vez, ao criá-la, antes de qualquer alvo. A tarefa pega a trava da
        operação (a mesma da porta de escrita) no próximo giro do laço, e o primeiro alvo só chega à escrita segundos
        depois: quando chega, a lacuna já foi coberta e ele só reusa. Os alvos não pesquisam mais. `None` quando não há o
        que agendar (pesquisa desligada ou provedor sem a ferramenta)."""
        if not self._pesquisa.cfg.enabled or not hasattr(self._st.provider, "pesquisar"):
            return None
        trava = self._st._draft_locks.setdefault(operacao_id, asyncio.Lock())  # noqa: SLF001
        tarefa = asyncio.get_running_loop().create_task(self._pesquisar_na_criacao(operacao_id, trava))
        self._tarefas_da_pesquisa.add(tarefa)
        tarefa.add_done_callback(self._tarefas_da_pesquisa.discard)
        return tarefa

    async def _pesquisar_na_criacao(self, operacao_id: str, trava: asyncio.Lock) -> None:
        """Pelo caminho de IA da execução do PRIMEIRO alvo (tetos, vaga e custo no run dele, que é o que
        `custo.pesquisa_usd` da operação soma), sem objetivo e sem contexto de tela: a consulta leva só o assunto e as
        fontes indicadas. Sem alvo com execução, não pesquisa (ninguém usaria e o custo não teria onde morar)."""
        async with trava:
            run_id = self._st.db.scalar("SELECT run_id FROM operacao_alvos WHERE operacao_id=? AND run_id IS NOT NULL"
                                        " ORDER BY seq, profile_id LIMIT 1", (operacao_id,))
            if not run_id:
                log.info("operação %s: sem alvo com execução; a pesquisa não roda", operacao_id)
                return
            executor = self._st.scheduler.executor
            provedor = self._st.provider

            async def chamar(req: PesquisaRequest) -> PesquisaBruta:
                return await executor._ai(str(run_id), None, lambda: provedor.pesquisar(req),  # noqa: SLF001
                                          role="plan")

            try:
                feito = await self._pesquisa.pesquisar_se_preciso(operacao_id, run_id=str(run_id), contexto="",
                                                                  chamar=chamar)
            except Exception:  # noqa: BLE001 - pesquisa é contexto: nunca derruba a operação
                log.exception("operação %s: a pesquisa na criação falhou", operacao_id)
                return
            if feito is not None:
                # Só contagens: os fatos e as fontes moram na memória da operação.
                self._st.bus.emit("log", f"operação {operacao_id}: pesquisa na criação: {feito.fatos} fato(s) "
                                         f"({feito.confirmados} confirmado(s)), {feito.fontes} fonte(s), "
                                         f"{feito.buscas} busca(s)", run_id=str(run_id))

    async def _ler_tela(self, rt: Any, pacote: str | None) -> Any:
        """O que está escrito na tela do aparelho agora — para o texto falar do que está ali.

        Nunca é obrigatório: se o aparelho não responder, se a sessão de automação não estiver de pé ou se a tela
        for de outro app, o rascunho segue sem ela. Ler a tela é bônus de contexto, não porta.

        O teto de tempo é próprio e curto de propósito: `ensure_automation` espera até 240 s por uma sessão que
        está subindo, e segurar o aparelho quatro minutos por um contexto opcional seria péssimo negócio. A etapa
        seguinte vai observar a tela de qualquer jeito.

        Devolve a árvore, e não o texto: quem escreve um comentário quer o que está na publicação, quem responde
        alguém quer a fala DAQUELA pessoa. São leituras diferentes da mesma tela, e ler duas vezes seria o dobro
        do custo pelo mesmo instante.
        """
        if rt is None:
            return None
        # Ler a tela NUNCA sobe a sessão de automação. Se subisse, o teto curto daqui cancelaria `ensure_automation`
        # no meio — e `CancelledError` não é `Exception`, então o aparelho ficaria marcado como "starting" para
        # sempre, estado que o monitor não re-tenta. O passo seguinte então esperaria 240 s por vez, três vezes,
        # com o aparelho preso. Contexto opcional não pode custar isso: se a sessão não está de pé, escreve sem.
        if rt.automation.state != "ready" or not rt.session.connected:
            return None
        try:
            arvore = await asyncio.wait_for(self._st.devices.hierarchy(rt), timeout=_TELA_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - qualquer falha de aparelho só custa contexto, não trava a etapa
            log.info("%s: não deu para ler a tela para o rascunho (%s)", getattr(rt, "id", "?"), exc)
            return None
        # Depois de revisão de plano o app é reiniciado ANTES desta porta: a tela pode ser a de início do Android.
        # Ler o launcher e mandar ao modelo como "o que está na tela" seria pior do que não ler nada.
        if pacote and pacote not in arvore.packages:
            return None
        return arvore

    def _approval_gate(self, obj: Any, srow: Any, cap: Any, profile_id: str, *, motivo: str = "",
                       excecao: str | None = None, pacote: str | None = None, app_id: str | None = None) -> Any:
        """Ação que exige aprovação: a decisão da pessoa acontece ANTES de digitar qualquer coisa.

        É por isso que a porta fica aqui e não no meio da etapa: etapa concluída é estado terminal, então não
        haveria como "editar e refazer" depois que o texto já foi digitado e enviado.

        `motivo` é o porquê de a aprovação ser exigida além da política (DM fria, o mesmo pedido a várias contas —
        ADR-055): vai no resumo do pedido e no motivo da espera, para quem decide saber o que está confirmando.

        `excecao`: a etapa usa esta exceção de política (30.65). O dono decide sobre o cartão DELA: a decisão de uma
        versão anterior da etapa, com o mesmo texto e alvo, não vale, nem a da própria etapa tomada antes de a exceção
        ser presa a ela.
        """
        pedido = self._st.approvals.for_step(srow["id"])
        descarte = ""
        presa = self._st.excecoes.obter(excecao) if excecao else None
        if pedido is not None and presa is not None and presa.presa_em \
                and parse_iso(pedido.created_at) < parse_iso(presa.presa_em):
            # 31.49 (nota da revisão): o sim do plano anterior à exceção presa sai de cena como `expired`; ignorado e
            # deixado `approved`, ele contava como "aprovada e não enviada" no `_repetido` de outras etapas até a faxina.
            if pedido.origem == "plano":
                self._st.approvals.descartar_do_plano(pedido.id, motivo="a exceção 30.65 foi presa a esta etapa depois do sim")
            pedido = None
        if pedido is not None and pedido.origem == "plano":
            # 30.61 (`pacote`: o app da etapa, para recalcular a chave). O sim do plano vale só para o item IDÊNTICO, na
            # validade e antes de o efeito sair. Senão sai de cena como `expired` (não fica "aprovado e não enviado"
            # contando contra outras etapas) e a porta pergunta de novo, como sempre.
            descarte = self._sim_do_plano_nao_vale(pedido, obj, srow, cap, profile_id, pacote, app_id)
            if descarte:
                self._st.approvals.descartar_do_plano(pedido.id, motivo=descarte)
                self._st.repo.decision(f"{obj['instance_id']}: o sim dado no plano para '{srow['title']}' não vale: "
                                   f"{descarte}; a porta pergunta de novo.", run_id=obj["run_id"],
                                   instance_id=obj["instance_id"], step_id=srow["id"])
                pedido = None
        bindings = self._st.repo.bindings_da_etapa(srow, profile_id)
        # O alvo normalizado é a chave da reserva de frota (`SocialRepository.fleet_targeting`).
        alvo = contraparte(cap, bindings) or alvo_da_acao(cap, bindings)
        # 31.113 F3: o pedido GUARDA o marcador (alvo e texto pela máscara reversível, resumo pela do registro); a
        # porta decide com o valor. A tela do painel resolve ao vivo (`texto_ao_vivo`); canal e evento levam o marcador.
        alvo_gravado = self._st.repo.texto_reversivel(alvo, obj["id"])
        texto_gravado = self._st.repo.texto_reversivel(_texto_ou_nada(bindings.get("content")), obj["id"])
        if pedido is None and excecao is None:
            # Etapa revisada (recuperação automática, “Tentar novamente”) tem id novo: sem isto, o que a pessoa já
            # aprovou na versão anterior virava pedido novo e o objetivo voltava a esperá-la. Só vale a decisão
            # sobre a mesma etapa, com o mesmo alvo e o mesmo texto, cujo efeito ainda não saiu.
            pedido = self._st.approvals.acompanhar_revisao(
                srow["id"], profile_id=profile_id, acao=cap, target=alvo_gravado, content=texto_gravado,
                disparou=lambda etapa: self._st.repo.commit_state(etapa)[0])
            if pedido is not None:
                self._st.repo.decision(
                    f"{obj['instance_id']}: a decisão {pedido.id} ({pedido.status}) sobre '{srow['title']}' numa "
                    f"versão anterior do plano vale para a etapa revisada (v{srow['plan_version']}): mesma ação, mesmo "
                    "alvo e mesmo texto, e o efeito ainda não tinha saído.",
                    run_id=obj["run_id"], instance_id=obj["instance_id"], step_id=srow["id"])
        if pedido is None:
            # 31.49: quando o sim do plano não valeu, o cartão novo diz por quê; quem decide vê que já tinha aprovado
            # algo e o que mudou, e não um pedido repetido sem explicação.
            motivo = "; ".join(m for m in (motivo, f"o sim dado no plano não vale: {descarte}" if descarte else "")
                               if m)
            pedido = self._st.approvals.open(
                profile_id=profile_id, capability=cap.key,
                summary=self._st.repo.texto_mascarado(f"{srow['title']} — {motivo}" if motivo else srow["title"],
                                                  obj["id"]) or srow["title"],
                target=alvo_gravado, content=texto_gravado,
                run_id=obj["run_id"], objective_id=obj["id"], step_id=srow["id"])
            self._st.bus.emit("approval.pending", f"{obj['instance_id']}: {srow['title']} aguarda aprovação",
                          level="warn", run_id=obj["run_id"], instance_id=obj["instance_id"],
                          objective_id=obj["id"], data={"approval": pedido.to_dict()})
        if pedido.status in ("approved", "edited"):
            return None
        if pedido.status == "rejected":
            return Verdict(allowed=False, policy="approval_required",
                           reason="esta ação foi rejeitada por quem aprova",
                           hint="Nada será enviado neste alvo. Retome o item se quiser planejar outra coisa.")
        self._st.db.execute("UPDATE objectives SET blocked_kind='approval' WHERE id=?", (obj["id"],))
        return Verdict(allowed=False, policy="approval_required",
                       reason=f"{srow['title']} precisa de aprovação antes de acontecer"
                              + (f" ({motivo})" if motivo else ""),
                       hint="Abra Aprovações e escolha aprovar, editar ou rejeitar.")

    def _sim_do_plano_nao_vale(self, pedido: Approval, obj: Row, srow: Row, cap: Capability, profile_id: str,
                               pacote: str | None, app_id: str | None = None) -> str:
        """30.61: por que o sim dado na prévia NÃO cobre esta etapa ('' quando cobre). Falha fechado: vencido, já gasto,
        ou a chave da etapa RELIDA (texto, alvo, objeto, mídia, perfil, aparelho, app) diferente da aprovada."""
        if pedido.status != "approved":
            return f"a aprovação está '{pedido.status}'"
        if not pedido.expires_at or parse_iso(pedido.expires_at) <= now():
            return "venceu"
        if pedido.interaction_id is not None:
            return "já foi gasto num efeito"
        bindings = self._st.repo.bindings_da_etapa(srow, profile_id)
        tem_imagem, sha = midia_da_etapa(self._st.db, bindings, perfil=profile_id)   # 29.79: só a imagem DESTA persona
        chave = chave_da_aprovacao(bindings, cap, perfil=profile_id, aparelho=str(obj["instance_id"]), pacote=pacote,
                                   run_id=str(obj["run_id"]), objective_id=str(obj["id"]), tem_imagem=tem_imagem,
                                   midia_sha256=sha)
        if chave is None or chave != pedido.chave_sha256:
            return "o item mudou desde a aprovação (a chave divergiu)"
        # 31.49: aprovação sem texto nunca libera escrita. A chave já leva o texto; esta conferência, depois dela, é a
        # regra dita com todas as letras, e pega o sim gravado sem o texto que o dono viu (o que vai sair tem de ser o
        # que ele leu).
        if cap.needs_draft:
            fechado, texto = texto_exato(cap, bindings)
            # 31.113 F3: o pedido guarda o marcador; a comparação é valor com valor, pela mesma troca da chave.
            visto = self._st.repo.texto_ao_vivo(pedido.content, str(obj["id"])) or ""
            if not fechado or texto is None or visto.strip() != texto.strip():
                return "o sim do plano não traz o texto que vai sair"
        # 31.49 (F1 da revisão): a chave não leva estado de fora do item. A mensagem repetida que SURGIU depois do sim
        # (outra execução mandou, ou teve aprovada, o mesmo texto ao mesmo alvo) é estado mudado: o dono não a viu na
        # prévia, então o sim não a cobre. A que já existia antes do sim estava no motivo da prévia e segue coberta.
        # (A confirmação do ADR-055 desta porta é a do mesmo pedido a várias contas DESTA execução: sai dos objetivos da
        # própria execução, que a prévia já via; a frota entre execuções RECUSA no `check`, antes de o sim ser lido.)
        if pedido.decided_at:
            nova = self._st.policies.mensagem_repetida(profile_id, cap, bindings, app_id=app_id, step_id=srow["id"],
                                                   desde=parse_iso(pedido.decided_at))
            if nova:
                return "a repetição surgiu depois do sim"
        return ""
