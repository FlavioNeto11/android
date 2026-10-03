"""Caixa de ENTRADA do bot do Telegram para a orquestradora (provisória, até o item 28.15 no central).

Long-poll do `getUpdates` com `offset` (confirma o que leu), pelo mesmo `EnvSettings` + `CanalTelegram` da
plataforma. Só aceita mensagens do chat configurado em TELEGRAM_CHAT_ID: o resto é ignorado e contado, nunca
guardado. Cada mensagem aceita vira UMA linha no stdout (evento do Monitor) e uma linha JSON em
`telegram_inbox.jsonl`; o `update_id` confirmado fica em `telegram_offset.txt`, então reiniciar não repete nada.

Mensagem que PARECE conter segredo (senha, código, token, chave) é retida: guarda-se só o aviso, nunca o texto.
Nada do token é impresso; erro de rede vira uma linha "erro: ..." sem URL (o adaptador já limpa).

Uso: backend/.venv/Scripts/python.exe .claude/canais/telegram_inbox.py [--uma-vez]
Parar: quem o lançou (Monitor) mata o processo; o offset está salvo.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r"C:\git\android")
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.config import EnvSettings  # noqa: E402

# O httpx loga a URL (com o token do bot) em INFO: fora do backend não há RedactingFilter, então cala o httpx.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
from app.modules.avisos.adapters.telegram import CanalTelegram, TokenAusente, _json  # noqa: E402
from app.modules.avisos.application.entrega import FalhaDeEnvio  # noqa: E402

#: Os DADOS (offset, mensagens, chats vistos) ficam fora do Git: têm id de chat. O código fica em .claude/canais/.
AQUI = ROOT / ".claude" / "handoffs" / "telegram"
INBOX = AQUI / "telegram_inbox.jsonl"
OFFSET = AQUI / "telegram_offset.txt"
#: Chats de convidado já vistos (dono, 03/10 20:55Z: "me avise se alguém te adicionar" e de quem são os chats novos).
VISTOS = AQUI / "telegram_chats_vistos.json"


def _vistos() -> set[str]:
    try:
        return set(json.loads(VISTOS.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _marcar_visto(chat: object) -> bool:
    """True quando o chat é novo (1ª mensagem ou 1ª entrada do bot nele)."""
    vistos = _vistos()
    if str(chat) in vistos:
        return False
    vistos.add(str(chat))
    VISTOS.write_text(json.dumps(sorted(vistos)), encoding="utf-8")
    return True


#: Histórico de quem fala com o bot (dono, Telegram 03/10 22:44Z: "o nome, o código do chat e o máximo de informações
#: que puder, precisamos desse histórico"). Fora do Git, como todo vínculo entre id e pessoa (regra C-08).
CONTATOS = ROOT / ".claude" / "handoffs" / "canais" / "contatos-telegram.json"
#: Campos do `from` e do `chat` do Telegram guardados como vieram (o que a API dá sobre a pessoa; nada é inferido).
_CAMPOS_DA_PESSOA = ("id", "is_bot", "first_name", "last_name", "username", "language_code", "is_premium")
_CAMPOS_DO_CHAT = ("id", "type", "title", "username", "first_name", "last_name")


def _contatos() -> dict[str, dict[str, object]]:
    try:
        dado = json.loads(CONTATOS.read_text(encoding="utf-8"))
        return dado if isinstance(dado, dict) else {}
    except (OSError, ValueError):
        return {}


def _registrar_contato(chat: dict[str, object], autor: dict[str, object] | None, quando: str, *,
                       evento: str, texto: str | None = None, message_id: object = None) -> None:
    """Uma entrada por chat, com a identidade como o Telegram a deu e o histórico de eventos (mensagem, pergunta do
    nome, bot posto ou tirado). Texto retido como segredo chega aqui já trocado pelo aviso de retenção."""
    contatos = _contatos()
    c = contatos.setdefault(str(chat.get("id")), {"chat": {}, "pessoa": {}, "primeira_vez": quando, "estado":
                                                    "aguardando_nome", "nome_informado": None, "historico": []})
    c["chat"] = {k: chat[k] for k in _CAMPOS_DO_CHAT if chat.get(k) is not None}
    if autor:
        c["pessoa"] = {k: autor[k] for k in _CAMPOS_DA_PESSOA if autor.get(k) is not None}
    c["ultima_vez"] = quando
    # A 1ª mensagem DEPOIS da pergunta do nome é a resposta dela: guarda-se como o nome informado (o dono confirma).
    if evento == "mensagem" and c.get("estado") == "aguardando_nome" and any(
            h.get("evento") == "pergunta_do_nome" for h in c["historico"]):
        c["nome_informado"] = (texto or "")[:120]
        c["estado"] = "aguardando_dono"
    c["historico"].append({k: v for k, v in (("quando", quando), ("evento", evento), ("message_id", message_id),
                                             ("texto", texto)) if v is not None})
    CONTATOS.write_text(json.dumps(contatos, ensure_ascii=False, indent=1), encoding="utf-8")
#: Sinais de segredo: a mensagem é retida inteira (o dono grava senha no painel, nunca por aqui). Retém pela FORMA de
#: um valor, não pela palavra: "a chave do Trello depende do domínio" passava como segredo (falso positivo, 03/10
#: 20:42Z). Retém: palavra-chave seguida de ":"/"=" ou "é" e um valor; sequência longa com letra e dígito; prefixo de
#: chave conhecido; código de 6 a 8 dígitos solto.
_SEGREDO = re.compile(
    r"((senha|password|passw|token|chave|api[_ -]?key|secret|segredo|c[óo]digo)\s*(do\s+\w+\s*)?(\s*[:=]\s*\S{4,}|\s+é\s+(?=\S*[\d!@#$%&*_\-])\S{4,})"
    r"|\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{20,}\b"
    r"|\b(sk-|ghp_|AIza|xox[bp]-|ATTA)\w"
    r"|(?<![\d/.,:])\b\d{6,8}\b(?![\d/.,:]))",
    re.I)


def _segredo(valor: object) -> str:
    obter = getattr(valor, "get_secret_value", None)
    return str(obter()) if callable(obter) else ""


def _hora(ts: object) -> str:
    try:
        return datetime.fromtimestamp(int(str(ts)), tz=timezone.utc).strftime("%H:%M:%SZ")
    except (TypeError, ValueError):
        return "??:??Z"


def _iso(ts: object) -> str:
    try:
        return datetime.fromtimestamp(int(str(ts)), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ler_offset() -> int:
    try:
        return int(OFFSET.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _gravar_offset(update_id: int) -> None:
    OFFSET.write_text(str(update_id), encoding="utf-8")


def _emitir(registro: dict[str, object]) -> None:
    with INBOX.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    texto = registro.get("texto") or ""
    if registro.get("de") == "convidado":
        # Convidado (dono, 03/10 20:51Z): a mesma regra do Trello. Pergunta se responde; pedido passa pelo dono.
        novo = " NOVO" if registro.get("novo") else ""
        print(f"[Telegram de convidado{novo} {registro['hora']} chat={registro['chat']} msg={registro['message_id']} "
              f"({registro.get('remetente')})] {texto}", flush=True)
        return
    print(f"[Telegram do dono {registro['hora']} msg={registro['message_id']}] {texto}", flush=True)


PERGUNTA_DO_NOME = ("Olá! Eu sou a ANA, a IA Gerente de Operações da Central de Aparelhos (sou uma IA, não uma "
                    "pessoa). Para eu saber com quem estou falando: qual é o seu nome?")


async def _pedir_nome(canal: CanalTelegram, chat: object, responder_a: object) -> bool:
    corpo: dict[str, object] = {"chat_id": chat, "text": PERGUNTA_DO_NOME}
    if responder_a:
        corpo["reply_parameters"] = {"message_id": responder_a}
    try:
        r = await canal._chamar("sendMessage", json=corpo)
        print(f"[Telegram: pergunta do nome enviada ao chat={chat}: {r.status_code}]", flush=True)
        return r.status_code == 200
    except Exception as exc:  # noqa: BLE001 - a caixa não pode cair por causa de um envio
        print(f"[Telegram: pergunta do nome FALHOU no chat={chat}: {type(exc).__name__}]", flush=True)
        return False


async def _uma_volta(canal: CanalTelegram, chat_id: str, offset: int) -> int:
    resposta = await canal._chamar("getUpdates", params={"offset": offset + 1 if offset else 0, "timeout": 50,
                                                          "limit": 50, "allowed_updates": '["message","my_chat_member"]'})
    corpo = _json(resposta)
    if resposta.status_code != 200 or corpo.get("ok") is not True:
        raise canal._falha(resposta)
    ultimo = offset
    ignoradas = 0
    for update in corpo.get("result") or []:
        if not isinstance(update, dict):
            continue
        uid = int(update.get("update_id") or 0)
        ultimo = max(ultimo, uid)
        membro = update.get("my_chat_member")
        if isinstance(membro, dict):
            # O bot foi posto ou tirado de um chat (grupo, canal, ou um privado bloqueado/desbloqueado). Só avisa.
            chat = membro.get("chat") or {}
            if str(chat.get("id")) != str(chat_id):
                autor = membro.get("from") or {}
                quem = " ".join(str(x) for x in (autor.get("first_name"), autor.get("username")) if x)
                status = (membro.get("new_chat_member") or {}).get("status")
                if chat.get("type") != "private":
                    # Privado não conta como visto aqui: o /start manda este aviso antes da 1ª mensagem, e a pergunta do
                    # nome tem de sair nela.
                    _marcar_visto(chat.get("id"))
                _registrar_contato(chat, autor, _iso(membro.get("date")), evento=f"bot_{status}")
                print(f"[Telegram: bot {status} num chat {chat.get('type')} chat={chat.get('id')} por ({quem}) "
                      f"às {_hora(membro.get('date'))}]", flush=True)
            continue
        msg = update.get("message")
        if not isinstance(msg, dict):
            continue
        chat = msg.get("chat") or {}
        do_dono = str(chat.get("id")) == str(chat_id)
        if not do_dono and chat.get("type") != "private":
            ignoradas += 1                                 # grupo ou canal: fora do escopo
            continue
        texto = msg.get("text") or msg.get("caption") or ""
        if not texto:
            texto = "[mensagem sem texto: foto, áudio ou arquivo; não lido]"
        elif _SEGREDO.search(texto):
            texto = "[mensagem retida: parece conter segredo; grave senha/código só no painel]"
        registro: dict[str, object] = {"update_id": uid, "message_id": msg.get("message_id"),
                                       "hora": _hora(msg.get("date")), "texto": texto[:2000]}
        if not do_dono:
            autor = msg.get("from") or {}
            registro.update(de="convidado", chat=chat.get("id"), novo=_marcar_visto(chat.get("id")),
                            remetente=" ".join(str(x) for x in (autor.get("first_name"), autor.get("username")) if x))
            quando = _iso(msg.get("date"))
            _registrar_contato(chat, autor, quando, evento="mensagem", texto=texto[:2000],
                               message_id=msg.get("message_id"))
            if registro["novo"]:
                # Dono, 03/10 21:44Z: chat novo recebe SÓ a pergunta do nome; nada mais até o dono autorizar a pessoa.
                if await _pedir_nome(canal, chat.get("id"), msg.get("message_id")):
                    _registrar_contato(chat, None, _iso(None), evento="pergunta_do_nome")
        _emitir(registro)
    if ultimo != offset:
        _gravar_offset(ultimo)
    if ignoradas:
        print(f"ignoradas {ignoradas} mensagem(ns) de outro chat", file=sys.stderr, flush=True)
    return ultimo


async def principal(uma_vez: bool) -> int:
    env = EnvSettings()
    token = _segredo(env.telegram_bot_token)
    chat_id = _segredo(env.telegram_chat_id)
    try:
        canal = CanalTelegram(token, chat_id, timeout_s=65.0)
    except TokenAusente as exc:
        print(f"erro: {exc}", flush=True)
        return 2
    if not chat_id:
        print("erro: TELEGRAM_CHAT_ID vazio", flush=True)
        return 2
    offset = _ler_offset()
    print(f"caixa de entrada ligada (offset {offset})", file=sys.stderr, flush=True)
    while True:
        try:
            offset = await _uma_volta(canal, chat_id, offset)
        except FalhaDeEnvio as exc:
            print(f"erro: {exc.motivo}", flush=True)
            if exc.definitiva:
                return 1
            time.sleep(exc.espera_s or 15)
        except Exception as exc:  # noqa: BLE001 - nunca derruba a caixa; sem str(exc) (pode trazer URL)
            print(f"erro: {type(exc).__name__}", flush=True)
            time.sleep(15)
        if uma_vez:
            return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uma-vez", action="store_true")
    args = ap.parse_args()
    return asyncio.run(principal(args.uma_vez))


if __name__ == "__main__":
    raise SystemExit(main())
