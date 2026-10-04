"""30.31: o ensaio só de leitura para ANTES da porta de política.

Com catálogo, a etapa de efeito em `approval_required` (o CREATE_COMMENT do Instagram) era segurada pela porta antes
de o ensaio a ver: o texto era escrito pela IA (pago), o pedido de aprovação abria, o dono era avisado, e o objetivo
ia para `waiting_user` em vez de fechar `cancelled`. O que se prova, pelo laço de verdade (`Scheduler._work`):
- no ensaio, a etapa CREATE_COMMENT fecha `skipped` e o objetivo `cancelled`, com zero texto escrito, zero aprovação
  e zero evento de aprovação;
- fora do ensaio, a mesma etapa segue segurada pela porta (o pedido de aprovação abre e o objetivo espera).

Armação: o Harness (porta 5640), com o app `ig` do catálogo e um perfil no aparelho, como em
`test_capabilities.py::test_porta_de_politica_cria_aprovacao_e_segura_a_etapa`. Nível de prova: `simulated`.
"""
from __future__ import annotations

from typing import Any

from app.contracts.origem import PREFIXO_ENSAIO
from app.models import ProfileCreate

from .test_capabilities import IG, SENHA

OID = "run-e:android-01"
ETAPA = f"{OID}:v1:comment_1"


def _comentario(harness: Any, chave: str) -> None:
    """Um objetivo de uma etapa só: CREATE_COMMENT (efeito, `approval_required` pelo catálogo), pronta para rodar."""
    state = harness.state
    db = state.db
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("UPDATE instances SET app_id='ig' WHERE id='android-01'")
    pid = state.social.create_profile(ProfileCreate(username="mariana.costa91182", password=SENHA,
                                                    instance_id="android-01")).id
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-e',?,'comente','execute','running',1,'[\"android-01\"]','2026-10-04T11:00:00Z')",
               (chave,))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
               " VALUES (?,'run-e','android-01','running',1,'{}',?)", (OID, pid))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
        " bindings) VALUES (?,'run-e',?,'android-01',1,1,'comment_1','Comentar na publicação','comentar','[]',1,'[]',"
        "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready','CREATE_COMMENT',"
        "'id=layout_comment_thread_post_button_icon',"
        "'{\"content_brief\": \"elogiar o post\", \"post_author\": \"@autora\"}')", (ETAPA, OID))


def _avisos_de_aprovacao(harness: Any) -> int:
    return int(harness.state.db.scalar("SELECT COUNT(*) FROM events WHERE run_id='run-e' AND kind LIKE '%approval%'"))


async def test_o_ensaio_para_antes_da_porta_sem_rascunho_sem_aprovacao_e_sem_aviso(harness: Any) -> None:
    state = harness.state
    _comentario(harness, f"{PREFIXO_ENSAIO}ig-comentar-01")
    escritos = harness.ai.count("social")
    await state.scheduler._work(OID, state.devices.get("android-01"))  # o laço de verdade
    db = state.db
    assert db.scalar("SELECT status FROM objectives WHERE id=?", (OID,)) == "cancelled"
    assert db.scalar("SELECT status FROM steps WHERE id=?", (ETAPA,)) == "skipped"
    assert db.scalar("SELECT COUNT(*) FROM attempts WHERE step_id=?", (ETAPA,)) == 0
    assert harness.ai.count("social") == escritos                       # nenhum texto escrito (pago)
    assert state.approval_service.list() == []                          # nenhum pedido de aprovação
    assert db.scalar("SELECT COUNT(*) FROM pending_approvals") == 0
    assert _avisos_de_aprovacao(harness) == 0                           # nenhum aviso ao dono
    assert db.scalar("SELECT blocked_kind FROM objectives WHERE id=?", (OID,)) is None


async def test_fora_do_ensaio_a_porta_segue_segurando_a_etapa(harness: Any) -> None:
    state = harness.state
    _comentario(harness, "k-comum")
    await state.scheduler._work(OID, state.devices.get("android-01"))
    db = state.db
    pendentes = state.approval_service.list()
    assert [a["capability"] for a in pendentes] == ["CREATE_COMMENT"]
    assert db.scalar("SELECT blocked_kind FROM objectives WHERE id=?", (OID,)) == "approval"
    assert db.scalar("SELECT status FROM steps WHERE id=?", (ETAPA,)) != "skipped"
    assert db.scalar("SELECT COUNT(*) FROM attempts WHERE step_id=?", (ETAPA,)) == 0
