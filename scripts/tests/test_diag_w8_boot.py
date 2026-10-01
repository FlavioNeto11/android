"""scripts/diag-w8-boot.py: os ensaios de boot do W8 no android-09. Só lógica (sem adb, sem central, sem restart).

Protegem: seguro por padrão e exclusivo do 09; um ensaio por marcador; o vocabulário de escrita fechado (nada de perfil, peer, dados
do app, WireGuard); o gate bloqueia sem escrever nada; lockdown nunca vira 1.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("diag_w8_boot", ROOT / "scripts" / "diag-w8-boot.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["diag_w8_boot"] = mod
spec.loader.exec_module(mod)                                                      # type: ignore[union-attr]


class BootW8(unittest.TestCase):
    def test_seguro_por_padrao_so_mostra_o_plano(self) -> None:
        for kind in mod.ENSAIOS:
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(mod.main([kind]), 0)
            self.assertEqual(json.loads(out.getvalue())["modo"], "plano (nenhuma chamada)")

    def test_so_o_09_e_exige_a_pasta(self) -> None:
        with redirect_stderr(io.StringIO()):
            self.assertEqual(mod.main(["os", "--execute", "--instance", "android-02", "--run", "."]), 2)
            self.assertEqual(mod.main(["os", "--execute", "--instance", mod.IID]), 2)

    def test_vocabulario_de_escrita_fechado(self) -> None:
        escritas = mod.plano("os")["escritas_possiveis"]
        for w in escritas:
            self.assertTrue(w.startswith(("settings put secure always_on_vpn", "settings delete secure always_on_vpn_app",
                                          "input keyevent KEYCODE_HOME", "am force-stop io.nekohasekai.sfa", "input tap", "POST /api/instances/android-09/actions/restart"))
                            or "w8load" in w, w)
        self.assertNotIn("always_on_vpn_lockdown 1", " ".join(escritas))
        for proibido in ("pm clear", "wg", "reset", "uninstall"):
            self.assertFalse(any(proibido in w for w in escritas), proibido)

    def test_a_carga_so_roda_no_ensaio_starved(self) -> None:
        self.assertEqual([k for k, v in mod.ENSAIOS.items() if v["starved"]], ["os-starved"])
        self.assertEqual([k for k, v in mod.ENSAIOS.items() if v["receiver"]], ["os+receiver", "os+stopped"])
        self.assertEqual([k for k, v in mod.ENSAIOS.items() if v.get("force_stop")], ["os+stopped"])

    def test_gate_que_falha_nao_escreve_e_a_segunda_tentativa_e_recusada(self) -> None:
        class Amb:
            escreveu = False

            def agora(self): return 1.0e9
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            (run / "boot-os.tentativa").write_text("x", encoding="utf-8")
            self.assertEqual(mod.rodar(Amb(), run, "os", lambda *a, **k: None)["parada"], "SEGUNDA_TENTATIVA_RECUSADA")

    def test_pares_do_aparelho(self) -> None:
        self.assertEqual(mod._pares("U=12.5\nB=1\nT=0\nP=\nL=0.5"), {"U": "12.5", "B": "1", "T": "0", "P": "", "L": "0.5"})


# ───────────────────────────── observação do boot (parsers puros; fixtures = linhas reais de data/diag-w8-boot, enxutas) ─────────────────────────────
obs = mod.obs

EVENTOS_E2 = """\
10-01 19:29:14.864   623   687 I am_proc_start: [0,1879,10196,io.nekohasekai.sfa,service,{io.nekohasekai.sfa/io.nekohasekai.sfa.bg.VPNService}]
10-01 19:29:22.124   623  1136 I am_foreground_service_start: [0,io.nekohasekai.sfa/.bg.VPNService,1,PROC_STATE_PERSISTENT,37,37,0,0,0,1,UNKNOWN,1024]
10-01 19:29:23.923   623   623 I notification_canceled: [0|io.nekohasekai.sfa|1|null|10196,8,1784,1780,0,-1,-1,NULL]
10-01 19:29:23.925   623  1136 I am_foreground_service_stop: [0,io.nekohasekai.sfa/.bg.VPNService,1,PROC_STATE_PERSISTENT,37,37,0,0,1798,1,STOP_FOREGROUND,1024]
10-01 19:30:24.072   623   775 I am_freeze: [1879,io.nekohasekai.sfa]
10-01 19:45:53.762   623   686 I am_kill : [0,1879,io.nekohasekai.sfa,999,empty #24]
10-01 19:45:54.765   623   738 I am_proc_died: [0,1879,io.nekohasekai.sfa,999,19]
10-01 19:29:05.860   623   998 I am_proc_died: [0,959,WebViewLoader-x86_64,-700,0]
"""
EVENTOS_E3 = """\
10-01 19:21:11.566   724   817 I am_proc_start: [0,2176,10196,io.nekohasekai.sfa,service,{io.nekohasekai.sfa/io.nekohasekai.sfa.bg.VPNService}]
10-01 19:21:45.506   724  2311 I am_anr  : [0,2176,io.nekohasekai.sfa,818462276,executing service io.nekohasekai.sfa/.bg.VPNService]
10-01 19:21:46.855   724  2311 I am_kill : [0,2176,io.nekohasekai.sfa,0,bg anr]
10-01 19:21:50.225   724  1516 I am_proc_died: [0,2176,io.nekohasekai.sfa,0,10]
10-01 19:21:52.067   724   817 I am_proc_start: [0,3421,10196,io.nekohasekai.sfa,broadcast,{io.nekohasekai.sfa/io.nekohasekai.sfa.bg.BootReceiver}]
10-01 19:21:57.376   724  1501 I am_crash: [3421,0,io.nekohasekai.sfa,818462276,go.Universe$proxyerror,open /x/CrashReport-Application.log: transport endpoint is not connected,Application.java,12]
"""
MAIN_E2 = (
    "10-01 19:29:12.935   623  1016 D ConnectivityService: registerNetworkAgent NetworkAgentInfo{network{100}  handle{1}  ni{MOBILE[HSPA] CONNECTING extra: x} "
    "created=2026 Score(Policies :  ; KeepConnected : 0)   lp{{LinkAddresses: [ ]}}  nc{[ Transports: CELLULAR Capabilities: INTERNET]}  factorySerialNumber=4}\n"
    "10-01 19:29:14.340   623   833 W ContextImpl: Calling a method in the system process without a qualified user: android.app.ContextImpl.startService:1899 "
    "com.android.server.connectivity.Vpn.startAlwaysOnVpn:1292 com.android.server.VpnManagerService.startAlwaysOnVpn:586\n"
    "10-01 19:29:14.915   623   828 D ConnectivityService: Switching to new default network for: uid/pid:1000/623 activeRequest: 1 callbackRequest: 1 [NetworkRequest [ REQUEST id=1]] "
    "callback flags: 1 order: 2147483647 using NetworkAgentInfo{network{100}  handle{1}  ni{MOBILE[HSPA] CONNECTED extra: x} created=2026 Score(Policies : )  "
    "lp{{InterfaceName: eth0 LinkAddresses: [ 10.0.2.15/24 ]}}  nc{[ Transports: CELLULAR Capabilities: INTERNET]}  factorySerialNumber=4}\n"
    "10-01 19:29:17.747   623   821 D ConnectivityService: registerNetworkAgent NetworkAgentInfo{network{101}  handle{2}  ni{WIFI CONNECTING extra: } "
    "created=2026 Score(Policies : IS_UNMETERED ; KeepConnected : 0)   lp{{LinkAddresses: [ ]}}  nc{[ Transports: WIFI Capabilities: INTERNET]}  factorySerialNumber=5}\n"
    "10-01 19:29:20.724   623   828 D ConnectivityService: Switching to new default network for: uid/pid:1000/623 activeRequest: 1 callbackRequest: 1 [NetworkRequest [ REQUEST id=1]] "
    "callback flags: 1 order: 2147483647 using NetworkAgentInfo{network{101}  handle{2}  ni{WIFI CONNECTED extra: } created=2026 Score(Policies : )  "
    "lp{{InterfaceName: wlan0 LinkAddresses: [ 10.0.2.18/24 ]}}  nc{[ Transports: WIFI Capabilities: INTERNET]}  factorySerialNumber=5}\n"
)
EXIT_INFO = """\
ACTIVITY MANAGER PROCESS EXIT INFO (dumpsys activity exit-info)
Last Timestamp of Persistence Into Persistent Storage: 2026-10-01 15:54:06.558
  package: io.nekohasekai.sfa
    Historical Process Exit for uid=10196
        ApplicationExitInfo #0:
          timestamp=2026-10-01 19:21:57.535 pid=3421 realUid=10196 packageUid=10196 definingUid=10196 user=0
          process=io.nekohasekai.sfa reason=4 (APP CRASH(EXCEPTION)) subreason=0 (UNKNOWN) status=0
          importance=300 pss=0.00 rss=0.00 description=crash state=empty trace=null
        ApplicationExitInfo #1:
          timestamp=2026-10-01 19:21:46.855 pid=2176 realUid=10196 packageUid=10196 definingUid=10196 user=0
          process=io.nekohasekai.sfa reason=6 (ANR) subreason=0 (UNKNOWN) status=0
          importance=300 pss=0.00 rss=0.00 description=bg anr: executing service io.nekohasekai.sfa/.bg.VPNService state=empty trace=/data/system/procexitstore/anr_x.gz
        ApplicationExitInfo #2:
          timestamp=2026-09-30 20:01:10.284 pid=5410 realUid=10196 packageUid=10196 definingUid=10196 user=0
          process=io.nekohasekai.sfa reason=10 (USER REQUESTED) subreason=21 (FORCE STOP) status=0
          importance=100 pss=88MB rss=210MB description=stop io.nekohasekai.sfa due to from pid 5606 state=empty trace=null
  package: com.outro.app
    Historical Process Exit for uid=10001
        ApplicationExitInfo #0:
          timestamp=2026-09-24 20:27:00.125 pid=8733 realUid=10121 packageUid=10121 definingUid=10121 user=0
          process=com.outro.app reason=13 (OTHER KILLS BY SYSTEM) subreason=3 (TOO MANY EMPTY PROCS) status=0
          importance=400 pss=1MB rss=1MB description=x state=empty trace=null
"""
PACOTE_TXT = ("    versionCode=739 minSdk=32 targetSdk=37\n    lastUpdateTime=2026-10-01 10:05:12\n    installerPackageName=null\n"
              "    pkgFlags=[ HAS_CODE ALLOW_CLEAR_USER_DATA ALLOW_BACKUP ]\n"
              "    User 0: ceDataInode=336164 installed=true hidden=false suspended=false distractionFlags=0 stopped=true notLaunched=false enabled=0 instant=false virtual=false\n"
              "      firstInstallTime=2026-09-30 19:36:47\n    User 0:\n")


def _amostras(*pares):
    return [{"t": f"2026-10-01T22:29:{s:02d}Z", "u": float(s), "pid": pid, "tun": tun, "wlan0": w, "eth0": e, "boot": b}
            for s, pid, tun, w, e, b in pares]


class ObservacaoBoot(unittest.TestCase):
    def test_o_ciclo_do_servico_e_o_motivo_do_fim_do_fgs(self) -> None:
        ev = obs.parse_eventos(EVENTOS_E2)
        self.assertEqual([p["pid"] for p in ev["proc_start"]], [1879])
        self.assertEqual(ev["proc_start"][0]["tipo"], "service")
        self.assertEqual(ev["fgs_start"][0]["classe"], "io.nekohasekai.sfa/.bg.VPNService")
        self.assertEqual(ev["fgs_stop"][0]["motivo"], "STOP_FOREGROUND")
        self.assertEqual(obs.atraso(ev["fgs_start"][0]["t"], ev["fgs_stop"][0]["t"]), 1.801)
        self.assertEqual(ev["notificacao_cancelada"], [{"t": "10-01 19:29:23.923", "razao": "8"}])
        self.assertEqual(ev["freeze"][0]["pid"], 1879)
        self.assertEqual(len(ev["proc_died"]), 1, "só o do pacote: o WebViewLoader é de outro processo")

    def test_a_morte_de_processo_em_cache_e_limpeza_nao_falha(self) -> None:
        ev = obs.parse_eventos(EVENTOS_E2)
        self.assertTrue(ev["kill"][0]["benigno"])
        self.assertTrue(ev["proc_died"][0]["benigno"])
        e3 = obs.parse_eventos(EVENTOS_E3)
        self.assertFalse(e3["kill"][0]["benigno"], "kill por ANR (adj 0) é falha")
        self.assertEqual(e3["anr"][0]["razao"], "executing service io.nekohasekai.sfa/.bg.VPNService")
        self.assertEqual(e3["crash"][0]["excecao"], "go.Universe$proxyerror")
        self.assertEqual([p["tipo"] for p in e3["proc_start"]], ["service", "broadcast"])

    def test_a_ordem_das_redes_e_a_rede_padrao(self) -> None:
        r = obs.parse_rede(MAIN_E2)
        self.assertEqual(r["ordem"], ["CELLULAR", "WIFI"])
        self.assertEqual([(p["transporte"], p["iface"]) for p in r["padrao"]], [("CELLULAR", "eth0"), ("WIFI", "wlan0")])
        self.assertEqual(r["always_on_start"], ["10-01 19:29:14.340"])
        self.assertEqual(r["primeira_rede"]["transporte"], "CELLULAR")
        self.assertEqual(obs.atraso(r["always_on_start"][0], r["primeira_padrao"]["t"]), 0.575, "o always-on do sistema não espera a rede padrão")

    def test_troca_de_transporte_da_rede_padrao_durante_o_inicio(self) -> None:
        r = obs.parse_rede(MAIN_E2)
        troca = obs.troca_durante(r["padrao"], "10-01 19:29:14.864", "10-01 19:29:23.925")
        self.assertEqual([(t["de"], t["para"]) for t in troca], [("CELLULAR", "WIFI")])
        self.assertAlmostEqual(troca[0]["depois_do_proc_s"], 5.86, places=2)
        # a 1ª rede padrão (sem `de`) e a troca para a mesma transporte não contam; sem janela, nada
        self.assertEqual(obs.troca_durante(r["padrao"][:1], "10-01 19:29:14.864", "10-01 19:29:23.925"), [])
        self.assertEqual(obs.troca_durante(r["padrao"], None, None), [])

    def test_estado_do_pacote_stopped_e_enabled(self) -> None:
        p = obs.parse_pacote(PACOTE_TXT)
        self.assertEqual((p["stopped"], p["not_launched"], p["enabled"], p["installed"]), (True, False, 0, True))
        self.assertEqual(p["version_code"], 739)
        self.assertIn("ALLOW_CLEAR_USER_DATA", p["pkg_flags"])
        self.assertEqual(obs.parse_pacote("User 0: ceDataInode=1 installed=true hidden=false suspended=false distractionFlags=0 stopped=false notLaunched=true enabled=2 x")["enabled"], 2)
        self.assertEqual(obs.parse_pacote(""), {})

    def test_exit_info_so_do_pacote_com_razao_e_descricao(self) -> None:
        e = obs.parse_exit_info(EXIT_INFO)
        self.assertEqual(e["persistido_em"], "2026-10-01 15:54:06.558")
        self.assertEqual([x["razao"] for x in e["entradas"]], [4, 6, 10])
        self.assertEqual(e["entradas"][1]["razao_nome"], "ANR")
        self.assertEqual(e["entradas"][2]["subrazao_nome"], "FORCE STOP")
        self.assertIn("bg anr", e["entradas"][1]["descricao"])
        self.assertEqual(obs.parse_exit_info("")["entradas"], [])

    def test_usagestats_e_dropbox(self) -> None:
        us = ('    time="2026-10-01 09:50:22" type=FOREGROUND_SERVICE_START package=io.nekohasekai.sfa class=io.nekohasekai.sfa.bg.VPNService flags=0x0 \n'
              '    time="2026-10-01 09:50:37" type=FOREGROUND_SERVICE_STOP package=io.nekohasekai.sfa class=io.nekohasekai.sfa.bg.VPNService flags=0x0 \n'
              '    time="2026-10-01 09:50:37" type=FOREGROUND_SERVICE_START package=outro class=x flags=0x0 \n')
        self.assertEqual([(u["tipo"], u["classe"]) for u in obs.parse_usagestats(us)],
                         [("FOREGROUND_SERVICE_START", "io.nekohasekai.sfa.bg.VPNService"), ("FOREGROUND_SERVICE_STOP", "io.nekohasekai.sfa.bg.VPNService")])
        db = ("========================================\n2026-10-01 19:29:17 SYSTEM_BOOT (text, 10 bytes)\nBuild: x\n"
              "========================================\n2026-10-01 19:21:57 data_app_crash (text, 99 bytes)\nProcess: io.nekohasekai.sfa\n"
              "========================================\n2026-09-30 12:53:40 system_app_anr (text, 9 bytes)\nProcess: com.android.systemui\n")
        ent = obs.parse_dropbox(db)
        self.assertEqual([(e["tag"], e["processo"]) for e in ent],
                         [("SYSTEM_BOOT", None), ("data_app_crash", "io.nekohasekai.sfa"), ("system_app_anr", "com.android.systemui")])
        self.assertEqual([e["tag"] for e in obs.dropbox_do_app(ent)], ["SYSTEM_BOOT", "data_app_crash"], "o ANR do systemui não é do app")

    def test_amostras_pid_tun_e_interfaces(self) -> None:
        a = obs.parse_amostras(_amostras((3, "", "0", "0", "0", ""), (5, "", "0", "0", "1", ""), (10, "1879", "0", "0", "1", "1"),
                                         (12, "1879", "0", "1", "1", "1"), (15, "1879 2000", "1", "1", "1", "1")))
        self.assertEqual(a["pids_distintos"], ["1879"])
        self.assertEqual(a["mudancas_de_pid"], 0)
        self.assertEqual(a["t_eth0"]["u"], 5.0)
        self.assertEqual(a["t_wlan0"]["u"], 12.0, "eth0 (celular) apareceu antes do wlan0 (Wi-Fi)")
        self.assertEqual(a["t_tun"]["u"], 15.0)
        self.assertEqual(a["t_boot_completed"]["u"], 10.0)
        b = obs.parse_amostras(_amostras((3, "100", "0", "0", "0", ""), (5, "200", "0", "0", "0", "")))
        self.assertEqual(b["mudancas_de_pid"], 1)

    def test_relogio_do_convidado_para_o_central(self) -> None:
        self.assertEqual(obs.estimar_gmtoff("10-01 19:33:46", "2026-10-01T22:33:45.826Z"), -10800)
        self.assertEqual(obs.para_central("10-01 19:29:22.124", 2026, -10800, 0.0), "2026-10-01T22:29:22.124Z")
        self.assertEqual(obs.para_central("10-01 19:29:22.124", 2026, -10800, -0.5), "2026-10-01T22:29:22.624Z", "convidado 0,5 s atrás do central")
        self.assertEqual(obs.segundos("10-02 00:00:01.000") - obs.segundos("10-01 23:59:59.000"), 2.0, "virada de dia")

    def _classe(self, eventos: str, main: str, amostras: list) -> dict:
        return obs.classificar(obs.parse_eventos(eventos), obs.parse_rede(main), obs.parse_amostras(amostras))

    def test_assinatura_e2_silent_stop(self) -> None:
        am = _amostras((10, "1879", "0", "0", "1", "1"), (40, "1879", "0", "1", "1", "1"))
        c = self._classe(EVENTOS_E2, MAIN_E2, am)
        self.assertEqual(c["codigo"], "SILENT_STOP")
        self.assertEqual((c["fgs_duracao_s"], c["motivo_fim"], c["mesmo_pid"], c["morte_ou_anr_no_boot"], c["tun"]), (1.801, "STOP_FOREGROUND", True, False, False))

    def test_a_assinatura_exige_todas_as_condicoes(self) -> None:
        am = _amostras((10, "1879", "0", "0", "1", "1"))
        com_tun = _amostras((10, "1879", "0", "0", "1", "1"), (20, "1879", "1", "1", "1", "1"))
        self.assertEqual(self._classe(EVENTOS_E2, MAIN_E2, com_tun)["codigo"], "TUN_OK", "com tun0 não é falha")
        lento = EVENTOS_E2.replace("19:29:23.925", "19:30:23.925")
        self.assertNotEqual(self._classe(lento, MAIN_E2, am)["codigo"], "SILENT_STOP", "fora da janela de 30 s não é a assinatura")
        outro_motivo = EVENTOS_E2.replace("STOP_FOREGROUND", "STOP_SERVICE")
        self.assertNotEqual(self._classe(outro_motivo, MAIN_E2, am)["codigo"], "SILENT_STOP", "só o STOP_FOREGROUND do app")
        com_anr = EVENTOS_E2 + "10-01 19:29:24.000   623   1 I am_anr  : [0,1879,io.nekohasekai.sfa,1,x]\n"
        self.assertEqual(self._classe(com_anr, MAIN_E2, am)["codigo"], "ANR_OU_KILL", "com ANR não é silencioso")
        pid_novo = _amostras((10, "1879", "0", "0", "1", "1"), (30, "1999", "0", "1", "1", "1"))
        self.assertFalse(self._classe(EVENTOS_E2, MAIN_E2, pid_novo)["mesmo_pid"], "PID que muda não é 'processo sobreviveu'")

    def test_outras_classes_de_boot(self) -> None:
        am = _amostras((10, "2176", "0", "1", "1", "1"))
        self.assertEqual(self._classe(EVENTOS_E3, MAIN_E2, am)["codigo"], "ANR_OU_KILL")
        so_proc = EVENTOS_E2.split("10-01 19:29:22.124")[0]
        self.assertEqual(self._classe(so_proc, MAIN_E2, am)["codigo"], "SERVICO_SEM_FOREGROUND")
        self.assertEqual(self._classe("", "", [])["codigo"], "SEM_ALWAYS_ON_DO_SISTEMA")
        so_fgs = EVENTOS_E2.split("10-01 19:29:23.923")[0]
        self.assertEqual(self._classe(so_fgs, MAIN_E2, am)["codigo"], "FGS_SEM_TUN")

    def test_resumir_uma_pasta_capturada_e_faltas_viram_lacunas(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            (run / "boot-os+stopped.events.txt").write_text(EVENTOS_E2, encoding="utf-8")
            (run / "boot-os+stopped.main-system.txt").write_text(MAIN_E2, encoding="utf-8")
            (run / "boot-os+stopped.exit-info.txt").write_text(EXIT_INFO, encoding="utf-8")
            (run / "boot-os+stopped.pacote.txt").write_text(PACOTE_TXT, encoding="utf-8")
            (run / "boot-os+stopped.relogio.json").write_text(json.dumps({"ano": 2026, "gmtoff_s": -10800, "offset_s": -0.066, "incerteza_s": 0.05}), encoding="utf-8")
            (run / "boot-os+stopped.amostras.jsonl").write_text("\n".join(json.dumps(x) for x in _amostras((10, "1879", "0", "0", "1", "1"))), encoding="utf-8")
            r = obs.resumir(run, "os+stopped")
            self.assertEqual(r["assinatura"]["codigo"], "SILENT_STOP")
            self.assertEqual(r["rede"]["ordem"], ["CELLULAR", "WIFI"])
            self.assertEqual([t["para"] for t in r["troca_padrao_durante_o_inicio"]], ["WIFI"])
            self.assertEqual(r["servico"]["always_on_ate_fgs_s"], 7.784)
            self.assertEqual(r["servico"]["proc_ate_fgs_s"], 7.26)
            self.assertEqual(r["momentos"]["fgs_stop"]["central_utc"], "2026-10-01T22:29:23.991Z", "convidado -0,066 s do central")
            self.assertTrue(r["pacote"]["stopped"])
            self.assertEqual(r["exit_info"]["n"], 3)
            self.assertEqual(r["lacunas"], [], r["lacunas"])
            (run / "boot-os+stopped.pacote.txt").unlink()
            self.assertEqual(obs.resumir(run, "os+stopped")["lacunas"], ["pacote.txt"])
            self.assertIn("SILENT_STOP", obs.tabela([r]))

    def test_resumo_offline_nao_toca_aparelho_nem_central(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            (run / "boot-os.events.txt").write_text(EVENTOS_E2, encoding="utf-8")
            (run / "boot-os.main-system.txt").write_text(MAIN_E2, encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(mod.main(["resumir", d]), 0)
            self.assertIn("SILENT_STOP", out.getvalue())
            self.assertTrue((run / "boot-os.resumo.json").exists())
            with redirect_stderr(io.StringIO()):
                self.assertEqual(mod.main(["resumir", d + "/vazia"]), 2)

    def test_a_captura_so_le(self) -> None:
        """Os pedidos de captura e as amostras são leituras: nenhuma escrita, injeção, reboot nem toque (isso fica no vocabulário de escrita)."""
        proibidos = ("settings put", "settings delete", "am force-stop", "am start", "am broadcast", "pm ", "input ", "rm ", "kill ", "reboot",
                     "svc ", "cmd package", "wm ", "su ", "tee ", "setprop", "chmod", "mv ", "cp ", "dd ")
        todos = [*mod.PEDIDOS_CAPTURA.values(), mod.CMD_AMOSTRA, mod.CMD_PACOTE, mod.CMD_RELOGIO, mod.CMD_SERVICOS]
        for cmd in todos:
            limpo = cmd.replace("2>/dev/null", "")
            for p in proibidos:
                self.assertNotIn(p, limpo, f"{p!r} em {cmd[:70]!r}")
            self.assertNotIn(">", limpo, cmd[:70])
        self.assertTrue(all("dumpsys" in c or "logcat" in c or c.startswith(("echo", "for t in")) for c in todos))

    def test_a_captura_nao_le_o_armazenamento_privado_do_cliente(self) -> None:
        for cmd in [*mod.PEDIDOS_CAPTURA.values(), mod.CMD_AMOSTRA, mod.CMD_PACOTE]:
            for privado in ("/data/data", "/data/user", "run-as", "/sdcard", "/storage/emulated", "databases", "shared_prefs", ".db", "package-restrictions"):
                self.assertNotIn(privado, cmd)

    def test_sobre_as_capturas_reais_quando_presentes(self) -> None:
        """Os 3 ensaios reais (fora do Git): E1 TUN_OK, E2 SILENT_STOP (a assinatura), E3 ANR e kill. Pula se a pasta não existir."""
        base = mod.raiz_do_central() / "data" / "diag-w8-boot"
        esperado = {("run1-os", "os"): "TUN_OK", ("run2-os-stopped", "os+stopped"): "SILENT_STOP", ("run3-os-starved", "os-starved"): "ANR_OU_KILL"}
        achou = False
        for (pasta, kind), codigo in esperado.items():
            if not (base / pasta / f"boot-{kind}.events.txt").exists():
                continue
            achou = True
            self.assertEqual(obs.resumir(base / pasta, kind)["assinatura"]["codigo"], codigo, pasta)
        if not achou:
            self.skipTest("sem as capturas reais (data/ não versionado)")


if __name__ == "__main__":
    unittest.main()
