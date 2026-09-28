"""Projeção e orçamento por ação (item 18.3; execução r-20260928165254-e31953: 31 chamadas e 18,7 min num plano cujo
normal medido era 16–28 chamadas e 3–5 min, sem nada dizer que estava fora)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.db import Database
from app.taskqueue.projecao import EstatisticaDeAcao, Faixa, HistoricoDeAcoes, app_da_etapa, projetar, resumo

from .conftest import Harness, make_config

PRECOS = {"modelo-x": [1.0, 0.1, 1.25, 5.0]}


class _BancoFalso:
    """Devolve linhas já no formato da consulta agregada (uma por etapa e modelo)."""

    def __init__(self, linhas: list[dict[str, Any]]) -> None:
        self.linhas = linhas
        self.consultas = 0

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.consultas += 1
        return self.linhas


def _etapa(sid: str, cap: str | None, n: int, seg: int, *, app_ids: str = '["instagram"]') -> dict[str, Any]:
    return {"sid": sid, "cap": cap, "app_id": None, "app_ids": app_ids, "ini": "2026-09-28T10:00:00.000Z",
            "fim": f"2026-09-28T10:{seg // 60:02d}:{seg % 60:02d}.000Z", "modelo": "modelo-x" if n else None, "n": n,
            "i": 1000 * n, "cr": 0, "cw": 0, "o": 100 * n, "usd_decl": 0}


def test_mediana_e_p90_por_app_e_acao_com_releitura_limitada() -> None:
    linhas = [_etapa(f"s{i}", "OPEN_POST", n, 20 + 10 * i) for i, n in enumerate([1, 2, 3, 3, 4, 9])]
    linhas.append(_etapa("q1", None, 1, 5, app_ids='["qa-messenger"]'))
    banco = _BancoFalso(linhas)
    agora = [0.0]
    h = HistoricoDeAcoes(banco, lambda: PRECOS, ttl_s=60, relogio=lambda: agora[0])
    est = h.de("instagram", "OPEN_POST")
    assert est is not None and est.amostras == 6
    assert est.chamadas == Faixa(3.0, 9.0) and est.segundos.p50 == 40.0
    assert round(est.usd.p50, 6) == round((3 * 1000 * 1.0 + 3 * 100 * 5.0) / 1e6, 6)
    assert h.de("qa-messenger", "*") is not None and h.de("instagram", "LIKE_POST") is None
    h.de("instagram", "OPEN_POST")
    assert banco.consultas == 1                               # dentro do TTL não relê a cada chamada de IA
    agora[0] = 61.0
    h.de("instagram", "OPEN_POST")
    assert banco.consultas == 2
    # orçamento: max(p90 × fator, p90 + folga); sem amostras suficientes, não há orçamento (vale o teto do objetivo)
    assert h.orcamento_de_chamadas("instagram", "OPEN_POST", fator=2.0, folga=4, minimo=5)[0] == 18
    assert h.orcamento_de_chamadas("instagram", "OPEN_POST", fator=1.0, folga=4, minimo=5)[0] == 13
    assert h.orcamento_de_chamadas("instagram", "OPEN_POST", fator=2.0, folga=4, minimo=7) is None


def test_projecao_soma_etapas_marca_sem_base_e_nunca_inventa() -> None:
    h = HistoricoDeAcoes(_BancoFalso([_etapa(f"s{i}", "OPEN_POST", 2, 60) for i in range(5)]
                                     + [_etapa(f"l{i}", None, 1, 30) for i in range(5)]), lambda: PRECOS)
    p = projetar([("abrir", "Abrir", "instagram", "OPEN_POST"), ("coment", "Comentar", "instagram", "CREATE_COMMENT"),
                  ("outro", "Outro app", "outro", "X")], h, minimo=5)
    assert p["sem_base"] == ["coment", "outro"]
    etapas = {e["key"]: e for e in p["etapas"]}  # type: ignore[union-attr]
    assert etapas["coment"]["calls"] == {"p50": 1.0, "p90": 1.0}   # sem base própria: o "*" do app, marcado
    assert etapas["outro"]["calls"] == {"p50": 0.0, "p90": 0.0} and etapas["outro"]["no_baseline"]
    assert p["chamadas"] == {"p50": 3.0, "p90": 3.0}
    texto = resumo(p)
    assert texto.startswith("3–3 chamadas de IA, US$ 0,") and "2 etapa(s) sem base" in texto
    vazio = projetar([("a", "A", "novo", "X")], HistoricoDeAcoes(_BancoFalso([]), lambda: PRECOS))
    assert resumo(vazio) == "sem histórico suficiente para projetar as 1 etapa(s) — a primeira execução mede"


def test_app_da_etapa_vem_da_execucao_quando_a_etapa_nao_grava() -> None:
    assert app_da_etapa(None, '["instagram","qa-messenger"]') == "instagram"
    assert app_da_etapa("qa-messenger", '["instagram"]') == "qa-messenger"
    assert app_da_etapa(None, None) == "*" and app_da_etapa(None, "não é json") == "*"


def test_a_consulta_roda_num_banco_migrado(tmp_path: Path) -> None:
    """A consulta agregada é SQL portável (GROUP BY completo): roda no SQLite e, com `TEST_DATABASE_URL`, no
    PostgreSQL. Banco vazio = nenhuma estatística, sem erro."""
    cfg = make_config(tmp_path)
    db = Database(cfg.db_dsn)
    db.migrate()
    try:
        assert HistoricoDeAcoes(db, lambda: PRECOS).estatisticas() == {}
    finally:
        db.close()


class _HistoricoApertado:
    """Orçamento de 1 chamada para qualquer etapa: a segunda chamada de IA de uma etapa é recusada."""

    janela_dias = 30

    def orcamento_de_chamadas(self, app: str, acao: str | None, **kw: Any) -> tuple[int, EstatisticaDeAcao]:
        est = EstatisticaDeAcao(app=app, acao=acao or "*", amostras=9, chamadas=Faixa(1.0, 1.0),
                                segundos=Faixa(10.0, 20.0), usd=Faixa(0.0, 0.0))
        return 1, est


async def test_etapa_que_passa_do_orcamento_para_com_o_motivo(harness: Harness) -> None:
    assert harness.state is not None
    harness.state.scheduler.executor.historico = _HistoricoApertado()          # type: ignore[assignment]
    run = harness.run(["android-01"])
    detalhe = await harness.wait_run(run.id)
    paradas = [s for s in detalhe.steps if "orçamento de 1 chamadas de IA" in (s.status_detail or "")]
    assert paradas, [(s.key, s.status, s.status_detail) for s in detalhe.steps]
    assert all(s.status in ("failed", "uncertain", "cancelled") for s in paradas)
    assert detalhe.objectives[0].status != "succeeded"                         # falha nunca conta como sucesso


async def test_rota_da_projecao_do_plano(harness: Harness) -> None:
    """`GET /api/runs/{id}/projection`: a soma por etapa do plano, sem chamar IA; 404 sem execução. O plano do
    harness é de execução simulada, que fica fora do histórico — as etapas vêm todas marcadas sem base."""
    import httpx

    from app.main import create_app

    assert harness.state is not None
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    run = harness.run(["android-01"])
    await harness.wait_run(run.id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/runs/{run.id}/projection")
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["etapas"] and set(corpo["sem_base"]) == {e["key"] for e in corpo["etapas"]}
        assert set(corpo["chamadas"]) == {"p50", "p90"} and corpo["minimo_de_amostras"] >= 1
        assert (await c.get("/api/runs/r-inexistente/projection")).status_code == 404
