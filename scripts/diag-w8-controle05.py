#!/usr/bin/env python
"""CONTROL-05 do W8 — controle positivo no android-05, o par conhecido-bom (docs/handoffs/w8-diagnostico-android09.md §16).

Perfil EXCLUSIVO do android-05 (QA), separado de `diag-w8-tile.py` (que é só do android-09): reaproveita o gesto do produto e
a leitura do aparelho de lá, mas o gate, o rollback e a recusa de outros aparelhos são outros. SEGURO POR PADRÃO.

Subcomandos:
  plano      mostra o que faria (nenhuma chamada)
  monitorar  somente leitura: central (estado do aparelho, `device_network`, comandos, eventos, par, conectividade) e, quando o
             adb do 05 existe, uma leitura leve do aparelho. Roda antes, durante e depois do wake; para pelo arquivo
             `parar-monitor` na pasta da execução
  baseline   somente leitura: o 05 está CONVERGIDO? (online, `trafego_verificado`, tun0, VPN CONNECTED, always-on, lockdown, regras,
             par com handshake depois do wake, conectividade `healthy`, nenhum comando aberto, silêncio de comandos de rede)
  controle   UMA tentativa (`--execute --instance android-05 --run <coletor>`): `am force-stop` → processo/tun0 ausentes → o MESMO
             gesto instrumentado do tile → observa 30 s → devolve o tile ao Q0. Um marcador na pasta impede a segunda tentativa.

Escritas possíveis no aparelho (o teste `test_vocabulario_de_escrita` fecha a lista): `am force-stop io.nekohasekai.sfa` e
`cmd statusbar remove-tile|add-tile|click-tile` do tile do cliente. NADA de segundo force-stop, de clique de desligar nem de
reinício: se o tile não religar, a recuperação é a NORMAL do produto (convergência), que este script só observa.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _carregar_tile():
    spec = importlib.util.spec_from_file_location("diag_w8_tile_base", ROOT / "scripts" / "diag-w8-tile.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["diag_w8_tile_base"] = mod
    spec.loader.exec_module(mod)                                                  # type: ignore[union-attr]
    return mod


tile = _carregar_tile()
coletor = tile.coletor
PACOTE = tile.PACOTE
IID = "android-05"
SERIAL = "emulator-5562"
WORKER_LOCAL = "WIN-7S2UASNLFOP"
POLITICA = "exigida_com_bloqueio"
OBSERVAR_S = 30.0
CMD_PACOTE = (f"dumpsys package {PACOTE} 2>/dev/null | grep -E 'versionName|versionCode|installerPackageName|lastUpdateTime' | head -5; "
              f"echo PM=$(pm path {PACOTE} | head -1)")
CMD_NETLINK = ("echo NL=$(logcat -b all -d 2>/dev/null | grep -c 'avc:.*denied.*netlink_route_socket'); "
               f"echo NLAPP=$(logcat -b all -d 2>/dev/null | grep 'avc:.*denied.*netlink_route_socket' | grep -c '{PACOTE}')")
CMD_MONITOR_LEVE = "echo B=$(getprop sys.boot_completed); echo S=$(cut -d. -f1 /proc/uptime); " + tile.CMD_LEVE_UNICA


def agora() -> float:
    return time.time()


def iso(t: float | None = None) -> str:
    return coletor.iso(agora() if t is None else t)


# ---------------------------------------------------------------------------------------------------- ambiente do 05
class Ambiente(tile.Ambiente):
    """O mundo, com o serial do 05. Os testes trocam isto por um falso."""

    def shell(self, cmd: str, timeout: float = 60) -> tuple[int, str, str]:
        try:
            r = subprocess.run([self.adb, "-s", SERIAL, "shell", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, timeout=timeout, encoding="utf-8", errors="replace")
            return r.returncode, r.stdout, r.stderr
        except (subprocess.TimeoutExpired, OSError) as exc:
            return 255, "", f"ERRO {type(exc).__name__}: {exc}"

    def logcat_vivo(self) -> tuple[bool, str]:
        """Um `adb logcat -b all` do COLETOR para este serial (a plataforma também abre um, sem `-b all`)."""
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='adb.exe'\" | Where-Object { $_.CommandLine -match 'logcat' "
              f"-and $_.CommandLine -match '-b all' -and $_.CommandLine -match '{re.escape(SERIAL)}' }} | "
              "Select-Object -ExpandProperty ProcessId")
        rc, out = coletor._executa(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], 30)
        pids = [x for x in out.split() if x.isdigit()]
        return bool(pids), f"adb logcat -b all pid(s) {','.join(pids) or '-'}"


def pares(texto: str) -> dict[str, str]:
    return tile.pares(texto)


# ---------------------------------------------------------------------------------------------------- baseline
def ler_pacote(amb: Ambiente) -> dict[str, Any]:
    rc, out, _ = amb.shell(CMD_PACOTE, 40)
    txt = out if rc == 0 else ""
    return {"versionName": (re.search(r"versionName=(\S+)", txt) or [None, None])[1],
            "versionCode": (re.search(r"versionCode=(\d+)", txt) or [None, None])[1],
            "installer": (re.search(r"installerPackageName=(\S+)", txt) or [None, None])[1],
            "lastUpdateTime": (re.search(r"lastUpdateTime=(.+)", txt) or [None, None])[1],
            "pm_path": pares(txt).get("PM", "")}


def ler_netlink(amb: Ambiente) -> dict[str, Any]:
    """Quantas negações de `netlink bind` o buffer do aparelho JÁ tem (antes do experimento): só uma contagem."""
    rc, out, _ = amb.shell(CMD_NETLINK, 60)
    v = pares(out if rc == 0 else "")
    n, napp = tile.coletor._int(v.get("NL")), tile.coletor._int(v.get("NLAPP"))
    return {"negacoes_no_buffer": n, "do_cliente": napp,
            "estado": None if n is None else ("PRESENT" if n > 0 else "ABSENT")}


def baseline(amb: Ambiente, desde_wake: float, *, run: Path | None = None, quiescencia_s: float = 120.0,
             completo: bool = False) -> dict[str, Any]:
    """O 05 está convergido? Tudo por LEITURA. `completo` lê também o pacote e o netlink (só antes do controle)."""
    itens: dict[str, dict[str, Any]] = {}

    def item(nome: str, ok: bool, detalhe: Any) -> None:
        itens[nome] = {"ok": bool(ok), "detalhe": detalhe}

    inst = wk = None
    try:
        snap = amb.snapshot()
        inst = next((i for i in snap.get("instances", []) if i.get("id") == IID), None)
        wk = next((w for w in snap.get("workers", []) if w.get("id") == WORKER_LOCAL), None)
    except Exception as exc:  # noqa: BLE001
        item("central_responde", False, f"{type(exc).__name__}: {exc}"[:200])
    con = (inst or {}).get("connectivity") or {}
    item("instancia_online", bool(inst) and inst.get("state") == "online", (inst or {}).get("state"))
    item("serial_confere", bool(inst) and inst.get("serial") == SERIAL, (inst or {}).get("serial"))
    item("qa_sem_conta_real", bool(inst) and str(inst.get("account_label") or "").lower().startswith("qa")
         and not inst.get("locked_account"), {"account_label": (inst or {}).get("account_label"),
                                              "locked_account": (inst or {}).get("locked_account")})
    item("sem_execucao_nem_operador", bool(inst) and inst.get("current") is None and inst.get("control") in (None, "none"),
         {"current": (inst or {}).get("current"), "control": (inst or {}).get("control")})
    item("worker_local_saudavel", bool(wk) and wk.get("connected") is True and wk.get("state") in ("online", "degraded")
         and not wk.get("maintenance"), {k: (wk or {}).get(k) for k in ("connected", "state", "maintenance")})
    item("conectividade_healthy_depois_do_wake", con.get("state") == "healthy" and (coletor.epoch(con.get("checked_at")) or 0) >= desde_wake,
         {"estado": con.get("state"), "checked_at": con.get("checked_at")})
    linha: list[tuple] = []
    try:
        linha = amb.sql("SELECT state, policy, desired_rev, applied_rev, leak_pending, verified_at FROM device_network "
                        "WHERE instance_id=?", (IID,))
        r = linha[0] if linha else None
        item("rede_trafego_verificado", bool(r) and r[0] == "trafego_verificado" and r[1] == POLITICA and r[2] == r[3]
             and not r[4], r)
        item("verificacao_depois_do_wake", bool(r) and (coletor.epoch(r[5]) or 0) >= desde_wake, r[5] if r else None)
        limite = iso(agora() - quiescencia_s)
        abertos = amb.sql(f"SELECT id, verb, state FROM commands WHERE instance_id=? AND state NOT IN "
                          f"({','.join('?' * len(tile.FINAIS_CMD))})", (IID, *tile.FINAIS_CMD))
        item("sem_comando_aberto", not abertos, abertos)
        recentes = amb.sql("SELECT id, verb, state, finished_at FROM commands WHERE instance_id=? AND "
                           "(created_at>=? OR coalesce(finished_at,'')>=?)", (IID, limite, limite))
        item("silencio_de_comandos", not recentes, {"janela_s": quiescencia_s, "recentes": recentes})
        trav = amb.sql("SELECT id FROM device_locked_accounts WHERE instance_id=? AND resolved_at IS NULL", (IID,))
        item("sem_conta_travada", not trav, trav)
    except Exception as exc:  # noqa: BLE001
        item("banco_ro_responde", False, f"{type(exc).__name__}: {exc}"[:200])
    peer = None
    try:
        peer = next((p for p in amb.servidor().get("peers", []) if p.get("instance_id") == IID), None)
        lc = coletor.epoch((peer or {}).get("last_connection"))
        item("par_com_handshake_depois_do_wake", bool(peer) and lc is not None and lc >= desde_wake,
             {"address": (peer or {}).get("address"), "last_connection": (peer or {}).get("last_connection")})
    except Exception as exc:  # noqa: BLE001
        item("servidor_responde", False, f"{type(exc).__name__}: {exc}"[:200])
    dev = amb.adb_devices()
    item("adb_online", bool(re.search(rf"^{re.escape(SERIAL)}\s+device\b", dev, re.M)), dev.strip()[-120:])
    est = tile.ler_estado(amb, pesada=True)
    item("adb_le_como_shell", est.get("adb") is True and est.get("uid") == 2000, {"uid": est.get("uid")})
    item("boot_completo", est.get("boot") is True, {"boot": est.get("boot"), "uptime": est.get("uptime")})
    item("always_on_do_cliente", est.get("always_on") == PACOTE, est.get("always_on"))
    item("lockdown_1", est.get("lockdown") == "1", est.get("lockdown"))
    item("tun0_no_ar", est.get("tun") is True, est.get("tun"))
    item("vpn_connected", est.get("vpn") is True, est.get("vpn"))
    item("regras_de_bloqueio", (est.get("regras") or 0) > 0, est.get("regras"))
    item("cliente_vivo_e_nao_stopped", est.get("pid_sfa") is not None and est.get("stopped") is False,
         {"pid": est.get("pid_sfa"), "stopped": est.get("stopped")})
    if run is not None:
        ok_s, det_s = amb.arquivo_recente(run / "samples.jsonl", 20.0)
        ok_l, det_l = amb.logcat_vivo()
        item("coletor_gravando", ok_s and ok_l and (run / "meta.json").exists(), f"{det_s}; {det_l}")
    base: dict[str, Any] = {"tile_presente_q0": bool(est.get("tile")), "pid_sfa": est.get("pid_sfa"), "pid_ui": est.get("pid_ui"),
                            "stopped": est.get("stopped"), "always_on": est.get("always_on"), "lockdown": est.get("lockdown"),
                            "tun": est.get("tun"), "vpn": est.get("vpn"), "regras": est.get("regras"),
                            "uptime_s": est.get("uptime"), "linha_de_rede": linha[0] if linha else None,
                            "par": {k: (peer or {}).get(k) for k in ("address", "last_connection")},
                            "conectividade": con.get("state")}
    if completo:
        base["pacote"] = ler_pacote(amb)
        base["netlink_antes"] = ler_netlink(amb)
    falhas = [n for n, i in itens.items() if not i["ok"]]
    return {"ok": not falhas, "falhas": falhas, "itens": itens, "base": base}


# ---------------------------------------------------------------------------------------------------- o controle
class Registro(tile.Registro):
    pass


def restaurar_tile(amb: Ambiente, q0: bool, reg: Registro) -> dict[str, Any]:
    """A ÚNICA arrumação: o tile volta ao Q0. Nada de desligar túnel, de segundo force-stop ou de reinício."""
    r: dict[str, Any] = {"q0": q0, "feito": None}
    est = tile.ler_estado(amb, pesada=False)
    if bool(est.get("tile")) != q0:
        cmd = tile.CMD_ADICIONAR if q0 else tile.CMD_REMOVER
        rc, _, err = amb.shell(cmd, 30)
        r["feito"] = cmd.split()[2]
        reg("restaurar_tile", comando=r["feito"], rc=rc, erro=err.strip()[:200])
        amb.dormir(3.0)
    r["tile_depois"] = bool(tile.ler_estado(amb, pesada=False).get("tile"))
    r["ok"] = r["tile_depois"] == q0
    return r


def rodar_controle(amb: Ambiente, run: Path, desde_wake: float, reg: Registro, *, observar_s: float = OBSERVAR_S,
                   quiescencia_s: float = 120.0) -> dict[str, Any]:
    res: dict[str, Any] = {"perfil": "controle-05", "instancia": IID, "inicio": iso(), "estado": "BLOCKED", "mutacoes": 0}
    marcador = run / "controle-05.tentativa"
    if marcador.exists():
        res["estado"] = "RECUSADO_SEGUNDA_TENTATIVA"
        res["marcador"] = marcador.read_text(encoding="utf-8")[:80]
        return res
    gate = baseline(amb, desde_wake, run=run, quiescencia_s=quiescencia_s, completo=True)
    res["gate"] = gate
    reg("gate", ok=gate["ok"], falhas=gate["falhas"])
    if not gate["ok"]:
        res["estado"] = "CONTROL_BASELINE_BLOCKED"
        res["fim"] = iso()
        return res                                                         # nada escrito, nenhuma tentativa gasta
    base = gate["base"]
    q0 = base["tile_presente_q0"]
    res["baseline"] = base
    marcador.write_text(iso(), encoding="utf-8")                           # a tentativa é gasta ANTES de qualquer escrita
    res["estado"] = "EM_EXECUCAO"
    try:
        t_fs = amb.agora()
        rc, _, err = amb.shell(tile.CMD_FORCE_STOP, 30)
        res["mutacoes"] += 1
        amb.dormir(3.0)
        apos = tile.ler_estado(amb, pesada=True)
        res["force_stop"] = {"inicio": iso(t_fs), "rc": rc, "erro": err.strip()[:200], "processo_depois": apos.get("pid_sfa"),
                             "stopped_depois": apos.get("stopped"), "tun_depois": apos.get("tun"), "vpn_depois": apos.get("vpn")}
        reg("force-stop", **res["force_stop"])
        if apos.get("pid_sfa") is not None:
            res["estado"] = "ERRO_FORCE_STOP_NAO_PEGOU"
            raise RuntimeError("o processo do cliente continua depois do force-stop: o gesto não é executado (tentativa gasta)")
        if apos.get("tun") is not False:
            res["estado"] = "ERRO_TUN_NAO_CAIU"
            raise RuntimeError("o tun0 não caiu com o cliente parado (ou não foi lido): o gesto não é executado (tentativa gasta)")
        t0 = amb.agora()
        rc, out, err = amb.shell(tile.comando_do_gesto(), 60)
        res["mutacoes"] += 1
        gesto = tile.ler_gesto(rc, out, err, amb.agora() - t0)
        gesto["inicio"], gesto["fim"] = iso(t0), iso()
        res["gesto"] = gesto
        reg("gesto", **{k: v for k, v in gesto.items() if k not in ("saida_bruta", "erro_bruto")})
        if gesto["CLICOU"] == "1":
            res["observacao"] = tile.observar(amb, observar_s, reg)
        else:
            res["observacao"] = {"pulada": "o gesto não clicou (CLICOU != 1): a única tentativa foi gasta, inconclusivo"}
        res["estado"] = "GESTO_EXECUTADO"
    except Exception as exc:  # noqa: BLE001
        res.setdefault("erro", f"{type(exc).__name__}: {exc}"[:300])
        if res["estado"] == "EM_EXECUCAO":
            res["estado"] = "ERRO"
        reg("erro", erro=res["erro"])
    finally:
        res["tile_restaurado"] = restaurar_tile(amb, q0, reg)
    obs = res.get("observacao") or {}
    res["tun_subiu"] = bool(obs.get("tun_visto"))
    res["leitura_final"] = {k: v for k, v in (tile.ler_estado(amb, pesada=True)).items()
                            if k in ("adb", "always_on", "lockdown", "tun", "vpn", "regras", "pid_sfa", "stopped", "tile")}
    try:
        peer = next((p for p in amb.servidor().get("peers", []) if p.get("instance_id") == IID), None)
        res["par_depois"] = {"address": (peer or {}).get("address"), "last_connection": (peer or {}).get("last_connection")}
    except Exception as exc:  # noqa: BLE001
        res["par_depois"] = {"erro": f"{type(exc).__name__}: {exc}"[:150]}
    res["fim"] = iso()
    return res


# ---------------------------------------------------------------------------------------------------- monitor
def tick_do_monitor(amb: Ambiente, desde: str, vistos: dict[str, Any], *, com_aparelho: bool) -> dict[str, Any]:
    """Um registro SÓ de leitura do que o produto está fazendo com o 05."""
    r: dict[str, Any] = {"ts": iso(), "src": "monitor"}
    try:
        inst = next((i for i in amb.snapshot().get("instances", []) if i.get("id") == IID), {})
        r["instancia"] = {"state": inst.get("state"), "detalhe": inst.get("state_detail"), "readiness": (inst.get("readiness") or {}).get("phase"),
                          "control": inst.get("control"), "current": inst.get("current"), "attention": inst.get("attention"),
                          "conectividade": (inst.get("connectivity") or {}).get("state"),
                          "checked_at": (inst.get("connectivity") or {}).get("checked_at")}
    except Exception as exc:  # noqa: BLE001
        r["instancia"] = {"erro": f"{type(exc).__name__}: {exc}"[:150]}
    try:
        linha = amb.sql("SELECT state, policy, desired_rev, applied_rev, leak_pending, verified_at, substr(coalesce(error,''),1,120) "
                        "FROM device_network WHERE instance_id=?", (IID,))
        r["rede"] = linha[0] if linha else None
        novos = []
        for c in amb.sql("SELECT id, verb, state, created_at, finished_at FROM commands WHERE instance_id=? AND created_at>=?",
                         (IID, desde)):
            chave = f"c:{c[0]}:{c[2]}"
            if chave not in vistos:
                vistos[chave] = True
                novos.append(c)
        r["comandos_novos"] = novos
        eventos = []
        for e in amb.sql("SELECT ts, kind, substr(message,1,170) FROM events WHERE instance_id=? AND ts>? ORDER BY ts",
                         (IID, vistos.get("ultimo_evento", desde))):
            vistos["ultimo_evento"] = e[0]
            eventos.append(e)
        r["eventos_novos"] = eventos
    except Exception as exc:  # noqa: BLE001
        r["banco"] = f"{type(exc).__name__}: {exc}"[:150]
    try:
        peer = next((p for p in amb.servidor().get("peers", []) if p.get("instance_id") == IID), None)
        r["par"] = {k: (peer or {}).get(k) for k in ("address", "last_connection")} if peer else None
    except Exception as exc:  # noqa: BLE001
        r["par"] = {"erro": f"{type(exc).__name__}: {exc}"[:150]}
    if com_aparelho:
        dev = amb.adb_devices()
        online = bool(re.search(rf"^{re.escape(SERIAL)}\s+device\b", dev, re.M))
        r["adb"] = "device" if online else (re.search(rf"^{re.escape(SERIAL)}\s+(\S+)", dev, re.M) or [None, "ausente"])[1]
        if online:
            rc, out, _ = amb.shell(CMD_MONITOR_LEVE, 25)
            v = coletor.parse_pares(out if rc == 0 else "")
            r["aparelho"] = {"boot": v.get("B"), "uptime": v.get("S"), "tun": v.get("T"), "pid_sfa": v.get("PS"), "pid_ui": v.get("PU")}
    return r


def monitorar(amb: Ambiente, run: Path, duracao_s: float, intervalo_s: float, *, aparelho_a_cada: int = 3) -> int:
    run.mkdir(parents=True, exist_ok=True)
    arq = run / "monitor.jsonl"
    parar = run / "parar-monitor"
    desde = iso()
    vistos: dict[str, Any] = {}
    n, fim = 0, amb.agora() + duracao_s
    with arq.open("a", encoding="utf-8", buffering=1) as f:
        while amb.agora() < fim and not parar.exists():
            f.write(json.dumps(tick_do_monitor(amb, desde, vistos, com_aparelho=n % aparelho_a_cada == 0),
                               ensure_ascii=False, default=str) + "\n")
            n += 1
            amb.dormir(intervalo_s)
    print(f"{n} registros em {arq} (parada: {'arquivo parar-monitor' if parar.exists() else 'duração'})", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------------------------------- CLI
def plano() -> dict[str, Any]:
    return {"perfil": "controle-05 (DRY-RUN: nada foi tocado)", "instancia": IID, "serial": SERIAL, "politica_esperada": POLITICA,
            "baseline": "convergido: online, trafego_verificado, tun0, VPN CONNECTED, always-on, lockdown 1, regras, par com handshake "
                        "depois do wake, conectividade healthy, nenhum comando aberto, silêncio de comandos de rede",
            "force_stop": tile.CMD_FORCE_STOP, "gesto": tile.comando_do_gesto(), "observar_s": OBSERVAR_S,
            "arrumacao": [tile.CMD_REMOVER, tile.CMD_ADICIONAR],
            "nunca": "segundo force-stop, clique de desligar, reinício, mudança de perfil/política/par/servidor"}


def main(argv: list[str] | None = None, amb: Ambiente | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    comum = argparse.ArgumentParser(add_help=False)
    comum.add_argument("--instance", help=f"só {IID}")
    sub.add_parser("plano", parents=[comum])
    m = sub.add_parser("monitorar", parents=[comum])
    m.add_argument("--run", required=True)
    m.add_argument("--duracao", type=float, default=3600.0)
    m.add_argument("--intervalo", type=float, default=5.0)
    b = sub.add_parser("baseline", parents=[comum])
    b.add_argument("--desde-wake", required=True, help="ISO UTC do wake")
    b.add_argument("--run")
    b.add_argument("--quiescencia-s", type=float, default=120.0)
    b.add_argument("--completo", action="store_true")
    c = sub.add_parser("controle", parents=[comum])
    c.add_argument("--desde-wake", required=True)
    c.add_argument("--run", required=True)
    c.add_argument("--execute", action="store_true")
    c.add_argument("--observar-s", type=float, default=OBSERVAR_S)
    c.add_argument("--quiescencia-s", type=float, default=120.0)
    a = p.parse_args(argv)
    if a.instance is not None and a.instance != IID:
        print(f"RECUSADO: este perfil é só do {IID} (recebido {a.instance!r}); sem curinga nem lista", file=sys.stderr)
        return 2
    if a.cmd == "plano":
        print(json.dumps(plano(), ensure_ascii=False, indent=2))
        return 0
    if a.instance != IID:
        print(f"RECUSADO: {a.cmd} exige --instance {IID}", file=sys.stderr)
        return 2
    amb = amb or Ambiente()
    if a.cmd == "monitorar":
        return monitorar(amb, Path(a.run), a.duracao, a.intervalo)
    desde = coletor.epoch(a.desde_wake)
    if desde is None:
        print("RECUSADO: --desde-wake precisa ser um horário ISO UTC", file=sys.stderr)
        return 2
    if a.cmd == "baseline":
        r = baseline(amb, desde, run=Path(a.run) if a.run else None, quiescencia_s=a.quiescencia_s, completo=a.completo)
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        return 0 if r["ok"] else 3
    if a.observar_s < tile.OBSERVAR_MIN_S:
        print(f"RECUSADO: --observar-s precisa ser >= {tile.OBSERVAR_MIN_S:g}", file=sys.stderr)
        return 2
    if not a.execute:
        print("RECUSADO: controle exige --execute (sem ele só `plano`)", file=sys.stderr)
        return 2
    run = Path(a.run)
    if not run.exists():
        print("RECUSADO: --run precisa ser a pasta de um coletor em andamento", file=sys.stderr)
        return 2
    res = rodar_controle(amb, run, desde, Registro(run / "acionador-controle05.jsonl"), observar_s=a.observar_s,
                         quiescencia_s=a.quiescencia_s)
    (run / "acionador-controle05.json").write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    return {"CONTROL_BASELINE_BLOCKED": 3, "RECUSADO_SEGUNDA_TENTATIVA": 5}.get(res["estado"], 0)


if __name__ == "__main__":
    sys.exit(main())
