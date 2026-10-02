"""Tipos do retrieval de contexto de código: pedido, seleção, mapa do repositório e pacote de contexto.

Só dados e regras puras: nada aqui abre arquivo, rede ou banco. Os retrievers e os provedores semânticos (portas em
`ports.py`) falam estes tipos, e é por isso que a regra híbrida não sabe qual provedor está por trás.

Linhas são 1-based e inclusivas nas duas pontas. Caminhos são relativos à raiz do repositório, com `/`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

#: Versão do conjunto retriever+regra híbrida. Entra na chave dos caches: mudar a regra invalida o que foi guardado.
RETRIEVAL_VERSION = "3"  # 3: envio remoto exige worktree limpo e HEAD público; 2: mapa da etapa A omite entrada com segredo


class RetrievalMode(str, Enum):
    DISABLED = "disabled"      # o pipeline antigo segue; o retrieval nem é consultado
    LOCAL_ONLY = "local_only"  # léxico + BM25, sem provedor semântico
    SHADOW = "shadow"          # o semântico roda para medição; o contexto entregue é o local
    HYBRID = "hybrid"          # o semântico participa da seleção


class FallbackReason(str, Enum):
    """Por que o caminho semântico não valeu e o resultado veio do local. Valor curto e fechado (rótulo de métrica)."""

    PROVIDER_UNAVAILABLE = "provider_unavailable"
    KEY_MISSING = "key_missing"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    OVERLOADED = "overloaded"
    PROVIDER_OFFLINE = "provider_offline"
    PROVIDER_ERROR = "provider_error"
    INVALID_RESPONSE = "invalid_response"
    BUDGET_EXCEEDED = "budget_exceeded"
    PRIVACY_BLOCK = "privacy_block"
    SECRET_BLOCK = "secret_block"
    EMPTY_SEMANTIC = "empty_semantic"
    NO_PROVIDER = "no_provider"


@dataclass(frozen=True)
class Region:
    """Janela de um arquivo. `text` é opcional e fica fora da igualdade: duas janelas são a mesma pelo lugar."""

    path: str
    start_line: int
    end_line: int
    text: str | None = field(default=None, compare=False, repr=False)

    def key(self) -> tuple[str, int, int]:
        return (self.path, self.start_line, self.end_line)


@dataclass(frozen=True)
class FileHit:
    """Um arquivo candidato. `score` só é comparável dentro do mesmo `source`."""

    path: str
    score: float
    source: str
    matched_terms: int = 0
    regions: tuple[Region, ...] = ()


@dataclass(frozen=True)
class Budget:
    """Teto técnico das chamadas ao provedor semântico. Estourar um teto manda o pedido para o local, nunca falha."""

    max_calls_per_request: int = 2
    max_calls_per_session: int = 40
    max_input_tokens: int = 24_000
    max_cost_usd: float = 0.05
    timeout_ms: int = 5_000


@dataclass(frozen=True)
class PayloadLimits:
    """Teto do que sai da máquina por chamada. Nenhum payload semântico é montado sem estes quatro limites."""

    max_map_files: int = 400    # entradas do mapa na etapa A
    max_candidate_files: int = 8  # arquivos que a etapa A pode escolher (e de onde a B tira chunks)
    max_chunks: int = 24        # chunks na etapa B
    max_bytes: int = 48_000     # bytes de payload por chamada


@dataclass(frozen=True)
class RetrievalRequest:
    query: str
    root: Path
    revision: str
    scope: tuple[str, ...] = ()
    top_k: int = 5


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0


@dataclass(frozen=True)
class ContextSelection:
    """O que um retriever escolheu, mais a metadata NÃO sensível de como chegou lá (nunca o código enviado)."""

    selected_files: tuple[FileHit, ...]
    selected_regions: tuple[Region, ...]
    source: str
    confidence: float | None = None
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    input_tokens: int = 0
    fallback_used: bool = False
    fallback_reason: FallbackReason | None = None
    warnings: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)

    @staticmethod
    def empty(source: str, **kw: object) -> "ContextSelection":
        return ContextSelection(selected_files=(), selected_regions=(), source=source, **kw)


# ---------------------------------------------------------------- mapa do repositório (etapa A)
@dataclass(frozen=True)
class MapEntry:
    path: str
    language: str
    size: int
    symbols: tuple[str, ...] = ()
    summary: str = ""


@dataclass(frozen=True)
class RepoMap:
    """Resumo estrutural pequeno do repositório. Nunca conteúdo de arquivo: caminho, linguagem, tamanho e símbolos."""

    revision: str
    entries: tuple[MapEntry, ...]

    def render(self, max_entries: int | None = None) -> str:
        linhas: list[str] = []
        for e in self.entries[:max_entries]:
            simbolos = ", ".join(e.symbols)
            linhas.append(f"{e.path} [{e.language}, {e.size}B]" + (f": {simbolos}" if simbolos else "")
                          + (f" — {e.summary}" if e.summary else ""))
        return "\n".join(linhas)


# ---------------------------------------------------------------- chunk (etapa B)
@dataclass(frozen=True)
class Chunk:
    path: str
    start_line: int
    end_line: int
    text: str = field(repr=False)

    def region(self, *, with_text: bool = False) -> Region:
        return Region(self.path, self.start_line, self.end_line, self.text if with_text else None)


# ---------------------------------------------------------------- respostas do provedor
@dataclass(frozen=True)
class FileChoice:
    path: str
    score: float


@dataclass(frozen=True)
class FilesReply:
    choices: tuple[FileChoice, ...]
    usage: ProviderUsage = ProviderUsage()


@dataclass(frozen=True)
class RegionChoice:
    path: str
    start_line: int
    end_line: int
    score: float = 0.0


@dataclass(frozen=True)
class RegionsReply:
    choices: tuple[RegionChoice, ...]
    usage: ProviderUsage = ProviderUsage()


# ---------------------------------------------------------------- pacote de contexto
@dataclass(frozen=True)
class ContextPack:
    """Saída reutilizável: serve a Claude, a outros modelos, a subagentes, a habilidades e ao planejamento."""

    query: str
    revision: str
    mode: RetrievalMode
    origin: str
    files: tuple[FileHit, ...]
    regions: tuple[Region, ...]
    confidence: float | None
    budget: dict[str, object]
    metadata: dict[str, object]
    warnings: tuple[str, ...] = ()
    retrieval_version: str = RETRIEVAL_VERSION

    def to_dict(self, *, with_text: bool = False) -> dict[str, object]:
        def regiao(r: Region) -> dict[str, object]:
            d: dict[str, object] = {"path": r.path, "start_line": r.start_line, "end_line": r.end_line}
            if with_text and r.text is not None:
                d["text"] = r.text
            return d

        return {
            "query": self.query, "revision": self.revision, "mode": self.mode.value, "origin": self.origin,
            "retrieval_version": self.retrieval_version, "confidence": self.confidence,
            "files": [{"path": f.path, "score": round(f.score, 4), "source": f.source} for f in self.files],
            "regions": [regiao(r) for r in self.regions],
            "budget": self.budget, "metadata": self.metadata, "warnings": list(self.warnings),
        }
