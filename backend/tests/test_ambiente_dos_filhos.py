"""29.47: nenhum segredo do backend chega aos processos filhos (adb, emulador, Appium, sing-box, Diagnóstico).

O qemu herdava o ambiente inteiro do backend, `TYPESAFE_API_KEY` inclusive (K-078, rodada por adição). Os testes põem
variáveis SENTINELA com nomes de segredo no ambiente deste processo (valores falsos) e olham só os NOMES que chegam:
nenhum valor é lido nem impresso, nem o das sentinelas.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.devices import diagnostics
from app.devices.rede_servidor import ProcessosReais
from app.devices.sdk import ambiente_dos_filhos, sem_segredos, SdkTools

#: Os nomes dos segredos do `.env` e das contas, mais dois genéricos que só a 2ª trava (nome com cara de segredo) pega.
SENTINELAS = ("TYPESAFE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "FARM_WORKER_TOKEN",
              "FARM_QUALQUER_COISA", "SERVICO_X_TOKEN", "ANDROID_X_SECRET", "ADB_PRIVATE_KEY")
#: O filho imprime os NOMES do próprio ambiente, nunca os valores.
NOMES_DO_FILHO = "import json, os; print(json.dumps(sorted(os.environ)))"


@pytest.fixture
def com_sentinelas(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in SENTINELAS:
        monkeypatch.setenv(nome, "sentinela-falsa")


def _sdk(tmp_path: Path) -> SdkTools:
    # SdkTools só lê `sdk_root` e `avd_home` da configuração
    return SdkTools(SimpleNamespace(sdk_root=tmp_path / "sdk", avd_home=tmp_path / "avd"))  # type: ignore[arg-type]


def _nomes(saida: str) -> set[str]:
    return {n.upper() for n in json.loads(saida.strip().splitlines()[-1])}


def _pelo_sdk(tmp_path: Path) -> set[str]:
    return _nomes(_sdk(tmp_path).run([sys.executable, "-c", NOMES_DO_FILHO], timeout=60, check=True).stdout)


def _pelo_sing_box(tmp_path: Path) -> set[str]:
    processos, saida = ProcessosReais(), tmp_path / "sing-box.log"
    pid = processos.lancar([sys.executable, "-c", NOMES_DO_FILHO], cwd=tmp_path, saida=saida)
    processos._filhos[pid].wait(timeout=60)
    return _nomes(saida.read_text(encoding="utf-8"))


def _pelo_diagnostico(tmp_path: Path) -> set[str]:
    return _nomes(diagnostics._run([sys.executable, "-c", NOMES_DO_FILHO], timeout=60))


@pytest.mark.parametrize("lancar", [_pelo_sdk, _pelo_sing_box, _pelo_diagnostico],
                         ids=["sdk (adb, emulador, Appium)", "sing-box", "diagnostico"])
def test_nenhum_segredo_chega_ao_filho(com_sentinelas: None, tmp_path: Path, lancar) -> None:
    assert all(n in os.environ for n in SENTINELAS)          # elas estão no pai...
    nomes = lancar(tmp_path)
    assert not nomes & set(SENTINELAS)                         # ...e não chegam ao filho
    assert not {n for n in nomes if n.startswith(("TYPESAFE_", "OPENAI_", "ANTHROPIC_", "GEMINI_", "FARM_"))}
    assert "PATH" in nomes                                     # o filho ainda acha os executáveis
    if os.name == "nt":
        assert "SYSTEMROOT" in nomes


def test_o_sdk_ainda_recebe_as_pastas_do_android(com_sentinelas: None, tmp_path: Path) -> None:
    nomes = _pelo_sdk(tmp_path)
    assert {"ANDROID_HOME", "ANDROID_SDK_ROOT", "ANDROID_AVD_HOME"} <= nomes


def test_a_decisao_e_pelo_nome_e_sem_caixa() -> None:
    origem = {"Path": "p", "SystemRoot": "s", "TEMP": "t", "JAVA_HOME": "j", "ANDROID_USER_HOME": "a",
              "ADB_VENDOR_KEYS": "k", "TypeSafe_Api_Key": "x", "openai_api_key": "x", "FARM_DB": "x",
              "MEU_APP_URL": "x", "ANDROID_EMU_TOKEN": "x", "GITHUB_TOKEN": "x", "NODE_OPTIONS": "x"}
    assert set(ambiente_dos_filhos(origem)) == {"Path", "SystemRoot", "TEMP", "JAVA_HOME", "ANDROID_USER_HOME",
                                                "ADB_VENDOR_KEYS"}


# ================================================== a varredura dos outros lançamentos (03/10, depois do 29.47)
def test_sem_segredos_tira_so_o_que_tem_nome_de_segredo() -> None:
    origem = {"Path": "p", "HOME": "h", "GIT_SSH_COMMAND": "g", "GIT_OPTIONAL_LOCKS": "0", "MEU_APP_URL": "u",
              "RIPGREP_CONFIG_PATH": "r", "TypeSafe_Api_Key": "x", "GITHUB_TOKEN": "x", "FARM_X": "x",
              "SERVICO_PASSWORD": "x", "AWS_SECRET_ACCESS_KEY": "x", "MY_PRIVATE_KEY": "x"}
    assert set(sem_segredos(origem)) == {"Path", "HOME", "GIT_SSH_COMMAND", "GIT_OPTIONAL_LOCKS", "MEU_APP_URL",
                                        "RIPGREP_CONFIG_PATH"}


class _Captura:
    """Troca `subprocess.run` e guarda o `env` de cada chamada; o teste só olha os NOMES."""

    def __init__(self, stdout: str | bytes = "") -> None:
        self.stdout, self.envs = stdout, []

    def __call__(self, args, **kw):
        self.envs.append(kw.get("env", "AUSENTE"))
        return subprocess.CompletedProcess(args, 0, stdout=self.stdout, stderr=type(self.stdout)())


def _confere(captura: _Captura) -> set[str]:
    assert captura.envs, "o lançamento não passou por subprocess.run"
    nomes: set[str] = set()
    for env in captura.envs:
        assert isinstance(env, dict), "filho com o ambiente inteiro do pai (sem env=)"
        nomes |= {n.upper() for n in env}
    assert not nomes & set(SENTINELAS)
    return nomes


def _icacls_da_rede(caminho: Path) -> None:
    from app.devices.rede_servidor import restringir_ao_usuario
    restringir_ao_usuario(caminho)


def _icacls_do_segredo_local(caminho: Path) -> None:
    from app.security.local_secret import restringir_acesso
    restringir_acesso(caminho)


def _icacls_do_agente(caminho: Path) -> None:
    from app.worker.settings import restringir_acesso
    restringir_acesso(caminho)


def _powershell_do_firewall(_caminho: Path) -> None:
    from app.devices.rede_firewall import executar_powershell
    executar_powershell("Write-Output 1")


@pytest.mark.skipif(os.name != "nt", reason="icacls e PowerShell só no Windows; fora dele é chmod")
@pytest.mark.parametrize("lancar", [_icacls_da_rede, _icacls_do_segredo_local, _icacls_do_agente,
                                    _powershell_do_firewall],
                         ids=["icacls da rede", "icacls do segredo local", "icacls do agente",
                              "powershell do firewall"])
def test_ferramentas_do_windows_recebem_a_lista_de_permissao(com_sentinelas: None, tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch, lancar) -> None:
    captura = _Captura()
    monkeypatch.setattr(subprocess, "run", captura)
    alvo = tmp_path / "arquivo"
    alvo.write_text("x", encoding="utf-8")
    lancar(alvo)
    assert {"PATH", "SYSTEMROOT"} <= _confere(captura)


@pytest.mark.skipif(os.name != "nt", reason="PowerShell do Windows")
def test_o_powershell_do_firewall_nao_herda_segredo(com_sentinelas: None) -> None:
    """Filho real: o PowerShell da leitura do firewall lista os NOMES do ambiente que recebeu."""
    from app.devices.rede_firewall import executar_powershell
    saida = executar_powershell(
        "[Environment]::GetEnvironmentVariables().Keys | Sort-Object | ConvertTo-Json -Compress", timeout=60)
    nomes = _nomes(saida)
    assert not nomes & set(SENTINELAS)
    assert {"PATH", "SYSTEMROOT", "PSMODULEPATH"} <= nomes


def test_o_git_e_o_ripgrep_do_context_retrieval_nao_recebem_segredo(com_sentinelas: None, tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.context_retrieval.adapters import github_visibility
    from app.modules.context_retrieval.infrastructure import lexical
    from app.modules.context_retrieval.infrastructure.workspace import Workspace

    git = _Captura(stdout=b"")
    monkeypatch.setattr(subprocess, "run", git)
    github_visibility.ler_remotos_do_git(tmp_path)
    github_visibility._git(tmp_path, "status")
    Workspace(tmp_path)._git_bytes("rev-parse", "HEAD")
    assert "PATH" in _confere(git)
    assert all(env.get("GIT_OPTIONAL_LOCKS") == "0" for env in git.envs)   # o que já valia continua valendo

    rg = _Captura(stdout=b"ripgrep 14.1.0")
    monkeypatch.setattr(subprocess, "run", rg)
    assert lexical.localizar_ripgrep(sys.executable) == sys.executable
    _confere(rg)


# ================================================== a guarda: lançamento novo sem filtro não entra
APP = Path(__file__).resolve().parents[1] / "app"
_LANCADORES_SUBPROCESS = {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}
_LANCADORES_ASYNC = {"create_subprocess_exec", "create_subprocess_shell", "subprocess_exec", "subprocess_shell"}
#: Estes não aceitam um ambiente filtrado do jeito que o resto do código passa: proibidos no `backend/app`.
_LANCADORES_OS = {"system", "popen", "startfile", "execv", "execve", "execl", "execle", "execlp", "execlpe", "execvp",
                  "execvpe", "spawnv", "spawnve", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnvp", "spawnvpe",
                  "posix_spawn", "posix_spawnp"}
#: (arquivo relativo a `app/`, função) que herda o ambiente de propósito. Só o supervisor: o filho dele É o backend,
#: que precisa da própria configuração.
EXCECOES = {("supervisor.py", "iniciar_backend")}


def _e_os_environ(no: ast.AST) -> bool:
    return (isinstance(no, ast.Attribute) and no.attr == "environ" and isinstance(no.value, ast.Name)
            and no.value.id == "os")


def _problema(no: ast.AST) -> str | None:
    """Por que este nó deixa um filho com o ambiente inteiro, ou `None`."""
    if isinstance(no, ast.Dict) and any(k is None and _e_os_environ(v) for k, v in zip(no.keys, no.values)):
        return "copia os.environ ({**os.environ})"
    if not isinstance(no, ast.Call):
        return None
    if isinstance(no.func, ast.Name) and no.func.id == "dict" and no.args and _e_os_environ(no.args[0]):
        return "copia os.environ (dict(os.environ, ...))"
    if not isinstance(no.func, ast.Attribute):
        return None
    base, nome = no.func.value, no.func.attr
    if nome == "copy" and _e_os_environ(base):
        return "copia os.environ (os.environ.copy())"
    if isinstance(base, ast.Name) and base.id == "os" and nome in _LANCADORES_OS:
        return f"os.{nome} herda o ambiente inteiro"
    if not ((isinstance(base, ast.Name) and base.id == "subprocess" and nome in _LANCADORES_SUBPROCESS)
            or nome in _LANCADORES_ASYNC):
        return None
    env = next((k.value for k in no.keywords if k.arg == "env"), None)
    if env is None or (isinstance(env, ast.Constant) and env.value is None):
        return f"{nome}() sem env= (herda o ambiente inteiro)"
    if _e_os_environ(env):
        return f"{nome}(env=os.environ)"
    return None


def lancamentos_sem_filtro(raiz: Path) -> list[str]:
    """Cada lançamento de processo em `raiz/**/*.py` sem `env=`, ou que copia ou passa `os.environ` inteiro."""
    erros: list[str] = []
    for arquivo in sorted(raiz.rglob("*.py")):
        rel = arquivo.relative_to(raiz).as_posix()
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"), filename=rel)
        funcoes = [n for n in ast.walk(arvore) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for no in ast.walk(arvore):
            motivo = _problema(no)
            if motivo is None:
                continue
            donas = [f for f in funcoes if f.lineno <= no.lineno <= (f.end_lineno or f.lineno)]
            dona = max(donas, key=lambda f: f.lineno).name if donas else "<módulo>"
            if (rel, dona) not in EXCECOES:
                erros.append(f"{rel}:{no.lineno} ({dona}) {motivo}")
    return erros


def test_nenhum_lancamento_do_backend_herda_o_ambiente_inteiro() -> None:
    erros = lancamentos_sem_filtro(APP)
    assert not erros, ("processo filho com acesso aos segredos do backend; use sdk.ambiente_dos_filhos() (ou "
                       "sdk.sem_segredos() para ferramenta de usuário, como o git):\n  " + "\n  ".join(erros))


def test_a_guarda_pega_os_formatos_que_vazam(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "import os, subprocess, asyncio\n"
        "def a():\n    subprocess.run(['x'])\n"
        "def b():\n    subprocess.Popen(['x'], env=None)\n"
        "def c():\n    subprocess.run(['x'], env=os.environ)\n"
        "def d():\n    subprocess.run(['x'], env=dict(os.environ, A='1'))\n"
        "def e():\n    subprocess.run(['x'], env={**os.environ})\n"
        "def f():\n    env = os.environ.copy()\n"
        "async def g():\n    await asyncio.create_subprocess_exec('x')\n"
        "def h():\n    os.system('x')\n"
        "def ok():\n    subprocess.run(['x'], env=filtrado())\n", encoding="utf-8")
    linhas = sorted(int(e.split(":")[1].split()[0]) for e in lancamentos_sem_filtro(tmp_path))
    assert linhas == [3, 5, 7, 9, 11, 13, 15, 17]
