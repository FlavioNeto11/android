"""31.341: `GET /api/instagram/contas/{id}/ciclo` também conta a vida da conta PLANEJADA e cadastrada no app, não só a do igfarm.

A fonte não é `contas_igfarm`: é a linha da conta (planejamento e confirmação), as tentativas de login, os eventos do cadastro
guiado (`identity.cadastro`) e a retirada (`profile.account_retired`). Continua sem @ nem e-mail na resposta.

Nível de prova: `simulated` (app e aparelho falsos do `test_cadastro_guiado`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from .conftest import Harness
from .test_cadastro_guiado import CODIGO, _api, _cenario, _iniciar, _limpar_cache  # noqa: F401  (a fixture autouse limpa o cache)


async def _ciclo(h: Harness, aid: str) -> tuple[int, dict[str, object]]:
    async with _api(h) as c:
        r = await c.get(f"/api/instagram/contas/{aid}/ciclo")
        return r.status_code, r.json()


async def test_conta_planejada_ainda_nao_cadastrada_tem_ciclo_pelo_planejamento(harness: Harness, tmp_path: Path,
                                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    status, d = await _ciclo(harness, cen.aid)
    assert status == 200, d
    assert d["origem"] == "app" and d["igfarm_account_id"] is None and d["criada_em"] is None
    assert d["registrada_em"] and d["referencia"] == "planejamento" and d["estado"] == "ativa" and d["contatos"] == []


async def test_cadastro_confirmado_vira_criada_em_e_o_contato_do_cadastro(harness: Harness, tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    cen.caixa.chegou(CODIGO, ha_s=-1)
    _iniciar(cen)
    assert await cen.rodar() is None
    status, d = await _ciclo(harness, cen.aid)
    assert status == 200, d
    assert d["origem"] == "app" and d["referencia"] == "criacao" and d["criada_em"], "a confirmação do cadastro é a criação"
    contatos = d["contatos"]
    assert isinstance(contatos, list) and [c["desfecho"] for c in contatos] == ["confirmada"]
    assert contatos[0]["etapa"] == "cadastro" and d["ultimo_desfecho"] == "confirmada"


async def test_cadastro_parado_aparece_com_o_codigo_fechado(harness: Harness, tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    # sem código na caixa o cadastro para; o código da parada é fechado
    _iniciar(cen)
    erro = await cen.rodar()
    assert erro is not None
    status, d = await _ciclo(harness, cen.aid)
    assert status == 200
    contatos = d["contatos"]
    assert isinstance(contatos, list) and contatos and contatos[-1]["desfecho"] == "parada" and contatos[-1]["detalhe"]
    assert d["criada_em"] is None and d["referencia"] == "planejamento"


def _depois(segundos: int) -> str:
    """Instante relativo a agora: data fixa em teste é bomba-relógio (a ordem dos contatos depende do relógio)."""
    return (datetime.now(timezone.utc) + timedelta(seconds=segundos)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


async def test_conta_retirada_sem_linha_ainda_tem_ciclo_e_a_resposta_nao_traz_arroba_nem_email(
        harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    cen.caixa.chegou(CODIGO, ha_s=-1)
    _iniciar(cen)
    assert await cen.rodar() is None
    harness.state.db.execute("INSERT INTO authentication_attempts(profile_id, instance_id, started_at, finished_at, outcome,"
                             " stage, detail, account_id) VALUES (?,?,?,?,?,?,?,?)",
                             (cen.pid, "android-01", _depois(3600), _depois(3640), "auth_challenge",
                              "classified", "o app exige confirmação em fulano@exemplo.com", cen.aid))
    harness.state.bus.emit("profile.account_retired", "Conta retirada por bloqueio confirmado", level="warn",
                           data={"profile_id": cen.pid, "account_id": cen.aid})
    harness.state.db.execute("DELETE FROM profile_accounts WHERE id=?", (cen.aid,))        # a retirada apaga a linha da conta
    async with _api(harness) as c:
        r = await c.get(f"/api/instagram/contas/{cen.aid}/ciclo")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["estado"] == "retirada" and d["retirada_em"] and d["origem"] == "app"
    assert d["criada_em"], "a confirmação do cadastro ficou no evento"
    assert [c["desfecho"] for c in d["contatos"]] == ["confirmada", "auth_challenge"]
    assert "@" not in r.text and cen.desejado not in r.text and "<e-mail omitido>" in r.text


async def test_conta_sem_nenhum_rastro_e_404(harness: Harness) -> None:
    async with _api(harness) as c:
        assert (await c.get("/api/instagram/contas/acc-que-nunca-existiu/ciclo")).status_code == 404
