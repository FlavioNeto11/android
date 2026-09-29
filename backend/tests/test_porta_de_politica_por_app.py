"""Item 24.2 (ADR-058, decisão 2): a porta de política julga cada etapa pelo app DELA, sem liberar efeito novo.

O que se prova, chamando `AppState._policy_gate` etapa por etapa (a execução fica com `pause_requested=1`, então o
despacho não pega o objetivo por conta própria):
- a mistura Outlook (leitura, app sem catálogo) + Instagram (efeito sem capability) segue recusada NA ETAPA DE
  EFEITO, com `manual_only`; a leitura no Outlook passa;
- uma capability que o app da etapa não tem conta como nenhuma: `NAO_EXISTE` numa etapa do Instagram com efeito é
  recusada (antes, `cap is None` liberava); `LIKE_POST` numa etapa do Outlook vale como etapa sem ação num app sem
  catálogo;
- a regra não muda (ADR-058, T19): app sem catálogo segue pela IA livre, sozinho ou num comando entre apps — é o
  comportamento decidido em `test_modo_treinamento.py::test_portao_recusa_efeito_sem_acao_num_app_com_catalogo`;
- a ação do catálogo do Instagram numa etapa do Instagram, dentro de um plano cujo app principal é o Outlook, é
  resolvida pelo app da etapa e segue para a política;
- o QA Messenger sozinho segue livre com efeito, como sempre.

Nível de prova: `simulated` (banco de teste do harness, porta 5640; nenhum aparelho real, nenhuma IA).
"""
from __future__ import annotations

import json
from typing import Any

from app.models import Plan, PlannerInfo, PlanStep, Postcondition
from app.planning.capabilities import load_catalog

from .conftest import Harness

IG = "com.instagram.android"
OUTLOOK = "com.microsoft.office.outlook"
RUN = "run-p"
OID = f"{RUN}:android-01"
POS = {"kind": "model_judged", "value": "x", "description": "y"}


def _preparar(harness: Harness, *, plano_app: str | None, required: list[str], device_app: str = "instagram",
              etapas: list[dict[str, Any]]) -> dict[str, Any]:
    """Execução, objetivo e etapas gravados direto no banco. `etapas`: key, app_id, side_effect, capability."""
    assert harness.state is not None
    db = harness.state.db
    if db.one("SELECT 1 FROM apps WHERE id='outlook'") is None:
        db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES "
                   "('outlook','Microsoft Outlook',?,'.Main',0)", (OUTLOOK,))
    db.execute("UPDATE instances SET app_id=? WHERE id='android-01'", (device_app,))
    plano = Plan(summary="entre apps", app_id=plano_app, required_apps=required,
                 planner=PlannerInfo(provider="fake", model="t", simulated=True),
                 steps=[PlanStep(key=e["key"], title=e["key"], goal=e["key"], side_effect=e["side_effect"],
                                 postcondition=Postcondition(**POS), max_attempts=1, capability=e.get("capability"),
                                 app_id=e["app_id"]) for e in etapas])
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
               " plan, pause_requested) VALUES (?,?,'entre apps','execute','running',1,'[\"android-01\"]',"
               "'2026-09-29T12:00:00Z',?,1)", (RUN, f"k-{RUN}", plano.model_dump_json()))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters)"
               " VALUES (?,?,'android-01','running',1,'{}')", (OID, RUN))
    for seq, e in enumerate(etapas, start=1):
        db.execute(
            "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
            " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, bindings,"
            " app_id) VALUES (?,?,?,'android-01',1,?,?,?,?,'[]',?,'[]',?,180,1,'ready',?,'{}',?)",
            (f"{OID}:v1:{e['key']}", RUN, OID, seq, e["key"], e["key"], e["key"], int(e["side_effect"]),
             json.dumps(POS), e.get("capability"), e["app_id"]))
    return harness.state.repo.run_row(RUN)


async def _porta(harness: Harness, key: str) -> Any:
    assert harness.state is not None
    state = harness.state
    return await state._policy_gate(state.repo.objective_row(OID), state.repo.step_row(f"{OID}:v1:{key}"),  # noqa: SLF001
                                    state.repo.run_row(RUN))


def test_premissa_outlook_sem_catalogo_e_instagram_com() -> None:
    """Se o Outlook ganhar catálogo (T17: só leitura), a mistura abaixo muda de natureza: o teste tem de ser revisto."""
    assert load_catalog(OUTLOOK) is None
    assert load_catalog(IG) is not None


async def test_outlook_leitura_mais_instagram_efeito_sem_capability_segue_recusada(harness: Harness) -> None:
    _preparar(harness, plano_app="instagram", required=["outlook", "instagram"], etapas=[
        {"key": "ler_email", "app_id": "outlook", "side_effect": False},
        {"key": "curtir", "app_id": None, "side_effect": True}])
    assert await _porta(harness, "ler_email") is None                  # leitura no app sem catálogo: IA livre
    veredito = await _porta(harness, "curtir")
    assert veredito is not None and not veredito.allowed and veredito.policy == "manual_only"
    assert "Instagram" in veredito.reason and "sem a ação do catálogo" in veredito.reason


async def test_capability_que_o_app_da_etapa_nao_tem_conta_como_nenhuma(harness: Harness) -> None:
    _preparar(harness, plano_app="instagram", required=["outlook", "instagram"], etapas=[
        {"key": "inventada", "app_id": None, "side_effect": True, "capability": "NAO_EXISTE"},
        {"key": "inventada_leitura", "app_id": None, "side_effect": False, "capability": "NAO_EXISTE"},
        {"key": "curtir_no_outlook", "app_id": "outlook", "side_effect": True, "capability": "LIKE_POST"}])
    inventada = await _porta(harness, "inventada")
    assert inventada is not None and not inventada.allowed and inventada.policy == "manual_only"
    assert "Instagram sem a ação do catálogo (a ação NAO_EXISTE não é do catálogo dele)" in inventada.reason
    assert await _porta(harness, "inventada_leitura") is None          # sem efeito: nada a recusar
    # A ação do Instagram numa etapa do Outlook não é julgada pelo catálogo do Instagram: no app DA ETAPA ela não
    # existe, e o Outlook não tem catálogo — segue como etapa livre desse app (a regra de sempre, T19).
    assert await _porta(harness, "curtir_no_outlook") is None


async def test_app_sem_catalogo_num_comando_entre_apps_segue_livre(harness: Harness) -> None:
    """A regra não muda (ADR-058 decisão 2): o app sem catálogo segue pela IA livre também quando o comando
    atravessa um app com catálogo. É o decidido em `test_modo_treinamento.py` (etapa `enviar_qa`)."""
    _preparar(harness, plano_app="instagram", required=["outlook", "instagram"], etapas=[
        {"key": "enviar_email", "app_id": "outlook", "side_effect": True},
        {"key": "abrir_feed", "app_id": None, "side_effect": False, "capability": "OPEN_FEED"}])
    assert await _porta(harness, "enviar_email") is None
    assert await _porta(harness, "abrir_feed") is None


async def test_capability_resolvida_pelo_app_da_etapa_e_nao_pelo_do_plano(harness: Harness) -> None:
    """Plano com principal no Outlook e aparelho do QA: a etapa do Instagram é julgada pelo catálogo do Instagram."""
    _preparar(harness, plano_app="outlook", required=["outlook", "instagram"], device_app="qa-messenger", etapas=[
        {"key": "ler_email", "app_id": None, "side_effect": False},
        {"key": "curtir", "app_id": "instagram", "side_effect": True, "capability": "LIKE_POST"},
        {"key": "curtir_solto", "app_id": "instagram", "side_effect": True}])
    assert await _porta(harness, "ler_email") is None
    # Resolvida no catálogo do Instagram, segue para a política. Sem perfil vinculado e sem texto a escrever, a
    # porta libera, como antes do 24.2 — pelo catálogo do Outlook (nenhum) ela nem seria achada.
    assert await _porta(harness, "curtir") is None
    solto = await _porta(harness, "curtir_solto")
    assert solto is not None and solto.policy == "manual_only" and "Instagram" in solto.reason


async def test_qa_messenger_sozinho_segue_livre_com_efeito(harness: Harness) -> None:
    _preparar(harness, plano_app="qa-messenger", required=["qa-messenger"], device_app="qa-messenger", etapas=[
        {"key": "abrir_conversa", "app_id": None, "side_effect": False},
        {"key": "enviar", "app_id": None, "side_effect": True}])
    assert await _porta(harness, "abrir_conversa") is None
    assert await _porta(harness, "enviar") is None
