"""31.87 F2: o ensino usa os dados da persona (decisão do dono, 05/10 15:13Z).

- O dado da persona que a pessoa DIGITOU (como palavra inteira) vira o marcador `{perfil_x}`: na proposta (o parâmetro
  do comando cujo exemplo é esse dado sai do comando; o literal nas etapas vira o marcador) e na destilação (a
  receita digita `{perfil_email}`, não o e-mail da persona que ensinou).
- O consumo é genérico por chave (`profile_variables`): uma chave nova da identidade entra sem mudar o ensino.
- O que não foi digitado, o valor curto e o que só aparece dentro de outra palavra não são trocados; sem persona,
  nada muda.

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.models import PlanStep, Postcondition
from app.automation.hierarchy import UiTree
from app.taskqueue.recipes import RecipeDiverged, Replayer, distill_training
from app.training import dado_da_persona as dp
from app.training.skills import _destilar
from app.util import now_iso

from .conftest import Harness
from .test_modo_treinamento import _no_controle
from .test_treino_segredo_na_gravacao import _arvore, _el
from .test_treino_validacao_do_salvar import _etapa

EMAIL = "ana.lopes@exemplo.test"
PERSONA = {"perfil_nome": "Ana", "perfil_email": EMAIL, "perfil_sobrenome": "Lopes"}
ALVO = {"resource_id": "app:id/email", "text": "E-mail", "unique": ["rid+text"]}


def _proposta(comando: str = "entre no app com {email} e mande {mensagem}") -> dict[str, Any]:
    escrever = _etapa("escrever", [1, 2], bindings=[{"name": "texto", "value": "{email}"}])
    escrever["goal"] = f"digitar {EMAIL} no campo"
    return {"summary": "entrar e mandar", "command_template": comando, "app_id": "qa-messenger",
            "parameters": [{"name": "email", "example": EMAIL, "description": ""},
                           {"name": "mensagem", "example": "oi", "description": ""}],
            "steps": [escrever], "discarded": [], "questions": []}


# ------------------------------------------------------------------ as funções puras
def test_so_entra_o_dado_digitado_inteiro_numa_entrada_e_com_tamanho() -> None:
    entradas = [{"type": "text", "text": f" {EMAIL} "}, {"type": "text", "text": "banana"}, {"type": "tap"}]
    assert dp.demonstrados(PERSONA, entradas) == {"perfil_email": EMAIL}       # "Ana" só dentro de "banana"
    assert dp.demonstrados({"perfil_nome": "Al"}, [{"type": "text", "text": "Al"}]) == {}     # curto demais
    assert dp.demonstrados({"perfil_nome": "Ana"}, [{"type": "text", "text": "oi Ana!"}]) == {}   # dentro de frase
    assert dp.demonstrados({"perfil_cidade": "Centro"}, [{"type": "text", "text": "rua do centro, 10"}]) == {}
    assert dp.demonstrados({"perfil_nome": "Ana"}, [{"type": "text", "text": "ana"}]) == {"perfil_nome": "Ana"}
    assert dp.demonstrados({}, entradas) == {}


def test_a_proposta_troca_o_parametro_e_o_literal_pelo_marcador() -> None:
    nova, usados = dp.na_proposta(_proposta(), {"perfil_email": EMAIL})
    assert nova["command_template"] == "entre no app com e mande {mensagem}"
    assert [x["name"] for x in nova["parameters"]] == ["mensagem"]
    (st,) = nova["steps"]
    assert st["bindings"] == [{"name": "texto", "value": "{perfil_email}"}]
    assert st["goal"] == "digitar {perfil_email} no campo"
    assert usados == ["perfil_email"] and "{perfil_email}" in dp.aviso(usados)[0]


def test_sem_persona_ou_com_outro_valor_a_proposta_nao_muda() -> None:
    original = _proposta()
    assert dp.na_proposta(original, {}) == (original, [])
    nova, usados = dp.na_proposta(original, {"perfil_email": "bia@exemplo.test"})
    assert nova["command_template"] == original["command_template"] and nova["parameters"] == original["parameters"]
    assert usados == []


def test_a_chave_nova_da_identidade_entra_sem_mudar_o_ensino() -> None:
    """Genérico por chave: `perfil_cidade` (exemplo de chave futura) segue o mesmo caminho do e-mail."""
    persona = {"perfil_cidade": "Sorocaba"}
    entradas = [{"type": "text", "text": "Sorocaba"}]
    assert dp.demonstrados(persona, entradas) == persona
    p = _proposta("procure eventos em {cidade}")
    p["parameters"] = [{"name": "cidade", "example": "Sorocaba", "description": ""}]
    p["steps"][0]["bindings"] = [{"name": "texto", "value": "{cidade}"}]
    nova, usados = dp.na_proposta(p, persona)
    assert nova["steps"][0]["bindings"][0]["value"] == "{perfil_cidade}" and usados == ["perfil_cidade"]


def test_a_receita_digita_o_marcador_e_nao_o_dado_de_quem_ensinou() -> None:
    entradas = [{"type": "tap", "x": 10, "y": 20, "target": dict(ALVO)}, {"type": "text", "text": EMAIL}]
    sem, _ = distill_training(entradas, {}, side_effect=False)
    com, motivo = distill_training(entradas, {"perfil_email": EMAIL}, side_effect=False)
    assert sem is None                                           # o literal não está coberto: nada de receita
    assert com is not None and com[-1]["tool"] == "type_text" and com[-1]["args"]["text"] == "{perfil_email}", motivo


def test_os_parametros_da_pessoa_vencem_a_persona_no_mesmo_valor() -> None:
    """`_destilar` recebe os exemplos da pessoa primeiro: o mesmo valor fica com o nome do parâmetro dela."""
    sess = {"inputs": [{"seq": 1, "type": "tap", "x": 1, "y": 2, "target": dict(ALVO)},
                       {"seq": 2, "type": "text", "text": EMAIL}]}
    p = {"steps": [_etapa("escrever", [1, 2])], "discarded": []}
    passo = PlanStep(key="escrever", title="t", goal="g", postcondition=Postcondition(kind="model_judged", value="",
                                                                                         description=""))
    (d,) = _destilar(sess, p, [passo], {"email": EMAIL, "perfil_email": EMAIL}, {})
    assert d.acoes is not None and d.acoes[-1]["args"]["text"] == "{email}"


# ------------------------------------------------------------------ pelo treino (prévia e save)
async def _sessao_com_persona(harness: Harness, persona: str | None = "p-ana") -> tuple[Any, str]:
    st, rt, lease = await _no_controle(harness)
    if persona is not None:
        st.db.execute("INSERT INTO instagram_profiles(id, username, first_name, last_name, email, created_at,"
                      " updated_at) VALUES (?,?,?,?,?,?,?)", (persona, "", "Ana", "Lopes", EMAIL, now_iso(), now_iso()))
    s = st.training.start("android-01", intent="Entrar e mandar", lease_id=lease, app_id="qa-messenger")
    st.training.record(rt, {"type": "open_app", "app_id": "qa-messenger"}, None)
    campo = _el("e1", rid="com.pocqa.messenger:id/message_input", editable=True, focused=True)
    st.training.record(rt, {"type": "text", "text": EMAIL}, _arvore(_el("e0", text="Entrar"), campo))
    assert st.training.get(s["id"])["inputs"][-1]["text"] == EMAIL         # o gravador guardou o texto
    st.training.stop(s["id"], lease_id=lease)
    st.db.execute("UPDATE training_sessions SET profile_id=? WHERE id=?", (persona, s["id"]))
    return st, s["id"]


async def test_a_previa_e_o_save_levam_o_marcador_e_avisam(harness: Harness) -> None:
    st, sid = await _sessao_com_persona(harness)
    previa = await st.skills.preview(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert any("{perfil_email}" in a for a in previa["warnings"]), previa["warnings"]
    salvo = await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert any("{perfil_email}" in a for a in salvo["warnings"])
    guardada = st.training.get(sid)["proposal"]
    assert guardada["command_template"] == "entre no app com e mande {mensagem}"
    plano = st.db.scalar("SELECT plan FROM flows WHERE id=?", (salvo["flow_id"],))
    assert "{perfil_email}" in plano and EMAIL not in plano           # o e-mail de quem ensinou não fica no fluxo
    assert set(json.loads(plano)["parameters"]) == {"mensagem"}      # o marcador não é parâmetro do comando


async def test_o_treino_sem_persona_segue_como_antes(harness: Harness) -> None:
    st, sid = await _sessao_com_persona(harness, persona=None)
    previa = await st.skills.preview(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert not any("perfil_email" in a for a in previa["warnings"])
    salvo = await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert st.training.get(sid)["proposal"]["command_template"] == "entre no app com {email} e mande {mensagem}"
    assert salvo["flow_id"]


def test_a_reproducao_digita_o_dado_da_persona_do_aparelho() -> None:
    """O executor passa os dados da persona do objetivo ao `Replayer` (os do objetivo vencem). Aqui, só o `Replayer`:
    com a variável da persona o marcador vira o e-mail DESTE aparelho; sem ela, a receita diverge e a IA conduz."""
    acao = [{"tool": "type_text", "commit": False, "why": "e-mail", "args": {"text": "{perfil_email}"}, "selectors": []}]
    arvore = UiTree(elements=[], packages=["com.pocqa.messenger"], sensitive=False)
    com = Replayer(recipe_id=1, version=1, actions=acao, variables={"perfil_email": "bia@exemplo.test"}).next(arvore)
    assert com is not None and com.args["text"] == "bia@exemplo.test"
    with pytest.raises(RecipeDiverged):
        Replayer(recipe_id=1, version=1, actions=acao, variables={}).next(arvore)
