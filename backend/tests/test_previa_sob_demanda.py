"""Prévia sob demanda (contratos C2 e C3 do adendo v0.20).

Antes, o laço de captura tirava screencap de TODO aparelho online a cada `capture_grid_interval_s`, com ou sem
alguém olhando; o foco só mudava o ritmo. Agora o painel diz o que está olhando (`watch` pelo WebSocket), o
gerenciador agrega por aparelho e o laço só captura com interesse. Sem interesse, a tela é `paused` — não `stale`.

O relógio do registro de interesse é injetado (`devs.relogio`): TTL vencido se prova movendo o relógio, não
dormindo. Cada volta do laço é chamada à mão (`_volta_da_previa`) com a tarefa de verdade cancelada.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from starlette.testclient import TestClient

from app.main import create_app
from app.metricas import metricas
from app.models import ControlOwner

from .conftest import Harness


class Relogio:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


async def _sem_laco(rt: Any) -> None:
    t = rt.tasks.get("capture")
    if t is not None and not t.done():
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


def _com_laco(devs: Any, rt: Any) -> asyncio.Task[Any]:
    """O laço de captura de verdade. No harness, o aparelho adotado ao subir não o tem (só quem liga pelo boot)."""
    t = asyncio.create_task(devs._capture_loop(rt), name=f"capture-{rt.id}")
    rt.tasks["capture"] = t
    return t


def _caps(fake: Any) -> int:
    return sum(1 for c in fake.calls if c == "screenshot")


async def _preparar(h: Harness) -> tuple[Any, Relogio]:
    s = h.state
    assert s is not None
    devs = s.devices
    rel = Relogio()
    devs.relogio = rel
    for rt in devs.devices.values():
        await _sem_laco(rt)
    metricas.limpar()
    return devs, rel


async def test_sem_assinante_nenhuma_captura_de_previa_e_a_tela_fica_paused(harness: Harness) -> None:
    devs, _ = await _preparar(harness)
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    antes = _caps(fake)
    for _ in range(5):
        resultado, _espera = await devs._volta_da_previa(rt)
        assert resultado == "sem_interesse"
    assert _caps(fake) == antes, "sem ninguém olhando, o aparelho não é tocado"
    # Uma por volta em que o laço antigo teria capturado — nem mais (inflar), nem menos.
    assert metricas.valor("captura.evitada", motivo="sem_interesse") == 5
    dto = devs.dto(rt)
    assert dto.stream is not None and dto.stream.status == "paused", dto.stream


async def test_dois_paineis_no_mesmo_aparelho_sao_uma_captura_por_ciclo(harness: Harness) -> None:
    devs, _ = await _preparar(harness)
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    devs.registrar_interesse("aba-1", ["android-01", "android-02"], None, 20)
    devs.registrar_interesse("aba-2", ["android-01"], None, 20)
    antes = _caps(fake)
    resultado, _ = await devs._volta_da_previa(rt, pedido=True)
    assert resultado == "capturada" and _caps(fake) == antes + 1
    assert metricas.valor("captura.total", origem="previa", resultado="ok") == 1
    assert devs.dto(rt).stream.status == "live"


async def test_aba_oculta_ttl_vencido_e_desconexao_voltam_a_paused(harness: Harness) -> None:
    devs, rel = await _preparar(harness)
    rt = devs.get("android-01")
    devs.registrar_interesse("aba", ["android-01"], None, 20)
    assert devs.nivel_de_interesse(rt) == "grade"
    devs.registrar_interesse("aba", [], None, 20)            # aba oculta: watch vazio SUBSTITUI o anterior
    assert devs.nivel_de_interesse(rt) is None and devs._previa_pausada(rt)
    devs.registrar_interesse("aba", ["android-01"], None, 20)
    rel.t += 21                                               # o navegador atrasou a renovação da aba oculta
    assert devs.nivel_de_interesse(rt) is None, "conexão que já mandou watch nunca volta a ser 'cliente antigo'"
    devs.registrar_interesse("aba", ["android-01"], None, 20)
    assert devs.nivel_de_interesse(rt) == "grade"
    devs.soltar_interesse("aba")                              # desconectou
    assert devs.nivel_de_interesse(rt) is None


async def test_ttl_fica_entre_5_e_60_segundos_e_ids_desconhecidos_somem(harness: Harness) -> None:
    devs, rel = await _preparar(harness)
    rt = devs.get("android-01")
    devs.registrar_interesse("aba", ["android-01", "nao-existe", 7], "tambem-nao", 3600)
    assert set(devs._interesses["aba"].grade) == {"android-01"} and devs._interesses["aba"].foco is None
    rel.t += 61
    assert devs.nivel_de_interesse(rt) is None                # 3600 s virou 60 s
    devs.registrar_interesse("aba", ["android-01"], None, 1)
    rel.t += 4
    assert devs.nivel_de_interesse(rt) == "grade"             # 1 s virou 5 s
    devs.registrar_interesse("aba", "não é lista", None, "abc")
    assert devs.nivel_de_interesse(rt) is None


async def test_interesse_novo_acorda_a_captura_na_hora(harness: Harness) -> None:
    devs, _ = await _preparar(harness)
    rt = devs.get("android-01")
    rt.capture_now.clear()
    devs.registrar_interesse("aba", ["android-01"], None, 20)
    assert rt.capture_now.is_set(), "a prévia tem de voltar já, não na próxima volta do laço"
    rt.capture_now.clear()
    devs.registrar_interesse("aba", ["android-01"], None, 20)  # renovação: não acorda de novo
    assert not rt.capture_now.is_set()
    devs.registrar_interesse("aba", ["android-01"], "android-01", 20)  # subiu para foco: acorda
    assert rt.capture_now.is_set()


async def test_retomada_imediata_com_o_laco_de_verdade(harness: Harness) -> None:
    """Com o laço rodando (grade = 5 s), o interesse novo produz frame bem antes da volta seguinte."""
    s = harness.state
    assert s is not None
    devs = s.devices
    rt, fake = devs.get("android-02"), harness.fakes["android-02"]
    antes = _caps(fake)
    laco = _com_laco(devs, rt)
    await asyncio.sleep(0.3)                                  # 1ª volta sem ninguém olhando: entra na espera de 5 s
    assert _caps(fake) == antes, "sem interesse o laço não toca no aparelho"
    devs.registrar_interesse("aba", ["android-02"], None, 20)
    await harness.wait(lambda: _caps(fake) > antes, timeout=2.0, what="captura logo após o interesse")
    devs.soltar_interesse("aba")
    laco.cancel()


async def test_foco_do_watch_mantem_o_aparelho_acordado_e_a_grade_nao(harness: Harness) -> None:
    """Rodízio e hibernação leem `rt.focused` (scheduler): grade não pode mantê-lo verdadeiro."""
    devs, rel = await _preparar(harness)
    rt = devs.get("android-01")
    devs.registrar_interesse("aba", ["android-01"], None, 20)
    assert not rt.focused
    devs.registrar_interesse("aba", ["android-01"], "android-01", 20)
    assert rt.focused
    rel.t += 21
    assert not rt.focused
    devs.set_focus("android-01", ttl_s=15)                    # o `focus` antigo continua valendo
    assert rt.focused and devs.nivel_de_interesse(rt) == "foco"


async def test_foco_do_watch_renova_o_lease_manual_e_controle_conta_como_foco(harness: Harness) -> None:
    devs, _ = await _preparar(harness)
    rt = devs.get("android-01")
    status, lease = devs.request_control(rt)
    assert status == "granted" and rt.control == ControlOwner.user
    assert devs.nivel_de_interesse(rt) == "foco", "quem controla está vendo a tela"
    rt.lease_expires_mono = 0.0
    devs.registrar_interesse("aba", [], "android-01", 20)
    assert rt.lease_expires_mono > time.monotonic() + 500
    devs.release_control(rt, lease)


async def test_cliente_antigo_sem_watch_e_grade_em_todos(harness: Harness) -> None:
    devs, _ = await _preparar(harness)
    devs.interesse_legado("painel-antigo")
    for rt in devs.devices.values():
        assert devs.nivel_de_interesse(rt) == "grade"
    rt, fake = devs.get("android-03"), harness.fakes["android-03"]
    antes = _caps(fake)
    assert (await devs._volta_da_previa(rt, pedido=True))[0] == "capturada" and _caps(fake) == antes + 1
    devs.registrar_interesse("painel-antigo", ["android-01"], None, 20)  # mandou watch: deixa de ser legado
    assert devs.nivel_de_interesse(rt) is None


async def test_preview_mode_always_e_o_laco_antigo(harness: Harness) -> None:
    devs, _ = await _preparar(harness)
    harness.state.settings.update({"preview_mode": "always"})   # type: ignore[union-attr]
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    antes = _caps(fake)
    assert (await devs._volta_da_previa(rt))[0] == "capturada" and _caps(fake) == antes + 1
    assert devs.dto(rt).stream.status == "live"             # sem `paused` no modo antigo


async def test_frame_fresco_da_observacao_dispensa_a_captura_de_previa(harness: Harness) -> None:
    devs, _ = await _preparar(harness)
    rt, fake = devs.get("android-01"), harness.fakes["android-01"]
    devs.registrar_interesse("aba", ["android-01"], None, 20)
    await devs.observe(rt, timeout=5)                         # a IA acabou de observar e publicar
    antes = _caps(fake)
    assert (await devs._volta_da_previa(rt))[0] == "frame_recente" and _caps(fake) == antes
    # Pedido explícito (entrada manual, interesse novo) captura mesmo com frame fresco.
    assert (await devs._volta_da_previa(rt, pedido=True))[0] == "capturada" and _caps(fake) == antes + 1


async def test_transicao_paused_live_publica_instance_updated(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    devs = s.devices
    rt = devs.get("android-01")
    fila = s.bus.subscribe()
    laco = _com_laco(devs, rt)
    await asyncio.sleep(0.2)
    devs.registrar_interesse("aba", ["android-01"], None, 20)
    await harness.wait(lambda: rt.frame is not None and devs.dto(rt).stream.status == "live", timeout=3,
                       what="prévia ao vivo")

    def status_publicados() -> list[str]:
        out = []
        while not fila.empty():
            ev = fila.get_nowait()
            if ev.kind == "instance.updated" and ev.instance_id == "android-01":
                out.append(ev.data["instance"]["stream"]["status"])
        return out
    vistos: list[str] = []
    await harness.wait(lambda: bool(vistos.extend(status_publicados()) or "live" in vistos), timeout=3,
                       what="instance.updated com a tela ao vivo")
    devs.soltar_interesse("aba")
    rt.capture_now.set()                                      # não espera os 5 s da grade
    await harness.wait(lambda: bool(vistos.extend(status_publicados()) or vistos[-1:] == ["paused"]), timeout=3,
                       what="instance.updated com a prévia suspensa")
    s.bus.unsubscribe(fila)
    laco.cancel()


def test_websocket_registra_legado_watch_e_solta_ao_desconectar(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    devs = s.devices
    app = create_app(harness.cfg, state=s)
    app.state.poc = s
    cliente = TestClient(app, client=("127.0.0.1", 123))

    def ate_pong(ws: Any) -> None:
        ws.send_json({"type": "ping"})
        while ws.receive_json().get("type") != "pong":
            pass

    with cliente.websocket_connect("/api/ws", headers={"host": "127.0.0.1"}) as ws:
        assert ws.receive_json()["type"] == "hello"
        ate_pong(ws)
        assert len(devs._interesses) == 1
        (conexao, interesse), = devs._interesses.items()
        assert interesse.grade is None                        # nunca mandou watch: painel antigo
        ws.send_json({"type": "watch", "grid": ["android-02"], "focus": "android-01", "ttl_s": 20})
        ate_pong(ws)
        i = devs._interesses[conexao]
        assert i.grade == frozenset({"android-02"}) and i.foco == "android-01"
        assert devs.nivel_de_interesse(devs.get("android-03")) is None
    assert devs._interesses == {}, "desconectar apaga o interesse da conexão"


def test_ordem_das_perguntas_paused_depois_de_offline_e_worker_e_sem_esconder_falha() -> None:
    from app.devices.stream import stream_status

    base = dict(device_state="online", worker_bound=False, worker_connected=True, frame_ts=None, frame_age_s=None,
                max_age_s=6.0, capture_failures=0, last_error=None, last_error_at=None, paused=True)
    assert stream_status(**base).status == "paused"
    assert stream_status(**{**base, "frame_age_s": 300.0, "frame_ts": "x"}).status == "paused"   # não `stale`
    assert stream_status(**{**base, "device_state": "hibernated"}).status == "device_hibernated"
    assert stream_status(**{**base, "worker_bound": True, "worker_connected": False}).status == "worker_offline"
    # Falha registrada é fato sobre o aparelho: `paused` não a esconde.
    assert stream_status(**{**base, "capture_failures": 2, "last_error": "DriverTimeout"}).status == "capture_error"
