"""Persistência do domínio social.

Regra de isolamento: **todo método que lê ou escreve dado de um perfil exige `profile_id`**. Não existe método que
devolva linha de perfil qualquer. O esquema reforça isso com chave estrangeira e unicidade; a API do repositório
reforça de novo, para um erro de consulta não virar vazamento entre perfis.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Sequence

from ..db import Database, OPERATIONAL_ERRORS, Row, dumps, loads
from ..models import (CredentialInfo, InstagramProfileDTO, OFFLINE_POLICY_PADRAO, ProfileLocality, SessionInfo,
                      SessionStatus)
from ..util import new_token, now, now_iso, to_iso


def sessao_vencida(session: Row | None, max_age_s: int) -> bool:
    """`session_ready` verificada há tempo demais. Uma regra só: o que a porta do despacho recusa é exatamente o
    que o cartão do perfil marca como dado velho.

    Só `session_ready` envelhece — os demais estados já dizem por si que a sessão não vale, e marcá-los de
    "velhos" seria dizer duas vezes a mesma coisa. `session_ready` SEM data de verificação é o pior caso: uma
    afirmação sem observação registrada, e conta como vencida.

    Função de módulo, e não método: ela não lê nada de perfil nenhum — recebe a linha que quem chamou já buscou
    com o `profile_id` na mão, que é a regra de isolamento deste arquivo.
    """
    if session is None or max_age_s <= 0 or session["status"] != SessionStatus.session_ready.value:
        return False
    verificada = session["verified_at"]
    return not verificada or verificada < to_iso(now() - timedelta(seconds=max_age_s))


class SocialRepository:
    def __init__(self, db: Database):
        self.db = db
        # Validade do "Conectado", em segundos. Injetada pelo AppState a partir da configuração; 0 desliga. Fica
        # aqui porque é o repositório que monta o DTO do perfil, e é no cartão que a idade precisa aparecer.
        self.session_max_age_s: int = 0

    # ------------------------------------------------------------------ perfis
    def create_profile(self, *, username: str, first_name: str | None, last_name: str | None,
                       display_name: str | None, birth_date: str | None, email: str | None,
                       persona_id: str | None) -> str:
        profile_id = f"ig-{new_token()}"
        now = now_iso()
        self.db.execute(
            "INSERT INTO instagram_profiles(id, username, display_name, first_name, last_name, birth_date, email,"
            " persona_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (profile_id, username, display_name, first_name, last_name, birth_date, email, persona_id, now, now))
        return profile_id

    def profile_row(self, profile_id: str) -> Row | None:
        return self.db.one("SELECT * FROM instagram_profiles WHERE id=?", (profile_id,))

    def profile_by_username(self, username: str) -> Row | None:
        return self.db.one("SELECT * FROM instagram_profiles WHERE lower(username)=lower(?)", (username,))

    def update_profile(self, profile_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE instagram_profiles SET {sets}, updated_at=? WHERE id=?",
                        (*fields.values(), now_iso(), profile_id))

    def delete_profile(self, profile_id: str) -> None:
        """Em cascata: credencial, binding, sessão e tentativas somem junto (chave estrangeira do esquema)."""
        self.db.execute("DELETE FROM instagram_profiles WHERE id=?", (profile_id,))

    def list_profile_ids(self) -> list[str]:
        return [r["id"] for r in self.db.query("SELECT id FROM instagram_profiles ORDER BY username")]

    # ------------------------------------------------------------------ credencial (só metadados aqui)
    def credential_row(self, profile_id: str) -> Row | None:
        return self.db.one("SELECT * FROM instagram_credentials WHERE profile_id=?", (profile_id,))

    def set_credential(self, profile_id: str, *, login_identifier: str, secret_ref: str, key_id: str) -> None:
        now = now_iso()
        self.db.execute(
            "INSERT INTO instagram_credentials(profile_id, login_identifier, secret_ref, key_id, created_at,"
            " updated_at) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(profile_id) DO UPDATE SET login_identifier=excluded.login_identifier,"
            " secret_ref=excluded.secret_ref, key_id=excluded.key_id, updated_at=excluded.updated_at,"
            " status='active', failed_attempts=0, blocked_until=NULL",
            (profile_id, login_identifier, secret_ref, key_id, now, now))

    def delete_credential(self, profile_id: str) -> str | None:
        """Devolve a referência do segredo para quem chama apagar no cofre."""
        row = self.credential_row(profile_id)
        self.db.execute("DELETE FROM instagram_credentials WHERE profile_id=?", (profile_id,))
        return row["secret_ref"] if row else None

    def mark_credential(self, profile_id: str, *, status: str, failed_attempts: int | None = None,
                        blocked_until: str | None = None) -> None:
        fields: dict[str, Any] = {"status": status, "updated_at": now_iso()}
        if failed_attempts is not None:
            fields["failed_attempts"] = failed_attempts
        fields["blocked_until"] = blocked_until
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE instagram_credentials SET {sets} WHERE profile_id=?",
                        (*fields.values(), profile_id))

    def touch_credential(self, profile_id: str) -> None:
        self.db.execute("UPDATE instagram_credentials SET last_used_at=? WHERE profile_id=?", (now_iso(), profile_id))

    # ------------------------------------------------------------------ vínculo perfil <-> aparelho
    def binding_row(self, profile_id: str) -> Row | None:
        return self.db.one("SELECT * FROM device_profile_bindings WHERE profile_id=? AND active=1", (profile_id,))

    def profile_id_for_instance(self, instance_id: str) -> str | None:
        return self.db.scalar("SELECT profile_id FROM device_profile_bindings WHERE instance_id=? AND active=1",
                              (instance_id,))

    def localidade_da_instancia(self, instance_id: str) -> tuple[str | None, str | None]:
        """(worker onde o aparelho está agora, impressão digital observada dele). `instances`, não perfil.

        Não viola a regra de isolamento deste arquivo: `instances` não é dado de perfil nenhum — é o inventário
        do parque, e é justamente o que o vínculo precisa fotografar para saber ONDE os dados foram gravados.
        """
        row = self.db.one("SELECT worker_id, physical_id FROM instances WHERE id=?", (instance_id,))
        if row is None:
            return None, None
        return row["worker_id"], row["physical_id"]

    def bind(self, profile_id: str, instance_id: str, *, reason: str | None = None) -> None:
        """Um perfil ativo por aparelho e um aparelho ativo por perfil — garantido por índice único parcial.
        O histórico fica: linhas inativas são a auditoria do rebinding.

        O vínculo fotografa a LOCALIDADE (migração 023): a máquina e a impressão digital do aparelho no momento
        em que os dados passam a viver ali. Reapontar depois `instances.worker_id` ou `instances.external` muda
        o id lógico de lugar, e é a diferença entre o fotografado e o atual que denuncia a troca.
        """
        with self.db.tx():
            self.unbind(profile_id, reason="rebinding")
            other = self.profile_id_for_instance(instance_id)
            if other and other != profile_id:
                self.unbind(other, reason=f"aparelho reatribuído para {profile_id}")
            worker_id, physical_id = self.localidade_da_instancia(instance_id)
            self.db.execute(
                "INSERT INTO device_profile_bindings(profile_id, instance_id, active, bound_at, reason,"
                " worker_id, physical_id, locality_at) VALUES (?,?,1,?,?,?,?,?)",
                (profile_id, instance_id, now_iso(), reason, worker_id, physical_id, now_iso()))

    def registrar_localidade(self, profile_id: str, *, worker_id: str | None, physical_id: str | None) -> None:
        """Preenche a localidade do vínculo ativo com o que só se soube DEPOIS.

        A impressão digital costuma ser nula no instante do vínculo (o aparelho pode estar desligado) e só é lida
        quando ele entra no ar. `COALESCE` de propósito: o que ainda não se observou nunca apaga o que já se
        sabia — a mesma regra de `taskqueue/repository.py`. `worker_id` é escrito como veio, inclusive `NULL`
        (que quer dizer "este servidor"), porque `locality_at` já diz que a localidade foi registrada.
        """
        self.db.execute(
            "UPDATE device_profile_bindings SET worker_id=?, physical_id=COALESCE(?, physical_id), locality_at=?"
            " WHERE profile_id=? AND active=1", (worker_id, physical_id, now_iso(), profile_id))

    def unbind(self, profile_id: str, *, reason: str | None = None) -> None:
        self.db.execute(
            "UPDATE device_profile_bindings SET active=0, unbound_at=?, reason=COALESCE(?, reason)"
            " WHERE profile_id=? AND active=1", (now_iso(), reason, profile_id))

    #: Estados de worker em que a máquina ainda responde. `degraded` é "conectado com problema declarado" — o
    #: aparelho pode até não servir, mas os dados do perfil continuam alcançáveis, que é o que esta pergunta faz.
    _WORKER_ALCANCAVEL = ("online", "degraded")

    def localidade(self, profile_id: str, binding: Row | None = None) -> ProfileLocality | None:
        """Onde os dados deste perfil vivem, e se o id lógico continua apontando para lá.

        `None` quando não há vínculo: sem aparelho não há localidade a afirmar. Com vínculo anterior à migração
        023 (`locality_at` nulo) devolve `known=False` e não acusa mudança nenhuma — falta de registro não é
        prova de troca.
        """
        binding = binding if binding is not None else self.binding_row(profile_id)
        if binding is None:
            return None
        conhecida = binding["locality_at"] is not None
        worker_id = binding["worker_id"]
        if worker_id:
            w = self.db.one("SELECT name, state, maintenance FROM workers WHERE id=?", (worker_id,))
            nome = w["name"] if w else worker_id
            estado = ("maintenance" if w and w["maintenance"] else w["state"]) if w else "offline"
            observado = w["state"] if w else "offline"
        else:
            # `worker_id` nulo é este servidor — que, por estar respondendo esta chamada, está de pé.
            nome, estado, observado = "este servidor", "online", "online"
        disponivel = observado in self._WORKER_ALCANCAVEL
        atual_worker, atual_physical = self.localidade_da_instancia(binding["instance_id"])
        mudou_de_maquina = conhecida and atual_worker != worker_id
        mudou_de_aparelho = bool(conhecida and binding["physical_id"] and atual_physical
                                 and binding["physical_id"] != atual_physical)
        if mudou_de_maquina:
            detalhe = (f"os dados deste perfil vivem em {nome}, mas {binding['instance_id']} aponta hoje para "
                       f"{atual_worker or 'este servidor'}; a sessão de lá não existe aqui")
        elif mudou_de_aparelho:
            detalhe = (f"o aparelho físico por trás de {binding['instance_id']} mudou desde o vínculo; a sessão "
                       "gravada no disco anterior não está neste aparelho")
        elif not disponivel:
            detalhe = f"{nome} está {observado}: os dados deste perfil não estão alcançáveis agora"
        elif not conhecida:
            detalhe = "este vínculo é anterior ao registro de localidade; ainda não se sabe onde os dados vivem"
        else:
            detalhe = None
        return ProfileLocality(worker_id=worker_id, worker_name=nome, worker_state=estado, known=conhecida,
                               available=disponivel, moved=mudou_de_maquina or mudou_de_aparelho,
                               physical_id=binding["physical_id"], detail=detalhe)

    def binding_history(self, profile_id: str) -> list[Row]:
        return self.db.query("SELECT * FROM device_profile_bindings WHERE profile_id=? ORDER BY id DESC",
                             (profile_id,))

    # ------------------------------------------------------------------ sessão (cache do observado)
    def session_row(self, profile_id: str) -> Row | None:
        return self.db.one("SELECT * FROM instagram_sessions WHERE profile_id=?", (profile_id,))

    def set_session(self, profile_id: str, *, status: SessionStatus, instance_id: str | None = None,
                    observed_username: str | None = None, verified_at: str | None = None,
                    detail: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO instagram_sessions(profile_id, instance_id, status, observed_username, verified_at, detail,"
            " updated_at) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(profile_id) DO UPDATE SET instance_id=excluded.instance_id, status=excluded.status,"
            " observed_username=excluded.observed_username, verified_at=excluded.verified_at,"
            " detail=excluded.detail, updated_at=excluded.updated_at",
            (profile_id, instance_id, status.value, observed_username, verified_at, detail, now_iso()))

    def invalidate_sessions_of_instance(self, instance_id: str, *, reason: str) -> int:
        """Wipe, perda do aparelho ou atualização do app: a sessão daquele aparelho deixa de valer.

        O motivo é reescrito mesmo numa sessão que já estava `unknown`. Sem isso, uma sequência de operações
        deixaria no painel a explicação da PRIMEIRA delas — "o app foi atualizado" continuaria aparecendo depois de
        o app ter sido desinstalado e reinstalado, que é justamente quando a pessoa precisa saber o que houve.

        O retorno conta só quem de fato mudou de estado: é o que decide se vale emitir um aviso.
        """
        rows = self.db.query("SELECT profile_id, status FROM instagram_sessions WHERE instance_id=?", (instance_id,))
        mudaram = 0
        for r in rows:
            if r["status"] != SessionStatus.unknown.value:
                mudaram += 1
            self.set_session(r["profile_id"], status=SessionStatus.unknown, instance_id=instance_id, detail=reason)
        return mudaram

    # ------------------------------------------------------------------ auditoria de autenticação
    def start_auth_attempt(self, profile_id: str, instance_id: str, *, stage: str = "started") -> int:
        return int(self.db.inserted_id(
            "INSERT INTO authentication_attempts(profile_id, instance_id, started_at, stage) VALUES (?,?,?,?)",
            (profile_id, instance_id, now_iso(), stage)) or 0)

    def finish_auth_attempt(self, profile_id: str, attempt_id: int, *, outcome: str, detail: str | None = None,
                            stage: str | None = None) -> None:
        self.db.execute(
            "UPDATE authentication_attempts SET finished_at=?, outcome=?, detail=?, stage=COALESCE(?, stage)"
            " WHERE id=? AND profile_id=?", (now_iso(), outcome, detail, stage, attempt_id, profile_id))

    def auth_attempts(self, profile_id: str, limit: int = 20) -> list[Row]:
        return self.db.query("SELECT * FROM authentication_attempts WHERE profile_id=? ORDER BY id DESC LIMIT ?",
                             (profile_id, limit))

    # ------------------------------------------------------------------ DTO
    def profile_dto(self, profile_id: str) -> InstagramProfileDTO | None:
        row = self.profile_row(profile_id)
        if row is None:
            return None
        cred = self.credential_row(profile_id)
        binding = self.binding_row(profile_id)
        session = self.session_row(profile_id)
        persona_name = self.db.scalar("SELECT name FROM personas WHERE id=?", (row["persona_id"],)) \
            if row["persona_id"] else None
        return InstagramProfileDTO(
            id=row["id"], username=row["username"], display_name=row["display_name"], first_name=row["first_name"],
            last_name=row["last_name"], birth_date=row["birth_date"], email=row["email"],
            persona_id=row["persona_id"], persona_name=persona_name, status=row["status"],
            instance_id=binding["instance_id"] if binding else None,
            locality=self.localidade(profile_id, binding),
            offline_policy=row["offline_policy"] or OFFLINE_POLICY_PADRAO,
            credential=CredentialInfo(
                configured=cred is not None,
                login_identifier=cred["login_identifier"] if cred else None,
                status=cred["status"] if cred else None,
                failed_attempts=cred["failed_attempts"] if cred else 0,
                blocked_until=cred["blocked_until"] if cred else None,
                updated_at=cred["updated_at"] if cred else None,
                last_used_at=cred["last_used_at"] if cred else None),
            session=SessionInfo(
                status=SessionStatus(session["status"]) if session else SessionStatus.unknown,
                instance_id=session["instance_id"] if session else None,
                observed_username=session["observed_username"] if session else None,
                verified_at=session["verified_at"] if session else None,
                detail=session["detail"] if session else None,
                stale=sessao_vencida(session, self.session_max_age_s)),
            last_verified_at=row["last_verified_at"], last_activity_at=row["last_activity_at"],
            created_at=row["created_at"], updated_at=row["updated_at"])

    # ------------------------------------------------------------------ personas
    # Persona não é "por perfil" no argumento porque tem identidade própria; a exclusividade é do esquema
    # (índice único parcial em instagram_profiles.persona_id) e o dono se descobre com `persona_owner`.
    def create_persona(self, *, name: str, summary: str | None = None, persona_prompt: str = "",
                       traits: dict[str, Any] | None = None) -> str:
        persona_id = f"persona-{new_token()}"
        now = now_iso()
        self.db.execute(
            "INSERT INTO personas(id, name, summary, persona_prompt, traits, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (persona_id, name, summary, persona_prompt, dumps(traits or {}), now, now))
        return persona_id

    def persona_row(self, persona_id: str) -> Row | None:
        return self.db.one("SELECT * FROM personas WHERE id=?", (persona_id,))

    def update_persona(self, persona_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE personas SET {sets}, updated_at=? WHERE id=?",
                        (*fields.values(), now_iso(), persona_id))

    def delete_persona(self, persona_id: str) -> None:
        self.db.execute("DELETE FROM personas WHERE id=?", (persona_id,))

    def persona_owner(self, persona_id: str) -> str | None:
        """Qual perfil usa esta persona. Como só um pode usá-la, a resposta é única por construção."""
        return self.db.scalar("SELECT id FROM instagram_profiles WHERE persona_id=?", (persona_id,))

    def list_personas(self) -> list[dict[str, Any]]:
        return [{"id": r["id"], "name": r["name"], "summary": r["summary"],
                 "persona_prompt": r["persona_prompt"], "traits": loads(r["traits"], {}),
                 "profile_id": self.persona_owner(r["id"]), "created_at": r["created_at"],
                 "updated_at": r["updated_at"]}
                for r in self.db.query("SELECT * FROM personas ORDER BY name")]

    def persona_exists(self, persona_id: str) -> bool:
        return self.db.one("SELECT id FROM personas WHERE id=?", (persona_id,)) is not None

    def persona_of_profile(self, profile_id: str) -> Row | None:
        return self.db.one(
            "SELECT p.* FROM personas p JOIN instagram_profiles i ON i.persona_id=p.id WHERE i.id=?", (profile_id,))

    # ------------------------------------------------------------------ histórico social
    def record_interaction(self, profile_id: str, *, type: str, direction: str, status: str,
                           instance_id: str | None = None, run_id: str | None = None,
                           objective_id: str | None = None, step_id: str | None = None,
                           counterparty: str | None = None, thread_key: str | None = None,
                           incoming_content: str | None = None, outgoing_content: str | None = None,
                           target: str | None = None, context: dict[str, Any] | None = None,
                           evidence: str | None = None, metadata: dict[str, Any] | None = None,
                           occurred_at: str | None = None) -> str:
        interaction_id = f"int-{new_token()}"
        now = now_iso()
        self.db.execute(
            "INSERT INTO social_interactions(id, profile_id, instance_id, run_id, objective_id, step_id, occurred_at,"
            " type, direction, counterparty, thread_key, incoming_content, outgoing_content, target, context, status,"
            " evidence, metadata, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (interaction_id, profile_id, instance_id, run_id, objective_id, step_id, occurred_at or now, type,
             direction, counterparty, thread_key, incoming_content, outgoing_content, target, dumps(context or {}),
             status, evidence, dumps(metadata or {}), now, now))
        return interaction_id

    def interactions_by_step(self, profile_id: str, step_id: str, *, status: str | None = None) -> list[Row]:
        """Interações abertas por uma etapa. Serve à confirmação manual: quem confirma a etapa precisa fechar
        também o que ela deixou em aberto no histórico do perfil.

        Exige `profile_id` como todo método por perfil, e não porque a etapa pudesse pertencer a dois: exige
        porque a regra desta classe é que a ASSINATURA diga de quem é o dado, em vez de depender de uma invariante
        mantida em outro arquivo. Quem chama já tem o perfil fotografado em `objectives.profile_id`.
        """
        if status is None:
            return self.db.query("SELECT * FROM social_interactions WHERE profile_id=? AND step_id=? ORDER BY seq",
                                 (profile_id, step_id))
        return self.db.query(
            "SELECT * FROM social_interactions WHERE profile_id=? AND step_id=? AND status=? ORDER BY seq",
            (profile_id, step_id, status))

    def update_interaction(self, profile_id: str, interaction_id: str, **fields: Any) -> None:
        if not fields:
            return
        for chave in ("context", "metadata"):
            if chave in fields and not isinstance(fields[chave], str):
                fields[chave] = dumps(fields[chave] or {})
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE social_interactions SET {sets}, updated_at=? WHERE id=? AND profile_id=?",
                        (*fields.values(), now_iso(), interaction_id, profile_id))

    def interaction_row(self, profile_id: str, interaction_id: str) -> Row | None:
        return self.db.one("SELECT * FROM social_interactions WHERE id=? AND profile_id=?",
                           (interaction_id, profile_id))

    def list_interactions(self, profile_id: str, *, counterparty: str | None = None, thread_key: str | None = None,
                          status: str | None = None, limit: int = 20) -> list[Row]:
        onde = ["profile_id=?"]
        args: list[Any] = [profile_id]
        for coluna, valor in (("counterparty", counterparty), ("thread_key", thread_key), ("status", status)):
            if valor is not None:
                onde.append(f"{coluna}=?")
                args.append(valor)
        args.append(limit)
        return self.db.query(
            f"SELECT * FROM social_interactions WHERE {' AND '.join(onde)} ORDER BY seq DESC LIMIT ?", tuple(args))

    def count_interactions(self, profile_id: str, *, status: str | None = None) -> int:
        if status:
            return int(self.db.scalar("SELECT COUNT(*) FROM social_interactions WHERE profile_id=? AND status=?",
                                      (profile_id, status)) or 0)
        return int(self.db.scalar("SELECT COUNT(*) FROM social_interactions WHERE profile_id=?", (profile_id,)) or 0)

    # ------------------------------------------------------------------ contagem para os limites
    # Os limites contam o HISTÓRICO, não um contador separado: um contador à parte poderia divergir do que
    # realmente aconteceu na conta, e é justamente o que aconteceu na conta que importa.
    def count_interactions_since(self, profile_id: str, since: str, *, types: tuple[str, ...],
                                 statuses: tuple[str, ...]) -> int:
        if not types or not statuses:
            return 0
        t, s = ",".join("?" * len(types)), ",".join("?" * len(statuses))
        return int(self.db.scalar(
            f"SELECT COUNT(*) FROM social_interactions WHERE profile_id=? AND occurred_at >= ?"
            f" AND type IN ({t}) AND status IN ({s})", (profile_id, since, *types, *statuses)) or 0)

    def oldest_interaction_since(self, profile_id: str, since: str, *, types: tuple[str, ...],
                                 statuses: tuple[str, ...]) -> str | None:
        if not types or not statuses:
            return None
        t, s = ",".join("?" * len(types)), ",".join("?" * len(statuses))
        return self.db.scalar(
            f"SELECT MIN(occurred_at) FROM social_interactions WHERE profile_id=? AND occurred_at >= ?"
            f" AND type IN ({t}) AND status IN ({s})", (profile_id, since, *types, *statuses))

    def count_run_interactions(self, profile_id: str, run_id: str, *, statuses: tuple[str, ...]) -> int:
        s = ",".join("?" * len(statuses))
        return int(self.db.scalar(
            f"SELECT COUNT(*) FROM social_interactions WHERE profile_id=? AND run_id=? AND direction='outbound'"
            f" AND status IN ({s})", (profile_id, run_id, *statuses)) or 0)

    def recent_outgoing_texts(self, profile_id: str, limit: int) -> list[str]:
        """Os últimos textos que ESTE perfil escreveu — para não repetir a si mesmo.

        O filtro é SQL, não Python: com o limite aplicado antes, um perfil que recebeu algumas mensagens desde o
        último texto próprio devolvia lista vazia, e a lista de "não repita" perdia em silêncio justamente a
        parte que evita repetir o que ele publicou ontem.
        """
        return [r["outgoing_content"] for r in self.db.query(
            "SELECT outgoing_content FROM social_interactions WHERE profile_id=? AND direction='outbound'"
            " AND outgoing_content IS NOT NULL AND trim(outgoing_content)<>''"
            " ORDER BY seq DESC LIMIT ?", (profile_id, limit))]

    def last_external_interaction_at(self, profile_id: str, *, statuses: tuple[str, ...]) -> str | None:
        s = ",".join("?" * len(statuses))
        return self.db.scalar(
            f"SELECT MAX(occurred_at) FROM social_interactions WHERE profile_id=? AND direction='outbound'"
            f" AND status IN ({s})", (profile_id, *statuses))

    # ------------------------------------------------------------------ memória
    def insert_memory(self, profile_id: str, *, subject: str, content: str, source: str, fingerprint: str,
                      interaction_id: str | None = None, importance: float = 0.5, confidence: float = 0.5,
                      expires_at: str | None = None) -> str:
        memory_id = f"mem-{new_token()}"
        now = now_iso()
        self.db.execute(
            "INSERT INTO memory_items(id, profile_id, subject, content, source, interaction_id, importance,"
            " confidence, fingerprint, expires_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (memory_id, profile_id, subject, content, source, interaction_id, importance, confidence, fingerprint,
             expires_at, now, now))
        return memory_id

    def memory_row(self, profile_id: str, memory_id: str) -> Row | None:
        return self.db.one("SELECT * FROM memory_items WHERE id=? AND profile_id=?", (memory_id, profile_id))

    def memory_by_fingerprint(self, profile_id: str, fingerprint: str) -> Row | None:
        return self.db.one("SELECT * FROM memory_items WHERE profile_id=? AND fingerprint=?",
                           (profile_id, fingerprint))

    def merge_memory(self, profile_id: str, memory_id: str, *, importance: float, confidence: float,
                     interaction_id: str | None, expires_at: str | None) -> None:
        """O mesmo fato observado de novo não duplica: conta uma ocorrência e fica mais importante/confiável."""
        # `CASE` e nao `MAX(a, b)`: o MAX escalar de dois argumentos e do SQLite. No PostgreSQL, `MAX` e agregacao,
        # e a chamada falha com "function max(real, double precision) does not exist" — parametro chega como
        # `double precision` e a coluna e `real`. `GREATEST` resolveria no PostgreSQL e nao existe no SQLite; `CASE`
        # vale nos dois. Este escapou ao inventario do E6 porque o teste que o cobre abria SQLite direto.
        self.db.execute(
            "UPDATE memory_items SET occurrences=occurrences+1,"
            " importance=CASE WHEN ? > importance THEN ? ELSE importance END,"
            " confidence=CASE WHEN ? > confidence THEN ? ELSE confidence END,"
            " interaction_id=COALESCE(?, interaction_id),"
            " expires_at=COALESCE(?, expires_at), updated_at=? WHERE id=? AND profile_id=?",
            (importance, importance, confidence, confidence, interaction_id, expires_at, now_iso(), memory_id,
             profile_id))

    def list_memories(self, profile_id: str, *, subject: str | None = None, limit: int = 100,
                      include_expired: bool = False, now: str | None = None) -> list[Row]:
        onde = ["profile_id=?"]
        args: list[Any] = [profile_id]
        if subject is not None:
            onde.append("subject=?")
            args.append(subject)
        if not include_expired:
            onde.append("(expires_at IS NULL OR expires_at > ?)")
            args.append(now or now_iso())
        args.append(limit)
        return self.db.query(
            f"SELECT * FROM memory_items WHERE {' AND '.join(onde)} ORDER BY importance DESC, seq DESC LIMIT ?",
            tuple(args))

    def search_memories(self, profile_id: str, termos: Sequence[str], *, limit: int = 60,
                        include_expired: bool = False, now: str | None = None) -> list[Row]:
        """Busca por relevância. Recebe **termos**, não uma expressão pronta.

        Isso é deliberado e foi a correção de um defeito: quem chamava montava `"a" OR "b"`, que é sintaxe do FTS5.
        O `plainto_tsquery` do PostgreSQL trata aquilo como texto comum e junta tudo com E — inclusive a palavra
        literal "or", que não existe em conteúdo nenhum. Resultado: no PostgreSQL a busca NUNCA encontrava nada, e
        sem erro: quem chamou caía no caminho alternativo e recebia a lembrança errada. Sintaxe de índice é assunto
        de quem conhece o índice, e quem conhece é este método.

        O índice de texto é compartilhado; o filtro por perfil é o que separa os perfis — por isso ele fica aqui, na
        única consulta que toca o índice, e não na chamada de quem usa.
        """
        termos = [t for t in termos if t]
        if not termos:
            return []
        extra = "" if include_expired else " AND (m.expires_at IS NULL OR m.expires_at > ?)"
        # É o único ponto do projeto onde os dois bancos divergem de verdade na CONSULTA: FTS5 com `bm25()` no
        # SQLite, `tsvector` com `ts_rank` no PostgreSQL. Um `if` aqui é mais honesto que uma abstração que
        # fingisse que busca textual é igual nos dois.
        if self.db.dialect == "postgres":
            # `||` entre tsquery é OU. Um `plainto_tsquery` por termo, parametrizado: termo vindo da tela do app
            # nunca é concatenado em texto de consulta. `sem_acento` nos dois lados (migração 017).
            # Os parênteses em volta do `||` são obrigatórios, não estilo: `@@` e `||` têm a MESMA precedência e
            # associam à esquerda, então `busca @@ q1 || q2` seria lido como `(busca @@ q1) || q2` — booleano OU
            # tsquery. Sem eles a consulta não casava com nada.
            tq = "(" + " || ".join(["plainto_tsquery('simple', sem_acento(?))"] * len(termos)) + ")"
            sql = (f"SELECT m.*, ts_rank(m.busca, {tq}) AS rank FROM memory_items m"
                   f" WHERE m.busca @@ {tq} AND m.profile_id=?{extra} ORDER BY rank DESC LIMIT ?")
            args: list[Any] = [*termos, *termos, profile_id]      # o tsquery aparece duas vezes: rank e filtro
        else:
            # Cada termo entre aspas: `NEAR(`, `*`, `^` e aspas soltas não são interpretados como sintaxe. Texto
            # hostil simplesmente não encontra nada; nunca derruba a consulta.
            expressao = " OR ".join(f'"{t}"' for t in dict.fromkeys(termos))
            sql = ("SELECT m.*, bm25(memory_fts) AS rank FROM memory_fts JOIN memory_items m ON m.seq=memory_fts.rowid"
                   f" WHERE memory_fts MATCH ? AND m.profile_id=?{extra} ORDER BY rank LIMIT ?")
            args = [expressao, profile_id]
        if not include_expired:
            args.append(now or now_iso())
        args.append(limit)
        try:
            linhas = self.db.query(sql, tuple(args))
        except OPERATIONAL_ERRORS:
            # Tolerância SÓ no SQLite, e de propósito. Lá o texto da tela vira expressão do FTS5, e expressão
            # inválida é um caso previsto: busca sem resultado não é erro. No PostgreSQL o termo é PARÂMETRO de
            # `plainto_tsquery`, que aceita qualquer texto — então nada que venha da tela pode dar erro ali, e o que
            # der é defeito meu. `OPERATIONAL_ERRORS` inclui `ProgrammingError`, então engolir aqui esconderia erro
            # de sintaxe: foi exatamente o que aconteceu enquanto esta consulta estava sem um parêntese, e a busca
            # devolveu lista vazia em silêncio em vez de falhar.
            if self.db.dialect != "sqlite":
                raise
            return []
        return self._com_relevancia(linhas)

    def _com_relevancia(self, linhas: list[Row]) -> list[Row]:
        """Acrescenta `relevancia` em 0..1, onde 1 é o mais relevante — **nos dois bancos**.

        Existe porque as duas notas são opostas, e isso não é detalhe de formatação: `bm25()` é NEGATIVO e menor
        significa melhor; `ts_rank` é POSITIVO e maior significa melhor. Quem consome a busca normalizava dividindo
        pelo `min()`, o que é certo para bm25 e silenciosamente errado para `ts_rank` — pegava o PIOR como referência
        e o corte em 1.0 achatava tudo, apagando a ordenação por relevância sem erro nenhum.
        Normalizar aqui é o certo: é o único lugar do projeto que já sabe qual banco respondeu.
        """
        if not linhas:
            return linhas
        notas = [(r["rank"] or 0.0) for r in linhas]
        melhor = max(notas) if self.db.dialect == "postgres" else min(notas)
        for r in linhas:
            r["relevancia"] = 0.0 if not melhor else min(1.0, (r["rank"] or 0.0) / melhor)
        return linhas

    def delete_memory(self, profile_id: str, memory_id: str) -> bool:
        cur = self.db.execute("DELETE FROM memory_items WHERE id=? AND profile_id=?", (memory_id, profile_id))
        return bool(cur.rowcount)

    def touch_memories(self, profile_id: str, memory_ids: list[str]) -> None:
        if not memory_ids:
            return
        marcas = ",".join("?" for _ in memory_ids)
        self.db.execute(
            f"UPDATE memory_items SET last_used_at=? WHERE profile_id=? AND id IN ({marcas})",
            (now_iso(), profile_id, *memory_ids))

    def purge_expired_memories(self, profile_id: str, *, now: str | None = None) -> int:
        cur = self.db.execute("DELETE FROM memory_items WHERE profile_id=? AND expires_at IS NOT NULL"
                              " AND expires_at <= ?", (profile_id, now or now_iso()))
        return int(cur.rowcount or 0)

    # ------------------------------------------------------------------ relacionamento e conversa
    def relationship_row(self, profile_id: str, counterparty: str) -> Row | None:
        return self.db.one("SELECT * FROM relationship_summaries WHERE profile_id=? AND counterparty=?",
                           (profile_id, counterparty))

    def upsert_relationship(self, profile_id: str, counterparty: str, *, summary: str | None = None,
                            tone: str | None = None, bump: bool = False, last_interaction_at: str | None = None
                            ) -> None:
        now = now_iso()
        self.db.execute(
            "INSERT INTO relationship_summaries(profile_id, counterparty, summary, tone, interactions,"
            " first_interaction_at, last_interaction_at, updated_at) VALUES (?,?,?,?,?,?,?,?)"
            # A coluna da linha EXISTENTE precisa do nome da tabela: sozinha, `summary` é ambígua entre a linha
            # nova e a antiga, e o PostgreSQL recusa (o SQLite adivinhava).
            " ON CONFLICT(profile_id, counterparty) DO UPDATE SET"
            " summary=COALESCE(excluded.summary, relationship_summaries.summary),"
            " tone=COALESCE(excluded.tone, relationship_summaries.tone),"
            " interactions=relationship_summaries.interactions+excluded.interactions,"
            " last_interaction_at=COALESCE(excluded.last_interaction_at,"
            " relationship_summaries.last_interaction_at),"
            " updated_at=excluded.updated_at",
            (profile_id, counterparty, summary or "", tone, 1 if bump else 0,
             last_interaction_at or now if bump else None, last_interaction_at or (now if bump else None), now))

    def list_relationships(self, profile_id: str, *, limit: int = 50) -> list[Row]:
        return self.db.query(
            "SELECT * FROM relationship_summaries WHERE profile_id=? ORDER BY last_interaction_at DESC LIMIT ?",
            (profile_id, limit))

    def thread_row(self, profile_id: str, thread_key: str) -> Row | None:
        return self.db.one("SELECT * FROM thread_summaries WHERE profile_id=? AND thread_key=?",
                           (profile_id, thread_key))

    def upsert_thread(self, profile_id: str, thread_key: str, *, summary: str | None = None,
                      counterparty: str | None = None, bump: bool = False,
                      last_message_at: str | None = None) -> None:
        now = now_iso()
        self.db.execute(
            "INSERT INTO thread_summaries(profile_id, thread_key, counterparty, summary, messages, last_message_at,"
            " updated_at) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(profile_id, thread_key) DO UPDATE SET"
            " counterparty=COALESCE(excluded.counterparty, thread_summaries.counterparty),"
            " summary=COALESCE(excluded.summary, thread_summaries.summary),"
            " messages=thread_summaries.messages+excluded.messages,"
            " last_message_at=COALESCE(excluded.last_message_at, thread_summaries.last_message_at),"
            " updated_at=excluded.updated_at",
            (profile_id, thread_key, counterparty, summary or "", 1 if bump else 0,
             last_message_at or (now if bump else None), now))
