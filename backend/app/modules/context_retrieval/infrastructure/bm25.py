"""Retriever BM25 por arquivo, em stdlib pura (sem `rank_bm25`), para pergunta SEM identificador explícito.

Onde o léxico precisa do nome exato, o BM25 acha "onde se calcula a fatura mensal" por vocabulário: o documento é o
arquivo (tokens do conteúdo mais os tokens do CAMINHO, com peso extra) e a consulta é a pergunta tokenizada. A
tokenização quebra snake_case e camelCase mas mantém o composto inteiro, ignora acento e maiúscula e descarta
stopwords pt/en. Ideia da baseline do piloto (`experiments/jev`), reescrita aqui.

O índice vive em memória e, com `cache_dir`, também em disco, chaveado por raiz + `Workspace.revision()` + versões do
retrieval e do índice + resumo do corpus (padrões sensíveis e teto de tamanho): edição ou arquivo novo muda a revisão e
nasce outro arquivo; revisão incompatível nunca é carregada. Determinístico: mesma árvore e mesma pergunta, mesma saída
(desempate por caminho).

O que vai para o disco é só vocabulário e contagens (token → documento, frequência), nunca texto de arquivo; arquivo de
caminho sensível nem entra no universo, e linha com formato de credencial é descartada ANTES de tokenizar, para que um
segredo esquecido num arquivo comum não vire termo do índice.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.modules.context_retrieval.domain.model import (RETRIEVAL_VERSION, ContextSelection, FileHit, Region,
                                                        RetrievalRequest)
from app.modules.context_retrieval.domain.sensitive import hard_secret_kind, has_soft_secret
from app.modules.context_retrieval.infrastructure.lexical import STOPWORDS, deaccent, fold
from app.modules.context_retrieval.infrastructure.workspace import Workspace, normalize_scope, path_in_scope

#: Muda quando a tokenização, a higiene ou o formato do arquivo mudam: invalida todo índice em disco.
INDEX_VERSION = "2"
#: Índices antigos mantidos em disco por raiz. Limpeza limitada: o diretório nunca cresce sem teto.
INDICES_MANTIDOS = 3

#: Marcas de linha que SÓ valem conferência contra os formatos de credencial se aparecerem. Conferir toda linha de todo
#: arquivo custava ~10 s; com as marcas, só as poucas linhas que falam de segredo passam pelas regexes caras (a duras e a
#: moles têm marcas próprias: uma linha de URL não precisa da regex de par chave/valor, e vice-versa).
_MARCA_DURA = re.compile(r"://|eyJ|sk-|AKIA|gh[pousr]_|xox|glpat|AIza|PRIVATE KEY|Bearer|Authoriz", re.IGNORECASE)
_MARCA_MOLE = re.compile(r"passw|senha|secret|segredo|token|api[_-]?key|credential|credencial|authoriz|"
                         r"private[_-]?key|psk", re.IGNORECASE)
#: A varredura do arquivo inteiro roda sobre o texto em minúsculas e SEM `IGNORECASE`: a mesma busca com a flag custava 8x
#: mais (3,7 s contra 0,5 s em 19 MB). A conferência da linha, essa sim, usa as regexes de verdade sobre o texto original.
_GATILHO = re.compile((_MARCA_DURA.pattern + "|" + _MARCA_MOLE.pattern).lower())
_GATILHO_QUALQUER_CAIXA = re.compile(_MARCA_DURA.pattern + "|" + _MARCA_MOLE.pattern, re.IGNORECASE)
#: Token cru sem `_`, longo e alfanumérico: tem cara de chave/hash, não de identificador de código.
_BLOB = re.compile(r"^(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{24,}$|^[A-Za-z0-9]{40,}$")


def _linha_suspeita(linha: str) -> bool:
    return bool((_MARCA_DURA.search(linha) and hard_secret_kind(linha))
                or (_MARCA_MOLE.search(linha) and has_soft_secret(linha)))


def higienizar(texto: str) -> str:
    """O texto sem as linhas com formato de credencial. Só para tokenizar o índice; o arquivo em si não muda.

    Procura as marcas no texto inteiro e confere só a linha de cada uma, em vez de partir e varrer todas as linhas.
    """
    ruins: list[tuple[int, int]] = []
    ate = -1
    baixo = texto.lower()
    # `lower()` que muda o tamanho desalinharia as posições: nesse caso (raro) vale a busca lenta e exata.
    achados = _GATILHO.finditer(baixo) if len(baixo) == len(texto) else _GATILHO_QUALQUER_CAIXA.finditer(texto)
    for m in achados:
        if m.start() < ate:                      # outra marca da linha que já foi conferida
            continue
        ini = texto.rfind("\n", 0, m.start()) + 1
        fim = texto.find("\n", m.end())
        fim = len(texto) if fim < 0 else fim
        ate = fim
        if _linha_suspeita(texto[ini:fim]):
            ruins.append((ini, fim))
    if not ruins:
        return texto
    partes: list[str] = []
    pos = 0
    for ini, fim in ruins:
        partes.append(texto[pos:ini])
        pos = fim
    partes.append(texto[pos:])
    return "".join(partes)


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
    """Frequência de cada token do texto (subtokens e compostos). Blob alfanumérico longo (chave, hash) é descartado."""
    if not texto.isascii():
        texto = deaccent(texto)
    contagem: Counter[str] = Counter()
    for ident, n in Counter(_IDENT.findall(texto)).items():
        if len(ident) >= 24 and _BLOB.match(ident):
            continue
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
    #: token → [doc0, tf0, doc1, tf1, ...] achatado: grava e carrega mais rápido que uma lista de tuplas.
    postings: dict[str, list[int]] = field(default_factory=dict)
    media: float = 0.0

    def idf(self, token: str) -> float:
        df = len(self.postings.get(token, ())) // 2
        n = len(self.caminhos)
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))


class BM25Retriever:
    """`ContextRetriever` por BM25 sobre o universo do `Workspace`."""

    name = "bm25"

    def __init__(self, workspace: Workspace, *, k1: float = 1.2, b: float = 0.75, window_lines: int = 12,
                 cache_dir: Path | None = None) -> None:
        self._ws = workspace
        self._k1 = k1
        self._b = b
        self._janela = max(1, window_lines)
        self._indice: _Indice | None = None
        self._dir = Path(cache_dir) if cache_dir is not None else None
        #: Quantas vezes o índice foi (re)construído: o teste e a observabilidade conferem o reaproveitamento.
        self.index_builds = 0
        #: De onde veio o índice da última consulta: "memory", "disk" ou "built". E o que cada via custou.
        self.last_source = ""
        self.stats: dict[str, float] = {"memory_hits": 0, "disk_hits": 0, "misses": 0, "build_ms": 0.0,
                                        "load_ms": 0.0, "save_ms": 0.0, "cache_bytes": 0}

    # ------------------------------------------------------------------ índice
    def _construir(self, revisao: str) -> _Indice:
        idx = _Indice(revisao=revisao)
        for caminho in self._ws.files():
            texto = self._ws.read_text(caminho)
            if texto is None:
                continue
            tf = tokenize_counts(higienizar(texto))
            for tok, n in tokenize_counts(caminho).items():
                tf[tok] += n * PESO_CAMINHO
            doc = len(idx.caminhos)
            idx.caminhos.append(caminho)
            idx.tamanhos.append(sum(tf.values()))
            for tok, n in tf.items():
                p = idx.postings.get(tok)
                if p is None:
                    idx.postings[tok] = [doc, n]
                else:
                    p.append(doc)
                    p.append(n)
        idx.media = (sum(idx.tamanhos) / len(idx.tamanhos)) if idx.tamanhos else 0.0
        self.index_builds += 1
        return idx

    # --- disco
    def _cabecalho(self, revisao: str) -> dict[str, str]:
        raiz = os.path.normcase(str(self._ws.root))
        return {"index_version": INDEX_VERSION, "retrieval_version": RETRIEVAL_VERSION,
                "root_id": hashlib.sha256(raiz.encode("utf-8")).hexdigest()[:16], "revision": revisao,
                "corpus": self._ws.corpus_digest()}

    def _arquivo_do_indice(self, cab: dict[str, str]) -> Path | None:
        if self._dir is None:
            return None
        chave = hashlib.sha256("|".join(f"{k}={cab[k]}" for k in sorted(cab)).encode("utf-8")).hexdigest()[:24]
        return self._dir / f"bm25-{cab['root_id']}-{chave}.json"

    def _carregar(self, cab: dict[str, str]) -> _Indice | None:
        arq = self._arquivo_do_indice(cab)
        if arq is None or not arq.is_file():
            return None
        try:
            bruto = json.loads(arq.read_text(encoding="utf-8"))
            if bruto.get("header") != cab:        # nome igual, identidade diferente dentro: não carrega
                return None
            idx = _Indice(revisao=cab["revision"], caminhos=[str(x) for x in bruto["paths"]],
                          tamanhos=[int(x) for x in bruto["sizes"]],
                          postings={str(t): [int(x) for x in v] for t, v in bruto["postings"].items()})
            n = len(idx.caminhos)
            if len(idx.tamanhos) != n or any(len(v) % 2 or any(d < 0 or d >= n for d in v[::2])
                                             for v in idx.postings.values()):
                return None                       # índice truncado ou adulterado: miss, nunca resultado errado
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None
        idx.media = (sum(idx.tamanhos) / n) if n else 0.0
        self.stats["cache_bytes"] = arq.stat().st_size
        return idx

    def _gravar(self, idx: _Indice, cab: dict[str, str]) -> None:
        arq = self._arquivo_do_indice(cab)
        if arq is None or self._dir is None:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = arq.with_suffix(".tmp")
            tmp.write_text(json.dumps({"header": cab, "paths": idx.caminhos, "sizes": idx.tamanhos,
                                       "postings": idx.postings}, ensure_ascii=False, separators=(",", ":")),
                           encoding="utf-8")
            os.replace(tmp, arq)                  # atômico: leitor nunca vê arquivo pela metade
            self.stats["cache_bytes"] = arq.stat().st_size
            self._podar(cab["root_id"], arq)
        except OSError:
            return

    def _podar(self, root_id: str, atual: Path) -> None:
        """Mantém os `INDICES_MANTIDOS` mais recentes DESTA raiz (o atual sempre) e apaga o resto e sobras `.tmp`."""
        if self._dir is None:
            return
        try:
            existentes = sorted(self._dir.glob(f"bm25-{root_id}-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            for velho in [p for p in existentes[INDICES_MANTIDOS:] if p != atual]:
                velho.unlink(missing_ok=True)
            for sobra in self._dir.glob(f"bm25-{root_id}-*.tmp"):
                if sobra.stat().st_mtime < time.time() - 3600:
                    sobra.unlink(missing_ok=True)
        except OSError:
            return

    def _indice_atual(self, revisao: str) -> tuple[_Indice, bool]:
        if self._indice is not None and self._indice.revisao == revisao:
            self.last_source = "memory"
            self.stats["memory_hits"] += 1
            return self._indice, True
        cab = self._cabecalho(revisao)
        t0 = time.perf_counter()
        idx = self._carregar(cab)
        if idx is not None:
            self.stats["load_ms"] = (time.perf_counter() - t0) * 1000
            self.stats["disk_hits"] += 1
            self.last_source = "disk"
            self._indice = idx
            return idx, True
        self.stats["misses"] += 1
        t0 = time.perf_counter()
        idx = self._construir(revisao)
        self.stats["build_ms"] = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        self._gravar(idx, cab)
        self.stats["save_ms"] = (time.perf_counter() - t0) * 1000
        self.last_source = "built"
        self._indice = idx
        return idx, False

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
        meta = {"tokens": len(consulta), "files_considered": len(idx.caminhos), "index_reused": reaproveitado,
                "index_source": self.last_source}
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
            for i in range(0, len(posting), 2):
                doc, tf = posting[i], posting[i + 1]
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
