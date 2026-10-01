#!/usr/bin/env python
"""Teste UI-START do W8 no android-09 (docs/handoffs/w8-diagnostico-android09.md §18): UM toque no botão Start do próprio cliente.

Pergunta: o caminho da interface (`MainActivity.startService0` → `Settings.rebuildServiceMode()` → `Settings.serviceClass()`)
troca o `ProxyService` do tile por `VPNService` no android-09? Se sim, o `serviceMode` do cliente estava em NORMAL (nunca
reconstruído: o import do perfil grava `selectedProfile` sem `rebuildServiceMode`). Só o android-09 (QA); SEGURO POR PADRÃO.

Subcomandos:
  plano   mostra o que faria (nenhuma chamada)
  ui      `--execute --instance android-09 --run <coletor>`: gate (as precondições do tile + conectividade healthy) → abre o app →
          lê a árvore da interface (perfil r2 selecionado, botão Start) → UM toque no Start → observa → se subiu, UM toque no Stop
          (o mecanismo normal da UI) → HOME → relê a linha de base. Um marcador na pasta impede o segundo toque.

Escritas possíveis no aparelho (o teste `test_vocabulario_de_escrita` fecha a lista): `am start` da MainActivity do cliente,
`input keyevent KEYCODE_HOME` e `input tap` (Start, depois Stop). Se o tun0 persistir depois do Stop da UI, o rollback do
acionador do tile (clique de desligar / `force-stop`) entra e é registrado como extraordinário. Sem perfil, par, política,
tile, WireGuard nem produto: nada disso é tocado aqui.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
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
IID = tile.IID
PERFIL_ESPERADO = "plataforma-android-09-r2"
OBSERVAR_S = 32.0
PARAR_S = 24.0
MARCADOR = "ui-start-09.tentativa"
CMD_ABRIR = f"am start -n {PACOTE}/.compose.MainActivity"
CMD_HOME = "input keyevent KEYCODE_HOME"
CMD_SERVICOS = (f"dumpsys activity services {PACOTE} | grep -E 'ServiceRecord|isForeground=|foregroundServiceType=|"
                "app=ProcessRecord' | head -20")
CMD_FOCO = "dumpsys window | grep -E 'mCurrentFocus' | head -2"


def agora() -> float:
    return time.time()


def iso(t: float | None = None) -> str:
    return coletor.iso(agora() if t is None else t)


# ---------------------------------------------------------------------------------------------------- ambiente
class Ambiente(tile.Ambiente):
    """O mundo (o do tile, para o android-09) + a árvore da interface pela API do central (leitura, sem controle)."""

    def hierarquia(self) -> dict:
        return coletor.get_json(f"{coletor.API}/instances/{IID}/hierarchy", 20)


def tocar(x: int, y: int) -> str:
    return f"input tap {int(x)} {int(y)}"


# ---------------------------------------------------------------------------------------------------- árvore da interface
def _texto(e: dict) -> str:
    return (e.get("text") or e.get("desc") or "").strip()


def textos(arvore: dict, limite: int = 60) -> list[str]:
    """Os textos visíveis do cliente (para registrar o que a tela diz, incluindo um eventual diálogo de erro)."""
    out: list[str] = []
    for e in arvore.get("elements", []):
        if e.get("package") == PACOTE and _texto(e):
            out.append(_texto(e)[:90])
    return out[:limite]


def achar_botao(arvore: dict, rotulo: str) -> dict[str, Any] | None:
    """O botão do cliente cujo rótulo (texto ou descrição) é EXATAMENTE `rotulo`. No Compose o rótulo é um filho não clicável
    de um contêiner clicável (android-09: `Start` em [608,960,656,1008] dentro do clicável [576,928,688,1040]); vale o menor
    contêiner clicável e habilitado que contém o rótulo. Rótulo ausente, repetido ou sem contêiner: None."""
    els = [e for e in arvore.get("elements", []) if e.get("package") == PACOTE and len(e.get("bounds") or []) == 4]
    rotulados = [e for e in els if _texto(e).lower() == rotulo.lower()]
    if len(rotulados) != 1:
        return None
    r = rotulados[0]
    rx0, ry0, rx1, ry1 = r["bounds"]
    cands = [e for e in els if e.get("clickable") and e.get("enabled", True)
             and e["bounds"][0] <= rx0 and e["bounds"][1] <= ry0 and e["bounds"][2] >= rx1 and e["bounds"][3] >= ry1]
    if not cands:
        return None
    alvo = min(cands, key=lambda e: (e["bounds"][2] - e["bounds"][0]) * (e["bounds"][3] - e["bounds"][1]))
    x0, y0, x1, y1 = alvo["bounds"]
    return {"id": alvo.get("id"), "rotulo_id": r.get("id"), "bounds": alvo["bounds"], "x": (x0 + x1) // 2, "y": (y0 + y1) // 2}


def perfil_selecionado(arvore: dict) -> bool:
    return any(e.get("package") == PACOTE and _texto(e) == PERFIL_ESPERADO for e in arvore.get("elements", []))


def servicos(amb: Ambiente) -> dict[str, Any]:
    """Os ServiceRecord do cliente: qual classe existe, qual está em primeiro plano (o rebind da UI ProxyService→VPNService)."""
    rc, out, _ = amb.shell(CMD_SERVICOS, 30)
    classes = sorted(set(re.findall(rf"{re.escape(PACOTE)}/\.bg\.(ProxyService|VPNService)", out if rc == 0 else "")))
    return {"classes": classes, "primeiro_plano": "isForeground=true" in out, "bruto": out.strip()[:700]}


# ---------------------------------------------------------------------------------------------------- precondições
def precondicoes(amb: Ambiente, run: Path | None) -> dict[str, Any]:
    g = tile.precondicoes(amb, run)
    try:
        inst = next(i for i in amb.snapshot()["instances"] if i["id"] == IID)
        con = inst.get("connectivity") or {}
    except Exception as exc:  # noqa: BLE001
        con = {"state": f"{type(exc).__name__}"}
    g["itens"]["conectividade_healthy"] = {"ok": con.get("state") == "healthy", "detalhe": con.get("state")}
    g["itens"]["foco_no_launcher"] = {"ok": True, "detalhe": "lido depois de abrir o app"}
    g["falhas"] = [n for n, i in g["itens"].items() if not i["ok"]]
    g["ok"] = not g["falhas"]
    return g


# ---------------------------------------------------------------------------------------------------- o teste
def rodar_ui(amb: Ambiente, run: Path, reg: tile.Registro, observar_s: float = OBSERVAR_S) -> dict[str, Any]:
    r: dict[str, Any] = {"iid": IID, "inicio": iso(amb.agora()), "toques": []}
    marcador = run / MARCADOR
    if marcador.exists():
        r["parada"] = "SEGUNDO_TOQUE_RECUSADO"
        return r
    g = precondicoes(amb, run)
    r["gate"] = {"ok": g["ok"], "falhas": g["falhas"], "base": g["base"]}
    reg("gate", ok=g["ok"], falhas=g["falhas"])
    if not g["ok"]:
        r["parada"] = "UI_START_BLOCKED"
        return r
    r["servicos_antes_de_abrir"] = servicos(amb)
    try:
        rc, out, err = amb.shell(CMD_ABRIR, 30)
        reg("abrir", rc=rc, saida=out.strip()[:200], erro=err.strip()[:200])
        amb.dormir(5.0)
        arvore = amb.hierarquia()
        r["foco"] = amb.shell(CMD_FOCO, 20)[1].strip()[:200]
        r["servicos_apos_abrir"] = servicos(amb)
        r["textos_antes"] = textos(arvore)
        r["perfil_r2_selecionado"] = perfil_selecionado(arvore)
        start = achar_botao(arvore, "Start")
        r["botao_start"] = start
        reg("arvore", perfil_r2=r["perfil_r2_selecionado"], start=start)
        if not r["perfil_r2_selecionado"] or start is None:
            r["parada"] = "UI_START_BLOCKED_ARVORE"
            return r
        est0 = tile.ler_estado(amb, pesada=True)
        if est0.get("tun") or est0.get("vpn"):
            r["parada"] = "UI_START_BLOCKED_TUN_JA_NO_AR"
            return r
        marcador.write_text(iso(), encoding="utf-8")                              # ANTES do toque: não há segunda chance
        t_toque = amb.agora()
        rc, out, err = amb.shell(tocar(start["x"], start["y"]), 20)
        r["toques"].append({"alvo": "Start", "ts": iso(t_toque), "rc": rc, "erro": err.strip()[:200]})
        reg("toque", alvo="Start", rc=rc)
        obs = tile.observar(amb, observar_s, reg)
        r["observacao"] = {k: obs[k] for k in ("inicio", "fim", "leituras", "tun_visto", "primeiro_tun_apos_s", "processo_depois",
                                              "stopped_depois", "tun_depois", "vpn_depois", "pid_ui_depois")}
        arvore2 = amb.hierarquia()
        r["textos_depois"] = textos(arvore2)
        r["servicos_depois"] = servicos(amb)
        r["botao_stop"] = achar_botao(arvore2, "Stop")
        reg("depois", tun=obs["tun_depois"], vpn=obs["vpn_depois"], classes=r["servicos_depois"]["classes"],
            primeiro_plano=r["servicos_depois"]["primeiro_plano"], stop=r["botao_stop"])
        r["subiu"] = bool(obs["tun_depois"] or obs["vpn_depois"] or r["servicos_depois"]["primeiro_plano"])
        if r["subiu"]:
            stop = r["botao_stop"]
            if stop is not None:                                                  # o mecanismo normal da UI
                t_stop = amb.agora()
                rc, out, err = amb.shell(tocar(stop["x"], stop["y"]), 20)
                r["toques"].append({"alvo": "Stop", "ts": iso(t_stop), "rc": rc, "erro": err.strip()[:200]})
                reg("toque", alvo="Stop", rc=rc)
                fim = amb.agora() + PARAR_S
                while amb.agora() < fim:
                    amb.dormir(4.0)
                    if tile.ler_leve(amb)["tun"] is False:
                        break
            else:
                reg("sem_botao_stop")
    except Exception as exc:  # noqa: BLE001 - o rollback roda de qualquer jeito
        r["erro"] = f"{type(exc).__name__}: {exc}"[:300]
        reg("erro", erro=r["erro"])
    finally:
        amb.shell(CMD_HOME, 20)
        r["rollback"] = fechar(amb, reg)
    r["fim"] = iso(amb.agora())
    return r


def fechar(amb: Ambiente, reg: tile.Registro) -> dict[str, Any]:
    """Linha de base de novo: se o tun0 persistiu depois do Stop da UI, o rollback do tile (extraordinário, registrado)."""
    est = tile.ler_estado(amb, pesada=True)
    extraordinario = None
    if est.get("tun") or est.get("vpn"):
        extraordinario = tile.rollback(amb, bool(est.get("tile")), reg)
        est = tile.ler_estado(amb, pesada=True)
    base = {k: est.get(k) for k in ("adb", "uid", "always_on", "lockdown", "tun", "vpn", "tile", "pid_sfa", "stopped", "regras")}
    return {"estado": base, "extraordinario": extraordinario,
            "ok": (est.get("adb") is True and est.get("always_on") == "null" and est.get("lockdown") == "0"
                   and est.get("tun") is False and est.get("vpn") is False and not est.get("tile"))}


# ---------------------------------------------------------------------------------------------------- CLI
def plano(observar_s: float) -> dict[str, Any]:
    return {"instancia": IID, "modo": "plano (nenhuma chamada)", "observar_s": observar_s,
            "passos": ["gate: precondições do tile + conectividade healthy (leitura)",
                       f"abrir: {CMD_ABRIR}", "ler a árvore da UI: perfil r2 selecionado e botão Start (leitura)",
                       "UM toque no Start", f"observar {observar_s:.0f} s (tun0, VPN, pids, ServiceRecord)",
                       "se subiu: UM toque no Stop; senão nada", "HOME e releitura da linha de base"],
            "escritas_possiveis": [CMD_ABRIR, CMD_HOME, "input tap <Start>", "input tap <Stop>"]}


def main(argv: list[str] | None = None, amb: Ambiente | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plano", help="mostra o que faria")
    p.add_argument("--observar-s", type=float, default=OBSERVAR_S)
    u = sub.add_parser("ui", help="o teste (seguro por padrão: sem --execute é o plano)")
    u.add_argument("--execute", action="store_true")
    u.add_argument("--instance", default=None)
    u.add_argument("--run", default=None, help="pasta do coletor (obrigatória com --execute)")
    u.add_argument("--observar-s", type=float, default=OBSERVAR_S)
    a = ap.parse_args(argv)
    if a.observar_s < 30.0:
        print(f"recusado: a janela de observação mínima é 30 s (recebido {a.observar_s})", file=sys.stderr)
        return 2
    if a.cmd == "plano" or not a.execute:
        print(json.dumps(plano(a.observar_s), ensure_ascii=False, indent=2))
        return 0
    if a.instance != IID:
        print(f"recusado: só o {IID} (recebido {a.instance!r})", file=sys.stderr)
        return 2
    if not a.run or not Path(a.run).is_dir():
        print("recusado: --run <pasta do coletor> é obrigatório e precisa existir", file=sys.stderr)
        return 2
    run = Path(a.run)
    amb = amb or Ambiente()
    reg = tile.Registro(run / "acionador-ui-start.jsonl")
    r = rodar_ui(amb, run, reg, a.observar_s)
    (run / "ui-start-09.saida.json").write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if r.get("parada") == "SEGUNDO_TOQUE_RECUSADO":
        return 5
    if str(r.get("parada", "")).startswith("UI_START_BLOCKED"):
        return 3
    return 0 if r.get("rollback", {}).get("ok") else 4


if __name__ == "__main__":
    raise SystemExit(main())
