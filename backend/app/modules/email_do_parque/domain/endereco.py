"""Geração de endereço de e-mail da persona sobre uma caixa catch-all: `nome.sobrenome` + sufixo determinístico.

Puro: sem rede, sem banco, sem relógio. A unicidade vem de quem chama (`existentes`); aqui só se re-tenta."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Collection

#: Tamanho máximo da parte local (RFC 5321). Cabe folgado: nome + sobrenome + 4 dígitos.
LOCAL_MAX = 64
_PADRAO = "pessoa"
#: Validação de endereço recebido (não o gerado): aceita o que um provedor comum aceita, sem espaço nem aspas.
_LOCAL_VALIDO = re.compile(r"^[a-z0-9_+-]+(?:\.[a-z0-9_+-]+)*$")
_DOMINIO_VALIDO = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")
#: Teto de re-tentativas: com 10^4 sufixos por base, estourar isto só acontece se `existentes` for adversário.
_TENTATIVAS_MAX = 10_000


def _so_alnum(texto: str) -> str:
    """Minúsculo, sem acento, só `[a-z0-9]` (espaços e pontuação somem)."""
    base = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]", "", base)


def _sufixo(persona_id: str, tentativa: int) -> str:
    digest = hashlib.sha256(f"{persona_id}:{tentativa}".encode("utf-8")).digest()
    return f"{int.from_bytes(digest[:8], 'big') % 10_000:04d}"


def gerar_endereco(*, persona_id: str, primeiro_nome: str, sobrenome: str, dominio: str,
                   existentes: Collection[str]) -> str:
    """`nome.sobrenome` + 4 dígitos do hash de (`persona_id`, tentativa), re-tentado até não estar em `existentes`
    (sem distinguir caixa). Mesma persona e mesmos `existentes` dão sempre o mesmo endereço."""
    dom = (dominio or "").strip().lower()
    if not _DOMINIO_VALIDO.match(dom):
        raise ValueError(f"domínio inválido: {dominio!r}")
    nome, resto = _so_alnum(primeiro_nome) or _PADRAO, _so_alnum(sobrenome)
    base = f"{nome}.{resto}" if resto else nome
    base = base[:LOCAL_MAX - 4].rstrip(".")
    tomados = {e.strip().lower() for e in existentes}
    for tentativa in range(_TENTATIVAS_MAX):
        endereco = f"{base}{_sufixo(persona_id, tentativa)}@{dom}"
        if endereco not in tomados:
            return endereco
    raise ValueError("sem endereço livre para a persona")


def endereco_valido(email: str) -> bool:
    try:
        dominio_do_endereco(email)
    except ValueError:
        return False
    return True


def dominio_do_endereco(email: str) -> str:
    """Domínio (minúsculo) de `local@dominio`; `ValueError` se não for um endereço que a geração aceitaria."""
    texto = (email or "").strip().lower()
    local, arroba, dominio = texto.rpartition("@")
    if (not arroba or not local or len(local) > LOCAL_MAX or not _LOCAL_VALIDO.match(local)
            or not _DOMINIO_VALIDO.match(dominio)):
        raise ValueError("endereço de e-mail inválido")
    return dominio
