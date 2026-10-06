"""31.182: o ensino avisa quando a etapa mira a conta da própria persona.

Medido em 06/10 (31.160): a pessoa ensinou a abrir o perfil da persona que ensinava; o 31.87 trocou o @ pelo marcador
`{conta_instagram_usuario}`, e a receita 221 virou "abrir o PRÓPRIO perfil", que não serve ao alvo de uma operação. Nada
avisou na prévia nem no salvar.

O que estes testes protegem:
* a etapa com o marcador da conta da persona (no objetivo, na digitação ou na conferência) gera UM aviso que diz a
  etapa e o marcador, sem valor; a etapa com o marcador do perfil (nome, e-mail) ou com parâmetro do comando, não;
* o aviso chega à prévia e ao salvar (é só aviso: o salvar não recusa).

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

from typing import Any

from app.training import conta_propria

from .conftest import Harness
from .test_treino_dado_da_persona import EMAIL, _proposta, _sessao_com_persona


def _com_conta(p: dict[str, Any]) -> dict[str, Any]:
    st = p["steps"][0]
    st["key"] = "abrir_perfil"
    st["goal"] = "abrir o perfil {conta_instagram_usuario} pela busca"
    st["postcondition"] = {"kind": "text_visible", "value": "{conta_instagram_usuario}", "description": "perfil aberto"}
    return p


def test_so_a_etapa_com_a_conta_da_persona_avisa() -> None:
    (linha,) = conta_propria.aviso(_com_conta(_proposta()))
    assert "“abrir_perfil”" in linha and "{conta_instagram_usuario}" in linha and EMAIL not in linha
    assert conta_propria.aviso(_proposta()) == []                     # {email}, parâmetro do comando: não
    p = _proposta()
    p["steps"][0]["goal"] = "digitar {perfil_email}"
    assert conta_propria.aviso(p) == []                              # dado do perfil: não é a conta
    p["steps"][0]["bindings"] = [{"name": "alvo", "value": "{conta_portal_exemplo_gov_br_usuario_2}"}]
    assert len(conta_propria.aviso(p)) == 1                          # conta de site, com o sufixo de colisão


async def test_o_aviso_chega_a_previa_e_ao_salvar(harness: Harness) -> None:
    st, sid = await _sessao_com_persona(harness)
    previa = await st.skills.preview(sid, proposal=_com_conta(_proposta()), profile_ids=[], group_ids=[])
    assert sum("mira a conta da própria persona" in a for a in previa["warnings"]) == 1, previa["warnings"]
    salvo = await st.skills.save(sid, proposal=_com_conta(_proposta()), profile_ids=[], group_ids=[])
    assert salvo["flow_id"] and any("mira a conta da própria persona" in a for a in salvo["warnings"])
