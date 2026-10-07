"""scripts/pg_necessario.py (29.193): a corrida noturna do PostgreSQL só roda se algo que ela testa mudou. Prova `simulated`: `gh` falso."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("pg_necessario", ROOT / "scripts" / "pg_necessario.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

REPO = "dono/repo"
HEAD = "b" * 40
VERDE = "a" * 40


def run(n, sha, evento="schedule"):
    return {"id": n, "head_sha": sha, "event": evento}


def job(conclusao, nome=mod.JOB):
    return {"name": nome, "conclusion": conclusao}


class Fake:
    def __init__(self, runs, jobs_por_run, compare=None, falha_em=None):
        self.runs, self.jobs, self.compare, self.falha_em, self.chamadas = runs, jobs_por_run, compare, falha_em, []

    def __call__(self, *args: str) -> str:
        self.chamadas.append(args)
        rota = args[1]
        if self.falha_em and self.falha_em in rota:
            raise RuntimeError("o gh falhou (código 1)")
        if "/workflows/ci.yml/runs" in rota:
            return json.dumps({"workflow_runs": self.runs})
        if "/compare/" in rota:
            return json.dumps(self.compare)
        n = int(re.search(r"/runs/(\d+)/jobs", rota).group(1))
        return json.dumps({"jobs": self.jobs[n]})


def comparacao(*arquivos, status="ahead", commits=3):
    return {"status": status, "total_commits": commits, "files": [{"filename": a} for a in arquivos]}


class Decidir(unittest.TestCase):
    def gh(self, compare, verde=True, **kw):
        return Fake([run(2, "c" * 40), run(1, VERDE)], {2: [job("failure")], 1: [job("success" if verde else "failure"), job("failure", "outro")]},
                    compare, **kw)

    def test_so_docs_e_scripts_mudaram_pula(self) -> None:
        rodar, motivo = mod.decidir(REPO, "schedule", HEAD, "9", self.gh(comparacao("docs/x.md", "scripts/y.py", "frontend/src/a.ts", ".github/workflows/pr-leve.yml")))
        self.assertFalse(rodar)
        self.assertIn("nenhum que o PostgreSQL teste", motivo)

    def test_arquivo_relevante_roda(self) -> None:
        for arq in ("backend/app/api.py", "backend/tests/test_x.py", "backend/migrations/001_a.sql", "backend/requirements.txt",
                    "backend/app/conhecimento/apps/x/catalogo.yaml", ".github/workflows/ci.yml", ".github/actions/python-isolado/action.yml"):
            self.assertTrue(mod.decidir(REPO, "schedule", HEAD, "9", self.gh(comparacao("docs/x.md", arq)))[0], arq)

    def test_mesmo_commit_do_ultimo_verde_pula(self) -> None:
        self.assertFalse(mod.decidir(REPO, "schedule", VERDE, "9", self.gh(comparacao()))[0])

    def test_disparo_manual_roda_sempre(self) -> None:
        gh = self.gh(comparacao("docs/x.md"))
        self.assertTrue(mod.decidir(REPO, "workflow_dispatch", HEAD, "9", gh)[0])
        self.assertEqual(gh.chamadas, [])

    def test_sem_corrida_verde_recente_roda(self) -> None:
        self.assertTrue(mod.decidir(REPO, "schedule", HEAD, "9", self.gh(comparacao("docs/x.md"), verde=False))[0])

    def test_a_corrida_atual_nao_conta_como_verde_e_pr_nao_conta(self) -> None:
        gh = Fake([run(9, "d" * 40), run(5, "e" * 40, "pull_request"), run(1, VERDE)], {9: [job("success")], 5: [job("success")], 1: [job("success")]}, comparacao("docs/x.md"))
        self.assertEqual(mod.ultima_verde(REPO, "9", gh), VERDE)

    def test_incerteza_sempre_roda(self) -> None:
        casos = [
            self.gh(comparacao("docs/x.md"), falha_em="/compare/"),
            self.gh(comparacao("docs/x.md"), falha_em="/workflows/ci.yml/runs"),
            self.gh(comparacao(*[f"docs/{i}.md" for i in range(300)])),
            self.gh(comparacao("docs/x.md", commits=900)),
            self.gh(comparacao("docs/x.md", status="behind")),
            self.gh(comparacao("docs/x.md", status="algo-novo")),
            self.gh({"files": "quebrado"}),
        ]
        for gh in casos:
            self.assertTrue(mod.decidir(REPO, "schedule", HEAD, "9", gh)[0])
        self.assertTrue(mod.decidir(REPO, "schedule", "curto", "9", self.gh(comparacao("docs/x.md")))[0])

    def test_so_le_nunca_escreve(self) -> None:
        gh = self.gh(comparacao("docs/x.md"))
        mod.decidir(REPO, "schedule", HEAD, "9", gh)
        for c in gh.chamadas:
            self.assertEqual(c[0], "api")
            for escrita in ("-X", "--method", "-f", "-F", "--field", "--input"):
                self.assertNotIn(escrita, c)

    def test_erro_do_gh_real_nao_leva_o_texto_do_gh(self) -> None:
        import subprocess
        from unittest import mock
        falso = subprocess.CompletedProcess(["gh"], 1, stdout="", stderr="HTTP 404: repos/dono/segredo-repo/compare")
        with mock.patch.object(mod.subprocess, "run", return_value=falso):
            with self.assertRaises(RuntimeError) as c:
                mod.gh_real("api", "x")
        self.assertNotIn("segredo-repo", str(c.exception))


class LinhaDeComando(unittest.TestCase):
    def _main(self, gh, *args, saida=None):
        out = io.StringIO()
        anterior = os.environ.get("GITHUB_OUTPUT")
        if saida:
            os.environ["GITHUB_OUTPUT"] = str(saida)
        try:
            with redirect_stdout(out):
                return mod.main(list(args), gh=gh), out.getvalue()
        finally:
            if saida:
                if anterior is None:
                    os.environ.pop("GITHUB_OUTPUT", None)
                else:
                    os.environ["GITHUB_OUTPUT"] = anterior

    def test_escreve_rodar_no_github_output_e_sai_com_0(self) -> None:
        saida = Path(tempfile.mkdtemp()) / "out.txt"
        gh = Fake([run(1, VERDE)], {1: [job("success")]}, comparacao("docs/x.md"))
        codigo, texto = self._main(gh, "--repo", REPO, "--evento", "schedule", "--head", HEAD, "--run", "9", saida=saida)
        self.assertEqual(codigo, 0)
        self.assertEqual(saida.read_text(encoding="utf-8"), "rodar=false\n")
        self.assertIn("rodar=false", texto)

    def test_argumento_torto_roda_em_vez_de_pular(self) -> None:
        saida = Path(tempfile.mkdtemp()) / "out.txt"
        codigo, _ = self._main(Fake([], {}), "--repo", "x y", "--evento", "schedule", "--head", HEAD, "--run", "9", saida=saida)
        self.assertEqual(codigo, 0)
        self.assertEqual(saida.read_text(encoding="utf-8"), "rodar=true\n")


class Workflow(unittest.TestCase):
    def test_job_porteiro_hospedado_e_postgres_depende_dele_sem_pular_por_erro(self) -> None:
        self.assertRegex(CI, r"(?m)^  pg-necessario:\n(?:.*\n){0,6}?    runs-on: ubuntu-latest")
        bloco = CI[CI.index("  backend-postgres:"):]
        bloco = bloco[: bloco.index("\n    steps:")]
        self.assertIn("needs: pg-necessario", bloco)
        self.assertIn("!cancelled()", bloco)
        self.assertIn("needs.pg-necessario.outputs.rodar != 'false'", bloco)  # job que falhou ou saída vazia: roda
        self.assertIn("github.event_name == 'schedule'", bloco)

    def test_porteiro_chama_o_script_com_o_ambiente_do_run(self) -> None:
        self.assertIn("python3 scripts/pg_necessario.py", CI)


if __name__ == "__main__":
    unittest.main()
