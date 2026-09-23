"""Onde os artefatos do parque são gravados: disco local ou bucket S3-compatível (item 5.7, achado #172).

O defeito que isto corrige: a chave gravada em `evidence.path` era um caminho relativo ao DISCO do processo que
executou a etapa, e o painel lia o arquivo do disco do processo que atendia o GET. Numa máquina funciona; no
instante em que houver um segundo backend (topologia que a posse de etapa e o `hosted_by` já admitem), a
evidência gravada por A responde 404 em B — e a retenção de B apaga do banco as linhas cujos arquivos ficaram
órfãos no disco de A.

Duas implementações atrás de uma interface:

* `DiskStorage` — o comportamento de hoje, e o certo para quem roda tudo numa máquina.
* `S3Storage` — qualquer endpoint compatível (AWS, MinIO). É o que faz a evidência gravada por uma réplica ser
  lida pela outra.

**Chave, não caminho.** A chave é sempre com barra normal (`run/instância/arquivo.jpg`), nos dois back-ends e
nos dois sistemas operacionais. As 976 linhas já gravadas no Windows têm `\\` no `path`; `DiskStorage` normaliza
na leitura, para o histórico continuar abrindo sem migração de dados.

**A escrita sai do laço de eventos.** `put` é bloqueante de propósito — quem chama de código assíncrono usa
`put_async`, que a joga numa thread. Em disco local a escrita síncrona era inofensiva; com o destino na rede,
ela travaria o scheduler inteiro a cada captura de tela.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Protocol

log = logging.getLogger("poc.storage")

DISK = "disk"
S3 = "s3"

#: Quanto tempo vale a URL pré-assinada que o painel recebe. Curto: ela é um atalho para a imagem que a pessoa
#: está olhando agora, não um link para compartilhar.
URL_TTL_S = 300

_CHAVE_VALIDA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./=+-]*$")


class StorageError(RuntimeError):
    pass


def normalizar_chave(chave: str) -> str:
    """`a\\b\\c.jpg` e `a/b/c.jpg` são a MESMA chave. Recusa o que sobe de diretório.

    A travessia é recusada aqui, num lugar só, em vez de em cada ponto de leitura: `evidence.path` vem do banco,
    mas o banco é compartilhado entre réplicas, e "veio do banco" nunca foi o mesmo que "é seguro abrir".
    """
    limpa = chave.replace("\\", "/").strip("/")
    if not limpa or not _CHAVE_VALIDA.match(limpa):
        raise StorageError(f"chave de storage inválida: {chave!r}")
    partes = PurePosixPath(limpa).parts
    if any(p in ("..", ".") for p in partes):
        raise StorageError(f"chave de storage inválida: {chave!r}")
    return "/".join(partes)


class Storage(Protocol):
    name: str

    def put(self, chave: str, dados: bytes, *, content_type: str = "application/octet-stream") -> str: ...

    def get(self, chave: str) -> bytes | None: ...

    def stream(self, chave: str) -> Iterator[bytes] | None: ...

    def exists(self, chave: str) -> bool: ...

    def delete_prefix(self, prefixo: str) -> int: ...

    def local_path(self, chave: str) -> Path | None: ...

    def url(self, chave: str) -> str | None: ...


class DiskStorage:
    """Pasta local. `local_path` devolve o arquivo para o `FileResponse` continuar servindo como sempre serviu."""

    name = DISK

    def __init__(self, raiz: Path):
        self.raiz = Path(raiz)

    def _arquivo(self, chave: str) -> Path:
        alvo = (self.raiz / normalizar_chave(chave)).resolve()
        if not alvo.is_relative_to(self.raiz.resolve()):
            raise StorageError(f"chave de storage fora da raiz: {chave!r}")
        return alvo

    def put(self, chave: str, dados: bytes, *, content_type: str = "application/octet-stream") -> str:
        del content_type                       # o disco não guarda tipo; quem serve o decide pela extensão
        chave = normalizar_chave(chave)
        alvo = self._arquivo(chave)
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_bytes(dados)
        return chave

    def get(self, chave: str) -> bytes | None:
        try:
            return self._arquivo(chave).read_bytes()
        except (FileNotFoundError, NotADirectoryError, StorageError):
            return None

    def stream(self, chave: str) -> Iterator[bytes] | None:
        dados = self.get(chave)
        return iter((dados,)) if dados is not None else None

    def exists(self, chave: str) -> bool:
        try:
            return self._arquivo(chave).is_file()
        except StorageError:
            return False

    def delete_prefix(self, prefixo: str) -> int:
        try:
            alvo = self._arquivo(prefixo)
        except StorageError:
            return 0
        if alvo.is_dir():
            n = sum(1 for _ in alvo.rglob("*") if _.is_file())
            shutil.rmtree(alvo, ignore_errors=True)
            return n
        if alvo.is_file():
            alvo.unlink(missing_ok=True)
            return 1
        return 0

    def local_path(self, chave: str) -> Path | None:
        try:
            alvo = self._arquivo(chave)
        except StorageError:
            return None
        return alvo if alvo.is_file() else None

    def url(self, chave: str) -> str | None:
        del chave
        return None                            # arquivo local não tem URL: quem serve é a própria API


class S3Storage:
    """Bucket S3-compatível. O cliente é INJETÁVEL para o teste de contrato não precisar de rede.

    `boto3` entra por import tardio: quem não liga `EVIDENCE_STORAGE=s3` não paga a dependência, e quem liga
    sem o pacote instalado descobre na PARTIDA, com a mensagem dizendo o que instalar.
    """

    name = S3

    def __init__(self, bucket: str, *, client: Any = None, endpoint_url: str | None = None,
                 region: str | None = None, access_key: str | None = None, secret_key: str | None = None,
                 prefixo: str = ""):
        self.bucket = bucket
        self.prefixo = prefixo.strip("/")
        if client is not None:
            self._c = client
            return
        try:
            import boto3                       # noqa: PLC0415 - dependência opcional
        except ModuleNotFoundError as exc:     # pragma: no cover - depende do ambiente
            raise StorageError(
                "EVIDENCE_STORAGE=s3 exige o pacote 'boto3' instalado. Veja docs/evidencias.md; para voltar ao "
                "disco local, apague a variável do .env.") from exc
        self._c = boto3.client("s3", endpoint_url=endpoint_url, region_name=region,
                               aws_access_key_id=access_key, aws_secret_access_key=secret_key)

    def _k(self, chave: str) -> str:
        chave = normalizar_chave(chave)
        return f"{self.prefixo}/{chave}" if self.prefixo else chave

    def put(self, chave: str, dados: bytes, *, content_type: str = "application/octet-stream") -> str:
        chave = normalizar_chave(chave)
        self._c.put_object(Bucket=self.bucket, Key=self._k(chave), Body=dados, ContentType=content_type)
        return chave

    def get(self, chave: str) -> bytes | None:
        try:
            resp = self._c.get_object(Bucket=self.bucket, Key=self._k(chave))
        except Exception as exc:               # noqa: BLE001 - botocore levanta ClientError; sem boto3 aqui, só o tipo base
            if _e_ausente(exc):
                return None
            raise
        corpo = resp["Body"]
        return corpo.read() if hasattr(corpo, "read") else bytes(corpo)

    def stream(self, chave: str) -> Iterator[bytes] | None:
        dados = self.get(chave)
        return iter((dados,)) if dados is not None else None

    def exists(self, chave: str) -> bool:
        try:
            self._c.head_object(Bucket=self.bucket, Key=self._k(chave))
        except Exception as exc:               # noqa: BLE001
            if _e_ausente(exc):
                return False
            raise
        return True

    def delete_prefix(self, prefixo: str) -> int:
        alvo = self._k(prefixo).rstrip("/") + "/"
        apagados = 0
        token: str | None = None
        while True:
            kw: dict[str, Any] = {"Bucket": self.bucket, "Prefix": alvo}
            if token:
                kw["ContinuationToken"] = token
            pagina = self._c.list_objects_v2(**kw)
            chaves = [{"Key": o["Key"]} for o in pagina.get("Contents", [])]
            if chaves:
                self._c.delete_objects(Bucket=self.bucket, Delete={"Objects": chaves})
                apagados += len(chaves)
            if not pagina.get("IsTruncated"):
                return apagados
            token = pagina.get("NextContinuationToken")
            if not token:
                return apagados

    def local_path(self, chave: str) -> Path | None:
        del chave
        return None                            # nada em disco: quem serve é a URL pré-assinada ou o streaming

    def url(self, chave: str) -> str | None:
        try:
            return str(self._c.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": self._k(chave)}, ExpiresIn=URL_TTL_S))
        except Exception:                      # noqa: BLE001 - cliente sem assinatura: o streaming cobre
            log.debug("sem URL pré-assinada para %s; será servido por streaming", chave, exc_info=True)
            return None


def _e_ausente(exc: Exception) -> bool:
    """"Não existe" no S3 chega como `NoSuchKey`/`404`, e como `ClientError` genérico em clientes falsos."""
    codigo = getattr(exc, "response", {}).get("Error", {}).get("Code") if hasattr(exc, "response") else None
    if codigo in ("NoSuchKey", "NoSuchBucket", "404", "NotFound"):
        return True
    return exc.__class__.__name__ in ("NoSuchKey", "ClientError404", "KeyError", "FileNotFoundError")


async def put_async(armazem: Storage, chave: str, dados: bytes, *,
                    content_type: str = "application/octet-stream") -> str:
    """A escrita SAI do laço de eventos. Em disco local isso era inofensivo; com o destino na rede, gravar
    dentro do laço travaria o scheduler inteiro a cada captura de tela."""
    return await asyncio.to_thread(armazem.put, chave, dados, content_type=content_type)


def build_storage(kind: str, *, evidence_dir: Path, bucket: str | None = None, endpoint_url: str | None = None,
                  region: str | None = None, access_key: str | None = None,
                  secret_key: str | None = None) -> Storage:
    """Escolhe o back-end pela configuração. Desconhecido é erro de partida, não conversão silenciosa."""
    if kind == DISK:
        return DiskStorage(evidence_dir)
    if kind == S3:
        if not bucket:
            raise StorageError("EVIDENCE_STORAGE=s3 exige S3_BUCKET.")
        return S3Storage(bucket, endpoint_url=endpoint_url, region=region,
                         access_key=access_key, secret_key=secret_key)
    raise StorageError(f"EVIDENCE_STORAGE desconhecido: {kind!r} (use 'disk' ou 's3').")
