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


def _previa(handle: str | None, account_id: str | None) -> list[str]:
    return [p for p in (normalizar(handle), (account_id or "").lower()) if p]


def reescrever_memoria(db: Database, *, profile_id: str, handle: str | None, account_id: str | None) -> int:
    """`memory_items` da PERSONA: `subject` e `content` passam a dizer `[conta removida]` onde diziam o @ da conta
    (com e sem `@`, sem diferenciar caixa) ou o id dela. A linha FICA: a persona sobrevive e lembra do que viveu.

    O `fingerprint` (unique por perfil) é recalculado com o texto novo; se colidir com outra lembrança, a linha
    guarda um fingerprint próprio em vez de ser apagada ou mesclada. Devolve quantas lembranças mudaram."""
    from .memory import fingerprint        # import tardio: `memory` importa o repositório, que importa este módulo

    previas = _previa(handle, account_id)
    if not previas:
        return 0
    casa = " OR ".join(f"lower({c}) LIKE ?" for c in ("subject", "content") for _ in previas)
    args = tuple(f"%{p}%" for _ in ("subject", "content") for p in previas)
    mudou = 0
    for m in db.query(f"SELECT seq, subject, content, fingerprint FROM memory_items WHERE profile_id=? AND ({casa})",
                      (profile_id, *args)):
        assunto, conteudo = sem_o_rastro(m["subject"], handle, account_id), sem_o_rastro(m["content"], handle, account_id)
        if assunto == m["subject"] and conteudo == m["content"]:
            continue
        fp = fingerprint(str(assunto), str(conteudo))
        if db.one("SELECT 1 FROM memory_items WHERE profile_id=? AND fingerprint=? AND seq<>?",
                  (profile_id, fp, m["seq"])) is not None:
            fp = f"{fp[:24]}r{m['seq']}"
        db.execute("UPDATE memory_items SET subject=?, content=?, fingerprint=? WHERE seq=?",
                   (assunto, conteudo, fp, m["seq"]))
        mudou += 1
    return mudou
