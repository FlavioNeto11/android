"""Proteção de contas pela frota (ADR-055, 29/09/2026): uma conta por alvo, DM fria com aprovação e a persona que não
fala por terceiros.

O caso real que isto fecha (lido por GET no central, `r-20260919220216-7cfa59`): em 19/09, SETE contas da frota
mandaram DM para a mesma pessoa em oito minutos, todas numa execução só, com variações de "seu marido mandou um oi".
Cinco das oito contas estão bloqueadas hoje. A investigação achou os buracos que deixavam a regra do dono sem efeito:

1. curtidas e comentários não amarravam de quem era a publicação: `counterparty` NULL em todo `post_liked` e
   `comment_replied`, e com isso a coordenação de frota nem era consultada;
2. a coordenação contava só a janela curta (1 h) e só o balde da própria ação, e ADIAVA o excedente em vez de recusar;
3. um grupo (ou o próprio perfil) podia deixar `SEND_MESSAGE` autônomo — inclusive para quem nunca falou com a conta;
4. nada impedia o texto de atribuir fala a um terceiro real ("seu marido mandou um oi").

Nível de prova: `simulated` (banco de teste, provedor falso ou simulado, sem aparelho).
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.models import (InteractionStatus, InteractionType, PolicyGroupCreate, ProfileCreate, ProfilePolicyPatch,
                        SocialDraftDTO)
from app.planning.capabilities import (BALDES_SEM_ALVO, CapabilityNode, CatalogoInvalido, capability_of, catalogo_de_dados, compose,
                                       contraparte, load_catalog)
from app.planning.provider import Usage
from app.social.approvals import DICA_DA_RECUSA
from app.social.conteudo import fala_atribuida_a_terceiro
from app.social.policy import PolicyEngine
from app.social.repository import SocialRepository
from app.social.service import SocialService
from app.util import now, to_iso

from .test_capabilities import IG, SENHA, build, perfil

ALVO = "@anarabottinipsicopedagoga"


class _Frota:
    """Os limites de frota como `LimitsCfg` os expõe (só o que a porta lê)."""

    def __init__(self, **over: Any):
        self.fleet_max_accounts_per_target = over.get("curtidas", 3)
        self.fleet_target_window_days = over.get("dias", 30)
        self.fleet_target_window_s = 3600
        self.fleet_min_spacing_between_accounts_s = over.get("espaco_s", 0)
        self.fleet_spacing_jitter_s = over.get("jitter_s", 0)


def _frota(tmp_path: Path, **over: Any) -> tuple[SocialService, SocialRepository, PolicyEngine, dict[str, str]]:
    svc, repo, _pol, _db = build(tmp_path)
    contas = {"lucas": perfil(svc, "tadeu.quintela4821", "android-01"),
              "mariana": perfil(svc, "luciana.bastos73519", "android-02")}
    for pid in contas.values():
        # sem aquecimento e sem intervalo entre ações: o que se mede aqui é só a regra de frota
        repo.update_profile(pid, {"automation_policy": '{"limits": {"warmup_days": 0, '
                                                       '"cooldown_between_external_actions_s": 0}}'})
    return svc, repo, PolicyEngine(repo, lambda: _Frota(**over)), contas


def _fez(svc: SocialService, pid: str, tipo: InteractionType, alvo: str = ALVO, *, dias_atras: int = 0,
         status: str = InteractionStatus.confirmed.value) -> None:
    iid = svc.record_interaction(pid, type=tipo.value, direction="outbound", status=status, counterparty=alvo,
                                 run_id="run-antiga").id
    if dias_atras:
        svc.repo.update_interaction(pid, iid, occurred_at=to_iso(now() - timedelta(days=dias_atras)))


# ======================================================================= 1. uma conta por alvo (follow/DM/comentário)
@pytest.mark.parametrize("acao", ["FOLLOW", "SEND_MESSAGE", "CREATE_COMMENT", "REPLY_COMMENT"])
def test_segunda_conta_no_mesmo_alvo_e_recusada_com_o_motivo(tmp_path: Path, acao: str) -> None:
    svc, _repo, policies, contas = _frota(tmp_path)
    _fez(svc, contas["lucas"], InteractionType.dm_sent)
    veredito = policies.check(contas["mariana"], capability_of(IG, acao), run_id="run-2", counterparty=ALVO)
    # RECUSA, não espera: o excedente não é adiado para daqui a uma hora — ele não acontece
    assert not veredito.allowed and veredito.retry_at is None and not veredito.is_wait
    assert "uma conta por alvo" in veredito.reason and ALVO in veredito.reason
    assert veredito.hint


def test_todos_os_baldes_contam_uma_curtida_de_outra_conta_ja_ocupa_o_alvo(tmp_path: Path) -> None:
    """Antes, FOLLOW só olhava `followed`/`unfollowed`: a conta que curtiu a publicação não contava para quem seguia."""
    svc, _repo, policies, contas = _frota(tmp_path)
    _fez(svc, contas["lucas"], InteractionType.post_liked)
    assert not policies.check(contas["mariana"], capability_of(IG, "FOLLOW"), counterparty=ALVO).allowed
    # a própria conta que já fala com a pessoa segue sendo a conta daquele alvo
    assert policies.check(contas["lucas"], capability_of(IG, "FOLLOW"), counterparty=ALVO).allowed


def test_janela_e_em_dias_e_nao_de_uma_hora(tmp_path: Path) -> None:
    svc, _repo, policies, contas = _frota(tmp_path, dias=30)
    _fez(svc, contas["lucas"], InteractionType.followed, dias_atras=10)
    # dez dias depois, o alvo continua sendo da conta do lucas (a janela antiga era de 3600 s)
    assert not policies.check(contas["mariana"], capability_of(IG, "FOLLOW"), counterparty=ALVO).allowed
    _svc2, _repo2, curta, contas2 = _frota(tmp_path / "curta", dias=5)
    _fez(_svc2, contas2["lucas"], InteractionType.followed, dias_atras=10)
    assert curta.check(contas2["mariana"], capability_of(IG, "FOLLOW"), counterparty=ALVO).allowed


def test_o_alvo_e_comparado_normalizado(tmp_path: Path) -> None:
    """O histórico grava `@minusculo`; a porta recebia o argumento cru do plano e `@Ana` nunca casava com `@ana`."""
    svc, _repo, policies, contas = _frota(tmp_path)
    _fez(svc, contas["lucas"], InteractionType.followed, alvo="@Ana.Silva")
    assert not policies.check(contas["mariana"], capability_of(IG, "FOLLOW"), counterparty="Ana.Silva").allowed


def test_pedido_de_aprovacao_em_aberto_de_outra_conta_ja_reserva_o_alvo(tmp_path: Path) -> None:
    """Duas execuções quase juntas: a primeira ainda espera aprovação (nada foi disparado) e a segunda já pediria o
    mesmo alvo com outra conta. O pedido em aberto conta como a conta daquele alvo."""
    svc, repo, policies, contas = _frota(tmp_path)
    repo.db.execute("INSERT INTO pending_approvals(id, profile_id, capability, target, summary, status, created_at)"
                    " VALUES ('ap-1', ?, 'SEND_MESSAGE', ?, 'Enviar', 'pending', ?)",
                    (contas["lucas"], "@AnaRabottiniPsicopedagoga", to_iso(now())))
    veredito = policies.check(contas["mariana"], capability_of(IG, "SEND_MESSAGE"), counterparty=ALVO)
    assert not veredito.allowed and veredito.retry_at is None
    # rejeitado ou expirado não reserva nada
    repo.db.execute("UPDATE pending_approvals SET status='rejected' WHERE id='ap-1'")
    assert policies.check(contas["mariana"], capability_of(IG, "SEND_MESSAGE"), counterparty=ALVO).allowed


def test_acao_de_frota_sem_alvo_conhecido_e_recusada(tmp_path: Path) -> None:
    """Sem saber de quem é a publicação, a regra de uma conta por alvo não tem como ser conferida: não se age."""
    _svc, _repo, policies, contas = _frota(tmp_path)
    for acao in ("LIKE_POST", "CREATE_COMMENT", "FOLLOW"):
        veredito = policies.check(contas["mariana"], capability_of(IG, acao), counterparty=None)
        assert not veredito.allowed and veredito.retry_at is None, acao
        assert "alvo" in veredito.reason and veredito.hint, acao


# ============================================================================ 2. curtida: teto configurável
def test_curtida_conta_para_o_teto_de_contas_por_alvo(tmp_path: Path) -> None:
    svc, _repo, policies, contas = _frota(tmp_path, curtidas=2)
    contas["bruno"] = svc.create_profile(ProfileCreate(username="valdir.teixeira6352", password=SENHA)).id
    _fez(svc, contas["lucas"], InteractionType.post_liked)
    like = capability_of(IG, "LIKE_POST")
    assert policies.check(contas["mariana"], like, counterparty=ALVO).allowed          # 1 outra < teto 2
    _fez(svc, contas["mariana"], InteractionType.post_liked)
    veredito = policies.check(contas["bruno"], like, counterparty=ALVO)                # 2 outras = teto
    assert not veredito.allowed and veredito.retry_at is None
    assert "2 outra(s) conta(s)" in veredito.reason


# ============================================================================ 3. DM fria: sempre com aprovação
def test_dm_fria_sai_approval_required_mesmo_com_grupo_autonomo(tmp_path: Path) -> None:
    svc, _repo, policies, contas = _frota(tmp_path)
    pid = contas["mariana"]
    svc.create_policy_group(PolicyGroupCreate(name="Operação", capabilities={"SEND_MESSAGE": "autonomous"},
                                              profile_ids=[pid]))
    dm = capability_of(IG, "SEND_MESSAGE")
    assert policies.policy_for(pid, dm) == "autonomous"                              # o grupo afrouxou...
    veredito = policies.check(pid, dm, run_id="run-1", counterparty="@nunca.falou")
    assert veredito.allowed and veredito.needs_approval and veredito.policy == "approval_required"  # ...e não vale
    assert "conversa" in veredito.reason
    # nem a escolha própria do perfil afrouxa
    svc.set_policy(pid, ProfilePolicyPatch(capabilities={"SEND_MESSAGE": "autonomous"}), package=IG)
    assert policies.check(pid, dm, counterparty="@nunca.falou").needs_approval
    # alvo desconhecido é alvo sem conversa (e a regra vale mesmo sem a coordenação de frota ligada)
    assert PolicyEngine(svc.repo).check(pid, dm).needs_approval


def test_com_conversa_previa_a_politica_autonoma_vale(tmp_path: Path) -> None:
    svc, _repo, policies, contas = _frota(tmp_path)
    pid = contas["mariana"]
    svc.set_policy(pid, ProfilePolicyPatch(capabilities={"SEND_MESSAGE": "autonomous"}), package=IG)
    svc.record_inbound(pid, texts=["oi! tudo bem?"], counterparty="@amiga", type=InteractionType.dm_received.value)
    veredito = policies.check(pid, capability_of(IG, "SEND_MESSAGE"), counterparty="@Amiga")
    assert veredito.allowed and not veredito.needs_approval
    # uma DM que ESTA conta mandou e ficou sem resposta não é conversa
    _fez(svc, pid, InteractionType.dm_sent, alvo="@sem.resposta")
    assert policies.check(pid, capability_of(IG, "SEND_MESSAGE"), counterparty="@sem.resposta").needs_approval


# ============================================================================ 4. o alvo das curtidas e comentários
def test_catalogo_declara_quem_e_o_alvo_de_toda_acao_com_limite() -> None:
    catalogo = load_catalog(IG)
    assert catalogo is not None
    # 29.30: publicar no próprio feed (balde `posts`) não tem outra pessoa do outro lado: só ele dispensa o alvo.
    alvos = {c.key: c.counterparty for c in catalogo.capabilities
             if c.limit_bucket and c.limit_bucket not in BALDES_SEM_ALVO}
    assert all(alvos.values()), alvos
    # quem publicou, nas ações sobre a publicação; o dono do comentário nas ações sobre o comentário
    assert alvos["LIKE_POST"] == alvos["UNLIKE_POST"] == alvos["CREATE_COMMENT"] == "post_author"
    assert alvos["LIKE_COMMENT"] == alvos["REPLY_COMMENT"] == alvos["FOLLOW"] == alvos["SEND_MESSAGE"] == "username"


def test_autor_do_post_entra_no_plano_e_e_herdado_por_curtir_e_comentar() -> None:
    catalogo = load_catalog(IG)
    assert catalogo is not None
    steps, missing = compose(catalogo, [
        CapabilityNode(key="perfil", capability="OPEN_PROFILE", bindings={"username": "@autora"}),
        CapabilityNode(key="post", capability="OPEN_POST", bindings={"target": "a primeira", "post_author": "@autora"}),
        CapabilityNode(key="curtir", capability="LIKE_POST"),
        CapabilityNode(key="coment", capability="OPEN_COMMENTS"),
        # responder um comentário NÃO muda de quem é a publicação (username ali é o dono do comentário)
        CapabilityNode(key="resp", capability="REPLY_COMMENT", bindings={"username": "@quem.comentou",
                                                                        "content_brief": "agradecer"}),
        CapabilityNode(key="c1", capability="CREATE_COMMENT", bindings={"content_brief": "elogiar"})])
    assert not missing
    por = {s.key: s for s in steps}
    for chave in ("curtir", "c1"):
        assert contraparte(capability_of(IG, por[chave].capability), por[chave].bindings) == "@autora", chave
    assert contraparte(capability_of(IG, "REPLY_COMMENT"), por["resp"].bindings) == "@quem.comentou"


def test_efeito_de_curtida_e_comentario_grava_o_autor_como_contraparte(tmp_path: Path) -> None:
    """O buraco medido no central: todos os `post_liked` e `comment_replied` com `counterparty` NULL."""
    svc, _repo, _pol, _db = build(tmp_path)
    pid = perfil(svc)
    for acao, tipo in (("LIKE_POST", InteractionType.post_liked), ("CREATE_COMMENT", InteractionType.comment_replied)):
        bindings = {"post_author": "@Autora", "caption_contains": "Setembro", "content": "que lindo"}
        iid = svc.open_effect(pid, capability=acao, interaction_type=tipo.value, bindings=bindings, run_id="run-1",
                              counterparty=contraparte(capability_of(IG, acao), bindings))
        assert svc.get_interaction(pid, iid).counterparty == "@autora", acao


def test_executor_passa_a_contraparte_declarada_ao_historico(tmp_path: Path) -> None:
    """O executor é quem abre o efeito no instante do commit: ele precisa usar o alvo DECLARADO pela ação."""
    from types import SimpleNamespace

    from app.taskqueue.executor import StepExecutor

    svc, _repo, _pol, _db = build(tmp_path)
    pid = perfil(svc)
    ex = StepExecutor.__new__(StepExecutor)
    ex.social, ex.approvals, ex._effects = svc, None, {}               # noqa: SLF001 - só o pedaço do efeito
    ex.repo = SimpleNamespace(db=svc.repo.db)
    step = SimpleNamespace(id="s-like", run_id="run-1", objective_id="o-1",
                           bindings={"post_author": "@autora", "caption_contains": "Setembro"})
    ex._open_effect({"profile_id": pid}, step, SimpleNamespace(id="android-02"),  # noqa: SLF001
                    capability_of(IG, "LIKE_POST"))
    linha = svc.repo.db.one("SELECT counterparty FROM social_interactions WHERE step_id='s-like'")
    assert linha is not None and linha["counterparty"] == "@autora"


def test_carga_recusa_acao_com_limite_sem_alvo_declarado() -> None:
    base = {"key": "SEGUIR", "title": "Seguir", "goal": "Seguir.", "post_kind": "model_judged", "post_value": "x",
            "post_description": "y", "side_effect": True, "commit_selector": "text=Follow", "limit_bucket": "follows",
            "bindings": ["username"], "objeto_alvo": ["username"]}
    with pytest.raises(CatalogoInvalido, match="counterparty"):
        catalogo_de_dados({"app": "com.exemplo.x", "contract_version": 1, "acoes": [base]})
    with pytest.raises(CatalogoInvalido, match="counterparty"):
        catalogo_de_dados({"app": "com.exemplo.x", "contract_version": 1,
                           "acoes": [{**base, "counterparty": "quem"}]})
    assert catalogo_de_dados({"app": "com.exemplo.x", "contract_version": 1,
                              "acoes": [{**base, "counterparty": "username"}]}).get("SEGUIR").counterparty == "username"


# ============================================================================ 5. a persona não fala por terceiros
_DO_INCIDENTE = [
    "Oi, seu marido mandou um oi aqui pra você",
    "Ana, recado rápido: seu marido pediu para eu te avisar que mandou um oi",
    "E aí, Ana, tudo bem por aí? Passando só pra avisar que seu marido pediu pra te mandar um beijo",
    "Opa Ana! De boa aí? Bora, um recadinho: seu marido pediu um oi pra você",
    "Ana, recadinho rápido: recebi um oi do seu marido pra te repassar. Boa noite!",
    "A Joana disse que você vai adorar o evento",
    "Your husband asked me to tell you hi",
    "Minha amiga mandou um oi pra você",            # o círculo da persona também não manda recado a ninguém
    "Ele nos disse que você vai adorar",
]
_LEGITIMOS = [
    "Passando só pra dar um oi e desejar uma boa noite 🤍",
    "Como você disse ontem, o evento foi incrível!",
    "Você mandou um oi e eu vim responder: tudo ótimo por aqui!",
    "Que post lindo, parabéns pelo trabalho com as crianças!",
    "Eu disse que ia aparecer, e apareci 😄",
    "Manda um beijo pra sua mãe!",                  # a persona pedindo, não relatando recado de ninguém
    "Você pediu pra eu te avisar quando saísse: saiu!",
    "Minha mãe sempre disse que o segredo é a paciência",   # lembrança da persona, não recado
    "Tenho uma dica pra te repassar: comece pelo básico",
]


@pytest.mark.parametrize("texto", _DO_INCIDENTE)
def test_detecta_fala_atribuida_a_terceiro(texto: str) -> None:
    assert fala_atribuida_a_terceiro(texto), texto


@pytest.mark.parametrize("texto", _LEGITIMOS)
def test_nao_confunde_fala_propria_ou_da_contraparte(texto: str) -> None:
    assert fala_atribuida_a_terceiro(texto) is None, texto


class _ProvedorQueAtribui:
    """Escreve a frase do incidente; só reescreve se o pedido disser que a anterior atribuía fala a terceiro."""

    def __init__(self, corrige: bool):
        self.corrige = corrige
        self.pedidos: list[Any] = []

    async def generate_social_response(self, req: Any) -> tuple[SocialDraftDTO, Usage]:
        self.pedidos.append(req)
        if req.attribution_retry and self.corrige:
            return SocialDraftDTO(content="Oi, Ana! Passando só pra dar um oi 🤍", rationale="ok"), Usage(role="social")
        return SocialDraftDTO(content="Oi, seu marido mandou um oi aqui pra você", rationale="ok"), Usage(role="social")


async def test_rascunho_que_atribui_fala_a_terceiro_e_reescrito_uma_vez(tmp_path: Path) -> None:
    svc, _repo, _pol, _db = build(tmp_path)
    prov = _ProvedorQueAtribui(corrige=True)
    svc.provider = prov
    pid = perfil(svc)
    draft, _i = await svc.draft_response(pid, kind="dm_initiate", brief="dizer que o marido dela mandou um oi",
                                         counterparty=ALVO, persist=False)
    assert len(prov.pedidos) == 2 and prov.pedidos[1].attribution_retry
    assert not draft.refused and draft.content == "Oi, Ana! Passando só pra dar um oi 🤍"


async def test_rascunho_que_insiste_em_atribuir_fala_e_recusado(tmp_path: Path) -> None:
    svc, _repo, _pol, _db = build(tmp_path)
    svc.provider = _ProvedorQueAtribui(corrige=False)
    pid = perfil(svc)
    draft, _i = await svc.draft_response(pid, kind="dm_initiate", brief="dizer que o marido dela mandou um oi",
                                         counterparty=ALVO, persist=False)
    assert draft.refused and not draft.content
    assert "terceiro" in (draft.refusal_reason or "")


def test_papel_do_redator_proibe_falar_por_terceiros() -> None:
    from app.planning.prompts import SOCIAL_SYSTEM

    assert "seu marido mandou" in SOCIAL_SYSTEM and "terceiro" in SOCIAL_SYSTEM


def test_planejador_e_o_catalogo_dizem_que_curtir_e_comentar_precisam_do_autor() -> None:
    """Sem o planejador preencher o autor, a porta recusaria toda curtida: a regra fica segura, mas inútil."""
    from app.planning.prompts import PLANNER_CAPABILITY_SYSTEM

    assert "post_author" in PLANNER_CAPABILITY_SYSTEM
    catalogo = load_catalog(IG)
    assert catalogo is not None
    assert "OPEN_POST(target, caption_contains?, post_author?)" in catalogo.prompt_block()


async def test_planejador_simulado_amarra_o_autor_na_curtida() -> None:
    from app.planning.provider import AppContext, PlanRequest
    from app.planning.simulated_provider import SimulatedProvider

    req = PlanRequest(command="curtir o último post de @autora.real", run_id="r-1",
                      instances=[{"instance_id": "android-02", "account_label": None, "app_id": "ig"}],
                      apps=[AppContext("ig", "Instagram", IG, None, None, None)], catalog=load_catalog(IG))
    plano, _uso = await SimulatedProvider().plan(req)
    curtir = next(s for s in plano.steps if s.capability == "LIKE_POST")
    assert contraparte(capability_of(IG, "LIKE_POST"), curtir.bindings) == "@autora.real"


# ================================================================= 6. o mesmo pedido a várias contas numa execução
def _execucao_em_duas_contas(state: Any, acao: str, bindings: dict[str, str]) -> dict[str, str]:
    db = state.db
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("UPDATE instances SET app_id='ig' WHERE id IN ('android-01','android-02')")
    pids = {"android-01": state.social.create_profile(ProfileCreate(username="tadeu.quintela4821", password=SENHA,
                                                                    instance_id="android-01")).id,
            "android-02": state.social.create_profile(ProfileCreate(username="luciana.bastos73519", password=SENHA,
                                                                    instance_id="android-02")).id}
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-f','kf','x','execute','running',1,'[\"android-01\",\"android-02\"]',"
               "'2026-09-29T10:00:00Z')")
    for iid, pid in pids.items():
        state.social_repo.update_profile(pid, {"automation_policy": '{"limits": {"warmup_days": 0, '
                                                                    '"cooldown_between_external_actions_s": 0}}'})
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
                   " VALUES (?,'run-f',?,'running',1,'{}',?)", (f"run-f:{iid}", iid, pid))
        db.execute(
            "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
            " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
            " bindings) VALUES (?,'run-f',?,?,1,1,'efeito','Efeito','efeito','[]',1,'[]',"
            "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready',?,'text=X',?)",
            (f"run-f:{iid}:v1:efeito", f"run-f:{iid}", iid, acao, json.dumps(bindings)))
    return pids


async def _porta(state: Any, iid: str) -> Any:
    db = state.db
    return await state._policy_gate(db.one("SELECT * FROM objectives WHERE id=?", (f"run-f:{iid}",)),  # noqa: SLF001
                                    db.one("SELECT * FROM steps WHERE id=?", (f"run-f:{iid}:v1:efeito",)),
                                    db.one("SELECT * FROM runs WHERE id='run-f'"))


async def test_mesmo_follow_para_duas_contas_so_uma_segue_e_com_confirmacao(harness: Any) -> None:
    state = harness.state
    pids = _execucao_em_duas_contas(state, "FOLLOW", {"username": ALVO})
    for pid in pids.values():               # nem com a política afrouxada a execução dispara sem confirmação
        state.social.set_policy(pid, ProfilePolicyPatch(capabilities={"FOLLOW": "autonomous"}), package=IG)
    escolhida = await _porta(state, "android-01")
    assert escolhida is not None and not escolhida.allowed and escolhida.policy == "approval_required"
    assert "2 contas" in escolhida.reason
    [pedido] = state.approval_service.list()
    assert pedido["target"] == ALVO and "2 contas" in pedido["summary"]
    recusada = await _porta(state, "android-02")
    assert recusada is not None and not recusada.allowed and recusada.retry_at is None
    assert "uma conta por alvo" in recusada.reason and "android-01" in recusada.reason
    assert len(state.approval_service.list()) == 1                       # a recusada nem chega a pedir aprovação


async def test_mesma_curtida_para_duas_contas_exige_confirmacao_em_cada_uma(harness: Any) -> None:
    state = harness.state
    _execucao_em_duas_contas(state, "LIKE_POST", {"post_author": ALVO})
    for iid in ("android-01", "android-02"):
        veredito = await _porta(state, iid)
        assert veredito is not None and not veredito.allowed and veredito.policy == "approval_required", iid
    assert len(state.approval_service.list()) == 2


async def test_dm_fria_na_porta_vira_pedido_de_aprovacao_com_o_motivo(harness: Any) -> None:
    """O caminho inteiro do despacho: grupo e perfil com SEND_MESSAGE autônomo, texto exato no comando — e ainda assim
    a mensagem para quem nunca escreveu à conta espera uma pessoa, e o cartão diz por quê."""
    state = harness.state
    pids = _execucao_em_duas_contas(state, "SEND_MESSAGE", {"username": ALVO, "content": "oi, tudo bem?",
                                                            "content_verbatim": "true"})
    state.db.execute("UPDATE steps SET bindings=? WHERE id='run-f:android-02:v1:efeito'",
                     (json.dumps({"username": "@outra.pessoa", "content": "oi", "content_verbatim": "true"}),))
    grupo = state.social.create_policy_group(PolicyGroupCreate(name="Operação",
                                                               capabilities={"SEND_MESSAGE": "autonomous"},
                                                               profile_ids=[pids["android-01"]]))
    assert grupo.capabilities == {"SEND_MESSAGE": "autonomous"}
    veredito = await _porta(state, "android-01")
    assert veredito is not None and not veredito.allowed and veredito.policy == "approval_required"
    [pedido] = state.approval_service.list()
    assert "conversa" in pedido["summary"] and pedido["target"] == ALVO


async def test_texto_que_fala_por_terceiro_nao_chega_ao_aparelho(harness: Any) -> None:
    state = harness.state
    state.social.provider = _ProvedorQueAtribui(corrige=False)
    _execucao_em_duas_contas(state, "CREATE_COMMENT", {"post_author": ALVO, "content_brief": "mandar um oi"})
    state.db.execute("UPDATE steps SET bindings=? WHERE id='run-f:android-02:v1:efeito'",
                     (json.dumps({"post_author": "@outra.pessoa", "content_brief": "oi"}),))
    veredito = await _porta(state, "android-01")
    assert veredito is not None and not veredito.allowed and veredito.retry_at is None
    # 31.65: o motivo (que cita o trecho do modelo) fica só no detalhe da etapa; a dica que viaja é a fixa.
    assert veredito.hint == DICA_DA_RECUSA
    etapa = state.repo.step_dto(state.db.one("SELECT * FROM steps WHERE id='run-f:android-01:v1:efeito'"))
    assert "terceiro" in (etapa.motivo_da_persona or "")
    # ...e não viaja em evento (gravado em `events` e transmitido a todo navegador).
    state.repo.emit_step("run-f:android-01:v1:efeito", "x")
    gravado = json.loads(state.db.scalar("SELECT data FROM events WHERE kind='step.updated' ORDER BY id DESC LIMIT 1"))
    assert "motivo_da_persona" not in gravado["step"] and "terceiro" not in json.dumps(gravado)
    bindings = json.loads(state.db.one("SELECT bindings FROM steps WHERE id='run-f:android-01:v1:efeito'")["bindings"])
    assert "content" not in bindings                                     # nada foi escrito na etapa
    assert state.approval_service.list() == []


async def test_a_recusa_guardada_nao_fecha_a_escrita_e_some_quando_a_persona_escreve(harness: Any) -> None:
    """31.65: o motivo da recusa mora em `draft_meta`, mas não é rascunho. A retomada escreve de novo (sem isto a porta
    pularia a escrita e a etapa seguiria sem texto), e o texto escrito substitui o motivo."""
    state = harness.state
    state.social.provider = _ProvedorQueAtribui(corrige=False)
    _execucao_em_duas_contas(state, "CREATE_COMMENT", {"post_author": ALVO, "content_brief": "mandar um oi"})
    state.db.execute("UPDATE steps SET bindings=? WHERE id='run-f:android-02:v1:efeito'",
                     (json.dumps({"post_author": "@outra.pessoa", "content_brief": "oi"}),))
    sid = "run-f:android-01:v1:efeito"
    assert (await _porta(state, "android-01")).hint == DICA_DA_RECUSA
    state.social.provider = _ProvedorQueAtribui(corrige=True)
    await _porta(state, "android-01")
    etapa = state.repo.step_dto(state.db.one("SELECT * FROM steps WHERE id=?", (sid,)))
    assert etapa.bindings.get("content") == "Oi, Ana! Passando só pra dar um oi 🤍"
    assert etapa.motivo_da_persona is None


async def test_uma_conta_so_segue_a_politica_de_sempre(harness: Any) -> None:
    state = harness.state
    _execucao_em_duas_contas(state, "LIKE_POST", {"post_author": ALVO})
    state.db.execute("UPDATE steps SET bindings=? WHERE id='run-f:android-02:v1:efeito'",
                     (json.dumps({"post_author": "@outra.pessoa"}),))
    assert await _porta(state, "android-01") is None                     # curtida autônoma, alvo só dela
    assert state.approval_service.list() == []
