"""Fase 1 — o cofre: senha cifrada em repouso, chave mestra fora do banco, estado explícito quando não abre."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.db import Database
from app.security.secret_store import (KEY_BYTES, DpapiKeyProvider, EnvKeyProvider, MemoryKeyProvider, SecretStore,
                                       SecretStoreLocked, SecretStoreUnavailable, build_key_provider)

from .conftest import _dsn_de_teste

SENHA = "$a=B7ee1#<b-C?S-{"


def store(tmp_path: Path, provider=None) -> tuple[SecretStore, Database]:
    # `_dsn_de_teste()` e nao o arquivo temporario: assim o cofre roda de verdade no PostgreSQL quando a suite e
    # apontada para la. Preso ao arquivo, o unico teste que exercitava a LEITURA (decifrar nonce/ciphertext lidos
    # de colunas BYTEA pelo psycopg) nunca tinha rodado no outro banco — e e a parte que guarda credencial
    # (achado #162). A escrita ja rodava, indiretamente, por outros testes que seguem `db_dsn`.
    db = Database(_dsn_de_teste() or tmp_path / "cofre.sqlite3")
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
        linha = db.one("SELECT * FROM secrets")
        assert SENHA.encode() not in bytes(linha["ciphertext"])  # o ciphertext não é o texto
        # A conferência byte a byte do ARQUIVO só existe no SQLite — no PostgreSQL não há arquivo para abrir. A
        # garantia que vale nos dois é a de cima, sobre a linha; esta aqui é a rede a mais que o arquivo permite,
        # e é ela que pegaria o segredo vazando por um índice, um WAL ou uma coluna esquecida.
        if db.dialect == "sqlite":
            assert SENHA.encode() not in Path(tmp_path / "cofre.sqlite3").read_bytes()
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


# ---------------------------------------------------------- o cofre entre backends (achado #126)
def test_key_id_carrega_a_impressao_digital_da_chave(tmp_path: Path) -> None:
    """Antes, `key_id` era uma CONSTANTE de classe (`dpapi-v1`). Duas máquinas com DPAPI gravavam o mesmo rótulo
    com chaves diferentes — então a conferência de `key_id` era inerte justamente no cenário de dois backends."""
    a, b = MemoryKeyProvider(), MemoryKeyProvider()
    assert a.key_id.startswith("memoria-v1:") and len(a.key_id.split(":")[1]) == 8
    assert a.key_id != b.key_id                                   # chaves diferentes, identidades diferentes
    assert MemoryKeyProvider(a.key()).key_id == a.key_id          # mesma chave, mesma identidade
    assert a.key_id != a.key().hex() and a.key().hex()[:8] not in a.key_id   # e a chave não vaza no rótulo


def test_segredo_de_outro_backend_e_recusado_com_o_motivo_certo(tmp_path: Path) -> None:
    """O caso do achado #126: backend B não abre o que A gravou. Antes isso aparecia como `InvalidTag` ->
    "recadastre-a" — e recadastrar por B fazia A parar de abrir, os dois se revezando em quebrar o login."""
    cofre_a, db = store(tmp_path)
    try:
        ref = cofre_a.store_secret(SENHA)
        cofre_b = SecretStore(db, MemoryKeyProvider())            # outro backend, outra chave, mesmo banco
        with pytest.raises(SecretStoreLocked) as erro:
            cofre_b.get_secret(ref)
        assert cofre_a.provider.key_id in str(erro.value)          # diz QUAL chave guardou
        assert cofre_b.provider.key_id in str(erro.value)          # e qual é a deste backend
        assert "rekey" in str(erro.value)                          # e o que fazer a respeito
        assert SENHA not in str(erro.value)
        assert cofre_a.get_secret(ref) == SENHA                    # e o de A continua abrindo, intacto
    finally:
        db.close()


def test_credencial_antiga_sem_impressao_digital_continua_abrindo(tmp_path: Path) -> None:
    """As 8 credenciais que já existem em produção foram gravadas com `key_id='dpapi-v1'`, sem impressão digital.
    Exigir recadastro delas por causa de uma melhoria de diagnóstico seria cobrar um preço que ela não vale."""
    provider = MemoryKeyProvider()
    cofre, db = store(tmp_path, provider)
    try:
        ref = cofre.store_secret(SENHA)
        db.execute("UPDATE secrets SET key_id=? WHERE ref=?", ("memoria-v1", ref))   # o formato antigo
        assert cofre.get_secret(ref) == SENHA
        # Mas outra FAMÍLIA continua sendo recusada de cara, sem gastar uma tentativa de decifragem.
        db.execute("UPDATE secrets SET key_id=? WHERE ref=?", ("env-v1", ref))
        with pytest.raises(SecretStoreLocked, match="outro tipo de chave"):
            cofre.get_secret(ref)
    finally:
        db.close()


def test_chaves_estranhas_encontra_o_que_este_backend_nao_abre(tmp_path: Path) -> None:
    """É o que vira `secret_store_foreign_key` no `/health`: antes, os DOIS backends diziam `ready` e o sintoma
    era login automático falhando de forma intermitente, sem nada apontando a causa."""
    cofre_a, db = store(tmp_path)
    try:
        assert cofre_a.chaves_estranhas() == []
        cofre_b = SecretStore(db, MemoryKeyProvider())
        cofre_b.store_secret(SENHA, ref="sec-de-b")
        assert cofre_a.chaves_estranhas() == [cofre_b.provider.key_id]
        assert cofre_b.chaves_estranhas() == []                    # B abre o que B gravou
        # Linha antiga, sem impressão digital: "não sei" não vira alarme.
        db.execute("UPDATE secrets SET key_id=? WHERE ref=?", ("memoria-v1", "sec-de-b"))
        assert cofre_a.chaves_estranhas() == []
    finally:
        db.close()


def test_rekey_recifra_o_cofre_inteiro_para_a_chave_nova(tmp_path: Path) -> None:
    """Sem esta ferramenta, "trocar de chave mestra" era sinônimo de recadastrar toda credencial na mão, pelo
    portal — e por isso a travessia do DPAPI para uma chave explícita nunca acontecia."""
    from app.security.rekey import recifrar

    antigo, novo = MemoryKeyProvider(), MemoryKeyProvider()
    cofre_antigo, db = store(tmp_path, antigo)
    try:
        refs = [cofre_antigo.store_secret(f"{SENHA}-{i}") for i in range(3)]
        # `--conferir` percorre tudo, inclusive a decifragem, e não grava nada.
        from app.security.rekey import _Conferencia

        with pytest.raises(_Conferencia) as conferencia:
            recifrar(db, antigo=antigo, novo=novo, aplicar=False)
        assert len(conferencia.value.relatorio["recifrados"]) == 3
        assert db.scalar("SELECT key_id FROM secrets WHERE ref=?", (refs[0],)) == antigo.key_id

        relatorio = recifrar(db, antigo=antigo, novo=novo, aplicar=True)
        assert len(relatorio["recifrados"]) == 3 and relatorio["nao_abriram"] == []
        cofre_novo = SecretStore(db, novo)
        for i, ref in enumerate(refs):
            assert cofre_novo.get_secret(ref) == f"{SENHA}-{i}"    # a chave nova abre tudo
        assert cofre_novo.chaves_estranhas() == []
        # E rodar de novo é seguro: um cofre já recifrado não é trabalho.
        assert recifrar(db, antigo=antigo, novo=novo, aplicar=True)["ja_na_chave_nova"] == sorted(refs)
    finally:
        db.close()


def test_rekey_preserva_o_que_nem_a_chave_antiga_abre(tmp_path: Path) -> None:
    """Apagar um ciphertext que não se sabe abrir destruiria o que ainda pode ser recuperado com a chave certa,
    se ela aparecer. O certo é preservar intacto e RELATAR."""
    from app.security.rekey import recifrar

    antigo, novo, perdida = MemoryKeyProvider(), MemoryKeyProvider(), MemoryKeyProvider()
    cofre_antigo, db = store(tmp_path, antigo)
    try:
        bom = cofre_antigo.store_secret(SENHA)
        SecretStore(db, perdida).store_secret("senha-de-ninguem", ref="sec-orfa")
        relatorio = recifrar(db, antigo=antigo, novo=novo, aplicar=True)
        assert relatorio["recifrados"] == [bom] and relatorio["nao_abriram"] == ["sec-orfa"]
        assert db.scalar("SELECT key_id FROM secrets WHERE ref=?", ("sec-orfa",)) == perdida.key_id
        assert SecretStore(db, perdida).get_secret("sec-orfa") == "senha-de-ninguem"   # intacta
    finally:
        db.close()


def test_a_chave_antiga_do_rekey_vem_do_ambiente_nunca_da_linha_de_comando(monkeypatch, tmp_path: Path) -> None:
    """Argumento de processo aparece na lista de processos da máquina inteira e em log de shell. A chave mestra
    não pode passar por lá."""
    import base64 as b64

    import pytest as _pytest

    from app.security import rekey

    # A ferramenta aceita DOIS argumentos, e nenhum deles carrega material de chave.
    with _pytest.raises(SystemExit):                      # argparse recusa o que nao conhece
        rekey.main(["--chave-anterior", b64.b64encode(b"k" * KEY_BYTES).decode()])

    bruta = b"k" * KEY_BYTES
    monkeypatch.setenv(rekey.VAR_ANTERIOR, b64.b64encode(bruta).decode())
    assert rekey.provider_anterior(tmp_path).key() == bruta
    monkeypatch.delenv(rekey.VAR_ANTERIOR)
    # Sem a variavel, a chave antiga e a do DPAPI local: o caso de quem esta SAINDO do DPAPI.
    assert isinstance(rekey.provider_anterior(tmp_path), DpapiKeyProvider)


def test_o_nome_antigo_da_chave_mestra_continua_valendo() -> None:
    """Achado #88: o cofre e generico (guarda credencial de qualquer app, e a loja pode vir a guardar nele texto
    sensivel da conta Google), entao a chave mestra perdeu o prefixo `INSTAGRAM_`. Quem ja tem o nome antigo no
    `.env` nao precisa mexer em nada — tres documentos prometem isso, e e este teste que o garante."""
    from app.config import EnvSettings

    novo = EnvSettings(_env_file=None, **{"CREDENTIALS_MASTER_KEY": "aGVsbG8="})       # type: ignore[call-arg]
    antigo = EnvSettings(_env_file=None, **{"INSTAGRAM_CREDENTIALS_MASTER_KEY": "aGVsbG8="})  # type: ignore[call-arg]
    assert novo.credentials_master_key == antigo.credentials_master_key == "aGVsbG8="
    assert EnvSettings(_env_file=None).credentials_master_key is None                  # type: ignore[call-arg]
