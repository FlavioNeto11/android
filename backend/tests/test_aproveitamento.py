"""Aproveitamento das receitas por fluxo (`taskqueue/aproveitamento.py`), com execuções de verdade do harness.

Cobertura (`GET /api/flows/cobertura`) só dizia quantas etapas TÊM receita. Aqui se prova o que elas fizeram na
janela: etapas elegíveis, reproduzidas, que voltaram para a IA, sem cobertura × receita de outra chave, divergência
de um aparelho × de todos, e chamadas de IA evitadas estimadas pela mediana da mesma etapa feita pela IA.
"""
from __future__ import annotations

from typing import Any

from app.social.capacidades import cobertura_dos_fluxos
from app.taskqueue.aproveitamento import aproveitamento

from .conftest import Harness

PKG = "com.pocqa.messenger"


def _grupo(db: Any) -> dict[str, Any]:
    fluxos = aproveitamento(db)["fluxos"]
    assert len(fluxos) == 1, fluxos
    return fluxos[0]


async def test_aproveitamento_por_fluxo_separa_origem_cobertura_e_divergencia(harness: Harness) -> None:
    harness.cfg.file.ai.recipes = "replay"
    harness.cfg.file.ai.flows = True
    db = harness.state.db                                                   # type: ignore[union-attr]

    r1 = await harness.wait_run(harness.run(["android-01"]).id)             # aprende
    assert r1.status == "completed"
    n_receitas = db.scalar("SELECT COUNT(*) FROM recipes WHERE status='active'")
    etapas_r1 = db.scalar("SELECT COUNT(*) FROM steps WHERE run_id=?", (r1.id,))
    g = _grupo(db)
    assert g["package"] == PKG and g["flow_id"] == db.scalar("SELECT id FROM flows")   # a execução-fonte é do fluxo
    assert g["etapas"] == g["elegiveis"] == g["so_ia"] == g["sem_cobertura"] == etapas_r1
    assert g["receitas_aprendidas"] == n_receitas >= 3
    assert g["por_receita"] == 0 and g["chamadas_evitadas_estimadas"] == 0
    assert g["chamadas_ia"]["decide"] > 0

    r2 = await harness.wait_run(harness.run(["android-02", "android-03"]).id)   # repete nos dois
    assert r2.status == "completed"
    g = _grupo(db)
    por_receita = db.scalar("SELECT COUNT(*) FROM steps WHERE run_id=? AND driven_by='recipe'", (r2.id,))
    assert g["por_receita"] == por_receita == 2 * n_receitas
    # as etapas só de leitura nunca viram receita: continuam "sem cobertura" em qualquer aparelho
    assert g["sem_cobertura"] == etapas_r1 + 2 * (etapas_r1 - n_receitas)
    assert g["outra_chave_ou_quarentena"] == 0
    # cada etapa reproduzida poupou a mediana de decisões que a IA gastou nela em r1 (≥ 1)
    assert g["chamadas_evitadas_estimadas"] >= por_receita and g["etapas_por_receita_sem_base"] == 0
    versoes = {v["app_version"]: v for v in g["por_versao"]}
    assert set(versoes) == {"1.0(1)"}
    assert versoes["1.0(1)"]["receitas_ativas"] == n_receitas and versoes["1.0(1)"]["reproducoes_ok"] == por_receita

    # divergência só num aparelho, com a mesma etapa limpa no outro da MESMA execução → do aparelho
    alvo = "SELECT id FROM steps WHERE run_id=? AND instance_id=? AND key='open_conversation'"
    db.execute("UPDATE steps SET driven_by='recipe+ai' WHERE id=?", (db.scalar(alvo, (r2.id, "android-03")),))
    g = _grupo(db)
    assert g["divergencias"] == {"so_neste_aparelho": 1, "em_todos_os_aparelhos": 0, "indeterminado": 0}
    assert g["retorno_ia"] == g["receita_mais_ia"] == 1
    # divergiu nos dois → da receita/tela
    db.execute("UPDATE steps SET driven_by='recipe+ai' WHERE id=?", (db.scalar(alvo, (r2.id, "android-02")),))
    g = _grupo(db)
    assert g["divergencias"] == {"so_neste_aparelho": 0, "em_todos_os_aparelhos": 2, "indeterminado": 0}

    # app de OUTRA versão num aparelho: a receita existia, mas de outra chave — não é "sem cobertura"
    rt = harness.state.devices.devices["android-02"]                        # type: ignore[union-attr]
    harness.fakes["android-02"].version = "2.0(7)"
    rt.app_versions.clear()
    r3 = await harness.wait_run(harness.run(["android-02"]).id)
    assert r3.status == "completed"
    g = _grupo(db)
    assert g["outra_chave_ou_quarentena"] == n_receitas
    assert {v["app_version"] for v in g["por_versao"]} == {"1.0(1)", "2.0(7)"}

    total = aproveitamento(db)["totais"]
    assert total["etapas"] == g["etapas"] and total["por_receita"] == g["por_receita"]


async def test_cobertura_dos_fluxos_traz_o_aproveitamento_do_fluxo(harness: Harness) -> None:
    harness.cfg.file.ai.recipes = "replay"
    harness.cfg.file.ai.flows = True
    assert (await harness.wait_run(harness.run(["android-01"]).id)).status == "completed"
    assert (await harness.wait_run(harness.run(["android-02"]).id)).status == "completed"
    linhas = cobertura_dos_fluxos(harness.state)
    assert len(linhas) == 1
    uso = linhas[0]["aproveitamento"]
    assert len(uso) == 1 and uso[0]["flow_id"] == linhas[0]["flow_id"] and uso[0]["por_receita"] >= 3
    # a cobertura continua a mesma de antes: o campo novo é só acréscimo
    assert {"steps_total", "steps_with_recipe", "ai_cost", "estimated_usd"} <= set(linhas[0])


async def test_janela_sem_etapas_devolve_vazio_sem_erro(harness: Harness) -> None:
    res = aproveitamento(harness.state.db)                                  # type: ignore[union-attr]
    assert res["fluxos"] == [] and res["totais"]["etapas"] == 0 and res["janela_dias"] == 7
