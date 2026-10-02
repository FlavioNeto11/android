"""Provedor semântico FALSO: determinístico, sem rede, forte o bastante para exercitar o pipeline A/B inteiro.

Não é um mock mudo. Ele pontua o mapa e os chunks por sobreposição de tokens (snake/camel, sem acento, sem
stopwords), aceita uma tabela de conceitos para simular acerto SEMÂNTICO sem coincidência lexical ("autenticação" →
`login`), respostas enlatadas e injeção de falha. Registra em `self.calls` tudo o que "enviou" — inclusive os
caminhos e o texto serializado — porque é um fake de teste: serve justamente para provar que caminho sensível e
segredo nunca chegaram a um provedor. Latência e custo são só REPORTADOS em `ProviderUsage`; nunca dorme de verdade.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Literal

from ...domain.errors import ProviderError
from ...domain.model import (
    Chunk, FileChoice, FilesReply, ProviderUsage, RegionChoice, RegionsReply, RepoMap,
)
from ...domain.ports import ProviderLocality, RepositoryProof, Visibility

#: Palavras que não distinguem arquivo nenhum (português e inglês). Lista curta de propósito: é um fake, não um NLP.
STOPWORDS = frozenset({
    "a", "o", "as", "os", "um", "uma", "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas", "por", "para",
    "com", "que", "se", "e", "ou", "ao", "aos", "onde", "como", "qual", "quais", "esta", "este", "isso", "ser", "foi",
    "the", "an", "of", "in", "on", "to", "is", "are", "and", "or", "for", "with", "where", "how", "what", "which",
    "does", "do", "it", "this", "that", "py", "ts", "tsx", "js",
})

_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
_NAO_ALNUM = re.compile(r"[^a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Tokens minúsculos, sem acento e sem stopword, partindo snake_case, CamelCase, caminho e ponto."""
    sem_acento = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    partido = _CAMEL.sub(r"\1 \2", sem_acento).lower()
    return [t for t in _NAO_ALNUM.split(partido) if len(t) >= 2 and t not in STOPWORDS]


def _serializar_chunks(chunks: Sequence[Chunk]) -> str:
    return "\n".join(f"### {c.path}:{c.start_line}-{c.end_line}\n{c.text}" for c in chunks)


class FixedVisibilityVerifier:
    """`RepositoryVisibilityVerifier` de teste: devolve sempre a mesma prova (ou levanta) e conta as chamadas.
    Nunca vai à rede nem lê o git. Por padrão HEAD público e worktree limpo; o teste suja um ou outro explicitamente."""

    def __init__(self, visibility: Visibility = Visibility.PUBLIC, *, raises: Exception | None = None,
                 head_public: bool = True, worktree_clean: bool = True) -> None:
        self.visibility = visibility
        self.raises = raises
        self.head_public = head_public
        self.worktree_clean = worktree_clean
        self.verify_calls = 0
        self.peek_calls = 0

    def _prova(self) -> RepositoryProof:
        return RepositoryProof(remote=self.visibility, head_public=self.head_public, worktree_clean=self.worktree_clean)

    def verify(self) -> RepositoryProof:
        self.verify_calls += 1
        if self.raises is not None:
            raise self.raises
        return self._prova()

    def peek(self) -> RepositoryProof:
        self.peek_calls += 1
        return self._prova()


class FakeSemanticProvider:
    name = "fake"

    def __init__(self, *, model: str = "fake-1", locality: ProviderLocality = ProviderLocality.FAKE,
                 concepts: Mapping[str, Sequence[str]] | None = None,
                 canned: Mapping[str, Sequence[str]] | None = None,
                 canned_regions: Mapping[str, Sequence[tuple[str, int, int]]] | None = None,
                 fail_with: ProviderError | None = None,
                 fail_on: Literal["files", "regions", "both"] = "both",
                 invalid_response: bool = False, empty: bool = False,
                 latency_ms: float = 0.0, cost_usd_per_call: float = 0.0, tokens_per_call: int = 0,
                 available_override: tuple[bool, str | None] | None = None) -> None:
        self.model = model
        self.locality = locality
        # A tabela de conceitos é normalizada uma vez: chave e sinônimos viram tokens, como a pergunta e o mapa.
        self._concepts: dict[str, frozenset[str]] = {}
        for chave, sinonimos in (concepts or {}).items():
            alvo = frozenset(t for s in sinonimos for t in tokenize(s))
            for t in tokenize(chave):
                self._concepts[t] = self._concepts.get(t, frozenset()) | alvo
        self._canned = {k.lower(): tuple(v) for k, v in (canned or {}).items()}
        self._canned_regions = {k.lower(): tuple(v) for k, v in (canned_regions or {}).items()}
        self.fail_with = fail_with
        self.fail_on = fail_on
        self.invalid_response = invalid_response
        self.empty = empty
        self.latency_ms = latency_ms
        self.cost_usd_per_call = cost_usd_per_call
        self.tokens_per_call = tokens_per_call
        self.available_override = available_override
        #: Um dict por chamada: o que "saiu" do processo. `paths` e `text` existem só neste fake de teste.
        self.calls: list[dict[str, object]] = []

    # ------------------------------------------------------------------ porta
    def available(self) -> tuple[bool, str | None]:
        return self.available_override if self.available_override is not None else (True, None)

    def select_files(self, query: str, repo_map: RepoMap, *, max_files: int, timeout_s: float) -> FilesReply:
        texto = repo_map.render()
        self._registrar("A", query, n=len(repo_map.entries), texto=texto,
                        paths=[e.path for e in repo_map.entries], limite=max_files, timeout_s=timeout_s)
        self._falhar("files")
        usage = self._usage()
        if self.empty:
            return FilesReply((), usage)
        if self.invalid_response:
            return FilesReply(tuple(FileChoice(f"inexistente/fantasma_{i}.py", 0.9 - i / 100)
                                    for i in range(min(max_files, 3))), usage)
        enlatado = self._enlatado(query, self._canned)
        if enlatado is not None:
            return FilesReply(tuple(FileChoice(p, 1.0 - i / 100) for i, p in enumerate(enlatado[:max_files])), usage)
        pontos = [(self._pontuar(query, f"{e.path} {' '.join(e.symbols)} {e.summary}"), e.path)
                  for e in repo_map.entries]
        return FilesReply(self._melhores(pontos, max_files, FileChoice), usage)

    def select_regions(self, query: str, chunks: Sequence[Chunk], *, max_regions: int,
                       timeout_s: float) -> RegionsReply:
        self._registrar("B", query, n=len(chunks), texto=_serializar_chunks(chunks),
                        paths=sorted({c.path for c in chunks}), limite=max_regions, timeout_s=timeout_s)
        self._falhar("regions")
        usage = self._usage()
        if self.empty:
            return RegionsReply((), usage)
        if self.invalid_response:
            return RegionsReply((RegionChoice("inexistente/fantasma.py", 1, 5, 0.9),), usage)
        enlatado = self._enlatado(query, self._canned_regions)
        if enlatado is not None:
            return RegionsReply(tuple(RegionChoice(p, s, e, 1.0 - i / 100)
                                      for i, (p, s, e) in enumerate(enlatado[:max_regions])), usage)
        pontos = [(self._pontuar(query, f"{c.path} {c.text}"), c) for c in chunks]
        ordenado = sorted((p for p in pontos if p[0] > 0), key=lambda p: (-p[0], p[1].path, p[1].start_line))
        return RegionsReply(tuple(RegionChoice(c.path, c.start_line, c.end_line, round(s, 4))
                                  for s, c in ordenado[:max_regions]), usage)

    # ------------------------------------------------------------------ internos
    def _registrar(self, stage: str, query: str, *, n: int, texto: str, paths: list[str], limite: int,
                   timeout_s: float) -> None:
        self.calls.append({"stage": stage, "query": query, "n": n, "bytes": len(texto.encode("utf-8")),
                           "paths": paths, "text": texto, "limit": limite, "timeout_s": timeout_s})

    def _falhar(self, etapa: Literal["files", "regions"]) -> None:
        if self.fail_with is not None and self.fail_on in (etapa, "both"):
            raise self.fail_with

    def _usage(self) -> ProviderUsage:
        return ProviderUsage(input_tokens=self.tokens_per_call, output_tokens=0,
                             cost_usd=self.cost_usd_per_call, latency_ms=self.latency_ms)

    @staticmethod
    def _enlatado(query: str, tabela: Mapping[str, object]) -> object:
        q = query.lower()
        for trecho, resposta in tabela.items():
            if trecho in q:
                return resposta
        return None

    def _pontuar(self, query: str, alvo: str) -> float:
        """Fração dos termos da pergunta que aparecem no alvo, com os sinônimos do conceito contando como acerto."""
        termos = set(tokenize(query))
        if not termos:
            return 0.0
        tokens_alvo = set(tokenize(alvo))
        acertos = 0
        for t in termos:
            if t in tokens_alvo or (self._concepts.get(t, frozenset()) & tokens_alvo):
                acertos += 1
        return acertos / len(termos)

    @staticmethod
    def _melhores(pontos: list[tuple[float, str]], limite: int, tipo: object) -> tuple[object, ...]:
        ordenado = sorted((p for p in pontos if p[0] > 0), key=lambda p: (-p[0], p[1]))
        return tuple(tipo(path, round(score, 4)) for score, path in ordenado[:limite])
