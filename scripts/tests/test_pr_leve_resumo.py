"""scripts/pr_leve_resumo.py (29.179): resumo de uma linha do job do CI leve. Prova `simulated`: `gh` falso."""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("resumo", ROOT / "scripts" / "pr_leve_resumo.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]


def passo(nome, ini, fim, conclusao="success"):
    return {"name": nome, "conclusion": conclusao, "started_at": ini, "completed_at": fim}


JOB = {"name": "docs-check + scripts/tests", "runner_name": "RUNNER-1", "steps": [
    passo("Set up job", "2026-10-06T22:00:00Z", "2026-10-06T22:00:02Z"),
    passo("Run actions/checkout@v4", "2026-10-06T22:00:02Z", "2026-10-06T22:00:05Z"),
    passo("instalar dependências", "2026-10-06T22:00:05Z", "2026-10-06T22:00:35Z"),
    passo("docs-check", "2026-10-06T22:00:35Z", "2026-10-06T22:00:38Z"),
    passo("pytest scripts/tests", "2026-10-06T22:00:38Z", "2026-10-06T22:02:38Z"),
    passo("pulado", "2026-10-06T22:02:38Z", "2026-10-06T22:02:38Z", "skipped"),
    passo("Resumo do job", "2026-10-06T22:02:38Z", None, None),
    passo("Post Run actions/checkout@v4", "2026-10-06T22:02:39Z", "2026-10-06T22:02:40Z"),
]}
OUTRO = {"name": "frontend", "runner_name": "RUNNER-2", "steps": []}
PYTEST = "........\n779 passed, 11 skipped, 4 subtests passed in 119.97s (0:01:59)\n"
VITEST = "x\n\x1b[2m      Tests \x1b[22m \x1b[1m\x1b[32m1941 passed\x1b[39m\x1b[22m\x1b[90m (1941)\x1b[39m\n"


def gh_com(*jobs):
    return lambda *a: "\n".join(json.dumps(j) for j in jobs)


def rodar(gh, *extra):
    out = io.StringIO()
    with redirect_stdout(out):
        codigo = mod.main(["--nome", "docs-check + scripts/tests", "--repo", "dono/repo", "--run", "9", "--runner", "RUNNER-1", "--espera", "0", *extra], gh=gh)
    return codigo, out.getvalue().strip()


def arquivo(texto):
    p = Path(tempfile.mkdtemp()) / "saida.txt"
    p.write_text(texto, encoding="utf-8")
    return str(p)


class Resumo(unittest.TestCase):
    def test_etapas_tem_tempo_e_deixa_fora_setup_post_pulado_e_o_proprio_resumo(self) -> None:
        self.assertEqual(mod.etapas(JOB["steps"]), [("Run actions/checkout@v4", 3), ("instalar dependências", 30), ("docs-check", 3), ("pytest scripts/tests", 120)])

    def test_linha_com_tempos_soma_e_contagem_de_testes(self) -> None:
        codigo, texto = rodar(gh_com(OUTRO, JOB), "--pytest", arquivo(PYTEST))
        self.assertEqual(codigo, 0)
        self.assertIn("**docs-check + scripts/tests**:", texto)
        self.assertIn("instalar dependências 30 s", texto)
        self.assertIn("pytest scripts/tests 120 s", texto)
        self.assertIn("soma das etapas 156 s", texto)
        self.assertIn("pytest 779 passed, 11 skipped", texto)
        self.assertEqual(len(texto.splitlines()), 1)

    def test_vitest_com_cores_ansi(self) -> None:
        _, texto = rodar(gh_com(JOB), "--vitest", arquivo(VITEST))
        self.assertIn("vitest 1941 passed", texto)

    def test_falha_de_teste_aparece_na_contagem(self) -> None:
        _, texto = rodar(gh_com(JOB), "--pytest", arquivo("1 failed, 779 passed, 10 skipped in 5s\n"))
        self.assertIn("pytest 1 failed, 779 passed, 10 skipped", texto)

    def test_acha_o_job_pelo_runner_e_cai_para_o_nome(self) -> None:
        sem_runner = dict(JOB, runner_name="outro")
        _, texto = rodar(gh_com(OUTRO, sem_runner))
        self.assertIn("instalar dependências 30 s", texto)

    def test_nunca_derruba_o_job(self) -> None:
        def falha(*a):
            raise RuntimeError("o gh falhou (código 1)")
        for gh in (falha, gh_com(OUTRO), lambda *a: "isto não é json"):
            codigo, texto = rodar(gh)
            self.assertEqual(codigo, 0)
            self.assertIn("resumo indisponível", texto)

    def test_saida_nao_leva_runner_nem_repo(self) -> None:
        _, texto = rodar(gh_com(JOB), "--pytest", arquivo(PYTEST))
        for proibido in ("RUNNER-1", "dono/repo", "dono"):
            self.assertNotIn(proibido, texto)

    def test_so_le_nunca_escreve(self) -> None:
        chamadas = []
        def gh(*a):
            chamadas.append(a)
            return json.dumps(JOB)
        rodar(gh)
        for c in chamadas:
            self.assertEqual(c[0], "api")
            for escrita in ("-X", "--method", "-f", "-F", "--field", "--raw-field", "--input"):
                self.assertNotIn(escrita, c)

    def test_repo_fora_do_formato(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(mod.main(["--nome", "x", "--repo", "x y", "--run", "1", "--runner", "r", "--espera", "0"], gh=gh_com(JOB)), 0)
        self.assertIn("resumo indisponível", out.getvalue())


if __name__ == "__main__":
    unittest.main()
