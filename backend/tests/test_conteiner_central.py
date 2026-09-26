"""O central em contêiner (deploy/): o endereço de escuta e a configuração do contêiner, sem Docker nenhum.

Prova SIMULADA, não real: nada aqui sobe imagem nem compose (o engine está parado e ligá-lo exige autorização). O
que se prova é a parte do contêiner que é código deste repositório:

- `main.endereco_de_escuta`: sem a variável da imagem, o `bind` é `server.host`, como sempre foi; com ela, o
  socket principal escuta onde o contêiner precisa, e sem `API_TOKEN` a subida é recusada ANTES de abrir o banco;
- `deploy/config.conteiner.yaml` sobe o `AppState` de verdade (zero aparelhos, provedor simulado) e responde como a
  Farm — inclusive ao `deploy/saude.py`, o healthcheck da imagem;
- o que o navegador do HOST vive: pela porta publicada o par é o gateway do Docker, então o painel exige login com
  token, e a origem aceita é a da porta PUBLICADA (8100), não a de dentro (8000).
"""
from __future__ import annotations

import functools
import importlib.util
import os
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest
import pytest_asyncio
import yaml
from pydantic import SecretStr

import app.main as principal
from app.config import AppConfigFile, Config, EnvSettings
from app.devices.emulator_backend import FakeEmulatorBackend
from app.main import VAR_ESCUTA_DO_CONTEINER, create_app, endereco_de_escuta
from app.planning.simulated_provider import SimulatedProvider
from app.state import AppState

from .conftest import _dsn_de_teste, make_config
from .fake_device import FakeQaDevice

RAIZ = Path(__file__).resolve().parents[2]
CONFIG_DO_CONTEINER = RAIZ / "deploy" / "config.conteiner.yaml"
SEGREDO = "tk-conteiner-7c1e4b9a20d35f8"          # token de teste, não existe fora daqui
#: Quem chega pela porta publicada: o gateway da rede do compose, nunca 127.0.0.1.
GATEWAY_DO_DOCKER = ("172.18.0.1", 40112)
PAINEL_NO_HOST = "http://127.0.0.1:8100"


def _saude() -> Any:
    """`deploy/saude.py` carregado como módulo — é o arquivo que a imagem executa no healthcheck."""
    spec = importlib.util.spec_from_file_location("saude_do_conteiner", RAIZ / "deploy" / "saude.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# ---------------------------------------------------------------- o endereço de escuta
@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    """Só a configuração do harness (loopback, sem token) — nada sobe: estes testes não precisam de `AppState`."""
    return make_config(tmp_path, 1)


def test_sem_a_variavel_o_bind_e_server_host_como_sempre(cfg: Config) -> None:
    assert endereco_de_escuta(cfg, ambiente={}) == "127.0.0.1"
    assert endereco_de_escuta(cfg, ambiente={VAR_ESCUTA_DO_CONTEINER: "   "}) == "127.0.0.1"
    cfg.file.server.host = "0.0.0.0"                 # noqa: S104 - o valor do config passa intacto
    assert endereco_de_escuta(cfg, ambiente={}) == "0.0.0.0"


def test_no_conteiner_escuta_onde_a_imagem_manda_com_token(cfg: Config) -> None:
    cfg.env.api_token = SecretStr(SEGREDO)
    assert endereco_de_escuta(cfg, ambiente={VAR_ESCUTA_DO_CONTEINER: "0.0.0.0"},
                              sistema="posix") == "0.0.0.0"


def test_no_conteiner_sem_token_a_subida_e_recusada(cfg: Config) -> None:
    """Pela porta publicada ninguém é loopback: sem token, o painel seria uma tela de login impossível."""
    cfg.env.api_token = None
    with pytest.raises(SystemExit) as e:
        endereco_de_escuta(cfg, ambiente={VAR_ESCUTA_DO_CONTEINER: "0.0.0.0"}, sistema="posix")
    assert "API_TOKEN" in str(e.value)


def test_no_windows_a_variavel_e_recusada(cfg: Config) -> None:
    """O central Windows sai do loopback só pelo caminho com TLS — a variável não é um atalho em volta dele."""
    cfg.env.api_token = SecretStr(SEGREDO)
    with pytest.raises(SystemExit) as e:
        endereco_de_escuta(cfg, ambiente={VAR_ESCUTA_DO_CONTEINER: "0.0.0.0"}, sistema="nt")
    assert "contêiner" in str(e.value)


def test_o_env_da_raiz_nao_liga_a_escuta_do_conteiner() -> None:
    """O `EnvSettings` lê o `.env`; se ele conhecesse a variável, uma linha esquecida num `.env` de Windows abriria a
    porta por fora de `conferir_exposicao`. Ela é lida só de `os.environ`, e só a imagem a define."""
    nomes = {str(n) for campo in EnvSettings.model_fields.values()
             for n in ((campo.alias,) if campo.alias else ())
             + tuple(getattr(campo.validation_alias, "choices", ()) or ())}
    assert VAR_ESCUTA_DO_CONTEINER not in nomes


class _Parou(Exception):
    """`main()` termina com `os._exit(0)`; no teste ele vira esta exceção."""


def _main_sem_efeito(monkeypatch: pytest.MonkeyPatch, cfg: Config, feito: dict[str, Any], *, sistema: str) -> None:
    """Roda `main.main()` com a configuração dada, sem abrir porta, banco nem uvicorn; o que ele fez vai em `feito`
    (preenchido mesmo quando `main` sai por `SystemExit` no meio)."""
    feito.update({"sockets": [], "estado": 0})

    class EstadoFalso:
        def __init__(self, _cfg: Config) -> None:
            feito["estado"] += 1

    class ServidorFalso:
        def __init__(self, _config: Any) -> None:
            self.should_exit = False

        def run(self, sockets: list[Any]) -> None:
            feito["run"] = True

    monkeypatch.setattr(principal, "get_config", lambda: cfg)
    monkeypatch.setattr(principal, "setup_logging", lambda _cfg: None)
    monkeypatch.setattr(principal, "AppState", EstadoFalso)
    monkeypatch.setattr(principal, "_socket_de", lambda host, porta: feito["sockets"].append((host, porta)))
    # `uvicorn.Config` de verdade reconfigura o logging do processo (dictConfig) — o da suíte inclusive.
    monkeypatch.setattr(principal.uvicorn, "Config", lambda alvo, **opcoes: (alvo, opcoes))
    monkeypatch.setattr(principal.uvicorn, "Server", ServidorFalso)
    monkeypatch.setattr(principal.logging, "shutdown", lambda *_a, **_k: None)
    monkeypatch.setattr(principal.os, "_exit", lambda _codigo: (_ for _ in ()).throw(_Parou()))
    monkeypatch.setattr(principal, "endereco_de_escuta",
                        functools.partial(endereco_de_escuta, sistema=sistema))
    try:
        principal.main()
    except _Parou:
        pass


def test_main_liga_o_socket_principal_no_endereco_do_conteiner_e_o_do_worker_segue_no_loopback(
        cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VAR_ESCUTA_DO_CONTEINER, "0.0.0.0")
    cfg.env.api_token = SecretStr(SEGREDO)
    cfg.file.server.worker_port = 8010
    feito: dict[str, Any] = {}
    _main_sem_efeito(monkeypatch, cfg, feito, sistema="posix")
    assert feito["sockets"] == [("0.0.0.0", cfg.file.server.port), ("127.0.0.1", 8010)]
    assert feito["estado"] == 1 and feito.get("run")


def test_main_sem_a_variavel_liga_em_server_host(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(VAR_ESCUTA_DO_CONTEINER, raising=False)
    cfg.file.server.worker_port = 0
    feito: dict[str, Any] = {}
    _main_sem_efeito(monkeypatch, cfg, feito, sistema="posix")
    assert feito["sockets"] == [("127.0.0.1", cfg.file.server.port)]


def test_main_recusa_antes_de_abrir_o_banco(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    """A recusa acontece antes do `AppState` — que é quem migra o banco. Recusar depois seria migrar à toa."""
    monkeypatch.setenv(VAR_ESCUTA_DO_CONTEINER, "0.0.0.0")
    cfg.env.api_token = None
    feito: dict[str, Any] = {}
    with pytest.raises(SystemExit) as e:
        _main_sem_efeito(monkeypatch, cfg, feito, sistema="posix")
    assert "API_TOKEN" in str(e.value)
    assert feito["estado"] == 0 and feito["sockets"] == []


# ---------------------------------------------------------------- a configuração do contêiner, de pé
def _config_do_conteiner(tmp: Path, *, token: str | None) -> Config:
    """O YAML do contêiner como está, com a raiz num diretório temporário: os caminhos são RELATIVOS à raiz
    (data/, apks/), então nada toca o data/ real. Só duas coisas mudam, e nenhuma é da configuração em teste: a
    porta do Appium (fechada, para a saúde não sondar o Appium real desta máquina) e a base das portas de console
    (a do harness, longe do parque — com zero aparelhos ela nem é usada)."""
    arquivo = AppConfigFile.model_validate(yaml.safe_load(CONFIG_DO_CONTEINER.read_text(encoding="utf-8")))
    arquivo.appium.port = 9
    arquivo.instances.base_console_port = 5640
    dsn = _dsn_de_teste()
    e_pg = bool(dsn) and str(dsn).startswith(("postgres://", "postgresql://"))
    campos: dict[str, Any] = {"AI_PROVIDER": "simulated", "OWNER_ID": "farm-central-validacao", "ROLE": "all",
                              "DATABASE_URL": dsn if e_pg else None}
    if token:
        campos["API_TOKEN"] = token
    if os.name != "nt":
        # O contêiner é Linux: sem DPAPI, a chave vem do ambiente (deploy/.env). Chave FIXA e falsa, só de teste.
        campos["CREDENTIALS_MASTER_KEY"] = "dGVzdGUtZG8tY29udGVpbmVyLW5hby1lLXNlZ3JlZG8="
    return Config(arquivo, EnvSettings(_env_file=None, **campos), root=tmp)  # type: ignore[call-arg]


@pytest_asyncio.fixture
async def conteiner(tmp_path: Path) -> AsyncIterator[AppState]:
    cfg = _config_do_conteiner(tmp_path, token=SEGREDO)
    estado = AppState(cfg, provider=SimulatedProvider(), io_factory=lambda _rt: FakeQaDevice(account="qa"),
                      manage_appium=False, emulator=FakeEmulatorBackend())
    await estado.start()
    try:
        yield estado
    finally:
        await estado.stop()


def _cliente(estado: AppState, *, base: str, par: tuple[str, int]) -> httpx.AsyncClient:
    app = create_app(estado.cfg, state=estado)
    app.state.poc = estado
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=par), base_url=base)


async def test_o_config_do_conteiner_sobe_sem_aparelho_e_o_healthcheck_reconhece_a_farm(conteiner: AppState) -> None:
    """De DENTRO do contêiner (par 127.0.0.1, porta 8000), como o healthcheck pergunta: sem token, é a Farm, e o
    banco está na última migração que a imagem traz."""
    assert conteiner.cfg.instance_ids() == []
    saude = _saude()
    async with _cliente(conteiner, base="http://127.0.0.1:8000", par=("127.0.0.1", 51000)) as c:
        r = await c.get("/api/health")
    assert r.status_code == 200
    assert saude.vivo(r.content)
    ok, motivo = saude.pronto(r.content, saude.ultima_migracao())
    assert ok, motivo
    codigos = {p["code"] for p in r.json()["problems"]}
    # Por que o healthcheck não olha `status`: sem SDK na imagem, `sdk_missing` (problema duro) vem sempre.
    assert "sdk_missing" in codigos
    assert "ai_simulated" in codigos


async def test_o_navegador_do_host_precisa_de_login_com_token(conteiner: AppState) -> None:
    """Pela porta publicada, o par é o gateway do Docker: sem credencial, 401 — e o login com o token abre."""
    async with _cliente(conteiner, base=PAINEL_NO_HOST, par=GATEWAY_DO_DOCKER) as c:
        assert (await c.get("/api/health")).status_code == 401
        sessao = await c.get("/api/session")
        assert sessao.status_code == 200 and sessao.json()["token_required"] is True
        login = await c.post("/api/login", json={"operator": "validacao", "token": SEGREDO},
                             headers={"Origin": PAINEL_NO_HOST})
        assert login.status_code == 200, login.text
        assert (await c.get("/api/health")).status_code == 200       # o cookie de sessão vale


async def test_a_origem_aceita_e_a_da_porta_publicada(conteiner: AppState) -> None:
    """`allowed_origins` fala da barra de endereços do navegador: 8100 (publicada), não 8000 (de dentro)."""
    async with _cliente(conteiner, base=PAINEL_NO_HOST, par=GATEWAY_DO_DOCKER) as c:
        r = await c.post("/api/login", json={"operator": "validacao", "token": SEGREDO},
                         headers={"Origin": "http://127.0.0.1:8000"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "forbidden_origin"
