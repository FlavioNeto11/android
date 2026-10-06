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
    def __init__(self, branches, estado=None, abertos=(), datas=None, falha_em=None, sem_pr=(), base_aberta=(), muda_ponta=()):
        self.branches, self.estado, self.abertos = branches, estado or {}, set(abertos)
        self.sem_pr, self.base_aberta, self.muda_ponta = set(sem_pr), set(base_aberta), set(muda_ponta)
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
            if "--base" in args:
                return json.dumps([{"number": 2}] if args[args.index("--base") + 1] in self.base_aberta else [])
            nome, estado = args[args.index("--head") + 1], args[args.index("--state") + 1]
            if estado == "open":
                return json.dumps([{"number": 1}] if nome in self.abertos else [])
            return json.dumps([] if nome in self.sem_pr else [{"number": 1}])
        if args[0] == "api" and "/git/ref/heads/" in args[1]:
            nome = args[1].split("/heads/", 1)[1]
            atual = next(s for n, s in self.branches if n == nome)
            return json.dumps({"object": {"sha": "f" * 40 if nome in self.muda_ponta else atual}})
        if args[0] == "api" and "/commits/" in args[1]:
            data = self.datas.get(args[1].rsplit("/", 1)[1], "2026-10-05T10:00:00Z")
            return json.dumps({"commit": {"committer": {"date": data} if data else None}})
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
        gh = FakeGh([("revisao/a", sha("a")), ("revisao/b", sha("b"))])
        self.assertEqual(rodar(gh, "--aplicar")[0], 0)
        self.assertEqual(gh.apagadas(), ["revisao/a", "revisao/b"])

    def test_identical_tambem_conta_como_na_main(self) -> None:
        gh = FakeGh([("revisao/a", sha("a"))], estado={sha("a"): "identical"})
        rodar(gh, "--aplicar")
        self.assertEqual(gh.apagadas(), ["revisao/a"])

    def test_branch_que_nunca_teve_pr_fica(self) -> None:
        gh = FakeGh([("revisao/a", sha("a"))], estado={sha("a"): "identical"}, sem_pr={"revisao/a"})
        rodar(gh, "--aplicar")
        self.assertEqual(gh.apagadas(), [])

    def test_pr_aberto_com_ela_como_base_fica(self) -> None:
        gh = FakeGh([("revisao/a", sha("a"))], base_aberta={"revisao/a"})
        rodar(gh, "--aplicar")
        self.assertEqual(gh.apagadas(), [])

    def test_ponta_que_mudou_antes_de_apagar_fica(self) -> None:
        gh = FakeGh([("revisao/a", sha("a")), ("revisao/b", sha("b"))], muda_ponta={"revisao/a"})
        rodar(gh, "--aplicar")
        self.assertEqual(gh.apagadas(), ["revisao/b"])

    def test_teto_por_execucao(self) -> None:
        gh = FakeGh([(f"revisao/{i:03d}", sha("a")) for i in range(mod.MAXIMO_POR_EXECUCAO + 3)])
        rodar(gh, "--aplicar")
        self.assertEqual(len(gh.apagadas()), mod.MAXIMO_POR_EXECUCAO)

    def test_commit_sem_committer_vira_erro_e_mantem(self) -> None:
        gh = FakeGh([("revisao/a", sha("a")), ("revisao/b", sha("b"))], datas={sha("a"): ""})
        codigo, _, _ = rodar(gh, "--aplicar")
        self.assertEqual((codigo, gh.apagadas()), (1, ["revisao/b"]))

    def test_teste_barra_barra_e_ponto_ponto_sao_ignorados(self) -> None:
        gh = FakeGh([("teste/b", sha("b")), ("revisao/../main", sha("a")), ("revisao//x", sha("c")), ("revisao/ok", sha("d"))])
        rodar(gh, "--aplicar")
        self.assertEqual(gh.apagadas(), ["revisao/ok"])

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
                self.assertTrue(c[2].endswith("/heads/revisao/"))

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
