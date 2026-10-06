"""Passe único do 31.138: a 1ª receita dos fluxos ensinados antes do 31.121 (e do 31.139) ganha a abertura do app.

A gravação que começou dentro do app (31.121) ou no lançador (31.139) passou a destilar a 1ª etapa com `open_app`, mas
só no que se salva depois. Este comando destila de novo cada fluxo ensinado com a regra de hoje e troca a receita viva
da etapa que agora abre o app e que não abria (`app/training/reparo_da_abertura.py`). A troca é a do treino (30.79),
na mesma chave, com a trilha no livro. Nada mais muda: nem o fluxo, nem o status dele, nem a gravação.

- PADRÃO = ENSAIO (`--ensaio`): copia o banco para um arquivo temporário (API de backup do SQLite, origem em `mode=ro`),
  roda a lógica de verdade na CÓPIA, conta e apaga a cópia. O original não é tocado.
- `--aplicar` é a confirmação explícita e exige `--backup CAMINHO` (arquivo ou pasta que exista). Antes de gravar
  confere que a maior migração do banco é IGUAL à do código; se não for, aborta sem gravar (abrir o banco NÃO migra).
- O relatório tem só contagens (antes e depois) e ids de fluxo e de receita: nenhum valor da persona.

Exemplos, a partir da raiz do repositório:
    backend/.venv/Scripts/python.exe scripts/abertura-nas-receitas-ensinadas.py --banco data/poc.sqlite3
    backend/.venv/Scripts/python.exe scripts/abertura-nas-receitas-ensinadas.py --banco data/poc.sqlite3 --aplicar --backup data/backups/<pasta>
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
from app.modules.identity.application.available_data import profile_variables  # noqa: E402
from app.modules.identity.infrastructure.profile_data import SqlProfileDataStore  # noqa: E402
from app.modules.learning.infrastructure import backfill_licoes as migracao  # noqa: E402
from app.planning.catalog import session_provider_of  # noqa: E402
from app.training.reparo_da_abertura import abrir_nas_receitas  # noqa: E402


def copiar(origem: Path, destino: Path) -> None:
    """Uma cópia consistente do banco (WAL incluso) para o ensaio; a origem é aberta SÓ PARA LEITURA."""
    de = sqlite3.connect(f"file:{origem.as_posix()}?mode=ro", uri=True)
    para = sqlite3.connect(str(destino))
    try:
        de.backup(para)
    finally:
        para.close()
        de.close()


def _passe(db: Database, *, escrever: bool) -> dict[str, object]:
    # O mesmo leitor da persona que o ensino usa (`Repository.variaveis_da_persona`): só o não sigiloso.
    dados = SqlProfileDataStore(db, tem_provedor_de_sessao=lambda pacote: session_provider_of(pacote) is not None)
    with db.tx():
        return abrir_nas_receitas(db, lambda pid: profile_variables(dados, pid) if pid else {}, escrever=escrever)


def executar(banco: Path, *, aplicar: bool) -> dict[str, object]:
    """Roda o passe e devolve o relatório. No ensaio, na cópia (que sai do disco arquivo por arquivo, sem recursão)."""
    if aplicar:
        db = Database(banco)                          # abrir NÃO migra
        try:
            migracao.conferir_migracao(db)
            return _passe(db, escrever=True)
        finally:
            db.close()
    pasta = Path(tempfile.mkdtemp(prefix="abertura-nas-receitas-"))
    copia = pasta / "ensaio.sqlite3"
    try:
        copiar(banco, copia)
        db = Database(copia)
        try:
            try:
                migracao.conferir_migracao(db)
            except (migracao.BancoDiferenteDoCodigo, migracao.MigracaoAusente) as exc:
                # O ensaio conta mesmo assim, mas o aviso diz que o `--aplicar` com este código abortaria.
                print(f"AVISO: {exc}", file=sys.stderr)
            return _passe(db, escrever=True)
        finally:
            db.close()
    finally:
        for arquivo in pasta.iterdir():
            arquivo.unlink()
        os.rmdir(pasta)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Põe a abertura do app na 1ª receita dos fluxos ensinados antigos "
                                             "(ensaio por padrão).")
    ap.add_argument("--banco", required=True, type=Path, help="o poc.sqlite3 (sem padrão, de propósito)")
    ap.add_argument("--ensaio", action="store_true", help="o padrão: ensaio numa cópia; nada é gravado")
    ap.add_argument("--aplicar", action="store_true", help="grava no banco dado (a confirmação; faça backup antes)")
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
        r = executar(banco, aplicar=bool(args.aplicar))
    except (migracao.BancoDiferenteDoCodigo, migracao.MigracaoAusente) as exc:
        print(f"ABORTADO: {exc}", file=sys.stderr)
        return 2
    print(("APLICADO" if args.aplicar else "ENSAIO (cópia descartada; nada foi gravado)")
          + f": fluxos_lidos={r['fluxos_lidos']}, sem_abertura_antes={r['antes']}, trocadas={r['trocadas']},"
          f" sem_abertura_depois={r['depois']}")
    trocas = r["trocas"]
    for t in trocas if isinstance(trocas, list) else []:
        para = t["para"] if args.aplicar else "(nova, no ensaio)"
        print(f"  fluxo {t['fluxo']} etapa {t['etapa']}: receita {t['de']} -> {para}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
