"""Item 28.31, F2a (migração 106): o pedido guarda quem o criou e a chave de lote, decididos UMA vez, na criação.

Prova `simulated` (`arquivo::teste`): banco de teste, aparelhos falsos, planejamento desligado. Cobre: a regra do autor
(só a lista declarada é o dono; loopback sem sessão e nome qualquer no login não são), a gravação pela API e pelo
serviço, as marcas no `AvisoDTO` (gravado e lido), o aviso de lote indo à janela (menos a aprovação e a ocorrência
incerta) e o pedido anterior à 106 saindo pelo id curto.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from app.modules.avisos.domain.mensagem import AGORA, JANELA, JANELA_DA_ROTINA_S, aviso_de_evento, entrega_do_tipo
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.pedidos.domain import autor
from app.modules.pedidos.domain.avisos import TIPOS as TIPOS_DO_PEDIDO
from app.modules.pedidos.presentation.schemas import CriarCorpo

from .conftest import Harness
from .test_avisos_rajada import Cena
from .test_pedidos_api import _cliente, _corpo, _criar
from .test_travas import Relogio

REDIGIR = TriagemDeCredencial().redigir


@pytest_asyncio.fixture
async def h(harness: Harness, monkeypatch) -> Harness:
    monkeypatch.setattr(harness.state.runs, "_spawn_planning", lambda run_id: None)
    harness.state.pedidos.relogio = Relogio()
    return harness


DONOS = autor.operadores_do_dono(["Dono Teste"], "m-dono")


@pytest.mark.parametrize(("operador", "chave", "esperado", "lote"), [
    # 1. só a lista declarada (sem diferença de caixa e espaço) e o `trello:<membro_dono>` são o dono
    ("Dono Teste", "chave-qualquer-0001", autor.DONO, None),
    ("  dono   teste ", "chave-qualquer-0001", autor.DONO, None),
    ("trello:m-dono", "chave-qualquer-0001", autor.DONO, None),
    # 2. `lote:` é da frente SEMPRE, mesmo com o operador do dono
    ("Dono Teste", "lote:canais:28.31", autor.FRENTE, "lote:canais:28.31"),
    (None, "lote:canais:28.31", autor.FRENTE, "lote:canais:28.31"),
    ("  ", " lote:aprendizado:30.1 ", autor.FRENTE, "lote:aprendizado:30.1"),
    # 3. operador que não é o dono: convidado (o membro autorizado do Trello, o nome qualquer do login)
    ("Outra Pessoa", "chave-qualquer-0001", autor.CONVIDADO, None),
    ("trello:m-autorizado", "chave-qualquer-0001", autor.CONVIDADO, None),
    ("telegram:m-dono", "chave-qualquer-0001", autor.CONVIDADO, None),
    # 4. sem operador e sem lote
    (None, "chave-qualquer-0001", autor.DESCONHECIDO, None),
    (None, "xlote:canais:1", autor.DESCONHECIDO, None),
    (None, None, autor.DESCONHECIDO, None),
])
def test_autor_da_criacao(operador: str | None, chave: str | None, esperado: str, lote: str | None) -> None:
    assert autor.autor_da_criacao(operador, chave, DONOS) == esperado
    assert autor.lote_da_chave(chave) == lote
    assert esperado in autor.TIPOS


def test_lista_vazia_ninguem_e_o_dono() -> None:
    vazia = autor.operadores_do_dono([], "")
    assert vazia == frozenset()
    assert autor.autor_da_criacao("Dono Teste", "chave-qualquer-0001", vazia) == autor.CONVIDADO
    assert autor.autor_da_criacao("trello:", "chave-qualquer-0001", vazia) == autor.CONVIDADO
    assert autor.autor_da_criacao("Dono Teste", "chave-qualquer-0001") == autor.CONVIDADO


def _autoria(h: Harness, pid: str) -> tuple[str | None, str | None]:
    r = h.state.db.one("SELECT criado_por_tipo, lote FROM pedidos WHERE id=?", (pid,))
    assert r is not None
    return r["criado_por_tipo"], r["lote"]


async def test_a_api_le_a_lista_da_config(h: Harness) -> None:
    cfg = h.cfg.file
    assert h.state.pedidos_api.donos == autor.operadores_do_dono(cfg.pedidos.operadores_do_dono, cfg.trello.membro_dono)


async def test_pela_api_o_loopback_sem_sessao_nao_e_o_dono(h: Harness) -> None:
    c = _cliente(h)
    frente = _criar(c, "lote:canais:28.31-f2a")
    assert frente.status_code == 201, frente.text
    assert _autoria(h, frente.json()["id"]) == (autor.FRENTE, "lote:canais:28.31-f2a")
    solto = _criar(c, "chave-sem-lote-0001")
    assert solto.status_code == 201, solto.text
    assert _autoria(h, solto.json()["id"]) == (autor.DESCONHECIDO, None)


async def test_nome_qualquer_no_login_nao_vira_dono(h: Harness, monkeypatch) -> None:
    """O `POST /api/login` aceita qualquer nome: a sessão com nome fora da lista grava `convidado`; o da lista, `dono`."""
    monkeypatch.setattr(h.state.pedidos_api, "donos", DONOS)
    c = _cliente(h)
    assert c.post("/api/login", json={"operator": "Alguém Qualquer"}).status_code == 200
    r = _criar(c, "chave-do-login-0001")
    assert r.status_code == 201, r.text
    assert _autoria(h, r.json()["id"]) == (autor.CONVIDADO, None)
    assert c.post("/api/login", json={"operator": "Dono Teste"}).status_code == 200
    r = _criar(c, "chave-do-login-0002")
    assert r.status_code == 201, r.text
    assert _autoria(h, r.json()["id"]) == (autor.DONO, None)


async def test_o_dono_declarado_cria_pelo_servico(h: Harness, monkeypatch) -> None:
    monkeypatch.setattr(h.state.pedidos_api, "donos", DONOS)
    api = h.state.pedidos_api
    corpo = CriarCorpo.model_validate({**_corpo(), "idempotency_key": "chave-do-dono-0001"}).para_corpo()
    view, _dup = api.criar(corpo, idempotency_key="chave-do-dono-0001", titulo=None,
                           confirmacao=api.previa(corpo)["confirmacao"], operador="Dono Teste")
    assert _autoria(h, view["id"]) == (autor.DONO, None)


async def test_o_aviso_leva_as_marcas_gravado_e_lido(h: Harness) -> None:
    c = _cliente(h)
    pid = _criar(c, "lote:canais:28.31-avisos").json()["id"]
    dto = h.state.pedidos_api.registrar_aviso({
        "id": "avs-f2a-1", "pedido_id": pid, "ocorrencia_id": None, "tipo": "pausa_automatica", "nivel": "warn",
        "mensagem": "pausado", "dados": {"falhas_seguidas": 3}, "requer_pessoa": True})
    assert dto is not None and dto["criado_pelo_dono"] is False and dto["de_lote"] is True
    lidos = c.get("/api/pedidos/avisos").json()["items"]
    assert [(a["criado_pelo_dono"], a["de_lote"]) for a in lidos if a["id"] == dto["id"]] == [(False, True)]


async def test_pedido_anterior_a_106_sai_sem_marca(h: Harness) -> None:
    """Sem backfill: o pedido antigo (as duas colunas nulas) não é do dono nem de lote, e o aviso sai pelo id curto."""
    c = _cliente(h)
    pid = _criar(c, "chave-antiga-0001").json()["id"]
    h.state.db.execute("UPDATE pedidos SET criado_por_tipo=NULL, lote=NULL WHERE id=?", (pid,))
    dto = h.state.pedidos_api.registrar_aviso({
        "id": "avs-f2a-2", "pedido_id": pid, "ocorrencia_id": None, "tipo": "pausa_automatica", "nivel": "warn",
        "mensagem": "pausado", "dados": {"falhas_seguidas": 3}, "requer_pessoa": True})
    assert dto is not None and dto["criado_pelo_dono"] is False and dto["de_lote"] is False
    a = aviso_de_evento("pedido.aviso", {"aviso": dto}, 1, redigir=REDIGIR, nomes=[])
    assert a is not None and a.tipo == "pedido.pausa_automatica" and f"#{pid[4:10]}" in a.titulo


def _aviso(sub: str, de_lote: object) -> object:
    return aviso_de_evento("pedido.aviso", {"aviso": {
        "id": f"avs_{sub}", "tipo": sub, "pedido_id": "ped_kUZT1aBcd", "pedido_titulo": "Preço do café",
        "criado_pelo_dono": False, "de_lote": de_lote, "requer_pessoa": TIPOS_DO_PEDIDO[sub][1],
        "dados": {"falhas_seguidas": 3, "gasto_usd": 4.0, "orcamento_total_usd": 5.0}}}, 1, redigir=REDIGIR, nomes=[])


@pytest.mark.parametrize("sub", sorted(TIPOS_DO_PEDIDO))
def test_aviso_de_lote_vai_a_janela_menos_a_aprovacao_e_a_incerta(sub: str) -> None:
    a = _aviso(sub, True)
    assert a is not None
    if sub in ("aprovacao_pendente", "ocorrencia_incerta"):
        # Só o dono decide a aprovação, e efeito incerto em conta real é crítico: os dois seguem na hora mesmo no lote.
        assert a.tipo == f"pedido.{sub}" and entrega_do_tipo(a.tipo) == AGORA
    else:
        assert a.tipo == f"pedido.lote.{sub}" and entrega_do_tipo(a.tipo) == JANELA
    # Só `True` liga: um valor que não é booleano não muda o tipo.
    sem = _aviso(sub, "sim")
    assert sem is not None and sem.tipo == f"pedido.{sub}"


def test_a_fila_segura_o_lote_na_janela_e_solta_a_aprovacao(tmp_path: Path) -> None:
    c = Cena(tmp_path)
    c.chega("pedido:pausa-lote", tipo="pedido.lote.pausa_automatica")
    c.chega("pedido:aprov", tipo="pedido.aprovacao_pendente")
    c.volta()
    assert [t for t, _c, _l in c.canal.enviados] == ["t-pedido:aprov"]
    c.avancar(JANELA_DA_ROTINA_S + 1)
    c.volta()
    assert [t for t, _c, _l in c.canal.enviados] == ["t-pedido:aprov", "t-pedido:pausa-lote"]
