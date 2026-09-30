"""Rede por aparelho, item 25.2 (ADR-056, contrato C3): o modelo e as rotas `/api/network/*`.

O que se prova aqui (tudo `simulated`: harness com aparelhos falsos, nenhum adb, nenhuma VPN):
- o segredo do perfil entra UMA vez, vai ao cofre e não volta em resposta (nem no 422), evento ou coluna de texto;
- perfil em uso não se apaga (409 com os aparelhos), e apagar leva o segredo junto;
- a atribuição em lote tem prévia sem gravar, é tudo ou nada, recusa a loja e a quarentena e exige a confirmação
  POR APARELHO para conta real — só quando a SAÍDA muda (trocar só `livre` ↔ `exigida` não é mudar de saída);
- estado só avança com evidência: atribuir e reaplicar regridem a `pendente`; `trafego_verificado` só nasce de uma
  medição de dentro do aparelho com IP de saída PÚBLICO e cada app das contas do aparelho `ok` (e o vazamento
  barrado, na política com bloqueio); o que falta vira `parcial`; observação de revisão velha, ou de revisão que
  mudou no meio do registro, não mexe no estado;
- `verify`/`reapply` respondem 202 com `executed: false`: registram o pedido, não fingem aplicação;
- o proxy legado da 041 aparece como `configurado` no máximo.
"""
from __future__ import annotations

import json
import secrets as pysecrets
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest
import pytest_asyncio

from app.devices import rede
from app.devices.rede import NetworkMeasurementInput, RedeError, pendencias, registrar_medicao, registrar_observacao
from app.main import create_app
from app.modules.identity.presentation.schemas import ProfileCreate
from app.security.secret_store import SecretStoreUnavailable
from app.util import now_iso

from .conftest import Harness

LOJA = "android-04"


@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> AsyncIterator[Harness]:
    """android-01..03 são de tarefa; android-04 é a loja."""
    h = Harness(tmp_path, 4, store=LOJA)
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


def _segredo() -> str:
    # Gerado na hora: nenhum valor de segredo mora no teste (CLAUDE.md, invariante de segredo).
    return f"wg-{pysecrets.token_urlsafe(24)}"


def _em_lugar_nenhum(h: Harness, valor: str) -> None:
    """O valor não aparece em nenhuma tabela de texto da rede nem nos eventos — inteiras."""
    db = h.state.db  # type: ignore[union-attr]
    for tabela in ("network_profiles", "device_network", "network_measurements", "events", "commands"):
        for linha in db.query(f"SELECT * FROM {tabela}"):                      # noqa: S608 - nome fixo do teste
            assert valor not in json.dumps(dict(linha), default=str), tabela


async def _perfil(c: httpx.AsyncClient, nome: str, kind: str = "vpn", protocol: str = "wireguard",
                  **extra: Any) -> dict[str, Any]:
    r = await c.post("/api/network/profiles", json={"name": nome, "kind": kind, "protocol": protocol,
                                                     "endpoint_host": "vpn.exemplo.test", "endpoint_port": 51820,
                                                     **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _linha(h: Harness, iid: str) -> dict[str, Any] | None:
    return h.state.db.one("SELECT * FROM device_network WHERE instance_id=?", (iid,))  # type: ignore[union-attr]


# ==================================================================== perfis e segredo
async def test_perfil_guarda_o_segredo_no_cofre_e_so_devolve_has_secret(parque: Harness) -> None:
    st = parque.state
    assert st is not None
    valor = _segredo()
    async with _cliente(parque) as c:
        r = await c.post("/api/network/profiles", json={
            "name": "Casa", "kind": "vpn", "protocol": "wireguard", "endpoint_host": "vpn.exemplo.test",
            "endpoint_port": 51820, "params": {"mtu": 1280, "dns": ["1.1.1.1"]}, "secret": valor})
        assert r.status_code == 201, r.text
        assert valor not in r.text
        perfil = r.json()
        assert perfil["has_secret"] is True and "secret_ref" not in perfil and perfil["params"]["mtu"] == 1280

        # O valor está no cofre (cifrado), e só a referência mora na linha.
        row = st.db.one("SELECT secret_ref FROM network_profiles WHERE id=?", (perfil["id"],))
        assert row is not None and row["secret_ref"] and st.secrets.get_secret(row["secret_ref"]) == valor

        lista = await c.get("/api/network/profiles")
        assert lista.status_code == 200 and valor not in lista.text
        assert lista.json()["profiles"] == [{**perfil, "in_use": []}]

        # Perfil sem segredo: has_secret falso, e proxy com IPv6 literal é endpoint válido.
        sem = await _perfil(c, "Proxy do escritório", "proxy", "socks5", endpoint_host="2001:db8::10")
        assert sem["has_secret"] is False and sem["endpoint_host"] == "2001:db8::10"

        dup = await c.post("/api/network/profiles", json={"name": "Casa", "kind": "vpn", "protocol": "singbox",
                                                           "endpoint_host": "x.test", "endpoint_port": 1})
        assert dup.status_code == 409 and dup.json()["detail"]["code"] == "name_taken"
    _em_lugar_nenhum(parque, valor)


async def test_cadastro_recusado_nunca_devolve_o_segredo(parque: Harness) -> None:
    """O 422 padrão devolve o corpo inteiro como `input` num campo faltando ou numa regra do modelo: o segredo
    iria junto. Cada recusa aqui leva um segredo no corpo, e nenhuma resposta o repete."""
    valor = _segredo()
    base = {"name": "X", "kind": "vpn", "protocol": "wireguard", "endpoint_host": "vpn.exemplo.test",
            "endpoint_port": 51820, "secret": valor}
    ruins = [
        {**base, "protocol": "http"},                                   # par tipo × protocolo
        {k: v for k, v in base.items() if k != "endpoint_port"},        # campo faltando
        {**base, "endpoint_host": "10.0.0.5;reboot"},                   # nada de shell dentro do host
        {**base, "params": {"password": valor}},                        # segredo em params, pela chave
        {**base, "params": {"upstream": f"http://usuario:{valor}@proxy.test:3128"}},  # ... e pela URL
        {**base, "extra": 1},                                           # campo desconhecido
    ]
    async with _cliente(parque) as c:
        for corpo in ruins:
            r = await c.post("/api/network/profiles", json=corpo)
            assert r.status_code == 422, (corpo.keys(), r.text)
            assert valor not in r.text, r.text
        vazio = await c.post("/api/network/profiles", json={**base, "secret": ""})
        assert vazio.status_code == 422 and vazio.json()["detail"]["code"] == "invalid_secret"
        assert (await c.get("/api/network/profiles")).json()["profiles"] == []
    _em_lugar_nenhum(parque, valor)


async def test_cofre_fechado_nao_deixa_perfil_sem_a_chave_que_anuncia(parque: Harness,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None

    def fechado(*_a: Any, **_k: Any) -> str:
        raise SecretStoreUnavailable("Nenhuma chave mestra disponível para proteger credenciais.")

    monkeypatch.setattr(st.secrets, "store_secret", fechado)
    async with _cliente(parque) as c:
        r = await c.post("/api/network/profiles", json={"name": "Casa", "kind": "vpn", "protocol": "wireguard",
                                                         "endpoint_host": "vpn.test", "endpoint_port": 51820,
                                                         "secret": _segredo()})
        assert r.status_code == 503 and r.json()["detail"]["code"] == "secret_store_unavailable"
    assert st.db.one("SELECT id FROM network_profiles") is None


async def test_perfil_em_uso_nao_se_apaga_e_apagar_leva_o_segredo(parque: Harness) -> None:
    st = parque.state
    assert st is not None
    async with _cliente(parque) as c:
        vpn = await _perfil(c, "Casa", secret=_segredo())
        ref = st.db.one("SELECT secret_ref FROM network_profiles WHERE id=?", (vpn["id"],))["secret_ref"]
        r = await c.post("/api/network/assign", json={"instance_ids": ["android-01"], "vpn_profile_id": vpn["id"]})
        assert r.status_code == 200, r.text

        r = await c.delete(f"/api/network/profiles/{vpn['id']}")
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "network_profile_in_use"
        assert r.json()["detail"]["instance_ids"] == ["android-01"]
        assert (await c.get("/api/network/profiles")).json()["profiles"][0]["in_use"] == ["android-01"]

        # Tirar o perfil do aparelho que nunca aplicou nada apaga a linha (nada a convergir) e libera o perfil.
        r = await c.post("/api/network/assign", json={"instance_ids": ["android-01"], "vpn_profile_id": None})
        assert r.status_code == 200 and _linha(parque, "android-01") is None
        assert (await c.delete(f"/api/network/profiles/{vpn['id']}")).status_code == 204
        assert not st.secrets.exists(ref)
        assert (await c.delete(f"/api/network/profiles/{vpn['id']}")).status_code == 404


# ==================================================================== atribuição
async def test_atribuicao_em_lote_previa_tudo_ou_nada_e_confirmacao_por_aparelho(parque: Harness) -> None:
    st = parque.state
    assert st is not None
    # android-01 tem conta real vinculada; android-02 está em quarentena (conta travada logada).
    st.social.create_profile(ProfileCreate(username="lucas.teste", instance_id="android-01"))
    st.social_repo.marcar_conta_travada("android-02", "felipe.teste", "tela de desafio", "declarado",
                                        visto_por="dono")
    async with _cliente(parque) as c:
        vpn = await _perfil(c, "Casa")
        alvo = {"vpn_profile_id": vpn["id"], "policy": "exigida"}

        r = await c.post("/api/network/assign", json={"instance_ids": ["android-01", "android-02", "android-03",
                                                                       LOJA], **alvo, "dry_run": True})
        assert r.status_code == 200, r.text
        previa = {d["id"]: d for d in r.json()["devices"]}
        assert r.json()["accepted"] is False
        assert previa["android-01"]["code"] == "real_account_confirm_required"
        assert "@lucas.teste" in previa["android-01"]["reason"]
        assert previa["android-02"]["code"] == "aparelho_em_quarentena"
        assert previa["android-03"]["outcome"] == "would_assign" and previa["android-03"]["reapply"] is True
        assert previa[LOJA]["code"] == "store_instance"
        assert st.db.one("SELECT * FROM device_network") is None          # prévia não grava

        # Sem dry_run: tudo ou nada. A recusa carrega a prévia, e nem o android-03 (que passaria) é gravado.
        r = await c.post("/api/network/assign", json={"instance_ids": ["android-01", "android-03"], **alvo})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "real_account_confirm_required"
        assert {d["id"] for d in r.json()["detail"]["devices"]} == {"android-01", "android-03"}
        assert st.db.one("SELECT * FROM device_network") is None

        # A confirmação é POR APARELHO: confirmar outro não vale para o android-01.
        r = await c.post("/api/network/assign", json={"instance_ids": ["android-01"], **alvo,
                                                      "confirm_real_account": ["android-03"]})
        assert r.status_code == 409
        r = await c.post("/api/network/assign", json={"instance_ids": ["android-01", "android-03"], **alvo,
                                                      "confirm_real_account": ["android-01"]})
        assert r.status_code == 200 and r.json()["accepted"] is True
        assert {d["id"]: d["outcome"] for d in r.json()["devices"]} == {"android-01": "assigned",
                                                                        "android-03": "assigned"}
        linha = _linha(parque, "android-01")
        assert linha is not None and linha["vpn_profile_id"] == vpn["id"] and linha["policy"] == "exigida"
        assert linha["desired_rev"] == 1 and linha["applied_rev"] is None and linha["state"] == "pendente"

        # Trocar só a política (exigida → livre) não muda a saída: sem confirmação, sem revisão nova.
        r = await c.post("/api/network/assign", json={"instance_ids": ["android-01"], "policy": "livre"})
        assert r.status_code == 200 and r.json()["devices"][0]["reapply"] is False
        assert _linha(parque, "android-01")["desired_rev"] == 1              # type: ignore[index]

        # A quarentena não recebe nem mudança de política, e a loja nunca.
        r = await c.post("/api/network/assign", json={"instance_ids": ["android-02"], **alvo})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "aparelho_em_quarentena"
        r = await c.post("/api/network/assign", json={"instance_ids": [LOJA], **alvo})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "store_instance"

    # O evento da mudança diz ids, política e revisão — e nada de segredo nem referência do cofre.
    eventos = st.db.query("SELECT data FROM events WHERE kind='network.updated' AND instance_id='android-03'")
    assert eventos and json.loads(eventos[-1]["data"])["vpn_profile_id"] == vpn["id"]
    assert all("secret" not in (e["data"] or "") for e in eventos)


async def test_atribuicao_recusa_o_que_nao_faz_sentido(parque: Harness) -> None:
    async with _cliente(parque) as c:
        vpn = await _perfil(c, "Casa")
        proxy = await _perfil(c, "Escritório", "proxy", "http")
        casos = [
            ({"instance_ids": ["android-01"]}, 400, "nothing_to_change"),
            ({"instance_ids": ["android-99"], "vpn_profile_id": vpn["id"]}, 400, "unknown_instance"),
            ({"instance_ids": ["android-01"], "vpn_profile_id": "nao-existe"}, 404, "network_profile_not_found"),
            ({"instance_ids": ["android-01"], "vpn_profile_id": proxy["id"]}, 400, "wrong_profile_kind"),
            ({"instance_ids": ["android-01"], "policy": "exigida"}, 409, "policy_without_profile"),
            ({"instance_ids": ["android-01"], "proxy_profile_id": proxy["id"], "policy": "exigida_com_bloqueio"},
             409, "policy_without_vpn"),
        ]
        for corpo, status, codigo in casos:
            r = await c.post("/api/network/assign", json=corpo)
            assert r.status_code == status and r.json()["detail"]["code"] == codigo, (corpo, r.text)
        r = await c.post("/api/network/assign", json={"instance_ids": []})
        assert r.status_code == 422                                     # lista explícita: o parque nunca é inferido
    assert parque.state.db.one("SELECT * FROM device_network") is None  # type: ignore[union-attr]


# ==================================================================== estado só avança com evidência
async def test_estado_so_avanca_com_evidencia_e_medicao_por_app(parque: Harness) -> None:
    st = parque.state
    assert st is not None
    async with _cliente(parque) as c:
        vpn = await _perfil(c, "Casa")
        await c.post("/api/network/assign", json={"instance_ids": ["android-01"], "vpn_profile_id": vpn["id"],
                                                  "policy": "exigida"})
    assert pendencias(st) == [{"instance_id": "android-01", "falta": "aplicar", "desired_rev": 1,
                               "applied_rev": None, "state": "pendente"}]

    # Sem evidência, nada muda; `trafego_verificado` nunca entra por observação.
    with pytest.raises(ValueError):
        registrar_observacao(st, "android-01", rev=1, estado="configurado", evidencia="  ")
    with pytest.raises(ValueError):
        registrar_observacao(st, "android-01", rev=1, estado="trafego_verificado", evidencia="confia")
    with pytest.raises(RedeError):
        registrar_observacao(st, "android-02", rev=1, estado="configurado", evidencia="x")   # nada pedido ali

    # Medir antes de aplicar: fica no histórico, o estado não sai de `pendente`.
    ok = {"com.instagram.android": "ok", "com.microsoft.office.outlook": "ok"}
    _, dto = registrar_medicao(st, "android-01", NetworkMeasurementInput(
        method="app_qa", egress_ipv4="45.162.8.7", per_app=ok), rev=1)
    assert dto is not None and dto.state == "pendente" and dto.egress_ipv4 == "45.162.8.7"

    dto = registrar_observacao(st, "android-01", rev=1, estado="configurado",
                               evidencia="perfil relido do cliente VPN")
    assert dto.state == "configurado" and dto.applied_rev == 1
    assert pendencias(st)[0]["falta"] == "verificar"
    dto = registrar_observacao(st, "android-01", rev=1, estado="conectado", evidencia="rede VPN no dumpsys")
    assert dto.state == "conectado"

    # Só o navegador medido: não prova os outros apps → parcial.
    _, dto = registrar_medicao(st, "android-01", NetworkMeasurementInput(
        method="app_qa", egress_ipv4="45.162.8.7", per_app={"com.instagram.android": "fora_da_rede"}), rev=1)
    assert dto is not None and dto.state == "parcial" and "fora_da_rede" in (dto.detail or "")
    # Sem IP de saída: o estado não sai do lugar.
    _, dto = registrar_medicao(st, "android-01", NetworkMeasurementInput(method="app_qa", per_app=ok), rev=1)
    assert dto is not None and dto.state == "parcial"
    # Tudo medido e ok: tráfego verificado, com a saída medida no aparelho.
    mid, dto = registrar_medicao(st, "android-01", NetworkMeasurementInput(
        method="app_qa", egress_ipv4="45.162.8.9", egress_ipv6="2804:14c::9", udp_ok=True, per_app=ok), rev=1)
    assert dto is not None and dto.state == "trafego_verificado" and dto.egress_ipv4 == "45.162.8.9"
    assert f"#{mid}" in (dto.detail or "") and pendencias(st) == []

    # Trocar só `exigida` → `livre` não desfaz a verificação (não muda o aparelho) ...
    async with _cliente(parque) as c:
        await c.post("/api/network/assign", json={"instance_ids": ["android-01"], "policy": "livre"})
        assert _linha(parque, "android-01")["state"] == "trafego_verificado"  # type: ignore[index]
        # ... e pedir o bloqueio muda: revisão nova, volta a pendente.
        await c.post("/api/network/assign", json={"instance_ids": ["android-01"],
                                                  "policy": "exigida_com_bloqueio"})
    linha = _linha(parque, "android-01")
    assert linha is not None and linha["desired_rev"] == 2 and linha["state"] == "pendente"

    # A observação da revisão velha chega depois: descartada, o estado fica.
    dto = registrar_observacao(st, "android-01", rev=1, estado="conectado", evidencia="atrasada")
    assert dto.state == "pendente" and "descartada" in (dto.detail or "")
    registrar_observacao(st, "android-01", rev=2, estado="conectado", evidencia="rede VPN no dumpsys")
    # Com bloqueio, sem provar o vazamento barrado: parcial. Provado: verificado.
    _, dto = registrar_medicao(st, "android-01", NetworkMeasurementInput(
        method="app_qa", egress_ipv4="45.162.8.9", per_app=ok), rev=2)
    assert dto is not None and dto.state == "parcial" and "bloqueio" in (dto.detail or "")
    # Uma medição que DIZ "bloqueado" não é prova: a prova é a da linha (o teste com o cliente VPN parado, item 29.2).
    _, dto = registrar_medicao(st, "android-01", NetworkMeasurementInput(
        method="app_qa", egress_ipv4="45.162.8.9", per_app=ok, leak_blocked=True), rev=2)
    assert dto is not None and dto.state == "parcial" and "bloqueio" in (dto.detail or "")
    cliente = "1.14.2 (739) /data/app/~~a==/io.nekohasekai.sfa-b=="
    assert rede.marcar_ensaio_de_vazamento(st, "android-01", rev=2, cliente=cliente)
    assert rede.gravar_prova_de_vazamento(st, "android-01", rev=2, cliente=cliente, resultado=True,
                                                 quando="2026-09-30T12:00:00Z", detalhe="Permission denied")
    _, dto = registrar_medicao(st, "android-01", NetworkMeasurementInput(
        method="app_qa", egress_ipv4="45.162.8.9", per_app=ok, leak_blocked=True), rev=2)
    assert dto is not None and dto.state == "trafego_verificado"
    assert (dto.leak_rev, dto.leak_result, dto.leak_pending, dto.leak_client) == (2, True, False, cliente)

    # Regressão (falha, deriva) vale sempre, com o erro.
    dto = registrar_observacao(st, "android-01", rev=2, estado="pendente", evidencia="VPN caiu", erro="sem túnel")
    assert dto.state == "pendente" and dto.error == "sem túnel"
    assert pendencias(st)[0]["falta"] == "aplicar"                 # regrediu: reaplicar, não só reconferir
    assert st.db.one("SELECT COUNT(*) AS n FROM network_measurements WHERE instance_id='android-01'")["n"] == 7


def test_medicao_recusa_ip_trocado_e_resultado_fora_do_vocabulario() -> None:
    with pytest.raises(ValueError):
        NetworkMeasurementInput(method="app_qa", egress_ipv4="2804:14c::1")
    with pytest.raises(ValueError):
        NetworkMeasurementInput(method="app_qa", egress_ipv6="45.162.8.1")
    with pytest.raises(ValueError):
        NetworkMeasurementInput(method="app_qa", per_app={"com.instagram.android": "talvez"})


def test_medicao_recusa_endereco_que_nao_e_a_saida_publica() -> None:
    """NAT do emulador, interface do túnel, loopback, CGNAT, link-local, ULA e documentação não são a saída
    observada de fora (ADR-056 §1): a medição que os devolve não mediu a saída."""
    for v4 in ("10.0.2.15", "10.0.0.2", "127.0.0.1", "192.168.1.19", "100.64.0.1", "169.254.1.1", "203.0.113.7"):
        with pytest.raises(ValueError, match="público"):
            NetworkMeasurementInput(method="app_qa", egress_ipv4=v4)
    for v6 in ("::1", "fe80::1", "fd00::2", "2001:db8::9"):
        with pytest.raises(ValueError, match="público"):
            NetworkMeasurementInput(method="app_qa", egress_ipv6=v6)
    # A forma canônica é a que entra no histórico e no aparelho.
    assert NetworkMeasurementInput(method="app_qa", egress_ipv6="2804:014c:0::9").egress_ipv6 == "2804:14c::9"


async def test_medicao_so_verifica_com_os_apps_das_contas_do_aparelho(parque: Harness) -> None:
    """O navegador sozinho não prova o Instagram: com conta vinculada, cada app dela tem de estar medido `ok`."""
    st = parque.state
    assert st is not None
    st.social.create_profile(ProfileCreate(username="lucas.teste", instance_id="android-01"))
    exigidos = rede.apps_exigidos(st, "android-01")
    assert "com.instagram.android" in exigidos and rede.apps_exigidos(st, "android-02") == []
    async with _cliente(parque) as c:
        vpn = await _perfil(c, "Casa")
        r = await c.post("/api/network/assign", json={"instance_ids": ["android-01"], "vpn_profile_id": vpn["id"],
                                                      "confirm_real_account": ["android-01"]})
        assert r.status_code == 200, r.text
        aparelhos = {d["instance_id"]: d for d in (await c.get("/api/network/devices")).json()["devices"]}
    assert aparelhos["android-01"]["required_apps"] == exigidos
    registrar_observacao(st, "android-01", rev=1, estado="conectado", evidencia="rede VPN no dumpsys")

    _, dto = registrar_medicao(st, "android-01", NetworkMeasurementInput(
        method="navegador", egress_ipv4="45.162.8.9", per_app={"com.android.chrome": "ok"}), rev=1)
    assert dto is not None and dto.state == "parcial"
    assert "com.instagram.android" in (dto.detail or "") and "não medidos" in (dto.detail or "")

    tudo = {**{app: "ok" for app in exigidos}, "com.android.chrome": "ok"}
    _, dto = registrar_medicao(st, "android-01", NetworkMeasurementInput(
        method="app_qa", egress_ipv4="45.162.8.9", per_app=tudo), rev=1)       # type: ignore[arg-type]
    assert dto is not None and dto.state == "trafego_verificado"


async def test_reaplicacao_no_meio_da_medicao_nao_vira_verificado(parque: Harness,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """A revisão pedida muda entre a leitura e a gravação (uma reaplicação concorrente): a medição fica só no
    histórico, e o aparelho segue `pendente` com a revisão nova — nunca `trafego_verificado` com
    `applied_rev < desired_rev`."""
    st = parque.state
    assert st is not None
    async with _cliente(parque) as c:
        vpn = await _perfil(c, "Casa")
        await c.post("/api/network/assign", json={"instance_ids": ["android-01"], "vpn_profile_id": vpn["id"],
                                                  "policy": "exigida"})
    registrar_observacao(st, "android-01", rev=1, estado="conectado", evidencia="rede VPN no dumpsys")

    original = rede._falta_para_verificar

    def com_reaplicacao_no_meio(*args: Any, **kw: Any) -> list[str]:
        rede.pedir_reaplicacao(st, "android-01", "outra aba")
        return original(*args, **kw)

    monkeypatch.setattr(rede, "_falta_para_verificar", com_reaplicacao_no_meio)
    _, dto = registrar_medicao(st, "android-01", NetworkMeasurementInput(
        method="app_qa", egress_ipv4="45.162.8.9", per_app={"com.instagram.android": "ok"}), rev=1)
    assert dto is not None and dto.state == "pendente"
    assert dto.desired_rev == 2 and dto.applied_rev == 1 and "só no histórico" in (dto.detail or "")
    assert pendencias(st)[0]["falta"] == "aplicar"


async def test_observacao_da_revisao_que_mudou_no_meio_e_descartada(parque: Harness,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Mesmo desenho do lado da observação: `applied_rev` não é gravado sobre um pedido que já mudou."""
    st = parque.state
    assert st is not None
    async with _cliente(parque) as c:
        vpn = await _perfil(c, "Casa")
        await c.post("/api/network/assign", json={"instance_ids": ["android-01"], "vpn_profile_id": vpn["id"]})

    original, uma_vez = rede.now_iso, [True]

    def relogio_com_reaplicacao() -> str:
        if uma_vez:
            uma_vez.clear()
            rede.pedir_reaplicacao(st, "android-01", "outra aba")
        return original()

    monkeypatch.setattr(rede, "now_iso", relogio_com_reaplicacao)
    dto = registrar_observacao(st, "android-01", rev=1, estado="conectado", evidencia="rede VPN no dumpsys")
    assert dto.state == "pendente" and dto.applied_rev is None and dto.desired_rev == 2
    assert "descartada" in (dto.detail or "")


# ==================================================================== verify / reapply: pedido, não execução
async def test_verify_e_reapply_registram_o_pedido_sem_fingir_aplicacao(parque: Harness) -> None:
    st = parque.state
    assert st is not None
    st.social_repo.marcar_conta_travada("android-02", "felipe.teste", "tela de desafio", "declarado",
                                        visto_por="dono")
    async with _cliente(parque) as c:
        vpn = await _perfil(c, "Casa")
        r = await c.post("/api/network/devices/android-01/verify")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "nothing_requested"
        assert (await c.post("/api/network/devices/android-99/reapply")).status_code == 404
        r = await c.post(f"/api/network/devices/{LOJA}/verify")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "store_instance"
        r = await c.post("/api/network/devices/android-02/reapply")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "aparelho_em_quarentena"

        await c.post("/api/network/assign", json={"instance_ids": ["android-01"], "vpn_profile_id": vpn["id"]})
        registrar_observacao(st, "android-01", rev=1, estado="conectado", evidencia="rede VPN no dumpsys")
        registrar_medicao(st, "android-01", NetworkMeasurementInput(
            method="app_qa", egress_ipv4="45.162.8.9", per_app={"com.instagram.android": "ok"}), rev=1)
        assert _linha(parque, "android-01")["state"] == "trafego_verificado"  # type: ignore[index]

        # Verificar não é evidência: o estado fica, o pedido fica no detail e no evento, nada é executado.
        r = await c.post("/api/network/devices/android-01/verify")
        assert r.status_code == 202, r.text
        corpo = r.json()
        assert corpo["accepted"] is True and corpo["executed"] is False and corpo["state"] == "trafego_verificado"
        detalhe = _linha(parque, "android-01")["detail"] or ""               # type: ignore[index]
        assert "verificação pedida" in detalhe and "saída e apps provados" in detalhe   # a evidência não some

        # Reaplicar é revisão nova: volta a pendente, e a convergência do 25.4 acha a pendência.
        r = await c.post("/api/network/devices/android-01/reapply")
        assert r.status_code == 202
        corpo = r.json()
        assert corpo["executed"] is False and corpo["desired_rev"] == 2 and corpo["applied_rev"] == 1
        assert corpo["state"] == "pendente" and corpo["pending"] == "aplicar"
    assert [p["instance_id"] for p in pendencias(st)] == ["android-01"]
    # Nenhum comando foi aberto: o 202 é "pedido registrado", e ninguém finge ter mexido no aparelho.
    assert st.db.one("SELECT id FROM commands WHERE verb='device.network'") is None
    acoes = [json.loads(e["data"])["acao"] for e in st.db.query(
        "SELECT data FROM events WHERE kind='network.updated' AND instance_id='android-01' ORDER BY id")]
    assert "verify" in acoes and "reapply" in acoes


# ==================================================================== visão por aparelho e legado da 041
async def test_visao_por_aparelho_com_legado_rebaixado_e_pendencias(parque: Harness) -> None:
    st = parque.state
    assert st is not None
    st.social.create_profile(ProfileCreate(username="lucas.teste", instance_id="android-01"))
    st.social_repo.marcar_conta_travada("android-02", "felipe.teste", "tela de desafio", "declarado",
                                        visto_por="dono")
    agora = now_iso()
    st.db.execute("INSERT INTO proxy_profiles(id, name, host, port, created_at) VALUES ('px','Lab','10.0.0.5',3128,?)",
                  (agora,))
    # O proxy global da 041, lido de volta do aparelho: prova a configuração, não o tráfego.
    st.db.execute("INSERT INTO device_proxy_state(instance_id, desired_proxy_id, observed_value, state, verified_at,"
                  " updated_at) VALUES ('android-03','px','10.0.0.5:3128','applied',?,?)", (agora, agora))
    st.db.execute("INSERT INTO device_proxy_state(instance_id, desired_proxy_id, state, updated_at)"
                  " VALUES ('android-02','px','failed',?)", (agora,))
    async with _cliente(parque) as c:
        vpn = await _perfil(c, "Casa")
        r = await c.post("/api/network/assign", json={"instance_ids": ["android-03"], "vpn_profile_id": vpn["id"],
                                                      "dry_run": True})
        assert any("proxy global legado Lab" in w for w in r.json()["devices"][0]["warnings"])

        r = await c.get("/api/network/devices")
        assert r.status_code == 200
        aparelhos = {d["instance_id"]: d for d in r.json()["devices"]}
    assert LOJA not in aparelhos                                        # a loja não é destino
    a3 = aparelhos["android-03"]
    assert a3["network"] is None and a3["effective_state"] == "configurado"      # nunca além disso
    assert a3["legacy_proxy"]["value"] == "10.0.0.5:3128" and a3["pending"] is None
    assert aparelhos["android-02"]["effective_state"] == "pendente"
    assert aparelhos["android-02"]["restriction"] and "quarentena" in aparelhos["android-02"]["restriction"]
    assert aparelhos["android-01"]["real_account"] == "@lucas.teste"
    assert aparelhos["android-01"]["effective_state"] is None and aparelhos["android-01"]["last_measurement"] is None

    # Com rede pedida, a linha nova manda, e a última medição aparece (a pendência some da quarentena).
    rede.atribuir(st, rede.NetworkAssignBody(instance_ids=["android-03"], vpn_profile_id=vpn["id"]), "teste")
    registrar_medicao(st, "android-03", NetworkMeasurementInput(method="app_qa", egress_ipv4="45.162.8.4"),
                      rev=None)
    visao = {d["instance_id"]: d for d in rede.listar_aparelhos(st)["devices"]}
    assert visao["android-03"]["network"]["state"] == "pendente"
    assert visao["android-03"]["effective_state"] == "pendente" and visao["android-03"]["pending"] == "aplicar"
    assert visao["android-03"]["last_measurement"]["egress_ipv4"] == "45.162.8.4"
    # A medição de base não mexeu no estado.
    assert _linha(parque, "android-03")["state"] == "pendente"              # type: ignore[index]
