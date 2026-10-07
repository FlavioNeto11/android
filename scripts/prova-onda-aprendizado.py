"""31.192: marca a prova da onda (prova30) dos itens do aprendizado que dependem do commit no ar: `real` com o que a
operação mostrou, ou `not_run` com o motivo. SÓ LEITURA: o banco abre em `mode=ro`, a saúde é um GET.

Por item, duas perguntas, nesta ordem:
1. o commit do item está no central? (`git merge-base --is-ancestor <commit do item> <commit no ar>`). Não: `not_run`,
   "o commit X não está no central (Y)";
2. a operação exercitou o item? Cada item tem a sua leitura (abaixo). Sim: `real`, com data, máquina, commit no ar e
   os ids; não: `not_run` com o que faltou.

    31.165  etapa de execução da operação conduzida por uma receita do ENSINO (`driven_by='recipe'`, receita
            `training:%`) e que comprovou;
    31.178  evidência a favor de receita (`reproducao:<run>`) gravada antes de a execução da operação terminar;
    31.179  fato de pesquisa da operação `confirmado` cuja evidência inclui a leitura do alvo (`leitura_do_alvo`).

A saída é o formato do plano-100 só com as linhas `real` (`resultados`); as `not_run` vão à parte (`pendentes`) e NÃO
entram no `aplicar`: uma linha `not_run` apaga a prova simulada já registrada (aprendizado de 06/10). Nada de nome,
@, legenda ou texto de memória: só ids, contagens e chaves.

Exemplo, a partir da raiz do repositório:
    backend/.venv/Scripts/python.exe scripts/prova-onda-aprendizado.py --operacao op-... > prova.json
    backend/.venv/Scripts/python.exe scripts/prova-onda-aprendizado.py --operacao op-... --commit 42cba3cd --banco x.sqlite3
"""
from __future__ import annotations

import argparse
import json
import socket
import sqlite3
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SAUDE = "http://127.0.0.1:8000/api/health"
NOME_DA_LEITURA = "leitura_do_alvo"            # `pedidos/domain/conhecimento_da_operacao.NOME_DA_LEITURA`


@dataclass(frozen=True)
class Leitura:
    exercitado: bool
    detalhe: str                               # ids e contagens; o motivo quando não exercitou


def _runs(c: sqlite3.Connection, op: str) -> list[str]:
    return [str(r[0]) for r in c.execute("SELECT id FROM runs WHERE operacao_id=? ORDER BY id", (op,))]


def _em(n: int) -> str:
    return ",".join("?" * n)


def ler_31_165(c: sqlite3.Connection, op: str) -> Leitura:
    runs = _runs(c, op)
    if not runs:
        return Leitura(False, "a operação não tem execução")
    linhas = c.execute(
        "SELECT s.id, a.recipe_id FROM steps s JOIN attempts a ON a.step_id = s.id JOIN recipes r ON r.id = a.recipe_id"
        f" WHERE s.run_id IN ({_em(len(runs))}) AND s.driven_by='recipe' AND s.status='succeeded'"
        " AND a.strategy='recipe' AND a.status='succeeded' AND r.learned_from_step LIKE 'training:%' ORDER BY s.id",
        tuple(runs)).fetchall()
    if not linhas:
        return Leitura(False, f"nenhuma etapa das {len(runs)} execução(ões) foi conduzida por receita do ensino")
    receitas = sorted({int(r[1]) for r in linhas})
    return Leitura(True, f"{len(linhas)} etapa(s) pela receita do ensino {receitas} (etapas {[r[0] for r in linhas][:5]})")


def ler_31_178(c: sqlite3.Connection, op: str) -> Leitura:
    runs = _runs(c, op)
    if not runs:
        return Leitura(False, "a operação não tem execução")
    linhas = c.execute(
        "SELECT e.item_ref, e.run_id FROM learning_evidence e JOIN runs r ON r.id = e.run_id"
        f" WHERE e.run_id IN ({_em(len(runs))}) AND e.stance='for' AND e.item_ref LIKE 'receita:%'"
        " AND e.origin_ref LIKE 'reproducao:%' AND e.simulated=0"
        " AND (r.finished_at IS NULL OR e.observed_at < r.finished_at) ORDER BY e.run_id", tuple(runs)).fetchall()
    if not linhas:
        return Leitura(False, "nenhuma evidência a favor de receita gravada com a execução aberta")
    return Leitura(True, f"{len(linhas)} evidência(s) a favor com a execução aberta "
                         f"({sorted({str(r[0]) for r in linhas})[:5]}; execuções {sorted({str(r[1]) for r in linhas})[:5]})")


def ler_31_179(c: sqlite3.Connection, op: str) -> Leitura:
    leitura = c.execute("SELECT id FROM pedido_observacoes WHERE operacao_id=? AND nome=? ORDER BY capturado_em LIMIT 1",
                        (op, NOME_DA_LEITURA)).fetchone()
    if leitura is None:
        return Leitura(False, "a operação não tem leitura do alvo")
    confirmados = []
    for chave, evidencia in c.execute("SELECT chave, evidencia FROM pedido_memoria WHERE operacao_id=? AND chave LIKE"
                                      " 'pesquisa.%' AND confianca='confirmado' ORDER BY chave", (op,)):
        try:
            ids = json.loads(evidencia or "[]")
        except ValueError:
            ids = []
        if str(leitura[0]) in [str(i) for i in ids if i is not None]:
            confirmados.append(str(chave))
    if not confirmados:
        return Leitura(False, "nenhuma hipótese da pesquisa tinha as âncoras da leitura do alvo")
    return Leitura(True, f"{len(confirmados)} fato(s) confirmado(s) pela leitura {leitura[0]} ({confirmados[:5]})")


#: id do item → (commit que o traz, leitura). O commit é o do `feat` do item (ancestral de tudo que o contém).
ITENS: dict[str, tuple[str, Callable[[sqlite3.Connection, str], Leitura]]] = {
    "31.165": ("8dff4e7335bb47a85947feb14d2b4de32c29a4ac", ler_31_165),
    "31.178": ("918bbc857b120bd7601fb65ada9ba371fe15d34b", ler_31_178),
    "31.179": ("41d57e300959606108da3081d7d539ae14917352", ler_31_179),
}


def no_ar(commit_do_item: str, commit_no_ar: str, *, repo: Path = RAIZ) -> bool | None:
    """True/False pelo git; None se um dos commits não existe neste clone (não se sabe: vira `not_run`)."""
    r = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", commit_do_item, commit_no_ar],
                       capture_output=True, text=True, check=False)
    return True if r.returncode == 0 else False if r.returncode == 1 else None


def commit_da_saude(url: str = SAUDE) -> str:
    with urllib.request.urlopen(url, timeout=10) as resp:      # noqa: S310 - URL local fixa ou dada pelo operador
        return str(json.load(resp).get("commit") or "")


def marcar(c: sqlite3.Connection, op: str, commit: str, *, ancestral: Callable[[str, str], bool | None] = no_ar,
           agora: str | None = None, maquina: str | None = None) -> dict[str, object]:
    quando = agora or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    host = maquina or socket.gethostname()
    reais: list[dict[str, object]] = []
    pendentes: list[dict[str, object]] = []
    for item, (do_item, ler) in ITENS.items():
        tem = ancestral(do_item, commit)
        if not tem:
            motivo = (f"o commit {do_item[:8]} do item não está no central ({commit[:8]})" if tem is False
                      else f"commit {do_item[:8]} ou {commit[:8]} desconhecido neste clone")
            pendentes.append({"id": item, "proof": "not_run", "motivo": motivo})
            continue
        leitura = ler(c, op)
        if leitura.exercitado:
            reais.append({"id": item, "status": "implemented", "proof": "real",
                          "evidence": f"{quando}, {host}, central {commit[:8]}, operação {op}: {leitura.detalhe}"})
        else:
            pendentes.append({"id": item, "proof": "not_run", "motivo": f"operação {op}: {leitura.detalhe}"})
    return {"resultados": [{"grupo": "aprendizado", "items": reais}] if reais else [], "pendentes": pendentes,
            "commit_no_ar": commit, "operacao": op, "lido_em": quando}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--operacao", required=True)
    ap.add_argument("--banco", default=str(RAIZ / "data" / "poc.sqlite3"))
    ap.add_argument("--commit", help="o commit no ar; sem ele, o da saúde (--saude)")
    ap.add_argument("--saude", default=SAUDE)
    a = ap.parse_args(argv)
    commit = a.commit or commit_da_saude(a.saude)
    if not commit:
        print("a saúde não disse o commit no ar", file=sys.stderr)
        return 2
    c = sqlite3.connect(f"file:{Path(a.banco).as_posix()}?mode=ro", uri=True)
    try:
        print(json.dumps(marcar(c, a.operacao, commit), ensure_ascii=False, indent=1))
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
