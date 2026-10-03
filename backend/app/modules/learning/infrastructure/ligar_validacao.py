"""A composição da validação automática (item 30.31): o registro e as fontes SQL, o despacho pelo parque e pela fila
de execuções, o minerador do digest, o laço do despachante e o gancho no curador. Ligada pelo `AppState` DEPOIS do
`RunService` (o aprendizado é montado antes dele), como o rótulo de intenção (`ligar_intencao`)."""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol

from app.config import ValidacaoCfg
from app.db import Database
from app.models import RunCreate
from app.modules.learning.application.curador import CuradorPorIA
from app.modules.learning.application.servico import LearningService
from app.modules.learning.application.validacao import AjustesDaValidacao, ServicoDeValidacao
from app.modules.learning.domain.livro import EntradaDoLivro
from app.modules.learning.domain.validacao import Ambiente, AparelhoCandidato
from app.modules.learning.domain.vocabulario import Modo
from app.modules.learning.infrastructure import linhas
from app.modules.learning.infrastructure.revisoes_sql import RegistroDeRevisoesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.validacoes_sql import FontesDaValidacaoSql, RegistroDeValidacoesSql
from app.taskqueue.balanceamento import Candidato

log = logging.getLogger(__name__)

#: As execuções que ainda não assentaram: com qualquer uma delas, o despachante espera (restart, suíte e deploy
#: derrubam ou seguram execuções; o parque é do uso antes de ser da validação).
EM_CURSO = ("planning", "planned", "running", "paused", "cancelling")


class Fila(Protocol):
    def create(self, req: RunCreate, *, origem: tuple[str, str] | None = None, prioridade: int = 0) -> object: ...


class Parque(Protocol):
    def candidatos_de(self, instance_ids: Sequence[str], *, com_trabalho: set[str] | None = None) -> list[Candidato]: ...


def ajustes_da_validacao(cfg: ValidacaoCfg) -> AjustesDaValidacao:
    return AjustesDaValidacao(modo=Modo(cfg.modo), beta=cfg.beta, maximo_por_hora=cfg.maximo_por_hora,
                              intervalo_s=cfg.intervalo_s, janela_dias=cfg.janela_dias, extra_usd=cfg.extra_usd,
                              extra_ate=cfg.extra_ate, custo_estimado_usd=cfg.custo_estimado_usd)


class DespachoDoParque:
    """O lado do parque: o ambiente (health e execuções em curso), os aparelhos que servem, o gasto da operação (o
    mesmo `G_W` do curador) e a fila de execuções (`RunService.create`, a mesma porta do `POST /api/runs`)."""

    def __init__(self, db: Database, fila: Fila, parque: Parque, *, saudavel: Callable[[], bool]) -> None:
        self._db = db
        self._fila = fila
        self._parque = parque
        self._saudavel = saudavel

    def ambiente(self) -> Ambiente:
        marcas = ",".join("?" for _ in EM_CURSO)
        r = self._db.one(f"SELECT COUNT(*) AS n FROM runs WHERE status IN ({marcas})", EM_CURSO)
        return Ambiente(saudavel=self._saudavel(), execucoes_em_curso=linhas.inteiro(r, "n") if r is not None else 0)

    def aparelhos(self, pacotes: Sequence[str]) -> Sequence[AparelhoCandidato]:
        """Os aparelhos com TODOS os `pacotes` prontos (30.33-C: o fluxo multi-app precisa de cada app dele)."""
        exigidos = sorted({p for p in pacotes if p})
        if not exigidos:
            return []
        com_o_app = [linhas.texto(r, "instance_id") for r in self._db.query(
            f"SELECT instance_id FROM device_app_state WHERE package_name IN ({linhas.marcas(len(exigidos))})"
            " AND state='ready' GROUP BY instance_id HAVING COUNT(DISTINCT package_name) = ? ORDER BY instance_id",
            (*exigidos, len(exigidos)))]
        if not com_o_app:
            return []
        com_conta = {linhas.texto(r, "instance_id") for r in self._db.query(
            "SELECT DISTINCT instance_id FROM device_profile_bindings WHERE active=1")}
        return [AparelhoCandidato(id=c.instance_id, online=c.ligado, ocioso=not c.ocupado, tem_o_app=True,
                                  conta_real=c.instance_id in com_conta)
                for c in self._parque.candidatos_de(com_o_app)]

    def gasto_da_operacao(self, agora: datetime, dias: int) -> float:
        return RegistroDeRevisoesSql(self._db).janela(agora, dias).gasto_da_operacao

    def enfileirar(self, comando: str, aparelho: str, chave: str) -> str:
        resumo = self._fila.create(RunCreate(command=comando, instance_ids=[aparelho], idempotency_key=chave))
        return str(getattr(resumo, "id"))


class LacoDaValidacao:
    """O despachante, sob a trava de líder da curadoria. Lê `intervalo_s` e `modo` a cada volta.

    A volta roda NA THREAD DO LOOP, não em `asyncio.to_thread`: `RunService.create` agenda o planejamento com
    `asyncio.create_task`, que fora do loop levanta "no running event loop". É o que fazem o laço de pedidos (28.4) e
    `POST /api/runs`; a volta é curta (banco, `health()` e a fila) e só passa do `pendentes()` com pedido na fila."""

    nome = "validacao"

    def __init__(self, servico: ServicoDeValidacao) -> None:
        self.servico = servico
        self._parado = threading.Event()

    def parar(self, timeout_s: float) -> bool:
        self._parado.set()
        return True                                     # a volta é curta (banco e fila), sem provedor no meio

    async def laco(self, lider: Callable[[], int | None]) -> None:
        while not self._parado.is_set():
            await asyncio.sleep(self.servico.intervalo_s)
            if self._parado.is_set():
                return
            try:
                self.servico.uma_volta(lider)
            except Exception:  # noqa: BLE001 - a validação nunca derruba o processo
                log.exception("aprendizado: despachante da validação")


class _Veto:
    """O veto do livro (o mesmo da rota: `contexto_de_publicacao`), para o pedido não nascer vivo em conteúdo vetado."""

    def __init__(self, servico: LearningService) -> None:
        self._servico = servico

    def __call__(self, e: object) -> bool:
        return isinstance(e, EntradaDoLivro) and self._servico.contexto_de_publicacao(e)[1] is not None


def ligar(servico: LearningService, db: Database, *, fila: Fila, parque: Parque, fluxo_ativo_para: Callable[[str], bool],
          saudavel: Callable[[], bool], config: Callable[[], ValidacaoCfg],
          precos: Callable[[], dict[str, list[float]]], relogio: Callable[[], datetime]) -> ServicoDeValidacao:
    fontes = FontesDaValidacaoSql(db, precos=precos, fluxo_ativo_para=fluxo_ativo_para,
                                  vetado=_Veto(servico))
    triagem = TriagemDeCredencial()
    validacao = ServicoDeValidacao(RegistroDeValidacoesSql(db), fontes,
                                   DespachoDoParque(db, fila, parque, saudavel=saudavel),
                                   triagem=triagem.recusa, ajustes=lambda: ajustes_da_validacao(config()),
                                   relogio=relogio)
    servico.anexar(validacao)
    servico.registrar_minerador(validacao)
    servico.registrar_laco(LacoDaValidacao(validacao))
    curador = servico.extensao(CuradorPorIA)
    if curador is not None:
        curador.validacao = validacao
    return validacao


__all__ = ["EM_CURSO", "DespachoDoParque", "LacoDaValidacao", "ajustes_da_validacao", "ligar"]

