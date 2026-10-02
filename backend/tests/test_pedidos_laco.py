"""Item 28.4 — o laço de pedidos de ponta a ponta, em SQLite, com `RunService` de verdade e relógio falso.

Prova `simulated` (`arquivo::teste`): provedor de IA simulado, aparelhos falsos, relógio escrito à mão. NADA aqui prova
o ambiente real (o 28.12 liga o laço no central). O planejamento da execução é desligado (`_spawn_planning` no-op):
a execução fica em `planning` e o teste a leva ao estado que quer, para o laço só ser medido nas decisões dele.

Cobre os achados do desenho (`docs/design/pedidos-laco.md` §0): A1 fechamento sem worker, A2 repetição depois de
queda com a execução já criada, A3 `create` no laço de eventos, A4 (campo de pedido na API pública, em
`test_pedidos_origem.py`), A5 instante no mesmo segundo, A6 editar sem perder ocorrência; e mais: duas voltas e dois
líderes produzem UMA ocorrência, coalescência e perdidas, sobreposição, retomada depois de reinício, laço desligado.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from app.config import PedidosCfg
from app.models import RunStatus
from app.modules.pedidos.infrastructure.laco import LacoDePedidos
from app.modules.pedidos.infrastructure.acoes import AcaoInvalida
from app.taskqueue.service import RunError
from app.taskqueue.travas import PEDIDOS, TRAVA_TTL_S, Lideranca
from app.util import to_iso

from .conftest import COMMAND, Harness
from .test_travas import Relogio

UTC = timezone.utc
ALVOS = json.dumps([{"instance_id": "android-01", "profile_id": None, "app_id": None}])


@pytest_asyncio.fixture
async def h(harness: Harness, monkeypatch) -> Harness:
    monkeypatch.setattr(harness.state.runs, "_spawn_planning", lambda run_id: None)
    return harness


def _laco(h: Harness, r: Relogio, *, dono: str = "laco-a", **cfg) -> LacoDePedidos:
    lid = Lideranca(h.state.db, dono=dono, relogio=r)
    # O relógio falso é de 2026-10-02 e as execuções nascem com o relógio REAL: um prazo de início curto cancelaria a
    # execução por acidente. Só o teste do prazo o diminui.
    cfg.setdefault("prazo_inicio_s", 604_800)
    return LacoDePedidos(h.state.db, h.state.runs, lid, PedidosCfg(enabled=True, **cfg), relogio=r)


def _pedido(db, pid: str = "ped1", *, criado: datetime, estado: str = "ativo", autonomia: str = "observar",
            sobreposicao: str = "pular", coalescer: int = 1, janela: int | None = None, max_oc: int | None = None,
            fuso: str = "UTC", max_tentativas: int = 1, pausa_por_falha: int = 3) -> None:
    # `max_tentativas=1` por padrão: os testes do 28.4 medem o fechamento, e uma falha sem efeito agora ganharia uma nova
    # tentativa (28.5; `test_pedidos_tentativas.py` cobre isso com o padrão de verdade, 2).
    db.execute("INSERT INTO pedidos(id, titulo, objetivo, alvos, autonomia, sobreposicao, coalescer,"
               " janela_recuperacao_s, max_ocorrencias, fuso, max_tentativas, pausa_por_falha, estado, versao,"
               " criado_em, atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
               (pid, "teste", COMMAND, ALVOS, autonomia, sobreposicao, coalescer, janela, max_oc, fuso, max_tentativas,
                pausa_por_falha, estado, to_iso(criado), to_iso(criado)))


def _gatilho(db, gid: str, pid: str, tipo: str, spec: dict, criado: datetime, cursor: str | None = None) -> None:
    db.execute("INSERT INTO pedido_gatilhos(id, pedido_id, tipo, spec, cursor, ativo, criado_em) VALUES (?,?,?,?,?,1,?)",
               (gid, pid, tipo, json.dumps(spec), cursor, to_iso(criado)))


def _horaria(db, *, dtstart: str = "2026-10-02T09:00:00", rrule: str = "FREQ=HOURLY", **kw) -> datetime:
    criado = datetime.fromisoformat(dtstart).replace(tzinfo=UTC) - timedelta(seconds=1)
    _pedido(db, criado=criado, **kw)
    _gatilho(db, "g1", "ped1", "recorrencia", {"dtstart": dtstart, "rrule": rrule}, criado)
    return criado


def _ocs(db, pid: str = "ped1") -> list[dict]:
    return db.query("SELECT * FROM pedido_ocorrencias WHERE pedido_id=? ORDER BY previsto_para, id", (pid,))


def _estados(db, pid: str = "ped1") -> list[tuple[str, str]]:
    return [(o["previsto_para"][11:16], o["estado"]) for o in _ocs(db, pid)]


def _runs(db) -> list[dict]:
    return db.query("SELECT * FROM runs WHERE pedido_id IS NOT NULL ORDER BY created_at, id")


def _agora(db, pid: str, r: Relogio, *, segundos_atras: int = 5, **kw) -> None:
    criado = r.t - timedelta(seconds=segundos_atras)
    _pedido(db, pid, criado=criado, **kw)
    _gatilho(db, "g" + pid, pid, "agora", {}, criado)


def _assentar(db, run_id: str, status: str, detalhe: str | None = None) -> None:
    db.execute("UPDATE runs SET status=?, status_detail=? WHERE id=?", (status, detalhe, run_id))


# =============================================================================================== volta completa
async def test_volta_completa_materializa_despacha_fecha_e_encerra(h: Harness, monkeypatch) -> None:
    r = Relogio()
    db, runs = h.state.db, h.state.runs
    laco = _laco(h, r)
    _agora(db, "ped1", r)
    visto: list[tuple[int, bool]] = []
    original = runs.create

    def espia(req, **kw):
        # A3: `create` roda na thread do laço de eventos (a que tem o `asyncio.create_task` do planejamento).
        import asyncio
        visto.append((threading.get_ident(), asyncio.get_running_loop() is not None))
        return original(req, **kw)

    monkeypatch.setattr(runs, "create", espia)
    res = laco.uma_volta()
    assert (res.materializadas, res.despachadas) == (1, 1)
    assert visto == [(threading.get_ident(), True)], "A3: criada fora da thread do laço de eventos"
    [o] = _ocs(db)
    assert o["estado"] == "despachada" and o["origem"] == "agenda" and o["tentativa"] == 1 and o["dono"] is None
    [run] = _runs(db)
    assert run["id"] == o["run_id"] and run["pedido_id"] == "ped1" and run["ocorrencia_id"] == o["id"]
    assert run["idempotency_key"] == o["chave"] + ":t1"
    assert laco.uma_volta().despachadas == 0 and len(_runs(db)) == 1, "a segunda volta não cria outra execução"

    _assentar(db, run["id"], "completed")
    assert laco.uma_volta().fechadas == 1
    assert _ocs(db)[0]["estado"] == "concluida" and _ocs(db)[0]["terminada_em"]
    p = db.one("SELECT estado, encerrado_motivo FROM pedidos WHERE id='ped1'")
    assert (p["estado"], p["encerrado_motivo"]) == ("encerrado", "contagem"), "gatilho `agora` esgotado e sem aberta"


# =============================================================================================== A1
async def test_a1_execucao_que_assenta_sem_worker_e_fechada_pela_varredura(h: Harness) -> None:
    """Falha no planejamento e cancelamento antes de iniciar assentam SEM passar por `on_run_settled`."""
    r = Relogio()
    db, runs = h.state.db, h.state.runs
    laco = _laco(h, r)
    _agora(db, "pa", r)
    _agora(db, "pb", r, sobreposicao="pular")
    laco.uma_volta()
    runs_por_pedido = {x["pedido_id"]: x for x in _runs(db)}
    assert set(runs_por_pedido) == {"pa", "pb"}
    h.state.repo.set_run_status(runs_por_pedido["pa"]["id"], RunStatus.failed, "o planejador recusou")
    runs.cancel(runs_por_pedido["pb"]["id"])                   # cancelada antes de iniciar: sem worker, sem gancho
    assert laco.uma_volta().fechadas == 2
    a, b = _ocs(db, "pa")[0], _ocs(db, "pb")[0]
    assert (a["estado"], a["motivo"]) == ("falhou", "o planejador recusou")
    assert (b["estado"], b["motivo"]) == ("cancelada", "execução cancelada")


async def test_a1_prazo_de_inicio_cancela_a_execucao_e_a_ocorrencia_vira_perdida(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    laco = _laco(h, r, prazo_inicio_s=3600)
    _agora(db, "ped1", r)
    laco.uma_volta()
    [run] = _runs(db)
    r.avancar(3500)
    laco.uma_volta()
    assert h.state.repo.run_row(run["id"])["status"] == "planning", "ainda dentro do prazo"
    r.avancar(200)                                             # 3700 s depois do despacho
    laco.uma_volta()
    assert h.state.repo.run_row(run["id"])["status"] == "cancelled"
    [o] = _ocs(db)
    assert o["estado"] == "perdida" and o["motivo"].startswith("não começou em 3600s")
    assert o["resumo"] == "prazo_inicio"


# =============================================================================================== A2
async def test_a2_queda_entre_criar_e_marcar_acha_a_execucao_pela_chave_e_nao_chama_create(h: Harness,
                                                                                         monkeypatch) -> None:
    r = Relogio()
    db, runs = h.state.db, h.state.runs
    laco = _laco(h, r)
    _agora(db, "ped1", r)
    original = laco.repo.marcar_despachada

    def cai(*a, **k):
        raise RuntimeError("queda entre criar e marcar")

    monkeypatch.setattr(laco.repo, "marcar_despachada", cai)
    assert laco.uma_volta().erros == 1
    [o] = _ocs(db)
    assert o["estado"] == "devida" and o["dono"] == "laco-a", "reservada, execução criada, sem marca"
    [run] = _runs(db)
    monkeypatch.setattr(laco.repo, "marcar_despachada", original)
    chamadas: list[object] = []
    monkeypatch.setattr(runs, "create", lambda *a, **k: chamadas.append(a) or pytest.fail("create não deve ser chamada"))
    laco.uma_volta()
    assert chamadas == []
    [o] = _ocs(db)
    assert (o["estado"], o["run_id"], o["tentativa"]) == ("despachada", run["id"], 1)
    assert len(_runs(db)) == 1


async def test_a2_com_a_execucao_ja_criada_um_runerror_do_pre_voo_nao_importa(h: Harness, monkeypatch) -> None:
    """O pré-voo poderia recusar (aparelho que ficou offline, IA sem chave) uma execução que EXISTE: o laço nem chama."""
    r = Relogio()
    db, runs = h.state.db, h.state.runs
    laco = _laco(h, r)
    _agora(db, "ped1", r)
    original = laco.repo.marcar_despachada
    monkeypatch.setattr(laco.repo, "marcar_despachada", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cai")))
    laco.uma_volta()
    monkeypatch.setattr(laco.repo, "marcar_despachada", original)

    def recusa(*a, **k):
        raise RunError("ai_not_configured", "sem chave", 503)

    monkeypatch.setattr(runs, "create", recusa)
    laco.uma_volta()
    assert _ocs(db)[0]["estado"] == "despachada"


async def test_runerror_mantem_devida_e_vira_perdida_depois_da_janela(h: Harness, monkeypatch) -> None:
    r = Relogio()
    db, runs = h.state.db, h.state.runs
    laco = _laco(h, r, janela_padrao_s=600)
    _agora(db, "ped1", r)

    def recusa(*a, **k):
        raise RunError("ai_not_configured", "A IA não está configurada.", 503)

    monkeypatch.setattr(runs, "create", recusa)
    laco.uma_volta()
    [o] = _ocs(db)
    assert o["estado"] == "devida" and o["dono"] is None and o["resumo"].startswith("ai_not_configured")
    assert _runs(db) == []
    r.avancar(300)
    laco.uma_volta()
    assert _ocs(db)[0]["estado"] == "devida", "dentro da janela, tenta de novo"
    r.avancar(400)                                             # 700 s depois: passou de previsto_para + janela
    laco.uma_volta()
    [o] = _ocs(db)
    assert o["estado"] == "perdida" and o["motivo"].startswith("não foi possível criar a execução: ai_not_configured")
    assert _runs(db) == [], "falha nunca vira execução"


# =============================================================================================== A5
async def test_a5_o_instante_cheio_e_devido_com_o_relogio_em_meio_segundo(h: Harness) -> None:
    r = Relogio()
    r.t = datetime(2026, 10, 2, 12, 0, 0, 500_000, tzinfo=UTC)
    db = h.state.db
    laco = _laco(h, r)
    _horaria(db, dtstart="2026-10-02T12:00:00", rrule="FREQ=DAILY")
    laco.uma_volta()
    [o] = _ocs(db)
    assert o["previsto_para"] == "2026-10-02T12:00:00Z"
    assert o["estado"] == "despachada", "no mesmo segundo: `…:00Z` já é devido às `…:00.500Z`"


# =============================================================================================== A6
async def test_a6_editar_nao_perde_ocorrencia_e_troca_a_versao_no_lugar(h: Harness) -> None:
    r = Relogio()
    r.t = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    db = h.state.db
    laco = _laco(h, r)
    _horaria(db, dtstart="2026-10-02T12:00:00")
    laco.uma_volta()
    assert _estados(db) == [("12:00", "despachada"), ("13:00", "prevista")]
    antes = {o["previsto_para"]: o["id"] for o in _ocs(db)}

    v2 = laco.acoes.editar("ped1", {"objetivo": COMMAND + " Depois confirme."})
    assert v2 == 2
    depois = _ocs(db)
    assert {o["previsto_para"]: o["id"] for o in depois} == antes, "mesma linha, nada recriado"
    assert [(o["estado"], o["pedido_versao"]) for o in depois] == [("despachada", 1), ("prevista", 2)]

    # Trocar a recorrência: gatilho NOVO; a `prevista` do velho é cancelada com motivo; a do novo NASCE (não é engolida).
    v3 = laco.acoes.editar("ped1", gatilho=("recorrencia", {"dtstart": "2026-10-02T09:00:00",
                                                            "rrule": "FREQ=HOURLY;BYMINUTE=30"}))
    assert v3 == 3
    velha = [o for o in _ocs(db) if o["previsto_para"] == "2026-10-02T13:00:00Z"][0]
    assert (velha["estado"], velha["motivo"], velha["id"]) == ("cancelada", "edição", antes["2026-10-02T13:00:00Z"])
    assert db.scalar("SELECT ativo FROM pedido_gatilhos WHERE id='g1'") == 0
    novo = db.one("SELECT id FROM pedido_gatilhos WHERE ativo=1")
    assert novo["id"] != "g1"
    laco.uma_volta()
    nova = [o for o in _ocs(db) if o["gatilho_id"] == novo["id"]]
    assert [(o["previsto_para"], o["estado"], o["pedido_versao"]) for o in nova] == [("2026-10-02T12:30:00Z", "prevista", 3)]
    with pytest.raises(AcaoInvalida):
        laco.acoes.editar("ped1", {"estado": "ativo"})        # campo que a edição não muda (28.9 abriu `autonomia`, alvos, fuso e orçamentos)


# =============================================================================================== líderes e voltas
async def test_duas_voltas_e_o_cursor_perdido_produzem_uma_ocorrencia_so(h: Harness) -> None:
    r = Relogio()
    r.t = datetime(2026, 10, 2, 12, 0, 5, tzinfo=UTC)
    db = h.state.db
    laco = _laco(h, r)
    _horaria(db, dtstart="2026-10-02T12:00:00", rrule="FREQ=DAILY")
    laco.uma_volta()
    laco.uma_volta()
    # `proxima_em` zerado: o laço só relê o pedido cuja próxima materialização chegou (índice do §2.2).
    db.execute("UPDATE pedido_gatilhos SET cursor=? WHERE id='g1'", ("2026-10-02T11:59:59Z",))   # cursor voltou atrás
    db.execute("UPDATE pedidos SET proxima_em=NULL")
    laco.uma_volta()
    assert len(_ocs(db)) == 1 and len(_runs(db)) == 1
    db.execute("UPDATE pedido_gatilhos SET cursor=NULL WHERE id='g1'")                            # ou foi perdido
    db.execute("UPDATE pedidos SET proxima_em=NULL")
    laco.uma_volta()
    assert len(_ocs(db)) == 1 and len(_runs(db)) == 1


async def test_dois_lacos_no_mesmo_banco_um_so_e_o_lider_e_o_velho_e_cercado(h: Harness) -> None:
    r = Relogio()
    r.t = datetime(2026, 10, 2, 12, 0, 5, tzinfo=UTC)
    db = h.state.db
    a, b = _laco(h, r, dono="laco-a"), _laco(h, r, dono="laco-b")
    _horaria(db, dtstart="2026-10-02T12:00:00", rrule="FREQ=DAILY")
    assert a.uma_volta().despachadas == 1
    assert b.uma_volta() is None, "o seguidor pula a volta sem erro"
    token_a = a.lideranca._tokens[PEDIDOS]
    r.avancar(TRAVA_TTL_S + 30)                                   # A parou de renovar (pausa longa)
    assert b.uma_volta() is not None                              # B assume o mandato
    assert len(_ocs(db)) == 1 and len(_runs(db)) == 1
    # A acorda achando que ainda manda: a escrita cercada recusa e nada é escrito.
    velho = _laco(h, r, dono="laco-a")
    velho._lider = lambda: token_a
    db.execute("DELETE FROM pedido_ocorrencias")
    db.execute("UPDATE pedido_gatilhos SET cursor=NULL")
    db.execute("UPDATE pedidos SET proxima_em=NULL")
    antes = len(_runs(db))
    res = velho.uma_volta()
    assert res.lider is False and _ocs(db) == [] and len(_runs(db)) == antes


# =============================================================================================== janela, coalescência, sobreposição
async def test_queda_de_horas_vira_perdidas_registradas_e_uma_recuperacao(h: Harness) -> None:
    r = Relogio()
    r.t = datetime(2026, 10, 2, 12, 10, 0, tzinfo=UTC)
    db = h.state.db
    laco = _laco(h, r)
    _horaria(db, dtstart="2026-10-02T09:00:00")
    laco.uma_volta()
    assert _estados(db) == [("09:00", "perdida"), ("10:00", "perdida"), ("11:00", "perdida"),
                            ("12:00", "despachada"), ("13:00", "prevista")]
    assert _ocs(db)[0]["motivo"].startswith("fora da janela de recuperação: atraso de 11400s > 1800s")
    assert _ocs(db)[3]["origem"] == "recuperacao" and len(_runs(db)) == 1


async def test_coalescencia_deixa_so_a_mais_recente_e_registra_as_outras(h: Harness) -> None:
    r = Relogio()
    r.t = datetime(2026, 10, 2, 12, 10, 0, tzinfo=UTC)
    db = h.state.db
    laco = _laco(h, r)
    _horaria(db, dtstart="2026-10-02T09:00:00", janela=4 * 3600)
    laco.uma_volta()
    assert _estados(db)[:4] == [("09:00", "pulada"), ("10:00", "pulada"), ("11:00", "pulada"), ("12:00", "despachada")]
    assert _ocs(db)[0]["motivo"] == "coalescida na de 2026-10-02T12:00:00Z"
    assert len(_runs(db)) == 1


async def test_sobreposicao_pular_com_varias_devidas_despacha_uma_e_pula_as_outras(h: Harness) -> None:
    r = Relogio()
    r.t = datetime(2026, 10, 2, 12, 10, 0, tzinfo=UTC)
    db = h.state.db
    laco = _laco(h, r)
    _horaria(db, dtstart="2026-10-02T10:00:00", janela=4 * 3600, coalescer=0)
    laco.uma_volta()
    assert _estados(db)[:3] == [("10:00", "despachada"), ("11:00", "pulada"), ("12:00", "pulada")]
    assert _ocs(db)[1]["motivo"] == f"a anterior ainda roda ({_ocs(db)[0]['id']})"
    assert len(_runs(db)) == 1
    # a próxima hora com a primeira ainda aberta: pula também (`pular` não acumula)
    r.avancar(3600)
    laco.uma_volta()
    assert _estados(db)[3] == ("13:00", "pulada") and len(_runs(db)) == 1


async def test_sobreposicao_permitir_todas_so_com_autonomia_observar(h: Harness) -> None:
    r = Relogio()
    r.t = datetime(2026, 10, 2, 12, 10, 0, tzinfo=UTC)
    db = h.state.db
    laco = _laco(h, r)
    _horaria(db, dtstart="2026-10-02T11:00:00", janela=4 * 3600, coalescer=0, sobreposicao="permitir_todas",
             autonomia="agir")
    laco.uma_volta()                                              # incoerente (agir só pula): aplica `pular`
    assert len(_runs(db)) == 1
    _pedido(db, "ped2", criado=r.t - timedelta(hours=2), janela=4 * 3600, coalescer=0, sobreposicao="permitir_todas")
    _gatilho(db, "g2", "ped2", "recorrencia", {"dtstart": "2026-10-02T11:00:00", "rrule": "FREQ=HOURLY"},
             r.t - timedelta(hours=2))
    laco.uma_volta()
    assert [x["estado"] for x in _ocs(db, "ped2")[:2]] == ["despachada", "despachada"]


async def test_max_ocorrencias_conta_so_as_que_viraram_execucao_e_encerra(h: Harness) -> None:
    r = Relogio()
    r.t = datetime(2026, 10, 2, 12, 10, 0, tzinfo=UTC)
    db = h.state.db
    laco = _laco(h, r)
    _horaria(db, dtstart="2026-10-02T10:00:00", janela=4 * 3600, coalescer=0, sobreposicao="permitir_todas", max_oc=1)
    laco.uma_volta()
    assert [e for _, e in _estados(db)][:3] == ["despachada", "pulada", "pulada"]
    assert _ocs(db)[1]["motivo"] == "máximo de ocorrências atingido (1)"
    assert len(_runs(db)) == 1
    _assentar(db, _runs(db)[0]["id"], "completed")
    laco.uma_volta()
    p = db.one("SELECT estado, encerrado_motivo FROM pedidos WHERE id='ped1'")
    assert (p["estado"], p["encerrado_motivo"]) == ("encerrado", "contagem")


# =============================================================================================== reinício
async def test_reinicio_no_meio_nao_duplica_nem_perde(h: Harness) -> None:
    r = Relogio()
    r.t = datetime(2026, 10, 2, 12, 0, 5, tzinfo=UTC)
    db = h.state.db
    _horaria(db, dtstart="2026-10-02T12:00:00")
    antes = _laco(h, r)
    antes.uma_volta()
    [run] = _runs(db)
    _assentar(db, run["id"], "completed")         # terminou antes da queda: o fechamento fica para a volta nova
    # O processo cai (o laço some) e volta 3 h depois, com o MESMO OWNER_ID: a partida solta a trava da queda.
    r.avancar(3 * 3600 + 5 * 60)
    novo = _laco(h, r)
    novo.lideranca.soltar_da_queda()
    novo.uma_volta()
    assert _estados(db) == [("12:00", "concluida"), ("13:00", "perdida"), ("14:00", "perdida"),
                            ("15:00", "despachada"), ("16:00", "prevista")]
    assert [o["origem"] for o in _ocs(db)][3] == "recuperacao"
    assert len(_runs(db)) == 2 and _runs(db)[0]["id"] == run["id"], "a execução anterior não foi recriada"


# =============================================================================================== pausa, retomada, cancelamento
async def test_pausar_pula_o_pendente_e_retomar_daqui_registra_o_que_a_pausa_atravessou(h: Harness) -> None:
    r = Relogio()
    r.t = datetime(2026, 10, 2, 12, 0, 5, tzinfo=UTC)
    db = h.state.db
    laco = _laco(h, r)
    _horaria(db, dtstart="2026-10-02T12:00:00")
    laco.uma_volta()
    assert _estados(db) == [("12:00", "despachada"), ("13:00", "prevista")]
    laco.acoes.pausar("ped1", "a pessoa pausou")
    assert [e for _, e in _estados(db)] == ["despachada", "pulada"] and _ocs(db)[1]["motivo"] == "pedido pausado"
    r.avancar(2 * 3600 + 600)                                    # 14:10 : a pausa atravessa 13:00 e 14:00
    assert laco.uma_volta().materializadas == 0, "pausado não materializa"
    laco.acoes.retomar("ped1", "daqui")
    assert _estados(db) == [("12:00", "despachada"), ("13:00", "pulada"), ("14:00", "pulada")]
    assert _ocs(db)[1]["motivo"] == "pedido pausado", "a que já existia fica como a pausa a deixou"
    assert _ocs(db)[2]["motivo"] == "pausado: retomado daqui"
    assert len(_runs(db)) == 1


async def test_cancelar_cancela_o_pendente_e_a_execucao_aberta_fecha_pela_varredura(h: Harness) -> None:
    r = Relogio()
    r.t = datetime(2026, 10, 2, 12, 0, 5, tzinfo=UTC)
    db = h.state.db
    laco = _laco(h, r)
    _horaria(db, dtstart="2026-10-02T12:00:00")
    laco.uma_volta()
    laco.acoes.cancelar("ped1", por="teste")
    assert db.scalar("SELECT estado FROM pedidos WHERE id='ped1'") == "cancelado"
    [run] = _runs(db)
    assert h.state.repo.run_row(run["id"])["status"] == "cancelled"
    laco.uma_volta()
    assert [(e, o["motivo"]) for (_, e), o in zip(_estados(db), _ocs(db))] == [("cancelada", "pedido cancelado"),
                                                                            ("cancelada", "pedido cancelado")]
    assert len(_runs(db)) == 1, "cancelado não despacha mais"


# =============================================================================================== desligado
@pytest.mark.parametrize("ligado", [False, True])
async def test_laco_desligado_nem_sobe_e_nao_toma_a_trava(tmp_path: Path, ligado: bool) -> None:
    hh = Harness(tmp_path, 1)
    hh.cfg.file.pedidos.enabled = ligado
    await hh.boot()
    try:
        nomes = {t.get_name() for t in hh.state._bg}
        trava = hh.state.db.one("SELECT dono FROM travas WHERE nome=?", (PEDIDOS,))
        if ligado:
            assert "pedidos" in nomes and trava is not None
        else:
            assert "pedidos" not in nomes, "desligado: o laço nem sobe"
            assert trava is None or trava["dono"] is None, "desligado: não segura a trava"
    finally:
        await hh.state.stop()


async def test_padrao_de_fabrica_e_desligado() -> None:
    assert PedidosCfg().enabled is False
