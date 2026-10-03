"""RA-7 (reavaliação de 03/10): a porta de política do item 13.2 também no PLANEJAMENTO.

O caso real (r-20261001190557-e7bc42, 01/10): "enviar um e-mail pelo Outlook" virou abrir → preencher destinatário →
assunto → corpo → enviar, e os preparativos gastaram dezenas de decisões antes de a etapa com efeito chegar à porta do
despacho. Hoje o Outlook tem catálogo SÓ DE LEITURA (enviar e-mail é da pessoa), e o plano que pede o envio é recusado
INTEIRO antes de virar etapa: a execução vai a `needs_input`, o evento `plan.refused` leva o motivo fechado, nenhuma
etapa é materializada e nenhuma decisão é gasta.

O que se prova:
- a regra pura (`efeito_fora_do_catalogo`): sem catálogo passa, efeito sem ação recusa, ação de outro catálogo recusa;
- plano do planejador com envio de e-mail no Outlook: recusado, motivo `sem_acao_do_catalogo`, 0 etapas, 0 `decide`;
- a ação de outro app numa etapa do Outlook: `acao_de_outro_catalogo`;
- o mesmo plano vindo de um FLUXO (RESOLVE, sem planejador) também é recusado;
- o QA Messenger (sem catálogo) com `send_message` livre segue materializado, como os 12 fluxos ativos do central;
- a porta do despacho continua com a mesma frase (`test_porta_de_politica_por_app.py` segue verde).

Nível de prova: `simulated` (harness na porta 5640, provedor por roteiro, banco de teste; nenhum aparelho real, nenhuma
IA paga).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import Plan, PlannerInfo, PlanStep, Postcondition
from app.planning.capabilities import efeito_fora_do_catalogo
from app.planning.provider import PlanRequest, Usage
from app.planning.simulated_provider import SimulatedProvider

from .conftest import CountingProvider, Harness

IG = "com.instagram.android"
OUTLOOK = "com.microsoft.office.outlook"
QA = "com.pocqa.messenger"
POS = {"kind": "model_judged", "value": "x", "description": "y"}


def _passo(key: str, *, efeito: bool = False, capability: str | None = None, app_id: str | None = None) -> PlanStep:
    return PlanStep(key=key, title=key.replace("_", " "), goal=key, side_effect=efeito,
                    postcondition=Postcondition(**POS), max_attempts=1, capability=capability, app_id=app_id)


def _envio_de_email(*, capability: str | None = None) -> Plan:
    """O plano de 01/10, em miniatura: os preparativos sem efeito e o envio com efeito, todos livres."""
    return Plan(summary="Enviar um e-mail pelo Outlook", app_id="outlook", app_package=OUTLOOK, required_apps=["outlook"],
                planner=PlannerInfo(provider="roteiro", model="t", simulated=True),
                steps=[_passo("open_compose"), _passo("fill_recipient"), _passo("fill_subject"),
                       _passo("send_email", efeito=True, capability=capability)])


class Roteiro(SimulatedProvider):
    """Planeja sempre o mesmo plano (o resto é o provedor simulado de sempre)."""

    def __init__(self, plano: Plan) -> None:
        super().__init__()
        self.plano = plano

    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]:
        return self.plano.model_copy(deep=True), Usage(calls=1, role="plan", model="roteiro")


async def _parque(h: Harness, *, app_do_aparelho: str) -> Any:
    state = await h.boot()
    db = state.db
    if db.one("SELECT 1 FROM apps WHERE id='outlook'") is None:
        db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES "
                   "('outlook','Microsoft Outlook',?,'.Main',0)", (OUTLOOK,))
    db.execute("UPDATE instances SET app_id=? WHERE id='android-01'", (app_do_aparelho,))
    state.devices.get("android-01").app_id = app_do_aparelho
    return state


def _eventos(state: Any, run_id: str, kind: str) -> list[dict[str, Any]]:
    return [{"message": r["message"], "data": json.loads(r["data"]) if r["data"] else None}
            for r in state.db.query("SELECT message, data FROM events WHERE run_id=? AND kind=? ORDER BY id",
                                    (run_id, kind))]


def _contagem(state: Any, tabela: str, run_id: str) -> int:
    return int(state.db.scalar(f"SELECT count(*) FROM {tabela} WHERE run_id=?", (run_id,)))


# ------------------------------------------------------------------ a regra pura
@pytest.mark.parametrize(("efeito", "capability", "pacote", "esperado"), [
    (True, None, QA, None),                                     # app sem catálogo: livre, como sempre
    (True, None, None, None),                                   # sem app conhecido: o despacho decide
    (False, None, OUTLOOK, None),                               # leitura livre não é desta regra
    (True, None, OUTLOOK, "sem_acao_do_catalogo"),              # enviar e-mail no Outlook
    (True, "LIKE_POST", OUTLOOK, "acao_de_outro_catalogo"),     # ação do Instagram numa etapa do Outlook
    (True, "NAO_EXISTE", IG, "acao_de_outro_catalogo"),         # chave inventada conta como nenhuma
    (True, "LIKE_POST", IG, None),                              # a ação do catálogo do app: segue para a política
])
def test_regra_do_13_2(efeito: bool, capability: str | None, pacote: str | None, esperado: str | None) -> None:
    assert efeito_fora_do_catalogo(efeito, capability, pacote) == esperado


# ------------------------------------------------------------------ plano do planejador
async def test_envio_de_email_no_outlook_e_recusado_no_planejamento(tmp_path: Any) -> None:
    h = Harness(tmp_path, 1)
    h.ai = CountingProvider(Roteiro(_envio_de_email()))
    state = await _parque(h, app_do_aparelho="outlook")
    try:
        run = h.run(["android-01"], command="envie um e-mail pelo Outlook para a Ana")
        await h.wait_run(run.id, ("needs_input", "failed", "planned", "running", "completed",
                                  "completed_with_issues"))
        linha = state.repo.run_row(run.id)
        assert linha["status"] == "needs_input", linha["status_detail"]
        assert "Recusado no planejamento" in linha["status_detail"] and "send email" in linha["status_detail"]
        eventos = _eventos(state, run.id, "plan.refused")
        assert len(eventos) == 1
        dado = eventos[0]["data"]
        assert dado["motivo"] == "efeito_fora_do_catalogo"
        assert [(e["key"], e["motivo"], e["app_id"]) for e in dado["etapas"]] == [
            ("send_email", "sem_acao_do_catalogo", "outlook")]
        # a mesma frase da porta do despacho, na linha do tempo
        assert "sem a ação do catálogo" in eventos[0]["message"]
        assert _contagem(state, "steps", run.id) == 0 and _contagem(state, "objectives", run.id) == 0
        assert h.ai.count("decide") == 0 and h.ai.count("plan") == 1
        assert json.loads(linha["plan"])["steps"] == []                  # nada que `start()` possa executar depois
    finally:
        await state.stop()


async def test_acao_de_outro_app_numa_etapa_do_outlook(tmp_path: Any) -> None:
    h = Harness(tmp_path, 1)
    h.ai = CountingProvider(Roteiro(_envio_de_email(capability="LIKE_POST")))
    state = await _parque(h, app_do_aparelho="outlook")
    try:
        run = h.run(["android-01"], command="curta pelo Outlook", mode="plan")
        await h.wait_run(run.id, ("needs_input", "planned", "failed"))
        assert state.repo.run_row(run.id)["status"] == "needs_input"
        (etapa,) = _eventos(state, run.id, "plan.refused")[0]["data"]["etapas"]
        assert (etapa["motivo"], etapa["capability"]) == ("acao_de_outro_catalogo", "LIKE_POST")
        assert "(a ação LIKE_POST não é do catálogo dele)" in state.repo.run_row(run.id)["status_detail"]
    finally:
        await state.stop()


async def test_etapa_sem_app_herda_o_do_aparelho(tmp_path: Any) -> None:
    """Plano livre sem app (nem na etapa nem no plano): o app é o do aparelho, como no `_app_context`."""
    plano = _envio_de_email().model_copy(update={"app_id": None, "app_package": None, "required_apps": []})
    h = Harness(tmp_path, 1)
    h.ai = CountingProvider(Roteiro(plano))
    state = await _parque(h, app_do_aparelho="outlook")
    try:
        run = h.run(["android-01"], command="mande um e-mail", mode="plan")
        await h.wait_run(run.id, ("needs_input", "planned", "failed"))
        assert state.repo.run_row(run.id)["status"] == "needs_input"
        assert _eventos(state, run.id, "plan.refused")
    finally:
        await state.stop()


# ------------------------------------------------------------------ plano de fluxo (RESOLVE, sem planejador)
async def test_plano_de_fluxo_com_envio_tambem_e_recusado(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(tmp_path, 1)
    state = await _parque(h, app_do_aparelho="outlook")
    try:
        resolvido = SimpleNamespace(plan=_envio_de_email(), resolution=None, issues=(), resources=(), secrets=())
        monkeypatch.setattr(state.runs.skills, "for_command", lambda *_a, **_k: resolvido)
        monkeypatch.setattr(state.runs, "_registrar_resolucao", lambda *_a, **_k: None)
        run = h.run(["android-01"], command="envie o e-mail de sempre pelo Outlook", mode="plan")
        await h.wait_run(run.id, ("needs_input", "planned", "failed"))
        assert state.repo.run_row(run.id)["status"] == "needs_input"
        assert _eventos(state, run.id, "plan.refused")
        assert _contagem(state, "steps", run.id) == 0
        assert h.ai.count("plan") == 0                                   # o planejador nem foi chamado
    finally:
        await state.stop()


# ------------------------------------------------------------------ o que NÃO muda
async def test_qa_messenger_sem_catalogo_segue_livre_com_efeito(tmp_path: Any) -> None:
    """Os 12 fluxos ativos do central com `send_message` livre são do QA Messenger, que não tem catálogo."""
    plano = Plan(summary="mandar oi", app_id="qa-messenger", app_package=QA, required_apps=["qa-messenger"],
                 planner=PlannerInfo(provider="roteiro", model="t", simulated=True),
                 steps=[_passo("open_conversation"), _passo("send_message", efeito=True)])
    h = Harness(tmp_path, 1)
    h.ai = CountingProvider(Roteiro(plano))
    state = await _parque(h, app_do_aparelho="qa-messenger")
    try:
        run = h.run(["android-01"], command="mande oi no QA", mode="plan")
        await h.wait_run(run.id, ("needs_input", "planned", "failed"))
        assert state.repo.run_row(run.id)["status"] == "planned"
        assert not _eventos(state, run.id, "plan.refused")
        assert _contagem(state, "steps", run.id) == 2
    finally:
        await state.stop()
