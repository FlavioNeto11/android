"""Desbravador (`ai.pathfinder_wait_s`): o primeiro aparelho aprende as receitas e os COMPATÍVEIS esperam por ele.

Antes desta frente (F3): a espera era um `continue` mudo — nenhum `wait_reason`, nenhuma medida —, o líder era "o
primeiro da execução" mesmo para aparelhos com outra versão do app, e o parque inteiro esperava o líder até em
comando repetido com o caminho já aprendido. Aqui, com o relógio do desbravador controlado pelo teste (nada de
dormir): espera visível; líder que falha solta os demais na hora; líder que aprende solta; teto estoura com desfecho
`expirou`; versão diferente não espera o mesmo líder; caminho já aberto não espera ninguém.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from app.db import dumps
from app.metricas import metricas
from app.planning.provider import Decision, Usage

from .conftest import Harness

PKG = "com.pocqa.messenger"
TETO = 240


class _Relogio:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _liga(h: Harness) -> _Relogio:
    h.cfg.file.ai.recipes = "replay"
    h.cfg.file.ai.pathfinder_wait_s = TETO
    relogio = _Relogio()
    h.state.scheduler.relogio = relogio                                  # type: ignore[union-attr]
    metricas.limpar()
    return relogio


def _segura(h: Harness, instancia: str, portao: asyncio.Event,
            depois: Callable[[Any], tuple[Decision, Usage]] | None = None, *,
            parado: asyncio.Event | None = None) -> None:
    """A PRIMEIRA decisão de IA de `instancia` fica parada até o portão abrir: o líder "ainda está aprendendo".

    `parado` é sinalizado quando o líder CHEGA à decisão segurada. Quem mexe no aparelho do líder espera por ele:
    antes disso o executor ainda está nos passos que tocam o aparelho, e derrubá-lo ali faz o próprio executor
    encerrar o objetivo (`falhou`) antes de a volta do scheduler ver o aparelho fora do ar (`liberado`)."""
    inner = h.ai.inner
    decide0 = inner.decide
    segurou = {"feito": False}

    async def decide(req: Any) -> Any:
        if req.ctx.instance_id == instancia and not segurou["feito"]:
            segurou["feito"] = True
            if parado is not None:
                parado.set()
            await portao.wait()
            if depois is not None:
                return depois(req)
        return await decide0(req)

    inner.decide = decide


def _obj(h: Harness, run_id: str, instancia: str) -> Any:
    return h.state.db.one("SELECT * FROM objectives WHERE run_id=? AND instance_id=?",  # type: ignore[union-attr]
                          (run_id, instancia))


def _espera_s() -> dict[str, Any] | None:
    return next((d for d in metricas.snapshot()["distribuicoes"] if d["nome"] == "pathfinder.espera_s"), None)


def _decisoes(h: Harness, run_id: str) -> list[str]:
    return [r["message"] for r in h.state.db.query(                      # type: ignore[union-attr]
        "SELECT message FROM events WHERE kind='decision' AND run_id=? ORDER BY id", (run_id,))]


def _status(h: Harness, run_id: str, instancia: str, campo: str = "status") -> Any:
    o = _obj(h, run_id, instancia)                         # o objetivo só existe depois do planejamento
    return o[campo] if o is not None else None


async def _esperando(h: Harness, run_id: str, instancia: str) -> Any:
    await h.wait(lambda: _status(h, run_id, instancia, "wait_reason") == "pathfinder", what=f"{instancia} esperando")
    return _obj(h, run_id, instancia)


async def test_espera_e_visivel_e_termina_quando_o_lider_aprende(harness: Harness) -> None:
    relogio = _liga(harness)
    portao = asyncio.Event()
    _segura(harness, "android-01", portao)
    run = harness.run(["android-01", "android-02"])

    o2 = await _esperando(harness, run.id, "android-02")
    assert o2["status"] == "pending"
    assert "desbravador android-01" in o2["status_detail"] and f"{TETO} s" in o2["status_detail"]
    relogio.t += 30                                         # dentro do teto: continua esperando, sem gastar IA
    await harness.ticks(3)
    assert _obj(harness, run.id, "android-02")["wait_reason"] == "pathfinder"
    assert harness.ai.count("decide", instance="android-02") == 0
    assert metricas.total("pathfinder.desfecho") == 0

    portao.set()
    assert (await harness.wait_run(run.id)).status == "completed"
    assert metricas.valor("pathfinder.desfecho", resultado="aprendeu") == 1
    dist = _espera_s()
    assert dist is not None and dist["n"] == 1 and dist["max"] == 30.0      # medido pelo relógio do desbravador
    for etapa in ("open_conversation", "compose_message", "send_message"):   # repetiu o que o líder aprendeu
        assert harness.ai.count("decide", instance="android-02", step=etapa) == 0, etapa
    assert any("android-02: parou de esperar o desbravador android-01" in m and "aprendido" in m
               for m in _decisoes(harness, run.id))


async def test_lider_que_falha_solta_os_demais_na_hora(harness: Harness) -> None:
    _liga(harness)
    harness.cfg.file.ai.cascade_blocked_to_tier1 = False    # o bloqueio forçado aqui é o que o teste exercita: a cascata do 17.10 o absorveria no tier 1
    portao = asyncio.Event()

    def bloqueia(req: Any) -> tuple[Decision, Usage]:
        return Decision(tool="step_blocked", args={"rationale": "teste", "kind": "missing_info",
                                                   "reason": "falta um dado para seguir", "needs_user": True}), Usage()

    _segura(harness, "android-01", portao, depois=bloqueia)
    run = harness.run(["android-01", "android-02"])
    await _esperando(harness, run.id, "android-02")

    portao.set()                                            # o líder para esperando uma pessoa — relógio parado
    await harness.wait(lambda: _status(harness, run.id, "android-02") == "succeeded", what="android-02 concluído")
    assert _obj(harness, run.id, "android-01")["status"] == "waiting_user"
    assert metricas.valor("pathfinder.desfecho", resultado="falhou") == 1
    assert metricas.valor("pathfinder.desfecho", resultado="expirou") == 0
    dist = _espera_s()
    assert dist is not None and dist["max"] == 0.0          # soltou sem o relógio andar: não esperou o teto
    assert harness.ai.count("decide", instance="android-02") > 0    # seguiu com a IA: não havia receita


async def test_teto_estourado_solta_com_desfecho_expirou(harness: Harness) -> None:
    relogio = _liga(harness)
    portao = asyncio.Event()
    _segura(harness, "android-01", portao)
    run = harness.run(["android-01", "android-02"])
    await _esperando(harness, run.id, "android-02")

    relogio.t += TETO                                      # o líder continua preso; o teto chega
    await harness.wait(lambda: _status(harness, run.id, "android-02") == "succeeded", what="android-02 concluído")
    assert metricas.valor("pathfinder.desfecho", resultado="expirou") == 1
    dist = _espera_s()
    assert dist is not None and dist["max"] == float(TETO)
    assert _obj(harness, run.id, "android-01")["status"] == "running"      # o líder segue; só a espera acabou
    portao.set()
    assert (await harness.wait_run(run.id)).status == "completed"


async def test_versao_diferente_nao_espera_o_mesmo_lider(harness: Harness) -> None:
    _liga(harness)
    devs = harness.state.devices.devices                                    # type: ignore[union-attr]
    # Versão conhecida pelo cache do executor (a mesma chave das receitas). android-02 tem OUTRA versão do app.
    devs["android-01"].app_versions[PKG] = "1.0(1)"
    devs["android-02"].app_versions[PKG] = "2.0(7)"
    devs["android-03"].app_versions[PKG] = "1.0(1)"
    harness._factory(type("RT", (), {"id": "android-02", "index": 2})()).version = "2.0(7)"
    portao = asyncio.Event()
    _segura(harness, "android-01", portao)
    run = harness.run(["android-01", "android-02", "android-03"])

    o3 = await _esperando(harness, run.id, "android-03")    # mesma versão do líder: espera
    assert "desbravador android-01" in o3["status_detail"]
    # android-02 não espera um caminho que não serviria para ele: abre o próprio, com a IA, enquanto o líder da 1.0
    # continua preso
    await harness.wait(lambda: _status(harness, run.id, "android-02") == "succeeded", what="android-02 concluído")
    assert harness.ai.count("decide", instance="android-02") > 0
    assert _obj(harness, run.id, "android-03")["wait_reason"] == "pathfinder"
    grupos = harness.state.scheduler._pathfinders[run.id]                   # type: ignore[union-attr]
    assert sorted(g.instance_id for g in grupos) == ["android-01", "android-02"]

    portao.set()
    assert (await harness.wait_run(run.id)).status == "completed"
    assert metricas.valor("pathfinder.desfecho", resultado="aprendeu") == 1          # só android-03 esperou
    assert harness.ai.count("decide", instance="android-03", step="send_message") == 0
    db = harness.state.db                                                   # type: ignore[union-attr]
    assert db.scalar("SELECT COUNT(*) FROM recipes WHERE app_version='2.0(7)'") >= 3  # cada grupo aprendeu o seu


async def test_caminho_ja_aberto_ninguem_espera(harness: Harness) -> None:
    """Comando repetido com receita ativa em TODA etapa, para a chave de cada aparelho: não há o que desbravar."""
    harness.cfg.file.ai.recipes = "replay"
    primeira = await harness.wait_run(harness.run(["android-01"]).id)
    assert primeira.status == "completed"
    db = harness.state.db                                                   # type: ignore[union-attr]
    modelo = db.one("SELECT * FROM recipes WHERE status='active' ORDER BY id LIMIT 1")
    # Etapas que não viram receita (só leitura) ganham uma receita inócua — diverge na 1ª ação e a IA conclui; o
    # que se testa é o despacho, que só olha se HÁ receita ativa para cada etapa.
    inocua = dumps([{"tool": "tap", "commit": False, "why": "teste", "args": {},
                     "selectors": [{"kind": "rid", "rid": "app:id/nao_existe"}]}])
    for s in db.query("SELECT DISTINCT key, template_hash FROM steps WHERE run_id=?", (primeira.id,)):
        if db.one("SELECT 1 FROM recipes WHERE step_hash=? AND status='active'", (s["template_hash"],)) is None:
            db.execute("INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key,"
                       " version, actions, created_at) VALUES (?,?,?,?,?,?,1,?,?)",
                       (PKG, modelo["app_version"], modelo["app_signature"], modelo["variant"], s["template_hash"],
                        s["key"], inocua, modelo["created_at"]))
    devs = harness.state.devices.devices                                    # type: ignore[union-attr]
    for iid in ("android-02", "android-03"):
        devs[iid].app_versions[PKG] = modelo["app_version"]

    _liga(harness)
    portao = asyncio.Event()
    _segura(harness, "android-01", portao)                  # o 1º da fila fica preso na etapa que a IA conduz
    run = harness.run(["android-01", "android-02", "android-03"])
    for iid in ("android-02", "android-03"):                # …e os outros NÃO esperam por ele
        await harness.wait(lambda iid=iid: _status(harness, run.id, iid) == "succeeded", what=f"{iid} concluído")
    assert _status(harness, run.id, "android-01") == "running"
    assert metricas.total("pathfinder.desfecho") == 0 and _espera_s() is None
    assert not any("desbravador" in m for m in _decisoes(harness, run.id))
    portao.set()
    assert (await harness.wait_run(run.id)).status in ("completed", "completed_with_issues")
