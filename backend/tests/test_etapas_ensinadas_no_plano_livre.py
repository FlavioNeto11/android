"""31.153: as etapas ensinadas como ações conhecidas do app para o planejador livre.

Medido em 06/10: a receita ensinada só era achada pela chave da etapa; o plano livre gerava outro texto e outro hash, e
as 27 receitas ensinadas só serviam aos próprios fluxos, quase todos desligados.

O que estes testes protegem:
* só a etapa ensinada com receita ativa e reproduzida, sem efeito, vira oferta, uma por (app, nome);
* o plano livre que usa o nome da etapa ensinada recebe a etapa-molde do ensino, e a etapa materializada tem o MESMO
  `template_hash` da receita (o executor a acha e roda sem IA); a trilha diz qual receita;
* sem o parâmetro declarado no plano, a etapa fica a do plano, com o motivo;
* a oferta vai ao prompt com nome, app e parâmetros.

Nível de prova: `simulated` (harness com provedor falso; nenhuma IA).
"""
from __future__ import annotations

from typing import Any

import pytest

from app.models import Plan, PlannerInfo, PlanStep, Postcondition
from app.planning import etapas_ensinadas as ens
from app.planning.prompts import planner_user
from app.planning.provider import PlanRequest

from .conftest import Harness
from .test_etapa_pacotes_aceitos import _gravada_com_busca, _proposta

POST = Postcondition(kind="model_judged", value="feito", description="d")


def _passo(key: str, **kw: Any) -> PlanStep:
    return PlanStep(**{"key": key, "title": f"Etapa {key}", "goal": f"fazer {key}", "postcondition": POST, **kw})


def test_so_a_estavel_sem_efeito_e_uma_por_nome() -> None:
    a = ens.EtapaEnsinada("buscar", "qa", _passo("buscar"), receita=1, reproducoes=1, origem="training:s1")
    b = ens.EtapaEnsinada("buscar", "qa", _passo("buscar"), receita=2, reproducoes=3, origem="training:s2")
    c = ens.EtapaEnsinada("buscar", "outro", _passo("buscar"), receita=3, reproducoes=9, origem="training:s3")
    assert ens.escolher([a, b, c], ["qa"]) == [b]
    assert ens.oferecivel(_passo("buscar")) and not ens.oferecivel(_passo("enviar", side_effect=True))
    assert not ens.oferecivel(_passo("buscar_2"))                                  # nome fora do formato
    com_param = ens.EtapaEnsinada("buscar", "qa", _passo("buscar").model_copy(update={"goal": "buscar {termo}"}),
                                  receita=1, reproducoes=1, origem="training:s1")
    assert com_param.parametros == ("termo",)
    passos, trocadas, recusas = ens.trocar([_passo("buscar", title="outro texto")], "qa", [com_param], {})
    assert not trocadas and recusas == ["buscar: o plano não declarou {termo}"] and passos[0].title == "outro texto"
    passos, trocadas, _ = ens.trocar([_passo("buscar", depends_on=[])], "qa", [com_param], {"termo": "wifi"})
    assert trocadas and passos[0].goal == "buscar {termo}"


async def test_o_plano_livre_que_usa_o_nome_ganha_o_hash_da_receita(harness: Harness,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    st, sid = await _gravada_com_busca(harness)
    await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    receita = st.db.one("SELECT * FROM recipes WHERE learned_from_step=? AND step_key='abrir_busca'", (f"training:{sid}",))
    assert receita is not None, "o ensino de teste deveria gerar a receita da etapa abrir_busca"
    st.db.execute("UPDATE recipes SET replay_ok=2 WHERE id=?", (receita["id"],))       # reproduzida: estável
    pedidos: list[PlanRequest] = []
    original = st.runs.provider.plan

    async def planejador(req: PlanRequest) -> Any:
        pedidos.append(req)
        _, uso = await original(req)
        return Plan(summary="buscar", app_id="qa-messenger", planner=PlannerInfo(provider="t", model="t", simulated=True),
                    steps=[_passo("abrir_app"), _passo("abrir_busca", title="Abrir a pesquisa", depends_on=["abrir_app"])]
                    ), uso
    monkeypatch.setattr(st.runs.provider, "plan", planejador)
    run = harness.run(["android-01"], mode="plan", command="procure no QA Messenger")
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed"), timeout=30)
    (req,) = pedidos
    assert [e.nome for e in req.etapas_ensinadas] == ["abrir_busca"]                # a sem receita não vai
    assert "- nome: abrir_busca | app: qa-messenger" in planner_user(req, 10)
    etapa = st.db.one("SELECT * FROM steps WHERE run_id=? AND key='abrir_busca'", (run.id,))
    assert etapa["template_hash"] == receita["step_hash"] and etapa["title"] == "Etapa abrir_busca"
    assert etapa["depends_on"] and "abrir_app" in etapa["depends_on"]
    assert st.db.scalar("SELECT COUNT(*) FROM events WHERE run_id=? AND message LIKE ?",
                        (run.id, f"%abrir_busca pela etapa ensinada em training:{sid} (receita {receita['id']}%")) == 1
