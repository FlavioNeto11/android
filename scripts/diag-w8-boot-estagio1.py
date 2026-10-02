#!/usr/bin/env python
"""Estágio 1 (K × R) do W8 no android-09 (docs/handoffs/w8-boot-recovery.md §15 e §17): o `force-stop` efêmero é necessário para o
SILENT_STOP, com o BootReceiver no caminho dos DOIS braços?

  R  `os+receiver`: always-on, UI Start (tun no ar), HOME, restart. BootReceiver entregue, SEM force-stop.
  K  `os+stopped` : igual a R + `am force-stop` ~3 s antes do restart (o E2).

Este script SÓ orquestra: cada boot é o `diag-w8-boot.py::rodar` de sempre (mesmo vocabulário de escrita, mesmo `desfazer`), numa
subpasta própria. O que ele acrescenta é o que o dono pediu para o estágio: a ORDEM PRÉ-COMPROMETIDA por semente (calculada daqui,
sem olhar resultado), o baseline por boot, a categoria FECHADA por boot e a REGRA DE PARADA aplicada depois de cada boot.

SEGURO POR PADRÃO: sem --execute só imprime o plano. Só o android-09. Não manipula rede, não toca outro aparelho, não implementa correção.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _carregar(nome: str, arquivo: str):
    spec = importlib.util.spec_from_file_location(nome, ROOT / "scripts" / arquivo)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)                                                  # type: ignore[union-attr]
    return mod


boot = _carregar("diag_w8_boot_estagio1_base", "diag-w8-boot.py")
obs = boot.obs
tile = boot.tile
IID = boot.IID

SEED = "w8-boot-estagio1-20261002"                                                # registrada no doc ANTES do 1º boot
BRACOS = {"K": "os+stopped", "R": "os+receiver"}
BLOCOS = 3                                                                        # o 3º só roda se nada parou nos 4 primeiros
MAX_BOOTS = 6
CATEGORIAS = ("TUN_OK", "SILENT_STOP", "ANR_OU_CRASH", "PROCESS_KILLED", "BOOT_INVALID", "UNKNOWN")
FALHAS = ("SILENT_STOP", "ANR_OU_CRASH", "PROCESS_KILLED")
HEARTBEAT_MAX_S = 45.0
# Mapeamento FIXADO antes de rodar (códigos de `obs.classificar` -> categoria fechada). Nada fora daqui vira categoria nova.
MAPA_CATEGORIA = {"TUN_OK": "TUN_OK", "SILENT_STOP": "SILENT_STOP", "CRASH": "ANR_OU_CRASH"}


def ordem(seed: str = SEED, blocos: int = BLOCOS) -> list[dict[str, Any]]:
    """A ordem K/R de cada bloco, determinística e sem olhar resultado: o primeiro bit do sha256 de `seed:bloco` (0 = K antes de R)."""
    r: list[dict[str, Any]] = []
    for b in range(1, blocos + 1):
        bit = hashlib.sha256(f"{seed}:{b}".encode()).digest()[0] & 1
        par = ("K", "R") if bit == 0 else ("R", "K")
        for i, arm in enumerate(par):
            r.append({"bloco": b, "braco": arm, "ensaio": BRACOS[arm], "indice": (b - 1) * 2 + i + 1})
    return r


def categoria(resumo: dict[str, Any]) -> str:
    """UMA das seis categorias fechadas. ANR/crash -> ANR_OU_CRASH; kill/morte sem ANR/crash -> PROCESS_KILLED; o resto que não é
    TUN_OK nem SILENT_STOP (serviço sem foreground, FGS sem tun, sem always-on, indeterminado) -> UNKNOWN."""
    a = resumo["assinatura"]
    cod = a["codigo"]
    p = resumo["processo"]
    if cod in MAPA_CATEGORIA:
        return MAPA_CATEGORIA[cod]
    if cod == "ANR_OU_KILL":
        return "ANR_OU_CRASH" if (p["anr"] or p["crash"]) else "PROCESS_KILLED"
    return "UNKNOWN"


def rede_na_partida(resumo: dict[str, Any]) -> str:
    """Transporte da rede padrão no instante em que o processo do serviço nasceu (`NONE_YET` se ainda não havia)."""
    proc = (resumo["servico"].get("proc_start") or {}).get("t")
    if not proc:
        return "SEM_PROCESSO"
    atual = "NONE_YET"
    for t, transporte in resumo["rede"].get("trocas_da_rede_padrao", []):
        if obs.segundos(t) <= obs.segundos(proc):
            atual = transporte
    return atual


def registro(slot: dict[str, Any], resumo: dict[str, Any], saida: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """A linha da tabela (campos do pedido). `PROVED`/`OBSERVED` = lido de log/estado; `INFERRED` está marcado no próprio campo."""
    cat = categoria(resumo)
    s, p, a = resumo["servico"], resumo["processo"], resumo["assinatura"]
    troca = resumo["troca_padrao_durante_o_inicio"]
    pids = p["pids"]
    pk = (resumo.get("pacote_no_boot") or {}).get("primeiro_adb") or {}
    br = resumo.get("boot_receiver") or {}
    fgs_t = (s.get("fgs_start") or {}).get("t")
    return {
        "BLOCK": slot["bloco"], "ARM": slot["braco"], "ORDER_INDEX": slot["indice"], "ENSAIO": slot["ensaio"], "RESULT": cat,
        "CODIGO_INTERNO": a["codigo"],
        "STOPPED_FIRST_ADB": pk.get("stopped"),
        "BOOT_RECEIVER": br.get("estado"), "BOOT_RECEIVER_ENTREGUE_EM": br.get("entregue_em"), "BOOT_RECEIVER_TERMINOU_EM": br.get("terminou_em"),
        "SECOND_START": extra.get("segunda_partida"),
        "FIRST_NETWORK": (resumo["rede"].get("primeira") or {}).get("transporte"),
        "DEFAULT_NETWORK_AT_SERVICE_START": rede_na_partida(resumo),
        "NETWORK_SWITCH": [f"{t['de']}->{t['para']}@{t['t']}" for t in troca],
        "NETWORK_SWITCH_DURING_WINDOW": bool(troca),
        "TIME_FROM_NETWORK_SWITCH_TO_FGS": obs.atraso(troca[0]["t"], fgs_t) if troca and fgs_t else None,
        "FGS_START": fgs_t, "FGS_STOP": (s.get("fgs_stop") or {}).get("t"), "STOP_DELTA": a.get("fgs_duracao_s"),
        "NOTIFICATION_CANCEL_REASON": (s.get("notificacao_cancelada") or [{}])[0].get("razao"),
        "PID_ALIVE_AFTER_STOP": (len(pids) == 1 and p["mudancas_de_pid"] == 0 and not p["kill"] and not p["died"]) if a.get("fgs_duracao_s") else None,
        "PID_EXIT": {"anr": p["anr"], "crash": p["crash"], "kill": p["kill"], "died": p["died"], "pids": pids},
        "TUN0": bool(a.get("tun")), "T_TUN": (resumo["amostras"].get("t_tun") or {}).get("t"),
        "ORDEM_DAS_REDES": resumo["rede"]["ordem"],
        "BASELINE_FINAL_OK": (saida.get("desfazer") or {}).get("ok"),
    }


def avaliar_parada(regs: list[dict[str, Any]]) -> tuple[str, str] | None:
    """A regra de parada do pedido, aplicada ao ÚLTIMO boot (e, com 4 boots, ao conjunto). Devolve (código, motivo) ou None."""
    u = regs[-1]
    cat, arm, troca = u["RESULT"], u["ARM"], u["NETWORK_SWITCH_DURING_WINDOW"]
    if cat in ("BOOT_INVALID", "UNKNOWN"):
        return "BOOT_INVALIDO_OU_INCERTO", f"boot {u['ORDER_INDEX']} terminou em {cat}: parar, sem retry"
    if cat in ("ANR_OU_CRASH", "PROCESS_KILLED"):
        return "COMPORTAMENTO_INESPERADO", f"boot {u['ORDER_INDEX']} terminou em {cat} (não é a assinatura em estudo): parar"
    if arm == "R" and cat == "SILENT_STOP":
        return "R_SILENT_STOP", "R (sem force-stop) produziu SILENT_STOP: o force-stop NÃO é necessário"
    if arm == "K" and cat == "TUN_OK":
        return "K_TUN_OK", "K (com force-stop) produziu TUN_OK: o force-stop NÃO é suficiente"
    if cat == "SILENT_STOP" and not troca:
        return "FALHA_SEM_TROCA_DE_REDE", "falha SEM troca de transporte da rede padrão na janela: 'troca de rede é causa necessária' falsificada"
    if cat == "TUN_OK" and troca:
        return "TUN_OK_COM_TROCA_DE_REDE", "TUN_OK COM troca de rede na janela: 'troca de rede é causa suficiente' falsificada"
    if len(regs) >= 4 and len({r["RESULT"] for r in regs[:4]}) == 1:
        return "QUATRO_IGUAIS", f"os quatro primeiros boots deram a mesma classe ({regs[0]['RESULT']})"
    return None


def baseline(amb: Any) -> dict[str, Any]:
    """O baseline por boot: o gate do tile (worker conectado e `up`, aparelho online e QA, sem comando aberto, sem tun0, sem peer, adb, always-on
    null e lockdown 0) MAIS o heartbeat do worker recente. O worker pode estar `degraded` SÓ pelo relógio do notebook (+8 s, conhecido,
    `CLOCK_SYNC_REQUIRED=false`); qualquer outra degradação invalida."""
    g = boot.gate(amb)
    falhas = list(g["falhas"])
    snap = amb.snapshot()
    wk = next((w for w in snap.get("workers", []) if w.get("id") == tile.WORKER), {}) or {}
    visto = wk.get("last_seen_at")
    idade = None
    if visto:
        idade = (datetime.now(timezone.utc) - datetime.fromisoformat(visto.replace("Z", "+00:00"))).total_seconds()
    if idade is None or idade > HEARTBEAT_MAX_S:
        falhas.append("heartbeat_do_worker")
    estado, detalhe = wk.get("state"), str(wk.get("state_detail") or "")
    so_relogio = estado == "degraded" and bool(re.search(r"rel[óo]gio desalinhado", detalhe)) and ";" not in detalhe
    if estado != "healthy" and not so_relogio:
        falhas.append("worker_degradado_por_outra_causa")
    srv = amb.servidor()
    wg = hashlib.sha256(json.dumps(srv, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return {"ok": not falhas, "falhas": falhas, "gate": g["base"], "worker": {"estado": estado, "detalhe": detalhe, "so_relogio": so_relogio,
            "heartbeat_idade_s": None if idade is None else round(idade, 1)}, "wg_servidor_digest": wg}


def plano_json() -> dict[str, Any]:
    return {"instancia": IID, "estagio": 1, "seed": SEED, "algoritmo": "bit0(sha256(f'{seed}:{bloco}')) == 0 -> K antes de R",
            "ordem": ordem(), "max_boots": MAX_BOOTS, "terceiro_bloco": "só se NENHUMA parada disparou nos 4 primeiros boots",
            "categorias": CATEGORIAS, "mapa_categoria": {**MAPA_CATEGORIA, "ANR_OU_KILL": "ANR_OU_CRASH se anr/crash, senão PROCESS_KILLED",
                                                         "demais": "UNKNOWN"},
            "paradas": ["R_SILENT_STOP", "K_TUN_OK", "FALHA_SEM_TROCA_DE_REDE", "TUN_OK_COM_TROCA_DE_REDE", "BOOT_INVALIDO_OU_INCERTO",
                        "COMPORTAMENTO_INESPERADO", "QUATRO_IGUAIS"],
            "troca_de_rede_relevante": "troca de TRANSPORTE da rede padrão entre o início do processo do serviço e 5 s depois do fim do FGS "
                                       "(ou do seu começo, sem fim)",
            "nao_faz": ["estágio 2 (os+uistop)", "manipular rede", "outro aparelho", "clear-data/reset/reinstalar", "peer/WireGuard", "correção"]}


def rodar_estagio(amb: Any, run: Path) -> dict[str, Any]:
    plano = ordem()
    saida: dict[str, Any] = {"seed": SEED, "ordem": plano, "inicio": boot.iso(), "boots": [], "parada": None}
    wg_antes = None
    regs: list[dict[str, Any]] = []
    for slot in plano:
        if slot["bloco"] == 3 and (len(regs) < 4 or saida["parada"]):
            break
        d = run / f"b{slot['indice']}-{slot['braco']}"
        d.mkdir(parents=True, exist_ok=False)
        (d / "slot.json").write_text(json.dumps(slot), encoding="utf-8")
        reg = tile.Registro(d / "acionador-estagio1.jsonl")
        try:
            b = baseline(amb)
            (d / "baseline.json").write_text(json.dumps(b, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            wg_antes = wg_antes or b["wg_servidor_digest"]
            if not b["ok"]:
                regs.append({"BLOCK": slot["bloco"], "ARM": slot["braco"], "ORDER_INDEX": slot["indice"], "RESULT": "BOOT_INVALID",
                             "NETWORK_SWITCH_DURING_WINDOW": False, "motivo": f"baseline: {b['falhas']}"})
                saida["parada"] = {"codigo": "BOOT_INVALIDO_OU_INCERTO", "motivo": f"baseline falhou antes do boot {slot['indice']}: {b['falhas']}"}
                break
            r = boot.rodar(amb, d, slot["ensaio"], reg)
            (d / f"boot-{slot['ensaio']}.saida.json").write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            if r.get("parada") or r.get("erro") or not (r.get("boot") or {}).get("boot_visto"):
                regs.append({"BLOCK": slot["bloco"], "ARM": slot["braco"], "ORDER_INDEX": slot["indice"], "RESULT": "BOOT_INVALID",
                             "NETWORK_SWITCH_DURING_WINDOW": False, "motivo": r.get("parada") or r.get("erro") or "boot não visto"})
            else:
                resumo = obs.resumir(d, slot["ensaio"], r)
                (d / f"boot-{slot['ensaio']}.resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                ev = (d / f"boot-{slot['ensaio']}.events.txt").read_text(encoding="utf-8", errors="replace")
                rg = registro(slot, resumo, r, {"segunda_partida": obs.segunda_partida(ev)})
                if not (r.get("desfazer") or {}).get("ok"):
                    rg["RESULT_ANTES_DO_BASELINE"] = rg["RESULT"]
                    rg["RESULT"] = "BOOT_INVALID"
                    rg["motivo"] = "desfazer não devolveu o baseline"
                regs.append(rg)
        except Exception as exc:  # noqa: BLE001 - qualquer imprevisto invalida o boot e PARA (sem retry)
            regs.append({"BLOCK": slot["bloco"], "ARM": slot["braco"], "ORDER_INDEX": slot["indice"], "RESULT": "BOOT_INVALID",
                         "NETWORK_SWITCH_DURING_WINDOW": False, "motivo": f"{type(exc).__name__}: {exc}"[:300]})
        saida["boots"] = regs
        (run / "estagio1.estado.json").write_text(json.dumps(saida, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        p = avaliar_parada(regs)
        if p:
            saida["parada"] = {"codigo": p[0], "motivo": p[1]}
            break
    if not saida["parada"]:
        saida["parada"] = {"codigo": "TETO" if len(regs) >= MAX_BOOTS else "FIM_SEM_PARADA", "motivo": f"{len(regs)} boots executados"}
    try:
        saida["wg_servidor_digest"] = {"antes": wg_antes, "depois": hashlib.sha256(json.dumps(amb.servidor(), sort_keys=True, default=str).encode()).hexdigest()[:16]}
    except Exception as exc:  # noqa: BLE001
        saida["wg_servidor_digest"] = {"antes": wg_antes, "erro": str(exc)[:100]}
    saida["fim"] = boot.iso()
    saida["boots"] = regs
    (run / "estagio1.estado.json").write_text(json.dumps(saida, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return saida


def main(argv: list[str] | None = None, amb: Any = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--instance", default=None)
    ap.add_argument("--run", default=None, help="pasta de saída (obrigatória com --execute; precisa existir e estar vazia)")
    a = ap.parse_args(argv)
    if not a.execute:
        print(json.dumps(plano_json(), ensure_ascii=False, indent=2))
        return 0
    if a.instance != IID:
        print(f"recusado: só o {IID} (recebido {a.instance!r})", file=sys.stderr)
        return 2
    if not a.run or not Path(a.run).is_dir() or any(Path(a.run).iterdir()):
        print("recusado: --run <pasta> precisa existir e estar VAZIA (um estágio por pasta, sem retomar)", file=sys.stderr)
        return 2
    amb = amb or boot.Ambiente()
    r = rodar_estagio(amb, Path(a.run))
    print(json.dumps({"parada": r["parada"], "boots": [{k: b.get(k) for k in ("ORDER_INDEX", "BLOCK", "ARM", "RESULT")} for b in r["boots"]]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
