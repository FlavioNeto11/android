"""Item 28.10, F5 — regras "para fora" da colaboração (`docs/design/pedidos-persistentes.md` §9 e §9.1, ADR-074).

Prova `simulated` (`arquivo::teste`): domínio puro, `TestClient` com SQLite e relógio falso, e o laço com `RunService` de
verdade e provedor simulado, como `test_pedidos_colaboracao_papeis.py`. Cobre o que é só do lado dos pedidos:

* só o porta-voz age para fora: numa família com porta-voz, a raiz sem papel e o irmão comum decidem com `observar` e levam
  esse teto à execução (`RunCreate.teto_de_autonomia`, 28.23); o porta-voz segue em `agir` e o redator em `preparar`;
* sem porta-voz na família, ou com a colaboração desligada, o laço decide como antes (contraprova);
* a prévia mostra o teto da raiz sem papel e a criação recusa (422 `reacao_repetida`) duas personas com o mesmo objetivo
  de efeito na mesma família;
* os motivos e as notas dizem só o papel e as autonomias: nunca nome de persona, de conta, nem o texto do comando.

A regra de uma conta por alvo e a aprovação para pessoa real são da porta de política (`PolicyEngine.check`, item 30.62),
e o provedor `contexto_do_pedido` fica em `test_pedidos_contexto_da_execucao.py`.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from app.config import ColaboracaoCfg, PedidosCfg
from app.modules.pedidos.domain import colaboracao
from app.modules.pedidos.infrastructure.laco import LacoDePedidos
from app.taskqueue.travas import Lideranca

from .conftest import Harness
from .test_pedidos_api import DIARIO
from .test_pedidos_colaboracao_api import AGORA, _cliente, _filho, _ligar, _pai
from .test_pedidos_laco import _assentar
from .test_travas import Relogio


@pytest_asyncio.fixture
async def h(harness: Harness, monkeypatch) -> Harness:
    monkeypatch.setattr(harness.state.runs, "_spawn_planning", lambda run_id: None)
    harness.state.pedidos.relogio = Relogio()
    return harness


def _laco(h: Harness, *, colaboracao_ligada: bool = True) -> LacoDePedidos:
    r = Relogio()
    h.state.pedidos.relogio = r
    cfg = PedidosCfg(enabled=True, prazo_inicio_s=604_800, colaboracao=ColaboracaoCfg(enabled=colaboracao_ligada))
    laco = LacoDePedidos(h.state.db, h.state.runs, Lideranca(h.state.db, dono="laco-para-fora", relogio=r), cfg, relogio=r)
    laco.notificar = h.state.pedidos_api.publicar
    return laco


def _teto_da_execucao(h: Harness, pedido_id: str) -> str | None:
    [o] = h.state.db.query("SELECT run_id FROM pedido_ocorrencias WHERE pedido_id=?", (pedido_id,))
    assert o["run_id"], "a ocorrência devia ter virado execução"
    return h.state.db.scalar("SELECT teto_de_autonomia FROM runs WHERE id=?", (o["run_id"],))


# =============================================================================================== domínio
@pytest.mark.parametrize(("autonomia", "papel", "porta_voz", "efetiva"), [
    ("agir", None, True, "observar"), ("preparar", None, True, "observar"), ("observar", None, True, "observar"),
    ("agir", None, False, "agir"),                   # sem porta-voz na família, vale a autonomia do pedido
    ("agir", "porta_voz", True, "agir"),             # o porta-voz é quem age
    ("agir", "redator", True, "preparar"),           # papel com teto próprio mantém o dele
    ("agir", "pesquisador", True, "observar"),
])
def test_numa_familia_com_porta_voz_so_ele_age(autonomia, papel, porta_voz, efetiva) -> None:
    assert colaboracao.autonomia_efetiva(autonomia, papel, porta_voz) == efetiva


def test_a_nota_da_familia_nao_cita_persona_nem_texto() -> None:
    nota = colaboracao.nota_de_rebaixamento("agir", None, True)
    assert nota == "autonomia rebaixada: a família tem porta-voz e só ele age para fora: agir → observar"
    assert colaboracao.nota_de_rebaixamento("agir", None, False) is None
    assert colaboracao.nota_de_rebaixamento("observar", None, True) is None
    # o texto antigo do papel segue igual
    assert colaboracao.nota_de_rebaixamento("agir", "pesquisador") == (
        "autonomia rebaixada ao teto do papel pesquisador: agir → observar")


# =============================================================================================== laço
async def test_a_raiz_sem_papel_com_porta_voz_na_familia_leva_observar_a_execucao(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    laco = _laco(h)
    pai = _pai(c, "pai-fora-0001", gatilhos=[AGORA], autonomia="agir")
    pv = _filho(c, pai, "filho-pv-0001", gatilhos=[DIARIO], papel="porta_voz", autonomia="agir")
    assert pv.status_code == 201, pv.text
    laco.uma_volta()
    assert _teto_da_execucao(h, pai) == "observar", "a raiz sem papel não age fora do porta-voz"
    [o] = h.state.db.query("SELECT * FROM pedido_ocorrencias WHERE pedido_id=?", (pai,))
    _assentar(h.state.db, o["run_id"], "completed")
    laco.uma_volta()
    motivo = h.state.db.scalar("SELECT motivo FROM pedido_ocorrencias WHERE id=?", (o["id"],)) or ""
    assert "a família tem porta-voz e só ele age para fora: agir → observar" in motivo
    objetivo = h.state.db.scalar("SELECT objetivo FROM pedidos WHERE id=?", (pai,))
    assert objetivo not in motivo and "android-01" not in motivo


async def test_o_porta_voz_o_irmao_comum_e_o_redator(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    laco = _laco(h)
    pai = _pai(c, "pai-fora-0002", gatilhos=[DIARIO])
    ids = {}
    for nome, papel in (("pv", "porta_voz"), ("comum", None), ("redator", "redator")):
        kw = {"papel": papel} if papel else {}
        r = _filho(c, pai, f"filho-{nome}-0002", gatilhos=[AGORA], autonomia="preparar" if papel == "redator" else "agir",
                   **kw)
        assert r.status_code == 201, r.text
        ids[nome] = r.json()["id"]
    laco.uma_volta()
    assert _teto_da_execucao(h, ids["pv"]) == "agir"
    assert _teto_da_execucao(h, ids["comum"]) == "observar", "o irmão comum também não age fora do porta-voz"
    assert _teto_da_execucao(h, ids["redator"]) == "preparar"


async def test_contraprova_sem_porta_voz_a_raiz_segue_com_a_autonomia_do_pedido(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    laco = _laco(h)
    pai = _pai(c, "pai-fora-0003", gatilhos=[AGORA], autonomia="agir")
    _filho(c, pai, "filho-pesq-0003", gatilhos=[DIARIO], papel="pesquisador", autonomia="observar")
    laco.uma_volta()
    assert _teto_da_execucao(h, pai) == "agir"
    [o] = h.state.db.query("SELECT * FROM pedido_ocorrencias WHERE pedido_id=?", (pai,))
    _assentar(h.state.db, o["run_id"], "completed")
    laco.uma_volta()
    assert "rebaixada" not in (h.state.db.scalar("SELECT motivo FROM pedido_ocorrencias WHERE id=?", (o["id"],)) or "")


async def test_contraprova_colaboracao_desligada_o_laco_decide_como_hoje(h: Harness) -> None:
    """Com a colaboração desligada no laço, um porta-voz gravado (legado) não muda o teto de ninguém."""
    _ligar(h)
    c = _cliente(h)
    pai = _pai(c, "pai-fora-0004", gatilhos=[AGORA], autonomia="agir")
    assert _filho(c, pai, "filho-pv-0004", gatilhos=[DIARIO], papel="porta_voz", autonomia="agir").status_code == 201
    laco = _laco(h, colaboracao_ligada=False)
    laco.uma_volta()
    assert _teto_da_execucao(h, pai) == "agir"


# =============================================================================================== prévia e criação
def _previa(c, **kw) -> dict:
    from .test_pedidos_api import _corpo
    return c.post("/api/pedidos/previa", json=_corpo(**kw)).json()


def _reator(id_: str, objetivo: str, autonomia: str, *pessoas: str) -> colaboracao.Reator:
    return colaboracao.Reator(id_, objetivo, autonomia, frozenset(pessoas))


def test_reacao_repetida_no_dominio() -> None:
    a = _reator("a", "Curta  a publicação X", "agir", "p1")
    ok = colaboracao.validar_reacao_repetida
    r = ok(_reator("b", "curta a publicação x", "preparar", "p2"), [a])
    assert r is not None and (r.codigo, r.campo) == ("reacao_repetida", "objetivo")
    assert "p1" not in r.mensagem and "p2" not in r.mensagem and "publicação" not in r.mensagem
    # contraprovas: a mesma persona, outro texto, quem só observa, e o que já observa na família
    assert ok(_reator("b", "curta a publicação x", "agir", "p1"), [a]) is None
    assert ok(_reator("b", "comente a publicação y", "agir", "p2"), [a]) is None
    assert ok(_reator("b", "curta a publicação x", "observar", "p2"), [a]) is None
    assert ok(_reator("b", "curta a publicação x", "agir", "p2"), [_reator("a", "curta a publicação x", "observar", "p1")]) is None


async def test_previa_e_criacao_recusam_duas_reacoes_ao_mesmo_conteudo(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    pai = _pai(c, "pai-reacao-0001", autonomia="observar")
    h.state.db.execute("UPDATE pedidos SET alvos=? WHERE id=?",
                       ('{"alvos":[{"instance_id":"android-01","profile_id":"persona-a","app_id":null}]}', pai))
    h.state.db.execute("UPDATE pedidos SET autonomia='agir' WHERE id=?", (pai,))
    corpo = {"autonomia": "agir", "pai_id": pai, "orcamento_total_usd": 2.0}
    previa = _previa(c, **corpo)           # o alvo do filho é o android-01 sem persona: outra pessoa (o aparelho)
    assert not previa["valido"] and previa["bloqueios"][0]["codigo"] == "reacao_repetida", previa["bloqueios"]
    from .test_pedidos_colaboracao_api import _tentar
    r = _tentar(c, "filho-reacao-0001", pai, autonomia="agir")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "reacao_repetida"
    # contraprova: o filho que só observa passa, e o texto diferente também
    assert _previa(c, **{**corpo, "autonomia": "observar"})["valido"]
    assert _previa(c, **{**corpo, "objetivo": "Outra coisa bem diferente para fazer"})["valido"]


async def test_a_previa_mostra_o_teto_da_familia_e_o_detalhe_o_teto_efetivo(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    pai = _pai(c, "pai-previa-0001", autonomia="agir")
    assert _filho(c, pai, "filho-pv-previa-01", papel="porta_voz", autonomia="agir").status_code == 201
    previa = _previa(c, pai_id=pai, autonomia="agir", orcamento_total_usd=1.0, objetivo="Reunir as fontes da semana")
    assert previa["valido"], previa["bloqueios"]
    assert previa["autonomia"]["teto"] == "observar"
    [alerta] = [a for a in previa["alertas"] if a["codigo"] == "autonomia_rebaixada_pela_familia"]
    assert "porta-voz" in alerta["mensagem"] and "observar" in alerta["mensagem"] and "Reunir" not in alerta["mensagem"]
    assert c.get(f"/api/pedidos/{pai}").json()["autonomia_efetiva"] == "observar"
    # contraprovas: o porta-voz e a família sem porta-voz não têm alerta; desligada, o detalhe volta ao gravado
    pv = _previa(c, pai_id=pai, autonomia="agir", papel="porta_voz", orcamento_total_usd=1.0)
    assert all(a["codigo"] != "autonomia_rebaixada_pela_familia" for a in pv["alertas"])
    outro = _pai(c, "pai-previa-0002", autonomia="agir")
    assert c.get(f"/api/pedidos/{outro}").json()["autonomia_efetiva"] == "agir"
    h.state.pedidos_api.cfg.colaboracao = ColaboracaoCfg(enabled=False)
    assert c.get(f"/api/pedidos/{pai}").json()["autonomia_efetiva"] == "agir"
