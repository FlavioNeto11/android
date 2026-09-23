"""Entrada do agente: `python -m app.worker --config worker.yaml [--enroll <token>]`.

Roda como SERVIÇO na máquina do worker — tarefa agendada no Windows, unidade systemd no Linux. Processo iniciado
dentro de uma sessão SSH pertence ao job dela e o Windows o mata no logout; foi medido em 19/09, com o emulador
morrendo em segundos já com o WHPX operacional.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from ..security.redaction import RedactingFilter
from . import AGENT_VERSION
from .agent import Agent
from .settings import load_settings


def instalar_redacao_de_log(raiz: logging.Logger | None = None) -> None:
    """Põe o filtro de redação nos HANDLERS do log do agente (achado #128).

    O backend instalava o filtro e o agente não — e o agente roda em OUTRA máquina, com `basicConfig` próprio: o
    processo que fica mais perto do aparelho era justamente o único sem a defesa secundária.

    No HANDLER, e não no logger raiz, porque filtro de logger só se aplica ao que é emitido NAQUELE logger. Tudo
    que o agente escreve sai em `poc.worker.*` e apenas PROPAGA até a raiz — um `addFilter` na raiz não redigiria
    uma linha sequer, e falharia em silêncio, que é o pior jeito de uma proteção falhar.

    `redaction` não traz dependência nenhuma (só `re`): a promessa das seis dependências do agente continua de pé.
    """
    for handler in (raiz or logging.getLogger()).handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.worker",
                                description="Agente do parque: hospeda aparelhos e obedece ao servidor central.")
    p.add_argument("--config", default="worker.yaml", help="caminho do YAML do worker")
    p.add_argument("--enroll", default=None, metavar="TOKEN",
                   help="token de inscrição de uso único (só na primeira vez; gere no painel, em Infraestrutura)")
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--version", action="version", version=f"agente do parque {AGENT_VERSION}")
    args = p.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    instalar_redacao_de_log()
    caminho = Path(args.config)
    if not caminho.exists():
        print(f"configuração não encontrada: {caminho}", file=sys.stderr)
        return 2
    settings = load_settings(caminho)
    if not settings.devices:
        print("nenhum aparelho declarado em `devices`: o agente não teria o que operar", file=sys.stderr)
        return 2
    agente = Agent(settings, enrollment=args.enroll)
    try:
        asyncio.run(agente.run_forever())
    except KeyboardInterrupt:
        return 0
    return 1        # `run_forever` só retorna quando a recusa é definitiva (credencial errada, não inscrito)


if __name__ == "__main__":
    raise SystemExit(main())
