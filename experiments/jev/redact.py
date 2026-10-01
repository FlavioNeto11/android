"""Redação e bloqueio ANTES de qualquer payload poder sair da máquina.

Só stdlib. O piloto JEV nunca deve enviar segredo, credencial, cookie, chave privada nem conteúdo de conta real.
Este módulo é a trava local: o `RealJevProvider` passa todo `state` por aqui e RECUSA enviar se sobrar um achado
"duro" (chave privada, token Bearer, chave de API) — redigir um segredo e mandar o resto seria apostar que a regex
pegou tudo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Achados que BLOQUEIAM o envio (mesmo depois de redigidos o payload não sai).
HARD = {
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "bearer": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    "api_key": re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b|\bAKIA[0-9A-Z]{16}\b|\bAIza[0-9A-Za-z_-]{30,}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
}
#: Achados que são redigidos e contados, mas não bloqueiam sozinhos.
SOFT = {
    "secret_assign": re.compile(
        r"""(?ix)\b(password|passwd|senha|secret|token|api[_-]?key|cookie|session[_-]?id)\b(\s*[:=]\s*)(['"])([^'"\n]{6,})\3"""),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "win_user_path": re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s'\"]+"),
}

#: Caminhos que NUNCA entram num payload (nome do arquivo ou segmento de diretório).
SENSITIVE_SEGMENTS = {"data", "evidence", "backups", "avatars", "personas", "secrets", ".git", "node_modules"}
SENSITIVE_NAMES = {".env", "config.yaml", "credentials.key", "poc.sqlite3"}
SENSITIVE_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".sqlite3", ".sqlite3-wal", ".jks", ".keystore")


@dataclass
class Redaction:
    text: str
    hard: dict[str, int] = field(default_factory=dict)
    soft: dict[str, int] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return bool(self.hard)


def is_sensitive_path(path: str) -> bool:
    p = path.replace("\\", "/").strip("/")
    parts = p.split("/")
    name = parts[-1]
    if name in SENSITIVE_NAMES or name.lower().endswith(SENSITIVE_SUFFIXES):
        return True
    return any(seg in SENSITIVE_SEGMENTS for seg in parts[:-1])


def redact(text: str) -> Redaction:
    """Devolve o texto redigido e a contagem de achados. Nunca devolve o VALOR achado."""
    out = Redaction(text=text)
    for nome, rx in HARD.items():
        achados = rx.findall(out.text)
        if achados:
            out.hard[nome] = len(achados)
            out.text = rx.sub(f"<REDACTED:{nome}>", out.text)
    for nome, rx in SOFT.items():
        if nome == "secret_assign":
            n = len(rx.findall(out.text))
            if n:
                out.soft[nome] = n
                out.text = rx.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}<REDACTED>{m.group(3)}", out.text)
        else:
            n = len(rx.findall(out.text))
            if n:
                out.soft[nome] = n
                out.text = rx.sub(f"<REDACTED:{nome}>", out.text)
    return out
