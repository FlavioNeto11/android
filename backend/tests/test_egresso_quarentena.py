"""31.317: a limpeza do egresso de uma conta retirada funciona com o aparelho ainda em quarentena.

Caso real (android-07, 10/10/2026): a conta do Rafael foi retirada com a quarentena da conta ainda aberta; o `atribuir`
recusou `aparelho_em_quarentena`, o perfil continuou pedido ao aparelho (`network_profile_in_use`) e ficou com o proxy
e o IP esperado da conta morta. Tudo `simulated` (harness, nenhum aparelho).
"""
from __future__ import annotations

import pytest

from app.devices.rede import NetworkAssignBody, RedeError, atribuir
from app.modules.identity.infrastructure import egresso

QUARENTENA = "conta retirada, bloqueada: nada toca o aparelho"


def _semear(harness, *, device: str = "android-01") -> str:
    db = harness.state.db
    db.execute("INSERT INTO instagram_profiles(id, username, status, created_at, updated_at) "
               "VALUES ('p1','alvo.um','active','2026-01-01','2026-01-01')")
    db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, created_at, updated_at) "
               "VALUES ('acc-1','p1','com.instagram.android','alvo.um','2026-01-01','2026-01-01')")
    db.execute("INSERT INTO contas_igfarm(account_id, profile_id, igfarm_account_id, username_registrado, "
               "criada_em_igfarm, registrada_em, proxy_secret_ref, ip_criacao) "
               "VALUES ('acc-1','p1','ig-1','alvo.um','2026-01-01','2026-01-01','ref-proxy','8.8.8.8')")
    db.execute("INSERT INTO network_profiles(id, name, kind, protocol, endpoint_host, endpoint_port, params, "
               "created_at) VALUES ('np-1','igfarm-acc-1','proxy','http','p.example.com',8080,"
               "'{\"egress_esperado\":\"8.8.8.8\"}','2026-01-01')")
    db.execute("INSERT INTO device_network(instance_id, proxy_profile_id, policy, desired_rev, applied_rev, state, "
               "updated_at) VALUES (?, 'np-1', 'exigida', 1, 1, 'trafego_verificado', '2026-01-01')", (device,))
    return "np-1"


def _em_quarentena(harness, monkeypatch, device: str = "android-01") -> None:
    monkeypatch.setattr(harness.state, "quarentena", lambda iid: QUARENTENA if iid == device else None)


def _linha(harness, device: str = "android-01"):
    return harness.state.db.one("SELECT proxy_profile_id, policy, desired_rev, state FROM device_network "
                                "WHERE instance_id=?", (device,))


def test_atribuir_comum_continua_recusando_aparelho_em_quarentena(harness, monkeypatch):
    """A porta da quarentena (ADR-055) não mudou: só a limpeza do egresso passa por `durante_quarentena`."""
    _semear(harness)
    _em_quarentena(harness, monkeypatch)
    with pytest.raises(RedeError) as exc:
        atribuir(harness.state, NetworkAssignBody(instance_ids=["android-01"], proxy_profile_id=None,
                                                  policy="livre", confirm_real_account=["android-01"]), quem="t")
    assert exc.value.code == "aparelho_em_quarentena"
    assert _linha(harness)["proxy_profile_id"] == "np-1"


def test_durante_quarentena_registra_o_pedido_sem_tocar_o_aparelho(harness, monkeypatch):
    _semear(harness)
    _em_quarentena(harness, monkeypatch)
    atribuir(harness.state, NetworkAssignBody(instance_ids=["android-01"], proxy_profile_id=None, policy="livre",
                                              confirm_real_account=["android-01"]), quem="t",
             durante_quarentena=True)
    linha = _linha(harness)
    assert linha["proxy_profile_id"] is None and linha["policy"] == "livre"
    assert linha["state"] == "pendente" and int(linha["desired_rev"]) == 2    # a convergência aplica depois


def test_limpar_egresso_com_aparelho_em_quarentena_desatribui_e_remove_o_perfil(harness, monkeypatch):
    perfil = _semear(harness)
    _em_quarentena(harness, monkeypatch)
    apagados: list[str] = []
    monkeypatch.setattr(harness.state.secrets, "delete_secret", lambda ref: apagados.append(ref))
    egresso.limpar_egresso(harness.state, "p1", "acc-1", "ref-proxy")
    assert _linha(harness)["proxy_profile_id"] is None
    assert harness.state.db.one("SELECT id FROM network_profiles WHERE id=?", (perfil,)) is None
    assert apagados == ["ref-proxy"]
    avisos = harness.state.db.query("SELECT message FROM events WHERE kind='log' AND level='warn' "
                                    "AND message LIKE '%egresso dela ficou pela metade%'")
    assert avisos == []


def test_limpar_egresso_que_falha_deixa_aviso_visivel_com_os_ids(harness, monkeypatch):
    perfil = _semear(harness)

    def recusa(st, body, quem, **kw):
        raise RedeError(409, "aparelho_em_quarentena", QUARENTENA)

    monkeypatch.setattr(egresso, "atribuir", recusa)
    egresso.limpar_egresso(harness.state, "p1", "acc-1", None)
    assert _linha(harness)["proxy_profile_id"] == perfil                   # ficou pedido: o perfil não saiu
    (aviso,) = harness.state.db.query("SELECT message, data FROM events WHERE kind='log' AND level='warn' "
                                      "AND message LIKE '%egresso dela ficou pela metade%'")
    assert "android-01" in aviso["message"] and "aparelho_em_quarentena" in aviso["message"]
    assert "np-1" in aviso["data"] and "network_profile_in_use" in aviso["data"]
    assert "ref-proxy" not in aviso["message"] + aviso["data"]            # nenhum segredo no aviso
