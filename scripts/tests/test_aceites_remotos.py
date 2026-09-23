"""scripts/aceites_remotos.py (item T.1): só a lógica pura — nada de rede e nada de aparelho.

O que estes testes protegem, nesta ordem de importância:
  1. a trava do `--yes`: sem ele NENHUM pedido HTTP sai (o executor injetado explode se for chamado);
  2. `reset` nunca entra no roteiro padrão (ele apaga os dados do aparelho);
  3. a lista de desfechos não pode divergir da máquina de estados do backend — inclusive `uncertain`, que
     não é terminal para o sistema mas é desfecho para este roteiro;
  4. o token nunca vai para a saída, e texto vindo do backend não quebra a tabela em Markdown.
"""
from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.commands.states import COMMAND_OPEN, COMMAND_TERMINAL, COMMAND_UNSETTLED  # noqa: E402

_spec = importlib.util.spec_from_file_location("aceites_remotos", ROOT / "scripts" / "aceites_remotos.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]


class Roteiro(unittest.TestCase):
    def test_reset_nunca_entra_no_roteiro_padrao(self) -> None:
        self.assertNotIn("reset", mod.roteiro())
        self.assertEqual(mod.roteiro(com_reset=True)[-1], "reset", "reset, quando entra, é o último")

    def test_hibernar_e_acordar_andam_colados(self) -> None:
        verbos = list(mod.roteiro())
        self.assertEqual(verbos[verbos.index("hibernate") + 1], "wake")

    def test_o_plano_termina_um_aparelho_antes_de_comecar_o_outro(self) -> None:
        passos = mod.plano("android-01", "android-13")
        aparelhos = [a for a, _ in passos]
        self.assertEqual(aparelhos, ["android-01"] * 5 + ["android-13"] * 5)
        self.assertEqual([v for _, v in passos[:5]], list(mod.VERBOS_PADRAO))

    def test_aparelho_vazio_nao_vira_passo(self) -> None:
        self.assertEqual([a for a, _ in mod.plano("android-01", "")], ["android-01"] * 5)

    def test_prazo_do_boot_cobre_o_prazo_do_proprio_agente(self) -> None:
        # O agente desiste do boot em 480 s (o `uncertain` de 21/09). Esperar menos que isso é inventar desfecho.
        self.assertGreater(mod.prazo_do_verbo("start"), 480)
        self.assertEqual(mod.prazo_do_verbo("verbo-que-nao-existe"), mod.PRAZO_PADRAO)


class Desfechos(unittest.TestCase):
    def test_a_lista_de_desfechos_e_a_do_backend_mais_o_incerto(self) -> None:
        esperado = {s.value for s in COMMAND_TERMINAL} | {s.value for s in COMMAND_UNSETTLED}
        self.assertEqual(mod.DESFECHOS, esperado)
        self.assertIn("uncertain", mod.DESFECHOS)

    def test_comando_ainda_vivo_nao_e_desfecho(self) -> None:
        for estado in COMMAND_OPEN:
            self.assertFalse(mod.terminou(estado.value), estado.value)
        self.assertFalse(mod.terminou(None))
        self.assertTrue(mod.terminou("succeeded"))


class Saida(unittest.TestCase):
    def test_a_tabela_nao_quebra_com_motivo_que_tem_barra_ou_quebra_de_linha(self) -> None:
        t = mod.tabela([{"instance_id": "android-13", "verbo": "start", "command_id": "c-1",
                         "state": "uncertain", "segundos": 482.4, "reason": "boot|480 s\nestado desconhecido"}])
        corpo = t.splitlines()[2]
        self.assertIn("\\|", corpo, "a barra do motivo tem de sair escapada")
        # 6 colunas => 7 separadores; a barra escapada do motivo não conta como separador.
        self.assertEqual(corpo.replace("\\|", "").count("|"), 7, corpo)
        self.assertIn("482", corpo)

    def test_comando_sem_id_aparece_como_travessao_em_vez_de_none(self) -> None:
        t = mod.tabela([{"instance_id": "android-13", "verbo": "stop", "command_id": None,
                         "state": "rejected", "segundos": None, "reason": None}])
        self.assertNotIn("None", t)

    def test_o_resumo_agrupa_por_aparelho(self) -> None:
        r = mod.resumo([{"instance_id": "android-01", "verbo": "stop", "command_id": "c-1", "state": "succeeded"},
                        {"instance_id": "android-01", "verbo": "start", "command_id": "c-2", "state": "succeeded"},
                        {"instance_id": "android-13", "verbo": "stop", "command_id": "c-3", "state": "failed"}])
        self.assertEqual(len(r.splitlines()), 2)
        self.assertIn("`c-2`", r)

    def test_o_token_nao_sai_nos_cabecalhos_quando_nao_existe_e_sai_uma_vez_quando_existe(self) -> None:
        self.assertNotIn("Authorization", mod.cabecalhos(None))
        self.assertEqual(mod.cabecalhos("t0k3n")["Authorization"], "Bearer t0k3n")


class TravaDoYes(unittest.TestCase):
    def test_sem_yes_nenhum_pedido_sai(self) -> None:
        def explode(*_a: object, **_k: object) -> list[dict[str, object]]:
            raise AssertionError("despachou sem --yes")

        saida = io.StringIO()
        with redirect_stdout(saida):
            codigo = mod.main(["--local", "android-01", "--remoto", "android-13"], executor=explode)
        self.assertEqual(codigo, 0)
        texto = saida.getvalue()
        self.assertIn("NADA foi despachado", texto)
        self.assertIn("POST /api/instances/android-13/actions/start", texto)
        self.assertNotIn("reset", texto)

    def test_sem_yes_com_reset_o_roteiro_avisa_e_ainda_assim_nao_despacha(self) -> None:
        def explode(*_a: object, **_k: object) -> list[dict[str, object]]:
            raise AssertionError("despachou sem --yes")

        saida = io.StringIO()
        with redirect_stdout(saida):
            mod.main(["--local", "android-01", "--remoto", "android-13", "--com-reset"], executor=explode)
        self.assertIn("APAGA os dados", saida.getvalue())


class Espera(unittest.TestCase):
    """`executar` com um `pedir` de mentira: prova o laço de espera sem tocar em HTTP."""

    def _executar(self, respostas: list[dict[str, object]]) -> list[dict[str, object]]:
        chamadas = iter(respostas)
        relogio = {"t": 0.0}

        def pedir(url: str, token: str | None, metodo: str = "GET", **_k: object) -> dict[str, object]:
            return next(chamadas)

        def dormir(s: float) -> None:
            relogio["t"] += s

        return mod.executar("http://x", "android-01", "", False, None, pedir=pedir, dormir=dormir,
                            agora=lambda: relogio["t"])

    def test_espera_ate_o_desfecho_e_registra_o_motivo_do_incerto(self) -> None:
        respostas: list[dict[str, object]] = []
        for i in range(5):  # 5 verbos do roteiro padrão
            respostas += [{"command_id": f"c-{i}", "state": "dispatched"},
                          {"state": "running"},
                          {"state": "uncertain", "reason": "estado desconhecido"}]
        resultados = self._executar(respostas)
        self.assertEqual([r["state"] for r in resultados], ["uncertain"] * 5)
        self.assertEqual(resultados[0]["reason"], "estado desconhecido")
        self.assertEqual(resultados[0]["command_id"], "c-0")

    def test_recusa_sem_command_id_nao_entra_no_laco_de_espera(self) -> None:
        respostas: list[dict[str, object]] = [{"_erro": "HTTP 409", "_detalhe": "device_busy"} for _ in range(5)]
        resultados = self._executar(respostas)  # se entrasse no laço, o iterador esvaziaria e daria StopIteration
        self.assertEqual(len(resultados), 5)
        self.assertEqual(resultados[0]["reason"], "device_busy")
        self.assertIsNone(resultados[0]["command_id"])

    def test_consulta_que_falha_no_meio_vira_motivo_em_vez_de_linha_muda(self) -> None:
        respostas: list[dict[str, object]] = []
        for i in range(5):
            respostas.append({"command_id": f"c-{i}", "state": "dispatched"})
            respostas += [{"_erro": "HTTP 502", "_detalhe": "bad gateway"}] * 400
        resultados = self._executar(respostas)
        self.assertEqual(resultados[0]["state"], "dispatched")
        self.assertIn("HTTP 502", str(resultados[0]["reason"]))

    def test_o_prazo_corta_a_espera_em_vez_de_esperar_para_sempre(self) -> None:
        respostas: list[dict[str, object]] = []
        for i in range(5):
            respostas.append({"command_id": f"c-{i}", "state": "dispatched"})
            respostas += [{"state": "running"}] * 400  # nunca chega a desfecho
        resultados = self._executar(respostas)
        self.assertEqual(len(resultados), 5)
        self.assertLessEqual(resultados[0]["segundos"], mod.prazo_do_verbo("stop") + 3.0)


class ScriptsDoRoteiro(unittest.TestCase):
    """Os `.ps1` do roteiro são LIDOS pelo analisador do PowerShell, nunca executados: eles reiniciam o
    backend de produção e mexem no parque. O mesmo que `bash -n` já faz com o instalador do worker."""

    def _parse(self, *nomes: str) -> None:
        import shutil
        import subprocess
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh:  # pragma: no cover - máquina sem PowerShell
            self.skipTest("sem pwsh nesta máquina")
        for nome in nomes:
            caminho = ROOT / "scripts" / nome
            script = ("$e=$null;$t=$null;"
                      f"[System.Management.Automation.Language.Parser]::ParseFile('{caminho}',[ref]$t,[ref]$e)"
                      "|Out-Null; if($e.Count){$e|%{$_.Message};exit 1}; exit 0")
            r = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", script],
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, f"{nome}: {r.stdout}{r.stderr}")

    def test_os_dois_scripts_do_aceite_tem_sintaxe_valida(self) -> None:
        self._parse("aceites-remotos.ps1", "test-restart-recovery.ps1")

    def test_o_wrapper_so_passa_yes_quando_o_dono_pede(self) -> None:
        texto = (ROOT / "scripts" / "aceites-remotos.ps1").read_text(encoding="utf-8")
        self.assertIn("if ($Yes) { $argumentos += '--yes' }", texto)
        self.assertIn("if ($ComReset) { $argumentos += '--com-reset' }", texto)

    def test_o_ensaio_de_reinicio_confere_comando_em_voo_e_volta_do_worker(self) -> None:
        texto = (ROOT / "scripts" / "test-restart-recovery.ps1").read_text(encoding="utf-8")
        self.assertIn("api/commands?limit=200", texto)
        self.assertIn("AINDA ABERTO", texto, "comando que continua aberto depois da partida tem de reprovar")
        self.assertIn("NÃO reconectaram", texto, "worker que não volta tem de reprovar")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
