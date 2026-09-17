"""Ferramentas tipadas que a IA pode pedir. O executor só aceita estes nomes e argumentos
validados por schema; nada que o modelo escreva vira código ou comando de shell."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..models import DeliveryLevel
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
    "open_app": OpenApp, "wait_for": WaitFor, "verify_state": VerifyState,
    "step_done": StepDone, "step_blocked": StepBlocked,
}
CONTROL_TOOLS = {"step_done", "step_blocked"}
EFFECT_CAPABLE = {"tap", "long_press", "drag", "type_text"}     # podem disparar um efeito externo
READ_ONLY = {"observe_screen", "find_element", "wait_for", "verify_state"}
STRICT_TOOLS = EFFECT_CAPABLE | CONTROL_TOOLS


class ToolValidationError(ValueError):
    pass


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
        await ctx.call(io.swipe, *moves[args.direction], 450)
        return ToolOutcome({"scrolled": args.direction})
    if isinstance(args, TypeText):
        el = None
        if args.element_id:
            x, y, el = resolve_point(ctx, args.element_id, None, None)
            await ctx.call(io.tap, x, y)
            await asyncio.sleep(0.4)
        await ctx.call(lambda: io.type_text(args.text, clear_first=args.clear_first))
        if args.press_enter:
            await ctx.call(io.press_key, "enter")
        return ToolOutcome({"typed_chars": len(args.text), "enter": args.press_enter}, el)
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
