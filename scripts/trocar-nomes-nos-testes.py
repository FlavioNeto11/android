"""Troca, nos TESTES, identificadores e nomes de contas reais por valores de exemplo fixos (31.101).

A tabela real → exemplo NUNCA entra no Git: este script a lê de um arquivo local fora do repositório versionado
(`--tabela`, JSON com `handles`, `pedacos`, `fora` e `simulados`). Um script com os nomes como chave guardaria
justamente o que o item tira.

    python scripts/trocar-nomes-nos-testes.py --tabela <arquivo.json>             # ensaio: só contagens
    python scripts/trocar-nomes-nos-testes.py --tabela <arquivo.json> --aplicar   # só os handles
    python scripts/trocar-nomes-nos-testes.py --tabela <arquivo.json> --amplo --aplicar

`--amplo` troca também os pedaços de nome; roda como ÚLTIMA junção no corte de uma suíte, sobre a ponta de integração,
quando a orquestradora marcar (são ~120 arquivos de teste: como ramo paralelo, conflitaria com todos). Reprodutível:
rodar de novo sobre a ponta nova resolve o conflito. Com `--banco`, confere contra o banco (modo só leitura) que nenhum
valor de exemplo é pedaço de nome real e que nenhum pedaço real ficou sem troca. A saída só tem caminhos e contagens.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
#: Onde há dado de teste: os testes do backend e dos scripts, e os `*.test.ts(x)` do frontend.
_ONDE = ("backend/tests", "scripts/tests", "frontend/src")
_SUFIXOS = (".py", ".ts", ".tsx", ".json", ".yaml", ".yml", ".xml", ".txt", ".md")


def arquivos(raiz: Path) -> list[Path]:
    nomes = subprocess.run(["git", "-C", str(raiz), "ls-files", *_ONDE], capture_output=True, text=True,
                           check=True).stdout.split()
    return [raiz / f for f in nomes if re.search(r"(^|/)tests/|\.test\.tsx?$", f) and f.endswith(_SUFIXOS)]


def _caixa(original: str, novo: str) -> str:
    """A caixa do original: TUDO MAIÚSCULO, Primeira maiúscula ou minúsculo."""
    if original.isupper():
        return novo.upper()
    return novo[0].upper() + novo[1:] if original[0].isupper() else novo


def trocador(tabela: dict, amplo: bool):
    """O handle inteiro primeiro (com o número trocado); com `amplo`, depois cada pedaço de nome por palavra inteira."""
    handles = [(re.compile(re.escape(velho), re.IGNORECASE), novo) for velho, novo in tabela["handles"].items()]
    pedacos = tabela["pedacos"] if amplo else {}
    pad = (re.compile(r"\b(" + "|".join(sorted(map(re.escape, pedacos), key=len, reverse=True)) + r")\b",
                      re.IGNORECASE) if pedacos else None)

    def trocar(texto: str) -> str:
        for rx, novo in handles:
            texto = rx.sub(lambda m, novo=novo: _caixa(m.group(0), novo), texto)
        return pad.sub(lambda m: _caixa(m.group(0), pedacos[m.group(0).lower()]), texto) if pad else texto
    return trocar


def nomes_do_banco(banco: Path, fora: set[str]) -> set[str]:
    db = sqlite3.connect(f"file:{banco.as_posix()}?mode=ro", uri=True)
    db.execute("PRAGMA query_only=ON")
    reais: set[str] = set()
    for sql in ("SELECT username FROM instagram_profiles", "SELECT display_name FROM instagram_profiles",
                "SELECT name FROM personas"):
        for (v,) in db.execute(sql):
            reais |= {p for p in re.split(r"[\s._\-0-9]+", (v or "").lower()) if len(p) >= 4}
    return reais - fora


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tabela", type=Path, required=True)
    ap.add_argument("--raiz", type=Path, default=RAIZ)
    ap.add_argument("--banco", type=Path)
    ap.add_argument("--amplo", action="store_true")
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()
    tabela = json.loads(args.tabela.read_text(encoding="utf-8"))
    trocar = trocador(tabela, args.amplo)
    novos = {p for v in [*tabela["pedacos"].values(), *tabela["handles"].values()]
             for p in re.split(r"[.\d]+", v.lower()) if p}
    if novos & {s.lower() for s in tabela.get("simulados", [])}:
        raise SystemExit("um valor de exemplo coincide com um nome do simulated_provider")
    if args.banco:
        reais = nomes_do_banco(args.banco, set(tabela.get("fora", [])))
        if novos & reais:
            raise SystemExit("um valor de exemplo é pedaço de nome real")
        if args.amplo and reais - set(tabela["pedacos"]):
            raise SystemExit(f"{len(reais - set(tabela['pedacos']))} pedaço(s) real(is) sem troca na tabela")
    textos = {f: f.read_bytes().decode("utf-8") for f in arquivos(args.raiz)}
    if args.amplo:
        juntos = "\n".join(textos.values())
        if any(re.search(r"\b" + re.escape(n) + r"\b", juntos, re.IGNORECASE) for n in novos):
            raise SystemExit("um valor de exemplo já existe nos testes: troque-o na tabela")
    mudados = linhas = 0
    for f, bruto in textos.items():
        novo = trocar(bruto)
        if novo != bruto:
            mudados += 1
            linhas += sum(1 for a, b in zip(bruto.splitlines(), novo.splitlines()) if a != b)
            if args.aplicar:
                f.write_bytes(novo.encode("utf-8"))
    print(f"arquivos: {mudados}, linhas: {linhas} ({'aplicado' if args.aplicar else 'ensaio'}"
          f"{', amplo' if args.amplo else ', só handles'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
