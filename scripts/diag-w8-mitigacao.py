#!/usr/bin/env python
"""Validação REAL da mitigação do W8 (PR #17: Start pela interface do cliente) no android-09. PROTOCOLO PRÉ-COMPROMETIDO, NÃO EXECUTADO.

Pergunta: depois de um boot em que o cliente VPN não sobe o túnel, o PRODUTO (a convergência de rede, sozinha) o recupera com UM Start
pela interface, sem reinício extra? (`docs/handoffs/w8-boot-recovery.md` §17.3 d e §17.5.)

Desenho: UM braço (mitigação ligada, o padrão do produto). O ATOR SÓ OBSERVA: ele pausa o reparo automático do aparelho (A2), aplica a
política de rede UMA vez e, a cada iteração, pede a REAPLICAÇÃO da revisão (o produto aplica, pede o reinício, espera o `tun0` por
`rede.espera_tun_s` e, sem ele, faz o Start pela interface). Nenhum toque, `force-stop`, Start ou Stop do ator durante o boot.

SEGURO POR PADRÃO: sem `--execute` só imprime o plano. `--execute` exige `--instance android-09`, uma pasta de evidência vazia e as TRÊS
confirmações explícitas (`--aplicar-politica`, `--aceito-reinicio-do-servidor-wireguard`, `--deploy-conferido`): aplicar a política cria o
par do 09 e REINICIA o servidor WireGuard do central (pisca o túnel de 02/03/05/06), e o rollback o reinicia de novo (revisão do dono).
Só o android-09; não toca outro aparelho; não mexe em DHCP, relógio, túnel SSH, firewall nem contas; sem chamada paga de IA.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]


def _carregar(nome: str, arquivo: str):
    spec = importlib.util.spec_from_file_location(nome, ROOT / "scripts" / arquivo)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)                                                  # type: ignore[union-attr]
    return mod


boot = _carregar("diag_w8_mitigacao_base", "diag-w8-boot.py")
est1 = _carregar("diag_w8_mitigacao_est1", "diag-w8-boot-estagio1.py")
IID = boot.IID
API = boot.API

# ------------------------------------------------------------------------------------------------ o compromisso (antes do 1º boot)
SEED = "w8-mitigacao-20261002"                                                    # identifica este protocolo; não há sorteio (braço único)
MAX_BOOTS = 6                                                                     # REINÍCIOS REAIS, contando os que o produto pede
MAX_ITERACOES = 6
CATEGORIAS = ("NO_FAILURE", "RECOVERED_BY_UI", "RECOVERED_BY_RESTART", "NOT_RECOVERED", "BOOT_INVALID", "UNKNOWN")
JANELA_DO_BOOT_S = 900.0                                                          # 180 s de espera do tun0 + Start + reinício, com folga
PAUSA_TTL_S = 1800
POLITICA = {"instance_ids": [IID], "vpn_profile_id": "vpn-central-wireguard", "policy": "livre"}      # NÃO aplicada por este módulo sem as 3 flags
ROLLBACK = {"instance_ids": [IID], "vpn_profile_id": None, "proxy_profile_id": None}
MARCA_DO_START = "religado pelo Start da interface do cliente"
VERDITOS = ("PASS", "PARTIAL", "FAIL", "INCONCLUSIVE")
BONS = ("NO_FAILURE", "RECOVERED_BY_UI")
RUINS = ("RECOVERED_BY_RESTART", "NOT_RECOVERED", "BOOT_INVALID", "UNKNOWN")


# ------------------------------------------------------------------------------------------------ lógica pura (testada sem rede)
def classificar_iteracao(o: dict[str, Any]) -> tuple[str, str]:
    """Uma das 6 categorias, de uma observação já reduzida (nada de adb aqui).

    `invalidos` (lista de motivos) → BOOT_INVALID. Sem leitura final (`conectado` é None) → UNKNOWN. Túnel no ar e linha conectada:
    `ui_ok` (o log do produto diz que o Start da interface religou) e nenhuma falha da interface → RECOVERED_BY_UI; falha da interface
    seguida de reinício → RECOVERED_BY_RESTART; nenhum Start e um só reinício (o do próprio gatilho) → NO_FAILURE; reinício a mais sem
    o Start → RECOVERED_BY_RESTART. Sem túnel até o fim da janela → NOT_RECOVERED."""
    if o.get("invalidos"):
        return "BOOT_INVALID", "; ".join(o["invalidos"])[:300]
    if o.get("conectado") is None:
        return "UNKNOWN", "não foi possível ler o estado final (tun0/linha de rede)"
    reinicios, falhas_ui = int(o.get("reinicios_da_rede", 0)), list(o.get("falhas_da_interface", []))
    if not o["conectado"]:
        return "NOT_RECOVERED", f"sem túnel até o fim da janela (reinícios da rede: {reinicios}; interface: {falhas_ui or 'sem falha registrada'})"
    if o.get("ui_ok") and not falhas_ui and reinicios <= 1:
        return "RECOVERED_BY_UI", "o produto religou pelo Start da interface, sem reinício extra"
    if falhas_ui or reinicios > 1:
        return "RECOVERED_BY_RESTART", f"o túnel voltou depois de reinício(s) extra(s) ({reinicios}; falhas da interface: {falhas_ui})"
    return "NO_FAILURE", "o tun0 subiu sem o Start (o always-on funcionou neste boot)"


def avaliar_parada(regs: list[dict[str, Any]], boots_consumidos: int, max_boots: int = MAX_BOOTS) -> tuple[str, str] | None:
    """Regra de parada depois de cada iteração. (veredito, motivo) ou None para seguir. FAIL em qualquer ruim; PASS com 2 RECOVERED_BY_UI."""
    ruins = [r for r in regs if r["RESULT"] in RUINS]
    if ruins:
        u = ruins[0]
        return "FAIL", f"iteração {u['ITERACAO']} terminou em {u['RESULT']}: {u.get('motivo', '')}"[:300]
    if sum(1 for r in regs if r["RESULT"] == "RECOVERED_BY_UI") >= 2:
        return "PASS", "2 recuperações pelo Start da interface, sem desfecho ruim"
    if boots_consumidos >= max_boots or len(regs) >= MAX_ITERACOES:
        return desfecho(regs, "TETO"), f"teto atingido ({boots_consumidos} boots, {len(regs)} iterações)"
    return None


def desfecho(regs: list[dict[str, Any]], motivo: str = "") -> str:
    """O veredito final dado o que foi observado (usado no teto): PARTIAL com 1 recuperação pelo Start; INCONCLUSIVE sem nenhuma (a
    mitigação não foi exercitada: só prova não-regressão); PASS/FAIL já saem de `avaliar_parada`."""
    if any(r["RESULT"] in RUINS for r in regs):
        return "FAIL"
    n = sum(1 for r in regs if r["RESULT"] == "RECOVERED_BY_UI")
    return "PASS" if n >= 2 else "PARTIAL" if n == 1 else "INCONCLUSIVE"


def pode_iniciar(boots_consumidos: int, iteracoes: int) -> bool:
    return boots_consumidos < MAX_BOOTS and iteracoes < MAX_ITERACOES


def reinicios_desde(comandos: list[dict[str, Any]], quem: str) -> int:
    """Os `restart` ACEITOS (não rejeitados) pedidos por `quem`: cada um é um boot real."""
    return sum(1 for c in comandos if c["verb"] == "restart" and c["requested_by"] == quem and c["state"] != "rejected")


def falhas_da_interface(comandos: list[dict[str, Any]]) -> list[str]:
    """Os códigos `[interface: <codigo>]` que a convergência põe na razão do reinício quando o Start da interface não religou."""
    achados: list[str] = []
    for c in comandos:
        achados += re.findall(r"\[interface: ([a-z_0-9]+)\]", str(c.get("reason") or ""))
    return achados


def comandos_do_sistema_nao_rejeitados(comandos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """O quase-acidente do §17.4: um `requested_by='system'` (escada de reparo) que NÃO foi rejeitado na janela invalida a iteração."""
    return [c for c in comandos if c["requested_by"] == "system" and c["state"] != "rejected"]


def plano_json() -> dict[str, Any]:
    return {"modo": "plano (nenhuma chamada)", "instancia": IID, "seed": SEED, "max_boots_reais": MAX_BOOTS, "max_iteracoes": MAX_ITERACOES,
            "um_braco": "mitigação ligada (o padrão do produto); o ator só observa",
            "politica_a_aplicar": POLITICA, "rollback": ROLLBACK, "pausa_do_reparo": {"ttl_s": PAUSA_TTL_S, "rota": f"PUT /api/instances/{IID}/repair-pause"},
            "iteracao": ["pausar o reparo automático (A2)", "baseline", "1ª: a atribuição da política aplica e pede o reinício; demais: POST "
                         f"/api/network/devices/{IID}/reapply + /apply", "observar até o estado final ou {JANELA_DO_BOOT_S:.0f} s",
                         "ler tun0 e a linha de rede", "classificar", "regra de parada"],
            "categorias": CATEGORIAS,
            "criterios": {"PASS": "≥ 2 RECOVERED_BY_UI e nenhum desfecho ruim", "PARTIAL": "1 RECOVERED_BY_UI e nenhum ruim ao fim do teto",
                          "FAIL": "qualquer RECOVERED_BY_RESTART, NOT_RECOVERED, BOOT_INVALID ou UNKNOWN",
                          "INCONCLUSIVE": "só NO_FAILURE até o teto: a mitigação não foi exercitada (apenas não-regressão)"},
            "boot_invalido_se": ["comando requested_by='system' não rejeitado na janela", "baseline falho", "evento de outro aparelho",
                                 "automação perdida que impeça a leitura final"],
            "nao_faz": ["toque, force-stop, Start ou Stop do ator", "estágio 2", "outro aparelho", "DHCP/relógio/túnel SSH/firewall", "conta real",
                        "chamada paga de IA", "sleep/retry/watchdog novo"]}


# ------------------------------------------------------------------------------------------------ o executor (só com as 3 confirmações)
class Api:
    """Chamadas HTTP ao central (loopback). Escritas só pelas rotas do produto: pausa do reparo, atribuição e reaplicação da rede."""

    def __init__(self, base: str = API) -> None:
        self.base = base

    def _req(self, metodo: str, caminho: str, corpo: dict[str, Any] | None = None) -> tuple[int, Any]:
        dados = json.dumps(corpo).encode() if corpo is not None else None
        req = urllib.request.Request(self.base + caminho, data=dados, method=metodo, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:                    # noqa: S310 - loopback do central
                txt = r.read().decode("utf-8")
                return r.status, (json.loads(txt) if txt else None)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")[:300]

    def get(self, c: str) -> tuple[int, Any]:
        return self._req("GET", c)

    def put(self, c: str, b: dict[str, Any]) -> tuple[int, Any]:
        return self._req("PUT", c, b)

    def post(self, c: str, b: dict[str, Any] | None = None) -> tuple[int, Any]:
        return self._req("POST", c, b)

    def delete(self, c: str) -> tuple[int, Any]:
        return self._req("DELETE", c)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int((t % 1) * 1000):03d}Z"


def comandos_da_janela(amb: Any, desde_iso: str) -> list[dict[str, Any]]:
    linhas = amb.sql("SELECT id, verb, state, requested_by, created_at, reason FROM commands WHERE instance_id=? AND created_at>=? ORDER BY created_at",
                     (IID, desde_iso))
    return [dict(zip(("id", "verb", "state", "requested_by", "created_at", "reason"), r)) for r in linhas]


def estado_da_rede(amb: Any) -> dict[str, Any] | None:
    r = amb.sql("SELECT state, detail, error, desired_rev, applied_rev FROM device_network WHERE instance_id=?", (IID,))
    return dict(zip(("state", "detail", "error", "desired_rev", "applied_rev"), r[0])) if r else None


def log_do_start(amb: Any, desde_iso: str) -> bool:
    """O produto emite um evento `log` quando o Start da interface religou o túnel (`rede_convergencia._religar_sem_reinicio`)."""
    r = amb.sql("SELECT COUNT(*) FROM events WHERE instance_id=? AND ts>=? AND kind='log' AND message LIKE ?", (IID, desde_iso, f"%{MARCA_DO_START}%"))
    return bool(r and r[0][0])


def outros_aparelhos(amb: Any, desde_iso: str) -> list[str]:
    """Eventos de ciclo de vida/rede de OUTRO aparelho na janela (sinal para parar). `device.network` de 02/03/05/06 é a medição periódica da
    plataforma (visto em 02/10): conta como observação, não como efeito; restart/reset/stop de outro aparelho invalida."""
    r = amb.sql("SELECT instance_id, verb, requested_by FROM commands WHERE instance_id<>? AND created_at>=? AND verb IN ('restart','reset','stop')",
                (IID, desde_iso))
    return [f"{a}:{b}:{c}" for a, b, c in r]


def uma_iteracao(amb: Any, api: Api, run: Path, n: int, primeira: bool, reg: Callable[..., None]) -> dict[str, Any]:
    d = run / f"i{n}"
    d.mkdir(parents=True, exist_ok=False)
    inicio = amb.agora()
    desde = _iso(inicio)
    invalidos: list[str] = []
    st, _ = api.put(f"/api/instances/{IID}/repair-pause", {"ttl_s": PAUSA_TTL_S, "reason": f"validação da mitigação W8, iteração {n}"})
    if st != 200:
        return {"ITERACAO": n, "RESULT": "BOOT_INVALID", "motivo": f"a pausa do reparo não foi aceita (HTTP {st})", "reinicios_da_rede": 0}
    b = est1.baseline(amb) if not primeira else {"ok": True, "falhas": [], "nota": "a 1ª iteração parte do baseline do pré-voo"}
    (d / "baseline.json").write_text(json.dumps(b, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if not b["ok"] and not primeira:
        invalidos.append(f"baseline: {b['falhas']}")
    if primeira:
        st, corpo = api.post("/api/network/assign", POLITICA)
        reg("atribuir", http=st)
        if st >= 300:
            invalidos.append(f"atribuição recusada (HTTP {st}): {str(corpo)[:120]}")
    else:
        st1, _ = api.post(f"/api/network/devices/{IID}/reapply")
        st2, _ = api.post(f"/api/network/devices/{IID}/apply")
        reg("reaplicar", http_reapply=st1, http_apply=st2)
        if st1 >= 300:
            invalidos.append(f"reaplicação recusada (HTTP {st1})")
    final: dict[str, Any] = {}
    resets = 0
    if not invalidos:
        fim = amb.agora() + JANELA_DO_BOOT_S
        ultimo_u: float | None = None
        with (d / "amostras.jsonl").open("a", encoding="utf-8") as fh:
            while amb.agora() < fim:
                v = boot.lido(amb, boot.CMD_AMOSTRA, 15)
                if v and v.get("U"):
                    u = float(v["U"])
                    if ultimo_u is not None and u < ultimo_u - 5:
                        resets += 1                                               # o uptime voltou atrás: um boot real
                    ultimo_u = u
                    fh.write(json.dumps({"t": boot.iso(amb.agora()), "u": u, "boot": v.get("B"), "tun": v.get("T")}) + chr(10))
                    fh.flush()
                linha, cmds = estado_da_rede(amb), comandos_da_janela(amb, desde)
                abertos = [c for c in cmds if c["state"] not in ("succeeded", "failed", "rejected", "cancelled")]
                assentou = bool(linha) and linha["state"] in ("conectado", "trafego_verificado", "parcial") and not abertos
                if assentou and (resets or reinicios_desde(cmds, "rede")):
                    break
                amb.dormir(10.0)
        try:
            boot.capturar(amb, d, "os")
        except Exception as exc:  # noqa: BLE001 - captura é melhor esforço (só leituras): a classificação não depende dela
            reg("captura_falhou", erro=str(exc)[:120])
        v = boot.lido(amb, boot.CMD_AMOSTRA, 20) or {}
        final = {"tun": v.get("T") not in (None, "", "0")} if v else {}
    cmds = comandos_da_janela(amb, desde)
    sistema = comandos_do_sistema_nao_rejeitados(cmds)
    if sistema:
        invalidos.append(f"comando(s) da escada na janela: {[c['id'] for c in sistema]}")
    if (alheios := outros_aparelhos(amb, desde)):
        invalidos.append(f"ciclo de vida de outro aparelho na janela: {alheios}")
    linha = estado_da_rede(amb)
    conectado = None if (linha is None or "tun" not in final) else bool(final["tun"] and linha["state"] in ("conectado", "trafego_verificado", "parcial"))
    o = {"invalidos": invalidos, "conectado": conectado, "ui_ok": log_do_start(amb, desde), "reinicios_da_rede": reinicios_desde(cmds, "rede"),
         "falhas_da_interface": falhas_da_interface(cmds)}
    cat, motivo = classificar_iteracao(o)
    reg_final = {"ITERACAO": n, "RESULT": cat, "motivo": motivo, "reinicios_da_rede": o["reinicios_da_rede"], "ui_ok": o["ui_ok"],
                 "falhas_da_interface": o["falhas_da_interface"], "boots_observados": resets, "reinicios_totais": max(resets, reinicios_desde(cmds, "rede")),
                 "linha_de_rede": linha, "tun": final.get("tun")}
    (d / "resultado.json").write_text(json.dumps(reg_final, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return reg_final


def rodar(amb: Any, api: Api, run: Path) -> dict[str, Any]:
    reg = boot.tile.Registro(run / "acionador-mitigacao.jsonl")
    saida: dict[str, Any] = {"seed": SEED, "inicio": boot.iso(), "iteracoes": [], "veredito": None}
    g = boot.gate(amb)
    (run / "preflight.json").write_text(json.dumps(g, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    saida["wg_antes"] = est1.digest_servidor(amb.servidor()) if hasattr(est1, "digest_servidor") else None
    if not g["ok"]:
        saida["veredito"], saida["motivo"] = "FAIL", f"pré-voo falhou antes de qualquer escrita: {g['falhas']}"
        return saida
    regs: list[dict[str, Any]] = []
    boots = 0
    try:
        while pode_iniciar(boots, len(regs)):
            r = uma_iteracao(amb, api, run, len(regs) + 1, not regs, reg)
            regs.append(r)
            boots += max(1, int(r.get("reinicios_totais") or 0))
            saida["iteracoes"], saida["boots_consumidos"] = regs, boots
            (run / "mitigacao.estado.json").write_text(json.dumps(saida, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            p = avaliar_parada(regs, boots)
            if p:
                saida["veredito"], saida["motivo"] = p
                break
    finally:
        saida["rollback"] = reverter(amb, api, reg)
    saida["veredito"] = saida["veredito"] or desfecho(regs)
    saida["wg_depois"] = est1.digest_servidor(amb.servidor())
    saida["fim"] = boot.iso()
    (run / "mitigacao.estado.json").write_text(json.dumps(saida, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return saida


def reverter(amb: Any, api: Api, reg: Callable[..., None]) -> dict[str, Any]:
    """Tira a política (a plataforma desfaz o always-on e reinicia o aparelho UMA vez, fora da conta dos boots) e encerra a pausa."""
    st, corpo = api.post("/api/network/assign", ROLLBACK)
    reg("rollback_da_politica", http=st)
    return {"http": st, "corpo": str(corpo)[:200], "pausa_encerrada": api.delete(f"/api/instances/{IID}/repair-pause")[0]}


def main(argv: list[str] | None = None, amb: Any = None, api: Api | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--instance", default=None)
    ap.add_argument("--run", default=None, help="pasta de evidência (precisa existir e estar VAZIA)")
    ap.add_argument("--aplicar-politica", action="store_true")
    ap.add_argument("--aceito-reinicio-do-servidor-wireguard", action="store_true")
    ap.add_argument("--deploy-conferido", action="store_true", help="o central roda um commit com a pausa do reparo (A2) e o PR #17")
    a = ap.parse_args(argv)
    if not a.execute:
        print(json.dumps(plano_json(), ensure_ascii=False, indent=2))
        return 0
    if a.instance != IID:
        print(f"recusado: só o {IID} (recebido {a.instance!r})", file=sys.stderr)
        return 2
    if not (a.aplicar_politica and a.aceito_reinicio_do_servidor_wireguard and a.deploy_conferido):
        print("recusado: faltam --aplicar-politica, --aceito-reinicio-do-servidor-wireguard e --deploy-conferido", file=sys.stderr)
        return 2
    if not a.run or not Path(a.run).is_dir() or any(Path(a.run).iterdir()):
        print("recusado: --run <pasta> precisa existir e estar VAZIA", file=sys.stderr)
        return 2
    amb = amb or boot.Ambiente()
    api = api or Api()
    r = rodar(amb, api, Path(a.run))
    print(json.dumps({"veredito": r["veredito"], "motivo": r.get("motivo"), "boots_consumidos": r.get("boots_consumidos"),
                      "iteracoes": [{k: i.get(k) for k in ("ITERACAO", "RESULT")} for i in r["iteracoes"]], "rollback": r.get("rollback")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
