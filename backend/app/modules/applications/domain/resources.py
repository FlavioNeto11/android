"""Recurso `app.installation` (design §11): o app no aparelho, na versão promovida do parque.

`{release: promoted}` quer dizer o que a convergência de hoje (ADR-026) já persegue, com os mesmos limites:

* **na promovida** = a versão que ESTÁ no aparelho (a regra de `AppState.release_no_aparelho`) é a promovida, ou tem
  o mesmo número de uma promovida entregável — trocar um build pelo outro reinstalaria o parque para ficar igual
  (`fora_da_convergencia`);
* **mais nova e não voltada** fica (`held`): rebaixar sozinho desfaria a prova do canário. Mais nova e VOLTADA
  (`rolled_back`) converge, com rebaixamento;
* **espalhar não é convergir**: sem o app, só o app principal do aparelho (`instances.app_id`) o recebe sozinho; nos
  demais, instalar é "Distribuir", decisão de pessoa;
* **falha não se repete às cegas**: `install_failed`, `verify_failed`, `incompatible` e `version_drift` são de pessoa
  (a nova tentativa diária de `aplicar_versao_promovida` é da varredura, não do plano);
* **sem desfecho não é sucesso**: `verifying` sem dono (a instalação incerta de `InstalacaoIncerta`) e o aparelho
  nunca inspecionado são `unknown`, e a resposta é `app.verify` — ler o `pm`, não instalar. A porta de hoje, nesse
  caso, instala direto no app principal; o plano é mais estrito de propósito;
* operação com dono (`pending_op`) ou `installing` é espera: o desfecho vem dela.

`installed` e `ready` contam os dois como presente, como na porta do app (`_app_gate`).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.shared.resources import (UNSUPPORTED_DESIRED, Drift, DriftStatus, ObservedState, ResourceAction,
                                  ResourceKind, ResourceSpec, plan_by_rules)

KIND = ResourceKind.app_installation
DESIRED_PROMOTED: tuple[tuple[str, str], ...] = (("release", "promoted"),)


class InstallState(StrEnum):
    """`models.InstalledAppState`, repetido porque o domínio não vê `app.models`; o teste confere a igualdade."""

    missing = "missing"
    installing = "installing"
    installed = "installed"
    verifying = "verifying"
    ready = "ready"
    install_failed = "install_failed"
    verify_failed = "verify_failed"
    incompatible = "incompatible"
    version_drift = "version_drift"


class ReleaseChannel(StrEnum):
    """`models.ReleaseChannel`."""

    candidate = "candidate"
    canary = "canary"
    promoted = "promoted"
    quarantined = "quarantined"
    rolled_back = "rolled_back"


#: Entrega que falhou: daqui ninguém tenta de novo sozinho (`AppState._ENTREGA_FALHOU`).
FAILED = frozenset({InstallState.install_failed, InstallState.verify_failed, InstallState.incompatible,
                    InstallState.version_drift})
#: O app está no aparelho (`_app_gate` libera nestes dois).
PRESENT = frozenset({InstallState.installed, InstallState.ready})
#: `device_app_state.drift_kind` da recusa do Android em voltar de versão sem apagar dados.
DOWNGRADE_REFUSED = "downgrade_refused"


class AppVerb(StrEnum):
    """Comandos de app que existem (`commands/despacho.APP_COMMAND_VERBS`)."""

    install = "app.install"
    verify = "app.verify"


VERB_RISKS: dict[str, str] = {
    AppVerb.install.value: ("instalar mexe no disco do aparelho; rebaixar de versão pode ser recusado pelo Android, e "
                            "reinstalar do zero apaga a sessão da conta"),
    AppVerb.verify.value: "só lê o pacote instalado (pm); não muda o aparelho",
}


class AppCode(StrEnum):
    current = "current"
    app_unknown = "app_unknown"
    unobserved = "unobserved"
    unrecognized_state = "unrecognized_state"
    no_outcome = "no_outcome"
    no_version = "no_version"
    in_progress = "in_progress"
    delivery_failed = "delivery_failed"
    downgrade_refused = "downgrade_refused"
    no_promoted_release = "no_promoted_release"
    not_distributed = "not_distributed"
    newer_than_promoted = "newer_than_promoted"
    missing = "missing"
    outdated = "outdated"
    other_build = "other_build"
    abandoned_version = "abandoned_version"


_CONVERGE: dict[str, str] = {c.value: AppVerb.install.value for c in (
    AppCode.missing, AppCode.outdated, AppCode.other_build, AppCode.abandoned_version)}
_OBSERVE: dict[str, str] = {c.value: AppVerb.verify.value for c in (
    AppCode.unobserved, AppCode.unrecognized_state, AppCode.no_outcome, AppCode.no_version)}


@dataclass(frozen=True, slots=True)
class ReleaseView:
    """O que o plano precisa de uma linha de `app_releases`."""

    id: str
    version_name: str
    version_code: int
    #: `None` quando o canal lido não é um conhecido.
    channel: ReleaseChannel | None
    #: `status == installable`: o arquivo pode ser instalado.
    installable: bool

    def label(self) -> str:
        return f"{self.version_name} ({self.version_code})"


@dataclass(frozen=True, slots=True, kw_only=True)
class InstallationObserved(ObservedState):
    #: O alvo do documento está cadastrado em `apps`.
    app_registered: bool
    package: str | None = None
    #: `instances.app_id` é este app: o único que a convergência instala em quem não o tem (ADR-026).
    main_app: bool = False
    #: Há linha em `device_app_state` (sem ela, o aparelho nunca foi inspecionado para este pacote).
    inspected: bool = False
    #: `None` também quando o valor lido não é um estado conhecido.
    state: InstallState | None = None
    pending_op: str | None = None
    observed_version_name: str | None = None
    observed_version_code: int | None = None
    drift_kind: str | None = None
    detail: str | None = None
    verified_at: str | None = None
    #: A release que ESTÁ no aparelho (regra de `release_no_aparelho`); `None` = nenhuma do catálogo.
    installed: ReleaseView | None = None
    #: A promovida-alvo do pacote: a maior, com o desempate de `releases_of_channel`.
    promoted: ReleaseView | None = None

    def facts(self) -> tuple[tuple[str, str], ...]:
        pares = [("pacote", self.package or "não cadastrado"),
                 ("estado", self.state.value if self.state is not None
                  else ("não inspecionado" if not self.inspected else "desconhecido")),
                 ("versão observada", f"{self.observed_version_name} ({self.observed_version_code})"
                  if self.observed_version_code is not None else "nenhuma"),
                 ("release instalada", self.installed.label() if self.installed is not None else "nenhuma"),
                 ("promovida", self.promoted.label() if self.promoted is not None else "nenhuma"),
                 ("app principal do aparelho", "sim" if self.main_app else "não")]
        if self.pending_op:
            pares.append(("operação em curso", self.pending_op))
        return tuple(pares)


def _drift(spec: ResourceSpec, observed: InstallationObserved, status: DriftStatus, code: str, detail: str) -> Drift:
    return Drift(spec=spec, target=observed.target, status=status, code=code, detail=detail, observed=observed)


def diff_app_installation(spec: ResourceSpec, observed: ObservedState) -> Drift:
    if spec.ref.kind is not KIND or not isinstance(observed, InstallationObserved):
        raise TypeError(f"diff de {KIND.value} recebeu {spec.ref.kind.value}/{type(observed).__name__}")
    o, iid, app = observed, observed.target.instance_id, spec.ref.target
    if spec.desired != DESIRED_PROMOTED or not app:
        return _drift(spec, o, DriftStatus.unsupported, UNSUPPORTED_DESIRED,
                      f"{KIND.value} precisa de um app alvo e só sabe perseguir 'release=promoted' "
                      f"(pedido: alvo '{app or '-'}', '{spec.desired_text()}')")
    if not o.app_registered or not o.package:
        return _drift(spec, o, DriftStatus.blocked, AppCode.app_unknown, f"o app '{app}' não está cadastrado")
    pkg = o.package
    if not o.inspected:
        return _drift(spec, o, DriftStatus.unknown, AppCode.unobserved,
                      f"{pkg} nunca foi inspecionado em {iid}: não se sabe se está instalado")
    if o.state is None:
        return _drift(spec, o, DriftStatus.unknown, AppCode.unrecognized_state,
                      f"o estado de {pkg} em {iid} não é um estado conhecido")
    if o.pending_op or o.state is InstallState.installing:
        return _drift(spec, o, DriftStatus.pending, AppCode.in_progress,
                      f"há uma operação de {pkg} em curso em {iid} ({o.pending_op or o.state.value})")
    if o.state is InstallState.verifying:
        return _drift(spec, o, DriftStatus.unknown, AppCode.no_outcome,
                      f"a última operação de {pkg} em {iid} ficou sem desfecho; o aparelho precisa ser relido")
    if o.state in FAILED:
        motivo = o.detail or o.state.value
        if o.drift_kind == DOWNGRADE_REFUSED:
            return _drift(spec, o, DriftStatus.blocked, AppCode.downgrade_refused,
                          f"o Android recusou voltar {pkg} de versão em {iid} sem apagar os dados: {motivo}")
        return _drift(spec, o, DriftStatus.blocked, AppCode.delivery_failed,
                      f"a entrega de {pkg} falhou em {iid} e não é repetida sozinha: {motivo}")
    alvo = o.promoted if o.promoted is not None and o.promoted.installable else None
    if o.state not in PRESENT:                       # `missing`
        if alvo is None:
            return _drift(spec, o, DriftStatus.blocked, AppCode.no_promoted_release,
                          f"{pkg} não está em {iid} e não há versão promovida entregável para instalar")
        if not o.main_app:
            return _drift(spec, o, DriftStatus.blocked, AppCode.not_distributed,
                          f"{pkg} não está em {iid}, que não é aparelho deste app: espalhar o app é Distribuir, "
                          "decisão de pessoa (ADR-026)")
        return _drift(spec, o, DriftStatus.diverged, AppCode.missing,
                      f"{pkg} não está em {iid}: a promovida {alvo.label()} seria instalada")
    if alvo is None:
        return _drift(spec, o, DriftStatus.held, AppCode.no_promoted_release,
                      f"{pkg} está em {iid}, mas não há versão promovida entregável para comparar; fica na que tem")
    instalada = o.installed
    codigo = o.observed_version_code if o.observed_version_code is not None else (
        instalada.version_code if instalada is not None else None)
    if codigo is None:
        return _drift(spec, o, DriftStatus.unknown, AppCode.no_version,
                      f"{pkg} consta em {iid} sem versão lida")
    if instalada is not None and instalada.id == alvo.id:
        return _drift(spec, o, DriftStatus.in_sync, AppCode.current, f"{pkg} na promovida {alvo.label()} em {iid}")
    if codigo == alvo.version_code and instalada is not None and instalada.version_code == codigo \
            and instalada.channel is ReleaseChannel.promoted and instalada.installable:
        return _drift(spec, o, DriftStatus.in_sync, AppCode.current,
                      f"{pkg} em {iid} já está numa promovida de mesmo número ({instalada.label()})")
    voltada = instalada is not None and instalada.channel is ReleaseChannel.rolled_back \
        and instalada.version_code == codigo
    if codigo > alvo.version_code:
        if voltada:
            return _drift(spec, o, DriftStatus.diverged, AppCode.abandoned_version,
                          f"{pkg} em {iid} está numa versão voltada ({codigo}); a promovida {alvo.label()} seria "
                          "instalada com rebaixamento, preservando os dados")
        return _drift(spec, o, DriftStatus.held, AppCode.newer_than_promoted,
                      f"{pkg} em {iid} tem uma versão mais nova ({codigo}) que a promovida {alvo.label()}; o parque "
                      "não rebaixa sozinho uma versão que ninguém voltou")
    if codigo == alvo.version_code:
        return _drift(spec, o, DriftStatus.diverged, AppCode.other_build,
                      f"{pkg} em {iid} tem o número da promovida ({codigo}) num build fora dela; a promovida "
                      f"{alvo.label()} seria instalada")
    return _drift(spec, o, DriftStatus.diverged, AppCode.outdated,
                  f"{pkg} em {iid} está em {codigo}, abaixo da promovida {alvo.label()}")


def plan_app_installation(drift: Drift) -> list[ResourceAction]:
    return plan_by_rules(drift, kind=KIND, converge=_CONVERGE, observe=_OBSERVE)
