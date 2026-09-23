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

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.automation.hierarchy import parse_hierarchy
from app.db import Database
from app.events import EventBus
from app.models import (InteractionStatus, InteractionType, PersonaCreate, PersonaTraits, ProfileCreate,
                        ProfilePatch, ProfilePolicyPatch, ResolveBody)
from app.planning.capabilities import (CapabilityCatalog, CapabilityNode, capability_of, compose, load_catalog,
                                       texto_a_gerar)
from app.security.secret_store import MemoryKeyProvider, SecretStore
from app.social.approvals import ApprovalService, ApprovalStore, apply_edit
from app.social.policy import DEFAULT_LIMITS, PolicyEngine
from app.social.repository import SocialRepository
from app.social.service import SocialService
from app.taskqueue.executor import guard_variants
from app.taskqueue.repository import Repository
from app.util import now, to_iso

from .conftest import make_config

SENHA = "$a=B7ee1#<b-C?S-{"
IG = "com.instagram.android"


def build(tmp_path: Path) -> tuple[SocialService, SocialRepository, PolicyEngine, Database]:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    # `db_dsn` e nao `db_path`: assim o teste segue `TEST_DATABASE_URL` e roda de verdade no PostgreSQL
    # quando a suite e apontada para la. Com `db_path` ele abriria SQLite mesmo dentro da corrida do
    # outro banco — cobertura que parece existir e nao existe.
    db = Database(cfg.db_dsn)
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


def test_toda_capability_que_escreve_declara_o_tipo_do_texto() -> None:
    """`_TIPO_DE_TEXTO` diz à persona QUE tipo de texto ela está escrevendo (comentário, resposta, mensagem).

    A consulta usa um padrão ("dm_initiate"), então uma capability NOVA com `needs_draft` passaria calada e sairia
    com voz de mensagem privada num comentário público — errado de um jeito que ninguém vê no log, só no feed.
    Aqui isso vira erro de teste na hora de adicionar a capability, não texto torto em produção.
    """
    from app.state import _TIPO_DE_TEXTO

    escrevem = {c.key for c in load_catalog(IG).offered if c.needs_draft}
    assert escrevem, "nenhuma capability escreve texto — o catálogo mudou de forma inesperada"
    faltando = sorted(escrevem - set(_TIPO_DE_TEXTO))
    assert not faltando, f"capability que escreve sem tipo de texto declarado: {faltando}"


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
    # `warmup_days: 0` desliga o aquecimento de conta nova (achado #114): este teste mede o teto por hora puro,
    # não a redução de aquecimento — que tem teste dedicado em test_aquecimento_reduz_o_teto_de_conta_nova.
    repo.update_profile(pid, {"automation_policy": '{"limits": {"likes_per_hour": 3, "warmup_days": 0, '
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


# ---------------------------------------------------------------- teto diário e aquecimento (achado #114)
def test_teto_diario_represa_mesmo_com_teto_por_hora_alto(tmp_path: Path) -> None:
    svc, repo, policies, _ = build(tmp_path)
    pid = perfil(svc)
    repo.update_profile(pid, {"automation_policy": '{"limits": {"likes_per_hour": 100, "likes_per_day": 3, '
                                                   '"warmup_days": 0, "cooldown_between_external_actions_s": 0}}'})
    _curtidas(svc, pid, 2)
    assert policies.check(pid, capability_of(IG, "LIKE_POST"), run_id="run-1").allowed
    _curtidas(svc, pid, 1)
    veredito = policies.check(pid, capability_of(IG, "LIKE_POST"), run_id="run-1")
    assert veredito.is_wait and veredito.retry_at
    assert "limite de 3 likes por dia" in veredito.reason


def test_aquecimento_reduz_o_teto_de_conta_nova(tmp_path: Path) -> None:
    svc, repo, policies, _ = build(tmp_path)
    pid = perfil(svc)
    # padrão: warmup_days=3, warmup_percent=34 — likes_per_hour=10 vira teto efetivo 3 (10*34//100)
    repo.update_profile(pid, {"automation_policy": '{"limits": {"likes_per_hour": 10, '
                                                   '"cooldown_between_external_actions_s": 0}}'})
    _curtidas(svc, pid, 3)
    veredito = policies.check(pid, capability_of(IG, "LIKE_POST"), run_id="run-1")
    assert veredito.is_wait
    assert "aquecimento" in veredito.reason

    # perfil "velho" (fora da janela de aquecimento): o mesmo teto de 10 vale inteiro
    repo.update_profile(pid, {"created_at": to_iso(now() - timedelta(days=10))})
    veredito = policies.check(pid, capability_of(IG, "LIKE_POST"), run_id="run-1")
    assert veredito.allowed


# ---------------------------------------------------------------- coordenação de frota (achado #114)
class _FleetSettings:
    def __init__(self, **over: Any):
        self.fleet_max_accounts_per_target = over.get("max_contas", 2)
        self.fleet_target_window_s = over.get("janela_s", 3600)
        self.fleet_min_spacing_between_accounts_s = over.get("espaco_s", 0)
        self.fleet_spacing_jitter_s = over.get("jitter_s", 0)


def test_frota_bloqueia_a_conta_seguinte_apos_o_teto_de_contas_no_mesmo_alvo(tmp_path: Path) -> None:
    svc, repo, _, db = build(tmp_path)
    lucas = perfil(svc, "lucas.almeida9484", "android-01")
    mariana = perfil(svc)
    for pid in (lucas, mariana):
        repo.update_profile(pid, {"automation_policy": '{"limits": {"warmup_days": 0}}'})
    policies = PolicyEngine(repo, lambda: _FleetSettings(max_contas=1))
    svc.record_interaction(lucas, type=InteractionType.followed.value, direction="outbound",
                           status=InteractionStatus.confirmed.value, counterparty="@alvo.comum", run_id="run-1")
    # lucas seguiu @alvo.comum: mariana (outra conta) tentando o MESMO alvo esbarra no teto de frota (1 conta)
    veredito = policies.check(mariana, capability_of(IG, "FOLLOW"), run_id="run-2", counterparty="@alvo.comum")
    assert veredito.is_wait
    assert "outra(s) conta(s) da frota" in veredito.reason
    # um alvo DIFERENTE não é afetado pelo que aconteceu com @alvo.comum
    assert policies.check(mariana, capability_of(IG, "FOLLOW"), run_id="run-2", counterparty="@outra.pessoa").allowed


def test_frota_espaca_acoes_de_contas_diferentes_sobre_o_mesmo_alvo(tmp_path: Path) -> None:
    svc, repo, _, db = build(tmp_path)
    lucas = perfil(svc, "lucas.almeida9484", "android-01")
    mariana = perfil(svc)
    for pid in (lucas, mariana):
        repo.update_profile(pid, {"automation_policy": '{"limits": {"warmup_days": 0}}'})
    # teto de contas alto (5): o que bloqueia aqui é só o espaçamento, sem jitter (determinístico)
    policies = PolicyEngine(repo, lambda: _FleetSettings(max_contas=5, espaco_s=600, jitter_s=0))
    svc.record_interaction(lucas, type=InteractionType.dm_sent.value, direction="outbound",
                           status=InteractionStatus.confirmed.value, counterparty="@alvo.comum", run_id="run-1")
    veredito = policies.check(mariana, capability_of(IG, "SEND_MESSAGE"), run_id="run-2", counterparty="@alvo.comum")
    assert veredito.is_wait and veredito.retry_at
    assert "espaçando ações" in veredito.reason


# ---------------------------------------------------------------- afrouxar política (achado #114)
def test_afrouxar_acao_de_risco_alto_e_aceito_e_marcado(tmp_path: Path) -> None:
    """O perfil sempre pôde afrouxar (o docstring antigo dizia o contrário); agora a diferença fica visível."""
    svc, repo, _, db = build(tmp_path)
    pid = perfil(svc)
    politica = svc.set_policy(pid, ProfilePolicyPatch(capabilities={"SEND_MESSAGE": "autonomous"}), package=IG)
    assert politica.capabilities["SEND_MESSAGE"] == "autonomous"    # não é recusado
    assert "SEND_MESSAGE" in politica.loosened                      # mas fica marcado: mais frouxo que o padrão
    aviso = db.query("SELECT message FROM events WHERE level='warn' ORDER BY id DESC LIMIT 1")
    assert aviso and "afrouxado" in aviso[0]["message"]


# ---------------------------------------------------------------- aprovação
def _aprovacoes(tmp_path: Path) -> tuple[ApprovalService, ApprovalStore, Repository, Database, SocialService]:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    # `db_dsn` e nao `db_path`: assim o teste segue `TEST_DATABASE_URL` e roda de verdade no PostgreSQL
    # quando a suite e apontada para la. Com `db_path` ele abriria SQLite mesmo dentro da corrida do
    # outro banco — cobertura que parece existir e nao existe.
    db = Database(cfg.db_dsn)
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


def test_aprovar_com_texto_e_recusado_em_vez_de_aceito_calado(tmp_path: Path) -> None:
    """`approve` com texto era aceito, gravado em `approved_content` e devolvido no 200 — e o aparelho digitava
    o outro. Quem chama pela API acreditaria ter aprovado o que mandou. Trocar texto é `edit`, e só."""
    svc, store, _repo, db, _ = _aprovacoes(tmp_path)
    _etapa_com_conteudo(db)
    pedido = store.open(profile_id=None, capability="SEND_MESSAGE", summary="Enviar", content="bom dia",
                        run_id="run-1", objective_id="run-1:android-02", step_id="run-1:android-02:v1:send_1")
    from app.social.service import SocialError

    with pytest.raises(SocialError) as exc:
        svc.decide(pedido.id, "approve", content="outro texto")
    assert exc.value.code == "content_not_allowed"
    assert store.get(pedido.id).status == "pending"               # a decisão não aconteceu pela metade


def test_texto_com_quebra_de_linha_nao_deixa_duas_guardas_contraditorias(tmp_path: Path) -> None:
    """O modelo devolve "…lindo!\\n" às vezes. Se a guarda ficasse crua, a edição seguinte não a reconheceria e a
    etapa passaria a exigir o texto VELHO e o NOVO visíveis ao mesmo tempo — commit rejeitado até a etapa morrer,
    depois de a pessoa ter aprovado, sem nenhuma pista da causa."""
    from app.social.approvals import definir_texto

    _svc, _store, _repo, db, _ = _aprovacoes(tmp_path)
    _etapa_com_conteudo(db)
    step_id = "run-1:android-02:v1:send_1"

    definir_texto(db, step_id, "Que post lindo!\n")
    definir_texto(db, step_id, "Na voz da mariana, com carinho.")

    guardas = json.loads(db.one("SELECT commit_guard FROM steps WHERE id=?", (step_id,))["commit_guard"])
    assert "Que post lindo!" not in guardas and "Que post lindo!\n" not in guardas
    assert guardas.count("Na voz da mariana, com carinho.") == 1
    assert json.loads(db.one("SELECT bindings FROM steps WHERE id=?",
                             (step_id,))["bindings"])["content"] == "Na voz da mariana, com carinho."


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


async def test_recusa_sobrevive_a_recuperacao_mas_item_que_falhou_ainda_volta(harness: Any) -> None:
    """Duas coisas ficam `cancelled` e elas NÃO são a mesma coisa.

    Quem rejeita ouve “as etapas deste alvo foram canceladas”. Se qualquer outra etapa do objetivo falhar depois,
    `recovery_steps` monta o plano do que falta — e recriar a chave recusada abriria, com um texto novo, uma
    aprovação que a pessoa já tinha negado. Essa fronteira é definitiva.

    Já `_skip_failed_item` cancela o resto de um item que falhou, e essa tem de voltar: “Tentar novamente” passa
    pelo mesmo `recovery_steps` e promete refazer justamente os itens que falharam."""
    from app.models import Plan, PlannerInfo, PlanStep, Postcondition

    state = harness.state
    db = state.db
    post = Postcondition(kind="model_judged", value="x", description="y")
    plano = Plan(summary="responder", planner=PlannerInfo(provider="fake", model="t", simulated=True),
                 steps=[PlanStep(key=k, title=k, goal="g", side_effect=True, postcondition=post)
                        for k in ("abrir", "send_i1", "send_i2")])
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
               " plan) VALUES ('run-r','kr','responda','execute','running',1,'[\"android-01\"]',"
               "'2026-09-17T10:00:00Z',?)", (plano.model_dump_json(),))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters)"
               " VALUES ('run-r:android-01','run-r','android-01','running',1,'{}')")
    for seq, (key, detalhe) in enumerate(
            (("abrir", None),
             ("send_i1", "rejeitado por quem aprova: não faz sentido responder isso"),
             ("send_i2", "item “@bruno” falhou antes desta etapa")), start=1):
        db.execute(
            "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
            " depends_on, side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, status_detail)"
            " VALUES (?,'run-r','run-r:android-01','android-01',1,?,?,?,'g','[]',1,'[]',"
            "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,?,?)",
            (f"run-r:android-01:v1:{key}", seq, key, key, "cancelled" if detalhe else "failed", detalhe))

    run = db.one("SELECT * FROM runs WHERE id='run-r'")
    chaves = {s.key for s in state.scheduler.recovery_steps(run, "run-r:android-01")}

    assert "send_i1" not in chaves            # recusa de uma pessoa: não renasce com outro texto
    assert "send_i2" in chaves                # item que falhou: “Tentar novamente” tem de alcançá-lo
    assert "abrir" in chaves


async def test_cancelar_a_execucao_expira_a_aprovacao_pendente(harness: Any) -> None:
    """Achado #109: `approvals.expire_for_objective` existia sem chamador — cancelar a execução deixava o pedido
    pendente na fila 'Aguardando aprovação', e uma pessoa podia decidir um pedido cuja etapa já morreu."""
    state = harness.state
    db = state.db
    post_json = '{"kind":"model_judged","value":"x","description":"y"}'
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-c','kc','responda','execute','running',1,'[\"android-01\"]','2026-09-17T10:00:00Z')")
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters)"
               " VALUES ('run-c:android-01','run-c','android-01','waiting_user',1,'{}')")
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
        " depends_on, side_effect, commit_guard, postcondition, timeout_s, max_attempts, status)"
        " VALUES ('run-c:android-01:v1:send_1','run-c','run-c:android-01','android-01',1,1,'send_1','Enviar',"
        "'g','[]',1,'[]',?,180,1,'waiting_user')", (post_json,))
    pedido = state.approvals.open(profile_id=None, capability="SEND_MESSAGE", summary="Enviar", content="bom dia",
                                  run_id="run-c", objective_id="run-c:android-01",
                                  step_id="run-c:android-01:v1:send_1")
    assert state.approvals.get(pedido.id).status == "pending"

    state.runs.cancel("run-c")
    state.scheduler._tick()

    assert state.approvals.get(pedido.id).status == "expired"


def test_o_tempo_de_quem_decide_nao_conta_contra_o_prazo_do_objetivo(tmp_path: Path) -> None:
    """Medido no banco real: seis aprovações decididas às 13:12 de 19/09 e NENHUMA publicada. O objetivo voltou
    à fila, o scheduler pegou o aparelho e matou a etapa no mesmo segundo — "Tempo total do objetivo esgotado",
    porque o relógio (`objective_timeout_s`, 15 min por padrão) correu enquanto o pedido esperava decisão.

    Esperar por uma pessoa é o propósito da aprovação, não lentidão da máquina: esse tempo entra em `paused_s`,
    como já entrava o tempo represado por limite de perfil."""
    svc, store, _repo, db, _ = _aprovacoes(tmp_path)
    _etapa_com_conteudo(db)
    uma_hora_atras = to_iso(now() - timedelta(hours=1))
    db.execute("UPDATE objectives SET started_at=?, paused_s=0 WHERE id='run-1:android-02'", (uma_hora_atras,))
    pedido = store.open(profile_id=None, capability="SEND_MESSAGE", summary="Enviar", content="bom dia",
                        run_id="run-1", objective_id="run-1:android-02",
                        step_id="run-1:android-02:v1:send_1")
    db.execute("UPDATE pending_approvals SET created_at=? WHERE id=?", (uma_hora_atras, pedido.id))

    svc.decide(pedido.id, "approve")

    # a hora que a pessoa levou para decidir foi creditada: o prazo do objetivo segue com o saldo que tinha
    assert db.one("SELECT paused_s FROM objectives WHERE id='run-1:android-02'")["paused_s"] >= 3500


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


def test_aprovacao_aponta_para_a_interacao_que_ela_liberou(tmp_path: Path) -> None:
    """Rascunho → aprovação → efeito tem de ser um fio só: sem o elo, um texto aprovado não leva ao que foi publicado."""
    svc, store, _, db, _ = _aprovacoes(tmp_path)
    _etapa_com_conteudo(db)
    step_id = "run-1:android-02:v1:send_1"
    pedido = store.open(profile_id=None, capability="SEND_MESSAGE", summary="Enviar", content="bom dia",
                        run_id="run-1", objective_id="run-1:android-02", step_id=step_id)
    assert store.get(pedido.id).interaction_id is None       # a aprovação nasce ANTES de existir interação

    store.link_interaction(step_id, "int-abc")
    assert store.get(pedido.id).interaction_id == "int-abc"
    store.link_interaction(step_id, "int-outra")             # o primeiro efeito é o que conta
    assert store.get(pedido.id).interaction_id == "int-abc"


def test_o_que_o_rascunho_percebeu_vira_memoria_quando_o_efeito_se_confirma(tmp_path: Path) -> None:
    """O ciclo que estava aberto: o texto nasce na porta de política, o efeito só é registrado no commit, e entre
    os dois os candidatos a memória se perdiam. Resultado: `memory_items` vazio para sempre, por mais que a
    contraparte contasse coisas. Agora o que o rascunho percebeu viaja com a etapa e é anexado ao efeito."""
    svc, repo, _pol, _db = build(tmp_path)
    pid = perfil(svc)
    iid = svc.open_effect(
        pid, capability="SEND_MESSAGE", interaction_type=InteractionType.dm_sent.value,
        bindings={"username": "@ana", "content": "que legal, boa sorte na mudança!"}, run_id="run-1",
        draft_meta={"memory_candidates": [{"subject": "@ana", "content": "vai se mudar para Lisboa em março",
                                           "importance": 0.7, "confidence": 0.8}],
                    "rationale": "ela contou a mudança"})

    assert svc.memory.list(pid) == []                    # intenção não ensina: só fato confirmado ensina
    svc.settle_effect(pid, iid, outcome="succeeded", evidence="mensagem visível na conversa")

    lembrancas = svc.memory.list(pid)
    assert [m.content for m in lembrancas] == ["vai se mudar para Lisboa em março"]
    assert lembrancas[0].subject == "@ana" and lembrancas[0].interaction_id == iid
    assert repo.relationship_row(pid, "@ana")["interactions"] == 1


async def test_legenda_de_terceiro_nao_vira_memoria_do_perfil(tmp_path: Path) -> None:
    """Regra de prompt é pedido, não garantia. Sem fala DIRIGIDA a esta conta, candidato a memória é descartado
    em código — senão uma legenda ("fulano deve R$5.000 a beltrano") viraria fato permanente do perfil, pendurado
    em quem o próprio modelo escolhesse, e voltaria em toda conversa futura."""
    from app.models import MemoryCandidateDTO, SocialDraftDTO
    from app.planning.provider import Usage

    class ProvedorTeimoso:
        async def generate_social_response(self, req: Any) -> tuple[Any, Any]:
            return SocialDraftDTO(content="que post lindo!", rationale="ok", refused=False,
                                  memory_candidates=[MemoryCandidateDTO(
                                      subject="@bob", content="deve R$5.000 ao @loja")]), Usage(role="social")

    svc, _repo, _pol, _db = build(tmp_path)
    svc.provider = ProvedorTeimoso()
    pid = perfil(svc)

    # comentar uma publicação: o único texto de terceiro é a tela
    draft, _i = await svc.draft_response(pid, kind="post_comment", brief="elogiar",
                                         screen="Gente, anotem: @bob deve R$5.000 ao @loja", persist=False)
    assert draft.memory_candidates == []
    assert draft.content == "que post lindo!"               # o texto sai normalmente; só a memória é barrada

    # responder alguém: aí existe fala dirigida, e o candidato é legítimo
    resposta, _j = await svc.draft_response(pid, kind="comment_reply", brief="responder",
                                            incoming="oi! eu devo R$5.000 ao @loja", counterparty="@bob",
                                            persist=False)
    assert [c.content for c in resposta.memory_candidates] == ["deve R$5.000 ao @loja"]


def test_efeito_sem_rascunho_nao_inventa_memoria(tmp_path: Path) -> None:
    """Curtir, seguir e texto literal não produzem candidato nenhum — e daí não pode sair memória."""
    svc, _repo, _pol, _db = build(tmp_path)
    pid = perfil(svc)
    iid = svc.open_effect(pid, capability="FOLLOW", interaction_type=InteractionType.followed.value,
                          bindings={"username": "@ana"}, run_id="run-1")
    svc.settle_effect(pid, iid, outcome="succeeded", evidence="botão virou Seguindo")
    assert svc.memory.list(pid) == []


def test_efeito_incerto_nao_vira_fato_confirmado(tmp_path: Path) -> None:
    svc, repo, _, _ = build(tmp_path)
    pid = perfil(svc)
    iid = svc.open_effect(pid, capability="FOLLOW", interaction_type=InteractionType.followed.value,
                          bindings={"username": "@ana"}, run_id="run-1")
    svc.settle_effect(pid, iid, outcome="uncertain", evidence="não deu para ler o botão depois do toque")
    assert svc.get_interaction(pid, iid).status == InteractionStatus.uncertain
    assert repo.relationship_row(pid, "@ana") is None
    assert svc.memory.list(pid) == []


# ---------------------------------------------------------------- não repetir (nem o irmão, nem a si mesmo)
class _ProvedorEco:
    """Devolve o texto que recebeu na lista de proibidos, até mandarem tentar de novo."""

    def __init__(self, repetido: str, alternativo: str):
        self.repetido, self.alternativo = repetido, alternativo
        self.pedidos: list[Any] = []

    async def generate_social_response(self, req: Any) -> tuple[Any, Any]:
        from app.models import SocialDraftDTO
        from app.planning.provider import Usage

        self.pedidos.append(req)
        texto = self.alternativo if req.retry else self.repetido
        return SocialDraftDTO(content=texto, rationale="teste", refused=False), Usage(role="social")


async def test_texto_igual_ao_de_outro_perfil_e_reescrito_uma_vez(tmp_path: Path) -> None:
    """Pedir para não repetir não impede repetir. Se o texto sair igual a um que já existe, gera de novo — uma vez."""
    svc, _repo, _pol, _db = build(tmp_path)
    prov = _ProvedorEco("O secretário faz um trabalho excelente!", "Gostei demais do que vi ali, viu!")
    svc.provider = prov
    pid = perfil(svc)

    draft, _i = await svc.draft_response(pid, kind="post_comment", brief="elogiar o trabalho",
                                         avoid=["  o secretario faz um TRABALHO excelente  "], persist=False)

    # o irmão entrou na lista de proibidos, mesmo com acento, caixa e espaço diferentes
    assert prov.pedidos[0].avoid == ("o secretario faz um TRABALHO excelente",)
    assert len(prov.pedidos) == 2 and prov.pedidos[1].retry is True
    assert draft.content == "Gostei demais do que vi ali, viu!"


async def test_o_perfil_tambem_nao_repete_a_si_mesmo(tmp_path: Path) -> None:
    """O que este perfil já publicou entra sozinho na lista: repetir a si mesmo denuncia tanto quanto copiar o vizinho."""
    svc, _repo, _pol, _db = build(tmp_path)
    prov = _ProvedorEco("Que orgulho desse time!", "Fiquei feliz de ver isso hoje.")
    svc.provider = prov
    pid = perfil(svc)
    svc.record_interaction(pid, type=InteractionType.comment_replied.value, direction="outbound",
                           status=InteractionStatus.confirmed.value, outgoing_content="Que orgulho desse time!")

    draft, _i = await svc.draft_response(pid, kind="post_comment", brief="elogiar", persist=False)

    assert "Que orgulho desse time!" in prov.pedidos[0].avoid      # veio do histórico, sem ninguém pedir
    assert draft.content == "Fiquei feliz de ver isso hoje."

    # E continua valendo quando o perfil recebeu coisas depois: o filtro é SQL, então o limite conta só os
    # textos PRÓPRIOS. Com o filtro em Python, bastavam algumas curtidas para o histórico sumir da lista.
    for _ in range(10):
        svc.record_interaction(pid, type=InteractionType.post_liked.value, direction="outbound",
                               status=InteractionStatus.confirmed.value)
    assert "Que orgulho desse time!" in svc._textos_recentes(pid)


def test_os_textos_dos_irmaos_desta_execucao_chegam_a_quem_escreve(tmp_path: Path) -> None:
    from app.social.approvals import textos_irmaos

    _svc, _repo, _pol, db = build(tmp_path)
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-i','ki','elogiar','execute','running',1,'[]','2026-09-17T10:00:00Z')")
    for i, conteudo in enumerate(('{"content": "texto do um"}', '{"content": "texto do dois"}', '{"content_brief": "x"}')):
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters)"
                   " VALUES (?,'run-i',?,'running',1,'{}')", (f"o{i}", f"android-0{i}"))
        db.execute(
            "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
            " depends_on, side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, bindings)"
            " VALUES (?,'run-i',?,?,1,1,'c1','Comentar','comentar','[]',1,'[]','{}',180,1,'ready',?)",
            (f"s{i}", f"o{i}", f"android-0{i}", conteudo))

    # o próprio não entra; quem ainda não escreveu (só briefing) também não
    assert textos_irmaos(db, "run-i", "s0") == ["texto do dois"]
    assert textos_irmaos(db, "run-i", "s1") == ["texto do um"]


# ---------------------------------------------------------------- receita é do app, não do perfil
def test_receita_e_compartilhada_entre_perfis_do_mesmo_app(tmp_path: Path) -> None:
    """Aprender a operar uma tela é conhecimento do APP. Guardar por perfil duplicaria custo de IA sem motivo —
    e é por isso que perfil não faz parte da identidade da receita."""
    from app.taskqueue.recipes import RecipeStore

    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    # `db_dsn` e nao `db_path`: assim o teste segue `TEST_DATABASE_URL` e roda de verdade no PostgreSQL
    # quando a suite e apontada para la. Com `db_path` ele abriria SQLite mesmo dentro da corrida do
    # outro banco — cobertura que parece existir e nao existe.
    db = Database(cfg.db_dsn)
    db.migrate()
    store = RecipeStore(db)
    identidade = dict(package=IG, app_version="300.0(300)", step_hash="h1", signature="ab12", variant="en-US/xhdpi")
    assert store.save(**identidade, step_key="send_1", actions=[{"tool": "tap"}], learned_from="s1")

    assert "profile_id" not in db.columns("recipes")     # receita e do APP, nao do perfil
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
        depois = db.one("SELECT bindings, commit_guard, draft_meta FROM steps WHERE id=?", (f"{oid}:v1:c1",))
        texto = (json.loads(depois["bindings"]) or {}).get("content")
        assert texto, f"{iid} ficou sem texto"
        assert texto in json.loads(depois["commit_guard"])              # a guarda passa a travar ESTE texto
        # Escrever não é agir: o rascunho NÃO vira interação. Se virasse, gastaria a cota da conta antes de
        # digitar qualquer coisa e contaria duas vezes o que fosse de fato enviado.
        assert state.social_repo.count_interactions(pid) == 0
        # O que o rascunho percebeu fica guardado NA ETAPA: é o que o commit vai anexar ao efeito, e é o que
        # sobrevive às horas de espera por aprovação e a um reinício do backend.
        guardado = json.loads(depois["draft_meta"] or "{}")
        assert "memory_candidates" in guardado and guardado["rationale"]
        # Escrever é chamada de modelo DENTRO da execução: entra no orçamento do objetivo como qualquer outra.
        # Se corresse por fora, oito aparelhos furariam juntos o teto de chamadas simultâneas e o gasto não
        # apareceria em nenhum dos dois contadores.
        assert db.one("SELECT ai_calls FROM objectives WHERE id=?", (oid,))["ai_calls"] >= 1
        assert db.one("SELECT COUNT(*) n FROM ai_calls WHERE run_id='run-p' AND role='social'")["n"] >= 1
        escritos[iid] = texto

    assert escritos["android-01"] != escritos["android-02"], f"os dois perfis escreveram igual: {escritos}"


class _ProvedorQueCede:
    """Escreve um texto por chamada e cede o controle no meio da geração: sem nada segurando, os dois aparelhos
    entram juntos e os dois leem a lista de irmãos VAZIA."""

    def __init__(self) -> None:
        self.pedidos: list[Any] = []

    async def generate_social_response(self, req: Any) -> tuple[Any, Any]:
        from app.models import SocialDraftDTO
        from app.planning.provider import Usage

        self.pedidos.append(req)
        meu = f"texto do aparelho numero {len(self.pedidos)}"
        for _ in range(8):
            await asyncio.sleep(0)            # janela larga para o irmão entrar, se nada o segurar
        return SocialDraftDTO(content=meu, rationale="teste", refused=False), Usage(role="social")


async def test_dois_aparelhos_escrevendo_juntos_ainda_enxergam_o_texto_um_do_outro(harness: Any) -> None:
    """`<nao_repita>` é lido do que os IRMÃOS já escreveram — uma consulta ao banco. Com os aparelhos da mesma
    execução gerando em paralelo (que é como o scheduler roda), todos consultam antes de qualquer um gravar,
    todos leem lista vazia e a anti-repetição vira enfeite: exatamente o defeito que esta série conserta.

    A escrita é serializada POR EXECUÇÃO. Custa fila (o último aparelho espera os outros escreverem), e é o preço
    de a lista existir de verdade."""
    state = harness.state
    db = state.db
    prov = _ProvedorQueCede()
    state.social.provider = prov
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-c','kc','elogiar','execute','running',1,'[\"android-01\",\"android-02\"]',"
               "'2026-09-17T10:00:00Z')")
    portas = []
    for iid, usuario in (("android-01", "lucas.almeida9484"), ("android-02", "mariana.costa91182")):
        db.execute("UPDATE instances SET app_id='ig' WHERE id=?", (iid,))
        pid = state.social.create_profile(ProfileCreate(username=usuario, password=SENHA, instance_id=iid)).id
        state.social.set_policy(pid, ProfilePolicyPatch(capabilities={"CREATE_COMMENT": "autonomous"}))
        oid = f"run-c:{iid}"
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
                   " VALUES (?,'run-c',?,'running',1,'{}',?)", (oid, iid, pid))
        db.execute(
            "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
            " depends_on, side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability,"
            " commit_selector, bindings) VALUES (?,'run-c',?,?,1,1,'c1','Comentar','comentar','[]',1,'[]',"
            "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready','CREATE_COMMENT',"
            "'id=post','{\"content_brief\": \"elogiar o trabalho\"}')",
            (f"{oid}:v1:c1", oid, iid))
        portas.append((db.one("SELECT * FROM objectives WHERE id=?", (oid,)),
                       db.one("SELECT * FROM steps WHERE id=?", (f"{oid}:v1:c1",))))

    run = db.one("SELECT * FROM runs WHERE id='run-c'")
    await asyncio.gather(*(state._policy_gate(o, s, run) for o, s in portas))

    assert len(prov.pedidos) == 2
    # O segundo a escrever viu o texto do primeiro. Sem a fila por execução, os dois teriam visto `()`.
    assert prov.pedidos[1].avoid == ("texto do aparelho numero 1",)
    textos = {json.loads(db.one("SELECT bindings FROM steps WHERE id=?", (s["id"],))["bindings"])["content"]
              for _o, s in portas}
    assert len(textos) == 2

    # A fila é por execução, não global: quando a execução acaba, o lock dela some do processo.
    assert "run-c" in state._draft_locks
    state.scheduler.on_run_settled("run-c")
    assert "run-c" not in state._draft_locks


async def test_o_texto_aprovado_e_o_texto_que_vai_ser_digitado(harness: Any) -> None:
    """A porta é atravessada DE NOVO quando o objetivo é retomado — e retomar é exatamente o que aprovar faz.

    Enquanto o rascunho não era marcado como fechado, a segunda passagem gerava outro texto por cima: a pessoa
    lia e aprovava uma frase e o aparelho digitava outra. Aprovação que não vale para o texto aprovado não é
    aprovação nenhuma.
    """
    state = harness.state
    db = state.db
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("UPDATE instances SET app_id='ig' WHERE id='android-01'")
    pid = state.social.create_profile(ProfileCreate(username="lucas.almeida9484", password=SENHA,
                                                    instance_id="android-01")).id
    persona = state.social.create_persona(PersonaCreate(name="lucas", traits=PersonaTraits(tone="Direto")))
    state.social.update_profile(pid, ProfilePatch(persona_id=persona.id))
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-a','ka','comentar','execute','running',1,'[\"android-01\"]','2026-09-17T10:00:00Z')")
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
               " VALUES ('run-a:android-01','run-a','android-01','running',1,'{}',?)", (pid,))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
        " bindings) VALUES ('run-a:android-01:v1:c1','run-a','run-a:android-01','android-01',1,1,'c1','Comentar',"
        "'comentar','[]',1,'[]','{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready',"
        "'CREATE_COMMENT','id=post','{\"content_brief\": \"elogiar o post\"}')")
    obj = db.one("SELECT * FROM objectives WHERE id='run-a:android-01'")
    srow = db.one("SELECT * FROM steps WHERE id='run-a:android-01:v1:c1'")
    run = db.one("SELECT * FROM runs WHERE id='run-a'")

    veredito = await state._policy_gate(obj, srow, run)
    assert veredito is not None and veredito.policy == "approval_required"
    pendentes = state.approval_service.list()
    assert len(pendentes) == 1
    rascunho = pendentes[0]["content"]
    assert rascunho

    # a pessoa lê, edita e aprova: ESTE é o texto combinado
    state.approval_service.decide(pendentes[0]["id"], "edit", content="Muito bom mesmo, parabéns pelo trabalho.")
    obj = db.one("SELECT * FROM objectives WHERE id='run-a:android-01'")
    srow = db.one("SELECT * FROM steps WHERE id='run-a:android-01:v1:c1'")

    # Etapa rascunhada ANTES de a coluna existir não tem marca nenhuma: quem a protege é o próprio pedido de
    # aprovação, que só existe porque aquele texto já foi mostrado a alguém.
    db.execute("UPDATE steps SET draft_meta=NULL WHERE id='run-a:android-01:v1:c1'")

    assert await state._policy_gate(obj, srow, run) is None            # segunda passagem: libera
    final = json.loads(db.one("SELECT bindings FROM steps WHERE id='run-a:android-01:v1:c1'")["bindings"])
    assert final["content"] == "Muito bom mesmo, parabéns pelo trabalho."
    assert final["content"] != rascunho                                # não voltou a ser o texto gerado
    assert state.approval_service.list() == []                         # e não abriu uma segunda aprovação


def test_plano_revisado_expira_a_aprovacao_da_etapa_que_morreu(tmp_path: Path) -> None:
    """Revisar o plano mata a etapa antiga e cria outra com id novo. Enquanto a aprovação da antiga continuava
    pendente, a aba mostrava DOIS cartões iguais — mesmo aparelho, mesma ação, mesmo alvo — e editar o antigo
    gravava o texto numa etapa que nunca roda: a pessoa aprovava uma frase e o aparelho digitava outra."""
    from app.models import PlanStep, Postcondition

    svc, store, repo, db, _ = _aprovacoes(tmp_path)
    _etapa_com_conteudo(db)
    step_id = "run-1:android-02:v1:send_1"
    pedido = store.open(profile_id=None, capability="SEND_MESSAGE", summary="Enviar", content="bom dia",
                        run_id="run-1", objective_id="run-1:android-02", step_id=step_id)

    repo.revise_plan("run-1:android-02", "tentar de novo por outro caminho", [
        PlanStep(key="send_1", title="Enviar a mensagem", goal="enviar", side_effect=True,
                 postcondition=Postcondition(kind="model_judged", value="x", description="y"))])

    assert db.one("SELECT status FROM steps WHERE id=?", (step_id,))["status"] == "skipped"
    depois = store.get(pedido.id)
    assert depois.status == "expired" and "plano revisado" in (depois.decided_note or "")
    assert store.list(status="pending") == []                    # um cartão só na tela: o da etapa que vai rodar


async def test_aparelho_sem_perfil_nao_publica_texto_inventado(harness: Any) -> None:
    """Regressão que esta própria série abriu: com o texto deixando de ser congelado no plano, uma etapa sem
    perfil chegaria ao ator sem `content` E sem a guarda que dependia dele. O modelo inventaria a frase e
    publicaria — sem rascunho, sem aprovação e sem nada que segurasse. Agora a porta barra."""
    state = harness.state
    db = state.db
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("UPDATE instances SET app_id='ig' WHERE id='android-01'")
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-s','ks','comentar','execute','running',1,'[\"android-01\"]','2026-09-17T10:00:00Z')")
    # objetivo SEM perfil: aparelho logado à mão, ou que nunca teve perfil vinculado
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters)"
               " VALUES ('run-s:android-01','run-s','android-01','running',1,'{}')")
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
        " bindings) VALUES ('run-s:android-01:v1:c1','run-s','run-s:android-01','android-01',1,1,'c1','Comentar',"
        "'comentar','[]',1,'[]','{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready',"
        "'CREATE_COMMENT','id=post','{\"content_brief\": \"elogiar o post\"}')")

    obj = db.one("SELECT * FROM objectives WHERE id='run-s:android-01'")
    srow = db.one("SELECT * FROM steps WHERE id='run-s:android-01:v1:c1'")
    run = db.one("SELECT * FROM runs WHERE id='run-s'")
    veredito = await state._policy_gate(obj, srow, run)

    assert veredito is not None and not veredito.allowed
    assert "perfil" in veredito.reason
    assert json.loads(db.one("SELECT bindings FROM steps WHERE id='run-s:android-01:v1:c1'")["bindings"]) == {
        "content_brief": "elogiar o post"}                        # nada foi escrito

    # texto exato pedido no comando continua passando: aí a frase é do operador, e a guarda a trava
    db.execute("UPDATE steps SET bindings=? WHERE id='run-s:android-01:v1:c1'",
               ('{"content": "parabéns!", "content_verbatim": "true"}',))
    srow = db.one("SELECT * FROM steps WHERE id='run-s:android-01:v1:c1'")
    assert await state._policy_gate(obj, srow, run) is None


async def test_orcamento_de_ia_esgotado_diz_o_que_fazer_e_nao_gasta_nada(harness: Any) -> None:
    """Orçamento estourado não é provedor com problema. Mandar "confira a chave e a persona" faria a pessoa
    procurar defeito onde não há, tentar de novo e bater na mesma parede — e a etapa fica esperando gente."""
    state = harness.state
    db = state.db
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("UPDATE instances SET app_id='ig' WHERE id='android-01'")
    pid = state.social.create_profile(ProfileCreate(username="lucas.almeida9484", password=SENHA,
                                                    instance_id="android-01")).id
    state.social.set_policy(pid, ProfilePolicyPatch(capabilities={"CREATE_COMMENT": "autonomous"}))
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-o','ko','comentar','execute','running',1,'[\"android-01\"]','2026-09-17T10:00:00Z')")
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
               " VALUES ('run-o:android-01','run-o','android-01','running',1,'{}',?)", (pid,))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
        " bindings) VALUES ('run-o:android-01:v1:c1','run-o','run-o:android-01','android-01',1,1,'c1','Comentar',"
        "'comentar','[]',1,'[]','{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready',"
        "'CREATE_COMMENT','id=post','{\"content_brief\": \"elogiar o post\"}')")
    # Orçamento de uma chamada, já gasta pelo planejamento: é assim que ele acaba de verdade, no meio da execução.
    state.settings.update({"ai_max_calls_per_objective": 1})
    db.execute("UPDATE objectives SET ai_calls=1 WHERE id='run-o:android-01'")

    veredito = await state._policy_gate(db.one("SELECT * FROM objectives WHERE id='run-o:android-01'"),
                                        db.one("SELECT * FROM steps WHERE id='run-o:android-01:v1:c1'"),
                                        db.one("SELECT * FROM runs WHERE id='run-o'"))

    assert veredito is not None and not veredito.allowed
    assert "orçamento" in veredito.reason.lower() or "chamadas de ia" in veredito.reason.lower()
    assert "Configuração" in (veredito.hint or "")               # a saída é aumentar o orçamento, não trocar a chave
    assert "persona" not in (veredito.hint or "").lower()
    # e nada foi gasto: o teto é conferido ANTES de chamar o modelo
    assert db.one("SELECT bindings FROM steps WHERE id='run-o:android-01:v1:c1'")["bindings"].find("content\"") == -1
    assert state.social_repo.count_interactions(pid) == 0


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


async def test_confirmar_concluido_tambem_fecha_a_interacao_no_historico(harness: Any) -> None:
    """Visto no aparelho: a DM saiu, mas a tela não trazia a prova que a pós-condição pedia, e a etapa ficou
    incerta. Quem confirma à mão conserta a fila; o histórico do perfil ficava dizendo que não se sabe — e é ele
    que alimenta relacionamento, conversa e memória. Confirmar a etapa precisa fechar a interação junto."""
    state = harness.state
    db = state.db
    pid = state.social.create_profile(ProfileCreate(username="mariana.costa91182", password=SENHA,
                                                    instance_id="android-01")).id
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-c','kc','enviar','execute','running',1,'[\"android-01\"]','2026-09-17T10:00:00Z')")
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id,"
               " blocked_kind) VALUES ('run-c:android-01','run-c','android-01','waiting_user',1,'{}',?,NULL)", (pid,))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
        " bindings) VALUES ('run-c:android-01:v1:send_1','run-c','run-c:android-01','android-01',1,1,'send_1',"
        "'Enviar a mensagem para @ana','enviar','[]',1,'[\"@ana\"]',"
        "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'uncertain','SEND_MESSAGE',"
        "'desc=Send','{\"username\": \"@ana\", \"content\": \"boa noite\"}')")
    iid = state.social_repo.record_interaction(
        pid, type="dm_sent", direction="outgoing", status="uncertain", instance_id="android-01",
        run_id="run-c", objective_id="run-c:android-01", step_id="run-c:android-01:v1:send_1",
        counterparty="@ana", thread_key="@ana", outgoing_content="boa noite")

    state.runs.resolve("run-c", "run-c:android-01", ResolveBody(resolution="confirm_done", note="vi sair no aparelho"))

    linha = state.social_repo.interaction_row(pid, iid)
    assert linha["status"] == "confirmed"                       # o histórico deixa de dizer "não se sabe"
    assert "usuário" in (linha["evidence"] or "").lower()       # e registra que quem provou foi uma pessoa
    # o que a confirmação destrava: relacionamento e conversa passam a contar esta mensagem
    assert db.one("SELECT 1 FROM relationship_summaries WHERE profile_id=? AND counterparty=?", (pid, "@ana"))
    assert db.one("SELECT 1 FROM thread_summaries WHERE profile_id=? AND thread_key=?", (pid, "@ana"))


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
            catalogo = (await c.get("/api/capabilities",
                                    params={"package": "com.instagram.android"})).json()
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
