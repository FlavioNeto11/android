"""scripts/ci_issue_falha.py (29.155, C2): a issue da noite em que o cron do CI não passou.

Prova `simulated`: o `gh` é um falso que grava as chamadas; nada chega ao GitHub. O que estes testes protegem:
  1. segredo e dado de pessoa não vão para a issue: a limpeza é POR FORMATO (Authorization, senha=, token do GitHub,
     chave sk-, IPv4, e-mail, pasta de usuário do Windows), e a cor ANSI some nas duas notações do log do GitHub;
  2. uma issue por noite: com uma ABERTA do mesmo dia o script comenta nela; de outro dia, ou fechada, abre outra;
  3. run que passou não abre nada, e o ensaio nunca escreve;
  4. falha do `gh` derruba o script (código 1), nunca vira "sem aviso";
  5. o log do contêiner do PostgreSQL não entra e o corte vai até o resumo do pytest.
"""
from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("ci_issue_falha", ROOT / "scripts" / "ci_issue_falha.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]

ESC = chr(27)
REPO = "dono/repo"


def _run(conclusao: str = "failure", data: str = "2026-10-05") -> dict[str, object]:
    return {
        "id": 777,
        "name": "CI",
        "conclusion": conclusao,
        "run_started_at": f"{data}T05:38:18Z",
        "html_url": "https://github.com/dono/repo/actions/runs/777",
        "run_attempt": 1,
        "head_sha": "81f99de0000000",
    }


def _jobs() -> list[dict[str, object]]:
    return [
        {
            "name": "frontend · typecheck + vitest",
            "conclusion": "failure",
            "started_at": "2026-10-05T06:42:07Z",
            "completed_at": "2026-10-05T06:44:18Z",
            "steps": [{"name": "Run npm test", "conclusion": "failure"}],
        },
        {"name": "docs · docs-check", "conclusion": "success", "steps": []},
        {
            "name": "backend · pytest (PostgreSQL)",
            "conclusion": "failure",
            "started_at": "2026-10-05T05:38:21Z",
            "completed_at": "2026-10-05T06:34:35Z",
            "steps": [{"name": "Run python -m pytest -q", "conclusion": "failure"}],
        },
    ]


LOG = "\n".join(
    [
        "frontend · typecheck + vitest\tRun npm test\t2026-10-05T06:44:12.31Z ^[[31mFAIL^[[39m src/a.test.tsx > nome",
        "frontend · typecheck + vitest\tRun npm test\t2026-10-05T06:44:12.32Z Tests  2 failed | 1596 passed (1598)",
        "backend · pytest (PostgreSQL)\tUNKNOWN STEP\t2026-10-05T06:30:00.0Z FAILED tests/test_x.py::test_a - assert 1 == 2",
        "backend · pytest (PostgreSQL)\tUNKNOWN STEP\t2026-10-05T06:30:01.0Z 1 failed, 10 passed in 3318.66s (0:55:18)",
        "backend · pytest (PostgreSQL)\tUNKNOWN STEP\t2026-10-05T06:34:33.1Z  2026-10-05 06:17:35.976 UTC [10599] ERROR:  duplicate key",
        "backend · pytest (PostgreSQL)\tUNKNOWN STEP\t2026-10-05T06:34:33.2Z  waiting for server to start",
        "outro job\tRun x\t2026-10-05T06:34:33.2Z nao deve aparecer",
    ]
)


class FakeGh:
    """Grava as chamadas; devolve respostas prontas por prefixo dos argumentos."""

    def __init__(self, run: dict[str, object], jobs: list[dict[str, object]], abertas: list[dict[str, object]] | None = None):
        self.run, self.jobs, self.abertas = run, jobs, abertas or []
        self.chamadas: list[tuple[tuple[str, ...], str | None]] = []
        self.erro_em: str | None = None

    def __call__(self, *args: str, entrada: str | None = None) -> str:
        self.chamadas.append((args, entrada))
        if self.erro_em and self.erro_em in args:
            raise RuntimeError(f"gh {args[0]} saiu com 1: simulado")
        if args[0] == "api" and args[1].endswith("/jobs?per_page=100"):
            return json.dumps({"jobs": self.jobs})
        if args[0] == "api":
            return json.dumps(self.run)
        if args[:2] == ("run", "view"):
            return LOG
        if args[:2] == ("issue", "list"):
            return json.dumps(self.abertas)
        if args[:2] == ("issue", "create"):
            return "https://github.com/dono/repo/issues/901\n"
        return ""

    def escritas(self) -> list[tuple[str, ...]]:
        return [a for a, _ in self.chamadas if a[:2] in (("issue", "create"), ("issue", "comment"), ("label", "create"))]


class LimpezaPorFormato(unittest.TestCase):
    def test_cor_some_nas_duas_notacoes_e_a_hora_tambem(self) -> None:
        self.assertEqual(mod.limpar_linha(f"2026-10-05T06:44:12.3174866Z {ESC}[31mFAIL{ESC}[39m x"), "FAIL x")
        self.assertEqual(mod.limpar_linha("2026-10-05T06:44:12Z ^[[31m⎯⎯^[[39m Failed Tests 2"), "⎯⎯ Failed Tests 2")

    def test_formatos_de_segredo_e_de_pessoa_nunca_passam(self) -> None:
        sujo = [
            "Authorization: Bearer abc.def.ghi",
            "chamada com senha=correto-cavalo e fim",
            "API_KEY: valor-qualquer-123",
            "token ghp_" + "a" * 30,
            "chave sk-" + "b" * 24,
            "ligou em 192.168.10.45 porta 22",
            "contato fulano.da.silva@exemplo.invalid avisado",
            r"C:\Users\fulano\AppData\Local\x",
        ]
        limpo = "\n".join(mod.limpar_linha(x) for x in sujo)
        for proibido in ("abc.def.ghi", "correto-cavalo", "valor-qualquer-123", "ghp_aaaa", "sk-bbbb", "192.168.10.45",
                         "fulano.da.silva", "exemplo.invalid", r"Users\fulano"):
            self.assertNotIn(proibido, limpo)
        self.assertIn("[oculto]", limpo)
        self.assertIn("[ip]", limpo)
        self.assertIn("[email]", limpo)
        self.assertIn(r"C:\Users\[usuario]\AppData", limpo)

    def test_cerca_dentro_do_log_nao_fecha_o_bloco_e_linha_longa_e_cortada(self) -> None:
        self.assertNotIn("```", mod.limpar_linha("antes ``` depois"))
        cortada = mod.limpar_linha("x" * 1000)
        self.assertEqual(len(cortada), mod.LIMITE_LINHA)
        self.assertTrue(cortada.endswith("…"))

    def test_versao_de_python_nao_vira_ip(self) -> None:
        self.assertEqual(mod.limpar_linha("Python 3.13.15 e pytest 8.4.2"), "Python 3.13.15 e pytest 8.4.2")


class LeituraDoLog(unittest.TestCase):
    def test_so_as_linhas_do_job_e_sem_o_log_do_contêiner_de_servico(self) -> None:
        linhas = [x for _, x in mod.linhas_do_passo(LOG, "backend · pytest (PostgreSQL)")]
        texto = "\n".join(linhas)
        self.assertIn("FAILED tests/test_x.py::test_a", texto)
        self.assertIn("waiting for server to start", texto)  # sem hora de serviço: fica, é linha do pytest/runner
        self.assertNotIn("duplicate key", texto)
        self.assertNotIn("nao deve aparecer", texto)

    def test_destaques_pegam_o_que_quebrou_sem_repetir(self) -> None:
        achados = mod.destaques(["ruído", "FAILED a - x", "FAILED a - x", "Found 254 errors in 47 files", "##[error]boom"])
        self.assertEqual(achados, ["FAILED a - x", "Found 254 errors in 47 files", "##[error]boom"])

    def test_corte_vai_ate_o_resumo_do_pytest(self) -> None:
        linhas = ["a", "FAILED x", "1 failed, 9 passed in 3.2s", "initdb ...", "server started"]
        self.assertEqual(mod.ate_o_resumo_do_pytest(linhas), linhas[:3])
        self.assertEqual(mod.ate_o_resumo_do_pytest(["sem", "resumo"]), ["sem", "resumo"])

    def test_duracao(self) -> None:
        self.assertEqual(mod.duracao("2026-10-05T05:00:00Z", "2026-10-05T06:00:25Z"), "60 min 25 s")
        self.assertEqual(mod.duracao("2026-10-05T05:00:00Z", "2026-10-05T05:00:07Z"), "7 s")
        self.assertEqual(mod.duracao(None, "2026-10-05T05:00:07Z"), "sem duração")


class TituloECorpo(unittest.TestCase):
    def test_titulo_tem_a_data_e_os_jobs_curtos_em_ordem(self) -> None:
        ruins = mod.jobs_ruins(_jobs())
        self.assertEqual([j["name"] for j in ruins], ["backend · pytest (PostgreSQL)", "frontend · typecheck + vitest"])
        self.assertEqual(mod.titulo("2026-10-05", ruins, "failure"), "CI noturno 2026-10-05: pytest (PostgreSQL), typecheck + vitest")

    def test_titulo_longo_corta_jobs_inteiros_e_diz_quantos_faltam(self) -> None:
        ruins = [{"name": f"grupo · job comprido numero {i} com muito texto", "conclusion": "failure"} for i in range(9)]
        t = mod.titulo("2026-10-05", ruins, "failure")
        self.assertLessEqual(len(t), 140)
        self.assertRegex(t, r" e mais \d+$")
        self.assertTrue(t.startswith(mod.prefixo_do_dia("2026-10-05")))

    def test_corpo_traz_run_commit_e_secoes_de_cada_job_sem_o_servico(self) -> None:
        texto = mod.corpo(_run(), mod.jobs_ruins(_jobs()), LOG, 40)
        self.assertIn("run 777", texto)
        self.assertIn("commit `81f99de`", texto)
        self.assertIn("### backend · pytest (PostgreSQL): failure (56 min 14 s)", texto)
        self.assertIn("FAILED tests/test_x.py::test_a", texto)
        self.assertNotIn("duplicate key", texto)
        self.assertNotIn("^[[", texto)

    def test_job_cancelado_sem_log_diz_que_nao_ha_log(self) -> None:
        job = {"name": "backend · pytest (SQLite)", "conclusion": "cancelled", "steps": [{"name": "Run pytest", "conclusion": "cancelled"}]}
        texto = mod.secao_do_job(job, "", 40)
        self.assertIn("cancelled", texto)
        self.assertIn("Sem linhas de log", texto)

    def test_corpo_respeita_o_limite_do_github(self) -> None:
        gigante = "\n".join(f"frontend · typecheck + vitest\tRun npm test\tlinha {i} " + "y" * 250 for i in range(5000))
        texto = mod.corpo(_run(), mod.jobs_ruins(_jobs()), gigante, 5000)
        self.assertLessEqual(len(texto), mod.LIMITE_CORPO)


class UmaIssuePorNoite(unittest.TestCase):
    def test_run_que_passou_nao_abre_nada(self) -> None:
        gh = FakeGh(_run("success"), _jobs())
        feito, _, _ = mod.avisar(REPO, "777", ensaio=False, gh=gh)
        self.assertEqual(feito, "nada")
        self.assertEqual(gh.escritas(), [])

    def test_sem_issue_aberta_cria_com_o_rotulo_ci_e_o_corpo_pela_entrada_padrao(self) -> None:
        gh = FakeGh(_run(), _jobs())
        feito, titulo, _ = mod.avisar(REPO, "777", ensaio=False, gh=gh)
        self.assertEqual(feito, "aberta 901")
        criacao = [(a, e) for a, e in gh.chamadas if a[:2] == ("issue", "create")]
        self.assertEqual(len(criacao), 1)
        args, corpo = criacao[0]
        self.assertIn("--label", args)
        self.assertEqual(args[args.index("--label") + 1], "ci")
        self.assertEqual(args[args.index("--title") + 1], titulo)
        self.assertIn("run 777", corpo or "")
        self.assertIn(("label", "create"), [a[:2] for a, _ in gh.chamadas])  # o rótulo existe antes de usar

    def test_com_issue_aberta_do_mesmo_dia_comenta_nela_em_vez_de_abrir_outra(self) -> None:
        abertas = [
            {"number": 905, "title": "CI noturno 2026-10-05: pytest (PostgreSQL)"},
            {"number": 903, "title": "CI noturno 2026-10-05: typecheck + vitest"},
            {"number": 880, "title": "CI noturno 2026-10-04: mypy (gradual)"},
        ]
        gh = FakeGh(_run(), _jobs(), abertas)
        feito, _, _ = mod.avisar(REPO, "777", ensaio=False, gh=gh)
        self.assertEqual(feito, "comentada 903")  # a mais antiga do dia
        self.assertFalse([a for a, _ in gh.chamadas if a[:2] == ("issue", "create")])
        comentario = [(a, e) for a, e in gh.chamadas if a[:2] == ("issue", "comment")]
        self.assertEqual(len(comentario), 1)
        self.assertIn("Nova corrida do mesmo dia", comentario[0][1] or "")

    def test_issue_de_outro_dia_nao_e_reaproveitada(self) -> None:
        gh = FakeGh(_run(data="2026-10-06"), _jobs(), [{"number": 880, "title": "CI noturno 2026-10-05: mypy (gradual)"}])
        feito, _, _ = mod.avisar(REPO, "777", ensaio=False, gh=gh)
        self.assertEqual(feito, "aberta 901")

    def test_ensaio_le_e_imprime_mas_nunca_escreve(self) -> None:
        gh = FakeGh(_run(), _jobs())
        feito, texto, _ = mod.avisar(REPO, "777", ensaio=True, gh=gh)
        self.assertEqual(feito, "ensaio")
        self.assertIn("TÍTULO: CI noturno 2026-10-05", texto or "")
        self.assertEqual(gh.escritas(), [])

    def test_erro_do_gh_propaga_e_o_main_sai_com_1(self) -> None:
        gh = FakeGh(_run(), _jobs())
        gh.erro_em = "list"
        with self.assertRaises(RuntimeError):
            mod.avisar(REPO, "777", ensaio=False, gh=gh)
        original = mod.gh_real
        mod.gh_real = gh  # type: ignore[assignment]
        try:
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                codigo = mod.main(["--repo", REPO, "--run-id", "777"])
        finally:
            mod.gh_real = original  # type: ignore[assignment]
        self.assertEqual(codigo, 1)
        self.assertIn("erro:", err.getvalue())


class LimpezaDoRevisor(unittest.TestCase):
    """Casos que a revisão de segredos apontou (29.155): aspas, prefixo no nome, caminho com barra dobrada, ReDoS, menção."""

    def test_dict_impresso_e_json_com_aspas_nao_vazam(self) -> None:
        for sujo, valor in [
            ("assert {'password': 'x1y2z3'} == {}", "x1y2z3"),
            ('"senha": "duas palavras secretas"', "palavras"),
            ('{"Authorization": "Bearer abc.defghij"}', "abc.defghij"),
        ]:
            self.assertNotIn(valor, mod.limpar_linha(sujo), sujo)

    def test_nome_com_prefixo_ou_sufixo_tambem_e_escondido(self) -> None:
        for sujo in ("access_token=ZZZ999", "client_secret: ZZZ999", "AWS_SECRET_ACCESS_KEY=ZZZ999", "db_password=ZZZ999"):
            limpo = mod.limpar_linha(sujo)
            self.assertNotIn("ZZZ999", limpo, sujo)
            self.assertIn("[oculto]", limpo)

    def test_nome_de_teste_com_token_nao_e_escondido(self) -> None:
        linha = "FAILED tests/test_a.py::test_token_cache::depois - assert 1 == 2"
        self.assertEqual(mod.limpar_linha(linha), linha)

    def test_credenciais_sem_nome_pelo_formato(self) -> None:
        sujo = [
            "usando Bearer abcdefgh12345678 no pedido",
            "Authorization: Digest username=x, realm=y, response=ZZ999",
            "jwt eyJhbGciOiJI.eyJzdWIiOiIx.c2lnbmF0dXJl fim",
            "chave AKIA" + "A" * 16 + " fim",
            "-----BEGIN RSA PRIVATE KEY-----",
        ]
        limpo = "\n".join(mod.limpar_linha(x) for x in sujo)
        for proibido in ("abcdefgh12345678", "ZZ999", "eyJhbGci", "AKIAAAAA", "RSA PRIVATE"):
            self.assertNotIn(proibido, limpo)

    def test_pasta_de_usuario_com_barra_dobrada_normal_e_de_git_bash(self) -> None:
        for sujo in (r"C:\\Users\\Fulano\\x", "C:/Users/Fulano/x", "/c/Users/Fulano/x", r"C:\Users\Fulano\x"):
            limpo = mod.limpar_linha(sujo)
            self.assertNotIn("Fulano", limpo, sujo)
            self.assertIn("[usuario]", limpo)

    def test_documento_telefone_e_ipv6(self) -> None:
        limpo = mod.limpar_linha("cpf 123.456.789-09 cnpj 12.345.678/0001-90 tel +55 11 91234-5678 v6 2001:db8:0:0:0:0:0:1")
        for proibido in ("123.456.789-09", "12.345.678/0001-90", "91234-5678", "2001:db8"):
            self.assertNotIn(proibido, limpo)

    def test_linha_gigante_e_barata(self) -> None:
        import time

        t = time.monotonic()
        mod.limpar_linha("a" * 200_000)
        mod.limpar_linha(("senha" * 5000) + "=x")
        mod.limpar_linha("1." * 100_000)
        self.assertLess(time.monotonic() - t, 1.0)

    def test_nome_de_job_nao_menciona_nem_linka_nem_quebra_o_codigo(self) -> None:
        feio = "job @alguem vê #12 `x`\nnova linha <b>"
        limpo = mod.nome_seguro(feio)
        for proibido in ("@", "#", "`", "\n", "<", ">"):
            self.assertNotIn(proibido, limpo)
        job = {"name": feio, "conclusion": "failure", "steps": [{"name": "passo `@x`", "conclusion": "failure"}]}
        secao = mod.secao_do_job(job, "", 40)
        self.assertNotIn("@", secao)
        self.assertNotIn("#12", secao)
        self.assertNotIn("@", mod.titulo("2026-10-05", [job], "failure"))


class QuandoOLogFalta(unittest.TestCase):
    def test_log_ilegivel_abre_a_issue_assim_mesmo_e_o_main_sai_com_1(self) -> None:
        job = {"name": "backend · pytest (PostgreSQL)", "conclusion": "cancelled", "steps": [{"name": "Run pytest", "conclusion": "cancelled"}]}
        gh = FakeGh(_run("cancelled"), [job])
        gh.erro_em = "--log-failed"
        feito, _, erro_log = mod.avisar(REPO, "777", ensaio=False, gh=gh)
        self.assertEqual(feito, "aberta 901")
        self.assertIn("simulado", erro_log or "")
        corpo = [e for a, e in gh.chamadas if a[:2] == ("issue", "create")][0] or ""
        self.assertIn("Log indisponível", corpo)
        original = mod.gh_real
        mod.gh_real = FakeGh(_run("cancelled"), [job])  # type: ignore[assignment]
        mod.gh_real.erro_em = "--log-failed"  # type: ignore[attr-defined]
        try:
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                codigo = mod.main(["--repo", REPO, "--run-id", "777"])
        finally:
            mod.gh_real = original  # type: ignore[assignment]
        self.assertEqual(codigo, 1)
        self.assertIn("log do run", err.getvalue())

    def test_run_de_outro_workflow_nao_escreve_nada(self) -> None:
        run = _run()
        run["name"] = "Contêiner"
        gh = FakeGh(run, _jobs())
        with self.assertRaises(ValueError):
            mod.avisar(REPO, "777", ensaio=False, gh=gh)
        self.assertEqual(gh.escritas(), [])


class LinhaDeComando(unittest.TestCase):
    def test_run_id_com_quebra_de_linha_no_fim_e_recusado(self) -> None:
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(mod.main(["--repo", REPO, "--run-id", "12\n"]), 1)

    def _main(self, *args: str) -> tuple[int, str]:
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            codigo = mod.main(list(args))
        return codigo, err.getvalue()

    def test_run_id_precisa_ser_numerico(self) -> None:
        codigo, err = self._main("--repo", REPO, "--run-id", "12; rm -rf /")
        self.assertEqual(codigo, 1)
        self.assertIn("numérico", err)

    def test_repo_fora_do_formato_e_recusado(self) -> None:
        codigo, err = self._main("--repo", "sem-barra", "--run-id", "1")
        self.assertEqual(codigo, 1)
        self.assertIn("dono/nome", err)


if __name__ == "__main__":
    unittest.main()
