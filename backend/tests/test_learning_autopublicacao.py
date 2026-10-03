"""30.34: a autopublicação do fluxo de classe B (emenda de 03/10 à D1 do ADR-054), a regra pura e o balanço da sombra.
Prova `simulated`: fatos montados à mão, sem banco nem IA."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.learning.domain.autopublicacao import (Acao, BalancoDaSombra, CasoDaSombra, Desfecho, EventoDoCaso,
                                                        FatosDaAutopublicacao, ModoDaAutopublicacao, MotivoDeFora,
                                                        ParecerParaAutopublicar, Regressao, acao, avaliar, balanco,
                                                        desfecho)
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.curador import Confianca, Decisao
from app.modules.learning.domain.politica_de_risco import ClasseDeRisco
from app.modules.learning.domain.promocao import Contadores

AGORA = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
PARECER = ParecerParaAutopublicar(decisao=Decisao.APROVAR, confianca=Confianca.ALTA, simulado=False,
                                  desatualizado=False, decidido=False)
PRONTO = FatosDaAutopublicacao(kind="fluxo", estado=SkillState.VALIDATED, requer_dono=True, reaprendido=False,
                               classe=ClasseDeRisco.B, parecer=PARECER,
                               contadores=Contadores(a_favor=3, contra=0, execucoes=2, aparelhos=2))


def test_o_fluxo_b_com_as_tres_condicoes_publicaria() -> None:
    assert avaliar(PRONTO).publicaria


@pytest.mark.parametrize(("mudanca", "motivo"), [
    ({"kind": "receita"}, MotivoDeFora.NAO_E_FLUXO),
    ({"estado": SkillState.CANDIDATE}, MotivoDeFora.NAO_ESPERA_O_DONO),
    ({"estado": SkillState.PUBLISHED}, MotivoDeFora.NAO_ESPERA_O_DONO),
    ({"requer_dono": False}, MotivoDeFora.NAO_ESPERA_O_DONO),        # sem efeito: a D1 já publica sozinho
    ({"reaprendido": True}, MotivoDeFora.REAPRENDIDO),                # 30.23: volta ao dono, mesmo sendo B
    ({"classe": ClasseDeRisco.A}, MotivoDeFora.CLASSE_NAO_B),
    ({"classe": ClasseDeRisco.C}, MotivoDeFora.CLASSE_NAO_B),         # C segue item a item
    ({"classe": None}, MotivoDeFora.CLASSE_NAO_B),
    ({"parecer": None}, MotivoDeFora.SEM_PARECER),
    ({"parecer": replace(PARECER, decisao=None)}, MotivoDeFora.SEM_PARECER),
    ({"parecer": replace(PARECER, simulado=True)}, MotivoDeFora.PARECER_SIMULADO),
    ({"parecer": replace(PARECER, desatualizado=True)}, MotivoDeFora.PARECER_DESATUALIZADO),
    ({"parecer": replace(PARECER, decidido=True)}, MotivoDeFora.PARECER_JA_DECIDIDO),
    ({"parecer": replace(PARECER, decisao=Decisao.PEDIR_EVIDENCIA)}, MotivoDeFora.NAO_SUGERE_APROVAR),
    ({"parecer": replace(PARECER, decisao=Decisao.MANTER)}, MotivoDeFora.NAO_SUGERE_APROVAR),
    ({"parecer": replace(PARECER, confianca=Confianca.MEDIA)}, MotivoDeFora.CONFIANCA_NAO_ALTA),
    ({"parecer": replace(PARECER, confianca=None)}, MotivoDeFora.CONFIANCA_NAO_ALTA),
    ({"contadores": Contadores(a_favor=5, contra=0, execucoes=1, aparelhos=2)}, MotivoDeFora.POUCAS_EXECUCOES),
    ({"contadores": Contadores(a_favor=5, contra=0, execucoes=3, aparelhos=1)}, MotivoDeFora.POUCOS_APARELHOS),
    ({"contadores": Contadores(a_favor=5, contra=1, execucoes=3, aparelhos=2)}, MotivoDeFora.EVIDENCIA_CONTRA),
])
def test_cada_condicao_recusa_sozinha(mudanca: dict[str, object], motivo: MotivoDeFora) -> None:
    a = avaliar(replace(PRONTO, **mudanca))  # type: ignore[arg-type]
    assert not a.publicaria and a.motivos == (motivo,)


def test_os_motivos_vem_todos_juntos_para_o_relatorio() -> None:
    a = avaliar(replace(PRONTO, classe=ClasseDeRisco.C, parecer=replace(PARECER, confianca=Confianca.MEDIA),
                        contadores=Contadores(a_favor=1, contra=0, execucoes=1, aparelhos=1)))
    assert a.motivos == (MotivoDeFora.CLASSE_NAO_B, MotivoDeFora.CONFIANCA_NAO_ALTA, MotivoDeFora.POUCAS_EXECUCOES,
                         MotivoDeFora.POUCOS_APARELHOS)


# ------------------------------------------------------------------ a sombra
CASO = CasoDaSombra("fluxo:x", AGORA)


def test_o_caso_fica_aberto_na_janela_e_fecha_limpo_depois_dela() -> None:
    assert desfecho(CASO, [], AGORA + timedelta(days=6, hours=23)) is Desfecho.ABERTO
    assert desfecho(CASO, [], AGORA + timedelta(days=7)) is Desfecho.LIMPO


@pytest.mark.parametrize("tipo", list(Regressao))
def test_regressao_dentro_da_janela_fecha_o_caso_na_hora(tipo: Regressao) -> None:
    evento = EventoDoCaso(tipo, AGORA + timedelta(days=2))
    assert desfecho(CASO, [evento], AGORA + timedelta(days=3)) is Desfecho.REGREDIU


def test_o_que_veio_antes_da_marca_ou_depois_da_janela_nao_e_regressao_do_caso() -> None:
    antes = EventoDoCaso(Regressao.EVIDENCIA_CONTRA, AGORA - timedelta(hours=1))
    depois = EventoDoCaso(Regressao.DESLIGADO, AGORA + timedelta(days=8))
    assert desfecho(CASO, [antes, depois], AGORA + timedelta(days=9)) is Desfecho.LIMPO


@pytest.mark.parametrize(("limpos", "regrediram", "abertos", "libera"), [
    (29, 0, 5, False),        # 29 fechados: falta um, e o aberto não conta a favor
    (30, 0, 0, True),
    (27, 3, 0, True),         # 90 % exatos
    (26, 4, 0, False),        # 86,7 %
    (40, 5, 10, False),       # 40/45 = 88,9 %: recusa, e os 10 abertos não ajudam
])
def test_o_balanco_libera_so_com_30_fechados_e_90_por_cento_limpos(limpos: int, regrediram: int, abertos: int,
                                                                  libera: bool) -> None:
    d = [Desfecho.LIMPO] * limpos + [Desfecho.REGREDIU] * regrediram + [Desfecho.ABERTO] * abertos
    b = balanco(d)
    assert (b.casos, b.abertos, b.limpos, b.regrediram) == (len(d), abertos, limpos, regrediram)
    assert b.taxa_sem_regressao == round(limpos / (limpos + regrediram), 3)
    assert b.libera is libera


def test_sem_caso_fechado_a_taxa_e_ausente_e_nao_libera() -> None:
    b = balanco([Desfecho.ABERTO] * 50)
    assert b.taxa_sem_regressao is None and not b.libera


LIBERADO = BalancoDaSombra(casos=30, abertos=0, limpos=30, regrediram=0, taxa_sem_regressao=1.0, libera=True)
PRESO = BalancoDaSombra(casos=3, abertos=3, limpos=0, regrediram=0, taxa_sem_regressao=None, libera=False)


def test_o_modo_e_do_config_e_o_balanco_e_medido() -> None:
    sim, nao = avaliar(PRONTO), avaliar(replace(PRONTO, classe=ClasseDeRisco.C))
    assert acao(ModoDaAutopublicacao.OFF, sim, LIBERADO) is Acao.NADA
    assert acao(ModoDaAutopublicacao.SHADOW, sim, LIBERADO) is Acao.REGISTRAR      # sombra nunca publica
    assert acao(ModoDaAutopublicacao.ON, sim, PRESO) is Acao.REGISTRAR             # `on` sem o balanço: só registra
    assert acao(ModoDaAutopublicacao.ON, sim, LIBERADO) is Acao.PUBLICAR
    assert acao(ModoDaAutopublicacao.ON, nao, LIBERADO) is Acao.NADA
