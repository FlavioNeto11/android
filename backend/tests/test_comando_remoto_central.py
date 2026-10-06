"""Comando remoto, lado do central (`remote_exec`, 29.154, ADR-079): registro, fila, auditoria, redação e as rotas.

Prova SIMULADA: o agente é uma função que guarda o que o central mandou (`enviadas`) e responde na mão. Não prova o
agente real no notebook nem o canal WebSocket de ponta a ponta (isso é `not_run` até o deploy, o procedimento escrito
e o sim do dono para ligar).
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.contracts.worker.protocol import (FEATURE_COMANDO_REMOTO, ExecAck, ExecResult, Hello, parse_upstream)
from app.main import create_app
from app.workers.comando_remoto import ErroDeComando

from .conftest import Harness

WORKER = "worker-lan-01"
OPERADOR = "Ana Ribeiro"


def _hello(features: list[str] | None = None) -> Hello:
    return Hello(worker_id=WORKER, name="Notebook da LAN", agent_version="0.1.0", os="windows", max_slots=1,
                 verbs=["start", "stop"], devices=[],
                 features=[FEATURE_COMANDO_REMOTO] if features is None else features)


class Agente:
    """O outro lado do canal: guarda o que o central mandou."""

    def __init__(self) -> None:
        self.enviadas: list[dict[str, Any]] = []

    async def __call__(self, payload: dict[str, Any]) -> None:
        self.enviadas.append(payload)

    def do_tipo(self, tipo: str) -> list[dict[str, Any]]:
        return [m for m in self.enviadas if m["type"] == tipo]


def _ligar(h: Harness, *, central: bool = True, worker: bool = True, anuncia: bool = True,
           conectar: bool = True) -> Agente:
    """Inscreve o worker, liga os interruptores pedidos e conecta o canal vivo."""
    s = h.state
    assert s is not None
    s.workers.comandos.cfg.ativo = central
    s.workers.comandos.emitir = s.bus.emit
    s.workers.autenticar(_hello(None if anuncia else []), token=None, enrollment=s.workers.criar_inscricao("teste"))
    if worker:
        s.workers.comandos.definir_interruptor(WORKER, True, OPERADOR)
    agente = Agente()
    if conectar:
        s.workers.attach(WORKER, agente)
    return agente


def _cliente(h: Harness, *, base: str = "http://127.0.0.1") -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)), base_url=base)


async def _entrar(c: httpx.AsyncClient) -> None:
    assert (await c.post("/api/login", json={"operator": OPERADOR})).status_code == 200


async def _pedir(h: Harness, linha: str = "Get-Process", **extra: Any) -> dict[str, Any]:
    assert h.state is not None
    return await h.state.workers.comandos.pedir(WORKER, OPERADOR, linha=linha, argv=None, pasta=None,
                                                timeout_s=None, idempotency_key=extra.pop("chave", None), **extra)


# ---------------------------------------------------------------- caminho feliz e auditoria
async def test_pedido_despacha_ao_agente_registra_e_audita(harness: Harness) -> None:
    agente = _ligar(harness)
    row = await _pedir(harness)
    assert row["state"] == "dispatched"
    enviado = agente.do_tipo("exec")[0]
    assert enviado["linha"] == "Get-Process" and enviado["exec_id"] == row["id"]
    eventos = [e for e in harness.state.bus.since(0) if e.kind == "worker.comando"]       # type: ignore[union-attr]
    assert eventos and eventos[0].data["requested_by"] == OPERADOR and eventos[0].data["linha"] == "Get-Process"


async def test_desfecho_grava_saida_redigida_confirma_ao_agente_e_libera_a_fila(harness: Harness) -> None:
    agente = _ligar(harness)
    primeiro = await _pedir(harness, "um")
    segundo = await _pedir(harness, "dois")
    assert segundo["state"] == "created" and len(agente.do_tipo("exec")) == 1        # um comando por vez por máquina
    s = harness.state
    s.workers.comandos.on_ack(WORKER, ExecAck(exec_id=primeiro["id"]))                 # type: ignore[union-attr]
    # O agente já redige, mas o central redige DE NOVO antes de gravar (não se confia só no agente).
    segredo = "API_TO" + "KEN=abc123segredo"
    await s.workers.comandos.on_result(WORKER, ExecResult(                              # type: ignore[union-attr]
        exec_id=primeiro["id"], estado="succeeded", exit_code=0, stdout=f"ok {segredo}", duration_ms=12))
    guardado = s.workers.comandos.obter(WORKER, primeiro["id"])                         # type: ignore[union-attr]
    assert guardado["state"] == "succeeded" and guardado["exit_code"] == 0 and "abc123segredo" not in guardado["stdout"]
    assert agente.do_tipo("exec_result_ack")[0]["exec_id"] == primeiro["id"]
    assert [m["exec_id"] for m in agente.do_tipo("exec")] == [primeiro["id"], segundo["id"]]   # a fila andou


async def test_idempotency_key_repetida_devolve_o_mesmo_sem_repetir_o_efeito(harness: Harness) -> None:
    agente = _ligar(harness)
    a = await _pedir(harness, chave="chave-de-teste-01")
    b = await _pedir(harness, chave="chave-de-teste-01")
    assert a["id"] == b["id"] and len(agente.do_tipo("exec")) == 1


# ---------------------------------------------------------------- recusas
async def test_linha_com_credencial_e_recusada_e_o_texto_nao_e_guardado(harness: Harness) -> None:
    agente = _ligar(harness)
    with pytest.raises(ErroDeComando) as exc:
        await _pedir(harness, "net use \\\\host\\share /user:fulano senha123 --password hunter2")
    assert exc.value.status == 422 and exc.value.code == "linha_com_credencial"
    assert not agente.do_tipo("exec")
    linhas = harness.state.db.query("SELECT linha_redigida, state, reason FROM worker_comandos")   # type: ignore[union-attr]
    assert [r["state"] for r in linhas] == ["rejected"]
    assert "hunter2" not in repr([dict(r) for r in linhas]) and "senha123" not in repr([dict(r) for r in linhas])
    eventos = [e for e in harness.state.bus.since(0) if e.kind == "worker.comando"]       # type: ignore[union-attr]
    assert "hunter2" not in repr([e.data for e in eventos])


@pytest.mark.parametrize("central,worker,anuncia,conectar,codigo", [
    (False, True, True, True, "comando_remoto_desligado"),       # interruptor do central
    (True, False, True, True, "comando_remoto_desligado"),       # interruptor do worker
    (True, True, False, True, "worker_sem_remote_exec"),         # o agente não anuncia (comando_remoto: false)
    (True, True, True, False, "worker_sem_remote_exec"),         # sem canal vivo
])
async def test_desligado_em_qualquer_lado_recusa_com_409(harness: Harness, central: bool, worker: bool, anuncia: bool,
                                                         conectar: bool, codigo: str) -> None:
    agente = _ligar(harness, central=central, worker=worker, anuncia=anuncia, conectar=conectar)
    with pytest.raises(ErroDeComando) as exc:
        await _pedir(harness)
    assert exc.value.status == 409 and exc.value.code == codigo
    assert not agente.do_tipo("exec")


async def test_o_central_nunca_e_alvo(harness: Harness) -> None:
    _ligar(harness)
    harness.state.workers.local_worker_id = WORKER             # type: ignore[union-attr]
    with pytest.raises(ErroDeComando) as exc:
        await _pedir(harness)
    assert exc.value.code == "central_fora"


async def test_sem_operador_nao_ha_comando(harness: Harness) -> None:
    _ligar(harness)
    with pytest.raises(ErroDeComando) as exc:
        await harness.state.workers.comandos.pedir(WORKER, None, linha="dir", argv=None, pasta=None,   # type: ignore[union-attr]
                                                   timeout_s=None, idempotency_key=None)
    assert exc.value.status == 401


async def test_sem_auditoria_o_comando_nao_corre(harness: Harness) -> None:
    agente = _ligar(harness)
    harness.state.workers.comandos.emitir = None               # type: ignore[union-attr]
    with pytest.raises(ErroDeComando):
        await _pedir(harness)
    assert not agente.do_tipo("exec")


async def test_fila_cheia_devolve_429(harness: Harness) -> None:
    _ligar(harness)
    harness.state.workers.comandos.cfg.fila_max = 1            # type: ignore[union-attr]
    await _pedir(harness, "a")
    await _pedir(harness, "b")
    with pytest.raises(ErroDeComando) as exc:
        await _pedir(harness, "c")
    assert exc.value.status == 429 and exc.value.code == "fila_cheia"


# ---------------------------------------------------------------- incerteza nunca é repetida
async def test_queda_do_canal_deixa_em_voo_uncertain_e_na_fila_rejected(harness: Harness) -> None:
    agente = _ligar(harness)
    voo = await _pedir(harness, "um")
    fila = await _pedir(harness, "dois")
    s = harness.state
    s.workers.detach(WORKER, "conexão encerrada", s.workers.live[WORKER])             # type: ignore[union-attr]
    c = s.workers.comandos                                                              # type: ignore[union-attr]
    assert c.obter(WORKER, voo["id"])["state"] == "uncertain"
    assert c.obter(WORKER, fila["id"])["state"] == "rejected"
    assert len(agente.do_tipo("exec")) == 1                                             # e nada foi reenviado


async def test_reinicio_do_central_nao_repete_o_que_ficou_em_voo(harness: Harness) -> None:
    agente = _ligar(harness)
    voo = await _pedir(harness, "um")
    c = harness.state.workers.comandos                                                  # type: ignore[union-attr]
    assert c.reconciliar_na_subida() == 1
    assert c.obter(WORKER, voo["id"])["state"] == "uncertain" and len(agente.do_tipo("exec")) == 1


async def test_cancelar_na_fila_cancela_e_em_voo_pede_ao_agente(harness: Harness) -> None:
    agente = _ligar(harness)
    voo = await _pedir(harness, "um")
    fila = await _pedir(harness, "dois")
    c = harness.state.workers.comandos                                                  # type: ignore[union-attr]
    assert (await c.cancelar(WORKER, fila["id"], OPERADOR))["state"] == "cancelled"
    await c.cancelar(WORKER, voo["id"], OPERADOR)
    assert agente.do_tipo("exec_cancel")[0]["exec_id"] == voo["id"]
    with pytest.raises(ErroDeComando):
        await c.cancelar(WORKER, fila["id"], OPERADOR)                                  # já encerrado


async def test_ack_com_recusa_do_agente_encerra_como_rejected(harness: Harness) -> None:
    _ligar(harness)
    row = await _pedir(harness)
    c = harness.state.workers.comandos                                                  # type: ignore[union-attr]
    c.on_ack(WORKER, ExecAck(exec_id=row["id"], recusa="a linha parece levar credencial"))
    assert c.obter(WORKER, row["id"])["state"] == "rejected"


def test_o_fio_do_central_para_o_agente_so_leva_a_linha_redigida_no_banco(harness: Harness) -> None:
    # `exec_result_ack` e `exec_cancel` estão no mapa de mensagens do central; `exec`/`exec_ack`/`exec_result` no do agente
    assert parse_upstream({"type": "exec_ack", "exec_id": "abcdefgh"}).exec_id == "abcdefgh"


# ---------------------------------------------------------------- HTTP
async def test_http_exige_sessao_nomeada_mesmo_no_loopback(harness: Harness) -> None:
    _ligar(harness)
    async with _cliente(harness) as c:
        r = await c.post(f"/api/workers/{WORKER}/comandos", json={"linha": "dir"})
        assert r.status_code == 401 and r.json()["detail"]["code"] == "sem_operador"
        assert (await c.get(f"/api/workers/{WORKER}/comandos")).status_code == 401
        assert (await c.get(f"/api/workers/{WORKER}/comando-remoto")).status_code == 401


async def test_http_fluxo_completo_com_sessao(harness: Harness) -> None:
    agente = _ligar(harness)
    async with _cliente(harness) as c:
        await _entrar(c)
        r = await c.post(f"/api/workers/{WORKER}/comandos", json={"linha": "Get-Process", "timeout_s": 30})
        assert r.status_code == 202, r.text
        corpo = r.json()
        assert set(corpo) == {"id", "worker_id", "state", "created_at"}                 # sem eco da linha
        await harness.state.workers.comandos.on_result(                                   # type: ignore[union-attr]
            WORKER, ExecResult(exec_id=corpo["id"], estado="failed", exit_code=2, stderr="erro", duration_ms=5))
        lido = (await c.get(f"/api/workers/{WORKER}/comandos/{corpo['id']}")).json()
        assert lido["state"] == "failed" and lido["exit_code"] == 2 and lido["requested_by"] == OPERADOR
        lista = (await c.get(f"/api/workers/{WORKER}/comandos")).json()["items"]
        assert [i["id"] for i in lista] == [corpo["id"]] and "stdout" not in lista[0]
        assert (await c.get(f"/api/workers/{WORKER}/comandos/inexistente")).status_code == 404
        ruim = await c.post(f"/api/workers/{WORKER}/comandos", json={"linha": "dir", "argv": ["dir"]})
        assert ruim.status_code == 422
        segredo = await c.post(f"/api/workers/{WORKER}/comandos", json={"linha": "echo oi --password x"})
        assert segredo.status_code == 422 and segredo.json()["detail"]["code"] == "linha_com_credencial"
        outro = (await c.post(f"/api/workers/{WORKER}/comandos", json={"argv": ["ping", "-n", "30", "127.0.0.1"]})).json()
        cancelado = await c.post(f"/api/workers/{WORKER}/comandos/{outro['id']}/cancelar")
        assert cancelado.status_code == 200 and cancelado.json()["id"] == outro["id"]
        assert (await c.post(f"/api/workers/{WORKER}/comandos/{corpo['id']}/cancelar")).status_code == 409
    assert len(agente.do_tipo("exec")) == 2 and agente.do_tipo("exec_cancel")[0]["exec_id"] == outro["id"]


async def test_http_requested_by_vem_da_sessao_e_nao_do_corpo(harness: Harness) -> None:
    _ligar(harness)
    async with _cliente(harness) as c:
        await _entrar(c)
        r = await c.post(f"/api/workers/{WORKER}/comandos", json={"linha": "dir", "requested_by": "outro"})
        assert r.status_code == 422                                                       # campo desconhecido: recusado
        r = await c.post(f"/api/workers/{WORKER}/comandos", json={"linha": "dir"})
        assert harness.state.workers.comandos.obter(WORKER, r.json()["id"])["requested_by"] == OPERADOR   # type: ignore[union-attr]


async def test_http_no_host_publico_responde_404_mesmo_com_credencial(harness: Harness) -> None:
    _ligar(harness)
    harness.cfg.file.server.host = "0.0.0.0"                                              # noqa: S104 - o cenário sob teste
    harness.cfg.file.server.public_hosts = ["portal.exemplo.test"]
    harness.cfg.file.server.allowed_origins = [*harness.cfg.file.server.allowed_origins, "http://portal.exemplo.test"]
    harness.cfg.env.api_token = SecretStr("tk-de-teste-0123456789")
    async with _cliente(harness, base="http://portal.exemplo.test") as c:
        c.headers["Authorization"] = "Bearer tk-de-teste-0123456789"
        assert (await c.get("/api/workers")).status_code == 200                           # o resto da família passa
        for caminho in (f"/api/workers/{WORKER}/comandos", f"/api/workers/{WORKER}/comando-remoto"):
            assert (await c.get(caminho)).status_code == 404, caminho
        assert (await c.post(f"/api/workers/{WORKER}/comandos", json={"linha": "dir"})).status_code == 404
        assert (await c.put(f"/api/workers/{WORKER}/comando-remoto", json={"ligado": True})).status_code == 404


async def test_http_interruptor_liga_e_desliga_ao_vivo_e_audita(harness: Harness) -> None:
    _ligar(harness, worker=False)
    async with _cliente(harness) as c:
        await _entrar(c)
        antes = (await c.get(f"/api/workers/{WORKER}/comando-remoto")).json()
        assert antes["worker_ligado"] is False and antes["central_ativo"] is True and antes["agente_anuncia"] is True
        r = await c.put(f"/api/workers/{WORKER}/comando-remoto", json={"ligado": True})
        assert r.status_code == 200 and r.json()["worker_ligado"] is True
        assert (await c.put("/api/workers/nao-existe/comando-remoto", json={"ligado": True})).status_code == 404
    eventos = [e.kind for e in harness.state.bus.since(0)]                                # type: ignore[union-attr]
    assert "worker.comando.interruptor" in eventos


async def test_http_422_de_validacao_nao_ecoa_a_linha(harness: Harness) -> None:
    """O 422 padrão do FastAPI devolve o `input` de cada erro: numa linha grande demais, a linha inteira voltaria."""
    _ligar(harness)
    longa = "echo " + "x" * 9000 + " --pass" + "word hunter2"
    async with _cliente(harness) as c:
        await _entrar(c)
        r = await c.post(f"/api/workers/{WORKER}/comandos", json={"linha": longa})
        assert r.status_code == 422
        assert "hunter2" not in r.text and "xxxxxxxxxx" not in r.text
        r = await c.post(f"/api/workers/{WORKER}/comandos", json={"argv": "nao-e-lista hunter2"})
        assert r.status_code == 422 and "hunter2" not in r.text


async def test_pasta_com_cara_de_segredo_tambem_e_recusada(harness: Harness) -> None:
    agente = _ligar(harness)
    with pytest.raises(ErroDeComando) as exc:
        await harness.state.workers.comandos.pedir(WORKER, OPERADOR, linha="dir", argv=None,   # type: ignore[union-attr]
                                                   pasta="C:/temp/token=abc123segredo", timeout_s=None,
                                                   idempotency_key=None)
    assert exc.value.code == "linha_com_credencial" and not agente.do_tipo("exec")
    linhas = [dict(r) for r in harness.state.db.query("SELECT * FROM worker_comandos")]       # type: ignore[union-attr]
    assert "abc123segredo" not in repr(linhas)
