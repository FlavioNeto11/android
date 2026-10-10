"""31.291: a saída esperada de um perfil só muda pelo app, com rastro (`PUT /api/network/profiles/{id}`).

Tudo `simulated` (harness com aparelhos falsos, nenhum adb nem proxy). Provam-se:
- validação igual à do cadastro (IP público, texto, nada de nome de host) e `null` tira a família;
- evento `network.updated` `perfil_atualizado` com o valor de antes e o de depois, e nada além disso nos params;
- o perfil de uma conta do igfarm em uso recusa a troca sem `motivo` (409 `egress_esperado_protegido`, com o motivo
  da recusa no corpo) e a aceita com ele, que vai ao evento;
- sem mudança de valor, nem grava nem emite; perfil inexistente é 404; só os campos de saída entram no corpo.
Caso real que motivou: android-05, 10/10/2026, esperado regravado fora do app para o IP medido, sem evento.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest_asyncio

from app.devices.rede import criar_perfil_de_conta
from app.main import create_app

from .conftest import Harness


@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> AsyncIterator[Harness]:
    h = Harness(tmp_path, 4, store="android-04")
    await h.boot()
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _proxy(c: httpx.AsyncClient, nome: str, **params: Any) -> dict[str, Any]:
    r = await c.post("/api/network/profiles", json={"name": nome, "kind": "proxy", "protocol": "http",
                                                     "endpoint_host": "proxy.exemplo.test", "endpoint_port": 8080,
                                                     "params": params})
    assert r.status_code == 201, r.text
    return r.json()


def _eventos(h: Harness) -> list[dict[str, Any]]:
    st = h.state
    assert st is not None
    return [json.loads(r["data"]) for r in st.db.query(
        "SELECT data FROM events WHERE kind='network.updated' ORDER BY id") if "perfil_atualizado" in r["data"]]


def _params(h: Harness, pid: str) -> dict[str, Any]:
    st = h.state
    assert st is not None
    return json.loads(st.db.one("SELECT params FROM network_profiles WHERE id=?", (pid,))["params"])


def _conta_igfarm_em_uso(h: Harness, *, account_id: str = "acc-1", ip: str = "8.8.8.8") -> str:
    """Conta do igfarm registrada, com o perfil `igfarm-{conta}` (esperado = IP da criação) pedido ao android-01."""
    st = h.state
    assert st is not None
    st.db.execute("INSERT INTO instagram_profiles(id, username, status, created_at, updated_at) "
                  "VALUES ('p1','alvo.um','active','2026-01-01','2026-01-01')")
    st.db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, created_at, updated_at) "
                  "VALUES (?, 'p1','com.instagram.android','alvo.um','2026-01-01','2026-01-01')", (account_id,))
    st.db.execute("INSERT INTO contas_igfarm(account_id, profile_id, igfarm_account_id, username_registrado, "
                  "criada_em_igfarm, registrada_em, proxy_secret_ref, ip_criacao) "
                  "VALUES (?, 'p1','ig-1','alvo.um','2026-01-01','2026-01-01','ref-proxy',?)", (account_id, ip))
    pid = criar_perfil_de_conta(st, account_id, host="proxy.exemplo.test", port=8080, protocol="http",
                                username=None, secret=None, ip_criacao=ip, quem="teste")
    st.db.execute("INSERT INTO device_network(instance_id, proxy_profile_id, policy, desired_rev, state, updated_at, "
                  "updated_by) VALUES ('android-01', ?, 'exigida_com_bloqueio', 1, 'pendente', '2026-01-01', 'teste')",
                  (pid,))
    return pid


async def test_troca_o_esperado_valida_grava_e_emite_antes_e_depois(parque: Harness) -> None:
    async with _cliente(parque) as c:
        p = await _proxy(c, "Saida fixa", egress_esperado="8.8.8.8", username="u")
        r = await c.put(f"/api/network/profiles/{p['id']}", json={"egress_esperado": "1.1.1.1"})
        assert r.status_code == 200, r.text
        assert r.json()["params"] == {"egress_esperado": "1.1.1.1", "username": "u"}
    assert _params(parque, p["id"]) == {"egress_esperado": "1.1.1.1", "username": "u"}
    (ev,) = _eventos(parque)
    assert ev["acao"] == "perfil_atualizado" and ev["profile_id"] == p["id"]
    assert ev["antes"] == {"egress_esperado": "8.8.8.8", "egress_esperado_ipv6": None}
    assert ev["depois"] == {"egress_esperado": "1.1.1.1", "egress_esperado_ipv6": None}
    assert "username" not in json.dumps(ev)


async def test_null_tira_a_familia_e_ipv6_entra(parque: Harness) -> None:
    async with _cliente(parque) as c:
        p = await _proxy(c, "Dupla", egress_esperado="8.8.8.8")
        r = await c.put(f"/api/network/profiles/{p['id']}",
                        json={"egress_esperado": None, "egress_esperado_ipv6": "2001:4860:4860::8888"})
        assert r.status_code == 200, r.text
    assert _params(parque, p["id"]) == {"egress_esperado_ipv6": "2001:4860:4860::8888"}
    (ev,) = _eventos(parque)
    assert ev["antes"]["egress_esperado"] == "8.8.8.8" and ev["depois"]["egress_esperado"] is None


async def test_recusa_ip_que_nao_e_publico_nome_de_host_e_corpo_vazio(parque: Harness) -> None:
    async with _cliente(parque) as c:
        p = await _proxy(c, "Saida", egress_esperado="8.8.8.8")
        url = f"/api/network/profiles/{p['id']}"
        for ruim in ("192.168.1.10", "10.0.0.1", "exemplo.test", "", "2001:4860:4860::8888"):
            r = await c.put(url, json={"egress_esperado": ruim})
            assert r.status_code == 422, (ruim, r.text)
            assert r.json()["detail"]["code"] == "invalid_egress"
        assert (await c.put(url, json={})).status_code == 422
        assert (await c.put(url, json={"motivo": "só motivo"})).status_code == 422
        assert (await c.put(url, json={"egress_esperado": "1.1.1.1", "endpoint_host": "x.test"})).status_code == 422
    assert _params(parque, p["id"]) == {"egress_esperado": "8.8.8.8"}
    assert _eventos(parque) == []


async def test_mesmo_valor_nao_grava_nem_emite_e_perfil_inexistente_e_404(parque: Harness) -> None:
    async with _cliente(parque) as c:
        p = await _proxy(c, "Igual", egress_esperado="8.8.8.8")
        r = await c.put(f"/api/network/profiles/{p['id']}", json={"egress_esperado": "8.8.8.8"})
        assert r.status_code == 200
        assert (await c.put("/api/network/profiles/nao-existe", json={"egress_esperado": "8.8.8.8"})).status_code == 404
    assert _eventos(parque) == []


async def test_perfil_de_conta_igfarm_em_uso_exige_motivo_e_nada_muda_na_recusa(parque: Harness) -> None:
    """O caso do android-05: trocar o esperado pelo IP medido, sem rastro, anulava a comparação."""
    pid = _conta_igfarm_em_uso(parque, ip="188.72.57.98")
    async with _cliente(parque) as c:
        r = await c.put(f"/api/network/profiles/{pid}", json={"egress_esperado": "91.126.178.139"})
        assert r.status_code == 409, r.text
        d = r.json()["detail"]
        assert d["code"] == "egress_esperado_protegido" and d["motivo_obrigatorio"] is True
        assert d["in_use"] == ["android-01"] and d["account_id"] == "acc-1"
        assert "motivo" in d["message"] and "188.72.57.98" in json.dumps(d)
        assert _params(parque, pid)["egress_esperado"] == "188.72.57.98"
        assert _eventos(parque) == []

        r = await c.put(f"/api/network/profiles/{pid}", json={"egress_esperado": "91.126.178.139",
                                                              "motivo": "dono trocou o proxy da conta"})
        assert r.status_code == 200, r.text
    assert _params(parque, pid)["egress_esperado"] == "91.126.178.139"
    (ev,) = _eventos(parque)
    assert ev["motivo"] == "dono trocou o proxy da conta" and ev["conta_igfarm"] == "acc-1"
    assert ev["antes"]["egress_esperado"] == "188.72.57.98" and ev["depois"]["egress_esperado"] == "91.126.178.139"


async def test_perfil_igfarm_fora_de_uso_ou_sem_conta_nao_exige_motivo(parque: Harness) -> None:
    st = parque.state
    assert st is not None
    async with _cliente(parque) as c:
        # Perfil com nome de conta do igfarm, mas SEM aparelho pedindo: não há o que proteger.
        livre = criar_perfil_de_conta(st, "acc-9", host="proxy.exemplo.test", port=8080, protocol="http",
                                      username=None, secret=None, ip_criacao="8.8.8.8", quem="teste")
        assert (await c.put(f"/api/network/profiles/{livre}", json={"egress_esperado": "1.1.1.1"})).status_code == 200
        # Perfil em uso por aparelho, mas que não é de conta do igfarm: também livre.
        p = await _proxy(c, "Casa", egress_esperado="8.8.8.8")
        r = await c.post("/api/network/assign", json={"instance_ids": ["android-02"], "proxy_profile_id": p["id"]})
        assert r.status_code == 200, r.text
        assert (await c.put(f"/api/network/profiles/{p['id']}", json={"egress_esperado": "1.1.1.1"})).status_code == 200


async def test_motivo_com_cara_de_segredo_e_recusado(parque: Harness) -> None:
    async with _cliente(parque) as c:
        p = await _proxy(c, "Saida", egress_esperado="8.8.8.8")
        r = await c.put(f"/api/network/profiles/{p['id']}",
                        json={"egress_esperado": "1.1.1.1", "motivo": "senha=http://u:abc123@proxy.test:8080"})
        assert r.status_code == 422
    assert _params(parque, p["id"]) == {"egress_esperado": "8.8.8.8"}
