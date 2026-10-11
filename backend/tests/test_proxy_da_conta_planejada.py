"""31.337: o proxy sticky da conta PLANEJADA (`igfarm-<conta>`) nasce no planejamento, e o egresso é medido na janela do cadastro
ANTES do primeiro toque.

- `POST /api/instagram/profiles/{id}/accounts/{aid}/proxy` cria (idempotente) o perfil e o atribui; o igfarm só entrega a URL;
- sem `egress_esperado`, a primeira medição com IPv4 público dentro da janela vira o esperado (o IP de criação), com rastro;
- com `egress_esperado`, vale a regra do login (31.329); se não casar (ou a medição não obtiver IP), o cadastro nem começa:
  nada é tocado e a parada é `egresso_nao_casou`;
- sem o proxy desta conta no aparelho, o cadastro segue como antes.

Nível de prova: `simulated` (sonda de IP falsa, app e aparelho falsos do `test_cadastro_guiado`). Nenhum proxy real.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.devices import rede_convergencia

from .conftest import Harness
from .test_cadastro_guiado import CODIGO, _api, _cenario, _iniciar, _limpar_cache  # noqa: F401  (a fixture autouse limpa o cache)

IP = "58.225.220.174"
URL = "http://usuario-do-proxy:senha-do-proxy@proxy.exemplo.test:8080"


def _perfil(h: Harness, aid: str, *, esperado: str | None = None, ligar: bool = True) -> str:
    db = h.state.db
    params = '{"username": "usuario-do-proxy"%s}' % (f', "egress_esperado": "{esperado}"' if esperado else "")
    db.execute("INSERT INTO network_profiles(id, name, kind, protocol, endpoint_host, endpoint_port, params, created_at) "
               "VALUES (?,?,?,?,?,?,?,?)", (f"np-{aid}", f"igfarm-{aid}", "proxy", "http", "proxy.exemplo.test", 8080, params,
                                            "2026-01-01"))
    if ligar:
        db.execute("INSERT INTO device_network(instance_id, policy, desired_rev, applied_rev, state, proxy_profile_id, updated_at)"
                   " VALUES ('android-01','exigida',1,1,'trafego_verificado',?, '2026-01-01')", (f"np-{aid}",))
    h.state.rede_convergencia._aparelho = lambda st, rt: None
    return f"np-{aid}"


def _saida(monkeypatch: pytest.MonkeyPatch, ipv4: str | None) -> None:
    async def medir(ap: Any, cfg: Any) -> tuple[str | None, str | None, str]:
        return ipv4, None, "IPv4 via teste" if ipv4 else "sem saída (tempo esgotado)"
    monkeypatch.setattr(rede_convergencia, "medir_saida", medir)


def _params(h: Harness, perfil: str) -> dict[str, object]:
    from app.db import loads
    return dict(loads(h.state.db.scalar("SELECT params FROM network_profiles WHERE id=?", (perfil,)), {}) or {})


# ------------------------------------------------------------------ a rota que planeja o proxy
async def test_a_rota_cria_o_perfil_sem_esperado_e_e_idempotente(harness: Harness, tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    async with _api(harness) as c:
        r = await c.post(f"/api/instagram/profiles/{cen.pid}/accounts/{cen.aid}/proxy", json={"proxy_url": URL})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["network_profile_id"] and d["egress_esperado"] is None and d["account_id"] == cen.aid
        assert "senha-do-proxy" not in r.text and "usuario-do-proxy" not in r.text
        linha = harness.state.db.one("SELECT * FROM network_profiles WHERE id=?", (d["network_profile_id"],))
        assert linha["name"] == f"igfarm-{cen.aid}" and "egress_esperado" not in _params(harness, d["network_profile_id"])
        r2 = await c.post(f"/api/instagram/profiles/{cen.pid}/accounts/{cen.aid}/proxy", json={"proxy_url": URL})
        assert r2.status_code == 200 and r2.json()["network_profile_id"] == d["network_profile_id"]
    assert harness.state.db.scalar("SELECT COUNT(*) FROM network_profiles WHERE name=?", (f"igfarm-{cen.aid}",)) == 1
    tudo = "\n".join(str(dict(x)) for t in ("events", "network_profiles") for x in harness.state.db.query(f"SELECT * FROM {t}"))  # noqa: S608
    assert "senha-do-proxy" not in tudo


async def test_a_rota_recusa_proxy_invalido_conta_inexistente_e_conta_ja_cadastrada(harness: Harness, tmp_path: Path,
                                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    async with _api(harness) as c:
        ruim = await c.post(f"/api/instagram/profiles/{cen.pid}/accounts/{cen.aid}/proxy", json={"proxy_url": "isto não é proxy"})
        assert ruim.status_code == 422, ruim.text
        assert (await c.post(f"/api/instagram/profiles/{cen.pid}/accounts/nao-existe/proxy",
                             json={"proxy_url": URL})).status_code == 404
        harness.state.db.execute("UPDATE profile_accounts SET provisioning_state='confirmada' WHERE id=?", (cen.aid,))
        tarde = await c.post(f"/api/instagram/profiles/{cen.pid}/accounts/{cen.aid}/proxy", json={"proxy_url": URL})
        assert tarde.status_code == 409 and tarde.json()["detail"]["code"] == "estado_inesperado"


# ------------------------------------------------------------------ a medição na janela do cadastro
async def test_primeira_medicao_publica_vira_o_esperado_com_rastro(harness: Harness, tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    perfil = _perfil(harness, cen.aid)
    _saida(monkeypatch, IP)
    rt = _rt()
    j = await harness.state.rede_convergencia.medir_para_o_cadastro(rt, cen.aid)
    assert j is not None and j.casou and j.esperado == IP and j.medido == IP and "primeira medição" in j.detalhe
    assert _params(harness, perfil)["egress_esperado"] == IP
    ev = harness.state.db.one("SELECT data FROM events WHERE kind='network.updated' ORDER BY id DESC LIMIT 1")
    assert ev is not None and IP in ev["data"]
    # a medição seguinte compara com o esperado fixado: outro IP não casa
    _saida(monkeypatch, "131.161.219.173")
    assert not (await harness.state.rede_convergencia.medir_para_o_cadastro(rt, cen.aid)).casou


async def test_sem_ip_ou_ip_nao_publico_nao_fixa_nem_casa(harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    perfil = _perfil(harness, cen.aid)
    for ipv4 in (None, "10.0.0.5"):
        _saida(monkeypatch, ipv4)
        j = await harness.state.rede_convergencia.medir_para_o_cadastro(_rt(), cen.aid)
        assert j is not None and not j.casou
        assert "egress_esperado" not in _params(harness, perfil)


async def test_proxy_de_outra_conta_ou_sem_proxy_nao_mede(harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)

    async def nunca(ap: Any, cfg: Any) -> tuple[str | None, str | None, str]:
        raise AssertionError("não devia medir")
    monkeypatch.setattr(rede_convergencia, "medir_saida", nunca)
    _perfil(harness, "outra-conta")
    assert await harness.state.rede_convergencia.medir_para_o_cadastro(_rt(), cen.aid) is None
    harness.state.db.execute("UPDATE device_network SET proxy_profile_id=NULL WHERE instance_id='android-01'")
    assert await harness.state.rede_convergencia.medir_para_o_cadastro(_rt(), cen.aid) is None


# ------------------------------------------------------------------ o cadastro guiado
async def test_egresso_que_nao_casa_impede_o_cadastro_antes_do_primeiro_toque(harness: Harness, tmp_path: Path,
                                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    _perfil(harness, cen.aid, esperado=IP)
    _saida(monkeypatch, "131.161.219.173")
    _iniciar(cen)
    erro = await cen.rodar()
    assert erro is not None and str(erro) == "cadastro guiado parado: egresso_nao_casou"
    assert cen.app.envios == 0 and not cen.app.toques and not cen.app.digitados, "nada foi tocado nem digitado"
    assert cen.estado() == "credencial_preparada", "o cadastro nem começou"
    ev = harness.state.db.one("SELECT message, data FROM events WHERE kind='session.egresso_na_janela'")
    assert ev is not None and "cadastro" in ev["message"] and '"fase":"cadastro"' in ev["data"]


async def test_egresso_que_casa_deixa_o_cadastro_seguir(harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    _perfil(harness, cen.aid, esperado=IP)
    _saida(monkeypatch, IP)
    cen.caixa.chegou(CODIGO, ha_s=-1)
    _iniciar(cen)
    assert await cen.rodar() is None
    assert cen.estado() == "confirmada"


async def test_sem_o_proxy_planejado_o_cadastro_segue_como_antes(harness: Harness, tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    cen.caixa.chegou(CODIGO, ha_s=-1)
    _iniciar(cen)
    assert await cen.rodar() is None
    assert harness.state.db.scalar("SELECT COUNT(*) FROM events WHERE kind='session.egresso_na_janela'") == 0


def _rt() -> Any:
    from types import SimpleNamespace
    return SimpleNamespace(id="android-01")
