"""Item 30.56: a resposta a comentário que esta conta já deu não volta ao dono como pedido novo.

O caso real (04/10/2026): o tadeu já tinha respondido ao comentário do quillon em 03/10 (interação int-fPuCkX3vCt7WnmsL,
`comment_replied`, confirmada, com `thread_key` e `target` nulos e `app_id='instagram'`). Uma execução nova pediu a
mesma resposta, o dono aprovou pelo Telegram e só o ator, já na folha de comentários, recusou a duplicata. Agora a
porta de política recusa ANTES do rascunho e da aprovação: (perfil, alvo, tipo de interação, janela da frota).

Nível de prova: `simulated` (banco de teste, perfis de teste). Nada real.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.models import InteractionStatus, InteractionType
from app.planning.capabilities import capability_of, normalizar_alvo
from app.social.approvals import ApprovalStore
from app.social.policy import PolicyEngine
from app.util import now, to_iso

from .test_capabilities import IG, build, perfil
from .test_protecao_de_frota import ALVO, _execucao_em_duas_contas, _Frota, _porta

QUILLON = "@valdir.teixeira6352"


def _lucas(tmp_path: Path, **over: Any) -> tuple[Any, Any, PolicyEngine, Any, str]:
    svc, repo, _pol, db = build(tmp_path)
    pid = perfil(svc, "tadeu.quintela4821", "android-01")
    repo.update_profile(pid, {"automation_policy": '{"limits": {"warmup_days": 0, '
                                                   '"cooldown_between_external_actions_s": 0}}'})
    return svc, repo, PolicyEngine(repo, lambda: _Frota(**over)), db, pid


def _respondeu(repo: Any, pid: str, alvo: str = QUILLON, *, dias_atras: float = 1.0,
               status: str = InteractionStatus.confirmed.value, tipo: str = InteractionType.comment_replied.value,
               step_id: str | None = None) -> str:
    """A forma exata da int-fPuCkX3vCt7WnmsL: saída, sem publicação (`thread_key`/`target` nulos), app `instagram`."""
    return repo.record_interaction(pid, type=tipo, direction="outbound", status=status, counterparty=alvo,
                                   thread_key=None, target=None, outgoing_content="Valeu, Quillon!", app_id="instagram",
                                   run_id="r-20261003054328-60e83c", step_id=step_id,
                                   occurred_at=to_iso(now() - timedelta(days=dias_atras)))


def test_o_caso_real_resposta_repetida_e_recusada_sem_retry_at(tmp_path: Path) -> None:
    _svc, repo, policies, _db, pid = _lucas(tmp_path)
    assert normalizar_alvo("@Valdir.Teixeira6352") == QUILLON          # o que a porta compara é o que o histórico grava
    interacao = _respondeu(repo, pid)
    veredito = policies.check(pid, capability_of(IG, "REPLY_COMMENT"), run_id="r-hoje",
                              counterparty="@Valdir.Teixeira6352", app_id="instagram")
    assert not veredito.allowed and veredito.retry_at is None
    assert interacao in veredito.reason and QUILLON in veredito.reason and "antes da aprovação" in veredito.reason
    assert veredito.hint and "à mão" in veredito.hint


def test_outra_pessoa_e_resposta_fora_da_janela_liberam(tmp_path: Path) -> None:
    _svc, repo, policies, _db, pid = _lucas(tmp_path, dias=30)
    responder = capability_of(IG, "REPLY_COMMENT")
    _respondeu(repo, pid, dias_atras=31)                             # fora da janela de 30 dias
    assert policies.check(pid, responder, counterparty=QUILLON, app_id="instagram").allowed
    _respondeu(repo, pid, "@outra.pessoa")
    assert policies.check(pid, responder, counterparty=QUILLON, app_id="instagram").allowed


def test_falha_e_cancelada_nao_contam_e_incerta_conta(tmp_path: Path) -> None:
    _svc, repo, policies, _db, pid = _lucas(tmp_path)
    responder = capability_of(IG, "REPLY_COMMENT")
    for status in (InteractionStatus.failed.value, InteractionStatus.cancelled.value):
        _respondeu(repo, pid, status=status)
    assert policies.check(pid, responder, counterparty=QUILLON).allowed
    _respondeu(repo, pid, status=InteractionStatus.uncertain.value)   # pode ter saído: conta
    assert not policies.check(pid, responder, counterparty=QUILLON).allowed


def test_comentar_na_mesma_pessoa_continua_valendo(tmp_path: Path) -> None:
    """A regra é por tipo de interação, não por balde: comentar em vários posts da mesma pessoa é legítimo."""
    _svc, repo, policies, _db, pid = _lucas(tmp_path)
    _respondeu(repo, pid)
    veredito = policies.check(pid, capability_of(IG, "CREATE_COMMENT"), counterparty=QUILLON)
    assert veredito.allowed and "30.56" not in veredito.reason


def test_a_propria_etapa_nao_se_barra_na_retomada(tmp_path: Path) -> None:
    """A porta roda de novo quando a etapa aprovada é retomada: o pedido e a interação DELA não contam."""
    _svc, repo, policies, db, pid = _lucas(tmp_path)
    responder = capability_of(IG, "REPLY_COMMENT")
    etapa = "r-hoje:android-01:v1:reply_1"
    ApprovalStore(db).open(profile_id=pid, capability="REPLY_COMMENT", summary="Responder", target=QUILLON,
                           content="Valeu, Quillon!", run_id="r-hoje", step_id=etapa)
    _respondeu(repo, pid, status=InteractionStatus.pending.value, dias_atras=0, step_id=etapa)
    assert policies.check(pid, responder, counterparty=QUILLON, step_id=etapa).allowed
    # a etapa de outra versão do plano (v2) é outra etapa: o pedido e a tentativa da v1 contam para ela
    veredito = policies.check(pid, responder, counterparty=QUILLON, step_id="r-hoje:android-01:v2:reply_1")
    assert not veredito.allowed and veredito.retry_at is None


def test_pedido_em_aberto_da_mesma_conta_recusa_o_segundo(tmp_path: Path) -> None:
    """Duas execuções quase juntas: nenhuma respondeu ainda, mas a primeira já tem pedido em Pendências."""
    _svc, _repo, policies, db, pid = _lucas(tmp_path)
    pedido = ApprovalStore(db).open(profile_id=pid, capability="REPLY_COMMENT", summary="Responder", target="valdir.teixeira6352",
                                    content="Valeu!", run_id="r-1", step_id="r-1:android-01:v1:reply_1")
    veredito = policies.check(pid, capability_of(IG, "REPLY_COMMENT"), counterparty=QUILLON,
                              step_id="r-2:android-01:v1:reply_1")
    assert not veredito.allowed and veredito.retry_at is None and pedido.id in veredito.reason
    assert "Pendências" in (veredito.hint or "")


async def test_comentario_gravado_por_etapa_de_comentar_nao_barra_a_resposta(harness: Any) -> None:
    """`CREATE_COMMENT` grava o mesmo `comment_replied`: o histórico separa as duas pela ação da etapa que gravou. Já a
    gravada por uma etapa de `REPLY_COMMENT` barra."""
    state = harness.state
    pids = _execucao_em_duas_contas(state, "CREATE_COMMENT", {"post_author": ALVO, "content": "Boa!",
                                                              "content_verbatim": "true"})
    pid = pids["android-01"]
    comentou = state.social_repo.record_interaction(pid, type=InteractionType.comment_replied.value,
                                                    direction="outbound", status=InteractionStatus.confirmed.value,
                                                    counterparty=ALVO, app_id="ig", step_id="run-f:android-01:v1:efeito")
    responder = capability_of(IG, "REPLY_COMMENT")
    assert state.policies.check(pid, responder, counterparty=ALVO, app_id="ig", step_id="outra").allowed
    state.db.execute("UPDATE steps SET capability='REPLY_COMMENT' WHERE id='run-f:android-01:v1:efeito'")
    veredito = state.policies.check(pid, responder, counterparty=ALVO, app_id="ig", step_id="outra")
    assert not veredito.allowed and comentou in veredito.reason


async def test_na_porta_do_despacho_a_repetida_nao_vira_pedido(harness: Any) -> None:
    """O caminho inteiro: com a resposta já dada, a etapa de REPLY_COMMENT para na porta e nenhum pedido é aberto."""
    state = harness.state
    pids = _execucao_em_duas_contas(state, "REPLY_COMMENT", {"username": ALVO, "content": "Valeu! 🙌",
                                                             "content_verbatim": "true"})
    state.db.execute("UPDATE objectives SET status='cancelled' WHERE id='run-f:android-02'")
    state.social_repo.record_interaction(pids["android-01"], type=InteractionType.comment_replied.value,
                                         direction="outbound", status=InteractionStatus.confirmed.value,
                                         counterparty=ALVO, app_id="ig",
                                         occurred_at=to_iso(now() - timedelta(days=1)))
    veredito = await _porta(state, "android-01")
    assert veredito is not None and not veredito.allowed and veredito.retry_at is None
    assert "já respondeu" in veredito.reason
    assert state.approval_service.list() == []
    bindings = json.loads(state.db.one("SELECT bindings FROM steps WHERE id='run-f:android-01:v1:efeito'")["bindings"])
    assert bindings["content"] == "Valeu! 🙌"                       # nada foi reescrito


def test_aprovacao_de_objetivo_encerrado_sem_efeito_nao_fica_em_aberto(tmp_path: Path) -> None:
    """Revisão da fila (suíte 31): a aprovação aprovada cuja etapa nunca disparou não ganha `interaction_id`, e o
    `expire_for_objective` só expira as pendentes. Com o objetivo encerrado, ela não recusa a resposta nova (30.56) nem
    ocupa o teto (30.57); com o objetivo vivo, continua reservando."""
    _svc, _repo, policies, db, pid = _lucas(tmp_path)
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('r-orfa', 'k-orfa', 'responder', 'execute', 'running', 0, '[\"android-01\"]', ?)", (to_iso(now()),))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status) VALUES ('r-orfa:o', 'r-orfa', 'android-01', 'running')")
    store = ApprovalStore(db)
    pedido = store.open(profile_id=pid, capability="REPLY_COMMENT", summary="Responder", target=QUILLON,
                        run_id="r-orfa", objective_id="r-orfa:o")
    store.decide(pedido.id, status="approved")
    responder = capability_of(IG, "REPLY_COMMENT")
    assert not policies.check(pid, responder, counterparty=QUILLON, app_id="instagram").allowed      # vivo: reserva
    assert policies.repo.pedidos_em_aberto_desde(pid, to_iso(now() - timedelta(days=1)))
    db.execute("UPDATE objectives SET status='failed' WHERE id='r-orfa:o'")                        # morreu antes do efeito
    assert policies.check(pid, responder, counterparty=QUILLON, app_id="instagram").allowed
    assert policies.repo.pedidos_em_aberto_desde(pid, to_iso(now() - timedelta(days=1))) == []
