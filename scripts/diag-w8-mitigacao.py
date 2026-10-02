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
Autorização do dono, 02/10, via sessão orquestradora: os 2 reinícios do servidor WireGuard só em JANELA OCIOSA de 02/03/05/06 (sem execução nem comando
aberto; espera, nunca interrompe), com a reconexão medida (≤ 2 min, senão PARA e tira o par); o firewall só é LIDO (regra ausente = bloqueado, não cria).
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
PARQUE_DO_SERVIDOR = ("android-02", "android-03", "android-05", "android-06")     # os pares que o reinício do servidor WireGuard pisca
JANELA_OCIOSA_LIMITE_S = 1800.0                                                   # quanto espera por uma janela ociosa; nunca interrompe
RECONEXAO_PRAZO_S = 120.0                                                         # depois do reinício do servidor: sem handshake novo → PARAR
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


def epoch_da_conexao(txt: Any) -> float | None:
    """`last_connection` do servidor: o relógio do log do sing-box, `-0300 2026-10-02 11:55:12` (deslocamento e hora local) → epoch UTC."""
    m = re.fullmatch(r"([+-])(\d{2})(\d{2}) (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})", str(txt or "").strip())
    if not m:
        return None
    sinal, hh, mm, dia, hora = m.groups()
    local = datetime.strptime(f"{dia} {hora}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    deslocamento = (int(hh) * 3600 + int(mm) * 60) * (1 if sinal == "+" else -1)
    return local - deslocamento


def conexoes_dos_pares(srv: dict[str, Any]) -> dict[str, float | None]:
    return {p["instance_id"]: epoch_da_conexao(p.get("last_connection")) for p in srv.get("peers", []) if p.get("instance_id") in PARQUE_DO_SERVIDOR}


def ocupacao(runs: list[tuple[Any, ...]], comandos: list[tuple[Any, ...]]) -> list[str]:
    """Por que o parque do servidor WireGuard NÃO está ocioso (lista vazia = ocioso). `runs`: (id, status, instance_ids, instances_used) das
    execuções não terminais; `comandos`: (instance_id, verb, state) dos comandos não terminais recentes. Execução sem alvo declarado conta
    como ocupada (pode estar nos quatro)."""
    motivos: list[str] = []
    for rid, status, ids, usados in runs:
        alvos = f"{ids or ''} {usados or ''}"
        if not (ids or "").strip("[] \n") or any(i in alvos for i in PARQUE_DO_SERVIDOR):
            motivos.append(f"execução {rid} ({status})")
    for iid, verb, state in comandos:
        if iid in PARQUE_DO_SERVIDOR:
            motivos.append(f"comando {verb}/{state} em {iid}")
    return motivos


def avaliar_reconexao(antes: dict[str, float | None], depois: dict[str, float | None], online: set[str], t_restart: float) -> tuple[bool, list[str], list[str]]:
    """(tudo_ok, pendentes, sem_evidencia). Um aparelho ONLINE que já tinha handshake antes precisa de um handshake DEPOIS do reinício do
    servidor; sem handshake anterior (ou aparelho que não está online) não há como medir por aqui: vai em `sem_evidencia` (não é sucesso nem
    falha: é incerteza declarada)."""
    pendentes: list[str] = []
    sem: list[str] = []
    for iid in PARQUE_DO_SERVIDOR:
        if iid not in online or antes.get(iid) is None:
            sem.append(iid)
        elif (depois.get(iid) or 0.0) < t_restart:
            pendentes.append(iid)
    return not pendentes, pendentes, sem


def pausa_vigente(health: dict[str, Any] | None, iid: str = IID) -> bool:
    """`GET /api/health` → `features.repair_pause` tem o aparelho (a pausa é só em memória: reiniciar o central a apaga)."""
    return bool(health) and iid in ((health or {}).get("features", {}) or {}).get("repair_pause", {})


def firewall_liberado(resposta: dict[str, Any] | None) -> tuple[bool, str, list[str]]:
    """`POST /api/network/server/firewall-check` (só leitura): o par remoto chega pela LAN e só passa com a regra do dono. Qualquer estado
    que não seja `liberado` bloqueia: o script NÃO cria regra; devolve os comandos que o dono roda."""
    fw = (resposta or {}).get("firewall") or {}
    return fw.get("state") == "liberado", str(fw.get("state")), list(fw.get("commands") or [])


def plano_json() -> dict[str, Any]:
    return {"modo": "plano (nenhuma chamada)", "instancia": IID, "seed": SEED, "max_boots_reais": MAX_BOOTS, "max_iteracoes": MAX_ITERACOES,
            "um_braco": "mitigação ligada (o padrão do produto); o ator só observa",
            "politica_a_aplicar": POLITICA, "rollback": ROLLBACK, "pausa_do_reparo": {"ttl_s": PAUSA_TTL_S, "rota": f"PUT /api/instances/{IID}/repair-pause"},
            "sequencia": ["deploy conferido (commit com a pausa e o PR #17)", "firewall-check (leitura): liberado", "janela ociosa de 02/03/05/06",
                          "pausa do reparo ligada e CONFERIDA no health ANTES de cada boot", "par do 09 (reinício 1 do servidor WireGuard) e "
                          f"medição da reconexão (≤ {RECONEXAO_PRAZO_S:.0f} s)", "boots (até 6)", "janela ociosa de novo, rollback (reinício 2 do servidor) e "
                          "medição da reconexão", "o reinício do android-09 pelo rollback fica FORA dos 6 e é contado à parte"],
            "janela_ociosa": {"parque": PARQUE_DO_SERVIDOR, "espera_max_s": JANELA_OCIOSA_LIMITE_S, "nunca_interrompe": True},
            "reconexao": {"prazo_s": RECONEXAO_PRAZO_S, "parada": "algum aparelho online do parque sem handshake novo no prazo → PARAR, tirar o par, relatar"},
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


def parque_ocupado(amb: Any) -> list[str]:
    """O parque do servidor WireGuard está ocupado? (leitura: execuções não terminais e comandos abertos dos últimos 30 min em 02/03/05/06)."""
    runs = amb.sql("SELECT id, status, instance_ids, instances_used FROM runs WHERE status IN ('planning','needs_input','running','paused','cancelling')")
    cmds = amb.sql("SELECT instance_id, verb, state FROM commands WHERE created_at>=? AND state NOT IN ('succeeded','failed','rejected','cancelled','uncertain')",
                   (_iso(amb.agora() - 1800.0),))
    return ocupacao(runs, cmds)


def esperar_janela_ociosa(amb: Any, reg: Callable[..., None], rotulo: str, limite_s: float = JANELA_OCIOSA_LIMITE_S) -> tuple[bool, list[str]]:
    """Espera duas leituras seguidas (15 s) sem ocupação. Nunca interrompe nada: esgotado o limite, devolve (False, motivos) e o chamador NÃO mexe."""
    fim, seguidas, motivos = amb.agora() + limite_s, 0, []
    while True:
        motivos = parque_ocupado(amb)
        seguidas = 0 if motivos else seguidas + 1
        if seguidas >= 2:
            reg("janela_ociosa", rotulo=rotulo, ok=True)
            return True, []
        if amb.agora() >= fim:
            reg("janela_ociosa", rotulo=rotulo, ok=False, ocupacao=motivos)
            return False, motivos
        amb.dormir(15.0)


def _epoch_iso(txt: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(txt).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def esperar_reinicio_do_servidor(amb: Any, ref: tuple[Any, Any], limite_s: float = 180.0) -> float | None:
    """O servidor WireGuard reiniciou? (pid ou `started_at` diferentes e `running`). Devolve o epoch do novo `started_at`, ou None."""
    fim = amb.agora() + limite_s
    while True:
        srv = amb.servidor()
        if srv.get("running") and (srv.get("pid"), srv.get("started_at")) != ref:
            return _epoch_iso(srv.get("started_at")) or amb.agora()
        if amb.agora() >= fim:
            return None
        amb.dormir(5.0)


def medir_reconexao(amb: Any, antes: dict[str, float | None], t_restart: float, prazo_s: float = RECONEXAO_PRAZO_S) -> dict[str, Any]:
    """Depois do reinício do servidor: cada par ONLINE que já tinha handshake precisa de um novo. Registra quanto levou e o que não mediu."""
    t0 = amb.agora()
    online = {i["id"] for i in amb.snapshot().get("instances", []) if (i.get("state") or i.get("status")) == "online"}
    while True:
        ok, pendentes, sem = avaliar_reconexao(antes, conexoes_dos_pares(amb.servidor()), online, t_restart)
        if ok or amb.agora() - t0 >= prazo_s:
            return {"ok": ok, "pendentes": pendentes, "sem_evidencia": sem, "levou_s": round(amb.agora() - t0, 1), "online": sorted(online)}
        amb.dormir(10.0)


def mexer_no_servidor(amb: Any, reg: Callable[..., None], rotulo: str, acao: Callable[[], tuple[int, Any]], *, urgente: bool = False) -> dict[str, Any]:
    """Uma mudança de pares que REINICIA o servidor WireGuard do central: janela ociosa antes (salvo `urgente`, o rollback depois de uma
    falha de reconexão), ação pela rota do produto, reinício observado e reconexão medida. `ok` False = o chamador PARA e reverte."""
    if not urgente:
        ok, motivos = esperar_janela_ociosa(amb, reg, rotulo)
        if not ok:
            return {"ok": False, "fase": "janela_ociosa", "ocupacao": motivos, "acao_executada": False}
    srv = amb.servidor()
    antes, ref, t_acao = conexoes_dos_pares(srv), (srv.get("pid"), srv.get("started_at")), amb.agora()
    st, corpo = acao()
    reg("servidor_wg", rotulo=rotulo, http=st)
    if st >= 300:
        return {"ok": False, "fase": "acao_recusada", "http": st, "corpo": str(corpo)[:200], "acao_executada": False}
    t_restart = esperar_reinicio_do_servidor(amb, ref)
    if t_restart is None:
        return {"ok": False, "fase": "reinicio_nao_observado", "acao_executada": True}
    rec = medir_reconexao(amb, antes, t_restart)
    reg("reconexao", rotulo=rotulo, **rec)
    return {"ok": rec["ok"], "fase": "reconexao", "acao_executada": True, "http": st, "reinicio_apos_s": round(t_restart - t_acao, 1), **rec}


def uma_iteracao(amb: Any, api: Api, run: Path, n: int, primeira: bool, reg: Callable[..., None]) -> dict[str, Any]:
    d = run / f"i{n}"
    d.mkdir(parents=True, exist_ok=False)
    inicio = amb.agora()
    desde = _iso(inicio)
    invalidos: list[str] = []
    st, _ = api.put(f"/api/instances/{IID}/repair-pause", {"ttl_s": PAUSA_TTL_S, "reason": f"validação da mitigação W8, iteração {n}"})
    if st != 200:
        return {"ITERACAO": n, "RESULT": "BOOT_INVALID", "motivo": f"a pausa do reparo não foi aceita (HTTP {st})", "reinicios_da_rede": 0}
    st_h, saude = api.get("/api/health")                                          # a pausa é só em memória (um reinício do central a apaga)
    if st_h != 200 or not pausa_vigente(saude if isinstance(saude, dict) else None):
        return {"ITERACAO": n, "RESULT": "BOOT_INVALID", "motivo": "a pausa não consta em features.repair_pause do health antes do boot", "reinicios_da_rede": 0}
    b = est1.baseline(amb) if not primeira else {"ok": True, "falhas": [], "nota": "a 1ª iteração parte do baseline do pré-voo"}
    (d / "baseline.json").write_text(json.dumps(b, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if not b["ok"] and not primeira:
        invalidos.append(f"baseline: {b['falhas']}")
    wg_parou, wg = False, {}
    if primeira:
        wg = mexer_no_servidor(amb, reg, "par do android-09", lambda: api.post("/api/network/assign", POLITICA))
        (d / "servidor-wg.json").write_text(json.dumps(wg, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if not wg["ok"]:
            wg_parou = True
            invalidos.append(f"servidor WireGuard ({wg['fase']}): {json.dumps({k: wg[k] for k in ('pendentes', 'ocupacao', 'http') if k in wg}, ensure_ascii=False)}")
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
                 "linha_de_rede": linha, "tun": final.get("tun"), "wg_parou": wg_parou, "wg": wg}
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
    _st, resp = api.post("/api/network/server/firewall-check")                    # só leitura: nunca cria regra (é do dono)
    liberado, estado_fw, comandos_fw = firewall_liberado(resp if isinstance(resp, dict) else None)
    reg("firewall_check", estado=estado_fw, liberado=liberado)
    if not liberado:
        saida["veredito"], saida["motivo"] = "INCONCLUSIVE", f"BLOQUEADO: a regra do firewall para o par remoto não está liberada ({estado_fw}); nada foi escrito"
        saida["comandos_do_dono"] = comandos_fw
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
            if r.get("wg_parou"):                                                  # janela ociosa não veio, ou um par online não reconectou
                saida["veredito"], saida["motivo"] = "INCONCLUSIVE", f"ABORTADO no servidor WireGuard: {r['motivo']}"
                break
            p = avaliar_parada(regs, boots)
            if p:
                saida["veredito"], saida["motivo"] = p
                break
    finally:
        wg1 = (regs[0].get("wg") or {}) if regs else {}
        saida["rollback"] = reverter(amb, api, reg, desfazer=bool(wg1.get("acao_executada")), urgente=bool(regs and regs[0].get("wg_parou")))
    saida["veredito"] = saida["veredito"] or desfecho(regs)
    saida["wg_depois"] = est1.digest_servidor(amb.servidor())
    saida["fim"] = boot.iso()
    (run / "mitigacao.estado.json").write_text(json.dumps(saida, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return saida


def reverter(amb: Any, api: Api, reg: Callable[..., None], *, desfazer: bool = True, urgente: bool = False) -> dict[str, Any]:
    """Tira a política (a plataforma desfaz o always-on, reinicia o aparelho UMA vez, FORA da conta dos 6 boots e contado à parte, e reinicia o
    servidor WireGuard) e encerra a pausa. O rollback do par espera a janela ociosa (nunca interrompe): sem ela fica PENDENTE e o relatório
    traz o que fazer. `urgente` (depois de uma falha de reconexão) não espera. Sem par aplicado (`desfazer` False) só encerra a pausa."""
    saida: dict[str, Any] = {"par_removido": False, "reinicio_do_09_pelo_rollback": "fora dos 6 boots; contado e registrado à parte"}
    if desfazer:
        wg = mexer_no_servidor(amb, reg, "rollback do par", lambda: api.post("/api/network/assign", ROLLBACK), urgente=urgente)
        saida.update({"http": wg.get("http"), "wg": wg, "par_removido": bool(wg.get("acao_executada"))})
        if not wg["ok"] and not wg.get("acao_executada"):
            saida["rollback_pendente"] = f"o par do {IID} CONTINUA no servidor: {wg['fase']}; refazer o POST /api/network/assign {json.dumps(ROLLBACK)} numa janela ociosa"
    saida["pausa_encerrada"] = api.delete(f"/api/instances/{IID}/repair-pause")[0]
    return saida


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
                      "iteracoes": [{k: i.get(k) for k in ("ITERACAO", "RESULT")} for i in r["iteracoes"]], "rollback": r.get("rollback"),
                      "comandos_do_dono": r.get("comandos_do_dono")},
                     ensure_ascii=False, indent=2))
    return 3 if (r.get("rollback") or {}).get("rollback_pendente") else 0


if __name__ == "__main__":
    raise SystemExit(main())
