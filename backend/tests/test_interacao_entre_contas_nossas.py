"""Contas nossas podem interagir entre si, em ritmo baixo (item 29.28, emenda do ADR-050; decisão do dono de 02/10/2026).

Antes, `PolicyEngine._fleet_gate` recusava todo efeito cujo alvo era conta nossa (viva ou retirada). Agora:
- alvo é conta RETIRADA (lápide, 29.23) → continua recusado, sem `retry_at`;
- alvo é conta nossa VIVA → passa pelas demais regras (política do perfil, aprovação, tetos, uma conta por alvo do
  ADR-055) e por um espaçamento mínimo desde o último gesto com efeito DESTA conta: o maior entre
  `limits.fleet_min_spacing_to_own_account_s` (600 s) e `cooldown_between_external_actions_s`, com `retry_at`.

Nível de prova: `simulated` (banco de teste, perfis de teste). Nada real.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from app.config import LimitsCfg
from app.models import InteractionStatus, InteractionType, ProfileCreate
from app.planning.capabilities import capability_of
from app.social.policy import PolicyEngine
from app.util import now, parse_iso, to_iso

from .test_capabilities import IG, SENHA, build, perfil
from .test_protecao_de_frota import _Frota

ANA, BIA, CLO = "ana.nossa01", "bia.nossa02", "clo.nossa03"


class _Ritmo(_Frota):
    """`_Frota` mais o espaçamento entre contas nossas, como `LimitsCfg` o expõe."""

    def __init__(self, **over: Any):
        super().__init__(**over)
        self.fleet_min_spacing_to_own_account_s = over.get("nossa_s", 600)
        self.fleet_one_account_rule_for_own_accounts = over.get("regra_nossa", True)


def _tres(tmp_path: Path, **over: Any) -> tuple[Any, Any, PolicyEngine, dict[str, str]]:
    svc, repo, _pol, db = build(tmp_path)
    contas = {"ana": perfil(svc, ANA, "android-01"), "bia": perfil(svc, BIA, "android-02"),
              "clo": svc.create_profile(ProfileCreate(username=CLO, password=SENHA)).id}
    for pid in contas.values():
        # sem aquecimento e sem cooldown próprio: o que se mede aqui é só a regra entre contas nossas
        repo.update_profile(pid, {"automation_policy": '{"limits": {"warmup_days": 0, '
                                                       '"cooldown_between_external_actions_s": 0}}'})
    return svc, db, PolicyEngine(repo, lambda: _Ritmo(**over)), contas


def _fez(svc: Any, pid: str, alvo: str, *, segundos_atras: int = 0,
         tipo: InteractionType = InteractionType.post_liked) -> None:
    iid = svc.record_interaction(pid, type=tipo.value, direction="outbound", status=InteractionStatus.confirmed.value,
                                 counterparty=alvo, run_id="run-antiga").id
    svc.repo.update_interaction(pid, iid, occurred_at=to_iso(now() - timedelta(seconds=segundos_atras)))


def test_alvo_nosso_vivo_libera_e_a_aprovacao_do_perfil_continua_valendo(tmp_path: Path) -> None:
    _svc, _db, policies, c = _tres(tmp_path)
    comentar = capability_of(IG, "CREATE_COMMENT")
    veredito = policies.check(c["ana"], comentar, run_id="run-1", counterparty=f"@{BIA}")
    # a regra de frota não barra mais: o que decide é a política da ação (comentário passa por aprovação por padrão)
    assert veredito.allowed and veredito.retry_at is None
    assert veredito.needs_approval == (policies.policy_for(c["ana"], comentar) == "approval_required")
    # política desligada no perfil segue barrando, mesmo para alvo nosso
    policies.repo.update_profile(c["ana"], {"automation_policy": '{"capabilities": {"CREATE_COMMENT": "disabled"}}'})
    assert not policies.check(c["ana"], comentar, counterparty=f"@{BIA}").allowed


def test_segundo_gesto_antes_de_600s_devolve_retry_at_e_depois_libera(tmp_path: Path) -> None:
    svc, _db, policies, c = _tres(tmp_path)
    curtir = capability_of(IG, "LIKE_POST")
    _fez(svc, c["ana"], f"@{BIA}", segundos_atras=100, tipo=InteractionType.post_liked)
    veredito = policies.check(c["ana"], curtir, counterparty=f"@{CLO}")
    assert not veredito.allowed and veredito.retry_at is not None
    espera = (parse_iso(veredito.retry_at) - now()).total_seconds()
    assert 480 < espera <= 500                                   # 600 s desde o gesto de 100 s atrás
    assert "ritmo baixo" in veredito.reason and veredito.hint
    # passados os 600 s, libera
    _svc2, _db2, depois, c2 = _tres(tmp_path / "depois")
    _fez(_svc2, c2["ana"], f"@{BIA}", segundos_atras=601, tipo=InteractionType.post_liked)
    assert depois.check(c2["ana"], curtir, counterparty=f"@{CLO}").allowed


def test_o_espaco_so_vale_quando_o_alvo_e_conta_nossa(tmp_path: Path) -> None:
    svc, _db, policies, c = _tres(tmp_path)
    curtir = capability_of(IG, "LIKE_POST")
    _fez(svc, c["ana"], "@alguem.de.fora", segundos_atras=5, tipo=InteractionType.post_liked)
    assert policies.check(c["ana"], curtir, counterparty="@outro.de.fora").allowed       # cooldown próprio é 0
    assert not policies.check(c["ana"], curtir, counterparty=f"@{BIA}").allowed          # alvo nosso: 600 s


def test_vale_o_maior_entre_o_espaco_entre_contas_nossas_e_o_cooldown_do_perfil(tmp_path: Path) -> None:
    svc, _db, policies, c = _tres(tmp_path)
    policies.repo.update_profile(c["ana"], {"automation_policy": '{"limits": {"warmup_days": 0, '
                                                                 '"cooldown_between_external_actions_s": 900}}'})
    curtir = capability_of(IG, "LIKE_POST")
    _fez(svc, c["ana"], f"@{BIA}", segundos_atras=700, tipo=InteractionType.post_liked)
    veredito = policies.check(c["ana"], curtir, counterparty=f"@{CLO}")
    assert not veredito.allowed and veredito.retry_at is not None            # 700 s < 900 s do cooldown do perfil
    assert 190 < (parse_iso(veredito.retry_at) - now()).total_seconds() <= 200
    # e com o espaço configurado maior que o cooldown, vale o espaço
    _s2, _d2, largo, c2 = _tres(tmp_path / "largo", nossa_s=1800)
    _fez(_s2, c2["ana"], f"@{BIA}", segundos_atras=1000, tipo=InteractionType.post_liked)
    assert not largo.check(c2["ana"], curtir, counterparty=f"@{CLO}").allowed


def test_alvo_retirado_continua_recusado_sem_retry_at(tmp_path: Path) -> None:
    svc, _db, policies, c = _tres(tmp_path)
    comentar = capability_of(IG, "CREATE_COMMENT")
    assert policies.check(c["ana"], comentar, counterparty=f"@{BIA}").allowed
    svc.retirar_conta_bloqueada(c["bia"], str(svc.repo.conta_ancora(c["bia"])["id"]))
    veredito = policies.check(c["ana"], comentar, counterparty=f"@{BIA}")
    assert not veredito.allowed and veredito.retry_at is None and "retirada" in veredito.reason
    assert not policies.check(c["ana"], capability_of(IG, "FOLLOW"), counterparty=f"@{BIA.upper()}").allowed


def test_uma_conta_por_alvo_continua_valendo_entre_contas_nossas(tmp_path: Path) -> None:
    svc, _db, policies, c = _tres(tmp_path)
    # a Ana já falou com a Clô (conta nossa viva); a Bia não pode mexer com a mesma pessoa (ADR-055: seguir/DM/comentário)
    _fez(svc, c["ana"], f"@{CLO}", segundos_atras=5000)
    for acao in ("FOLLOW", "CREATE_COMMENT", "SEND_MESSAGE"):
        veredito = policies.check(c["bia"], capability_of(IG, acao), counterparty=f"@{CLO}")
        assert not veredito.allowed and veredito.retry_at is None and "uma conta por alvo" in veredito.reason
    # a própria Ana segue sendo a conta daquele alvo (e o ritmo baixo já passou: 5000 s)
    assert policies.check(c["ana"], capability_of(IG, "FOLLOW"), counterparty=f"@{CLO}").allowed


def test_p030_sem_a_regra_para_conta_nossa_o_mesmo_alvo_nosso_passa_e_pessoa_real_segue_recusada(tmp_path: Path) -> None:
    """P-030 (proposta de emenda ao ADR-055, só com o sim do dono): `fleet_one_account_rule_for_own_accounts=false` tira o
    alvo que é conta nossa VIVA da regra de uma conta por alvo; o resto (espaçamento, retirada, pessoa real) continua."""
    svc, _db, policies, c = _tres(tmp_path, regra_nossa=False, espaco_s=120)
    _fez(svc, c["ana"], f"@{CLO}", segundos_atras=5000)
    comentar = capability_of(IG, "CREATE_COMMENT")
    veredito = policies.check(c["bia"], comentar, counterparty=f"@{CLO}")
    assert veredito.allowed and veredito.retry_at is None
    # o espaçamento entre contas sobre o mesmo alvo continua: a Ana acabou de mexer com a Clô, a Bia espera
    _fez(svc, c["ana"], f"@{CLO}", segundos_atras=1)
    veredito = policies.check(c["bia"], comentar, counterparty=f"@{CLO}")
    assert not veredito.allowed and veredito.retry_at is not None
    # pessoa real nunca sai da regra
    _fez(svc, c["ana"], "@pessoa.real", segundos_atras=5000)
    veredito = policies.check(c["bia"], comentar, counterparty="@pessoa.real")
    assert not veredito.allowed and veredito.retry_at is None and "uma conta por alvo" in veredito.reason
    # conta retirada segue recusada
    svc.retirar_conta_bloqueada(c["clo"], str(svc.repo.conta_ancora(c["clo"])["id"]))
    veredito = policies.check(c["bia"], comentar, counterparty=f"@{CLO}")
    assert not veredito.allowed and "retirada" in veredito.reason
    # o padrão é a regra valendo, como decidido em 02/10, até o sim do dono
    assert LimitsCfg().fleet_one_account_rule_for_own_accounts is True


def test_o_valor_vem_de_configuracao_com_padrao_de_600s() -> None:
    assert LimitsCfg().fleet_min_spacing_to_own_account_s == 600
    exemplo = (Path(__file__).resolve().parents[2] / "config" / "config.example.yaml").read_text(encoding="utf-8")
    assert "fleet_min_spacing_to_own_account_s: 600" in exemplo
