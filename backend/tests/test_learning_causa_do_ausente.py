"""A causa do "ausente" e a doadora da herança (RA-20, item 29.40). Domínio puro; prova `simulated`."""
from __future__ import annotations

import itertools

import pytest

from app.modules.learning.domain.causa_do_ausente import (CausaDoAusente, ChaveDaReceita, ReceitaVizinha,
                                                          causa_do_ausente, doadora)

AQUI = ChaveDaReceita("447.0.0.55.81", "sig-a", "pt-BR/xhdpi")
_ids = itertools.count(1)


def _v(status: str, *, versao: str = "447.0.0.55.81", assinatura: str = "sig-a", variante: str = "pt-BR/xhdpi",
       rid: int | None = None) -> ReceitaVizinha:
    return ReceitaVizinha(rid if rid is not None else next(_ids), ChaveDaReceita(versao, assinatura, variante), status)


@pytest.mark.parametrize(("vizinhas", "esperada"), [
    ([], CausaDoAusente.SEM_RECEITA),
    ([_v("quarantined", versao="446")], CausaDoAusente.SEM_RECEITA),             # quarentena não é viva
    ([_v("candidate", variante="en-US/xhdpi")], CausaDoAusente.VARIANTE),
    ([_v("active", variante="", assinatura="")], CausaDoAusente.LEGADA),            # as 19 de 17/09
    ([_v("active", variante="")], CausaDoAusente.LEGADA),
    ([_v("active", versao="446.0.0.1")], CausaDoAusente.VERSAO),
    ([_v("active", versao="446.0.0.1", assinatura="")], CausaDoAusente.VERSAO),
    ([_v("active", assinatura="sig-b")], CausaDoAusente.ASSINATURA),
    ([_v("validated")], CausaDoAusente.ESPERA_O_DONO),
    ([_v("superseded")], CausaDoAusente.DESLIGADA),
])
def test_uma_causa_por_situacao(vizinhas: list[ReceitaVizinha], esperada: CausaDoAusente) -> None:
    assert causa_do_ausente(AQUI, vizinhas) is esperada


def test_a_ordem_decide_quando_ha_varias() -> None:
    todas = [_v("active", assinatura="sig-b"), _v("active", versao="446"), _v("active", variante=""),
             _v("active", variante="en-US/xhdpi"), _v("superseded"), _v("validated")]
    esperadas = [CausaDoAusente.ESPERA_O_DONO, CausaDoAusente.DESLIGADA, CausaDoAusente.VARIANTE,
                 CausaDoAusente.LEGADA, CausaDoAusente.VERSAO, CausaDoAusente.ASSINATURA]
    for n, esperada in zip(range(len(todas), 0, -1), esperadas, strict=True):
        assert causa_do_ausente(AQUI, todas[:n]) is esperada


def test_a_doadora_e_a_ativa_mais_proxima_e_nunca_de_outra_assinatura() -> None:
    legada = _v("active", variante="", assinatura="", rid=10)
    antiga = _v("active", versao="446", rid=11)
    nova = _v("active", versao="448", rid=12)
    assert doadora(AQUI, [antiga, legada]) == legada                       # mesma versão vence outra versão
    assert doadora(AQUI, [antiga, nova]) == nova                           # no empate, a mais nova
    assert doadora(AQUI, [_v("active", assinatura="sig-b")]) is None       # outro APK nunca doa
    assert doadora(AQUI, [_v("candidate", versao="446")]) is None          # só a provada doa
    assert doadora(AQUI, [_v("quarantined", versao="446")]) is None
    assert doadora(AQUI, []) is None


def test_chave_esperando_o_dono_ou_desligada_nao_herda() -> None:
    assert doadora(AQUI, [_v("validated"), _v("active", versao="446")]) is None
    assert doadora(AQUI, [_v("superseded"), _v("active", versao="446")]) is None


def test_vocabulario_fechado_e_estavel() -> None:
    assert [c.value for c in CausaDoAusente] == ["espera_o_dono", "desligada", "variante", "legada", "versao",
                                                 "assinatura", "sem_receita"]
