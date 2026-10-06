"""scripts/github_rotulos.py (29.155, C5). Prova `simulated`: o `gh` é um falso que grava as chamadas.

Protegem: o ensaio nunca escreve; só o `--aplicar` escreve; nunca há `delete`; rótulo igual não é reescrito; a lista
é válida (cor de 6 dígitos, descrição de até 100 caracteres, sem nome repetido); falha do `gh` sai com código 1.
"""
from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("github_rotulos", ROOT / "scripts" / "github_rotulos.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]

PADRAO = [{"name": "bug", "color": "d73a4a", "description": "Something isn't working"}]


class FakeGh:
    def __init__(self, existentes: list[dict[str, object]], falha: bool = False):
        self.existentes, self.falha, self.chamadas = existentes, falha, []

    def __call__(self, *args: str) -> str:
        self.chamadas.append(args)
        if self.falha:
            raise RuntimeError("gh label list saiu com 1: simulado")
        return json.dumps(self.existentes) if args[:2] == ("label", "list") else ""

    def escritas(self) -> list[tuple[str, ...]]:
        return [a for a in self.chamadas if a[:2] != ("label", "list")]


def _rodar(gh: FakeGh, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        codigo = mod.main(["--repo", "dono/repo", *args], gh=gh)
    return codigo, out.getvalue(), err.getvalue()


class ListaDeRotulos(unittest.TestCase):
    def test_lista_valida_e_sem_repeticao(self) -> None:
        nomes = [n.lower() for n, _, _ in mod.ROTULOS]
        self.assertEqual(len(nomes), len(set(nomes)))
        for nome, cor, descricao in mod.ROTULOS:
            self.assertRegex(cor, r"^[0-9a-f]{6}$", nome)
            self.assertLessEqual(len(descricao), 100, nome)

    def test_tem_o_que_a_coordenacao_pediu(self) -> None:
        nomes = {n for n, _, _ in mod.ROTULOS}
        for esperado in ("ci", "agente", "tamanho:P", "tamanho:M", "tamanho:G", "frente:android", "frente:jev",
                         "frente:aprendizado", "frente:portal", "frente:canais", "frente:github", "frente:desenho"):
            self.assertIn(esperado, nomes)


class Plano(unittest.TestCase):
    def test_rotulo_igual_nao_entra_e_o_divergente_vira_atualizar(self) -> None:
        nome, cor, desc = mod.ROTULOS[0]
        existentes = PADRAO + [{"name": nome.upper(), "color": cor, "description": desc}]
        acoes = mod.plano(existentes)
        self.assertNotIn(nome, [a[1] for a in acoes])
        divergente = PADRAO + [{"name": nome, "color": "000000", "description": desc}]
        self.assertIn(("atualizar", nome, cor, desc), mod.plano(divergente))

    def test_todos_faltando_vira_criar_de_todos(self) -> None:
        self.assertEqual(len(mod.plano(PADRAO)), len(mod.ROTULOS))
        self.assertEqual({a[0] for a in mod.plano(PADRAO)}, {"criar"})


class Execucao(unittest.TestCase):
    def test_ensaio_le_e_nao_escreve(self) -> None:
        gh = FakeGh(PADRAO)
        codigo, saida, _ = _rodar(gh)
        self.assertEqual(codigo, 0)
        self.assertEqual(gh.escritas(), [])
        self.assertIn("ensaio:", saida)

    def test_aplicar_cria_com_force_e_nunca_apaga(self) -> None:
        gh = FakeGh(PADRAO)
        codigo, saida, _ = _rodar(gh, "--aplicar")
        self.assertEqual(codigo, 0)
        self.assertEqual(len(gh.escritas()), len(mod.ROTULOS))
        for a in gh.escritas():
            self.assertEqual(a[:2], ("label", "create"))
            self.assertIn("--force", a)
            self.assertNotIn("delete", a)
        self.assertIn("aplicado:", saida)

    def test_tudo_em_dia_nao_escreve_nada(self) -> None:
        existentes = [{"name": n, "color": c, "description": d} for n, c, d in mod.ROTULOS]
        gh = FakeGh(existentes)
        codigo, saida, _ = _rodar(gh, "--aplicar")
        self.assertEqual((codigo, gh.escritas()), (0, []))
        self.assertIn("nada a fazer", saida)

    def test_falha_do_gh_sai_com_1(self) -> None:
        codigo, _, err = _rodar(FakeGh(PADRAO, falha=True), "--aplicar")
        self.assertEqual(codigo, 1)
        self.assertIn("erro:", err)

    def test_repo_fora_do_formato_e_recusado(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(mod.main(["--repo", "x; rm -rf /"], gh=FakeGh(PADRAO)), 1)


if __name__ == "__main__":
    unittest.main()
