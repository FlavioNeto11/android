"""Envia ao dono, pelo bot do Telegram configurado na plataforma, o texto de um arquivo (HTML do Telegram).

Usa o MESMO mecanismo do backend e de `scripts/avisos-telegram.py`: `EnvSettings` lê TELEGRAM_BOT_TOKEN e
TELEGRAM_CHAT_ID do `.env` do central e `CanalTelegram` faz o `sendMessage`. Nada do segredo é impresso: a saída
é só "enviado" ou o motivo da falha já limpo pelo adaptador.

O arquivo é texto em HTML do Telegram (<b>, <i>, <code>, <a href>); "<", ">" e "&" literais devem ir como
&lt; &gt; &amp;. Se o Telegram recusar o HTML (400), reenvia como texto puro, sem as tags, para a mensagem
nunca se perder.

Uso: backend/.venv/Scripts/python.exe .claude/canais/telegram_status.py <arquivo> [--reply-to <message_id>]
"""
from __future__ import annotations

import argparse
import json
import asyncio
import logging
import html
import re
import sys
from pathlib import Path

ROOT = Path(r"C:\git\android")
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.config import EnvSettings  # noqa: E402

# O httpx loga a URL (com o token do bot) em INFO: fora do backend não há RedactingFilter, então cala o httpx.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
from app.modules.avisos.adapters.telegram import TEXTO_MAX, CanalTelegram, TokenAusente, _json  # noqa: E402
from app.modules.avisos.application.entrega import FalhaDeEnvio  # noqa: E402

_TAGS = re.compile(r"</?(b|i|u|s|code|pre|a)(\s[^>]*)?>")


def _segredo(valor: object) -> str:
    obter = getattr(valor, "get_secret_value", None)
    return str(obter()) if callable(obter) else ""


def _sem_tags(texto: str) -> str:
    return html.unescape(_TAGS.sub("", texto))


#: Quem pode receber fora do dono: só convidado registrado no arquivo local (fora do Git) com o chat já conhecido.
MEMBROS = ROOT / ".claude" / "handoffs" / "canais" / "membros-trello.json"     # fora do Git: vínculo id↔pessoa


def _chat_de_convidado(chat: str) -> bool:
    try:
        dados = json.loads(MEMBROS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return any(str(c.get("telegram_chat_id")) == chat for c in dados.get("convidados") or [] if c.get("telegram_chat_id"))


async def _enviar(texto: str, reply_to: int | None, chat: str | None = None) -> int:
    env = EnvSettings()
    token = _segredo(env.telegram_bot_token)
    chat_id = _segredo(env.telegram_chat_id)
    if chat:
        if not _chat_de_convidado(chat):
            print("chat não registrado como convidado em membros-trello.json; nada enviado")
            return 2
        chat_id = chat
    try:
        canal = CanalTelegram(token, chat_id)
    except TokenAusente as exc:
        print(str(exc))
        return 2
    if not chat_id:
        print("TELEGRAM_CHAT_ID vazio no .env do central.")
        return 2
    corpo: dict[str, object] = {"chat_id": chat_id, "text": texto[:TEXTO_MAX], "parse_mode": "HTML",
                                "disable_web_page_preview": True}
    if reply_to:
        corpo["reply_parameters"] = {"message_id": reply_to}
    try:
        resposta = await canal._chamar("sendMessage", json=corpo)
        if resposta.status_code == 400:
            # HTML recusado: manda o texto puro para a mensagem chegar de qualquer jeito.
            corpo.pop("parse_mode")
            corpo["text"] = _sem_tags(texto)[:TEXTO_MAX]
            resposta = await canal._chamar("sendMessage", json=corpo)
            modo = "texto puro (HTML recusado)"
        else:
            modo = "HTML"
        if resposta.status_code != 200 or _json(resposta).get("ok") is not True:
            raise canal._falha(resposta)
        mid = (_json(resposta).get("result") or {}).get("message_id")
        print(f"enviado {modo} ({len(texto)} chars) message_id={mid}")
        return 0
    except FalhaDeEnvio as exc:
        print(f"Falhou: {exc.motivo}")
        return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("arquivo")
    ap.add_argument("--reply-to", type=int, default=None)
    ap.add_argument("--chat", default=None, help="convidado registrado (membros-trello.json); sem isto, vai ao dono")
    ap.add_argument("--titulo", default=None, help="compatibilidade: vira a 1ª linha em negrito")
    args = ap.parse_args()
    texto = Path(args.arquivo).read_text(encoding="utf-8").strip()
    if not texto:
        print("arquivo vazio")
        return 2
    if args.titulo:
        texto = f"<b>{html.escape(args.titulo)}</b>\n{texto}"
    return asyncio.run(_enviar(texto, args.reply_to, args.chat))


if __name__ == "__main__":
    raise SystemExit(main())
