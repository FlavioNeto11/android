"""K-pendente do 31.160: a gravação crua do ensino sai com o dado da persona mascarado na leitura da sessão.

Medido em 06/10 (ensino do 31.160, alvo = o perfil da própria persona): o fluxo, a receita e o Livro saíram limpos, mas
o `GET /api/training/{id}` devolvia o @ e o nome dela no `target.text` do resultado tocado, no `target.desc` ("Photo by
…") e nas linhas da tela: 7 ocorrências.

O que estes testes protegem:
* a leitura troca TODO dado da persona pelo marcador (sem diferença de caixa, também depois do @) no alvo tocado, nas
  linhas e no título da tela e no texto digitado; o valor curto e a palavra que só contém o valor ficam;
* a destilação continua lendo a gravação crua (`crua=True`): o fluxo sai com o marcador, como antes;
* a prévia avisa quando um parâmetro do comando sai porque o exemplo é o dado da própria persona que ensinou.

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

from app.training import dado_da_persona as dp

from .conftest import Harness
from .test_treino_dado_da_persona import EMAIL, _proposta, _sessao_com_persona

PERSONA = {"perfil_nome": "Ana Lopes", "conta_instagram_usuario": "ana_lopes", "perfil_email": EMAIL, "perfil_uf": "SP"}


def test_a_leitura_troca_o_dado_da_persona_em_todo_texto_da_entrada() -> None:
    entrada = {"seq": 1, "type": "tap", "text": None,
               "target": {"text": "@Ana_Lopes", "desc": "Photo by ana lopes", "resource_id": "app:id/ana_lopes_x",
                          "bounds": [0, 0, 1, 1], "filhos": [{"text": "ana_lopes", "unique": ["text"]}]},
               "screen_title": "Perfil de ana_lopes", "screen_lines": ["ana_lopes · SP", "bananalopes", 3]}
    (d,) = dp.na_gravacao([entrada], PERSONA)
    assert d["target"]["text"] == "@{conta_instagram_usuario}"
    assert d["target"]["desc"] == "Photo by {perfil_nome}"
    assert d["target"]["resource_id"] == "app:id/ana_lopes_x"           # identificador do app não é texto mostrado
    assert d["target"]["filhos"] == [{"text": "{conta_instagram_usuario}", "unique": ["text"]}]   # o @ no filho da linha
    assert d["screen_title"] == "Perfil de {conta_instagram_usuario}"
    assert d["screen_lines"] == ["{conta_instagram_usuario} · SP", "bananalopes", 3]    # "SP" é curto; dentro de palavra fica
    (t,) = dp.na_gravacao([{"type": "text", "text": f" {EMAIL} ", "target": None, "screen_lines": []}], PERSONA)
    assert t["text"] == " {perfil_email} "
    assert dp.na_gravacao([entrada], {}) == [entrada]
    assert entrada["target"]["text"] == "@Ana_Lopes"                     # a entrada original não muda


def test_o_aviso_do_parametro_que_saiu_por_ser_a_persona() -> None:
    trocados = dp.parametros_da_persona(_proposta(), {"perfil_email": EMAIL})
    assert trocados == [("email", "perfil_email")]
    (linha,) = dp.aviso_dos_parametros(trocados)
    assert linha.startswith("{email} saiu do comando") and "{perfil_email}" in linha and EMAIL not in linha


async def test_o_get_mascara_e_a_destilacao_le_a_crua(harness: Harness) -> None:
    st, sid = await _sessao_com_persona(harness)
    lida = st.training.get(sid)
    assert lida["inputs"][-1]["text"] == "{perfil_email}" and EMAIL not in repr(lida["inputs"])
    assert st.training.get(sid, crua=True)["inputs"][-1]["text"] == EMAIL
    previa = await st.skills.preview(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert any(a.startswith("{email} saiu do comando") for a in previa["warnings"]), previa["warnings"]
    salvo = await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    plano = str(st.db.scalar("SELECT plan FROM flows WHERE id=?", (salvo["flow_id"],)))
    assert "{perfil_email}" in plano and EMAIL not in plano
    assert EMAIL not in repr(salvo["session"]["inputs"])                # a resposta do save também sai mascarada
