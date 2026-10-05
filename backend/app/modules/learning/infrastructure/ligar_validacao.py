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
from app.models import Plan, RunCreate
from app.modules.learning.application.curador import CuradorPorIA
from app.modules.learning.application.servico import LearningService
from app.modules.learning.application.validacao import AjustesDaValidacao, ServicoDeValidacao
from app.modules.learning.domain.livro import EntradaDoLivro
from app.modules.learning.domain.validacao import Ambiente, AparelhoCandidato
from app.modules.learning.domain.vocabulario import Modo
from app.modules.learning.infrastructure import linhas
from app.modules.learning.infrastructure.dossies import DossiesSql
from app.modules.learning.infrastructure.eventos import RiscoDoRegistro
from app.modules.learning.infrastructure.revisoes_sql import RegistroDeRevisoesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.infrastructure.validacoes_sql import FontesDaValidacaoSql, RegistroDeValidacoesSql
from app.shared.vinculos import aparelhos_com_vinculo_ativo
from app.taskqueue.balanceamento import Candidato
from app.util import to_iso

log = logging.getLogger(__name__)

#: As execuções que ainda não assentaram: com qualquer uma delas, o despachante espera (restart, suíte e deploy
#: derrubam ou seguram execuções; o parque é do uso antes de ser da validação). O `planned` só conta no modo
#: `execute` (30.49): na execução só de plano (`mode='plan'`) ele é o estado FINAL, e três delas paradas em `planned`
#: seguraram o P4 por mais de 30 min em 04/10 (`ambiente_ocupado` em todas as voltas).
EM_CURSO = ("planning", "planned", "running", "paused", "cancelling")

#: 30.50: a coluna de `instances` de onde o executor tira cada variável de aparelho (`domain.validacao.
#: VARIAVEIS_DE_APARELHO`). Variável nova lá precisa da coluna aqui; o teste confere que as duas listas casam.
COLUNA_DA_VARIAVEL = {"account_label": "account_label"}


class Fila(Protocol):
    def create(self, req: RunCreate, *, origem: tuple[str, str] | None = None, prioridade: int = 0,
               prova: str | None = None) -> object: ...


class Parque(Protocol):
    def candidatos_de(self, instance_ids: Sequence[str], *, com_trabalho: set[str] | None = None) -> list[Candidato]: ...


def ajustes_da_validacao(cfg: ValidacaoCfg) -> AjustesDaValidacao:
    return AjustesDaValidacao(modo=Modo(cfg.modo), beta=cfg.beta, maximo_por_hora=cfg.maximo_por_hora,
                              intervalo_s=cfg.intervalo_s, janela_dias=cfg.janela_dias, extra_usd=cfg.extra_usd,
                              extra_ate=cfg.extra_ate, custo_estimado_usd=cfg.custo_estimado_usd,
                              teto_por_pedido_usd=cfg.teto_por_pedido_usd)


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
        r = self._db.one(f"SELECT COUNT(*) AS n FROM runs WHERE status IN ({marcas})"
                         " AND NOT (status='planned' AND mode='plan')", EM_CURSO)
        return Ambiente(saudavel=self._saudavel(), execucoes_em_curso=linhas.inteiro(r, "n") if r is not None else 0)

    def aparelhos(self, pacotes: Sequence[str], exige: frozenset[str] = frozenset()) -> Sequence[AparelhoCandidato]:
        """Os aparelhos com TODOS os `pacotes` prontos (30.33-C: o fluxo multi-app precisa de cada app dele) e com valor
        para cada variável de aparelho de `exige` (30.50: `{account_label}` vazio reprova a etapa que o confere)."""
        exigidos = sorted({p for p in pacotes if p})
        if not exigidos:
            return []
        com_o_app = [linhas.texto(r, "instance_id") for r in self._db.query(
            f"SELECT instance_id FROM device_app_state WHERE package_name IN ({linhas.marcas(len(exigidos))})"
            " AND state='ready' GROUP BY instance_id HAVING COUNT(DISTINCT package_name) = ? ORDER BY instance_id",
            (*exigidos, len(exigidos)))]
        for variavel in sorted(exige):
            coluna = COLUNA_DA_VARIAVEL[variavel]           # KeyError: variável de aparelho nova sem coluna aqui
            com_valor = {linhas.texto(r, "id") for r in self._db.query(
                f"SELECT id FROM instances WHERE {coluna} IS NOT NULL AND TRIM({coluna}) <> '' ORDER BY id")}
            com_o_app = [i for i in com_o_app if i in com_valor]
        sem_sessao = self._sem_sessao_no_app(exigidos)
        com_o_app = [i for i in com_o_app if i not in sem_sessao]
        if not com_o_app:
            return []
        com_conta = aparelhos_com_vinculo_ativo(self._db)     # conta vinculada é conta real (ADR-055)
        return [AparelhoCandidato(id=c.instance_id, online=c.ligado, ocioso=not c.ocupado, tem_o_app=True,
                                  conta_real=c.instance_id in com_conta)
                for c in self._parque.candidatos_de(com_o_app)]

    def _sem_sessao_no_app(self, pacotes: Sequence[str]) -> set[str]:
        """30.75 (b): os aparelhos em que uma prova de fluxo de um destes `pacotes` parou porque o app pediu login
        (`objectives.blocked_kind='auth'`) e que ainda não tiveram uma verificação nova DEPOIS da parada: a verificação
        do app no aparelho (`device_app_state.verified_at`) ou um objetivo concluído com sucesso num fluxo do mesmo app
        no mesmo aparelho (a execução comum que entrou no app prova que a sessão voltou). Sem isto, a prova seguinte
        escolhia o mesmo aparelho e parava na mesma tela de senha (lv-61f13d634e7bf08e, 05/10)."""
        if not pacotes:
            return set()
        marcas = linhas.marcas(len(pacotes))
        return {linhas.texto(r, "i") for r in self._db.query(
            "SELECT o.instance_id AS i FROM objectives o JOIN runs r ON r.id = o.run_id"
            " JOIN flows f ON f.id = r.prova_fluxo_id JOIN apps a ON a.id = f.app_id"
            f" WHERE o.blocked_kind = 'auth' AND o.instance_id IS NOT NULL AND a.package IN ({marcas})"
            " AND NOT EXISTS (SELECT 1 FROM device_app_state d WHERE d.instance_id = o.instance_id"
            "   AND d.package_name = a.package AND d.verified_at > COALESCE(o.finished_at, r.finished_at, ''))"
            " AND NOT EXISTS (SELECT 1 FROM objectives o2 JOIN runs r2 ON r2.id = o2.run_id"
            "   JOIN flows f2 ON f2.id = COALESCE(r2.prova_fluxo_id, r2.flow_id) JOIN apps a2 ON a2.id = f2.app_id"
            "   WHERE o2.instance_id = o.instance_id AND a2.package = a.package AND o2.status = 'succeeded'"
            "   AND o2.finished_at > COALESCE(o.finished_at, r.finished_at, ''))"
            " ORDER BY o.instance_id", tuple(pacotes))}

    def gasto_da_operacao(self, agora: datetime, dias: int) -> float:
        return RegistroDeRevisoesSql(self._db).janela(agora, dias).gasto_da_operacao

    def enfileirar(self, comando: str, aparelho: str, chave: str, prova: str | None = None) -> str:
        """`prova` (30.37): o id do fluxo que a execução prova; só então passa o argumento à fila."""
        req = RunCreate(command=comando, instance_ids=[aparelho], idempotency_key=chave)
        resumo = self._fila.create(req, prova=prova) if prova else self._fila.create(req)
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
          precos: Callable[[], dict[str, list[float]]], relogio: Callable[[], datetime],
          plano_ativo_para: Callable[[str], Plan | None] | None = None) -> ServicoDeValidacao:
    fontes = FontesDaValidacaoSql(db, precos=precos, fluxo_ativo_para=fluxo_ativo_para,
                                  vetado=_Veto(servico), plano_ativo_para=plano_ativo_para)
    triagem = TriagemDeCredencial()
    # 30.47: a classe de risco de agora para o pedido da pessoa, pelo mesmo dossiê do curador (só leitura).
    dossies = DossiesSql(db, servico, SqlLearningRepository(db, clock=lambda: to_iso(relogio()), precos=precos),
                         RiscoDoRegistro())
    validacao = ServicoDeValidacao(RegistroDeValidacoesSql(db), fontes,
                                   DespachoDoParque(db, fila, parque, saudavel=saudavel),
                                   triagem=triagem.recusa, ajustes=lambda: ajustes_da_validacao(config()),
                                   relogio=relogio,
                                   risco_do_item=lambda e: d.risco if (d := dossies.dossie(e)) is not None else None)
    servico.anexar(validacao)
    # Depois da sombra dos fluxos (`fluxos_d1`, ligada antes): o digest roda os mineradores em ordem, e o pedido fecha
    # pela evidência que a sombra acabou de gravar. Trocar a ordem fecharia toda validação de fluxo `sem_evidencia`.
    servico.registrar_minerador(validacao)
    servico.registrar_passo(validacao)               # 30.36: o motivo do pedido segue a evidência que chega depois
    servico.registrar_laco(LacoDaValidacao(validacao))
    curador = servico.extensao(CuradorPorIA)
    if curador is not None:
        curador.validacao = validacao
    return validacao


__all__ = ["EM_CURSO", "DespachoDoParque", "LacoDaValidacao", "ajustes_da_validacao", "ligar"]

