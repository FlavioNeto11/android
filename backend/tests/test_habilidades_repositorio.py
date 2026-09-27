"""`SqlSkillRepository` nos dois bancos: rascunho, congelamento, transições com as condições da §10.3, publicação
com depreciação e comando livre, adoção de fluxo na mesma transação, escopo, integridade e segredo.

Nível de prova: `simulated` (banco de teste, validador falso). Nenhum aparelho, nenhuma IA.
"""
from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path

import pytest

from app.db import Database
from app.modules.skills.application.ports import DocumentValidator, SkillRegistry, SkillRepository, SkillSource
from app.modules.skills.application.registry import CompositeSkillRegistry
from app.modules.skills.domain.lifecycle import (SYSTEM_ACTOR, ContentTampered, DuplicateCommand, FrozenVersion,
                                                 InvalidDocument, SkillNotFound, SkillState, StateConflict,
                                                 TransitionForbidden, ValidationPending)
from app.modules.skills.domain.refs import InvalidSkillRef, SkillRef
from app.modules.skills.domain.validation import CaseKind, Outcome, Proof
from app.modules.skills.domain.versions import SCHEMA_LEGACY_PLAN, Provenance, SourceKind
from app.modules.skills.infrastructure.legacy_flows import LegacyFlowAdapter, legacy_plan
from app.modules.skills.infrastructure.sql_repository import SecretInParameters, SqlSkillRepository
from app.taskqueue.flows import FlowStore

from .fake_skills import ValidadorFalso, banco, documento, fluxo, perfil, repositorio, sem_gatilho_046

S = SkillState
PESSOA = "painel:flavio"
MANUAL = Provenance(SourceKind.MANUAL)
ABRIR = "ig.abrir_conversa"
TEMPLATE = "abrir conversa com {usuario}"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = banco(tmp_path)
    yield d  # type: ignore[misc]
    d.close()


@pytest.fixture
def repo(db: Database) -> SqlSkillRepository:
    return repositorio(db)[0]


def _ate_candidata(repo: SqlSkillRepository, skill_id: str = ABRIR, template: str | None = TEMPLATE,
                   nota: str = "v1") -> SkillRef:
    v = repo.create_draft(skill_id, documento(skill_id, template, nota=nota), source=MANUAL, by=PESSOA)
    repo.transition(v.ref, S.CANDIDATE, by=PESSOA, reason="submeter")
    return v.ref


def _publicada(repo: SqlSkillRepository, skill_id: str = ABRIR, template: str | None = TEMPLATE,
               nota: str = "v1") -> SkillRef:
    ref = _ate_candidata(repo, skill_id, template, nota)
    repo.transition(ref, S.VALIDATED, by=PESSOA, reason="dono validou no teste", manual=True)
    repo.transition(ref, S.PUBLISHED, by=PESSOA, reason="publicar")
    return ref


# ------------------------------------------------------------------ rascunho
def test_o_primeiro_rascunho_cria_a_definicao_e_a_versao_1(repo: SqlSkillRepository, db: Database) -> None:
    v = repo.create_draft(ABRIR, documento(ABRIR, TEMPLATE, apps=("chrome",)), source=MANUAL, by=PESSOA)
    assert v.ref == SkillRef(ABRIR, 1) and v.state is S.DRAFT and v.parent_version is None
    assert v.match_key == "abrir conversa com {usuario}" and v.app_ids == ("chrome", "instagram")
    d = repo.definition(ABRIR)
    assert d is not None and (d.name, d.app_id, d.legacy_flow_id, d.created_by) == ("Abrir Conversa", "instagram",
                                                                                     None, PESSOA)
    assert repo.get(v.ref) == v                                    # ida e volta pelo banco, sem perder nada
    assert [(t.from_state, t.to_state, t.decided_by) for t in repo.history(v.ref)] == [(None, S.DRAFT, PESSOA)]
    assert db.scalar("SELECT content_hash FROM skill_versions WHERE id=?", (str(v.ref),)) == v.content_hash


def test_o_documento_precisa_ser_da_habilidade(repo: SqlSkillRepository) -> None:
    with pytest.raises(InvalidDocument, match="metadata.id"):
        repo.create_draft(ABRIR, documento("ig.outra", TEMPLATE), source=MANUAL)
    with pytest.raises(InvalidSkillRef):
        repo.create_draft("flow:curtir", documento("flow:curtir", TEMPLATE), source=MANUAL)
    assert repo.definition(ABRIR) is None                          # nada ficou pela metade


def test_rascunho_muda_e_versao_submetida_nao_mesmo_sem_o_gatilho(repo: SqlSkillRepository, db: Database) -> None:
    """A camada obrigatória é o repositório: sem a 046, a edição fora de `draft` continua recusada."""
    sem_gatilho_046(db)
    v = repo.create_draft(ABRIR, documento(ABRIR, TEMPLATE), source=MANUAL, by=PESSOA)
    editada = repo.update_draft(v.ref, documento(ABRIR, "abrir a conversa com {usuario}", nota="v1b"))
    assert editada.content_hash != v.content_hash and repo.get(v.ref) == editada
    repo.transition(v.ref, S.CANDIDATE, by=PESSOA, reason="submeter")
    congelada = repo.get(v.ref)
    with pytest.raises(FrozenVersion):
        repo.update_draft(v.ref, documento(ABRIR, TEMPLATE, nota="outra coisa"))
    with pytest.raises(FrozenVersion):
        repo.discard_draft(v.ref)
    assert repo.get(v.ref) == congelada
    # Mudar = nova versão, que aponta para a de origem.
    v2 = repo.create_draft(ABRIR, documento(ABRIR, TEMPLATE, nota="v2"), source=MANUAL, by=PESSOA, parent_version=1)
    assert (v2.ref.version, v2.parent_version, v2.state) == (2, 1, S.DRAFT)
    with pytest.raises(SkillNotFound):
        repo.create_draft(ABRIR, documento(ABRIR, TEMPLATE), source=MANUAL, parent_version=9)


def test_rascunho_se_apaga(repo: SqlSkillRepository) -> None:
    v = repo.create_draft(ABRIR, documento(ABRIR, TEMPLATE), source=MANUAL, by=PESSOA)
    repo.discard_draft(v.ref)
    with pytest.raises(SkillNotFound):
        repo.get(v.ref)


# ------------------------------------------------------------------ transições
def test_submeter_exige_documento_que_compila(db: Database) -> None:
    repo, validador = repositorio(db)
    v = repo.create_draft(ABRIR, documento(ABRIR, TEMPLATE), source=MANUAL, by=PESSOA)
    validador.erros = ("E_UNKNOWN_CAPABILITY: nodes[0]",)
    with pytest.raises(InvalidDocument) as recusa:
        repo.transition(v.ref, S.CANDIDATE, by=PESSOA, reason="submeter")
    assert recusa.value.errors == ("E_UNKNOWN_CAPABILITY: nodes[0]",)
    assert repo.get(v.ref).state is S.DRAFT
    validador.erros = ()
    assert repo.transition(v.ref, S.CANDIDATE, by=PESSOA, reason="submeter").state is S.CANDIDATE


def test_transicao_fora_da_tabela_e_recusada_e_nao_grava_nada(repo: SqlSkillRepository) -> None:
    ref = _ate_candidata(repo)
    for para in (S.PUBLISHED, S.DRAFT, S.DEPRECATED):
        with pytest.raises(TransitionForbidden):
            repo.transition(ref, para, by=PESSOA, reason="x")
    with pytest.raises(TransitionForbidden):                       # candidata → disabled é decisão de pessoa
        repo.transition(ref, S.DISABLED, by=SYSTEM_ACTOR, reason="x")
    assert repo.get(ref).state is S.CANDIDATE and len(repo.history(ref)) == 2


def test_validated_exige_observacao_e_caso_device_so_com_prova_real(repo: SqlSkillRepository) -> None:
    ref = _ate_candidata(repo)
    with pytest.raises(ValidationPending, match="prova"):
        repo.transition(ref, S.VALIDATED, by=SYSTEM_ACTOR, reason="")          # nenhum caso
    repo.add_case(ABRIR, "abre", name="abre a conversa", kind=CaseKind.SIMULATED,
                  expected={"postcondition": "OPEN_THREAD"}, parameters={"usuario": "fulano"})
    repo.add_case(ABRIR, "no-aparelho", name="abre no aparelho", kind=CaseKind.DEVICE, expected={"ok": True})
    repo.record_result(ref, "abre", proof=Proof.SIMULATED, outcome=Outcome.PASSED)
    repo.record_result(ref, "no-aparelho", proof=Proof.SIMULATED, outcome=Outcome.PASSED)
    with pytest.raises(ValidationPending) as pendente:
        repo.transition(ref, S.VALIDATED, by=SYSTEM_ACTOR, reason="")
    assert pendente.value.pending == ("no-aparelho (abre no aparelho): sem observação com prova real",)
    repo.record_result(ref, "no-aparelho", proof=Proof.REAL, outcome=Outcome.PASSED, run_id="r-1",
                       physical_id="emu-5554", app_version="447")
    v = repo.transition(ref, S.VALIDATED, by=SYSTEM_ACTOR, reason="")
    assert v.state is S.VALIDATED and v.state_by == SYSTEM_ACTOR and v.state_detail == "casos de validação aprovados"


def test_caso_aposentado_ou_de_faixa_fechada_nao_conta(repo: SqlSkillRepository) -> None:
    ref = _ate_candidata(repo)                                      # versão 1
    repo.add_case(ABRIR, "abre", name="abre", kind=CaseKind.SIMULATED, expected={})
    repo.add_case(ABRIR, "no-aparelho", name="no aparelho", kind=CaseKind.DEVICE, expected={})
    repo.add_case(ABRIR, "antigo", name="antigo", kind=CaseKind.REPLAY, expected={})
    repo.record_result(ref, "abre", proof=Proof.SIMULATED, outcome=Outcome.PASSED)
    with pytest.raises(ValidationPending):
        repo.transition(ref, S.VALIDATED, by=SYSTEM_ACTOR, reason="")
    repo.retire_case("no-aparelho")                                 # aposentado: não vale para versão nenhuma
    repo.retire_case("antigo", until_version=0)                     # faixa fechada antes da versão 1
    assert {c.id: (c.status.value, c.until_version) for c in repo.cases(ABRIR)} == {
        "abre": ("active", None), "antigo": ("active", 0), "no-aparelho": ("retired", None)}
    assert repo.transition(ref, S.VALIDATED, by=SYSTEM_ACTOR, reason="").state is S.VALIDATED
    with pytest.raises(SkillNotFound):
        repo.retire_case("nao-existe")


def test_as_implementacoes_tem_a_forma_das_portas() -> None:
    """A composição (fase G) liga as peças fora do mypy estrito: aqui se confere nome, tipo e padrão de cada
    parâmetro de cada método das portas, contra as classes concretas."""
    pares = [(SkillSource, SqlSkillRepository), (SkillSource, LegacyFlowAdapter), (SkillRegistry,
                                                                                    CompositeSkillRegistry),
             (SkillRepository, SqlSkillRepository), (DocumentValidator, ValidadorFalso)]
    for porta, classe in pares:
        metodos = [m for m in vars(porta) if not m.startswith("_") and callable(getattr(porta, m))]
        assert metodos, porta
        for nome in metodos:
            esperado = [(p.name, p.kind, p.default) for p in inspect.signature(getattr(porta, nome)).parameters.values()]
            real = [(p.name, p.kind, p.default) for p in inspect.signature(getattr(classe, nome)).parameters.values()]
            assert real == esperado, f"{classe.__name__}.{nome} não tem a forma de {porta.__name__}.{nome}"


def test_validacao_manual_e_de_pessoa_com_motivo_e_fica_registrada(repo: SqlSkillRepository) -> None:
    ref = _ate_candidata(repo)
    repo.add_case(ABRIR, "no-aparelho", name="abre no aparelho", kind=CaseKind.DEVICE, expected={})
    for by, motivo in ((SYSTEM_ACTOR, "automático"), (PESSOA, "  ")):
        with pytest.raises(TransitionForbidden):
            repo.transition(ref, S.VALIDATED, by=by, reason=motivo, manual=True)
    with pytest.raises(TransitionForbidden, match="manual"):
        repo.transition(ref, S.DISABLED, by=PESSOA, reason="x", manual=True)
    v = repo.transition(ref, S.VALIDATED, by=PESSOA, reason="conferi no aparelho de casa", manual=True)
    assert v.state is S.VALIDATED
    assert v.state_detail == ("validação manual: conferi no aparelho de casa "
                              "(pendências: no-aparelho (abre no aparelho): sem observação com prova real)")
    assert repo.history(ref)[-1].reason == v.state_detail and repo.history(ref)[-1].decided_by == PESSOA


def test_rascunho_nao_recebe_prova(repo: SqlSkillRepository) -> None:
    v = repo.create_draft(ABRIR, documento(ABRIR, TEMPLATE), source=MANUAL, by=PESSOA)
    repo.add_case(ABRIR, "abre", name="abre", kind=CaseKind.SIMULATED, expected={})
    with pytest.raises(FrozenVersion, match="rascunho"):
        repo.record_result(v.ref, "abre", proof=Proof.SIMULATED, outcome=Outcome.PASSED)


# ------------------------------------------------------------------ publicar
def test_publicar_deprecia_a_anterior_na_mesma_transacao_e_rollback_volta(repo: SqlSkillRepository) -> None:
    v1 = _publicada(repo)
    assert repo.published(ABRIR) == repo.get(v1)
    v2 = _publicada(repo, nota="v2")                               # mesmo comando: a anterior sai antes
    assert repo.get(v1).state is S.DEPRECATED and repo.get(v2).state is S.PUBLISHED
    ultima = repo.history(v1)[-1]
    assert (ultima.from_state, ultima.to_state, ultima.decided_by) == (S.PUBLISHED, S.DEPRECATED, SYSTEM_ACTOR)
    assert ultima.reason == f"substituída por {v2} (publicada por {PESSOA})"
    # rollback: a depreciada volta, e a atual sai — as validações dela continuam valendo (conteúdo igual).
    repo.transition(v1, S.PUBLISHED, by=PESSOA, reason="voltar")
    assert repo.published(ABRIR) == repo.get(v1) and repo.get(v2).state is S.DEPRECATED
    repo.transition(v1, S.DISABLED, by=PESSOA, reason="parada de emergência")
    assert repo.published(ABRIR) is None
    with pytest.raises(TransitionForbidden):                       # disabled é terminal
        repo.transition(v1, S.PUBLISHED, by=PESSOA, reason="x")


def test_publicar_recusa_o_comando_de_outra_habilidade_e_nao_mexe_em_nada(repo: SqlSkillRepository) -> None:
    _publicada(repo)
    outra = _ate_candidata(repo, "ig.outra", "Abrir   conversa com {usuario}")     # a mesma chave normalizada
    repo.transition(outra, S.VALIDATED, by=PESSOA, reason="ok", manual=True)
    with pytest.raises(DuplicateCommand):
        repo.transition(outra, S.PUBLISHED, by=PESSOA, reason="publicar")
    assert repo.get(outra).state is S.VALIDATED
    assert repo.published(ABRIR) is not None


def test_publicar_recusa_o_comando_de_um_fluxo_ativo_que_nao_e_o_adotado(repo: SqlSkillRepository,
                                                                         db: Database) -> None:
    fluxo(db, "abrir-conversa", TEMPLATE)
    ref = _ate_candidata(repo)
    repo.transition(ref, S.VALIDATED, by=PESSOA, reason="ok", manual=True)
    with pytest.raises(DuplicateCommand, match="fluxo ativo"):
        repo.transition(ref, S.PUBLISHED, by=PESSOA, reason="publicar")
    assert db.scalar("SELECT status FROM flows WHERE id='abrir-conversa'") == "active"
    db.execute("UPDATE flows SET status='disabled' WHERE id='abrir-conversa'")
    assert repo.transition(ref, S.PUBLISHED, by=PESSOA, reason="publicar").state is S.PUBLISHED


def test_versao_adulterada_e_recusada_na_leitura_e_na_transicao(repo: SqlSkillRepository, db: Database) -> None:
    ref = _publicada(repo)
    sem_gatilho_046(db)                                              # por fora do repositório, sem a 2ª camada
    db.execute("UPDATE skill_versions SET content=? WHERE id=?", ('{"adulterado":true}', str(ref)))
    for ler in (lambda: repo.get(ref), lambda: repo.published(ABRIR),
                lambda: repo.resolve("abrir conversa com fulano", None),
                lambda: repo.transition(ref, S.DISABLED, by=PESSOA, reason="x")):
        with pytest.raises(ContentTampered):
            ler()
    assert [s.intact for s in repo.list()] == [False]


# ------------------------------------------------------------------ resolver
def test_resolve_so_publicada_com_valores_do_comando(repo: SqlSkillRepository) -> None:
    ref = _ate_candidata(repo)
    assert repo.resolve("abrir conversa com fulano", None) is None           # candidata não casa
    repo.transition(ref, S.VALIDATED, by=PESSOA, reason="ok", manual=True)
    repo.transition(ref, S.PUBLISHED, by=PESSOA, reason="publicar")
    achada = repo.resolve("Abrir  conversa com @fulano", None)
    assert achada is not None and achada.ref == ref and dict(achada.parameters) == {"usuario": "@fulano"}
    assert achada.definition.id == ABRIR and achada.legacy_flow_id is None
    assert repo.resolve("fechar conversa com fulano", None) is None


def test_resolve_prefere_o_modelo_mais_especifico(repo: SqlSkillRepository) -> None:
    _publicada(repo, "ig.generica", "abrir {coisa}")
    _publicada(repo, ABRIR, TEMPLATE)
    achada = repo.resolve("abrir conversa com fulano", None)
    assert achada is not None and achada.ref.skill_id == ABRIR
    generica = repo.resolve("abrir o app", None)
    assert generica is not None and generica.ref.skill_id == "ig.generica"


def test_resolve_respeita_o_escopo_como_o_fluxo(repo: SqlSkillRepository, db: Database) -> None:
    _publicada(repo)
    perfil(db, "p1")
    perfil(db, "p2", grupo="g1")
    perfil(db, "p3")
    repo.set_scope(ABRIR, profile_ids=["p1"], group_ids=["g1"])
    cmd = "abrir conversa com fulano"
    assert repo.resolve(cmd, None) is not None                    # prévia sem aparelhos: qualquer escopo
    assert repo.resolve(cmd, ["p1", "p2"]) is not None            # todos dentro (p2 pelo grupo)
    assert repo.resolve(cmd, ["p1", "p3"]) is None                # um fora: planeja sozinho
    assert repo.resolve(cmd, ["p1", None]) is None                # aparelho sem perfil não está no escopo
    assert repo.resolve(cmd, []) is None                          # com escopo, lista vazia não casa
    repo.set_scope(ABRIR, profile_ids=[], group_ids=[])
    assert repo.resolve(cmd, []) is not None and repo.scope(ABRIR).everyone


# ------------------------------------------------------------------ adoção de fluxo (adopt-on-write)
def test_adotar_desliga_o_fluxo_na_mesma_transacao(repo: SqlSkillRepository, db: Database) -> None:
    fluxo(db, "curtir", "curtir o post de {perfil}", perfis=("p1",), uses=4)
    antes = FlowStore(db).match("curtir o post de @nasa", None)
    assert antes is not None
    v = repo.adopt_flow("curtir", skill_id="ig.curtir", by=PESSOA)
    assert v.ref == SkillRef("ig.curtir", 1) and v.state is S.PUBLISHED and v.schema_version == SCHEMA_LEGACY_PLAN
    assert v.provenance.kind is SourceKind.LEGACY_FLOW and v.provenance.ref == "curtir"
    assert v.match_key == db.scalar("SELECT match_key FROM flows WHERE id='curtir'")
    assert db.scalar("SELECT status FROM flows WHERE id='curtir'") == "disabled"
    assert FlowStore(db).match("curtir o post de @nasa", None) is None      # nunca os dois vivos
    d = repo.definition("ig.curtir")
    assert d is not None and d.legacy_flow_id == "curtir" and repo.scope("ig.curtir").profile_ids == ("p1",)
    achada = repo.resolve("curtir o post de @nasa", None)
    assert achada is not None and achada.legacy_flow_id == "curtir"
    plano = legacy_plan(achada)
    esperado = antes[1].model_copy(update={"planner": antes[1].planner.model_copy(
        update={"provider": "skill", "model": "skill:ig.curtir@1"})})
    assert plano == esperado                                     # o mesmo plano; só o planejador diz "skill"
    assert repo.history(v.ref)[0].from_state is None


def test_adocao_que_falha_nao_deixa_nada_pela_metade(repo: SqlSkillRepository, db: Database) -> None:
    _publicada(repo, "ig.curtir_nova", "curtir o post de {perfil}")
    fluxo(db, "curtir", "curtir o post de {perfil}")
    with pytest.raises(DuplicateCommand):
        repo.adopt_flow("curtir", skill_id="ig.curtir", by=PESSOA)
    assert repo.definition("ig.curtir") is None
    assert db.scalar("SELECT status FROM flows WHERE id='curtir'") == "active"
    fluxo(db, "parado", "parar {x}", status="disabled")
    with pytest.raises(StateConflict, match="desligado"):
        repo.adopt_flow("parado", skill_id="ig.parado", by=PESSOA)
    with pytest.raises(SkillNotFound):
        repo.adopt_flow("nao-existe", skill_id="ig.nada", by=PESSOA)


def test_desfazer_a_adocao_religa_o_fluxo_e_readotar_usa_a_mesma_definicao(repo: SqlSkillRepository,
                                                                         db: Database) -> None:
    fluxo(db, "curtir", "curtir o post de {perfil}")
    v1 = repo.adopt_flow("curtir", skill_id="ig.curtir", by=PESSOA)
    with pytest.raises(StateConflict, match="desligado"):
        repo.adopt_flow("curtir", skill_id="ig.outro_nome", by=PESSOA)
    # Religado por fora (a rota legada `PUT /api/flows/{id}`): outra adoção continua recusada pela definição.
    db.execute("UPDATE flows SET status='active' WHERE id='curtir'")
    with pytest.raises(StateConflict, match="já foi adotado"):
        repo.adopt_flow("curtir", skill_id="ig.outro_nome", by=PESSOA)
    db.execute("UPDATE flows SET status='disabled' WHERE id='curtir'")
    repo.release_flow("ig.curtir", by=PESSOA, reason="voltar ao fluxo")
    assert repo.get(v1.ref).state is S.DISABLED and repo.published("ig.curtir") is None
    assert db.scalar("SELECT status FROM flows WHERE id='curtir'") == "active"
    v2 = repo.adopt_flow("curtir", skill_id="ig.curtir", by=PESSOA)
    assert v2.ref == SkillRef("ig.curtir", 2) and v2.content_hash == v1.content_hash
    assert db.scalar("SELECT status FROM flows WHERE id='curtir'") == "disabled"


# ------------------------------------------------------------------ listagem, Row que não vaza, segredo
def test_listagem_e_leituras_devolvem_objetos_do_dominio(repo: SqlSkillRepository) -> None:
    _publicada(repo)
    repo.create_draft("ig.rascunho", documento("ig.rascunho", "x {y}", app="chrome"), source=MANUAL)
    todos = repo.list()
    assert [str(s.ref) for s in todos] == [f"{ABRIR}@1", "ig.rascunho@1"]
    assert [str(s.ref) for s in repo.list(state=S.PUBLISHED)] == [f"{ABRIR}@1"]
    assert [str(s.ref) for s in repo.list(app_id="chrome")] == ["ig.rascunho@1"]
    for obj in (*todos, repo.get(SkillRef(ABRIR, 1)), repo.definition(ABRIR), *repo.history(SkillRef(ABRIR, 1)),
                repo.resolve("abrir conversa com x", None)):
        assert dataclasses.is_dataclass(obj) and not isinstance(obj, dict)


def test_segredo_nao_entra_em_caso_de_validacao_nem_na_observacao(repo: SqlSkillRepository, db: Database) -> None:
    senha = "Segr3do-de-teste-42"
    ref = _ate_candidata(repo)
    for params in ({"senha": senha}, {"usuario": "fulano", "extra": {"password": senha}},
                   {"texto": f"senha: {senha}"}, {"lista": [f"password={senha}"]}):
        with pytest.raises(SecretInParameters):
            repo.add_case(ABRIR, "vaza", name="vaza", kind=CaseKind.REPLAY, expected={}, parameters=params)
    repo.add_case(ABRIR, "ok", name="ok", kind=CaseKind.REPLAY, expected={}, parameters={"usuario": "fulano"})
    repo.record_result(ref, "ok", proof=Proof.SIMULATED, outcome=Outcome.FAILED,
                       detail=f"falhou digitando senha={senha}")
    despejo = ""
    for tabela in sorted(t for t in db.tables() if t.startswith(("skill_", "teaching_"))):
        for linha in db.query(f"SELECT * FROM {tabela}"):        # noqa: S608 - nomes vêm do esquema
            despejo += str(dict(linha))
    assert senha not in despejo
    assert "vaza" not in {c.id for c in repo.cases(ABRIR)}


def test_validador_falso_cumpre_a_porta() -> None:
    """O dublê precisa ter a forma da porta que o coordenador vai ligar (o mypy confere a verdadeira)."""
    assert list(inspect.signature(ValidadorFalso().inspect).parameters) == \
        [p for p in inspect.signature(DocumentValidator.inspect).parameters if p != "self"]
