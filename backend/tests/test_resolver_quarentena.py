"""Resolver a quarentena por rota e aviso sem o @ de conta retirada (item 29.24, ADR-055 e ADR-068).

Em 02/10/2026 o android-04 seguia em quarentena depois de a conta bloqueada ser retirada (29.23) e de o app ser limpo
com `pm clear`: o marcador só saía no reset do disco. A rota `POST /api/instances/{id}/locked-account/resolve` é o
gesto explícito da pessoa — só banco, nunca dispara sozinha. E o aviso de quarentena não pode trazer de volta o @ de
uma conta já retirada (29.23, opção A do dono); o de conta VIVA continua com o @.

Prova: `simulated` (harness com aparelho falso).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from app.commands import despacho
from app.main import create_app
from app.models import ProfileCreate
from app.social.contas_nossas import MARCADOR, foi_retirada, registrar_lapide

from .conftest import Harness

RETIRADA = "conta retirada (bloqueada)"
GILBERTO = "gilberto.vasconcelos517"
VIVA = "tadeu.quintela4821"
URL = "/api/instances/android-02/locked-account/resolve"


@pytest_asyncio.fixture
async def h(tmp_path: Path) -> AsyncIterator[Harness]:
    harness = Harness(tmp_path, 2)
    await harness.boot()
    try:
        yield harness
    finally:
        if harness.state is not None:
            await harness.state.stop()


async def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _marcar(h: Harness, handle: str) -> None:
    assert h.state is not None
    assert h.state.social_repo.marcar_conta_travada("android-02", handle, "tela de desafio", "declarado",
                                                    visto_por="dono") is True


def _retirar(h: Harness, handle: str) -> None:
    """A conta saiu da plataforma (29.23): só a lápide com o hash fica."""
    assert h.state is not None
    assert registrar_lapide(h.state.db, app_id="instagram", handle=handle, profile_id=None) is True


def _eventos(h: Harness, kind: str) -> list[dict[str, object]]:
    assert h.state is not None
    return [{**json.loads(e["data"]), "message": e["message"]}
            for e in h.state.db.query("SELECT data, message FROM events WHERE kind=? ORDER BY id", (kind,))]


async def test_rota_resolve_o_marcador_sincroniza_o_rotulo_e_emite_a_saida(h: Harness) -> None:
    s = h.state
    assert s is not None
    _marcar(h, GILBERTO)
    antes = s.db.scalar("SELECT COUNT(*) FROM commands")
    rotulo = s.db.one("SELECT account_label, account_label_origin FROM instances WHERE id='android-02'")
    assert (rotulo["account_label"], rotulo["account_label_origin"]) == (GILBERTO, "marcador")
    async with await _cliente(h) as c:
        r = await c.post(URL, json={"nota": "app limpo com pm clear; conta retirada (29.23)"})
    assert r.status_code == 200, r.text
    assert r.json() == {"instance_id": "android-02", "resolvidos": 1}
    assert s.social_repo.conta_travada_no_aparelho("android-02") is None
    linha = s.db.one("SELECT resolved_by, resolution FROM device_locked_accounts WHERE instance_id='android-02'")
    assert linha["resolved_by"] and "pm clear" in linha["resolution"]
    rotulo = s.db.one("SELECT account_label, account_label_origin FROM instances WHERE id='android-02'")
    assert rotulo["account_label_origin"] != "marcador" and rotulo["account_label"] != GILBERTO
    assert [e["acao"] for e in _eventos(h, "device.locked_account")] == ["marcado", "resolvido"]
    # Só banco: nenhum comando chegou ao aparelho (nem disco, nem app).
    assert s.db.scalar("SELECT COUNT(*) FROM commands") == antes


async def test_nota_e_obrigatoria_e_o_marcador_continua_aberto(h: Harness) -> None:
    s = h.state
    assert s is not None
    _marcar(h, GILBERTO)
    async with await _cliente(h) as c:
        assert (await c.post(URL, json={})).status_code == 422
        assert (await c.post(URL, json={"nota": ""})).status_code == 422
        assert (await c.post(URL, json={"nota": "   "})).status_code == 422
        assert (await c.post(URL, json={"nota": "x" * 501})).status_code == 422
    assert s.social_repo.conta_travada_no_aparelho("android-02") is not None


async def test_sem_marcador_aberto_e_404_com_codigo_e_aparelho_inexistente_tambem(h: Harness) -> None:
    async with await _cliente(h) as c:
        r = await c.post(URL, json={"nota": "nada a resolver"})
        assert r.status_code == 404 and r.json()["detail"]["code"] == "no_locked_account"
        r = await c.post("/api/instances/nao-existe/locked-account/resolve", json={"nota": "x"})
        assert r.status_code == 404


async def test_resolver_nao_reativa_o_perfil_nem_resolve_duas_vezes(h: Harness) -> None:
    async with await _cliente(h) as c:
        _marcar(h, GILBERTO)
        assert (await c.post(URL, json={"nota": "primeira"})).status_code == 200
        r = await c.post(URL, json={"nota": "segunda"})
        assert r.status_code == 404 and r.json()["detail"]["code"] == "no_locked_account"


async def test_aviso_de_conta_retirada_sai_sem_o_arroba_e_o_de_conta_viva_com_ele(h: Harness) -> None:
    s = h.state
    assert s is not None
    _marcar(h, GILBERTO)
    _retirar(h, GILBERTO)
    assert foi_retirada(s.db, GILBERTO) and not foi_retirada(s.db, VIVA)
    # A frase da quarentena (409 do painel, porta do despacho, histórico do comando).
    frase = s.quarentena("android-02")
    assert frase is not None and RETIRADA in frase and GILBERTO not in frase
    # Recusa de start: o motivo gravado no histórico do comando.
    assert despacho.pedir_ciclo_de_vida(s, "android-02", "restart", "teste", requested_by="saude") is None
    motivo = s.db.one("SELECT reason FROM commands WHERE instance_id='android-02' ORDER BY created_at DESC, id DESC"
                      " LIMIT 1")["reason"]
    assert RETIRADA in motivo and GILBERTO not in motivo
    # Start confirmado pela pessoa: o aviso do log não traz o @ da conta retirada.
    async with await _cliente(h) as c:
        r = await c.post("/api/instances/android-02/actions/home",
                         json={"idempotency_key": "r-29-24-a", "confirm_locked_account": True})
        assert r.status_code == 202, r.text
        r = await c.post("/api/instances/android-02/actions/start", json={"idempotency_key": "r-29-24-b"})
        assert r.status_code == 409 and GILBERTO not in r.text and RETIRADA in r.text, r.text
    avisos = [e["message"] for e in s.db.query("SELECT message FROM events WHERE instance_id='android-02'"
                                                " AND level='warn' ORDER BY id")]
    confirmado = [m for m in avisos if "confirmado explicitamente" in m]
    assert confirmado and all(RETIRADA in m and GILBERTO not in m for m in confirmado), avisos
    # A saída da quarentena (evento NOVO, depois da retirada) também não leva o @, nem no texto nem nos dados.
    s.social_repo.resolver_conta_travada("android-02", por="dono", nota="app limpo")
    saida = _eventos(h, "device.locked_account")[-1]
    assert saida["acao"] == "resolvido" and RETIRADA in str(saida["message"])
    assert GILBERTO not in json.dumps(saida) and saida["handle"] == MARCADOR


async def test_aviso_de_conta_viva_continua_com_o_arroba(h: Harness) -> None:
    s = h.state
    assert s is not None
    _marcar(h, VIVA)
    frase = s.quarentena("android-02")
    assert frase is not None and f"@{VIVA}" in frase and RETIRADA not in frase
    async with await _cliente(h) as c:
        r = await c.post("/api/instances/android-02/actions/home",
                         json={"idempotency_key": "r-29-24-c", "confirm_locked_account": True})
        assert r.status_code == 202, r.text
    confirmado = [e["message"] for e in s.db.query("SELECT message FROM events WHERE instance_id='android-02'"
                                                    " AND level='warn' ORDER BY id") if "confirmado" in e["message"]]
    assert confirmado and all(f"@{VIVA}" in m for m in confirmado)
    anuncio = _eventos(h, "device.locked_account")[0]
    assert f"@{VIVA}" in str(anuncio["message"]) and anuncio["handle"] == VIVA


async def test_retirada_tira_o_arroba_do_aparelho_e_da_saude_e_a_quarentena_segue_resolvivel(h: Harness) -> None:
    """B1 da validação (29.24): o @ da conta retirada não fica no cartão, no rótulo, no marcador nem no /health."""
    s = h.state
    assert s is not None
    s.db.execute("UPDATE instances SET app_id='instagram' WHERE id='android-02'")
    s.social.create_profile(ProfileCreate(username=GILBERTO))
    _marcar(h, GILBERTO)                       # origem `declarado`: retira a conta (29.23) e mascara (29.24)
    assert foi_retirada(s.db, GILBERTO)
    marcador = s.social_repo.conta_travada_no_aparelho("android-02")
    assert marcador is not None and marcador["handle"] == MARCADOR                  # a quarentena segue ABERTA
    rotulo = s.db.one("SELECT account_label, account_label_origin FROM instances WHERE id='android-02'")
    assert (rotulo["account_label"], rotulo["account_label_origin"]) == (MARCADOR, "marcador")
    async with await _cliente(h) as c:
        for url in ("/api/instances", "/api/health"):
            r = await c.get(url)
            assert r.status_code == 200 and GILBERTO not in r.text, url
        saude = (await c.get("/api/health")).json()
        assert any(p["code"] == "locked_account_on_device" and "retirada" in p["message"] for p in saude["problems"])
        r = await c.post(URL, json={"nota": "app limpo"})                            # resolve com o handle mascarado
        assert r.status_code == 200 and r.json()["resolvidos"] == 1, r.text
    assert s.social_repo.conta_travada_no_aparelho("android-02") is None


async def test_dado_antigo_com_o_arroba_e_mascarado_na_subida_e_o_rotulo_de_configuracao_nao(h: Harness) -> None:
    s = h.state
    assert s is not None
    s.db.execute("UPDATE instances SET app_id='instagram' WHERE id='android-02'")
    _marcar(h, GILBERTO)
    _retirar(h, GILBERTO)                      # a lápide nasce DEPOIS: o marcador e o rótulo ainda têm o @
    assert s.social_repo.conta_travada_no_aparelho("android-02")["handle"] == GILBERTO
    s.db.execute("UPDATE instances SET account_label=?, account_label_origin=NULL WHERE id='android-01'", (GILBERTO,))
    s.social_repo.sincronizar_rotulos()                                              # a varredura da partida
    assert s.social_repo.conta_travada_no_aparelho("android-02")["handle"] == MARCADOR
    assert s.db.one("SELECT account_label FROM instances WHERE id='android-02'")["account_label"] == MARCADOR
    # Configuração (origem nula) não é nossa para mexer: só é contada.
    assert s.db.one("SELECT account_label FROM instances WHERE id='android-01'")["account_label"] == GILBERTO
    assert s.social_repo.mascarar_contas_retiradas() == {"marcadores": 0, "rotulos": 0, "rotulo_de_configuracao": 1}
