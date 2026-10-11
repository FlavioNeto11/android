"""Leitor IMAP da caixa compartilhada (catch-all `*@dominio` → uma caixa). Só leitura: `INBOX` em readonly e
`BODY.PEEK`, então nada é marcado como lido nem apagado.

Credencial só no construtor; nenhum erro devolvido carrega a senha, o corpo de e-mail ou a mensagem do servidor."""
from __future__ import annotations

import asyncio
import email
import imaplib
import re
import socket
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime

from ..application.ports import CabecalhoDeMensagem, Mensagem
from ..application.servico import ConfigEmail, EmailDoParque, ErroEmailDoParque

TIMEOUT_S = 15
MAX_MENSAGENS = 10
_MESES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
#: Aspas, barra invertida e quebra de linha no critério de busca fechariam a string do comando IMAP.
_PERIGOSO = re.compile(r'["\\\r\n]')
_TAGS = re.compile(r"<[^>]+>")
_CABECALHOS_DE_DESTINO = ("To", "Cc", "Delivered-To", "X-Original-To", "Envelope-To")


def _indisponivel() -> ErroEmailDoParque:
    return ErroEmailDoParque("email_indisponivel", "Caixa de e-mail indisponível.", 503)


def _decodificar(valor: str | None) -> str:
    if not valor:
        return ""
    try:
        return str(make_header(decode_header(valor)))
    except (ValueError, LookupError):
        return valor


def _texto(msg: Message) -> str:
    """`text/plain` quando existe; senão o `text/html` sem tags (o código vem no texto visível, não em estilo)."""
    plano, html = "", ""
    for parte in msg.walk():
        if parte.is_multipart() or parte.get_content_disposition() == "attachment":
            continue
        tipo = parte.get_content_type()
        if tipo not in ("text/plain", "text/html"):
            continue
        carga = parte.get_payload(decode=True)
        if not isinstance(carga, bytes):
            continue
        texto = carga.decode(parte.get_content_charset() or "utf-8", errors="replace")
        if tipo == "text/plain" and not plano:
            plano = texto
        elif tipo == "text/html" and not html:
            html = _TAGS.sub(" ", texto)
    return plano or html


def mensagem_de_bytes(bruto: bytes) -> Mensagem | None:
    """Bytes RFC822 → `Mensagem`. `None` quando a data não dá para provar: sem ela não se sabe se o código é novo."""
    msg = email.message_from_bytes(bruto)
    try:
        quando = parsedate_to_datetime(msg.get("Date", ""))
    except (TypeError, ValueError):
        return None
    if quando is None:
        return None
    quando = quando.replace(tzinfo=timezone.utc) if quando.tzinfo is None else quando.astimezone(timezone.utc)
    destinos = tuple(dict.fromkeys(
        a.lower() for _, a in getaddresses([_decodificar(h) for c in _CABECALHOS_DE_DESTINO
                                            for h in msg.get_all(c, [])]) if a))
    remetentes = getaddresses([_decodificar(h) for h in msg.get_all("From", [])])
    return Mensagem(remetente=(remetentes[0][1] if remetentes else "").lower(), destinatarios=destinos,
                    assunto=_decodificar(msg.get("Subject")), corpo=_texto(msg), recebida_em=quando)


#: Só estes campos saem do servidor, e nunca o corpo: `BODY.PEEK[HEADER.FIELDS (...)]`.
CAMPOS_DO_CABECALHO = "FROM SUBJECT DATE AUTHENTICATION-RESULTS"
_AUTENTICACAO = re.compile(r"\b(spf|dkim|dmarc)\s*=\s*([a-z]+)", re.IGNORECASE)
_DEVOLUCAO_ASSUNTO = re.compile(r"(undeliver|delivery status|delivery failure|failure notice|returned mail|n[ãa]o entregue)",
                                re.IGNORECASE)


def cabecalho_de_bytes(bruto: bytes) -> CabecalhoDeMensagem | None:
    """Bytes de CABEÇALHO RFC822 → `CabecalhoDeMensagem`. `None` quando a data não dá para provar."""
    msg = email.message_from_bytes(bruto)
    try:
        quando = parsedate_to_datetime(msg.get("Date", ""))
    except (TypeError, ValueError):
        return None
    if quando is None:
        return None
    quando = quando.replace(tzinfo=timezone.utc) if quando.tzinfo is None else quando.astimezone(timezone.utc)
    remetentes = getaddresses([_decodificar(h) for h in msg.get_all("From", [])])
    remetente = (remetentes[0][1] if remetentes else "").lower()
    assunto = _decodificar(msg.get("Subject"))
    resultados: dict[str, str] = {}
    for h in msg.get_all("Authentication-Results", []):
        for metodo, valor in _AUTENTICACAO.findall(_decodificar(h)):
            resultados.setdefault(metodo.lower(), valor.lower())
    devolucao = remetente.split("@")[0] in ("mailer-daemon", "postmaster") or bool(_DEVOLUCAO_ASSUNTO.search(assunto))
    return CabecalhoDeMensagem(remetente=remetente, assunto=assunto, recebida_em=quando,
                               autenticacao=tuple(sorted(resultados.items())), devolucao=devolucao)


class LeitorImap:
    def __init__(self, host: str, porta: int, usuario: str, senha: str) -> None:
        self._host, self._porta, self._usuario, self._senha = host, porta, usuario, senha

    def __repr__(self) -> str:
        return f"LeitorImap({self._host}:{self._porta}, usuario={self._usuario})"

    async def buscar(self, *, destinatario: str, remetente: str, desde: datetime) -> list[Mensagem]:
        if _PERIGOSO.search(destinatario) or _PERIGOSO.search(remetente):
            raise ErroEmailDoParque("email_invalido", "Critério de busca inválido.", 422)
        return await asyncio.to_thread(self._buscar_sincrono, destinatario, remetente, desde)

    async def listar_cabecalhos(self, *, destinatario: str, desde: datetime, limite: int) -> list[CabecalhoDeMensagem]:
        if _PERIGOSO.search(destinatario):
            raise ErroEmailDoParque("email_invalido", "Critério de busca inválido.", 422)
        return await asyncio.to_thread(self._cabecalhos_sincrono, destinatario, desde, limite)

    def _cabecalhos_sincrono(self, destinatario: str, desde: datetime, limite: int) -> list[CabecalhoDeMensagem]:
        dia = desde.astimezone(timezone.utc)
        data = f"{dia.day:02d}-{_MESES[dia.month - 1]}-{dia.year}"
        conn: imaplib.IMAP4_SSL | None = None
        try:
            conn = imaplib.IMAP4_SSL(self._host, self._porta, timeout=TIMEOUT_S)
            conn.login(self._usuario, self._senha)
            conn.select("INBOX", readonly=True)
            tipo, achados = conn.search(None, "SINCE", data, "TO", f'"{destinatario}"')
            if tipo != "OK":
                raise _indisponivel()
            saida: list[CabecalhoDeMensagem] = []
            for numero in (achados[0] or b"").split()[-max(1, limite):]:
                tipo, partes = conn.fetch(numero.decode("ascii"), f"(BODY.PEEK[HEADER.FIELDS ({CAMPOS_DO_CABECALHO})])")
                if tipo != "OK":
                    continue
                for parte in partes:
                    if isinstance(parte, tuple) and isinstance(parte[1], bytes):
                        achado = cabecalho_de_bytes(parte[1])
                        if achado is not None:
                            saida.append(achado)
            return sorted(saida, key=lambda c: c.recebida_em, reverse=True)
        except ErroEmailDoParque:
            raise
        except (imaplib.IMAP4.error, OSError, socket.timeout):
            raise _indisponivel() from None
        finally:
            if conn is not None:
                try:
                    conn.logout()
                except (imaplib.IMAP4.error, OSError):
                    pass

    def _buscar_sincrono(self, destinatario: str, remetente: str, desde: datetime) -> list[Mensagem]:
        dia = desde.astimezone(timezone.utc)
        data = f"{dia.day:02d}-{_MESES[dia.month - 1]}-{dia.year}"
        conn: imaplib.IMAP4_SSL | None = None
        try:
            conn = imaplib.IMAP4_SSL(self._host, self._porta, timeout=TIMEOUT_S)
            conn.login(self._usuario, self._senha)
            conn.select("INBOX", readonly=True)
            tipo, achados = conn.search(None, "SINCE", data, "TO", f'"{destinatario}"', "FROM", f'"{remetente}"')
            if tipo != "OK":
                raise _indisponivel()
            ids = (achados[0] or b"").split()[-MAX_MENSAGENS:]
            saida: list[Mensagem] = []
            for numero in ids:
                tipo, partes = conn.fetch(numero.decode("ascii"), "(BODY.PEEK[])")
                if tipo != "OK":
                    continue
                for parte in partes:
                    if isinstance(parte, tuple) and isinstance(parte[1], bytes):
                        msg = mensagem_de_bytes(parte[1])
                        if msg is not None:
                            saida.append(msg)
            return sorted(saida, key=lambda m: m.recebida_em, reverse=True)
        except ErroEmailDoParque:
            raise
        except (imaplib.IMAP4.error, OSError, socket.timeout):
            # Sem `from exc` nem texto do servidor: falha de login pode ecoar o usuário, e nada daqui pode vazar.
            raise _indisponivel() from None
        finally:
            if conn is not None:
                try:
                    conn.logout()
                except (imaplib.IMAP4.error, OSError):
                    pass


def construir_email_do_parque(*, dominio: str, allowlist: tuple[str, ...], host: str, porta: int,
                              usuario: str, senha: str, remetente_codigo: str = "") -> EmailDoParque:
    """Leitor `None` quando falta host, usuário ou senha: gerar e validar endereço funciona; ler código dá
    `email_indisponivel`."""
    imap = bool(host.strip() and usuario.strip() and senha)
    leitor = LeitorImap(host.strip(), porta, usuario.strip(), senha) if imap else None
    return EmailDoParque(ConfigEmail(dominio_padrao=dominio.strip().lower(), allowlist=allowlist,
                                     imap_configurado=imap, remetente_codigo=remetente_codigo.strip().lower()), leitor)
