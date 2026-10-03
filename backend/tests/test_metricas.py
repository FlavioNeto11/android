"""Métricas agregadas de desempenho (`app/metricas.py`, contrato C5 do adendo v0.20).

O que se prova aqui é a REGRA da medição, não número de desempenho: contar e distribuir sem crescer sem limite,
não confundir "sem amostra" com zero, gravar UMA linha por janela e não empurrar as medições do Diagnóstico.
"""
from __future__ import annotations

import threading

import httpx

from app.db import loads
from app.devices.diagnostics import medicoes_recentes
from app.main import create_app
from app.metricas import AMOSTRA, Metricas, metricas, percentil

from .conftest import Harness


def test_contador_e_distribuicao_por_rotulo() -> None:
    m = Metricas(seed=1)
    m.contar("captura.total", origem="previa", resultado="ok")
    m.contar("captura.total", 2, origem="previa", resultado="ok")
    m.contar("captura.total", origem="observacao", resultado="ok")
    for v in (10, 20, 30, 40, 1000):
        m.observar("captura.ms", v, origem="previa")
    assert m.valor("captura.total", origem="previa", resultado="ok") == 3
    assert m.total("captura.total") == 4
    dist = next(d for d in m.snapshot()["distribuicoes"] if d["nome"] == "captura.ms")
    assert dist["n"] == 5 and dist["min"] == 10 and dist["max"] == 1000
    assert dist["p50"] == 30 and dist["p95"] == 1000 and dist["amostra_n"] == 5


def test_sem_amostra_e_desconhecido_nao_zero() -> None:
    assert percentil([], 50) is None
    assert Metricas().snapshot()["distribuicoes"] == []


#: K-085: os (n, p) com n ≤ 40 e p em {50, 90, 95, 99} em que o `round(p/100·n + 0,5)` antigo (arredondamento de
#: banqueiro no ,5) subia o posto um acima do mais próximo. Posto 1-based certo = ceil(p·n/100).
DESLOCADOS = ((2, 50), (6, 50), (10, 50), (10, 90), (14, 50), (18, 50), (20, 95), (22, 50), (26, 50), (30, 50),
              (30, 90), (34, 50), (38, 50))


def test_percentil_pelo_posto_mais_proximo_sem_o_arredondamento_de_banqueiro() -> None:
    for n, p in DESLOCADOS:
        valores = [float(i) for i in range(1, n + 1)]                  # o valor é o próprio posto 1-based
        assert percentil(valores, p) == p * n // 100, (n, p)            # p·n/100 inteiro: o posto é ele mesmo
        assert percentil(valores, p) != round(p / 100 * n + 0.5), (n, p)  # o antigo dava o posto seguinte
    assert percentil([10.0, 20.0], 50) == 10.0                           # a mediana de 2 é o menor, não o maior


def test_percentil_e_o_mesmo_da_sombra_da_decisao_fechada() -> None:
    """Um percentil só no processo: `sombra._p95` (que grava `decisao_fechada_diario.ms_p95`) delega a este."""
    import math

    from app.planning.decisao_fechada.sombra import _p95

    for n in range(1, 201):
        valores = [float((i * 37) % 211) for i in range(n)]            # fora de ordem, sem repetição
        ordenada = sorted(valores)
        assert _p95(valores) == percentil(ordenada, 95) == round(ordenada[math.ceil(95 * n / 100) - 1], 3)
        for p in (50, 90, 99):
            assert percentil(ordenada, p) == ordenada[math.ceil(p * n / 100) - 1]
    assert _p95([]) is None


def test_teto_de_series_vai_para_excedente_e_conta_o_descarte() -> None:
    """Rótulo com valor livre (o erro que o teto existe para barrar) não faz o registro crescer sem limite, e a
    contagem não se perde: vai para `_excedente`."""
    m = Metricas(max_series=3)
    for i in range(10):
        m.contar("x", aparelho=f"a{i}")
    snap = m.snapshot()
    assert len(snap["contadores"]) == 4                     # 3 séries + a de excedente
    assert m.total("x") == 10
    assert m.valor("x", _excedente="sim") == 7
    assert snap["series_descartadas"] >= 7


def test_amostra_tem_tamanho_fixo() -> None:
    m = Metricas(seed=7)
    for i in range(AMOSTRA * 4):
        m.observar("d", float(i))
    dist = m.snapshot()["distribuicoes"][0]
    assert dist["n"] == AMOSTRA * 4 and dist["amostra_n"] == AMOSTRA


def test_janela_esvazia_sem_tocar_o_acumulado() -> None:
    m = Metricas()
    assert m.fechar_janela() is None                         # nada medido: não se grava linha vazia
    m.contar("c")
    j = m.fechar_janela()
    assert j is not None and j["contadores"][0]["valor"] == 1
    assert m.fechar_janela() is None
    assert m.valor("c") == 1                                 # o acumulado do processo segue


def test_escrita_concorrente_nao_perde_contagem() -> None:
    m = Metricas()

    def bater() -> None:
        for _ in range(2000):
            m.contar("c", origem="t")
            m.observar("d", 1.0)

    threads = [threading.Thread(target=bater) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert m.valor("c", origem="t") == 8000


def test_limpar_mantem_o_mesmo_objeto() -> None:
    antes = metricas
    metricas.contar("teste.limpar")
    metricas.limpar()
    from app.metricas import metricas as depois
    assert depois is antes and depois.total("teste.limpar") == 0


async def test_janela_gravada_e_rota_de_desempenho(harness: Harness) -> None:
    """Uma linha `measurements(kind='metricas')` por janela; o Diagnóstico não a lista; `/api/desempenho` devolve
    o acumulado do processo e as janelas."""
    metricas.limpar()
    metricas.contar("captura.evitada", motivo="sem_interesse")
    metricas.observar("captura.ms", 120.0, origem="previa")
    janela = harness.state.gravar_janela_de_metricas()
    assert janela is not None and janela["owner"] == harness.state.cfg.owner_id
    assert harness.state.gravar_janela_de_metricas() is None   # janela nova vazia: nenhuma linha a mais
    linhas = harness.state.db.query("SELECT data FROM measurements WHERE kind='metricas'")
    assert len(linhas) == 1 and loads(linhas[0]["data"], {})["contadores"][0]["nome"] == "captura.evitada"

    harness.state.db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)",
                             ("2026-09-26T00:00:00+00:00", "boot", "{}"))
    kinds = [m["kind"] for m in medicoes_recentes(harness.state.db)]
    assert "boot" in kinds and "metricas" not in kinds

    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/desempenho", params={"janelas": 5})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["processo"]["owner"] == harness.state.cfg.owner_id
    assert any(d["nome"] == "captura.ms" and d["p50"] == 120.0 for d in corpo["processo"]["distribuicoes"])
    assert len(corpo["janelas"]) == 1 and corpo["janelas"][0]["owner"] == harness.state.cfg.owner_id
    metricas.limpar()


async def test_rota_de_desempenho_com_historico(harness: Harness) -> None:
    """`?dias=N` acrescenta `historico` (`desempenho.resumo`); sem `dias`, a resposta é a de antes."""
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        sem = await c.get("/api/desempenho")
        com = await c.get("/api/desempenho", params={"dias": 7})
    assert sem.status_code == 200 and "historico" not in sem.json()
    assert com.status_code == 200, com.text
    assert isinstance(com.json()["historico"], dict)
