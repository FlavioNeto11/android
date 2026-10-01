#!/usr/bin/env python
"""Acionador DIAGNÓSTICO do W8 (Fase A, "tile isolado") — SÓ o android-09. docs/handoffs/w8-diagnostico-android09.md §8.

Separado de `scripts/diag-w8.py` de propósito: aquele coletor é somente leitura e continua sendo; este é o ÚNICO que
escreve no aparelho, e escreve pouco. SEGURO POR PADRÃO: sem `--execute` só mostra o plano (nenhuma chamada ao aparelho,
ao central ou ao disco). Para executar exige `--execute --instance android-09` (qualquer outro id, lista ou curinga é
recusado) e um coletor vivo gravando em `--run`.

O que ele pode mandar ao aparelho (e nada além disso; o teste `test_vocabulario_de_escrita` confere):
  - `cmd statusbar remove-tile|add-tile|click-tile io.nekohasekai.sfa/.bg.TileService`  (o gesto do produto);
  - `am force-stop io.nekohasekai.sfa`  (fase a2 e, só se o tile não desligar o túnel, o rollback).
Sem `network/assign`, `verify`, `reapply`, peer, servidor WireGuard, reinício, `settings put`, `svc`, `pm`, nem outro aparelho.

Fases (uma por execução; cada uma refaz as precondições do zero):
  a1  o gesto do produto SEM force-stop: add-tile → P1==P2, Q>0, T==0 → click-tile, com stdout/stderr/exit code do
      clique CAPTURADOS (o produto os descarta) e o estado observado em silêncio por >= 30 s;
  a2  só se a a1 subiu o tun0 (`--apos-a1`): `am force-stop` → processo ausente → o MESMO gesto → mesma observação.
Rollback sempre (try/finally): túnel desligado só neste aparelho (tile de novo; `force-stop` se o tun0 persistir), tile
devolvido ao Q0 original, e a releitura que prova always-on=null, lockdown=0, sem tun0, sem VPN CONNECTED.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _carregar_coletor():
    spec = importlib.util.spec_from_file_location("diag_w8_coletor", ROOT / "scripts" / "diag-w8.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["diag_w8_coletor"] = mod
    spec.loader.exec_module(mod)                                                  # type: ignore[union-attr]
    return mod


coletor = _carregar_coletor()
PACOTE = coletor.PACOTE
TILE = f"{PACOTE}/.bg.TileService"
IID = "android-09"
SERIAL = coletor.SERIAL
WORKER = "worker-lan-01"
OBSERVAR_MIN_S = 30.0
FINAIS_CMD = ("succeeded", "failed", "rejected", "uncertain")
FINAIS_RUN = ("completed", "completed_with_issues", "failed", "cancelled")

CMD_FORCE_STOP = f"am force-stop {PACOTE}"
CMD_CLICAR = f"cmd statusbar click-tile {TILE}; echo CK_RC=$?"
CMD_REMOVER = f"cmd statusbar remove-tile {TILE}"
CMD_ADICIONAR = f"cmd statusbar add-tile {TILE}"
CMD_LEVE_UNICA = (f"echo T=$(ip -o addr show tun0 2>/dev/null | grep -c inet); echo PS=$(pidof {PACOTE}); "
                  "echo PU=$(pidof com.android.systemui)")

# O trecho do produto que muda: o clique sem saída e sem código de retorno. Tudo o mais é o comando do produto, byte a byte.
_CLIQUE_DO_PRODUTO = f"cmd statusbar click-tile {TILE} >/dev/null 2>&1; echo CLICOU=1"
_CLIQUE_CAPTURADO = (f"echo CK_BEGIN; echo CK_BEGIN >&2; CK_T0=$EPOCHREALTIME; cmd statusbar click-tile {TILE}; CK_RC=$?; "
                     "CK_T1=$EPOCHREALTIME; echo CK_END; echo CK_END >&2; echo CK_RC=$CK_RC; echo CK_T0=$CK_T0; "
                     "echo CK_T1=$CK_T1; echo CLICOU=1")


def agora() -> float:
    return time.time()


def iso(t: float | None = None) -> str:
    return coletor.iso(agora() if t is None else t)


def comando_do_produto() -> str:
    """`comando_de_religar` do produto, importado (não copiado): se o produto mudar, o acionador deixa de reproduzi-lo."""
    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from app.devices.rede_aplicacao import comando_de_religar                  # noqa: PLC0415
    finally:
        sys.path.pop(0)
    return comando_de_religar(TILE)


def comando_do_gesto(produto: str | None = None) -> str:
    """O gesto do produto com UMA diferença: o clique devolve stdout, stderr e exit code (e o instante, no aparelho)."""
    base = produto if produto is not None else comando_do_produto()
    if base.count(_CLIQUE_DO_PRODUTO) != 1:
        raise RuntimeError("o comando do produto mudou: o trecho do clique não é mais o esperado; revise o acionador")
    return base.replace(_CLIQUE_DO_PRODUTO, _CLIQUE_CAPTURADO)


# ---------------------------------------------------------------------------------------------------- parsers
_PAR = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def pares(texto: str) -> dict[str, str]:
    v: dict[str, str] = {}
    for linha in (texto or "").splitlines():
        m = _PAR.match(linha.strip())
        if m:
            v[m.group(1)] = m.group(2).strip()
    return v


def bloco(texto: str, ini: str = "CK_BEGIN", fim: str = "CK_END") -> str:
    """O que veio entre os marcadores (o stdout ou o stderr do clique, separados pelo adb)."""
    dentro, linhas = False, []
    for linha in (texto or "").splitlines():
        s = linha.strip()
        if s == ini:
            dentro = True
        elif s == fim:
            dentro = False
        elif dentro:
            linhas.append(linha.rstrip())
    return "\n".join(linhas)


def ler_gesto(rc: int, out: str, err: str, duracao_s: float) -> dict[str, Any]:
    v = pares(out)
    t0, t1 = v.get("CK_T0"), v.get("CK_T1")
    try:
        dur_aparelho = round(float(t1) - float(t0), 4) if t0 and t1 else None
    except ValueError:
        dur_aparelho = None
    return {"adb_rc": rc, "Q0": v.get("Q0"), "P1": v.get("P1"), "P2": v.get("P2"), "Q": v.get("Q"), "T": v.get("T"),
            "CLICOU": v.get("CLICOU"), "click_exit": v.get("CK_RC"), "click_stdout": bloco(out), "click_stderr": bloco(err),
            "click_duracao_aparelho_s": dur_aparelho, "gesto_duracao_host_s": round(duracao_s, 3),
            "saida_bruta": out[-1500:], "erro_bruto": err[-600:]}


# ---------------------------------------------------------------------------------------------------- ambiente
class Ambiente:
    """Tudo o que toca o mundo: aparelho, central, disco. Os testes trocam isto por um falso."""

    def __init__(self) -> None:
        self.adb = coletor.achar_adb(None)
        if self.adb is None:
            raise RuntimeError("adb não encontrado")

    def shell(self, cmd: str, timeout: float = 60) -> tuple[int, str, str]:
        try:
            r = subprocess.run([self.adb, "-s", SERIAL, "shell", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, timeout=timeout, encoding="utf-8", errors="replace")
            return r.returncode, r.stdout, r.stderr
        except (subprocess.TimeoutExpired, OSError) as exc:
            return 255, "", f"ERRO {type(exc).__name__}: {exc}"

    def adb_devices(self) -> str:
        return coletor._executa([self.adb, "devices"], 15)[1]

    def snapshot(self) -> dict:
        return coletor.get_json(f"{coletor.API}/snapshot", 30)

    def servidor(self) -> dict:
        return coletor.get_json(f"{coletor.API}/network/server", 15)

    def sql(self, consulta: str, params: tuple = ()) -> list[tuple]:
        con = sqlite3.connect(f"file:{(ROOT / 'data' / 'poc.sqlite3').as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            return [tuple(r) for r in con.execute(consulta, params)]
        finally:
            con.close()

    def logcat_vivo(self) -> tuple[bool, str]:
        """Há um `adb logcat` do coletor para este serial? (o arquivo pode estar vazio num aparelho parado)."""
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='adb.exe'\" | Where-Object { $_.CommandLine -match 'logcat' "
              f"-and $_.CommandLine -match '{re.escape(SERIAL)}' }} | Select-Object -ExpandProperty ProcessId")
        rc, out = coletor._executa(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], 30)
        pids = [x for x in out.split() if x.isdigit()]
        return bool(pids), f"adb logcat pid(s) {','.join(pids) or '-'}"

    def arquivo_recente(self, caminho: Path, max_s: float) -> tuple[bool, str]:
        try:
            idade = agora() - caminho.stat().st_mtime
        except OSError as exc:
            return False, f"{caminho.name}: {exc}"
        return idade <= max_s, f"{caminho.name} atualizado há {idade:.1f} s"

    def dormir(self, s: float) -> None:
        time.sleep(s)

    def agora(self) -> float:
        return agora()


# ---------------------------------------------------------------------------------------------------- leitura do aparelho
def ler_estado(amb: Ambiente, *, pesada: bool) -> dict[str, Any]:
    cmd = coletor.CMD_LEVE + ("; " + coletor.CMD_PESADO if pesada else "")
    rc, out, _ = amb.shell(cmd, 60)
    return coletor.amostra_do_aparelho(out if rc == 0 else "", amb.agora(), pesada=pesada)


def ler_leve(amb: Ambiente) -> dict[str, Any]:
    rc, out, _ = amb.shell(CMD_LEVE_UNICA, 30)
    v = coletor.parse_pares(out if rc == 0 else "")
    return {"t": amb.agora(), "ts": iso(amb.agora()), "ok": rc == 0 and "T" in v,
            "tun": (coletor._int(v.get("T")) or 0) > 0 if "T" in v else None,
            "pid_sfa": coletor._pid(v.get("PS")), "pid_ui": coletor._pid(v.get("PU"))}


# ---------------------------------------------------------------------------------------------------- precondições
def precondicoes(amb: Ambiente, run: Path | None) -> dict[str, Any]:
    """Todas por LEITURA. Qualquer falha = BLOCKED, e nada é escrito. Devolve também a linha de base (Q0, stopped, pids)."""
    itens: dict[str, dict[str, Any]] = {}

    def item(nome: str, ok: bool, detalhe: Any) -> None:
        itens[nome] = {"ok": bool(ok), "detalhe": detalhe}

    try:
        snap = amb.snapshot()
        inst = next((i for i in snap.get("instances", []) if i.get("id") == IID), None)
        wk = next((w for w in snap.get("workers", []) if w.get("id") == WORKER), None)
    except Exception as exc:  # noqa: BLE001
        snap, inst, wk = {}, None, None
        item("central_responde", False, f"{type(exc).__name__}: {exc}"[:200])
    item("instancia_online", bool(inst) and inst.get("state") == "online", (inst or {}).get("state"))
    item("aparelho_qa_sem_conta_real", bool(inst) and str(inst.get("account_label") or "").lower().startswith("qa")
         and not inst.get("locked_account"), {"account_label": (inst or {}).get("account_label"),
                                              "locked_account": (inst or {}).get("locked_account")})
    item("sem_execucao_ativa_no_snapshot", bool(inst) and inst.get("current") is None and inst.get("control") in (None, "none"),
         {"current": (inst or {}).get("current"), "control": (inst or {}).get("control")})
    item("worker_conectado", bool(wk) and wk.get("connected") is True and wk.get("transport_state") == "up"
         and (inst or {}).get("worker_id") == WORKER,
         {"connected": (wk or {}).get("connected"), "transport": (wk or {}).get("transport_state"),
          "estado_do_worker": (wk or {}).get("state"), "manutencao": (wk or {}).get("maintenance")})
    try:
        linha = amb.sql("SELECT instance_id, state FROM device_network WHERE instance_id=?", (IID,))
        item("sem_linha_de_rede", not linha, linha)
        abertos = amb.sql(f"SELECT id, verb, state FROM commands WHERE instance_id=? AND state NOT IN "
                          f"({','.join('?' * len(FINAIS_CMD))})", (IID, *FINAIS_CMD))
        item("sem_comando_aberto", not abertos, abertos)
        item("sem_restart_ou_device_network_aberto", not [r for r in abertos if r[1] in ("restart", "device.network")], abertos)
        runs = amb.sql(f"SELECT id, status FROM runs WHERE status NOT IN ({','.join('?' * len(FINAIS_RUN))}) "
                       "AND instance_ids LIKE ?", (*FINAIS_RUN, f"%{IID}%"))
        item("sem_execucao_ativa_no_banco", not runs, runs)
        trav = amb.sql("SELECT id, handle FROM device_locked_accounts WHERE instance_id=? AND resolved_at IS NULL", (IID,))
        item("sem_conta_travada", not trav, trav)
    except Exception as exc:  # noqa: BLE001
        item("banco_ro_responde", False, f"{type(exc).__name__}: {exc}"[:200])
    try:
        srv = amb.servidor()
        item("sem_peer_no_servidor", not [p for p in srv.get("peers", []) if p.get("instance_id") == IID],
             {"peers": [p.get("instance_id") for p in srv.get("peers", [])]})
    except Exception as exc:  # noqa: BLE001
        item("servidor_responde", False, f"{type(exc).__name__}: {exc}"[:200])
    dev = amb.adb_devices()
    item("adb_online", bool(re.search(rf"^{re.escape(SERIAL)}\s+device\b", dev, re.M)), dev.strip()[-120:])
    est = ler_estado(amb, pesada=True)
    item("adb_le_como_shell", est.get("adb") is True and est.get("uid") == 2000, {"uid": est.get("uid")})
    item("always_on_null", est.get("always_on") == "null", est.get("always_on"))
    item("lockdown_0", est.get("lockdown") == "0", est.get("lockdown"))
    item("tun0_ausente", est.get("tun") is False, est.get("tun"))
    item("vpn_nao_conectada", est.get("vpn") is False, est.get("vpn"))
    if run is None:
        item("coletor_gravando", False, "sem --run")
    else:
        ok_s, det_s = amb.arquivo_recente(run / "samples.jsonl", 20.0)
        ok_l, det_l = amb.logcat_vivo()
        item("coletor_gravando", ok_s and ok_l and (run / "meta.json").exists(), f"{det_s}; {det_l}")
    falhas = [n for n, i in itens.items() if not i["ok"]]
    base = {"tile_presente_q0": bool(est.get("tile")), "stopped": est.get("stopped"), "pid_sfa": est.get("pid_sfa"),
            "pid_ui": est.get("pid_ui"), "uptime_s": est.get("uptime")}
    return {"ok": not falhas, "falhas": falhas, "itens": itens, "base": base}


# ---------------------------------------------------------------------------------------------------- a fase
class Registro:
    def __init__(self, arquivo: Path | None) -> None:
        self.arquivo = arquivo
        self.eventos: list[dict] = []

    def __call__(self, nome: str, **dados: Any) -> None:
        e = {"ts": iso(), "evento": nome, **dados}
        self.eventos.append(e)
        print(json.dumps(e, ensure_ascii=False, default=str), file=sys.stderr)
        if self.arquivo is not None:
            with self.arquivo.open("a", encoding="utf-8") as f:
                f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")


def observar(amb: Ambiente, observar_s: float, reg: Registro) -> dict[str, Any]:
    """Depois do clique: NADA de escrita. Leituras leves (tun0, pids) a cada ~4 s; ao fim uma leitura pesada."""
    t_ini = amb.agora()
    amostras: list[dict] = []
    while amb.agora() - t_ini < observar_s:
        amb.dormir(4.0)
        a = ler_leve(amb)
        amostras.append(a)
        reg("observacao", tun=a["tun"], pid_sfa=a["pid_sfa"], pid_ui=a["pid_ui"], ok=a["ok"])
    fim = ler_estado(amb, pesada=True)
    return {"inicio": iso(t_ini), "fim": iso(amb.agora()), "leituras": len(amostras),
            "tun_visto": any(a["tun"] for a in amostras) or bool(fim.get("tun")),
            "primeiro_tun_apos_s": next((round(a["t"] - t_ini, 1) for a in amostras if a["tun"]), None),
            "processo_depois": fim.get("pid_sfa"), "stopped_depois": fim.get("stopped"), "tun_depois": fim.get("tun"),
            "vpn_depois": fim.get("vpn"), "pid_ui_depois": fim.get("pid_ui"), "estado_fim": fim}


def rollback(amb: Ambiente, q0: bool, reg: Registro) -> dict[str, Any]:
    """Devolve o aparelho: túnel desligado só aqui, tile como estava, e a releitura que prova. Nunca reinicia."""
    r: dict[str, Any] = {"passos": [], "metodo_do_tunel": None, "ok": False}

    def passo(nome: str, **d: Any) -> None:
        r["passos"].append({"passo": nome, "ts": iso(), **d})
        reg("rollback", passo=nome, **d)

    try:
        est = ler_estado(amb, pesada=True)
        if est.get("tun") or est.get("vpn"):
            if est.get("tile"):
                rc, out, err = amb.shell(CMD_CLICAR, 30)
                passo("click-tile (desligar)", rc=rc, saida=out.strip()[:200], erro=err.strip()[:200])
                r["metodo_do_tunel"] = "tile"
                for _ in range(5):
                    amb.dormir(3.0)
                    if not ler_leve(amb)["tun"]:
                        break
            if ler_leve(amb)["tun"] is not False:
                rc, out, err = amb.shell(CMD_FORCE_STOP, 30)
                passo("force-stop (o tun0 persistiu)", rc=rc, erro=err.strip()[:200])
                r["metodo_do_tunel"] = "force-stop" if r["metodo_do_tunel"] is None else "tile+force-stop"
                for _ in range(5):
                    amb.dormir(3.0)
                    if ler_leve(amb)["tun"] is False:
                        break
        else:
            passo("túnel já ausente", tun=est.get("tun"), vpn=est.get("vpn"))
        est = ler_estado(amb, pesada=False)
        if bool(est.get("tile")) != q0:
            cmd = CMD_ADICIONAR if q0 else CMD_REMOVER
            rc, out, err = amb.shell(cmd, 30)
            passo("tile devolvido ao Q0", q0=q0, comando=cmd.split()[2], rc=rc)
            amb.dormir(3.0)
        final = ler_estado(amb, pesada=True)
        r["estado_final"] = {k: final.get(k) for k in ("adb", "uid", "always_on", "lockdown", "tun", "vpn", "tile", "pid_sfa",
                                                      "stopped", "regras", "uptime")}
        r["ok"] = (final.get("adb") is True and final.get("uid") == 2000 and final.get("always_on") == "null"
                   and final.get("lockdown") == "0" and final.get("tun") is False and final.get("vpn") is False
                   and bool(final.get("tile")) == q0)
    except Exception as exc:  # noqa: BLE001 - o rollback relata, não esconde
        r["erro"] = f"{type(exc).__name__}: {exc}"[:300]
        reg("rollback_erro", erro=r["erro"])
    return r


def central_final(amb: Ambiente, esperar_s: float = 60.0) -> dict[str, Any]:
    """O que o central vê no fim: online, sem linha de rede, sem peer, sem comando aberto e a conectividade."""
    r: dict[str, Any] = {}
    fim = amb.agora() + esperar_s
    while True:
        try:
            inst = next(i for i in amb.snapshot()["instances"] if i["id"] == IID)
            r["estado"] = inst.get("state")
            r["conectividade"] = (inst.get("connectivity") or {}).get("state")
            r["conectividade_checada_em"] = (inst.get("connectivity") or {}).get("checked_at")
            r["current"] = inst.get("current")
        except Exception as exc:  # noqa: BLE001
            r["erro_snapshot"] = f"{type(exc).__name__}: {exc}"[:200]
        if r.get("conectividade") == "healthy" or amb.agora() >= fim:
            break
        amb.dormir(10.0)
    try:
        r["linha_de_rede"] = amb.sql("SELECT instance_id FROM device_network WHERE instance_id=?", (IID,))
        r["peer"] = [p.get("instance_id") for p in amb.servidor().get("peers", []) if p.get("instance_id") == IID]
        r["comandos_abertos"] = amb.sql(f"SELECT id, verb, state FROM commands WHERE instance_id=? AND state NOT IN "
                                        f"({','.join('?' * len(FINAIS_CMD))})", (IID, *FINAIS_CMD))
    except Exception as exc:  # noqa: BLE001
        r["erro_central"] = f"{type(exc).__name__}: {exc}"[:200]
    r["ok"] = (r.get("estado") == "online" and not r.get("linha_de_rede") and not r.get("peer")
               and not r.get("comandos_abertos") and "erro_central" not in r)
    return r


def rodar_fase(amb: Ambiente, fase: str, run: Path | None, observar_s: float, reg: Registro) -> dict[str, Any]:
    res: dict[str, Any] = {"fase": fase, "instancia": IID, "inicio": iso(), "estado": "BLOCKED", "mutacoes": 0}
    gate = precondicoes(amb, run)
    res["gate"] = gate
    reg("gate", ok=gate["ok"], falhas=gate["falhas"])
    if not gate["ok"]:
        res["fim"] = iso()
        return res                                                         # nenhuma escrita: nada a desfazer
    base = gate["base"]
    q0 = base["tile_presente_q0"]
    res["baseline"] = base
    res["comparacao_force_stop"] = ("INCONCLUSIVE" if (base["stopped"] or base["pid_sfa"] is None) else "VALIDA")
    res["estado"] = "EM_EXECUCAO"
    try:
        if fase == "a2":
            rc, out, err = amb.shell(CMD_FORCE_STOP, 30)
            res["mutacoes"] += 1
            amb.dormir(3.0)
            apos = ler_estado(amb, pesada=True)
            res["a2_force_stop"] = {"rc": rc, "erro": err.strip()[:200], "processo_depois": apos.get("pid_sfa"),
                                    "stopped_depois": apos.get("stopped")}
            reg("force-stop", **res["a2_force_stop"])
            if apos.get("pid_sfa") is not None:
                raise RuntimeError("o processo do cliente continua depois do force-stop: o gesto não é executado")
        t0 = amb.agora()
        rc, out, err = amb.shell(comando_do_gesto(), 60)
        res["mutacoes"] += 1
        gesto = ler_gesto(rc, out, err, amb.agora() - t0)
        gesto["inicio"], gesto["fim"] = iso(t0), iso()
        res["gesto"] = gesto
        reg("gesto", **{k: v for k, v in gesto.items() if k not in ("saida_bruta", "erro_bruto")})
        if gesto["CLICOU"] == "1":
            res["observacao"] = observar(amb, observar_s, reg)
        else:
            res["observacao"] = {"pulada": "o gesto não clicou (CLICOU != 1); ver Q/P1/P2/T"}
        res["estado"] = "GESTO_EXECUTADO"
    except Exception as exc:  # noqa: BLE001
        res["erro"] = f"{type(exc).__name__}: {exc}"[:300]
        res["estado"] = "ERRO"
        reg("erro", erro=res["erro"])
    finally:
        res["rollback"] = rollback(amb, q0, reg)
    res["central_final"] = central_final(amb)
    obs = res.get("observacao") or {}
    res["tun_subiu"] = bool(obs.get("tun_visto"))
    res["fim"] = iso()
    return res


# ---------------------------------------------------------------------------------------------------- plano e CLI
def plano(fase: str, observar_s: float) -> dict[str, Any]:
    return {"instancia": IID, "serial": SERIAL, "fase": fase, "observar_s": observar_s,
            "precondicoes": "leitura: snapshot, banco (mode=ro), /network/server, adb, coletor vivo",
            "leitura_do_aparelho": {"leve": coletor.CMD_LEVE, "pesada": coletor.CMD_PESADO},
            "force_stop_antes_do_gesto": CMD_FORCE_STOP if fase == "a2" else None,
            "gesto": comando_do_gesto(),
            "rollback": {"desligar_tunel": [CMD_CLICAR, f"(só se o tun0 persistir) {CMD_FORCE_STOP}"],
                         "tile_ao_q0": [CMD_REMOVER, CMD_ADICIONAR], "prova": "releitura leve+pesada"}}


def main(argv: list[str] | None = None, amb: Ambiente | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fase", choices=("a1", "a2"), required=True)
    p.add_argument("--instance", help=f"só {IID} (exigido com --execute)")
    p.add_argument("--execute", action="store_true", help="sem isto: só mostra o plano, não toca em nada")
    p.add_argument("--run", help="pasta do coletor em andamento (data/diag-w8/<ts>)")
    p.add_argument("--apos-a1", help="resultado JSON da a1 (a a2 só roda se ela subiu o tun0 e o rollback fechou)")
    p.add_argument("--observar-s", type=float, default=35.0, help=f"janela de silêncio depois do clique (>= {OBSERVAR_MIN_S:g})")
    a = p.parse_args(argv)
    if a.instance is not None and a.instance != IID:
        print(f"RECUSADO: só {IID} (recebido {a.instance!r}); sem curinga nem lista", file=sys.stderr)
        return 2
    if a.observar_s < OBSERVAR_MIN_S:
        print(f"RECUSADO: --observar-s precisa ser >= {OBSERVAR_MIN_S:g}", file=sys.stderr)
        return 2
    if not a.execute:
        print(json.dumps({"modo": "DRY-RUN (nada foi tocado)", **plano(a.fase, a.observar_s)}, ensure_ascii=False, indent=2))
        return 0
    if a.instance != IID:
        print(f"RECUSADO: --execute exige --instance {IID}", file=sys.stderr)
        return 2
    if not a.run:
        print("RECUSADO: --execute exige --run <pasta do coletor em andamento>", file=sys.stderr)
        return 2
    if a.fase == "a2":
        try:
            a1 = json.loads(Path(a.apos_a1 or "").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print("RECUSADO: a fase a2 exige --apos-a1 com o JSON da a1", file=sys.stderr)
            return 2
        if not (a1.get("fase") == "a1" and a1.get("tun_subiu") is True and (a1.get("rollback") or {}).get("ok") is True):
            print("RECUSADO: a2 só roda se a a1 subiu o tun0 e o rollback da a1 fechou", file=sys.stderr)
            return 2
    run = Path(a.run)
    amb = amb or Ambiente()
    reg = Registro(run / "acionador.jsonl" if run.exists() else None)
    res = rodar_fase(amb, a.fase, run if run.exists() else None, a.observar_s, reg)
    if run.exists():
        (run / f"acionador-{a.fase}.json").write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    if res["estado"] == "BLOCKED":
        return 3
    return 0 if res.get("rollback", {}).get("ok") else 4


if __name__ == "__main__":
    sys.exit(main())
