"""Quais @ são NOSSOS: os das contas vivas e as lápides das que saíram por bloqueio (item 29.23, ADR-068).

A conta bloqueada some de tudo, mas o produto precisa continuar sabendo que aquele @ foi nosso: sem isso, uma persona
nossa trataria a conta antiga como terceira e poderia responder ou engajar com ela, que é engajamento simulado
(ADR-050). A lápide (`contas_retiradas`, migração 071) guarda só o HASH do @ normalizado, nunca o texto.

Este módulo também tem a reescrita do rastro textual da conta na memória da persona (`reescrever_memoria`, parte da
retirada). O HISTÓRICO (eventos, comandos, etapas, aprovações, interações, ai_calls) fica intacto por decisão do dono
(ADR-068, opção A): as provas antigas seguem legíveis.
"""
from __future__ import annotations

import hashlib
import re

from ..db import Database
from ..util import now_iso

#: O que substitui o @ e o id da conta em texto.
MARCADOR = "[conta removida]"


def normalizar(handle: str | None) -> str:
    """O @ como a lápide o guarda: sem espaços, em minúsculas, SEM o `@` (a marca é posta ao fazer o hash)."""
    return re.sub(r"\s+", "", str(handle or "")).lower().lstrip("@")


def hash_do_handle(handle: str | None) -> str:
    """sha256 de `@` + handle normalizado. Handle vazio não tem hash (`""`)."""
    limpo = normalizar(handle)
    return hashlib.sha256(f"@{limpo}".encode("utf-8")).hexdigest() if limpo else ""


def registrar_lapide(db: Database, *, app_id: str, handle: str | None, profile_id: str | None) -> bool:
    """Grava a lápide do @ (idempotente: a unicidade é (app, hash)). Devolve se nasceu agora."""
    h = hash_do_handle(handle)
    if not h:
        return False
    antes = db.scalar("SELECT COUNT(*) FROM contas_retiradas WHERE app_id=? AND handle_sha256=?", (app_id, h))
    if antes:
        return False
    db.execute("INSERT INTO contas_retiradas(app_id, handle_sha256, retirada_em, profile_id) VALUES (?,?,?,?)"
               " ON CONFLICT(app_id, handle_sha256) DO NOTHING", (app_id, h, now_iso(), profile_id))
    return True


def foi_retirada(db: Database, handle: str | None) -> bool:
    """Este @ está na lápide (conta retirada da plataforma, 29.23), ou já foi trocado por `MARCADOR`? Diferente de
    `eh_conta_nossa`: conta VIVA também é nossa, e um aviso de quarentena de conta viva continua com o @ — só o da
    retirada some do produto vivo."""
    if (handle or "").strip() == MARCADOR:
        return True
    h = hash_do_handle(handle)
    return bool(h) and bool(db.scalar("SELECT COUNT(*) FROM contas_retiradas WHERE handle_sha256=?", (h,)))


def rotulo_da_conta(db: Database, handle: str | None) -> str:
    """Como citar a conta num texto NOVO: `@handle` se está viva, `MARCADOR` se foi retirada (29.23, opção A)."""
    return MARCADOR if foi_retirada(db, handle) else f"@{normalizar(handle)}"


def citacao_da_conta(db: Database, handle: str | None) -> str:
    """A conta dentro de uma frase de aviso (`a …`, `da …`): `conta @x`, ou `conta retirada (bloqueada)` se ela já saiu."""
    return "conta retirada (bloqueada)" if foi_retirada(db, handle) else f"conta @{normalizar(handle)}"


def eh_conta_nossa(db: Database, handle: str | None) -> bool:
    """Este @ é de uma conta NOSSA? Consulta GLOBAL: os @ vivos de todos os perfis e apps, mais as lápides.

    É o que todo filtro de "terceiro" deve usar. Handle vazio nunca é nosso (persona sem conta tem `username=''`)."""
    limpo = normalizar(handle)
    if not limpo:
        return False
    chaves = (limpo, f"@{limpo}")
    if db.scalar("SELECT COUNT(*) FROM profile_accounts WHERE lower(handle) IN (?,?)", chaves):
        return True
    if db.scalar("SELECT COUNT(*) FROM instagram_profiles WHERE lower(username) IN (?,?)", chaves):
        return True
    return bool(db.scalar("SELECT COUNT(*) FROM contas_retiradas WHERE handle_sha256=?", (hash_do_handle(limpo),)))


def _padroes(handle: str | None, account_id: str | None) -> list[re.Pattern[str]]:
    """O @ (com ou sem `@`, sem diferenciar caixa, só como palavra inteira: `ana` não casa dentro de `ana.silva` nem
    de `banana`) e o id da conta em texto."""
    out: list[re.Pattern[str]] = []
    limpo = normalizar(handle)
    if limpo:
        out.append(re.compile(rf"(?<![\w.])@?{re.escape(limpo)}(?!\w|\.\w)", re.IGNORECASE))
    if account_id:
        out.append(re.compile(re.escape(account_id), re.IGNORECASE))
    return out


def sem_o_rastro(texto: str | None, handle: str | None, account_id: str | None = None) -> str | None:
    """`texto` com o @ e o id da conta trocados por `[conta removida]`. `None` e vazio passam como vieram."""
    if not texto:
        return texto
    for padrao in _padroes(handle, account_id):
        texto = padrao.sub(MARCADOR, texto)
    return texto


def previa_do_rastro(handle: str | None, account_id: str | None) -> list[str]:
    return [p for p in (normalizar(handle), (account_id or "").lower()) if p]
