"""Item 14.13 (RA-11, parte Android): o DTO do aparelho vai ao log só quando conta um fato.

Medido no central em 04/10 (24 h, só leitura): 7383 `instance.updated`; ~2550 eram trocas de controle `ai↔none`, que o
`control.changed` já gravava (a troca ia ao log duas vezes), e 391 mudavam só telemetria. Agora o que não é fato sai
como `instance.progress`, efêmero: o painel recebe o mesmo DTO e o log não cresce.

Nível de prova: `simulated` (harness na porta 5640, aparelho falso). As chamadas a `publish` são síncronas e seguidas,
sem `await` entre elas: nenhum laço de fundo publica no meio.
"""
from __future__ import annotations

from typing import Any

from app.devices.publicacao import assinatura_material
from app.events import EPHEMERAL_KINDS
from app.models import ControlOwner, InstanceResources

from .conftest import Harness


def _ultimo_id(state: Any) -> int:
    return int(state.db.query("SELECT COALESCE(MAX(id), 0) AS m FROM events")[0]["m"])


def _gravados_desde(state: Any, desde: int, instance_id: str) -> list[str]:
    return [r["kind"] for r in state.db.query("SELECT kind FROM events WHERE id > ? AND instance_id = ? ORDER BY id",
                                              (desde, instance_id))]


def _emitidos(fila: Any) -> list[str]:
    kinds = []
    while not fila.empty():
        kinds.append(fila.get_nowait().kind)
    return kinds


def test_assinatura_ignora_telemetria_e_controle_e_ve_o_fato() -> None:
    base: dict[str, object] = {
        "id": "android-01", "state": "online", "state_detail": None, "control": "none", "control_since": None,
        "control_pending": False, "resources": {"rss_mb": 900.0, "cpu_percent": 12.0}, "frame": {"id": 1, "ts": "a"},
        "connectivity": {"state": "ok", "checked_at": "t1"}, "stream": {"status": "live", "frame_age_s": 0.5,
                                                                        "last_frame_at": "t1"},
        "repair_pause": {"remaining_s": 900, "reason": "deploy"},
    }
    so_telemetria = {**base, "resources": {"rss_mb": 950.0, "cpu_percent": 80.0}, "frame": {"id": 2, "ts": "b"},
                     "connectivity": {"state": "ok", "checked_at": "t2"},
                     "stream": {"status": "live", "frame_age_s": 3.0, "last_frame_at": "t2"},
                     "repair_pause": {"remaining_s": 880, "reason": "deploy"},
                     "control": "ai", "control_since": "agora", "control_pending": True}
    assert assinatura_material(so_telemetria) == assinatura_material(base)
    assert assinatura_material({**base, "state_detail": "reiniciando"}) != assinatura_material(base)
    assert assinatura_material({**base, "stream": {"status": "stale", "frame_age_s": 0.5,
                                                   "last_frame_at": "t1"}}) != assinatura_material(base)
    assert assinatura_material({**base, "connectivity": {"state": "sem_rede", "checked_at": "t1"}}) \
        != assinatura_material(base)
    assert "instance.progress" in EPHEMERAL_KINDS and "instance.updated" not in EPHEMERAL_KINDS


async def test_dto_repetido_ou_so_telemetria_nao_vai_ao_log(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    devs = s.devices
    rt = devs.get("android-01")
    rt.dto_publicado = rt.controle_anunciado = None                 # como num processo novo: nada publicado ainda
    desde = _ultimo_id(s)
    fila = s.bus.subscribe()
    try:
        devs.publish(rt)                                              # 1: primeira vez conta (a referência)
        devs.publish(rt)                                              # 2: nada mudou
        rt.resources = InstanceResources(rss_mb=1234.0, cpu_percent=99.0)
        devs.publish(rt)                                              # 3: só telemetria
        rt.state_detail = "detalhe novo do 14.13"
        devs.publish(rt)                                              # 4: fato
        devs.publish(rt, level="warn")                                # 5: aviso conta sempre
        devs.publish(rt, f"{rt.id}: inventário conferido (14.13)")    # 6: mensagem própria é registro
        emitidos = _emitidos(fila)
    finally:
        s.bus.unsubscribe(fila)
    assert _gravados_desde(s, desde, rt.id) == ["instance.updated"] * 4          # 1, 4, 5 e 6
    # O painel recebeu todas: as que não foram ao log chegaram como `instance.progress`, com o DTO.
    assert emitidos.count("instance.progress") == 2 and emitidos.count("instance.updated") == 4


async def test_troca_de_controle_grava_uma_vez_so(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    devs = s.devices
    rt = devs.get("android-01")
    devs.publish(rt)
    desde = _ultimo_id(s)
    assert devs.ai_begin(rt)
    devs.ai_end(rt)
    # Antes: control.changed + instance.updated (DTO inteiro) a cada troca. Agora o fato fica só no control.changed.
    assert _gravados_desde(s, desde, rt.id) == ["control.changed", "control.changed"]


async def test_controle_que_mudou_sem_anuncio_vai_ao_log(harness: Harness) -> None:
    """Rede de segurança: troca de controle que não passou por `control.changed` não pode sumir do log."""
    s = harness.state
    assert s is not None
    devs = s.devices
    rt = devs.get("android-01")
    devs.publish(rt)
    desde = _ultimo_id(s)
    rt.control = ControlOwner.ai                                      # sem `_control_event`
    devs.publish(rt)
    rt.control = ControlOwner.none
    assert _gravados_desde(s, desde, rt.id) == ["instance.updated"]
