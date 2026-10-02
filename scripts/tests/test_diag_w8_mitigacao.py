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
from datetime import datetime, timezone
from typing import Any, Callable

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


class _AmbWG:
    """Ambiente falso com servidor WireGuard e parque: tempo simulado; o reinício e a reconexão dos pares são roteirizados."""

    T0 = 1_790_000_000.0

    def __init__(self) -> None:
        self.t = self.T0
        self.pid, self.started = 1, self.T0 - 5000.0
        self.conn: dict[str, float | None] = {"android-03": self.T0 - 60.0, "android-06": self.T0 - 60.0}
        self.runs: list[tuple[Any, ...]] = []
        self.cmds: list[tuple[Any, ...]] = []
        self.liberar_runs_em: float | None = None
        self.reconecta_apos_s: float | None = 30.0
        self.restart_em: float | None = None

    def agora(self) -> float:
        return self.t

    def dormir(self, s: float) -> None:
        self.t += s
        if self.liberar_runs_em is not None and self.t - self.T0 >= self.liberar_runs_em:
            self.runs, self.liberar_runs_em = [], None
        if self.restart_em is not None and self.reconecta_apos_s is not None and self.t - self.restart_em >= self.reconecta_apos_s:
            self.conn = {k: self.t for k in self.conn}

    def restart(self) -> None:
        self.pid, self.started, self.restart_em = self.pid + 1, self.t, self.t

    def sql(self, consulta: str, params: tuple = ()) -> list[tuple]:
        return list(self.runs) if "FROM runs" in consulta else list(self.cmds)

    def snapshot(self) -> dict[str, Any]:
        return {"instances": [{"id": "android-03", "state": "online"}, {"id": "android-06", "state": "online"},
                              {"id": "android-02", "state": "hibernated"}, {"id": "android-05", "state": "hibernated"}]}

    def servidor(self) -> dict[str, Any]:
        def fmt(t: float | None) -> str | None:
            return None if t is None else datetime.fromtimestamp(t, tz=timezone.utc).strftime("+0000 %Y-%m-%d %H:%M:%S")
        iso = datetime.fromtimestamp(self.started, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return {"running": True, "pid": self.pid, "started_at": iso, "signature": "s",
                "peers": [{"instance_id": i, "last_connection": fmt(self.conn.get(i))} for i in mod.PARQUE_DO_SERVIDOR]}


class _gate_ok:
    """O pré-voo do ator (já testado à parte) passa: aqui só interessa o que vem depois dele."""

    def __enter__(self) -> None:
        self.orig = mod.boot.gate
        mod.boot.gate = lambda _a: {"ok": True, "falhas": [], "base": {}}         # type: ignore[assignment]

    def __exit__(self, *_a: Any) -> None:
        mod.boot.gate = self.orig                                                 # type: ignore[assignment]


class _Api:
    def __init__(self) -> None:
        self.chamadas: list[tuple[str, str, Any]] = []
        self.saude: dict[str, Any] = {"features": {"repair_pause": {"android-09": {}}}}
        self.resposta_firewall: dict[str, Any] = {"firewall": {"state": "liberado"}}
        self.ao_postar: Callable[[], None] | None = None

    def get(self, c: str) -> tuple[int, Any]:
        self.chamadas.append(("GET", c, None))
        return 200, self.saude

    def put(self, c: str, b: Any) -> tuple[int, Any]:
        self.chamadas.append(("PUT", c, b))
        return 200, {}

    def post(self, c: str, b: Any = None) -> tuple[int, Any]:
        self.chamadas.append(("POST", c, b))
        if c == "/api/network/server/firewall-check":
            return 200, self.resposta_firewall
        if self.ao_postar and c == "/api/network/assign":
            self.ao_postar()
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
        api, amb = _Api(), _AmbWG()
        api.ao_postar = amb.restart                                               # o assign reinicia o servidor (e o par reconecta)
        amb.reconecta_apos_s = 20.0
        r = mod.reverter(amb, api, lambda *_a, **_k: None)                       # type: ignore[arg-type]
        self.assertEqual([(m, c) for m, c, _ in api.chamadas], [("POST", "/api/network/assign"), ("DELETE", "/api/instances/android-09/repair-pause")])
        self.assertEqual(api.chamadas[0][2], mod.ROLLBACK)
        self.assertEqual(r["http"], 202)
        self.assertTrue(r["par_removido"])
        self.assertIn("fora dos 6 boots", r["reinicio_do_09_pelo_rollback"])

    def test_sem_par_aplicado_so_encerra_a_pausa(self) -> None:
        api = _Api()
        r = mod.reverter(None, api, lambda *_a, **_k: None, desfazer=False)      # type: ignore[arg-type]
        self.assertEqual([(m, c) for m, c, _ in api.chamadas], [("DELETE", "/api/instances/android-09/repair-pause")])
        self.assertFalse(r["par_removido"])


class SeguraDoServidorWireGuard(unittest.TestCase):
    """Condições do dono (02/10): janela ociosa antes de cada reinício, reconexão medida (≤ 2 min), firewall só lido, pausa conferida no health."""

    def test_o_horario_do_log_do_servidor_vira_epoch_utc(self) -> None:
        self.assertEqual(mod.epoch_da_conexao("-0300 2026-10-02 11:55:12"), mod.epoch_da_conexao("+0000 2026-10-02 14:55:12"))
        self.assertIsNone(mod.epoch_da_conexao(None))
        self.assertIsNone(mod.epoch_da_conexao("lixo"))

    def test_ocupacao_por_execucao_ou_comando_no_parque(self) -> None:
        self.assertEqual(mod.ocupacao([], []), [])
        self.assertEqual(mod.ocupacao([("r1", "running", '["android-03"]', None)], []), ["execução r1 (running)"])
        self.assertEqual(mod.ocupacao([("r2", "running", '["android-09"]', None)], []), [])             # só o 09: não é o parque
        self.assertEqual(len(mod.ocupacao([("r3", "running", "[]", None)], [])), 1)                     # sem alvo declarado = pode ser qualquer um
        self.assertEqual(mod.ocupacao([], [("android-06", "restart", "running"), ("android-09", "restart", "running")]),
                         ["comando restart/running em android-06"])

    def test_reconexao_exige_handshake_novo_dos_online_que_ja_tinham(self) -> None:
        antes = {"android-02": None, "android-03": 100.0, "android-05": None, "android-06": 100.0}
        online = {"android-03", "android-06"}
        self.assertEqual(mod.avaliar_reconexao(antes, antes, online, 200.0), (False, ["android-03", "android-06"], ["android-02", "android-05"]))
        ok, pend, _sem = mod.avaliar_reconexao(antes, {"android-03": 250.0, "android-06": 199.0}, online, 200.0)
        self.assertEqual((ok, pend), (False, ["android-06"]))
        self.assertTrue(mod.avaliar_reconexao(antes, {"android-03": 250.0, "android-06": 260.0}, online, 200.0)[0])
        self.assertTrue(mod.avaliar_reconexao(antes, {}, set(), 200.0)[0])      # nenhum online: nada a medir (fica declarado em sem_evidencia)

    def test_pausa_so_vale_se_consta_no_health(self) -> None:
        self.assertTrue(mod.pausa_vigente({"features": {"repair_pause": {"android-09": {}}}}))
        self.assertFalse(mod.pausa_vigente({"features": {"repair_pause": {"android-03": {}}}}))
        self.assertFalse(mod.pausa_vigente({"features": {}}))
        self.assertFalse(mod.pausa_vigente(None))

    def test_firewall_so_libera_no_estado_liberado_e_devolve_o_comando_do_dono(self) -> None:
        self.assertTrue(mod.firewall_liberado({"firewall": {"state": "liberado"}})[0])
        self.assertEqual(mod.firewall_liberado({"firewall": {"state": "sem_regra", "commands": ["New-NetFirewallRule ..."]}}),
                         (False, "sem_regra", ["New-NetFirewallRule ..."]))
        self.assertFalse(mod.firewall_liberado(None)[0])
        self.assertFalse(mod.firewall_liberado({"firewall": None})[0])

    def test_firewall_nao_liberado_bloqueia_sem_escrever_nada(self) -> None:
        amb, api = _AmbWG(), _Api()
        api.resposta_firewall = {"firewall": {"state": "sem_regra", "commands": ["New-NetFirewallRule X"]}}
        with tempfile.TemporaryDirectory() as d, _gate_ok():
            r = mod.rodar(amb, api, Path(d))                                      # type: ignore[arg-type]
        self.assertEqual(r["veredito"], "INCONCLUSIVE")
        self.assertIn("BLOQUEADO", r["motivo"])
        self.assertEqual(r["comandos_do_dono"], ["New-NetFirewallRule X"])
        self.assertEqual([c for _m, c, _b in api.chamadas], ["/api/network/server/firewall-check"], "só a leitura; nenhuma escrita")

    def test_espera_a_janela_ociosa_e_nao_mexe_se_ela_nao_vem(self) -> None:
        amb = _AmbWG()
        amb.runs = [("r1", "running", '["android-03"]', None)]                  # ocupado o tempo todo
        chamou: list[int] = []
        r = mod.mexer_no_servidor(amb, lambda *_a, **_k: None, "par", lambda: (chamou.append(1), (202, {}))[1])   # type: ignore[arg-type]
        self.assertFalse(r["ok"])
        self.assertEqual((r["fase"], r["acao_executada"]), ("janela_ociosa", False))
        self.assertEqual(chamou, [], "ocupado: a ação não roda (nunca interrompe)")
        self.assertGreaterEqual(amb.t - amb.T0, mod.JANELA_OCIOSA_LIMITE_S)

    def test_espera_ate_o_parque_ficar_ocioso_e_so_entao_age(self) -> None:
        amb = _AmbWG()
        amb.runs = [("r1", "running", '["android-03"]', None)]
        amb.liberar_runs_em = 300.0
        amb.reconecta_apos_s = 30.0
        r = mod.mexer_no_servidor(amb, lambda *_a, **_k: None, "par", lambda: (amb.restart(), (202, {}))[1])     # type: ignore[arg-type]
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["acao_executada"])
        self.assertGreaterEqual(amb.restart_em - amb.T0, 300.0, "só depois de a execução terminar")
        self.assertEqual(r["pendentes"], [])
        self.assertLess(r["levou_s"], mod.RECONEXAO_PRAZO_S)

    def test_par_online_que_nao_reconecta_em_2_min_para_a_mudanca(self) -> None:
        amb = _AmbWG()
        amb.reconecta_apos_s = None                                               # nunca reconecta
        r = mod.mexer_no_servidor(amb, lambda *_a, **_k: None, "par", lambda: (amb.restart(), (202, {}))[1])     # type: ignore[arg-type]
        self.assertFalse(r["ok"])
        self.assertEqual(r["fase"], "reconexao")
        self.assertEqual(r["pendentes"], ["android-03", "android-06"])
        self.assertGreaterEqual(r["levou_s"], mod.RECONEXAO_PRAZO_S)
        self.assertTrue(r["acao_executada"])

    def test_servidor_que_nao_reinicia_e_incerteza_e_para(self) -> None:
        amb = _AmbWG()
        r = mod.mexer_no_servidor(amb, lambda *_a, **_k: None, "par", lambda: (202, {}))                       # type: ignore[arg-type]
        self.assertEqual((r["ok"], r["fase"]), (False, "reinicio_nao_observado"))

    def test_rollback_urgente_nao_espera_a_janela(self) -> None:
        amb, api = _AmbWG(), _Api()
        amb.runs = [("r1", "running", '["android-03"]', None)]
        amb.reconecta_apos_s = 20.0
        api.ao_postar = amb.restart
        r = mod.reverter(amb, api, lambda *_a, **_k: None, urgente=True)        # type: ignore[arg-type]
        self.assertTrue(r["par_removido"])
        self.assertEqual(api.chamadas[0][1], "/api/network/assign")

    def test_rollback_sem_janela_fica_pendente_e_diz_o_que_fazer(self) -> None:
        amb, api = _AmbWG(), _Api()
        amb.runs = [("r1", "running", '["android-06"]', None)]
        r = mod.reverter(amb, api, lambda *_a, **_k: None)                       # type: ignore[arg-type]
        self.assertFalse(r["par_removido"])
        self.assertIn("CONTINUA no servidor", r["rollback_pendente"])
        self.assertEqual([c for _m, c, _b in api.chamadas], ["/api/instances/android-09/repair-pause"], "não interrompeu ninguém")

    def test_pausa_ausente_do_health_invalida_o_boot_antes_de_qualquer_escrita_de_rede(self) -> None:
        amb, api = _AmbWG(), _Api()
        api.saude = {"features": {}}
        with tempfile.TemporaryDirectory() as d:
            r = mod.uma_iteracao(amb, api, Path(d), 1, True, lambda *_a, **_k: None)   # type: ignore[arg-type]
        self.assertEqual(r["RESULT"], "BOOT_INVALID")
        self.assertIn("repair_pause", r["motivo"])
        self.assertNotIn("/api/network/assign", [c for _m, c, _b in api.chamadas])

    def test_plano_registra_a_sequencia_e_as_condicoes_do_servidor(self) -> None:
        p = mod.plano_json()
        self.assertEqual(tuple(p["janela_ociosa"]["parque"]), mod.PARQUE_DO_SERVIDOR)
        self.assertEqual(p["reconexao"]["prazo_s"], 120.0)
        self.assertTrue(any("health" in x for x in p["sequencia"]))


if __name__ == "__main__":
    unittest.main()
