"""Sobe as evidências (e os avatares) que já estão em disco para um storage S3-compatível.

    python -m app.tools.upload_evidence --conferir
    python -m app.tools.upload_evidence

Por que existe (item 5.7, achado #172). A chave gravada em `evidence.path` era um caminho relativo ao disco do
processo que executou a etapa. Ligar `EVIDENCE_STORAGE=s3` faz as evidências NOVAS nascerem no bucket, mas as
que já existem continuam no disco de quem as gravou — e num parque com duas réplicas elas respondem 404 na
outra. Esta ferramenta copia o que já existe e carimba `storage`/`stored_by` no banco, para as duas coisas
passarem a contar a mesma história.

**Como usar, na ordem certa:**

1. Pare o backend (`scripts/stop.ps1`). Com ele no ar, uma evidência gravada no meio da cópia ficaria como
   `disk` no banco e sem arquivo no disco depois de o operador achar que já migrou tudo.
2. Preencha `EVIDENCE_STORAGE=s3` e as chaves do bucket no `.env` (veja `.env.example`), e instale o cliente:
   `pip install boto3`.
3. `--conferir` primeiro: ele percorre tudo e diz o que faria, sem gravar nada em lugar nenhum.
4. Rode sem `--conferir`. O banco só é atualizado DEPOIS de o objeto existir no bucket, uma linha por vez: uma
   interrupção no meio deixa parte migrada e parte não, e rodar de novo continua de onde parou.
5. Suba o backend. Confira uma evidência antiga pelo painel antes de apagar qualquer coisa do disco.

**O que esta ferramenta NÃO faz, de propósito:** apagar o arquivo local. O disco é a cópia de segurança até
alguém conferir que o bucket responde — e apagar 976 provas de execução com base num relatório desta mesma
ferramenta seria confiar na parte errada do processo.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config import load_config
from ..db import Database
from ..storage import DISK, DiskStorage, build_storage
from ..taskqueue.repository import CONTENT_TYPES


def _subir(db: Database, disco: DiskStorage, destino: object, *, conferir: bool, owner_id: str) -> tuple[int, int, int]:
    enviados = ausentes = pulados = 0
    linhas = db.query("SELECT id, path, storage FROM evidence WHERE path IS NOT NULL AND redacted=0 ORDER BY id")
    for linha in linhas:
        if (linha["storage"] or DISK) != DISK:
            pulados += 1                       # já está no bucket: rodar de novo não recopia
            continue
        chave = str(linha["path"])
        dados = disco.get(chave)
        if dados is None:
            ausentes += 1                      # retenção já levou o arquivo; a linha fica como está
            continue
        if conferir:
            enviados += 1
            continue
        ext = chave.rsplit(".", 1)[-1].lower()
        destino.put(chave, dados, content_type=CONTENT_TYPES.get(ext, "application/octet-stream"))  # type: ignore[attr-defined]
        # Só DEPOIS de o objeto existir: o banco nunca aponta para um bucket que ainda não tem o arquivo.
        db.execute("UPDATE evidence SET storage=?, stored_by=? WHERE id=?",
                   (destino.name, owner_id, linha["id"]))                                          # type: ignore[attr-defined]
        enviados += 1
    return enviados, ausentes, pulados


def _subir_avatares(data_dir: Path, destino: object, *, conferir: bool) -> int:
    pasta = data_dir / "avatars"
    if not pasta.is_dir():
        return 0
    n = 0
    for arquivo in sorted(pasta.glob("*.jpg")):
        n += 1
        if not conferir:
            destino.put(f"avatars/{arquivo.name}", arquivo.read_bytes(), content_type="image/jpeg")  # type: ignore[attr-defined]
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sobe evidências e avatares do disco para o storage S3-compatível.")
    ap.add_argument("--conferir", action="store_true", help="só relata o que faria; não grava nada")
    args = ap.parse_args(argv)

    cfg = load_config()
    if cfg.env.evidence_storage == DISK:
        print("EVIDENCE_STORAGE ainda é 'disk'. Configure o bucket no .env antes de migrar — sem destino, não há")
        print("para onde subir. Veja docs/evidencias.md.")
        return 2
    destino = build_storage(
        cfg.env.evidence_storage, evidence_dir=cfg.evidence_dir, bucket=cfg.env.s3_bucket,
        endpoint_url=cfg.env.s3_endpoint_url, region=cfg.env.s3_region,
        access_key=cfg.env.s3_access_key_id.get_secret_value() if cfg.env.s3_access_key_id else None,
        secret_key=cfg.env.s3_secret_access_key.get_secret_value() if cfg.env.s3_secret_access_key else None)
    db = Database(cfg.db_dsn)
    try:
        enviados, ausentes, pulados = _subir(db, DiskStorage(cfg.evidence_dir), destino,
                                             conferir=args.conferir, owner_id=cfg.owner_id)
        avatares = _subir_avatares(cfg.data_dir, destino, conferir=args.conferir)
    finally:
        db.close()
    verbo = "seriam enviadas" if args.conferir else "enviadas"
    print(f"{enviados} evidências {verbo}; {ausentes} sem arquivo em disco (retenção); {pulados} já no bucket.")
    print(f"{avatares} avatares {'seriam enviados' if args.conferir else 'enviados'}.")
    if not args.conferir:
        print("O arquivo local NÃO foi apagado: confira uma evidência antiga pelo painel antes de limpar o disco.")
    return 0


if __name__ == "__main__":       # pragma: no cover - ponto de entrada
    sys.exit(main())
