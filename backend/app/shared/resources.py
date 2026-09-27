"""Modelo de recursos declarativos (design §11): o estado DESEJADO de que uma skill precisa, o que se OBSERVOU, a
diferença entre os dois e as ações que a fechariam.

Mora no kernel porque três contextos pares implementam um tipo de recurso cada (fleet: `device.state`;
applications: `app.installation`; identity: `account.binding` e `app.session`) e o contexto de execução consome os
quatro no PLAN (§14.2). Posto num deles, os outros dois dependeriam de um par; posto em execução, os três
dependeriam de quem está abaixo deles no grafo (D5).

Três regras valem para todo tipo de recurso, e é por isso que o `plan` genérico mora aqui:

* **O que não se sabe não vira "já está certo".** `unknown` é um estado próprio: a resposta a ele é LER (um comando
  de leitura que já existe, como `app.verify`), nunca aplicar. Sem comando de leitura, nenhuma ação — e o recurso
  continua `unknown` no relatório, jamais `in_sync`.
* **Observado = desejado ⇒ zero ações.** A segunda passada sobre o mesmo estado não planeja nada.
* **Só comando que já existe.** `ResourceAction.verb` é um verbo de `commands/despacho.py`; o que nenhum comando sabe
  fazer vira `ask` (pessoa), não um verbo inventado.

Tudo aqui é valor imutável e função pura: ler, aplicar e verificar são de quem implementa (infraestrutura), e
aplicar passa só por `commands` (R10) — fora do escopo desta primeira parte da fase H.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class ResourceKind(StrEnum):
    """Os tipos de recurso da DSL (`contracts/skills/v1alpha1.ResourceKind`); o teste confere que são os mesmos."""

    device_state = "device.state"
    app_installation = "app.installation"
    account_binding = "account.binding"
    app_session = "app.session"


class OnMissing(StrEnum):
    """O que a skill pede quando o recurso não está no estado desejado.

    `wait` e `apply` planejam a MESMA convergência: até o apply da fase H, as portas do `_tick` (rodízio, porta do
    app, porta de sessão) já são o "apply" desses recursos (§11), e o plano diz o que elas fariam. A diferença — quem
    dispara — só passa a valer quando o `apply` existir. `ask` troca a convergência por uma pessoa.
    """

    wait = "wait"
    apply = "apply"
    ask = "ask"


#: `online`, ou um mapa (`{release: promoted}`) em pares ordenados por chave — a mesma forma de `ResourceDecl` (IR).
DesiredValue = str | tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """(tipo, alvo) — §5. O alvo é o do DOCUMENTO: o id do app (`instagram`) ou `None`, o próprio aparelho."""

    kind: ResourceKind
    target: str | None = None

    def label(self) -> str:
        return f"{self.kind.value}({self.target})" if self.target else self.kind.value


@dataclass(frozen=True, slots=True)
class Target:
    """ONDE o recurso é resolvido: um aparelho e, quando a execução escolheu um, o perfil (`RunCreate.profile_ids`)."""

    instance_id: str
    profile_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    ref: ResourceRef
    desired: DesiredValue
    on_missing: OnMissing = OnMissing.wait

    @classmethod
    def of(cls, kind: str, target: str | None, desired: str | Mapping[str, str],
           on_missing: str = OnMissing.wait.value) -> ResourceSpec:
        """Normaliza o que vem do documento ou do IR. Tipo ou `on_missing` desconhecido levanta `ValueError`: o
        compilador já os recusa (`E_RESOURCE_UNKNOWN_KIND`), e aqui é a segunda camada, não um silêncio."""
        valor: DesiredValue = desired if isinstance(desired, str) else tuple(sorted(desired.items()))
        return cls(ResourceRef(ResourceKind(kind), target), valor, OnMissing(on_missing))

    def desired_map(self) -> dict[str, str]:
        """O desejado como mapa; `{}` quando é um valor só (`online`)."""
        return {} if isinstance(self.desired, str) else dict(self.desired)

    def desired_text(self) -> str:
        if isinstance(self.desired, str):
            return self.desired
        return ", ".join(f"{k}={v}" for k, v in self.desired)


class DriftStatus(StrEnum):
    #: Observado = desejado, COM observação que o sustenta.
    in_sync = "in_sync"
    #: Observado ≠ desejado, e se sabe o que o fecharia.
    diverged = "diverged"
    #: Há uma operação em curso (ligando, instalando): o desfecho vem dela, e nada se planeja por cima.
    pending = "pending"
    #: Diverge, mas a regra do parque manda ficar (ex.: versão mais nova que a promovida, ADR-026). Sem ação e sem
    #: bloqueio; o relatório avisa.
    held = "held"
    #: Não se sabe. A resposta é ler/provar, nunca agir às cegas.
    unknown = "unknown"
    #: Só uma pessoa resolve (desafio de segurança, entrega que falhou, aparelho em erro).
    blocked = "blocked"
    #: O estado desejado pedido não é um que este tipo de recurso saiba interpretar: o documento precisa mudar.
    unsupported = "unsupported"


#: Código de `Drift` para o par (recurso, alvo) que ninguém leu. É do kernel porque quem o produz é o relatório, não
#: um provider; nenhum `plan` tem ação para ele.
NOT_READ = "not_read"
#: Código comum de `unsupported`.
UNSUPPORTED_DESIRED = "unsupported_desired"


@dataclass(frozen=True, slots=True, kw_only=True)
class ObservedState:
    """O que um provider leu, sem efeito nenhum. Cada tipo de recurso estende com os seus campos."""

    ref: ResourceRef
    target: Target

    def facts(self) -> tuple[tuple[str, str], ...]:
        """O observado para o relatório, em pares ordenados. Nunca segredo: credencial entra só como "tem/não tem"."""
        return ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Drift:
    spec: ResourceSpec
    target: Target
    status: DriftStatus
    #: Motivo estável, do vocabulário de cada tipo de recurso (ex.: `stopped`, `outdated`, `challenge`). É o que o
    #: `plan` lê para escolher o comando, e o que o painel pode traduzir.
    code: str
    #: Em português, para quem lê o relatório.
    detail: str
    #: `None` só quando ninguém leu (`NOT_READ`).
    observed: ObservedState | None = None

    @property
    def ref(self) -> ResourceRef:
        return self.spec.ref


class ActionPurpose(StrEnum):
    #: Ler/provar o estado; não muda o alvo. É a única resposta a `unknown`.
    observe = "observe"
    #: Levar ao desejado por um comando existente.
    converge = "converge"
    #: Só uma pessoa resolve; nenhum comando.
    ask = "ask"


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceAction:
    ref: ResourceRef
    target: Target
    purpose: ActionPurpose
    #: Verbo de um comando que JÁ existe (`commands/despacho.py`); `None` só em `ask`.
    verb: str | None
    reason: str

    def __post_init__(self) -> None:
        if (self.purpose is ActionPurpose.ask) != (self.verb is None):
            raise ValueError("ação de pessoa não tem verbo, e ação de sistema sempre tem")


def _acao(drift: Drift, purpose: ActionPurpose, verb: str | None, reason: str | None = None) -> ResourceAction:
    return ResourceAction(ref=drift.ref, target=drift.target, purpose=purpose, verb=verb,
                          reason=reason or drift.detail)


def plan_by_rules(drift: Drift, *, kind: ResourceKind, converge: Mapping[str, str], observe: Mapping[str, str],
                  always_ask: bool = False) -> list[ResourceAction]:
    """O `plan` de todo tipo de recurso: as regras comuns, com a tabela código → verbo de cada um.

    `converge` e `observe` mapeiam o `code` do drift no verbo que o fecharia. `always_ask` é para o recurso que nunca
    se aplica sozinho (`account.binding`: vincular é decisão de pessoa, §11).
    """
    if drift.ref.kind is not kind:
        raise ValueError(f"plan de {kind.value} recebeu um drift de {drift.ref.kind.value}")
    status = drift.status
    if status in (DriftStatus.in_sync, DriftStatus.held, DriftStatus.pending):
        return []
    if status in (DriftStatus.blocked, DriftStatus.unsupported):
        return [_acao(drift, ActionPurpose.ask, None)]
    if status is DriftStatus.unknown:
        # Só leitura. Sem comando que leia, nada: o recurso segue `unknown` no relatório, nunca "certo".
        verbo = observe.get(drift.code)
        return [_acao(drift, ActionPurpose.observe, verbo)] if verbo is not None else []
    if always_ask or drift.spec.on_missing is OnMissing.ask:
        return [_acao(drift, ActionPurpose.ask, None)]
    verbo = converge.get(drift.code)
    if verbo is None:
        return [_acao(drift, ActionPurpose.ask, None,
                      f"{drift.detail} (nenhum comando existente converge este caso)")]
    return [_acao(drift, ActionPurpose.converge, verbo)]
