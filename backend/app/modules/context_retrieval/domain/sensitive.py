"""Caminho sensível e portão duro de segredo, antes de qualquer mapa ou chunk sair para um provedor.

Reaproveita `security.redaction` (formato, nunca lista de valores) para o que é "mole" e acrescenta os formatos de
credencial que o filtro de log não cobre porque nem precisava: chave privada, JWT, token de bearer, chave de API,
DSN com senha. O que é DURO bloqueia o pedido inteiro; o mole só tira o trecho do payload.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache

from app.security.redaction import redact

# ---------------------------------------------------------------- caminhos sensíveis
#: Pastas na RAIZ do repositório que guardam dado do parque, prova, cópia ou conta. Fora do Git por desenho.
_PASTAS_DA_RAIZ = ("data", "evidence", "backups", "personas", "apks", "logs", "screenshots", "secrets")
#: Pastas sensíveis em QUALQUER nível.
_PASTAS_EM_QUALQUER_NIVEL = ("secrets", ".ssh", ".aws", ".gnupg", ".kube")

_PADROES_BASE: tuple[str, ...] = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "id_rsa*", "id_ed25519*",
    "id_ecdsa*", "*.sqlite", "*.sqlite3", "*.db", "*.log", "*.secret", "*.secrets", "*.token", ".netrc", ".npmrc",
    ".pypirc", "config.yaml", "config.yml", "credentials", "credentials.*", "*credentials*.json",
    "*credentials*.yaml", "*credentials*.yml", "*credentials*.txt", "*cookie*.json", "*cookie*.txt",
    "*token*.json", "*token*.txt",
)
#: Modelos versionados de propósito (`.env.example`): nome parecido, conteúdo neutro.
_EXCECOES = (".env.example", ".env.sample", ".env.template")


def _glob(padrao: str) -> re.Pattern[str]:
    saida: list[str] = []
    i = 0
    while i < len(padrao):
        c = padrao[i]
        if padrao.startswith("**/", i):
            saida.append("(?:.*/)?")
            i += 3
            continue
        if padrao.startswith("**", i):
            saida.append(".*")
            i += 2
            continue
        saida.append("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c))
        i += 1
    return re.compile("^" + "".join(saida) + "$")


class SensitivePathMatcher:
    """`is_sensitive("data/x.json")`. Caminho relativo à raiz, com `/`; maiúsculas não contam."""

    def __init__(self, extra: Iterable[str] = ()) -> None:
        self._extra_nome: list[re.Pattern[str]] = []
        self._extra_caminho: list[re.Pattern[str]] = []
        for p in extra:
            p = p.strip().lower().replace(chr(92), "/")
            if not p:
                continue
            (self._extra_caminho if "/" in p else self._extra_nome).append(_glob(p))
        self._base = [_glob(p) for p in _PADROES_BASE]

    def reason(self, path: str) -> str | None:
        """Motivo curto (rótulo, nunca o nome do arquivo) ou `None` quando o caminho pode seguir."""
        p = path.replace(chr(92), "/").lower()
        while p.startswith("./"):
            p = p[2:]
        partes = [x for x in p.split("/") if x]
        if not partes:
            return None
        nome = partes[-1]
        if nome in _EXCECOES:
            return None
        if len(partes) > 1 and partes[0] in _PASTAS_DA_RAIZ:
            return "sensitive_dir"
        if any(d in _PASTAS_EM_QUALQUER_NIVEL for d in partes[:-1]):
            return "sensitive_dir"
        if any(rx.match(nome) for rx in self._base):
            return "sensitive_name"
        if any(rx.match(nome) for rx in self._extra_nome) or any(rx.match(p) for rx in self._extra_caminho):
            return "configured_pattern"
        return None

    def is_sensitive(self, path: str) -> bool:
        return self.reason(path) is not None


@lru_cache(maxsize=1)
def default_matcher() -> SensitivePathMatcher:
    return SensitivePathMatcher()


# ---------------------------------------------------------------- portão duro de segredo
_DUROS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=\-]{20,}")),
    ("authorization_header", re.compile(r"Authorization\s*:\s*(?:Basic|Token|ApiKey|Digest)\s+\S{8,}", re.IGNORECASE)),
    ("api_key", re.compile(
        r"\b(?:sk-ant-[A-Za-z0-9_\-]{8,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{35}|"
        r"gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9\-]{10,}|glpat-[A-Za-z0-9_\-]{20,})")),
    ("dsn_password", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:[^@\s/]{3,}@", re.IGNORECASE)),
)


def hard_secret_kind(text: str | None) -> str | None:
    """Rótulo do primeiro formato de credencial DURO achado, ou `None`. Nunca devolve o valor."""
    if not text:
        return None
    for tipo, rx in _DUROS:
        if rx.search(text):
            return tipo
    return None


def has_soft_secret(text: str | None) -> bool:
    """Par chave/valor com cara de segredo, que a redação central mascararia. O trecho sai do payload; o pedido segue."""
    return bool(text) and redact(text) != text
