"""31.87 F2 (identidade): gênero, idioma e a biografia por seção viram variável da persona (`{perfil_genero}`,
`{perfil_idioma}`, `{perfil_cidade}`…).

Decisão do dono de 05/10 15:13Z (os dados da persona entram no ensinado) e P-012 (as seis seções da biografia, inclusive
crenças). Senha, código e 2FA só pelo cofre. Nível de prova: `simulated`, valores sintéticos, lógica pura (sem banco).
"""
from __future__ import annotations

import json

from app.models import Plan, PlanStep, PlannerInfo, Postcondition, ProfileCreate
from app.modules.identity.application.available_data import available_data as dados_do_perfil
from app.modules.identity.application.available_data import profile_variables as variaveis_do_perfil
from app.modules.identity.domain.available_data import (BIOGRAPHY_FIELDS, PROFILE_FIELDS, ROTULOS_DO_PERFIL,
                                                      available_data, profile_data, profile_variables)
from app.taskqueue.dado_da_persona import faltas_por_aparelho, nomes_citados, perguntas, rotulo

from .conftest import Harness

BIOGRAFIA = {
    "schema_version": 2, "approx_age": 31,
    "origin": {"birthplace": "Vila Sintetica", "hometown": "Cidadela Velha", "nationality": "hyliana"},
    "home": {"city": "Cidadela", "state": "Reino", "country": "Hyrule", "residence": "casa com a irmã"},
    "work": {"profession": "armeira", "employer": "Oficina Exemplo", "education": ["curso de forja", "oficina"]},
    "life": {"marital_status": "solteira", "children": 0, "history": ["um fato longo que não vira marcador"]},
    "beliefs": {"religion": {"affiliation": "sem religião", "practice": "nao_pratica", "in_speech": "frase longa"},
                "politics": {"orientation": "centro", "engagement": "baixo", "summary": "frase longa"}},
    "tastes": {"interests": ["forja", "música"], "hobbies": ["pesca"], "preferences": [], "dislikes": ["pressa"]},
}
CAMPOS = {"first_name": "Zelda", "last_name": "Sintetica", "email": "zelda@exemplo.test",
          "gender": "feminino", "locale": "pt-BR", "biography": json.dumps(BIOGRAFIA),
          "password": "valor-que-nunca-vira-variavel"}


def test_as_colunas_novas_entram_na_lista_fechada_e_a_biografia_nao() -> None:
    nomes = {nome: coluna for nome, coluna, _r, _t in PROFILE_FIELDS}
    assert nomes["perfil_genero"] == "gender" and nomes["perfil_idioma"] == "locale"
    assert "biography" not in nomes.values() and not any("senha" in n or "codigo" in n for n in nomes)
    assert not any("senha" in n or "codigo" in n or "2fa" in n for n in ROTULOS_DO_PERFIL)


def test_valor_vira_variavel_so_quando_existe_e_o_resto_do_perfil_nao_vaza() -> None:
    variaveis = profile_variables(CAMPOS, [])
    assert variaveis["perfil_genero"] == "feminino" and variaveis["perfil_idioma"] == "pt-BR"
    assert "biography" not in variaveis and "valor-que-nunca-vira-variavel" not in str(variaveis)
    assert variaveis["perfil_cidade"] == "Cidadela"                 # a biografia entra pelos marcadores, nunca crua
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


def test_a_biografia_por_secao_vira_marcador_so_do_que_tem_valor() -> None:
    v = profile_variables(CAMPOS, [])
    assert {n: v[n] for n in ("perfil_cidade_natal", "perfil_cidade", "perfil_estado", "perfil_pais")} == {
        "perfil_cidade_natal": "Vila Sintetica", "perfil_cidade": "Cidadela", "perfil_estado": "Reino",
        "perfil_pais": "Hyrule"}
    assert v["perfil_formacao"] == "curso de forja, oficina" and v["perfil_interesses"] == "forja, música"
    assert v["perfil_filhos"] == "0"                                  # zero filhos é valor, não ausência
    assert v["perfil_religiao"] == "sem religião" and v["perfil_orientacao_politica"] == "centro"
    assert "perfil_preferencias" not in v                             # lista vazia: sem nome, sem variável
    # frases longas e listas de fatos ficam fora: só o campo escalar da seção é marcador
    assert not any("frase longa" in x or "fato longo" in x for x in v.values())
    assert {d.name for d in available_data(CAMPOS, [])} >= {"perfil_cidade", "perfil_religiao"}
    assert len(BIOGRAFIA) and len({n for n, _c, _r in BIOGRAPHY_FIELDS}) == len(BIOGRAPHY_FIELDS)


def test_biografia_ausente_estragada_ou_na_forma_antiga_nao_derruba_nem_inventa() -> None:
    for bio in (None, "", "{", "[]", "123", json.dumps({}), json.dumps({"home": "texto solto"}),
                json.dumps({"home": {"city": 7}})):
        v = profile_variables({**CAMPOS, "biography": bio}, [])
        assert "perfil_cidade" not in v and v["perfil_nome"] == "Zelda"
    # a forma v1 (crença em texto) é lida na forma atual, sem erro
    antiga = {"schema_version": 1, "home": {"city": "Cidadela"}, "beliefs": {"religion": "católica praticante"}}
    assert profile_variables({"biography": antiga}, [])["perfil_cidade"] == "Cidadela"
    # já um dicionário (e não o texto da coluna) também vale
    assert profile_variables({"biography": BIOGRAFIA}, [])["perfil_estado"] == "Reino"


def test_o_prevoo_conhece_os_marcadores_da_biografia_e_pergunta_so_com_rotulo() -> None:
    plano = _plano("Digitar {perfil_cidade}, {perfil_profissao} e {perfil_religiao}.")
    assert nomes_citados(plano) == ["perfil_cidade", "perfil_profissao", "perfil_religiao"]
    parcial = [{"instance_id": "android-01", "variables": {"perfil_cidade": "Cidadela"}}]
    assert faltas_por_aparelho(plano, parcial) == {"android-01": ["perfil_profissao", "perfil_religiao"]}
    texto = str(perguntas({"android-01": ["perfil_profissao", "perfil_religiao"]})[0]["question"])
    assert "profissão e religião" in texto and "sem religião" not in texto
    # um marcador que não é da persona não conta ({perfil_alvo} é parâmetro do comando)
    assert nomes_citados(_plano("Abrir {perfil_alvo}.")) == []


def test_o_adaptador_do_banco_le_a_coluna_da_biografia(harness: Harness) -> None:
    """A porta (`SqlProfileDataStore`) tem de SELECIONAR a coluna `biography`; sem ela o domínio nunca a veria."""
    s = harness.state
    pid = s.social.create_profile(ProfileCreate(username="zelda.teste", instance_id="android-01",
                                                email="zelda@exemplo.test", first_name="Zelda")).id
    s.db.execute("UPDATE instagram_profiles SET gender=?, locale=?, biography=? WHERE id=?",
                 ("feminino", "pt-BR", json.dumps(BIOGRAFIA), pid))
    v = variaveis_do_perfil(s.runs.dados, pid)
    assert v["perfil_genero"] == "feminino" and v["perfil_cidade"] == "Cidadela" and v["perfil_religiao"] == "sem religião"
    assert "perfil_cidade" in {d.name for d in dados_do_perfil(s.runs.dados, pid)}
    # outro perfil, sem biografia: nenhum marcador dela
    outro = s.social.create_profile(ProfileCreate(username="link.teste", instance_id="android-02", first_name="Link")).id
    assert "perfil_cidade" not in variaveis_do_perfil(s.runs.dados, outro)


# ---------------------------------------------------------------- C1 da leitura: o valor não sai pelo Telegram
def test_textos_da_biografia_para_o_filtro_sao_um_por_item_e_sem_enumeracao() -> None:
    from app.modules.identity.domain.available_data import textos_da_biografia_para_filtro

    textos = textos_da_biografia_para_filtro(CAMPOS)
    assert {"Cidadela", "Reino", "armeira", "Oficina Exemplo", "curso de forja", "oficina", "sem religião", "forja",
            "música", "pesca", "pressa"} <= set(textos)
    # enumerações curtas e o número de filhos ficam de fora; lista vazia e biografia estragada não dão nada
    assert not {"centro", "baixo", "nao_pratica", "0"} & set(textos)
    assert textos_da_biografia_para_filtro({"biography": "{"}) == [] and textos_da_biografia_para_filtro(None) == []


def test_valor_da_biografia_num_titulo_de_etapa_nao_sai_pelo_telegram(harness: Harness) -> None:
    """A orquestradora pediu a prova: um título de etapa com valor de biografia (agora variável da persona) não sai no
    `approval.pending` nem no `run.needs_input`, que o Telegram carrega. A porta REAL (`nomes_de_persona`) lê o banco."""
    from app.modules.avisos.domain.mensagem import aviso_de_evento, texto_da_mensagem
    from app.modules.avisos.infrastructure.portas_da_central import nomes_de_persona
    from app.modules.learning.infrastructure.segredo import TriagemDeCredencial

    s = harness.state
    pid = s.social.create_profile(ProfileCreate(username="zelda.teste", instance_id="android-01",
                                                email="zelda@exemplo.test", first_name="Zelda")).id
    s.db.execute("UPDATE instagram_profiles SET biography=? WHERE id=?", (json.dumps(BIOGRAFIA), pid))
    nomes = nomes_de_persona(s.db)
    assert "Cidadela" in nomes and "Oficina Exemplo" in nomes and "armeira" in nomes
    titulo = "Digitar Cidadela e armeira na Oficina Exemplo, com curso de forja"
    proibidos = ("Cidadela", "armeira", "Oficina Exemplo", "forja")
    redigir = TriagemDeCredencial().redigir
    aprovacao = {"approval": {"id": "ap1", "summary": titulo, "target": "caixa", "content": titulo}}
    parada = {"run": {"id": "r-20261005-abc123", "short_id": "abc123", "status": "needs_input",
                      "started_at": "2026-10-05T14:02:11.000Z", "instance_ids": ["android-01"], "command": titulo,
                      "status_detail": titulo}}
    for kind, dados in (("approval.pending", aprovacao), ("run.updated", parada)):
        for conversa in (False, True):
            aviso = aviso_de_evento(kind, dados, 9, "https://painel.exemplo/central", redigir=redigir, nomes=nomes,
                                    conversa=conversa)
            assert aviso is not None
            texto = texto_da_mensagem(aviso.titulo, aviso.corpo, aviso.link)
            for proibido in proibidos:
                assert proibido.casefold() not in texto.casefold(), (kind, conversa, proibido, texto)
    # sem o filtro, o mesmo texto SAIA: é o que a prova pega (mutação: nomes sem a biografia)
    sem = [n for n in nomes if n not in textos_da_biografia(s.db)]
    vazou = aviso_de_evento("run.updated", parada, 9, "https://painel.exemplo/central", redigir=redigir, nomes=sem,
                            conversa=True)
    assert vazou is not None and "Cidadela" in texto_da_mensagem(vazou.titulo, vazou.corpo, vazou.link)


def textos_da_biografia(db) -> set[str]:
    from app.modules.identity.domain.available_data import textos_da_biografia_para_filtro
    return {t for r in db.query("SELECT biography FROM instagram_profiles")
            for t in textos_da_biografia_para_filtro({"biography": r["biography"]})}
