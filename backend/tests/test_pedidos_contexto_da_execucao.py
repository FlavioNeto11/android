"""Item 28.10, F5 — o provedor `contexto_do_pedido(db, run_id)` (a porta de política o lerá no 30.62).

Prova `simulated`: `TestClient` com SQLite e o laço com `RunService` de verdade e provedor simulado, como
`test_pedidos_colaboracao_para_fora.py`. Cobre a subida execução → ocorrência → pedido → raiz, a família (raiz e filhos
diretos) em ids de persona, o porta-voz, e as contraprovas: execução sem pedido, aparelho com mais de uma persona, mais de uma
persona no porta-voz e porta-voz cancelado. Só ids: nenhum texto de pedido vai ao contexto.
"""
from __future__ import annotations

import dataclasses

import pytest_asyncio

from app.modules.pedidos.infrastructure.contexto import ContextoDoPedido, contexto_do_pedido

from .conftest import Harness
from .test_pedidos_api import DIARIO
from .test_pedidos_colaboracao_api import AGORA, _cliente, _filho, _ligar, _pai
from .test_pedidos_colaboracao_para_fora import _laco
from .test_travas import Relogio


@pytest_asyncio.fixture
async def h(harness: Harness, monkeypatch) -> Harness:
    monkeypatch.setattr(harness.state.runs, "_spawn_planning", lambda run_id: None)
    harness.state.pedidos.relogio = Relogio()
    return harness


def _alvos(h: Harness, pedido_id: str, *perfis: str, aparelho: str = "android-01") -> None:
    lista = ",".join(f'{{"instance_id":"{aparelho}","profile_id":"{p}","app_id":null}}' for p in perfis)
    h.state.db.execute("UPDATE pedidos SET alvos=? WHERE id=?", (f'{{"alvos":[{lista}]}}', pedido_id))


def _run(h: Harness, pedido_id: str) -> str:
    [o] = h.state.db.query("SELECT run_id FROM pedido_ocorrencias WHERE pedido_id=?", (pedido_id,))
    assert o["run_id"]
    return o["run_id"]


async def test_a_execucao_de_um_filho_sobe_a_raiz_e_lista_a_familia(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    pai = _pai(c, "pai-ctx-0001", gatilhos=[DIARIO])
    pv = _filho(c, pai, "filho-pv-ctx-01", gatilhos=[AGORA], papel="porta_voz", autonomia="agir").json()["id"]
    pesq = _filho(c, pai, "filho-pesq-ctx-1", gatilhos=[DIARIO], papel="pesquisador", autonomia="observar").json()["id"]
    _laco(h).uma_volta()          # o laço roda com os alvos reais; as personas entram depois, só para o provedor ler
    _alvos(h, pai, "persona-raiz")
    _alvos(h, pv, "persona-pv")
    _alvos(h, pesq, "persona-pesq")
    ctx = contexto_do_pedido(h.state.db, _run(h, pv))
    assert ctx == ContextoDoPedido(raiz=pai, familia=frozenset({"persona-raiz", "persona-pv", "persona-pesq"}),
                                   porta_vozes=frozenset({"persona-pv"}))
    assert {f.name for f in dataclasses.fields(ctx)} == {"raiz", "familia", "porta_vozes"}


async def test_a_execucao_da_raiz_tem_a_mesma_familia(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    pai = _pai(c, "pai-ctx-0002", gatilhos=[AGORA])
    pv = _filho(c, pai, "filho-pv-ctx-02", gatilhos=[DIARIO], papel="porta_voz", autonomia="agir").json()["id"]
    _laco(h).uma_volta()
    _alvos(h, pai, "persona-raiz")
    _alvos(h, pv, "persona-pv")
    ctx = contexto_do_pedido(h.state.db, _run(h, pai))
    assert ctx is not None and ctx.raiz == pai and ctx.porta_vozes == {"persona-pv"}
    assert ctx.familia == frozenset({"persona-raiz", "persona-pv"})


async def test_contraprova_execucao_sem_pedido_ou_inexistente_e_none(h: Harness) -> None:
    assert contexto_do_pedido(h.state.db, "run-que-nao-existe") is None
    r = h.state.db.one("SELECT id FROM runs WHERE pedido_id IS NULL LIMIT 1")
    if r is not None:
        assert contexto_do_pedido(h.state.db, r["id"]) is None


async def test_sem_porta_voz_e_com_dois_os_porta_vozes_sao_o_conjunto(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    pai = _pai(c, "pai-ctx-0003", gatilhos=[AGORA])
    pv = _filho(c, pai, "filho-pv-ctx-03", gatilhos=[DIARIO], papel="porta_voz", autonomia="agir").json()["id"]
    _laco(h).uma_volta()
    _alvos(h, pai, "persona-raiz")
    run = _run(h, pai)
    # porta-voz sem persona resolvida (aparelho sem vínculo único): ninguém é inventado
    h.state.db.execute("UPDATE pedidos SET alvos=NULL WHERE id=?", (pv,))
    ctx = contexto_do_pedido(h.state.db, run)
    assert ctx is not None and ctx.porta_vozes == frozenset() and ctx.familia == frozenset({"persona-raiz"})
    # duas personas no porta-voz: as duas são porta-vozes (30.62: só elas tocam um alvo; entre elas, uma conta por alvo)
    _alvos(h, pv, "persona-a", "persona-b")
    ctx = contexto_do_pedido(h.state.db, run)
    assert ctx is not None and ctx.porta_vozes == {"persona-a", "persona-b"} and {"persona-a", "persona-b"} <= ctx.familia
    # porta-voz cancelado deixa de ser porta-voz; a persona dele segue na família (já agiu, a porta olha o histórico)
    _alvos(h, pv, "persona-pv")
    h.state.db.execute("UPDATE pedidos SET estado='cancelado' WHERE id=?", (pv,))
    ctx = contexto_do_pedido(h.state.db, run)
    assert ctx is not None and ctx.porta_vozes == frozenset() and "persona-pv" in ctx.familia
