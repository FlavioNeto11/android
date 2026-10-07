"""31.165: no empate de valor entre um parâmetro do objetivo e um dado da persona, a identidade da etapa
(`recipes.para_hash`) fica com o marcador da persona, como o 31.87 faz na proposta do ensino.

Visto na onda 1 de 06/10 (execução r-20261006194323-240d40): a receita ensinada 221 está em "perfil de
{conta_instagram_usuario} aberto" (8a9a214a…), porque o alvo do ensino era a própria persona. A execução planejada para o
mesmo alvo calculava "perfil de {perfil} aberto" (a25a2299…): o parâmetro "@x" é mais longo que a conta "x" e ganhava a
troca. Mesma etapa, dois nomes, e a receita nunca era consultada. Medido contra o banco real, só leitura: com a regra
nova, o hash da etapa da onda 1 é o da 221.

O que estes testes protegem:
* o empate (com e sem o @ da frente, sem caixa) dá o marcador da persona, no literal e no `{perfil}` já escrito;
* sem `persona`, ou com valores diferentes, a identidade é a de antes (as receitas para outro alvo seguem casando);
* num `plan` simulado, a etapa ganha o hash do ensino e a loja de receitas acha a receita gravada nele.

Nível de prova: `simulated` (planejador por regras, aparelho falso, valores sintéticos, banco de teste).
"""
from __future__ import annotations

from typing import Any

from app.models import PlanStep, Postcondition, ProfileCreate
from app.taskqueue.recipes import para_hash, step_template_hash

from .conftest import Harness

HANDLE = "tadeu.teste"                    # sintético
PERSONA = {"conta_instagram_usuario": HANDLE}


def _etapa(valor: str) -> PlanStep:
    return PlanStep(key="open_profile_1", title="Abrir o perfil", goal="abrir o perfil",
                    postcondition=Postcondition(kind="model_judged", value=valor, description="o perfil aberto"))


#: A identidade que o ensino grava (31.87: o alvo é a própria persona, e o parâmetro vira o marcador dela).
DO_ENSINO = step_template_hash(_etapa("perfil de {conta_instagram_usuario} aberto"))


def test_empate_fica_com_o_marcador_da_persona_no_literal_e_no_marcador_escrito() -> None:
    variaveis = {"perfil": f"@{HANDLE.upper()}", "caption_contains": "coleção", **PERSONA}
    persona = tuple(PERSONA)
    literal = para_hash(_etapa(f"perfil de @{HANDLE.upper()} aberto"), variaveis, persona=persona)
    assert literal.postcondition.value == "perfil de {conta_instagram_usuario} aberto"
    assert step_template_hash(literal) == DO_ENSINO
    escrito = para_hash(_etapa("perfil de {perfil} aberto"), variaveis, persona=persona)
    assert step_template_hash(escrito) == DO_ENSINO


def test_sem_persona_ou_com_outro_valor_a_identidade_e_a_de_antes() -> None:
    variaveis = {"perfil": f"@{HANDLE}", **PERSONA}
    de_antes = step_template_hash(_etapa("perfil de {perfil} aberto"))
    assert step_template_hash(para_hash(_etapa(f"perfil de @{HANDLE} aberto"), variaveis)) == de_antes
    outro = {"perfil": "@outra.conta", **PERSONA}
    assert step_template_hash(para_hash(_etapa("perfil de @outra.conta aberto"), outro,
                                        persona=tuple(PERSONA))) == de_antes


async def test_no_plan_simulado_a_etapa_ganha_o_hash_do_ensino_e_a_receita_e_achada(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    st.social.create_profile(ProfileCreate(username=HANDLE, instance_id="android-01"))
    inner: Any = harness.ai.inner
    plan0 = inner.plan

    async def plan(req: Any) -> Any:
        p, u = await plan0(req)
        primeira = p.steps[0].model_copy(update={
            "key": "open_profile_1", "side_effect": False, "commit_guard": [],
            "postcondition": Postcondition(kind="model_judged", value=f"perfil de @{HANDLE} aberto",
                                           description="o perfil aberto")})
        return p.model_copy(update={"steps": [primeira], "parameters": {"perfil": f"@{HANDLE}"}}), u

    inner.plan = plan
    run = harness.run(["android-01"], mode="plan")
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed", "completed"))
    linha = st.db.one("SELECT template_hash FROM steps WHERE run_id=? AND key='open_profile_1'", (run.id,))
    assert linha is not None and linha["template_hash"] == DO_ENSINO
    loja = st.scheduler.executor.recipes
    rid = loja.save(package="com.instagram.android", app_version="447", step_hash=DO_ENSINO, step_key="open_profile_1",
                    actions=[{"tool": "open_app", "commit": False, "args": {"package": "com.instagram.android"}}],
                    learned_from=f"{run.id}:android-01:v1:open_profile_1")
    achada = loja.find("com.instagram.android", "447", linha["template_hash"])
    assert rid and achada is not None and achada["id"] == rid
