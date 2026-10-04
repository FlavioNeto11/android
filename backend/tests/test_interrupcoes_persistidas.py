"""Fração de interrupção do convidado persistida (`measurements`, kind='irq') e servida por `/api/desempenho`.

Em 28/09 o android-06 (68 h no ar) e o android-04 (44 h) passavam 21% e 90% da CPU em irq+softirq com o aparelho
ocioso, e ~2% depois do reinício a frio. A causa do acúmulo não é conhecida (hipóteses: acertos de relógio com
TIME_SET, prévia aberta). O `DeviceManager` já media a fração entre duas sondas de saúde, mas só em memória: nada
sobrava para cruzar com o relógio, com a prévia ou com os dias no ar. Aqui se prova que cada medida vira uma linha
consultável — e que a regra do reinício automático não muda por causa disso.

"Cada sonda com ticks grava" se lê junto com "com instância, irq e load": a sonda que tem ticks mas não tem leitura
anterior (a primeira, ou a primeira depois de o convidado reiniciar) não tem fração a gravar. Gravar `irq_frac`
nulo seria registrar ausência como se fosse medida.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.db import loads
from app.devices.diagnostics import medicoes_recentes
from app.main import create_app
from app.models import ControlOwner, InstanceState

from .conftest import Harness


def _ticks(total: float, irq: float, load1: float = 0.5) -> dict[str, float]:
    return {"load1": load1, "mem_total_mb": 2048.0, "mem_available_mb": 900.0, "ncpu": 2.0,
            "cpu_total_ticks": total, "cpu_irq_ticks": irq}


def _linhas_irq(h: Harness) -> list[dict[str, Any]]:
    assert h.state is not None
    return [{"ts": r["ts"], **loads(r["data"], {})}
            for r in h.state.db.query("SELECT ts, data FROM measurements WHERE kind='irq' ORDER BY id")]


async def _aparelho_no_ar(h: Harness) -> Any:
    await h.boot()
    assert h.state is not None
    rt = h.state.devices.get("android-01")
    rt.state, rt.attention, rt.control = InstanceState.online, None, ControlOwner.none
    # A sonda periódica do monitor também chama `conferir_saude`: empurrada 30 s para frente, as linhas contadas
    # aqui são só as das sondas que o teste dispara.
    rt.health_checked_mono = time.monotonic()
    return rt


@pytest.mark.asyncio
async def test_cada_sonda_com_fracao_grava_uma_medida_com_instancia_irq_e_load(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    rt = await _aparelho_no_ar(h)
    try:
        assert h.state is not None
        d = h.state.devices
        d.on_health_restart = lambda iid, motivo: None
        d._cpu_do_host = lambda: 10.0  # type: ignore[method-assign]  # host calmo: a regra de IRQ conta (29.67)
        total, irq = 1000.0, 0.0
        rt.io.pressure = _ticks(total, irq)
        await d.conferir_saude(rt)
        assert _linhas_irq(h) == []                         # primeira leitura: só guarda os ticks, sem fração
        for carga in (0.4, 0.9):
            total, irq = total + 1000, irq + 300            # 30% em interrupção
            rt.io.pressure = _ticks(total, irq, load1=carga)
            await d.conferir_saude(rt)
        linhas = _linhas_irq(h)
        assert len(linhas) == 2
        for linha, carga in zip(linhas, (0.4, 0.9)):
            assert linha["instance_id"] == "android-01"
            assert linha["irq_frac"] == pytest.approx(0.3)
            assert linha["load1"] == carga and linha["ncpu"] == 2.0
            assert linha["ocioso"] is True and linha["controle"] == "none"
            assert "interesse" in linha                     # prévia aberta é uma das hipóteses da causa
            assert linha["ts"]
    finally:
        assert h.state is not None
        await h.state.stop()


@pytest.mark.asyncio
async def test_medida_grava_com_alguem_no_controle_e_a_regra_do_reinicio_nao_muda(tmp_path: Path) -> None:
    """Com a IA no controle a fração é gravada igual (é o dado que falta), mas o contador de reinício não sobe."""
    h = Harness(tmp_path, 1)
    rt = await _aparelho_no_ar(h)
    try:
        assert h.state is not None
        d = h.state.devices
        pedidos: list[str] = []
        d.on_health_restart = lambda iid, motivo: pedidos.append(iid) or None
        d._cpu_do_host = lambda: 10.0  # type: ignore[method-assign]  # host calmo: a regra de IRQ conta (29.67)
        rt.control = ControlOwner.ai
        total, irq = 1000.0, 0.0
        for _ in range(5):
            rt.io.pressure = _ticks(total, irq)
            await d.conferir_saude(rt)
            total, irq = total + 1000, irq + 500            # 50% em interrupção
        linhas = _linhas_irq(h)
        assert len(linhas) == 4                             # 5 sondas, a primeira sem fração
        assert all(ln["controle"] == "ai" and ln["ocioso"] is False for ln in linhas)
        assert rt.irq_strikes == 0 and pedidos == []
    finally:
        rt.control = ControlOwner.none
        assert h.state is not None
        await h.state.stop()


@pytest.mark.asyncio
async def test_sonda_sem_ticks_ou_com_convidado_reiniciado_nao_grava(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    rt = await _aparelho_no_ar(h)
    try:
        assert h.state is not None
        d = h.state.devices
        d.on_health_restart = lambda iid, motivo: None
        d._cpu_do_host = lambda: 10.0  # type: ignore[method-assign]  # host calmo: a regra de IRQ conta (29.67)
        sem_ticks = {"load1": 0.5, "mem_total_mb": 2048.0, "mem_available_mb": 900.0, "ncpu": 2.0}
        for _ in range(3):
            rt.io.pressure = dict(sem_ticks)
            await d.conferir_saude(rt)
        assert _linhas_irq(h) == []
        # Convidado reiniciado entre duas sondas: os ticks voltam para trás, e não há fração a medir.
        rt.io.pressure = _ticks(50_000.0, 4_000.0)
        await d.conferir_saude(rt)
        rt.io.pressure = _ticks(1_000.0, 20.0)
        await d.conferir_saude(rt)
        assert _linhas_irq(h) == []
    finally:
        assert h.state is not None
        await h.state.stop()


@pytest.mark.asyncio
async def test_diagnostico_nao_lista_as_medidas_de_interrupcao(tmp_path: Path) -> None:
    """Duas linhas por minuto por aparelho empurrariam boot, relógio e capacidade para fora das 60 do Diagnóstico —
    o mesmo motivo de `metricas` ficar fora."""
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        assert h.state is not None
        db = h.state.db
        db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)",
                   ("2026-09-28T10:00:00+00:00", "boot", "{}"))
        db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)",
                   ("2026-09-28T10:00:30+00:00", "irq", '{"instance_id":"android-01","irq_frac":0.2}'))
        kinds = [m["kind"] for m in medicoes_recentes(db)]
        assert "boot" in kinds and "irq" not in kinds
    finally:
        assert h.state is not None
        await h.state.stop()


@pytest.mark.asyncio
async def test_rota_de_desempenho_devolve_a_serie_e_o_ultimo_valor_por_aparelho(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    rt = await _aparelho_no_ar(h)
    try:
        assert h.state is not None
        d = h.state.devices
        d.on_health_restart = lambda iid, motivo: None
        d._cpu_do_host = lambda: 10.0  # type: ignore[method-assign]  # host calmo: a regra de IRQ conta (29.67)
        total, irq = 1000.0, 0.0
        for fatia in (0, 100, 200, 300):                    # 3 frações: 10%, 20%, 30%
            irq += fatia
            rt.io.pressure = _ticks(total, irq, load1=0.5)
            await d.conferir_saude(rt)
            total += 1000
        # Uma medida antiga, fora da janela pedida, que não pode aparecer.
        h.state.db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)",
                           ("2026-01-01T00:00:00+00:00", "irq",
                            '{"instance_id":"android-02","irq_frac":0.9,"load1":0.1,"ncpu":2}'))

        app = create_app(h.cfg, state=h.state)
        app.state.poc = h.state
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            sem = await c.get("/api/desempenho")
            com = await c.get("/api/desempenho", params={"irq_horas": 6})
            filtrado = await c.get("/api/desempenho", params={"irq_horas": 6, "irq_aparelho": "android-02"})
        assert sem.status_code == 200 and "interrupcoes" not in sem.json()     # sem pedir, a resposta de antes
        assert com.status_code == 200, com.text
        bloco = com.json()["interrupcoes"]
        assert bloco["truncada"] is False and bloco["n"] == 3
        assert set(bloco["por_aparelho"]) == {"android-01"}                     # a de janeiro ficou fora
        a1 = bloco["por_aparelho"]["android-01"]
        assert [p["irq_frac"] for p in a1["serie"]] == pytest.approx([0.1, 0.2, 0.3])
        assert all(p["load1"] == 0.5 and p["ts"] for p in a1["serie"])
        assert a1["ultimo"]["irq_frac"] == pytest.approx(0.3) and a1["ultimo"]["ts"] == a1["serie"][-1]["ts"]
        assert a1["irq_frac"]["n"] == 3 and a1["irq_frac"]["max"] == pytest.approx(0.3)
        assert filtrado.status_code == 200 and filtrado.json()["interrupcoes"]["por_aparelho"] == {}
    finally:
        assert h.state is not None
        await h.state.stop()


@pytest.mark.asyncio
async def test_serie_cortada_pelo_teto_diz_truncada_e_guarda_as_mais_recentes(tmp_path: Path) -> None:
    """Série cortada nunca passa por série inteira; e o filtro por aparelho confere o id exato (o `_` do LIKE casaria
    `android-0X` com `android_0X`)."""
    from app.desempenho import interrupcoes

    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        assert h.state is not None
        db = h.state.db
        for i, (iid, frac) in enumerate([("android-01", 0.1), ("android_01", 0.5), ("android-01", 0.2),
                                         ("android-01", 0.3)]):
            db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)",
                       (f"2026-09-28T10:00:{i:02d}+00:00", "irq", f'{{"instance_id":"{iid}","irq_frac":{frac}}}'))
        inteiro = interrupcoes(db, desde_iso="2026-09-28T00:00:00+00:00", aparelho="android-01")
        assert inteiro["truncada"] is False and inteiro["n"] == 3
        cortado = interrupcoes(db, desde_iso="2026-09-28T00:00:00+00:00", aparelho="android-01", limite=2)
        assert cortado["truncada"] is True
        por: Any = cortado["por_aparelho"]
        assert [p["irq_frac"] for p in por["android-01"]["serie"]] == [0.2, 0.3]
        assert por["android-01"]["ultimo"]["irq_frac"] == 0.3
    finally:
        assert h.state is not None
        await h.state.stop()
