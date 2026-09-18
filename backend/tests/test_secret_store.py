"""Fase 1 — o cofre: senha cifrada em repouso, chave mestra fora do banco, estado explícito quando não abre."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.db import Database
from app.security.secret_store import (KEY_BYTES, DpapiKeyProvider, EnvKeyProvider, MemoryKeyProvider, SecretStore,
                                       SecretStoreLocked, SecretStoreUnavailable, build_key_provider)

SENHA = "$a=B7ee1#<b-C?S-{"


def store(tmp_path: Path, provider=None) -> tuple[SecretStore, Database]:
    db = Database(tmp_path / "cofre.sqlite3")
    db.migrate()
    return SecretStore(db, provider or MemoryKeyProvider()), db


def test_ida_e_volta_do_segredo(tmp_path: Path) -> None:
    cofre, db = store(tmp_path)
    try:
        ref = cofre.store_secret(SENHA)
        assert ref.startswith("sec-") and cofre.get_secret(ref) == SENHA
        assert cofre.status() == "ready"
    finally:
        db.close()


def test_a_senha_nunca_fica_em_claro_no_banco(tmp_path: Path) -> None:
    cofre, db = store(tmp_path)
    try:
        cofre.store_secret(SENHA)
        bruto = Path(tmp_path / "cofre.sqlite3").read_bytes()
        assert SENHA.encode() not in bruto                      # nem no arquivo do banco
        linha = db.one("SELECT * FROM secrets")
        assert SENHA.encode() not in bytes(linha["ciphertext"])  # o ciphertext não é o texto
        assert len(linha["nonce"]) == 12
    finally:
        db.close()


def test_cada_gravacao_usa_nonce_proprio(tmp_path: Path) -> None:
    cofre, db = store(tmp_path)
    try:
        a = cofre.store_secret(SENHA)
        b = cofre.store_secret(SENHA)
        ra = db.one("SELECT nonce, ciphertext FROM secrets WHERE ref=?", (a,))
        rb = db.one("SELECT nonce, ciphertext FROM secrets WHERE ref=?", (b,))
        assert ra["nonce"] != rb["nonce"]                        # senão o mesmo texto daria o mesmo ciphertext
        assert bytes(ra["ciphertext"]) != bytes(rb["ciphertext"])
    finally:
        db.close()


def test_ciphertext_nao_pode_ser_movido_para_outra_referencia(tmp_path: Path) -> None:
    """A referência entra como dado autenticado: trocar a etiqueta invalida a decifragem."""
    cofre, db = store(tmp_path)
    try:
        ref = cofre.store_secret(SENHA)
        outra = cofre.store_secret("outra coisa")
        linha = db.one("SELECT nonce, ciphertext FROM secrets WHERE ref=?", (ref,))
        db.execute("UPDATE secrets SET nonce=?, ciphertext=? WHERE ref=?",
                   (linha["nonce"], linha["ciphertext"], outra))
        with pytest.raises(SecretStoreLocked):
            cofre.get_secret(outra)
    finally:
        db.close()


def test_atualizar_e_apagar(tmp_path: Path) -> None:
    cofre, db = store(tmp_path)
    try:
        ref = cofre.store_secret(SENHA)
        cofre.update_secret(ref, "senha nova")
        assert cofre.get_secret(ref) == "senha nova"
        cofre.delete_secret(ref)
        assert not cofre.exists(ref)
        with pytest.raises(KeyError):
            cofre.get_secret(ref)
        with pytest.raises(KeyError):
            cofre.update_secret(ref, "x")
    finally:
        db.close()


def test_chave_mestra_diferente_trava_sem_apagar_o_ciphertext(tmp_path: Path) -> None:
    """Máquina trocada: o estado é 'travado', e o texto cifrado é preservado para recadastro consciente."""
    provider = MemoryKeyProvider()
    cofre, db = store(tmp_path, provider)
    try:
        ref = cofre.store_secret(SENHA)
        antes = bytes(db.one("SELECT ciphertext FROM secrets WHERE ref=?", (ref,))["ciphertext"])

        outro = MemoryKeyProvider()
        cofre2 = SecretStore(db, outro)
        with pytest.raises(SecretStoreLocked):
            cofre2.get_secret(ref)                               # key_id igual, chave diferente -> InvalidTag
        depois = bytes(db.one("SELECT ciphertext FROM secrets WHERE ref=?", (ref,))["ciphertext"])
        assert depois == antes                                   # nada foi apagado nem sobrescrito
    finally:
        db.close()


def test_sem_chave_o_cofre_fica_indisponivel_e_o_resto_segue(tmp_path: Path) -> None:
    cofre, db = store(tmp_path, EnvKeyProvider(None))
    try:
        assert cofre.status() == "unavailable"
        with pytest.raises(SecretStoreUnavailable):
            cofre.store_secret(SENHA)
    finally:
        db.close()


def test_chave_de_ambiente_precisa_ser_base64_de_32_bytes(tmp_path: Path) -> None:
    import base64

    boa = base64.b64encode(os.urandom(KEY_BYTES)).decode()
    cofre, db = store(tmp_path, EnvKeyProvider(boa))
    try:
        assert cofre.status() == "ready" and cofre.get_secret(cofre.store_secret(SENHA)) == SENHA
    finally:
        db.close()
    for ruim in ("nao-e-base64!!", base64.b64encode(os.urandom(16)).decode()):
        with pytest.raises(SecretStoreUnavailable):
            EnvKeyProvider(ruim).key()


@pytest.mark.skipif(os.name != "nt", reason="DPAPI só existe no Windows")
def test_dpapi_protege_a_chave_mestra_e_ela_nao_fica_em_claro(tmp_path: Path) -> None:
    provider = DpapiKeyProvider(tmp_path / "credentials.key")
    chave = provider.key()
    assert len(chave) == KEY_BYTES
    assert provider.key() == chave                               # estável entre chamadas
    embrulhada = (tmp_path / "credentials.key").read_bytes()
    assert chave not in embrulhada                               # o arquivo não contém a chave em claro
    assert len(embrulhada) > KEY_BYTES

    cofre, db = store(tmp_path, DpapiKeyProvider(tmp_path / "credentials.key"))
    try:
        assert cofre.get_secret(cofre.store_secret(SENHA)) == SENHA
    finally:
        db.close()


@pytest.mark.skipif(os.name != "nt", reason="DPAPI só existe no Windows")
def test_chave_embrulhada_corrompida_trava_com_mensagem_util(tmp_path: Path) -> None:
    path = tmp_path / "credentials.key"
    DpapiKeyProvider(path).key()
    path.write_bytes(b"lixo que nao veio deste usuario")
    with pytest.raises(SecretStoreLocked, match="recadastre"):
        DpapiKeyProvider(path).key()


def test_ambiente_tem_precedencia_sobre_dpapi(tmp_path: Path) -> None:
    import base64

    material = base64.b64encode(os.urandom(KEY_BYTES)).decode()
    assert isinstance(build_key_provider(data_dir=tmp_path, env_material=material), EnvKeyProvider)
    assert isinstance(build_key_provider(data_dir=tmp_path, env_material=None), DpapiKeyProvider)
