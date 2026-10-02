"""Bateria de avaliação (config/eval-set.yaml): roda os casos pela MESMA API do painel, confere o resultado pelo
verificador independente do app de QA (ContentProvider via adb) e mede custo de IA por caso (GET /api/usage).

Uso:  backend\\.venv\\Scripts\\python.exe scripts\\eval_run.py --label sonnet-ator [--cases msg-qa001,perfil-formulario]
      (sem --yes: só imprime o plano e sai com código 2, sem abrir conexão com backend nenhum nem chamar o adb)
Saída: data/eval-results.jsonl (uma linha por caso) + tabela em Markdown no stdout.

É [P] mesmo com o provedor simulado: cada caso faz POST /api/runs no backend VIVO (--base), roda adb nos
aparelhos de verdade (flags do app de QA, force-stop, conferência pelo ContentProvider) e, com `relogin_after`,
scripts/provision-qa.ps1. Com o provedor real também é [T] (gasta tokens). Por isso nada acontece sem --yes.
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
#: Queda transitória do transporte (K-045): o backend segue vivo e a conexão reaproveitada é que cai.
TRANSITORIOS = (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError, httpx.ConnectError, httpx.ReadTimeout)


class Resistente:
    """Cliente que repete a chamada quando o TRANSPORTE cai (`RemoteProtocolError`, `ReadError`, ...) em vez de abandonar a bateria
    com uma execução em curso órfã (K-045). Só o transporte: resposta HTTP de erro (4xx/5xx) não é repetida aqui, e a leitura que
    segue falhando depois das tentativas levanta a exceção original. O POST de `/api/runs` leva a `idempotency_key` do caso, então
    repetir o mesmo corpo devolve a execução já criada em vez de abrir outra; o cancelamento também é seguro de repetir."""

    def __init__(self, http: httpx.Client, *, tentativas: int = 4, espera_s: float = 1.0, dorme=time.sleep) -> None:
        self._http, self._tentativas, self._espera_s, self._dorme = http, tentativas, espera_s, dorme

    def _repetindo(self, metodo: str, *args: object, **kw: object) -> httpx.Response:
        for n in range(1, self._tentativas + 1):
            try:
                return getattr(self._http, metodo)(*args, **kw)
            except TRANSITORIOS as exc:
                if n == self._tentativas:
                    raise
                print(f"  (queda transitória do transporte: {type(exc).__name__}; nova tentativa {n + 1}/{self._tentativas})",
                      file=sys.stderr)
                self._dorme(self._espera_s * n)
        raise AssertionError("inalcançável")

    def get(self, *args: object, **kw: object) -> httpx.Response:
        return self._repetindo("get", *args, **kw)

    def post(self, *args: object, **kw: object) -> httpx.Response:
        return self._repetindo("post", *args, **kw)


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
        elif chk["kind"] == "message_set":      # várias mensagens desta execução: UMA por contato, nenhuma repetida
            rows = [ln for ln in adb(serial, "shell", "content", "query", "--uri", f"{PROVIDER}/messages").splitlines()
                    if run["id"] in ln]
            contacts = [m.group(1) for ln in rows if (m := re.search(r"contact=([^,]*),", ln))]
            sent = all(re.search(f"status=[^,]*({chk['status']})", ln) for ln in rows)
            want = set(chk.get("contacts") or [])
            fine = (len(contacts) == len(set(contacts)) and sent and len(contacts) >= int(chk.get("min", 1))
                    and (not want or set(contacts) == want))
            ok = ok and fine
            notes.append(f"{iid}: {len(rows)} msg desta execução para {len(set(contacts))} contato(s)")
        elif chk["kind"] == "profile":
            out = adb(serial, "shell", "content", "query", "--uri", f"{PROVIDER}/profile")
            for field, value in chk["fields"].items():
                want = value.replace("{instance_id}", iid).replace("{run_id}", run["id"])
                if f"{field}={want}" not in out:
                    ok = False
                    notes.append(f"{iid}: perfil sem {field}={want}")
    return ok, "; ".join(notes)


def corpo_da_execucao(case: dict, ids: list[str], perfil: str | None) -> dict:
    """O POST de `/api/runs` de um caso. `perfil` (item 17.7) escolhe `ai.profiles.<nome>` só para esta execução: o
    braço B da comparação roda no MESMO backend, sem reiniciar o central e sem mexer no que as outras execuções usam."""
    corpo = {"command": case["command"], "instance_ids": ids, "mode": "execute", "idempotency_key": f"eval-{uuid.uuid4()}"}
    if perfil:
        corpo["ai_profile"] = perfil
    return corpo


def plano(cases: list[dict], spec: dict, a: argparse.Namespace) -> str:
    """O que a bateria FARIA, sem fazer nada: casos, aparelhos e cada efeito colateral, por caso."""
    linhas = [f"PLANO (nada foi executado; rode de novo com --yes para executar) — backend {a.base}, rótulo '{a.label}'"
              + (f", perfil de IA '{a.profile}'" if getattr(a, "profile", "") else ", funções de IA padrão"),
              "Efeitos de cada caso: POST /api/runs no backend vivo (e POST .../cancel no estouro de prazo); adb nos "
              "aparelhos para conferir o resultado; com provedor real, chamadas pagas de IA."]
    for case in cases:
        ids = [x for x in a.instances.split(",") if x] or case.get("instances") or spec["defaults"]["instances"]
        extras = []
        if case.get("flags"):
            extras.append(f"adb: liga as flags {sorted(case['flags'])} no app de QA e faz force-stop")
        if case.get("relogin_after"):
            extras.append("roda scripts/provision-qa.ps1 -SkipInstall (relogin)")
        linhas.append(f"- {case['id']}: aparelhos {','.join(ids)}; espera {case['expect']}; "
                      f"prazo {case.get('timeout_s', spec['defaults']['timeout_s'])} s"
                      + (f"; {'; '.join(extras)}" if extras else ""))
    return "\n".join(linhas)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bateria de avaliação. [P] (backend vivo + adb) e, com provedor real, "
                                             "[T]. Sem --yes só imprime o plano.")
    ap.add_argument("--label", required=True, help="nome da configuração avaliada (ex.: opus-tudo, sonnet-ator+receitas)")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--cases", default="", help="ids separados por vírgula (padrão: todos)")
    ap.add_argument("--instances", default="", help="sobrescreve as instâncias de todos os casos (a,b,c)")
    ap.add_argument("--profile", default="", help="perfil de IA (ai.profiles.<nome>) das execuções desta bateria; vazio = "
                                                  "as funções padrão (item 17.7: A/B sem reiniciar o central)")
    ap.add_argument("--yes", action="store_true",
                    help="confirma a execução de verdade: POST /api/runs no backend vivo, adb nos aparelhos (flags, "
                         "force-stop, conferência), provision-qa.ps1 quando o caso pede e, com provedor real, gasto "
                         "de IA. Sem ele o script só imprime o plano")
    a = ap.parse_args(argv)
    spec = yaml.safe_load((ROOT / "config" / "eval-set.yaml").read_text(encoding="utf-8"))
    wanted = {c for c in a.cases.split(",") if c}
    cases = [c for c in spec["cases"] if not wanted or c["id"] in wanted]
    if not a.yes:
        # Seguro por padrão: sem --yes não se abre NEM a conexão de leitura. O plano sai só do YAML local.
        print(plano(cases, spec, a))
        return 2
    http = Resistente(httpx.Client(base_url=a.base, timeout=30, headers={"Origin": a.base}))
    ai = http.get("/api/ai").json()
    insts = {i["id"]: i for i in http.get("/api/instances").json()}
    apps = {x["package"] for x in http.get("/api/apps").json()}
    print(f"Configuração '{a.label}': provedor={ai['provider']} simulado={ai['simulated']} modelos={ai.get('models')} "
          f"receitas={ai.get('recipes')} fluxos={ai.get('flows')} imagem={ai.get('image_policy')}")
    if not ai["simulated"]:
        print(f"{len(cases)} caso(s) com o provedor REAL (confirmado por --yes). Referência medida: ≈US$ 0,05–0,45 por "
              "aparelho-caso, conforme o modelo.")
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
        run = http.post("/api/runs", json=corpo_da_execucao(case, ids, a.profile or None)).json()
        deadline = t0 + case.get("timeout_s", spec["defaults"]["timeout_s"])
        detail = None
        while True:
            time.sleep(3)
            try:
                detail = http.get(f"/api/runs/{run['id']}").json()
            except TRANSITORIOS:
                # Esgotou as tentativas desta leitura: a execução segue viva no backend, então se espera a próxima volta
                # (até o prazo) em vez de largá-la órfã. Sem nenhuma leitura boa até o prazo, o erro aparece abaixo.
                if time.monotonic() > deadline:
                    http.post(f"/api/runs/{run['id']}/cancel", json={})
                    break
                continue
            pending = [o for o in detail["objectives"] if o["status"] in ("pending", "running")]
            if detail["status"] in TERMINAL or (detail["status"] == "running" and detail["objectives"] and not pending):
                break
            if time.monotonic() > deadline:
                http.post(f"/api/runs/{run['id']}/cancel", json={})
                break
        if detail is None:
            raise RuntimeError(f"{case['id']}: nenhuma leitura de /api/runs/{run['id']} respondeu até o prazo")
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
               "driven_by": usage["steps_driven_by"], "models": ai.get("models"), "simulated": ai["simulated"],
               # `models` é o do PADRÃO (o /api/ai não conhece a execução); com perfil, o que valeu é o do perfil.
               "ai_profile": run.get("ai_profile"), "ai_profile_source": run.get("ai_profile_source")}
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
