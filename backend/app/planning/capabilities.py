"""Catálogo de capabilities: o que o sistema sabe fazer em um aplicativo, descrito uma vez e no backend.

Por que existe: hoje o planejador devolve a etapa inteira (título, objetivo, pós-condição, guardas). Quanto mais
rico o esquema, maior a chance de o modelo devolver algo que não valida — e um 400 no planejamento derruba a
execução sem recuperação. Com catálogo, o modelo devolve só `{key, capability, depends_on, bindings, for_each}`;
quem monta a etapa é este módulo, com texto revisado por gente.

O caminho livre continua valendo: app sem catálogo planeja como sempre planejou. É o que mantém o QA Messenger
intacto enquanto o Instagram ganha vocabulário próprio.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..models import MissingInfo, PlanStep, Postcondition

# Política padrão por capability. O perfil pode endurecer (nunca afrouxar em silêncio) em `automation_policy`.
POLICIES = ("autonomous", "approval_required", "manual_only", "disabled")

_VARIAVEL = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


@dataclass(frozen=True, slots=True)
class Capability:
    """Uma ação nomeada, com pré-condição, pós-condição, efeito, risco e reconciliação — como o §13 pede."""

    key: str
    title: str
    goal: str
    post_kind: str
    post_value: str
    post_description: str
    bindings: tuple[str, ...] = ()              # variáveis obrigatórias (o planejador precisa preencher)
    optional_bindings: tuple[str, ...] = ()
    precondition: str | None = None
    side_effect: bool = False
    risk: str = "low"                           # low | medium | high — orienta a política padrão
    commit_selector: str | None = None          # QUEM dispara o efeito; sem isso o commit é adivinhado por verbo
    commit_guard: tuple[str, ...] = ()
    # Guardas que precisam estar na MESMA faixa vertical do alvo. É o que distingue a linha certa numa lista:
    # numa lista de pedidos, "@ana" em qualquer lugar da tela não prova que o botão tocado é o dela.
    band_guard: tuple[str, ...] = ()
    reconciliation: str = ""                    # o que observar depois do efeito para saber se ele valeu
    default_policy: str = "autonomous"
    limit_bucket: str | None = None             # likes | comments | follows | dms — chave do limite por hora
    needs_draft: bool = False                   # exige conteúdo gerado (e aprovado, se a política pedir) antes
    interaction_type: str | None = None         # que interação isto vira no histórico do perfil (dm_sent, followed…)
    internal: bool = False                      # resolvida por código determinístico; não é oferecida ao planejador
    timeout_s: int = 180
    max_attempts: int = 3
    collect: bool = False                       # etapa de coleta (pós-condição items_collected)
    # Coleta: teto de itens e se a lista deve voltar ao topo antes de ler. No feed, voltar ao topo é "puxar para
    # atualizar" — muda o conteúdo em vez de reposicionar. Estes ajustes vivem no CONTEXTO da ferramenta, nunca
    # nos argumentos, senão a receita aprendida deixaria de casar.
    collect_limit: int | None = None
    collect_from_top: bool = True

    def describe(self) -> str:
        """Linha que vai ao planejador. Curta de propósito: o prompt cresce com o catálogo."""
        argumentos = ", ".join(self.bindings + tuple(f"{b}?" for b in self.optional_bindings)) or "sem argumentos"
        efeito = " [EFEITO EXTERNO]" if self.side_effect else ""
        return f"- {self.key}({argumentos}){efeito}: {self.title}"


class UnknownCapability(LookupError):
    """Capability que não existe no catálogo. Vira PERGUNTA ao usuário, nunca morte da execução."""


class MissingBinding(ValueError):
    pass


@dataclass(slots=True)
class CapabilityNode:
    """O que o planejador devolve por etapa. Deliberadamente pequeno."""

    key: str
    capability: str
    depends_on: list[str] = field(default_factory=list)
    bindings: dict[str, str] = field(default_factory=dict)
    for_each: str | None = None


class CapabilityCatalog:
    def __init__(self, package: str, capabilities: list[Capability]):
        self.package = package
        self._por_chave = {c.key: c for c in capabilities}

    def get(self, key: str) -> Capability:
        cap = self._por_chave.get((key or "").strip().upper())
        if cap is None:
            raise UnknownCapability(key)
        return cap

    def has(self, key: str) -> bool:
        return (key or "").strip().upper() in self._por_chave

    @property
    def offered(self) -> list[Capability]:
        """O que o planejador pode usar. O que é resolvido por código (login, verificação) fica de fora."""
        return [c for c in self._por_chave.values() if not c.internal]

    def prompt_block(self) -> str:
        return "\n".join(c.describe() for c in self.offered)

    # ------------------------------------------------------------------ composição
    def build_step(self, node: CapabilityNode) -> PlanStep:
        """Monta a etapa a partir do catálogo. O texto é do backend; do modelo vêm só os argumentos."""
        cap = self.get(node.capability)
        faltando = [b for b in cap.bindings if not (node.bindings.get(b) or "").strip()]
        if faltando:
            raise MissingBinding(f"{cap.key} exige {', '.join(faltando)}")
        valores = {k: v for k, v in node.bindings.items() if v is not None}
        # `{item}` só é resolvido na expansão do for_each; aqui ele segue como variável, de propósito.
        preencher = (lambda texto: _aplicar(texto, valores))
        return PlanStep(
            key=node.key, title=preencher(cap.title), goal=preencher(cap.goal),
            depends_on=list(node.depends_on), side_effect=cap.side_effect,
            commit_guard=[preencher(g) for g in cap.commit_guard],
            precondition=preencher(cap.precondition) if cap.precondition else None,
            postcondition=Postcondition(kind=cap.post_kind, value=preencher(cap.post_value),  # type: ignore[arg-type]
                                        description=preencher(cap.post_description)),
            timeout_s=cap.timeout_s, max_attempts=1 if cap.side_effect else cap.max_attempts,
            capability=cap.key, commit_selector=cap.commit_selector,
            band_guard=[preencher(g) for g in cap.band_guard], bindings=dict(valores),
            for_each=node.for_each, variables={})


def compose(catalog: CapabilityCatalog, nodes: list[CapabilityNode]) -> tuple[list[PlanStep], list[MissingInfo]]:
    """Monta o plano a partir das ações escolhidas.

    Ação desconhecida ou argumento faltando NÃO derruba a execução: vira pergunta ao usuário, que é o que o motor
    já sabe tratar (`needs_input`). Um plano meio montado seria pior que nenhum.
    """
    steps: list[PlanStep] = []
    missing: list[MissingInfo] = []
    for node in nodes:
        try:
            steps.append(catalog.build_step(node))
        except UnknownCapability:
            disponiveis = ", ".join(c.key for c in catalog.offered)
            missing.append(MissingInfo(
                field="capability",
                question=(f"A ação '{node.capability}' não existe para este aplicativo. "
                          f"As disponíveis são: {disponiveis}. Como devo fazer isso?")))
        except MissingBinding as exc:
            missing.append(MissingInfo(field=node.capability.lower(),
                                       question=f"Falta informação para a etapa '{node.key}': {exc}."))
    return steps, missing


def _aplicar(texto: str, valores: dict[str, str]) -> str:
    """Substitui só as variáveis conhecidas; `{item}`, `{instance_id}` e afins continuam para o executor resolver."""
    return _VARIAVEL.sub(lambda m: valores.get(m.group(1), m.group(0)), texto or "")


def capability_of(package: str | None, key: str | None) -> Capability | None:
    """A capability desta etapa, quando o app tem catálogo. Fora disso, `None` — e tudo segue como antes."""
    catalog = load_catalog(package)
    if catalog is None or not key or not catalog.has(key):
        return None
    return catalog.get(key)


def load_catalog(package: str | None) -> CapabilityCatalog | None:
    """Catálogo do app, quando existe. Sem catálogo, o planejamento livre continua valendo — sem exceção nenhuma."""
    if not package:
        return None
    if package == "com.instagram.android":
        from .catalog.instagram import INSTAGRAM_CATALOG

        return INSTAGRAM_CATALOG
    return None
