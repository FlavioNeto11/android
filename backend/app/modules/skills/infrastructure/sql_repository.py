"""`SqlSkillRepository`: habilidades versionadas nas tabelas 042–043 (§10, ADR-034) e a adoção de fluxo (ADR-037).

É a camada OBRIGATÓRIA da imutabilidade: só `draft` muda de conteúdo (`SkillVersion.revise`), o `UPDATE` de conteúdo
leva `AND state='draft'`, e o `content_hash` é conferido a cada leitura que vai executar ou mover a versão. O gatilho
da 046 é a segunda camada, e nada aqui depende dele.

Cada operação que muda estado é UMA transação, com CAS no estado (`WHERE id=? AND state=?`): duas sessões do painel
publicando ao mesmo tempo não publicam as duas. Publicar deprecia a publicada anterior ANTES de marcar a nova — os
índices parciais (`ux_skill_versions_publicada`, `ux_skill_versions_comando`) conferem a cada instrução, e a anterior
quase sempre tem o mesmo comando.

`flows` só é tocado em dois lugares, e só no `status`: a adoção desliga o fluxo, e desfazer a adoção o religa — na
mesma transação que cria ou desabilita a versão. Nunca há escrita dupla (o mesmo comando vivo nos dois lugares).
"""
from __future__ import annotations

import builtins
from collections.abc import Callable, Sequence

from app.db import INTEGRITY_ERRORS, Database, Row
from app.modules.skills.application.ports import DocumentValidator
from app.modules.skills.domain.document import JsonObject, NotJson, as_json_object, canonical_json
from app.modules.skills.domain.lifecycle import (SYSTEM_ACTOR, Actor, DuplicateCommand, FrozenVersion,
                                                 InvalidDocument, SkillError, SkillNotFound, SkillState,
                                                 StateConflict, TransitionForbidden, ValidationPending, actor_of,
                                                 check_transition)
from app.modules.skills.domain.intent import SkillMatch
from app.modules.skills.domain.matching import (bind_template_parameters, extract_parameters, extract_with_gaps,
                                                specificity)
from app.modules.skills.domain.refs import InvalidSkillRef, SkillRef, is_skill_id
from app.modules.skills.domain.validation import (CaseKind, CaseStatus, Outcome, Proof, ValidationCase,
                                                  ValidationResult, validation_verdict)
from app.modules.skills.domain.versions import (SCHEMA_LEGACY_PLAN, DocumentFacts, Provenance, ResolvedSkill,
                                                SkillDefinition, SkillScope, SkillSummary, SkillVersion, SourceKind,
                                                TransitionRecord, legacy_plan_parameters)
from app.modules.skills.infrastructure import rows
from app.modules.skills.infrastructure.legacy_flows import legacy_content, legacy_provenance, required_apps
from app.security.redaction import chave_sensivel, looks_secret, redact


class SecretInParameters(SkillError):
    """Parâmetro de caso de validação com cara de credencial: credencial entra pelo NOME, nunca pelo valor."""

    code = "secret_in_parameters"


class SkillsDisabled(SkillError):
    """Adotar um fluxo com `skills.enabled` desligado desligaria o fluxo e deixaria o comando sem resolução nenhuma:
    a versão adotada só casa com as habilidades ligadas (guarda apontada pela fase D, G2)."""

    code = "skills_disabled"


class SqlSkillRepository:
    def __init__(self, db: Database, validator: DocumentValidator, *,
                 clock: Callable[[], str] | None = None,
                 adoption_enabled: Callable[[], bool] | None = None) -> None:
        """`adoption_enabled`: o `skills.enabled` da instalação, lido a cada adoção. `None` = sem a guarda (testes do
        repositório isolado); a composição do `AppState` sempre a passa."""
        self._db = db
        self._validator = validator
        self._clock = clock if clock is not None else db.agora_iso
        self._adoption_enabled = adoption_enabled

    # ================================================================== leitura
    def definition(self, skill_id: str) -> SkillDefinition | None:
        row = self._db.one("SELECT * FROM skill_definitions WHERE id=?", (skill_id,))
        return rows.definicao(row) if row else None

    def get(self, ref: SkillRef) -> SkillVersion:
        """A versão pelo nome, com a integridade conferida (hash que não bate é recusa, não aviso)."""
        versao = self._load(ref)
        versao.verify_integrity()
        return versao

    def published(self, skill_id: str) -> SkillVersion | None:
        row = self._db.one("SELECT * FROM skill_versions WHERE skill_id=? AND state='published'", (skill_id,))
        if row is None:
            return None
        versao = self._from_row(row)
        versao.verify_integrity()
        return versao

    def list(self, *, app_id: str | None = None, state: SkillState | None = None) -> list[SkillSummary]:
        sql = ("SELECT v.*, d.name AS d_name, d.app_id AS d_app_id, d.legacy_flow_id AS d_legacy_flow_id"
               " FROM skill_versions v JOIN skill_definitions d ON d.id = v.skill_id WHERE 1=1")
        params: list[str] = []
        if state is not None:
            sql += " AND v.state=?"
            params.append(state.value)
        if app_id is not None:
            sql += (" AND (d.app_id=? OR EXISTS (SELECT 1 FROM skill_version_apps a"
                    " WHERE a.version_id = v.id AND a.app_id=?))")
            params += [app_id, app_id]
        return [rows.resumo(r) for r in self._db.query(sql + " ORDER BY v.skill_id, v.version", tuple(params))]

    def history(self, ref: SkillRef) -> builtins.list[TransitionRecord]:
        return [rows.transicao(r) for r in self._db.query(
            "SELECT * FROM skill_version_transitions WHERE version_id=? ORDER BY id", (str(ref),))]

    def scope(self, skill_id: str) -> SkillScope:
        linhas = self._db.query("SELECT profile_id, group_id FROM skill_scope WHERE skill_id=?", (skill_id,))
        perfis = [p for r in linhas if (p := rows.texto_ou_nulo(r, "profile_id"))]
        grupos = [g for r in linhas if (g := rows.texto_ou_nulo(r, "group_id"))]
        return SkillScope(tuple(sorted(set(perfis))), tuple(sorted(set(grupos))))

    def resolve(self, command: str, profile_ids: Sequence[str | None] | None) -> ResolvedSkill | None:
        """A versão PUBLICADA cujo comando-modelo casa com `command`, respeitando o escopo da habilidade.

        Mesma extração e mesmo escopo do fluxo legado. Entre várias que casam, a mais específica
        (`matching.specificity`), e depois o id — a mesma resposta sempre. Uma publicada adulterada que case é
        recusa (`ContentTampered`), não "pula para a próxima": executar outra coisa em silêncio seria pior.
        """
        primeira = next(iter(self.candidates(command, profile_ids)), None)
        return primeira.skill if primeira is not None and primeira.complete else None

    def candidates(self, command: str, profile_ids: Sequence[str | None] | None) -> tuple[SkillMatch, ...]:
        """TODAS as publicadas que casam com a força da melhor (fase I): o empate é do `IntentResolver`, que pergunta
        em vez de escolher pelo id. Em ordem (especificidade, depois id), então a primeira é a de `resolve`.

        Sem nenhuma que case inteira, as de conteúdo da DSL que casam com um `{nome}` VAZIO (`extract_with_gaps`),
        também só as da força mais alta: servem para perguntar o que falta. Conteúdo legado (`schema_version` 0) não
        entra aí — não tem tipo para perguntar, e o fluxo que ele adotou nunca casou assim.

        Adulteração: confere-se o hash de cada versão DEVOLVIDA. Uma adulterada entre as empatadas é recusa, como a
        primeira que casa sempre foi; a que perderia de qualquer jeito nem é lida.
        """
        linhas = self._db.query("SELECT * FROM skill_versions WHERE state='published' AND command_template IS NOT NULL")
        ordem = sorted(linhas, key=lambda r: (*_negativo(specificity(rows.texto(r, "command_template"))),
                                              rows.texto(r, "skill_id")))
        inteiras: list[SkillMatch] = []
        com_buraco: list[tuple[Row, dict[str, str], tuple[str, ...]]] = []
        for row in ordem:
            modelo = rows.texto(row, "command_template")
            forca = specificity(modelo)
            if inteiras and forca != inteiras[0].strength:
                break                                 # a ordem é por força: daqui para baixo, nenhuma empata
            skill_id = rows.texto(row, "skill_id")
            if profile_ids is not None and not self._in_scope(skill_id, profile_ids):
                continue
            valores = extract_parameters(modelo, command)
            if valores is not None:
                versao = self._from_row(row)
                versao.verify_integrity()
                if versao.schema_version == SCHEMA_LEGACY_PLAN and bind_template_parameters(
                        legacy_plan_parameters(versao.document()), valores) is None:
                    continue                          # faltou valor para algum parâmetro do plano: não é esta
                inteiras.append(SkillMatch(self._resolved(skill_id, versao, valores)))
                continue
            lacunas = None if inteiras else extract_with_gaps(modelo, command)
            if lacunas is not None and (not com_buraco or forca == specificity(
                    rows.texto(com_buraco[0][0], "command_template"))):
                com_buraco.append((row, *lacunas))
        if inteiras:
            return tuple(inteiras)
        parciais: list[SkillMatch] = []
        for row, dados, vazios in com_buraco:
            versao = self._from_row(row)
            versao.verify_integrity()
            if versao.schema_version != SCHEMA_LEGACY_PLAN:
                parciais.append(SkillMatch(self._resolved(rows.texto(row, "skill_id"), versao, dados), vazios))
        return tuple(parciais)

    def _resolved(self, skill_id: str, versao: SkillVersion, valores: dict[str, str]) -> ResolvedSkill:
        definicao = self.definition(skill_id)
        assert definicao is not None                  # FK: versão sem definição não existe
        return ResolvedSkill(definition=definicao, version=versao, parameters=valores)

    # ================================================================== rascunho
    def create_draft(self, skill_id: str, doc: JsonObject, *, source: Provenance, by: str | None = None,
                     parent_version: int | None = None) -> SkillVersion:
        """Nova versão em `draft` (a definição nasce junto na primeira). Numeração: a maior + 1, por habilidade."""
        if not is_skill_id(skill_id):
            raise InvalidSkillRef(f"id de habilidade inválido: {skill_id!r} (o prefixo 'flow:' é do legado)")
        fatos = self._facts(skill_id, doc)
        agora = self._clock()
        try:
            with self._db.tx():
                if self.definition(skill_id) is None:
                    self._db.execute(
                        "INSERT INTO skill_definitions(id, name, description, app_id, created_by, created_at,"
                        " updated_at) VALUES (?,?,?,?,?,?,?)",
                        (skill_id, fatos.name, fatos.description, fatos.app_id, by, agora, agora))
                if parent_version is not None:
                    self._load(SkillRef(skill_id, parent_version))
                versao = SkillVersion.new_draft(SkillRef(skill_id, self._next_version(skill_id)), doc, fatos,
                                                provenance=source, parent_version=parent_version, by=by, now=agora)
                self._insert(versao)
                self._record(versao.ref, None, SkillState.DRAFT, "rascunho criado", by, agora)
        except INTEGRITY_ERRORS as exc:
            raise StateConflict(f"{skill_id}: outra versão foi criada ao mesmo tempo; tente de novo.") from exc
        return versao

    def update_draft(self, ref: SkillRef, doc: JsonObject) -> SkillVersion:
        """Troca o conteúdo de um rascunho. Fora de `draft`: `FrozenVersion` (a edição vira versão nova)."""
        fatos = self._facts(ref.skill_id, doc)
        with self._db.tx():
            nova = self._load(ref).revise(doc, fatos)
            cur = self._db.execute(
                "UPDATE skill_versions SET content=?, content_hash=?, command_template=?, match_key=?"
                " WHERE id=? AND state='draft'",
                (nova.content_json, nova.content_hash, nova.command_template, nova.match_key, str(ref)))
            if int(cur.rowcount or 0) != 1:
                raise StateConflict(f"{ref} deixou de ser rascunho durante a edição.")
            self._set_apps(ref, nova.app_ids)
        return nova

    def discard_draft(self, ref: SkillRef) -> None:
        """Só rascunho se apaga (§10.3). Referenciado por um ensino, não se apaga: recusa em vez de quebrar a FK."""
        try:
            with self._db.tx():
                if not self._load(ref).editable:
                    raise FrozenVersion(f"{ref} já saiu de rascunho: desabilite em vez de apagar.")
                self._db.execute("DELETE FROM skill_version_apps WHERE version_id=?", (str(ref),))
                self._db.execute("DELETE FROM skill_version_transitions WHERE version_id=?", (str(ref),))
                cur = self._db.execute("DELETE FROM skill_versions WHERE id=? AND state='draft'", (str(ref),))
                if int(cur.rowcount or 0) != 1:
                    raise StateConflict(f"{ref} deixou de ser rascunho.")
        except INTEGRITY_ERRORS as exc:
            raise StateConflict(f"{ref} é referenciado (ensino ou validação) e não se apaga.") from exc

    # ================================================================== transições
    def transition(self, ref: SkillRef, to: SkillState, *, by: str, reason: str, manual: bool = False) -> SkillVersion:
        """Move a versão pela tabela de `lifecycle`, com as condições da §10.3 conferidas NA MESMA transação."""
        if manual and to is not SkillState.VALIDATED:
            raise TransitionForbidden("Só a validação admite decisão manual.")
        agora = self._clock()
        try:
            with self._db.tx():
                versao = self._load(ref)
                versao.verify_integrity()
                check_transition(versao.state, to, by)
                detalhe = reason.strip() or None
                if to is SkillState.CANDIDATE:
                    self._require_compiles(versao)
                elif to is SkillState.VALIDATED:
                    detalhe = self._validation_detail(versao, by=by, reason=reason, manual=manual)
                elif to is SkillState.PUBLISHED:
                    self._publish_side_effects(versao, by=by, at=agora)
                return self._move(versao, to, by=by, detail=detalhe, at=agora)
        except INTEGRITY_ERRORS as exc:
            raise StateConflict(f"{ref}: o banco recusou a transição (outra publicação chegou antes?).") from exc

    def _require_compiles(self, versao: SkillVersion) -> None:
        fatos = self._validator.inspect(versao.document())
        if fatos.errors:
            raise InvalidDocument(f"{versao.ref} não compila: corrija o rascunho antes de submeter.", fatos.errors)

    def _validation_detail(self, versao: SkillVersion, *, by: str, reason: str, manual: bool) -> str:
        veredito = validation_verdict(versao.ref.version, self.cases(versao.ref.skill_id), self.results(versao.ref))
        if veredito.ready:
            return reason.strip() or "casos de validação aprovados"
        if not manual:
            raise ValidationPending(f"{versao.ref} ainda não tem a prova que `validated` exige.", veredito.pending)
        if actor_of(by) is not Actor.PERSON or not reason.strip():
            raise TransitionForbidden("A validação manual é decisão de uma pessoa, com motivo escrito.")
        return f"validação manual: {reason.strip()} (pendências: {'; '.join(veredito.pending)})"

    def _publish_side_effects(self, versao: SkillVersion, *, by: str, at: str) -> None:
        """Comando livre, publicada anterior depreciada e, se for a conversão, o fluxo desligado — nesta ordem."""
        fluxo_a_desligar: str | None = None
        if versao.match_key is not None:
            outra = self._db.one("SELECT id FROM skill_versions WHERE state='published' AND match_key=? AND skill_id<>?",
                                 (versao.match_key, versao.ref.skill_id))
            if outra is not None:
                raise DuplicateCommand(f"O comando de {versao.ref} já está publicado em {rows.texto(outra, 'id')}.")
            fluxo = self._db.one("SELECT id FROM flows WHERE status='active' AND match_key=?", (versao.match_key,))
            if fluxo is not None:
                definicao = self.definition(versao.ref.skill_id)
                if definicao is None or definicao.legacy_flow_id != rows.texto(fluxo, "id"):
                    raise DuplicateCommand(f"O comando de {versao.ref} já é do fluxo ativo "
                                           f"'{rows.texto(fluxo, 'id')}': adote o fluxo ou desative-o antes.")
                fluxo_a_desligar = rows.texto(fluxo, "id")
        anterior = self._db.one("SELECT * FROM skill_versions WHERE skill_id=? AND state='published' AND id<>?",
                                (versao.ref.skill_id, str(versao.ref)))
        if anterior is not None:
            self._move(self._from_row(anterior), SkillState.DEPRECATED, by=SYSTEM_ACTOR,
                       detail=f"substituída por {versao.ref} (publicada por {by})", at=at)
        if fluxo_a_desligar is not None:
            self._set_flow_status(fluxo_a_desligar, frm="active", to="disabled")

    def _move(self, versao: SkillVersion, to: SkillState, *, by: str, detail: str | None, at: str) -> SkillVersion:
        nova = versao.moved_to(to, by=by, detail=detail, at=at)
        cur = self._db.execute(
            "UPDATE skill_versions SET state=?, state_at=?, state_by=?, state_detail=? WHERE id=? AND state=?",
            (to.value, at, by, detail, str(versao.ref), versao.state.value))
        if int(cur.rowcount or 0) != 1:
            raise StateConflict(f"{versao.ref} mudou de estado durante a transição; releia e tente de novo.")
        self._record(versao.ref, versao.state, to, detail, by, at)
        return nova

    # ================================================================== fluxo legado (adopt-on-write)
    def adopt_flow(self, flow_id: str, *, skill_id: str, by: str, reason: str = "") -> SkillVersion:
        """Converte um fluxo ATIVO em habilidade: definição com `legacy_flow_id`, versão publicada com o plano do
        fluxo (`schema_version` 0, `source_kind='legacy_flow'`), escopo copiado e o fluxo desligado. Uma transação.

        A conversão completa (fase J) é `convert_flow`: esta adoção e, na MESMA transação, o rascunho com o documento
        descompilado (`create_draft` com `parent_version`).
        """
        if not is_skill_id(skill_id):
            raise InvalidSkillRef(f"id de habilidade inválido: {skill_id!r}")
        if not by.strip():
            raise TransitionForbidden("A adoção precisa dizer quem decidiu.")
        if self._adoption_enabled is not None and not self._adoption_enabled():
            raise SkillsDisabled(f"As habilidades estão desligadas (skills.enabled): adotar o fluxo {flow_id} o "
                                 "desligaria e o comando ficaria sem resolução. Ligue as habilidades antes.")
        agora = self._clock()
        try:
            with self._db.tx():
                fluxo = self._db.one("SELECT * FROM flows WHERE id=?", (flow_id,))
                if fluxo is None:
                    raise SkillNotFound(f"Fluxo não encontrado: {flow_id}.")
                if rows.texto(fluxo, "status") != "active":
                    raise StateConflict(f"O fluxo {flow_id} está desligado: religue-o antes de adotá-lo.")
                self._definition_for_adoption(flow_id, skill_id, fluxo, by=by, at=agora)
                apps = required_apps(self._db, flow_id)
                versao = SkillVersion.frozen(
                    SkillRef(skill_id, self._next_version(skill_id)), legacy_content(fluxo, apps),
                    state=SkillState.PUBLISHED, schema_version=SCHEMA_LEGACY_PLAN,
                    command_template=rows.texto(fluxo, "command_template"), app_ids=apps,
                    provenance=legacy_provenance(fluxo), at=agora, by=by,
                    detail=reason.strip() or f"adoção do fluxo {flow_id}")
                if versao.match_key is not None and self._db.one(
                        "SELECT id FROM skill_versions WHERE state='published' AND match_key=?", (versao.match_key,)):
                    raise DuplicateCommand(f"O comando do fluxo {flow_id} já está publicado em outra habilidade.")
                self._insert(versao)
                self._record(versao.ref, None, SkillState.PUBLISHED, versao.state_detail, by, agora)
                self._db.execute("DELETE FROM skill_scope WHERE skill_id=?", (skill_id,))
                for r in self._db.query("SELECT profile_id, group_id FROM flow_scope WHERE flow_id=?", (flow_id,)):
                    self._db.execute("INSERT INTO skill_scope(skill_id, profile_id, group_id) VALUES (?,?,?)",
                                     (skill_id, rows.texto_ou_nulo(r, "profile_id"),
                                      rows.texto_ou_nulo(r, "group_id")))
                self._set_flow_status(flow_id, frm="active", to="disabled")
        except INTEGRITY_ERRORS as exc:
            raise StateConflict(f"O banco recusou a adoção do fluxo {flow_id}.") from exc
        return versao

    def release_flow(self, skill_id: str, *, by: str, reason: str) -> None:
        """Desfaz a adoção: desabilita a versão publicada e religa o fluxo, na mesma transação."""
        agora = self._clock()
        with self._db.tx():
            definicao = self.definition(skill_id)
            if definicao is None or definicao.legacy_flow_id is None:
                raise SkillNotFound(f"{skill_id} não é a adoção de um fluxo.")
            publicada = self._db.one("SELECT * FROM skill_versions WHERE skill_id=? AND state='published'",
                                     (skill_id,))
            if publicada is not None:
                self._move(self._from_row(publicada), SkillState.DISABLED, by=by,
                           detail=reason.strip() or f"adoção desfeita; o fluxo {definicao.legacy_flow_id} volta",
                           at=agora)
            fluxo = self._db.one("SELECT match_key FROM flows WHERE id=?", (definicao.legacy_flow_id,))
            if fluxo is None:
                raise SkillNotFound(f"O fluxo {definicao.legacy_flow_id} foi apagado: não há o que religar.")
            if self._db.one("SELECT id FROM skill_versions WHERE state='published' AND match_key=?",
                            (rows.texto(fluxo, "match_key"),)):
                raise DuplicateCommand(f"Outra habilidade publicada já usa o comando do fluxo "
                                       f"{definicao.legacy_flow_id}.")
            self._set_flow_status(definicao.legacy_flow_id, frm="disabled", to="active")

    def convert_flow(self, flow_id: str, *, skill_id: str, by: str, draft_of: Callable[[SkillVersion], JsonObject],
                     reason: str = "") -> tuple[SkillVersion, SkillVersion]:
        """Conversão fluxo → habilidade (fase J, §15.2), numa transação só: a adoção (v1 publicada com o plano do
        fluxo, o fluxo desligado) e a v2 em RASCUNHO com o documento que `draft_of` dá para a v1 — o descompilador.

        `draft_of` recebe a v1 JÁ gravada, dentro da transação: o documento sai do plano exato que foi adotado, e não
        de uma leitura anterior que outra sessão pudesse ter mudado. Se ele recusar (`InvalidDocument` com os erros da
        ida e volta) ou o rascunho falhar, nada fica: nem a v1, nem o fluxo desligado.
        """
        with self._db.tx():
            v1 = self.adopt_flow(flow_id, skill_id=skill_id, by=by, reason=reason or f"conversão do fluxo {flow_id}")
            documento = draft_of(v1)
            v2 = self.create_draft(skill_id, documento, by=by, parent_version=v1.ref.version,
                                   source=Provenance(kind=SourceKind.LEGACY_FLOW, ref=flow_id,
                                                     notes=(("decompiled_from", str(v1.ref)),)))
        return v1, v2

    def undo_conversion(self, flow_id: str, *, by: str, reason: str = "") -> tuple[str, tuple[SkillRef, ...]]:
        """Desfaz a conversão, numa transação: `release_flow` (a versão publicada desabilitada, o fluxo religado com
        o plano, o escopo e os apps que ele tinha — `flows` nunca foi reescrito) e os rascunhos que a conversão criou
        e que ainda são rascunho, apagados. Devolve a habilidade e os rascunhos apagados.

        Rascunho que já saiu de `draft` fica: é trabalho da pessoa, e publicá-lo depois é converter de novo (a
        publicação desliga o fluxo adotado na mesma transação). A definição também fica: a v1 desabilitada continua
        apontando o fluxo, e readotar usa a mesma habilidade.
        """
        skill_id = self.adopter_id(flow_id)
        if skill_id is None:
            raise SkillNotFound(f"O fluxo {flow_id} não foi convertido em habilidade.")
        apagados: list[SkillRef] = []
        with self._db.tx():
            self.release_flow(skill_id, by=by, reason=reason or f"conversão desfeita; o fluxo {flow_id} volta")
            for r in self._db.query("SELECT id FROM skill_versions WHERE skill_id=? AND state='draft' AND source_kind=?"
                                    " AND source_ref=? ORDER BY version",
                                    (skill_id, SourceKind.LEGACY_FLOW.value, flow_id)):
                versao = rows.texto(r, "id")
                # Referenciado pelo ensino, não se apaga (a FK recusaria, e no PostgreSQL a recusa aborta a transação
                # inteira): confere antes, em vez de tentar e capturar.
                if self._db.one("SELECT 1 AS x FROM teaching_sessions WHERE result_version_id=? UNION ALL"
                                " SELECT 1 AS x FROM teaching_candidates WHERE version_id=?", (versao, versao)):
                    continue
                ref = SkillRef.parse(versao)
                self.discard_draft(ref)
                apagados.append(ref)
        return skill_id, tuple(apagados)

    def flow_app_id(self, flow_id: str) -> str | None:
        """O app do fluxo, para sugerir o id da habilidade da conversão (`<app>.<fluxo>`)."""
        row = self._db.one("SELECT app_id FROM flows WHERE id=?", (flow_id,))
        return rows.texto_ou_nulo(row, "app_id") if row is not None else None

    def published_with_command(self, match_key: str) -> SkillVersion | None:
        """A versão publicada, de QUALQUER habilidade, com este comando. `PUT /api/flows/{id}` pergunta aqui antes de
        religar um fluxo: o mesmo comando não fica vivo nos dois backends, adotado ou não (critério da fase J)."""
        row = self._db.one("SELECT * FROM skill_versions WHERE state='published' AND match_key=?", (match_key,))
        return self._from_row(row) if row is not None else None

    def published_adopter(self, flow_id: str) -> SkillVersion | None:
        """A versão PUBLICADA da habilidade que adotou este fluxo, ou `None`. Enquanto ela existir, religar o fluxo
        (`PUT /api/flows/{id}`) deixaria o mesmo comando vivo nos dois lugares; quem volta ao fluxo é
        `release_flow`, que desabilita a versão na mesma transação."""
        row = self._db.one("SELECT v.* FROM skill_versions v JOIN skill_definitions d ON d.id = v.skill_id"
                           " WHERE d.legacy_flow_id=? AND v.state='published'", (flow_id,))
        return self._from_row(row) if row is not None else None

    def adopter_id(self, flow_id: str) -> str | None:
        """A habilidade que adotou este fluxo, em QUALQUER estado. Apagar o fluxo dela tira o caminho de volta
        (`release_flow` religa o fluxo pelo `legacy_flow_id`), por isso `DELETE /api/flows/{id}` consulta aqui."""
        dona = self._db.one("SELECT id FROM skill_definitions WHERE legacy_flow_id=?", (flow_id,))
        return rows.texto(dona, "id") if dona is not None else None

    def _definition_for_adoption(self, flow_id: str, skill_id: str, fluxo: Row, *, by: str, at: str) -> None:
        dona = self._db.one("SELECT * FROM skill_definitions WHERE legacy_flow_id=?", (flow_id,))
        if dona is not None:
            if rows.texto(dona, "id") != skill_id:
                raise StateConflict(f"O fluxo {flow_id} já foi adotado por {rows.texto(dona, 'id')}.")
            return                                        # readoção depois de desfazer: a mesma definição
        if self.definition(skill_id) is not None:
            raise StateConflict(f"{skill_id} já existe e não é a adoção do fluxo {flow_id}.")
        self._db.execute(
            "INSERT INTO skill_definitions(id, name, description, app_id, legacy_flow_id, created_by, created_at,"
            " updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (skill_id, rows.texto(fluxo, "name"), "", rows.texto_ou_nulo(fluxo, "app_id"), flow_id, by, at, at))

    def _set_flow_status(self, flow_id: str, *, frm: str, to: str) -> None:
        cur = self._db.execute("UPDATE flows SET status=? WHERE id=? AND status=?", (to, flow_id, frm))
        if int(cur.rowcount or 0) != 1:
            raise StateConflict(f"O fluxo {flow_id} não estava '{frm}'.")

    # ================================================================== escopo
    def set_scope(self, skill_id: str, *, profile_ids: Sequence[str], group_ids: Sequence[str]) -> None:
        """Escopo é distribuição, não conteúdo (§10.1): muda sem versão nova, e vale para todas as versões."""
        with self._db.tx():
            if self.definition(skill_id) is None:
                raise SkillNotFound(f"Habilidade não encontrada: {skill_id}.")
            self._db.execute("DELETE FROM skill_scope WHERE skill_id=?", (skill_id,))
            for pid in dict.fromkeys(profile_ids):
                self._db.execute("INSERT INTO skill_scope(skill_id, profile_id) VALUES (?,?)", (skill_id, pid))
            for gid in dict.fromkeys(group_ids):
                self._db.execute("INSERT INTO skill_scope(skill_id, group_id) VALUES (?,?)", (skill_id, gid))

    def _in_scope(self, skill_id: str, profile_ids: Sequence[str | None]) -> bool:
        """A regra de `FlowStore._no_escopo`: sem escopo vale para todos; com escopo, TODOS os perfis dentro."""
        escopo = self.scope(skill_id)
        if escopo.everyone:
            return True
        permitidos: set[str] = set(escopo.profile_ids)
        for gid in escopo.group_ids:
            permitidos |= {rows.texto(r, "id") for r in self._db.query(
                "SELECT id FROM instagram_profiles WHERE policy_group_id=?", (gid,))}
        return bool(profile_ids) and all(p is not None and p in permitidos for p in profile_ids)

    # ================================================================== casos de validação
    def add_case(self, skill_id: str, case_id: str, *, name: str, kind: CaseKind, expected: JsonObject,
                 parameters: JsonObject | None = None, preconditions: JsonObject | None = None,
                 since_version: int | None = None, until_version: int | None = None,
                 source_kind: str | None = None, source_ref: str | None = None,
                 by: str | None = None) -> ValidationCase:
        parametros = as_json_object(parameters or {})
        _refuse_secrets(parametros)
        agora = self._clock()
        with self._db.tx():
            if self.definition(skill_id) is None:
                raise SkillNotFound(f"Habilidade não encontrada: {skill_id}.")
            self._db.execute(
                "INSERT INTO skill_validation_cases(id, skill_id, name, kind, since_version, until_version, parameters,"
                " preconditions, expected, source_kind, source_ref, status, created_by, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (case_id, skill_id, name, kind.value, since_version, until_version, canonical_json(parametros),
                 canonical_json(as_json_object(preconditions or {})), canonical_json(as_json_object(expected)),
                 source_kind, source_ref, CaseStatus.ACTIVE.value, by, agora, agora))
        return rows.caso(self._one("SELECT * FROM skill_validation_cases WHERE id=?", case_id))

    def retire_case(self, case_id: str, *, until_version: int | None = None) -> None:
        """Aposenta o caso. Com `until_version`, ele continua valendo para as versões até ela (a faixa fecha)."""
        agora = self._clock()
        if until_version is None:
            cur = self._db.execute("UPDATE skill_validation_cases SET status=?, updated_at=? WHERE id=?",
                                   (CaseStatus.RETIRED.value, agora, case_id))
        else:
            cur = self._db.execute("UPDATE skill_validation_cases SET until_version=?, updated_at=? WHERE id=?",
                                   (until_version, agora, case_id))
        if int(cur.rowcount or 0) != 1:
            raise SkillNotFound(f"Caso de validação não encontrado: {case_id}.")

    def cases(self, skill_id: str) -> builtins.list[ValidationCase]:
        return [rows.caso(r) for r in self._db.query(
            "SELECT * FROM skill_validation_cases WHERE skill_id=? ORDER BY id", (skill_id,))]

    def record_result(self, ref: SkillRef, case_id: str, *, proof: Proof, outcome: Outcome,
                      run_id: str | None = None, instance_id: str | None = None, physical_id: str | None = None,
                      app_version: str | None = None, variant: str | None = None, detail: str | None = None,
                      by: str | None = None) -> ValidationResult:
        """Grava uma OBSERVAÇÃO. Rascunho não recebe prova: o conteúdo ainda muda, e a prova seria de outro."""
        agora = self._clock()
        with self._db.tx():
            versao = self._load(ref)
            if versao.editable:
                raise FrozenVersion(f"{ref} ainda é rascunho: submeta antes de validar.")
            caso = self._db.one("SELECT skill_id FROM skill_validation_cases WHERE id=?", (case_id,))
            if caso is None or rows.texto(caso, "skill_id") != ref.skill_id:
                raise SkillNotFound(f"Caso {case_id} não é de {ref.skill_id}.")
            novo = self._db.inserted_id(
                "INSERT INTO skill_validation_results(case_id, version_id, proof, outcome, run_id, instance_id,"
                " physical_id, app_version, variant, detail, observed_at, observed_by)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (case_id, str(ref), proof.value, outcome.value, run_id, instance_id, physical_id, app_version,
                 variant, redact(detail), agora, by))
        return rows.resultado(self._one("SELECT * FROM skill_validation_results WHERE id=?", novo))

    def results(self, ref: SkillRef) -> builtins.list[ValidationResult]:
        return [rows.resultado(r) for r in self._db.query(
            "SELECT * FROM skill_validation_results WHERE version_id=? ORDER BY id", (str(ref),))]

    # ================================================================== auxiliares
    def _facts(self, skill_id: str, doc: JsonObject) -> DocumentFacts:
        try:
            as_json_object(doc)
        except NotJson as exc:
            raise InvalidDocument(f"O documento de {skill_id} não é JSON: {exc}") from exc
        fatos = self._validator.inspect(doc)
        if fatos.skill_id != skill_id:
            raise InvalidDocument(f"O documento declara metadata.id={fatos.skill_id!r}, e a versão é de {skill_id}.")
        if not (fatos.name or "").strip():
            raise InvalidDocument(f"O documento de {skill_id} não tem nome (metadata.name).")
        return fatos

    def _load(self, ref: SkillRef) -> SkillVersion:
        row = self._db.one("SELECT * FROM skill_versions WHERE id=?", (str(ref),))
        if row is None:
            raise SkillNotFound(f"Versão não encontrada: {ref}.")
        return self._from_row(row)

    def _from_row(self, row: Row) -> SkillVersion:
        apps = tuple(rows.texto(r, "app_id") for r in self._db.query(
            "SELECT app_id FROM skill_version_apps WHERE version_id=? ORDER BY app_id", (rows.texto(row, "id"),)))
        return rows.versao(row, apps)

    def _one(self, sql: str, chave: object) -> Row:
        row = self._db.one(sql, (chave,))
        if row is None:
            raise SkillNotFound(f"Linha sumiu durante a gravação: {chave}.")
        return row

    def _next_version(self, skill_id: str) -> int:
        atual = self._db.one("SELECT MAX(version) AS n FROM skill_versions WHERE skill_id=?", (skill_id,))
        return (rows.inteiro_ou_nulo(atual, "n") or 0) + 1 if atual else 1

    def _insert(self, versao: SkillVersion) -> None:
        p = versao.provenance
        self._db.execute(
            "INSERT INTO skill_versions(id, skill_id, version, state, schema_version, content, content_hash,"
            " command_template, match_key, parent_version, source_kind, source_ref, provenance, created_by,"
            " created_at, state_at, state_by, state_detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(versao.ref), versao.ref.skill_id, versao.ref.version, versao.state.value, versao.schema_version,
             versao.content_json, versao.content_hash, versao.command_template, versao.match_key,
             versao.parent_version, p.kind.value, p.ref, canonical_json(p.to_json()), versao.created_by,
             versao.created_at, versao.state_at, versao.state_by, versao.state_detail))
        self._set_apps(versao.ref, versao.app_ids)

    def _set_apps(self, ref: SkillRef, app_ids: Sequence[str]) -> None:
        self._db.execute("DELETE FROM skill_version_apps WHERE version_id=?", (str(ref),))
        for app_id in sorted(set(app_ids)):
            self._db.execute("INSERT INTO skill_version_apps(version_id, app_id) VALUES (?,?)", (str(ref), app_id))

    def _record(self, ref: SkillRef, frm: SkillState | None, to: SkillState, reason: str | None, by: str | None,
                at: str) -> None:
        self._db.execute(
            "INSERT INTO skill_version_transitions(version_id, from_state, to_state, reason, decided_by, decided_at)"
            " VALUES (?,?,?,?,?,?)", (str(ref), frm.value if frm else None, to.value, reason, by, at))


def _negativo(par: tuple[int, int]) -> tuple[int, int]:
    return (-par[0], -par[1])


def _refuse_secrets(parametros: JsonObject, onde: str = "parameters") -> None:
    """Credencial é citada pelo NOME (`requires.secrets`, `run_secrets`); o VALOR nunca entra num caso gravado."""
    for chave, valor in parametros.items():
        if chave_sensivel(chave):
            raise SecretInParameters(f"{onde}.{chave}: parâmetro com nome de credencial. Cite o segredo pelo nome.")
        for item in valor if isinstance(valor, list) else [valor]:
            if isinstance(item, dict):
                _refuse_secrets(item, f"{onde}.{chave}")
            elif isinstance(item, str) and (redact(item) != item or looks_secret(item)):
                raise SecretInParameters(f"{onde}.{chave}: o valor tem formato de credencial e não pode ser gravado.")
