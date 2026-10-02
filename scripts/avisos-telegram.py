"""Aviso fora do painel (item 28.11): descobre o `chat_id` do Telegram e testa o envio.

Dois comandos, os dois lendo `TELEGRAM_BOT_TOKEN` (e `TELEGRAM_CHAT_ID`) do `.env` do central pelo MESMO mecanismo das
chaves de IA (`EnvSettings`), e nenhum imprime o token:

  python scripts/avisos-telegram.py descobrir   lista os chats que escreveram ao bot (id, tipo, nome, @usuário).
                                                Antes: mande `/start` ao bot. Só LÊ (`getUpdates` sem offset: não
                                                confirma nem descarta nada, pode rodar de novo à vontade).
  python scripts/avisos-telegram.py testar      manda UMA mensagem de teste ao `TELEGRAM_CHAT_ID`. É um envio real:
                                                só o dono roda.

Por que script e não rota da API: é um passo de instalação, feito uma vez, que precisa funcionar ANTES de o backend ser
reiniciado com o token novo (o script lê o `.env` na hora) e sem criar, na API, uma rota que dispara chamada de
saída. A decisão do `chat_id` continua sendo do dono: o script mostra, ele confere e grava no `.env`.

Saída: o que sobrou do erro nunca contém a URL do bot (o token vai nela); ver `app.modules.avisos.adapters.telegram`.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.config import EnvSettings  # noqa: E402
from app.modules.avisos.adapters.telegram import CanalTelegram, TokenAusente  # noqa: E402
from app.modules.avisos.application.entrega import FalhaDeEnvio  # noqa: E402

TITULO_DO_TESTE = "Central de Aparelhos: teste do aviso fora do painel"
CORPO_DO_TESTE = "Se você leu isto, o canal está pronto. Pode ligar avisos.enabled."


def _segredo(valor: object) -> str:
    obter = getattr(valor, "get_secret_value", None)
    return str(obter()) if callable(obter) else ""


async def _descobrir(canal: CanalTelegram, out: TextIO) -> int:
    chats = await canal.descobrir_chats()
    if not chats:
        print("Nenhum chat encontrado. Mande /start ao bot no Telegram e rode de novo.", file=out)
        return 1
    print("Chats que escreveram ao bot (grave o `id` certo em TELEGRAM_CHAT_ID no .env):", file=out)
    for c in chats:
        print(f"  id={c.id}  tipo={c.tipo}  nome={c.titulo or '-'}  usuario={'@' + c.username if c.username else '-'}",
              file=out)
    return 0


async def executar(argv: Sequence[str], env: EnvSettings, out: TextIO, client: httpx.AsyncClient | None = None) -> int:
    ap = argparse.ArgumentParser(description="Telegram do aviso fora do painel (28.11)")
    ap.add_argument("comando", choices=("descobrir", "testar"))
    args = ap.parse_args(argv)
    token = _segredo(env.telegram_bot_token)
    chat_id = _segredo(env.telegram_chat_id)
    try:
        canal = CanalTelegram(token, chat_id, client=client)
        if args.comando == "descobrir":
            return await _descobrir(canal, out)
        if not chat_id:
            print("TELEGRAM_CHAT_ID vazio: rode `descobrir`, confira o id e grave no .env.", file=out)
            return 2
        await canal.enviar(TITULO_DO_TESTE, CORPO_DO_TESTE, None)
        print("Mensagem de teste enviada.", file=out)
        return 0
    except TokenAusente as exc:
        print(str(exc), file=out)
        return 2
    except FalhaDeEnvio as exc:
        print(f"Falhou: {exc.motivo}", file=out)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(executar(sys.argv[1:] if argv is None else argv, EnvSettings(), sys.stdout))


if __name__ == "__main__":
    raise SystemExit(main())
