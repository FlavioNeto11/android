#!/usr/bin/env python
"""Coletor DIAGNÓSTICO do W8 do android-09 (docs/handoffs/w8-diagnostico-android09.md). SOMENTE LEITURA.

Não existe modo que escreva: nada de `network/assign`, `verify`, `reapply`, manutenção, tile, `force-stop`, reinício,
`settings put` ou escrita no banco. O que ele faz, em paralelo, para uma janela que OUTRA pessoa/rotina abriu:

  - aparelho (uma ida `adb shell` por amostra, ~2 s; a parte pesada, `dumpsys`, a cada N amostras, porque o convidado
    de 2 vCPU sem CPU é a hipótese de fundo e o coletor não pode ser o que o derruba);
  - logcat filtrado e com horário, num laço que reconecta (o reinício derruba o adb e apaga o buffer do aparelho);
  - central: linha `device_network`, comandos `device.network`/`restart`, eventos `network.updated` (SQLite `mode=ro`) e
    o peer em `GET /api/network/server`;
  - notebook por SSH, só se o SSH responder de verdade (a chave do projeto é restrita a túnel: então só o que o agente
    do worker já reporta ao central).

Subcomandos: `comandos` (mostra o que vai ao aparelho), `preflight`, `coletar`, `classificar`.
O classificador aponta o PRIMEIRO ponto quebrado (F1..F10) a partir do que foi coletado; faltou dado = UNKNOWN,
nunca "sucesso pela ausência de log".
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACOTE = "io.nekohasekai.sfa"
TILE = f"custom({PACOTE}/.bg.TileService)"
SERIAL = "127.0.0.1:15555"
IID = "android-09"
API = "http://127.0.0.1:8000/api"

# ---------------------------------------------------------------------------------------------------- o que vai ao aparelho
# Só leitura (o teste `test_comandos_so_leem` confere). `settings get`, `getprop`, `pidof`, `ip addr`, `dumpsys`.
CMD_LEVE = (
    "echo U=$(id -u); echo S=$(cut -d. -f1 /proc/uptime); echo B=$(getprop sys.boot_completed); "
    "echo A=$(settings get secure always_on_vpn_app); echo L=$(settings get secure always_on_vpn_lockdown); "
    "echo T=$(ip -o addr show tun0 2>/dev/null | grep -c inet); "
    f"echo PS=$(pidof {PACOTE}); echo PU=$(pidof com.android.systemui); "
    f"echo Q=$(settings get secure sysui_qs_tiles | grep -c -F '{TILE}')"
)
# O `dumpsys connectivity` é grande: conta só as linhas `UIDs:` das regras (como `comando_de_observacao`, 658e5bb) e o
# `ni{VPN CONNECTED`. Pesado para um convidado sem CPU: por isso só a cada `--pesado-a-cada` amostras.
CMD_PESADO = (
    "D=$(dumpsys connectivity 2>/dev/null); "
    "echo V=$(echo \"$D\" | grep -c 'ni[{]VPN CONNECTED'); "
    "echo R=$(echo \"$D\" | grep -A8 'Lockdown filtering rules' | grep -c 'UIDs:'); "
    f"echo ST=$(dumpsys package {PACOTE} 2>/dev/null | grep -m1 -o 'stopped=[a-z]*')"
)

_SO_LEITURA_PROIBIDO = (r"\bforce-stop\b", r"cmd statusbar", r"\bsettings put\b", r"\bam (start|broadcast|force-stop)\b",
                        r"\bsvc\b", r"\breboot\b", r"\bpm (clear|disable|uninstall|install)\b", r"\binput\b",
                        r"\bsetprop\b", r"\bkill\b", r"\brm\b", r"\bsu\b")

PS_NOTEBOOK = r"""
$ErrorActionPreference = 'SilentlyContinue'
$c = Get-Counter '\Memory\Pages/sec','\Memory\Committed Bytes','\Memory\Commit Limit'
foreach ($s in $c.CounterSamples) { 'ctr=' + $s.Path.Split('\')[-1] + '=' + [math]::Round($s.CookedValue) }
$os = Get-CimInstance Win32_OperatingSystem
'ram_free_mb=' + [math]::Round($os.FreePhysicalMemory / 1024)
Get-Process -Name 'qemu-system*' | ForEach-Object { 'qemu=' + $_.Id + ':' + [math]::Round($_.CPU, 1) + ':' + [math]::Round($_.WorkingSet64 / 1MB) }
'adb=' + ((& adb devices 2>$null) -join ';')
"""


def agora() -> float:
    return time.time()


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(t * 1000) % 1000:03d}Z"


def epoch(texto: str | float | None) -> float | None:
    """ISO UTC (`2026-10-01T13:26:55Z`), `-0300 2026-10-01 10:17:49` (o `last_connection` do servidor) ou epoch."""
    if texto is None or texto == "":
        return None
    if isinstance(texto, (int, float)):
        return float(texto)
    t = texto.strip()
    m = re.match(r"^([+-]\d{4}) (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})$", t)
    if m:
        return datetime.strptime(f"{m.group(2)} {m.group(3)} {m.group(1)}", "%Y-%m-%d %H:%M:%S %z").timestamp()
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except ValueError:
        try:
            return float(t)
        except ValueError:
            return None


# ---------------------------------------------------------------------------------------------------- parsers
def parse_pares(saida: str) -> dict[str, str]:
    """`CHAVE=valor` por linha; o que não for um par é ignorado."""
    v: dict[str, str] = {}
    for linha in (saida or "").splitlines():
        m = re.match(r"^([A-Z]{1,2})=(.*)$", linha.strip())
        if m:
            v[m.group(1)] = m.group(2).strip()
    return v


def _int(valor: str | None) -> int | None:
    return int(valor) if valor is not None and valor.strip().isdigit() else None


def _pid(valor: str | None) -> int | None:
    """`pidof` devolve vários pids separados por espaço; o primeiro basta (o que importa é mudar ou sumir)."""
    primeiro = (valor or "").split()
    return int(primeiro[0]) if primeiro and primeiro[0].isdigit() else None


def amostra_do_aparelho(saida: str, t: float, *, pesada: bool) -> dict[str, object]:
    """A amostra estruturada. Campo que não veio é `None`: o classificador trata como desconhecido."""
    v = parse_pares(saida)
    ok = "U" in v
    a: dict[str, object] = {"t": t, "ts": iso(t), "src": "android", "adb": ok, "uid": _int(v.get("U")),
                            "uptime": _int(v.get("S")), "boot": (v.get("B") == "1") if ok else None,
                            "always_on": v.get("A") if ok else None, "lockdown": v.get("L") if ok else None,
                            "tun": (_int(v.get("T")) or 0) > 0 if "T" in v else None,
                            "pid_sfa": _pid(v.get("PS")) if ok else None, "pid_ui": _pid(v.get("PU")) if ok else None,
                            "tile": (_int(v.get("Q")) or 0) > 0 if "Q" in v else None}
    if pesada:
        a["vpn"] = (_int(v.get("V")) or 0) > 0 if "V" in v else None
        a["regras"] = _int(v.get("R"))
        a["stopped"] = {"stopped=true": True, "stopped=false": False}.get(v.get("ST", ""))
    return a


def parse_notebook(saida: str) -> dict[str, object]:
    out: dict[str, object] = {"ctr": {}, "qemu": []}
    for linha in (saida or "").splitlines():
        linha = linha.strip()
        if linha.startswith("ctr="):
            _, nome, valor = linha.split("=", 2)
            out["ctr"][nome] = _int(valor)                                    # type: ignore[index]
        elif linha.startswith("ram_free_mb="):
            out["ram_free_mb"] = _int(linha.split("=", 1)[1])
        elif linha.startswith("qemu="):
            p = linha.split("=", 1)[1].split(":")
            if len(p) == 3:
                out["qemu"].append({"pid": _int(p[0]), "cpu_s": p[1], "ws_mb": _int(p[2])})   # type: ignore[union-attr]
        elif linha.startswith("adb="):
            out["adb"] = linha.split("=", 1)[1]
    return out


# ---------------------------------------------------------------------------------------------------- classificador
CLICK_RE = re.compile(rf"{re.escape(PACOTE)}.*(TileService|onClick|onStartListening|handleClick)|"
                      rf"(TileService|onClick|onStartListening|handleClick).*{re.escape(PACOTE)}", re.I)
SERVICO_RE = re.compile(r"Established by|startForeground|VpnService|onStartCommand|Vpn.*prepare", re.I)
ANR_RE = re.compile(rf"ANR in {re.escape(PACOTE)}|did not then call Service\.startForeground", re.I)

ESTAGIOS = ("F1_SYSTEMUI_RESTART", "F2_TILE_NOT_ADDED", "F3_TILE_NOT_CLICKED", "F4_PACKAGE_STAYS_STOPPED",
            "F5_VPN_SERVICE_NOT_STARTED", "F6_TUN_NOT_CREATED", "F7_VPN_NOT_CONNECTED", "F8_LOCKDOWN_MISMATCH",
            "F9_NO_WIREGUARD_HANDSHAKE")
PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"


def _validos(am: list[dict], campo: str) -> list[dict]:
    return [a for a in am if a.get("adb") and a.get(campo) is not None]


def _pids_lidos(am: list[dict]) -> list[int | None]:
    """O pid do cliente em cada amostra com adb: `None` = o processo não existe (lido), não "não li"."""
    return [a.get("pid_sfa") for a in am if a.get("adb") and a.get("uid") is not None]


def _f1(am, log, ctx):
    pids = [a["pid_ui"] for a in _validos(am, "pid_ui")]
    if len(pids) < 2:
        return UNKNOWN, "menos de 2 leituras do pid do SystemUI"
    if len(set(pids)) > 1:
        return FAIL, f"pid do SystemUI mudou na janela: {pids[0]} → {pids[-1]}"
    return PASS, f"SystemUI estável (pid {pids[0]}, {len(pids)} leituras)"


def _f2(am, log, ctx):
    q = [a["tile"] for a in _validos(am, "tile")]
    if any(q):
        return PASS, "o tile apareceu em sysui_qs_tiles"
    if len(q) >= 3:
        return FAIL, f"o tile não apareceu em {len(q)} leituras"
    return UNKNOWN, "poucas leituras para dizer que o tile não entrou (a janela do add-tile é de ~3 s)"


def _f3(am, log, ctx):
    pids = _pids_lidos(am)
    nasceu = bool(pids) and pids[0] is None and any(p is not None for p in pids)
    if any(CLICK_RE.search(x) for x in log) or nasceu:
        return PASS, "o clique chegou ao cliente (logcat do tile ou processo do cliente nasceu)"
    if ctx.get("logcat_ok") and pids:
        return FAIL, "logcat capturado e sem sinal do tile do cliente; o processo do cliente não nasceu"
    return UNKNOWN, "sem logcat da janela: não dá para separar 'não clicou' de 'clicou e nada aconteceu'"


def _f4(am, log, ctx):
    st = [a["stopped"] for a in _validos(am, "stopped")]
    pids = [p for p in _pids_lidos(am) if p is not None]
    if not st:
        return UNKNOWN, "sem leitura pesada (stopped=) na janela"
    if any(st) and not pids:
        return FAIL, "o pacote ficou stopped=true e o processo do cliente nunca existiu na janela"
    return PASS, "pacote não ficou preso em stopped"


def _f5(am, log, ctx):
    if any(a.get("tun") for a in am if a.get("adb")):
        return PASS, "há tun0: o serviço do VPN subiu"
    if any(ANR_RE.search(x) for x in log):
        return FAIL, "ANR do cliente / startForeground não chamado a tempo (logcat)"
    pids = [p for p in _pids_lidos(am) if p is not None]
    if not pids:
        return UNKNOWN, "o processo do cliente nunca apareceu e não há ANR: ver F3/F4"
    if any(SERVICO_RE.search(x) for x in log):
        return PASS, "o serviço de VPN subiu (logcat), sem ANR"
    if ctx.get("logcat_ok"):
        return FAIL, "o cliente tem processo, mas o logcat não mostra o serviço de VPN subindo"
    return UNKNOWN, "sem logcat do serviço"


def _f6(am, log, ctx):
    if any(a.get("tun") for a in am if a.get("adb")):
        return PASS, "tun0 criado"
    pids = [p for p in _pids_lidos(am) if p is not None]
    if pids and any(SERVICO_RE.search(x) for x in log):
        return FAIL, "o serviço de VPN subiu (logcat) e o tun0 nunca apareceu"
    return UNKNOWN, "sem sinal de que o serviço chegou a tentar criar o tun0"


def _f7(am, log, ctx):
    com_tun = [a for a in _validos(am, "vpn") if a.get("tun")]
    if any(a["vpn"] for a in com_tun):
        return PASS, "VPN CONNECTED com tun0"
    if com_tun and com_tun[-1]["t"] - com_tun[0]["t"] >= 5:
        return FAIL, "tun0 no ar por ≥5 s sem VPN CONNECTED no dumpsys"
    return UNKNOWN, "sem leitura pesada com tun0 no ar"


def _f8(am, log, ctx):
    esperado = ctx.get("bloqueio")
    ok = [a for a in _validos(am, "regras") if a.get("tun") and a.get("vpn")]
    if esperado is None or not ok:
        return UNKNOWN, "política esperada ou leitura de regras ausente"
    real = ok[-1]["regras"] > 0
    if real == esperado:
        return PASS, f"regras de bloqueio {'ativas' if real else 'ausentes'}, como a política pede"
    return FAIL, f"regras de bloqueio {'ativas' if real else 'ausentes'}, a política espera {'ativas' if esperado else 'ausentes'}"


def _f9(am, log, ctx):
    h = ctx.get("handshake_depois")
    if h is None:
        return UNKNOWN, "sem leitura do peer no servidor"
    return (PASS, "o servidor viu o par depois do início da janela") if h else \
           (FAIL, "o servidor não viu o par depois do início da janela")


def _f10(am, log, ctx):
    com_uptime = _validos(am, "uptime")
    if not com_uptime:
        return UNKNOWN, "sem uptime"
    if any(a.get("tun") for a in am if a.get("adb")):
        return PASS, "o túnel subiu depois do boot"
    if max(a["uptime"] for a in com_uptime) >= 180:
        return FAIL, "180 s de uptime sem tun0 (o always-on não subiu o túnel neste boot)"
    return UNKNOWN, "o boot ainda não completou 180 s"


_FUNCOES = {"F1_SYSTEMUI_RESTART": _f1, "F2_TILE_NOT_ADDED": _f2, "F3_TILE_NOT_CLICKED": _f3,
            "F4_PACKAGE_STAYS_STOPPED": _f4, "F5_VPN_SERVICE_NOT_STARTED": _f5, "F6_TUN_NOT_CREATED": _f6,
            "F7_VPN_NOT_CONNECTED": _f7, "F8_LOCKDOWN_MISMATCH": _f8, "F9_NO_WIREGUARD_HANDSHAKE": _f9}


def classificar(amostras: list[dict], logcat: list[str], ctx: dict | None = None) -> dict[str, object]:
    """O primeiro ponto quebrado, na ordem do caminho do tile (modo `tile`) ou o boot sem túnel (modo `boot`).

    - `FAIL` num estágio com estágio ANTERIOR desconhecido → `UNKNOWN` com `candidato` (não dá para provar que foi o
      primeiro);
    - nada falhou mas faltou dado → `UNKNOWN`; tudo passou → `OK_HEALTHY`;
    - tudo passou, mas o túnel caiu no fim → `F_OTHER`.
    Ausência de log nunca vira PASS: sem `logcat_ok`, F3/F5/F6 ficam desconhecidos.
    """
    ctx = ctx or {}
    modo = ctx.get("modo", "tile")
    am = [a for a in amostras if a.get("src", "android") == "android"]
    if not [a for a in am if a.get("adb")]:
        return {"codigo": "UNKNOWN", "motivo": "nenhuma amostra do aparelho com adb na janela", "estagios": {}}
    nomes = ["F10_BOOT_RECOVERY_FAILED"] if modo == "boot" else list(ESTAGIOS)
    funcoes = dict(_FUNCOES, F10_BOOT_RECOVERY_FAILED=_f10)
    estagios: dict[str, dict[str, str]] = {}
    desconhecidos: list[str] = []
    primeiro: str | None = None
    for nome in nomes:
        status, evidencia = funcoes[nome](am, logcat, ctx)
        estagios[nome] = {"status": status, "evidencia": evidencia}
        if status == UNKNOWN:
            desconhecidos.append(nome)
        elif status == FAIL and primeiro is None:
            primeiro = nome
            break
    if primeiro is not None:
        if desconhecidos:
            return {"codigo": "UNKNOWN", "candidato": primeiro, "desconhecidos_antes": desconhecidos,
                    "motivo": f"{primeiro} falhou, mas {', '.join(desconhecidos)} não pôde ser avaliado antes",
                    "estagios": estagios}
        return {"codigo": primeiro, "evidencia": estagios[primeiro]["evidencia"], "estagios": estagios}
    if desconhecidos:
        return {"codigo": "UNKNOWN", "desconhecidos": desconhecidos, "motivo": "faltou evidência", "estagios": estagios}
    ult = [a for a in am if a.get("adb") and a.get("tun") is not None]
    if ult and not ult[-1]["tun"]:
        return {"codigo": "F_OTHER", "motivo": "tudo passou, mas o túnel caiu no fim da janela", "estagios": estagios}
    return {"codigo": "OK_HEALTHY", "estagios": estagios}


def comandos_so_leem() -> list[str]:
    """Os padrões proibidos que aparecem nos comandos que o coletor manda (vazio = só leitura)."""
    texto = " ; ".join((CMD_LEVE, CMD_PESADO, PS_NOTEBOOK))
    return [p for p in _SO_LEITURA_PROIBIDO if re.search(p, texto)]


# ---------------------------------------------------------------------------------------------------- logcat
_KEEP_VPN = re.compile(r"vpn|lockdown|always", re.I)
_KEEP_FORTE = re.compile(r"\bANR\b|FATAL EXCEPTION|force-?stop|startForeground|Start proc.*nekohasekai", re.I)


def logcat_mantem(linha: str) -> bool:
    """O filtro do logcat (nunca o log inteiro): o cliente, tile, VPN/ConnectivityService, ANR e SystemUI que morre."""
    if PACOTE in linha or "sing-box" in linha or "singbox" in linha:
        return True
    if "TileService" in linha or "QSTileHost" in linha or _KEEP_FORTE.search(linha):
        return True
    if ("ConnectivityService" in linha or "VpnService" in linha or re.search(r"\bVpn\b", linha)) and _KEEP_VPN.search(linha):
        return True
    return bool(re.search(r"systemui", linha, re.I) and re.search(r"died|crash|ANR|restart", linha, re.I))


# ---------------------------------------------------------------------------------------------------- E/S
class Saida:
    def __init__(self, pasta: Path):
        pasta.mkdir(parents=True, exist_ok=True)
        self.pasta = pasta
        self._amostras = (pasta / "samples.jsonl").open("a", encoding="utf-8", buffering=1)
        self._logcat = (pasta / "logcat.txt").open("a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()

    def amostra(self, d: dict) -> None:
        with self._lock:
            self._amostras.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")

    def linha_logcat(self, t: float, linha: str) -> None:
        with self._lock:
            self._logcat.write(f"{iso(t)} {linha.rstrip()}\n")


def achar_adb(informado: str | None) -> str | None:
    for c in (informado, os.environ.get("ADB"), shutil.which("adb"),
              str(Path(os.environ.get("ANDROID_SDK_ROOT", r"C:\Android\Sdk")) / "platform-tools" / "adb.exe")):
        if c and Path(c).exists() or (c and shutil.which(c)):
            return c
    return None


def _executa(args: list[str], timeout: float, *, juntar_erro: bool = False) -> tuple[int, str]:
    try:
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT if juntar_erro else subprocess.PIPE,
                           text=True, timeout=timeout, encoding="utf-8", errors="replace")
        return r.returncode, r.stdout
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 255, f"ERRO {type(exc).__name__}: {exc}"


def preflight_ssh(chave: Path, usuario: str, host: str) -> dict[str, str]:
    """O SSH responde de verdade? `ok` = rodou um comando; `restrito` = conectou mas o servidor força `exit` (a chave do
    projeto é só de túnel); `falhou` = nem conectou. Nada é configurado aqui."""
    if not chave.exists():
        return {"estado": "falhou", "detalhe": f"chave ausente: {chave}"}
    rc, out = _executa(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-i", str(chave), f"{usuario}@{host}",
                        "echo DIAGW8_OK"], 20, juntar_erro=True)
    if "DIAGW8_OK" in out:
        return {"estado": "ok", "detalhe": ""}
    if rc == 0:
        return {"estado": "restrito", "detalhe": "conectou, mas nenhum comando rodou (chave restrita a encaminhamento)"}
    util = " ".join(x for x in out.splitlines() if x.strip() and not x.startswith("**"))   # tira o aviso pós-quântico
    return {"estado": "falhou", "detalhe": f"rc={rc} {util}"[:200]}


def cmd_notebook(chave: Path, usuario: str, host: str) -> list[str]:
    cod = base64.b64encode(PS_NOTEBOOK.encode("utf-16-le")).decode()
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-i", str(chave), f"{usuario}@{host}",
            "powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", cod]


def get_json(url: str, timeout: float = 10):
    with urllib.request.urlopen(url, timeout=timeout) as r:                    # noqa: S310 - loopback do central
        return json.loads(r.read().decode("utf-8"))


def amostra_central(db: Path, iid: str, desde: float, vistos: set, ultimo_evento: list[str]) -> list[dict]:
    """Mudanças desde a última amostra, só leitura (`mode=ro`): linha de rede, comandos, eventos e o peer."""
    saida: list[dict] = []
    t = agora()
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        linha = con.execute("SELECT desired_rev, applied_rev, state, policy, leak_pending, leak_result, leak_client, "
                            "leak_at, detail, error FROM device_network WHERE instance_id=?", (iid,)).fetchone()
        saida.append({"t": t, "ts": iso(t), "src": "linha", "linha": dict(linha) if linha else None})
        for r in con.execute("SELECT id, verb, state, created_at, finished_at, substr(reason,1,200) AS reason "
                             "FROM commands WHERE instance_id=? AND verb IN ('device.network','restart') "
                             "AND created_at>=?", (iid, iso(desde))):
            chave = (r["id"], r["state"])
            if chave not in vistos:
                vistos.add(chave)
                saida.append({"t": t, "ts": iso(t), "src": "comando", **dict(r)})
        for r in con.execute("SELECT ts, kind, level, substr(message,1,300) AS message FROM events "
                             "WHERE instance_id=? AND kind='network.updated' AND ts>? ORDER BY ts",
                             (iid, ultimo_evento[0] or iso(desde))):
            ultimo_evento[0] = r["ts"]
            saida.append({"t": t, "ts": iso(t), "src": "evento", **dict(r)})
    finally:
        con.close()
    return saida


def amostra_peer(api: str, iid: str) -> dict:
    t = agora()
    try:
        s = get_json(f"{api}/network/server")
        peer = next((p for p in s.get("peers", []) if p.get("instance_id") == iid), None)
        return {"t": t, "ts": iso(t), "src": "peer", "servidor_no_ar": s.get("running"), "peer": peer}
    except Exception as exc:  # noqa: BLE001 - leitura que falha vira registro, não aborta a coleta
        return {"t": t, "ts": iso(t), "src": "peer", "erro": str(exc)[:200]}


def amostra_agente(api: str) -> dict:
    """Sem SSH útil: o que o agente do worker já reporta ao central (RAM livre, CPU, swap)."""
    t = agora()
    try:
        w = next(x for x in get_json(f"{api}/snapshot", 30)["workers"] if x["id"] == "worker-lan-01")
        r = w.get("resources") or {}
        return {"t": t, "ts": iso(t), "src": "agente", "cpu_percent": r.get("cpu_percent"),
                "ram_free_mb": r.get("ram_free_mb"), "swap_used_pct": r.get("swap_used_pct"),
                "worker_state": w.get("state"), "transport": w.get("transport_state")}
    except Exception as exc:  # noqa: BLE001
        return {"t": t, "ts": iso(t), "src": "agente", "erro": str(exc)[:200]}


def laco_logcat(adb: str, serial: str, saida: Saida, parar: threading.Event, max_mb: float) -> None:
    """Reconecta enquanto a coleta durar: o boot derruba o adb e zera o buffer do aparelho. Só linhas filtradas."""
    escrito = 0
    while not parar.is_set():
        p = subprocess.Popen([adb, "-s", serial, "logcat", "-v", "threadtime", "-T", "20"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
        try:
            assert p.stdout is not None
            for linha in p.stdout:
                if parar.is_set():
                    break
                if logcat_mantem(linha):
                    saida.linha_logcat(agora(), linha)
                    escrito += len(linha)
                    if escrito > max_mb * 1_000_000:
                        parar.set()
                        break
        finally:
            p.kill()
        parar.wait(1.0)


def coletar(a: argparse.Namespace) -> int:
    adb = achar_adb(a.adb)
    if adb is None:
        print("adb não encontrado (use --adb)", file=sys.stderr)
        return 2
    pasta = Path(a.saida) if a.saida else ROOT / "data" / "diag-w8" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    saida = Saida(pasta)
    chave = Path(a.ssh_chave).expanduser()
    ssh = preflight_ssh(chave, a.ssh_usuario, a.ssh_host)
    (pasta / "meta.json").write_text(json.dumps({"args": vars(a), "adb": adb, "ssh": ssh, "inicio": iso(agora())},
                                                ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"coletando em {pasta} (SOMENTE LEITURA); ssh notebook: {ssh['estado']}", file=sys.stderr)
    parar = threading.Event()
    desde = agora()
    threading.Thread(target=laco_logcat, args=(adb, a.serial, saida, parar, a.max_logcat_mb), daemon=True).start()

    def central() -> None:
        vistos: set = set()
        ultimo = [""]
        while not parar.is_set():
            try:
                for d in amostra_central(Path(a.db), a.instancia, desde, vistos, ultimo):
                    saida.amostra(d)
                saida.amostra(amostra_peer(a.api, a.instancia))
            except Exception as exc:  # noqa: BLE001
                saida.amostra({"t": agora(), "ts": iso(agora()), "src": "central", "erro": str(exc)[:200]})
            parar.wait(a.intervalo_central)

    def notebook() -> None:
        while not parar.is_set():
            if ssh["estado"] == "ok":
                rc, out = _executa(cmd_notebook(chave, a.ssh_usuario, a.ssh_host), 40)
                t = agora()
                saida.amostra({"t": t, "ts": iso(t), "src": "notebook", "rc": rc, **parse_notebook(out)})
            else:
                saida.amostra(amostra_agente(a.api))
            parar.wait(a.intervalo_notebook)

    threading.Thread(target=central, daemon=True).start()
    threading.Thread(target=notebook, daemon=True).start()
    n = 0
    fim = agora() + a.duracao if a.duracao else None
    try:
        while not parar.is_set() and (fim is None or agora() < fim):
            pesada = n % max(1, a.pesado_a_cada) == 0
            t = agora()
            rc, out = _executa([adb, "-s", a.serial, "shell", CMD_LEVE + ("; " + CMD_PESADO if pesada else "")], 25)
            saida.amostra(amostra_do_aparelho(out if rc == 0 else "", t, pesada=pesada))
            n += 1
            parar.wait(max(0.2, a.intervalo - (agora() - t)))
    except KeyboardInterrupt:
        pass
    parar.set()
    print(f"{n} amostras do aparelho em {pasta}", file=sys.stderr)
    return 0


def carregar(pasta: Path, de: float, ate: float) -> tuple[list[dict], list[str], dict]:
    amostras, peers = [], []
    for linha in (pasta / "samples.jsonl").read_text(encoding="utf-8").splitlines():
        d = json.loads(linha)
        if de <= d.get("t", 0) <= ate:
            (amostras if d.get("src") == "android" else peers).append(d)
    logcat, ok = [], False
    arq = pasta / "logcat.txt"
    if arq.exists():
        for linha in arq.read_text(encoding="utf-8", errors="replace").splitlines():
            ts, _, resto = linha.partition(" ")
            t = epoch(ts)
            if t is not None and de <= t <= ate:
                logcat.append(resto)
        ok = arq.stat().st_size > 0
    return amostras, logcat, {"logcat_ok": ok, "peers": peers}


def classificar_pasta(a: argparse.Namespace) -> int:
    de, ate = epoch(a.desde), epoch(a.ate)
    if de is None or ate is None:
        print("--desde e --ate precisam de horário ISO UTC", file=sys.stderr)
        return 2
    amostras, logcat, extra = carregar(Path(a.run), de, ate)
    ultimo_hs = None
    for p in extra["peers"]:
        if p.get("src") == "peer" and p.get("peer"):
            ultimo_hs = epoch((p["peer"] or {}).get("last_connection")) or ultimo_hs
    ctx = {"modo": a.modo, "logcat_ok": extra["logcat_ok"],
           "bloqueio": {"sim": True, "nao": False}.get(a.bloqueio),
           "handshake_depois": None if ultimo_hs is None else ultimo_hs >= de}
    print(json.dumps(classificar(amostras, logcat, ctx), ensure_ascii=False, indent=2))
    return 0


def preflight(a: argparse.Namespace) -> int:
    adb = achar_adb(a.adb)
    rel: dict[str, object] = {"adb": adb}
    if adb:
        rc, out = _executa([adb, "-s", a.serial, "shell", "echo U=$(id -u)"], 15)
        rel["aparelho"] = {"rc": rc, "uid": parse_pares(out).get("U"), "saida": out.strip()[:80]}
    rel["db_ro"] = Path(a.db).exists()
    try:
        rel["api"] = bool(get_json(f"{a.api}/health").get("status"))
    except Exception as exc:  # noqa: BLE001
        rel["api"] = f"erro: {exc}"
    rel["ssh_notebook"] = preflight_ssh(Path(a.ssh_chave).expanduser(), a.ssh_usuario, a.ssh_host)
    rel["comandos_so_leem"] = comandos_so_leem() == []
    print(json.dumps(rel, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    comum = argparse.ArgumentParser(add_help=False)
    comum.add_argument("--adb")
    comum.add_argument("--serial", default=SERIAL)
    comum.add_argument("--instancia", default=IID)
    comum.add_argument("--db", default=str(ROOT / "data" / "poc.sqlite3"))
    comum.add_argument("--api", default=API)
    comum.add_argument("--ssh-chave", default="~/.ssh/worker_ed25519")
    comum.add_argument("--ssh-usuario", default="farm-tunel")
    comum.add_argument("--ssh-host", default="192.168.1.19")
    sub.add_parser("comandos", help="mostra o que vai ao aparelho e ao notebook")
    sub.add_parser("preflight", parents=[comum], help="adb, banco, API e SSH respondem?")
    c = sub.add_parser("coletar", parents=[comum], help="coleta contínua, somente leitura")
    c.add_argument("--saida")
    c.add_argument("--duracao", type=float, default=0, help="segundos; 0 = até Ctrl-C")
    c.add_argument("--intervalo", type=float, default=2.0)
    c.add_argument("--pesado-a-cada", type=int, default=5, help="amostras entre leituras do dumpsys (default 5 = ~10 s)")
    c.add_argument("--intervalo-central", type=float, default=5.0)
    c.add_argument("--intervalo-notebook", type=float, default=10.0)
    c.add_argument("--max-logcat-mb", type=float, default=20.0)
    k = sub.add_parser("classificar", help="primeiro ponto quebrado (F1..F10) numa janela de uma coleta")
    k.add_argument("--run", required=True)
    k.add_argument("--desde", required=True)
    k.add_argument("--ate", required=True)
    k.add_argument("--modo", choices=("tile", "boot"), default="tile")
    k.add_argument("--bloqueio", choices=("sim", "nao", "desconhecido"), default="desconhecido")
    a = p.parse_args(argv)
    if a.cmd == "comandos":
        print("# aparelho (leve, cada amostra):\n" + CMD_LEVE + "\n# aparelho (pesado, a cada N):\n" + CMD_PESADO
              + "\n# notebook (PowerShell, via SSH só se responder):" + PS_NOTEBOOK)
        return 0
    return {"preflight": preflight, "coletar": coletar, "classificar": classificar_pasta}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
