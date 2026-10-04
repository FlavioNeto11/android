"""Item 31.34: a prévia da GRADE cede a vez à leitura da árvore no mesmo aparelho.

Medido em 04/10 (android-04, `ab_3128.py`, 40 leituras de /hierarchy por condição): com a grade puxando a miniatura, o
p95 da leitura da árvore ia de ~0,3 s a ~2 s (a mediana não mudava). O laço de captura disputava o ADB com a leitura.
Agora `hierarchy()` marca o aparelho por `capture_yield_to_tree_s` e a volta da grade pula a captura nesse intervalo;
o foco e o pedido explícito não cedem; 0 desliga.

Nível de prova: `simulated` (harness na porta 5640, aparelho falso).
"""
from __future__ import annotations

from app.metricas import metricas

from .conftest import Harness
from .test_previa_sob_demanda import _caps, _preparar


async def test_grade_pula_a_captura_enquanto_a_arvore_e_lida_e_volta_depois(harness: Harness) -> None:
    devs, _ = await _preparar(harness)
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    devs.registrar_interesse("aba", ["android-01"], None, 20)       # grade visível, sem foco
    await devs.hierarchy(rt)
    antes = _caps(fake)
    assert (await devs._volta_da_previa(rt))[0] == "arvore_em_curso"
    assert _caps(fake) == antes and metricas.valor("captura.evitada", motivo="arvore_em_curso") >= 1
    # O pedido explícito (interesse novo, entrada manual) não cede.
    assert (await devs._volta_da_previa(rt, pedido=True))[0] == "capturada" and _caps(fake) == antes + 1
    # Acabou a janela: a grade volta a capturar sozinha.
    rt.arvore_ate = 0.0
    rt.frame = None
    assert (await devs._volta_da_previa(rt))[0] == "capturada"
    devs.soltar_interesse("aba")


async def test_zero_desliga_e_sem_espectador_segue_sem_interesse(harness: Harness) -> None:
    devs, _ = await _preparar(harness)
    rt = devs.get("android-01")
    assert (await devs._volta_da_previa(rt))[0] == "sem_interesse"
    await devs.hierarchy(rt)
    assert (await devs._volta_da_previa(rt))[0] == "sem_interesse"   # sem espectador, nada a ceder
    harness.state.settings.update({"capture_yield_to_tree_s": 0})   # type: ignore[union-attr]
    rt.arvore_ate = 0.0
    await devs.hierarchy(rt)
    assert rt.arvore_ate == 0.0


async def test_leitura_mais_longa_que_a_janela_continua_protegida_e_a_janela_conta_do_fim(harness: Harness) -> None:
    devs, _ = await _preparar(harness)
    rt = devs.get("android-01")
    devs.registrar_interesse("aba", ["android-01"], None, 20)
    rt.arvores_em_curso = 1                    # uma leitura longa em curso, com a janela do início já vencida
    rt.arvore_ate = 0.0
    assert (await devs._volta_da_previa(rt))[0] == "arvore_em_curso"
    rt.arvores_em_curso = 0
    await devs.hierarchy(rt)                   # ao terminar, a janela recomeça do FIM e o contador volta a zero
    assert rt.arvores_em_curso == 0 and rt.arvore_ate > 0
    assert (await devs._volta_da_previa(rt))[0] == "arvore_em_curso"
    devs.soltar_interesse("aba")
