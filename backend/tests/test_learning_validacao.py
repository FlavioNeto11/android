"""30.31 (domínio): quando o parecer do curador vira pedido de validação, por que ele nasce recusado, e quando o
despachante pode rodar. Puro, sem banco. Nível de prova: `simulated`."""
from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.config import ValidacaoCfg
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.curador import Decisao, Falta
from app.modules.learning.domain.validacao import (Ambiente, AparelhoCandidato, EstadoDoPedido, FatosDoParecer, Folego,
                                                   Grupo, Motivo, escolher_aparelho, falta_automatizavel,
                                                   pedido_do_parecer, pode_despachar)
from app.modules.learning.domain.vocabulario import LivroKind

S = SkillState
#: A receita do QA da 1ª volta (03/10): ativa, com efeito, sem sombra, pedindo execução real noutro aparelho.
RECEITA_QA = FatosDoParecer(
    decisao=Decisao.PEDIR_EVIDENCIA,
    falta=(Falta.EXECUCAO_REAL, Falta.SOMBRA, Falta.REPRODUCAO_EM_OUTRO_APARELHO, Falta.DECISAO_DA_PESSOA),
    kind=LivroKind.RECEITA, estado=S.PUBLISHED, vetado=False, toca_sessao=False, efeito=True, app_qa=True,
    comando='No QA Messenger, envie "Entrega POC {instance_id} {run_id}" para o contato QA-001',
    comando_com_credencial=False, fluxo_ativo=True)


def test_a_falta_da_pessoa_nao_e_evidencia_e_a_ordem_e_a_do_vocabulario() -> None:
    assert falta_automatizavel([Falta.VOTO_DA_PESSOA, Falta.SOMBRA, Falta.EXECUCAO_REAL, Falta.SOMBRA]) == (
        Falta.EXECUCAO_REAL, Falta.SOMBRA)
    assert falta_automatizavel([Falta.VOTO_DA_PESSOA, Falta.DECISAO_DA_PESSOA]) == ()


def test_receita_do_qa_vira_pedido_pendente_que_pode_fazer_o_efeito() -> None:
    pedido = pedido_do_parecer(RECEITA_QA)
    assert pedido is not None and pedido.estado is EstadoDoPedido.PENDENTE and pedido.motivo is None
    assert pedido.grupo is Grupo.QA
    assert Falta.DECISAO_DA_PESSOA not in pedido.falta


def test_so_pedir_evidencia_com_falta_automatizavel_gera_pedido() -> None:
    for decisao in Decisao:
        if decisao is not Decisao.PEDIR_EVIDENCIA:
            assert pedido_do_parecer(replace(RECEITA_QA, decisao=decisao)) is None, decisao
    assert pedido_do_parecer(replace(RECEITA_QA, falta=(Falta.VOTO_DA_PESSOA,))) is None


def test_as_recusas_nascem_como_registro_na_ordem_do_desenho() -> None:
    def motivo(**kw: object) -> Motivo | None:
        p = pedido_do_parecer(replace(RECEITA_QA, **kw))      # type: ignore[arg-type]
        assert p is not None
        assert (p.estado is EstadoDoPedido.RECUSADA) is (p.motivo is not None)
        return p.motivo

    assert motivo(kind=LivroKind.LICAO) is Motivo.TIPO_SEM_EXECUCAO
    assert motivo(estado=S.DISABLED) is Motivo.DESLIGADO               # devolver à prova antes (item 0)
    assert motivo(vetado=True) is Motivo.VETADO
    assert motivo(toca_sessao=True) is Motivo.SESSAO
    assert motivo(comando=None) is Motivo.SEM_ORIGEM
    assert motivo(comando_com_credencial=True) is Motivo.CREDENCIAL
    assert motivo(app_qa=False) is Motivo.EFEITO_REAL                  # Instagram com efeito: ensaio é a fatia 2
    assert motivo(fluxo_ativo=False) is Motivo.SEM_FLUXO_ATIVO         # a chave da etapa pode mudar
    # Fluxo não precisa de fluxo ativo (ele é o fluxo); leitura em app real roda (sem efeito).
    assert motivo(kind=LivroKind.FLUXO, estado=S.CANDIDATE, fluxo_ativo=False) is None
    leitura = pedido_do_parecer(replace(RECEITA_QA, kind=LivroKind.FLUXO, efeito=False, app_qa=False))
    assert leitura is not None and leitura.grupo is Grupo.LEITURA and leitura.motivo is None


def test_o_despachante_espera_o_ambiente_quieto_o_ritmo_e_o_orcamento() -> None:
    quieto = Ambiente(saudavel=True, execucoes_em_curso=0)
    folego = Folego(g_w=18.0, gasto_w=0.0, na_ultima_hora=0)            # β·G_W = US$ 0,90
    assert folego.orcamento == 0.9
    assert pode_despachar(quieto, folego, custo_estimado=0.07) is None
    assert pode_despachar(Ambiente(saudavel=False, execucoes_em_curso=0), folego, custo_estimado=0.07) is Motivo.AMBIENTE_OCUPADO
    assert pode_despachar(Ambiente(saudavel=True, execucoes_em_curso=1), folego, custo_estimado=0.07) is Motivo.AMBIENTE_OCUPADO
    assert pode_despachar(quieto, replace(folego, na_ultima_hora=4), custo_estimado=0.07) is Motivo.RITMO
    assert pode_despachar(quieto, replace(folego, gasto_w=0.85), custo_estimado=0.07) is Motivo.ORCAMENTO
    # A verba única (P4) soma ao β enquanto vale.
    assert pode_despachar(quieto, replace(folego, gasto_w=0.85, extra_usd=2.5), custo_estimado=0.07) is None
    # Sem gasto da operação, não há orçamento (proporcional, sem teto fixo).
    assert pode_despachar(quieto, Folego(g_w=0.0, gasto_w=0.0, na_ultima_hora=0), custo_estimado=0.07) is Motivo.ORCAMENTO


def test_o_aparelho_e_ocioso_outro_que_o_de_origem_e_sem_conta_real_quando_faz_o_efeito() -> None:
    def ap(i: str, **kw: bool) -> AparelhoCandidato:
        base = {"online": True, "ocioso": True, "tem_o_app": True, "conta_real": False}
        return AparelhoCandidato(i, **{**base, **kw})

    parque = [ap("android-09"), ap("android-10", ocioso=False), ap("android-12", conta_real=True),
              ap("android-13", tem_o_app=False), ap("android-01", conta_real=True)]
    assert escolher_aparelho(Grupo.QA, parque, excluido="android-09") is None    # 12 e 01 têm conta real
    assert escolher_aparelho(Grupo.QA, parque, excluido="android-05") == "android-09"
    # Fatia 1: nem a leitura roda onde há conta real (o despachante não confere a tela da conta).
    assert escolher_aparelho(Grupo.LEITURA, parque, excluido="android-09") is None
    assert escolher_aparelho(Grupo.LEITURA, parque, excluido="android-05") == "android-09"
    assert escolher_aparelho(Grupo.EFEITO_REAL, parque, excluido=None) is None


def test_extra_ate_errado_recusa_o_config_e_o_certo_passa() -> None:
    """A data da verba única (P4) é conferida ao ler o config: errada, o central não sobe calado sem a verba."""
    with pytest.raises(ValidationError):
        ValidacaoCfg(extra_ate="semana que vem")
    assert ValidacaoCfg(extra_ate=" 2026-10-10T00:00:00.000Z ").extra_ate == "2026-10-10T00:00:00.000Z"
    assert ValidacaoCfg().modo == "off" and ValidacaoCfg().extra_ate == ""
