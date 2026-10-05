"""30.73: na classe B, o parecer do curador não pede voto nem decisão da pessoa por "efeito sem catálogo".

A medida do 30.72 (05/10, banco do central, só leitura): 5 fluxos B com ≥ 2 execuções reais em ≥ 2 aparelhos e nenhuma
evidência contra pararam em `observar`, porque o parecer pedia `voto_da_pessoa` e `decisao_da_pessoa`. Na classe B,
"efeito em app sem catálogo" é a própria definição da classe, e nenhuma execução produz essas duas faltas.

O que estes testes guardam:
- o dossiê B leva o fato `risco.classe_b_e`; o A e o C não mudam;
- as opções de `falta` do item B não têm as duas faltas da pessoa; as do A e do C continuam todas;
- o parecer B novo que as traga (provedor sem esquema estrito) sai sem elas, e continua válido; no A e no C, ficam;
- o descarte fica visível: `falta_descartada` no parecer e na `saida` gravada (só quando houve), e volta na leitura;
- o parecer JÁ GRAVADO com `voto_da_pessoa` se lê como foi gravado (o estoque não muda);
- a instrução do hub (`CURADOR_SYSTEM`) tem a frase da B, e o texto está preso à versão do template (`curador-v2`).

Prova `simulated`: domínio puro, sem provedor, sem banco, sem aparelho.
"""
from __future__ import annotations

import hashlib

from app.modules.learning.domain.curador import (CLASSE_B_DO_ITEM, FALTA_SO_DA_PESSOA, OPCOES_FECHADAS, Falta,
                                                 faltas_do_item, opcoes_do_dossie, validar_saida)
from app.modules.learning.domain.parecer import parecer_gravado
from app.modules.learning.domain.politica_de_risco import ClasseDeRisco
from app.planning.curador import CURADOR_SYSTEM, VERSAO_DO_TEMPLATE, esquema_do_parecer

from .test_learning_curador_dominio import A, B, C, _dossie, _saida

PESSOA = {Falta.VOTO_DA_PESSOA.value, Falta.DECISAO_DA_PESSOA.value}
#: O hash do `CURADOR_SYSTEM` da `curador-v2`. Mudou o texto: suba a `VERSAO_DO_TEMPLATE` e troque o hash aqui. É o
#: que liga o parecer gravado (`learning_reviews.template_versao`) ao texto que a IA leu.
HASH_DO_CURADOR_V2 = "7c0916f1a669275cc92ada31ef5027cdd56b00738741566ac45231a6666b185b"


def test_o_dossie_b_leva_o_fato_da_classe_e_o_a_e_o_c_nao() -> None:
    for fatos, classe, com_fato in ((A, ClasseDeRisco.A, False), (B, ClasseDeRisco.B, True),
                                    (C, ClasseDeRisco.C, False)):
        d = _dossie(fatos)
        assert d.classe is classe
        risco = d.como_dados()["risco"]
        assert isinstance(risco, dict)
        assert ("classe_b_e" in risco) is com_fato, classe
        if com_fato:
            assert risco["classe_b_e"] == CLASSE_B_DO_ITEM


def test_so_a_classe_b_perde_as_duas_faltas_da_pessoa() -> None:
    todas = OPCOES_FECHADAS["falta"]
    assert PESSOA <= set(todas)
    assert {f.value for f in FALTA_SO_DA_PESSOA} == PESSOA
    assert faltas_do_item(ClasseDeRisco.A) == todas
    assert faltas_do_item(ClasseDeRisco.C) == todas
    assert faltas_do_item(ClasseDeRisco.B) == tuple(f for f in todas if f not in PESSOA)
    for fatos, esperado in ((A, todas), (C, todas), (B, faltas_do_item(ClasseDeRisco.B))):
        opcoes = opcoes_do_dossie(_dossie(fatos))
        assert opcoes["falta"] == esperado
        # o esquema estrito que vai ao provedor (hub) leva o mesmo enum
        props = esquema_do_parecer({k: list(v) for k, v in opcoes.items()})["properties"]
        assert isinstance(props, dict)
        assert props["falta"]["items"]["enum"] == list(esperado)


def test_o_parecer_b_novo_sai_sem_as_faltas_da_pessoa_e_o_a_e_o_c_as_mantem() -> None:
    falta = ["reproducao_em_outro_aparelho", "voto_da_pessoa", "decisao_da_pessoa"]
    b = validar_saida(_saida(falta=falta), _dossie(B))
    assert b.ok and b.parecer is not None
    assert [f.value for f in b.parecer.falta] == ["reproducao_em_outro_aparelho"]
    assert {f.value for f in b.parecer.falta_descartada} == PESSOA
    gravada = b.parecer.como_dados()
    assert set(gravada["falta_descartada"]) == PESSOA                 # type: ignore[arg-type]
    relido = parecer_gravado(gravada)
    assert relido is not None and relido.falta_descartada == b.parecer.falta_descartada
    for fatos in (A, C):
        v = validar_saida(_saida(falta=falta), _dossie(fatos))
        assert v.ok and v.parecer is not None
        assert {f.value for f in v.parecer.falta} == set(falta)
        assert v.parecer.falta_descartada == ()
        assert "falta_descartada" not in v.parecer.como_dados()      # sem descarte, a `saida` é a de antes


def test_o_parecer_ja_gravado_com_voto_da_pessoa_se_le_como_foi_gravado() -> None:
    """O estoque de `curador-v1` (as 27 revisões B de fluxo do 30.72 têm `voto_da_pessoa`): a lista fechada vale para o
    parecer NOVO; a leitura do gravado não filtra nada."""
    gravado = _saida(falta=["voto_da_pessoa", "decisao_da_pessoa", "execucao_real"], faixa="B")
    p = parecer_gravado(gravado)
    assert p is not None
    assert {f.value for f in p.falta} == {"voto_da_pessoa", "decisao_da_pessoa", "execucao_real"}


def test_a_instrucao_do_hub_tem_a_frase_da_b_e_esta_presa_a_versao() -> None:
    assert VERSAO_DO_TEMPLATE == "curador-v2"
    assert "Na classe B, o efeito sem entrada no catálogo é o que define a classe" in CURADOR_SYSTEM
    assert "O aceite continua sendo da pessoa." in CURADOR_SYSTEM            # a redação da Jev: o parecer não decide
    assert hashlib.sha256(CURADOR_SYSTEM.encode()).hexdigest() == HASH_DO_CURADOR_V2
