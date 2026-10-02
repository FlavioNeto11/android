"""Composição: da configuração (`context_retrieval:`) ao serviço pronto. É o único lugar que conhece as peças concretas.

Com o retrieval desligado devolve `DisabledContextRetrieval` SEM abrir o repositório, o git, o cache ou o provedor:
mergear este módulo não pode custar nada a quem não ligou. A chave do provedor semântico vem só do ambiente
(`EnvSettings.typesafe_api_key`, o mesmo padrão das chaves de IA) e só é passada ao adaptador; nunca ao arquivo de
configuração, ao cache, às métricas ou ao pacote de contexto.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path

from ...config import Config, EnvSettings
from .adapters.github_visibility import GitHubVisibilityVerifier
from .application.budget import BudgetLedger
from .application.hybrid import HybridRetriever
from .application.local import LocalRetriever
from .application.semantic import SemanticRetriever
from .application.service import ContextRetrievalService, DisabledContextRetrieval
from .domain.model import Budget, PayloadLimits, RetrievalMode
from .domain.policy import ExternalContextPolicy, RepositoryClass
from .domain.ports import MetricsSink, ProviderLocality, RepositoryVisibilityVerifier, SemanticProvider, Visibility
from .domain.sensitive import SensitivePathMatcher
from .infrastructure.bm25 import BM25Retriever
from .infrastructure.cache import SemanticCache
from .infrastructure.chunker import Chunker
from .infrastructure.lexical import LexicalRetriever
from .infrastructure.metrics import RetrievalMetrics, resumir
from .infrastructure.providers.factory import build_provider
from .infrastructure.repomap import RepoMapProvider
from .infrastructure.workspace import Workspace


def pasta_de_dados(cfg: Config) -> Path:
    return cfg.path(cfg.file.paths.data_dir) / cfg.file.context_retrieval.cache.directory


class _AmbienteDoProvedor(Mapping[str, str]):
    """O que o adaptador pode ler do ambiente: só a chave do Jev, e só quando alguém a lê.

    Guarda o `EnvSettings` (cujo campo é `SecretStr`), não a string: o valor só existe durante o `get`, e nem `vars()` do
    adaptador nem o `repr` deste objeto o mostram.
    """

    _CHAVE = "TYPESAFE_API_KEY"

    def __init__(self, env: EnvSettings) -> None:
        self._env = env

    def __getitem__(self, chave: str) -> str:
        if chave == self._CHAVE and self._env.typesafe_api_key is not None:
            return self._env.typesafe_api_key.get_secret_value()
        raise KeyError(chave)

    def __iter__(self) -> Iterator[str]:
        if self._env.typesafe_api_key is not None:
            yield self._CHAVE

    def __len__(self) -> int:
        return 1 if self._env.typesafe_api_key is not None else 0

    def __repr__(self) -> str:
        return "<ambiente do provedor semântico>"


def ambiente_do_provedor(cfg: Config) -> Mapping[str, str]:
    return _AmbienteDoProvedor(cfg.env)


def criar_provedor(cfg: Config) -> SemanticProvider | None:
    s = cfg.file.context_retrieval.semantic
    return build_provider(s.provider, model=s.model, env=ambiente_do_provedor(cfg))


def criar_verificador(cfg: Config, raiz: Path, locality: ProviderLocality) -> RepositoryVisibilityVerifier | None:
    """A prova de visibilidade só existe para provedor REMOTE com a configuração já pedindo repositório público: em qualquer outro
    caso ela nunca é construída (nada de git nem de rede para quem é local, fake ou privado)."""
    s = cfg.file.context_retrieval.semantic
    if locality is not ProviderLocality.REMOTE or s.repository_class != "public" or not s.allow_public:
        return None
    return GitHubVisibilityVerifier(raiz, store_dir=pasta_de_dados(cfg))


def build_service(cfg: Config, *, root: Path | None = None, mode: RetrievalMode | None = None,
                  provider: SemanticProvider | None = None, sink: MetricsSink | None = None,
                  visibility: RepositoryVisibilityVerifier | None = None,
                  ) -> ContextRetrievalService | DisabledContextRetrieval:
    """`mode`, `provider` e `visibility` só existem para teste e CLI; sem eles, vale o que a configuração diz."""
    cc = cfg.file.context_retrieval
    efetivo = mode or RetrievalMode(cc.effective_mode)
    if efetivo is RetrievalMode.DISABLED:
        return DisabledContextRetrieval()

    raiz = (root or cfg.root).resolve()
    dados = pasta_de_dados(cfg)
    sensivel = SensitivePathMatcher(cc.sensitive_paths)
    ws = Workspace(raiz, sensitive=sensivel)
    local = LocalRetriever(
        LexicalRetriever(ws, use_ripgrep=cc.lexical.use_ripgrep, window_lines=cc.lexical.window_lines,
                         ripgrep_path=cc.lexical.ripgrep_path),
        BM25Retriever(ws, k1=cc.bm25.k1, b=cc.bm25.b, cache_dir=dados / "bm25" if cc.cache.enabled else None))

    s = cc.semantic
    budget = Budget(max_calls_per_request=s.max_calls, max_calls_per_session=s.max_calls_per_session,
                    max_input_tokens=s.max_input_tokens, max_cost_usd=s.max_cost_usd, timeout_ms=s.timeout_ms)
    hibrido: HybridRetriever | None = None
    nome, modelo = "none", ""
    if efetivo in (RetrievalMode.HYBRID, RetrievalMode.SHADOW):
        prov = provider if provider is not None else criar_provedor(cfg)
        if prov is not None:
            nome, modelo = prov.name, prov.model
            politica = ExternalContextPolicy(repository=RepositoryClass(s.repository_class), locality=prov.locality,
                                             allow_public=s.allow_public, sensitive=sensivel,
                                             verifier=visibility if visibility is not None
                                             else criar_verificador(cfg, raiz, prov.locality))
            semantico = SemanticRetriever(
                provider=prov, policy=politica,
                map_source=RepoMapProvider(ws, cache_dir=dados / "repomap"),
                chunk_source=Chunker(ws), ledger=BudgetLedger(budget),
                cache=SemanticCache(dados / "semantic", enabled=cc.cache.enabled),
                limits=PayloadLimits(max_map_files=s.max_map_files, max_candidate_files=s.max_candidate_files,
                                     max_chunks=s.max_chunks, max_bytes=s.max_bytes))
            hibrido = HybridRetriever(local=local, semantic=semantico, lexical_preserve=cc.lexical_preserve)
    return ContextRetrievalService(
        mode=efetivo, root=raiz, top_k=cc.top_k, local=local, hybrid=hibrido, revision=ws.revision,
        read_text=ws.read_text, pinned=ws.pinned, sink=sink if sink is not None else RetrievalMetrics(dados),
        budget=budget, provider=nome, model=modelo)


def estado_do_retrieval(cfg: Config) -> dict[str, object]:
    """Para a API de status: configuração efetiva, disponibilidade do provedor (sem rede) e o resumo das métricas."""
    cc = cfg.file.context_retrieval
    prov = criar_provedor(cfg) if cc.semantic.provider != "none" else None
    disponivel, motivo = prov.available() if prov is not None else (False, "no_provider")
    locality = prov.locality if prov is not None else ProviderLocality.REMOTE
    politica = ExternalContextPolicy(
        repository=RepositoryClass(cc.semantic.repository_class), locality=locality,
        allow_public=cc.semantic.allow_public, verifier=criar_verificador(cfg, cfg.root.resolve(), locality))
    configurado = politica.configured_for_remote()
    envio, prova = politica.peek_repository()      # `peek`: só o que já está provado + git local; o status nunca vai à rede
    if prova is None:
        estado = "not_applicable" if locality is not ProviderLocality.REMOTE or not configurado.allowed else "unverified"
    else:
        estado = prova.remote.value if prova.remote is not Visibility.UNKNOWN else "unverified"
    remoto_ok = prova is not None and prova.remote is Visibility.PUBLIC
    return {
        "enabled": cc.enabled, "mode": cc.effective_mode, "top_k": cc.top_k,
        "provider": {"name": cc.semantic.provider, "model": prov.model if prov is not None else "",
                     "available": disponivel, "unavailable_reason": None if disponivel else motivo},
        "external_send": {"allowed": envio.allowed, "reason": envio.reason,
                          "repository_class": cc.semantic.repository_class,
                          "configured_for_remote": configurado.allowed and locality is ProviderLocality.REMOTE,
                          "visibility_verified": remoto_ok, "visibility": estado,
                          "remote_visibility_verified": remoto_ok,
                          "head_public_verified": prova is not None and prova.head_public,
                          "worktree_clean": None if prova is None else prova.worktree_clean},
        "budget": {"timeout_ms": cc.semantic.timeout_ms, "max_calls": cc.semantic.max_calls,
                   "max_cost_usd": cc.semantic.max_cost_usd},
        "summary": resumir(RetrievalMetrics(pasta_de_dados(cfg)).recentes(500)),
    }
