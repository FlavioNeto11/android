"""Retriever BM25 por arquivo, em stdlib pura (sem `rank_bm25`), para pergunta SEM identificador explícito.

Onde o léxico precisa do nome exato, o BM25 acha "onde se calcula a fatura mensal" por vocabulário: o documento é o
arquivo (tokens do conteúdo mais os tokens do CAMINHO, com peso extra) e a consulta é a pergunta tokenizada. A
tokenização quebra snake_case e camelCase mas mantém o composto inteiro, ignora acento e maiúscula e descarta
stopwords pt/en. Ideia da baseline do piloto (`experiments/jev`), reescrita aqui.

O índice vive em memória, chaveado por `Workspace.revision()`: edição ou arquivo novo muda a revisão e o índice é
refeito no pedido seguinte; sem mudança, é reaproveitado. Determinístico: mesma árvore e mesma pergunta, mesma saída
(desempate por caminho).
"""
from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field

from app.modules.context_retrieval.domain.model import ContextSelection, FileHit, Region, RetrievalRequest
from app.modules.context_retrieval.infrastructure.lexical import STOPWORDS, deaccent, fold
from app.modules.context_retrieval.infrastructure.workspace import Workspace, normalize_scope, path_in_scope

#: Quantas vezes cada token do caminho conta no documento: nome de arquivo é o sinal mais denso que ele tem.
PESO_CAMINHO = 3

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")

#: Subtokenização por identificador: o vocabulário de um repositório repete muito, então memoizar custa pouco e
#: tira o regex do caminho quente (é o que torna indexar o repositório inteiro viável em Python puro).
_MEMO: dict[str, tuple[str, ...]] = {}
_MEMO_MAX = 200_000


def _subtokens(ident: str) -> tuple[str, ...]:
    cached = _MEMO.get(ident)
    if cached is not None:
        return cached
    subs: list[str] = []
    for parte in (p for p in ident.split("_") if p):
        subs.extend(fold(x) for x in _CAMEL.findall(parte))
    subs = [s for s in subs if len(s) > 1 and s not in STOPWORDS]
    composto = fold(ident).strip("_")
    if len(subs) > 1 and len(composto) > 3:
        subs.append(composto)  # o nome inteiro também vale: casar `pick_device_for_task` é mais forte que casar `device`
    elif len(subs) == 1 and ident.startswith("_") and len(composto) > 3:
        subs.append(composto)
    out = tuple(subs)
    if len(_MEMO) < _MEMO_MAX:
        _MEMO[ident] = out
    return out


def tokenize_counts(texto: str) -> Counter[str]:
    """Frequência de cada token do texto (subtokens e compostos)."""
    if not texto.isascii():
        texto = deaccent(texto)
    contagem: Counter[str] = Counter()
    for ident, n in Counter(_IDENT.findall(texto)).items():
        for tok in _subtokens(ident):
            contagem[tok] += n
    return contagem


def tokenize(texto: str) -> list[str]:
    """Tokens do texto em ordem de aparição aproximada (um por ocorrência de identificador)."""
    if not texto.isascii():
        texto = deaccent(texto)
    return [tok for ident in _IDENT.findall(texto) for tok in _subtokens(ident)]


@dataclass
class _Indice:
    revisao: str
    caminhos: list[str] = field(default_factory=list)
    tamanhos: list[int] = field(default_factory=list)
    postings: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    media: float = 0.0

    def idf(self, token: str) -> float:
        df = len(self.postings.get(token, ()))
        n = len(self.caminhos)
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))


class BM25Retriever:
    """`ContextRetriever` por BM25 sobre o universo do `Workspace`."""

    name = "bm25"

    def __init__(self, workspace: Workspace, *, k1: float = 1.2, b: float = 0.75, window_lines: int = 12) -> None:
        self._ws = workspace
        self._k1 = k1
        self._b = b
        self._janela = max(1, window_lines)
        self._indice: _Indice | None = None
        #: Quantas vezes o índice foi (re)construído: o teste e a observabilidade conferem o reaproveitamento.
        self.index_builds = 0

    # ------------------------------------------------------------------ índice
    def _construir(self, revisao: str) -> _Indice:
        idx = _Indice(revisao=revisao)
        for caminho in self._ws.files():
            texto = self._ws.read_text(caminho)
            if texto is None:
                continue
            tf = tokenize_counts(texto)
            for tok, n in tokenize_counts(caminho).items():
                tf[tok] += n * PESO_CAMINHO
            doc = len(idx.caminhos)
            idx.caminhos.append(caminho)
            idx.tamanhos.append(sum(tf.values()))
            for tok, n in tf.items():
                idx.postings.setdefault(tok, []).append((doc, n))
        idx.media = (sum(idx.tamanhos) / len(idx.tamanhos)) if idx.tamanhos else 0.0
        self.index_builds += 1
        return idx

    def _indice_atual(self, revisao: str) -> tuple[_Indice, bool]:
        if self._indice is not None and self._indice.revisao == revisao:
            return self._indice, True
        self._indice = self._construir(revisao)
        return self._indice, False

    # ------------------------------------------------------------------ janela
    def _melhor_janela(self, caminho: str, texto: str, idx: _Indice, consulta: list[str]) -> Region:
        """Janela de `window_lines` linhas com mais peso (idf) de termos da pergunta; empate fica com a mais cedo."""
        linhas = texto.split("\n")
        if linhas and linhas[-1] == "":
            linhas.pop()
        total = max(1, len(linhas))
        alvo = set(consulta)
        peso = [0.0] * total
        for i, linha in enumerate(linhas):
            presentes = alvo.intersection(tokenize(linha))
            peso[i] = sum(idx.idf(t) for t in presentes)
        tam = min(self._janela, total)
        soma = sum(peso[:tam])
        melhor, ini_melhor = soma, 0
        for ini in range(1, total - tam + 1):
            soma += peso[ini + tam - 1] - peso[ini - 1]
            if soma > melhor + 1e-12:
                melhor, ini_melhor = soma, ini
        a, b = ini_melhor + 1, ini_melhor + tam
        return Region(caminho, a, b, "\n".join(linhas[a - 1:b]))

    # ------------------------------------------------------------------ API
    def retrieve(self, request: RetrievalRequest) -> ContextSelection:
        t0 = time.perf_counter()
        revisao = self._ws.revision()
        idx, reaproveitado = self._indice_atual(revisao)
        consulta = sorted(set(tokenize(request.query)))
        meta = {"tokens": len(consulta), "files_considered": len(idx.caminhos), "index_reused": reaproveitado}
        ms = lambda: (time.perf_counter() - t0) * 1000  # noqa: E731
        if not consulta:
            return ContextSelection.empty(self.name, warnings=("no_tokens",), metadata=meta, latency_ms=ms())

        escopo = normalize_scope(request.scope)
        pontos: dict[int, float] = {}
        casados: dict[int, int] = {}
        n = len(idx.caminhos)
        for tok in consulta:  # ordem fixa: a soma de floats não pode depender da ordem de iteração
            posting = idx.postings.get(tok)
            if not posting:
                continue
            idf = idx.idf(tok)
            for doc, tf in posting:
                if not path_in_scope(idx.caminhos[doc], escopo):
                    continue
                norma = self._k1 * (1.0 - self._b + self._b * idx.tamanhos[doc] / idx.media) if idx.media else self._k1
                pontos[doc] = pontos.get(doc, 0.0) + idf * tf * (self._k1 + 1.0) / (tf + norma)
                casados[doc] = casados.get(doc, 0) + 1
        if not pontos or n == 0:
            return ContextSelection.empty(self.name, warnings=("no_matches",), metadata=meta, latency_ms=ms())

        ordem = sorted(pontos, key=lambda d: (-round(pontos[d], 9), idx.caminhos[d]))[: max(0, request.top_k)]
        hits: list[FileHit] = []
        regioes: list[Region] = []
        for d in ordem:
            caminho = idx.caminhos[d]
            texto = self._ws.read_text(caminho) or ""
            regiao = self._melhor_janela(caminho, texto, idx, consulta)
            hits.append(FileHit(path=caminho, score=pontos[d], source=self.name, matched_terms=casados[d],
                                regions=(regiao,)))
            regioes.append(regiao)
        return ContextSelection(selected_files=tuple(hits), selected_regions=tuple(regioes), source=self.name,
                                latency_ms=ms(), metadata=meta, warnings=() if hits else ("no_matches",))
