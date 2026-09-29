"""Composição do aprendizado: o que o `AppState` chama para ter um `LearningService` pronto.

Aqui (e só aqui) o bloco `aprendizado:` do config vira `Ajustes`, a guarda do fluxo vira uma função sobre o
repositório das habilidades, e os pacotes seguintes ligam as suas partes (`ligar_*`, hoje sem efeito). Cada
leitura do config é feita A CADA uso: o arquivo muda com o processo no ar.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.config import LearningCfg
from app.db import Database
from app.modules.learning.application.ports import Ajustes, Retencao
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.vocabulario import Modo, ModoDeTelas
from app.modules.learning.infrastructure import ligar_costuras, ligar_licoes, ligar_nativos, ligar_telas, ligar_voz
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository
from app.util import now


def ajustes_do_config(cfg: LearningCfg) -> Ajustes:
    r = cfg.retencao
    return Ajustes(enabled=cfg.enabled, curadoria_s=cfg.curadoria_s, modo_licoes=Modo(cfg.licoes.modo),
                   modo_telas=ModoDeTelas(cfg.telas.modo), modo_voz=Modo(cfg.voz.modo),
                   modo_preferencias=Modo(cfg.preferencias.modo),
                   retencao=Retencao(sinais_dias=r.sinais_dias, feedback_dias=r.feedback_dias,
                                     exposicoes_dias=r.exposicoes_dias, evidencias_por_item=r.evidencias_por_item,
                                     diario_dias=r.diario_dias,
                                     candidata_sem_evidencia_dias=r.candidata_sem_evidencia_dias))


class GuardaDoFluxo:
    """As guardas que `PUT /api/flows/{id}` já aplicava, agora também no livro: religar um fluxo adotado por
    habilidade publicada, ou cujo comando outra habilidade publicou, deixaria o mesmo comando vivo nos dois lugares."""

    def __init__(self, db: Database, habilidades: SqlSkillRepository) -> None:
        self._db = db
        self._habilidades = habilidades

    def __call__(self, flow_id: str) -> str | None:
        adotante = self._habilidades.published_adopter(flow_id)
        if adotante is not None:
            return (f"O fluxo foi adotado pela habilidade {adotante.ref}, que está publicada: religá-lo deixaria o "
                    "mesmo comando vivo nos dois lugares. Desfaça a adoção para voltar ao fluxo.")
        linha = self._db.one("SELECT match_key FROM flows WHERE id=?", (flow_id,))
        chave = linha["match_key"] if linha else None
        if isinstance(chave, str) and (outra := self._habilidades.published_with_command(chave)) is not None:
            return (f"A habilidade {outra.ref} está publicada com o mesmo comando: religar o fluxo deixaria o comando "
                    "vivo nos dois lugares. Desabilite a habilidade antes.")
        return None


def montar_aprendizado(db: Database, *, config: Callable[[], LearningCfg], retencao_de_logs_dias: Callable[[], int],
                       precos: Callable[[], dict[str, list[float]]], habilidades: SqlSkillRepository | None = None,
                       relogio: Callable[[], datetime] = now) -> LearningService:
    repo = SqlLearningRepository(db, guarda_do_fluxo=GuardaDoFluxo(db, habilidades) if habilidades else None,
                                 precos=precos)
    servico = LearningService(repo, FontesSql(db), TriagemDeCredencial(), ajustes=lambda: ajustes_do_config(config()),
                              relogio=relogio, retencao_de_logs_dias=retencao_de_logs_dias)
    for ligar in (ligar_costuras.ligar, ligar_nativos.ligar, ligar_telas.ligar, ligar_voz.ligar):
        ligar(servico)
    # Pacote A7: as lições medidas (fornecedor da costura, mineradores do digest e passo da curadoria).
    ligar_licoes.ligar(servico, repo, db, config=lambda: config().licoes, precos=precos, relogio=relogio)
    return servico
