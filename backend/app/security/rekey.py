"""Recifra o cofre inteiro para a chave mestra ATUAL deste backend.

    python -m app.security.rekey --conferir        # não grava nada: diz o que faria
    python -m app.security.rekey --aplicar

Por que existe (achado #126). A chave mestra é embrulhada por DPAPI, que é **por usuário e por máquina**. Quando
dois backends passam a dividir o mesmo PostgreSQL, cada um gera a sua chave, e as credenciais gravadas por um não
abrem no outro — com os dois dizendo `ready`. O contorno é a mesma `CREDENTIALS_MASTER_KEY` nos dois, e a
travessia para lá exige recifrar o que já está guardado. Sem esta ferramenta, "trocar de chave" é sinônimo de
"recadastrar toda credencial na mão, uma por uma, pelo portal" — e por isso ninguém trocava.

**A chave ANTIGA vem do ambiente, nunca da linha de comando**: argumento de processo aparece na lista de
processos da máquina inteira e em log de shell.

    $env:PREVIOUS_CREDENTIALS_MASTER_KEY = "<base64 da chave antiga>"

Sem essa variável, a chave antiga é a do DPAPI local (`data/credentials.key`) — o caso de quem está saindo do
DPAPI para uma chave explícita, que é a travessia que o parque precisa fazer.

O trabalho inteiro vai numa transação: ou todas as credenciais passam para a chave nova, ou nenhuma passa. Uma
recifragem pela metade deixaria o cofre com duas chaves e nenhuma delas abrindo tudo.
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

from ..config import load_config
from ..db import Database
from ..util import now_iso
from .secret_store import (KEY_BYTES, DpapiKeyProvider, EnvKeyProvider, KeyProvider, SecretStore, SecretStoreLocked,
                           SecretStoreUnavailable, build_key_provider, familia)

#: O nome tem o qualificador na FRENTE, e isso não é estilo: a redação de segredos (security/redaction.py)
#: casa `<qualquer_prefixo>master_key` seguido de `=`, mas não um SUFIXO: não há fronteira de palavra entre `Y` e
#: `_`. Com `CREDENTIALS_MASTER_KEY_ANTERIOR=...` a chave antiga sairia EM CLARO em log de ambiente e em erro.
#: Medido, não suposto: o teste de redação reprovou o nome com sufixo (test_sensitive_input).
VAR_ANTERIOR = "PREVIOUS_CREDENTIALS_MASTER_KEY"


def provider_anterior(data_dir: Path) -> KeyProvider:
    """A chave de ONDE se está saindo: a do ambiente, se declarada; senão a do DPAPI desta máquina."""
    material = os.environ.get(VAR_ANTERIOR)
    if material:
        try:
            bruta = base64.b64decode(material, validate=True)
        except Exception:  # noqa: BLE001 - a mensagem NUNCA pode carregar o conteúdo da variável
            raise SystemExit(f"{VAR_ANTERIOR} não é base64 válido.") from None
        if len(bruta) != KEY_BYTES:
            raise SystemExit(f"{VAR_ANTERIOR} precisa ter {KEY_BYTES} bytes em base64.")
        return EnvKeyProvider(material)
    return DpapiKeyProvider(data_dir / "credentials.key")


def recifrar(db: Database, *, antigo: KeyProvider, novo: KeyProvider, aplicar: bool) -> dict[str, list[str]]:
    """Abre cada segredo com a chave antiga e regrava com a nova. Devolve o que aconteceu, por categoria.

    `ja_na_chave_nova` não é erro nem trabalho: rodar de novo depois de uma recifragem interrompida tem de ser
    seguro, e um cofre onde metade já passou é exatamente o que uma segunda rodada precisa tolerar.
    """
    de, para = SecretStore(db, antigo), SecretStore(db, novo)
    relatorio: dict[str, list[str]] = {"recifrados": [], "ja_na_chave_nova": [], "nao_abriram": []}
    # Ordem por ponto de código, em Python, e não `ORDER BY ref`: num TEXT o `ORDER BY` segue a colação do banco
    # — no PostgreSQL com `en_US.utf8`, `sec-c…` vem antes de `sec-V…`; no SQLite (BINARY), depois. O relatório
    # (as três listas saem desta iteração) tem de ser o mesmo nos dois dialetos; `chaves_estranhas` faz o mesmo.
    refs = sorted(r["ref"] for r in db.query("SELECT ref FROM secrets"))
    # Uma transação só: ou o cofre inteiro passa, ou nada passa. Metade recifrada seria o pior dos dois mundos.
    with db.tx():
        for ref in refs:
            gravado = db.scalar("SELECT key_id FROM secrets WHERE ref=?", (ref,))
            if gravado == novo.key_id:
                relatorio["ja_na_chave_nova"].append(ref)
                continue
            try:
                valor = de.get_secret(ref)
            except (SecretStoreLocked, SecretStoreUnavailable, KeyError):
                # Nem a chave antiga abre: recifrar é impossível e recadastrar é a única saída. Fica REGISTRADO e
                # intocado — apagar um ciphertext que não se sabe abrir seria destruir o que ainda pode ser
                # recuperado com a chave certa, se ela aparecer.
                relatorio["nao_abriram"].append(ref)
                continue
            if aplicar:
                para.store_secret(valor, ref=ref)
                # `secrets.key_id` é a verdade sobre "com que chave isto foi cifrado". `account_credentials.key_id`
                # (049) é uma CÓPIA gravada no cadastro, que ninguém lê para decidir nada — mas é a tabela viva da
                # credencial, e quem abrir o banco depois da recifragem não pode achar duas respostas. A
                # `instagram_credentials` (008) é só leitura desde a 049 e sai numa migração posterior: não se
                # escreve mais nela, nem aqui.
                db.execute("UPDATE account_credentials SET key_id=?, updated_at=? WHERE secret_ref=?",
                           (novo.key_id, now_iso(), ref))
            relatorio["recifrados"].append(ref)
            del valor                    # o valor em claro não sobrevive à iteração
        if not aplicar:
            raise _Conferencia(relatorio)
    return relatorio


class _Conferencia(Exception):
    """Desfaz a transação da conferência. `--conferir` percorre o mesmo caminho de `--aplicar` — inclusive a
    decifragem — e desiste no fim: assim ele responde "isto funcionaria?", não "isto deveria funcionar"."""

    def __init__(self, relatorio: dict[str, list[str]]):
        self.relatorio = relatorio


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m app.security.rekey",
        description="Recifra as credenciais do cofre para a chave mestra atual deste backend.")
    grupo = p.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--conferir", action="store_true", help="não grava: percorre tudo e relata o que faria")
    grupo.add_argument("--aplicar", action="store_true", help="recifra de verdade, numa transação só")
    args = p.parse_args(argv)

    cfg = load_config()
    db = Database(cfg.db_dsn)
    novo = build_key_provider(data_dir=cfg.data_dir, env_material=cfg.env.credentials_master_key)
    antigo = provider_anterior(cfg.data_dir)
    if antigo.key_id == novo.key_id:
        print(f"A chave antiga e a nova são a MESMA ({novo.key_id}). Nada a fazer.")
        print(f"Para sair do DPAPI, defina CREDENTIALS_MASTER_KEY (nova) e {VAR_ANTERIOR} (antiga, se houver).")
        return 0
    print(f"banco:  {cfg.db_dsn if db.dialect == 'sqlite' else db.dialect}")
    print(f"de:     {antigo.key_id}")
    print(f"para:   {novo.key_id}")
    if familia(antigo.key_id) == familia(novo.key_id) == "dpapi-v1":
        print("AVISO: as duas são chaves DPAPI desta máquina. Recifrar para outra chave DPAPI não resolve o caso "
              "de dois backends — a chave continua presa a este usuário e a esta máquina.")
    try:
        relatorio = recifrar(db, antigo=antigo, novo=novo, aplicar=args.aplicar)
        acao = "recifradas"
    except _Conferencia as c:
        relatorio, acao = c.relatorio, "seriam recifradas (nada foi gravado)"
    print(f"{len(relatorio['recifrados'])} credenciais {acao}.")
    if relatorio["ja_na_chave_nova"]:
        print(f"{len(relatorio['ja_na_chave_nova'])} já estavam na chave nova.")
    if relatorio["nao_abriram"]:
        print(f"ATENÇÃO: {len(relatorio['nao_abriram'])} não abriram nem com a chave antiga e foram preservadas "
              f"intactas: {', '.join(relatorio['nao_abriram'])}. Elas precisam ser recadastradas pelo portal.")
    db.close()
    return 0


if __name__ == "__main__":                  # pragma: no cover - ponto de entrada
    sys.exit(main())
