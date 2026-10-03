"""Monta o pacote de REGRAVAÇÃO dos cartões já criados: nome e parte técnica novos, saídos do `gerar.py` atual.

A parte para leigos (`**Para quem não é técnico:**` e `**Por que importa:**`) é escrita por agente e fica no cartão: o
agente que regrava lê o cartão, mantém tudo o que vem antes de `**Técnico`, troca o resto pelo `tecnico` daqui e acrescenta
`**O que destrava:**` quando o item está pendente ou bloqueado. Cartão escrito à mão (sem `**Técnico (requisito):**`) não
se regrava.

Uso: python .claude/trello/regravar.py <pasta do gerar.py --so todos> [--partes 5] [--so 16.3,26.1]
Saída: <pasta>/regravar/parte_N.json  [{id, card, url, nome, tecnico, destrava, implementado}]
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

AQUI = Path(__file__).parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pasta")
    ap.add_argument("--partes", type=int, default=5)
    ap.add_argument("--so", default="")
    args = ap.parse_args()
    pasta = Path(args.pasta)
    mapa = json.load(io.open(AQUI / "mapa.json", encoding="utf-8"))["cartoes"]
    so = {x.strip() for x in args.so.split(",") if x.strip()}

    itens = []
    for arq in sorted((pasta / "lotes").glob("*.json")):
        for c in json.load(io.open(arq, encoding="utf-8")):
            alvo = mapa.get(c["id"])
            if not alvo or (so and c["id"] not in so):
                continue
            i = c["desc"].find("**Técnico")
            itens.append({
                "id": c["id"], "card": alvo["card"], "url": alvo.get("url"), "nome": c["nome"],
                "tecnico": c["desc"][i:] if i >= 0 else c["desc"],
                "destrava": c.get("blocker") or "" if c["status"] != "implemented" else "",
                "implementado": c["status"] == "implemented",
            })
    itens.sort(key=lambda x: [int(p) for p in x["id"].split(".")])
    saida = pasta / "regravar"
    saida.mkdir(parents=True, exist_ok=True)
    n = max(1, args.partes)
    tam = -(-len(itens) // n)
    for k in range(n):
        parte = itens[k * tam:(k + 1) * tam]
        if parte:
            json.dump(parte, io.open(saida / f"parte_{k + 1}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"parte_{k + 1}.json: {len(parte)} cartões ({parte[0]['id']} a {parte[-1]['id']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
