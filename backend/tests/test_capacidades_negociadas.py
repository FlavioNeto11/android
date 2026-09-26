"""Negociação de capacidades do worker (adendo v0.20, C7; frente F4, item A5).

Os campos existiam (`Hello.features`, `Welcome.accepted_features`), mas o central nunca preenchia o segundo: todo
agente recebia `[]`, e uma mensagem de tipo novo não tinha a quem perguntar se podia ir. Agora o `welcome` leva a
interseção do que o agente anunciou com o que este central sabe usar, guardada no LINK (é por conexão), e
`WorkerRegistry.aceitou` é a porta de toda mensagem de tipo novo. Agente antigo não anuncia nada e segue o
caminho de antes.
"""
from __future__ import annotations

import json
from pathlib import Path

from starlette.testclient import TestClient

from app.main import create_worker_app
from app.workers.protocol import FEATURE_RESERVA_DE_BOOT
from app.workers.registry import FEATURES_DO_CENTRAL

from .conftest import Harness
from .test_canal_do_worker import PAR_LOCAL
from .test_canal_do_worker import _hello as _hello_bruto
from .test_workers import AgenteFalso, _hello, _registro

WID = "worker-lan-01"


async def test_welcome_leva_so_o_que_o_agente_anunciou_e_o_central_sabe_usar(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    reg.autenticar(_hello(features=[FEATURE_RESERVA_DE_BOOT, "coisa-do-futuro"]), token=None,
                   enrollment=reg.criar_inscricao())
    link = reg.attach(WID, AgenteFalso().send)
    assert link.features_aceitas == {FEATURE_RESERVA_DE_BOOT}
    assert reg.welcome({}, sorted(link.features_aceitas)).accepted_features == [FEATURE_RESERVA_DE_BOOT]
    assert reg.aceitou(WID, FEATURE_RESERVA_DE_BOOT)
    assert not reg.aceitou(WID, "coisa-do-futuro"), "o central aceitou o que não sabe usar"


async def test_agente_antigo_sem_features_nao_tem_nada_aceito(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    link = reg.attach(WID, AgenteFalso().send)
    assert link.features_aceitas == frozenset()
    assert reg.welcome({}, sorted(link.features_aceitas)).accepted_features == []
    assert not reg.aceitou(WID, FEATURE_RESERVA_DE_BOOT)


async def test_negociacao_e_por_conexao(tmp_path: Path) -> None:
    """O agente foi trocado por um antigo entre duas conexões: o que valia para o anterior não vale mais. E sem
    canal não há a quem mandar nada."""
    reg = _registro(tmp_path)
    token = reg.autenticar(_hello(features=[FEATURE_RESERVA_DE_BOOT]), token=None,
                           enrollment=reg.criar_inscricao())
    primeiro = reg.attach(WID, AgenteFalso().send)
    assert reg.aceitou(WID, FEATURE_RESERVA_DE_BOOT)

    reg.autenticar(_hello(), token=token, enrollment=None)
    segundo = reg.attach(WID, AgenteFalso().send)
    assert segundo.features_aceitas == frozenset() and not reg.aceitou(WID, FEATURE_RESERVA_DE_BOOT)
    assert primeiro.features_aceitas == {FEATURE_RESERVA_DE_BOOT}, "o link antigo é histórico, não é reescrito"

    reg.detach(WID, "conexão encerrada", segundo)
    assert not reg.aceitou(WID, FEATURE_RESERVA_DE_BOOT)


def test_welcome_do_canal_de_verdade_leva_a_negociacao(harness: Harness) -> None:
    """Pelo WebSocket do worker, como o agente o vê: o `welcome` é a resposta ao `hello`."""
    assert FEATURE_RESERVA_DE_BOOT in FEATURES_DO_CENTRAL
    app = create_worker_app(harness.state)
    cliente = TestClient(app, client=PAR_LOCAL)
    novo = {**_hello_bruto(), "features": [FEATURE_RESERVA_DE_BOOT, "coisa-do-futuro"]}
    with cliente.websocket_connect("/api/worker/ws", headers={"host": "127.0.0.1:18000"}) as ws:
        ws.send_text(json.dumps({"hello": novo, "enrollment_token": harness.state.workers.criar_inscricao()}))
        bem_vindo = ws.receive_json()
    assert bem_vindo["accepted_features"] == [FEATURE_RESERVA_DE_BOOT]

    # Agente antigo: o `hello` nem tem a chave. Lista vazia, e o agente antigo nem procura por ela.
    with cliente.websocket_connect("/api/worker/ws", headers={"host": "127.0.0.1:18000"}) as ws:
        ws.send_text(json.dumps({"hello": _hello_bruto(), "token": bem_vindo["credential"]}))
        antigo = ws.receive_json()
    assert antigo["accepted_features"] == []
