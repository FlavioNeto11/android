"""Orquestra a importação de releases: descobrir na inbox, validar, catalogar e registrar.

Confiança na assinatura é progressiva e explícita. A primeira release de um pacote entra como `validated` e só vira
`installable` quando o operador aprova a assinatura de propósito. Depois disso, qualquer release com assinatura
diferente é bloqueada automaticamente — o sistema não vira autoridade de procedência, mas também não aceita em
silêncio um APK de outra origem numa atualização futura.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Config
from ..events import EventBus
from ..models import ReleaseDTO, ReleaseState
from ..util import now_iso
from . import catalog
from .catalog import ReleaseValidationError
from .inspector import ApkInspector, sha256_of
from .repository import ReleaseRepository

log = logging.getLogger("poc.releases")


@dataclass(slots=True)
class ImportOutcome:
    """Resultado por conjunto encontrado na inbox. Conjunto rejeitado não some: fica lá com o motivo registrado."""

    label: str
    ok: bool
    release_id: str | None = None
    package_name: str | None = None
    version_name: str | None = None
    version_code: int | None = None
    status: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict:
        return {"label": self.label, "ok": self.ok, "release_id": self.release_id, "package": self.package_name,
                "version_name": self.version_name, "version_code": self.version_code, "status": self.status,
                "reason": self.reason}


class ReleaseService:
    def __init__(self, cfg: Config, repo: ReleaseRepository, inspector: ApkInspector, bus: EventBus):
        self.cfg = cfg
        self.repo = repo
        self.inspector = inspector
        self.bus = bus

    # ------------------------------------------------------------------ importação
    def import_inbox(self, *, source_reference: str | None = None, expected_package: str | None = None,
                     keep_source: bool = False) -> list[ImportOutcome]:
        """Varre `apks/inbox/`, valida cada conjunto e cataloga os aprovados."""
        if not self.inspector.available():
            raise ReleaseValidationError(
                "aapt2/apksigner não encontrados no Android SDK; sem eles não dá para inspecionar um APK.")
        found = catalog.discover(self.cfg.apk_inbox)
        candidates = found.sets + catalog.group_loose(found.loose, self.inspector)
        results: list[ImportOutcome] = []
        for candidate in candidates:
            try:
                results.append(self._import_one(candidate, source_reference=source_reference,
                                                expected_package=expected_package, keep_source=keep_source))
            except ReleaseValidationError as exc:
                results.append(ImportOutcome(label=candidate.label, ok=False, reason=str(exc)))
                self.bus.emit("log", f"Importação recusada ({candidate.label}): {exc}", level="warn")
            finally:
                catalog.cleanup(candidate)
        if not results:
            self.bus.emit("log", f"Nenhum APK encontrado em {self.cfg.file.paths.apk_inbox}.", level="warn")
        return results

    def _import_one(self, candidate: catalog.CandidateSet, *, source_reference: str | None,
                    expected_package: str | None, keep_source: bool) -> ImportOutcome:
        release = catalog.validate(candidate, self.inspector, expected_package=expected_package)
        status, detail = self._signature_verdict(release.package_name, release.signature_sha256)
        target = catalog.store(release, self.cfg.apk_catalog)
        meta = catalog.metadata(release)
        self.repo.save_release(
            release_id=release.release_id, package_name=release.package_name, version_name=release.version_name,
            version_code=release.version_code, artifact_type=release.artifact_type,
            signature_sha256=release.signature_sha256, min_sdk=release.min_sdk, target_sdk=release.target_sdk,
            abis=release.abis, catalog_dir=str(target.relative_to(self.cfg.root)), source_type="inbox",
            source_reference=source_reference, status=status, detail=detail, files=meta["files"])
        if not keep_source:
            self._clear_source(candidate)
        self.bus.emit("log", f"Release importada: {release.package_name} {release.version_name} "
                             f"({release.version_code}) — {status.value}",
                      data={"release_id": release.release_id, "status": status.value})
        return ImportOutcome(label=candidate.label, ok=True, release_id=release.release_id,
                             package_name=release.package_name, version_name=release.version_name,
                             version_code=release.version_code, status=status.value, reason=detail)

    def _signature_verdict(self, package_name: str, signature: str) -> tuple[ReleaseState, str | None]:
        trusted = self.repo.trusted_signer(package_name)
        if trusted is None:
            return ReleaseState.validated, ("Assinatura ainda não aprovada para este pacote. "
                                            "Aprove-a explicitamente para liberar a instalação.")
        if trusted.lower() != signature.lower():
            return ReleaseState.invalid, ("Assinatura diferente da aprovada para este pacote; instalação bloqueada "
                                          "até nova aprovação explícita.")
        return ReleaseState.installable, None

    def _clear_source(self, candidate: catalog.CandidateSet) -> None:
        """O conteúdo já está no catálogo imutável; o original sai da inbox para não ser reimportado sem querer."""
        if candidate.container or candidate.temp_dir:
            source = self.cfg.apk_inbox / candidate.label
        else:
            roots = {p.parent for p in candidate.files}
            source = next(iter(roots)) if len(roots) == 1 and next(iter(roots)) != self.cfg.apk_inbox else None  # type: ignore[assignment]
            if source is None:
                for p in candidate.files:
                    p.unlink(missing_ok=True)
                return
        try:
            if source.is_dir():
                shutil.rmtree(source, ignore_errors=True)
            else:
                source.unlink(missing_ok=True)
        except OSError:
            log.warning("não foi possível limpar a inbox: %s", candidate.label)

    # ------------------------------------------------------------------ confiança e consulta
    def approve_signature(self, release_id: str, *, note: str | None = None) -> ReleaseDTO:
        """Aprovação explícita do operador. Promove as releases do pacote que casam com esta assinatura."""
        row = self.repo.release_row(release_id)
        if row is None:
            raise ReleaseValidationError("Release não encontrada.")
        self.repo.approve_signer(row["package_name"], row["signature_sha256"], note)
        for other in self.repo.list_releases(row["package_name"]):
            if other.signature_sha256.lower() == row["signature_sha256"].lower():
                if other.status in (ReleaseState.validated, ReleaseState.invalid):
                    self.repo.set_status(other.id, ReleaseState.installable, None)
            elif other.status == ReleaseState.installable:
                self.repo.set_status(other.id, ReleaseState.invalid,
                                     "Assinatura diferente da aprovada para este pacote.")
        self.bus.emit("log", f"Assinatura aprovada para {row['package_name']}: {row['signature_sha256'][:16]}…",
                      level="warn", data={"package": row["package_name"]})
        return self.repo.release_dto(self.repo.release_row(release_id))  # type: ignore[arg-type]

    def list_releases(self, package_name: str | None = None) -> list[ReleaseDTO]:
        return self.repo.list_releases(package_name)

    def files_for_install(self, release_id: str) -> tuple[ReleaseDTO, list[Path]]:
        """Caminhos do conjunto, conferindo o hash de cada arquivo: artefato validado é imutável, e uma troca
        silenciosa no disco tem de virar erro, não instalação."""
        row = self.repo.release_row(release_id)
        if row is None:
            raise ReleaseValidationError("Release não encontrada.")
        base = self.cfg.path(row["catalog_dir"])
        paths: list[Path] = []
        for f in self.repo.files_of(release_id):
            path = base / f["file_name"]
            if not path.is_file():
                raise ReleaseValidationError(f"Arquivo ausente no catálogo: {f['file_name']}.")
            digest, _ = sha256_of(path)
            if digest.lower() != f["sha256"].lower():
                self.repo.set_status(release_id, ReleaseState.invalid, "Artefato adulterado: o hash mudou no disco.")
                raise ReleaseValidationError(f"Artefato adulterado: {f['file_name']} não confere com o hash registrado.")
            paths.append(path)
        return self.repo.release_dto(row), paths

    # ------------------------------------------------------------------ instalação com estado observado
    async def install_on(self, rt: Any, release_id: str, installer: Any) -> dict:
        """Instala a release no aparelho e só a considera pronta depois de ler o estado DO APARELHO e abrir o app.

        Sequência: conferir hash -> conferir compatibilidade -> marcar operação pendente -> instalar -> observar ->
        comparar com o esperado -> abrir e ver se o processo sobrevive. Código de retorno zero do ADB não é prova.
        """
        from ..devices.installer import compatibility, drift_of
        from ..models import InstalledAppState

        dto, paths = self.files_for_install(release_id)
        if dto.status != ReleaseState.installable:
            raise ReleaseValidationError(
                f"A release está em '{dto.status.value}' e não pode ser instalada. {dto.detail or ''}".strip())

        package = dto.package_name
        profile = await installer.profile(rt)
        compat = compatibility(min_sdk=dto.min_sdk, abis=dto.supported_abis, profile=profile)
        if compat.blocked:
            self.repo.upsert_app_state(rt.id, package, state=InstalledAppState.incompatible.value,
                                       desired_release_id=release_id, detail=compat.reason, pending_op=None)
            raise ReleaseValidationError(f"Aparelho incompatível com esta release: {compat.reason}.")

        self.repo.upsert_app_state(rt.id, package, desired_release_id=release_id, pending_op="install",
                                   pending_op_at=now_iso(), state=InstalledAppState.installing.value,
                                   detail=compat.reason, drift_kind=None)
        self.bus.emit("log", f"{rt.id}: instalando {package} {dto.version_name} ({dto.version_code})…",
                      instance_id=rt.id)
        try:
            await installer.install(rt, paths=paths)
        except Exception as exc:  # noqa: BLE001 - a falha tem de ficar registrada, não deixar o estado preso
            self.repo.upsert_app_state(rt.id, package, state=InstalledAppState.install_failed.value,
                                       pending_op=None, pending_op_at=None, detail=str(exc)[:300])
            self.bus.emit("log", f"{rt.id}: instalação de {package} falhou — {exc}", level="error", instance_id=rt.id)
            raise

        self.repo.upsert_app_state(rt.id, package, pending_op="verify", state=InstalledAppState.verifying.value)
        observed = await installer.inspect(rt, package)
        expected_splits = ["base"] + [f.split_name for f in dto.files if f.role == "split" and f.split_name]
        state, drift, detail = drift_of(observed, expected_version_code=dto.version_code,
                                        expected_splits=expected_splits)
        common = {
            "observed_version_name": observed.version_name, "observed_version_code": observed.version_code,
            "observed_splits": observed.splits, "first_install_time": observed.first_install_time,
            "last_update_time": observed.last_update_time, "pending_op": None, "pending_op_at": None,
        }
        if state is not InstalledAppState.installed:
            self.repo.upsert_app_state(rt.id, package, state=state.value, drift_kind=drift, detail=detail, **common)
            raise ReleaseValidationError(detail or "A instalação não pôde ser comprovada no aparelho.")

        ok, why = await installer.launch_probe(rt, package)
        final = InstalledAppState.ready if ok else InstalledAppState.verify_failed
        self.repo.upsert_app_state(rt.id, package, state=final.value, installed_release_id=release_id if ok else None,
                                   verified_at=now_iso() if ok else None, drift_kind=None, detail=why, **common)
        if ok and compat.verdict == "uncertain":
            self.bus.emit("log", f"{rt.id}: {package} roda com ABI traduzida ({compat.translated_abi}) — confirmado "
                                 "abrindo o app", instance_id=rt.id)
        self.bus.emit("log", f"{rt.id}: {package} {observed.version_name} ({observed.version_code}) — {why}",
                      level="info" if ok else "warn", instance_id=rt.id)
        if not ok:
            raise ReleaseValidationError(f"O app foi instalado mas não passou na prova de abertura: {why}.")
        return self.repo.app_state_dto(self.repo.app_state(rt.id, package)).model_dump(mode="json")

    def reconcile_after_restart(self) -> int:
        """Operação interrompida por queda do backend nunca é repetida às cegas: vira 'precisa verificar'."""
        rows = self.repo.pending_operations()
        for r in rows:
            self.repo.upsert_app_state(
                r["instance_id"], r["package_name"], pending_op=None, pending_op_at=None,
                state="verifying", detail="Operação interrompida por reinício; o estado será relido do aparelho.")
        if rows:
            self.bus.emit("log", f"{len(rows)} instalação(ões) interrompida(s) por reinício: estado será reobservado.",
                          level="warn")
        return len(rows)

    async def verify_on(self, rt: Any, package: str, installer: Any) -> dict:
        """Relê o estado do aparelho e compara com o que o banco esperava. É assim que a divergência aparece."""
        from ..devices.installer import drift_of
        from ..models import InstalledAppState

        row = self.repo.app_state(rt.id, package)
        expected_code: int | None = None
        expected_splits: list[str] = []
        release_id = (row["installed_release_id"] or row["desired_release_id"]) if row else None
        if release_id and (rel := self.repo.release_row(release_id)) is not None:
            expected_code = rel["version_code"]
            expected_splits = ["base"] + [f["split_name"] for f in self.repo.files_of(release_id)
                                          if f["role"] == "split" and f["split_name"]]
        observed = await installer.inspect(rt, package)
        state, drift, detail = drift_of(observed, expected_version_code=expected_code,
                                        expected_splits=expected_splits or None)
        if state is InstalledAppState.installed and release_id:
            state = InstalledAppState.ready
        self.repo.upsert_app_state(
            rt.id, package, state=state.value, drift_kind=drift, detail=detail,
            observed_version_name=observed.version_name, observed_version_code=observed.version_code,
            observed_splits=observed.splits, first_install_time=observed.first_install_time,
            last_update_time=observed.last_update_time, pending_op=None, pending_op_at=None,
            verified_at=now_iso() if state is InstalledAppState.ready else None)
        if drift:
            self.bus.emit("log", f"{rt.id}: divergência no app {package} — {detail}", level="warn", instance_id=rt.id)
        return self.repo.app_state_dto(self.repo.app_state(rt.id, package)).model_dump(mode="json")
