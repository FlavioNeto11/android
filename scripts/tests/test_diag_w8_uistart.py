"""scripts/diag-w8-uistart.py: o toque único no Start do cliente no android-09. Só lógica e um aparelho FALSO (sem adb, sem central).

Protegem, nesta ordem: seguro por padrão e exclusivo do 09; o gate e a árvore da UI (sem r2 ou sem Start único = nenhum toque);
UM toque no Start (marcador impede o segundo) e UM no Stop só se subiu; o rollback; o vocabulário de escrita fechado.
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

ROOT = Path(__file__).resolve().parents[2]


def _carrega(nome: str, arquivo: str):
    spec = importlib.util.spec_from_file_location(nome, ROOT / arquivo)
    m = importlib.util.module_from_spec(spec)
    sys.modules[nome] = m
    spec.loader.exec_module(m)                                                    # type: ignore[union-attr]
    return m


mod = _carrega("diag_w8_uistart", "scripts/diag-w8-uistart.py")
_base = _carrega("test_diag_w8_tile_base", "scripts/tests/test_diag_w8_tile.py")
tile = mod.tile


def el(i: str, texto: str = "", *, clicavel: bool = False, bounds=(0, 0, 10, 10), pacote: str = tile.PACOTE) -> dict:
    return {"id": i, "text": texto, "desc": "", "package": pacote, "clickable": clicavel, "enabled": True, "bounds": list(bounds)}


class Falso09(_base.Falso):
    """O 09 de brinquedo + UI. `mapeia`: o Start da UI sobe o VPNService (A) ou o ProxyService que se encerra (B)."""

    def __init__(self, *, resultado="A", perfil=mod.PERFIL_ESPERADO, com_start=True, dois_starts=False, stop_funciona=True,
                 conectividade="healthy", **kw):
        super().__init__(**kw)
        self.resultado, self.perfil, self.com_start, self.dois_starts = resultado, perfil, com_start, dois_starts
        self.stop_funciona, self.conectividade = stop_funciona, conectividade
        self.rodando = False
        self.toques: list[str] = []

    def snapshot(self) -> dict:
        s = super().snapshot()
        s["instances"][0]["connectivity"]["state"] = self.conectividade
        return s

    def hierarquia(self) -> dict:
        els = [el("e1", "Dashboard")]
        if self.perfil:
            els.append(el("e2", self.perfil))
        if self.rodando:
            els += [el("e3", "", clicavel=True, bounds=(500, 1100, 700, 1200)), el("e4", "Stop", bounds=(560, 1130, 640, 1170))]
        elif self.com_start:
            els += [el("e3", "", clicavel=True, bounds=(500, 1100, 700, 1200)), el("e4", "Start", bounds=(560, 1130, 640, 1170))]
            if self.dois_starts:
                els += [el("e5", "", clicavel=True, bounds=(10, 10, 100, 100)), el("e6", "Start", bounds=(20, 20, 40, 40))]
        return {"elements": els}

    def shell(self, cmd: str, timeout: float = 60) -> tuple[int, str, str]:
        e = self.estado
        if cmd == mod.CMD_ABRIR:
            self.log.append(cmd)
            return 0, "Starting: Intent\n", ""
        if cmd == mod.CMD_HOME:
            self.log.append(cmd)
            return 0, "", ""
        if cmd == mod.CMD_FOCO:
            return 0, "mCurrentFocus=Window{1 u0 io.nekohasekai.sfa/io.nekohasekai.sfa.compose.MainActivity}\n", ""
        if cmd == mod.CMD_SERVICOS:
            if self.rodando:
                return 0, f"  * ServiceRecord{{1 u0 {tile.PACOTE}/.bg.VPNService}}\n    isForeground=true foregroundId=1\n", ""
            return 0, f"  * ServiceRecord{{2 u0 {tile.PACOTE}/.bg.ProxyService}}\n", ""
        if cmd.startswith("input tap"):
            self.log.append(cmd)
            self.toques.append(cmd)
            x, y = map(int, cmd.split()[2:4])
            if (x, y) == (600, 1150):
                if not self.rodando and self.resultado in ("A", "C"):
                    self.rodando = True
                    e.update(tun=self.resultado == "A", pid_sfa=e["pid_sfa"] or 7001)
                elif self.rodando and self.stop_funciona:
                    self.rodando = False
                    e["tun"] = False
            return 0, "", ""
        return super().shell(cmd, timeout)

    def escritas(self) -> list[str]:
        return [c for c in self.log if re.search(r"cmd statusbar|am force-stop|input |am start", c)]


def pasta() -> Path:
    d = Path(tempfile.mkdtemp(prefix="diagw8ui-"))
    (d / "meta.json").write_text("{}", encoding="utf-8")
    return d


def roda(argv: list[str], amb: Falso09 | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = mod.main(argv, amb)
    return rc, out.getvalue(), err.getvalue()


class SeguroPorPadrao(unittest.TestCase):
    def test_sem_execute_e_so_o_plano(self) -> None:
        amb = Falso09()
        rc, out, _ = roda(["ui"], amb)
        self.assertEqual(rc, 0)
        self.assertEqual(amb.log, [])
        self.assertIn("plano", json.loads(out)["modo"])

    def test_recusa_outro_aparelho_e_run_ausente(self) -> None:
        amb = Falso09()
        for argv in (["ui", "--execute", "--instance", "android-05", "--run", str(pasta())],
                     ["ui", "--execute", "--instance", "android-09"],
                     ["ui", "--execute", "--instance", "android-09", "--run", "/nao/existe"]):
            self.assertEqual(roda(argv, amb)[0], 2, argv)
        self.assertEqual(amb.log, [])

    def test_janela_minima_de_30_s(self) -> None:
        self.assertEqual(roda(["ui", "--execute", "--instance", "android-09", "--run", str(pasta()), "--observar-s", "10"])[0], 2)


class Arvore(unittest.TestCase):
    def test_start_e_o_conteiner_clicavel_do_rotulo(self) -> None:
        # a árvore real do 09: o contêiner clicável vem antes e o rótulo "Start" é um filho não clicável
        real = {"elements": [el("e16", "", clicavel=True, bounds=(576, 928, 688, 1040)),
                             el("e17", "Start", clicavel=False, bounds=(608, 960, 656, 1008))]}
        b = mod.achar_botao(real, "Start")
        self.assertEqual((b["id"], b["x"], b["y"]), ("e16", 632, 984))

    def test_start_recusa_ausente_repetido_de_outro_pacote_ou_sem_conteiner(self) -> None:
        self.assertIsNone(mod.achar_botao({"elements": [el("a", "Start")]}, "Start"))                     # sem contêiner clicável
        self.assertIsNone(mod.achar_botao({"elements": [el("c", "", clicavel=True, bounds=(0, 0, 100, 100)),
                                                       el("a", "Start", pacote="x.y", bounds=(10, 10, 20, 20))]}, "Start"))
        dois = [el("c", "", clicavel=True, bounds=(0, 0, 100, 100)), el("a", "Start", bounds=(10, 10, 20, 20)),
                el("b", "Start", bounds=(30, 30, 40, 40))]
        self.assertIsNone(mod.achar_botao({"elements": dois}, "Start"))
        fora = [el("c", "", clicavel=True, bounds=(0, 0, 5, 5)), el("a", "Start", bounds=(10, 10, 20, 20))]
        self.assertIsNone(mod.achar_botao({"elements": fora}, "Start"))                                   # não contém o rótulo

    def test_o_menor_conteiner_clicavel_vence(self) -> None:
        els = [el("grande", "", clicavel=True, bounds=(0, 0, 720, 1280)), el("peq", "", clicavel=True, bounds=(10, 10, 50, 50)),
               el("a", "Stop", bounds=(20, 20, 30, 30))]
        self.assertEqual(mod.achar_botao({"elements": els}, "Stop")["id"], "peq")

    def test_perfil_r2(self) -> None:
        self.assertTrue(mod.perfil_selecionado({"elements": [el("a", mod.PERFIL_ESPERADO)]}))
        self.assertFalse(mod.perfil_selecionado({"elements": [el("a", "plataforma-android-09-r1")]}))


class Fluxo(unittest.TestCase):
    def _roda(self, amb: Falso09, run: Path | None = None) -> dict:
        run = run or pasta()
        rc, out, _ = roda(["ui", "--execute", "--instance", "android-09", "--run", str(run)], amb)
        self.last_rc = rc
        return json.loads(out)

    def test_resultado_a_start_sobe_o_vpnservice_e_o_stop_da_ui_fecha(self) -> None:
        amb = Falso09(resultado="A")
        r = self._roda(amb)
        self.assertEqual([t["alvo"] for t in r["toques"]], ["Start", "Stop"])
        self.assertTrue(r["subiu"])
        self.assertEqual(r["servicos_depois"]["classes"], ["VPNService"])
        self.assertTrue(r["rollback"]["ok"])
        self.assertIsNone(r["rollback"]["extraordinario"])
        self.assertEqual(self.last_rc, 0)
        self.assertEqual(len(amb.toques), 2)

    def test_resultado_b_o_servico_nao_sobe_nenhum_stop(self) -> None:
        amb = Falso09(resultado="B")
        r = self._roda(amb)
        self.assertEqual([t["alvo"] for t in r["toques"]], ["Start"])
        self.assertFalse(r["subiu"])
        self.assertEqual(self.last_rc, 0)

    def test_stop_que_nao_pega_cai_no_rollback_extraordinario(self) -> None:
        amb = Falso09(resultado="A", stop_funciona=False)
        r = self._roda(amb)
        self.assertIsNotNone(r["rollback"]["extraordinario"])
        self.assertTrue(any("force-stop" in c for c in amb.log))

    def test_perfil_errado_ou_sem_start_nao_toca_em_nada(self) -> None:
        for kw in ({"perfil": "plataforma-android-09-r1"}, {"com_start": False}, {"dois_starts": True}):
            amb = Falso09(**kw)
            r = self._roda(amb)
            self.assertEqual(r["parada"], "UI_START_BLOCKED_ARVORE", kw)
            self.assertEqual(amb.toques, [], kw)
            self.assertEqual(self.last_rc, 3)
            self.assertIn(mod.CMD_HOME, amb.log)

    def test_gate_quebrado_bloqueia_sem_escrever(self) -> None:
        for kw in ({"conectividade": "degraded"}, {"always_on": tile.PACOTE}, {"tun": True}, {"abertos": [("c1", "restart", "running")]}):
            amb = Falso09(**kw)
            r = self._roda(amb)
            self.assertEqual(r["parada"], "UI_START_BLOCKED", kw)
            self.assertEqual(amb.escritas(), [], kw)

    def test_segunda_tentativa_e_recusada(self) -> None:
        run = pasta()
        self._roda(Falso09(resultado="A"), run)
        amb = Falso09(resultado="A")
        r = self._roda(amb, run)
        self.assertEqual(r["parada"], "SEGUNDO_TOQUE_RECUSADO")
        self.assertEqual(self.last_rc, 5)
        self.assertEqual(amb.log, [])


class VocabularioDeEscrita(unittest.TestCase):
    def test_vocabulario_de_escrita(self) -> None:
        amb = Falso09(resultado="A")
        run = pasta()
        roda(["ui", "--execute", "--instance", "android-09", "--run", str(run)], amb)
        for c in amb.escritas():
            ok = (c in (mod.CMD_ABRIR, mod.CMD_HOME) or re.fullmatch(r"input tap \d+ \d+", c))
            self.assertTrue(ok, f"escrita fora do vocabulário: {c!r}")
        self.assertFalse(any("force-stop" in c or "statusbar" in c for c in amb.log))


if __name__ == "__main__":
    unittest.main()
