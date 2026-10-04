"""scripts/candidatos-do-portal.py (item 29.72): só a lógica — nada de rede, nada de banco.

O que estes testes protegem:
  1. só GET, e só a rota do relatório (`/api/aprendizado/falhas`, JSON), a de sempre e a da camada `pessoa`;
  2. candidato é o grupo aberto, sem item do plano e com o mínimo de ocorrências; o resto é contado pelo motivo;
  3. o exemplo leva ids, nunca o texto do erro; a frente é sugestão pela camada; o arquivo diz a regra (nada entra
     no plano sem número);
  4. o token vai no cabeçalho e nunca para a saída, nem no erro; só para o central local (29.76);
  5. o denominador da amostra de lote é todo exemplo com run_id, achado no banco ou não (29.76).
"""
from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("candidatos_do_portal", ROOT / "scripts" / "candidatos-do-portal.py")
mod = importlib.util.module_from_spec(_spec)
sys.modules["candidatos_do_portal"] = mod
_spec.loader.exec_module(mod)  # type: ignore[union-attr]

TOKEN = "tok-super-secreto-123456"
AGORA = datetime(2026, 10, 4, 16, 30, tzinfo=timezone.utc)


def _grupo(gid: str, camada: str, ocorrencias: int, custo: float, **extra: object) -> dict[str, object]:
    g: dict[str, object] = {
        "id": gid, "camada": camada, "tipo": "interrompida", "app": "com.x", "capability": "*", "tela": "",
        "titulo": f"com.x · {gid}", "ocorrencias": ocorrencias, "execucoes": ocorrencias, "aparelhos": 2,
        "intervencoes": 1, "custo_total": custo, "estado": "open", "plan_item": None,
        "onde_alterar": {"arquivos": ["backend/app/taskqueue/scheduler.py"], "prova": "reinício com execução"},
        "exemplos": [{"run_id": f"r-{gid}-{i}", "attempt_id": f"r-{gid}-{i}:a1", "quando": "2026-10-04T08:00:00Z",
                      "erro": "o app pede autenticação de lucas@exemplo.test"} for i in range(5)]}
    g.update(extra)
    return g


GERAL = {"commit": "abc1234", "itens": [
    _grupo("fk-barato", "execucao", 4, 0.5), _grupo("fk-caro", "verificacao", 9, 7.0),
    _grupo("fk-com-item", "aparelho", 9, 3.0, plan_item="29.21"),
    _grupo("fk-corrigido", "automacao", 9, 3.0, estado="fixed"),
    _grupo("fk-raro", "plano", 2, 9.0)],
    "verificacao": [], "propostas": [
        {"id": "fk-prop", "tipo": "investigar", "parent_id": "fk-caro", "estado": "open", "app": "com.x",
         "titulo": "investigar fk-caro", "detalhe": "causa provável verificador"},
        {"id": "fk-prop-fechada", "tipo": "investigar", "parent_id": "fk-caro", "estado": "fixed"}]}
PESSOA = {"commit": "abc1234", "itens": [_grupo("fk-pessoa", "pessoa", 5, 1.0), _grupo("fk-caro", "verificacao", 9, 7.0)],
          "verificacao": [], "propostas": []}


class Fake:
    def __init__(self, corpos: list[dict[str, object]] | None = None, erro: Exception | None = None):
        self.corpos = list(corpos or [GERAL, PESSOA])
        self.erro = erro
        self.pedidos: list[tuple[str, dict[str, str]]] = []

    def __call__(self, endereco: str, cab: dict[str, str]) -> str:
        self.pedidos.append((endereco, cab))
        if self.erro is not None:
            raise self.erro
        return json.dumps(self.corpos.pop(0))


class TestCandidatos(unittest.TestCase):
    def _rodar(self, fake: Fake, *args: str) -> tuple[int, str, str, dict[str, object] | None]:
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "c.json"
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = mod.main(["--saida", str(destino), *args], buscador=fake, agora=AGORA, token=TOKEN)
            corpo = json.loads(destino.read_text(encoding="utf-8")) if destino.exists() else None
            return rc, out.getvalue(), err.getvalue(), corpo

    def test_so_get_e_so_a_rota_do_relatorio_com_a_camada_pessoa(self) -> None:
        self.assertEqual(mod.METODO, "GET")
        fake = Fake()
        rc, *_ = self._rodar(fake)
        self.assertEqual(rc, 0)
        caminhos = [urllib.parse.urlsplit(e) for e, _ in fake.pedidos]
        self.assertEqual({c.path for c in caminhos}, {"/api/aprendizado/falhas"})
        consultas = [urllib.parse.parse_qs(c.query) for c in caminhos]
        self.assertEqual([q.get("camada") for q in consultas], [None, ["pessoa"]])
        self.assertTrue(all(q["formato"] == ["json"] for q in consultas))

    def test_candidato_e_o_aberto_sem_item_e_com_o_minimo(self) -> None:
        _, _, _, corpo = self._rodar(Fake())
        assert corpo is not None
        chaves = [c["chave"] for c in corpo["candidatos"]]   # type: ignore[index]
        # Do mais caro ao mais barato, sem repetir o grupo que veio nos dois relatórios; a proposta aberta no fim.
        self.assertEqual(chaves, ["fk-caro", "fk-pessoa", "fk-barato", "fk-prop"])
        self.assertEqual(corpo["fora"], {"com_item_do_plano": 1, "fora_de_aberto": 1, "abaixo_do_minimo": 1})
        self.assertEqual(corpo["regra"], mod.REGRA)
        self.assertIn("número", corpo["regra"])                 # type: ignore[operator]

    def test_exemplo_leva_ids_e_nunca_o_texto_do_erro(self) -> None:
        _, _, _, corpo = self._rodar(Fake())
        assert corpo is not None
        caro = corpo["candidatos"][0]                           # type: ignore[index]
        self.assertEqual(len(caro["exemplos"]), mod.EXEMPLOS)
        self.assertEqual(set(caro["exemplos"][0]), {"run_id", "attempt_id", "quando"})
        self.assertNotIn("exemplo.test", json.dumps(corpo, ensure_ascii=False))
        self.assertEqual(caro["frente_sugerida"], "jev")
        pessoa = next(c for c in corpo["candidatos"] if c["chave"] == "fk-pessoa")   # type: ignore[union-attr]
        self.assertEqual((pessoa["origem"], pessoa["frente_sugerida"]), ("pessoa", "canais"))
        prop = corpo["candidatos"][-1]                          # type: ignore[index]
        self.assertEqual((prop["origem"], prop["causa"]["camada"]), ("proposta", "verificacao"))

    def test_minimo_configuravel(self) -> None:
        _, _, _, corpo = self._rodar(Fake(), "--minimo", "5")
        assert corpo is not None
        self.assertEqual([c["chave"] for c in corpo["candidatos"]], ["fk-caro", "fk-pessoa", "fk-prop"])  # type: ignore[index]

    def test_token_no_cabecalho_e_nunca_na_saida_nem_no_erro(self) -> None:
        fake = Fake()
        _, out, _, _ = self._rodar(fake)
        self.assertEqual(fake.pedidos[0][1].get("Authorization"), f"Bearer {TOKEN}")
        self.assertNotIn(TOKEN, out)
        erro = Fake(erro=urllib.error.URLError(f"falhou com {TOKEN}"))
        rc, out, err, corpo = self._rodar(erro)
        self.assertEqual((rc, corpo), (2, None))
        self.assertNotIn(TOKEN, out + err)
        self.assertIn("***", err)

    def test_amostra_de_lote_e_ultima_ocorrencia_rebaixam(self) -> None:
        """Ajuste da orquestradora: o grupo de amostra toda nossa (lote, ensaio, prova) ou parado há mais de 7 dias vai
        para o fim, com o motivo; o resto segue pelo custo. Sem leitura do banco, a amostra é `null`."""
        geral = json.loads(json.dumps(GERAL))
        for g in geral["itens"]:
            g["ultima"] = "2026-10-04T08:00:00Z"
        next(g for g in geral["itens"] if g["id"] == "fk-barato")["ultima"] = "2026-09-25T08:00:00Z"
        nossos = {f"r-fk-caro-{i}": True for i in range(5)} | {"r-fk-pessoa-0": True, "r-fk-pessoa-1": False}
        pedidos: list[list[str]] = []

        def de_lote(ids: list[str]) -> dict[str, bool]:
            pedidos.append(list(ids))
            return {i: nossos[i] for i in ids if i in nossos}

        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "c.json"
            with redirect_stdout(io.StringIO()):
                mod.main(["--saida", str(destino)], buscador=Fake([geral, PESSOA]), agora=AGORA, token=None,
                         de_lote=de_lote)
            corpo = json.loads(destino.read_text(encoding="utf-8"))
        por = {c["chave"]: c for c in corpo["candidatos"]}
        self.assertEqual([c["chave"] for c in corpo["candidatos"]], ["fk-pessoa", "fk-caro", "fk-barato", "fk-prop"])
        self.assertEqual((por["fk-caro"]["amostra_de_lote"], por["fk-pessoa"]["amostra_de_lote"]), ("3 de 3", "1 de 3"))
        self.assertIn("execução nossa", por["fk-caro"]["rebaixado"])
        self.assertIsNone(por["fk-pessoa"]["rebaixado"])
        self.assertIn("7 dias", por["fk-barato"]["rebaixado"])
        self.assertEqual((por["fk-barato"]["dias_sem_ocorrer"], por["fk-barato"]["amostra_de_lote"]), (9.4, None))
        self.assertEqual(len(pedidos), 1)                       # uma leitura só, com os ids dos exemplos
        self.assertEqual(len(pedidos[0]), 3 * 3)

    def test_de_lote_no_banco_so_le_e_reconhece_as_marcas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            banco = Path(tmp) / "poc.sqlite3"
            with closing(sqlite3.connect(banco)) as c, c:
                c.execute("CREATE TABLE runs(id TEXT, idempotency_key TEXT, prova_fluxo_id TEXT)")
                c.executemany("INSERT INTO runs VALUES (?,?,?)", [
                    ("r1", "lote:android:x", None), ("r2", "ensaio:ig-01", None), ("r3", "chave-do-painel", "pf-1"),
                    ("r4", "chave-do-painel", None)])
            ler = mod.de_lote_no_banco(banco)
            self.assertEqual(ler(["r1", "r2", "r3", "r4", "r9"]), {"r1": True, "r2": True, "r3": True, "r4": False})
            self.assertEqual(ler([]), {})
            self.assertEqual(mod.de_lote_no_banco(Path(tmp) / "nao-existe.sqlite3")(["r1"]), {})
            with self.assertRaises(sqlite3.OperationalError):    # aberto só para leitura
                with closing(sqlite3.connect(f"file:{banco.as_posix()}?mode=ro", uri=True)) as ro:
                    ro.execute("DELETE FROM runs")


    def test_amostra_conta_o_exemplo_que_o_banco_nao_achou(self) -> None:
        """29.76 (a): 2 exemplos ausentes do banco e 1 de lote dão "1 de 3", e o grupo não é rebaixado. Antes, o
        denominador só contava os achados ("1 de 1") e o grupo ia para o fim."""
        geral = {"commit": "abc", "itens": [_grupo("fk-um", "execucao", 9, 2.0), _grupo("fk-dois", "execucao", 9, 1.0)],
                 "verificacao": [], "propostas": []}
        nossos = {"r-fk-um-0": True}
        saida = mod.montar([geral], minimo=3, agora=AGORA, de_lote=lambda ids: {i: nossos[i] for i in ids if i in nossos})
        por = {c["chave"]: c for c in saida["candidatos"]}
        self.assertEqual((por["fk-um"]["amostra_de_lote"], por["fk-um"]["rebaixado"]), ("1 de 3", None))
        self.assertIsNone(por["fk-dois"]["amostra_de_lote"])
        self.assertEqual([c["chave"] for c in saida["candidatos"]], ["fk-um", "fk-dois"])

    def test_token_so_para_o_central_local(self) -> None:
        """29.76 (b): um `--base` fora desta máquina não recebe o Bearer; o loopback recebe."""
        for base, esperado in (("http://exemplo.test:8000", None), ("https://dev.nvit.com.br/central", None),
                               ("http://localhost:8000", f"Bearer {TOKEN}"), ("http://[::1]:8000", f"Bearer {TOKEN}")):
            with self.subTest(base=base):
                fake = Fake()
                _, out, err, _ = self._rodar(fake, "--base", base)
                self.assertEqual([c.get("Authorization") for _, c in fake.pedidos], [esperado, esperado])
                self.assertNotIn(TOKEN, out + err)

    def test_ultima_sem_fuso_vale_como_utc(self) -> None:
        """29.76 (b): 'ultima' sem fuso derrubava a subtração com TypeError; vale como UTC."""
        self.assertEqual(mod._dias_sem_ocorrer("2026-09-25T08:00:00", AGORA), 9.4)
        self.assertEqual(mod._dias_sem_ocorrer("2026-09-25T08:00:00Z", AGORA), 9.4)
        self.assertIsNone(mod._dias_sem_ocorrer("ontem", AGORA))


if __name__ == "__main__":
    unittest.main()
