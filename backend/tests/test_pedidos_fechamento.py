"""Item 28.4 — fechamento puro: a tabela do §5.3 e o prazo de início com corrida (pedidos-laco.md §5.3).

Prova `simulated`: funções puras, sem banco.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.pedidos.domain.estados import transicionar_ocorrencia
from app.modules.pedidos.domain.fechamento import (INTENCAO_PRAZO_DE_INICIO, Fechamento, ObjetivoVisto, fechar,
                                                   prazo_de_inicio_vencido)

UTC = timezone.utc
AGORA = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)


def _o(status: str, *, id: str = "o1", iniciado: bool = False) -> ObjetivoVisto:
    return ObjetivoVisto(id, status, iniciado)


def _f(run_status, objetivos=(), *, atual="despachada", **kw):
    return fechar(estado_atual=atual, run_id="r-1", run_status=run_status, objetivos=list(objetivos), **kw)


@pytest.mark.parametrize("status", ["planning", "planned", "needs_input"])
def test_em_preparo_a_ocorrencia_continua_despachada(status: str) -> None:
    assert _f(status) is None


@pytest.mark.parametrize("status", ["running", "paused", "cancelling"])
def test_em_curso_vira_rodando_uma_vez_so_e_leva_o_inicio(status: str) -> None:
    f = _f(status, started_at="2026-10-02T11:59:00.000Z")
    assert f == Fechamento("rodando", None, "2026-10-02T11:59:00.000Z") and not f.terminal
    assert _f(status, atual="rodando") is None, "já rodando: nada a escrever (idempotente)"


def test_concluida_e_o_unico_sucesso() -> None:
    f = _f("completed", [_o("succeeded")])
    assert f == Fechamento("concluida") and f.terminal
    # direto de `despachada` (a varredura nunca viu `rodando`) e de `rodando`: as duas arestas existem na tabela
    for de in ("despachada", "rodando"):
        transicionar_ocorrencia(de, f.estado, motivo=f.motivo)


def test_com_pendencias_nunca_vira_sucesso_incerto_ou_falhou() -> None:
    f = _f("completed_with_issues", [_o("succeeded", id="a"), _o("uncertain", id="b"), _o("failed", id="c")])
    assert f.estado == "incerta" and f.motivo == "objetivo incerto: b"
    f = _f("completed_with_issues", [_o("succeeded", id="a"), _o("waiting_user", id="b"), _o("failed", id="c")])
    assert f.estado == "falhou" and f.motivo == "parcial: 1 de 3"


def test_falha_leva_o_detalhe_da_execucao() -> None:
    assert _f("failed", status_detail="o planejador recusou").motivo == "o planejador recusou"
    assert _f("failed").motivo == "a execução falhou"      # o motivo é obrigatório na ocorrência


def test_execucao_purgada_fecha_falhou_com_o_id() -> None:
    f = _f(None)
    assert f.estado == "falhou" and f.motivo == "execução r-1 não existe mais"


def test_cancelada_vira_cancelada_com_quem_pediu() -> None:
    assert _f("cancelled", [_o("cancelled")]).motivo == "execução cancelada"
    assert _f("cancelled", [_o("cancelled")], pedido_cancelado=True).motivo == "pedido cancelado"
    assert _f("cancelled", [_o("cancelled")]).estado == "cancelada"


def test_cancelada_pelo_prazo_de_inicio_sem_objetivo_iniciado_vira_perdida() -> None:
    f = _f("cancelled", [_o("cancelled")], intencao=INTENCAO_PRAZO_DE_INICIO, prazo_inicio_s=3600,
           motivo_de_espera="sem vaga no aparelho")
    assert f.estado == "perdida" and f.motivo == "não começou em 3600s: sem vaga no aparelho"
    assert _f("cancelled", [_o("cancelled")], intencao=INTENCAO_PRAZO_DE_INICIO, prazo_inicio_s=60).motivo \
        == "não começou em 60s"


def test_corrida_com_o_scheduler_um_objetivo_iniciado_nunca_vira_perdida() -> None:
    """O laço cancelou por prazo, mas o `_tick` já tinha começado um objetivo: efeito possível, então `cancelada`."""
    f = _f("cancelled", [_o("cancelled", iniciado=True)], intencao=INTENCAO_PRAZO_DE_INICIO, prazo_inicio_s=60)
    assert f.estado == "cancelada"
    # e com objetivo incerto, `incerta`, mesmo com a intenção do prazo
    f = _f("cancelled", [_o("uncertain", iniciado=True)], intencao=INTENCAO_PRAZO_DE_INICIO, prazo_inicio_s=60)
    assert f.estado == "incerta"


def test_motivo_de_todo_fechamento_e_aceito_pela_tabela_de_transicoes() -> None:
    casos = [_f("completed_with_issues", [_o("failed")]), _f("failed"), _f(None), _f("cancelled"),
             _f("cancelled", [_o("cancelled")], intencao=INTENCAO_PRAZO_DE_INICIO, prazo_inicio_s=1),
             _f("completed_with_issues", [_o("uncertain")])]
    for f in casos:
        assert f is not None
        transicionar_ocorrencia("despachada", f.estado, motivo=f.motivo)


# ----------------------------------------------------------------------------------------------- prazo de início
def _vencido(**kw) -> bool:
    base = dict(estado_atual="despachada", run_status="planned", objetivos=[_o("pending")],
                despachada_em=AGORA - timedelta(seconds=3601), agora=AGORA, prazo_inicio_s=3600)
    return prazo_de_inicio_vencido(**{**base, **kw})


def test_prazo_de_inicio_vence_so_depois_do_prazo_e_sem_objetivo_andando() -> None:
    assert _vencido()
    assert not _vencido(despachada_em=AGORA - timedelta(seconds=3600)), "exatamente o prazo ainda não venceu"
    assert _vencido(objetivos=[]), "ainda planejando: nenhum objetivo, nenhum início"
    assert _vencido(run_status="planning", objetivos=[])
    assert not _vencido(objetivos=[_o("running", iniciado=True)])
    assert not _vencido(objetivos=[_o("pending"), _o("succeeded")])
    assert not _vencido(objetivos=[_o("pending", iniciado=True)])


def test_prazo_de_inicio_nao_vale_fora_do_que_ainda_nao_comecou() -> None:
    assert not _vencido(run_status="needs_input"), "esperar a pessoa não é atraso de fila (28.5)"
    assert not _vencido(run_status="completed")
    assert not _vencido(run_status="cancelling")
    assert not _vencido(estado_atual="rodando")
    assert not _vencido(intencao=INTENCAO_PRAZO_DE_INICIO), "já foi cancelada pelo prazo: não cancela de novo"
