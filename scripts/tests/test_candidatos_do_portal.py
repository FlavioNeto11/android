"""scripts/candidatos-do-portal.py (item 29.72): só a lógica — nada de rede, nada de banco.

O que estes testes protegem:
  1. só GET, e só a rota do relatório (`/api/aprendizado/falhas`, JSON), a de sempre e a da camada `pessoa`;
  2. candidato é o grupo aberto, sem item do plano e com o mínimo de ocorrências; o resto é contado pelo motivo;
  3. o exemplo leva ids, nunca o texto do erro; a frente é sugestão pela camada; o arquivo diz a regra (nada entra
     no plano sem número);
  4. o token vai no cabeçalho e nunca para a saída, nem no erro.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
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


if __name__ == "__main__":
    unittest.main()
