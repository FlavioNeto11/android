"""scripts/diag-w8.py: coletor read-only do W8 do android-09. Só lógica pura: nenhum aparelho, nenhum SSH, nenhuma rede.

O que estes testes protegem, nesta ordem:
  1. o coletor só LÊ: nenhum comando que ele manda ao aparelho/notebook contém force-stop, tile, settings put, reboot...;
  2. o classificador aponta o PRIMEIRO ponto quebrado e NUNCA transforma ausência de dado em sucesso (UNKNOWN);
  3. os parsers entendem a saída do aparelho (como a de `comando_de_observacao`) e a do notebook.
"""
from __future__ import annotations

import importlib.util
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("diag_w8", ROOT / "scripts" / "diag-w8.py")
mod = importlib.util.module_from_spec(_spec)
sys.modules["diag_w8"] = mod
_spec.loader.exec_module(mod)                                                     # type: ignore[union-attr]

BASE = 1_000_000.0


def amostra(i: int, *, tile=False, ui=100, sfa=None, tun=False, vpn=None, regras=None, stopped=None,
            uptime=200, adb=True) -> dict:
    """Uma amostra do aparelho no instante BASE + 2*i. `vpn`/`regras`/`stopped` None = leitura leve (sem dumpsys)."""
    a = {"t": BASE + 2 * i, "ts": "x", "src": "android", "adb": adb, "uid": 2000, "uptime": uptime + 2 * i, "boot": True,
         "always_on": "io.nekohasekai.sfa", "lockdown": "1", "tun": tun, "pid_sfa": sfa, "pid_ui": ui, "tile": tile}
    if vpn is not None or regras is not None or stopped is not None:
        a.update({"vpn": vpn, "regras": regras, "stopped": stopped})
    return a


CLIQUE = "10-01 13:24:15.000  1  1 I TileService: io.nekohasekai.sfa onClick"
SERVICO = "10-01 13:24:17.000  1  1 I VpnService: Established by io.nekohasekai.sfa on tun0"


class SoLeitura(unittest.TestCase):
    def test_comandos_so_leem(self) -> None:
        self.assertEqual(mod.comandos_so_leem(), [])

    def test_o_guarda_pega_um_comando_que_escreve(self) -> None:
        proibidos = ["am force-stop io.x", "cmd statusbar click-tile a/b", "settings put secure x 1", "reboot",
                     "am start -n a/b", "svc wifi disable", "setprop a b", "input tap 1 1", "pm clear a"]
        for cmd in proibidos:
            with mock.patch.object(mod, "CMD_LEVE", cmd):
                self.assertTrue(mod.comandos_so_leem(), cmd)

    def test_o_codigo_nao_tem_escrita(self) -> None:
        """Sem `POST`, sem SQL que escreve, sem banco aberto para escrita: o coletor só tem GET e `mode=ro`."""
        import re as _re
        fonte = Path(mod.__file__).read_text(encoding="utf-8")
        codigo = fonte.split('"' * 3, 2)[2]                                       # fora da docstring do módulo
        self.assertIsNone(_re.search(r"(INSERT|UPDATE|DELETE|REPLACE)\s", codigo))
        self.assertIsNone(_re.search(r"method\s*=|urlopen\(\s*urllib\.request\.Request|mode=rw", codigo))
        self.assertIn("mode=ro", codigo)


class Parsers(unittest.TestCase):
    def test_amostra_leve_e_pesada(self) -> None:
        saida = ("U=2000\nS=185\nB=1\nA=io.nekohasekai.sfa\nL=1\nT=0\nPS=\nPU=1234\nQ=0\n"
                 "V=0\nR=3\nST=stopped=true\n")
        a = mod.amostra_do_aparelho(saida, BASE, pesada=True)
        self.assertEqual((a["uid"], a["uptime"], a["boot"], a["tun"], a["pid_sfa"], a["pid_ui"], a["tile"]),
                         (2000, 185, True, False, None, 1234, False))
        self.assertEqual((a["vpn"], a["regras"], a["stopped"]), (False, 3, True))
        leve = mod.amostra_do_aparelho("U=2000\nS=5\nT=1\nPS=777 778\nPU=1\nQ=1\n", BASE, pesada=False)
        self.assertNotIn("vpn", leve)
        self.assertEqual((leve["pid_sfa"], leve["tile"], leve["tun"]), (777, True, True))

    def test_saida_vazia_e_adb_ausente_nao_viram_zero(self) -> None:
        a = mod.amostra_do_aparelho("", BASE, pesada=True)
        self.assertFalse(a["adb"])
        self.assertIsNone(a["tun"])                                               # sem leitura ≠ sem túnel
        self.assertIsNone(a["vpn"])

    def test_notebook(self) -> None:
        n = mod.parse_notebook("ctr=Pages/sec=1\nctr=Committed Bytes=31000000000\nctr=Commit Limit=70000000000\n"
                               "ram_free_mb=36100\nqemu=4242:310.5:640\nadb=List of devices attached;emulator-5554\tdevice")
        self.assertEqual(n["ctr"]["Pages/sec"], 1)
        self.assertEqual(n["qemu"], [{"pid": 4242, "cpu_s": "310.5", "ws_mb": 640}])
        self.assertEqual(n["ram_free_mb"], 36100)

    def test_horarios(self) -> None:
        self.assertEqual(mod.epoch("-0300 2026-10-01 10:17:49"), mod.epoch("2026-10-01T13:17:49Z"))
        self.assertIsNone(mod.epoch("lixo"))

    def test_filtro_do_logcat(self) -> None:
        manter = ["I/ActivityManager: Start proc 123:io.nekohasekai.sfa/u0a1", "E ActivityManager: ANR in com.x",
                  "W ConnectivityService: VPN lockdown rules", "I TileService: QSTileHost add custom(x)"]
        largar = ["I ActivityManager: Displayed com.android.settings", "D SurfaceFlinger: frame", "I wifi: scan"]
        self.assertTrue(all(mod.logcat_mantem(x) for x in manter))
        self.assertFalse(any(mod.logcat_mantem(x) for x in largar))


class Preflight(unittest.TestCase):
    def test_ssh_ok_restrito_e_falhou(self) -> None:
        chave = Path(__file__)                                                    # qualquer arquivo existente
        with mock.patch.object(mod, "_executa", return_value=(0, "DIAGW8_OK\n")):
            self.assertEqual(mod.preflight_ssh(chave, "u", "h")["estado"], "ok")
        with mock.patch.object(mod, "_executa", return_value=(0, "")):            # `command="exit"`: conecta e sai
            self.assertEqual(mod.preflight_ssh(chave, "u", "h")["estado"], "restrito")
        with mock.patch.object(mod, "_executa", return_value=(255, "Permission denied")):
            self.assertEqual(mod.preflight_ssh(chave, "u", "h")["estado"], "falhou")
        self.assertEqual(mod.preflight_ssh(Path("nao-existe"), "u", "h")["estado"], "falhou")


class Classificador(unittest.TestCase):
    """Uma fixture por ponto quebrado, mais o caminho saudável. Cada uma é só o que o coletor veria."""

    def ctx(self, **kw) -> dict:
        return {"modo": "tile", "logcat_ok": True, "bloqueio": True, "handshake_depois": True, **kw}

    def test_saudavel(self) -> None:
        am = [amostra(0, tile=True, sfa=None), amostra(1, tile=True, sfa=500),
              amostra(2, tile=True, sfa=500, tun=True, vpn=True, regras=3, stopped=False)]
        r = mod.classificar(am, [CLIQUE, SERVICO], self.ctx())
        self.assertEqual(r["codigo"], "OK_HEALTHY", r)

    def test_f1_systemui_reiniciou(self) -> None:
        am = [amostra(0, ui=100), amostra(1, ui=100), amostra(2, ui=777)]
        self.assertEqual(mod.classificar(am, [], self.ctx())["codigo"], "F1_SYSTEMUI_RESTART")

    def test_f2_tile_nao_entrou(self) -> None:
        am = [amostra(i, tile=False) for i in range(4)]
        self.assertEqual(mod.classificar(am, [], self.ctx())["codigo"], "F2_TILE_NOT_ADDED")

    def test_f3_clicou_sem_efeito(self) -> None:
        am = [amostra(i, tile=True, sfa=None) for i in range(4)]
        r = mod.classificar(am, ["I Other: nada do tile do cliente"], self.ctx())
        self.assertEqual(r["codigo"], "F3_TILE_NOT_CLICKED", r)

    def test_f4_pacote_continua_stopped(self) -> None:
        am = [amostra(i, tile=True, sfa=None, stopped=True) for i in range(4)]
        r = mod.classificar(am, [CLIQUE], self.ctx())
        self.assertEqual(r["codigo"], "F4_PACKAGE_STAYS_STOPPED", r)

    def test_f5_processo_sobe_sem_servico(self) -> None:
        am = [amostra(0, tile=True, sfa=None, stopped=False), amostra(1, tile=True, sfa=500, stopped=False),
              amostra(2, tile=True, sfa=500, stopped=False)]
        r = mod.classificar(am, [CLIQUE, "E ActivityManager: ANR in io.nekohasekai.sfa"], self.ctx())
        self.assertEqual(r["codigo"], "F5_VPN_SERVICE_NOT_STARTED", r)

    def test_f6_processo_inicia_sem_tun0(self) -> None:
        am = [amostra(0, tile=True, sfa=None, stopped=False), amostra(1, tile=True, sfa=500, stopped=False),
              amostra(2, tile=True, sfa=500, stopped=False)]
        r = mod.classificar(am, [CLIQUE, SERVICO], self.ctx())
        self.assertEqual(r["codigo"], "F6_TUN_NOT_CREATED_AFTER_TILE", r)

    def test_f7_tun_sem_connected(self) -> None:
        am = [amostra(0, tile=True, sfa=500, stopped=False),
              amostra(1, tile=True, sfa=500, tun=True, vpn=False, regras=3, stopped=False),
              amostra(4, tile=True, sfa=500, tun=True, vpn=False, regras=3, stopped=False)]
        r = mod.classificar(am, [CLIQUE, SERVICO], self.ctx())
        self.assertEqual(r["codigo"], "F7_VPN_NOT_CONNECTED", r)

    def test_f8_lockdown_incoerente(self) -> None:
        am = [amostra(0, tile=True, sfa=500, stopped=False),
              amostra(1, tile=True, sfa=500, tun=True, vpn=True, regras=0, stopped=False)]
        r = mod.classificar(am, [CLIQUE, SERVICO], self.ctx(bloqueio=True))
        self.assertEqual(r["codigo"], "F8_LOCKDOWN_MISMATCH", r)

    def test_f9_tunel_saudavel_sem_handshake(self) -> None:
        am = [amostra(0, tile=True, sfa=500, stopped=False),
              amostra(1, tile=True, sfa=500, tun=True, vpn=True, regras=3, stopped=False)]
        r = mod.classificar(am, [CLIQUE, SERVICO], self.ctx(handshake_depois=False))
        self.assertEqual(r["codigo"], "F9_NO_WIREGUARD_HANDSHAKE", r)

    def test_f10_boot_sem_tunel(self) -> None:
        am = [amostra(i, uptime=150) for i in range(20)]
        r = mod.classificar(am, [], self.ctx(modo="boot"))
        self.assertEqual(r["codigo"], "F10_BOOT_RECOVERY_FAILED", r)

    def test_boot_que_ainda_nao_completou_180s_e_desconhecido(self) -> None:
        am = [amostra(i, uptime=20) for i in range(5)]
        self.assertEqual(mod.classificar(am, [], self.ctx(modo="boot"))["codigo"], "UNKNOWN")

    def test_f_other_tunel_caiu_no_fim(self) -> None:
        am = [amostra(0, tile=True, sfa=500, stopped=False),
              amostra(1, tile=True, sfa=500, tun=True, vpn=True, regras=3, stopped=False),
              amostra(2, tile=True, sfa=500, tun=False, vpn=True, regras=3, stopped=False)]
        r = mod.classificar(am, [CLIQUE, SERVICO], self.ctx())
        self.assertEqual(r["codigo"], "F_OTHER", r)

    def test_ausencia_de_log_nunca_vira_sucesso(self) -> None:
        # Sem logcat: o clique e o serviço não podem ser afirmados nem negados → UNKNOWN, nunca OK nem F3.
        am = [amostra(i, tile=True, sfa=None, stopped=False) for i in range(4)]
        r = mod.classificar(am, [], self.ctx(logcat_ok=False))
        self.assertEqual(r["codigo"], "UNKNOWN", r)
        self.assertNotEqual(r["codigo"], "OK_HEALTHY")

    def test_falha_com_estagio_anterior_desconhecido_e_so_candidata(self) -> None:
        am = [amostra(0, tile=True, sfa=500, tun=True, vpn=True, regras=0, stopped=False)]
        r = mod.classificar(am, [], self.ctx(logcat_ok=False))
        self.assertEqual(r["codigo"], "UNKNOWN", r)

    def test_sem_adb_na_janela_e_desconhecido(self) -> None:
        am = [amostra(i, adb=False) for i in range(5)]
        self.assertEqual(mod.classificar(am, [], self.ctx())["codigo"], "UNKNOWN")


# Linhas REAIS do A1 do android-09 (01/10, 13:39 -0300; logcat -b all): o que o coletor antigo perdeu e o novo precisa guardar.
L_CLICK = ("10-01 13:39:33.207  2529  2529 I sysui_multi_action: [757,925,758,4,759,268,806,io.nekohasekai.sfa,871,"
           "io.nekohasekai.sfa.bg.TileService,927,0,928,0,1592,0,1593,0]")
L_FGS_PERMITIDO = ("10-01 13:39:33.256   544   987 I ActivityManager: Background started FGS: Allowed [callingPackage: "
                   "io.nekohasekai.sfa; callingUid: 10196; uidState: CEM ; intent: Intent { cmp=io.nekohasekai.sfa/"
                   ".bg.ProxyService }; code:OP_ACTIVATE_VPN; tempAllowListReason:<null>; targetSdkVersion:37; "
                   "startForegroundCount:0; bindFromPackage:null: isBindService:false]")
L_START = ("10-01 13:39:33.540   544  2621 I am_foreground_service_start: [0,io.nekohasekai.sfa/.bg.ProxyService,0,"
           "OP_ACTIVATE_VPN,37,37,0,0,0,1,UNKNOWN,1073741824]")
L_STOP = ("10-01 13:39:35.336   544   987 I am_foreground_service_stop: [0,io.nekohasekai.sfa/.bg.ProxyService,0,"
          "OP_ACTIVATE_VPN,37,37,0,0,1789,1,STOP_FOREGROUND,1073741824]")
L_AVC_BIND = ('10-01 13:39:35.108  6512  6512 W DefaultDispatch: type=1400 audit(0.0:416): avc:  denied  { bind } for  '
              'scontext=u:r:untrusted_app:s0:c196,c256,c512,c768 tcontext=u:r:untrusted_app:s0:c196,c256,c512,c768 '
              'tclass=netlink_route_socket permissive=0 bug=b/155595000 app=io.nekohasekai.sfa')
L_AVC_SOMAXCONN = ('10-01 13:39:33.452  6512  6512 W DefaultDispatch: type=1400 audit(0.0:415): avc:  denied  { read } for  '
                   'name="somaxconn" dev="proc" ino=57151 scontext=u:r:untrusted_app:s0:c196,c256,c512,c768 '
                   'tcontext=u:object_r:proc_net:s0 tclass=file permissive=0 app=io.nekohasekai.sfa')
L_PROPRIA_SEM_PACOTE = ("10-01 13:39:34.415  6512  6518 I nekohasekai.sfa: Background young concurrent copying GC freed "
                        "31642(2336KB) AllocSpace objects")
L_REQUEST_NETWORK = ("10-01 13:39:33.614   544  2621 D ConnectivityService: requestNetwork for uid/pid:10196/6512 "
                     "activeRequest: null callbackRequest: 104")
L_AVC_DE_OUTRO = ('10-01 13:40:00.000   777   777 W DefaultDispatch: type=1400 audit(0.0:9): avc:  denied  { bind } for  '
                  'scontext=u:r:untrusted_app:s0:c11 tclass=netlink_route_socket permissive=0 app=com.outro.app')
RUIDO = ["10-01 13:39:36.000  1234  1234 D SurfaceFlinger: frame",
         "10-01 13:39:36.100   544   683 D ConnectivityService: NetReassign [104 : null → 101] [c 2] [a 5] [i 1]",
         "10-01 13:39:36.200   900   900 I wifi: scan done", L_AVC_DE_OUTRO]


class EvidenciaDoCliente(unittest.TestCase):
    def test_o_buffer_de_eventos_e_guardado(self) -> None:
        for linha in (L_CLICK, L_START, L_STOP, L_FGS_PERMITIDO, L_AVC_BIND, L_AVC_SOMAXCONN):
            self.assertTrue(mod.logcat_mantem(linha), linha)

    def test_linha_do_proprio_cliente_so_pelo_pid_vivo(self) -> None:
        self.assertFalse(mod.logcat_mantem(L_PROPRIA_SEM_PACOTE))
        self.assertTrue(mod.logcat_mantem(L_PROPRIA_SEM_PACOTE, {6512}))
        self.assertFalse(mod.logcat_mantem(L_PROPRIA_SEM_PACOTE, {777}))
        self.assertTrue(mod.logcat_mantem(L_REQUEST_NETWORK, {6512}))             # `uid/pid:10196/6512` no ConnectivityService

    def test_o_ruido_continua_fora_e_o_avc_de_outro_app_tambem(self) -> None:
        for linha in RUIDO:
            self.assertFalse(mod.logcat_mantem(linha, {6512}), linha)

    def test_avc_sem_o_pacote_mas_do_pid_do_cliente(self) -> None:
        linha = L_AVC_BIND.replace(" app=io.nekohasekai.sfa", "")
        self.assertFalse(mod.logcat_mantem(linha))
        self.assertTrue(mod.logcat_mantem(linha, {6512}))

    def test_pids_do_cliente_valem_um_tempo_depois_de_trocar(self) -> None:
        p = mod.Pids(retencao_s=100)
        p.ver(6512, 1000.0)
        p.ver(None, 1001.0)
        self.assertEqual(p.ativos(1050.0), {6512})
        p.ver(7001, 1090.0)
        self.assertEqual(p.ativos(1099.0), {6512, 7001})
        self.assertEqual(p.ativos(1150.0), {7001})

    def test_o_logcat_le_todos_os_buffers_e_so_le(self) -> None:
        cmd = mod.comando_logcat("adb", "127.0.0.1:15555")
        self.assertEqual(cmd[cmd.index("-b") + 1], "all")
        self.assertTrue(set(cmd) <= {"adb", "-s", "127.0.0.1:15555", "logcat", "-b", "all", "-v", "threadtime", "-T", "20"})

    def test_segredos_saem_por_formato(self) -> None:
        sujos = ['{"private_key": "AAAA1234bbbb"}', "PrivateKey = AAAA1234bbbb", "wireguard psk=ZZZZ9999", "password: hunter2",
                 "Authorization: Bearer abc.def.ghi"]
        for s in sujos:
            r = mod.redigir(s)
            self.assertIn("<redigido>", r, s)
            for resto in ("AAAA1234bbbb", "ZZZZ9999", "hunter2", "abc.def.ghi"):
                self.assertNotIn(resto, r)
        self.assertEqual(mod.redigir(L_START), L_START)

    def test_janela_larga_tem_teto_e_so_dois_arquivos(self) -> None:
        import tempfile
        pasta = Path(tempfile.mkdtemp(prefix="diagw8-"))
        saida = mod.Saida(pasta, janela_mb=0.002)                                 # 1000 B por arquivo
        for i in range(400):
            saida.linha_ampla(BASE + i, f"10-01 13:39:{i % 60:02d}.000   544   544 D Tag: linha numero {i} " + "x" * 40)
        arquivos = sorted(p.name for p in pasta.glob("logcat-wide.*.txt"))
        self.assertEqual(arquivos, ["logcat-wide.0.txt", "logcat-wide.1.txt"])
        total = sum((pasta / n).stat().st_size for n in arquivos)
        self.assertLess(total, 2 * 1000 + 2 * 200)
        texto = "".join((pasta / n).read_text(encoding="utf-8") for n in arquivos)
        self.assertIn("linha numero 399", texto)
        self.assertNotIn("linha numero 0 ", texto)

    def test_o_codigo_novo_continua_sem_escrita_no_aparelho(self) -> None:
        self.assertEqual(mod.comandos_so_leem(), [])

    def test_parada_limpa_pelo_arquivo_parar(self) -> None:
        import argparse
        import tempfile
        import threading
        pasta = Path(tempfile.mkdtemp(prefix="diagw8-"))

        class Falso:
            stdout = iter(())

            def kill(self) -> None:
                pass

        a = argparse.Namespace(adb="adb", saida=str(pasta), ssh_chave="x", ssh_usuario="u", ssh_host="h", serial="s",
                               instancia="android-09", db="nao-existe.sqlite3", api="http://127.0.0.1:1", duracao=60.0,
                               intervalo=0.1, pesado_a_cada=2, intervalo_central=0.1, intervalo_notebook=0.1,
                               max_logcat_mb=1.0, janela_mb=1.0)
        threading.Timer(0.8, lambda: (pasta / "parar").write_text("", encoding="utf-8")).start()
        with mock.patch.object(mod, "achar_adb", return_value="adb"), \
                mock.patch.object(mod, "preflight_ssh", return_value={"estado": "falhou", "detalhe": "x"}), \
                mock.patch.object(mod, "_executa", return_value=(0, "U=2000\nS=5\nT=0\nPS=6512\nPU=1\nQ=0\n")), \
                mock.patch.object(mod, "amostra_agente", return_value={"src": "agente"}), \
                mock.patch.object(mod, "amostra_peer", return_value={"src": "peer"}), \
                mock.patch.object(mod, "amostra_central", return_value=[]), \
                mock.patch.object(mod.subprocess, "Popen", return_value=Falso()):
            t0 = time.time()
            self.assertEqual(mod.coletar(a), 0)
        self.assertLess(time.time() - t0, 10)
        linhas = (pasta / "samples.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertTrue(any('"pid_sfa": 6512' in x for x in linhas))


class FalsoPassDoF5(unittest.TestCase):
    """`startForegroundCount:0` do ActivityManager NÃO é o serviço de VPN subindo (o A1 do 09 tinha o serviço de verdade)."""

    def ctx(self) -> dict:
        return {"modo": "tile", "logcat_ok": True, "bloqueio": False, "handshake_depois": None}

    def am(self) -> list[dict]:
        return [amostra(i, tile=True, sfa=6512, stopped=False, ui=2529) for i in range(4)]

    def test_so_a_linha_do_activitymanager_nao_aprova_o_f5(self) -> None:
        self.assertIsNone(mod.SERVICO_RE.search(L_FGS_PERMITIDO))
        self.assertIsNotNone(mod.SERVICO_RE.search(L_START))
        r = mod.classificar(self.am(), [CLIQUE, L_FGS_PERMITIDO], self.ctx())
        self.assertEqual(r["codigo"], "F5_VPN_SERVICE_NOT_STARTED", r)

    def test_servico_que_subiu_e_tun0_ausente_e_f6(self) -> None:
        r = mod.classificar(self.am(), [CLIQUE, L_FGS_PERMITIDO, L_START, L_STOP], self.ctx())
        self.assertEqual(r["codigo"], "F6_TUN_NOT_CREATED_AFTER_TILE", r)


class Sinais(unittest.TestCase):
    def setUp(self) -> None:
        self.t0 = mod.epoch("2026-10-01T16:39:30Z")

    def log(self, *linhas: str) -> list[tuple[float, str]]:
        return [(self.t0 + 3.2 + i * 0.1, x) for i, x in enumerate(linhas)]

    def test_a1_do_android_09(self) -> None:
        s = mod.sinais(self.log(L_CLICK, L_FGS_PERMITIDO, L_AVC_SOMAXCONN, L_START, L_AVC_BIND, L_STOP),
                       [amostra(i, tile=True, sfa=6512, stopped=False) for i in range(5)])
        self.assertEqual(s["clique_ate_proxy_start_s"], 0.333)
        self.assertEqual(s["proxy_start_ate_stop_s"], 1.796)
        self.assertTrue(s["proxy_start"] and s["proxy_stop"] and s["stop_foreground"])
        self.assertFalse(s["proxy_vivo_no_fim"])
        self.assertTrue(s["avc_netlink_bind_negado"] and s["avc_somaxconn_negado"])
        self.assertFalse(s["anr"] or s["established_by"])
        self.assertIsNone(s["primeiro_tun_apos_clique_s"])

    def test_servico_que_continua_vivo_e_tun0(self) -> None:
        t_clique = self.t0 + 3.2
        am = [dict(amostra(0), t=t_clique - 1, tun=False), dict(amostra(1), t=t_clique + 4.0, tun=True, vpn=True, regras=3, stopped=False)]
        s = mod.sinais(self.log(L_CLICK, L_START, "10-01 13:39:34.100   544   544 I VpnService: Established by io.nekohasekai.sfa on tun0"), am)
        self.assertFalse(s["proxy_stop"])
        self.assertTrue(s["proxy_vivo_no_fim"])
        self.assertTrue(s["established_by"])
        self.assertEqual(s["primeiro_tun_apos_clique_s"], 4.0)
        self.assertEqual(s["primeiro_vpn_connected_apos_clique_s"], 4.0)
        self.assertEqual(s["regras_ultima"], 3)

    def test_o_que_nao_foi_visto_e_none_nunca_zero(self) -> None:
        s = mod.sinais([], [])
        self.assertIsNone(s["clique_ate_proxy_start_s"])
        self.assertIsNone(s["proxy_vivo_no_fim"])
        self.assertIsNone(s["primeiro_tun_apos_clique_s"])
        self.assertFalse(s["proxy_start"])

    def test_o_peer_que_volta_depois_do_clique(self) -> None:
        peers = [{"src": "peer", "t": self.t0, "peer": {"last_connection": "-0300 2026-10-01 13:30:55"}},
                 {"src": "peer", "t": self.t0 + 20, "peer": {"last_connection": "-0300 2026-10-01 13:39:40"}}]
        s = mod.sinais(self.log(L_CLICK, L_START), [amostra(0)], peers)
        self.assertEqual(s["peer_last_connection_antes"], "2026-10-01T16:30:55.000Z")
        self.assertEqual(s["peer_voltou_apos_clique_s"], 6.8)

    def test_resumo_pela_cli_junta_o_filtrado_e_a_janela_larga_sem_repetir(self) -> None:
        import json as _json
        import tempfile
        from contextlib import redirect_stdout
        import io
        pasta = Path(tempfile.mkdtemp(prefix="diagw8-"))
        t = self.t0 + 3.2
        (pasta / "samples.jsonl").write_text(_json.dumps(dict(amostra(0), t=t - 1)) + "\n", encoding="utf-8")
        (pasta / "logcat.txt").write_text(f"{mod.iso(t)} {L_CLICK}\n{mod.iso(t + 0.3)} {L_START}\n", encoding="utf-8")
        (pasta / "logcat-wide.0.txt").write_text(f"{mod.iso(t)} {L_CLICK}\n{mod.iso(t + 2.1)} {L_STOP}\n", encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = mod.main(["resumo", "--run", str(pasta), "--desde", "2026-10-01T16:39:00Z", "--ate", "2026-10-01T16:41:00Z"])
        self.assertEqual(rc, 0)
        r = _json.loads(out.getvalue())
        self.assertEqual(r["logcat_linhas"], 3)                                   # o clique não conta duas vezes
        self.assertEqual(r["proxy_start_ate_stop_s"], 1.796)


if __name__ == "__main__":
    unittest.main()
