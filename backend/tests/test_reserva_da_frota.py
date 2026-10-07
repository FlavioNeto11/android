"""31.240: a reserva de frota por alvo faz o espaçamento entre contas valer quando as aprovações saem juntas.

Onda 2 de 07/10 (op-20261007100755-096a28): três contas aprovadas no mesmo instante passaram a porta juntas, antes de
qualquer efeito existir para o espaçamento contar, e dois comentários saíram a 0,4 s um do outro.

O que estes testes protegem:
* três contas aprovadas juntas sobre o mesmo alvo, cada uma passando como a porta passa (o `check` com o espaçamento e
  depois `reservar_frota`), têm os efeitos espaçados em pelo menos `fleet_min_spacing_between_accounts_s`; sem a
  reserva, o mesmo roteiro sai em rajada (o defeito);
* a reserva é de quem chegou primeiro, cai quando o dono registra o efeito, quando a etapa dele termina ou quando vence,
  e a própria conta a renova; alvo diferente não espera; sem espaçamento configurado, nada é reservado.

Nível de prova: `simulated` (banco de teste com relógio trocado; nenhum aparelho).
"""
from __future__ import annotations

import heapq
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.models import InteractionStatus, InteractionType
from app.planning.capabilities import capability_of
from app.social import policy as politica
from app.social.policy import CONTAM, PolicyEngine
from app.social.reserva_da_frota import RESERVA_TTL_S, ReservaDaFrota
from app.util import to_iso

from .test_capabilities import IG, _FleetSettings, build, perfil

ALVO = "@pagina.alvo"
T0 = datetime(2026, 10, 7, 10, 12, 51, tzinfo=timezone.utc)
ESPACO_S, ATE_O_EFEITO_S = 120, 20


def _contas(tmp_path: Path) -> tuple[Any, Any, list[str]]:
    svc, repo, _, db = build(tmp_path)
    contas = [perfil(svc, "tadeu.quintela4821", "android-01"), perfil(svc, "luciana.bastos73519", "android-02"),
              perfil(svc, "marina.fontes20417", None)]  # type: ignore[arg-type]
    for pid in contas:
        repo.update_profile(pid, {"automation_policy": '{"limits": {"warmup_days": 0, '
                                                       '"cooldown_between_external_actions_s": 0}}'})
    return svc, db, contas


def _rodar(svc: Any, policies: PolicyEngine, contas: list[str], relogio: list[datetime], *,
           com_reserva: bool) -> list[datetime]:
    """As contas aprovadas em T0 passam a porta como `_policy_gate`: o `check`, e depois a reserva. Quem passa registra
    o efeito `ATE_O_EFEITO_S` depois; quem é adiado volta no `retry_at`. Devolve a hora de cada efeito."""
    cap = capability_of(IG, "LIKE_POST")
    fila: list[tuple[datetime, int, str, str]] = [(T0, 1, "porta", pid) for pid in contas]
    heapq.heapify(fila)
    efeitos: list[datetime] = []
    while fila:
        quando, _, oque, pid = heapq.heappop(fila)
        relogio[0] = quando
        if oque == "efeito":
            svc.record_interaction(pid, type=InteractionType.post_liked.value, direction="outbound",
                                   status=InteractionStatus.confirmed.value, counterparty=ALVO, run_id=f"run-{pid}",
                                   occurred_at=to_iso(quando))
            efeitos.append(quando)
            continue
        veredito = policies.check(pid, cap, run_id=f"run-{pid}", counterparty=ALVO, step_id=f"st-{pid}")
        retry: str | None = veredito.retry_at if not veredito.allowed else None
        if veredito.allowed and com_reserva:
            adiada = policies.reservar_frota(pid, cap, ALVO, app_id=None, step_id=f"st-{pid}")
            retry = adiada[1] if adiada else None
        if retry is None:
            heapq.heappush(fila, (quando + timedelta(seconds=ATE_O_EFEITO_S), 0, "efeito", pid))
        else:
            assert veredito.allowed or veredito.is_wait, veredito.reason        # adiado, nunca recusado
            heapq.heappush(fila, (datetime.fromisoformat(retry.replace("Z", "+00:00")), 1, "porta", pid))
    return sorted(efeitos)


@pytest.mark.parametrize("com_reserva", [True, False])
def test_tres_aprovadas_juntas_saem_espacadas_so_com_a_reserva(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                com_reserva: bool) -> None:
    svc, _db, contas = _contas(tmp_path)
    relogio = [T0]
    monkeypatch.setattr(politica, "now", lambda: relogio[0])
    policies = PolicyEngine(svc.repo, lambda: _FleetSettings(max_contas=5, espaco_s=ESPACO_S, jitter_s=0))
    efeitos = _rodar(svc, policies, contas, relogio, com_reserva=com_reserva)
    assert len(efeitos) == 3
    intervalos = [(b - a).total_seconds() for a, b in zip(efeitos, efeitos[1:])]
    if com_reserva:
        assert min(intervalos) >= ESPACO_S, intervalos
    else:                                                # o defeito da onda 2: sem a reserva, rajada
        assert max(intervalos) == 0, intervalos


def test_a_reserva_e_de_quem_chega_primeiro_e_cai_no_efeito_do_dono(tmp_path: Path) -> None:
    svc, db, (a, b, _c) = _contas(tmp_path)
    reserva = ReservaDaFrota(db)
    assert reserva.tomar(app_id="instagram", alvo=ALVO, profile_id=a, step_id="st-a", statuses=CONTAM, agora=T0) is None
    retry = reserva.tomar(app_id="instagram", alvo=ALVO, profile_id=b, step_id="st-b", statuses=CONTAM, agora=T0)
    assert retry == to_iso(T0 + timedelta(seconds=30))                   # confere de novo logo, não espera o TTL
    # a própria conta renova; outro alvo e outro app não esperam
    assert reserva.tomar(app_id="instagram", alvo=ALVO, profile_id=a, step_id="st-a2", statuses=CONTAM, agora=T0) is None
    assert reserva.tomar(app_id="instagram", alvo="@outra", profile_id=b, step_id="st-b", statuses=CONTAM, agora=T0) is None
    assert reserva.tomar(app_id="outro-app", alvo=ALVO, profile_id=b, step_id="st-b", statuses=CONTAM, agora=T0) is None
    # o efeito do dono libera
    svc.record_interaction(a, type=InteractionType.post_liked.value, direction="outbound",
                           status=InteractionStatus.confirmed.value, counterparty=ALVO, run_id="run-a",
                           occurred_at=to_iso(T0 + timedelta(seconds=5)))
    depois = T0 + timedelta(seconds=10)
    assert reserva.tomar(app_id="instagram", alvo=ALVO, profile_id=b, step_id="st-b", statuses=CONTAM, agora=depois) is None


def test_a_reserva_cai_quando_vence_ou_a_etapa_do_dono_termina(tmp_path: Path) -> None:
    _svc, db, (a, b, c) = _contas(tmp_path)
    estados: dict[str, str] = {}

    class _Reserva(ReservaDaFrota):
        def _estado_da_etapa(self, etapa: str) -> str | None:
            return estados.get(etapa)

    reserva = _Reserva(db)
    assert reserva.tomar(app_id=None, alvo=ALVO, profile_id=a, step_id="st-a", statuses=CONTAM, agora=T0) is None
    vencida = T0 + timedelta(seconds=RESERVA_TTL_S + 1)
    assert reserva.tomar(app_id=None, alvo=ALVO, profile_id=b, step_id="st-b", statuses=CONTAM, agora=vencida) is None
    # b é dona agora; com a etapa dela em curso (ou não achada), c espera; terminada sem efeito, c assume na hora
    logo = vencida + timedelta(seconds=1)
    assert reserva.tomar(app_id=None, alvo=ALVO, profile_id=c, step_id="st-c", statuses=CONTAM, agora=logo) is not None
    estados["st-b"] = "running"
    assert reserva.tomar(app_id=None, alvo=ALVO, profile_id=c, step_id="st-c", statuses=CONTAM, agora=logo) is not None
    estados["st-b"] = "failed"
    assert reserva.tomar(app_id=None, alvo=ALVO, profile_id=c, step_id="st-c", statuses=CONTAM, agora=logo) is None


def test_sem_espacamento_configurado_nada_e_reservado(tmp_path: Path) -> None:
    svc, db, (a, b, _c) = _contas(tmp_path)
    policies = PolicyEngine(svc.repo, lambda: _FleetSettings(max_contas=5, espaco_s=0, jitter_s=0))
    cap = capability_of(IG, "LIKE_POST")
    assert policies.reservar_frota(a, cap, ALVO, app_id=None, step_id="st-a") is None
    assert policies.reservar_frota(b, cap, ALVO, app_id=None, step_id="st-b") is None
    assert db.scalar("SELECT COUNT(*) FROM travas WHERE nome LIKE 'frota:%'") == 0


# ---------------------------------------------------------------- pela porta de verdade (`_policy_gate`)
async def _comentario_aprovado_por(harness: Any, run_id: str, instancia: str, usuario: str) -> tuple[str, str]:
    """Uma execução própria (como o alvo de uma operação) com CREATE_COMMENT para o mesmo autor, aprovado. Devolve
    (objetivo, etapa). Com `pause_requested=1` o despacho não pega o objetivo: a ordem das portas é a do teste."""
    from app.models import PersonaCreate, PersonaTraits, Plan, PlannerInfo, PlanStep, Postcondition, ProfileCreate, \
        ProfilePatch
    from .test_capabilities import SENHA
    state = harness.state
    db = state.db
    if db.one("SELECT id FROM apps WHERE id='ig'") is None:
        db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("UPDATE instances SET app_id='ig' WHERE id=?", (instancia,))
    pid = state.social.create_profile(ProfileCreate(username=usuario, password=SENHA, instance_id=instancia)).id
    persona = state.social.create_persona(PersonaCreate(name=usuario[:8], traits=PersonaTraits(tone="Direto")))
    state.social.update_profile(pid, ProfilePatch(persona_id=persona.id))
    oid, sid = f"{run_id}:{instancia}", f"{run_id}:{instancia}:v1:c1"
    post = Postcondition(kind="model_judged", value="x", description="y")
    plano = Plan(summary="comentar", app_id="ig", planner=PlannerInfo(provider="fake", model="t", simulated=True),
                 steps=[PlanStep(key="c1", title="Comentar", goal="comentar", side_effect=True, postcondition=post,
                                 max_attempts=1, capability="CREATE_COMMENT", commit_selector="id=post",
                                 bindings={"content_brief": "elogiar o post", "post_author": "@autora"})])
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
               " plan, pause_requested) VALUES (?,?,'comentar','execute','running',1,?,'2026-10-07T10:07:55Z',?,1)",
               (run_id, f"k-{run_id}", json.dumps([instancia]), plano.model_dump_json()))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
               " VALUES (?,?,?,'running',1,'{}',?)", (oid, run_id, instancia, pid))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
        " bindings) VALUES (?,?,?,?,1,1,'c1','Comentar','comentar','[]',1,'[]',"
        "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready','CREATE_COMMENT',"
        "'id=post','{\"content_brief\": \"elogiar o post\", \"post_author\": \"@autora\"}')",
        (sid, run_id, oid, instancia))
    run = state.repo.run_row(run_id)
    veredito = await state._policy_gate(state.repo.objective_row(oid), state.repo.step_row(sid), run)
    assert veredito is not None and veredito.policy == "approval_required"
    state.scheduler._hold(state.repo.objective_row(oid), state.repo.step_row(sid), veredito)  # noqa: SLF001
    pedido = next(p for p in state.approval_service.list() if p["run_id"] == run_id)
    state.approval_service.decide(pedido["id"], "approve")
    return oid, sid


async def test_pela_porta_a_segunda_aprovada_e_adiada_ate_o_efeito_da_primeira(harness: Any) -> None:
    state = harness.state
    a = await _comentario_aprovado_por(harness, "run-a", "android-01", "tadeu.quintela4821")
    b = await _comentario_aprovado_por(harness, "run-b", "android-02", "luciana.bastos73519")
    # as duas aprovadas: a primeira a passar reserva o alvo; a segunda é ADIADA (não recusada), com o motivo
    assert await state._policy_gate(state.repo.objective_row(a[0]), state.repo.step_row(a[1]),
                                    state.repo.run_row("run-a")) is None
    adiada = await state._policy_gate(state.repo.objective_row(b[0]), state.repo.step_row(b[1]),
                                      state.repo.run_row("run-b"))
    assert adiada is not None and not adiada.allowed and adiada.retry_at and "31.240" in adiada.reason
    assert state.db.scalar("SELECT COUNT(*) FROM travas WHERE nome LIKE 'frota:%'") == 1
