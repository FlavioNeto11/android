"""`CapabilityDefinition`: a operação semântica de um app, como valor imutável (design §5, §15.1; ADR-032, proposto).

É o mesmo contrato de `planning/capabilities.py::Capability`, campo a campo, agrupado pelo que cada campo governa:
o contrato (parâmetros, pré e pós-condição, prova local), o efeito, a governança, a execução, as saídas e os
requisitos do app. O mapeamento é 1:1 e fica em `infrastructure/catalog_registry.py`, junto com o teste que falha
se o catálogo ganhar um campo sem destino aqui.

O catálogo em código continua sendo a fonte até a fase K. Este VO é a forma que o resto do sistema novo enxerga —
o compilador de skills, o provider e a trilha — sem importar o legado, que a regra D2 proíbe ao domínio.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .strategy import StrategyKind

#: O catálogo em código não tem versão de contrato. Todas as capabilities de hoje são a versão 1; quando o catálogo
#: virar dado (fase K), mudar o contrato de uma capability sobe este número e a trava das skills (`uses_lock`) passa
#: a enxergar a mudança.
LEGACY_CONTRACT_VERSION = 1


class Risk(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class DefaultPolicy(StrEnum):
    """Mesmo vocabulário de `planning/capabilities.py::POLICIES`. O perfil pode endurecer, nunca afrouxar."""

    autonomous = "autonomous"
    approval_required = "approval_required"
    manual_only = "manual_only"
    disabled = "disabled"


class PostconditionKind(StrEnum):
    """Mesmo vocabulário de `models.Postcondition.kind`."""

    text_visible = "text_visible"
    app_foreground = "app_foreground"
    element_present = "element_present"
    model_judged = "model_judged"
    items_collected = "items_collected"


@dataclass(frozen=True, slots=True)
class CapabilityRef:
    """(app, chave, versão do contrato). `app` é o PACOTE Android, que é a chave do catálogo: o id de app (`instagram`)
    é configuração de cada instalação, e a mesma capability precisa ter a mesma identidade em todas elas."""

    app: str
    key: str
    contract_version: int = LEGACY_CONTRACT_VERSION

    def __str__(self) -> str:
        return f"{self.app}:{self.key}@{self.contract_version}"


@dataclass(frozen=True, slots=True)
class ParameterContract:
    """Os argumentos da capability (os `bindings` da etapa)."""

    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    #: Pelo menos um destes precisa de valor. É a regra da etapa que escreve (`needs_draft`): a intenção
    #: (`content_brief`) ou o texto exato (`content`) — sem nenhum dos dois, o `build_step` recusa.
    one_of: tuple[str, ...] = ()
    #: Opcionais que, sem valor na etapa, vêm da etapa anterior do mesmo plano que declara o argumento (a legenda da
    #: publicação alvo). Subconjunto de `optional`, sem texto a escrever: conferido na carga do catálogo.
    inherited: tuple[str, ...] = ()

    @property
    def accepted(self) -> tuple[str, ...]:
        return self.required + self.optional


@dataclass(frozen=True, slots=True)
class PostconditionContract:
    kind: PostconditionKind
    value: str
    description: str


@dataclass(frozen=True, slots=True)
class SideEffectContract:
    """O efeito externo e como ele é disparado, guardado e desmentido."""

    external: bool = False
    #: QUEM dispara o efeito. Sem isso o commit seria adivinhado pelo texto do elemento (commit falso é irreversível).
    commit_selector: str | None = None
    commit_guard: tuple[str, ...] = ()
    band_guard: tuple[str, ...] = ()
    #: Que interação isto vira no histórico do perfil (`dm_sent`, `followed`…).
    interaction_type: str | None = None
    #: Textos que, visíveis depois do efeito, provam que ele NÃO valeu.
    failure_marks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Governance:
    risk: Risk = Risk.low
    default_policy: DefaultPolicy = DefaultPolicy.autonomous
    limit_bucket: str | None = None
    #: Exige conteúdo gerado (e aprovado, se a política pedir) antes de agir.
    needs_draft: bool = False


@dataclass(frozen=True, slots=True)
class ExecutionContract:
    #: Resolvida por código determinístico, fora do laço da etapa; não é oferecida ao planejador nem a skill.
    internal: bool = False
    timeout_s: int = 180
    #: O do catálogo. Com efeito externo a etapa sai com 1, sempre (P5): quem garante é o `build_step`.
    max_attempts: int = 3
    #: Estratégias que servem a esta capability. Um nó só pode pedir estas.
    strategies: tuple[StrategyKind, ...] = ()


@dataclass(frozen=True, slots=True)
class CollectOutput:
    """A saída da capability. Na v1alpha1 só a coleta produz saída: uma lista de itens lidos da tela (§10.5)."""

    collects: bool = False
    limit: int | None = None
    from_top: bool = True
    rewind: bool = False
    #: Como extrair a CHAVE do item do texto lido (grupo 1 da expressão).
    item_key: str | None = None


@dataclass(frozen=True, slots=True)
class AppRequirements:
    """O que o APP exige de qualquer etapa sua (declarado no registro de apps, `planning/catalog`)."""

    session_provider: str | None = None
    needs_profile: bool = False
    requires_internet: bool = False


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    ref: CapabilityRef
    title: str
    goal: str
    parameters: ParameterContract
    precondition: str | None
    postcondition: PostconditionContract
    #: Atalho POSITIVO de prova (gramática em `taskqueue/proofs.py`). Só o catálogo, que é código revisado, declara.
    local_proof: str | None
    #: Prosa: o que observar depois do efeito para saber se ele valeu. Sem consumidor até um provider lê-la.
    reconciliation: str
    side_effect: SideEffectContract
    governance: Governance
    execution: ExecutionContract
    output: CollectOutput
    requirements: AppRequirements
    #: Textos (modelos com `{argumento}`) que identificam a PUBLICAÇÃO alvo num feed — a legenda. Com o argumento
    #: preenchido, a pós-condição exige o texto na tela, o toque de efeito só vale no cartão que o traz e a prova
    #: local só vale nesse cartão; sem ele, nada muda. Não é do efeito: abrir a publicação também o usa.
    card_guard: tuple[str, ...] = ()
    #: Seletores dos controles do cartão que a etapa toca SEM efeito (o balão de comentários): com a legenda de
    #: `card_guard`, o toque que acerta um deles só vale no cartão dela. Vazio: só o toque de efeito é conferido por
    #: cartão.
    card_control: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # `collect` e `post_kind == items_collected` dizem a mesma coisa em dois campos do catálogo. Divergirem seria
        # uma etapa de coleta que o executor não comprova, ou uma comprovação por itens de etapa que não coleta.
        coleta = self.postcondition.kind is PostconditionKind.items_collected
        if self.output.collects != coleta:
            raise ValueError(f"{self.ref}: `collect` e a pós-condição items_collected precisam andar juntos")
        # Mesma regra do validador do `Plan`: coleta não tem efeito externo.
        if coleta and self.side_effect.external:
            raise ValueError(f"{self.ref}: etapa de coleta não pode ter efeito externo")

    @property
    def key(self) -> str:
        return self.ref.key

    @property
    def app(self) -> str:
        return self.ref.app
