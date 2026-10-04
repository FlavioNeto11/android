"""O que o `AppState` guarda do portal (29.77, ADR-075): o serviço do contato, o token da página e o laço de reenvio.

A dependência da Canais (28.32) é resolvida na hora de usar, nunca na importação: `state.avisos.avisar_contato_do_portal`
e o tipo `ContatoDoPortal` podem não existir nesta base, e sem eles o contato fica guardado como `pendente`. Por isso
o `importlib` (o ponto cego declarado de `tests/test_arquitetura.py`), e não um import tardio.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import time
from collections.abc import Callable

from app.config import Config
from app.db import Database
from app.modules.portal.application.contato import Avisar, ServicoDeContato, TipoDoContato
from app.modules.portal.application.protecao import emitir_token
from app.modules.portal.infrastructure.contatos_sql import ContatosSql
from app.util import now

log = logging.getLogger("poc.portal")

#: Uma volta do reenvio por minuto; a retenção de 180 dias a cada hora (é faxina, não prazo).
REENVIO_S = 60
RETENCAO_S = 3600

MODULO_DA_CANAIS = "app.modules.avisos.domain.portal"


def _tipo_da_canais() -> TipoDoContato | None:
    try:
        modulo = importlib.import_module(MODULO_DA_CANAIS)
    except ImportError:
        return None
    tipo = getattr(modulo, "ContatoDoPortal", None)
    return tipo if callable(tipo) else None


class Portal:
    def __init__(self, cfg: Config, db: Database, avisos: object) -> None:
        self.cfg = cfg
        self.repo = ContatosSql(db)

        def avisar() -> Avisar | None:
            funcao = getattr(avisos, "avisar_contato_do_portal", None)
            return funcao if callable(funcao) else None

        self.contatos = ServicoDeContato(self.repo, limites=lambda: cfg.file.portal.limites, avisar=avisar,
                                         tipo_do_contato=_tipo_da_canais)

    @property
    def contato_ligado(self) -> bool:
        return bool(self.cfg.file.portal.contato_ligado)

    def token(self) -> str:
        """O token da página. Vazio com o contato desligado: a rota nem existe, e o banco não é tocado."""
        if not self.contato_ligado:
            return ""
        try:
            return emitir_token(self.contatos.sal(), time.time())
        except Exception:  # noqa: BLE001 - sem banco a página ainda abre; o envio é que vai falhar, com aviso
            log.exception("portal: não foi possível emitir o token da página")
            return ""

    async def laco(self, lider: Callable[[], int | None]) -> None:
        """Reenvio dos não entregues e retenção, só no líder da trava `avisos` (é ele quem manda as mensagens).

        Roda mesmo com o contato desligado: a promessa dos 180 dias da página vale para o que já foi guardado, e
        desligar o formulário não pode congelar a retenção. Desligado, só o reenvio é pulado."""
        ultima_retencao = float("-inf")
        while True:
            await asyncio.sleep(REENVIO_S)
            if lider() is None:
                continue
            try:
                if self.contato_ligado:
                    contagem = await asyncio.to_thread(self.contatos.reenviar, now())
                    if contagem:
                        log.info("portal: reenvio %s", contagem)
                if time.monotonic() - ultima_retencao >= RETENCAO_S:
                    apagados = await asyncio.to_thread(self.repo.apagar_vencidos, now())
                    ultima_retencao = time.monotonic()
                    if apagados:
                        log.info("portal: %s contato(s) apagado(s) pela retenção", apagados)
            except Exception:  # noqa: BLE001 - o laço nunca derruba o processo
                log.exception("portal: volta do reenvio")
