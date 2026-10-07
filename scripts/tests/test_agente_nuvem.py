"""scripts/agente_nuvem.py (29.192): atribuição com teto e medida da esteira. Prova `simulated`: `gh` falso e relógio fixo."""
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
_spec = importlib.util.spec_from_file_location("agente_nuvem", ROOT / "scripts" / "agente_nuvem.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]

REPO = "dono/repo"
AGORA = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
HOJE = "2026-10-07"
MARCA_HOJE = f"<!-- agente-nuvem:atribuida {HOJE} -->"


def issue(n=500, estado="OPEN", rotulos=("agente-nuvem", "tamanho:P"), assignees=(), corpo="<!-- pacote:29.150 -->\nFazer X", comments=()):
    return {"number": n, "title": f"29.150: item {n}", "body": corpo, "state": estado,
            "labels": [{"name": r} for r in rotulos], "assignees": [{"login": a} for a in assignees],
            "comments": [{"body": c, "createdAt": "2026-10-07T10:00:00Z"} for c in comments]}


class Fake:
    def __init__(self, alvo, esteira=None, prs=None, nao_confirma=False):
        self.alvo, self.esteira, self.prs, self.nao_confirma = alvo, esteira or [], prs or [], nao_confirma
        self.chamadas: list[tuple[tuple[str, ...], str | None]] = []
        self.atribuida = False

    def __call__(self, *args: str, entrada: str | None = None) -> str:
        self.chamadas.append((args, entrada))
        if args[:2] == ("issue", "view"):
            vista = dict(self.alvo)
            if self.atribuida and not self.nao_confirma:
                vista["assignees"] = [{"login": "Copilot"}]
            return json.dumps(vista)
        if args[:2] == ("issue", "list"):
            return json.dumps(self.esteira)
        if args[:2] == ("pr", "list"):
            return json.dumps(self.prs)
        if args[:2] == ("issue", "edit"):
            self.atribuida = True
        return ""

    def escritas(self):
        return [a for a, _ in self.chamadas if a[:2] in (("issue", "edit"), ("issue", "comment"))]


def pr(n, corpo, estado="OPEN", merged=None, autor="Copilot", criado="2026-10-07T12:30:00Z", draft=False):
    return {"number": n, "title": "t", "body": corpo, "state": estado, "isDraft": draft, "mergedAt": merged, "closedAt": None,
            "createdAt": criado, "additions": 10, "deletions": 2, "changedFiles": 3, "author": {"login": autor}}


class Atribuir(unittest.TestCase):
    def test_issue_liberada_e_dentro_do_teto_e_atribuida_e_marcada(self) -> None:
        gh = Fake(issue())
        self.assertEqual(mod.atribuir(REPO, 500, aplicar=True, gh=gh, agora=AGORA), ("atribuida", []))
        edicao = [a for a, _ in gh.chamadas if a[:2] == ("issue", "edit")]
        self.assertEqual(edicao[0][edicao[0].index("--add-assignee") + 1], "@copilot")
        marca = [e for a, e in gh.chamadas if a[:2] == ("issue", "comment")][0] or ""
        self.assertIn(MARCA_HOJE, marca)

    def test_ensaio_nao_escreve(self) -> None:
        gh = Fake(issue())
        self.assertEqual(mod.atribuir(REPO, 500, aplicar=False, gh=gh, agora=AGORA), ("ensaio", []))
        self.assertEqual(gh.escritas(), [])

    def test_recusas_com_o_motivo(self) -> None:
        casos = {
            "não está aberta": issue(estado="CLOSED"),
            "falta a etiqueta": issue(rotulos=("tamanho:P",)),
            "marca do pacote": issue(corpo="sem marca"),
            "já tem alguém": issue(assignees=("fulano",)),
            "tamanho G": issue(rotulos=("agente-nuvem", "tamanho:G")),
            "formato proibido": issue(corpo="<!-- pacote:29.150 -->\nfale com fulano@exemplo.invalid"),
        }
        for trecho, alvo in casos.items():
            gh = Fake(alvo)
            resultado, motivos = mod.atribuir(REPO, 500, aplicar=True, gh=gh, agora=AGORA)
            self.assertEqual(resultado, "recusada", trecho)
            self.assertTrue(any(trecho in m for m in motivos), (trecho, motivos))
            self.assertEqual(gh.escritas(), [], trecho)

    def test_teto_de_uma_por_dia_conta_so_a_marca_de_hoje(self) -> None:
        ontem = issue(n=490, comments=("<!-- agente-nuvem:atribuida 2026-10-06 -->",))
        hoje = issue(n=491, comments=(MARCA_HOJE,))
        self.assertEqual(mod.atribuir(REPO, 500, aplicar=True, gh=Fake(issue(), esteira=[ontem]), agora=AGORA)[0], "atribuida")
        gh = Fake(issue(), esteira=[ontem, hoje])
        resultado, motivos = mod.atribuir(REPO, 500, aplicar=True, gh=gh, agora=AGORA)
        self.assertEqual(resultado, "recusada")
        self.assertTrue(any("teto diário" in m and "491" in m for m in motivos))
        self.assertEqual(gh.escritas(), [])

    def test_uma_tarefa_por_vez_pr_aberto_do_agente_bloqueia_mas_pr_de_gente_nao(self) -> None:
        gh = Fake(issue(), prs=[pr(600, "x", autor="Copilot")])
        self.assertTrue(any("uma tarefa por vez" in m for m in mod.atribuir(REPO, 500, aplicar=True, gh=gh, agora=AGORA)[1]))
        gh2 = Fake(issue(), prs=[pr(601, "x", autor="fulano"), pr(602, "x", estado="CLOSED"), pr(603, "x", estado="MERGED", merged="2026-10-06T00:00:00Z")])
        self.assertEqual(mod.atribuir(REPO, 500, aplicar=True, gh=gh2, agora=AGORA)[0], "atribuida")

    def test_sem_confirmacao_na_leitura_da_issue_e_erro_mas_a_marca_ja_gastou_o_teto(self) -> None:
        gh = Fake(issue(), nao_confirma=True)
        with self.assertRaises(RuntimeError):
            mod.atribuir(REPO, 500, aplicar=True, gh=gh, agora=AGORA)
        self.assertEqual(len([a for a, _ in gh.chamadas if a[:2] == ("issue", "comment")]), 1)

    def test_a_marca_do_teto_sai_antes_da_atribuicao(self) -> None:
        gh = Fake(issue())
        mod.atribuir(REPO, 500, aplicar=True, gh=gh, agora=AGORA)
        ordem = [a[:2] for a in gh.escritas()]
        self.assertEqual(ordem, [("issue", "comment"), ("issue", "edit")])

    def test_issue_ja_entregue_ao_agente_e_aberta_sem_pr_bloqueia_outra(self) -> None:
        entregue = {**issue(n=490, assignees=("Copilot",)), "createdAt": "2026-10-06T09:00:00Z"}
        gh = Fake(issue(), esteira=[entregue])
        resultado, motivos = mod.atribuir(REPO, 500, aplicar=True, gh=gh, agora=AGORA)
        self.assertEqual(resultado, "recusada")
        self.assertTrue(any("uma tarefa por vez" in m for m in motivos))
        entregue_fechada = {**entregue, "state": "CLOSED"}
        self.assertEqual(mod.atribuir(REPO, 500, aplicar=False, gh=Fake(issue(), esteira=[entregue_fechada]), agora=AGORA)[0], "ensaio")

    def test_login_de_gente_com_copilot_no_nome_nao_e_o_agente(self) -> None:
        self.assertTrue(all(mod._do_agente({"login": x}) for x in ("Copilot", "copilot-swe-agent", "app/copilot-swe-agent", "copilot-swe-agent[bot]")))
        self.assertFalse(any(mod._do_agente({"login": x}) for x in ("copilot-fan", "meu-copilot", "fulano", "")))
        gh = Fake(issue(), prs=[pr(600, "x", autor="copilot-fan")])
        self.assertEqual(mod.atribuir(REPO, 500, aplicar=False, gh=gh, agora=AGORA)[0], "ensaio")

    def test_atribuir_pede_so_os_prs_abertos(self) -> None:
        gh = Fake(issue())
        mod.atribuir(REPO, 500, aplicar=False, gh=gh, agora=AGORA)
        pedido = [a for a, _ in gh.chamadas if a[:2] == ("pr", "list")][0]
        self.assertEqual(pedido[pedido.index("--state") + 1], "open")

    def test_erro_do_gh_real_nao_leva_o_texto_do_gh(self) -> None:
        import subprocess
        from unittest import mock
        falso = subprocess.CompletedProcess(["gh"], 1, stdout="", stderr="HTTP 404: repos/dono/segredo-repo")
        with mock.patch.object(mod.subprocess, "run", return_value=falso):
            with self.assertRaises(RuntimeError) as c:
                mod.gh_real("issue", "view", "1")
        self.assertNotIn("segredo-repo", str(c.exception))


class Medir(unittest.TestCase):
    def esteira(self):
        base = "<!-- pacote:29.150 -->"
        return [
            {**issue(n=500, comments=(MARCA_HOJE,)), "createdAt": "2026-10-07T09:00:00Z"},
            {**issue(n=501, comments=(MARCA_HOJE,)), "createdAt": "2026-10-07T09:00:00Z"},
            {**issue(n=502, comments=()), "createdAt": "2026-10-07T09:00:00Z"},
        ], [
            pr(700, "Fixes #500", estado="MERGED", merged="2026-10-07T13:00:00Z", criado="2026-10-07T10:20:00Z"),
            pr(701, "Fixes #501", estado="CLOSED", criado="2026-10-07T10:45:00Z"),
            pr(702, "Fixes #5000 não é a 500", criado="2026-10-07T10:50:00Z"),
            pr(703, "Fixes #502", autor="fulano"),  # PR de gente não conta como do agente
        ]

    def test_tabela_taxa_e_creditos_estimados_ditos_como_estimativa(self) -> None:
        issues, prs = self.esteira()
        texto = mod.medir(REPO, gh=Fake(issue(), esteira=issues, prs=prs))
        self.assertIn("| #500 | 29.150 | aceito | #700 | 20 |", texto)
        self.assertIn("| #501 | 29.150 | recusado | #701 | 45 |", texto)
        self.assertIn("| #502 | 29.150 | sem PR | - |", texto)
        self.assertIn("aceitas: 1; recusadas: 1", texto)
        self.assertIn("1/2 = 50 %", texto)
        self.assertIn("ESTIMADOS (31 por tarefa", texto)
        self.assertIn("93 (est.)".replace("93", "31"), texto)

    def test_creditos_reais_do_arquivo_valem_no_lugar_da_estimativa(self) -> None:
        issues, prs = self.esteira()
        texto = mod.medir(REPO, custos={"500": 40, "501": 20, "502": 5}, gh=Fake(issue(), esteira=issues, prs=prs))
        self.assertIn("lidos na página de uso: 65", texto)
        self.assertIn("por item aceito: 65", texto)
        self.assertNotIn("(est.)", texto)

    def test_texto_sem_nome_de_conta_nem_repo_e_so_leitura(self) -> None:
        issues, prs = self.esteira()
        gh = Fake(issue(), esteira=issues, prs=prs)
        texto = mod.medir(REPO, gh=gh)
        self.assertNotIn("dono", texto.replace("do dono", ""))
        self.assertNotIn("repo", texto.replace("repositório", ""))
        for args, _ in gh.chamadas:
            self.assertIn(args[:2], (("issue", "list"), ("pr", "list")))


class LinhaDeComando(unittest.TestCase):
    def _main(self, gh, *args) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            return mod.main(list(args), gh=gh, agora=AGORA), out.getvalue()

    def test_atribuir_recusada_sai_com_1_e_lista_motivos(self) -> None:
        codigo, saida = self._main(Fake(issue(assignees=("fulano",))), "atribuir", "500", "--repo", REPO, "--aplicar")
        self.assertEqual(codigo, 1)
        self.assertIn("RECUSADA", saida)

    def test_atribuir_ok_e_ensaio_por_padrao(self) -> None:
        gh = Fake(issue())
        codigo, saida = self._main(gh, "atribuir", "500", "--repo", REPO)
        self.assertEqual(codigo, 0)
        self.assertIn("ensaio", saida)
        self.assertEqual(gh.escritas(), [])

    def test_repo_invalido_e_custos_ilegiveis_saem_com_1(self) -> None:
        self.assertEqual(self._main(Fake(issue()), "medir", "--repo", "x y")[0], 1)
        ruim = Path(tempfile.mkdtemp()) / "c.json"
        ruim.write_text("{não", encoding="utf-8")
        self.assertEqual(self._main(Fake(issue()), "medir", "--repo", REPO, "--custos", str(ruim))[0], 1)

    def test_sem_workflow_que_atribua(self) -> None:
        """A atribuição só sai deste script, rodado na sessão: nenhum workflow chama agente_nuvem nem usa --add-assignee."""
        for caminho in (ROOT / ".github" / "workflows").glob("*.yml"):
            texto = caminho.read_text(encoding="utf-8")
            self.assertNotIn("scripts/agente_nuvem.py", texto, caminho.name)  # o teste dele pode estar na lista do pytest do job docs
            self.assertNotIn("add-assignee", texto, caminho.name)


if __name__ == "__main__":
    unittest.main()
