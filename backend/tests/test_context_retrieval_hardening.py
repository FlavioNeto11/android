"""Retrieval de contexto: blindagem. Orçamento de sessão em lote, modo sombra, híbrido EXACT/SEMANTIC/MIXED e cache x política.

`simulated`: retrievers locais REAIS sobre um mini-repositório em tmp_path, provedor FALSO (nenhuma rede; um fixture derruba
`connect` fora de loopback). Cada teste aqui responde a uma pergunta de segurança ou de contrato, em linguagem de produto.
"""
from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

from app.modules.context_retrieval import wiring
from app.modules.context_retrieval.domain.errors import ProviderTimeout
from app.modules.context_retrieval.domain.model import RetrievalMode
from app.modules.context_retrieval.domain.ports import ProviderLocality
from app.modules.context_retrieval.domain.ports import Visibility
from app.modules.context_retrieval.infrastructure.providers.fake import FakeSemanticProvider, FixedVisibilityVerifier

from .test_context_retrieval_integration import _cfg, _eventos


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch: pytest.MonkeyPatch) -> Any:
    tentativas: list[object] = []
    conectar = socket.socket.connect

    def barrar(self: socket.socket, endereco: Any) -> Any:
        if not (isinstance(endereco, tuple) and endereco and endereco[0] in ("127.0.0.1", "::1", "localhost")):
            tentativas.append(endereco)
            raise AssertionError("rede no teste de retrieval")
        return conectar(self, endereco)

    monkeypatch.setattr(socket.socket, "connect", barrar)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    yield
    assert tentativas == []


def _arquivos(raiz: Path) -> Path:
    corpo = {
        "app/auth.py": '"""Sessao do usuario."""\n\n\ndef verify_token(token):\n    return token\n\n\ndef login_user(nome):\n    return nome\n',
        "app/billing.py": '"""Cobranca. Autenticacao do cliente no gateway: autenticacao autenticacao."""\n\n\n'
                          "def emitir_fatura(cliente):\n    return cliente\n",
        "docs/gateway.md": "# Gateway\n\nAutenticacao do gateway de pagamento. Autenticacao e segredos ficam fora do repositorio.\n",
        "docs/seguranca.md": "# Seguranca\n\nPolitica de autenticacao do painel.\n",
        "app/relatorio.py": "def montar_relatorio_mensal(dados):\n    return dados\n",
        "app/estoque.py": "def baixar_estoque(item):\n    return item\n",
        ".env": "SEGREDO=1\n",
        "data/dump.json": '{"a": 1}\n',
    }
    for rel, texto in corpo.items():
        p = raiz / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(texto, encoding="utf-8")
    return raiz


def _servico(tmp_path: Path, provider: FakeSemanticProvider | None, *, mode: str = "hybrid", **semantic: Any):
    raiz = _arquivos(tmp_path / "repo")
    cfg = _cfg(tmp_path, enabled=True, mode=mode,
               semantic={"provider": "fake", "repository_class": "public", "allow_public": True, **semantic},
               cache={"enabled": semantic.pop("cache", False)})
    return wiring.build_service(cfg, root=raiz, provider=provider, visibility=FixedVisibilityVerifier(Visibility.PUBLIC))


def _caminhos(pack: Any) -> list[str]:
    return [f.path for f in pack.files]


# ---------------------------------------------------------------- orçamento de sessão em lote
def test_orcamento_de_sessao_vale_para_a_vida_toda_do_lote_e_o_resto_cai_no_local(tmp_path: Path) -> None:
    falso = FakeSemanticProvider(locality=ProviderLocality.REMOTE, concepts={"autenticacao": ["login", "token"]})
    servico = _servico(tmp_path, falso, max_calls_per_session=3, max_calls=2)
    consultas = [f"como funciona a autenticacao do sistema variante {i}" for i in range(8)]
    with servico.session():
        packs = [servico.gather(q) for q in consultas]
    assert all(p is not None for p in packs)
    assert len(falso.calls) == 3                                  # NENHUMA chamada além do teto da sessão, mesmo em 8 itens
    razoes = [p.metadata["fallback_reason"] for p in packs if p]  # type: ignore[union-attr]
    assert razoes.count("budget_exceeded") >= 5                   # os demais caíram no local, sem erro
    assert all(p.files for p in packs if p)                       # e o local entregou mesmo assim
    # não "reseta" por item: a última consulta também está sem orçamento
    assert packs[-1].metadata["fallback_reason"] == "budget_exceeded"          # type: ignore[union-attr]


def test_orcamento_de_sessao_nao_reseta_fora_do_session(tmp_path: Path) -> None:
    falso = FakeSemanticProvider(locality=ProviderLocality.REMOTE)
    servico = _servico(tmp_path, falso, max_calls_per_session=2, max_calls=2)
    for i in range(5):                                            # um gather por vez, sem `session()`
        servico.gather(f"autenticacao do gateway {i}")
    assert len(falso.calls) == 2


def test_orcamento_de_custo_da_sessao_tambem_trava(tmp_path: Path) -> None:
    falso = FakeSemanticProvider(locality=ProviderLocality.REMOTE, cost_usd_per_call=0.03, tokens_per_call=10)
    servico = _servico(tmp_path, falso, max_cost_usd=0.05, max_calls=1, max_calls_per_session=100)
    for i in range(6):
        servico.gather(f"autenticacao do gateway {i}")
    assert len(falso.calls) == 2                                  # 0,03 + 0,03 passa de 0,05: a terceira nem sai


# ---------------------------------------------------------------- modo sombra
def test_sombra_roda_o_pipeline_mede_e_entrega_exatamente_o_local(tmp_path: Path) -> None:
    falso = FakeSemanticProvider(locality=ProviderLocality.REMOTE, concepts={"autenticacao": ["login", "token"]},
                                 cost_usd_per_call=0.002, tokens_per_call=30)
    sombra = _servico(tmp_path, falso, mode="shadow")
    pergunta = "como funciona a autenticacao do sistema"
    pack = sombra.gather(pergunta)
    assert falso.calls, "o pipeline semântico tem de ter rodado"
    local = wiring.build_service(_cfg(tmp_path, enabled=True, mode="local_only"), root=tmp_path / "repo").gather(pergunta)
    assert pack is not None and local is not None
    assert _caminhos(pack) == _caminhos(local)                    # o entregue é o local
    assert [(r.path, r.start_line, r.end_line) for r in pack.regions] == [(r.path, r.start_line, r.end_line)
                                                                          for r in local.regions]
    assert pack.origin == "local" and pack.mode is RetrievalMode.SHADOW
    ev = _eventos(tmp_path)[-1] if (tmp_path / "dados").exists() else {}
    assert ev.get("mode") in ("shadow", "local_only") and any(e["mode"] == "shadow" and e["cost_usd"] > 0
                                                              for e in _eventos(tmp_path))


def test_sombra_o_resultado_semantico_diferente_nao_muda_o_pacote(tmp_path: Path) -> None:
    pergunta = "como funciona a autenticacao do sistema"
    um = _servico(tmp_path / "a", FakeSemanticProvider(locality=ProviderLocality.REMOTE,
                                                       canned={"autenticacao": ["app/relatorio.py"]}), mode="shadow")
    outro = _servico(tmp_path / "b", FakeSemanticProvider(locality=ProviderLocality.REMOTE,
                                                          canned={"autenticacao": ["app/estoque.py"]}), mode="shadow")
    p1, p2 = um.gather(pergunta), outro.gather(pergunta)
    assert p1 is not None and p2 is not None and _caminhos(p1) == _caminhos(p2)


@pytest.mark.parametrize("fake", [
    FakeSemanticProvider(locality=ProviderLocality.REMOTE, fail_with=ProviderTimeout("lento")),
    FakeSemanticProvider(locality=ProviderLocality.REMOTE, invalid_response=True),
    FakeSemanticProvider(locality=ProviderLocality.REMOTE, empty=True),
    FakeSemanticProvider(locality=ProviderLocality.REMOTE, available_override=(False, "key_missing"))])
def test_sombra_falha_do_fake_nao_interfere_no_consumidor(tmp_path: Path, fake: FakeSemanticProvider) -> None:
    pergunta = "como funciona a autenticacao do sistema"
    pack = _servico(tmp_path, fake, mode="shadow").gather(pergunta)
    local = wiring.build_service(_cfg(tmp_path, enabled=True, mode="local_only"), root=tmp_path / "repo").gather(pergunta)
    assert pack is not None and local is not None and _caminhos(pack) == _caminhos(local)
    assert pack.metadata["fallback_used"] is True                 # a falha foi observada, não propagada


# ---------------------------------------------------------------- híbrido sem rede: EXACT, SEMANTIC, MIXED
def test_exact_a_salvaguarda_lexical_preserva_o_arquivo_mesmo_que_o_semantico_o_ignore(tmp_path: Path) -> None:
    falso = FakeSemanticProvider(locality=ProviderLocality.REMOTE,
                                 canned={"verify_token": ["app/relatorio.py", "app/estoque.py", "docs/seguranca.md"]})
    pack = _servico(tmp_path, falso).gather("onde `verify_token` é definido?", top_k=3)
    assert pack is not None and pack.files[0].path == "app/auth.py"       # o lexical manda no topo
    assert pack.metadata["safeguard"] is True and pack.metadata["fallback_used"] is False
    assert pack.regions[0].path == "app/auth.py"                          # e as janelas dele vêm primeiro
    assert "app/relatorio.py" in _caminhos(pack)                          # o semântico completou o top-k


def test_semantic_o_provedor_introduz_arquivo_ausente_do_top_local(tmp_path: Path) -> None:
    pergunta = "como funciona a autenticacao do sistema"
    falso = FakeSemanticProvider(locality=ProviderLocality.REMOTE, concepts={"autenticacao": ["login", "token"]})
    hib = _servico(tmp_path, falso).gather(pergunta, top_k=2)
    local = wiring.build_service(_cfg(tmp_path, enabled=True, mode="local_only"), root=tmp_path / "repo").gather(pergunta, top_k=2)
    assert hib is not None and local is not None
    assert "app/auth.py" not in _caminhos(local)                          # o BM25 não o acha (o vocabulário é outro)
    assert "app/auth.py" in _caminhos(hib) and hib.files[0].path == "app/auth.py"   # o semântico o traz, no topo
    assert hib.metadata["safeguard"] is False


def test_mixed_o_lexical_entra_no_conjunto_protegido_e_o_semantico_completa_o_top_k(tmp_path: Path) -> None:
    falso = FakeSemanticProvider(locality=ProviderLocality.REMOTE, concepts={"autenticacao": ["login", "token"]})
    pack = _servico(tmp_path, falso).gather("a `emitir_fatura` participa da autenticacao do gateway?", top_k=3)
    assert pack is not None and len(pack.files) == 3
    assert pack.files[0].path == "app/billing.py"                         # símbolo exato, protegido no topo
    assert "app/auth.py" in _caminhos(pack)                               # o conceito veio do semântico
    assert pack.metadata["safeguard"] is True
    assert len(set(_caminhos(pack))) == 3                                 # sem duplicata


def test_contrato_e_top_k_nao_top_1(tmp_path: Path) -> None:
    falso = FakeSemanticProvider(locality=ProviderLocality.REMOTE, concepts={"autenticacao": ["login", "token"]})
    for k in (3, 5):
        pack = _servico(tmp_path / f"k{k}", falso).gather("como funciona a autenticacao do sistema", top_k=k)
        assert pack is not None and 2 <= len(pack.files) <= k


# ---------------------------------------------------------------- cache x política
def test_cache_nunca_reintroduz_arquivo_que_a_politica_passou_a_bloquear(tmp_path: Path) -> None:
    pergunta = "como funciona a autenticacao do sistema"
    falso = FakeSemanticProvider(locality=ProviderLocality.REMOTE, concepts={"autenticacao": ["login", "token"]})
    cfg1 = _cfg(tmp_path, enabled=True, mode="hybrid", semantic={"provider": "fake", "repository_class": "public", "allow_public": True})
    raiz = _arquivos(tmp_path / "repo")
    antes = wiring.build_service(cfg1, root=raiz, provider=falso, visibility=FixedVisibilityVerifier()).gather(pergunta)
    assert antes is not None and "app/auth.py" in _caminhos(antes)
    chamadas = len(falso.calls)

    # agora a instalação passa a tratar `app/auth.py` como sensível; o MESMO cache em disco continua lá
    cfg2 = _cfg(tmp_path, enabled=True, mode="hybrid", sensitive_paths=["app/auth.py"],
                semantic={"provider": "fake", "repository_class": "public", "allow_public": True})
    depois = wiring.build_service(cfg2, root=raiz, provider=falso, visibility=FixedVisibilityVerifier()).gather(pergunta)
    assert depois is not None
    assert len(falso.calls) > chamadas                                       # outra política = outra chave: não serviu do cache
    assert "app/auth.py" not in _caminhos(depois)
    assert all("app/auth.py" not in c["paths"] for c in falso.calls[chamadas:])   # nem foi enviado de novo
    assert all(r.path != "app/auth.py" for r in depois.regions)


def test_arquivo_sensivel_nao_aparece_no_mapa_externo_nem_no_cache(tmp_path: Path) -> None:
    falso = FakeSemanticProvider(locality=ProviderLocality.REMOTE)
    servico = _servico(tmp_path, falso, cache=True)
    servico.gather("autenticacao do gateway")
    enviados = {p for c in falso.calls for p in c["paths"]}
    assert not enviados & {".env", "data/dump.json"}
    bruto = "".join(f.read_text(encoding="utf-8") for f in (tmp_path / "dados").rglob("*.json"))
    assert ".env" not in bruto and "data/dump.json" not in bruto


def test_portao_de_segredo_mole_falha_fechado_sem_redator_registrado(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.context_retrieval.domain import sensitive

    assert sensitive.has_soft_secret("password=abc12345") is True          # ligado pelo __init__ do pacote
    monkeypatch.setattr(sensitive, "_redator", None)
    with pytest.raises(RuntimeError):                                      # nunca "sem segredo" por falta de ligação
        sensitive.has_soft_secret("qualquer texto")


# ---------------------------------------------------------------- achados da revisão final (PR #18)
def test_chamada_que_falha_tambem_gasta_a_cota_da_sessao(tmp_path: Path) -> None:
    """Provedor falhando em laço não pode passar do teto: a tentativa conta, responda ou não (antes só a resposta contava)."""
    falso = FakeSemanticProvider(locality=ProviderLocality.REMOTE, fail_with=ProviderTimeout("timeout"))
    servico = _servico(tmp_path, falso, max_calls_per_session=3, max_calls=1)
    with servico.session():
        packs = [servico.gather(f"como funciona a autenticacao do sistema variante {i}") for i in range(8)]
    assert len(falso.calls) == 3                                    # nenhuma tentativa além do teto
    assert all(p is not None and p.files for p in packs)            # e o local entregou sempre
    assert packs[-1].metadata["fallback_reason"] == "budget_exceeded"   # type: ignore[union-attr]


def test_cache_do_mapa_com_numero_absurdo_ou_aninhamento_profundo_e_miss(tmp_path: Path) -> None:
    from app.modules.context_retrieval.domain.model import RETRIEVAL_VERSION
    from app.modules.context_retrieval.infrastructure import repomap

    entrada = {"path": "a.py", "language": "py", "size": float("inf"), "symbols": [], "summary": ""}
    bruto = {"revision": "r1", "version": RETRIEVAL_VERSION, "max_symbols": 8, "entries": [entrada]}
    assert repomap._de_dict(bruto, "r1", 8) is None                 # int(inf) = OverflowError: miss, nunca exceção


def test_chave_de_api_de_projeto_com_hifen_e_underscore_e_segredo_duro() -> None:
    from app.modules.context_retrieval.domain.sensitive import hard_secret_kind

    assert hard_secret_kind("chave: sk-proj-Ab12_Cd34-Ef56Gh78Ij90Kl12Mn34") is not None


def test_texto_da_regiao_usa_a_mesma_numeracao_de_linha_dos_retrievers(tmp_path: Path) -> None:
    """`\x0c` (form feed, comum em fontes GNU) quebra `str.splitlines()` mas não é fim de linha para os retrievers."""
    raiz = _arquivos(tmp_path / "repo")
    (raiz / "app" / "gnu.py").write_text("# cabecalho\n" + "\x0c\n" * 5 + "def funcao_unica_do_gnu():\n    return 1\n", encoding="utf-8")
    cfg = _cfg(tmp_path, enabled=True, mode="local_only")
    pack = wiring.build_service(cfg, root=raiz).gather("onde fica `funcao_unica_do_gnu`", with_text=True)
    assert pack is not None
    regiao = next(r for r in pack.regions if r.path == "app/gnu.py")
    assert "funcao_unica_do_gnu" in (regiao.text or "")


@pytest.mark.parametrize("onde", ["cache", "bm25"])
def test_falha_ao_gravar_nao_deixa_tmp_orfao(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, onde: str) -> None:
    import os

    from app.modules.context_retrieval.infrastructure.cache import SemanticCache

    def quebrar(origem: object, destino: object) -> None:
        raise PermissionError("antivirus segurando o arquivo")

    monkeypatch.setattr(os, "replace", quebrar)
    pasta = tmp_path / "c"
    if onde == "cache":
        SemanticCache(pasta).put("a" * 64, {"files": []})
    else:
        raiz = _arquivos(tmp_path / "repo")
        cfg = _cfg(tmp_path, enabled=True, mode="local_only")
        cfg.file.paths.data_dir = str(pasta.parent)
        wiring.build_service(cfg, root=raiz).gather("onde fica `verify_token`")
    sobras = [p.name for p in tmp_path.rglob("*.tmp")]
    assert sobras == []



# ---------------------------------------------------------------- política de envio: classe do repositório x localidade
def _contar_mapa_e_chunks(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Conta quantas vezes o mapa foi construído e quantos pedidos de chunk houve (o que a política tem de impedir)."""
    from app.modules.context_retrieval.infrastructure.chunker import Chunker
    from app.modules.context_retrieval.infrastructure.repomap import RepoMapProvider

    n = {"mapa": 0, "chunks": 0}
    mapa, chunks = RepoMapProvider.repo_map, Chunker.chunks_for

    def contar_mapa(self: Any, *a: Any, **k: Any) -> Any:
        n["mapa"] += 1
        return mapa(self, *a, **k)

    def contar_chunks(self: Any, *a: Any, **k: Any) -> Any:
        n["chunks"] += 1
        return chunks(self, *a, **k)

    monkeypatch.setattr(RepoMapProvider, "repo_map", contar_mapa)
    monkeypatch.setattr(Chunker, "chunks_for", contar_chunks)
    return n


def _build(tmp_path: Path, provider: FakeSemanticProvider, classe: str, allow_public: bool, **extra: Any):
    cfg = _cfg(tmp_path, enabled=True, mode="hybrid", **extra,
               semantic={"provider": "fake", "repository_class": classe, "allow_public": allow_public})
    raiz = tmp_path / "repo"
    if not raiz.exists():                      # não reescrever: o mtime entra na revisão e invalidaria o cache sozinho
        _arquivos(raiz)
    return wiring.build_service(cfg, root=raiz, provider=provider, visibility=FixedVisibilityVerifier(Visibility.PUBLIC))


@pytest.mark.parametrize("classe,allow_public", [("private", False), ("private", True),
                                                 ("synthetic", False), ("synthetic", True),
                                                 ("public", False)])
def test_remoto_bloqueado_nao_monta_mapa_nem_chunk_nem_chama(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                              classe: str, allow_public: bool) -> None:
    falso = FakeSemanticProvider(locality=ProviderLocality.REMOTE, concepts={"autenticacao": ["login", "token"]})
    n = _contar_mapa_e_chunks(monkeypatch)
    pack = _build(tmp_path, falso, classe, allow_public).gather("como funciona a autenticacao do sistema")
    assert pack is not None and pack.metadata["fallback_reason"] == "privacy_block"
    assert falso.calls == [] and n == {"mapa": 0, "chunks": 0}
    assert pack.files                                                      # e o local entregou mesmo assim


def test_publico_remoto_com_allow_public_explicito_e_permitido(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    falso = FakeSemanticProvider(locality=ProviderLocality.REMOTE, concepts={"autenticacao": ["login", "token"]})
    n = _contar_mapa_e_chunks(monkeypatch)
    pack = _build(tmp_path, falso, "public", True).gather("como funciona a autenticacao do sistema")
    assert pack is not None and not pack.metadata["fallback_used"]
    assert falso.calls and n["mapa"] >= 1


def test_sintetico_com_fake_e_local_nao_sofrem_bloqueio_externo(tmp_path: Path) -> None:
    for loc in (ProviderLocality.FAKE, ProviderLocality.LOCAL):
        falso = FakeSemanticProvider(locality=loc, concepts={"autenticacao": ["login", "token"]})
        pack = _build(tmp_path / loc.value, falso, "synthetic", False).gather("como funciona a autenticacao do sistema")
        assert pack is not None and not pack.metadata["fallback_used"], loc
        assert falso.calls, loc                                            # o pipeline de teste continua exercitável


def test_privado_local_nao_sofre_bloqueio_externo(tmp_path: Path) -> None:
    falso = FakeSemanticProvider(locality=ProviderLocality.LOCAL, concepts={"autenticacao": ["login", "token"]})
    pack = _build(tmp_path, falso, "private", False).gather("como funciona a autenticacao do sistema")
    assert pack is not None and not pack.metadata["fallback_used"] and falso.calls


@pytest.mark.parametrize("de,para", [("synthetic", "public"), ("private", "public")])
def test_mudar_a_classe_do_repositorio_nao_reaproveita_resposta_de_politica_antiga(tmp_path: Path, de: str,
                                                                                    para: str) -> None:
    pergunta = "como funciona a autenticacao do sistema"
    falso = FakeSemanticProvider(locality=ProviderLocality.FAKE, concepts={"autenticacao": ["login", "token"]})
    antes = _build(tmp_path, falso, de, False, cache={"enabled": True}).gather(pergunta)
    assert antes is not None and falso.calls
    chamadas = len(falso.calls)
    # a MESMA pergunta, o MESMO cache em disco, outra classe: é outra política, então outra chave (nada servido do cache)
    depois = _build(tmp_path, falso, para, True, cache={"enabled": True}).gather(pergunta)
    assert depois is not None and len(falso.calls) > chamadas
    # e repetir sob a política NOVA agora vem do cache
    apos_nova = len(falso.calls)
    de_novo = _build(tmp_path, falso, para, True, cache={"enabled": True}).gather(pergunta)
    assert de_novo is not None and len(falso.calls) == apos_nova
    from app.modules.context_retrieval.domain.policy import ExternalContextPolicy, RepositoryClass

    f = [ExternalContextPolicy(repository=RepositoryClass(c), locality=ProviderLocality.FAKE).fingerprint()
         for c in (de, para)]
    assert f[0] != f[1]
