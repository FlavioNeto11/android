"""scripts/custo_por_pr.py (29.202): custo por PR (Copilot estimado, Codex contado). Prova `simulated`: `gh` falso."""
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
_spec = importlib.util.spec_from_file_location("custo_por_pr", ROOT / "scripts" / "custo_por_pr.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]

REPO = "dono/repo"
AGORA = datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc)


def iso(minutos_atras):
    return (AGORA - timedelta(minutes=minutos_atras)).strftime("%Y-%m-%dT%H:%M:%SZ")


PRS = [
    {"number": 10, "title": "[revisão] 31.1 segredo-no-titulo", "headRefName": "feat/x", "createdAt": iso(30)},
    {"number": 11, "title": "docs: algo", "headRefName": "copilot/tarefa", "createdAt": iso(60)},
    {"number": 12, "title": "feat: outra", "headRefName": "feat/y", "createdAt": iso(90)},
    {"number": 13, "title": "antigo", "headRefName": "feat/z", "createdAt": iso(60 * 24 * 30)},  # fora da janela
]


def run(nome, inicio_min_atras, dur_min, prs, branch=""):
    return {"name": nome, "head_branch": branch, "run_started_at": iso(inicio_min_atras), "updated_at": iso(inicio_min_atras - dur_min), "pull_requests": prs}


RUNS = [
    run("Running Copilot Code Review", 29, 5, [10]),
    run("Running Copilot cloud agent", 59, 12, [11]),
    run("Running Copilot Code Review", 58, 4, [11]),
    run("CI leve do PR", 28, 3, [10, 12]),
    run("Running Copilot Code Review", 25, 3, [], "feat/y"),  # dynamic: sem pull_requests, o elo é a branch (PR 12)
    run("Running Copilot Code Review", 20, 3, [], "branch-sem-pr"),  # branch desconhecida: some
    run("CI", 20, 9, []),                      # sem PR
    run("CI leve do PR", 10, 2, [999]),       # PR fora da janela
]
CODEX = {"login": "chatgpt-codex-connector[bot]"}


class Fake:
    def __init__(self, falha_em=None):
        self.falha_em, self.chamadas = falha_em, []

    def __call__(self, *args: str) -> str:
        self.chamadas.append(args)
        if self.falha_em and self.falha_em in " ".join(args):
            raise RuntimeError("o gh falhou")
        if args[:2] == ("pr", "list"):
            return json.dumps(PRS)
        if "actions/runs" in args[-3]:
            return "".join(json.dumps(r) for r in RUNS)
        if args[-3].endswith("/reviews"):
            n = int(args[-3].split("/pulls/")[1].split("/")[0])
            return "".join(json.dumps(x) for x in ([{"login": CODEX["login"]}] * 2 if n == 10 else [{"login": "fulano"}] if n == 12 else []))
        return ""


def rodar(gh, *extra):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        codigo = mod.main(["--repo", REPO, *extra], gh=gh, agora=AGORA)
    return codigo, out.getvalue(), err.getvalue()


class Calculo(unittest.TestCase):
    def test_tipo_do_pr_e_da_execucao(self) -> None:
        self.assertEqual(mod.tipo_do_pr("[revisão] 31.1 x", "feat/x"), "revisão")
        self.assertEqual(mod.tipo_do_pr("docs: x", "copilot/a"), "agente")
        self.assertEqual(mod.tipo_do_pr("feat: x", "feat/a"), "sessão")
        self.assertEqual(mod.tipo_da_execucao("Running Copilot Code Review"), "revisao_copilot")
        self.assertEqual(mod.tipo_da_execucao("Running Copilot cloud agent"), "tarefa_agente")
        self.assertEqual(mod.tipo_da_execucao("CI"), "outra")

    def test_minutos_por_duracao_arredondada_e_zero_sem_hora(self) -> None:
        self.assertEqual(mod.minutos({"run_started_at": "2026-10-07T10:00:00Z", "updated_at": "2026-10-07T10:04:20Z"}), 4)
        self.assertEqual(mod.minutos({"run_started_at": "2026-10-07T10:00:00Z", "updated_at": "2026-10-07T10:00:05Z"}), 1)
        self.assertEqual(mod.minutos({"run_started_at": None, "updated_at": "x"}), 0)

    def test_pico_por_janela_deslizante(self) -> None:
        h = [AGORA - timedelta(minutes=m) for m in (0, 30, 60, 290, 299, 301, 600)]
        self.assertEqual(mod.pico_por_janela(h, 5), 5)  # 0, 30, 60, 290, 299 minutos atrás cabem em 5 h (300 min)
        self.assertEqual(mod.pico_por_janela([], 5), 0)

    def test_coleta_liga_execucoes_ao_pr_e_ignora_o_que_esta_fora(self) -> None:
        linhas = mod.coletar(REPO, AGORA - timedelta(days=7), True, Fake())
        self.assertEqual(sorted(linhas), [10, 11, 12])  # o 13 é antigo; a execução do PR 999 some
        self.assertEqual((linhas[10]["execucoes"], linhas[10]["revisoes_copilot"], linhas[10]["codex"]), (2, 1, 2))
        self.assertEqual((linhas[11]["revisoes_copilot"], linhas[11]["tarefas_agente"], linhas[11]["min"]), (1, 1, 16))
        self.assertEqual(linhas[12]["codex"], 0)  # a revisão de gente não conta como do Codex
        self.assertEqual((linhas[12]["revisoes_copilot"], linhas[12]["execucoes"]), (1, 2))  # a execução dynamic entrou pela branch

    def test_execucao_dynamic_vai_para_o_pr_mais_recente_criado_antes(self) -> None:
        por = {"b": [(AGORA - timedelta(hours=9), 1), (AGORA - timedelta(hours=2), 2)]}
        depois = {"head_branch": "b", "run_started_at": iso(60), "pull_requests": []}
        antes = {"head_branch": "b", "run_started_at": iso(60 * 6), "pull_requests": []}
        antigo = {"head_branch": "b", "run_started_at": iso(60 * 10), "pull_requests": []}
        self.assertEqual(mod._prs_da_execucao(depois, por), [2])
        self.assertEqual(mod._prs_da_execucao(antes, por), [1])
        self.assertEqual(mod._prs_da_execucao(antigo, por), [])  # nenhum PR da janela anterior à execução: não é atribuída
        self.assertEqual(mod._prs_da_execucao({"head_branch": "b", "pull_requests": []}, por), [])  # sem hora, não adivinha
        self.assertEqual(mod._prs_da_execucao({"head_branch": "x", "pull_requests": [7]}, por), [7])


class Relatorio(unittest.TestCase):
    def test_creditos_estimados_totais_e_pico(self) -> None:
        codigo, out, _ = rodar(Fake())
        self.assertEqual(codigo, 0)
        self.assertIn("~469", out)  # PR 10: 146; PR 11: 146 + 31; PR 12 (dynamic pela branch): 146
        self.assertIn("~177 créditos", out)  # o tipo agente (PR 11)
        self.assertIn("dentro da regra", out)

    def test_nao_imprime_titulo_nem_branch_nem_repo(self) -> None:
        _, out, _ = rodar(Fake())
        for proibido in ("segredo-no-titulo", "copilot/tarefa", "feat/x", REPO, "dono/"):
            self.assertNotIn(proibido, out)

    def test_sem_codex_nao_le_as_revisoes(self) -> None:
        gh = Fake()
        codigo, out, _ = rodar(gh, "--sem-codex")
        self.assertEqual(codigo, 0)
        self.assertFalse([c for c in gh.chamadas if c[-3].endswith("/reviews")])
        self.assertIn("não lida", out)

    def test_pico_acima_da_regra_avisa(self) -> None:
        linhas = {n: {"pr": n, "tipo": "revisão", "criado": AGORA - timedelta(minutes=n), "execucoes": 0, "min": 0,
                      "revisoes_copilot": 0, "tarefas_agente": 0, "codex": 0} for n in range(1, 23)}
        self.assertIn("ACIMA da regra", mod.relatorio(linhas, AGORA, 7, True))


class Truncamento(unittest.TestCase):
    def test_150_prs_lidos_avisa(self) -> None:
        cheios = [{"number": n, "title": "x", "headRefName": "b", "createdAt": iso(30)} for n in range(1, mod.MAX_PRS + 1)]

        class Cheio(Fake):
            def __call__(self, *args: str) -> str:
                return json.dumps(cheios) if args[:2] == ("pr", "list") else super().__call__(*args)

        codigo, _, err = rodar(Cheio(), "--sem-codex")
        self.assertEqual(codigo, 0)
        self.assertIn("pode estar truncada", err)


class Linha(unittest.TestCase):
    def test_so_le_nunca_escreve(self) -> None:
        gh = Fake()
        rodar(gh)
        for c in gh.chamadas:
            self.assertIn(c[0], ("pr", "api"))
            self.assertNotIn("POST", c)
            self.assertNotIn("close", c)

    def test_anexar_acrescenta_sem_sobrescrever(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            arq = Path(d) / "custo.md"
            arq.write_text("antes\n", encoding="utf-8")
            self.assertEqual(rodar(Fake(), "--anexar", str(arq))[0], 0)
            texto = arq.read_text(encoding="utf-8")
        self.assertTrue(texto.startswith("antes\n"))
        self.assertIn("Custo por PR", texto)

    def test_argumentos_tortos_e_erro_do_gh_saem_com_1_sem_repo(self) -> None:
        self.assertEqual(rodar(Fake(), "--dias", "0")[0], 1)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(mod.main(["--repo", "../x"], gh=Fake(), agora=AGORA), 1)
        codigo, _, erro = rodar(Fake(falha_em="actions"))
        self.assertEqual(codigo, 1)
        self.assertNotIn(REPO, erro)


if __name__ == "__main__":
    unittest.main()
