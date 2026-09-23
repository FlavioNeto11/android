"""Cria as personas de `scripts/personas-novas.json` que ainda não existem — por NOME, nunca duas vezes.

Par de `personas_completar.py`: aquele preenche o que falta nas personas que já existem; este cria as que faltam
para os aparelhos que nunca tiveram uma (os seis do worker, em 23/09/2026). Nenhuma nasce vinculada a perfil:
o vínculo depende de conta registrada pelo dono, na tela do perfil.

Uso
---
    python scripts/personas_criar.py            # o que seria criado (não escreve nada)
    python scripts/personas_criar.py --aplicar  # cria as que faltam
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from personas_completar import pedir  # mesmo cliente HTTP, mesmo loopback

RAIZ = Path(__file__).resolve().parent.parent
NOVAS = RAIZ / "scripts" / "personas-novas.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://127.0.0.1:8000/api")
    ap.add_argument("--aplicar", action="store_true", help="cria de verdade; sem isto só imprime")
    args = ap.parse_args()

    propostas = json.loads(NOVAS.read_text(encoding="utf-8"))["personas"]
    existentes = {p["name"].strip().lower() for p in pedir(args.base, "GET", "/personas")}
    print(f"Personas em {args.base}: {len(existentes)} existentes, {len(propostas)} propostas")
    criadas = 0
    for p in propostas:
        if p["name"].strip().lower() in existentes:
            print(f"  {p['name']}: já existe, nada a fazer")
            continue
        if not args.aplicar:
            print(f"  {p['name']}: seria criada  (simulação)")
            continue
        corpo = {"name": p["name"], "summary": p.get("summary", ""), "persona_prompt": p.get("persona_prompt", ""),
                 "traits": p.get("traits", {})}
        nova = pedir(args.base, "POST", "/personas", corpo)
        lacunas = nova.get("voice_gaps") or []
        print(f"  {p['name']}: criada ({nova['id']})" + (f" — AINDA FALTAM {lacunas}" if lacunas else " — voz completa"))
        criadas += 1
    if not args.aplicar:
        print("\nNada foi escrito. Rode de novo com --aplicar para criar.")
    else:
        print(f"\n{criadas} persona(s) criada(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
