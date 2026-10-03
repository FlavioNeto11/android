"""Passe ÚNICO e idempotente da memória das contas retiradas ANTES do item 29.32 (ADR-068, opção A).

Desde o 29.32 a retirada reescreve `memory_items` de TODAS as personas. As contas retiradas antes disso (e a memória
de OUTRAS personas que as citavam) ficaram como estavam. Este comando passa o mesmo corte por elas: o @ e o id da conta
viram `[conta removida]` em `subject` e `content`. `runs.command` e `actions.args` são histórico e NÃO são tocados.

Fonte da lista: as lápides (`contas_retiradas`, só o hash do @) e os ids dos eventos `profile.account_retired`. O @ é
achado na memória por HASH; o texto da conta não está em lugar nenhum do banco e nunca é lido nem impresso. O e-mail
da conta retirada também não está (a conta e a credencial já saíram): só entra pela lista do operador, em `--lista-stdin`
(um e-mail ou @ por linha, sem eco), e o que for e-mail de conta viva é recusado.

- PADRÃO = ENSAIO (`--ensaio`): copia o banco para um arquivo temporário (API de backup do SQLite, origem em `mode=ro`),
  roda a lógica de verdade na CÓPIA, conta e apaga a cópia. O original não é tocado. `--aplicar` grava no banco dado.
- Antes de gravar confere que a maior migração do banco é IGUAL à do código; se não for, aborta sem gravar (abrir o
  banco NÃO migra).
- O relatório só tem contagens.

Faça BACKUP do banco antes de `--aplicar` (o `scripts/deploy.ps1` já faz). Exemplos, a partir da raiz do repositório:
    backend/.venv/Scripts/python.exe scripts/memoria-conta-retirada.py --banco data/poc.sqlite3
    backend/.venv/Scripts/python.exe scripts/memoria-conta-retirada.py --banco data/poc.sqlite3 --aplicar
    type lista.txt | backend/.venv/Scripts/python.exe scripts/memoria-conta-retirada.py --banco data/poc.sqlite3 --lista-stdin
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
from app.modules.learning.infrastructure import backfill_licoes as migracao  # noqa: E402
from app.social.memory import reescrever_memoria_das_retiradas  # noqa: E402


def copiar(origem: Path, destino: Path) -> None:
    """Uma cópia consistente do banco (WAL incluso) para o ensaio; a origem é aberta SÓ PARA LEITURA."""
    de = sqlite3.connect(f"file:{origem.as_posix()}?mode=ro", uri=True)
    para = sqlite3.connect(str(destino))
    try:
        de.backup(para)
    finally:
        para.close()
        de.close()


def executar(banco: Path, *, aplicar: bool, extras: Sequence[str]) -> dict[str, int]:
    """Roda o passe e devolve as contagens. No ensaio, na cópia (que sai do disco arquivo por arquivo, sem recursão)."""
    if aplicar:
        db = Database(banco)                          # abrir NÃO migra
        try:
            migracao.conferir_migracao(db)
            with db.tx():
                return reescrever_memoria_das_retiradas(db, extras=extras, escrever=True)
        finally:
            db.close()
    pasta = Path(tempfile.mkdtemp(prefix="memoria-retirada-"))
    copia = pasta / "ensaio.sqlite3"
    try:
        copiar(banco, copia)
        db = Database(copia)
        try:
            with db.tx():
                return reescrever_memoria_das_retiradas(db, extras=extras, escrever=True)
        finally:
            db.close()
    finally:
        for arquivo in pasta.iterdir():
            arquivo.unlink()
        os.rmdir(pasta)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Reescreve a memória das contas retiradas antes do 29.32 (ensaio por padrão).")
    ap.add_argument("--banco", required=True, type=Path, help="o poc.sqlite3 (sem padrão, de propósito)")
    ap.add_argument("--ensaio", action="store_true", help="o padrão: ensaio numa cópia; nada é gravado")
    ap.add_argument("--aplicar", action="store_true", help="grava no banco dado (faça backup antes)")
    ap.add_argument("--lista-stdin", action="store_true",
                    help="lê do stdin e-mails ou @ de contas retiradas, um por linha (nunca ecoados)")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    ap = _parser()
    args = ap.parse_args(argv)
    if args.aplicar and args.ensaio:
        ap.error("--aplicar e --ensaio são excludentes")
    banco: Path = args.banco
    if str(banco).startswith(("postgres://", "postgresql://")) or not banco.is_file():
        print(f"erro: {banco} não é um arquivo SQLite existente (o passe só opera SQLite local)", file=sys.stderr)
        return 2
    extras = [linha.strip() for linha in sys.stdin.read().splitlines() if linha.strip()] if args.lista_stdin else []
    try:
        contagem = executar(banco, aplicar=bool(args.aplicar), extras=extras)
    except (migracao.BancoDiferenteDoCodigo, migracao.MigracaoAusente) as exc:
        print(f"ABORTADO: {exc}", file=sys.stderr)
        return 2
    print(("APLICADO" if args.aplicar else "ENSAIO (cópia descartada; nada foi gravado)") + ": "
          + ", ".join(f"{k}={v}" for k, v in contagem.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
