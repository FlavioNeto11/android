"""scripts/diag-w8-tile.py: o acionador diagnóstico do W8. Só lógica e um aparelho FALSO: nenhum adb, nenhum central.

O que estes testes protegem, nesta ordem:
  1. seguro por padrão: sem --execute não há nenhuma chamada; só o android-09; precondição falha = nenhuma escrita;
  2. fidelidade: o gesto é o `comando_de_religar` do produto, trocando SÓ o clique (agora com stdout/stderr/exit code);
  3. o vocabulário de escrita é fechado (tile e force-stop do cliente; nada de settings put, reboot, svc, pm...);
  4. o rollback roda sempre (inclusive depois de erro) e devolve o tile ao Q0.
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("diag_w8_tile", ROOT / "scripts" / "diag-w8-tile.py")
mod = importlib.util.module_from_spec(_spec)
sys.modules["diag_w8_tile"] = mod
_spec.loader.exec_module(mod)                                                     # type: ignore[union-attr]

SFA = mod.PACOTE


class Falso(mod.Ambiente):
    """Um android-09 de brinquedo. `clique_funciona`: o clique sobe o tun0. Guarda todo comando que recebeu."""

    def __init__(self, *, tile=False, tun=False, pid_sfa=6512, stopped=False, always_on="null", lockdown="0",
                 clique_funciona=True, tun_persiste=False, linha_de_rede=None, peers=None, abertos=None,
                 explode_na_observacao=False, stderr_do_clique="", stdout_do_clique=""):
        self.estado = {"tile": tile, "tun": tun, "pid_sfa": pid_sfa, "stopped": stopped, "always_on": always_on,
                       "lockdown": lockdown}
        self.clique_funciona, self.tun_persiste = clique_funciona, tun_persiste
        self.linha_de_rede, self.peers, self.abertos = linha_de_rede or [], peers or [], abertos or []
        self.explode_na_observacao = explode_na_observacao
        self.stderr_do_clique, self.stdout_do_clique = stderr_do_clique, stdout_do_clique
        self.log: list[str] = []
        self.t = 1_000_000.0
        self.adb = "adb-falso"

    # ---- relógio
    def agora(self) -> float:
        return self.t

    def dormir(self, s: float) -> None:
        self.t += s

    # ---- aparelho
    def _pares(self, pesada: bool) -> str:
        e = self.estado
        s = (f"U=2000\nS=10448\nB=1\nA={e['always_on']}\nL={e['lockdown']}\nT={int(e['tun'])}\n"
             f"PS={e['pid_sfa'] or ''}\nPU=2529\nQ={int(e['tile'])}\n")
        if pesada:
            s += f"V={int(e['tun'])}\nR=0\nST=stopped={str(e['stopped']).lower()}\n"
        return s

    def shell(self, cmd: str, timeout: float = 60) -> tuple[int, str, str]:
        self.log.append(cmd)
        e = self.estado
        if cmd.startswith("echo U=$(id -u)"):
            return 0, self._pares("dumpsys connectivity" in cmd), ""
        if cmd == mod.CMD_LEVE_UNICA:
            if self.explode_na_observacao and any("CK_BEGIN" in c for c in self.log):
                self.explode_na_observacao = False                              # cai uma vez; o rollback precisa funcionar
                raise RuntimeError("adb caiu no meio da observação")
            return 0, f"T={int(e['tun'])}\nPS={e['pid_sfa'] or ''}\nPU=2529\n", ""
        if "CK_BEGIN" in cmd:                                                   # o gesto do produto, com captura
            q0 = int(e["tile"])
            e["tile"] = True
            out = f"Q0={q0}\nP1=2529\nP2=2529\nQ=1\nT={int(e['tun'])}\n"
            err = ""
            if not e["tun"]:
                out += f"CK_BEGIN\n{self.stdout_do_clique}\nCK_END\nCK_RC=0\nCK_T0=100.0\nCK_T1=100.25\nCLICOU=1\n"
                err = f"CK_BEGIN\n{self.stderr_do_clique}\nCK_END\n"
                if self.clique_funciona:
                    e.update(tun=True, pid_sfa=e["pid_sfa"] or 7001, stopped=False)
            else:
                out += "CLICOU=0\n"
            return 0, out, err
        if cmd.startswith("cmd statusbar click-tile"):
            if e["tile"] and not self.tun_persiste:
                e["tun"] = not e["tun"]
            return 0, "CK_RC=0\n", ""
        if cmd == mod.CMD_FORCE_STOP:
            e.update(pid_sfa=None, stopped=True, tun=False)
            return 0, "", ""
        if cmd == mod.CMD_REMOVER:
            e["tile"] = False
            return 0, "", ""
        if cmd == mod.CMD_ADICIONAR:
            e["tile"] = True
            return 0, "", ""
        raise AssertionError(f"comando inesperado: {cmd!r}")

    def adb_devices(self) -> str:
        return f"List of devices attached\n{mod.SERIAL}\tdevice\n"

    # ---- central
    def snapshot(self) -> dict:
        return {"instances": [{"id": mod.IID, "state": "online", "account_label": "qa-user-09", "locked_account": None,
                               "current": None, "control": "none", "worker_id": mod.WORKER,
                               "connectivity": {"state": "healthy", "checked_at": "x"}}],
                "workers": [{"id": mod.WORKER, "connected": True, "transport_state": "up", "state": "degraded",
                             "maintenance": False}]}

    def servidor(self) -> dict:
        return {"peers": [{"instance_id": p} for p in self.peers]}

    def sql(self, consulta: str, params: tuple = ()) -> list[tuple]:
        if "FROM device_network" in consulta:
            return self.linha_de_rede
        if "FROM commands" in consulta:
            return self.abertos
        return []

    def logcat_vivo(self) -> tuple[bool, str]:
        return True, "adb logcat pid(s) 1"

    def arquivo_recente(self, caminho: Path, max_s: float) -> tuple[bool, str]:
        return True, f"{caminho.name} ok"

    def escritas(self) -> list[str]:
        return [c for c in self.log if re.search(r"cmd statusbar|am force-stop", c)]


def pasta_do_coletor() -> Path:
    d = Path(tempfile.mkdtemp(prefix="diagw8tile-"))
    (d / "meta.json").write_text("{}", encoding="utf-8")
    return d


def roda(argv: list[str], amb: Falso | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = mod.main(argv, amb)
    return rc, out.getvalue(), err.getvalue()


class SeguroPorPadrao(unittest.TestCase):
    def test_dry_run_nao_toca_em_nada(self) -> None:
        with mock.patch("subprocess.run") as run, mock.patch("subprocess.Popen") as popen, \
                mock.patch.object(mod.Ambiente, "__init__", side_effect=AssertionError("o ambiente foi criado")):
            rc, out, _ = roda(["--fase", "a1"])
        self.assertEqual(rc, 0)
        run.assert_not_called()
        popen.assert_not_called()
        self.assertIn("DRY-RUN", out)
        self.assertIn("CK_BEGIN", out)                                          # o gesto exato aparece no plano

    def test_dry_run_tambem_recusa_outro_aparelho(self) -> None:
        self.assertEqual(roda(["--fase", "a1", "--instance", "android-05"])[0], 2)

    def test_execute_recusa_qualquer_outro_aparelho(self) -> None:
        for outro in ("android-05", "android-09,android-05", "android-*", "*", "all", "android-9", ""):
            amb = Falso()
            rc, _, err = roda(["--fase", "a1", "--execute", "--instance", outro, "--run", str(pasta_do_coletor())], amb)
            self.assertEqual(rc, 2, outro)
            self.assertEqual(amb.log, [], outro)
            self.assertIn("RECUSADO", err)

    def test_execute_exige_instance_e_run(self) -> None:
        amb = Falso()
        self.assertEqual(roda(["--fase", "a1", "--execute"], amb)[0], 2)
        self.assertEqual(roda(["--fase", "a1", "--execute", "--instance", "android-09"], amb)[0], 2)
        self.assertEqual(amb.log, [])

    def test_janela_de_observacao_minima(self) -> None:
        self.assertEqual(roda(["--fase", "a1", "--observar-s", "10"])[0], 2)

    def test_a2_exige_a1_que_subiu_o_tun_e_fechou_o_rollback(self) -> None:
        run = pasta_do_coletor()
        base = ["--fase", "a2", "--execute", "--instance", "android-09", "--run", str(run)]
        self.assertEqual(roda(base, Falso())[0], 2)                             # sem --apos-a1
        for a1 in ({"fase": "a1", "tun_subiu": False, "rollback": {"ok": True}},
                   {"fase": "a1", "tun_subiu": True, "rollback": {"ok": False}},
                   {"fase": "a2", "tun_subiu": True, "rollback": {"ok": True}}):
            f = run / "a1.json"
            f.write_text(json.dumps(a1), encoding="utf-8")
            amb = Falso()
            self.assertEqual(roda(base + ["--apos-a1", str(f)], amb)[0], 2)
            self.assertEqual(amb.log, [])


class Precondicoes(unittest.TestCase):
    def _bloqueia(self, amb: Falso, falha: str) -> None:
        run = pasta_do_coletor()
        res = mod.rodar_fase(amb, "a1", run, 35.0, mod.Registro(None))
        self.assertEqual(res["estado"], "BLOCKED")
        self.assertIn(falha, res["gate"]["falhas"])
        self.assertEqual(amb.escritas(), [], "uma precondição falhou e mesmo assim algo foi escrito")
        self.assertEqual(res["mutacoes"], 0)

    def test_cada_premissa_quebrada_bloqueia_sem_escrever(self) -> None:
        self._bloqueia(Falso(always_on="io.nekohasekai.sfa"), "always_on_null")
        self._bloqueia(Falso(lockdown="1"), "lockdown_0")
        self._bloqueia(Falso(tun=True), "tun0_ausente")
        self._bloqueia(Falso(linha_de_rede=[("android-09", "pendente")]), "sem_linha_de_rede")
        self._bloqueia(Falso(peers=["android-09"]), "sem_peer_no_servidor")
        self._bloqueia(Falso(abertos=[("c-1", "restart", "running")]), "sem_restart_ou_device_network_aberto")
        self._bloqueia(Falso(abertos=[("c-2", "open_app", "running")]), "sem_comando_aberto")

    def test_sem_coletor_vivo_bloqueia(self) -> None:
        amb = Falso()
        amb.logcat_vivo = lambda: (False, "nenhum adb logcat")                  # type: ignore[method-assign]
        self._bloqueia(amb, "coletor_gravando")

    def test_sem_pasta_do_coletor_bloqueia(self) -> None:
        amb = Falso()
        res = mod.rodar_fase(amb, "a1", None, 35.0, mod.Registro(None))
        self.assertEqual(res["estado"], "BLOCKED")
        self.assertEqual(amb.escritas(), [])

    def test_stopped_na_linha_de_base_marca_a_comparacao_como_inconclusiva(self) -> None:
        res = mod.rodar_fase(Falso(stopped=True, pid_sfa=None), "a1", pasta_do_coletor(), 35.0, mod.Registro(None))
        self.assertEqual(res["comparacao_force_stop"], "INCONCLUSIVE")
        self.assertEqual(mod.rodar_fase(Falso(), "a1", pasta_do_coletor(), 35.0, mod.Registro(None))["comparacao_force_stop"],
                         "VALIDA")


class Fidelidade(unittest.TestCase):
    def test_o_gesto_e_o_do_produto_trocando_so_o_clique(self) -> None:
        sys.path.insert(0, str(ROOT / "backend"))
        try:
            from app.devices.rede_aplicacao import comando_de_religar
        finally:
            sys.path.pop(0)
        produto = comando_de_religar(mod.TILE)
        gesto = mod.comando_do_gesto()
        self.assertNotEqual(gesto, produto)
        self.assertEqual(gesto.replace(mod._CLIQUE_CAPTURADO, mod._CLIQUE_DO_PRODUTO), produto)
        self.assertEqual(mod.comando_do_gesto(produto), gesto)

    def test_se_o_produto_mudar_o_acionador_recusa(self) -> None:
        with self.assertRaises(RuntimeError):
            mod.comando_do_gesto("cmd statusbar click-tile outro/tile; echo CLICOU=1")

    def test_captura_de_stdout_stderr_e_exit_code(self) -> None:
        out = ("Q0=0\nP1=10\nP2=10\nQ=1\nT=0\nCK_BEGIN\nok do clique\nCK_END\nCK_RC=3\nCK_T0=100.0\nCK_T1=100.5\nCLICOU=1\n")
        err = "algum aviso\nCK_BEGIN\nboom no clique\nCK_END\n"
        g = mod.ler_gesto(0, out, err, 61.234)
        self.assertEqual((g["Q0"], g["P1"], g["P2"], g["Q"], g["T"], g["CLICOU"]), ("0", "10", "10", "1", "0", "1"))
        self.assertEqual(g["click_exit"], "3")
        self.assertEqual(g["click_stdout"], "ok do clique")
        self.assertEqual(g["click_stderr"], "boom no clique")
        self.assertEqual(g["click_duracao_aparelho_s"], 0.5)
        self.assertEqual(g["gesto_duracao_host_s"], 61.234)

    def test_o_clique_sem_saida_e_registrado_como_vazio(self) -> None:
        g = mod.ler_gesto(0, "CK_BEGIN\nCK_END\nCK_RC=0\nCLICOU=1\n", "CK_BEGIN\nCK_END\n", 1.0)
        self.assertEqual((g["click_stdout"], g["click_stderr"], g["click_exit"]), ("", "", "0"))
        self.assertIsNone(g["click_duracao_aparelho_s"])


class Fluxo(unittest.TestCase):
    def _a1(self, amb: Falso, obs: float = 35.0) -> dict:
        return mod.rodar_fase(amb, "a1", pasta_do_coletor(), obs, mod.Registro(None))

    def test_a1_com_sucesso_desliga_pelo_tile_e_fecha_o_rollback(self) -> None:
        amb = Falso(stdout_do_clique="Broadcasting", stderr_do_clique="")
        res = self._a1(amb)
        self.assertEqual(res["estado"], "GESTO_EXECUTADO")
        self.assertTrue(res["tun_subiu"])
        self.assertEqual(res["gesto"]["click_stdout"], "Broadcasting")
        self.assertEqual(res["gesto"]["click_exit"], "0")
        self.assertGreaterEqual(res["observacao"]["leituras"], 8)               # >= 30 s a cada ~4 s
        self.assertEqual(res["rollback"]["metodo_do_tunel"], "tile")
        self.assertTrue(res["rollback"]["ok"])
        self.assertFalse(amb.estado["tun"])
        self.assertNotIn(mod.CMD_FORCE_STOP, amb.log)                           # o force-stop é só do a2 ou do plano B
        self.assertEqual(res["mutacoes"], 1)

    def test_a1_sem_tun_registra_e_nao_inventa_sucesso(self) -> None:
        amb = Falso(clique_funciona=False)
        res = self._a1(amb)
        self.assertFalse(res["tun_subiu"])
        self.assertTrue(res["rollback"]["ok"])
        self.assertIsNone(res["rollback"]["metodo_do_tunel"])                   # nada a desligar
        self.assertFalse(amb.estado["tile"])                                    # Q0 era 0: o tile saiu

    def test_q0_zero_remove_o_tile_e_q0_um_deixa(self) -> None:
        a0 = Falso(tile=False, clique_funciona=False)
        self.assertTrue(self._a1(a0)["rollback"]["ok"])
        self.assertIn(mod.CMD_REMOVER, a0.log)
        self.assertFalse(a0.estado["tile"])
        a1 = Falso(tile=True, clique_funciona=False)
        r = self._a1(a1)
        self.assertTrue(r["rollback"]["ok"])
        self.assertNotIn(mod.CMD_REMOVER, a1.log)
        self.assertTrue(a1.estado["tile"])

    def test_tun_que_persiste_cai_para_o_force_stop_no_rollback(self) -> None:
        amb = Falso(tun_persiste=True)
        res = self._a1(amb)
        self.assertEqual(res["rollback"]["metodo_do_tunel"], "tile+force-stop")
        self.assertTrue(res["rollback"]["ok"])
        self.assertIn(mod.CMD_FORCE_STOP, amb.log)

    def test_erro_no_meio_ainda_faz_o_rollback(self) -> None:
        amb = Falso(explode_na_observacao=True)
        res = self._a1(amb)
        self.assertEqual(res["estado"], "ERRO")
        self.assertIn("adb caiu", res["erro"])
        self.assertIn("rollback", res)
        self.assertTrue(res["rollback"]["ok"], res["rollback"])
        self.assertFalse(amb.estado["tun"])
        self.assertFalse(amb.estado["tile"])

    def test_a2_faz_force_stop_confere_e_so_depois_o_gesto(self) -> None:
        amb = Falso()
        res = mod.rodar_fase(amb, "a2", pasta_do_coletor(), 35.0, mod.Registro(None))
        escritas = amb.escritas()
        self.assertEqual(escritas[0], mod.CMD_FORCE_STOP)
        self.assertIsNone(res["a2_force_stop"]["processo_depois"])
        self.assertTrue(res["a2_force_stop"]["stopped_depois"])
        self.assertEqual(res["mutacoes"], 2)
        self.assertTrue(res["rollback"]["ok"])

    def test_a2_nao_executa_o_gesto_se_o_processo_sobreviveu_ao_force_stop(self) -> None:
        amb = Falso()
        original = amb.shell

        def shell(cmd: str, timeout: float = 60):
            if cmd == mod.CMD_FORCE_STOP:
                amb.log.append(cmd)                                             # o force-stop "não pegou"
                return 0, "", ""
            return original(cmd, timeout)

        amb.shell = shell                                                       # type: ignore[method-assign]
        res = mod.rodar_fase(amb, "a2", pasta_do_coletor(), 35.0, mod.Registro(None))
        self.assertEqual(res["estado"], "ERRO")
        self.assertFalse(any("CK_BEGIN" in c for c in amb.log))
        self.assertIn("rollback", res)

    def test_execucao_completa_pela_cli_grava_resultado_e_codigo_0(self) -> None:
        run = pasta_do_coletor()
        rc, out, _ = roda(["--fase", "a1", "--execute", "--instance", "android-09", "--run", str(run)], Falso())
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads((run / "acionador-a1.json").read_text(encoding="utf-8"))["tun_subiu"])
        self.assertIn('"rollback"', out)
        self.assertTrue((run / "acionador.jsonl").exists())


class VocabularioDeEscrita(unittest.TestCase):
    """O inverso de `comandos_so_leem`: tudo o que o acionador manda ao aparelho é leitura ou uma destas escritas."""
    PERMITIDO = (re.compile(r"cmd statusbar (remove|add|click)-tile " + re.escape(mod.TILE) + r"(?![\w/.$])"),
                 re.compile(r"am force-stop " + re.escape(SFA) + r"$"))
    PROIBIDO = (r"\bsettings put\b", r"\bsvc\b", r"\breboot\b", r"\bpm (clear|disable|uninstall|install)\b", r"\binput\b",
                r"\bsetprop\b", r"\bkill\b", r"\brm\b", r"\bsu\b", r"\bam (start|broadcast|startservice)\b", r"\bwm\b",
                r"\bip (link|addr (add|del)|route)\b", r"\biptables\b", r">\s*/data", r"\btee\b")

    def _comandos(self) -> list[str]:
        cmds: list[str] = []
        for fase, kw in (("a1", {}), ("a1", {"clique_funciona": False}), ("a1", {"tun_persiste": True}),
                         ("a1", {"explode_na_observacao": True}), ("a2", {}), ("a1", {"tile": True})):
            amb = Falso(**kw)
            mod.rodar_fase(amb, fase, pasta_do_coletor(), 35.0, mod.Registro(None))
            cmds += amb.log
        return cmds

    def test_vocabulario_de_escrita(self) -> None:
        cmds = self._comandos()
        self.assertGreater(len(cmds), 20)
        for c in cmds:
            for p in self.PROIBIDO:
                self.assertIsNone(re.search(p, c), f"{p} em {c!r}")
            for trecho in re.findall(r"(?:cmd statusbar \S+ \S+|am force-stop \S+)", c):
                self.assertTrue(any(p.search(trecho) for p in self.PERMITIDO), f"escrita fora do vocabulário: {trecho!r}")
        # o plano do dry-run é o mesmo vocabulário (o gesto cru, o do produto, também só usa tile)
        plano = json.dumps(mod.plano("a2", 35.0))
        self.assertNotIn("settings put", plano)

    def test_a_leitura_do_aparelho_continua_so_leitura(self) -> None:
        self.assertEqual(mod.coletor.comandos_so_leem(), [])


if __name__ == "__main__":
    unittest.main()
