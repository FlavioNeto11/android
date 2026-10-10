"""O `device.network` incerto fecha quando a linha da rede prova a mesma revisão (reconciliador).

Caso real: quatro comandos `device.network` (`verificar`, rev 1) ficaram `uncertain` entre 30/09 e 07/10 porque o backend
reiniciou no meio, e nada os fechava: o verbo não é de ciclo de vida, então o estado do aparelho (`online`) não os prova.
A rede, sim, é observável: `device_network` guarda a revisão aplicada, o estado e quando o tráfego foi verificado.

A regra é a da sonda, assimétrica: só conclui SUCESSO, e só da MESMA revisão que o comando tratava, com a verificação feita
depois de ele começar. Revisão trocada, estado que não é `trafego_verificado`, verificação anterior ao comando, `desfazer`
e `rev` ausente esperam a pessoa. Tudo `simulated` (harness, nenhum aparelho).
"""
from __future__ import annotations

import json

import pytest

from app.commands.reconciler import reconciliar_incertos, verificar_comando

INICIO = "2026-10-07T22:31:17.082Z"
DEPOIS = "2026-10-10T07:57:19.879Z"
ANTES = "2026-10-07T20:00:00.000Z"


def _comando(harness, *, cid: str = "c-teste-rede-1", acao: str = "verificar", rev: object = 1,
             verbo: str = "device.network", iid: str = "android-03") -> str:
    params = {"acao": acao, "motivo": "varredura"}
    if rev is not None:
        params["rev"] = rev
    harness.state.db.execute(
        "INSERT INTO commands(id, instance_id, verb, params, idempotency_key, state, requested_by, reason, attempt, "
        "created_at, started_at, finished_at) VALUES (?,?,?,?,?, 'uncertain','rede',"
        "'o backend reiniciou durante o comando; resultado desconhecido', 1, ?, ?, ?)",
        (cid, iid, verbo, json.dumps(params), f"k-{cid}", INICIO, INICIO, "2026-10-07T22:32:08.686Z"))
    return cid


def _rede(harness, *, iid: str = "android-03", applied: int | None = 1, desired: int = 1,
          state: str = "trafego_verificado", verificado: str | None = DEPOIS) -> None:
    harness.state.db.execute(
        "INSERT OR REPLACE INTO device_network(instance_id, policy, desired_rev, applied_rev, state, verified_at, "
        "updated_at) VALUES (?, 'livre', ?, ?, ?, ?, '2026-10-10T00:00:00.000Z')",
        (iid, desired, applied, state, verificado))


def _estado(harness, cid: str) -> str:
    return str(harness.state.commands.get(cid)["state"])


def test_fecha_quando_a_mesma_revisao_esta_verificada_depois_do_comando(harness):
    cid = _comando(harness)
    _rede(harness)
    fechados = reconciliar_incertos(harness.state)
    assert [f["id"] for f in fechados] == [cid]
    registro = harness.state.commands.get(cid)
    assert registro["state"] == "succeeded"
    assert "verificado pelo estado real" in registro["reason"] and "revisão 1" in registro["reason"]
    assert DEPOIS in registro["reason"]


@pytest.mark.parametrize("acao", ["aplicar", "conectar", "verificar"])
def test_as_acoes_que_levam_ao_trafego_verificado_fecham(harness, acao):
    cid = _comando(harness, acao=acao)
    _rede(harness)
    assert [f["id"] for f in reconciliar_incertos(harness.state)] == [cid]


def test_revisao_trocada_depois_nao_prova_o_comando_antigo(harness):
    """O android-05: o comando era da rev 1; hoje a linha está na rev 3. Outra configuração não prova nada dele."""
    cid = _comando(harness, rev=1)
    _rede(harness, applied=3, desired=3)
    assert reconciliar_incertos(harness.state) == []
    assert _estado(harness, cid) == "uncertain"


@pytest.mark.parametrize("estado", ["pendente", "configurado", "conectado", "parcial"])
def test_estado_que_nao_e_trafego_verificado_nao_fecha(harness, estado):
    cid = _comando(harness)
    _rede(harness, state=estado)
    assert reconciliar_incertos(harness.state) == []
    assert _estado(harness, cid) == "uncertain"


def test_verificacao_anterior_ao_comando_nao_fecha(harness):
    cid = _comando(harness)
    _rede(harness, verificado=ANTES)
    assert reconciliar_incertos(harness.state) == []
    assert _estado(harness, cid) == "uncertain"


def test_sem_verificacao_registrada_nao_fecha(harness):
    cid = _comando(harness)
    _rede(harness, verificado=None)
    assert reconciliar_incertos(harness.state) == []
    assert _estado(harness, cid) == "uncertain"


def test_sem_linha_de_rede_nao_fecha(harness):
    cid = _comando(harness)
    assert reconciliar_incertos(harness.state) == []
    assert _estado(harness, cid) == "uncertain"


def test_desfazer_e_rev_ausente_esperam_a_pessoa(harness):
    desfazer = _comando(harness, cid="c-teste-rede-d", acao="desfazer")
    sem_rev = _comando(harness, cid="c-teste-rede-s", rev=None)
    _rede(harness)
    assert reconciliar_incertos(harness.state) == []
    assert _estado(harness, desfazer) == "uncertain" and _estado(harness, sem_rev) == "uncertain"


def test_outro_aparelho_nao_prova(harness):
    """A prova é da linha DESTE aparelho: a rede verificada de outro não fecha o comando."""
    cid = _comando(harness, iid="android-03")
    _rede(harness, iid="android-01")
    assert reconciliar_incertos(harness.state) == []
    assert _estado(harness, cid) == "uncertain"


def test_verificar_agora_do_comando_tambem_usa_a_regra(harness):
    cid = _comando(harness)
    _rede(harness)
    novo = verificar_comando(harness.state, harness.state.commands.get(cid))
    assert novo["state"] == "succeeded"


def test_comando_ja_fechado_nao_e_tocado(harness):
    cid = _comando(harness)
    _rede(harness)
    verificar_comando(harness.state, harness.state.commands.get(cid))
    de_novo = verificar_comando(harness.state, harness.state.commands.get(cid))
    assert de_novo["state"] == "succeeded"
