"""scripts/limpar_branches_revisao.py (29.155, C18): só apaga branch dos prefixos permitidos, já na main, sem PR aberto e com
ponta antiga. Prova `simulated`: `gh` falso."""
from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("limpar", ROOT / "scripts" / "limpar_branches_revisao.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]

AGORA = datetime(2026, 10, 6, 21, 0, tzinfo=timezone.utc)


def sha(c: str) -> str:
    return c * 40


class FakeGh:
    def __init__(self, branches, estado=None, abertos=(), datas=None, falha_em=None):
        self.branches, self.estado, self.abertos = branches, estado or {}, set(abertos)
        self.datas, self.falha_em, self.chamadas = datas or {}, falha_em, []

    def __call__(self, *args: str) -> str:
        self.chamadas.append(args)
        if args[0] == "api" and args[1] == "--paginate":
            p = args[2].split("/heads/", 1)[1]
            return json.dumps([{"ref": f"refs/heads/{n}", "object": {"sha": s}} for n, s in self.branches if n.startswith(p)])
        if args[0] == "api" and "/compare/main..." in args[1]:
            s = args[1].rsplit("...", 1)[1]
            if self.falha_em == s:
                raise RuntimeError("gh api saiu com 1: simulado")
            return json.dumps({"status": self.estado.get(s, "behind")})
        if args[:2] == ("pr", "list"):
            return json.dumps([{"number": 1}] if args[args.index("--head") + 1] in self.abertos else [])
        if args[0] == "api" and "/commits/" in args[1]:
            return json.dumps({"commit": {"committer": {"date": self.datas.get(args[1].rsplit("/", 1)[1], "2026-10-05T10:00:00Z")}}})
        if args[:3] == ("api", "-X", "DELETE"):
            return ""
        raise AssertionError(args)

    def apagadas(self) -> list[str]:
        return [c[3].split("/heads/", 1)[1] for c in self.chamadas if c[:3] == ("api", "-X", "DELETE")]


def rodar(gh, *extra):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        codigo = mod.main(["--repo", "dono/repo", *extra], gh=gh, agora=AGORA)
    return codigo, out.getvalue(), err.getvalue()


class Limpeza(unittest.TestCase):
    def test_ensaio_nao_apaga(self) -> None:
        gh = FakeGh([("revisao/a", sha("a"))])
        codigo, out, _ = rodar(gh)
        self.assertEqual((codigo, gh.apagadas()), (0, []))
        self.assertIn("apagaria revisao/a", out)

    def test_aplicar_apaga_a_mesclada_antiga_sem_pr(self) -> None:
        gh = FakeGh([("revisao/a", sha("a")), ("teste/b", sha("b"))])
        self.assertEqual(rodar(gh, "--aplicar")[0], 0)
        self.assertEqual(gh.apagadas(), ["revisao/a", "teste/b"])

    def test_nao_mesclada_fica(self) -> None:
        gh = FakeGh([("revisao/a", sha("a"))], estado={sha("a"): "ahead"})
        rodar(gh, "--aplicar")
        self.assertEqual(gh.apagadas(), [])

    def test_divergida_fica(self) -> None:
        gh = FakeGh([("revisao/a", sha("a"))], estado={sha("a"): "diverged"})
        rodar(gh, "--aplicar")
        self.assertEqual(gh.apagadas(), [])

    def test_com_pr_aberto_fica(self) -> None:
        gh = FakeGh([("revisao/a", sha("a"))], abertos={"revisao/a"})
        rodar(gh, "--aplicar")
        self.assertEqual(gh.apagadas(), [])

    def test_ponta_recente_fica(self) -> None:
        gh = FakeGh([("revisao/a", sha("a"))], datas={sha("a"): "2026-10-06T20:00:00Z"})
        rodar(gh, "--aplicar")
        self.assertEqual(gh.apagadas(), [])

    def test_erro_da_api_deixa_a_branch_e_sai_com_1(self) -> None:
        gh = FakeGh([("revisao/a", sha("a")), ("revisao/b", sha("b"))], falha_em=sha("a"))
        codigo, out, _ = rodar(gh, "--aplicar")
        self.assertEqual(codigo, 1)
        self.assertEqual(gh.apagadas(), ["revisao/b"])
        self.assertIn("mantida revisao/a", out)

    def test_nunca_toca_fora_dos_prefixos(self) -> None:
        gh = FakeGh([("feat/x", sha("a")), ("main", sha("b")), ("revisao/ok", sha("c"))])
        rodar(gh, "--aplicar")
        self.assertEqual(gh.apagadas(), ["revisao/ok"])
        for c in gh.chamadas:  # a lista só pede os dois prefixos
            if c[:2] == ("api", "--paginate"):
                self.assertTrue(c[2].endswith(("/heads/revisao/", "/heads/teste/")))

    def test_nome_estranho_e_ignorado(self) -> None:
        gh = FakeGh([("revisao/a b", sha("a")), ("revisao/ok", sha("c"))])
        rodar(gh, "--aplicar")
        self.assertEqual(gh.apagadas(), ["revisao/ok"])

    def test_paginas_coladas_sao_lidas(self) -> None:
        achados = mod._itens('[{"ref": "refs/heads/revisao/a"}][{"ref": "refs/heads/revisao/b"}]')
        self.assertEqual(len(achados), 2)

    def test_repo_invalido(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(mod.main(["--repo", "x y"], gh=FakeGh([]), agora=AGORA), 1)


if __name__ == "__main__":
    unittest.main()
