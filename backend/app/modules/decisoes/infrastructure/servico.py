"""O laço do 28.25: recolhe as decisões (qualquer réplica) e manda o resumo (só o líder da trava `avisos`).

O recolher é idempotente pela `origem_ref`, então não precisa de líder. O resumo escreve na fila de avisos e marca as
decisões como resumidas: um líder só evita duas mensagens da mesma janela.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from app.config import Config
from app.modules.avisos.domain.mensagem import Aviso
from app.modules.decisoes.infrastructure.adaptador_sql import AdaptadorDeDecisoes
from app.modules.decisoes.infrastructure.resumo_sql import ResumoDasDecisoes
from app.taskqueue.travas import AVISOS

log = logging.getLogger("poc.decisoes")


class ServicoDeDecisoes:
    def __init__(self, cfg: Config, adaptador: AdaptadorDeDecisoes, resumo: ResumoDasDecisoes, *,
                 lider: Callable[[str], int | None]):
        self.cfg = cfg
        self.adaptador = adaptador
        self.resumo = resumo
        self._lider = lider

    def uma_volta(self) -> tuple[int, Aviso | None]:
        """Recolhe e, se sou o líder, resume. Devolve (decisões novas, aviso enfileirado)."""
        novas = self.adaptador.varrer()
        aviso: Aviso | None = None
        if self._lider(AVISOS) is not None:
            cfg = self.cfg.file.avisos
            try:
                aviso = self.resumo.resumir(janela_min=cfg.decisoes_automaticas.janela_min,
                                            desfazer_dias=cfg.decisoes_automaticas.desfazer_dias,
                                            validade_h=cfg.validade_h, url_painel=cfg.url_painel)
            except Exception:  # noqa: BLE001 - o resumo nunca derruba o laço; a próxima volta tenta de novo
                log.exception("decisoes: falha no resumo")
        return novas, aviso

    async def laco(self) -> None:
        while True:
            try:
                await asyncio.sleep(float(self.cfg.file.avisos.decisoes_automaticas.intervalo_s))
                self.uma_volta()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - o laço nunca morre
                log.exception("decisoes: laço")
                await asyncio.sleep(5)
