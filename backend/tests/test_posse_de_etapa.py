"""Posse de etapa entre dois backends no MESMO banco.

Existe por causa de um defeito que o PostgreSQL tornou possível: `interrupted_steps` pegava toda etapa `running`,
sem perguntar de quem era. Com um processo só isso nunca doeu — havia um dono e ele era o único. Com dois, o
segundo a subir reconciliaria as etapas VIVAS do primeiro: devolveria para `ready` o que estava sendo executado
naquele instante, e o primeiro perderia o trabalho em andamento sem nem saber.
"""
from __future__ import annotations

from pathlib import Path

from app.db import Database
from app.events import EventBus
from app.taskqueue.repository import Repository
from app.util import iso_in

from .conftest import make_config


def _dois_backends(tmp_path: Path) -> tuple[Repository, Repository, Database]:
    """Mesmo banco, dois donos — exatamente o cenário que o PostgreSQL passou a permitir."""
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    # `db_dsn` e nao `db_path`: assim o teste segue `TEST_DATABASE_URL` e roda de verdade no PostgreSQL
    # quando a suite e apontada para la. Com `db_path` ele abriria SQLite mesmo dentro da corrida do
    # outro banco — cobertura que parece existir e nao existe.
    db = Database(cfg.db_dsn)
    db.migrate()
    bus = EventBus(db)
    aqui = Repository(db, bus, cfg.evidence_dir, owner_id="servidor-central")
    lah = Repository(db, bus, cfg.evidence_dir, owner_id="notebook-lan-01")
    return aqui, lah, db


def _etapa_pronta(db: Database, step_id: str = "run-1:android-01:v1:send_1") -> str:
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-1','k1','responda','execute','running',1,'[]','2026-09-21T10:00:00Z')")
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters)"
               " VALUES ('run-1:android-01','run-1','android-01','running',1,'{}')")
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
        " bindings, variables) VALUES (?, 'run-1','run-1:android-01','android-01',1,1,'send_1','Enviar para @ana',"
        "'enviar','[]',1,?,'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready',"
        "'SEND_MESSAGE','desc=Send',?,?)",
        (step_id, '["@ana", "bom dia"]', '{\"username\": \"@ana\", \"content\": \"bom dia\"}', None))
    return step_id


def test_quem_assume_a_etapa_fica_registrado(tmp_path: Path) -> None:
    aqui, _, db = _dois_backends(tmp_path)
    sid = _etapa_pronta(db)
    assert aqui.claim_step(sid) is not None
    linha = db.one("SELECT claimed_by, claim_expires_at FROM steps WHERE id=?", (sid,))
    assert linha is not None
    assert linha["claimed_by"] == "servidor-central"
    assert linha["claim_expires_at"] is not None


def test_o_outro_backend_nao_reconcilia_etapa_viva_deste(tmp_path: Path) -> None:
    """O defeito, no menor tamanho em que ele aparece.

    Antes da posse, `interrupted_steps` do segundo backend devolvia a etapa do primeiro — e quem chamasse
    `reconcile_after_restart` a jogaria de volta para `ready` no meio da execução alheia.
    """
    aqui, lah, db = _dois_backends(tmp_path)
    sid = _etapa_pronta(db)
    aqui.claim_step(sid)

    assert [s["id"] for s in aqui.interrupted_steps()] == [sid]      # é minha: reinício MEU reconcilia
    assert lah.interrupted_steps() == []                             # não é dele: ele não toca
    assert lah.abandoned_steps() == []                               # e o lease está valendo


def test_etapa_de_backend_que_parou_de_renovar_pode_ser_adotada(tmp_path: Path) -> None:
    """O outro lado: um backend que caiu não pode deixar a etapa presa para sempre.

    A única prova de que ele morreu é ter parado de renovar. Por isso o lease é longo — reconciliar cedo demais
    significaria dois backends operando o mesmo aparelho.
    """
    aqui, lah, db = _dois_backends(tmp_path)
    sid = _etapa_pronta(db)
    aqui.claim_step(sid)
    # o primeiro backend morre: ninguém renova, e o lease vence
    db.execute("UPDATE steps SET claim_expires_at=? WHERE id=?", (iso_in(-1), sid))

    orfas = lah.abandoned_steps()
    assert [s["id"] for s in orfas] == [sid]
    assert lah.take_over(orfas[0]) is True
    assert db.one("SELECT claimed_by FROM steps WHERE id=?", (sid,))["claimed_by"] == "notebook-lan-01"
    assert lah.abandoned_steps() == []                               # adotada: não aparece duas vezes
    assert aqui.abandoned_steps() == []                              # e o dono antigo não a readota


def test_so_um_backend_ganha_a_etapa_abandonada(tmp_path: Path) -> None:
    """Três backends: A morre, B e C tentam adotar a MESMA etapa.

    Sem compare-and-swap os dois "conseguiriam" e os dois reconciliariam — a segunda passagem devolveria a tentativa
    duas vezes antes de esbarrar numa transição `ready → ready`. O `take_over` condiciona ao dono e ao vencimento que
    o chamador leu, então exatamente um ganha.
    """
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    # `db_dsn` e nao `db_path`: assim o teste segue `TEST_DATABASE_URL` e roda de verdade no PostgreSQL
    # quando a suite e apontada para la. Com `db_path` ele abriria SQLite mesmo dentro da corrida do
    # outro banco — cobertura que parece existir e nao existe.
    db = Database(cfg.db_dsn)
    db.migrate()
    bus = EventBus(db)
    a = Repository(db, bus, cfg.evidence_dir, owner_id="servidor-que-morreu")
    b = Repository(db, bus, cfg.evidence_dir, owner_id="notebook-b")
    c = Repository(db, bus, cfg.evidence_dir, owner_id="notebook-c")
    sid = _etapa_pronta(db)
    a.claim_step(sid)
    db.execute("UPDATE steps SET claim_expires_at=? WHERE id=?", (iso_in(-1), sid))

    # os dois leem a MESMA linha antes de qualquer um escrever — é assim que a corrida acontece de verdade
    vista_por_b = b.abandoned_steps()[0]
    vista_por_c = c.abandoned_steps()[0]
    ganhou = [b.take_over(vista_por_b), c.take_over(vista_por_c)]

    assert ganhou.count(True) == 1, "os dois adotaram a mesma etapa"
    dono = db.one("SELECT claimed_by FROM steps WHERE id=?", (sid,))["claimed_by"]
    assert dono == ("notebook-b" if ganhou[0] else "notebook-c")


def test_renovar_impede_a_adocao(tmp_path: Path) -> None:
    aqui, lah, db = _dois_backends(tmp_path)
    sid = _etapa_pronta(db)
    aqui.claim_step(sid)
    db.execute("UPDATE steps SET claim_expires_at=? WHERE id=?", (iso_in(-1), sid))
    assert lah.abandoned_steps()                                     # venceu

    assert aqui.renew_claims() == 1                                  # o dono estava vivo e renovou
    assert lah.abandoned_steps() == []                               # então ninguém o adota


def test_etapa_sem_dono_e_reconciliada_por_quem_subir(tmp_path: Path) -> None:
    """Linhas de antes da migração não têm dono. São de um processo que já morreu (o banco mudou de versão no
    reinício), então quem subir reconcilia — e um banco novo nunca produz essa linha."""
    aqui, lah, db = _dois_backends(tmp_path)
    sid = _etapa_pronta(db)
    db.execute("UPDATE steps SET status='running', claimed_by=NULL, claim_expires_at=NULL WHERE id=?", (sid,))

    assert [s["id"] for s in aqui.interrupted_steps()] == [sid]
    assert [s["id"] for s in lah.interrupted_steps()] == [sid]


def test_exclusividade_por_aparelho_vale_entre_backends(tmp_path: Path) -> None:
    """A garantia mais importante, e ela já existia: um aparelho, uma etapa ativa. O que a posse acrescenta é que
    agora ela vale ENTRE processos, porque a contagem olha o banco e não a memória de quem pergunta."""
    aqui, lah, db = _dois_backends(tmp_path)
    sid = _etapa_pronta(db)
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
        " bindings, variables) SELECT ?, run_id, objective_id, instance_id, plan_version, 2, ?, title, goal,"
        " depends_on, side_effect, commit_guard, postcondition, timeout_s, max_attempts, 'ready', capability,"
        " commit_selector, bindings, variables FROM steps WHERE id=?", (f"{sid}-b", "send_2", sid))

    assert aqui.claim_step(sid) is not None
    assert lah.claim_step(f"{sid}-b") is None                        # mesmo aparelho, outro backend: recusado
