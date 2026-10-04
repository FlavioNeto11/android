"""O catálogo legado (`planning/catalog`) lido como `CapabilityDefinition` (design §8, `CatalogCapabilityRegistry`).

Só lê. O catálogo em código continua sendo a fonte, e o registro de apps (`planning/catalog.register`) continua
sendo o ponto de extensão: um app registrado lá aparece aqui sem nada novo.

O compilador de skills fala em id de app (`instagram`), que é configuração de cada instalação (tabela `apps`); o
catálogo fala em pacote (`com.instagram.android`). Quem converte um no outro é injetado (`pacote_do_app`), porque a
fonte dessa tabela é de outro contexto (applications) e a composição é que liga os dois.
"""
from __future__ import annotations

from collections.abc import Callable

from app.planning import catalog as registro_de_apps
from app.planning.capabilities import BRIEFING, TEXTO, Capability, CapabilityCatalog

from ..domain.definition import (LEGACY_CONTRACT_VERSION, AppRequirements, CapabilityDefinition, CapabilityRef,
                                 CollectOutput, DefaultPolicy, ExecutionContract, Governance, ParameterContract,
                                 PostconditionContract, PostconditionKind, Risk, SideEffectContract)
from ..domain.strategy import STRATEGIES_OF_A_NODE, StrategyKind

#: Cada campo de `Capability` e onde ele mora na `CapabilityDefinition`. É a declaração do mapeamento 1:1: o teste
#: `test_capabilities_do_dominio.py` confere, para as capabilities do catálogo, que o valor chega inteiro ao destino, e
#: reprova quando o catálogo ganha um campo que não está aqui.
CAMPOS: dict[str, str] = {
    "key": "ref.key",
    "title": "title",
    "goal": "goal",
    "post_kind": "postcondition.kind",
    "post_value": "postcondition.value",
    "post_description": "postcondition.description",
    "bindings": "parameters.required",
    "optional_bindings": "parameters.optional",
    "inherited_bindings": "parameters.inherited",
    "precondition": "precondition",
    "side_effect": "side_effect.external",
    "risk": "governance.risk",
    "commit_selector": "side_effect.commit_selector",
    "commit_guard": "side_effect.commit_guard",
    "commit_switch": "side_effect.commit_switch",
    "commit_switch_mark": "side_effect.commit_switch_mark",
    "band_guard": "side_effect.band_guard",
    "card_guard": "card_guard",
    "card_control": "card_control",
    "reconciliation": "reconciliation",
    "default_policy": "governance.default_policy",
    "limit_bucket": "governance.limit_bucket",
    "counterparty": "governance.counterparty",
    "objeto_alvo": "governance.objeto_alvo",
    "needs_draft": "governance.needs_draft",
    "interaction_type": "side_effect.interaction_type",
    "internal": "execution.internal",
    "preparo": "execution.preparo",
    "timeout_s": "execution.timeout_s",
    "max_attempts": "execution.max_attempts",
    "collect": "output.collects",
    "collect_limit": "output.limit",
    "collect_from_top": "output.from_top",
    "collect_rewind": "output.rewind",
    "item_key": "output.item_key",
    "saidas": "output.values",
    "saidas_relacao": "output.relations",
    "failure_marks": "side_effect.failure_marks",
    "pending_marks": "side_effect.pending_marks",
    "local_proof": "local_proof",
}

#: Campos mapeados que nenhum código do backend lê hoje (medido por busca em 5c1565a). Ficam sinalizados para não
#: passarem por contrato vivo: quem começar a consumi-los tira daqui no mesmo commit.
SEM_CONSUMIDOR: dict[str, str] = {
    "reconciliation": "prosa para gente; nenhum código a lê até o provider reconciliar (fase G/H)",
    "collect": "redundante com post_kind == items_collected, que é o que o executor e o validador do Plan olham",
}


def definicao(cap: Capability, package: str) -> CapabilityDefinition:
    """`Capability` do catálogo → `CapabilityDefinition`. Os requisitos vêm do registro do app, não da capability."""
    app = registro_de_apps.capabilities_of(package)
    # Capability interna é resolvida por código (login, leitura da conta), fora do laço da etapa: a única
    # estratégia que a serve é a determinística. As demais servem-se das estratégias de nó da v1alpha1.
    estrategias: tuple[StrategyKind, ...] = ((StrategyKind.deterministic,) if cap.internal
                                             else STRATEGIES_OF_A_NODE)
    return CapabilityDefinition(
        ref=CapabilityRef(app=package, key=cap.key, contract_version=LEGACY_CONTRACT_VERSION),
        title=cap.title,
        goal=cap.goal,
        parameters=ParameterContract(required=tuple(cap.bindings), optional=tuple(cap.optional_bindings),
                                     # a mesma regra que o `build_step` aplica a etapa que escreve
                                     one_of=(BRIEFING, TEXTO) if cap.needs_draft else (),
                                     inherited=tuple(cap.inherited_bindings)),
        precondition=cap.precondition,
        postcondition=PostconditionContract(kind=PostconditionKind(cap.post_kind), value=cap.post_value,
                                            description=cap.post_description),
        local_proof=cap.local_proof,
        reconciliation=cap.reconciliation,
        side_effect=SideEffectContract(external=cap.side_effect, commit_selector=cap.commit_selector,
                                       commit_guard=tuple(cap.commit_guard), band_guard=tuple(cap.band_guard),
                                       commit_switch=tuple(cap.commit_switch),
                                       commit_switch_mark=tuple(cap.commit_switch_mark),
                                       interaction_type=cap.interaction_type,
                                       failure_marks=tuple(cap.failure_marks),
                                       pending_marks=tuple(cap.pending_marks)),
        governance=Governance(risk=Risk(cap.risk), default_policy=DefaultPolicy(cap.default_policy),
                              limit_bucket=cap.limit_bucket, counterparty=cap.counterparty,
                              objeto_alvo=tuple(cap.objeto_alvo),
                              needs_draft=cap.needs_draft),
        execution=ExecutionContract(internal=cap.internal, timeout_s=cap.timeout_s, max_attempts=cap.max_attempts,
                                    strategies=estrategias, preparo=tuple(cap.preparo)),
        output=CollectOutput(collects=cap.collect, limit=cap.collect_limit, from_top=cap.collect_from_top,
                             rewind=cap.collect_rewind, item_key=cap.item_key,
                             values=tuple(cap.saidas), relations=tuple(cap.saidas_relacao)),
        requirements=AppRequirements(session_provider=app.session_provider, needs_profile=app.needs_profile,
                                     requires_internet=app.requires_internet),
        card_guard=tuple(cap.card_guard),
        card_control=tuple(cap.card_control),
    )


class CatalogCapabilityRegistry:
    """Leitura do catálogo por id de app (o que a skill escreve) ou por `CapabilityRef` (o que a etapa carrega).

    Cumpre, por estrutura, a porta `CapabilityLookup` do compilador de skills (`modules/skills/domain/compiler.py`)
    e a `CapabilityCatalogPort` da §7. Não guarda nada: o registro de apps pode ser trocado em teste
    (`register`/`unregister`), e montar 23 valores pequenos é mais barato que invalidar um cache.
    """

    def __init__(self, pacote_do_app: Callable[[str], str | None]) -> None:
        self._pacote_do_app = pacote_do_app

    def package_of(self, app_id: str) -> str | None:
        return self._pacote_do_app(app_id) or None

    def app_known(self, app_id: str) -> bool:
        return self.package_of(app_id) is not None

    def _catalogo(self, app_id: str) -> tuple[str, CapabilityCatalog] | None:
        pacote = self.package_of(app_id)
        catalogo = registro_de_apps.get(pacote) if pacote else None
        return (pacote, catalogo) if pacote and catalogo is not None else None

    def has_catalog(self, app_id: str) -> bool:
        return self._catalogo(app_id) is not None

    def definition(self, app_id: str, key: str) -> CapabilityDefinition | None:
        achado = self._catalogo(app_id)
        if achado is None or not achado[1].has(key):
            return None
        pacote, catalogo = achado
        return definicao(catalogo.get(key), pacote)

    def offered(self, app_id: str) -> list[CapabilityDefinition]:
        """O que uma skill (ou o planejador) pode usar: as internas ficam de fora, como em `CapabilityCatalog.offered`."""
        achado = self._catalogo(app_id)
        if achado is None:
            return []
        pacote, catalogo = achado
        return [definicao(c, pacote) for c in catalogo.offered]

    def by_ref(self, ref: CapabilityRef) -> CapabilityDefinition | None:
        """A definição de uma etapa em execução. Versão de contrato diferente da atual é `None`: o contrato mudou
        e ninguém deve verificar uma etapa pelo contrato de outra."""
        catalogo = registro_de_apps.get(ref.app)
        if catalogo is None or not catalogo.has(ref.key) or ref.contract_version != LEGACY_CONTRACT_VERSION:
            return None
        return definicao(catalogo.get(ref.key), ref.app)
