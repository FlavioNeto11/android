"""Ferramentas tipadas que a IA pode pedir. O executor só aceita estes nomes e argumentos
validados por schema; nada que o modelo escreva vira código ou comando de shell."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..models import DeliveryLevel
from ..util import norm_text, url_abrivel
from .driver import DeviceIO, DriverError
from .hierarchy import UiElement, UiTree

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


class VerifyState(_Args):
    """Confere, na tela atual, quais dos textos informados estão visíveis."""
    texts: list[str]


class StepDone(_Args):
    """Declara que o objetivo da etapa foi atingido. O executor ainda verifica a pós-condição."""
    evidence: str = Field(description="O que, na tela, comprova a conclusão.")
    delivery_level: DeliveryLevel | None = Field(
        default=None, description="Só para envio de mensagem: nível observado (appeared/sent/delivered/read).")


class StepBlocked(_Args):
    """A etapa não pode prosseguir sem intervenção ou é impossível."""
    kind: Literal["auth_required", "wrong_account", "missing_info", "app_incompatible", "unexpected_screen", "other"]
    reason: str
    needs_user: bool


TOOLS: dict[str, type[_Args]] = {
    "observe_screen": ObserveScreen, "find_element": FindElement, "tap": Tap, "long_press": LongPress,
    "drag": Drag, "scroll": Scroll, "type_text": TypeText, "press_back": PressBack, "press_home": PressHome,
    "open_app": OpenApp, "wait_for": WaitFor, "verify_state": VerifyState, "collect_list": CollectList,
    "step_done": StepDone, "step_blocked": StepBlocked, "type_secret": TypeSecret, "open_url": OpenUrl,
}
CONTROL_TOOLS = {"step_done", "step_blocked"}
EFFECT_CAPABLE = {"tap", "long_press", "drag", "type_text"}     # podem disparar um efeito externo
READ_ONLY = {"observe_screen", "find_element", "wait_for", "verify_state"}
STRICT_TOOLS = EFFECT_CAPABLE | CONTROL_TOOLS


class ToolValidationError(ValueError):
    pass


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
        errs = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:4])
        raise ToolValidationError(f"Argumentos inválidos para {name}: {errs}") from exc


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


#: Quantas vezes a digitação completa o que faltou antes de desistir e dizer ao modelo que o texto está incompleto.
DIGITACAO_COMPLEMENTOS = 2


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


async def _conferir_digitacao(ctx: ToolContext, texto: str, alvo: UiElement | None) -> dict[str, object]:
    """Relê o campo depois de digitar e devolve o que DE FATO entrou (execução e31953: `mobile: type` cortou um
    comentário de 125 caracteres em 22 num aparelho lento, o resultado dizia 125, o guarda de commit barrou o envio e
    a IA redigitou até estourar o prazo da etapa).

    Completa o que faltou só quando o campo termina com o começo do texto pedido — nunca reescreve, nunca duplica.
    Se o app transformou o texto (menção, formatação) e ele não aparece nem como começo, devolve `verified: False`
    sem digitar de novo. Sem leitura de tela (`observe`) ou sem campo identificável, não afirma nada (`None`)."""
    if ctx.observe is None or not texto:
        return {"typed_chars": len(texto)}
    for tentativa in range(DIGITACAO_COMPLEMENTOS + 1):
        await asyncio.sleep(0.5)
        campo = _campo_digitado(await ctx.observe(), alvo)
        if campo is None:
            return {"typed_chars": len(texto), "verified": None}
        atual = campo.text or ""
        if texto in atual or (norm_text(texto) and norm_text(texto) in norm_text(atual)):
            saida: dict[str, object] = {"typed_chars": len(texto), "verified": True}
            if tentativa:
                saida["completed_after_cut"] = tentativa
            return saida
        k = _prefixo_no_fim(atual, texto)
        if k == 0 or tentativa == DIGITACAO_COMPLEMENTOS:
            return {"typed_chars": k, "requested_chars": len(texto), "verified": False,
                    "missing": texto[k:][:80], "field_now": atual[-80:]}
        await ctx.call(lambda resto=texto[k:]: ctx.io.type_text(resto, clear_first=False))
    return {"typed_chars": len(texto), "verified": None}        # inalcançável: o laço sempre devolve


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
            await asyncio.sleep(0.6)
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
        await asyncio.sleep(0.8)
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
            await asyncio.sleep(0.6)
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
        if args.element_id:
            el = ctx.tree.by_id(args.element_id)
            if el is None:
                raise DriverError(f"element_id {args.element_id!r} não existe.", effect_possible=False)
            x1, y1, x2, y2 = el.bounds
        else:
            x1, y1, x2, y2 = 0, int(ctx.height * 0.2), ctx.width, int(ctx.height * 0.85)
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        dx, dy = int((x2 - x1) * 0.35), int((y2 - y1) * 0.35)
        moves = {"down": (cx, cy + dy, cx, cy - dy), "up": (cx, cy - dy, cx, cy + dy),
                 "right": (cx + dx, cy, cx - dx, cy), "left": (cx - dx, cy, cx + dx, cy)}
        before = _content_in(ctx.tree, (x1, y1, x2, y2))
        await ctx.call(io.swipe, *moves[args.direction], 450)
        result: dict[str, Any] = {"scrolled": args.direction}
        if ctx.observe is not None:                # fato do aparelho: o conteúdo da área rolada mudou?
            await asyncio.sleep(0.8)
            changed = _content_in(await ctx.observe(), (x1, y1, x2, y2)) != before
            result.update(changed=changed, at_end=not changed)     # nada mudou = fim da lista nesta direção
        return ToolOutcome(result)
    if isinstance(args, CollectList):
        return await _collect(ctx, args)
    if isinstance(args, TypeText):
        el = None
        if args.element_id:
            x, y, el = resolve_point(ctx, args.element_id, None, None)
            await ctx.call(io.tap, x, y)
            await asyncio.sleep(0.4)
        await ctx.call(lambda: io.type_text(args.text, clear_first=args.clear_first))
        conferencia = await _conferir_digitacao(ctx, args.text, el)
        # Texto incompleto não se "confirma" com Enter: num chat, isso mandaria a mensagem cortada.
        enter = args.press_enter and conferencia.get("verified") is not False
        if enter:
            await ctx.call(io.press_key, "enter")
        return ToolOutcome({**conferencia, "enter": enter}, el)
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
        await asyncio.sleep(2.0)
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
        await asyncio.sleep(1.5)
        return ToolOutcome({"opened": package})
    if isinstance(args, WaitFor):
        total = max(0.5, min(float(args.seconds), 15.0))
        if not args.text or ctx.observe is None:
            await asyncio.sleep(total)
            return ToolOutcome({"waited_s": total})
        waited = 0.0
        while waited < total:
            tree = await ctx.observe()
            if tree.contains_text(args.text):
                return ToolOutcome({"found": True, "waited_s": round(waited, 1)})
            await asyncio.sleep(1.0)
            waited += 1.0
        return ToolOutcome({"found": False, "waited_s": total})
    raise DriverError(f"Ferramenta {name} não é executável.", effect_possible=False)
