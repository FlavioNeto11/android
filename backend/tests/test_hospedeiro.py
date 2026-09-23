"""Item 5.1 — hospedeiro e papéis: dois backends no MESMO banco, cada um com os SEUS aparelhos.

O defeito que estes testes fecham (achado #171): só a ETAPA tinha dono. Tudo acima dela supunha um processo
único, e o que um backend fazia com o objetivo de um aparelho que ele NÃO hospeda era destrutivo — bloqueava o
item com "Instância não existe na configuração atual", marcava o aparelho como offline e, com o rodízio ligado,
criava no PRÓPRIO disco um AVD novo com o mesmo id lógico para atendê-lo: um aparelho vazio, sem a sessão do
perfil. A regra que passa a valer é "objetivo alheio é IGNORADO, nunca bloqueado".

E o achado #26: as reconciliações de partida (comandos, planejamento) também eram cegas de dono.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.db import Database
from app.events import EventBus
from app.main import create_app
from app.taskqueue.repository import Repository

from .conftest import Harness, make_config

OUTRO = "servidor-central"
EU = "notebook-lan-01"


def _parque(db: Database, *, hosted_by: str | None, ids: list[str], base_idx: int = 1) -> None:
    """Instâncias já existentes no banco, hospedadas por outro backend (ou por ninguém)."""
    for n, iid in enumerate(ids, start=base_idx):
        db.execute(
            "INSERT INTO instances(id, idx, avd_name, console_port, system_port, mjpeg_port, chromedriver_port,"
            " hosted_by) VALUES (?,?,?,?,?,?,?,?)",
            # Portas longe das que o `seed` calcula para os aparelhos DESTE backend: a tabela tem UNIQUE em
            # cada uma delas, e o aparelho do outro backend não pode colidir com o meu android-03.
            (iid, n, iid, 19000 + 2 * n, 19100 + n, 19200 + n, 19300 + n, hosted_by))


def _objetivo_pronto(db: Database, iid: str, run_id: str = "run-1") -> str:
    """Uma execução em andamento com uma etapa `ready` naquele aparelho."""
    oid = f"{run_id}:{iid}"
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES (?,?,'responda','execute','running',1,'[]','2026-09-22T10:00:00Z')", (run_id, run_id))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters)"
               " VALUES (?,?,?,'pending',1,'{}')", (oid, run_id, iid))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
        " bindings, variables) VALUES (?,?,?,?,1,1,'send_1','Enviar','enviar','[]',1,'[]',"
        "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready','SEND_MESSAGE',"
        "'desc=Send','{}',NULL)",
        (f"{run_id}:{iid}:v1:send_1", run_id, oid, iid))
    return oid


# --------------------------------------------------------------------------- o filtro, no menor tamanho
def test_despacho_ignora_o_objetivo_de_aparelho_alheio(tmp_path: Path) -> None:
    """`dispatchable_objectives` é a porta por onde o objetivo do outro backend entrava. Agora não entra."""
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    bus = EventBus(db)
    _parque(db, hosted_by=OUTRO, ids=["android-01"])
    _parque(db, hosted_by=EU, ids=["android-02"], base_idx=2)
    _objetivo_pronto(db, "android-01", "run-do-outro")
    _objetivo_pronto(db, "android-02", "run-minha")

    dele = Repository(db, bus, cfg.evidence_dir, owner_id=OUTRO)
    meu = Repository(db, bus, cfg.evidence_dir, owner_id=EU)

    assert [o["instance_id"] for o in dele.dispatchable_objectives()] == ["android-01"]
    assert [o["instance_id"] for o in meu.dispatchable_objectives()] == ["android-02"]


def test_aparelho_sem_dono_registrado_continua_sendo_de_quem_perguntar(tmp_path: Path) -> None:
    """Banco anterior à migração 027 (e produção, com um backend só): `hosted_by IS NULL` não pode sumir da fila."""
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    _parque(db, hosted_by=None, ids=["android-01"])
    _objetivo_pronto(db, "android-01")
    repo = Repository(db, EventBus(db), cfg.evidence_dir, owner_id=EU)

    assert [o["instance_id"] for o in repo.dispatchable_objectives()] == ["android-01"]


# --------------------------------------------------------------------------- o backend inteiro
@pytest.mark.asyncio
async def test_backend_nao_carrega_nem_toca_no_aparelho_de_outro(tmp_path: Path) -> None:
    """O caminho completo: aparelho na MINHA configuração, mas hospedado por outro backend.

    Prova as três coisas que o achado #171 descreve como destrutivas: o aparelho não entra no meu ciclo de vida
    (logo não há AVD fantasma para criar), o rodízio não pede para ligá-lo, e o objetivo dele NÃO é bloqueado —
    fica esperando quem o hospeda, que é a diferença entre "ignorado" e "atropelado".
    """
    h = Harness(tmp_path, 4, owner_id=EU)
    h.cfg.ensure_dirs()
    antes = Database(h.cfg.db_dsn)
    antes.migrate()
    _parque(antes, hosted_by=OUTRO, ids=["android-01", "android-02"])
    antes.close()

    s = await h.boot()
    try:
        # 1) o que eu carrego é só o meu — os dois primeiros ficaram com o outro backend
        assert set(s.devices.devices) == {"android-03", "android-04"}
        donos = {r["id"]: r["hosted_by"] for r in s.db.query("SELECT id, hosted_by FROM instances")}
        assert donos == {"android-01": OUTRO, "android-02": OUTRO, "android-03": EU, "android-04": EU}

        # 2) o objetivo do aparelho alheio nem aparece no meu despacho
        oid = _objetivo_pronto(s.db, "android-01", "run-do-outro")
        assert [o["instance_id"] for o in s.repo.dispatchable_objectives()] == []

        pedidos: list[str] = []
        original = s.devices.request_start
        s.devices.request_start = lambda rt, porque="": (pedidos.append(rt.id), original(rt, porque))[1]  # type: ignore[assignment,method-assign]

        for _ in range(3):
            s.scheduler._tick()

        # 3) nada foi feito com ele: nem bloqueio, nem pedido de boot, nem worker
        obj = s.db.one("SELECT status, status_detail, blocked_reason FROM objectives WHERE id=?", (oid,))
        assert obj is not None
        assert obj["status"] == "pending", "o objetivo do outro backend foi mexido"
        assert obj["blocked_reason"] is None
        assert "android-01" not in pedidos
        assert "android-01" not in s.scheduler.workers
        assert s.db.one("SELECT emulator_pid FROM instances WHERE id='android-01'")["emulator_pid"] is None
    finally:
        await h.state.stop()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_reconciliacoes_de_partida_so_mexem_no_que_eu_hospedo(tmp_path: Path) -> None:
    """Achado #26: comando em voo e planejamento em curso do OUTRO backend não podem virar `uncertain`/replanejamento.

    O planejamento é o caro dos dois: sem `runs.planned_by`, subir um segundo backend disparava uma chamada PAGA
    ao planejador da mesma execução que o primeiro já estava planejando.
    """
    h = Harness(tmp_path, 4, owner_id=EU)
    h.cfg.ensure_dirs()
    antes = Database(h.cfg.db_dsn)
    antes.migrate()
    _parque(antes, hosted_by=OUTRO, ids=["android-01", "android-02"])
    antes.close()

    s = await h.boot()
    try:
        agora = "2026-09-22T10:00:00.000Z"
        for cid, iid in (("c-alheio", "android-01"), ("c-meu", "android-03")):
            s.db.execute("INSERT INTO commands(id, instance_id, verb, idempotency_key, state, requested_by,"
                         " created_at, dispatched_at) VALUES (?,?,'start',?, 'dispatched','panel',?,?)",
                         (cid, iid, cid, agora, agora))
        abertos = {r["id"] for r in s.commands.open_commands(hospedados_por=EU)}
        assert abertos == {"c-meu"}, "o comando do aparelho alheio entrou na minha reconciliação"

        mudados = {r["id"] for r in s.commands.reconcile_after_restart()}
        assert mudados == {"c-meu"}
        assert s.db.one("SELECT state FROM commands WHERE id='c-alheio'")["state"] == "dispatched"

        # planejamento: o que é do outro não é replanejado (nenhuma chamada paga a mais)
        for rid, dono in (("run-do-outro", OUTRO), ("run-minha", EU)):
            s.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids,"
                         " created_at, planned_by) VALUES (?,?,'responda','execute','planning',1,'[]',?,?)",
                         (rid, rid, agora, dono))
        s.runs._planning.clear()
        s.runs.resume_planning_after_restart()
        assert set(s.runs._planning) == {"run-minha"}
        for t in s.runs._planning.values():
            t.cancel()
    finally:
        await h.state.stop()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_dois_backends_vivos_no_mesmo_banco(tmp_path: Path) -> None:
    """Os DOIS backends de pé ao mesmo tempo, no mesmo banco — o que o achado #171(4) pede.

    A hospeda android-01/02; B sobe depois com quatro na configuração e fica com 03/04, porque os dois primeiros
    já têm dono. Uma execução de verdade roda em A enquanto o tick de B gira: B não bloqueia, não liga, não
    executa e não cancela nada do aparelho de A — nem quando a execução é cancelada.
    """
    a = Harness(tmp_path / "a", 2, owner_id="servidor-a")
    sa = await a.boot()
    b = Harness(tmp_path / "b", 4, owner_id="servidor-b", db_dsn=a.cfg.db_dsn)
    sb = await b.boot()
    try:
        assert set(sa.devices.devices) == {"android-01", "android-02"}
        assert set(sb.devices.devices) == {"android-03", "android-04"}, "B ficou com aparelho que A já hospeda"

        pedidos: list[str] = []
        sb.devices.request_start = lambda rt, porque="": pedidos.append(rt.id) or False  # type: ignore[assignment,method-assign]

        run = a.run(["android-01"])
        oid = f"{run.id}:android-01"

        vistos: list[str] = []
        for _ in range(150):
            sb.scheduler._tick()                     # o tick de B gira enquanto A executa de verdade
            if (linha := sa.db.one("SELECT status FROM objectives WHERE id=?", (oid,))) is not None:
                vistos.append(linha["status"])       # o plano é assíncrono: o objetivo aparece depois
            if sa.repo.run_row(run.id)["status"] in ("completed", "completed_with_issues", "failed"):
                break
            await asyncio.sleep(0.1)

        assert vistos, "o objetivo de A nunca foi materializado: o teste não provou nada"
        assert "waiting_user" not in vistos, "B bloqueou o objetivo do aparelho de A"
        assert pedidos == [], f"B tentou ligar aparelho que não hospeda: {pedidos}"
        assert "android-01" not in sb.scheduler.workers
        # a etapa em execução é de A, e é A quem aparece como dono dela
        donos = {r["claimed_by"] for r in sa.db.query(
            "SELECT claimed_by FROM steps WHERE instance_id='android-01' AND claimed_by IS NOT NULL")}
        assert donos <= {"servidor-a"}, f"etapa de A assumida por outro: {donos}"

        # o tick de B não pode fechar o objetivo do aparelho de A nem estourar quando a execução é cancelada
        if sa.repo.run_row(run.id)["status"] == "running":
            sa.runs.cancel(run.id)
        for _ in range(5):
            sb.scheduler._tick()
        assert sa.repo.objective_row(oid)["instance_id"] == "android-01"
    finally:
        await b.state.stop()  # type: ignore[union-attr]
        await a.state.stop()  # type: ignore[union-attr]


# --------------------------------------------------------------------------- papéis
@pytest.mark.asyncio
async def test_papel_api_nao_hospeda_nem_despacha(tmp_path: Path) -> None:
    """`ROLE=api`: a réplica que atende o painel não pode carimbar aparelho, subir scheduler nem reconciliar."""
    h = Harness(tmp_path, 2, owner_id="replica-de-api", role="api")
    assert h.cfg.hospeda_aparelhos is False
    assert h.cfg.roda_scheduler is False
    assert h.cfg.serve_api is True

    s = await h.boot()
    try:
        # não reivindicou nada: os aparelhos continuam sem dono, prontos para o hospedeiro carimbar
        donos = {r["hosted_by"] for r in s.db.query("SELECT hosted_by FROM instances")}
        assert donos == {None}
        assert s.scheduler._task is None, "ROLE=api subiu o scheduler"
        assert s.local_worker.link is None, "ROLE=api conectou o worker local"
    finally:
        await h.state.stop()  # type: ignore[union-attr]


def test_papel_scheduler_nao_publica_a_api_rest(tmp_path: Path) -> None:
    """O contrário: quem só despacha não publica REST. O canal do worker fica, porque é o transporte dos
    aparelhos desta máquina — sem ele o hospedeiro não alcança o que hospeda."""
    def caminhos_de(app: Any) -> set[str]:
        """Os caminhos que o app atende. `include_router` não achata as rotas na lista do app (o FastAPI guarda
        um `_IncludedRouter` com o router original dentro), então a coleta desce um nível."""
        achados: set[str] = set()
        pilha = list(app.routes)
        while pilha:
            r = pilha.pop()
            if (caminho := getattr(r, "path", None)):
                achados.add(caminho)
            if (dentro := getattr(r, "original_router", None)) is not None:
                pilha.extend(dentro.routes)
        return achados

    caminhos: dict[str, set[str]] = {}
    for papel in ("all", "scheduler"):
        cfg = make_config(tmp_path / papel, 1, role=papel)
        cfg.ensure_dirs()
        caminhos[papel] = caminhos_de(create_app(cfg))

    assert "/api/health" in caminhos["all"]
    assert "/api/health" not in caminhos["scheduler"]
    assert "/api/worker/ws" in caminhos["scheduler"]
