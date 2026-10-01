"""Retrieval de contexto de ponta a ponta, num mini-repositório em tmp_path: configuração → serviço → pacote.

`simulated`: retrievers locais REAIS (ripgrep/BM25/mapa/chunker) sobre arquivos sintéticos, provedor semântico FALSO ou
Jev com chave ausente. Nenhuma rede (um fixture derruba `socket.connect`), nenhum repositório real, nenhum `.env` lido
(`EnvSettings(_env_file=None)`). É onde se prova o que vale para o produto: nada muda com o recurso desligado, caminho
sensível e segredo nunca chegam ao provedor, e toda falha do semântico cai no local.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppConfigFile, Config, EnvSettings
from app.modules.context_retrieval import wiring
from app.modules.context_retrieval.application.service import DisabledContextRetrieval
from app.modules.context_retrieval.domain.errors import ProviderTimeout
from app.modules.context_retrieval.domain.model import RetrievalMode
from app.modules.context_retrieval.domain.ports import ProviderLocality
from app.modules.context_retrieval.infrastructure.providers.fake import FakeSemanticProvider
from app.modules.context_retrieval.presentation import cli
from app.modules.context_retrieval.presentation.router import router


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch: pytest.MonkeyPatch) -> Any:
    tentativas: list[object] = []
    conectar = socket.socket.connect
    criar = socket.create_connection

    def externo(endereco: object) -> bool:
        # o loop do asyncio e o TestClient abrem um `socketpair` em 127.0.0.1: isso não é rede.
        return not (isinstance(endereco, tuple) and endereco and endereco[0] in ("127.0.0.1", "::1", "localhost"))

    def barrar_connect(self: socket.socket, endereco: Any) -> Any:
        if externo(endereco):
            tentativas.append(endereco)
            raise AssertionError("tentativa de conexão de rede no teste de retrieval")
        return conectar(self, endereco)

    def barrar_create(endereco: Any, *a: Any, **k: Any) -> Any:
        if externo(endereco):
            tentativas.append(endereco)
            raise AssertionError("tentativa de conexão de rede no teste de retrieval")
        return criar(endereco, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", barrar_connect)
    monkeypatch.setattr(socket, "create_connection", barrar_create)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    yield
    assert tentativas == []                       # REAL_JEV_NETWORK_CALLS = 0


AUTH = '''"""Autenticação de usuários: valida credenciais e emite a sessão."""


def verify_token(token: str) -> bool:
    """Confere se o token de sessão ainda vale."""
    return token.startswith("sess-") and len(token) > 8


def login_user(name: str, secret_hash: str) -> str:
    return "sess-" + name + secret_hash[:6]
'''
BILLING = '''"""Cobrança: emite faturas e calcula o total do mês."""


def issue_invoice(customer: str, cents: int) -> dict:
    return {"customer": customer, "cents": cents}
'''


def _repo(tmp_path: Path) -> Path:
    raiz = tmp_path / "repo"
    arquivos = {"app/auth.py": AUTH, "app/billing.py": BILLING, "docs/guide.md": "# Guia\n\nComo rodar o projeto.\n",
                ".env": "SEGREDO_FAKE=1\n", "data/clientes.json": '{"x": 1}\n',
                "config/config.yaml": "api: valor-do-ambiente\n",
                "app/leak.py": "KEY = '" + "sk-" + "z" * 24 + "'\nlogin = 'usuario'\n"}
    for rel, texto in arquivos.items():
        p = raiz / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(texto, encoding="utf-8")
    return raiz


def _cfg(tmp_path: Path, **context_retrieval: Any) -> Config:
    arquivo = AppConfigFile.model_validate({"paths": {"data_dir": str(tmp_path / "dados")},
                                            "context_retrieval": context_retrieval})
    return Config(arquivo, EnvSettings(_env_file=None), root=tmp_path / "repo")


def _eventos(tmp_path: Path) -> list[dict[str, Any]]:
    arq = tmp_path / "dados" / "context_retrieval" / "events.jsonl"
    return [json.loads(x) for x in arq.read_text(encoding="utf-8").splitlines()] if arq.exists() else []


def _servico(tmp_path: Path, provider: FakeSemanticProvider | None = None, **cfg: Any):
    raiz = _repo(tmp_path) if not (tmp_path / "repo").exists() else tmp_path / "repo"
    return wiring.build_service(_cfg(tmp_path, **cfg), root=raiz, provider=provider)


# ---------------------------------------------------------------- feature desligada
def test_desligado_nem_abre_o_repositorio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("o retrieval desligado não pode tocar no repositório")

    monkeypatch.setattr(wiring, "Workspace", boom)
    monkeypatch.setattr(wiring, "RetrievalMetrics", boom)
    for cfg in (_cfg(tmp_path), _cfg(tmp_path, enabled=False, mode="hybrid"), _cfg(tmp_path, enabled=True)):
        servico = wiring.build_service(cfg, root=_repo(tmp_path))
        assert isinstance(servico, DisabledContextRetrieval)
        assert servico.gather("`verify_token`") is None
    assert not (tmp_path / "dados").exists()          # nem diretório de cache, nem de métricas


# ---------------------------------------------------------------- local_only
def test_local_only_acha_o_simbolo_exato_e_registra_metricas_sem_texto(tmp_path: Path) -> None:
    servico = _servico(tmp_path, enabled=True, mode="local_only")
    pack = servico.gather("onde `verify_token` é definido?")
    assert pack is not None and pack.origin == "local"
    assert pack.files[0].path == "app/auth.py"
    assert any(r.path == "app/auth.py" for r in pack.regions)
    bruto = (tmp_path / "dados" / "context_retrieval" / "events.jsonl").read_text(encoding="utf-8")
    assert "verify_token" not in bruto and "def " not in bruto
    assert _eventos(tmp_path)[-1]["mode"] == "local_only" and _eventos(tmp_path)[-1]["files_selected"] >= 1


def test_arquivo_sensivel_nunca_aparece_nem_no_local(tmp_path: Path) -> None:
    pack = _servico(tmp_path, enabled=True, mode="local_only", top_k=20).gather("SEGREDO_FAKE clientes api valor")
    assert pack is not None
    caminhos = {f.path for f in pack.files} | {r.path for r in pack.regions}
    assert not caminhos & {".env", "data/clientes.json", "config/config.yaml"}


def test_pergunta_por_significado_com_bm25(tmp_path: Path) -> None:
    pack = _servico(tmp_path, enabled=True, mode="local_only").gather("emitir fatura de cobrança do cliente")
    assert pack is not None and pack.files and pack.files[0].path == "app/billing.py"


# ---------------------------------------------------------------- hybrid / shadow com o falso
def test_hibrido_com_falso_acha_o_que_o_lexico_nao_acha(tmp_path: Path) -> None:
    falso = FakeSemanticProvider(locality=ProviderLocality.FAKE, concepts={"autenticação": ["login", "token"]})
    servico = _servico(tmp_path, falso, enabled=True, mode="hybrid", semantic={"provider": "fake"})
    pack = servico.gather("como funciona a autenticação do sistema")
    assert pack is not None and pack.origin == "hybrid" and pack.metadata["fallback_used"] is False
    assert "app/auth.py" in [f.path for f in pack.files]
    # o que saiu da máquina (para o falso) não tem caminho sensível nem o arquivo com segredo duro
    enviados = {p for c in falso.calls for p in c["paths"]}
    assert not enviados & {".env", "data/clientes.json", "config/config.yaml"}
    assert all("sk-" + "z" * 24 not in c["text"] for c in falso.calls)


def test_hibrido_cache_hit_na_segunda_e_miss_quando_o_codigo_muda(tmp_path: Path) -> None:
    falso = FakeSemanticProvider(concepts={"autenticação": ["login"]}, cost_usd_per_call=0.001, tokens_per_call=50)
    servico = _servico(tmp_path, falso, enabled=True, mode="hybrid", semantic={"provider": "fake"})
    servico.gather("como funciona a autenticação")
    chamadas = len(falso.calls)
    assert chamadas >= 1
    servico.gather("como funciona a autenticação")
    assert len(falso.calls) == chamadas                                  # cache: o provedor não foi chamado de novo
    assert _eventos(tmp_path)[-1]["cache"] == "hit" and _eventos(tmp_path)[-1]["cost_usd"] == 0
    (tmp_path / "repo" / "app" / "billing.py").write_text(BILLING + "\n# editado\n", encoding="utf-8")
    novo = wiring.build_service(_cfg(tmp_path, enabled=True, mode="hybrid", semantic={"provider": "fake"}),
                                root=tmp_path / "repo", provider=falso)    # processo novo: ws sem memória
    novo.gather("como funciona a autenticação")
    assert len(falso.calls) > chamadas                                   # revisão nova = miss
    assert _eventos(tmp_path)[-1]["cache"] == "miss"


def test_shadow_entrega_o_local_e_o_evento_traz_o_custo_do_semantico(tmp_path: Path) -> None:
    falso = FakeSemanticProvider(cost_usd_per_call=0.002, tokens_per_call=40)
    servico = _servico(tmp_path, falso, enabled=True, mode="shadow", semantic={"provider": "fake"})
    pack = servico.gather("emitir fatura de cobrança")
    assert pack is not None and pack.origin == "local" and pack.metadata["shadow"] is True
    ev = _eventos(tmp_path)[-1]
    assert ev["mode"] == "shadow" and ev["cost_usd"] > 0 and falso.calls


def test_falha_do_provedor_cai_no_local_com_a_razao(tmp_path: Path) -> None:
    falso = FakeSemanticProvider(fail_with=ProviderTimeout("lento"))
    servico = _servico(tmp_path, falso, enabled=True, mode="hybrid", semantic={"provider": "fake"})
    pack = servico.gather("emitir fatura de cobrança")
    assert pack is not None and pack.metadata["fallback_used"] and pack.metadata["fallback_reason"] == "timeout"
    assert pack.files and pack.files[0].path == "app/billing.py"          # o resultado do local, intacto
    assert _eventos(tmp_path)[-1]["fallback_reason"] == "timeout"


def test_repositorio_privado_a_provedor_remoto_nao_envia_nada(tmp_path: Path) -> None:
    remoto = FakeSemanticProvider(locality=ProviderLocality.REMOTE)
    servico = _servico(tmp_path, remoto, enabled=True, mode="hybrid", semantic={"provider": "fake"})  # classe: private
    pack = servico.gather("como funciona a autenticação")
    assert pack is not None and pack.metadata["fallback_reason"] == "privacy_block"
    assert remoto.calls == []                                             # NADA saiu
    assert _eventos(tmp_path)[-1]["privacy_block_reason"] == "privacy_block"
    assert pack.files                                                      # e o local entregou mesmo assim


def test_repositorio_sintetico_a_provedor_remoto_segue_mas_o_segredo_duro_barra_a_etapa_b(tmp_path: Path) -> None:
    remoto = FakeSemanticProvider(locality=ProviderLocality.REMOTE, concepts={"login": ["login"]})
    servico = _servico(tmp_path, remoto, enabled=True, mode="hybrid",
                       semantic={"provider": "fake", "repository_class": "synthetic"})
    pack = servico.gather("login de usuário")
    assert pack is not None
    assert all(c["stage"] == "A" or "sk-" + "z" * 24 not in c["text"] for c in remoto.calls)
    enviados = {p for c in remoto.calls for p in c["paths"]}
    assert not enviados & {".env", "data/clientes.json", "config/config.yaml"}


def test_publico_sem_allow_public_e_negado(tmp_path: Path) -> None:
    remoto = FakeSemanticProvider(locality=ProviderLocality.REMOTE)
    servico = _servico(tmp_path, remoto, enabled=True, mode="hybrid",
                       semantic={"provider": "fake", "repository_class": "public"})
    pack = servico.gather("login de usuário")
    assert pack is not None and pack.metadata["fallback_reason"] == "privacy_block" and remoto.calls == []


def test_jev_sem_chave_e_indisponivel_e_cai_no_local(tmp_path: Path) -> None:
    servico = _servico(tmp_path, None, enabled=True, mode="hybrid",
                       semantic={"provider": "jev", "repository_class": "synthetic"})
    pack = servico.gather("login de usuário")
    assert pack is not None and pack.metadata["fallback_reason"] == "key_missing"   # e o fixture prova: zero rede


def test_jev_em_repositorio_privado_nega_antes_de_qualquer_envio(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "valor-de-teste-nao-e-uma-chave")
    cfg = _cfg(tmp_path, enabled=True, mode="hybrid", semantic={"provider": "jev"})
    pack = wiring.build_service(cfg, root=_repo(tmp_path)).gather("login de usuário")   # type: ignore[union-attr]
    assert pack is not None and pack.metadata["fallback_reason"] == "privacy_block"


def test_a_chave_do_jev_nao_fica_em_atributo_do_adaptador(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    segredo = "valor-de-teste-nao-e-uma-chave"
    monkeypatch.setenv("TYPESAFE_API_KEY", segredo)
    prov = wiring.criar_provedor(_cfg(tmp_path, semantic={"provider": "jev"}))
    assert prov is not None and prov.available() == (True, None)
    assert segredo not in repr(vars(prov)) and segredo not in repr(prov)


def test_hibrido_sem_provedor_configurado_cai_no_local(tmp_path: Path) -> None:
    pack = _servico(tmp_path, None, enabled=True, mode="hybrid").gather("emitir fatura")
    assert pack is not None and pack.metadata["fallback_reason"] == "no_provider" and pack.files


# ---------------------------------------------------------------- API de status e CLI
def _app(cfg: Config) -> TestClient:
    app = FastAPI()
    app.state.poc = SimpleNamespace(cfg=cfg)
    app.include_router(router)
    return TestClient(app)


def test_status_desligado_e_ligado_sem_vazar_nada(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = _app(_cfg(tmp_path)).get("/api/context-retrieval/status")
    assert r.status_code == 200
    j = r.json()
    assert j["enabled"] is False and j["mode"] == "disabled" and j["provider"]["available"] is False
    assert j["summary"]["requests"] == 0

    monkeypatch.setenv("TYPESAFE_API_KEY", "valor-de-teste-nao-e-uma-chave")
    cfg = _cfg(tmp_path, enabled=True, mode="shadow", semantic={"provider": "jev"})
    j2 = _app(cfg).get("/api/context-retrieval/status").json()
    assert j2["provider"] == {"name": "jev", "model": j2["provider"]["model"], "available": True,
                              "unavailable_reason": None}
    assert j2["external_send"]["allowed"] is False and j2["external_send"]["reason"] == "private_repository"
    assert "valor-de-teste-nao-e-uma-chave" not in json.dumps(j2)


def test_status_apos_pedidos_resume_as_metricas(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, enabled=True, mode="local_only")
    servico = wiring.build_service(cfg, root=_repo(tmp_path))
    servico.gather("emitir fatura")
    servico.gather("`verify_token`")
    resumo = _app(cfg).get("/api/context-retrieval/status").json()["summary"]
    assert resumo["requests"] == 2 and resumo["by_mode"] == {"local_only": 2}


def test_cli_desligado_nao_le_nada_e_sai_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "load_config", lambda: _cfg(tmp_path))
    assert cli.main(["`verify_token`", "--json", "--root", str(_repo(tmp_path))]) == 0
    assert json.loads(capsys.readouterr().out) == {"enabled": False, "mode": "disabled"}
    assert not (tmp_path / "dados").exists()


def test_cli_com_modo_explicito_devolve_o_pacote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "load_config", lambda: _cfg(tmp_path))
    assert cli.main(["onde `verify_token` é definido", "--json", "--mode", "local_only",
                     "--root", str(_repo(tmp_path))]) == 0
    saida = json.loads(capsys.readouterr().out)
    assert saida["files"][0]["path"] == "app/auth.py" and saida["mode"] == "local_only"
    assert all("text" not in r for r in saida["regions"])


def test_cli_escreve_utf8_mesmo_com_o_console_em_cp1252(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Achado ao rodar `plano-100-pacotes.py --contexto` de verdade: a seta da pergunta derrubava a CLI no pipe cp1252."""
    import io
    import sys
    bruto = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(bruto, encoding="cp1252", write_through=True))
    monkeypatch.setattr(cli, "load_config", lambda: _cfg(tmp_path))
    assert cli.main(["valida o token → cobrança `verify_token`", "--json", "--mode", "local_only",
                     "--root", str(_repo(tmp_path))]) == 0
    saida = json.loads(bruto.getvalue().decode("utf-8"))
    assert "→" in saida["query"] and saida["files"]


def test_cli_so_aceita_none_e_fake_como_provedor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "load_config", lambda: _cfg(tmp_path))
    with pytest.raises(SystemExit):
        cli.main(["x", "--provider", "jev"])


def test_modo_efetivo_do_servico_ligado(tmp_path: Path) -> None:
    servico = _servico(tmp_path, enabled=True, mode="local_only")
    assert servico.mode is RetrievalMode.LOCAL_ONLY and servico.enabled
