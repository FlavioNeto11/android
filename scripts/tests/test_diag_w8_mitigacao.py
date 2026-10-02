"""scripts/diag-w8-mitigacao.py: o protocolo pré-comprometido da validação real da mitigação do W8. Só lógica e executor com ambiente FALSO
(sem adb, sem central, sem aparelho, sem rede). O que protege: seguro por padrão e exclusivo do android-09, as três confirmações, a
classificação fechada, as regras de parada e que o ator não escreve nada fora das rotas do produto."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("diag_w8_mitigacao", ROOT / "scripts" / "diag-w8-mitigacao.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["diag_w8_mitigacao"] = mod
spec.loader.exec_module(mod)                                                      # type: ignore[union-attr]


def _obs(**kw: Any) -> dict[str, Any]:
    base = {"invalidos": [], "conectado": True, "ui_ok": False, "reinicios_da_rede": 1, "falhas_da_interface": []}
    base.update(kw)
    return base


def _reg(n: int, cat: str) -> dict[str, Any]:
    return {"ITERACAO": n, "RESULT": cat, "motivo": "x"}


class Classificacao(unittest.TestCase):
    def test_as_seis_categorias_fechadas(self) -> None:
        casos = {
            "NO_FAILURE": _obs(),
            "RECOVERED_BY_UI": _obs(ui_ok=True),
            "RECOVERED_BY_RESTART": _obs(reinicios_da_rede=2, falhas_da_interface=["tun_nao_subiu"]),
            "NOT_RECOVERED": _obs(conectado=False),
            "BOOT_INVALID": _obs(invalidos=["comando da escada"]),
            "UNKNOWN": _obs(conectado=None),
        }
        for esperado, o in casos.items():
            self.assertEqual(mod.classificar_iteracao(o)[0], esperado, esperado)
        self.assertEqual(set(casos), set(mod.CATEGORIAS))

    def test_invalido_vence_tudo_e_unknown_vence_o_resto(self) -> None:
        self.assertEqual(mod.classificar_iteracao(_obs(invalidos=["a"], conectado=False, ui_ok=True))[0], "BOOT_INVALID")
        self.assertEqual(mod.classificar_iteracao(_obs(conectado=None, ui_ok=True))[0], "UNKNOWN")

    def test_o_start_so_conta_sem_falha_da_interface_e_sem_reinicio_extra(self) -> None:
        self.assertEqual(mod.classificar_iteracao(_obs(ui_ok=True, falhas_da_interface=["tun_nao_subiu"], reinicios_da_rede=2))[0],
                         "RECOVERED_BY_RESTART")                                  # o Start falhou e o reinício é que religou
        self.assertEqual(mod.classificar_iteracao(_obs(ui_ok=True, reinicios_da_rede=2))[0], "RECOVERED_BY_RESTART")
        self.assertEqual(mod.classificar_iteracao(_obs(reinicios_da_rede=2))[0], "RECOVERED_BY_RESTART")   # reinício a mais sem o Start

    def test_comandos_de_reparo_e_codigos_da_interface(self) -> None:
        cmds = [{"id": "a", "verb": "restart", "state": "succeeded", "requested_by": "rede", "reason": "rede: [interface: tun_nao_subiu] x"},
                {"id": "b", "verb": "restart", "state": "rejected", "requested_by": "system", "reason": ""},
                {"id": "c", "verb": "restart", "state": "succeeded", "requested_by": "rede", "reason": "rede: configurado"},
                {"id": "d", "verb": "reset", "state": "created", "requested_by": "system", "reason": ""}]
        self.assertEqual(mod.reinicios_desde(cmds, "rede"), 2)
        self.assertEqual(mod.falhas_da_interface(cmds), ["tun_nao_subiu"])
        self.assertEqual([c["id"] for c in mod.comandos_do_sistema_nao_rejeitados(cmds)], ["d"])   # o rejeitado do §17.4 não invalida


class Parada(unittest.TestCase):
    def test_qualquer_desfecho_ruim_e_fail(self) -> None:
        for ruim in mod.RUINS:
            self.assertEqual(mod.avaliar_parada([_reg(1, "NO_FAILURE"), _reg(2, ruim)], 2)[0], "FAIL", ruim)

    def test_dois_recuperados_pelo_start_e_pass_sem_gastar_o_resto(self) -> None:
        self.assertIsNone(mod.avaliar_parada([_reg(1, "RECOVERED_BY_UI")], 1))
        self.assertEqual(mod.avaliar_parada([_reg(1, "NO_FAILURE"), _reg(2, "RECOVERED_BY_UI"), _reg(3, "RECOVERED_BY_UI")], 3)[0], "PASS")

    def test_o_teto_decide_partial_ou_inconclusive(self) -> None:
        seis = [_reg(i, "NO_FAILURE") for i in range(1, 7)]
        self.assertEqual(mod.avaliar_parada(seis, 6)[0], "INCONCLUSIVE")           # a mitigação não foi exercitada
        um = seis[:5] + [_reg(6, "RECOVERED_BY_UI")]
        self.assertEqual(mod.avaliar_parada(um, 6)[0], "PARTIAL")
        self.assertIsNone(mod.avaliar_parada(seis[:3], 3))                         # abaixo do teto e sem desfecho: segue

    def test_o_teto_conta_reinicios_reais_inclusive_os_do_produto(self) -> None:
        self.assertTrue(mod.pode_iniciar(5, 5))
        self.assertFalse(mod.pode_iniciar(6, 3))
        self.assertFalse(mod.pode_iniciar(2, 6))
        self.assertEqual(mod.MAX_BOOTS, 6)


class SegurancaDoScript(unittest.TestCase):
    def test_sem_execute_so_imprime_o_plano_e_nao_chama_nada(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(mod.main([]), 0)
        p = json.loads(out.getvalue())
        self.assertEqual(p["modo"], "plano (nenhuma chamada)")
        self.assertEqual((p["instancia"], p["seed"], p["max_boots_reais"]), ("android-09", "w8-mitigacao-20261002", 6))
        self.assertEqual(p["politica_a_aplicar"]["instance_ids"], ["android-09"])

    def test_recusa_outro_aparelho_falta_de_confirmacao_e_pasta_suja(self) -> None:
        confirmacoes = ["--aplicar-politica", "--aceito-reinicio-do-servidor-wireguard", "--deploy-conferido"]
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()):
            self.assertEqual(mod.main(["--execute", "--instance", "android-01", "--run", d, *confirmacoes]), 2)
            self.assertEqual(mod.main(["--execute", "--instance", "android-09", "--run", d]), 2)
            for faltando in range(3):                                             # cada confirmação é obrigatória
                self.assertEqual(mod.main(["--execute", "--instance", "android-09", "--run", d,
                                           *[c for i, c in enumerate(confirmacoes) if i != faltando]]), 2)
            (Path(d) / "sujo.txt").write_text("x")
            self.assertEqual(mod.main(["--execute", "--instance", "android-09", "--run", d, *confirmacoes]), 2)

    def test_o_script_so_escreve_pelas_rotas_do_produto(self) -> None:
        fonte = (ROOT / "scripts" / "diag-w8-mitigacao.py").read_text(encoding="utf-8").split('"""', 2)[2]
        for proibido in ("amb.shell(", "settings put", "am force-stop", "svc wifi", "svc data", "pm clear", "wg set", "wg-quick", "input tap",
                         "uninstall", "KEYCODE", "tocar_botao", "preparar("):
            self.assertNotIn(proibido, fonte, proibido)
        rotas = {"/api/network/assign", "/repair-pause", "/reapply", "/apply"}
        self.assertTrue(all(r in fonte for r in rotas))

    def test_o_rollback_tira_so_a_politica_do_09(self) -> None:
        self.assertEqual(mod.ROLLBACK["instance_ids"], ["android-09"])
        self.assertIsNone(mod.ROLLBACK["vpn_profile_id"])
        self.assertEqual(mod.POLITICA["policy"], "livre")


class _Amb:
    """Ambiente falso: o aparelho nunca é tocado; as respostas vêm de um roteiro."""

    def __init__(self, gate_ok: bool = True) -> None:
        self.t, self.gate_ok, self.chamadas = 1000.0, gate_ok, []

    def agora(self) -> float:
        return self.t

    def dormir(self, s: float) -> None:
        self.t += s

    def servidor(self) -> dict[str, Any]:
        return {"pid": 1, "started_at": "x", "signature": "s", "peers": [{"instance_id": "android-02", "last_connection": None}]}


class _Api:
    def __init__(self) -> None:
        self.chamadas: list[tuple[str, str, Any]] = []

    def put(self, c: str, b: Any) -> tuple[int, Any]:
        self.chamadas.append(("PUT", c, b))
        return 200, {}

    def post(self, c: str, b: Any = None) -> tuple[int, Any]:
        self.chamadas.append(("POST", c, b))
        return 202, {}

    def delete(self, c: str) -> tuple[int, Any]:
        self.chamadas.append(("DELETE", c, None))
        return 200, {}


class Executor(unittest.TestCase):
    def test_pre_voo_falho_nao_escreve_nada(self) -> None:
        amb, api = _Amb(), _Api()
        orig = mod.boot.gate
        mod.boot.gate = lambda _a: {"ok": False, "falhas": ["sem_peer_no_servidor"], "base": {}}     # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as d:
                r = mod.rodar(amb, api, Path(d))                                  # type: ignore[arg-type]
        finally:
            mod.boot.gate = orig
        self.assertEqual(r["veredito"], "FAIL")
        self.assertEqual(api.chamadas, [], "nenhuma escrita antes de o pré-voo passar")

    def test_reverter_tira_a_politica_e_encerra_a_pausa(self) -> None:
        api = _Api()
        r = mod.reverter(None, api, lambda *_a, **_k: None)                      # type: ignore[arg-type]
        self.assertEqual([(m, c) for m, c, _ in api.chamadas], [("POST", "/api/network/assign"), ("DELETE", "/api/instances/android-09/repair-pause")])
        self.assertEqual(api.chamadas[0][2], mod.ROLLBACK)
        self.assertEqual(r["http"], 202)


if __name__ == "__main__":
    unittest.main()
