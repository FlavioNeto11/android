"""scripts/issue_do_pacote.py (29.177): issue a partir do pacote, idempotente, nunca atribui ao agente, recusa texto proibido.
Prova `simulated`: `gh` falso e pacote de mentira em pasta temporária."""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("issue_pacote", ROOT / "scripts" / "issue_do_pacote.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]

PACOTE = """# Item 9.9 — Exemplo de tarefa

**Fase 9 — Teste** · tamanho no plano: P · achados: —

Texto de abertura.

## O trabalho

**Exemplo de tarefa**: ajustar `backend/app/exemplo.py` no aparelho android-06 sem tocar o resto.

## Como a fase fecha (contexto)

fecha quando fecha.
"""


def pasta(texto: str = PACOTE, arquivos=("backend/app/exemplo.py", "../fora/sai demais")) -> Path:
    p = Path(tempfile.mkdtemp())
    (p / "9.9.md").write_text(texto, encoding="utf-8")
    (p / "indice.json").write_text(json.dumps({"9.9": {"arquivos": list(arquivos)}}), encoding="utf-8")
    return p


class FakeGh:
    def __init__(self, existentes=(), rotulos=("tamanho:P", "frente:github", "agente")):
        self.existentes, self.rotulos, self.chamadas = list(existentes), rotulos, []

    def __call__(self, *args: str, entrada: str | None = None) -> str:
        self.chamadas.append((args, entrada))
        if args[:2] == ("issue", "list"):
            return json.dumps(self.existentes)
        if args[:2] == ("label", "list"):
            return json.dumps([{"name": r} for r in self.rotulos])
        if args[:2] == ("issue", "create"):
            return "https://github.com/dono/repo/issues/7\n"
        raise AssertionError(args)

    def criacoes(self):
        return [c for c in self.chamadas if c[0][:2] == ("issue", "create")]


def rodar(gh, pac, *extra, item="9.9"):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        codigo = mod.main([item, "--repo", "dono/repo", *extra], gh=gh, pacotes=pac)
    return codigo, out.getvalue(), err.getvalue()


class Issue(unittest.TestCase):
    def test_ensaio_mostra_e_nao_cria(self) -> None:
        gh = FakeGh()
        codigo, out, _ = rodar(gh, pasta())
        self.assertEqual((codigo, gh.criacoes()), (0, []))
        self.assertIn("Título: 9.9: Exemplo de tarefa", out)
        self.assertIn("<!-- pacote:9.9 -->", out)

    def test_corpo_tem_trabalho_prova_regras_e_arquivos_validos(self) -> None:
        _, out, _ = rodar(FakeGh(), pasta())
        for esperado in ("## O que fazer", "## Prova exigida", "`real`", "`simulated`", "`not_run`", "## Regras", "- `backend/app/exemplo.py`"):
            self.assertIn(esperado, out)
        self.assertNotIn("sai demais", out)  # caminho fora do formato não entra

    def test_nome_do_trabalho_nao_e_reescrito(self) -> None:
        _, out, _ = rodar(FakeGh(), pasta())
        self.assertIn("android-06", out)  # id do parque e nome de imagem (android-36) não são serial: ficam como no pacote

    def test_aplicar_cria_com_rotulos_existentes_e_nunca_atribui(self) -> None:
        gh = FakeGh()
        codigo, out, _ = rodar(gh, pasta(), "--aplicar", "--frente", "github")
        (args, entrada), = gh.criacoes()
        self.assertEqual(codigo, 0)
        self.assertIn("issues/7", out)
        self.assertEqual([args[i + 1] for i, x in enumerate(args) if x == "--label"], ["tamanho:P", "frente:github"])
        for proibido in ("-a", "--project"):
            self.assertNotIn(proibido, args)
        self.assertFalse([x for x in args if "assign" in x], "a issue nunca é atribuída (assignee, assignee-me, add-assignee)")
        self.assertIn("<!-- pacote:9.9 -->", entrada)

    def test_rotulo_agente_so_com_a_flag(self) -> None:
        sem = FakeGh()
        rodar(sem, pasta(), "--aplicar")
        self.assertNotIn("agente", sem.criacoes()[0][0])
        com = FakeGh()
        rodar(com, pasta(), "--aplicar", "--agente")
        self.assertIn("agente", com.criacoes()[0][0])
        self.assertNotIn("--assignee", com.criacoes()[0][0])

    def test_etiqueta_agente_nuvem_so_com_a_flag_e_continua_sem_atribuir(self) -> None:
        sem = FakeGh(rotulos=("tamanho:P", "agente-nuvem"))
        rodar(sem, pasta(), "--aplicar")
        self.assertNotIn("agente-nuvem", sem.criacoes()[0][0])
        com = FakeGh(rotulos=("tamanho:P", "agente-nuvem"))
        rodar(com, pasta(), "--aplicar", "--agente-nuvem")
        args, corpo = com.criacoes()[0][0], com.criacoes()[0][1]
        self.assertIn("agente-nuvem", args)
        self.assertFalse([x for x in args if "assign" in x])
        self.assertIn("scripts/agente_nuvem.py", corpo)  # o corpo diz quem atribui

    def test_rotulo_inexistente_e_pulado_com_aviso(self) -> None:
        gh = FakeGh(rotulos=())
        codigo, _, err = rodar(gh, pasta(), "--aplicar")
        self.assertEqual(codigo, 0)
        self.assertNotIn("--label", gh.criacoes()[0][0])
        self.assertIn("foi pulado", err)

    def test_idempotente_aberta_ou_fechada(self) -> None:
        for estado in ("OPEN", "CLOSED"):
            gh = FakeGh(existentes=[{"number": 3, "state": estado, "body": "x\n<!-- pacote:9.9 -->\ny"}])
            codigo, out, _ = rodar(gh, pasta(), "--aplicar")
            self.assertEqual((codigo, gh.criacoes()), (0, []))
            self.assertIn("já existe a issue #3", out)

    def test_marca_de_outro_item_nao_conta(self) -> None:
        gh = FakeGh(existentes=[{"number": 3, "state": "OPEN", "body": "<!-- pacote:9.99 -->"}])
        rodar(gh, pasta(), "--aplicar")
        self.assertEqual(len(gh.criacoes()), 1)

    def test_recusa_texto_proibido_sem_imprimir_o_trecho(self) -> None:
        for sujo, nome in (("fale com alguem@exemplo.invalid", "e-mail"), ("host 10.1.2.3", "IPv4"), ("emulator-5554 caiu", "serial"),
                           ("siga @alguem_x", "arroba"), ("token " + "a" * 40, "sequência"), (r"em C:\Users\x\y", "caminho"),
                           (r"em C:\git\android\x", "caminho"), ("em /home/fulano/x", "caminho"), ("usa Bearer abc12345", "credencial"),
                           ("senha=hunter2", "credencial"), ("chave AKIAABCDEFGH1234", "chave"), ("fale com @ab", "arroba")):
            gh = FakeGh()
            codigo, out, err = rodar(gh, pasta(PACOTE.replace("sem tocar o resto", sujo)), "--aplicar")
            self.assertEqual((codigo, gh.criacoes()), (1, []), nome)
            self.assertIn(nome, err)
            for trecho in ("alguem@", "10.1.2.3", "emulator-5554", "@alguem_x", "a" * 40, r"C:\Users", "hunter2", "abc12345", "AKIAABCDEFGH", "/home/fulano"):
                self.assertNotIn(trecho, err + out)

    def test_pacote_inexistente_ou_mal_formado(self) -> None:
        self.assertEqual(rodar(FakeGh(), pasta(), item="9.8")[0], 1)
        self.assertEqual(rodar(FakeGh(), pasta("# Item 9.9 — x\n\nsem trabalho"))[0], 1)

    def test_trabalho_longo_e_cortado(self) -> None:
        longo = PACOTE.replace("sem tocar o resto.", "palavra " * 800)
        _, out, _ = rodar(FakeGh(), pasta(longo))
        self.assertIn("texto cortado", out)

    def test_argumentos_invalidos(self) -> None:
        self.assertEqual(rodar(FakeGh(), pasta(), item="9.9; ls")[0], 1)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(mod.main(["9.9", "--repo", "x y"], gh=FakeGh(), pacotes=pasta()), 1)

    def test_erro_do_gh_sai_com_1_e_nao_cria(self) -> None:
        def falha(*a, entrada=None):
            raise RuntimeError("gh issue saiu com 1: simulado")
        self.assertEqual(rodar(falha, pasta(), "--aplicar")[0], 1)


if __name__ == "__main__":
    unittest.main()
