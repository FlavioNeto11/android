"""Ferramentas tipadas que a IA pode pedir. O executor só aceita estes nomes e argumentos
validados por schema; nada que o modelo escreva vira código ou comando de shell."""
from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..models import DeliveryLevel
from ..util import norm_text, url_abrivel
from .driver import DeviceIO, DriverError, DriverTimeout, SemCampoEmFoco
from .hierarchy import ContaTravada, UiElement, UiTree

#: Até quando se espera um app aberto chegar ao primeiro plano. A frio, no android-06 (2 vCPU saturadas), o Instagram
#: levou 28–51 s (r-20260928195344-02ee9e) — e a janela de partida não tem forma `pacote/atividade`, então o foco lê
#: "nenhum" até a primeira tela. Cada leitura é um `dumpsys window` num convidado sem CPU: não se lê mais rápido que
#: `INTERVALO_DO_FOCO_S`. Lidos na hora da chamada (os testes os encurtam).
ESPERA_DO_FOCO_S = 60.0
INTERVALO_DO_FOCO_S = 2.0
#: LT-6 (caminho rápido 2): nos primeiros segundos a sondagem é mais fina. Em 7 d o `open_app` mediu 2,2 s de mediana —
#: exatamente um ciclo de 2 s: a 1ª leitura logo após o `am start` ainda não vê o app, e a 2ª só vinha 2 s depois. A
#: partida quente chega em < 1 s; a fria (28–51 s) volta ao intervalo largo depois desta janela.
INTERVALO_INICIAL_DO_FOCO_S = 0.5
JANELA_INICIAL_DO_FOCO_S = 5.0


async def esperar_foco(ler: Callable[[], Awaitable[tuple[str | None, str | None]]], pacote: str, *,
                       ate: float | None = None, aceitos: Collection[str] = ()) -> bool:
    """Espera `pacote` ter a janela em foco. `True` só com o foco LIDO; o prazo vencido é `False`, nunca "abriu".

    `aceitos` (31.137, a mesma lista de `StepDTO.pacotes_aceitos` do 31.123): pacotes vizinhos que a etapa declara e que valem como o app
    quando estão na frente (a busca do Configurações é de outro pacote e o foco do Configurações nunca chegava).

    `ate` (relógio monotônico) corta a espera antes de `ESPERA_DO_FOCO_S` — o executor passa o prazo da etapa. Ler o
    foco é só leitura: erro de leitura é "ainda não"; tempo esgotado na fila do aparelho encerra a espera (a próxima
    leitura ficaria atrás da que não voltou).
    """
    inicio = time.monotonic()
    limite = inicio + ESPERA_DO_FOCO_S
    if ate is not None:
        limite = min(limite, ate)
    while True:
        try:
            dono, _ = await ler()
        except DriverTimeout:
            return False
        except DriverError:
            dono = None
        if dono == pacote or (dono is not None and dono in aceitos):
            return True
        agora = time.monotonic()
        if agora >= limite:
            return False
        # `min` com o intervalo largo: os testes encurtam `INTERVALO_DO_FOCO_S`, e a janela fina não pode deixá-los lentos.
        intervalo = (min(INTERVALO_INICIAL_DO_FOCO_S, INTERVALO_DO_FOCO_S)
                     if agora - inicio < JANELA_INICIAL_DO_FOCO_S else INTERVALO_DO_FOCO_S)
        await asyncio.sleep(min(intervalo, limite - agora))


COMMIT_VOCAB = re.compile(
    r"\b(enviar|envia|send|submit|confirmar|confirm|pagar|pay|comprar|buy|publicar|postar|post|excluir|apagar|"
    r"delete|remover|transferir|finalizar)\b", re.IGNORECASE)


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rationale: str = Field(description="Uma frase curta, em português, explicando a decisão.")


class ObserveScreen(_Args):
    """Apenas observar novamente (a tela ainda está carregando ou mudando)."""
    need_image: bool = Field(
        default=False, description="true para receber a IMAGEM da tela na próxima observação, quando a lista de "
                                   "elementos não basta (ícones sem texto, conteúdo desenhado, WebView).")


class _Action(_Args):
    expect_done: bool = Field(
        default=False, description="true se, depois DESTA ação, o objetivo da etapa deve estar atingido. O executor "
                                   "confere a pós-condição e conclui a etapa sem precisar de step_done.")


class FindElement(_Args):
    """Procura elementos na hierarquia atual por texto, resource-id ou descrição de acessibilidade."""
    text: str | None = None
    resource_id: str | None = None
    description: str | None = None


class FindRow(_Args):
    """Só onde a lista não expõe texto na hierarquia (o executor diz no histórico): acha a LINHA de uma lista pelo REMETENTE
    escrito na imagem. O executor lê o remetente de cada linha por você (às cegas, sem lhe mostrar o que leu) e devolve os
    `element_id` das linhas cujo remetente é `sender`; aí você toca nelas com `tap(element_id=…)`. Uma busca por tela: se nenhuma
    linha for dele, role a lista e procure de novo."""
    sender: str = Field(description="O nome do remetente como aparece na lista, ex.: Bruno Ferreira.")


class Tap(_Action):
    """Toca em um elemento (element_id da lista) ou, se não houver elemento adequado, em coordenadas x,y da imagem."""
    element_id: str | None = None
    x: int | None = None
    y: int | None = None
    is_commit_action: bool = Field(
        description="true se ESTE toque dispara o efeito externo da etapa (enviar, confirmar, pagar…).")


class LongPress(_Action):
    """Mantém pressionado um elemento ou coordenada."""
    element_id: str | None = None
    x: int | None = None
    y: int | None = None
    duration_ms: int = 800
    is_commit_action: bool = False


class Drag(_Action):
    """Arrasta de um ponto a outro (coordenadas da imagem)."""
    from_x: int
    from_y: int
    to_x: int
    to_y: int
    duration_ms: int = 500
    is_commit_action: bool = False


class Scroll(_Action):
    """Rola o conteúdo para revelar itens. direction = para onde o CONTEÚDO avança (down = ver itens abaixo)."""
    direction: Literal["up", "down", "left", "right"]
    element_id: str | None = None


class TypeText(_Action):
    """Digita texto no campo focado (ou toca antes em element_id para focar)."""
    text: str
    element_id: str | None = None
    clear_first: bool = True
    press_enter: bool = False
    is_commit_action: bool = False


class TypeSecret(_Action):
    """Digita no CAMPO DE SENHA a senha de uma conta da persona deste aparelho, pelo NOME lógico listado em "Dados da
    persona disponíveis" (ex.: conta_chrome_senha). Você nunca vê o valor: ele sai do cofre direto para o campo, só
    no app (e no site) daquela conta e só com o consentimento da pessoa. Para enviar o formulário, toque no botão
    (Entrar) numa ação à parte."""
    # Sem `press_enter`, de propósito: Enter pode SUBMETER, e `type_secret` fica fora de EFFECT_CAPABLE — o envio
    # tem de ser um `tap`, que o executor rastreia como efeito (commit, guarda, não repetir).
    name: str = Field(description="Nome lógico da senha da conta, ex.: conta_chrome_senha.")
    element_id: str | None = Field(default=None, description="O campo de senha; sem ele, o primeiro campo de senha.")


class OpenUrl(_Action):
    """Abre um endereço http/https no navegador do aparelho. Só endereços escritos no comando da pessoa."""
    url: str


class PressBack(_Action):
    """Botão Voltar do Android."""


class PressHome(_Action):
    """Botão Início do Android."""


class OpenApp(_Action):
    """Abre (traz à frente) o aplicativo alvo da execução."""
    package: str | None = None


class WaitFor(_Args):
    """Aguarda até um texto aparecer (ou apenas alguns segundos)."""
    text: str | None = None
    seconds: float = 3


class CollectList(_Action):
    """SÓ em etapa de coleta: lê TODOS os itens de uma lista. O executor rola a lista do início ao fim e devolve os
    textos dos elementos que casam com `item_selector` (sem repetição, na ordem da tela)."""
    element_id: str = Field(description="A lista (contêiner rolável) na observação atual.")
    item_selector: str = Field(description="Seletor dos elementos cujo TEXTO é o item, ex.: id=conversation_name.")
    exclude: list[str] = Field(default=[], description="Textos a ignorar — só se o objetivo da etapa mandar excluir.")


class ReadValue(_Args):
    """SÓ em etapa que entrega um valor às seguintes: lê na tela o valor de nome `name` (os nomes vêm no histórico, na
    linha "(executor) esta etapa entrega…"). O executor tira o valor do TEXTO do elemento — você não o escreve; com
    `value`, só aquele trecho do texto do elemento. Código de verificação, senha ou token nunca: a etapa para."""
    # Item 24.3 (ADR-058): quem lê é o executor, pela árvore (`taskqueue/saidas.ler_valor`), como na coleta. `value` é
    # só o RECORTE — e precisa estar no texto do elemento: um valor que a tela não tem não passa.
    name: str = Field(description="Nome da saída declarada nesta etapa (ex.: perfil_citado).")
    element_id: str = Field(description="O elemento cujo texto é o valor; numa lista (value_kind=list), o contêiner.")
    value: str | None = Field(default=None, description="Só o trecho do texto do elemento que é o valor, quando o "
                                                        "texto tem mais do que ele (ex.: o @nome dentro do assunto).")
    value_kind: Literal["text", "number", "url", "list"] = Field(
        default="text", description="text, number, url, ou list (os textos dentro do contêiner).")
    # Item 12.5 (ADR-070): `visual` SÓ quando o executor disser, no histórico, que a linha desta tela não expõe texto na
    # árvore e o app declarou que ela pode ser lida da imagem. O valor é o que VOCÊ leu na imagem; o executor o
    # confere com a transcrição às cegas de outro leitor, sobre o recorte da linha. Sem concordância, recusa.
    source: Literal["tree", "visual"] = Field(
        default="tree", description="tree (padrão): o texto do elemento; visual: o valor que você leu na imagem, só "
                                    "numa tela cega declarada (exige `value`; só value_kind=text).")

    @model_validator(mode="after")
    def _visual_exige_o_valor_e_so_texto(self) -> "ReadValue":
        """Número, endereço e lista lidos da imagem são recusados na v1: número visto na imagem é justamente o formato
        de um código. E sem o valor do ator não há o que conferir."""
        if self.source == "visual":
            if self.value_kind != "text":
                raise ValueError("source=visual só vale com value_kind=text")
            if not (self.value or "").strip():
                raise ValueError("source=visual exige `value` (o que você leu na imagem)")
        return self


class VerifyState(_Args):
    """Confere, na tela atual, quais dos textos informados estão visíveis."""
    texts: list[str]


class StepDone(_Args):
    """Declara que o objetivo da etapa foi atingido. O executor ainda verifica a pós-condição."""
    evidence: str = Field(description="O que, na tela, comprova a conclusão.")
    delivery_level: DeliveryLevel | None = Field(
        default=None, description="Só para envio de mensagem: nível observado (appeared/sent/delivered/read).")


class StepBlocked(_Args):
    """A etapa não pode prosseguir sem intervenção ou é impossível.

    `challenge` (ADR-055): tela de verificação — confirmar que é humano, CAPTCHA, código de login/2FA. Vira
    `auth_challenge` (só uma pessoa resolve), nunca `auth_required`: este devolvia o caso ao login automático, que
    voltava a abrir o app sobre uma conta travada.

    `dado_ausente` (31.38): só em etapa de LEITURA — o valor pedido não está onde a etapa manda procurar. A etapa não
    se repete: o objetivo ganha UM plano revisado com a evidência, e a segunda vez fecha como falha."""
    kind: Literal["auth_required", "challenge", "wrong_account", "missing_info", "app_incompatible",
                  "unexpected_screen", "dado_ausente", "other"]
    reason: str
    needs_user: bool


TOOLS: dict[str, type[_Args]] = {
    "observe_screen": ObserveScreen, "find_element": FindElement, "find_row": FindRow, "tap": Tap, "long_press": LongPress,
    "drag": Drag, "scroll": Scroll, "type_text": TypeText, "press_back": PressBack, "press_home": PressHome,
    "open_app": OpenApp, "wait_for": WaitFor, "verify_state": VerifyState, "collect_list": CollectList,
    "step_done": StepDone, "step_blocked": StepBlocked, "type_secret": TypeSecret, "open_url": OpenUrl,
    "read_value": ReadValue,
}
CONTROL_TOOLS = {"step_done", "step_blocked"}
EFFECT_CAPABLE = {"tap", "long_press", "drag", "type_text"}     # podem disparar um efeito externo
READ_ONLY = {"observe_screen", "find_element", "find_row", "wait_for", "verify_state"}

#: 31.74: o relógio que mede a leitura da árvore no `wait_for` (o teste injeta um falso, com leitura lenta).
_relogio: Callable[[], float] = time.monotonic
STRICT_TOOLS = EFFECT_CAPABLE | CONTROL_TOOLS


class ToolValidationError(ValueError):
    pass


class TelaDeContaTravada(DriverError):
    """Uma leitura da tela DENTRO de uma ferramenta (a rolagem que confere o conteúdo, a coleta que rola a lista, a
    conferência da digitação) achou a tela de conta travada ou de código (ADR-055). A ferramenta para ali mesmo: a
    rolagem fecharia com "voltar" a janela que entrou por cima, e a coleta seguiria arrastando. Quem levanta é a
    observação rápida do executor; quem trata é o executor, com o mesmo desfecho da observação do laço.

    `effect_possible` segue a regra de `DriverError`: a leitura em si não tem efeito, mas quem a pediu depois de um
    gesto (`_ler_depois_do_gesto`) o marca como possível."""

    def __init__(self, trava: ContaTravada, pacote: str | None) -> None:
        super().__init__(f"tela de verificação da conta ({trava.descrever()}); nada mais foi tocado",
                         effect_possible=False)
        self.trava = trava
        self.pacote = pacote


_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def _no_site_da_conta(url: str, hosts: set[str]) -> bool:
    """O endereço está num host de conta de portal da persona (ou em subdomínio dele)? A mesma trava de subdomínio
    do `type_secret`: `sso.portal.exemplo.test` entra por `portal.exemplo.test`; `portal-parecido.exemplo.test` não."""
    if not hosts:
        return False
    sem_esquema = url.split("://", 1)[-1]
    host = sem_esquema.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].rsplit("@", 1)[-1].split(":", 1)[0].casefold()
    return bool(host) and any(host == h or host.endswith("." + h) for h in hosts)


def urls_do_texto(texto: str | None) -> list[str]:
    """Endereços http/https escritos num texto da PESSOA (o comando): os únicos que `open_url` aceita. Pontuação de
    fim de frase não faz parte do endereço; `)` final só sai quando está sobrando — "(veja https://x/a)" perde, mas
    ".../Java_(linguagem)" fica inteiro."""
    urls = []
    for u in _URL.findall(texto or ""):
        while u and (u[-1] in ".,;:" or (u[-1] == ")" and u.count(")") > u.count("("))):
            u = u[:-1]
        urls.append(u)
    return urls


def validate_call(name: str, raw_args: Any) -> _Args:
    model = TOOLS.get(name)
    if model is None:
        raise ToolValidationError(f"Ferramenta desconhecida: {name!r}. Disponíveis: {', '.join(TOOLS)}")
    if not isinstance(raw_args, dict):
        raise ToolValidationError("Os argumentos devem ser um objeto JSON.")
    try:
        return model.model_validate(raw_args)
    except ValidationError as exc:
        errs = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                         for e in exc.errors(include_input=False, include_url=False, include_context=False)[:4])
    # 31.67 (V4 da revisão do 31.63): levantado FORA do `except`. A `ValidationError` em `__cause__`/`__context__` traz
    # os ARGUMENTOS que o ator escolheu (o texto a digitar, por exemplo) como `input_value`.
    raise ToolValidationError(f"Argumentos inválidos para {name}: {errs}")


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema compatível com `strict: true` (todos os campos obrigatórios, sem restrições não suportadas)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return clean(dict(defs[node["$ref"].rsplit("/", 1)[-1]]))
            # em `properties` as chaves são NOMES de campos (um campo pode se chamar "title"): só os valores são limpos
            out = {k: ({name: clean(sub) for name, sub in v.items()} if k == "properties" and isinstance(v, dict)
                       else clean(v))
                   for k, v in node.items()
                   if k not in ("title", "default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                                "minLength", "maxLength", "pattern", "minItems", "maxItems")}
            if out.get("type") == "object":
                out["additionalProperties"] = False
                out["required"] = list(out.get("properties", {}).keys())
            return out
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    return clean(schema)


def tool_definitions(strict: bool = True) -> list[dict[str, Any]]:
    """`strict` (gramática imposta pelo provedor) só nas ferramentas que podem causar efeito externo ou encerrar a
    etapa: a API recusa as 14 como estritas ("Schema is too complex" — medido: 6 passam, 8 não). As demais usam o
    mesmo schema sem a gramática; TODA chamada é revalidada por `validate_call` antes de qualquer execução."""
    out = []
    for name, model in TOOLS.items():
        d: dict[str, Any] = {"name": name, "description": (model.__doc__ or name).strip(),
                             "input_schema": strict_schema(model)}
        if strict and name in STRICT_TOOLS:
            d["strict"] = True
        out.append(d)
    return out


# ------------------------------------------------------------------ execução
@dataclass
class ToolContext:
    io: DeviceIO
    call: Callable[..., Awaitable[Any]]          # roda fn no executor do dispositivo, com timeout
    tree: UiTree
    width: int
    height: int
    image_scale: float                           # pixels do aparelho por pixel da imagem vista pelo modelo
    app_package: str | None
    app_activity: str | None
    allowed_packages: set[str] = field(default_factory=set)
    observe: Callable[[], Awaitable[UiTree]] | None = None
    collect_max_items: int | None = None         # teto da coleta; None = ler até o fim da lista (comportamento antigo)
    collect_from_top: bool = True                # False em lista infinita, onde voltar ao topo é atualizar
    # Desfaz a rolagem da coleta no fim, contando as páginas que desceu. Existe separado de `collect_from_top`
    # porque há lista onde IR ao topo é perigoso (na folha de comentários, arrastar demais no topo a FECHA), mas
    # voltar para perto do início é necessário: os alvos são lidos de cima para baixo e as etapas seguintes
    # começariam do fim da lista.
    collect_rewind: bool = False
    #: ADR-025/040: preenche o campo de senha com a senha da conta `name` pelo canal sensível e devolve só o recibo.
    #: `None` = este aparelho não tem perfil (logo, nenhuma conta cuja senha digitar).
    fill_secret: Callable[[str, str | None], Awaitable[dict[str, Any]]] | None = None
    #: Os endereços que `open_url` aceita: os que a PESSOA escreveu no comando.
    allowed_urls: set[str] = field(default_factory=set)
    #: E os sites das contas de portal da persona deste aparelho (`profile_accounts.host`, ADR-040): ali `open_url`
    #: aceita qualquer caminho do host (ou de subdomínio dele). Vazio = só os endereços do comando.
    allowed_hosts: set[str] = field(default_factory=set)
    #: Prazo da ETAPA (`time.monotonic()`): o que a ferramenta espera no aparelho (o foco de `open_app`) não passa
    #: dele — a mesma regra de `_com_prazo` para a chamada de IA (achado #96). `None` = sem etapa (só o teto próprio).
    deadline: float | None = None
    #: T.2 (achado #164): como a ferramenta ESPERA o aparelho assentar (0,4 s depois de tocar no campo, 0,5 s antes de
    #: conferir a digitação, `wait_for`, a rolagem...). Era `asyncio.sleep` fixo: cada passo de mensagem pagava ~2,9 s
    #: de tempo real, e a suíte do rodízio e das execuções era feita quase só disso. Injetável, como o `relogio` do
    #: scheduler e do gerenciador: a produção dorme de verdade; o teste que quer pular o tempo entrega um `dormir`
    #: que AVANÇA o relógio do aparelho falso em vez de esperar (`tests/relogio_virtual.py`).
    dormir: Callable[[float], Awaitable[None]] = asyncio.sleep


@dataclass
class ToolOutcome:
    result: dict[str, Any]
    target: UiElement | None = None


def resolve_point(ctx: ToolContext, element_id: str | None, x: int | None, y: int | None) -> tuple[int, int, UiElement | None]:
    """Resolve o alvo ANTES de tocar. Erros aqui não chegaram ao aparelho (effect_possible=False)."""
    if element_id:
        el = ctx.tree.by_id(element_id)
        if el is None:
            raise DriverError(f"element_id {element_id!r} não existe na observação atual.", effect_possible=False)
        if not el.enabled:
            raise DriverError(f"O elemento {element_id} está desabilitado.", effect_possible=False)
        cx, cy = el.center
        return cx, cy, el
    if x is None or y is None:
        raise DriverError("Informe element_id ou as coordenadas x e y.", effect_possible=False)
    dx, dy = round(x * ctx.image_scale), round(y * ctx.image_scale)
    if not (0 <= dx < ctx.width and 0 <= dy < ctx.height):
        raise DriverError(f"Coordenadas ({x},{y}) fora da tela.", effect_possible=False)
    hit = [e for e in ctx.tree.elements if e.bounds[0] <= dx <= e.bounds[2] and e.bounds[1] <= dy <= e.bounds[3]]
    el = min(hit, key=lambda e: (e.bounds[2] - e.bounds[0]) * (e.bounds[3] - e.bounds[1])) if hit else None
    return dx, dy, el


def _content_in(tree: UiTree, area: tuple[int, int, int, int]) -> frozenset[tuple[Any, ...]]:
    """Conteúdo dentro da área rolada. Não usa a assinatura da tela (o relógio da barra de status mudaria o
    resultado) nem os ids `eN` (mudam a cada observação)."""
    x1, y1, x2, y2 = area
    return frozenset((e.resource_id, e.text, e.desc, e.class_name, e.bounds) for e in tree.elements
                     if e.bounds[0] >= x1 and e.bounds[1] >= y1 and e.bounds[2] <= x2 and e.bounds[3] <= y2
                     and e.bounds != (x1, y1, x2, y2))


#: Fração da tela (no eixo do arrasto) abaixo da qual um elemento é uma FAIXA — uma linha da grade, um cabeçalho —
#: e não a área onde a rolagem acontece.
FAIXA_MINIMA = 0.4


def _dentro(interno: tuple[int, int, int, int], externo: tuple[int, int, int, int]) -> bool:
    return (externo[0] <= interno[0] and externo[1] <= interno[1]
            and interno[2] <= externo[2] and interno[3] <= externo[3])


def _area_de_rolagem(ctx: ToolContext, el: UiElement | None, *, vertical: bool) -> tuple[int, int, int, int]:
    """Onde o arrasto da rolagem é desenhado. O elemento que a IA escolhe nem sempre é a lista: na
    r-20260928165254-e31953 foi uma linha de ~200 px da grade, e o arrasto de 140 px dentro dela começava sobre uma
    miniatura — curto e lento, virou toque longo. Elemento que não rola, ou que é faixa estreita no eixo do arrasto,
    cede lugar ao menor ancestral rolável que é largo nesse eixo; sem ancestral, à área útil da tela (0,2h–0,85h).
    Exceção: uma lista que ROLA e não está dentro de nada rolável (menu suspenso, folha inferior) é a própria
    superfície — arrastar na área da tela começaria ou terminaria fora dela e fecharia a folha.
    O eixo importa: um carrossel horizontal é estreito na altura e é exatamente onde rolar para o lado."""
    padrao = (0, int(ctx.height * 0.2), ctx.width, int(ctx.height * 0.85))
    if el is None:
        return padrao
    tela = ctx.height if vertical else ctx.width

    def largo(b: tuple[int, int, int, int]) -> bool:
        return (b[3] - b[1] if vertical else b[2] - b[0]) >= FAIXA_MINIMA * tela

    if el.scrollable and largo(el.bounds):
        return el.bounds
    ancestrais = [e for e in ctx.tree.elements if e.scrollable and _dentro(el.bounds, e.bounds) and largo(e.bounds)]
    if ancestrais:
        return min(ancestrais, key=lambda e: (e.bounds[2] - e.bounds[0]) * (e.bounds[3] - e.bounds[1])).bounds
    return el.bounds if el.scrollable else padrao


def _sobreposicao_nova(antes: UiTree, depois: UiTree, recipientes: set[tuple[str, str]]) -> str | None:
    """Uma janela nova cobriu a tela depois do arrasto? O dump é o da janela ATIVA: um menu de contexto ou uma
    folha de compartilhar entra no lugar da tela — a árvore encolhe e a lista rolada some dela. Conservador de
    propósito, porque a resposta é apertar Voltar, e Voltar numa tela normal navega para trás: rolagem de verdade
    que encolhe a árvore mantém a lista (classe e resource-id; os limites mudam com cabeçalho que recolhe)."""
    # Sem pacote nenhum antes (árvore vazia: tela carregando), qualquer pacote depois pareceria "novo".
    novos = [p for p in depois.packages if p not in antes.packages] if antes.packages else []
    if novos:
        return f"janela de outro pacote ({novos[0]}) apareceu depois do arrasto"
    n0, n1 = len(antes.elements), len(depois.elements)
    if recipientes:
        encolheu = n0 >= 8 and 2 * n1 < n0 and not any((e.class_name, e.resource_id) in recipientes
                                                       for e in depois.elements)
    else:                                          # sem a lista para conferir, só um encolhimento bem maior conta
        encolheu = n0 >= 8 and 4 * n1 < n0
    if encolheu:
        return f"a tela passou de {n0} para {n1} elementos e a lista rolada sumiu (menu ou janela por cima)"
    return None


async def _ler_depois_do_gesto(ctx: ToolContext) -> UiTree:
    """Leitura da tela DEPOIS de um gesto que já foi ao aparelho. Sozinha, uma leitura que falha não tem efeito
    (`effect_possible=False`); aqui ela vem depois do gesto, e dizer "nada chegou ao aparelho" faria um `type_text`
    de commit virar `fired=False` e poder ser repetido. O erro sobe dizendo que o efeito é possível."""
    if ctx.observe is None:
        raise DriverError("Sem observação rápida da tela.", effect_possible=True)
    try:
        return await ctx.observe()
    except DriverError as exc:
        exc.effect_possible = True
        raise


#: Quantas vezes a digitação completa o que faltou antes de desistir e dizer ao modelo que o texto está incompleto.
DIGITACAO_COMPLEMENTOS = 2
#: Pedaço do teclado (`mobile: type`) quando não há campo em foco onde definir o texto de uma vez. Cada chamada tem
#: uma janela, e num convidado saturado a cauda de uma fila longa se perde (r-20260928165254-e31953: 22 de 125).
PEDACO_DO_TECLADO = 20


def _campo_digitado(tree: UiTree, alvo: UiElement | None) -> UiElement | None:
    """O campo que recebeu o texto: o do alvo (mesmo id de recurso), senão o editável com foco, senão o único
    editável da tela. Sem um desses, `None` — melhor não afirmar nada do que conferir o campo errado."""
    editaveis = [e for e in tree.elements if e.editable and not e.password]
    if alvo is not None and alvo.resource_id:
        mesmos = [e for e in editaveis if e.resource_id == alvo.resource_id]
        if len(mesmos) == 1:
            return mesmos[0]
    focados = [e for e in editaveis if e.focused]
    if len(focados) == 1:
        return focados[0]
    return editaveis[0] if len(editaveis) == 1 else None


def _prefixo_no_fim(campo: str, texto: str) -> int:
    """Quantos caracteres do COMEÇO de `texto` já estão no fim do campo (0 = nenhum). É o que permite completar um
    texto cortado sem reescrever o que entrou nem duplicar quando o campo já tinha outra coisa antes."""
    for k in range(min(len(campo), len(texto)), 0, -1):
        if campo.endswith(texto[:k]):
            return k
    return 0


async def _escrever(ctx: ToolContext, texto: str, *, clear_first: bool) -> str:
    """Escreve `texto` no campo em foco e diz por onde: `set_text` (de uma vez, ACTION_SET_TEXT) ou `keyboard`.

    O teclado é alternativa, não caminho: só entra quando `set_text` garante que nada foi escrito (`SemCampoEmFoco`),
    e em pedaços que cabem na janela de cada chamada. Qualquer outra falha sobe como veio — a escrita pode ter
    chegado, e digitar por cima duplicaria (efeito disparado, não comprovado)."""
    try:
        await ctx.call(lambda: ctx.io.set_text(texto, clear_first=clear_first))
        return "set_text"
    except SemCampoEmFoco:
        pass
    pedacos = [texto[i:i + PEDACO_DO_TECLADO] for i in range(0, len(texto), PEDACO_DO_TECLADO)] or [""]
    for n, pedaco in enumerate(pedacos):
        await ctx.call(lambda p=pedaco, limpar=clear_first and n == 0: ctx.io.type_text(p, clear_first=limpar))
    return "keyboard"


async def _conferir_digitacao(ctx: ToolContext, texto: str, alvo: UiElement | None, *,
                              clear_first: bool) -> dict[str, object]:
    """Relê o campo depois de escrever e devolve o que DE FATO está nele (execuções r-20260928165254-e31953 e
    r-20260928195344-02ee9e: `mobile: type` deixou 22 dos 125 caracteres no compositor, o resultado dizia 125, o
    guarda de commit barrou o envio e a IA redigitou até estourar o prazo da etapa).

    `typed_chars` é quanto do texto pedido está NO CAMPO, nunca `len(text)`. Sem leitura de tela ou sem campo
    identificável, `verified: False` com o motivo: o `None` de antes o executor não distinguia de sucesso.

    Completa só quando o campo termina com o começo do texto, e REAPLICA a definição do conteúdo inteiro — idempotente:
    mesmo que a primeira escrita chegue atrasada, não duplica — nunca pelo teclado, que é o caminho que corta. Se o
    app transformou o texto (menção, formatação) e ele não aparece nem como começo, não escreve de novo. Se reaplicar
    não mudou o campo (convidado sob pressão, app que recusa), encerra com o motivo em vez de gastar o prazo."""
    if not texto:
        return {"typed_chars": 0}
    pedidos = len(texto)
    if ctx.observe is None:
        return {"typed_chars": 0, "requested_chars": pedidos, "verified": False,
                "reason": "sem leitura da tela para conferir o campo: o texto não foi comprovado"}
    anterior: str | None = None
    for tentativa in range(DIGITACAO_COMPLEMENTOS + 1):
        await ctx.dormir(0.5)
        campo = _campo_digitado(await _ler_depois_do_gesto(ctx), alvo)
        if campo is None:
            return {"typed_chars": 0, "requested_chars": pedidos, "verified": False,
                    "reason": "o campo digitado não foi identificado na tela: o texto não foi comprovado"}
        atual = campo.text or ""
        if texto in atual or (norm_text(texto) and norm_text(texto) in norm_text(atual)):
            saida: dict[str, object] = {"typed_chars": pedidos, "verified": True}
            if tentativa:
                saida["completed_after_cut"] = tentativa
            return saida
        k = _prefixo_no_fim(atual, texto)
        incompleto: dict[str, object] = {"typed_chars": k, "requested_chars": pedidos, "verified": False,
                                         "missing": texto[k:][:80], "field_now": atual[-80:]}
        if k == 0:
            return {**incompleto, "reason": "o campo não tem o texto nem o começo dele (o app pode tê-lo "
                                            "transformado); não escrevi de novo"}
        if atual == anterior:
            return {**incompleto, "reason": "o campo não mudou depois de reaplicar o texto (aparelho lento ou sob "
                                            "pressão, ou o app recusa o texto); não insisti"}
        if tentativa == DIGITACAO_COMPLEMENTOS:
            return {**incompleto, "reason": f"o texto continua incompleto depois de {tentativa} reaplicações"}
        # Com limpeza, o campo tem de ficar com o texto EXATO: o que vem antes do começo casado pode ser a dica do
        # campo vazio ("…para ana.teste...", e o texto ".@fulano"), e mantê-lo gravaria a dica como conteúdo.
        anterior, final = atual, (texto if clear_first else atual[: len(atual) - k] + texto)
        try:
            await ctx.call(lambda: ctx.io.set_text(final, clear_first=True))
        except SemCampoEmFoco:
            return {**incompleto, "reason": "o campo saiu de foco antes de completar; não redigitei pelo teclado"}
    return {"typed_chars": 0, "requested_chars": pedidos, "verified": False}   # inalcançável: o laço sempre devolve


COLLECT_MAX_PAGES = 25


async def _collect(ctx: ToolContext, args: "CollectList") -> ToolOutcome:
    """Coleta determinística: volta ao topo, depois lê e rola até o conteúdo parar de mudar. Os itens são fato
    observado pelo executor (não alegação do modelo); `at_end` diz se a lista foi lida até o fim."""
    el = ctx.tree.by_id(args.element_id)
    if el is None:
        raise DriverError(f"element_id {args.element_id!r} não existe.", effect_possible=False)
    if ctx.observe is None:
        raise DriverError("Coleta indisponível: sem observação rápida da tela.", effect_possible=False)
    x1, y1, x2, y2 = area = el.bounds
    cx, cy, dy = (x1 + x2) // 2, (y1 + y2) // 2, int((y2 - y1) * 0.35)

    def inside(e: UiElement) -> bool:
        return e.bounds[0] >= x1 and e.bounds[1] >= y1 and e.bounds[2] <= x2 and e.bounds[3] <= y2

    async def to_top(tree: UiTree) -> UiTree:
        for _ in range(COLLECT_MAX_PAGES):
            before = _content_in(tree, area)
            await ctx.call(ctx.io.swipe, cx, cy - dy, cx, cy + dy, 450)
            await ctx.dormir(0.6)
            tree = await ctx.observe()                 # type: ignore[misc]
            if _content_in(tree, area) == before:
                break
        return tree

    # Em lista infinita (feed, caixa de entrada), voltar ao topo é "puxar para atualizar": muda o conteúdo.
    tree = await to_top(ctx.tree) if ctx.collect_from_top else ctx.tree
    skip = {t.strip().casefold() for t in args.exclude}
    items: list[str] = []
    pages, at_end, capped = 0, False, False
    teto = ctx.collect_max_items
    while pages < COLLECT_MAX_PAGES:
        pages += 1
        for e in tree.find_selector(args.item_selector):
            text = " ".join((e.text or "").split())
            if text and not e.password and inside(e) and text.casefold() not in skip and text not in items:
                items.append(text)
        if teto is not None and len(items) >= teto:
            # Parar no teto é DECLARADO: quem lê o resultado sabe que a lista continua depois daqui.
            items, capped = items[:teto], True
            break
        before = _content_in(tree, area)
        await ctx.call(ctx.io.swipe, cx, cy + dy, cx, cy - dy, 450)
        await ctx.dormir(0.8)
        tree = await ctx.observe()
        if _content_in(tree, area) == before:
            at_end = True
            break
    if pages > 1 and ctx.collect_from_top:
        await to_top(tree)                             # as próximas etapas partem do topo, como numa tela recém-aberta
    elif pages > 1 and ctx.collect_rewind:
        # Rebobina o que a coleta desceu, sem `to_top`: aqui a lista está numa folha que se FECHA se o arrasto
        # passar do topo. Desfaz no máximo o que foi rolado e para assim que o conteúdo deixa de mudar.
        for _ in range(pages - 1):
            before = _content_in(tree, area)
            await ctx.call(ctx.io.swipe, cx, cy - dy, cx, cy + dy, 450)
            await ctx.dormir(0.6)
            tree = await ctx.observe()                 # type: ignore[misc]
            if _content_in(tree, area) == before:
                break
    # `at_end` continua sendo fato: a lista acabou. `capped` diz que PARAMOS por decisão nossa, e são coisas
    # diferentes — quem lê o resultado (executor, verificador, receita) precisa saber qual das duas aconteceu.
    return ToolOutcome({"items": items, "count": len(items), "pages": pages, "at_end": at_end,
                        "capped": capped, "limit": teto}, el)


def looks_like_commit(el: UiElement | None) -> bool:
    if el is None:
        return False
    return bool(COMMIT_VOCAB.search(" ".join((el.text, el.desc, el.resource_id.rsplit("/", 1)[-1].replace("_", " ")))))


async def execute_tool(ctx: ToolContext, name: str, args: _Args) -> ToolOutcome:
    io = ctx.io
    if isinstance(args, ObserveScreen):
        return ToolOutcome({"ok": True})
    if isinstance(args, FindElement):
        found = ctx.tree.find(text=args.text, resource_id=args.resource_id, desc=args.description)
        return ToolOutcome({"count": len(found), "elements": [e.line(ctx.image_scale) for e in found[:10]]})
    if isinstance(args, VerifyState):
        return ToolOutcome({"visible": {t: ctx.tree.contains_text(t) for t in args.texts[:10]}})
    if isinstance(args, Tap):
        x, y, el = resolve_point(ctx, args.element_id, args.x, args.y)
        await ctx.call(io.tap, x, y)
        # o resultado volta ao modelo no histórico: limites no espaço da imagem; o ponto físico fica rotulado
        return ToolOutcome({"tapped_device_px": [x, y], "element": el.line(ctx.image_scale) if el else None}, el)
    if isinstance(args, LongPress):
        x, y, el = resolve_point(ctx, args.element_id, args.x, args.y)
        await ctx.call(io.long_press, x, y, max(300, min(args.duration_ms, 5000)))
        return ToolOutcome({"long_pressed_device_px": [x, y]}, el)
    if isinstance(args, Drag):
        x1, y1, _ = resolve_point(ctx, None, args.from_x, args.from_y)
        x2, y2, _ = resolve_point(ctx, None, args.to_x, args.to_y)
        await ctx.call(io.swipe, x1, y1, x2, y2, max(100, min(args.duration_ms, 5000)))
        return ToolOutcome({"dragged": [x1, y1, x2, y2]})
    if isinstance(args, Scroll):
        el = None
        if args.element_id:
            el = ctx.tree.by_id(args.element_id)
            if el is None:
                raise DriverError(f"element_id {args.element_id!r} não existe.", effect_possible=False)
        x1, y1, x2, y2 = area = _area_de_rolagem(ctx, el, vertical=args.direction in ("up", "down"))
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        dx, dy = int((x2 - x1) * 0.35), int((y2 - y1) * 0.35)
        moves = {"down": (cx, cy + dy, cx, cy - dy), "up": (cx, cy - dy, cx, cy + dy),
                 "right": (cx + dx, cy, cx - dx, cy), "left": (cx - dx, cy, cx + dx, cy)}
        ix, iy = moves[args.direction][:2]
        # As listas sob o dedo: é por elas que se reconhece, depois, uma janela que entrou por cima.
        recipientes = {(e.class_name, e.resource_id) for e in ctx.tree.elements
                       if e.scrollable and e.bounds[0] <= ix <= e.bounds[2] and e.bounds[1] <= iy <= e.bounds[3]}
        before = _content_in(ctx.tree, area)
        await ctx.call(io.swipe, *moves[args.direction], 450)
        result: dict[str, Any] = {"scrolled": args.direction}
        if ctx.observe is not None:                # fato do aparelho: o conteúdo da área rolada mudou?
            await ctx.dormir(0.8)
            depois = await _ler_depois_do_gesto(ctx)
            motivo = _sobreposicao_nova(ctx.tree, depois, recipientes)
            if motivo:
                # O arrasto virou toque longo (menu de contexto na r-20260928165254-e31953, ~60 s e 4 chamadas de IA
                # para sair dele) ou abriu outra janela. Fecha-se aqui, sem a IA, e a rolagem NÃO aconteceu: dizer
                # `changed=true` (o conteúdo "mudou" porque a janela é outra) era mentir; `at_end=true`, também.
                await ctx.call(io.press_key, "back")
                await ctx.dormir(0.8)
                result.update(changed=False, at_end=False, overlay_dismissed=motivo)
                return ToolOutcome(result)
            changed = _content_in(depois, area) != before
            result.update(changed=changed, at_end=not changed)     # nada mudou = fim da lista nesta direção
        return ToolOutcome(result)
    if isinstance(args, CollectList):
        return await _collect(ctx, args)
    if isinstance(args, FindRow):
        # Quem lê o remetente é o executor (leitor às cegas, triagem do ADR-009): cair aqui é um caminho sem essa conferência.
        raise DriverError("find_row é conferido pelo executor da etapa, não executado no aparelho.",
                          effect_possible=False)
    if isinstance(args, ReadValue):
        # A leitura precisa dos nomes que a ETAPA declara e da triagem (D3), que só o executor conhece: ele a trata
        # antes de chegar aqui. Cair aqui é um caminho novo sem essa conferência — recusa, sem tocar o aparelho.
        raise DriverError("read_value é conferido pelo executor da etapa, não executado no aparelho.",
                          effect_possible=False)
    if isinstance(args, TypeText):
        el = None
        if args.element_id:
            x, y, el = resolve_point(ctx, args.element_id, None, None)
            await ctx.call(io.tap, x, y)
            await ctx.dormir(0.4)
        via = await _escrever(ctx, args.text, clear_first=args.clear_first)
        conferencia = await _conferir_digitacao(ctx, args.text, el, clear_first=args.clear_first)
        # Texto incompleto não se "confirma" com Enter: num chat, isso mandaria a mensagem cortada.
        enter = args.press_enter and conferencia.get("verified") is not False
        if enter:
            await ctx.call(io.press_key, "enter")
        return ToolOutcome({**conferencia, "via": via, "enter": enter}, el)
    if isinstance(args, TypeSecret):
        if ctx.fill_secret is None:
            raise DriverError("Este aparelho não tem perfil com contas; não há senha de conta a digitar.",
                              effect_possible=False)
        recibo = await ctx.fill_secret(args.name, args.element_id)
        return ToolOutcome({"typed_secret": args.name, **recibo})
    if isinstance(args, OpenUrl):
        url = args.url.strip()
        # Endereço lido na tela é dado não confiável (UNTRUSTED_RULE): abrir só o que a pessoa escreveu. Aspa e
        # espaço ficam fora porque o endereço vai para o `am start` numa linha de shell do aparelho.
        if not url_abrivel(url):
            raise DriverError(f"Endereço {url[:80]!r} não é http/https válido.", effect_possible=False)
        if (url.rstrip("/") not in {u.rstrip("/") for u in ctx.allowed_urls}
                and not _no_site_da_conta(url, ctx.allowed_hosts)):
            raise DriverError("Só é possível abrir endereço escrito no comando"
                              + (" ou do site de uma conta da persona" if ctx.allowed_hosts else "") + ": "
                              + (", ".join(sorted(ctx.allowed_urls | ctx.allowed_hosts)) or "nenhum nesta execução"),
                              effect_possible=False)
        await ctx.call(io.open_url, url)
        await ctx.dormir(2.0)
        return ToolOutcome({"opened_url": url})
    if isinstance(args, PressBack):
        await ctx.call(io.press_key, "back")
        return ToolOutcome({"pressed": "back"})
    if isinstance(args, PressHome):
        await ctx.call(io.press_key, "home")
        return ToolOutcome({"pressed": "home"})
    if isinstance(args, OpenApp):
        package = args.package or ctx.app_package
        if not package:
            raise DriverError("Nenhum aplicativo alvo configurado para esta execução.", effect_possible=False)
        if package not in ctx.allowed_packages:
            raise DriverError(f"Pacote {package!r} não está entre os apps configurados.", effect_possible=False)
        activity = ctx.app_activity if package == ctx.app_package else None
        await ctx.call(io.open_app, package, activity)
        # Antes era dormir 1,5 s e dizer "abriu": numa partida a frio de 28–51 s a IA via o launcher, reabria, e o
        # app morria de novo por ANR (r-20260928195344-02ee9e). Agora o resultado diz se ele CHEGOU à frente.
        focused = await esperar_foco(lambda: ctx.call(io.current_focus), package, ate=ctx.deadline)
        return ToolOutcome({"opened": package, "focused": focused})
    if isinstance(args, WaitFor):
        total = max(0.5, min(float(args.seconds), 15.0))
        if not args.text or ctx.observe is None:
            await ctx.dormir(total)
            return ToolOutcome({"waited_s": total})
        # 31.74: o prazo conta o SONO e a LEITURA da árvore. Antes contava só o sono: com 8 s pedidos, as 8 leituras do
        # Chrome a ~4 s cada fizeram o `wait_for` durar 40,9 s (r-20261005071303-f24955). O sono conta pelo valor pedido
        # (o `dormir` da suíte pula o tempo); a leitura, pelo relógio. Depois do último sono vem uma última leitura, então a
        # parede não passa do pedido mais uma leitura.
        dormido = lendo = 0.0
        while True:
            t0 = _relogio()
            tree = await ctx.observe()
            lendo += _relogio() - t0
            if tree.contains_text(args.text):
                return ToolOutcome({"found": True, "waited_s": round(dormido + lendo, 1)})
            restante = total - dormido - lendo
            if restante <= 0:
                return ToolOutcome({"found": False, "waited_s": round(dormido + lendo, 1)})
            pausa = min(1.0, restante)
            await ctx.dormir(pausa)
            dormido += pausa
    raise DriverError(f"Ferramenta {name} não é executável.", effect_possible=False)
