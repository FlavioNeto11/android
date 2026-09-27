"""`TeachingService`: o ensino v2 de ponta a ponta (§13), sobre portas — sem banco, sem HTTP e sem IA aqui.

O caminho feliz: `start` (instrução) → `attach_recording`/`attach_run` (demonstrações) → `propose` (o generalizador
gera a candidata; se ela tem perguntas, a sessão fica em `asking`) → `answer` (uma resposta por pergunta) →
`propose` de novo (a candidata nova já sabe as respostas) → `validate` (estática: o compilador) → `publish` (a
candidata vira `skill_versions` em DRAFT, numa transação). Publicar a HABILIDADE continua sendo outra decisão da
pessoa, pelo ciclo de vida da versão (§10.3): o ensino nunca publica nada sozinho.

Três garantias que moram aqui, e não na borda:
- **Credencial nunca entra.** Instrução, resposta, correção e nota com credencial são recusadas antes de gravar
  (o texto vai ao provedor de IA); a candidata com valor em formato de segredo é rejeitada e gravada MASCARADA.
- **Toda transição é CAS** (`WHERE status=?`): dois cliques em "propor" não geram duas candidatas pagas.
- **Candidata → versão numa transação só**: o rascunho, a candidata aceita e a sessão fechada entram juntos, ou
  nada entra.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.modules.skills.application.ports import (DocumentValidator, SecretScreen, SkillDraftStore, SkillGeneralizer,
                                                  TeachingRepository)
from app.modules.skills.domain.document import JsonObject, JsonValue, as_json_object
from app.modules.skills.domain.generalization import compiler_questions
from app.modules.skills.domain.refs import SkillRef
from app.modules.skills.domain.teaching import (ACCEPTS_DEMONSTRATION, CandidateEnvelope,
                                                CandidateStatus, CredentialInText, Demonstration, DemonstrationKind,
                                                Generalization, GeneralizationRequest, NewTurn,
                                                Recording, SecretInCandidate, SkillCandidate, TeachingInputInvalid,
                                                TeachingNotFound, TeachingSession, TeachingSource,
                                                TeachingStateConflict, TeachingStatus, TeachingTurn,
                                                TeachingValidation, TurnAuthor, TurnKind, answered_questions,
                                                check_move, open_questions, source_of, suggest_skill_id)
from app.modules.skills.domain.versions import DocumentFacts, Provenance, SourceKind
from app.util import new_token, now_iso

S = TeachingStatus
MASK = "**REDACTED**"
#: Folhas do documento que são DADO (exemplo, padrão, argumento, valor de caso): nelas vale também a regra de palavra
#: com cara de senha. No resto (id, apiVersion, texto de objetivo) só o formato, senão `automation/v1alpha1` —
#: minúscula, dígito e barra — seria "senha".
_FOLHA_DE_DADO = re.compile(r"\.(example|default)$|\.examples\[\d+\]$|\.with\.[^.\[]+$|\.parameters\.[^.\[]+$")
#: Teto do texto da pessoa: a instrução vai inteira ao prompt do generalizador.
MAX_TEXT = 2000
#: Etapa que pode ser corrigida: a que falhou ou ficou sem prova (§13.1, fonte `correction`).
CORRECTABLE_STEP = frozenset({"failed", "uncertain"})


@dataclass(frozen=True, slots=True)
class TeachingView:
    """A sessão como o painel a mostra: a fonte derivada, a conversa, as candidatas e o que falta."""

    session: TeachingSession
    source: TeachingSource
    demonstrations: tuple[Demonstration, ...]
    turns: tuple[TeachingTurn, ...]
    candidates: tuple[SkillCandidate, ...]
    current: SkillCandidate | None           # a candidata mais recente (maior `seq`)
    open_questions: tuple[TeachingTurn, ...]
    errors: tuple[str, ...]                   # erros de compilação da candidata atual, lidos agora


@dataclass(frozen=True, slots=True)
class CandidateDetail:
    candidate: SkillCandidate
    session: TeachingSession
    facts: DocumentFacts
    open_questions: tuple[TeachingTurn, ...]


def _new_id(prefix: str) -> str:
    return f"{prefix}-{new_token()}"


class TeachingService:
    def __init__(self, repo: TeachingRepository, skills: SkillDraftStore, validator: DocumentValidator,
                 generalizer: SkillGeneralizer, secrets: SecretScreen, *, enabled: Callable[[], bool],
                 clock: Callable[[], str] = now_iso, new_id: Callable[[str], str] = _new_id,
                 on_updated: Callable[[str, str], None] | None = None) -> None:
        """`enabled`: o `skills.enabled` da instalação, lido a cada chamada (decisão P1). Quem barra as rotas com ele
        é a apresentação; o serviço o expõe para que a borda não precise conhecer a configuração."""
        self._repo = repo
        self._skills = skills
        self._validator = validator
        self._generalizer = generalizer
        self._secrets = secrets
        self._enabled = enabled
        self._clock = clock
        self._new_id = new_id
        self._on_updated = on_updated

    @property
    def enabled(self) -> bool:
        return self._enabled()

    # ================================================================== leitura
    def get(self, teaching_id: str) -> TeachingView:
        return self._view(self._settle(self._session(teaching_id)))

    def list_sessions(self, *, status: TeachingStatus | None = None, limit: int = 50,
                      training_session_id: str | None = None) -> list[TeachingView]:
        """`training_session_id`: o ensino que usa aquela gravação v1 (é como a revisão do treino o reencontra)."""
        if training_session_id is not None:
            tid = self._repo.teaching_of_recording(training_session_id)
            sessao = self._repo.session(tid) if tid is not None else None
            return [self._view(sessao)] if sessao is not None else []
        return [self._view(s) for s in self._repo.sessions(status=status, limit=limit)]

    def candidate(self, candidate_id: str) -> CandidateDetail:
        c = self._candidate(candidate_id)
        s = self._session(c.teaching_id)
        return CandidateDetail(c, s, self._validator.inspect(c.envelope.document),
                               tuple(open_questions(self._repo.turns(s.id), c.id)))

    def compile(self, candidate_id: str) -> DocumentFacts:
        """Compila a candidata agora, sem mudar nada: o painel mostra os erros antes de validar."""
        return self._validator.inspect(self._candidate(candidate_id).envelope.document)

    # ================================================================== sessão
    def start(self, instruction: str, *, skill_id: str | None = None, base_version: int | None = None,
              app_id: str | None = None, profile_id: str | None = None, operator: str | None = None) -> TeachingView:
        texto = self._person_text(instruction, "instrução", allow_empty=True)
        if skill_id is not None:
            definicao = self._skills.definition(skill_id)
            if definicao is None:
                raise TeachingNotFound(f"Habilidade não encontrada: {skill_id}.")
            if base_version is not None:
                self._skills.get(SkillRef(skill_id, base_version))       # SkillNotFound se não existir
            app_id = app_id or definicao.app_id
        elif base_version is not None:
            raise TeachingInputInvalid("base_version só vale junto de skill_id (o ensino que melhora a versão N).")
        if app_id is not None and not self._repo.app_exists(app_id):
            raise TeachingInputInvalid(f"Aplicativo '{app_id}' não está cadastrado.", code="unknown_app")
        agora = self._clock()
        sessao = TeachingSession(id=self._new_id("ens"), instruction=texto, skill_id=skill_id,
                                 base_version=base_version, app_id=app_id, profile_id=profile_id, status=S.OPEN,
                                 validation_status=TeachingValidation.NONE, result_version_id=None,
                                 operator=operator, created_at=agora, updated_at=agora, closed_at=None)
        with self._repo.atomic():
            self._repo.create_session(sessao)
            if texto:
                self._repo.add_turn(NewTurn(sessao.id, TurnKind.INSTRUCTION, TurnAuthor.PERSON, body=texto,
                                            created_by=operator), at=agora)
        self._notify(sessao.id, "ensino iniciado")
        return self._view(sessao)

    def attach_recording(self, teaching_id: str, training_session_id: str, *, note: str | None = None,
                         by: str | None = None) -> TeachingView:
        """Liga uma gravação v1 ao ensino. O gravador não muda: a demonstração só APONTA para a gravação, e uma
        gravação pertence a no máximo um ensino (índice único da 044)."""
        sessao = self._accepting_demonstration(teaching_id)
        gravacao = self._repo.recording(training_session_id)
        if gravacao is None:
            raise TeachingNotFound(f"Gravação não encontrada: {training_session_id}.")
        if gravacao.status == "discarded":
            raise TeachingInputInvalid("Esta gravação foi descartada.", code="recording_discarded")
        nota = self._person_text(note or "", "nota", allow_empty=True) or None
        agora = self._clock()
        with self._repo.atomic():
            self._repo.add_demonstration(Demonstration(
                id=self._new_id("dem"), teaching_id=sessao.id, seq=self._repo.next_demonstration_seq(sessao.id),
                kind=DemonstrationKind.RECORDING, training_session_id=gravacao.id, run_id=None,
                instance_id=gravacao.instance_id, app_snapshot=None, note=nota, created_at=agora))
            if gravacao.still_recording and sessao.status is S.OPEN:
                self._repo.move(sessao.id, S.OPEN, S.DEMONSTRATING, at=agora)
        self._notify(sessao.id, "demonstração anexada")
        return self.get(sessao.id)

    def attach_run(self, teaching_id: str, run_id: str, *, note: str | None = None,
                   by: str | None = None) -> TeachingView:
        """Uma execução COMPROVADA como exemplo (fonte `successful_execution`). Só `completed`: exemplo de quem
        falhou ensinaria o erro."""
        sessao = self._accepting_demonstration(teaching_id)
        execucao = self._repo.run_status(run_id)
        if execucao is None:
            raise TeachingNotFound(f"Execução não encontrada: {run_id}.")
        if execucao[0] != "completed":
            raise TeachingInputInvalid(f"A execução {run_id} está '{execucao[0]}': só execução concluída com prova "
                                       "serve de exemplo.", code="run_not_completed")
        nota = self._person_text(note or "", "nota", allow_empty=True) or None
        agora = self._clock()
        with self._repo.atomic():
            self._repo.add_demonstration(Demonstration(
                id=self._new_id("dem"), teaching_id=sessao.id, seq=self._repo.next_demonstration_seq(sessao.id),
                kind=DemonstrationKind.RUN, training_session_id=None, run_id=run_id, instance_id=None,
                app_snapshot=None, note=nota, created_at=agora))
        self._notify(sessao.id, "execução anexada como exemplo")
        return self.get(sessao.id)

    def add_correction(self, teaching_id: str, body: str, *, run_id: str, step_id: str,
                       payload: JsonObject | None = None, by: str | None = None) -> TeachingView:
        """Correção de uma habilidade que errou (fonte `correction`): aponta a etapa `failed`/`uncertain`."""
        sessao = self._accepting_demonstration(teaching_id)
        if sessao.skill_id is None:
            raise TeachingInputInvalid("Correção é de uma habilidade existente: comece o ensino com skill_id.",
                                       code="correction_needs_skill")
        etapa = self._repo.step_status(step_id)
        if etapa is None or etapa[0] != run_id:
            raise TeachingNotFound(f"Etapa {step_id} não é da execução {run_id}.")
        if etapa[1] not in CORRECTABLE_STEP:
            raise TeachingInputInvalid(f"A etapa está '{etapa[1]}': só se corrige etapa que falhou ou ficou sem "
                                       "prova.", code="step_not_correctable")
        texto = self._person_text(body, "correção")
        dados = as_json_object(payload or {})
        self._screen_json(dados, "correção")
        self._repo.add_turn(NewTurn(sessao.id, TurnKind.CORRECTION, TurnAuthor.PERSON, body=texto,
                                    target={"run_id": run_id, "step_id": step_id}, payload=dados or None,
                                    created_by=by), at=self._clock())
        self._notify(sessao.id, "correção registrada")
        return self.get(sessao.id)

    def discard(self, teaching_id: str, *, by: str | None = None) -> TeachingView:
        sessao = self._session(teaching_id)
        check_move(sessao.status, S.DISCARDED)
        self._repo.move(sessao.id, sessao.status, S.DISCARDED, at=self._clock())
        self._notify(sessao.id, "ensino descartado")
        return self.get(sessao.id)

    # ================================================================== candidata
    async def propose(self, teaching_id: str, *, by: str | None = None,
                      idempotency_key: str | None = None) -> TeachingView:
        """Pede a candidata ao generalizador. Na IA real é UMA chamada paga do planejador; repetir é pedido
        explícito (e a mesma `idempotency_key` não repete)."""
        sessao = self._settle(self._session(teaching_id))
        turnos = self._repo.turns(sessao.id)
        if idempotency_key and self._replayed(turnos, "propose", idempotency_key):
            return self._view(sessao)
        if sessao.status is S.DEMONSTRATING:
            raise TeachingStateConflict("Conclua a gravação antes de pedir a candidata.", code="still_recording")
        if sessao.status is S.ASKING:
            atual = self._current(sessao.id)
            pendentes = open_questions(turnos, atual.id) if atual is not None else []
            if pendentes:
                raise TeachingStateConflict(f"Responda as {len(pendentes)} pergunta(s) antes de pedir outra candidata.",
                                            code="questions_pending")
        elif sessao.status is not S.OPEN:
            check_move(sessao.status, S.PROPOSING)
        pedido = self._request(sessao, turnos)
        self._repo.move(sessao.id, sessao.status, S.PROPOSING, at=self._clock())
        try:
            geracao = await self._generalizer.generalize(pedido)
            self._record_candidate(sessao, geracao, turnos, by=by, idempotency_key=idempotency_key)
        except BaseException:
            # A sessão não pode ficar presa em `proposing` (falha da IA, do banco, cancelamento): volta a aceitar
            # um pedido novo. Se a candidata já foi gravada, a sessão já saiu de `proposing` e o CAS não muda nada.
            try:
                self._repo.move(sessao.id, S.PROPOSING, S.OPEN, at=self._clock())
            except TeachingStateConflict:
                pass
            raise
        return self.get(sessao.id)

    def answer(self, teaching_id: str, question_id: int, body: str, *, by: str | None = None) -> TeachingView:
        sessao = self._session(teaching_id)
        if sessao.status is not S.ASKING:
            raise TeachingStateConflict(f"O ensino está '{sessao.status}': não há pergunta esperando resposta.")
        turnos = self._repo.turns(sessao.id)
        pergunta = next((t for t in turnos if t.id == question_id and t.kind is TurnKind.QUESTION), None)
        if pergunta is None:
            raise TeachingNotFound(f"Pergunta {question_id} não é deste ensino.")
        atual = self._current(sessao.id)
        if atual is None or pergunta.candidate_id != atual.id:
            raise TeachingStateConflict("A pergunta é de uma candidata antiga.")
        if all(p.id != pergunta.id for p in open_questions(turnos, atual.id)):
            raise TeachingStateConflict("Esta pergunta já foi respondida.")
        texto = self._person_text(body, "resposta")
        self._repo.add_turn(NewTurn(sessao.id, TurnKind.ANSWER, TurnAuthor.PERSON, body=texto, reply_to=pergunta.id,
                                    candidate_id=atual.id, created_by=by), at=self._clock())
        self._notify(sessao.id, "pergunta respondida")
        return self.get(sessao.id)

    def validate(self, candidate_id: str, *, mode: str = "static", by: str | None = None) -> TeachingView:
        """Validação ESTÁTICA: o compilador inteiro (schema, catálogo, apps, baixa para `Plan`). A validação em
        aparelho é outra peça (P4) e não existe nesta fase."""
        if mode != "static":
            raise TeachingInputInvalid("Só a validação estática existe nesta fase.", code="validation_mode")
        c = self._candidate(candidate_id)
        sessao = self._session(c.teaching_id)
        if c.status is CandidateStatus.PROPOSED and c.validation_status is TeachingValidation.PASSED \
                and sessao.status is S.READY:
            return self.get(sessao.id)                                   # repetir não muda nada
        if sessao.status is not S.VALIDATING or c.status is not CandidateStatus.PROPOSED:
            raise TeachingStateConflict(f"O ensino está '{sessao.status}' e a candidata '{c.status}': não há o que "
                                        "validar.")
        fatos = self._validator.inspect(c.envelope.document)
        agora = self._clock()
        with self._repo.atomic():
            if fatos.errors:
                self._repo.set_candidate(c.id, CandidateStatus.PROPOSED, CandidateStatus.REJECTED, at=agora,
                                         validation=TeachingValidation.FAILED)
                self._compiler_note(sessao.id, c.id, fatos.errors, agora)
                self._repo.move(sessao.id, S.VALIDATING, S.OPEN, at=agora, validation=TeachingValidation.FAILED)
            else:
                self._repo.set_candidate(c.id, CandidateStatus.PROPOSED, CandidateStatus.PROPOSED, at=agora,
                                         validation=TeachingValidation.PASSED)
                self._repo.move(sessao.id, S.VALIDATING, S.READY, at=agora, validation=TeachingValidation.PASSED)
        self._notify(sessao.id, "candidata validada" if not fatos.errors else "candidata reprovada na validação")
        return self.get(sessao.id)

    def publish(self, candidate_id: str, *, by: str) -> TeachingView:
        """A candidata validada vira `skill_versions` em DRAFT (`source_kind='teaching'`), numa transação com a
        candidata aceita e a sessão fechada. Não publica a habilidade: isso é o ciclo de vida da versão."""
        c = self._candidate(candidate_id)
        sessao = self._session(c.teaching_id)
        if c.status is CandidateStatus.ACCEPTED:
            return self.get(sessao.id)                                   # a mesma candidata dá a mesma versão
        if sessao.status is not S.READY or c.status is not CandidateStatus.PROPOSED \
                or c.validation_status is not TeachingValidation.PASSED:
            raise TeachingStateConflict("Só a candidata validada de um ensino pronto vira versão.")
        fatos = self._validator.inspect(c.envelope.document)
        skill_id = fatos.skill_id or ""
        if sessao.skill_id is not None and skill_id != sessao.skill_id:
            raise TeachingInputInvalid(f"A candidata declara {skill_id!r}, e o ensino é de {sessao.skill_id}.")
        if sessao.skill_id is None and self._skills.definition(skill_id) is not None:
            raise TeachingStateConflict(f"Já existe a habilidade {skill_id}: ensine como melhoria dela (skill_id) "
                                        "ou mude o id na candidata.")
        origem = Provenance(kind=SourceKind.TEACHING, ref=sessao.id, candidate_id=c.id, teaching_id=sessao.id,
                            generated_by=c.generated_by, reviewed_by=by)
        agora = self._clock()
        with self._repo.atomic():
            versao = self._skills.create_draft(skill_id, c.envelope.document, source=origem, by=by,
                                               parent_version=sessao.base_version)
            self._repo.set_candidate(c.id, CandidateStatus.PROPOSED, CandidateStatus.ACCEPTED, at=agora,
                                     version_id=str(versao.ref))
            self._repo.move(sessao.id, S.READY, S.PUBLISHED, at=agora, result_version_id=str(versao.ref))
        self._notify(sessao.id, f"candidata virou o rascunho {versao.ref}")
        return self.get(sessao.id)

    # ================================================================== auxiliares
    def _record_candidate(self, sessao: TeachingSession, geracao: Generalization, turnos: Sequence[TeachingTurn],
                          *, by: str | None, idempotency_key: str | None) -> None:
        envelope = geracao.envelope
        segredos = self._secret_paths(envelope.to_json())
        agora = self._clock()
        cid = self._new_id("cand")
        with self._repo.atomic():
            self._repo.supersede_candidates(sessao.id, at=agora)
            seq = self._repo.next_candidate_seq(sessao.id)
            if segredos:
                # Não se grava o valor: a candidata fica registrada (a pessoa vê que foi recusada e por quê), com
                # todo valor suspeito mascarado — o hash é do documento mascarado.
                mascarado = CandidateEnvelope.from_json(self._masked_object(envelope.to_json()))
                self._repo.add_candidate(self._new_candidate(cid, sessao.id, seq, mascarado, geracao.generated_by,
                                                             CandidateStatus.REJECTED, TeachingValidation.FAILED,
                                                             agora))
                self._repo.add_turn(NewTurn(sessao.id, TurnKind.NOTE, TurnAuthor.SYSTEM, candidate_id=cid,
                                            body=SecretInCandidate.code + ": a candidata tinha valor com formato de "
                                            "credencial e foi rejeitada — credencial nunca entra em habilidade (vai "
                                            "no campo Credenciais da execução, pelo nome).",
                                            payload={"paths": list(segredos[:20])}), at=agora)
                self._repo.move(sessao.id, S.PROPOSING, S.OPEN, at=agora, validation=TeachingValidation.FAILED)
            else:
                fatos = self._validator.inspect(envelope.document)
                respondidas = {a.key: a for a in answered_questions(turnos)}
                do_compilador, erros = compiler_questions(fatos.errors, envelope.document, respondidas)
                perguntas = (*geracao.questions, *do_compilador)
                status = CandidateStatus.REJECTED if erros else CandidateStatus.PROPOSED
                self._repo.add_candidate(self._new_candidate(
                    cid, sessao.id, seq, envelope, geracao.generated_by, status,
                    TeachingValidation.FAILED if erros else TeachingValidation.NONE, agora))
                if erros:
                    self._compiler_note(sessao.id, cid, tuple(erros), agora)
                    self._repo.move(sessao.id, S.PROPOSING, S.OPEN, at=agora)
                elif perguntas:
                    for p in perguntas:
                        self._repo.add_turn(NewTurn(sessao.id, TurnKind.QUESTION, p.origin, body=p.text,
                                                    target=p.target, candidate_id=cid,
                                                    payload={"key": p.key, "kind": p.kind.value}), at=agora)
                    self._repo.move(sessao.id, S.PROPOSING, S.ASKING, at=agora)
                else:
                    self._repo.move(sessao.id, S.PROPOSING, S.VALIDATING, at=agora)
            if idempotency_key:
                self._repo.add_turn(NewTurn(sessao.id, TurnKind.NOTE, TurnAuthor.SYSTEM, candidate_id=cid,
                                            body="candidata pedida", created_by=by,
                                            payload={"op": "propose", "idempotency_key": idempotency_key}), at=agora)
        self._notify(sessao.id, "candidata gerada")

    @staticmethod
    def _new_candidate(cid: str, teaching_id: str, seq: int, envelope: CandidateEnvelope, generated_by: str,
                       status: CandidateStatus, validation: TeachingValidation, at: str) -> SkillCandidate:
        return SkillCandidate(id=cid, teaching_id=teaching_id, seq=seq, envelope=envelope,
                              content_hash=envelope.document_hash, generated_by=generated_by, status=status,
                              validation_status=validation, version_id=None, created_at=at, updated_at=at)

    def _compiler_note(self, teaching_id: str, candidate_id: str, errors: Sequence[str], at: str) -> None:
        self._repo.add_turn(NewTurn(teaching_id, TurnKind.NOTE, TurnAuthor.COMPILER, candidate_id=candidate_id,
                                    body="A candidata não compila:\n" + "\n".join(errors[:20]),
                                    payload={"errors": list(errors[:50])}), at=at)

    def _request(self, sessao: TeachingSession, turnos: Sequence[TeachingTurn]) -> GeneralizationRequest:
        demos = self._repo.demonstrations(sessao.id)
        gravacoes: list[Recording] = []
        for d in demos:
            if d.kind is DemonstrationKind.RECORDING and d.training_session_id is not None:
                g = self._repo.recording(d.training_session_id)
                if g is not None:
                    gravacoes.append(g)
        instrucao = sessao.instruction or next((g.intent for g in gravacoes if g.intent.strip()), "")
        if sessao.instruction == "" and instrucao:
            # A intenção da gravação v1 não passou pela regra de credencial ao ser escrita; vai ao prompt agora.
            self._person_text(instrucao, "intenção da gravação")
        if not instrucao.strip() and not gravacoes:
            raise TeachingInputInvalid("Nada a generalizar: dê uma instrução ou anexe uma demonstração.",
                                       code="nothing_to_teach")
        app_id = self._app_of(sessao, gravacoes)
        if app_id is None:
            raise TeachingInputInvalid("Em qual aplicativo? Informe o app do ensino (app_id) antes de pedir a "
                                       "candidata.", code="app_required")
        exemplos: list[str] = []
        for d in demos:
            if d.kind is DemonstrationKind.RUN and d.run_id is not None:
                r = self._repo.run_status(d.run_id)
                if r is not None:
                    exemplos.append(r[1])
        return GeneralizationRequest(
            teaching_id=sessao.id, skill_id=sessao.skill_id or suggest_skill_id(app_id, instrucao), app_id=app_id,
            instruction=instrucao, inputs=tuple(e for g in gravacoes for e in g.inputs),
            answers=tuple(answered_questions(turnos)),
            corrections=tuple(t.body for t in turnos if t.kind is TurnKind.CORRECTION and t.body),
            example_commands=tuple(exemplos),
            base_version=str(SkillRef(sessao.skill_id, sessao.base_version))
            if sessao.skill_id is not None and sessao.base_version is not None else None)

    def _app_of(self, sessao: TeachingSession, gravacoes: Sequence[Recording]) -> str | None:
        if sessao.app_id:
            return sessao.app_id
        for g in gravacoes:
            if g.app_id and self._repo.app_exists(g.app_id):
                return g.app_id
        for g in gravacoes:
            for e in g.inputs:
                if e.type == "open_app" and e.app_id and self._repo.app_exists(e.app_id):
                    return e.app_id
                achado = self._repo.app_of_package(e.package) if e.package else None
                if achado:
                    return achado
        return None

    def _settle(self, sessao: TeachingSession) -> TeachingSession:
        """`demonstrating` → `open` quando nenhuma gravação ligada está mais gravando (o gravador não avisa o
        ensino: ele não muda, §13.3). CAS: se outra chamada já moveu, vale o que está gravado."""
        if sessao.status is not S.DEMONSTRATING:
            return sessao
        for d in self._repo.demonstrations(sessao.id):
            g = self._repo.recording(d.training_session_id) if d.training_session_id else None
            if g is not None and g.still_recording:
                return sessao
        try:
            self._repo.move(sessao.id, S.DEMONSTRATING, S.OPEN, at=self._clock())
        except TeachingStateConflict:
            pass
        return self._session(sessao.id)

    def _view(self, sessao: TeachingSession) -> TeachingView:
        demos = tuple(self._repo.demonstrations(sessao.id))
        turnos = tuple(self._repo.turns(sessao.id))
        candidatas = tuple(self._repo.candidates(sessao.id))
        atual = candidatas[-1] if candidatas else None
        erros: tuple[str, ...] = ()
        if atual is not None and atual.status is CandidateStatus.PROPOSED:
            erros = self._validator.inspect(atual.envelope.document).errors
        return TeachingView(session=sessao, source=source_of(sessao, demos, turnos), demonstrations=demos,
                            turns=turnos, candidates=candidatas, current=atual,
                            open_questions=tuple(open_questions(turnos, atual.id)) if atual is not None else (),
                            errors=erros)

    def _session(self, teaching_id: str) -> TeachingSession:
        sessao = self._repo.session(teaching_id)
        if sessao is None:
            raise TeachingNotFound(f"Ensino não encontrado: {teaching_id}.")
        return sessao

    def _candidate(self, candidate_id: str) -> SkillCandidate:
        c = self._repo.candidate(candidate_id)
        if c is None:
            raise TeachingNotFound(f"Candidata não encontrada: {candidate_id}.")
        return c

    def _current(self, teaching_id: str) -> SkillCandidate | None:
        candidatas = self._repo.candidates(teaching_id)
        return candidatas[-1] if candidatas else None

    def _accepting_demonstration(self, teaching_id: str) -> TeachingSession:
        sessao = self._settle(self._session(teaching_id))
        if sessao.status not in ACCEPTS_DEMONSTRATION:
            raise TeachingStateConflict(f"O ensino está '{sessao.status}': demonstração e correção entram antes da "
                                        "candidata.")
        return sessao

    def _person_text(self, text: str, what: str, *, allow_empty: bool = False) -> str:
        texto = text.strip()
        if not texto and not allow_empty:
            raise TeachingInputInvalid(f"A {what} está vazia.")
        if len(texto) > MAX_TEXT:
            raise TeachingInputInvalid(f"A {what} passa de {MAX_TEXT} caracteres.")
        if texto and self._secrets.text_has_credential(texto):
            raise CredentialInText(f"A {what} contém uma credencial (senha, token ou código). O texto do ensino vai ao "
                                   "provedor de IA e fica na conversa: tire o valor. Credencial entra só pelo campo "
                                   "Credenciais da execução, pelo nome — nunca na habilidade.")
        return texto

    def _screen_json(self, value: JsonObject, what: str) -> None:
        if self._secret_paths(value):
            raise CredentialInText(f"A {what} contém um valor com formato de credencial.")

    def _suspicious(self, text: str, where: str) -> bool:
        if self._secrets.value_is_secret(text):
            return True
        return _FOLHA_DE_DADO.search(where) is not None and self._secrets.text_has_credential(text)

    def _secret_paths(self, value: JsonValue, where: str = "$") -> list[str]:
        """Onde o documento (ou a anotação) tem valor com formato de credencial. Só o caminho sai daqui."""
        if isinstance(value, str):
            return [where] if self._suspicious(value, where) else []
        if isinstance(value, list):
            return [p for i, x in enumerate(value) for p in self._secret_paths(x, f"{where}[{i}]")]
        if isinstance(value, dict):
            return [p for k, x in value.items() for p in self._secret_paths(x, f"{where}.{k}")]
        return []

    def _masked(self, value: JsonValue, where: str) -> JsonValue:
        if isinstance(value, str):
            return MASK if self._suspicious(value, where) else value
        if isinstance(value, list):
            return [self._masked(x, f"{where}[{i}]") for i, x in enumerate(value)]
        if isinstance(value, dict):
            return {k: self._masked(x, f"{where}.{k}") for k, x in value.items()}
        return value

    def _masked_object(self, value: JsonObject) -> JsonObject:
        mascarado = self._masked(value, "$")
        return mascarado if isinstance(mascarado, dict) else {}

    @staticmethod
    def _replayed(turnos: Sequence[TeachingTurn], op: str, key: str) -> bool:
        return any(t.kind is TurnKind.NOTE and t.payload is not None and t.payload.get("op") == op
                   and t.payload.get("idempotency_key") == key for t in turnos)

    def _notify(self, teaching_id: str, message: str) -> None:
        if self._on_updated is not None:
            self._on_updated(teaching_id, message)

