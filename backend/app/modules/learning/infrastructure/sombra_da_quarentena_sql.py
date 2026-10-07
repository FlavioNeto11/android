"""31.202: o passo da curadoria que lê o uso real de cada receita ENSINADA ativa e grava, em sombra, o parecer do
`domain/sombra_da_quarentena.py` ("liberaria", "prenderia de volta" ou "nenhuma"). Sem IA. NADA se aplica: a loja de
receitas, a quarentena e o 30.81 seguem iguais; o parecer só fica para a medida e para o dono comparar.

Onde fica: um sinal por receita em `learning_signals` (`kind='sombra_da_quarentena'`, `source_ref='receita:<id>'`,
`created_by='sistema'`), sobrescrito a cada passo (`substituir`): `reason` é a sugestão, `note` o motivo e `data` as
contagens e a versão da regra. Como as outras sombras (30.34, 30.55), é marca do sistema, não gesto: fica fora da aba
Sinais e se lê por `GET /api/aprendizado/sinais?kind=sombra_da_quarentena`.

O uso real é a régua do rendimento (`domain/rendimento.tipo_de_uso`: nem simulada, nem prova, nem lote); a persona de
cada tentativa é a do objetivo da etapa (`objectives.profile_id`); a liberação é a régua da loja (`liberada`).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from app.db import Database, Row, loads
from app.modules.learning.application.ports import NovoSinal
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import SYSTEM_ACTOR
from app.modules.learning.domain.livro import receita_tem_efeito
from app.modules.learning.domain.rendimento import tipo_de_uso
from app.modules.learning.domain.sombra_da_quarentena import (VERSAO_DA_REGRA, ReceitaEnsinada, Sugestao,
                                                              TentativaReal, mira_a_propria_conta, parecer)
from app.modules.learning.domain.vocabulario import Polaridade, SignalKind

log = logging.getLogger(__name__)

PREFIXO_DO_TREINO = "training:"
_POLARIDADE = {Sugestao.LIBERARIA: Polaridade.POSITIVE, Sugestao.PRENDERIA_DE_VOLTA: Polaridade.NEGATIVE,
               Sugestao.NENHUMA: Polaridade.NEUTRAL}


class SombraDaQuarentena:
    """`PassoDeCuradoria` (`application/ports.py`)."""

    nome = "sombra_da_quarentena"

    def __init__(self, servico: LearningService, db: Database, *, liberada: Callable[[Row], bool]) -> None:
        self._servico = servico
        self.db = db
        self._liberada = liberada

    def _tentativas(self, recipe_id: int) -> tuple[TentativaReal, ...]:
        saida = []
        for t in self.db.query(
                "SELECT a.status, a.strategy, s.run_id, o.profile_id, r.simulated, r.prova_fluxo_id, r.idempotency_key"
                " FROM attempts a JOIN steps s ON s.id = a.step_id JOIN runs r ON r.id = s.run_id"
                " LEFT JOIN objectives o ON o.id = s.objective_id"
                " WHERE a.recipe_id=? ORDER BY a.started_at, a.id", (recipe_id,)):
            if tipo_de_uso(simulated=t["simulated"], prova_fluxo_id=t["prova_fluxo_id"],
                           idempotency_key=t["idempotency_key"]) != "real":
                continue
            saida.append(TentativaReal(run_id=str(t["run_id"]), persona=str(t["profile_id"] or ""),
                                       sem_ia=t["strategy"] == "recipe" and t["status"] == "succeeded"))
        return tuple(saida)

    def receitas(self) -> list[ReceitaEnsinada]:
        saida = []
        for r in self.db.query("SELECT * FROM recipes WHERE status='active' AND learned_from_step LIKE ? ORDER BY id",
                               (PREFIXO_DO_TREINO + "%",)):
            sessao = str(r["learned_from_step"])[len(PREFIXO_DO_TREINO):]
            quem = self.db.scalar("SELECT profile_id FROM training_sessions WHERE id=?", (sessao,))
            acoes = str(r["actions"] or "")
            saida.append(ReceitaEnsinada(
                id=int(r["id"]), app=str(r["app_package"] or ""), quem_ensinou=str(quem or ""),
                liberada=self._liberada(r), com_efeito=receita_tem_efeito(loads(acoes, [])),
                propria_conta=mira_a_propria_conta(acoes), tentativas=self._tentativas(int(r["id"]))))
        return saida

    def executar(self, agora: datetime) -> int:
        """Quantas receitas receberam uma sugestão diferente de `nenhuma` neste passo."""
        sugeridas = 0
        for r in self.receitas():
            p = parecer(r)
            self._servico.registrar_sinal(NovoSinal(
                kind=SignalKind.SOMBRA_DA_QUARENTENA, source_ref=f"receita:{r.id}", created_by=SYSTEM_ACTOR,
                polarity=_POLARIDADE[p.sugestao], reason=p.sugestao.value, note=p.motivo, app_package=r.app,
                data={**p.contagem, "regra": VERSAO_DA_REGRA, "liberada": r.liberada}), substituir=True)
            sugeridas += p.sugestao is not Sugestao.NENHUMA
        return sugeridas
