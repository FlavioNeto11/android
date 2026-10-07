"""31.222: a escolha por semelhança não derruba a ação final do comando.

Achado do 31.219: a escolha por semelhança (31.151, executa direto pelo 31.210) troca o plano INTEIRO pelo do fluxo e
só confere se os valores estão no comando. Um fluxo de leitura escolhido para um comando que também comenta deixaria o
comentário de fora.

O que estes testes protegem:
* `acoes_finais_fora`: a etapa do plano livre com efeito ou trava de commit que o fluxo não cobre (pela ação do
  catálogo, senão pela chave) sai na lista, sem repetir; a que o fluxo cobre não sai; a de leitura não conta;
* no planejamento: o fluxo de leitura escolhido para um comando com ação final é recusado, fica o plano livre com a
  etapa de efeito, `runs.flow_id` e `flows.uses` não andam, e a trilha diz o motivo (31.222);
* sem ação final no plano livre, a escolha segue valendo (o 31.151 e o 31.210 não mudam).

Nível de prova: `simulated` (harness com provedor falso; nenhuma IA).
"""
from __future__ import annotations

from typing import Any

import pytest

from app.models import Plan, PlanStep, Postcondition
from app.planning import habilidades as hab

from .conftest import Harness
from .test_fluxo_por_semelhanca import _fluxo, _trilha

COMANDO = "procure wifi nas configurações e mande o resultado"


def _p(nome: str, *, efeito: bool = False, cap: str | None = None, trava: list[str] | None = None) -> PlanStep:
    return PlanStep(key=nome, title=nome, goal=nome, side_effect=efeito, capability=cap, commit_guard=trava or [],
                    postcondition=Postcondition(kind="text_visible", value="x", description="d"))


def test_as_acoes_finais_fora_do_fluxo() -> None:
    leitura = [_p("abrir_perfil", cap="OPEN_PROFILE"), _p("abrir_post", cap="OPEN_POST")]
    livre = [*leitura, _p("comentar", efeito=True, cap="CREATE_COMMENT"),
             _p("comentar_2", efeito=True, cap="CREATE_COMMENT")]
    assert hab.acoes_finais_fora(livre, leitura) == ["CREATE_COMMENT"]
    assert hab.acoes_finais_fora(livre, livre) == []
    assert hab.acoes_finais_fora(leitura, leitura[:1]) == []                       # leitura a mais não conta
    assert hab.acoes_finais_fora([_p("mandar", trava=["algo"])], leitura) == ["mandar"]    # só a trava já conta


async def _planejar(harness: Harness, monkeypatch: pytest.MonkeyPatch, ref: str, *, com_efeito: bool) -> str:
    st = harness.state
    original = st.runs.provider.plan

    async def planejador(req: Any) -> Any:
        plano, uso = await original(req)
        assert isinstance(plano, Plan)
        plano.steps = [_p("pesquisar"), *([_p("mandar_resultado", efeito=True)] if com_efeito else [])]
        plano.escolha_por_semelhanca = {"ref": ref, "valores": {"termo": "wifi"}}
        return plano, uso
    monkeypatch.setattr(st.runs.provider, "plan", planejador)
    run = harness.run(["android-01"], mode="plan", command=COMANDO)
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed"), timeout=30)
    return run.id


async def test_o_fluxo_de_leitura_nao_troca_o_plano_com_acao_final(harness: Harness,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    ref = _fluxo(st)
    run_id = await _planejar(harness, monkeypatch, ref, com_efeito=True)
    row = st.repo.run_row(run_id)
    plano = Plan.model_validate_json(row["plan"])
    assert not plano.planner.model.startswith("fluxo:") and "mandar_resultado" in [s.key for s in plano.steps]
    fid = st.db.scalar("SELECT id FROM flows WHERE ref_publico=?", (ref,))
    assert row["flow_id"] is None and st.db.scalar("SELECT uses FROM flows WHERE id=?", (fid,)) == 0
    assert _trilha(st, run_id, "recusada (31.222): o fluxo não cobre a ação final mandar_resultado") == 1


async def test_sem_acao_final_a_escolha_vale(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    ref = _fluxo(st)
    run_id = await _planejar(harness, monkeypatch, ref, com_efeito=False)
    assert Plan.model_validate_json(st.repo.run_row(run_id)["plan"]).planner.model.startswith("fluxo:")
    assert _trilha(st, run_id, "31.222") == 0
