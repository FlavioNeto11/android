"""29.156 (fatia 3): o ensaio do rollback com migração roda de verdade, num repositório e num banco de mentira.

Simulado (`simulated`): o repositório git, o `backend/app/db.py` do "código antigo" e o banco do backup são montados aqui; o
`rollback-ensaio.ps1` é copiado para a árvore descartável (a raiz é passada por `-Raiz`). Nada toca `data\\` do central.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
PWSH = shutil.which("pwsh")
GIT = shutil.which("git")
SENTINELA = "SENTINELA-SEGREDO-4b1e"

precisa_pwsh = pytest.mark.skipif(PWSH is None or GIT is None or sys.platform != "win32",
                                  reason="precisa de pwsh e git no Windows")

DB_ANTIGO = """
class Database:
    def __init__(self, caminho):
        self.caminho = caminho

    def migrate(self):
        return %s
"""


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull}
    r = subprocess.run([GIT, "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True)
    return r.stdout.strip()


def _arvore(tmp: Path, *, migracao_do_backup: str = "002_b", migracao_antes: str = "002_b", pendentes: str = "[]",
            com_backup: bool = True, com_linha: bool = True, commit_na_linha: str | None = None) -> Path:
    raiz = tmp / "arv"
    shutil.copytree(SCRIPTS, raiz / "scripts", ignore=shutil.ignore_patterns("tests", "__pycache__"))
    (raiz / "backend" / "app").mkdir(parents=True)
    (raiz / "backend" / "app" / "__init__.py").write_text("", encoding="utf-8")
    (raiz / "backend" / "app" / "db.py").write_text(DB_ANTIGO % pendentes, encoding="utf-8")
    (raiz / "backend" / "migrations").mkdir()
    for n in ("001_a", "002_b"):
        (raiz / "backend" / "migrations" / f"{n}.sql").write_text("-- x", encoding="utf-8")
    _git(raiz, "init", "-q")
    _git(raiz, "add", "backend")
    _git(raiz, "commit", "-q", "-m", "codigo antigo")
    commit = _git(raiz, "rev-parse", "HEAD")
    pasta = raiz / "data" / "backups" / "20261006-181424"
    pasta.mkdir(parents=True)
    if com_backup:
        con = sqlite3.connect(pasta / "poc.sqlite3")
        con.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)")
        con.execute("INSERT INTO schema_migrations VALUES (?)", (migracao_do_backup,))
        con.execute("CREATE TABLE t (x TEXT)")
        con.commit()
        con.close()
        (pasta / ("credentials" + ".key")).write_text(SENTINELA, encoding="utf-8")
        (pasta / "manifesto.json").write_text(json.dumps({"ts": "2026-10-06T18:14:24-03:00", "migration": migracao_do_backup,
                                                          "tables": 2}), encoding="utf-8")
    if com_linha:
        linha = {"ts_utc": "2026-10-06T21:15:51Z", "resultado": "ok", "commit_antes": commit_na_linha or commit,
                 "migracao_antes": migracao_antes, "commit_depois": "f" * 40, "migracao_depois": "003_c",
                 "backup": "20261006-181424", "tag": None, "duracao_s": 80.0, "opcoes": []}
        falha = dict(linha, resultado="falhou", ts_utc="2026-10-06T22:00:00Z")
        (raiz / "data" / "deploys.jsonl").write_text("\n".join(json.dumps(x) for x in (linha, falha)) + "\n",
                                                     encoding="utf-8")
    return raiz


def _rodar(raiz: Path) -> tuple[int, dict | None, str]:
    r = subprocess.run([PWSH, "-NoProfile", "-File", str(raiz / "scripts" / "rollback-ensaio.ps1"), "-Raiz", str(raiz),
                        "-Python", sys.executable], capture_output=True, text=True, timeout=300)
    ultimo = raiz / "data" / "rollback-ensaio" / "ultimo.json"
    return r.returncode, (json.loads(ultimo.read_text(encoding="utf-8")) if ultimo.exists() else None), r.stdout + r.stderr


@precisa_pwsh
class TestRollbackEnsaio:
    def test_codigo_antigo_abre_o_banco_do_backup_sem_querer_migrar(self, tmp_path):
        raiz = _arvore(tmp_path)
        codigo, v, saida = _rodar(raiz)
        assert codigo == 0, saida
        assert v["resultado"] == "ok" and v["integridade"] == "ok"
        assert v["migracao_da_copia"] == v["migracao_antes"] == "002_b"
        assert v["migracoes_que_o_codigo_antigo_quis_aplicar"] == []
        assert v["deploy_ts"] == "2026-10-06T21:15:51Z", "usa a linha ok mais nova, ignora a de falha"
        assert v["migracao_do_codigo_antigo"] == "002_b"

    def test_trabalho_com_a_chave_restaurada_some_e_nada_vaza(self, tmp_path):
        raiz = _arvore(tmp_path)
        codigo, v, saida = _rodar(raiz)
        assert codigo == 0, saida
        assert not any((raiz / "data" / "rollback-ensaio" / "trabalho").iterdir())
        tudo = saida + json.dumps(v) + (raiz / "data" / "rollback-ensaio" / "historico.jsonl").read_text(encoding="utf-8")
        assert SENTINELA not in tudo and str(raiz) not in tudo
        assert not (raiz / "data" / "poc.sqlite3").exists(), "o ensaio nunca cria nem toca data\\poc.sqlite3"

    def test_codigo_antigo_que_quer_migrar_o_banco_do_backup_falha(self, tmp_path):
        raiz = _arvore(tmp_path, pendentes="['003_c']")
        codigo, v, _ = _rodar(raiz)
        assert codigo == 1 and v["resultado"] == "falhou" and "quis aplicar" in v["motivo"]
        assert v["migracoes_que_o_codigo_antigo_quis_aplicar"] == ["003_c"]

    def test_migracao_do_backup_diferente_da_linha_falha(self, tmp_path):
        raiz = _arvore(tmp_path, migracao_do_backup="001_a")
        codigo, v, _ = _rodar(raiz)
        assert codigo == 1 and "difere" in v["motivo"]

    def test_backup_podado_e_pulado_nao_aprovado(self, tmp_path):
        raiz = _arvore(tmp_path, com_backup=False)
        codigo, v, _ = _rodar(raiz)
        assert codigo == 2 and v["resultado"] == "pulado" and "backup" in v["motivo"]

    def test_sem_linha_de_deploy_e_pulado(self, tmp_path):
        raiz = _arvore(tmp_path, com_linha=False)
        codigo, v, _ = _rodar(raiz)
        assert codigo == 2 and v["resultado"] == "pulado"

    def test_commit_que_nao_existe_e_pulado(self, tmp_path):
        raiz = _arvore(tmp_path, commit_na_linha="0" * 40)
        codigo, v, _ = _rodar(raiz)
        assert codigo == 2 and "commit_antes" in v["motivo"]

    def test_sobra_de_ensaio_anterior_e_varrida(self, tmp_path):
        raiz = _arvore(tmp_path)
        velha = raiz / "data" / "rollback-ensaio" / "trabalho" / "20200101-000000"
        velha.mkdir(parents=True)
        (velha / ("credentials" + ".key")).write_text(SENTINELA, encoding="utf-8")
        codigo, v, saida = _rodar(raiz)
        assert codigo == 0, saida
        assert not velha.exists() and v["sobras_antigas"] == 0

    def test_nao_mexe_no_git_do_checkout(self, tmp_path):
        raiz = _arvore(tmp_path)
        antes = _git(raiz, "worktree", "list")
        codigo, _, saida = _rodar(raiz)
        assert codigo == 0, saida
        assert _git(raiz, "worktree", "list") == antes
        texto = (SCRIPTS / "rollback-ensaio.ps1").read_text(encoding="utf-8")
        codigo = texto[texto.index("#>") + 2:]  # sem o bloco de ajuda, que cita o checkout para dizer que não o toca
        assert "worktree" not in codigo and "checkout" not in codigo and "git -C $Raiz archive" in codigo.replace("& git", "git")
