"""Roteamento das execuções por persona, de ponta a ponta no harness (onda C; design persona-e-parque §7.3–§7.7).
Nível de prova: `simulated` (aparelhos falsos na porta 5640, planejador simulado contado pelo `CountingProvider`).

O que se prova:
- a prévia (`POST /runs/targets/resolve`) não cria execução nem chama o planejador;
- persona em dois aparelhos: `one` escolhe o aparelho com sessão pronta e, entre dois com sessão, o LIGADO (pelo
  balanceamento); `all` cria um objetivo em cada, com o `profile_id` do alvo;
- `profile_ids` com `instance_ids` é INTERSEÇÃO; o mesmo aparelho para duas personas → 409;
- destino tirado do texto nunca executa sem eco (409 `alvos_nao_confirmados`), e o eco em `targets` executa;
- contradição entre a seleção e o texto → execução em `needs_input` com pergunta estruturada, sem plano;
- a persona do OBJETIVO é a do alvo mesmo com duas personas no aparelho (a porta de sessão recebe essa).
"""
from __future__ import annotations

import secrets as pysecrets
from typing import Any

import httpx
import pytest

from app.db import loads
from app.main import create_app
from app.models import (InstanceState, PersonaDeviceBody, ProfileCreate, RunCreate, RunTarget,
                        RunTargetsResolveBody, SessionStatus)
from app.taskqueue.service import RunError

from .conftest import COMMAND, Harness

pytestmark = pytest.mark.asyncio


def _persona(h: Harness, nome: str, instance: str | None) -> str:
    assert h.state is not None
    return h.state.social.create_profile(ProfileCreate(
        username=f"{nome.lower()}.{pysecrets.token_hex(3)}", instance_id=instance, first_name=nome,
        last_name="Teste")).id


def _pronta(h: Harness, pid: str, iid: str) -> None:
    assert h.state is not None
    repo = h.state.social_repo
    conta = repo.conta_ancora(pid)
    repo.set_account_session(pid, conta["id"], iid, status=SessionStatus.session_ready, verified_at="2026-09-28T00:00:00Z")


def _chave() -> str:
    return f"rot-{pysecrets.token_hex(6)}"


def _previa(h: Harness, **campos: Any) -> list[tuple[str, str | None, str]]:
    assert h.state is not None
    p = h.state.runs.previa_de_alvos(RunTargetsResolveBody(command=COMMAND, **campos))
    assert not p.questions, p.questions
    return [(t.instance_id, t.profile_id, t.origem) for t in p.targets]


async def test_persona_em_dois_aparelhos_one_prefere_sessao_e_ligado_all_cria_dois(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    andre = _persona(harness, "Andre", "android-01")
    st.social.bind_device(andre, PersonaDeviceBody(instance_id="android-02", app_id="instagram"))
    planos = harness.ai.count("plan")

    # Sem sessão pronta em lugar nenhum: o principal.
    assert _previa(harness, profile_ids=[andre]) == [("android-01", andre, "vinculo")]
    # Sessão pronta só no android-02: lá, mesmo não sendo o principal.
    _pronta(harness, andre, "android-02")
    assert _previa(harness, profile_ids=[andre]) == [("android-02", andre, "vinculo")]
    # Sessão pronta nos dois e os dois ligados: o principal (29.65). O principal desligado cede ao secundário
    # ligado, pelo balanceamento (que escolhe o LIGADO): preferir o principal não pode acordar aparelho à toa.
    _pronta(harness, andre, "android-01")
    r1, r2 = st.devices.get("android-01"), st.devices.get("android-02")
    await harness.wait(lambda: r1.state == InstanceState.online and r2.state == InstanceState.online,
                       what="aparelhos online")
    try:
        assert _previa(harness, profile_ids=[andre]) == [("android-01", andre, "vinculo")]
        r1.state = InstanceState.stopped
        assert _previa(harness, profile_ids=[andre]) == [("android-02", andre, "balanceamento")]
        r1.state, r2.state = InstanceState.online, InstanceState.stopped
        assert _previa(harness, profile_ids=[andre]) == [("android-01", andre, "vinculo")]
    finally:
        r1.state = r2.state = InstanceState.online
    # `primary` ignora a sessão e vai no principal; `all` pega os dois.
    assert _previa(harness, profile_ids=[andre], device_policy="primary") == [("android-01", andre, "vinculo")]
    assert _previa(harness, profile_ids=[andre], device_policy="all") == [
        ("android-01", andre, "vinculo"), ("android-02", andre, "vinculo")]
    # Interseção: a persona e os aparelhos escolhidos.
    assert _previa(harness, profile_ids=[andre], instance_ids=["android-02", "android-03"]) == [
        ("android-02", andre, "ui")]
    assert harness.ai.count("plan") == planos                      # prévia não planeja

    run = st.runs.create(RunCreate(command=COMMAND, profile_ids=[andre], device_policy="all", mode="plan",
                                   idempotency_key=_chave()))
    await harness.wait_run(run.id, ("planned",))
    objetivos = st.db.query("SELECT instance_id, profile_id FROM objectives WHERE run_id=? ORDER BY instance_id",
                            (run.id,))
    assert [(o["instance_id"], o["profile_id"]) for o in objetivos] == [("android-01", andre), ("android-02", andre)]
    foto = loads(st.repo.run_row(run.id)["targets"])
    assert [a["origem"] for a in foto["alvos"]] == ["vinculo", "vinculo"] and foto["device_policy"] == "all"
    assert harness.ai.count("plan") == planos + 1


async def test_duas_personas_no_mesmo_aparelho_o_objetivo_leva_a_do_alvo(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    andre = _persona(harness, "Andre", "android-01")
    bruno = _persona(harness, "Bruno", None)
    st.social.bind_device(bruno, PersonaDeviceBody(instance_id="android-01", app_id="qa-messenger"))
    # O mesmo aparelho para as duas numa execução: 409 (fase 1: um objetivo por aparelho por execução).
    with pytest.raises(RunError) as exc:
        st.runs.create(RunCreate(command=COMMAND, idempotency_key=_chave(), targets=[
            RunTarget(profile_id=andre, instance_ids=["android-01"]),
            RunTarget(profile_id=bruno, instance_ids=["android-01"])]))
    assert (exc.value.code, exc.value.status) == ("aparelho_repetido_na_execucao", 409)
    # Pelo aparelho, sem dizer quem: pergunta (a execução nasce em `needs_input`, sem plano).
    planos = harness.ai.count("plan")
    run = st.runs.create(RunCreate(command=COMMAND, instance_ids=["android-01"], idempotency_key=_chave()))
    await harness.wait_run(run.id, ("needs_input",))
    assert not st.db.scalar("SELECT COUNT(*) FROM objectives WHERE run_id=?", (run.id,))
    assert "mais de uma persona" in (st.repo.run_row(run.id)["status_detail"] or "")
    assert harness.ai.count("plan") == planos
    # Pela persona: o objetivo leva o Bruno, não "o primeiro vínculo do aparelho".
    run = st.runs.create(RunCreate(command=COMMAND, profile_ids=[bruno], mode="plan", idempotency_key=_chave()))
    await harness.wait_run(run.id, ("planned",))
    assert st.db.scalar("SELECT profile_id FROM objectives WHERE run_id=?", (run.id,)) == bruno


async def test_destino_do_texto_exige_eco_e_contradicao_vira_pergunta(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    andre = _persona(harness, "Andre", "android-01")
    lucas = _persona(harness, "Lucas", "android-02")
    comando = f"{COMMAND} Faça isso com a persona Lucas."
    # A seleção tem os dois; o texto estreita para o Lucas → não executa sem o eco.
    with pytest.raises(RunError) as exc:
        st.runs.create(RunCreate(command=comando, profile_ids=[andre, lucas], idempotency_key=_chave()))
    assert exc.value.code == "alvos_nao_confirmados" and exc.value.status == 409
    assert [(a["instance_id"], a["profile_id"]) for a in exc.value.details["targets"]] == [("android-02", lucas)]
    assert "persona" not in exc.value.details["command_sem_destinos"]
    # A prévia mostra a origem; o eco em `targets` executa, e o comando que vai ao planejador vem sem o destino.
    previa = st.runs.previa_de_alvos(RunTargetsResolveBody(command=comando, profile_ids=[andre, lucas]))
    assert [(t.instance_id, t.profile_id, t.origem) for t in previa.targets] == [("android-02", lucas, "texto")]
    alvos = [RunTarget(profile_id=t.profile_id, instance_ids=[t.instance_id]) for t in previa.targets
             if t.profile_id]
    run = st.runs.create(RunCreate(command=comando, targets=alvos, mode="plan", idempotency_key=_chave()))
    await harness.wait_run(run.id, ("planned",))
    assert st.db.scalar("SELECT profile_id FROM objectives WHERE run_id=?", (run.id,)) == lucas
    assert st.repo.run_row(run.id)["command"] == comando              # o texto fica como veio
    assert loads(st.repo.run_row(run.id)["targets"])["command_sem_destinos"] == previa.command_sem_destinos
    # As prévias de casamento (`/flows/match`, `/skills/resolve`) veem o MESMO comando sem destinos.
    assert st.runs.sem_destinos(comando) == previa.command_sem_destinos == f"{COMMAND} Faça isso."
    # Contradição: a seleção é o android-03 (sem persona); o texto fala do Lucas → pergunta, nunca escolha.
    run = st.runs.create(RunCreate(command=comando, instance_ids=["android-03"], idempotency_key=_chave()))
    await harness.wait_run(run.id, ("needs_input",))
    assert not st.db.scalar("SELECT COUNT(*) FROM objectives WHERE run_id=?", (run.id,))


async def test_rota_da_previa_pelo_http(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    lucas = _persona(harness, "Lucas", "android-02")
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    planos = harness.ai.count("plan")
    runs_antes = st.db.scalar("SELECT COUNT(*) FROM runs")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/runs/targets/resolve", json={"command": "peça para o Lucas abrir o QA Messenger"})
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["targets"] == [{"instance_id": "android-02", "profile_id": lucas, "app_id": None,
                                     "origem": "texto", "motivo": None, "app_ids": []}]
        assert corpo["command_sem_destinos"] == "abrir o QA Messenger" and corpo["questions"] == []
        r = await c.post("/api/runs/targets/resolve", json={"command": "abra o app"})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "sem_alvo"
        r = await c.post("/api/runs/targets/resolve", json={"command": "abra o app", "profile_ids": [lucas],
                                                           "instance_ids": ["android-01"]})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "sem_intersecao"
        r = await c.post("/api/runs", json={"command": "abra o app", "idempotency_key": _chave(),
                                            "targets": [{"profile_id": lucas}], "distribute": {"count": 1,
                                                                                               "app_id": "qa-messenger"}})
        assert r.status_code == 422                                   # `targets` e `distribute` não se misturam
    assert harness.ai.count("plan") == planos
    assert st.db.scalar("SELECT COUNT(*) FROM runs") == runs_antes
