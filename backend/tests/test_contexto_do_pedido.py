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
    a = perfil(svc, "ana.silva91182", "android-01")
    b = perfil(svc, "bia.souza91182", "android-02")
    c = svc.create_profile(ProfileCreate(username="caio.lima91182", password=SENHA)).id      # sem aparelho
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
    veredito = policies.check(a, _curtir(), counterparty="@bia.souza91182", pedido=pedido)
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
