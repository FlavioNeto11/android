"""O livro de aprendizado (ADR-054, decisão 3): uma LEITURA e um CICLO DE VIDA únicos sobre fontes que continuam
donas do próprio conteúdo.

Não é uma VIEW SQL, por três motivos: os placeholders de dialeto; a cópia entre bancos, que ordena por FK; e a regra
"active ≡ published", que merece teste. O mapeamento de estado das fontes existentes mora aqui, puro:

| fonte | nativo → livro |
|---|---|
| receita (`recipes.status`) | active→published, candidate→candidate, validated→validated, quarantined→disabled, superseded→deprecated |
| fluxo (`flows.status`) | active→published, candidate→candidate, validated→validated, disabled→disabled |
| habilidade (`skill_versions.state`) | o próprio `SkillState` (o ciclo dela é mais estrito que o D1 e fica com ela) |
| memória da persona | só a CONTAGEM por perfil; o conteúdo nunca sai no livro nem no relatório |

Os tipos sem casa nativa (tela, lição, voz, preferência) moram em `learning_items` e são descritos por
`ItemDeAprendizado`; o conteúdo é JSON canônico e o `content_hash` segue a regra de `skill_versions`.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.modules.learning.domain.ciclo import SkillState, exige_o_dono
from app.modules.learning.domain.vocabulario import (FONTES_HUMANAS, KINDS_DE_ITEM, LivroKind, Origem, SourceKind)
from app.modules.skills.domain.document import JsonObject, JsonValue, content_hash

_S = SkillState

ESTADO_DA_RECEITA: Mapping[str, SkillState] = {
    "active": _S.PUBLISHED, "candidate": _S.CANDIDATE, "validated": _S.VALIDATED, "quarantined": _S.DISABLED,
    "superseded": _S.DEPRECATED,
}
ESTADO_DO_FLUXO: Mapping[str, SkillState] = {
    "active": _S.PUBLISHED, "candidate": _S.CANDIDATE, "validated": _S.VALIDATED, "disabled": _S.DISABLED,
}
#: O caminho de volta. Fluxo não tem aposentadoria (`deprecated`): o que sai de circulação fica `disabled`.
STATUS_DA_RECEITA: Mapping[SkillState, str] = {v: k for k, v in ESTADO_DA_RECEITA.items()}
STATUS_DO_FLUXO: Mapping[SkillState, str] = {v: k for k, v in ESTADO_DO_FLUXO.items()}


def estado_nativo(kind: LivroKind, status: str) -> SkillState | None:
    """O estado do livro de um status nativo. Desconhecido é `None` (aparece como está, nunca como publicado)."""
    if kind is LivroKind.RECEITA:
        return ESTADO_DA_RECEITA.get(status)
    if kind is LivroKind.FLUXO:
        return ESTADO_DO_FLUXO.get(status)
    if kind is LivroKind.HABILIDADE:
        return SkillState(status) if status in {s.value for s in SkillState} else None
    return None


def status_nativo(kind: LivroKind, estado: SkillState) -> str | None:
    """O status nativo de um estado do livro, ou `None` quando a fonte não tem equivalente."""
    if kind is LivroKind.RECEITA:
        return STATUS_DA_RECEITA.get(estado)
    if kind is LivroKind.FLUXO:
        return STATUS_DO_FLUXO.get(estado)
    return None


def ref_da_trilha(kind: LivroKind, ref: str) -> str:
    """A chave de `learning_transitions.item_ref`: `li-…` para os itens, `<tipo>:<id>` para as fontes nativas."""
    return ref if kind in KINDS_DE_ITEM else f"{kind.value}:{ref}"


# ------------------------------------------------------------------ efeito externo das fontes nativas
def _lista(valor: JsonValue) -> list[JsonValue]:
    return valor if isinstance(valor, list) else []


def receita_tem_efeito(acoes: JsonValue) -> bool:
    """Receita com ação `commit` (efeito externo). Em 21 das 22 medidas em 28/09 o commit é a primeira ação."""
    return any(isinstance(a, dict) and a.get("commit") is True for a in _lista(acoes))


def fluxo_tem_efeito(plano: JsonValue) -> bool:
    passos = plano.get("steps") if isinstance(plano, dict) else None
    return any(isinstance(p, dict) and p.get("side_effect") is True for p in _lista(passos))


def hash_da_receita(acoes: JsonValue) -> str:
    """O conteúdo da receita para o veto: o CAMINHO, sem o `why` da IA (que muda a cada execução)."""
    return content_hash([{k: v for k, v in a.items() if k != "why"} if isinstance(a, dict) else a
                         for a in _lista(acoes)])


def escopo_da_receita(pacote: str, versao: str, assinatura: str, variante: str, step_hash: str) -> str:
    return "|".join(("receita", pacote, versao, assinatura, variante, step_hash))


def escopo_do_fluxo(match_key: str) -> str:
    return f"fluxo|{match_key}"


# ------------------------------------------------------------------ itens do livro (learning_items)
@dataclass(frozen=True, slots=True)
class Escopo:
    """'' = qualquer. `capability='*'` é a etapa livre."""

    app: str = ""
    capability: str = ""
    step_hash: str = ""
    role: str = ""
    profile_id: str = ""

    def chave(self, kind: LivroKind) -> str:
        return "|".join((kind.value, self.app, self.capability, self.step_hash, self.role, self.profile_id))


@dataclass(frozen=True, slots=True)
class NovoItem:
    """O que um minerador (ou uma pessoa) propõe. Os bits do D1 são calculados aqui, não recebidos de rota."""

    kind: LivroKind
    escopo: Escopo
    content: JsonObject
    summary: str
    source_kind: SourceKind
    side_effect: bool
    app_version: str | None = None
    provenance: JsonObject = field(default_factory=dict)
    parent_id: str | None = None
    tokens: int | None = None

    @property
    def human_origin(self) -> bool:
        return self.source_kind in FONTES_HUMANAS or self.kind is LivroKind.VOZ

    @property
    def content_hash(self) -> str:
        return content_hash(self.content)


@dataclass(frozen=True, slots=True)
class ItemDeAprendizado:
    id: str
    kind: LivroKind
    state: SkillState
    state_detail: str | None
    escopo: Escopo
    app_version: str | None
    side_effect: bool
    human_origin: bool
    content: JsonObject
    content_hash: str
    summary: str
    tokens: int | None
    source_kind: SourceKind
    provenance: JsonObject
    evidence_for: int
    evidence_against: int
    distinct_runs: int
    distinct_devices: int
    parent_id: str | None
    created_by: str
    created_at: str
    updated_at: str | None
    state_at: str | None
    state_by: str | None
    last_used_at: str | None

    @property
    def requires_owner(self) -> bool:
        return exige_o_dono(self.side_effect, self.human_origin)


@dataclass(frozen=True, slots=True)
class Transicao:
    """Uma linha de `learning_transitions`."""

    id: int
    item_ref: str
    item_kind: LivroKind
    content_hash: str | None
    scope_key: str
    app_version: str | None
    from_state: SkillState | None
    to_state: SkillState
    reason: str
    decided_by: str
    decided_at: str
    run_id: str | None


# ------------------------------------------------------------------ a entrada do livro (a união)
_ORIGEM_DO_ITEM: Mapping[SourceKind, Origem] = {
    SourceKind.RECOVERY: Origem.EXECUCAO, SourceKind.PLAN_DEFECT: Origem.EXECUCAO,
    SourceKind.SCREEN_OBSERVATION: Origem.SISTEMA, SourceKind.SESSION_UNKNOWN: Origem.SISTEMA,
    SourceKind.APPROVAL_EDIT: Origem.PESSOA, SourceKind.ANSWER: Origem.PESSOA,
    SourceKind.DISAMBIGUATION: Origem.PESSOA, SourceKind.FEEDBACK_NOTE: Origem.PESSOA, SourceKind.MANUAL: Origem.PESSOA,
}


@dataclass(frozen=True, slots=True)
class EntradaDoLivro:
    """Uma linha do livro, venha de onde vier. `ref` é o id na fonte (receita: o número; habilidade: `id@versão`;
    memória: o perfil; item: `li-…`)."""

    kind: LivroKind
    ref: str
    state: SkillState | None
    native_status: str | None
    title: str
    app: str | None
    origin: Origem
    side_effect: bool = False
    human_origin: bool = False
    created_at: str | None = None
    state_at: str | None = None
    last_used_at: str | None = None
    uses: int | None = None
    a_favor: int = 0
    contra: int = 0
    count: int | None = None
    detail: str | None = None
    content_hash: str | None = None
    scope_key: str = ""
    app_version: str | None = None
    #: O `app_id` CRU do fluxo ou da habilidade quando ele não casou com nenhum pacote (`app` = `APP_NAO_RESOLVIDO`):
    #: o dono vê o que não resolveu. `None` em tudo que resolveu e nos tipos que não têm eixo de app.
    app_ref: str | None = None

    @property
    def requires_owner(self) -> bool:
        """Habilidade publica só por pessoa (o ciclo dela é mais estrito que o D1); o resto segue o D1."""
        return self.kind is LivroKind.HABILIDADE or exige_o_dono(self.side_effect, self.human_origin)

    @property
    def trail_ref(self) -> str:
        return ref_da_trilha(self.kind, self.ref)


def entrada_do_item(item: ItemDeAprendizado) -> EntradaDoLivro:
    return EntradaDoLivro(
        kind=item.kind, ref=item.id, state=item.state, native_status=None, title=item.summary,
        app=item.escopo.app or None, origin=_ORIGEM_DO_ITEM.get(item.source_kind, Origem.SISTEMA),
        side_effect=item.side_effect, human_origin=item.human_origin, created_at=item.created_at,
        state_at=item.state_at, last_used_at=item.last_used_at, a_favor=item.evidence_for,
        contra=item.evidence_against, detail=item.state_detail, content_hash=item.content_hash,
        scope_key=item.escopo.chave(item.kind), app_version=item.app_version)


def para_aprovar(e: EntradaDoLivro) -> bool:
    """A fila do D1 ("Para aprovar"): validado que só o dono publica, e candidata com texto de pessoa (a nota vira
    lição só depois que a pessoa a valida)."""
    if e.kind is LivroKind.MEMORIA or e.state is None:
        return False
    if e.state is SkillState.VALIDATED and e.requires_owner:
        return True
    return e.state is SkillState.CANDIDATE and e.human_origin and e.kind in KINDS_DE_ITEM


def a_revisar(e: EntradaDoLivro, decididos_por_pessoa: frozenset[str]) -> bool:
    """"Revisar": receita ou fluxo ATIVO com efeito externo que nenhuma pessoa decidiu pelo livro — o legado de
    antes do D1, que continua valendo (desvio consciente, ADR-054) até o dono aprovar ou rebaixar."""
    return (e.kind in (LivroKind.RECEITA, LivroKind.FLUXO) and e.state is SkillState.PUBLISHED and e.side_effect
            and e.trail_ref not in decididos_por_pessoa)


def contagem(entradas: Iterable[EntradaDoLivro]) -> dict[str, dict[str, int]]:
    """{tipo: {estado: n}}; memória conta lembranças, não linhas."""
    saida: dict[str, dict[str, int]] = {}
    for e in entradas:
        por_estado = saida.setdefault(e.kind.value, {})
        chave = e.state.value if e.state is not None else "-"
        por_estado[chave] = por_estado.get(chave, 0) + (e.count if e.count is not None else 1)
    return saida
