"""Itens 10.1 e 10.4 — achados #137, #14 e #180: não havia instalador, serviço nem caminho para o Linux.

O que existia na máquina real (`C:\\farm\\run-agente.ps1`, `agente-tarefa.ps1`) não estava versionado, as tarefas
não tinham gatilho de boot nem reinício, a linha de comando carregava o token de inscrição já gasto, e
`C:\\farm\\agent` não era checkout — o central não tinha como saber que código rodava lá. Do lado Linux, nada:
nenhuma unidade systemd versionada, e o emulador nascia dentro do cgroup do serviço.

Aqui se trava o CONTRATO dos instaladores (pelo `-Simular` / `--dry-run`, sem registrar tarefa, criar conta nem
copiar arquivo), a versão derivada do commit, os padrões por sistema e o destacamento do emulador.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
SCRIPTS = RAIZ / "scripts"
UNIDADE = RAIZ / "config" / "farm-worker.service"

precisa_pwsh = pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh não está no PATH")
precisa_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash não está no PATH")


def _simular(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    r = subprocess.run(["pwsh", "-NoProfile", "-File", str(SCRIPTS / script), *args],
                       capture_output=True, text=True, timeout=180, cwd=str(RAIZ))
    assert r.returncode == 0, r.stdout + r.stderr
    return r


# ---------------------------------------------------------------- serviço do agente (Windows)
@precisa_pwsh
def test_a_tarefa_do_agente_nao_carrega_o_token_de_inscricao() -> None:
    """O token é de USO ÚNICO e já foi gasto na primeira partida. Mantê-lo na ação da tarefa — era o caso, no
    `run-agente.ps1` escrito à mão — é segredo gasto exposto em texto, que só atrapalha a recuperação."""
    r = _simular("worker-agent.ps1", "-Simular", "-Instalar")
    argumentos = next(l for l in r.stdout.splitlines() if l.startswith("argumentos: "))
    assert "--enroll" not in argumentos, argumentos
    assert "-m app.worker" in argumentos and "--config" in argumentos


@precisa_pwsh
def test_o_token_nao_aparece_na_saida_nem_quando_e_passado() -> None:
    segredo = "token-de-mentira-para-o-ensaio-nao-e-segredo-de-verdade"
    r = _simular("worker-agent.ps1", "-Simular", "-Inscrever", segredo)
    assert segredo not in (r.stdout + r.stderr)
    assert "inscricao: rodaria o agente em primeiro plano" in r.stdout


@precisa_pwsh
def test_a_tarefa_do_agente_sobe_no_boot_e_religa_em_falha() -> None:
    """Medido por SSH no worker: `farm-agente` e `farm-emulador-worker-01..06` tinham ZERO gatilhos e
    `RestartCount=0`. Reiniciar o notebook deixava os seis aparelhos e o agente fora até alguém entrar por SSH."""
    r = _simular("worker-agent.ps1", "-Simular", "-Instalar")
    assert "gatilho: AtStartup" in r.stdout
    assert "reinicio: RestartCount=999" in r.stdout
    assert "rotacao 5 x 5 MB" in r.stdout          # o `*>` do lançador antigo deixava o log crescer sem teto
    assert "simulacao: nada foi registrado nem iniciado" in r.stdout


@precisa_pwsh
def test_o_instalador_windows_grava_a_versao_derivada_do_commit() -> None:
    r = _simular("worker-install.ps1", "-Simular")
    versao = next(l for l in r.stdout.splitlines() if l.startswith("versao: ")).removeprefix("versao: ")
    assert versao.startswith("0.1.0+") and versao != "0.1.0+desconhecido", versao
    # Só o que o agente importa de verdade — não o backend inteiro.
    pastas = next(l for l in r.stdout.splitlines() if l.startswith("pastas: "))
    assert "worker" in pastas and "devices" in pastas
    assert "taskqueue" not in pastas and "social" not in pastas
    assert "simulacao: nada foi copiado" in r.stdout


# ---------------------------------------------------------------- Linux
@precisa_bash
def test_o_instalador_linux_tem_sintaxe_valida_e_um_ensaio_que_nao_toca_em_nada() -> None:
    # Caminho RELATIVO: o bash do Windows (Git Bash) não entende `C:\...` como argumento.
    caminho = "scripts/worker-install.sh"
    sintaxe = subprocess.run(["bash", "-n", caminho], capture_output=True, text=True, timeout=60, cwd=str(RAIZ))
    assert sintaxe.returncode == 0, sintaxe.stderr
    r = subprocess.run(["bash", caminho, "--dry-run"], capture_output=True, text=True, timeout=120,
                       cwd=str(RAIZ))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "simulacao: nada foi copiado, instalado nem registrado" in r.stdout
    assert "unidade: /etc/systemd/system/farm-worker.service" in r.stdout
    versao = next(l for l in r.stdout.splitlines() if l.startswith("versao: ")).removeprefix("versao: ")
    assert versao.startswith("0.1.0+")
    # Nada foi criado: o ensaio sai antes de qualquer escrita.
    assert not Path("/opt/farm/agent").exists() or os.name == "nt"


def test_a_unidade_systemd_nao_mata_os_emuladores_ao_reiniciar_o_agente() -> None:
    """`KillMode` padrão é `control-group`: `systemctl restart farm-worker` derrubaria TODOS os emuladores,
    porque eles nascem dentro do cgroup do serviço. É o acoplamento que o projeto mediu e evitou no Windows."""
    texto = UNIDADE.read_text(encoding="utf-8")
    codigo = [l.strip() for l in texto.splitlines() if l.strip() and not l.strip().startswith("#")]
    assert "KillMode=process" in codigo
    assert "Restart=always" in codigo
    assert "SupplementaryGroups=kvm" in codigo          # sem isto o agente não abre /dev/kvm
    assert "After=network-online.target" in codigo
    # Os marcadores existem para o instalador substituir; nenhum caminho de máquina fica chumbado aqui.
    for marcador in ("__AGENT_DIR__", "__CONFIG__", "__LOG__", "__USUARIO__"):
        assert marcador in texto, marcador


def test_o_instalador_linux_substitui_todos_os_marcadores_da_unidade() -> None:
    """Marcador que ficasse para trás viraria um `ExecStart` com `__AGENT_DIR__` literal — o serviço subiria no
    boot só para falhar."""
    script = (SCRIPTS / "worker-install.sh").read_text(encoding="utf-8")
    assert "$UNIDADE_MODELO" in script
    for marcador in ("__AGENT_DIR__", "__CONFIG__", "__LOG__", "__USUARIO__"):
        assert f"s|{marcador}|" in script, marcador


# ---------------------------------------------------------------- o agente fora do Windows
def test_o_emulador_nasce_destacado_da_sessao_fora_do_windows(monkeypatch: pytest.MonkeyPatch,
                                                              tmp_path: Path) -> None:
    """Achado #180: no Windows o emulador nasce em grupo de processos novo; no Linux herdava a sessão e o grupo
    do agente. Sob systemd, reiniciar o serviço levaria todos os emuladores junto."""
    from app.devices import emulator as mod

    capturado: dict[str, object] = {}

    class PopenFalso:
        pid = 4242

        def __init__(self, *args: object, **kw: object) -> None:
            capturado.update(kw)

    monkeypatch.setattr(mod.subprocess, "Popen", PopenFalso)
    monkeypatch.setattr(mod, "IS_WINDOWS", False)

    class ToolsFalso:
        emulator = tmp_path / "emulator"

        def env(self) -> dict[str, str]:
            return {}

    ToolsFalso.emulator.write_text("", encoding="utf-8")

    class CfgFalso:
        logs_dir = tmp_path / "logs"

    from app.config import AndroidCfg

    pid = mod.start_process(CfgFalso(), ToolsFalso(), "worker-01", 5554, AndroidCfg())  # type: ignore[arg-type]
    assert pid == 4242
    assert capturado["start_new_session"] is True, "o emulador continuaria no cgroup/sessão do agente"

    capturado.clear()
    monkeypatch.setattr(mod, "IS_WINDOWS", True)
    mod.start_process(CfgFalso(), ToolsFalso(), "worker-01", 5554, AndroidCfg())  # type: ignore[arg-type]
    assert capturado["start_new_session"] is False, "no Windows quem destaca é CREATE_NEW_PROCESS_GROUP"


def test_os_padroes_do_agente_seguem_o_sistema(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eram `C:\\Android\\Sdk` e `C:\\farm` fixos: num worker Linux o agente procurava o SDK num caminho que não
    existe naquele SO e só falhava adiante, com uma mensagem sobre o emulador em vez de sobre a configuração."""
    from app.worker import settings as mod

    monkeypatch.setattr(mod.os, "name", "nt")
    assert mod._padrao_sdk_root() == r"C:\Android\Sdk"
    assert mod._padrao_work_dir() == r"C:\farm"

    monkeypatch.setattr(mod.os, "name", "posix")
    lar = os.path.expanduser("~")
    assert mod._padrao_sdk_root() == os.path.join(lar, "Android", "Sdk")
    assert mod._padrao_work_dir() == os.path.join(lar, "farm")
    assert not mod._padrao_sdk_root().startswith(r"C:\Android"), "o padrão do Windows vazou para o POSIX"


def test_a_aceleracao_do_host_le_dev_kvm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.worker import settings as mod

    monkeypatch.setattr(mod.os, "name", "nt")
    assert mod.aceleracao_do_host() is None, "no Windows, afirmar WHPX sem medir seria dado falso"

    monkeypatch.setattr(mod.os, "name", "posix")
    falso = tmp_path / "kvm"
    monkeypatch.setattr(mod, "Path", lambda _p: falso)          # o `/dev/kvm` deste teste
    assert mod.aceleracao_do_host() == mod.KVM_AUSENTE
    falso.write_text("", encoding="utf-8")
    monkeypatch.setattr(mod.os, "access", lambda *_a, **_k: False)
    assert mod.aceleracao_do_host() == mod.KVM_INACESSIVEL
    monkeypatch.setattr(mod.os, "access", lambda *_a, **_k: True)
    assert mod.aceleracao_do_host() == mod.KVM


# ---------------------------------------------------------------- a versão que o agente declara
def test_a_versao_sai_do_arquivo_de_build_quando_a_maquina_nao_e_checkout(tmp_path: Path) -> None:
    """É o caso do worker: `C:\\farm\\agent` chega por cópia, `Test-Path .git` = False. Sem o arquivo gravado
    pelo instalador, os dois lados diriam `0.1.0` para sempre."""
    from app.version import DESCONHECIDO, agent_version

    pacote = tmp_path / "app"
    pacote.mkdir()
    assert agent_version(pacote) == DESCONHECIDO        # sem BUILD_VERSION e sem .git acima

    (pacote / "BUILD_VERSION").write_text("0.1.0+abc1234\n", encoding="utf-8")
    agent_version.cache_clear()
    assert agent_version(pacote) == "0.1.0+abc1234"


def test_a_versao_sai_do_git_quando_o_codigo_roda_do_checkout() -> None:
    from app.version import VERSION, agent_version

    versao = agent_version(RAIZ / "backend" / "app")
    assert versao.startswith(f"{VERSION}+") and versao != f"{VERSION}+desconhecido", versao
    assert len(versao.split("+")[1]) == 7


def test_o_agente_declara_a_versao_derivada_e_nao_uma_constante() -> None:
    from app.worker import AGENT_VERSION
    from app.version import agent_version

    assert AGENT_VERSION == agent_version()
    assert AGENT_VERSION != "0.1.0", "a constante era exatamente o defeito do achado #137"
    assert sys.version_info >= (3, 11)


def test_o_exemplo_versionado_do_worker_passa_pela_validacao_do_codigo(tmp_path: Path) -> None:
    """Achado #14: `config/worker.example.yaml` sugeria `avd_name: ""` para aparelho não gerido, e isso falhava
    no `min_length=1` do próprio código que o exemplo exemplifica. Aqui todas as linhas de `devices` do exemplo —
    inclusive as comentadas, que é o que alguém descomenta — são validadas de verdade."""
    import re

    import yaml

    from app.worker.settings import DeviceSpec, WorkerSettings

    texto = (RAIZ / "config" / "worker.example.yaml").read_text(encoding="utf-8")
    exemplos = [m.group(1) for m in re.finditer(r"^\s*#?\s*-\s*(\{.*\})\s*$", texto, re.MULTILINE)]
    assert len(exemplos) >= 7, exemplos
    for bruto in exemplos:
        DeviceSpec.model_validate(yaml.safe_load(bruto))

    # E o arquivo inteiro carrega (sem os comentários, que é como ele chega à máquina).
    dados = yaml.safe_load(texto)
    WorkerSettings.model_validate(dados)


def test_aparelho_gerido_sem_avd_e_recusado_na_leitura_do_yaml() -> None:
    import pydantic

    from app.worker.settings import DeviceSpec

    with pytest.raises(pydantic.ValidationError) as exc:
        DeviceSpec.model_validate({"instance_id": "android-16", "avd_name": "", "console_port": 5566})
    assert "avd_name" in str(exc.value)


@precisa_pwsh
def test_o_instalador_windows_acha_a_versao_mesmo_sem_o_venv_do_central(tmp_path: Path) -> None:
    """O caminho que existe JUSTAMENTE para a máquina que não tem o venv do central (copiar de um
    compartilhamento). Com `$ErrorActionPreference='Stop'`, chamar um executável ausente é erro terminante — o
    recurso ao `.git` da árvore de origem só é alcançável porque há um `Test-Path` antes."""
    origem = tmp_path / "origem"
    (origem / "backend").mkdir(parents=True)
    (origem / "config").mkdir()
    shutil.copytree(RAIZ / "backend" / "app", origem / "backend" / "app",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(RAIZ / "backend" / "worker-requirements.txt", origem / "backend")
    shutil.copy(RAIZ / "config" / "worker.example.yaml", origem / "config")
    (origem / ".git").mkdir()
    (origem / ".git" / "HEAD").write_text("ref: refs/heads/principal\n", encoding="utf-8")
    (origem / ".git" / "refs" / "heads").mkdir(parents=True)
    (origem / ".git" / "refs" / "heads" / "principal").write_text("abc1234def5678" + "0" * 26, encoding="utf-8")
    assert not (origem / "backend" / ".venv").exists()

    r = _simular("worker-install.ps1", "-Simular", "-Origem", str(origem))
    assert "versao: 0.1.0+abc1234" in r.stdout, r.stdout
