"""30.11: liga o curador por IA ao Livro. O curador fica pendurado no serviço (a apresentação o acha pelo tipo) e o
laço dele é registrado à parte da curadoria (`LearningService.lacos`): o `AppState` o sobe sob a trava de líder.

O adaptador padrão é o SIMULADO: o do hub de IA é do 30.12 (frente Jev) e entra por `curador_de_ia`. De fábrica o modo
é `off` e nada roda; o modo é lido a cada volta."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

from app.config import CuradorCfg
from app.db import Database
from app.modules.learning.application.curador import CuradorPorIA
from app.modules.learning.application.ports import (AjustesDoCurador, CatalogoDeRisco, CuradorDeIA,
                                                    RepositorioDeAprendizado, TriagemDeTexto)
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.vocabulario import Modo
from app.modules.learning.infrastructure.curador_simulado import CuradorSimulado
from app.modules.learning.infrastructure.dossies import DossiesSql
from app.modules.learning.infrastructure.revisoes_sql import RegistroDeRevisoesSql

log = logging.getLogger("poc.aprendizado")


class LacoDoCurador:
    """O laço periódico do curador (§8.6), à parte do `PassoDeCuradoria`. Lê `intervalo_s` e `modo` a cada volta: mudar
    o config com o processo no ar vale na próxima. A trava (`lider`) é conferida dentro da volta."""

    nome = "curador"

    def __init__(self, curador: CuradorPorIA) -> None:
        self.curador = curador

    async def laco(self, lider: Callable[[], int | None]) -> None:
        while True:
            await asyncio.sleep(self.curador.intervalo_s)
            try:
                await asyncio.to_thread(self.curador.uma_volta, lider)
            except Exception:  # noqa: BLE001 - o curador nunca derruba o processo
                log.exception("aprendizado: curador por IA")


def ajustes_do_curador(cfg: CuradorCfg) -> AjustesDoCurador:
    return AjustesDoCurador(modo=Modo(cfg.modo), intervalo_s=cfg.intervalo_s, cooldown_h=cfg.cooldown_h, alfa=cfg.alfa,
                            k=cfg.k, janela_dias=cfg.janela_dias, m_cmax=cfg.m_cmax)


def ligar(servico: LearningService, repo: RepositorioDeAprendizado, db: Database, triagem: TriagemDeTexto, *,
          config: Callable[[], CuradorCfg], precos: Callable[[], dict[str, list[float]]],
          relogio: Callable[[], datetime], catalogo: CatalogoDeRisco | None,
          curador_de_ia: CuradorDeIA | None = None) -> CuradorPorIA:
    curador = CuradorPorIA(servico, DossiesSql(db, servico, repo, catalogo), curador_de_ia or CuradorSimulado(),
                           RegistroDeRevisoesSql(db), triagem, ajustes=lambda: ajustes_do_curador(config()),
                           precos=precos, relogio=relogio)
    servico.anexar(curador)
    servico.registrar_laco(LacoDoCurador(curador))
    return curador


__all__ = ["LacoDoCurador", "ajustes_do_curador", "ligar"]
