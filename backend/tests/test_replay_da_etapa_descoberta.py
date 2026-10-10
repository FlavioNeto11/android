"""31.311 (ADR-084): a etapa que a IA DESCOBRIU volta por receita, de ponta a ponta, sem API paga.

A ressalva que ficou do 31.273: os testes provavam o planejamento (a receita semeada é oferecida pelo nome) e a execução da
etapa exploratória, mas nunca o ciclo inteiro pelo executor de verdade. Aqui, com o aparelho falso do QA Messenger e um
provedor roteirizado (nenhuma chamada paga):

1. 1ª execução: a IA conduz a etapa exploratória (só leitura) e a receita nasce `candidate`;
2. 2ª execução: a IA conduz de novo, a prova sombra concorda e a receita vira `active` (etapa sem efeito: uma concordância);
3. o leitor das descobertas passa a oferecer a etapa pelo nome, e o molde refeito SÓ da chave tem o mesmo hash da etapa que a
   aprendeu (K-111), mesmo com outro texto de pedido;
4. 3ª execução, com o molde: a etapa é conduzida pela receita (`driven_by = recipe`), com 0 decisões da IA nela.

Prova `simulated`. `real`: `not_run` (depende do sim do dono, P-043, e do Outlook).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.models import Plan, PlannerInfo, PlanStep
from app.planning import exploracao as ex
from app.planning.provider import Decision, PlanRequest, Usage, Verdict
from app.planning.simulated_provider import QA_PACKAGE, SimulatedProvider
from app.taskqueue.recipes import hash_generico_da_etapa, step_template_hash

from .conftest import CountingProvider, Harness

PEDIDO_1 = "ver o perfil"
PEDIDO_2 = "ver o perfil do aplicativo com calma"              # outro texto, a mesma chave: o hash não pode mudar


class Explorador(SimulatedProvider):
    """Planeja o plano dado e conduz a etapa exploratória: abre o app, toca em "Perfil" uma vez e declara pronta."""

    def __init__(self) -> None:
        super().__init__()
        self.plano: Plan | None = None

    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]:
        assert self.plano is not None
        return self.plano.model_copy(deep=True), Usage(calls=1, role="plan", model="roteiro")

    async def decide(self, req: Any) -> tuple[Decision, Usage]:
        if not req.ctx.step_key.startswith("explorar_"):
            return await super().decide(req)
        if req.screen.package != QA_PACKAGE:
            return Decision(tool="open_app", args={"rationale": "[roteiro] abrir o app", "package": None}), Usage()
        if not any("tap" in h for h in req.history):
            botao = next(e for e in req.screen.tree.elements if e.resource_id.endswith("btn_profile"))
            return Decision(tool="tap", args={"rationale": "[roteiro] olhar o perfil", "element_id": botao.id,
                                              "x": None, "y": None, "is_commit_action": False}), Usage()
        return Decision(tool="step_done", args={"rationale": "[roteiro] visto", "evidence": "tela do app aberta",
                                                "delivery_level": None}), Usage()

    async def verify(self, req: Any) -> tuple[Verdict, Usage]:
        if req.ctx.step_key.startswith("explorar_"):
            return Verdict(satisfied="yes", evidence="[roteiro] a tela mostra o que a etapa diz"), Usage()
        return await super().verify(req)


def _plano(passo: PlanStep) -> Plan:
    return Plan(summary="explorar", app_id="qa-messenger", steps=[passo],
                planner=PlannerInfo(provider="roteiro", model="t", simulated=True))


def _passo(pedido: str) -> PlanStep:
    return ex.passo_da_exploracao(pedido, ex.classificar(pedido), app_id=None, nome_do_app="QA Messenger")


def _receita(h: Harness, chave: str) -> Any:
    return h.state.db.one("SELECT * FROM recipes WHERE step_key=? AND status <> 'superseded' ORDER BY id DESC",  # type: ignore[union-attr]
                          (chave,))


def _etapa(h: Harness, run_id: str, chave: str) -> Any:
    return h.state.db.one("SELECT * FROM steps WHERE run_id=? AND key=?", (run_id, chave))  # type: ignore[union-attr]


async def test_a_etapa_descoberta_aprende_prova_e_volta_por_receita_sem_ia(tmp_path: Path) -> None:
    h = Harness(tmp_path, 3)
    provedor = Explorador()
    h.ai = CountingProvider(provedor)
    await h.boot()
    try:
        h.pular_o_tempo()
        h.cfg.file.ai.recipes = "replay"
        h.cfg.file.ai.recipes_promote_after = 1
        st = h.state
        assert st is not None
        passo = _passo(PEDIDO_1)
        chave = passo.key
        assert chave == "explorar_ver_perfil" and passo.exploratoria and not passo.side_effect

        # 1) a IA conduz; a receita nasce candidata
        provedor.plano = _plano(passo)
        r1 = await h.wait_run(h.run(["android-01"], command="veja o perfil no QA Messenger").id)
        assert r1.status == "completed", r1.status_detail
        e1 = _etapa(h, r1.id, chave)
        assert e1["exploratoria"] == 1 and e1["status"] == "succeeded" and e1["driven_by"] == "ai"
        assert h.ai.count("decide", step=chave) >= 2
        candidata = _receita(h, chave)
        assert candidata is not None and candidata["status"] == "candidate"
        assert st.runs.flows.etapas_descobertas() == []                  # candidata ainda não é oferecida

        # 2) a IA conduz de novo (outro texto de pedido, mesma chave): a sombra concorda e a receita fica ativa
        provedor.plano = _plano(_passo(PEDIDO_2))
        r2 = await h.wait_run(h.run(["android-02"], command="veja o perfil do app, com calma").id)
        assert r2.status == "completed", r2.status_detail
        assert _etapa(h, r2.id, chave)["driven_by"] == "ai"
        ativa = _receita(h, chave)
        assert ativa is not None and ativa["status"] == "active" and ativa["shadow_agree"] >= 1
        assert ativa["step_hash"] == candidata["step_hash"]              # o texto do pedido não entra no hash (K-111)

        # 3) o leitor oferece a etapa pelo nome; o molde da chave tem o hash da etapa que a aprendeu
        (oferecida,) = st.runs.flows.etapas_descobertas()
        assert (oferecida.nome, oferecida.descoberta, oferecida.receita) == (chave, True, int(ativa["id"]))
        molde = ex.molde_da_exploracao(passo)
        assert molde is not None
        assert step_template_hash(molde) == ativa["step_hash"] or hash_generico_da_etapa(molde) == ativa["step_hash"]

        # 4) a etapa do molde (como o planejador a devolveria ao receber o bloco) entra pela receita, sem decisão da IA
        provedor.plano = _plano(molde)
        h.ai.calls.clear()
        r3 = await h.wait_run(h.run(["android-03"], command="veja o perfil no QA Messenger").id)
        assert r3.status == "completed", r3.status_detail
        e3 = _etapa(h, r3.id, chave)
        assert e3["status"] == "succeeded" and e3["driven_by"] == "recipe"
        assert h.ai.count("decide", step=chave) == 0
        db = st.db
        assert db.scalar("SELECT COUNT(*) FROM actions a JOIN attempts t ON t.id=a.attempt_id JOIN steps s ON"
                         " s.id=t.step_id WHERE s.run_id=? AND s.key=? AND a.source='recipe'", (r3.id, chave)) >= 1
        assert db.scalar("SELECT replay_ok FROM recipes WHERE id=?", (int(ativa["id"]),)) >= 1
    finally:
        if h.state is not None:
            await h.state.stop()
