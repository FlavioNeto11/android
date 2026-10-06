"""Ensino v2 (fase F, design §13 e §15.3): sessão, demonstração, candidata, perguntas e respostas, submissão.

Nível de prova: `simulated`. O generalizador é o `generalize` do provedor SIMULADO (regras fixas) por baixo do
`CountingProvider`, que conta as chamadas; nenhuma IA real é chamada (a chamada paga fica `not_run`). O aparelho é
o falso do harness (`base_console_port: 5640`).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.contracts.skills.v1alpha1 import SkillDocument
from app.modules.skills.application.teaching import TeachingService
from app.modules.skills.domain.lifecycle import SkillState
from app.modules.skills.domain.refs import SkillRef, is_skill_id
from app.modules.skills.domain.teaching import (TERMINAL, TRANSITIONS, CandidateStatus, CredentialInText,
                                                Demonstration, DemonstrationKind, TeachingSession, TeachingSource,
                                                TeachingStateConflict, TeachingStatus, TeachingTurn,
                                                TeachingValidation, TurnAuthor, TurnKind, source_of,
                                                suggest_skill_id)
from app.modules.skills.domain.versions import SourceKind
from app.training.generalizer import ProviderSkillGeneralizer
from app.planning.provider import Usage

from .conftest import Harness
from .test_modo_treinamento import SEGREDO, _gravar_mensagem, _no_controle
from .test_perfil_bloqueado_e_capacidades import _cliente

TABELAS = ("teaching_sessions", "teaching_demonstrations", "teaching_turns", "teaching_candidates",
           "skill_definitions", "skill_versions", "skill_version_transitions")


def _despejo(st: Any) -> str:
    """Tudo o que o ensino e as habilidades gravaram, como texto: é onde um segredo não pode aparecer."""
    return json.dumps({t: [dict(r) for r in st.db.query(f"SELECT * FROM {t}")] for t in TABELAS},
                      ensure_ascii=False, default=str)


def _servico(st: Any) -> TeachingService:
    assert isinstance(st.teaching, TeachingService)
    return st.teaching


async def _ensino_com_gravacao(harness: Harness) -> tuple[Any, TeachingService, str, str]:
    st, rt, lease = await _no_controle(harness)
    sid = await _gravar_mensagem(st, rt, lease, harness.fakes["android-01"])
    ens = _servico(st)
    v = ens.start("Mandar mensagem no QA Messenger para", app_id="qa-messenger", operator="painel:flavio")
    ens.attach_recording(v.session.id, sid, by="painel:flavio")
    return st, ens, v.session.id, sid


# ================================================================== domínio
def test_fonte_e_derivada_do_conteudo() -> None:
    base = TeachingSession(id="ens-1", instruction="abrir a conversa", skill_id=None, base_version=None,
                           app_id="qa-messenger", profile_id=None, status=TeachingStatus.OPEN,
                           validation_status=TeachingValidation.NONE, result_version_id=None, operator=None,
                           created_at="t", updated_at="t", closed_at=None)
    gravacao = Demonstration("d1", "ens-1", 1, DemonstrationKind.RECORDING, "trn-1", None, "android-01", None, None,
                             "t")
    execucao = Demonstration("d2", "ens-1", 2, DemonstrationKind.RUN, None, "r-1", None, None, None, "t")
    correcao = TeachingTurn(1, "ens-1", TurnKind.CORRECTION, TurnAuthor.PERSON, None, None, "x", None, None, None,
                            "t")
    assert source_of(base, [], []) is TeachingSource.INSTRUCTION
    assert source_of(base, [gravacao], []) is TeachingSource.HYBRID
    from dataclasses import replace
    assert source_of(replace(base, instruction=""), [gravacao], []) is TeachingSource.DEMONSTRATION
    assert source_of(base, [execucao], []) is TeachingSource.SUCCESSFUL_EXECUTION
    assert source_of(base, [gravacao, execucao], []) is TeachingSource.HYBRID
    # correção só vale para quem melhora uma habilidade existente
    assert source_of(base, [], [correcao]) is TeachingSource.INSTRUCTION
    assert source_of(replace(base, skill_id="qa.x"), [execucao], [correcao]) is TeachingSource.CORRECTION


def test_tabela_de_estados_do_ensino() -> None:
    assert set(TRANSITIONS) == set(TeachingStatus)
    assert TERMINAL == {TeachingStatus.PUBLISHED, TeachingStatus.DISCARDED}
    for s in TeachingStatus:
        if s not in TERMINAL:
            assert TeachingStatus.DISCARDED in TRANSITIONS[s], s          # qualquer não terminal descarta
    assert TRANSITIONS[TeachingStatus.ASKING] == {TeachingStatus.PROPOSING, TeachingStatus.DISCARDED}
    assert TeachingStatus.PUBLISHED in TRANSITIONS[TeachingStatus.READY]
    assert TeachingStatus.PUBLISHED not in TRANSITIONS[TeachingStatus.VALIDATING]   # só o validado vira versão


def test_id_sugerido_e_valido_e_deterministico() -> None:
    a = suggest_skill_id("qa-messenger", "Mandar mensagem no QA Messenger para")
    assert a == suggest_skill_id("qa-messenger", "Mandar mensagem no QA Messenger para")
    assert is_skill_id(a) and a.startswith("qa-messenger.")
    assert is_skill_id(suggest_skill_id("Instagram", "Ação: curtir a publicação de @nasa"))
    assert is_skill_id(suggest_skill_id("1app", ""))


# ================================================================== candidata do simulado
async def test_candidata_do_provedor_simulado_a_partir_da_gravacao(harness: Harness) -> None:
    st, ens, tid, sid = await _ensino_com_gravacao(harness)
    assert ens.get(tid).source is TeachingSource.HYBRID
    antes = harness.ai.count("generalize")
    v = await ens.propose(tid, by="painel:flavio")
    assert harness.ai.count("generalize") == antes + 1                # uma chamada ao generalizador (simulado)
    c = v.current
    assert c is not None and c.status is CandidateStatus.PROPOSED and c.generated_by.startswith("ai:")
    doc = SkillDocument.model_validate(c.envelope.document)          # a saída é DADO no contrato v1alpha1
    assert doc.metadata.app == "qa-messenger" and is_skill_id(doc.metadata.id)
    assert [n.id for n in doc.spec.nodes] == ["abrir_app", "abrir_conversa", "escrever", "enviar"]
    assert all(n.depends_on is not None for n in doc.spec.nodes)     # a composição sempre emite depends_on
    assert "{contato}" in doc.spec.invocation.command_template
    assert v.errors == ()                                             # compila sem erro
    anot = c.envelope.annotations
    tipos = {p.name: (p.type, p.examples) for p in anot.parameters}
    assert tipos == {"contato": ("string", ("QA-001",)), "mensagem": ("text", ("Olá, tudo certo?",))}
    assert anot.evidence["abrir_conversa"] == (2, 3) and anot.discarded == ((6, anot.discarded[0][1]),)
    assert [e["node"] for e in anot.effects] == ["enviar"]
    assert any(p["kind"] == "device" for p in anot.suggested_proofs)  # efeito só se prova em aparelho real
    assert anot.preconditions and anot.postconditions and anot.assumptions
    # o generalizador PERGUNTA sobre o efeito em vez de supor
    assert v.session.status is TeachingStatus.ASKING
    assert [q.question_key for q in v.open_questions] == ["efeito:enviar"]
    assert v.open_questions[0].author is TurnAuthor.AI
    # o hash da candidata é o do DOCUMENTO (o envelope não entra): é o que a versão terá
    from app.modules.skills.domain.document import content_hash
    assert c.content_hash == content_hash(c.envelope.document)
    # nada mudou na gravação v1
    assert st.training.get(sid)["status"] == "recorded"


async def test_laco_de_perguntas_e_respostas_ate_o_rascunho(harness: Harness) -> None:
    st, ens, tid, _ = await _ensino_com_gravacao(harness)
    v = await ens.propose(tid, by="painel:flavio")
    primeira = v.current
    assert primeira is not None
    with pytest.raises(TeachingStateConflict) as pendente:
        await ens.propose(tid)                                        # pergunta sem resposta: não gera de novo
    assert pendente.value.code == "questions_pending"
    pergunta = v.open_questions[0]
    v = ens.answer(tid, pergunta.id, "Sim, enviar a mensagem é o efeito.", by="painel:flavio")
    assert v.open_questions == () and v.session.status is TeachingStatus.ASKING
    with pytest.raises(TeachingStateConflict):
        ens.answer(tid, pergunta.id, "de novo")                      # não se responde duas vezes

    v = await ens.propose(tid, by="painel:flavio")
    segunda = v.current
    assert segunda is not None and segunda.id != primeira.id
    assert v.session.status is TeachingStatus.VALIDATING and v.open_questions == ()   # respondida não volta
    assert {c.id: c.status for c in v.candidates}[primeira.id] is CandidateStatus.SUPERSEDED
    assert any("enviar a mensagem é o efeito" in a for a in segunda.envelope.annotations.assumptions)
    assert harness.ai.count("generalize") == 2

    with pytest.raises(TeachingStateConflict):
        ens.publish(segunda.id, by="painel:flavio")                   # sem validar, não vira versão
    v = ens.validate(segunda.id, by="painel:flavio")
    assert v.session.status is TeachingStatus.READY and v.session.validation_status is TeachingValidation.PASSED
    v = ens.publish(segunda.id, by="painel:flavio")
    assert v.session.status is TeachingStatus.PUBLISHED and v.session.closed_at
    ref = SkillRef.parse(v.session.result_version_id or "")
    versao = st.skill_repo.get(ref)
    assert versao.state is SkillState.DRAFT                           # o ensino não publica a habilidade
    assert versao.provenance.kind is SourceKind.TEACHING and versao.provenance.ref == tid
    assert versao.provenance.candidate_id == segunda.id and versao.provenance.reviewed_by == "painel:flavio"
    assert versao.content_hash == segunda.content_hash                # só o `document` vai para a versão
    aceita = {c.id: c for c in v.candidates}[segunda.id]
    assert aceita.status is CandidateStatus.ACCEPTED and aceita.version_id == str(ref)
    # repetir a submissão devolve a mesma versão (idempotente pela candidata), sem versão nova
    assert ens.publish(segunda.id, by="painel:flavio").session.result_version_id == str(ref)
    assert st.db.scalar("SELECT COUNT(*) FROM skill_versions") == 1
    assert not st.skill_registry.list(state=SkillState.PUBLISHED)     # nada publicado sozinho


async def test_so_instrucao_pergunta_em_vez_de_inventar(harness: Harness) -> None:
    ens = _servico(harness.state)
    tid = ens.start("Abrir o QA Messenger e mostrar a caixa de entrada", app_id="qa-messenger").session.id
    assert ens.get(tid).source is TeachingSource.INSTRUCTION
    v = await ens.propose(tid)
    c = v.current
    assert c is not None and v.session.status is TeachingStatus.ASKING
    assert [n["id"] for n in c.envelope.document["spec"]["nodes"]] == ["executar"]   # type: ignore[index,union-attr]
    assert any("julgamento do modelo" in r for r in c.envelope.annotations.risks)
    assert [q.question_key for q in v.open_questions] == ["etapas:instrucao"]
    v = ens.answer(tid, v.open_questions[0].id, "Pode ficar assim por enquanto.")
    v = await ens.propose(tid)
    assert v.session.status is TeachingStatus.VALIDATING
    assert ens.validate(v.current.id).session.status is TeachingStatus.READY          # type: ignore[union-attr]


async def test_idempotencia_do_pedido_de_candidata(harness: Harness) -> None:
    ens = _servico(harness.state)
    tid = ens.start("Abrir o QA Messenger", app_id="qa-messenger").session.id
    await ens.propose(tid, idempotency_key="ensino-chave-1")
    v = await ens.propose(tid, idempotency_key="ensino-chave-1")      # repetição: nenhuma chamada nova
    assert harness.ai.count("generalize") == 1 and len(v.candidates) == 1


# ================================================================== credencial nunca entra
async def test_credencial_recusada_na_instrucao_e_na_resposta(harness: Harness) -> None:
    st = harness.state
    ens = _servico(st)
    for instrucao in (f"Entrar no app com senha: {SEGREDO}", "Use o token eyJhbGciOiJIUzI1NiJ9.abcdefghijk",
                      "Digite o código 482913 quando pedir", f"Entrar e digitar {SEGREDO} no campo"):
        with pytest.raises(CredentialInText):
            ens.start(instrucao, app_id="qa-messenger")
    assert st.db.scalar("SELECT COUNT(*) FROM teaching_sessions") == 0

    tid = ens.start("Abrir o QA Messenger", app_id="qa-messenger").session.id
    v = await ens.propose(tid)
    pergunta = v.open_questions[0]
    for resposta in (SEGREDO, "a senha é Abc12345", "o código é 482913", "password=hunter22"):
        with pytest.raises(CredentialInText):
            ens.answer(tid, pergunta.id, resposta)
    assert ens.get(tid).open_questions[0].id == pergunta.id           # continua sem resposta
    despejo = _despejo(st)
    for segredo in (SEGREDO, "Abc12345", "482913", "hunter22", "eyJhbGciOiJIUzI1NiJ9"):
        assert segredo not in despejo, segredo


class _ProvedorQueVaza:
    """Generalizador que devolve credencial no documento (o que um modelo real poderia alucinar)."""

    model = "vazador"
    simulated = True

    def __init__(self, exemplo: str) -> None:
        self.exemplo = exemplo

    async def generalize(self, req: Any) -> tuple[dict[str, object], Usage]:
        return ({"summary": "Entrar", "command_template": "entre com {usuario} usando {chave_de_acesso}",
                 "parameters": [{"name": "usuario", "example": "tadeu.quintela4821", "description": ""},
                                {"name": "chave_de_acesso", "example": self.exemplo, "description": ""}],
                 "steps": [{"key": "entrar", "title": "Entrar", "goal": "entrar", "inputs": [], "side_effect": False,
                            "capability": None, "bindings": [], "app_id": None,
                            "postcondition": {"kind": "text_visible", "value": "Conta: {usuario}",
                                              "description": "logado"}}],
                 "discarded": [], "questions": []}, Usage(calls=1, model="vazador"))


@pytest.mark.parametrize("vazado", [SEGREDO, "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.xyz"])
async def test_candidata_com_valor_de_segredo_e_rejeitada_e_gravada_mascarada(harness: Harness, vazado: str) -> None:
    st = harness.state
    ens = _servico(st)
    ens._generalizer = ProviderSkillGeneralizer(_ProvedorQueVaza(vazado), st.db)      # noqa: SLF001
    tid = ens.start("Entrar no QA Messenger", app_id="qa-messenger").session.id
    v = await ens.propose(tid)
    c = v.current
    assert c is not None and c.status is CandidateStatus.REJECTED
    assert v.session.status is TeachingStatus.OPEN                   # volta a aceitar um pedido novo
    nota = [t for t in v.turns if t.kind is TurnKind.NOTE][-1]
    assert nota.body and nota.body.startswith("secret_in_candidate")
    assert vazado not in _despejo(st)                                  # nem no documento, nem na anotação
    assert "**REDACTED**" in json.dumps(c.envelope.to_json(), ensure_ascii=False)
    with pytest.raises(TeachingStateConflict):
        ens.validate(c.id)                                            # rejeitada não valida nem vira versão


# ================================================================== transação da submissão
async def test_submissao_falha_no_meio_e_nao_deixa_lixo(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    ens = _servico(st)
    tid = ens.start("Abrir o QA Messenger", app_id="qa-messenger").session.id
    v = await ens.propose(tid)
    v = ens.answer(tid, v.open_questions[0].id, "Pode ficar assim.")
    v = await ens.propose(tid)
    cid = v.current.id                                                # type: ignore[union-attr]
    ens.validate(cid)
    repo = ens._repo                                                  # noqa: SLF001
    original = repo.move

    def quebra(teaching_id: str, frm: TeachingStatus, to: TeachingStatus, **kw: Any) -> None:
        if to is TeachingStatus.PUBLISHED:
            raise RuntimeError("queda simulada depois do rascunho criado")
        original(teaching_id, frm, to, **kw)

    monkeypatch.setattr(repo, "move", quebra)
    with pytest.raises(RuntimeError):
        ens.publish(cid, by="painel:flavio")
    # o rascunho criado dentro da transação some junto: nem definição, nem versão, nem transição
    for tabela in ("skill_definitions", "skill_versions", "skill_version_transitions"):
        assert st.db.scalar(f"SELECT COUNT(*) FROM {tabela}") == 0, tabela
    v = ens.get(tid)
    assert v.session.status is TeachingStatus.READY and v.session.result_version_id is None
    assert v.current is not None and v.current.status is CandidateStatus.PROPOSED and v.current.version_id is None
    monkeypatch.setattr(repo, "move", original)
    v = ens.publish(cid, by="painel:flavio")
    assert v.session.status is TeachingStatus.PUBLISHED
    assert st.db.scalar("SELECT COUNT(*) FROM skill_versions") == 1


async def test_id_existente_nao_vira_versao_de_outra_habilidade(harness: Harness) -> None:
    ens = _servico(harness.state)

    async def pronto(instrucao: str) -> str:
        tid = ens.start(instrucao, app_id="qa-messenger").session.id
        v = await ens.propose(tid)
        v = ens.answer(tid, v.open_questions[0].id, "ok")
        v = await ens.propose(tid)
        cid = v.current.id                                            # type: ignore[union-attr]
        ens.validate(cid)
        return cid

    primeira = await pronto("Abrir o QA Messenger")
    ens.publish(primeira, by="painel:flavio")
    segunda = await pronto("Abrir o QA Messenger")                     # mesma instrução → mesmo id sugerido
    with pytest.raises(TeachingStateConflict):
        ens.publish(segunda, by="painel:flavio")
    assert harness.state.db.scalar("SELECT COUNT(*) FROM skill_versions") == 1


# ================================================================== demonstração, correção, descarte
async def test_demonstracao_so_liga_gravacao_valida_e_uma_vez(harness: Harness) -> None:
    st, ens, tid, sid = await _ensino_com_gravacao(harness)
    outro = ens.start("outra coisa", app_id="qa-messenger").session.id
    with pytest.raises(TeachingStateConflict) as dona:
        ens.attach_recording(outro, sid)                               # uma gravação, um ensino
    assert dona.value.code == "recording_taken"
    from app.modules.skills.domain.teaching import TeachingInputInvalid, TeachingNotFound
    with pytest.raises(TeachingNotFound):
        ens.attach_recording(outro, "trn-nao-existe")
    with pytest.raises(TeachingNotFound):
        ens.attach_run(outro, "r-nao-existe")
    with pytest.raises(TeachingInputInvalid) as sem_skill:
        ens.add_correction(outro, "o botão é outro", run_id="r-x", step_id="r-x:a:v1:b")
    assert sem_skill.value.code == "correction_needs_skill"
    v = ens.discard(outro)
    assert v.session.status is TeachingStatus.DISCARDED and v.session.closed_at
    with pytest.raises(TeachingStateConflict):
        ens.discard(outro)


async def test_gravacao_ainda_gravando_deixa_o_ensino_demonstrando(harness: Harness) -> None:
    st, rt, lease = await _no_controle(harness)
    gravacao = st.training.start("android-01", intent="Mandar mensagem", lease_id=lease, app_id="qa-messenger")
    ens = _servico(st)
    tid = ens.start("Mandar mensagem", app_id="qa-messenger").session.id
    assert ens.attach_recording(tid, gravacao["id"]).session.status is TeachingStatus.DEMONSTRATING
    with pytest.raises(TeachingStateConflict) as gravando:
        await ens.propose(tid)
    assert gravando.value.code == "still_recording"
    st.training.stop(gravacao["id"], lease_id=lease)                                  # o gravador não avisa; a leitura acomoda
    assert ens.get(tid).session.status is TeachingStatus.OPEN


# ================================================================== HTTP
async def test_rotas_desligadas_respondem_404_explicito_e_o_treino_segue(harness: Harness) -> None:
    st = harness.state
    assert st.cfg.file.skills.enabled is False                        # padrão da instalação (decisão P1)
    async with _cliente(harness) as c:
        assert (await c.get("/api/health")).json()["features"]["skills"] is False
        assert (await c.get("/api/health")).json()["features"]["ensino_v2"] is False   # 31.91 F1: a tela, desligada
        for metodo, caminho in (("GET", "/api/skills"), ("POST", "/api/teaching-sessions"),
                                ("GET", "/api/teaching-sessions/ens-x"), ("GET", "/api/skill-candidates/cand-x")):
            r = await c.request(metodo, caminho, json={} if metodo == "POST" else None)
            assert r.status_code == 404 and r.json()["detail"]["code"] == "skills_disabled", (caminho, r.text)
        assert (await c.get("/api/training")).status_code == 200      # o legado não passa pelo interruptor


async def test_rotas_ligadas_do_ensino_ao_rascunho_e_ciclo_da_versao(harness: Harness) -> None:
    st, rt, lease = await _no_controle(harness)
    sid = await _gravar_mensagem(st, rt, lease, harness.fakes["android-01"])
    st.cfg.file.skills.enabled = True
    async with _cliente(harness) as c:
        assert (await c.get("/api/health")).json()["features"]["skills"] is True
        assert (await c.get("/api/health")).json()["features"]["ensino_v2"] is False     # a tela tem chave própria
        st.cfg.file.skills.ensino_v2_na_tela = True
        assert (await c.get("/api/health")).json()["features"]["ensino_v2"] is True      # 31.91 F1: ligada, aparece
        r = await c.post("/api/teaching-sessions", json={"instruction": f"senha: {SEGREDO}"})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "credential_in_text"
        assert SEGREDO not in r.text
        r = await c.post("/api/teaching-sessions", json={"instruction": "Mandar mensagem no QA Messenger para",
                                                         "app_id": "qa-messenger"})
        assert r.status_code == 201, r.text
        tid = r.json()["id"]
        r = await c.post(f"/api/teaching-sessions/{tid}/demonstrations", json={"training_session_id": sid})
        assert r.status_code == 200 and r.json()["source"] == "hybrid", r.text
        assert (await c.post(f"/api/teaching-sessions/{tid}/demonstrations", json={})).status_code == 400
        r = await c.post(f"/api/teaching-sessions/{tid}/demonstrations", json={"run_id": "r-nao-existe"})
        assert r.status_code == 404
        r = await c.post(f"/api/teaching-sessions/{tid}/corrections",
                         json={"body": "o botão é outro", "run_id": "r-x", "step_id": "r-x:s"})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "correction_needs_skill"

        r = await c.post(f"/api/teaching-sessions/{tid}/candidates", json={"idempotency_key": "http-ensino-1"})
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["status"] == "asking" and corpo["current_candidate"]["document"]["kind"] == "Skill"
        pergunta = corpo["open_questions"][0]
        assert pergunta["kind"] == "effect_confirmation" and pergunta["origin"] == "ai"
        r = await c.post(f"/api/teaching-sessions/{tid}/answers", json={"question_id": pergunta["id"],
                                                                        "body": "Abc12345"})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "credential_in_text"
        r = await c.post(f"/api/teaching-sessions/{tid}/answers", json={"question_id": pergunta["id"],
                                                                        "body": "Sim, é enviar."})
        assert r.status_code == 200 and r.json()["open_questions"] == []
        r = await c.post(f"/api/teaching-sessions/{tid}/candidates")
        assert r.status_code == 200 and r.json()["status"] == "validating", r.text
        cid = r.json()["current_candidate"]["id"]

        r = await c.get(f"/api/skill-candidates/{cid}")
        assert r.status_code == 200 and r.json()["compile"]["ok"] is True
        assert (await c.post(f"/api/skill-candidates/{cid}/compile")).json()["errors"] == []
        r = await c.post(f"/api/skill-candidates/{cid}/publish")
        assert r.status_code == 409                                    # sem validar
        r = await c.post(f"/api/skill-candidates/{cid}/validate", json={"mode": "static"})
        assert r.status_code == 200 and r.json()["status"] == "ready", r.text
        r = await c.post(f"/api/skill-candidates/{cid}/publish", json={"idempotency_key": "http-publica-1"})
        assert r.status_code == 200 and r.json()["status"] == "published", r.text
        ref = r.json()["result_version_id"]
        skill_id, versao = ref.split("@")

        lista = (await c.get("/api/teaching-sessions")).json()
        assert lista[0]["id"] == tid and lista[0]["candidate_count"] == 2
        por_gravacao = (await c.get(f"/api/teaching-sessions?training_session_id={sid}")).json()
        assert [x["id"] for x in por_gravacao] == [tid]
        assert (await c.get("/api/teaching-sessions?training_session_id=trn-outra")).json() == []
        assert [s["ref"] for s in (await c.get("/api/skills")).json()] == [ref]
        detalhe = (await c.get(f"/api/skills/{skill_id}")).json()
        assert detalhe["versions"][0]["state"] == "draft"
        r = await c.get(f"/api/skills/{skill_id}/versions/{versao}")
        assert r.status_code == 200 and r.json()["source_kind"] == "teaching"
        assert r.json()["content"]["metadata"]["id"] == skill_id
        r = await c.post(f"/api/skills/{skill_id}/versions/{versao}/status", json={"to": "candidate"})
        assert r.status_code == 200 and r.json()["state"] == "candidate", r.text
        r = await c.post(f"/api/skills/{skill_id}/versions/{versao}/status", json={"to": "published"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "transition_forbidden"
        r = await c.put(f"/api/skills/{skill_id}/scope", json={"profile_ids": [], "group_ids": []})
        assert r.status_code == 200
        r = await c.post(f"/api/skills/{skill_id}/rollback", json={"version": int(versao)})
        assert r.status_code == 409                                    # só `deprecated` volta a publicar
        assert (await c.get("/api/skills/nao-existe")).status_code == 404
        assert (await c.get(f"/api/skills/{skill_id}/versions/99")).status_code == 404

        outro = (await c.post("/api/teaching-sessions", json={"instruction": "x", "app_id": "qa-messenger"})).json()
        r = await c.post(f"/api/teaching-sessions/{outro['id']}/discard")
        assert r.status_code == 200 and r.json()["status"] == "discarded"
        assert (await c.get(f"/api/teaching-sessions/{outro['id']}")).json()["status"] == "discarded"
    assert SEGREDO not in _despejo(st)
