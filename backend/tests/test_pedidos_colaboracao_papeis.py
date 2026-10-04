"""Item 28.10, F3 — o papel limita a autonomia do pedido (`docs/design/pedidos-persistentes.md` §9, ADR-074).

Prova `simulated` (`arquivo::teste`): domínio puro, `TestClient` com SQLite e relógio falso, e o laço com `RunService` de
verdade e provedor simulado, como `test_pedidos_colaboracao_api.py`. Cobre o teto de cada papel (pesquisador e checador
observam, redator prepara, só o porta-voz age), o 422 `autonomia_acima_do_papel` na prévia, na criação e na edição, e a
defesa no laço: um pedido gravado acima do teto (legado, ou colaboração desligada) decide com o teto do papel e a
ocorrência registra o rebaixamento, só com os nomes do papel e das autonomias.

O que NÃO cobre, de propósito: a execução não recebe a autonomia (achado da F3, item 28.23). Aqui o teto vale no que o
laço decide (sobreposição, janela e piso), não no que a execução faz.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from app.config import PedidosCfg
from app.modules.pedidos.domain import colaboracao
from app.modules.pedidos.infrastructure import laco as modulo_laco
from app.modules.pedidos.infrastructure.laco import LacoDePedidos
from app.taskqueue.travas import Lideranca

from .conftest import Harness
from .test_pedidos_colaboracao_api import AGORA, _cliente, _codigo, _filho, _ligar, _pai, _tentar
from .test_pedidos_api import DIARIO, _corpo
from .test_pedidos_laco import _assentar
from .test_travas import Relogio


@pytest_asyncio.fixture
async def h(harness: Harness, monkeypatch) -> Harness:
    monkeypatch.setattr(harness.state.runs, "_spawn_planning", lambda run_id: None)
    harness.state.pedidos.relogio = Relogio()
    return harness


# =============================================================================================== domínio
@pytest.mark.parametrize(("autonomia", "papel", "efetiva"), [
    ("agir", "pesquisador", "observar"), ("preparar", "checador", "observar"), ("observar", "checador", "observar"),
    ("agir", "redator", "preparar"), ("observar", "redator", "observar"), ("agir", "porta_voz", "agir"),
    ("agir", None, "agir"), ("preparar", None, "preparar"),
])
def test_a_autonomia_efetiva_e_a_mais_restrita_entre_o_pedido_e_o_papel(autonomia, papel, efetiva) -> None:
    assert colaboracao.autonomia_efetiva(autonomia, papel) == efetiva


def test_papel_ou_autonomia_desconhecidos_nao_afrouxam_nada() -> None:
    assert colaboracao.autonomia_efetiva("agir", "inventado") == "agir"
    assert colaboracao.autonomia_efetiva("qualquer", "pesquisador") == "qualquer", "a prévia é quem recusa o vocabulário"


def test_a_recusa_e_a_nota_dizem_so_o_papel_e_as_autonomias() -> None:
    r = colaboracao.validar_autonomia("agir", "redator")
    assert r is not None and (r.codigo, r.campo) == ("autonomia_acima_do_papel", "autonomia")
    assert "redator" in r.mensagem and "preparar" in r.mensagem and "agir" in r.mensagem
    assert colaboracao.validar_autonomia("preparar", "redator") is None
    assert colaboracao.validar_autonomia("agir", None) is None
    assert colaboracao.nota_de_rebaixamento("agir", "pesquisador") == (
        "autonomia rebaixada ao teto do papel pesquisador: agir → observar")
    assert colaboracao.nota_de_rebaixamento("observar", "pesquisador") is None
    assert colaboracao.nota_de_rebaixamento("agir", None) is None


# =============================================================================================== API
async def test_previa_e_criacao_recusam_a_autonomia_acima_do_papel(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    pai = _pai(c, gatilhos=[DIARIO])
    antes = h.state.db.scalar("SELECT COUNT(*) FROM pedidos")
    ruim = c.post("/api/pedidos/previa", json=_corpo(pai_id=pai, orcamento_total_usd=2.0, papel="pesquisador",
                                                     autonomia="preparar")).json()
    assert not ruim["valido"] and ruim["confirmacao"] is None
    assert [(b["codigo"], b["campo"]) for b in ruim["bloqueios"]] == [("autonomia_acima_do_papel", "autonomia")]
    r = _tentar(c, "filho-papel-0001", pai, papel="redator", autonomia="agir")
    assert r.status_code == 422 and _codigo(r) == "autonomia_acima_do_papel"
    raiz = _tentar(c, "raiz-papel-0001", papel="checador", autonomia="preparar")
    assert raiz.status_code == 422 and _codigo(raiz) == "autonomia_acima_do_papel", "vale também para o pedido sem pai"
    assert h.state.db.scalar("SELECT COUNT(*) FROM pedidos") == antes, "nada gravado"


async def test_dentro_do_teto_cria_normalmente(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    pai = _pai(c, gatilhos=[DIARIO])
    assert _filho(c, pai, "filho-papel-0002", papel="redator", autonomia="preparar").status_code == 201
    assert _filho(c, pai, "filho-papel-0003", papel="checador").status_code == 201, "o padrão (observar) cabe em todo papel"
    porta = _filho(c, pai, "filho-papel-0004", papel="porta_voz", autonomia="agir")
    assert porta.status_code == 201, porta.text


async def test_estrutura_errada_aparece_antes_da_autonomia(h: Harness) -> None:
    """A pessoa corrige primeiro a árvore: com o pai inexistente, o código é o da estrutura."""
    _ligar(h)
    c = _cliente(h)
    r = _tentar(c, "filho-papel-0005", "fantasma", papel="pesquisador", autonomia="agir")
    assert r.status_code == 422 and _codigo(r) == "pai_inexistente"


async def test_desligada_continua_recusando_o_papel_e_nao_fala_de_autonomia(h: Harness) -> None:
    c = _cliente(h)
    r = _tentar(c, "raiz-papel-0002", papel="pesquisador", autonomia="agir")
    assert r.status_code == 422 and _codigo(r) == "colaboracao_desligada"


async def test_editar_a_autonomia_acima_do_papel_e_422_e_dentro_passa(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    pai = _pai(c, gatilhos=[DIARIO])
    f = _filho(c, pai, "filho-papel-0006", papel="redator")
    assert f.status_code == 201, f.text
    fid, versao = f.json()["id"], f.json()["versao"]
    acima = c.patch(f"/api/pedidos/{fid}", json={"autonomia": "agir", "versao": versao, "dry_run": True})
    assert acima.status_code == 422 and _codigo(acima) == "autonomia_acima_do_papel"
    dentro = c.patch(f"/api/pedidos/{fid}", json={"autonomia": "preparar", "versao": versao, "dry_run": True})
    assert dentro.status_code == 200, dentro.text


# =============================================================================================== laço
async def test_o_laco_decide_com_o_teto_do_papel_e_a_ocorrencia_registra_o_rebaixamento(h: Harness, monkeypatch) -> None:
    """Defesa: o pedido gravado acima do teto (aqui, por UPDATE direto, como um legado) não ganha a autonomia gravada no
    laço. A sobreposição é decidida com `observar`, e a ocorrência fechada diz que foi rebaixada, sem texto do pedido."""
    _ligar(h)
    c = _cliente(h)
    r = Relogio()
    h.state.pedidos.relogio = r
    laco = LacoDePedidos(h.state.db, h.state.runs, Lideranca(h.state.db, dono="laco-papel", relogio=r),
                         PedidosCfg(enabled=True, prazo_inicio_s=604_800), relogio=r)
    laco.notificar = h.state.pedidos_api.publicar
    pai = _pai(c, gatilhos=[DIARIO])
    f = _filho(c, pai, "filho-papel-0007", gatilhos=[AGORA], papel="pesquisador")
    assert f.status_code == 201, f.text
    fid = f.json()["id"]
    h.state.db.execute("UPDATE pedidos SET autonomia='agir' WHERE id=?", (fid,))
    vistas: dict[str, str] = {}
    original = modulo_laco.decidir

    def espia(sobreposicao, autonomia, abertas, devidas):
        devida_ids = {d.id for d in devidas}
        for o in h.state.db.query("SELECT id, pedido_id FROM pedido_ocorrencias"):
            if o["id"] in devida_ids:
                vistas[o["pedido_id"]] = autonomia
        return original(sobreposicao, autonomia, abertas, devidas)

    monkeypatch.setattr(modulo_laco, "decidir", espia)
    laco.uma_volta()
    assert vistas.get(fid) == "observar", "o laço decidiu com o teto do papel, não com o `agir` gravado"
    [o] = h.state.db.query("SELECT * FROM pedido_ocorrencias WHERE pedido_id=?", (fid,))
    assert o["estado"] == "despachada" and o["run_id"]
    _assentar(h.state.db, o["run_id"], "completed")
    laco.uma_volta()
    fechada = h.state.db.one("SELECT estado, motivo FROM pedido_ocorrencias WHERE id=?", (o["id"],))
    assert fechada["estado"] == "concluida"
    assert "autonomia rebaixada ao teto do papel pesquisador: agir → observar" in (fechada["motivo"] or "")
    assert h.state.db.scalar("SELECT objetivo FROM pedidos WHERE id=?", (fid,)) not in fechada["motivo"]


async def test_dentro_do_teto_a_ocorrencia_nao_ganha_nota(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    r = Relogio()
    h.state.pedidos.relogio = r
    laco = LacoDePedidos(h.state.db, h.state.runs, Lideranca(h.state.db, dono="laco-papel", relogio=r),
                         PedidosCfg(enabled=True, prazo_inicio_s=604_800), relogio=r)
    pai = _pai(c, gatilhos=[DIARIO])
    fid = _filho(c, pai, "filho-papel-0008", gatilhos=[AGORA], papel="pesquisador").json()["id"]
    laco.uma_volta()
    [o] = h.state.db.query("SELECT * FROM pedido_ocorrencias WHERE pedido_id=?", (fid,))
    _assentar(h.state.db, o["run_id"], "completed")
    laco.uma_volta()
    motivo = h.state.db.scalar("SELECT motivo FROM pedido_ocorrencias WHERE id=?", (o["id"],))
    assert "rebaixada" not in (motivo or "")
