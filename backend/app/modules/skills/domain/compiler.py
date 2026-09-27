"""`SkillCompiler`: documento `automation/v1alpha1` → validações → IR (design §12.1–12.6; ADR-033, proposto).

Determinístico, sem I/O e sem IA: o documento (inclusive o que o LLM propôs) é DADO validado contra o esquema, e
nenhum campo é avaliado como código. Expressões são só as da §12.2, reconhecidas por gramática fechada; qualquer
outra coisa é erro com código, caminho e mensagem — nunca exceção.

Dois momentos de compilação (§12.1):

- sem valores (`parameters=None`), em `draft → candidate`: todos os ramos do `when`, para achar erros;
- com os parâmetros ligados, na RESOLVE de cada execução: o `when` é avaliado e os nós omitidos saem.

O que o compilador precisa do mundo chega por duas portas definidas AQUI, do lado de quem consome: `CapabilityLookup`
(o catálogo, que `modules/capabilities/infrastructure` implementa por estrutura) e `SkillLookup` (as versões fixadas
das skills compostas). A baixa para o `Plan` de hoje fica na infraestrutura (`lowering.py`), porque `Plan` e
`build_step` são legado e a regra D2 os esconde do domínio.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from app.contracts.skills.v1alpha1 import NODE_ID, NodeSpec, ParameterSpec, SkillDocument
from app.modules.capabilities.domain.definition import CapabilityDefinition
from app.modules.capabilities.domain.strategy import DEFAULT_STRATEGIES, STRATEGIES_OF_A_NODE, StrategyKind

from .errors import Code, CompileIssue, Severity, pointer
from .ir import (GoalContract, ItemRef, Lit, LockEntry, NodeKind, OutputDecl, ParameterDecl, ParamRef, ProcessGraph,
                 ProcessNode, ResourceDecl, Segment, TextExpr, WhenCondition, WhenOp)

#: Variáveis que o runtime resolve sozinho (`taskqueue/flows.py::RESERVED`): um parâmetro com esse nome brigaria
#: com elas no `materialize`.
RESERVED = frozenset({"instance_id", "run_id", "account_label"})
MAX_COMPOSITION_DEPTH = 3
#: Teto de itens de uma coleta (`config.py`, `for_each_max_items`, `le=200`): as cópias do bloco são simuladas até
#: aqui para achar colisão de id ANTES de a execução descobrir.
MAX_FOREACH_ITEMS = 200
NODE_ID_MAX = 41
DELIVERY_ORDER = ("none", "appeared", "sent", "delivered", "read")

_NODE_ID = re.compile(NODE_ID)
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_RAW = re.compile(r"\{[A-Za-z_][A-Za-z0-9_.]*\}")
_EXPR = re.compile(r"\$\{([^{}]*)\}")
_PARAM_REF = re.compile(r"^parameters\.([a-z_][a-z0-9_]*)$")
_WHEN = re.compile(r"^\s*\$\{\s*parameters\.([a-z_][a-z0-9_]*)\s*\}\s*(?:(==|!=)\s*'([^']*)')?\s*$")
_STEP_OUTPUT = re.compile(r"^\s*\$\{\s*steps\.([a-z][a-z0-9_]*)\.output\s*\}\s*$")
#: Nome de parâmetro com cara de credencial. As palavras são as de `security/redaction.py` (o teste confere), mas por
#: TOKEN do nome: `opiniao` contém "pin" e não é segredo.
_SECRET_NAME = re.compile(r"(?:^|_)(?:password|passwd|senha|pin|secret|segredo|token|credential|credencial|otp|2fa)"
                          r"(?:_|$)|(?:api|master|access|private|secret)_?key")


# ------------------------------------------------------------------ portas (do lado de quem consome)
class CapabilityLookup(Protocol):
    def app_known(self, app_id: str) -> bool: ...

    def has_catalog(self, app_id: str) -> bool: ...

    def definition(self, app_id: str, key: str) -> CapabilityDefinition | None: ...

    def offered(self, app_id: str) -> list[CapabilityDefinition]: ...


@dataclass(frozen=True, slots=True)
class LockedSkill:
    """Uma versão exata de skill, com o hash do conteúdo — o que entra em `provenance.uses_lock`."""

    document: SkillDocument
    content_hash: str


class SkillLookup(Protocol):
    def locked(self, skill_id: str, version: int) -> LockedSkill | None: ...


@dataclass(frozen=True, slots=True)
class CompileResult:
    graph: ProcessGraph | None
    issues: tuple[CompileIssue, ...]

    @property
    def errors(self) -> tuple[CompileIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.error)

    @property
    def warnings(self) -> tuple[CompileIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.warning)

    @property
    def ok(self) -> bool:
        return self.graph is not None and not self.errors


def match_key(command_template: str) -> str:
    """A mesma normalização de `flows.match_key` (`taskqueue/flows.py::_norm`): NFKC, espaços colapsados, sem caixa."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", command_template)).strip().casefold()


class SkillCompiler:
    def __init__(self, capabilities: CapabilityLookup, skills: SkillLookup | None = None) -> None:
        self._caps = capabilities
        self._skills = skills

    def compilar(self, documento: Mapping[str, object] | SkillDocument, *, version: int,
                 parameters: Mapping[str, str] | None = None,
                 occupied_match_keys: Collection[str] = ()) -> CompileResult:
        """`occupied_match_keys`: os comandos já publicados (skills e fluxos ativos), para `E_DUPLICATE_COMMAND` na
        publicação. Quem os conhece é o registro de skills; fora da publicação, fica vazio."""
        issues: list[CompileIssue] = []
        doc = documento if isinstance(documento, SkillDocument) else parse_document(documento, issues)
        grafo = (None if doc is None else
                 _Compilacao(self._caps, self._skills, issues).executar(doc, version, parameters, occupied_match_keys))
        erro = any(i.severity is Severity.error for i in issues)
        # O mesmo problema visto por dois caminhos (o app do documento conferido na raiz e em cada nó, um valor que
        # falha nos dois lados de uma união do esquema) sai uma vez só.
        return CompileResult(None if erro else grafo, tuple(dict.fromkeys(issues)))


# ------------------------------------------------------------------ esquema
def parse_document(bruto: object, issues: list[CompileIssue]) -> SkillDocument | None:
    """Mapa (JSON ou YAML já lido) → `SkillDocument`. O erro do pydantic vira `E_*` com o ponteiro do campo."""
    if not isinstance(bruto, Mapping):
        issues.append(CompileIssue(Code.E_SCHEMA, "o documento precisa ser um objeto (apiVersion, kind, metadata, "
                                                  "spec)", ""))
        return None
    try:
        return SkillDocument.model_validate(dict(bruto))
    except ValidationError as exc:
        for e in exc.errors():
            loc = tuple(e["loc"])
            contexto: Mapping[str, object] = e.get("ctx") or {}
            codigo = _codigo_de_esquema(loc, e["type"])
            mensagem = _MENSAGEM_POR_CODIGO.get(codigo) or _mensagem_de_esquema(e["type"], e["msg"], contexto)
            issues.append(CompileIssue(codigo, mensagem, pointer(*loc)))
        return None


#: Quando o código já diz mais que o erro de campo do pydantic, a mensagem é a do código.
_MENSAGEM_POR_CODIGO: dict[Code, str] = {
    Code.E_VERIFICATION_WEAKENED: "prova local é só do catálogo, que é código revisado: declarada no documento ela "
                                  "dispensaria o verificador",
    Code.E_FIELD_RESERVED: "reservado na v1alpha1: policy só aceita inherit (a governança do catálogo e do perfil) e "
                           "on_failure só aceita fail; política por nó fica para a v1alpha2",
}


def _codigo_de_esquema(loc: tuple[int | str, ...], tipo: str) -> Code:
    ultimo = loc[-1] if loc else ""
    if loc[:1] == ("apiVersion",):
        return Code.E_API_VERSION
    if loc[:1] == ("kind",):
        return Code.E_KIND
    if tipo == "extra_forbidden" and ultimo == "local_proof":
        return Code.E_VERIFICATION_WEAKENED
    if ultimo in ("policy", "on_failure") or (tipo == "extra_forbidden" and ultimo in ("approval", "approvals")):
        return Code.E_FIELD_RESERVED
    if tipo == "string_pattern_mismatch" and loc == ("metadata", "id"):
        return Code.E_ID_FORMAT
    if tipo == "string_pattern_mismatch" and len(loc) == 4 and loc[:2] == ("spec", "nodes") and ultimo == "id":
        return Code.E_NODE_ID_FORMAT
    if tipo == "literal_error" and "strategies" in loc:
        return Code.E_STRATEGY_UNKNOWN
    if tipo == "literal_error" and loc[:2] == ("spec", "resources") and ultimo == "kind":
        return Code.E_RESOURCE_UNKNOWN_KIND
    if len(loc) == 4 and loc[:2] == ("spec", "uses") and ultimo == "version":
        return Code.E_SKILL_VERSION_UNPINNED
    return Code.E_SCHEMA


def _mensagem_de_esquema(tipo: str, original: str, ctx: Mapping[str, object]) -> str:
    fixas = {
        "missing": "campo obrigatório ausente",
        "extra_forbidden": "campo fora do esquema automation/v1alpha1",
        "string_type": "esperado texto",
        "int_type": "esperado número inteiro", "int_parsing": "esperado número inteiro",
        "bool_type": "esperado verdadeiro ou falso", "bool_parsing": "esperado verdadeiro ou falso",
        "list_type": "esperada uma lista",
        "dict_type": "esperado um objeto", "model_type": "esperado um objeto",
    }
    if tipo in fixas:
        return fixas[tipo]
    com_contexto = {
        "string_pattern_mismatch": ("valor fora do padrão {}", "pattern"),
        "literal_error": ("valor não permitido; aceitos: {}", "expected"),
        "greater_than_equal": ("valor abaixo do mínimo ({})", "ge"),
        "less_than_equal": ("valor acima do máximo ({})", "le"),
        "too_short": ("curto demais (mínimo {})", "min_length"),
        "string_too_short": ("curto demais (mínimo {})", "min_length"),
        "too_long": ("longo demais (máximo {})", "max_length"),
        "string_too_long": ("longo demais (máximo {})", "max_length"),
    }
    if tipo in com_contexto and com_contexto[tipo][1] in ctx:
        modelo, chave = com_contexto[tipo]
        return modelo.format(str(ctx[chave]).replace("' or '", "' ou '"))
    return f"valor inválido ({tipo}): {original}"


# ------------------------------------------------------------------ compilação
@dataclass(frozen=True, slots=True)
class _Escopo:
    """Onde um documento está sendo expandido: a raiz, ou uma skill chamada por um nó `skill:`."""

    skill_id: str
    version: int
    declarados: Mapping[str, ParameterSpec]
    #: `None` = raiz: `${parameters.x}` vira parâmetro do plano. No filho, vira a EXPRESSÃO que a chamadora passou.
    argumentos: Mapping[str, TextExpr] | None
    prefixo: str
    item: bool                               # `${item}` vale em todo nó (a chamada tem `foreach`)
    quando: tuple[WhenCondition, ...]        # condições herdadas da chamada
    for_each: str | None                     # coleta herdada da chamada (id qualificado)
    deps_raiz: tuple[str, ...]               # o que as raízes deste documento herdam da chamada
    profundidade: int
    pilha: tuple[str, ...]                   # skills em expansão, da raiz até esta
    ponteiro_da_chamada: str | None          # erro no filho aponta para o nó de chamada da raiz
    rotulo: str


@dataclass(frozen=True, slots=True)
class _NoLocal:
    """O que um nó do documento virou, para quem depende dele."""

    sumidouros: tuple[str, ...]              # ele mesmo, ou os sumidouros da skill chamada
    coleta: str | None                       # id qualificado, quando é uma coleta do catálogo


def _texto_do_valor(valor: str | int | bool) -> str:
    return ("true" if valor else "false") if isinstance(valor, bool) else str(valor)


def _copy_key(key: str, n: int) -> str:
    """A chave da n-ésima cópia de um bloco `for_each` — a mesma conta de `taskqueue/foreach.py::_copy_key`."""
    sufixo = f"_i{n}"
    return key[:NODE_ID_MAX - len(sufixo)] + sufixo


def _alcanca(origem: str, alvo: str, grafo: Mapping[str, tuple[str, ...]]) -> bool:
    vistos: set[str] = set()
    pilha = [origem]
    while pilha:
        atual = pilha.pop()
        if atual == alvo:
            return True
        if atual in vistos:
            continue
        vistos.add(atual)
        pilha.extend(grafo.get(atual, ()))
    return False


class _Compilacao:
    def __init__(self, caps: CapabilityLookup, skills: SkillLookup | None, issues: list[CompileIssue]) -> None:
        self.caps = caps
        self.skills = skills
        self.issues = issues
        self.nos: list[ProcessNode] = []
        self.caminho_do_no: dict[str, str] = {}
        self.definicao_do_no: dict[str, CapabilityDefinition] = {}
        self.usados: set[str] = set()
        self.apps: list[str] = []
        self.segredos: list[str] = []
        self.recursos: dict[tuple[str, str | None], ResourceDecl] = {}
        self.travas: dict[tuple[str, int], LockEntry] = {}

    # -------------------------------------------------------------- erros
    def _erro(self, esc: _Escopo, code: Code, msg: str, *local: str | int) -> None:
        if esc.ponteiro_da_chamada is None:
            self.issues.append(CompileIssue(code, msg, pointer(*local)))
        else:
            self.issues.append(CompileIssue(code, f"em {esc.rotulo}, {pointer(*local)}: {msg}",
                                            esc.ponteiro_da_chamada))

    # -------------------------------------------------------------- raiz
    def executar(self, doc: SkillDocument, version: int, parametros: Mapping[str, str] | None,
                 ocupados: Collection[str]) -> ProcessGraph | None:
        esc = _Escopo(skill_id=doc.metadata.id, version=version,
                      declarados={p.name: p for p in doc.spec.parameters}, argumentos=None, prefixo="", item=False,
                      quando=(), for_each=None, deps_raiz=(), profundidade=0, pilha=(doc.metadata.id,),
                      ponteiro_da_chamada=None, rotulo=f"{doc.metadata.id}@{version}")
        self._documento_raiz(doc, esc, ocupados)
        self._requisitos(doc, esc)
        self._expandir(doc, esc)
        self._grafo()
        saidas = self._saidas(doc)
        criterios = tuple(self._texto(esc, c, ("spec", "success_criteria", i), item=False)
                          for i, c in enumerate(doc.spec.success_criteria))
        for i, p in enumerate(doc.spec.parameters):
            if p.name not in self.usados:
                self.issues.append(CompileIssue(Code.W_PARAMETER_UNUSED,
                                                f"o parâmetro '{p.name}' não é usado por nenhum nó",
                                                pointer("spec", "parameters", i)))
        argumentos: tuple[tuple[str, str], ...] | None = None
        nos = tuple(self.nos)
        if parametros is not None:
            valores = self._ligar(doc, parametros)
            argumentos = tuple(valores.items())
            nos = tuple(n for n in self.nos if all(c.holds(valores) for c in n.when))
            incluidos = {n.node_id for n in nos}
            saidas = tuple(o for o in saidas if o.node_id in incluidos)
        return ProcessGraph(
            skill_id=doc.metadata.id, skill_version=version, name=doc.metadata.name, app=doc.metadata.app,
            command_template=doc.spec.invocation.command_template,
            parameters=tuple(ParameterDecl(p.name, p.type, p.required,
                                           None if p.default is None else _texto_do_valor(p.default))
                             for p in doc.spec.parameters),
            arguments=argumentos, required_apps=tuple(self.apps), secrets=tuple(self.segredos),
            resources=tuple(self.recursos.values()), nodes=nos, outputs=saidas, success_criteria=criterios,
            uses_lock=tuple(self.travas.values()))

    def _documento_raiz(self, doc: SkillDocument, esc: _Escopo, ocupados: Collection[str]) -> None:
        spec = doc.spec
        if not self.caps.app_known(doc.metadata.app):
            self._erro(esc, Code.E_APP_UNKNOWN, f"app '{doc.metadata.app}' não está cadastrado", "metadata", "app")
        nomes: set[str] = set()
        for i, p in enumerate(spec.parameters):
            base = ("spec", "parameters", i)
            if p.name in nomes:
                self._erro(esc, Code.E_SCHEMA, f"parâmetro '{p.name}' declarado duas vezes", *base, "name")
            nomes.add(p.name)
            if p.name in RESERVED:
                self._erro(esc, Code.E_COMMAND_RESERVED, f"'{p.name}' é variável do runtime, não parâmetro "
                                                         f"(reservados: {', '.join(sorted(RESERVED))})", *base, "name")
            if _SECRET_NAME.search(p.name):
                self._erro(esc, Code.E_SECRET_PARAMETER,
                           f"'{p.name}' tem cara de credencial. Segredo não é parâmetro: declare o nome em "
                           "requires.secrets; o valor vem do campo Credenciais da execução, que vai para o cofre "
                           "(ADR-025)", *base, "name")
            if p.type == "enum" and not p.values:
                self._erro(esc, Code.E_SCHEMA, "parâmetro enum precisa de `values`", *base, "values")
            if p.type == "enum" and p.default is not None and _texto_do_valor(p.default) not in (p.values or []):
                self._erro(esc, Code.E_SCHEMA, "o padrão de um enum precisa estar em `values`", *base, "default")
            if p.default is not None and (
                    (p.type == "boolean" and not isinstance(p.default, bool))
                    or (p.type == "integer" and (isinstance(p.default, bool) or not isinstance(p.default, int)))):
                self._erro(esc, Code.E_SCHEMA, f"padrão incompatível com o tipo {p.type}", *base, "default")
            if p.pattern is not None:
                try:
                    re.compile(p.pattern)
                except re.error as exc:
                    self._erro(esc, Code.E_SCHEMA, f"padrão (regex) inválido: {exc}", *base, "pattern")
            if not p.example:
                self.issues.append(CompileIssue(Code.W_PARAMETER_NO_EXAMPLE,
                                                f"o parâmetro '{p.name}' não tem exemplo: o painel e o resolvedor "
                                                "de intenção usam o exemplo", pointer(*base)))
        comando = spec.invocation.command_template
        caminho = ("spec", "invocation", "command_template")
        if "${" in comando:
            self._erro(esc, Code.E_EXPRESSION, "o comando usa {parametro}, não ${...}", *caminho)
        if re.search(r"\}\s*\{", comando):
            self._erro(esc, Code.E_COMMAND_AMBIGUOUS, "dois parâmetros colados no comando (ex.: “{a} {b}”): coloque "
                                                      "uma palavra fixa entre eles, senão não dá para separar os "
                                                      "valores", *caminho)
        no_comando = _PLACEHOLDER.findall(comando)
        for nome in no_comando:
            if nome in RESERVED:
                self._erro(esc, Code.E_COMMAND_RESERVED, f"'{{{nome}}}' é variável do runtime", *caminho)
            elif nome not in nomes:
                self._erro(esc, Code.E_COMMAND_PARAMETER_MISMATCH,
                           f"'{{{nome}}}' no comando não é parâmetro declarado", *caminho)
        for i, p in enumerate(spec.parameters):
            if p.required and p.default is None and p.name not in no_comando:
                self._erro(esc, Code.E_COMMAND_PARAMETER_MISMATCH,
                           f"'{p.name}' é obrigatório e não aparece no comando: de onde viria o valor?",
                           "spec", "parameters", i)
        if match_key(comando) in {match_key(k) for k in ocupados}:
            self._erro(esc, Code.E_DUPLICATE_COMMAND, "já existe skill publicada ou fluxo ativo com este comando",
                       *caminho)
        if not spec.validation.cases:
            self.issues.append(CompileIssue(Code.W_NO_VALIDATION_CASE, "sem caso de validação: a versão não chega a "
                                                                       "`validated`", pointer("spec", "validation")))
        for i, caso in enumerate(spec.validation.cases):
            for chave in caso.parameters:
                local = ("spec", "validation", "cases", i, "parameters", chave)
                if _SECRET_NAME.search(chave):
                    self._erro(esc, Code.E_SECRET_PARAMETER, "caso de validação não guarda credencial", *local)
                elif chave not in nomes:
                    self._erro(esc, Code.E_UNKNOWN_PARAMETER, f"'{chave}' não é parâmetro desta skill", *local)

    def _requisitos(self, doc: SkillDocument, esc: _Escopo) -> None:
        req = doc.spec.requires
        for i, app in enumerate(req.apps):
            if not self.caps.app_known(app):
                self._erro(esc, Code.E_APP_UNKNOWN, f"app '{app}' não está cadastrado", "spec", "requires", "apps", i)
            elif app not in self.apps:
                self.apps.append(app)
        for nome in req.secrets:
            if nome not in self.segredos:
                self.segredos.append(nome)
        for i, r in enumerate(doc.spec.resources):
            decl = ResourceDecl(r.kind, r.target, r.desired if isinstance(r.desired, str)
                                else tuple(sorted(r.desired.items())), r.on_missing)
            anterior = self.recursos.setdefault((r.kind, r.target), decl)
            if anterior != decl:
                self._erro(esc, Code.E_RESOURCE_CONFLICT,
                           f"o recurso {r.kind} ({r.target or 'aparelho'}) já foi pedido com outro estado desejado",
                           "spec", "resources", i)

    # -------------------------------------------------------------- expansão
    def _expandir(self, doc: SkillDocument, esc: _Escopo) -> tuple[str, ...]:
        """Expande os nós do documento em `self.nos`. Devolve os sumidouros (ids qualificados)."""
        nodes = doc.spec.nodes
        posicao: dict[str, int] = {}
        duplicados: set[int] = set()
        for i, n in enumerate(nodes):
            if n.id in posicao:
                self._erro(esc, Code.E_NODE_ID_DUPLICATE, f"id '{n.id}' repetido", "spec", "nodes", i, "id")
                duplicados.add(i)
            else:
                posicao[n.id] = i
        # Omitido = depende do anterior; `[]` explícito = independente. Daqui para a frente a lista é SEMPRE explícita.
        deps_locais = [((nodes[i - 1].id,) if i else ()) if n.depends_on is None else tuple(dict.fromkeys(n.depends_on))
                       for i, n in enumerate(nodes)]
        grafo_local = {n.id: deps_locais[i] for i, n in enumerate(nodes) if i not in duplicados}
        feitos: dict[str, _NoLocal] = {}
        dependidos: set[str] = set()
        ordem: list[str] = []
        for i, n in enumerate(nodes):
            if i in duplicados:
                continue
            base = ("spec", "nodes", i)
            validos: list[str] = []
            for d in deps_locais[i]:
                if d not in posicao:
                    self._erro(esc, Code.E_DEPENDENCY_UNKNOWN, f"'{d}' não é nó desta skill", *base, "depends_on")
                elif posicao[d] >= i:
                    if _alcanca(d, n.id, grafo_local):
                        self._erro(esc, Code.E_DEPENDENCY_CYCLE, f"'{n.id}' e '{d}' dependem um do outro",
                                   *base, "depends_on")
                    else:
                        self._erro(esc, Code.E_DEPENDENCY_UNKNOWN, f"'{d}' vem depois de '{n.id}': declare os nós "
                                                                   "na ordem em que rodam", *base, "depends_on")
                elif d in feitos:
                    validos.append(d)
            dependidos.update(validos)
            deps_q = (tuple(dict.fromkeys(q for d in validos for q in feitos[d].sumidouros)) if deps_locais[i]
                      else esc.deps_raiz)
            quando = esc.quando
            if n.when is not None:
                condicao = self._quando(esc, n.when, (*base, "when"))
                if condicao is not None and condicao not in quando:
                    quando = (*quando, condicao)
            for_each = esc.for_each
            if n.foreach is not None:
                for_each = self._fonte_do_foreach(esc, n, base, feitos) or for_each
            item = esc.item or n.foreach is not None
            qualificado = esc.prefixo + n.id
            tipos = [k for k, v in (("capability", n.capability), ("goal", n.goal), ("skill", n.skill)) if v is not None]
            if len(tipos) != 1:
                self._erro(esc, Code.E_SCHEMA, "o nó precisa de exatamente um de capability, goal ou skill "
                                               f"(veio: {', '.join(tipos) or 'nenhum'})", *base)
                feitos[n.id] = _NoLocal((qualificado,), None)
            elif n.skill is not None:
                sumidouros = self._chamada(doc, esc, i, n, deps_q, quando, for_each, item)
                feitos[n.id] = _NoLocal(sumidouros or (qualificado,), None)
            else:
                construido = (self._no_capability(doc, esc, i, n, deps_q, quando, for_each, item)
                              if n.capability is not None
                              else self._no_goal(doc, esc, i, n, deps_q, quando, for_each, item))
                coleta = None
                if construido is not None:
                    no, definicao = construido
                    self._acrescentar(esc, no, base, definicao)
                    coleta = no.node_id if no.collects else None
                feitos[n.id] = _NoLocal((qualificado,), coleta)
            ordem.append(n.id)
        return tuple(q for local in ordem if local not in dependidos for q in feitos[local].sumidouros)

    def _fonte_do_foreach(self, esc: _Escopo, n: NodeSpec, base: tuple[str | int, ...],
                          feitos: Mapping[str, _NoLocal]) -> str | None:
        m = _STEP_OUTPUT.match(n.foreach or "")
        if m is None:
            self._erro(esc, Code.E_FOREACH_SOURCE, "foreach precisa ser ${steps.<coleta>.output}", *base, "foreach")
            return None
        if esc.for_each is not None:
            self._erro(esc, Code.E_FOREACH_SOURCE, "foreach dentro de uma skill chamada por foreach: repetição "
                                                   "aninhada não existe na v1alpha1", *base, "foreach")
            return None
        fonte = feitos.get(m.group(1))
        if fonte is None or fonte.coleta is None:
            self._erro(esc, Code.E_FOREACH_SOURCE, f"'{m.group(1)}' precisa ser uma coleta anterior (capability que "
                                                   "lê uma lista da tela)", *base, "foreach")
            return None
        return fonte.coleta

    def _acrescentar(self, esc: _Escopo, no: ProcessNode, base: tuple[str | int, ...],
                     definicao: CapabilityDefinition | None) -> None:
        if len(no.node_id) > NODE_ID_MAX or not _NODE_ID.match(no.node_id):
            self._erro(esc, Code.E_NODE_ID_TOO_LONG, f"o id expandido '{no.node_id}' passa de {NODE_ID_MAX} "
                                                     "caracteres: encurte o id do nó de chamada ou do filho", *base)
            return
        caminho = esc.ponteiro_da_chamada or pointer(*base)
        if no.node_id in self.caminho_do_no:
            self._erro(esc, Code.E_NODE_ID_COLLISION, f"o id expandido '{no.node_id}' repete o de outro nó", *base)
            return
        self.caminho_do_no[no.node_id] = caminho
        if definicao is not None:
            self.definicao_do_no[no.node_id] = definicao
        # Todo app em que algum nó roda é exigido: com `required_apps` preenchido, o pré-voo deixa de supor o
        # `app_id` do plano (`service.py`, `apps_exigidos`), então o app principal também precisa estar na lista.
        if no.app not in self.apps:
            self.apps.append(no.app)
        self.nos.append(no)

    def _app_do_no(self, doc: SkillDocument, esc: _Escopo, n: NodeSpec, base: tuple[str | int, ...]) -> str | None:
        app = n.app or doc.metadata.app
        if not self.caps.app_known(app):
            local = (*base, "app") if n.app else ("metadata", "app")
            self._erro(esc, Code.E_APP_UNKNOWN, f"app '{app}' não está cadastrado", *local)
            return None
        return app

    def _estrategias(self, esc: _Escopo, n: NodeSpec, base: tuple[str | int, ...],
                     permitidas: tuple[StrategyKind, ...]) -> tuple[StrategyKind, ...]:
        pedidas = (DEFAULT_STRATEGIES if n.strategies is None
                   else tuple(dict.fromkeys(StrategyKind(s) for s in n.strategies)))
        for s in pedidas:
            if s not in permitidas:
                self._erro(esc, Code.E_STRATEGY_UNAVAILABLE,
                           f"a estratégia {s.value} não tem provider para este nó na v1alpha1 "
                           f"(disponíveis: {', '.join(p.value for p in permitidas)})", *base, "strategies")
        return tuple(s for s in pedidas if s in permitidas)

    def _no_capability(self, doc: SkillDocument, esc: _Escopo, i: int, n: NodeSpec, deps: tuple[str, ...],
                       quando: tuple[WhenCondition, ...], for_each: str | None,
                       item: bool) -> tuple[ProcessNode, CapabilityDefinition] | None:
        base = ("spec", "nodes", i)
        app = self._app_do_no(doc, esc, n, base)
        if app is None or n.capability is None:
            return None
        definicao = self.caps.definition(app, n.capability)
        if definicao is None:
            if self.caps.has_catalog(app):
                oferta = ", ".join(d.key for d in self.caps.offered(app))
                self._erro(esc, Code.E_UNKNOWN_CAPABILITY, f"'{n.capability}' não existe no catálogo de {app} "
                                                           f"(disponíveis: {oferta})", *base, "capability")
            else:
                self._erro(esc, Code.E_UNKNOWN_CAPABILITY, f"o app {app} não tem catálogo de capabilities: use um nó "
                                                           "goal", *base, "capability")
            return None
        if definicao.execution.internal:
            self._erro(esc, Code.E_CAPABILITY_INTERNAL, f"{definicao.key} é resolvida por código (login, leitura da "
                                                        "conta), fora do laço da etapa: não entra em skill",
                       *base, "capability")
            return None
        efeito = definicao.side_effect.external
        if n.commit_guard:
            self._erro(esc, Code.E_SCHEMA, "commit_guard é só de nó goal: numa capability vale o do catálogo",
                       *base, "commit_guard")
        if n.verification is not None and n.verification.postcondition is not None:
            self._erro(esc, Code.E_VERIFICATION_WEAKENED, "a pós-condição de uma capability é a do catálogo; o nó só "
                                                          "pode subir o nível de entrega",
                       *base, "verification", "postcondition")
        if n.side_effect is not None and n.side_effect != efeito:
            self._erro(esc, Code.E_SIDE_EFFECT_MISMATCH, f"{definicao.key} {'tem' if efeito else 'não tem'} efeito "
                                                         "externo no catálogo", *base, "side_effect")
        if n.retries and efeito:
            self._erro(esc, Code.E_RETRY_ON_EFFECT, f"{definicao.key} tem efeito externo: uma tentativa só (repetir "
                                                    "duplicaria o efeito)", *base, "retries")
        vinculos: list[tuple[str, TextExpr]] = []
        for chave, valor in sorted(n.with_.items()):
            if chave not in definicao.parameters.accepted:
                aceitos = ", ".join(definicao.parameters.accepted) or "nenhum"
                self._erro(esc, Code.E_UNKNOWN_PARAMETER, f"{definicao.key} não recebe '{chave}' (aceita: {aceitos})",
                           *base, "with", chave)
            else:
                vinculos.append((chave, self._texto(esc, valor, (*base, "with", chave), item=item)))
        ligados = {k for k, v in vinculos if not v.is_blank}
        faltam = [b for b in definicao.parameters.required if b not in ligados]
        if faltam:
            self._erro(esc, Code.E_MISSING_BINDING, f"{definicao.key} exige {', '.join(faltam)}", *base, "with")
        um_de = definicao.parameters.one_of
        if um_de and not ligados.intersection(um_de):
            self._erro(esc, Code.E_MISSING_BINDING, f"{definicao.key} exige {' ou '.join(um_de)}", *base, "with")
        estrategias = self._estrategias(esc, n, base, definicao.execution.strategies)
        if definicao.output.collects and for_each is not None:
            # O `foreach` pode ser do nó ou herdado da chamada (`skill:` com foreach): só aponta o campo se existe.
            local = (*base, "foreach") if n.foreach is not None else base
            self._erro(esc, Code.E_COLLECT_WITH_EFFECT, f"{definicao.key} é coleta: não repete por foreach", *local)
        entrega = n.verification.required_delivery_level if n.verification else None
        return ProcessNode(
            node_id=esc.prefixo + n.id, kind=NodeKind.capability, app=app, skill_id=esc.skill_id,
            skill_version=esc.version, capability=definicao.ref, bindings=tuple(vinculos), depends_on=deps,
            for_each=for_each, when=quando, timeout_s=n.timeout_s, retries=n.retries,
            required_delivery_level=entrega, strategies=estrategias, side_effect=efeito,
            collects=definicao.output.collects), definicao

    def _no_goal(self, doc: SkillDocument, esc: _Escopo, i: int, n: NodeSpec, deps: tuple[str, ...],
                 quando: tuple[WhenCondition, ...], for_each: str | None,
                 item: bool) -> tuple[ProcessNode, None] | None:
        base = ("spec", "nodes", i)
        app = self._app_do_no(doc, esc, n, base)
        if app is None or n.goal is None:
            return None
        efeito = bool(n.side_effect)
        if efeito and self.caps.has_catalog(app):
            self._erro(esc, Code.E_CAPABILITY_REQUIRED,
                       f"etapa com efeito externo em {app} precisa ser a capability do catálogo que ela realiza: sem "
                       "isso passaria por fora da aprovação e dos limites do perfil", *base, "side_effect")
        if n.retries and efeito:
            self._erro(esc, Code.E_RETRY_ON_EFFECT, "etapa com efeito externo tem uma tentativa só", *base, "retries")
        verificacao = n.verification
        pos = verificacao.postcondition if verificacao else None
        if verificacao is None or pos is None:
            self._erro(esc, Code.E_SCHEMA, "nó goal precisa de verification.postcondition: sem pós-condição não há o "
                                           "que comprovar", *base, "verification")
            return None
        g = n.goal
        contrato = GoalContract(
            title=self._texto(esc, g.title, (*base, "goal", "title"), item=item),
            goal=self._texto(esc, g.goal, (*base, "goal", "goal"), item=item),
            precondition=(self._texto(esc, g.precondition, (*base, "goal", "precondition"), item=item)
                          if g.precondition is not None else None),
            post_kind=pos.kind,
            post_value=self._texto(esc, pos.value, (*base, "verification", "postcondition", "value"), item=item),
            post_description=self._texto(esc, pos.description or pos.value,
                                         (*base, "verification", "postcondition", "description"), item=item),
            commit_guard=tuple(self._texto(esc, x, (*base, "commit_guard", j), item=item)
                               for j, x in enumerate(n.commit_guard)))
        vinculos = tuple((k, self._texto(esc, v, (*base, "with", k), item=item)) for k, v in sorted(n.with_.items()))
        niveis = [x for x in (pos.required_delivery_level, verificacao.required_delivery_level) if x is not None]
        entrega = max(niveis, key=DELIVERY_ORDER.index) if niveis else None
        estrategias = self._estrategias(esc, n, base, STRATEGIES_OF_A_NODE)
        return ProcessNode(
            node_id=esc.prefixo + n.id, kind=NodeKind.goal, app=app, skill_id=esc.skill_id, skill_version=esc.version,
            goal=contrato, bindings=vinculos, depends_on=deps, for_each=for_each, when=quando, timeout_s=n.timeout_s,
            retries=n.retries, required_delivery_level=entrega, strategies=estrategias, side_effect=efeito), None

    def _chamada(self, doc: SkillDocument, esc: _Escopo, i: int, n: NodeSpec, deps: tuple[str, ...],
                 quando: tuple[WhenCondition, ...], for_each: str | None, item: bool) -> tuple[str, ...]:
        """Nó `skill:` → os nós do filho, com prefixo, parâmetros substituídos e dependências religadas (§12.3)."""
        base = ("spec", "nodes", i)
        proibidos = [c for c in ("app", "timeout_s", "retries", "strategies", "verification", "side_effect")
                     if getattr(n, c) is not None] + (["commit_guard"] if n.commit_guard else [])
        if proibidos:
            self._erro(esc, Code.E_SCHEMA, f"{', '.join(proibidos)} não se aplica(m) a um nó skill: cada nó da skill "
                                           "chamada traz o seu", *base)
        alvo = n.skill or ""
        versao = next((u.version for u in doc.spec.uses if u.skill == alvo), None)
        if versao is None:
            self._erro(esc, Code.E_SKILL_VERSION_UNPINNED, f"'{alvo}' precisa de uma entrada em spec.uses com a versão "
                                                           "exata", *base, "skill")
            return ()
        if alvo in esc.pilha:
            self._erro(esc, Code.E_SKILL_CYCLE, "composição em ciclo: " + " → ".join((*esc.pilha, alvo)),
                       *base, "skill")
            return ()
        if esc.profundidade + 1 > MAX_COMPOSITION_DEPTH:
            self._erro(esc, Code.E_COMPOSITION_DEPTH, f"composição com mais de {MAX_COMPOSITION_DEPTH} níveis: "
                                                      + " → ".join((*esc.pilha, alvo)), *base, "skill")
            return ()
        travada = self.skills.locked(alvo, versao) if self.skills is not None else None
        if travada is None or travada.document.metadata.id != alvo:
            self._erro(esc, Code.E_SKILL_NOT_FOUND, f"'{alvo}@{versao}' não existe", *base, "skill")
            return ()
        filho = travada.document
        params_filho = {p.name: p for p in filho.spec.parameters}
        argumentos: dict[str, TextExpr] = {}
        chamada_invalida = False
        for chave, valor in sorted(n.with_.items()):
            if chave not in params_filho:
                self._erro(esc, Code.E_UNKNOWN_PARAMETER, f"'{alvo}' não tem o parâmetro '{chave}'", *base, "with",
                           chave)
                chamada_invalida = True
            else:
                # A expressão é lida no escopo da CHAMADORA: `${parameters.contato}` da raiz, ou `${item}`.
                argumentos[chave] = self._texto(esc, valor, (*base, "with", chave), item=item)
        for p in filho.spec.parameters:
            if p.name in argumentos:
                continue
            if p.default is not None:
                argumentos[p.name] = TextExpr.literal(_texto_do_valor(p.default))
            elif p.required:
                self._erro(esc, Code.E_MISSING_ARGUMENT, f"'{alvo}' exige o parâmetro '{p.name}', que não tem valor "
                                                         "padrão", *base, "with")
                chamada_invalida = True
            else:
                argumentos[p.name] = TextExpr()
        if chamada_invalida:
            # Expandir com argumento faltando só repetiria o erro em cada nó do filho (binding vazio).
            return ()
        self.travas.setdefault((alvo, versao), LockEntry(alvo, versao, travada.content_hash))
        filho_esc = _Escopo(skill_id=alvo, version=versao, declarados=params_filho, argumentos=argumentos,
                            prefixo=esc.prefixo + n.id + "_", item=item, quando=quando, for_each=for_each,
                            deps_raiz=deps, profundidade=esc.profundidade + 1, pilha=(*esc.pilha, alvo),
                            ponteiro_da_chamada=esc.ponteiro_da_chamada or pointer(*base), rotulo=f"{alvo}@{versao}")
        self._requisitos(filho, filho_esc)
        return self._expandir(filho, filho_esc)

    # -------------------------------------------------------------- expressões
    def _texto(self, esc: _Escopo, bruto: str, local: tuple[str | int, ...], *, item: bool) -> TextExpr:
        partes: list[Segment] = []
        pos = 0
        for m in _EXPR.finditer(bruto):
            partes.extend(self._literal(esc, bruto[pos:m.start()], local))
            partes.extend(self._referencia(esc, m.group(1).strip(), local, item))
            pos = m.end()
        partes.extend(self._literal(esc, bruto[pos:], local))
        return TextExpr.build(partes)

    def _literal(self, esc: _Escopo, texto: str, local: tuple[str | int, ...]) -> list[Segment]:
        if "${" in texto:
            self._erro(esc, Code.E_EXPRESSION, "expressão malformada: use ${parameters.x}, ${item} ou "
                                               "${steps.<id>.output}", *local)
        else:
            cru = _RAW.search(texto)
            if cru is not None:
                self._erro(esc, Code.E_RAW_PLACEHOLDER, f"'{cru.group(0)}' cru no texto: use ${{parameters.x}} (só o "
                                                        "comando usa {x})", *local)
        return [Lit(texto)]

    def _referencia(self, esc: _Escopo, corpo: str, local: tuple[str | int, ...], item: bool) -> list[Segment]:
        m = _PARAM_REF.match(corpo)
        if m is not None:
            nome = m.group(1)
            if nome not in esc.declarados:
                self._erro(esc, Code.E_UNKNOWN_PARAMETER, f"'{nome}' não é parâmetro desta skill", *local)
                return []
            if esc.argumentos is None:
                self.usados.add(nome)
                return [ParamRef(nome)]
            return list(esc.argumentos.get(nome, TextExpr()).parts)
        if corpo == "item":
            if item:
                return [ItemRef()]
            self._erro(esc, Code.E_EXPRESSION, "${item} só vale num nó com foreach", *local)
            return []
        if corpo == "secrets" or corpo.startswith("secrets."):
            self._erro(esc, Code.E_SECRET_INLINE, "segredo nunca vai no documento: declare o nome em requires.secrets; "
                                                  "o valor vem do campo Credenciais da execução (ADR-025)", *local)
            return []
        if corpo == "steps" or corpo.startswith("steps."):
            self._erro(esc, Code.E_OUTPUT_REF_UNSUPPORTED, "saída de etapa só vale em foreach e em outputs[].from",
                       *local)
            return []
        self._erro(esc, Code.E_EXPRESSION, f"expressão desconhecida: ${{{corpo}}}", *local)
        return []

    def _quando(self, esc: _Escopo, bruto: str, local: tuple[str | int, ...]) -> WhenCondition | None:
        m = _WHEN.match(bruto)
        if m is None:
            self._erro(esc, Code.E_WHEN_UNSUPPORTED, "when aceita só ${parameters.x}, ${parameters.x} == 'valor' ou "
                                                     "${parameters.x} != 'valor'", *local)
            return None
        nome, operador, literal = m.group(1), m.group(2), m.group(3)
        if nome not in esc.declarados:
            self._erro(esc, Code.E_UNKNOWN_PARAMETER, f"'{nome}' não é parâmetro desta skill", *local)
            return None
        operando: ParamRef | Lit
        if esc.argumentos is None:
            self.usados.add(nome)
            operando = ParamRef(nome)
        else:
            arg = esc.argumentos.get(nome, TextExpr())
            refs = [p for p in arg.parts if isinstance(p, ParamRef)]
            if arg.uses_item or (refs and len(arg.parts) > 1):
                self._erro(esc, Code.E_WHEN_UNSUPPORTED, f"o valor de '{nome}' vem de ${{item}} ou mistura texto e "
                                                         "parâmetro: não é estático", *local)
                return None
            operando = refs[0] if refs else Lit(arg.template())
        op = WhenOp.truthy if operador is None else WhenOp.eq if operador == "==" else WhenOp.ne
        return WhenCondition(operando, op, literal)

    # -------------------------------------------------------------- grafo expandido
    def _usa_item(self, no: ProcessNode) -> bool:
        """O que o validador do `Plan` confere: `{item}` no título, objetivo, pós-condição ou guarda do bloco."""
        if no.goal is not None:
            return any(t.uses_item for t in (no.goal.title, no.goal.goal, no.goal.post_value, *no.goal.commit_guard))
        definicao = self.definicao_do_no.get(no.node_id)
        if definicao is None:
            return False
        modelos = (definicao.title, definicao.goal, definicao.postcondition.value, *definicao.side_effect.commit_guard)
        variaveis = {v for t in modelos for v in _PLACEHOLDER.findall(t)}
        return "item" in variaveis or any(v.uses_item for k, v in no.bindings if k in variaveis)

    def _grafo(self) -> None:
        def erro(no: ProcessNode, code: Code, msg: str) -> None:
            self.issues.append(CompileIssue(code, msg, self.caminho_do_no[no.node_id]))

        fixos = {n.node_id for n in self.nos if n.for_each is None}
        dono_da_copia: dict[str, str] = {}
        for n in self.nos:
            if n.for_each is None:
                continue
            for k in range(1, MAX_FOREACH_ITEMS + 1):
                copia = _copy_key(n.node_id, k)
                dono = dono_da_copia.setdefault(copia, n.node_id)
                if copia in fixos or dono != n.node_id:
                    erro(n, Code.E_NODE_ID_COLLISION, f"a cópia '{copia}' do foreach de '{n.node_id}' colide com "
                                                      f"'{copia if copia in fixos else dono}': encurte os ids")
                    break
        encerrados: set[str] = set()
        anterior: str | None = None
        for n in self.nos:
            if anterior is not None and n.for_each != anterior:
                encerrados.add(anterior)
            if n.for_each is not None and n.for_each in encerrados:
                erro(n, Code.E_FOREACH_NOT_CONTIGUOUS, f"os nós com foreach de '{n.for_each}' precisam ser "
                                                       "consecutivos")
                encerrados.discard(n.for_each)
            anterior = n.for_each
        blocos: dict[str, list[ProcessNode]] = {}
        for n in self.nos:
            if n.for_each is not None:
                blocos.setdefault(n.for_each, []).append(n)
        for fonte, bloco in blocos.items():
            if not any(self._usa_item(n) for n in bloco):
                erro(bloco[0], Code.E_FOREACH_NO_ITEM, f"o bloco foreach de '{fonte}' não usa ${{item}} em nenhum "
                                                       "nó: todas as cópias fariam a mesma coisa")
        por_id = {n.node_id: n for n in self.nos}
        for n in self.nos:
            for d in (*n.depends_on, *((n.for_each,) if n.for_each else ())):
                dep = por_id.get(d)
                if dep is not None and not set(dep.when) <= set(n.when):
                    erro(n, Code.E_WHEN_DEPENDENCY, f"'{n.node_id}' depende de '{d}', que pode ser omitido pelo when: "
                                                    "repita a condição")

    def _saidas(self, doc: SkillDocument) -> tuple[OutputDecl, ...]:
        por_id = {n.node_id: n for n in self.nos}
        saidas: list[OutputDecl] = []
        for i, o in enumerate(doc.spec.outputs):
            local = ("spec", "outputs", i)
            if any(s.name == o.name for s in saidas):
                self.issues.append(CompileIssue(Code.E_SCHEMA, f"saída '{o.name}' declarada duas vezes",
                                                pointer(*local, "name")))
                continue
            m = _STEP_OUTPUT.match(o.from_)
            no = por_id.get(m.group(1)) if m is not None else None
            if no is None or not no.collects:
                self.issues.append(CompileIssue(Code.E_OUTPUT_REF_UNSUPPORTED,
                                                "outputs[].from precisa ser ${steps.<coleta>.output} de uma coleta "
                                                "desta skill", pointer(*local, "from")))
                continue
            saidas.append(OutputDecl(o.name, no.node_id))
        return tuple(saidas)

    def _ligar(self, doc: SkillDocument, parametros: Mapping[str, str]) -> dict[str, str]:
        valores: dict[str, str] = {}
        for i, p in enumerate(doc.spec.parameters):
            if p.name in parametros:
                valores[p.name] = parametros[p.name]
            elif p.default is not None:
                valores[p.name] = _texto_do_valor(p.default)
            elif p.required:
                self.issues.append(CompileIssue(Code.E_MISSING_ARGUMENT,
                                                f"a execução não trouxe valor para '{p.name}'",
                                                pointer("spec", "parameters", i)))
            else:
                valores[p.name] = ""
        for chave in sorted(parametros):
            if chave not in valores and all(p.name != chave for p in doc.spec.parameters):
                self.issues.append(CompileIssue(Code.E_UNKNOWN_PARAMETER,
                                                f"'{chave}' não é parâmetro de {doc.metadata.id}", ""))
        return valores
