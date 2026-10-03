"""Ciclo de vida do livro de aprendizado com o D1 (ADR-054, decisão 1), a promoção por repetição e o teto de tokens.

Domínio puro, sem banco. A tabela é percorrida INTEIRA — estado × estado × ator × side_effect × human_origin × modo —
contra uma reescrita independente da regra do dono: o sistema publica sozinho só o que não tem efeito externo nem
texto de pessoa, com o modo do tipo em `on`; rebaixar é automático; reativar é sempre de pessoa.

Nível de prova: `simulated` (regra pura).
"""
from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.learning.domain.ciclo import (ESTADOS, SYSTEM_ACTOR, TRANSICOES, Actor, Desligamento, ExigeODono,
                                               SkillState, TransicaoProibida, conferir_nascimento, conferir_transicao,
                                               exige_o_dono, motivo_do_veto, permitido)
from app.modules.learning.domain.promocao import (Decisao, Evidencia, Limiares, contadores, evidencia_decisiva,
                                                  veredito_de_repeticao)
from app.modules.learning.domain.tokens import TETOS_DE_FABRICA, Teto, caber, estimar_tokens
from app.modules.learning.domain.vocabulario import Papel, Posicao

S = SkillState
AGORA = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _esperado(frm: SkillState, to: SkillState, actor: Actor, side_effect: bool, human_origin: bool,
              modo_on: bool) -> bool:
    """A regra do dono, reescrita sem olhar a implementação."""
    if S.DRAFT in (frm, to):
        return False
    so_pessoa = {(S.DEPRECATED, S.PUBLISHED), (S.DISABLED, S.PUBLISHED)}
    os_dois = {(S.CANDIDATE, S.VALIDATED), (S.VALIDATED, S.PUBLISHED), (S.CANDIDATE, S.DISABLED),
               (S.VALIDATED, S.DISABLED), (S.PUBLISHED, S.DEPRECATED), (S.PUBLISHED, S.DISABLED)}
    if (frm, to) in so_pessoa:
        return actor is Actor.PERSON
    if (frm, to) not in os_dois:
        return False
    if actor is Actor.PERSON:
        return True
    if to is S.PUBLISHED:
        return not (side_effect or human_origin) and modo_on
    if to is S.VALIDATED:
        return not human_origin
    return True                      # rebaixar é automático


def test_a_tabela_inteira_bate_com_a_regra_do_dono() -> None:
    divergencias = []
    for frm, to, actor, se, ho, modo in itertools.product(SkillState, SkillState, Actor, (False, True),
                                                          (False, True), (False, True)):
        obtido = permitido(frm, to, actor, side_effect=se, human_origin=ho, modo_publica=modo)
        if obtido != _esperado(frm, to, actor, se, ho, modo):
            divergencias.append((frm.value, to.value, actor.value, se, ho, modo, obtido))
    assert divergencias == []
    assert S.DRAFT not in ESTADOS and set(ESTADOS) == set(SkillState) - {S.DRAFT}


def test_o_sistema_nunca_publica_o_que_exige_o_dono() -> None:
    for frm in ESTADOS:
        for se, ho in ((True, False), (False, True), (True, True)):
            assert exige_o_dono(se, ho)
            assert not permitido(frm, S.PUBLISHED, Actor.SYSTEM, side_effect=se, human_origin=ho, modo_publica=True)
    with pytest.raises(ExigeODono, match="efeito externo"):
        conferir_transicao(S.VALIDATED, S.PUBLISHED, SYSTEM_ACTOR, side_effect=True, human_origin=False,
                           modo_publica=True)
    with pytest.raises(ExigeODono, match="texto de pessoa"):
        conferir_transicao(S.VALIDATED, S.PUBLISHED, SYSTEM_ACTOR, side_effect=False, human_origin=True,
                           modo_publica=True)
    # A PESSOA publica o mesmo item: o D1 é sobre quem decide, não sobre o conteúdo.
    assert conferir_transicao(S.VALIDATED, S.PUBLISHED, "painel:flavio", side_effect=True, human_origin=True,
                              modo_publica=False) is Actor.PERSON


def test_o_sistema_nao_nasce_num_estado_que_so_o_dono_alcanca() -> None:
    """`conferir_nascimento` (a segunda camada de `criar_item`): o item criado já num estado segue o mesmo D1 da
    tabela. Percorre estado × ator × side_effect × human_origin, e o que o sistema pode criar publicado é exatamente o
    que ele publicaria a partir de `validated` com o modo em `on`."""
    divergencias = []
    for estado, actor, se, ho in itertools.product(SkillState, Actor, (False, True), (False, True)):
        by = SYSTEM_ACTOR if actor is Actor.SYSTEM else "painel:flavio"
        esperado = actor is Actor.PERSON or not ((estado is S.PUBLISHED and (se or ho))
                                                 or (estado is S.VALIDATED and ho))
        try:
            conferir_nascimento(estado, by, side_effect=se, human_origin=ho)
            obtido = True
        except ExigeODono:
            obtido = False
        if obtido != esperado:
            divergencias.append((estado.value, actor.value, se, ho, obtido))
        if estado is S.PUBLISHED and actor is Actor.SYSTEM:
            assert obtido == permitido(S.VALIDATED, S.PUBLISHED, Actor.SYSTEM, side_effect=se, human_origin=ho,
                                       modo_publica=True)
    assert divergencias == []
    with pytest.raises(ExigeODono, match="efeito externo"):
        conferir_nascimento(S.PUBLISHED, SYSTEM_ACTOR, side_effect=True, human_origin=True)
    with pytest.raises(ExigeODono, match="texto de pessoa"):
        conferir_nascimento(S.PUBLISHED, SYSTEM_ACTOR, side_effect=False, human_origin=True)


def test_sem_efeito_o_sistema_publica_so_com_o_modo_em_on() -> None:
    assert conferir_transicao(S.VALIDATED, S.PUBLISHED, SYSTEM_ACTOR, side_effect=False, human_origin=False,
                              modo_publica=True) is Actor.SYSTEM
    with pytest.raises(TransicaoProibida, match="modo"):
        conferir_transicao(S.VALIDATED, S.PUBLISHED, SYSTEM_ACTOR, side_effect=False, human_origin=False,
                           modo_publica=False)


def test_reativar_e_sempre_de_pessoa_e_rebaixar_e_automatico() -> None:
    for frm in (S.DEPRECATED, S.DISABLED):
        assert TRANSICOES[(frm, S.PUBLISHED)] == frozenset({Actor.PERSON})
        with pytest.raises(TransicaoProibida, match="pessoa"):
            conferir_transicao(frm, S.PUBLISHED, SYSTEM_ACTOR, side_effect=False, human_origin=False,
                               modo_publica=True)
    for par in ((S.PUBLISHED, S.DISABLED), (S.PUBLISHED, S.DEPRECATED), (S.CANDIDATE, S.DISABLED),
                (S.VALIDATED, S.DISABLED)):
        assert conferir_transicao(*par, SYSTEM_ACTOR, side_effect=True, human_origin=True,
                                  modo_publica=False) is Actor.SYSTEM


def test_quem_decidiu_nunca_fica_vazio_e_nao_ha_rascunho() -> None:
    with pytest.raises(TransicaoProibida, match="quem decidiu"):
        conferir_transicao(S.CANDIDATE, S.VALIDATED, "  ", side_effect=False, human_origin=False, modo_publica=True)
    with pytest.raises(TransicaoProibida, match="rascunho"):
        conferir_transicao(S.DRAFT, S.CANDIDATE, "painel", side_effect=False, human_origin=False, modo_publica=True)
    with pytest.raises(TransicaoProibida, match="proibida"):
        conferir_transicao(S.CANDIDATE, S.PUBLISHED, "painel", side_effect=False, human_origin=False,
                           modo_publica=True)


# ------------------------------------------------------------------ veto por content_hash
def _d(to: SkillState, by: str, dias_atras: float, versao: str | None = "447") -> Desligamento:
    return Desligamento(to_state=to, decided_by=by, app_version=versao,
                        decided_at=(AGORA - timedelta(days=dias_atras)).strftime("%Y-%m-%dT%H:%M:%S.000Z"))


def test_desligado_por_pessoa_nao_volta_pelo_sistema_ate_uma_pessoa_reativar() -> None:
    assert motivo_do_veto([], agora=AGORA, app_version="447") is None
    vetado = [_d(S.PUBLISHED, SYSTEM_ACTOR, 400), _d(S.DISABLED, "painel:flavio", 300)]
    assert "pessoa" in (motivo_do_veto(vetado, agora=AGORA, app_version="999") or "")    # nem com versão nova
    reativado = [*vetado, _d(S.PUBLISHED, "painel:flavio", 10)]
    assert motivo_do_veto(reativado, agora=AGORA, app_version="447") is None


def test_a_frase_do_veto_fala_a_data_do_painel_e_o_motivo_de_quem_desligou() -> None:
    # UX do deploy 8: a linha do item dizia "em 2026-10-03" (ISO) e escondia o motivo da pessoa na trilha.
    pessoa = Desligamento(to_state=S.DISABLED, decided_by="orquestradora", decided_at="2026-09-27T10:00:00.000Z",
                          app_version="447", reason="validar pela IA\n antes de valer")
    assert motivo_do_veto([pessoa], agora=AGORA, app_version="447") == (
        "desligado por uma pessoa (orquestradora) em 27/09/2026 (validar pela IA antes de valer): só uma pessoa o reativa")
    sem_motivo = Desligamento(to_state=S.DISABLED, decided_by="painel:flavio", decided_at="2026-09-27T10:00:00.000Z",
                              app_version="447")
    assert motivo_do_veto([sem_motivo], agora=AGORA, app_version="447") == (
        "desligado por uma pessoa (painel:flavio) em 27/09/2026: só uma pessoa o reativa")
    longo = Desligamento(to_state=S.DISABLED, decided_by="painel:flavio", decided_at="2026-09-27", app_version=None,
                         reason="x" * 500)
    frase = motivo_do_veto([longo], agora=AGORA, app_version="447") or ""
    assert frase.count("x") < 120 and "…): só uma pessoa o reativa" in frase          # cortado, numa linha
    assert "em 19/09/2026:" in (motivo_do_veto([_d(S.DISABLED, SYSTEM_ACTOR, 10)], agora=AGORA, app_version="447") or "")
    # O que não for ISO segue como veio (nunca some).
    torto = Desligamento(to_state=S.DISABLED, decided_by="painel:flavio", decided_at="ontem", app_version=None)
    assert "em ontem:" in (motivo_do_veto([torto], agora=AGORA, app_version="447") or "")


def test_desligado_pelo_sistema_fica_vetado_90_dias_ou_ate_mudar_a_versao() -> None:
    recente = [_d(S.DISABLED, SYSTEM_ACTOR, 10)]
    assert "sistema" in (motivo_do_veto(recente, agora=AGORA, app_version="447") or "")
    assert motivo_do_veto(recente, agora=AGORA, app_version="448") is None
    assert motivo_do_veto([_d(S.DISABLED, SYSTEM_ACTOR, 91)], agora=AGORA, app_version="447") is None
    # Aposentar (deprecated) não é refutação: não veta.
    assert motivo_do_veto([_d(S.DEPRECATED, SYSTEM_ACTOR, 1)], agora=AGORA, app_version="447") is None


# ------------------------------------------------------------------ promoção por repetição
def _ev(stance: Posicao = Posicao.FOR, run: str = "r1", aparelho: str = "android-01", *,
        simulado: bool = False, origem: str = "") -> Evidencia:
    return Evidencia(item_ref="li-x", stance=stance, origin_ref=origem or f"attempt:{run}:{aparelho}", run_id=run,
                     instance_id=aparelho, app_version="447", simulated=simulado, detail=None,
                     observed_at="2026-09-29T10:00:00.000Z")


def test_so_evidencia_real_promove_e_conta_por_execucao_distinta() -> None:
    limiares = Limiares(n_min=3, execucoes_min=2, aparelhos_min=1, contra_max=0)
    mesma_execucao = [_ev(run="r1", origem=f"attempt:{i}") for i in range(5)]
    v = veredito_de_repeticao(mesma_execucao, limiares)
    assert v.decisao is Decisao.ESPERA and v.execucoes == 1 and v.faltam == 1
    simuladas = [_ev(run=f"r{i}", simulado=True) for i in range(10)]
    assert veredito_de_repeticao(simuladas, limiares).decisao is Decisao.ESPERA
    assert veredito_de_repeticao(simuladas, limiares).a_favor == 0
    real = [_ev(run="r1"), _ev(run="r2"), _ev(run="r2", aparelho="android-06")]
    promove = veredito_de_repeticao(real, limiares)
    assert promove.decisao is Decisao.PROMOVE and promove.faltam == 0 and promove.aparelhos == 2


def test_contra_ou_conflito_real_contradiz_e_simulado_nao() -> None:
    base = [_ev(run="r1"), _ev(run="r2"), _ev(run="r3")]
    assert veredito_de_repeticao([*base, _ev(Posicao.CONFLICT, run="r4")]).decisao is Decisao.CONTRADITA
    assert veredito_de_repeticao([*base, _ev(Posicao.AGAINST, run="r4", simulado=True)]).decisao is Decisao.PROMOVE
    c = contadores([*base, _ev(Posicao.AGAINST, run="r4"), _ev(run="r9", simulado=True)])
    assert (c.a_favor, c.contra, c.execucoes, c.aparelhos) == (3, 1, 3, 1)


def test_evidencia_decisiva_e_a_primeira_que_vira_o_veredito() -> None:
    """A execução que a trilha leva: a da evidência cujo prefixo já dá o veredito — a simulada e a da mesma execução
    não viram nada; depois de virado, as seguintes só reforçam."""
    limiares = Limiares(n_min=3, execucoes_min=3, aparelhos_min=0, contra_max=0)
    cronologicas = [_ev(run="r1"), _ev(run="r1", origem="attempt:bis"), _ev(run="r2"), _ev(run="r9", simulado=True),
                    _ev(run="r3"), _ev(run="r4"), _ev(Posicao.AGAINST, run="r5"), _ev(Posicao.AGAINST, run="r6")]
    promove = evidencia_decisiva(cronologicas, Decisao.PROMOVE, limiares)
    assert promove is not None and promove.run_id == "r3"
    contradita = evidencia_decisiva(cronologicas, Decisao.CONTRADITA, limiares)
    assert contradita is not None and contradita.run_id == "r5"
    assert evidencia_decisiva(cronologicas[:3], Decisao.PROMOVE, limiares) is None
    assert evidencia_decisiva([], Decisao.CONTRADITA, limiares) is None


# ------------------------------------------------------------------ teto de tokens
def test_teto_por_papel_corta_inteiro_e_conta_o_que_ficou_de_fora() -> None:
    assert TETOS_DE_FABRICA[Papel.ACTOR] == Teto(tokens=120, itens=3, caracteres_por_item=240)
    assert TETOS_DE_FABRICA[Papel.PLANNER].tokens == 150 and Papel.CLASSIFIER not in TETOS_DE_FABRICA
    assert estimar_tokens("") == 0 and estimar_tokens("a" * 36) == 11
    curtas = ["x" * 100] * 5                                  # 28 tokens cada: cabem 3 (itens), não 4
    s = caber(curtas, TETOS_DE_FABRICA[Papel.ACTOR])
    assert len(s.escolhidos) == 3 and s.cortados == 2 and s.tokens <= 120
    longa = "y" * 241                                         # passa do limite de caracteres por lição do ator
    assert caber([longa, "z" * 10], TETOS_DE_FABRICA[Papel.ACTOR]).escolhidos == ("z" * 10,)
    grandes = ["w" * 300] * 3                                 # 84 tokens cada: o planejador (150) leva uma
    assert len(caber(grandes, TETOS_DE_FABRICA[Papel.PLANNER]).escolhidos) == 1
