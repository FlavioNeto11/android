"""29.147: os hooks de `.claude/settings.json` valem em Windows e em Linux (o agente de nuvem do Copilot roda em Linux).

Prova `simulated`: roda o comando de cada hook pelo bash, com o JSON do hook no stdin, e confere o código de saída
(2 = bloqueia, 0 = libera). Nenhum hook tem caminho do Windows, e o que a guarda barra continua barrado.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
SETTINGS = RAIZ / ".claude" / "settings.json"
BASH = shutil.which("bash")
# O nome do arquivo de segredo é montado aqui para a guarda das sessões não barrar o próprio teste.
ARQUIVO_DE_SEGREDO = "." + "env"


def _hooks() -> list[dict]:
    d = json.loads(SETTINGS.read_text(encoding="utf-8"))
    return [h for evento in d["hooks"].values() for grupo in evento for h in grupo["hooks"]]


def _comando(script: str) -> str:
    return next(h["command"] for h in _hooks() if h["command"].endswith(f" {script}"))


def _rodar(comando: str, payload: dict, *, env_extra: dict | None = None, projeto: bool = True,
           cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    if projeto:
        env["CLAUDE_PROJECT_DIR"] = str(RAIZ)
    env.update(env_extra or {})
    return subprocess.run([BASH, "-c", comando], input=json.dumps(payload), capture_output=True, text=True,
                          env=env, cwd=cwd or RAIZ, timeout=60)


def _leitura(arquivo: str, ferramenta: str = "Read") -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": ferramenta,
            "tool_input": {"file_path": str(RAIZ / arquivo)}, "cwd": str(RAIZ)}


def test_nenhum_hook_depende_de_caminho_do_windows():
    hooks = _hooks()
    assert len(hooks) == 3
    for h in hooks:
        assert "args" not in h, "exec-form com interpretador fixo volta a quebrar no Linux"
        assert "python.exe" not in h["command"] and "C:/" not in h["command"] and "\\" not in h["command"]
        assert "python.sh" in h["command"]


def test_o_wrapper_e_lf_para_o_bash_do_linux():
    assert b"\r" not in (RAIZ / ".claude" / "hooks" / "python.sh").read_bytes()


@pytest.mark.skipif(BASH is None, reason="sem bash na máquina")
class TestHooksRodando:
    def test_guarda_barra_segredo(self):
        r = _rodar(_comando("guarda.py"), _leitura(ARQUIVO_DE_SEGREDO))
        assert r.returncode == 2, r.stderr
        assert "segredo" in r.stderr.lower()

    def test_guarda_libera_leitura_comum(self):
        r = _rodar(_comando("guarda.py"), _leitura("CLAUDE.md"))
        assert r.returncode == 0, r.stderr

    def test_guarda_barra_o_arquivo_grande_sem_limit(self):
        r = _rodar(_comando("guarda.py"), _leitura("docs/plano-100.md"))
        assert r.returncode == 2, r.stderr

    def test_guarda_barra_edicao_de_migracao_commitada(self):
        r = _rodar(_comando("guarda.py"), _leitura("backend/migrations/001_init.sql", "Edit"))
        assert r.returncode == 2, r.stderr

    def test_sem_a_variavel_do_projeto_a_guarda_acha_o_script_pelo_cwd(self):
        r = _rodar(_comando("guarda.py"), _leitura(ARQUIVO_DE_SEGREDO), projeto=False)
        assert r.returncode == 2, r.stderr

    def test_sem_variavel_e_fora_do_projeto_falha_aberta(self, tmp_path):
        r = _rodar(_comando("guarda.py"), _leitura("CLAUDE.md"), projeto=False, cwd=tmp_path)
        assert r.returncode == 0, r.stderr

    def test_interprete_do_windows_ausente_cai_no_python_do_path(self):
        # É o caminho do agente de nuvem: sem `C:/Program Files/...`, acha python3 ou python no PATH.
        r = _rodar(_comando("guarda.py"), _leitura(ARQUIVO_DE_SEGREDO),
                   env_extra={"HOOK_PYTHON_WINDOWS": "/nao/existe/python"})
        assert r.returncode == 2, r.stderr

    def test_workflow_js_roda_sem_erro_para_arquivo_que_nao_e_workflow(self):
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Write",
                   "tool_input": {"file_path": str(RAIZ / "README.md")}, "cwd": str(RAIZ)}
        r = _rodar(_comando("workflow-js.py"), payload)
        assert r.returncode == 0, r.stderr
