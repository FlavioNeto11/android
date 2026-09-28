"""Descompilador: o plano congelado de um fluxo (`Plan`) → documento `automation/v1alpha1` (fase J; design §15.2).

Serve à conversão fluxo → habilidade (adopt-on-write): a v1 publicada é o plano do fluxo como está (passagem direta,
`legacy_plan`), e a v2 em rascunho é ESTE documento, que a pessoa edita. A regra que manda aqui é a identidade de
receita: `node_id = PlanStep.key`, e o plano que o compilador produz do documento tem, etapa por etapa, os mesmos
campos que `recipes.step_template_hash` lê (chave, efeito, pós-condição, nível de entrega, guardas). Senão as receitas
aprendidas com o fluxo deixariam de casar, e a conversão custaria IA de novo.

Por isso o descompilador não confia em si: depois de montar o documento, compila-o pelo compilador REAL (o
`SkillPlanCompiler` que a execução usa) duas vezes: sem valores, que é o que a submissão `draft → candidate` verá; e
com valores de amostra, comparado ao plano do fluxo ligado aos MESMOS valores (`bind_template_parameters`, a ligação
do legado). Diferença de identidade ou de comportamento é ERRO, e a conversão é recusada. Diferença de texto que o
catálogo reescreveu desde que o fluxo foi congelado é AVISO: a v2 usa o catálogo de hoje, a v1 continua como o fluxo.
Nada se perde calado.

O que a v1alpha1 não tem como dizer vira erro com o caminho no plano (`/steps/2/goal`):

- variável do runtime (`{instance_id}`, `{run_id}`, `{account_label}`, `{item_index}`) em texto: o `materialize` a
  resolve por aparelho, e a DSL não tem forma para ela;
- coleta fora do catálogo (etapa livre `items_collected`): na DSL, só capability coleta;
- parâmetro-modelo (`{nome}`) do plano que o comando não traz, `missing`, e campos internos de runtime.

Todo parâmetro sai `string`, de propósito: a normalização de `string` é o `strip()` da extração do fluxo, e o valor
chega igual ao plano; `handle` poria o nome em minúsculas e mudaria o plano. Apertar o tipo é edição da pessoa.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import ValidationError

from app.contracts.skills.v1alpha1 import API_VERSION, KIND, TEXTO_CURTO
from app.models import Plan, PlanStep

from ..domain.compiler import RESERVED
from ..domain.document import JsonObject, JsonValue
from ..domain.errors import CompileIssue, Severity
from ..domain.matching import PLACEHOLDER, bind_template_parameters
from ..domain.refs import SKILL_ID
from ..domain.versions import SCHEMA_LEGACY_PLAN, SkillVersion
from .lowering import SkillPlanCompiler

#: Variáveis que só existem na execução, por aparelho ou por cópia do `for_each`. Num texto, a DSL não as expressa.
RUNTIME_VARIABLES = RESERVED | frozenset({"item_index"})
#: Faixas da DSL (`NodeSpec`): `timeout_s` de 10 a 900 s, `retries` de 0 a 4.
_TIMEOUT = (10, 900)
_RETRIES = (0, 4)
#: Padrão das tentativas de uma etapa livre sem `retries` (`lowering._passo`), o mesmo de `PlanStep.max_attempts`.
_TENTATIVAS_PADRAO = 3


class DecompileCode(StrEnum):
    E_RUNTIME_VARIABLE = "E_RUNTIME_VARIABLE"
    E_UNREPRESENTABLE = "E_UNREPRESENTABLE"
    E_ROUNDTRIP = "E_ROUNDTRIP"
    W_ROUNDTRIP = "W_ROUNDTRIP"


class IssueOrigin(StrEnum):
    #: `path` aponta para o plano do fluxo (`/steps/1/postcondition/value`).
    PLAN = "plan"
    #: `path` aponta para o documento gerado: o problema é do compilador, com o código dele.
    DOCUMENT = "document"


@dataclass(frozen=True, slots=True)
class DecompileIssue:
    """Um problema da descompilação. `code` é um `DecompileCode` ou, vindo do compilador, o `Code` dele: o
    vocabulário do compilador é fechado por fixture de documento inválido, e estes códigos não são de documento."""

    code: str
    message: str
    path: str = ""
    origin: IssueOrigin = IssueOrigin.PLAN

    @property
    def severity(self) -> Severity:
        return Severity.warning if self.code.startswith("W_") else Severity.error

    def text(self) -> str:
        """`E_CODIGO /caminho: mensagem` — a forma de `DocumentFacts.errors` e do 422 do roteador."""
        return f"{self.code} {self.path}: {self.message}" if self.path else f"{self.code}: {self.message}"

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path, "severity": self.severity.value,
                "origin": self.origin.value}


@dataclass(frozen=True, slots=True)
class Decompilation:
    """O documento (ou `None`, se nem deu para montá-lo) e tudo o que a ida e volta achou."""

    document: JsonObject | None
    issues: tuple[DecompileIssue, ...]

    @property
    def errors(self) -> tuple[DecompileIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.error)

    @property
    def warnings(self) -> tuple[DecompileIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.warning)

    @property
    def ok(self) -> bool:
        return self.document is not None and not self.errors


def suggested_skill_id(flow_id: str, app_id: str | None) -> str:
    """Id de habilidade para a conversão de um fluxo: `<app>.<fluxo>`, no formato de `SKILL_ID`, sempre o mesmo para
    o mesmo fluxo. O id do fluxo nasce de um resumo (`flows.py`) e pode começar com dígito ou ter caractere de fora."""
    bruto = f"{app_id or 'fluxo'}.{flow_id}".lower()
    limpo = re.sub(r"[^a-z0-9_.-]+", "-", bruto).strip("-.") or "fluxo"
    if not limpo[0].isalpha():
        limpo = "f" + limpo
    limpo = limpo[:64].rstrip("-.")
    return limpo if SKILL_ID.fullmatch(limpo) else (limpo + "-fluxo")[:64]


# ================================================================== montagem (pura)
class _Montagem:
    """Plano → documento, sem compilar. Junta os problemas que a DSL não tem como dizer, com o caminho no plano."""

    def __init__(self, plan: Plan, nomes: set[str]) -> None:
        self.plan = plan
        self.nomes = nomes
        self.issues: list[DecompileIssue] = []

    def erro(self, code: DecompileCode, message: str, path: str) -> None:
        self.issues.append(DecompileIssue(code.value, message, path))

    def texto(self, bruto: str, path: str) -> str:
        """`{nome}` do plano → `${parameters.nome}`; `{item}` → `${item}`. Variável do runtime é erro: fica crua, e o
        compilador também a recusa (E_RAW_PLACEHOLDER) — a mensagem daqui diz por quê."""
        de_runtime: list[str] = []

        def troca(m: re.Match[str]) -> str:
            nome = m.group(1)
            if nome == "item":
                return "${item}"
            if nome in self.nomes:
                return "${parameters." + nome + "}"
            if nome in RUNTIME_VARIABLES:
                de_runtime.append(nome)
            return m.group(0)

        saida = PLACEHOLDER.sub(troca, bruto)
        for nome in dict.fromkeys(de_runtime):
            self.erro(DecompileCode.E_RUNTIME_VARIABLE,
                      f"{{{nome}}} é variável do runtime (resolvida por aparelho no materialize): a v1alpha1 não tem "
                      "forma para ela num texto, e o fluxo só se converte sem ela", path)
        return saida

    def parametros(self, command_template: str) -> list[JsonValue]:
        do_comando = list(dict.fromkeys(n for n in PLACEHOLDER.findall(command_template) if n not in RESERVED))
        saida: list[JsonValue] = []
        vistos: set[str] = set()
        for nome, valor in self.plan.parameters.items():
            vistos.add(nome)
            if valor == "{" + nome + "}":
                if nome not in do_comando:
                    self.erro(DecompileCode.E_UNREPRESENTABLE,
                              f"o plano espera '{nome}' do comando, e o comando-modelo não tem {{{nome}}}: o fluxo "
                              "nunca casaria assim", f"/parameters/{nome}")
                saida.append({"name": nome, "type": "string", "required": True})
            else:
                # Valor fixo do plano (não veio do comando): padrão, para o plano ligado sair igual ao do fluxo.
                saida.append({"name": nome, "type": "string", "required": False, "default": valor})
        saida.extend({"name": n, "type": "string", "required": True} for n in do_comando if n not in vistos)
        return saida

    def no(self, i: int, s: PlanStep, app: str) -> JsonObject:
        base = f"/steps/{i}"
        if s.template_key is not None or s.variables:
            self.erro(DecompileCode.E_UNREPRESENTABLE, f"{s.key}: cópia de for_each (template_key/variables) num plano "
                                                       "congelado — campo interno do runtime, não de definição", base)
        no: JsonObject = {"id": s.key, "depends_on": list(s.depends_on)}
        if s.app_id is not None and s.app_id != app:
            no["app"] = s.app_id
        if s.for_each is not None:
            no["foreach"] = "${steps." + s.for_each + ".output}"
        # Tempo e tentativas sempre explícitos: o fluxo os congelou, e o nó os mantém sem depender do padrão do
        # catálogo de hoje. Com efeito externo, uma tentativa só (P5): o compilador fixa 1 e a ida e volta avisa se o
        # fluxo tinha outra coisa.
        no["timeout_s"] = min(max(s.timeout_s, _TIMEOUT[0]), _TIMEOUT[1])
        if not s.side_effect:
            no["retries"] = min(max(s.max_attempts - 1, _RETRIES[0]), _RETRIES[1])
        if s.bindings:
            vinculos: JsonObject = {k: self.texto(v, f"{base}/bindings/{k}") for k, v in sorted(s.bindings.items())}
            no["with"] = vinculos
        post = s.postcondition
        nivel = post.required_delivery_level.value if post.required_delivery_level is not None else None
        if s.capability is not None:
            # A capability traz do catálogo título, objetivo, pós-condição, guardas e efeito; o nó só a nomeia e liga
            # os argumentos. Se o catálogo de hoje não bater com o que o fluxo congelou, a comparação diz onde.
            no["capability"] = s.capability
            if nivel is not None:
                no["verification"] = {"required_delivery_level": nivel}
            return no
        if post.kind == "items_collected":
            self.erro(DecompileCode.E_UNREPRESENTABLE, f"{s.key}: coleta fora do catálogo (etapa livre com "
                                                       "items_collected); na v1alpha1 só uma capability coleta",
                      f"{base}/postcondition/kind")
        if s.commit_selector is not None or s.band_guard:
            self.erro(DecompileCode.E_UNREPRESENTABLE, f"{s.key}: commit_selector/band_guard numa etapa livre; na "
                                                       "v1alpha1 eles só vêm do catálogo", base)
        meta: JsonObject = {"title": self.texto(s.title, f"{base}/title"), "goal": self.texto(s.goal, f"{base}/goal")}
        if s.precondition is not None:
            meta["precondition"] = self.texto(s.precondition, f"{base}/precondition")
        pos: JsonObject = {"kind": post.kind, "value": self.texto(post.value, f"{base}/postcondition/value"),
                           "description": self.texto(post.description, f"{base}/postcondition/description")}
        if nivel is not None:
            pos["required_delivery_level"] = nivel
        no["goal"] = meta
        no["verification"] = {"postcondition": pos}
        no["side_effect"] = s.side_effect
        if s.commit_guard:
            guardas: list[JsonValue] = [self.texto(g, f"{base}/commit_guard/{j}") for j, g in enumerate(s.commit_guard)]
            no["commit_guard"] = guardas
        return no


def plan_to_document(plan: Plan, *, skill_id: str, command_template: str, app_id: str | None = None,
                     required_apps: Sequence[str] = (),
                     description: str | None = None) -> tuple[JsonObject | None, tuple[DecompileIssue, ...]]:
    """A parte pura: monta o documento. `app_id`/`required_apps` vêm do fluxo (a tabela manda, como no legado)."""
    app = plan.app_id or app_id
    if app is None:
        return None, (DecompileIssue(DecompileCode.E_UNREPRESENTABLE.value, "o plano não diz o app, e a habilidade "
                                                                              "precisa de metadata.app", "/app_id"),)
    comando = set(n for n in PLACEHOLDER.findall(command_template) if n not in RESERVED)
    m = _Montagem(plan, set(plan.parameters) | comando)
    if plan.missing:
        m.erro(DecompileCode.E_UNREPRESENTABLE, "plano com perguntas pendentes (missing) não é plano de fluxo",
               "/missing")
    parametros = m.parametros(command_template)
    nos: list[JsonValue] = [m.no(i, s, app) for i, s in enumerate(plan.steps)]
    criterios: list[JsonValue] = [m.texto(c, f"/success_criteria/{i}") for i, c in enumerate(plan.success_criteria)]
    exigidos: list[JsonValue] = list(dict.fromkeys(required_apps or plan.required_apps))
    # O nome é o `summary` do plano: é dele que o compilador tira o `summary` do plano compilado.
    nome = plan.summary[:TEXTO_CURTO] if plan.summary.strip() else skill_id
    metadata: JsonObject = {"id": skill_id, "name": nome, "app": app}
    if description:
        metadata["description"] = description
    spec: JsonObject = {"invocation": {"command_template": command_template}, "parameters": parametros,
                        "requires": {"apps": exigidos}, "nodes": nos, "success_criteria": criterios}
    documento: JsonObject = {"apiVersion": API_VERSION, "kind": KIND, "metadata": metadata, "spec": spec}
    return documento, tuple(m.issues)


# ================================================================== ida e volta
def _curto(valor: object) -> str:
    texto = repr(valor)
    return texto if len(texto) <= 90 else texto[:87] + "…"


def _dica_de_literal(s: PlanStep) -> str:
    """O caso de produção mais provável: o planejador pôs o VALOR no argumento, e o aprendizado do fluxo trocou o valor
    pelo `{nome}` nos textos, mas não nos argumentos (`learn_from_run` só passou a trocar em `bindings` em 28/09; os
    fluxos aprendidos antes continuam no banco assim)."""
    literais = [k for k, v in s.bindings.items() if "{" not in v]
    if literais and PLACEHOLDER.search(s.postcondition.value + " ".join(s.commit_guard)):
        return (f" O argumento {', '.join(sorted(literais))} guarda um valor fixo e o texto congelado usa um "
                "{parâmetro}: o fluxo aprendeu o texto em forma de modelo e o argumento não.")
    return ""


def compare_plans(original: Plan, compiled: Plan, *, legacy_parameters: Mapping[str, str],
                  legacy_required_apps: Sequence[str]) -> tuple[DecompileIssue, ...]:
    """O plano do fluxo (ligado aos valores de amostra) × o compilado do documento (com os mesmos valores).

    ERRO no que muda a identidade de receita ou o comportamento; AVISO no texto que o catálogo reescreveu. Numa etapa
    livre, o texto é do plano: se ele mudou, o descompilador perdeu algo, e isso também é erro.
    """
    issues: list[DecompileIssue] = []

    def erro(path: str, msg: str) -> None:
        issues.append(DecompileIssue(DecompileCode.E_ROUNDTRIP.value, msg, path))

    def aviso(path: str, msg: str) -> None:
        issues.append(DecompileIssue(DecompileCode.W_ROUNDTRIP.value, msg, path))

    if original.app_id != compiled.app_id:
        erro("/app_id", f"o app muda de {_curto(original.app_id)} para {_curto(compiled.app_id)}")
    if dict(legacy_parameters) != compiled.parameters:
        erro("/parameters", f"os parâmetros ligados mudam de {_curto(dict(legacy_parameters))} para "
                            f"{_curto(compiled.parameters)}")
    exigidos = set(legacy_required_apps) or ({original.app_id} if original.app_id else set())
    if exigidos != set(compiled.required_apps):
        aviso("/required_apps", f"os apps exigidos mudam de {sorted(exigidos)} para {sorted(compiled.required_apps)}: "
                                "o pré-voo da v2 confere o que as etapas usam")
    do_plano: tuple[tuple[str, object, object], ...] = (
        ("summary", original.summary, compiled.summary), ("app_package", original.app_package, compiled.app_package),
        ("success_criteria", original.success_criteria, compiled.success_criteria))
    for campo, antes, depois in do_plano:
        if antes != depois:
            aviso(f"/{campo}", f"{campo} muda de {_curto(antes)} para {_curto(depois)}")
    if len(original.steps) != len(compiled.steps):
        erro("/steps", f"o plano tem {len(original.steps)} etapa(s) e o compilado, {len(compiled.steps)}")
        return tuple(issues)
    for i, (a, b) in enumerate(zip(original.steps, compiled.steps, strict=True)):
        base = f"/steps/{i}"
        livre = a.capability is None
        app_a = None if a.app_id == original.app_id else a.app_id
        pa, pb = a.postcondition, b.postcondition
        identidade: tuple[tuple[str, object, object], ...] = (
            ("key", a.key, b.key), ("capability", a.capability, b.capability),
            ("depends_on", a.depends_on, b.depends_on), ("for_each", a.for_each, b.for_each),
            ("side_effect", a.side_effect, b.side_effect), ("app_id", app_a, b.app_id),
            ("bindings", a.bindings, b.bindings), ("postcondition/kind", pa.kind, pb.kind),
            ("postcondition/value", pa.value, pb.value),
            ("postcondition/required_delivery_level", pa.required_delivery_level, pb.required_delivery_level),
            ("commit_guard", sorted(a.commit_guard), sorted(b.commit_guard)))
        for campo, antes, depois in identidade:
            if antes != depois:
                dica = _dica_de_literal(a) if campo in ("postcondition/value", "commit_guard") else ""
                erro(f"{base}/{campo}", f"{a.key}: {campo} muda de {_curto(antes)} para {_curto(depois)} — a etapa "
                                        f"mudaria de identidade (receitas) ou de comportamento.{dica}")
        textuais: tuple[tuple[str, object, object], ...] = (
            ("title", a.title, b.title), ("goal", a.goal, b.goal), ("precondition", a.precondition, b.precondition),
            ("commit_selector", a.commit_selector, b.commit_selector), ("band_guard", a.band_guard, b.band_guard),
            ("postcondition/description", pa.description, pb.description),
            ("max_attempts", a.max_attempts, b.max_attempts), ("timeout_s", a.timeout_s, b.timeout_s))
        for campo, antes, depois in textuais:
            if antes == depois:
                continue
            if livre and campo in ("title", "goal", "precondition", "commit_selector", "band_guard"):
                erro(f"{base}/{campo}", f"{a.key}: {campo} muda de {_curto(antes)} para {_curto(depois)} numa etapa "
                                        "livre, em que o texto é do próprio plano")
            elif livre or campo in ("max_attempts", "timeout_s"):
                aviso(f"{base}/{campo}", f"{a.key}: {campo} muda de {_curto(antes)} para {_curto(depois)} (a "
                                         "v1alpha1 não representa o valor congelado)")
            else:
                aviso(f"{base}/{campo}", f"{a.key}: {campo} muda de {_curto(antes)} para {_curto(depois)} — o "
                                         "catálogo de hoje difere do que o fluxo congelou; a v2 usa o de hoje, a v1 "
                                         "adotada continua como o fluxo")
    return tuple(issues)


def _do_compilador(i: CompileIssue) -> DecompileIssue:
    return DecompileIssue(i.code.value, i.message, i.path, IssueOrigin.DOCUMENT)


class PlanDecompiler:
    """Monta o documento e o confere pelo compilador real. `compiler` é o `SkillPlanCompiler` da execução
    (`SkillRunPlanner.compiler`): "converteu" e "compila na execução" não podem divergir por caminho."""

    def __init__(self, compiler: SkillPlanCompiler) -> None:
        self._compiler = compiler

    def decompile(self, plan: Plan, *, skill_id: str, command_template: str, app_id: str | None = None,
                  required_apps: Sequence[str] = (), description: str | None = None) -> Decompilation:
        documento, montagem = plan_to_document(plan, skill_id=skill_id, command_template=command_template,
                                               app_id=app_id, required_apps=required_apps, description=description)
        issues = list(montagem)
        if documento is None:
            return Decompilation(None, tuple(issues))
        seco = self._compiler.compilar(documento, version=1)
        issues.extend(_do_compilador(i) for i in seco.issues)
        if seco.errors:
            return Decompilation(documento, tuple(dict.fromkeys(issues)))
        comando = [n for n in PLACEHOLDER.findall(command_template) if n not in RESERVED]
        amostra = {n: f"amostra-{n}" for n in dict.fromkeys(comando)}
        ligados = bind_template_parameters(plan.parameters, amostra)
        molhado = self._compiler.compilar(documento, version=1, parameters=amostra)
        issues.extend(_do_compilador(i) for i in molhado.errors)
        if ligados is None or molhado.executable is None:
            # Parâmetro-modelo fora do comando (já apontado na montagem) ou compilação com valores recusada.
            return Decompilation(documento, tuple(dict.fromkeys(issues)))
        issues.extend(compare_plans(plan, molhado.executable.plan, legacy_parameters=ligados,
                                    legacy_required_apps=required_apps))
        return Decompilation(documento, tuple(dict.fromkeys(issues)))

    def decompile_version(self, version: SkillVersion, *, skill_id: str, app_id: str | None = None,
                          description: str | None = None) -> Decompilation:
        """O conteúdo legado (`schema_version` 0: `flow:<id>@1` ou a v1 de um fluxo adotado) → documento."""
        if version.schema_version != SCHEMA_LEGACY_PLAN:
            return Decompilation(None, (DecompileIssue(DecompileCode.E_UNREPRESENTABLE.value,
                                                       f"{version.ref} já é documento da DSL: não há o que "
                                                       "descompilar"),))
        conteudo = version.document()
        try:
            plano = Plan.model_validate(conteudo.get("plan"))
        except ValidationError as exc:
            return Decompilation(None, (DecompileIssue(DecompileCode.E_UNREPRESENTABLE.value,
                                                       f"o plano congelado de {version.ref} não é um Plan válido: "
                                                       f"{exc.error_count()} erro(s)", "/plan"),))
        exigidos = conteudo.get("required_apps")
        apps = [a for a in exigidos if isinstance(a, str)] if isinstance(exigidos, list) else []
        return self.decompile(plano, skill_id=skill_id, command_template=version.command_template or "",
                              app_id=app_id, required_apps=apps, description=description)
