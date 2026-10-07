"""Frente GitHub (29.190): confere um JSON de resultado do plano-100 ANTES de ele ir para o `aplicar`.

    python scripts/resultado_confere.py .claude/handoffs/github-resultado-29-190.json [outro.json ...]
    python scripts/resultado_confere.py --gravar rascunho.json .claude/handoffs/github-resultado-29-190.json

Nasceu do deploy 58: um resultado com `status: "done"` quebrou o `aplicar`. As regras vêm de `scripts/claude-plan-100.py`
(constantes `ESTADOS` e `PROVAS`, IDs do plano e a própria `validar`, usada item a item para listar TODOS os problemas, não só o
primeiro), mais o que o livro-razão cobra em texto livre e o `validar` não vê:

  - `proof` é exatamente `real`, `simulated` ou `not_run`: a descrição vai em `evidence`;
  - `real` traz data, máquina (ou runner) e commit ou id de execução na evidência;
  - `simulated` cita `arquivo::teste` ou um arquivo de teste;
  - `not_run` diz por que (evidência ou bloqueio);
  - `blocked` traz o motivo em `blocker`.

`--gravar` só grava (de forma atômica) se não houver problema. Só LÊ o plano; não chama IA, rede nem `gh`.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
_DATA = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{2}/\d{2}(?:/\d{2,4})?\b")
# hex com letra E dígito: "defaced" e um número de 7 dígitos não valem como commit
_COMMIT = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
_ID_EXECUCAO = re.compile(r"\b(?:run|id|execu[cç][aã]o)\D{0,12}\d{6,}\b", re.IGNORECASE)
_MAQUINA = re.compile(r"m[aá]quina|ubuntu|runner|hospedad|notebook|\bhost\b", re.IGNORECASE)
# arquivo de teste de verdade (test_x.py, x.test.ts, pasta tests/) ou arquivo::teste; um .py qualquer não prova nada
_TESTE = re.compile(r"\w::\w|\btest_\w+|\w\.test\.[jt]sx?\b|\w_test\.py\b|\btests?/")


def _plano():
    """O módulo do livro-razão (nome com hífen: carregado pelo caminho)."""
    spec = importlib.util.spec_from_file_location("claude_plan_100", RAIZ / "scripts" / "claude-plan-100.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)  # type: ignore[union-attr]
    return modulo


def ids_do_plano(plano) -> set[str]:
    """Os IDs da tabela do plano (mesma expressão do `carregar`, sem exigir os pacotes gerados, que ficam fora do Git)."""
    config = plano.ler_json(plano.CONFIG)
    texto = (plano.RAIZ / config["plan"]).read_text(encoding="utf-8")
    return set(re.findall(r"^\|\s*((?:\d+|T)\.\d+)\s*\|", texto, re.M))


def _do_nivel(item: dict) -> list[str]:
    """O que a prova de cada nível tem de dizer na evidência (o `validar` só exige que haja evidência)."""
    nivel = item.get("proof")
    # `blocker` é o motivo do bloqueio: só completa o not_run; real e simulated se provam na evidência
    texto = str(item.get("evidence") or "")
    if nivel == "not_run":
        texto += f" {item.get('blocker') or ''}"
    if nivel == "real":
        faltas = []
        if not _DATA.search(texto):
            faltas.append("data")
        if not _MAQUINA.search(texto):
            faltas.append("máquina ou runner")
        if not (_COMMIT.search(texto) or _ID_EXECUCAO.search(texto)):
            faltas.append("commit ou id de execução")
        return [f"prova real sem {', '.join(faltas)}"] if faltas else []
    if nivel == "simulated" and not _TESTE.search(texto):
        return ["prova simulated sem arquivo::teste"]
    if nivel == "not_run" and not texto.strip():
        return ["prova not_run sem o motivo"]
    return []


def problemas(resultado: object, ids_validos: set[str], plano=None) -> list[str]:
    """Lista de problemas (vazia = pode ir para o `aplicar`), um por item, com o ID na frente."""
    plano = plano or _plano()
    grupos = resultado.get("resultados") if isinstance(resultado, dict) else None
    if not isinstance(grupos, list) or not grupos:
        return ["o arquivo precisa ser {\"resultados\": [ ... ]} com ao menos um grupo"]
    achados: list[str] = []
    for g, grupo in enumerate(grupos):
        if not isinstance(grupo, dict) or not isinstance(grupo.get("items"), list):
            achados.append(f"grupo {g}: sem a lista `items`")
            continue
        entregues = {i.get("id") for i in grupo["items"] if isinstance(i, dict)}
        for pedido in grupo.get("solicitados") or []:
            if pedido not in entregues:
                achados.append(f"{str(pedido)[:40]}: pedido em `solicitados` e sem linha em `items`")
        for item in grupo["items"]:
            if not isinstance(item, dict):
                achados.append(f"grupo {g}: item que não é objeto")
                continue
            ident = item.get("id")
            if ident not in ids_validos:
                achados.append(f"{str(ident)[:40]}: ID que não existe no plano")
                continue
            if item.get("status") not in plano.ESTADOS:
                achados.append(f"{ident}: status {str(item.get('status'))[:40]!r} (aceitos: {', '.join(sorted(plano.ESTADOS))})")
            if item.get("proof") not in plano.PROVAS:
                achados.append(f"{ident}: proof {str(item.get('proof'))[:40]!r} (aceitos: {', '.join(sorted(plano.PROVAS))}; a descrição vai em `evidence`)")
            if item.get("status") in plano.ESTADOS and item.get("proof") in plano.PROVAS:
                unico = {"resultados": [{"grupo": grupo.get("grupo", ""), "solicitados": [ident], "items": [item]}]}
                try:
                    plano.validar(unico, ids_validos)
                except plano.ErroDoPlano as erro:
                    achados.append(str(erro))
                achados += [f"{ident}: {m}" for m in _do_nivel(item)]
    return achados


def conferir_arquivo(caminho: Path, ids_validos: set[str], plano=None) -> list[str]:
    try:
        resultado = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError) as erro:
        return [f"não consegui ler o JSON ({type(erro).__name__})"]
    return problemas(resultado, ids_validos, plano)


def gravar(rascunho: Path, destino: Path, ids_validos: set[str], plano=None) -> list[str]:
    """Confere o rascunho e, sem problema, grava no destino de forma atômica. Devolve os problemas (vazio = gravou)."""
    achados = conferir_arquivo(rascunho, ids_validos, plano)
    if achados:
        return achados
    temporario = destino.with_suffix(destino.suffix + ".tmp")
    try:
        temporario.write_bytes(rascunho.read_bytes())  # bytes: o que se grava é o que se conferiu, sem troca de quebra de linha
        os.replace(temporario, destino)
    except OSError:
        temporario.unlink(missing_ok=True)
        raise
    return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Confere o JSON de resultado do plano-100 antes do `aplicar`.")
    ap.add_argument("arquivos", nargs="*", type=Path, help="JSONs a conferir")
    ap.add_argument("--gravar", nargs=2, metavar=("RASCUNHO", "DESTINO"), type=Path,
                    help="confere RASCUNHO e só então grava em DESTINO")
    a = ap.parse_args(argv)
    for f in (sys.stdout, sys.stderr):
        if hasattr(f, "reconfigure"):
            f.reconfigure(encoding="utf-8", errors="replace")
    if not a.arquivos and not a.gravar:
        ap.error("informe arquivos a conferir ou --gravar RASCUNHO DESTINO")
    plano = _plano()
    try:
        ids = ids_do_plano(plano)
    except (OSError, ValueError, KeyError) as erro:
        print(f"erro: não consegui ler os IDs do plano: {erro}", file=sys.stderr)
        return 1
    ruim = False
    if a.gravar:
        try:
            achados = gravar(a.gravar[0], a.gravar[1], ids, plano)
        except OSError as erro:
            print(f"erro: não consegui gravar ({type(erro).__name__})", file=sys.stderr)
            return 1
        if achados:
            ruim = True
            print(f"NÃO gravado ({a.gravar[1]}):", *achados, sep="\n  - ")
        else:
            print(f"gravado: {a.gravar[1]}")
    for caminho in a.arquivos:
        achados = conferir_arquivo(caminho, ids, plano)
        if achados:
            ruim = True
            print(f"{caminho}: {len(achados)} problema(s)", *achados, sep="\n  - ")
        else:
            print(f"{caminho}: ok")
    return 1 if ruim else 0


if __name__ == "__main__":
    sys.exit(main())
