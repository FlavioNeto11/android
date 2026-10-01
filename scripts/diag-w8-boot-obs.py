#!/usr/bin/env python
"""Parsers PUROS da observação do boot do W8 (docs/handoffs/w8-boot-recovery.md): texto de log/dumpsys -> fatos.

Não fala com aparelho nenhum, nem com o central: lê o texto que o `diag-w8-boot.py` capturou (ou que alguém colou) e devolve
dicts. Serve a duas coisas: (1) o resumo automático de cada boot, para comparar `os` x `os+stopped` sem ler 3 MB de logcat;
(2) rodar sobre capturas antigas (`data/diag-w8-boot/run*/`), que é como os testes e o doc provam o que afirmam.

O que cada fonte responde (A–E do doc §10): A estado `stopped` do pacote (`dumpsys package`), B ordem e hora das redes
(`registerNetworkAgent`/`Switching to new default network` do `main`), C ciclo exato do serviço (`am_proc_start`,
`am_foreground_service_start/stop` com o motivo, `notification_canceled`), D processo morto pelo framework (`am_kill`,
`am_anr`, `am_crash`, `am_proc_died` + `dumpsys activity exit-info`, que guarda a razão da morte), E parada voluntária do
app (FGS stop com `STOP_FOREGROUND` e o PID inalterado). Sem segredo: só nomes de serviço, ids de rede, horários e razões.
"""
from __future__ import annotations

import calendar
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PACOTE = "io.nekohasekai.sfa"
JANELA_SILENCIOSA_S = 30.0        # o FGS some até aqui depois de subir: parada "silenciosa" (E2 parou em ~1,8 s)

RE_LOG = re.compile(r"^(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)\.(\d{3})\s+(\d+)\s+(\d+)\s+([VDIWEF])\s+(.+?)\s*:\s?(.*)$")
RE_ARGS = re.compile(r"^\[(.*)\]\s*$")


# ───────────────────────────── tempo ─────────────────────────────
def _linha(texto: str):
    m = RE_LOG.match(texto)
    if not m:
        return None
    mes, dia, h, mi, s, ms, pid, tid, nivel, tag, resto = m.groups()
    return {"mes": int(mes), "dia": int(dia), "h": int(h), "mi": int(mi), "s": int(s), "ms": int(ms), "pid": int(pid),
            "tid": int(tid), "nivel": nivel, "tag": tag.strip(), "resto": resto}


def rotulo_hora(l: dict) -> str:
    return f"{l['mes']:02d}-{l['dia']:02d} {l['h']:02d}:{l['mi']:02d}:{l['s']:02d}.{l['ms']:03d}"


def segundos(rotulo: str) -> float:
    """`MM-DD HH:MM:SS.mmm` -> segundos desde 01-01 (sem ano; o boot cabe em dias vizinhos, e a virada de ano não importa aqui)."""
    m = re.match(r"(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)\.(\d{3})$", rotulo)
    if not m:
        raise ValueError(rotulo)
    mes, dia, h, mi, s, ms = (int(x) for x in m.groups())
    dias = sum(calendar.monthrange(2001, k)[1] for k in range(1, mes)) + dia - 1                      # 2001: ano não bissexto, só relativo
    return dias * 86400 + h * 3600 + mi * 60 + s + ms / 1000.0


def atraso(a: str | None, b: str | None) -> float | None:
    """Segundos de `a` até `b` (rótulos do logcat); None se faltar um."""
    return None if not a or not b else round(segundos(b) - segundos(a), 3)


# ───────────────────────────── events: o ciclo do serviço ─────────────────────────────
def _args(resto: str) -> list[str]:
    m = RE_ARGS.match(resto.strip())
    return m.group(1).split(",") if m else []


ADJ_CACHEADO = 900                # oom_adj de processo em cache/vazio: a morte dele (`empty #N`) é limpeza do sistema, não falha do serviço


def _int(x: str) -> int | None:
    try:
        return int(x)
    except ValueError:
        return None


def _benigno(adj: int | None, razao: str) -> bool:
    return adj is not None and adj >= ADJ_CACHEADO and razao.strip().startswith(("empty", "cached"))


def parse_eventos(texto: str, pacote: str = PACOTE) -> dict[str, Any]:
    """O ciclo de vida do app no buffer `events`: início de processo, FGS (com o motivo do fim), mortes, ANR, crash, congelamento."""
    r: dict[str, Any] = {"proc_start": [], "fgs_start": [], "fgs_stop": [], "kill": [], "proc_died": [], "anr": [], "crash": [],
                         "notificacao_cancelada": [], "freeze": [], "pids": []}
    for bruto in texto.splitlines():
        l = _linha(bruto)
        if not l or pacote not in l["resto"]:
            continue
        a, t, tag = _args(l["resto"]), rotulo_hora(l), l["tag"]
        if tag == "am_proc_start" and len(a) >= 6:
            r["proc_start"].append({"t": t, "pid": int(a[1]), "tipo": a[4], "componente": a[5].strip("{}")})
            r["pids"].append(int(a[1]))
        elif tag == "am_foreground_service_start" and len(a) >= 2:
            r["fgs_start"].append({"t": t, "classe": a[1], "motivo": a[10] if len(a) > 10 else None})
        elif tag == "am_foreground_service_stop" and len(a) >= 2:
            r["fgs_stop"].append({"t": t, "classe": a[1], "motivo": a[10] if len(a) > 10 else None})
        elif tag == "am_kill" and len(a) >= 5:
            adj, razao = _int(a[3]), ",".join(a[4:])
            r["kill"].append({"t": t, "pid": int(a[1]), "adj": adj, "razao": razao, "benigno": _benigno(adj, razao)})
        elif tag == "am_proc_died" and len(a) >= 3:
            adj = _int(a[3]) if len(a) > 3 else None
            r["proc_died"].append({"t": t, "pid": int(a[1]), "adj": adj, "benigno": adj is not None and adj >= ADJ_CACHEADO})
        elif tag == "am_anr" and len(a) >= 5:
            r["anr"].append({"t": t, "pid": int(a[1]), "razao": ",".join(a[4:])})
        elif tag == "am_crash" and len(a) >= 5:
            r["crash"].append({"t": t, "pid": int(a[0]), "excecao": a[4]})
        elif tag == "notification_canceled":
            corpo = a[0].split("|") if a else []
            if len(corpo) > 1 and corpo[1] == pacote and len(a) > 1:
                r["notificacao_cancelada"].append({"t": t, "razao": a[1]})
        elif tag == "am_freeze" and a:
            r["freeze"].append({"t": t, "pid": int(a[0])})
    return r


# ───────────────────────────── main: a ordem das redes e o always-on ─────────────────────────────
def _transporte(resto: str) -> str | None:
    m = re.search(r"Transports: ([A-Z_|]+)", resto)
    if m:
        return m.group(1)
    m = re.search(r"ni\{(\w+)", resto)
    return {"MOBILE": "CELLULAR"}.get(m.group(1), m.group(1)) if m else None


def parse_rede(texto: str) -> dict[str, Any]:
    """Quando cada rede apareceu e virou a padrão, e quando o sistema pediu o always-on (`Vpn.startAlwaysOnVpn`), no relógio do convidado."""
    agentes, padrao, always_on = [], [], []
    for bruto in texto.splitlines():
        if "ConnectivityService" not in bruto and "startAlwaysOnVpn" not in bruto:
            continue
        l = _linha(bruto)
        if not l:
            continue
        t, resto = rotulo_hora(l), l["resto"]
        if l["tag"] == "ConnectivityService" and "registerNetworkAgent" in resto:
            m = re.search(r"network\{(\d+)\}", resto)
            agentes.append({"t": t, "rede": int(m.group(1)) if m else None, "transporte": _transporte(resto)})
        elif l["tag"] == "ConnectivityService" and "Switching to new default network" in resto:
            m = re.search(r"using NetworkAgentInfo\{network\{(\d+)\}", resto)
            i = re.search(r"InterfaceName: (\w+)", resto)
            padrao.append({"t": t, "rede": int(m.group(1)) if m else None, "transporte": _transporte(resto.split("using ", 1)[-1]),
                           "iface": i.group(1) if i else None})
        elif l["tag"] == "ContextImpl" and "Vpn.startAlwaysOnVpn" in resto:
            always_on.append(t)
    reais = [a for a in agentes if a["transporte"] and a["transporte"] != "VPN"]
    ordem: list[str] = []
    for a in reais:
        if a["transporte"] not in ordem:
            ordem.append(a["transporte"])
    return {"agentes": agentes, "padrao": padrao, "always_on_start": always_on, "ordem": ordem,
            "primeira_rede": reais[0] if reais else None, "primeira_padrao": padrao[0] if padrao else None,
            "vpn_agentes": [a for a in agentes if a["transporte"] == "VPN"]}


# ───────────────────────────── dumpsys: pacote, exit-info, usagestats, dropbox ─────────────────────────────
def parse_pacote(texto: str) -> dict[str, Any]:
    """`dumpsys package <pkg>` (ou só a linha `User 0:`): `stopped`, `notLaunched`, `enabled` e os dados fixos do APK."""
    r: dict[str, Any] = {}
    m = re.search(r"User 0:.*?installed=(\w+) hidden=(\w+) suspended=(\w+).*?stopped=(\w+) notLaunched=(\w+) enabled=(\d+)", texto)
    if m:
        inst, hid, susp, stp, nl, en = m.groups()
        r.update({"installed": inst == "true", "hidden": hid == "true", "suspended": susp == "true", "stopped": stp == "true",
                  "not_launched": nl == "true", "enabled": int(en)})
    for chave, rx in (("version_code", r"versionCode=(\d+)"), ("last_update", r"lastUpdateTime=([\d\- :]+)"),
                      ("first_install", r"firstInstallTime=([\d\- :]+)")):
        mm = re.search(rx, texto)
        if mm:
            r[chave] = mm.group(1).strip() if chave != "version_code" else int(mm.group(1))
    mm = re.search(r"pkgFlags=\[ ([^\]]*)\]", texto)
    if mm:
        r["pkg_flags"] = mm.group(1).split()
    return r


def parse_exit_info(texto: str, pacote: str = PACOTE) -> dict[str, Any]:
    """`dumpsys activity exit-info <pkg>`: as mortes de processo com a razão (4 crash, 6 ANR, 10 usuário/force-stop, 13 outros...).
    Mortes DENTRO do boot capturado aparecem; as de antes do reboot só se o sistema já as persistiu (a persistência é preguiçosa)."""
    r: dict[str, Any] = {"persistido_em": None, "entradas": []}
    m = re.search(r"Last Timestamp of Persistence Into Persistent Storage: ([\d\- :.]+)", texto)
    if m:
        r["persistido_em"] = m.group(1).strip()
    corpo = texto.split(f"package: {pacote}", 1)
    if len(corpo) < 2:
        return r
    for bloco in re.split(r"ApplicationExitInfo #\d+:", corpo[1].split("package: ", 1)[0])[1:]:
        t = re.search(r"timestamp=([\d\- :.]+) pid=(\d+)", bloco)
        z = re.search(r"reason=(\d+) \((.*?)\) subreason=(\d+) \((.*?)\) status=", bloco)           # `APP CRASH(EXCEPTION)` tem parêntese dentro
        d = re.search(r"description=(.*?) state=", bloco)
        if t and z:
            r["entradas"].append({"t": t.group(1).strip(), "pid": int(t.group(2)), "razao": int(z.group(1)), "razao_nome": z.group(2),
                                  "subrazao": int(z.group(3)), "subrazao_nome": z.group(4), "descricao": d.group(1) if d else None})
    return r


def parse_usagestats(texto: str, pacote: str = PACOTE) -> list[dict[str, Any]]:
    """Eventos do usagestats do pacote: FGS start/stop por classe e troca de bucket. O horário é o do convidado (pode atrasar ~9 s
    em relação ao `events`) e o log cobre 24 h: sobrevive ao reboot do AVD, ao contrário do logcat."""
    r = []
    for l in texto.splitlines():
        m = re.search(rf'time="([\d\- :]+)" type=(FOREGROUND_SERVICE_START|FOREGROUND_SERVICE_STOP|STANDBY_BUCKET_CHANGED|'
                      rf'SERVICE_START|SERVICE_STOP) package={re.escape(pacote)}(?: class=(\S+))?', l)
        if m:
            r.append({"t": m.group(1), "tipo": m.group(2), "classe": m.group(3)})
    return r


def parse_dropbox(texto: str) -> list[dict[str, Any]]:
    """Entradas do `dumpsys dropbox --print`: horário, tag e processo. ANR/crash do app e SYSTEM_BOOT (marca cada boot)."""
    r = []
    for bloco in re.split(r"^={20,}\s*$", texto, flags=re.M):
        m = re.match(r"\s*(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (\S+) \(", bloco)
        if not m:
            continue
        p = re.search(r"^Process: (\S+)", bloco, re.M)
        r.append({"t": m.group(1), "tag": m.group(2), "processo": p.group(1) if p else None})
    return r


def dropbox_do_app(entradas: list[dict[str, Any]], pacote: str = PACOTE) -> list[dict[str, Any]]:
    tags = ("data_app_anr", "data_app_crash", "system_app_anr", "system_app_crash", "SYSTEM_BOOT", "SYSTEM_TOMBSTONE")
    return [e for e in entradas if e["tag"] in tags and (e["tag"] in ("SYSTEM_BOOT",) or e["processo"] == pacote)]


def _duracao_s(txt: str) -> float:
    """`+2m11s863ms` / `+5s891ms` / `+964ms` -> segundos."""
    t = 0.0
    for n, u in re.findall(r"(\d+)(ms|m|s)", txt):
        t += int(n) * {"m": 60.0, "s": 1.0, "ms": 0.001}[u]
    return round(t, 3)


def parse_broadcasts(texto: str, pacote: str = PACOTE) -> list[dict[str, Any]]:
    """`dumpsys activity broadcasts history` (já filtrado no aparelho: cabeçalho do registro, `enqueueClockTime`, estado de cada receptor,
    `name=`/`packageName=`, `reason:`; sem os extras). Devolve, para os broadcasts de BOOT/LOCKED_BOOT/MY_PACKAGE_REPLACED, o que
    aconteceu com os receptores do pacote: `DELIVERED` (rodou no processo) ou `SKIPPED` (com a razão). É a única fonte legível de que a
    SEGUNDA chance (o `BootReceiver` -> `BoxService.start()`) foi entregue; ausência de `am_proc_start` não prova nada com o processo vivo."""
    r: list[dict[str, Any]] = []
    acao = enq = None
    estado: dict[str, Any] | None = None
    for l in texto.splitlines():
        m = re.match(r"\s+BroadcastRecord\{\w+ (\S+)/u(-?\d+)\}", l)
        if m:
            acao, enq, estado = m.group(1), None, None
            continue
        m = re.search(r"enqueueClockTime=(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})", l)
        if m and enq is None:
            enq = m.group(1)
            continue
        m = re.match(r"\s+(DELIVERED|SKIPPED|\w+) (?:scheduled \+(\S+) )?(?:terminal \+(\S+) )?\((-?\d+)\) #(\d+): ", l)
        if m:
            estado = {"estado": m.group(1), "agendado_s": _duracao_s(m.group(2)) if m.group(2) else None,
                      "terminal_s": _duracao_s(m.group(3)) if m.group(3) else None, "razao": None}
            continue
        m = re.match(r"\s+name=(\S+)", l)
        if m and estado is not None:
            estado["receptor"] = m.group(1)
            continue
        m = re.match(r"\s+packageName=(\S+)", l)
        if m and estado is not None:
            estado["pacote"] = m.group(1)
            continue
        m = re.match(r"\s+reason: (.*)", l)
        if m and estado is not None:
            estado["razao"] = m.group(1)[:200]
            if estado.get("pacote") == pacote and acao and re.search(r"BOOT_COMPLETED|MY_PACKAGE_REPLACED", acao) and enq:
                r.append(_registro_broadcast(acao, enq, estado))
            estado = None
    return r


def _registro_broadcast(acao: str, enq: str, e: dict[str, Any]) -> dict[str, Any]:
    d = {"acao": acao.rsplit(".", 1)[-1], "enfileirado": enq, "receptor": e.get("receptor"), "estado": e["estado"],
         "agendado_s": e["agendado_s"], "terminal_s": e["terminal_s"], "razao": e["razao"]}
    if e["agendado_s"] is not None:
        ini = segundos(enq[5:])                                                                       # `2026-10-01 19:29:13.355` -> rótulo do logcat
        d["entregue_em"] = _rotulo_com_segundos(ini + e["agendado_s"])
        if e["terminal_s"] is not None:
            d["terminou_em"] = _rotulo_com_segundos(ini + e["agendado_s"] + e["terminal_s"])
    return d


def _rotulo_com_segundos(seg: float) -> str:
    """Inverso de `segundos` para o rótulo `MM-DD HH:MM:SS.mmm` (o mesmo ano relativo de `segundos`)."""
    dias, resto = divmod(seg, 86400)
    mes, d = 1, int(dias)
    while d >= calendar.monthrange(2001, mes)[1]:
        d -= calendar.monthrange(2001, mes)[1]
        mes += 1
    h, resto = divmod(resto, 3600)
    mi, s = divmod(resto, 60)
    ms = int(round((s % 1) * 1000))
    s = int(s)
    if ms == 1000:
        ms, s = 0, s + 1
    return f"{mes:02d}-{d + 1:02d} {int(h):02d}:{int(mi):02d}:{s:02d}.{ms:03d}"


# ───────────────────────────── amostras do host (o jsonl do coletor) ─────────────────────────────
def parse_amostras(linhas: list[dict[str, Any]]) -> dict[str, Any]:
    """PID do cliente (mudanças), `tun0` e as interfaces de rede vistas do host, a cada ~2 s."""
    pids: list[dict[str, Any]] = []
    r: dict[str, Any] = {"n": len(linhas), "t_tun": None, "t_wlan0": None, "t_eth0": None, "t_boot_completed": None}
    ultimo = object()
    for a in linhas:
        pid = (a.get("pid") or "").split()[0] if a.get("pid") else ""
        if pid != ultimo:
            pids.append({"t": a.get("t"), "u": a.get("u"), "pid": pid or None})
            ultimo = pid
        for chave, campo in (("t_tun", "tun"), ("t_wlan0", "wlan0"), ("t_eth0", "eth0")):
            if r[chave] is None and a.get(campo) not in (None, "", "0"):
                r[chave] = {"t": a.get("t"), "u": a.get("u")}
        if r["t_boot_completed"] is None and a.get("boot") == "1":
            r["t_boot_completed"] = {"t": a.get("t"), "u": a.get("u")}
    r["pids"] = pids
    vivos = [p["pid"] for p in pids if p["pid"]]
    r["pids_distintos"] = list(dict.fromkeys(vivos))
    r["mudancas_de_pid"] = max(0, len(r["pids_distintos"]) - 1)
    return r


# ───────────────────────────── relógio do convidado x do central ─────────────────────────────
def estimar_gmtoff(local: str, host_iso: str) -> int:
    """Diferença (s) entre a hora LOCAL do convidado (`MM-DD HH:MM:SS`) e o UTC do host, arredondada a 30 min (fuso)."""
    ano = int(host_iso[:4])
    lm = datetime.strptime(f"{ano}-{local[:14]}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    hu = datetime.fromisoformat(host_iso.replace("Z", "+00:00"))
    return int(round((lm - hu).total_seconds() / 1800.0)) * 1800


def para_central(rotulo: str, ano: int, gmtoff_s: int, offset_s: float = 0.0) -> str:
    """Hora local do logcat -> UTC do central (`gmtoff_s` = fuso do convidado; `offset_s` = convidado menos central, medido)."""
    m = re.match(r"(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)\.(\d{3})$", rotulo)
    mes, dia, h, mi, s, ms = (int(x) for x in m.groups())                                             # type: ignore[union-attr]
    epoch = calendar.timegm((ano, mes, dia, h, mi, s)) + ms / 1000.0 - gmtoff_s - offset_s
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(round((epoch % 1) * 1000)):03d}Z"


# ───────────────────────────── a assinatura do boot ─────────────────────────────
def classificar(ev: dict[str, Any], rede: dict[str, Any], am: dict[str, Any], classe_vpn: str = f"{PACOTE}/.bg.VPNService") -> dict[str, Any]:
    """Classifica UM boot pelo que o sistema registrou. A assinatura E2 (`SILENT_STOP`): o `VPNService` sobe a foreground e sai dela
    até JANELA_SILENCIOSA_S depois com `STOP_FOREGROUND` (chamado pelo app), sem ANR, crash, kill nem morte e com o MESMO PID, sem `tun0`."""
    fg = [e for e in ev["fgs_start"] if e["classe"] == classe_vpn]
    fim = [e for e in ev["fgs_stop"] if e["classe"] == classe_vpn]
    pids_servico = [p["pid"] for p in ev["proc_start"] if "VPNService" in p["componente"]]
    kills = [k for k in ev["kill"] if not k["benigno"]]                                               # `empty #24` tardio não conta
    mortes = [m for m in ev["proc_died"] if not m["benigno"]]
    morreu = bool(ev["anr"] or ev["crash"] or kills or mortes)
    sem_tun = am.get("t_tun") is None
    d: dict[str, Any] = {"morte_ou_anr_no_boot": morreu, "mesmo_pid": am.get("mudancas_de_pid", 0) == 0 and bool(am.get("pids_distintos")),
                         "tun": not sem_tun}
    if fg and fim:
        d["fgs_duracao_s"] = atraso(fg[0]["t"], fim[0]["t"])
        d["motivo_fim"] = fim[0]["motivo"]
    if not sem_tun:
        d["codigo"] = "TUN_OK"
    elif ev["anr"] or kills:
        d["codigo"] = "ANR_OU_KILL"
    elif ev["crash"]:
        d["codigo"] = "CRASH"
    elif fg and fim and (d.get("fgs_duracao_s") or 1e9) <= JANELA_SILENCIOSA_S and fim[0]["motivo"] == "STOP_FOREGROUND" and not morreu:
        d["codigo"] = "SILENT_STOP"                                                                   # a assinatura E2
    elif fg and not fim:
        d["codigo"] = "FGS_SEM_TUN"
    elif pids_servico and not fg:
        d["codigo"] = "SERVICO_SEM_FOREGROUND"
    elif not pids_servico and not rede["always_on_start"]:
        d["codigo"] = "SEM_ALWAYS_ON_DO_SISTEMA"
    else:
        d["codigo"] = "INDETERMINADO"
    return d


def relogio_do_ensaio(pasta: Path, kind: str, saida: dict[str, Any]) -> dict[str, Any] | None:
    """Fuso e deslocamento do convidado em relação ao central. Capturas novas trazem `relogio.json` (medido com o host dos dois lados
    da leitura); as antigas só têm o `date` local no `.txt`, e o fuso sai da comparação com o fim da observação (arredondada a 30 min,
    sem deslocamento medido: `offset_s` = 0 e `fonte` diz isso)."""
    pj = pasta / f"boot-{kind}.relogio.json"
    if pj.exists():
        j = json.loads(pj.read_text(encoding="utf-8"))
        return {"fonte": "medido", "ano": j["ano"], "gmtoff_s": j["gmtoff_s"], "offset_s": j["offset_s"], "incerteza_s": j.get("incerteza_s")}
    pt = pasta / f"boot-{kind}.relogio.txt"
    fim = ((saida.get("boot") or {}).get("fim_observacao"))
    if pt.exists() and fim:
        m = re.search(r"^(\d\d-\d\d \d\d:\d\d:\d\d)", pt.read_text(encoding="utf-8", errors="replace"), re.M)
        if m:
            return {"fonte": "estimado_do_fuso", "ano": int(fim[:4]), "gmtoff_s": estimar_gmtoff(m.group(1), fim), "offset_s": 0.0,
                    "incerteza_s": 30.0}
    return None


def troca_durante(padrao: list[dict[str, Any]], inicio: str | None, fim: str | None) -> list[dict[str, Any]]:
    """Trocas de TRANSPORTE da rede padrão (celular -> Wi-Fi ou o inverso) entre o início do processo do serviço e 5 s depois do fim
    do FGS (ou do seu começo, se não houve fim): a janela em que o cliente lê a rede padrão e abre o túnel. É a variável da hipótese
    H2 do doc. A 1ª rede padrão do boot não conta como troca (`de` seria None); mudar de rede do mesmo transporte também não."""
    if not inicio or not fim:
        return []
    a, b = segundos(inicio), segundos(fim) + 5.0
    saida, antes = [], None
    for p in padrao:
        if antes is not None and p["transporte"] != antes and a <= segundos(p["t"]) <= b:
            saida.append({"t": p["t"], "de": antes, "para": p["transporte"], "depois_do_proc_s": round(segundos(p["t"]) - a, 3)})
        antes = p["transporte"]
    return saida


def resumir(pasta: Path, kind: str, saida: dict[str, Any] | None = None) -> dict[str, Any]:
    """O resumo de UM ensaio já capturado em `pasta` (`boot-<kind>.*`). Falta de arquivo vira `lacunas`, nunca erro."""
    lacunas: list[str] = []

    def ler(nome: str) -> str | None:
        p = pasta / f"boot-{kind}.{nome}"
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")
        lacunas.append(nome)
        return None

    events, main = ler("events.txt"), ler("main-system.txt")
    ev = parse_eventos(events or "")
    rede = parse_rede(main or "")
    linhas = []
    pam = pasta / f"boot-{kind}.amostras.jsonl"
    if pam.exists():
        linhas = [json.loads(x) for x in pam.read_text(encoding="utf-8").splitlines() if x.strip()]
    else:
        lacunas.append("amostras.jsonl")
    am = parse_amostras(linhas)
    ps = pasta / f"boot-{kind}.saida.json"
    if saida is None:                                                                                 # `rodar` passa o dict em memória (o json só sai depois)
        saida = json.loads(ps.read_text(encoding="utf-8")) if ps.exists() else {}
    ass = classificar(ev, rede, am)
    exit_txt = ler("exit-info.txt")
    exitinfo = parse_exit_info(exit_txt or "")
    pacote_txt = (pasta / f"boot-{kind}.pacote.txt")
    pacote_fim = parse_pacote(pacote_txt.read_text(encoding="utf-8", errors="replace")) if pacote_txt.exists() else None
    if pacote_fim is None:
        lacunas.append("pacote.txt")
    pacote_boot = (saida.get("boot") or {}).get("pacote")                                             # 1º adb / boot_completed / fim, lidos DURANTE o boot
    if not pacote_boot:
        lacunas.append("pacote_no_boot")
    btxt = pasta / f"boot-{kind}.broadcasts.txt"
    bcast = parse_broadcasts(btxt.read_text(encoding="utf-8", errors="replace")) if btxt.exists() else None
    if bcast is None:
        lacunas.append("broadcasts.txt")
    # Tempo relativo (convidado): o always-on e o serviço contra a primeira rede / a rede padrão.
    ao = rede["always_on_start"][0] if rede["always_on_start"] else None
    proc = next((p for p in ev["proc_start"] if "VPNService" in p["componente"]), None)
    fg = next((e for e in ev["fgs_start"] if "VPNService" in e["classe"]), None)
    fim = next((e for e in ev["fgs_stop"] if "VPNService" in e["classe"]), None)
    pr, pp = rede["primeira_rede"], rede["primeira_padrao"]
    wifi = next((a for a in rede["agentes"] if a["transporte"] == "WIFI"), None)
    cel = next((a for a in rede["agentes"] if a["transporte"] == "CELLULAR"), None)
    rel = relogio_do_ensaio(pasta, kind, saida)
    momentos: dict[str, Any] = {}
    if rel:
        for nome, rot in (("primeira_rede", pr["t"] if pr else None), ("rede_padrao", pp["t"] if pp else None), ("always_on", ao),
                          ("proc_start", proc["t"] if proc else None), ("fgs_start", fg["t"] if fg else None),
                          ("fgs_stop", fim["t"] if fim else None)):
            if rot:
                momentos[nome] = {"convidado": rot, "central_utc": para_central(rot, rel["ano"], rel["gmtoff_s"], rel["offset_s"])}
    return {
        "ensaio": kind, "assinatura": ass, "lacunas": lacunas, "relogio": rel, "momentos": momentos,
        "rede": {"ordem": rede["ordem"], "primeira": pr, "primeira_padrao": pp,
                 "wifi_menos_celular_s": atraso(cel["t"], wifi["t"]) if cel and wifi else None,
                 "trocas_da_rede_padrao": [(p["t"], p["transporte"]) for p in rede["padrao"]]},
        "always_on": {"t": ao, "depois_da_primeira_rede_s": atraso(pr["t"], ao) if pr else None,
                      "depois_da_rede_padrao_s": atraso(pp["t"], ao) if pp else None},
        "troca_padrao_durante_o_inicio": troca_durante(rede["padrao"], proc["t"] if proc else None, fim["t"] if fim else (fg["t"] if fg else None)),
        "servico": {"proc_start": proc, "fgs_start": fg, "fgs_stop": fim,
                    "proc_ate_fgs_s": atraso(proc["t"], fg["t"]) if proc and fg else None,
                    "always_on_ate_fgs_s": atraso(ao, fg["t"]) if ao and fg else None,
                    "fgs_ate_rede_padrao_s": atraso(pp["t"], fg["t"]) if pp and fg else None,
                    "notificacao_cancelada": ev["notificacao_cancelada"]},
        "processo": {"pids": am["pids_distintos"], "mudancas_de_pid": am["mudancas_de_pid"], "anr": ev["anr"], "kill": [k for k in ev["kill"] if not k["benigno"]],
                     "kill_benigno": [k for k in ev["kill"] if k["benigno"]],
                     "crash": ev["crash"], "died": [m for m in ev["proc_died"] if not m["benigno"]], "freeze": ev["freeze"][:2]},
        "amostras": {"t_tun": am["t_tun"], "t_wlan0": am["t_wlan0"], "t_eth0": am["t_eth0"], "t_boot_completed": am["t_boot_completed"]},
        "exit_info": {"persistido_em": exitinfo["persistido_em"], "n": len(exitinfo["entradas"]), "entradas": exitinfo["entradas"][:6]},
        "pacote_fim": pacote_fim, "pacote_no_boot": pacote_boot,
        "boot_receiver": _boot_receiver(bcast, fg),
        "preparo": saida.get("preparo"),
    }


def _boot_receiver(bcast: list[dict[str, Any]] | None, fg: dict[str, Any] | None) -> dict[str, Any] | None:
    """A 2ª chance do cliente: o `BootReceiver` foi ENTREGUE (rodou) ou pulado, e quando, contra o `startForeground` do serviço."""
    if bcast is None:
        return None
    b = next((x for x in bcast if x["acao"] == "BOOT_COMPLETED" and (x["receptor"] or "").endswith("BootReceiver")), None)
    if b is None:
        return {"estado": "AUSENTE"}
    d = dict(b)
    if fg and b.get("terminou_em"):
        d["terminou_ate_fgs_s"] = atraso(b["terminou_em"], fg["t"])
    return d


def tabela(resumos: list[dict[str, Any]]) -> str:
    """Uma linha por boot, para comparar os pares do experimento (os x os+stopped) de relance."""
    cab = ("ensaio", "codigo", "ordem_rede", "fgs_s", "fim", "pid", "ao_ate_fgs_s", "troca_padrao", "stopped_1o_adb", "boot_receiver")
    linhas = [" | ".join(cab)]
    for r in resumos:
        a = r["assinatura"]
        stp = ((r.get("pacote_no_boot") or {}).get("primeiro_adb") or {}).get("stopped")
        br = (r.get("boot_receiver") or {}).get("estado", "-")
        linhas.append(" | ".join(str(x) for x in (r["ensaio"], a["codigo"], ">".join(r["rede"]["ordem"]) or "-", a.get("fgs_duracao_s", "-"),
                                                   a.get("motivo_fim", "-"), ",".join(r["processo"]["pids"]) or "-",
                                                   r["servico"]["always_on_ate_fgs_s"], len(r["troca_padrao_durante_o_inicio"]), "-" if stp is None else stp, br)))
    return "\n".join(linhas)
