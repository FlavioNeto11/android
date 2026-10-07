"""scripts/coletar_achados_revisao.py (29.170): só lê, filtra revisor automático, marca gravidade e mascara por formato.
Prova `simulated`: `gh` falso."""
from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("coletor", ROOT / "scripts" / "coletar_achados_revisao.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]

AGORA = datetime(2026, 10, 6, 22, 0, tzinfo=timezone.utc)
COMENTARIOS = {
    10: [
        {"user": {"login": "chatgpt-codex-connector[bot]"}, "path": "a.py", "line": 7,
         "html_url": "https://github.com/dono/repo/pull/10#discussion_r1",
         "body": "![P1 Badge](https://x/p1.svg) **Apaga a branch errada**\n\nmais detalhe"},
        {"user": {"login": "pessoa"}, "path": "b.py", "line": 1, "body": "P1 comentario humano"},
        {"user": {"login": "Copilot"}, "path": "c.py", "line": None, "original_line": 3, "body": "Sem marca de gravidade",
         "html_url": "https://evil.example/x"},
    ],
    11: [],
}
REVISOES = {
    10: [{"user": {"login": "chatgpt-codex-connector[bot]"}, "body": "### Resumo\nP2 achado geral"}],
    11: [{"user": {"login": "copilot-pull-request-reviewer[bot]"}, "body": ""}],
}


class FakeGh:
    def __init__(self, falha: bool = False):
        self.chamadas, self.falha = [], falha

    def __call__(self, *args: str) -> str:
        self.chamadas.append(args)
        if self.falha:
            raise RuntimeError("gh api saiu com 1: simulado")
        if args[:2] == ("pr", "list"):
            return json.dumps([{"number": 10, "updatedAt": "2026-10-06T20:00:00Z"},
                               {"number": 11, "updatedAt": "2026-10-06T21:00:00Z"},
                               {"number": 5, "updatedAt": "2026-09-01T00:00:00Z"}])
        if args[:2] == ("pr", "view"):
            return json.dumps({"state": "MERGED" if args[2] == "10" else "OPEN"})
        if args[0] == "api":
            n = int(args[2].split("/pulls/")[1].split("/")[0])
            return json.dumps((COMENTARIOS if args[2].endswith("/comments") else REVISOES)[n])
        raise AssertionError(args)


def rodar(gh, *extra):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        codigo = mod.main(["--repo", "dono/repo", *extra], gh=gh, agora=AGORA)
    return codigo, out.getvalue(), err.getvalue()


class Coletor(unittest.TestCase):
    def test_so_revisor_automatico_e_so_pr_da_janela(self) -> None:
        codigo, out, _ = rodar(FakeGh(), "--json")
        achados = json.loads(out)
        self.assertEqual(codigo, 0)
        self.assertEqual(sorted((a["pr"], a["revisor"]) for a in achados),
                         [(10, "Copilot"), (10, "chatgpt-codex-connector[bot]"), (10, "chatgpt-codex-connector[bot]")])

    def test_gravidade_resumo_e_local(self) -> None:
        achados = json.loads(rodar(FakeGh(), "--json")[1])
        codex = next(a for a in achados if a["arquivo"] == "a.py")
        self.assertEqual((codex["gravidade"], codex["frase"], codex["linha"]), ("P1", "Apaga a branch errada", 7))
        self.assertEqual(next(a for a in achados if a["arquivo"] == "c.py")["gravidade"], "-")
        self.assertEqual(next(a for a in achados if a["arquivo"] == "")["gravidade"], "P2")

    def test_contrato_json_tem_id_estavel_e_so_as_chaves_combinadas(self) -> None:
        a = json.loads(rodar(FakeGh(), "--json")[1])
        b = json.loads(rodar(FakeGh(), "--json")[1])
        self.assertEqual([x["id"] for x in a], [x["id"] for x in b])
        self.assertEqual(len({x["id"] for x in a}), len(a))
        self.assertIn("10:a.py:7:chatgpt-codex-connector[bot]", [x["id"] for x in a])
        self.assertEqual(set(a[0]), {"id", "pr", "revisor", "gravidade", "arquivo", "linha", "frase", "artefato", "url", "pr_estado"})
        self.assertIsInstance(a[0]["artefato"], bool)

    def test_url_so_de_github_e_estado_do_pr(self) -> None:
        a = json.loads(rodar(FakeGh(), "--json")[1])
        self.assertEqual(next(x for x in a if x["arquivo"] == "a.py")["url"], "https://github.com/dono/repo/pull/10#discussion_r1")
        self.assertEqual(next(x for x in a if x["arquivo"] == "c.py")["url"], "")  # fora do github.com vira vazio
        self.assertEqual({x["pr_estado"] for x in a}, {"merged"})

    def test_artefato_marca_so_regra_de_conduta_de_agente(self) -> None:
        self.assertTrue(mod._ARTEFATO.search("Este hunk viola a regra explícita do repositório que proíbe agentes"))
        self.assertFalse(mod._ARTEFATO.search("Apaga a branch errada"))

    def test_frase_sem_trecho_de_codigo(self) -> None:
        txt = mod.resumo("Troque `x = foo(a, b)` por `nome_ok`\n```py\nsegredo()\n```")
        self.assertNotIn("foo(", txt)
        self.assertIn("nome_ok", txt)
        self.assertNotIn("segredo", mod.resumo("```py\nsegredo()\n```\nresto"))

    def test_tabela_ordena_por_gravidade_e_diz_a_conferir(self) -> None:
        saida = rodar(FakeGh())[1]
        self.assertIn("A CONFERIR, nunca ordem", saida)
        linhas = [x for x in saida.splitlines() if x.startswith("| 10 ")]
        self.assertEqual([x.split("|")[3].strip() for x in linhas], ["P1", "P2", "-"])

    def test_sem_achado_diz_que_nao_ha(self) -> None:
        self.assertIn("Nenhum achado", mod.tabela([]))

    def test_mascara_por_formato(self) -> None:
        txt = mod.resumo("Vaza a@b.com em 10.1.2.3 e " + "a" * 40)
        for proibido in ("a@b.com", "10.1.2.3", "a" * 40):
            self.assertNotIn(proibido, txt)

    def test_comentario_html_do_revisor_e_pulado(self) -> None:
        self.assertEqual(mod.resumo("<!-- ccr-overview-v2\n## Visao geral\ntexto"), "Visao geral")

    def test_tag_html_do_selo_do_codex_nao_vai_para_o_titulo(self) -> None:
        # 29.195: o Codex abre o corpo com <sub><sub></sub></sub> (selo de gravidade em imagem)
        self.assertEqual(mod.resumo("<sub><sub></sub></sub> Recuse colisões de lote\nmais texto"), "Recuse colisões de lote")
        self.assertNotIn("<", mod.resumo("**<sub>P1</sub> Badge** Propague a falha"))

    def test_credencial_curta_por_formato_e_mascarada(self) -> None:
        txt = mod.resumo("Loga Authorization: Bearer abc123 e senha=hunter2 e api_key: xyz")
        for proibido in ("abc123", "hunter2", "xyz"):
            self.assertNotIn(proibido, txt)

    def test_login_humano_com_copilot_no_nome_nao_e_revisor(self) -> None:
        self.assertFalse(mod.eh_revisor("copilot-fan"))
        self.assertTrue(mod.eh_revisor("Copilot"))

    def test_resumo_e_encurtado(self) -> None:
        self.assertLessEqual(len(mod.resumo("palavra " * 100)), mod.RESUMO_MAX)

    def test_so_le_nunca_escreve(self) -> None:
        gh = FakeGh()
        rodar(gh)
        for c in gh.chamadas:
            self.assertIn(c[0], ("api", "pr"))
            self.assertNotIn("-X", c)
            self.assertNotIn("--method", c)
            for escrita in ("-f", "-F", "--field", "--raw-field", "--input"):
                self.assertNotIn(escrita, c)

    def test_prs_explicitos_dispensam_a_janela(self) -> None:
        gh = FakeGh()
        rodar(gh, "--prs", "11")
        self.assertFalse(any(c[:2] == ("pr", "list") for c in gh.chamadas))

    def test_falha_do_gh_sai_com_1(self) -> None:
        self.assertEqual(rodar(FakeGh(falha=True))[0], 1)

    def test_argumentos_invalidos(self) -> None:
        self.assertEqual(rodar(FakeGh(), "--prs", "1;ls")[0], 1)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(mod.main(["--repo", "x y"], gh=FakeGh(), agora=AGORA), 1)


if __name__ == "__main__":
    unittest.main()
