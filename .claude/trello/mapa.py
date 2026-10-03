"""Funde os mapas dos lotes (`<pasta>/mapa/*.json`) em `.claude/trello/mapa.json` (id do plano → cartão).

Uso: python .claude/trello/mapa.py <pasta com mapa/*.json>
Idempotente: o que já está no mapa.json é mantido; entradas novas entram; conflitos ficam com a mais recente.
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DESTINO = Path(__file__).with_name("mapa.json")


def main() -> int:
    pasta = Path(sys.argv[1]) / "mapa" if len(sys.argv) > 1 else None
    atual = json.load(io.open(DESTINO, encoding="utf-8")) if DESTINO.exists() else {"cartoes": {}, "falhas": [], "atualizado_em": None}
    novos = 0
    for arq in sorted(pasta.glob("*.json")) if pasta and pasta.exists() else []:
        dados = json.load(io.open(arq, encoding="utf-8"))
        for ident, c in (dados.get("criados") or {}).items():
            if ident not in atual["cartoes"]:
                novos += 1
            atual["cartoes"][ident] = {"card": c.get("card"), "url": c.get("url"), "lista": c.get("lista"), "lote": arq.stem}
        for f in dados.get("falhas") or []:
            atual["falhas"].append({**(f if isinstance(f, dict) else {"id": str(f)}), "lote": arq.stem})
    atual["atualizado_em"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    io.open(DESTINO, "w", encoding="utf-8", newline="\n").write(json.dumps(atual, ensure_ascii=False, indent=1) + "\n")
    print(f"mapa.json: {len(atual['cartoes'])} cartões ({novos} novos), {len(atual['falhas'])} falhas registradas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
