"""Completa os campos de VOZ das personas e produz a prova antes/depois com a mesma intenção.

Por que existe
--------------
As oito personas do parque foram cadastradas com os traços básicos — personalidade, tom, formalidade, tamanho,
emoji, humor e interesses — e com OITO campos de voz vazios: gírias, estilo em DM, estilo em comentário, com
conhecidos, com desconhecidos, expressões comuns, expressões proibidas e exemplos. Com tão pouco, o modelo tem
quase nada para separar oito vozes, e as contas convergem para o mesmo jeito de escrever (achado #107).

O que ele faz, e o que NÃO faz
------------------------------
* Só preenche campo VAZIO. O que o dono já escreveu nunca é sobrescrito — rodar duas vezes não desfaz edição.
* Casa pelo NOME da persona, não pelo id: id é de cada banco, nome é do parque.
* Sem `--aplicar`, não escreve nada: imprime o que faria. É o padrão.
* `--prova` é uma chamada PAGA de IA por persona (uma prévia cada): só roda quando pedida, e o dono decide.

Onde fica a proposta
--------------------
A proposta de voz é dado de persona de verdade e NÃO fica no Git (31.105): mora na pasta privada da instalação,
`C:/farm/privado/personas-voz.json`, como a tabela de nomes de teste. Outro lugar: `--vozes <arquivo>` ou a
variável `PERSONAS_VOZES`. O formato é `{"personas": {"<nome da persona>": {<os oito campos>}}}`.

Uso
---
    python scripts/personas_completar.py                      # o que falta em cada persona (não escreve nada)
    python scripts/personas_completar.py --aplicar            # preenche os campos vazios
    python scripts/personas_completar.py --prova "dar boa tarde" --saida data/prova-personas.json

A prova roda a MESMA intenção em todas as personas por `POST /api/personas/{id}/preview` — que não publica nada,
não grava interação e não toca aparelho — e escreve um JSON com os textos e uma tabela pronta para colar em
`docs/relatorio-validacao.md`. Rodada antes e depois de `--aplicar`, é o antes/depois do item.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent
#: A pasta privada da instalação: fora de qualquer checkout, fora do Git (31.105).
PRIVADO = Path("C:/farm/privado")
VOZES = PRIVADO / "personas-voz.json"
#: Os campos que este script preenche. É a lista do achado: os que estavam vazios em todas as oito.
CAMPOS = ("slang", "dm_style", "comment_style", "with_known", "with_strangers", "common_phrases",
          "forbidden_phrases", "examples")


def pedir(base: str, metodo: str, rota: str, corpo: Any = None, *, timeout: float = 120.0) -> Any:
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    req = urllib.request.Request(f"{base}{rota}", data=dados, method=metodo,
                                 headers={"Content-Type": "application/json"} if dados else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310 - só loopback, informado pelo dono
            return json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"{metodo} {rota} respondeu {e.code}: {detalhe}") from None
    except urllib.error.URLError as e:
        raise SystemExit(f"não foi possível falar com {base}: {e.reason}. O backend está no ar?") from None


def ler_personas(caminho: Path) -> Any:
    """A chave `personas` do arquivo de dados; sem o arquivo, uma mensagem clara em vez de um traceback."""
    if not caminho.is_file():
        raise SystemExit(f"arquivo de personas não encontrado: {caminho}. Ele fica fora do Git, na pasta privada "
                         f"da instalação ({PRIVADO}); outro lugar, pelo argumento ou pela variável.")
    return json.loads(caminho.read_text(encoding="utf-8"))["personas"]


def vazio(valor: Any) -> bool:
    return valor is None or valor == "" or valor == []


def completar(base: str, aplicar: bool, vozes: Path = VOZES) -> int:
    proposta = ler_personas(vozes)
    personas = pedir(base, "GET", "/api/personas")
    mexidas = 0
    for p in personas:
        traits = dict(p.get("traits") or {})
        nova = proposta.get(p["name"])
        faltando = [c for c in CAMPOS if vazio(traits.get(c))]
        if not faltando:
            print(f"  {p['name']}: completa")
            continue
        if nova is None:
            print(f"  {p['name']}: faltam {', '.join(faltando)} — SEM proposta em {vozes.name}")
            continue
        preencher = {c: nova[c] for c in faltando if c in nova}
        print(f"  {p['name']}: preencher {', '.join(preencher)}" + ("" if aplicar else "  (simulação)"))
        if not aplicar or not preencher:
            continue
        pedir(base, "PATCH", f"/api/personas/{p['id']}", {"traits": {**traits, **preencher}})
        mexidas += 1
    if not aplicar:
        print("\nNada foi escrito. Rode de novo com --aplicar para preencher.")
    return mexidas


def prova(base: str, intencao: str, saida: Path) -> None:
    """A MESMA intenção em todas as personas, uma prévia cada. Nada é publicado; nada é gravado."""
    # Desde a migração 047 a persona É o perfil: o id é o mesmo, e `username` vem vazio em quem ainda não tem conta.
    linhas: list[dict[str, Any]] = []
    for p in pedir(base, "GET", "/api/personas"):
        corpo = {"kind": "dm_initiate", "brief": intencao, "profile_id": p["id"]}
        draft = pedir(base, "POST", f"/api/personas/{p['id']}/preview", corpo)
        texto = (draft.get("content") or "").strip()
        linhas.append({"persona": p["name"], "perfil": p.get("username"),
                       "campos_de_voz_vazios": p.get("voice_gaps") or [], "texto": texto,
                       "caracteres": len(texto), "recusou": bool(draft.get("refused"))})
        print(f"  {p['name']}: {len(texto)} chars · {texto[:80]}")
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(json.dumps({"intencao": intencao, "resultados": linhas}, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    distintos = len({linha["texto"].casefold() for linha in linhas if linha["texto"]})
    print(f"\n{len(linhas)} personas, {distintos} textos distintos. JSON em {saida}")
    print("\nTabela para docs/relatorio-validacao.md:\n")
    print("| persona | perfil | campos de voz vazios | chars | texto |")
    print("| --- | --- | ---: | ---: | --- |")
    for linha in linhas:
        print(f"| {linha['persona']} | {linha['perfil'] or '—'} | {len(linha['campos_de_voz_vazios'])} "
              f"| {linha['caracteres']} | {linha['texto'].replace('|', '/')} |")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="backend (padrão: loopback)")
    ap.add_argument("--aplicar", action="store_true", help="escreve de verdade; sem isto, só mostra")
    ap.add_argument("--prova", metavar="INTENCAO",
                    help="roda a MESMA intenção em todas as personas (chamada paga de IA, uma por persona)")
    ap.add_argument("--saida", type=Path, default=RAIZ / "data" / "prova-personas.json")
    ap.add_argument("--vozes", type=Path, default=Path(os.environ.get("PERSONAS_VOZES") or VOZES),
                    help=f"a proposta de voz (padrão: PERSONAS_VOZES ou {VOZES})")
    args = ap.parse_args(argv)

    if args.prova:
        prova(args.base, args.prova, args.saida)
        return 0
    print(f"Personas em {args.base}:")
    completar(args.base, args.aplicar, args.vozes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
