"""Resolução de intenção (fase I; design §14.1, RESOLVE, e §10.4): os value objects e a extração tipada, puros.

A RESOLVE responde "que habilidade este comando pede, e com que valores?". Três respostas, e só três:

- `resolved`: uma habilidade, com cada parâmetro validado pelo TIPO declarado no documento (`ExtractedParameter`);
- `needs_input`: a pergunta estruturada (`MissingInfo`) — parâmetro obrigatório vazio, valor que não serve para o
  tipo, ou dois candidatos com a mesma força. **Nunca se escolhe às cegas nem se inventa valor**: na dúvida,
  pergunta-se à pessoa, e a execução para em `needs_input` sem plano;
- `no_match`: nada casa, e o planejador fica com o comando, como sempre.

A normalização por tipo é o que o documento `automation/v1alpha1` declara (`ParameterSpec.type`), e o valor que sai
daqui é TEXTO, porque é texto que `Plan.parameters` guarda (§10.4, lowering): `integer` sai em decimal, `boolean`
sai `true`/`false` (o mesmo texto que o compilador dá a um padrão booleano, `compiler.py::_texto_do_valor`), `enum`
sai no valor declarado, `handle` sai `@nome` em minúsculas.

Conteúdo legado (`schema_version` 0: o fluxo, ou a v1 de um fluxo adotado) não declara tipo: o valor passa como o
comando o trouxe, e o resultado é idêntico ao do `FlowStore.match` de sempre.

O domínio não conhece app nenhum. O que um link de perfil quer dizer (o do Instagram vira `@nome`) é DADO que a
borda injeta (`ProfileLinkRule`); a regra do Instagram mora na infraestrutura.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import SplitResult, urlsplit, urlunsplit

from app.contracts.skills.v1alpha1 import ParameterSpec, ParameterType

from .document import JsonObject, JsonValue
from .matching import specificity
from .refs import SkillRef
from .versions import ResolvedSkill


# ================================================================== vocabulários
class ParameterOrigin(StrEnum):
    #: O comando deu o valor: capturado pelo `{nome}` do modelo e normalizado pelo tipo.
    COMMAND = "command"
    #: O documento declara o padrão e o comando não deu o valor. Quem aplica o padrão é o compilador
    #: (`_ligar`); aqui ele só é MOSTRADO, para a pessoa ver com que valor a habilidade vai rodar.
    DEFAULT = "default"


class ResolutionMethod(StrEnum):
    """A etapa da cadeia que ESCOLHEU a habilidade (a normalização dos valores aparece em cada parâmetro)."""

    TEMPLATE = "template"
    TYPED = "typed"
    SEMANTIC = "semantic"
    LLM = "llm"


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    NEEDS_INPUT = "needs_input"
    NO_MATCH = "no_match"


class QuestionReason(StrEnum):
    MISSING_PARAMETER = "missing_parameter"
    INVALID_PARAMETER = "invalid_parameter"
    AMBIGUOUS_INTENT = "ambiguous_intent"


class StageOutcome(StrEnum):
    MATCHED = "matched"
    PARTIAL = "partial"
    AMBIGUOUS = "ambiguous"
    NO_MATCH = "no_match"
    VALIDATED = "validated"
    INVALID = "invalid"
    TIE_BROKEN = "tie_broken"
    SKIPPED = "skipped"
    #: A etapa existe, mas o provedor dela é o nulo: nada foi perguntado a ninguém (prova `not_run`).
    NOT_RUN = "not_run"


# ================================================================== value objects
@dataclass(frozen=True, slots=True)
class SkillMatch:
    """Um candidato da etapa de modelos. `missing` não vazio = o comando é o modelo com esses `{nome}` vazios: vale
    para perguntar, nunca para planejar. `skill.parameters` são os valores CRUS que o comando deu."""

    skill: ResolvedSkill
    missing: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def ref(self) -> SkillRef:
        return self.skill.ref

    @property
    def strength(self) -> tuple[int, int]:
        """A força do casamento: a especificidade do modelo (`matching.specificity`)."""
        return specificity(self.skill.version.command_template or "")


@dataclass(frozen=True, slots=True)
class ExtractedParameter:
    name: str
    #: `None` = conteúdo legado, sem tipo declarado: o valor passa como veio.
    type: ParameterType | None
    #: O texto que vai para `Plan.parameters`.
    value: str
    origin: ParameterOrigin
    #: O texto como o comando o trouxe; `None` quando o valor é o padrão do documento.
    raw: str | None = None

    def as_dict(self) -> JsonObject:
        return {"name": self.name, "type": self.type, "value": self.value, "origin": self.origin.value,
                "raw": self.raw}


@dataclass(frozen=True, slots=True)
class MissingInfo:
    """A pergunta estruturada. `field` é o parâmetro (ou `skill`, na ambiguidade); `options`, as escolhas fechadas
    (os valores de um `enum`, ou as habilidades candidatas); `received`, o que o comando trouxe e não serviu."""

    field: str
    question: str
    reason: QuestionReason
    expected: str | None = None
    options: tuple[str, ...] = ()
    received: str | None = None
    #: A habilidade a que a pergunta se refere, quando é uma só.
    skill: str | None = None

    def as_dict(self) -> JsonObject:
        opcoes: list[JsonValue] = list(self.options)
        return {"field": self.field, "question": self.question, "reason": self.reason.value,
                "expected": self.expected, "options": opcoes, "received": self.received, "skill": self.skill}


@dataclass(frozen=True, slots=True)
class ParameterExtraction:
    parameters: tuple[ExtractedParameter, ...]
    questions: tuple[MissingInfo, ...]

    @property
    def ok(self) -> bool:
        return not self.questions

    def command_values(self) -> dict[str, str]:
        """O que vai ao compilador: só os valores que o COMANDO deu, já normalizados. O padrão fica com o
        compilador (`_ligar`), que o aplica como sempre aplicou."""
        return {p.name: p.value for p in self.parameters if p.origin is ParameterOrigin.COMMAND}


def backend_of(ref: SkillRef) -> str:
    return "legacy_flow" if ref.is_legacy else "skill"


@dataclass(frozen=True, slots=True)
class ResolvedIntent:
    """A habilidade escolhida. `skill.parameters` já são os valores normalizados que o compilador recebe."""

    skill: ResolvedSkill
    method: ResolutionMethod
    parameters: tuple[ExtractedParameter, ...]

    @property
    def ref(self) -> SkillRef:
        return self.skill.ref

    def as_dict(self) -> JsonObject:
        params: list[JsonValue] = [p.as_dict() for p in self.parameters]
        return {"skill_ref": str(self.ref), "skill_id": self.ref.skill_id, "version": self.ref.version,
                "name": self.skill.definition.name, "backend": backend_of(self.ref), "method": self.method.value,
                "parameters": params}


@dataclass(frozen=True, slots=True)
class StageTrace:
    stage: str
    outcome: StageOutcome
    detail: str = ""

    def as_dict(self) -> JsonObject:
        return {"stage": self.stage, "outcome": self.outcome.value, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class IntentResolution:
    status: ResolutionStatus
    intent: ResolvedIntent | None = None
    questions: tuple[MissingInfo, ...] = ()
    #: `needs_input` sobre UMA habilidade (parâmetro vazio ou inválido): a trilha da execução a registra.
    subject: ResolvedSkill | None = None
    #: `needs_input` por ambiguidade: as habilidades entre as quais nada decidiu.
    candidates: tuple[ResolvedSkill, ...] = ()
    trace: tuple[StageTrace, ...] = ()

    @property
    def skill(self) -> ResolvedSkill | None:
        """A habilidade de que se fala: a resolvida, ou a única que precisa de resposta."""
        return self.intent.skill if self.intent is not None else self.subject

    def as_dict(self) -> JsonObject:
        perguntas: list[JsonValue] = [q.as_dict() for q in self.questions]
        candidatas: list[JsonValue] = [{"skill_ref": str(c.ref), "name": c.definition.name,
                                        "backend": backend_of(c.ref)} for c in self.candidates]
        etapas: list[JsonValue] = [t.as_dict() for t in self.trace]
        return {"status": self.status.value, "intent": self.intent.as_dict() if self.intent is not None else None,
                "questions": perguntas, "subject": str(self.subject.ref) if self.subject is not None else None,
                "candidates": candidatas, "stages": etapas}


# ================================================================== links de perfil
@dataclass(frozen=True, slots=True)
class ProfileLinkRule:
    """Como o link de perfil de um app vira o nome de usuário (`handle`). É dado, e quem o declara é a borda.

    `hosts`: domínios aceitos, com qualquer subdomínio (`instagram.com` casa `www.` e `m.`). `reserved`: o primeiro
    segmento do caminho que NÃO é perfil (`p`, `reel`, `explore`...). Só um link de UM segmento é perfil: o de um
    post ou de um story não diz de quem se fala com certeza, e na dúvida se pergunta.
    """

    hosts: frozenset[str]
    reserved: frozenset[str]


_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_BARE_HOST = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}(?:[/?#:]|$)", re.IGNORECASE)


def _split_link(text: str) -> SplitResult | None:
    t = text.strip()
    if not t or any(c.isspace() for c in t):
        return None
    if not _SCHEME.match(t):
        if not _BARE_HOST.match(t):
            return None
        t = "https://" + t
    try:
        partes = urlsplit(t)
        host = partes.hostname
    except ValueError:
        return None
    if partes.scheme.lower() not in ("http", "https") or not host or "." not in host:
        return None
    return partes


def handle_from_link(text: str, rules: Sequence[ProfileLinkRule]) -> str | None:
    """O nome de usuário de um link de perfil (`https://www.instagram.com/ana.teste/` → `ana.teste`), ou `None`."""
    partes = _split_link(text)
    if partes is None or partes.hostname is None:
        return None
    host = partes.hostname.lower()
    for regra in rules:
        if any(host == h or host.endswith("." + h) for h in regra.hosts):
            segmentos = [s for s in partes.path.split("/") if s]
            if len(segmentos) != 1 or segmentos[0].casefold() in regra.reserved:
                return None
            return segmentos[0]
    return None


# ================================================================== normalização por tipo
@dataclass(frozen=True, slots=True)
class ValueProblem:
    """Por que um valor não serve: `expected` diz o que serve; `detail`, o que houve com este."""

    expected: str
    detail: str


def fold(text: str) -> str:
    """Comparação sem acento e sem caixa: "Não" == "nao"."""
    decomposto = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).casefold().strip()


#: Por extenso, sem acento (`fold`): o bastante para "os últimos cinco posts" e "vinte e cinco".
_UNIDADES = {"zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
             "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "quatorze": 14,
             "catorze": 14, "quinze": 15, "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19}
_DEZENAS = {"vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "sessenta": 60, "setenta": 70,
            "oitenta": 80, "noventa": 90}
_DIGITOS = re.compile(r"[+-]?\d+")
#: "1.000" em português é mil. "1,5" e "1.5" não são inteiros e voltam como pergunta.
_MILHAR = re.compile(r"\d{1,3}(?:\.\d{3})+")

#: Os textos que valem sim/não (sem acento). `true`/`false` são o que sai, e o que o `when` do compilador lê.
_VERDADE = frozenset({"sim", "s", "verdadeiro", "true", "1", "yes", "y", "ligado", "ligada", "ativo", "ativa",
                      "ativado", "ativada"})
_FALSO = frozenset({"nao", "n", "falso", "false", "0", "no", "desligado", "desligada", "inativo", "inativa",
                    "desativado", "desativada"})

#: Nome de usuário: letras, dígitos, ponto e sublinhado, até 30 (a regra do Instagram, que é a mais comum entre as
#: redes). Sem caixa: `@Ana` e `@ana` são a mesma conta.
_HANDLE = re.compile(r"[a-z0-9._]{1,30}")


def integer_from_text(text: str) -> int | None:
    t = fold(text)
    if _DIGITOS.fullmatch(t):
        return int(t)
    if _MILHAR.fullmatch(t):
        return int(t.replace(".", ""))
    if t in _UNIDADES:
        return _UNIDADES[t]
    if t in _DEZENAS:
        return _DEZENAS[t]
    if t == "cem":
        return 100
    dezena, e, unidade = t.partition(" e ")
    if e and dezena in _DEZENAS and unidade in _UNIDADES and 0 < _UNIDADES[unidade] < 10:
        return _DEZENAS[dezena] + _UNIDADES[unidade]
    return None


def boolean_from_text(text: str) -> bool | None:
    t = fold(text)
    return True if t in _VERDADE else False if t in _FALSO else None


def expected_of(spec: ParameterSpec) -> str:
    """O que serve para o parâmetro, em português: é o que a pergunta diz à pessoa."""
    match spec.type:
        case "integer":
            base = "um número inteiro (ex.: 3 ou três)"
        case "boolean":
            base = "sim ou não"
        case "enum":
            base = "um destes: " + ", ".join(spec.values or ())
        case "handle":
            base = "um nome de usuário (@nome) ou o link do perfil"
        case "url":
            base = "um link http(s)"
        case _:
            base = "um texto"
    if spec.max_length is not None and spec.type in ("string", "text", "url"):
        base += f" de até {spec.max_length} caracteres"
    return base


def _handle(raw: str, links: Sequence[ProfileLinkRule]) -> str | ValueProblem:
    texto = raw.strip().strip("\"'").strip()      # aspas em volta do nome não são parte dele
    if "/" in texto or _SCHEME.match(texto):
        nome = handle_from_link(texto, links)
        if nome is None:
            return ValueProblem("", "o link não é de um perfil que eu saiba ler")
        texto = nome
    nome = texto.removeprefix("@").casefold()
    if not _HANDLE.fullmatch(nome):
        return ValueProblem("", "nome de usuário tem só letras, números, ponto e sublinhado, sem espaço")
    return "@" + nome


def _url(raw: str) -> str | ValueProblem:
    partes = _split_link(raw)
    if partes is None:
        return ValueProblem("", "não é um link http(s)")
    netloc = partes.netloc if "@" in partes.netloc else partes.netloc.lower()
    return urlunsplit(partes._replace(scheme=partes.scheme.lower(), netloc=netloc))


def _enum(raw: str, values: Sequence[str]) -> str | ValueProblem:
    if raw in values:
        return raw
    iguais = [v for v in values if fold(v) == fold(raw)]
    if len(iguais) == 1:
        return iguais[0]
    if len(iguais) > 1:
        return ValueProblem("", "o valor corresponde a mais de uma opção")
    return ValueProblem("", "não é uma das opções")


def normalize_value(spec: ParameterSpec, raw: str,
                    links: Sequence[ProfileLinkRule] = ()) -> str | ValueProblem:
    """O valor capturado, normalizado pelo tipo declarado — ou por que ele não serve. Puro, sem I/O."""
    valor: str | ValueProblem
    match spec.type:
        case "integer":
            n = integer_from_text(raw)
            valor = str(n) if n is not None else ValueProblem("", "não é um número inteiro")
        case "boolean":
            b = boolean_from_text(raw)
            valor = ("true" if b else "false") if b is not None else ValueProblem("", "não é sim nem não")
        case "enum":
            valor = _enum(raw, spec.values or ())
        case "handle":
            valor = _handle(raw, links)
        case "url":
            valor = _url(raw)
        case _:                                   # string e text: o texto como veio (já sem as bordas)
            valor = raw.strip()
    if isinstance(valor, ValueProblem):
        return ValueProblem(expected_of(spec), valor.detail)
    if spec.max_length is not None and len(valor) > spec.max_length:
        return ValueProblem(expected_of(spec), f"passa de {spec.max_length} caracteres")
    if spec.pattern is not None:
        try:
            cumpre = re.fullmatch(spec.pattern, valor) is not None
        except re.error:
            cumpre = False                        # o compilador recusa regex inválida (E_SCHEMA): não publica
        if not cumpre:
            return ValueProblem(expected_of(spec), "não está no formato que a habilidade pede")
    return valor


def _default_text(value: str | int | bool) -> str:
    """O texto de um padrão: o MESMO de `compiler.py::_texto_do_valor`, que é quem o aplica."""
    return ("true" if value else "false") if isinstance(value, bool) else str(value)


def _exemplo(spec: ParameterSpec | None) -> str:
    return f" (ex.: {spec.example})" if spec is not None and spec.example else ""


def extract_typed(specs: Sequence[ParameterSpec] | None, captured: Mapping[str, str], *,
                  missing: Sequence[str] = (), links: Sequence[ProfileLinkRule] = (),
                  skill: str | None = None, skill_name: str | None = None) -> ParameterExtraction:
    """Os valores do comando, validados pelo tipo de cada parâmetro, e as perguntas que faltam responder.

    - `specs=None`: conteúdo legado, sem tipo. O valor passa como veio (a mesma resposta do fluxo de sempre).
    - Parâmetro com valor: normalizado pelo tipo; o que não serve vira pergunta (`invalid_parameter`), com o que
      veio, o que serve e, no `enum`, as opções.
    - Parâmetro sem valor: o padrão, se houver (só mostrado: quem o aplica é o compilador); senão, se obrigatório,
      pergunta (`missing_parameter`).
    - `missing`: os `{nome}` que o comando deixou vazios. Esses SEMPRE viram pergunta, mesmo com padrão: casar com
      buraco vazio não é casar, e o padrão não pode decidir por quem escreveu o comando pela metade.
    - Valor para um nome que o documento não declara passa sem tipo: o compilador o recusa com
      `E_UNKNOWN_PARAMETER`, como antes.
    """
    rotulo = f" de “{skill_name}”" if skill_name else ""
    perguntas: list[MissingInfo] = []
    if specs is None:
        for nome in missing:
            perguntas.append(MissingInfo(nome, f"Falta o valor de '{nome}'{rotulo}.", QuestionReason.MISSING_PARAMETER,
                                         skill=skill))
        return ParameterExtraction(tuple(ExtractedParameter(n, None, v, ParameterOrigin.COMMAND, v)
                                         for n, v in captured.items()), tuple(perguntas))
    parametros: list[ExtractedParameter] = []
    declarados = {s.name: s for s in specs}
    for spec in specs:
        if spec.name in missing:
            perguntas.append(MissingInfo(spec.name, f"Falta o valor de '{spec.name}'{rotulo}: informe "
                                                    f"{expected_of(spec)}{_exemplo(spec)}.",
                                         QuestionReason.MISSING_PARAMETER, expected_of(spec),
                                         tuple(spec.values or ()) if spec.type == "enum" else (), skill=skill))
        elif spec.name in captured:
            bruto = captured[spec.name]
            valor = normalize_value(spec, bruto, links)
            if isinstance(valor, ValueProblem):
                perguntas.append(MissingInfo(
                    spec.name, f"“{bruto}” não serve para '{spec.name}'{rotulo}: {valor.detail}. Informe "
                               f"{valor.expected}{_exemplo(spec)}.",
                    QuestionReason.INVALID_PARAMETER, valor.expected,
                    tuple(spec.values or ()) if spec.type == "enum" else (), received=bruto, skill=skill))
            else:
                parametros.append(ExtractedParameter(spec.name, spec.type, valor, ParameterOrigin.COMMAND, bruto))
        elif spec.default is not None:
            parametros.append(ExtractedParameter(spec.name, spec.type, _default_text(spec.default),
                                                 ParameterOrigin.DEFAULT))
        elif spec.required:
            perguntas.append(MissingInfo(spec.name, f"Falta o valor de '{spec.name}'{rotulo}: informe "
                                                    f"{expected_of(spec)}{_exemplo(spec)}.",
                                         QuestionReason.MISSING_PARAMETER, expected_of(spec),
                                         tuple(spec.values or ()) if spec.type == "enum" else (), skill=skill))
    for nome, bruto in captured.items():
        if nome not in declarados:
            parametros.append(ExtractedParameter(nome, None, bruto, ParameterOrigin.COMMAND, bruto))
    return ParameterExtraction(tuple(parametros), tuple(perguntas))


def ambiguity_question(candidates: Sequence[ResolvedSkill]) -> MissingInfo:
    """Dois ou mais candidatos com a mesma força e nada que os distinga: a pessoa escolhe reescrevendo o comando."""
    nomes = "; ".join(f"“{c.definition.name}” ({c.ref})" for c in candidates)
    return MissingInfo("skill", f"O comando casa com mais de uma habilidade e nada as distingue: {nomes}. Reescreva "
                                "o comando com o texto de uma delas.", QuestionReason.AMBIGUOUS_INTENT,
                       "o texto do comando de uma das habilidades", tuple(str(c.ref) for c in candidates))
