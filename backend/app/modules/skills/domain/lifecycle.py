"""Ciclo de vida de uma versão de habilidade (§10.3, ADR-034): estados, QUEM pode mover, e as recusas.

A tabela de transições é explícita e fechada: o que não está nela é proibido. É o mesmo desenho de
`commands/states.py` — a regra vive num dicionário que um teste percorre estado × estado, e não espalhada em `if`.

Pontos que a tabela sozinha não diz, e que o repositório confere na mesma transação:
- `draft → candidate` exige documento sem erro (o `content_hash` fica fixado daqui em diante);
- `candidate → validated` exige observação registrada (regra P4; `validation.py`), ou validação MANUAL de uma pessoa,
  com motivo, registrada na transição;
- `validated|deprecated → published` exige conteúdo íntegro e comando livre, e deprecia a publicada anterior;
- editar é só em `draft` (`update_draft`), e não é transição: não aparece aqui.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum

#: Quem decide quando não é uma pessoa (a validação automática, a substituição da publicada anterior).
SYSTEM_ACTOR = "sistema"


class SkillState(StrEnum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"


class Actor(StrEnum):
    PERSON = "person"
    SYSTEM = "system"


_P = frozenset({Actor.PERSON})
_PS = frozenset({Actor.PERSON, Actor.SYSTEM})
_S = SkillState

#: (de, para) → quem pode. Não existe `candidate → draft` nem `validated → draft` (conteúdo congelado: mudar é
#: criar uma nova `draft` com `parent_version`), e `disabled` é terminal.
TRANSITIONS: Mapping[tuple[SkillState, SkillState], frozenset[Actor]] = {
    (_S.DRAFT, _S.CANDIDATE): _P,            # "submeter"; compilação sem erro
    (_S.CANDIDATE, _S.VALIDATED): _PS,       # sistema, pelas observações; pessoa, só como validação manual (P4)
    (_S.CANDIDATE, _S.DISABLED): _P,         # abandonar uma candidata congelada
    (_S.VALIDATED, _S.PUBLISHED): _P,
    (_S.VALIDATED, _S.DISABLED): _P,
    (_S.PUBLISHED, _S.DEPRECATED): _PS,      # sistema, quando outra versão da mesma habilidade é publicada
    (_S.PUBLISHED, _S.DISABLED): _P,         # parada de emergência: o resolvedor deixa de casar na hora
    (_S.DEPRECATED, _S.PUBLISHED): _P,       # rollback; as validações continuam valendo (o conteúdo é o mesmo)
    (_S.DEPRECATED, _S.DISABLED): _P,
}

#: Estado em que o conteúdo ainda muda.
EDITABLE = frozenset({SkillState.DRAFT})
TERMINAL = frozenset(s for s in SkillState if not any(de == s for de, _ in TRANSITIONS) and s not in EDITABLE)


def actor_of(by: str) -> Actor:
    return Actor.SYSTEM if by == SYSTEM_ACTOR else Actor.PERSON


def allowed(frm: SkillState, to: SkillState, actor: Actor) -> bool:
    return actor in TRANSITIONS.get((frm, to), frozenset())


# ------------------------------------------------------------------ recusas
class SkillError(Exception):
    """Recusa de uma regra de habilidade. `code` é estável (vai para a API); a mensagem é para a pessoa."""

    code = "skill_error"


class SkillNotFound(SkillError):
    code = "not_found"


class TransitionForbidden(SkillError):
    code = "transition_forbidden"


class FrozenVersion(SkillError):
    """Conteúdo de versão fora de `draft` não muda (e só `draft` se apaga)."""

    code = "frozen_version"


class ContentTampered(SkillError):
    """O `content_hash` gravado não bate com o conteúdo lido: alguém mudou a versão por fora do repositório."""

    code = "content_tampered"


class DuplicateCommand(SkillError):
    """`E_DUPLICATE_COMMAND`: outro comando publicado (habilidade ou fluxo ativo) casa com o mesmo modelo."""

    code = "E_DUPLICATE_COMMAND"


class InvalidDocument(SkillError):
    code = "invalid_document"

    def __init__(self, message: str, errors: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.errors = tuple(errors)


class ValidationPending(SkillError):
    code = "validation_pending"

    def __init__(self, message: str, pending: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.pending = tuple(pending)


class StateConflict(SkillError):
    """A linha mudou entre a leitura e a escrita (outra sessão chegou antes), ou está referenciada."""

    code = "state_conflict"


def check_transition(frm: SkillState, to: SkillState, by: str) -> Actor:
    """Confere a tabela e devolve quem está movendo. Recusa com a razão legível."""
    if not by.strip():
        raise TransitionForbidden("Toda transição precisa dizer quem decidiu.")
    actor = actor_of(by)
    if (frm, to) not in TRANSITIONS:
        if frm in EDITABLE and to is SkillState.DRAFT:
            raise TransitionForbidden("Editar um rascunho não é transição: use a edição do rascunho.")
        if frm is not SkillState.DRAFT and to is SkillState.DRAFT:
            raise TransitionForbidden(f"Versão em '{frm}' não volta a rascunho: crie uma nova versão a partir dela.")
        raise TransitionForbidden(f"Transição proibida: {frm} → {to}.")
    if not allowed(frm, to, actor):
        quem = "uma pessoa" if actor is Actor.SYSTEM else "o sistema"
        raise TransitionForbidden(f"{frm} → {to} é decisão de {quem}, não de '{by}'.")
    return actor
