"""Domínio de habilidades, sem banco (§10, ADR-034): hash canônico, `SkillRef`, tabela de transições estado × estado,
congelamento fora de `draft`, regra P4 da validação e a equivalência do casamento com o fluxo legado."""
from __future__ import annotations

import dataclasses
import itertools

import pytest

from app.modules.skills.domain.document import (NotJson, as_json_object, canonical_json, content_hash,
                                                parse_json_object)
from app.modules.skills.domain.lifecycle import (SYSTEM_ACTOR, TERMINAL, TRANSITIONS, Actor, ContentTampered,
                                                 FrozenVersion, InvalidDocument, SkillState, TransitionForbidden,
                                                 allowed, check_transition)
from app.modules.skills.domain.matching import (bind_template_parameters, extract_parameters, normalize_command,
                                                specificity)
from app.modules.skills.domain.refs import InvalidSkillRef, SkillRef
from app.modules.skills.domain.validation import (CaseKind, CaseStatus, Outcome, Proof, ValidationCase,
                                                  ValidationResult, validation_verdict)
from app.modules.skills.domain.versions import (DocumentFacts, Provenance, SkillVersion, SourceKind, UsesLock,
                                                legacy_plan_parameters)
from app.taskqueue.flows import FlowStore, _norm

S = SkillState
T0 = "2026-09-27T10:00:00Z"


# ------------------------------------------------------------------ hash canônico
def test_mesmo_conteudo_da_o_mesmo_hash_qualquer_que_seja_a_ordem_das_chaves() -> None:
    a = {"b": 1, "a": {"y": [1, 2, {"q": "ç", "p": None}], "x": True}}
    b = {"a": {"x": True, "y": [1, 2, {"p": None, "q": "ç"}]}, "b": 1}
    assert canonical_json(a) == canonical_json(b) == '{"a":{"x":true,"y":[1,2,{"p":null,"q":"ç"}]},"b":1}'
    assert content_hash(a) == content_hash(b)
    assert len(content_hash(a)) == 64
    assert content_hash(a) != content_hash({**a, "b": 2})                 # o conteúdo ainda conta
    assert content_hash({"l": [1, 2]}) != content_hash({"l": [2, 1]})     # e a ordem de LISTA também


def test_o_hash_e_o_sha256_do_texto_canonico_em_utf8() -> None:
    """Congelado: mudar o serializador muda o hash de toda versão gravada, e a leitura passaria a acusar adulteração."""
    assert canonical_json({"nome": "ação", "n": 1}) == '{"n":1,"nome":"ação"}'
    assert content_hash({"nome": "ação", "n": 1}) == "0aa0b51f86d0bf925d9989b792b36429da506d828d57a4bb6bb4cdbee1319dc7"


@pytest.mark.parametrize("ruim", [float("nan"), float("inf"), {1: "x"}, {"a": object()}, {"a": {1, 2}}])
def test_o_que_nao_e_json_e_recusado(ruim: object) -> None:
    with pytest.raises(NotJson):
        canonical_json(as_json_object({"v": ruim}))


def test_o_documento_sai_como_copia() -> None:
    original = {"spec": {"nodes": [{"id": "abrir"}]}}
    copia = as_json_object(original)
    assert copia == original and copia is not original
    assert copia["spec"] is not original["spec"]
    assert parse_json_object('{"a": [1]}') == {"a": [1]}
    with pytest.raises(NotJson):
        parse_json_object("[1, 2]")                                      # objeto, não lista


# ------------------------------------------------------------------ SkillRef
def test_skill_ref_formato_e_ida_e_volta() -> None:
    ref = SkillRef.parse("ig.abrir_conversa@3")
    assert ref == SkillRef("ig.abrir_conversa", 3) and str(ref) == "ig.abrir_conversa@3"
    assert not ref.is_legacy and ref.legacy_flow_id is None


@pytest.mark.parametrize("flow_id", ["curtir-o-post", "2-curtir", "fluxo", "a", "habilidade-3"])
def test_skill_ref_do_fluxo_legado_aceita_qualquer_id_de_fluxo(flow_id: str) -> None:
    ref = SkillRef.legacy(flow_id)
    assert str(ref) == f"flow:{flow_id}@1" and ref.is_legacy and ref.legacy_flow_id == flow_id
    assert SkillRef.parse(str(ref)) == ref


@pytest.mark.parametrize("ruim", ["ig.abrir", "IG.abrir@1", "ab@1", "ig.abrir@0", "ig abrir@1", "flow:x@2",
                                  "flow:@1", "flow:a@b@1", "@1", "9skill@1"])
def test_skill_ref_invalido(ruim: str) -> None:
    with pytest.raises(InvalidSkillRef):
        SkillRef.parse(ruim)


# ------------------------------------------------------------------ transições S × S
PERMITIDAS = {
    (S.DRAFT, S.CANDIDATE), (S.CANDIDATE, S.VALIDATED), (S.CANDIDATE, S.DISABLED), (S.VALIDATED, S.PUBLISHED),
    (S.VALIDATED, S.DISABLED), (S.PUBLISHED, S.DEPRECATED), (S.PUBLISHED, S.DISABLED), (S.DEPRECATED, S.PUBLISHED),
    (S.DEPRECATED, S.DISABLED),
}
SO_PESSOA = PERMITIDAS - {(S.CANDIDATE, S.VALIDATED), (S.PUBLISHED, S.DEPRECATED)}


def test_a_tabela_e_exatamente_a_do_design() -> None:
    assert set(TRANSITIONS) == PERMITIDAS
    assert TERMINAL == {S.DISABLED}


@pytest.mark.parametrize("de, para", list(itertools.product(SkillState, SkillState)))
def test_cada_par_de_estados_pela_pessoa(de: SkillState, para: SkillState) -> None:
    if (de, para) in PERMITIDAS:
        assert check_transition(de, para, "painel:flavio") is Actor.PERSON
    else:
        with pytest.raises(TransitionForbidden):
            check_transition(de, para, "painel:flavio")


@pytest.mark.parametrize("de, para", list(itertools.product(SkillState, SkillState)))
def test_cada_par_de_estados_pelo_sistema(de: SkillState, para: SkillState) -> None:
    pode = (de, para) in PERMITIDAS - SO_PESSOA
    assert allowed(de, para, Actor.SYSTEM) is pode
    if pode:
        assert check_transition(de, para, SYSTEM_ACTOR) is Actor.SYSTEM
    else:
        with pytest.raises(TransitionForbidden):
            check_transition(de, para, SYSTEM_ACTOR)


def test_congelada_nao_volta_a_rascunho_e_disabled_e_terminal() -> None:
    for de in (S.CANDIDATE, S.VALIDATED, S.PUBLISHED, S.DEPRECATED, S.DISABLED):
        with pytest.raises(TransitionForbidden, match="rascunho|proibida"):
            check_transition(de, S.DRAFT, "pessoa")
    assert not any(allowed(S.DISABLED, para, a) for para in SkillState for a in Actor)
    with pytest.raises(TransitionForbidden):
        check_transition(S.DRAFT, S.CANDIDATE, "  ")                    # sem dizer quem decidiu


# ------------------------------------------------------------------ versão: congelamento e integridade
def _fatos(template: str = "abrir conversa com {usuario}", **kw: object) -> DocumentFacts:
    base: dict[str, object] = {"skill_id": "ig.abrir_conversa", "name": "Abrir conversa", "app_id": "instagram",
                               "command_template": template, "app_ids": ("instagram",)}
    base.update(kw)
    return DocumentFacts(**base)  # type: ignore[arg-type]


def _rascunho() -> SkillVersion:
    return SkillVersion.new_draft(SkillRef("ig.abrir_conversa", 1), {"spec": {"n": 1}}, _fatos(),
                                  provenance=Provenance(SourceKind.MANUAL), parent_version=None, by="pessoa", now=T0)


def test_rascunho_muda_de_conteudo_e_o_hash_acompanha() -> None:
    v = _rascunho()
    assert v.state is S.DRAFT and v.editable and v.match_key == "abrir conversa com {usuario}"
    nova = v.revise({"spec": {"n": 2}}, _fatos("Abrir  a conversa com {usuario}"))
    assert nova.content_hash != v.content_hash and nova.document() == {"spec": {"n": 2}}
    assert nova.match_key == "abrir a conversa com {usuario}" and nova.ref == v.ref
    nova.verify_integrity()


@pytest.mark.parametrize("estado", [S.CANDIDATE, S.VALIDATED, S.PUBLISHED, S.DEPRECATED, S.DISABLED])
def test_fora_de_rascunho_o_conteudo_nao_muda(estado: SkillState) -> None:
    congelada = dataclasses.replace(_rascunho(), state=estado)
    with pytest.raises(FrozenVersion):
        congelada.revise({"spec": {"n": 2}}, _fatos())


def test_o_formato_do_documento_nao_muda_num_rascunho() -> None:
    with pytest.raises(InvalidDocument):
        _rascunho().revise({"spec": {}}, _fatos(schema_version=0))


def test_a_transicao_devolve_outra_versao_e_nao_mexe_na_original() -> None:
    v = _rascunho()
    c = v.moved_to(S.CANDIDATE, by="pessoa", detail="submetida", at="2026-09-27T11:00:00Z")
    assert (v.state, c.state) == (S.DRAFT, S.CANDIDATE) and c.content_hash == v.content_hash
    assert c.state_by == "pessoa" and c.state_detail == "submetida"
    with pytest.raises(FrozenVersion):
        c.revise({"spec": {}}, _fatos())


def test_conteudo_adulterado_e_percebido_na_leitura() -> None:
    v = _rascunho()
    adulterada = dataclasses.replace(v, content_json='{"spec":{"n":999}}')
    with pytest.raises(ContentTampered):
        adulterada.verify_integrity()
    with pytest.raises(ContentTampered):
        dataclasses.replace(v, content_json="{não é json").verify_integrity()


def test_o_documento_devolvido_e_copia_e_nao_altera_a_versao() -> None:
    v = _rascunho()
    doc = v.document()
    doc["spec"] = "mexido"
    assert v.document() == {"spec": {"n": 1}}
    v.verify_integrity()


def test_proveniencia_ida_e_volta() -> None:
    p = Provenance(SourceKind.TEACHING, "ens-1", candidate_id="cand-1", teaching_id="ens-1", generated_by="ai:m",
                   compiler_version="1", uses_lock=(UsesLock("ig.abrir_conversa", 2, "abc"),), reviewed_by="flavio")
    assert Provenance.from_json(p.kind, p.ref, p.to_json()) == p
    assert Provenance.from_json(SourceKind.MANUAL, None, {}) == Provenance(SourceKind.MANUAL)


def test_parametros_do_plano_legado() -> None:
    assert legacy_plan_parameters({"plan": {"parameters": {"a": "{a}", "n": 3}}}) == {"a": "{a}"}
    assert legacy_plan_parameters({"plan": None}) == {}


# ------------------------------------------------------------------ P4: validação
def _caso(cid: str, kind: CaseKind, **kw: object) -> ValidationCase:
    base: dict[str, object] = {"id": cid, "skill_id": "ig.x", "name": cid, "kind": kind, "since_version": None,
                               "until_version": None, "parameters": {}, "preconditions": {}, "expected": {},
                               "source_kind": None, "source_ref": None, "status": CaseStatus.ACTIVE}
    base.update(kw)
    return ValidationCase(**base)  # type: ignore[arg-type]


_n = itertools.count(1)


def _res(cid: str, proof: Proof, outcome: Outcome) -> ValidationResult:
    return ValidationResult(id=next(_n), case_id=cid, ref=SkillRef("ig.x", 2), proof=proof, outcome=outcome,
                            run_id=None, instance_id=None, physical_id=None, app_version=None, variant=None,
                            detail=None, observed_at=T0, observed_by=None)


def test_sem_caso_nao_valida() -> None:
    assert not validation_verdict(2, [], []).ready


def test_caso_device_so_conta_com_prova_real() -> None:
    casos = [_caso("sim", CaseKind.SIMULATED), _caso("dev", CaseKind.DEVICE)]
    so_simulado = [_res("sim", Proof.SIMULATED, Outcome.PASSED), _res("dev", Proof.SIMULATED, Outcome.PASSED)]
    v = validation_verdict(2, casos, so_simulado)
    assert not v.ready and v.pending == ("dev (dev): sem observação com prova real",)
    real = [*so_simulado, _res("dev", Proof.REAL, Outcome.PASSED)]
    assert validation_verdict(2, casos, real).ready
    # Um `simulated` reprovado DEPOIS do real não derruba o caso device; um real reprovado derruba.
    assert validation_verdict(2, casos, [*real, _res("dev", Proof.SIMULATED, Outcome.FAILED)]).ready
    assert not validation_verdict(2, casos, [*real, _res("dev", Proof.REAL, Outcome.UNCERTAIN)]).ready


def test_o_ultimo_resultado_manda_e_negativo_conta_como_os_outros() -> None:
    casos = [_caso("neg", CaseKind.NEGATIVE), _caso("rep", CaseKind.REPLAY)]
    base = [_res("neg", Proof.SIMULATED, Outcome.PASSED), _res("rep", Proof.SIMULATED, Outcome.FAILED)]
    v = validation_verdict(2, casos, base)
    assert not v.ready and v.pending == ("rep (rep): último resultado failed",)
    assert validation_verdict(2, casos, [*base, _res("rep", Proof.SIMULATED, Outcome.PASSED)]).ready


def test_so_os_casos_ativos_na_faixa_da_versao_contam() -> None:
    casos = [_caso("velho", CaseKind.REPLAY, until_version=1), _caso("novo", CaseKind.REPLAY, since_version=3),
             _caso("aposentado", CaseKind.REPLAY, status=CaseStatus.RETIRED), _caso("vale", CaseKind.REPLAY,
                                                                                   since_version=2)]
    v = validation_verdict(2, casos, [])
    assert v.pending == ("vale (vale): não executado",)
    assert validation_verdict(2, casos, [_res("vale", Proof.SIMULATED, Outcome.PASSED)]).ready


# ------------------------------------------------------------------ casamento = o do fluxo legado
CASOS_DE_CASAMENTO = [
    ("curtir o post de {perfil}", "curtir o post de @nasa"),
    ("curtir o post de {perfil}", "Curtir   o POST de @nasa "),
    ("mande “{texto}” para {usuario}", "mande \"oi, tudo bem?\" para fulano"),
    ("abrir conversa com {usuario}", "abrir conversa com"),
    ("{a} e {a}", "x e x"),
    ("{a} e {a}", "x e y"),
    ("no {instance_id}, abrir {app}", "no {instance_id}, abrir chrome"),
    ("no {instance_id}, abrir {app}", "no android-01, abrir chrome"),
    ("seguir {perfil}", "seguir " + "x" * 501),
    ("seguir {perfil}", "seguir " + "x" * 500),
    ("sem parâmetro", "SEM   parâmetro"),
    ("ﬁcar com {x}", "ficar com algo"),
]


@pytest.mark.parametrize("template, comando", CASOS_DE_CASAMENTO)
def test_a_extracao_e_a_mesma_do_fluxo_legado(template: str, comando: str) -> None:
    assert extract_parameters(template, comando) == FlowStore._extract(template, comando)


@pytest.mark.parametrize("texto", ["Curtir  O post", " ﬁ x ", "AÇÃO\tlá", "Straße"])
def test_a_chave_de_casamento_e_a_mesma_do_fluxo_legado(texto: str) -> None:
    assert normalize_command(texto) == _norm(texto)


def test_ligar_parametros_do_plano_modelo() -> None:
    assert bind_template_parameters({"perfil": "{perfil}", "fixo": "x"}, {"perfil": "@nasa"}) == {
        "perfil": "@nasa", "fixo": "x"}
    assert bind_template_parameters({"perfil": "{perfil}", "alvo": "{alvo}"}, {"perfil": "@nasa"}) is None


def test_o_modelo_mais_especifico_vem_primeiro() -> None:
    modelos = ["curtir {x}", "curtir o post de {perfil}", "curtir o post de {perfil} e {outro}", "{tudo}"]
    ordem = sorted(modelos, key=lambda t: (-specificity(t)[0], -specificity(t)[1], t))
    assert ordem == ["curtir o post de {perfil} e {outro}", "curtir o post de {perfil}", "curtir {x}", "{tudo}"]
