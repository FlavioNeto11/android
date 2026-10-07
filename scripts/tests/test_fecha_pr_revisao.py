"""scripts/fecha_pr_revisao.py (29.201): fecha sem merge os PRs de revisão cumpridos. Prova `simulated`: `gh` falso."""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("fecha_pr_revisao", ROOT / "scripts" / "fecha_pr_revisao.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]

REPO = "dono/repo"
AGORA = datetime(2026, 10, 7, 14, 0, tzinfo=timezone.utc)
CODEX = {"login": "chatgpt-codex-connector[bot]"}


def pr(n, horas_atras=5, titulo="[revisão] 31.1 x"):
    return {"number": n, "title": titulo, "createdAt": (AGORA - timedelta(hours=horas_atras)).strftime("%Y-%m-%dT%H:%M:%SZ")}


RESUMO_OK = "<!-- codex-pull-request-review-summary -->\n| Code Review | ✅ **Completed** |"
RESUMO_FALHOU = "<!-- codex-pull-request-review-summary -->\n| Code Review | ⚠️ **Failed** |"
LIMITE = "You have reached your Codex usage limits for code reviews."


class Fake:
    """`dados[n]` = (revisoes, comentarios, reacoes)."""

    def __init__(self, prs, dados, falha_em=None):
        self.prs, self.dados, self.falha_em, self.chamadas = prs, dados, falha_em, []

    def __call__(self, *args: str) -> str:
        self.chamadas.append(args)
        if self.falha_em and self.falha_em in " ".join(args):
            raise RuntimeError("o gh falhou")
        if args[:2] == ("pr", "list"):
            return json.dumps(self.prs)
        if args[0] == "api":
            rota = args[-1]
            n = int(rota.split("/")[4])
            rev, com, rea = self.dados[n]
            if rota.endswith("/reviews"):
                return json.dumps([{"user": CODEX, "body": "x"} for _ in range(rev)] + [{"user": {"login": "fulano"}, "body": "y"}])
            if rota.endswith("/comments"):
                return json.dumps([{"user": CODEX, "body": c} for c in com])
            if rota.endswith("/reactions"):
                return json.dumps([{"user": CODEX, "content": r} for r in rea])
        return ""

    def fechamentos(self):
        return [c for c in self.chamadas if c[:2] == ("pr", "close")]


DADOS = {
    1: (2, [RESUMO_OK], []),     # com achados
    2: (0, [RESUMO_OK], ["+1"]),  # sem achado
    3: (0, [LIMITE], []),         # limite
    4: (0, [RESUMO_FALHOU], []),  # falhou
    5: (0, [], ["eyes"]),         # andamento
    6: (2, [RESUMO_OK], []),     # com achados e lido
}


def rodar(gh, *extra):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        codigo = mod.main(["--repo", REPO, *extra], gh=gh, agora=AGORA)
    return codigo, out.getvalue(), err.getvalue()


class Estado(unittest.TestCase):
    def test_classifica_o_que_o_codex_fez(self) -> None:
        gh = Fake([], DADOS)
        got = {n: mod.estado_do_codex(REPO, n, gh) for n in DADOS}
        self.assertEqual(got, {1: "com_achados", 2: "sem_achado", 3: "limite", 4: "falhou", 5: "andamento", 6: "com_achados"})

    def test_revisao_de_gente_nao_conta_como_do_codex(self) -> None:
        gh = Fake([], {7: (0, [], [])})
        self.assertEqual(mod.estado_do_codex(REPO, 7, gh), "andamento")  # o falso devolve uma revisão de "fulano" que não é do Codex


class Login(unittest.TestCase):
    def test_so_o_login_exato_do_bot_decide(self) -> None:
        falso = {"user": {"login": "codex-fan"}, "body": "usage limits"}
        self.assertFalse(mod._do_codex(falso))
        self.assertTrue(mod._do_codex({"user": CODEX}))


class Decisao(unittest.TestCase):
    def test_regras(self) -> None:
        d = mod.decidir
        self.assertEqual(d("sem_achado", 1, 5, set(), 2), (True, "sem_achado"))
        self.assertFalse(d("sem_achado", 1, 1, set(), 2)[0])  # jovem demais
        self.assertFalse(d("andamento", 1, 9, set(), 2)[0])
        self.assertFalse(d("com_achados", 1, 9, set(), 2)[0])  # achado não lido
        self.assertTrue(d("com_achados", 1, 9, {1}, 2)[0])
        self.assertTrue(d("limite", 1, 9, set(), 2)[0])
        self.assertTrue(d("falhou", 1, 9, set(), 2)[0])


class Linha(unittest.TestCase):
    def prs(self):
        return [pr(n) for n in DADOS] + [pr(8, titulo="feat: outra coisa"), pr(9, titulo="[revisão] 31.2 y")]

    def test_ensaio_nao_fecha_nada_e_so_olha_titulo_de_revisao(self) -> None:
        gh = Fake(self.prs(), {**DADOS, 9: (0, [], [])})
        codigo, out, _ = rodar(gh)
        self.assertEqual(codigo, 0)
        self.assertIn("ensaio: fecharia 3 de 7", out)  # 2, 3 e 4 fecham; 1 não lido, 5 e 9 andamento, 6 não lido (sem --lidos)
        self.assertEqual(gh.fechamentos(), [])
        self.assertNotIn("| 8 |", out)

    def test_aplicar_fecha_com_comentario_padrao_e_so_os_decididos(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            lidos = Path(d) / "lidos.json"
            lidos.write_text(json.dumps({"1": {"confirmados": 2}, "6": {"falsos": 1}, "5": {"confirmados": 0, "falsos": 0}}), encoding="utf-8")
            gh = Fake(self.prs(), {**DADOS, 9: (0, [], [])})
            codigo, out, _ = rodar(gh, "--lidos", str(lidos), "--aplicar")
        self.assertEqual(codigo, 0)
        fechados = sorted(int(c[2]) for c in gh.fechamentos())
        self.assertEqual(fechados, [1, 2, 3, 4, 6])
        for c in gh.fechamentos():
            self.assertEqual(c[c.index("--comment") + 1], mod.COMENTARIO)
        self.assertNotIn("merge", [c[1] for c in gh.chamadas if c[0] == "pr"])  # nunca mescla
        self.assertNotIn(REPO, mod.COMENTARIO)

    def test_idade_minima(self) -> None:
        gh = Fake([pr(2, horas_atras=1)], DADOS)
        codigo, out, _ = rodar(gh, "--aplicar")
        self.assertEqual(codigo, 0)
        self.assertEqual(gh.fechamentos(), [])
        self.assertIn("mínimo", out)

    def test_lidos_lista_ou_objeto_e_entrada_torta(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            lista = Path(d) / "l.json"
            lista.write_text("[1, \"6\", \"x\"]", encoding="utf-8")
            self.assertEqual(mod.ler_lidos(lista), {1, 6})
            registro = Path(d) / "g.json"
            registro.write_text('{"1": {"confirmados": 2}, "2": {}, "3": {"confirmados": 0, "falsos": 0}, "4": {"falsos": 1}}', encoding="utf-8")
            self.assertEqual(mod.ler_lidos(registro), {1, 4})  # PR sem nada registrado não conta como lido
            ruim = Path(d) / "r.json"
            ruim.write_text("{nao", encoding="utf-8")
            self.assertEqual(rodar(Fake([], {}), "--lidos", str(ruim))[0], 1)
        self.assertEqual(rodar(Fake([], {}), "--horas-minimas", "-1")[0], 1)
        self.assertEqual(rodar(Fake([], {}), "--horas-minimas", "nan")[0], 1)

    def test_repo_torto_e_erro_do_gh_saem_com_1_sem_repo_na_mensagem(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(mod.main(["--repo", "../x"], gh=Fake([], {}), agora=AGORA), 1)
        codigo, _, erro = rodar(Fake([pr(1)], DADOS, falha_em="reviews"))
        self.assertEqual(codigo, 1)
        self.assertNotIn(REPO, erro)

    def test_falha_ao_fechar_para_e_sai_com_1(self) -> None:
        gh = Fake([pr(2), pr(3)], DADOS, falha_em="pr close")
        self.assertEqual(rodar(gh, "--aplicar")[0], 1)


if __name__ == "__main__":
    unittest.main()
