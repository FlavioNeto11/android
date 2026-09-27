from __future__ import annotations

import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Protocol


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return to_iso(now())


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso_in(seconds: float) -> str:
    return to_iso(now() + timedelta(seconds=seconds))


def new_run_id() -> str:
    """ID curto, ordenável e legível: r-20260917-1a2b3c."""
    return f"r-{now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"


def new_command_id() -> str:
    """Mesma forma do id de execução, outro prefixo: c-20260921153012-1a2b3c."""
    return f"c-{now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"


def new_token() -> str:
    return secrets.token_urlsafe(12)


class ConsultaDeLinha(Protocol):
    """O pedaço do `Database` que `novo_id_de_app` usa. Protocolo, e não o tipo: `db` importa `util`, e o kernel
    não importa ninguém do app."""

    def one(self, sql: str, params: tuple[object, ...] = ...) -> object | None: ...


def novo_id_de_app(db: ConsultaDeLinha, nome: str, *, tabela: str = "apps") -> str:
    """Id legível e único a partir do nome: "Configurações" → "configuracoes", "Outlook" → "outlook".

    `tabela` é constante do código (`apps` ou `proxy_profiles`), nunca entrada de quem chama a API. Mora no kernel
    porque serve a duas tabelas de dois contextos — o registro de apps e os perfis de proxy —, e morando na vitrine
    obrigava o proxy a importá-la."""
    plain = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-") or "app"
    app_id, n = base, 2
    while db.one(f"SELECT id FROM {tabela} WHERE id=?", (app_id,)):
        app_id, n = f"{base}-{n}", n + 1
    return app_id


_WS = re.compile(r"\s+")


def norm_text(value: str | None) -> str:
    """Normaliza texto para comparação tolerante (espaços e caixa)."""
    return _WS.sub(" ", (value or "")).strip().casefold()


def truncate(value: str | None, limit: int = 300) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[: limit - 1] + "…"


def sem_marcacao(texto: str, *, limite: int = 1200) -> str:
    """Texto que vai entrar num bloco do prompt sem poder FECHAR esse bloco.

    Vale para tudo que não foi escrito pelo operador: legenda, comentário, nome de conta lido da tela, memória
    aprendida de uma conversa. A delimitação é a defesa — um texto contendo `</intencao>` seguido de ordens sairia
    do bloco e passaria a parecer moldura do prompt —, então os sinais que formam a marcação não sobrevivem aqui.

    Mora em `util` de propósito: quem monta prompt é `planning/prompts.py` E `social/context.py`, e defesa que só
    vale num dos dois não é defesa. O teto de tamanho existe pelo motivo prático de sempre: 2200 caracteres de
    legenda empurrariam persona e memória para longe do fim do prompt, e posição importa.
    """
    limpo = (texto or "").replace("<", "‹").replace(">", "›").strip()
    return limpo[:limite].rstrip() + "…" if len(limpo) > limite else limpo


#: Endereço que a automação aceita abrir no navegador (ADR-025): http/https, sem espaço, aspa nem usuário:senha@ —
#: ele vai entre aspas simples numa linha do shell do aparelho, e credencial na URL é credencial fora do cofre.
_URL_ABRIVEL = re.compile(r"https?://(?![^/?#\s]*@)[^\s'\"]+", re.IGNORECASE)


def url_abrivel(url: str) -> bool:
    return bool(_URL_ABRIVEL.fullmatch(url or ""))
