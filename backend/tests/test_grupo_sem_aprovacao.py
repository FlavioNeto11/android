"""28.61 restaurado (depois do ADR-083): a persona do grupo `grupo_sem_aprovacao` não passa pela aprovação de política.

O refactor do dono de 07/10 tirou de `policy.py` o leitor `_sem_aprovacao_pelo_grupo` junto com os tetos de taxa, e o campo
da config ficou sem leitor: o grupo liberado voltou a pedir aprovação para comentar e mandar DM. O pedido do dono de 06/10
("um grupo com tudo liberado para todas as personas… para que não seja necessário permissões") era sobre aprovação, não
sobre taxa. Aqui o leitor volta, só ele: troca `approval_required` por `autonomous` e nada mais; ação desligada ou manual
segue recusada.

Nível de prova: `simulated` (banco de teste e perfis de teste). Nada real.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.models import PolicyGroupCreate, ProfileCreate, ProfilePolicyPatch
from app.planning.capabilities import capability_of
from app.social.policy import PolicyEngine

from .apoio_politica import IG, SENHA, build, perfil


def _cenario(tmp_path: Path, grupo_ligado: bool = True):  # type: ignore[no-untyped-def]
    svc, repo, _pol, _db = build(tmp_path)
    dentro = perfil(svc)
    fora = svc.create_profile(ProfileCreate(username="outra.persona4821", password=SENHA, instance_id="android-01")).id
    grupo = svc.create_policy_group(PolicyGroupCreate(name="Liberado", profile_ids=[dentro]))
    motor = PolicyEngine(repo, lambda: SimpleNamespace(grupo_sem_aprovacao=grupo.id if grupo_ligado else ""))
    cap = capability_of(IG, "SEND_MESSAGE")
    assert cap is not None and cap.default_policy == "approval_required"
    return svc, motor, cap, dentro, fora


def test_a_persona_do_grupo_age_sem_pedir_aprovacao_e_o_motivo_diz_por_que(tmp_path: Path) -> None:
    _svc, motor, cap, dentro, _fora = _cenario(tmp_path)
    v = motor.check(dentro, cap, package=IG)
    assert v.allowed and v.policy == "autonomous" and not v.needs_approval
    assert "28.61" in v.reason


def test_fora_do_grupo_ou_com_a_chave_vazia_segue_pedindo_aprovacao(tmp_path: Path) -> None:
    _svc, motor, cap, _dentro, fora = _cenario(tmp_path)
    v = motor.check(fora, cap, package=IG)
    assert v.policy == "approval_required" and v.needs_approval
    _svc, motor, cap, dentro, _fora = _cenario(tmp_path / "sem-chave", grupo_ligado=False)
    assert motor.check(dentro, cap, package=IG).needs_approval


def test_acao_desligada_ou_manual_do_perfil_segue_recusada_mesmo_no_grupo(tmp_path: Path) -> None:
    svc, motor, cap, dentro, _fora = _cenario(tmp_path)
    for escolha in ("disabled", "manual_only"):
        svc.set_policy(dentro, ProfilePolicyPatch(capabilities={"SEND_MESSAGE": escolha}), package=IG)
        v = motor.check(dentro, cap, package=IG)
        assert not v.allowed and v.policy == escolha
