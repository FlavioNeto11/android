"""`ResourceProvider` de `app.installation` (design §7, §8, §11): a leitura, o `diff` e o `plan`; com um `CommandBus`,
também `apply`, `verify` e `reconcile`.

Lê `apps`, `instances.app_id`, `app_releases` e `device_app_state` — só `SELECT`. As regras de leitura que no legado
moram dentro de métodos que também GRAVAM (`AppState.aplicar_versao_promovida` faz upsert da versão desejada;
`_app_resolver` a adota antes de responder) são refeitas aqui sobre as linhas, sem efeito:

* a promovida-alvo é a de `ReleaseService.promoted_release`: a maior entre as promovidas, com o desempate de
  `ReleaseRepository.releases_of_channel` (número, depois `channel_at`, depois id — ordenado em Python, K-030);
* a release que ESTÁ no aparelho é a de `AppState.release_no_aparelho`: a registrada como instalada; sem ela, a
  desejada se o número observado é o dela; sem isso, a do catálogo com aquele número, a voltada primeiro.

Com um `CommandBus` (segunda parte da fase H), aplica pelo canal de comandos o que a convergência de hoje faz:

* `app.install` = a entrega da porta do app (`AppState._entregar`: a promovida, com `-d` quando o parque sai de uma
  versão voltada, e a falha virando estado para não repetir), num comando `app.install` como o da rota de instalação;
* `app.verify` = a releitura do `pm` (`ReleaseService.verify_on`), a resposta a `unknown` — nunca instalar às cegas.

A prova de um `uncertain` é a leitura do `pm` (`device_app_state.verified_at`) POSTERIOR ao comando e na promovida.
"""
from __future__ import annotations

from collections.abc import Mapping

from app.db import Database, Row
from app.modules.applications.domain.resources import (AppVerb, InstallationObserved, InstallState, ReleaseChannel,
                                                       ReleaseView, diff_app_installation, plan_app_installation)
from app.modules.applications.infrastructure.app_repository import AppRepository
from app.shared.commands import CommandBus, CommandRef, RunRef
from app.shared.convergence import (ReconcileOutcome, ResourceVerification, apply_action, reconcile_resource,
                                    verify_resource)
from app.shared.resources import (Drift, ObservedState, ResourceAction, ResourceKind, ResourceRef, ResourceSpec,
                                  Target, known)

#: Os verbos que este recurso pede — é por eles que o `reconcile` procura os próprios incertos.
VERBS: tuple[str, ...] = tuple(v.value for v in AppVerb)


def _texto(valor: object) -> str | None:
    if valor is None or isinstance(valor, str):
        return valor
    raise TypeError(f"esperado texto ou nulo, veio {type(valor).__name__}")


def _inteiro(valor: object) -> int | None:
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise TypeError(f"esperado inteiro ou nulo, veio {type(valor).__name__}")
    return valor


def _release(r: Row) -> ReleaseView:
    codigo = _inteiro(r["version_code"])
    if codigo is None:
        raise TypeError("app_releases.version_code nulo")
    return ReleaseView(id=str(r["id"]), version_name=str(r["version_name"]), version_code=codigo,
                       channel=known(ReleaseChannel, r["channel"]), installable=r["status"] == "installable")


class AppInstallationProvider:
    kind = ResourceKind.app_installation

    def __init__(self, db: Database, *, bus: CommandBus | None = None) -> None:
        self._db = db
        self._apps = AppRepository(db)
        #: Sem canal, o provider é só leitura (o `PlanReport` não precisa de mais).
        self._bus = bus

    def read_current_state(self, ref: ResourceRef, target: Target) -> InstallationObserved:
        app = self._apps.obter(ref.target) if ref.target else None
        if app is None:
            return InstallationObserved(ref=ref, target=target, app_registered=False)
        pacote, iid = app["package"], target.instance_id
        principal = self._db.scalar("SELECT app_id FROM instances WHERE id=?", (iid,)) == app["id"]
        linhas = self._db.query("SELECT id, version_name, version_code, channel, channel_at, status FROM app_releases"
                                " WHERE package_name=?", (pacote,))
        promovidas = sorted((r for r in linhas if r["channel"] == ReleaseChannel.promoted.value),
                            key=lambda r: (int(r["version_code"]), str(r["channel_at"] or ""), str(r["id"])),
                            reverse=True)
        promovida = _release(promovidas[0]) if promovidas else None
        estado = self._db.one(
            "SELECT state, pending_op, desired_release_id, installed_release_id, observed_version_name,"
            " observed_version_code, drift_kind, detail, verified_at FROM device_app_state"
            " WHERE instance_id=? AND package_name=?", (iid, pacote))
        if estado is None:
            return InstallationObserved(ref=ref, target=target, app_registered=True, package=pacote,
                                        main_app=principal, promoted=promovida)
        return InstallationObserved(
            ref=ref, target=target, app_registered=True, package=pacote, main_app=principal, inspected=True,
            state=known(InstallState, estado["state"]), pending_op=_texto(estado["pending_op"]),
            observed_version_name=_texto(estado["observed_version_name"]),
            observed_version_code=_inteiro(estado["observed_version_code"]), drift_kind=_texto(estado["drift_kind"]),
            detail=_texto(estado["detail"]), verified_at=_texto(estado["verified_at"]),
            installed=self._no_aparelho(estado, linhas), promoted=promovida)

    @staticmethod
    def _no_aparelho(estado: Row, linhas: list[Row]) -> ReleaseView | None:
        por_id = {str(r["id"]): r for r in linhas}
        if (instalada := _texto(estado["installed_release_id"])) is not None:
            return _release(por_id[instalada]) if instalada in por_id else None
        observado = _inteiro(estado["observed_version_code"])
        if observado is None:
            return None
        desejada = por_id.get(str(estado["desired_release_id"])) if estado["desired_release_id"] else None
        if desejada is not None and int(desejada["version_code"]) == observado:
            return _release(desejada)
        candidatas = sorted((r for r in linhas if int(r["version_code"]) == observado),
                            key=lambda r: (r["channel"] != ReleaseChannel.rolled_back.value, str(r["id"])))
        return _release(candidatas[0]) if candidatas else None

    def diff(self, desired: ResourceSpec, observed: ObservedState) -> Drift:
        return diff_app_installation(desired, observed)

    def plan(self, drift: Drift) -> list[ResourceAction]:
        return plan_app_installation(drift)

    def _canal(self) -> CommandBus:
        if self._bus is None:
            raise RuntimeError("AppInstallationProvider sem CommandBus: só leitura")
        return self._bus

    def apply(self, action: ResourceAction, *, spec: ResourceSpec, run_ref: RunRef | None = None) -> CommandRef:
        return apply_action(self._canal(), self, action, spec=spec, params_of=_parametros, run_ref=run_ref)

    def verify(self, spec: ResourceSpec, target: Target) -> ResourceVerification:
        return verify_resource(self, spec, target)

    def reconcile(self, spec: ResourceSpec, target: Target) -> ReconcileOutcome:
        return reconcile_resource(self._canal(), self, spec, target, verbs=VERBS, proof_time=_lido_em)


def _parametros(observado: ObservedState, verbo: str) -> Mapping[str, str] | str:
    """`app.install` leva a promovida-alvo LIDA AGORA (não a do plano): é ela que a porta do app entregaria."""
    if not isinstance(observado, InstallationObserved) or not observado.package:
        return "o app não está cadastrado"
    if verbo == AppVerb.verify.value:
        return {"package": observado.package}
    alvo = observado.promoted
    if alvo is None or not alvo.installable:
        return f"{observado.package} não tem versão promovida entregável"
    return {"package": observado.package, "release_id": alvo.id}


def _lido_em(observado: ObservedState) -> str | None:
    return observado.verified_at if isinstance(observado, InstallationObserved) else None
