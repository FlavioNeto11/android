"""Pacote A7 do ADR-054: liga as lições ao livro — o fornecedor da costura `licoes_para` (A2), os mineradores do digest
(desfecho das exposições, contraste do ator, defeito do plano) e o passo da curadoria.

A costura só chama o fornecedor fora do modo `off`; no `shadow` (de fábrica) o fornecedor devolve nada e não grava
exposição — o prompt sai como sempre saiu. O registro é por serviço (um `AppState`, um livro), como o das extensões
das costuras: nada global que um segundo `AppState` herdasse do primeiro.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from weakref import WeakKeyDictionary

from app.config import LicoesCfg
from app.db import Database
from app.metricas import metricas
from app.modules.learning.application.licoes import AjustesDeLicoes, ServicoDeLicoes
from app.modules.learning.application.ports import RepositorioDeAprendizado
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.licoes import LICAO_MAX_CARACTERES, Pedido
from app.modules.learning.domain.tokens import Teto
from app.modules.learning.domain.vocabulario import Papel
from app.modules.learning.infrastructure.licoes_sql import SqlLicoesRepository
from app.modules.learning.infrastructure.ligar_costuras import extensoes
from app.taskqueue.costuras import PedidoDeLicoes
from app.util import now

_LICOES: WeakKeyDictionary[LearningService, ServicoDeLicoes] = WeakKeyDictionary()


def ajustes_de_licoes(cfg: LicoesCfg) -> AjustesDeLicoes:
    return AjustesDeLicoes(
        ator=Teto(tokens=cfg.ator.tokens, itens=cfg.ator.max, caracteres_por_item=LICAO_MAX_CARACTERES),
        planejador=Teto(tokens=cfg.planejador.tokens, itens=cfg.planejador.max),
        minimo_por_braco=cfg.minimo_por_braco, maximo_por_braco=max(cfg.minimo_por_braco, cfg.maximo_por_braco),
        holdout_publicada=cfg.holdout_publicada)


class FornecedorDoLivro:
    """Cumpre `FornecedorDeLicoes` (`ligar_costuras.py`): traduz o pedido da costura para o da aplicação."""

    def __init__(self, licoes: ServicoDeLicoes) -> None:
        self._licoes = licoes

    def licoes_para(self, pedido: PedidoDeLicoes) -> list[str]:
        return self._licoes.licoes_para(Pedido(
            papel=Papel(pedido.papel), unidade=pedido.unidade, run_id=pedido.run_id, app=pedido.app_package,
            capability=pedido.capability, step_hash=pedido.step_hash, simulated=pedido.simulated,
            objective_id=pedido.objective_id, step_id=pedido.step_id))


def ligar(servico: LearningService, livro: RepositorioDeAprendizado, db: Database, *,
          config: Callable[[], LicoesCfg], precos: Callable[[], dict[str, list[float]]] | None = None,
          relogio: Callable[[], datetime] = now) -> ServicoDeLicoes:
    """Monta o serviço das lições deste livro, registra mineradores e passo, e pendura o fornecedor na costura."""
    atual = _LICOES.get(servico)
    if atual is not None:
        return atual                                    # a montagem pode rodar de novo no mesmo serviço
    licoes = ServicoDeLicoes(servico, livro, SqlLicoesRepository(db, precos=precos),
                             ajustes=lambda: ajustes_de_licoes(config()), relogio=relogio, contar=metricas.contar)
    for minerador in licoes.mineradores():
        servico.registrar_minerador(minerador)
    servico.registrar_passo(licoes.passo())
    extensoes(servico).definir_licoes(FornecedorDoLivro(licoes))
    _LICOES[servico] = licoes
    return licoes


def licoes_de(servico: LearningService) -> ServicoDeLicoes | None:
    """O serviço das lições deste livro (a apresentação o acha por aqui), ou `None` antes de `ligar`."""
    return _LICOES.get(servico)


__all__ = ["FornecedorDoLivro", "ajustes_de_licoes", "ligar", "licoes_de"]
