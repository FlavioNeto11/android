"""scripts/aprendizado-backlog.py (ADR-054, decisão 7): só a lógica — nada de rede, nada de banco.

O que estes testes protegem:
  1. só GET, e só a rota do relatório (`/api/aprendizado/falhas`, formato md): o script nunca escreve no central;
  2. `--retroativo` tem efeito de verdade (pede o legado classificado na leitura); sem ele, só o que a execução
     classificou ao gravar;
  3. o arquivo datado em `data/aprendizado/backlog-AAAA-MM-DD.md` com o md do central, e o top N (a skill `retomar`
     mostra o top 5) tirado só da seção 1;
  4. o token vai no cabeçalho e nunca para a saída, nem no erro.
"""
from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("aprendizado_backlog", ROOT / "scripts" / "aprendizado-backlog.py")
mod = importlib.util.module_from_spec(_spec)
sys.modules["aprendizado_backlog"] = mod
_spec.loader.exec_module(mod)  # type: ignore[union-attr]

TOKEN = "tok-super-secreto-123456"
MD = """# O que mais falha — 2026-09-29

Janela: 14 dias · commit implantado: abc1234 · sem IA.
Inclui o legado classificado na leitura (retroativo).

## 1. O que mais falha (top 20; 7 grupos, 2 abaixo do mínimo)

| chave | camada | o quê | ocorrências (retroativas) | taxa | US$ | min | intervenções | 7 d × 7 d | estado | onde alterar |
|---|---|---|---|---|---|---|---|---|---|---|
| fk-aaaaaaaaaa | verificacao | com.x · OPEN_POST: falso positivo do verificador | 3 (0) | 10% | 0,00 | 0.0 | 0 | 3×0 nova | open | executor.py |
| fk-bbbbbbbbbb | aparelho | com.x · OPEN_POST: o app parou de responder (ANR) | 5 (5) | 20% | 0,40 | 12.0 | 1 | 5×0 nova | open | manager.py |
| fk-cccccccccc | aparelho | com.x · SEND_DM: prazo da etapa esgotado | 4 (4) | 30% | 0,10 | 9.0 | 0 | 4×0 nova | planned 20.3 | manager.py |

- **fk-aaaaaaaaaa** — prova sugerida: x.

## 2. Verificação (falso positivo, falso negativo, confirmações à mão)

| chave | o quê | ocorrências | intervenções | última |
|---|---|---|---|---|
| fk-dddddddddd | com.x · OPEN_POST: falso negativo do verificador | 1 | 0 | 2026-09-28 |
"""


class Pedido(unittest.TestCase):
    def test_so_get_na_rota_do_relatorio_em_md(self) -> None:
        endereco = mod.url("http://127.0.0.1:8000/", dias=30, retroativo=True, limite=20)
        partes = urllib.parse.urlsplit(endereco)
        self.assertEqual(partes.path, "/api/aprendizado/falhas")
        consulta = dict(urllib.parse.parse_qsl(partes.query))
        self.assertEqual(consulta, {"dias": "30", "formato": "md", "retroativo": "1", "limite": "20"})
        filtrado = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(mod.url(
            "http://x", dias=7, retroativo=False, app="com.instagram.android", camada="aparelho", limite=5,
            simulados=True)).query))
        self.assertEqual(filtrado["retroativo"], "0")
        self.assertEqual((filtrado["app"], filtrado["camada"], filtrado["simulados"]),
                         ("com.instagram.android", "aparelho", "1"))
        self.assertEqual(mod.METODO, "GET")

    def test_retroativo_tem_efeito(self) -> None:
        pedidos: list[str] = []

        def buscador(endereco: str, cabecalhos: dict[str, str]) -> str:
            pedidos.append(endereco)
            return MD

        with tempfile.TemporaryDirectory() as pasta, redirect_stdout(io.StringIO()):
            mod.main(["--saida", pasta], buscador=buscador, hoje=date(2026, 9, 29), token=None)
            mod.main(["--saida", pasta, "--retroativo", "--dias", "30"], buscador=buscador,
                     hoje=date(2026, 9, 29), token=None)
        sem, com = (dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(p).query)) for p in pedidos)
        self.assertEqual((sem["retroativo"], sem["dias"]), ("0", "14"))
        self.assertEqual((com["retroativo"], com["dias"]), ("1", "30"))

    def test_token_no_cabecalho_e_sem_token_nada(self) -> None:
        self.assertEqual(mod.cabecalhos(TOKEN)["Authorization"], f"Bearer {TOKEN}")
        self.assertNotIn("Authorization", mod.cabecalhos(None))


class Saida(unittest.TestCase):
    def test_grava_o_arquivo_datado_e_imprime_o_top(self) -> None:
        recebidos: list[dict[str, str]] = []

        def buscador(endereco: str, cabecalhos: dict[str, str]) -> str:
            recebidos.append(cabecalhos)
            return MD

        with tempfile.TemporaryDirectory() as pasta:
            saida = io.StringIO()
            with redirect_stdout(saida):
                codigo = mod.main(["--saida", pasta, "--top", "2"], buscador=buscador, hoje=date(2026, 9, 29),
                                  token=TOKEN)
            self.assertEqual(codigo, 0)
            arquivo = Path(pasta) / "backlog-2026-09-29.md"
            self.assertEqual(arquivo.read_text(encoding="utf-8"), MD)
        texto = saida.getvalue()
        self.assertIn("backlog-2026-09-29.md", texto)
        self.assertIn("fk-aaaaaaaaaa", texto)
        self.assertIn("fk-bbbbbbbbbb", texto)
        self.assertNotIn("fk-cccccccccc", texto)                      # --top 2
        self.assertNotIn("fk-dddddddddd", texto)                      # a seção 2 não é o topo
        self.assertNotIn(TOKEN, texto)
        self.assertEqual(recebidos[0]["Authorization"], f"Bearer {TOKEN}")

    def test_resumo_respeita_a_barra_escapada(self) -> None:
        linha = ("| fk-eeeeeeeeee | conhecimento_do_app | com.x · OPEN_FEED: coleta vazia (tela a\\|b) | 3 (3) | 5% | "
                 "0,10 | 1.0 | 0 | 3×0 nova | open | telas.yaml |")
        self.assertEqual(mod.resumo(linha), "fk-eeeeeeeeee · conhecimento_do_app · com.x · OPEN_FEED: coleta vazia "
                                            "(tela a|b) · 3 (3) ocorrências · US$ 0,10 · open")

    def test_topo_so_da_secao_um(self) -> None:
        self.assertEqual([c.split("|")[1].strip() for c in mod.topo(MD, 5)],
                         ["fk-aaaaaaaaaa", "fk-bbbbbbbbbb", "fk-cccccccccc"])
        self.assertEqual(mod.topo("# vazio\n\n## 1. O que mais falha\n\nNenhum grupo.\n", 5), [])

    def test_erro_de_rede_nao_mostra_o_token_e_nao_grava(self) -> None:
        def quebra(endereco: str, cabecalhos: dict[str, str]) -> str:
            raise urllib.error.URLError(f"conexão recusada ({cabecalhos.get('Authorization')})")

        with tempfile.TemporaryDirectory() as pasta:
            err, out = io.StringIO(), io.StringIO()
            with redirect_stderr(err), redirect_stdout(out):
                codigo = mod.main(["--saida", pasta], buscador=quebra, hoje=date(2026, 9, 29), token=TOKEN)
            self.assertNotEqual(codigo, 0)
            self.assertEqual(list(Path(pasta).iterdir()), [])
        self.assertNotIn(TOKEN, err.getvalue() + out.getvalue())
        self.assertIn("/api/aprendizado/falhas", err.getvalue())


if __name__ == "__main__":
    unittest.main()
