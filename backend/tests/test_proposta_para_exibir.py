"""31.183: a leitura da sessão de ensino traz `proposal_exibicao`, a proposta com o dado da persona mascarado.

A revisão de segredos do K-107 deixou um médio: o título, o objetivo e o resumo da proposta só trocavam o dado que a
pessoa DIGITOU inteiro, e um @ ou um nome visto na tela e citado pela IA saía em claro no `GET /api/training/{id}`. A
`proposal` não pode mudar (o painel a devolve no salvar, e o marcador no título faria a etapa mirar a persona).

O que estes testes protegem:
* a cópia troca todo dado da persona (sem caixa, depois do @, com qualquer espaço) em título, objetivo, resumo,
  comando, perguntas e conferência; identificadores (`key`, `capability`, `app_id`) ficam;
* a `proposal` sai igual, e sem persona a cópia é a própria proposta.

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

import json

import httpx

from app.main import create_app
from app.training import exibicao

from .conftest import Harness
from .test_treino_dado_da_persona import _sessao_com_persona

PERSONA = {"perfil_nome": "Ana Lopes", "conta_instagram_usuario": "ana_lopes"}


def test_a_copia_mascara_o_texto_e_deixa_os_identificadores() -> None:
    p = {"summary": "abrir o perfil de @Ana_Lopes", "command_template": "abra o perfil de ana lopes",
         "questions": ["A Ana\u00a0Lopes é o alvo?"],
         "steps": [{"key": "ana_lopes_x", "title": "Abrir ana_lopes", "capability": None, "app_id": "instagram",
                    "postcondition": {"kind": "text_visible", "value": "ana_lopes", "description": "perfil de Ana Lopes"}}]}
    c = exibicao.proposta(p, PERSONA)
    assert isinstance(c, dict)
    assert c["summary"] == "abrir o perfil de @{conta_instagram_usuario}"
    assert c["command_template"] == "abra o perfil de {perfil_nome}"
    assert c["questions"] == ["A {perfil_nome} é o alvo?"]
    (st,) = c["steps"]
    assert st["key"] in ("{conta_instagram_usuario}_x", "{perfil_nome}_x") and st["app_id"] == "instagram"
    assert st["title"] == "Abrir {conta_instagram_usuario}"
    assert st["postcondition"] == {"kind": "text_visible", "value": "{conta_instagram_usuario}",
                                   "description": "perfil de {perfil_nome}"}
    assert p["summary"] == "abrir o perfil de @Ana_Lopes"                     # a original não muda
    assert exibicao.proposta(p, {}) is p and exibicao.proposta(None, PERSONA) is None


async def test_o_get_da_sessao_traz_a_copia_e_a_proposta_igual(harness: Harness) -> None:
    st, sid = await _sessao_com_persona(harness)                             # persona "Ana" "Lopes"
    proposta = {"summary": "falar com Lopes", "steps": [{"key": "abrir", "title": "abrir a conversa de Lopes"}]}
    st.db.execute("UPDATE training_sessions SET proposal=? WHERE id=?", (json.dumps(proposta), sid))
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        corpo = (await c.get(f"/api/training/{sid}")).json()
    assert corpo["proposal"]["summary"] == "falar com Lopes"
    assert corpo["proposal_exibicao"]["summary"] == "falar com {perfil_sobrenome}"
    assert corpo["proposal_exibicao"]["steps"][0]["title"] == "abrir a conversa de {perfil_sobrenome}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        lista = (await c.get("/api/training")).json()
    (da_lista,) = [x for x in lista if x["id"] == sid]                       # a lista também leva a cópia
    assert da_lista["proposal_exibicao"]["summary"] == "falar com {perfil_sobrenome}"


async def test_o_relatorio_da_previa_e_do_salvar_sai_com_o_titulo_mascarado(harness: Harness) -> None:
    """Achado da Portal no 31.189: o `steps[].title` do resultado do salvar vinha em claro."""
    from .test_treino_dado_da_persona import _proposta

    st, sid = await _sessao_com_persona(harness)                             # persona "Ana" "Lopes"
    p = _proposta()
    p["steps"][0]["title"] = "escrever para Lopes"
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    corpo = {"proposal": p, "profile_ids": [], "group_ids": []}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        previa = (await c.post(f"/api/training/{sid}/preview", json=corpo)).json()
        salvo = (await c.post(f"/api/training/{sid}/save", json=corpo)).json()
    assert [s["title"] for s in previa["steps"]] == ["escrever para {perfil_sobrenome}"], previa
    assert [s["title"] for s in salvo["steps"]] == ["escrever para {perfil_sobrenome}"]
    assert salvo["steps"][0]["key"] == p["steps"][0]["key"]                   # a chave do relatório fica
