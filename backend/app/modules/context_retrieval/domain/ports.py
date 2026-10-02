"""Portas do retrieval: o que um retriever, um provedor semântico, uma fonte de chunks e um sorvedouro de métricas são."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from .model import Chunk, ContextSelection, FilesReply, RegionsReply, RepoMap, RetrievalRequest


class ProviderLocality(str, Enum):
    """Onde o provedor executa. É o que a política de envio olha — nunca o NOME do provedor."""

    LOCAL = "local"    # roda na máquina (embeddings locais, Ollama): nada sai
    REMOTE = "remote"  # serviço de terceiros: o payload sai da máquina
    FAKE = "fake"      # determinístico, para teste


class Visibility(str, Enum):
    """O que se PROVOU sobre a visibilidade do repositório real. `UNKNOWN` é o valor de qualquer dúvida e bloqueia o envio."""

    PUBLIC = "public"
    PRIVATE = "private"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RepositoryProof:
    """Tudo o que se PROVOU sobre o conteúdo que o envio remoto levaria. O padrão de cada campo é "não provado".

    `remote`: o repositório real (todos os remotos) é público. `head_public`: o commit HEAD local existe no repositório público.
    `worktree_clean`: `True` só com nada modificado, removido, preparado ou não rastreado (nem ignorado) no worktree. Saber que o REMOTE é
    público não diz que o conteúdo LOCAL é: o universo do workspace inclui arquivo não rastreado e o HEAD local pode não ter
    sido publicado. Só os três juntos autorizam o envio.
    """

    remote: Visibility = Visibility.UNKNOWN
    head_public: bool = False
    worktree_clean: bool | None = None     # None = não deu para ler o git; False = sujo


class RepositoryVisibilityVerifier(Protocol):
    """Evidência INDEPENDENTE da configuração de que o que sai é público: remoto público, HEAD público, worktree limpo.

    A política não sabe como se prova (GitHub, git remote, ...): só pergunta. Erro, remoto ausente, remoto de host não
    suportado, resposta ambígua, git que falha: tudo é "não provado", nunca público.
    """

    def verify(self) -> RepositoryProof:
        """Pode ir à rede. Só é chamada quando o provedor é REMOTE e a configuração já pede envio de repositório público."""
        ...

    def peek(self) -> RepositoryProof:
        """Só o que já está provado e vigente (cache) mais o estado LOCAL do git; NUNCA vai à rede. Serve ao endpoint de status."""
        ...


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
    """Quem corta os chunks (etapa B) — só dos arquivos pedidos, com teto de quantidade e de bytes.

    `query`, quando dada, deixa a fonte escolher as janelas pela pergunta e repartir o teto entre os candidatos (J13); sem ela,
    vale o corte original, do início de cada arquivo, na ordem pedida."""

    def chunks_for(self, paths: Sequence[str], *, max_chunks: int, max_bytes: int, query: str | None = None) -> list[Chunk]: ...


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
