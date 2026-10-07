"""scripts/pr_revisao.py (29.200): PR de revisão do corte. Prova `simulated`: `gh` falso com o formato da API."""
from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("pr_revisao", ROOT / "scripts" / "pr_revisao.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]

REPO = "dono/repo"
AGORA = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
SHA = "a" * 40


def comparacao(status="ahead", ahead=2, patch="+linha nova\n", n=3):
    return {"status": status, "ahead_by": ahead, "merge_base_commit": {"sha": SHA},
            "files": [{"filename": f"a{i}.py", "patch": patch} for i in range(n)]}


def pr_de_revisao(minutos_atras, titulo="[revisão] 31.1 x"):
    return {"createdAt": (AGORA - timedelta(minutes=minutos_atras)).strftime("%Y-%m-%dT%H:%M:%SZ"), "title": titulo}


class Fake:
    def __init__(self, cmp_=None, prs=None, rotulos=("frente:jev",), falha_em=None, abertos=()):
        self.cmp, self.abertos = cmp_ or comparacao(), list(abertos)
        self.prs, self.rotulos, self.falha_em, self.chamadas = prs or [], rotulos, falha_em, []

    def __call__(self, *args: str) -> str:
        self.chamadas.append(args)
        if self.falha_em and self.falha_em in " ".join(args):
            raise RuntimeError("o gh falhou")
        if args[0] == "api" and "/commits/" in args[-1]:
            return json.dumps({"sha": SHA})
        if args[0] == "api" and "/compare/" in args[-1]:
            return json.dumps(self.cmp)
        if args[:2] == ("pr", "list") and "--head" in args:
            return json.dumps(self.abertos)
        if args[:2] == ("pr", "list"):
            return json.dumps(self.prs)
        if args[:2] == ("label", "list"):
            return json.dumps([{"name": n} for n in self.rotulos])
        if args[:2] == ("pr", "create"):
            return "https://github.com/dono/repo/pull/777\n"
        return ""

    def escritas(self):
        return [c for c in self.chamadas if c[:2] == ("pr", "create") or (c[0] == "api" and "POST" in c)]


def rodar(gh, *extra, id_="31.241", titulo="reabre após aprovação", head="feat/31-241-x"):
    out, err = io.StringIO(), io.StringIO()
    args = ["--repo", REPO, "--id", id_, "--titulo", titulo, "--head", head, "--frente", "jev", *extra]
    with redirect_stdout(out), redirect_stderr(err):
        codigo = mod.main(args, gh=gh, agora=AGORA)
    return codigo, out.getvalue(), err.getvalue()


class Plano(unittest.TestCase):
    def test_ensaio_nao_escreve_nada(self) -> None:
        gh = Fake()
        codigo, out, _ = rodar(gh)
        self.assertEqual(codigo, 0)
        self.assertIn("abriria", out)
        self.assertEqual(gh.escritas(), [])

    def test_aplicar_cria_o_pr_com_titulo_corpo_e_rotulo(self) -> None:
        gh = Fake()
        codigo, out, _ = rodar(gh, "--aplicar")
        self.assertEqual(codigo, 0)
        self.assertIn("pull/777", out)
        criar = next(c for c in gh.escritas() if c[:2] == ("pr", "create"))
        self.assertEqual(criar[criar.index("--title") + 1], "[revisão] 31.241 reabre após aprovação")
        self.assertEqual(criar[criar.index("--base") + 1], "main")
        self.assertEqual(criar[criar.index("--label") + 1], "frente:jev")
        corpo = criar[criar.index("--body") + 1]
        self.assertIn("fecha sem merge", corpo)
        self.assertNotIn(REPO, corpo)

    def test_branch_que_nao_nasceu_da_base_ganha_branch_de_base_no_merge_base(self) -> None:
        gh = Fake(comparacao(status="diverged"))
        codigo, out, _ = rodar(gh, "--aplicar")
        self.assertEqual(codigo, 0)
        self.assertIn("base nova em aaaaaaaa", out)
        post = next(c for c in gh.escritas() if c[0] == "api")
        self.assertIn(f"sha={SHA}", post)
        self.assertIn("ref=refs/heads/revisao/base-31.241", post)
        criar = next(c for c in gh.escritas() if c[:2] == ("pr", "create"))
        self.assertEqual(criar[criar.index("--base") + 1], "revisao/base-31.241")

    def test_base_dada_como_commit_vira_branch_de_base(self) -> None:
        gh = Fake()
        codigo, out, _ = rodar(gh, "--base", "9a718527", "--aplicar")
        self.assertEqual(codigo, 0)
        self.assertIn("a base é um commit", out)
        post = next(c for c in gh.escritas() if c[0] == "api")
        self.assertIn(f"sha={SHA}", post)
        criar = next(c for c in gh.escritas() if c[:2] == ("pr", "create"))
        self.assertEqual(criar[criar.index("--base") + 1], "revisao/base-31.241")
        ensaio = Fake()
        self.assertEqual(rodar(ensaio, "--base", "9a718527")[0], 0)
        self.assertEqual(ensaio.escritas(), [])  # no ensaio nem a branch de base é criada

    def test_sem_commit_novo_nao_abre(self) -> None:
        for cmp_ in (comparacao(status="identical", ahead=0), comparacao(status="behind", ahead=0)):
            gh = Fake(cmp_)
            codigo, _, err = rodar(gh, "--aplicar")
            self.assertEqual(codigo, 1)
            self.assertIn("sem commit novo", err.replace("não tem commit novo", "sem commit novo"))
            self.assertEqual(gh.escritas(), [])

    def test_branch_que_ja_tem_pr_aberto_nao_abre_outro(self) -> None:
        gh = Fake(abertos=[{"number": 500}])
        codigo, _, err = rodar(gh, "--aplicar")
        self.assertEqual(codigo, 1)
        self.assertIn("#500", err)
        self.assertEqual(gh.escritas(), [])

    def test_diff_grande_avisa_mas_abre(self) -> None:
        codigo, out, _ = rodar(Fake(comparacao(n=mod.ARQUIVOS_MAX + 1)))
        self.assertEqual(codigo, 0)
        self.assertIn("diff grande", out)

    def test_formato_sensivel_no_diff_recusa_e_nao_mostra_o_valor(self) -> None:
        for patch, formato in (("+contato fulano@exemplo.invalid\n", "e-mail"), ("+host 10.20.30.40\n", "IPv4"), ("+senha = hunter2abc\n", "credencial")):
            gh = Fake(comparacao(patch=patch))
            codigo, _, err = rodar(gh, "--aplicar")
            self.assertEqual(codigo, 1, patch)
            self.assertIn(formato, err)
            self.assertNotIn("fulano@", err)
            self.assertNotIn("10.20", err)
            self.assertNotIn("hunter2abc", err)
            self.assertEqual(gh.escritas(), [])

    def test_nome_de_teste_em_snake_case_nao_e_sequencia_longa_mas_chave_aleatoria_e(self) -> None:
        nome = "+def test_a_primeira_leitura_agenda_a_pesquisa_e_dois_alvos_pagam_uma_vez(self):\n"
        self.assertEqual(rodar(Fake(comparacao(patch=nome)))[0], 0)
        for aleatoria in ("+x = 'Zk3pQ9vL2mN8xR5tY7wB4cD6fH1jG0aS9eU3iO5'\n", "+y = 'abcdefghijklmnopqrstuvwxyzabcdefghijklmnop'\n"):
            codigo, _, err = rodar(Fake(comparacao(patch=aleatoria)))
            self.assertEqual(codigo, 1, aleatoria)
            self.assertIn("sequência longa", err)
            self.assertNotIn("Zk3pQ9", err)

    def test_valores_falsos_conferidos_liberam_com_aviso(self) -> None:
        gh = Fake(comparacao(patch="+contato fulano@exemplo.invalid\n"))
        codigo, out, _ = rodar(gh, "--aplicar", "--valores-falsos")
        self.assertEqual(codigo, 0)
        self.assertIn("liberado por --valores-falsos", out)
        self.assertNotIn("fulano@", out)
        self.assertTrue(gh.escritas())

    def test_linha_removida_com_valor_nao_conta(self) -> None:
        codigo, _, _ = rodar(Fake(comparacao(patch="-contato fulano@exemplo.invalid\n+ok\n")))
        self.assertEqual(codigo, 0)


class Ondas(unittest.TestCase):
    def test_quatro_na_ultima_hora_barra_e_diz_quando_cabe(self) -> None:
        gh = Fake(prs=[pr_de_revisao(m) for m in (50, 40, 30, 5)])
        codigo, _, err = rodar(gh, "--aplicar")
        self.assertEqual(codigo, 2)
        self.assertIn("a partir de 12:10Z", err)  # o mais antigo (50 min atrás = 11:10Z) sai da hora às 12:10Z
        self.assertEqual(gh.escritas(), [])

    def test_so_conta_titulo_de_revisao_e_so_a_ultima_hora(self) -> None:
        prs = [pr_de_revisao(10, "feat: outra coisa"), pr_de_revisao(70), pr_de_revisao(90), pr_de_revisao(100), pr_de_revisao(110), pr_de_revisao(5)]
        ok, msg = mod.vaga_da_onda(REPO, AGORA, Fake(prs=prs))
        self.assertTrue(ok, msg)
        self.assertIn("1/4 na hora", msg)

    def test_vinte_na_janela_de_5_horas_barra(self) -> None:
        prs = [pr_de_revisao(65 + i * 10) for i in range(20)]  # 65 a 255 min atrás: nenhum na última hora, 20 na janela
        codigo, _, err = rodar(Fake(prs=prs), "--aplicar")
        self.assertEqual(codigo, 2)
        self.assertIn("5 h", err)


class Entradas(unittest.TestCase):
    def test_rotulo_que_nao_existe_abre_sem_rotulo(self) -> None:
        gh = Fake(rotulos=())
        codigo, out, _ = rodar(gh, "--aplicar")
        self.assertEqual(codigo, 0)
        self.assertIn("não existe", out)
        criar = next(c for c in gh.escritas() if c[:2] == ("pr", "create"))
        self.assertNotIn("--label", criar)

    def test_titulo_e_argumentos_tortos_saem_com_1_sem_chamar_o_gh(self) -> None:
        for titulo in ("com `crase`", "com @mencao", "a" * 81, "fale com fulano@exemplo.invalid", ""):
            gh = Fake()
            self.assertEqual(rodar(gh, titulo=titulo)[0], 1, titulo)
            self.assertEqual(gh.chamadas, [])
        self.assertEqual(rodar(Fake(), id_="31;rm")[0], 1)
        self.assertEqual(rodar(Fake(), head="feat/../x")[0], 1)

    def test_resumo_inteiro_e_validado_e_repo_ou_ref_estranhos_saem_com_1(self) -> None:
        longo = "a" * 100 + " fale com fulano@exemplo.invalid"
        gh = Fake()
        self.assertEqual(rodar(gh, "--resumo", longo)[0], 1)  # o e-mail está depois do caractere 80
        self.assertEqual(rodar(gh, "--resumo", "x" * 401)[0], 1)
        self.assertEqual(rodar(gh, "--resumo", "veja `isso` @alguem")[0], 1)
        self.assertEqual(gh.chamadas, [])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(mod.main(["--repo", REPO, "--id", "31.1", "--titulo", "t", "--head=-x"], gh=Fake(), agora=AGORA), 1)
            self.assertEqual(mod.main(["--repo", "../x", "--id", "31.1", "--titulo", "t", "--head", "feat/a"], gh=Fake(), agora=AGORA), 1)

    def test_sem_patch_ou_lista_truncada_avisa_que_a_varredura_ficou_parcial(self) -> None:
        sem_patch = comparacao()
        sem_patch["files"][0]["patch"] = ""
        self.assertIn("varredura de formato sensível ficou parcial", rodar(Fake(sem_patch))[1])

    def test_pr_que_falha_depois_de_criar_a_base_avisa_da_branch(self) -> None:
        codigo, _, err = rodar(Fake(comparacao(status="diverged"), falha_em="pr create"), "--aplicar")
        self.assertEqual(codigo, 1)
        self.assertIn("revisao/base-31.241 foi criada e o PR falhou", err)

    def test_erro_do_gh_sai_com_1_sem_repo_na_mensagem(self) -> None:
        codigo, _, err = rodar(Fake(falha_em="compare"), "--aplicar")
        self.assertEqual(codigo, 1)
        self.assertNotIn(REPO, err)

    def test_gh_real_nao_poe_a_saida_do_gh_na_mensagem(self) -> None:
        import inspect
        self.assertNotIn("stderr", inspect.getsource(mod.gh_real).split("raise")[1])


if __name__ == "__main__":
    unittest.main()
