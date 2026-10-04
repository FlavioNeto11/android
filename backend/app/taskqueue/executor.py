"""Execução de UMA etapa em UM aparelho: observar → decidir → validar → agir → observar → verificar.

Regras centrais:
- o retorno do driver só prova que o comando foi aceito; a etapa só conclui com a pós-condição observada;
- etapa com efeito externo dispara no máximo UMA ação "commit" (em todas as tentativas); se o resultado
  ficar desconhecido, reconcilia pela tela e, persistindo a dúvida, marca `uncertain` — nunca reenvia sozinha;
- pontos seguros entre ações permitem pausar, cancelar ou ceder o aparelho ao usuário.
"""
from __future__ import annotations

import asyncio
import dataclasses
import io
import logging
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol, Sequence, TypeVar

from PIL import Image
from pydantic import BaseModel

from ..automation import conhecimento_de_telas as telas_do_app
from ..automation import tools as ferramentas
from ..automation.driver import DriverBusy, DriverError, DriverTimeout, FalhaDeLeitura, sessao_perdida
from ..automation.hierarchy import (MOTIVO_DESAFIO, MOTIVO_SENHA, SUBTIPO_CODIGO, SUBTIPO_CONTA_TRAVADA,
                                    SUBTIPO_VERIFICACAO, ContaTravada, UiElement, UiTree)
from ..automation.tools import (CONTROL_TOOLS, EFFECT_CAPABLE, TOOLS, ReadValue, StepBlocked, StepDone,
                                TelaDeContaTravada, ToolContext, ToolValidationError, esperar_foco, execute_tool,
                                looks_like_commit, resolve_point, urls_do_texto, validate_call)
from ..config import AiCfg, Config, LimitsCfg
from ..devices.adb import AVISO_DE_ANR, MorteDoApp, motivo_de_anr
from ..devices.conta_observada import evidencia_legivel
from ..devices.manager import DeviceManager, DeviceRuntime, Limiter, Observation, dimensoes_do_modelo
from ..metricas import metricas
from ..models import (DELIVERY_ORDER, ActionStatus, AttemptStatus, DeliveryLevel, EfeitoRepetido, Plan, Postcondition,
                      StepDTO, StepResult, StepStatus)
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
from ..modules.learning.infrastructure.segredo import TriagemDeCredencial
from ..planning.capabilities import (CONHECIMENTO_DE_APPS, Capability, capability_of, contraparte, guardas_do_cartao,
                                     load_catalog)
from ..planning.catalog import session_provider_of
from ..planning.provider import (AIError, AIProvider, AppContext, Decision, DecisionRequest, LeituraRequest,
                                 MarcaDaChamada, MotivoDaChamada, MotivoDaImagem, MotivoDeEscalonamento,
                                 PreparoDaDecisao, ScreenInput, StepContext, Transcricao, Usage, Verdict, VerifyRequest)
from ..db import Row, loads
from ..security.secret_store import SecretStoreLocked, SecretStoreUnavailable
from ..security.sensitive_input import SensitiveInputError, SensitiveInputUnavailable
from ..social.approvals import ler_rascunho
from ..util import norm_text, now, now_iso, parse_iso
from .costuras import (SAIU_POR_EXCECAO, SEM_COSTURAS, CosturasDeAprendizado, FechamentoDeTentativa, PedidoDeLicoes,
                       avisar, pedir_licoes)
from .foreach import sanitize_item, teto_de_chamadas
from .proofs import marcas_pendentes_na_tela, variantes_de_arroba
from .projecao import HistoricoDeAcoes, app_da_etapa
from .latencia import TemposDaTentativa, ms_desde
from .midia_galeria import INTERNAS_POR_CODIGO, MidiaRecusada, colocar_midia_na_galeria
from .recipes import (READ_ONLY, RecipeDiverged, RecipeStore, Replayer, contar_retorno_ia, distill, eh_generica,
                      filhos_rotulados, hash_generico_da_linha, unique_selectors)
from .repository import Repository
from .relacao import relacao_do_valor
from .saidas import (RECUSAS_DETERMINISTICAS, ChaveDeTentativa, LeituraInvalida, LeituraSemTexto,
                     LeituraVisualRecusada, args_da_chamada_invalida, args_sem_valor, como_texto, ler_valor,
                     ler_valor_visual, nomes_citados, razao_sem_segredo, texto_da_tela, texto_do_elemento, triagem,
                     variaveis_da_receita)

if TYPE_CHECKING:
    from ..modules.identity.application.persona_images import PersonaImageService

log = logging.getLogger("poc.executor")

T = TypeVar("T")

#: UI ocupada (`DriverBusy`) numa LEITURA: quantas releituras, e o recuo entre elas. A sessão está viva (quem
#: respondeu o 500 foi ela); o convidado é que está saturado. Recriá-la era o que custava 27–80 s por vez e
#: reinstrumentava o UiAutomator2 no convidado sem folga: 5 recriações = 305,6 s de 925 s na
#: r-20260928195344-02ee9e, 4 = 214 s na r-20260928165254-e31953. Vale igual para a leitura que falhou fora da
#: sessão (`FalhaDeLeitura`: o screencap pelo adb, a captura na origem) — o mesmo convidado saturado.
RELEITURAS_UI_OCUPADA = 3
RECUO_UI_OCUPADA_S = 4.0
#: Quantas conferências do juiz (`_verify`, uma rodada) a etapa julgada paga ANTES de o ator decidir nada, por tentativa
#: (LT-1, caminho rápido 1): só na entrada, onde a tela já pode ser a final. O juiz custa uma chamada, e uma etapa de
#: várias ações não pode pagar uma a cada volta — depois que o ator agiu, o atalho da julgada é o `expect_done` (LT-2).
JULGAMENTOS_ANTES_DO_ATOR = 1
#: A conferência de ENTRADA de uma etapa julgada (LT-1) usa só a prova local do catálogo, sem chamar o modelo. Medido nos
#: testes de custo (`test_equivalencia_fluxo_skill`, `test_fatia_abrir_conversa`): na maioria das etapas julgadas a tela de
#: entrada AINDA não é a final, e um julgamento pago ali — antes de o ator fazer qualquer coisa — sobe `verify` por etapa
#: (o aceite do LT-1 diz que não pode subir). Com `False`, a etapa sem prova local paga o juiz barato na entrada, como o
#: handoff de latência descreve; mexer nisto só com a fração "já pronta na entrada" medida em `real`.
ENTRADA_JULGADA_SO_COM_PROVA_LOCAL = True
#: Liga o atalho de ENTRADA do LT-1 (a pós-condição já vale na tela lida → sai para a comprovação sem o ator). Existe
#: para os testes que provam regras do ator NUM CENÁRIO em que o atalho cortaria a decisão observada (o piso de tier, a
#: política de imagem): eles desligam isto e reafirmam a prova antiga sem enfraquecê-la.
ATALHO_ANTES_DO_ATOR = True
#: LT-5 (caminho rápido 2): sondagens seguidas na mesma tela depois de um "não" para a verificação desistir com o mesmo
#: veredito (~4,5 s com `judge_wait_s` de 1,5). Medido em 7 d: as "NÃO comprovada" pagavam o orçamento inteiro (mediana
#: 16,9 s, p90 55,9 s) sem nenhuma 2ª chamada — a tela não mudou e o juiz não é consultado de novo na mesma tela.
SONDAGENS_DA_TELA_PARADA = 3
#: 31.27: o intervalo entre duas leituras da árvore na espera adaptativa do juiz (`judge_wait_estavel_s`).
PASSO_DA_ESPERA_DO_JUIZ_S = 0.3
#: 31.32: o diagnóstico do seletor composto impossível nesta tela (`UiTree.partes_em_elementos_diferentes`). O texto é o
#: que `learning/domain/falhas.py` classifica como `seletor_em_elementos_diferentes`.
PARTES_EM_ELEMENTOS_DIFERENTES = "as partes do seletor estão em elementos diferentes desta tela"
#: LT-6 (caminho rápido 2): a etapa `app_foreground` abre o app pelo executor antes de consultar o ator. Existe para os
#: testes cujo gancho é a decisão do ator numa etapa dessas (como `ATALHO_ANTES_DO_ATOR`): eles desligam isto.
OPEN_APP_SEM_IA = True


def parte_vazia_da_pos_condicao(post: Postcondition) -> str | None:
    """31.44: o que a pós-condição confere sem valor (`text=`, `<vazia>`, `texto vazio`), ou `None` quando tem valor.
    Só olha o que se decide pelo texto da própria pós-condição: `element_present` e `text_visible`."""
    if post.kind == "element_present":
        return UiTree.parte_sem_valor(post.value)
    if post.kind == "text_visible" and not norm_text(post.value):
        return "texto vazio"
    return None


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
    `auth_challenge` (ADR-055): `conta_travada` bloqueia o perfil; `codigo` e `verificacao` só pedem uma pessoa.
    `package` é o app da TELA que desmentiu (item 23.4): decide a conta da persona e se ela é a âncora (23.5). Sem ele
    o AppState deduz pelo app da etapa em curso, e a trava vista no segundo app caía na conta do primeiro."""

    def __call__(self, instance_id: str, kind: str, detail: str, *, subtipo: str | None = None,
                 package: str | None = None) -> None: ...


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


#: Item 31.40 b: a chave da etapa de limpeza que o scheduler insere antes da etapa coberta (`limpar_antes_<chave>`).
PREFIXO_LIMPEZA = "limpar_antes_"

#: 31.40 b: sem o id do juiz, o que na árvore tem cara de coisa que cobre a tela (classe ou resource-id).
_PISTAS_DE_COBERTURA = re.compile(r"dialog|modal|banner|cookie|popup|pop_up|overlay|consent|bottom_?sheet|snackbar",
                                  re.IGNORECASE)

#: 31.40 b (iii): os gestos que contam como "fechar" numa limpeza (o ator que só olhou não limpou nada).
GESTOS_DA_LIMPEZA = ("tap", "long_press", "drag", "scroll", "press_back")


@dataclass(frozen=True, slots=True)
class Cobertura:
    """Item 31.40 b: o elemento que cobre o alvo de uma etapa (pelo id que o juiz citou ou pela árvore). Viaja para a
    limpeza em `steps.variables` (`cobre_*`, sem migração): o ator o recebe, e a limpeza se comprova pela árvore quando
    ele sai, sem IA."""
    resource_id: str
    texto: str
    bounds: tuple[int, int, int, int]

    def variaveis(self) -> dict[str, str]:
        return {"cobre_id": self.resource_id, "cobre_texto": self.texto,
                "cobre_bounds": ",".join(str(v) for v in self.bounds)}

    @staticmethod
    def das_variaveis(variaveis: dict[str, str] | None) -> Cobertura | None:
        v = variaveis or {}
        if "cobre_bounds" not in v:
            return None
        try:
            x1, y1, x2, y2 = (int(p) for p in v["cobre_bounds"].split(","))
        except ValueError:
            return None
        return Cobertura(v.get("cobre_id", ""), v.get("cobre_texto", ""), (x1, y1, x2, y2))

    @staticmethod
    def do_elemento(e: UiElement) -> Cobertura:
        return Cobertura(e.resource_id, (e.text or e.desc or "")[:120], e.bounds)


def cobertura_na_arvore(tree: UiTree, ref: str | None) -> Cobertura | None:
    """31.40 b (ii): o elemento que cobre — o que o juiz citou (`Verdict.cobre`), senão o primeiro da árvore com cara de
    diálogo, banner ou cookies. Nenhum dos dois: `None` (a limpeza segue sem ele, com UM julgamento)."""
    if ref and (e := tree.by_id(ref)) is not None:
        return Cobertura.do_elemento(e)
    for e in tree.elements:
        if _PISTAS_DE_COBERTURA.search(e.class_name or "") or _PISTAS_DE_COBERTURA.search(e.resource_id or ""):
            return Cobertura.do_elemento(e)
    return None


def ainda_cobre(c: Cobertura, tree: UiTree) -> bool:
    """31.40 b (i): o elemento que cobria segue na árvore E na mesma área. Sumiu, ou saiu da área que cobria: a limpeza
    está feita. Sem resource-id nem texto, só a mesma caixa conta como o mesmo elemento."""
    x1, y1, x2, y2 = c.bounds
    for e in tree.elements:
        if c.resource_id or c.texto:
            mesmo = ((not c.resource_id or e.resource_id == c.resource_id)
                     and (not c.texto or c.texto in ((e.text or "")[:120], (e.desc or "")[:120])))
        else:
            mesmo = e.bounds == c.bounds
        a1, b1, a2, b2 = e.bounds
        if mesmo and a1 < x2 and x1 < a2 and b1 < y2 and y1 < b2:
            return True
    return False


@dataclass(slots=True)
class StepOutcome:
    outcome: Outcome
    detail: str | None = None
    needs: str | None = None
    delivery_level: DeliveryLevel | None = None
    items: list[str] | None = None   # etapa de coleta: itens lidos (o scheduler expande o bloco for_each com eles)
    plan_defect: bool = False        # a pós-condição não é comprovável por tela: repetir ou refazer o MESMO plano não resolve
    #: 29.49: refazer a navegação (a recuperação automática) leva à mesma tela e ao mesmo resultado — a leitura visual
    #: que o leitor recusou e o ator repetiu. O item termina sem plano revisado; os outros aparelhos seguem.
    sem_recuperacao: bool = False
    # Item 7.3: motivo ESTRUTURADO do bloqueio, quando o outcome é `waiting_user` por causa da IA (not_configured |
    # billing | refusal) — o scheduler grava isto em `objectives.blocked_kind='ai'` para a interface distinguir
    # "a IA está travando este item" de política/limite/aprovação, em vez de só um texto livre.
    ai_blocked: bool = False
    #: ADR-055: a etapa parou numa tela de verificação da conta. Não é veredito sobre a receita nem sobre o plano — a
    #: conta travou, o caminho não errou; contar isso levaria à quarentena uma receita que funciona nas outras contas.
    trava_da_conta: bool = False
    #: Item 22.3: a tela onde a tentativa parou (`tela_da_falha`), que vai para `attempts.failure_screen`. Quem preenche
    #: é o scheduler (`_run_guarded`), o único que vê também o desfecho montado para a exceção — salvo quando o executor
    #: já sabe e a última árvore não diz: a verificação achada DENTRO de uma ferramenta (`quick_tree` não atualiza
    #: `rt.last_tree`) sai com o tipo da trava daqui, e o scheduler o respeita.
    tela_da_falha: str | None = None
    #: Contrato C2 (ADR-058): os valores que a etapa leu, por nome (`PlanStep.saidas`), para as etapas seguintes do
    #: objetivo (`Repository.save_step_output`). `None` = a etapa não produziu saída; quem preenche é a Fase 24.
    outputs: dict[str, str] | None = None
    #: RA-22: o `AIError.kind` que encerrou a etapa (o desfecho saiu de um erro de IA), que vai para
    #: `attempts.error_kind` e decide o tipo da falha antes do texto (`classificar_falha`). `None` = não foi erro de IA.
    ai_error_kind: str | None = None
    #: 29.58 (C): o resultado a gravar na etapa quando o desfecho NÃO é sucesso (hoje: `uncertain` por efeito repetido,
    #: com `efeito_repetido`). O sucesso grava o dele no próprio executor.
    result: StepResult | None = None
    #: 29.35 (RA-9): o ator relatou `missing_info` e a falta NÃO é credencial (senha, código, 2FA ficam com a pessoa,
    #: ADR-009). O scheduler tenta UMA revisão determinística do plano (a mesma da recuperação automática, com o motivo
    #: "falta de informação") antes de parar o objetivo em `waiting_user`: "o campo X não existe" quase sempre é o plano
    #: na tela errada, não informação que a pessoa precise dar.
    falta_de_informacao: bool = False
    #: Item 31.38: a etapa de LEITURA não achou o valor (o ator relatou `dado_ausente` ou o teto de decisões da leitura
    #: estourou). Não há nova tentativa da MESMA etapa; o scheduler dá UM plano revisado por objetivo, e a segunda vez
    #: fecha o objetivo como falha.
    dado_ausente: bool = False
    #: Item 31.40: o juiz recusou a etapa sem efeito porque algo COBRE o alvo. O scheduler insere a limpeza opcional
    #: antes dela (uma vez por objetivo) em vez de repetir a mesma etapa.
    sobreposicao: bool = False
    #: 31.40 b: o elemento que cobre (do id do juiz ou da árvore), que a limpeza recebe; `None` quando não se achou.
    cobertura: Cobertura | None = None


class OrcamentoDaEtapa(AIError):
    """Item 18.3: o orçamento de chamadas da AÇÃO estourou. É `kind="budget"` como os outros tetos; a classe própria
    deixa o executor dar, numa etapa de leitura, o desfecho do 31.38 (dado ausente) em vez da falha genérica."""


#: Item 31.38: o começo do `detail` da etapa de leitura que não achou o valor (o scheduler e o plano revisado o reconhecem).
PREFIXO_DADO_AUSENTE = "Dado ausente:"

#: A mesma triagem que decide se uma pergunta pede credencial (29.52): uma só, para a falta de informação também.
_TRIAGEM_DE_CREDENCIAL = TriagemDeCredencial()


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


#: Telas gravadas pelo TIPO do motor, e não pelo nome que o app lhes dá: é o vocabulário que a exclusão das lições lê
#: (`modules/learning/domain/licoes.TELAS_EXCLUIDAS`). Gravado como `challenge` (o nome do Instagram), o desafio
#: passaria pela exclusão e uma falha nele poderia virar lição (ADR-009: desafio e código seguem com a pessoa).
_TELA_PELO_TIPO = frozenset({*telas_do_app.TIPOS_DE_TRAVA, "login"})


def tela_da_falha(arvore: object, pacote: str | None) -> str | None:
    """`attempts.failure_screen` (item 22.3): a tela da última observação, pelo conhecimento DECLARADO do app da etapa
    (`conhecimento/apps/<pacote>/telas.yaml`, o mesmo classificador do motor de sessão); `None` quando desconhecida.

    Nunca texto da tela — só o nome de uma regra do repositório ou, nas telas de verificação e de login, o tipo do
    motor (`_TELA_PELO_TIPO`). As aprendidas ficam de fora de propósito: dependem do modo do livro, e a tela de uma
    falha mudaria de grupo no backlog ao ligar ou desligar o aprendizado. Outro app na frente, app sem conhecimento
    ou conhecimento inválido: `None`. Registro nunca derruba a etapa já decidida: o corpo inteiro fica no `try`, o
    desfecho da etapa já foi decidido quando esta função roda."""
    try:
        if not isinstance(arvore, UiTree) or not pacote or not pacote.replace(".", "").replace("_", "").isalnum():
            return None
        k = telas_do_app.da_pasta(CONHECIMENTO_DE_APPS / pacote)
        if k is None:
            return None
        # O pacote da frente pela mesma regra da observação (`DeviceManager.observe`).
        frente = next((p for p in arvore.packages if p != "com.android.systemui"), None)
        r = telas_do_app.classificar(k, arvore, package=frente)
        if r.tipo in _TELA_PELO_TIPO:
            return r.tipo
        if r.tela == telas_do_app.DESCONHECIDA:
            # O formulário de login é geometria do motor de sessão, que a fila não importa: campo de senha no app da
            # etapa é login — o mesmo critério do ciclo das telas (`ligar_telas.ObservadorDeTelas`).
            return "login" if not r.outro_app and any(e.password for e in arvore.elements) else None
        regra = k.regra(r.tela)
        return r.tela if regra is not None and not regra.aprendida else None
    except Exception:  # noqa: BLE001 - a tela da falha é registro: sem ela, a coluna fica nula
        log.exception("tela da falha de %s não classificada", pacote)
        return None


#: RA-10: os motivos de imagem com que a imagem VAI junto; nos demais, a chamada decide pela árvore.
#: Item 31.36: decisões do ator que uma etapa opcional (só limpa a tela) pode gastar antes de ser pulada.
LIMITE_DA_ETAPA_OPCIONAL = 3
#: Item 31.35: a barra do navegador, por pacote, que NÃO vai na árvore do prompt do ator (`ai.podar_ui_do_navegador`).
#: Lista fechada e explícita: diálogos próprios do Chrome (primeira execução, permissões) continuam no prompt. Na
#: ocorrência r-20261004090000-bbfe54 (28.12) as telas com árvore rica foram as de entrada nova mais cara (~4,2 mil).
UI_DO_NAVEGADOR: dict[str, frozenset[str]] = {
    "com.android.chrome": frozenset(f"com.android.chrome:id/{i}" for i in (
        "toolbar", "toolbar_container", "toolbar_buttons", "location_bar", "url_bar", "location_bar_status_icon",
        "url_action_container", "delete_button", "mic_button", "tab_switcher_button", "tab_count",
        "menu_button", "menu_button_wrapper", "home_button", "optional_toolbar_button", "bottom_toolbar",
        "control_container", "security_button")),
}


def _fila_encadeada(extras: list[Decision]) -> list[Decision]:
    """Item 31.35 (parte B): as ações seguintes que o executor aceita encadear. Nada com efeito externo, e `scroll` só
    como a última: depois de rolar, nenhum alvo escolhido antes continua no mesmo lugar."""
    fila: list[Decision] = []
    for d in extras:
        if d.tool in EFFECT_CAPABLE - {"tap", "long_press"} or d.tool in CONTROL_TOOLS:
            break
        fila.append(d)
        if d.tool == "scroll":
            break
    return fila


def _proxima_encadeada(fila: list[Decision], antes: UiTree | None, agora: UiTree,
                       history: list[str]) -> Decision | None:
    """Item 31.35 (parte B): a próxima ação encadeada, com o alvo achado na tela NOVA. Os ids de elemento são posição
    na árvore (`e12`) e mudam depois de uma ação; o alvo vale só se o MESMO elemento (id de recurso, texto, descrição e
    classe) aparece uma única vez agora. Sem isso, ou com alvo por coordenada, a fila acaba e o ator decide de novo."""
    d = fila.pop(0)
    alvo = d.args.get("element_id")
    if alvo is None:
        if d.args.get("x") is None and d.args.get("y") is None:   # sem alvo (rolar, esperar, voltar)
            return d
        history.append(f"(executor) ação encadeada {d.tool} descartada: alvo por coordenada, sem conferência")
        fila.clear()
        return None
    original = antes.by_id(str(alvo)) if antes is not None else None
    chave = ((original.resource_id, original.text, original.desc, original.class_name)
             if original is not None else None)
    iguais = ([e for e in agora.elements if (e.resource_id, e.text, e.desc, e.class_name) == chave]
              if chave and any(chave[:3]) else [])
    if len(iguais) != 1:
        history.append(f"(executor) ação encadeada {d.tool} descartada: o alvo não está (ou não é único) na tela nova")
        fila.clear()
        return None
    return Decision(tool=d.tool, args={**d.args, "element_id": iguais[0].id}, raw_text=d.raw_text)


_IMAGEM_VAI: frozenset[str] = frozenset({"politica_sempre", "pedida", "problema", "primeira_julgada",
                                         "primeira_da_leitura", "arvore_pobre"})
#: Item 31.37: ferramentas que só LEEM a tela. Repeti-las não é ciclo sem progresso: na leitura do Outlook (d62546,
#: e7df7c) `observe_screen` duas vezes contou como ciclo e escalou 2 ou 3 decisões para o Opus. A contagem segue
#: valendo para toda ferramenta que age na tela.
FORA_DO_CICLO: frozenset[str] = frozenset({"observe_screen", "read_value"})
#: RA-10: a frase da linha do tempo de cada motivo de escalonamento do `decide` (a do efeito vem da política de risco).
_FRASE_DO_ESCALONAMENTO: dict[str, str] = {
    "nova_tentativa": "nova tentativa da mesma etapa", "erros_seguidos": "erros seguidos",
    "bloqueio": "bloqueio relatado pelo modelo de ação (item 17.10)",
    "piso": "alvo inexistente na tela (piso do modelo local, item 7.8)", "ciclo": "ação repetida na mesma tela"}


def veredito_da_chamada(resultado: object) -> str | None:
    """RA-10: o desfecho da chamada em vocabulário fechado (`ai_calls.verdict`). No `decide`, o nome da ferramenta só
    quando é uma delas: o nome vem do modelo, e fora da lista vira `desconhecida`."""
    if isinstance(resultado, Verdict):
        return resultado.satisfied
    if isinstance(resultado, Decision):
        return resultado.tool if resultado.tool in TOOLS else "desconhecida"
    if isinstance(resultado, Plan):
        return "pergunta" if resultado.missing and not resultado.steps else "plano"
    return None


@dataclass(slots=True)
class AiBreakerTrip:
    """Última vez que o disjuntor de conta de IA disparou: `/api/health` e a aba IA leem isto."""
    kind: str            # billing | not_configured
    message: str
    run_id: str
    at: str               # ISO 8601


def evidencia_da_conta(account_label: str | None, pos_condicao: str, texto: str | None) -> str | None:
    """O que a etapa comprovada grava como conta observada (`instances.account_evidence`), ou None.

    Só quando o rótulo aparece na pós-condição ou na prova; e a evidência é a frase que NOMEIA a conta. A prova pela
    árvore local é um seletor cru, sem o nome ("…sem IA (selector:id=…|text=={username})"), e o painel a lia como
    conta diferente do rótulo (validação do deploy 8, android-06): sem o rótulo na prova, vale a pós-condição. A prova
    por seletor com o rótulo dentro ("seletor id=…|text=qa-user-10: 1 elemento(s)") vira "qa-user-10 visto na tela"
    (`conta_observada.evidencia_legivel`, validação do deploy 9)."""
    if not account_label or norm_text(account_label) not in norm_text(pos_condicao + " " + (texto or "")):
        return None
    bruta = texto if texto and norm_text(account_label) in norm_text(texto) else pos_condicao
    return evidencia_legivel(account_label, bruta)


class StepExecutor:
    def __init__(self, cfg: Config, repo: Repository, devices: DeviceManager, provider: AIProvider,
                 ai_limiter: Limiter, settings_getter: Callable[[], Any]):
        self.cfg = cfg
        self.repo = repo
        self.devices = devices
        self.provider = provider
        self.ai_limiter = ai_limiter
        self.get_settings = settings_getter
        #: T.2: como as ferramentas esperam o aparelho assentar (vai no `ToolContext`). Produção: `asyncio.sleep`; o
        #: teste que pula o tempo troca por um que avança o relógio do aparelho falso (`tests/relogio_virtual.py`).
        self.dormir: Callable[[float], Awaitable[None]] = asyncio.sleep
        # RA-20: herdar só com a prova em sombra. Com `recipes_promote_after: 0` (o modo anterior) a receita que a IA
        # aprende já nasce ativa; uma herdeira candidata ocuparia a chave no lugar dela e, com efeito, ainda pararia
        # esperando o dono depois da primeira concordância.
        self.recipes = RecipeStore(repo.db, herdar=lambda: (self.cfg.file.ai.recipes_heranca
                                                            and self.cfg.file.ai.recipes_promote_after > 0))
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
        #: 31.40 b: tentativas de limpeza (sem o elemento que cobria) que já gastaram o seu ÚNICO julgamento
        self._juiz_da_limpeza: set[str] = set()
        # Pacote "anr": etapas que já gastaram a sua reabertura determinística depois de um ANR. Por ETAPA, não por
        # tentativa — na r-20260928195344-02ee9e cada tentativa acabava pelo prazo e a seguinte reabria de novo. Some
        # no desfecho final da etapa. Memória do processo: reiniciado o backend, a contagem de mortes (que vem do
        # aparelho, desde `steps.started_at`) continua valendo e o pior caso é UMA reabertura a mais.
        self._reabertas_por_anr: set[str] = set()
        # LT-12: a última ação decidida em cada etapa, como (tela estrutural, ação) — a da tentativa anterior é o que a
        # retentativa NÃO repete no modelo barato. Some no desfecho final. Memória do processo: reiniciado o backend, a
        # retentativa começa no tier 0 sem este gatilho (os outros — erros seguidos, ciclo, efeito — seguem valendo).
        self._ultima_acao_da_etapa: dict[str, tuple[str, str]] = {}
        # Item 31.24 (C-4): o juiz e a evidência de cada tentativa EM CURSO, somados enquanto ela roda e gravados uma
        # vez no fim (`_registrar_estrategia`). Some no fim da tentativa, saia ela como sair.
        self._tempos_da_tentativa: dict[str, TemposDaTentativa] = {}
        # Disjuntor de conta de IA (achado #90): por execução, a PRIMEIRA falha de cobrança/credencial represa
        # as etapas seguintes sem gastar tentativa — os aparelhos seguintes nem chegam a chamar o provedor.
        self._tripped_runs: dict[str, AiBreakerTrip] = {}
        self.ai_breaker: AiBreakerTrip | None = None        # a mais recente, de qualquer execução — para a saúde
        #: ADR-025/040, injetados pelo AppState: o cofre onde está a senha da conta da persona e o canal
        #: sensível que a digita. Sem os dois, `type_secret` recusa (o valor nunca toma o caminho de `type_text`).
        self.secrets: Any = None
        self.sensitive_input: Any = None
        #: 29.30, injetado pelo AppState: o serviço de imagens da persona, de onde `PUT_MEDIA_IN_GALLERY` tira a mídia.
        #: Sem ele a etapa falha com o motivo (nada vai ao aparelho).
        self.persona_images: PersonaImageService | None = None
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
                  attempt_id: str | None = None, marca: MarcaDaChamada | None = None,
                  preparo: PreparoDaDecisao | None = None) -> Any:
        """Ponto único de toda chamada de IA de uma execução: disjuntor, tetos, limite global e novas tentativas.

        `objective_id=None` é uso ligado à execução mas a objetivo nenhum — é assim que o PLANEJAMENTO passa a
        entrar aqui (achado #96, item 4): ele chamava `provider.plan` direto e ficava fora da repetição com
        espera e da conferência de orçamento.

        `deadline` é o `time.monotonic()` em que a ETAPA vence. A chamada é cortada no que sobra dele: sem isso,
        um provedor pendurado segurava a vaga de IA e o aparelho para além do prazo da etapa, que só era conferido
        no topo do laço.

        `attempt_id` (migração 045): a tentativa que paga a chamada, gravada em `ai_calls` — custo e modelo por
        tentativa, não só por etapa. O planejamento não tem tentativa (nulo).

        `marca` (RA-10, migração 080): para que a chamada foi feita, por que subiu de modelo e por que a imagem foi. Vai
        para a linha da chamada, inclusive a de erro e a de orçamento recusado (a recusa de uma decisão escalada ainda
        é de uma decisão escalada); o veredito sai do resultado.

        Item 31.24 (migração 088): toda linha que sai daqui leva `started_at` (a hora em que a chamada foi entregue ao
        provedor, já com a vaga) e `vaga_ms` (a espera pela vaga). `preparo` é o que o executor gastou para chegar a
        esta decisão do ator: vai só na linha da resposta, e `_ai` devolve nele o id dela (`preparo.ai_call_id`).
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
            teto, origem = self._teto_de_chamadas(objective_id, int(obj["ai_calls"]), s)
            if obj["ai_calls"] >= teto:
                exc = AIError(f"Limite de {teto} chamadas de IA por objetivo atingido"
                              + (f" ({origem})." if origem != f"{teto}" else "."), kind="budget")
                self._registrar_orcamento_estourado(run_id, objective_id, step_id, role, exc, attempt_id, marca)
                raise exc
        if step_id is not None and objective_id is not None and self.cfg.file.ai.step_budget.enabled:
            self._conferir_orcamento_da_etapa(run_id, objective_id, step_id, role, run["app_ids"] if run else None,
                                              attempt_id, marca)
        if run and (run["ai_input_tokens"] + run["ai_output_tokens"]) >= s.ai_max_tokens_per_run:
            exc = AIError(f"Orçamento de {s.ai_max_tokens_per_run} tokens da execução esgotado.", kind="budget")
            self._registrar_orcamento_estourado(run_id, objective_id, step_id, role, exc, attempt_id, marca)
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
            t_vaga = time.monotonic()
            async with self.ai_limiter:       # limite de chamadas simultâneas ao modelo (≠ aparelhos ativos)
                vaga_ms = ms_desde(t_vaga)
                if objective_id is not None:
                    self.repo.note_waiting(objective_id, f"aguardando resposta do modelo ({role or 'ia'})",
                                           wait_reason="model_response")
                entregue = now_iso()
                try:
                    result, usage = await _com_prazo(coro_factory(), deadline, role)
                    uso = self._carimbar(usage if usage.calls or self.provider.simulated else Usage(calls=1), marca,
                                         result)
                    uso.started_at, uso.vaga_ms, uso.preparo = entregue, vaga_ms, preparo
                    chamada = self.repo.add_usage(run_id, objective_id, uso, step_id=step_id, attempt_id=attempt_id)
                    if preparo is not None:
                        preparo.ai_call_id = chamada
                    return result
                except AIError as exc:
                    # Achado #101: o log é o único jeito de casar uma chamada com erro à exceção real quando o
                    # texto gravado não basta (era assim que se sabia que um 5xx do provedor virou "Verificação
                    # não pôde ser feita" — casando horário com data/logs/backend.log). E a linha de custo passa
                    # a gravar o modelo REALMENTE pedido e o tipo do erro — nunca mais o pseudo-modelo '(erro)'.
                    log.warning("%s: chamada de IA (%s) falhou: %s", role or "ia", exc.kind, exc, exc_info=True)
                    falha = self._uso_sem_resposta(role, marca, exc.model)
                    falha.started_at, falha.vaga_ms = entregue, vaga_ms
                    self.repo.add_usage(run_id, objective_id, falha,
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

    def _teto_de_chamadas(self, objective_id: str, feitas: int, s: LimitsCfg) -> tuple[int, str]:
        """Item 17.12: o teto de chamadas deste objetivo — `ai_max_calls_per_objective` fixo, ou proporcional aos itens do
        `for_each` (`foreach.teto_de_chamadas`). Os itens saem das etapas JÁ GRAVADAS (`item_index` em `steps.variables`,
        em todas as versões do plano: uma recuperação recomeça só com o que faltava e os itens já feitos continuam
        contando), não de estado em memória — sobrevive a reinício e à recuperação. Só consulta o banco quando o teto
        base já foi atingido: abaixo dele a resposta não depende do número de itens."""
        base = int(s.ai_max_calls_per_objective)
        por_item = int(s.ai_max_calls_per_item)
        if por_item <= 0 or feitas < base:
            return base, f"{base}"
        indices = {(loads(r["variables"], {}) or {}).get("item_index")
                   for r in self.repo.db.query("SELECT variables FROM steps WHERE objective_id=? AND variables LIKE ?",
                                               (objective_id, '%"item_index"%'))}
        indices.discard(None)
        return teto_de_chamadas(base, por_item, int(s.ai_max_calls_absolute), len(indices))

    def _conferir_orcamento_da_etapa(self, run_id: str, objective_id: str, step_id: str, role: str,
                                     app_ids: object, attempt_id: str | None,
                                     marca: MarcaDaChamada | None = None) -> None:
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
            exc = OrcamentoDaEtapa(f"A etapa passou do orçamento de {limite} chamadas de IA para {nome}: o normal, em "
                          f"{est.amostras} etapas concluídas nos últimos {janela} dias, é {normal}. Parada "
                          "para não girar até o prazo.", kind="budget")
            self._registrar_orcamento_estourado(run_id, objective_id, step_id, role, exc, attempt_id, marca)
            raise exc
        if feitas > est.chamadas.p90 and step_id not in self._acima_do_normal:
            self._acima_do_normal.add(step_id)
            self.repo.decision(f"{step['instance_id']}: etapa '{step['title']}' acima do normal — {feitas} chamadas "
                               f"de IA; o normal para {nome} é {normal} (para em {limite})",
                               run_id=run_id, instance_id=step["instance_id"], step_id=step_id)

    def _registrar_orcamento_estourado(self, run_id: str, objective_id: str | None, step_id: str | None, role: str,
                                       exc: "AIError", attempt_id: str | None = None,
                                       marca: MarcaDaChamada | None = None) -> None:
        """Achado #99: os dois tetos de ORÇAMENTO (chamadas por objetivo, tokens por execução) recusam ANTES de
        entrar no laço de tentativas — nenhum provedor é chamado, de propósito. Sem esta linha, a recusa nunca
        virava uma linha em `ai_calls` e `/api/usage` não mostrava NADA sobre o estouro (nem em `errors_by_kind`
        nem no painel), embora a etapa e o objetivo já tivessem parado por causa dele. `calls=1` conta como
        tentativa recusada (é o mesmo `Usage` que o laço grava para qualquer erro), sem custo (0 tokens)."""
        self.repo.add_usage(run_id, objective_id, self._uso_sem_resposta(role, marca),
                            step_id=step_id, ok=False, error_kind=exc.kind, error_status=exc.status,
                            error_message=str(exc), attempt_id=attempt_id)

    def _tem_leitor(self) -> bool:
        """Há um leitor para a leitura visual (barreira 8)? O roteador só tem o papel `leitura` quando `ai.roles.leitura`
        está escrito; um provedor sem `roles` (simulado, dublê de teste) responde pelo que implementa."""
        roles = getattr(self.provider, "roles", None)
        return hasattr(self.provider, "transcribe") and (roles is None or "leitura" in roles)

    def _leitor_visual(self) -> str:
        """`provedor/modelo` do leitor, para a ação, a evidência e a coluna `step_outputs.leitor`."""
        roles = getattr(self.provider, "roles", None)
        if roles and "leitura" in roles:
            return f"{roles['leitura'].provider}/{roles['leitura'].model}"
        return f"{getattr(self.provider, 'name', 'leitor')}/{getattr(self.provider, 'model', '')}"

    def _uso_sem_resposta(self, role: str, marca: MarcaDaChamada | None, modelo: str | None = None) -> Usage:
        """A linha de uma chamada que o provedor não respondeu (erro ou orçamento recusado antes de chamar). RA-10: o
        provedor e o modelo são os da função que a chamada usaria — a de escalonamento, quando a marca diz que ela subiu
        —, porque `provider` nulo deixava a linha fora do custo por conta e da conferência do RA-10."""
        papel = "escalation" if marca is not None and marca.escalate is not None else role
        return self._carimbar(Usage(calls=1, role=role, model=modelo or self._role_model(papel),
                                    provider=self._role_provider(papel)), marca)

    @staticmethod
    def _carimbar(usage: Usage, marca: MarcaDaChamada | None, resultado: object = None) -> Usage:
        """RA-10: o que o executor sabe da chamada (`marca`) e o desfecho dela (`veredito_da_chamada`) na linha de
        `ai_calls`. A chamada que subiu ao modelo de escalonamento é tier 1 também no `verify`: o provedor do verificador
        não recebe tier, e o rejulgamento era gravado como `verify` tier 0."""
        if marca is not None:
            usage.motivo, usage.escalate, usage.image_reason = marca.motivo, marca.escalate, marca.image_reason
            if marca.escalate is not None:
                usage.tier = max(usage.tier, 1)
        usage.verdict = veredito_da_chamada(resultado)
        return usage

    def _role_provider(self, role: str) -> str:
        """O endpoint configurado para esta função (a mesma busca de `_role_model`), para a linha de uma chamada que o
        provedor não respondeu."""
        roles = getattr(self.provider, "roles", None)
        if roles and role in roles:
            return getattr(roles[role], "provider", "") or ""
        return getattr(self.provider, "name", "") or ""

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

    def _want_image(self, tree: UiTree, *, judged_step: bool, first: bool, trouble: bool, requested: bool,
                    ai: AiCfg | None = None, le_valor: bool = False) -> bool:
        """Política `ai.image_policy`: se a imagem vai junto. O porquê é `_motivo_da_imagem`, a única régua."""
        return self._motivo_da_imagem(tree, judged_step=judged_step, first=first, trouble=trouble,
                                      requested=requested, ai=ai, le_valor=le_valor) in _IMAGEM_VAI

    def _motivo_da_imagem(self, tree: UiTree, *, judged_step: bool, first: bool, trouble: bool,
                          requested: bool, ai: AiCfg | None = None, le_valor: bool = False) -> MotivoDaImagem:
        """Política `ai.image_policy`. A imagem custa ~1/3 dos tokens novos de cada chamada; a hierarquia quase sempre
        basta. Em `auto` a imagem vai quando a árvore é pobre (WebView/canvas), na 1ª decisão de etapa julgada por
        visão, depois de erro/ciclo, ou quando o próprio modelo pede (observe_screen.need_image).

        Decide pela ÁRVORE, antes de a imagem existir (adendo v0.20, C1): o resto do que pesa aqui já se sabe antes
        de observar, então a imagem só é adquirida quando vai ser mandada. RA-10: devolve o MOTIVO (`ai_calls.
        image_reason`), na ordem em que a regra decide; vai junto quando ele está em `_IMAGEM_VAI`.
        `ai`: o bloco da execução (17.14, `_ai_da_execucao`); vazio = o global."""
        ai = ai or self.cfg.file.ai
        if tree.sensitive:
            return "sensivel"
        if ai.image_policy == "never":
            return "politica_nunca"
        if ai.image_policy == "always":
            return "politica_sempre"
        if requested:
            return "pedida"
        if trouble:
            return "problema"
        if first and judged_step:
            return "primeira_julgada"
        if first and le_valor:
            # Item 31.37: a etapa que LÊ um valor (`saidas`) quase sempre precisa ver a tela; sem a imagem, a 1ª
            # decisão era um `observe_screen(need_image)` pago só para pedi-la (d62546, e7df7c).
            return "primeira_da_leitura"
        informative = sum(1 for e in tree.elements if e.text or e.desc or e.clickable or e.editable)
        return "arvore_pobre" if informative < ai.rich_tree_min_elements else "arvore_rica"

    def _image_scale(self, obs: Observation, ai: AiCfg | None = None) -> float:
        """Pixels do aparelho por pixel do espaço de coordenadas que o modelo enxerga."""
        return max(1.0, max(obs.width, obs.height) / (ai or self.cfg.file.ai).screenshot_max_side)

    def _ai_da_execucao(self, run_id: str) -> AiCfg:
        """O bloco `ai` desta execução (17.14): o global com o lado da imagem e o mínimo da árvore rica do perfil dela
        por cima (`Config.ai_da_execucao`). Sem perfis na configuração, nem lê o banco: é o objeto de sempre."""
        if not self.cfg.file.ai.profiles:
            return self.cfg.file.ai
        perfil = self.repo.db.scalar("SELECT ai_profile FROM runs WHERE id=?", (run_id,))
        return self.cfg.ai_da_execucao(perfil or None)

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
               boost: tuple[str, ...] = (), ai: AiCfg | None = None) -> tuple[ScreenInput, float]:
        jpeg = obs.jpeg if with_image else None
        # com ou sem imagem, x,y do modelo vivem no mesmo espaço reduzido — a MESMA conta de quem codificou a imagem
        w, h, scale = dimensoes_do_modelo(obs.width, obs.height, (ai or self.cfg.file.ai).screenshot_max_side)
        if jpeg:
            with Image.open(io.BytesIO(jpeg)) as img:     # só o cabeçalho: a observação já pode vir no tamanho certo
                pronta = img.size == (w, h)
            if not pronta:                            # imagem cheia (observação antiga): reduz aqui, como antes
                buf = io.BytesIO()
                Image.open(io.BytesIO(jpeg)).resize((w, h)).save(buf, "JPEG", quality=72)
                jpeg = buf.getvalue()
        ocultar = (UI_DO_NAVEGADOR.get(obs.package or "", frozenset())
                   if (ai or self.cfg.file.ai).podar_ui_do_navegador else frozenset())
        podados = sum(1 for e in obs.tree.elements if e.resource_id in ocultar) if ocultar else 0
        lines = obs.tree.prompt_lines(self.cfg.file.ai.max_hierarchy_elements, scale, protect=protect, boost=boost,
                                      ocultar=ocultar)
        return ScreenInput(width=w, height=h, jpeg=jpeg, elements=lines, package=obs.package,
                           sensitive=obs.sensitive, tree=obs.tree, podados=podados), scale

    # ------------------------------------------------------------------ etapa
    async def run_step(self, *, run: Any, objective: Any, step: StepDTO, attempt_id: str, rt: DeviceRuntime,
                       app: AppContext, account_label: str | None, remaining: list[str],
                       stop_reason: Callable[[], str | None], resumed_after_manual: bool) -> StepOutcome:
        """Etapa com receitas: procura a receita, executa, e depois contabiliza o replay ou aprende com a IA."""
        mode = self.cfg.file.ai.recipes
        leitura = mode != "off" and bool(saidas_exigidas(self.repo.saidas_da_etapa(step.id),
                                                         capability_of(app.package, step.capability)))
        if leitura:
            # Item 24.3: a etapa que entrega um valor às seguintes precisa LER (`read_value`), e ler é decisão sobre a
            # tela da vez — a receita reproduziria os gestos e concluiria sem valor nenhum.
            mode = "off"
        if step.opcional:
            # 31.40 b: a limpeza fecha o diálogo DA VEZ; a receita repetiria os toques de um diálogo noutro.
            mode = "off"
        self._effects.pop(step.id, None)
        rr = _RecipeRun(mode=mode, leitura=leitura)
        fired_at_entry, _ = self.repo.commit_state(step.id)
        if mode != "off" and app.package and not fired_at_entry:
            try:
                rr.app_version = await self.devices.app_version(rt, app.package)
                linha = self.repo.step_row(step.id)
                rr.step_hash = linha["template_hash"]
                # RA-20 B: a 2ª chave, a da receita que serve a qualquer valor. Pela linha, não pelo DTO: o DTO não traz
                # o `template_key` da cópia do for_each e vem com as variáveis resolvidas.
                rr.step_hash_generico = hash_generico_da_linha(linha)
                # As saídas do objetivo entram como `saida_<nome>` (item 24.3): a identidade da etapa é a do MOLDE
                # (`{{saida:x}}`), então a receita aprendida digitando "@ana" reproduziria "@ana" quando o valor lido
                # fosse "@bia" — com a variável, `distill` guarda `{saida_x}` e a reprodução digita o valor da vez.
                rr.variables = {**loads(objective["parameters"], {}), "instance_id": rt.id, "run_id": run["id"],
                                "account_label": account_label or "",
                                **variaveis_da_receita(self.repo.saidas_com_tipo(objective["id"]),
                                                       step.variables.get("item_index"),
                                                       self.repo.saidas_visuais(objective["id"])),
                                **step.variables}
                rr.signature = self._installed_signature(rt.id, app.package)
                rr.variant = await self.devices.variant_of(rt)
                rr.row = self.recipes.find(app.package, rr.app_version, rr.step_hash,
                                           signature=rr.signature, variant=rr.variant,
                                           step_hash_generico=rr.step_hash_generico)
                if rr.row is not None:
                    rr.replayer = self.recipes.replayer(rr.row, rr.variables)
                    if rr.row["status"] == "candidate":
                        rr.mode = "shadow"      # em prova: a IA decide a etapa e a receita só é comparada
            except Exception as exc:  # noqa: BLE001 - receita é otimização: nunca derruba a etapa
                log.warning("%s: receitas indisponíveis nesta etapa: %s", rt.id, exc)
                rr = _RecipeRun(mode="off")
        fechada = False
        self._tempos_da_tentativa[attempt_id] = TemposDaTentativa()
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
            self._ultima_acao_da_etapa.pop(step.id, None)
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
        tempos = self._tempos_da_tentativa.pop(attempt_id, None)      # 31.24 (C-4): no mesmo UPDATE
        try:
            self.repo.note_attempt_strategy(attempt_id, ">".join(rr.exercised) or None, receita, tempos)
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
    def _excecao_encerrada(self, step: StepDTO) -> str | None:
        """30.65: o motivo literal quando a exceção de política presa a esta etapa foi encerrada por uma pessoa depois
        da porta (revogada pela rota, ou o cartão recusado). A porta não roda de novo no meio da etapa; sem isto a DM
        aprovada sairia com a exceção já revogada e a rota teria respondido 200 como se a tivesse impedido."""
        excecoes = getattr(self.social, "excecoes", None)
        x = excecoes.encerrada_da_etapa(step.id) if excecoes is not None else None
        if x is None:
            return None
        return (f"a exceção {x.id} à regra de uma conta por alvo foi {x.encerramento} por {x.encerrada_por or 'uma pessoa'}"
                " depois da aprovação; o efeito não foi disparado (30.65)")

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

    def _gravar_saidas(self, step: StepDTO, app: AppContext, lidos: dict[str, tuple[str, str]],
                       visuais: dict[str, tuple[str, str, int | None]] | None = None) -> None:
        """Os valores lidos, na tabela de saídas (contrato C2), com o app onde foram lidos (`None` = o do plano). Chamado
        dentro da transação que comprova a etapa: o valor só existe para as seguintes se a etapa que o leu valeu.
        `visuais` (item 12.5): os nomes lidos da imagem, com leitor, sha256 do recorte e evidência; os demais são `arvore`."""
        for nome, (valor, tipo) in lidos.items():
            leitor, sha, evidencia = (visuais or {}).get(nome, (None, None, None))
            self.repo.save_step_output(step.id, nome, valor, value_kind=tipo, app_id=app.id,
                                       origem="visual" if leitor else "arvore", leitor=leitor, frame_sha256=sha,
                                       evidence_id=evidencia)

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
        if rr.leitura and outcome.outcome not in (Outcome.yielded, Outcome.cancelled, Outcome.retry):
            # A receita ficou de fora só por ser etapa de leitura (item 24.3): quem conduziu foi a IA, e a trilha diz
            # isso como em qualquer etapa sem receita — com as receitas ligadas, `driven_by` nulo pareceria legado.
            self.repo.db.execute("UPDATE steps SET driven_by='ai' WHERE id=?", (step.id,))
        # RA-10: a IA decidiu nesta tentativa, e ela não foi cedida, cancelada nem devolvida para outra tentativa.
        conduziu_a_ia = (StrategyKind.ai_actor.value in rr.exercised
                         and outcome.outcome not in (Outcome.yielded, Outcome.cancelled, Outcome.retry))
        ok = outcome.outcome == Outcome.succeeded
        # LT-1: a etapa fechou por atalho do executor sem o ator decidir nada nem receita agir. É `sem_ator` — nunca `ai`
        # (o ator não conduziu) e nunca nulo (pareceria legado). Fora do modo de receitas o nome vale do mesmo jeito.
        sem_ator = rr.sem_ator and ok and not rr.leitura
        if sem_ator and rr.mode == "off":
            self.repo.db.execute("UPDATE steps SET driven_by='sem_ator' WHERE id=?", (step.id,))
            return
        if rr.mode == "off" or outcome.outcome in (Outcome.yielded, Outcome.cancelled):
            if rr.mode == "off" and conduziu_a_ia:
                # RA-10: receitas desligadas (ou indisponíveis nesta etapa) e a IA decidiu: quem conduziu foi ela. Sem
                # isto `driven_by` ficava nulo, e `/api/usage` o contava como IA por suposição (o COALESCE).
                self.repo.db.execute("UPDATE steps SET driven_by='ai' WHERE id=?", (step.id,))
            return                                     # tentativa interrompida (cedida, cancelada): não é veredito
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
            if conduziu_a_ia and not replayed:
                # RA-10: sem veredito sobre a receita (defeito do plano, trava da conta), mas quem conduziu foi a IA.
                self.repo.db.execute("UPDATE steps SET driven_by='ai' WHERE id=?", (step.id,))
            return
        repo = self.repo
        if na_receita:
            clean = ok and not rr.diverged
            quarantined = self.recipes.result(rr.row["id"], clean)
            driven = "recipe" if clean else ("sem_ator" if sem_ator else "recipe+ai")
            repo.db.execute("UPDATE steps SET driven_by=? WHERE id=?", (driven, step.id))
            if clean:
                pela = " pela chave genérica" if rr.row["step_hash"] != rr.step_hash else ""
                repo.decision(f"{iid} · {step.title}: receita v{rr.row['version']} reproduzida{pela} (0 decisões de IA)",
                              run_id=run_id, instance_id=iid, step_id=step.id)
            if quarantined:
                repo.decision(f"{iid} · {step.title}: receita v{rr.row['version']} em quarentena após falhas seguidas; "
                              "a etapa será reaprendida com a IA", run_id=run_id, instance_id=iid, step_id=step.id)
            return
        if sem_ator:
            # Nenhuma ação foi feita: não há caminho a aprender (receita vazia) e quem conduziu não foi a IA.
            repo.db.execute("UPDATE steps SET driven_by='sem_ator' WHERE id=?", (step.id,))
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
        # RA-19 B: a execução simulada não publica. A receita que ela ensina nasce candidata mesmo com
        # `recipes_promote_after: 0`, e só a concordância de uma execução real a promove (`RecipeStore.shadow`).
        simulada = self._origem_simulada(run_id)
        candidata = n > 0 or simulada
        # RA-20 B: o caminho que não traz o literal do valor desta etapa vale para qualquer valor e vai para a chave
        # genérica; o que traz (o toque em "nasa") fica na específica. A genérica que divergiu num valor e um caminho
        # específico: ela segue em prova para os outros valores (a divergência já foi contada) e a específica nasce
        # ao lado — sem `replaces`, que a trocaria.
        generica = bool(rr.step_hash_generico) and eh_generica(actions, step.postcondition.value, rr.variables,
                                                               titulo=step.title)
        chave = rr.step_hash_generico if generica else rr.step_hash
        if substitui is not None and not generica and rr.row["step_hash"] != rr.step_hash:
            substitui = None
        rid = self.recipes.save(package=app.package, app_version=rr.app_version, step_hash=chave,
                                step_key=step.key, actions=actions, learned_from=step.id,
                                signature=rr.signature, variant=rr.variant, candidate=candidata, replaces=substitui)
        if rid and generica and rr.step_hash_generico != rr.step_hash:
            repo.decision(f"{iid} · {step.title}: o caminho não depende do valor desta etapa — a receita vale para "
                          "qualquer valor (chave genérica)", run_id=run_id, instance_id=iid, step_id=step.id)
        if rid and candidata:
            no_lugar = f", no lugar da v{rr.row['version']}, que divergiu" if substitui else ""
            repo.decision(f"{iid} · {step.title}: receita aprendida como candidata ({len(actions)} ação(ões)){no_lugar}"
                          f" — a IA segue conduzindo esta etapa e a receita só é comparada; vira ativa depois de "
                          f"{max(1, n)} execução(ões) seguidas em que a IA fizer exatamente o caminho dela"
                          + (" (execuções reais: esta foi simulada)" if simulada else ""),
                          run_id=run_id, instance_id=iid, step_id=step.id)
        elif rid:
            repo.decision(f"{iid} · {step.title}: receita aprendida ({len(actions)} ação(ões)) — as próximas execuções "
                          "desta etapa dispensam a IA enquanto a tela casar", run_id=run_id, instance_id=iid, step_id=step.id)
        elif substitui:
            log.info("%s: etapa %s: candidata v%s não trocada (a IA comprovou o mesmo caminho, ou a chave já tem "
                     "ativa); segue em prova", iid, step.key, rr.row["version"])

    def _origem_simulada(self, run_id: str) -> bool:
        """RA-19 B: a execução é simulada (`runs.simulated=1`) e o que ela ensina não publica. `False` com
        `aprendizado.simulada_publica` (o modo anterior, só da suíte). Sem a linha da execução, simulada: nada se
        publica pelo que não se sabe de onde veio."""
        if self.cfg.file.aprendizado.simulada_publica:
            return False
        valor = self.repo.db.scalar("SELECT simulated FROM runs WHERE id=?", (run_id,))
        return valor is None or bool(valor)

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
        promovida = self.recipes.shadow(rr.row["id"], concordou, promote_after=self.cfg.file.ai.recipes_promote_after,
                                        simulada=self._origem_simulada(run_id))
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
            # O pacote vai junto: é a conta DAQUELE app que a tela desmentiu, não a do app da etapa (item 23.4).
            if subtipo is None:
                self.on_auth_needed(instance_id, kind, detail, package=package)
            else:
                self.on_auth_needed(instance_id, kind, detail, subtipo=subtipo, package=package)
        except Exception:  # noqa: BLE001 - corrigir o cache nunca pode derrubar a etapa
            log.exception("%s: falha ao atualizar o estado de sessão do perfil", instance_id)

    async def _run_interna(self, *, run: Row, objective: Row, step: StepDTO, attempt_id: str,
                           rt: DeviceRuntime) -> StepOutcome:
        """`PUT_MEDIA_IN_GALLERY` (29.30): a imagem da persona da conta deste aparelho vai para a galeria dele.

        Sem efeito na conta, sem tela. A conferência de posse e de estado da imagem acontece ANTES do push
        (`colocar_midia_na_galeria`): recusa nunca toca no aparelho. O mesmo `rt.adb` serve aparelho local e remoto (a
        central alcança o remoto pelo túnel, `docs/worker.md`); a prova real num aparelho remoto está `not_run`."""
        repo = self.repo
        # A persona da conta do aparelho: o perfil do OBJETIVO, ou o único do aparelho (a mesma resolução da porta de
        # política). O id do perfil é o da persona (051), por onde as imagens são guardadas.
        persona_id = objective["profile_id"] or (
            self.social.repo.perfil_unico_da_instancia(rt.id) if self.social is not None else None)
        # Aparelho falso (testes): o dublê tem o método; o `adb` de verdade não existe ali (o mesmo desvio da
        # conferência no app de QA, `Scheduler`).
        enviar = (getattr(rt.io, "enviar_midia_para_galeria", None) if self.devices.io_factory is not None
                  else rt.adb.enviar_midia_para_galeria)

        async def falha(motivo: str, *, tentar_de_novo: bool) -> StepOutcome:
            await repo.add_evidence_async(run_id=run["id"], instance_id=rt.id, step_id=step.id, attempt_id=attempt_id,
                                          kind="text", note=f"Falha: {motivo}")
            if tentar_de_novo and step.attempts < step.max_attempts:
                return StepOutcome(Outcome.retry, motivo)
            return StepOutcome(Outcome.failed, motivo)

        if enviar is None:
            return await falha("este aparelho não sabe receber mídia na galeria", tentar_de_novo=False)
        # 30.60 (achado 6): o perfil do objetivo precisa ter vínculo ATIVO com ESTE aparelho. Sem isso, a imagem de uma
        # persona iria para a galeria de outra (objetivo de A despachado num aparelho que só tem B). O vínculo secundário
        # conta (android-13 é também do André): é pertencer ao aparelho, não ser o único dele.
        if persona_id and self.social is not None and self.social.repo.binding(persona_id, rt.id) is None:
            return await falha(f"a persona do objetivo não está vinculada a {rt.id}: a imagem dela não vai para a galeria "
                               "de outro perfil (nada foi enviado ao aparelho)", tentar_de_novo=False)
        try:
            remoto = await rt.executor.run(colocar_midia_na_galeria, self.persona_images, persona_id,
                                           step.bindings.get("image_id"), enviar, timeout=float(step.timeout_s),
                                           label="mídia na galeria")
        except MidiaRecusada as exc:
            return await falha(str(exc), tentar_de_novo=False)              # repetir não muda a posse nem o estado
        except Exception as exc:  # noqa: BLE001 - AdbError, prazo, aparelho fora: o push é repetível
            return await falha(f"a mídia não chegou à galeria: {str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__}",
                               tentar_de_novo=True)
        texto = f"imagem colocada na galeria do aparelho ({remoto})"
        await repo.add_evidence_async(run_id=run["id"], instance_id=rt.id, step_id=step.id, attempt_id=attempt_id,
                                      kind="text", note=f"Pós-condição comprovada: {texto}")
        with repo.db.tx():
            repo.transition_step(step.id, StepStatus.succeeded, detail=texto,
                                 result=StepResult(verified=True, evidence_text=texto),
                                 message=f"Etapa '{step.title}' comprovada: {texto}")
        repo.finish_attempt(attempt_id, AttemptStatus.succeeded, observed=texto)
        return StepOutcome(Outcome.succeeded, texto)

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
        if cap is not None and cap.internal and cap.key in INTERNAS_POR_CODIGO:
            # Código determinístico, fora do laço da IA (como o login): não há observação, decisão nem verificador.
            return await self._run_interna(run=run, objective=objective, step=step, attempt_id=attempt_id, rt=rt)
        # A legenda da publicação alvo (`caption_contains`), quando o pedido a citou: vazio = post por posição, como
        # sempre. Com ela, a pós-condição exige o texto na tela e o toque de efeito só vale no cartão que o traz.
        cartao = guardas_do_cartao(cap.card_guard, step.bindings) if cap else ()
        collected: list[str] | None = None
        empty_collects = 0
        lista_ate_o_fim = False          # a última leitura vazia chegou ao FIM da lista (fato do executor)
        vazio_provado: str | None = None     # prova do vazio (item 12.4): a lista abriu sem item nenhum e a tela disse
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
        ausente = repo.db.one("SELECT status_detail FROM steps WHERE objective_id=? AND key=? AND plan_version<? AND "
                              "status='failed' AND status_detail LIKE ? ORDER BY plan_version DESC, id DESC LIMIT 1",
                              (step.objective_id, step.key, step.plan_version, PREFIXO_DADO_AUSENTE + "%"))
        if ausente:
            # 31.38: o plano foi revisado porque a leitura não achou o valor; o ator do plano novo sabe onde já se procurou.
            history.append(f"(plano revisado) na versão anterior: {str(ausente['status_detail'])[:300]} — procure em "
                           "outra tela ou outro caminho; se também não estiver lá, chame step_blocked(kind=\"dado_ausente\").")
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
        # Item 24.3 (ADR-058): os valores que ESTA etapa entrega às seguintes (`steps.saidas`). Ficam só na memória
        # (`lidos`) até a etapa ser comprovada: uma tentativa que falha não deixa valor para ninguém usar.
        # Item 12.4: sem nomes escolhidos pelo planejador, a etapa entrega o que a AÇÃO declara (`Capability.saidas`).
        saidas_declaradas = saidas_exigidas(repo.saidas_da_etapa(step.id), cap)
        lidos: dict[str, tuple[str, str]] = {}
        # Item 12.5 (ADR-070): as saídas lidas da IMAGEM (nome → (leitor, sha256 do recorte, id da evidência)); as
        # tentativas visuais já feitas nesta tentativa da etapa (barreira `repetida`); e a conta PRÓPRIA das recusas da
        # barreira de saídas, que `observe_screen` e `find_element` não zeram (`errors_in_row` zera): com 4, a etapa vai
        # para `fail_or_retry`. Sem ela o ator alternava recusa e observação sem nunca esbarrar no limite (r-…-178742).
        visuais: dict[str, tuple[str, str, int | None]] = {}
        tentativas_visuais: set[ChaveDeTentativa] = set()
        # 29.49: a recusa determinística de cada par (tela, âncora) já lido; reler o par vira `repetida` definitiva.
        recusas_visuais: dict[ChaveDeTentativa, str] = {}
        recusas_de_saida = 0

        def faltam_saidas() -> list[str]:
            return [n for n in saidas_declaradas if n not in lidos]

        # A referência `{{saida:…}}` é resolvida no despacho, antes da porta de política (`Scheduler._work`). Se uma
        # chegou aqui sem valor, quem chamou pulou essa passagem: a etapa não roda com o molde no lugar do valor.
        sem_valor = sorted({n for t in (step.title, step.goal, step.precondition, step.postcondition.value,
                                        step.postcondition.description, *step.commit_guard, *step.band_guard,
                                        *step.bindings.values(), *step.variables.values())
                            for n in nomes_citados(t)})
        if sem_valor:
            return StepOutcome(Outcome.failed, "A etapa usa o valor " + ", ".join(f"'{n}'" for n in sem_valor)
                               + " sem ele ter sido lido por uma etapa anterior; nada foi inventado.", plan_defect=True)
        # Item 24.4: idem para a conta esperada. O despacho resolve `{account_label}` ou segura o item
        # (`Scheduler._conta_da_etapa`); o molde que chegar aqui não vira "qualquer conta" na conferência da tela.
        if any("{account_label}" in (t or "") for t in (step.title, step.goal, step.precondition,
                                                          step.postcondition.value, step.postcondition.description,
                                                          *step.commit_guard, *step.band_guard,
                                                          *step.bindings.values())):
            return StepOutcome(Outcome.waiting_user, "A etapa confere a conta, e não há UMA conta da pessoa conhecida "
                                                     "no app dela; nada foi conferido contra uma conta vazia.",
                               needs="Cadastre ou reative a conta da pessoa neste aplicativo e retome o item.")
        # 31.44: pós-condição com valor VAZIO (`text=` de um molde `text={var}` cuja variável chegou sem valor) não tem
        # como ser comprovada: o seletor degrada para a busca do texto literal "text=" e a etapa gastaria as tentativas
        # e a IA (fec1a1: 3 chamadas) para chegar a "0 elemento(s)". Falha fechada ANTES de qualquer observação ou ação.
        if (vazia := parte_vazia_da_pos_condicao(step.postcondition)) is not None:
            if not account_label:
                # O aparelho está sem conta conhecida (rótulo vazio): é o caso do 24.4, com a mesma saída (a tentativa é
                # devolvida e a pessoa cadastra a conta); o tipo é de conta, nunca vira lição para o planejador.
                return StepOutcome(Outcome.waiting_user, f"A pós-condição confere {vazia} sem valor, e não há UMA conta "
                                                         "da pessoa conhecida no app dela; nada foi conferido contra "
                                                         "um valor vazio.",
                                   needs="Cadastre ou reative a conta da pessoa neste aplicativo e retome o item.")
            return StepOutcome(Outcome.failed, f"Defeito do plano — a pós-condição confere {vazia} sem valor (variável "
                                               "do plano que chegou vazia); nada foi conferido contra um valor vazio e "
                                               "repetir não resolve.", plan_defect=True)
        if saidas_declaradas:
            history.append("(executor) esta etapa entrega às seguintes o(s) valor(es) "
                           + ", ".join(f"'{n}'" for n in saidas_declaradas)
                           + ": leia cada um na tela com read_value(name, element_id) antes de concluir — o executor "
                             "tira o valor do texto do elemento. Código de verificação, senha e token nunca.")
            if self.cfg.file.ai.leitura_visual.enabled:
                history.append("(executor) se a linha do valor NÃO expõe texto na árvore (elemento sem texto nem descrição), "
                               "leia-o na imagem e chame read_value(name, element_id da linha, value=o que você leu, "
                               "source='visual'): outro leitor, que não vê o seu valor, transcreve a linha e o executor só "
                               "aceita se os dois concordarem. Só vale onde o app declara; uma recusa vem só com um código.")

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

        tempos = self._tempos(attempt_id)
        # C-1 (31.24): a linha de `ai_calls` do decide desta volta; `None` quando quem decide é a receita ou o executor.
        chamada_do_ator: int | None = None

        def intencao(tool: str, args: dict[str, object], rationale: str | None, *, side_effect: bool,
                     source: str = "ai") -> int:
            return repo.log_intent(attempt_id, tool, args, rationale, side_effect=side_effect, source=source,
                                   ai_call_id=chamada_do_ator)

        async def evidence(obs: Observation | None, note: str, kind: str = "screenshot") -> None:
            # C-4 (31.24): o tempo das capturas e gravações de evidência da tentativa (`attempts.evidencia_ms`).
            t_evidencia = time.monotonic()
            try:
                await registrar_evidencia(obs, note, kind)
            finally:
                if tempos is not None:
                    tempos.evidencia_ms += ms_desde(t_evidencia)

        async def registrar_evidencia(obs: Observation | None, note: str, kind: str) -> None:
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

        async def falhar_sem_nova_tentativa(detail: str, obs: Observation | None = None) -> StepOutcome:
            """Como `fail_or_retry`, mas sem nova tentativa nem recuperação: a falha não muda tentando de novo, nem refazendo
            a navegação até a mesma tela (29.49)."""
            await evidence(obs, f"Falha: {detail}")
            if step.side_effect and fired:
                return StepOutcome(Outcome.uncertain, detail)
            return StepOutcome(Outcome.failed, detail, sem_recuperacao=True)

        async def dado_ausente(motivo: str, obs: Observation | None) -> StepOutcome:
            """31.38: o que se procurou e onde, para a pessoa, sem nada lido da página (o texto do modelo fica na nota
            da evidência, já triado). Sem nova tentativa da mesma etapa: quem decide o replano é o scheduler."""
            onde = f"na etapa '{step.title}'" + (f" do {app.name or app.package}" if app else "")
            texto = (f"{PREFIXO_DADO_AUSENTE} procurei " + ", ".join(f"'{n}'" for n in saidas_declaradas)
                     + f" {onde} e não encontrei ({motivo}).")
            await evidence(obs, f"Falha: {texto}")
            metricas.contar("etapa.dado_ausente", motivo="teto" if motivo.startswith("teto") else "ator")
            if step.side_effect and fired:
                return StepOutcome(Outcome.uncertain, texto)
            return StepOutcome(Outcome.failed, texto, dado_ausente=True)

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
            # A tela da falha (item 22.3) é a da trava, pelo TIPO do motor ('desafio', 'dois_fatores'; o relato do ator
            # vale 'desafio'), o vocabulário que a exclusão das lições lê. Não dá para deixá-la à última árvore: a
            # trava achada dentro de uma ferramenta (`quick_tree`) não passa por `rt.last_tree`, que ainda mostra a
            # tela de ANTES do gesto.
            if step.side_effect and fired:
                return StepOutcome(Outcome.uncertain, motivo, trava_da_conta=True, tela_da_falha=trava.tipo)
            return StepOutcome(Outcome.waiting_user, motivo, trava_da_conta=True, tela_da_falha=trava.tipo,
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
        opcional = step.opcional and self.cfg.file.ai.limpeza_opcional
        if opcional:                       # item 31.36: limpar a tela vale no máximo 3 decisões do ator
            max_actions = min(max_actions, LIMITE_DA_ETAPA_OPCIONAL)
        # Item 31.38: a etapa de LEITURA (declara saídas e não deixa marca) tem teto próprio de decisões; a de efeito não.
        leitura = bool(saidas_declaradas) and not step.side_effect and not step.commit_guard
        teto_leitura = int(self.cfg.file.ai.max_decisoes_leitura) if leitura else 0
        ai_cfg = self._ai_da_execucao(str(run["id"]))      # 17.14: o perfil da execução pode trocar imagem e árvore
        judged_step = step.postcondition.kind == "model_judged" or need is not None
        decisions = 0
        image_requested = False
        # Lições medidas do ator (ADR-054): pedidas na PRIMEIRA consulta ao ator desta tentativa e reusadas nas
        # seguintes. Preguiçoso de propósito — a tentativa que a receita leva até o fim nunca pede.
        licoes: list[str] | None = None
        # Modelo forte (escalonamento) onde errar custa caro ou o barato já tropeçou: etapa com efeito externo
        # (conforme o risco, ver `side_effect_tier`), erros seguidos, ação repetida na mesma tela ou — na nova
        # tentativa — a ação em que a anterior parou, e o efeito.
        tier_efeito, motivo_efeito = side_effect_tier(step, cap, ai_cfg.strong_model_for_side_effect,
                                                      app.builtin and app.category == CATEGORIA_APP_DE_PROVA)
        base_tier = 1 if tier_efeito else 0
        # LT-12: a nova tentativa inteira subia ao modelo forte (76 decides de tentativa 2 no tier 1 em 7 d, +1,9 s cada),
        # mas ela recomeça quase sempre pelo prefixo que a anterior já acertou. Agora começa no tier 0 e sobe — até o fim
        # da tentativa — na 1ª decisão que repetir, na mesma tela estrutural, a última ação da anterior (onde ela
        # parou) ou que dispararia o efeito. Essa decisão é descartada e refeita no modelo forte.
        retentativa = step.attempts > 1 and not tier_efeito
        acao_onde_parou = self._ultima_acao_da_etapa.get(step.id) if retentativa else None
        retentativa_subiu = False
        escalated = False                     # a linha do escalonamento sai UMA vez por etapa, não por decisão
        # Item 7.8 (piso de conteúdo): o provedor de `decide` É o do `.env`/YAML, não o desta instância de etapa —
        # ele não muda no meio de uma execução, então resolver uma vez aqui é o mesmo resultado de resolver a cada
        # volta do laço, sem pagar a travessia de config de novo. Só interessa quando NÃO é Anthropic: é o
        # endpoint local (pouco contexto, resposta pode "esquecer" a árvore atual) quem inventa um `element_id`
        # de uma tela que já passou — a Anthropic recebe a árvore inteira e não tropeça nisto.
        decide_kind = self.cfg.ai_role("decide").kind
        forcar_tier_1 = False                  # a decisão anterior foi descartada pelo piso: a PRÓXIMA sobe de tier
        bloqueio_escalado = False              # item 17.10: o bloqueio do tier 0 sobe ao tier 1 UMA vez por tentativa
        cascata_pendente = False               # RA-10: a PRÓXIMA decisão é a da cascata (o `bloqueio_escalado` fica)
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
        abriu_sem_ia = False                   # LT-6: o `open_app` determinístico já foi gasto nesta tentativa

        async def desfecho_de_ia(exc: AIError, obs: Observation, durante: str) -> StepOutcome:
            """O que a etapa faz quando uma chamada de IA (a decisão do ator, a leitura visual) falha por motivo que NÃO
            é da chamada em si: chave, crédito, prazo, orçamento, recusa por política. Um só lugar, para o leitor da
            leitura visual não ter tratamento próprio e mais frouxo que o do ator. O `kind` vai no desfecho (RA-22): é
            ele, e não o texto abaixo, que classifica a falha."""
            return dataclasses.replace(await desfecho_pelo_tipo(exc, obs, durante), ai_error_kind=exc.kind)

        async def desfecho_pelo_tipo(exc: AIError, obs: Observation, durante: str) -> StepOutcome:
            if exc.kind == "not_configured":
                return StepOutcome(Outcome.waiting_user, str(exc), needs="Configure a chave do provedor no .env, "
                                   "reinicie o backend e retome este item.", ai_blocked=True)
            if exc.kind in ("billing", "balance"):
                return StepOutcome(Outcome.waiting_user, str(exc),
                                   needs="Recarregue o crédito do provedor de IA e retome a execução.",
                                   ai_blocked=True)
            if exc.kind == "step_deadline":
                return await fail_or_retry(com_anr(f"Prazo da etapa ({step.timeout_s}s) esgotado durante {durante}: "
                                                   f"{exc}"), obs)
            if exc.kind == "budget":
                if isinstance(exc, OrcamentoDaEtapa) and leitura and not fired:
                    # 31.40 (b): o orçamento da ação cortou a LEITURA: o desfecho do 31.38, sem repetir a etapa.
                    return await dado_ausente("orçamento de chamadas da etapa esgotado", obs)
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

        def com_anr(detalhe: str) -> str:
            """O prazo que vence DEPOIS de um ANR contado nesta etapa diz o ANR: "tempo esgotado" sozinho, como na
            r-20260928195344-02ee9e, mandava procurar a lentidão em outro lugar. E o aviso vai para o aparelho."""
            if not (mortes_por_anr and app.package):
                return detalhe
            motivo = motivo_de_anr(app.name or app.package, iid)
            self._avisar_anr(rt, motivo)
            return f"{detalhe.rstrip('.')}; {motivo}."

        # Atalhos do caminho rápido 1 (LT-1, LT-2): "pular o ator, nunca a prova". Quando o juiz (`_verify`, o mesmo de
        # depois do laço) já aprova antes do ator, o veredito fica aqui e o fim do laço o REUSA: a etapa paga uma
        # verificação, não duas. Quando não aprova, o laço segue na mesma tentativa, com o veredito no `history`.
        veredito_antecipado: tuple[bool, str, DeliveryLevel | None, Observation | None, bool] | None = None
        copias_vistas: list[int] = []        # 29.58 (C): `Verdict.copias` de cada julgamento desta tentativa
        sobreposicoes: list[bool] = []       # 31.40: `Verdict.sobreposicao` de cada "não"/"incerto" desta tentativa
        coberturas: list[Cobertura] = []     # 31.40 b: o elemento que cobre, a cada sobreposição achada
        julgamentos_antes_do_ator = 0
        sig_julgada_antes_do_ator: str | None = None

        async def julgar_antes_do_ator(onde: str, *, so_prova_local: bool = False) -> StepOutcome | bool:
            """`True`: o veredito foi guardado para o fim do laço (comprovada, ou incomprovável pela tela: o desfecho
            do fim do laço é o mesmo). `False`: não comprovou (nem "não" nem "incerto" valem como prova) — o ator segue, na
            MESMA tentativa; nunca `retry` nem `failed` por causa disso, porque uma tentativa nova custa mais que o decide
            poupado, e o veredito definitivo continua sendo o do fim do laço. `StepOutcome`: o desfecho que a falha de IA
            ou de driver já tinha depois do laço."""
            nonlocal veredito_antecipado, last_obs
            try:
                v = await self._verify(rt, step, ctx_for, run_id, oid, deadline, call_timeout,
                                       patient=bool(need) or fired, facts=history[-12:],
                                       failure_marks=(tuple(cap.failure_marks) if cap and fired else ()),
                                       local_proof=(cap.local_proof if cap else None),
                                       capability=(CapabilityRef(app.package, cap.key) if cap and app.package else None),
                                       attempt_id=attempt_id, cartao=cartao, pacote=app.package,
                                       imagem_forcada=bool(visuais), uma_rodada=True, so_prova_local=so_prova_local,
                                       copias_vistas=copias_vistas)
            except DriverTimeout as exc:
                return await self._stuck(rt, step, fired, str(exc))
            except AIError as exc:
                return await desfecho_de_ia(exc, last_obs, "a verificação antes do ator")
            except DriverError as exc:
                log.info("%s: a conferência %s falhou (%s); segue pelo ator", iid, onde, exc)
                return False
            ok_v, texto_v, _nivel, obs_v, sem_prova = v
            if obs_v is not None:
                last_obs = obs_v
            if ok_v or sem_prova:
                # "Incomprovável pela tela" é do TEXTO da pós-condição (descreve processo/histórico), não da tela da
                # vez: o desfecho é o mesmo que o fim do laço daria (defeito do plano), e voltar ao ator para só então
                # perguntar de novo ao juiz pagaria outro julgamento para ouvir a mesma coisa. `ok` e `unprovable`
                # seguem no veredito guardado.
                veredito_antecipado = v
                return True
            history.append(f"(executor) {onde}: o verificador conferiu a tela e a pós-condição NÃO está comprovada: "
                           f"{texto_v[:300]}. Continue a partir da tela atual.")
            repo.decision(f"{iid} · {step.title}: o verificador não comprovou a pós-condição {onde}; o ator segue "
                          "nesta mesma tentativa", run_id=run_id, instance_id=iid, step_id=step.id)
            return False

        settle_pendente: int | None = None     # C-2 (31.24): o assentamento depois da ação, para a decisão seguinte
        # Item 31.35 (parte B): as ações seguintes da última decisão (`Decision.extras`), a árvore em que o ator as
        # escolheu (para achar o MESMO alvo na tela nova) e a linha de `ai_calls` que as pagou.
        fila_encadeada: list[Decision] = []
        arvore_da_fila: UiTree | None = None
        chamada_da_fila: int | None = None
        for _ in range(max_actions + 1):
            chamada_do_ator = None
            settle_da_volta, settle_pendente = settle_pendente, None
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
                        requested=image_requested, ai=ai_cfg, le_valor=bool(saidas_declaradas))
            t_observacao = time.monotonic()
            try:
                obs = last_obs = await reler_se_ocupada(
                    lambda: self.devices.observe(rt, timeout=call_timeout, lado_max=ai_cfg.screenshot_max_side,
                                                 imagem=lambda t: not receita_decide and self._want_image(t, **pede)),
                    prazo=deadline, quem=iid)
                observacao_ms = ms_desde(t_observacao)
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
                        if judged_step or self._postcondition_holds(step, obs, cartao, pacote=app.package):
                            rr.completed_by_recipe = True
                            aid = intencao("step_done", {"rationale": "[receita] ações reproduzidas"},
                                           f"[receita v{rep.version}] ações reproduzidas; conferindo a pós-condição",
                                           side_effect=False, source="recipe")
                            repo.finish_action(aid, ActionStatus.done, result={"declared": True})
                            break
                        rr.settle += 1
                        if rr.settle <= 3:                     # a interface pode estar assentando
                            t_settle = time.monotonic()
                            await asyncio.sleep(float(self.cfg.file.ai.recipe_settle_s))
                            settle_pendente = ms_desde(t_settle)
                            continue
                        raise RecipeDiverged("ações reproduzidas, mas a pós-condição não apareceu")
                    from_recipe = True
                except RecipeDiverged as exc:
                    if (rep.done_actions == 0 and not judged_step
                            and self._postcondition_holds(step, obs, cartao, pacote=app.package)):
                        rr.completed_by_recipe = True          # o aparelho já estava no estado final desta etapa
                        break
                    rr.diverged = str(exc)
                    decision = None
                    history.append(f"(executor) a receita desta etapa divergiu: {exc}. Continue a partir da tela atual.")
                    repo.decision(f"{iid} · {step.title}: receita divergiu — {exc}; a IA assume esta etapa",
                                  run_id=run_id, instance_id=iid, step_id=step.id)
            scale = self._image_scale(obs, ai_cfg)
            if decision is None:
                # ---------- LT-1: a pós-condição já vale na tela que acabou de ser lida? Pular o ator, nunca a prova.
                # Só etapa SEM efeito (a UI otimista de uma etapa com efeito mostra o "feito" antes de ele valer),
                # tela não sensível e nenhuma saída por ler. Prova local verdadeira: sai do laço para o `_verify` de
                # sempre (custo zero, a árvore já foi lida). Etapa julgada sem nível de entrega: o juiz barato confere
                # a tela agora, e só um "não" chama o ator. Receita que ainda reproduz decide antes daqui.
                if (ATALHO_ANTES_DO_ATOR and not step.side_effect and not fired and not obs.sensitive
                        and not faltam_saidas()):
                    pelo_atalho: str | None = None
                    if not judged_step:
                        if self._postcondition_holds(step, obs, cartao, pacote=app.package):
                            pelo_atalho = "a pós-condição já vale na tela lida"
                    elif (need is None and decisions == 0 and julgamentos_antes_do_ator < JULGAMENTOS_ANTES_DO_ATOR
                          and (ENTRADA_JULGADA_SO_COM_PROVA_LOCAL is False or (cap is not None and cap.local_proof))):
                        # Sem prova local declarada (e a constante no padrão) a conferência de entrada não teria como
                        # aprovar: nem se chama, para não pagar uma releitura da árvore nem uma linha enganosa no `history`.
                        sig_atual = obs.tree.signature()
                        if sig_atual != sig_julgada_antes_do_ator:      # a mesma tela já julgada "não" não paga de novo
                            sig_julgada_antes_do_ator = sig_atual
                            julgamentos_antes_do_ator += 1
                            r = await julgar_antes_do_ator("antes de chamar o ator",
                                                           so_prova_local=(ENTRADA_JULGADA_SO_COM_PROVA_LOCAL
                                                                           or bool(cap and cap.local_proof)))
                            if isinstance(r, StepOutcome):
                                return r
                            if r:
                                pelo_atalho = "o verificador já tem o veredito da tela lida"
                    if pelo_atalho is not None:
                        rr.sem_ator = decisions == 0 and not (rr.replayer is not None and rr.replayer.done_actions)
                        repo.decision(f"{iid} · {step.title}: {pelo_atalho}; segue para a comprovação sem chamar o ator",
                                      run_id=run_id, instance_id=iid, step_id=step.id)
                        break
                # ---------- LT-6: "abrir o app" é código, não decisão. A etapa cuja pós-condição é `app_foreground` abre
                # o app pelo mesmo caminho da reabertura pós-ANR (`open_app` + foco lido), UMA vez por tentativa, antes
                # do ator — em 7 d, 48 dessas etapas pagaram um decide (p50 9,0 s) para pedir exatamente isso. Só quando
                # a receita não conduz (ela também não chama a IA, e o funil dela fica intacto) e nunca em etapa com
                # efeito. Interstitial ou foco que não chega: a volta seguinte não comprova e o ator assume, nesta tentativa.
                alvo_do_foco = step.postcondition.value if step.postcondition.kind == "app_foreground" else ""
                if (OPEN_APP_SEM_IA and alvo_do_foco and not abriu_sem_ia and rep is None and decisions == 0
                        and not step.side_effect and not fired and alvo_do_foco in self._allowed_packages()
                        and not self._postcondition_holds(step, obs, cartao, pacote=app.package)):
                    abriu_sem_ia = True
                    rr.exerceu(StrategyKind.deterministic)
                    t_abrir = time.monotonic()
                    try:
                        await call(rt.io.open_app, alvo_do_foco, app.activity if alvo_do_foco == app.package else None)
                        na_frente = await esperar_foco(lambda: call(rt.io.current_focus), alvo_do_foco, ate=deadline)
                    except DriverTimeout as exc:
                        return await self._stuck(rt, step, fired, str(exc))
                    except DriverError as exc:
                        log.info("%s: abrir %s sem IA falhou (%s); o ator assume", iid, alvo_do_foco, exc)
                        na_frente = None
                    gasto = time.monotonic() - t_abrir
                    situacao = ("em primeiro plano" if na_frente else "ainda não está em primeiro plano"
                                if na_frente is False else "o pedido de abertura falhou")
                    repo.decision(f"{iid} · {step.title}: app {alvo_do_foco} aberto pelo executor, sem IA — {situacao} "
                                  f"({gasto:.1f} s)", run_id=run_id, instance_id=iid, step_id=step.id)
                    history.append(f"(executor) abriu o app {alvo_do_foco} sem IA: {situacao}. Se a tela não for a "
                                   "dele, continue a partir dela.")
                    agiu = True
                    continue
                trouble = errors_in_row >= 1 or same_count >= 1
                piso_forcou = forcar_tier_1    # captura ANTES de zerar: o motivo do escalonamento lê daqui embaixo
                forcar_tier_1 = False          # consumido: só a decisão SEGUINTE ao descarte sobe de tier, não todas
                cascata, cascata_pendente = cascata_pendente, False     # RA-10: idem, a decisão da cascata (17.10)
                if rr.mode == "replay" and rr.diverged and not rr.retorno_contado:
                    # Funil de receitas (contrato C5): a IA assume a etapa depois da divergência — contado UMA vez
                    # por tentativa, no instante da primeira consulta. A divergência sozinha NÃO sobe de tier
                    # (decisão da evolução de desempenho, 26/09): a IA decide no modelo de ação e só escala pelos
                    # controles abaixo. Escalar aqui é decisão do dono, com o custo medido no relatório.
                    rr.retorno_contado = True
                    contar_retorno_ia(rr.diverged)
                tier = 1 if (base_tier or retentativa_subiu or errors_in_row >= 2 or same_count >= 1 or piso_forcou) else 0
                if opcional:                   # item 31.36: a limpeza opcional nunca sobe para o modelo caro
                    tier = 0
                # RA-10: o porquê do modelo forte NESTA decisão, em vocabulário fechado (`ai_calls.escalate`); a frase
                # da linha do tempo sai dele, uma vez por etapa.
                escalonamento: MotivoDeEscalonamento | None = (
                    None if not tier else "efeito" if tier_efeito else "nova_tentativa" if retentativa_subiu
                    else "erros_seguidos" if errors_in_row >= 2
                    else ("bloqueio" if cascata else "piso") if piso_forcou else "ciclo")
                if tier and not escalated:
                    # O escalonamento é configuração explícita do dono (AI_MODEL_ESCALATION,
                    # strong_model_for_side_effect) e já aparecia no cartão de custo — o que faltava era a linha
                    # na execução dizendo POR QUE esta etapa passou a decidir no modelo caro (achado #92, item 5).
                    escalated = True
                    motivo = (motivo_efeito if escalonamento == "efeito"
                              else _FRASE_DO_ESCALONAMENTO.get(escalonamento or "", ""))
                    repo.decision(f"{iid} · {step.title}: decisão escalonada para o modelo de escalonamento "
                                  f"({motivo})", run_id=run_id, instance_id=iid, step_id=step.id)
                t_prompt = time.monotonic()                     # C-2 (31.24): daqui até `_ai` é a montagem do pedido
                arvore_ms, imagem_ms, completar_ms = obs.ms_arvore, obs.ms_imagem, 0
                motivo_imagem = self._motivo_da_imagem(obs.tree, judged_step=judged_step, first=decisions == 0,
                                                       trouble=trouble, requested=image_requested, ai=ai_cfg,
                                                       le_valor=bool(saidas_declaradas))
                quer_imagem = motivo_imagem in _IMAGEM_VAI
                if quer_imagem and obs.jpeg is None and obs.image_omitted == "policy":
                    # A receita divergiu depois da observação só de árvore: a imagem vem agora, da mesma árvore,
                    # pelo mesmo executor e sem ação no meio.
                    t_completar = time.monotonic()
                    try:
                        obs = last_obs = await self.devices.completar_imagem(rt, obs, timeout=call_timeout,
                                                                             lado_max=ai_cfg.screenshot_max_side)
                    except DriverTimeout as exc:
                        return await self._stuck(rt, step, fired, str(exc))
                    except DriverError as exc:
                        log.info("%s: imagem para a decisão indisponível (%s); decide pela árvore", iid, exc)
                    completar_ms = ms_desde(t_completar)
                encadeada = (_proxima_encadeada(fila_encadeada, arvore_da_fila, obs.tree, history)
                             if fila_encadeada and errors_in_row == 0 else None)
                fila_encadeada = fila_encadeada if encadeada is not None else []
                screen, scale = self._screen(obs, with_image=quer_imagem,
                                             protect=tuple(step.commit_guard), boost=_boost_terms(step, app),
                                             ai=ai_cfg)
                image_requested = False
                if encadeada is None:          # a ação encadeada não é decisão nova (31.35)
                    if teto_leitura and decisions >= teto_leitura:
                        return await dado_ausente(f"teto de {teto_leitura} decisões da leitura", obs)
                    decisions += 1
                actor_history = compress_history(history, ai_cfg.actor_history_lines)
                rr.exerceu(StrategyKind.ai_actor)
                if licoes is None:
                    licoes = self._licoes_da_tentativa(run, objective, step, attempt_id, app, rr)
                pedidas = licoes
                marca = MarcaDaChamada(motivo="cascata" if cascata else "decisao", escalate=escalonamento,
                                       image_reason=motivo_imagem)
                preparo = PreparoDaDecisao(
                    settle_ms=settle_da_volta, observacao_ms=observacao_ms + completar_ms,
                    arvore_ms=round(arvore_ms) if arvore_ms is not None else None,
                    imagem_ms=(round(imagem_ms or 0) + completar_ms) if (imagem_ms is not None or completar_ms)
                    else None,
                    prompt_ms=max(0, ms_desde(t_prompt) - completar_ms),
                    arvore_chars=sum(len(linha) for linha in screen.elements),
                    historico_chars=sum(len(linha) for linha in actor_history), podados=screen.podados)
                try:
                    if encadeada is not None:
                        decision, preparo.ai_call_id = encadeada, chamada_da_fila
                    else:
                        decision = await self._ai(run_id, oid, lambda: self.provider.decide(
                            DecisionRequest(ctx=ctx_for(), screen=screen, history=actor_history, tier=tier,
                                            lessons=list(pedidas),
                                            encadear=1 if step.side_effect else ai_cfg.acoes_por_decisao)),
                            step_id=step.id, role="decide", deadline=deadline, attempt_id=attempt_id, marca=marca,
                            preparo=preparo)
                except AIError as exc:
                    return await desfecho_de_ia(exc, obs, "a decisão da IA")
                chamada_do_ator = preparo.ai_call_id
                if encadeada is None and not step.side_effect:
                    fila_encadeada, arvore_da_fila, chamada_da_fila = (
                        _fila_encadeada(decision.extras), obs.tree, chamada_do_ator)
                if rr.mode == "shadow" and rr.replayer is not None and not rr.diverged:
                    self._shadow_compare(rr, obs, decision)     # aprende-se a confiar na receita antes de deixá-la agir
            # ---------- validar
            try:
                args = validate_call(decision.tool, decision.args)
            except ToolValidationError as exc:
                aid = intencao(decision.tool, _safe_args(decision.args), None, side_effect=False,
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
            if isinstance(args, ReadValue):
                # ---------- valor para as etapas seguintes (item 24.3): lido da árvore pelo executor, triado (D3). Só na
                # tela cega que o app declara (item 12.5, ADR-070) o valor pode vir da IMAGEM, conferido às cegas.
                bruto = args.model_dump(mode="json")
                erro: str | None = None
                valor, partes, alvo = "", [], None
                visual = False                     # a árvore não tem texto e o ator pediu a leitura visual
                recusa_visual: LeituraVisualRecusada | None = None
                lido_da_imagem = None
                motivo_visual: str | None = None   # a triagem recusou o que o leitor viu: segue o caminho da árvore
                if not saidas_declaradas:
                    erro = "esta etapa não entrega valor às seguintes; read_value não se aplica aqui"
                elif args.name not in saidas_declaradas:
                    # O nome escrito não é citado: o modelo escreve o que quiser ali (um código, até), e esta mensagem
                    # vai para `actions.error`. Os nomes certos são do plano.
                    erro = ("o nome informado não é valor desta etapa; os dela são: "
                            + ", ".join(f"'{n}'" for n in saidas_declaradas))
                elif (frente := self._tela_fora_do_app(step, obs, app.package)) is not None:
                    # Um valor lido na tela de OUTRO app seria entregue às seguintes como se fosse deste.
                    erro = (f"a tela é do app {frente}, não do app desta etapa ({app.package}): abra-o com open_app "
                            "antes de ler")
                else:
                    try:
                        valor, partes, alvo = ler_valor(obs.tree, element_id=args.element_id, trecho=args.value,
                                                        tipo=args.value_kind)
                    except LeituraSemTexto as exc:
                        # A ÚNICA falha da árvore que abre o caminho visual (barreira 1): o elemento existe e não tem
                        # texto nem descrição. Qualquer outra falha (id, trecho, tipo) é recusa comum.
                        if args.source == "visual":
                            visual = True
                        else:
                            erro = str(exc)
                    except LeituraInvalida as exc:
                        erro = str(exc)
                if visual:
                    async def obter_imagem() -> tuple[UiTree, bytes | None, int, int] | None:
                        if obs.jpeg is not None:
                            return obs.tree, obs.jpeg, obs.width, obs.height
                        if obs.sensitive:
                            return None
                        # A observação saiu só com a árvore: captura uma nova, e `ler_valor_visual` exige a MESMA
                        # assinatura de árvore e os mesmos limites da âncora (barreira 6).
                        try:
                            nova = await self.devices.observe(rt, timeout=call_timeout, imagem=True,
                                                              lado_max=ai_cfg.screenshot_max_side)
                        except (DriverError, DriverTimeout):
                            return None
                        return nova.tree, nova.jpeg, nova.width, nova.height

                    async def transcrever(recorte: bytes, pedidas: dict[str, str]) -> Transcricao:
                        try:
                            return await self._ai(
                                run_id, oid, lambda: self.provider.transcribe(
                                    LeituraRequest(recorte=recorte, saidas=pedidas, run_id=run_id)),
                                step_id=step.id, role="leitura", deadline=deadline, attempt_id=attempt_id,
                                marca=MarcaDaChamada(motivo="leitura"))
                        except AIError as exc:
                            if exc.kind in ("budget", "step_deadline", "billing", "balance", "refusal"):
                                raise                   # o mesmo desfecho do ator (`desfecho_de_ia`), não uma recusa
                            # Nunca o texto do erro ao ator: só a falha do provedor e a saída inválida do leitor viram
                            # recusa como as outras. `from None`: a cadeia não leva o texto do leitor ao log.
                            raise LeituraVisualRecusada(
                                "sem_leitor" if exc.kind == "not_configured" else "leitor_falhou") from None

                    conhecimento = telas_do_app.da_pasta(CONHECIMENTO_DE_APPS / (app.package or ""))
                    reconhecida = (telas_do_app.classificar(conhecimento, obs.tree, package=obs.package)
                                   if conhecimento is not None else None)
                    tela_conhecida = reconhecida.tela if reconhecida is not None else None
                    try:
                        lido_da_imagem = await ler_valor_visual(
                            habilitado=ai_cfg.leitura_visual.enabled, arvore=obs.tree, element_id=args.element_id,
                            nome=args.name, valor_do_ator=args.value or "", conhecimento=conhecimento,
                            tela=tela_conhecida, image_policy=ai_cfg.image_policy,
                            # defesa em profundidade: o `elif` acima já recusa a tela de outro app antes de chegar aqui,
                            # mas a barreira vale por si (a observação pode mudar entre uma checagem e outra).
                            fora_do_app=self._tela_fora_do_app(step, obs, app.package),
                            largura=obs.width, altura=obs.height, obter_imagem=obter_imagem,
                            tentativas=tentativas_visuais, tipo_da_tela=reconhecida.tipo if reconhecida else None,
                            transcrever=transcrever if self._tem_leitor() else None, recusas=recusas_visuais)
                    except AIError as exc:
                        return await desfecho_de_ia(exc, obs, "a leitura visual")
                    except LeituraVisualRecusada as rec:
                        if rec.codigo == "triagem":
                            # O leitor viu código de verificação, senha ou token (ADR-009): NÃO é erro de chamada. Segue o
                            # caminho da árvore (a etapa para em `waiting_user`), sem nova tentativa do ator e sem lhe
                            # dizer que a linha tem código — com eco, ele leria o código em outro recorte.
                            motivo_visual = rec.motivo or "código de verificação"
                        else:
                            # Barreira fechada: o ator recebe SÓ o código — nem a transcrição, nem o valor dele. O
                            # recorte recusado não é guardado, e nada é gravado.
                            erro = rec.rotulo
                            recusa_visual = rec
                    else:
                        valor, partes, alvo = lido_da_imagem.valor, [lido_da_imagem.valor], lido_da_imagem.alvo
                if erro is not None:
                    # Erro de chamada: o ator tenta de novo. Nada aqui passou pela triagem — nem o texto do elemento, nem
                    # o que o modelo escreveu —, então nada disso vai para o registro da ação nem para o evento
                    # `action.logged`: o erro não cita valor (`saidas.ler_valor`), os argumentos saem sem o recorte e
                    # sem nome ou id que não tenham forma de nome ou de id (`args_da_chamada_invalida`), e a
                    # justificativa, que pode citar o valor, fica de fora como no caminho recusado pela triagem.
                    aid = intencao("read_value", args_da_chamada_invalida(bruto, obs.tree), None,
                                   side_effect=False)
                    repo.finish_action(aid, ActionStatus.rejected, error=erro)
                    history.append(f"read_value REJEITADA: {erro}")
                    if visual:
                        repo.decision(f"{iid} · {step.title}: leitura visual de '{args.name}' recusada ({erro})",
                                      run_id=run_id, instance_id=iid, step_id=step.id)
                    if recusa_visual is not None and recusa_visual.definitiva:
                        # 29.49 (run 89b814): o ator releu a âncora que o leitor já recusou, na mesma tela. Repetir de
                        # novo, ou numa nova tentativa, daria o mesmo não e só gastaria chamadas (ali: 13, duas no modelo
                        # de escalonamento, até o teto). A etapa termina como não lida, sem nova tentativa.
                        return await falhar_sem_nova_tentativa(
                            f"O valor da etapa não foi lido na tela: a leitura visual de '{args.name}' foi recusada "
                            f"({recusa_visual.anterior}) e repetida na mesma tela", obs)
                    if recusa_visual is not None and recusa_visual.codigo in RECUSAS_DETERMINISTICAS:
                        history.append(
                            f"(executor) a leitura visual de '{args.name}' foi recusada pelo leitor ({erro}). Ler de novo "
                            "o mesmo elemento nesta mesma tela não será aceito e encerra a etapa como não lida. Se o "
                            "objetivo permitir, leia o valor por outro caminho; senão, não repita a leitura.")
                    errors_in_row += 1
                    recusas_de_saida += 1
                    if errors_in_row >= 4 or recusas_de_saida >= 4:
                        return await fail_or_retry(f"O valor da etapa não foi lido na tela: {erro}", obs)
                    continue
                motivo = motivo_visual
                if motivo is None and lido_da_imagem is None:
                    assert alvo is not None
                    tela = texto_da_tela(obs.tree)
                    # A leitura visual já passou pela triagem (barreira 12) sobre a transcrição e o valor.
                    motivo = next((m for p in partes if (m := triagem(p, do_elemento=texto_do_elemento(alvo), da_tela=tela,
                                                                      campo_de_senha=alvo.password)) is not None), None)
                if motivo is not None:
                    # D3 (ADR-009, ADR-022, ADR-058): a etapa PARA. O valor não vai para a tabela de saídas, nem para os
                    # argumentos da ação, nem para evento ou evidência — que sai em texto, sem captura da tela que o
                    # mostra; a justificativa do modelo, que pode citá-lo, também fica de fora.
                    aid = intencao("read_value", args_sem_valor(bruto), None, side_effect=False)
                    repo.finish_action(aid, ActionStatus.rejected, error=f"valor recusado pela triagem: {motivo}")
                    texto = (f"O valor '{args.name}' lido na tela tem formato de {motivo}: código de verificação, "
                             "senha e token não passam de uma etapa a outra (ADR-009, ADR-058). Nada foi gravado.")
                    await evidence(None, f"Parada sem gravar o valor: {texto}")
                    repo.decision(f"{iid} · {step.title}: {texto}", run_id=run_id, instance_id=iid, step_id=step.id)
                    if step.side_effect and fired:
                        return StepOutcome(Outcome.uncertain, texto)
                    return StepOutcome(Outcome.waiting_user, texto,
                                       needs="Este valor é da pessoa (ADR-009): faça esta parte manualmente, ou refaça "
                                             "o comando sem depender dele, e retome o item.")
                assert alvo is not None
                if ai_cfg.relacao_do_valor:
                    # 31.41: o valor só vale se algo na tela o liga ao nome pedido; dúvida recusa a leitura (03d58e: um
                    # id de execução numa mensagem foi entregue como "protocolo"). Da árvore, a regra determinística; da
                    # imagem (sem texto na árvore), UMA pergunta de sim ou não ao verificador, e incerto conta como não.
                    relacao = (relacao_do_valor(obs.tree, alvo, args.name, valor,
                                                relacoes=tuple(cap.saidas_relacao) if cap else ())
                               if lido_da_imagem is None else None)
                    perguntou = relacao is None and (lido_da_imagem is not None or bool(cap and args.name in cap.saidas))
                    if perguntou:
                        # Da imagem, ou saída que o CATÁLOGO declara sem seletor nem rótulo na árvore (a caixa do Outlook,
                        # a lista do QA): a ação diz onde está o valor, mas só o juiz confirma que é ele. Livre: dúvida.
                        relacao = await self._relacao_visual(rt, step, ctx_for, run_id, oid, deadline, attempt_id,
                                                             obs, args.name, valor, ai_cfg)
                    if relacao is None:
                        aid = intencao("read_value", args_da_chamada_invalida(bruto, obs.tree), None, side_effect=False)
                        repo.finish_action(aid, ActionStatus.rejected, error=f"sem relação com '{args.name}'")
                        metricas.contar("leitura.sem_relacao", origem="imagem" if lido_da_imagem else "arvore")
                        repo.decision(f"{iid} · {step.title}: leitura de '{args.name}' recusada: nada na tela liga o "
                                      "valor ao que foi pedido (sem seletor, rótulo ou forma"
                                      + ("; o verificador não confirmou)" if perguntou else ")"),
                                      run_id=run_id, instance_id=iid, step_id=step.id)
                        history.append(f"read_value REJEITADA: nada na tela liga este valor a '{args.name}' (nem rótulo "
                                       "vizinho, nem forma, nem o seletor do catálogo). Leia o elemento rotulado como "
                                       f"'{args.name}'; se ele não existe nesta tela, chame "
                                       'step_blocked(kind="dado_ausente").')
                        errors_in_row += 1
                        recusas_de_saida += 1
                        if errors_in_row >= 4 or recusas_de_saida >= 4:
                            return await dado_ausente("o valor lido não tinha relação com o pedido", obs)
                        continue
                lidos[args.name] = (valor, args.value_kind)
                if lido_da_imagem is not None:
                    # O recorte vira evidência SÓ agora, com a leitura válida; a nota não traz o valor. A ação não leva o
                    # valor (`args.value` fica **OMITIDO**) nem a transcrição: só nome, tipo, tamanho, origem e ids.
                    leitor = self._leitor_visual()
                    sha8 = lido_da_imagem.sha256[:8]
                    evidencia_id = await repo.add_evidence_async(
                        run_id=run_id, instance_id=iid, step_id=step.id, attempt_id=attempt_id, kind="screenshot",
                        note=f"Recorte da linha de '{args.name}', lido da imagem; conferido às cegas por {leitor} "
                             f"(captura {sha8}).", data=lido_da_imagem.recorte)
                    visuais[args.name] = (leitor, lido_da_imagem.sha256, evidencia_id)
                    aid = intencao("read_value", args_da_chamada_invalida(bruto, obs.tree), None,
                                   side_effect=False)
                    repo.finish_action(aid, ActionStatus.done,
                                       result={"name": args.name, "value_kind": args.value_kind, "chars": len(valor),
                                               "origem": "visual", "frame_id": obs.frame_id,
                                               "evidence_id": evidencia_id, "leitor": leitor},
                                       target=_safe_target(alvo, obs.tree))
                    history.append(f"read_value({args.name}) → lido da imagem e conferido às cegas"
                                   + (f"; faltam: {', '.join(faltam_saidas())}" if faltam_saidas()
                                      else "; todos os valores da etapa lidos"))
                    repo.decision(f"{iid} · {step.title}: valor '{args.name}' lido da imagem ({args.value_kind}, "
                                  f"{len(valor)} caractere(s)), conferido às cegas por {leitor}",
                                  run_id=run_id, instance_id=iid, step_id=step.id)
                else:
                    visuais.pop(args.name, None)          # a releitura pela árvore substitui uma leitura visual antiga
                    aid = intencao("read_value", bruto, rationale, side_effect=False)
                    repo.finish_action(aid, ActionStatus.done,
                                       result={"name": args.name, "value_kind": args.value_kind, "chars": len(valor),
                                               "origem": "arvore"},
                                       target=_safe_target(alvo, obs.tree))
                    faltam = faltam_saidas()
                    history.append(f"read_value({args.name}) → lido: {como_texto(valor, args.value_kind)[:120]}"
                                   + (f"; faltam: {', '.join(faltam)}" if faltam else "; todos os valores da etapa lidos"))
                    repo.decision(f"{iid} · {step.title}: valor '{args.name}' lido da tela ({args.value_kind}, "
                                  f"{len(valor)} caractere(s))", run_id=run_id, instance_id=iid, step_id=step.id)
                faltam = faltam_saidas()
                errors_in_row = 0
                recusas_de_saida = 0
                if (not faltam and not judged_step and not obs.sensitive
                        and self._postcondition_holds(step, obs, cartao, pacote=app.package)):
                    break              # ler não muda a tela: com tudo lido e a pós-condição valendo, só comprovar
                continue
            if ((isinstance(args, StepDone) or decision.tool == "collect_list")
                    and (frente := self._tela_fora_do_app(step, obs, app.package)) is not None):
                # Etapa entre apps (item 24.7): as telas de dois apps podem mostrar a mesma coisa ("Conta: …", a lista),
                # e concluir ou coletar na tela do app errado seria dar por comprovado o que não foi. Recusa aqui, sem
                # gastar tentativa nem verificação, e diz ao ator qual app abrir; `_verify` tem a mesma trava.
                aid = intencao(decision.tool, args.model_dump(mode="json"), rationale,
                               side_effect=False, source="recipe" if from_recipe else "ai")
                repo.finish_action(aid, ActionStatus.rejected,
                                   error=f"a tela é do app {frente}, não do app da etapa ({app.package})")
                if from_recipe:
                    rr.diverged = f"a tela é do app {frente}, não do app da etapa"
                history.append(f"{decision.tool} REJEITADA: esta etapa é do app {app.package}, e a tela é do app "
                               f"{frente} — abra o app da etapa com open_app e conclua nele.")
                errors_in_row += 1
                if errors_in_row >= 4:
                    return await fail_or_retry("A IA insistiu em concluir a etapa fora do app dela.", obs)
                continue
            if isinstance(args, StepDone) and faltam_saidas():
                aid = intencao("step_done", args.model_dump(mode="json"), rationale, side_effect=False)
                repo.finish_action(aid, ActionStatus.rejected, error="valor da etapa ainda não lido")
                history.append("step_done REJEITADA: esta etapa entrega " + ", ".join(f"'{n}'" for n in faltam_saidas())
                               + " às seguintes — leia na tela com read_value antes de concluir.")
                errors_in_row += 1
                recusas_de_saida += 1
                if errors_in_row >= 4 or recusas_de_saida >= 4:
                    return await fail_or_retry("A IA concluiu a etapa sem ler o valor que ela entrega às seguintes: "
                                               + ", ".join(f"'{n}'" for n in faltam_saidas()) + ".", obs)
                continue
            if isinstance(args, StepDone) and collecting:
                aid = intencao("step_done", args.model_dump(mode="json"), rationale, side_effect=False)
                repo.finish_action(aid, ActionStatus.rejected, error="etapa de coleta: use collect_list")
                history.append("step_done REJEITADA: esta é uma etapa de COLETA — chame collect_list na lista; "
                               "os itens têm de ser lidos pelo executor.")
                errors_in_row += 1
                if errors_in_row >= 4:
                    return await fail_or_retry("A IA não usou collect_list na etapa de coleta.", obs)
                continue
            if (isinstance(args, StepDone) and step.opcional and self.cfg.file.ai.limpeza_opcional
                    and not self._tocou_na_limpeza(attempt_id)
                    and ((cobria := Cobertura.das_variaveis(step.variables)) is None or ainda_cobre(cobria, obs.tree))):
                # 31.40 b (iii): na 95d10f o ator deu step_done sem tocar em nada e a tela seguiu coberta. Concluir uma
                # limpeza sem fechar nada não é limpeza feita.
                aid = intencao("step_done", args.model_dump(mode="json"), rationale, side_effect=False)
                repo.finish_action(aid, ActionStatus.rejected, error="limpeza sem toque")
                history.append("step_done REJEITADA: nesta limpeza nada foi tocado e o que cobre a tela continua lá — "
                               "feche-o (o X, “Fechar”, “Agora não”, ou press_back) antes de concluir.")
                errors_in_row += 1
                if errors_in_row >= 4:
                    return await fail_or_retry("A IA concluiu a limpeza sem tocar no que cobre a tela.", obs)
                continue
            if isinstance(args, StepDone):
                declared = args
                aid = intencao("step_done", args.model_dump(mode="json"), rationale, side_effect=False)
                repo.finish_action(aid, ActionStatus.done, result={"declared": True})
                break
            if isinstance(args, StepBlocked) and args.kind == "dado_ausente" and not leitura:
                # 31.38: "dado ausente" só existe em etapa de leitura; na de efeito o ator usa os tipos de sempre.
                aid = intencao("step_blocked", {"kind": args.kind, "needs_user": args.needs_user}, rationale,
                               side_effect=False)
                repo.finish_action(aid, ActionStatus.rejected, error="dado_ausente fora de etapa de leitura")
                history.append("step_blocked(dado_ausente) REJEITADO: esta etapa não é de leitura; se não dá para "
                               "seguir, use outro kind (missing_info, unexpected_screen, other).")
                errors_in_row += 1
                if errors_in_row >= 4:
                    return await fail_or_retry("A IA insistiu em 'dado ausente' numa etapa que não é de leitura.", obs)
                continue
            if isinstance(args, StepBlocked):
                # O motivo é texto do MODELO e pode citar o que ele viu na tela (um código de verificação, um segredo). Ele
                # vai a quatro destinos — `steps.status_detail` e `attempts.error` (pelo desfecho), a nota da evidência e o
                # evento `decision` — e todos recebem a versão triada: se a triagem acusar, "motivo omitido (triagem: …)".
                razao = razao_sem_segredo(args.reason)
                aid = intencao("step_blocked", {**args.model_dump(mode="json"), "reason": razao},
                               rationale, side_effect=False)
                repo.finish_action(aid, ActionStatus.done, result={"kind": args.kind})
                await evidence(obs, f"Bloqueio relatado pela IA ({args.kind}): {razao}")
                repo.decision(f"{iid}: etapa '{step.title}' bloqueada — {razao}", run_id=run_id, instance_id=iid,
                              step_id=step.id)
                if args.kind == "challenge":
                    # A IA reconheceu uma verificação que o detector não conhece (ele já olhou ESTA tela antes de
                    # perguntar a ela). É julgamento do modelo, não casamento determinístico: vira `auth_challenge`
                    # e pede uma pessoa, sem bloquear o perfil — quem olhar decide se é a conta travada (ADR-055).
                    return await parar_na_trava(ContaTravada(SUBTIPO_VERIFICACAO, razao[:80], origem="ator"),
                                                obs.package)
                if step.side_effect and fired:
                    return StepOutcome(Outcome.uncertain, razao)
                if args.kind == "dado_ausente":
                    # 31.38: sem a cascata ao modelo forte nem nova tentativa: o plano revisado (uma vez) é a segunda olhada.
                    return await dado_ausente("o modelo de ação não o achou na tela", obs)
                if (tier == 0 and not from_recipe and not bloqueio_escalado and ai_cfg.cascade_blocked_to_tier1
                        and args.kind not in ("challenge", "auth_required", "wrong_account")):
                    # Item 17.10: o ator barato desiste cedo ("não vejo", "falta informação"). Antes de acordar uma pessoa,
                    # o modelo de escalonamento olha a MESMA tela, uma vez. Se ele também relatar o bloqueio, vale o caminho
                    # de sempre (tier 1 não sobe de novo).
                    bloqueio_escalado = True
                    forcar_tier_1 = True
                    cascata_pendente = True
                    history.append(f"(executor) o modelo de ação relatou bloqueio ({args.kind}: {razao}); o modelo de "
                                   "escalonamento reavalia esta mesma tela antes de pedir uma pessoa.")
                    repo.decision(f"{iid} · {step.title}: bloqueio relatado pelo modelo de ação ({args.kind}); "
                                  "subindo ao modelo de escalonamento antes de pedir uma pessoa",
                                  run_id=run_id, instance_id=iid, step_id=step.id)
                    continue
                if args.kind in ("auth_required", "wrong_account"):
                    # A IA viu login ou conta errada na tela. O perfil para de afirmar "Conectado": `wrong_account`
                    # e desafio dependem de pessoa; `auth_required` volta a ser trabalho do autenticador.
                    self._sessao_desmentida(iid, app.package, args.kind, razao)
                if args.kind == "missing_info":
                    # 29.35: a falta que não é credencial ganha uma revisão do plano antes de chegar à pessoa.
                    sensivel = _TRIAGEM_DE_CREDENCIAL.pergunta_sensivel(razao) is not None
                    return StepOutcome(Outcome.waiting_user, razao, needs=_needs_for(args.kind),
                                       falta_de_informacao=not sensivel)
                if args.needs_user or args.kind in ("auth_required", "wrong_account"):
                    return StepOutcome(Outcome.waiting_user, razao, needs=_needs_for(args.kind))
                return await fail_or_retry(razao)

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
                                   allowed_urls=urls_permitidas, allowed_hosts=hosts_das_contas, deadline=deadline,
                                   dormir=self.dormir)
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
                        aid = intencao(decision.tool, args.model_dump(mode="json"), rationale,
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
            elif decision.tool in EFFECT_CAPABLE and (
                    fora := efeito_fora_da_etapa(decision.tool, args, tool_ctx, obs.tree, app.package)) is not None:
                # 29.58 (A): o efeito é marcado pela AÇÃO, não pela etapa. Na 5f2de5 a IA, dentro de `open_app` (sem
                # efeito declarado), digitou e tocou em enviar: o toque saiu gravado sem efeito, sem a trava de não
                # repetir, sem a guarda e sem a aprovação da etapa que declara o envio — que enviou de novo depois.
                # Recusado ANTES de agir; o motivo diz ao ator o que fazer.
                motivo_fora = (f"{REJEICAO_EFEITO_FORA_DA_ETAPA} ({fora}). Não toque nele: termine esta etapa quando o "
                               "objetivo DELA estiver cumprido e deixe o efeito para a etapa que o declara.")
                aid = intencao(decision.tool, args.model_dump(mode="json"), rationale,
                               side_effect=True, source="recipe" if from_recipe else "ai")
                repo.finish_action(aid, ActionStatus.rejected, error=motivo_fora)
                metricas.contar("executor.efeito_fora_da_etapa", origem="recipe" if from_recipe else "ai",
                                ferramenta=decision.tool)
                if from_recipe:
                    rr.diverged = f"efeito externo numa etapa sem efeito: {fora}"
                history.append(f"{decision.tool} REJEITADA pelo executor: {motivo_fora}")
                errors_in_row += 1
                if errors_in_row >= 4:
                    return await fail_or_retry("Um efeito externo foi tentado numa etapa que não o declara.", obs)
                continue
            # ---------- LT-12: na nova tentativa, o modelo barato não repete onde a anterior parou nem dispara o efeito
            if retentativa and not retentativa_subiu and not from_recipe and tier == 0:
                repete = acao_onde_parou == (obs.tree.signature(estrutural=True), f"{decision.tool}:{_target_key(args)}")
                if repete or is_commit:
                    retentativa_subiu = True
                    history.append(f"(executor) {decision.tool} " + ("é a ação em que a tentativa anterior parou, nesta "
                                   "mesma tela" if repete else "dispararia o efeito desta etapa")
                                   + ": a decisão sobe ao modelo de escalonamento antes de agir.")
                    continue
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
                    aid = intencao(decision.tool, args.model_dump(mode="json"), rationale,
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
                    aid = intencao(decision.tool, args.model_dump(mode="json"), rationale, side_effect=True,
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
            if decision.tool not in FORA_DO_CICLO:        # item 31.37: só ler a tela não é ciclo
                sig = (obs.tree.signature(), f"{decision.tool}:{_target_key(args)}")
                same_count = same_count + 1 if sig == last_sig else 0
                last_sig = sig
                sigs.append((sig[0], obs.tree.signature(estrutural=True), sig[1]))
                self._ultima_acao_da_etapa[step.id] = (sigs[-1][1], sigs[-1][2])  # LT-12: onde esta tentativa parou
                ciclo = ciclo_sem_progresso(sigs, int(s.no_progress_limit))
                if ciclo:
                    return await fail_or_retry(ciclo, obs)

            # ---------- agir (intenção gravada ANTES)
            if is_commit and (revogada := self._excecao_encerrada(step)) is not None:
                # 30.65: a exceção desta etapa foi revogada (ou recusada) depois de a porta passar: nada sai.
                return await falhar_sem_nova_tentativa(revogada, obs)
            aid = intencao(decision.tool, args.model_dump(mode="json"), rationale, side_effect=is_commit,
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
            if decision.tool == "open_url":
                await self._espera_depois_do_open_url(rt, call_timeout, ai_cfg, history)
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
                    lista_ate_o_fim = bool(out.result.get("at_end"))
                    if empty_collects >= 3:
                        sufixo = ""
                        if lista_ate_o_fim:
                            # Lista sem item é falha, a menos que o vazio seja COMPROVADO (item 12.4): o julgamento
                            # pergunta pelo estado vazio explícito, não pela tela em geral.
                            vazio_provado, motivo = await self._prova_de_vazio(
                                rt, step, ctx_for, run_id, oid, deadline, call_timeout, history, attempt_id, app.package)
                            if vazio_provado is not None:
                                collected = []
                                break
                            sufixo = f" O vazio não foi comprovado: {motivo}"
                        return await fail_or_retry("A coleta não encontrou nenhum item na lista." + sufixo, obs)
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
            t_settle = time.monotonic()
            await asyncio.sleep(float(self.cfg.file.ai.action_settle_s))   # deixa a interface assentar antes da próxima observação
            settle_pendente = ms_desde(t_settle)
            if is_commit:
                break                  # depois do efeito não há mais o que decidir: só comprovar (sem outra chamada)
            if getattr(args, "expect_done", False) and not judged_step:
                # a IA previu que esta ação conclui a etapa: uma conferência determinística poupa o step_done
                try:
                    # conferência pela árvore: sem imagem (a evidência, se houver, adquire a sua)
                    peek = last_obs = await self.devices.observe(rt, timeout=call_timeout, imagem=False)
                except DriverError:
                    continue
                if not peek.sensitive and self._postcondition_holds(step, peek, cartao, pacote=app.package):
                    if not faltam_saidas():
                        break
                    history.append("(executor) a pós-condição já vale, mas falta ler com read_value: "
                                   + ", ".join(faltam_saidas()) + ".")
                    continue
                history.append("(executor) a pós-condição ainda NÃO vale depois desta ação; continue.")
            elif getattr(args, "expect_done", False) and not faltam_saidas() and not step.side_effect:
                # LT-2: o ator previu que esta ação conclui uma etapa JULGADA. Em vez de devolvê-la ao ator só para dizer
                # "pronto" (um decide a mais), o juiz de sempre confere a tela agora e o veredito é reusado no fim do
                # laço. "Não"/"incerto": entra no `history` e o laço segue NESTA tentativa (sem retry, sem falha).
                # NUNCA em etapa com efeito (como o LT-1): o commit sai do laço sozinho logo acima, e antes dele um "sim"
                # do juiz (o texto digitado no campo lido como já publicado) fecharia a etapa como sucesso sem o efeito.
                r = await julgar_antes_do_ator("depois desta ação (expect_done)")
                if isinstance(r, StepOutcome):
                    return r
                if r:
                    break
        else:
            return await fail_or_retry(f"Limite de {max_actions} ações por etapa atingido sem concluir.", last_obs)

        if collecting and collected is not None:
            if faltam_saidas():
                return await fail_or_retry("A lista foi lida, mas o valor " + ", ".join(f"'{n}'" for n in faltam_saidas())
                                           + " que esta etapa entrega às seguintes não foi lido (read_value).", last_obs)
            if vazio_provado is not None:
                text = "Lista vazia comprovada (nenhum item): " + vazio_provado[:300]
            else:
                text = f"{len(collected)} item(ns) lidos até o fim da lista: " + ", ".join(collected)[:400]
            await evidence(last_obs, f"Coleta comprovada pelo executor: {text}")
            repo.transition_step(step.id, StepStatus.verifying, message=f"Etapa '{step.title}': itens lidos pelo executor")
            with repo.db.tx():
                self._gravar_saidas(step, app, lidos, visuais)
                repo.transition_step(step.id, StepStatus.succeeded, detail=text,
                                     result=StepResult(verified=True, evidence_text=text, items=collected,
                                                       vazio_comprovado=vazio_provado is not None),
                                     message=f"Etapa '{step.title}' comprovada: {text}")
            repo.finish_attempt(attempt_id, AttemptStatus.succeeded, observed=text)
            return StepOutcome(Outcome.succeeded, text, items=collected, outputs=_saidas_do_desfecho(lidos))

        # ================================================================ verificar a pós-condição
        repo.transition_step(step.id, StepStatus.verifying,
                             message=f"Etapa '{step.title}': verificando a pós-condição"
                             + (" (reconciliação após resultado desconhecido)" if unknown else ""))
        try:
            if veredito_antecipado is not None:          # LT-1/LT-2: o juiz já conferiu esta tela; não paga outro
                ok, text, level, obs, unprovable = veredito_antecipado
            else:
                ok, text, level, obs, unprovable = await self._verify(rt, step, ctx_for, run_id, oid, deadline,
                                                                      call_timeout, patient=bool(need) or fired,
                                                                      facts=history[-12:],
                                                                      failure_marks=(tuple(cap.failure_marks)
                                                                                     if cap and fired else ()),
                                                                      local_proof=(cap.local_proof if cap else None),
                                                                      capability=(CapabilityRef(app.package, cap.key)
                                                                                  if cap and app.package else None),
                                                                      attempt_id=attempt_id, cartao=cartao,
                                                                      pacote=app.package, imagem_forcada=bool(visuais),
                                                                      copias_vistas=copias_vistas,
                                                                      sobreposicoes=sobreposicoes,
                                                                      coberturas=coberturas)
        except DriverTimeout as exc:
            return await self._stuck(rt, step, fired, str(exc))
        except AIError as exc:
            if exc.kind == "not_configured":
                desfecho = StepOutcome(Outcome.waiting_user, str(exc), needs="Configure a chave do provedor no .env, "
                                       "reinicie o backend e retome este item.", ai_blocked=True)
            elif exc.kind in ("billing", "balance"):
                desfecho = StepOutcome(Outcome.waiting_user, str(exc),
                                       needs="Recarregue o crédito do provedor de IA e retome a execução.",
                                       ai_blocked=True)
            elif exc.kind == "step_deadline":
                desfecho = await fail_or_retry(com_anr(f"Prazo da etapa ({step.timeout_s}s) esgotado durante a "
                                                       f"verificação: {exc}"), last_obs)
            elif exc.kind == "budget":
                desfecho = StepOutcome(Outcome.failed if not fired else Outcome.uncertain, str(exc))
            elif exc.kind == "refusal":
                # Mesma regra do achado #93 do lado da decisão: recusa por política não é "não pôde ser feita" —
                # repetir a verificação tende a dar a mesma recusa, sem gastar tentativa à toa.
                desfecho = StepOutcome(Outcome.uncertain if fired else Outcome.waiting_user,
                                       f"O provedor de IA recusou verificar esta etapa por política: {exc}",
                                       needs=None if fired else
                                       "O provedor recusou por política — repetir tende a dar o mesmo resultado. "
                                       "Reescreva a intenção desta etapa (ou o comando) e retome, ou replaneje.",
                                       ai_blocked=True)
            else:
                desfecho = await fail_or_retry(f"Verificação não pôde ser feita: {exc}", last_obs)
            # RA-22: o kind vai no desfecho; é ele, e não o texto, que classifica a falha.
            return dataclasses.replace(desfecho, ai_error_kind=exc.kind)
        except DriverError as exc:
            return await fail_or_retry(f"Verificação não pôde ser feita: {exc}", last_obs)
        # o nível de entrega declarado pela IA em step_done não vale como prova; só o observado na verificação
        note = f"Pós-condição {'comprovada' if ok else 'NÃO comprovada'}: {text}"
        await evidence(obs, note, kind="verifier" if obs is None else "screenshot")
        if step.side_effect and fired and (repetido := self._efeito_repetido(step, copias_vistas)) is not None:
            # 29.58 (C): o efeito saiu mais de uma vez. Nunca "sucesso comprovado" — nem falha: o efeito existe. A
            # pessoa confere e decide; `steps.result.efeito_repetido` diz quantas cópias e quem as viu.
            motivo = (f"efeito repetido ({repetido.copias}): o efeito externo desta etapa saiu mais de uma vez "
                      f"(visto {'pelo verificador na tela' if repetido.fonte == 'verificador' else 'nas ações gravadas'})"
                      f"; {text}")
            return StepOutcome(Outcome.uncertain, motivo, delivery_level=level,
                               result=StepResult(verified=False, evidence_text=text, delivery_level=level,
                                                 efeito_repetido=repetido))
        if ok and faltam_saidas():
            # Comprovada, mas sem o valor que as seguintes usam: nunca é sucesso — a próxima etapa pararia sem ele, e
            # com o efeito disparado repetir a etapa é o que não se faz.
            falta = ("Pós-condição comprovada, mas o valor " + ", ".join(f"'{n}'" for n in faltam_saidas())
                     + " que esta etapa entrega às seguintes não foi lido na tela (read_value).")
            if step.side_effect and fired:
                return StepOutcome(Outcome.uncertain, falta, delivery_level=level)
            return StepOutcome(Outcome.retry if step.attempts < step.max_attempts else Outcome.failed, falta)
        if ok:
            if (conta := evidencia_da_conta(account_label, step.postcondition.value, text)) is not None:
                repo.db.execute("UPDATE instances SET account_evidence=?, account_evidence_ts=? WHERE id=?",
                                (conta, now_iso(), iid))
            # As saídas e o sucesso na MESMA transação: sem etapa comprovada não há valor gravado, e vice-versa.
            with repo.db.tx():
                self._gravar_saidas(step, app, lidos, visuais)
                repo.transition_step(step.id, StepStatus.succeeded, detail=text,
                                     result=StepResult(verified=True, evidence_text=text, delivery_level=level),
                                     message=f"Etapa '{step.title}' comprovada: {text}")
            repo.finish_attempt(attempt_id, AttemptStatus.succeeded, observed=text,
                                recovery="Resultado confirmado por reconciliação da tela" if unknown else None)
            return StepOutcome(Outcome.succeeded, text, delivery_level=level, outputs=_saidas_do_desfecho(lidos))
        if step.side_effect and fired:
            return StepOutcome(Outcome.uncertain, f"O efeito foi disparado, mas não foi possível comprová-lo: {text}",
                               delivery_level=level)
        if unprovable and PARTES_EM_ELEMENTOS_DIFERENTES in text:
            return StepOutcome(Outcome.failed, "Defeito do plano — o seletor da pós-condição exige no MESMO elemento (`|`) "
                               "o que a tela mostra em elementos separados; repetir ou recuperar não resolve: "
                               f"{text}", plan_defect=True)
        if unprovable:
            return StepOutcome(Outcome.failed, "Defeito do plano — a pós-condição não é comprovável pela tela (descreve "
                               f"processo/histórico); repetir não resolve: {text}", plan_defect=True)
        if (sobreposicoes and sobreposicoes[-1] and not step.side_effect and not step.commit_guard and not step.opcional
                and self.cfg.file.ai.limpeza_apos_sobreposicao):
            # 31.40: o último "não" foi por algo que COBRE o alvo. Repetir a etapa daria na mesma tela coberta (3894c1:
            # três recusas e um plano revisado igual): o scheduler põe a limpeza opcional antes dela, uma vez.
            metricas.contar("etapa.sobreposicao")
            return StepOutcome(Outcome.failed, f"Pós-condição não comprovada (sobreposição): {text}", sobreposicao=True,
                               cobertura=coberturas[-1] if coberturas else None)
        return StepOutcome(Outcome.retry if step.attempts < step.max_attempts else Outcome.failed,
                           f"Pós-condição não comprovada: {text}")

    def _efeito_repetido(self, step: StepDTO, copias_vistas: list[int]) -> EfeitoRepetido | None:
        """29.58 (C): o efeito desta etapa saiu repetido? O maior entre o que o verificador contou na tela e o que as
        ações gravadas mostram; empate fica com as ações (determinístico). `None` com menos de 2 cópias."""
        pelo_verificador = max(copias_vistas, default=0)
        pelas_acoes = self.repo.copias_pelas_acoes(step.id)
        if max(pelo_verificador, pelas_acoes) < 2:
            return None
        if pelas_acoes >= pelo_verificador:
            return EfeitoRepetido(copias=pelas_acoes, fonte="acoes")
        return EfeitoRepetido(copias=pelo_verificador, fonte="verificador")

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

    @staticmethod
    def _tela_fora_do_app(step: StepDTO, obs: Observation, pacote: str | None) -> str | None:
        """O pacote à frente quando a tela NÃO é do app da etapa (`pacote`); `None` quando é, ou não há o que conferir.

        Com etapas de apps diferentes no mesmo objetivo (item 24.7), duas telas podem mostrar o mesmo texto e o mesmo
        seletor; a pós-condição comprovada na tela de outro app é sucesso falso. Pacote desconhecido nunca comprova
        (incerteza nunca conta como sucesso); a leitura só com `package` vazio e o da etapa na árvore segue a mesma
        regra do `app_foreground` em `_deterministic`. Ficam de fora a etapa sem pacote e a pós-condição
        `app_foreground`, que já diz qual pacote quer à frente."""
        if not pacote or step.postcondition.kind == "app_foreground":
            return None
        if obs.package == pacote or (obs.package is None and pacote in obs.tree.packages):
            return None
        return obs.package or "desconhecido"

    def _postcondition_holds(self, step: StepDTO, obs: Observation, cartao: tuple[str, ...] = (), *,
                             pacote: str | None) -> bool:
        """Conferência barata (sem modelo) usada pelo atalho `expect_done`; nunca vale para etapa julgada por visão.
        Com a legenda da publicação alvo (`cartao`), ela também precisa estar na tela — senão o atalho da receita
        ("o aparelho já estava no estado final") aceitaria qualquer publicação aberta. E a tela precisa ser do app da
        etapa (`pacote`): o atalho sai do laço de ações, e sair na tela de outro app só gastaria a verificação."""
        if step.postcondition.kind == "model_judged" or step.postcondition.required_delivery_level is not None:
            return False
        return (self._deterministic(step, obs)[0] and not textos_do_cartao_ausentes(cartao, obs.tree)
                and self._tela_fora_do_app(step, obs, pacote) is None)

    async def _prova_de_vazio(self, rt: DeviceRuntime, step: StepDTO, ctx_for: Callable[[], StepContext], run_id: str,
                              objective_id: str, deadline: float, call_timeout: float, history: list[str],
                              attempt_id: str, pacote: str | None) -> tuple[str | None, str]:
        """Item 12.4: a coleta leu a lista até o fim e não achou item. Só é sucesso com o vazio COMPROVADO: o julgamento
        pergunta se a tela mostra, de forma explícita, que não há itens (estado vazio). Lista com itens à vista, ou
        tela que só "parece certa", não comprova. Devolve `(evidência, "")` quando comprovado e `(None, motivo)` quando
        não — erro de IA ou do aparelho aqui também é não comprovado, nunca sucesso."""
        post = step.postcondition
        julgada = step.model_copy(update={"postcondition": Postcondition(
            kind="model_judged", value=f"a lista está vazia: {post.value}",
            description=("A tela mostra de forma EXPLÍCITA que a lista não tem nenhum item (estado vazio, como "
                         "\"nenhum resultado\"). Se há qualquer item da lista à vista, ou a tela não diz que está vazia, "
                         "NÃO está comprovado. Original: " + post.description))})
        def ctx_vazio() -> StepContext:                # `ctx_for` fecha sobre a etapa original: a pergunta é outra
            return dataclasses.replace(ctx_for(), postcondition_description=julgada.postcondition.description)

        try:
            ok, texto, _, _, _ = await self._verify(rt, julgada, ctx_vazio, run_id, objective_id, deadline, call_timeout,
                                                    patient=False, facts=history[-12:], attempt_id=attempt_id,
                                                    pacote=pacote, proposito="vazio")
        except (AIError, DriverError, DriverTimeout) as exc:
            return None, f"a verificação não pôde ser feita ({type(exc).__name__})"
        return (texto, "") if ok else (None, texto or "a tela não mostra o estado vazio")

    def _tocou_na_limpeza(self, attempt_id: str) -> bool:
        """31.40 b (iii): a tentativa da limpeza já fez algum gesto que fecha (toque, arrasto, rolagem, voltar)."""
        marcas = ",".join("?" * len(GESTOS_DA_LIMPEZA))
        return bool(self.repo.db.scalar(f"SELECT COUNT(*) FROM actions WHERE attempt_id=? AND status='done' "
                                        f"AND tool IN ({marcas})", (attempt_id, *GESTOS_DA_LIMPEZA)))

    def _tempos(self, attempt_id: str | None) -> TemposDaTentativa | None:
        """Os tempos da tentativa em curso (31.24, C-4), ou `None` fora de `run_step` (chamada sem tentativa)."""
        return self._tempos_da_tentativa.get(attempt_id) if attempt_id else None

    async def _relacao_visual(self, rt: DeviceRuntime, step: StepDTO, ctx_for: Callable[[], StepContext], run_id: str,
                              objective_id: str, deadline: float, attempt_id: str | None, obs: Observation, nome: str,
                              valor: str, ai: AiCfg) -> str | None:
        """31.41: o valor lido da IMAGEM não tem elemento com texto na árvore para a regra determinística. Uma pergunta
        de sim ou não ao verificador, com a imagem: este valor é, na tela, o `nome` pedido? "yes" = `"verificador"`;
        "no", "uncertain" ou qualquer outra coisa = `None` (dúvida nunca fecha como sucesso). Custa uma chamada de
        verificação por leitura visual aceita pelo leitor."""
        ctx = dataclasses.replace(ctx_for(), postcondition_description=(
            f"O valor lido para '{nome}' foi \"{valor}\". Julgue SÓ a relação: na tela, esse texto é o '{nome}' que o "
            "objetivo pede (o rótulo, a posição ou o papel dele na tela o identificam como tal), e não outro texto "
            "qualquer? yes = é; no = é outro; uncertain = não dá para afirmar."))
        if obs.jpeg is None:
            obs = await self.devices.completar_imagem(rt, obs, timeout=float(self.get_settings().driver_call_timeout_s),
                                                      lado_max=ai.screenshot_max_side)
        screen, _ = self._screen(obs, with_image=True, protect=tuple(step.commit_guard), ai=ai)
        t_end = min(deadline, time.monotonic() + float(ai.verify_budget_s))
        verdict = await self._ai(run_id, objective_id,
                                 lambda: self.provider.verify(VerifyRequest(ctx=ctx, screen=screen, facts=[])),
                                 step_id=step.id, role="verify", deadline=t_end, attempt_id=attempt_id,
                                 marca=MarcaDaChamada(motivo="julgamento", image_reason="pedida"))
        return "verificador" if verdict.satisfied == "yes" else None

    async def _verify(self, rt: DeviceRuntime, step: StepDTO, ctx_for: Callable[[], StepContext], run_id: str,
                      objective_id: str, deadline: float, call_timeout: float, *, patient: bool,
                      facts: list[str] | None = None, failure_marks: tuple[str, ...] = (),
                      local_proof: str | None = None, capability: CapabilityRef | None = None,
                      attempt_id: str | None = None, cartao: tuple[str, ...] = (), pacote: str | None,
                      imagem_forcada: bool = False, uma_rodada: bool = False, so_prova_local: bool = False,
                      proposito: MotivoDaChamada = "julgamento", copias_vistas: list[int] | None = None,
                      sobreposicoes: list[bool] | None = None, coberturas: list[Cobertura] | None = None
                      ) -> tuple[bool, str, DeliveryLevel | None, Observation | None, bool]:
        """A verificação (`_verificar`), com o tempo inteiro dela somado na tentativa (31.24, C-4:
        `attempts.verificacao_ms`). Só mede: os argumentos passam como vieram."""
        inicio = time.monotonic()
        if step.opcional and self.cfg.file.ai.limpeza_opcional:
            if step.key.startswith(PREFIXO_LIMPEZA) and Cobertura.das_variaveis(step.variables) is None:
                # 31.40 b (i): a limpeza da sobreposição sem o elemento que cobria: a árvore não decide, então UM
                # julgamento (uma leitura, um veredito) por limpeza; os seguintes, só a prova local. A conferência da
                # ENTRADA (antes do ator, `so_prova_local`) não o gasta: o juiz é para depois do gesto.
                if so_prova_local:
                    pass
                elif attempt_id is not None and attempt_id in self._juiz_da_limpeza:
                    so_prova_local = True
                else:
                    uma_rodada = True
                    if attempt_id is not None:
                        self._juiz_da_limpeza.add(attempt_id)
            else:
                so_prova_local = True          # item 31.36: a limpeza opcional só se comprova sem juiz (31.40 b: com o
                                               # elemento que cobria conhecido, a árvore decide antes, em `_verificar`)
        try:
            return await self._verificar(rt, step, ctx_for, run_id, objective_id, deadline, call_timeout,
                                         patient=patient, facts=facts, failure_marks=failure_marks,
                                         local_proof=local_proof, capability=capability, attempt_id=attempt_id,
                                         cartao=cartao, pacote=pacote, imagem_forcada=imagem_forcada,
                                         uma_rodada=uma_rodada, so_prova_local=so_prova_local, proposito=proposito,
                                         copias_vistas=copias_vistas, sobreposicoes=sobreposicoes,
                                         coberturas=coberturas)
        finally:
            if (tempos := self._tempos(attempt_id)) is not None:
                tempos.verificacao_ms += ms_desde(inicio)

    async def _verificar(self, rt: DeviceRuntime, step: StepDTO, ctx_for: Callable[[], StepContext], run_id: str,
                         objective_id: str, deadline: float, call_timeout: float, *, patient: bool,
                         facts: list[str] | None = None, failure_marks: tuple[str, ...] = (),
                         local_proof: str | None = None, capability: CapabilityRef | None = None,
                         attempt_id: str | None = None, cartao: tuple[str, ...] = (), pacote: str | None,
                         imagem_forcada: bool = False, uma_rodada: bool = False, so_prova_local: bool = False,
                         proposito: MotivoDaChamada = "julgamento", copias_vistas: list[int] | None = None,
                         sobreposicoes: list[bool] | None = None, coberturas: list[Cobertura] | None = None
                         ) -> tuple[bool, str, DeliveryLevel | None, Observation | None, bool]:
        """`uma_rodada`: uma só leitura e, se a pós-condição a exigir, um só julgamento — devolve o veredito mesmo
        negativo, sem esperar a tela mudar até o fim do orçamento. É o modo dos atalhos que conferem ANTES do ator
        (LT-1/LT-2): ali um "não" devolve a etapa ao ator na mesma tentativa, e esperar o orçamento inteiro custaria
        mais do que o decide poupado. A prova é a mesma; só a insistência muda.

        `so_prova_local`: a pós-condição julgada só pode ser aprovada pela prova local do catálogo; sem ela (ou com ela
        negativa) NÃO chama o modelo e devolve "não comprovada". É o que a conferência na ENTRADA da etapa usa quando o
        catálogo declara prova local: a maioria das telas de entrada ainda não é a final, e um julgamento pago ali, sem
        o ator ter feito nada, custaria mais verificações do que o atalho poupa decisões.

        `proposito` (RA-10): para que este julgamento existe — o de sempre ou a prova de vazio da coleta (12.4) —,
        gravado em `ai_calls.motivo`; o rejulgamento escalado sobre a mesma tela é `rejulgamento` em qualquer caso."""
        post = step.postcondition
        need = post.required_delivery_level
        ai_cfg = self._ai_da_execucao(run_id)              # 17.14: o perfil da execução pode trocar imagem e árvore
        budget = min(max(deadline - time.monotonic(), float(ai_cfg.verify_budget_min_s)),
                     float(ai_cfg.verify_budget_patient_s if patient else ai_cfg.verify_budget_s))
        t_end = time.monotonic() + budget
        max_calls = int(self.cfg.file.ai.verify_max_model_calls)
        judged_polls = 0
        judged_sig: str | None = None
        parada = 0                                 # LT-5: sondagens seguidas na MESMA tela depois do "não"
        verdict_text, level, obs = "", None, None
        escalou = False                            # no máximo UM rejulgamento escalado por verificação (item 7.10)
        marcas_pendentes = self._marcas_pendentes(capability)
        # LT-5: o "não" numa tela parada só valia no fim do orçamento (15 s; 60 s `patient`) — a 2ª chamada só vem com a
        # tela mudada, então esperar não muda o veredito. Sai cedo, exceto onde a mudança tem dono fora da tela: o
        # "Sending…" declarado (ADR-055) e o nível que depende do outro lado (entregue/lida chega sem a árvore mudar antes).
        sai_cedo = not (patient and marcas_pendentes) and (need is None
                                                           or DELIVERY_ORDER[need] <= DELIVERY_ORDER[DeliveryLevel.sent])
        pronta: Observation | None = None          # 31.27: a última leitura da espera adaptativa vira a 1ª do laço
        if patient and (post.kind == "model_judged" or need is not None):
            # o app costuma levar ~1–2 s para sair de "enviando": evita pagar 2 julgamentos
            pronta = await self._esperar_o_juiz(attempt_id, rt=rt, call_timeout=call_timeout, marcas=marcas_pendentes)
        lado_max = ai_cfg.screenshot_max_side
        while True:
            # Só a árvore: a maioria das conferências é determinística. A imagem vem logo antes do julgamento que a
            # usa (`completar_imagem`), e a evidência final adquire a sua se a observação não tiver (C1). UI ocupada
            # relê dentro do orçamento desta verificação: logo depois do efeito é quando o convidado mais pena.
            if pronta is not None:
                obs, pronta = pronta, None
            else:
                obs = await reler_se_ocupada(lambda: self.devices.observe(rt, timeout=call_timeout, imagem=False),
                                             prazo=t_end, quem=rt.id)
            ok, text = self._deterministic(step, obs)
            # Nível de entrega (enviada/entregue/lida) não é comprovável por texto/seletor — o texto já aparece no
            # campo ANTES do envio. Sempre que o plano exigir um nível, o verificador julga a tela também.
            judged = post.kind == "model_judged" or (ok and need is not None)
            if step.opcional and (cobria := Cobertura.das_variaveis(step.variables)) is not None:
                # 31.40 b (i): a limpeza sabe o que cobria a tela; a árvore decide, sem IA: saiu (ou deixou a área que
                # cobria) é limpeza feita; ainda lá, não comprovada. O juiz não é chamado nos dois casos.
                judged = False
                ok = not ainda_cobre(cobria, obs.tree)
                text = ("o elemento que cobria a tela saiu dela: comprovado pela árvore, sem IA" if ok else
                        "o elemento que cobria a tela continua nela (árvore, sem IA)")
            if (frente := self._tela_fora_do_app(step, obs, pacote)) is not None:
                # A tela é de OUTRO app (etapas entre apps, item 24.7): o texto ou o seletor da pós-condição podem
                # estar lá também, e o modelo julgaria a tela errada. Nem prova local nem modelo; segue olhando até o
                # fim do prazo desta verificação (o app da etapa pode estar chegando à frente) e, sem ele, não comprova.
                ok, judged = False, False
                text = "; ".join(t for t in (text, f"a tela é do app {frente}, não do app da etapa ({pacote}): não "
                                             "conta como prova") if t)
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
            provada = (judged and need is None and bool(local_proof)
                       and await self._prova_local(step, capability, obs, conta=getattr(ctx_for(), "account_label", None)))
            if provada and step.side_effect and local_proof.startswith("count_gt"):
                # 30.60: a prova por CONTAGEM (publicar) não fecha o efeito sozinha. O contador do cabeçalho sobe de forma
                # otimista, antes de o upload terminar ("Posting…"), e uma publicação nunca se repete por dúvida: a prova
                # vira fato para o modelo, e o "sim" dele passa pelo rejulgamento do 17.10. O `sent_text` da DM segue
                # como atalho: é o critério objetivo do ADR-055 (bolha com o texto e campo vazio), não um contador.
                fato = f"a prova local da pós-condição casou na tela ({local_proof})"
                if fato not in (facts or []):           # o laço relê a tela: o fato entra uma vez só
                    facts = [*(facts or []), fato]
                    text = "; ".join(t for t in (text, fato + "; o modelo confere antes de dar por feito") if t)
            elif provada:
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
            if judged and so_prova_local:
                ok, judged = False, False
                text = "; ".join(t for t in (text, "a prova local do catálogo não confirmou e o modelo não foi consultado "
                                             "antes do ator") if t)
            if judged:
                sig = obs.tree.signature()
                if judged_polls and sig == judged_sig:
                    ok = False             # mesma tela que já foi julgada insuficiente: espera mudar, sem gastar chamada
                    parada += 1
                else:
                    parada = 0
                    # 1º julgamento só pela hierarquia quando ela é rica; os seguintes levam a imagem
                    # Item 12.5: com saída lida da IMAGEM o juiz recebe a imagem à força. Ele julga a tela ("caixa
                    # aberta, aba, mais recente"), não o valor.
                    motivo_imagem = self._motivo_da_imagem(obs.tree, judged_step=False, first=False,
                                                           trouble=judged_polls >= 1, requested=imagem_forcada, ai=ai_cfg)
                    quer_imagem = motivo_imagem in _IMAGEM_VAI
                    if quer_imagem:
                        obs = await self.devices.completar_imagem(rt, obs, timeout=call_timeout, lado_max=lado_max)
                    screen, _ = self._screen(obs, with_image=quer_imagem, protect=tuple(step.commit_guard), ai=ai_cfg)
                    # Item 31.46: o que o app declara sobre a própria árvore (a linha da lista do Outlook sem texto),
                    # uma vez por julgamento; vazio para quem não declara, e o pedido fica como era.
                    dicas = telas_do_app.dicas_da_tela(CONHECIMENTO_DE_APPS / (obs.package or ""), obs.tree,
                                                       package=obs.package)
                    # `t_end` é o orçamento DESTA verificação (nunca além do prazo da etapa): a chamada de
                    # verificação passa a ter limite próprio, que era o que faltava (achado #96).
                    if await self._sent_text_dispensa_o_juiz(step, capability, obs, need=need, local_proof=local_proof,
                                                             ja_julgou=judged_polls > 0, escalou=escalou):
                        # 31.26 (A): a prova local `sent_text` já comprovou o envio desta execução na árvore (o app não
                        # mostra "Entregue"). Ela substitui SÓ o julgamento barato: o "sim" daqui segue para o
                        # rejulgamento do 17.10 logo abaixo, que é quem decide.
                        verdict = Verdict(satisfied="yes", delivery_level=DeliveryLevel.sent,
                                          evidence=f"prova local ({local_proof}) na árvore: o primeiro julgamento foi "
                                                   "dispensado; o rejulgamento confere")
                        metricas.contar("verificacao.primeiro_juiz_dispensado", prova=str(local_proof))
                        self.repo.decision(f"{rt.id} · {step.title}: envio comprovado pela árvore local (sent_text); o "
                                           "primeiro julgamento foi dispensado e o rejulgamento confere",
                                           run_id=run_id, instance_id=rt.id, step_id=step.id)
                    else:
                        verdict = await self._ai(run_id, objective_id,
                                                 lambda: self.provider.verify(VerifyRequest(ctx=ctx_for(), screen=screen,
                                                                                            facts=list(facts or []),
                                                                                            dicas_da_tela=dicas)),
                                                 step_id=step.id, role="verify", deadline=t_end, attempt_id=attempt_id,
                                                 marca=MarcaDaChamada(motivo=proposito, image_reason=motivo_imagem))
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
                                                                       facts=list(facts or []), escalate=True,
                                                                       dicas_da_tela=dicas)),
                            step_id=step.id, role="verify", deadline=t_end, attempt_id=attempt_id,
                            marca=MarcaDaChamada(motivo="rejulgamento", escalate="nivel", image_reason=motivo_imagem))
                        level = verdict.delivery_level
                    if (verdict.satisfied == "yes" and not escalou and (step.side_effect or need is not None)
                            and self.cfg.file.ai.rejudge_yes_on_side_effect
                            and self.cfg.ai_role("verify").model != self.cfg.ai_role("escalation").model):
                        # Item 17.10: um "sim" errado numa etapa com efeito externo (ela mesma, ou a que confirma o nível de entrega do efeito) vira sucesso falso (no rejulgamento de
                        # 25/09 o Haiku aprovou 6 telas erradas em 56). O modelo de escalonamento confere a mesma tela,
                        # uma vez, e o veredito dele é o que vale: se discordar, não conta como prova.
                        escalou = True
                        self.repo.decision(
                            f"{rt.id} · {step.title}: o verificador aprovou uma etapa com efeito externo; "
                            "conferindo com o modelo de escalonamento",
                            run_id=run_id, instance_id=rt.id, step_id=step.id)
                        verdict = await self._ai(
                            run_id, objective_id,
                            lambda: self.provider.verify(VerifyRequest(ctx=ctx_for(), screen=screen,
                                                                       facts=list(facts or []), escalate=True,
                                                                       dicas_da_tela=dicas)),
                            step_id=step.id, role="verify", deadline=t_end, attempt_id=attempt_id,
                            marca=MarcaDaChamada(motivo="rejulgamento", escalate="sim_com_efeito",
                                                 image_reason=motivo_imagem))
                        level = verdict.delivery_level
                    if copias_vistas is not None and verdict.copias is not None:
                        copias_vistas.append(verdict.copias)
                    if sobreposicoes is not None and verdict.satisfied in ("no", "uncertain"):
                        sobreposicoes.append(bool(verdict.sobreposicao))
                        if (coberturas is not None and verdict.sobreposicao
                                and (achada := cobertura_na_arvore(obs.tree, verdict.cobre)) is not None):
                            coberturas.append(achada)
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
                if (frente := self._tela_fora_do_app(step, obs, pacote)) is not None:
                    # O retrato assentado já é de outro app: o "sim" de antes não vale para a etapa (item 24.7).
                    ok = False
                    text = "; ".join(x for x in (text, f"depois de assentar, a tela é do app {frente}, não do app da "
                                                 f"etapa ({pacote})") if x)
                pendentes = marcas_pendentes_na_tela(marcas_pendentes, obs.tree)
                if pendentes:
                    # O mesmo retrato assentado com "Sending…" (ADR-055): não desmente o envio, mas também não deixa
                    # o "sim" valer. Segue olhando até o prazo; se a marca sumir, a prova se refaz na próxima leitura.
                    ok = False
                    text = "; ".join(x for x in (text, "depois de assentar, a tela mostra "
                                                 + ", ".join(f'"{m}"' for m in pendentes)
                                                 + ": envio pendente, não conta como feito") if x)
            if not ok and sai_cedo and parada >= SONDAGENS_DA_TELA_PARADA:
                return False, "; ".join(t for t in (text, f"a tela não mudou em {parada} sondagens depois do \"não\": a "
                                                    "verificação encerra sem esperar o fim do orçamento") if t), \
                    level, obs, False
            if ok or uma_rodada or time.monotonic() >= t_end or judged_polls >= max_calls:
                if (not ok and not uma_rodada and post.kind == "element_present" and frente is None and not pendentes
                        and not ausentes and obs.tree.partes_em_elementos_diferentes(post.value)):
                    # 31.32: na tela FINAL (o orçamento inteiro olhado), cada parte do seletor está na tela, mas em
                    # elementos diferentes, e `|` exige o mesmo elemento. Outra tentativa ou o plano revisado (que
                    # copia a etapa) pediriam o mesmo impossível: é defeito do plano, e a etapa falha já.
                    return False, "; ".join(t for t in (text, PARTES_EM_ELEMENTOS_DIFERENTES) if t), level, obs, True
                return ok, text, level, obs, False
            await self._esperar_o_juiz(attempt_id)

    async def _esperar_o_juiz(self, attempt_id: str | None, *, rt: DeviceRuntime | None = None,
                              call_timeout: float = 0.0, marcas: tuple[str, ...] = ()) -> Observation | None:
        """A espera deliberada do verificador (`judge_wait_s`), somada na tentativa (31.24, C-4:
        `attempts.juiz_espera_ms`).

        31.27: com `rt` (a espera ANTES do primeiro julgamento) e `judge_wait_estavel_s` > 0, ela é adaptativa: lê a
        árvore e sai quando a tela fica igual por `judge_wait_estavel_s`, sem marca pendente do catálogo, nunca além de
        `judge_wait_s`. Devolve a última leitura, que o verificador usa como a sua primeira (não lê de novo). Tela que
        ainda muda espera até o teto, como antes; leitura que falha cai na espera fixa. Sem `rt` (a espera entre duas
        sondagens, que espera a tela MUDAR) ou com 0, é a espera fixa de sempre."""
        inicio = time.monotonic()
        ai = self.cfg.file.ai
        teto, estavel = float(ai.judge_wait_s), float(ai.judge_wait_estavel_s)
        try:
            if rt is None or estavel <= 0 or teto <= 0:
                await asyncio.sleep(teto)
                return None
            return await self._esperar_a_tela_parar(rt, inicio + teto, estavel, call_timeout, marcas)
        finally:
            if (tempos := self._tempos(attempt_id)) is not None:
                tempos.juiz_espera_ms += ms_desde(inicio)

    async def _espera_depois_do_open_url(self, rt: DeviceRuntime, call_timeout: float, ai: AiCfg,
                                         history: list[str]) -> bool:
        """Item 31.35 (parte B), `ai.espera_apos_open_url`: a página carregando não pede decisão; a espera adaptativa do
        31.27 (tela parada por 1 s, teto de 8 s) faz o que o ator fazia com um `wait_for` pago. Desligada, nada muda."""
        if not ai.espera_apos_open_url:
            return False
        await self._esperar_a_tela_parar(rt, time.monotonic() + 8.0, 1.0, call_timeout, ())
        history.append("(executor) esperou a página assentar depois do open_url, sem decisão da IA")
        return True

    async def _esperar_a_tela_parar(self, rt: DeviceRuntime, fim: float, estavel: float, call_timeout: float,
                                    marcas: tuple[str, ...]) -> Observation | None:
        """A espera adaptativa do 31.27: a última leitura quando a assinatura da árvore ficou igual por `estavel` sem
        marca pendente, ou quando o teto (`fim`, relógio monotônico) chegou. Marca pendente na tela recomeça a contagem:
        "Enviando…" parado não é tela assentada."""
        assinatura: str | None = None
        desde = 0.0
        while True:
            try:
                obs = await self.devices.observe(rt, timeout=call_timeout, imagem=False)
            except (DriverBusy, FalhaDeLeitura, DriverError, DriverTimeout):
                # A leitura da espera não decide nada: falhou, espera o resto do teto e o verificador lê como sempre.
                await asyncio.sleep(max(0.0, fim - time.monotonic()))
                return None
            agora = time.monotonic()
            atual = obs.tree.signature()
            if atual != assinatura or marcas_pendentes_na_tela(marcas, obs.tree):
                assinatura, desde = atual, agora
            if agora - desde >= estavel or agora >= fim:
                return obs
            await asyncio.sleep(min(PASSO_DA_ESPERA_DO_JUIZ_S, max(0.0, fim - agora)))

    @staticmethod
    def _marcas_pendentes(capability: CapabilityRef | None) -> tuple[str, ...]:
        """As `pending_marks` que o catálogo declara para a ação desta etapa ("Sending…" no SEND_MESSAGE, ADR-055).

        Lidas pela referência de capability que o `_run_step` já entrega ao `_verify`: acrescentar uma marca no
        catálogo não pede fiação nova. Etapa sem catálogo (QA Messenger, plano livre) não tem marca: o "Enviando…"
        dele continua julgado como sempre."""
        cap = capability_of(capability.app, capability.key) if capability is not None else None
        return tuple(m for m in cap.pending_marks if m) if cap is not None else ()

    async def _sent_text_dispensa_o_juiz(self, step: StepDTO, capability: CapabilityRef | None, obs: Observation, *,
                                         need: DeliveryLevel | None, local_proof: str | None, ja_julgou: bool,
                                         escalou: bool) -> bool:
        """31.26 (opção A): o primeiro julgamento desta verificação pode ser dispensado? Só quando TUDO vale: nível exigido
        `sent` (nunca entregue/lida, que a árvore não prova), a ação declara a prova local `sent_text` e ela confirma
        nesta tela, é o primeiro julgamento (nenhum "não" antes), e o rejulgamento do 17.10 vai acontecer (ligado e com
        modelo diferente). Sem o rejulgamento, a prova local sozinha fecharia o efeito, e isso o desenho não aceita."""
        ai = self.cfg.file.ai
        if not (ai.sent_text_dispensa_primeiro_juiz and need == DeliveryLevel.sent and local_proof
                and local_proof.startswith("sent_text") and not ja_julgou and not escalou
                and ai.rejudge_yes_on_side_effect
                and self.cfg.ai_role("verify").model != self.cfg.ai_role("escalation").model):
            return False
        # A porta não afirma nível de entrega (`catalog_provider`: "exige o verificador"), e está certa: aqui a pergunta
        # é só se o TEXTO saiu (`sent_text`). O nível `sent` já foi conferido acima, e o rejulgamento confere a tela.
        return await self._prova_local(step, capability, obs, sem_nivel=True)

    async def _prova_local(self, step: StepDTO, capability: CapabilityRef | None, obs: Observation, *,
                           sem_nivel: bool = False, conta: str | None = None) -> bool:
        """A prova local pela porta `CapabilityProvider.verify` (fase G). `proved` é o atalho de sempre.

        `not_proved` (marca de falha visível na tela) também cai para o caminho de sempre, e não reprova aqui: quem
        reprova por marca de falha é a conferência depois do "sim", que só vale com o efeito disparado. A única
        diferença para a `local_proof_holds` direta é a tela com marca de falha E prova positiva: antes era atalho,
        agora o modelo julga — mais conservador, nunca transforma falha em sucesso.
        """
        if capability is None:
            return False
        # 30.60: a conta esperada no aparelho entra como `account_label`, para a prova conferir que a tela é a do PRÓPRIO
        # perfil (o argumento da etapa, se houver, vence).
        argumentos = {**({"account_label": conta} if conta else {}),
                      **{k: str(v) for k, v in (step.bindings or {}).items() if v is not None}}
        vista = StepView(node_id=step.key, capability=capability,
                         bindings=tuple(argumentos.items()),
                         band_guard=tuple(step.band_guard or ()),
                         required_delivery_level=(step.postcondition.required_delivery_level.value
                                                  if step.postcondition.required_delivery_level and not sem_nivel
                                                  else None))
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
        "missing_info": ("Revise o comando/configuração com a informação que falta e retome o item (o plano já foi "
                         "revisado uma vez automaticamente quando a falta não era credencial)."),
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
        if not d["unique"] and (filhos := filhos_rotulados(tree, el)):
            d["filhos"] = filhos         # 29.40 item 2: o contêiner sem identidade, pelo filho rotulado
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
    #: RA-20 B: a chave sem a pós-condição escrita (`hash_generico_da_linha`); None = a etapa só tem a específica.
    step_hash_generico: str | None = None
    signature: str = ""
    variant: str = ""
    diverged: str | None = None
    retorno_contado: bool = False      # `receita.retorno_ia` já contado nesta tentativa
    completed_by_recipe: bool = False
    #: A etapa fechou por um atalho do executor (LT-1) SEM o ator decidir nada e sem ação de receita: `driven_by` grava
    #: `sem_ator`, nunca `ai` (nem nulo, que pareceria legado).
    sem_ator: bool = False
    settle: int = 0
    #: Etapa de leitura (item 24.3): as receitas estão ligadas, mas esta etapa não usa nem aprende nenhuma.
    leitura: bool = False
    #: Estratégias que a tentativa EXERCEU, em ordem (trilha da 045, `attempts.strategy`): `recipe` quando a receita
    #: foi consultada, `ai_actor` quando a IA decidiu; a divergência dá a cadeia `recipe>ai_actor`.
    exercised: list[str] = field(default_factory=list)

    def exerceu(self, kind: StrategyKind) -> None:
        if kind.value not in self.exercised:
            self.exercised.append(kind.value)


def saidas_exigidas(escolhidas: list[str], cap: Capability | None) -> list[str]:
    """Item 12.4: os valores que a etapa TEM de entregar para ser comprovada. O planejador escolhe o subconjunto que as
    etapas seguintes usam (`steps.saidas`); sem escolha, vale tudo o que a ação do catálogo declara
    (`Capability.saidas`). Antes só a escolha contava: o plano que não citou nenhum valor tinha `saidas=[]`, e a
    verificação — que prova a TELA — dava a etapa por comprovada sem remetente nem assunto (r-20261002204347-8c3f6e)."""
    return list(escolhidas) or list(cap.saidas if cap is not None else ())


def _saidas_do_desfecho(lidos: dict[str, tuple[str, str]]) -> dict[str, str] | None:
    """`StepOutcome.outputs`: os valores lidos em forma de texto; `None` quando a etapa não entrega nada."""
    return {nome: como_texto(valor, tipo) for nome, (valor, tipo) in lidos.items()} or None


def _safe_args(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {"raw": str(raw)[:300]}


#: 29.58 (A): o motivo da recusa de uma ação com cara de efeito externo numa etapa que não declara efeito.
REJEICAO_EFEITO_FORA_DA_ETAPA = "o efeito só sai na etapa que o declara: esta etapa não declara efeito externo"

#: Campo de composição (mensagem, comentário, resposta, legenda): Enter nele envia. Campo de busca fica de fora.
_CAMPO_DE_COMPOSICAO = re.compile(r"(mensag|message|coment|comment|reply|respo|legenda|caption|escrev|write)",
                                  re.IGNORECASE)


def efeito_fora_da_etapa(tool: str, args: BaseModel, ctx: ToolContext, tree: UiTree,
                         package: str | None) -> str | None:
    """29.58 (A): esta ação PARECE disparar um efeito externo? Devolve o porquê, ou `None`. Só é perguntado numa etapa
    sem efeito declarado. Mesma regra do caminho com efeito: com catálogo, o gatilho é ESTRUTURAL (o `commit_selector`
    das capacidades com efeito do app; o "New post" que só abre a criação não é envio); sem catálogo, o vocabulário de
    verbos (`looks_like_commit`). A decisão que se declara commit (`is_commit_action`) conta sempre."""
    if bool(getattr(args, "is_commit_action", False)):
        return "a própria decisão declarou este gesto como o efeito externo"
    alvo = None
    try:
        if tool in ("tap", "long_press"):
            alvo = resolve_point(ctx, getattr(args, "element_id", None), getattr(args, "x", None),
                                 getattr(args, "y", None))[2]
        elif tool == "type_text" and bool(getattr(args, "press_enter", False)):
            eid = getattr(args, "element_id", None)
            alvo = (tree.by_id(eid) if eid else
                    next((e for e in tree.elements if e.focused and e.editable), None))
    except DriverError:
        return None                    # o alvo nem existe: a ferramenta falha adiante, sem chegar ao aparelho
    if alvo is None:
        return None
    rotulo = (alvo.text or alvo.desc or alvo.resource_id.rsplit("/", 1)[-1] or alvo.class_name)[:60]
    if tool == "type_text":
        campo = " ".join((alvo.text, alvo.desc, alvo.resource_id.rsplit("/", 1)[-1].replace("_", " ")))
        return f"Enter no campo '{rotulo}' envia" if _CAMPO_DE_COMPOSICAO.search(campo) else None
    catalogo = load_catalog(package)
    gatilhos = [c for c in catalogo.capabilities if c.side_effect and c.commit_selector] if catalogo is not None else []
    if gatilhos:
        # Catálogo sem nenhum gatilho declarado (o Outlook, hoje) cai no vocabulário, como a etapa sem seletor.
        for cap in gatilhos:
            if _gatilho_exato(alvo, cap.commit_selector or ""):
                return f"'{rotulo}' é o gatilho do efeito '{cap.key}' deste app"
        return None
    return f"'{rotulo}' parece disparar um efeito externo (enviar, publicar, confirmar)" if looks_like_commit(alvo) else None


def _gatilho_exato(alvo: UiElement, seletor: str) -> bool:
    """O `commit_selector` casa com `alvo` EXATAMENTE? Na etapa com efeito o seletor vale só para a capacidade da
    própria etapa; aqui ele é conferido contra TODAS as capacidades com efeito do app, e a substring sem caixa daria
    recusa falsa: `text=Follow` casa "Followers" e `text=Following` casa o rótulo "following" do perfil — abrir a lista
    de seguidores numa leitura seria recusado. Por isso texto e descrição casam o texto CRU inteiro; o `id` casa como
    sempre (é estrutural)."""
    for campo, valor, _ in UiTree._partes_do_seletor(seletor):
        if campo == "resource_id":
            rid = alvo.resource_id
            if not (rid == valor or rid.endswith("/" + valor) or rid.endswith(":id/" + valor)):
                return False
        elif (alvo.text if campo == "text" else alvo.desc).strip() != valor:
            return False
    return True


#: `apps.category` do app de prova (o QA Messenger embutido). A regra lê o DADO do app, nunca o nome (ADR-052), e exige
#: também `builtin`: `POST/PUT /apps` aceitam `category='qa'` em qualquer app, e marcar um app real assim não pode
#: tirá-lo do escalonamento.
CATEGORIA_APP_DE_PROVA = "qa"


def side_effect_tier(step: Any, cap: Any, modo: Any, app_de_prova: bool = False) -> tuple[int, str]:
    """(nível, motivo) do escalonamento POR EFEITO EXTERNO desta etapa.

    `True` = qualquer efeito sobe (era o único modo: em 19-23/09, 39 % das decisões foram ao Opus, inclusive curtir
    com `commit_selector` declarado — o executor já confere o seletor, as guardas e a faixa antes do toque, e o
    modelo caro não acrescentava nada). `False` = nunca por efeito. `by_risk` = sobe quando errar é caro E o
    software não tem como travar o alvo: risco alto do catálogo, risco médio sem seletor de commit, ou app sem
    catálogo (risco desconhecido). O motivo mantém o prefixo "etapa com efeito externo", que a linha do tempo e
    os testes reconhecem.

    `app_de_prova` (item 29.31, RA-8 da reavaliação de 03/10): o app de prova (`builtin` e `apps.category='qa'`) não tem
    catálogo e o efeito dele não sai da máquina de teste; "risco desconhecido" ali só pagava o modelo forte (64 a 66
    escalonamentos em 7 dias, 43 % das chamadas do Opus no tier 1). Vale SÓ no `by_risk` e SÓ para etapa sem
    capability: `True` é escolha explícita da instalação, e etapa com capability segue as regras de sempre (uma
    capability sem catálogo continua sendo risco desconhecido).
    """
    if not getattr(step, "side_effect", False) or modo is False:
        return 0, ""
    if modo is True:
        return 1, "etapa com efeito externo"
    if cap is None:
        if app_de_prova and not getattr(step, "capability", None):
            return 0, ""
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
