"""Telas aprendidas (ADR-054, fatia 5 = item 18.8): a prova offline sobre as observações REAIS e a ponte para o
repositório. Só leitura, sem IA (US$ 0).

- `minerar` (o padrão): as candidatas que as telas vistas (`learning_signals.kind='tela_vista'`) gerariam agora. Com
  `--sem-regra TELA` é o DEIXA-UM-FORA: numa cópia do conhecimento do app SEM aquela tela (nem no estado conhecido),
  as amostras dela viram desconhecidas, e o minerador precisa reencontrá-la — sem `thread`, uma candidata com o
  compositor da conversa; sem `feed`, uma candidata de casa com ids do feed. O banco é aberto SÓ PARA LEITURA
  (`mode=ro`) e nada é gravado; `--dry-run` é aceito por clareza, mas este comando nunca escreve.
- `exportar --app PACOTE`: `GET /api/aprendizado/export?kind=tela&app=PACOTE` no central — o fragmento YAML com a
  proveniência, já conferido pelo carregador. O token da API (quando houver) vem de `API_TOKEN` no ambiente e NUNCA
  é impresso; o loopback não exige credencial.

Exemplos (no central, a partir da raiz do repositório):
    backend/.venv/Scripts/python.exe scripts/aprendizado-telas.py --dry-run --sem-regra thread --sem-regra feed
    backend/.venv/Scripts/python.exe scripts/aprendizado-telas.py exportar --app com.exemplo.app > fragmento.yaml
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ / "backend") not in sys.path:
    sys.path.insert(0, str(RAIZ / "backend"))

from app.modules.learning.domain import telas as dominio  # noqa: E402
from app.modules.learning.infrastructure.ligar_telas import (ConhecimentoDoRepositorio,  # noqa: E402
                                                             LeituraDeTelasSql)
from app.util import now, to_iso  # noqa: E402

BANCO_PADRAO = RAIZ / "data" / "poc.sqlite3"


class SoLeitura:
    """O banco SQLite do central em `mode=ro`: o máximo que este script faz é um SELECT."""

    def __init__(self, caminho: Path) -> None:
        self._conexao = sqlite3.connect(f"file:{caminho.as_posix()}?mode=ro", uri=True)
        self._conexao.row_factory = lambda cur, row: {d[0]: v for d, v in zip(cur.description, row)}

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return list(self._conexao.execute(sql, params).fetchall())

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        linha: dict[str, Any] | None = self._conexao.execute(sql, params).fetchone()
        return linha

    def close(self) -> None:
        self._conexao.close()


def _candidata(c: dominio.Candidata) -> dict[str, Any]:
    return {"tela": c.regra.tela, "ids_todos": list(c.regra.ids_todos), "casa": c.regra.casa,
            "contexto": c.contexto, "observacoes": len(c.observacoes),
            "execucoes": len({o.run_id for o in c.observacoes if o.run_id}),
            "aparelhos": len({o.instance_id for o in c.observacoes if o.instance_id}),
            "simuladas": sum(1 for o in c.observacoes if o.simulated)}


def minerar(db: Any, *, apps: list[str], sem: list[str], minimo: int, dias: int,
            pasta: Path | None = None) -> list[dict[str, Any]]:
    """Por app (e por tela deixada de fora): as candidatas, sem gravar nada."""
    leitura = LeituraDeTelasSql(db)
    declarado = ConhecimentoDoRepositorio(pasta) if pasta is not None else ConhecimentoDoRepositorio()
    alvo = apps or sorted({str(r["app_package"]) for r in db.query(
        "SELECT DISTINCT app_package FROM learning_signals WHERE kind='tela_vista'") if r["app_package"]})
    desde = to_iso(now() - timedelta(days=dias))
    saida: list[dict[str, Any]] = []
    for app in alvo:
        declaradas = declarado.declaradas(app)
        if declaradas is None:
            saida.append({"app": app, "erro": "o app não tem telas.yaml no repositório"})
            continue
        desconhecidas = leitura.observacoes(app, desde=desde, limite=5000)
        amostras = leitura.amostras(app, por_tela=dominio.AMOSTRAS_POR_TELA)
        for tela in sem or [None]:
            if tela is not None and tela not in declaradas.nomes():
                saida.append({"app": app, "sem_regra": tela, "erro": "o telas.yaml não declara esta tela"})
                continue
            candidatas = (dominio.propor(desconhecidas, amostras, declaradas, minimo=minimo) if tela is None
                          else dominio.deixa_um_fora(declaradas, tela, [*desconhecidas, *amostras], minimo=minimo))
            saida.append({"app": app, "sem_regra": tela, "desconhecidas": len(desconhecidas),
                          "amostras": len(amostras), "candidatas": [_candidata(c) for c in candidatas]})
    return saida


def exportar(base: str, app: str, token: str | None, timeout: float = 30.0) -> str:
    consulta = urllib.parse.urlencode({"kind": "tela", "app": app})
    cabecalhos = {"Accept": "text/yaml"}
    if token:
        cabecalhos["Authorization"] = f"Bearer {token}"
    pedido = urllib.request.Request(f"{base.rstrip('/')}/api/aprendizado/export?{consulta}", headers=cabecalhos)
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as resposta:  # noqa: S310 - URL é do próprio painel
            texto: str = resposta.read().decode("utf-8")
            return texto
    except urllib.error.HTTPError as erro:  # o corpo explica a recusa; o token não aparece nele
        raise SystemExit(f"o central recusou ({erro.code}): {erro.read().decode('utf-8', 'replace')[:400]}") from None


def _imprimir(linhas: list[dict[str, Any]]) -> None:
    for linha in linhas:
        titulo = linha["app"] + (f" sem a regra '{linha['sem_regra']}'" if linha.get("sem_regra") else "")
        if "erro" in linha:
            print(f"{titulo}: {linha['erro']}")
            continue
        print(f"{titulo}: {linha['desconhecidas']} telas desconhecidas e {linha['amostras']} amostras declaradas")
        if not linha["candidatas"]:
            print("  nenhuma candidata")
        for c in linha["candidatas"]:
            casa = " · CASA" if c["casa"] else ""
            print(f"  {c['tela']}{casa}: ids_todos={c['ids_todos']} · {c['observacoes']} observações em "
                  f"{c['execucoes']} execuções e {c['aparelhos']} aparelho(s) (simuladas: {c['simuladas']})")


def principal(argv: list[str] | None = None, *, db: Any = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("comando", nargs="?", choices=("minerar", "exportar"), default="minerar")
    p.add_argument("--db", type=Path, default=BANCO_PADRAO, help="o banco SQLite do central (aberto só para leitura)")
    p.add_argument("--app", action="append", default=[], help="o pacote (repetível); sem ele, todos os observados")
    p.add_argument("--sem-regra", action="append", default=[], help="deixa-um-fora: a tela do telas.yaml a tirar")
    p.add_argument("--minimo", type=int, default=3, help="observações para uma candidata (aprendizado.telas)")
    p.add_argument("--dias", type=int, default=30, help="janela das telas desconhecidas")
    p.add_argument("--pasta", type=Path, default=None, help="raiz dos pacotes de app (padrão: a do repositório)")
    p.add_argument("--dry-run", action="store_true", help="aceito por clareza: este script nunca grava nada")
    p.add_argument("--json", action="store_true", help="saída em JSON")
    p.add_argument("--url", default="http://127.0.0.1:8000", help="o central (exportar)")
    args = p.parse_args(argv)
    if args.comando == "exportar":
        if len(args.app) != 1:
            p.error("exportar precisa de exatamente um --app")
        sys.stdout.write(exportar(args.url, args.app[0], os.environ.get("API_TOKEN")))
        return 0
    proprio = db is None
    banco = SoLeitura(args.db) if proprio else db
    try:
        linhas = minerar(banco, apps=args.app, sem=args.sem_regra, minimo=args.minimo, dias=args.dias,
                         pasta=args.pasta)
    finally:
        if proprio:
            banco.close()
    if args.json:
        print(json.dumps(linhas, ensure_ascii=False, indent=2))
    else:
        _imprimir(linhas)
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
