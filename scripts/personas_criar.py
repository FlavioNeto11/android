"""Cria as personas do arquivo de personas novas que ainda não existem — por NOME, nunca duas vezes.

Par de `personas_completar.py`: aquele preenche o que falta nas personas que já existem; este cria as que faltam
para os aparelhos que nunca tiveram uma (os seis do worker, em 23/09/2026). Nenhuma nasce vinculada a perfil:
o vínculo depende de conta registrada pelo dono, na tela do perfil.

O arquivo é dado de persona de verdade e NÃO fica no Git (31.105): mora em `C:/farm/privado/personas-novas.json`,
como a tabela de nomes de teste. Outro lugar: `--novas <arquivo>` ou a variável `PERSONAS_NOVAS`. O formato é
`{"personas": [{"name", "summary", "persona_prompt", "traits"}]}`.

Uso
---
    python scripts/personas_criar.py            # o que seria criado (não escreve nada)
    python scripts/personas_criar.py --aplicar  # cria as que faltam
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from personas_completar import PRIVADO, ler_personas, pedir  # mesmo cliente HTTP, mesmo loopback, mesma pasta

NOVAS = PRIVADO / "personas-novas.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://127.0.0.1:8000/api")
    ap.add_argument("--aplicar", action="store_true", help="cria de verdade; sem isto só imprime")
    ap.add_argument("--novas", type=Path, default=Path(os.environ.get("PERSONAS_NOVAS") or NOVAS),
                    help=f"as personas a criar (padrão: PERSONAS_NOVAS ou {NOVAS})")
    args = ap.parse_args()

    propostas = ler_personas(args.novas)
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
