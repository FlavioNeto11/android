"""Item 28.4 (D3, A4) — a execução nasce LIGADA ao pedido pelo parâmetro interno de `RunService.create`.

A API pública não ganha campo (`RunCreate` segue `extra="forbid"`); a ligação vai no MESMO `INSERT` de `runs`.
Prova `simulated`: `RunService` com o provedor simulado do harness e o planejamento desligado.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import RunCreate

from .conftest import COMMAND, Harness


def _req(chave: str) -> RunCreate:
    return RunCreate(command=COMMAND, instance_ids=["android-01"], idempotency_key=chave)


def test_a4_a_api_publica_continua_recusando_campo_de_pedido() -> None:
    """A4: a ligação com o pedido não é do contrato público; `extra="forbid"` devolve 422 em vez de aceitar calado."""
    base = {"command": COMMAND, "instance_ids": ["android-01"], "idempotency_key": "chave-de-teste-1"}
    RunCreate(**base)
    for extra in ({"pedido_id": "p1"}, {"ocorrencia_id": "o1"}, {"origem": ["p1", "o1"]}, {"prioridade": 5}):
        with pytest.raises(ValidationError):
            RunCreate(**base, **extra)
    assert "pedido_id" not in RunCreate.model_fields and "ocorrencia_id" not in RunCreate.model_fields


async def test_a_execucao_criada_com_origem_grava_pedido_e_ocorrencia_no_mesmo_insert(harness: Harness,
                                                                                      monkeypatch) -> None:
    runs, repo = harness.state.runs, harness.state.repo
    monkeypatch.setattr(runs, "_spawn_planning", lambda run_id: None)        # sem planejador: só a criação
    comum = runs.create(_req("chave-comum-0001"))
    ligada = runs.create(_req("ped:p1:g1:2026-10-02T12:00:00Z:t1"), origem=("p1", "o1"))
    linha = repo.db.one("SELECT pedido_id, ocorrencia_id, prioridade FROM runs WHERE id=?", (ligada.id,))
    assert (linha["pedido_id"], linha["ocorrencia_id"], linha["prioridade"]) == ("p1", "o1", 0)
    sem = repo.db.one("SELECT pedido_id, ocorrencia_id FROM runs WHERE id=?", (comum.id,))
    assert (sem["pedido_id"], sem["ocorrencia_id"]) == (None, None), "o legado continua sem pedido"


async def test_repetir_a_criacao_com_a_mesma_chave_devolve_a_mesma_execucao_e_mantem_a_ligacao(harness: Harness,
                                                                                               monkeypatch) -> None:
    runs, repo = harness.state.runs, harness.state.repo
    monkeypatch.setattr(runs, "_spawn_planning", lambda run_id: None)
    primeira = runs.create(_req("ped:p1:g1:2026-10-02T12:00:00Z:t1"), origem=("p1", "o1"))
    segunda = runs.create(_req("ped:p1:g1:2026-10-02T12:00:00Z:t1"), origem=("p1", "o1"))
    assert segunda.deduplicated and segunda.id == primeira.id
    assert repo.db.scalar("SELECT COUNT(*) FROM runs WHERE pedido_id='p1'") == 1


async def test_a_execucao_que_nasce_em_needs_input_tambem_leva_a_origem(harness: Harness, monkeypatch) -> None:
    """`_criar_com_perguntas` é o outro chamador de `create_run`: pergunta de destino conta como despachada."""
    runs, repo = harness.state.runs, harness.state.repo
    visto: dict = {}
    original = repo.create_run

    def espia(req, **kw):
        visto.update(kw)
        return original(req, **kw)

    monkeypatch.setattr(repo, "create_run", espia)
    monkeypatch.setattr(runs, "_spawn_planning", lambda run_id: None)
    monkeypatch.setattr(runs, "_pedir_resposta", lambda run_id, perguntas: None)
    resolucao = type("R", (), {"alvos": [], "perguntas": [type("P", (), {"as_dict": lambda self: {"question": "?"}})()],
                               "app_ids": (), "instance_ids": ["android-01"]})()
    monkeypatch.setattr(runs, "_resolver", lambda *a, **k: (resolucao, COMMAND))
    runs.create(_req("ped:p2:g1:2026-10-02T12:00:00Z:t1"), origem=("p2", "o2"))
    assert visto["origem"] == ("p2", "o2")
