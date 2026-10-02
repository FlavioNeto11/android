"""Backfill ÚNICO e idempotente das lições (ADR-054, pacote A7) das execuções reais anteriores à migração 055.

O digest que minera lições só existe desde a 055; as execuções reais fechadas antes dela nunca passaram por ele. Este
comando passa SÓ os mineradores de lição (`licoes.contraste` e `licoes.plano`) por elas, pela mesma lógica de
produção, e grava candidatas no livro. Modo fixo `shadow` (nada é publicado nem muda modo), sem IA, sem aparelho, sem
rede. Rodar de novo não duplica item, evidência nem transição.

- PADRÃO = ENSAIO (`--dry-run`): copia o banco para um arquivo temporário (API de backup do SQLite, origem em
  `mode=ro`), roda a lógica de verdade na CÓPIA e relata; o original não é tocado. `--aplicar` grava no banco dado.
- Antes de qualquer escrita confere que a maior migração do banco é IGUAL à do código que roda; se não for, aborta
  sem gravar (nunca aplica migração; o banco é aberto sem migrar).
- Escolha as execuções com `--antes-da-055` (reais, terminais, terminadas antes do `applied_at` da 055) ou com
  `--run-id ID` (repetível).
- O relatório só tem ids, estados, escopo e contagens; nunca texto de lição nem de persona.

Faça BACKUP do banco antes de `--aplicar`. Exemplos (a partir da raiz do repositório):
    backend/.venv/Scripts/python.exe scripts/aprendizado-backfill-licoes.py --banco data/poc.sqlite3 --antes-da-055
    backend/.venv/Scripts/python.exe scripts/aprendizado-backfill-licoes.py --banco data/poc.sqlite3 --antes-da-055 --aplicar
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ / "backend") not in sys.path:
    sys.path.insert(0, str(RAIZ / "backend"))

from app.db import Database  # noqa: E402
from app.modules.learning.infrastructure import backfill_licoes as bf  # noqa: E402


def copiar(origem: Path, destino: Path) -> None:
    """Uma cópia consistente do banco (WAL incluso) para o ensaio; a origem é aberta SÓ PARA LEITURA."""
    de = sqlite3.connect(f"file:{origem.as_posix()}?mode=ro", uri=True)
    para = sqlite3.connect(str(destino))
    try:
        de.backup(para)
    finally:
        para.close()
        de.close()


def _selecionar(db: Database, args: argparse.Namespace) -> bf.Selecao:
    return bf.antes_da_055(db) if args.antes_da_055 else bf.da_lista(db, args.run_id)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Backfill único das lições anteriores à 055 (ensaio por padrão).")
    ap.add_argument("--banco", required=True, type=Path, help="o poc.sqlite3 (sem padrão, de propósito)")
    ap.add_argument("--run-id", action="append", default=[], metavar="ID", help="uma execução (repetível)")
    ap.add_argument("--antes-da-055", action="store_true",
                    help="as execuções reais terminadas antes do applied_at da migração 055")
    ap.add_argument("--dry-run", action="store_true", help="o padrão: ensaio numa cópia; nada é gravado")
    ap.add_argument("--aplicar", action="store_true", help="grava no banco dado (faça backup antes)")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    ap = _parser()
    args = ap.parse_args(argv)
    if bool(args.run_id) == bool(args.antes_da_055):
        ap.error("diga exatamente um: --antes-da-055 ou --run-id ID")
    if args.aplicar and args.dry_run:
        ap.error("--aplicar e --dry-run são excludentes")
    banco: Path = args.banco
    if str(banco).startswith(("postgres://", "postgresql://")) or not banco.is_file():
        print(f"erro: {banco} não é um arquivo SQLite existente (o backfill só opera SQLite local)", file=sys.stderr)
        return 2
    try:
        if args.aplicar:
            db = Database(banco)                      # abrir NÃO migra
            try:
                bf.conferir_migracao(db)
                resultado = bf.executar(db, _selecionar(db, args))
            finally:
                db.close()
        else:
            pasta = Path(tempfile.mkdtemp(prefix="backfill-licoes-"))
            try:
                copia = pasta / "ensaio.sqlite3"
                copiar(banco, copia)
                db = Database(copia)
                try:
                    resultado = bf.executar(db, _selecionar(db, args))
                finally:
                    db.close()
            finally:
                shutil.rmtree(pasta, ignore_errors=True)
    except (bf.BancoDiferenteDoCodigo, bf.MigracaoAusente) as exc:
        print(f"ABORTADO: {exc}", file=sys.stderr)
        return 2
    print(bf.relatorio(resultado, aplicado=bool(args.aplicar)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
