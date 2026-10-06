"""scripts/github_rotina.py (29.155, C10): a leitura diária do GitHub. Prova `simulated`: `gh` falso, relógio fixo.

Protegem: o script só LÊ (nenhuma chamada de escrita do `gh`); a janela de 26 h corta runs antigos; o cron da noite é o `schedule`
do CI mais recente; a estimativa de créditos é revisões x 146 + tarefas x 31 e diz que é estimativa; falha do `gh` sai com 1.
"""
from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("github_rotina", ROOT / "scripts" / "github_rotina.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]

AGORA = datetime(2026, 10, 6, 6, 0, tzinfo=timezone.utc)
REPO = "dono/repo"


def run(id_: int, nome: str, evento: str, conclusao: str | None, criado: str, fim: str | None = None) -> dict[str, object]:
    return {"id": id_, "name": nome, "event": evento, "conclusion": conclusao, "status": "completed",
            "created_at": criado, "run_started_at": criado, "updated_at": fim or criado}


RUNS = [
    run(1, "CI", "schedule", "failure", "2026-10-06T05:17:30Z", "2026-10-06T06:00:00Z"),
    run(2, "CI", "schedule", "failure", "2026-10-05T05:17:30Z", "2026-10-05T06:00:00Z"),  # na janela de 26 h? começa 04:00Z de 05/10: sim
    run(3, "Contêiner", "push", "success", "2026-10-05T20:00:00Z"),
    run(4, "Running Copilot Code Review", "dynamic", "success", "2026-10-05T15:40:00Z"),
    run(5, "Running Copilot cloud agent", "dynamic", "success", "2026-10-05T23:39:00Z"),
    run(6, "Running Copilot cloud agent", "dynamic", "success", "2026-10-06T01:38:00Z"),
    run(7, "CI", "schedule", "success", "2026-10-03T05:17:30Z"),  # fora da janela
]
RUNNERS = [{"name": "central", "status": "online", "busy": False}]


class FakeGh:
    def __init__(self, falha: bool = False):
        self.falha, self.chamadas = falha, []

    def __call__(self, *args: str) -> str:
        self.chamadas.append(args)
        if self.falha:
            raise RuntimeError("gh api saiu com 1: simulado")
        if args[0] == "api" and "/runners" in args[1]:
            return json.dumps({"runners": RUNNERS})
        if args[0] == "api":
            return json.dumps({"workflow_runs": RUNS, "total_count": len(RUNS)})
        if args[:2] == ("issue", "list"):
            return json.dumps([{"number": 466, "title": "x"}] if "ci" in args else [])
        if args[:2] == ("pr", "list"):
            return json.dumps([{"number": 465, "state": "MERGED", "isDraft": False}, {"number": 470, "state": "OPEN", "isDraft": True}])
        return "[]"


class Resumo(unittest.TestCase):
    def test_janela_de_26h_corta_o_que_e_antigo(self) -> None:
        r = mod.resumir(RUNS, [], [], [], RUNNERS, AGORA)
        self.assertEqual(r["runs"], 6)  # o run 7, de 03/10, fica de fora

    def test_cron_da_noite_e_o_schedule_mais_recente(self) -> None:
        r = mod.resumir(RUNS, [], [], [], RUNNERS, AGORA)
        self.assertEqual(r["cron"]["id"], 1)
        self.assertEqual((r["cron"]["conclusao"], r["cron"]["minutos"], r["cron"]["data"]), ("failure", 42, "2026-10-06"))

    def test_sem_cron_na_janela_diz_que_nao_houve(self) -> None:
        r = mod.resumir([x for x in RUNS if x["event"] != "schedule"], [], [], [], RUNNERS, AGORA)
        self.assertIsNone(r["cron"])
        self.assertIn("sem corrida na janela", mod.linha(r, AGORA))

    def test_creditos_sao_estimativa_de_revisoes_e_tarefas(self) -> None:
        r = mod.resumir(RUNS, [], [], [], RUNNERS, AGORA)
        self.assertEqual((r["revisoes_copilot"], r["tarefas_do_agente"]), (1, 2))
        self.assertEqual(r["creditos_estimados"], 146 + 2 * 31)
        self.assertIn("ESTIMADOS", mod.linha(r, AGORA))

    def test_runs_ruins_so_do_ci_e_do_conteiner(self) -> None:
        r = mod.resumir(RUNS, [], [], [], RUNNERS, AGORA)
        self.assertEqual([x["id"] for x in r["runs_ruins"]], [1, 2])

    def test_runner_ausente_nao_vira_online(self) -> None:
        r = mod.resumir(RUNS, [], [], [], [], AGORA)
        self.assertIsNone(r["runner_central"])
        self.assertIn("NÃO listado", mod.linha(r, AGORA))


class Truncamento(unittest.TestCase):
    def test_mais_runs_que_a_pagina_marca_truncado(self) -> None:
        r = mod.resumir(RUNS, [], [], [], RUNNERS, AGORA, total_runs=250)
        self.assertTrue(r["truncado"])
        self.assertIn("TRUNCADO (7 de 250", mod.linha(r, AGORA))

    def test_pagina_completa_nao_marca(self) -> None:
        r = mod.resumir(RUNS, [], [], [], RUNNERS, AGORA, total_runs=len(RUNS))
        self.assertFalse(r["truncado"])
        self.assertNotIn("TRUNCADO", mod.linha(r, AGORA))

    def test_run_sem_hora_legivel_fica_fora_da_janela(self) -> None:
        sem_hora = {**RUNS[0], "id": 99, "created_at": "nao-e-hora"}
        self.assertEqual(mod.resumir([sem_hora], [], [], [], RUNNERS, AGORA)["runs"], 0)


class Execucao(unittest.TestCase):
    def _main(self, gh: FakeGh, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            codigo = mod.main(["--repo", REPO, *args], gh=gh, agora=AGORA)
        return codigo, out.getvalue(), err.getvalue()

    def test_so_le_nunca_escreve(self) -> None:
        gh = FakeGh()
        codigo, saida, _ = self._main(gh)
        self.assertEqual(codigo, 0)
        for a in gh.chamadas:
            self.assertIn(a[0], ("api", "issue", "pr"))
            self.assertNotIn(a[1] if a[0] != "api" else "", ("create", "comment", "close", "edit"))
            if a[0] == "api":
                self.assertNotIn("-X", a)
                self.assertNotIn("--method", a)
                for escrita in ("-f", "-F", "--field", "--raw-field", "--input"):  # qualquer um deles faz o gh api virar POST
                    self.assertNotIn(escrita, a)
        self.assertIn("issues ci abertas: 1", saida)
        self.assertIn("PRs do agente abertos: 1", saida)

    def test_json_traz_os_mesmos_dados(self) -> None:
        codigo, saida, _ = self._main(FakeGh(), "--json")
        self.assertEqual(codigo, 0)
        self.assertEqual(json.loads(saida)["tarefas_do_agente"], 2)

    def test_falha_do_gh_sai_com_1(self) -> None:
        codigo, _, err = self._main(FakeGh(falha=True))
        self.assertEqual(codigo, 1)
        self.assertIn("erro:", err)

    def test_repo_ausente_ou_fora_do_formato(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(mod.main(["--repo", "x; ls"], gh=FakeGh(), agora=AGORA), 1)


if __name__ == "__main__":
    unittest.main()
