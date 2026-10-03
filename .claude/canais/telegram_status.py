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
import unicodedata
import asyncio
import logging
import html
import re
import sys
from datetime import datetime, timezone
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


#: Modelo das boas-vindas a um convidado recém-autorizado (C-10). Sem nome de pessoa: no chat do convidado, o dono é
#: "o dono" (C-02; as boas-vindas de 03/10 22:53Z saíram com nomes e tiveram de ser editadas).
BOAS_VINDAS = ("Obrigada! O dono da Central confirmou que você é convidado dele, então pode falar comigo por aqui.\n\n"
               "Eu sou a ANA, a IA Gerente de Operações da Central de Aparelhos. Você pode me perguntar como andam as "
               "frentes, os prazos e o que mudou. Quando for um pedido, eu levo ao dono, e ele autoriza antes.")


def _nomes_proibidos() -> list[str]:
    """Nomes de pessoa que nunca vão ao chat de um convidado: os dos convidados e os do dono, do arquivo local
    (`nome` de cada convidado e `_nomes_do_dono`). Cada parte do nome com 3+ letras conta."""
    try:
        dados = json.loads(MEMBROS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    nomes = [str(c.get("nome") or "") for c in dados.get("convidados") or []] + list(dados.get("_nomes_do_dono") or [])
    return sorted({p for n in nomes for p in re.findall(r"\w{3,}", n.lower())})


#: O histórico de quem fala com o bot (dono, 03/10 22:44Z) leva também o que a ANA respondeu ao convidado.
CONTATOS = ROOT / ".claude" / "handoffs" / "canais" / "contatos-telegram.json"


def _historico_de_saida(chat: str, message_id: object, texto: str) -> None:
    try:
        contatos = json.loads(CONTATOS.read_text(encoding="utf-8"))
        entrada = contatos[str(chat)]
    except (OSError, ValueError, KeyError):
        return                                         # sem registro do chat: a caixa ainda não o viu; nada a anexar
    quando = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entrada.setdefault("historico", []).append({"quando": quando, "evento": "resposta_da_ana",
                                                "message_id": message_id, "texto": texto[:2000]})
    CONTATOS.write_text(json.dumps(contatos, ensure_ascii=False, indent=1), encoding="utf-8")


def _sem_acento(t: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", t) if unicodedata.category(ch) != "Mn")


async def _enviar(texto: str, reply_to: int | None, chat: str | None = None) -> int:
    env = EnvSettings()
    token = _segredo(env.telegram_bot_token)
    chat_id = _segredo(env.telegram_chat_id)
    if chat:
        if not _chat_de_convidado(chat):
            print("chat não registrado como convidado em membros-trello.json; nada enviado")
            return 2
        palavras = set(re.findall(r"\w+", _sem_acento(_sem_tags(texto).lower())))
        achados = [n for n in _nomes_proibidos() if _sem_acento(n) in palavras]
        if achados:
            # C-02: nome de pessoa só no chat do dono. Não imprime qual nome (o log fica no terminal da sessão).
            print(f"recusado: o texto para o convidado tem {len(achados)} nome(s) de pessoa; use 'o dono' e nada enviado")
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
        if chat:
            _historico_de_saida(chat, mid, _sem_tags(texto))
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
