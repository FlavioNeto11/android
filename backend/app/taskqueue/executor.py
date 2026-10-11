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
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Mapping, Protocol, Sequence, TypeVar

from PIL import Image, ImageDraw
from pydantic import BaseModel

from ..automation import conhecimento_de_telas as telas_do_app
from ..automation import tools as ferramentas
from ..automation.driver import DriverBusy, DriverError, DriverTimeout, FalhaDeLeitura, sessao_perdida
from ..automation.hierarchy import (MOTIVO_DESAFIO, MOTIVO_SENHA, SUBTIPO_CODIGO, SUBTIPO_CONTA_TRAVADA,
                                    SUBTIPO_VERIFICACAO, ContaTravada, UiElement, UiTree)
from ..automation.tools import (CONTROL_TOOLS, EFFECT_CAPABLE, TOOLS, Drag, FindRow, ReadValue, StepBlocked, StepDone,
                                TelaDeContaTravada, ToolContext, ToolValidationError, esperar_foco, execute_tool,
                                looks_like_commit, resolve_point, urls_do_texto, validate_call)
from ..config import AiCfg, Config, LimitsCfg
from ..security.enderecos import endereco_para_o_prompt, enderecos_limpos
from ..shared.vinculos import tem_vinculo_ativo
from ..devices.adb import ABERTURA_COM_TAREFA_LIMPA, AVISO_DE_ANR, MorteDoApp, motivo_de_anr
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
from ..planning import costs
from ..planning.capabilities import (CONHECIMENTO_DE_APPS, Capability, capability_of, contraparte, guardas_do_cartao,
                                     load_catalog, marcas_de_entrega)
from ..planning.catalog import session_provider_of
from ..planning.entrega_declarada import entrega_do_pacote
from ..planning.provider import (AIError, AIProvider, AppContext, Decision, DecisionRequest, LeituraRequest,
                                 MarcaDaChamada, MotivoDaChamada, MotivoDaImagem, MotivoDeEscalonamento,
                                 PreparoDaDecisao, ScreenInput, StepContext, Transcricao, Usage, Verdict, VerifyRequest)
from ..db import Row, loads
from ..security.redaction import redact
from ..security.secret_store import SecretStoreLocked, SecretStoreUnavailable
from ..security.sensitive_input import SensitiveInputError, SensitiveInputUnavailable
from ..social.approvals import ler_rascunho
from ..social.chave_da_aprovacao import ARGUMENTO_DO_ROTULO_IA, rotulo_ia_exigido
from ..util import norm_text, now, now_iso, parse_iso, to_iso
from .costuras import (SAIU_POR_EXCECAO, SEM_COSTURAS, CosturasDeAprendizado, FechamentoDeTentativa, PedidoDeLicoes,
                       avisar, pedir_licoes)
from .foreach import sanitize_item, teto_de_chamadas
from .proofs import marcas_pendentes_na_tela, nivel_pelo_marcador, variantes_de_arroba
from .observabilidade import efeitos_rejulgados_do_app
from .projecao import HistoricoDeAcoes, app_da_etapa
from .latencia import TemposDaTentativa, ms_desde
from .midia_galeria import INTERNAS_POR_CODIGO, MidiaRecusada, colocar_midia_na_galeria
from .dado_da_persona import resolver_persona, rotulo, sem_valor_na_etapa
from .recipes import (escopo_do_alvo, mesmo_alvo, NAO_APLICAVEL_CONTA_APOS, READ_ONLY, AlvoAusente, RecipeDiverged, RecipeStore, Replayer,
                      contar_retorno_ia, distill, eh_generica, filhos_rotulados, hash_generico_da_linha,
                      unique_selectors)
from ..security.mascara_da_persona import USUARIO_DA_CONTA
from .repository import Repository
from .dialogos import (FRACAO_DA_PAGINA, FRACAO_QUE_COBRE, LIMITE_DE_DIALOGOS, LIMITE_DE_RECUSAS_DE_ACEITE,
                       MOTIVO_ACEITE_RECUSADO, MOTIVO_SEM_SAIDA, REJEICAO_TYPE_TEXT_FORA_DE_CAMPO, botao_que_fecha,
                       dialogo_sem_saida, e_navegador, rotulo_para_o_ator, texto_da_barra, tipo_do_elemento,
                       toque_que_aceita)
from .linha_por_remetente import SAIDA as SAIDA_DA_LINHA
from .linha_por_remetente import achar_linhas_por_remetente
from .relacao import e_nome_de_papel, pergunta_de_papel, relacao_do_valor
from .saidas import (RECUSAS_DETERMINISTICAS, ChaveDeTentativa, LeituraInvalida, LeituraSemTexto, LeituraVisual,
                     LeituraVisualRecusada, ObterImagem, Transcrever, args_da_chamada_invalida, args_sem_valor,
                     como_texto, ler_valor, ler_valor_visual, nomes_citados, razao_sem_segredo, texto_da_tela,
                     texto_do_elemento, triagem, variaveis_da_receita)

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
#: 31.61 (A): a etapa de LEITURA julgada com prova local declarada sai do laço sem `step_done` quando todas as saídas
#: estão lidas e a prova local vale numa árvore lida DEPOIS da última leitura. Na r-…2e0775 (caixa do Outlook) o último
#: `decide` era só o ator dizendo "pronto". `False` devolve o caminho de antes (a medida "antes" do teste).
LEITURA_FECHA_SEM_STEP_DONE = True
#: As provas locais que comprovam um EFEITO (publicar, enviar), não uma tela: etapa com uma delas não entra no 31.61.
PROVAS_DE_EFEITO = ("count_gt", "sent_text", "comentario")
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

#: 31.48: o pacote de um `id=<pacote>:id/<nome>` na pós-condição `element_present` (a primeira parte que o traz).
_ID_COM_PACOTE = re.compile(r"(?:^|\|)\s*id=([A-Za-z][\w.]*):id/")


def pacote_da_prova(post: Postcondition) -> str:
    """31.48: o app cuja tela a pós-condição `element_present` exige, quando o seletor diz (`id=com.x:id/lista`).
    Sem pacote no seletor, ou outro tipo de pós-condição, `""`.

    MEDIDO (banco central, 04/10): a etapa "abrir o QA Messenger" com `element_present id=…:id/conversation_list`
    (modelo 141e) foi à IA em 43 de 49 sucessos, 15 deles só para o ator pedir `open_app`; a mesma etapa com
    `app_foreground` (modelo 2c35) fechou sem ator em 50 de 50, pelo LT-6. A abertura é a mesma; o que muda é a prova."""
    if post.kind != "element_present":
        return ""
    achado = _ID_COM_PACOTE.search(post.value or "")
    return achado.group(1) if achado else ""


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


def imagem_com_barra_tapada(jpeg: bytes, tree: UiTree, pacote: str | None, largura: int,
                            altura: int) -> bytes | None:
    """31.54: a imagem (já no tamanho do modelo) com a barra de endereço do navegador tapada por um retângulo opaco,
    pelos bounds da `url_bar` na árvore (coordenadas do aparelho, `largura` x `altura`). `None` quando não há o que
    tapar com segurança: pacote sem barra conhecida, barra fora da árvore ou sem área. Quem chama manda a imagem como
    está e registra o motivo."""
    barra = BARRA_DE_ENDERECO.get(pacote or "")
    # 31.103 (S1 da leitura): TODO nó com o id da barra é tapado, não só o primeiro. A página pode pôr o id num elemento
    # antes da barra real; tapar só o primeiro taparia a falsa e mandaria o endereço real ao provedor. Tapar também o da
    # página é o lado que protege (some um trecho da página da imagem, nunca o endereço).
    caixas = [e.bounds for e in tree.elements if barra is not None and e.resource_id == barra
              and e.bounds[2] > e.bounds[0] and e.bounds[3] > e.bounds[1]]
    if not caixas or largura <= 0 or altura <= 0:
        return None
    with Image.open(io.BytesIO(jpeg)) as img:
        img = img.convert("RGB")
        fx, fy = img.width / largura, img.height / altura
        desenho = ImageDraw.Draw(img)
        for x1, y1, x2, y2 in caixas:
            desenho.rectangle((int(x1 * fx), int(y1 * fy), int(x2 * fx + 0.999), int(y2 * fy + 0.999)), fill=(0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=72)
    return buf.getvalue()


def linha_da_recusa_do_juiz(onde: str, texto: str) -> str:
    """31.54 (U1 da revisão): a linha do histórico do ator quando o juiz conferiu a tela e recusou. Com a barra sem
    tapar (o padrão), o juiz pode transcrever a URL da imagem; ela vai limpa (`enderecos_limpos`) ao histórico, que
    volta ao ator e chega ao `attempts.error` pelo `fail_or_retry`."""
    return (f"(executor) {onde}: o verificador conferiu a tela e a pós-condição NÃO está comprovada: "
            f"{enderecos_limpos(texto)[:300]}. Continue a partir da tela atual.")


#: 31.69: abaixo disto (caracteres normalizados) o valor é curto demais para se reconhecer sozinho ("1", "Sim", "OK").
VALOR_CURTO = 4


def valor_segue_na_tela(tree: UiTree, valor: str, resource_id: str, exato: bool) -> bool:
    """31.61, L2 da revisão do #348: o valor lido ainda está na tela relida, no MESMO elemento (o `resource_id` dele,
    quando tem) e, lido sem trecho, com o texto ou a descrição IGUAL (normalizado). Por contenção, um valor curto ou
    comum ("1", "Sim") casaria em outra tela do mesmo tipo e a etapa fecharia com o valor velho. Com trecho, o valor é
    parte do elemento: basta estar contido nele.

    31.69 (L3 da mesma leitura): o valor CURTO só fecha por aqui vindo de um elemento com `resource_id` que seja o
    ÚNICO com esse id na tela relida. Sem id (Compose, WebView), qualquer outro elemento que seja só "1" casaria; com o
    id repetido (linhas de lista), outra linha com o mesmo valor curto também. Nesses casos a etapa volta ao ator, que
    é só uma volta a mais."""
    n = norm_text(valor)
    if not n:
        return False
    if len(n) < VALOR_CURTO and (not resource_id
                                 or sum(1 for e in tree.elements if e.resource_id == resource_id) != 1):
        return False
    for e in tree.elements:
        if resource_id and e.resource_id != resource_id:
            continue
        if any((t == n) if exato else (n in t) for t in (norm_text(e.text), norm_text(e.desc))):
            return True
    return False


def linha_do_valor_lido(nome: str, valor: str, faltam: Sequence[str]) -> str:
    """A linha do histórico do ator depois de um `read_value` lido da árvore. 31.54: o valor que é URL vai limpo
    (`enderecos_limpos`); a saída da etapa guarda o valor como foi lido, que é o que a pessoa pediu. 31.78: "entregue" e
    o valor entre aspas: "lido: 0" se lia como "nada lido" (hipótese do 29.30, não provada pelo banco)."""
    return (f'read_value({nome}) → lido e entregue: "{enderecos_limpos(valor)[:120]}"'
            + (f"; faltam: {', '.join(faltam)}" if faltam else "; todos os valores da etapa lidos"))


def _arvore_com_endereco_limpo(tree: UiTree, pacote: str | None) -> UiTree:
    """A árvore com a barra de endereço do navegador passada por `endereco_para_o_prompt`. A árvore local (seletores,
    guardas, a conferência do site em `type_secret`) segue com o texto cru; esta é só a que sai daqui."""
    barra = BARRA_DE_ENDERECO.get(pacote or "")
    if barra is None or not any(e.resource_id == barra and e.text for e in tree.elements):
        return tree
    return dataclasses.replace(tree, elements=[
        dataclasses.replace(e, text=endereco_para_o_prompt(e.text)) if e.resource_id == barra and e.text else e
        for e in tree.elements])


def urls_da_pessoa(command: str) -> set[str]:
    """Os endereços que `open_url` abre: SÓ os escritos no comando (o executor acrescenta, por aparelho, os sites das
    contas de portal da persona — `ToolContext.allowed_hosts`). Nem os parâmetros do plano (o planejador pode
    completar "portal MTR" com um domínio que ninguém escreveu), nem `step.variables` (onde mora o `{item}` lido da
    tela). Onde `type_secret` digita é outra pergunta: o `host` da CONTA (ADR-040)."""
    return set(urls_do_texto(command))


def _descricao_da_acao(tree: UiTree, tool: str, args: Mapping[str, object]) -> str:
    """31.327: a ação e o alvo dela, para o motivo da divergência da receita: o `element_id` e o que NÃO é texto da tela
    (classe e limites), sem o texto do elemento (pode ser assunto de e-mail ou nome de pessoa)."""
    id_ = args.get("element_id")
    el = tree.by_id(str(id_)) if id_ is not None else None
    onde = f" {id_} {el.class_name} {list(el.bounds)}" if el is not None else (f" {id_}" if id_ is not None else "")
    return f"{tool}{onde}"


def pede_intervencao_humana(tree: UiTree, *, tem_credencial: bool = False) -> bool:
    """A tela sensível exige uma PESSOA, ou só exige que a imagem não saia daqui?

    Eram a mesma pergunta enquanto `sensitive` significava apenas "há campo de senha" (achado #127). Deixaram de
    ser: uma tela declarada em `config.yaml: sensitive_screens` é sensível, mas parar a etapa nela seria inventar
    uma falha de autenticação e marcar o perfil como `auth_required` toda vez que a IA passasse por ali. Nenhuma
    imagem é omitida por isso (ADR-089), nem na decisão de rotina (31.323: `_motivo_da_imagem` não separa mais a sensível).

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


#: 31.73: a fração mínima da tela que o que cobre ocupa para a recusa do juiz valer como sobreposição. É UMA constante,
#: a do 31.51 (`dialogos.FRACAO_QUE_COBRE`), medida nas duas árvores reais do banner "Abra o app e ganhe frete grátis" do
#: Mercado Livre (720 x 1280): 11,6 % como faixa no topo (r-20261005071303-f24955, o juiz disse que a causa era o
#: conteúdo; a captura de 05/10 08:06Z confirmou os bounds) e 83,8 % como modal (r-20261004190200-5b56e6, cobria).
FRACAO_DA_SOBREPOSICAO = FRACAO_QUE_COBRE

def _area_de(b: tuple[int, int, int, int]) -> int:
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def _contem(fora: tuple[int, int, int, int], dentro: tuple[int, int, int, int]) -> bool:
    return fora[0] <= dentro[0] and fora[1] <= dentro[1] and dentro[2] <= fora[2] and dentro[3] <= fora[3]


def _cruza(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def sobreposicao_vale(tree: UiTree, ref: str | None, largura: int, altura: int) -> bool:
    """31.73: a recusa do juiz por `sobreposicao` vale? Só se julga o elemento que o juiz CITOU e que está na árvore
    (J2 da leitura: a heurística de `cobertura_na_arvore` não decide; sem citado, vale como antes). Sem o tamanho da
    tela, vale (como antes; o `dialogos._cobre_a_tela` responde "não" ali porque lá a pergunta é outra: se há diálogo
    a fechar, e na dúvida a limpeza não falha).

    Vale quando: o citado cobre `FRACAO_DA_SOBREPOSICAO` da tela; ou (J1) o menor elemento com pista de diálogo que o
    CONTÉM cobre (o juiz pode citar o "X" de um modal); ou (J3) outra folha com texto, que não é ancestral nem
    descendente, cruza a área (a faixa fixa sobre o conteúdo, como o aviso de cookies no rodapé sobre a última
    mensagem). Senão é uma faixa no fluxo da página, que não esconde o alvo: na árvore real da f24955, nada da página
    cruza o banner do topo (só os ancestrais, os filhos e 2 px da barra do Chrome, sem texto)."""
    citado = tree.by_id(ref) if ref else None
    if citado is None or largura <= 0 or altura <= 0:
        return True
    tela = largura * altura
    if _area_de(citado.bounds) >= FRACAO_DA_SOBREPOSICAO * tela:
        return True
    caixas = [e for e in tree.elements if e is not citado and _contem(e.bounds, citado.bounds)
              and (_PISTAS_DE_COBERTURA.search(e.class_name or "") or _PISTAS_DE_COBERTURA.search(e.resource_id or ""))]
    caixa = min(caixas, key=lambda e: _area_de(e.bounds)) if caixas else None
    if caixa is not None and _area_de(caixa.bounds) >= FRACAO_DA_SOBREPOSICAO * tela:
        return True
    # L2 da leitura do #391: no diálogo nativo (AlertDialog) o leitor corta o painel (`android:id/parentPanel`, sem
    # texto) e, com o dump só da janela do diálogo, sobram título, mensagem e botões, todos pequenos e sem pista. A
    # JANELA do dump menor que `FRACAO_DA_PAGINA` da tela é uma janela flutuante: o que se vê é o próprio diálogo.
    # L2-a (31.77): a janela é a dos nós de topo do dump (`UiTree.janela`), não a extensão das folhas que sobraram: a
    # página esparsa sem ids (Compose, Flutter) fica com folhas abaixo de 60 % e passaria por janela. Sem a janela (árvore
    # montada fora de `parse_hierarchy`), o L2 não decide: a extensão das folhas é justamente o erro que ele corrige.
    if tree.janela is not None and _area_de(tree.janela) < FRACAO_DA_PAGINA * tela:
        return True
    base = caixa or citado
    area = base.bounds
    # Os descendentes da base, sem a relação de pai na árvore: na ordem do documento (a do uiautomator, em
    # profundidade), eles vêm em sequência logo depois dela, todos contidos na área. Uma folha contida na área FORA dessa
    # sequência é a página por baixo de um aviso fixo (gov.br, 05/10: a folha de cookies de 49 % vem no fim do documento,
    # e "Trabalho…" e "Viagens…" vêm antes dela, inteiras dentro da área).
    ordem = tree.elements
    i = next(k for k, e in enumerate(ordem) if e is base)
    descendentes: set[int] = set()
    for e in ordem[i + 1:]:
        if not _contem(area, e.bounds):
            break
        descendentes.add(id(e))
    for e in ordem:
        if e is citado or e is caixa or id(e) in descendentes or not (e.text or e.desc):
            continue
        if _contem(e.bounds, area):
            continue                                   # ancestral: a página ou a tela
        if any(o is not e and o.bounds != e.bounds and _contem(e.bounds, o.bounds) for o in tree.elements):
            # Não é folha: um contêiner da página. Com os MESMOS bounds não conta (L1 da leitura do #391): no Chrome, o
            # View com texto e o TextView filho com o mesmo texto se conteriam um ao outro e a linha sumiria do J3.
            continue
        if _cruza(e.bounds, area):
            return True
    return False


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
    #: 30.75 (no fim, para não deslocar quem monta por posição): a etapa parou na tela de senha do app (o app pede
    #: login). Na execução de PROVA, o scheduler grava `objectives.blocked_kind='auth'` e o pedido de validação fecha
    #: `app_sem_sessao`, não `sem_evidencia`.
    pede_login: bool = False
    #: 31.278: o app pediu a senha e a persona NÃO tem senha guardada para ele (sem conta ou sem credencial). O aviso ao
    #: dono diz isso, em vez do "pediu um novo login" genérico; o scheduler leva o sinal ao `objective.updated`.
    sem_senha_guardada: bool = False


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
#: 31.52: a `url_bar` FICA no prompt. É onde o ator lê em que página está e onde digita um endereço; sem ela, "abrir a
#: página X" e "estou na página certa?" ficavam às cegas (risco achado na medida do 31.35).
UI_DO_NAVEGADOR: dict[str, frozenset[str]] = {
    "com.android.chrome": frozenset(f"com.android.chrome:id/{i}" for i in (
        "toolbar", "toolbar_container", "toolbar_buttons", "location_bar", "location_bar_status_icon",
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


_IMAGEM_VAI: frozenset[str] = frozenset({"politica_sempre", "alvo_fora_da_arvore", "pedida", "problema",
                                         "primeira_julgada", "primeira_da_leitura", "leitura_pendente", "arvore_pobre"})


def alvo_na_arvore(args: object, tree: UiTree) -> bool:
    """31.232: o alvo do efeito foi escolhido na árvore (um `element_id` que existe nela). Toque por coordenada, elemento
    ausente ou ferramenta sem elemento: fora, e quem confere o efeito no modelo forte recebe a imagem."""
    element_id = getattr(args, "element_id", None)
    return bool(element_id) and tree.by_id(str(element_id)) is not None
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
        #: Revisão do #307: por tentativa, (elemento, nome, valor) que o juiz de PAPEL respondeu "no" — só o "no"; o
        #: "uncertain" é passageiro (página carregando) e pergunta de novo. Some no fim da tentativa (`run_step`).
        self._papel_negado: dict[str, set[tuple[str, str, str]]] = {}
        #: O último veredito do juiz de relação (`_relacao_visual`), para o cache acima distinguir "no" de "uncertain".
        self._ultimo_veredito_de_relacao: str | None = None
        # Pacote "anr": etapas que já gastaram a sua reabertura determinística depois de um ANR. Por ETAPA, não por
        # tentativa — na r-20260928195344-02ee9e cada tentativa acabava pelo prazo e a seguinte reabria de novo. Some
        # no desfecho final da etapa. Memória do processo: reiniciado o backend, a contagem de mortes (que vem do
        # aparelho, desde `steps.started_at`) continua valendo e o pior caso é UMA reabertura a mais.
        self._reabertas_por_anr: set[str] = set()
        # LT-12: a última ação decidida em cada etapa, como (tela estrutural, ação) — a da tentativa anterior é o que a
        # retentativa NÃO repete no modelo barato. Some no desfecho final. Memória do processo: reiniciado o backend, a
        # retentativa começa no tier 0 sem este gatilho (os outros — erros seguidos, ciclo, efeito — seguem valendo).
        self._ultima_acao_da_etapa: dict[str, tuple[str, str]] = {}
        #: 31.59: por etapa, quantas mensagens com o texto IGUAL ao `content` a tela tinha no toque do efeito.
        self._mensagens_antes: dict[str, int] = {}
        #: 31.250: por etapa SEM capability (plano livre, o QA Messenger), o texto do último `type_text`: é o `content` que
        #: a etapa livre não tem nos argumentos. Só memória: nunca vai a log, evento nem prompt; `type_secret` não entra.
        self._textos_digitados: dict[str, str] = {}
        # Item 31.24 (C-4): o juiz e a evidência de cada tentativa EM CURSO, somados enquanto ela roda e gravados uma
        # vez no fim (`_registrar_estrategia`). Some no fim da tentativa, saia ela como sair.
        self._tempos_da_tentativa: dict[str, TemposDaTentativa] = {}
        #: 31.230: por tentativa, a ação da IA (id) → a tela antes dela era o estado conhecido do app? Lida pela
        #: destilação no fim da tentativa; só a anotação, nunca a tela.
        self._em_casa_antes: dict[str, dict[int, bool]] = {}
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

    async def _marcas_depois_do_efeito(self, rt: DeviceRuntime, cap: Capability, step: StepDTO,
                                       obs: Observation | None, conta: str | None, call_timeout: float
                                       ) -> tuple[list[str], Observation | None]:
        """29.79 (d): as marcas exigidas (`commit_switch_mark`) que NÃO aparecem junto do nome da conta depois do efeito,
        e a última tela lida (a evidência). Uma leitura (a última tela da verificação, ou uma nova se ela não existir) e
        no máximo uma releitura depois de `ESPERA_DA_MARCA_S`. Leitura que falha é dúvida: a marca conta como ausente."""
        exigidas = marcas_exigidas(cap.commit_switch_mark, self._argumentos_da_guarda(cap, step))
        if not exigidas:
            return [], obs
        for releitura in (False, True):
            if releitura:
                await asyncio.sleep(ESPERA_DA_MARCA_S)
            if obs is None or releitura:
                try:
                    obs = await self.devices.observe(rt, timeout=call_timeout, imagem=False)
                except DriverError:
                    obs = None
                    continue
            if all(marca_junto_da_conta(obs.tree, m, conta) for m in exigidas):
                return [], obs
        return exigidas, obs

    def _argumentos_da_guarda(self, cap: Capability, step: StepDTO) -> dict[str, str]:
        """29.79, revisão R1: os argumentos que `rejeicao_do_interruptor` lê. O `rotulo_ia` vem da ORIGEM da imagem
        (`rotulo_ia_exigido`), não do que a etapa gravou: a etapa antiga ou com a imagem resolvida depois não o tem, e
        em dúvida o Share exige o rótulo ligado."""
        argumentos = {k: str(v) for k, v in (step.bindings or {}).items()}
        if any(e.partition(":")[0].strip() == ARGUMENTO_DO_ROTULO_IA for e in cap.commit_switch):
            argumentos[ARGUMENTO_DO_ROTULO_IA] = "true" if rotulo_ia_exigido(self.repo.db, argumentos) else "false"
        return argumentos

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
        # 31.103 (A1 da leitura): a barra de VERDADE, fora do trecho da página. O primeiro nó com o id e com texto podia
        # ser um elemento da página de outro domínio com o host da conta, e a senha seria digitada nela. Sem barra
        # fora da página, o host é vazio e nada é digitado.
        host = _host(texto_da_barra(await observe(), {barra}))
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
        exploratoria = step_id is not None and self._etapa_exploratoria(step_id)
        if (step_id is not None and objective_id is not None and self.cfg.file.ai.step_budget.enabled
                and not exploratoria):
            # 31.331: a etapa exploratória tem o teto PRÓPRIO (`_teto_da_exploracao`); o normal medido de uma etapa livre (3 a 8
            # chamadas, parava em 16) a derrubou na prova do P-046 antes de ela achar a linha da mensagem.
            self._conferir_orcamento_da_etapa(run_id, objective_id, step_id, role, run["app_ids"] if run else None,
                                              attempt_id, marca)
        if exploratoria and step_id is not None and (parada := self._teto_da_exploracao(run_id, step_id, s)) is not None:
            exc = AIError(parada, kind="budget")
            self._registrar_orcamento_estourado(run_id, objective_id, step_id, role, exc, attempt_id, marca)
            raise exc
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

    def _etapa_exploratoria(self, step_id: str) -> bool:
        """31.331: a etapa é exploratória (`steps.exploratoria`)? Uma leitura de uma linha."""
        marca = self.repo.db.one("SELECT exploratoria FROM steps WHERE id=?", (step_id,))
        return marca is not None and bool(marca["exploratoria"])

    def _teto_da_exploracao(self, run_id: str, step_id: str, s: LimitsCfg) -> str | None:
        """Item 31.273 (ADR-084): a etapa exploratória para quando as chamadas de IA das etapas exploratórias da execução
        chegam a `exploracao_max_chamadas_ia`, ou o gasto delas (tokens x preço) chega a `exploracao_max_usd`. Devolve a
        frase do motivo (o que foi gasto e o que vale), ou `None`. A etapa que não é exploratória não paga nada aqui
        além de uma leitura de uma linha.

        31.331 (prova do P-046: US$ 0,1075 contra 0,10 e US$ 0,2615 contra 0,25): o gasto conta SÓ as chamadas das etapas
        exploratórias (o planejamento, que sempre vem antes, comia 40 % do teto de US$ 0,10) e a conferência olha a
        PRÓXIMA chamada: o gasto mais a média das já feitas (estimativa; a 1ª chamada não tem média) tem de caber no teto."""
        if not self._etapa_exploratoria(step_id):
            return None
        feitas = int(self.repo.db.scalar(
            "SELECT COUNT(*) FROM ai_calls WHERE run_id=? AND step_id IN"
            " (SELECT id FROM steps WHERE run_id=? AND exploratoria=1)", (run_id, run_id)) or 0)
        if feitas >= int(s.exploracao_max_chamadas_ia):
            return (f"Teto da exploração: {feitas} chamadas de IA (limite {int(s.exploracao_max_chamadas_ia)}). "
                    "Parei sem concluir; o que vi está nas ações da etapa.")
        teto_usd = float(s.exploracao_max_usd)
        if teto_usd > 0:
            gasto = costs.spent_usd(self.repo.db, self.cfg.file.ai.prices, run_id=run_id, so_exploratorias=True)
            proxima = gasto / feitas if feitas else 0.0
            if gasto + proxima >= teto_usd:
                return (f"Teto da exploração: US$ {gasto:.2f} gastos na exploração e a próxima chamada custaria cerca de "
                        f"US$ {proxima:.2f} (limite US$ {teto_usd:.2f}). Parei sem concluir; o que vi está nas ações da etapa.")
        return None

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
                    ai: AiCfg | None = None, le_valor: bool = False, falta_saida: bool = False,
                    alvo_fora: bool = False) -> bool:
        """Política `ai.image_policy`: se a imagem vai junto. O porquê é `_motivo_da_imagem`, a única régua."""
        return self._motivo_da_imagem(tree, judged_step=judged_step, first=first, trouble=trouble,
                                      requested=requested, ai=ai, le_valor=le_valor,
                                      falta_saida=falta_saida, alvo_fora=alvo_fora) in _IMAGEM_VAI

    def _motivo_da_imagem(self, tree: UiTree, *, judged_step: bool, first: bool, trouble: bool,
                          requested: bool, ai: AiCfg | None = None, le_valor: bool = False,
                          falta_saida: bool = False, alvo_fora: bool = False) -> MotivoDaImagem:
        """Política `ai.image_policy`. A imagem custa ~1/3 dos tokens novos de cada chamada; a hierarquia quase sempre
        basta. Em `auto` a imagem vai quando a árvore é pobre (WebView/canvas), na 1ª decisão de etapa julgada por
        visão, depois de erro/ciclo, ou quando o próprio modelo pede (observe_screen.need_image).

        Decide pela ÁRVORE, antes de a imagem existir (adendo v0.20, C1): o resto do que pesa aqui já se sabe antes
        de observar, então a imagem só é adquirida quando vai ser mandada. RA-10: devolve o MOTIVO (`ai_calls.
        image_reason`), na ordem em que a regra decide; vai junto quando ele está em `_IMAGEM_VAI`. O motivo `sensivel` já não
        é produzido (31.323); só as linhas antigas de `ai_calls` o trazem.
        `ai`: o bloco da execução (17.14, `_ai_da_execucao`); vazio = o global. `alvo_fora` (31.232): esta chamada é do
        modelo forte que confere um efeito cujo alvo não está na árvore (`alvo_na_arvore`)."""
        ai = ai or self.cfg.file.ai
        # 31.323 (P-044): a tela sensível segue a mesma régua das outras. Já foi `return "sensivel"` aqui, sem imagem; com o
        # ADR-089 (a plataforma não esconde tela, e o Android mascara o campo de senha) isso só deixava a IA às cegas em
        # login e verificação. `ai.image_policy = never` continua valendo para toda tela, a sensível inclusive.
        if ai.image_policy == "never":
            return "politica_nunca"
        if ai.image_policy == "always":
            return "politica_sempre"
        if alvo_fora and ai.imagem_quando_alvo_fora_da_arvore:
            # 31.232: o toque por coordenada que o forte confere sem a imagem é às cegas: a árvore não diz o que há ali.
            return "alvo_fora_da_arvore"
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
        if falta_saida and ai.imagem_enquanto_falta_saida:
            # Item 31.71: enquanto faltar saída declarada, a imagem segue em toda decisão. Vem DEPOIS de tudo acima: a
            # política e a tela sensível mandam, e o motivo que já se grava hoje não muda de nome.
            return "leitura_pendente"
        informative = sum(1 for e in tree.elements if e.text or e.desc or e.clickable or e.editable)
        return "arvore_pobre" if informative < ai.rich_tree_min_elements else "arvore_rica"

    def _aceite_do_toque(self, tool_ctx: ToolContext, args: object, tree: UiTree, ai: AiCfg) -> UiElement | None:
        """31.72: o elemento cujo toque do ator aceitaria um aviso de consentimento (`dialogos.toque_que_aceita`), pelo
        PONTO tocado (o do `resolve_point`, também por coordenada); `None` se o gesto pode seguir. No `drag`, o ponto de
        INÍCIO e o de FIM: um arrasto curto dentro do botão é um toque nele. No `type_text` com `element_id`, o toque que
        ele dá no elemento antes de escrever. Um host em `ai.consentimento_aceito_em` (vazia por padrão; preenchê-la é
        decisão do dono) libera o aceite ali."""
        if isinstance(args, Drag):
            pontos = [(None, args.from_x, args.from_y), (None, args.to_x, args.to_y)]
        else:
            pontos = [(getattr(args, "element_id", None), getattr(args, "x", None), getattr(args, "y", None))]
        alvos: list[tuple[UiElement | None, tuple[int, int]]] = []
        for element_id, x, y in pontos:
            try:
                px, py, el = resolve_point(tool_ctx, element_id, x, y)
            except DriverError:
                continue                               # o gesto falharia na execução, como antes
            alvos.append((el, (px, py)))
        if ai.consentimento_aceito_em:
            # 31.103: a barra de verdade, fora do trecho da página; a página com o id da barra não escolhe o host.
            host = _host(texto_da_barra(tree, set(BARRA_DE_ENDERECO.values())))
            if host and any(host == h or host.endswith("." + h) for h in map(str.casefold, ai.consentimento_aceito_em)):
                return None
        tela = (tool_ctx.width, tool_ctx.height)        # 31.104: a terceira medida da tela, vale mesmo sem janela
        return next((r for el, ponto in alvos if (r := toque_que_aceita(tree, el, ponto, tela)) is not None), None)

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
        rep = rr.replayer
        rr.antes_da_comparacao = (rep.idx, rep.scrolls, rep.done_actions, rr.diverged, rr.partida_diferente)
        try:
            would = rr.replayer.next(obs.tree)
        except AlvoAusente as exc:
            if rr.replayer.idx == 0 and rr.replayer.scrolls == 0:
                # 31.262: o alvo da AÇÃO 1 não está na tela de partida (sem a receita ter rolado com a IA até aqui) (a folha de comentários que a operação anterior
                # deixou aberta, o app fechado): a receita não se aplica aqui, e a IA começar por outro lugar não é
                # caminho diferente do dela. Como o 30.80 na reprodução: sem comparação, sem veredito nesta execução.
                rr.partida_diferente = True
                return
            rr.diverged = str(exc)
            return
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
            # 31.327: o alvo é o que recebe o toque (`mesmo_alvo`), não o id: o rótulo da receita e a linha clicável da IA
            # são ids diferentes para a mesma ação (P-043, "Junk").
            agreed = (would.tool == decision.tool
                      and mesmo_alvo(obs.tree, would.args.get("element_id"), decision.args.get("element_id"))
                      and (would.tool != "type_text" or would.args.get("text") == decision.args.get("text")))
        if not agreed:
            rr.diverged = ("a IA escolheu outra ação (IA: " + _descricao_da_acao(obs.tree, decision.tool, decision.args)
                           + "; receita: " + (_descricao_da_acao(obs.tree, would.tool, would.args) if would is not None
                                              else "nada, a receita acabou") + ")")

    @staticmethod
    def _desfazer_comparacao(rr: "_RecipeRun") -> None:
        """31.287: a decisão que acabou de ser comparada com a receita foi DESCARTADA pelo executor (o commit do modelo de
        ação sobe ao forte, 31.223; ou a repetição da tentativa anterior, LT-12) e vai ser refeita. Comparar as duas
        gastava a ação da receita na primeira: a refeita (a MESMA ação) via a receita esgotada e contava como divergência
        (`a IA escolheu outra ação`), em todo FOLLOW — a receita candidata nunca somava prova. Devolve o cursor e os sinais
        ao estado de antes, e a comparação que vale é a da decisão que de fato agir."""
        if rr.replayer is None or rr.antes_da_comparacao is None:
            return
        rr.replayer.idx, rr.replayer.scrolls, rr.replayer.done_actions, rr.diverged, rr.partida_diferente = (
            rr.antes_da_comparacao)
        rr.antes_da_comparacao = None

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
        if jpeg and (ai or self.cfg.file.ai).tapar_barra_de_endereco and (obs.package or "") in BARRA_DE_ENDERECO:
            # 31.54: a barra do Chrome mostra a URL inteira; tapada na imagem, o ator lê o endereço pela árvore limpa
            tapada = imagem_com_barra_tapada(jpeg, obs.tree, obs.package, obs.width, obs.height)
            if tapada is None:
                metricas.contar("executor.barra_tapada", resultado="sem_bounds")
                log.info("31.54: a barra de endereço não tem bounds na árvore; a imagem vai como está")
            else:
                metricas.contar("executor.barra_tapada", resultado="tapada")
                jpeg = tapada
        ocultar = (UI_DO_NAVEGADOR.get(obs.package or "", frozenset())
                   if (ai or self.cfg.file.ai).podar_ui_do_navegador else frozenset())
        podados = sum(1 for e in obs.tree.elements if e.resource_id in ocultar) if ocultar else 0
        # 31.52: a `url_bar` fica no prompt, mas sem query, fragmento e pedaço opaco do caminho
        lines = _arvore_com_endereco_limpo(obs.tree, obs.package).prompt_lines(
            self.cfg.file.ai.max_hierarchy_elements, scale, protect=protect, boost=boost, ocultar=ocultar)
        return ScreenInput(width=w, height=h, jpeg=jpeg, elements=lines, package=obs.package,
                           tree=obs.tree, podados=podados), scale

    async def _arvore_antes_da_poda(self, obs: Observation, podados: int, *, run_id: str, iid: str, step_id: str,
                                    attempt_id: str) -> None:
        """31.52, diagnóstico DESLIGADO por padrão: grava, como evidência `hierarchy` (JSON), a árvore do navegador
        ANTES da poda do 31.35, só nos aparelhos de `ai.diagnostico_arvore_aparelhos` (os de teste que o dono listar) e
        nunca de tela sensível. Sem ela, o A/B offline da poda era impossível: só os números depois dela ficavam em
        `ai_calls`. Texto e descrição passam pela redação de segredos. Falhar ao gravar não muda a etapa.

        Aparelho com conta real (vínculo ativo de persona, a regra do ADR-055) nunca grava, mesmo listado: o vínculo
        mora no banco, não no config, por isso a recusa é aqui, na hora de gravar, e não na carga do config."""
        if tem_vinculo_ativo(self.repo.db, iid):
            log.warning("%s: diagnóstico 31.52 recusado, o aparelho tem conta real vinculada", iid)
            return
        corpo = {"regra": "31.52", "package": obs.package, "width": obs.width, "height": obs.height, "podados": podados,
                 "elements": [{**e.to_dict(), "text": redact(e.text) or "", "desc": redact(e.desc) or ""}
                              for e in _arvore_com_endereco_limpo(obs.tree, obs.package).elements]}
        try:
            await self.repo.add_evidence_async(
                run_id=run_id, instance_id=iid, step_id=step_id, attempt_id=attempt_id, kind="hierarchy",
                note=f"31.52: árvore do navegador antes da poda ({podados} podado(s)); diagnóstico",
                data=json.dumps(corpo, ensure_ascii=False).encode("utf-8"), ext="json")
        except Exception:  # noqa: BLE001 - diagnóstico nunca derruba a etapa
            log.exception("%s: não foi possível gravar a árvore antes da poda", iid)

    # ------------------------------------------------------------------ etapa
    async def run_step(self, *, run: Any, objective: Any, step: StepDTO, attempt_id: str, rt: DeviceRuntime,
                       app: AppContext, account_label: str | None, remaining: list[str],
                       stop_reason: Callable[[], str | None], resumed_after_manual: bool) -> StepOutcome:
        """Etapa com receitas: procura a receita, executa, e depois contabiliza o replay ou aprende com a IA."""
        # 31.113 F2: o ponto ÚNICO onde o dado da persona entra na etapa. A linha guarda o marcador; daqui em diante
        # (ator, receita, conferência da tela, juiz) a etapa da tentativa tem o valor, só em memória.
        # F3: a mesma persona da porta (com o fallback do aparelho de persona única), também para os `bindings`.
        persona = self.repo.variaveis_da_persona(self.repo.persona_do_objetivo(objective["profile_id"],
                                                                               str(objective["instance_id"])))
        step = resolver_persona(step, persona)
        if faltam := sem_valor_na_etapa(step):
            # O pré-voo conferiu na materialização; o dado sumiu da persona depois. O molde não vai ao aparelho.
            return StepOutcome(Outcome.waiting_user, f"{PREFIXO_DADO_AUSENTE} a persona deste aparelho não tem "
                               + ", ".join(rotulo(n) for n in faltam) + "; nada foi digitado.",
                               needs="Preencha o dado no perfil da persona e retome o item.")
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
                # 31.244: os NOMES das variáveis da persona (sem valor), para a destilação aceitar o marcador dela que o
                # registro gravou no `type_text`; a reprodução o resolve com `{**persona, **rr.variables}` (abaixo).
                rr.persona = frozenset(persona)
                rr.signature = self._installed_signature(rt.id, app.package)
                rr.variant = await self.devices.variant_of(rt)
                rr.row = self.recipes.find(app.package, rr.app_version, rr.step_hash,
                                           signature=rr.signature, variant=rr.variant,
                                           step_hash_generico=rr.step_hash_generico,
                                           # 30.81: a receita ensinada em espera de prova só vale para quem ensinou
                                           persona=self.repo.persona_do_objetivo(objective["profile_id"], rt.id),
                                           # 31.249: o escopo do alvo desta etapa (conta da persona ou de terceiro)
                                           escopo=escopo_do_alvo(step.bindings or {},
                                                                 [v for n, v in persona.items()
                                                                  if USUARIO_DA_CONTA.match(n)]
                                                                 + ([account_label] if account_label else [])),
                                           prova_fluxo=run["prova_fluxo_id"])
                if rr.row is not None:
                    # 31.87 F2: a receita ensinada digita `{perfil_email}`; os dados da persona do objetivo entram
                    # só na REPRODUÇÃO (os do objetivo vencem). A destilação na execução segue com `rr.variables`.
                    rr.replayer = self.recipes.replayer(rr.row, {**persona, **rr.variables})
                    rr.replayer.em_casa = self._conferidor_de_casa(app.package)       # 31.230: a âncora
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
            self._papel_negado.pop(attempt_id, None)
            if not fechada:
                # Saiu por exceção (o scheduler a transforma em falha): o aprendizado sabe da tentativa do mesmo jeito,
                # uma vez, e sem desfecho que pareça sucesso.
                self._fechar_tentativa(run, objective, step, attempt_id, rt, app, rr, None)
        if outcome.outcome in (Outcome.succeeded, Outcome.failed, Outcome.uncertain, Outcome.cancelled):
            self._reabertas_por_anr.discard(step.id)     # desfecho final: a etapa não volta a rodar com este id
            self._ultima_acao_da_etapa.pop(step.id, None)
            self._digitados().pop(step.id, None)
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
    def _reservar_excecao(self, step: StepDTO) -> str | None:
        """30.65: reserva (UPDATE condicional) a exceção de política presa a esta etapa, logo antes do gesto. `None`
        segue; um texto é o motivo literal para falhar fechado. A porta não roda de novo no meio da etapa, e o
        `open_effect` nem sempre roda (ação sem `interaction_type`, objetivo sem perfil): a reserva é o que garante que
        a revogada, a recusada e a vencida não saem, e que a rota de revogar não responde 200 com o efeito em voo. Se
        a própria reserva falhar, o efeito também não sai."""
        excecoes = getattr(self.social, "excecoes", None)
        if excecoes is None:
            return None
        try:
            return excecoes.reservar(step.id)
        except Exception:  # noqa: BLE001 - na dúvida, a exceção não autoriza nada
            log.exception("30.65: a reserva da exceção da etapa %s falhou", step.id)
            return "não foi possível reservar a exceção à regra de uma conta por alvo; o efeito não foi disparado (30.65)"

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
        # plano também não é veredito sobre ela. Nem a espera por uma pessoa (30.70): aviso do app, autenticação,
        # conta errada ou falta de informação param a etapa por um motivo que não é da receita — contá-la como falha
        # punha uma receita boa em quarentena e gravava evidência contra ela no aprendizado. A etapa retomada que
        # terminar dá o veredito de verdade.
        veredito = not (outcome.plan_defect or outcome.outcome in (Outcome.retry, Outcome.waiting_user)
                        or outcome.trava_da_conta)
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
        if na_receita and ok and rr.partida_diferente and not replayed:
            # 30.80: a tela de partida era outra (a ação 1 não achou o alvo) e a IA comprovou a etapa: a receita não se
            # aplicou, e isso não é veredito sobre ela. Quem conduziu foi a IA (`ai`: nenhuma ação da receita rodou), e
            # o aprendizado não lê evidência contra. A N-ésima seguida conta como falha comum (`nao_aplicavel`).
            contou, quarantined = self.recipes.nao_aplicavel(rr.row["id"])
            repo.db.execute("UPDATE steps SET driven_by=? WHERE id=?", ("recipe+ai" if contou else "ai", step.id))
            texto = (f"{iid} · {step.title}: receita v{rr.row['version']} não se aplicou: tela de partida diferente"
                     + (f" ({NAO_APLICAVEL_CONTA_APOS}ª seguida: conta como falha da receita)" if contou
                        else "; não conta como falha da receita"))
            # O código estável (`kind`) e os ids deixam a contagem sem ler o texto: a receita ensinada tentada que não
            # servia naquela tela aparece já na 1ª vez, não só na quarentena.
            repo.bus.emit("decision", texto, run_id=run_id, instance_id=iid, step_id=step.id,
                          data={"text": texto, "kind": "receita_nao_aplicavel", "recipe_id": int(rr.row["id"]),
                                "step_id": step.id, "contou_como_falha": contou})
            if quarantined:
                repo.decision(f"{iid} · {step.title}: receita v{rr.row['version']} em quarentena após falhas seguidas; "
                              "a etapa será reaprendida com a IA", run_id=run_id, instance_id=iid, step_id=step.id)
            return
        da_divergencia = False             # 31.233: a ativa que divergiu agora ensina a candidata (abaixo)
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
            # 31.233: a ativa divergiu, caiu em quarentena agora e a IA completou a etapa. A chave ficou livre (só a
            # quarentena tira a ativa) e o caminho que rodou nesta tentativa vira candidata, em vez de se perder.
            da_divergencia = (quarantined and ok and rr.diverged and not sem_ator
                              and self.cfg.file.ai.candidata_da_ativa_que_divergiu)
            if not da_divergencia:
                return
        if sem_ator and not da_divergencia:
            # Nenhuma ação foi feita: não há caminho a aprender (receita vazia) e quem conduziu não foi a IA.
            repo.db.execute("UPDATE steps SET driven_by='sem_ator' WHERE id=?", (step.id,))
            return
        if not da_divergencia:
            repo.db.execute("UPDATE steps SET driven_by='ai' WHERE id=?", (step.id,))
        # A candidata que divergiu é trocada pelo caminho que a IA acabou de comprovar: sem isto, uma IA que passou a
        # fazer outro caminho deixaria a etapa presa para sempre numa candidata que nunca concorda (e candidata não
        # reproduz, então nem a quarentena a tiraria dali).
        substitui = (rr.row["id"] if rr.row is not None and rr.row["status"] == "candidate" and rr.diverged
                     and not da_divergencia else None)
        if (not ok or (rr.row is not None and substitui is None and not da_divergencia)
                or not (app.package and rr.app_version and rr.step_hash)):
            return
        rows = repo.db.query("SELECT * FROM actions WHERE attempt_id=? ORDER BY seq", (attempt_id,))
        actions, why = distill(rows, rr.variables, em_casa_antes=self._em_casa_antes.pop(attempt_id, None),
                               com_trecho_da_receita=da_divergencia, persona=rr.persona,
                               exploratoria=bool(step.exploratoria))
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
            no_lugar = (f", no lugar da v{rr.row['version']}, que divergiu" if substitui
                        else f", a partir da v{rr.row['version']} ativa, que divergiu e foi à quarentena (31.233)"
                        if da_divergencia and rr.row is not None else "")
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
        conclusiva (a etapa falhou antes, ou o aparelho já estava no estado final) não há veredito.

        31.262: a candidata que não se aplicou na tela de partida (`partida_diferente`) e a etapa comprovada pela IA
        também não têm veredito: nem a prova zera, nem o caminho da IA a substitui (`_after_step` só troca a que
        divergiu). A `NAO_APLICAVEL_CONTA_APOS`-ésima seguida conta como divergência, como na reprodução."""
        rep = rr.replayer
        if rr.partida_diferente and not rr.diverged:
            if not ok:
                return
            if not self.recipes.nao_aplicavel_em_prova(rr.row["id"]):
                texto = (f"{iid} · {step.title}: receita v{rr.row['version']} em prova não se aplicou: tela de partida "
                         "diferente; não conta contra ela, e a prova continua")
                self.repo.bus.emit("decision", texto, run_id=run_id, instance_id=iid, step_id=step.id,
                                   data={"text": texto, "kind": "receita_nao_aplicavel", "recipe_id": int(rr.row["id"]),
                                         "step_id": step.id, "contou_como_falha": False, "em_prova": True})
                return
            rr.diverged = f"tela de partida diferente {NAO_APLICAVEL_CONTA_APOS} vezes seguidas"
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
        if rr.diverged and rr.row["status"] == "candidate":
            # 31.287: a divergência da candidata diz POR QUÊ (antes só a contagem zerava e ninguém sabia o motivo).
            self.repo.decision(f"{iid} · {step.title}: receita v{rr.row['version']} em prova divergiu: {rr.diverged}; "
                               "a prova recomeça", run_id=run_id, instance_id=iid, step_id=step.id)
        promovida = self.recipes.shadow(rr.row["id"], concordou, promote_after=self.cfg.file.ai.recipes_promote_after,
                                        promote_after_com_efeito=self.cfg.file.ai.recipes_promote_after_com_efeito,
                                        simulada=self._origem_simulada(run_id))
        if promovida:
            self.repo.decision(f"{iid} · {step.title}: receita v{rr.row['version']} promovida a ativa — a IA fez "
                               "exatamente o caminho dela em execuções seguidas; as próximas execuções desta etapa "
                               "dispensam a IA enquanto a tela casar", run_id=run_id, instance_id=iid, step_id=step.id)

    def _em_casa(self, pacote: str | None, arvore: UiTree) -> bool | None:
        """31.230: a tela é o estado conhecido DECLARADO do app (`telas.yaml`, `estado_conhecido.telas`)? `None` sem
        conhecimento do app. As telas aprendidas ficam de fora de propósito (dependem do modo do livro)."""
        if not pacote:
            return None
        k = telas_do_app.da_pasta(CONHECIMENTO_DE_APPS / pacote)
        if k is None:
            return None
        frente = next((p for p in arvore.packages if p != "com.android.systemui"), None)
        return k.em_casa(telas_do_app.classificar(k, arvore, package=frente).tela)

    async def _partir_do_estado_conhecido(self, rt: DeviceRuntime, app: AppContext, *, run_id: str, iid: str,
                                          step: StepDTO, call_timeout: float) -> list[str]:
        """31.327: a etapa de EXPLORAÇÃO parte do estado conhecido do app, não de onde o app estava.

        A receita que ela ensina é o caminho desde a 1ª ação da IA; partindo de uma tela que ninguém escolheu (a gaveta
        aberta, a pasta da vez anterior) ela só servia nessa tela e respondia `nao_aplicavel` em qualquer outra (prova real do
        P-043, 10/10). Aqui o app volta ao estado declarado em `telas.yaml` (`voltar_ao_estado_conhecido`: só "voltar" e,
        no máximo uma vez, reabrir o app, sem efeito externo) ANTES da 1ª decisão, na IA e na reprodução. Sem estado
        conhecido declarado, ou com qualquer falha, nada muda: a etapa segue de onde está (e a receita não nasce, ver
        `distill`). Devolve os passos dados (`[]` = já estava lá, ou não se sabe)."""
        pacote = app.package
        if not pacote:
            return []
        k = telas_do_app.da_pasta(CONHECIMENTO_DE_APPS / pacote)
        if k is None:
            return []

        async def observar() -> tuple[UiTree, str | None]:
            obs = await self.devices.observe(rt, timeout=call_timeout, imagem=False)
            return obs.tree, next((p for p in obs.tree.packages if p != "com.android.systemui"), None)

        async def voltar() -> None:
            await rt.executor.run(rt.io.press_key, "back", timeout=30, label="voltar")
            await asyncio.sleep(float(self.cfg.file.ai.action_settle_s))

        async def reabrir() -> None:
            await rt.executor.run(rt.io.open_app, pacote, app.activity, timeout=60, label="abrir o app")
            await asyncio.sleep(float(self.cfg.file.ai.action_settle_s))

        try:
            _tree, _pkg, _estado, passos = await telas_do_app.voltar_ao_estado_conhecido(
                k, observar=observar, voltar=voltar, reabrir=reabrir,
                reconhecer=lambda t, p: telas_do_app.classificar(k, t, package=p))
        except Exception:  # noqa: BLE001 - preparar a partida é otimização da receita; a etapa segue de onde está
            log.exception("%s: não foi possível levar %s ao estado conhecido antes de explorar", iid, pacote)
            return []
        if passos:
            self.repo.decision(f"{iid} · {step.title}: o app voltou ao estado conhecido antes de explorar ("
                               f"{', '.join(passos)}), para a receita partir de um ponto que se repete",
                               run_id=run_id, instance_id=iid, step_id=step.id)
        return passos

    def _anotar_casa(self, attempt_id: str, aid: int, pacote: str | None, arvore: UiTree) -> None:
        """31.230: anota, para a destilação, se a ação `aid` da IA partiu do estado conhecido. Anotação nunca derruba
        a etapa; sem conhecimento do app, nada é anotado (e o voltar inicial segue recusado)."""
        try:
            casa = self._em_casa(pacote, arvore)
        except Exception:  # noqa: BLE001 - a anotação é otimização da receita
            log.exception("estado conhecido não conferido para a receita (%s)", pacote)
            return
        if casa is None:
            return
        if len(self._em_casa_antes) > 256 and attempt_id not in self._em_casa_antes:   # a tentativa que não destilou
            self._em_casa_antes.pop(next(iter(self._em_casa_antes)))
        self._em_casa_antes.setdefault(attempt_id, {})[aid] = casa

    def _conferidor_de_casa(self, pacote: str | None) -> Callable[[UiTree], bool]:
        """31.230: o que a reprodução usa para conferir a âncora: só `True` quando o app declara e a tela é ele."""
        return lambda arvore: bool(self._em_casa(pacote, arvore))

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
        # conta (android-13 é também do Beltrano): é pertencer ao aparelho, não ser o único dele.
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
        # 31.61 L2: de onde cada valor foi lido (o `resource_id` do elemento e se foi o texto inteiro, sem trecho), para o
        # fecho sem `step_done` conferir o MESMO elemento na tela relida.
        origem_dos_lidos: dict[str, tuple[str, bool]] = {}
        # Item 12.5 (ADR-070): as saídas lidas da IMAGEM (nome → (leitor, sha256 do recorte, id da evidência)); as
        # tentativas visuais já feitas nesta tentativa da etapa (barreira `repetida`); e a conta PRÓPRIA das recusas da
        # barreira de saídas, que `observe_screen` e `find_element` não zeram (`errors_in_row` zera): com 4, a etapa vai
        # para `fail_or_retry`. Sem ela o ator alternava recusa e observação sem nunca esbarrar no limite (r-…-178742).
        visuais: dict[str, tuple[str, str, int | None]] = {}
        tentativas_visuais: set[ChaveDeTentativa] = set()
        # 29.49: a recusa determinística de cada par (tela, âncora) já lido; reler o par vira `repetida` definitiva.
        recusas_visuais: dict[ChaveDeTentativa, str] = {}
        recusas_de_saida = 0
        # 31.78: releituras de um nome já lido, com o MESMO valor e nada faltando. O ator do 29.30 releu 12 vezes o "0" da
        # contagem sem chamar `step_done` (r-…-701173); na 1ª o ator é avisado de que é hora de concluir, na 2ª a etapa
        # vai à verificação. Só uma leitura de valor DIFERENTE zera a conta (a tela pode ter mudado); observar, rolar ou
        # tocar entre as leituras não zera: quem decide se a tela ainda prova é o juiz da verificação.
        releituras_iguais = 0
        relido: str | None = None           # o nome do aviso, que vai só na cópia da decisão do ator (não ao juiz)
        # 31.78 (C1 da leitura do #413): os nomes cuja ÚLTIMA leitura diverge da anterior. No teto, valor divergente não
        # vai à verificação: o juiz julga "a contagem foi lida", não qual valor é o certo, e a saída é a base da prova.
        divergentes: set[str] = set()

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

        #: 31.230: a árvore da última observação de decisão (a tela de onde parte a próxima ação da IA)
        arvore_da_decisao: list[UiTree | None] = [None]

        def intencao(tool: str, args: dict[str, object], rationale: str | None, *, side_effect: bool,
                     source: str = "ai") -> int:
            aid = repo.log_intent(attempt_id, tool, args, rationale, side_effect=side_effect, source=source,
                                  ai_call_id=chamada_do_ator)
            if source == "ai" and arvore_da_decisao[0] is not None:
                self._anotar_casa(attempt_id, aid, app.package, arvore_da_decisao[0])     # 31.230
            return aid

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
            if data is None and obs.image_omitted in ("policy", "capture_failed"):
                # A observação saiu só com a árvore (a imagem não ia ao modelo, ou a captura falhou e o 31.76 tolerou).
                # A evidência adquire a SUA, agora, com o próprio horário na nota — é só evidência, nunca fonte de
                # coordenada (adendo v0.20, C1).
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
            if data is None:
                motivo = "captura da tela falhou" if obs.image_omitted == "capture_failed" else "imagem ausente"
                await repo.add_evidence_async(run_id=run_id, instance_id=iid, step_id=step.id, attempt_id=attempt_id,
                                              kind="text", note=note + f" ({motivo})")
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

        def recusas_da_trava() -> int:
            """31.72, N8 e K3 da leitura do #386: o `errors_in_row` zera em qualquer ação bem-sucedida, então alternar
            o aceite com `observe_screen` nunca chegaria a 4. Contam as recusas da trava acumuladas na ETAPA (em todas
            as tentativas dela; uma etapa nova começa do zero): o toque de aceite e o `type_text` fora de campo (B1),
            que é o mesmo aceite por outro caminho."""
            return int(repo.db.scalar(
                "SELECT COUNT(*) FROM actions a JOIN attempts t ON t.id = a.attempt_id WHERE t.step_id = ? "
                "AND a.status = 'rejected' AND (a.error LIKE ? OR a.error LIKE ?)",
                (step.id, MOTIVO_ACEITE_RECUSADO + "%", REJEICAO_TYPE_TEXT_FORA_DE_CAMPO + "%")) or 0)

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
        falhas_de_captura = 0              # 31.76: capturas seguidas que falharam com a árvore já lida (zera com imagem)
        declared: StepDone | None = None
        max_actions = int(s.max_actions_per_step)
        if step.exploratoria:              # item 31.273: a exploração tem o teto próprio de ações (e o de chamadas e US$ em `_ai`)
            max_actions = int(s.exploracao_max_acoes)
        opcional = step.opcional and self.cfg.file.ai.limpeza_opcional
        if opcional:                       # item 31.36: limpar a tela vale no máximo 3 decisões do ator
            max_actions = min(max_actions, LIMITE_DA_ETAPA_OPCIONAL)
        # Item 31.38: a etapa de LEITURA (declara saídas e não deixa marca) tem teto próprio de decisões; a de efeito não.
        leitura = bool(saidas_declaradas) and not step.side_effect and not step.commit_guard
        teto_leitura = int(self.cfg.file.ai.max_decisoes_leitura) if leitura else 0
        ai_cfg = self._ai_da_execucao(str(run["id"]))      # 17.14: o perfil da execução pode trocar imagem e árvore
        judged_step = step.postcondition.kind == "model_judged" or need is not None
        # 31.61 (A): só a etapa de leitura (sem efeito, sem gatilho, sem `commit_guard`), julgada, com prova local de TELA
        # declarada no catálogo. Sem prova declarada nada muda; o juiz do fim do laço continua onde o contrato o pede.
        prova_da_leitura = (cap.local_proof if (LEITURA_FECHA_SEM_STEP_DONE and cap is not None and cap.local_proof
                                                and judged_step and leitura and not step.commit_selector
                                                and not cap.side_effect and not cap.commit_selector
                                                and not cap.local_proof.startswith(PROVAS_DE_EFEITO)) else None)

        async def leitura_pronta(tela: Observation) -> bool:
            """A prova local declarada da etapa de leitura vale nesta tela (a mesma porta da verificação final)."""
            if prova_da_leitura is None or cap is None or not app.package or tela.sensitive:
                return False
            return await self._prova_local(step, CapabilityRef(app.package, cap.key), tela,
                                           conta=getattr(ctx_for(), "account_label", None))

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
        # 31.223: com `strong_model_only_on_commit`, a etapa com efeito começa no modelo de ação e só o commit sobe
        so_no_commit = bool(tier_efeito) and ai_cfg.strong_model_only_on_commit
        base_tier = 1 if tier_efeito and not so_no_commit else 0
        commit_subiu = False
        commit_alvo_fora = False           # 31.232: a decisão do commit refeita no forte confere um alvo fora da árvore
        efeito_alvo_fora = False           # 31.232: o efeito disparado tinha o alvo fora da árvore (vai ao rejulgamento)
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
        recusas_do_interruptor = 0             # 29.79: a 2ª recusa por interruptor desligado para pedindo uma pessoa
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
            history.append(linha_da_recusa_do_juiz(onde, texto_v))
            repo.decision(f"{iid} · {step.title}: o verificador não comprovou a pós-condição {onde}; o ator segue "
                          "nesta mesma tentativa", run_id=run_id, instance_id=iid, step_id=step.id)
            return False

        settle_pendente: int | None = None     # C-2 (31.24): o assentamento depois da ação, para a decisão seguinte
        # Item 31.35 (parte B): as ações seguintes da última decisão (`Decision.extras`), a árvore em que o ator as
        # escolheu (para achar o MESMO alvo na tela nova) e a linha de `ai_calls` que as pagou.
        fila_encadeada: list[Decision] = []
        arvore_da_fila: UiTree | None = None
        chamada_da_fila: int | None = None
        # Item 31.51: na limpeza, o executor fecha pela árvore os diálogos do site em série (`botao_que_fecha`), com
        # teto próprio: essas voltas não contam nas `max_actions` do ator.
        limpeza = step.key.startswith(PREFIXO_LIMPEZA)
        cobertura_da_limpeza = Cobertura.das_variaveis(step.variables) if limpeza else None
        fechados_pela_regra = 0
        # 29.87: a folha de aviso fechada sem escolher e a rolagem até o interruptor exigido também são da regra: não
        # gastam as ações do ator.
        folhas_fechadas = 0
        rolagens_ate_o_interruptor = 0
        releituras_antes_do_efeito = 0
        coberturas_antes_do_efeito = 0
        #: N2 da leitura do #370: o texto da ÚLTIMA cobertura, para o motivo da parada não citar um alvo movido.
        ultima_cobertura = ""
        # Um texto só para as duas saídas do teto: `falhas.py` o classifica como ciclo sem progresso.
        motivo_do_teto = f"Limite de {max_actions} ações por etapa atingido sem concluir."
        if step.exploratoria:
            await self._partir_do_estado_conhecido(rt, app, run_id=run_id, iid=iid, step=step, call_timeout=call_timeout)
        # 31.223: o commit do modelo de ação descartado e refeito no forte tem uma volta reservada (não conta como
        # ação): no limite de ações, o commit ainda chega ao forte (achado da revisão do PR 505)
        for volta in range(max_actions + 1 + (LIMITE_DE_DIALOGOS if limpeza else 0) + LIMITE_DE_FOLHAS
                           + LIMITE_DE_ROLAGENS_ATE_O_INTERRUPTOR + int(so_no_commit)):
            if (volta - fechados_pela_regra - folhas_fechadas - rolagens_ate_o_interruptor - int(commit_subiu)
                    > max_actions):
                # E1 da revisão do 29.87: o teto do ator, não o fim do laço. Sair com `break` pularia o `else` e levaria
                # a etapa à verificação como se ela tivesse dito `step_done`.
                return await fail_or_retry(motivo_do_teto, last_obs)
            pela_regra = False
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
                        requested=image_requested, ai=ai_cfg, le_valor=bool(saidas_declaradas),
                        falta_saida=bool(faltam_saidas()), alvo_fora=commit_alvo_fora)
            t_observacao = time.monotonic()
            try:
                obs = last_obs = await reler_se_ocupada(
                    lambda: self.devices.observe(rt, timeout=call_timeout, lado_max=ai_cfg.screenshot_max_side,
                                                 imagem=lambda t: not receita_decide and self._want_image(t, **pede),
                                                 tolerar_falha_da_imagem=True),
                    prazo=deadline, quem=iid)
                arvore_da_decisao[0] = obs.tree                  # 31.230: a tela de onde parte a próxima ação
                if obs.image_omitted == "capture_failed":
                    # 31.76: a falha foi SÓ da imagem (a árvore saiu e o tamanho da tela se sabe): nada de `_stuck`,
                    # de erro seguido nem de sessão recriada. A decisão segue pela árvore; a captura nunca vira prova.
                    falhas_de_captura += 1
                    log.info("%s: a captura da tela falhou (%s); a decisão segue pela árvore", iid, obs.captura_falha)
                    if falhas_de_captura == 1:
                        repo.decision(f"{iid} · {step.title}: a captura da tela falhou "
                                      f"({(obs.captura_falha or '').split(':', 1)[0]}); a decisão segue só pela árvore",
                                      run_id=run_id, instance_id=iid, step_id=step.id)
                    if obs.captura_excedeu_prazo and not await rt.executor.drain(
                            max_wait_s=max(0.0, min(180.0, deadline - time.monotonic()))):
                        # O screencap segue preso no executor do aparelho: a próxima chamada entraria atrás dele.
                        if time.monotonic() >= deadline:
                            return await fail_or_retry(com_anr(f"Tempo da etapa esgotado ({step.timeout_s}s)."), obs)
                        return await self._stuck(rt, step, fired, obs.captura_falha or "")
                    if image_requested and falhas_de_captura >= 2:
                        # O ator pediu a imagem e ela falhou de novo: a árvore sozinha não responde ao pedido.
                        return await fail_or_retry(f"A captura da tela seguiu falhando: {obs.captura_falha}", obs)
                    if image_requested:
                        history.append("(executor) a captura da tela falhou nesta volta; decida pela lista de "
                                       "elementos, sem a imagem.")
                    if obs.captura_excedeu_prazo:
                        # Com o executor livre de novo, a árvore da decisão é lida DEPOIS dele (e só ela).
                        relida = await reler_se_ocupada(
                            lambda: self.devices.observe(rt, timeout=call_timeout, imagem=False,
                                                         lado_max=ai_cfg.screenshot_max_side,
                                                         tolerar_falha_da_imagem=True),
                            prazo=deadline, quem=iid)
                        obs = last_obs = (dataclasses.replace(relida, image_omitted="capture_failed",
                                                              captura_falha=obs.captura_falha,
                                                              captura_excedeu_prazo=True)
                                          if relida.image_omitted == "policy" else relida)
                elif obs.jpeg is not None:
                    falhas_de_captura = 0
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
            # "Não levar a imagem" e "parar e chamar uma pessoa" eram a MESMA coisa enquanto `sensitive` só
            # significava campo de senha. Deixaram de ser (achado #127): uma tela declarada em
            # `sensitive_screens` é sensível, mas parar a etapa ali seria inventar uma falha de autenticação e
            # marcar o perfil como `auth_required` toda vez que a IA passasse por ela. Só o campo de senha e o
            # desafio de verificação pedem gente. (Desde o ADR-089 e o 31.323 nenhuma tela tem a imagem omitida,
            # nem da observação nem da decisão de rotina.)
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
                                             "retome o item — ou faça o login manualmente e devolva o controle.",
                                       pede_login=True)
                if porque == MOTIVO_SENHA and senha_do_app.sem_senha_guardada:
                    # 31.278: não há senha da persona para ESTE app (sem conta ou sem credencial). Não é falha de fluxo
                    # (ADR-040: só se digita o que a pessoa guardou e consentiu): o item diz o que falta e onde guardar.
                    nome = app.name or app.package or "este app"
                    return StepOutcome(Outcome.waiting_user,
                                       f"O app pede autenticação ({porque}) e não há senha guardada para a conta desta "
                                       f"persona no {nome}: autenticação sem senha guardada.",
                                       needs=f"Guarde a senha da conta no {nome} (com consentimento) na ficha da persona "
                                             "e retome o item — ou faça o login manualmente e devolva o controle.",
                                       pede_login=True, sem_senha_guardada=True)
                return StepOutcome(Outcome.waiting_user, f"O app pede autenticação ({porque}).",
                                   needs="Assuma o controle, faça o login manualmente e devolva o controle à IA.",
                                   pede_login=True)
            # ---------- decidir: a receita (se houver e ainda casar) fala primeiro; na divergência a IA assume
            decision: Decision | None = None
            from_recipe = False
            # ---------- 29.87: a folha de aviso DECLARADA no conhecimento do app (`fechar: toque_fora`) fecha pela
            # árvore, sem IA e sem escolher nada: o toque cai no fundo escurecido acima dela. "OK" de um aviso numa
            # conta real é aceitar, e isso é do dono (`nunca`). Antes da receita e do ator: por cima da folha, nenhum
            # toque deles chega aonde miram. Sem ponto seguro, ou de volta depois do teto: uma pessoa, sem mais toque.
            regra_da_folha = None
            # E2 da revisão: lido uma vez por modificação (roda a cada volta); inválido não derruba a etapa e é
            # avisado no log uma vez por modificação do arquivo (29.90, L2).
            conhecimento_da_tela = (telas_do_app.da_pasta_por_data(CONHECIMENTO_DE_APPS / app.package)
                                    if app.package and app.package.replace(".", "").replace("_", "").isalnum()
                                    else None)
            if conhecimento_da_tela is not None:      # D1c: em qualquer idioma declarado
                regra_da_folha = telas_do_app.regra_de_fechar(conhecimento_da_tela, obs.tree, package=obs.package)
            if regra_da_folha is not None and regra_da_folha.fechar_fora is not None:
                ponto = telas_do_app.toque_fora_da_folha(regra_da_folha, obs.tree)
                if ponto is None or folhas_fechadas >= LIMITE_DE_FOLHAS:
                    await evidence(obs, f"Folha que não fechou sem escolher: {regra_da_folha.razao}")
                    return StepOutcome(Outcome.waiting_user, (
                        f'A folha "{regra_da_folha.razao}" não fechou com um toque fora dela; nada foi tocado nela.'),
                        needs="Feche a folha na tela sem aceitar nada (toque fora dela) e retome o item"
                              + ("." if fired else "; nada foi publicado."))
                escala = self._image_scale(obs, ai_cfg)
                decision = Decision(tool="tap", args={
                    "x": round(ponto[0] / escala), "y": round(ponto[1] / escala), "is_commit_action": False,
                    "rationale": f"[regra 29.87] fechar sem escolher: {regra_da_folha.razao}"})
                folhas_fechadas += 1
                pela_regra = True
                history.append(f'(executor) a folha "{regra_da_folha.razao}" foi fechada com um toque fora dela, sem '
                               "IA e sem escolher nada")
            rep = (rr.replayer if (decision is None and rr.mode == "replay" and not rr.diverged and not fired)
                   else None)
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
                    rr.partida_diferente = isinstance(exc, AlvoAusente) and rep.done_actions == 0
                    decision = None
                    history.append(f"(executor) a receita desta etapa divergiu: {exc}. Continue a partir da tela atual.")
                    repo.decision(f"{iid} · {step.title}: receita divergiu — {exc}"
                                  + ("; tela de partida diferente" if rr.partida_diferente else "")
                                  + "; a IA assume esta etapa", run_id=run_id, instance_id=iid, step_id=step.id)
            scale = self._image_scale(obs, ai_cfg)
            # 31.51: só no NAVEGADOR (revisão do #308): num app com conta real, um aviso não reconhecido com "Dismiss"
            # não se fecha por regra sem pessoa; ali fica o comportamento de antes.
            area = (cobertura_da_limpeza.bounds if cobertura_da_limpeza is not None
                    and ainda_cobre(cobertura_da_limpeza, obs.tree) else None)
            regra_vale = decision is None and limpeza and not fired and e_navegador(obs.package)
            botao = botao_que_fecha(obs.tree, area) if regra_vale else None
            if botao is not None and fechados_pela_regra < LIMITE_DE_DIALOGOS:
                # ---------- o botão que fecha ou recusa está na árvore: toca nele, sem IA e sem gastar ação
                rotulo = (botao.text or botao.desc or botao.resource_id)[:60]
                decision = Decision(tool="tap", args={"element_id": botao.id, "is_commit_action": False,
                                                      "rationale": f"[regra 31.51] fechar o diálogo: '{rotulo}'"})
                fechados_pela_regra += 1
                pela_regra = True
                history.append(f"(executor) diálogo fechado pela árvore, sem IA: '{rotulo}' ({botao.id})")
            elif botao is not None:
                # O diálogo volta depois do teto de toques: falha dizendo qual ficou, sem cair na IA.
                return await falhar_sem_nova_tentativa(
                    f"{MOTIVO_SEM_SAIDA} '{(botao.text or botao.desc or botao.resource_id)[:60]}': ele voltou depois de "
                    f"{LIMITE_DE_DIALOGOS} toques; nada foi aceito.", obs)
            elif regra_vale and (sobra := dialogo_sem_saida(obs.tree, area)) is not None:
                # Diálogo sem saída que preserve a privacidade (só aceitar, ou não reconhecido): falha com o motivo,
                # sem IA. A IA poderia aceitar os cookies opcionais ou abrir o app; nunca vira sucesso.
                return await falhar_sem_nova_tentativa(
                    f"{MOTIVO_SEM_SAIDA} '{sobra}': nenhum botão de recusar, fechar ou continuar no navegador; nada "
                    "foi aceito.", obs)
            if (decision is None and not fired and cap is not None and cap.commit_switch
                    and rolagens_ate_o_interruptor < LIMITE_DE_ROLAGENS_ATE_O_INTERRUPTOR
                    and (arrasto := rolagem_ate_o_interruptor(cap.commit_switch, cap.commit_selector,
                                                              self._argumentos_da_guarda(cap, step), obs.tree))):
                # ---------- 29.87: a linha do interruptor exigido está abaixo da dobra (o "Add AI label" do Instagram):
                # a regra rola até ela, sem IA, para o ator vê-la e ligá-la e para a guarda não ler "ausente" no Share.
                decision = Decision(tool="drag", args={
                    "from_x": round(arrasto[0] / scale), "from_y": round(arrasto[1] / scale),
                    "to_x": round(arrasto[2] / scale), "to_y": round(arrasto[3] / scale),
                    "duration_ms": DURACAO_DA_ROLAGEM_MS, "is_commit_action": False,
                    "rationale": "[regra 29.87] rolar até a linha do interruptor exigido"})
                rolagens_ate_o_interruptor += 1
                pela_regra = True
                history.append("(executor) a tela rolou até a linha do interruptor exigido, sem IA: ligue-o antes do "
                               "toque de efeito")
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
                # 31.48: também a etapa que prova por um elemento DO app (`id=<pacote>:id/…`) quando o app não está na
                # frente. Com ele já na frente (dentro de uma conversa, num aviso), abrir não muda nada: segue o ator.
                # Aberto o app, a volta seguinte lê a tela: lista à vista, o LT-1 fecha sem ator; o aviso "Novidades da
                # versão" é outra tela (a lista some), e o ator o dispensa.
                alvo_do_foco = (step.postcondition.value if step.postcondition.kind == "app_foreground"
                                else pacote_da_prova(step.postcondition))
                # Só o app DA ETAPA: um seletor com o pacote de outro app cadastrado não abre esse outro app.
                if (step.postcondition.kind == "element_present" and alvo_do_foco
                        and (alvo_do_foco != app.package or obs.package == alvo_do_foco)):
                    alvo_do_foco = ""
                if (OPEN_APP_SEM_IA and alvo_do_foco and not abriu_sem_ia and rep is None and decisions == 0
                        and not step.side_effect and not fired and alvo_do_foco in self._allowed_packages()
                        and not self._postcondition_holds(step, obs, cartao, pacote=app.package)):
                    abriu_sem_ia = True
                    rr.exerceu(StrategyKind.deterministic)
                    t_abrir = time.monotonic()
                    # 31.137: no app de prova (Configurações) a abertura apaga a tarefa e abre a tela inicial; o `am start` comum a
                    # retomava na busca (outro pacote) e o foco do app nunca chegava (60 s). Os pacotes vizinhos que a etapa
                    # declara (31.123) também valem como "na frente". Dublê sem a extensão cai na abertura comum.
                    abrir = (getattr(rt.io, "open_app_tarefa_limpa", None) if alvo_do_foco in ABERTURA_COM_TAREFA_LIMPA else None)
                    tarefa_limpa = abrir is not None
                    try:
                        await call(abrir or rt.io.open_app, alvo_do_foco, app.activity if alvo_do_foco == app.package else None)
                        na_frente = await esperar_foco(lambda: call(rt.io.current_focus), alvo_do_foco, ate=deadline,
                                                       aceitos=step.pacotes_aceitos)
                    except DriverTimeout as exc:
                        return await self._stuck(rt, step, fired, str(exc))
                    except DriverError as exc:
                        log.info("%s: abrir %s sem IA falhou (%s); o ator assume", iid, alvo_do_foco, exc)
                        na_frente = None
                    gasto = time.monotonic() - t_abrir
                    situacao = ("em primeiro plano" if na_frente else "ainda não está em primeiro plano"
                                if na_frente is False else "o pedido de abertura falhou")
                    repo.decision(f"{iid} · {step.title}: app {alvo_do_foco} aberto pelo executor, sem IA"
                                  f"{' (tarefa limpa, na tela inicial)' if tarefa_limpa else ''} — {situacao} "
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
                tier = 1 if (base_tier or commit_subiu or retentativa_subiu or errors_in_row >= 2 or same_count >= 1
                             or piso_forcou) else 0
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
                    motivo = (motivo_efeito + ("; só a decisão do commit (31.223)" if commit_subiu else "")
                              if escalonamento == "efeito" else _FRASE_DO_ESCALONAMENTO.get(escalonamento or "", ""))
                    repo.decision(f"{iid} · {step.title}: decisão escalonada para o modelo de escalonamento "
                                  f"({motivo})", run_id=run_id, instance_id=iid, step_id=step.id)
                t_prompt = time.monotonic()                     # C-2 (31.24): daqui até `_ai` é a montagem do pedido
                arvore_ms, imagem_ms, completar_ms = obs.ms_arvore, obs.ms_imagem, 0
                motivo_imagem = self._motivo_da_imagem(obs.tree, judged_step=judged_step, first=decisions == 0,
                                                       trouble=trouble, requested=image_requested, ai=ai_cfg,
                                                       le_valor=bool(saidas_declaradas),
                                                       falta_saida=bool(faltam_saidas()), alvo_fora=commit_alvo_fora)
                commit_alvo_fora = False       # 31.232: vale só para a decisão refeita, a primeira depois da subida
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
                if screen.podados and iid in ai_cfg.diagnostico_arvore_aparelhos and not obs.sensitive:
                    await self._arvore_antes_da_poda(obs, screen.podados, run_id=run_id, iid=iid, step_id=step.id,
                                                     attempt_id=attempt_id)
                image_requested = False
                if encadeada is None:          # a ação encadeada não é decisão nova (31.35)
                    if teto_leitura and decisions >= teto_leitura:
                        if not faltam_saidas() and divergentes:
                            return await fail_or_retry(
                                "A IA leu valores divergentes de " + ", ".join(f"'{n}'" for n in sorted(divergentes))
                                + f" até o teto de {teto_leitura} decisões da leitura; nenhum valor ficou estável para "
                                  "entregar às seguintes.", obs)
                        if not faltam_saidas():
                            # 31.78: com tudo lido, o teto não é "dado ausente" (r-…-701173: 12 leituras do mesmo
                            # "0" e a etapa falhou dizendo "não encontrei"). Vai à verificação, que julga a pós-condição.
                            repo.decision(f"{iid} · {step.title}: teto de {teto_leitura} decisões da leitura com todos "
                                          "os valores lidos; a etapa vai à verificação sem step_done",
                                          run_id=run_id, instance_id=iid, step_id=step.id)
                            break
                        return await dado_ausente(f"teto de {teto_leitura} decisões da leitura", obs)
                    decisions += 1
                # 31.71: o lembrete vai só na cópia desta decisão, em TODA decisão com saída faltando e imagem anexada,
                # qualquer que seja o motivo dela (A1 da leitura do #379): logo depois da recusa do `step_done` o motivo
                # é `problema`, e é justamente ali que o lembrete mais importa.
                lembrete = (lembrete_da_leitura(faltam_saidas(), visual=ai_cfg.leitura_visual.enabled)
                            if ai_cfg.imagem_enquanto_falta_saida and faltam_saidas() and screen.jpeg else None)
                actor_history = historico_do_ator(history, ai_cfg.actor_history_lines, lembrete)
                if releituras_iguais and relido is not None:
                    # 31.78 (C2): instrução ao ator, não fato do executor; por isso fora do `history` (os `facts` do juiz).
                    actor_history = [*actor_history, f"(executor) '{relido}' já foi lido e entregue, com o mesmo valor; "
                                                     "todos os valores da etapa estão lidos: a próxima ação é step_done."]
                elif leitura and divergentes and not faltam_saidas():
                    # 30.78: com tudo lido, uma saída cuja última leitura diverge da anterior segura a etapa até o teto
                    # (C1 do 31.78). Sem o aviso, o ator relia a saída ESTÁVEL sem saber por que nada andava. Instrução,
                    # como a de cima: só na cópia do ator.
                    nomes = ", ".join(f"'{n}'" for n in sorted(divergentes))
                    actor_history = [*actor_history, f"(executor) {nomes}: a última leitura deu um valor diferente da "
                                                     "anterior; releia para confirmar qual vale antes do step_done."]
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
                if (rr.mode == "shadow" and rr.replayer is not None and not rr.diverged
                        and not rr.partida_diferente):
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
            if isinstance(args, FindRow):
                # ---------- 31.340: a linha de uma lista cega pelo REMETENTE, lido da imagem às cegas (ADR-070). Só os
                # element_id voltam ao ator: nem a transcrição, nem o remetente das outras linhas, nem o assunto.
                conhecimento_f = telas_do_app.da_pasta(CONHECIMENTO_DE_APPS / (app.package or ""))
                reconhecida_f = (telas_do_app.classificar(conhecimento_f, obs.tree, package=obs.package)
                                 if conhecimento_f is not None else None)
                tela_f = reconhecida_f.tela if reconhecida_f is not None else None
                obter_f, transcrever_f = self._leitor_de_linhas(rt, obs, run_id=run_id, oid=oid, step=step,
                                                                deadline=deadline, attempt_id=attempt_id,
                                                                call_timeout=call_timeout, ai_cfg=ai_cfg)
                fora_f = self._tela_fora_do_app(step, obs, app.package)

                async def ler_linha(linha: UiElement) -> LeituraVisual:
                    return await ler_valor_visual(
                        habilitado=ai_cfg.leitura_visual.enabled, arvore=obs.tree, element_id=linha.id, nome=SAIDA_DA_LINHA,
                        valor_do_ator=args.sender, conhecimento=conhecimento_f, tela=tela_f,
                        image_policy=ai_cfg.image_policy, fora_do_app=fora_f, largura=obs.width, altura=obs.height,
                        obter_imagem=obter_f, tentativas=tentativas_visuais,
                        tipo_da_tela=reconhecida_f.tipo if reconhecida_f else None,
                        transcrever=transcrever_f if self._tem_leitor() else None, recusas=recusas_visuais)

                try:
                    achadas = await achar_linhas_por_remetente(obs.tree, conhecimento_f, tela_f, ler=ler_linha)
                except AIError as exc:
                    return await desfecho_de_ia(exc, obs, "a busca da linha")
                aid = intencao("find_row", {"sender_chars": len(args.sender)}, None, side_effect=False)
                repo.finish_action(aid, ActionStatus.done,
                                   result={"candidatas": achadas.candidatas, "lidas": achadas.lidas,
                                           "puladas": achadas.puladas, "achadas": [e.id for e in achadas.linhas],
                                           "indisponivel": achadas.indisponivel})
                if achadas.indisponivel:
                    history.append(f"find_row REJEITADA: {achadas.indisponivel}; leia a imagem e toque pelas coordenadas")
                elif achadas.linhas:
                    history.append("find_row → linha(s) do remetente pedido, de cima para baixo: "
                                   + ", ".join(e.line(self._image_scale(obs, ai_cfg)) for e in achadas.linhas)
                                   + f" ({achadas.lidas} de {achadas.candidatas} linhas lidas)")
                else:
                    history.append(f"find_row → nenhuma das {achadas.lidas} linhas lidas é desse remetente"
                                   + (f" ({achadas.puladas} não puderam ser lidas)" if achadas.puladas else "")
                                   + "; role a lista para ver outras linhas")
                continue
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
                    papel = e_nome_de_papel(args.name)
                    perguntou = relacao is None and (lido_da_imagem is not None or papel
                                                     or bool(cap and args.name in cap.saidas))
                    negados = self._papel_negado.setdefault(attempt_id, set())
                    chave_do_papel = (alvo.id if alvo is not None else "", args.name, valor)
                    if perguntou and chave_do_papel not in negados:
                        # Da imagem, ou saída que o CATÁLOGO declara sem seletor nem rótulo na árvore (a caixa do Outlook,
                        # a lista do QA): a ação diz onde está o valor, mas só o juiz confirma que é ele. Livre: dúvida.
                        # 31.47: nome de PAPEL (manchete, assunto…) cai aqui também, mesmo sem catálogo, e a pergunta é
                        # a do papel (df1212: duas leituras certas da manchete do g1 recusadas sem juiz nenhum).
                        try:
                            relacao = await self._relacao_visual(rt, step, ctx_for, run_id, oid, deadline, attempt_id,
                                                                 obs, args.name, valor, ai_cfg, alvo=alvo)
                        except AIError as exc:        # revisão do #307 (achado 3): como a leitura visual
                            return await desfecho_de_ia(exc, obs, "a relação do valor lido")
                        if relacao is None and papel and self._ultimo_veredito_de_relacao == "no":
                            negados.add(chave_do_papel)
                    if relacao is None:
                        aid = intencao("read_value", args_da_chamada_invalida(bruto, obs.tree), None, side_effect=False)
                        repo.finish_action(aid, ActionStatus.rejected, error=f"sem relação com '{args.name}'")
                        metricas.contar("leitura.sem_relacao", origem="imagem" if lido_da_imagem else "arvore")
                        repo.decision(f"{iid} · {step.title}: leitura de '{args.name}' recusada: nada na tela liga o "
                                      "valor ao que foi pedido (sem seletor, rótulo ou forma"
                                      + ("; o verificador não confirmou)" if perguntou else ")"),
                                      run_id=run_id, instance_id=iid, step_id=step.id)
                        if papel and perguntou:
                            history.append(f"read_value REJEITADA: o verificador não confirmou que este elemento ocupa "
                                           f"o papel de '{args.name}' nesta tela. Leia o elemento que de fato é o "
                                           f"'{args.name}' (posição, destaque e tamanho), não rodapé, menu, botão nem "
                                           "anúncio; se ele não existe nesta tela, chame "
                                           'step_blocked(kind="dado_ausente").')
                        else:
                            history.append(f"read_value REJEITADA: nada na tela liga este valor a '{args.name}' (nem "
                                           "rótulo vizinho, nem forma, nem o seletor do catálogo). Leia o elemento "
                                           f"rotulado como '{args.name}'; se ele não existe nesta tela, chame "
                                           'step_blocked(kind="dado_ausente").')
                        errors_in_row += 1
                        recusas_de_saida += 1
                        if errors_in_row >= 4 or recusas_de_saida >= 4:
                            return await dado_ausente("o valor lido não tinha relação com o pedido", obs)
                        continue
                repetida = args.name in lidos and lidos[args.name][0] == valor
                if args.name in lidos and not repetida:
                    divergentes.add(args.name)
                    # 30.78: FATO neutro, que vai também ao juiz (`history`): a tela mudou ou o ator leu outro elemento,
                    # e quem julga a pós-condição sabe que o valor não foi o mesmo a tentativa inteira. Sem os valores
                    # (eles já estão nas linhas das leituras) e sem instrução.
                    history.append(f"(executor) o valor lido de '{args.name}' mudou nesta tentativa; vale a última "
                                   "leitura.")
                elif repetida:
                    divergentes.discard(args.name)
                lidos[args.name] = (valor, args.value_kind)
                origem_dos_lidos[args.name] = (alvo.resource_id or "", not (args.value or "").strip())
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
                    history.append(linha_do_valor_lido(args.name, como_texto(valor, args.value_kind), faltam))
                    repo.decision(f"{iid} · {step.title}: valor '{args.name}' lido da tela ({args.value_kind}, "
                                  f"{len(valor)} caractere(s))", run_id=run_id, instance_id=iid, step_id=step.id)
                faltam = faltam_saidas()
                errors_in_row = 0
                recusas_de_saida = 0
                if (not faltam and not judged_step and not obs.sensitive
                        and self._postcondition_holds(step, obs, cartao, pacote=app.package)):
                    break              # ler não muda a tela: com tudo lido e a pós-condição valendo, só comprovar
                # Com outra saída divergente, nem aviso nem verificação: o teto decide (C1), e nada se grava.
                releituras_iguais = (releituras_iguais + 1 if repetida and not faltam and leitura and not divergentes
                                     else 0)
                if releituras_iguais >= 2:
                    # 31.78: a verificação julga a pós-condição como depois de um `step_done`; nada sai comprovado aqui.
                    repo.decision(f"{iid} · {step.title}: o ator releu '{args.name}' com o mesmo valor e todos os "
                                  "valores lidos; a etapa vai à verificação sem step_done",
                                  run_id=run_id, instance_id=iid, step_id=step.id)
                    break
                relido = args.name if releituras_iguais else None
                if not faltam and prova_da_leitura is not None and not visuais:
                    # 31.61 (A): a prova vale numa árvore lida AGORA, depois da última leitura (não na de antes): a tela
                    # pode ter mudado enquanto se lia. Fechar aqui tira só a volta ao ator; a verificação final roda igual.
                    # Valor lido da IMAGEM não se confere na árvore: com um deles, o ator segue no laço.
                    try:
                        peek = last_obs = await self.devices.observe(rt, timeout=call_timeout, imagem=False)
                    except DriverError:
                        continue
                    # L1 da revisão do #348: a prova confere a TELA; cada valor lido tem de seguir nela. Outra tela do
                    # mesmo tipo (outro e-mail, a lista rolada) casaria a prova com os valores da anterior.
                    if (await leitura_pronta(peek)
                            and all(valor_segue_na_tela(peek.tree, como_texto(v, k), *origem_dos_lidos.get(n, ("", True)))
                                    for n, (v, k) in lidos.items())):
                        repo.decision(f"{iid} · {step.title}: valores lidos e a prova local vale na tela relida; "
                                      "a etapa vai à verificação sem step_done", run_id=run_id, instance_id=iid,
                                      step_id=step.id)
                        break
                elif faltam and await leitura_pronta(obs):
                    # Instrução direta, só com o NOME da saída (nunca valor, endereço nem texto da tela).
                    history.append(f"(executor) a tela já está pronta: a próxima ação é read_value de '{faltam[0]}'.")
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
                if await leitura_pronta(obs):          # 31.61 (A): só o nome da saída, nada da tela
                    history.append("(executor) a tela já está pronta: a próxima ação é read_value de "
                                   f"'{faltam_saidas()[0]}'.")
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
            no_navegador = e_navegador(obs.package)
            escreve_em = getattr(args, "element_id", None) if decision.tool == "type_text" else None
            if (no_navegador and escreve_em and (campo := obs.tree.by_id(escreve_em)) is not None
                    and not campo.editable):
                # ---------- 31.72 (B1 da leitura do #386): o `type_text` com `element_id` TOCA o elemento antes de
                # escrever, sem exigir campo; num botão do aviso seria o aceite por fora da trava. No navegador, só em
                # campo editável.
                aid = intencao(decision.tool, args.model_dump(mode="json"), rationale,
                               side_effect=False, source="recipe" if from_recipe else "ai")
                repo.finish_action(aid, ActionStatus.rejected,
                                   error=f"{REJEICAO_TYPE_TEXT_FORA_DE_CAMPO} ({campo.id}, {tipo_do_elemento(campo)})")
                history.append(f"type_text REJEITADA pelo executor: {REJEICAO_TYPE_TEXT_FORA_DE_CAMPO} "
                               f"('{rotulo_para_o_ator(campo)}', {campo.id}); toque no campo de texto, não no botão.")
                if from_recipe:
                    rr.diverged = f"type_text fora de campo: ({campo.id}, {tipo_do_elemento(campo)})"   # K4
                errors_in_row += 1
                if errors_in_row >= 4 or recusas_da_trava() >= LIMITE_DE_RECUSAS_DE_ACEITE:
                    return await falhar_sem_nova_tentativa("A IA insistiu em type_text fora de campo editável.", obs)
                continue
            if (no_navegador and (decision.tool in ("tap", "long_press", "drag") or escreve_em)
                    and (aceite := self._aceite_do_toque(tool_ctx, args, obs.tree, ai_cfg)) is not None):
                # ---------- 31.72: a regra do 31.51 vale para o ATOR. Na r-20261005071303-f24955 ele tocou "Aceitar
                # cookies" duas vezes por conta própria. Recusado ANTES de o toque chegar ao aparelho, por coordenada
                # também, sem depender de o modelo obedecer ao prompt; nunca vira sucesso por aceite. No `error` (e no
                # `status_detail`, que chega a aviso e cartão) vão só o id e o tipo; o rótulo, texto da página, fica no
                # histórico do ator (S1 da leitura).
                saida = botao_que_fecha(obs.tree)
                quem = f"({aceite.id}, {tipo_do_elemento(aceite)})"
                aid = intencao(decision.tool, args.model_dump(mode="json"), rationale,
                               side_effect=False, source="recipe" if from_recipe else "ai")
                repo.finish_action(aid, ActionStatus.rejected, error=f"{MOTIVO_ACEITE_RECUSADO} {quem}")
                metricas.contar("executor.consentimento_recusado", origem="recipe" if from_recipe else "ai")
                if from_recipe:
                    rr.diverged = f"toque de aceite: {quem}"
                history.append(f"{decision.tool} REJEITADA pelo executor: {MOTIVO_ACEITE_RECUSADO} "
                               f"('{rotulo_para_o_ator(aceite)}', {aceite.id}). Recuse ou feche o aviso"
                               + (f" ('{rotulo_para_o_ator(saida)}', {saida.id})" if saida is not None else "")
                               + ", ou siga sem aceitar.")
                errors_in_row += 1
                if errors_in_row >= 4 or recusas_da_trava() >= LIMITE_DE_RECUSAS_DE_ACEITE:
                    # Sem saída que preserve a privacidade, a etapa não tem o que repetir: falha com o motivo.
                    return await falhar_sem_nova_tentativa(
                        f"A IA insistiu em aceitar: {MOTIVO_ACEITE_RECUSADO} {quem}; nada foi aceito.", obs)
                continue
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
            # ---------- 31.223: na etapa com efeito, o modelo de ação navega e o commit é decidido pelo forte
            if so_no_commit and not commit_subiu and not from_recipe and tier == 0 and is_commit:
                commit_subiu = True
                self._desfazer_comparacao(rr)     # 31.287: esta decisão não age; a que vale é a do forte
                commit_alvo_fora = not alvo_na_arvore(args, obs.tree)
                history.append(f"(executor) {decision.tool} dispararia o efeito desta etapa: a decisão sobe ao modelo "
                               "de escalonamento antes de agir.")
                continue
            # ---------- LT-12: na nova tentativa, o modelo barato não repete onde a anterior parou nem dispara o efeito
            if retentativa and not retentativa_subiu and not from_recipe and tier == 0:
                repete = acao_onde_parou == (obs.tree.signature(estrutural=True), f"{decision.tool}:{_target_key(args)}")
                if repete or is_commit:
                    retentativa_subiu = True
                    self._desfazer_comparacao(rr)     # 31.287: idem — a decisão do barato é descartada e refeita
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
                    desligado = (rejeicao_do_interruptor(cap.commit_switch, self._argumentos_da_guarda(cap, step),
                                                         obs.tree)
                                 if reject is None and cap is not None else None)
                    if desligado:
                        recusas_do_interruptor += 1
                        aid = intencao(decision.tool, args.model_dump(mode="json"), rationale, side_effect=True,
                                       source="recipe" if from_recipe else "ai")
                        repo.finish_action(aid, ActionStatus.rejected, error=desligado)
                        await evidence(obs, f"Efeito recusado antes do toque: {desligado}")
                        if recusas_do_interruptor >= 2 or from_recipe:
                            # 29.79: sem o interruptor confirmado ligado, o efeito não sai; uma pessoa olha a tela.
                            return StepOutcome(Outcome.waiting_user, desligado, needs=(
                                "Confira na tela se o interruptor pedido está ligado (o rótulo de IA da publicação) e "
                                "retome o item; nada foi publicado."))
                        history.append(f"{decision.tool} REJEITADA pelo executor: {desligado}")
                        continue
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
                if decision.tool in ("tap", "long_press"):
                    # ---------- 29.90: a tela pode mudar entre a leitura conferida e o toque (a folha "Sharing
                    # posts" abre por cima do Share). Relida AGORA: algo novo por cima do ponto, ou o alvo fora do
                    # lugar, e o toque não sai; o laço observa de novo, e a folha declarada fecha pela regra. Só no
                    # toque: `type_text` e `drag` de efeito ficam fora por escopo (nenhuma folha medida os cobre).
                    # A releitura conta no prazo da etapa e é medida à parte (`executor.releitura_antes_do_efeito_ms`).
                    t_releitura = time.monotonic()
                    try:
                        px, py, _ = resolve_point(tool_ctx, getattr(args, "element_id", None),
                                                  getattr(args, "x", None), getattr(args, "y", None))
                        mudou = cobertura_nova_no_ponto(obs.tree, await quick_tree(), (px, py), target)
                    except (DriverError, TelaDeContaTravada) as exc:
                        mudou = (MUDANCA_RELEITURA_FALHOU, f"a releitura antes do toque falhou ({exc})")
                    metricas.observar("executor.releitura_antes_do_efeito_ms", ms_desde(t_releitura),
                                      resultado=mudou[0] if mudou else "tocou")
                    if mudou:
                        tipo_da_mudanca, mudanca = mudou
                        releituras_antes_do_efeito += 1
                        coberturas_antes_do_efeito += tipo_da_mudanca == MUDANCA_POR_CIMA
                        if tipo_da_mudanca == MUDANCA_POR_CIMA:
                            ultima_cobertura = mudanca
                        metricas.contar("executor.tela_mudou_antes_do_efeito", motivo=tipo_da_mudanca,
                                        origem="receita" if from_recipe else "ator")
                        await evidence(obs, f"Toque de efeito segurado [{tipo_da_mudanca}]: {mudanca}")
                        aid = intencao(decision.tool, args.model_dump(mode="json"), rationale, side_effect=True,
                                       source="recipe" if from_recipe else "ai")
                        repo.finish_action(aid, ActionStatus.rejected,
                                           error=f"tela mudou antes do toque [{tipo_da_mudanca}]: {mudanca}")
                        if from_recipe:   # D2-R1: o cursor da receita já passou desta ação; quem decide agora é a IA
                            rr.diverged = f"tela mudou antes do toque: {mudanca}"
                        if releituras_antes_do_efeito >= LIMITE_DE_RELEITURAS_ANTES_DO_EFEITO:
                            if coberturas_antes_do_efeito:
                                # D5: um clicável NOVO por cima do botão de efeito que a regra do app não fechou
                                # (não declarado, ou a folha declarada que reabriu a cada leitura); responder a ele é
                                # da pessoa (como a folha que não fecha). Sem nova navegação: a etapa para e a pessoa
                                # olha a tela. O texto cita a última COBERTURA, não a última mudança (que pode ser o
                                # alvo movido: N2 da leitura do #370); D5-N1: nem sempre é aviso "não declarado".
                                return StepOutcome(Outcome.waiting_user, (
                                    "Um aviso cobre o botão de efeito e não fechou pela regra do conhecimento do "
                                    f"app: {ultima_cobertura}; nada foi tocado."),
                                    needs="Veja o aviso na tela, feche-o sem aceitar nada se for o caso e retome o "
                                          "item; nada foi publicado.")
                            # Só o alvo se mexendo (ou a releitura falhando): tela instável, repetir é o certo.
                            return await fail_or_retry(
                                f"A tela mudou entre a conferência e o toque de efeito: {mudanca}; nada foi tocado.",
                                obs)
                        history.append(f"{decision.tool} NÃO SAIU: a tela mudou entre a conferência e o toque "
                                       f"({mudanca}); olhe de novo")
                        continue

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
            if is_commit and (sem_reserva := self._reservar_excecao(step)) is not None:
                # 30.65: a exceção desta etapa não pôde ser reservada (revogada, recusada, vencida, já em uso): nada sai.
                return await falhar_sem_nova_tentativa(sem_reserva, obs)
            aid = intencao(decision.tool, args.model_dump(mode="json"), rationale, side_effect=is_commit,
                           source="recipe" if from_recipe else ("regra" if pela_regra else "ai"))
            if decision.tool == "type_text" and not step.capability:
                self._digitados()[step.id] = str(getattr(args, "text", "") or "")   # 31.250: o texto da etapa livre
            if is_commit:
                self._guardar_linha_de_base(step, obs.tree)      # 31.59: a tela de ANTES do toque
                efeito_alvo_fora = not alvo_na_arvore(args, obs.tree)
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
                    history.append(f"(executor) {decision.tool}({_brief(args)}) sem confirmação "
                                   f"({enderecos_limpos(str(exc))}): a ação pode "
                                   "ter chegado ao app — confira na tela atual antes de repetir.")
                else:
                    history.append(f"{decision.tool}({_brief(args)}) FALHOU: {enderecos_limpos(str(exc))}")
                errors_in_row += 1
                if errors_in_row >= 3:
                    # o erro vai a attempts.error, que a tentativa seguinte põe no histórico do ator
                    return await fail_or_retry(f"Falhas consecutivas do driver: {enderecos_limpos(str(exc))}", obs)
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
            return await fail_or_retry(motivo_do_teto, last_obs)

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
                                                                      alvo_do_efeito_fora=fired and efeito_alvo_fora,
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
        # 31.54 (U1b da revisão): com a barra sem tapar, o juiz pode transcrever a URL da imagem. Limpa UMA vez aqui, na
        # fonte: o mesmo `text` vira a nota da evidência, o `detail` do desfecho (status da etapa e do objetivo,
        # `blocked_reason`, evento, atenção, `settle_effect`) e, no sucesso, o `evidence_text`. `evidencia_da_conta` e
        # `PARTES_EM_ELEMENTOS_DIFERENTES` casam frase e rótulo, não URL, e a limpeza guarda o host e o 1º pedaço.
        text = enderecos_limpos(text) if text else text
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
        if ok and step.side_effect and fired and cap is not None and cap.commit_switch_mark:
            # 29.79 (d): a contagem prova que publicou, não que saiu COM o rótulo. Só leitura, pela árvore: a última
            # tela da verificação e, sem a marca, UMA releitura depois de uma espera curta. Sem ela, o efeito existe e
            # a pessoa confere; nada se repete.
            faltam, tela_da_marca = await self._marcas_depois_do_efeito(rt, cap, step, obs, account_label,
                                                                         call_timeout)
            if faltam:
                # Sem o texto do juiz: ele pode trazer o que a tela mostrou (URL com token), e este motivo vai ao
                # `detail`, à evidência e ao evento. A prova da publicação fica na evidência do verificador (S3).
                motivo = ("publicado; o rótulo de IA não foi confirmado. Abra a publicação: se o rótulo não "
                          "estiver lá, ligue-o pelo app ou remova a publicação. Não publique de novo. (A publicação "
                          "foi comprovada pela verificação; " + ", ".join(f"'{m}'" for m in faltam) + " não apareceu "
                          "no cartão do topo, junto do nome da conta " + (account_label or "(desconhecida)") + ".)")
                await evidence(tela_da_marca or obs, motivo)
                return StepOutcome(Outcome.uncertain, motivo, delivery_level=level,
                                   result=StepResult(verified=False, evidence_text=motivo, delivery_level=level,
                                                     efeito_comprovado=True))
            if exigidas := marcas_exigidas(cap.commit_switch_mark, self._argumentos_da_guarda(cap, step)):
                vista = ("marca " + ", ".join(f"'{m}'" for m in exigidas) + " vista no cartão do topo, junto do nome "
                         "da conta")
                # Revisão D2 (condição da orquestradora): no sucesso a tela da conferência também fica, para a pessoa
                # ver QUAL cartão confirmou o rótulo (o limite aceito: o post novo fora da tela e um antigo nosso
                # rotulado como primeiro cartão ainda passaria).
                await evidence(tela_da_marca or obs, "Conferência da marca depois do efeito: " + vista)
                text += "; " + vista
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

    def _leitor_de_linhas(self, rt: DeviceRuntime, obs: Observation, *, run_id: str, oid: str, step: StepDTO, deadline: float,
                          attempt_id: str, call_timeout: float, ai_cfg: AiCfg) -> tuple[ObterImagem, Transcrever]:
        """31.340: a imagem e o leitor às cegas da `find_row`, os mesmos da `read_value` visual (mesmas recusas)."""
        async def obter_imagem() -> tuple[UiTree, bytes | None, int, int] | None:
            if obs.jpeg is not None:
                return obs.tree, obs.jpeg, obs.width, obs.height
            if obs.sensitive:
                return None
            try:
                nova = await self.devices.observe(rt, timeout=call_timeout, imagem=True, lado_max=ai_cfg.screenshot_max_side)
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
                    raise
                raise LeituraVisualRecusada(
                    "sem_leitor" if exc.kind == "not_configured" else "leitor_falhou") from None

        return obter_imagem, transcrever

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
        # 31.123: os pacotes vizinhos que a etapa declara (a busca do Configurações) comprovam como o do app; pacote
        # desconhecido continua não comprovando
        for aceito in (pacote, *step.pacotes_aceitos):
            if obs.package == aceito or (obs.package is None and aceito in obs.tree.packages):
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
                              valor: str, ai: AiCfg, *, alvo: UiElement | None = None) -> str | None:
        """31.41: o valor lido da IMAGEM não tem elemento com texto na árvore para a regra determinística. Uma pergunta
        de sim ou não ao verificador, com a imagem: este valor é, na tela, o `nome` pedido? "yes" = `"verificador"`;
        "no", "uncertain" ou qualquer outra coisa = `None` (dúvida nunca fecha como sucesso). Custa uma chamada de
        verificação por leitura visual aceita pelo leitor. 31.47: para nome de PAPEL (`e_nome_de_papel`) a pergunta é "este
        elemento ocupa o papel `nome` nesta tela?" (posição, destaque, vizinhança), não "o texto tem relação com `nome`"."""
        if e_nome_de_papel(nome):
            pergunta = pergunta_de_papel(nome, valor, alvo, (obs.width, obs.height))
        else:
            pergunta = (f"O valor lido para '{nome}' foi \"{valor}\". Julgue SÓ a relação: na tela, esse texto é o "
                        f"'{nome}' que o objetivo pede (o rótulo, a posição ou o papel dele na tela o identificam como "
                        "tal), e não outro texto qualquer? yes = é; no = é outro; uncertain = não dá para afirmar.")
        ctx = dataclasses.replace(ctx_for(), postcondition_description=pergunta)
        if obs.jpeg is None:
            obs = await self.devices.completar_imagem(rt, obs, timeout=float(self.get_settings().driver_call_timeout_s),
                                                      lado_max=ai.screenshot_max_side)
        screen, _ = self._screen(obs, with_image=True, protect=tuple(step.commit_guard), ai=ai)
        t_end = min(deadline, time.monotonic() + float(ai.verify_budget_s))
        verdict = await self._ai(run_id, objective_id,
                                 lambda: self.provider.verify(VerifyRequest(ctx=ctx, screen=screen, facts=[])),
                                 step_id=step.id, role="verify", deadline=t_end, attempt_id=attempt_id,
                                 marca=MarcaDaChamada(motivo="julgamento", image_reason="pedida"))
        self._ultimo_veredito_de_relacao = verdict.satisfied
        return "verificador" if verdict.satisfied == "yes" else None

    async def _verify(self, rt: DeviceRuntime, step: StepDTO, ctx_for: Callable[[], StepContext], run_id: str,
                      objective_id: str, deadline: float, call_timeout: float, *, patient: bool,
                      facts: list[str] | None = None, failure_marks: tuple[str, ...] = (),
                      local_proof: str | None = None, capability: CapabilityRef | None = None,
                      attempt_id: str | None = None, cartao: tuple[str, ...] = (), pacote: str | None,
                      imagem_forcada: bool = False, uma_rodada: bool = False, so_prova_local: bool = False,
                      proposito: MotivoDaChamada = "julgamento", copias_vistas: list[int] | None = None,
                      sobreposicoes: list[bool] | None = None, coberturas: list[Cobertura] | None = None,
                      alvo_do_efeito_fora: bool = False
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
                                         coberturas=coberturas, alvo_do_efeito_fora=alvo_do_efeito_fora)
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
                         sobreposicoes: list[bool] | None = None, coberturas: list[Cobertura] | None = None,
                         alvo_do_efeito_fora: bool = False
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
                # ADR-055: "Sending…" na tela é efeito A CAMINHO, nunca feito. Em 19/09 a DM da ciclana foi dada por
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
            # 31.239: a prova do comentário publicado nunca fecha o efeito sozinha; ela dispensa o 1º juiz lá embaixo.
            provada = (judged and need is None and bool(local_proof) and not local_proof.startswith("comentario")
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
                    marcado = self._marcador_dispensa_o_juiz(step, capability, obs, need=need,
                                                             ja_julgou=judged_polls > 0, escalou=escalou)
                    pela_prova_local = False       # 31.238: o "sim" veio da prova local do app, não do juiz barato
                    if marcado is not None:
                        pela_prova_local = True
                        # 31.57: o marcador de entrega que o catálogo declara ("Seen" debaixo da bolha desta execução)
                        # afirma o nível na árvore. Como no 31.26, substitui SÓ o julgamento barato: o "sim" segue para
                        # o rejulgamento do 17.10 logo abaixo, que confere a tela e diz o nível que vale.
                        verdict = Verdict(satisfied="yes", delivery_level=marcado,
                                          evidence=f"marcador de entrega '{marcado.value}' declarado no catálogo, logo "
                                                   "abaixo da mensagem desta execução: o primeiro julgamento foi "
                                                   "dispensado; o rejulgamento confere")
                        metricas.contar("verificacao.primeiro_juiz_dispensado", prova=f"marcador:{marcado.value}")
                        self.repo.decision(f"{rt.id} · {step.title}: nível {marcado.value} pelo marcador do catálogo na "
                                           "árvore; o primeiro julgamento foi dispensado e o rejulgamento confere",
                                           run_id=run_id, instance_id=rt.id, step_id=step.id)
                    elif await self._sent_text_dispensa_o_juiz(step, capability, obs, need=need,
                                                               local_proof=local_proof, ja_julgou=judged_polls > 0,
                                                               escalou=escalou):
                        # 31.26 (A): a prova local `sent_text` já comprovou o envio desta execução na árvore (o app não
                        # mostra "Entregue"). Ela substitui SÓ o julgamento barato: o "sim" daqui segue para o
                        # rejulgamento do 17.10 logo abaixo, que é quem decide.
                        verdict = Verdict(satisfied="yes", delivery_level=DeliveryLevel.sent,
                                          evidence=f"prova local ({local_proof}) na árvore: o primeiro julgamento foi "
                                                   "dispensado; o rejulgamento confere")
                        pela_prova_local = True
                        metricas.contar("verificacao.primeiro_juiz_dispensado", prova=str(local_proof))
                        self.repo.decision(f"{rt.id} · {step.title}: envio comprovado pela árvore local (sent_text); o "
                                           "primeiro julgamento foi dispensado e o rejulgamento confere",
                                           run_id=run_id, instance_id=rt.id, step_id=step.id)
                    elif await self._comentario_dispensa_o_juiz(step, capability, obs, local_proof=local_proof,
                                                                ja_julgou=judged_polls > 0, escalou=escalou,
                                                                conta=getattr(ctx_for(), "account_label", None)):
                        # 31.239: o comentário desta execução está na lista, atribuído à conta conectada, sem marca de
                        # pendente. Substitui SÓ o julgamento barato; o rejulgamento logo abaixo decide (ou o 31.238).
                        verdict = Verdict(satisfied="yes", delivery_level=None,
                                          evidence=f"prova local ({local_proof}) na árvore: o comentário aparece na "
                                                   "lista, atribuído à conta conectada; o primeiro julgamento foi "
                                                   "dispensado; o rejulgamento confere")
                        pela_prova_local = True
                        metricas.contar("verificacao.primeiro_juiz_dispensado", prova="comentario")
                        self.repo.decision(f"{rt.id} · {step.title}: comentário comprovado pela árvore local; o primeiro "
                                           "julgamento foi dispensado e o rejulgamento confere",
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
                    rejulgaria = (verdict.satisfied == "yes" and not escalou and (step.side_effect or need is not None)
                                  and self.cfg.file.ai.rejudge_yes_on_side_effect
                                  and self.cfg.ai_role("verify").model != self.cfg.ai_role("escalation").model)
                    if rejulgaria and pela_prova_local and self._rejulgamento_dispensado(run_id, rt.id, step):
                        escalou = True             # 31.238: dispensado; o "sim" da prova local vale
                        rejulgaria = False
                    if rejulgaria:
                        # Item 17.10: um "sim" errado numa etapa com efeito externo (ela mesma, ou a que confirma o nível de entrega do efeito) vira sucesso falso (no rejulgamento de
                        # 25/09 o Haiku aprovou 6 telas erradas em 56). O modelo de escalonamento confere a mesma tela,
                        # uma vez, e o veredito dele é o que vale: se discordar, não conta como prova.
                        escalou = True
                        self.repo.decision(
                            f"{rt.id} · {step.title}: o verificador aprovou uma etapa com efeito externo; "
                            "conferindo com o modelo de escalonamento",
                            run_id=run_id, instance_id=rt.id, step_id=step.id)
                        # 31.232: o efeito disparado por um alvo fora da árvore é conferido COM a imagem, a mesma
                        # régua do commit refeito; com o alvo na árvore, a tela do 1º juiz, como antes.
                        motivo_rejulgamento = motivo_imagem
                        tela_rejulgamento = screen
                        if alvo_do_efeito_fora and motivo_imagem not in _IMAGEM_VAI:
                            motivo_rejulgamento = self._motivo_da_imagem(obs.tree, judged_step=False, first=False,
                                                                         trouble=False, requested=False, ai=ai_cfg,
                                                                         alvo_fora=True)
                            if motivo_rejulgamento in _IMAGEM_VAI:
                                try:
                                    obs = await self.devices.completar_imagem(rt, obs, timeout=call_timeout,
                                                                              lado_max=lado_max)
                                    tela_rejulgamento, _ = self._screen(obs, with_image=True,
                                                                        protect=tuple(step.commit_guard), ai=ai_cfg)
                                except DriverTimeout:
                                    raise
                                except DriverError as exc:   # sem a imagem, o rejulgamento segue pela árvore (o de antes)
                                    log.info("%s: imagem do rejulgamento indisponível (%s)", rt.id, exc)
                        verdict = await self._ai(
                            run_id, objective_id,
                            lambda: self.provider.verify(VerifyRequest(ctx=ctx_for(), screen=tela_rejulgamento,
                                                                       facts=list(facts or []), escalate=True)),
                            # 31.50 (d): SEM a dica da tela. O rejulgamento do "sim" com efeito é a segunda opinião
                            # independente; com a mesma orientação do primeiro juiz, deixava de ser.
                            step_id=step.id, role="verify", deadline=t_end, attempt_id=attempt_id,
                            marca=MarcaDaChamada(motivo="rejulgamento", escalate="sim_com_efeito",
                                                 image_reason=motivo_rejulgamento))
                        level = verdict.delivery_level
                    if copias_vistas is not None and verdict.copias is not None:
                        copias_vistas.append(verdict.copias)
                    if sobreposicoes is not None and verdict.satisfied in ("no", "uncertain"):
                        achada = cobertura_na_arvore(obs.tree, verdict.cobre) if verdict.sobreposicao else None
                        pequena = bool(verdict.sobreposicao) and not sobreposicao_vale(obs.tree, verdict.cobre,
                                                                                       obs.width, obs.height)
                        if pequena:
                            # 31.73: o juiz marcou sobreposição citando uma faixa que não esconde o alvo (o banner "Abra
                            # o app" do topo, na f24955: 11,6 %, no fluxo da página), e a causa principal era o conteúdo
                            # errado. A limpeza seria inserida à toa e o desfecho esconderia a causa: vale como "não"
                            # comum. A `Cobertura` da limpeza, quando vale, segue sendo o citado (o "X", se for ele).
                            metricas.contar("juiz.sobreposicao_descartada", motivo="cobertura_pequena")
                        sobreposicoes.append(bool(verdict.sobreposicao) and not pequena)
                        if coberturas is not None and achada is not None and not pequena:
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

    def _linha_de_base(self) -> dict[str, int]:
        """31.59: o mapa das linhas de base; em memória, então uma verificação depois de reinício (reconciliação) não tem
        linha de base e a árvore não afirma o envio: o modelo julga."""
        if not hasattr(self, "_mensagens_antes"):
            self._mensagens_antes = {}
        return self._mensagens_antes

    def _digitados(self) -> dict[str, str]:
        """31.250: o mapa dos textos digitados nas etapas livres (mesmo cuidado de `_linha_de_base` com o executor de
        teste montado sem `__init__`)."""
        if not hasattr(self, "_textos_digitados"):
            self._textos_digitados = {}
        return self._textos_digitados

    def _conteudo_da_etapa(self, step: StepDTO) -> str | None:
        """O `content` da etapa; na etapa SEM capability (plano livre), o texto do último `type_text` dela (31.250)."""
        conteudo = (step.bindings or {}).get("content")
        if conteudo is None and not step.capability:
            conteudo = self._digitados().get(step.id) or None
        return None if conteudo is None else str(conteudo)

    def _guardar_linha_de_base(self, step: StepDTO, tree: UiTree) -> None:
        """31.59: no toque do efeito, quantas bolhas com o texto IGUAL ao `content` a tela tem AGORA. A prova `sent_text`
        e o marcador do 31.57 só contam envio se depois houver mais do que isso. Etapa sem `content` não guarda nada.
        31.250: na etapa livre, o `content` é o texto digitado nela (`_conteudo_da_etapa`)."""
        conteudo = self._conteudo_da_etapa(step)
        if conteudo is not None:
            self._linha_de_base()[step.id] = tree.mensagens_iguais(str(conteudo))

    def _marcador_dispensa_o_juiz(self, step: StepDTO, capability: CapabilityRef | None, obs: Observation, *,
                                  need: DeliveryLevel | None, ja_julgou: bool, escalou: bool) -> DeliveryLevel | None:
        """31.57: o nível que o marcador declarado no catálogo afirma nesta tela, quando ele dispensa o PRIMEIRO
        julgamento; `None` senão. As mesmas travas do 31.26: há nível exigido e o marcado o atende, é o primeiro
        julgamento, e o rejulgamento do 17.10 vai acontecer (ligado e com modelo diferente). Sem rejulgamento, o
        marcador sozinho fecharia o efeito, e isso o desenho não aceita.

        31.250: na etapa SEM capability, as marcas vêm do `entrega.yaml` do app da tela (o QA Messenger, que não tem
        catálogo), e o texto da mensagem é o digitado na etapa; o resto (linha de base, travas) é o mesmo."""
        ai = self.cfg.file.ai
        if not (ai.marcador_de_entrega_dispensa_primeiro_juiz and need is not None
                and not ja_julgou and not escalou and ai.rejudge_yes_on_side_effect
                and self.cfg.ai_role("verify").model != self.cfg.ai_role("escalation").model):
            return None
        if capability is not None:
            cap = capability_of(capability.app, capability.key)
            if cap is None or not cap.delivery_marks:
                return None
            marcas, pendentes, falhas = cap.delivery_marks, cap.pending_marks, cap.failure_marks
        else:
            try:
                declarada = entrega_do_pacote(obs.package)
            except ValueError:  # arquivo inválido: o juiz julga, como antes
                log.exception("entrega.yaml de %s não carregou", obs.package)
                return None
            if declarada is None:
                return None
            marcas, pendentes, falhas = declarada.delivery_marks, declarada.pending_marks, declarada.failure_marks
        nivel = nivel_pelo_marcador(marcas_de_entrega(marcas), self._conteudo_da_etapa(step), obs.tree,
                                    antes=self._linha_de_base().get(step.id), pendentes=pendentes, falhas=falhas)
        if nivel is None or DELIVERY_ORDER[DeliveryLevel(nivel)] < DELIVERY_ORDER[need]:
            return None
        return DeliveryLevel(nivel)

    def _rejulgamento_dispensado(self, run_id: str, iid: str, step: StepDTO) -> bool:
        """31.238: o app desta etapa ganhou o direito à dispensa do rejulgamento `sim_com_efeito` (mínimo de rejulgados na
        janela, nenhuma discordância; `efeitos_rejulgados_do_app`, a régua de `/api/usage`)? Dispensado, fica na trilha
        da execução (`kind = rejulgamento_dispensado`, com o app e a conta que deu o direito) e na métrica: é o contador da
        próxima medida. Leitura que falha não dispensa nada."""
        ai = self.cfg.file.ai
        if not ai.rejulgamento_dispensado_por_app:
            return False
        try:
            app = app_da_etapa(step.app_id, self.repo.db.scalar("SELECT app_ids FROM runs WHERE id=?", (run_id,)))
            desde = to_iso(now() - timedelta(days=ai.rejulgamento_dispensa_janela_dias))
            rejulgados, discordancias = efeitos_rejulgados_do_app(self.repo.db, app, desde=desde)
        except Exception:  # noqa: BLE001 - na dúvida, o rejulgamento roda
            log.exception("%s: direito à dispensa do rejulgamento não lido; rejulga", iid)
            return False
        if rejulgados < ai.rejulgamento_dispensa_minimo or discordancias:
            return False
        texto = (f"{iid} · {step.title}: efeito comprovado pela prova local do app; rejulgamento dispensado (31.238: "
                 f"{rejulgados} rejulgamento(s) do app {app} em {ai.rejulgamento_dispensa_janela_dias} dia(s), "
                 "nenhuma discordância)")
        self.repo.bus.emit("decision", texto, run_id=run_id, instance_id=iid, step_id=step.id,
                           data={"text": texto, "kind": "rejulgamento_dispensado", "app": app,
                                 "rejulgados": rejulgados, "discordancias": discordancias})
        metricas.contar("verificacao.rejulgamento_dispensado", app=app)
        return True

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

    async def _comentario_dispensa_o_juiz(self, step: StepDTO, capability: CapabilityRef | None, obs: Observation, *,
                                          local_proof: str | None, ja_julgou: bool, escalou: bool,
                                          conta: str | None) -> bool:
        """31.239: o primeiro julgamento desta verificação pode ser dispensado pela prova do comentário publicado? As
        travas do 31.26: a ação declara `comentario:`, é o primeiro julgamento, e o rejulgamento do 17.10 vai acontecer
        (ligado e com modelo diferente). A marca de pendente ("Posting…") a porta confere antes (`pending_marks`)."""
        ai = self.cfg.file.ai
        if not (ai.comentario_dispensa_primeiro_juiz and local_proof and local_proof.startswith("comentario")
                and not ja_julgou and not escalou and ai.rejudge_yes_on_side_effect
                and self.cfg.ai_role("verify").model != self.cfg.ai_role("escalation").model):
            return False
        return await self._prova_local(step, capability, obs, conta=conta)

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
                         mensagens_antes=self._linha_de_base().get(step.id),
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


#: Dois candidatos a interruptor com o centro a menos disto um do outro (em px, na vertical) são dúvida: recusa.
EMPATE_DO_INTERRUPTOR_PX = 12


def estado_do_interruptor(tree: UiTree, seletor: str) -> str:
    """O interruptor de `seletor` na tela: "ligado", "desligado", "ambiguo" ou "ausente". Ligado é o próprio elemento do
    seletor marcado, ou o ÚNICO candidato da linha dele: um elemento clicável, marcável (`checkable`) ou marcado, à
    DIREITA do texto, cuja faixa vertical se sobrepõe à do texto (o interruptor medido em 03/10 é mais alto que o
    texto), o de centro mais próximo do centro do texto. Revisão (c) do 29.79: qualquer marcável encostado na faixa
    valia, e o interruptor ligado da linha de cima (compartilhar em outra rede) passaria por este. Revisão C1: o
    interruptor `clickable=false` (a linha é que recebe o toque) entra pelo `checkable` — sem isso o vizinho ligado
    virava o único candidato. Revisão C1b: e o centro do candidato tem de cair na faixa do texto alargada em meia
    altura. Dois candidatos quase empatados: "ambiguo" (em dúvida, não publica)."""
    estado = "ausente"
    for alvo in tree.find_selector(seletor):
        if alvo.checked:
            return "ligado"
        x2, y1, y2 = alvo.bounds[2], alvo.bounds[1], alvo.bounds[3]
        centro = (y1 + y2) / 2
        # Revisão C1b: o centro do candidato cai na faixa do texto alargada em meia altura para cada lado (no 8.3,
        # 486..562: o interruptor medido tem o centro em 543). O ligado da linha vizinha que só encosta (centro em 465)
        # fica de fora mesmo quando o da linha não é clicável nem marcável.
        meia = (y2 - y1) / 2
        candidatos = sorted(
            (e for e in tree.elements
             if e.id != alvo.id and (e.clickable or e.checkable or e.checked) and e.bounds[0] >= x2
             and e.bounds[1] < y2 and e.bounds[3] > y1
             and y1 - meia <= (e.bounds[1] + e.bounds[3]) / 2 <= y2 + meia),
            key=lambda e: abs((e.bounds[1] + e.bounds[3]) / 2 - centro))
        if not candidatos:
            estado = "desligado" if estado == "ausente" else estado     # o texto está, o interruptor não se acha
            continue
        if len(candidatos) > 1 and (abs((candidatos[1].bounds[1] + candidatos[1].bounds[3]) / 2 - centro)
                                    - abs((candidatos[0].bounds[1] + candidatos[0].bounds[3]) / 2 - centro)
                                    < EMPATE_DO_INTERRUPTOR_PX):
            estado = "ambiguo"
            continue
        if candidatos[0].checked:
            return "ligado"
        if estado != "ambiguo":
            estado = "desligado"
    return estado


#: 29.87: quantas vezes, por tentativa, a regra rola a tela atrás da linha do interruptor exigido.
LIMITE_DE_ROLAGENS_ATE_O_INTERRUPTOR = 4
#: 29.87: cada passo arrasta esta fração da área rolável. Medido no android-13 (05/10): na tela da legenda (área de
#: 955 px) a legenda e a linha "Add AI label" só cabem juntas com 380 a 544 px de rolagem. O `scroll` do ator (70 %)
#: tiraria a legenda da tela, e a guarda `{content}` do Share exige o texto visível; em passos de 25 % a linha aparece
#: no 2º, com a legenda ainda à vista.
PASSO_DA_ROLAGEM = 0.25
#: 29.87: o arrasto é lento (sem arremesso): o conteúdo anda o que o dedo andou.
DURACAO_DA_ROLAGEM_MS = 600
#: 29.87: quantas folhas declaradas (`fechar: toque_fora`) a regra fecha por tentativa antes de chamar uma pessoa.
LIMITE_DE_FOLHAS = 2
#: 29.90: quantas vezes, NO TOTAL da tentativa, a releitura logo antes do toque de efeito pode achar a tela mudada
#: (algo novo por cima do ponto, ou o alvo fora do lugar) antes de a tentativa falhar sem tocar. Não zera: depois do
#: toque que saiu, `fired` não deixa sair outro toque de efeito.
LIMITE_DE_RELEITURAS_ANTES_DO_EFEITO = 3
#: 29.90: os tipos de mudança que seguram o toque, no motivo e na métrica `executor.tela_mudou_antes_do_efeito`
#: (D2-M1): para a janela do deploy 35 contar quantas foram só mudança de posição.
MUDANCA_POR_CIMA = "cobertura"
MUDANCA_FORA_DO_LUGAR = "alvo_movido"
MUDANCA_RELEITURA_FALHOU = "releitura_falhou"


def cobertura_nova_no_ponto(antes: UiTree, depois: UiTree, ponto: tuple[int, int],
                            alvo: UiElement | None) -> tuple[str, str] | None:
    """29.90: o que mudou, entre a árvore em que a guarda conferiu o efeito (`antes`) e a releitura logo antes do toque
    (`depois`), que faria o toque não cair no alvo: `(tipo, texto)`, ou `None` = pode tocar. Medido no android-13: a
    folha "Sharing posts" abre por cima do Share, e o acerto por área (`resolve_point`, o menor elemento que contém o
    ponto, sem ordem de camadas) seguiria achando o texto "Share" embaixo dela. Por isso o critério é o que APARECEU:
    um clicável que não estava na árvore de antes e cobre o ponto (por área: um clicável redimensionado por baixo
    também conta, e custa uma volta, nunca um toque errado). Sem elemento no ponto (`alvo` `None`, toque por x,y), só a
    cobertura vale. Pura."""
    if alvo is not None and not any(e.resource_id == alvo.resource_id and e.bounds == alvo.bounds
                                    and e.text == alvo.text and e.desc == alvo.desc for e in depois.elements):
        return MUDANCA_FORA_DO_LUGAR, "o alvo do efeito não está mais no mesmo lugar"
    x, y = ponto

    def chave(e: UiElement) -> tuple[str, str, tuple[int, int, int, int]]:
        return e.resource_id, e.class_name, tuple(e.bounds)       # os ids `eN` mudam a cada leitura

    vistos = {chave(e) for e in antes.elements}
    novo = next((e for e in depois.elements if e.clickable and chave(e) not in vistos
                 and e.bounds[0] <= x <= e.bounds[2] and e.bounds[1] <= y <= e.bounds[3]), None)
    if novo is not None:
        return MUDANCA_POR_CIMA, f"apareceu por cima do alvo ({novo.resource_id.rsplit('/', 1)[-1] or novo.class_name})"
    return None


def rolagem_ate_o_interruptor(commit_switch: Sequence[str], commit_selector: str | None,
                              bindings: Mapping[str, str], tree: UiTree) -> tuple[int, int, int, int] | None:
    """29.87: o arrasto (x1, y1, x2, y2, no aparelho) que traz à tela a linha de um interruptor exigido e AUSENTE,
    ou `None`. Só na tela do efeito (o `commit_selector` à vista: sem isso a regra rolaria a galeria atrás da linha) e
    só quando o argumento do interruptor é "true". O arrasto sobe um passo (`PASSO_DA_ROLAGEM`) dentro da maior área
    rolável vertical e começa num ponto sem nada clicável nem editável embaixo: é rolagem, nunca toque. Pura."""
    if not commit_selector or not tree.find_selector(commit_selector):
        return None
    if not any(estado_do_interruptor(tree, s) == "ausente" for s in marcas_exigidas(commit_switch, bindings)):
        return None
    verticais = [e for e in tree.elements
                 if e.scrollable and (e.bounds[3] - e.bounds[1]) * 2 > (e.bounds[2] - e.bounds[0])]
    if not verticais:
        return None
    area = max(verticais, key=lambda e: (e.bounds[2] - e.bounds[0]) * (e.bounds[3] - e.bounds[1]))
    x1, y1, x2, y2 = area.bounds
    passo = int((y2 - y1) * PASSO_DA_ROLAGEM)
    if passo < 40:
        return None
    tocaveis = [e for e in tree.elements if e.clickable or e.editable]
    margem = max(8, (x2 - x1) // 20)
    for x in ((x1 + x2) // 2, x1 + margem, x2 - margem):
        for y in range(y2 - 12, y1 + passo + 12, -12):
            if not any(e.bounds[0] <= x <= e.bounds[2] and e.bounds[1] <= y <= e.bounds[3] for e in tocaveis):
                return x, y, x, y - passo
    return None


def interruptor_ligado(tree: UiTree, seletor: str) -> bool:
    """O interruptor de `seletor` está ligado na tela (`estado_do_interruptor`)?"""
    return estado_do_interruptor(tree, seletor) == "ligado"


#: 29.79 (d): a marca conta colada no nome da conta: a borda de cima dela até isto (px) abaixo da de baixo do nome.
COLA_DA_MARCA_PX = 12
#: 29.79 (d): a espera antes da única releitura da marca (ela pode chegar depois do cartão do post).
ESPERA_DA_MARCA_S = 3.0


def marcas_exigidas(commit_switch_mark: Sequence[str], bindings: Mapping[str, str]) -> list[str]:
    """29.79 (d): os seletores de `commit_switch_mark` (`<argumento>:<seletor>`) cujo argumento é "true"."""
    exigidas: list[str] = []
    for entrada in commit_switch_mark:
        argumento, _, seletor = entrada.partition(":")
        if str(bindings.get(argumento.strip(), "")).strip().lower() == "true":
            exigidas.append(seletor.strip())
    return exigidas


def marca_junto_da_conta(tree: UiTree, seletor: str, conta: str | None) -> bool:
    """29.79 (d): a marca do `seletor` está na tela logo ABAIXO do nome da `conta`, na mesma coluna, no cartão do TOPO?
    Medido no 8.3 (03/10, android-01): logo depois do Share o post novo é o primeiro do feed, com o nome em
    (98,395)-(632,446) e "AI info" em (98,445)-(632,499). Só o nome da conta mais acima conta, e nenhum cabeçalho do
    mesmo tipo (mesmo `resource_id`) pode estar acima dele: um post ANTIGO nosso com rótulo, mais abaixo no feed, não é
    a marca do novo. A marca de OUTRO perfil não conta, e sem a conta conhecida não há como dizer que o post é o nosso:
    dúvida, não conta."""
    nomes = {norm_text(v) for v in variantes_de_arroba(conta or "") if v}
    if not nomes:
        return False
    contas = [e for e in tree.elements if norm_text(e.text) in nomes or norm_text(e.desc) in nomes]
    if not contas:
        return False
    nome = min(contas, key=lambda e: (e.bounds[1], e.bounds[0]))
    if nome.resource_id and any(e.resource_id == nome.resource_id and e.bounds[1] < nome.bounds[1]
                                for e in tree.elements):
        return False                       # o cartão do topo é de outro perfil: o post novo não está onde devia
    for marca in tree.find_selector(seletor):
        colada = nome.bounds[1] <= marca.bounds[1] <= nome.bounds[3] + COLA_DA_MARCA_PX
        mesma_coluna = marca.bounds[0] < nome.bounds[2] and marca.bounds[2] > nome.bounds[0]
        if marca.id != nome.id and colada and mesma_coluna:
            return True
    return False


def rejeicao_do_interruptor(commit_switch: Sequence[str], bindings: Mapping[str, str], tree: UiTree) -> str | None:
    """29.79: por que o toque de efeito NÃO pode acontecer por um interruptor desligado; `None` quando cada interruptor
    exigido (`<argumento>:<seletor>` com o argumento "true") está ligado na tela. Pura, para o teste bater sem
    aparelho."""
    for entrada in commit_switch:
        argumento, _, seletor = entrada.partition(":")
        if str(bindings.get(argumento.strip(), "")).strip().lower() != "true":
            continue
        estado = estado_do_interruptor(tree, seletor.strip())
        if estado == "ambiguo":
            return (f"interruptor ambíguo: mais de um controle na linha de '{seletor.strip()}' (a etapa pede "
                    f"{argumento.strip()}) e não dá para dizer qual é o dele; em dúvida o efeito não sai — uma pessoa "
                    "confere a tela")
        if estado != "ligado":
            return (f"antes do efeito, '{seletor.strip()}' tem de estar LIGADO (a etapa pede {argumento.strip()}) e não "
                    "está: ligue-o nesta tela antes de tocar no efeito")
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


def lembrete_da_leitura(faltam: list[str], *, visual: bool) -> str:
    """Item 31.71: o lembrete das saídas que faltam, só com os NOMES (nunca valor lido). Sem a leitura visual ligada, não
    oferece o `source='visual'`, que o executor recusaria."""
    nomes = ", ".join(f"'{n}'" for n in faltam)
    como = "read_value(source='visual')" if visual else "read_value"
    return f"(executor) a imagem desta observação está anexada; para {nomes}, use {como} na linha que o mostra."


def historico_do_ator(history: list[str], n: int, lembrete: str | None) -> list[str]:
    """O histórico que vai NESTA decisão: o comprimido e, no fim, o lembrete do 31.71. O lembrete não entra no
    `history` durável: o `compress_history` guarda toda linha `(executor)`, e ele se acumularia a cada decisão."""
    copia = compress_history(history, n)
    return [*copia, lembrete] if lembrete else copia


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
    #: 31.244: os nomes das variáveis da persona do objetivo (sem valor): a destilação aceita o marcador dela.
    persona: frozenset[str] = frozenset()
    app_version: str | None = None
    step_hash: str | None = None
    #: RA-20 B: a chave sem a pós-condição escrita (`hash_generico_da_linha`); None = a etapa só tem a específica.
    step_hash_generico: str | None = None
    signature: str = ""
    variant: str = ""
    diverged: str | None = None
    #: 30.80: a divergência foi na AÇÃO 1, antes de a receita agir, por alvo ausente: a tela de partida era outra.
    partida_diferente: bool = False
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
    #: 31.287: o cursor da receita e os sinais de divergência ANTES da última comparação da sombra, para desfazê-la quando a
    #: decisão comparada é descartada pelo executor (o commit que sobe ao modelo forte, 31.223 / LT-12) e refeita.
    antes_da_comparacao: tuple[int, int, int, str | None, bool] | None = None

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
    """A ação no histórico do ator. 31.52: a URL do `open_url` vai só com host e 1º pedaço do caminho."""
    d = args.model_dump(exclude={"rationale"}, exclude_none=True)
    return ", ".join(f"{k}={str(endereco_para_o_prompt(str(v)) if k.endswith('url') else v)[:60]!r}"
                     for k, v in d.items())


def _brief_result(result: dict[str, Any]) -> str:
    # o `open_url` devolve `opened_url` (revisão 15c): toda chave que termina em `url` passa pela limpeza
    return ", ".join(f"{k}={str(endereco_para_o_prompt(str(v)) if k.endswith('url') else v)[:80]}"
                     for k, v in result.items() if k != "ms") or "ok"
