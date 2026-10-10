"""O código de confirmação por e-mail, lido da caixa da conta (ADR-090).

Liga a porta `CodigoDeEmail` (consumida pelo motor de sessão) à caixa que a ponte do igfarm registrou para a conta
(`caixas_email`) e ao leitor IMAP do parque (`email_do_parque`). Só devolve código RECEBIDO DEPOIS de `desde`: o da
tentativa anterior (que ainda está na caixa) nunca serve para o login de agora.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.db import Database
from app.modules.email_do_parque.application.servico import EmailDoParque, ErroEmailDoParque
from app.modules.identity.infrastructure.ponte_igfarm import ArmazemSql

log = logging.getLogger("farm.identidade.codigo_de_email")

#: Folga entre o relógio daqui e o horário da mensagem no servidor de e-mail.
FOLGA_DE_RELOGIO = timedelta(seconds=5)
#: Intervalo entre leituras da caixa enquanto o e-mail não chega.
PAUSA_S = 3.0
#: A mensagem mais velha que isto não entra na busca (o filtro fino é `desde`).
JANELA_MIN = 15


class CodigoDoEmailDoParque:
    def __init__(self, db: Database, email: Callable[[], EmailDoParque | None], *, pausa_s: float = PAUSA_S) -> None:
        self.armazem = ArmazemSql(db)
        self.email = email
        self.pausa_s = pausa_s

    async def codigo_depois_de(self, account_id: str, desde: datetime, *, espera_s: float) -> str | None:
        leitor = self.email()
        endereco = self.armazem.endereco_da_conta(account_id)
        if leitor is None or endereco is None:
            return None
        if desde.tzinfo is None:
            desde = desde.replace(tzinfo=timezone.utc)
        limite = time.monotonic() + max(0.0, espera_s)
        while True:
            try:
                achado = await leitor.codigo_recente(endereco, janela_min=JANELA_MIN)
            except ErroEmailDoParque as exc:
                log.warning("código por e-mail indisponível para a conta %s: %s", account_id, exc.code)
                return None
            except Exception:  # noqa: BLE001 - falha do IMAP não pode derrubar o login: a pessoa assume
                log.warning("leitura do código por e-mail falhou para a conta %s", account_id, exc_info=False)
                return None
            if achado is not None and achado.recebido_em >= desde - FOLGA_DE_RELOGIO:
                return achado.codigo
            if time.monotonic() >= limite:
                return None
            await asyncio.sleep(self.pausa_s)
