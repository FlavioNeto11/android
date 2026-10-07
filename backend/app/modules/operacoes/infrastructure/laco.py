"""31.220: o laço do sistema que avança a operação sem depender de leitura externa.

Antes dele, só a leitura (o GET do painel, o laço da Canais, a resposta de um POST) derivava o estágio de cada alvo e
fechava a operação: sem ninguém lendo, ela não fechava e o `operacao.encerrada` não saía. O laço lê as operações abertas
pelo mesmo `ServicoDeOperacoes.ler`, que grava só o que mudou desde a leitura anterior (31.216). Por isso dispensa a
trava de líder (`taskqueue/travas.py`: só os laços que não são idempotentes a pedem) e convive com o GET.

O intervalo é `LimitsCfg.operacao_laco_s`, relido a cada volta (`PUT /api/settings`, sem reinício). 0 = desligado, o
padrão do corte 61: a prova de 07/10 usa o laço da Canais, e dois laços ao mesmo tempo ficam para depois da prova.
Para quando não há alvo em curso: a volta só lê as operações sem `finished_at`, e a leitura fecha a que não tem mais
alvo pendente ou em curso; com nenhuma aberta, a volta é uma consulta e nada mais.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.db import Database
from app.modules.operacoes.infrastructure.servico import ServicoDeOperacoes

log = logging.getLogger("poc.operacoes.laco")

#: Com o laço desligado, de quanto em quanto tempo ele confere se foi ligado.
OCIOSO_S = 30


def abertas(db: Database) -> list[str]:
    """As operações ainda sem `finished_at`, a mais antiga primeiro."""
    return [str(r["id"]) for r in db.query("SELECT id FROM operacoes WHERE finished_at IS NULL"
                                           " ORDER BY created_at, id")]


class LacoDasOperacoes:
    def __init__(self, db: Database, servico: Callable[[], ServicoDeOperacoes], intervalo_s: Callable[[], int], *,
                 dormir: Callable[[float], Awaitable[object]] = asyncio.sleep) -> None:
        # `dormir` é o relógio do laço: o teste o troca por um falso que conta as voltas.
        self.db, self.servico, self.intervalo_s, self.dormir = db, servico, intervalo_s, dormir
        self.voltas = 0

    def uma_volta(self) -> int:
        """Lê cada operação aberta uma vez; devolve quantas leu. A falha numa não impede as outras."""
        ids = abertas(self.db)
        if not ids:
            return 0
        servico = self.servico()
        for op_id in ids:
            try:
                servico.ler(op_id)
            except Exception:  # noqa: BLE001 - uma operação com dado ruim não para o laço das outras
                log.exception("operação %s: leitura do laço", op_id)
        return len(ids)

    async def laco(self) -> None:
        while True:
            intervalo = self._intervalo()
            if intervalo > 0:
                try:
                    self.uma_volta()
                except Exception:  # noqa: BLE001 - banco fora do ar: a próxima volta tenta de novo
                    log.exception("laço das operações")
                self.voltas += 1
            await self.dormir(intervalo if intervalo > 0 else OCIOSO_S)

    def _intervalo(self) -> int:
        try:
            return max(0, int(self.intervalo_s()))
        except Exception:  # noqa: BLE001 - sem a configuração, o laço fica como no padrão: desligado
            log.exception("laço das operações: intervalo")
            return 0
