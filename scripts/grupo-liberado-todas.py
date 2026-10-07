"""31.253 / ADR-082: põe TODAS as personas no grupo sem aprovação (ordem do dono em 07/10: "todas as personas").

Só pela API do central (`GET /api/personas`, `GET /api/settings`, `PATCH /api/instagram/profiles/{id}`); não lê o banco
nem o `.env`. Sem `--aplicar` é ensaio: só mostra a contagem por grupo antes e o que mudaria. Com `--aplicar`, grava
antes um arquivo de desfazer (id da persona → grupo anterior; nenhum nome, @ ou conta) e mostra a contagem depois.
`--desfazer <arquivo>` devolve cada persona ao grupo anterior.

Uso, da raiz do checkout:

    backend/.venv/Scripts/python.exe scripts/grupo-liberado-todas.py [--url http://127.0.0.1:8000] [--grupo grp-…]
        [--aplicar | --desfazer arquivo.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

#: O desfazer fica nos handoffs do checkout central: num worktree, `<raiz>/.claude/handoffs` não existe.
DESFAZER_EM = Path("C:/git/android/.claude/handoffs")


def _pedir(url: str, metodo: str = "GET", corpo: dict[str, object] | None = None) -> object:
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(url, data=dados, method=metodo, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - só o central local
        return json.loads(resp.read().decode() or "null")


def _personas(base: str) -> list[dict[str, object]]:
    lidas = _pedir(f"{base}/api/personas")
    return [p for p in lidas if isinstance(p, dict)] if isinstance(lidas, list) else []


def _contagem(personas: list[dict[str, object]]) -> dict[str, int]:
    return dict(Counter(str(p.get("policy_group_id") or "-") for p in personas))


def _mudar(base: str, pid: str, grupo: str | None) -> None:
    _pedir(f"{base}/api/instagram/profiles/{pid}", "PATCH", {"policy_group_id": grupo})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--grupo", help="id do grupo; sem ele, o `grupo_sem_aprovacao` em vigor")
    ap.add_argument("--desfazer-em", type=Path, default=DESFAZER_EM, help="pasta do arquivo de desfazer")
    modo = ap.add_mutually_exclusive_group()
    modo.add_argument("--aplicar", action="store_true")
    modo.add_argument("--desfazer", type=Path)
    a = ap.parse_args(argv)
    base = a.url.rstrip("/")

    if a.desfazer:
        anterior = json.loads(a.desfazer.read_text(encoding="utf-8"))
        for pid, grupo in anterior["grupo_anterior"].items():
            _mudar(base, pid, grupo)
        print(json.dumps({"desfeito": len(anterior["grupo_anterior"]), "depois": _contagem(_personas(base))}))
        return 0

    ajustes = _pedir(f"{base}/api/settings")
    grupo = a.grupo or (str(ajustes.get("grupo_sem_aprovacao") or "") if isinstance(ajustes, dict) else "")
    if not grupo:
        print("sem grupo: passe --grupo ou configure grupo_sem_aprovacao", file=sys.stderr)
        return 2
    personas = _personas(base)
    fora = {str(p["id"]): p.get("policy_group_id") for p in personas if p.get("policy_group_id") != grupo}
    saida: dict[str, object] = {"grupo": grupo, "personas": len(personas), "antes": _contagem(personas),
                                "a_mover": len(fora), "aplicado": False}
    if a.aplicar and fora:
        quando = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        a.desfazer_em.mkdir(parents=True, exist_ok=True)
        arquivo = a.desfazer_em / f"grupo-liberado-desfazer-{quando}.json"
        arquivo.write_text(json.dumps({"grupo": grupo, "em": quando, "grupo_anterior": fora}, indent=1),
                           encoding="utf-8")
        for pid in fora:
            _mudar(base, pid, grupo)
        saida.update(aplicado=True, desfazer=str(arquivo), depois=_contagem(_personas(base)))
    print(json.dumps(saida, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
