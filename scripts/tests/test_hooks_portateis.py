"""29.147: os hooks de `.claude/settings.json` valem em Windows e em Linux (o agente de nuvem do Copilot roda em Linux).

Prova `simulated`: roda o comando de cada hook pelo bash, com o JSON do hook no stdin, e confere o código de saída
(2 = bloqueia, 0 = libera). Nenhum hook tem caminho do Windows, e o que a guarda barra continua barrado.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
SETTINGS = RAIZ / ".claude" / "settings.json"
# O nome do arquivo de segredo é montado aqui para a guarda das sessões não barrar o próprio teste.
ARQUIVO_DE_SEGREDO = "." + "env"


def _hooks() -> list[dict]:
    d = json.loads(SETTINGS.read_text(encoding="utf-8"))
    return [h for evento in d["hooks"].values() for grupo in evento for h in grupo["hooks"]]


def _comando(script: str) -> list[str]:
    """O hook como o Claude Code o roda: exec-form (command + args, sem shell), com a variável do projeto trocada."""
    h = next(h for h in _hooks() if h["args"][-1].endswith("/" + script))
    return [h["command"], *[a.replace("${CLAUDE_PROJECT_DIR}", str(RAIZ)) for a in h["args"]]]


def _rodar(comando, payload: dict, *, env_extra: dict | None = None, projeto: bool = True,
           cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    if projeto:
        env["CLAUDE_PROJECT_DIR"] = str(RAIZ)
    env.update(env_extra or {})
    return subprocess.run(comando, input=json.dumps(payload), capture_output=True, text=True,
                          env=env, cwd=cwd or RAIZ, timeout=60)


def _leitura(arquivo: str, ferramenta: str = "Read") -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": ferramenta,
            "tool_input": {"file_path": str(RAIZ / arquivo)}, "cwd": str(RAIZ)}


def test_nenhum_hook_depende_de_caminho_do_windows():
    hooks = _hooks()
    assert len(hooks) == 3
    for h in hooks:
        texto = h["command"] + " " + " ".join(h["args"])
        assert "python.exe" not in texto and "C:/" not in texto and "C:\\" not in texto, "caminho do Windows no hook"
        # exec-form: o interpretador sai do PATH da máquina (o Python do dono no Windows, o setup-python no agente)
        assert h["command"] == "python"


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

    def test_workflow_js_roda_sem_erro_para_arquivo_que_nao_e_workflow(self):
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Write",
                   "tool_input": {"file_path": str(RAIZ / "README.md")}, "cwd": str(RAIZ)}
        r = _rodar(_comando("workflow-js.py"), payload)
        assert r.returncode == 0, r.stderr

