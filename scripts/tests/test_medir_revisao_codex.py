"""scripts/medir_revisao_codex.py (29.194): medida da revisão do Codex nos PRs. Prova `simulated`: `gh` falso, dados gravados do formato da API."""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("medir_codex", ROOT / "scripts" / "medir_revisao_codex.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]
coletor = mod._coletor()

REPO = "dono/repo"
CODEX = {"login": "chatgpt-codex-connector[bot]"}


def comentario(corpo, quando="2026-10-06T18:52:00Z", user=CODEX, path="backend/app/x.py", line=10):
    return {"user": user, "body": corpo, "created_at": quando, "path": path, "line": line, "html_url": "https://github.com/dono/repo/pull/1#c"}


PRS = {
    473: {"created_at": "2026-10-06T18:47:00Z", "state": "closed", "merged_at": "2026-10-06T20:00:00Z"},
    474: {"created_at": "2026-10-06T18:47:00Z", "state": "closed", "merged_at": None},
    475: {"created_at": "2026-10-06T18:47:00Z", "state": "open", "merged_at": None},
}
COMENTARIOS = {
    473: [comentario("**P1 Badge** Propague a falha", "2026-10-06T18:51:00Z"), comentario("**P2 Badge** Outro ponto", "2026-10-06T18:55:00Z")],
    474: [comentario("**P1 Badge** Remova alteração: regra explícita do repositório, um PR por tarefa (AGENTS.md)", "2026-10-06T18:53:00Z"),
          comentario("Comentário de gente", user={"login": "fulano"})],
    475: [],
}
REVISOES = {
    473: [{"user": CODEX, "body": "Codex Review: resumo", "submitted_at": "2026-10-06T18:51:30Z"}],
    474: [{"user": CODEX, "body": "Codex Review: resumo", "submitted_at": "2026-10-06T18:53:30Z"}],
    475: [{"user": CODEX, "body": "Codex Review: Didn't find any major issues. Nice work!", "submitted_at": "2026-10-06T18:52:00Z"}],
}
COMMITS = [
    {"commit": {"message": "fix(sessao): achados do Codex: toque de saída (PR 473)"}},
    {"commit": {"message": "fix(operacoes): revisão dos PRs 473 e 475: reserva do teto"}},
    {"commit": {"message": "feat(portal): outra coisa sem citar PR"}},
    {"commit": {"message": "docs: coisa que cita #474 mas não fala de correção nenhuma"}},
    {"commit": {"message": "fix: achado da revisão #474 corrigido"}},
]


class Fake:
    def __init__(self, falha_em=None):
        self.falha_em, self.chamadas = falha_em, []

    def __call__(self, *args: str, entrada: str | None = None) -> str:
        self.chamadas.append((args, entrada))
        rota = args[-1] if args[0] == "api" else ""
        if self.falha_em and self.falha_em in rota:
            raise RuntimeError("o gh falhou (código 1)")
        if args[:2] == ("issue", "list"):
            return "[]"
        if args[:2] == ("issue", "create"):
            return "https://github.com/dono/repo/issues/950\n"
        if "/commits" in rota:
            return "".join(json.dumps(c) for c in COMMITS)
        n = int(rota.split("/pulls/")[1].split("/")[0]) if "/pulls/" in rota else 0
        if rota.endswith(f"/pulls/{n}"):
            return json.dumps(PRS[n])
        if rota.endswith("/comments"):
            return "".join(json.dumps(c) for c in COMENTARIOS[n])
        if rota.endswith("/reviews"):
            return "".join(json.dumps(r) for r in REVISOES[n])
        return ""


class Medir(unittest.TestCase):
    def test_conta_achados_gravidade_tempo_e_so_do_codex(self) -> None:
        l = mod.medir_pr(REPO, 473, Fake(), coletor)
        self.assertEqual((l["achados"], l["p1"], l["p2"], l["estado"], l["revisada"]), (2, 1, 1, "merged", True))
        self.assertEqual(l["minutos_ate_o_achado"], 4)  # 18:47 -> 18:51
        l474 = mod.medir_pr(REPO, 474, Fake(), coletor)
        self.assertEqual((l474["achados"], l474["artefato"], l474["estado"]), (1, 1, "closed"))  # o comentário de gente não conta

    def test_revisao_sem_achado_conta_como_limpa(self) -> None:
        l = mod.medir_pr(REPO, 475, Fake(), coletor)
        self.assertEqual((l["achados"], l["sem_achado"], l["revisada"]), (0, 1, True))

    def test_correcoes_por_pr_leem_numero_unico_lista_e_hashtag(self) -> None:
        cont = mod.correcoes_por_pr(REPO, None, Fake(), coletor)
        self.assertEqual(cont, {473: 2, 475: 1, 474: 1})  # "PRs 473 e 475" cita os dois; commit sem a palavra revisão/achado/Codex não conta

    def test_recomendacao_mecanica(self) -> None:
        self.assertEqual(mod.recomendar(0, 0, 0)[0], "sem dados")
        self.assertEqual(mod.recomendar(38, 12, 25)[0], "restringir")  # 31 % de falso
        self.assertEqual(mod.recomendar(20, 1, 15)[0], "manter")
        self.assertEqual(mod.recomendar(12, 6, 1)[0], "desligar")
        self.assertEqual(mod.recomendar(5, 0, 0)[0], "restringir")  # pouco dado: não desliga

    def test_relatorio_usa_a_classificacao_da_frente_no_lugar_do_indicio(self) -> None:
        linhas = [mod.medir_pr(REPO, n, Fake(), coletor) for n in (473, 474, 475)]
        sem = mod.relatorio(linhas, {473: 2}, {}, "janela")
        self.assertIn("(indício; artefato por regex, impreciso)", sem)
        com = mod.relatorio(linhas, {473: 2}, {"473": {"confirmados": 2, "falsos": 0}, "474": {"confirmados": 0, "falsos": 1}, "475": {}}, "janela")
        self.assertIn("| 473 |", com)
        self.assertIn("2 / 0 (frente)", com)
        self.assertIn("Confirmados e corrigidos: 2; falsos: 1", com)

    def test_relatorio_diz_que_custo_do_codex_nao_tem_numero(self) -> None:
        texto = mod.relatorio([mod.medir_pr(REPO, 473, Fake(), coletor)], {}, {}, "janela")
        self.assertIn("não tem API de uso", texto)
        self.assertIn("dado a conferir, nunca ordem", texto)
        self.assertNotIn("US$", texto)

    def test_sem_nome_de_conta_nem_repo_no_texto(self) -> None:
        texto = mod.relatorio([mod.medir_pr(REPO, 473, Fake(), coletor)], {}, {}, "janela")
        self.assertNotIn("dono/", texto)
        self.assertNotIn("repo", texto.replace("repositório", ""))


class LinhaDeComando(unittest.TestCase):
    def _main(self, gh, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            return mod.main(list(args), gh=gh), out.getvalue(), err.getvalue()

    def test_so_le_sem_publicar_e_grava_a_saida(self) -> None:
        gh = Fake()
        saida = Path(tempfile.mkdtemp()) / "r.md"
        codigo, texto, _ = self._main(gh, "--repo", REPO, "--prs", "473,474,475", "--saida", str(saida))
        self.assertEqual(codigo, 0)
        self.assertIn("Medida da revisão automática do Codex", saida.read_text(encoding="utf-8"))
        for args, _ in gh.chamadas:
            self.assertEqual(args[0], "api")  # nenhuma escrita sem --publicar

    def test_publicar_comenta_na_issue_de_custo(self) -> None:
        gh = Fake()
        codigo, texto, _ = self._main(gh, "--repo", REPO, "--prs", "473", "--publicar")
        self.assertEqual(codigo, 0)
        self.assertIn("aberta 950", texto)
        self.assertIn(("issue", "create"), [a[:2] for a, _ in gh.chamadas])

    def test_erro_do_gh_e_argumento_torto_saem_com_1(self) -> None:
        self.assertEqual(self._main(Fake(falha_em="/commits"), "--repo", REPO, "--prs", "473")[0], 1)
        self.assertEqual(self._main(Fake(), "--repo", "x y", "--prs", "473")[0], 1)
        self.assertEqual(self._main(Fake(), "--repo", REPO, "--prs", "473;rm")[0], 1)

    def test_desde_torto_e_janela_com_formato_proibido_nao_publicam(self) -> None:
        self.assertEqual(self._main(Fake(), "--repo", REPO, "--prs", "473", "--desde", "2026-10-06&per_page=1")[0], 1)
        gh = Fake()
        codigo, _, err = self._main(gh, "--repo", REPO, "--prs", "473", "--janela", "contato fulano@exemplo.invalid", "--publicar")
        self.assertEqual(codigo, 1)
        self.assertIn("e-mail", err)
        self.assertNotIn(("issue", "create"), [a[:2] for a, _ in gh.chamadas])

    def test_relatorio_mostra_as_revisoes_sem_achado(self) -> None:
        texto = mod.relatorio([mod.medir_pr(REPO, n, Fake(), coletor) for n in (473, 475)], {}, {}, "janela")
        self.assertIn("sem achado nenhum: 1", texto)

    def test_classificacao_ilegivel_sai_com_1(self) -> None:
        ruim = Path(tempfile.mkdtemp()) / "c.json"
        ruim.write_text("{não", encoding="utf-8")
        self.assertEqual(self._main(Fake(), "--repo", REPO, "--prs", "473", "--classificacao", str(ruim))[0], 1)


if __name__ == "__main__":
    unittest.main()
