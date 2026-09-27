"""Máquinas de estado da execução (`app/modules/execution/domain/states.py`), fase "só conferir" do design §16.

A prova de que as tabelas descrevem o comportamento ATUAL não mora aqui: é a suíte inteira, pelo fixture automático
`tests/conftest.py::_transicoes_dentro_da_tabela`, que reprova qualquer teste cuja execução real produza uma
transição de execução, objetivo ou tentativa fora da tabela. Aqui ficam a forma das tabelas, a regra `pode`, a
paridade com os enums de `app.models` e o comportamento do registro: avisa, conta e NÃO bloqueia.
"""
from __future__ import annotations

import json
from collections import Counter, deque
from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path

import pytest

from app.db import Database
from app.events import EventBus
from app.models import AttemptStatus, ObjectiveStatus, RunCreate, RunStatus, StepStatus
from app.modules.execution.domain.states import (ATTEMPT, MAQUINAS, OBJECTIVE, RUN, STEP, MaquinaDeEstados)
from app.taskqueue import repository as repo_mod
from app.taskqueue import states as fila
from app.taskqueue.repository import Repository

from .conftest import make_config

#: A métrica do repositório: (máquina, de, para) → quantas vezes caiu fora da tabela.
Contagem = Counter[tuple[str, str, str]]

#: máquina → (enum de `app.models` com o mesmo vocabulário, estado em que a linha nasce)
PARES: dict[str, tuple[type[StrEnum], str]] = {
    "run": (RunStatus, "planning"),
    "objective": (ObjectiveStatus, "pending"),
    "step": (StepStatus, "pending"),
    "attempt": (AttemptStatus, "running"),
}


@pytest.mark.parametrize("maquina", MAQUINAS, ids=lambda m: m.nome)
def test_vocabulario_igual_ao_do_enum(maquina: MaquinaDeEstados) -> None:
    """Estado novo no enum sem linha na tabela (ou o contrário) reprova: a tabela é o vocabulário inteiro."""
    enum, _ = PARES[maquina.nome]
    assert maquina.estados == {e.value for e in enum}
    destinos = set().union(*maquina.transicoes.values())
    assert destinos <= maquina.estados, f"destino desconhecido: {destinos - maquina.estados}"


@pytest.mark.parametrize("maquina", MAQUINAS, ids=lambda m: m.nome)
def test_todo_estado_e_alcancavel_do_nascimento(maquina: MaquinaDeEstados) -> None:
    """Estado que nenhuma aresta alcança seria linha morta (ou aresta esquecida)."""
    _, inicio = PARES[maquina.nome]
    vistos, pendentes = {inicio}, deque([inicio])
    while pendentes:
        for proximo in maquina.transicoes[pendentes.popleft()]:
            if proximo not in vistos:
                vistos.add(proximo)
                pendentes.append(proximo)
    assert vistos == maquina.estados


def test_pode_aceita_enum_e_texto_e_recusa_o_desconhecido() -> None:
    assert RUN.pode(RunStatus.planning, RunStatus.planned)
    assert RUN.pode("planning", "planned")
    assert RUN.pode(RunStatus.running, "paused")
    assert not RUN.pode("needs_input", "running")      # não há replanejamento: a pessoa cria outra execução
    assert not RUN.pode("inexistente", "running")
    assert not RUN.pode("running", "inexistente")


def test_mesmo_estado_so_onde_esta_escrito() -> None:
    """`pode(x, x)` não é regra geral: só as reafirmações que o código faz de propósito."""
    reafirma = {(m.nome, e) for m in MAQUINAS for e in m.estados if m.pode(e, e)}
    assert reafirma == {("run", "cancelling"), ("run", "completed_with_issues"), ("run", "cancelled"),
                        ("objective", "failed")}


def test_arestas_que_o_dominio_garante() -> None:
    # sucesso comprovado não reabre, em nenhum nível
    assert OBJECTIVE.transicoes["succeeded"] == frozenset()
    assert STEP.transicoes["succeeded"] == frozenset()
    # incerto só sai por decisão da pessoa, e nunca direto para sucesso do objetivo
    assert not OBJECTIVE.pode("uncertain", "succeeded")
    # tentativa não reabre: a próxima é outra linha
    assert ATTEMPT.terminais == ATTEMPT.estados - {"running"}
    # execução que terminou de verdade (`completed`, `failed`) não tem saída
    assert RUN.terminais == {"completed", "failed"}


def test_a_fila_impoe_a_mesma_tabela_de_etapa_do_dominio() -> None:
    """`taskqueue/states.py` continua IMPONDO a etapa, agora com a tabela do domínio (uma fonte só)."""
    assert {de.value: {p.value for p in para} for de, para in fila.STEP_TRANSITIONS.items()} == \
        {de: set(para) for de, para in STEP.transicoes.items()}
    assert all(isinstance(de, StepStatus) for de in fila.STEP_TRANSITIONS)
    for de, destinos in STEP.transicoes.items():
        for para in STEP.estados:
            if para in destinos:
                fila.check_transition(de, para)
            else:
                with pytest.raises(fila.InvalidTransition):
                    fila.check_transition(de, para)


def test_tabela_e_imutavel() -> None:
    with pytest.raises(TypeError):
        RUN.transicoes["completed"] = frozenset({"running"})  # type: ignore[index]


# ------------------------------------------------------------------ registro no repositório
@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)       # `db_dsn`: segue TEST_DATABASE_URL quando a suíte roda no PostgreSQL
    db.migrate()
    return Repository(db, EventBus(db), cfg.evidence_dir)


@pytest.fixture
def contagem_restaurada() -> Iterator[Contagem]:
    """O teste força transições fora da tabela DE PROPÓSITO: devolve a métrica ao que era, para o fixture automático
    do `conftest` (que reprova transição fora da tabela) não confundir a prova com um defeito."""
    antes = repo_mod.TRANSICOES_FORA_DA_TABELA.copy()
    yield antes
    repo_mod.TRANSICOES_FORA_DA_TABELA.clear()
    repo_mod.TRANSICOES_FORA_DA_TABELA.update(antes)


def _avisos(repo: Repository) -> list[dict[str, object]]:
    return repo.db.query("SELECT kind, level, message, run_id, data FROM events WHERE kind='log' AND level='warn'"
                         " AND message LIKE 'Transição de %' ORDER BY id")


def test_execucao_fora_da_tabela_avisa_conta_e_nao_bloqueia(repo: Repository,
                                                           contagem_restaurada: Contagem) -> None:
    row, _ = repo.create_run(RunCreate(command="abrir o app", instance_ids=["android-01"],
                                       idempotency_key="k-maquina-1", mode="plan"), simulated=True)
    run_id = row["id"]
    repo.set_run_status(run_id, RunStatus.planned)              # na tabela: nada a registrar
    assert _avisos(repo) == []
    repo.set_run_status(run_id, RunStatus.needs_input)          # planned → needs_input: fora da tabela
    assert repo.run_row(run_id)["status"] == "needs_input"      # NÃO bloqueou: a fase é só conferir
    avisos = _avisos(repo)
    assert len(avisos) == 1 and avisos[0]["run_id"] == run_id
    assert "planned → needs_input" in str(avisos[0]["message"])
    assert json.loads(str(avisos[0]["data"])) == {"state_machine": "run", "entity_id": run_id, "from": "planned",
                                                   "to": "needs_input"}
    assert repo_mod.TRANSICOES_FORA_DA_TABELA[("run", "planned", "needs_input")] == \
        contagem_restaurada[("run", "planned", "needs_input")] + 1


def test_objetivo_fora_da_tabela_avisa_e_nao_bloqueia(repo: Repository, contagem_restaurada: Contagem) -> None:
    row, _ = repo.create_run(RunCreate(command="abrir o app", instance_ids=["android-01"],
                                       idempotency_key="k-maquina-2", mode="plan"), simulated=True)
    oid = f"{row['id']}:android-01"
    # INSERT à mão com os tipos da migração 001 (K-029): TEXT nos ids e no status, INTEGER no plan_version.
    repo.db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,?)",
                    (oid, row["id"], "android-01", "succeeded", 1))
    repo.set_objective(oid, ObjectiveStatus.pending, detail="reabrir sucesso")   # sucesso não reabre: fora
    assert repo.objective_row(oid)["status"] == "pending"
    assert [a["message"] for a in _avisos(repo)] == \
        [f"Transição de objective fora da tabela: {oid} succeeded → pending (registrada, não bloqueada)"]
