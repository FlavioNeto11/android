"""Revisão independente F8 (evolução de desempenho, onda 1): prévia sob demanda, tela sensível, métricas e
compatibilidade.

Cada teste aqui é PROVA de um cenário obrigatório da revisão. Os marcados `xfail(strict=True)` provam um DEFEITO:
afirmam o comportamento do contrato e falham hoje; quando a correção entrar, o `strict` reprova o arquivo e obriga a
tirar a marca. Os demais são prova de cobertura de um risco que a suíte da frente não exercitava.

Tudo simulado: harness com aparelho falso (`base_console_port: 5640`), provedor simulado, sem adb nem rede.
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

import app.devices.manager as manager_mod
from app.config import AppConfigFile, LimitsCfg
from app.db import dumps
from app.main import create_app
from app.metricas import Metricas, metricas
from app.models import ControlOwner
from app.bootstrap import SettingsStore

from .conftest import COMMAND, Harness


def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _sem_laco(rt: Any) -> None:
    t = rt.tasks.get("capture")
    if t is not None and not t.done():
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


def _com_laco(devs: Any, rt: Any) -> asyncio.Task[Any]:
    t = asyncio.create_task(devs._capture_loop(rt), name=f"capture-{rt.id}")
    rt.tasks["capture"] = t
    return t


def _caps(h: Harness) -> int:
    return sum(1 for f in h.fakes.values() for c in f.calls if c == "screenshot")


# ------------------------------------------------------------------------------------------ prévia sob demanda
async def test_revisao_conexao_nova_nao_dispara_rajada_de_captura(harness: Harness) -> None:
    """Painel novo abre o WebSocket e, um instante depois (a ida e volta da rede), manda `watch` vazio (aba sem
    nenhum cartão visível). Nenhum aparelho está sendo olhado em momento algum: o contrato C2 diz zero screencap
    de prévia sem interesse.

    Era defeito F8 (toda conexão passa pelo estado "legado", e `interesse_legado` acordava o laço de todos os
    aparelhos); corrigido em a3dd949 — o legado só registra, e o painel antigo recebe no ritmo da grade."""
    s = harness.state
    assert s is not None
    devs = s.devices
    assert devs._interesses == {}
    tarefas = []
    for rt in devs.devices.values():
        await _sem_laco(rt)
        tarefas.append(_com_laco(devs, rt))
    await asyncio.sleep(0.3)                      # 1ª volta sem interesse: todos estacionam na espera da grade
    antes = _caps(harness)
    devs.interesse_legado("ws-revisao")           # `listen()` registra o legado ao começar a ler
    await asyncio.sleep(0.05)                     # o `watch` do navegador ainda está na rede
    devs.registrar_interesse("ws-revisao", [], None, 20)   # chegou: esta aba não olha nada
    await asyncio.sleep(0.3)
    capturas = _caps(harness) - antes
    devs.soltar_interesse("ws-revisao")
    for t in tarefas:
        t.cancel()
    assert capturas == 0, f"{capturas} screencap(s) de prévia sem ninguém olhando, só por abrir a conexão"


async def test_revisao_legado_e_grade_nao_seguram_rodizio_nem_renovam_lease(harness: Harness) -> None:
    """Rodízio e hibernação leem `rt.focused` (scheduler.evictable). Nem o painel antigo (legado = grade em todos)
    nem a grade do painel novo podem torná-lo verdadeiro, nem renovar o lease de quem controla."""
    s = harness.state
    assert s is not None
    devs = s.devices
    rt = devs.get("android-01")
    await _sem_laco(rt)
    devs.interesse_legado("antigo")
    devs.registrar_interesse("novo", ["android-01"], None, 20)
    assert devs.nivel_de_interesse(rt) == "grade" and not rt.focused
    status, lease = devs.request_control(rt)
    assert status == "granted"
    rt.lease_expires_mono = 0.0
    devs.interesse_legado("antigo-2")
    devs.registrar_interesse("novo", ["android-01"], None, 20)
    assert rt.lease_expires_mono == 0.0, "interesse de grade não é quem está com a tela aberta"
    devs.release_control(rt, lease)
    for c in ("antigo", "antigo-2", "novo"):
        devs.soltar_interesse(c)


async def test_revisao_lease_manual_so_vive_com_foco_aberto(harness: Harness) -> None:
    """Quem segura o controle sem tocar: enquanto ALGUM painel tem o aparelho em `watch.focus` (renovado a cada
    8 s pelo painel), o lease não vence — paridade com o `focus` antigo, que também renovava por qualquer
    conexão. Sem foco aberto, o lease de 10 min corre e o monitor devolve o controle."""
    s = harness.state
    assert s is not None
    devs = s.devices
    rt = devs.get("android-02")
    await _sem_laco(rt)
    status, lease = devs.request_control(rt)
    assert status == "granted" and rt.control == ControlOwner.user
    rt.lease_expires_mono = 1.0
    devs.registrar_interesse("outro-operador", [], "android-02", 20)
    assert rt.lease_expires_mono > manager_mod.time.monotonic() + manager_mod.MANUAL_LEASE_TTL_S - 5
    devs.registrar_interesse("outro-operador", [], None, 20)   # fechou o foco
    rt.lease_expires_mono = 1.0
    devs.registrar_interesse("outro-operador", ["android-02"], None, 20)
    assert rt.lease_expires_mono == 1.0
    devs.release_control(rt, lease)
    devs.soltar_interesse("outro-operador")


# ------------------------------------------------------------------------------------------ tela sensível (C4)
async def test_revisao_config_e_limites_antigos_sem_preview_mode(harness: Harness) -> None:
    """Instalação anterior ao adendo: `config.yaml` e a linha `settings.limits` do banco sem `preview_mode`
    carregam com `on_demand`; PUT aceita só os dois valores."""
    arquivo = AppConfigFile.model_validate({"limits": {"capture_grid_interval_s": 5}})
    assert arquivo.limits.preview_mode == "on_demand"
    s = harness.state
    assert s is not None
    antigo = {k: v for k, v in LimitsCfg().model_dump().items() if k != "preview_mode"}
    s.db.execute("INSERT INTO settings(key, value) VALUES ('limits', ?) ON CONFLICT(key) DO UPDATE SET "
                 "value=excluded.value", (dumps(antigo),))
    assert SettingsStore(s.db, LimitsCfg()).get().preview_mode == "on_demand"
    async with _cliente(harness) as c:
        ruim = await c.put("/api/settings", json={"preview_mode": "sempre"})
        assert ruim.status_code == 400, ruim.text
        bom = await c.put("/api/settings", json={"preview_mode": "always"})
        assert bom.status_code == 200 and bom.json()["preview_mode"] == "always"
        volta = await c.put("/api/settings", json={"preview_mode": "on_demand"})
        assert volta.status_code == 200 and volta.json()["preview_mode"] == "on_demand"


# ------------------------------------------------------------------------------------------ métricas e privacidade
async def test_revisao_desempenho_sem_texto_de_comando_nem_perfil(harness: Harness) -> None:
    """`/api/desempenho` (acumulado, janelas e `historico`) não carrega texto de comando nem @ de perfil."""
    s = harness.state
    assert s is not None
    marca = "@perfil_f8_revisao"
    run = harness.run(["android-01"], command=COMMAND + f" Responda a {marca} com cuidado XYZZY-F8.")
    await harness.wait_run(run.id, timeout=60)
    s.gravar_janela_de_metricas()
    async with _cliente(harness) as c:
        r = await c.get("/api/desempenho", params={"dias": 1, "janelas": 10})
    assert r.status_code == 200, r.text
    assert "historico" in r.json()
    assert marca not in r.text and "XYZZY-F8" not in r.text and "perfil_f8_revisao" not in r.text
    assert run.id not in r.text, "id de execução não é rótulo nem campo do agregado"


def test_revisao_descarte_de_serie_conta_uma_vez_por_observacao() -> None:
    """Achado F8 (corrigido): cada observação passa pelo acumulado e pela janela, e só o acumulado conta o descarte."""
    m = Metricas(max_series=1)
    m.contar("x", motivo="a")
    m.contar("x", motivo="b")                                 # fora do teto: vai para `_excedente`
    assert m.valor("x", _excedente="sim") == 1
    assert m.snapshot()["series_descartadas"] == 1


def test_revisao_rotulo_longo_e_cortado_e_nao_cresce_sem_teto() -> None:
    """Rótulo com valor livre (texto de tela, por engano) é cortado em 48 caracteres e cai no excedente depois do
    teto: o registro não cresce sem limite, e a contagem total não se perde."""
    m = Metricas(max_series=5)
    for i in range(50):
        m.contar("receita.retorno_ia", motivo=f"texto livre da tela número {i} " + "x" * 200)
    snap = m.snapshot()
    assert len(snap["contadores"]) <= 6
    assert all(len(v) <= 48 for c in snap["contadores"] for v in c["rotulos"].values())
    assert m.total("receita.retorno_ia") == 50
    metricas.limpar()
