"""Ações permitidas calculadas no backend (aprendizado vivo §5.4, item 30.5).

O painel deixou de espelhar `ciclo.TRANSICOES`: a entrada do livro traz `acoes` (o que a PESSOA pode fazer) e
`por_que_nao_publica` (por que o sistema não publica sozinho). Estes testes percorrem a tabela × D1 × tipo × estado e
conferem que `acoes` só contém transições válidas para a pessoa e que nenhuma válida falta, salvo as exceções
declaradas aqui (a regra de cada fonte).

Nível de prova: `simulated` (função pura e o JSON da apresentação, sem banco nem aparelho).
"""
from __future__ import annotations

from itertools import product

from app.modules.learning.domain.ciclo import (ESTADOS, TRANSICOES, Actor, SkillState, TransicaoProibida,
                                               conferir_transicao, permitido)
from app.modules.learning.domain.livro import EntradaDoLivro, acoes_da_pessoa, por_que_o_sistema_nao_publica
from app.modules.learning.domain.vocabulario import LivroKind, Origem
from app.modules.learning.presentation.livro import _entrada

S = SkillState
BOOLS = (False, True)


def _e(kind: LivroKind, state: SkillState | None, side_effect: bool = False, human_origin: bool = False,
       ) -> EntradaDoLivro:
    return EntradaDoLivro(kind=kind, ref="1", state=state, native_status=None, title="t", app=None,
                          origin=Origem.SISTEMA, side_effect=side_effect, human_origin=human_origin)


def _excecao(kind: LivroKind, de: SkillState, para: SkillState) -> bool:
    """O que a tabela permite à pessoa mas o livro não oferece: a regra da fonte, não a do ciclo."""
    if kind in (LivroKind.HABILIDADE, LivroKind.MEMORIA):
        return True                                  # rota própria / fora do D1
    if kind is LivroKind.FLUXO and para is S.DEPRECATED:
        return True                                  # fluxo não tem aposentadoria
    return kind is LivroKind.RECEITA and de is S.DEPRECATED       # receita substituída não volta


def test_paridade_com_a_tabela_para_a_pessoa() -> None:
    for kind, estado, efeito, texto in product(LivroKind, ESTADOS, BOOLS, BOOLS):
        acoes = acoes_da_pessoa(_e(kind, estado, efeito, texto))
        destinos = [a.to for a in acoes]
        assert len(destinos) == len(set(destinos))
        for a in acoes:                              # só o que é válido para `by=pessoa`
            assert Actor.PERSON in TRANSICOES[(estado, a.to)], (kind, estado, a.to)
            conferir_transicao(estado, a.to, "painel", side_effect=efeito, human_origin=texto, modo_publica=True)
            assert a.exige_motivo is True and a.rotulo
        for para in ESTADOS:                         # e nenhuma válida falta
            try:
                conferir_transicao(estado, para, "painel", side_effect=efeito, human_origin=texto, modo_publica=True)
            except TransicaoProibida:
                assert para not in destinos, (kind, estado, para)
                continue
            assert (para in destinos) is (not _excecao(kind, estado, para)), (kind, estado, para, efeito, texto)


def test_o_modo_do_tipo_nao_trava_a_pessoa() -> None:
    for kind, estado in product(LivroKind, ESTADOS):
        e = _e(kind, estado, True)
        assert acoes_da_pessoa(e, modo_publica=False) == acoes_da_pessoa(e, modo_publica=True)
    assert permitido(S.VALIDATED, S.PUBLISHED, Actor.PERSON, side_effect=True, modo_publica=False)


def test_o_roteiro_de_cada_estado() -> None:
    def passos(kind: LivroKind, estado: SkillState | None) -> list[tuple[str, str]]:
        return [(a.rotulo, a.to.value) for a in acoes_da_pessoa(_e(kind, estado))]

    assert passos(LivroKind.RECEITA, S.CANDIDATE) == [("validar", "validated"), ("rejeitar", "disabled")]
    assert passos(LivroKind.LICAO, S.VALIDATED) == [("aprovar", "published"), ("rejeitar", "disabled")]
    assert passos(LivroKind.RECEITA, S.PUBLISHED) == [("aposentar", "deprecated"), ("desligar", "disabled")]
    assert passos(LivroKind.FLUXO, S.PUBLISHED) == [("desligar", "disabled")]
    assert passos(LivroKind.FLUXO, S.DISABLED) == [("reativar", "published")]
    assert passos(LivroKind.TELA, S.DEPRECATED) == [("reativar", "published")]
    assert passos(LivroKind.RECEITA, S.DEPRECATED) == []
    assert passos(LivroKind.HABILIDADE, S.VALIDATED) == []
    assert passos(LivroKind.MEMORIA, None) == []


def test_por_que_o_sistema_nao_publica() -> None:
    def codigo(e: EntradaDoLivro, **kw: object) -> str | None:
        m = por_que_o_sistema_nao_publica(e, **kw)  # type: ignore[arg-type]
        return None if m is None else m.codigo

    assert codigo(_e(LivroKind.HABILIDADE, S.VALIDATED)) == "habilidade"
    assert codigo(_e(LivroKind.RECEITA, S.VALIDATED, side_effect=True)) == "efeito_externo"
    assert codigo(_e(LivroKind.LICAO, S.CANDIDATE, human_origin=True)) == "texto_de_pessoa"
    assert codigo(_e(LivroKind.LICAO, S.CANDIDATE, side_effect=True, human_origin=True)) == "efeito_externo"
    livre = _e(LivroKind.LICAO, S.VALIDATED)
    assert por_que_o_sistema_nao_publica(livre) is None
    vetado = por_que_o_sistema_nao_publica(livre, veto="desligado por uma pessoa")
    assert vetado is not None and (vetado.codigo, vetado.espera_o_dono, vetado.detalhe) == (
        "vetado", False, "desligado por uma pessoa")
    modo = por_que_o_sistema_nao_publica(livre, modo_publica=False)
    assert modo is not None and (modo.codigo, modo.espera_o_dono) == ("modo_desligado", False)
    for kind, efeito, texto in product(LivroKind, BOOLS, BOOLS):     # o dono só espera onde o D1 manda
        m = por_que_o_sistema_nao_publica(_e(kind, S.VALIDATED, efeito, texto))
        assert (m is not None and m.espera_o_dono) is (kind is LivroKind.HABILIDADE or efeito or texto)


def test_o_json_da_entrada_traz_acoes_e_motivo() -> None:
    j = _entrada(_e(LivroKind.RECEITA, S.VALIDATED, side_effect=True))
    assert j["acoes"] == [{"to": "published", "rotulo": "aprovar", "exige_motivo": True},
                          {"to": "disabled", "rotulo": "rejeitar", "exige_motivo": True}]
    assert j["por_que_nao_publica"] == {"codigo": "efeito_externo", "espera_o_dono": True, "detalhe": None}
    j = _entrada(_e(LivroKind.HABILIDADE, S.VALIDATED))
    assert j["acoes"] == [] and j["por_que_nao_publica"] is not None
    assert _entrada(_e(LivroKind.LICAO, S.PUBLISHED))["por_que_nao_publica"] is None
