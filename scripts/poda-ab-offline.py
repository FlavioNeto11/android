"""31.52: o A/B offline da poda do 31.35 sobre árvores REAIS gravadas pelo diagnóstico (`ai.diagnostico_arvore_aparelhos`).

Lê as evidências `hierarchy` com a nota "31.52" do banco (somente leitura) ou os arquivos JSON passados, remonta a
árvore e mede, por árvore, os caracteres das linhas do prompt do ator SEM e COM a poda (a mesma `prompt_lines` do
executor, o mesmo teto de elementos). Imprime só números e ids: nenhum texto da página.

Uso: backend/.venv/Scripts/python.exe scripts/poda-ab-offline.py [--banco C:/git/android/data/poc.sqlite3]
     [--evidencias C:/git/android/data/evidence] [arquivo.json ...]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "backend"))

from app.automation.hierarchy import UiElement, UiTree  # noqa: E402
from app.taskqueue.executor import UI_DO_NAVEGADOR  # noqa: E402

MAX_LINHAS = 140     # `ai.max_hierarchy_elements` padrão; passe --max para outro


def arvore(corpo: dict[str, object]) -> UiTree:
    elementos = [UiElement(**{**e, "bounds": tuple(e["bounds"])}) for e in corpo["elements"]]  # type: ignore[union-attr]
    return UiTree(elements=elementos, packages=[str(corpo.get("package") or "")], sensitive=False)


def medir(corpo: dict[str, object], max_linhas: int) -> dict[str, object]:
    t = arvore(corpo)
    ocultar = UI_DO_NAVEGADOR.get(str(corpo.get("package") or ""), frozenset())
    inteira = sum(len(x) + 1 for x in t.prompt_lines(max_linhas))
    podada = sum(len(x) + 1 for x in t.prompt_lines(max_linhas, ocultar=ocultar))
    return {"elementos": len(t.elements), "chars_sem_poda": inteira, "chars_com_poda": podada,
            "reducao": round(1 - podada / inteira, 3) if inteira else 0.0}


def do_banco(banco: Path, evidencias: Path) -> list[tuple[str, Path]]:
    c = sqlite3.connect(f"file:{banco.as_posix()}?mode=ro", uri=True)
    c.execute("PRAGMA query_only=ON")
    linhas = c.execute("SELECT run_id, path FROM evidence WHERE kind='hierarchy' AND note LIKE '31.52:%' "
                       "AND path IS NOT NULL ORDER BY ts, id").fetchall()
    c.close()
    return [(str(r), evidencias / str(p)) for r, p in linhas]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("arquivos", nargs="*")
    p.add_argument("--banco", default="C:/git/android/data/poc.sqlite3")
    p.add_argument("--evidencias", default="C:/git/android/data/evidence")
    p.add_argument("--max", type=int, default=MAX_LINHAS)
    a = p.parse_args()
    fontes = ([("arquivo", Path(x)) for x in a.arquivos] if a.arquivos
              else do_banco(Path(a.banco), Path(a.evidencias)))
    medidas = []
    for run_id, caminho in fontes:
        m = medir(json.loads(caminho.read_text(encoding="utf-8")), a.max)
        medidas.append(m)
        print(json.dumps({"run": run_id, **m}))
    if medidas:
        print(json.dumps({"arvores": len(medidas),
                          "reducao_mediana": statistics.median(m["reducao"] for m in medidas),  # type: ignore[misc]
                          "chars_sem_poda_mediana": statistics.median(m["chars_sem_poda"] for m in medidas),  # type: ignore[misc]
                          "chars_com_poda_mediana": statistics.median(m["chars_com_poda"] for m in medidas)}))  # type: ignore[misc]
    else:
        print(json.dumps({"arvores": 0, "nota": "nenhuma árvore do 31.52 gravada ainda"}))


if __name__ == "__main__":
    main()
