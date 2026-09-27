"""Ensino v2 (`TeachingSession`, §13; tabelas da 044): o modelo, os estados e as recusas — sem banco e sem IA.

Uma sessão de ensino junta INSTRUÇÃO (o que a pessoa disse), DEMONSTRAÇÕES (gravações v1 ou execuções comprovadas),
CORREÇÕES e a CONVERSA com o generalizador (perguntas e respostas). Dela saem CANDIDATAS; só a aceita vira versão
de habilidade (`skill_versions`, em `draft`), e as rejeitadas não gastam número de versão.

Por que a fonte (`instruction`, `demonstration`, `hybrid`, `correction`, `successful_execution`) é DERIVADA e não
coluna: uma sessão muda de fonte ao ganhar uma demonstração ou uma correção, e uma coluna teria de ser mantida em
dia por todo escritor (§13.1).

Os vocabulários são os da 044, repetidos aqui como enum porque a migração não tem `CHECK`: o texto que sai do banco
é conferido na borda (`infrastructure/teaching_rows.py`) e um valor estranho é erro, não "desconhecido".
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from app.modules.skills.domain.document import JsonObject, JsonValue, as_json_object, content_hash
from app.modules.skills.domain.lifecycle import SkillError
from app.modules.skills.domain.refs import SKILL_ID


class TeachingStatus(StrEnum):
    OPEN = "open"
    DEMONSTRATING = "demonstrating"         # há gravação v1 ligada que ainda está gravando
    PROPOSING = "proposing"                 # o generalizador está trabalhando (chamada paga na IA real)
    ASKING = "asking"                       # a candidata tem perguntas que só a pessoa responde
    VALIDATING = "validating"               # a candidata compilou; falta a validação (estática nesta fase)
    READY = "ready"                         # validada: pode virar versão de habilidade
    PUBLISHED = "published"                 # a candidata aceita virou `skill_versions` (em draft); terminal
    DISCARDED = "discarded"                 # terminal


class TeachingValidation(StrEnum):
    NONE = "none"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    PARTIAL = "partial"


class TeachingSource(StrEnum):
    INSTRUCTION = "instruction"
    DEMONSTRATION = "demonstration"
    HYBRID = "hybrid"
    CORRECTION = "correction"
    SUCCESSFUL_EXECUTION = "successful_execution"


class DemonstrationKind(StrEnum):
    RECORDING = "recording"                 # gravação v1 (`training_sessions` + `training_inputs`)
    RUN = "run"                             # execução `completed` usada como exemplo


class TurnKind(StrEnum):
    INSTRUCTION = "instruction"
    QUESTION = "question"
    ANSWER = "answer"
    CORRECTION = "correction"
    NOTE = "note"


class TurnAuthor(StrEnum):
    PERSON = "person"
    AI = "ai"
    COMPILER = "compiler"
    SYSTEM = "system"


class CandidateStatus(StrEnum):
    PROPOSED = "proposed"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"


class QuestionKind(StrEnum):
    AMBIGUITY = "ambiguity"
    MISSING_PARAMETER = "missing_parameter"
    EFFECT_CONFIRMATION = "effect_confirmation"
    SCOPE = "scope"
    POLICY = "policy"


_T = TeachingStatus

#: De → para. O que não está aqui é recusado. `proposing → open` é a candidata rejeitada (documento que não compila,
#: valor com formato de segredo) ou a falha do generalizador: a sessão volta a aceitar um novo pedido, que é
#: explícito porque custa. Os terminais são `published` e `discarded`.
TRANSITIONS: Mapping[TeachingStatus, frozenset[TeachingStatus]] = {
    _T.OPEN: frozenset({_T.DEMONSTRATING, _T.PROPOSING, _T.DISCARDED}),
    _T.DEMONSTRATING: frozenset({_T.OPEN, _T.DISCARDED}),
    _T.PROPOSING: frozenset({_T.ASKING, _T.VALIDATING, _T.OPEN, _T.DISCARDED}),
    _T.ASKING: frozenset({_T.PROPOSING, _T.DISCARDED}),
    _T.VALIDATING: frozenset({_T.READY, _T.OPEN, _T.DISCARDED}),
    _T.READY: frozenset({_T.PUBLISHED, _T.DISCARDED}),
    _T.PUBLISHED: frozenset(),
    _T.DISCARDED: frozenset(),
}
TERMINAL = frozenset(s for s, destinos in TRANSITIONS.items() if not destinos)
#: Onde se pode anexar demonstração: depois que há candidata, uma demonstração nova mudaria o que ela generalizou.
ACCEPTS_DEMONSTRATION = frozenset({_T.OPEN, _T.DEMONSTRATING})


# ------------------------------------------------------------------ recusas
class TeachingNotFound(SkillError):
    code = "not_found"


class TeachingStateConflict(SkillError):
    """Transição fora da tabela, ou a sessão mudou entre a leitura e a escrita (CAS perdeu)."""

    code = "teaching_state"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class TeachingInputInvalid(SkillError):
    """Pedido mal formado (texto vazio, gravação descartada, execução que não terminou, app desconhecido...)."""

    code = "invalid_input"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class CredentialInText(SkillError):
    """Texto da pessoa com credencial: ele vai ao provedor de IA e fica na conversa do ensino (§13.1, ADR-025)."""

    code = "credential_in_text"


class SecretInCandidate(SkillError):
    """Documento de candidata com valor em formato de credencial: credencial nunca entra em habilidade."""

    code = "secret_in_candidate"


class GeneralizerFailed(SkillError):
    """O generalizador não devolveu uma proposta utilizável (IA fora, resposta que não é o formato pedido)."""

    code = "generalizer_error"


def check_move(frm: TeachingStatus, to: TeachingStatus) -> None:
    if to not in TRANSITIONS[frm]:
        if frm in TERMINAL:
            raise TeachingStateConflict(f"O ensino já está '{frm}': não muda mais.")
        raise TeachingStateConflict(f"O ensino está '{frm}' e não pode ir para '{to}'.")


# ------------------------------------------------------------------ entidades
@dataclass(frozen=True, slots=True)
class TeachingSession:
    id: str
    instruction: str
    skill_id: str | None                    # nulo = habilidade nova
    base_version: int | None                # ensino que melhora a versão N
    app_id: str | None
    profile_id: str | None
    status: TeachingStatus
    validation_status: TeachingValidation
    result_version_id: str | None
    operator: str | None
    created_at: str
    updated_at: str
    closed_at: str | None

    @property
    def closed(self) -> bool:
        return self.status in TERMINAL


@dataclass(frozen=True, slots=True)
class Demonstration:
    id: str
    teaching_id: str
    seq: int
    kind: DemonstrationKind
    training_session_id: str | None
    run_id: str | None
    instance_id: str | None
    app_snapshot: JsonObject | None
    note: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class NewTurn:
    """Um turno ainda sem id (o banco dá a ordem)."""

    teaching_id: str
    kind: TurnKind
    author: TurnAuthor
    body: str | None = None
    reply_to: int | None = None
    target: JsonObject | None = None
    payload: JsonObject | None = None
    candidate_id: str | None = None
    created_by: str | None = None


@dataclass(frozen=True, slots=True)
class TeachingTurn:
    id: int
    teaching_id: str
    kind: TurnKind
    author: TurnAuthor
    reply_to: int | None
    target: JsonObject | None
    body: str | None
    payload: JsonObject | None
    candidate_id: str | None
    created_by: str | None
    created_at: str

    @property
    def question_key(self) -> str | None:
        """A chave estável da pergunta (`payload.key`): é por ela que a pergunta respondida não volta."""
        chave = self.payload.get("key") if self.payload else None
        return chave if isinstance(chave, str) else None

    @property
    def question_kind(self) -> QuestionKind:
        bruto = self.payload.get("kind") if self.payload else None
        try:
            return QuestionKind(bruto) if isinstance(bruto, str) else QuestionKind.AMBIGUITY
        except ValueError:
            return QuestionKind.AMBIGUITY


# ------------------------------------------------------------------ candidata
@dataclass(frozen=True, slots=True)
class InferredParameter:
    """Parâmetro que o generalizador inferiu: o tipo sai do exemplo, e o exemplo é o valor da demonstração."""

    name: str
    type: str
    examples: tuple[str, ...]
    description: str = ""
    required: bool = True

    def to_json(self) -> JsonObject:
        return {"name": self.name, "type": self.type, "examples": list(self.examples),
                "description": self.description, "required": self.required}


@dataclass(frozen=True, slots=True)
class ProposedQuestion:
    """Pergunta que só a pessoa responde. `key` é estável entre gerações: a mesma dúvida tem a mesma chave, e por
    isso uma pergunta respondida não é feita de novo."""

    key: str
    kind: QuestionKind
    text: str
    origin: TurnAuthor                      # ai (o generalizador) | compiler (erro que só uma pessoa resolve)
    target: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class CandidateAnnotations:
    """O que acompanha o documento sem fazer parte dele: não é validado, não entra no hash da versão."""

    evidence: Mapping[str, tuple[int, ...]] = field(default_factory=dict)      # nó → seqs gravadas
    discarded: tuple[tuple[int, str], ...] = ()                                  # (seq, por quê)
    assumptions: tuple[str, ...] = ()
    parameters: tuple[InferredParameter, ...] = ()
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[JsonObject, ...] = ()                                  # {node, kind, value}
    suggested_proofs: tuple[JsonObject, ...] = ()
    effects: tuple[JsonObject, ...] = ()                                         # {node, capability, description}
    risks: tuple[str, ...] = ()

    def to_json(self) -> JsonObject:
        return {
            "evidence": {k: list(v) for k, v in sorted(self.evidence.items())},
            "discarded": [{"seq": s, "why": w} for s, w in self.discarded],
            "assumptions": list(self.assumptions),
            "parameters": [p.to_json() for p in self.parameters],
            "preconditions": list(self.preconditions),
            "postconditions": [dict(x) for x in self.postconditions],
            "suggested_proofs": [dict(x) for x in self.suggested_proofs],
            "effects": [dict(x) for x in self.effects],
            "risks": list(self.risks),
        }

    @classmethod
    def from_json(cls, doc: JsonObject) -> CandidateAnnotations:
        evid = doc.get("evidence")
        evidencia = {k: tuple(i for i in v if isinstance(i, int) and not isinstance(i, bool))
                     for k, v in evid.items() if isinstance(v, list)} if isinstance(evid, dict) else {}
        descartes = tuple((d["seq"], d["why"]) for d in _objetos(doc.get("discarded"))
                          if isinstance(d.get("seq"), int) and isinstance(d.get("why"), str))
        parametros = []
        for p in _objetos(doc.get("parameters")):
            nome, tipo = p.get("name"), p.get("type")
            if isinstance(nome, str) and isinstance(tipo, str):
                desc, obrig = p.get("description"), p.get("required")
                parametros.append(InferredParameter(
                    nome, tipo, tuple(x for x in _lista(p.get("examples")) if isinstance(x, str)),
                    desc if isinstance(desc, str) else "", obrig if isinstance(obrig, bool) else True))
        return cls(evidence=evidencia, discarded=_pares(descartes), assumptions=_textos(doc.get("assumptions")),
                   parameters=tuple(parametros), preconditions=_textos(doc.get("preconditions")),
                   postconditions=tuple(_objetos(doc.get("postconditions"))),
                   suggested_proofs=tuple(_objetos(doc.get("suggested_proofs"))),
                   effects=tuple(_objetos(doc.get("effects"))), risks=_textos(doc.get("risks")))


@dataclass(frozen=True, slots=True)
class CandidateEnvelope:
    """`teaching_candidates.content` (§13.2): `document` é o `SkillDocument` v1alpha1 — só ele vai para
    `skill_versions.content` — e `annotations` é o resto."""

    document: JsonObject
    annotations: CandidateAnnotations

    def to_json(self) -> JsonObject:
        return {"document": as_json_object(self.document), "annotations": self.annotations.to_json()}

    @classmethod
    def from_json(cls, doc: JsonObject) -> CandidateEnvelope:
        documento = doc.get("document")
        anotacoes = doc.get("annotations")
        return cls(document=documento if isinstance(documento, dict) else {},
                   annotations=CandidateAnnotations.from_json(anotacoes if isinstance(anotacoes, dict) else {}))

    @property
    def document_hash(self) -> str:
        """O hash do DOCUMENTO: é o mesmo `content_hash` que a versão terá ao ser aceita (o envelope não entra)."""
        return content_hash(self.document)


@dataclass(frozen=True, slots=True)
class SkillCandidate:
    id: str
    teaching_id: str
    seq: int
    envelope: CandidateEnvelope
    content_hash: str
    generated_by: str                       # ai:<modelo> | person | merge
    status: CandidateStatus
    validation_status: TeachingValidation
    version_id: str | None
    created_at: str
    updated_at: str


# ------------------------------------------------------------------ o que o generalizador recebe e devolve
@dataclass(frozen=True, slots=True)
class RecordedInput:
    """Uma entrada gravada pelo gravador v1 (`training_inputs`). O texto já vem nulo quando era sigiloso."""

    seq: int
    type: str
    package: str | None = None
    app_id: str | None = None
    text: str | None = None
    has_text: bool = False
    text_len: int | None = None
    target: JsonObject | None = None
    screen_title: str | None = None
    screen_lines: tuple[str, ...] = ()
    x: int | None = None
    y: int | None = None
    x2: int | None = None
    y2: int | None = None
    key_name: str | None = None
    sensitive: bool = False

    @property
    def secret_text(self) -> bool:
        """Digitou algo que o gravador não guardou (senha, código, tela sensível)."""
        return self.type == "text" and self.text is None and self.has_text


@dataclass(frozen=True, slots=True)
class Recording:
    """Uma gravação v1, lida sem mudar nada: o gravador continua o mesmo (§13.3)."""

    id: str
    status: str                             # recording | recorded | proposed | saved | discarded
    instance_id: str
    profile_id: str | None
    app_id: str | None
    intent: str
    inputs: tuple[RecordedInput, ...]

    @property
    def still_recording(self) -> bool:
        return self.status == "recording"


@dataclass(frozen=True, slots=True)
class AnsweredQuestion:
    key: str
    kind: QuestionKind
    question: str
    answer: str


@dataclass(frozen=True, slots=True)
class GeneralizationRequest:
    teaching_id: str
    skill_id: str                           # o id que o documento deve declarar (a habilidade existente, ou a nova)
    app_id: str
    instruction: str
    inputs: tuple[RecordedInput, ...] = ()
    answers: tuple[AnsweredQuestion, ...] = ()
    corrections: tuple[str, ...] = ()
    example_commands: tuple[str, ...] = ()  # comandos de execuções comprovadas usadas como exemplo
    base_version: str | None = None         # `skill@N` que este ensino melhora


@dataclass(frozen=True, slots=True)
class Generalization:
    envelope: CandidateEnvelope
    questions: tuple[ProposedQuestion, ...]
    generated_by: str


# ------------------------------------------------------------------ regras puras
def source_of(session: TeachingSession, demonstrations: Sequence[Demonstration],
              turns: Sequence[TeachingTurn]) -> TeachingSource:
    """A fonte do ensino, pelo conteúdo (§13.1). Correção vence (é melhoria de uma habilidade que falhou); execução
    comprovada sem gravação é `successful_execution`; gravação com instrução é `hybrid` (o treino v1)."""
    gravacoes = any(d.kind is DemonstrationKind.RECORDING for d in demonstrations)
    execucoes = any(d.kind is DemonstrationKind.RUN for d in demonstrations)
    if session.skill_id is not None and any(t.kind is TurnKind.CORRECTION for t in turns):
        return TeachingSource.CORRECTION
    if execucoes and not gravacoes:
        return TeachingSource.SUCCESSFUL_EXECUTION
    if gravacoes:
        return TeachingSource.HYBRID if session.instruction.strip() else TeachingSource.DEMONSTRATION
    return TeachingSource.INSTRUCTION


def open_questions(turns: Sequence[TeachingTurn], candidate_id: str) -> list[TeachingTurn]:
    """Perguntas da candidata ainda sem resposta, na ordem em que foram feitas."""
    respondidas = {t.reply_to for t in turns if t.kind is TurnKind.ANSWER and t.reply_to is not None}
    return [t for t in turns if t.kind is TurnKind.QUESTION and t.candidate_id == candidate_id
            and t.id not in respondidas]


def answered_questions(turns: Sequence[TeachingTurn]) -> list[AnsweredQuestion]:
    """Todas as perguntas respondidas do ensino (de qualquer candidata): o generalizador não as refaz."""
    por_id = {t.id: t for t in turns if t.kind is TurnKind.QUESTION}
    saida: list[AnsweredQuestion] = []
    for t in turns:
        pergunta = por_id.get(t.reply_to) if t.kind is TurnKind.ANSWER and t.reply_to is not None else None
        if pergunta is not None and pergunta.question_key is not None:
            saida.append(AnsweredQuestion(pergunta.question_key, pergunta.question_kind, pergunta.body or "",
                                          t.body or ""))
    return saida


def suggest_skill_id(app_id: str, text: str) -> str:
    """Um id de habilidade nova a partir do app e da instrução: `<app>.<primeiras palavras>`.

    É só sugestão, e é determinística (a mesma instrução dá o mesmo id). Um id que já existe é recusado na
    submissão — nunca vira, calado, a versão N+1 de outra habilidade.
    """
    def slug(s: str, sep: str) -> str:
        ascii_ = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()
        return re.sub(r"[^a-z0-9]+", sep, ascii_).strip(sep)

    app = slug(app_id, "-") or "app"
    palavras = [p for p in slug(text, " ").split() if len(p) > 2][:5]
    corpo = "_".join(palavras) or "ensinada"
    candidato = f"{app}.{corpo}"[:64].rstrip("._-")
    if not candidato[:1].isalpha():
        candidato = f"s{candidato}"[:64]
    if re.fullmatch(SKILL_ID, candidato):
        return candidato
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"skill.{digest}"


# ------------------------------------------------------------------ auxiliares de leitura
def _lista(v: JsonValue) -> list[JsonValue]:
    return v if isinstance(v, list) else []


def _objetos(v: JsonValue) -> list[JsonObject]:
    return [x for x in _lista(v) if isinstance(x, dict)]


def _textos(v: JsonValue) -> tuple[str, ...]:
    return tuple(x for x in _lista(v) if isinstance(x, str))


def _pares(pares: tuple[tuple[JsonValue, JsonValue], ...]) -> tuple[tuple[int, str], ...]:
    return tuple((s, w) for s, w in pares if isinstance(s, int) and isinstance(w, str))
