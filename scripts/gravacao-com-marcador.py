"""Passe ÚNICO e idempotente do 31.118: as gravações de ensino SALVAS antes dele recebem o marcador da persona.

Desde o 31.118, salvar a habilidade troca, em `training_inputs.text`, o dado da persona que ela usa pelo marcador
(`{perfil_nome}`). As sessões salvas antes ficaram com o valor em claro. Este comando passa a mesma regra por elas
(`app/training/reparo_da_gravacao.py`). Só o texto digitado da gravação muda; nada mais é tocado.

- PADRÃO = ENSAIO (`--ensaio`): copia o banco para um arquivo temporário (API de backup do SQLite, origem em `mode=ro`),
  roda a lógica de verdade na CÓPIA, conta e apaga a cópia. O original não é tocado.
- `--aplicar` é a confirmação explícita e exige `--backup CAMINHO` (arquivo ou pasta que exista): a prova de que o
  backup foi feito. Antes de gravar confere que a maior migração do banco é IGUAL à do código; se não for, aborta sem
  gravar (abrir o banco NÃO migra). Rode SÓ com o backend no ar já no 31.118, que lê o marcador.
- O relatório tem só contagens e os ids das sessões que mudam: nenhum valor nem id de persona.

Exemplos, a partir da raiz do repositório:
    backend/.venv/Scripts/python.exe scripts/gravacao-com-marcador.py --banco data/poc.sqlite3
    backend/.venv/Scripts/python.exe scripts/gravacao-com-marcador.py --banco data/poc.sqlite3 --aplicar --backup data/backups/<pasta-do-backup>
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
from app.training.reparo_da_gravacao import marcar_gravacoes_salvas  # noqa: E402


def copiar(origem: Path, destino: Path) -> None:
    """Uma cópia consistente do banco (WAL incluso) para o ensaio; a origem é aberta SÓ PARA LEITURA."""
    de = sqlite3.connect(f"file:{origem.as_posix()}?mode=ro", uri=True)
    para = sqlite3.connect(str(destino))
    try:
        de.backup(para)
    finally:
        para.close()
        de.close()


def _passe(db: Database) -> dict[str, object]:
    # O mesmo leitor da persona que o ensino usa (`Repository.variaveis_da_persona`): só o não sigiloso.
    dados = SqlProfileDataStore(db, tem_provedor_de_sessao=lambda pacote: session_provider_of(pacote) is not None)
    with db.tx():
        return marcar_gravacoes_salvas(db, lambda pid: profile_variables(dados, pid), escrever=True)


def executar(banco: Path, *, aplicar: bool) -> dict[str, object]:
    """Roda o passe e devolve o relatório. No ensaio, na cópia (que sai do disco arquivo por arquivo, sem recursão)."""
    if aplicar:
        db = Database(banco)                          # abrir NÃO migra
        try:
            migracao.conferir_migracao(db)
            return _passe(db)
        finally:
            db.close()
    pasta = Path(tempfile.mkdtemp(prefix="gravacao-com-marcador-"))
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
            return _passe(db)
        finally:
            db.close()
    finally:
        for arquivo in pasta.iterdir():
            arquivo.unlink()
        os.rmdir(pasta)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Marca o dado da persona nas gravações salvas antes do 31.118 "
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
          + f": sessoes_lidas={r['sessoes_lidas']}, sessoes_com_marca={r['sessoes_com_marca']},"
          f" entradas_marcadas={r['entradas_marcadas']}")
    sessoes = r["sessoes"]
    for sid in sessoes if isinstance(sessoes, list) else []:
        print(f"  sessao {sid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
