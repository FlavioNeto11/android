"""Execução de UMA etapa em UM aparelho: observar → decidir → validar → agir → observar → verificar.

Regras centrais:
- o retorno do driver só prova que o comando foi aceito; a etapa só conclui com a pós-condição observada;
- etapa com efeito externo dispara no máximo UMA ação "commit" (em todas as tentativas); se o resultado
  ficar desconhecido, reconcilia pela tela e, persistindo a dúvida, marca `uncertain` — nunca reenvia sozinha;
- pontos seguros entre ações permitem pausar, cancelar ou ceder o aparelho ao usuário.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Awaitable, Callable, Protocol, Sequence, TypeVar

from PIL import Image

from ..automation import conhecimento_de_telas as telas_do_app
from ..automation import tools as ferramentas
from ..automation.driver import DriverBusy, DriverError, DriverTimeout, FalhaDeLeitura, sessao_perdida
from ..automation.hierarchy import (MOTIVO_DESAFIO, MOTIVO_SENHA, SUBTIPO_CODIGO, SUBTIPO_CONTA_TRAVADA,
                                    SUBTIPO_VERIFICACAO, ContaTravada, UiElement, UiTree)
from ..automation.tools import (CONTROL_TOOLS, EFFECT_CAPABLE, StepBlocked, StepDone, TelaDeContaTravada, ToolContext,
                                ToolValidationError, esperar_foco, execute_tool, looks_like_commit, resolve_point,
                                urls_do_texto, validate_call)
from ..config import Config
from ..devices.adb import AVISO_DE_ANR, MorteDoApp, motivo_de_anr
from ..devices.manager import DeviceManager, DeviceRuntime, Limiter, Observation, dimensoes_do_modelo
from ..metricas import metricas
from ..models import (DELIVERY_ORDER, ActionStatus, AttemptStatus, DeliveryLevel, StepDTO, StepResult, StepStatus)
from ..modules.capabilities.domain.definition import CapabilityRef
from ..modules.capabilities.domain.strategy import StrategyKind
from ..modules.capabilities.domain.verification import Observation as Leitura
from ..modules.capabilities.domain.verification import StepView, VerifyOutcome
from ..modules.capabilities.infrastructure.catalog_provider import CatalogCapabilityProvider
from ..modules.capabilities.infrastructure.catalog_registry import CatalogCapabilityRegistry
from ..modules.identity.application.available_data import (account_hosts, available_data, resolve_secret,
                                                            typable_secret_for)
from ..modules.identity.domain.available_data import ResolvedSecret, SecretResolution
from ..modules.identity.infrastructure.profile_data import SqlProfileDataStore
from ..planning.capabilities import CONHECIMENTO_DE_APPS, capability_of, contraparte, guardas_do_cartao
from ..planning.catalog import session_provider_of
from ..planning.provider import (AIError, AIProvider, AppContext, Decision, DecisionRequest, ScreenInput, StepContext,
                                 Usage, VerifyRequest)
from ..db import Row, loads
from ..security.secret_store import SecretStoreLocked, SecretStoreUnavailable
from ..security.sensitive_input import SensitiveInputError, SensitiveInputUnavailable
from ..social.approvals import ler_rascunho
from ..util import norm_text, now, now_iso, parse_iso
from .costuras import (SAIU_POR_EXCECAO, SEM_COSTURAS, CosturasDeAprendizado, FechamentoDeTentativa, PedidoDeLicoes,
                       avisar, pedir_licoes)
from .foreach import sanitize_item
from .proofs import marcas_pendentes_na_tela, variantes_de_arroba
from .projecao import HistoricoDeAcoes, app_da_etapa
from .recipes import READ_ONLY, RecipeDiverged, RecipeStore, Replayer, contar_retorno_ia, distill, unique_selectors
from .repository import Repository

log = logging.getLogger("poc.executor")

T = TypeVar("T")

#: UI ocupada (`DriverBusy`) numa LEITURA: quantas releituras, e o recuo entre elas. A sessão está viva (quem
#: respondeu o 500 foi ela); o convidado é que está saturado. Recriá-la era o que custava 27–80 s por vez e
#: reinstrumentava o UiAutomator2 no convidado sem folga: 5 recriações = 305,6 s de 925 s na
#: r-20260928195344-02ee9e, 4 = 214 s na r-20260928165254-e31953. Vale igual para a leitura que falhou fora da
#: sessão (`FalhaDeLeitura`: o screencap pelo adb, a captura na origem) — o mesmo convidado saturado.
RELEITURAS_UI_OCUPADA = 3
RECUO_UI_OCUPADA_S = 4.0


async def reler_se_ocupada(ler: Callable[[], Awaitable[T]], *, prazo: float, quem: str) -> T:
    """Faz a leitura e, se a UI estiver ocupada ou a leitura tiver falhado fora da sessão, relê com recuo — sem tocar
    na sessão. O recuo sai do prazo da etapa (`prazo`, relógio monotônico): sem tempo para esperar, o erro sobe na
    hora. Só LEITURA passa por aqui; uma ação que volta ocupada pode ter chegado ao app e não se repete às cegas."""
    releituras, metrica = 0, ""
    while True:
        try:
            valor = await ler()
        except (DriverBusy, FalhaDeLeitura) as exc:
            # Métricas separadas: UI ocupada é o app segurando a thread de interface; a outra é o adb ou o agente.
            metrica = "automacao.ui_ocupada" if isinstance(exc, DriverBusy) else "automacao.leitura_falhou"
            releituras += 1
            if releituras > RELEITURAS_UI_OCUPADA or time.monotonic() + RECUO_UI_OCUPADA_S > prazo:
                metricas.contar(metrica, resultado="persistiu")
                raise
            log.info("%s: %s (%s); relendo em %.0f s sem recriar a sessão", quem,
                     "UI ocupada" if isinstance(exc, DriverBusy) else "leitura falhou", exc, RECUO_UI_OCUPADA_S)
            await asyncio.sleep(RECUO_UI_OCUPADA_S)
            continue
        if releituras:
            metricas.contar(metrica, resultado="relida")
        return valor


#: Navegador → resource-id da barra de endereço. É por ela que `type_secret` confere o SITE antes de digitar.
BARRA_DE_ENDERECO = {"com.android.chrome": "com.android.chrome:id/url_bar"}


def _host(url_ou_texto: str) -> str:
    """Host de uma URL ou do texto da barra de endereço (que o Chrome mostra sem `https://`)."""
    t = (url_ou_texto or "").strip().casefold()
    t = t.split("://", 1)[1] if "://" in t else t
    return t.split("/", 1)[0].split("#", 1)[0].split("?", 1)[0].rsplit("@", 1)[-1].split(":", 1)[0]


def urls_da_pessoa(command: str) -> set[str]:
    """Os endereços que `open_url` abre: SÓ os escritos no comando (o executor acrescenta, por aparelho, os sites das
    contas de portal da persona — `ToolContext.allowed_hosts`). Nem os parâmetros do plano (o planejador pode
    completar "portal MTR" com um domínio que ninguém escreveu), nem `step.variables` (onde mora o `{item}` lido da
    tela). Onde `type_secret` digita é outra pergunta: o `host` da CONTA (ADR-040)."""
    return set(urls_do_texto(command))


def pede_intervencao_humana(tree: UiTree, *, tem_credencial: bool = False) -> bool:
    """A tela sensível exige uma PESSOA, ou só exige que a imagem não saia daqui?

    Eram a mesma pergunta enquanto `sensitive` significava apenas "há campo de senha" (achado #127). Deixaram de
    ser: uma tela declarada em `config.yaml: sensitive_screens`, ou qualquer tela da VM-loja, tem a imagem
    omitida — mas parar a etapa nela seria inventar uma falha de autenticação e marcar o perfil como
    `auth_required` toda vez que a IA passasse por ali.

    Função nomeada, e não uma condição embutida no laço, porque é a regra que separa as duas coisas: escondida no
    meio de 900 linhas ela voltaria a ser "sensível = pare", que é de onde ela veio.
    """
    # ADR-025/040: com a senha da conta da persona no app DESTA etapa (guardada e consentida), a tela de senha é só
    # mais uma tela — o ator preenche com `type_secret`. Desafio (código não fornecido, CAPTCHA) continua pedindo
    # gente, com ou sem credencial.
    if tem_credencial and tree.sensitive_reason == MOTIVO_SENHA:
        return False
    return tree.sensitive and tree.sensitive_reason in (MOTIVO_SENHA, MOTIVO_DESAFIO)


class AoDesmentirSessao(Protocol):
    """`AppState._sessao_desmentida`: a tela contradisse o que a sessão do perfil afirmava. `subtipo` só acompanha
    `auth_challenge` (ADR-055): `conta_travada` bloqueia o perfil; `codigo` e `verificacao` só pedem uma pessoa."""

    def __call__(self, instance_id: str, kind: str, detail: str, *, subtipo: str | None = None) -> None: ...


def motivo_da_trava(trava: ContaTravada) -> str:
    """O motivo gravado na tentativa, no objetivo e no evento: o desfecho, o subtipo e o trecho que casou. Antes o
    tipo e o trecho eram calculados e descartados, e a pessoa lia só "o app pede autenticação"."""
    o_que = {SUBTIPO_CONTA_TRAVADA: "a verificação de conta travada apareceu na tela",
             SUBTIPO_CODIGO: "o app pede um código de login/2FA"}.get(
        trava.subtipo, "a IA relatou uma tela de verificação que nenhum sinal conhece")
    return f"auth_challenge ({trava.subtipo}): {o_que} — “{trava.trecho}”; nada foi tocado."


#: O que a pessoa faz diante de cada subtipo. Conta travada não pede "resolva na tela": a regra do dono é que ela
#: está perdida para a automação, e quem decide reativar é ele.
_NECESSIDADE_DA_TRAVA = {
    SUBTIPO_CONTA_TRAVADA: "A conta mostrou a verificação de segurança (conta travada): o perfil foi bloqueado e nada "
                           "foi tocado. Veja o aparelho; reative o perfil só se a conta voltar a ser usável.",
    SUBTIPO_CODIGO: "O app pede um código de login/2FA: assuma o controle, digite o código que chegou à pessoa e "
                    "devolva o controle à IA.",
    SUBTIPO_VERIFICACAO: "A IA viu uma tela de verificação: assuma o controle e veja o aparelho. Se for a verificação "
                         "de conta travada, bloqueie o perfil; se não, resolva na tela e devolva o controle à IA.",
}


class Outcome(StrEnum):
    succeeded = "succeeded"
    retry = "retry"
    failed = "failed"
    waiting_user = "waiting_user"
    uncertain = "uncertain"
    yielded = "yielded"          # cedeu num ponto seguro (pausa / controle manual)
    cancelled = "cancelled"
    device_stuck = "device_stuck"


@dataclass(slots=True)
class StepOutcome:
    outcome: Outcome
    detail: str | None = None
    needs: str | None = None
    delivery_level: DeliveryLevel | None = None
    items: list[str] | None = None   # etapa de coleta: itens lidos (o scheduler expande o bloco for_each com eles)
    plan_defect: bool = False        # a pós-condição não é comprovável por tela: repetir ou refazer o MESMO plano não resolve
    # Item 7.3: motivo ESTRUTURADO do bloqueio, quando o outcome é `waiting_user` por causa da IA (not_configured |
    # billing | refusal) — o scheduler grava isto em `objectives.blocked_kind='ai'` para a interface distinguir
    # "a IA está travando este item" de política/limite/aprovação, em vez de só um texto livre.
    ai_blocked: bool = False
    #: ADR-055: a etapa parou numa tela de verificação da conta. Não é veredito sobre a receita nem sobre o plano — a
    #: conta travou, o caminho não errou; contar isso levaria à quarentena uma receita que funciona nas outras contas.
    trava_da_conta: bool = False


# kinds de AIError que são problema de CONTA (crédito ou credencial), não da etapa: nenhuma tentativa nova
# resolveria, então acionam o disjuntor em vez de reenviar. `_ai` é o ponto único que os classifica assim.
# `balance` (ADR-051): a PLATAFORMA barrou antes de gastar — saldo estimado da conta abaixo do bloqueio do dono.
ACCOUNT_ERROR_KINDS = ("billing", "not_configured", "balance")

# mensagem mostrada ao usuário — nunca o dicionário cru do provedor (ver achado #90).
_ACCOUNT_ERROR_MESSAGE = {
    "billing": "Sem crédito no provedor de IA — recarregue e retome.",
    "not_configured": "Credencial do provedor de IA inválida ou ausente — corrija e retome.",
    "balance": "Saldo estimado de uma conta de IA abaixo do limite de bloqueio — recarregue, registre a recarga "
               "em Configuração › IA e retome.",
}


async def _com_prazo(coro: Any, deadline: float | None, role: str) -> Any:
    """A chamada nunca passa do prazo da ETAPA (achado #96).

    O provedor já tem o seu próprio prazo por função; este aqui é o teto de cima, o que impede uma chamada de
    sobreviver à etapa que a pediu. Cancelar a corrotina solta a vaga de IA e o aparelho na hora.
    """
    if deadline is None:
        return await coro
    restante = deadline - time.monotonic()
    try:
        return await asyncio.wait_for(coro, timeout=max(0.1, restante))
    except asyncio.TimeoutError as exc:
        # `step_deadline`, e não o `error` genérico: quem trata dizia "IA indisponível" e mandava procurar defeito no
        # provedor, quando quem venceu foi o prazo da ETAPA (r-20260928195344-02ee9e: o convidado sem CPU gastou o
        # prazo em partidas a frio e a IA levou a culpa).
        raise AIError(f"A chamada de IA ({role or 'modelo'}) passou do prazo restante da etapa "
                      f"({max(0.0, restante):.0f} s).", retryable=False, kind="step_deadline") from exc


def _inicio_monotonico(started_at: str | None, inicio_da_tentativa: float) -> float:
    """`steps.started_at` (relógio de parede, gravado na 1ª tentativa) no relógio monotônico daqui: o início da ETAPA,
    que sobrevive às tentativas. Sem ele, o da tentativa. Nunca depois do início da tentativa: hora no futuro é outro
    processo, de relógio adiantado, que assumiu a 1ª tentativa."""
    inicio = parse_iso(started_at)
    if inicio is None:
        return inicio_da_tentativa
    return min(inicio_da_tentativa, time.monotonic() - max(0.0, (now() - inicio).total_seconds()))


def _perfil_do_objetivo(objective: Row) -> str | None:
    """O perfil do objetivo, quando a linha o tem (a fotografia do planejamento); nunca adivinhado."""
    try:
        perfil = objective["profile_id"]
    except (KeyError, IndexError, TypeError):
        return None
    return str(perfil) if perfil else None


@dataclass(slots=True)
class AiBreakerTrip:
    """Última vez que o disjuntor de conta de IA disparou: `/api/health` e a aba IA leem isto."""
    kind: str            # billing | not_configured
    message: str
    run_id: str
    at: str               # ISO 8601


class StepExecutor:
    def __init__(self, cfg: Config, repo: Repository, devices: DeviceManager, provider: AIProvider,
                 ai_limiter: Limiter, settings_getter: Callable[[], Any]):
        self.cfg = cfg
        self.repo = repo
        self.devices = devices
        self.provider = provider
        self.ai_limiter = ai_limiter
        self.get_settings = settings_getter
        self.recipes = RecipeStore(repo.db)
        # Normal medido por ação (item 18.3): aviso ao passar do p90 e parada conservadora de laço descontrolado.
        self.historico = HistoricoDeAcoes(repo.db, lambda: cfg.file.ai.prices,
                                          janela_dias=cfg.file.ai.step_budget.window_days,
                                          retencao_dias=lambda: int(self.get_settings().log_retention_days))
        self._acima_do_normal: set[str] = set()
        #: Os dados da persona de cada aparelho (ADR-040): a lista para o ator, a resolução de `type_secret(name)` e
        #: "há senha para o app desta etapa?". Metadados e referência do cofre; o valor só no canal sensível.
        self.dados = SqlProfileDataStore(repo.db, tem_provedor_de_sessao=lambda p: session_provider_of(p) is not None)
        # Serviço social (injetado pelo AppState). Sem ele, nada de histórico — e o motor antigo segue igual.
        self.social: Any = None
        self.approvals: Any = None                          # idem: só para ligar a aprovação ao efeito que ela liberou
        #: Login ou desafio apareceu NO MEIO da execução. O estado de sessão é cache do que se observou, e o
        #: executor é quem está olhando a tela naquele instante — antes ele devolvia `waiting_user` e deixava o
        #: perfil dizendo "Conectado". Injetado pelo AppState: (instance_id, kind, detail, subtipo=).
        self.on_auth_needed: AoDesmentirSessao | None = None
        #: Costuras do aprendizado (ADR-054, A2), injetadas pelo AppState: as lições medidas do ator (pedidas UMA vez
        #: por tentativa, e nunca na etapa que a receita conduz) e o aviso de cada tentativa fechada. No-op por padrão;
        #: nunca decidem desfecho, verificação nem guarda.
        self.costuras: CosturasDeAprendizado = SEM_COSTURAS
        self._effects: dict[str, tuple[str, str]] = {}      # step_id → (perfil, interação em aberto)
        # Pacote "anr": etapas que já gastaram a sua reabertura determinística depois de um ANR. Por ETAPA, não por
        # tentativa — na r-20260928195344-02ee9e cada tentativa acabava pelo prazo e a seguinte reabria de novo. Some
        # no desfecho final da etapa. Memória do processo: reiniciado o backend, a contagem de mortes (que vem do
        # aparelho, desde `steps.started_at`) continua valendo e o pior caso é UMA reabertura a mais.
        self._reabertas_por_anr: set[str] = set()
        # Disjuntor de conta de IA (achado #90): por execução, a PRIMEIRA falha de cobrança/credencial represa
        # as etapas seguintes sem gastar tentativa — os aparelhos seguintes nem chegam a chamar o provedor.
        self._tripped_runs: dict[str, AiBreakerTrip] = {}
        self.ai_breaker: AiBreakerTrip | None = None        # a mais recente, de qualquer execução — para a saúde
        #: ADR-025/040, injetados pelo AppState: o cofre onde está a senha da conta da persona e o canal
        #: sensível que a digita. Sem os dois, `type_secret` recusa (o valor nunca toma o caminho de `type_text`).
        self.secrets: Any = None
        self.sensitive_input: Any = None
        #: VERIFY pela porta de capability (fase G, §14.1): a prova local do catálogo, embrulhada pelo provider. O
        #: executor continua dono do aparelho e do desfecho: ele observa e entrega a leitura; só `proved` dispensa o
        #: verificador, `not_proved`/`unknown` seguem o caminho de sempre (o modelo).
        self.capabilities = CatalogCapabilityProvider(CatalogCapabilityRegistry(self._pacote_do_app_id))

    def _pacote_do_app_id(self, app_id: str) -> str | None:
        return self.repo.db.scalar("SELECT package FROM apps WHERE id=?", (app_id,))

    def preenchedor(self, rt: DeviceRuntime, resolver: Callable[[str], SecretResolution], tree_vista: UiTree,
                    observe: Callable[[], Any], *, profile_id: str | None = None, run_id: str | None = None,
                    step_id: str | None = None) -> Callable[[str, str | None], Any] | None:
        """`type_secret(name)` → canal sensível (ADR-025/040). `resolver` traduz o NOME lógico na senha de uma conta
        da persona do aparelho (perfil do OBJETIVO), ou na recusa; `None` só quando não há perfil nenhum.

        Três travas antes de o valor sair do cofre:
        - só campo de SENHA: num campo comum o valor apareceria na hierarquia seguinte, que vai ao modelo;
        - só no APP DA CONTA (o pacote dela): a senha do portal não vai para a senha de outro app que aparecer no
          caminho (Instagram, conta Google), nem para outro app da mesma etapa;
        - no navegador, só no SITE DA CONTA: o host da barra de endereço tem de ser o `host` da conta (ou
          subdomínio dele). Conta de navegador sem `host` não recebe a senha em site nenhum. Um link seguido até
          outro domínio não recebe a senha.
        A referência da conta nunca passa por `run_secrets`; o valor nunca passa pelo modelo. Depois de digitar, a
        conta ganha `last_used_at` e a execução registra QUAL conta foi usada (nome, nunca valor)."""
        if profile_id is None:
            return None

        async def preencher(nome: str, element_id: str | None) -> dict[str, Any]:
            resolucao = resolver(nome)
            if resolucao.secret is None:
                raise DriverError(resolucao.refusal or f"Credencial {nome!r} não disponível.", effect_possible=False)
            segredo = resolucao.secret
            ref = segredo.secret_ref
            hosts = {segredo.host} if segredo.host else set()
            if self.secrets is None or self.sensitive_input is None:
                raise DriverError("Canal de entrada sensível indisponível neste servidor.", effect_possible=False)
            ordem, rid = 0, ""
            if element_id:
                alvo = tree_vista.by_id(element_id)
                if alvo is None or not alvo.password:
                    raise DriverError("type_secret só preenche campo de SENHA; este elemento não é um.",
                                      effect_possible=False)
                ordem = [e.id for e in tree_vista.elements if e.password].index(alvo.id)
                rid = alvo.resource_id
            await self._conferir_destino(rt, observe, segredo.package, hosts)

            def localizar(tree: UiTree) -> UiElement | None:
                # Nem id nem posição da observação do modelo sobrevivem ao teclado abrir (a WebView redimensiona e
                # rola o campo): casa pelo resource-id quando ele é único, senão pela ordem entre os campos de senha.
                senhas = [e for e in tree.elements if e.password]
                mesmo = [e for e in senhas if rid and e.resource_id == rid]
                if len(mesmo) == 1:
                    return mesmo[0]
                return senhas[ordem] if len(senhas) > ordem else None
            try:
                recibo = await self.sensitive_input.fill(call=rt.executor.run, io=rt.io, observe=observe,
                                                         locate=localizar, secret=lambda: self.secrets.get_secret(ref))
            except SensitiveInputUnavailable as exc:  # recusou ANTES de tocar no aparelho: nada foi digitado
                raise DriverError(str(exc), effect_possible=False) from None
            except SensitiveInputError as exc:       # mensagem fixa do canal: nunca carrega o valor
                raise DriverError(str(exc), effect_possible=True) from None
            except (KeyError, SecretStoreLocked, SecretStoreUnavailable):
                # A senha saiu do cofre (conta apagada no meio) ou a chave não a abre: nada foi digitado.
                raise DriverError("A senha desta conta não está mais disponível no cofre.",
                                  effect_possible=False) from None
            self._credencial_usada(rt, segredo, profile_id=profile_id, run_id=run_id, step_id=step_id)
            return recibo.to_dict()
        return preencher

    def _credencial_usada(self, rt: DeviceRuntime, segredo: ResolvedSecret, *, profile_id: str, run_id: str | None,
                          step_id: str | None) -> None:
        """A conta ganha `last_used_at`, e a execução registra QUAL conta entrou (só nomes: conta, app, host e a data
        do consentimento). Enriquecimento: nunca derruba a digitação que já aconteceu."""
        try:
            if self.social is not None:
                self.social.repo.touch_account_credential(profile_id, segredo.account_id)
            if run_id is not None:
                self.repo.decision(f"{rt.id}: senha da conta em {segredo.label} digitada pelo canal sensível "
                                   f"(consentimento de {segredo.consent_at}; a IA conheceu só o nome {segredo.name})",
                                   run_id=run_id, instance_id=rt.id, step_id=step_id)
        except Exception:  # noqa: BLE001 - registro é enriquecimento; a digitação já aconteceu
            log.exception("%s: não foi possível registrar o uso da credencial da conta", rt.id)

    async def _conferir_destino(self, rt: DeviceRuntime, observe: Callable[[], Any], app_package: str | None,
                                hosts: set[str]) -> None:
        pacote = await rt.executor.run(rt.io.current_package, timeout=10, label="pacote em primeiro plano")
        if app_package and pacote != app_package:
            raise DriverError(f"A senha só é digitada no app da conta dela ({app_package}); a tela está em "
                              f"{pacote or 'app desconhecido'}.", effect_possible=False)
        barra = BARRA_DE_ENDERECO.get(pacote or "")
        if barra is None:
            return
        texto = next((e.text for e in (await observe()).elements if e.resource_id == barra and e.text), "")
        host = _host(texto)
        if not host:
            raise DriverError("Não dá para confirmar o site: a barra de endereço não está visível. Role a página ao "
                              "topo e tente de novo.", effect_possible=False)
        if not any(host == h or host.endswith("." + h) for h in hosts):
            sites = ", ".join(sorted(hosts)) or "nenhum: a conta não tem host"
            raise DriverError(f"A senha só é digitada no site da conta ({sites}); a página está em {host}.",
                              effect_possible=False)

    async def _mortes_por_anr(self, rt: DeviceRuntime, pacote: str, *, desde: float,
                              timeout: float) -> list[MorteDoApp]:
        """ANRs do processo principal de `pacote` desde `desde` (relógio monotônico DAQUI: o início da etapa).

        A idade de cada morte é medida no relógio do CONVIDADO (`Adb.app_deaths`), que pode estar 26–31 s fora do
        daqui. A janela que vai até ele é o decorrido mais o prazo da própria leitura (a hora é lida lá, até `timeout`
        depois daqui); o corte fino é na volta: morte desta etapa tem idade MENOR que o decorrido até a resposta
        chegar, e a de antes da etapa — a da etapa anterior, logo antes — não. Não deu para ler = lista vazia: sem o
        sinal segue o caminho de antes (a IA vê a tela), e ler de novo é permitido — é só leitura. Só ANR: um crash
        comum não é "convidado sem CPU", e a mensagem diria o que não se sabe.
        """
        janela = time.monotonic() - desde + timeout
        try:
            mortes = await rt.executor.run(lambda: rt.io.app_deaths(pacote, within_s=janela), timeout=timeout,
                                           label="mortes do app")
        except (DriverError, AttributeError, TypeError) as exc:
            log.info("%s: sem ler as mortes de %s agora (%s)", rt.id, pacote, exc)
            return []
        decorrido = time.monotonic() - desde
        return [m for m in mortes if m.anr and m.idade_s <= decorrido]

    def _avisar_anr(self, rt: DeviceRuntime, motivo: str) -> None:
        """O motivo vai para a saúde do aparelho (o aviso do cartão), com a regra dos outros avisos (pressão, relógio,
        internet): só ocupa o cartão vazio ou o que já é dele — nunca atropela um aviso de outro assunto."""
        if rt.attention is None or AVISO_DE_ANR in rt.attention:
            self.devices.marcar_atencao(rt, motivo[:1].upper() + motivo[1:])

    def account_error_message(self, kind: str) -> str:
        return _ACCOUNT_ERROR_MESSAGE.get(kind, "Provedor de IA indisponível para esta conta.")

    def _trip_ai_breaker(self, run_id: str, exc: "AIError") -> None:
        """Primeira falha de conta nesta execução: registra o disjuntor e pede a pausa (não repete se já disparado)."""
        if run_id in self._tripped_runs:
            return
        trip = AiBreakerTrip(kind=exc.kind, message=self.account_error_message(exc.kind), run_id=run_id, at=now_iso())
        self._tripped_runs[run_id] = trip
        self.ai_breaker = trip
        self.repo.request_pause(run_id, trip.message)
        self.repo.bus.emit("log", f"Disjuntor de conta de IA acionado ({exc.kind}): {trip.message}",
                           level="error", run_id=run_id)

    def clear_ai_breaker(self, run_id: str) -> None:
        """Ao retomar a execução (usuário recarregou o crédito ou corrigiu a credencial), o disjuntor solta:
        a próxima chamada volta a ir ao provedor de verdade em vez de represar sozinha para sempre."""
        self._tripped_runs.pop(run_id, None)
        if self.ai_breaker is not None and self.ai_breaker.run_id == run_id:
            self.ai_breaker = None

    # ------------------------------------------------------------------ IA com limites
    async def _ai(self, run_id: str, objective_id: str | None, coro_factory: Callable[[], Any], *,
                  step_id: str | None = None, role: str = "", deadline: float | None = None,
                  attempt_id: str | None = None) -> Any:
        """Ponto único de toda chamada de IA de uma execução: disjuntor, tetos, limite global e novas tentativas.

        `objective_id=None` é uso ligado à execução mas a objetivo nenhum — é assim que o PLANEJAMENTO passa a
        entrar aqui (achado #96, item 4): ele chamava `provider.plan` direto e ficava fora da repetição com
        espera e da conferência de orçamento.

        `deadline` é o `time.monotonic()` em que a ETAPA vence. A chamada é cortada no que sobra dele: sem isso,
        um provedor pendurado segurava a vaga de IA e o aparelho para além do prazo da etapa, que só era conferido
        no topo do laço.

        `attempt_id` (migração 045): a tentativa que paga a chamada, gravada em `ai_calls` — custo e modelo por
        tentativa, não só por etapa. O planejamento não tem tentativa (nulo).
        """
        s = self.get_settings()
        tripped = self._tripped_runs.get(run_id)
        if tripped is not None:
            # disjuntor já disparado nesta execução: nem chama o provedor — represa sem gastar tentativa nem chamada.
            raise AIError(tripped.message, kind=tripped.kind)
        run = self.repo.run_row(run_id)
        # `status_detail` de ANTES de qualquer anotação de espera desta chamada — para devolvê-lo ao sair
        # (`clear_wait_reason`, achado #68): sem isto, "aguardando resposta do modelo" ficava escrito na tela
        # bem depois de a chamada terminar, até a PRÓXIMA chamada de IA reescrever o texto por cima.
        detalhe_anterior: str | None = None
        if objective_id is not None:
            obj = self.repo.objective_row(objective_id)
            detalhe_anterior = obj["status_detail"]
            if obj["ai_calls"] >= s.ai_max_calls_per_objective:
                exc = AIError(f"Limite de {s.ai_max_calls_per_objective} chamadas de IA por objetivo atingido.",
                              kind="budget")
                self._registrar_orcamento_estourado(run_id, objective_id, step_id, role, exc, attempt_id)
                raise exc
        if step_id is not None and objective_id is not None and self.cfg.file.ai.step_budget.enabled:
            self._conferir_orcamento_da_etapa(run_id, objective_id, step_id, role, run["app_ids"] if run else None,
                                              attempt_id)
        if run and (run["ai_input_tokens"] + run["ai_output_tokens"]) >= s.ai_max_tokens_per_run:
            exc = AIError(f"Orçamento de {s.ai_max_tokens_per_run} tokens da execução esgotado.", kind="budget")
            self._registrar_orcamento_estourado(run_id, objective_id, step_id, role, exc, attempt_id)
            raise exc
        last: AIError | None = None
        for attempt in range(3):
            restante = None if deadline is None else deadline - time.monotonic()
            if restante is not None and restante <= 0:
                # Prazo, não orçamento: `budget` fechava a etapa como `failed` sem nova tentativa, como se fosse o teto
                # de chamadas (mesmo motivo do `step_deadline` em `_com_prazo`).
                raise AIError("Prazo da etapa esgotado antes da chamada de IA.", kind="step_deadline")
            # Achado #68/#93: a espera pela VAGA (semáforo cheio) e a espera pela RESPOSTA (chamada em voo) são
            # motivos DIFERENTES — a primeira o operador resolve subindo `max_ai_concurrency`; a segunda só espera.
            # `objective_id=None` é o planejamento (achado #96, item 4): sem objetivo ainda, nada para anotar.
            if objective_id is not None:
                cheio = self.ai_limiter.active >= self.ai_limiter.limit
                self.repo.note_waiting(
                    objective_id,
                    f"aguardando vaga de IA ({self.ai_limiter.active} de {self.ai_limiter.limit} em uso)" if cheio
                    else "aguardando vaga de IA", wait_reason="ai_capacity")
            async with self.ai_limiter:       # limite de chamadas simultâneas ao modelo (≠ aparelhos ativos)
                if objective_id is not None:
                    self.repo.note_waiting(objective_id, f"aguardando resposta do modelo ({role or 'ia'})",
                                           wait_reason="model_response")
                try:
                    result, usage = await _com_prazo(coro_factory(), deadline, role)
                    self.repo.add_usage(run_id, objective_id, usage if usage.calls or self.provider.simulated
                                        else Usage(calls=1), step_id=step_id, attempt_id=attempt_id)
                    return result
                except AIError as exc:
                    # Achado #101: o log é o único jeito de casar uma chamada com erro à exceção real quando o
                    # texto gravado não basta (era assim que se sabia que um 5xx do provedor virou "Verificação
                    # não pôde ser feita" — casando horário com data/logs/backend.log). E a linha de custo passa
                    # a gravar o modelo REALMENTE pedido e o tipo do erro — nunca mais o pseudo-modelo '(erro)'.
                    log.warning("%s: chamada de IA (%s) falhou: %s", role or "ia", exc.kind, exc, exc_info=True)
                    self.repo.add_usage(run_id, objective_id,
                                        Usage(calls=1, role=role, model=exc.model or self._role_model(role)),
                                        step_id=step_id, ok=False, error_kind=exc.kind, error_status=exc.status,
                                        error_message=str(exc), attempt_id=attempt_id)
                    last = exc
                    if exc.kind in ACCOUNT_ERROR_KINDS:
                        # erro de conta: nova tentativa (aqui ou noutro aparelho) gastaria igual — dispara o
                        # disjuntor e pausa a execução em vez de deixar cada aparelho descobrir sozinho.
                        self._trip_ai_breaker(run_id, exc)
                        raise AIError(self.account_error_message(exc.kind), kind=exc.kind) from exc
                    if not exc.retryable:
                        raise
                finally:
                    # A chamada terminou (sucesso, erro ou cancelamento pelo prazo): "aguardando resposta do
                    # modelo" deixa de valer aqui, sucesso ou não — senão ficaria preso até a PRÓXIMA chamada de
                    # IA reescrever o motivo, mostrando a etapa "esperando o modelo" enquanto ela já agia na tela.
                    # `restore_detail` devolve o TEXTO de antes desta chamada pelo mesmo motivo.
                    if objective_id is not None:
                        self.repo.clear_wait_reason(objective_id, restore_detail=detalhe_anterior)
            await asyncio.sleep(float(self.get_settings().ai_retry_wait_s) * (attempt + 1))
        assert last is not None
        raise last

    def _conferir_orcamento_da_etapa(self, run_id: str, objective_id: str, step_id: str, role: str,
                                     app_ids: object, attempt_id: str | None) -> None:
        """Item 18.3: o normal de chamadas desta AÇÃO, medido nas etapas concluídas. Passar do p90 vira aviso na linha
        do tempo; passar de `max(p90 × fator, p90 + folga)` para a etapa com o motivo — execução e31953: 31 chamadas
        e 18,7 min numa execução cujo normal medido era 16–28 chamadas e 3–5 min, sem nada dizer que estava fora."""
        orc = self.cfg.file.ai.step_budget
        step = self.repo.step_row(step_id)
        app = app_da_etapa(step["app_id"], app_ids)
        acao = step["capability"]
        teto = self.historico.orcamento_de_chamadas(app, acao, fator=orc.p90_factor, folga=orc.slack,
                                                    minimo=orc.min_samples)
        if teto is None:
            return
        limite, est = teto
        feitas = int(self.repo.db.query("SELECT count(*) n FROM ai_calls WHERE step_id=?", (step_id,))[0]["n"])
        nome = acao or "etapa livre"
        normal = f"{est.chamadas.p50:g}–{est.chamadas.p90:g}"
        janela = self.historico.janela_dias          # a EFETIVA: nunca passa da retenção de ai_calls (ADR-054)
        if feitas >= limite:
            exc = AIError(f"A etapa passou do orçamento de {limite} chamadas de IA para {nome}: o normal, em "
                          f"{est.amostras} etapas concluídas nos últimos {janela} dias, é {normal}. Parada "
                          "para não girar até o prazo.", kind="budget")
            self._registrar_orcamento_estourado(run_id, objective_id, step_id, role, exc, attempt_id)
            raise exc
        if feitas > est.chamadas.p90 and step_id not in self._acima_do_normal:
            self._acima_do_normal.add(step_id)
            self.repo.decision(f"{step['instance_id']}: etapa '{step['title']}' acima do normal — {feitas} chamadas "
                               f"de IA; o normal para {nome} é {normal} (para em {limite})",
                               run_id=run_id, instance_id=step["instance_id"], step_id=step_id)

    def _registrar_orcamento_estourado(self, run_id: str, objective_id: str | None, step_id: str | None, role: str,
                                       exc: "AIError", attempt_id: str | None = None) -> None:
        """Achado #99: os dois tetos de ORÇAMENTO (chamadas por objetivo, tokens por execução) recusam ANTES de
        entrar no laço de tentativas — nenhum provedor é chamado, de propósito. Sem esta linha, a recusa nunca
        virava uma linha em `ai_calls` e `/api/usage` não mostrava NADA sobre o estouro (nem em `errors_by_kind`
        nem no painel), embora a etapa e o objetivo já tivessem parado por causa dele. `calls=1` conta como
        tentativa recusada (é o mesmo `Usage` que o laço grava para qualquer erro), sem custo (0 tokens)."""
        self.repo.add_usage(run_id, objective_id, Usage(calls=1, role=role, model=self._role_model(role)),
                            step_id=step_id, ok=False, error_kind=exc.kind, error_status=exc.status,
                            error_message=str(exc), attempt_id=attempt_id)

    def _role_model(self, role: str) -> str:
        """Modelo configurado para esta função, para quando o `AIError` não sabia qual era (falha antes de
        resolver o modelo, ex.: endpoint não configurado). Duck-typing de propósito: nem todo `AIProvider` é o
        `RoutingProvider` do hub (achado #101 — a linha de erro precisa do modelo mesmo assim)."""
        roles = getattr(self.provider, "roles", None)
        if roles and role in roles:
            return getattr(roles[role], "model", "") or ""
        models = getattr(self.provider, "models", None)
        if models and role in models:
            return models[role] or ""
        return getattr(self.provider, "model", "") or ""

    def _want_image(self, tree: UiTree, *, judged_step: bool, first: bool, trouble: bool, requested: bool) -> bool:
        """Política `ai.image_policy`. A imagem custa ~1/3 dos tokens novos de cada chamada; a hierarquia quase sempre
        basta. Em `auto` a imagem vai quando a árvore é pobre (WebView/canvas), na 1ª decisão de etapa julgada por
        visão, depois de erro/ciclo, ou quando o próprio modelo pede (observe_screen.need_image).

        Decide pela ÁRVORE, antes de a imagem existir (adendo v0.20, C1): o resto do que pesa aqui já se sabe antes
        de observar, então a imagem só é adquirida quando vai ser mandada."""
        ai = self.cfg.file.ai
        if tree.sensitive or ai.image_policy == "never":
            return False
        if ai.image_policy == "always" or requested or trouble or (first and judged_step):
            return True
        informative = sum(1 for e in tree.elements if e.text or e.desc or e.clickable or e.editable)
        return informative < ai.rich_tree_min_elements

    def _image_scale(self, obs: Observation) -> float:
        """Pixels do aparelho por pixel do espaço de coordenadas que o modelo enxerga."""
        return max(1.0, max(obs.width, obs.height) / self.cfg.file.ai.screenshot_max_side)

    def _shadow_compare(self, rr: "_RecipeRun", obs: Observation, decision: Decision) -> None:
        """Modo sombra: a receita diz o que FARIA; só a IA age. O veredito é da EXECUÇÃO da etapa (`_after_step`).
        Compara-se só o que a receita grava: o que ela não guarda não pode separar o caminho dela do da IA."""
        assert rr.replayer is not None
        if decision.tool in READ_ONLY:
            return          # olhar a tela não é caminho: `distill` nunca grava leitura, então não há o que comparar
        if decision.tool == "step_done" and rr.replayer.idx == 0:
            return          # já estava no estado final (K-004): não houve caminho — nem concordância nem divergência
        try:
            would = rr.replayer.next(obs.tree)
        except RecipeDiverged as exc:
            rr.diverged = str(exc)
            return
        if would is None:
            agreed = decision.tool == "step_done"                # a receita acabou: só falta declarar pronta
        elif would.tool == "scroll":
            # Da rolagem a receita grava só a DIREÇÃO (`distill`), nunca o contêiner: reproduzida, rola a tela
            # (`element_id: None`), e a IA quase sempre rola a lista (158 de 174 rolagens da IA no central, 28/09).
            # Comparar o contêiner marcava divergência justamente quando a IA fazia o caminho da receita.
            agreed = decision.tool == "scroll" and decision.args.get("direction") == would.args.get("direction")
        else:
            # O texto entra na conta: com o mesmo campo e outro texto, a receita digitaria o que a IA não digitou.
            agreed = (would.tool == decision.tool and would.args.get("element_id") == decision.args.get("element_id")
                      and (would.tool != "type_text" or would.args.get("text") == decision.args.get("text")))
        if not agreed:
            rr.diverged = "a IA escolheu outra ação"

    def _screen(self, obs: Observation, *, with_image: bool = True, protect: tuple[str, ...] = (),
               boost: tuple[str, ...] = ()) -> tuple[ScreenInput, float]:
        jpeg = obs.jpeg if with_image else None
        # com ou sem imagem, x,y do modelo vivem no mesmo espaço reduzido — a MESMA conta de quem codificou a imagem
        w, h, scale = dimensoes_do_modelo(obs.width, obs.height, self.cfg.file.ai.screenshot_max_side)
        if jpeg:
            with Image.open(io.BytesIO(jpeg)) as img:     # só o cabeçalho: a observação já pode vir no tamanho certo
                pronta = img.size == (w, h)
            if not pronta:                            # imagem cheia (observação antiga): reduz aqui, como antes
                buf = io.BytesIO()
                Image.open(io.BytesIO(jpeg)).resize((w, h)).save(buf, "JPEG", quality=72)
                jpeg = buf.getvalue()
        lines = obs.tree.prompt_lines(self.cfg.file.ai.max_hierarchy_elements, scale, protect=protect, boost=boost)
        return ScreenInput(width=w, height=h, jpeg=jpeg, elements=lines,
                           package=obs.package, sensitive=obs.sensitive, tree=obs.tree), scale

    # ------------------------------------------------------------------ etapa
    async def run_step(self, *, run: Any, objective: Any, step: StepDTO, attempt_id: str, rt: DeviceRuntime,
                       app: AppContext, account_label: str | None, remaining: list[str],
                       stop_reason: Callable[[], str | None], resumed_after_manual: bool) -> StepOutcome:
        """Etapa com receitas: procura a receita, executa, e depois contabiliza o replay ou aprende com a IA."""
        mode = self.cfg.file.ai.recipes
        self._effects.pop(step.id, None)
        rr = _RecipeRun(mode=mode)
        fired_at_entry, _ = self.repo.commit_state(step.id)
        if mode != "off" and app.package and not fired_at_entry:
            try:
                rr.app_version = await self.devices.app_version(rt, app.package)
                rr.step_hash = self.repo.step_row(step.id)["template_hash"]
                rr.variables = {**loads(objective["parameters"], {}), "instance_id": rt.id, "run_id": run["id"],
                                "account_label": account_label or "", **step.variables}
                rr.signature = self._installed_signature(rt.id, app.package)
                rr.variant = await self.devices.variant_of(rt)
                rr.row = self.recipes.find(app.package, rr.app_version, rr.step_hash,
                                           signature=rr.signature, variant=rr.variant)
                if rr.row is not None:
                    rr.replayer = self.recipes.replayer(rr.row, rr.variables)
                    if rr.row["status"] == "candidate":
                        rr.mode = "shadow"      # em prova: a IA decide a etapa e a receita só é comparada
            except Exception as exc:  # noqa: BLE001 - receita é otimização: nunca derruba a etapa
                log.warning("%s: receitas indisponíveis nesta etapa: %s", rt.id, exc)
                rr = _RecipeRun(mode="off")
        fechada = False
        try:
            outcome = await self._run_step(run=run, objective=objective, step=step, attempt_id=attempt_id, rt=rt,
                                           app=app, account_label=account_label, remaining=remaining,
                                           stop_reason=stop_reason, resumed_after_manual=resumed_after_manual, rr=rr)
            fechada = True
        finally:
            self._registrar_estrategia(attempt_id, rr)
            if not fechada:
                # Saiu por exceção (o scheduler a transforma em falha): o aprendizado sabe da tentativa do mesmo jeito,
                # uma vez, e sem desfecho que pareça sucesso.
                self._fechar_tentativa(run, objective, step, attempt_id, rt, app, rr, None)
        if outcome.outcome in (Outcome.succeeded, Outcome.failed, Outcome.uncertain, Outcome.cancelled):
            self._reabertas_por_anr.discard(step.id)     # desfecho final: a etapa não volta a rodar com este id
        try:
            self._after_step(rr, outcome, run["id"], rt.id, step, attempt_id, app)
        except Exception:  # noqa: BLE001
            log.exception("%s: contabilidade da receita falhou", rt.id)
        self._settle_effect(step, outcome)
        if outcome.outcome == Outcome.succeeded:
            self._remember_screen(objective, step, rt, outcome, app)
        self._fechar_tentativa(run, objective, step, attempt_id, rt, app, rr, outcome)
        return outcome

    def _fechar_tentativa(self, run: Row, objective: Row, step: StepDTO, attempt_id: str, rt: DeviceRuntime,
                          app: AppContext, rr: "_RecipeRun", outcome: StepOutcome | None) -> None:
        """Costura `ao_fechar_tentativa` (ADR-054, A2): UMA vez por tentativa, com o que já está em memória — a última
        árvore observada inclusive (sem dump extra). Quem observa decide o que aproveita; a tela sensível e o
        aparelho-loja vão marcados, e nada daqui volta para a etapa."""
        try:
            ultima = getattr(rt, "last_tree", None)
            fechamento = FechamentoDeTentativa(
                attempt_id=attempt_id, step_id=step.id, run_id=str(run["id"]), objective_id=str(objective["id"]),
                instance_id=rt.id, profile_id=_perfil_do_objetivo(objective), app_package=app.package,
                capability=step.capability, template_hash=rr.step_hash,
                status=outcome.outcome.value if outcome is not None else SAIU_POR_EXCECAO,
                verified=outcome is not None and outcome.outcome == Outcome.succeeded,
                arvore=ultima if isinstance(ultima, UiTree) else None, loja=bool(getattr(rt, "store", False)),
                simulated=bool(run["simulated"]))
        except Exception:  # noqa: BLE001 - aprendizado é registro: nunca derruba a etapa já decidida
            log.exception("%s: fechamento da tentativa %s não chegou ao aprendizado", rt.id, attempt_id)
            return
        avisar(self.costuras.ao_fechar_tentativa, fechamento)

    def _licoes_da_tentativa(self, run: Row, objective: Row, step: StepDTO, attempt_id: str, app: AppContext,
                             rr: "_RecipeRun") -> list[str]:
        """Costura `licoes_para` (ADR-054, A2): as lições medidas do ator para ESTA etapa, pedidas uma vez por
        tentativa, no instante da primeira consulta ao ator — a etapa que a receita conduz sem divergir nunca chega
        aqui, então não gera lição, exposição nem custo. Falha = nenhuma lição (o prompt sai como o de antes)."""
        try:
            passo = rr.step_hash or str(self.repo.step_row(step.id)["template_hash"] or "")
            pedido = PedidoDeLicoes(papel="actor", unidade=f"step:{step.id}", run_id=str(run["id"]),
                                    app_package=app.package or "", capability=step.capability or "*", step_hash=passo,
                                    simulated=bool(run["simulated"]), objective_id=str(objective["id"]),
                                    step_id=step.id, attempt_id=attempt_id)
        except Exception:  # noqa: BLE001 - lição é contexto opcional
            log.exception("etapa %s: pedido de lições não montado (seguindo sem lição)", step.id)
            return []
        return pedir_licoes(self.costuras, pedido)

    def _registrar_estrategia(self, attempt_id: str, rr: "_RecipeRun") -> None:
        """Trilha da 045: a cadeia exercida e a receita reproduzida (só quando ela foi de fato consultada). Vale
        também quando a tentativa sai por exceção — o que ela já fez aconteceu."""
        receita = rr.row["id"] if rr.row is not None and StrategyKind.recipe.value in rr.exercised else None
        try:
            self.repo.note_attempt_strategy(attempt_id, ">".join(rr.exercised) or None, receita)
        except Exception:  # noqa: BLE001 - trilha é registro: nunca derruba a etapa nem esconde o erro dela
            log.exception("tentativa %s: estratégia não registrada", attempt_id)

    def _remember_screen(self, objective: Any, step: StepDTO, rt: DeviceRuntime, outcome: StepOutcome,
                         app: AppContext | None = None) -> None:
        """Decisão do dono (24/09): o que o perfil VIU vira memória dele. A tela é a da comprovação da etapa, que o
        executor já tinha observado — sem dump extra e sem IA. Tela sensível e aparelho-loja nunca entram."""
        tree = getattr(rt, "last_tree", None)
        if self.social is None or tree is None or tree.sensitive or getattr(rt, "store", False):
            return
        try:
            profile_id = objective["profile_id"]
        except (KeyError, IndexError, TypeError):
            profile_id = None
        if not profile_id:
            return
        try:
            self.social.remember_screen(profile_id, step_title=step.title, bindings=step.bindings,
                                        elements=tree.elements, items=outcome.items,
                                        app_label=(app.name or app.package or "app") if app else "app",
                                        app_id=app.id if app else None)
        except Exception:  # noqa: BLE001 - memória é enriquecimento: nunca derruba a etapa já comprovada
            log.exception("%s: a tela da etapa %s não virou memória", rt.id, step.key)

    # ------------------------------------------------------------------ histórico social do efeito
    def _open_effect(self, objective: Any, step: StepDTO, rt: DeviceRuntime, cap: Any,
                     app_id: str | None = None) -> None:
        """Chamado no instante do commit. Efeito disparado é efeito que conta, mesmo sem resultado observado."""
        if self.social is None or cap is None or not cap.interaction_type or step.id in self._effects:
            return
        profile_id = objective["profile_id"] or None
        if not profile_id:
            return
        try:
            interaction_id = self.social.open_effect(
                profile_id, capability=cap.key, interaction_type=cap.interaction_type, bindings=step.bindings,
                run_id=step.run_id, objective_id=step.objective_id, step_id=step.id, instance_id=rt.id,
                draft_meta=ler_rascunho(self.repo.db, step.id), app_id=app_id,
                # O alvo que a AÇÃO declara (ADR-055): em curtir e comentar, o autor da publicação. Sem isto o
                # histórico adivinhava por `username` e gravava `counterparty` NULL em toda curtida e comentário.
                counterparty=contraparte(cap, step.bindings))
            self._effects[step.id] = (profile_id, interaction_id)
            if self.approvals is not None:
                self.approvals.link_interaction(step.id, interaction_id)
        except Exception:  # noqa: BLE001 - histórico nunca derruba a etapa em andamento
            log.exception("%s: não foi possível registrar o efeito no histórico", rt.id)

    def _settle_effect(self, step: StepDTO, outcome: StepOutcome) -> None:
        aberto = self._effects.pop(step.id, None)
        if aberto is None or self.social is None:
            return
        profile_id, interaction_id = aberto
        # `retry` e `yielded` deixam a interação em aberto de propósito: a etapa ainda vai continuar.
        if outcome.outcome in (Outcome.retry, Outcome.yielded):
            self._effects[step.id] = aberto
            return
        try:
            self.social.settle_effect(profile_id, interaction_id, outcome=outcome.outcome.value,
                                      evidence=outcome.detail)
        except Exception:  # noqa: BLE001
            log.exception("não foi possível fechar a interação %s", interaction_id)

    def _after_step(self, rr: "_RecipeRun", outcome: StepOutcome, run_id: str, iid: str, step: StepDTO,
                    attempt_id: str, app: AppContext) -> None:
        if rr.mode == "off" or outcome.outcome in (Outcome.yielded, Outcome.cancelled):
            return                                     # tentativa interrompida (cedida, cancelada): não é veredito
        ok = outcome.outcome == Outcome.succeeded
        replayed = rr.mode == "replay" and rr.replayer is not None and rr.replayer.done_actions + int(rr.completed_by_recipe) > 0
        # `retry` não é veredito sobre a receita: só o desfecho da etapa (ou a divergência) entra na conta — senão um
        # aparelho com problema próprio poria em quarentena, sozinho, uma receita que funciona nos demais. Defeito do
        # plano também não é veredito sobre ela.
        veredito = not (outcome.plan_defect or outcome.outcome == Outcome.retry or outcome.trava_da_conta)
        na_receita = rr.mode == "replay" and rr.row is not None and veredito and (replayed or rr.diverged)
        if rr.mode == "replay" and rr.row is not None and not na_receita:
            # Funil de receitas (C5) contado por TENTATIVA, nas três pontas: a consulta (`RecipeStore.find`) e o
            # retorno à IA (`contar_retorno_ia`) já eram por tentativa; a reprodução só saía com veredito da etapa,
            # e uma nova tentativa virava duas consultas para uma reprodução — sumindo com a divergência da 1ª
            # (revisão F8). Agora toda receita ENCONTRADA desemboca em exatamente um veredito de reprodução por
            # tentativa: `ok` só se a tentativa terminou comprovada pela receita sozinha; senão `divergiu` (ela não
            # levou a tentativa até o fim). Aqui é o mesmo contador de `RecipeStore.result`, sem mexer na quarentena
            # (que continua só com veredito da etapa). Interrompida (cedida, cancelada) não conta; modo sombra não
            # reproduz, então também não.
            metricas.contar("receita.reproducao", resultado="ok" if (ok and replayed and not rr.diverged) else "divergiu")
        if rr.mode == "shadow" and rr.row is not None:
            self._veredito_da_sombra(rr, ok, run_id, iid, step)
        if not veredito:
            return
        repo = self.repo
        if na_receita:
            clean = ok and not rr.diverged
            quarantined = self.recipes.result(rr.row["id"], clean)
            driven = "recipe" if clean else "recipe+ai"
            repo.db.execute("UPDATE steps SET driven_by=? WHERE id=?", (driven, step.id))
            if clean:
                repo.decision(f"{iid} · {step.title}: receita v{rr.row['version']} reproduzida (0 decisões de IA)",
                              run_id=run_id, instance_id=iid, step_id=step.id)
            if quarantined:
                repo.decision(f"{iid} · {step.title}: receita v{rr.row['version']} em quarentena após falhas seguidas; "
                              "a etapa será reaprendida com a IA", run_id=run_id, instance_id=iid, step_id=step.id)
            return
        repo.db.execute("UPDATE steps SET driven_by='ai' WHERE id=?", (step.id,))
        # A candidata que divergiu é trocada pelo caminho que a IA acabou de comprovar: sem isto, uma IA que passou a
        # fazer outro caminho deixaria a etapa presa para sempre numa candidata que nunca concorda (e candidata não
        # reproduz, então nem a quarentena a tiraria dali).
        substitui = rr.row["id"] if rr.row is not None and rr.row["status"] == "candidate" and rr.diverged else None
        if not ok or (rr.row is not None and substitui is None) or not (app.package and rr.app_version and rr.step_hash):
            return
        rows = repo.db.query("SELECT * FROM actions WHERE attempt_id=? ORDER BY seq", (attempt_id,))
        actions, why = distill(rows, rr.variables)
        if actions is None:
            log.info("%s: etapa %s não virou receita: %s", iid, step.key, why)
            return
        n = self.cfg.file.ai.recipes_promote_after
        rid = self.recipes.save(package=app.package, app_version=rr.app_version, step_hash=rr.step_hash,
                                step_key=step.key, actions=actions, learned_from=step.id,
                                signature=rr.signature, variant=rr.variant, candidate=n > 0, replaces=substitui)
        if rid and n > 0:
            no_lugar = f", no lugar da v{rr.row['version']}, que divergiu" if substitui else ""
            repo.decision(f"{iid} · {step.title}: receita aprendida como candidata ({len(actions)} ação(ões)){no_lugar}"
                          f" — a IA segue conduzindo esta etapa e a receita só é comparada; vira ativa depois de {n} "
                          "execução(ões) seguidas em que a IA fizer exatamente o caminho dela",
                          run_id=run_id, instance_id=iid, step_id=step.id)
        elif rid:
            repo.decision(f"{iid} · {step.title}: receita aprendida ({len(actions)} ação(ões)) — as próximas execuções "
                          "desta etapa dispensam a IA enquanto a tela casar", run_id=run_id, instance_id=iid, step_id=step.id)
        elif substitui:
            log.info("%s: etapa %s: candidata v%s não trocada (a IA comprovou o mesmo caminho, ou a chave já tem "
                     "ativa); segue em prova", iid, step.key, rr.row["version"])

    def _veredito_da_sombra(self, rr: "_RecipeRun", ok: bool, run_id: str, iid: str, step: StepDTO) -> None:
        """Uma execução da etapa, um veredito sobre a receita comparada. Concordar = a IA fez, uma a uma, todas as
        ações da receita e nada além (nem declarou pronta antes do fim dela), e a etapa foi comprovada — pela IA ou
        pela prova local depois do efeito, que encerra a etapa sem outra decisão. A divergência conta sempre que foi
        vista — também numa tentativa que vai se repetir: segurar a promoção é o lado seguro. Sem nenhuma comparação
        conclusiva (a etapa falhou antes, ou o aparelho já estava no estado final) não há veredito."""
        rep = rr.replayer
        if ok and not rr.diverged and rep is not None and 0 < rep.idx < len(rep.actions):
            # Comprovada no meio da receita: reproduzida, ela faria ações a mais depois do fim da etapa. É outro
            # caminho — e marcado como divergência para o caminho da IA poder substituí-la (`_after_step`); sem isso
            # a candidata ficaria sem veredito para sempre, nem promovida nem trocada.
            rr.diverged = f"a etapa se comprovou na ação {rep.idx} de {len(rep.actions)} da receita"
        if rr.diverged:
            concordou = False
        elif ok and rep is not None and rep.exhausted:
            concordou = True
        else:
            return
        promovida = self.recipes.shadow(rr.row["id"], concordou, promote_after=self.cfg.file.ai.recipes_promote_after)
        if promovida:
            self.repo.decision(f"{iid} · {step.title}: receita v{rr.row['version']} promovida a ativa — a IA fez "
                               "exatamente o caminho dela em execuções seguidas; as próximas execuções desta etapa "
                               "dispensam a IA enquanto a tela casar", run_id=run_id, instance_id=iid, step_id=step.id)

    def _installed_signature(self, instance_id: str, package: str) -> str:
        """Assinatura do APK que está NESTE aparelho, quando ele veio de uma release catalogada.

        Versão igual com assinatura diferente não é o mesmo app: a receita aprendida num não vale no outro.
        """
        return self.repo.db.scalar(
            "SELECT r.signature_sha256 FROM device_app_state d JOIN app_releases r ON r.id = d.installed_release_id"
            " WHERE d.instance_id=? AND d.package_name=?", (instance_id, package)) or ""

    def _trava_na_tela(self, tree: UiTree, pacote: str | None) -> ContaTravada | None:
        """O detector único de conta travada (ADR-055), com o conhecimento de telas do app que está NA TELA (não o da
        etapa: a verificação é desenhada pelo app da conta). Conhecimento inválido não derruba a etapa: fica com os
        sinais genéricos, que cobrem as frases do Instagram (`test_sensitive_input` confere que concordam)."""
        k: telas_do_app.ConhecimentoDeTelas | None = None
        if pacote and pacote.replace(".", "").replace("_", "").isalnum():
            try:
                k = telas_do_app.da_pasta(CONHECIMENTO_DE_APPS / pacote)
            except telas_do_app.ConhecimentoInvalido as exc:
                log.warning("conhecimento de telas de %s inválido; só os sinais genéricos de conta travada: %s",
                            pacote, exc)
        return telas_do_app.detectar_conta_travada(tree, k)

    def _sessao_desmentida(self, instance_id: str, package: str | None, kind: str, detail: str, *,
                           subtipo: str | None = None) -> None:
        """A tela contradisse o que o painel afirmava sobre a sessão. O cache passa a dizer a verdade.

        O estado de sessão sempre foi um cache do que se observou uma vez — e que nunca era corrigido por quem
        estava olhando a tela DEPOIS. O painel mostrava "Conectado" para uma conta presa num desafio, a porta de
        sessão deixava despachar, e como o status seguia `session_ready` o autenticador automático nunca era
        acionado: a etapa parava em `waiting_user` pedindo login manual com a senha guardada no cofre.
        """
        if self.on_auth_needed is None:
            return
        if session_provider_of(package) is None:
            # Só a sessão de app com conta gerenciada (provedor de sessão no registro de apps). Uma tela de login do
            # QA Messenger num aparelho com perfil vinculado não diz nada sobre a conta do Instagram — e marcá-la de
            # `auth_required` gastaria, sozinha, uma das tentativas de autenticação automática daquele perfil.
            return
        try:
            if subtipo is None:
                self.on_auth_needed(instance_id, kind, detail)
            else:
                self.on_auth_needed(instance_id, kind, detail, subtipo=subtipo)
        except Exception:  # noqa: BLE001 - corrigir o cache nunca pode derrubar a etapa
            log.exception("%s: falha ao atualizar o estado de sessão do perfil", instance_id)

    async def _run_step(self, *, run: Any, objective: Any, step: StepDTO, attempt_id: str, rt: DeviceRuntime,
                        app: AppContext, account_label: str | None, remaining: list[str],
                        stop_reason: Callable[[], str | None], resumed_after_manual: bool,
                        rr: "_RecipeRun") -> StepOutcome:
        s = self.get_settings()
        repo = self.repo
        run_id, oid, iid = run["id"], objective["id"], rt.id
        params: dict[str, str] = {**loads(objective["parameters"], {}), **step.variables}   # inclui {item} da cópia
        collecting = step.postcondition.kind == "items_collected"
        cap = capability_of(app.package, step.capability)      # None em app sem catálogo: nada muda
        # A legenda da publicação alvo (`caption_contains`), quando o pedido a citou: vazio = post por posição, como
        # sempre. Com ela, a pós-condição exige o texto na tela e o toque de efeito só vale no cartão que o traz.
        cartao = guardas_do_cartao(cap.card_guard, step.bindings) if cap else ()
        collected: list[str] | None = None
        empty_collects = 0
        inicio = time.monotonic()
        deadline = inicio + step.timeout_s
        call_timeout = float(s.driver_call_timeout_s)
        fired, unknown = repo.commit_state(step.id)
        history: list[str] = []
        if step.attempts > 1:
            anterior = repo.db.one("SELECT error FROM attempts WHERE step_id=? AND status='failed' AND error IS NOT NULL"
                                   " ORDER BY number DESC LIMIT 1", (step.id,))
            if anterior and anterior["error"]:
                history.append(f"(tentativa anterior desta etapa falhou) {str(anterior['error'])[:400]} — "
                               "não repita o mesmo caminho; procure outro.")
        if fired:
            history.append("(tentativa anterior) a ação com efeito externo desta etapa JÁ foi disparada; "
                           "resultado " + ("desconhecido" if unknown else "registrado") + ".")
        if cartao and cap and cap.card_control:
            # A legenda não chega ao ator por outro caminho (OPEN_COMMENTS não tem `target` nem guarda de texto): sem
            # esta linha ele só saberia qual balão é o certo depois de uma recusa — uma decisão a mais num aparelho
            # que já satura a CPU (android-06, r-20260928195344-02ee9e).
            history.append("(executor) a publicação desta etapa é a da legenda com "
                           + ", ".join(f'"{c}"' for c in cartao)
                           + ": o toque em " + " ou ".join(f"'{c}'" for c in cap.card_control)
                           + " só vale no cartão dela, logo acima da legenda.")
        need = step.postcondition.required_delivery_level

        # Item 7.6: só os parâmetros QUE ESTA ETAPA USA, não o objetivo inteiro (que pode ter dezenas de
        # aparelhos/itens de `for_each` resolvidos). Com catálogo, a capability declara exatamente quais —
        # `cap.bindings` (obrigatórios) e `cap.optional_bindings`, mais o que a própria etapa gravou em
        # `step.variables` (ex.: `{item}` da cópia de `for_each`). Sem catálogo (plano livre) mantém tudo: não
        # há como saber de antemão o que o texto livre do plano vai referenciar.
        ctx_params = actor_params(params, cap, step.variables)
        # Dados da persona deste aparelho (ADR-040): o ator conhece só os NOMES; a senha de uma conta sai do cofre na
        # hora de digitar, resolvida pelo perfil do OBJETIVO. `senha_do_app` diz se há senha utilizável para o app
        # DESTA etapa (é o que faz a tela de senha ser só mais uma tela). Endereços abríveis: os do comando e os
        # sites das contas de portal da persona.
        profile_id = objective["profile_id"] or None
        dados = list(available_data(self.dados, profile_id))
        senha_do_app = typable_secret_for(self.dados, profile_id, app.package)
        urls_permitidas = urls_da_pessoa(run["command"])
        hosts_das_contas = set(account_hosts(self.dados, profile_id))

        def ctx_for() -> StepContext:
            desc = step.postcondition.description + (f" (nível de entrega exigido: {need.value})" if need else "")
            return StepContext(run_id=run_id, instance_id=iid, objective_summary=run["command"], parameters=ctx_params,
                               step_key=step.key, step_title=step.title, step_goal=step.goal,
                               side_effect=step.side_effect, commit_done=fired, commit_guard=step.commit_guard,
                               precondition=step.precondition, postcondition_description=desc, remaining_steps=remaining,
                               app=app, account_label=account_label, required_delivery_level=need.value if need else None,
                               resumed_after_manual_control=resumed_after_manual, available_data=dados)

        async def call(fn: Callable[..., Any], *args: Any) -> Any:
            return await rt.executor.run(fn, *args, timeout=call_timeout, label=getattr(fn, "__name__", "driver"))

        async def quick_tree() -> UiTree:
            xml = await reler_se_ocupada(
                lambda: rt.executor.run(rt.io.page_source, timeout=call_timeout, label="hierarquia"),
                prazo=deadline, quem=iid)
            tree = self.devices.arvore(rt, xml)      # mesmos critérios de tela sensível da observação completa
            # ADR-055: a leitura DENTRO de uma ferramenta também passa pelo detector. A rolagem fecha com "voltar" a
            # janela que entra por cima, e a coleta segue arrastando: sem isto, a verificação que aparecesse no meio
            # delas levaria um gesto antes de o laço voltar a observar.
            pacote = next((p for p in tree.packages if p != "com.android.systemui"), None)
            if (trava := self._trava_na_tela(tree, pacote)) is not None:
                raise TelaDeContaTravada(trava, pacote)
            return tree

        async def evidence(obs: Observation | None, note: str, kind: str = "screenshot") -> None:
            # `add_evidence_async`: a ESCRITA do arquivo sai do laço de eventos (item 5.7). Em disco local isso
            # era inofensivo; com o storage apontado para um bucket, gravar aqui dentro travaria o scheduler
            # inteiro a cada captura de tela.
            if obs is None:
                await repo.add_evidence_async(run_id=run_id, instance_id=iid, step_id=step.id, attempt_id=attempt_id,
                                              kind="text", note=note)
                return
            data = obs.jpeg
            if data is None and not obs.sensitive and obs.image_omitted == "policy":
                # A observação saiu só com a árvore (a imagem não ia ao modelo). A evidência adquire a SUA, agora, com
                # o próprio horário na nota — é só evidência, nunca fonte de coordenada (adendo v0.20, C1).
                try:
                    tardia = await self.devices.imagem_tardia(rt, timeout=call_timeout)
                except DriverError as exc:
                    await repo.add_evidence_async(run_id=run_id, instance_id=iid, step_id=step.id,
                                                  attempt_id=attempt_id, kind="text",
                                                  note=note + f" (imagem não adquirida: {type(exc).__name__})")
                    return
                if tardia is not None:
                    data, quando = tardia
                    note += f" (imagem adquirida depois da observação, às {quando})"
            if obs.sensitive or data is None:
                await repo.add_evidence_async(run_id=run_id, instance_id=iid, step_id=step.id, attempt_id=attempt_id,
                                              kind=kind, note=note + " (tela sensível: captura omitida)", redacted=True)
            else:
                await repo.add_evidence_async(run_id=run_id, instance_id=iid, step_id=step.id, attempt_id=attempt_id,
                                              kind=kind, note=note, data=data)

        async def fail_or_retry(detail: str, obs: Observation | None = None) -> StepOutcome:
            await evidence(obs, f"Falha: {detail}")
            if step.side_effect and fired:
                return StepOutcome(Outcome.uncertain, detail)
            return StepOutcome(Outcome.retry if step.attempts < step.max_attempts else Outcome.failed, detail)

        async def parar_na_trava(trava: ContaTravada, pacote: str | None) -> StepOutcome:
            """ADR-055: a tela de verificação encerra a etapa SEM tocar, teclar nem reabrir — nem a receita nem o ator
            chegam a vê-la. O desfecho é `auth_challenge` com o subtipo e o trecho, na tentativa (o `detail`) e no
            evento; `conta_travada` bloqueia o perfil (quem aplica é o AppState, pela regra do perfil)."""
            motivo = motivo_da_trava(trava)
            # Evidência em TEXTO: a imagem não ajuda ninguém a decidir, e quando só o conhecimento do app reconheceu a
            # tela a árvore não é "sensível" — `imagem_tardia` fotografaria a verificação (com o nome da pessoa).
            await evidence(None, f"Parada sem tocar: {motivo}")
            texto = f"{iid} · {step.title}: {motivo}"
            repo.bus.emit("decision", texto, level="warn", run_id=run_id, instance_id=iid, step_id=step.id,
                          data={"text": texto, "kind": "auth_challenge", "subtipo": trava.subtipo,
                                "trecho": trava.trecho, "tela": trava.origem, "package": pacote})
            self._sessao_desmentida(iid, pacote or app.package, "auth_challenge", motivo, subtipo=trava.subtipo)
            if step.side_effect and fired:
                return StepOutcome(Outcome.uncertain, motivo, trava_da_conta=True)
            return StepOutcome(Outcome.waiting_user, motivo, trava_da_conta=True,
                               needs=_NECESSIDADE_DA_TRAVA.get(trava.subtipo, _needs_for("challenge")))

        for tries in range(3):                     # logo após ligar/acordar o Android às vezes recusa a 1ª sessão (visto:
            if await self.devices.ensure_automation(rt):   # `adb shell settings …` exit 20) e aceita segundos depois
                break
            if tries == 2 or stop_reason():
                return StepOutcome(Outcome.waiting_user, f"Sessão de automação indisponível: {rt.automation.detail}",
                                   needs="Verifique o Appium/UiAutomator2 (Diagnóstico) e retome este item.")
            await asyncio.sleep(float(self.get_settings().session_retry_wait_s))

        last_obs: Observation | None = None
        last_sig: tuple[str, str] | None = None
        same_count = 0
        sigs: list[tuple[str, str, str]] = []                  # (tela exata, tela estrutural, ação)
        errors_in_row = 0
        declared: StepDone | None = None
        max_actions = int(s.max_actions_per_step)
        ai_cfg = self.cfg.file.ai
        judged_step = step.postcondition.kind == "model_judged" or need is not None
        decisions = 0
        image_requested = False
        # Lições medidas do ator (ADR-054): pedidas na PRIMEIRA consulta ao ator desta tentativa e reusadas nas
        # seguintes. Preguiçoso de propósito — a tentativa que a receita leva até o fim nunca pede.
        licoes: list[str] | None = None
        # Modelo forte (escalonamento) onde errar custa caro ou o barato já tropeçou: etapa com efeito externo
        # (conforme o risco, ver `side_effect_tier`), nova tentativa da mesma etapa, erros seguidos ou ação
        # repetida na mesma tela.
        tier_efeito, motivo_efeito = side_effect_tier(step, cap, ai_cfg.strong_model_for_side_effect)
        base_tier = 1 if (tier_efeito or step.attempts > 1) else 0
        escalated = False                     # a linha do escalonamento sai UMA vez por etapa, não por decisão
        # Item 7.8 (piso de conteúdo): o provedor de `decide` É o do `.env`/YAML, não o desta instância de etapa —
        # ele não muda no meio de uma execução, então resolver uma vez aqui é o mesmo resultado de resolver a cada
        # volta do laço, sem pagar a travessia de config de novo. Só interessa quando NÃO é Anthropic: é o
        # endpoint local (pouco contexto, resposta pode "esquecer" a árvore atual) quem inventa um `element_id`
        # de uma tela que já passou — a Anthropic recebe a árvore inteira e não tropeça nisto.
        decide_kind = self.cfg.ai_role("decide").kind
        forcar_tier_1 = False                  # a decisão anterior foi descartada pelo piso: a PRÓXIMA sobe de tier
        tier = base_tier                       # só existe de verdade dentro do laço (decisão fresca); este é o
                                                # valor antes de qualquer decisão — nunca lido por uma de receita
        # Pacote "anr" (r-20260928165254-e31953 e r-20260928195344-02ee9e): as mortes do app alvo por ANR contam pela
        # ETAPA, não pela tentativa. Lá cada tentativa acabava por "Tempo da etapa esgotado (180s)" e a seguinte
        # recomeçava a janela do zero: a morte da tentativa N nunca era vista na N+1, "2ª morte → falha" não disparava
        # e cada tentativa gastava a sua reabertura. A janela vai do início da etapa (`steps.started_at`, gravado na
        # 1ª tentativa); com o controle devolvido por uma pessoa, recomeça — ela mexeu no aparelho.
        desde_etapa = inicio if resumed_after_manual else _inicio_monotonico(step.started_at, inicio)
        if resumed_after_manual:
            self._reabertas_por_anr.discard(step.id)
        # As mortes desta etapa já lidas nesta tentativa (cada leitura devolve TODAS as da janela), e quando reconferir
        # o `exit-info` — cada leitura é uma chamada de adb num convidado que pode estar sem CPU. Confere quando o app
        # alvo não está na frente E (quem está na frente mudou OU algo foi feito no aparelho desde a última
        # observação): abrir o app e ele morrer na partida deixa launcher → launcher.
        mortes_por_anr: set[tuple[str, int]] = set()
        fora_anterior: str | None = None
        agiu = True

        def com_anr(detalhe: str) -> str:
            """O prazo que vence DEPOIS de um ANR contado nesta etapa diz o ANR: "tempo esgotado" sozinho, como na
            r-20260928195344-02ee9e, mandava procurar a lentidão em outro lugar. E o aviso vai para o aparelho."""
            if not (mortes_por_anr and app.package):
                return detalhe
            motivo = motivo_de_anr(app.name or app.package, iid)
            self._avisar_anr(rt, motivo)
            return f"{detalhe.rstrip('.')}; {motivo}."

        for _ in range(max_actions + 1):
            # ---------- ponto seguro
            why = stop_reason()
            if why:
                return StepOutcome(Outcome.cancelled if why == "cancel" else Outcome.yielded, why)
            if time.monotonic() > deadline:
                return await fail_or_retry(com_anr(f"Tempo da etapa esgotado ({step.timeout_s}s)."), last_obs)
            # ---------- observar
            # Árvore primeiro; a imagem só se esta volta for mandá-la ao modelo (adendo v0.20, C1). Tudo o que pesa
            # na decisão além da árvore já se sabe aqui. Com a receita reproduzindo, quem decide é ela, pela árvore:
            # a imagem só vem se ela divergir e a IA precisar (`completar_imagem`, mais abaixo).
            receita_decide = rr.mode == "replay" and not rr.diverged and not fired and rr.replayer is not None
            pede = dict(judged_step=judged_step, first=decisions == 0, trouble=errors_in_row >= 1 or same_count >= 1,
                        requested=image_requested)
            try:
                obs = last_obs = await reler_se_ocupada(
                    lambda: self.devices.observe(rt, timeout=call_timeout, lado_max=ai_cfg.screenshot_max_side,
                                                 imagem=lambda t: not receita_decide and self._want_image(t, **pede)),
                    prazo=deadline, quem=iid)
            except DriverTimeout as exc:
                return await self._stuck(rt, step, fired, str(exc))
            except (DriverBusy, FalhaDeLeitura) as exc:
                # A UI seguiu ocupada, ou a leitura pelo adb/agente seguiu falhando, depois das releituras. A sessão
                # continua viva (ou nem participou) — derrubá-la aqui é o que custava 27–80 s por vez nas execuções de
                # 28/09. Conta como erro e volta a observar, até o limite de sempre.
                errors_in_row += 1
                if errors_in_row >= 3:
                    return await fail_or_retry(f"A interface do aparelho seguiu ocupada: {exc}"
                                               if isinstance(exc, DriverBusy)
                                               else f"A leitura da tela seguiu falhando: {exc}")
                continue
            except DriverError as exc:
                # O que sobra veio do Appium (a hierarquia) e não é UI ocupada: sessão ou instrumentação morta,
                # conexão recusada, sessão indisponível — ou um erro dele que não se sabe ler. Recria em todos: nada
                # mais confere se a sessão está viva (`ensure_automation` confia no "pronta"), e uma sessão morta que
                # escapasse de uma lista de marcadores ("socket hang up" no backend.log de 18/09) nunca voltaria.
                errors_in_row += 1
                self.devices.invalidate_automation(rt, str(exc))
                if errors_in_row >= 3 or not await self.devices.ensure_automation(rt):
                    return await fail_or_retry(f"Não foi possível observar a tela: {exc}")
                continue
            # ---------- conta travada? (ADR-055) ANTES da reabertura por ANR, da receita e do ator.
            # "Confirm you’re human" só contava como desafio com campo de texto na tela; sem campo, decidiam a receita
            # e o ator — instruído a dispensar "diálogos inesperados" (tocar em "Continue", "Get support", voltar).
            # E quando contava, o executor gravava `auth_required`: o login automático reabria o app sobre a conta
            # travada, e o bloqueio do perfil (ADR-029) nunca rodava por aqui. Cinco das oito contas se perderam assim.
            if (trava := self._trava_na_tela(obs.tree, obs.package)) is not None:
                return await parar_na_trava(trava, obs.package)
            # ---------- o app alvo morreu por ANR? (pacote "anr")
            # Com `hide_error_dialogs=1` o ANR não tem diálogo: o sistema fecha o app e o launcher volta. A IA só via
            # "launcher" e reabria; a partida a frio dava outro ANR (5 mortes do Instagram na 02ee9e, 6 na e31953 v3).
            # Agora a morte tem sinal próprio: a 1ª da ETAPA é reaberta aqui, UMA vez por etapa e sem IA; a 2ª para a
            # etapa com o motivo, mesmo que as duas tenham caído em tentativas diferentes.
            fora = bool(app.package) and obs.package != app.package
            conferir, agiu = fora and (agiu or obs.package != fora_anterior), False
            fora_anterior = obs.package if fora else None
            if conferir and app.package:
                lidas = await self._mortes_por_anr(rt, app.package, desde=desde_etapa, timeout=call_timeout)
                mortes_por_anr.update(m.chave for m in lidas)
                rotulo = app.name or app.package
                motivo = motivo_de_anr(rotulo, iid)
                if len(mortes_por_anr) >= 2:
                    self._avisar_anr(rt, motivo)
                    await evidence(obs, f"Falha: {motivo} (mortes às "
                                        f"{', '.join(sorted(q for q, _ in mortes_por_anr))}, relógio do aparelho)")
                    # A reabertura já foi gasta e a próxima partida a frio morreria igual: repetir a etapa aqui é o
                    # laço que esta regra existe para cortar. Com o efeito já disparado, segue a regra de sempre.
                    return StepOutcome(Outcome.uncertain if (step.side_effect and fired) else Outcome.failed, motivo)
                # Uma morte e a reabertura já gasta (nesta ou numa tentativa anterior): nada de reabrir outra vez. A IA
                # vê o launcher como antes; se ela reabrir e o app morrer, é a 2ª morte, logo acima.
                if mortes_por_anr and step.id not in self._reabertas_por_anr:
                    restante = deadline - time.monotonic()
                    if restante < ferramentas.ESPERA_DO_FOCO_S:
                        # O que resta do prazo não cobre uma partida a frio (é o que `ESPERA_DO_FOCO_S` mede): a
                        # reabertura escorreria para a tentativa seguinte num convidado sem CPU, disputando com ela o
                        # aparelho. A tentativa acaba com o motivo, e a seguinte — prazo inteiro — gasta a reabertura.
                        self._avisar_anr(rt, motivo)
                        return await fail_or_retry(
                            f"{motivo} (faltavam {max(0.0, restante):.0f} s do prazo da etapa, menos que uma partida "
                            f"a frio: o app não foi reaberto nesta tentativa)", obs)
                    self._reabertas_por_anr.add(step.id)
                    repo.decision(f"{iid} · {step.title}: o {rotulo} parou de responder (ANR) e o sistema o fechou "
                                  f"(às {max(m.quando for m in lidas) if lidas else '?'}, relógio do aparelho); "
                                  "reaberto uma vez, sem IA", run_id=run_id, instance_id=iid, step_id=step.id)
                    try:
                        await call(rt.io.open_app, app.package, app.activity)
                        na_frente = await esperar_foco(lambda: call(rt.io.current_focus), app.package, ate=deadline)
                    except DriverTimeout as exc:
                        return await self._stuck(rt, step, fired, str(exc))
                    except DriverError as exc:
                        log.info("%s: a reabertura de %s depois do ANR falhou (%s)", iid, app.package, exc)
                        na_frente = False
                    history.append(f"(executor) o {rotulo} parou de responder (ANR) e o sistema o fechou; o executor "
                                   "o reabriu uma vez, sem IA" + ("" if na_frente else ", e ele ainda não chegou à frente")
                                   + ". Se ele morrer de novo, a etapa para.")
                    agiu = True
                    continue
            # "Não mandar a imagem" e "parar e chamar uma pessoa" eram a MESMA coisa enquanto `sensitive` só
            # significava campo de senha. Deixaram de ser (achado #127): uma tela declarada em
            # `sensitive_screens` — ou qualquer tela da VM-loja — precisa ter a imagem omitida, mas parar a
            # etapa ali seria inventar uma falha de autenticação e marcar o perfil como `auth_required` toda vez
            # que a IA passasse por ela. A omissão da imagem já aconteceu (aqui em cima e nos provedores); só o
            # campo de senha e o desafio de verificação pedem gente.
            if pede_intervencao_humana(obs.tree, tem_credencial=senha_do_app.secret is not None):
                porque = obs.tree.sensitive_reason
                if porque == MOTIVO_DESAFIO:
                    # O desafio já saiu pelo detector, lá em cima: `MOTIVO_DESAFIO` só nasce quando ele acha a trava.
                    # Se um caminho novo o fizer chegar aqui, a regra é a mesma — `auth_challenge`, nunca o
                    # `auth_required` que devolvia a conta travada ao login automático.
                    return await parar_na_trava(obs.tree.conta_travada or ContaTravada(SUBTIPO_CONTA_TRAVADA, porque),
                                                obs.package)
                await evidence(obs, f"Tela sensível detectada ({porque})")
                # A tela de senha DESMENTE o "Conectado" do painel: a sessão daquele perfil passa a valer como
                # `auth_required` aqui mesmo. É o que faz o autenticador automático (que tem a credencial no
                # cofre) finalmente disparar na próxima passada, em vez de a etapa parar para sempre pedindo
                # login manual enquanto o status continuava `session_ready`.
                self._sessao_desmentida(iid, app.package, "auth_required",
                                        "o app pediu autenticação durante a execução")
                if senha_do_app.pending_consent and porque == MOTIVO_SENHA:
                    # A senha existe na conta da persona; falta o consentimento (ADR-040). A pendência é da pessoa,
                    # e o item diz exatamente isso em vez de pedir um login manual que ela não precisa fazer.
                    return StepOutcome(Outcome.waiting_user,
                                       f"O app pede autenticação e a senha da conta está guardada sem consentimento "
                                       f"({senha_do_app.refusal}): consentimento_pendente.",
                                       needs="Marque o consentimento na conta da persona (guia Contas e acesso da persona) e "
                                             "retome o item — ou faça o login manualmente e devolva o controle.")
                return StepOutcome(Outcome.waiting_user, f"O app pede autenticação ({porque}).",
                                   needs="Assuma o controle, faça o login manualmente e devolva o controle à IA.")
            # ---------- decidir: a receita (se houver e ainda casar) fala primeiro; na divergência a IA assume
            decision: Decision | None = None
            from_recipe = False
            rep = rr.replayer if (rr.mode == "replay" and not rr.diverged and not fired) else None
            if rep is not None:
                rr.exerceu(StrategyKind.recipe)
                try:
                    decision = rep.next(obs.tree)
                    if decision is None:                       # receita esgotada: falta só comprovar
                        if judged_step or self._postcondition_holds(step, obs, cartao):
                            rr.completed_by_recipe = True
                            aid = repo.log_intent(attempt_id, "step_done", {"rationale": "[receita] ações reproduzidas"},
                                                  f"[receita v{rep.version}] ações reproduzidas; conferindo a pós-condição",
                                                  side_effect=False, source="recipe")
                            repo.finish_action(aid, ActionStatus.done, result={"declared": True})
                            break
                        rr.settle += 1
                        if rr.settle <= 3:                     # a interface pode estar assentando
                            await asyncio.sleep(float(self.cfg.file.ai.recipe_settle_s))
                            continue
                        raise RecipeDiverged("ações reproduzidas, mas a pós-condição não apareceu")
                    from_recipe = True
                except RecipeDiverged as exc:
                    if rep.done_actions == 0 and not judged_step and self._postcondition_holds(step, obs, cartao):
                        rr.completed_by_recipe = True          # o aparelho já estava no estado final desta etapa
                        break
                    rr.diverged = str(exc)
                    decision = None
                    history.append(f"(executor) a receita desta etapa divergiu: {exc}. Continue a partir da tela atual.")
                    repo.decision(f"{iid} · {step.title}: receita divergiu — {exc}; a IA assume esta etapa",
                                  run_id=run_id, instance_id=iid, step_id=step.id)
            scale = self._image_scale(obs)
            if decision is None:
                trouble = errors_in_row >= 1 or same_count >= 1
                piso_forcou = forcar_tier_1    # captura ANTES de zerar: o motivo do escalonamento lê daqui embaixo
                forcar_tier_1 = False          # consumido: só a decisão SEGUINTE ao descarte sobe de tier, não todas
                if rr.mode == "replay" and rr.diverged and not rr.retorno_contado:
                    # Funil de receitas (contrato C5): a IA assume a etapa depois da divergência — contado UMA vez
                    # por tentativa, no instante da primeira consulta. A divergência sozinha NÃO sobe de tier
                    # (decisão da evolução de desempenho, 26/09): a IA decide no modelo de ação e só escala pelos
                    # controles abaixo. Escalar aqui é decisão do dono, com o custo medido no relatório.
                    rr.retorno_contado = True
                    contar_retorno_ia(rr.diverged)
                tier = 1 if (base_tier or errors_in_row >= 2 or same_count >= 1 or piso_forcou) else 0
                if tier and not escalated:
                    # O escalonamento é configuração explícita do dono (AI_MODEL_ESCALATION,
                    # strong_model_for_side_effect) e já aparecia no cartão de custo — o que faltava era a linha
                    # na execução dizendo POR QUE esta etapa passou a decidir no modelo caro (achado #92, item 5).
                    escalated = True
                    motivo = (motivo_efeito if tier_efeito
                              else "nova tentativa da mesma etapa" if step.attempts > 1
                              else "erros seguidos" if errors_in_row >= 2
                              else "alvo inexistente na tela (piso do modelo local, item 7.8)" if piso_forcou
                              else "ação repetida na mesma tela")
                    repo.decision(f"{iid} · {step.title}: decisão escalonada para o modelo de escalonamento "
                                  f"({motivo})", run_id=run_id, instance_id=iid, step_id=step.id)
                quer_imagem = self._want_image(obs.tree, judged_step=judged_step, first=decisions == 0,
                                               trouble=trouble, requested=image_requested)
                if quer_imagem and obs.jpeg is None and obs.image_omitted == "policy":
                    # A receita divergiu depois da observação só de árvore: a imagem vem agora, da mesma árvore,
                    # pelo mesmo executor e sem ação no meio.
                    try:
                        obs = last_obs = await self.devices.completar_imagem(rt, obs, timeout=call_timeout,
                                                                             lado_max=ai_cfg.screenshot_max_side)
                    except DriverTimeout as exc:
                        return await self._stuck(rt, step, fired, str(exc))
                    except DriverError as exc:
                        log.info("%s: imagem para a decisão indisponível (%s); decide pela árvore", iid, exc)
                screen, scale = self._screen(obs, with_image=quer_imagem,
                                             protect=tuple(step.commit_guard), boost=_boost_terms(step, app))
                image_requested = False
                decisions += 1
                actor_history = compress_history(history, ai_cfg.actor_history_lines)
                rr.exerceu(StrategyKind.ai_actor)
                if licoes is None:
                    licoes = self._licoes_da_tentativa(run, objective, step, attempt_id, app, rr)
                pedidas = licoes
                try:
                    decision = await self._ai(run_id, oid, lambda: self.provider.decide(
                        DecisionRequest(ctx=ctx_for(), screen=screen, history=actor_history, tier=tier,
                                        lessons=list(pedidas))),
                        step_id=step.id, role="decide", deadline=deadline, attempt_id=attempt_id)
                except AIError as exc:
                    if exc.kind == "not_configured":
                        return StepOutcome(Outcome.waiting_user, str(exc), needs="Configure a chave do provedor no .env, "
                                           "reinicie o backend e retome este item.", ai_blocked=True)
                    if exc.kind in ("billing", "balance"):
                        return StepOutcome(Outcome.waiting_user, str(exc),
                                           needs="Recarregue o crédito do provedor de IA e retome a execução.",
                                           ai_blocked=True)
                    if exc.kind == "step_deadline":
                        return await fail_or_retry(com_anr(f"Prazo da etapa ({step.timeout_s}s) esgotado durante a "
                                                           f"decisão da IA: {exc}"), obs)
                    if exc.kind == "budget":
                        return StepOutcome(Outcome.failed if not fired else Outcome.uncertain, str(exc))
                    if exc.kind == "refusal":
                        # Achado #93: recusa do provedor por política NÃO é "IA indisponível" — repetir a etapa
                        # tende a dar a mesma recusa, e `fail_or_retry` gastaria uma tentativa à toa. Efeito já
                        # disparado: `uncertain` (mesma regra de qualquer falha após o commit); senão, espera a
                        # pessoa decidir — reescrever a intenção ou replanejar — sem consumir tentativa.
                        return StepOutcome(Outcome.uncertain if (step.side_effect and fired) else Outcome.waiting_user,
                                           f"O provedor de IA recusou esta requisição por política: {exc}",
                                           needs=None if (step.side_effect and fired) else
                                           "O provedor recusou por política — repetir tende a dar o mesmo resultado. "
                                           "Reescreva a intenção desta etapa (ou o comando) e retome, ou replaneje.",
                                           ai_blocked=True)
                    return await fail_or_retry(f"IA indisponível: {exc}", obs)
                if rr.mode == "shadow" and rr.replayer is not None and not rr.diverged:
                    self._shadow_compare(rr, obs, decision)     # aprende-se a confiar na receita antes de deixá-la agir
            # ---------- validar
            try:
                args = validate_call(decision.tool, decision.args)
            except ToolValidationError as exc:
                aid = repo.log_intent(attempt_id, decision.tool, _safe_args(decision.args), None, side_effect=False,
                                      source="recipe" if from_recipe else "ai")
                repo.finish_action(aid, ActionStatus.rejected, error=str(exc))
                if from_recipe:
                    rr.diverged = f"ação da receita inválida: {exc}"
                history.append(f"{decision.tool} REJEITADA: {exc}")
                errors_in_row += 1
                if errors_in_row >= 4:
                    return await fail_or_retry("A IA insistiu em chamadas inválidas.", obs)
                continue
            # ---------- piso de conteúdo (item 7.8): só entra numa decisão FRESCA (não de receita) de tier 0 num
            # provedor não-Anthropic. Um `element_id` que não está em `obs.tree` é o modelo local respondendo com
            # o id de uma tela anterior — nem `validate_call` (só confere a FORMA do argumento) nem
            # `resolve_point` (só roda depois, e só para tap/long_press) pegam isto cedo. Descartar aqui, sem
            # contar como ação nem como erro, e escalar a PRÓXIMA chamada para o tier 1 é mais barato que deixar
            # o driver tentar resolver um id inexistente e "gastar" uma tentativa de verdade nisso.
            alvo_id = getattr(args, "element_id", None)
            if (not from_recipe and tier == 0 and decide_kind != "anthropic"
                    and alvo_id is not None and obs.tree.by_id(alvo_id) is None):
                history.append(f"(executor) o alvo {alvo_id} não existe nesta tela; decisão descartada")
                forcar_tier_1 = True
                continue
            rationale = getattr(args, "rationale", None)
            if isinstance(args, StepDone) and collecting:
                aid = repo.log_intent(attempt_id, "step_done", args.model_dump(mode="json"), rationale, side_effect=False)
                repo.finish_action(aid, ActionStatus.rejected, error="etapa de coleta: use collect_list")
                history.append("step_done REJEITADA: esta é uma etapa de COLETA — chame collect_list na lista; "
                               "os itens têm de ser lidos pelo executor.")
                errors_in_row += 1
                if errors_in_row >= 4:
                    return await fail_or_retry("A IA não usou collect_list na etapa de coleta.", obs)
                continue
            if isinstance(args, StepDone):
                declared = args
                aid = repo.log_intent(attempt_id, "step_done", args.model_dump(mode="json"), rationale, side_effect=False)
                repo.finish_action(aid, ActionStatus.done, result={"declared": True})
                break
            if isinstance(args, StepBlocked):
                aid = repo.log_intent(attempt_id, "step_blocked", args.model_dump(mode="json"), rationale, side_effect=False)
                repo.finish_action(aid, ActionStatus.done, result={"kind": args.kind})
                await evidence(obs, f"Bloqueio relatado pela IA ({args.kind}): {args.reason}")
                repo.decision(f"{iid}: etapa '{step.title}' bloqueada — {args.reason}", run_id=run_id, instance_id=iid,
                              step_id=step.id)
                if args.kind == "challenge":
                    # A IA reconheceu uma verificação que o detector não conhece (ele já olhou ESTA tela antes de
                    # perguntar a ela). É julgamento do modelo, não casamento determinístico: vira `auth_challenge`
                    # e pede uma pessoa, sem bloquear o perfil — quem olhar decide se é a conta travada (ADR-055).
                    return await parar_na_trava(ContaTravada(SUBTIPO_VERIFICACAO, args.reason[:80], origem="ator"),
                                                obs.package)
                if step.side_effect and fired:
                    return StepOutcome(Outcome.uncertain, args.reason)
                if args.kind in ("auth_required", "wrong_account"):
                    # A IA viu login ou conta errada na tela. O perfil para de afirmar "Conectado": `wrong_account`
                    # e desafio dependem de pessoa; `auth_required` volta a ser trabalho do autenticador.
                    self._sessao_desmentida(iid, app.package, args.kind, args.reason)
                if args.needs_user or args.kind in ("auth_required", "wrong_account", "missing_info"):
                    return StepOutcome(Outcome.waiting_user, args.reason, needs=_needs_for(args.kind))
                return await fail_or_retry(args.reason)

            # ---------- guardas de efeito externo
            tool_ctx = ToolContext(io=rt.io, call=call, tree=obs.tree, width=obs.width, height=obs.height,
                                   image_scale=scale, app_package=app.package, app_activity=app.activity,
                                   allowed_packages=self._allowed_packages(), observe=quick_tree,
                                   collect_max_items=(min(cap.collect_limit, int(s.for_each_max_items))
                                                      if cap and cap.collect_limit else None),
                                   collect_from_top=cap.collect_from_top if cap else True,
                                   collect_rewind=bool(cap and cap.collect_rewind),
                                   fill_secret=self.preenchedor(
                                       rt, lambda nome: resolve_secret(self.dados, profile_id, nome), obs.tree,
                                       quick_tree, profile_id=profile_id, run_id=run_id, step_id=step.id),
                                   allowed_urls=urls_permitidas, allowed_hosts=hosts_das_contas, deadline=deadline)
            is_commit = False
            if step.side_effect and decision.tool in EFFECT_CAPABLE:
                target = None
                try:
                    if decision.tool in ("tap", "long_press"):
                        target = resolve_point(tool_ctx, getattr(args, "element_id", None), getattr(args, "x", None),
                                               getattr(args, "y", None))[2]
                except DriverError:
                    target = None
                alegado = bool(getattr(args, "is_commit_action", False)) or looks_like_commit(target)
                if step.commit_selector:
                    # Com seletor declarado, o commit é ESTRUTURAL: é este elemento ou não é o efeito da etapa.
                    # O vocabulário de verbos continua valendo só onde não há seletor (planejamento livre).
                    casa = target is not None and any(
                        e.id == target.id for e in obs.tree.find_selector(step.commit_selector))
                    is_commit = casa
                    if alegado and not casa:
                        rejeicao_seletor = (f"o efeito desta etapa é disparado por '{step.commit_selector}'; "
                                            "o elemento escolhido não é ele")
                        aid = repo.log_intent(attempt_id, decision.tool, args.model_dump(mode="json"), rationale,
                                              side_effect=True, source="recipe" if from_recipe else "ai")
                        repo.finish_action(aid, ActionStatus.rejected, error=rejeicao_seletor)
                        if from_recipe:
                            rr.diverged = f"alvo do efeito externo: {rejeicao_seletor}"
                        history.append(f"{decision.tool} REJEITADA pelo executor: {rejeicao_seletor}")
                        errors_in_row += 1
                        if errors_in_row >= 4:
                            return await fail_or_retry("O efeito externo foi tentado no elemento errado.", obs)
                        continue
                else:
                    is_commit = alegado
            # ---------- guarda de cartão no toque SEM efeito (o balão que abre a folha "Comments")
            # r-20260928165254-e31953: com a folha aberta a legenda do fundo continua na árvore, então a pós-condição
            # não distingue o balão do cartão vizinho — e o comentário seguinte sairia no post errado. Vale para o
            # ator e para a receita (que pode repetir o primeiro balão da tela). Antes da guarda do efeito, para uma
            # recusa aqui nunca vir depois da evidência "Conferência antes do efeito externo".
            if cartao and cap and cap.card_control and decision.tool in ("tap", "long_press"):
                try:
                    px, py, _ = resolve_point(tool_ctx, getattr(args, "element_id", None), getattr(args, "x", None),
                                              getattr(args, "y", None))
                    fora_do_cartao = rejeicao_dos_controles(cap.card_control, cartao, obs.tree, (px, py))
                except DriverError:
                    fora_do_cartao = None      # o próprio toque falha adiante, sem chegar ao aparelho
                if fora_do_cartao:
                    aid = repo.log_intent(attempt_id, decision.tool, args.model_dump(mode="json"), rationale,
                                          side_effect=False, source="recipe" if from_recipe else "ai")
                    repo.finish_action(aid, ActionStatus.rejected, error=fora_do_cartao)
                    if from_recipe:            # a receita tocaria outra publicação: não decide mais nada nesta etapa
                        rr.diverged = f"controle de outro cartão: {fora_do_cartao}"
                    history.append(f"{decision.tool} REJEITADA pelo executor: {fora_do_cartao}")
                    errors_in_row += 1
                    if errors_in_row >= 4:
                        return await fail_or_retry("O toque foi tentado no controle de outra publicação.", obs)
                    continue
            if is_commit:
                reject: str | None = None
                if fired:
                    reject = "o efeito externo desta etapa já foi disparado; é proibido repetir. Apenas verifique."
                else:
                    reject = rejeicao_do_commit(step.commit_guard, step.band_guard, cartao, obs.tree, target)
                if reject:
                    aid = repo.log_intent(attempt_id, decision.tool, args.model_dump(mode="json"), rationale, side_effect=True,
                                          source="recipe" if from_recipe else "ai")
                    repo.finish_action(aid, ActionStatus.rejected, error=reject)
                    if from_recipe:            # guarda de commit não atendida: a receita não decide mais nada nesta etapa
                        rr.diverged = f"guarda do efeito externo: {reject}"
                    history.append(f"{decision.tool} REJEITADA pelo executor: {reject}")
                    errors_in_row += 1
                    if errors_in_row >= 4:
                        return await fail_or_retry("Pré-condições do efeito externo não foram atendidas.", obs)
                    continue
                await evidence(obs, "Conferência antes do efeito externo: " +
                               (", ".join(step.commit_guard) or "sem textos de guarda") + " visíveis")

            # ---------- detectar ciclo sem progresso
            sig = (obs.tree.signature(), f"{decision.tool}:{_target_key(args)}")
            same_count = same_count + 1 if sig == last_sig else 0
            last_sig = sig
            sigs.append((sig[0], obs.tree.signature(estrutural=True), sig[1]))
            ciclo = ciclo_sem_progresso(sigs, int(s.no_progress_limit))
            if ciclo:
                return await fail_or_retry(ciclo, obs)

            # ---------- agir (intenção gravada ANTES)
            aid = repo.log_intent(attempt_id, decision.tool, args.model_dump(mode="json"), rationale, side_effect=is_commit,
                                  source="recipe" if from_recipe else "ai")
            if is_commit:
                fired = True           # a partir daqui o efeito pode ter ocorrido, aconteça o que acontecer
                self._open_effect(objective, step, rt, cap, app.id)   # o histórico registra a INTENÇÃO, não o sucesso
            t0 = time.monotonic()
            agiu = True                # a próxima observação fora do app alvo reconfere as mortes (pacote "anr")
            try:
                out = await execute_tool(tool_ctx, decision.tool, args)
            except TelaDeContaTravada as exc:
                # A verificação apareceu no meio da ferramenta (ADR-055): o gesto que a precedeu aconteceu, e nada
                # depois dele. Com efeito externo possível, a regra de sempre: `fired` continua e o desfecho é incerto.
                repo.finish_action(aid, ActionStatus.unknown if (is_commit and exc.effect_possible) else ActionStatus.done,
                                   error=str(exc), effect_possible=exc.effect_possible)
                if is_commit and not exc.effect_possible:
                    fired = False
                return await parar_na_trava(exc.trava, exc.pacote)
            except DriverError as exc:
                possible = exc.effect_possible
                # Sem resposta a tempo ou com a UI ocupada, o gesto pode ter chegado ao app: é INCERTO, não "falhou".
                # O executor não o repete; a próxima volta relê a tela, e a IA decide a partir dela.
                incerta = possible and isinstance(exc, (DriverTimeout, DriverBusy))
                status = ActionStatus.unknown if (possible and is_commit) or incerta else ActionStatus.failed
                repo.finish_action(aid, status, error=str(exc), effect_possible=possible)
                if is_commit and not possible:
                    fired = False      # nada chegou ao aparelho: o efeito NÃO foi disparado
                if is_commit and possible:
                    unknown = True
                    repo.add_effect(oid, f"'{step.title}': ação com efeito disparada, resultado desconhecido ({exc})")
                    repo.note_attempt(attempt_id, error=str(exc),
                                      recovery="Reconciliação pela tela antes de qualquer nova tentativa")
                    if isinstance(exc, DriverTimeout) and not await rt.executor.drain(max_wait_s=120):
                        return await self._stuck(rt, step, True, str(exc))
                    break              # vai direto para a verificação (reconciliação)
                if isinstance(exc, DriverTimeout):
                    if not await rt.executor.drain(max_wait_s=120):
                        return await self._stuck(rt, step, fired, str(exc))
                if sessao_perdida(exc):
                    # Só sessão morta de verdade se recria — e a instrumentação morta é isso, sem citar "session".
                    # UI ocupada veio DA sessão: ela está viva (execuções r-20260928195344-02ee9e e
                    # r-20260928165254-e31953).
                    self.devices.invalidate_automation(rt, str(exc))
                    await self.devices.ensure_automation(rt)
                if incerta:
                    history.append(f"(executor) {decision.tool}({_brief(args)}) sem confirmação ({exc}): a ação pode "
                                   "ter chegado ao app — confira na tela atual antes de repetir.")
                else:
                    history.append(f"{decision.tool}({_brief(args)}) FALHOU: {exc}")
                errors_in_row += 1
                if errors_in_row >= 3:
                    return await fail_or_retry(f"Falhas consecutivas do driver: {exc}", obs)
                continue
            errors_in_row = 0
            repo.finish_action(aid, ActionStatus.done, effect_possible=decision.tool in EFFECT_CAPABLE,
                               result={**out.result, "ms": round((time.monotonic() - t0) * 1000)},
                               target=_safe_target(out.target, obs.tree))
            if is_commit:
                repo.add_effect(oid, f"'{step.title}': {decision.tool} executado ({rationale or 'ação com efeito'})")
            history.append(f"{decision.tool}({_brief(args)}) → {_brief_result(out.result)}")
            if rationale:
                repo.decision(f"{iid} · {step.title}: {rationale}", run_id=run_id, instance_id=iid, step_id=step.id)
            if decision.tool == "collect_list":
                got = [t for t in (sanitize_item(x) for x in out.result.get("items", [])) if t]
                if cap and cap.item_key:
                    # O que a lista mostra é uma frase ("fulano said oi"); quem identifica o alvo é a chave dentro
                    # dela. Recortar aqui faz `{item}` — e o `{username}` das etapas do bloco — nascer já limpo.
                    padrao = re.compile(cap.item_key)
                    got = [(m.group(1) if (m := padrao.search(t)) else t) for t in got]
                limit = int(s.for_each_max_items)
                if not collecting:
                    history.append("(executor) collect_list só vale em etapa de coleta; os itens foram ignorados.")
                elif not got:
                    history.append("(executor) nenhum item casou com item_selector dentro da lista; confira o seletor.")
                    empty_collects += 1
                    if empty_collects >= 3:
                        return await fail_or_retry("A coleta não encontrou nenhum item na lista.", obs)
                    continue
                elif not (out.result.get("at_end") or out.result.get("capped")):
                    return await fail_or_retry("A lista não chegou ao fim dentro do limite de páginas da coleta.", obs)
                elif len(got) > limit:
                    return StepOutcome(Outcome.waiting_user, f"A lista tem {len(got)} itens; o limite é {limit}.",
                                       needs="Aumente “itens por coleta” (for_each_max_items) em Configuração e retome.")
                else:
                    if out.result.get("capped"):
                        await evidence(obs, f"Coleta limitada a {out.result.get('limit')} itens por decisão do "
                                            "catálogo: a lista continua depois deste ponto.", kind="text")
                    collected = got
                    break                  # fato medido pelo executor: dispensa verificador
            if getattr(args, "need_image", False):
                image_requested = True
            await asyncio.sleep(float(self.cfg.file.ai.action_settle_s))   # deixa a interface assentar antes da próxima observação
            if is_commit:
                break                  # depois do efeito não há mais o que decidir: só comprovar (sem outra chamada)
            if getattr(args, "expect_done", False) and not judged_step:
                # a IA previu que esta ação conclui a etapa: uma conferência determinística poupa o step_done
                try:
                    # conferência pela árvore: sem imagem (a evidência, se houver, adquire a sua)
                    peek = last_obs = await self.devices.observe(rt, timeout=call_timeout, imagem=False)
                except DriverError:
                    continue
                if not peek.sensitive and self._postcondition_holds(step, peek, cartao):
                    break
                history.append("(executor) a pós-condição ainda NÃO vale depois desta ação; continue.")
        else:
            return await fail_or_retry(f"Limite de {max_actions} ações por etapa atingido sem concluir.", last_obs)

        if collecting and collected is not None:
            text = f"{len(collected)} item(ns) lidos até o fim da lista: " + ", ".join(collected)[:400]
            await evidence(last_obs, f"Coleta comprovada pelo executor: {text}")
            repo.transition_step(step.id, StepStatus.verifying, message=f"Etapa '{step.title}': itens lidos pelo executor")
            repo.transition_step(step.id, StepStatus.succeeded, detail=text,
                                 result=StepResult(verified=True, evidence_text=text, items=collected),
                                 message=f"Etapa '{step.title}' comprovada: {text}")
            repo.finish_attempt(attempt_id, AttemptStatus.succeeded, observed=text)
            return StepOutcome(Outcome.succeeded, text, items=collected)

        # ================================================================ verificar a pós-condição
        repo.transition_step(step.id, StepStatus.verifying,
                             message=f"Etapa '{step.title}': verificando a pós-condição"
                             + (" (reconciliação após resultado desconhecido)" if unknown else ""))
        try:
            ok, text, level, obs, unprovable = await self._verify(rt, step, ctx_for, run_id, oid, deadline, call_timeout,
                                                                  patient=bool(need) or fired, facts=history[-12:],
                                                                  failure_marks=(tuple(cap.failure_marks)
                                                                                 if cap and fired else ()),
                                                                  local_proof=(cap.local_proof if cap else None),
                                                                  capability=(CapabilityRef(app.package, cap.key)
                                                                              if cap and app.package else None),
                                                                  attempt_id=attempt_id, cartao=cartao)
        except DriverTimeout as exc:
            return await self._stuck(rt, step, fired, str(exc))
        except AIError as exc:
            if exc.kind == "not_configured":
                return StepOutcome(Outcome.waiting_user, str(exc), needs="Configure a chave do provedor no .env, "
                                   "reinicie o backend e retome este item.", ai_blocked=True)
            if exc.kind in ("billing", "balance"):
                return StepOutcome(Outcome.waiting_user, str(exc),
                                   needs="Recarregue o crédito do provedor de IA e retome a execução.",
                                   ai_blocked=True)
            if exc.kind == "step_deadline":
                return await fail_or_retry(com_anr(f"Prazo da etapa ({step.timeout_s}s) esgotado durante a "
                                                   f"verificação: {exc}"), last_obs)
            if exc.kind == "budget":
                return StepOutcome(Outcome.failed if not fired else Outcome.uncertain, str(exc))
            if exc.kind == "refusal":
                # Mesma regra do achado #93 do lado da decisão: recusa por política não é "não pôde ser feita" —
                # repetir a verificação tende a dar a mesma recusa, sem gastar tentativa à toa.
                return StepOutcome(Outcome.uncertain if fired else Outcome.waiting_user,
                                   f"O provedor de IA recusou verificar esta etapa por política: {exc}",
                                   needs=None if fired else
                                   "O provedor recusou por política — repetir tende a dar o mesmo resultado. "
                                   "Reescreva a intenção desta etapa (ou o comando) e retome, ou replaneje.",
                                   ai_blocked=True)
            return await fail_or_retry(f"Verificação não pôde ser feita: {exc}", last_obs)
        except DriverError as exc:
            return await fail_or_retry(f"Verificação não pôde ser feita: {exc}", last_obs)
        # o nível de entrega declarado pela IA em step_done não vale como prova; só o observado na verificação
        note = f"Pós-condição {'comprovada' if ok else 'NÃO comprovada'}: {text}"
        await evidence(obs, note, kind="verifier" if obs is None else "screenshot")
        if ok:
            if account_label and norm_text(account_label) in norm_text(step.postcondition.value + " " + (text or "")):
                repo.db.execute("UPDATE instances SET account_evidence=?, account_evidence_ts=? WHERE id=?",
                                (text or step.postcondition.value, now_iso(), iid))
            repo.transition_step(step.id, StepStatus.succeeded, detail=text,
                                 result=StepResult(verified=True, evidence_text=text, delivery_level=level),
                                 message=f"Etapa '{step.title}' comprovada: {text}")
            repo.finish_attempt(attempt_id, AttemptStatus.succeeded, observed=text,
                                recovery="Resultado confirmado por reconciliação da tela" if unknown else None)
            return StepOutcome(Outcome.succeeded, text, delivery_level=level)
        if step.side_effect and fired:
            return StepOutcome(Outcome.uncertain, f"O efeito foi disparado, mas não foi possível comprová-lo: {text}",
                               delivery_level=level)
        if unprovable:
            return StepOutcome(Outcome.failed, "Defeito do plano — a pós-condição não é comprovável pela tela (descreve "
                               f"processo/histórico); repetir não resolve: {text}", plan_defect=True)
        return StepOutcome(Outcome.retry if step.attempts < step.max_attempts else Outcome.failed,
                           f"Pós-condição não comprovada: {text}")

    # ------------------------------------------------------------------ verificação
    @staticmethod
    def _deterministic(step: StepDTO, obs: Observation) -> tuple[bool, str]:
        """Parte da pós-condição que dispensa modelo. `model_judged` não tem parte determinística (devolve True)."""
        post = step.postcondition
        if post.kind == "text_visible":
            ok = obs.tree.contains_text(post.value)
            return ok, f"texto \"{post.value}\" {'visível' if ok else 'não encontrado'} na tela"
        if post.kind == "app_foreground":
            ok = obs.package == post.value or (obs.package is None and post.value in obs.tree.packages)
            return ok, f"app em primeiro plano: {obs.package or 'desconhecido'} (esperado {post.value})"
        if post.kind == "element_present":
            found = obs.tree.find_selector(post.value)
            return bool(found), f"seletor {post.value}: {len(found)} elemento(s)"
        if post.kind == "items_collected":         # só o resultado de collect_list comprova (tratado antes de verificar)
            return False, "os itens ainda não foram coletados (collect_list)"
        return True, ""

    def _postcondition_holds(self, step: StepDTO, obs: Observation, cartao: tuple[str, ...] = ()) -> bool:
        """Conferência barata (sem modelo) usada pelo atalho `expect_done`; nunca vale para etapa julgada por visão.
        Com a legenda da publicação alvo (`cartao`), ela também precisa estar na tela — senão o atalho da receita
        ("o aparelho já estava no estado final") aceitaria qualquer publicação aberta."""
        if step.postcondition.kind == "model_judged" or step.postcondition.required_delivery_level is not None:
            return False
        return self._deterministic(step, obs)[0] and not textos_do_cartao_ausentes(cartao, obs.tree)

    async def _verify(self, rt: DeviceRuntime, step: StepDTO, ctx_for: Callable[[], StepContext], run_id: str,
                      objective_id: str, deadline: float, call_timeout: float, *, patient: bool,
                      facts: list[str] | None = None, failure_marks: tuple[str, ...] = (),
                      local_proof: str | None = None, capability: CapabilityRef | None = None,
                      attempt_id: str | None = None, cartao: tuple[str, ...] = ()
                      ) -> tuple[bool, str, DeliveryLevel | None, Observation | None, bool]:
        post = step.postcondition
        need = post.required_delivery_level
        budget = min(max(deadline - time.monotonic(), 8.0), 60.0 if patient else 15.0)
        t_end = time.monotonic() + budget
        max_calls = int(self.cfg.file.ai.verify_max_model_calls)
        judged_polls = 0
        judged_sig: str | None = None
        verdict_text, level, obs = "", None, None
        escalou = False                            # no máximo UM rejulgamento escalado por verificação (item 7.10)
        marcas_pendentes = self._marcas_pendentes(capability)
        if patient and (post.kind == "model_judged" or need is not None):
            # o app costuma levar ~1–2 s para sair de "enviando": evita pagar 2 julgamentos
            await asyncio.sleep(float(self.cfg.file.ai.judge_wait_s))
        lado_max = self.cfg.file.ai.screenshot_max_side
        while True:
            # Só a árvore: a maioria das conferências é determinística. A imagem vem logo antes do julgamento que a
            # usa (`completar_imagem`), e a evidência final adquire a sua se a observação não tiver (C1). UI ocupada
            # relê dentro do orçamento desta verificação: logo depois do efeito é quando o convidado mais pena.
            obs = await reler_se_ocupada(lambda: self.devices.observe(rt, timeout=call_timeout, imagem=False),
                                         prazo=t_end, quem=rt.id)
            ok, text = self._deterministic(step, obs)
            # Nível de entrega (enviada/entregue/lida) não é comprovável por texto/seletor — o texto já aparece no
            # campo ANTES do envio. Sempre que o plano exigir um nível, o verificador julga a tela também.
            judged = post.kind == "model_judged" or (ok and need is not None)
            pendentes = marcas_pendentes_na_tela(marcas_pendentes, obs.tree)
            if pendentes:
                # ADR-055: "Sending…" na tela é efeito A CAMINHO, nunca feito. Em 19/09 a DM da beatriz foi dada por
                # enviada com "Sending…" congelado: a bolha e o campo limpo já estavam lá, e o modelo disse "sim". Nem
                # a prova local nem o modelo são consultados; segue olhando até o fim do prazo desta verificação (o
                # app costuma sair de "Sending…" em 1–2 s). Sem sair, o efeito disparado fica incerto.
                ok, judged = False, False
                text = "; ".join(t for t in (text, "envio pendente: a tela ainda mostra "
                                             + ", ".join(f'"{m}"' for m in pendentes)
                                             + " — pendente não conta como feito, e o modelo não é consultado") if t)
            ausentes = textos_do_cartao_ausentes(cartao, obs.tree)
            legendas = ", ".join(f'"{c}"' for c in cartao)
            if ausentes:
                # A publicação ALVO não está na tela. O título "Posts" e um coração marcado valem para qualquer
                # publicação (r-20260928195344-02ee9e), e o modelo julgaria justamente isso — então nem se pergunta a
                # ele. Continua olhando até o fim do prazo: num aparelho lento a legenda chega depois do título.
                ok, judged = False, False
                text = "; ".join(t for t in (text, "a publicação alvo não está na tela: "
                                             + ", ".join(f'"{a}"' for a in ausentes) + " não aparece") if t)
            elif cartao and not judged:
                # A evidência dizia só o seletor ("Posts"), como se a legenda não tivesse sido conferida. Foi: aqui
                # é presença NA TELA (é o que `textos_do_cartao_ausentes` confere), não dentro de um cartão.
                text = "; ".join(t for t in (text, f"legenda {legendas} presente na tela") if t)
            # Achado #102: antes de gastar uma chamada de modelo (que só via os 80 primeiros caracteres de cada
            # elemento), confere pela árvore local quando o catálogo declara uma prova determinística para esta
            # pós-condição julgada. `need` de nível de entrega exige o modelo mesmo assim — "enviado" não prova
            # "entregue/lido". Qualquer condição que falhe (sem `content` conhecido, texto só no campo de escrita,
            # texto ausente) devolve `None`/`False` e cai para o modelo — nunca vira reprovação por si só.
            # Fase G: a pergunta vai ao `CapabilityProvider` (a mesma `local_proof_holds`, embrulhada); só `proved`
            # vale como atalho, exatamente como o `True` de antes.
            if judged and need is None and local_proof and await self._prova_local(step, capability, obs):
                ok, judged = True, False
                text = (f"pós-condição comprovada pela árvore local, sem IA ({local_proof})"
                        if not local_proof.startswith("sent_text") else
                        "conteúdo comprovado pela árvore local, sem IA: presente numa mensagem do fio, ausente do campo de escrita"
                        + (", sem marca de envio pendente" if marcas_pendentes else ""))
                if cartao:
                    # Com legenda, a prova só casa o elemento do CARTÃO dela (`proofs.local_proof_holds`); a evidência
                    # diz, em vez de parecer que só o seletor foi conferido.
                    text += f", no cartão da legenda {legendas}"
                self.repo.decision(f"{rt.id} · {step.title}: pós-condição comprovada pela árvore local (sem IA)",
                                   run_id=run_id, instance_id=rt.id, step_id=step.id)
            elif judged and cartao:
                # Com a legenda da publicação alvo, SÓ a prova local fecha a etapa. O modelo olha a tela "Posts", que é
                # um feed: veria o coração marcado de OUTRO cartão e diria "sim" — e uma curtida que o app não registrou
                # viraria sucesso. A prova negativa (ou ausente) não é reprovação imediata: segue olhando até o fim do
                # prazo desta verificação (o coração pode marcar depois), sem chamada de modelo; sem prova, a etapa
                # falha — ou fica incerta, com o efeito disparado.
                ok, judged = False, False
                motivo = (f"a prova local ({local_proof}) não confirmou no cartão da legenda {legendas}"
                          if local_proof and need is None else
                          f"nenhuma prova local se aplica ao cartão da legenda {legendas}")
                text = "; ".join(t for t in (text, f"{motivo}; com legenda de cartão só a prova local vale e o modelo "
                                             "não é consultado (ele julgaria outra publicação da tela, como o coração "
                                             "marcado de outro cartão)") if t)
            if judged:
                sig = obs.tree.signature()
                if judged_polls and sig == judged_sig:
                    ok = False             # mesma tela que já foi julgada insuficiente: espera mudar, sem gastar chamada
                else:
                    # 1º julgamento só pela hierarquia quando ela é rica; os seguintes levam a imagem
                    quer_imagem = self._want_image(obs.tree, judged_step=False, first=False,
                                                   trouble=judged_polls >= 1, requested=False)
                    if quer_imagem:
                        obs = await self.devices.completar_imagem(rt, obs, timeout=call_timeout, lado_max=lado_max)
                    screen, _ = self._screen(obs, with_image=quer_imagem, protect=tuple(step.commit_guard))
                    # `t_end` é o orçamento DESTA verificação (nunca além do prazo da etapa): a chamada de
                    # verificação passa a ter limite próprio, que era o que faltava (achado #96).
                    verdict = await self._ai(run_id, objective_id,
                                             lambda: self.provider.verify(VerifyRequest(ctx=ctx_for(), screen=screen,
                                                                                        facts=list(facts or []))),
                                             step_id=step.id, role="verify", deadline=t_end, attempt_id=attempt_id)
                    judged_polls += 1
                    judged_sig = sig
                    level = verdict.delivery_level
                    if (not escalou and need and level and verdict.satisfied in ("no", "uncertain")
                            and DELIVERY_ORDER[level] >= DELIVERY_ORDER[need]):
                        # Item 7.10 (bateria de 25/09): o verificador barato recusou dizendo que viu um nível que JÁ
                        # atende ao exigido ("Entregue" onde bastava "Enviada"). Promover sozinho seria arriscado —
                        # o "não" pode ter outro motivo (contato ou texto errado) —, então quem decide é o modelo de
                        # escalonamento, uma vez, sobre a mesma tela. Raro e barato; evita parar numa pessoa à toa.
                        escalou = True
                        self.repo.decision(
                            f"{rt.id} · {step.title}: o verificador recusou vendo o nível {level.value} "
                            f"(exigido {need.value}); rejulgando com o modelo de escalonamento",
                            run_id=run_id, instance_id=rt.id, step_id=step.id)
                        verdict = await self._ai(
                            run_id, objective_id,
                            lambda: self.provider.verify(VerifyRequest(ctx=ctx_for(), screen=screen,
                                                                       facts=list(facts or []), escalate=True)),
                            step_id=step.id, role="verify", deadline=t_end, attempt_id=attempt_id)
                        level = verdict.delivery_level
                    ok = verdict.satisfied == "yes"
                    if ok and need and DELIVERY_ORDER[level or DeliveryLevel.none] < DELIVERY_ORDER[need]:
                        ok = False
                    if ok and not obs.tree.elements:
                        # Item 7.10: 4 dos 6 falsos positivos do rejulgamento de 25/09 foram "sim" sobre uma tela sem
                        # NENHUM elemento na hierarquia (carregando, em branco). Tela vazia não prova estado: segue
                        # esperando — se ela carregar, a assinatura muda e o modelo julga de novo.
                        ok = False
                        verdict = verdict.model_copy(update={
                            "evidence": verdict.evidence + " [tela sem elementos na hierarquia: não conta como prova]"})
                    verdict_text = verdict.evidence + (f" [nível observado: {level.value}]" if level else "")
                    if verdict.satisfied == "unprovable":      # esperar ou rejulgar não muda nada: sai já, sem 2ª chamada
                        return False, "; ".join(t for t in (text, verdict_text) if t), level, obs, True
                text = "; ".join(t for t in (text, verdict_text) if t)
            if ok and (failure_marks or marcas_pendentes):
                # Um "sim" no primeiro retrato é UI otimista: no app de mensagem o balão aparece e o campo
                # limpa ANTES de o servidor confirmar — a marca de falha só chega depois. Assenta e
                # reconfere por TEXTO (sem gastar outra chamada de modelo) antes de dar a etapa por provada.
                await asyncio.sleep(float(self.cfg.file.ai.effect_settle_s))
                obs = await reler_se_ocupada(lambda: self.devices.observe(rt, timeout=call_timeout, imagem=False),
                                             prazo=t_end, quem=rt.id)
                achadas = [m for m in failure_marks if m and obs.tree.contains_text(m)]
                if achadas:
                    marcas = ", ".join(f'"{m}"' for m in achadas)
                    text = "; ".join(x for x in (text, f"a tela passou a mostrar {marcas} depois do envio") if x)
                    return False, text, level, obs, False
                pendentes = marcas_pendentes_na_tela(marcas_pendentes, obs.tree)
                if pendentes:
                    # O mesmo retrato assentado com "Sending…" (ADR-055): não desmente o envio, mas também não deixa
                    # o "sim" valer. Segue olhando até o prazo; se a marca sumir, a prova se refaz na próxima leitura.
                    ok = False
                    text = "; ".join(x for x in (text, "depois de assentar, a tela mostra "
                                                 + ", ".join(f'"{m}"' for m in pendentes)
                                                 + ": envio pendente, não conta como feito") if x)
            if ok or time.monotonic() >= t_end or judged_polls >= max_calls:
                return ok, text, level, obs, False
            await asyncio.sleep(float(self.cfg.file.ai.judge_wait_s))

    @staticmethod
    def _marcas_pendentes(capability: CapabilityRef | None) -> tuple[str, ...]:
        """As `pending_marks` que o catálogo declara para a ação desta etapa ("Sending…" no SEND_MESSAGE, ADR-055).

        Lidas pela referência de capability que o `_run_step` já entrega ao `_verify`: acrescentar uma marca no
        catálogo não pede fiação nova. Etapa sem catálogo (QA Messenger, plano livre) não tem marca: o "Enviando…"
        dele continua julgado como sempre."""
        cap = capability_of(capability.app, capability.key) if capability is not None else None
        return tuple(m for m in cap.pending_marks if m) if cap is not None else ()

    async def _prova_local(self, step: StepDTO, capability: CapabilityRef | None, obs: Observation) -> bool:
        """A prova local pela porta `CapabilityProvider.verify` (fase G). `proved` é o atalho de sempre.

        `not_proved` (marca de falha visível na tela) também cai para o caminho de sempre, e não reprova aqui: quem
        reprova por marca de falha é a conferência depois do "sim", que só vale com o efeito disparado. A única
        diferença para a `local_proof_holds` direta é a tela com marca de falha E prova positiva: antes era atalho,
        agora o modelo julga — mais conservador, nunca transforma falha em sucesso.
        """
        if capability is None:
            return False
        vista = StepView(node_id=step.key, capability=capability,
                         bindings=tuple((k, str(v)) for k, v in (step.bindings or {}).items() if v is not None),
                         band_guard=tuple(step.band_guard or ()),
                         required_delivery_level=(step.postcondition.required_delivery_level.value
                                                  if step.postcondition.required_delivery_level else None))
        veredito = await self.capabilities.verify(vista, Leitura(obs.tree, obs.package))
        return veredito.outcome is VerifyOutcome.proved

    async def _stuck(self, rt: DeviceRuntime, step: StepDTO, fired: bool, detail: str) -> StepOutcome:
        """Timeout do driver: o aparelho NÃO é liberado enquanto a chamada anterior puder agir."""
        self.repo.bus.emit("log", f"{rt.id}: chamada ao aparelho excedeu o tempo; aguardando ela terminar antes de liberar.",
                           level="warn", instance_id=rt.id, step_id=step.id)
        if await rt.executor.drain(max_wait_s=180):
            out = Outcome.uncertain if (step.side_effect and fired) else (
                Outcome.retry if step.attempts < step.max_attempts else Outcome.failed)
            return StepOutcome(out, f"Tempo esgotado numa chamada ao aparelho: {detail}")
        return StepOutcome(Outcome.device_stuck, f"Chamada ao aparelho travada: {detail}",
                           needs="Reinicie a instância; o aparelho fica retido até a chamada anterior terminar.")

    def _allowed_packages(self) -> set[str]:
        return {r["package"] for r in self.repo.db.query("SELECT package FROM apps")}


#: Formas aceitas de um texto de guarda (`@usuario` também sem a arroba). A regra mora em `proofs.py`, onde as
#: provas locais a reaproveitam; o nome fica aqui porque é por ele que o executor e os testes a conhecem.
guard_variants = variantes_de_arroba


def rejeicao_do_commit(commit_guard: Sequence[str], band_guard: Sequence[str], cartao: Sequence[str], tree: UiTree,
                       target: UiElement | None) -> str | None:
    """Por que o toque de efeito NÃO pode acontecer nesta tela; `None` quando as guardas estão atendidas.

    Três níveis, do mais frouxo ao mais preso: o texto visível em qualquer lugar (`commit_guard`), na mesma linha do
    alvo numa lista (`band_guard`) e no mesmo cartão de publicação que o alvo (`cartao`, a legenda que o pedido
    citou). O último é o que faltava em r-20260928165254-e31953: a receita tocou o primeiro coração da tela "Posts",
    e numa tela com dois cartões ele pode ser o da publicação de cima. Sem alvo resolvido não há linha nem cartão a
    conferir: recusa. Pura, para o teste bater nela sem aparelho."""
    missing = [g for g in commit_guard if g and not any(tree.contains_text(v) for v in guard_variants(g))]
    # Guarda de linha: numa lista, o texto tem de estar na MESMA faixa do alvo, não em qualquer lugar.
    fora_da_faixa = [g for g in band_guard
                     if g and g not in missing
                     and not (target is not None
                              and any(tree.text_in_band(v, target.bounds) for v in guard_variants(g)))]
    fora_do_cartao = [g for g in cartao
                      if g and g not in missing
                      and not (target is not None and any(tree.text_in_card(v, target) for v in guard_variants(g)))]
    if missing:
        return ("antes do efeito, estes textos precisam estar visíveis e não estão: "
                + ", ".join(f'"{m}"' for m in missing))
    if fora_da_faixa:
        return ("o alvo precisa estar na mesma linha de: " + ", ".join(f'"{m}"' for m in fora_da_faixa)
                + " — como está, o efeito pode acertar outro item da lista")
    if fora_do_cartao:
        return ("o alvo precisa estar no mesmo cartão (publicação) de: " + ", ".join(f'"{m}"' for m in fora_do_cartao)
                + " — é o botão logo ACIMA dessa legenda; como está, o efeito pode acertar outra publicação. Role até a"
                  " legenda aparecer logo abaixo do botão, ou chame step_blocked se ela não estiver nesta tela")
    return None


def rejeicao_do_controle(card_control: str, cartao: Sequence[str], tree: UiTree, ponto: tuple[int, int]) -> str | None:
    """Por que um toque SEM efeito no controle de um cartão (`card_control`, o balão de comentários) não pode
    acontecer; `None` quando o toque não acerta esse controle, ou acerta o do cartão que traz cada texto de `cartao`.

    É o que faltava em r-20260928165254-e31953: a folha "Comments" é igual para qualquer publicação e, aberta, deixa a
    legenda do fundo na árvore — a pós-condição passaria com o balão do cartão vizinho, e o comentário seguinte sairia
    no post errado. Pelo PONTO do toque, e não pelo elemento escolhido: um toque num contêiner ou num filho do balão
    acerta o balão do mesmo jeito. Toque fora do controle (uma aba, um "Not now") é navegação comum e segue livre.
    Sem `cartao` (post por posição), nada muda. Pura, para o teste bater nela sem aparelho."""
    textos = [c for c in cartao if c]
    if not textos:
        return None
    x, y = ponto
    tocados = [e for e in tree.find_selector(card_control)
               if e.bounds[0] <= x <= e.bounds[2] and e.bounds[1] <= y <= e.bounds[3]]
    if not tocados:
        return None
    fora = [c for c in textos if not any(tree.text_in_card(v, e) for e in tocados for v in guard_variants(c))]
    if not fora:
        return None
    return (f"o toque em '{card_control}' precisa ser no mesmo cartão (publicação) de: "
            + ", ".join(f'"{m}"' for m in fora)
            + " — é o controle logo ACIMA dessa legenda; este é de outra publicação. Role até a legenda aparecer logo"
              " abaixo do controle, ou chame step_blocked se ela não estiver nesta tela")


def rejeicao_dos_controles(controles: Sequence[str], cartao: Sequence[str], tree: UiTree,
                           ponto: tuple[int, int]) -> str | None:
    """`rejeicao_do_controle` para cada controle declarado (`card_control` é lista: mais de um controle do cartão abre
    a mesma folha). A primeira recusa vale; um controle que não está na tela não dispensa a conferência dos outros."""
    return next((r for c in controles if (r := rejeicao_do_controle(c, cartao, tree, ponto))), None)


def textos_do_cartao_ausentes(cartao: Sequence[str], tree: UiTree) -> list[str]:
    """Os textos da publicação alvo (`card_guard` resolvido) que NÃO estão na tela. Vazio quando não há legenda a
    exigir — o post por posição segue sem nenhuma exigência nova."""
    return [c for c in cartao if c and not any(tree.contains_text(v) for v in guard_variants(c))]


def compress_history(history: list[str], n: int) -> list[str]:
    """Item 7.6 (dieta do contexto do ator): histórico da tentativa sem gastar chamada de modelo.

    Mantém, em ordem: toda linha que marca o que NÃO repetir (`REJEITADA`, `FALHOU`, linhas do próprio
    `(executor)` — precondição, receita divergida, etc.) mais as últimas `n` linhas quaisquer. Sem duplicar
    quando as duas regras pegam a mesma linha."""
    if n < 0:
        n = 0
    relevantes = {i for i, h in enumerate(history) if "REJEITADA" in h or "FALHOU" in h or h.startswith("(executor)")}
    relevantes |= set(range(max(0, len(history) - n), len(history)))
    return [history[i] for i in sorted(relevantes)]


def actor_params(params: dict[str, str], cap: Any, step_variables: dict[str, str]) -> dict[str, str]:
    """Item 7.6: os parâmetros que vão ao modelo para ESTA etapa, não o objetivo inteiro. Com catálogo
    (`cap`), só o que a capability declara (`bindings` + `optional_bindings`) mais o que a própria etapa
    gravou (`step_variables`, ex.: `{item}` da cópia de `for_each`). Sem catálogo (plano livre) mantém tudo —
    não há como saber de antemão o que o texto livre do plano referencia."""
    if cap is None:
        return params
    permitidos = set(cap.bindings) | set(cap.optional_bindings) | set(step_variables)
    return {k: v for k, v in params.items() if k in permitidos}


def _boost_terms(step: StepDTO, app: AppContext) -> tuple[str, ...]:
    """Item 7.6: textos do ALVO desta etapa, para `UiTree.prompt_lines(boost=…)` não cortar o elemento certo
    de uma tela grande. Bindings passam pelas mesmas variantes de arroba usadas nas guardas; seletores
    (`commit_selector`, `known_selectors`) contribuem só o VALOR de cada parte (o que aparece na tela, não a
    sintaxe `id=`/`desc=`)."""
    termos: list[str] = []
    for v in (step.bindings or {}).values():
        if v:
            termos.extend(guard_variants(str(v)))
    seletores = [step.commit_selector] if step.commit_selector else []
    seletores.extend((app.known_selectors or {}).values())
    for sel in seletores:
        termos.extend(valor for _, valor, _ in UiTree._partes_do_seletor(sel) if valor)
    return tuple(t for t in termos if t)


def _needs_for(kind: str) -> str:
    return {
        "auth_required": "Assuma o controle, conclua a autenticação no app e devolva o controle à IA.",
        "challenge": _NECESSIDADE_DA_TRAVA[SUBTIPO_VERIFICACAO],
        "wrong_account": "Conecte a conta esperada neste aparelho (ou ajuste o rótulo da conta) e retome o item.",
        "missing_info": "Revise o comando/configuração com a informação que falta e retome o item.",
        "app_incompatible": "O app não expõe uma tela automatizável neste emulador; veja as evidências.",
    }.get(kind, "Verifique o aparelho e decida: retomar, confirmar ou abandonar o item.")


def _safe_target(el: Any, tree: UiTree | None = None) -> dict[str, Any] | None:
    """Alvo resolvido da ação + quais seletores o identificavam SOZINHOS naquela tela (base das receitas).
    Campo de senha nunca é registrado."""
    if el is None or getattr(el, "password", False):
        return None
    d = el.to_dict()
    d.pop("id", None)                      # "e7" só vale naquela observação
    if tree is not None:
        d["unique"] = unique_selectors(tree, el)
    return d


@dataclass
class _RecipeRun:
    """Estado das receitas durante UMA tentativa de etapa."""
    mode: str = "off"
    row: Any = None
    replayer: Replayer | None = None
    variables: dict[str, str] = field(default_factory=dict)
    app_version: str | None = None
    step_hash: str | None = None
    signature: str = ""
    variant: str = ""
    diverged: str | None = None
    retorno_contado: bool = False      # `receita.retorno_ia` já contado nesta tentativa
    completed_by_recipe: bool = False
    settle: int = 0
    #: Estratégias que a tentativa EXERCEU, em ordem (trilha da 045, `attempts.strategy`): `recipe` quando a receita
    #: foi consultada, `ai_actor` quando a IA decidiu; a divergência dá a cadeia `recipe>ai_actor`.
    exercised: list[str] = field(default_factory=list)

    def exerceu(self, kind: StrategyKind) -> None:
        if kind.value not in self.exercised:
            self.exercised.append(kind.value)


def _safe_args(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {"raw": str(raw)[:300]}


def side_effect_tier(step: Any, cap: Any, modo: Any) -> tuple[int, str]:
    """(nível, motivo) do escalonamento POR EFEITO EXTERNO desta etapa.

    `True` = qualquer efeito sobe (era o único modo: em 19-23/09, 39 % das decisões foram ao Opus, inclusive curtir
    com `commit_selector` declarado — o executor já confere o seletor, as guardas e a faixa antes do toque, e o
    modelo caro não acrescentava nada). `False` = nunca por efeito. `by_risk` = sobe quando errar é caro E o
    software não tem como travar o alvo: risco alto do catálogo, risco médio sem seletor de commit, ou app sem
    catálogo (risco desconhecido). O motivo mantém o prefixo "etapa com efeito externo", que a linha do tempo e
    os testes reconhecem.
    """
    if not getattr(step, "side_effect", False) or modo is False:
        return 0, ""
    if modo is True:
        return 1, "etapa com efeito externo"
    if cap is None:
        return 1, "etapa com efeito externo sem catálogo: risco desconhecido"
    risco = getattr(cap, "risk", "high")
    if risco == "high":
        return 1, f"etapa com efeito externo de risco alto ({getattr(cap, 'key', '?')})"
    if not (getattr(step, "commit_selector", None) or getattr(cap, "commit_selector", None)):
        return 1, "etapa com efeito externo de risco médio sem seletor de commit"
    return 0, ""


def ciclo_sem_progresso(sigs: Sequence[tuple[str, str, str]], limite: int) -> str | None:
    """Laço sem progresso pelas assinaturas (tela exata, tela estrutural, ação) das decisões desta tentativa.

    Período 1 — a mesma ação na mesma tela EXATA `limite` vezes — já era detectado; a tela exata (com texto) é
    de propósito: rolar uma lista longa repete a ação e a estrutura, mas muda o texto, e isso é progresso.
    Período 2 é o caso da execução f41d10: tocar no 1º quadro da grade → post de outro autor → voltar → grade →
    tocar no MESMO quadro…, nove voltas em dois minutos até o dono pausar. Cada decisão "mudava a tela" em relação
    à imediatamente anterior, então a comparação só com a última nunca disparava; e a tela do post traz "há 32
    minutos" e contagens, por isso o par é comparado pela estrutura, não pelo texto.
    """
    limite = max(2, int(limite))
    if len(sigs) >= limite and len({(s[0], s[2]) for s in sigs[-limite:]}) == 1:
        return "Ciclo sem progresso: a mesma ação não muda a tela."
    voltas = max(2, limite - 1)
    n = 2 * voltas
    if len(sigs) >= n:
        janela = [(s[1], s[2]) for s in sigs[-n:]]
        a, b = janela[-2], janela[-1]
        if a != b and janela == [a, b] * voltas:
            return (f"Ciclo sem progresso: '{a[1]}' e '{b[1]}' se alternam há {voltas} voltas e a tela volta sempre "
                    "à mesma — repetir não vai mudar o resultado; a etapa precisa de outro caminho.")
    return None


def _target_key(args: Any) -> str:
    d = args.model_dump(exclude={"rationale"})
    return ",".join(f"{k}={v}" for k, v in sorted(d.items()) if v is not None)[:200]


def _brief(args: Any) -> str:
    d = args.model_dump(exclude={"rationale"}, exclude_none=True)
    return ", ".join(f"{k}={str(v)[:60]!r}" for k, v in d.items())


def _brief_result(result: dict[str, Any]) -> str:
    return ", ".join(f"{k}={str(v)[:80]}" for k, v in result.items() if k != "ms") or "ok"
