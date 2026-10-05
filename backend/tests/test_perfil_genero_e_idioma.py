"""31.87 F2 (identidade): gênero e idioma do perfil viram variável da persona (`{perfil_genero}`, `{perfil_idioma}`).

Decisão do dono de 05/10 15:13Z: os dados da persona entram no ensinado. A biografia NÃO entra aqui (JSON de seis
seções; que seção vira marcador é pergunta ao dono) e senha, código e 2FA só pelo cofre. Nível de prova: `simulated`,
valores sintéticos, lógica pura (sem banco).
"""
from __future__ import annotations

from app.models import Plan, PlanStep, PlannerInfo, Postcondition
from app.modules.identity.domain.available_data import PROFILE_FIELDS, available_data, profile_data, profile_variables
from app.taskqueue.dado_da_persona import faltas_por_aparelho, nomes_citados, perguntas, rotulo

CAMPOS = {"first_name": "Zelda", "last_name": "Sintetica", "email": "zelda@exemplo.test",
          "gender": "feminino", "locale": "pt-BR", "biography": '{"home": {"city": "Cidadela"}}',
          "password": "valor-que-nunca-vira-variavel"}


def test_as_colunas_novas_entram_na_lista_fechada_e_a_biografia_nao() -> None:
    nomes = {nome: coluna for nome, coluna, _r, _t in PROFILE_FIELDS}
    assert nomes["perfil_genero"] == "gender" and nomes["perfil_idioma"] == "locale"
    assert "biography" not in nomes.values() and not any("senha" in n or "codigo" in n for n in nomes)


def test_valor_vira_variavel_so_quando_existe_e_o_resto_do_perfil_nao_vaza() -> None:
    variaveis = profile_variables(CAMPOS, [])
    assert variaveis["perfil_genero"] == "feminino" and variaveis["perfil_idioma"] == "pt-BR"
    assert "biography" not in variaveis and "valor-que-nunca-vira-variavel" not in str(variaveis)
    assert "Cidadela" not in str(variaveis)
    # sem valor, sem nome: nada de variável vazia (o modelo não recebe nome de dado que a persona não tem)
    vazio = profile_variables({**CAMPOS, "gender": None, "locale": "  "}, [])
    assert "perfil_genero" not in vazio and "perfil_idioma" not in vazio
    nomes = {d.name for d in available_data(CAMPOS, [])}
    assert {"perfil_genero", "perfil_idioma"} <= nomes
    assert all(not d.sensitive for d in profile_data(CAMPOS))


def _plano(texto: str) -> Plan:
    passo = PlanStep(key="escolher", title="t", goal=texto,
                     postcondition=Postcondition(kind="text_visible", value="ok", description="d"))
    return Plan(summary="s", steps=[passo], planner=PlannerInfo(provider="t", model="t", simulated=True))


def test_o_prevoo_pede_resposta_quando_a_persona_nao_tem_genero_ou_idioma() -> None:
    plano = _plano("Escolher {perfil_genero} e o idioma {perfil_idioma}.")
    assert nomes_citados(plano) == ["perfil_genero", "perfil_idioma"]
    parcial = [{"instance_id": "android-01", "variables": {"perfil_genero": "feminino"}}]
    assert faltas_por_aparelho(plano, parcial) == {"android-01": ["perfil_idioma"]}
    completo = [{"instance_id": "android-01", "variables": profile_variables(CAMPOS, [])}]
    assert faltas_por_aparelho(plano, completo) == {}
    assert rotulo("perfil_genero") == "gênero" and rotulo("perfil_idioma") == "idioma e região"
    texto = str(perguntas({"android-01": ["perfil_idioma"]})[0]["question"])
    assert "idioma e região" in texto and "pt-BR" not in texto
