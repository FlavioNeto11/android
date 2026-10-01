"""scripts/diag-w8.py: coletor read-only do W8 do android-09. Só lógica pura: nenhum aparelho, nenhum SSH, nenhuma rede.

O que estes testes protegem, nesta ordem:
  1. o coletor só LÊ: nenhum comando que ele manda ao aparelho/notebook contém force-stop, tile, settings put, reboot...;
  2. o classificador aponta o PRIMEIRO ponto quebrado e NUNCA transforma ausência de dado em sucesso (UNKNOWN);
  3. os parsers entendem a saída do aparelho (como a de `comando_de_observacao`) e a do notebook.
"""
from __future__ import annotations

import importlib.util
import sys
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
        self.assertEqual(r["codigo"], "F6_TUN_NOT_CREATED", r)

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


if __name__ == "__main__":
    unittest.main()
