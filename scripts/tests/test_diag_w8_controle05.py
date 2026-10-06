"""scripts/diag-w8-controle05.py: o perfil exclusivo do android-05. Só lógica e um aparelho FALSO: nenhum adb, nenhum central.

O que estes testes protegem, nesta ordem:
  1. seguro por padrão e exclusivo: o plano não toca em nada; só o android-05 (o 09 e qualquer outro id são recusados);
  2. o gate: qualquer premissa do baseline convergido que falhe = CONTROL_BASELINE_BLOCKED, sem escrita e sem gastar a tentativa;
  3. UMA tentativa: force-stop → gesto instrumentado → tile ao Q0; marcador impede a segunda; nunca desliga túnel nem reinicia;
  4. o vocabulário de escrita é fechado e o monitor só lê.
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
_spec = importlib.util.spec_from_file_location("diag_w8_controle05", ROOT / "scripts" / "diag-w8-controle05.py")
mod = importlib.util.module_from_spec(_spec)
sys.modules["diag_w8_controle05"] = mod
_spec.loader.exec_module(mod)                                                     # type: ignore[union-attr]
tile = mod.tile

T0 = 1_790_000_000.0
WAKE = T0 - 900.0                                                                 # o wake foi 15 min antes


class Falso05(mod.Ambiente):
    """Um android-05 convergido (always-on, lockdown 1, tun0, VPN CONNECTED, 3 regras, par com handshake)."""

    def __init__(self, *, tile_q0=False, clique_funciona=True, force_stop_pega=True, **estado):
        self.estado = {"tile": tile_q0, "tun": True, "vpn": True, "regras": 3, "pid_sfa": 6512, "stopped": False,
                       "always_on": tile.PACOTE, "lockdown": "1", "boot": True}
        self.estado.update(estado)
        self.clique_funciona, self.force_stop_pega = clique_funciona, force_stop_pega
        self.state, self.serial, self.label, self.locked = "online", mod.SERIAL, "qa-user-05", None
        self.worker = {"id": mod.WORKER_LOCAL, "connected": True, "state": "degraded", "maintenance": False}
        self.rede = ("trafego_verificado", mod.POLITICA, 1, 1, 0, coletor_iso(T0 - 300))
        self.peers = [{"instance_id": "android-05", "address": "10.66.0.2", "last_connection": lc_str(T0 - 60)}]
        self.conectividade = {"state": "healthy", "checked_at": coletor_iso(T0 - 120)}
        self.abertos: list[tuple] = []
        self.recentes: list[tuple] = []
        self.eventos: list[tuple] = []
        self.comandos_vistos: list[tuple] = []
        self.coletor_ok = True
        self.log: list[str] = []
        self.t = T0
        self.adb = "adb-falso"

    def agora(self) -> float:
        return self.t

    def dormir(self, s: float) -> None:
        self.t += s

    # ---- aparelho
    def _pares(self, pesada: bool) -> str:
        e = self.estado
        s = (f"U=2000\nS=900\nB={int(e['boot'])}\nA={e['always_on']}\nL={e['lockdown']}\nT={int(e['tun'])}\n"
             f"PS={e['pid_sfa'] or ''}\nPU=2529\nQ={int(e['tile'])}\n")
        if pesada:
            s += f"V={int(e['vpn'])}\nR={e['regras']}\nST=stopped={str(e['stopped']).lower()}\n"
        return s

    def shell(self, cmd: str, timeout: float = 60) -> tuple[int, str, str]:
        self.log.append(cmd)
        e = self.estado
        if cmd.startswith("echo U=$(id -u)"):
            return 0, self._pares("dumpsys connectivity" in cmd), ""
        if cmd == tile.CMD_LEVE_UNICA:
            return 0, f"T={int(e['tun'])}\nPS={e['pid_sfa'] or ''}\nPU=2529\n", ""
        if cmd.startswith("echo B=$(getprop"):
            return 0, f"B=1\nS=900\nT={int(e['tun'])}\nPS={e['pid_sfa'] or ''}\nPU=2529\n", ""
        if cmd == mod.CMD_PACOTE:
            return 0, "    versionCode=739 minSdk=32 targetSdk=37\n    versionName=1.14.2\nPM=package:/data/app/x/base.apk\n", ""
        if cmd == mod.CMD_NETLINK:
            return 0, "NL=4\nNLAPP=2\n", ""
        if cmd == tile.CMD_FORCE_STOP:
            if self.force_stop_pega:
                e.update(pid_sfa=None, stopped=True, tun=False, vpn=False)
            return 0, "", ""
        if "CK_BEGIN" in cmd:                                                   # o gesto do produto, com captura
            q0 = int(e["tile"])
            e["tile"] = True
            out = f"Q0={q0}\nP1=2529\nP2=2529\nQ=1\nT={int(e['tun'])}\n"
            if not e["tun"]:
                out += "CK_BEGIN\nCK_END\nCK_RC=0\nCK_T0=100.0\nCK_T1=100.02\nCLICOU=1\n"
                if self.clique_funciona:
                    e.update(tun=True, vpn=True, pid_sfa=7001, stopped=False)
                return 0, out, "CK_BEGIN\nCK_END\n"
            return 0, out + "CLICOU=0\n", ""
        if cmd.startswith("cmd statusbar click-tile"):
            raise AssertionError("clique de desligar: o controle NUNCA desliga o túnel")
        if cmd == tile.CMD_REMOVER:
            e["tile"] = False
            return 0, "", ""
        if cmd == tile.CMD_ADICIONAR:
            e["tile"] = True
            return 0, "", ""
        raise AssertionError(f"comando inesperado: {cmd!r}")

    def adb_devices(self) -> str:
        return f"List of devices attached\n{mod.SERIAL}\tdevice\n"

    # ---- central
    def snapshot(self) -> dict:
        return {"instances": [{"id": mod.IID, "state": self.state, "serial": self.serial, "account_label": self.label,
                               "locked_account": self.locked, "current": None, "control": "none",
                               "connectivity": self.conectividade, "readiness": {"phase": "ready"}}],
                "workers": [self.worker]}

    def servidor(self) -> dict:
        return {"peers": self.peers}

    def sql(self, consulta: str, params: tuple = ()) -> list[tuple]:
        if "FROM device_network" in consulta:
            return [self.rede] if self.rede else []
        if "created_at>=? OR" in consulta:
            return self.recentes
        if "FROM commands" in consulta and "NOT IN" in consulta:
            return self.abertos
        if "FROM commands" in consulta:
            return self.comandos_vistos
        if "FROM events" in consulta:
            return self.eventos
        return []

    def logcat_vivo(self) -> tuple[bool, str]:
        return self.coletor_ok, "adb logcat -b all pid(s) 1"

    def arquivo_recente(self, caminho: Path, max_s: float) -> tuple[bool, str]:
        return True, f"{caminho.name} ok"

    def escritas(self) -> list[str]:
        return [c for c in self.log if re.search(r"cmd statusbar|am force-stop", c)]


def coletor_iso(t: float) -> str:
    return mod.coletor.iso(t)


def lc_str(t: float) -> str:
    """O formato do `last_connection` do servidor: `-0300 AAAA-MM-DD hh:mm:ss`."""
    from datetime import datetime, timedelta, timezone
    return "-0300 " + datetime.fromtimestamp(t, tz=timezone(timedelta(hours=-3))).strftime("%Y-%m-%d %H:%M:%S")


def pasta() -> Path:
    d = Path(tempfile.mkdtemp(prefix="diagw8c05-"))
    (d / "meta.json").write_text("{}", encoding="utf-8")
    return d


def roda(argv: list[str], amb: Falso05 | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = mod.main(argv, amb)
    return rc, out.getvalue(), err.getvalue()


def gate(amb: Falso05, run: Path | None = None) -> dict:
    return mod.baseline(amb, WAKE, run=run if run is not None else pasta())


class SeguroEExclusivo(unittest.TestCase):
    def test_plano_nao_toca_em_nada(self) -> None:
        with mock.patch("subprocess.run") as run, mock.patch("subprocess.Popen") as popen, \
                mock.patch.object(mod.Ambiente, "__init__", side_effect=AssertionError("o ambiente foi criado")):
            rc, out, _ = roda(["plano"])
        self.assertEqual(rc, 0)
        run.assert_not_called()
        popen.assert_not_called()
        self.assertIn("DRY-RUN", out)
        self.assertIn("CK_BEGIN", out)

    def test_so_o_android_05(self) -> None:
        for outro in ("android-09", "android-02", "android-05,android-09", "*", "all", ""):
            for cmd in (["plano"], ["monitorar", "--run", "x"], ["baseline", "--desde-wake", "2026-10-01T17:00:00Z"],
                        ["controle", "--desde-wake", "2026-10-01T17:00:00Z", "--run", "x", "--execute"]):
                amb = Falso05()
                rc, _, err = roda(cmd + ["--instance", outro], amb)
                self.assertEqual(rc, 2, (outro, cmd[0]))
                self.assertEqual(amb.log, [])
                self.assertIn("RECUSADO", err)

    def test_subcomandos_exigem_a_instancia(self) -> None:
        amb = Falso05()
        self.assertEqual(roda(["baseline", "--desde-wake", "2026-10-01T17:00:00Z"], amb)[0], 2)
        self.assertEqual(roda(["controle", "--desde-wake", "2026-10-01T17:00:00Z", "--run", "x"], amb)[0], 2)
        self.assertEqual(amb.log, [])

    def test_controle_exige_execute_run_e_observacao_minima(self) -> None:
        amb = Falso05()
        base = ["controle", "--instance", "android-05", "--desde-wake", "2026-10-01T17:00:00Z"]
        self.assertEqual(roda(base + ["--run", str(pasta())], amb)[0], 2)                       # sem --execute
        self.assertEqual(roda(base + ["--run", "nao-existe", "--execute"], amb)[0], 2)
        self.assertEqual(roda(base + ["--run", str(pasta()), "--execute", "--observar-s", "10"], amb)[0], 2)
        self.assertEqual(roda(["controle", "--instance", "android-05", "--desde-wake", "lixo", "--run", str(pasta()),
                               "--execute"], amb)[0], 2)
        self.assertEqual(amb.log, [])


class Gate(unittest.TestCase):
    def test_convergido_passa(self) -> None:
        g = gate(Falso05())
        self.assertTrue(g["ok"], g["falhas"])
        self.assertEqual(g["base"]["always_on"], tile.PACOTE)
        self.assertEqual(g["base"]["lockdown"], "1")
        self.assertFalse(g["base"]["tile_presente_q0"])

    def _bloqueia(self, amb: Falso05, falha: str) -> None:
        g = gate(amb)
        self.assertFalse(g["ok"])
        self.assertIn(falha, g["falhas"])

    def test_cada_premissa_quebrada_bloqueia(self) -> None:
        self._bloqueia(Falso05(always_on="null"), "always_on_do_cliente")
        self._bloqueia(Falso05(lockdown="0"), "lockdown_1")
        self._bloqueia(Falso05(tun=False), "tun0_no_ar")
        self._bloqueia(Falso05(vpn=False), "vpn_connected")
        self._bloqueia(Falso05(regras=0), "regras_de_bloqueio")
        self._bloqueia(Falso05(pid_sfa=None), "cliente_vivo_e_nao_stopped")
        self._bloqueia(Falso05(stopped=True), "cliente_vivo_e_nao_stopped")
        self._bloqueia(Falso05(boot=False), "boot_completo")
        a = Falso05(); a.state = "hibernated"; self._bloqueia(a, "instancia_online")
        a = Falso05(); a.serial = "emulator-5554"; self._bloqueia(a, "serial_confere")
        a = Falso05(); a.label = "tadeu.quintela"; self._bloqueia(a, "qa_sem_conta_real")
        a = Falso05(); a.locked = "x"; self._bloqueia(a, "qa_sem_conta_real")
        a = Falso05(); a.worker["maintenance"] = True; self._bloqueia(a, "worker_local_saudavel")
        a = Falso05(); a.worker["connected"] = False; self._bloqueia(a, "worker_local_saudavel")
        a = Falso05(); a.rede = ("configurado", mod.POLITICA, 1, 1, 0, coletor_iso(T0)); self._bloqueia(a, "rede_trafego_verificado")
        a = Falso05(); a.rede = ("trafego_verificado", "livre", 1, 1, 0, coletor_iso(T0)); self._bloqueia(a, "rede_trafego_verificado")
        a = Falso05(); a.rede = ("trafego_verificado", mod.POLITICA, 2, 1, 0, coletor_iso(T0)); self._bloqueia(a, "rede_trafego_verificado")
        a = Falso05(); a.rede = ("trafego_verificado", mod.POLITICA, 1, 1, 1, coletor_iso(T0)); self._bloqueia(a, "rede_trafego_verificado")
        a = Falso05(); a.rede = ("trafego_verificado", mod.POLITICA, 1, 1, 0, coletor_iso(WAKE - 7 * 3600)); self._bloqueia(a, "verificacao_depois_do_wake")
        a = Falso05(); a.peers = []; self._bloqueia(a, "par_com_handshake_depois_do_wake")
        a = Falso05(); a.peers[0]["last_connection"] = None; self._bloqueia(a, "par_com_handshake_depois_do_wake")
        a = Falso05(); a.peers[0]["last_connection"] = lc_str(WAKE - 3600); self._bloqueia(a, "par_com_handshake_depois_do_wake")
        a = Falso05(); a.abertos = [("c-1", "restart", "running")]; self._bloqueia(a, "sem_comando_aberto")
        a = Falso05(); a.recentes = [("c-2", "device.network", "succeeded", coletor_iso(T0 - 30))]; self._bloqueia(a, "silencio_de_comandos")
        a = Falso05(); a.conectividade = {"state": "unavailable", "checked_at": coletor_iso(T0)}; self._bloqueia(a, "conectividade_healthy_depois_do_wake")
        a = Falso05(); a.conectividade = {"state": "healthy", "checked_at": coletor_iso(WAKE - 60)}; self._bloqueia(a, "conectividade_healthy_depois_do_wake")
        a = Falso05(); a.coletor_ok = False; self._bloqueia(a, "coletor_gravando")

    def test_baseline_so_le(self) -> None:
        amb = Falso05()
        mod.baseline(amb, WAKE, run=pasta(), completo=True)
        self.assertEqual(amb.escritas(), [])

    def test_baseline_completo_registra_pacote_e_netlink_antes(self) -> None:
        g = mod.baseline(Falso05(), WAKE, run=pasta(), completo=True)
        self.assertEqual(g["base"]["pacote"]["versionName"], "1.14.2")
        self.assertEqual(g["base"]["pacote"]["versionCode"], "739")
        self.assertEqual(g["base"]["netlink_antes"], {"negacoes_no_buffer": 4, "do_cliente": 2, "estado": "PRESENT"})

    def test_netlink_ausente(self) -> None:
        amb = Falso05()
        amb.shell = lambda cmd, timeout=60: (0, "NL=0\nNLAPP=0\n", "")           # type: ignore[method-assign]
        self.assertEqual(mod.ler_netlink(amb)["estado"], "ABSENT")
        amb.shell = lambda cmd, timeout=60: (255, "", "x")                         # type: ignore[method-assign]
        self.assertIsNone(mod.ler_netlink(amb)["estado"])                         # sem leitura ≠ ausente


class Controle(unittest.TestCase):
    def _roda(self, amb: Falso05, run: Path | None = None) -> tuple[dict, Path]:
        run = run or pasta()
        return mod.rodar_controle(amb, run, WAKE, mod.Registro(None)), run

    def test_gate_falha_nao_escreve_nem_gasta_a_tentativa(self) -> None:
        amb = Falso05(tun=False)
        res, run = self._roda(amb)
        self.assertEqual(res["estado"], "CONTROL_BASELINE_BLOCKED")
        self.assertEqual(amb.escritas(), [])
        self.assertFalse((run / "controle-05.tentativa").exists())
        self.assertEqual(res["mutacoes"], 0)

    def test_tile_que_funciona(self) -> None:
        amb = Falso05()
        res, run = self._roda(amb)
        self.assertEqual(res["estado"], "GESTO_EXECUTADO")
        self.assertTrue(res["tun_subiu"])
        self.assertEqual(res["force_stop"]["processo_depois"], None)
        self.assertTrue(res["force_stop"]["stopped_depois"])
        self.assertFalse(res["force_stop"]["tun_depois"])
        self.assertEqual(res["gesto"]["click_exit"], "0")
        self.assertGreaterEqual(res["observacao"]["leituras"], 7)                  # 30 s a cada ~4 s
        self.assertEqual(res["mutacoes"], 2)
        self.assertEqual(res["leitura_final"]["tun"], True)
        self.assertEqual(res["leitura_final"]["vpn"], True)
        self.assertTrue(res["tile_restaurado"]["ok"])
        self.assertEqual(res["par_depois"]["address"], "10.66.0.2")
        self.assertTrue((run / "controle-05.tentativa").exists())

    def test_tile_que_falha_nao_improvisa(self) -> None:
        amb = Falso05(clique_funciona=False)
        res, _ = self._roda(amb)
        self.assertEqual(res["estado"], "GESTO_EXECUTADO")
        self.assertFalse(res["tun_subiu"])
        self.assertEqual(res["leitura_final"]["tun"], False)
        forcas = [c for c in amb.escritas() if "force-stop" in c]
        gestos = [c for c in amb.log if "CK_BEGIN" in c]
        self.assertEqual((len(forcas), len(gestos)), (1, 1))
        # além do force-stop e do gesto, só a devolução do tile ao Q0 (Q0 era 0: remove-tile)
        self.assertEqual([c for c in amb.escritas() if "CK_BEGIN" not in c and "force-stop" not in c], [tile.CMD_REMOVER])

    def test_q0_um_deixa_o_tile(self) -> None:
        amb = Falso05(tile_q0=True)
        res, _ = self._roda(amb)
        self.assertTrue(res["tile_restaurado"]["ok"])
        self.assertTrue(amb.estado["tile"])
        self.assertNotIn(tile.CMD_REMOVER, amb.log)

    def test_segunda_tentativa_e_recusada(self) -> None:
        run = pasta()
        self._roda(Falso05(), run)
        amb = Falso05()
        res, _ = self._roda(amb, run)
        self.assertEqual(res["estado"], "RECUSADO_SEGUNDA_TENTATIVA")
        self.assertEqual(amb.log, [])

    def test_segunda_tentativa_pela_cli_recusada_com_codigo_5(self) -> None:
        run = pasta()
        argv = ["controle", "--instance", "android-05", "--desde-wake", coletor_iso(WAKE), "--run", str(run), "--execute"]
        self.assertEqual(roda(argv, Falso05())[0], 0)
        self.assertTrue((run / "acionador-controle05.json").exists())
        self.assertEqual(roda(argv, Falso05())[0], 5)

    def test_baseline_bloqueado_pela_cli_devolve_3(self) -> None:
        run = pasta()
        argv = ["controle", "--instance", "android-05", "--desde-wake", coletor_iso(WAKE), "--run", str(run), "--execute"]
        self.assertEqual(roda(argv, Falso05(lockdown="0"))[0], 3)

    def test_force_stop_que_nao_pegou_nao_executa_o_gesto(self) -> None:
        amb = Falso05(force_stop_pega=False)
        res, run = self._roda(amb)
        self.assertEqual(res["estado"], "ERRO_FORCE_STOP_NAO_PEGOU")
        self.assertFalse(any("CK_BEGIN" in c for c in amb.log))
        self.assertTrue((run / "controle-05.tentativa").exists())                  # a tentativa foi gasta

    def test_tun_que_nao_caiu_nao_executa_o_gesto(self) -> None:
        amb = Falso05()
        original = amb.shell

        def shell(cmd: str, timeout: float = 60):
            r = original(cmd, timeout)
            if cmd == tile.CMD_FORCE_STOP:
                amb.estado["tun"] = True
            return r

        amb.shell = shell                                                         # type: ignore[method-assign]
        res, _ = self._roda(amb)
        self.assertEqual(res["estado"], "ERRO_TUN_NAO_CAIU")
        self.assertFalse(any("CK_BEGIN" in c for c in amb.log))

    def test_erro_no_meio_ainda_devolve_o_tile(self) -> None:
        amb = Falso05()
        original = amb.shell

        def shell(cmd: str, timeout: float = 60):
            if cmd == tile.CMD_LEVE_UNICA:
                raise RuntimeError("adb caiu na observação")
            return original(cmd, timeout)

        amb.shell = shell                                                         # type: ignore[method-assign]
        res, _ = self._roda(amb)
        self.assertEqual(res["estado"], "ERRO")
        self.assertIn("adb caiu", res["erro"])
        self.assertTrue(res["tile_restaurado"]["ok"])
        self.assertFalse(amb.estado["tile"])


class VocabularioDeEscrita(unittest.TestCase):
    PERMITIDO = (re.compile(r"cmd statusbar (remove|add|click)-tile " + re.escape(tile.TILE) + r"(?![\w/.$])"),
                 re.compile(r"am force-stop " + re.escape(tile.PACOTE) + r"$"))
    PROIBIDO = (r"\bsettings put\b", r"\bsvc\b", r"\breboot\b", r"\bpm (clear|disable|uninstall|install)\b", r"\binput\b",
                r"\bsetprop\b", r"\bkill\b", r"\brm\b", r"\bsu\b", r"\bam (start|broadcast|startservice)\b", r"\bwm\b",
                r"\bip (link|addr (add|del)|route)\b", r"\biptables\b", r">\s*/data", r"\btee\b", r"\bcmd (wifi|connectivity)\b")

    def test_vocabulario_de_escrita(self) -> None:
        cmds: list[str] = []
        for kw in ({}, {"clique_funciona": False}, {"tile_q0": True}, {"force_stop_pega": False}):
            amb = Falso05(**kw)
            mod.rodar_controle(amb, pasta(), WAKE, mod.Registro(None))
            cmds += amb.log
        mod.baseline(Falso05(), WAKE, run=pasta(), completo=True)
        self.assertGreater(len(cmds), 20)
        for c in cmds:
            for p in self.PROIBIDO:
                self.assertIsNone(re.search(p, c), f"{p} em {c!r}")
            for trecho in re.findall(r"(?:cmd statusbar \S+ \S+|am force-stop \S+)", c):
                self.assertTrue(any(p.search(trecho) for p in self.PERMITIDO), f"escrita fora do vocabulário: {trecho!r}")

    def test_o_plano_so_usa_o_vocabulario(self) -> None:
        texto = json.dumps(mod.plano())
        self.assertNotIn("settings put", texto)
        self.assertNotIn("reboot", texto)

    def test_o_monitor_so_le(self) -> None:
        amb = Falso05()
        for n in range(4):
            mod.tick_do_monitor(amb, coletor_iso(WAKE), {}, com_aparelho=True)
        self.assertEqual(amb.escritas(), [])
        for c in amb.log:
            for p in self.PROIBIDO:
                self.assertIsNone(re.search(p, c), c)


class Monitor(unittest.TestCase):
    def test_tick_registra_estado_rede_par_e_novidades(self) -> None:
        amb = Falso05()
        amb.comandos_vistos = [("c-1", "device.network", "running", coletor_iso(T0), None)]
        amb.eventos = [(coletor_iso(T0), "instance.updated", "android-05: online")]
        vistos: dict = {}
        r = mod.tick_do_monitor(amb, coletor_iso(WAKE), vistos, com_aparelho=True)
        self.assertEqual(r["instancia"]["state"], "online")
        self.assertEqual(r["rede"][0], "trafego_verificado")
        self.assertEqual(r["par"]["address"], "10.66.0.2")
        self.assertEqual(len(r["comandos_novos"]), 1)
        self.assertEqual(len(r["eventos_novos"]), 1)
        self.assertEqual(r["adb"], "device")
        self.assertEqual(r["aparelho"]["boot"], "1")
        r2 = mod.tick_do_monitor(amb, coletor_iso(WAKE), vistos, com_aparelho=True)
        self.assertEqual(r2["comandos_novos"], [])                                 # o mesmo comando/estado não repete

    def test_sem_adb_do_aparelho_hibernado(self) -> None:
        amb = Falso05()
        amb.adb_devices = lambda: "List of devices attached\n127.0.0.1:15555\tdevice\n"   # type: ignore[method-assign]
        amb.state = "hibernated"
        r = mod.tick_do_monitor(amb, coletor_iso(WAKE), {}, com_aparelho=True)
        self.assertEqual(r["adb"], "ausente")
        self.assertNotIn("aparelho", r)
        self.assertFalse(any(c.startswith("echo B=") for c in amb.log))            # sem aparelho, nenhuma ida ao adb

    def test_monitorar_para_pelo_arquivo(self) -> None:
        run = pasta()
        amb = Falso05()
        original = amb.dormir
        chamadas = []

        def dormir(s: float) -> None:
            chamadas.append(s)
            original(s)
            if len(chamadas) == 3:
                (run / "parar-monitor").write_text("", encoding="utf-8")

        amb.dormir = dormir                                                       # type: ignore[method-assign]
        buf = io.StringIO()
        with redirect_stderr(buf):
            self.assertEqual(mod.monitorar(amb, run, 3600.0, 5.0), 0)
        linhas = (run / "monitor.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(linhas), 3)
        self.assertEqual(json.loads(linhas[0])["src"], "monitor")


if __name__ == "__main__":
    unittest.main()
