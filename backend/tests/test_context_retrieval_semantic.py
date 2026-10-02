"""Provedores semânticos (Fake e Jev), fábrica e `SemanticRetriever` (etapas A/B), tudo SEM rede.

Prova `simulated`: o Jev só fala com `httpx.MockTransport`, e o fixture autouse abaixo derruba qualquer tentativa de
conexão de socket (REAL_JEV_NETWORK_CALLS = 0). Nada aqui prova o serviço real da TypeSafe.
Segredos das fixtures são valores obviamente falsos montados em runtime.
"""
from __future__ import annotations

import dataclasses
import json
import socket
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.modules.context_retrieval.application.budget import BudgetLedger
from app.modules.context_retrieval.application.semantic import SemanticRetriever
from app.modules.context_retrieval.domain import policy as policy_mod
from app.modules.context_retrieval.domain.errors import (
    ProviderError, ProviderInvalidResponse, ProviderOffline, ProviderOptionLimit, ProviderOverloaded, ProviderRateLimited,
    ProviderTimeout, ProviderUnavailable,
)
from app.modules.context_retrieval.domain.model import (
    Budget, Chunk, FallbackReason, MapEntry, PayloadLimits, ProviderUsage, RepoMap, RetrievalRequest,
)
from app.modules.context_retrieval.domain.policy import ExternalContextPolicy, RepositoryClass
from app.modules.context_retrieval.domain.ports import ContextRetriever, ProviderLocality, SemanticProvider, Visibility
from app.modules.context_retrieval.infrastructure.cache import SemanticCache
from app.modules.context_retrieval.infrastructure.providers.factory import build_provider
from app.modules.context_retrieval.infrastructure.providers.fake import (FakeSemanticProvider, FixedVisibilityVerifier,
                                                                          tokenize)
from app.modules.context_retrieval.adapters.jev import (
    DEFAULT_MODEL, ID_NENHUMA, JevSemanticProvider, MAX_OPCOES, PRICE_USD_PER_MTOK_INPUT,
)

REMOTE, LOCAL, FAKE = ProviderLocality.REMOTE, ProviderLocality.LOCAL, ProviderLocality.FAKE
CHAVE_FALSA = "chave-falsa-" + "x" * 16  # só para provar que não vaza; não é credencial
CORPO_SENSIVEL_DO_SERVIDOR = "corpo-de-erro-do-servidor-" + "y" * 8


# ---------------------------------------------------------------- rede proibida
@pytest.fixture(autouse=True)
def sem_rede(monkeypatch: pytest.MonkeyPatch):
    """Qualquer conexão de socket nesta rodada é bug: o Jev só pode falar com o MockTransport."""
    tentativas: list[str] = []

    def proibido(*a: Any, **k: Any):
        tentativas.append("connect")
        raise AssertionError("REAL_JEV_NETWORK_CALLS: conexao de rede proibida nos testes")

    monkeypatch.setattr(socket.socket, "connect", proibido)
    monkeypatch.setattr(socket, "create_connection", proibido)
    yield
    assert tentativas == [], "houve tentativa de conexao de rede (REAL_JEV_NETWORK_CALLS deveria ser 0)"


# ---------------------------------------------------------------- mini-repositório sintético
def _e(path: str, symbols: tuple[str, ...] = (), summary: str = "", lang: str = "python") -> MapEntry:
    return MapEntry(path=path, language=lang, size=900, symbols=symbols, summary=summary)


ENTRADAS = (
    _e("app/auth/login.py", ("verify_password", "create_session", "LoginForm")),
    _e("app/billing/invoice.py", ("generate_invoice", "InvoiceTotal"), "calculo de cobranca"),
    _e("app/utils/strings.py", ("slugify",)),
    _e("app/cache/lru.py", ("LruCache",)),
    _e("data/accounts.json", (), "contas do parque", "json"),   # pasta sensível na raiz
    _e(".env", (), "variaveis", "env"),                         # nome sensível
    _e("config.yaml", (), "config", "yaml"),                    # nome sensível
)
SENSIVEIS = ("data/accounts.json", ".env", "config.yaml")
NAO_SENSIVEIS = 4


def _segredo_duro() -> str:
    return "-----BEGIN " + "RSA PRIVATE KEY-----"


def _segredo_mole() -> str:
    return "db_" + "password=" + "valor-ficticio-123"


def _chunks(login_segundo: str = "def create_session(user):\n    return new_token()") -> dict[str, list[Chunk]]:
    return {
        "app/auth/login.py": [
            Chunk("app/auth/login.py", 1, 20, "def verify_password(user, raw):\n    return compare(user, raw)"),
            Chunk("app/auth/login.py", 21, 40, login_segundo),
        ],
        "app/billing/invoice.py": [
            Chunk("app/billing/invoice.py", 1, 30, "def generate_invoice(order):\n    return total(order)"),
        ],
        "app/utils/strings.py": [Chunk("app/utils/strings.py", 1, 10, "def slugify(text):\n    return text")],
    }


class MapaFalso:
    def __init__(self, entradas: Sequence[MapEntry] = ENTRADAS, revision: str = "rev1") -> None:
        self.entradas, self.revision, self.chamadas = tuple(entradas), revision, 0

    def repo_map(self) -> RepoMap:
        self.chamadas += 1
        return RepoMap(self.revision, self.entradas)


class ChunksFalsos:
    """Devolve TUDO dos caminhos pedidos, ignorando os tetos: quem impõe o limite de payload é o retriever."""

    def __init__(self, por_caminho: dict[str, list[Chunk]] | None = None) -> None:
        self.por_caminho = _chunks() if por_caminho is None else por_caminho
        self.pedidos: list[list[str]] = []
        self.consultas: list[str | None] = []

    def chunks_for(self, paths: Sequence[str], *, max_chunks: int, max_bytes: int, query: str | None = None) -> list[Chunk]:
        self.pedidos.append(list(paths))
        self.consultas.append(query)
        return [c for p in paths for c in self.por_caminho.get(p, [])]


def _politica(repo: RepositoryClass = RepositoryClass.PUBLIC, loc: ProviderLocality = REMOTE,
              allow_public: bool = True, visibilidade: Visibility = Visibility.PUBLIC) -> ExternalContextPolicy:
    return ExternalContextPolicy(repository=repo, locality=loc, allow_public=allow_public,
                                 verifier=FixedVisibilityVerifier(visibilidade))


class Montagem:
    def __init__(self, provider: SemanticProvider | None, *, politica: ExternalContextPolicy | None = None,
                 mapa: MapaFalso | None = None, chunks: ChunksFalsos | None = None, budget: Budget | None = None,
                 cache: SemanticCache | None = None, limits: PayloadLimits | None = None) -> None:
        self.provider = provider
        self.mapa = mapa or MapaFalso()
        self.chunks = chunks or ChunksFalsos()
        self.ledger = BudgetLedger(budget or Budget())
        self.retriever = SemanticRetriever(
            provider=provider, policy=politica or _politica(), map_source=self.mapa, chunk_source=self.chunks,
            ledger=self.ledger, cache=cache or SemanticCache(None), limits=limits or PayloadLimits())

    def pedir(self, query: str = "onde verify_password valida a senha no login", *, revision: str = "rev1",
              scope: tuple[str, ...] = (), top_k: int = 5):
        return self.retriever.retrieve(RetrievalRequest(query=query, root=Path("."), revision=revision, scope=scope,
                                                        top_k=top_k))


def _fake(**kw: Any) -> FakeSemanticProvider:
    kw.setdefault("locality", REMOTE)  # REMOTE exercita a política inteira
    return FakeSemanticProvider(**kw)


# ---------------------------------------------------------------- Fake: pipeline A/B
def test_pipeline_feliz_com_fake():
    fake = _fake(cost_usd_per_call=0.01, tokens_per_call=100)
    m = Montagem(fake)
    sel = m.pedir()
    assert isinstance(m.retriever, ContextRetriever) and m.retriever.name == "semantic"
    assert not sel.fallback_used and sel.fallback_reason is None and sel.source == "semantic"
    assert sel.selected_files[0].path == "app/auth/login.py"
    assert sel.selected_files[0].source == "semantic:fake"
    assert sel.selected_regions and sel.selected_regions[0].path == "app/auth/login.py"
    assert (sel.selected_regions[0].start_line, sel.selected_regions[0].end_line) == (1, 20)
    assert all(r.text is None for r in sel.selected_regions)  # nunca código na seleção
    assert sel.confidence is None
    assert sel.cost_usd == pytest.approx(0.02) and sel.input_tokens == 200
    assert [c["stage"] for c in fake.calls] == ["A", "B"]
    assert sel.metadata == {"provider": "fake", "model": "fake-1", "stage_a_cache": "miss", "stage_b_cache": "miss",
                            "files_considered": NAO_SENSIVEIS, "chunks_sent": 2, "calls": 2, "chunks_dropped_soft": 0}
    assert sel.metadata["files_considered"] == NAO_SENSIVEIS
    assert sel.latency_ms >= 0


def test_top_k_limita_arquivos_e_regioes():
    fake = _fake(canned={"coisa": ["app/auth/login.py", "app/billing/invoice.py", "app/utils/strings.py"]})
    sel = Montagem(fake).pedir("qualquer coisa", top_k=2)
    assert [f.path for f in sel.selected_files] == ["app/auth/login.py", "app/billing/invoice.py"]
    assert len(sel.selected_regions) <= 2


def test_semantica_sem_coincidencia_lexical_via_conceitos():
    pergunta = "como o sistema valida a identidade do usuario"
    sem = Montagem(_fake()).pedir(pergunta)
    assert sem.fallback_used and sem.fallback_reason is FallbackReason.EMPTY_SEMANTIC  # nenhum termo em comum

    fake = _fake(concepts={"identidade": ["login", "password"]})
    com = Montagem(fake).pedir(pergunta)
    assert not com.fallback_used and com.selected_files[0].path == "app/auth/login.py"


def test_tokenizacao_do_fake():
    assert tokenize("Verificação da SenhaDoUsuário em user_session.py") == [
        "verificacao", "senha", "usuario", "user", "session"]


def test_resposta_alucinada_cai_com_invalid_response():
    fake = _fake(invalid_response=True)
    sel = Montagem(fake).pedir()
    assert sel.fallback_used and sel.fallback_reason is FallbackReason.INVALID_RESPONSE
    assert sel.selected_files == () and [c["stage"] for c in fake.calls] == ["A"]


def test_resposta_vazia_cai_com_empty_semantic():
    sel = Montagem(_fake(empty=True)).pedir()
    assert sel.fallback_used and sel.fallback_reason is FallbackReason.EMPTY_SEMANTIC


def test_alucinacao_parcial_descarta_so_o_invalido():
    fake = _fake(canned={"coisa": ["inexistente/x.py", "app/auth/login.py", ".env"]})
    sel = Montagem(fake).pedir("qualquer coisa")
    assert [f.path for f in sel.selected_files] == ["app/auth/login.py"]  # o `.env` não estava no mapa filtrado


def test_regiao_inventada_na_etapa_b_e_descartada():
    fake = _fake(canned={"coisa": ["app/auth/login.py"]}, canned_regions={"coisa": [("app/auth/login.py", 5, 6),
                                                                                       ("app/auth/login.py", 21, 40)]})
    sel = Montagem(fake).pedir("qualquer coisa")
    assert [(r.start_line, r.end_line) for r in sel.selected_regions] == [(21, 40)]


def test_etapa_b_so_invalida_preserva_a():
    fake = _fake(canned={"coisa": ["app/auth/login.py"]})
    fake.invalid_response = False
    m = Montagem(fake)
    # B responde com região inventada e nenhuma válida
    fake._canned_regions = {"coisa": (("app/x.py", 1, 2),)}
    sel = m.pedir("qualquer coisa")
    assert not sel.fallback_used and sel.selected_files and sel.selected_regions == ()
    assert sel.warnings == ("stage_b_failed:invalid_response",)
    assert sel.metadata["stage_b_reason"] == "invalid_response"


@pytest.mark.parametrize("erro,razao", [
    (ProviderTimeout("t"), FallbackReason.TIMEOUT),
    (ProviderRateLimited("r"), FallbackReason.RATE_LIMITED),
    (ProviderOverloaded("o"), FallbackReason.OVERLOADED),
    (ProviderOffline("off"), FallbackReason.PROVIDER_OFFLINE),
    (ProviderUnavailable("u"), FallbackReason.PROVIDER_UNAVAILABLE),
    (ProviderInvalidResponse("i"), FallbackReason.INVALID_RESPONSE),
    (ProviderError("e"), FallbackReason.PROVIDER_ERROR),
])
def test_falha_do_provedor_na_etapa_a_vira_fallback(erro: ProviderError, razao: FallbackReason):
    sel = Montagem(_fake(fail_with=erro)).pedir()
    assert sel.fallback_used and sel.fallback_reason is razao and sel.selected_files == ()


def test_excecao_inesperada_do_provedor_tambem_nao_levanta():
    class Quebrado(FakeSemanticProvider):
        def select_files(self, *a: Any, **k: Any):
            raise RuntimeError("bug do provedor")

    sel = Montagem(Quebrado(locality=REMOTE)).pedir()
    assert sel.fallback_used and sel.fallback_reason is FallbackReason.PROVIDER_ERROR
    assert "bug do provedor" not in repr(sel)  # nome do tipo, nunca a mensagem


def test_falha_na_etapa_b_preserva_a_sem_fallback():
    fake = _fake(fail_with=ProviderTimeout("t"), fail_on="regions")
    sel = Montagem(fake).pedir()
    assert not sel.fallback_used and sel.selected_files and sel.selected_regions == ()
    assert sel.warnings == ("stage_b_failed:timeout",) and sel.metadata["stage_b_reason"] == "timeout"


def test_falha_da_fonte_de_chunks_preserva_a():
    class Quebra:
        def chunks_for(self, *a: Any, **k: Any):
            raise OSError("disco")

    m = Montagem(_fake())
    m.retriever.chunk_source = Quebra()  # type: ignore[assignment]
    sel = m.pedir()
    assert not sel.fallback_used and sel.selected_files and sel.selected_regions == ()
    assert sel.warnings == ("stage_b_failed:chunk_source_error",)


def test_falha_do_mapa_nunca_levanta():
    class MapaQuebrado:
        def repo_map(self) -> RepoMap:
            raise RuntimeError("repo ilegivel")

    m = Montagem(_fake())
    m.retriever.map_source = MapaQuebrado()  # type: ignore[assignment]
    sel = m.pedir()
    assert sel.fallback_used and sel.fallback_reason is FallbackReason.PROVIDER_ERROR


def test_latencia_e_custo_do_fake_sao_so_reportados():
    import time
    fake = _fake(latency_ms=60_000.0)
    t0 = time.perf_counter()
    sel = Montagem(fake).pedir()
    assert time.perf_counter() - t0 < 5  # não dormiu
    assert not sel.fallback_used and fake._usage().latency_ms == 60_000.0


# ---------------------------------------------------------------- sem provedor / indisponível
def test_sem_provedor():
    m = Montagem(None)
    sel = m.pedir()
    assert sel.fallback_used and sel.fallback_reason is FallbackReason.NO_PROVIDER and m.mapa.chamadas == 0


def test_provedor_indisponivel_e_chave_ausente():
    chave = Montagem(_fake(available_override=(False, "key_missing"))).pedir()
    assert chave.fallback_reason is FallbackReason.KEY_MISSING
    fora = Montagem(_fake(available_override=(False, "service_down"))).pedir()
    assert fora.fallback_reason is FallbackReason.PROVIDER_UNAVAILABLE


# ---------------------------------------------------------------- política
def test_privado_com_provedor_remoto_bloqueia_sem_montar_o_mapa():
    fake = _fake()
    m = Montagem(fake, politica=_politica(RepositoryClass.PRIVATE, REMOTE))
    sel = m.pedir()
    assert sel.fallback_used and sel.fallback_reason is FallbackReason.PRIVACY_BLOCK
    assert m.mapa.chamadas == 0 and m.chunks.pedidos == [] and fake.calls == []
    assert m.ledger.snapshot()["calls"] == 0


def test_publico_depende_de_allow_public():
    bloqueado = Montagem(_fake(), politica=_politica(RepositoryClass.PUBLIC, REMOTE, allow_public=False))
    assert bloqueado.pedir().fallback_reason is FallbackReason.PRIVACY_BLOCK and bloqueado.mapa.chamadas == 0
    assert bloqueado.chunks.pedidos == []
    liberado = Montagem(_fake(), politica=_politica(RepositoryClass.PUBLIC, REMOTE, allow_public=True)).pedir()
    assert not liberado.fallback_used and liberado.selected_files


def test_sintetico_remoto_e_negado_sem_mapa_sem_chunk_sem_chamada():
    fake = _fake()
    m = Montagem(fake, politica=_politica(RepositoryClass.SYNTHETIC, REMOTE, allow_public=True))
    sel = m.pedir()
    assert sel.fallback_reason is FallbackReason.PRIVACY_BLOCK
    assert m.mapa.chamadas == 0 and m.chunks.pedidos == [] and fake.calls == []


def test_privado_remoto_e_negado_sem_mapa_sem_chunk_sem_chamada():
    fake = _fake()
    m = Montagem(fake, politica=_politica(RepositoryClass.PRIVATE, REMOTE, allow_public=True))
    sel = m.pedir()
    assert sel.fallback_reason is FallbackReason.PRIVACY_BLOCK
    assert m.mapa.chamadas == 0 and m.chunks.pedidos == [] and fake.calls == []


def test_sintetico_com_provedor_fake_continua_exercitando_o_pipeline():
    fake = _fake(locality=ProviderLocality.FAKE)
    m = Montagem(fake, politica=_politica(RepositoryClass.SYNTHETIC, ProviderLocality.FAKE, allow_public=False))
    sel = m.pedir()
    assert not sel.fallback_used and sel.selected_files and fake.calls


def test_local_em_repositorio_privado_segue():
    local = Montagem(_fake(locality=LOCAL), politica=_politica(RepositoryClass.PRIVATE, LOCAL)).pedir()
    assert not local.fallback_used and local.selected_files


def test_politica_mais_frouxa_que_o_provedor_remoto_falha_fechada():
    m = Montagem(_fake(), politica=_politica(RepositoryClass.PRIVATE, LOCAL))  # provedor REMOTE, política LOCAL
    sel = m.pedir()
    assert sel.fallback_reason is FallbackReason.PRIVACY_BLOCK and m.mapa.chamadas == 0


def test_caminho_sensivel_nunca_chega_ao_provedor():
    fake = _fake()
    m = Montagem(fake, chunks=ChunksFalsos({**_chunks(), ".env": [Chunk(".env", 1, 2, "X=1")]}))
    sel = m.pedir("variaveis contas config login")
    assert not sel.fallback_used
    for chamada in fake.calls:
        for proibido in SENSIVEIS:
            assert proibido not in chamada["paths"] and proibido not in chamada["text"]
    assert fake.calls[0]["n"] == NAO_SENSIVEIS == sel.metadata["files_considered"]
    assert all(f.path not in SENSIVEIS for f in sel.selected_files)


def test_chunk_fora_dos_candidatos_nao_e_enviado():
    fake = _fake(canned={"coisa": ["app/auth/login.py"]})
    intruso = Chunk("app/billing/invoice.py", 1, 3, "def generate_invoice(): pass")
    m = Montagem(fake, chunks=ChunksFalsos({"app/auth/login.py": _chunks()["app/auth/login.py"] + [intruso]}))
    m.pedir("qualquer coisa")
    assert "app/billing/invoice.py" not in fake.calls[1]["paths"]


def test_segredo_duro_no_chunk_bloqueia_a_etapa_b_e_preserva_a():
    fake = _fake(canned={"coisa": ["app/auth/login.py"]})
    m = Montagem(fake, chunks=ChunksFalsos(_chunks(login_segundo=_segredo_duro())))
    sel = m.pedir("qualquer coisa")
    assert not sel.fallback_used and [f.path for f in sel.selected_files] == ["app/auth/login.py"]
    assert sel.selected_regions == () and sel.warnings == ("stage_b_blocked:secret",)
    assert sel.metadata["stage_b_reason"] == "secret_block"
    assert [c["stage"] for c in fake.calls] == ["A"]  # nada da B saiu
    assert _segredo_duro() not in repr(sel)


def test_segredo_mole_no_chunk_tira_so_o_chunk():
    fake = _fake(canned={"coisa": ["app/auth/login.py"]})
    m = Montagem(fake, chunks=ChunksFalsos(_chunks(login_segundo="x = 1\n" + _segredo_mole())))
    sel = m.pedir("qualquer coisa")
    assert not sel.fallback_used and sel.metadata["chunks_dropped_soft"] == 1
    assert fake.calls[1]["n"] == 1 and _segredo_mole() not in fake.calls[1]["text"]
    assert sel.metadata["chunks_sent"] == 1


def test_pergunta_com_segredo_bloqueia_sem_mapa():
    fake = _fake()
    m = Montagem(fake)
    sel = m.pedir("use a chave " + "sk-" + "a" * 24 + " para entrar")
    assert sel.fallback_used and sel.fallback_reason is FallbackReason.SECRET_BLOCK
    assert m.mapa.chamadas == 0 and fake.calls == []
    assert "sk-" + "a" * 24 not in repr(sel)


def test_segredo_no_mapa_serializado_bloqueia_antes_de_enviar():
    entradas = ENTRADAS[:2] + (_e("app/x.py", (_segredo_duro(),)),)
    fake = _fake()
    sel = Montagem(fake, mapa=MapaFalso(entradas)).pedir()
    assert sel.fallback_reason is FallbackReason.SECRET_BLOCK and fake.calls == []


def test_constante_de_codigo_privado_e_o_unico_interruptor(monkeypatch: pytest.MonkeyPatch):
    assert policy_mod.PRIVATE_CODE_SEND_APPROVED is False
    privado = lambda: Montagem(_fake(), politica=_politica(RepositoryClass.PRIVATE, REMOTE)).pedir()  # noqa: E731
    assert privado().fallback_reason is FallbackReason.PRIVACY_BLOCK
    monkeypatch.setattr(policy_mod, "PRIVATE_CODE_SEND_APPROVED", True)
    assert not privado().fallback_used


# ---------------------------------------------------------------- escopo, limites, orçamento
def test_escopo_restringe_o_mapa():
    fake = _fake()
    sel = Montagem(fake).pedir("fatura cobranca calculo", scope=("app/billing",))
    assert fake.calls[0]["paths"] == ["app/billing/invoice.py"] and sel.metadata["files_considered"] == 1


def test_limites_de_payload_sao_respeitados():
    fake = _fake(canned={"coisa": ["app/auth/login.py", "app/billing/invoice.py", "app/utils/strings.py"]})
    sel = Montagem(fake, limits=PayloadLimits(max_map_files=2, max_chunks=2, max_candidate_files=8)).pedir("coisa")
    assert fake.calls[0]["n"] == 2 and fake.calls[1]["n"] == 2 and sel.metadata["chunks_sent"] == 2

    fake2 = _fake()
    sel2 = Montagem(fake2, limits=PayloadLimits(max_bytes=200)).pedir("login billing strings lru")
    assert fake2.calls[0]["bytes"] <= 200 and 0 < fake2.calls[0]["n"] < NAO_SENSIVEIS
    assert sel2.metadata["files_considered"] == fake2.calls[0]["n"]


def test_max_candidate_files_limita_a_etapa_a():
    fake = _fake(canned={"coisa": ["app/auth/login.py", "app/billing/invoice.py", "app/utils/strings.py"]})
    Montagem(fake, limits=PayloadLimits(max_candidate_files=1)).pedir("coisa")
    assert fake.calls[0]["limit"] == 1 and fake.calls[1]["paths"] == ["app/auth/login.py"]


def test_orcamento_por_pedido_zerado_cai_no_local():
    fake = _fake()
    sel = Montagem(fake, budget=Budget(max_calls_per_request=0)).pedir()
    assert sel.fallback_used and sel.fallback_reason is FallbackReason.BUDGET_EXCEEDED and fake.calls == []


def test_orcamento_estoura_na_etapa_b_e_preserva_a():
    fake = _fake()
    sel = Montagem(fake, budget=Budget(max_calls_per_request=1)).pedir()
    assert not sel.fallback_used and sel.selected_files and sel.selected_regions == ()
    assert sel.warnings == ("stage_b_failed:budget_exceeded",) and sel.metadata["calls"] == 1
    # o rótulo não pode dizer "miss" (que sugere chamada feita) quando o orçamento barrou a etapa B antes dela
    assert sel.metadata["stage_a_cache"] == "miss" and sel.metadata["stage_b_cache"] == "skipped"
    assert [c["stage"] for c in fake.calls] == ["A"]


def test_etapa_b_chamada_e_que_leva_o_rotulo_miss():
    fake = _fake()
    sel = Montagem(fake, budget=Budget(max_calls_per_request=2)).pedir()
    assert [c["stage"] for c in fake.calls] == ["A", "B"] and sel.metadata["stage_b_cache"] == "miss"


def test_etapa_b_que_falha_no_provedor_depois_de_chamada_fica_miss():
    # o orçamento deixou chamar; a falha é do provedor, então a chamada existiu e o rótulo é "miss"
    fake = _fake(fail_with=ProviderTimeout("t"), fail_on="regions")
    sel = Montagem(fake).pedir()
    assert sel.metadata["stage_b_cache"] == "miss" and sel.metadata["stage_b_reason"] == "timeout"


def test_orcamento_de_sessao_e_compartilhado_entre_pedidos():
    m = Montagem(_fake(), budget=Budget(max_calls_per_request=2, max_calls_per_session=2))
    assert not m.pedir("verify_password login").fallback_used
    sel = m.pedir("generate_invoice cobranca")
    assert sel.fallback_reason is FallbackReason.BUDGET_EXCEEDED


def test_orcamento_de_tokens_estimados():
    sel = Montagem(_fake(), budget=Budget(max_input_tokens=1)).pedir()
    assert sel.fallback_reason is FallbackReason.BUDGET_EXCEEDED


def test_timeout_do_orcamento_chega_ao_provedor():
    fake = _fake()
    Montagem(fake, budget=Budget(timeout_ms=1500)).pedir()
    assert fake.calls[0]["timeout_s"] == 1.5


# ---------------------------------------------------------------- cache
def test_cache_hit_na_segunda_chamada_nao_chama_o_provedor_e_custa_zero(tmp_path: Path):
    fake = _fake(cost_usd_per_call=0.01, tokens_per_call=100)
    m = Montagem(fake, cache=SemanticCache(tmp_path / "c"))
    a = m.pedir()
    b = m.pedir()
    assert len(fake.calls) == 2 and a.cost_usd == pytest.approx(0.02)
    assert b.cost_usd == 0.0 and b.input_tokens == 0 and b.metadata["calls"] == 0
    assert (b.metadata["stage_a_cache"], b.metadata["stage_b_cache"]) == ("hit", "hit")
    assert [f.path for f in b.selected_files] == [f.path for f in a.selected_files]
    assert [r.key() for r in b.selected_regions] == [r.key() for r in a.selected_regions]


def test_cache_erra_quando_a_revisao_muda(tmp_path: Path):
    fake = _fake()
    m = Montagem(fake, cache=SemanticCache(tmp_path / "c"))
    m.pedir(revision="rev1")
    sel = m.pedir(revision="rev2")
    assert len(fake.calls) == 4 and sel.metadata["stage_a_cache"] == "miss"


def test_cache_guarda_so_caminho_linha_e_nota(tmp_path: Path):
    Montagem(_fake(), cache=SemanticCache(tmp_path / "c")).pedir()
    brutos = [p.read_text(encoding="utf-8") for p in (tmp_path / "c").glob("*.json")]
    assert len(brutos) == 2
    for texto in brutos:
        assert "verify_password(user, raw)" not in texto and "onde verify_password" not in texto
        assert set(json.loads(texto)) <= {"files", "regions"}


def test_cache_corrompido_ou_de_outra_forma_e_miss_silencioso(tmp_path: Path):
    fake = _fake()
    m = Montagem(fake, cache=SemanticCache(tmp_path / "c"))
    m.pedir()
    arquivos = list((tmp_path / "c").glob("*.json"))
    arquivos[0].write_text("{nao e json", encoding="utf-8")
    arquivos[1].write_text(json.dumps({"files": 5, "regions": [["x"]]}), encoding="utf-8")
    sel = m.pedir()
    assert not sel.fallback_used and sel.selected_files and len(fake.calls) == 4


def test_cache_com_caminho_fora_do_mapa_atual_e_miss(tmp_path: Path):
    class CacheEnvenenado(SemanticCache):
        def get(self, key: str):
            return {"files": [["inexistente/x.py", 0.9], [".env", 0.8]]}

    fake = _fake()
    sel = Montagem(fake, cache=CacheEnvenenado(tmp_path / "c")).pedir()
    assert sel.metadata["stage_a_cache"] == "miss" and [f.path for f in sel.selected_files][0] == "app/auth/login.py"


def test_cache_que_levanta_nao_derruba():
    class CacheQuebrado(SemanticCache):
        def get(self, key: str):
            raise OSError("disco")

        def put(self, key: str, value: dict) -> None:
            raise OSError("disco")

    sel = Montagem(_fake(), cache=CacheQuebrado(None)).pedir()
    assert not sel.fallback_used and sel.selected_files


def test_metadata_nao_carrega_codigo_nem_pergunta():
    m = Montagem(_fake())
    q = "onde verify_password valida a senha no login"
    sel = m.pedir(q)
    bruto = json.dumps(sel.metadata) + repr(sel.warnings)
    assert q not in bruto and "compare(user, raw)" not in bruto
    for proibido in SENSIVEIS:
        assert proibido not in bruto


# ---------------------------------------------------------------- Jev: fio, parse, erros
def _ok(probs: dict[str, float], *, usage: dict[str, int] | None = None, model: str = DEFAULT_MODEL) -> httpx.Response:
    topo = max(probs, key=lambda k: probs[k])
    corpo: dict[str, Any] = {"model": model, "answers": {"best": {
        "type": "choice", "choice": topo, "probabilities": probs, "confidence": 0.9}}}
    if usage is not None:
        corpo["usage"] = usage
    return httpx.Response(200, json=corpo)


def _jev(handler, *, env: dict[str, str] | None = None, **kw: Any) -> JevSemanticProvider:
    return JevSemanticProvider(env={"TYPESAFE_API_KEY": CHAVE_FALSA} if env is None else env,
                               transport=httpx.MockTransport(handler), **kw)


def _mapa_pequeno() -> RepoMap:
    return RepoMap("rev1", ENTRADAS[:3])


def test_jev_formato_do_request_e_parse_de_usage_e_custo():
    vistos: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        vistos.append(req)
        return _ok({"app/auth/login.py": 0.6, "app/billing/invoice.py": 0.3, "app/utils/strings.py": 0.1},
                   usage={"input_tokens": 1000, "output_tokens": 7})

    prov = _jev(handler)
    resp = prov.select_files("onde fica o login", _mapa_pequeno(), max_files=2, timeout_s=3.0)
    req = vistos[0]
    assert req.method == "POST" and str(req.url) == "https://api.typesafe.ai/v1/systemone"
    assert req.headers["authorization"] == f"Bearer {CHAVE_FALSA}" and req.headers["content-type"] == "application/json"
    corpo = json.loads(req.content)
    assert corpo["model"] == DEFAULT_MODEL == "jev-1.13.0" and corpo["state"]["question"] == "onde fica o login"
    assert set(corpo["state"]["entries"]) == {"app/auth/login.py", "app/billing/invoice.py", "app/utils/strings.py",
                                             ID_NENHUMA}
    assert corpo["questions"]["best"]["type"] == "choice"
    assert set(corpo["questions"]["best"]["criteria"]) == set(corpo["state"]["entries"])
    assert CHAVE_FALSA not in req.content.decode()  # a chave vai só no cabeçalho
    assert [(c.path, c.score) for c in resp.choices] == [("app/auth/login.py", 0.6), ("app/billing/invoice.py", 0.3)]
    assert resp.usage.input_tokens == 1000 and resp.usage.output_tokens == 7
    assert resp.usage.cost_usd == pytest.approx(1000 * PRICE_USD_PER_MTOK_INPUT / 1e6) and resp.usage.latency_ms >= 0


def test_jev_base_url_e_modelo_configuraveis_e_usage_ausente_estima_por_bytes():
    vistos: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        vistos.append(req)
        return _ok({"app/auth/login.py": 1.0})

    prov = _jev(handler, base_url="http://localhost:9999/", model="jev-teste")
    resp = prov.select_files("q", _mapa_pequeno(), max_files=3, timeout_s=1.0)
    assert str(vistos[0].url) == "http://localhost:9999/v1/systemone"
    assert json.loads(vistos[0].content)["model"] == "jev-teste" and prov.model == "jev-teste"
    assert resp.usage.input_tokens == len(vistos[0].content) // 4 > 0


def test_jev_regioes_viram_ids_path_linhas():
    vistos: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        vistos.append(json.loads(req.content))
        return _ok({"app/auth/login.py:21-40": 0.7, "app/auth/login.py:1-20": 0.3})

    chunks = _chunks()["app/auth/login.py"]
    resp = _jev(handler).select_regions("q", chunks, max_regions=1, timeout_s=1.0)
    assert set(vistos[0]["state"]["entries"]) == {"app/auth/login.py:1-20", "app/auth/login.py:21-40", ID_NENHUMA}
    assert [(r.path, r.start_line, r.end_line, r.score) for r in resp.choices] == [("app/auth/login.py", 21, 40, 0.7)]


def test_jev_repr_e_atributos_nao_vazam_a_chave():
    prov = _jev(lambda r: _ok({"app/auth/login.py": 1.0}))
    prov.select_files("q", _mapa_pequeno(), max_files=1, timeout_s=1.0)
    assert CHAVE_FALSA not in repr(prov) and CHAVE_FALSA not in str(vars(prov).keys())
    assert all(CHAVE_FALSA not in str(v) for v in (prov.name, prov.model, prov.base_url))


def test_jev_available_nao_faz_rede_e_le_a_chave_na_hora():
    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("available() não pode tocar a rede")

    env: dict[str, str] = {}
    prov = _jev(handler, env=env)
    assert prov.available() == (False, "key_missing")
    env["TYPESAFE_API_KEY"] = CHAVE_FALSA  # lida NO MOMENTO da chamada, não congelada no __init__
    assert prov.available() == (True, None)
    env["TYPESAFE_API_KEY"] = "   "
    assert prov.available() == (False, "key_missing")


def test_jev_le_o_ambiente_do_processo_por_padrao(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    prov = JevSemanticProvider(transport=httpx.MockTransport(lambda r: _ok({"a": 1.0})))
    assert prov.available() == (False, "key_missing")
    monkeypatch.setenv("TYPESAFE_API_KEY", CHAVE_FALSA)
    assert prov.available() == (True, None)


def _erro(status: int) -> Any:
    return lambda req: httpx.Response(status, text=CORPO_SENSIVEL_DO_SERVIDOR)


def _levanta(exc: Exception) -> Any:
    def h(req: httpx.Request) -> httpx.Response:
        raise exc
    return h


CENARIOS_JEV = [
    ("timeout", _levanta(httpx.ReadTimeout("lento")), ProviderTimeout, FallbackReason.TIMEOUT),
    ("connect_timeout", _levanta(httpx.ConnectTimeout("lento")), ProviderTimeout, FallbackReason.TIMEOUT),
    ("429", _erro(429), ProviderRateLimited, FallbackReason.RATE_LIMITED),
    ("529", _erro(529), ProviderOverloaded, FallbackReason.OVERLOADED),
    ("503", _erro(503), ProviderOverloaded, FallbackReason.OVERLOADED),
    ("offline", _levanta(httpx.ConnectError("sem rede")), ProviderOffline, FallbackReason.PROVIDER_OFFLINE),
    ("401", _erro(401), ProviderUnavailable, FallbackReason.PROVIDER_UNAVAILABLE),
    ("403", _erro(403), ProviderUnavailable, FallbackReason.PROVIDER_UNAVAILABLE),
    ("500", _erro(500), ProviderError, FallbackReason.PROVIDER_ERROR),
    ("422", _erro(422), ProviderError, FallbackReason.PROVIDER_ERROR),
    ("nao_json", lambda r: httpx.Response(200, text="<html>" + CORPO_SENSIVEL_DO_SERVIDOR), ProviderInvalidResponse,
     FallbackReason.INVALID_RESPONSE),
    ("sem_answers", lambda r: httpx.Response(200, json={"model": "m"}), ProviderInvalidResponse,
     FallbackReason.INVALID_RESPONSE),
    ("nao_choice", lambda r: httpx.Response(200, json={"answers": {"best": {"type": "noul", "noul": 0.1}}}),
     ProviderInvalidResponse, FallbackReason.INVALID_RESPONSE),
    ("sem_probabilidades", lambda r: httpx.Response(200, json={"answers": {"best": {"type": "choice"}}}),
     ProviderInvalidResponse, FallbackReason.INVALID_RESPONSE),
    ("opcao_desconhecida", lambda r: _ok({"inexistente.py": 1.0}), ProviderInvalidResponse,
     FallbackReason.INVALID_RESPONSE),
    ("probabilidade_fora_da_faixa", lambda r: _ok({"app/auth/login.py": 7.0}), ProviderInvalidResponse,
     FallbackReason.INVALID_RESPONSE),
]


@pytest.mark.parametrize("nome,handler,tipo,razao", CENARIOS_JEV, ids=[c[0] for c in CENARIOS_JEV])
def test_jev_erros_viram_provider_error_do_dominio_sem_vazar(nome, handler, tipo, razao):
    prov = _jev(handler)
    with pytest.raises(tipo) as info:
        prov.select_files("q", _mapa_pequeno(), max_files=2, timeout_s=1.0)
    exc = info.value
    assert type(exc) is tipo and exc.reason is razao
    visivel = f"{exc!s} {exc!r} {vars(exc)} {exc.args}"
    assert CHAVE_FALSA not in visivel and CORPO_SENSIVEL_DO_SERVIDOR not in visivel
    assert exc.__cause__ is None and exc.__context__ is None  # a exceção do httpx (com a requisição) não fica encadeada


@pytest.mark.parametrize("nome,handler,tipo,razao", CENARIOS_JEV, ids=[c[0] for c in CENARIOS_JEV])
def test_jev_pelo_retriever_cai_com_a_razao_certa(nome, handler, tipo, razao):
    sel = Montagem(_jev(handler)).pedir()
    assert sel.fallback_used and sel.fallback_reason is razao and sel.selected_files == ()
    assert CHAVE_FALSA not in repr(sel) and CORPO_SENSIVEL_DO_SERVIDOR not in repr(sel)


def test_jev_chave_ausente_pelo_retriever_nao_toca_a_rede():
    chamadas: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        chamadas.append(1)
        return _ok({"app/auth/login.py": 1.0})

    m = Montagem(_jev(handler, env={}))
    sel = m.pedir()
    assert sel.fallback_reason is FallbackReason.KEY_MISSING and chamadas == [] and m.mapa.chamadas == 0


def test_jev_chave_some_entre_available_e_chamada():
    env = {"TYPESAFE_API_KEY": CHAVE_FALSA}
    prov = _jev(lambda r: _ok({"app/auth/login.py": 1.0}), env=env)
    assert prov.available() == (True, None)
    env.clear()
    from app.modules.context_retrieval.domain.errors import ProviderKeyMissing
    with pytest.raises(ProviderKeyMissing):
        prov.select_files("q", _mapa_pequeno(), max_files=1, timeout_s=1.0)


def _mapa_de(n: int) -> RepoMap:
    e = ENTRADAS[0]
    return RepoMap("rev-n", [dataclasses.replace(e, path=f"app/m{i:04d}.py") for i in range(n)])


def test_jev_choice_sempre_leva_a_opcao_nenhuma_e_ela_nunca_vira_arquivo():
    """31.1: `nenhuma` (id opaco) vai em `state` e em `criteria`; escolhê-la é abster-se e não produz candidato."""
    vistos: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        vistos.append(req)
        return _ok({ID_NENHUMA: 0.7, "app/auth/login.py": 0.3})

    resp = _jev(handler).select_files("q", _mapa_pequeno(), max_files=3, timeout_s=3.0)
    corpo = json.loads(vistos[0].content)
    assert ID_NENHUMA in corpo["state"]["entries"] and ID_NENHUMA in corpo["questions"]["best"]["criteria"]
    assert ID_NENHUMA not in {"app/auth/login.py", "app/billing/invoice.py", "app/utils/strings.py"}
    assert [c.path for c in resp.choices] == ["app/auth/login.py"]  # só a escolha real; a nenhuma some do ranking


def test_jev_choice_recusa_local_acima_de_255_opcoes_sem_montar_corpo_nem_rede():
    """31.1: a nenhuma conta no teto. 254 entradas + nenhuma = 255 passa; 255 entradas + nenhuma = 256 é recusado antes do fio."""
    chamadas: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        chamadas.append(req)
        return _ok({ID_NENHUMA: 1.0})

    prov = _jev(handler)
    prov.select_files("q", _mapa_de(MAX_OPCOES - 1), max_files=3, timeout_s=1.0)
    assert len(chamadas) == 1 and len(json.loads(chamadas[0].content)["questions"]["best"]["criteria"]) == MAX_OPCOES
    with pytest.raises(ProviderOptionLimit):
        prov.select_files("q", _mapa_de(MAX_OPCOES), max_files=3, timeout_s=1.0)
    assert len(chamadas) == 1  # a recusada não chegou ao transporte


def test_jev_choice_id_reservado_da_nenhuma_nao_pode_colidir_com_uma_entrada():
    with pytest.raises(ProviderInvalidResponse):
        _jev(lambda r: _ok({"a": 1.0}))._ranquear("q", {ID_NENHUMA: "x"}, "i", 1, 1.0)


def test_jev_pipeline_completo_com_mock_e_sem_caminho_sensivel_no_fio():
    corpos: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        corpos.append(req.content.decode())
        criterios = list(json.loads(req.content)["questions"]["best"]["criteria"])
        alvo = next((c for c in criterios if "login" in c), criterios[0])
        return _ok({alvo: 0.8, **{c: 0.2 / max(len(criterios) - 1, 1) for c in criterios if c != alvo}},
                   usage={"input_tokens": 500, "output_tokens": 5})

    m = Montagem(_jev(handler))
    sel = m.pedir()
    assert not sel.fallback_used and sel.selected_files[0].path == "app/auth/login.py"
    assert sel.selected_files[0].source == "semantic:jev" and sel.selected_regions
    assert sel.cost_usd == pytest.approx(2 * 500 * PRICE_USD_PER_MTOK_INPUT / 1e6) and sel.input_tokens == 1000
    assert len(corpos) == 2
    for c in corpos:
        assert all(s not in c for s in SENSIVEIS) and CHAVE_FALSA not in c


def test_jev_repositorio_privado_bloqueia_antes_de_qualquer_requisicao():
    chamadas: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        chamadas.append(1)
        return _ok({"x": 1.0})

    m = Montagem(_jev(handler), politica=_politica(RepositoryClass.PRIVATE, REMOTE))
    sel = m.pedir()
    assert sel.fallback_reason is FallbackReason.PRIVACY_BLOCK and chamadas == [] and m.mapa.chamadas == 0


# ---------------------------------------------------------------- fábrica
def test_fabrica():
    assert build_provider("none") is None and build_provider("") is None and build_provider("  NONE ") is None
    fake = build_provider("fake")
    assert isinstance(fake, FakeSemanticProvider) and fake.model == "fake-1" and fake.locality is FAKE
    assert build_provider("fake", model="fake-9", locality=REMOTE).model == "fake-9"  # type: ignore[union-attr]
    jev = build_provider("jev", env={"TYPESAFE_API_KEY": CHAVE_FALSA})
    assert isinstance(jev, JevSemanticProvider) and jev.model == DEFAULT_MODEL and jev.locality is REMOTE
    assert jev.available() == (True, None)
    assert build_provider("jev", model="jev-x", env={}).model == "jev-x"  # type: ignore[union-attr]
    assert build_provider("jev", env={}).available() == (False, "key_missing")  # type: ignore[union-attr]
    for p in (fake, jev):
        assert isinstance(p, SemanticProvider)
    with pytest.raises(ValueError):
        build_provider("openai")


def test_fake_registra_o_que_enviou_para_os_testes():
    fake = _fake()
    fake.select_files("login", _mapa_pequeno(), max_files=2, timeout_s=2.0)
    c = fake.calls[0]
    assert c["stage"] == "A" and c["n"] == 3 and c["bytes"] == len(c["text"].encode()) and "app/auth/login.py" in c["paths"]
    assert fake.available() == (True, None)
    assert _fake(available_override=(False, "x")).available() == (False, "x")


def test_fake_fail_on_so_na_etapa_pedida():
    fake = _fake(fail_with=ProviderTimeout("t"), fail_on="regions")
    assert fake.select_files("login", _mapa_pequeno(), max_files=2, timeout_s=1.0).choices
    with pytest.raises(ProviderTimeout):
        fake.select_regions("login", _chunks()["app/auth/login.py"], max_regions=1, timeout_s=1.0)
    assert [c["stage"] for c in fake.calls] == ["A", "B"]  # a chamada que falhou também fica registrada


def test_usage_do_fake_e_provider_usage():
    fake = _fake(cost_usd_per_call=0.5, tokens_per_call=9, latency_ms=3.0)
    r = fake.select_files("login", _mapa_pequeno(), max_files=2, timeout_s=1.0)
    assert r.usage == ProviderUsage(9, 0, 0.5, 3.0)


def test_o_servico_passa_a_pergunta_ao_chunker(tmp_path: Path):
    """J13: a fonte de chunks recebe a pergunta (é ela que escolhe as janelas da etapa B)."""
    fonte = ChunksFalsos()
    m = Montagem(_fake())
    m.retriever.chunk_source = fonte  # type: ignore[assignment]
    m.pedir("onde verify_password valida a senha no login")
    assert fonte.consultas == ["onde verify_password valida a senha no login"]


def test_trocar_a_selecao_de_chunks_invalida_o_cache_da_b(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Outra seleção é outro payload e outra resposta: a versão da seleção entra na chave da B (a A segue em cache)."""
    from app.modules.context_retrieval.application import semantic as sem_mod

    fake = _fake()
    m = Montagem(fake, cache=SemanticCache(tmp_path / "c"))
    m.pedir()
    monkeypatch.setattr(sem_mod, "CHUNK_SELECTION_VERSION", "outra-selecao")
    sel = m.pedir()
    assert (sel.metadata["stage_a_cache"], sel.metadata["stage_b_cache"]) == ("hit", "miss")
