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
    `approval.pending` nem no `run.needs_input`, que o Telegram carrega, e o dado vira `<dado da persona>`. A função REAL
    do caminho de evento (`nomes_e_dados_da_persona`) lê o banco."""
    from app.modules.avisos.domain.mensagem import aviso_de_evento, texto_da_mensagem
    from app.modules.avisos.domain.privacidade import DADO_OCULTO
    from app.modules.avisos.infrastructure.portas_da_central import nomes_e_dados_da_persona
    from app.modules.learning.infrastructure.segredo import TriagemDeCredencial

    s = harness.state
    _persona_com_biografia(s)
    nomes = nomes_e_dados_da_persona(s.db)
    assert {"Cidadela", "Oficina Exemplo", "armeira"} <= set(nomes)
    assert "Hyrule" not in nomes                                   # país fica fora da lista
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
            if conversa:
                assert DADO_OCULTO in texto and "<persona>" not in texto, texto
    # sem os dados no filtro, o mesmo título SAIA: é o que a prova pega (mutação: só os nomes)
    vazou = aviso_de_evento("run.updated", parada, 9, "https://painel.exemplo/central", redigir=redigir,
                            nomes=[n for n in nomes if n not in textos_da_biografia(s.db)], conversa=True)
    assert vazou is not None and "Cidadela" in texto_da_mensagem(vazou.titulo, vazou.corpo, vazou.link)


def test_a_resposta_da_ana_e_o_eco_do_trello_nao_mascaram_o_dado_da_persona(harness: Harness) -> None:
    """O outro lado (decisão da orquestradora, 05/10 22:26Z): a conversa composta pela ANA e o eco do Trello usam só
    NOMES. "São Paulo", "música" ou "armeira" são texto comum e passam; o nome da persona continua mascarado."""
    from app.modules.avisos.domain.privacidade import menciona_persona, sem_nome_de_persona, texto_seguro
    from app.modules.avisos.infrastructure.portas_da_central import nomes_de_persona
    from app.modules.learning.infrastructure.segredo import TriagemDeCredencial

    s = harness.state
    _persona_com_biografia(s)
    nomes = nomes_de_persona(s.db)
    assert "Cidadela" not in nomes and "Oficina Exemplo" not in nomes and "zelda.teste" in nomes
    resposta = "Fica em Cidadela, uma armeira da Oficina Exemplo; gosta de música e de São Paulo."
    assert sem_nome_de_persona(resposta, nomes) == resposta                      # a resposta da ANA passa inteira
    assert sem_nome_de_persona("falei com zelda.teste", nomes) == "falei com <persona>"
    redigir = TriagemDeCredencial().redigir
    assert texto_seguro(resposta, nomes, redigir) == resposta                    # o eco do Trello passa
    assert not menciona_persona(resposta, nomes)


def test_dado_e_nome_iguais_saem_como_nome_e_estado_so_pelo_nome_por_extenso() -> None:
    from app.modules.avisos.domain.privacidade import DadoDaPersona, sem_nome_de_persona
    from app.modules.identity.domain.available_data import textos_da_biografia_para_filtro

    # o mesmo texto como nome e como dado: o nome vence
    assert sem_nome_de_persona("oi Cidadela", ["Cidadela", DadoDaPersona("Cidadela")]) == "oi <persona>"
    assert sem_nome_de_persona("oi Cidadela", [DadoDaPersona("Cidadela")]) == "oi <dado da persona>"
    bio = {"home": {"city": "Sao Paulo", "state": "SP", "country": "Brasil"}}
    assert textos_da_biografia_para_filtro({"biography": bio}) == ["Sao Paulo"]      # sem a sigla e sem o país
    assert "São Paulo" in textos_da_biografia_para_filtro({"biography": {"home": {"state": "São Paulo"}}})


def _persona_com_biografia(s) -> None:
    pid = s.social.create_profile(ProfileCreate(username="zelda.teste", instance_id="android-01",
                                                email="zelda@exemplo.test", first_name="Zelda")).id
    s.db.execute("UPDATE instagram_profiles SET biography=? WHERE id=?", (json.dumps(BIOGRAFIA), pid))


def textos_da_biografia(db) -> set[str]:
    from app.modules.identity.domain.available_data import textos_da_biografia_para_filtro
    return {t for r in db.query("SELECT biography FROM instagram_profiles")
            for t in textos_da_biografia_para_filtro({"biography": r["biography"]})}


def test_o_servico_de_avisos_real_leva_os_dados_e_a_conversa_leva_so_os_nomes(harness: Harness) -> None:
    """A ligação em `state.py`: o serviço de eventos usa `nomes_e_dados_da_persona`; a porta da conversa (entrada),
    `nomes_de_persona`. Mutação: trocar uma pela outra quebra um dos dois asserts."""
    from app.modules.avisos.infrastructure.portas_da_central import PortasReais

    s = harness.state
    _persona_com_biografia(s)
    do_servico = s.avisos._nomes() or []
    assert "Cidadela" in do_servico and "zelda.teste" in do_servico
    da_conversa = PortasReais.nomes_de_persona.__get__(type("P", (), {"db": s.db})())()
    assert "Cidadela" not in da_conversa and "zelda.teste" in da_conversa
