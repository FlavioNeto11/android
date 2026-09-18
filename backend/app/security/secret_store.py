"""Cofre de segredos: credencial cifrada em repouso, com a chave mestra fora do banco.

Desenho: `senha -> AES-256-GCM -> ciphertext no SQLite`. A chave que abre tudo **não** fica ao lado do banco — no
Windows ela é embrulhada por DPAPI e só o mesmo usuário na mesma máquina consegue desembrulhar. Copiar banco e
arquivos juntos não basta para abrir as senhas.

Quem chama nunca recebe o valor por acaso: `get_secret` existe e é usado num único lugar, o canal de entrada
sensível, no instante da digitação.
"""
from __future__ import annotations

import base64
import ctypes
import logging
import os
import secrets as pysecrets
from ctypes import wintypes
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..db import Database
from ..util import now_iso

log = logging.getLogger("poc.security")

KEY_BYTES = 32                       # AES-256
NONCE_BYTES = 12                     # tamanho recomendado para GCM
DPAPI_ENTROPY = b"poc-central-de-aparelhos/credenciais"
IS_WINDOWS = os.name == "nt"


class SecretStoreLocked(RuntimeError):
    """A chave mestra existe mas não pode ser aberta nesta máquina/usuário.

    Acontece com Windows reinstalado, usuário de serviço trocado ou aplicação movida de máquina. O texto cifrado
    **não** é apagado nem sobrescrito: a recuperação é recadastrar as credenciais de propósito.
    """


class SecretStoreUnavailable(RuntimeError):
    """Não há chave mestra utilizável. O resto do sistema segue funcionando; só credencial fica bloqueada."""


class KeyProvider(Protocol):
    key_id: str

    def available(self) -> bool: ...
    def key(self) -> bytes: ...


# ---------------------------------------------------------------- DPAPI (padrão no Windows)
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(protect: bool, data: bytes) -> bytes | None:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def blob(raw: bytes) -> _Blob:
        buf = ctypes.create_string_buffer(raw, len(raw))
        return _Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    src, out, entropy = blob(data), _Blob(), blob(DPAPI_ENTROPY)
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, ctypes.byref(entropy), None, None, 0, ctypes.byref(out))
    if not ok:
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


class DpapiKeyProvider:
    """Chave gerada uma vez e guardada embrulhada por DPAPI, presa a este usuário e a esta máquina."""

    key_id = "dpapi-v1"

    def __init__(self, path: Path):
        self.path = path

    def available(self) -> bool:
        return IS_WINDOWS

    def key(self) -> bytes:
        if not IS_WINDOWS:
            raise SecretStoreUnavailable("DPAPI só existe no Windows.")
        if self.path.exists():
            wrapped = self.path.read_bytes()
            raw = _dpapi(False, wrapped)
            if raw is None or len(raw) != KEY_BYTES:
                raise SecretStoreLocked(
                    "A chave mestra não pôde ser aberta nesta máquina/usuário (Windows reinstalado, usuário de "
                    "serviço trocado ou aplicação movida). As credenciais cifradas foram preservadas: recadastre-as "
                    "para voltar a usar autenticação automática.")
            return raw
        raw = AESGCM.generate_key(bit_length=KEY_BYTES * 8)
        wrapped = _dpapi(True, raw)
        if wrapped is None:
            raise SecretStoreUnavailable("O Windows recusou proteger a chave mestra (DPAPI indisponível).")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(wrapped)
        log.info("chave mestra de credenciais criada e protegida por DPAPI em %s", self.path.name)
        return raw


class EnvKeyProvider:
    """Alternativa portátil: chave em base64 no ambiente. Serve para outra máquina e para integração contínua."""

    key_id = "env-v1"

    def __init__(self, material: str | None):
        self._material = material

    def available(self) -> bool:
        return bool(self._material)

    def key(self) -> bytes:
        if not self._material:
            raise SecretStoreUnavailable("INSTAGRAM_CREDENTIALS_MASTER_KEY não está definida.")
        try:
            raw = base64.b64decode(self._material, validate=True)
        except Exception:  # noqa: BLE001 - conteúdo inválido não pode vazar na mensagem
            raise SecretStoreUnavailable("INSTAGRAM_CREDENTIALS_MASTER_KEY não é base64 válido.") from None
        if len(raw) != KEY_BYTES:
            raise SecretStoreUnavailable(f"A chave mestra precisa ter {KEY_BYTES} bytes ({KEY_BYTES * 8} bits).")
        return raw


class MemoryKeyProvider:
    """Só para teste: chave explícita, em memória."""

    key_id = "memoria-v1"

    def __init__(self, raw: bytes | None = None):
        self._raw = raw or AESGCM.generate_key(bit_length=KEY_BYTES * 8)

    def available(self) -> bool:
        return True

    def key(self) -> bytes:
        return self._raw


def build_key_provider(*, data_dir: Path, env_material: str | None) -> KeyProvider:
    """Ambiente manda: se a chave estiver no ambiente, ela é usada; senão, DPAPI no Windows."""
    if env_material:
        return EnvKeyProvider(env_material)
    return DpapiKeyProvider(data_dir / "credentials.key")


# ---------------------------------------------------------------- o cofre
class SecretStore:
    """Guarda ciphertext e devolve só uma referência. Trocar por Key Vault ou Secrets Manager depois é trocar esta
    classe, sem mexer no domínio."""

    def __init__(self, db: Database, provider: KeyProvider):
        self.db = db
        self.provider = provider

    # -- estado ----------------------------------------------------------------
    def status(self) -> str:
        """`ready` | `locked` | `unavailable` — é o que o painel mostra."""
        if not self.provider.available():
            return "unavailable"
        try:
            self.provider.key()
        except SecretStoreLocked:
            return "locked"
        except SecretStoreUnavailable:
            return "unavailable"
        return "ready"

    def _cipher(self) -> AESGCM:
        if not self.provider.available():
            raise SecretStoreUnavailable("Nenhuma chave mestra disponível para proteger credenciais.")
        return AESGCM(self.provider.key())

    # -- operações -------------------------------------------------------------
    def store_secret(self, plaintext: str, *, ref: str | None = None) -> str:
        """Cifra e guarda. A referência é o que circula pelo domínio; o valor fica só aqui."""
        if not plaintext:
            raise ValueError("segredo vazio")
        ref = ref or f"sec-{pysecrets.token_urlsafe(16)}"
        nonce = os.urandom(NONCE_BYTES)
        # A referência entra como dado autenticado: um ciphertext não pode ser movido para outra referência.
        blob = self._cipher().encrypt(nonce, plaintext.encode("utf-8"), ref.encode("ascii"))
        now = now_iso()
        self.db.execute(
            "INSERT INTO secrets(ref, key_id, nonce, ciphertext, created_at, updated_at) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(ref) DO UPDATE SET key_id=excluded.key_id, nonce=excluded.nonce,"
            " ciphertext=excluded.ciphertext, updated_at=excluded.updated_at",
            (ref, self.provider.key_id, nonce, blob, now, now))
        return ref

    def get_secret(self, ref: str) -> str:
        """Único ponto que devolve o valor. Quem chama tem de usá-lo e soltar a referência imediatamente."""
        row = self.db.one("SELECT key_id, nonce, ciphertext FROM secrets WHERE ref=?", (ref,))
        if row is None:
            raise KeyError(ref)
        if row["key_id"] != self.provider.key_id:
            raise SecretStoreLocked(
                f"A credencial foi guardada com outra chave mestra ({row['key_id']}); recadastre-a.")
        try:
            return self._cipher().decrypt(row["nonce"], row["ciphertext"], ref.encode("ascii")).decode("utf-8")
        except InvalidTag:
            raise SecretStoreLocked(
                "A credencial não pôde ser decifrada com a chave mestra atual; recadastre-a.") from None

    def update_secret(self, ref: str, plaintext: str) -> str:
        if self.db.one("SELECT ref FROM secrets WHERE ref=?", (ref,)) is None:
            raise KeyError(ref)
        return self.store_secret(plaintext, ref=ref)

    def delete_secret(self, ref: str) -> None:
        self.db.execute("DELETE FROM secrets WHERE ref=?", (ref,))

    def exists(self, ref: str) -> bool:
        return self.db.one("SELECT ref FROM secrets WHERE ref=?", (ref,)) is not None
