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

# Bindings do texto de uma etapa que escreve. `content` é o texto pronto; `content_brief` é a INTENÇÃO, e é o
# padrão — de um briefing cada perfil escreve a sua versão, na própria voz. `content_verbatim` é o pedido
# explícito de "estas palavras exatas, iguais para todos".
TEXTO = "content"
BRIEFING = "content_brief"
VERBATIM = "content_verbatim"
_SIM = ("true", "1", "sim", "yes", "verdadeiro")


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
    # Desfaz a rolagem da coleta no fim. Para lista onde ir ao topo e perigoso (a folha de comentarios FECHA
    # se o arrasto passar do topo), mas cujos alvos sao lidos de cima para baixo.
    collect_rewind: bool = False
    # Como extrair, do texto lido na tela, a CHAVE que identifica o item. O que a lista mostra costuma ser uma frase
    # de acessibilidade ("fulano said oi"), e é essa frase que vira `{item}` e, daí, o `{username}` das etapas do
    # bloco. Sem recortar a chave, as guardas passam a exigir a frase inteira na tela e nunca casam. Grupo 1 = chave.
    item_key: str | None = None
    # Textos que, se aparecerem na tela DEPOIS do efeito, provam que ele NÃO valeu (ex.: "Not delivered").
    # Ficam aqui, e não no executor, porque são específicos do app e da versão — como `commit_selector`.
    failure_marks: tuple[str, ...] = ()
    # Prova local (sem modelo) para uma pós-condição `model_judged`, quando existe uma conferência determinística
    # confiável pela árvore. "sent_text": o `content` da etapa apareceu num elemento não-editável (mensagem já no
    # fio) e não sobra no campo de escrita — ver `UiTree.sent_as_message` (achado #102). `None` = sempre julgar
    # pelo modelo, como antes.
    local_proof: str | None = None

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
        valores = {k: v for k, v in node.bindings.items() if v is not None}
        faltando = [b for b in cap.bindings if not (valores.get(b) or "").strip()]
        if cap.needs_draft and not (valores.get(BRIEFING) or "").strip() and not (valores.get(TEXTO) or "").strip():
            # Etapa que escreve precisa de UM dos dois: a intenção (para cada perfil escrever a sua) ou o texto
            # exato. Nenhum dos dois é informação faltando de verdade, e vira pergunta — não plano torto.
            faltando.append(f"{BRIEFING} ou {TEXTO}")
        if faltando:
            raise MissingBinding(f"{cap.key} exige {', '.join(faltando)}")
        # O texto só fica congelado no plano quando o comando pediu as MESMAS palavras para todo mundo. Fora isso,
        # ele nasce por perfil, na hora, com a persona de cada um — e as guardas que dependem dele só podem ser
        # montadas quando esse texto existir (ver `state._draft_gate`). Congelar aqui era o que fazia oito contas
        # publicarem, byte a byte, a mesma frase.
        texto_fixo = bool((valores.get(TEXTO) or "").strip()) and (not cap.needs_draft or _e_verbatim(valores))
        # `{item}` só é resolvido na expansão do for_each; aqui ele segue como variável, de propósito.
        preencher = (lambda texto: _aplicar(texto, valores))
        guardas = (lambda brutas: [preencher(g) for g in brutas
                                   if texto_fixo or TEXTO not in _variaveis(g)])
        return PlanStep(
            key=node.key, title=preencher(cap.title), goal=preencher(cap.goal),
            depends_on=list(node.depends_on), side_effect=cap.side_effect,
            commit_guard=guardas(cap.commit_guard),
            precondition=preencher(cap.precondition) if cap.precondition else None,
            postcondition=Postcondition(kind=cap.post_kind, value=preencher(cap.post_value),  # type: ignore[arg-type]
                                        description=preencher(cap.post_description)),
            timeout_s=cap.timeout_s, max_attempts=1 if cap.side_effect else cap.max_attempts,
            capability=cap.key, commit_selector=cap.commit_selector,
            band_guard=guardas(cap.band_guard), bindings=dict(valores),
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


def _variaveis(texto: str) -> set[str]:
    return set(_VARIAVEL.findall(texto or ""))


def _e_verbatim(valores: dict[str, str]) -> bool:
    return str(valores.get(VERBATIM) or "").strip().lower() in _SIM


def texto_a_gerar(bindings: dict[str, Any] | None) -> str | None:
    """O briefing desta etapa, quando o texto ainda precisa ser escrito por perfil; `None` quando já está fechado.

    Regra (decisão do usuário): briefing é o padrão. Um `content` que veio do comando SEM `content_verbatim` é
    tratado como intenção, não como as palavras finais — foi exatamente assim que "o texto pode ser X" virou a
    mesma frase em oito contas.
    """
    valores = {k: v for k, v in (bindings or {}).items() if v is not None}
    if _e_verbatim(valores):
        return None
    briefing = str(valores.get(BRIEFING) or "").strip()
    return briefing or str(valores.get(TEXTO) or "").strip() or None


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
