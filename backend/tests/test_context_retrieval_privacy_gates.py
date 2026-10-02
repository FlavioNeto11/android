"""Retrieval de contexto: os dois últimos portões de privacidade.

1. `public` no YAML não basta: o envio a provedor REMOTE exige PROVA independente de que o repositório real é público
   (`RepositoryVisibilityVerifier`). Sem prova (privado, desconhecido, erro, remoto ausente) a política falha FECHADA: nenhum
   mapa, nenhum chunk, nenhuma chamada.
2. Segredo "mole" no mapa da etapa A (resumo de docstring, título de markdown, símbolo, caminho) não sai para o provedor.

`simulated`: verificador de mentira (`FixedVisibilityVerifier`) e `httpx.MockTransport` para o GitHub; nenhuma rede (um fixture
derruba `connect` fora de loopback) e nenhuma chamada ao Jev.
"""
from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.metricas import metricas
from app.modules.context_retrieval import wiring
from app.modules.context_retrieval.adapters import github_visibility as gv
from app.modules.context_retrieval.adapters.github_visibility import GitHubVisibilityVerifier, canonizar_remoto
from app.modules.context_retrieval.domain.model import Chunk, MapEntry
from app.modules.context_retrieval.domain.policy import ExternalContextPolicy, RepositoryClass
from app.modules.context_retrieval.domain.ports import ProviderLocality, RepositoryProof, Visibility
from app.modules.context_retrieval.infrastructure.providers.fake import FakeSemanticProvider, FixedVisibilityVerifier

from .test_context_retrieval_integration import _cfg, _eventos
from .test_context_retrieval_semantic import ChunksFalsos, MapaFalso, Montagem, _e, _fake, _politica

REMOTE = ProviderLocality.REMOTE
SEGREDO_DOCSTRING = "hunter2hunter2xyz"
SEGREDO_TITULO = "abcdefghij1234567890"


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch: pytest.MonkeyPatch) -> Any:
    tentativas: list[object] = []
    conectar = socket.socket.connect

    def barrar(self: socket.socket, endereco: Any) -> Any:
        if not (isinstance(endereco, tuple) and endereco and endereco[0] in ("127.0.0.1", "::1", "localhost")):
            tentativas.append(endereco)
            raise AssertionError("rede real no teste de retrieval")
        return conectar(self, endereco)

    monkeypatch.setattr(socket.socket, "connect", barrar)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    yield
    assert tentativas == []


def _repo(raiz: Path) -> Path:
    arquivos = {
        "app/cadastro.py": '"""Cadastro de clientes."""\n\n\ndef cadastrar_cliente(dados):\n    return dados\n',
        "app/carregador.py": f'"""Carrega a configuracao. password={SEGREDO_DOCSTRING}"""\n\n\ndef carregar_cadastro():\n    return 1\n',
        "docs/guia.md": "# Guia de cadastro\n\nComo cadastrar um cliente.\n",
        "docs/acesso.md": f'# Acesso api_key: "{SEGREDO_TITULO}"\n\nTexto sobre cadastro e acesso.\n',
    }
    for rel, texto in arquivos.items():
        p = raiz / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(texto, encoding="utf-8")
    return raiz


def _contar_mapa_e_chunks(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
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


def _servico(tmp_path: Path, provider: FakeSemanticProvider, classe: str, allow_public: bool,
             verificador: FixedVisibilityVerifier | None, **extra: Any):
    raiz = tmp_path / "repo"
    if not raiz.exists():
        _repo(raiz)
    cfg = _cfg(tmp_path, enabled=True, mode="hybrid", **extra,
               semantic={"provider": "fake", "repository_class": classe, "allow_public": allow_public})
    return wiring.build_service(cfg, root=raiz, provider=provider, visibility=verificador)


PERGUNTA = "como cadastrar um cliente no cadastro"


# ====================================================================== GATE 1 — política (sem serviço)
@pytest.mark.parametrize("visibilidade,esperado,razao", [
    (Visibility.PUBLIC, True, "allowed"),
    (Visibility.PRIVATE, False, "repository_not_public"),
    (Visibility.UNKNOWN, False, "repository_visibility_unverified"),
])
def test_publico_no_yaml_so_vale_com_a_prova_de_visibilidade(visibilidade: Visibility, esperado: bool, razao: str) -> None:
    v = FixedVisibilityVerifier(visibilidade)
    d = ExternalContextPolicy(repository=RepositoryClass.PUBLIC, locality=REMOTE, allow_public=True,
                              verifier=v).can_send_repository()
    assert d.allowed is esperado and d.reason == razao and d.hard is True
    assert v.verify_calls == 1


def test_sem_verificador_ou_com_verificador_que_levanta_a_politica_falha_fechada() -> None:
    sem = ExternalContextPolicy(repository=RepositoryClass.PUBLIC, locality=REMOTE, allow_public=True)
    assert sem.can_send_repository().reason == "repository_visibility_unverified"
    quebra = FixedVisibilityVerifier(raises=RuntimeError("rede caiu"))
    d = ExternalContextPolicy(repository=RepositoryClass.PUBLIC, locality=REMOTE, allow_public=True,
                              verifier=quebra).can_send_repository()
    assert not d.allowed and d.reason == "repository_visibility_unverified"


@pytest.mark.parametrize("classe", [RepositoryClass.PRIVATE, RepositoryClass.SYNTHETIC])
def test_a_prova_de_publico_nao_libera_privado_nem_sintetico(classe: RepositoryClass) -> None:
    v = FixedVisibilityVerifier(Visibility.PUBLIC)
    d = ExternalContextPolicy(repository=classe, locality=REMOTE, allow_public=True, verifier=v).can_send_repository()
    assert not d.allowed
    assert d.reason == ("private_repository" if classe is RepositoryClass.PRIVATE else "synthetic_repository")
    assert v.verify_calls == 0                      # nem pergunta: a configuração já negou


@pytest.mark.parametrize("loc", [ProviderLocality.LOCAL, ProviderLocality.FAKE])
def test_publico_com_provedor_local_ou_fake_nao_verifica_nada(loc: ProviderLocality) -> None:
    v = FixedVisibilityVerifier(Visibility.UNKNOWN)
    p = ExternalContextPolicy(repository=RepositoryClass.PUBLIC, locality=loc, allow_public=False, verifier=v)
    assert p.can_send_repository().allowed and p.peek_repository()[0].allowed
    assert v.verify_calls == 0 and v.peek_calls == 0


def test_peek_nunca_chama_verify() -> None:
    v = FixedVisibilityVerifier(Visibility.PUBLIC)
    d, prova = ExternalContextPolicy(repository=RepositoryClass.PUBLIC, locality=REMOTE, allow_public=True,
                                     verifier=v).peek_repository()
    assert d.allowed and prova is not None and prova.remote is Visibility.PUBLIC and v.verify_calls == 0 and v.peek_calls == 1


# ====================================================================== GATE 1 — serviço de ponta a ponta
@pytest.mark.parametrize("visibilidade", [Visibility.PRIVATE, Visibility.UNKNOWN])
def test_config_publica_com_repositorio_real_nao_publico_nao_monta_mapa_nem_chunk_nem_chama(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, visibilidade: Visibility) -> None:
    falso = FakeSemanticProvider(locality=REMOTE, concepts={"cadastro": ["cadastrar"]})
    n = _contar_mapa_e_chunks(monkeypatch)
    pack = _servico(tmp_path, falso, "public", True, FixedVisibilityVerifier(visibilidade)).gather(PERGUNTA)
    assert pack is not None and pack.metadata["fallback_reason"] == "privacy_block"
    assert falso.calls == [] and n == {"mapa": 0, "chunks": 0}
    assert pack.files                                                      # o local entregou mesmo assim


def test_erro_de_verificacao_nao_monta_mapa_nem_chunk_nem_chama(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    falso = FakeSemanticProvider(locality=REMOTE)
    n = _contar_mapa_e_chunks(monkeypatch)
    v = FixedVisibilityVerifier(raises=ConnectionError("sem rede"))
    pack = _servico(tmp_path, falso, "public", True, v).gather(PERGUNTA)
    assert pack is not None and pack.metadata["fallback_reason"] == "privacy_block"
    assert falso.calls == [] and n == {"mapa": 0, "chunks": 0}


def test_config_publica_com_repositorio_real_publico_e_permitido(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    falso = FakeSemanticProvider(locality=REMOTE, concepts={"cadastro": ["cadastrar"]})
    n = _contar_mapa_e_chunks(monkeypatch)
    pack = _servico(tmp_path, falso, "public", True, FixedVisibilityVerifier(Visibility.PUBLIC)).gather(PERGUNTA)
    assert pack is not None and not pack.metadata["fallback_used"]
    assert falso.calls and n["mapa"] >= 1


@pytest.mark.parametrize("classe", ["private", "synthetic"])
def test_config_privada_ou_sintetica_continua_bloqueada_mesmo_com_repositorio_real_publico(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, classe: str) -> None:
    falso = FakeSemanticProvider(locality=REMOTE)
    n = _contar_mapa_e_chunks(monkeypatch)
    pack = _servico(tmp_path, falso, classe, True, FixedVisibilityVerifier(Visibility.PUBLIC)).gather(PERGUNTA)
    assert pack is not None and pack.metadata["fallback_reason"] == "privacy_block"
    assert falso.calls == [] and n == {"mapa": 0, "chunks": 0}


@pytest.mark.parametrize("loc", [ProviderLocality.LOCAL, ProviderLocality.FAKE])
def test_publico_com_local_ou_fake_nao_faz_nenhuma_verificacao(tmp_path: Path, loc: ProviderLocality,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    def proibido(*a: object, **k: object) -> None:
        raise AssertionError("verificação de visibilidade onde não deveria haver")

    monkeypatch.setattr(GitHubVisibilityVerifier, "__init__", proibido)     # nem construído
    falso = FakeSemanticProvider(locality=loc, concepts={"cadastro": ["cadastrar"]})
    pack = _servico(tmp_path, falso, "public", True, None).gather(PERGUNTA)   # sem verificador injetado: vale o padrão
    assert pack is not None and not pack.metadata["fallback_used"] and falso.calls


@pytest.mark.parametrize("modo", ["local_only", "disabled"])
def test_local_only_e_desligado_nao_constroem_verificador_nem_tocam_a_rede(tmp_path: Path, modo: str,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    def proibido(*a: object, **k: object) -> None:
        raise AssertionError("verificação de visibilidade onde não deveria haver")

    monkeypatch.setattr(GitHubVisibilityVerifier, "__init__", proibido)
    monkeypatch.setattr(wiring, "criar_verificador", proibido)
    raiz = _repo(tmp_path / "repo")
    cfg = _cfg(tmp_path, enabled=modo != "disabled", mode=modo,
               semantic={"provider": "jev", "repository_class": "public", "allow_public": True})
    wiring.build_service(cfg, root=raiz).gather(PERGUNTA)


def test_sem_injecao_o_verificador_padrao_e_o_do_github_so_para_remoto_publico_com_allow(tmp_path: Path) -> None:
    def cfg(classe: str, allow: bool) -> Any:
        return _cfg(tmp_path, enabled=True, mode="hybrid",
                    semantic={"provider": "fake", "repository_class": classe, "allow_public": allow})

    raiz = _repo(tmp_path / "repo")
    assert isinstance(wiring.criar_verificador(cfg("public", True), raiz, REMOTE), GitHubVisibilityVerifier)
    assert wiring.criar_verificador(cfg("public", False), raiz, REMOTE) is None
    assert wiring.criar_verificador(cfg("private", True), raiz, REMOTE) is None
    assert wiring.criar_verificador(cfg("public", True), raiz, ProviderLocality.LOCAL) is None
    assert wiring.criar_verificador(cfg("public", True), raiz, ProviderLocality.FAKE) is None


def test_repositorio_sem_remoto_com_o_verificador_padrao_falha_fechado(tmp_path: Path) -> None:
    """Sem injeção, o verificador real lê o git: um diretório sem remoto não prova nada, e nem chega a ir ao GitHub."""
    falso = FakeSemanticProvider(locality=REMOTE)
    raiz = _repo(tmp_path / "repo")
    cfg = _cfg(tmp_path, enabled=True, mode="hybrid",
               semantic={"provider": "fake", "repository_class": "public", "allow_public": True})
    pack = wiring.build_service(cfg, root=raiz, provider=falso).gather(PERGUNTA)
    assert pack is not None and pack.metadata["fallback_reason"] == "privacy_block" and falso.calls == []


def test_o_smoke_publico_usa_a_mesma_regra_sem_atalho() -> None:
    """O script do Poetry constrói o serviço pela mesma `build_service`, sem injetar verificador: a regra é a de produção."""
    fonte = (Path(__file__).resolve().parents[2] / "scripts" / "context-retrieval-public-smoke.py").read_text(encoding="utf-8")
    assert "build_service(" in fonte and "visibility=" not in fonte and "FixedVisibilityVerifier" not in fonte
    assert "repository_class = 'public'" in fonte and "allow_public = True" in fonte


def test_nao_ha_campo_de_configuracao_que_dispense_a_verificacao() -> None:
    from app.config import ContextRetrievalSemanticCfg

    campos = [c for c in ContextRetrievalSemanticCfg.model_fields
              if any(t in c.lower() for t in ("visib", "verif", "skip", "bypass", "trust"))]
    assert campos == []


# ====================================================================== GATE 1 — o verificador do GitHub
def _publico(repo: str = "o/r", **corpo: Any) -> dict[str, Any]:
    return {"full_name": repo, "private": False, "visibility": "public", **corpo}


class _Github:
    """Transporte de mentira para `api.github.com`: guarda as requisições e responde `resposta(requisicao)`."""

    def __init__(self, resposta: Any) -> None:
        self.requisicoes: list[httpx.Request] = []
        self._resposta = resposta
        self.transport = httpx.MockTransport(self._tratar)

    def _tratar(self, req: httpx.Request) -> httpx.Response:
        self.requisicoes.append(req)
        r = self._resposta(req)
        if isinstance(r, Exception):
            raise r
        return r


def _verificador(remotos: list[str], github: _Github, tmp_path: Path | None = None, **kw: Any) -> GitHubVisibilityVerifier:
    kw.setdefault("worktree", lambda: True)
    kw.setdefault("head", lambda: None)            # estes testes são da visibilidade do REMOTO; o HEAD tem seção própria
    return GitHubVisibilityVerifier(Path("."), store_dir=tmp_path, transport=github.transport,
                                    remotes=lambda: list(remotos), **kw)


@pytest.mark.parametrize("url,esperado", [
    ("https://github.com/Python-Poetry/poetry.git", "github.com/python-poetry/poetry"),
    ("https://github.com/python-poetry/poetry", "github.com/python-poetry/poetry"),
    ("https://github.com/python-poetry/poetry/", "github.com/python-poetry/poetry"),
    ("https://usuario:token-que-nunca-aparece@github.com/o/r.git", "github.com/o/r"),
    ("git@github.com:o/r.git", "github.com/o/r"),
    ("ssh://git@github.com/o/r.git", "github.com/o/r"),
    ("git://github.com/o/r.git", "github.com/o/r"),
    ("https://github.com/o/r.js.git", "github.com/o/r.js"),
])
def test_remoto_canonico(url: str, esperado: str) -> None:
    assert canonizar_remoto(url) == esperado


@pytest.mark.parametrize("url", [
    "https://gitlab.com/o/r.git", "https://github.com.evil.example/o/r.git", "https://evil.example/github.com/o/r",
    "http://github.com/o/r.git", "https://github.com/o", "https://github.com/o/r/extra", "github.com/o/r",
    "/caminho/local/repo", "../repo", "", "https://github.com/../r", "file:///c:/x/github.com/o/r",
])
def test_remoto_nao_reconhecido_nao_e_github(url: str) -> None:
    assert canonizar_remoto(url) is None


def test_200_com_private_false_e_o_nome_certo_prova_publico_de_forma_anonima(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "valor-de-teste-nao-e-token")
    monkeypatch.setenv("GH_TOKEN", "valor-de-teste-nao-e-token")
    gh = _Github(lambda r: httpx.Response(200, json=_publico()))
    v = _verificador(["https://x:senha-de-teste@github.com/o/r.git"], gh)
    assert v.verify().remote is Visibility.PUBLIC
    (req,) = gh.requisicoes
    assert str(req.url) == "https://api.github.com/repos/o/r"
    assert "authorization" not in {k.lower() for k in req.headers}           # anônimo: nada de credencial
    assert "valor-de-teste" not in str(req.headers) and "senha-de-teste" not in str(req.url)


@pytest.mark.parametrize("resposta", [
    lambda r: httpx.Response(404, json={"message": "Not Found"}),
    lambda r: httpx.Response(403, json={"message": "rate limit"}),
    lambda r: httpx.Response(301, headers={"location": "https://api.github.com/repos/o/novo"}),
    lambda r: httpx.Response(500),
    lambda r: httpx.Response(200, text="nao e json"),
    lambda r: httpx.Response(200, json=["lista"]),
    lambda r: httpx.Response(200, json={"full_name": "o/r", "private": True}),
    lambda r: httpx.Response(200, json={"full_name": "o/r"}),
    lambda r: httpx.Response(200, json={"full_name": "o/r", "private": "false"}),
    lambda r: httpx.Response(200, json={"full_name": "o/r", "private": False, "visibility": "internal"}),
    lambda r: httpx.Response(200, json={"full_name": "outro/repo", "private": False}),
    lambda r: httpx.Response(200, json={"private": False}),
    lambda r: httpx.ConnectError("sem rede"),
    lambda r: httpx.ReadTimeout("lento"),
])
def test_qualquer_coisa_que_nao_prove_publico_e_unknown(resposta: Any) -> None:
    gh = _Github(resposta)
    v = _verificador(["https://github.com/o/r.git"], gh)
    assert v.verify().remote is Visibility.UNKNOWN


@pytest.mark.parametrize("remotos", [
    [], ["https://gitlab.com/o/r.git"], ["/caminho/local"], ["https://github.com/o/r.git", "https://gitlab.com/o/r.git"],
])
def test_remoto_ausente_ou_nao_suportado_e_unknown_sem_ir_ao_github(remotos: list[str]) -> None:
    gh = _Github(lambda r: httpx.Response(200, json=_publico()))
    assert _verificador(remotos, gh).verify().remote is Visibility.UNKNOWN
    assert gh.requisicoes == []


def test_leitura_dos_remotos_que_falha_e_unknown() -> None:
    def quebra() -> list[str]:
        raise OSError("git sumiu")

    gh = _Github(lambda r: httpx.Response(200, json=_publico()))
    v = GitHubVisibilityVerifier(Path("."), transport=gh.transport, remotes=quebra,
                                  worktree=lambda: True, head=lambda: None)
    assert v.verify().remote is Visibility.UNKNOWN and gh.requisicoes == []


def test_todos_os_remotos_precisam_ser_publicos() -> None:
    def resposta(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/o/privado"):
            return httpx.Response(404)
        return httpx.Response(200, json=_publico(req.url.path.removeprefix("/repos/")))

    gh = _Github(resposta)
    v = _verificador(["https://github.com/o/r.git", "https://github.com/o/privado.git"], gh)
    assert v.verify().remote is Visibility.UNKNOWN                  # um remoto "público" não limpa o outro
    ok = _verificador(["https://github.com/o/r.git", "git@github.com:o/r.git"], gh)
    assert ok.verify().remote is Visibility.PUBLIC                  # o mesmo repositório por duas URLs é um só


def test_prova_fica_em_cache_ate_o_ttl_e_depois_pergunta_de_novo() -> None:
    agora = [1000.0]
    gh = _Github(lambda r: httpx.Response(200, json=_publico()))
    v = _verificador(["https://github.com/o/r.git"], gh, clock=lambda: agora[0], ttl_publico_s=900)
    assert v.verify().remote is Visibility.PUBLIC and v.verify().remote is Visibility.PUBLIC
    assert len(gh.requisicoes) == 1
    agora[0] += 899
    assert v.verify().remote is Visibility.PUBLIC and len(gh.requisicoes) == 1
    agora[0] += 2                                            # passou do TTL: a prova caducou
    assert v.verify().remote is Visibility.PUBLIC and len(gh.requisicoes) == 2


def test_prova_caducada_que_agora_falha_nao_continua_publica() -> None:
    agora = [0.0]
    estado = {"publico": True}
    gh = _Github(lambda r: httpx.Response(200, json=_publico()) if estado["publico"] else httpx.Response(404))
    v = _verificador(["https://github.com/o/r.git"], gh, clock=lambda: agora[0], ttl_publico_s=900)
    assert v.verify().remote is Visibility.PUBLIC
    estado["publico"] = False                                # virou privado depois da prova
    agora[0] += 901
    assert v.verify().remote is Visibility.UNKNOWN and v.peek().remote is Visibility.UNKNOWN


def test_unknown_nunca_vira_publico_nem_vai_para_o_disco(tmp_path: Path) -> None:
    gh = _Github(lambda r: httpx.Response(404))
    v = _verificador(["https://github.com/o/r.git"], gh, tmp_path)
    assert v.verify().remote is Visibility.UNKNOWN and v.peek().remote is Visibility.UNKNOWN
    assert not (tmp_path / "visibility.json").exists()
    assert len(gh.requisicoes) == 1
    v.verify()                                               # dentro do TTL curto negativo: não bate de novo no GitHub
    assert len(gh.requisicoes) == 1


def test_trocar_o_remoto_invalida_a_prova_anterior(tmp_path: Path) -> None:
    remotos = ["https://github.com/o/r.git"]
    gh = _Github(lambda r: httpx.Response(200, json=_publico(r.url.path.removeprefix("/repos/"))))
    v = GitHubVisibilityVerifier(Path("."), store_dir=tmp_path, transport=gh.transport, remotes=lambda: list(remotos),
                                  worktree=lambda: True, head=lambda: None)
    assert v.verify().remote is Visibility.PUBLIC and v.peek().remote is Visibility.PUBLIC
    remotos[:] = ["https://github.com/o/outro.git"]          # o `origin` mudou
    assert v.peek().remote is Visibility.UNKNOWN                    # a prova anterior não vale (e peek não foi à rede)
    assert len(gh.requisicoes) == 1
    assert v.verify().remote is Visibility.PUBLIC and len(gh.requisicoes) == 2   # prova nova, para o repositório novo
    remotos[:] = ["https://github.com/o/r.git"]
    assert v.peek().remote is Visibility.PUBLIC                     # voltou: a prova do primeiro ainda vigente, por chave


def test_remoto_novo_privado_nao_herda_a_prova_do_antigo_publico(tmp_path: Path) -> None:
    remotos = ["https://github.com/o/r.git"]

    def resposta(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_publico("o/r")) if req.url.path.endswith("/o/r") else httpx.Response(404)

    gh = _Github(resposta)
    v = GitHubVisibilityVerifier(Path("."), store_dir=tmp_path, transport=gh.transport, remotes=lambda: list(remotos),
                                  worktree=lambda: True, head=lambda: None)
    assert v.verify().remote is Visibility.PUBLIC
    remotos[:] = ["https://github.com/o/privado.git"]
    assert v.verify().remote is Visibility.UNKNOWN


def test_peek_nunca_vai_a_rede_e_le_a_prova_gravada_em_disco(tmp_path: Path) -> None:
    gh = _Github(lambda r: httpx.Response(200, json=_publico()))
    escreve = _verificador(["https://github.com/o/r.git"], gh, tmp_path)
    assert escreve.verify().remote is Visibility.PUBLIC
    outro = _Github(lambda r: pytest.fail("peek foi à rede"))
    le = _verificador(["https://github.com/o/r.git"], outro, tmp_path)             # outra instância, mesmo disco
    assert le.peek().remote is Visibility.PUBLIC and outro.requisicoes == []
    # arquivo corrompido ou de outro formato: sem prova, sem exceção
    (tmp_path / "visibility.json").write_text("{nao e json", encoding="utf-8")
    assert _verificador(["https://github.com/o/r.git"], outro, tmp_path).peek().remote is Visibility.UNKNOWN
    (tmp_path / "visibility.json").write_text(json.dumps({"x": "y"}), encoding="utf-8")
    assert _verificador(["https://github.com/o/r.git"], outro, tmp_path).peek().remote is Visibility.UNKNOWN


def test_prova_em_disco_adulterada_para_outro_valor_ou_vencida_nao_vale(tmp_path: Path) -> None:
    gh = _Github(lambda r: httpx.Response(200, json=_publico()))
    agora = [5000.0]
    v = _verificador(["https://github.com/o/r.git"], gh, tmp_path, clock=lambda: agora[0], ttl_publico_s=900)
    v.verify()
    arq = tmp_path / "visibility.json"
    dados = json.loads(arq.read_text(encoding="utf-8"))
    (chave,) = dados
    arq.write_text(json.dumps({chave: {"visibility": "private", "verified_at": agora[0]}}), encoding="utf-8")
    assert _verificador(["https://github.com/o/r.git"], gh, tmp_path, clock=lambda: agora[0]).peek().remote is Visibility.UNKNOWN
    arq.write_text(json.dumps({chave: {"visibility": "public", "verified_at": agora[0] - 901}}), encoding="utf-8")
    assert _verificador(["https://github.com/o/r.git"], gh, tmp_path, clock=lambda: agora[0]).peek().remote is Visibility.UNKNOWN
    arq.write_text(json.dumps({chave: {"visibility": "public", "verified_at": agora[0] + 500}}), encoding="utf-8")   # do futuro
    assert _verificador(["https://github.com/o/r.git"], gh, tmp_path, clock=lambda: agora[0]).peek().remote is Visibility.UNKNOWN


def _git(raiz: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=raiz, check=True, capture_output=True)


def test_leitura_real_dos_remotos_do_git_inclui_todos_e_aplica_insteadof(tmp_path: Path) -> None:
    raiz = tmp_path / "r"
    raiz.mkdir()
    _git(raiz, "init", "-q")
    assert gv.ler_remotos_do_git(raiz) == []                                  # sem remoto
    _git(raiz, "remote", "add", "origin", "https://github.com/o/r.git")
    _git(raiz, "remote", "add", "upstream", "git@github.com:o/outro.git")
    assert {canonizar_remoto(u) for u in gv.ler_remotos_do_git(raiz)} == {"github.com/o/r", "github.com/o/outro"}
    _git(raiz, "config", "url.https://gitlab.com/.insteadOf", "https://github.com/")
    assert "github.com/o/r" not in {canonizar_remoto(u) for u in gv.ler_remotos_do_git(raiz)}   # reescrito: não é GitHub
    assert gv.ler_remotos_do_git(tmp_path / "nao-existe") == []


# ====================================================================== GATE 1 — status sem rede, sem presumir
def _status_cfg(tmp_path: Path, classe: str, allow_public: bool, provider: str = "jev") -> Any:
    return _cfg(tmp_path, enabled=True, mode="shadow",
                semantic={"provider": provider, "repository_class": classe, "allow_public": allow_public})


def _commitar(raiz: Path, msg: str = "c") -> str:
    _git(raiz, "add", "-A")
    _git(raiz, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-q",
         "--allow-empty", "-m", msg)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=raiz, check=True, capture_output=True,
                          text=True).stdout.strip()


def _repo_git(tmp_path: Path, origin: str) -> Path:
    """Repositório git real, com um commit e worktree limpo."""
    raiz = tmp_path / "repo"
    raiz.mkdir(parents=True, exist_ok=True)
    _git(raiz, "init", "-q")
    _git(raiz, "remote", "add", "origin", origin)
    _commitar(raiz)
    return raiz


def _github_publico(raiz: Path, repo: str = "o/r", *, commit_publico: bool = True) -> _Github:
    """GitHub de mentira: o repositório é público e o commit HEAD atual existe (ou não) publicamente."""
    def resposta(req: httpx.Request) -> httpx.Response:
        caminho = req.url.path.removeprefix("/repos/")
        if caminho == repo:
            return httpx.Response(200, json=_publico(repo))
        if caminho.startswith(f"{repo}/commits/") and commit_publico:
            return httpx.Response(200, json={"sha": caminho.rsplit("/", 1)[1]})
        return httpx.Response(404)

    return _Github(resposta)


def test_status_nao_afirma_autorizacao_so_pelo_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "valor-de-teste-nao-e-uma-chave")
    _repo_git(tmp_path, "https://github.com/o/r.git")

    def proibido(*a: object, **k: object) -> None:
        raise AssertionError("o status foi à rede")

    monkeypatch.setattr(GitHubVisibilityVerifier, "_consultar", proibido)
    envio = wiring.estado_do_retrieval(_status_cfg(tmp_path, "public", True))["external_send"]
    assert envio == {"allowed": False, "reason": "repository_visibility_unverified", "repository_class": "public",
                     "configured_for_remote": True, "visibility_verified": False, "visibility": "unverified",
                     "remote_visibility_verified": False, "head_public_verified": False, "worktree_clean": True}


def test_status_reflete_a_prova_vigente_e_deixa_de_valer_quando_o_remoto_muda(tmp_path: Path,
                                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "valor-de-teste-nao-e-uma-chave")
    raiz = _repo_git(tmp_path, "https://github.com/o/r.git")
    cfg = _status_cfg(tmp_path, "public", True)
    gh = _github_publico(raiz)
    GitHubVisibilityVerifier(raiz, store_dir=wiring.pasta_de_dados(cfg), transport=gh.transport).verify()   # prova gravada

    monkeypatch.setattr(GitHubVisibilityVerifier, "_get", lambda *a, **k: pytest.fail("o status foi à rede"))
    envio = wiring.estado_do_retrieval(cfg)["external_send"]
    assert envio == {"allowed": True, "reason": "allowed", "repository_class": "public", "configured_for_remote": True,
                     "visibility_verified": True, "visibility": "public", "remote_visibility_verified": True,
                     "head_public_verified": True, "worktree_clean": True}
    _git(raiz, "remote", "set-url", "origin", "https://github.com/o/outro.git")      # trocou o origin
    envio2 = wiring.estado_do_retrieval(cfg)["external_send"]
    assert envio2["allowed"] is False and envio2["visibility_verified"] is False
    assert envio2["reason"] == "repository_visibility_unverified"


@pytest.mark.parametrize("classe,allow_public,razao,configurado,estado", [
    ("private", True, "private_repository", False, "not_applicable"),
    ("synthetic", True, "synthetic_repository", False, "not_applicable"),
    ("public", False, "public_repository_not_enabled", False, "not_applicable"),
])
def test_status_para_as_classes_bloqueadas_pela_configuracao(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, classe: str,
                                                             allow_public: bool, razao: str, configurado: bool,
                                                             estado: str) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "valor-de-teste-nao-e-uma-chave")
    envio = wiring.estado_do_retrieval(_status_cfg(tmp_path, classe, allow_public))["external_send"]
    assert envio["allowed"] is False and envio["reason"] == razao
    assert envio["configured_for_remote"] is configurado and envio["visibility_verified"] is False
    assert envio["visibility"] == estado


def test_status_com_provedor_fake_nao_exige_prova_e_nao_cria_verificador(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(GitHubVisibilityVerifier, "__init__", lambda *a, **k: pytest.fail("criou verificador"))
    envio = wiring.estado_do_retrieval(_status_cfg(tmp_path, "public", True, provider="fake"))["external_send"]
    assert envio["allowed"] is True and envio["configured_for_remote"] is False and envio["visibility"] == "not_applicable"


# ====================================================================== GATE 2 — segredo mole no mapa da etapa A
def _entradas_com_segredo() -> tuple[MapEntry, ...]:
    return (
        _e("app/cadastro/cliente.py", ("cadastrar_cliente", "validar_cliente"), "cadastro de clientes"),
        _e("app/cadastro/carregador.py", ("carregar_cadastro",), f"Carrega a configuracao. password={SEGREDO_DOCSTRING}"),
        _e("docs/acesso.md", (f'Acesso api_key: "{SEGREDO_TITULO}"', "Cadastro"), "", "markdown"),
        _e("app/cadastro/simbolo.py", ('senha="abcdefg12345"', "outra_funcao"), "modulo comum"),
        _e('app/cadastro/senha_x="Zq8vXk29ab".txt', (), "arquivo com segredo no nome", "text"),
        _e('app/cadastro/token="abcdef123456".txt', (), "o matcher de caminho barra antes", "text"),
    )


def _texto_enviado(fake: FakeSemanticProvider) -> str:
    return "\n".join(str(c["text"]) + " ".join(c["paths"]) for c in fake.calls)


def test_etapa_a_so_leva_a_entrada_segura_e_omite_resumo_titulo_simbolo_e_caminho_com_segredo_mole() -> None:
    fake = _fake()
    chunks = {"app/cadastro/cliente.py": [Chunk("app/cadastro/cliente.py", 1, 10, "def cadastrar_cliente(d):\n    return d")]}
    m = Montagem(fake, politica=_politica(), mapa=MapaFalso(_entradas_com_segredo()), chunks=ChunksFalsos(chunks))
    sel = m.pedir("como cadastrar um cliente no cadastro")
    assert not sel.fallback_used
    enviado = _texto_enviado(fake)
    for segredo in (SEGREDO_DOCSTRING, SEGREDO_TITULO, "abcdefg12345", "abcdef123456", "Zq8vXk29ab"):
        assert segredo not in enviado
    assert "app/cadastro/cliente.py" in enviado
    for omitido in ("carregador.py", "acesso.md", "simbolo.py", "senha_x", "token="):
        assert omitido not in enviado
    assert sel.metadata["map_entries_withheld_secret"] == 4      # a do caminho com `token` o matcher de caminho barra antes
    assert sel.metadata["files_considered"] == 1


def test_a_omissao_e_so_da_saida_externa_o_provedor_local_ve_o_mapa_inteiro() -> None:
    fake = _fake(locality=ProviderLocality.LOCAL)
    m = Montagem(fake, politica=_politica(RepositoryClass.PRIVATE, ProviderLocality.LOCAL),
                 mapa=MapaFalso(_entradas_com_segredo()))
    m.pedir("como cadastrar um cliente no cadastro")
    assert "carregador.py" in _texto_enviado(fake)          # nada sai da máquina: o filtro de saída não se aplica


def test_segredo_duro_numa_entrada_do_mapa_continua_derrubando_o_pedido() -> None:
    fake = _fake()
    dura = _e("app/x.py", ("f",), "chave sk-" + "d" * 26)
    m = Montagem(fake, politica=_politica(), mapa=MapaFalso((_e("app/ok.py", ("g",)), dura)))
    sel = m.pedir("como cadastrar um cliente no cadastro")
    assert sel.fallback_used and sel.fallback_reason.value == "secret_block" and fake.calls == []


def test_mutacao_se_a_politica_deixasse_passar_o_segredo_mole_o_teste_reprova(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prova que o teste do mapa discrimina: com o filtro desligado, o segredo CHEGA ao provedor."""
    from app.modules.context_retrieval.domain import policy as policy_mod

    monkeypatch.setattr(policy_mod.ExternalContextPolicy, "can_send_map_entry", lambda self, texto: policy_mod._ALLOW)
    fake = _fake()
    Montagem(fake, politica=_politica(), mapa=MapaFalso(_entradas_com_segredo())).pedir("como cadastrar um cliente no cadastro")
    assert SEGREDO_DOCSTRING in _texto_enviado(fake) and SEGREDO_TITULO in _texto_enviado(fake)


def _tudo_em_disco(tmp_path: Path) -> str:
    dados = tmp_path / "dados"
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in sorted(dados.rglob("*")) if p.is_file())


def test_ponta_a_ponta_segredo_mole_nao_vai_ao_provedor_nem_a_evento_metrica_ou_cache(tmp_path: Path) -> None:
    metricas.limpar()
    falso = FakeSemanticProvider(locality=REMOTE, concepts={"cadastro": ["cadastrar", "carregar", "acesso"]})
    servico = _servico(tmp_path, falso, "public", True, FixedVisibilityVerifier(Visibility.PUBLIC))
    with servico.session():
        for q in (PERGUNTA, "carregar a configuracao do cadastro", "acesso ao cadastro"):
            pack = servico.gather(q)
            assert pack is not None
    assert falso.calls
    enviado = _texto_enviado(falso)
    segredos = (SEGREDO_DOCSTRING, SEGREDO_TITULO)
    assert all(s not in enviado for s in segredos)                                       # payload
    assert "app/cadastro.py" in enviado                                                  # o seguro foi
    eventos = json.dumps(_eventos(tmp_path))
    assert all(s not in eventos for s in segredos)                                       # eventos
    assert all(s not in json.dumps(metricas.snapshot(), default=str) for s in segredos)  # métricas
    em_disco = _tudo_em_disco(tmp_path)
    assert all(s not in em_disco for s in segredos)                                      # cache semântico, mapa e índice


def test_ponta_a_ponta_o_local_continua_vendo_o_arquivo_com_segredo_no_resumo(tmp_path: Path) -> None:
    """O filtro é de SAÍDA externa: BM25 e léxico locais seguem enxergando o corpus permitido."""
    raiz = _repo(tmp_path / "repo")
    cfg = _cfg(tmp_path, enabled=True, mode="local_only")
    pack = wiring.build_service(cfg, root=raiz).gather("onde fica `carregar_cadastro`")
    assert pack is not None and [f.path for f in pack.files][0] == "app/carregador.py"


# ====================================================================== GATE 3 — procedência: worktree limpo + HEAD público
# Remoto público não prova que o CONTEÚDO LOCAL é público: o universo do workspace inclui arquivo não rastreado e o HEAD local
# pode não ter sido publicado. `simulated`: git REAL em diretório temporário, GitHub de mentira (`httpx.MockTransport`).
LOCAL_PRIVADO = "private_local_algorithm.py"


def _repo_publicavel(tmp_path: Path) -> tuple[Path, str]:
    """Repositório git real com os arquivos do `_repo`, um commit e o `origin` apontando para `o/r`."""
    raiz = tmp_path / "repo"
    _repo(raiz)
    _git(raiz, "init", "-q")
    _git(raiz, "remote", "add", "origin", "https://github.com/o/r.git")
    return raiz, _commitar(raiz)


def _servico_real(tmp_path: Path, raiz: Path, gh: _Github, provider: FakeSemanticProvider):
    cfg = _cfg(tmp_path, enabled=True, mode="hybrid",
               semantic={"provider": "fake", "repository_class": "public", "allow_public": True})
    ver = GitHubVisibilityVerifier(raiz, store_dir=wiring.pasta_de_dados(cfg), transport=gh.transport)
    return wiring.build_service(cfg, root=raiz, provider=provider, visibility=ver), ver


def _politica_real(ver: GitHubVisibilityVerifier) -> ExternalContextPolicy:
    return ExternalContextPolicy(repository=RepositoryClass.PUBLIC, locality=REMOTE, allow_public=True, verifier=ver)


def test_remoto_publico_head_publico_e_worktree_limpo_e_permitido(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raiz, sha = _repo_publicavel(tmp_path)
    gh = _github_publico(raiz)
    falso = FakeSemanticProvider(locality=REMOTE, concepts={"cadastro": ["cadastrar"]})
    n = _contar_mapa_e_chunks(monkeypatch)
    servico, _ = _servico_real(tmp_path, raiz, gh, falso)
    pack = servico.gather(PERGUNTA)
    assert pack is not None and not pack.metadata["fallback_used"] and falso.calls and n["mapa"] >= 1
    assert [r.url.path for r in gh.requisicoes] == ["/repos/o/r", f"/repos/o/r/commits/{sha}"]   # anônimo, só leitura
    assert all("authorization" not in {k.lower() for k in r.headers} for r in gh.requisicoes)


def test_head_nao_publico_com_worktree_limpo_nao_monta_mapa_nem_chunk_nem_chama(tmp_path: Path,
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    raiz, _ = _repo_publicavel(tmp_path)                      # commit local que nunca foi publicado
    gh = _github_publico(raiz, commit_publico=False)
    falso = FakeSemanticProvider(locality=REMOTE, concepts={"cadastro": ["cadastrar"]})
    n = _contar_mapa_e_chunks(monkeypatch)
    servico, ver = _servico_real(tmp_path, raiz, gh, falso)
    pack = servico.gather(PERGUNTA)
    assert pack is not None and pack.metadata["fallback_reason"] == "privacy_block" and pack.files   # o local entregou
    assert falso.calls == [] and n == {"mapa": 0, "chunks": 0}
    d = _politica_real(ver).can_send_repository()
    assert not d.allowed and d.reason == "repository_head_not_public"
    prova = ver.verify()
    assert prova.remote is Visibility.PUBLIC and prova.worktree_clean is True and prova.head_public is False


def _sujar_modificado(raiz: Path) -> None:
    with (raiz / "app" / "cadastro.py").open("a", encoding="utf-8") as f:
        f.write("\n# alteracao local\n")


def _sujar_untracked(raiz: Path) -> None:
    (raiz / "app" / "novo.py").write_text("def novo():\n    return 1\n", encoding="utf-8")


def _sujar_deletado(raiz: Path) -> None:
    (raiz / "docs" / "guia.md").unlink()


def _sujar_staged(raiz: Path) -> None:
    _sujar_modificado(raiz)
    _git(raiz, "add", "app/cadastro.py")


def _sujar_renomeado(raiz: Path) -> None:
    _git(raiz, "mv", "docs/guia.md", "docs/guia2.md")


@pytest.mark.parametrize("sujar", [_sujar_modificado, _sujar_untracked, _sujar_deletado, _sujar_staged, _sujar_renomeado],
                         ids=["modificado", "untracked", "deletado", "staged", "renomeado"])
def test_worktree_sujo_nao_monta_mapa_nem_chunk_nem_chama_e_nem_vai_a_rede(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                           sujar: Any) -> None:
    raiz, _ = _repo_publicavel(tmp_path)
    sujar(raiz)
    gh = _github_publico(raiz)
    falso = FakeSemanticProvider(locality=REMOTE, concepts={"cadastro": ["cadastrar"]})
    n = _contar_mapa_e_chunks(monkeypatch)
    servico, ver = _servico_real(tmp_path, raiz, gh, falso)
    pack = servico.gather(PERGUNTA)
    assert pack is not None and pack.metadata["fallback_reason"] == "privacy_block" and pack.files
    assert falso.calls == [] and n == {"mapa": 0, "chunks": 0}
    assert gh.requisicoes == []                               # sujo: nem chegou a perguntar ao GitHub
    d = _politica_real(ver).can_send_repository()
    assert not d.allowed and d.reason == "repository_worktree_dirty"


def test_arquivo_local_privado_sem_segredo_nao_sai_mesmo_com_remoto_e_head_publicos(tmp_path: Path,
                                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Não depende do detector de segredo: o arquivo não tem nada que pareça credencial, mas é propriedade local."""
    from app.modules.context_retrieval.infrastructure.workspace import Workspace

    raiz, _ = _repo_publicavel(tmp_path)
    (raiz / LOCAL_PRIVADO).write_text(
        '"""Ranking proprietario de clientes."""\n\n\ndef ranking_proprietario(cadastro):\n'
        "    return sorted(cadastro, key=lambda c: c.valor)\n", encoding="utf-8")
    assert LOCAL_PRIVADO in Workspace(raiz).files()           # antes do gate ele ENTRA no universo
    gh = _github_publico(raiz)                                # remoto público e HEAD público: tudo "verde" menos o worktree
    falso = FakeSemanticProvider(locality=REMOTE, concepts={"cadastro": ["cadastrar", "ranking"]})
    n = _contar_mapa_e_chunks(monkeypatch)
    servico, _ = _servico_real(tmp_path, raiz, gh, falso)
    pack = servico.gather("ranking proprietario do cadastro de clientes")
    assert pack is not None and pack.metadata["fallback_reason"] == "privacy_block"
    assert falso.calls == [] and n == {"mapa": 0, "chunks": 0}
    assert LOCAL_PRIVADO not in json.dumps(falso.calls, default=str)


def test_depois_de_prova_valida_trocar_o_head_invalida_a_prova_antiga(tmp_path: Path) -> None:
    raiz, sha1 = _repo_publicavel(tmp_path)
    gh = _github_publico(raiz)
    ver = GitHubVisibilityVerifier(raiz, store_dir=tmp_path / "dados", transport=gh.transport)
    assert _politica_real(ver).can_send_repository().allowed
    assert ver.peek().head_public is True

    (raiz / "app" / "outro.py").write_text("def outro():\n    return 2\n", encoding="utf-8")
    sha2 = _commitar(raiz, "novo commit, nunca publicado")
    assert sha2 != sha1
    gh.requisicoes.clear()
    gh._resposta = lambda r: (httpx.Response(200, json=_publico()) if r.url.path == "/repos/o/r"
                              else httpx.Response(404))       # o commit novo não existe no GitHub
    assert ver.peek().head_public is False                    # a prova do sha1 não vale para o sha2 (e peek não foi à rede)
    assert gh.requisicoes == []
    d = _politica_real(ver).can_send_repository()
    assert not d.allowed and d.reason == "repository_head_not_public"
    assert f"/repos/o/r/commits/{sha2}" in [r.url.path for r in gh.requisicoes]   # perguntou pelo sha NOVO


def test_depois_de_prova_valida_sujar_o_worktree_bloqueia_na_hora_sem_rede_e_limpar_reaproveita(tmp_path: Path) -> None:
    raiz, _ = _repo_publicavel(tmp_path)
    gh = _github_publico(raiz)
    ver = GitHubVisibilityVerifier(raiz, store_dir=tmp_path / "dados", transport=gh.transport)
    pol = _politica_real(ver)
    assert pol.can_send_repository().allowed
    feitas = len(gh.requisicoes)

    (raiz / LOCAL_PRIVADO).write_text("def segredo_de_negocio():\n    return 42\n", encoding="utf-8")
    d, prova = pol.peek_repository()                          # status: sem rede, sem esperar TTL
    assert not d.allowed and d.reason == "repository_worktree_dirty"
    assert prova is not None and prova.worktree_clean is False and prova.remote is Visibility.PUBLIC and prova.head_public
    d2 = pol.can_send_repository()                            # retrieval: idem
    assert not d2.allowed and d2.reason == "repository_worktree_dirty"
    assert len(gh.requisicoes) == feitas                      # nenhuma rede adicional

    (raiz / LOCAL_PRIVADO).unlink()                           # limpou de novo: a prova do MESMO HEAD ainda vigente serve
    assert pol.can_send_repository().allowed and len(gh.requisicoes) == feitas


def test_git_que_nao_responde_nao_prova_worktree_limpo(tmp_path: Path) -> None:
    sem_git = tmp_path / "vazio"
    sem_git.mkdir()
    assert gv.worktree_limpo(sem_git) is None and gv.ler_head(sem_git) is None
    gh = _github_publico(sem_git)
    ver = GitHubVisibilityVerifier(sem_git, transport=gh.transport, remotes=lambda: ["https://github.com/o/r.git"])
    d = _politica_real(ver).can_send_repository()
    assert not d.allowed and d.reason == "repository_visibility_unverified"
    assert gh.requisicoes == []


def test_leitura_real_do_estado_do_git_cobre_cada_tipo_de_alteracao(tmp_path: Path) -> None:
    raiz, sha = _repo_publicavel(tmp_path)
    assert gv.worktree_limpo(raiz) is True and gv.ler_head(raiz) == sha
    for sujar in (_sujar_modificado, _sujar_untracked, _sujar_deletado, _sujar_staged, _sujar_renomeado):
        _git(raiz, "reset", "-q", "--hard")
        _git(raiz, "clean", "-fdq")
        assert gv.worktree_limpo(raiz) is True
        sujar(raiz)
        assert gv.worktree_limpo(raiz) is False, sujar.__name__


def test_arquivo_ignorado_nao_suja_porque_o_workspace_tambem_nao_o_le(tmp_path: Path) -> None:
    from app.modules.context_retrieval.infrastructure.workspace import Workspace

    raiz, _ = _repo_publicavel(tmp_path)
    (raiz / ".gitignore").write_text("dados/\n", encoding="utf-8")
    _commitar(raiz, "ignora dados/")
    (raiz / "dados").mkdir()
    (raiz / "dados" / "cache.json").write_text("{}", encoding="utf-8")
    assert gv.worktree_limpo(raiz) is True and "dados/cache.json" not in Workspace(raiz).files()


# ---- HEAD no GitHub
SHA_A = "a" * 40


def _head_verif(remotos: list[str], gh: _Github, *, head: str | None = SHA_A, limpo: bool | None = True,
                **kw: Any) -> GitHubVisibilityVerifier:
    return GitHubVisibilityVerifier(Path("."), transport=gh.transport, remotes=lambda: list(remotos),
                                    worktree=lambda: limpo, head=lambda: head, **kw)


def _gh_commit(resposta_commit: Any, repo: str = "o/r") -> _Github:
    return _Github(lambda r: httpx.Response(200, json=_publico(repo)) if r.url.path == f"/repos/{repo}"
                   else resposta_commit(r))


@pytest.mark.parametrize("resposta", [
    lambda r: httpx.Response(404, json={"message": "No commit found"}),
    lambda r: httpx.Response(403, json={"message": "rate limit"}),
    lambda r: httpx.Response(500),
    lambda r: httpx.Response(301, headers={"location": "https://api.github.com/repos/o/novo/commits/x"}),
    lambda r: httpx.Response(302, headers={"location": "https://api.github.com/x"}),
    lambda r: httpx.Response(200, text="nao e json"),
    lambda r: httpx.Response(200, json=["lista"]),
    lambda r: httpx.Response(200, json={}),
    lambda r: httpx.Response(200, json={"sha": "b" * 40}),                       # outro commit: incoerente
    lambda r: httpx.Response(200, json={"sha": SHA_A[:-1]}),                     # só um prefixo
    lambda r: httpx.Response(200, json={"sha": 123}),
    lambda r: httpx.ConnectError("sem rede"),
    lambda r: httpx.ReadTimeout("lento"),
])
def test_head_so_vale_com_200_e_o_sha_exatamente_igual(resposta: Any) -> None:
    prova = _head_verif(["https://github.com/o/r.git"], _gh_commit(resposta)).verify()
    assert prova.remote is Visibility.PUBLIC and prova.worktree_clean is True and prova.head_public is False


def test_head_publico_com_200_e_o_mesmo_sha_e_anonimo() -> None:
    gh = _gh_commit(lambda r: httpx.Response(200, json={"sha": SHA_A.upper()}))     # a comparação ignora caixa
    prova = _head_verif(["https://x:senha-de-teste@github.com/o/r.git"], gh).verify()
    assert prova == RepositoryProof(remote=Visibility.PUBLIC, head_public=True, worktree_clean=True)
    assert [str(r.url) for r in gh.requisicoes] == ["https://api.github.com/repos/o/r",
                                                    f"https://api.github.com/repos/o/r/commits/{SHA_A}"]
    assert "senha-de-teste" not in str([r.headers for r in gh.requisicoes]) and \
        all("authorization" not in {k.lower() for k in r.headers} for r in gh.requisicoes)


def test_head_basta_existir_em_um_dos_remotos_publicos() -> None:
    def resposta(req: httpx.Request) -> httpx.Response:
        caminho = req.url.path.removeprefix("/repos/")
        if caminho in ("o/r", "o/espelho"):
            return httpx.Response(200, json=_publico(caminho))
        return httpx.Response(200, json={"sha": SHA_A}) if caminho == f"o/espelho/commits/{SHA_A}" else httpx.Response(404)

    remotos = ["https://github.com/o/r.git", "https://github.com/o/espelho.git"]
    assert _head_verif(remotos, _Github(resposta)).verify().head_public is True
    nenhum = _head_verif(remotos, _Github(lambda r: httpx.Response(200, json=_publico(r.url.path.removeprefix("/repos/")))
                                          if "/commits/" not in r.url.path else httpx.Response(404)))
    assert nenhum.verify().head_public is False


@pytest.mark.parametrize("head", [None, "", "HEAD", "main", "abc123", "g" * 40, "a" * 41])
def test_head_ilegivel_ou_fora_do_formato_nao_prova_nada_nem_vai_ao_github_pelo_commit(head: str | None) -> None:
    gh = _gh_commit(lambda r: httpx.Response(200, json={"sha": SHA_A}))
    prova = _head_verif(["https://github.com/o/r.git"], gh, head=head).verify()
    assert prova.head_public is False
    assert not any("/commits/" in r.url.path for r in gh.requisicoes)


def test_remoto_nao_publico_nao_pergunta_pelo_head() -> None:
    gh = _Github(lambda r: httpx.Response(404))
    prova = _head_verif(["https://github.com/o/r.git"], gh).verify()
    assert prova.remote is Visibility.UNKNOWN and prova.head_public is False and len(gh.requisicoes) == 1


def test_worktree_sujo_ou_ilegivel_nao_vai_a_rede_e_nao_perde_a_prova_vigente() -> None:
    gh = _gh_commit(lambda r: httpx.Response(200, json={"sha": SHA_A}))
    estado: dict[str, bool | None] = {"limpo": True}
    v = GitHubVisibilityVerifier(Path("."), transport=gh.transport, remotes=lambda: ["https://github.com/o/r.git"],
                                 worktree=lambda: estado["limpo"], head=lambda: SHA_A)
    assert v.verify() == RepositoryProof(Visibility.PUBLIC, True, True)
    feitas = len(gh.requisicoes)
    estado["limpo"] = False
    assert v.verify() == RepositoryProof(Visibility.PUBLIC, True, False) and v.peek().worktree_clean is False
    estado["limpo"] = None
    assert v.verify().worktree_clean is None and v.peek().worktree_clean is None
    assert len(gh.requisicoes) == feitas
    estado["limpo"] = True                                    # limpou: a prova do mesmo HEAD volta a valer sem rede
    assert v.verify() == RepositoryProof(Visibility.PUBLIC, True, True) and len(gh.requisicoes) == feitas


def test_prova_do_head_e_por_sha_e_vai_para_o_disco_so_quando_publica(tmp_path: Path) -> None:
    gh = _gh_commit(lambda r: httpx.Response(200, json={"sha": r.url.path.rsplit("/", 1)[1]}))
    head = [SHA_A]
    v = GitHubVisibilityVerifier(Path("."), store_dir=tmp_path, transport=gh.transport,
                                 remotes=lambda: ["https://github.com/o/r.git"], worktree=lambda: True, head=lambda: head[0])
    v.verify()
    head[0] = "b" * 40
    assert v.peek().head_public is False and v.peek().remote is Visibility.PUBLIC    # outro SHA: sem prova
    outro = GitHubVisibilityVerifier(Path("."), store_dir=tmp_path, transport=_Github(lambda r: pytest.fail("rede")).transport,
                                     remotes=lambda: ["https://github.com/o/r.git"], worktree=lambda: True,
                                     head=lambda: SHA_A)
    assert outro.peek() == RepositoryProof(Visibility.PUBLIC, True, True)            # outra instância lê o disco do SHA_A
    nega = _gh_commit(lambda r: httpx.Response(404))
    novo = tmp_path / "novo"
    _head_verif(["https://github.com/o/r.git"], nega, store_dir=novo).verify()
    assert "b" * 40 not in json.dumps(json.loads((novo / "visibility.json").read_text(encoding="utf-8")))
    assert len(json.loads((novo / "visibility.json").read_text(encoding="utf-8"))) == 1   # só o remoto; head negativo não grava


# ---- política e status
@pytest.mark.parametrize("prova,razao", [
    (RepositoryProof(Visibility.PUBLIC, True, True), "allowed"),
    (RepositoryProof(Visibility.PUBLIC, True, False), "repository_worktree_dirty"),
    (RepositoryProof(Visibility.PUBLIC, False, False), "repository_worktree_dirty"),
    (RepositoryProof(Visibility.UNKNOWN, False, False), "repository_worktree_dirty"),
    (RepositoryProof(Visibility.PUBLIC, False, True), "repository_head_not_public"),
    (RepositoryProof(Visibility.PUBLIC, True, None), "repository_visibility_unverified"),
    (RepositoryProof(Visibility.UNKNOWN, True, True), "repository_visibility_unverified"),
    (RepositoryProof(Visibility.PRIVATE, True, True), "repository_not_public"),
    (RepositoryProof(), "repository_visibility_unverified"),
])
def test_a_politica_exige_as_tres_provas(prova: RepositoryProof, razao: str) -> None:
    class V:
        def verify(self) -> RepositoryProof:
            return prova

        def peek(self) -> RepositoryProof:
            return prova

    pol = ExternalContextPolicy(repository=RepositoryClass.PUBLIC, locality=REMOTE, allow_public=True, verifier=V())
    assert pol.can_send_repository().reason == razao and pol.peek_repository()[0].reason == razao
    assert pol.can_send_repository().allowed is (razao == "allowed")


def test_status_com_remoto_provado_mas_head_nao_provado_nao_esta_permitido(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "valor-de-teste-nao-e-uma-chave")
    raiz = _repo_git(tmp_path, "https://github.com/o/r.git")
    cfg = _status_cfg(tmp_path, "public", True)
    gh = _github_publico(raiz, commit_publico=False)
    GitHubVisibilityVerifier(raiz, store_dir=wiring.pasta_de_dados(cfg), transport=gh.transport).verify()
    monkeypatch.setattr(GitHubVisibilityVerifier, "_get", lambda *a, **k: pytest.fail("o status foi à rede"))
    envio = wiring.estado_do_retrieval(cfg)["external_send"]
    assert envio["allowed"] is False and envio["reason"] == "repository_head_not_public"
    assert (envio["remote_visibility_verified"], envio["head_public_verified"], envio["worktree_clean"]) == (True, False, True)


def test_status_com_head_publico_provado_mas_worktree_sujo_bloqueia_na_hora_e_sem_rede(tmp_path: Path,
                                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "valor-de-teste-nao-e-uma-chave")
    raiz = _repo_git(tmp_path, "https://github.com/o/r.git")
    cfg = _status_cfg(tmp_path, "public", True)
    GitHubVisibilityVerifier(raiz, store_dir=wiring.pasta_de_dados(cfg), transport=_github_publico(raiz).transport).verify()
    monkeypatch.setattr(GitHubVisibilityVerifier, "_get", lambda *a, **k: pytest.fail("o status foi à rede"))
    assert wiring.estado_do_retrieval(cfg)["external_send"]["allowed"] is True
    (raiz / LOCAL_PRIVADO).write_text("def privado():\n    return 1\n", encoding="utf-8")
    envio = wiring.estado_do_retrieval(cfg)["external_send"]
    assert envio["allowed"] is False and envio["reason"] == "repository_worktree_dirty"
    assert (envio["remote_visibility_verified"], envio["head_public_verified"], envio["worktree_clean"]) == (True, True, False)


def test_status_sem_exigencia_de_prova_nao_afirma_nada_sobre_o_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    envio = wiring.estado_do_retrieval(_status_cfg(tmp_path, "public", True, provider="fake"))["external_send"]
    assert envio["allowed"] is True and envio["worktree_clean"] is None
    assert envio["remote_visibility_verified"] is False and envio["head_public_verified"] is False


@pytest.mark.parametrize("loc", [ProviderLocality.LOCAL, ProviderLocality.FAKE])
def test_local_e_fake_aceitam_worktree_sujo_e_nao_dependem_da_prova_remota(tmp_path: Path, loc: ProviderLocality,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(GitHubVisibilityVerifier, "__init__", lambda *a, **k: pytest.fail("verificador construído"))
    raiz, _ = _repo_publicavel(tmp_path)
    _sujar_untracked(raiz)
    (raiz / LOCAL_PRIVADO).write_text("def privado():\n    return 1\n", encoding="utf-8")
    falso = FakeSemanticProvider(locality=loc, concepts={"cadastro": ["cadastrar"]})
    cfg = _cfg(tmp_path, enabled=True, mode="hybrid",
               semantic={"provider": "fake", "repository_class": "public", "allow_public": True})
    pack = wiring.build_service(cfg, root=raiz, provider=falso).gather(PERGUNTA)
    assert pack is not None and not pack.metadata["fallback_used"] and falso.calls


def test_o_retrieval_local_continua_funcionando_com_worktree_sujo(tmp_path: Path) -> None:
    raiz, _ = _repo_publicavel(tmp_path)
    (raiz / LOCAL_PRIVADO).write_text("def cadastro_privado():\n    return 1\n", encoding="utf-8")
    cfg = _cfg(tmp_path, enabled=True, mode="local_only",
               semantic={"provider": "jev", "repository_class": "public", "allow_public": True})
    pack = wiring.build_service(cfg, root=raiz).gather("cadastro privado")
    assert pack is not None and any(f.path == LOCAL_PRIVADO for f in pack.files)


# ---- versão e cache
def test_a_semantica_do_que_pode_sair_mudou_e_a_versao_e_a_politica_invalidam_o_cache(tmp_path: Path,
                                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.context_retrieval.domain import model
    from app.modules.context_retrieval.infrastructure import cache as cache_mod

    assert model.RETRIEVAL_VERSION != "2"
    assert "provenance2" in ExternalContextPolicy(repository=RepositoryClass.PUBLIC, locality=REMOTE,
                                                  allow_public=True).fingerprint()
    cache = cache_mod.SemanticCache(tmp_path / "c", enabled=True)
    args = dict(revision="r", query="q", scope=("a",), provider="p", model="m", stage="A")
    atual = cache.key(**args)
    monkeypatch.setattr(cache_mod, "RETRIEVAL_VERSION", "2")
    assert cache.key(**args) != atual                         # resposta dada sob a versão antiga nunca é reaproveitada
