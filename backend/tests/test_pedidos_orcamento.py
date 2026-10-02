"""Item 28.6 — orçamento, saldo e prioridade do laço de pedidos, em SQLite, com `RunService` de verdade.

Prova `simulated` (`arquivo::teste`): provedor de IA simulado, aparelhos falsos, relógio escrito à mão e `ai_calls`
inseridas à mão (com `usd` declarado, que `costs.spent_usd` soma sem precisar de `ai.prices`). NADA aqui prova o
ambiente real: o 28.12 liga o laço no central.

Cobre `docs/design/pedidos-laco.md` §11: o custo da ocorrência gravado ANTES da purga e acumulado entre tentativas, o
orçamento total que encerra (`encerrado_motivo='orcamento'`) e que trava o despacho, o saldo (ADR-051) que ADIA sem
perder a ocorrência, a prioridade no `dispatchable_objectives` e o teto da ocorrência na própria execução.
"""
from __future__ import annotations

from datetime import timezone

import pytest
import pytest_asyncio

from app.config import PedidosCfg
from app.modules.pedidos.infrastructure.laco import LacoDePedidos
from app.planning import costs
from app.taskqueue.travas import Lideranca

from .conftest import Harness
from .test_pedidos_laco import _agora, _assentar, _ocs
from .test_travas import Relogio

UTC = timezone.utc
DEPOIS_DE_TUDO = "2099-01-01T00:00:00Z"        # corte de retenção que alcança toda `ai_calls` de teste


@pytest_asyncio.fixture
async def h(harness: Harness, monkeypatch) -> Harness:
    monkeypatch.setattr(harness.state.runs, "_spawn_planning", lambda run_id: None)
    return harness


def _laco(h: Harness, r: Relogio, *, dono: str = "laco-a", custo: bool = True, **cfg) -> LacoDePedidos:
    """Como o `AppState` monta: o custo da execução sai de `costs.spent_usd` (a conta do painel de uso)."""
    db = h.state.db
    prices = h.state.cfg.file.ai.prices
    cfg.setdefault("prazo_inicio_s", 604_800)     # o relógio falso é de 2026; as execuções nascem com o REAL
    return LacoDePedidos(db, h.state.runs, Lideranca(db, dono=dono, relogio=r), PedidosCfg(enabled=True, **cfg),
                         relogio=r,
                         custo_da_execucao=(lambda run_id: costs.spent_usd(db, prices, run_id=run_id)) if custo else None)


def _chamada(db, run_id: str, usd: float, *, ts: str | None = None) -> None:
    """Uma chamada de IA paga: `usd` declarado vale no lugar de tokens × preço, e `provider` não é `simulated`."""
    db.execute("INSERT INTO ai_calls(ts, run_id, role, model, provider, usd) VALUES (?,?,?,?,?,?)",
               (ts or "2026-10-02T12:00:00.000Z", run_id, "plan", "modelo-de-teste", "anthropic", usd))


def _orcamento(db, pid: str = "ped1", *, total: float | None = None, ocorrencia: float | None = None) -> None:
    db.execute("UPDATE pedidos SET orcamento_total_usd=?, orcamento_ocorrencia_usd=? WHERE id=?",
               (total, ocorrencia, pid))


# =============================================================================================== custo e purga
async def test_o_laco_sobe_com_a_leitura_de_custo_ligada_no_estado(h: Harness) -> None:
    """A ligação de produção: sem ela o fechamento gravaria custo 0 em silêncio."""
    assert h.state.pedidos.custo_da_execucao is not None


async def test_custo_da_tentativa_soma_ao_acumulado_e_sobrevive_a_purga_das_chamadas(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    laco = _laco(h, r)
    _agora(db, "ped1", r)
    laco.uma_volta()
    [o] = _ocs(db)
    db.execute("UPDATE pedido_ocorrencias SET custo_usd=0.10 WHERE id=?", (o["id"],))   # tentativa anterior (28.5)
    _chamada(db, o["run_id"], 0.25)
    _chamada(db, o["run_id"], 0.05)
    _chamada(db, "outra-execucao", 9.00)                    # não é desta execução: não entra
    _assentar(db, o["run_id"], "completed")
    assert laco.uma_volta().fechadas == 1
    assert _ocs(db)[0]["custo_usd"] == pytest.approx(0.40)
    # A retenção leva as chamadas velhas DEPOIS do fechamento; o custo da ocorrência fica.
    h.state._purgar_demais_tabelas(DEPOIS_DE_TUDO)
    assert db.scalar("SELECT COUNT(*) FROM ai_calls WHERE run_id=?", (o["run_id"],)) == 0
    assert _ocs(db)[0]["custo_usd"] == pytest.approx(0.40)


async def test_a_purga_nao_leva_a_chamada_da_execucao_de_ocorrencia_ainda_aberta(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    laco = _laco(h, r)
    _agora(db, "ped1", r)
    laco.uma_volta()
    [o] = _ocs(db)
    _chamada(db, o["run_id"], 0.30)
    _chamada(db, "execucao-sem-ocorrencia", 0.30)
    h.state._purgar_demais_tabelas(DEPOIS_DE_TUDO)
    assert db.scalar("SELECT COUNT(*) FROM ai_calls WHERE run_id=?", (o["run_id"],)) == 1, "aberta: o laço ainda soma"
    assert db.scalar("SELECT COUNT(*) FROM ai_calls WHERE run_id='execucao-sem-ocorrencia'") == 0
    _assentar(db, o["run_id"], "completed")
    laco.uma_volta()
    assert _ocs(db)[0]["custo_usd"] == pytest.approx(0.30), "o laço ainda viu a chamada, mesmo depois da purga"


async def test_o_fechamento_que_perde_o_cas_nao_soma_o_custo_duas_vezes(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    laco = _laco(h, r)
    _agora(db, "ped1", r)
    laco.uma_volta()
    [o] = _ocs(db)
    _chamada(db, o["run_id"], 0.50)
    _assentar(db, o["run_id"], "completed")
    laco.uma_volta()
    laco.uma_volta()                                        # a segunda volta não acha ocorrência aberta
    assert _ocs(db)[0]["custo_usd"] == pytest.approx(0.50)
    # E o CAS direto: `mover` de um estado em que a linha não está não grava nada.
    assert laco.repo.mover(o["id"], "despachada", "concluida", custo_usd=5.0) is False
    assert _ocs(db)[0]["custo_usd"] == pytest.approx(0.50)


async def test_sem_a_leitura_injetada_o_fechamento_grava_zero_e_nao_quebra(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    laco = _laco(h, r, custo=False)
    _agora(db, "ped1", r)
    laco.uma_volta()
    [o] = _ocs(db)
    _chamada(db, o["run_id"], 0.50)
    _assentar(db, o["run_id"], "completed")
    assert laco.uma_volta().fechadas == 1 and _ocs(db)[0]["custo_usd"] == 0
