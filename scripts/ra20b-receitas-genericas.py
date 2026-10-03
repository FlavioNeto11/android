"""RA-20 fatia B: semeia a chave genérica das receitas que já existem (passe ÚNICO e idempotente).

O que faz e o que deixa de fora: `backend/app/taskqueue/receitas_genericas.py`. Em uma linha: por chave genérica entra
UMA candidata, copiada da ativa elegível com mais evidência; as receitas atuais não mudam.

- PADRÃO = ENSAIO (`--ensaio`): copia o banco (API de backup do SQLite, origem em `mode=ro`), roda o passe de verdade na
  CÓPIA, imprime o relatório e apaga a cópia. O original não é tocado.
- `--aplicar` exige `--backup CAMINHO` (arquivo ou pasta que exista) e confere que a maior migração do banco é a do
  código (abrir o banco NÃO migra). Só com o OK da Android e da orquestradora (combinado em 03/10).
- O relatório tem ids, chaves de etapa e contagens; nenhum texto de pós-condição, parâmetro ou ação.

A partir da raiz do repositório:
    backend/.venv/Scripts/python.exe scripts/ra20b-receitas-genericas.py --banco data/poc.sqlite3
    backend/.venv/Scripts/python.exe scripts/ra20b-receitas-genericas.py --banco data/poc.sqlite3 --aplicar --backup data/backups/<pasta>
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ / "backend") not in sys.path:
    sys.path.insert(0, str(RAIZ / "backend"))

from app.db import Database  # noqa: E402
from app.modules.learning.application.nativos import D1Nativo  # noqa: E402
from app.modules.learning.infrastructure import backfill_licoes as migracao  # noqa: E402
from app.modules.learning.infrastructure.ligar_nativos import LeituraSql, OuvinteD1DasReceitas, TrilhaDasLojas  # noqa: E402
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository  # noqa: E402
from app.taskqueue.receitas_genericas import Relatorio, semear  # noqa: E402
from app.taskqueue.recipes import RecipeStore  # noqa: E402
from app.util import now  # noqa: E402


def copiar(origem: Path, destino: Path) -> None:
    """Uma cópia consistente do banco (WAL incluso) para o ensaio; a origem é aberta SÓ PARA LEITURA."""
    de = sqlite3.connect(f"file:{origem.as_posix()}?mode=ro", uri=True)
    para = sqlite3.connect(str(destino))
    try:
        de.backup(para)
    finally:
        para.close()
        de.close()


def loja(db: Database) -> RecipeStore:
    """A loja com o ouvinte do livro, como o central a monta (`ligar_nativos.ligar`): o veto da pessoa vale e a
    semeada entra na trilha, com o motivo, por conta do sistema. Sem aviso de mudança (o passe não é um gesto)."""
    leitura = LeituraSql(db)
    d1 = D1Nativo(SqlLearningRepository(db), com_prova=lambda: True, relogio=now,
                  execucao_real=leitura.execucao_real)
    return RecipeStore(db, OuvinteD1DasReceitas(d1, TrilhaDasLojas(db), db))


def _passe(db: Database, *, ensaio: bool) -> Relatorio:
    try:
        migracao.conferir_migracao(db)
    except (migracao.BancoDiferenteDoCodigo, migracao.MigracaoAusente) as exc:
        if not ensaio:
            raise
        print(f"AVISO: {exc}", file=sys.stderr)       # o ensaio conta mesmo assim; o --aplicar abortaria
    with db.tx():
        return semear(db, loja(db), gravar=True)


def executar(banco: Path, *, aplicar: bool) -> Relatorio:
    if aplicar:
        db = Database(banco)                          # abrir NÃO migra
        try:
            return _passe(db, ensaio=False)
        finally:
            db.close()
    pasta = Path(tempfile.mkdtemp(prefix="ra20b-ensaio-"))
    copia = pasta / "ensaio.sqlite3"
    try:
        copiar(banco, copia)
        db = Database(copia)
        try:
            return _passe(db, ensaio=True)
        finally:
            db.close()
    finally:
        for arquivo in pasta.iterdir():
            arquivo.unlink()
        os.rmdir(pasta)


def imprimir(rel: Relatorio, *, aplicar: bool) -> None:
    for g in rel.grupos:
        pacote, versao, _assinatura, variante, generico = g.chave
        ids = ", ".join(f"{r['id']}({r['step_key']}, ok {r['replay_ok']}, falhas {r['replay_fail']})" for r in g.receitas)
        print(f"- {pacote} {versao} [{variante or 'sem variante'}] chave genérica {generico}: {ids}")
        print(f"    escolhida: {g.escolhida if g.escolhida is not None else '-'}; {g.motivo}"
              + (f"; nova receita {g.semeada}" if g.semeada else ""))
    print(("APLICADO" if aplicar else "ENSAIO (cópia descartada; nada foi gravado)") + ": "
          + ", ".join(f"{k}={v}" for k, v in rel.contagem.items()))


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="RA-20 B: semeia a chave genérica das receitas (ensaio por padrão).")
    ap.add_argument("--banco", required=True, type=Path, help="o poc.sqlite3 (sem padrão, de propósito)")
    ap.add_argument("--ensaio", action="store_true", help="o padrão: ensaio numa cópia; nada é gravado")
    ap.add_argument("--aplicar", action="store_true", help="grava no banco dado (faça backup antes)")
    ap.add_argument("--backup", type=Path, metavar="CAMINHO",
                    help="arquivo ou pasta do backup feito antes (obrigatório com --aplicar: a prova de que existe)")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    ap = _parser()
    args = ap.parse_args(argv)
    if args.aplicar and args.ensaio:
        ap.error("--aplicar e --ensaio são excludentes")
    if args.aplicar and (args.backup is None or not args.backup.exists()):
        ap.error("--aplicar exige --backup CAMINHO de um arquivo ou pasta que exista (faça o backup antes)")
    banco: Path = args.banco
    if str(banco).startswith(("postgres://", "postgresql://")) or not banco.is_file():
        print(f"erro: {banco} não é um arquivo SQLite existente (o passe só opera SQLite local)", file=sys.stderr)
        return 2
    try:
        rel = executar(banco, aplicar=bool(args.aplicar))
    except (migracao.BancoDiferenteDoCodigo, migracao.MigracaoAusente) as exc:
        print(f"ABORTADO: {exc}", file=sys.stderr)
        return 2
    imprimir(rel, aplicar=bool(args.aplicar))
    return 0


if __name__ == "__main__":
    sys.exit(main())
