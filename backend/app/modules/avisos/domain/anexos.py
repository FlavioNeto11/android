"""Anexos dos canais (item 28.24, F1): o que é um anexo recebido e quais tipos a Central aceita. Puro, sem Telegram e sem
disco: o adaptador de cada canal traduz o que chegou em `AnexoRecebido`, e o armazém (`infrastructure/anexos.py`) guarda.

A lista de tipos é FIXA no código (mime → extensão): a extensão do arquivo em disco vem daqui, nunca do remetente. A
config (`avisos.entrada.anexos.tipos`) só escolhe, dentro desta lista, o que fica ligado.

O mime é conferido pelo CONTEÚDO (assinatura): JPEG, PNG, WEBP e PDF têm assinatura; o texto é UTF-8 válido, sem byte
nulo. O declarado pelo remetente só serve para recusar a divergência (um executável declarado como PNG).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: mime → extensão do arquivo em disco. O `text/plain` guarda como `.txt`; nada aqui é executável.
EXTENSAO: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
    "text/plain": "txt",
}
#: Como o dono lê cada tipo na resposta.
ROTULO: dict[str, str] = {
    "image/jpeg": "a imagem",
    "image/png": "a imagem",
    "image/webp": "a imagem",
    "application/pdf": "o PDF",
    "text/plain": "o arquivo de texto",
}
_SINONIMOS = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg", "text/x-log": "text/plain"}
_SHA256 = re.compile(r"[0-9a-f]{64}")


class AnexoGrandeDemais(Exception):
    """O arquivo passa do teto: pelo tamanho que o canal declarou, pelo que a consulta ao arquivo disse ou pelo que chegou
    no download (que é interrompido ali). Baixar de novo não muda o tamanho."""

    def __init__(self, max_bytes: int):
        super().__init__(f"arquivo acima do limite de {max_bytes} bytes")
        self.max_bytes = max_bytes


@dataclass(frozen=True, slots=True)
class AnexoRecebido:
    """Um anexo que chegou numa mensagem, já traduzido pelo canal e ANTES de baixar. `ref` é opaca (o `file_id` do
    Telegram): só o adaptador sabe usá-la. `recusa`: o motivo, em português, quando o próprio canal já sabe que não
    serve (voz, vídeo, figurinha…); nesse caso nada é baixado."""

    tipo: str                       # 'imagem' | 'documento' | 'voz' | 'video' | ... (como o canal chama)
    mime: str | None = None         # o mime que o remetente declarou (pode mentir)
    tamanho: int | None = None      # os bytes que o remetente declarou (pode mentir)
    ref: str | None = None
    recusa: str | None = None


def normalizar_mime(mime: str | None) -> str | None:
    """O mime declarado em minúsculas, sem parâmetros (`text/plain; charset=utf-8`) e sem as grafias comuns de outro."""
    if not mime:
        return None
    base = mime.split(";", 1)[0].strip().lower()
    return _SINONIMOS.get(base, base) or None


def detectar_mime(conteudo: bytes) -> str | None:
    """O mime pelo conteúdo, ou None quando não é um dos tipos conhecidos."""
    if conteudo[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if conteudo[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if conteudo[:4] == b"RIFF" and conteudo[8:12] == b"WEBP":
        return "image/webp"
    if conteudo[:5] == b"%PDF-":
        return "application/pdf"
    if conteudo and b"\x00" not in conteudo:
        try:
            conteudo.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return "text/plain"
    return None


def sha256_valido(sha: str) -> bool:
    return bool(_SHA256.fullmatch(sha))


def tamanho_legivel(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{round(n / 1024)} KB"
    return f"{n / (1024 * 1024):.1f} MB".replace(".0", "").replace(".", ",")
