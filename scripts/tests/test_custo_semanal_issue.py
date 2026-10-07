"""scripts/custo_semanal_issue.py e .github/workflows/custo-semanal.yml (29.188). Prova `simulated`: `gh` falso e logs gravados."""
from __future__ import annotations

import importlib.util
import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("custo_issue", ROOT / "scripts" / "custo_semanal_issue.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]
_spec2 = importlib.util.spec_from_file_location("custo", ROOT / "scripts" / "github_custo.py")
custo = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(custo)  # type: ignore[union-attr]
WORKFLOW = (ROOT / ".github" / "workflows" / "custo-semanal.yml").read_text(encoding="utf-8")

REPO = "dono/repo"
RELATORIO = (
    "## Custo do GitHub: janela de 7 dia(s) até 2026-10-12 06:00Z\n\n"
    "Minutos hospedados FATURÁVEIS (estimativa): 412\n"
    "| docs-check + scripts/tests | 3 | 188 | 9 | pytest 820 |\n"
)


class Fake:
    def __init__(self, abertas=None):
        self.abertas, self.chamadas = abertas or [], []

    def __call__(self, *args: str, entrada: str | None = None) -> str:
        self.chamadas.append((args, entrada))
        if args[:2] == ("issue", "list"):
            return json.dumps(self.abertas)
        if args[:2] == ("issue", "create"):
            return "https://github.com/dono/repo/issues/950\n"
        return ""

    def escritas(self):
        return [a for a, _ in self.chamadas if a[:2] in (("issue", "create"), ("issue", "comment"), ("label", "create"))]


class Publicar(unittest.TestCase):
    def test_sem_issue_abre_com_titulo_fixo_e_rotulo_custo(self) -> None:
        gh = Fake()
        self.assertEqual(mod.publicar(REPO, RELATORIO, ensaio=False, gh=gh), "aberta 950")
        args, corpo = [(a, e) for a, e in gh.chamadas if a[:2] == ("issue", "create")][0]
        self.assertEqual(args[args.index("--title") + 1], mod.TITULO)
        self.assertEqual(args[args.index("--label") + 1], "custo")
        self.assertEqual(corpo, RELATORIO)

    def test_com_issue_aberta_comenta_na_mais_antiga_so_do_titulo_fixo(self) -> None:
        gh = Fake([{"number": 960, "title": mod.TITULO}, {"number": 940, "title": mod.TITULO}, {"number": 930, "title": "Outra do custo"}])
        self.assertEqual(mod.publicar(REPO, RELATORIO, ensaio=False, gh=gh), "comentada 940")
        self.assertFalse([a for a, _ in gh.chamadas if a[:2] == ("issue", "create")])

    def test_issue_fechada_ou_de_outro_titulo_nao_e_reaproveitada(self) -> None:
        gh = Fake([{"number": 930, "title": "Outra do custo"}])
        self.assertEqual(mod.publicar(REPO, RELATORIO, ensaio=False, gh=gh), "aberta 950")

    def test_nunca_atribui_nem_usa_rotulo_de_agente(self) -> None:
        gh = Fake()
        mod.publicar(REPO, RELATORIO, ensaio=False, gh=gh)
        for args, _ in gh.chamadas:
            for proibido in ("--assignee", "--add-assignee", "agente", "@copilot"):
                self.assertNotIn(proibido, args)

    def test_ensaio_nao_escreve(self) -> None:
        gh = Fake()
        self.assertEqual(mod.publicar(REPO, RELATORIO, ensaio=True, gh=gh), "ensaio")
        self.assertEqual(gh.escritas(), [])

    def test_formato_proibido_ou_vazio_nao_publica(self) -> None:
        for sujo in ("fulano.da.silva@exemplo.invalid", "ip 10.0.0.9", "emulator-5554", "@alguem", "token=abc123", "ghp_" + "a" * 20, "  \n"):
            gh = Fake()
            with self.assertRaises(ValueError, msg=sujo):
                mod.publicar(REPO, RELATORIO + sujo, ensaio=False, gh=gh) if sujo.strip() else mod.publicar(REPO, sujo, ensaio=False, gh=gh)
            self.assertEqual(gh.escritas(), [])

    def test_corpo_gigante_e_cortado_no_limite(self) -> None:
        gh = Fake()
        mod.publicar(REPO, RELATORIO + "x " * 100_000, ensaio=False, gh=gh)
        corpo = [e for a, e in gh.chamadas if a[:2] == ("issue", "create")][0] or ""
        self.assertLessEqual(len(corpo), mod.LIMITE_CORPO)
        self.assertIn("cortado", corpo)

    def test_erro_do_gh_sai_com_1(self) -> None:
        def falha(*a, entrada=None):
            raise RuntimeError("gh issue list saiu com 1: simulado")
        arq = Path(tempfile.mkdtemp()) / "r.md"
        arq.write_text(RELATORIO, encoding="utf-8")
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(mod.main(["--repo", REPO, "--arquivo", str(arq)], gh=falha), 1)
        self.assertIn("erro:", err.getvalue())

    def test_arquivo_ausente_e_repo_invalido_saem_com_1(self) -> None:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(mod.main(["--repo", REPO, "--arquivo", str(Path(tempfile.mkdtemp()) / "nao-existe.md")], gh=Fake()), 1)
            self.assertEqual(mod.main(["--repo", "x y", "--arquivo", "a"], gh=Fake()), 1)

    def test_relatorio_real_do_github_custo_passa_na_checagem(self) -> None:
        """Log gravado: a saída de verdade do github_custo (sem billing, com resumos) não tem formato proibido."""
        texto = custo.relatorio({}, 0, custo.datetime(2026, 10, 12, 6, 0, tzinfo=custo.timezone.utc), 7, "NÃO lido: a API de billing não respondeu")
        texto += custo.relatorio_resumos({"docs-check + scripts/tests": {"corridas": 1, "soma_s": 188, "cobrados_min": 3, "testes_pytest": 820}}, 1, 2)
        self.assertEqual(mod.proibidos(texto), [])


class NomeDeJob(unittest.TestCase):
    def test_nome_vindo_do_log_nao_vira_link_imagem_html_nem_mencao(self) -> None:
        feio = "job #12 ![x](http://x.invalid/a.png) <b>@alguem</b> `y` | z"
        texto = custo.relatorio_resumos({feio: {"corridas": 1, "soma_s": 5, "cobrados_min": 1}}, 1, 1)
        linha = [x for x in texto.splitlines() if x.startswith("| job ")][0]
        for proibido in ("#", "![", "](", "<", "@", "`"):
            self.assertNotIn(proibido, linha)
        self.assertEqual(linha.count("|"), 6)  # a barra do nome não abre coluna nova

    def test_erro_do_gh_real_nao_leva_o_texto_do_gh(self) -> None:
        import subprocess
        from unittest import mock
        falso = subprocess.CompletedProcess(["gh"], 1, stdout="", stderr="HTTP 404: repos/dono/segredo-repo/issues")
        with mock.patch.object(mod.subprocess, "run", return_value=falso):
            with self.assertRaises(RuntimeError) as c:
                mod.gh_real("issue", "list")
        self.assertNotIn("segredo-repo", str(c.exception))


class Workflow(unittest.TestCase):
    def test_roda_so_na_segunda_06z_e_manual_em_runner_hospedado(self) -> None:
        self.assertIn('cron: "0 6 * * 1"', WORKFLOW)
        self.assertEqual(len(re.findall(r"cron:", WORKFLOW)), 1)
        self.assertIn("runs-on: ubuntu-latest", WORKFLOW)
        codigo = "\n".join(x for x in WORKFLOW.splitlines() if not x.lstrip().startswith("#"))  # o cabeçalho pode citar o que NÃO fazer
        for proibido in ("CI_RUNS_ON", "self-hosted", "pull_request", "push:"):
            self.assertNotIn(proibido, codigo)

    def test_permissoes_minimas_e_acoes_fixadas_por_sha(self) -> None:
        self.assertIn("contents: read", WORKFLOW)
        self.assertIn("issues: write", WORKFLOW)
        self.assertNotIn("write-all", WORKFLOW)
        for uso in re.findall(r"uses: (\S+)", WORKFLOW):
            self.assertRegex(uso, r"@[0-9a-f]{40}$")

    def test_custo_por_pr_entra_no_relatorio_antes_da_conferencia_e_nao_some_em_silencio(self) -> None:
        """29.207: o passo roda depois do relatório e antes da checagem de formato; a falha vira 'NÃO lido', nunca omissão."""
        passo = WORKFLOW.index("- name: custo por PR")
        self.assertLess(WORKFLOW.index("github_custo.py"), passo)
        self.assertLess(passo, WORKFLOW.index("--arquivo relatorio-custo.md --ensaio"))
        corpo = WORKFLOW[passo:WORKFLOW.index("# Confere o formato")]
        self.assertIn("custo_por_pr.py", corpo)
        self.assertIn("NÃO lido nesta semana", corpo)
        self.assertIn("pull-requests: read", WORKFLOW)
        self.assertNotIn("pull-requests: write", WORKFLOW)
        self.assertNotIn("--aplicar", corpo)  # só leitura

    def test_artifact_antes_de_publicar_e_sem_atribuicao(self) -> None:
        conferir = WORKFLOW.index("--arquivo relatorio-custo.md --ensaio")
        self.assertLess(conferir, WORKFLOW.index("GITHUB_STEP_SUMMARY"))  # checagem de formato antes de summary e artifact
        self.assertLess(conferir, WORKFLOW.index("upload-artifact"))
        self.assertLess(WORKFLOW.index("upload-artifact"), WORKFLOW.index("--arquivo relatorio-custo.md\n"))  # artifact antes de publicar
        self.assertNotIn("assignee", WORKFLOW)
        self.assertIn("--resumos", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
