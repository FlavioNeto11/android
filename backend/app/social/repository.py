"""Persistência do domínio social.

Regra de isolamento: **todo método que lê ou escreve dado de um perfil exige `profile_id`**. Não existe método que
devolva linha de perfil qualquer. O esquema reforça isso com chave estrangeira e unicidade; a API do repositório
reforça de novo, para um erro de consulta não virar vazamento entre perfis.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ..db import Database, dumps, loads
from ..models import CredentialInfo, InstagramProfileDTO, SessionInfo, SessionStatus
from ..util import new_token, now_iso


class SocialRepository:
    def __init__(self, db: Database):
        self.db = db

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

    def profile_row(self, profile_id: str) -> sqlite3.Row | None:
        return self.db.one("SELECT * FROM instagram_profiles WHERE id=?", (profile_id,))

    def profile_by_username(self, username: str) -> sqlite3.Row | None:
        return self.db.one("SELECT * FROM instagram_profiles WHERE username=? COLLATE NOCASE", (username,))

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
    def credential_row(self, profile_id: str) -> sqlite3.Row | None:
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
    def binding_row(self, profile_id: str) -> sqlite3.Row | None:
        return self.db.one("SELECT * FROM device_profile_bindings WHERE profile_id=? AND active=1", (profile_id,))

    def profile_id_for_instance(self, instance_id: str) -> str | None:
        return self.db.scalar("SELECT profile_id FROM device_profile_bindings WHERE instance_id=? AND active=1",
                              (instance_id,))

    def bind(self, profile_id: str, instance_id: str, *, reason: str | None = None) -> None:
        """Um perfil ativo por aparelho e um aparelho ativo por perfil — garantido por índice único parcial.
        O histórico fica: linhas inativas são a auditoria do rebinding."""
        with self.db.tx():
            self.unbind(profile_id, reason="rebinding")
            other = self.profile_id_for_instance(instance_id)
            if other and other != profile_id:
                self.unbind(other, reason=f"aparelho reatribuído para {profile_id}")
            self.db.execute(
                "INSERT INTO device_profile_bindings(profile_id, instance_id, active, bound_at, reason)"
                " VALUES (?,?,1,?,?)", (profile_id, instance_id, now_iso(), reason))

    def unbind(self, profile_id: str, *, reason: str | None = None) -> None:
        self.db.execute(
            "UPDATE device_profile_bindings SET active=0, unbound_at=?, reason=COALESCE(?, reason)"
            " WHERE profile_id=? AND active=1", (now_iso(), reason, profile_id))

    def binding_history(self, profile_id: str) -> list[sqlite3.Row]:
        return self.db.query("SELECT * FROM device_profile_bindings WHERE profile_id=? ORDER BY id DESC",
                             (profile_id,))

    # ------------------------------------------------------------------ sessão (cache do observado)
    def session_row(self, profile_id: str) -> sqlite3.Row | None:
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
        """Wipe, perda do aparelho ou atualização do app: a sessão daquele aparelho deixa de valer."""
        rows = self.db.query("SELECT profile_id FROM instagram_sessions WHERE instance_id=? AND status!=?",
                             (instance_id, SessionStatus.unknown.value))
        for r in rows:
            self.set_session(r["profile_id"], status=SessionStatus.unknown, instance_id=instance_id, detail=reason)
        return len(rows)

    # ------------------------------------------------------------------ auditoria de autenticação
    def start_auth_attempt(self, profile_id: str, instance_id: str, *, stage: str = "started") -> int:
        cur = self.db.execute(
            "INSERT INTO authentication_attempts(profile_id, instance_id, started_at, stage) VALUES (?,?,?,?)",
            (profile_id, instance_id, now_iso(), stage))
        return int(cur.lastrowid or 0)

    def finish_auth_attempt(self, profile_id: str, attempt_id: int, *, outcome: str, detail: str | None = None,
                            stage: str | None = None) -> None:
        self.db.execute(
            "UPDATE authentication_attempts SET finished_at=?, outcome=?, detail=?, stage=COALESCE(?, stage)"
            " WHERE id=? AND profile_id=?", (now_iso(), outcome, detail, stage, attempt_id, profile_id))

    def auth_attempts(self, profile_id: str, limit: int = 20) -> list[sqlite3.Row]:
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
                detail=session["detail"] if session else None),
            last_verified_at=row["last_verified_at"], last_activity_at=row["last_activity_at"],
            created_at=row["created_at"], updated_at=row["updated_at"])

    # ------------------------------------------------------------------ personas (só identidade nesta fase)
    def create_persona(self, *, name: str, summary: str | None = None) -> str:
        persona_id = f"persona-{new_token()}"
        now = now_iso()
        self.db.execute("INSERT INTO personas(id, name, summary, created_at, updated_at) VALUES (?,?,?,?,?)",
                        (persona_id, name, summary, now, now))
        return persona_id

    def list_personas(self) -> list[dict[str, Any]]:
        return [{"id": r["id"], "name": r["name"], "summary": r["summary"], "traits": loads(r["traits"], {})}
                for r in self.db.query("SELECT * FROM personas ORDER BY name")]

    def persona_exists(self, persona_id: str) -> bool:
        return self.db.one("SELECT id FROM personas WHERE id=?", (persona_id,)) is not None
