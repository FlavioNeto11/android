"""Passe idempotente do 31.130: os fluxos ensinados numa PROVA, salvos antes da marca, recebem `nascido_de_prova`.

Desde o 31.130, a sessão de treino aberta com `nascido_de_prova: true` leva a marca ao fluxo que salva, e o Livro e
Salvas a mostram. Os fluxos de prova anteriores ficaram iguais a um fluxo real desligado por uma pessoa. Este comando
marca, PELO ID que a frente informa, o fluxo e a sessão de treino de onde ele veio (`flows.source = training:<id>`).
Nada mais é tocado: nem o status, nem o plano, nem a trilha do livro.

- PADRÃO = ENSAIO (`--ensaio`): copia o banco para um arquivo temporário (API de backup do SQLite, origem em `mode=ro`),
  roda a lógica de verdade na CÓPIA, conta e apaga a cópia. O original não é tocado.
- `--aplicar` é a confirmação explícita e exige `--backup CAMINHO` (arquivo ou pasta que exista): a prova de que o
  backup foi feito. Antes de gravar confere que a maior migração do banco é IGUAL à do código; se não for, aborta sem
  gravar (abrir o banco NÃO migra).
- O relatório tem só contagens e ids de fluxo e de sessão.

Exemplos, a partir da raiz do repositório:
    backend/.venv/Scripts/python.exe scripts/marcar-fluxo-de-prova.py --banco data/poc.sqlite3 --fluxo f-xxxx --fluxo f-yyyy
    backend/.venv/Scripts/python.exe scripts/marcar-fluxo-de-prova.py --banco data/poc.sqlite3 --fluxo f-xxxx --aplicar --backup data/backups/<pasta>
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

PREFIXO_DO_TREINO = "training:"


def copiar(origem: Path, destino: Path) -> None:
    """Uma cópia consistente do banco (WAL incluso) para o ensaio; a origem é aberta SÓ PARA LEITURA."""
    de = sqlite3.connect(f"file:{origem.as_posix()}?mode=ro", uri=True)
    para = sqlite3.connect(str(destino))
    try:
        de.backup(para)
    finally:
        para.close()
        de.close()


def marcar(db: Database, fluxos: Sequence[str]) -> dict[str, object]:
    """Marca cada fluxo (pelo id ou pela referência pública) e a sessão de treino de origem. Devolve
    `{fluxos_lidos, fluxos_marcados, sessoes_marcadas, ja_marcados, inexistentes, marcados}`."""
    marcados: list[str] = []
    ja: list[str] = []
    inexistentes: list[str] = []
    sessoes = 0
    with db.tx():
        for pedido in dict.fromkeys(fluxos):
            row = db.one("SELECT id, source, nascido_de_prova FROM flows WHERE id=? OR ref_publico=? ORDER BY id LIMIT 1",
                         (pedido, pedido))
            if row is None:
                inexistentes.append(pedido)
                continue
            fid = str(row["id"])
            if row["nascido_de_prova"] == 1:
                ja.append(fid)
            else:
                db.execute("UPDATE flows SET nascido_de_prova=1 WHERE id=?", (fid,))
                marcados.append(fid)
            fonte = str(row["source"] or "")
            if fonte.startswith(PREFIXO_DO_TREINO):
                sid = fonte[len(PREFIXO_DO_TREINO):]
                if db.one("SELECT id FROM training_sessions WHERE id=? AND nascido_de_prova IS NOT 1", (sid,)):
                    db.execute("UPDATE training_sessions SET nascido_de_prova=1 WHERE id=?", (sid,))
                    sessoes += 1
    return {"fluxos_lidos": len(dict.fromkeys(fluxos)), "fluxos_marcados": len(marcados), "sessoes_marcadas": sessoes,
            "ja_marcados": ja, "inexistentes": inexistentes, "marcados": marcados}


def executar(banco: Path, fluxos: Sequence[str], *, aplicar: bool) -> dict[str, object]:
    """Roda o passe e devolve o relatório. No ensaio, na cópia (que sai do disco arquivo por arquivo, sem recursão)."""
    if aplicar:
        db = Database(banco)                          # abrir NÃO migra
        try:
            migracao.conferir_migracao(db)
            return marcar(db, fluxos)
        finally:
            db.close()
    pasta = Path(tempfile.mkdtemp(prefix="marcar-fluxo-de-prova-"))
    copia = pasta / "ensaio.sqlite3"
    try:
        copiar(banco, copia)
        db = Database(copia)
        try:
            try:
                migracao.conferir_migracao(db)
            except (migracao.BancoDiferenteDoCodigo, migracao.MigracaoAusente) as exc:
                # Sem a coluna (banco antes da 122) o ensaio não tem onde marcar: aborta como o `--aplicar` abortaria.
                print(f"AVISO: {exc}", file=sys.stderr)
                raise
            return marcar(db, fluxos)
        finally:
            db.close()
    finally:
        for arquivo in pasta.iterdir():
            arquivo.unlink()
        os.rmdir(pasta)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Marca como nascidos de uma prova os fluxos dados pelo id (ensaio por padrão).")
    ap.add_argument("--banco", required=True, type=Path, help="o poc.sqlite3 (sem padrão, de propósito)")
    ap.add_argument("--fluxo", required=True, action="append", metavar="ID",
                    help="id (ou referência pública) do fluxo de prova; repita para mais de um")
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
        r = executar(banco, args.fluxo, aplicar=bool(args.aplicar))
    except (migracao.BancoDiferenteDoCodigo, migracao.MigracaoAusente) as exc:
        print(f"ABORTADO: {exc}", file=sys.stderr)
        return 2
    print(("APLICADO" if args.aplicar else "ENSAIO (cópia descartada; nada foi gravado)")
          + f": fluxos_lidos={r['fluxos_lidos']}, fluxos_marcados={r['fluxos_marcados']},"
          f" sessoes_marcadas={r['sessoes_marcadas']}")
    for rotulo in ("marcados", "ja_marcados", "inexistentes"):
        ids = r[rotulo]
        for fid in ids if isinstance(ids, list) else []:
            print(f"  {rotulo} {fid}")
    return 1 if r["inexistentes"] else 0


if __name__ == "__main__":
    sys.exit(main())
