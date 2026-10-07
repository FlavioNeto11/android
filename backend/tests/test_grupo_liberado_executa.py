"""31.253 / ADR-082: na operação que executa, a persona do grupo sem aprovação age sem pedir aprovação.

Decisão do dono em 07/10 ("colocar todas as personas em um grupo que libera tudo para não precisar de permissão pra
nada"). Até aqui todo alvo de operação nascia com o teto `preparar` (28.23), e o grupo do 28.61 só dispensava a
aprovação de POLÍTICA: o efeito ainda esperava o liberar ou as Pendências. Agora, com `acao_final=executar`, o alvo cuja
persona está no grupo nasce com o teto `agir`; fora do grupo, com `acao_final=preparar` ou com
`operacao_grupo_liberado_executa` desligado, segue `preparar`, como antes.

Nível de prova: `simulated` (harness na porta 5640; a porta `_policy_gate` de verdade, sem aparelho nem IA).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.config import LimitsCfg
from app.modules.operacoes.infrastructure.servico import AlvoPedido
from app.util import now_iso

from .conftest import Harness
from .test_operacoes import _alvo, _conta, _pedido, _persona, _servico

GRUPO = "grp-liberado-teste"


def _no_grupo(st: Any, *pids: str) -> None:
    if st.db.one("SELECT id FROM policy_groups WHERE id=?", (GRUPO,)) is None:
        st.db.execute("INSERT INTO policy_groups(id, name, created_at, updated_at) VALUES (?,?,?,?)",
                      (GRUPO, "Libera tudo (teste)", now_iso(), now_iso()))
    for pid in pids:
        st.db.execute("UPDATE instagram_profiles SET policy_group_id=? WHERE id=?", (GRUPO, pid))


def _teto(st: Any, op: dict[str, Any], pid: str) -> str:
    return str(st.repo.run_row(str(_alvo(op, pid)["run_id"]))["teto_de_autonomia"])


async def test_o_alvo_do_grupo_numa_operacao_que_executa_nasce_com_o_teto_agir(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    dentro, fora = _persona(harness, "Olivia", "android-02"), _persona(harness, "Nina", "android-03")
    _conta(harness, dentro, "qa-user-61", sessao_em="android-02")
    _conta(harness, fora, "qa-user-62", sessao_em="android-03")
    _no_grupo(st, dentro)
    st.settings.update({"grupo_sem_aprovacao": GRUPO})
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(dentro), AlvoPedido(fora)], chave="teste-op-253-a", acao_final="executar"))
    assert (_teto(st, op, dentro), _teto(st, op, fora)) == ("agir", "preparar")
    # a operação que só prepara continua preparando, mesmo para o grupo
    prepara = s.criar(_pedido([AlvoPedido(dentro)], chave="teste-op-253-b"))
    assert _teto(st, prepara, dentro) == "preparar"


async def test_desligado_ou_sem_grupo_configurado_tudo_volta_ao_preparar(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    assert LimitsCfg().operacao_grupo_liberado_executa is True              # padrão ligado (decisão do dono)
    pid = _persona(harness, "Olivia", "android-02")
    _conta(harness, pid, "qa-user-63", sessao_em="android-02")
    _no_grupo(st, pid)
    s = _servico(harness)
    st.settings.update({"grupo_sem_aprovacao": GRUPO, "operacao_grupo_liberado_executa": False})
    op = s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-253-c", acao_final="executar"))
    assert _teto(st, op, pid) == "preparar"
    st.settings.update({"grupo_sem_aprovacao": "", "operacao_grupo_liberado_executa": True})
    op = s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-253-d", acao_final="executar"))
    assert _teto(st, op, pid) == "preparar"


# ---------------------------------------------------------------- pela porta de verdade (`_policy_gate`)
async def _comentario(harness: Any, run_id: str, instancia: str, usuario: str, teto: str) -> tuple[str, str]:
    """Uma execução de alvo com CREATE_COMMENT, persona no grupo, o teto dado. Devolve (objetivo, etapa). Com
    `pause_requested=1` o despacho não pega o objetivo: quem passa pela porta é o teste."""
    from app.models import PersonaCreate, PersonaTraits, Plan, PlannerInfo, PlanStep, Postcondition, ProfileCreate, \
        ProfilePatch
    from .apoio_politica import IG, SENHA
    state = harness.state
    db = state.db
    if db.one("SELECT id FROM apps WHERE id='ig'") is None:
        db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("UPDATE instances SET app_id='ig' WHERE id=?", (instancia,))
    pid = state.social.create_profile(ProfileCreate(username=usuario, password=SENHA, instance_id=instancia)).id
    persona = state.social.create_persona(PersonaCreate(name=usuario[:8], traits=PersonaTraits(tone="Direto")))
    state.social.update_profile(pid, ProfilePatch(persona_id=persona.id))
    _no_grupo(state, pid)
    oid, sid = f"{run_id}:{instancia}", f"{run_id}:{instancia}:v1:c1"
    post = Postcondition(kind="model_judged", value="x", description="y")
    plano = Plan(summary="comentar", app_id="ig", planner=PlannerInfo(provider="fake", model="t", simulated=True),
                 steps=[PlanStep(key="c1", title="Comentar", goal="comentar", side_effect=True, postcondition=post,
                                 max_attempts=1, capability="CREATE_COMMENT", commit_selector="id=post",
                                 bindings={"content": "Que post lindo!", "post_author": "@autora"})])
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
               " plan, pause_requested, teto_de_autonomia) VALUES (?,?,'comentar','execute','running',1,?,?,?,1,?)",
               (run_id, f"k-{run_id}", json.dumps([instancia]), now_iso(), plano.model_dump_json(), teto))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
               " VALUES (?,?,?,'running',1,'{}',?)", (oid, run_id, instancia, pid))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
        " bindings) VALUES (?,?,?,?,1,1,'c1','Comentar','comentar','[]',1,'[]',"
        "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready','CREATE_COMMENT',"
        "'id=post','{\"content\": \"Que post lindo!\", \"post_author\": \"@autora\"}')",
        (sid, run_id, oid, instancia))
    return oid, sid


@pytest.mark.skip(reason="ADR-083: a dispensa de aprovação de política pelo grupo (28.61) saiu de policy.py")
async def test_pela_porta_o_teto_agir_do_grupo_libera_o_efeito_sem_pedido_de_aprovacao(harness: Any) -> None:
    state = harness.state
    state.settings.update({"grupo_sem_aprovacao": GRUPO})
    a = await _comentario(harness, "run-agir", "android-01", "tadeu.quintela4821", "agir")
    assert await state._policy_gate(state.repo.objective_row(a[0]), state.repo.step_row(a[1]),
                                    state.repo.run_row("run-agir")) is None          # o efeito sai
    assert state.db.scalar("SELECT COUNT(*) FROM pending_approvals WHERE run_id='run-agir'") == 0
    # o mesmo grupo com o teto preparar (o alvo de antes, ou fora da regra nova) ainda pede a aprovação pelo teto
    b = await _comentario(harness, "run-prep", "android-02", "luciana.bastos73519", "preparar")
    parada = await state._policy_gate(state.repo.objective_row(b[0]), state.repo.step_row(b[1]),
                                      state.repo.run_row("run-prep"))
    assert parada is not None and parada.policy == "approval_required" and "teto de autonomia preparar" in parada.reason
