"""Crenças ricas da persona (ADR-047, biografia v2): religião e política viram objetos, vão ao modelo e aparecem no
painel. Tudo `simulated`.

O que se prova aqui:
- o modelo novo (`BioReligion`, `BioPolitics`) valida o rico e o vazio, recusa chave desconhecida e valor fora do
  espectro, e descarta pauta sem tema em vez de derrubar o rascunho;
- a v1 (crença em TEXTO) vira v2 NA LEITURA, sem migração SQL: a frase vira `summary`, e só o que não é adivinhação
  vira `affiliation`/`orientation`; a forma que existe hoje na produção (v1 SEM `beliefs`) sobe de versão intacta;
- o PATCH por seção mescla dentro de `beliefs` e grava a forma nova, também numa linha v1; `null` apaga a crença;
- crença não é exigida para a biografia contar como completa.

Fixtures ANONIMIZADAS: a cópia da produção de 28/09 (`ensaio3`, 14 pessoas) não tem `beliefs` em linha nenhuma —
todas são `{"schema_version": 1, "tastes": {...}}`, algumas com `approx_age`. As frases v1 abaixo são as formas que
o gerador v1 e o painel v1 (um campo de texto livre) podiam gravar.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models import BioBeliefs, BioPolitics, BioReligion, PersonaBiography, PersonaCreate, PersonaPatch
from app.modules.identity.domain.persona import (BIOGRAFIA_MINIMA, BIOGRAPHY_SCHEMA_VERSION, CRENCAS_MINIMAS,
                                                 crenca_legada, lacunas_da_biografia, normalizar_biografia,
                                                 vazio_profundo)

from .test_social_profiles import build

#: A forma REAL de hoje (anonimizada da cópia `ensaio3`): v1, sem `beliefs`.
V1_REAL = {"schema_version": 1, "tastes": {"interests": ["fotografia", "café", "viagem", "arquitetura"]},
           "approx_age": 31}

RELIGIAO_RICA = {
    "affiliation": "católica", "practice": "ocasional", "practices": ["missa em datas especiais", "festa junina"],
    "importance": "tradição de família; pesa nas datas", "in_speech": "“se Deus quiser”, “graças a Deus”",
    "values": ["família", "gratidão"], "sensitive_topics": ["piada com fé alheia"],
    "summary": "católica de tradição, vai à missa no Natal e na Páscoa",
}
POLITICA_RICA = {
    "orientation": "centro_esquerda", "engagement": "baixo",
    "issues": [{"topic": "transporte público", "stance": "quer mais linhas e tarifa menor"},
               {"topic": "saúde pública", "stance": "defende mais verba para os postos"}],
    "discussion_style": "evita discutir com desconhecidos; com amigos, fala com calma",
    "sources": ["jornal local", "podcast de notícias"], "values": ["serviço público", "igualdade"],
    "summary": "vota, se informa pouco e não briga por política",
}


# ---------------------------------------------------------------- modelo
def test_crencas_ricas_validam_e_vazio_tambem() -> None:
    cheia = BioBeliefs.model_validate({"religion": RELIGIAO_RICA, "politics": POLITICA_RICA})
    assert cheia.religion is not None and cheia.religion.practice == "ocasional"
    assert cheia.politics is not None and [i.topic for i in cheia.politics.issues] == ["transporte público", "saúde pública"]
    assert BioBeliefs.model_validate({}) == BioBeliefs() and BioBeliefs().religion is None
    # Pouco preenchido é válido: só a afiliação, só a orientação.
    assert BioReligion(affiliation="espírita").practices == [] and BioPolitics(orientation="apolitica").issues == []


def test_crencas_recusam_chave_desconhecida_e_valor_fora_do_espectro() -> None:
    with pytest.raises(ValidationError):
        BioBeliefs.model_validate({"religion": {"denomination": "x"}})           # extra="forbid" no aninhado
    with pytest.raises(ValidationError):
        BioBeliefs.model_validate({"politics": {"orientation": "extrema"}})      # fora do enum
    with pytest.raises(ValidationError):
        BioBeliefs.model_validate({"religion": {"practice": "sempre"}})
    with pytest.raises(ValidationError):
        BioBeliefs.model_validate({"religion": {"practices": [f"p{i}" for i in range(13)]}})   # teto da lista
    with pytest.raises(ValidationError):
        BioBeliefs.model_validate({"politics": {"summary": "x" * 401}})


def test_pauta_sem_tema_sai_da_lista_em_vez_de_derrubar() -> None:
    p = BioPolitics.model_validate({"issues": [{"topic": None, "stance": "a favor"}, {"topic": "  "},
                                               {"topic": "ciclovias", "stance": None}]})
    assert [(i.topic, i.stance) for i in p.issues] == [("ciclovias", None)]


# ---------------------------------------------------------------- v1 → v2 na leitura
@pytest.mark.parametrize(("campo", "texto", "esperado"), [
    ("religion", "católica", {"summary": "católica", "affiliation": "católica"}),
    ("religion", "sem religião", {"summary": "sem religião", "affiliation": "sem religião"}),
    ("religion", "espírita kardecista", {"summary": "espírita kardecista", "affiliation": "espírita kardecista"}),
    # Frase, não nome de tradição: fica só no resumo (que também vai ao modelo). Nada de adivinhar.
    ("religion", "católica não praticante", {"summary": "católica não praticante"}),
    ("religion", "Testemunha de Jeová", {"summary": "Testemunha de Jeová"}),
    ("religion", "evangélica, vai ao culto todo domingo", {"summary": "evangélica, vai ao culto todo domingo"}),
    ("politics", "centro-esquerda", {"summary": "centro-esquerda", "orientation": "centro_esquerda"}),
    ("politics", "Centro Direita", {"summary": "Centro Direita", "orientation": "centro_direita"}),
    ("politics", "apolítico", {"summary": "apolítico", "orientation": "apolitica"}),
    ("politics", "não fala de política", {"summary": "não fala de política"}),
    ("politics", "votou na esquerda a vida toda", {"summary": "votou na esquerda a vida toda"}),
    ("religion", "   ", None), ("politics", "", None),
])
def test_crenca_v1_vira_objeto_sem_adivinhar(campo: str, texto: str, esperado: dict[str, object] | None) -> None:
    assert crenca_legada(texto, campo=campo) == esperado


def test_biografia_v1_real_sobe_de_versao_intacta() -> None:
    """A forma que EXISTE hoje: v1 sem `beliefs`. Sobe para a versão atual e nada mais muda."""
    assert BIOGRAPHY_SCHEMA_VERSION == 2
    assert normalizar_biografia(V1_REAL) == {**V1_REAL, "schema_version": 2}
    lida = PersonaBiography.model_validate(V1_REAL)
    assert lida.schema_version == 2 and lida.approx_age == 31 and lida.tastes.interests == V1_REAL["tastes"]["interests"]
    assert lida.beliefs == BioBeliefs()


def test_biografia_v1_com_crencas_em_texto_vira_v2() -> None:
    v1 = {"schema_version": 1, "home": {"city": "Recife"},
          "beliefs": {"religion": "católica não praticante", "politics": "centro"}}
    v2 = normalizar_biografia(v1)
    assert v2 == {"schema_version": 2, "home": {"city": "Recife"},
                  "beliefs": {"religion": {"summary": "católica não praticante"},
                              "politics": {"summary": "centro", "orientation": "centro"}}}
    assert normalizar_biografia(v2) == v2                                     # idempotente
    assert v1["beliefs"] == {"religion": "católica não praticante", "politics": "centro"}   # a entrada não muda
    lida = PersonaBiography.model_validate(v1)
    assert lida.beliefs.religion == BioReligion(summary="católica não praticante")
    assert lida.beliefs.politics == BioPolitics(summary="centro", orientation="centro")
    # Texto vazio na v1 = sem crença; versão desconhecida e mais nova não é rebaixada.
    assert normalizar_biografia({"schema_version": 1, "beliefs": {"religion": ""}}) == {"schema_version": 2, "beliefs": {}}
    assert normalizar_biografia({"schema_version": 3})["schema_version"] == 3


def test_crenca_nao_e_exigida_para_a_biografia_contar_como_completa() -> None:
    completa = {"origin": {"birthplace": "Natal"}, "home": {"city": "Recife"}, "work": {"profession": "barista",
                "education": ["Gastronomia"]}, "tastes": {"hobbies": ["trilha"]}}
    assert lacunas_da_biografia(completa) == [] and not any(c.startswith("beliefs") for c in BIOGRAFIA_MINIMA)
    assert lacunas_da_biografia(completa, CRENCAS_MINIMAS) == list(CRENCAS_MINIMAS)
    assert lacunas_da_biografia({"beliefs": {"religion": {"affiliation": "budista"}, "politics": {"orientation": "centro"}}},
                                CRENCAS_MINIMAS) == []
    assert vazio_profundo({"practices": [], "summary": None, "values": [""]}) and not vazio_profundo({"summary": "x"})


# ---------------------------------------------------------------- PATCH por seção
def test_patch_de_crencas_mescla_e_grava_a_forma_nova_tambem_numa_linha_v1(tmp_path: Path) -> None:
    svc, repo, _secrets, db = build(tmp_path)
    try:
        pessoa = svc.create_persona(PersonaCreate(name="Lia Nunes"))
        # Uma linha gravada na v1 (antes desta mudança), escrita direto no banco.
        db.execute("UPDATE instagram_profiles SET biography=? WHERE id=?",
                   (json.dumps({"schema_version": 1, "home": {"city": "Recife"},
                                "beliefs": {"religion": "católica", "politics": "não fala de política"}}), pessoa.id))
        lida = svc.get_persona(pessoa.id)
        assert lida.biography.schema_version == 2 and lida.biography.beliefs.religion is not None
        assert lida.biography.beliefs.religion.affiliation == "católica"
        # Mudar só a prática mantém a afiliação e o resumo que vieram da v1; o JSON gravado sai na forma nova.
        dto = svc.update_persona(pessoa.id, PersonaPatch.model_validate(
            {"biography": {"beliefs": {"religion": {"practice": "regular", "practices": ["missa de domingo"]}}}}))
        assert dto.biography.beliefs.religion == BioReligion(affiliation="católica", summary="católica",
                                                             practice="regular", practices=["missa de domingo"])
        gravado = json.loads(repo.profile_row(pessoa.id)["biography"])  # type: ignore[index]
        assert gravado["schema_version"] == 2 and gravado["home"] == {"city": "Recife"}
        assert gravado["beliefs"]["politics"] == {"summary": "não fala de política"}
        # Política: mescla campo a campo; a lista de pautas substitui inteira.
        dto = svc.update_persona(pessoa.id, PersonaPatch.model_validate(
            {"biography": {"beliefs": {"politics": {"orientation": "nao_declara", "engagement": "baixo",
                                                    "issues": [{"topic": "ciclovias", "stance": "a favor"}]}}}}))
        politica = dto.biography.beliefs.politics
        assert politica is not None and politica.summary == "não fala de política" and politica.orientation == "nao_declara"
        assert [(i.topic, i.stance) for i in politica.issues] == [("ciclovias", "a favor")]
        dto = svc.update_persona(pessoa.id, PersonaPatch.model_validate(
            {"biography": {"beliefs": {"politics": {"issues": []}}}}))
        assert dto.biography.beliefs.politics is not None and dto.biography.beliefs.politics.issues == []
        # `null` explícito apaga a crença inteira (é o único jeito de limpar); a outra fica.
        dto = svc.update_persona(pessoa.id, PersonaPatch.model_validate({"biography": {"beliefs": {"religion": None}}}))
        assert dto.biography.beliefs.religion is None and dto.biography.beliefs.politics is not None
        # Um cliente antigo que ainda manda texto é aceito e convertido, não recusado.
        dto = svc.update_persona(pessoa.id, PersonaPatch.model_validate({"biography": {"beliefs": {"religion": "budista"}}}))
        assert dto.biography.beliefs.religion == BioReligion(affiliation="budista", summary="budista")
    finally:
        db.close()


def test_patch_de_outra_secao_numa_linha_v1_tambem_grava_v2(tmp_path: Path) -> None:
    svc, repo, _secrets, db = build(tmp_path)
    try:
        pessoa = svc.create_persona(PersonaCreate(name="Caio Prado"))
        db.execute("UPDATE instagram_profiles SET biography=? WHERE id=?",
                   (json.dumps({"schema_version": 1, "beliefs": {"religion": "ateu"}}), pessoa.id))
        svc.update_persona(pessoa.id, PersonaPatch.model_validate({"biography": {"home": {"city": "Goiânia"}}}))
        gravado = json.loads(repo.profile_row(pessoa.id)["biography"])  # type: ignore[index]
        assert gravado == {"schema_version": 2, "home": {"city": "Goiânia"},
                           "beliefs": {"religion": {"summary": "ateu", "affiliation": "ateu"}}}
    finally:
        db.close()
