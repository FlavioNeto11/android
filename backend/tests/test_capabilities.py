"""Fase 4 — catálogo de ações, commit declarado, políticas, aprovação e limites.

O que estes testes protegem:

1. **O planejador não inventa ações.** Capability desconhecida vira PERGUNTA, não execução torta nem erro fatal.
2. **Commit é estrutural.** Com seletor declarado, tocar no elemento errado é rejeitado — commit falso é irreversível.
3. **Guarda de linha.** Numa lista, o texto que identifica o alvo tem de estar na MESMA faixa do botão.
4. **Limite represa sem gastar nada**: nem tentativa, nem chamada de modelo.
5. **Aprovar, editar e rejeitar** decidem o que VAI acontecer — nenhum deles marca etapa como feita sem executar.
6. **O caminho livre segue igual**: o QA Messenger não tem catálogo e não passa por nenhuma destas portas.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.automation.hierarchy import parse_hierarchy
from app.db import Database
from app.events import EventBus
from app.models import (InteractionStatus, InteractionType, PersonaCreate, PersonaTraits, ProfileCreate,
                        ProfilePatch, ProfilePolicyPatch)
from app.planning.capabilities import (CapabilityCatalog, CapabilityNode, capability_of, compose, load_catalog,
                                       texto_a_gerar)
from app.security.secret_store import MemoryKeyProvider, SecretStore
from app.social.approvals import ApprovalService, ApprovalStore, apply_edit
from app.social.policy import DEFAULT_LIMITS, PolicyEngine
from app.social.repository import SocialRepository
from app.social.service import SocialService
from app.taskqueue.executor import guard_variants
from app.taskqueue.repository import Repository

from .conftest import make_config

SENHA = "$a=B7ee1#<b-C?S-{"
IG = "com.instagram.android"


def build(tmp_path: Path) -> tuple[SocialService, SocialRepository, PolicyEngine, Database]:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_path)
    db.migrate()
    repo = SocialRepository(db)
    svc = SocialService(repo, SecretStore(db, MemoryKeyProvider()), EventBus(db),
                        known_instances=lambda: ["android-01", "android-02"])
    return svc, repo, PolicyEngine(repo), db


def perfil(svc: SocialService, username: str = "mariana.costa91182", instance: str = "android-02") -> str:
    return svc.create_profile(ProfileCreate(username=username, password=SENHA, instance_id=instance)).id


# ---------------------------------------------------------------- catálogo e composição
def test_o_catalogo_nao_oferece_o_que_e_resolvido_por_codigo() -> None:
    """Login e verificação de conta existem, mas não são oferecidos: quem autentica é o canal determinístico."""
    catalogo = load_catalog(IG)
    oferecidas = {c.key for c in catalogo.offered}
    assert "AUTHENTICATE_INSTAGRAM" not in oferecidas
    assert catalogo.has("AUTHENTICATE_INSTAGRAM")                  # existe no catálogo, só não vai ao planejador
    assert {"SEND_MESSAGE", "FOLLOW", "LIKE_POST", "OPEN_THREAD"} <= oferecidas


def test_abrir_conversa_aceita_conversa_nova_e_nao_exige_o_cabecalho() -> None:
    """Numa conversa NOVA o Instagram põe "New message" no cabeçalho e o destinatário em "To:". A pós-condição
    antiga exigia o @usuário NO CABEÇALHO: era impossível de cumprir por esse caminho, e a etapa era reprovada com
    a conversa aberta e o compositor pronto — o envio acabava cancelado (r-20260918213007-a353d8).

    O mesmo para a caixa de entrada: conta nova mostra "No messages yet", e caixa vazia é caixa aberta."""
    cap = capability_of(IG, "OPEN_THREAD")
    assert cap is not None
    texto = cap.post_description.lower()
    assert "to:" in texto or "para:" in texto           # o caminho da conversa nova é aceito
    assert "pronta para escrever" in texto              # o que importa é poder escrever, não onde o nome aparece

    inbox = capability_of(IG, "OPEN_INBOX")
    assert inbox is not None
    assert "vazia" in inbox.post_description.lower()    # caixa vazia conta como aberta


def test_toda_acao_com_efeito_declara_seletor_de_commit_e_como_reconciliar() -> None:
    """Sem seletor, o efeito seria adivinhado pelo texto do elemento — e numa lista qualquer linha viraria commit."""
    for cap in load_catalog(IG).offered:
        if cap.side_effect:
            assert cap.commit_selector, f"{cap.key} não declara commit_selector"
            assert cap.reconciliation, f"{cap.key} não diz como reconciliar"
            # Ação social (a que mexe com outra pessoa) vira histórico e tem balde de limite; sair da conta mexe
            # só com a própria sessão, e por isso não entra em nenhum dos dois.
            if cap.limit_bucket:
                assert cap.interaction_type, f"{cap.key} não vira interação no histórico"


def test_composicao_monta_a_etapa_com_guardas_alvo_e_uma_tentativa() -> None:
    steps, missing = compose(load_catalog(IG), [
        CapabilityNode(key="open_inbox", capability="OPEN_INBOX"),
        CapabilityNode(key="send_1", capability="SEND_MESSAGE", depends_on=["open_inbox"],
                       bindings={"username": "@ana", "content": "bom dia!"})])
    assert not missing
    envio = steps[-1]
    assert envio.capability == "SEND_MESSAGE" and envio.commit_selector == "desc=Send"
    assert envio.side_effect and envio.max_attempts == 1            # efeito externo: uma tentativa, sempre
    # Texto do comando SEM "exatamente" é BRIEFING: cada perfil escreve o seu, então o plano não congela a frase
    # nem a guarda que depende dela. A guarda do texto entra junto com o rascunho, no gate.
    assert envio.commit_guard == ["@ana"]
    assert envio.bindings["content"] == "bom dia!"                  # fica guardado como intenção


def test_texto_exato_pedido_no_comando_continua_literal_e_igual_para_todos() -> None:
    """Briefing é o padrão, mas "envie exatamente isto" tem de continuar valendo — e aí a guarda volta a travar
    o texto, porque as palavras são as mesmas em todos os aparelhos."""
    steps, missing = compose(load_catalog(IG), [
        CapabilityNode(key="send_1", capability="SEND_MESSAGE",
                       bindings={"username": "@ana", "content": "bom dia!", "content_verbatim": "true"})])
    assert not missing
    assert steps[-1].commit_guard == ["@ana", "bom dia!"]
    assert texto_a_gerar(steps[-1].bindings) is None               # nada a gerar: o texto já está fechado


def test_etapa_que_escreve_sem_intencao_nem_texto_vira_pergunta() -> None:
    """Sem briefing e sem texto não há o que escrever. Isso é pergunta ao usuário, não plano torto."""
    steps, missing = compose(load_catalog(IG), [
        CapabilityNode(key="c1", capability="CREATE_COMMENT", bindings={})])
    assert steps == []
    assert missing and "content_brief" in missing[0].question


def test_briefing_e_o_que_sera_escrito_por_perfil() -> None:
    steps, _ = compose(load_catalog(IG), [
        CapabilityNode(key="c1", capability="CREATE_COMMENT",
                       bindings={"content_brief": "elogiar o trabalho do secretário, tom positivo"})])
    assert steps[0].commit_guard == []                              # nada a travar antes do texto existir
    assert texto_a_gerar(steps[0].bindings) == "elogiar o trabalho do secretário, tom positivo"


def test_acao_desconhecida_e_argumento_faltando_viram_pergunta(tmp_path: Path) -> None:
    steps, missing = compose(load_catalog(IG), [
        CapabilityNode(key="x", capability="ENVIAR_PIX"),
        CapabilityNode(key="y", capability="FOLLOW", bindings={})])
    assert steps == []
    assert [m.field for m in missing] == ["capability", "follow"]
    assert "não existe" in missing[0].question and "SEND_MESSAGE" in missing[0].question


async def test_planejador_simulado_usa_o_catalogo_quando_o_app_tem_um() -> None:
    from app.planning.provider import AppContext, PlanRequest
    from app.planning.simulated_provider import SimulatedProvider

    apps = [AppContext("ig", "Instagram", IG, None, None, None)]
    req = PlanRequest(command='Responda a mensagem de @ana com "bom dia"', run_id="r1",
                      instances=[{"instance_id": "android-02", "account_label": None, "app_id": "ig"}],
                      apps=apps, catalog=load_catalog(IG))
    plan, _ = await SimulatedProvider().plan(req)
    assert [s.capability for s in plan.steps] == ["OPEN_INBOX", "OPEN_THREAD", "SEND_MESSAGE"]
    assert plan.steps[-1].bindings == {"username": "@ana", "content": "bom dia"}


# ---------------------------------------------------------------- alvo do efeito e guarda de linha
def _lista_de_pedidos() -> Any:
    """Duas linhas, cada uma com seu botão: é exatamente onde um commit no elemento errado acerta outra pessoa."""
    linhas = ""
    for i, nome in enumerate(("@ana", "@bruno")):
        y = 200 + i * 200
        linhas += (f'<node class="android.widget.TextView" text="{nome}" resource-id="app:id/username" '
                   f'bounds="[20,{y}][400,{y + 80}]"/>'
                   f'<node class="android.widget.Button" text="Confirm" resource-id="app:id/confirm" clickable="true" '
                   f'bounds="[420,{y}][700,{y + 80}]"/>')
    return parse_hierarchy(f"<hierarchy>{linhas}</hierarchy>")


def test_guarda_de_linha_distingue_o_alvo_certo_do_vizinho() -> None:
    tree = _lista_de_pedidos()
    botoes = tree.find_selector("text=Confirm")
    assert len(botoes) == 2
    da_ana, do_bruno = botoes
    assert tree.text_in_band("@ana", da_ana.bounds)
    assert not tree.text_in_band("@ana", do_bruno.bounds)           # a guarda antiga (tela inteira) passaria aqui
    assert tree.contains_text("@ana")                               # e é por isso que "está na tela" não basta


def test_guarda_aceita_o_usuario_escrito_sem_arroba() -> None:
    """A tela do Instagram quase nunca mostra a arroba: o cabeçalho da conversa traz "thi.mnz". Exigir o literal
    "@thi.mnz" recusava o toque em Enviar com a conversa certa aberta e o texto digitado, e a etapa parava
    esperando uma pessoa (r-20260918214743-54d31a). A arroba é notação nossa, não o que está na tela."""
    tela = parse_hierarchy(
        '<hierarchy>'
        '<node class="android.widget.TextView" text="Thiago Menezes" resource-id="app:id/header_title"'
        ' bounds="[20,20][400,80]"/>'
        '<node class="android.widget.TextView" text="thi.mnz" resource-id="app:id/header_subtitle"'
        ' bounds="[20,80][400,130]"/>'
        '<node class="android.widget.EditText" text="Boa noite" resource-id="app:id/composer"'
        ' bounds="[20,900][600,960]"/>'
        '<node class="android.widget.Button" content-desc="Send" clickable="true" bounds="[620,900][700,960]"/>'
        '</hierarchy>')

    assert not tela.contains_text("@thi.mnz")                       # o literal com arroba não está na tela
    assert any(tela.contains_text(v) for v in guard_variants("@thi.mnz"))   # e mesmo assim a guarda passa
    assert guard_variants("@thi.mnz") == ("@thi.mnz", "thi.mnz")
    assert guard_variants("Boa noite") == ("Boa noite",)            # texto comum não ganha variante

    # E não afrouxa: outro usuário continua reprovado.
    assert not any(tela.contains_text(v) for v in guard_variants("@outra.pessoa"))


def test_seletor_de_commit_reconhece_so_o_elemento_declarado() -> None:
    tree = _lista_de_pedidos()
    alvos = {e.id for e in tree.find_selector("text=Confirm")}
    outros = [e for e in tree.elements if e.resource_id.endswith("username")]
    assert alvos and all(e.id not in alvos for e in outros)


# ---------------------------------------------------------------- políticas
def test_politica_padrao_vem_do_catalogo_e_o_perfil_pode_endurecer(tmp_path: Path) -> None:
    svc, repo, policies, _ = build(tmp_path)
    pid = perfil(svc)
    assert policies.policy_for(pid, capability_of(IG, "LIKE_POST")) == "autonomous"
    assert policies.policy_for(pid, capability_of(IG, "SEND_MESSAGE")) == "approval_required"
    assert policies.policy_for(pid, capability_of(IG, "UNFOLLOW")) == "manual_only"

    repo.update_profile(pid, {"automation_policy": '{"capabilities": {"LIKE_POST": "disabled"}}'})
    veredito = policies.check(pid, capability_of(IG, "LIKE_POST"))
    assert not veredito.allowed and veredito.retry_at is None       # não é espera: é decisão de quem configurou
    assert "desligada" in veredito.reason


def test_acao_manual_nao_roda_por_automacao(tmp_path: Path) -> None:
    svc, _, policies, _ = build(tmp_path)
    pid = perfil(svc)
    veredito = policies.check(pid, capability_of(IG, "UNFOLLOW"))
    assert not veredito.allowed and not veredito.is_wait and "manual" in veredito.reason


def test_aprovacao_exigida_nao_bloqueia_por_limite(tmp_path: Path) -> None:
    svc, _, policies, _ = build(tmp_path)
    pid = perfil(svc)
    veredito = policies.check(pid, capability_of(IG, "SEND_MESSAGE"), run_id="run-1")
    assert veredito.allowed and veredito.needs_approval


# ---------------------------------------------------------------- limites
def _curtidas(svc: SocialService, pid: str, n: int, *, status: str = InteractionStatus.confirmed.value) -> None:
    for i in range(n):
        svc.record_interaction(pid, type=InteractionType.post_liked.value, direction="outbound", status=status,
                               counterparty=f"@pessoa{i}", run_id="run-1")


def test_teto_por_hora_represa_e_marca_quando_libera(tmp_path: Path) -> None:
    svc, repo, policies, _ = build(tmp_path)
    pid = perfil(svc)
    repo.update_profile(pid, {"automation_policy": '{"limits": {"likes_per_hour": 3, '
                                                   '"cooldown_between_external_actions_s": 0}}'})
    _curtidas(svc, pid, 2)
    assert policies.check(pid, capability_of(IG, "LIKE_POST"), run_id="run-1").allowed
    _curtidas(svc, pid, 1)
    veredito = policies.check(pid, capability_of(IG, "LIKE_POST"), run_id="run-1")
    assert veredito.is_wait and veredito.retry_at                   # é espera, não falha
    assert "limite de 3 likes por hora" in veredito.reason


def test_acao_disparada_sem_resultado_conhecido_ja_conta_para_o_limite(tmp_path: Path) -> None:
    """Efeito disparado é efeito que pode ter acontecido: fingir que não conta seria a forma mais fácil de estourar."""
    svc, repo, policies, _ = build(tmp_path)
    pid = perfil(svc)
    repo.update_profile(pid, {"automation_policy": '{"limits": {"likes_per_hour": 1, '
                                                   '"cooldown_between_external_actions_s": 0}}'})
    _curtidas(svc, pid, 1, status=InteractionStatus.pending.value)
    assert not policies.check(pid, capability_of(IG, "LIKE_POST"), run_id="run-1").allowed


def test_intervalo_minimo_entre_acoes_com_efeito(tmp_path: Path) -> None:
    svc, repo, policies, _ = build(tmp_path)
    pid = perfil(svc)
    repo.update_profile(pid, {"automation_policy": '{"limits": {"cooldown_between_external_actions_s": 300}}'})
    _curtidas(svc, pid, 1)
    veredito = policies.check(pid, capability_of(IG, "LIKE_POST"), run_id="run-1")
    assert veredito.is_wait and "intervalo mínimo de 300s" in veredito.reason


def test_limite_por_execucao_conta_so_o_que_saiu_desta_execucao(tmp_path: Path) -> None:
    svc, repo, policies, _ = build(tmp_path)
    pid = perfil(svc)
    repo.update_profile(pid, {"automation_policy": '{"limits": {"actions_per_run": 2, "likes_per_hour": 100, '
                                                   '"cooldown_between_external_actions_s": 0}}'})
    _curtidas(svc, pid, 2)
    assert not policies.check(pid, capability_of(IG, "LIKE_POST"), run_id="run-1").allowed
    assert policies.check(pid, capability_of(IG, "LIKE_POST"), run_id="run-2").allowed   # outra execução, outro teto


def test_limites_de_um_perfil_nao_afetam_o_outro(tmp_path: Path) -> None:
    svc, repo, policies, _ = build(tmp_path)
    lucas = perfil(svc, "lucas.almeida9484", "android-01")
    mariana = perfil(svc)
    for pid in (lucas, mariana):
        repo.update_profile(pid, {"automation_policy": '{"limits": {"likes_per_hour": 2, '
                                                       '"cooldown_between_external_actions_s": 0}}'})
    _curtidas(svc, lucas, 2)
    assert not policies.check(lucas, capability_of(IG, "LIKE_POST")).allowed
    assert policies.check(mariana, capability_of(IG, "LIKE_POST")).allowed


def test_limite_padrao_existe_mesmo_sem_configuracao(tmp_path: Path) -> None:
    svc, _, policies, _ = build(tmp_path)
    pid = perfil(svc)
    assert policies.limits_for(pid) == DEFAULT_LIMITS


# ---------------------------------------------------------------- aprovação
def _aprovacoes(tmp_path: Path) -> tuple[ApprovalService, ApprovalStore, Repository, Database, SocialService]:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_path)
    db.migrate()
    bus = EventBus(db)
    repo = Repository(db, bus, cfg.evidence_dir)
    social = SocialService(SocialRepository(db), SecretStore(db, MemoryKeyProvider()), bus,
                           known_instances=lambda: ["android-02"])
    store = ApprovalStore(db)
    return ApprovalService(store, repo), store, repo, db, social


def _etapa_com_conteudo(db: Database, *, step_id: str = "run-1:android-02:v1:send_1",
                        item: str | None = None) -> None:
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-1','k1','responda','execute','running',1,'[]','2026-09-17T10:00:00Z')")
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters)"
               " VALUES ('run-1:android-02','run-1','android-02','waiting_user',1,'{}')")
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
        " bindings, variables) VALUES (?, 'run-1','run-1:android-02','android-02',1,1,'send_1','Enviar para @ana',"
        "'enviar','[]',1,?,'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready',"
        "'SEND_MESSAGE','desc=Send',?,?)",
        (step_id, '["@ana", "bom dia"]', '{"username": "@ana", "content": "bom dia"}',
         f'{{"item": "{item}"}}' if item else None))


def test_execucao_junta_os_textos_e_decide_em_lote(tmp_path: Path) -> None:
    """Com um perfil por aparelho, cada execução abre N aprovações — uma por texto. Lê-las de perfil em perfil é
    inviável: a lista por execução junta todas, e o lote decide cada uma com o seu verbo (aprovar umas, editar
    outras). Uma decisão que falha não derruba nem impede as demais."""
    from app.models import ApprovalDecisionItem

    svc, store, repo, db, social = _aprovacoes(tmp_path)
    _etapa_com_conteudo(db)
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-9','k9','outra','execute','running',1,'[]','2026-09-17T10:00:00Z')")
    p1 = social.create_profile(ProfileCreate(username="lucas.almeida9484", password=SENHA)).id
    p2 = social.create_profile(ProfileCreate(username="mariana.costa91182", password=SENHA)).id
    a1 = store.open(profile_id=p1, capability="CREATE_COMMENT", summary="Comentar", target="@ana",
                    content="texto do lucas", run_id="run-1")
    a2 = store.open(profile_id=p2, capability="CREATE_COMMENT", summary="Comentar", target="@ana",
                    content="texto da mariana", run_id="run-1", objective_id="run-1:android-02",
                    step_id="run-1:android-02:v1:send_1")
    store.open(profile_id=p1, capability="CREATE_COMMENT", summary="Comentar", content="de outra execução",
               run_id="run-9")

    da_execucao = svc.list(run_id="run-1")
    assert [a["id"] for a in da_execucao] == [a1.id, a2.id]            # só esta execução, na ordem em que entraram

    saida = svc.decide_many([
        ApprovalDecisionItem(id=a1.id, verb="approve"),
        ApprovalDecisionItem(id=a2.id, verb="edit", content="na voz da mariana"),
        ApprovalDecisionItem(id=a1.id, verb="approve")])                # repetida: já decidida
    assert [d["status"] for d in saida["decided"]] == ["approved", "edited"]
    assert [r["id"] for r in saida["refused"]] == [a1.id]               # a repetida não derrubou o lote
    assert svc.list(run_id="run-1") == []                               # nada mais pendente nesta execução
    # A edição vale no que vai ser digitado, não só no registro da aprovação.
    assert json.loads(db.one("SELECT bindings FROM steps WHERE id=?",
                             ("run-1:android-02:v1:send_1",))["bindings"])["content"] == "na voz da mariana"


def test_aprovar_libera_a_etapa_sem_marcar_como_concluida(tmp_path: Path) -> None:
    svc, store, repo, db, _ = _aprovacoes(tmp_path)
    _etapa_com_conteudo(db)
    pedido = store.open(profile_id=None, capability="SEND_MESSAGE", summary="Enviar para @ana", target="@ana",
                        content="bom dia", run_id="run-1", objective_id="run-1:android-02",
                        step_id="run-1:android-02:v1:send_1")
    decidido = svc.decide(pedido.id, "approve", note="pode mandar")

    assert decidido["status"] == "approved" and decidido["content"] == "bom dia"
    assert db.one("SELECT status FROM steps WHERE id=?", (pedido.step_id,))["status"] == "ready"   # NÃO virou feita
    assert db.one("SELECT status, blocked_kind FROM objectives WHERE id=?", (pedido.objective_id,))["status"] == "pending"


def test_editar_troca_o_texto_que_vai_ser_digitado_e_guarda_o_original(tmp_path: Path) -> None:
    svc, store, repo, db, _ = _aprovacoes(tmp_path)
    _etapa_com_conteudo(db)
    pedido = store.open(profile_id=None, capability="SEND_MESSAGE", summary="Enviar para @ana", target="@ana",
                        content="bom dia", run_id="run-1", objective_id="run-1:android-02",
                        step_id="run-1:android-02:v1:send_1")
    decidido = svc.decide(pedido.id, "edit", content="bom dia, Ana! tudo certo?")

    assert decidido["status"] == "edited"
    assert decidido["generated_content"] == "bom dia"                      # o original fica para auditoria
    assert decidido["content"] == "bom dia, Ana! tudo certo?"
    etapa = db.one("SELECT bindings, commit_guard FROM steps WHERE id=?", (pedido.step_id,))
    assert "bom dia, Ana! tudo certo?" in etapa["bindings"]
    assert "bom dia, Ana! tudo certo?" in etapa["commit_guard"] and '"bom dia"' not in etapa["commit_guard"]


def test_rejeitar_cancela_so_as_etapas_daquele_alvo(tmp_path: Path) -> None:
    svc, store, repo, db, _ = _aprovacoes(tmp_path)
    _etapa_com_conteudo(db, item="@ana")
    # segunda cópia do bloco, de OUTRO alvo: não pode ser cancelada junto
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, variables)"
        " VALUES ('run-1:android-02:v1:send_2','run-1','run-1:android-02','android-02',1,2,'send_2','Enviar para "
        "@bruno','enviar','[]',1,'[]','{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,"
        "'pending','SEND_MESSAGE','{\"item\": \"@bruno\"}')")
    pedido = store.open(profile_id=None, capability="SEND_MESSAGE", summary="Enviar para @ana", target="@ana",
                        content="bom dia", run_id="run-1", objective_id="run-1:android-02",
                        step_id="run-1:android-02:v1:send_1")
    svc.decide(pedido.id, "reject", note="não faz sentido responder isso")

    assert db.one("SELECT status FROM steps WHERE id='run-1:android-02:v1:send_1'")["status"] == "cancelled"
    assert db.one("SELECT status FROM steps WHERE id='run-1:android-02:v1:send_2'")["status"] == "pending"
    assert db.one("SELECT status FROM objectives WHERE id='run-1:android-02'")["status"] == "pending"


def test_decisao_nao_pode_ser_refeita(tmp_path: Path) -> None:
    svc, store, repo, db, _ = _aprovacoes(tmp_path)
    _etapa_com_conteudo(db)
    pedido = store.open(profile_id=None, capability="SEND_MESSAGE", summary="Enviar", content="oi",
                        run_id="run-1", objective_id="run-1:android-02", step_id="run-1:android-02:v1:send_1")
    svc.decide(pedido.id, "approve")
    from app.social.service import SocialError

    with pytest.raises(SocialError) as exc:
        svc.decide(pedido.id, "reject")
    assert exc.value.code == "already_decided"


def test_editar_sem_texto_e_recusado(tmp_path: Path) -> None:
    svc, store, repo, db, _ = _aprovacoes(tmp_path)
    _etapa_com_conteudo(db)
    pedido = store.open(profile_id=None, capability="SEND_MESSAGE", summary="Enviar", content="oi",
                        run_id="run-1", objective_id="run-1:android-02", step_id="run-1:android-02:v1:send_1")
    from app.social.service import SocialError

    with pytest.raises(SocialError) as exc:
        svc.decide(pedido.id, "edit", content="   ")
    assert exc.value.code == "empty_content"
    assert store.get(pedido.id).status == "pending"


# ---------------------------------------------------------------- histórico do efeito
def test_efeito_disparado_vira_interacao_e_o_desfecho_a_fecha(tmp_path: Path) -> None:
    svc, repo, _, db = build(tmp_path)
    pid = perfil(svc)
    iid = svc.open_effect(pid, capability="SEND_MESSAGE", interaction_type=InteractionType.dm_sent.value,
                          bindings={"username": "@ana", "content": "bom dia"}, run_id="run-1",
                          objective_id="run-1:android-02", step_id="s1", instance_id="android-02")
    aberta = svc.get_interaction(pid, iid)
    assert aberta.status == InteractionStatus.pending and aberta.counterparty == "@ana"

    svc.settle_effect(pid, iid, outcome="succeeded", evidence="mensagem visível na conversa")
    assert svc.get_interaction(pid, iid).status == InteractionStatus.confirmed
    assert repo.relationship_row(pid, "@ana")["interactions"] == 1


def test_efeito_incerto_nao_vira_fato_confirmado(tmp_path: Path) -> None:
    svc, repo, _, _ = build(tmp_path)
    pid = perfil(svc)
    iid = svc.open_effect(pid, capability="FOLLOW", interaction_type=InteractionType.followed.value,
                          bindings={"username": "@ana"}, run_id="run-1")
    svc.settle_effect(pid, iid, outcome="uncertain", evidence="não deu para ler o botão depois do toque")
    assert svc.get_interaction(pid, iid).status == InteractionStatus.uncertain
    assert repo.relationship_row(pid, "@ana") is None
    assert svc.memory.list(pid) == []


# ---------------------------------------------------------------- receita é do app, não do perfil
def test_receita_e_compartilhada_entre_perfis_do_mesmo_app(tmp_path: Path) -> None:
    """Aprender a operar uma tela é conhecimento do APP. Guardar por perfil duplicaria custo de IA sem motivo —
    e é por isso que perfil não faz parte da identidade da receita."""
    from app.taskqueue.recipes import RecipeStore

    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_path)
    db.migrate()
    store = RecipeStore(db)
    identidade = dict(package=IG, app_version="300.0(300)", step_hash="h1", signature="ab12", variant="en-US/xhdpi")
    assert store.save(**identidade, step_key="send_1", actions=[{"tool": "tap"}], learned_from="s1")

    colunas = {c["name"] for c in db.query("PRAGMA table_info(recipes)")}
    assert "profile_id" not in colunas
    achada = store.find(IG, "300.0(300)", "h1", signature="ab12", variant="en-US/xhdpi")
    assert achada is not None
    # mesma etapa, outra variante de interface: NÃO reaproveita (a tela é outra)
    assert store.find(IG, "300.0(300)", "h1", signature="ab12", variant="pt-BR/xhdpi") is None
    # mesma versão com assinatura diferente também não: não é o mesmo app
    assert store.find(IG, "300.0(300)", "h1", signature="ff99", variant="en-US/xhdpi") is None


async def test_cada_perfil_escreve_o_seu_texto_a_partir_do_mesmo_briefing(harness: Any) -> None:
    """O defeito que originou isto: um plano, N aparelhos, e o texto congelado no plano — na execução
    r-20260918181035-7bfa38 quatro perfis com personas opostas publicaram a MESMA frase, byte a byte.

    Agora o texto nasce no gate, por perfil: mesmo briefing, dois perfis, dois textos — e a guarda de commit de
    cada etapa passa a ser o texto daquele aparelho, não uma frase comum."""
    state = harness.state
    db = state.db
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-p','kp','elogiar','execute','running',1,'[\"android-01\",\"android-02\"]',"
               "'2026-09-17T10:00:00Z')")
    escritos: dict[str, str] = {}
    vozes = (("android-01", "lucas.almeida9484", "Direto e sóbrio, sem firula"),
             ("android-02", "mariana.costa91182", "Acolhedor e caloroso, próximo"))
    for iid, usuario, tom in vozes:
        db.execute("UPDATE instances SET app_id='ig' WHERE id=?", (iid,))
        pid = state.social.create_profile(ProfileCreate(username=usuario, password=SENHA, instance_id=iid)).id
        persona = state.social.create_persona(PersonaCreate(name=usuario, traits=PersonaTraits(tone=tom)))
        state.social.update_profile(pid, ProfilePatch(persona_id=persona.id))
        # Política autônoma: aqui interessa o TEXTO, não a porta de aprovação (que tem teste próprio).
        state.social.set_policy(pid, ProfilePolicyPatch(capabilities={"CREATE_COMMENT": "autonomous"}))
        oid = f"run-p:{iid}"
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
                   " VALUES (?,'run-p',?,'running',1,'{}',?)", (oid, iid, pid))
        db.execute(
            "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
            " depends_on, side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability,"
            " commit_selector, bindings) VALUES (?,'run-p',?,?,1,1,'c1','Comentar','comentar','[]',1,'[]',"
            "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready','CREATE_COMMENT',"
            "'id=post','{\"content_brief\": \"elogiar o trabalho do secretario\"}')",
            (f"{oid}:v1:c1", oid, iid))
        obj = db.one("SELECT * FROM objectives WHERE id=?", (oid,))
        srow = db.one("SELECT * FROM steps WHERE id=?", (f"{oid}:v1:c1",))
        run = db.one("SELECT * FROM runs WHERE id='run-p'")
        assert await state._policy_gate(obj, srow, run) is None        # gerou e liberou
        depois = db.one("SELECT bindings, commit_guard FROM steps WHERE id=?", (f"{oid}:v1:c1",))
        texto = (json.loads(depois["bindings"]) or {}).get("content")
        assert texto, f"{iid} ficou sem texto"
        assert texto in json.loads(depois["commit_guard"])              # a guarda passa a travar ESTE texto
        escritos[iid] = texto

    assert escritos["android-01"] != escritos["android-02"], f"os dois perfis escreveram igual: {escritos}"


async def test_porta_de_politica_cria_aprovacao_e_segura_a_etapa(harness: Any) -> None:
    """Integração da porta: etapa com capability de risco não é assumida — vira pedido de aprovação."""
    state = harness.state
    db = state.db
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("UPDATE instances SET app_id='ig' WHERE id='android-01'")
    pid = state.social.create_profile(ProfileCreate(username="mariana.costa91182", password=SENHA,
                                                    instance_id="android-01")).id
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-x','kx','responda','execute','running',1,'[\"android-01\"]','2026-09-17T10:00:00Z')")
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
               " VALUES ('run-x:android-01','run-x','android-01','running',1,'{}',?)", (pid,))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
        " bindings) VALUES ('run-x:android-01:v1:send_1','run-x','run-x:android-01','android-01',1,1,'send_1',"
        "'Enviar a mensagem para @ana','enviar','[]',1,'[\"@ana\"]',"
        "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready','SEND_MESSAGE','desc=Send',"
        "'{\"username\": \"@ana\", \"content\": \"bom dia\", \"content_verbatim\": \"true\"}')")

    obj = db.one("SELECT * FROM objectives WHERE id='run-x:android-01'")
    srow = db.one("SELECT * FROM steps WHERE id='run-x:android-01:v1:send_1'")
    run = db.one("SELECT * FROM runs WHERE id='run-x'")

    veredito = await state._policy_gate(obj, srow, run)
    assert veredito is not None and not veredito.allowed and veredito.policy == "approval_required"
    pendentes = state.approval_service.list()
    assert [(a["capability"], a["target"], a["content"]) for a in pendentes] == [("SEND_MESSAGE", "@ana", "bom dia")]
    assert db.one("SELECT blocked_kind FROM objectives WHERE id='run-x:android-01'")["blocked_kind"] == "approval"

    # decidido, o item volta para a fila e a segunda passagem pela porta libera
    state.approval_service.decide(pendentes[0]["id"], "approve")
    assert await state._policy_gate(db.one("SELECT * FROM objectives WHERE id='run-x:android-01'"), srow, run) is None
    # e "confirmar concluído" não serve para isto: marcaria como feita sem executar
    db.execute("UPDATE objectives SET blocked_kind='approval', status='waiting_user' WHERE id='run-x:android-01'")
    from app.models import ResolveBody
    from app.taskqueue.service import RunError

    with pytest.raises(RunError) as exc:
        state.runs.resolve("run-x", "run-x:android-01", ResolveBody(resolution="confirm_done"))
    assert exc.value.code == "needs_approval"


async def test_etapa_sem_capability_nao_passa_por_nenhuma_porta_nova(harness: Any) -> None:
    """O QA Messenger não tem catálogo: o caminho livre segue exatamente como antes desta fase."""
    state = harness.state
    db = state.db
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-y','ky','enviar','execute','running',1,'[\"android-01\"]','2026-09-17T10:00:00Z')")
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters)"
               " VALUES ('run-y:android-01','run-y','android-01','running',1,'{}')")
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status) VALUES"
        " ('run-y:android-01:v1:send','run-y','run-y:android-01','android-01',1,1,'send','Enviar','enviar','[]',1,"
        "'[]','{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready')")
    obj = db.one("SELECT * FROM objectives WHERE id='run-y:android-01'")
    srow = db.one("SELECT * FROM steps WHERE id='run-y:android-01:v1:send'")
    run = db.one("SELECT * FROM runs WHERE id='run-y'")
    assert await state._policy_gate(obj, srow, run) is None
    assert state.approval_service.list() == []


# ---------------------------------------------------------------- API do portal
async def test_rotas_de_catalogo_politica_e_auditoria(tmp_path: Path) -> None:
    import httpx

    from app.main import create_app

    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    app = create_app(cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            catalogo = (await c.get("/api/capabilities")).json()
            envio = next(x for x in catalogo if x["key"] == "SEND_MESSAGE")
            assert envio["side_effect"] and envio["default_policy"] == "approval_required"
            # `content` saiu dos obrigatórios: quem escreve é cada perfil, a partir de `content_brief`.
            assert envio["bindings"] == ["username"]
            assert "content_brief" in envio["optional_bindings"]
            assert all(x["key"] != "AUTHENTICATE_INSTAGRAM" for x in catalogo)
            assert (await c.get("/api/capabilities", params={"package": "com.pocqa.messenger"})).json() == []

            pid = (await c.post("/api/instagram/profiles",
                                json={"username": "mariana.costa91182", "password": SENHA})).json()["id"]

            politica = (await c.get(f"/api/instagram/profiles/{pid}/policy")).json()
            assert politica["capabilities"]["LIKE_POST"] == "autonomous"
            assert politica["limits"]["likes_per_hour"] == DEFAULT_LIMITS["likes_per_hour"]

            mudado = await c.put(f"/api/instagram/profiles/{pid}/policy",
                                 json={"capabilities": {"LIKE_POST": "approval_required"},
                                       "limits": {"likes_per_hour": 5}})
            assert mudado.status_code == 200
            assert mudado.json()["capabilities"]["LIKE_POST"] == "approval_required"
            assert mudado.json()["defaults"]["LIKE_POST"] == "autonomous"      # o padrão continua visível ao lado
            assert mudado.json()["limits"]["likes_per_hour"] == 5

            ruim = await c.put(f"/api/instagram/profiles/{pid}/policy", json={"capabilities": {"DANCAR": "autonomous"}})
            assert ruim.status_code == 400 and ruim.json()["detail"]["code"] == "unknown_capability"
            ruim2 = await c.put(f"/api/instagram/profiles/{pid}/policy", json={"limits": {"curtidas": 5}})
            assert ruim2.status_code == 400 and ruim2.json()["detail"]["code"] == "unknown_limit"

            assert (await c.get(f"/api/instagram/profiles/{pid}/auth-attempts")).json() == []
            assert (await c.get(f"/api/instagram/profiles/{pid}/runs")).json() == []
            assert (await c.get("/api/instagram/profiles/ig-nao-existe/policy")).status_code == 404
