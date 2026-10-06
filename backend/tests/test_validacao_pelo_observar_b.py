"""30.34 (sim da orquestradora, 06/10): na classe B, o parecer `observar` cuja falta uma execução produz também gera
pedido de prova, SÓ no app de prova.

Desde o 30.73 o parecer B não pede voto nem decisão da pessoa e fica em `observar` com `execucao_real`; só
`pedir_evidencia` gerava pedido, e o fluxo B nunca ganhava a 2ª execução (re-medida do 30.72, 06/10). O efeito em app
real segue fora, sem nem registro (ninguém pediu evidência); A e C seguem como antes; o teto, a verba e o ritmo são os
de sempre. O que nasce por aqui é contado à parte (`/api/health`, `features.validacao_pelo_observar_b`).

Nível de prova: `simulated` (regra pura; banco de teste; harness sem IA).
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.modules.learning.application.validacao import ServicoDeValidacao
from app.modules.learning.domain.curador import Decisao, Falta, Parecer
from app.modules.learning.domain.politica_de_risco import ClasseDeRisco, Classificacao, Razao
from app.modules.learning.domain.validacao import EstadoDoPedido, Grupo, Motivo, pedido_do_parecer
from app.modules.learning.infrastructure.contador_pelo_observar import ContadorPeloObservar

from .conftest import Harness
from .fake_skills import banco as banco_migrado
from .test_learning_prova_validacao import _fluxo, mundo  # noqa: F401  (a fixture `mundo` é a armação da prova)
from .test_learning_validacao import RECEITA_QA
from .test_learning_validacao_sql import B

#: O parecer B de depois do 30.73, como o central gravou em 05/10 19:05Z: `observar`, faltando a execução real.
OBSERVA_B = replace(RECEITA_QA, decisao=Decisao.OBSERVAR, falta=(Falta.EXECUCAO_REAL,), classe=ClasseDeRisco.B)
OBSERVA = Parecer(decisao=Decisao.OBSERVAR, evidencias_citadas=("item",), falta=(Falta.EXECUCAO_REAL,))


# ------------------------------------------------------------------ a regra (pura)
def test_observar_b_no_app_de_prova_gera_pedido_pendente_marcado() -> None:
    pedido = pedido_do_parecer(OBSERVA_B)
    assert pedido is not None and pedido.estado is EstadoDoPedido.PENDENTE and pedido.grupo is Grupo.QA
    assert pedido.pelo_observar and pedido.falta == (Falta.EXECUCAO_REAL,)


def test_observar_b_com_efeito_em_app_real_nao_gera_nada() -> None:
    assert pedido_do_parecer(replace(OBSERVA_B, app_qa=False)) is None       # nem registro: efeito_real fica fora


@pytest.mark.parametrize("classe", [ClasseDeRisco.A, ClasseDeRisco.C, None])
def test_observar_fora_da_classe_b_segue_sem_pedido(classe: ClasseDeRisco | None) -> None:
    assert pedido_do_parecer(replace(OBSERVA_B, classe=classe)) is None


def test_observar_b_que_so_espera_a_pessoa_nao_gera_pedido() -> None:
    assert pedido_do_parecer(replace(OBSERVA_B, falta=(Falta.VOTO_DA_PESSOA, Falta.DECISAO_DA_PESSOA))) is None
    assert pedido_do_parecer(replace(OBSERVA_B, falta=())) is None


def test_observar_b_recusado_nasce_como_registro_do_porque() -> None:
    pedido = pedido_do_parecer(replace(OBSERVA_B, caminho=False))
    assert pedido is not None and pedido.estado is EstadoDoPedido.RECUSADA and pedido.motivo is Motivo.SEM_CAMINHO
    assert pedido.pelo_observar


def test_pedir_evidencia_segue_igual_e_nao_e_contado_como_observar() -> None:
    pedido = pedido_do_parecer(replace(RECEITA_QA, classe=ClasseDeRisco.B))
    assert pedido is not None and pedido.estado is EstadoDoPedido.PENDENTE and not pedido.pelo_observar


# ------------------------------------------------------------------ o serviço e o contador
def test_o_observar_b_vira_pedido_no_registro_e_conta(mundo) -> None:  # noqa: F811  # type: ignore[no-untyped-def]
    db, servico, _parque, _relogio, _ajustes = mundo
    contados: list[str] = []
    servico._contar_pelo_observar = contados.append                                # noqa: SLF001
    fluxo = _fluxo(db)
    pid = servico.ao_parecer(fluxo, "lr-1", OBSERVA, B)
    assert pid is not None and contados == ["pendente"]
    linha = db.one("SELECT estado, grupo, falta FROM learning_validations WHERE id=?", (pid,))
    assert (linha["estado"], linha["grupo"]) == ("pendente", "qa") and "execucao_real" in linha["falta"]
    assert servico.ao_parecer(_fluxo(db, "f-qa2"), "lr-2", OBSERVA,
                              Classificacao(ClasseDeRisco.C, (Razao.COMMIT_SEM_CATALOGO,), None)) is None
    assert servico.ao_parecer(fluxo, "lr-3", OBSERVA, B) is None       # já há pedido vivo do item: não conta
    assert contados == ["pendente"]


def test_o_contador_persiste_e_resume(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = banco_migrado(tmp_path, "contador.sqlite3")
    c = ContadorPeloObservar(db)
    assert c.resumo() == {"total": 0, "desde": None, "ultima": None, "por_estado": {}}
    c.registrar("pendente")
    c.registrar("sem_caminho")
    c.registrar("pendente")
    r = ContadorPeloObservar(db).resumo()
    assert r["total"] == 3 and r["por_estado"] == {"pendente": 2, "sem_caminho": 1} and r["desde"] and r["ultima"]


def test_a_saude_mostra_o_contador_e_o_servico_esta_ligado(harness: Harness) -> None:
    st = harness.state
    assert st.health().features["validacao_pelo_observar_b"]["total"] == 0
    validacao = st.learning.extensao(ServicoDeValidacao)
    assert validacao is not None and validacao._contar_pelo_observar is not None          # noqa: SLF001
    validacao._contar_pelo_observar("pendente")                                           # noqa: SLF001
    assert st.health().features["validacao_pelo_observar_b"]["por_estado"] == {"pendente": 1}
