"""31.285: o mesmo pedido a N contas sobre o mesmo alvo NÃO tem mais porta própria (ADR-083).

O dono mandou "seguir @alvo com todas as personas que têm Instagram"; a porta `_mesmo_pedido_noutras_contas` deixava
seguir só a conta do menor objetivo e recusava as outras ("esta execução manda o mesmo pedido a N contas… segue só a
de X"), além de exigir confirmação da conta escolhida. Era coordenação de frota, que o refactor do dono tirou. Daqui em
diante cada conta é julgada pela política do SEU perfil, como qualquer outra etapa.

Nível de prova: `simulated` (harness, catálogo do Instagram, banco de teste). Nada real.
"""
from __future__ import annotations

import json
from typing import Any

from app.gates import PortaDaEtapa
from app.models import ProfileCreate
from app.planning.capabilities import capability_of

from .apoio_politica import IG
from .test_porta_do_plano import ALVO, _plano

SEGUIR = {"username": ALVO}


def _segunda_conta(state: Any) -> None:
    """Um segundo objetivo na MESMA execução, de outra persona, com o mesmo FOLLOW sobre o mesmo alvo."""
    state.db.execute("UPDATE instances SET app_id='ig' WHERE id='android-02'")
    outra = state.social.create_profile(ProfileCreate(username="pessoa.dois", instance_id="android-02")).id
    state.db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
                     " VALUES ('run-p:android-02','run-p','android-02','pending',1,'{}',?)", (outra,))
    state.db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, bindings)"
        " VALUES ('run-p:android-02:v1:seguir','run-p','run-p:android-02','android-02',1,1,'seguir','seguir','x','[]',1,"
        "'[]','{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'pending','FOLLOW',?)",
        (json.dumps(SEGUIR),))


def _porta(state: Any, aparelho: str) -> PortaDaEtapa:
    etapa = state.db.one("SELECT * FROM steps WHERE id=?", (f"run-p:{aparelho}:v1:seguir",))
    return state.vereditos_da_porta(state.db.one("SELECT * FROM objectives WHERE id=?", (etapa["objective_id"],)),
                                    etapa, state.db.one("SELECT * FROM runs WHERE id='run-p'"))


async def test_o_mesmo_follow_em_duas_contas_nao_recusa_nenhuma_nem_exige_confirmacao_de_frota(harness: Any) -> None:
    state = harness.state
    assert capability_of(IG, "FOLLOW") is not None
    _plano(state, [{"key": "seguir", "cap": "FOLLOW", "bindings": SEGUIR}])
    _segunda_conta(state)
    for aparelho in ("android-01", "android-02"):
        porta = _porta(state, aparelho)
        v = porta.veredito
        assert v is not None, aparelho
        assert "mesmo pedido" not in (v.reason or "") and "contas sobre" not in (v.reason or ""), (aparelho, v.reason)
        assert not hasattr(porta, "confirmacao") and not hasattr(porta, "registrar_confirmacao")
    # nenhuma decisão de "confirmação exigida" foi gravada
    assert not state.db.scalar("SELECT COUNT(*) FROM events WHERE message LIKE '%confirmação exigida%'")
