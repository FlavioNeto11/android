"""scripts/diag-w8-smoke-produto.py: o smoke real da função de produto. Só lógica e um aparelho FALSO (sem adb, sem central).

Protegem: seguro por padrão e exclusivo do 09; a função chamada é a REAL do branch (não uma cópia); o adaptador de aparelho
traduz a árvore do central para `NoDaTela` (nós sem rótulo incluídos) e recusa shell que falha.
"""
from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("diag_w8_smoke_produto", ROOT / "scripts" / "diag-w8-smoke-produto.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["diag_w8_smoke_produto"] = mod
spec.loader.exec_module(mod)                                                      # type: ignore[union-attr]


class AmbFalso:
    def __init__(self, rc: int = 0) -> None:
        self.rc, self.cmds = rc, []

    def shell(self, cmd: str, timeout: float = 60):
        self.cmds.append(cmd)
        return self.rc, "ok\n", "boom" if self.rc else ""

    def hierarquia(self) -> dict:
        return {"elements": [
            {"text": "", "desc": "", "package": mod.PACOTE, "clickable": True, "enabled": True, "bounds": [576, 928, 688, 1040]},
            {"text": "Start", "desc": "", "package": mod.PACOTE, "clickable": False, "enabled": True, "bounds": [608, 960, 656, 1008]}]}


class SmokeProduto(unittest.TestCase):
    def test_a_funcao_chamada_e_a_do_produto(self) -> None:
        from app.devices import rede_aplicacao as ra
        self.assertIs(mod.ra.religar_pela_interface, ra.religar_pela_interface)

    def test_seguro_por_padrao_so_mostra_o_plano(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(mod.main([]), 0)
        self.assertEqual(json.loads(out.getvalue())["modo"], "plano (nenhuma chamada)")

    def test_so_o_09_e_exige_a_pasta_do_coletor(self) -> None:
        with redirect_stderr(io.StringIO()):
            self.assertEqual(mod.main(["--execute", "--instance", "android-02", "--run", "."]), 2)
            self.assertEqual(mod.main(["--execute", "--instance", mod.IID]), 2)
            self.assertEqual(mod.main(["--execute", "--instance", mod.IID, "--run", "nao-existe-xyz"]), 2)

    def test_o_adaptador_traduz_a_arvore_e_toca_so_por_input_tap(self) -> None:
        amb = AmbFalso()
        ap = mod.AparelhoDoSmoke(amb)
        nos = asyncio.run(ap.arvore())
        self.assertEqual([(n.texto, n.clicavel) for n in nos], [("", True), ("Start", False)])
        no, _ = mod.ra.achar_botao(nos, mod.PACOTE, "Start")
        self.assertEqual(no.centro, (632, 984))
        asyncio.run(ap.tocar(632, 984))
        self.assertEqual(amb.cmds, ["input tap 632 984"])

    def test_a_raiz_do_central_sobe_ate_o_banco(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            raiz = Path(d)
            (raiz / "data").mkdir()
            (raiz / "data" / "poc.sqlite3").write_bytes(b"")
            filho = raiz / ".claude" / "worktrees" / "w"
            filho.mkdir(parents=True)
            self.assertEqual(mod.raiz_do_central(filho), raiz)
            self.assertEqual(mod.raiz_do_central(Path(d) / "x-nao-existe"), raiz)

    def test_shell_que_falha_levanta(self) -> None:
        with self.assertRaises(RuntimeError):
            asyncio.run(mod.AparelhoDoSmoke(AmbFalso(rc=1)).shell("echo x"))

    def test_vocabulario_de_escrita_do_script(self) -> None:
        plano = mod.plano()
        self.assertEqual(len(plano["escritas_possiveis"]), 4)
        for w in plano["escritas_possiveis"]:
            self.assertTrue(w.startswith(("am start -n", "input tap", "input keyevent KEYCODE_HOME")), w)


if __name__ == "__main__":
    unittest.main()
