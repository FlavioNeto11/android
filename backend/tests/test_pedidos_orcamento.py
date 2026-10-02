"""Item 28.6 — orçamento, saldo e prioridade do laço de pedidos, em SQLite, com `RunService` de verdade.

Prova `simulated` (`arquivo::teste`): provedor de IA simulado, aparelhos falsos, relógio escrito à mão e `ai_calls`
inseridas à mão (com `usd` declarado, que `costs.spent_usd` soma sem precisar de `ai.prices`). NADA aqui prova o
ambiente real: o 28.12 liga o laço no central.

Cobre `docs/design/pedidos-laco.md` §11: o custo da ocorrência gravado ANTES da purga e acumulado entre tentativas, o
orçamento total que encerra (`encerrado_motivo='orcamento'`) e que trava o despacho, o saldo (ADR-051) que ADIA sem
perder a ocorrência, a prioridade no `dispatchable_objectives` e o teto da ocorrência na própria execução.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio

from pydantic import ValidationError

from app.config import PedidosCfg
from app.models import RunCreate
from app.modules.pedidos.domain.orcamento import (custo_estimado, motivo_sem_orcamento, quantas_cabem,
                                                  teto_da_execucao)
from app.planning.provider import AIError, DecisionRequest
from app.modules.pedidos.infrastructure.laco import LacoDePedidos
from app.planning import costs
from app.taskqueue.travas import Lideranca

from .conftest import Harness
from .test_hospedeiro import _objetivo_pronto, _parque
from .test_hub_de_ia import SCREEN, FakeProvider, com_hub, ctx, roteador
from .test_pedidos_laco import _agora, _assentar, _estados, _horaria, _ocs, _runs
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


# =============================================================================================== prioridade
def _tres_execucoes(db, prioridades: dict[str, int]) -> None:
    """Três execuções prontas em aparelhos diferentes, criadas às 10:00, 10:30 e 11:00 de um mesmo dia."""
    _parque(db, hosted_by=None, ids=["prio-a", "prio-b", "prio-c"], base_idx=50)
    for run_id, aparelho, hora in (("run-a", "prio-a", "10:00"), ("run-b", "prio-b", "10:30"), ("run-c", "prio-c", "11:00")):
        _objetivo_pronto(db, aparelho, run_id)
        db.execute("UPDATE runs SET created_at=?, prioridade=? WHERE id=?",
                   (f"2026-09-22T{hora}:00Z", prioridades.get(run_id, 0), run_id))


async def test_prioridade_maior_passa_na_frente_e_a_igual_segue_a_ordem_de_chegada(h: Harness) -> None:
    db = h.state.db
    _tres_execucoes(db, {"run-c": 5, "run-b": 1})
    ordem = [o["run_id"] for o in h.state.repo.dispatchable_objectives() if o["run_id"].startswith("run-")]
    assert ordem == ["run-c", "run-b", "run-a"], "maior primeiro; a de prioridade 0 por último"


async def test_todo_o_legado_prioridade_zero_mantem_a_ordem_antiga(h: Harness) -> None:
    db = h.state.db
    _tres_execucoes(db, {})
    ordem = [o["run_id"] for o in h.state.repo.dispatchable_objectives() if o["run_id"].startswith("run-")]
    assert ordem == ["run-a", "run-b", "run-c"], "só `created_at`, como antes da 28.6"
    assert db.scalar("SELECT MIN(prioridade) FROM runs") == 0 == db.scalar("SELECT MAX(prioridade) FROM runs")


async def test_o_laco_grava_a_prioridade_na_execucao_e_o_padrao_e_zero(h: Harness, monkeypatch) -> None:
    r = Relogio()
    db = h.state.db
    laco = _laco(h, r)
    _agora(db, "ped1", r)
    laco.uma_volta()
    assert [x["prioridade"] for x in _runs(db)] == [0]
    # A costura: quando o pedido tiver o campo, `_prioridade` o devolve e a execução nasce com ele.
    _agora(db, "ped2", r)
    monkeypatch.setattr(LacoDePedidos, "_prioridade", staticmethod(lambda p: 7 if p["id"] == "ped2" else 0))
    laco.uma_volta()
    assert {x["pedido_id"]: x["prioridade"] for x in _runs(db)} == {"ped1": 0, "ped2": 7}


async def test_a_api_publica_nao_aceita_prioridade(h: Harness) -> None:
    with pytest.raises(ValidationError):
        RunCreate(command="abra o app", instance_ids=["android-01"], prioridade=9)


# =============================================================================================== orçamento (domínio)
def test_estimativa_e_a_mediana_das_ultimas_cinco_com_custo_ou_o_teto_da_ocorrencia() -> None:
    assert custo_estimado([0.2, 0.4, 0.3], None) == pytest.approx(0.3)
    assert custo_estimado([0.0, 0.0, 0.4], 9.0) == pytest.approx(0.4), "custo 0 não puxa a mediana para baixo"
    assert custo_estimado([9.0, 9.0, 9.0, 9.0, 9.0, 1.0, 1.0, 1.0, 1.0], None) == 9.0, "só as 5 primeiras (mais recentes)"
    assert custo_estimado([], 0.5) == 0.5, "primeira ocorrência: o teto dela"
    assert custo_estimado([0.0], None) == 0.0, "sem histórico nem teto: nada a comparar"


def test_motivo_sem_orcamento_e_quantas_cabem() -> None:
    assert motivo_sem_orcamento(None, 99.0, 5.0) is None, "sem orçamento total não há o que esgotar"
    assert motivo_sem_orcamento(1.0, 0.5, 0.0) is None and motivo_sem_orcamento(1.0, 0.5, 0.5) is None
    assert "esgotado" in motivo_sem_orcamento(1.0, 1.0, 0.0)
    assert "esgotado" in motivo_sem_orcamento(1.0, 1.5, 0.2)
    assert "não cobre" in motivo_sem_orcamento(1.0, 0.7, 0.4)
    assert quantas_cabem(None, 0.0, 0.5) is None and quantas_cabem(1.0, 0.0, 0.0) is None
    assert quantas_cabem(1.0, 0.0, 0.4) == 2 and quantas_cabem(1.0, 0.7, 0.4) == 0 and quantas_cabem(1.0, 1.0, 0.0) == 0


def test_teto_da_execucao_e_o_menor_dos_dois_restos() -> None:
    assert teto_da_execucao(None, 5.0, None, 1.0) is None, "sem orçamento nenhum: sem teto"
    assert teto_da_execucao(None, 0.0, 0.5, 0.1) == pytest.approx(0.4), "teto da ocorrência menos as tentativas anteriores"
    assert teto_da_execucao(2.0, 1.8, 0.5, 0.0) == pytest.approx(0.2), "o resto do total é menor"
    assert teto_da_execucao(1.0, 3.0, None, 0.0) == 0.0, "nunca negativo"


# =============================================================================================== orçamento (laço)
def _fechada(db, oid: str, custo: float, *, pid: str = "ped1", minuto: int = 0) -> None:
    """Uma ocorrência antiga, já fechada, com execução e custo gravado (o que o laço deixaria depois de fechar)."""
    db.execute("INSERT INTO pedido_ocorrencias(id, pedido_id, pedido_versao, gatilho_id, previsto_para, chave, origem,"
               " estado, tentativa, run_id, custo_usd, criada_em, terminada_em) VALUES (?,?,1,NULL,?,?,'manual',"
               "'concluida',1,?,?,?,?)",
               (oid, pid, f"2026-10-01T08:{minuto:02d}:00Z", f"chave-{oid}", f"run-{oid}", custo,
                "2026-10-01T08:00:00.000Z", f"2026-10-01T08:{minuto:02d}:30.000Z"))


def _pedido_horario(h: Harness, r: Relogio, **kw) -> tuple[LacoDePedidos, object]:
    r.t = datetime(2026, 10, 2, 12, 10, 0, tzinfo=UTC)
    db = h.state.db
    _horaria(db, dtstart="2026-10-02T10:00:00", janela=4 * 3600, coalescer=0, **kw)
    return _laco(h, r), db


async def test_orcamento_total_gasto_encerra_o_pedido_com_motivo_orcamento_e_nada_mais_despacha(h: Harness) -> None:
    r = Relogio()
    laco, db = _pedido_horario(h, r)
    _orcamento(db, total=1.0)
    laco.uma_volta()
    assert [e for _, e in _estados(db)][:3] == ["despachada", "pulada", "pulada"]
    [run] = _runs(db)
    _chamada(db, run["id"], 1.20)
    _assentar(db, run["id"], "completed")
    res = laco.uma_volta()
    assert res.encerrados == 1
    p = db.one("SELECT estado, encerrado_motivo FROM pedidos WHERE id='ped1'")
    assert (p["estado"], p["encerrado_motivo"]) == ("encerrado", "orcamento")
    assert _ocs(db)[0]["custo_usd"] == pytest.approx(1.20)
    pulada = [o for o in _ocs(db) if (o["motivo"] or "").startswith("orçamento:")]
    assert pulada and all(o["estado"] == "pulada" for o in pulada), "a prevista das 13h foi pulada com o motivo"
    r.avancar(4 * 3600)
    laco.uma_volta()
    assert len(_runs(db)) == 1, "encerrado por orçamento: nunca mais despacha"


async def test_o_que_resta_nao_cobre_a_estimativa_encerra_antes_de_estourar(h: Harness) -> None:
    r = Relogio()
    laco, db = _pedido_horario(h, r)
    _orcamento(db, total=1.0)
    laco.uma_volta()
    [run] = _runs(db)
    _chamada(db, run["id"], 0.60)                           # sobram 0.40 < ~0.60 (a mediana)
    _assentar(db, run["id"], "completed")
    laco.uma_volta()
    p = db.one("SELECT estado, encerrado_motivo FROM pedidos WHERE id='ped1'")
    assert (p["estado"], p["encerrado_motivo"]) == ("encerrado", "orcamento")


async def test_sem_orcamento_para_a_primeira_nada_e_despachado(h: Harness) -> None:
    """Gasto anterior (por exemplo de outra tentativa) já acima do total: a volta nem chama o planejador."""
    r = Relogio()
    db = h.state.db
    laco = _laco(h, r)
    _agora(db, "ped1", r)
    _orcamento(db, total=0.50)
    _fechada(db, "antiga", 0.60)
    res = laco.uma_volta()
    assert (res.despachadas, res.encerrados) == (0, 1) and _runs(db) == []
    pulada = [o for o in _ocs(db) if o["estado"] == "pulada"]
    assert len(pulada) == 1 and pulada[0]["motivo"].startswith("orçamento: orçamento total esgotado")
    assert db.scalar("SELECT encerrado_motivo FROM pedidos WHERE id='ped1'") == "orcamento"


async def test_com_execucao_aberta_espera_ela_fechar_para_encerrar(h: Harness) -> None:
    r = Relogio()
    laco, db = _pedido_horario(h, r)
    _orcamento(db, total=0.50)
    laco.uma_volta()
    [run] = _runs(db)
    _fechada(db, "antiga", 0.60)                            # o gasto ultrapassa o total com a execução ainda aberta
    laco.uma_volta()
    assert db.scalar("SELECT estado FROM pedidos WHERE id='ped1'") == "ativo", "a aberta ainda decide o custo final"
    _assentar(db, run["id"], "completed")
    laco.uma_volta()
    assert db.scalar("SELECT encerrado_motivo FROM pedidos WHERE id='ped1'") == "orcamento"


async def test_limite_conhecido_o_excesso_do_orcamento_e_no_maximo_o_custo_de_uma_ocorrencia_aberta(h: Harness) -> None:
    """LIMITE CONHECIDO (28.6, `pedidos-laco.md` §11): o custo só entra no total quando a ocorrência FECHA; por isso o
    excesso máximo do orçamento é o custo de UMA ocorrência aberta, limitado pelo teto da execução. Nenhuma outra sai."""
    r = Relogio()
    laco, db = _pedido_horario(h, r)
    _orcamento(db, total=1.0)
    for i in range(3):
        _fechada(db, f"a{i}", 0.2, minuto=i)                # gasto 0.6, mediana 0.2: sobram 0.4 e UMA cabe (a volta passa)
    laco.uma_volta()
    [run] = _runs(db)
    assert h.state.repo.teto_usd_da_execucao(run["id"]) == pytest.approx(0.4), "o teto da execução é o que sobra do total"
    _chamada(db, run["id"], 0.45)                           # a chamada em voo passa do teto pelo custo dela (só ela)
    laco.uma_volta()
    assert len(_runs(db)) == 1, "com uma aberta nenhuma outra é despachada, mesmo com `devida` acumulada"
    assert db.scalar("SELECT estado FROM pedidos WHERE id='ped1'") == "ativo", "a aberta ainda decide o custo final"
    _assentar(db, run["id"], "completed")
    laco.uma_volta()
    total = laco.repo.custo_total("ped1")
    assert total == pytest.approx(1.05) and total > 1.0, "o orçamento foi ultrapassado"
    assert total - 1.0 <= 0.45 + 1e-9, "…em no máximo o custo da ocorrência aberta"
    assert db.scalar("SELECT encerrado_motivo FROM pedidos WHERE id='ped1'") == "orcamento"
    r.avancar(4 * 3600)
    laco.uma_volta()
    assert len(_runs(db)) == 1, "e nada mais é despachado depois"


async def test_a_estimativa_limita_quantas_ocorrencias_saem_numa_volta(h: Harness) -> None:
    r = Relogio()
    r.t = datetime(2026, 10, 2, 12, 10, 0, tzinfo=UTC)
    db = h.state.db
    laco = _laco(h, r)
    _horaria(db, dtstart="2026-10-02T10:00:00", janela=4 * 3600, coalescer=0, sobreposicao="permitir_todas")
    _orcamento(db, total=2.0)
    for i in range(3):
        _fechada(db, f"a{i}", 0.5, minuto=i)                # gasto 1.5, mediana 0.5: sobram 0.5 = UMA ocorrência
    res = laco.uma_volta()
    assert res.despachadas == 1 and len(_runs(db)) == 1
    estados = [o["estado"] for o in _ocs(db) if o["gatilho_id"] == "g1"]
    assert estados.count("despachada") == 1 and estados.count("devida") >= 1, "as outras ficam `devida`, não se perdem"


async def test_pedido_sem_orcamento_nao_muda_de_comportamento_por_mais_que_gaste(h: Harness) -> None:
    r = Relogio()
    laco, db = _pedido_horario(h, r)
    laco.uma_volta()
    [run] = _runs(db)
    _chamada(db, run["id"], 500.0)
    _assentar(db, run["id"], "completed")
    r.avancar(3600)
    res = laco.uma_volta()
    assert res.encerrados == 0 and db.scalar("SELECT estado FROM pedidos WHERE id='ped1'") == "ativo"
    assert _ocs(db)[0]["custo_usd"] == pytest.approx(500.0)
    assert len(_runs(db)) == 2, "a ocorrência seguinte sai normalmente"


# =============================================================================================== teto na execução
async def test_o_teto_da_ocorrencia_barra_a_chamada_de_ia_da_execucao_antes_de_gastar(h: Harness, tmp_path) -> None:
    r = Relogio()
    db = h.state.db
    laco = _laco(h, r)
    _agora(db, "ped1", r)
    _orcamento(db, total=2.0, ocorrencia=0.50)
    laco.uma_volta()
    [run] = _runs(db)
    repo = h.state.repo
    assert repo.teto_usd_da_execucao(run["id"]) == pytest.approx(0.50)
    db.execute("UPDATE pedido_ocorrencias SET custo_usd=0.10 WHERE run_id=?", (run["id"],))   # tentativa anterior
    assert repo.teto_usd_da_execucao(run["id"]) == pytest.approx(0.40)

    cfg = com_hub(tmp_path, providers={"anthropic": {"kind": "anthropic"}}, roles={})
    rot = roteador(cfg, {"decide": FakeProvider("anthropic", "claude-opus-5")})
    rot.attach(repo=repo, settings_getter=lambda: SimpleNamespace(ai_max_usd_per_run=0.0, ai_max_usd_per_day=0.0))
    _chamada(db, run["id"], 0.20)
    await rot.decide(DecisionRequest(ctx=ctx(run["id"]), screen=SCREEN))        # 0,20 de 0,40: passa
    _chamada(db, run["id"], 0.25)
    with pytest.raises(AIError) as e:
        await rot.decide(DecisionRequest(ctx=ctx(run["id"]), screen=SCREEN))
    assert e.value.kind == "budget" and "Orçamento do pedido" in str(e.value)
    # Execução que não é de pedido: sem teto, a chamada passa como sempre.
    assert repo.teto_usd_da_execucao("run-sem-pedido") is None
    await rot.decide(DecisionRequest(ctx=ctx("run-sem-pedido"), screen=SCREEN))


async def test_execucao_de_pedido_sem_orcamento_nao_tem_teto(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    laco = _laco(h, r)
    _agora(db, "ped1", r)
    laco.uma_volta()
    assert h.state.repo.teto_usd_da_execucao(_runs(db)[0]["id"]) is None


# =============================================================================================== saldo (ADR-051)
async def test_saldo_baixo_adia_sem_perder_a_ocorrencia_nem_gastar_tentativa(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    laco = _laco(h, r)
    laco.adiar_por_saldo = lambda: "OpenAI: saldo estimado US$ 0,50, abaixo do bloqueio"
    _agora(db, "ped1", r)
    res = laco.uma_volta()
    assert (res.despachadas, res.adiadas, res.erros) == (0, 1, 0) and _runs(db) == []
    [o] = _ocs(db)
    assert (o["estado"], o["tentativa"], o["run_id"], o["dono"]) == ("devida", 0, None, None)
    assert o["resumo"].startswith("adiada: OpenAI: saldo estimado")
    # Ainda DENTRO da janela de recuperação (1800 s): adiar NÃO é falha, a ocorrência segue `devida`.
    r.avancar(600)
    assert laco.uma_volta().adiadas == 1
    assert [x["estado"] for x in _ocs(db)] == ["devida"] and db.scalar("SELECT estado FROM pedidos WHERE id='ped1'") == "ativo"
    # O saldo volta: a MESMA ocorrência é despachada, com a tentativa 1 e o resumo limpo.
    laco.adiar_por_saldo = lambda: None
    res = laco.uma_volta()
    assert res.despachadas == 1 and res.adiadas == 0
    [o2] = _ocs(db)
    assert (o2["id"], o2["estado"], o2["tentativa"], o2["resumo"]) == (o["id"], "despachada", 1, None)


async def test_saldo_baixo_alem_da_janela_vira_perdida_com_o_motivo_do_saldo(h: Harness) -> None:
    """28.6 (coordenador): esperar para sempre esconderia o atraso; passou de `previsto_para + J`, a ocorrência é `perdida`
    (visível, §7.5) e o motivo do saldo fica nela. O pedido segue vivo, sem execução criada."""
    r = Relogio()
    db = h.state.db
    laco = _laco(h, r)
    laco.adiar_por_saldo = lambda: "OpenAI: saldo estimado US$ 0,50, abaixo do bloqueio"
    _agora(db, "ped1", r)
    laco.uma_volta()
    r.avancar(1790)                 # janela de `agora` = 1800 s, contada de `previsto_para` (5 s antes do início)
    assert laco.uma_volta().perdidas == 0 and [x["estado"] for x in _ocs(db)] == ["devida"]
    r.avancar(30)
    res = laco.uma_volta()
    assert (res.perdidas, res.despachadas) == (1, 0) and _runs(db) == []
    [o] = _ocs(db)
    assert o["estado"] == "perdida" and o["motivo"].startswith("adiada por saldo além da janela: OpenAI: saldo estimado")
    assert o["terminada_em"] and o["run_id"] is None and o["tentativa"] == 0, "nunca virou execução"


async def test_a_leitura_do_saldo_que_falha_nao_segura_o_despacho(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    laco = _laco(h, r)

    def quebra() -> str | None:
        raise RuntimeError("sem banco de saldos")

    laco.adiar_por_saldo = quebra
    _agora(db, "ped1", r)
    assert laco.uma_volta().despachadas == 1


async def test_sem_saldo_injetado_ou_sem_o_que_despachar_o_saldo_nem_e_lido(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    laco = _laco(h, r)
    leituras: list[int] = []
    laco.adiar_por_saldo = lambda: leituras.append(1)       # type: ignore[assignment,return-value]
    laco.uma_volta()                                        # nenhum pedido: nada a despachar
    assert leituras == []
    _agora(db, "ped1", r)
    laco.uma_volta()
    assert leituras == [1], "uma leitura por volta, não uma por ocorrência"


async def test_motivo_de_adiamento_le_o_servico_de_saldos_sem_chamada_paga(tmp_path) -> None:
    from app.db import Database
    from app.modules.pedidos.infrastructure.saldo import motivo_de_adiamento
    from app.planning import saldos

    from .conftest import _dsn_de_teste
    from .test_saldos_de_ia import _cfg

    cfg = _cfg(tmp_path)
    db = Database(_dsn_de_teste() or tmp_path / "saldo-pedidos.sqlite3")
    db.migrate()
    assert motivo_de_adiamento(db, cfg, 1.0) is None, "sem leitura de saldo não há o que comparar"
    saldos.registrar_leitura(db, "openai", 3.0)             # a conta que paga `decide` e `verify`
    assert motivo_de_adiamento(db, cfg, 0.0) is None, "mínimo 0 desliga a regra; sem bloqueio do dono, libera"
    assert "abaixo do mínimo dos pedidos" in motivo_de_adiamento(db, cfg, 5.0)
    assert motivo_de_adiamento(db, cfg, 1.0) is None
    saldos.ajustar_regra(db, "openai", block_below=4.0)     # o bloqueio do dono adia mesmo com mínimo 0
    assert "barrada" in motivo_de_adiamento(db, cfg, 0.0)
    saldos.registrar_leitura(db, "openai", 8.0)
    assert motivo_de_adiamento(db, cfg, 1.0) is None
    db.close()


async def test_o_estado_liga_o_saldo_e_o_minimo_vem_da_configuracao(h: Harness) -> None:
    assert h.state.pedidos.adiar_por_saldo is not None
    assert PedidosCfg().saldo_minimo_usd == 0.0
