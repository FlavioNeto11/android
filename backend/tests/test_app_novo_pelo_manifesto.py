"""Fase K1: um app novo entra por manifesto + provedor + catálogo, sem tocar no núcleo — provado com o QA Messenger.

O QA Messenger é o app de teste de sempre, que o núcleo trata pelo caminho livre (sem catálogo, sem sessão). Aqui ele
é registrado SÓ por `register_manifest()` (`fake_dois_apps.manifesto_do_qa`), e o que muda é todo do registro:

1. a porta de sessão, a invalidação por app e a composição passam a falar com o provedor do QA (`SessaoDoQa`),
   fabricado com as dependências da composição, sem nenhum `if` novo — e o Instagram continua com o dele;
2. uma skill `automation/v1alpha1` sobre 3 capabilities do QA compila e executa pelo caminho de skills (registro →
   compilador → `Plan` → executor) com `skills.enabled`;
3. um PROCESSO CROSS-APP: skill composta que reusa `ig.abrir_conversa@1` (Instagram) e depois manda uma mensagem no
   QA, executada num aparelho com os dois apps (`AparelhoComDoisApps`).

Nível de prova: `simulated` — Harness (porta base 5640), `FakeQaDevice`, `FakeInstagram`, atores por regras envoltos
no `CountingProvider`. Nenhum aparelho, conta ou IA real: a prova `real` fica `not_run`.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

import pytest
import pytest_asyncio

from app.models import Plan, ProfileCreate, SessionStatus
from app.modules.skills.domain.lifecycle import SkillState
from app.modules.skills.domain.refs import SkillRef
from app.modules.skills.domain.versions import Provenance, SourceKind
from app.planning.capabilities import capability_of, load_catalog
from app.planning.catalog import (capabilities_of, register_manifest, screen_reader_of, session_factory_of,
                                  session_provider_of, unregister)
from app.state import AppState
from app.util import now, to_iso

from .conftest import CountingProvider, Harness
from .fake_device import PKG as QA
from .fake_device import FakeQaDevice
from .fake_dois_apps import (TIPO_DE_SESSAO_DO_QA, AparelhoComDoisApps, AtorDosDoisApps, SessaoDoQa,
                             manifesto_do_qa)
from .fake_instagram import PKG as IG
from .fake_instagram import FakeInstagram

DONO = "painel:flavio"
IID = "android-01"


@pytest.fixture
def app_qa() -> Iterator[None]:
    """O QA registrado pelo manifesto, e tirado no fim — o registro é global ao processo."""
    antes = capabilities_of(QA)
    assert not antes.has_catalog and antes.session_provider is None, "o QA já estava registrado: teste contaminado"
    register_manifest(manifesto_do_qa())
    try:
        yield
    finally:
        unregister(QA)


def estado(h: Harness) -> AppState:
    assert h.state is not None
    return h.state


def publicar(s: AppState, documento: dict[str, Any]) -> SkillRef:
    """Rascunho → candidata → validada À MÃO pelo dono, com motivo (P4) → publicada. Sem caso observado: estes
    documentos existem só neste teste, e o que se prova é a execução."""
    repo = s.skill_repo
    skill_id = documento["metadata"]["id"]
    v = repo.create_draft(skill_id, documento, source=Provenance(SourceKind.MANUAL), by=DONO)
    repo.transition(v.ref, SkillState.CANDIDATE, by=DONO, reason="submetida")
    repo.transition(v.ref, SkillState.VALIDATED, by=DONO, manual=True,
                    reason="teste da fase K1: extensibilidade do registro de apps")
    repo.transition(v.ref, SkillState.PUBLISHED, by=DONO, reason="fase K1")
    return v.ref


def etapas(s: AppState, run_id: str) -> dict[str, Any]:
    return {r["key"]: r for r in s.db.query("SELECT * FROM steps WHERE run_id=? ORDER BY seq", (run_id,))}


async def executar(h: Harness, comando: str) -> Any:
    run = h.run([IID], command=comando)
    detalhe = await h.wait_run(run.id, timeout=60)
    assert detalhe.status == "completed", (detalhe.status, detalhe.status_detail)
    return estado(h).repo.run_row(run.id)


# ==================================================================== 1. o registro é o único ponto de entrada
async def test_app_novo_entra_so_pelo_registro_com_provedor_e_catalogo(harness: Harness, app_qa: None) -> None:
    s = estado(harness)
    # o que o manifesto declara é o que o núcleo passa a ver — e só isso
    definicao = capabilities_of(QA)
    assert (definicao.has_catalog, definicao.session_provider, definicao.label) == (
        True, TIPO_DE_SESSAO_DO_QA, "QA Messenger")
    assert [c.key for c in load_catalog(QA).offered] == ["QA_OPEN_CHAT", "QA_COMPOSE", "QA_SEND_MESSAGE"]
    assert capability_of(QA, "QA_SEND_MESSAGE").side_effect
    assert screen_reader_of(QA) is None and session_factory_of(QA) is SessaoDoQa
    assert session_provider_of(IG) == "instagram"                      # o Instagram segue com o dele

    # o provedor é fabricado com as dependências DESTA composição, uma vez, e é o mesmo para quem perguntar
    provedor = s.sessoes.for_package(QA)
    assert isinstance(provedor, SessaoDoQa) and provedor.deps is not None and provedor.deps.repo is s.social_repo
    assert s.sessoes.for_package(QA) is provedor
    assert s.sessoes.for_package(IG) is s.instagram and not isinstance(s.instagram, SessaoDoQa)

    # a porta de sessão é do provedor do PACOTE do item: IG → autenticador do Instagram; QA → provedor do QA
    rt = s.devices.get(IID)
    pid = s.social.create_profile(ProfileCreate(username="conta.do.qa", instance_id=IID)).id
    # Desde a 047 a porta só considera que a pessoa tem conta num app quando há `profile_accounts` daquele app (o
    # usuário de cadastro vale só para o app do perfil, o Instagram). Sem esta linha, o QA responderia "sem conta".
    conta_qa = s.social_repo.create_account(pid, app_id="qa-messenger", handle="conta.do.qa")
    ancora = s.social_repo.conta_ancora(pid)["id"]
    vencida = to_iso(now() - timedelta(seconds=s.social_repo.session_max_age_s + 60))
    # A sessão é da CONTA de cada app (item 23.4): as duas vencidas, cada uma na sua conta.
    s.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id=IID, verified_at=vencida)
    s.social_repo.set_account_session(pid, conta_qa, IID, status=SessionStatus.session_ready, verified_at=vencida)
    do_instagram: list[dict[str, Any]] = []

    async def instagram_falso(rt_: Any, profile_id: str, **kw: Any) -> None:
        do_instagram.append(kw)

    s.instagram.ensure_session = instagram_falso  # type: ignore[union-attr,method-assign]
    await s._session_gate(rt, IG)[1]()  # noqa: SLF001
    assert do_instagram == [{"account_id": ancora, "observe_only": True}] and provedor.chamadas == []
    await s._session_gate(rt, QA)[1]()  # noqa: SLF001
    assert provedor.chamadas == [(IID, pid, False, False, True)] and len(do_instagram) == 1
    assert provedor.contas == [conta_qa]                               # a porta do QA pede a conta DO QA
    sessao = s.social_repo.account_session_row(pid, conta_qa, IID)     # gravada pelo provedor do QA, na conta dele
    assert (sessao["status"], sessao["detail"]) == ("session_ready", "conta do QA Messenger aberta na tela")
    assert s.social_repo.session_row(pid, IID)["verified_at"] == vencida   # a da âncora ficou como estava
    assert s._session_gate(rt, QA) is None  # noqa: SLF001                  # fresca: a porta abre

    # mexer no disco do QA agora invalida — ele declara conta gerenciada — e só a conta DELE
    s.releases._app_mudou(IID, QA, "o aplicativo foi instalado neste aparelho")  # noqa: SLF001
    assert s.social_repo.account_session_row(pid, conta_qa, IID)["status"] == SessionStatus.unknown.value
    assert s.social_repo.session_row(pid, IID)["status"] == SessionStatus.session_ready.value

    # fora do registro, o QA volta ao caminho livre, sem reiniciar nada
    unregister(QA)
    assert s.sessoes.for_package(QA) is None and s._session_gate(rt, QA) is None  # noqa: SLF001
    assert load_catalog(QA) is None and capabilities_of(QA).session_provider is None


# ==================================================================== 2. skill v1alpha1 sobre o QA
SKILL_QA: dict[str, Any] = {
    "apiVersion": "automation/v1alpha1", "kind": "Skill",
    "metadata": {"id": "qa.enviar_mensagem", "name": "Enviar mensagem no QA Messenger",
                 "description": "Abre a conversa com um contato do QA Messenger e manda uma mensagem.",
                 "app": "qa-messenger"},
    "spec": {
        "invocation": {"command_template": "mande {message} para {recipient} no qa messenger"},
        "parameters": [{"name": "recipient", "type": "string", "required": True, "example": "QA-001"},
                       {"name": "message", "type": "text", "required": True, "example": "oi"}],
        "requires": {"apps": ["qa-messenger"]},
        "nodes": [
            {"id": "open_conversation", "capability": "QA_OPEN_CHAT", "depends_on": [],
             "with": {"recipient": "${parameters.recipient}"}, "strategies": ["recipe", "ai_actor"]},
            {"id": "compose_message", "capability": "QA_COMPOSE", "depends_on": ["open_conversation"],
             "with": {"message": "${parameters.message}"}, "strategies": ["recipe", "ai_actor"]},
            {"id": "send_message", "capability": "QA_SEND_MESSAGE", "depends_on": ["compose_message"],
             "with": {"recipient": "${parameters.recipient}", "message": "${parameters.message}"},
             "strategies": ["recipe", "ai_actor"]},
        ],
        "success_criteria": ["A mensagem aparece na conversa com ${parameters.recipient}."],
    },
}


@pytest_asyncio.fixture
async def parque_qa(tmp_path: Path) -> AsyncIterator[Harness]:
    """Um aparelho de QA (o padrão do harness), habilidades ligadas."""
    h = Harness(tmp_path, 1)
    h.cfg.file.skills.enabled = True
    await h.boot()
    await h.medir_a_internet()   # T.2: a sonda do monitor agora; o Instagram não espera os 6 s da 1ª volta
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_skill_do_qa_compila_e_executa_pelo_caminho_de_skills(parque_qa: Harness, app_qa: None) -> None:
    s, ai = estado(parque_qa), parque_qa.ai
    ref = publicar(s, SKILL_QA)
    run = await executar(parque_qa, "mande Oi do K1 para QA-001 no qa messenger")

    assert ai.count("plan") == 0                                         # o planejador não foi chamado
    plano = Plan.model_validate_json(run["plan"])
    assert (plano.planner.provider, plano.planner.model) == ("skill", "skill:qa.enviar_mensagem@1")
    assert plano.parameters == {"recipient": "QA-001", "message": "Oi do K1"} and plano.app_package == QA
    assert (run["skill_id"], run["skill_version"], run["skill_hash"]) == (
        "qa.enviar_mensagem", 1, s.skill_repo.get(ref).content_hash)
    passos = etapas(s, run["id"])
    assert {k: (p["capability"], p["status"]) for k, p in passos.items()} == {
        "open_conversation": ("QA_OPEN_CHAT", "succeeded"), "compose_message": ("QA_COMPOSE", "succeeded"),
        "send_message": ("QA_SEND_MESSAGE", "succeeded")}
    # a etapa com efeito saiu do catálogo do QA: uma tentativa só, seletor de commit e guardas do contrato
    envio = passos["send_message"]
    assert (envio["side_effect"], envio["max_attempts"], envio["commit_selector"]) == (1, 1, "id=send_button")
    assert json.loads(envio["commit_guard"]) == ["QA-001", "Oi do K1"]
    fake = parque_qa.fakes[IID]
    assert isinstance(fake, FakeQaDevice)
    assert [(m.contact, m.body) for m in fake.messages] == [("QA-001", "Oi do K1")]     # exatamente uma vez


# ==================================================================== 3. processo cross-app
IG_ABRIR_CONVERSA = Path(__file__).parent / "fixtures" / "dsl" / "v1alpha1" / "validos" / "ig.abrir_conversa.yaml"

SKILL_CROSS_APP: dict[str, Any] = {
    "apiVersion": "automation/v1alpha1", "kind": "Skill",
    "metadata": {"id": "x.conversa_ig_aviso_qa", "name": "Abrir a conversa no Instagram e avisar no QA",
                 "description": "Reusa ig.abrir_conversa e depois manda um aviso a um contato do QA Messenger.",
                 "app": "instagram"},
    "spec": {
        "invocation": {"command_template": "abra a conversa com {username} no instagram e avise {recipient} no qa "
                                           "messenger: {message}"},
        "parameters": [{"name": "username", "type": "handle", "required": True, "example": "@ana.teste"},
                       {"name": "recipient", "type": "string", "required": True, "example": "QA-001"},
                       {"name": "message", "type": "text", "required": True, "example": "vi a conversa"}],
        "requires": {"apps": ["instagram", "qa-messenger"]},
        "uses": [{"skill": "ig.abrir_conversa", "version": 1}],
        "nodes": [
            {"id": "abrir", "skill": "ig.abrir_conversa", "depends_on": [],
             "with": {"username": "${parameters.username}"}},
            {"id": "open_conversation", "app": "qa-messenger", "capability": "QA_OPEN_CHAT", "depends_on": ["abrir"],
             "with": {"recipient": "${parameters.recipient}"}},
            {"id": "compose_message", "app": "qa-messenger", "capability": "QA_COMPOSE",
             "depends_on": ["open_conversation"], "with": {"message": "${parameters.message}"}},
            {"id": "send_message", "app": "qa-messenger", "capability": "QA_SEND_MESSAGE",
             "depends_on": ["compose_message"],
             "with": {"recipient": "${parameters.recipient}", "message": "${parameters.message}"}},
        ],
    },
}


@pytest_asyncio.fixture
async def parque_dois_apps(tmp_path: Path) -> AsyncIterator[Harness]:
    """Um aparelho com o Instagram (logado, no feed, na frente) e o QA Messenger (fechado), habilidades ligadas."""
    h = Harness(tmp_path, 1, factory=lambda rt: AparelhoComDoisApps(FakeInstagram(account="eu.teste", screen="feed"),
                                                                   FakeQaDevice(account="qa-user-01")))
    h.ai = CountingProvider(AtorDosDoisApps())
    h.cfg.file.skills.enabled = True
    await h.boot()
    await h.medir_a_internet()   # T.2: a sonda do monitor agora; o Instagram não espera os 6 s da 1ª volta
    s = estado(h)
    s.db.execute("UPDATE instances SET app_id='instagram' WHERE id=?", (IID,))
    s.devices.get(IID).app_id = "instagram"
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_processo_cross_app_instagram_e_qa_num_aparelho_so(parque_dois_apps: Harness, app_qa: None) -> None:
    import yaml

    s, ai = estado(parque_dois_apps), parque_dois_apps.ai
    publicar(s, yaml.safe_load(IG_ABRIR_CONVERSA.read_text(encoding="utf-8")))
    publicar(s, SKILL_CROSS_APP)
    run = await executar(parque_dois_apps,
                         "abra a conversa com @ana no instagram e avise QA-001 no qa messenger: vi a ana")

    assert ai.count("plan") == 0
    plano = Plan.model_validate_json(run["plan"])
    assert plano.planner.model == "skill:x.conversa_ig_aviso_qa@1"
    assert plano.required_apps == ["instagram", "qa-messenger"]
    assert plano.parameters == {"username": "@ana", "recipient": "QA-001", "message": "vi a ana"}
    passos = etapas(s, run["id"])
    # a filha do Instagram, na ordem, e depois as três do QA — cada uma no app dela
    assert {k: (p["capability"], p["app_id"], p["skill_id"]) for k, p in passos.items()} == {
        "abrir_abrir_inbox": ("OPEN_INBOX", None, "ig.abrir_conversa"),
        "abrir_abrir_conversa": ("OPEN_THREAD", None, "ig.abrir_conversa"),
        "open_conversation": ("QA_OPEN_CHAT", "qa-messenger", "x.conversa_ig_aviso_qa"),
        "compose_message": ("QA_COMPOSE", "qa-messenger", "x.conversa_ig_aviso_qa"),
        "send_message": ("QA_SEND_MESSAGE", "qa-messenger", "x.conversa_ig_aviso_qa")}
    assert json.loads(passos["open_conversation"]["depends_on"]) == ["abrir_abrir_conversa"]
    assert all(p["status"] == "succeeded" for p in passos.values())
    # cada etapa foi conduzida pelo ator do app dela (as do QA pelas chaves do simulado, as do IG pelo objetivo)
    assert ai.count("decide", step="abrir_abrir_conversa") >= 1 and ai.count("decide", step="send_message") >= 1
    aparelho = parque_dois_apps.fakes[IID]
    assert isinstance(aparelho, AparelhoComDoisApps)
    assert (aparelho.instagram.screen, aparelho.instagram.thread_with) == ("thread", "ana")     # efeito no Instagram
    assert [(m.contact, m.body) for m in aparelho.qa.messages] == [("QA-001", "vi a ana")]     # efeito no QA
    assert QA in aparelho.aberturas and aparelho.frente is aparelho.qa     # o executor abriu o QA pelo pacote dele
