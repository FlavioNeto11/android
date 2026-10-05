"""31.88 F2: a escolha de escopo do salvar (`scope_on_proof`) e o gesto de ampliar ou restringir depois.

O 30.81 já prende o fluxo ensinado à persona que ensinou ENQUANTO espera a prova. A escolha diz o que vale depois:
`todos` (padrão, a lista do corpo ou vazio = todos) ou `quem_ensinou` (o escopo permanente é a persona do treino). O
`match` respeita o escopo gravado com a prova feita, e só uma pessoa o muda depois (`PUT /api/flows/{id}/scope`),
com o antes e o depois no evento.

Nível de prova: `simulated` (aparelho falso do harness, sem IA)."""
from __future__ import annotations

from typing import Any

import pytest

from app.models import ProfileCreate
from app.training.recorder import TrainingError

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente
from .test_treino_previa_e_refazer_receitas import _proposta, _sessao_mista


def _persona(st: Any, nome: str, *, aparelho: bool = True) -> str:
    """Duas contas do mesmo app não dividem o aparelho: a segunda persona nasce sem aparelho."""
    return st.social.create_profile(ProfileCreate(username=nome, instance_id="android-01" if aparelho else None)).id


def _ensinou(st: Any, sid: str, persona: str | None) -> None:
    st.db.execute("UPDATE training_sessions SET profile_id=? WHERE id=?", (persona, sid))


def _escopo(st: Any, flow_id: str) -> dict[str, list[str]]:
    return st.scheduler.flows.scope(flow_id)


async def test_padrao_todos_guarda_so_a_lista_do_corpo(harness: Harness) -> None:
    st, _rt, _lease, sid = await _sessao_mista(harness)
    _ensinou(st, sid, _persona(st, "ensinou.um"))
    salvo = await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert salvo["scope"] == {"on_proof": "todos", "profile_ids": [], "group_ids": []}
    assert _escopo(st, salvo["flow_id"]) == {"profile_ids": [], "group_ids": []}      # vazio = todos, como antes


async def test_quem_ensinou_grava_a_persona_do_treino_como_escopo(harness: Harness) -> None:
    st, _rt, _lease, sid = await _sessao_mista(harness)
    persona = _persona(st, "ensinou.dois")
    _ensinou(st, sid, persona)
    previa = await st.skills.preview(sid, proposal=_proposta(), profile_ids=[], group_ids=[], scope_on_proof="quem_ensinou")
    assert previa["scope"] == {"on_proof": "quem_ensinou", "profile_ids": [persona], "group_ids": []}
    assert st.db.scalar("SELECT COUNT(*) FROM flow_scope") == 0                      # a prévia não escreve
    salvo = await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[], scope_on_proof="quem_ensinou")
    assert salvo["scope"] == previa["scope"]
    assert _escopo(st, salvo["flow_id"]) == {"profile_ids": [persona], "group_ids": []}
    flows = st.scheduler.flows
    outra = _persona(st, "outra.pessoa", aparelho=False)
    assert flows.match("responda a DM de QA-001 com oi", [persona]) is not None
    assert flows.match("responda a DM de QA-001 com oi", [outra]) is None            # nem depois da prova


@pytest.mark.parametrize(("persona", "lista", "codigo", "status"), [
    (None, {}, "no_teacher_persona", 409),                       # sem persona não existe "quem ensinou"
    ("com", {"profile_ids": ["qualquer"]}, "scope_ambiguous", 400),
    ("com", {"group_ids": ["qualquer"]}, "scope_ambiguous", 400),
])
async def test_quem_ensinou_recusa_sem_persona_e_com_lista_junto_sem_escrever(
        harness: Harness, persona: str | None, lista: dict[str, list[str]], codigo: str, status: int) -> None:
    st, _rt, _lease, sid = await _sessao_mista(harness)
    _ensinou(st, sid, _persona(st, "ensinou.tres") if persona else None)
    for operacao in (st.skills.save, st.skills.preview):
        with pytest.raises(TrainingError) as exc:
            await operacao(sid, proposal=_proposta(), profile_ids=lista.get("profile_ids", []),
                           group_ids=lista.get("group_ids", []), scope_on_proof="quem_ensinou")
        assert exc.value.code == codigo and exc.value.status == status
    assert st.db.scalar("SELECT COUNT(*) FROM flows") == 0 and st.db.scalar("SELECT COUNT(*) FROM flow_scope") == 0


async def test_a_rota_do_salvar_aceita_a_escolha_e_recusa_valor_estranho(harness: Harness) -> None:
    st, _rt, _lease, sid = await _sessao_mista(harness)
    persona = _persona(st, "ensinou.quatro")
    _ensinou(st, sid, persona)
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/save", json={"proposal": _proposta(), "scope_on_proof": "ninguem"})
        assert r.status_code == 422
        r = await c.post(f"/api/training/{sid}/save", json={"proposal": _proposta(), "scope_on_proof": "quem_ensinou"})
        assert r.status_code == 200, r.text
        assert r.json()["scope"] == {"on_proof": "quem_ensinou", "profile_ids": [persona], "group_ids": []}


async def test_a_pessoa_amplia_e_restringe_depois_com_o_antes_e_o_depois_no_evento(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st, _rt, _lease, sid = await _sessao_mista(harness)
    persona = _persona(st, "ensinou.cinco")
    outra = _persona(st, "outra.cinco", aparelho=False)
    _ensinou(st, sid, persona)
    salvo = await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[], scope_on_proof="quem_ensinou")
    flow_id = salvo["flow_id"]
    emitidos: list[dict[str, Any]] = []
    original = st.bus.emit
    monkeypatch.setattr(st.bus, "emit", lambda *a, **k: (emitidos.append({"msg": a[1], **(k.get("data") or {})}),
                                                         original(*a, **k))[1])
    async with _cliente(harness) as c:
        r = await c.put(f"/api/flows/{flow_id}/scope", json={"profile_ids": [persona, outra]})
        assert r.status_code == 200, r.text
        assert r.json() == {"flow_id": flow_id, "profile_ids": [persona, outra], "group_ids": []}
        r = await c.put(f"/api/flows/{flow_id}/scope", json={})                                  # vazio = todos
        assert r.json()["profile_ids"] == []
        assert (await c.put(f"/api/flows/{flow_id}/scope", json={"profile_ids": ["nao-existe"]})).status_code == 400
        assert (await c.put(f"/api/flows/{flow_id}/scope", json={"group_ids": ["nao-existe"]})).status_code == 400
        assert (await c.put("/api/flows/fluxo-que-nao-existe/scope", json={})).status_code == 404
    mudancas = [e for e in emitidos if e["msg"] == "Escopo da habilidade mudou"]
    assert len(mudancas) == 2                                                                    # só as duas que valeram
    assert mudancas[0]["antes"] == {"profile_ids": [persona], "group_ids": []}
    assert mudancas[0]["depois"] == {"profile_ids": [persona, outra], "group_ids": []}
    assert mudancas[1]["depois"] == {"profile_ids": [], "group_ids": []} and mudancas[0]["por"]
    # O escopo largo não libera a espera: até a prova (30.81) o fluxo só casa para a persona que ensinou.
    assert st.scheduler.flows.match("responda a DM de QA-001 com oi", [outra]) is None
    assert st.scheduler.flows.match("responda a DM de QA-001 com oi", [persona]) is not None
