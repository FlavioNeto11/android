"""Item 31.24 (migração 088): o tempo dentro da tentativa passa a ter dono.

- C-3: toda chamada que passa por `StepExecutor._ai` grava quando foi entregue ao provedor (`started_at`) e quanto
  esperou a vaga de IA (`vaga_ms`).
- C-2: a decisão do ator grava o preparo (settle, observação, árvore, imagem, montagem do pedido).
- C-1: a ação escolhida pela IA aponta para a linha da chamada que a decidiu (`actions.ai_call_id`).
- C-4: a tentativa grava o juiz, a verificação e a evidência, no MESMO UPDATE da trilha da 045.
- C-5: `esperas` abre e fecha só quando o motivo muda; vaga e resposta do modelo não entram.
- Sobrecarga: contada em instruções ao banco, não em relógio (0 a mais por decisão e por ação, ≤ 2 por troca de motivo,
  0 quando nada muda).

Nível de prova: `simulated` (banco de teste pela fábrica, que segue `TEST_DATABASE_URL`; o executor no Harness da porta
5640, com aparelho falso e provedor simulado). Nada disto mede a latência do ambiente real.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.db import Database
from app.events import EventBus
from app.models import ObjectiveStatus
from app.planning.provider import PreparoDaDecisao, Usage
from app.taskqueue.latencia import MOTIVOS_DE_ESPERA, TemposDaTentativa, motivo_da_espera
from app.taskqueue.repository import Repository
from app.util import now_iso

from .conftest import Harness, make_config

TERMINAIS = ("completed", "completed_with_issues", "failed", "waiting_user")


def _repo(tmp_path: Path) -> tuple[Repository, Database]:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)          # `db_dsn`: segue `TEST_DATABASE_URL` e roda de verdade no PostgreSQL
    db.migrate()
    return Repository(db, EventBus(db), cfg.evidence_dir), db


def _objetivo(db: Database, oid: str = "o-1", run_id: str = "r-1") -> str:
    agora = now_iso()
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at)"
               " VALUES (?,?,?,?,?,?,?)", (run_id, f"k-{run_id}", "x", "plan", "running", "[]", agora))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status) VALUES (?,?,?,?)",
               (oid, run_id, "android-01", "pending"))
    return oid


def _esperas(db: Database) -> list[tuple[str, str | None, bool]]:
    return [(r["motivo"], r["run_id"], r["fim"] is not None)
            for r in db.query("SELECT motivo, run_id, fim FROM esperas ORDER BY id")]


@contextmanager
def _instrucoes(db: Database) -> Iterator[list[str]]:
    """Conta as instruções que chegam ao banco (o SQL de cada uma), trocando os métodos SÓ desta instância. Só a chamada
    de fora conta: `inserted_id` e `scalar` usam `one` por dentro, e isso é uma instrução só."""
    vistas: list[str] = []
    originais = {nome: getattr(db, nome) for nome in ("execute", "one", "scalar", "query", "inserted_id")}
    dentro = [0]

    def contar(nome: str) -> Callable[..., object]:
        def chamar(sql: str, *a: object, **kw: object) -> object:
            if not dentro[0]:
                vistas.append(sql)
            dentro[0] += 1
            try:
                return originais[nome](sql, *a, **kw)
            finally:
                dentro[0] -= 1
        return chamar

    for nome in originais:
        setattr(db, nome, contar(nome))
    try:
        yield vistas
    finally:
        for nome in originais:
            delattr(db, nome)


def _de(vistas: list[str], tabela: str) -> int:
    return sum(1 for sql in vistas if tabela in sql)


# ------------------------------------------------------------------ C-5: o vocabulário
@pytest.mark.parametrize("status, wait_reason, motivo", [
    ("pending", "device_slot", "aparelho"),
    ("running", "profile_limit", "perfil"),
    ("running", "rede", "rede"),
    ("pending", "pathfinder", "caminho"),
    ("waiting_user", None, "pessoa"),
    ("waiting_user", "device_slot", "pessoa"),       # o estado vem antes da espera tipada que sobrou
    ("running", "ai_capacity", None),                # já é `ai_calls.vaga_ms`
    ("running", "model_response", None),             # já é `ai_calls.ms`
    ("running", None, None),
    ("running", "motivo_novo", None),                # fora do mapa: não abre (a leitura o vê como "sem dono")
])
def test_motivo_da_espera(status: str, wait_reason: str | None, motivo: str | None) -> None:
    assert motivo_da_espera(status, wait_reason) == motivo
    assert motivo is None or motivo in MOTIVOS_DE_ESPERA


# ------------------------------------------------------------------ C-5: as transições do repositório
def test_esperas_abrem_e_fecham_so_quando_o_motivo_muda(tmp_path: Path) -> None:
    repo, db = _repo(tmp_path)
    oid = _objetivo(db)
    repo.note_waiting(oid, "aguardando o aparelho ligar", wait_reason="device_slot")
    repo.note_waiting(oid, "aguardando o aparelho ligar (2)", wait_reason="device_slot")    # só o texto mudou
    assert _esperas(db) == [("aparelho", "r-1", False)]
    repo.note_waiting(oid, "aguardando o limite do perfil", wait_reason="profile_limit")
    repo.note_waiting(oid, "aguardando vaga de IA", wait_reason="ai_capacity")              # perfil fecha, nada abre
    repo.note_waiting(oid, "aguardando resposta do modelo", wait_reason="model_response")
    repo.clear_wait_reason(oid)
    assert _esperas(db) == [("aparelho", "r-1", True), ("perfil", "r-1", True)]
    repo.set_objective(oid, ObjectiveStatus.running)
    repo.note_waiting(oid, "à espera da rede", wait_reason="rede")
    repo.clear_wait_reason(oid)                                                             # a rede convergiu
    repo.set_objective(oid, ObjectiveStatus.waiting_user, needs="faça o login")
    repo.set_objective(oid, ObjectiveStatus.cancelled)                                      # quem encerra é a pessoa
    assert _esperas(db) == [("aparelho", "r-1", True), ("perfil", "r-1", True), ("rede", "r-1", True),
                            ("pessoa", "r-1", True)]
    assert db.scalar("SELECT COUNT(*) FROM esperas WHERE fim < inicio") == 0


def test_espera_de_pessoa_atravessa_a_retomada(tmp_path: Path) -> None:
    """A pessoa resolve e o item volta à fila: a espera fecha na retomada, não no fim do objetivo (o "pendurado" de 7 h
    de 03/10 era isto, até o cancelamento)."""
    repo, db = _repo(tmp_path)
    oid = _objetivo(db)
    repo.set_objective(oid, ObjectiveStatus.running)
    repo.set_objective(oid, ObjectiveStatus.waiting_user, needs="aprove")
    assert _esperas(db) == [("pessoa", "r-1", False)]
    repo.set_objective(oid, ObjectiveStatus.pending)
    repo.note_waiting(oid, "aguardando o aparelho ligar", wait_reason="device_slot")
    assert _esperas(db) == [("pessoa", "r-1", True), ("aparelho", "r-1", False)]


# ------------------------------------------------------------------ C-3 e C-2: as colunas de `ai_calls`
def test_add_usage_devolve_o_id_e_grava_inicio_vaga_e_preparo(tmp_path: Path) -> None:
    repo, db = _repo(tmp_path)
    inicio = now_iso()
    preparo = PreparoDaDecisao(settle_ms=601, observacao_ms=1500, arvore_ms=1200, imagem_ms=250, prompt_ms=12)
    com = repo.add_usage(None, None, Usage(calls=1, input_tokens=10, output_tokens=5, role="decide", model="m", ms=2200,
                                           started_at=inicio, vaga_ms=3, preparo=preparo))
    sem = repo.add_usage(None, None, Usage(calls=1, input_tokens=10, output_tokens=5, role="curador", model="m",
                                           ms=900))
    nada = repo.add_usage(None, None, Usage())
    assert isinstance(com, int) and isinstance(sem, int) and com != sem and nada is None
    linhas = {r["id"]: r for r in db.query(
        "SELECT id, started_at, vaga_ms, prep_settle_ms, prep_observacao_ms, prep_arvore_ms, prep_imagem_ms,"
        " prep_prompt_ms FROM ai_calls")}
    c = linhas[com]
    assert (c["started_at"], c["vaga_ms"], c["prep_settle_ms"], c["prep_observacao_ms"], c["prep_arvore_ms"],
            c["prep_imagem_ms"], c["prep_prompt_ms"]) == (inicio, 3, 601, 1500, 1200, 250, 12)
    assert all(v is None for k, v in linhas[sem].items() if k != "id")      # fora do executor: NULOS


# ------------------------------------------------------------------ sobrecarga, em instruções
def test_sobrecarga_em_instrucoes_ao_banco(tmp_path: Path) -> None:
    repo, db = _repo(tmp_path)
    oid = _objetivo(db)
    repo.note_waiting(oid, "aguardando o aparelho ligar", wait_reason="device_slot")
    with _instrucoes(db) as vistas:                                  # nada muda: nem objetivo nem espera
        repo.note_waiting(oid, "aguardando o aparelho ligar", wait_reason="device_slot")
    assert _de(vistas, "esperas") == 0 and _de(vistas, "UPDATE objectives") == 0
    with _instrucoes(db) as vistas:                                  # só o texto: nenhuma escrita em `esperas`
        repo.note_waiting(oid, "aguardando o aparelho ligar (de novo)", wait_reason="device_slot")
    assert _de(vistas, "esperas") == 0
    with _instrucoes(db) as vistas:                                  # troca de motivo: fecha e abre
        repo.note_waiting(oid, "aguardando o limite do perfil", wait_reason="profile_limit")
    assert _de(vistas, "esperas") == 2
    with _instrucoes(db) as vistas:                                  # as duas anotações de cada chamada de IA
        repo.note_waiting(oid, "aguardando vaga de IA", wait_reason="ai_capacity")     # fecha a do perfil
        repo.note_waiting(oid, "aguardando resposta do modelo", wait_reason="model_response")
        repo.clear_wait_reason(oid)
    assert _de(vistas, "esperas") == 1
    with _instrucoes(db) as vistas:                                  # a próxima chamada de IA: nada em `esperas`
        repo.note_waiting(oid, "aguardando vaga de IA", wait_reason="ai_capacity")
        repo.note_waiting(oid, "aguardando resposta do modelo", wait_reason="model_response")
        repo.clear_wait_reason(oid)
    assert _de(vistas, "esperas") == 0
    # A decisão do ator: as colunas novas vão no MESMO INSERT (uma instrução em `ai_calls`, com ou sem preparo).
    for uso in (Usage(calls=1, role="decide", model="m", ms=1, started_at=now_iso(), vaga_ms=0,
                      preparo=PreparoDaDecisao(settle_ms=1, observacao_ms=1, arvore_ms=1, imagem_ms=None, prompt_ms=1)),
                Usage(calls=1, role="decide", model="m", ms=1)):
        with _instrucoes(db) as vistas:
            repo.add_usage(None, None, uso)
        assert _de(vistas, "ai_calls") == 1
    # A tentativa: o juiz e a evidência vão no MESMO UPDATE da trilha da 045.
    for tempos in (TemposDaTentativa(juiz_espera_ms=1500, verificacao_ms=4000, evidencia_ms=600), None):
        with _instrucoes(db) as vistas:
            repo.note_attempt_strategy("a-inexistente", "ai_actor", None, tempos)
        assert len(vistas) == 1 and "UPDATE attempts" in vistas[0]


# ------------------------------------------------------------------ o executor de ponta a ponta (simulado)
async def test_execucao_simulada_grava_os_tempos_e_liga_a_acao_a_decisao(harness: Harness) -> None:
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    db = harness.state.db
    assert db is not None
    chamadas = db.query("SELECT id, role, ts, started_at, vaga_ms, attempt_id, prep_settle_ms, prep_observacao_ms,"
                        " prep_arvore_ms, prep_imagem_ms, prep_prompt_ms FROM ai_calls WHERE run_id=? AND ok=1",
                        (run.id,))
    decides = [c for c in chamadas if c["role"] == "decide"]
    assert decides, "o plano simulado não chamou o ator: o teste não mediria nada"
    for c in chamadas:                                    # C-3: tudo o que passa por `_ai` tem início e vaga
        assert c["started_at"] is not None and c["started_at"] <= c["ts"] and c["vaga_ms"] >= 0, c["role"]
    for c in decides:                                     # C-2: o preparo de cada decisão
        assert None not in (c["prep_observacao_ms"], c["prep_arvore_ms"], c["prep_prompt_ms"])
        assert min(v for v in (c["prep_settle_ms"], c["prep_observacao_ms"], c["prep_arvore_ms"], c["prep_imagem_ms"],
                               c["prep_prompt_ms"]) if v is not None) >= 0
    for c in chamadas:
        if c["role"] != "decide":
            assert (c["prep_observacao_ms"], c["prep_prompt_ms"]) == (None, None), c["role"]
    # C-1: a ação da IA aponta para o decide DA MESMA tentativa; a receita e o executor não apontam para nada.
    por_id = {c["id"]: c for c in decides}
    acoes = db.query("SELECT a.attempt_id, a.source, a.ai_call_id FROM actions a JOIN attempts t ON t.id=a.attempt_id"
                     " JOIN steps s ON s.id=t.step_id WHERE s.run_id=?", (run.id,))
    da_ia = [a for a in acoes if a["source"] == "ai"]
    assert da_ia and all(a["ai_call_id"] in por_id and por_id[a["ai_call_id"]]["attempt_id"] == a["attempt_id"]
                         for a in da_ia)
    assert all(a["ai_call_id"] is None for a in acoes if a["source"] != "ai")
    # C-4: toda tentativa que passou pelo executor tem os três tempos (zero = medido, não houve).
    tentativas = db.query("SELECT t.juiz_espera_ms, t.verificacao_ms, t.evidencia_ms FROM attempts t"
                          " JOIN steps s ON s.id=t.step_id WHERE s.run_id=? AND t.finished_at IS NOT NULL", (run.id,))
    assert tentativas and all(min(t["juiz_espera_ms"], t["verificacao_ms"], t["evidencia_ms"]) >= 0
                              for t in tentativas)
    assert any(t["verificacao_ms"] > 0 for t in tentativas)
