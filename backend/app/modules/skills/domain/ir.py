"""IR de skill: o processo que o compilador produz a partir do documento DSL (design §12.1; ADR-032, proposto).

`ProcessGraph` é uma lista ordenada de `ProcessNode`s já expandidos (a composição `uses:` vira nós com id
qualificado) e com as dependências SEMPRE explícitas. Tudo é valor imutável; o hash é o sha256 do JSON canônico, e a
mesma entrada dá o mesmo IR e o mesmo hash — é o que permite conferir, depois, que o plano de uma execução é o que a
versão publicada produz.

A forma canônica e o hash são os de `document.py`, reexportados aqui: o hash do documento que a versão grava, a
trava de composição (`uses_lock`) e o hash do IR e do plano são UMA função. Duas implementações "iguais" divergiriam
no primeiro detalhe (NaN, chave não-texto) e a trava deixaria de bater com a versão travada.

Texto com expressão não é string com marcador: é `TextExpr`, uma sequência de pedaços tipados (literal, parâmetro,
item). A composição troca o parâmetro do filho pela expressão da chamadora pedaço por pedaço, sem reescrever texto
— substituição de DADO, nunca de código. Só na baixa (`infrastructure/lowering.py`) o texto vira o modelo `{x}` que
o `materialize` de hoje resolve.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.modules.capabilities.domain.definition import CapabilityRef
from app.modules.capabilities.domain.strategy import StrategyKind

from .document import JsonValue as Json
from .document import canonical_json as canonical_json     # reexportados: ver a docstring do módulo
from .document import content_hash as content_hash

#: Valores que um `when` sem comparação lê como verdadeiro (os mesmos de `content_verbatim` no catálogo).
TRUTHY = ("true", "1", "sim", "yes", "verdadeiro")


# ------------------------------------------------------------------ texto com expressão
@dataclass(frozen=True, slots=True)
class Lit:
    text: str


@dataclass(frozen=True, slots=True)
class ParamRef:
    name: str


@dataclass(frozen=True, slots=True)
class ItemRef:
    """O item corrente de um bloco `foreach` (vira `{item}`, resolvido pela expansão do `for_each`)."""


type Segment = Lit | ParamRef | ItemRef


@dataclass(frozen=True, slots=True)
class TextExpr:
    parts: tuple[Segment, ...] = ()

    @staticmethod
    def build(parts: Iterable[Segment]) -> TextExpr:
        """Forma normal: literais vizinhos unidos e literais vazios fora. Duas grafias do mesmo texto têm de dar o
        mesmo IR, senão o hash mudaria sem o conteúdo mudar."""
        saida: list[Segment] = []
        for p in parts:
            if isinstance(p, Lit):
                if not p.text:
                    continue
                if saida and isinstance(saida[-1], Lit):
                    saida[-1] = Lit(saida[-1].text + p.text)
                    continue
            saida.append(p)
        return TextExpr(tuple(saida))

    @staticmethod
    def literal(text: str) -> TextExpr:
        return TextExpr.build([Lit(text)])

    def template(self) -> str:
        """O texto no formato de modelo que o runtime de hoje resolve: `{param}` e `{item}`."""
        return "".join(p.text if isinstance(p, Lit) else "{item}" if isinstance(p, ItemRef) else "{" + p.name + "}"
                       for p in self.parts)

    @property
    def params(self) -> frozenset[str]:
        return frozenset(p.name for p in self.parts if isinstance(p, ParamRef))

    @property
    def uses_item(self) -> bool:
        return any(isinstance(p, ItemRef) for p in self.parts)

    @property
    def is_blank(self) -> bool:
        return all(isinstance(p, Lit) and not p.text.strip() for p in self.parts)

    def canonical(self) -> Json:
        return [["lit", p.text] if isinstance(p, Lit) else ["item"] if isinstance(p, ItemRef) else ["param", p.name]
                for p in self.parts]


# ------------------------------------------------------------------ condição estática
class WhenOp(StrEnum):
    truthy = "truthy"
    eq = "eq"
    ne = "ne"


@dataclass(frozen=True, slots=True)
class WhenCondition:
    """`${parameters.x}`, `== 'lit'` ou `!= 'lit'`. Estática: só parâmetro ou literal, nunca `${item}` nem saída de
    etapa, porque é avaliada na compilação de cada execução, antes de qualquer coisa rodar."""

    operand: ParamRef | Lit
    op: WhenOp
    literal: str | None = None

    def holds(self, valores: Mapping[str, str]) -> bool:
        valor = self.operand.text if isinstance(self.operand, Lit) else valores.get(self.operand.name, "")
        if self.op is WhenOp.eq:
            return valor == self.literal
        if self.op is WhenOp.ne:
            return valor != self.literal
        return valor.strip().lower() in TRUTHY

    def canonical(self) -> Json:
        operando: Json = (["lit", self.operand.text] if isinstance(self.operand, Lit)
                          else ["param", self.operand.name])
        return {"operand": operando, "op": self.op.value, "literal": self.literal}


# ------------------------------------------------------------------ nós e grafo
class NodeKind(StrEnum):
    capability = "capability"
    goal = "goal"


@dataclass(frozen=True, slots=True)
class GoalContract:
    """Contrato inline de um nó `goal` (app sem catálogo, ou navegação sem efeito)."""

    title: TextExpr
    goal: TextExpr
    precondition: TextExpr | None
    post_kind: str
    post_value: TextExpr
    post_description: TextExpr
    commit_guard: tuple[TextExpr, ...] = ()

    def canonical(self) -> Json:
        return {"title": self.title.canonical(), "goal": self.goal.canonical(),
                "precondition": self.precondition.canonical() if self.precondition else None,
                "post_kind": self.post_kind, "post_value": self.post_value.canonical(),
                "post_description": self.post_description.canonical(),
                "commit_guard": [g.canonical() for g in self.commit_guard]}


@dataclass(frozen=True, slots=True)
class ProcessNode:
    """Um nó já expandido. `node_id` é qualificado (`abrir_abrir_conversa`) e VIRA `PlanStep.key` (decisão 2)."""

    node_id: str
    kind: NodeKind
    app: str
    skill_id: str                 # a skill dona do nó: o filho, numa composição (trilha `steps.skill_id`)
    skill_version: int
    capability: CapabilityRef | None = None
    goal: GoalContract | None = None
    bindings: tuple[tuple[str, TextExpr], ...] = ()
    depends_on: tuple[str, ...] = ()
    for_each: str | None = None
    when: tuple[WhenCondition, ...] = ()
    timeout_s: int | None = None
    retries: int | None = None
    required_delivery_level: str | None = None
    strategies: tuple[StrategyKind, ...] = ()
    side_effect: bool = False
    collects: bool = False

    def canonical(self) -> Json:
        return {
            "node_id": self.node_id, "kind": self.kind.value, "app": self.app,
            "skill_id": self.skill_id, "skill_version": self.skill_version,
            "capability": (None if self.capability is None else
                           {"app": self.capability.app, "key": self.capability.key,
                            "contract_version": self.capability.contract_version}),
            "goal": self.goal.canonical() if self.goal else None,
            "bindings": {k: v.canonical() for k, v in self.bindings},
            "depends_on": list(self.depends_on), "for_each": self.for_each,
            "when": [c.canonical() for c in self.when],
            "timeout_s": self.timeout_s, "retries": self.retries,
            "required_delivery_level": self.required_delivery_level,
            "strategies": [s.value for s in self.strategies],
            "side_effect": self.side_effect, "collects": self.collects,
        }


@dataclass(frozen=True, slots=True)
class ParameterDecl:
    name: str
    type: str
    required: bool
    #: Já em texto: é o que vai para `Plan.parameters` (`dict[str, str]`).
    default: str | None = None


@dataclass(frozen=True, slots=True)
class ResourceDecl:
    kind: str
    target: str | None
    #: `online` ou um mapa (`{release: promoted}`), como no documento; o mapa em pares ordenados.
    desired: str | tuple[tuple[str, str], ...]
    on_missing: str

    def canonical(self) -> Json:
        desejado: Json = self.desired if isinstance(self.desired, str) else {k: v for k, v in self.desired}
        return {"kind": self.kind, "target": self.target, "desired": desejado, "on_missing": self.on_missing}


@dataclass(frozen=True, slots=True)
class OutputDecl:
    name: str
    node_id: str


@dataclass(frozen=True, slots=True)
class LockEntry:
    """Uma skill composta, com a versão fixada e o hash do conteúdo que foi compilado (`provenance.uses_lock`)."""

    skill: str
    version: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class ProcessGraph:
    skill_id: str
    skill_version: int
    name: str
    app: str
    command_template: str
    parameters: tuple[ParameterDecl, ...]
    #: Valores ligados na compilação de uma execução; `None` = compilação sem valores (todos os ramos do `when`).
    arguments: tuple[tuple[str, str], ...] | None
    required_apps: tuple[str, ...]
    secrets: tuple[str, ...]
    resources: tuple[ResourceDecl, ...]
    nodes: tuple[ProcessNode, ...]
    outputs: tuple[OutputDecl, ...]
    success_criteria: tuple[TextExpr, ...]
    uses_lock: tuple[LockEntry, ...]

    def node(self, node_id: str) -> ProcessNode | None:
        return next((n for n in self.nodes if n.node_id == node_id), None)

    def canonical(self) -> Json:
        return {
            "skill_id": self.skill_id, "skill_version": self.skill_version, "name": self.name, "app": self.app,
            "command_template": self.command_template,
            "parameters": [{"name": p.name, "type": p.type, "required": p.required, "default": p.default}
                           for p in self.parameters],
            "arguments": None if self.arguments is None else {k: v for k, v in self.arguments},
            "required_apps": list(self.required_apps), "secrets": list(self.secrets),
            "resources": [r.canonical() for r in self.resources],
            "nodes": [n.canonical() for n in self.nodes],
            "outputs": [{"name": o.name, "node_id": o.node_id} for o in self.outputs],
            "success_criteria": [c.canonical() for c in self.success_criteria],
            "uses_lock": [{"skill": u.skill, "version": u.version, "content_hash": u.content_hash}
                          for u in self.uses_lock],
        }

    def content_hash(self) -> str:
        return content_hash(self.canonical())
