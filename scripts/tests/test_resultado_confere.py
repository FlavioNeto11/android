"""scripts/resultado_confere.py (29.190): conferência do JSON de resultado antes do `aplicar`. Prova `simulated`: arquivos temporários."""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("resultado_confere", ROOT / "scripts" / "resultado_confere.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]

IDS = {"29.169", "29.185", "29.188"}


def item(**campos) -> dict:
    base = {
        "id": "29.185",
        "status": "implemented",
        "proof": "simulated",
        "evidence": "scripts/tests/test_x.py::test_a passou com o gh falso",
        "blocker": "",
    }
    base.update(campos)
    return base


def resultado(*itens, solicitados=None) -> dict:
    return {"resultados": [{"grupo": "g", "modelo": "m", "esforco": "high",
                            "solicitados": solicitados if solicitados is not None else [i["id"] for i in itens], "items": list(itens)}]}


def escrever(valor, nome="r.json") -> Path:
    pasta = Path(tempfile.mkdtemp())
    caminho = pasta / nome
    caminho.write_text(json.dumps(valor), encoding="utf-8")
    return caminho


class Regras(unittest.TestCase):
    def test_item_correto_nao_tem_problema(self) -> None:
        reais = item(id="29.169", proof="real",
                     evidence="07/10/2026 01:18Z, runner hospedado ubuntu-latest, run 37556472756, commit c2cf9a31")
        nao = item(id="29.188", status="partial", proof="not_run", evidence="", blocker="falta o primeiro disparo real")
        self.assertEqual(mod.problemas(resultado(item(), reais, nao), IDS), [])

    def test_done_e_prova_descritiva_sao_recusados_com_a_lista_do_que_vale(self) -> None:
        achados = mod.problemas(resultado(item(status="done", proof="scripts/tests/test_x.py (gh falso)")), IDS)
        texto = "\n".join(achados)
        self.assertIn("status 'done'", texto)
        self.assertIn("implemented", texto)
        self.assertIn("proof 'scripts/tests/test_x.py", texto)
        self.assertIn("real, simulated", texto.replace("not_run, ", ""))

    def test_lista_todos_os_problemas_e_nao_so_o_primeiro(self) -> None:
        achados = mod.problemas(resultado(item(status="done"), item(id="29.188", proof="x"), item(id="29.999")), IDS)
        self.assertEqual(len(achados), 3)
        self.assertTrue(any("29.999" in a and "não existe" in a for a in achados))

    def test_real_exige_data_maquina_e_commit_ou_id(self) -> None:
        sem_data = mod.problemas(resultado(item(proof="real", evidence="runner ubuntu, commit abcdef1")), IDS)
        self.assertTrue(any("data" in a for a in sem_data))
        sem_commit = mod.problemas(resultado(item(proof="real", evidence="07/10/2026 no runner hospedado")), IDS)
        self.assertTrue(any("commit ou id de execução" in a for a in sem_commit))
        sem_maquina = mod.problemas(resultado(item(proof="real", evidence="07/10/2026, commit abcdef1")), IDS)
        self.assertTrue(any("máquina ou runner" in a for a in sem_maquina))

    def test_real_com_texto_fraco_nao_passa(self) -> None:
        """Achado do revisor: palavra só de a-f, número de 7 dígitos, telefone e data solta não são commit, id nem data."""
        fraco = "ligou 11987654321 em 3/4 no runner; defaced 1234567"
        achados = mod.problemas(resultado(item(proof="real", evidence=fraco)), IDS)
        self.assertTrue(any("data" in a and "commit ou id de execução" in a for a in achados), achados)

    def test_mensagem_nao_ecoa_campo_comprido(self) -> None:
        longo = "x" * 500
        achados = mod.problemas(resultado(item(status=longo, proof=longo)), IDS)
        for a in achados:
            self.assertLess(len(a), 200)

    def test_simulated_exige_arquivo_teste_e_not_run_exige_motivo(self) -> None:
        self.assertTrue(mod.problemas(resultado(item(evidence="funcionou no meu teste")), IDS))
        sem_motivo = mod.problemas(resultado(item(status="partial", proof="not_run", evidence="", blocker="")), IDS)
        self.assertTrue(any("not_run" in a for a in sem_motivo))

    def test_implemented_sem_evidencia_e_blocked_sem_motivo(self) -> None:
        self.assertTrue(mod.problemas(resultado(item(evidence="")), IDS))
        self.assertTrue(mod.problemas(resultado(item(status="blocked", proof="not_run", evidence="", blocker="")), IDS))

    def test_solicitado_sem_linha_e_estrutura_errada(self) -> None:
        achados = mod.problemas(resultado(item(), solicitados=["29.185", "29.188"]), IDS)
        self.assertTrue(any(a.startswith("29.188") and "sem linha" in a for a in achados))
        for torto in ([], {}, {"resultados": []}, {"resultados": [{"items": "x"}]}, {"resultados": [{"items": [1]}]}):
            self.assertTrue(mod.problemas(torto, IDS), torto)


class Gravar(unittest.TestCase):
    def test_rascunho_ruim_nao_grava_nada(self) -> None:
        rascunho = escrever(resultado(item(status="done")))
        destino = rascunho.parent / "final.json"
        self.assertTrue(mod.gravar(rascunho, destino, IDS))
        self.assertFalse(destino.exists())
        self.assertFalse(destino.with_suffix(".json.tmp").exists())

    def test_rascunho_bom_grava_igual_e_sem_sobra(self) -> None:
        rascunho = escrever(resultado(item()))
        destino = rascunho.parent / "final.json"
        self.assertEqual(mod.gravar(rascunho, destino, IDS), [])
        self.assertEqual(destino.read_text(encoding="utf-8"), rascunho.read_text(encoding="utf-8"))
        self.assertFalse(destino.with_suffix(".json.tmp").exists())

    def test_json_ilegivel_vira_problema(self) -> None:
        ruim = Path(tempfile.mkdtemp()) / "x.json"
        ruim.write_text("{nao é json", encoding="utf-8")
        self.assertTrue(mod.conferir_arquivo(ruim, IDS))
        self.assertTrue(mod.conferir_arquivo(ruim.parent / "nao-existe.json", IDS))


class LinhaDeComando(unittest.TestCase):
    def _main(self, *args) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            return mod.main([str(a) for a in args]), out.getvalue()

    def test_codigo_de_saida_e_mensagem(self) -> None:
        # 29.185 existe na tabela do plano da main; o ID vem do próprio plano do checkout
        bom = escrever(resultado(item()))
        ruim = escrever(resultado(item(status="done")))
        self.assertEqual(self._main(bom)[0], 0)
        codigo, saida = self._main(bom, ruim)
        self.assertEqual(codigo, 1)
        self.assertIn("1 problema(s)", saida)
        self.assertIn("ok", saida)

    def test_gravar_pela_linha_de_comando(self) -> None:
        bom = escrever(resultado(item()))
        destino = bom.parent / "final.json"
        self.assertEqual(self._main("--gravar", bom, destino)[0], 0)
        self.assertTrue(destino.exists())
        ruim = escrever(resultado(item(status="done")), "ruim.json")
        destino2 = ruim.parent / "final.json"
        codigo, saida = self._main("--gravar", ruim, destino2)
        self.assertEqual(codigo, 1)
        self.assertIn("NÃO gravado", saida)
        self.assertFalse(destino2.exists())

    def test_sem_argumento_e_erro(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            mod.main([])

    def test_ids_do_plano_incluem_os_da_tabela(self) -> None:
        ids = mod.ids_do_plano(mod._plano())
        self.assertIn("29.185", ids)
        self.assertTrue(all("." in i for i in ids))

    def test_so_le_nunca_chama_rede(self) -> None:
        texto = (ROOT / "scripts" / "resultado_confere.py").read_text(encoding="utf-8")
        for proibido in ("subprocess", "urllib", "requests", "socket"):
            self.assertNotIn(f"import {proibido}", texto)


if __name__ == "__main__":
    unittest.main()
