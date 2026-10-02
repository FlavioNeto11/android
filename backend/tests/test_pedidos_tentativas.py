"""Item 28.5 — as regras puras de tentativa, efeito e pausa (`modules/pedidos/domain/tentativas.py`).

Prova `simulated` (`arquivo::teste`): funções puras, relógio passado à mão. O laço de verdade, em SQLite, está em
`test_pedidos_retentativa.py`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.pedidos.domain.estados import OCORRENCIA, PEDIDO_ATORES
from app.modules.pedidos.domain.tentativas import (ACAO_DEFINITIVA, ACAO_FIM, ACAO_INCERTA, ACAO_REPETIR, AVISOS,
                                                   atraso_s, aviso, decidir, deve_pausar, falhas_seguidas)

AGORA = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)


def _decidir(**kw):
    base = dict(estado="falhou", tentativa=1, max_tentativas=2, efeito_possivel=False, agora=AGORA, base_s=60.0,
                teto_s=900.0)
    base.update(kw)
    return decidir(**base)


# =============================================================================================== atraso
def test_atraso_dobra_a_cada_falha_e_para_no_teto() -> None:
    assert [atraso_s(n, 60, 900) for n in (1, 2, 3, 4, 5, 6)] == [60, 120, 240, 480, 900, 900]


def test_atraso_nao_estoura_com_tentativa_absurda_nem_zero() -> None:
    assert atraso_s(0, 60, 900) == 60 and atraso_s(10_000, 60, 900) == 900


# =============================================================================================== decidir
def test_falha_sem_efeito_repete_com_atraso_exponencial_a_partir_de_agora() -> None:
    d = _decidir()
    assert d.acao == ACAO_REPETIR and d.repetir_em == AGORA + timedelta(seconds=60) and d.complemento is None
    d3 = _decidir(tentativa=3, max_tentativas=5)
    assert d3.acao == ACAO_REPETIR and d3.repetir_em == AGORA + timedelta(seconds=240)


def test_falha_com_efeito_possivel_vira_incerta_e_nunca_repete() -> None:
    for tentativa in (1, 2, 5):
        d = _decidir(efeito_possivel=True, tentativa=tentativa, max_tentativas=9)
        assert d.acao == ACAO_INCERTA and d.repetir_em is None and "efeito" in (d.complemento or "")


def test_incerta_nunca_repete_e_vai_a_pessoa_mesmo_sem_efeito_declarado() -> None:
    for efeito in (False, True):
        d = _decidir(estado="incerta", efeito_possivel=efeito)
        assert d.acao == ACAO_INCERTA and d.repetir_em is None


def test_max_tentativas_e_o_total_de_execucoes_da_ocorrencia() -> None:
    assert _decidir(tentativa=1, max_tentativas=2).acao == ACAO_REPETIR
    esgotada = _decidir(tentativa=2, max_tentativas=2)
    assert esgotada.acao == ACAO_DEFINITIVA and "esgotou" in (esgotada.complemento or "")
    assert _decidir(tentativa=1, max_tentativas=1).complemento is None, "pedido que nunca repete não precisa explicar"


def test_sem_orcamento_para_outra_a_falha_e_definitiva_e_diz_o_motivo() -> None:
    d = _decidir(sem_orcamento="orçamento total esgotado")
    assert d.acao == ACAO_DEFINITIVA and "orçamento total esgotado" in (d.complemento or "")


@pytest.mark.parametrize("estado", ["concluida", "cancelada", "perdida", "rodando"])
def test_o_que_nao_e_falha_nem_incerteza_nao_tem_o_que_decidir(estado: str) -> None:
    assert _decidir(estado=estado).acao == ACAO_FIM


# =============================================================================================== falhas seguidas
def test_falhas_seguidas_param_na_primeira_concluida() -> None:
    assert falhas_seguidas(["falhou", "falhou", "concluida", "falhou"]) == 2
    assert falhas_seguidas(["concluida", "falhou", "falhou"]) == 0, "o sucesso zera a contagem"
    assert falhas_seguidas([]) == 0


def test_incerta_zera_e_cancelada_nao_conta_nem_zera() -> None:
    assert falhas_seguidas(["falhou", "incerta", "falhou", "falhou"]) == 1
    assert falhas_seguidas(["falhou", "cancelada", "falhou", "concluida", "falhou"]) == 2


def test_deve_pausar_so_no_limite() -> None:
    assert not deve_pausar(2, 3) and deve_pausar(3, 3) and deve_pausar(4, 3)
    assert not deve_pausar(5, 0), "limite inválido nunca pausa"


# =============================================================================================== aviso
def test_aviso_tem_o_formato_do_contrato_e_o_requer_pessoa_do_tipo() -> None:
    a = aviso(tipo="ocorrencia_incerta", aviso_id="o1:ocorrencia_incerta", pedido_id="p1", pedido_titulo="t",
              ocorrencia_id="o1", mensagem="m", criado_em="2026-10-02T12:00:00.000Z", dados={"x": 1})
    assert a["requer_pessoa"] is True and a["nivel"] == "warn" and a["lido_em"] is None and a["dados"] == {"x": 1}
    assert set(a) == {"id", "pedido_id", "pedido_titulo", "ocorrencia_id", "tipo", "nivel", "mensagem", "dados",
                      "requer_pessoa", "criado_em", "lido_em"}
    p = aviso(tipo="pausa_automatica", aviso_id="x", pedido_id="p1", pedido_titulo="t", ocorrencia_id=None, mensagem="m",
              criado_em="2026-10-02T12:00:00.000Z")
    assert p["requer_pessoa"] is False and p["dados"] == {}


def test_os_tipos_de_aviso_existem_no_vocabulario_do_contrato() -> None:
    assert {"pausa_automatica", "ocorrencia_incerta"} <= set(AVISOS), "o vocabulário completo é de `domain/avisos.py`"


def test_as_arestas_que_a_peca_usa_existem_nas_tabelas_de_estado() -> None:
    """`falhou → devida` (a nova tentativa) e o pedido a `aguardando_pessoa`/`pausado` pelo sistema."""
    assert OCORRENCIA.pode("falhou", "devida") and OCORRENCIA.pode("despachada", "falhou") and OCORRENCIA.pode("rodando", "falhou")
    assert not OCORRENCIA.pode("incerta", "devida"), "incerta nunca gera tentativa nova"
    assert "sistema" in PEDIDO_ATORES[("ativo", "aguardando_pessoa")] and "sistema" in PEDIDO_ATORES[("ativo", "pausado")]
