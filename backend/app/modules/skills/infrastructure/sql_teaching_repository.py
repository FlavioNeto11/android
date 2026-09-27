"""`SqlTeachingRepository`: o ensino v2 nas tabelas da 044 (`teaching_sessions`, `_demonstrations`, `_turns`,
`_candidates`) e a LEITURA do que o ensino cita — gravação v1, execução, etapa e apps. Nada daqui escreve em
`training_sessions` nem em `training_inputs`: o gravador não muda (§13.3).

- Toda mudança de estado é CAS (`WHERE id=? AND status=?`), de sessão e de candidata: quem perde a corrida recebe
  `TeachingStateConflict`, e não uma escrita por cima da outra.
- `atomic()` é o `Database.tx()`, reentrante: o `SqlSkillRepository.create_draft` chamado dentro dele entra na MESMA
  transação, e é isso que faz "candidata → versão" ser uma coisa só.
- `Row` não sai daqui: cada coluna é conferida no tipo ao virar objeto do domínio (`rows.py`).
- Tipos da migração em todo INSERT (`seq`, `base_version`, `reply_to` são INTEGER): no SQLite um texto numa coluna
  INTEGER passa, e no PostgreSQL não (K-029).
"""
from __future__ import annotations

import json
from contextlib import AbstractContextManager

from app.db import INTEGRITY_ERRORS, Database, Row
from app.modules.skills.domain.document import JsonObject, canonical_json, parse_json_object
from app.modules.skills.domain.teaching import (TERMINAL, CandidateEnvelope, CandidateStatus, Demonstration,
                                                DemonstrationKind, NewTurn, RecordedInput, Recording, SkillCandidate,
                                                TeachingSession, TeachingStateConflict, TeachingStatus, TeachingTurn,
                                                TeachingValidation, TurnAuthor, TurnKind, check_move)
from app.modules.skills.infrastructure.rows import inteiro, inteiro_ou_nulo, texto, texto_ou_nulo


class SqlTeachingRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def atomic(self) -> AbstractContextManager[object]:
        return self._db.tx()

    # ================================================================== sessões
    def create_session(self, session: TeachingSession) -> None:
        s = session
        self._db.execute(
            "INSERT INTO teaching_sessions(id, instruction, skill_id, base_version, app_id, profile_id, status,"
            " validation_status, result_version_id, operator, created_at, updated_at, closed_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s.id, s.instruction, s.skill_id, s.base_version, s.app_id, s.profile_id, s.status.value,
             s.validation_status.value, s.result_version_id, s.operator, s.created_at, s.updated_at, s.closed_at))

    def session(self, teaching_id: str) -> TeachingSession | None:
        row = self._db.one("SELECT * FROM teaching_sessions WHERE id=?", (teaching_id,))
        return _sessao(row) if row is not None else None

    def sessions(self, *, status: TeachingStatus | None = None, limit: int = 50) -> list[TeachingSession]:
        if status is None:
            linhas = self._db.query("SELECT * FROM teaching_sessions ORDER BY updated_at DESC, id LIMIT ?", (limit,))
        else:
            linhas = self._db.query("SELECT * FROM teaching_sessions WHERE status=? ORDER BY updated_at DESC, id"
                                    " LIMIT ?", (status.value, limit))
        return [_sessao(r) for r in linhas]

    def move(self, teaching_id: str, frm: TeachingStatus, to: TeachingStatus, *, at: str,
             validation: TeachingValidation | None = None, result_version_id: str | None = None) -> None:
        check_move(frm, to)
        campos = ["status=?", "updated_at=?"]
        valores: list[str] = [to.value, at]
        if validation is not None:
            campos.append("validation_status=?")
            valores.append(validation.value)
        if result_version_id is not None:
            campos.append("result_version_id=?")
            valores.append(result_version_id)
        if to in TERMINAL:
            campos.append("closed_at=?")
            valores.append(at)
        cur = self._db.execute(f"UPDATE teaching_sessions SET {', '.join(campos)} WHERE id=? AND status=?",
                               (*valores, teaching_id, frm.value))
        if int(cur.rowcount or 0) != 1:
            raise TeachingStateConflict(f"O ensino {teaching_id} saiu de '{frm}' antes desta mudança: releia e "
                                        "tente de novo.")

    # ================================================================== demonstrações
    def add_demonstration(self, demo: Demonstration) -> None:
        d = demo
        try:
            self._db.execute(
                "INSERT INTO teaching_demonstrations(id, teaching_id, seq, kind, training_session_id, run_id,"
                " instance_id, app_snapshot, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (d.id, d.teaching_id, d.seq, d.kind.value, d.training_session_id, d.run_id, d.instance_id,
                 canonical_json(d.app_snapshot) if d.app_snapshot is not None else None, d.note, d.created_at))
        except INTEGRITY_ERRORS as exc:
            raise TeachingStateConflict("Esta gravação já pertence a um ensino (uma gravação, um ensino).",
                                        code="recording_taken") from exc

    def demonstrations(self, teaching_id: str) -> list[Demonstration]:
        return [_demonstracao(r) for r in self._db.query(
            "SELECT * FROM teaching_demonstrations WHERE teaching_id=? ORDER BY seq", (teaching_id,))]

    def teaching_of_recording(self, training_session_id: str) -> str | None:
        """O ensino que usa esta gravação (no máximo um: índice `ux_teaching_demo_gravacao`)."""
        row = self._db.one("SELECT teaching_id FROM teaching_demonstrations WHERE training_session_id=?",
                           (training_session_id,))
        return texto(row, "teaching_id") if row is not None else None

    def next_demonstration_seq(self, teaching_id: str) -> int:
        return self._proximo("teaching_demonstrations", teaching_id)

    # ================================================================== conversa
    def add_turn(self, turn: NewTurn, *, at: str) -> TeachingTurn:
        t = turn
        novo = self._db.inserted_id(
            "INSERT INTO teaching_turns(teaching_id, kind, author, reply_to, target, body, payload, candidate_id,"
            " created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (t.teaching_id, t.kind.value, t.author.value, t.reply_to,
             canonical_json(t.target) if t.target is not None else None, t.body,
             canonical_json(t.payload) if t.payload is not None else None, t.candidate_id, t.created_by, at))
        row = self._db.one("SELECT * FROM teaching_turns WHERE id=?", (novo,))
        if row is None:
            raise TeachingStateConflict("O turno sumiu durante a gravação.")
        return _turno(row)

    def turns(self, teaching_id: str) -> list[TeachingTurn]:
        return [_turno(r) for r in self._db.query(
            "SELECT * FROM teaching_turns WHERE teaching_id=? ORDER BY id", (teaching_id,))]

    # ================================================================== candidatas
    def add_candidate(self, candidate: SkillCandidate) -> None:
        c = candidate
        self._db.execute(
            "INSERT INTO teaching_candidates(id, teaching_id, seq, content, content_hash, generated_by, status,"
            " validation_status, version_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (c.id, c.teaching_id, c.seq, canonical_json(c.envelope.to_json()), c.content_hash, c.generated_by,
             c.status.value, c.validation_status.value, c.version_id, c.created_at, c.updated_at))

    def candidate(self, candidate_id: str) -> SkillCandidate | None:
        row = self._db.one("SELECT * FROM teaching_candidates WHERE id=?", (candidate_id,))
        return _candidata(row) if row is not None else None

    def candidates(self, teaching_id: str) -> list[SkillCandidate]:
        return [_candidata(r) for r in self._db.query(
            "SELECT * FROM teaching_candidates WHERE teaching_id=? ORDER BY seq", (teaching_id,))]

    def next_candidate_seq(self, teaching_id: str) -> int:
        return self._proximo("teaching_candidates", teaching_id)

    def set_candidate(self, candidate_id: str, frm: CandidateStatus, to: CandidateStatus, *, at: str,
                      validation: TeachingValidation | None = None, version_id: str | None = None) -> None:
        campos = ["status=?", "updated_at=?"]
        valores: list[str] = [to.value, at]
        if validation is not None:
            campos.append("validation_status=?")
            valores.append(validation.value)
        if version_id is not None:
            campos.append("version_id=?")
            valores.append(version_id)
        cur = self._db.execute(f"UPDATE teaching_candidates SET {', '.join(campos)} WHERE id=? AND status=?",
                               (*valores, candidate_id, frm.value))
        if int(cur.rowcount or 0) != 1:
            raise TeachingStateConflict(f"A candidata {candidate_id} saiu de '{frm}' antes desta mudança.")

    def supersede_candidates(self, teaching_id: str, *, at: str) -> None:
        self._db.execute("UPDATE teaching_candidates SET status=?, updated_at=? WHERE teaching_id=? AND status=?",
                         (CandidateStatus.SUPERSEDED.value, at, teaching_id, CandidateStatus.PROPOSED.value))

    # ================================================================== só leitura
    def recording(self, training_session_id: str) -> Recording | None:
        row = self._db.one("SELECT * FROM training_sessions WHERE id=?", (training_session_id,))
        if row is None:
            return None
        entradas = tuple(_entrada(r) for r in self._db.query(
            "SELECT * FROM training_inputs WHERE session_id=? ORDER BY seq", (training_session_id,)))
        return Recording(id=texto(row, "id"), status=texto(row, "status"), instance_id=texto(row, "instance_id"),
                         profile_id=texto_ou_nulo(row, "profile_id"), app_id=texto_ou_nulo(row, "app_id"),
                         intent=texto(row, "intent"), inputs=entradas)

    def run_status(self, run_id: str) -> tuple[str, str] | None:
        row = self._db.one("SELECT status, command FROM runs WHERE id=?", (run_id,))
        return (texto(row, "status"), texto(row, "command")) if row is not None else None

    def step_status(self, step_id: str) -> tuple[str, str] | None:
        row = self._db.one("SELECT run_id, status FROM steps WHERE id=?", (step_id,))
        return (texto(row, "run_id"), texto(row, "status")) if row is not None else None

    def app_exists(self, app_id: str) -> bool:
        return self._db.one("SELECT id FROM apps WHERE id=?", (app_id,)) is not None

    def app_of_package(self, package: str) -> str | None:
        row = self._db.one("SELECT id FROM apps WHERE package=? ORDER BY id", (package,))
        return texto(row, "id") if row is not None else None

    # ================================================================== auxiliares
    def _proximo(self, tabela: str, teaching_id: str) -> int:
        row = self._db.one(f"SELECT MAX(seq) AS n FROM {tabela} WHERE teaching_id=?", (teaching_id,))
        return (inteiro_ou_nulo(row, "n") or 0) + 1 if row is not None else 1


# ------------------------------------------------------------------ linha → domínio
def _json_ou_nulo(row: Row, coluna: str) -> JsonObject | None:
    bruto = texto_ou_nulo(row, coluna)
    return parse_json_object(bruto) if bruto else None


def _sessao(row: Row) -> TeachingSession:
    return TeachingSession(
        id=texto(row, "id"), instruction=texto(row, "instruction"), skill_id=texto_ou_nulo(row, "skill_id"),
        base_version=inteiro_ou_nulo(row, "base_version"), app_id=texto_ou_nulo(row, "app_id"),
        profile_id=texto_ou_nulo(row, "profile_id"), status=TeachingStatus(texto(row, "status")),
        validation_status=TeachingValidation(texto(row, "validation_status")),
        result_version_id=texto_ou_nulo(row, "result_version_id"), operator=texto_ou_nulo(row, "operator"),
        created_at=texto(row, "created_at"), updated_at=texto(row, "updated_at"),
        closed_at=texto_ou_nulo(row, "closed_at"))


def _demonstracao(row: Row) -> Demonstration:
    return Demonstration(
        id=texto(row, "id"), teaching_id=texto(row, "teaching_id"), seq=inteiro(row, "seq"),
        kind=DemonstrationKind(texto(row, "kind")), training_session_id=texto_ou_nulo(row, "training_session_id"),
        run_id=texto_ou_nulo(row, "run_id"), instance_id=texto_ou_nulo(row, "instance_id"),
        app_snapshot=_json_ou_nulo(row, "app_snapshot"), note=texto_ou_nulo(row, "note"),
        created_at=texto(row, "created_at"))


def _turno(row: Row) -> TeachingTurn:
    return TeachingTurn(
        id=inteiro(row, "id"), teaching_id=texto(row, "teaching_id"), kind=TurnKind(texto(row, "kind")),
        author=TurnAuthor(texto(row, "author")), reply_to=inteiro_ou_nulo(row, "reply_to"),
        target=_json_ou_nulo(row, "target"), body=texto_ou_nulo(row, "body"), payload=_json_ou_nulo(row, "payload"),
        candidate_id=texto_ou_nulo(row, "candidate_id"), created_by=texto_ou_nulo(row, "created_by"),
        created_at=texto(row, "created_at"))


def _candidata(row: Row) -> SkillCandidate:
    return SkillCandidate(
        id=texto(row, "id"), teaching_id=texto(row, "teaching_id"), seq=inteiro(row, "seq"),
        envelope=CandidateEnvelope.from_json(parse_json_object(texto(row, "content"))),
        content_hash=texto(row, "content_hash"), generated_by=texto(row, "generated_by"),
        status=CandidateStatus(texto(row, "status")),
        validation_status=TeachingValidation(texto(row, "validation_status")),
        version_id=texto_ou_nulo(row, "version_id"), created_at=texto(row, "created_at"),
        updated_at=texto(row, "updated_at"))


def _linhas_da_tela(bruto: str | None) -> tuple[str, ...]:
    """`training_inputs.screen_lines` é uma LISTA JSON (o gravador grava assim); o que não for texto fica de fora."""
    if not bruto:
        return ()
    try:
        valor = json.loads(bruto)
    except ValueError:
        return ()
    return tuple(x for x in valor if isinstance(x, str)) if isinstance(valor, list) else ()


def _entrada(row: Row) -> RecordedInput:
    return RecordedInput(
        seq=inteiro(row, "seq"), type=texto(row, "type"), package=texto_ou_nulo(row, "package"),
        app_id=texto_ou_nulo(row, "app_id"), text=texto_ou_nulo(row, "text"),
        has_text=bool(inteiro(row, "has_text")), text_len=inteiro_ou_nulo(row, "text_len"),
        target=_json_ou_nulo(row, "target"), screen_title=texto_ou_nulo(row, "screen_title"),
        screen_lines=_linhas_da_tela(texto_ou_nulo(row, "screen_lines")),
        x=inteiro_ou_nulo(row, "x"), y=inteiro_ou_nulo(row, "y"), x2=inteiro_ou_nulo(row, "x2"),
        y2=inteiro_ou_nulo(row, "y2"), key_name=texto_ou_nulo(row, "key_name"),
        sensitive=bool(inteiro(row, "sensitive")))
