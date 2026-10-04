"""30.64 (b): o mesmo perfil, a mesma ação e o mesmo OBJETO em duas execuções não saem duas vezes.

`_mesmo_pedido_noutras_contas` é por execução e exclui o próprio perfil; o `_fleet_gate` também; só a REPLY_COMMENT
tinha a trava do 30.56. Agora a porta olha o que o PRÓPRIO perfil já fez, ou tem pedido em aberto de outra etapa,
sobre o mesmo objeto (`objeto_alvo` do catálogo): mensagem direta passa por aprovação; o resto é recusado.

Nível de prova: `simulated` (banco de teste, perfis de teste). Nada real.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.models import InteractionStatus, InteractionType
from app.planning.capabilities import capability_of
from app.social.approvals import ApprovalStore
from app.social.policy import PolicyEngine
from app.util import now, to_iso

from .test_capabilities import IG, build, perfil
from .test_protecao_de_frota import _Frota

ANA = "@ana.souza"
_SERVICOS: dict[str, Any] = {}                 # perfil → o SocialService que o criou (para mudar a política dele)


def _conta(tmp_path: Path) -> tuple[Any, PolicyEngine, Any, str]:
    _svc, repo, _pol, db = build(tmp_path)
    pid = perfil(_svc, "lucas.almeida9484", "android-01")
    _SERVICOS[pid] = _svc
    repo.update_profile(pid, {"automation_policy": '{"limits": {"warmup_days": 0, '
                                                   '"cooldown_between_external_actions_s": 0}}'})
    return repo, PolicyEngine(repo, lambda: _Frota()), db, pid


def _etapa(db: Any, run: str, acao: str, argumentos: dict[str, str], *, objetivo: str = "running") -> str:
    """Uma etapa de outra execução, com os argumentos que dizem o objeto."""
    oid, sid = f"{run}:android-01", f"{run}:android-01:v1:efeito"
    if db.scalar("SELECT 1 FROM runs WHERE id=?", (run,)) is None:
        db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
                   " VALUES (?,?,'x','execute','running',1,'[]','2026-10-04T10:00:00Z')", (run, f"k-{run}"))
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters)"
                   " VALUES (?,?,'android-01',?,1,'{}')", (oid, run, objetivo))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, bindings)"
        " VALUES (?,?,?,'android-01',1,1,'efeito','Efeito','efeito','[]',1,'[]',"
        "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready',?,?)",
        (sid, run, oid, acao, json.dumps(argumentos)))
    return sid


def _fez(repo: Any, pid: str, tipo: str, alvo: str, step_id: str | None, *, dias_atras: float = 1.0) -> str:
    return repo.record_interaction(pid, type=tipo, direction="outbound", status=InteractionStatus.confirmed.value,
                                   counterparty=alvo, thread_key=None, target=None, outgoing_content=None,
                                   app_id="instagram", run_id="r-a", step_id=step_id,
                                   occurred_at=to_iso(now() - timedelta(days=dias_atras)))


def _conversa(repo: Any, pid: str, app_id: str = "instagram") -> None:
    """A pessoa já escreveu a esta conta: a DM não é fria e o piso do ADR-055 não entra no caminho."""
    repo.record_interaction(pid, type=InteractionType.dm_received.value, direction="inbound",
                            status=InteractionStatus.confirmed.value, counterparty=ANA, app_id=app_id,
                            incoming_content="oi!", occurred_at=to_iso(now() - timedelta(days=2)))


def _mandou(repo: Any, pid: str, texto: str, app_id: str = "instagram") -> str:
    return repo.record_interaction(pid, type=InteractionType.dm_sent.value, direction="outbound",
                                   status=InteractionStatus.confirmed.value, counterparty=ANA, app_id=app_id,
                                   outgoing_content=texto, run_id="r-a",
                                   occurred_at=to_iso(now() - timedelta(days=1)))


def _dm_autonoma(policies: PolicyEngine, pid: str) -> None:
    from app.models import ProfilePolicyPatch
    _SERVICOS[pid].set_policy(pid, ProfilePolicyPatch(capabilities={"SEND_MESSAGE": "autonomous"}), package=IG)


def test_dm_com_o_mesmo_texto_aprovado_noutra_execucao_pede_confirmacao(tmp_path: Path) -> None:
    _repo, policies, db, pid = _conta(tmp_path)
    dm = capability_of(IG, "SEND_MESSAGE")
    sid = _etapa(db, "r-a", "SEND_MESSAGE", {"username": ANA, "content": "oi"})
    pedido = ApprovalStore(db).open(profile_id=pid, capability="SEND_MESSAGE", summary="DM", target=ANA, content="oi",
                                    run_id="r-a", objective_id="r-a:android-01", step_id=sid)
    # ainda pendente: o dono não aprovou nada, não há repetição a confirmar
    pendente = policies.check(pid, dm, run_id="r-b", counterparty=ANA, app_id="instagram",
                              step_id="r-b:android-01:v1:efeito", bindings={"username": ANA, "content": "oi"})
    assert "30.64" not in pendente.reason
    ApprovalStore(db).decide(pedido.id, status="approved", decided_by="dono")
    veredito = policies.check(pid, dm, run_id="r-b", counterparty=ANA, app_id="instagram",
                              step_id="r-b:android-01:v1:efeito", bindings={"username": "Ana.Souza", "content": " OI "})
    assert veredito.allowed and veredito.needs_approval
    assert pedido.id in veredito.reason and "30.64" in veredito.reason
    outra = policies.check(pid, dm, run_id="r-b", counterparty=ANA, app_id="instagram",
                           step_id="r-b:android-01:v1:efeito", bindings={"username": ANA, "content": "tudo bem?"})
    assert "30.64" not in outra.reason


def test_dm_ja_enviada_com_o_mesmo_texto_pede_e_texto_novo_segue_a_politica(tmp_path: Path) -> None:
    """Conversa em andamento num perfil autônomo: a mensagem nova sai sozinha; a MESMA de ontem pede confirmação."""
    repo, policies, _db, pid = _conta(tmp_path)
    dm = capability_of(IG, "SEND_MESSAGE")
    _dm_autonoma(policies, pid)
    _conversa(repo, pid)
    interacao = _mandou(repo, pid, "Bom dia, Ana!")
    nova = policies.check(pid, dm, counterparty=ANA, app_id="instagram", step_id="r-b:x",
                          bindings={"username": ANA, "content": "Como foi o fim de semana?"})
    assert nova.allowed and not nova.needs_approval and "30.64" not in nova.reason
    mesma = policies.check(pid, dm, counterparty=ANA, app_id="instagram", step_id="r-b:x",
                           bindings={"username": ANA, "content": "bom dia,   ana!"})
    assert mesma.allowed and mesma.needs_approval and interacao in mesma.reason
    # texto ainda por escrever (briefing sem `content`): não se sabe se repete, segue a política
    assert not policies.check(pid, dm, counterparty=ANA, app_id="instagram", step_id="r-b:x",
                              bindings={"username": ANA, "content_brief": "deseje bom dia"}).needs_approval


def test_o_bom_dia_diario_no_app_de_teste_nao_pede_aprovacao(tmp_path: Path) -> None:
    """O fluxo de bom dia a cada contato da lista, rodado duas vezes no app de QA em perfil autônomo: a mesma mensagem
    ao mesmo contato não vira pedido (app `qa` fica fora do 30.64). No Instagram, as mesmas duas execuções pedem."""
    repo, policies, db, pid = _conta(tmp_path)
    db.execute("INSERT INTO apps(id, name, package, activity, builtin, category)"
               " VALUES ('qa-msg','QA Messenger','com.pocqa.messenger',NULL,1,'qa')")
    dm = capability_of(IG, "SEND_MESSAGE")
    _dm_autonoma(policies, pid)
    for app_id in ("qa-msg", "instagram"):
        _conversa(repo, pid, app_id)
        _mandou(repo, pid, "Bom dia android-05", app_id)          # a 1ª execução já mandou
    segunda = {app_id: policies.check(pid, dm, counterparty=ANA, app_id=app_id, step_id="r-2:x",
                                      bindings={"username": ANA, "content": "Bom dia android-05"})
               for app_id in ("qa-msg", "instagram")}
    assert segunda["qa-msg"].allowed and not segunda["qa-msg"].needs_approval
    assert "30.64" not in segunda["qa-msg"].reason
    assert segunda["instagram"].allowed and segunda["instagram"].needs_approval
    assert "30.64" in segunda["instagram"].reason


def test_seguir_feito_noutra_execucao_recusa_o_segundo(tmp_path: Path) -> None:
    repo, policies, db, pid = _conta(tmp_path)
    sid = _etapa(db, "r-a", "FOLLOW", {"username": ANA})
    interacao = _fez(repo, pid, InteractionType.followed.value, ANA, sid)
    veredito = policies.check(pid, capability_of(IG, "FOLLOW"), run_id="r-b", counterparty=ANA, app_id="instagram",
                              step_id="r-b:android-01:v1:efeito", bindings={"username": ANA})
    assert not veredito.allowed and veredito.retry_at is None
    assert interacao in veredito.reason and "antes da aprovação" in veredito.reason


def test_curtir_outro_post_da_mesma_pessoa_libera_e_o_mesmo_recusa(tmp_path: Path) -> None:
    repo, policies, db, pid = _conta(tmp_path)
    curtir = capability_of(IG, "LIKE_POST")
    sid = _etapa(db, "r-a", "LIKE_POST", {"post_author": ANA, "caption_contains": "praia"})
    _fez(repo, pid, InteractionType.post_liked.value, ANA, sid)
    outro = policies.check(pid, curtir, counterparty=ANA, app_id="instagram", step_id="r-b:x",
                           bindings={"post_author": ANA, "caption_contains": "serra"})
    assert outro.allowed and "30.64" not in outro.reason
    mesmo = policies.check(pid, curtir, counterparty=ANA, app_id="instagram", step_id="r-b:x",
                           bindings={"post_author": "@Ana.Souza", "caption_contains": "praia"})
    assert not mesmo.allowed and "30.64" in mesmo.reason and "alterna" in mesmo.reason


def test_curtida_antiga_sem_etapa_nao_diz_qual_post_e_nao_conta(tmp_path: Path) -> None:
    """Sem etapa conhecida só a pessoa é sabida: conta quando o objeto É a pessoa (seguir), não numa curtida."""
    repo, policies, _db, pid = _conta(tmp_path)
    _fez(repo, pid, InteractionType.post_liked.value, ANA, None)
    assert policies.check(pid, capability_of(IG, "LIKE_POST"), counterparty=ANA, app_id="instagram",
                          bindings={"post_author": ANA, "caption_contains": "praia"}).allowed
    _fez(repo, pid, InteractionType.followed.value, ANA, None)
    assert not policies.check(pid, capability_of(IG, "FOLLOW"), counterparty=ANA, app_id="instagram",
                              bindings={"username": ANA}).allowed


def test_publicar_a_mesma_imagem_com_pedido_aberto_noutra_execucao_recusa(tmp_path: Path) -> None:
    _repo, policies, db, pid = _conta(tmp_path)
    publicar = capability_of(IG, "CREATE_POST")
    sid = _etapa(db, "r-a", "CREATE_POST", {"image_id": "img-1", "content": "Fim de tarde"})
    pedido = ApprovalStore(db).open(profile_id=pid, capability="CREATE_POST", summary="Publicar", content="Fim de tarde",
                                    run_id="r-a", objective_id="r-a:android-01", step_id=sid)
    veredito = policies.check(pid, publicar, step_id="r-b:x", bindings={"image_id": "img-1", "content": "Outra"})
    assert not veredito.allowed and pedido.id in veredito.reason and "Pendências" in (veredito.hint or "")
    # outra imagem não é repetição; ela só espera o teto de publicações por hora, que já conta o pedido aberto (30.57)
    outra = policies.check(pid, publicar, step_id="r-b:x", bindings={"image_id": "img-2"})
    assert "30.64" not in outra.reason and outra.retry_at is not None


def test_pedido_de_objetivo_encerrado_nao_conta(tmp_path: Path) -> None:
    _repo, policies, db, pid = _conta(tmp_path)
    sid = _etapa(db, "r-a", "FOLLOW", {"username": ANA}, objetivo="cancelled")
    pedido = ApprovalStore(db).open(profile_id=pid, capability="FOLLOW", summary="Seguir", target=ANA,
                                    run_id="r-a", objective_id="r-a:android-01", step_id=sid)
    ApprovalStore(db).decide(pedido.id, status="approved", decided_by="dono")
    assert policies.check(pid, capability_of(IG, "FOLLOW"), counterparty=ANA, step_id="r-b:x",
                          bindings={"username": ANA}).allowed


def test_a_propria_etapa_e_o_objeto_por_resolver_nao_se_barram(tmp_path: Path) -> None:
    repo, policies, db, pid = _conta(tmp_path)
    sid = _etapa(db, "r-a", "FOLLOW", {"username": ANA})
    ApprovalStore(db).open(profile_id=pid, capability="FOLLOW", summary="Seguir", target=ANA,
                           run_id="r-a", objective_id="r-a:android-01", step_id=sid)
    _fez(repo, pid, InteractionType.followed.value, ANA, sid, dias_atras=0)
    seguir = capability_of(IG, "FOLLOW")
    assert policies.check(pid, seguir, counterparty=ANA, step_id=sid, bindings={"username": ANA}).allowed  # retomada
    # `{item}` ainda não diz quem é: a porta de frota e a aprovação de sempre valem; o 30.64 não adivinha
    veredito = policies.check(pid, seguir, counterparty=None, step_id="r-b:x", bindings={"username": "{item}"})
    assert "30.64" not in veredito.reason
