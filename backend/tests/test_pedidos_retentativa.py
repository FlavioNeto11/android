"""Item 28.5 — tentativas por ocorrência, `incerta` e pausa por falhas seguidas, no laço de verdade (SQLite).

Prova `simulated` (`arquivo::teste`): `RunService` de verdade com o planejamento desligado, aparelhos falsos e relógio
escrito à mão; a execução é levada ao estado que o teste quer (`UPDATE runs`). NADA aqui prova o ambiente real (o 28.12
liga o laço no central). Regras puras em `test_pedidos_tentativas.py`.

Cobre `docs/design/pedidos-laco.md` §13: falha sem efeito → nova tentativa DEPOIS do atraso e com `chave:t2`; efeito
possível ou etapa `uncertain` → nunca repete (esta, `incerta`, leva o pedido a `aguardando_pessoa` com aviso);
`max_tentativas`; N falhas seguidas pausam com aviso e o sucesso zera a contagem; a nova tentativa passa pelo saldo e
pelo orçamento; duas voltas não criam duas tentativas; `max_ocorrencias` não barra a nova tentativa.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest_asyncio

from app.modules.pedidos.infrastructure.laco import LacoDePedidos

from .conftest import Harness
from .test_pedidos_laco import _agora, _assentar, _horaria, _ocs, _runs
from .test_pedidos_orcamento import _chamada, _laco as _laco_com_custo, _orcamento
from .test_travas import Relogio

UTC = timezone.utc


@pytest_asyncio.fixture
async def h(harness: Harness, monkeypatch) -> Harness:
    monkeypatch.setattr(harness.state.runs, "_spawn_planning", lambda run_id: None)
    return harness


def _laco(h: Harness, r: Relogio, **cfg) -> tuple[LacoDePedidos, list]:
    """O laço com o custo ligado (como o `AppState`) e a lista que recebe os avisos emitidos."""
    laco = _laco_com_custo(h, r, **cfg)
    avisos: list = []
    laco.avisar = avisos.append
    return laco, avisos


def _pedido_estado(db, pid: str = "ped1"):
    return db.one("SELECT estado, pausado_motivo, encerrado_motivo FROM pedidos WHERE id=?", (pid,))


def _acao_com_efeito(db, run_id: str, *, alvo: str = "android-01") -> None:
    """Uma ação da execução que PODE ter chegado ao aparelho (`actions.effect_possible=1`)."""
    oid, sid = f"{run_id}:{alvo}", f"{run_id}:{alvo}:v1:tocar"
    if not db.one("SELECT id FROM objectives WHERE id=?", (oid,)):
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,1)",
                   (oid, run_id, alvo, "failed"))
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " postcondition, timeout_s, max_attempts, status) VALUES (?,?,?,?,1,1,?,?,?,'{}',60,1,'failed')",
               (sid, run_id, oid, alvo, "tocar", "Tocar", "tocar"))
    db.execute("INSERT INTO attempts(id, step_id, number, status, started_at) VALUES (?,?,1,'failed',?)",
               (f"{sid}:a1", sid, "2026-10-02T12:00:00.000Z"))
    db.execute("INSERT INTO actions(attempt_id, seq, tool, args, status, effect_possible, intent_at)"
               " VALUES (?,1,'tap','{}','done',1,?)", (f"{sid}:a1", "2026-10-02T12:00:00.000Z"))


def _falhar_a_ultima(db, detalhe: str = "o app travou") -> str:
    run = _runs(db)[-1]
    _assentar(db, run["id"], "failed", detalhe)
    return run["id"]


# =============================================================================================== nova tentativa
async def test_falha_sem_efeito_vira_nova_tentativa_depois_do_atraso_e_com_a_chave_t2(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco, _ = _laco(h, r, retentativa_base_s=60, retentativa_teto_s=900)
    _agora(db, "ped1", r, max_tentativas=2)
    laco.uma_volta()
    [o] = _ocs(db)
    run1 = _falhar_a_ultima(db)
    custo_antes = float(o["custo_usd"])

    res = laco.uma_volta()
    assert (res.retentadas, res.fechadas) == (1, 0), "uma nova tentativa não é um fechamento terminal"
    [o] = _ocs(db)
    assert o["estado"] == "devida" and o["tentativa"] == 1 and o["run_id"] == run1, "a MESMA linha, `tentativa` intacta"
    assert "tentativa 1 falhou (o app travou)" in o["motivo"]
    esperado = r.t + timedelta(seconds=60)
    assert o["terminada_em"].startswith(esperado.strftime("%Y-%m-%dT%H:%M:%S")), "atraso base de 60 s"
    assert float(o["custo_usd"]) >= custo_antes
    assert _pedido_estado(db)["estado"] == "ativo", "uma falha repetida não pausa nem encerra o pedido"

    r.avancar(59)
    assert laco.uma_volta().despachadas == 0 and len(_runs(db)) == 1, "antes do atraso ninguém a despacha"
    r.avancar(2)
    assert laco.uma_volta().despachadas == 1
    [o] = _ocs(db)
    assert o["estado"] == "despachada" and o["tentativa"] == 2 and o["terminada_em"] is None
    runs = _runs(db)
    assert [x["idempotency_key"] for x in runs] == [o["chave"] + ":t1", o["chave"] + ":t2"]
    assert runs[1]["ocorrencia_id"] == o["id"] and o["run_id"] == runs[1]["id"]

    _assentar(db, runs[1]["id"], "completed")
    assert laco.uma_volta().fechadas == 1
    assert _ocs(db)[0]["estado"] == "concluida"


async def test_o_atraso_cresce_a_cada_falha_da_mesma_ocorrencia(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco, _ = _laco(h, r, retentativa_base_s=60, retentativa_teto_s=900)
    _agora(db, "ped1", r, max_tentativas=3)
    laco.uma_volta()
    _falhar_a_ultima(db)
    laco.uma_volta()
    r.avancar(61)
    laco.uma_volta()
    _falhar_a_ultima(db)
    t0 = r.t
    laco.uma_volta()
    [o] = _ocs(db)
    assert o["estado"] == "devida" and o["tentativa"] == 2
    assert o["terminada_em"].startswith((t0 + timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%S")), "2ª falha: 2 x base"


async def test_max_tentativas_e_respeitado_a_ultima_falha_e_definitiva(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco, _ = _laco(h, r)
    _agora(db, "ped1", r, max_tentativas=2)
    laco.uma_volta()
    _falhar_a_ultima(db)
    laco.uma_volta()
    r.avancar(61)
    laco.uma_volta()
    _falhar_a_ultima(db, "de novo")
    res = laco.uma_volta()
    assert (res.fechadas, res.retentadas) == (1, 0)
    [o] = _ocs(db)
    assert o["estado"] == "falhou" and o["tentativa"] == 2 and "esgotou as 2 tentativas" in o["motivo"]
    r.avancar(3600)
    laco.uma_volta()
    assert len(_runs(db)) == 2, "nenhuma terceira execução"


# =============================================================================================== efeito e incerteza
async def test_falha_com_efeito_possivel_nao_repete(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco, _ = _laco(h, r)
    _agora(db, "ped1", r, max_tentativas=2)
    laco.uma_volta()
    _acao_com_efeito(db, _falhar_a_ultima(db))
    res = laco.uma_volta()
    assert (res.fechadas, res.retentadas) == (1, 0)
    [o] = _ocs(db)
    assert o["estado"] == "falhou" and "efeito externo possível" in o["motivo"]
    r.avancar(3600)
    laco.uma_volta()
    assert len(_runs(db)) == 1


async def test_execucao_purgada_conta_como_efeito_possivel_e_nao_repete(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco, _ = _laco(h, r)
    _agora(db, "ped1", r, max_tentativas=2)
    laco.uma_volta()
    db.execute("DELETE FROM runs WHERE id=?", (_runs(db)[-1]["id"],))
    laco.uma_volta()
    [o] = _ocs(db)
    assert o["estado"] == "falhou" and "não existe mais" in o["motivo"]


async def test_etapa_uncertain_leva_o_pedido_a_aguardando_pessoa_com_aviso_e_nunca_repete(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco, avisos = _laco(h, r)
    _agora(db, "ped1", r, max_tentativas=3)
    laco.uma_volta()
    run = _runs(db)[-1]
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,'uncertain',1)",
               (f"{run['id']}:android-01", run["id"], "android-01"))
    _assentar(db, run["id"], "completed_with_issues")
    res = laco.uma_volta()
    assert (res.fechadas, res.aguardando, res.retentadas) == (1, 1, 0)
    [o] = _ocs(db)
    assert o["estado"] == "incerta" and "objetivo incerto" in o["motivo"]
    assert _pedido_estado(db)["estado"] == "aguardando_pessoa"
    [a] = avisos
    assert (a["tipo"], a["requer_pessoa"], a["nivel"], a["pedido_id"], a["ocorrencia_id"]) == (
        "ocorrencia_incerta", True, "warn", "ped1", o["id"])
    assert a["id"] == f"{o['id']}:ocorrencia_incerta" and a["lido_em"] is None

    r.avancar(3600)
    laco.uma_volta()
    laco.uma_volta()
    assert len(_runs(db)) == 1 and [x["estado"] for x in _ocs(db)] == ["incerta"], "nunca repete sozinha"
    assert len(avisos) == 1, "um aviso só"


# =============================================================================================== falhas seguidas
def _ciclo(h: Harness, r: Relogio, laco: LacoDePedidos, desfecho: str) -> None:
    """Uma hora: despacha a ocorrência, a execução assenta com `desfecho` e a volta seguinte fecha."""
    db = h.state.db
    laco.uma_volta()
    run = _runs(db)[-1]
    _assentar(db, run["id"], desfecho, "falhou de propósito" if desfecho == "failed" else None)
    laco.uma_volta()
    r.avancar(3600)


async def test_tres_falhas_seguidas_pausam_o_pedido_e_emitem_o_aviso(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    r.t = datetime(2026, 10, 2, 12, 0, 5, tzinfo=UTC)
    laco, avisos = _laco(h, r)
    _horaria(db, dtstart="2026-10-02T12:00:00", max_tentativas=1, pausa_por_falha=3)
    _ciclo(h, r, laco, "failed")
    _ciclo(h, r, laco, "failed")
    assert _pedido_estado(db)["estado"] == "ativo" and not avisos, "duas falhas ainda não pausam"
    _ciclo(h, r, laco, "failed")
    p = _pedido_estado(db)
    assert (p["estado"], p["pausado_motivo"]) == ("pausado", "3 falhas seguidas")
    [a] = avisos
    assert (a["tipo"], a["requer_pessoa"], a["pedido_id"]) == ("pausa_automatica", False, "ped1")
    assert a["dados"] == {"falhas_seguidas": 3} and "3 falhas seguidas" in a["mensagem"]
    assert sum(1 for o in _ocs(db) if o["estado"] == "falhou") == 3
    n = len(_runs(db))
    laco.uma_volta()
    laco.uma_volta()
    assert len(_runs(db)) == n, "pausado não despacha mais"
    assert len(avisos) == 1


async def test_o_sucesso_zera_a_contagem_de_falhas_seguidas(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    r.t = datetime(2026, 10, 2, 12, 0, 5, tzinfo=UTC)
    laco, avisos = _laco(h, r)
    _horaria(db, dtstart="2026-10-02T12:00:00", max_tentativas=1, pausa_por_falha=3)
    for desfecho in ("failed", "failed", "completed", "failed", "failed"):
        _ciclo(h, r, laco, desfecho)
    assert _pedido_estado(db)["estado"] == "ativo" and not avisos, "F F S F F: só duas seguidas"
    _ciclo(h, r, laco, "failed")
    assert _pedido_estado(db)["estado"] == "pausado" and len(avisos) == 1


async def test_a_ocorrencia_repetida_conta_uma_falha_so_quando_a_ultima_tentativa_tambem_falha(h: Harness) -> None:
    """Falha que ganhou nova tentativa não é falha seguida: a ocorrência continua `devida` e só o fim dela conta."""
    r, db = Relogio(), h.state.db
    r.t = datetime(2026, 10, 2, 12, 0, 5, tzinfo=UTC)
    laco, avisos = _laco(h, r, retentativa_base_s=60)
    _horaria(db, dtstart="2026-10-02T12:00:00", max_tentativas=2, pausa_por_falha=2)
    laco.uma_volta()
    _falhar_a_ultima(db)
    laco.uma_volta()
    r.avancar(61)
    laco.uma_volta()
    _falhar_a_ultima(db)
    laco.uma_volta()
    assert [o["estado"] for o in _ocs(db) if o["estado"] != "prevista"] == ["falhou"]
    assert _pedido_estado(db)["estado"] == "ativo" and not avisos, "uma ocorrência falhou: ainda uma só"


# =============================================================================================== orçamento, saldo, máximo
async def test_a_nova_tentativa_passa_pelo_saldo_e_fica_devida_sem_gastar_enquanto_ele_adia(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco, _ = _laco(h, r, retentativa_base_s=60)
    _agora(db, "ped1", r, max_tentativas=2)
    laco.uma_volta()
    _falhar_a_ultima(db)
    laco.uma_volta()
    laco.adiar_por_saldo = lambda: "saldo baixo"
    r.avancar(61)
    assert laco.uma_volta().adiadas == 1
    [o] = _ocs(db)
    assert o["estado"] == "devida" and o["resumo"] == "adiada: saldo baixo" and len(_runs(db)) == 1
    laco.adiar_por_saldo = None
    assert laco.uma_volta().despachadas == 1 and _ocs(db)[0]["tentativa"] == 2


async def test_a_nova_tentativa_adiada_por_saldo_conta_a_janela_a_partir_do_atraso_e_depois_vira_perdida(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco, _ = _laco(h, r, retentativa_base_s=60)
    _agora(db, "ped1", r, max_tentativas=2)
    laco.uma_volta()
    _falhar_a_ultima(db)
    laco.uma_volta()
    laco.adiar_por_saldo = lambda: "saldo baixo"
    r.avancar(1830)         # além de `previsto_para + 1800 s` (5 s antes do início), mas aquém de `atraso (60 s) + 1800 s`
    assert laco.uma_volta().perdidas == 0, "a janela da repetição parte do atraso, não do instante previsto"
    [o] = _ocs(db)
    assert o["estado"] == "devida"
    r.avancar(40)
    assert laco.uma_volta().perdidas == 1
    [o] = _ocs(db)
    assert o["estado"] == "perdida" and o["motivo"] == "adiada por saldo além da janela: saldo baixo"


async def test_sem_orcamento_para_outra_tentativa_a_falha_e_definitiva_e_o_pedido_encerra(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco, _ = _laco(h, r)
    _agora(db, "ped1", r, max_tentativas=3)
    _orcamento(db, total=0.10)
    laco.uma_volta()
    run_id = _runs(db)[-1]["id"]
    _chamada(db, run_id, 0.10)
    _assentar(db, run_id, "failed", "estourou")
    laco.uma_volta()
    [o] = _ocs(db)
    assert o["estado"] == "falhou" and "orçamento total esgotado" in o["motivo"] and abs(float(o["custo_usd"]) - 0.10) < 1e-9
    laco.uma_volta()
    assert _pedido_estado(db)["encerrado_motivo"] == "orcamento" and len(_runs(db)) == 1


async def test_a_tentativa_que_cabe_no_orcamento_repete_e_o_custo_acumula(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco, _ = _laco(h, r, retentativa_base_s=60)
    _agora(db, "ped1", r, max_tentativas=2)
    _orcamento(db, total=1.00)
    laco.uma_volta()
    run1 = _runs(db)[-1]["id"]
    _chamada(db, run1, 0.10)
    _assentar(db, run1, "failed", "x")
    laco.uma_volta()
    assert abs(float(_ocs(db)[0]["custo_usd"]) - 0.10) < 1e-9
    r.avancar(61)
    laco.uma_volta()
    run2 = _runs(db)[-1]["id"]
    assert run2 != run1
    _chamada(db, run2, 0.05)
    _assentar(db, run2, "completed")
    laco.uma_volta()
    [o] = _ocs(db)
    assert o["estado"] == "concluida" and abs(float(o["custo_usd"]) - 0.15) < 1e-9, "custo das duas tentativas"


async def test_duas_voltas_no_mesmo_instante_nao_criam_duas_tentativas(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco, _ = _laco(h, r, retentativa_base_s=60)
    outro, _ = _laco(h, r, dono="laco-b", retentativa_base_s=60)
    _agora(db, "ped1", r, max_tentativas=2)
    laco.uma_volta()
    _falhar_a_ultima(db)
    laco.uma_volta()
    r.avancar(61)
    laco.uma_volta()
    laco.uma_volta()
    outro.uma_volta()
    assert len(_runs(db)) == 2 and len(_ocs(db)) == 1
    assert [x["idempotency_key"][-3:] for x in _runs(db)] == [":t1", ":t2"]


async def test_o_maximo_de_ocorrencias_nao_barra_a_nova_tentativa_da_que_ja_virou_execucao(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco, _ = _laco(h, r, retentativa_base_s=60)
    _agora(db, "ped1", r, max_tentativas=2, max_oc=1)
    laco.uma_volta()
    _falhar_a_ultima(db)
    laco.uma_volta()
    r.avancar(61)
    laco.uma_volta()
    [o] = _ocs(db)
    assert o["estado"] == "despachada" and o["tentativa"] == 2, "executadas já é 1 = o máximo, e a repetição sai"
    assert _pedido_estado(db)["estado"] == "ativo", "nem o pedido encerra com a repetição pendente"
