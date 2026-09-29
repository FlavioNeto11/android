"""Telas aprendidas (ADR-054, fatia 5 = item 18.8), a parte PURA: o que se guarda de uma tela vista, como as
observações viram uma candidata, a prova local, a absorção pelo repositório e o conflito.

Só conjuntos de ids. O domínio não vê a árvore, o YAML nem texto de tela (a catraca de camadas proíbe
`app.automation`): quem tira os ids da árvore, pula a tela protegida e diz o que o arquivo do app reconhece é a
infraestrutura. Aqui as regras declaradas chegam como `RegraDeclarada` — nome, tipo e ids —, e a que depende de texto
(sinal, extração, formulário) é marcada como não reavaliável só com ids.

As regras desta fatia:

- uma observação guarda até 60 sufixos ESTÁVEIS de resource-id: sem texto, sem sequência numérica (id gerado, item
  numerado) e sem os que se repetem na mesma tela (item de lista: dependem do conteúdo);
- a candidata sai de pelo menos 3 observações da mesma tela desconhecida; exige de 2 a 4 ids (`ids_todos`) presentes
  em TODAS elas e ausentes das amostras das telas declaradas e das regras do repositório (inclusive desafio, 2FA,
  login e intersticial). É sempre `autenticada`; é de casa só se TODA observação mostrava a aba de perfil declarada;
- a prova local, sem IA: a regra reclassifica todas as suas amostras reais e não casa com nenhuma amostra declarada;
- o conflito — login, desafio, 2FA ou conta errada no mesmo aparelho até 2 min de um uso — desliga na primeira vez;
- a absorção: quando as regras do repositório classificam todas as amostras da aprendida, ela se aposenta.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from app.modules.skills.domain.document import JsonObject

#: O mesmo vocabulário do classificador (`automation/conhecimento_de_telas.py`), copiado: o domínio não o importa.
DESCONHECIDA = "desconhecida"
PREFIXO = "aprendida_"
TIPO = "autenticada"
IDS_TODOS_MIN = 2
IDS_TODOS_MAX = 4
#: Ids guardados por observação.
IDS_POR_OBSERVACAO = 60
#: Amostras guardadas de cada tela DECLARADA (servem à prova negativa).
AMOSTRAS_POR_TELA = 20
#: Um uso da tela aprendida e um login, desafio, 2FA ou conta errada no mesmo aparelho, até isto de distância: conflito.
JANELA_DE_CONFLITO_S = 120
#: Sem casar há isto numa versão nova do app: a regra se aposenta.
DIAS_SEM_CASAR = 30
#: Tipos de tela que, perto de um uso, são conflito.
TIPOS_DE_CONFLITO = frozenset({"login", "desafio", "dois_fatores"})
#: Duas observações são da mesma tela quando esta fração do menor conjunto de ids é comum.
SOBREPOSICAO_MIN = 0.6

_ESTAVEL = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
_SEQUENCIA = re.compile(r"\d{2,}|_\d+$")
_FORA_DO_NOME = re.compile(r"[^a-z0-9]+")


def estaveis(sufixos: Sequence[str]) -> tuple[str, ...]:
    """Os ids que se guardam de uma tela: sufixos em minúsculas que aparecem UMA vez (o repetido é item de lista), sem
    sequência numérica, ordenados, no máximo `IDS_POR_OBSERVACAO`. Nunca texto."""
    contagem = Counter(s.strip().lower() for s in sufixos)
    bons = sorted(s for s, n in contagem.items() if n == 1 and _ESTAVEL.match(s) and not _SEQUENCIA.search(s))
    return tuple(bons[:IDS_POR_OBSERVACAO])


def assinatura(ids: Iterable[str], tamanho: int = 8) -> str:
    return hashlib.sha1(",".join(sorted(ids)).encode()).hexdigest()[:tamanho]


# ------------------------------------------------------------------ o que se observa
@dataclass(frozen=True, slots=True)
class Observacao:
    """Uma tela vista (`learning_signals.kind='tela_vista'`), já sem nada além de ids."""

    origem: str                      # 'attempt:<id>': a chave da evidência, a mesma do observador e do digest
    ids: frozenset[str]
    classificada: str                # a tela que o repositório reconheceu, ou DESCONHECIDA
    tem_aba: bool                    # a aba de perfil DECLARADA estava na tela
    run_id: str | None = None
    instance_id: str | None = None
    simulated: bool = False
    contexto: str = "*"              # a ação da etapa ('*' = etapa livre)
    versao: str | None = None        # a versão do app no aparelho
    quando: str = ""                 # ISO


# ------------------------------------------------------------------ o que o repositório declara
@dataclass(frozen=True, slots=True)
class RegraDeclarada:
    tela: str
    tipo: str
    ids: tuple[str, ...] = ()        # prefixos: casa com qualquer um
    ids_todos: tuple[str, ...] = ()  # exatos: casa com todos
    so_por_ids: bool = True          # sem sinal de texto, extração, formulário ou "sem elementos"

    def casa(self, ids: frozenset[str]) -> bool | None:
        """Casa só pelos ids? `None` quando a regra depende de texto (não se diz sem a tela inteira)."""
        if not self.so_por_ids or not (self.ids or self.ids_todos):
            return None
        if self.ids and not any(i.startswith(self.ids) for i in ids):
            return False
        return all(i in ids for i in self.ids_todos)

    def ids_da_regra(self) -> tuple[str, ...]:
        return (*self.ids, *self.ids_todos)


@dataclass(frozen=True, slots=True)
class Declaradas:
    """As regras do `telas.yaml` de um app, em ordem de precedência, e as telas de casa."""

    app: str
    regras: tuple[RegraDeclarada, ...]
    casa: tuple[str, ...] = ()

    def nomes(self) -> frozenset[str]:
        return frozenset(r.tela for r in self.regras)

    def sem(self, tela: str) -> Declaradas:
        """Uma cópia sem a regra `tela` (e sem ela no estado conhecido): o deixa-um-fora."""
        return replace(self, regras=tuple(r for r in self.regras if r.tela != tela),
                       casa=tuple(t for t in self.casa if t != tela))

    def classificar(self, ids: frozenset[str]) -> str:
        """A primeira regra que casa SÓ pelos ids; a que depende de texto conta como não casou."""
        return next((r.tela for r in self.regras if r.casa(ids) is True), DESCONHECIDA)


def reclassificar_sem(completas: Declaradas, removida: str, obs: Observacao) -> str:
    """O que a observação seria sem a regra `removida`: igual, se ela não era dessa tela (a regra removida não casou
    antes da que venceu, ou veio depois); senão, a próxima regra que casa pelos ids (as de texto contam como não
    casou), ou DESCONHECIDA."""
    if obs.classificada != removida:
        return obs.classificada
    nomes = [r.tela for r in completas.regras]
    if removida not in nomes:
        return obs.classificada
    return next((r.tela for r in completas.regras[nomes.index(removida) + 1:] if r.casa(obs.ids) is True),
                DESCONHECIDA)


# ------------------------------------------------------------------ a regra aprendida
@dataclass(frozen=True, slots=True)
class RegraAprendida:
    tela: str
    ids_todos: tuple[str, ...]
    casa: bool
    razao: str

    def casa_com(self, ids: frozenset[str]) -> bool:
        return all(i in ids for i in self.ids_todos)

    def conteudo(self) -> JsonObject:
        """O `content` do item do livro — o que `conhecimento_de_telas.regra_aprendida` lê. Nada de contagem nem de
        data: o conteúdo é o que se deduplica (`content_hash`)."""
        return {"tela": self.tela, "tipo": TIPO, "autenticada": True, "ids_todos": list(self.ids_todos),
                "casa": self.casa, "razao": self.razao}


def regra_do_conteudo(conteudo: JsonObject) -> RegraAprendida | None:
    """A regra de um item do livro, ou `None` quando o conteúdo não é uma regra de tela válida (o item é ignorado)."""
    tela, ids, casa, razao = (conteudo.get("tela"), conteudo.get("ids_todos"), conteudo.get("casa", False),
                              conteudo.get("razao"))
    if (not isinstance(tela, str) or not tela.startswith(PREFIXO) or conteudo.get("tipo") != TIPO
            or not isinstance(ids, list) or not isinstance(casa, bool)):
        return None
    todos = tuple(dict.fromkeys(i for i in ids if isinstance(i, str) and i))
    if len(todos) != len(ids) or not IDS_TODOS_MIN <= len(todos) <= IDS_TODOS_MAX:
        return None
    return RegraAprendida(tela, todos, casa, razao if isinstance(razao, str) else tela)


# ------------------------------------------------------------------ candidatas
@dataclass(frozen=True, slots=True)
class Candidata:
    regra: RegraAprendida
    contexto: str
    observacoes: tuple[Observacao, ...]


def agrupar(observacoes: Iterable[Observacao]) -> list[list[Observacao]]:
    """Observações da mesma tela, juntas: cada uma entra no grupo cujo NÚCLEO (os ids comuns a todos os membros) mais
    se sobrepõe a ela, se a sobreposição passa de `SOBREPOSICAO_MIN` do menor conjunto e deixa pelo menos 2 ids em
    comum; senão abre um grupo. Determinístico (ordem da observação)."""
    grupos: list[list[Observacao]] = []
    nucleos: list[frozenset[str]] = []
    for o in sorted(observacoes, key=lambda x: (x.quando, x.origem)):
        melhor, qual = 0.0, -1
        for i, nucleo in enumerate(nucleos):
            comum = len(nucleo & o.ids)
            if nucleo and o.ids and comum >= IDS_TODOS_MIN:
                fracao = comum / min(len(nucleo), len(o.ids))
                if fracao > melhor:
                    melhor, qual = fracao, i
        if qual >= 0 and melhor >= SOBREPOSICAO_MIN:
            grupos[qual].append(o)
            nucleos[qual] = nucleos[qual] & o.ids
        else:
            grupos.append([o])
            nucleos.append(o.ids)
    return grupos


def escolher_ids(livres: Iterable[str]) -> tuple[str, ...]:
    """Até `IDS_TODOS_MAX` ids, preferindo a RAIZ de uma família (o id que é prefixo de outros: o componente, como o
    compositor de uma conversa), depois o mais curto, depois a ordem alfabética."""
    todos = sorted(set(livres))
    familia = {i: sum(1 for j in todos if j.startswith(i)) for i in todos}
    return tuple(sorted(todos, key=lambda i: (-familia[i], len(i), i))[:IDS_TODOS_MAX])


def _contexto(grupo: Sequence[Observacao]) -> str:
    contagem = Counter(o.contexto or "*" for o in grupo)
    mais, _ = min(contagem.items(), key=lambda kv: (-kv[1], kv[0]))
    nome = _FORA_DO_NOME.sub("_", mais.lower()).strip("_")[:24]
    return nome or "livre"


def propor(positivas: Sequence[Observacao], amostras: Sequence[Observacao], declaradas: Declaradas, *,
           minimo: int = 3) -> list[Candidata]:
    """As candidatas das observações DESCONHECIDAS (`positivas`), contra as `amostras` das telas declaradas.

    Um grupo vira candidata com pelo menos `minimo` observações e 2 ids livres: presentes em todas, ausentes de toda
    amostra declarada e de toda regra do repositório (o prefixo de uma regra, de qualquer tipo, exclui o id).

    O agrupamento olha só os ids DISTINTIVOS (os que nenhuma amostra declarada tem): a moldura comum do app — a barra
    de abas, o cabeçalho — juntaria telas diferentes num grupo só, cujo núcleo seria só a moldura."""
    proibidos = frozenset(i for a in amostras for i in a.ids)
    prefixos = tuple(p for r in declaradas.regras for p in r.ids_da_regra())
    originais = {o.origem: o for o in positivas if o.classificada == DESCONHECIDA}
    saida: list[Candidata] = []
    nomes = set(declaradas.nomes())
    for grupo in agrupar(replace(o, ids=o.ids - proibidos) for o in originais.values()):
        if len(grupo) < max(1, minimo):
            continue
        nucleo = frozenset.intersection(*(o.ids for o in grupo))
        livres = [i for i in nucleo if not (prefixos and i.startswith(prefixos))]
        ids = escolher_ids(livres)
        if len(ids) < IDS_TODOS_MIN:
            continue
        contexto = _contexto(grupo)
        nome = f"{PREFIXO}{contexto}_{assinatura(ids, 6)}"
        if nome in nomes:
            continue
        nomes.add(nome)
        regra = RegraAprendida(tela=nome, ids_todos=ids, casa=all(o.tem_aba for o in grupo),
                               razao=f"tela aprendida por observação ({contexto})")
        saida.append(Candidata(regra, contexto, tuple(originais[o.origem] for o in grupo)))
    return saida


def deixa_um_fora(completas: Declaradas, removida: str, observacoes: Sequence[Observacao], *,
                  minimo: int = 3) -> list[Candidata]:
    """A prova offline da fatia: sem a regra `removida` no repositório (nem no estado conhecido), as amostras dela
    viram desconhecidas — o minerador as reencontra? As observações são reclassificadas só pelos ids; as que caem em
    DESCONHECIDA são as positivas, as demais seguem como amostras das telas declaradas."""
    positivas: list[Observacao] = []
    amostras: list[Observacao] = []
    for o in observacoes:
        nova = reclassificar_sem(completas, removida, o)
        (positivas if nova == DESCONHECIDA else amostras).append(replace(o, classificada=nova))
    return propor(positivas, amostras, completas.sem(removida), minimo=minimo)


# ------------------------------------------------------------------ prova local
@dataclass(frozen=True, slots=True)
class ProvaLocal:
    reclassificadas: int             # amostras reais da regra que ela reconhece
    total: int                       # amostras reais da regra
    declaradas_casadas: tuple[str, ...]  # origens das amostras declaradas que ela casaria (tem de ficar vazio)

    @property
    def ok(self) -> bool:
        return self.total > 0 and self.reclassificadas == self.total and not self.declaradas_casadas

    def descrever(self) -> str:
        return (f"reclassifica {self.reclassificadas}/{self.total} amostras e casa "
                f"{len(self.declaradas_casadas)} de telas declaradas")


def provar(regra: RegraAprendida, positivas: Sequence[Observacao], amostras: Sequence[Observacao]) -> ProvaLocal:
    """Sem IA: a regra reconhece TODAS as suas amostras reais (simulado não conta) e nenhuma amostra de tela declarada
    — ela nunca muda o que o repositório já reconhece."""
    reais = [o for o in positivas if not o.simulated]
    return ProvaLocal(reclassificadas=sum(1 for o in reais if regra.casa_com(o.ids)), total=len(reais),
                      declaradas_casadas=tuple(a.origem for a in amostras
                                               if a.classificada != DESCONHECIDA and regra.casa_com(a.ids)))


# ------------------------------------------------------------------ conflito, absorção, aposentadoria
@dataclass(frozen=True, slots=True)
class Uso:
    """Uma vez em que a tela aprendida casou num aparelho (evidência a favor, com o instante)."""

    item_ref: str
    instance_id: str
    quando: datetime


def em_conflito(usos: Iterable[Uso], *, instance_id: str, quando: datetime,
                janela_s: int = JANELA_DE_CONFLITO_S) -> tuple[str, ...]:
    """Os itens usados no MESMO aparelho até `janela_s` de um login, desafio, 2FA ou conta errada (antes ou depois)."""
    janela = timedelta(seconds=janela_s)
    return tuple(sorted({u.item_ref for u in usos
                         if u.instance_id == instance_id and abs(quando - u.quando) <= janela}))


def absorvida_por(declaradas: Declaradas, amostras: Sequence[Observacao]) -> str | None:
    """A tela do repositório que agora reconhece TODAS as amostras da aprendida (só pelos ids), ou `None`. É quando o
    YAML a absorveu (o fragmento exportado foi commitado, ou alguém escreveu uma regra que a cobre)."""
    if not amostras:
        return None
    classes = [declaradas.classificar(a.ids) for a in amostras]
    if DESCONHECIDA in classes:
        return None
    return Counter(classes).most_common(1)[0][0]


def sem_casar(*, ultima_a_favor: datetime | None, criada: datetime, versao_do_item: str | None,
              versoes_recentes: Iterable[str], agora: datetime, dias: int = DIAS_SEM_CASAR) -> bool:
    """`DIAS_SEM_CASAR` sem casar E uma versão nova do app observada: a tela provavelmente mudou de novo."""
    referencia = ultima_a_favor or criada
    novas = {v for v in versoes_recentes if v and v != versao_do_item}
    return agora - referencia >= timedelta(days=dias) and bool(novas)


__all__ = ["AMOSTRAS_POR_TELA", "DESCONHECIDA", "DIAS_SEM_CASAR", "IDS_POR_OBSERVACAO", "IDS_TODOS_MAX",
           "IDS_TODOS_MIN", "JANELA_DE_CONFLITO_S", "PREFIXO", "TIPOS_DE_CONFLITO", "Candidata", "Declaradas",
           "Observacao", "ProvaLocal", "RegraAprendida", "RegraDeclarada", "Uso", "absorvida_por", "agrupar",
           "assinatura", "deixa_um_fora", "em_conflito", "escolher_ids", "estaveis", "propor", "provar",
           "reclassificar_sem", "regra_do_conteudo", "sem_casar"]
