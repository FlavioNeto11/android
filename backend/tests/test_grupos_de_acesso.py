"""Grupos de acesso (migração 036): a escolha própria do perfil → o grupo → o padrão do catálogo."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import httpx
import pytest

from app.models import PolicyGroupCreate, PolicyGroupPatch, ProfilePatch, ProfilePolicyPatch
from app.planning.capabilities import Capability, CapabilityCatalog, capability_of
from app.planning.catalog import capabilities_of, register, unregister
from app.social.policy import DEFAULT_LIMITS
from app.social.service import SocialError

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente
from .test_capabilities import IG, build, perfil


def test_tres_camadas_proprio_grupo_padrao(tmp_path: Path) -> None:
    svc, repo, policies, _ = build(tmp_path)
    pid = perfil(svc)
    like, dm = capability_of(IG, "LIKE_POST"), capability_of(IG, "SEND_MESSAGE")
    g = svc.create_policy_group(PolicyGroupCreate(name="Cautelosos", capabilities={"LIKE_POST": "approval_required"},
                                                  limits={"likes_per_hour": 5}, profile_ids=[pid]))
    assert [m.id for m in g.members] == [pid]
    # sem escolha própria: vale o grupo; o que o grupo não diz vem do catálogo
    assert policies.policy_for(pid, like) == "approval_required" and policies.origin_for(pid, like) == "group"
    assert policies.policy_for(pid, dm) == dm.default_policy and policies.origin_for(pid, dm) == "default"
    assert policies.limits_for(pid)["likes_per_hour"] == 5
    assert policies.limits_origin(pid)["likes_per_hour"] == "group"
    assert policies.limits_for(pid)["dms_per_hour"] == DEFAULT_LIMITS["dms_per_hour"]

    # a escolha deliberada do perfil sobrepõe o grupo
    svc.set_policy(pid, ProfilePolicyPatch(capabilities={"LIKE_POST": "autonomous"}, limits={"likes_per_hour": 9}))
    assert policies.policy_for(pid, like) == "autonomous" and policies.origin_for(pid, like) == "own"
    assert policies.limits_for(pid)["likes_per_hour"] == 9

    # `null` devolve a ação (e o limite) ao grupo
    dto = svc.set_policy(pid, ProfilePolicyPatch(capabilities={"LIKE_POST": None}, limits={"likes_per_hour": None}))
    assert dto.capabilities["LIKE_POST"] == "approval_required" and dto.origin["LIKE_POST"] == "group"
    assert "LIKE_POST" not in dto.own and dto.group == {"LIKE_POST": "approval_required"}
    assert dto.limits["likes_per_hour"] == 5 and dto.limits_origin["likes_per_hour"] == "group"
    assert dto.group_id == g.id and dto.group_name == "Cautelosos"

    # mudar o grupo muda todos os membros que não escolheram por conta própria
    svc.update_policy_group(g.id, PolicyGroupPatch(capabilities={"LIKE_POST": "disabled"}))
    assert policies.policy_for(pid, like) == "disabled"
    veredito = policies.check(pid, like)                         # é o que o despacho consulta (_policy_gate)
    assert not veredito.allowed and "desligada" in veredito.reason


def test_o_despacho_pede_aprovacao_quando_o_grupo_exige(tmp_path: Path) -> None:
    """Sem escolha própria para FOLLOW e com o grupo em `approval_required`, o portão pede aprovação."""
    svc, _, policies, _ = build(tmp_path)
    pid = perfil(svc)
    follow = capability_of(IG, "FOLLOW")
    svc.update_policy_group(svc.create_policy_group(PolicyGroupCreate(name="Tudo aprovado")).id,
                            PolicyGroupPatch(capabilities={"FOLLOW": "approval_required"}, profile_ids=[pid]))
    veredito = policies.check(pid, follow, run_id="run-1")
    assert veredito.allowed and veredito.needs_approval and veredito.policy == "approval_required"


def test_escolhas_proprias_ja_gravadas_continuam_valendo_sobre_o_grupo(tmp_path: Path) -> None:
    """Os 8 perfis de produção têm SEND_MESSAGE autônomo gravado como escolha própria: entrar num grupo mais
    rígido NÃO muda isso sozinho — a origem fica visível (`own`) e 'herdar' é um gesto explícito."""
    svc, repo, policies, _ = build(tmp_path)
    pid = perfil(svc)
    repo.update_profile(pid, {"automation_policy": '{"capabilities": {"SEND_MESSAGE": "autonomous"}}'})
    svc.create_policy_group(PolicyGroupCreate(name="Rígido", capabilities={"SEND_MESSAGE": "manual_only"},
                                              profile_ids=[pid]))
    dm = capability_of(IG, "SEND_MESSAGE")
    assert policies.policy_for(pid, dm) == "autonomous" and policies.origin_for(pid, dm) == "own"


def test_apagar_o_grupo_desvincula_os_membros_e_mantem_as_escolhas_proprias(tmp_path: Path) -> None:
    svc, repo, policies, _ = build(tmp_path)
    a, b = perfil(svc), perfil(svc, "outro.perfil", "android-01")
    svc.set_policy(a, ProfilePolicyPatch(capabilities={"LIKE_POST": "manual_only"}))
    g = svc.create_policy_group(PolicyGroupCreate(name="G", capabilities={"LIKE_POST": "disabled"},
                                                  profile_ids=[a, b]))
    assert policies.policy_for(b, capability_of(IG, "LIKE_POST")) == "disabled"
    svc.delete_policy_group(g.id)
    assert repo.profile_row(a)["policy_group_id"] is None and repo.profile_row(b)["policy_group_id"] is None
    assert policies.policy_for(a, capability_of(IG, "LIKE_POST")) == "manual_only"      # a própria ficou
    assert policies.policy_for(b, capability_of(IG, "LIKE_POST")) == "autonomous"       # volta ao catálogo
    with pytest.raises(SocialError):
        svc.get_policy_group(g.id)


def test_membros_nome_validacao_e_grupo_a_partir_de_um_perfil(tmp_path: Path) -> None:
    svc, _, _, _ = build(tmp_path)
    a, b = perfil(svc), perfil(svc, "outro.perfil", "android-01")
    svc.set_policy(a, ProfilePolicyPatch(capabilities={"SEND_MESSAGE": "autonomous"}, limits={"dms_per_hour": 4}))
    g = svc.create_policy_group(PolicyGroupCreate(name="Como a Luciana", from_profile_id=a))
    assert g.capabilities == {"SEND_MESSAGE": "autonomous"} and g.limits == {"dms_per_hour": 4}
    assert g.loosened == ["SEND_MESSAGE"]                         # risco alto afrouxado fica marcado no grupo
    # a lista de membros é a COMPLETA
    g = svc.update_policy_group(g.id, PolicyGroupPatch(profile_ids=[a, b]))
    assert {m.id for m in g.members} == {a, b}
    g = svc.update_policy_group(g.id, PolicyGroupPatch(profile_ids=[b]))
    assert [m.id for m in g.members] == [b]
    # o perfil também entra/sai pelo próprio cadastro
    assert svc.update_profile(a, ProfilePatch(policy_group_id=g.id)).policy_group_name == "Como a Luciana"
    assert svc.update_profile(a, ProfilePatch(policy_group_id=None)).policy_group_id is None
    for ruim in (PolicyGroupCreate(name="como a luciana"),                                # nome repetido
                 PolicyGroupCreate(name="X", capabilities={"VOAR": "autonomous"}),       # ação inexistente
                 PolicyGroupCreate(name="Y", limits={"likes_per_hour": -1}),             # limite negativo
                 PolicyGroupCreate(name="Z", profile_ids=["ig-nao-existe"])):            # membro inexistente
        with pytest.raises(SocialError):
            svc.create_policy_group(ruim)
    with pytest.raises(SocialError):
        svc.update_profile(a, ProfilePatch(policy_group_id="grp-nao-existe"))


def test_membro_sem_conta_traz_o_nome_da_pessoa(tmp_path: Path) -> None:
    """29.25: depois que a conta da persona sai (29.23), o `username` fica vazio; o membro ainda traz o NOME para
    o painel não desenhar um chip "@" sozinho. Com conta e sem nome próprio, o nome cai no @."""
    svc, repo, _, _ = build(tmp_path)
    a, b = perfil(svc), perfil(svc, "outro.perfil", "android-01")
    repo.db.execute("UPDATE instagram_profiles SET username='', display_name='Sueli Barreto' WHERE id=?", (a,))
    g = svc.create_policy_group(PolicyGroupCreate(name="Sem arroba", profile_ids=[a, b]))
    por_id = {m.id: m for m in g.members}
    assert not por_id[a].username and por_id[a].name == "Sueli Barreto"
    assert por_id[b].username == "outro.perfil" and por_id[b].name


async def test_rotas_http_dos_grupos(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        from app.models import ProfileCreate
        pid = h.state.social.create_profile(ProfileCreate(username="rota.teste", instance_id="android-01")).id
        async with _cliente(h) as c:
            r = await c.post("/api/instagram/policy-groups", json={"name": "Aquecendo", "profile_ids": [pid],
                                                                    "limits": {"likes_per_hour": 3}})
            assert r.status_code == 201, r.text
            gid = r.json()["id"]
            # 23.10: sem `?package=` nas rotas de grupo, o painel sempre editava o app âncora "por acaso" (o
            # primeiro da lista com login gerenciado); agora é explícito, e a resposta diz contra qual catálogo
            # `loosened` foi calculado.
            assert r.json()["package"] == "com.instagram.android"
            explicito = await c.get(f"/api/instagram/policy-groups/{gid}", params={"package": "com.instagram.android"})
            assert explicito.json()["package"] == "com.instagram.android"
            assert (await c.get("/api/instagram/policy-groups")).json()[0]["members"][0]["id"] == pid
            pol = (await c.get(f"/api/instagram/profiles/{pid}/policy")).json()
            assert pol["limits"]["likes_per_hour"] == 3 and pol["limits_origin"]["likes_per_hour"] == "group"
            r = await c.put(f"/api/instagram/profiles/{pid}/policy", json={"capabilities": {"LIKE_POST": None}})
            assert r.status_code == 200, r.text
            assert (await c.put(f"/api/instagram/policy-groups/{gid}", json={"name": "Aquecidos"})).json()["name"] == "Aquecidos"
            assert (await c.get(f"/api/instagram/profiles/{pid}")).json()["policy_group_name"] == "Aquecidos"
            assert (await c.delete(f"/api/instagram/policy-groups/{gid}")).status_code == 204
            assert (await c.get(f"/api/instagram/policy-groups/{gid}")).status_code == 404
            assert (await c.get("/api/instagram/policy-defaults")).json()["limits"] == DEFAULT_LIMITS
    finally:
        await h.state.stop()


# ==================================================================== política POR APP (23.10)
PACOTE_OUTRO = "com.exemplo.correio"


@pytest.fixture
def outro_app_com_send_message() -> Iterator[None]:
    """Um segundo app com catálogo que REPETE uma chave do Instagram (SEND_MESSAGE), como um catálogo do Outlook
    provavelmente fará (T17). É o caso que um dicionário plano por `cap.key` não separa."""
    enviar = Capability(
        key="SEND_MESSAGE", title="Enviar e-mail", goal="Mandar um e-mail para {destinatario}.",
        post_kind="model_judged", post_value="e-mail enviado", post_description="O e-mail aparece em Enviados.",
        bindings=("destinatario",), side_effect=True, risk="high", default_policy="manual_only",
        limit_bucket="dms", counterparty="destinatario")
    register(PACOTE_OUTRO, CapabilityCatalog(PACOTE_OUTRO, [enviar]),
             capabilities_of(PACOTE_OUTRO).__class__(package=PACOTE_OUTRO, name="Correio", session_provider=None,
                                                     needs_profile=False))
    try:
        yield
    finally:
        unregister(PACOTE_OUTRO)


def test_politica_do_perfil_e_por_app_mesmo_com_a_mesma_chave(tmp_path: Path, outro_app_com_send_message: None) -> None:
    svc, _, policies, _ = build(tmp_path)
    pid = perfil(svc)
    dm_ig, email = capability_of(IG, "SEND_MESSAGE"), capability_of(PACOTE_OUTRO, "SEND_MESSAGE")
    antes = svc.get_policy(pid, package=IG).capabilities["SEND_MESSAGE"]

    dto = svc.set_policy(pid, ProfilePolicyPatch(capabilities={"SEND_MESSAGE": "disabled"}), package=PACOTE_OUTRO)
    assert dto.package == PACOTE_OUTRO and dto.own == {"SEND_MESSAGE": "disabled"}

    # o Instagram não mudou: nem a política efetiva, nem a origem, nem o que o perfil "escolheu" nele
    ig = svc.get_policy(pid, package=IG)
    assert ig.capabilities["SEND_MESSAGE"] == antes and ig.origin["SEND_MESSAGE"] == "default" and ig.own == {}
    assert svc.get_policy(pid).capabilities["SEND_MESSAGE"] == antes                 # sem `package` = o âncora
    # e o despacho (o que `_policy_gate` consulta, com o pacote da etapa) separa os dois
    assert policies.policy_for(pid, dm_ig, IG) == antes and policies.origin_for(pid, dm_ig, IG) == "default"
    assert policies.policy_for(pid, email, PACOTE_OUTRO) == "disabled"
    assert not policies.check(pid, email, package=PACOTE_OUTRO).allowed
    assert policies.check(pid, dm_ig, package=IG).policy != "disabled"

    # o caminho inverso: mudar o Instagram não mexe no outro app, e herdar num não apaga o outro
    svc.set_policy(pid, ProfilePolicyPatch(capabilities={"SEND_MESSAGE": "approval_required"}), package=IG)
    assert svc.get_policy(pid, package=PACOTE_OUTRO).capabilities["SEND_MESSAGE"] == "disabled"
    svc.set_policy(pid, ProfilePolicyPatch(capabilities={"SEND_MESSAGE": None}), package=PACOTE_OUTRO)
    assert svc.get_policy(pid, package=PACOTE_OUTRO).origin["SEND_MESSAGE"] == "default"
    assert svc.get_policy(pid, package=IG).own == {"SEND_MESSAGE": "approval_required"}


def test_grupo_de_acesso_e_por_app_mesmo_com_a_mesma_chave(tmp_path: Path, outro_app_com_send_message: None) -> None:
    svc, _, policies, _ = build(tmp_path)
    pid = perfil(svc)
    dm_ig = capability_of(IG, "SEND_MESSAGE")
    g = svc.create_policy_group(PolicyGroupCreate(name="Correio fechado", profile_ids=[pid]))
    svc.update_policy_group(g.id, PolicyGroupPatch(capabilities={"SEND_MESSAGE": "autonomous"}), package=PACOTE_OUTRO)

    assert svc.get_policy_group(g.id, package=PACOTE_OUTRO).capabilities == {"SEND_MESSAGE": "autonomous"}
    # afrouxar no outro app aparece como afrouxado NELE, e o Instagram segue sem escolha nenhuma do grupo
    assert svc.get_policy_group(g.id, package=PACOTE_OUTRO).loosened == ["SEND_MESSAGE"]
    assert svc.get_policy_group(g.id, package=IG).capabilities == {} and svc.get_policy_group(g.id).loosened == []
    assert svc.get_policy(pid, package=IG).group == {}
    assert policies.origin_for(pid, dm_ig, IG) == "default"
    assert policies.policy_for(pid, dm_ig, IG) == dm_ig.default_policy

    # editar o grupo no Instagram preserva o outro app
    svc.update_policy_group(g.id, PolicyGroupPatch(capabilities={"SEND_MESSAGE": "disabled"}), package=IG)
    assert svc.get_policy_group(g.id, package=PACOTE_OUTRO).capabilities == {"SEND_MESSAGE": "autonomous"}
    assert svc.get_policy_group(g.id, package=IG).capabilities == {"SEND_MESSAGE": "disabled"}

    # "começar a partir de" um perfil copia o recorte do app pedido, não a mistura dos dois
    svc.set_policy(pid, ProfilePolicyPatch(capabilities={"SEND_MESSAGE": "approval_required"}), package=PACOTE_OUTRO)
    novo = svc.create_policy_group(PolicyGroupCreate(name="Copia do correio", from_profile_id=pid), package=IG)
    assert novo.capabilities == {"SEND_MESSAGE": "disabled"}                         # o do grupo no Instagram
    assert svc.get_policy_group(novo.id, package=PACOTE_OUTRO).capabilities == {}
