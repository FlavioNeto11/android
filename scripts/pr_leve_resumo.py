"""Frente GitHub (29.179): resumo de UMA linha de um job do CI leve do PR, para o `GITHUB_STEP_SUMMARY`.

`python scripts/pr_leve_resumo.py --nome "docs-check + scripts/tests" --repo dono/nome --run 123 --tentativa 1 --runner NOME
[--pytest ARQ] [--vitest ARQ] >> "$GITHUB_STEP_SUMMARY"` lê, pela API do `gh`, os passos do job em andamento (o passo deste resumo
ainda não terminou e fica de fora) e imprime o tempo de cada etapa em segundos e o número de testes que o pytest e o vitest
contaram nos arquivos de saída. É a base para ler o custo do CI leve por PR sem abrir o log.

Só LEITURA e nunca derruba o job: qualquer falha vira a linha "resumo indisponível" com saída 0. Não imprime nome de conta, de
repositório nem de runner.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

Gh = Callable[..., str]
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_IGNORAR = ("Set up job", "Complete job", "Post ", "Resumo do job")


def gh_real(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"o gh falhou (código {r.returncode})")
    return r.stdout


def _segundos(ini: object, fim: object) -> int | None:
    try:
        a = datetime.fromisoformat(str(ini).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(fim).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, round((b - a).total_seconds()))


def etapas(passos: list[dict[str, object]]) -> list[tuple[str, int]]:
    achadas = []
    for p in passos:
        nome = str(p.get("name") or "")
        if not nome or nome.startswith(_IGNORAR) or p.get("conclusion") in (None, "skipped"):
            continue
        s = _segundos(p.get("started_at"), p.get("completed_at"))
        if s is not None:
            achadas.append((nome[:32], s))
    return achadas


def _contagem(texto: str, so_linha_que_comeca: str | None = None) -> str:
    """'779 passed, 11 skipped' a partir da linha final do pytest ou da linha `Tests` do vitest."""
    for linha in reversed([_ANSI.sub("", x).strip() for x in texto.splitlines()]):
        if so_linha_que_comeca and not linha.startswith(so_linha_que_comeca):
            continue
        achados = re.findall(r"(\d+) (passed|failed|skipped|errors?)\b", linha)
        if achados:
            return ", ".join(f"{n} {k}" for n, k in achados)
    return ""


def _ler(arq: Path | None) -> str:
    try:
        return arq.read_text(encoding="utf-8", errors="replace") if arq else ""
    except OSError:
        return ""


def cobrado(job: dict[str, object]) -> str:
    """Minutos cobrados ESTIMADOS: do início do job ao fim da última etapa concluída, arredondado para cima; 0 no runner próprio."""
    if "self-hosted" in [str(x) for x in (job.get("labels") or [])]:  # type: ignore[union-attr]
        return "0 min cobrados (runner próprio)"
    fins = [str(p.get("completed_at")) for p in (job.get("steps") or []) if p.get("completed_at")]  # type: ignore[union-attr]
    s = _segundos(job.get("started_at"), max(fins)) if fins and job.get("started_at") else None
    return f"~{max(1, math.ceil(s / 60))} min cobrados" if s is not None else ""


def linha(nome: str, passos: list[tuple[str, int]], pytest: str, vitest: str, cobrados: str = "") -> str:
    partes = [f"{n} {s} s" for n, s in passos]
    total = sum(s for _, s in passos)
    testes = " · ".join(x for x in (f"pytest {_contagem(pytest)}" if _contagem(pytest) else "",
                                    f"vitest {_contagem(vitest, 'Tests')}" if _contagem(vitest, "Tests") else "") if x)
    return (f"**{nome}**: " + " · ".join(partes) + f" · soma das etapas {total} s" + (f" · {cobrados}" if cobrados else "")
            + (f" · {testes}" if testes else ""))


def main(argv: list[str] | None = None, gh: Gh | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resumo de uma linha de um job do CI leve do PR.")
    ap.add_argument("--nome", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--run", required=True, type=int)
    ap.add_argument("--tentativa", type=int, default=1)
    ap.add_argument("--runner", required=True)
    ap.add_argument("--pytest", type=Path)
    ap.add_argument("--vitest", type=Path)
    ap.add_argument("--espera", type=float, default=4.0, help="segundos antes de ler a API: ela atrasa a última etapa (medido em 06/10: o build de 5 s não aparecia)")
    a = ap.parse_args(argv)
    for f in (sys.stdout, sys.stderr):
        if hasattr(f, "reconfigure"):
            f.reconfigure(encoding="utf-8", errors="replace")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", a.repo):
        print(f"**{a.nome}**: resumo indisponível (--repo fora do formato)")
        return 0
    time.sleep(max(0.0, min(a.espera, 15.0)))
    try:
        jobs = [json.loads(x) for x in (gh or gh_real)(
            "api", f"repos/{a.repo}/actions/runs/{a.run}/attempts/{a.tentativa}/jobs?per_page=100",
            "--jq", ".jobs[] | {name, runner_name, labels, started_at, steps}").splitlines() if x.strip()]
        meu = next((j for j in jobs if j.get("runner_name") == a.runner), None) or next((j for j in jobs if j.get("name") == a.nome), None)
        if meu is None:
            raise ValueError("job não encontrado")
        print(linha(a.nome, etapas(meu.get("steps") or []), _ler(a.pytest), _ler(a.vitest), cobrado(meu)))
    except (RuntimeError, ValueError, KeyError, TypeError, AttributeError, OSError, json.JSONDecodeError):
        print(f"**{a.nome}**: resumo indisponível")
    return 0


if __name__ == "__main__":
    sys.exit(main())
