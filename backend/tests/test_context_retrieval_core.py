"""Retrieval de contexto: política, portão de segredo, orçamento, cache, métricas, fusão local, híbrido e serviço.

Tudo `simulated`: retrievers e provedores falsos, sem rede e sem o repositório real. O que mora nestes testes é a
REGRA (quem manda quando, o que cai em fallback, o que nunca sai da máquina), não a qualidade da busca — essa é do
`test_context_retrieval_local.py` e do `test_context_retrieval_semantic.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import AppConfigFile, ContextRetrievalCfg
from app.modules.context_retrieval.application.budget import BudgetLedger
from app.modules.context_retrieval.application.hybrid import HybridRetriever
from app.modules.context_retrieval.application.local import LocalRetriever
from app.modules.context_retrieval.application.service import ContextRetrievalService, DisabledContextRetrieval
from app.modules.context_retrieval.domain import policy as policy_mod
from app.modules.context_retrieval.domain.identifiers import explicit_identifiers, query_fingerprint
from app.modules.context_retrieval.domain.model import (Budget, ContextSelection, FallbackReason, FileHit,
                                                        ProviderUsage, Region, RetrievalMode, RetrievalRequest)
from app.modules.context_retrieval.domain.policy import ExternalContextPolicy, RepositoryClass
from app.modules.context_retrieval.domain.ports import ProviderLocality
from app.modules.context_retrieval.domain.sensitive import (SensitivePathMatcher, hard_secret_kind, has_soft_secret)
from app.modules.context_retrieval.infrastructure.cache import SemanticCache
from app.modules.context_retrieval.infrastructure.metrics import RetrievalMetrics, resumir, sanear


# ---------------------------------------------------------------- dublês
class Stub:
    """Retriever que devolve arquivos fixos (e janelas 10-20 de cada um)."""

    def __init__(self, name: str, files: list[str], *, fallback: FallbackReason | None = None,
                 cost: float = 0.0, boom: bool = False) -> None:
        self.name = name
        self._files = files
        self._fallback = fallback
        self._cost = cost
        self._boom = boom
        self.calls = 0

    def retrieve(self, request: RetrievalRequest) -> ContextSelection:
        self.calls += 1
        if self._boom:
            raise RuntimeError("falha inesperada do retriever")
        if self._fallback:
            return ContextSelection.empty(self.name, fallback_used=True, fallback_reason=self._fallback)
        hits = tuple(FileHit(f, 1.0 / (i + 1), self.name) for i, f in enumerate(self._files))
        regs = tuple(Region(f, 10, 20) for f in self._files)
        return ContextSelection(selected_files=hits, selected_regions=regs, source=self.name, cost_usd=self._cost,
                                input_tokens=100 if self._cost else 0, latency_ms=3.0,
                                metadata={"files_considered": 40})


def _req(query: str, top_k: int = 5) -> RetrievalRequest:
    return RetrievalRequest(query=query, root=Path("."), revision="rev1", top_k=top_k)


class _Sink:
    def __init__(self) -> None:
        self.eventos: list[dict] = []

    def record(self, event: dict) -> None:
        self.eventos.append(event)


def _servico(mode: RetrievalMode, *, lex: Stub, bm: Stub, sem: Stub | None, sink: _Sink | None = None,
             texto: dict[str, str] | None = None) -> ContextRetrievalService:
    local = LocalRetriever(lex, bm)
    hib = HybridRetriever(local=local, semantic=sem) if sem is not None else None
    return ContextRetrievalService(
        mode=mode, root=Path("."), top_k=5, local=local, hybrid=hib, revision=lambda: "rev1",
        read_text=lambda p: (texto or {}).get(p), sink=sink or _Sink(), budget=Budget())


# ---------------------------------------------------------------- identificadores
def test_identificadores_explicitos() -> None:
    ids = explicit_identifiers("onde `foo_bar` é usado por MyClassName e por app.core.utils?")
    assert "foo_bar" in ids and "MyClassName" in ids and "app.core.utils" in ids
    assert explicit_identifiers("como o sistema decide quando reiniciar o aparelho") == []


def test_impressao_digital_ignora_caixa_e_espaco_e_nao_guarda_o_texto() -> None:
    assert query_fingerprint("Onde  fica?") == query_fingerprint("onde fica?")
    assert "onde" not in query_fingerprint("onde fica?")


# ---------------------------------------------------------------- caminho sensível e segredo
@pytest.mark.parametrize("caminho", [
    ".env", ".env.local", "config/config.yaml", "secrets/chave.txt", "data/poc.sqlite3", "evidence/r1/tela.png",
    "backups/a.sql", "personas/ana.json", "apks/app.apk", "id_rsa", "x/server.pem", "app/credentials.json",
    "a/.ssh/config", "cookies.txt", "logs/central.log", "DATA/x.json"])
def test_caminho_sensivel(caminho: str) -> None:
    assert SensitivePathMatcher().is_sensitive(caminho), caminho


@pytest.mark.parametrize("caminho", [
    "backend/app/security/secret_store.py", "config/config.example.yaml", ".env.example",
    "frontend/src/data/tipos.ts", "docs/operacao.md", "backend/app/modules/skills/domain/document.py"])
def test_caminho_comum_segue(caminho: str) -> None:
    assert not SensitivePathMatcher().is_sensitive(caminho), caminho


def test_padrao_configurado_se_soma_aos_fixos() -> None:
    m = SensitivePathMatcher(["interno/**", "*.cfg"])
    assert m.is_sensitive("interno/a/b.py") and m.is_sensitive("x/y.cfg") and m.is_sensitive(".env")
    assert not m.is_sensitive("externo/a.py")


def test_portao_duro_pega_formatos_de_credencial() -> None:
    falsos = {
        "private_key": "-----BEGIN " + "RSA PRIVATE KEY-----\nabc",
        "jwt": "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12,
        "bearer_token": "Authorization: Bearer " + "x" * 30,
        "api_key": "chave = sk-" + "a" * 24,
        "dsn_password": "postgresql://" + "u" + ":" + "senha123" + "@host/db",
    }
    for tipo, texto in falsos.items():
        assert hard_secret_kind(texto) is not None, tipo
    assert hard_secret_kind("def soma(a, b):\n    return a + b") is None


def test_segredo_mole_e_diferente_de_duro() -> None:
    assert has_soft_secret("password=hunter2hunter2") and hard_secret_kind("password=hunter2hunter2") is None
    assert not has_soft_secret("def senha_valida(texto: str) -> bool:")


# ---------------------------------------------------------------- política
def test_repositorio_privado_e_negado_a_provedor_remoto() -> None:
    p = ExternalContextPolicy(repository=RepositoryClass.PRIVATE, locality=ProviderLocality.REMOTE)
    d = p.can_send_repository()
    assert not d.allowed and d.code is FallbackReason.PRIVACY_BLOCK and d.hard


def test_publico_remoto_so_com_allow_public_explicito() -> None:
    nega = ExternalContextPolicy(repository=RepositoryClass.PUBLIC, locality=ProviderLocality.REMOTE)
    assert not nega.can_send_repository().allowed and nega.can_send_repository().reason == "public_repository_not_enabled"
    assert ExternalContextPolicy(repository=RepositoryClass.PUBLIC, locality=ProviderLocality.REMOTE,
                                 allow_public=True).can_send_repository().allowed


@pytest.mark.parametrize("allow_public", [False, True])
def test_sintetico_remoto_e_negado_com_ou_sem_allow_public(allow_public: bool) -> None:
    d = ExternalContextPolicy(repository=RepositoryClass.SYNTHETIC, locality=ProviderLocality.REMOTE,
                              allow_public=allow_public).can_send_repository()
    assert not d.allowed and d.hard and d.code is FallbackReason.PRIVACY_BLOCK and d.reason == "synthetic_repository"


def test_privado_remoto_e_negado_mesmo_com_allow_public() -> None:
    d = ExternalContextPolicy(repository=RepositoryClass.PRIVATE, locality=ProviderLocality.REMOTE,
                              allow_public=True).can_send_repository()
    assert not d.allowed and d.reason == "private_repository"


def test_sintetico_com_fake_ou_local_nao_sofre_o_bloqueio_externo() -> None:
    for loc in (ProviderLocality.FAKE, ProviderLocality.LOCAL):
        assert ExternalContextPolicy(repository=RepositoryClass.SYNTHETIC, locality=loc).can_send_repository().allowed


def test_a_classe_sintetica_nao_pode_ser_liberada_por_configuracao() -> None:
    """A permissão é constante de código, como a do código privado: não há campo de config que a ligue."""
    assert policy_mod.SYNTHETIC_REMOTE_SEND_APPROVED is False
    from app.config import ContextRetrievalSemanticCfg

    assert not [c for c in ContextRetrievalSemanticCfg.model_fields if "synthetic" in c.lower()]


def test_provedor_local_nao_envia_nada_para_fora_e_tudo_passa() -> None:
    p = ExternalContextPolicy(repository=RepositoryClass.PRIVATE, locality=ProviderLocality.LOCAL)
    assert p.can_send_repository().allowed and p.can_send_file(".env").allowed
    assert p.can_send_chunk("a.py", "-----BEGIN " + "PRIVATE KEY-----").allowed


def test_fake_exercita_os_portoes_de_caminho_e_segredo_mas_nao_o_de_repositorio() -> None:
    p = ExternalContextPolicy(repository=RepositoryClass.PRIVATE, locality=ProviderLocality.FAKE)
    assert p.can_send_repository().allowed
    assert not p.can_send_file("data/x.json").allowed
    assert not p.can_send_chunk("a.py", "-----BEGIN " + "PRIVATE KEY-----").allowed


def test_chunk_com_segredo_duro_e_mole_e_arquivo_sensivel() -> None:
    p = ExternalContextPolicy(repository=RepositoryClass.PUBLIC, locality=ProviderLocality.REMOTE, allow_public=True)
    duro = p.can_send_chunk("a.py", "k = 'sk-" + "b" * 24 + "'")
    assert not duro.allowed and duro.hard and duro.code is FallbackReason.SECRET_BLOCK
    mole = p.can_send_chunk("a.py", "password=hunter2hunter2")
    assert not mole.allowed and not mole.hard
    assert not p.can_send_chunk(".env", "A=1").allowed
    assert p.can_send_chunk("a.py", "def f():\n    return 1").allowed
    assert not p.can_send_query("use o token Bearer " + "z" * 30).allowed


def test_a_constante_e_o_unico_interruptor_do_codigo_privado(monkeypatch: pytest.MonkeyPatch) -> None:
    assert policy_mod.PRIVATE_CODE_SEND_APPROVED is False
    p = ExternalContextPolicy(repository=RepositoryClass.PRIVATE, locality=ProviderLocality.REMOTE)
    assert not p.can_send_repository().allowed
    monkeypatch.setattr(policy_mod, "PRIVATE_CODE_SEND_APPROVED", True)
    assert p.can_send_repository().allowed


# ---------------------------------------------------------------- orçamento
def test_orcamento_por_pedido_e_por_sessao() -> None:
    ledger = BudgetLedger(Budget(max_calls_per_request=2, max_calls_per_session=3))
    r1 = ledger.begin_request()
    # `check_call` autorizado RESERVA a chamada: ela conta mesmo que o provedor falhe e `record` nunca chegue
    assert r1.check_call(est_input_tokens=10) is None
    r1.record(ProviderUsage(input_tokens=10))
    assert r1.check_call(est_input_tokens=10) is None
    assert r1.check_call(est_input_tokens=10) is FallbackReason.BUDGET_EXCEEDED   # 2 por pedido (a 2ª falhou e contou)
    r2 = ledger.begin_request()
    assert r2.check_call(est_input_tokens=10) is None                              # 3ª da sessão
    assert ledger.begin_request().check_call(est_input_tokens=10) is FallbackReason.BUDGET_EXCEEDED  # 3 na sessão
    assert ledger.snapshot()["calls"] == 3


def test_orcamento_de_tokens_e_de_custo() -> None:
    ledger = BudgetLedger(Budget(max_input_tokens=100, max_cost_usd=0.01))
    r = ledger.begin_request()
    assert r.check_call(est_input_tokens=101) is FallbackReason.BUDGET_EXCEEDED
    assert r.check_call(est_input_tokens=50) is None
    r.record(ProviderUsage(input_tokens=50, cost_usd=0.02))
    assert ledger.begin_request().check_call(est_input_tokens=1) is FallbackReason.BUDGET_EXCEEDED  # custo da sessão


# ---------------------------------------------------------------- cache
def test_cache_hit_miss_e_invalidacao_por_revisao(tmp_path: Path) -> None:
    c = SemanticCache(tmp_path / "c")
    base = dict(query="Onde fica?", scope=(), provider="fake", model="m", stage="A")
    k1 = c.key(revision="r1", **base)
    assert c.get(k1) is None
    c.put(k1, {"files": [["a.py", 0.9]]})
    assert c.get(k1) == {"files": [["a.py", 0.9]]}
    assert c.key(revision="r1", **{**base, "query": "  onde   fica? "}) == k1       # pergunta normalizada
    assert c.key(revision="r2", **base) != k1                                       # revisão nova = outra chave
    assert c.key(revision="r1", **{**base, "provider": "jev"}) != k1
    assert c.key(revision="r1", **{**base, "model": "m2"}) != k1
    assert c.key(revision="r1", **{**base, "stage": "B"}) != k1
    assert c.key(revision="r1", **{**base, "scope": ("backend",)}) != k1
    assert c.get(c.key(revision="r2", **base)) is None


def test_cache_corrompido_e_miss_e_desligado_nao_grava(tmp_path: Path) -> None:
    c = SemanticCache(tmp_path / "c")
    k = c.key(revision="r", query="q", scope=(), provider="p", model="m", stage="A")
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / f"{k}.json").write_text("{nao é json", encoding="utf-8")
    assert c.get(k) is None
    off = SemanticCache(tmp_path / "off", enabled=False)
    off.put(k, {"x": 1})
    assert not (tmp_path / "off").exists() and off.get(k) is None
    assert SemanticCache(None).get(k) is None


# ---------------------------------------------------------------- métricas
def test_evento_so_leva_campos_da_lista_fechada() -> None:
    limpo = sanear({"retriever": "hybrid", "mode": "hybrid", "codigo": "def f(): pass", "query": "pergunta crua",
                    "files_selected": 3, "fallback": True, "latency_ms": 12.5, "cost_usd": 0.001,
                    "files_considered": True, "fallback_reason": "x" * 100})
    assert "codigo" not in limpo and "query" not in limpo
    assert limpo["files_selected"] == 3 and limpo["fallback"] is True
    assert "files_considered" not in limpo             # bool não vira contador
    assert len(limpo["fallback_reason"]) == 32          # texto limitado


def test_metricas_gravam_jsonl_sem_texto_e_resumem(tmp_path: Path) -> None:
    m = RetrievalMetrics(tmp_path / "ev")
    m.record({"retriever": "hybrid", "mode": "hybrid", "query_fp": query_fingerprint("segredo da pergunta"),
              "latency_ms": 10.0, "fallback": False, "cache": "hit", "cost_usd": 0.002, "files_selected": 3})
    m.record({"retriever": "hybrid:local_fallback", "mode": "hybrid", "latency_ms": 30.0, "fallback": True,
              "fallback_reason": "privacy_block", "privacy_block_reason": "privacy_block", "cache": "miss"})
    bruto = (tmp_path / "ev" / "events.jsonl").read_text(encoding="utf-8")
    assert "segredo da pergunta" not in bruto and len(bruto.splitlines()) == 2
    r = resumir(m.recentes())
    assert r["requests"] == 2 and r["cache"] == {"hit": 1, "miss": 1}
    assert r["fallbacks"] == {"privacy_block": 1} and r["privacy_blocks"] == {"privacy_block": 1}
    assert r["cost_usd"] == pytest.approx(0.002)


def test_metrica_nunca_levanta(tmp_path: Path) -> None:
    arq = tmp_path / "arquivo"
    arq.write_text("x", encoding="utf-8")
    RetrievalMetrics(arq / "dentro").record({"mode": "hybrid"})   # diretório impossível: engole


# ---------------------------------------------------------------- fusão local
def test_local_identificador_explicito_com_achado_lexical_manda_o_lexico() -> None:
    local = LocalRetriever(Stub("lexical", ["a.py", "b.py"]), Stub("bm25", ["c.py", "a.py", "d.py"]))
    parts = local.parts(_req("onde `fetch_user` é definido", top_k=4))
    assert parts.safeguard
    assert [h.path for h in parts.merged.selected_files] == ["a.py", "b.py", "c.py", "d.py"]


def test_local_sem_identificador_o_bm25_manda_e_o_lexico_completa() -> None:
    local = LocalRetriever(Stub("lexical", ["a.py"]), Stub("bm25", ["c.py", "d.py"]))
    parts = local.parts(_req("como o sistema reinicia o aparelho", top_k=3))
    assert not parts.safeguard
    assert [h.path for h in parts.merged.selected_files] == ["c.py", "d.py", "a.py"]


def test_local_identificador_sem_achado_lexical_nao_ativa_salvaguarda() -> None:
    parts = LocalRetriever(Stub("lexical", []), Stub("bm25", ["c.py"])).parts(_req("`fetch_user`"))
    assert not parts.safeguard and [h.path for h in parts.merged.selected_files] == ["c.py"]


def test_local_respeita_top_k_e_teto_de_regioes() -> None:
    arquivos = [f"f{i}.py" for i in range(20)]
    sel = LocalRetriever(Stub("lexical", arquivos), Stub("bm25", arquivos)).retrieve(_req("`fetch_user`", top_k=3))
    assert len(sel.selected_files) == 3 and len(sel.selected_regions) == 3


# ---------------------------------------------------------------- híbrido
def _hib(lex: list[str], bm: list[str], sem: Stub, preserve: int = 1) -> HybridRetriever:
    return HybridRetriever(local=LocalRetriever(Stub("lexical", lex), Stub("bm25", bm)), semantic=sem,
                           lexical_preserve=preserve)


def test_hibrido_salvaguarda_lexical_no_topo_e_semantico_completa_sem_duplicar() -> None:
    sel = _hib(["lex.py", "lex2.py"], ["bm.py"], Stub("semantic", ["s1.py", "lex.py", "s2.py"])
               ).retrieve(_req("onde `fetch_user` valida o token", top_k=4))
    assert [h.path for h in sel.selected_files] == ["lex.py", "s1.py", "s2.py"]   # só o MELHOR lexical é preservado
    assert sel.source == "hybrid" and not sel.fallback_used and sel.metadata["safeguard"] is True
    assert sel.selected_regions[0].path == "lex.py"                               # janela lexical antes das semânticas


def test_hibrido_preserva_n_arquivos_lexicais_quando_configurado() -> None:
    sel = _hib(["lex.py", "lex2.py"], [], Stub("semantic", ["s1.py"]), preserve=2
               ).retrieve(_req("`fetch_user`", top_k=5))
    assert [h.path for h in sel.selected_files] == ["lex.py", "lex2.py", "s1.py"]


def test_hibrido_consulta_sem_sinal_lexical_usa_o_semantico_como_principal() -> None:
    sel = _hib(["lex.py"], ["bm.py"], Stub("semantic", ["s1.py", "s2.py", "bm.py"])
               ).retrieve(_req("como o sistema decide reiniciar", top_k=3))
    assert [h.path for h in sel.selected_files] == ["s1.py", "s2.py", "bm.py"]
    assert sel.metadata["safeguard"] is False


@pytest.mark.parametrize("razao", [FallbackReason.TIMEOUT, FallbackReason.RATE_LIMITED, FallbackReason.OVERLOADED,
                                   FallbackReason.PROVIDER_OFFLINE, FallbackReason.INVALID_RESPONSE,
                                   FallbackReason.BUDGET_EXCEEDED, FallbackReason.PRIVACY_BLOCK,
                                   FallbackReason.KEY_MISSING, FallbackReason.SECRET_BLOCK])
def test_hibrido_cai_no_local_com_a_razao(razao: FallbackReason) -> None:
    sel = _hib(["lex.py"], ["bm.py"], Stub("semantic", [], fallback=razao)).retrieve(_req("`fetch_user`"))
    assert sel.fallback_used and sel.fallback_reason is razao
    assert [h.path for h in sel.selected_files] == ["lex.py", "bm.py"]            # o resultado do local, determinístico


def test_hibrido_semantico_vazio_e_fallback_e_excecao_tambem() -> None:
    sel = _hib(["lex.py"], [], Stub("semantic", [])).retrieve(_req("`fetch_user`"))
    assert sel.fallback_used and sel.fallback_reason is FallbackReason.EMPTY_SEMANTIC
    sel2 = _hib(["lex.py"], [], Stub("semantic", ["x.py"], boom=True)).retrieve(_req("`fetch_user`"))
    assert sel2.fallback_used and sel2.fallback_reason is FallbackReason.PROVIDER_ERROR
    assert [h.path for h in sel2.selected_files] == ["lex.py"]


def test_hibrido_regiao_local_ocupa_arquivo_semantico_sem_regiao() -> None:
    class SoArquivos(Stub):
        def retrieve(self, request: RetrievalRequest) -> ContextSelection:
            return ContextSelection(selected_files=(FileHit("bm.py", 1.0, "semantic"),), selected_regions=(),
                                    source="semantic")
    sel = _hib([], ["bm.py"], SoArquivos("semantic", [])).retrieve(_req("como reinicia"))
    assert [r.path for r in sel.selected_regions] == ["bm.py"]


def test_hibrido_soma_custo_e_tokens_do_semantico() -> None:
    sel = _hib(["lex.py"], [], Stub("semantic", ["s.py"], cost=0.003)).retrieve(_req("`fetch_user`"))
    assert sel.cost_usd == pytest.approx(0.003) and sel.input_tokens == 100


# ---------------------------------------------------------------- serviço e modos
def test_desligado_nao_toca_em_nada() -> None:
    lex, bm, sem, sink = Stub("lexical", ["a.py"]), Stub("bm25", ["b.py"]), Stub("semantic", ["c.py"]), _Sink()
    s = _servico(RetrievalMode.DISABLED, lex=lex, bm=bm, sem=sem, sink=sink)
    assert s.gather("`fetch_user`") is None
    assert lex.calls == bm.calls == sem.calls == 0 and sink.eventos == []
    assert DisabledContextRetrieval().gather("x") is None and not DisabledContextRetrieval().enabled


def test_local_only_nunca_chama_o_semantico() -> None:
    sem = Stub("semantic", ["c.py"])
    pack = _servico(RetrievalMode.LOCAL_ONLY, lex=Stub("lexical", ["a.py"]), bm=Stub("bm25", ["b.py"]),
                    sem=sem).gather("`fetch_user`")
    assert pack is not None and pack.origin == "local" and sem.calls == 0
    assert [f.path for f in pack.files] == ["a.py", "b.py"]
    assert pack.mode is RetrievalMode.LOCAL_ONLY and pack.revision == "rev1"


def test_hybrid_entrega_o_hibrido_e_registra_o_evento() -> None:
    sink = _Sink()
    pack = _servico(RetrievalMode.HYBRID, lex=Stub("lexical", ["a.py"]), bm=Stub("bm25", ["b.py"]),
                    sem=Stub("semantic", ["c.py"], cost=0.002), sink=sink).gather("onde `fetch_user` vive")
    assert pack is not None and pack.origin == "hybrid"
    assert [f.path for f in pack.files] == ["a.py", "c.py"]
    ev = sink.eventos[-1]
    assert ev["mode"] == "hybrid" and ev["files_selected"] == 2 and ev["cost_usd"] == pytest.approx(0.002)
    assert "onde" not in json.dumps(ev) and ev["query_fp"] == query_fingerprint("onde `fetch_user` vive")


def test_shadow_entrega_o_local_mas_mede_o_semantico() -> None:
    sink = _Sink()
    pack = _servico(RetrievalMode.SHADOW, lex=Stub("lexical", ["a.py"]), bm=Stub("bm25", ["b.py"]),
                    sem=Stub("semantic", ["c.py"], cost=0.002), sink=sink).gather("`fetch_user`")
    assert pack is not None and pack.origin == "local"
    assert [f.path for f in pack.files] == ["a.py", "b.py"]                       # o semântico NÃO altera o entregue
    assert pack.metadata["shadow"] is True and 0.0 <= pack.metadata["agreement"] <= 1.0
    assert sink.eventos[-1]["cost_usd"] == pytest.approx(0.002)                   # mas o custo foi observado


def test_hybrid_com_falha_do_provedor_cai_no_local_e_registra_a_razao() -> None:
    sink = _Sink()
    pack = _servico(RetrievalMode.HYBRID, lex=Stub("lexical", ["a.py"]), bm=Stub("bm25", ["b.py"]),
                    sem=Stub("semantic", [], fallback=FallbackReason.PRIVACY_BLOCK), sink=sink).gather("`fetch_user`")
    assert pack is not None and pack.metadata["fallback_used"] is True
    assert pack.metadata["fallback_reason"] == "privacy_block"
    assert sink.eventos[-1]["privacy_block_reason"] == "privacy_block" and sink.eventos[-1]["fallback"] is True


def test_hybrid_sem_provedor_configurado_cai_no_local() -> None:
    pack = _servico(RetrievalMode.HYBRID, lex=Stub("lexical", ["a.py"]), bm=Stub("bm25", ["b.py"]),
                    sem=None).gather("`fetch_user`")
    assert pack is not None and pack.metadata["fallback_reason"] == "no_provider"
    assert [f.path for f in pack.files] == ["a.py", "b.py"]


def test_local_que_explode_vira_pacote_vazio_nao_excecao() -> None:
    pack = _servico(RetrievalMode.LOCAL_ONLY, lex=Stub("lexical", [], boom=True), bm=Stub("bm25", []),
                    sem=None).gather("x")
    assert pack is not None and pack.files == () and any(w.startswith("local_failed") for w in pack.warnings)


def test_texto_das_regioes_so_quando_pedido_e_nunca_com_segredo() -> None:
    texto = {"a.py": "\n".join(f"linha {i}" for i in range(1, 40)),
             "b.py": "\n".join(["x = 1"] * 9 + ["token = 'sk-" + "q" * 24 + "'"] + ["y = 2"] * 12)}
    s = _servico(RetrievalMode.LOCAL_ONLY, lex=Stub("lexical", ["a.py", "b.py"]), bm=Stub("bm25", []),
                 sem=None, texto=texto)
    sem_texto = s.gather("`fetch_user`")
    assert sem_texto is not None and all(r.text is None for r in sem_texto.regions)
    com = s.gather("`fetch_user`", with_text=True)
    assert com is not None
    por_arquivo = {r.path: r for r in com.regions}
    assert por_arquivo["a.py"].text is not None and por_arquivo["a.py"].text.startswith("linha 10")
    assert por_arquivo["b.py"].text is None and "text_withheld:secret" in com.warnings


def test_pacote_serializa_sem_texto_por_padrao() -> None:
    pack = _servico(RetrievalMode.LOCAL_ONLY, lex=Stub("lexical", ["a.py"]), bm=Stub("bm25", []), sem=None,
                    texto={"a.py": "x\n" * 30}).gather("`fetch_user`", with_text=True)
    assert pack is not None
    assert "text" not in json.dumps(pack.to_dict())
    assert "text" in json.dumps(pack.to_dict(with_text=True))
    d = pack.to_dict()
    assert d["retrieval_version"] and d["revision"] == "rev1" and d["budget"]["limits"]["timeout_ms"] == 5000


# ---------------------------------------------------------------- configuração
def test_configuracao_padrao_esta_desligada() -> None:
    cfg = AppConfigFile().context_retrieval
    assert cfg.enabled is False and cfg.mode == "disabled" and cfg.effective_mode == "disabled"
    assert cfg.semantic.provider == "none" and cfg.semantic.repository_class == "private"
    assert cfg.semantic.allow_public is False


def test_enabled_false_vence_o_modo_escrito() -> None:
    assert ContextRetrievalCfg(enabled=False, mode="hybrid").effective_mode == "disabled"
    assert ContextRetrievalCfg(enabled=True, mode="hybrid").effective_mode == "hybrid"
    assert ContextRetrievalCfg(enabled=True).effective_mode == "disabled"        # ligar sem escolher modo não liga nada


def test_chave_do_provedor_nao_e_campo_do_arquivo_de_configuracao() -> None:
    campos = set(ContextRetrievalCfg.model_fields) | set(ContextRetrievalCfg().semantic.model_fields)
    assert not any(("key" in c or "secret" in c or "password" in c or c == "token") for c in campos)
