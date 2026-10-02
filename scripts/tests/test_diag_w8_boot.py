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


BROADCASTS_E2 = """\
      BroadcastRecord{49bd38a android.intent.action.BOOT_COMPLETED/u0} to user 0
      enqueueClockTime=2026-10-01 19:29:13.355 dispatchClockTime=1969-12-31 21:00:00.000
      DELIVERED scheduled +5s891ms terminal +2s830ms (7) #8: (manifest)
          name=io.nekohasekai.sfa.bg.BootReceiver
          packageName=io.nekohasekai.sfa
        reason: remote app
      DELIVERED scheduled +11s890ms terminal +4ms (10) #91: (manifest)
          name=androidx.work.impl.background.systemalarm.RescheduleReceiver
          packageName=io.nekohasekai.sfa
        reason: remote app
      DELIVERED scheduled +9s25ms terminal +175ms (9) #9: (manifest)
          name=com.android.dialer.app.calllog.CallLogReceiver
          packageName=com.google.android.dialer
        reason: remote app
      BroadcastRecord{a57f98e android.intent.action.TIME_TICK/u-1} to user -1
      enqueueClockTime=2026-10-01 19:31:00.000 dispatchClockTime=2026-10-01 19:31:00.001
      SKIPPED terminal +23m46s665ms (-1) #3: (manifest)
          name=io.nekohasekai.sfa.bg.BootReceiver
          packageName=io.nekohasekai.sfa
        reason: skipped por política
      BroadcastRecord{bb1 android.intent.action.MY_PACKAGE_REPLACED/u0} to user 0
      enqueueClockTime=2026-10-01 19:40:00.000 dispatchClockTime=2026-10-01 19:40:00.001
      SKIPPED terminal +23m46s665ms (-1) #0: (manifest)
          name=io.nekohasekai.sfa.bg.BootReceiver
          packageName=io.nekohasekai.sfa
        reason: Background execution not allowed
"""


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

    def test_broadcasts_a_segunda_chance_entregue_ou_pulada(self) -> None:
        b = obs.parse_broadcasts(BROADCASTS_E2)
        boot = [x for x in b if x["acao"] == "BOOT_COMPLETED"]
        self.assertEqual([x["receptor"].rsplit(".", 1)[-1] for x in boot], ["BootReceiver", "RescheduleReceiver"])
        self.assertEqual(boot[0]["estado"], "DELIVERED")
        self.assertEqual((boot[0]["agendado_s"], boot[0]["terminal_s"]), (5.891, 2.83))
        self.assertEqual((boot[0]["entregue_em"], boot[0]["terminou_em"]), ("10-01 19:29:19.246", "10-01 19:29:22.076"))
        self.assertEqual(boot[1]["entregue_em"], "10-01 19:29:25.245", "o RescheduleReceiver bate com o log do WorkManager (25,247)")
        self.assertTrue(all(x["pacote"] if "pacote" in x else True for x in b))
        self.assertEqual([x["acao"] for x in b if x["estado"] == "SKIPPED"], ["MY_PACKAGE_REPLACED"], "o TIME_TICK não é de boot; o dialer não é do pacote")
        self.assertEqual(obs._duracao_s("+2m11s863ms"), 131.863)
        self.assertEqual(obs._duracao_s("+964ms"), 0.964)
        self.assertEqual(obs.parse_broadcasts(""), [])

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
            (run / "boot-os+stopped.broadcasts.txt").write_text(BROADCASTS_E2, encoding="utf-8")
            (run / "boot-os+stopped.saida.json").write_text(json.dumps({"boot": {"pacote": {
                "primeiro_adb": {"t": "x", "u": 18.1, "stopped": False, "not_launched": False, "enabled": 0},
                "boot_completed": {"t": "y", "u": 39.0, "stopped": False}}}}), encoding="utf-8")
            (run / "boot-os+stopped.relogio.json").write_text(json.dumps({"ano": 2026, "gmtoff_s": -10800, "offset_s": -0.066, "incerteza_s": 0.05}), encoding="utf-8")
            (run / "boot-os+stopped.amostras.jsonl").write_text("\n".join(json.dumps(x) for x in _amostras((10, "1879", "0", "0", "1", "1"))), encoding="utf-8")
            r = obs.resumir(run, "os+stopped")
            self.assertEqual(r["assinatura"]["codigo"], "SILENT_STOP")
            self.assertEqual(r["rede"]["ordem"], ["CELLULAR", "WIFI"])
            self.assertEqual([t["para"] for t in r["troca_padrao_durante_o_inicio"]], ["WIFI"])
            self.assertEqual(r["servico"]["always_on_ate_fgs_s"], 7.784)
            self.assertEqual(r["servico"]["proc_ate_fgs_s"], 7.26)
            self.assertEqual(r["momentos"]["fgs_stop"]["central_utc"], "2026-10-01T22:29:23.991Z", "convidado -0,066 s do central")
            self.assertTrue(r["pacote_fim"]["stopped"], "o dumpsys completo é do FIM do boot")
            self.assertFalse(r["pacote_no_boot"]["primeiro_adb"]["stopped"], "o que discrimina H1 é o `stopped` no 1º adb, lido DURANTE o boot")
            self.assertEqual(r["boot_receiver"]["estado"], "DELIVERED")
            self.assertEqual(r["boot_receiver"]["terminou_em"], "10-01 19:29:22.076")
            self.assertEqual(r["boot_receiver"]["terminou_ate_fgs_s"], 0.048, "o BootReceiver terminou 48 ms antes do startForeground")
            self.assertEqual(r["exit_info"]["n"], 3)
            self.assertEqual(r["lacunas"], [], r["lacunas"])
            tab = obs.tabela([r])
            self.assertIn("SILENT_STOP", tab)
            self.assertTrue(tab.rstrip().endswith("False | DELIVERED"), "a tabela mostra stopped_1o_adb e o BootReceiver")
            (run / "boot-os+stopped.pacote.txt").unlink()
            self.assertEqual(obs.resumir(run, "os+stopped")["lacunas"], ["pacote.txt"])
            (run / "boot-os+stopped.saida.json").unlink()
            self.assertEqual(obs.resumir(run, "os+stopped")["lacunas"], ["pacote.txt", "pacote_no_boot"])
            em_memoria = obs.resumir(run, "os+stopped", {"boot": {"pacote": {"primeiro_adb": {"stopped": True}}}})
            self.assertTrue(em_memoria["pacote_no_boot"]["primeiro_adb"]["stopped"], "`rodar` passa o dict em memória: o json só sai depois")

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

    def test_o_historico_de_broadcasts_sai_sem_os_extras(self) -> None:
        """Os extras dos broadcasts carregam valores de outros apps: o filtro mantém só cabeçalho, estados, nomes e razões."""
        cmd = mod.PEDIDOS_CAPTURA["broadcasts"]
        self.assertIn("dumpsys activity broadcasts history", cmd)
        self.assertNotIn("extras", cmd)
        self.assertIn("grep -E", cmd)
        self.assertIn("head -c", cmd, "com teto de tamanho")

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


# ───────────────────────────── estágio 1 (K × R): ordem pré-comprometida, categorias fechadas, regra de parada ─────────────────────────────
spec_e1 = importlib.util.spec_from_file_location("diag_w8_boot_estagio1", ROOT / "scripts" / "diag-w8-boot-estagio1.py")
est1 = importlib.util.module_from_spec(spec_e1)
sys.modules["diag_w8_boot_estagio1"] = est1
spec_e1.loader.exec_module(est1)                                                  # type: ignore[union-attr]


def _reg(idx, arm, cat, troca=False):
    return {"ORDER_INDEX": idx, "BLOCK": (idx + 1) // 2, "ARM": arm, "RESULT": cat, "NETWORK_SWITCH_DURING_WINDOW": troca}


class Estagio1(unittest.TestCase):
    def test_a_ordem_e_deterministica_e_pre_comprometida(self) -> None:
        o = est1.ordem()
        self.assertEqual(o, est1.ordem(), "mesma semente, mesma ordem")
        self.assertEqual([x["braco"] for x in o], ["K", "R", "R", "K", "K", "R"], "a ordem registrada no doc ANTES do 1º boot")
        self.assertEqual(est1.SEED, "w8-boot-estagio1-20261002")
        self.assertEqual([x["indice"] for x in o], [1, 2, 3, 4, 5, 6])
        for b in (1, 2, 3):
            self.assertEqual(sorted(x["braco"] for x in o if x["bloco"] == b), ["K", "R"], "cada bloco tem um K e um R")
        self.assertNotEqual(est1.ordem("outra-semente"), o)
        self.assertEqual({x["ensaio"] for x in o}, {"os+stopped", "os+receiver"}, "só os dois braços do estágio 1: nada de os+uistop")

    def test_seguro_por_padrao_so_o_09_e_pasta_vazia(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(est1.main([]), 0)
        self.assertEqual(json.loads(out.getvalue())["max_boots"], 6)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(est1.main(["--execute", "--instance", "android-02", "--run", "."]), 2)
            self.assertEqual(est1.main(["--execute", "--instance", est1.IID]), 2)
            with tempfile.TemporaryDirectory() as d:
                (Path(d) / "x").write_text("a", encoding="utf-8")
                self.assertEqual(est1.main(["--execute", "--instance", est1.IID, "--run", d]), 2, "pasta com conteúdo: sem retomar")

    def test_as_categorias_sao_fechadas(self) -> None:
        def resumo(cod, anr=(), crash=(), kill=()):
            return {"assinatura": {"codigo": cod}, "processo": {"anr": list(anr), "crash": list(crash), "kill": list(kill)}}
        self.assertEqual(est1.CATEGORIAS, ("TUN_OK", "SILENT_STOP", "ANR_OU_CRASH", "PROCESS_KILLED", "BOOT_INVALID", "UNKNOWN"))
        self.assertEqual(est1.categoria(resumo("TUN_OK")), "TUN_OK")
        self.assertEqual(est1.categoria(resumo("SILENT_STOP")), "SILENT_STOP")
        self.assertEqual(est1.categoria(resumo("ANR_OU_KILL", anr=[1])), "ANR_OU_CRASH")
        self.assertEqual(est1.categoria(resumo("CRASH", crash=[1])), "ANR_OU_CRASH")
        self.assertEqual(est1.categoria(resumo("ANR_OU_KILL", kill=[1])), "PROCESS_KILLED")
        for cod in ("SERVICO_SEM_FOREGROUND", "FGS_SEM_TUN", "SEM_ALWAYS_ON_DO_SISTEMA", "INDETERMINADO", "QUALQUER_NOVO"):
            self.assertEqual(est1.categoria(resumo(cod)), "UNKNOWN", cod)

    def test_a_regra_de_parada_do_pedido(self) -> None:
        av = est1.avaliar_parada
        # continuam: o esperado de H1 (K falha COM troca de rede; R funciona SEM troca)
        self.assertIsNone(av([_reg(1, "K", "SILENT_STOP", True)]))
        self.assertIsNone(av([_reg(1, "K", "SILENT_STOP", True), _reg(2, "R", "TUN_OK", False)]))
        # as quatro paradas de hipótese
        self.assertEqual(av([_reg(1, "R", "SILENT_STOP", True)])[0], "R_SILENT_STOP")
        self.assertEqual(av([_reg(1, "K", "TUN_OK", False)])[0], "K_TUN_OK")
        self.assertEqual(av([_reg(1, "K", "SILENT_STOP", False)])[0], "FALHA_SEM_TROCA_DE_REDE")
        self.assertEqual(av([_reg(1, "R", "TUN_OK", True)])[0], "TUN_OK_COM_TROCA_DE_REDE")
        # inválido, incerto e inesperado: param
        for cat in ("BOOT_INVALID", "UNKNOWN"):
            self.assertEqual(av([_reg(1, "K", cat)])[0], "BOOT_INVALIDO_OU_INCERTO")
        for cat in ("ANR_OU_CRASH", "PROCESS_KILLED"):
            self.assertEqual(av([_reg(1, "R", cat)])[0], "COMPORTAMENTO_INESPERADO")
        # quatro iguais só com 4 boots que não dispararam nada antes (R TUN_OK sem troca, nunca K TUN_OK) -> não ocorre com K; vale o caso sintético
        iguais = [_reg(1, "R", "TUN_OK"), _reg(2, "R", "TUN_OK"), _reg(3, "R", "TUN_OK"), _reg(4, "R", "TUN_OK")]
        self.assertEqual(av(iguais)[0], "QUATRO_IGUAIS")
        misto = [_reg(1, "K", "SILENT_STOP", True), _reg(2, "R", "TUN_OK"), _reg(3, "R", "TUN_OK"), _reg(4, "K", "SILENT_STOP", True)]
        self.assertIsNone(av(misto), "K falha e R funciona, nos dois blocos: o 3º bloco é permitido")

    def test_a_rede_padrao_no_instante_da_partida(self) -> None:
        r = {"servico": {"proc_start": {"t": "10-01 19:29:14.864"}}, "rede": {"trocas_da_rede_padrao": [["10-01 19:29:14.915", "CELLULAR"]]}}
        self.assertEqual(est1.rede_na_partida(r), "NONE_YET", "a rede padrão só veio 51 ms DEPOIS do processo")
        r["rede"]["trocas_da_rede_padrao"] = [["10-01 19:29:14.000", "WIFI"], ["10-01 19:29:20.700", "CELLULAR"]]
        self.assertEqual(est1.rede_na_partida(r), "WIFI")
        self.assertEqual(est1.rede_na_partida({"servico": {}, "rede": {}}), "SEM_PROCESSO")

    def test_a_segunda_partida_so_por_indicio(self) -> None:
        so = ("10-01 19:29:22.054   623   686 I am_wtf  : [0,623,system_server,-1,ActivityManager,Background started FGS: Allowed "
              "[callingPackage: android; callingUid: 1000; uidState: PER ; intent: Intent { act=android.net.VpnService pkg=io.nekohasekai.sfa }; code:X]]\n")
        a = obs.segunda_partida(so)
        self.assertEqual((a["estado"], a["verificacoes_de_fgs"], len(a["do_sistema"])), ("NOT_OBSERVED", 1, 1))
        dois = so + ("10-01 19:29:22.060   623   686 I am_wtf  : [0,623,system_server,-1,ActivityManager,Background started FGS: Allowed "
                     "[callingPackage: io.nekohasekai.sfa; callingUid: 10196; intent: Intent { cmp=io.nekohasekai.sfa/io.nekohasekai.sfa.bg.VPNService }; code:X]]\n")
        b = obs.segunda_partida(dois)
        self.assertEqual((b["estado"], len(b["do_proprio_app"])), ("OBSERVED", 1))
        self.assertEqual(obs.segunda_partida("")["estado"], "NOT_OBSERVED")

    def test_o_baseline_exige_a_automacao_de_ui(self) -> None:
        """O 1º ensaio real parou em 503 da API de hierarquia (UiAutomator2 morto) DEPOIS de escrever o always-on: o baseline agora lê antes."""
        class Amb:
            def agora(self): return 1.0e9
            def snapshot(self): return {"workers": [{"id": est1.tile.WORKER, "state": "healthy", "last_seen_at": est1.datetime.now(est1.timezone.utc).isoformat()}]}
            def servidor(self): return {"peers": []}
            def hierarquia(self): raise RuntimeError("HTTP 503 automation_unavailable")
        orig = est1.boot.gate
        est1.boot.gate = lambda amb: {"ok": True, "falhas": [], "base": {}}
        try:
            b = est1.baseline(Amb())
        finally:
            est1.boot.gate = orig
        self.assertFalse(b["ok"])
        self.assertTrue(any(f.startswith("automacao_ui_indisponivel") for f in b["falhas"]), b["falhas"])

    def test_so_orquestra_o_que_o_ator_ja_faz(self) -> None:
        """O estágio 1 não escreve nada novo: os braços são os ensaios existentes e o vocabulário de escrita é o do `diag-w8-boot.py`."""
        self.assertTrue(set(est1.BRACOS.values()) <= set(mod.ENSAIOS))
        self.assertNotIn("os+uistop", mod.ENSAIOS)
        fonte = (ROOT / "scripts" / "diag-w8-boot-estagio1.py").read_text(encoding="utf-8").split('"""', 2)[2]      # sem a docstring
        for proibido in ("amb.shell(", "settings put", "am force-stop", "svc wifi", "svc data", "pm clear", "wg set", "wg-quick", "wg show", "uninstall"):
            self.assertNotIn(proibido, fonte, proibido)

    def test_digest_do_servidor_ignora_a_telemetria_volatil_e_pega_o_resto(self) -> None:
        """02/10: o digest mudou só porque `peers[].last_connection` (android-03/06) andou. Isso não é o servidor ter sido mexido."""
        base = {"pid": 8416, "started_at": "2026-10-02T11:07:55Z", "signature": "ab82064daf07", "in_sync": True,
                "peers": [{"instance_id": "android-03", "address": "10.66.0.5", "last_connection": "-0300 2026-10-02 09:51:56"},
                          {"instance_id": "android-06", "address": "10.66.0.6", "last_connection": None}],
                "remote_access": {"remote_peers": [], "firewall": {"state": "ok", "checked_at": "2026-10-02T12:00:00Z"}}}
        d0 = est1.digest_servidor(base)
        andou = json.loads(json.dumps(base))
        andou["peers"][0]["last_connection"] = "-0300 2026-10-02 09:53:09"
        andou["peers"][1]["last_connection"] = "-0300 2026-10-02 09:53:09"
        andou["remote_access"]["firewall"]["checked_at"] = "2026-10-02T12:30:00Z"
        self.assertEqual(est1.digest_servidor(andou), d0, "só telemetria mudou: o digest não muda")
        for caminho, valor in (("pid", 9999), ("started_at", "2026-10-02T13:00:00Z"), ("signature", "outra"), ("in_sync", False)):
            mexido = json.loads(json.dumps(base))
            mexido[caminho] = valor
            self.assertNotEqual(est1.digest_servidor(mexido), d0, f"{caminho} mudou: o digest tem de mudar (servidor reiniciado ou refeito)")
        peer_novo = json.loads(json.dumps(base))
        peer_novo["peers"].append({"instance_id": "android-09", "address": "10.66.0.9", "last_connection": None})
        self.assertNotEqual(est1.digest_servidor(peer_novo), d0, "peer novo é alteração de verdade")
        sem_peer = json.loads(json.dumps(base))
        sem_peer["peers"].pop()
        self.assertNotEqual(est1.digest_servidor(sem_peer), d0)
        self.assertEqual(base["peers"][0]["last_connection"], "-0300 2026-10-02 09:51:56", "a entrada não é alterada")


if __name__ == "__main__":
    unittest.main()
