"""DSL de skill `automation/v1alpha1`: o documento que o painel edita, o LLM propõe e o compilador lê (design §12.2).

É contrato, e por isso mora em `contracts`: só stdlib e pydantic (regra D1). O esquema JSON deste módulo é congelado
em `tests/contratos/skill-dsl.v1alpha1.json`; mudar um campo, um padrão ou um limite muda o contrato com o painel e
com o prompt que gera candidatas, e o teste obriga a dizer isso no commit.

Três escolhas que valem explicação:

* **`extra="forbid"` em tudo.** Um campo desconhecido é recusado, nunca ignorado: um documento vindo do LLM é dado
  validado, e um campo que ninguém lê seria instrução sem dono (§12.1, decisão 4).
* **Não existe `local_proof`.** A prova local é atalho POSITIVO — dispensa o verificador. Declarada num documento, ela
  afrouxaria a verificação; só o catálogo, que é código revisado, a declara. O compilador aponta a tentativa como
  `E_VERIFICATION_WEAKENED`, não como campo desconhecido.
* **Texto é texto.** Nenhum campo é avaliado como código. Expressões são só as da §12.2 (`${parameters.x}`,
  `${item}`, `${steps.<id>.output}`), analisadas pelo compilador por gramática fechada, nunca por `eval`.

Os vocabulários (estratégias, tipos de recurso, níveis de entrega) são repetidos aqui como `Literal`, e não
importados dos módulos que os usam, porque o contrato não depende de ninguém; o teste do contrato confere que os
dois lados continuam iguais.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

API_VERSION = "automation/v1alpha1"
KIND = "Skill"

#: `skill_definitions.id` (§10.1). Sem `:`, de propósito: `flow:` é o prefixo do adaptador legado.
SKILL_ID = r"^[a-z][a-z0-9_.-]{2,63}$"
#: `NodeId` (§5): o mesmo padrão de `PlanStep.key`, porque o id do nó VIRA a chave da etapa (decisão 2).
NODE_ID = r"^[a-z][a-z0-9_]{1,40}$"
#: Nome de parâmetro: o mesmo `PLACEHOLDER` dos fluxos (`taskqueue/flows.py`).
PARAMETER_NAME = r"^[a-z_][a-z0-9_]*$"

TEXTO_CURTO = 200
TEXTO_LONGO = 2000

StrategyName = Literal["deterministic", "recipe", "app_provider", "ui_generic", "ai_actor", "human"]
DeliveryLevelName = Literal["none", "appeared", "sent", "delivered", "read"]
ResourceKind = Literal["device.state", "app.installation", "account.binding", "app.session"]
ParameterType = Literal["string", "text", "integer", "boolean", "enum", "handle", "url"]
#: `items_collected` fica de fora: só capability de coleta do catálogo produz itens.
GoalPostconditionKind = Literal["text_visible", "app_foreground", "element_present", "model_judged"]


class _Estrito(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Metadata(_Estrito):
    id: str = Field(pattern=SKILL_ID)
    name: str = Field(min_length=1, max_length=TEXTO_CURTO)
    description: str | None = Field(default=None, max_length=TEXTO_LONGO)
    #: Id do app (tabela `apps`), não o pacote.
    app: str = Field(min_length=1, max_length=64)
    labels: dict[str, str] = Field(default_factory=dict)


class Invocation(_Estrito):
    #: Texto com `{param}`: é o que casa com o comando da pessoa. Teto do `_extract` dos fluxos.
    command_template: str = Field(min_length=1, max_length=500)
    examples: list[str] = Field(default_factory=list, max_length=20)


class ParameterSpec(_Estrito):
    """§10.4. Segredo não é parâmetro: credencial entra só pelo nome, em `requires.secrets` (ADR-025)."""

    name: str = Field(pattern=PARAMETER_NAME, max_length=40)
    type: ParameterType = "string"
    required: bool = True
    default: str | int | bool | None = None
    example: str | None = Field(default=None, max_length=TEXTO_CURTO)
    description: str | None = Field(default=None, max_length=TEXTO_LONGO)
    values: list[str] | None = Field(default=None, max_length=50)
    pattern: str | None = Field(default=None, max_length=TEXTO_CURTO)
    max_length: int | None = Field(default=None, ge=1, le=500)


class DeviceRequirements(_Estrito):
    api_level_min: int | None = Field(default=None, ge=1)
    play_store: bool | None = None
    abis: list[str] = Field(default_factory=list)


class Requires(_Estrito):
    apps: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    device: DeviceRequirements | None = None
    #: Informativo na v1alpha1: alimenta a estimativa de custo.
    ai_roles: list[str] = Field(default_factory=list)


class ResourceSpec(_Estrito):
    """§11: estado desejado de que a skill precisa. Validado agora, aplicado só pelas portas atuais até a fase H."""

    kind: ResourceKind
    target: str | None = None
    desired: str | dict[str, str]
    on_missing: Literal["wait", "apply", "ask"] = "wait"


class UsesEntry(_Estrito):
    skill: str = Field(pattern=SKILL_ID)
    #: Versão EXATA. Opcional no esquema só para o compilador dizer `E_SKILL_VERSION_UNPINNED`, em vez de um erro
    #: genérico de campo ausente.
    version: int | None = Field(default=None, ge=1)


class GoalSpec(_Estrito):
    """Contrato inline de um nó sem capability (app sem catálogo, ou navegação sem efeito num app com catálogo)."""

    title: str = Field(min_length=1, max_length=TEXTO_CURTO)
    goal: str = Field(min_length=1, max_length=TEXTO_LONGO)
    precondition: str | None = Field(default=None, max_length=TEXTO_LONGO)


class PostconditionSpec(_Estrito):
    kind: GoalPostconditionKind
    value: str = Field(min_length=1, max_length=TEXTO_LONGO)
    description: str | None = Field(default=None, max_length=TEXTO_LONGO)
    required_delivery_level: DeliveryLevelName | None = None


class VerificationSpec(_Estrito):
    """Nó `capability`: só `required_delivery_level`, e só para subir. Nó `goal`: a pós-condição inteira."""

    required_delivery_level: DeliveryLevelName | None = None
    postcondition: PostconditionSpec | None = None


class NodeSpec(_Estrito):
    """Um nó do processo (§12.2). Exatamente um de `capability`, `goal` ou `skill` — conferido pelo compilador, que
    diz qual combinação veio e onde."""

    id: str = Field(pattern=NODE_ID)
    capability: str | None = Field(default=None, max_length=64)
    goal: GoalSpec | None = None
    skill: str | None = Field(default=None, pattern=SKILL_ID)
    app: str | None = Field(default=None, max_length=64)
    with_: dict[str, str] = Field(default_factory=dict, alias="with")
    #: Omitido = depende do nó anterior; independência exige `[]` explícito. O compilador SEMPRE emite a lista.
    depends_on: list[str] | None = None
    foreach: str | None = None
    #: Expressão ESTÁTICA sobre parâmetros: `${parameters.x}`, `== 'lit'` ou `!= 'lit'`.
    when: str | None = Field(default=None, max_length=TEXTO_CURTO)
    timeout_s: int | None = Field(default=None, ge=10, le=900)
    retries: int | None = Field(default=None, ge=0, le=4)
    strategies: list[StrategyName] | None = Field(default=None, min_length=1)
    verification: VerificationSpec | None = None
    side_effect: bool | None = None
    commit_guard: list[str] = Field(default_factory=list, max_length=10)
    #: Reservado: a regra de falha é global do scheduler na v1alpha1.
    on_failure: Literal["fail"] = "fail"


class OutputSpec(_Estrito):
    name: str = Field(pattern=PARAMETER_NAME, max_length=40)
    from_: str = Field(alias="from")


class ValidationExpectation(_Estrito):
    outcome: Literal["succeeded", "failed", "uncertain", "not_proved", "waiting_user"]
    proofs: list[str] = Field(default_factory=list)


class ValidationCase(_Estrito):
    """Caso de validação (043). `parameters` nunca guarda segredo."""

    name: str = Field(min_length=1, max_length=80)
    kind: Literal["replay", "simulated", "device", "negative"]
    parameters: dict[str, str] = Field(default_factory=dict)
    preconditions: dict[str, str] = Field(default_factory=dict)
    expected: ValidationExpectation


class ValidationSpec(_Estrito):
    cases: list[ValidationCase] = Field(default_factory=list)


class SkillSpec(_Estrito):
    invocation: Invocation
    parameters: list[ParameterSpec] = Field(default_factory=list, max_length=30)
    requires: Requires = Field(default_factory=Requires)
    resources: list[ResourceSpec] = Field(default_factory=list)
    uses: list[UsesEntry] = Field(default_factory=list)
    nodes: list[NodeSpec] = Field(min_length=1, max_length=100)
    outputs: list[OutputSpec] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list, max_length=20)
    #: Só `inherit`: a governança inteira do catálogo e do perfil. Política por nó fica para a v1alpha2.
    policy: Literal["inherit"] = "inherit"
    validation: ValidationSpec = Field(default_factory=ValidationSpec)


class SkillDocument(_Estrito):
    api_version: Literal["automation/v1alpha1"] = Field(alias="apiVersion")
    kind: Literal["Skill"]
    metadata: Metadata
    spec: SkillSpec
