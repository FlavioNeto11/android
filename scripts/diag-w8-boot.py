#!/usr/bin/env python
"""Ensaios de BOOT do W8 no android-09 (docs/handoffs/w8-boot-recovery.md): o que acontece entre o boot do Android e o `tun0`.

Pergunta (boots 1/3/4 do W8): com o always-on configurado, o que o sistema e o próprio cliente (SFA 1.14.2) fazem no boot, em que
ordem, e o que quebra? Cada ensaio é UM boot a frio do android-09 pelo `restart` normal da plataforma, com captura do boot inteiro
(`logcat -b events/main/system/crash -d` logo depois: os buffers do convidado guardam o boot desde o início) e amostras do `tun0`.

Ensaios (variáveis separadas; nenhum combina mais de uma):
  os            always-on, SFA parado pela UI antes (`startedByUser=false`): só a 1ª chance (o `startAlwaysOnVpn` do sistema).
  os+receiver   always-on, VPN ligada pela UI antes do restart (`startedByUser` fica `true`): as DUAS chances (sistema + BootReceiver).
  os-starved    como `os`, mais carga de CPU DENTRO do convidado desde o primeiro adb do boot (emula o convidado sem CPU do K-066).
  os+stopped    always-on, VPN ligada pela UI e depois `am force-stop` do cliente antes do restart: a precondição do boot 3 do W8
                (pacote em `stopped`: o BootReceiver não é entregue; só a 1ª chance, a do sistema, pode subir o serviço).

SEGURO POR PADRÃO (sem --execute só mostra o plano). Só o android-09. Um marcador por ensaio impede repetir. Escritas possíveis no
aparelho (o teste `test_vocabulario_de_escrita` fecha a lista): `settings put secure always_on_vpn_app`/`always_on_vpn_lockdown`,
`settings delete secure always_on_vpn_app`, o toque único no Start/Stop do cliente pela árvore (como o diagnóstico §18.4), HOME,
laços de CPU em `/data/local/tmp` (só `os-starved`; morrem no fim e no reboot) e o `restart` da plataforma. Sem perfil, par, peer,
política, WireGuard, dados do app nem outro aparelho. Lockdown fica 0 (com 1 e sem túnel o aparelho perde a internet).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _carregar(nome: str, arquivo: str):
    spec = importlib.util.spec_from_file_location(nome, ROOT / "scripts" / arquivo)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)                                                  # type: ignore[union-attr]
    return mod


ui = _carregar("diag_w8_uistart_boot", "diag-w8-uistart.py")
tile = ui.tile
coletor = tile.coletor
PACOTE, IID = tile.PACOTE, tile.IID


def raiz_do_central(inicio: Path = ROOT) -> Path:
    """O checkout com o banco do central (`data/poc.sqlite3`): rodado de um worktree, o `data/` está num ancestral."""
    for d in (inicio, *inicio.parents):
        if (d / "data" / "poc.sqlite3").exists():
            return d
    return inicio


tile.ROOT = raiz_do_central()                                                     # o `sql` do tile lê `ROOT/data/poc.sqlite3`
API = coletor.API
ENSAIOS = {
    "os": {"receiver": False, "starved": False},
    "os+receiver": {"receiver": True, "starved": False},
    "os-starved": {"receiver": False, "starved": True},
    "os+stopped": {"receiver": True, "starved": False, "force_stop": True},
}
CMD_ALWAYS_ON = f"settings put secure always_on_vpn_app {PACOTE}"
CMD_LOCKDOWN_0 = "settings put secure always_on_vpn_lockdown 0"
CMD_ALWAYS_ON_OFF = "settings delete secure always_on_vpn_app"
CMD_HOME = "input keyevent KEYCODE_HOME"
CMD_FORCE_STOP = f"am force-stop {PACOTE}"
PIDS_CARGA = "/data/local/tmp/w8load.pids"
CMD_CARGA = ("rm -f " + PIDS_CARGA + "; for i in 1 2 3 4 5 6 7 8; do nohup sh -c 'while :; do :; done' >/dev/null 2>&1 & "
             "echo $! >> " + PIDS_CARGA + "; done; echo CARGA=$(wc -l < " + PIDS_CARGA + ")")
CMD_SEM_CARGA = f"[ -f {PIDS_CARGA} ] && kill $(cat {PIDS_CARGA}) 2>/dev/null; rm -f {PIDS_CARGA}; echo SEM_CARGA=1"
CMD_AMOSTRA = ("echo U=$(cut -d' ' -f1 /proc/uptime); echo B=$(getprop sys.boot_completed); "
               "echo T=$(ip -o addr show tun0 2>/dev/null | grep -c inet); echo P=$(pidof " + PACOTE + "); "
               "echo L=$(cut -d' ' -f1 /proc/loadavg)")
CMD_SERVICOS = ("echo S=$(dumpsys activity services " + PACOTE + " 2>/dev/null | grep -o '" + PACOTE +
                "/[.]bg[.][A-Za-z]*' | sort -u | tr '\\n' ',')")
OBSERVAR_S = 300.0                                                                # uptime do convidado até onde se observa
LIMITE_DO_BOOT_S = 420.0                                                          # sem adb voltando até aqui: ensaio incerto


def agora() -> float:
    return time.time()


def iso(t: float | None = None) -> str:
    return coletor.iso(agora() if t is None else t)


class Ambiente(ui.Ambiente):
    def pedir_restart(self) -> dict:
        req = urllib.request.Request(f"{API}/instances/{IID}/actions/restart", data=b'{"idempotency_key": "w8-boot-%d"}' % int(agora()),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:                         # noqa: S310 - loopback do central
            return json.loads(r.read().decode("utf-8"))

    def comando(self, cid: str) -> dict:
        return coletor.get_json(f"{API}/commands/{cid}", 20)


def _pares(texto: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^([A-Z]+)=(.*)$", texto, re.M)}


def lido(amb: Ambiente, cmd: str, timeout: float = 20) -> dict[str, str] | None:
    rc, out, _ = amb.shell(cmd, timeout)
    return _pares(out) if rc == 0 else None


def gate(amb: Ambiente) -> dict[str, Any]:
    """As precondições do tile, SEM o coletor contínuo (este script captura o boot por conta própria) e com a conectividade healthy."""
    g = ui.precondicoes(amb, None)
    falhas = [f for f in g["falhas"] if f != "coletor_gravando"]
    return {"ok": not falhas, "falhas": falhas, "base": g["base"]}


def tocar_botao(amb: Ambiente, rotulo: str, reg: Any) -> dict[str, Any]:
    """UM toque no botão (`Start`/`Stop`) do cliente, achado pela árvore (o localizador do diagnóstico §18.4); falha fechada."""
    amb.shell(ui.CMD_ABRIR, 30)
    alvo = None
    for _ in range(15):
        amb.dormir(1.0)
        alvo = ui.achar_botao(amb.hierarquia(), rotulo)
        if alvo is not None:
            break
    if alvo is None:
        reg("botao_nao_achado", rotulo=rotulo)
        return {"rotulo": rotulo, "tocado": False}
    rc, _, err = amb.shell(ui.tocar(alvo["x"], alvo["y"]), 20)
    reg("toque", alvo=rotulo, x=alvo["x"], y=alvo["y"], rc=rc)
    return {"rotulo": rotulo, "tocado": rc == 0, "x": alvo["x"], "y": alvo["y"]}


def esperar_tun(amb: Ambiente, presente: bool, prazo_s: float) -> bool:
    fim = amb.agora() + prazo_s
    while amb.agora() < fim:
        v = lido(amb, "echo T=$(ip -o addr show tun0 2>/dev/null | grep -c inet)")
        if v is not None and (v.get("T", "0") not in ("", "0")) == presente:
            return True
        amb.dormir(2.0)
    return False


def preparar(amb: Ambiente, kind: str, reg: Any) -> dict[str, Any]:
    """Escreve o que o ensaio varia ANTES do restart: o always-on (lockdown 0) e, em `os+receiver`, a VPN ligada pela UI."""
    p: dict[str, Any] = {}
    for cmd in (CMD_ALWAYS_ON, CMD_LOCKDOWN_0):
        rc, _, err = amb.shell(cmd, 20)
        reg("escrita", cmd=cmd, rc=rc, erro=err.strip()[:120])
    est = tile.ler_estado(amb, pesada=True)
    p["always_on"], p["lockdown"] = est.get("always_on"), est.get("lockdown")
    if ENSAIOS[kind]["receiver"]:
        p["start"] = tocar_botao(amb, "Start", reg)
        p["tun_subiu_antes"] = esperar_tun(amb, True, 40)
        amb.shell(CMD_HOME, 20)
    if ENSAIOS[kind].get("force_stop"):
        # A precondição do boot 3 do W8: o pacote em `stopped` (o `force-stop` do teste de vazamento) com a VPN tendo estado no ar.
        rc, _, err = amb.shell(CMD_FORCE_STOP, 20)
        reg("escrita", cmd=CMD_FORCE_STOP, rc=rc, erro=err.strip()[:120])
        amb.dormir(3.0)
        est = tile.ler_estado(amb, pesada=True)
        p["apos_force_stop"] = {"stopped": est.get("stopped"), "tun": est.get("tun")}
    return p


def observar_boot(amb: Ambiente, run: Path, kind: str, uptime_antes: float, reg: Any) -> dict[str, Any]:
    """Depois do `restart`: espera o adb voltar COM uptime menor (boot novo), injeta a carga (os-starved), amostra o `tun0` até
    OBSERVAR_S de uptime e devolve os instantes (host) do primeiro adb, do `boot_completed` e do `tun0`."""
    r: dict[str, Any] = {"t_restart": iso(amb.agora())}
    t0 = amb.agora()
    amostras = run / f"boot-{kind}.amostras.jsonl"
    boot_novo = False
    carga_em = None
    ultimo_servico = 0.0
    with amostras.open("a", encoding="utf-8") as fh:
        while amb.agora() - t0 < LIMITE_DO_BOOT_S + OBSERVAR_S:
            v = lido(amb, CMD_AMOSTRA, 15)
            agora_h = amb.agora()
            if v is None or not v.get("U"):
                amb.dormir(1.0)
                continue
            u = float(v["U"])
            if not boot_novo:
                if u >= uptime_antes:
                    amb.dormir(1.0)
                    continue
                boot_novo = True
                r["t_adb"], r["uptime_no_primeiro_adb"] = iso(agora_h), u
                if ENSAIOS[kind]["starved"]:
                    rc, out, _ = amb.shell(CMD_CARGA, 30)
                    carga_em = agora_h
                    r["carga"] = {"t": iso(agora_h), "rc": rc, "saida": out.strip()[:80]}
                    reg("carga", **r["carga"])
            linha = {"t": iso(agora_h), "u": u, "boot": v.get("B"), "tun": v.get("T"), "pid": v.get("P"), "load": v.get("L")}
            if agora_h - ultimo_servico > 8.0:
                s = lido(amb, CMD_SERVICOS, 25)
                linha["servicos"] = (s or {}).get("S")
                ultimo_servico = agora_h
            fh.write(json.dumps(linha) + "\n")
            fh.flush()
            if v.get("B") == "1" and "t_boot_completed" not in r:
                r["t_boot_completed"], r["uptime_no_boot_completed"] = iso(agora_h), u
            if v.get("T") not in (None, "", "0") and "t_tun" not in r:
                r["t_tun"], r["uptime_no_tun"] = iso(agora_h), u
            if carga_em is not None and u >= OBSERVAR_S - 40 and "carga_parada" not in r:
                amb.shell(CMD_SEM_CARGA, 20)
                r["carga_parada"] = iso(agora_h)
            if u >= OBSERVAR_S:
                break
            amb.dormir(2.0)
    r["boot_visto"] = boot_novo
    r["fim_observacao"] = iso(amb.agora())
    return r


def capturar(amb: Ambiente, run: Path, kind: str) -> dict[str, int]:
    """Os buffers do boot, lidos logo depois (o `-T` do coletor perde o começo): events, main+system+crash e kernel, mais o
    estado do framework (VPN, serviços, exit-info). Só texto de log; nada de segredo (o cliente não loga o perfil)."""
    tam: dict[str, int] = {}
    pedidos = {
        "events": "logcat -b events -d -v threadtime", "main-system": "logcat -b main,system,crash -d -v threadtime",
        "kernel": "logcat -b kernel -d -v threadtime", "buffers": "logcat -g",
        "vpn": "dumpsys connectivity 2>/dev/null | grep -i -B1 -A3 'vpn' | head -120",
        "servicos": f"dumpsys activity services {PACOTE}", "exit-info": f"dumpsys activity exit-info {PACOTE}",
        "relogio": "date '+%m-%d %H:%M:%S.%N'; cut -d' ' -f1 /proc/uptime",
    }
    for nome, cmd in pedidos.items():
        rc, out, err = amb.shell(cmd, 90)
        (run / f"boot-{kind}.{nome}.txt").write_text(out + ("\n[stderr] " + err if err.strip() else ""), encoding="utf-8")
        tam[nome] = len(out)
    return tam


def desfazer(amb: Ambiente, reg: Any) -> dict[str, Any]:
    """A linha de base de novo: carga parada, VPN desligada pela UI (se subiu), always-on e lockdown como estavam, HOME."""
    amb.shell(CMD_SEM_CARGA, 20)
    d: dict[str, Any] = {}
    est = tile.ler_estado(amb, pesada=True)
    if est.get("tun") or est.get("vpn"):
        d["stop"] = tocar_botao(amb, "Stop", reg)
        d["tun_desceu"] = esperar_tun(amb, False, 30)
    for cmd in (CMD_ALWAYS_ON_OFF, CMD_LOCKDOWN_0, CMD_HOME):
        rc, _, err = amb.shell(cmd, 20)
        reg("escrita", cmd=cmd, rc=rc, erro=err.strip()[:120])
    est = tile.ler_estado(amb, pesada=True)
    base = {k: est.get(k) for k in ("adb", "uid", "always_on", "lockdown", "tun", "vpn", "tile", "pid_sfa", "stopped")}
    d["estado"] = base
    d["ok"] = (est.get("adb") is True and est.get("always_on") == "null" and est.get("lockdown") == "0"
               and est.get("tun") is False and est.get("vpn") is False)
    return d


def rodar(amb: Ambiente, run: Path, kind: str, reg: Any) -> dict[str, Any]:
    r: dict[str, Any] = {"iid": IID, "ensaio": kind, "inicio": iso(amb.agora())}
    marcador = run / f"boot-{kind}.tentativa"
    if marcador.exists():
        r["parada"] = "SEGUNDA_TENTATIVA_RECUSADA"
        return r
    g = gate(amb)
    r["gate"] = g
    reg("gate", **{k: g[k] for k in ("ok", "falhas")})
    if not g["ok"]:
        r["parada"] = "BOOT_BLOCKED"
        return r
    marcador.write_text(iso(), encoding="utf-8")                                  # ANTES de escrever: não há segunda chance
    try:
        r["relogio_antes"] = {"host": iso(amb.agora()), "aparelho": (lido(amb, "echo U=$(date +%s)") or {}).get("U")}
        up = lido(amb, "echo U=$(cut -d' ' -f1 /proc/uptime)")
        uptime_antes = float((up or {}).get("U") or 1e9)
        r["preparo"] = preparar(amb, kind, reg)
        cmd = amb.pedir_restart()
        r["restart"] = {"command_id": cmd.get("command_id"), "estado": cmd.get("state")}
        reg("restart", **r["restart"])
        r["boot"] = observar_boot(amb, run, kind, uptime_antes, reg)
        if r["boot"]["boot_visto"]:
            r["capturado"] = capturar(amb, run, kind)
        else:
            r["parada"] = "BOOT_NAO_VISTO_ESTADO_INCERTO"
    except Exception as exc:  # noqa: BLE001 - o desfazer roda de qualquer jeito
        r["erro"] = f"{type(exc).__name__}: {exc}"[:300]
        reg("erro", erro=r["erro"])
    finally:
        r["desfazer"] = desfazer(amb, reg)
    r["fim"] = iso(amb.agora())
    return r


def plano(kind: str) -> dict[str, Any]:
    return {"instancia": IID, "ensaio": kind, "modo": "plano (nenhuma chamada)", "variaveis": ENSAIOS[kind],
            "passos": ["gate (precondições do tile + conectividade healthy)", f"escrever: {CMD_ALWAYS_ON}; {CMD_LOCKDOWN_0}",
                       "os+receiver/os+stopped: UM toque no Start (VPN no ar antes do restart)", "os+stopped: am force-stop do cliente", "restart pela plataforma (boot a frio)",
                       "os-starved: laços de CPU no convidado desde o primeiro adb", "amostras do tun0 até 300 s de uptime",
                       "capturar buffers events/main/system/crash/kernel", "desfazer: carga, Stop se subiu, always-on/lockdown, HOME"],
            "escritas_possiveis": [CMD_ALWAYS_ON, CMD_LOCKDOWN_0, CMD_ALWAYS_ON_OFF, CMD_HOME, CMD_FORCE_STOP, CMD_CARGA, CMD_SEM_CARGA,
                                   "input tap <Start|Stop>", "POST /api/instances/android-09/actions/restart"]}


def main(argv: list[str] | None = None, amb: Ambiente | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ensaio", choices=sorted(ENSAIOS))
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--instance", default=None)
    ap.add_argument("--run", default=None, help="pasta de saída (obrigatória com --execute)")
    a = ap.parse_args(argv)
    if not a.execute:
        print(json.dumps(plano(a.ensaio), ensure_ascii=False, indent=2))
        return 0
    if a.instance != IID:
        print(f"recusado: só o {IID} (recebido {a.instance!r})", file=sys.stderr)
        return 2
    if not a.run or not Path(a.run).is_dir():
        print("recusado: --run <pasta de saída> é obrigatório e precisa existir", file=sys.stderr)
        return 2
    run = Path(a.run)
    amb = amb or Ambiente()
    reg = tile.Registro(run / f"acionador-boot-{a.ensaio}.jsonl")
    r = rodar(amb, run, a.ensaio, reg)
    (run / f"boot-{a.ensaio}.saida.json").write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if r.get("parada") == "SEGUNDA_TENTATIVA_RECUSADA":
        return 5
    if r.get("parada") == "BOOT_BLOCKED":
        return 3
    return 0 if r.get("desfazer", {}).get("ok") else 4


if __name__ == "__main__":
    raise SystemExit(main())
