"""30.62 (28.10 F5): a porta de política trata as personas de UM pedido como uma conta só.

(a) uma conta por alvo no pedido inteiro, em todos os baldes (curtir também); com porta-voz, só ele toca o alvo;
(b) efeito sobre pessoa real sem conversa prévia pede aprovação quando há pedido; (c) sem pedido, nada muda.
Banco migrado (SQLite, ou PostgreSQL com `TEST_DATABASE_URL`). Nível de prova: `simulated`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import LimitsCfg
from app.models import InteractionStatus, ProfileCreate
from app.planning.capabilities import load_catalog
from app.social.policy import BUCKET_TYPES, ContextoDoPedido, PolicyEngine

from .test_capabilities import IG, SENHA, build, perfil

ALVO = "@fulana.real"
SEM_ESPERA = '{"limits": {"cooldown_between_external_actions_s": 0}}'
AUTONOMO = ('{"capabilities": {"LIKE_POST": "autonomous", "FOLLOW": "autonomous"},'
            ' "limits": {"cooldown_between_external_actions_s": 0}}')


def _sem_espacamento() -> LimitsCfg:
    """O espaçamento entre contas da frota (curtidas) é outra regra; aqui ele só atrapalharia a leitura."""
    return LimitsCfg(fleet_min_spacing_between_accounts_s=0, fleet_spacing_jitter_s=0)


def _mundo(tmp_path: Path, politica: str = SEM_ESPERA) -> tuple[Any, Any, PolicyEngine, str, str, str]:
    svc, repo, _p, _db = build(tmp_path)
    a = perfil(svc, "ana.silva40517", "android-01")
    b = perfil(svc, "bia.souza40517", "android-02")
    c = svc.create_profile(ProfileCreate(username="caio.lima40517", password=SENHA)).id      # sem aparelho
    for p in (a, b, c):
        repo.update_profile(p, {"automation_policy": politica})
    return svc, repo, PolicyEngine(repo, settings_getter=_sem_espacamento), a, b, c


def _curtiu(svc: Any, pid: str, alvo: str = ALVO) -> None:
    svc.record_interaction(pid, type=BUCKET_TYPES["likes"][0], direction="outbound",
                           status=InteractionStatus.confirmed.value, counterparty=alvo, run_id="run-0")


def _curtir() -> Any:
    cap = load_catalog(IG).get("LIKE_POST")
    assert cap.limit_bucket == "likes"
    return cap


# ------------------------------------------------------------------------------------------- (c) sem pedido
def test_sem_pedido_curtir_aceita_mais_de_uma_conta_como_antes(tmp_path: Path) -> None:
    svc, _repo, policies, a, b, _c = _mundo(tmp_path)
    _curtiu(svc, a)
    assert policies.check(b, _curtir(), counterparty=ALVO).allowed


# ------------------------------------------------------------------------------------- (a) uma conta no pedido
def test_no_pedido_a_segunda_persona_da_familia_e_recusada_mesmo_em_curtir(tmp_path: Path) -> None:
    svc, _repo, policies, a, b, _c = _mundo(tmp_path)
    _curtiu(svc, a)
    pedido = ContextoDoPedido(raiz="r-1", familia=frozenset({a, b}))
    veredito = policies.check(b, _curtir(), counterparty=ALVO, pedido=pedido)
    assert not veredito.allowed and veredito.retry_at is None and "30.62" in veredito.reason


def test_persona_fora_da_familia_nao_conta_para_o_pedido(tmp_path: Path) -> None:
    svc, _repo, policies, a, b, c = _mundo(tmp_path)
    _curtiu(svc, c)                                            # C não é deste pedido
    pedido = ContextoDoPedido(raiz="r-1", familia=frozenset({a, b}))
    assert policies.check(b, _curtir(), counterparty=ALVO, pedido=pedido).allowed


def test_com_porta_voz_so_ele_toca_o_alvo(tmp_path: Path) -> None:
    _svc, _repo, policies, a, b, _c = _mundo(tmp_path)
    pedido = ContextoDoPedido(raiz="r-1", familia=frozenset({a, b}), porta_vozes=frozenset({a}))
    recusado = policies.check(b, _curtir(), counterparty=ALVO, pedido=pedido)
    assert not recusado.allowed and "porta-voz" in recusado.reason and recusado.retry_at is None
    assert policies.check(a, _curtir(), counterparty=ALVO, pedido=pedido).allowed


def test_o_pedido_em_aberto_de_outra_persona_da_familia_reserva_o_alvo(tmp_path: Path) -> None:
    from app.social.approvals import ApprovalStore

    _svc, repo, policies, a, b, _c = _mundo(tmp_path)
    ApprovalStore(repo.db).open(profile_id=a, capability="FOLLOW", summary="Seguir", target=ALVO)
    pedido = ContextoDoPedido(raiz="r-1", familia=frozenset({a, b}))
    assert not policies.check(b, _curtir(), counterparty=ALVO, pedido=pedido).allowed


# -------------------------------------------------------------------- (b) pessoa real sem conversa prévia
def test_no_pedido_efeito_em_pessoa_real_sem_conversa_pede_aprovacao(tmp_path: Path) -> None:
    _svc, _repo, policies, a, b, _c = _mundo(tmp_path, AUTONOMO)
    sem_pedido = policies.check(a, _curtir(), counterparty=ALVO)
    assert sem_pedido.allowed and not sem_pedido.needs_approval                 # (c): autônomo, como antes
    pedido = ContextoDoPedido(raiz="r-1", familia=frozenset({a, b}))
    com_pedido = policies.check(a, _curtir(), counterparty=ALVO, pedido=pedido)
    assert com_pedido.allowed and com_pedido.needs_approval and "30.62" in com_pedido.reason


def test_alvo_que_e_conta_nossa_nao_ganha_o_piso_da_pessoa_real(tmp_path: Path) -> None:
    _svc, _repo, policies, a, b, _c = _mundo(tmp_path, AUTONOMO)
    pedido = ContextoDoPedido(raiz="r-1", familia=frozenset({a}))
    veredito = policies.check(a, _curtir(), counterparty="@bia.souza40517", pedido=pedido)
    assert "30.62" not in veredito.reason


def test_com_dois_porta_vozes_o_terceiro_nao_toca_e_entre_eles_vale_um_por_alvo(tmp_path: Path) -> None:
    """Revisão da fila: com mais de um porta-voz o provedor devolvia `None`, e a porta caía no caso menos restrito."""
    svc, _repo, policies, a, b, c = _mundo(tmp_path)
    pedido = ContextoDoPedido(raiz="r-1", familia=frozenset({a, b, c}), porta_vozes=frozenset({a, b}))
    recusado = policies.check(c, _curtir(), counterparty=ALVO, pedido=pedido)
    assert not recusado.allowed and "porta-voz" in recusado.reason
    assert policies.check(b, _curtir(), counterparty=ALVO, pedido=pedido).allowed
    _curtiu(svc, a)                                            # um porta-voz tocou: o outro não toca o mesmo alvo
    assert not policies.check(b, _curtir(), counterparty=ALVO, pedido=pedido).allowed


# ------------------------------------------------------------------ ligação: a porta do despacho lê o pedido da execução
def _pedido_da_familia(state: Any, *pids: str) -> str:
    """Um pedido raiz com a 1ª persona e um filho por persona seguinte (28.10), e a execução `run-f` nascida da raiz
    (`runs.pedido_id`, 28.4). Com uma persona só, é um pedido solo: sem filhos."""
    for i, pid in enumerate(pids):
        state.db.execute("INSERT INTO pedidos(id, titulo, objetivo, alvos, pai_id, criado_em, atualizado_em)"
                         " VALUES (?,'Curtir','curtir',?,?,'2026-10-04T10:00:00Z','2026-10-04T10:00:00Z')",
                         (f"ped-familia-{i}", f'{{"alvos":[{{"profile_id":"{pid}"}}]}}',
                          None if i == 0 else "ped-familia-0"))
    state.db.execute("UPDATE runs SET pedido_id='ped-familia-0' WHERE id='run-f'")
    return "ped-familia-0"


async def test_a_porta_do_despacho_le_a_familia_do_pedido_da_execucao(harness: Any) -> None:
    """30.62 ligado ao `_policy_gate`: curtir aceita mais de uma conta por alvo na frota, mas no pedido entre personas a
    família conta como UMA. Sem `runs.pedido_id`, a mesma etapa não ouve falar do 30.62."""
    from .test_protecao_de_frota import ALVO, _execucao_em_duas_contas, _porta

    state = harness.state
    pids = _execucao_em_duas_contas(state, "LIKE_POST", {"post_author": ALVO})
    state.social_repo.record_interaction(pids["android-02"], type="post_liked", direction="outbound",
                                         status=InteractionStatus.confirmed.value, counterparty=ALVO, app_id="ig")
    sem_pedido = await _porta(state, "android-01")
    assert sem_pedido is None or "30.62" not in (sem_pedido.reason or "")
    _pedido_da_familia(state, *pids.values())
    no_pedido = await _porta(state, "android-01")
    assert no_pedido is not None and not no_pedido.allowed and no_pedido.retry_at is None
    assert "30.62" in no_pedido.reason


async def test_pedido_solo_nao_e_entre_personas_e_curtir_segue_autonomo(harness: Any) -> None:
    """Revisão da fila da suíte 32, item 2: o 30.62 é para pedido ENTRE personas. Um pedido solo ("curtir os posts de
    @x") com uma persona só não ganha contexto, e a curtida autônoma em pessoa real sem conversa segue autônoma, em vez
    de virar uma aprovação por curtida."""
    from app.modules.pedidos.infrastructure.contexto import contexto_do_pedido

    from .test_protecao_de_frota import ALVO, _execucao_em_duas_contas, _porta

    state = harness.state
    pids = _execucao_em_duas_contas(state, "LIKE_POST", {"post_author": ALVO})
    # só a conta do pedido no despacho: duas na mesma execução pediriam a confirmação de várias contas
    state.db.execute("DELETE FROM steps WHERE objective_id='run-f:android-02'")
    state.db.execute("DELETE FROM objectives WHERE id='run-f:android-02'")
    _pedido_da_familia(state, pids["android-01"])
    assert contexto_do_pedido(state.db, "run-f") is None
    assert await _porta(state, "android-01") is None                 # segue sem parar: nem recusa, nem aprovação
    assert state.approval_service.list() == []
