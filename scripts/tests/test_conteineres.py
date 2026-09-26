"""Invariantes do empacotamento em contêiner do central (deploy/), conferidas por LEITURA — nada aqui chama Docker.

O engine não roda nesta máquina sem autorização (ligar o Docker Desktop liga o WSL), e o que dá para garantir sem
ele é justamente o que um descuido de edição quebraria calado: um `privileged`, uma porta em 0.0.0.0, um dado fora
de volume, uma segunda réplica, um segredo escrito no arquivo. Cada teste diz qual promessa segura.

    backend/.venv/Scripts/python.exe -m pytest -q scripts/tests/test_conteineres.py     # a partir da raiz
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

import pytest

yaml = pytest.importorskip("yaml")

RAIZ = Path(__file__).resolve().parents[2]
DEPLOY = RAIZ / "deploy"
COMPOSE = DEPLOY / "compose.yaml"
DOCKERFILE = DEPLOY / "central.Dockerfile"
CONFIG = DEPLOY / "config.conteiner.yaml"
ENV_EXEMPLO = DEPLOY / "conteiner.env.example"
DOCKERIGNORE = RAIZ / ".dockerignore"
CI = RAIZ / ".github" / "workflows" / "ci.yml"

#: Nome de variável que carrega segredo: nunca com valor literal no compose, no Dockerfile nem nos exemplos.
NOME_SENSIVEL = re.compile(r"(TOKEN|KEY|PASSWORD|SECRET|DATABASE_URL)", re.I)
#: A única forma aceita para um segredo no compose: interpolação sem padrão (`${X}` ou `${X:-}`).
INTERPOLACAO_SEM_PADRAO = re.compile(r"^\$\{[A-Z0-9_]+(:-)?\}$")


def _compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _servicos() -> dict[str, dict[str, Any]]:
    return _compose()["services"]


def _sem_comentarios(caminho: Path) -> list[str]:
    return [ln for ln in caminho.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")]


def _dockerfile() -> str:
    return "\n".join(_sem_comentarios(DOCKERFILE))


def _ci_env() -> dict[str, str]:
    return {k: str(v) for k, v in (yaml.safe_load(CI.read_text(encoding="utf-8")).get("env") or {}).items()}


def _modulo(nome: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"deploy_{nome}", DEPLOY / f"{nome}.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _porta_publicada() -> int:
    (porta,) = _servicos()["central"]["ports"]
    return int(str(porta).split(":")[1])


# ---------------------------------------------------------------- sem privilégio de host
def test_nenhum_servico_tem_privilegio_de_host() -> None:
    """Nada de `privileged`, rede/PID/IPC do host, capacidade extra ou dispositivo — em serviço nenhum. O Ollama
    com GPU usa reserva de dispositivo (`deploy.resources`), que fica comentada e não é privilégio."""
    for nome, servico in _servicos().items():
        assert servico.get("privileged") is not True, nome
        for chave in ("network_mode", "pid", "ipc", "userns_mode", "uts"):
            assert servico.get(chave) != "host", f"{nome}.{chave}"
        assert not servico.get("cap_add"), f"{nome}.cap_add"
        assert not servico.get("devices"), f"{nome}.devices"
        assert "no-new-privileges:true" in (servico.get("security_opt") or []), nome


def test_sem_socket_do_docker_e_sem_kvm_em_lugar_nenhum() -> None:
    """O socket do Docker é root no host; `/dev/kvm` é o que o EMULADOR precisaria (outra frente) — o central não."""
    for caminho in (COMPOSE, DOCKERFILE):
        texto = "\n".join(_sem_comentarios(caminho))
        assert "docker.sock" not in texto, caminho.name
        assert "/dev/kvm" not in texto, caminho.name
    for nome, servico in _servicos().items():
        for volume in servico.get("volumes") or []:
            origem = volume.get("source", "") if isinstance(volume, dict) else str(volume).split(":")[0]
            assert "docker.sock" not in origem, nome


def test_o_central_roda_sem_root_sem_capacidades_e_com_disco_so_de_leitura() -> None:
    central = _servicos()["central"]
    assert central.get("cap_drop") == ["ALL"]
    assert central.get("read_only") is True and "/tmp" in (central.get("tmpfs") or [])
    usuarios = [ln.split()[1] for ln in _dockerfile().splitlines() if ln.strip().upper().startswith("USER ")]
    assert usuarios and usuarios[-1] not in ("root", "0"), usuarios


# ---------------------------------------------------------------- portas
def test_portas_so_no_loopback_do_host_e_so_a_do_painel() -> None:
    """Só o central publica, só em 127.0.0.1, só a porta HTTP. 8010 (canal do worker), ADB e Appium nunca."""
    for nome, servico in _servicos().items():
        if nome != "central":
            assert not servico.get("ports"), f"{nome} não publica porta nenhuma"
    portas = _servicos()["central"]["ports"]
    assert len(portas) == 1
    for porta in portas:
        assert re.fullmatch(r"127\.0\.0\.1:\d+:8000", str(porta)), porta
    # 8000 no host é da Farm de produção (127.0.0.1) e do cartorio-api-1 (0.0.0.0) nesta máquina.
    assert _porta_publicada() != 8000


def test_a_origem_do_painel_e_a_porta_publicada() -> None:
    """O backend confere `Origin` em todo método que altera estado: a origem é a da barra de endereços do navegador,
    ou seja, a porta PUBLICADA. Divergir aqui deixa o login recusado com 403 sem motivo aparente."""
    origens = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["server"]["allowed_origins"]
    assert f"http://127.0.0.1:{_porta_publicada()}" in origens


# ---------------------------------------------------------------- dados, réplica, identidade
def test_dados_em_volume_nomeado_e_config_so_de_leitura() -> None:
    compose = _compose()
    declarados = set(compose.get("volumes") or {})
    esperado = {"central": {"/app/data", "/app/apks"}, "postgres": {"/var/lib/postgresql/data"},
                "ollama": {"/root/.ollama"}}
    for nome, alvos in esperado.items():
        nomeados = {str(v).split(":")[1] for v in compose["services"][nome].get("volumes") or []
                    if isinstance(v, str) and str(v).split(":")[0] in declarados}
        assert alvos <= nomeados, f"{nome}: {alvos - nomeados} fora de volume nomeado"
    binds = [v for s in compose["services"].values() for v in s.get("volumes") or [] if isinstance(v, dict)]
    assert [(b["type"], b["target"], b.get("read_only")) for b in binds] == [
        ("bind", "/app/config/config.yaml", True)]
    assert (DEPLOY / binds[0]["source"]).resolve() == CONFIG.resolve()


def test_uma_replica_so_e_o_docker_garante() -> None:
    """Estado de worker, controle manual, eventos e frames vivem na memória de UM processo; e dois processos com o
    mesmo OWNER_ID reconheceriam as etapas um do outro. `container_name` fixo faz o Docker recusar a segunda cópia."""
    central = _servicos()["central"]
    assert central["deploy"]["replicas"] == 1
    assert central.get("container_name")
    assert central["deploy"].get("update_config", {}).get("order") == "stop-first"
    assert central["environment"]["ROLE"] == "all"


def test_owner_id_fixo_e_literal() -> None:
    """O padrão é o hostname, que muda por contêiner: a identidade de dono das etapas não pode depender dele."""
    dono = _servicos()["central"]["environment"]["OWNER_ID"]
    assert isinstance(dono, str) and dono.strip() and "$" not in dono


def test_ambiente_explicito_do_central() -> None:
    ambiente = _servicos()["central"]["environment"]
    assert ambiente["AI_PROVIDER"] == "simulated"            # validação sem chamada paga
    assert ambiente["POC_CONFIG"] == "/app/config/config.yaml"
    assert ambiente["CONTAINER_LISTEN_HOST"] == "0.0.0.0"
    assert _servicos()["central"]["env_file"] == [".env"]
    # deploy/.env fica fora do Git pela regra `.env` (sem barra) do .gitignore da raiz.
    assert ".env" in (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_reinicio_limitado_e_parada_graciosa() -> None:
    """Nada de ciclo infinito de reinício: `always`/`unless-stopped` fariam uma recusa de partida girar para
    sempre. A parada espera mais que o encerramento gracioso do uvicorn (10 s)."""
    for nome, servico in _servicos().items():
        politica = str(servico.get("restart", "no"))
        casou = re.fullmatch(r"no|on-failure:(\d+)", politica)
        assert casou and (casou.group(1) is None or int(casou.group(1)) <= 5), f"{nome}: {politica}"
    central = _servicos()["central"]
    assert central.get("init") is True
    assert int(str(central["stop_grace_period"]).rstrip("s")) >= 15


def test_healthcheck_por_identidade() -> None:
    central = _servicos()["central"]
    assert "/app/deploy/saude.py" in central["healthcheck"]["test"]
    assert "--pronto" not in central["healthcheck"]["test"]    # vivo ≠ pronto: o healthcheck é o vivo
    assert re.search(r"^HEALTHCHECK .*\n?.*deploy/saude\.py", _dockerfile(), re.M)
    for nome in ("postgres", "ollama"):
        assert _servicos()[nome].get("healthcheck"), nome


# ---------------------------------------------------------------- segredo
def test_nenhum_segredo_literal() -> None:
    for nome, servico in _servicos().items():
        for chave, valor in (servico.get("environment") or {}).items():
            if NOME_SENSIVEL.search(chave):
                assert INTERPOLACAO_SEM_PADRAO.match(str(valor)), f"{nome}.{chave} tem valor escrito"
    for linha in _dockerfile().splitlines():
        if linha.strip().upper().startswith(("ENV ", "ARG ")):
            assert not NOME_SENSIVEL.search(linha), linha
    # O exemplo de env: toda linha ativa sem valor.
    for linha in _sem_comentarios(ENV_EXEMPLO):
        if "=" in linha:
            assert linha.split("=", 1)[1].strip() == "", linha
    padroes = (r"sk-ant-", r"-----BEGIN [A-Z ]*PRIVATE KEY", r"postgres(?:ql)?://[^:\s/]+:[^@<\s]+@")
    for caminho in (COMPOSE, DOCKERFILE, CONFIG, ENV_EXEMPLO, DOCKERIGNORE):
        texto = caminho.read_text(encoding="utf-8")
        for padrao in padroes:
            assert not re.search(padrao, texto), f"{caminho.name}: {padrao}"


def test_dockerignore_e_lista_de_permissao_com_as_exclusoes() -> None:
    linhas = [ln.strip() for ln in _sem_comentarios(DOCKERIGNORE) if ln.strip()]
    assert linhas[0] == "*"
    ultima_permissao = max(i for i, ln in enumerate(linhas) if ln.startswith("!"))
    for exclusao in (".env", "**/.env", "config/config.yaml", "data/", "apks/", "**/node_modules", "**/.venv",
                     ".claude/"):
        assert exclusao in linhas[ultima_permissao:], exclusao
    for permissao in (ln for ln in linhas if ln.startswith("!")):
        assert not re.search(r"(\.env|config/|^!data|^!apks|\.claude)", permissao), permissao


def test_o_que_o_dockerfile_copia_esta_na_lista_de_permissao() -> None:
    """Uma permissão que falta só aparece no primeiro build real ("not found"); aqui aparece antes."""
    permitidos = [ln.strip()[1:] for ln in _sem_comentarios(DOCKERIGNORE) if ln.strip().startswith("!")]
    copias = [ln.split()[1:-1] for ln in _dockerfile().splitlines()
              if ln.strip().upper().startswith("COPY ") and "--from=" not in ln]
    fontes = [f for grupo in copias for f in grupo]
    assert fontes
    for fonte in fontes:
        assert any(fonte == p or fonte.startswith(p) or p.startswith(fonte.rstrip("/") + "/") or fonte == p.rstrip("/")
                   for p in permitidos), fonte
        assert not fonte.startswith(("config", ".env", "data", "apks")), fonte


# ---------------------------------------------------------------- versões
def test_versoes_da_imagem_sao_as_do_ci() -> None:
    ci = _ci_env()
    args = dict(re.findall(r"^ARG (\w+)=(\S+)$", DOCKERFILE.read_text(encoding="utf-8"), re.M))
    assert args["NODE_VERSION"] == ci["NODE_VERSION"]
    assert args["PYTHON_VERSION"].startswith(ci["PYTHON_VERSION"] + ".")
    assert len(re.findall(r"^FROM ", _dockerfile(), re.M)) >= 2          # multi-stage: node só no build


def test_imagens_com_versao_fixa() -> None:
    for nome, servico in _servicos().items():
        imagem = str(servico.get("image", ""))
        if "build" in servico:
            continue
        repositorio, _, tag = imagem.rpartition(":")
        assert repositorio and re.match(r"\d+\.\d+", tag), f"{nome}: {imagem}"
    # Mesma major do PostgreSQL do CI, e Debian (a colação do CI é a do Debian — aprendizados, 26/09).
    ci_pg = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["backend-postgres"]["services"]["postgres"]["image"]
    pg = _servicos()["postgres"]["image"]
    assert pg.split(":")[1].split(".")[0] == ci_pg.split(":")[1].split(".")[0]
    assert "alpine" not in pg


# ---------------------------------------------------------------- a configuração do contêiner
def test_config_do_conteiner_valida_no_modelo_do_backend() -> None:
    pytest.importorskip("pydantic")
    sys.path.insert(0, str(RAIZ / "backend"))
    from app.config import AppConfigFile  # noqa: PLC0415 - só com o backend no caminho

    cfg = AppConfigFile.model_validate(yaml.safe_load(CONFIG.read_text(encoding="utf-8")))
    assert cfg.server.host == "127.0.0.1"                  # a escuta no contêiner vem de CONTAINER_LISTEN_HOST
    assert cfg.server.worker_port == 0                     # listener do túnel seria inalcançável no contêiner
    assert cfg.instances.count == 0 and not cfg.instances.external and cfg.instances.store is None
    assert cfg.appium.autostart is False
    assert all(not a.apk_path for a in cfg.apps)
    # Relativos à raiz (/app): o catálogo de APK é gravado com `relative_to(cfg.root)`.
    caminhos = [cfg.paths.data_dir, cfg.paths.evidence_dir, cfg.paths.logs_dir, cfg.paths.apk_inbox,
                cfg.paths.apk_catalog, *cfg.paths.apk_dirs]
    assert all(not Path(c).is_absolute() and not c.startswith("/") for c in caminhos), caminhos
    # …e dentro dos volumes montados (/app/data, /app/apks).
    assert cfg.paths.data_dir == "data" and cfg.paths.apk_catalog == "apks"
    assert all(c.split("/")[0] in ("data", "apks") for c in caminhos), caminhos


# ---------------------------------------------------------------- deploy/saude.py e deploy/iniciar.py
FARM_OK = b'{"service":"android-farm-central","status":"ok","database":{"dialect":"sqlite","reachable":true},' \
          b'"migration":"041_loja_de_apps","problems":[]}'
FARM_ERRO = (b'{"service":"android-farm-central","status":"error","database":{"dialect":"sqlite","reachable":true},'
             b'"migration":"041_loja_de_apps","problems":[{"code":"sdk_missing"}]}')
FARM_SEM_BANCO = (b'{"service":"android-farm-central","status":"error","database":{"dialect":"postgres",'
                  b'"reachable":false},"migration":null,"problems":[{"code":"database_down"}]}')
CARTORIO = b'{"detail":"Not Found"}'


def test_vivo_e_identidade_e_nao_status() -> None:
    saude = _modulo("saude")
    assert saude.vivo(FARM_OK) and saude.vivo(FARM_ERRO) and saude.vivo(FARM_SEM_BANCO)
    assert not saude.vivo(CARTORIO) and not saude.vivo(None) and not saude.vivo(b"<html>")


def test_pronto_pede_banco_e_a_migracao_da_imagem() -> None:
    saude = _modulo("saude")
    assert saude.pronto(FARM_OK, "041_loja_de_apps")[0]
    assert saude.pronto(FARM_ERRO, "041_loja_de_apps")[0]        # sdk_missing não impede: é esperado aqui
    assert not saude.pronto(FARM_SEM_BANCO, "041_loja_de_apps")[0]
    assert not saude.pronto(FARM_OK, "042_mais_nova")[0]         # imagem à frente do banco (ou atrás)
    assert not saude.pronto(CARTORIO, "041_loja_de_apps")[0]
    atual = sorted(p.stem for p in (RAIZ / "backend" / "migrations").glob("*.sql"))[-1]
    assert saude.ultima_migracao() == atual


def test_ninguem_respondendo_nao_e_a_farm() -> None:
    saude = _modulo("saude")
    assert saude.ler("http://127.0.0.1:9/api/health", prazo_s=0.5) is None


def test_partida_recusa_sem_config_explicito(tmp_path: Path) -> None:
    iniciar = _modulo("iniciar")
    assert "não definido" in iniciar.conferir({})
    assert "absoluto" in iniciar.conferir({"POC_CONFIG": "config/config.yaml"})
    assert "não existe" in iniciar.conferir({"POC_CONFIG": str(tmp_path / "config.yaml")})
    (tmp_path / "config.yaml").write_text("server: {}\n", encoding="utf-8")
    assert iniciar.conferir({"POC_CONFIG": str(tmp_path / "config.yaml")}) is None
