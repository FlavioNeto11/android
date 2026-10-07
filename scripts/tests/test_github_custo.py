"""scripts/github_custo.py (29.178): relatório de custo do GitHub, só leitura. Prova `simulated`: `gh` falso, relógio fixo."""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("custo", ROOT / "scripts" / "github_custo.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]

AGORA = datetime(2026, 10, 7, 6, 0, tzinfo=timezone.utc)


def job(inicio: str, fim: str, labels=("ubuntu-latest",), conclusao="success") -> dict:
    return {"labels": list(labels), "started_at": inicio, "completed_at": fim, "conclusion": conclusao}


RUNS = [
    {"id": 1, "name": "CI", "event": "schedule", "conclusion": "failure", "created_at": "2026-10-06T05:17:00Z"},
    {"id": 2, "name": "CI leve do PR", "event": "pull_request", "conclusion": "success", "created_at": "2026-10-06T22:30:00Z"},
    {"id": 3, "name": "Running Copilot Code Review", "event": "dynamic", "conclusion": "success", "created_at": "2026-10-06T10:00:00Z"},
    {"id": 4, "name": "Running Copilot cloud agent", "event": "dynamic", "conclusion": "success", "created_at": "2026-10-06T11:00:00Z"},
    {"id": 5, "name": "CI", "event": "schedule", "conclusion": "success", "created_at": "2026-09-01T05:17:00Z"},  # fora da janela
]
JOBS = {
    1: [job("2026-10-06T05:17:00Z", "2026-10-06T05:20:01Z"),                                  # 3 min 1 s -> 4
        job("2026-10-06T05:17:00Z", "2026-10-06T06:17:00Z", ("self-hosted", "central"))],     # 60 min no central
    2: [job("2026-10-06T22:30:12Z", "2026-10-06T22:33:17Z"), job("2026-10-06T22:30:12Z", "2026-10-06T22:33:34Z")],  # 4 + 4
    3: [job("2026-10-06T10:00:00Z", "2026-10-06T10:00:20Z")],                                  # 20 s -> 1
    4: [job("2026-10-06T11:00:00Z", "2026-10-06T11:00:00Z"), job("x", "y"), job("2026-10-06T11:00:00Z", "2026-10-06T11:02:00Z", conclusao="skipped")],
}


class FakeGh:
    def __init__(self, billing_ok=False, falha=False):
        self.billing_ok, self.falha, self.chamadas = billing_ok, falha, []

    def __call__(self, *args: str) -> str:
        self.chamadas.append(args)
        if self.falha:
            raise RuntimeError("o gh falhou (código 1)")
        if args[:2] == ("run", "view"):
            if args[2] == "2":  # o run do CI leve tem resumo; o do CI (1) é de antes do 29.184 e não tem
                return ("2026-10-06T22:33:00Z **docs-check + scripts/tests**: a 4 s · soma das etapas 188 s · ~3 min cobrados · pytest 820 passed, 11 skipped\n"
                        "2026-10-06T22:33:01Z **frontend · typecheck + vitest + build**: b 3 s · soma das etapas 190 s · ~4 min cobrados · vitest 1941 passed\n")
            return "log sem resumo\n"
        rota = args[1] if args[1] != "--paginate" else args[2]
        if "/settings/billing/" in rota:
            if self.billing_ok:
                return "{}"
            raise RuntimeError("o gh falhou (código 1)")
        if "/actions/runs?" in rota:
            return "".join(json.dumps(r) for r in RUNS)  # objetos colados, como o --jq faz
        n = int(rota.split("/actions/runs/")[1].split("/")[0])
        return "".join(json.dumps(j) for j in JOBS.get(n, []))


def rodar(gh, *extra):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        codigo = mod.main(["--repo", "dono/repo", *extra], gh=gh, agora=AGORA)
    return codigo, out.getvalue(), err.getvalue()


class Custo(unittest.TestCase):
    def test_minutos_do_job_arredonda_para_cima_e_ignora_o_que_nao_rodou(self) -> None:
        self.assertEqual(mod.minutos_do_job(job("2026-10-06T05:17:00Z", "2026-10-06T05:20:01Z")), 4)
        self.assertEqual(mod.minutos_do_job(job("2026-10-06T10:00:00Z", "2026-10-06T10:00:20Z")), 1)
        self.assertEqual(mod.minutos_do_job(job("2026-10-06T11:00:00Z", "2026-10-06T11:00:00Z")), 0)
        self.assertEqual(mod.minutos_do_job(job("x", "y")), 0)
        self.assertEqual(mod.minutos_do_job(job("2026-10-06T11:00:00Z", "2026-10-06T11:02:00Z", conclusao="skipped")), 0)

    def test_separa_hospedado_de_central(self) -> None:
        por, lidos = mod.coletar("dono/repo", datetime(2026, 9, 30, tzinfo=timezone.utc), FakeGh())
        self.assertEqual(lidos, 4)  # o run de setembro sai
        self.assertEqual((por["CI"]["min_hospedados"], por["CI"]["min_central"], por["CI"]["jobs_central"]), (4, 60, 1))
        self.assertEqual(por["Running Copilot cloud agent"].get("jobs_hospedados", 0), 0)  # job sem hora e skipped não contam
        self.assertEqual((por["CI"]["runs"], por["CI"]["falhas"]), (1, 1))
        self.assertEqual(por["CI leve do PR"]["min_hospedados"], 8)

    def test_relatorio_tem_totais_creditos_estimados_e_limites(self) -> None:
        _, out, _ = rodar(FakeGh())
        self.assertIn("Minutos hospedados FATURÁVEIS (estimativa", out)
        self.assertIn("1 revisão(ões) x 146 + 1 tarefa(s) do agente x 31 = 177", out)
        self.assertIn("Codex: sem API de uso", out)
        self.assertIn("NÃO lido: a API de billing não respondeu", out)
        self.assertIn("CI leve do PR: 8.0 min faturáveis estimados por run", out)

    def test_billing_lido_quando_a_api_deixa(self) -> None:
        self.assertIn("NÃO imprime os números do billing", rodar(FakeGh(billing_ok=True))[1])

    def test_saida_sem_nome_de_runner(self) -> None:
        _, out, _ = rodar(FakeGh())
        self.assertNotIn("central", out)
        self.assertIn("self-hosted", out)

    def test_erro_do_gh_real_nao_imprime_o_nome_do_repo(self) -> None:
        import subprocess
        from unittest import mock
        falso = subprocess.CompletedProcess(["gh"], 1, stdout="", stderr="HTTP 404: repos/dono/segredo-repo/actions/runs")
        with mock.patch.object(mod.subprocess, "run", return_value=falso):
            with self.assertRaises(RuntimeError) as c:
                mod.gh_real("api", "repos/dono/segredo-repo/actions/runs")
        self.assertNotIn("segredo-repo", str(c.exception))
        self.assertNotIn("dono", str(c.exception))

    def test_truncamento_diz_que_os_antigos_ficaram_fora(self) -> None:
        texto = mod.relatorio({}, mod.MAX_RUNS, AGORA, 7, "x")
        self.assertIn("TRUNCADO", texto)
        self.assertIn("MAIS ANTIGOS", texto)
        self.assertNotIn("TRUNCADO", mod.relatorio({}, 10, AGORA, 7, "x"))

    def test_url_dos_runs_filtra_por_dia_e_jobs_pedem_todas_as_tentativas(self) -> None:
        gh = FakeGh()
        rodar(gh)
        rotas = [c[2] if c[1] == "--paginate" else c[1] for c in gh.chamadas]
        self.assertTrue(any("created=%3E%3D2026-09-30" in r for r in rotas))
        self.assertTrue(all("filter=all" in r for r in rotas if r.endswith("/jobs?per_page=100&filter=all") or "/jobs?" in r))

    def test_anexar_em_pasta_inexistente_sai_com_1(self) -> None:
        arq = Path(tempfile.mkdtemp()) / "nao-existe" / "custo.md"
        codigo, _, err = rodar(FakeGh(), "--anexar", str(arq))
        self.assertEqual(codigo, 1)
        self.assertIn("não foi possível anexar", err)

    def test_resumos_da_semana_somam_corridas_cobrados_e_maior_contagem(self) -> None:
        por, com_resumo, lidos = mod.resumos_da_semana("dono/repo", datetime(2026, 9, 30, tzinfo=timezone.utc), FakeGh())
        self.assertEqual((com_resumo, lidos), (1, 2))  # CI (1) e CI leve (2) da janela; só o 2 tem a linha
        d = por["docs-check + scripts/tests"]
        self.assertEqual((d["corridas"], d["soma_s"], d["cobrados_min"], d["testes_pytest"]), (1, 188, 3, 820))
        self.assertEqual(por["frontend · typecheck + vitest + build"]["testes_vitest"], 1941)

    def test_relatorio_dos_resumos_e_a_flag(self) -> None:
        _, out, _ = rodar(FakeGh(), "--resumos")
        self.assertIn("Resumos por corrida", out)
        self.assertIn("| docs-check + scripts/tests | 1 | 188 | 3 | pytest 820 |", out)
        self.assertIn("com resumo: 1", out)
        self.assertNotIn("Resumos por corrida", rodar(FakeGh())[1])

    def test_log_que_falha_nao_derruba_o_relatorio(self) -> None:
        class SemLog(FakeGh):
            def __call__(self, *args, **kw):
                if args[:2] == ("run", "view"):
                    raise RuntimeError("o gh falhou (código 1)")
                return super().__call__(*args, **kw)
        _, com_resumo, lidos = mod.resumos_da_semana("dono/repo", datetime(2026, 9, 30, tzinfo=timezone.utc), SemLog())
        self.assertEqual((com_resumo, lidos), (0, 2))

    def test_texto_nao_leva_nome_de_conta_nem_repo(self) -> None:
        _, out, _ = rodar(FakeGh())
        self.assertNotIn("dono", out.replace("do dono", "").replace("dono)", ""))
        self.assertNotIn("repo", out.lower().replace("repositório", "").replace("repo)", ""))

    def test_so_le_nunca_escreve(self) -> None:
        gh = FakeGh()
        rodar(gh)
        for c in gh.chamadas:
            self.assertEqual(c[0], "api")
            for escrita in ("-X", "--method", "-f", "-F", "--field", "--raw-field", "--input"):
                self.assertNotIn(escrita, c)

    def test_anexar_acrescenta_e_nao_sobrescreve(self) -> None:
        arq = Path(tempfile.mkdtemp()) / "custo.md"
        arq.write_text("# Antes\nlinha antiga\n", encoding="utf-8")
        self.assertEqual(rodar(FakeGh(), "--anexar", str(arq))[0], 0)
        texto = arq.read_text(encoding="utf-8")
        self.assertTrue(texto.startswith("# Antes\nlinha antiga\n"))
        self.assertIn("## Custo do GitHub: janela de 7 dia(s) até 2026-10-07 06:00Z", texto)

    def test_falha_da_api_sai_com_1_e_nao_anexa(self) -> None:
        arq = Path(tempfile.mkdtemp()) / "custo.md"
        self.assertEqual(rodar(FakeGh(falha=True), "--anexar", str(arq))[0], 1)
        self.assertFalse(arq.exists())

    def test_argumentos_invalidos(self) -> None:
        self.assertEqual(rodar(FakeGh(), "--dias", "0")[0], 1)
        self.assertEqual(rodar(FakeGh(), "--dias", "99")[0], 1)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(mod.main(["--repo", "x y"], gh=FakeGh(), agora=AGORA), 1)


if __name__ == "__main__":
    unittest.main()
