"""scripts/rotulo_do_pr.py (29.171): rótulo só pelo prefixo da branch, só se existir, nunca cria nem tira. Prova `simulated`."""
from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("rotulo", ROOT / "scripts" / "rotulo_do_pr.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]


class FakeGh:
    def __init__(self, rotulos=("frente:github", "agente"), falha=False):
        self.rotulos, self.falha, self.chamadas = rotulos, falha, []

    def __call__(self, *args: str) -> str:
        self.chamadas.append(args)
        if self.falha:
            raise RuntimeError("gh label saiu com 1: simulado")
        if args[:2] == ("label", "list"):
            return json.dumps([{"name": r} for r in self.rotulos])
        return ""

    def edicoes(self) -> list[tuple[str, ...]]:
        return [c for c in self.chamadas if c[:2] == ("pr", "edit")]


def rodar(gh, branch, *extra):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        codigo = mod.main(["--repo", "dono/repo", "--pr", "7", "--branch", branch, *extra], gh=gh)
    return codigo, out.getvalue(), err.getvalue()


class Rotulo(unittest.TestCase):
    def test_prefixos(self) -> None:
        self.assertEqual(mod.rotulo_da_branch("ci/29-171-modelo-pr"), "frente:github")
        self.assertEqual(mod.rotulo_da_branch("copilot/fix-x"), "agente")

    def test_prefixos_das_outras_frentes_29_203(self) -> None:
        for branch, rotulo in (("devops/29-196-funil", "frente:devops"), ("jev/integ-61", "frente:jev"),
                               ("aprendizado/integ-61", "frente:aprendizado"), ("portal/x", "frente:portal"), ("canais/28-77", "frente:canais")):
            self.assertEqual(mod.rotulo_da_branch(branch), rotulo, branch)

    def test_outros_prefixos_nao_ganham_rotulo(self) -> None:
        for b in ("feat/31-146-x", "fix/y", "revisao/z", "main", "devopsx/29-160", "jevs/a"):
            self.assertIsNone(mod.rotulo_da_branch(b), b)

    def test_nome_estranho_e_ignorado(self) -> None:
        for b in ("ci/a b", "ci/../x", "ci/$(x)", "ci/" + "a" * 300):
            self.assertIsNone(mod.rotulo_da_branch(b), b)

    def test_ensaio_nao_escreve(self) -> None:
        gh = FakeGh()
        codigo, out, _ = rodar(gh, "ci/x")
        self.assertEqual((codigo, gh.edicoes()), (0, []))
        self.assertIn("rotularia PR 7", out)

    def test_aplicar_adiciona_so_o_rotulo_do_prefixo(self) -> None:
        gh = FakeGh()
        rodar(gh, "ci/x", "--aplicar")
        self.assertEqual(gh.edicoes(), [("pr", "edit", "7", "--repo", "dono/repo", "--add-label", "frente:github")])

    def test_rotulo_inexistente_nao_e_criado(self) -> None:
        gh = FakeGh(rotulos=())
        codigo, out, _ = rodar(gh, "ci/x", "--aplicar")
        self.assertEqual((codigo, gh.edicoes()), (0, []))
        self.assertIn("não existe", out)
        self.assertFalse(any(c[:2] == ("label", "create") for c in gh.chamadas))

    def test_erro_do_gh_deixa_o_pr_e_sai_com_0(self) -> None:
        codigo, _, err = rodar(FakeGh(falha=True), "ci/x", "--aplicar")
        self.assertEqual(codigo, 0)
        self.assertIn("deixado como está", err)

    def test_repo_invalido(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(mod.main(["--repo", "x y", "--pr", "1", "--branch", "ci/x"], gh=FakeGh()), 1)

    def test_nunca_tira_rotulo(self) -> None:
        gh = FakeGh()
        rodar(gh, "ci/x", "--aplicar")
        self.assertFalse(any("--remove-label" in c for c in gh.chamadas))


if __name__ == "__main__":
    unittest.main()
