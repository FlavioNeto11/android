"""O que uma execução ensinou ao livro e o que ela usou dele — "Aprendizado desta execução" no relatório (ADR-054,
D2). Puro: recebe os fatos já lidos e devolve as linhas agrupadas como o painel as mostra.

| grupo | entra |
|---|---|
| receita | a que a execução usou (`attempts.recipe_id`), a que aprendeu (`learned_from_step`), a que mudou de estado ou recebeu evidência por causa dela |
| fluxo | o que usou (`runs.flow_id`), o que nasceu dela (`source_run_id`), o que mudou de estado ou recebeu evidência por causa dela |
| falha | as tentativas que falharam, pelo tipo gravado (A2) ou, no legado, pelo mesmo classificador puro na leitura (retroativo) |
| candidata | o item do livro (lição, tela, voz, preferência) que NASCEU desta execução (a transição de nascimento leva o `run_id`; a preferência, o da observação que fechou o limiar, e o papel diz "entre N execuções"), e a tela, a voz e a preferência que já existiam e mudaram de estado por causa dela (o papel diz "… que já existia" e o que mudou) |
| licao | a lição que não nasceu aqui e foi exposta (por papel e braço), mudou de estado ou recebeu evidência por causa dela |

Uma linha por item em cada grupo — o painel usa `grupo-ref` como chave —, e cada item num grupo só: a lição que nasceu
desta execução e recebeu dela a primeira evidência é candidata, não "lição exposta". O que o painel não tem onde
mostrar fica de fora: a versão de habilidade (a trilha dela é outra) e a tela, a voz e a preferência que a execução só
reforçou (a página Aprendizado as mostra com a evidência). Quem nasce com o `run_id`: a lição e a voz (mineradores do
digest), a tela (`telas.minerar(run_id)`) e a preferência. Esta nasce na curadoria periódica, da evidência de várias
execuções: a trilha leva a execução cuja observação fechou o limiar, e o papel não a vende como causa única — "nasceu
com a evidência desta execução, entre N execuções" (`TransicaoDaExecucao.entre`, lido de `provenance.limiar`).

A trilha diz QUEM decidiu (`decided_by`). Só a transição do sistema é "nesta execução"; a de uma pessoa que leva o
`run_id` (hoje, o desligamento pelo voto "deu errado") diz que foi uma pessoa — "pelo voto de uma pessoa" quando o
motivo é o do voto (`voto.veio_do_voto`), "por uma pessoa" nos demais —, porque a decisão humana posterior não é da
execução.

O `papel` é texto fechado, montado aqui: nenhum texto livre de tela, de nota, de motivo ou de quem decidiu sai por ele.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from app.modules.learning.domain.ciclo import SYSTEM_ACTOR, SkillState
from app.modules.learning.domain.falhas import classificar_falha
from app.modules.learning.domain.prova import ROTULO_DO_MOTIVO, motivo_da_invalida
from app.modules.learning.domain.vocabulario import KINDS_DE_ITEM, KINDS_NATIVOS, Braco, LivroKind, Papel, Posicao
from app.modules.learning.domain.voto import veio_do_voto


class Grupo(StrEnum):
    RECEITA = "receita"
    FLUXO = "fluxo"
    FALHA = "falha"
    CANDIDATA = "candidata"
    LICAO = "licao"


#: A chave de cada grupo no bloco `aprendizado` de `GET /api/runs/{id}/feedback`, na ordem em que o painel os mostra
#: (`frontend/src/features/aprendizado/model.ts::GRUPOS_DO_APRENDIZADO`).
CHAVE_DO_GRUPO: Mapping[Grupo, str] = {Grupo.RECEITA: "receitas", Grupo.FLUXO: "fluxos", Grupo.FALHA: "falhas",
                                       Grupo.CANDIDATA: "candidatas", Grupo.LICAO: "licoes"}


# ------------------------------------------------------------------ os fatos (o que a leitura entrega)
@dataclass(frozen=True, slots=True)
class TransicaoDaExecucao:
    """Uma linha de `learning_transitions` com o `run_id` desta execução. `de=None` é o nascimento do item. `por` é o
    `decided_by` e `motivo` o `reason`: só servem para dizer se foi o sistema, o voto de uma pessoa ou uma pessoa —
    nenhum dos dois sai no papel. `entre`: no nascimento da preferência, em quantas execuções estava a evidência que
    fechou o limiar (as outras transições, `None`)."""

    item_ref: str
    item_kind: str
    de: str | None
    para: str
    por: str = SYSTEM_ACTOR
    motivo: str = ""
    entre: int | None = None


@dataclass(frozen=True, slots=True)
class EvidenciaDaExecucao:
    """As evidências desta execução contra um item, somadas por posição. `item_kind` é o do `learning_items` (só nos
    itens `li-…`; o nativo diz o tipo no próprio `item_ref`)."""

    item_ref: str
    item_kind: str | None
    posicao: str
    n: int
    #: O `detail` de uma das linhas (só serve à `invalida`, que traz o motivo no começo: `domain.prova`).
    detalhe: str | None = None


@dataclass(frozen=True, slots=True)
class ExposicaoDaExecucao:
    """A lição foi ao prompt (`with`) ou ficou de fora de propósito (`holdout`) para um papel, nesta execução."""

    item_id: str
    papel: str
    braco: str


@dataclass(frozen=True, slots=True)
class TentativaDaExecucao:
    failure_kind: str | None
    status: str | None
    erro: str | None


@dataclass(frozen=True, slots=True)
class FatosDaExecucao:
    fluxo_usado: str | None = None
    fluxos_aprendidos: tuple[str, ...] = ()
    receitas_usadas: tuple[str, ...] = ()
    receitas_aprendidas: tuple[str, ...] = ()
    transicoes: tuple[TransicaoDaExecucao, ...] = ()
    evidencias: tuple[EvidenciaDaExecucao, ...] = ()
    exposicoes: tuple[ExposicaoDaExecucao, ...] = ()
    tentativas: tuple[TentativaDaExecucao, ...] = ()


# ------------------------------------------------------------------ a linha
@dataclass(frozen=True, slots=True)
class ItemAprendido:
    """Uma linha do "Aprendizado desta execução". `titulo` e `estado` vêm do livro (a aplicação os preenche); na
    falha, o painel escreve o rótulo pelo `failure_kind` e pelo `n`."""

    grupo: Grupo
    kind: LivroKind | None
    ref: str | None
    papel: str | None
    braco: Braco | None = None
    failure_kind: str | None = None
    n: int | None = None
    titulo: str | None = None
    estado: SkillState | None = None


# ------------------------------------------------------------------ o texto fechado do papel
#: Só o fluxo é masculino entre os tipos que chegam aqui (receita, lição, tela, voz e preferência são femininos).
_MASCULINOS = frozenset({LivroKind.FLUXO})

_ROTULO_DO_KIND: Mapping[LivroKind, str] = {LivroKind.LICAO: "lição", LivroKind.TELA: "tela", LivroKind.VOZ: "voz",
                                            LivroKind.PREFERENCIA: "preferência"}

_ROTULO_DO_PAPEL: Mapping[str, str] = {Papel.ACTOR.value: "ator", Papel.PLANNER.value: "planejador",
                                       Papel.WRITER.value: "redator", Papel.RESOLVER.value: "resolvedor",
                                       Papel.CLASSIFIER.value: "classificador"}

#: As mesmas palavras do painel (`model.ts::lerAprendizado`, quando só o braço chega).
_ROTULO_DO_BRACO: Mapping[str, str] = {Braco.WITH.value: "exposta ao prompt", Braco.HOLDOUT.value: "braço de controle"}

_POSICAO: Mapping[str, str] = {Posicao.FOR.value: "a favor", Posicao.AGAINST.value: "contra",
                               Posicao.CONFLICT.value: "em conflito",
                               Posicao.FORMA.value: "de forma (não conta)",   # 30.36
                               Posicao.INVALIDA.value: "inválida (não conta)",   # 30.42
                               Posicao.REVALIDADA.value: "revalidada (desfaz a inválida; não conta)"}   # 30.53

_MOTIVO_DA_INVALIDA: Mapping[str, str] = {m.value: r for m, r in ROTULO_DO_MOTIVO.items()}

#: Quem fez a transição, no fim do verbo: só a do sistema é da execução.
_DA_EXECUCAO = "nesta execução"
_PELO_VOTO = "pelo voto de uma pessoa"
_POR_PESSOA = "por uma pessoa"


def _autoria(t: TransicaoDaExecucao) -> str:
    if t.por == SYSTEM_ACTOR:
        return _DA_EXECUCAO
    return _PELO_VOTO if veio_do_voto(t.motivo) else _POR_PESSOA


def _verbo(kind: LivroKind, para: str) -> str:
    o = "o" if kind in _MASCULINOS else "a"
    if kind is LivroKind.RECEITA and para == SkillState.DISABLED.value:
        return "posta em quarentena"
    if kind is LivroKind.RECEITA and para == SkillState.DEPRECATED.value:
        return "substituída"
    verbos = {SkillState.CANDIDATE.value: f"voltou a candidat{o}", SkillState.VALIDATED.value: f"validad{o}",
              SkillState.PUBLISHED.value: f"publicad{o}", SkillState.DISABLED.value: f"desligad{o}",
              SkillState.DEPRECATED.value: f"aposentad{o}"}
    return verbos.get(para, f"foi para {para}")


@dataclass(slots=True)
class _Marcas:
    """O que a execução fez com um item, na ordem em que os fatos chegam; vira o `papel` no fim."""

    kind: LivroKind
    ref: str
    nasceu: bool = False
    entre: int | None = None                 # a preferência nasce da evidência de várias execuções
    usos: list[str] = field(default_factory=list)
    exposicoes: list[tuple[str, str]] = field(default_factory=list)
    transicoes: list[tuple[str, str]] = field(default_factory=list)     # (para, autoria), na ordem da trilha
    evidencias: dict[str, int] = field(default_factory=dict)

    def usar(self, texto: str) -> None:
        if texto not in self.usos:
            self.usos.append(texto)

    def expor(self, papel: str, braco: str) -> None:
        if (papel, braco) not in self.exposicoes:
            self.exposicoes.append((papel, braco))

    def transitar(self, t: TransicaoDaExecucao) -> None:
        if t.de is None:
            self.nasceu = True
            self.entre = t.entre
            return
        marca = (t.para, _autoria(t))
        if not self.transicoes or self.transicoes[-1] != marca:
            self.transicoes.append(marca)

    def papel(self) -> str | None:
        partes: list[str] = []
        if _grupo(self) is Grupo.CANDIDATA:
            # Na candidata que já existia, o papel diz que ela não nasceu aqui: o grupo é "Candidatas geradas".
            rotulo = _ROTULO_DO_KIND.get(self.kind, self.kind.value)
            if not self.nasceu:
                partes.append(f"{rotulo} que já existia")
            elif self.kind is LivroKind.PREFERENCIA:
                # Nasce da repetição em várias execuções: esta só fechou o limiar, não a causou sozinha.
                outras = f", entre {self.entre} execuções" if self.entre and self.entre > 1 else " e de outras"
                partes.append(f"{rotulo} que nasceu com a evidência desta execução{outras}")
            else:
                partes.append(rotulo)
        partes.extend(self.usos)
        partes.extend(f"{_ROTULO_DO_BRACO.get(b, b)} ({_ROTULO_DO_PAPEL.get(p, p)})" for p, b in self.exposicoes)
        blocos: list[tuple[str, list[str]]] = []       # as transições seguidas de um mesmo autor, num verbo só
        for para, autoria in self.transicoes:
            if blocos and blocos[-1][0] == autoria:
                blocos[-1][1].append(_verbo(self.kind, para))
            else:
                blocos.append((autoria, [_verbo(self.kind, para)]))
        partes.extend(f"{' e '.join(verbos)} {autoria}" for autoria, verbos in blocos)
        for posicao, n in self.evidencias.items():
            base, _, motivo = posicao.partition(":")
            rotulo = _POSICAO.get(base, base)
            if motivo and (m := _MOTIVO_DA_INVALIDA.get(motivo)):
                rotulo = f"inválida ({m}; não conta)"
            partes.append(f"evidência {rotulo}" if n == 1 else f"{n} evidências {rotulo}")
        return ", ".join(partes) or None

    def braco(self) -> Braco | None:
        """O braço, quando todas as exposições desta execução caíram no mesmo; misturado, só o `papel` diz."""
        bracos = {b for _, b in self.exposicoes}
        if len(bracos) != 1:
            return None
        unico = next(iter(bracos))
        return Braco(unico) if unico in {x.value for x in Braco} else None


_NATIVOS = frozenset(k.value for k in KINDS_NATIVOS)
_DE_ITEM = frozenset(k.value for k in KINDS_DE_ITEM)


def _alvo(item_ref: str, item_kind: str | None) -> tuple[LivroKind, str] | None:
    """`receita:12` → (receita, '12'); `li-…` → (o tipo do item, 'li-…'). Referência sem tipo conhecido: `None`."""
    tipo, sep, resto = item_ref.partition(":")
    if sep and resto and tipo in _NATIVOS:
        return LivroKind(tipo), resto
    if item_kind is not None and item_kind in _DE_ITEM and not sep:
        return LivroKind(item_kind), item_ref
    return None


class _Coleta:
    def __init__(self) -> None:
        self.marcas: dict[tuple[LivroKind, str], _Marcas] = {}

    def de(self, kind: LivroKind, ref: str) -> _Marcas:
        chave = (kind, ref)
        if chave not in self.marcas:
            self.marcas[chave] = _Marcas(kind, ref)
        return self.marcas[chave]


def _grupo(m: _Marcas) -> Grupo | None:
    if m.kind is LivroKind.RECEITA:
        return Grupo.RECEITA
    if m.kind is LivroKind.FLUXO:
        return Grupo.FLUXO
    if m.kind in KINDS_DE_ITEM and m.nasceu:
        return Grupo.CANDIDATA
    if m.kind is LivroKind.LICAO:
        return Grupo.LICAO
    if m.kind in KINDS_DE_ITEM and m.transicoes:
        return Grupo.CANDIDATA      # tela, voz ou preferência que já existia e mudou de estado por causa da execução
    return None                     # habilidade, e tela/voz/preferência só reforçadas: o painel não tem onde


def _falhas(tentativas: Iterable[TentativaDaExecucao]) -> list[ItemAprendido]:
    """Uma linha por tipo, da mais frequente à menos; o legado (sem tipo gravado) é classificado aqui, sem gravar."""
    total: dict[str, int] = {}
    retroativas: dict[str, int] = {}
    for t in tentativas:
        tipo = t.failure_kind
        if not tipo:
            classificada = classificar_falha(t.erro, t.status)
            if classificada is None:
                continue
            tipo = classificada.value
            retroativas[tipo] = retroativas.get(tipo, 0) + 1
        total[tipo] = total.get(tipo, 0) + 1
    saida: list[ItemAprendido] = []
    for tipo, n in sorted(total.items(), key=lambda kv: (-kv[1], kv[0])):
        r = retroativas.get(tipo, 0)
        papel = None if not r else ("classificada na leitura (retroativo)" if r == n
                                    else f"{r} de {n} classificadas na leitura (retroativo)")
        saida.append(ItemAprendido(Grupo.FALHA, None, None, papel, failure_kind=tipo, n=n))
    return saida


def aprendido_na_execucao(fatos: FatosDaExecucao) -> tuple[ItemAprendido, ...]:
    """As linhas, na ordem dos grupos do painel; dentro de cada grupo, na ordem em que a execução as tocou."""
    c = _Coleta()
    if fatos.fluxo_usado:
        c.de(LivroKind.FLUXO, fatos.fluxo_usado).usar("usado")
    for ref in fatos.fluxos_aprendidos:
        c.de(LivroKind.FLUXO, ref).usar("aprendido nesta execução")
    for ref in fatos.receitas_usadas:
        c.de(LivroKind.RECEITA, ref).usar("usada")
    for ref in fatos.receitas_aprendidas:
        c.de(LivroKind.RECEITA, ref).usar("aprendida nesta execução")
    for x in fatos.exposicoes:
        c.de(LivroKind.LICAO, x.item_id).expor(x.papel, x.braco)
    for t in fatos.transicoes:
        alvo = _alvo(t.item_ref, t.item_kind)
        if alvo is None:
            continue
        c.de(*alvo).transitar(t)
    for e in fatos.evidencias:
        alvo = _alvo(e.item_ref, e.item_kind)
        if alvo is None:
            continue
        m = c.de(*alvo)
        chave = e.posicao
        if chave == Posicao.INVALIDA.value and (motivo := motivo_da_invalida(e.detalhe)) is not None:
            chave = f"{chave}:{motivo.value}"                # 30.42: a `invalida` diz o porquê
        m.evidencias[chave] = m.evidencias.get(chave, 0) + max(0, e.n)

    por_grupo: dict[Grupo, list[ItemAprendido]] = {g: [] for g in Grupo}
    for m in c.marcas.values():
        grupo = _grupo(m)
        if grupo is not None:
            por_grupo[grupo].append(ItemAprendido(grupo, m.kind, m.ref, m.papel(), braco=m.braco()))
    por_grupo[Grupo.FALHA] = _falhas(fatos.tentativas)
    return tuple(i for g in CHAVE_DO_GRUPO for i in por_grupo[g])


__all__ = ["CHAVE_DO_GRUPO", "EvidenciaDaExecucao", "ExposicaoDaExecucao", "FatosDaExecucao", "Grupo",
           "ItemAprendido", "TentativaDaExecucao", "TransicaoDaExecucao", "aprendido_na_execucao"]
