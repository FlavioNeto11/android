"""Rodada QA pareada: o canário do planejador, Opus padrão (braço A) × perfil `planejador-sonnet` (braço B), em ABBA
por caso. [P] (backend vivo + adb) e [T] (chamadas pagas de IA) só com `--yes`.

Roteiro aprovado pela orquestradora (03/10, `.claude/handoffs/aprendizado.md`). Os 4 casos são os do `eval-set.yaml`
que CHAMAM o planejador: um caso que casa com fluxo ativo ou habilidade não chama (`service.py::_plan`), e o A/B não
mediria nada (12 dos 17 casavam em 03/10). Aceite: sucesso de B ≥ A e p50 do planejador em B ≤ 11 s, com ao menos 6
execuções válidas por braço; senão inconclusivo.

Modos:
- sem opção: só o plano (nenhuma conexão);
- `--checar`: as pré-checagens de custo zero (saúde, aparelho, `POST /api/flows/match`);
- `--yes`: pré-checagens e a rodada, chamando `eval_run.py` por caso e braço (grava em `data/eval-results.jsonl`);
- `--ler RODADA`: a leitura, de `data/eval-results.jsonl` + `GET /api/usage?run_id=` (grupo `role=plan`).
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_run  # script irmão, sem pacote

ROOT = Path(__file__).resolve().parents[1]
RESULTADOS = ROOT / "data" / "eval-results.jsonl"
CASOS = ("abrir-tela", "perfil-campo-inexistente", "comando-ambiguo", "msg-todos-os-contatos")
ORDEM = ("A", "B", "B", "A")
PERFIL_B = "planejador-sonnet"
P50_MAX_MS = 11_000
VALIDAS_MIN = 6


# ------------------------------------------------------------------ puras (testadas)
def ordem_abba(casos: Iterable[str]) -> list[tuple[str, str]]:
    """ABBA por caso: a deriva dentro do caso (cache, fluxo que nasce, aparelho que esquenta) cai igual nos dois."""
    return [(caso, braco) for caso in casos for braco in ORDEM]


def rotulo(rodada: str, braco: str) -> str:
    return f"qa-par-{rodada}-{braco}"


def argv_do_eval(caso: str, braco: str, rodada: str, instancia: str, perfil: str = PERFIL_B) -> list[str]:
    argv = ["--label", rotulo(rodada, braco), "--cases", caso, "--instances", instancia, "--yes"]
    return argv + (["--profile", perfil] if braco == "B" else [])


def percentil(valores: list[float], p: float) -> float | None:
    """Percentil por posição mais próxima (o mesmo de `GET /api/desempenho`): com poucas amostras, um valor medido."""
    if not valores:
        return None
    ordenados = sorted(valores)
    return ordenados[max(0, math.ceil(p / 100 * len(ordenados)) - 1)]


def veredito(linhas: list[dict[str, Any]]) -> dict[str, Any]:
    """`linhas`: uma por execução, com `braco`, `pass`, `usd`, `plan_ms` (`None` = sem chamada ao planejador, servida
    por fluxo: sai da comparação) e `plan_model`."""
    por_braco: dict[str, Any] = {}
    for braco in ("A", "B"):
        todas = [x for x in linhas if x["braco"] == braco]
        validas = [x for x in todas if x.get("plan_ms") is not None]
        ms = [float(x["plan_ms"]) for x in validas]
        por_braco[braco] = {
            "execucoes": len(todas), "validas": len(validas), "sem_planejador": len(todas) - len(validas),
            "sucesso": sum(1 for x in validas if x["pass"]), "taxa": (sum(1 for x in validas if x["pass"]) / len(validas)
                                                                       if validas else None),
            "p50_ms": percentil(ms, 50), "p90_ms": percentil(ms, 90),
            "usd": round(sum(float(x.get("usd") or 0) for x in todas), 4),
            "modelos": sorted({str(x["plan_model"]) for x in validas if x.get("plan_model")})}
    a, b = por_braco["A"], por_braco["B"]
    if a["validas"] < VALIDAS_MIN or b["validas"] < VALIDAS_MIN:
        decisao, motivo = "inconclusivo", f"menos de {VALIDAS_MIN} execuções válidas num braço"
    elif b["taxa"] < a["taxa"]:
        decisao, motivo = "nao_passa", "sucesso do B abaixo do A"
    elif b["p50_ms"] > P50_MAX_MS:
        decisao, motivo = "nao_passa", f"p50 do planejador no B acima de {P50_MAX_MS} ms"
    else:
        decisao, motivo = "passa", f"sucesso B ≥ A e p50 do B ≤ {P50_MAX_MS} ms"
    return {"bracos": por_braco, "decisao": decisao, "motivo": motivo}


def plano(casos: Iterable[str], instancia: str, rodada: str, teto: float) -> str:
    linhas = [f"PLANO (nada foi executado) — rodada {rodada}, aparelho {instancia}, teto US$ {teto:.2f}",
              f"A = funções padrão (Opus no plan); B = --profile {PERFIL_B} (só o plan muda).",
              "Antes de cada execução: POST /api/flows/match do caso (casou = pula: o planejador não seria chamado)."]
    for n, (caso, braco) in enumerate(ordem_abba(casos), start=1):
        linhas.append(f"{n:2}. {caso} · braço {braco} · eval_run.py {' '.join(argv_do_eval(caso, braco, rodada, instancia))}")
    return "\n".join(linhas)


# ------------------------------------------------------------------ com o backend
def _comandos() -> dict[str, str]:
    spec = eval_run.yaml.safe_load((ROOT / "config" / "eval-set.yaml").read_text(encoding="utf-8"))
    return {c["id"]: c["command"] for c in spec["cases"]}


def servida_por_fluxo(http: Any, comando: str) -> bool:
    r = http.post("/api/flows/match", json={"command": comando})
    r.raise_for_status()
    return r.json() is not None


def pre_checagens(http: Any, casos: Iterable[str], instancia: str, exigido: str | None,
                  e_ancestral: Callable[[str, str], bool]) -> list[str]:
    """Custo zero. Devolve os problemas; vazio = pode rodar."""
    problemas: list[str] = []
    saude = http.get("/api/health").json()
    if exigido and not e_ancestral(exigido, str(saude.get("commit") or "")):
        problemas.append(f"o central está em {saude.get('commit')}, sem {exigido} (deploy 7?)")
    aparelhos = {i["id"]: i for i in http.get("/api/instances").json()}
    if aparelhos.get(instancia, {}).get("state") != "online":
        problemas.append(f"{instancia} não está online")
    comandos = _comandos()
    for caso in casos:
        if caso not in comandos:
            problemas.append(f"{caso} não está no eval-set.yaml")
        elif servida_por_fluxo(http, comandos[caso]):
            problemas.append(f"{caso} casa com fluxo ou habilidade: o planejador não seria chamado")
    return problemas


def _e_ancestral(sha: str, commit: str) -> bool:
    return bool(commit) and subprocess.run(["git", "merge-base", "--is-ancestor", sha, commit], cwd=ROOT,
                                           capture_output=True, check=False).returncode == 0


def _linhas_da_rodada(rodada: str) -> list[dict[str, Any]]:
    if not RESULTADOS.is_file():
        return []
    saida = []
    for texto in RESULTADOS.read_text(encoding="utf-8").splitlines():
        r = json.loads(texto) if texto.strip() else {}
        if str(r.get("label", "")).startswith(f"qa-par-{rodada}-") and "run_id" in r:
            saida.append(r)
    return saida


def ler(http: Any, rodada: str) -> dict[str, Any]:
    linhas = []
    for r in _linhas_da_rodada(rodada):
        uso = http.get("/api/usage", params={"run_id": r["run_id"]}).json()
        plan = [g for g in uso.get("groups", []) if g.get("role") == "plan"]
        linhas.append({"braco": str(r["label"]).rsplit("-", 1)[-1], "caso": r["case"], "run_id": r["run_id"],
                       "pass": bool(r["pass"]), "usd": r.get("usd", 0),
                       "plan_ms": plan[0]["avg_ms"] if plan else None, "plan_model": plan[0]["model"] if plan else None})
    resultado = veredito(linhas)
    resultado["rodada"], resultado["execucoes"] = rodada, linhas
    return resultado


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rodada QA pareada (canário do planejador). Sem opção, só o plano.")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--instancia", default="android-09")
    ap.add_argument("--casos", default=",".join(CASOS))
    ap.add_argument("--rodada", default=time.strftime("%Y%m%d%H%M"))
    ap.add_argument("--teto-usd", type=float, default=8.0, help="para ANTES da próxima execução se a soma passar disto")
    ap.add_argument("--exige-commit", default="", help="sha que o central tem de conter (o do perfil, e4a597f8)")
    ap.add_argument("--checar", action="store_true", help="só as pré-checagens de custo zero")
    ap.add_argument("--yes", action="store_true", help="roda de verdade: [P] e [T]")
    ap.add_argument("--ler", default="", help="lê a rodada RODADA já executada")
    a = ap.parse_args(argv)
    eval_run.saida_segura()
    casos = [c for c in a.casos.split(",") if c]
    if not (a.checar or a.yes or a.ler):
        print(plano(casos, a.instancia, a.rodada, a.teto_usd))
        return 2
    http = eval_run.Resistente(httpx.Client(base_url=a.base, timeout=30, headers={"Origin": a.base}))
    if a.ler:
        print(json.dumps(ler(http, a.ler), ensure_ascii=False, indent=2))
        return 0
    problemas = pre_checagens(http, casos, a.instancia, a.exige_commit or None, _e_ancestral)
    for p in problemas:
        print(f"- PRÉ-CHECAGEM: {p}")
    if problemas or a.checar:
        return 1 if problemas else 0
    comandos = _comandos()
    for caso, braco in ordem_abba(casos):
        gasto = sum(float(r.get("usd") or 0) for r in _linhas_da_rodada(a.rodada))
        if gasto > a.teto_usd:
            print(f"PAROU: US$ {gasto:.4f} gastos, acima do teto de US$ {a.teto_usd:.2f}")
            return 1
        if servida_por_fluxo(http, comandos[caso]):
            print(f"- {caso} · {braco}: PULADO (passou a casar com fluxo; o planejador não seria chamado)")
            continue
        try:
            eval_run.main(argv_do_eval(caso, braco, a.rodada, a.instancia))
        except Exception as exc:  # noqa: BLE001 - 422 do perfil, queda: para a rodada inteira, sem seguir gastando
            print(f"PAROU em {caso} · {braco}: {type(exc).__name__}: {exc}")
            return 1
    print(json.dumps(ler(http, a.rodada), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
