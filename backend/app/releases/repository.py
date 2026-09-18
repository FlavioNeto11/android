"""Persistência do domínio de release. O banco é registro do que foi importado e cache do que foi observado."""
from __future__ import annotations

import sqlite3
from typing import Any

from ..db import Database, dumps, loads
from ..models import (DeviceAppStateDTO, InstalledAppState, ReleaseChannel, ReleaseDTO, ReleaseFileDTO, ReleaseState,
                      ReleaseValidationDTO)
from ..util import now_iso


class ReleaseRepository:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ releases
    def save_release(self, *, release_id: str, package_name: str, version_name: str, version_code: int,
                     artifact_type: str, signature_sha256: str, min_sdk: int | None, target_sdk: int | None,
                     abis: list[str], catalog_dir: str, source_type: str, source_reference: str | None,
                     status: ReleaseState, detail: str | None, files: list[dict[str, Any]]) -> None:
        """Grava release e arquivos numa transação. Reimportar o mesmo conjunto é idempotente: o id vem do conteúdo."""
        with self.db.tx():
            self.db.execute(
                "INSERT INTO app_releases(id, package_name, version_name, version_code, artifact_type,"
                " signature_sha256, min_sdk, target_sdk, supported_abis, catalog_dir, source_type, source_reference,"
                " imported_at, status, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET status=excluded.status, detail=excluded.detail",
                (release_id, package_name, version_name, version_code, artifact_type, signature_sha256, min_sdk,
                 target_sdk, dumps(abis), catalog_dir, source_type, source_reference, now_iso(), status.value, detail))
            for f in files:
                self.db.execute(
                    "INSERT INTO app_release_files(release_id, role, split_name, file_name, sha256, size_bytes)"
                    " VALUES (?,?,?,?,?,?) ON CONFLICT(release_id, file_name) DO UPDATE SET sha256=excluded.sha256,"
                    " size_bytes=excluded.size_bytes",
                    (release_id, f["role"], f.get("split"), f["name"], f["sha256"], f["sizeBytes"]))

    def release_row(self, release_id: str) -> sqlite3.Row | None:
        return self.db.one("SELECT * FROM app_releases WHERE id=?", (release_id,))

    def files_of(self, release_id: str) -> list[sqlite3.Row]:
        return self.db.query("SELECT * FROM app_release_files WHERE release_id=? ORDER BY role DESC, file_name",
                             (release_id,))

    def list_releases(self, package_name: str | None = None) -> list[ReleaseDTO]:
        sql = "SELECT * FROM app_releases"
        params: tuple[Any, ...] = ()
        if package_name:
            sql += " WHERE package_name=?"
            params = (package_name,)
        sql += " ORDER BY package_name, version_code DESC"
        return [self.release_dto(r) for r in self.db.query(sql, params)]

    def set_status(self, release_id: str, status: ReleaseState, detail: str | None = None) -> None:
        self.db.execute("UPDATE app_releases SET status=?, detail=? WHERE id=?", (status.value, detail, release_id))

    # ------------------------------------------------------------------ canal, provas e promoção
    def set_channel(self, release_id: str, channel: ReleaseChannel, *, detail: str | None = None,
                    canary_instance_id: str | None = None) -> None:
        """`canary_instance_id` só é gravado quando informado: promover não apaga em qual aparelho a prova aconteceu."""
        campos: dict[str, Any] = {"channel": channel.value, "channel_at": now_iso(), "channel_detail": detail}
        if canary_instance_id is not None:
            campos["canary_instance_id"] = canary_instance_id
        sets = ", ".join(f"{k}=?" for k in campos)
        self.db.execute(f"UPDATE app_releases SET {sets} WHERE id=?", (*campos.values(), release_id))

    def record_validation(self, release_id: str, instance_id: str, *, stage: str, ok: bool,
                          detail: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO app_release_validations(release_id, instance_id, stage, ok, detail, observed_at)"
            " VALUES (?,?,?,?,?,?)", (release_id, instance_id, stage, 1 if ok else 0, detail, now_iso()))

    def validations_of(self, release_id: str, instance_id: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM app_release_validations WHERE release_id=?"
        params: tuple[Any, ...] = (release_id,)
        if instance_id:
            sql += " AND instance_id=?"
            params += (instance_id,)
        return self.db.query(sql + " ORDER BY id", params)

    def last_validation(self, release_id: str, instance_id: str, stage: str) -> sqlite3.Row | None:
        """A ÚLTIMA prova, não "alguma prova": uma versão que abriu ontem e quebrou hoje não pode promover."""
        return self.db.one(
            "SELECT * FROM app_release_validations WHERE release_id=? AND instance_id=? AND stage=?"
            " ORDER BY id DESC LIMIT 1", (release_id, instance_id, stage))

    def releases_of_channel(self, package_name: str, channel: ReleaseChannel) -> list[sqlite3.Row]:
        return self.db.query("SELECT * FROM app_releases WHERE package_name=? AND channel=? ORDER BY version_code DESC",
                             (package_name, channel.value))

    def release_dto(self, row: sqlite3.Row) -> ReleaseDTO:
        devices = [r["instance_id"] for r in self.db.query(
            "SELECT instance_id FROM device_app_state WHERE installed_release_id=? ORDER BY instance_id", (row["id"],))]
        return ReleaseDTO(
            id=row["id"], package_name=row["package_name"], version_name=row["version_name"],
            version_code=row["version_code"], artifact_type=row["artifact_type"],
            signature_sha256=row["signature_sha256"], min_sdk=row["min_sdk"], target_sdk=row["target_sdk"],
            supported_abis=loads(row["supported_abis"], []) or [], source_type=row["source_type"],
            source_reference=row["source_reference"], imported_at=row["imported_at"],
            status=ReleaseState(row["status"]), detail=row["detail"],
            channel=ReleaseChannel(row["channel"]), channel_at=row["channel_at"],
            channel_detail=row["channel_detail"], canary_instance_id=row["canary_instance_id"],
            validations=[ReleaseValidationDTO(instance_id=v["instance_id"], stage=v["stage"], ok=bool(v["ok"]),
                                              detail=v["detail"], observed_at=v["observed_at"])
                         for v in self.validations_of(row["id"])],
            files=[ReleaseFileDTO(role=f["role"], split_name=f["split_name"], file_name=f["file_name"],
                                  sha256=f["sha256"], size_bytes=f["size_bytes"]) for f in self.files_of(row["id"])],
            devices=devices)

    # ------------------------------------------------------------------ assinatura aprovada pelo operador
    def trusted_signer(self, package_name: str) -> str | None:
        return self.db.scalar("SELECT signature_sha256 FROM app_trusted_signers WHERE package_name=?", (package_name,))

    def approve_signer(self, package_name: str, signature_sha256: str, note: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO app_trusted_signers(package_name, signature_sha256, approved_at, note) VALUES (?,?,?,?)"
            " ON CONFLICT(package_name) DO UPDATE SET signature_sha256=excluded.signature_sha256,"
            " approved_at=excluded.approved_at, note=excluded.note",
            (package_name, signature_sha256, now_iso(), note))

    # ------------------------------------------------------------------ estado do app no aparelho
    def app_state(self, instance_id: str, package_name: str) -> sqlite3.Row | None:
        return self.db.one("SELECT * FROM device_app_state WHERE instance_id=? AND package_name=?",
                           (instance_id, package_name))

    def upsert_app_state(self, instance_id: str, package_name: str, **fields: Any) -> None:
        """Cria a linha se não existir e aplica só os campos informados."""
        for campo in ("observed_splits", "expected_splits"):
            if campo in fields:
                fields[campo] = dumps(fields[campo] or [])
        if isinstance(fields.get("state"), InstalledAppState):
            fields["state"] = fields["state"].value
        with self.db.tx():
            self.db.execute(
                "INSERT INTO device_app_state(instance_id, package_name) VALUES (?,?)"
                " ON CONFLICT(instance_id, package_name) DO NOTHING", (instance_id, package_name))
            if fields:
                sets = ", ".join(f"{k}=?" for k in fields)
                self.db.execute(f"UPDATE device_app_state SET {sets} WHERE instance_id=? AND package_name=?",
                                (*fields.values(), instance_id, package_name))

    def list_app_state(self, package_name: str | None = None) -> list[DeviceAppStateDTO]:
        sql = "SELECT * FROM device_app_state"
        params: tuple[Any, ...] = ()
        if package_name:
            sql += " WHERE package_name=?"
            params = (package_name,)
        return [self.app_state_dto(r) for r in self.db.query(sql + " ORDER BY instance_id", params)]

    def pending_operations(self) -> list[sqlite3.Row]:
        """Operações que estavam em curso quando o backend caiu. Nunca são repetidas às cegas: só reconciliadas."""
        return self.db.query("SELECT * FROM device_app_state WHERE pending_op IS NOT NULL")

    @staticmethod
    def app_state_dto(r: sqlite3.Row) -> DeviceAppStateDTO:
        return DeviceAppStateDTO(
            instance_id=r["instance_id"], package_name=r["package_name"],
            desired_release_id=r["desired_release_id"], installed_release_id=r["installed_release_id"],
            observed_version_name=r["observed_version_name"], observed_version_code=r["observed_version_code"],
            observed_splits=loads(r["observed_splits"], []) or [],
            expected_splits=loads(r["expected_splits"], []) or [], first_install_time=r["first_install_time"],
            last_update_time=r["last_update_time"], state=InstalledAppState(r["state"]), pending_op=r["pending_op"],
            verified_at=r["verified_at"], drift_kind=r["drift_kind"], detail=r["detail"],
            previous_release_id=r["previous_release_id"], last_operation=r["last_operation"])
