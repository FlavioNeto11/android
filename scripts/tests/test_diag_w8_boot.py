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


if __name__ == "__main__":
    unittest.main()
