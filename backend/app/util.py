from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone


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


def new_token() -> str:
    return secrets.token_urlsafe(12)


_WS = re.compile(r"\s+")


def norm_text(value: str | None) -> str:
    """Normaliza texto para comparação tolerante (espaços e caixa)."""
    return _WS.sub(" ", (value or "")).strip().casefold()


def truncate(value: str | None, limit: int = 300) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[: limit - 1] + "…"
