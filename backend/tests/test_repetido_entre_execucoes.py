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
    pid = perfil(_svc, "tadeu.quintela4821", "android-01")
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


def test_a_mesma_dm_em_duas_execucoes_no_instagram_pede_confirmacao(tmp_path: Path) -> None:
    """A mesma mensagem ao mesmo contato, de novo, num perfil autônomo do Instagram: pede confirmação."""
    repo, policies, _db, pid = _conta(tmp_path)
    dm = capability_of(IG, "SEND_MESSAGE")
    _dm_autonoma(policies, pid)
    _conversa(repo, pid)
    _mandou(repo, pid, "Bom dia android-05")                        # a 1ª execução já mandou
    segunda = policies.check(pid, dm, counterparty=ANA, app_id="instagram", step_id="r-2:x",
                             bindings={"username": ANA, "content": "Bom dia android-05"})
    assert segunda.allowed and segunda.needs_approval and "30.64" in segunda.reason


async def test_o_bom_dia_do_qa_messenger_sai_antes_da_porta_e_roda_duas_vezes_sem_pedir(harness: Any) -> None:
    """Revisão da fila, item 7: o QA Messenger não tem catálogo, então a etapa sai da porta ANTES do `check` e o 30.64
    nem é consultado. Duas execuções do "bom dia" ao mesmo contato, com a mesma mensagem, seguem sem pedido."""
    from app.models import ProfileCreate

    from .test_capabilities import SENHA

    state = harness.state
    db = state.db
    pid = state.social.create_profile(ProfileCreate(username="tadeu.quintela4821", password=SENHA,
                                                    instance_id="android-01")).id
    pacote = db.scalar("SELECT a.package FROM instances i JOIN apps a ON a.id=i.app_id WHERE i.id='android-01'")
    assert pacote and capability_of(pacote, "SEND_MESSAGE") is None          # sem catálogo: nada a consultar
    for n in (1, 2):
        run = f"run-bd{n}"
        db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
                   " VALUES (?,?,'bom dia','execute','running',1,'[\"android-01\"]','2026-10-04T10:00:00Z')",
                   (run, f"k-{run}"))
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
                   " VALUES (?,?,'android-01','running',1,'{}',?)", (f"{run}:android-01", run, pid))
        db.execute(
            "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
            " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, bindings)"
            " VALUES (?,?,?,'android-01',1,1,'efeito','Bom dia','bom dia','[]',1,'[]',"
            "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready','SEND_MESSAGE',?)",
            (f"{run}:android-01:v1:efeito", run, f"{run}:android-01",
             json.dumps({"username": "contato1", "content": "Bom dia android-01"})))
        veredito = await state._policy_gate(db.one("SELECT * FROM objectives WHERE id=?", (f"{run}:android-01",)),  # noqa: SLF001
                                            db.one("SELECT * FROM steps WHERE id=?", (f"{run}:android-01:v1:efeito",)),
                                            db.one("SELECT * FROM runs WHERE id=?", (run,)))
        assert veredito is None, veredito
        state.social_repo.record_interaction(pid, type=InteractionType.dm_sent.value, direction="outbound",
                                             status=InteractionStatus.confirmed.value, counterparty="@contato1",
                                             outgoing_content="Bom dia android-01", run_id=run,
                                             step_id=f"{run}:android-01:v1:efeito")
    assert state.approval_service.list() == []


def test_comentar_sem_dizer_qual_post_pede_aprovacao_e_nunca_recusa(tmp_path: Path) -> None:
    """Revisão da fila, item 4: "comente no post mais recente de @ana" duas vezes pode ser o mesmo post ou dois.
    Objeto ambíguo (legenda vazia) não recusa; com um comentário já feito sobre ela, passa por aprovação."""
    repo, policies, db, pid = _conta(tmp_path)
    comentar = capability_of(IG, "CREATE_COMMENT")
    sid = _etapa(db, "r-a", "CREATE_COMMENT", {"post_author": ANA, "content": "Lindo!"})
    _fez(repo, pid, InteractionType.comment_replied.value, ANA, sid)
    veredito = policies.check(pid, comentar, counterparty=ANA, app_id="instagram", step_id="r-b:x",
                              bindings={"post_author": ANA, "content": "Que foto!"})
    assert veredito.allowed and veredito.needs_approval
    assert "objeto não identificado" in veredito.reason and "30.64" in veredito.reason


def test_curtir_sem_legenda_pede_aprovacao_ate_a_prova_em_tela_real(tmp_path: Path) -> None:
    """Revisão da fila, item 4: a curtida por posição (objeto ambíguo) seria segura pelo seletor de commit EXATO
    (`desc==Like` não casa com o coração curtido), provado no executor com o aparelho falso em
    `test_alvo_por_legenda.py::test_post_ja_curtido_o_commit_exato_nao_toca_e_a_etapa_nao_conta`. Até a prova com a
    árvore de uma tela REAL gravada, ela passa por aprovação, como os outros ambíguos."""
    repo, policies, db, pid = _conta(tmp_path)
    for acao, args, tipo in (("LIKE_POST", {"post_author": ANA}, InteractionType.post_liked.value),
                             ("LIKE_COMMENT", {"username": ANA}, InteractionType.comment_liked.value)):
        cap = capability_of(IG, acao)
        assert cap is not None and cap.commit_selector == "desc==Like"
        sid = _etapa(db, f"r-{acao}", acao, args)
        _fez(repo, pid, tipo, ANA, sid)
        veredito = policies.check(pid, cap, counterparty=ANA, app_id="instagram", step_id="r-b:x", bindings=args)
        assert veredito.allowed and veredito.needs_approval and "objeto não identificado" in veredito.reason, acao


def test_acao_com_efeito_sem_objeto_declarado_passa_por_aprovacao(tmp_path: Path) -> None:
    """Revisão da fila, item 8: a carga já exige `objeto_alvo`; se uma ação com efeito chegar sem ele, falha fechado."""
    from dataclasses import replace

    _repo, policies, _db, pid = _conta(tmp_path)
    seguir = capability_of(IG, "FOLLOW")
    assert seguir is not None
    sem = replace(seguir, objeto_alvo=())
    veredito = policies.check(pid, sem, counterparty=ANA, app_id="instagram", step_id="r-b:x", bindings={"username": ANA})
    assert veredito.allowed and veredito.needs_approval and "objeto_alvo" in veredito.reason


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


def test_o_texto_gerado_se_compara_depois_do_rascunho(tmp_path: Path) -> None:
    """Revisão da fila, item 5: na porta o texto ainda é briefing e passa; relida a etapa com o rascunho, a mesma
    mensagem de ontem dá o motivo para a confirmação (o `_policy_gate` chama isto depois do `_draft_gate`)."""
    repo, policies, _db, pid = _conta(tmp_path)
    dm = capability_of(IG, "SEND_MESSAGE")
    _dm_autonoma(policies, pid)
    _conversa(repo, pid)
    interacao = _mandou(repo, pid, "Bom dia, Ana!")
    antes = policies.check(pid, dm, counterparty=ANA, app_id="instagram", step_id="r-2:x",
                           bindings={"username": ANA, "content_brief": "deseje bom dia"})
    assert antes.allowed and not antes.needs_approval
    motivo = policies.mensagem_repetida(pid, dm, {"username": ANA, "content_brief": "deseje bom dia",
                                                  "content": "Bom dia, Ana!"}, app_id="instagram", step_id="r-2:x")
    assert motivo and interacao in motivo and "30.64" in motivo
    assert policies.mensagem_repetida(pid, dm, {"username": ANA, "content": "Oi, tudo bem?"}, app_id="instagram",
                                      step_id="r-2:x") is None
    assert policies.mensagem_repetida(pid, capability_of(IG, "FOLLOW"), {"username": ANA}) is None   # só DM
