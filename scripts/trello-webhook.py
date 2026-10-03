"""Webhook do Trello (item 32.2, passo 5; `docs/design/trello-integracao.md`, §8.7): mostra ou faz o cadastro.

  python scripts/trello-webhook.py --ensaio    (padrão) só LÊ o Trello e imprime o que seria feito: criar, recriar, apagar ou
                                               manter o webhook de cada quadro de `trello.quadros`. Não escreve nada.
  python scripts/trello-webhook.py --aplicar   faz isso (`POST /1/webhooks`). É um efeito real no Trello do dono: só depois
                                               do 32.2 implantado, do portal no ar (o Trello faz um HEAD na URL ao criar) e do
                                               "vai" da orquestradora.

Lê `TRELLO_API_KEY` e `TRELLO_TOKEN` do `.env` pelo MESMO mecanismo das outras chaves (`EnvSettings`), e os quadros e a URL de
`trello.quadros` e `trello.webhook.callback_url` do `config.yaml`. A chave e o token vão SÓ no cabeçalho `Authorization`;
nenhum é impresso, e a URL cadastrada não pode levar parâmetro nem credencial. O mesmo cadastro roda sozinho no líder, de hora
em hora, quando `trello.webhook.enabled` é true (desligado de fábrica).
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

from app.config import Config, get_config  # noqa: E402
from app.modules.avisos.adapters.trello import ClienteTrello, FalhaDoTrello  # noqa: E402
from app.modules.avisos.infrastructure.trello_webhook import CadastroDoWebhook  # noqa: E402


def _segredo(valor: object) -> str:
    obter = getattr(valor, "get_secret_value", None)
    return str(obter()).strip() if callable(obter) else ""


async def executar(argv: Sequence[str], cfg: Config, out: TextIO, client: httpx.AsyncClient | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cadastro do webhook do Trello (32.2)")
    grupo = ap.add_mutually_exclusive_group()
    grupo.add_argument("--ensaio", action="store_true", help="só mostra o que seria feito (padrão)")
    grupo.add_argument("--aplicar", action="store_true", help="faz o cadastro no Trello")
    args = ap.parse_args(argv)
    chave, token = _segredo(cfg.env.trello_api_key), _segredo(cfg.env.trello_token)
    if not (chave and token):
        print("Faltam TRELLO_API_KEY e/ou TRELLO_TOKEN no .env (docs/operacao.md).", file=out)
        return 2
    if not cfg.file.trello.quadros:
        print("trello.quadros está vazio no config.yaml: não há quadro a cadastrar.", file=out)
        return 2
    if args.aplicar and not _segredo(cfg.env.trello_api_secret):
        print("Falta TRELLO_API_SECRET no .env: sem ele a Central não verifica a assinatura, e o webhook não deve nascer.",
              file=out)
        return 2
    cad = CadastroDoWebhook(cfg)
    cliente = ClienteTrello(chave, token, client=client)
    try:
        passos = await cad.planejar(cliente)
        if args.aplicar:
            await cad.aplicar(cliente, [p for p in passos if p.acao != "manter"])
    except ValueError as exc:
        print(f"Configuração: {exc}.", file=out)
        return 2
    except FalhaDoTrello as exc:
        print(f"Falhou: {exc.motivo}", file=out)
        return 1
    print("Ensaio: nada foi escrito no Trello." if not args.aplicar else "Aplicado.", file=out)
    if not cfg.file.trello.webhook.enabled:
        print("trello.webhook.enabled é false: o plano é o de DESLIGAR (apagar só os webhooks da Central).", file=out)
    for p in passos:
        print(f"  {p.acao:8} quadro={p.quadro}  {p.motivo}", file=out)
    if not passos:
        print("  (nada a fazer)", file=out)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(executar(sys.argv[1:] if argv is None else argv, get_config(), sys.stdout))


if __name__ == "__main__":
    raise SystemExit(main())
