"""Quais @ são NOSSOS: os das contas vivas e as lápides das que saíram por bloqueio (item 29.23, ADR-068).

A conta bloqueada some de tudo, mas o produto precisa continuar sabendo que aquele @ foi nosso: sem isso, uma persona
nossa trataria a conta antiga como terceira e poderia responder ou engajar com ela, que é engajamento simulado
(ADR-050). A lápide (`contas_retiradas`, migração 071) guarda só o HASH do @ normalizado, nunca o texto.

Este módulo também tem o casamento do rastro textual da conta (@, id e e-mail) que a retirada usa para reescrever
`memory_items` (`social/memory.py::reescrever_memoria`, de TODAS as personas, 29.32). O HISTÓRICO (eventos, comandos,
etapas, aprovações, interações, ai_calls) fica intacto por decisão do dono (ADR-068, opção A): as provas antigas seguem
legíveis.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

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


def parece_email(texto: str | None) -> bool:
    """Forma de e-mail (`x@y.z`, sem espaço): o login da conta pode ser um @ do app ou um e-mail, e só o e-mail entra
    na reescrita por endereço."""
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", (texto or "").strip()))


def emails_so_desta_conta(db: Database, *, profile_id: str, account_id: str, handle: str | None,
                          ancora: bool) -> list[str]:
    """Os e-mails que IDENTIFICAM a conta que sai (o `handle`, se a conta é de e-mail, e o login dela) E que nenhuma
    outra conta VIVA usa (29.32). O Outlook segue vivo com o mesmo endereço do login do Instagram retirado: o
    endereço continua verdadeiro no produto e não pode sumir da memória. Só conta (`profile_accounts` e a credencial
    por conta, de qualquer persona, e a credencial LEGADA de outra persona, ou da mesma se a conta que sai não é a
    âncora: ali ela é a credencial de outra conta) segura o e-mail; o e-mail de cadastro da persona (`email` do
    perfil) não é conta e fica de fora, a persona nem precisa dele na memória. Minúsculas, sem repetição."""
    candidatos = [handle]
    cred = db.one("SELECT login_identifier FROM account_credentials WHERE account_id=?", (account_id,))
    candidatos.append(cred["login_identifier"] if cred is not None else None)
    if ancora:
        legada = db.one("SELECT login_identifier FROM instagram_credentials WHERE profile_id=?", (profile_id,))
        candidatos.append(legada["login_identifier"] if legada is not None else None)
    out: list[str] = []
    for bruto in candidatos:
        email = str(bruto or "").strip().lower()
        if not parece_email(email) or email in out:
            continue
        viva = (db.scalar("SELECT COUNT(*) FROM profile_accounts WHERE id<>? AND lower(handle)=?", (account_id, email))
                or db.scalar("SELECT COUNT(*) FROM account_credentials WHERE account_id<>? AND lower(login_identifier)=?",
                             (account_id, email))
                or db.scalar("SELECT COUNT(*) FROM instagram_credentials WHERE lower(login_identifier)=?"
                             + ("" if not ancora else " AND profile_id<>?"),
                             (email,) if not ancora else (email, profile_id)))
        if not viva:
            out.append(email)
    return out


def handle_vivo(db: Database, *, profile_id: str, account_id: str, handle: str | None, ancora: bool) -> bool:
    """Este @ é de OUTRA conta viva (29.32)? O mesmo @ em outro app da mesma ou de outra persona, ou o cadastro de
    OUTRA persona. Conta viva não some da memória, como a lápide viva não é rastro no passe retroativo. O cadastro da
    própria persona só conta se a conta que sai NÃO é a âncora (a âncora tem o @ nos dois lugares e os dois saem)."""
    limpo = normalizar(handle)
    if not limpo or parece_email(limpo):
        return False
    chaves = (limpo, f"@{limpo}")
    if db.scalar("SELECT COUNT(*) FROM profile_accounts WHERE id<>? AND lower(handle) IN (?,?)", (account_id, *chaves)):
        return True
    return bool(db.scalar("SELECT COUNT(*) FROM instagram_profiles WHERE lower(username) IN (?,?)"
                          + (" AND id<>?" if ancora else ""), (*chaves, *((profile_id,) if ancora else ()))))


def _padroes(handle: str | None, account_id: str | None, emails: Sequence[str] = ()) -> list[re.Pattern[str]]:
    """O e-mail inteiro, o @ (com ou sem `@`, sem diferenciar caixa, só como palavra inteira: `ana` não casa dentro de
    `ana.silva`, de `banana`, de `foo@ana.com` nem é a parte local de `ana@x.com`) e o id da conta em texto. O e-mail
    vem PRIMEIRO: o `ana@x.com` de uma conta que sai some inteiro antes de o @ `ana` ser procurado."""
    out: list[re.Pattern[str]] = []
    for email in emails:
        limpo = email.strip().lower()
        if limpo:
            out.append(re.compile(rf"(?<![\w.+-]){re.escape(limpo)}(?!\w|\.\w)", re.IGNORECASE))
    limpo = normalizar(handle)
    # Handle em forma de e-mail (conta de e-mail): só vai ao texto como ENDEREÇO, e só se está em `emails` (é exclusivo
    # da conta que sai). Se outra conta viva o usa, ele não é rastro: nem o padrão do handle o pode redigir.
    if limpo and not parece_email(limpo):
        # Mesma fronteira do `esquecer_conta` do aprendizado: nada de letra/dígito/`_`/`@` antes, nem `palavra.`
        # (seria `x.ana`); depois, nada de letra/dígito/`_`, nem `.palavra` (`ana.silva`), nem `@palavra` (a parte
        # local de um e-mail). O ponto final de frase passa: o Instagram não aceita handle terminado em ponto.
        out.append(re.compile(rf"(?<![\w@])(?<!\w\.)@?{re.escape(limpo)}(?!\w|\.\w|@\w)", re.IGNORECASE))
    if account_id:
        out.append(re.compile(re.escape(account_id), re.IGNORECASE))
    return out


def sem_o_rastro(texto: str | None, handle: str | None, account_id: str | None = None,
                 emails: Sequence[str] = ()) -> str | None:
    """`texto` com o e-mail, o @ e o id da conta trocados por `[conta removida]`. `None` e vazio passam como vieram."""
    if not texto:
        return texto
    for padrao in _padroes(handle, account_id, emails):
        texto = padrao.sub(MARCADOR, texto)
    return texto


def previa_do_rastro(handle: str | None, account_id: str | None, emails: Sequence[str] = ()) -> list[str]:
    """As pistas em minúsculas para o `LIKE` que pré-filtra no banco; quem decide é a regex de `sem_o_rastro`."""
    pistas = [normalizar(handle) if not parece_email(handle) else "", (account_id or "").lower(), *(e.strip().lower() for e in emails)]
    return list(dict.fromkeys(p for p in pistas if p))
