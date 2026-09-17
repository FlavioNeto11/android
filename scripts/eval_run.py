"""Bateria de avaliação (config/eval-set.yaml): roda os casos pela MESMA API do painel, confere o resultado pelo
verificador independente do app de QA (ContentProvider via adb) e mede custo de IA por caso (GET /api/usage).

Uso:  backend\\.venv\\Scripts\\python.exe scripts\\eval_run.py --label sonnet-ator [--cases msg-qa001,perfil-formulario]
Saída: data/eval-results.jsonl (uma linha por caso) + tabela em Markdown no stdout.
ATENÇÃO: com o provedor real cada caso GASTA tokens; a estimativa aparece antes de começar.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
ADB = Path(r"C:\Android\Sdk\platform-tools\adb.exe")
PROVIDER = "content://com.pocqa.messenger.provider"
TERMINAL = {"completed", "completed_with_issues", "failed", "cancelled", "needs_input"}


def adb(serial: str, *args: str) -> str:
    res = subprocess.run([str(ADB), "-s", serial, *args], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=60)
    return (res.stdout or "") + (res.stderr or "")


def set_flag(serial: str, key: str, value: int) -> None:
    adb(serial, "shell", "content", "insert", "--uri", f"{PROVIDER}/flags", "--bind", f"key:s:{key}", "--bind", f"value:s:{value}")


def check(case: dict, run: dict, serials: dict[str, str]) -> tuple[bool, str]:
    chk = case.get("check") or {"kind": "none"}
    notes = []
    ok = True
    for obj in run["objectives"]:
        iid, serial = obj["instance_id"], serials[obj["instance_id"]]
        if chk["kind"] == "message":
            rows = [ln for ln in adb(serial, "shell", "content", "query", "--uri", f"{PROVIDER}/messages").splitlines()
                    if run["id"] in ln]
            good = [ln for ln in rows if f"contact={chk['contact']}," in ln and re.search(f"status=[^,]*({chk['status']})", ln)]
            if len(rows) != 1 or len(good) != 1:
                ok = False
            notes.append(f"{iid}: {len(rows)} msg desta execução, {len(good)} com contato/status esperados")
        elif chk["kind"] == "profile":
            out = adb(serial, "shell", "content", "query", "--uri", f"{PROVIDER}/profile")
            for field, value in chk["fields"].items():
                want = value.replace("{instance_id}", iid).replace("{run_id}", run["id"])
                if f"{field}={want}" not in out:
                    ok = False
                    notes.append(f"{iid}: perfil sem {field}={want}")
    return ok, "; ".join(notes)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="nome da configuração avaliada (ex.: opus-tudo, sonnet-ator+receitas)")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--cases", default="", help="ids separados por vírgula (padrão: todos)")
    ap.add_argument("--instances", default="", help="sobrescreve as instâncias de todos os casos (a,b,c)")
    ap.add_argument("--yes", action="store_true", help="não pedir confirmação de gasto")
    a = ap.parse_args()
    spec = yaml.safe_load((ROOT / "config" / "eval-set.yaml").read_text(encoding="utf-8"))
    wanted = {c for c in a.cases.split(",") if c}
    cases = [c for c in spec["cases"] if not wanted or c["id"] in wanted]
    http = httpx.Client(base_url=a.base, timeout=30, headers={"Origin": a.base})
    ai = http.get("/api/ai").json()
    insts = {i["id"]: i for i in http.get("/api/instances").json()}
    apps = {x["package"] for x in http.get("/api/apps").json()}
    print(f"Configuração '{a.label}': provedor={ai['provider']} simulado={ai['simulated']} modelos={ai.get('models')} "
          f"receitas={ai.get('recipes')} fluxos={ai.get('flows')} imagem={ai.get('image_policy')}")
    if not ai["simulated"] and not a.yes:
        print(f"{len(cases)} caso(s) com o provedor REAL. Referência medida: ≈US$ 0,05–0,45 por aparelho-caso, conforme o "
              "modelo. Rode de novo com --yes para confirmar o gasto.")
        return 2
    out_file = ROOT / "data" / "eval-results.jsonl"
    results = []
    for case in cases:
        ids = [x for x in a.instances.split(",") if x] or case.get("instances") or spec["defaults"]["instances"]
        skip = next((f"{i} não está online" for i in ids if insts.get(i, {}).get("state") != "online"), None)
        if case.get("requires_app") and case["requires_app"] not in apps:
            skip = f"app {case['requires_app']} não cadastrado"
        if skip and not http.get("/api/settings").json().get("auto_start_devices"):
            results.append({"case": case["id"], "label": a.label, "skipped": skip})
            print(f"- {case['id']}: PULADO ({skip})")
            continue
        serials = {i: insts[i]["serial"] for i in ids}
        for serial in serials.values():
            for k, v in (case.get("flags") or {}).items():
                set_flag(serial, k, v)
            if case.get("flags"):
                adb(serial, "shell", "am", "force-stop", "com.pocqa.messenger")
        t0 = time.monotonic()
        run = http.post("/api/runs", json={"command": case["command"], "instance_ids": ids, "mode": "execute",
                                           "idempotency_key": f"eval-{uuid.uuid4()}"}).json()
        deadline = t0 + case.get("timeout_s", spec["defaults"]["timeout_s"])
        while True:
            time.sleep(3)
            detail = http.get(f"/api/runs/{run['id']}").json()
            pending = [o for o in detail["objectives"] if o["status"] in ("pending", "running")]
            if detail["status"] in TERMINAL or (detail["status"] == "running" and detail["objectives"] and not pending):
                break
            if time.monotonic() > deadline:
                http.post(f"/api/runs/{run['id']}/cancel", json={})
                break
        secs = round(time.monotonic() - t0)
        got = "needs_input" if detail["status"] == "needs_input" else (
            sorted({o["status"] for o in detail["objectives"]}) or [detail["status"]])
        got_s = got if isinstance(got, str) else "+".join(got)
        state_ok = got_s == case["expect"]
        data_ok, note = check(case, detail, serials) if detail["objectives"] else (True, "")
        usage = http.get("/api/usage", params={"run_id": run["id"]}).json()
        for serial in serials.values():
            for k in (case.get("flags") or {}):
                set_flag(serial, k, 0)
        if case.get("relogin_after"):
            subprocess.run(["pwsh", "-NoProfile", "-File", str(ROOT / "scripts" / "provision-qa.ps1"), "-Instances",
                            ",".join(ids), "-SkipInstall"], capture_output=True, timeout=180)
        calls = sum(g["calls"] for g in usage["groups"])
        tokens_in = sum(g["fresh"] + g["cache_read"] + g["cache_write"] for g in usage["groups"])
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "label": a.label, "case": case["id"], "run_id": run["id"],
               "instances": len(ids), "expected": case["expect"], "got": got_s, "pass": bool(state_ok and data_ok),
               "note": note, "seconds": secs, "ai_calls": calls, "tokens_in": tokens_in,
               "tokens_out": sum(g["output"] for g in usage["groups"]), "usd": usage["total_usd"],
               "driven_by": usage["steps_driven_by"], "models": ai.get("models"), "simulated": ai["simulated"]}
        results.append(rec)
        with out_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"- {case['id']}: {'OK' if rec['pass'] else 'FALHOU'} (esperado {case['expect']}, obtido {got_s}) "
              f"{secs}s · {calls} chamadas · US$ {usage['total_usd']:.4f} {note}")
    done = [r for r in results if "skipped" not in r]
    print(f"\n## Bateria '{a.label}' — {sum(r['pass'] for r in done)}/{len(done)} casos corretos\n")
    print("| Caso | Esperado | Obtido | OK | s | Chamadas | Tokens in | US$ | Etapas por receita/IA |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in done:
        d = r["driven_by"]
        print(f"| {r['case']} | {r['expected']} | {r['got']} | {'✅' if r['pass'] else '❌'} | {r['seconds']} | {r['ai_calls']} | "
              f"{r['tokens_in']} | {r['usd']:.4f} | {d.get('recipe', 0)}/{d.get('ai', 0) + d.get('recipe+ai', 0)} |")
    n = max(1, sum(r["instances"] for r in done))
    print(f"\nTotal: US$ {sum(r['usd'] for r in done):.4f} · US$ {sum(r['usd'] for r in done) / n:.4f} por aparelho-caso · "
          f"{sum(r['ai_calls'] for r in done) / n:.1f} chamadas por aparelho-caso")
    return 0 if all(r["pass"] for r in done) else 1


if __name__ == "__main__":
    sys.exit(main())
