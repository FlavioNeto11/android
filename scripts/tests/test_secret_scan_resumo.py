"""scripts/secret_scan_resumo.py (29.158): o resumo do gitleaks NUNCA deixa passar valor, contexto, autor nem e-mail.
Prova `simulated`: relatório JSON falso (os "segredos" abaixo são texto de teste que não é credencial) e `gh` falso."""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("secret_scan_resumo", ROOT / "scripts" / "secret_scan_resumo.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]

ISCA = "VALOR-DE-TESTE-NAO-E-CREDENCIAL"
EMAIL = "alguem@exemplo.invalid"


def achado(regra="generic-api-key", arquivo="backend/x.py", linha=12, commit="abcdef1234567890", secret="REDACTED") -> dict[str, object]:
    return {"RuleID": regra, "File": arquivo, "StartLine": linha, "Commit": commit, "Secret": secret,
            "Match": f"chave = {secret}", "Author": "Pessoa Teste", "Email": EMAIL, "Fingerprint": f"{commit}:{arquivo}:{regra}:{linha}"}


class FakeGh:
    def __init__(self, abertas=None):
        self.abertas, self.chamadas = abertas or [], []

    def __call__(self, *args: str, entrada: str | None = None) -> str:
        self.chamadas.append((args, entrada))
        if args[:2] == ("issue", "list"):
            return json.dumps(self.abertas)
        if args[:2] == ("issue", "create"):
            return "https://github.com/dono/repo/issues/77\n"
        return ""


def rodar(achados, *extra, gh=None, repo="dono/repo", run_id="123"):
    with tempfile.TemporaryDirectory() as d:
        arq = Path(d) / "r.json"
        arq.write_text(json.dumps(achados), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            codigo = mod.main(["--relatorio", str(arq), "--repo", repo, "--run-id", run_id, *extra], gh=gh)
    return codigo, out.getvalue(), err.getvalue()


class Resumo(unittest.TestCase):
    def test_so_campos_seguros(self) -> None:
        r = mod.resumir([achado()])
        self.assertEqual(r["itens"], [{"regra": "generic-api-key", "arquivo": "backend/x.py", "linha": 12, "commit": "abcdef1"}])
        texto = mod.texto(r, "9")
        for proibido in ("Pessoa Teste", EMAIL, "chave =", "Fingerprint"):
            self.assertNotIn(proibido, texto)

    def test_relatorio_sem_redacao_e_recusado(self) -> None:
        with self.assertRaises(mod.RelatorioSemRedacao):
            mod.resumir([achado(secret=ISCA)])

    def test_chave_secret_ausente_tambem_e_recusada(self) -> None:
        sem = achado()
        del sem["Secret"]
        with self.assertRaises(mod.RelatorioSemRedacao):
            mod.resumir([sem])

    def test_conteudo_do_match_nunca_aparece_na_saida_nem_na_issue(self) -> None:
        a = achado()
        a["Match"] = f"senha = {ISCA}"  # Secret redigido, mas o contexto trouxe um valor: o script não pode repassá-lo
        gh = FakeGh()
        codigo, out, err = rodar([a], gh=gh)
        self.assertEqual(codigo, 0)
        publico = out + err + " ".join(str(c) for c in gh.chamadas)
        self.assertNotIn(ISCA, publico)
        self.assertNotIn("senha =", publico)

    def test_nao_cria_nem_altera_rotulo(self) -> None:
        gh = FakeGh()
        rodar([achado()], gh=gh)
        self.assertFalse([c for c in gh.chamadas if c[0][0] == "label"])

    def test_sem_redacao_nao_imprime_nada_e_sai_com_2(self) -> None:
        gh = FakeGh()
        codigo, out, err = rodar([achado(secret=ISCA)], gh=gh)
        self.assertEqual(codigo, 2)
        self.assertNotIn(ISCA, out + err)
        self.assertNotIn("backend/x.py", out + err)
        self.assertEqual(gh.chamadas, [])

    def test_nomes_com_marcacao_ou_arroba_sao_omitidos(self) -> None:
        r = mod.resumir([achado(arquivo="a/<b>.py"), achado(arquivo="a/@pessoa.py"), achado(arquivo="a`b"), achado(regra="x y"),
                         achado(commit="ZZZ"), achado(linha=-3)])
        self.assertEqual([i["arquivo"] for i in r["itens"]].count("(caminho omitido)"), 3)
        self.assertEqual({i["regra"] for i in r["itens"]}, {"generic-api-key", "regra-desconhecida"})
        self.assertIn("?", {i["commit"] for i in r["itens"]})
        self.assertIn(0, {i["linha"] for i in r["itens"]})

    def test_lista_e_cortada(self) -> None:
        r = mod.resumir([achado(linha=n) for n in range(1, 80)])
        self.assertIn("mostrados 50 de 79", mod.texto(r, "1"))


class Execucao(unittest.TestCase):
    def test_zero_achados_nao_abre_issue(self) -> None:
        gh = FakeGh()
        codigo, out, _ = rodar([], gh=gh)
        self.assertEqual((codigo, gh.chamadas), (0, []))
        self.assertIn("0 achado(s)", out)

    def test_com_achados_abre_issue_com_rotulo_e_sem_dado(self) -> None:
        gh = FakeGh()
        codigo, out, _ = rodar([achado(), achado(regra="aws-access-token")], gh=gh)
        self.assertEqual(codigo, 0)
        self.assertIn("issue: aberta 77", out)
        criar = next(c for c in gh.chamadas if c[0][:2] == ("issue", "create"))
        self.assertIn("achado", criar[0])
        self.assertIn("2 achado(s)", " ".join(criar[0]))
        self.assertNotIn(EMAIL, criar[1] or "")

    def test_issue_aberta_do_scan_recebe_comentario(self) -> None:
        gh = FakeGh(abertas=[{"number": 40, "title": "Secret scan 2026-10-01: 1 achado(s)"}])
        codigo, out, _ = rodar([achado()], gh=gh)
        self.assertEqual(codigo, 0)
        self.assertIn("comentada 40", out)
        self.assertFalse([c for c in gh.chamadas if c[0][:2] == ("issue", "create")])

    def test_ensaio_nao_chama_o_gh(self) -> None:
        gh = FakeGh()
        self.assertEqual(rodar([achado()], "--ensaio", gh=gh)[0], 0)
        self.assertEqual(gh.chamadas, [])

    def test_run_id_e_repo_validados(self) -> None:
        self.assertEqual(rodar([achado()], gh=FakeGh(), run_id="1; ls")[0], 1)
        self.assertEqual(rodar([achado()], gh=FakeGh(), repo="x y")[0], 1)

    def test_relatorio_ilegivel_sai_com_1_sem_conteudo(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            arq = Path(d) / "r.json"
            arq.write_text("{não é json", encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                codigo = mod.main(["--relatorio", str(arq), "--run-id", "1"], gh=FakeGh())
        self.assertEqual(codigo, 1)
        self.assertNotIn("não é json", out.getvalue() + err.getvalue())


if __name__ == "__main__":
    unittest.main()
