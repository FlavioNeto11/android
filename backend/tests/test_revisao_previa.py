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
from app.state import SettingsStore

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
async def test_revisao_codificacao_lenta_nao_sobrescreve_marcador_sensivel(harness: Harness,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """Era defeito F8: a prévia decidia "sensível" ANTES de codificar em thread, e uma hierarquia que classificava a
    tela como sensível DURANTE a codificação publicava o marcador para o frame codificado sobrescrevê-lo. Agora a
    publicação confere a classificação e a geração vigentes no instante de publicar (`_publicar_imagem`)."""
    s = harness.state
    assert s is not None
    devs = s.devices
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    await _sem_laco(rt)
    fake.screen = "home"
    await devs.observe(rt, timeout=5)                         # última classificação: tela comum
    assert not devs._tela_sensivel(rt)
    fake.screen = "login"                                     # a tela virou a de senha; ninguém leu a árvore ainda

    original = manager_mod._encode_frame
    entrou, liberar = threading.Event(), threading.Event()

    def codificacao_lenta(png: bytes) -> Any:
        # O pool padrão do `to_thread` é dividido com banco, evidência e o resumo de desempenho: fila ali é real.
        entrou.set()
        liberar.wait(5)
        return original(png)

    monkeypatch.setattr(manager_mod, "_encode_frame", codificacao_lenta)
    ciclo = asyncio.create_task(devs._ciclo_de_previa(rt))
    assert await asyncio.to_thread(entrou.wait, 5)
    devs.arvore(rt, fake.page_source())                       # ex.: `quick_tree` da IA relê a tela: SENSÍVEL
    assert rt.frame is not None and rt.frame.sensitive        # o marcador tirou a imagem do ar
    liberar.set()
    await ciclo
    assert devs._tela_sensivel(rt), "a última leitura continua dizendo que a tela é sensível"
    async with _cliente(harness) as c:
        r = await c.get("/api/instances/android-01/frame")
    assert rt.frame.sensitive and r.status_code == 404, (
        f"a imagem da tela de senha voltou ao ar (HTTP {r.status_code}) sem leitura dizendo que deixou de ser "
        "sensível")


async def test_revisao_marcador_por_hierarquia_nao_declara_captura_recuperada(harness: Harness) -> None:
    """Era defeito F8: o marcador publicado por LEITURA DE HIERARQUIA zerava `capture_failures` e anunciava "captura
    recuperada" sem screencap nenhum ter funcionado. Só um screencap que funcionou apaga a falha da captura."""
    s = harness.state
    assert s is not None
    devs = s.devices
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    await _sem_laco(rt)
    fake.screen = "home"
    await devs.observe(rt, timeout=5)
    # O screencap passou a falhar (o android-09 congelado de 25/09 começou assim); a hierarquia ainda responde.
    devs._falha_de_captura(rt, "DriverTimeout: screencap")
    devs._falha_de_captura(rt, "DriverTimeout: screencap")
    assert rt.capture_failures == 2 and rt.capture_error
    fila = s.bus.subscribe()
    fake.screen = "login"
    devs.arvore(rt, fake.page_source())                       # a IA releu a tela: sensível -> marcador
    textos = []
    while not fila.empty():
        ev = fila.get_nowait()
        textos.append(str(ev.message or ""))
    s.bus.unsubscribe(fila)
    assert rt.frame is not None and rt.frame.sensitive
    assert not any("recuperada" in t for t in textos), "nenhum screencap funcionou para a captura 'voltar'"
    assert rt.capture_failures == 2 and rt.capture_error, "a falha da captura foi apagada por uma leitura de árvore"


async def test_revisao_marcador_sensivel_nao_vaza_por_evento_cache_nem_rota(harness: Harness) -> None:
    """Tela sensível: nenhum caminho de saída carrega imagem — `frame` e `instance.updated` só com metadados,
    `recent_frames` só com (instante, largura, altura), `/frame` thumb e full com 404 `sensitive_screen`, e a
    observação da IA sem `jpeg`."""
    s = harness.state
    assert s is not None
    devs = s.devices
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    await _sem_laco(rt)
    fila = s.bus.subscribe()
    fake.screen = "login"
    obs = await devs.observe(rt, timeout=5)
    assert obs.jpeg is None and rt.frame is not None and rt.frame.jpeg_full == b"" == rt.frame.jpeg_thumb
    for valor in rt.recent_frames.values():
        assert isinstance(valor, tuple) and len(valor) == 3 and not any(isinstance(v, (bytes, bytearray)) for v in valor)
    devs.publish(rt)
    eventos = []
    while not fila.empty():
        eventos.append(fila.get_nowait())
    s.bus.unsubscribe(fila)
    assert any(ev.kind == "frame" for ev in eventos) and any(ev.kind == "instance.updated" for ev in eventos)
    for ev in eventos:
        bruto = dumps(ev.data or {})
        # "/9j/" é o começo de todo JPEG em base64; `jpeg_full`/`jpeg_thumb` são os campos do `Frame` em memória.
        assert "/9j/" not in bruto and "jpeg_full" not in bruto and "jpeg_thumb" not in bruto, ev.kind
        assert len(bruto) < 20_000, f"{ev.kind}: evento grande demais para ser só metadado"
    async with _cliente(harness) as c:
        for modo in ("thumb", "full"):
            r = await c.get(f"/api/instances/android-01/frame?mode={modo}")
            assert r.status_code == 404 and r.json()["detail"]["code"] == "sensitive_screen"


# ------------------------------------------------------------------------------------------ compatibilidade
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
