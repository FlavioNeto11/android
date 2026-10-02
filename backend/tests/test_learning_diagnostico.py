"""Falhas com diagnóstico determinístico (item 30.13, `docs/design/aprendizado-vivo.md` §9.1).

O que se prova:
- o vocabulário fechado de `CausaProvavel` e os tipos novos de `TipoDeProposta` (cada causa com regra e fatos);
- as regras puras de `domain/diagnostico.py`: o tipo decide o que não depende de contexto; a receita (versão nova,
  aparelho, receita), a lição, a tela e a falta de conhecimento dependem das tentativas; o que nada sustenta é
  `indeterminada`, escrito, sem chamada de IA;
- o "teto atingido" pelo TIPO (`AIError.kind == 'budget'` em `ai_calls.error_kind`), não pelo texto: o texto do teto do
  pedido, que fugia da regra por trecho, passa a cair em `ia_orcamento`; o casamento por texto segue como legado;
- com banco semeado: o conhecimento envolvido por grupo (receita aproximada ou exata, lição exposta, tela, fluxo), a
  causa, a proposta como linha do backlog com `parent_id` e o JSON do relatório.

Nível de prova: `simulated` (banco de teste; nenhum aparelho, nenhuma IA).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta

import pytest

from app.db import Database
from app.modules.learning.application.falhas import ServicoDeFalhas
from app.modules.learning.domain.backlog import chave_do_grupo
from app.modules.learning.domain.diagnostico import (ContextoDaTentativa, EstatisticaDaLicao, ReceitaDaTentativa,
                                                     TelaDaTentativa, diagnosticar, proposta_do_diagnostico)
from app.modules.learning.domain.falhas import FailureKind, classificar_falha, classificar_pelo_tipo_da_ia
from app.modules.learning.domain.vocabulario import CausaProvavel, EstadoDoBacklog, TipoDeProposta
from app.modules.learning.infrastructure.montagem import montar_aprendizado
from app.modules.learning.presentation.falhas import relatorio_json, relatorio_md

from .fake_skills import banco as banco_migrado
from .test_learning_backlog import AGORA, PACOTE, Etapa, Mundo, T, iso, montar, semear

APP = "com.exemplo.app"


def chave(tipo: FailureKind | str, *, capability: str = "OPEN_POST", tela: str = ""):
    return chave_do_grupo(APP, capability, tipo.value if isinstance(tipo, FailureKind) else tipo, tela)


def ctx(n: int = 1, **kw) -> ContextoDaTentativa:
    base = dict(attempt_id=f"a{n}", step_id=f"s{n}", run_id=f"r{n}", instance_id="android-01", driven_by="ai",
                estrategia=None, capability="OPEN_POST", receita=None, licoes=(), tela=None, fluxo=None,
                habilidade=None)
    return ContextoDaTentativa(**{**base, **kw})


def receita(ref: str = "receita:12", *, status: str = "active", estado: str = "comprovado",
            aproximada: bool = False) -> ReceitaDaTentativa:
    return ReceitaDaTentativa(ref, status, "1.0", estado, aproximada)


# ------------------------------------------------------------------ vocabulário
def test_vocabulario_fechado_e_tipos_novos_de_proposta() -> None:
    assert {c.value for c in CausaProvavel} == {
        "teto_de_ia", "provedor_de_ia", "sessao_ou_autenticacao", "aparelho", "plano", "informacao_da_pessoa",
        "catalogo_recusou", "verificador", "receita_divergiu", "versao_nova", "licao_atrapalha",
        "tela_desconhecida", "falta_conhecimento", "indeterminada"}
    assert {"rebaixar_receita", "revisar_licao", "reaprender_tela", "ajustar_catalogo",
            "investigar"} <= {t.value for t in TipoDeProposta}


def test_todo_tipo_de_falha_tem_causa_sem_excecao() -> None:
    """Nenhum tipo do vocabulário fecha em erro: o diagnóstico sem contexto sempre devolve uma causa do enum."""
    for tipo in FailureKind:
        d = diagnosticar(chave(tipo), ())
        assert isinstance(d.causa, CausaProvavel)
        assert d.fatos, tipo


# ------------------------------------------------------------------ o tipo decide
@pytest.mark.parametrize("tipo,causa", [
    (FailureKind.IA_ORCAMENTO, CausaProvavel.TETO_DE_IA),
    (FailureKind.IA_SALDO, CausaProvavel.PROVEDOR_DE_IA), (FailureKind.IA_INDISPONIVEL, CausaProvavel.PROVEDOR_DE_IA),
    (FailureKind.IA_RECUSA, CausaProvavel.PROVEDOR_DE_IA),
    (FailureKind.AUTENTICACAO, CausaProvavel.SESSAO_OU_AUTENTICACAO),
    (FailureKind.CONTA_ERRADA, CausaProvavel.SESSAO_OU_AUTENTICACAO),
    (FailureKind.APP_ANR, CausaProvavel.APARELHO), (FailureKind.PRAZO_DA_ETAPA, CausaProvavel.APARELHO),
    (FailureKind.SESSAO_DE_AUTOMACAO, CausaProvavel.APARELHO), (FailureKind.INTERROMPIDA, CausaProvavel.APARELHO),
    (FailureKind.DEFEITO_DO_PLANO, CausaProvavel.PLANO),
    (FailureKind.FALTA_INFORMACAO, CausaProvavel.INFORMACAO_DA_PESSOA),
    (FailureKind.EFEITO_GUARDA_NAO_ATENDIDA, CausaProvavel.CATALOGO_RECUSOU),
    (FailureKind.EFEITO_NAO_COMPROVADO, CausaProvavel.VERIFICADOR),
    (FailureKind.POS_CONDICAO_NAO_COMPROVADA, CausaProvavel.VERIFICADOR),
    ("verificacao_falso_positivo", CausaProvavel.VERIFICADOR),
    (FailureKind.OUTRO, CausaProvavel.INDETERMINADA),
])
def test_o_tipo_decide_as_causas_que_nao_dependem_de_contexto(tipo, causa) -> None:
    assert diagnosticar(chave(tipo), ()).causa is causa


def test_teto_pelo_tipo_traz_o_fato_do_kind_e_o_legado_diz_texto() -> None:
    pelo_tipo = diagnosticar(chave(FailureKind.IA_ORCAMENTO), (), erros_de_ia={"budget": 2})
    assert any("error_kind=budget em 2" in f.valor for f in pelo_tipo.fatos)
    legado = diagnosticar(chave(FailureKind.IA_ORCAMENTO), ())
    assert any("legado" in f.valor for f in legado.fatos)


def test_catalogo_recusou_propoe_ajustar_o_catalogo_com_alvo_citavel() -> None:
    d = diagnosticar(chave(FailureKind.EFEITO_GUARDA_NAO_ATENDIDA, capability="LIKE_POST"), ())
    assert d.proposta is not None
    assert (d.proposta.tipo, d.proposta.alvo) == (TipoDeProposta.AJUSTAR_CATALOGO, f"catalogo:{APP}/LIKE_POST")


def test_causas_de_ambiente_ou_de_ia_nao_geram_proposta() -> None:
    for tipo in (FailureKind.IA_ORCAMENTO, FailureKind.IA_SALDO, FailureKind.APP_ANR, FailureKind.AUTENTICACAO):
        assert diagnosticar(chave(tipo), ()).proposta is None


# ------------------------------------------------------------------ o contexto decide
def test_tipo_de_conhecimento_sem_contexto_e_indeterminada_e_nunca_um_palpite() -> None:
    d = diagnosticar(chave(FailureKind.ALVO_AUSENTE), ())
    assert d.indeterminada and [f.codigo for f in d.fatos] == ["sem_contexto"] and d.amostra == 0
    assert d.proposta is not None and d.proposta.tipo is TipoDeProposta.INVESTIGAR


def test_receita_que_falha_em_todos_os_aparelhos_e_receita_divergiu_e_propoe_rebaixar() -> None:
    contextos = [ctx(n, driven_by="recipe+ai", receita=receita(), outros_falha=1) for n in range(1, 4)]
    d = diagnosticar(chave(FailureKind.ALVO_AUSENTE), contextos)
    assert d.causa is CausaProvavel.RECEITA_DIVERGIU
    assert d.proposta is not None and (d.proposta.tipo, d.proposta.alvo) == (TipoDeProposta.REBAIXAR_RECEITA,
                                                                              "receita:12")
    assert {f.codigo for f in d.fatos} >= {"conduzidas_por_receita", "falhou_em_todos_os_aparelhos", "receita"}


def test_mesma_etapa_ok_noutro_aparelho_e_aparelho() -> None:
    d = diagnosticar(chave(FailureKind.ALVO_AUSENTE),
                     [ctx(n, driven_by="recipe+ai", receita=receita(), outros_ok=2) for n in range(1, 4)])
    assert d.causa is CausaProvavel.APARELHO and d.proposta is None


def test_receita_em_aparelho_unico_sem_quarentena_e_indeterminada() -> None:
    """A regra do `aproveitamento.py`: aparelho sozinho na execução fica indeterminado; sem a receita em falha, nada
    sustenta culpá-la."""
    d = diagnosticar(chave(FailureKind.ALVO_AUSENTE), [ctx(1, driven_by="recipe", receita=receita())])
    assert d.indeterminada and "sem_comparacao" in {f.codigo for f in d.fatos}


def test_receita_em_aparelho_unico_mas_em_quarentena_e_receita_divergiu() -> None:
    d = diagnosticar(chave(FailureKind.ALVO_AUSENTE),
                     [ctx(1, driven_by="recipe", receita=receita(status="quarantined", estado="falhando"))])
    assert d.causa is CausaProvavel.RECEITA_DIVERGIU


def test_comparacao_mista_nao_culpa_ninguem() -> None:
    d = diagnosticar(chave(FailureKind.ALVO_AUSENTE),
                     [ctx(1, driven_by="recipe", receita=receita(), outros_ok=1),
                      ctx(2, driven_by="recipe", receita=receita(), outros_falha=1)])
    assert d.indeterminada and "comparacao_mista" in {f.codigo for f in d.fatos}


def test_receita_incompativel_na_versao_nova_do_app_e_versao_nova() -> None:
    d = diagnosticar(chave(FailureKind.ALVO_AUSENTE),
                     [ctx(1, driven_by="recipe+ai", receita=receita(status="quarantined", estado="incompativel"))])
    assert d.causa is CausaProvavel.VERSAO_NOVA
    assert d.proposta is not None and d.proposta.tipo is TipoDeProposta.REAPRENDER_TELA


def test_licao_com_taxa_pior_que_o_controle_e_licao_atrapalha() -> None:
    contextos = [ctx(n, licoes=("li-ruim",)) for n in range(1, 4)]
    estatistica = EstatisticaDaLicao("li-ruim", "published", "em_prova", com=5, com_falhas=4, controle=5,
                                     controle_falhas=1)
    d = diagnosticar(chave(FailureKind.CICLO_SEM_PROGRESSO), contextos, licoes={"li-ruim": estatistica})
    assert d.causa is CausaProvavel.LICAO_ATRAPALHA
    assert d.proposta is not None and (d.proposta.tipo, d.proposta.alvo) == (TipoDeProposta.REVISAR_LICAO, "li-ruim")


def test_licao_sem_amostra_no_controle_nao_e_acusada() -> None:
    contextos = [ctx(n, licoes=("li-x",)) for n in range(1, 4)]
    pouca = EstatisticaDaLicao("li-x", "published", None, com=5, com_falhas=5, controle=1, controle_falhas=0)
    d = diagnosticar(chave(FailureKind.CICLO_SEM_PROGRESSO), contextos, licoes={"li-x": pouca})
    assert d.causa is not CausaProvavel.LICAO_ATRAPALHA


def test_falha_fora_de_tela_declarada_e_tela_desconhecida() -> None:
    d = diagnosticar(chave(FailureKind.ALVO_AUSENTE, tela=""), [ctx(1), ctx(2)])
    assert d.causa is CausaProvavel.TELA_DESCONHECIDA
    assert d.proposta is not None and d.proposta.alvo == f"tela:{APP}/desconhecida"


def test_so_a_ia_na_tela_conhecida_sem_receita_nem_licao_e_falta_de_conhecimento() -> None:
    contextos = [ctx(n, tela=TelaDaTentativa(f"tela:{APP}/feed")) for n in range(1, 4)]
    d = diagnosticar(chave(FailureKind.ALVO_AUSENTE, tela="feed"), contextos)
    assert d.causa is CausaProvavel.FALTA_CONHECIMENTO


def test_sinais_que_nao_fecham_deixam_indeterminada_com_os_numeros() -> None:
    contextos = [ctx(1, licoes=("li-a",), tela=TelaDaTentativa(f"tela:{APP}/feed")),
                 ctx(2, licoes=("li-b",), tela=TelaDaTentativa(f"tela:{APP}/feed"))]
    d = diagnosticar(chave(FailureKind.ALVO_AUSENTE, tela="feed"), contextos)
    assert d.indeterminada and [f.codigo for f in d.fatos] == ["sinais_insuficientes"]


def test_conhecimento_envolvido_cita_refs_conta_etapas_e_marca_o_aproximado() -> None:
    contextos = [ctx(1, receita=receita(aproximada=True), licoes=("li-a",), fluxo="fluxo:f1", habilidade="habilidade:s@2"),
                 ctx(2, receita=receita(aproximada=True), licoes=("li-a",), fluxo="fluxo:f1", habilidade="habilidade:s@2"),
                 ctx(3, receita=None, licoes=(), tela=TelaDaTentativa(f"tela:{APP}/feed", "li-t", "published"))]
    d = diagnosticar(chave(FailureKind.ALVO_AUSENTE, tela="feed"), contextos)
    por_ref = {k.ref: k for k in d.conhecimento}
    assert por_ref["receita:12"].etapas == 2 and por_ref["receita:12"].aproximado is True
    assert por_ref["li-a"].etapas == 2 and por_ref["fluxo:f1"].kind == "fluxo"
    assert por_ref["habilidade:s@2"].kind == "habilidade"
    assert por_ref["li-t"].estado == "published" and por_ref[f"tela:{APP}/feed"].kind == "tela"


def test_proposta_do_diagnostico_tem_parent_no_grupo_e_ref_estavel() -> None:
    c = chave(FailureKind.EFEITO_GUARDA_NAO_ATENDIDA)
    p = proposta_do_diagnostico(c, diagnosticar(c, ()))
    assert p is not None and p.parent_id == c.id and p.ref == f"{c.id}|catalogo:{APP}/OPEN_POST"
    assert p.causa == "catalogo_recusou" and p.fragmento == "" and p.tipo is TipoDeProposta.AJUSTAR_CATALOGO
    assert proposta_do_diagnostico(c, diagnosticar(c, ())).id == p.id        # estável: o upsert não duplica
    assert proposta_do_diagnostico(chave(FailureKind.IA_ORCAMENTO), diagnosticar(chave(FailureKind.IA_ORCAMENTO), ())) is None


# ------------------------------------------------------------------ o teto pelo tipo
def test_kind_do_provedor_vence_o_texto_e_o_texto_segue_como_legado() -> None:
    assert classificar_pelo_tipo_da_ia(["budget"]) is FailureKind.IA_ORCAMENTO
    assert classificar_pelo_tipo_da_ia(["error", "budget", "refusal"]) is FailureKind.IA_ORCAMENTO   # precedência
    assert classificar_pelo_tipo_da_ia(["billing"]) is FailureKind.IA_SALDO
    assert classificar_pelo_tipo_da_ia(["refusal"]) is FailureKind.IA_RECUSA
    assert classificar_pelo_tipo_da_ia(["not_configured"]) is FailureKind.IA_INDISPONIVEL
    assert classificar_pelo_tipo_da_ia(["step_deadline", "invalid_output", "error"]) is None
    assert classificar_pelo_tipo_da_ia([]) is None
    # Legado: o texto de hoje do executor ainda cai em `ia_orcamento` sem `ai_calls`...
    assert classificar_falha("Limite de 40 chamadas de IA por objetivo atingido.", "failed") is FailureKind.IA_ORCAMENTO
    # ...e o teto do PEDIDO, que a regra por trecho nunca pegou, é o que só o tipo resolve.
    assert classificar_falha("Orçamento do pedido atingido nesta ocorrência: US$ 1.00 de US$ 1.00.",
                             "failed") is FailureKind.OUTRO


