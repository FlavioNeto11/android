"""As relações derivadas de um item do Livro (30.7, `docs/design/aprendizado-vivo.md` §6): o que ele substitui, por
quem foi substituído, de que foi derivado, o que ele reaprende depois de uma evidência inválida (30.23), em que regra
declarada foi absorvido e com quem se contradiz.

SEM tabela de arestas: cada relação sai de um dado que já existe (versão vizinha da receita, `parent_version`,
`parent_id`, o prefixo `absorvida:`, o mesmo `scope_key` com outro `content_hash`) e carrega a `fonte` de onde foi lida,
para a pessoa conferir. Puro: recebe linhas já lidas e devolve JSON; quem lê o banco é o serviço e `infrastructure/fontes.py`.

O que o §6 marca como NOVO sem fonte hoje (`complementa / depende de`, `revisado por` da IA) e o que não tem alvo
nomeável (a evidência `conflict` de uma tela aponta para um aparelho, não para outro item) NÃO sai aqui: relação
inventada é pior que relação ausente.

Forma de cada relação: `{tipo, kind, ref, rotulo, fonte}`. `kind`/`ref` apontam para o detalhe do alvo
(`GET /api/aprendizado/{kind}/{ref}`), salvo a absorvida, cujo alvo é uma regra declarada (`kind` `regra_declarada`, `ref`
o nome da regra) ou só o commit (`kind` `commit`).
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.livro import EntradaDoLivro
from app.modules.learning.domain.vocabulario import LivroKind, absorvida_em
from app.modules.skills.domain.document import JsonObject, JsonValue

#: O prefixo do `skill_id` da habilidade que embrulha um fluxo legado (`flow:<flows.id>@1`, `skills/domain/refs.py`).
PREFIXO_DE_FLUXO_LEGADO = "flow:"
#: "Vivo" é o que ainda pode ser usado ou decidido; `deprecated` e `disabled` já saíram de circulação e não disputam.
VIVOS = frozenset({SkillState.CANDIDATE, SkillState.VALIDATED, SkillState.PUBLISHED})
KIND_DA_REGRA_DECLARADA = "regra_declarada"
KIND_DO_COMMIT = "commit"


class TipoDeRelacao(StrEnum):
    SUBSTITUI = "substitui"
    SUBSTITUIDA_POR = "substituida_por"
    DERIVADO_DE = "derivado_de"
    REAPRENDE = "reaprende"
    REAPRENDIDA_POR = "reaprendida_por"
    ABSORVIDA = "absorvida"
    CONTRADIZ = "contradiz"


#: A ordem em que o detalhe lista as relações (a de linhagem primeiro, o alerta de contradição por último).
_ORDEM = {t: i for i, t in enumerate(TipoDeRelacao)}


@dataclass(frozen=True, slots=True)
class Parente:
    """Uma entrada do livro candidata a contradizer outra. `tema`: o que o `scope_key` sozinho não separa.

    O `scope_key` da receita é a chave exata da etapa (`tema` ""); o da tela é só o app e o da lição não nomeia a
    proposição, então `tema` leva o nome da regra de tela e as demais ficam `None` (sem critério seguro: não se
    deriva contradição, em vez de acusar toda lição do mesmo passo). Fluxo (`match_key` único) e habilidade (versões
    da mesma habilidade dividem o comando) nem chegam aqui."""

    entrada: EntradaDoLivro
    tema: str | None


def relacao(tipo: TipoDeRelacao, kind: str, ref: str, *, fonte: str, rotulo: str | None = None) -> JsonObject:
    return {"tipo": tipo.value, "kind": kind, "ref": ref, "rotulo": rotulo, "fonte": fonte}


def _vizinha(valor: JsonValue) -> tuple[str, int, str] | None:
    if not isinstance(valor, dict):
        return None
    ident, versao, estado = valor.get("id"), valor.get("versao"), valor.get("estado")
    if isinstance(ident, int) and isinstance(versao, int) and isinstance(estado, str):
        return str(ident), versao, estado
    return None


# ------------------------------------------------------------------ receita
def da_receita(conteudo: JsonObject | None) -> list[JsonObject]:
    """A versão anterior e a seguinte da MESMA chave: `substitui` e `substituida_por`. O `conteudo` do 30.3 já as leu
    (`recipes` com a mesma chave e `version` menor ou maior); aqui só viram relação, sem repetir o SQL."""
    if conteudo is None:
        return []
    saida: list[JsonObject] = []
    for campo, tipo in (("substitui", TipoDeRelacao.SUBSTITUI), ("substituida_por", TipoDeRelacao.SUBSTITUIDA_POR)):
        vizinha = _vizinha(conteudo.get(campo))
        if vizinha is not None:
            ref, versao, estado = vizinha
            saida.append(relacao(tipo, LivroKind.RECEITA.value, ref, rotulo=f"v{versao} ({estado})",
                                 fonte="recipes: mesma chave, versão vizinha"))
    return saida


# ------------------------------------------------------------------ habilidade
@dataclass(frozen=True, slots=True)
class Sucessora:
    """A versão de habilidade editada a partir desta (`skill_versions.parent_version` apontando para ela)."""

    ref: str
    versao: int
    estado: str


def da_habilidade(conteudo: JsonObject | None, sucessoras: Iterable[Sucessora]) -> list[JsonObject]:
    """`parent_version` → `substitui` (a versão de que esta foi editada); as versões que a têm por pai →
    `substituida_por`; o `skill_id` `flow:<id>` (o fluxo legado, sempre a versão 1) → `derivado_de` o fluxo."""
    if conteudo is None:
        return []
    saida: list[JsonObject] = []
    skill_id, pai = conteudo.get("skill_id"), conteudo.get("parent_version")
    if isinstance(skill_id, str) and isinstance(pai, int) and not isinstance(pai, bool):
        saida.append(relacao(TipoDeRelacao.SUBSTITUI, LivroKind.HABILIDADE.value, f"{skill_id}@{pai}",
                             rotulo=f"v{pai}", fonte="skill_versions.parent_version"))
    for s in sucessoras:
        saida.append(relacao(TipoDeRelacao.SUBSTITUIDA_POR, LivroKind.HABILIDADE.value, s.ref,
                             rotulo=f"v{s.versao} ({s.estado})", fonte="skill_versions.parent_version"))
    if isinstance(skill_id, str) and skill_id.startswith(PREFIXO_DE_FLUXO_LEGADO):
        fluxo = skill_id[len(PREFIXO_DE_FLUXO_LEGADO):]
        if fluxo:
            saida.append(relacao(TipoDeRelacao.DERIVADO_DE, LivroKind.FLUXO.value, fluxo, rotulo="fluxo legado",
                                 fonte="skill_versions.skill_id: flow:<id>@1"))
    return saida


# ------------------------------------------------------------------ item do livro
def do_item(*, parent_id: str | None, pai: EntradaDoLivro | None, filhos: Iterable[EntradaDoLivro]) -> list[JsonObject]:
    """`parent_id` (o item é a edição de outro: congelado, mudar é criar outro): o pai é `substitui`, os itens que o
    têm por pai são `substituida_por`. O pai que sumiu do banco não vira relação (`ref` sem alvo é relação inventada)."""
    saida: list[JsonObject] = []
    if parent_id and pai is not None:
        saida.append(relacao(TipoDeRelacao.SUBSTITUI, pai.kind.value, pai.ref, rotulo=pai.title,
                             fonte="learning_items.parent_id"))
    for f in filhos:
        saida.append(relacao(TipoDeRelacao.SUBSTITUIDA_POR, f.kind.value, f.ref, rotulo=f.title,
                             fonte="learning_items.parent_id"))
    return saida


def absorvida(entrada: EntradaDoLivro, tema: str | None) -> list[JsonObject]:
    """`state_detail = absorvida:<commit>`: o repositório passou a declarar o que o item aprendeu. Com o nome da regra
    (a tela do `telas.yaml`) o alvo é a regra declarada; sem ele, o próprio commit."""
    commit = absorvida_em(entrada.detail)
    if commit is None:
        return []
    fonte = "learning_items.state_detail: absorvida:<commit>"
    if tema:
        return [relacao(TipoDeRelacao.ABSORVIDA, KIND_DA_REGRA_DECLARADA, tema,
                        rotulo=f"regra '{tema}' declarada no commit {commit}", fonte=fonte)]
    return [relacao(TipoDeRelacao.ABSORVIDA, KIND_DO_COMMIT, commit, rotulo=f"declarada no commit {commit}", fonte=fonte)]


# ------------------------------------------------------------------ reaprendizado (30.23)
_FONTE_DO_REAPRENDIZADO = "learning_transitions: evidencia_invalida:<run>, mesmo scope_key"


def de_reaprendizado(entrada: EntradaDoLivro, mesmo_escopo: Iterable[EntradaDoLivro]) -> list[JsonObject]:
    """O reaprendido aponta para o item desligado por evidência inválida no mesmo escopo (`reaprende`); o desligado,
    para os que o reaprenderam (`reaprendida_por`). Sai de `EntradaDoLivro.reaprendido`, que o serviço deriva da
    trilha. O fluxo renasce na MESMA linha (`match_key` único): não aponta para si mesmo (a marca do item diz isso)."""
    saida: list[JsonObject] = []
    r = entrada.reaprendido
    if r is not None and r.item_invalidado != entrada.trail_ref:
        kind, sep, ref = r.item_invalidado.partition(":")
        if sep and ref:
            saida.append(relacao(TipoDeRelacao.REAPRENDE, kind, ref,
                                 rotulo=f"{ref} (evidência inválida da execução {r.run_invalidada})",
                                 fonte=_FONTE_DO_REAPRENDIZADO))
    for o in mesmo_escopo:
        if (o.reaprendido is not None and o.reaprendido.item_invalidado == entrada.trail_ref
                and o.trail_ref != entrada.trail_ref):
            saida.append(relacao(TipoDeRelacao.REAPRENDIDA_POR, o.kind.value, o.ref, rotulo=o.title,
                                 fonte=_FONTE_DO_REAPRENDIZADO))
    return saida


# ------------------------------------------------------------------ contradição
def contradiz(entrada: EntradaDoLivro, tema: str | None, outras: Iterable[Parente]) -> list[JsonObject]:
    """Mesmo `scope_key`, `content_hash` diferente, os DOIS vivos (§6). `tema` `None` na própria entrada: o tipo não
    tem critério seguro (ver `Parente`) e nada é derivado."""
    if tema is None or entrada.state not in VIVOS or not entrada.scope_key or not entrada.content_hash:
        return []
    achadas = [p.entrada for p in outras
               if p.entrada.ref != entrada.ref and p.entrada.state in VIVOS and p.tema == tema
               and p.entrada.scope_key == entrada.scope_key and p.entrada.content_hash
               and p.entrada.content_hash != entrada.content_hash]
    return [relacao(TipoDeRelacao.CONTRADIZ, o.kind.value, o.ref, rotulo=o.title,
                    fonte="mesmo scope_key, content_hash diferente, ambos vivos")
            for o in sorted(achadas, key=lambda o: o.ref)]


def ordenar(relacoes: Sequence[JsonObject]) -> list[JsonObject]:
    """Por tipo (a ordem de `TipoDeRelacao`) e depois pelo alvo, para a resposta ser estável entre leituras."""
    def chave(r: JsonObject) -> tuple[int, str, str]:
        tipo = r.get("tipo")
        return (_ORDEM[TipoDeRelacao(tipo)] if isinstance(tipo, str) else len(_ORDEM), str(r.get("kind")),
                str(r.get("ref")))
    return sorted(relacoes, key=chave)
