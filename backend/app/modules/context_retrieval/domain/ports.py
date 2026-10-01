"""Portas do retrieval: o que um retriever, um provedor semântico, uma fonte de chunks e um sorvedouro de métricas são."""
from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import Protocol, runtime_checkable

from .model import Chunk, ContextSelection, FilesReply, RegionsReply, RepoMap, RetrievalRequest


class ProviderLocality(str, Enum):
    """Onde o provedor executa. É o que a política de envio olha — nunca o NOME do provedor."""

    LOCAL = "local"    # roda na máquina (embeddings locais, Ollama): nada sai
    REMOTE = "remote"  # serviço de terceiros: o payload sai da máquina
    FAKE = "fake"      # determinístico, para teste


@runtime_checkable
class ContextRetriever(Protocol):
    name: str

    def retrieve(self, request: RetrievalRequest) -> ContextSelection: ...


@runtime_checkable
class SemanticProvider(Protocol):
    """Contrato mínimo de um provedor semântico: escolher arquivos num mapa e regiões em chunks.

    Só serializa, envia, interpreta, mede e traduz erro (`domain/errors.py`). A regra híbrida NÃO mora aqui.
    Os métodos são bloqueantes (o chamador async usa `asyncio.to_thread`). Levantam `ProviderError` e subclasses.
    """

    name: str
    model: str
    locality: ProviderLocality

    def available(self) -> tuple[bool, str | None]:
        """`(True, None)` ou `(False, motivo)` — chave ausente, serviço desligado. Nunca faz rede."""
        ...

    def select_files(self, query: str, repo_map: RepoMap, *, max_files: int, timeout_s: float) -> FilesReply: ...

    def select_regions(self, query: str, chunks: Sequence[Chunk], *, max_regions: int,
                       timeout_s: float) -> RegionsReply: ...


class ChunkSource(Protocol):
    """Quem corta os chunks (etapa B) — só dos arquivos pedidos, com teto de quantidade e de bytes."""

    def chunks_for(self, paths: Sequence[str], *, max_chunks: int, max_bytes: int) -> list[Chunk]: ...


class MapSource(Protocol):
    def repo_map(self) -> RepoMap: ...


class ResponseCache(Protocol):
    """Cache da resposta semântica (JSON, por revisão). Quem implementa é a infraestrutura; a aplicação só vê esta porta."""

    def key(self, *, revision: str, query: str, scope: tuple[str, ...], provider: str, model: str, stage: str,
            extra: str = "") -> str: ...

    def get(self, key: str) -> dict[str, object] | None: ...

    def put(self, key: str, value: dict[str, object]) -> None: ...


class MetricsSink(Protocol):
    def record(self, event: dict[str, object]) -> None:
        """Evento já saneado (sem código, sem segredo, sem a pergunta crua). Não pode levantar."""
        ...
