"""29.152: o primeiro aviso de cada episódio de pressão de CPU diz QUEM pesa no convidado (3 processos e o pacote em
primeiro plano), só com nomes. Medida do android-02 em 06/10/2026: 375 avisos, nenhum nomeava processo ou app."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.devices.adb import ler_culpados
from app.models import InstanceState

from .conftest import Harness

_CPUINFO = """Load: 0.23 / 1.75 / 1.67
CPU usage from 418380ms to 118303ms ago (2026-10-06 04:43:32.583 to 2026-10-06 04:48:34.506):
  4.9% 768/com.android.systemui: 2.1% user + 2.7% kernel / faults: 40099 minor
  2.5% 397/surfaceflinger: 0.2% user + 2.3% kernel / faults: 16 minor
  2.2% 541/system_server: 1% user + 1.1% kernel / faults: 5802 minor
  1.2% 384/android.hardware.graphics.composer3-service.ranchu: 0% user + 1.2% kernel
---FG---
  topResumedActivity=ActivityRecord{34cf9e1 u0 com.instagram.android/.activity.MainTabActivity t34}
"""


def test_a_leitura_traz_os_tres_maiores_e_o_pacote_em_primeiro_plano() -> None:
    c = ler_culpados(_CPUINFO)
    assert c["processos"] == [{"nome": "com.android.systemui", "cpu_pct": 4.9},
                              {"nome": "surfaceflinger", "cpu_pct": 2.5},
                              {"nome": "system_server", "cpu_pct": 2.2}]
    assert c["primeiro_plano"] == "com.instagram.android"      # o pacote, não a atividade


def test_so_nomes_passam_nunca_titulo_de_janela_nem_conteudo() -> None:
    """Uma linha que não tem o formato de processo, ou nome com espaço e acento, é descartada em vez de copiada."""
    sujo = ("  9.0% 11/Mensagem de Maria: 5% user\n"
            "  8.0% 12/com.app.ok: 4% user\n"
            "  7.0% sem formato nenhum\n"
            "  6.0% 13/senha=abc123 : 1% user\n"
            "---FG---\n  mResumedActivity: ActivityRecord{1 u0 Título da tela/x t1}\n")
    c = ler_culpados(sujo)
    assert c["processos"] == [{"nome": "com.app.ok", "cpu_pct": 8.0}]
    assert c["primeiro_plano"] is None
    assert "Maria" not in json.dumps(c) and "senha" not in json.dumps(c)


def test_sem_nada_a_leitura_vem_vazia() -> None:
    assert ler_culpados("") == {"processos": [], "primeiro_plano": None}


def _avisos(h: Harness, instancia: str) -> list[dict[str, object]]:
    linhas = h.state.db.query("SELECT message, data FROM events WHERE instance_id=? AND level='warn' "
                              "AND message LIKE '%sob pressão de CPU%' ORDER BY id", (instancia,))
    return [{"message": r["message"], "data": json.loads(r["data"] or "{}")} for r in linhas]


@pytest.mark.asyncio
async def test_so_o_primeiro_aviso_do_episodio_leva_os_culpados_e_o_proximo_episodio_leva_de_novo(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        d = h.state.devices
        rt = d.get("android-01")
        rt.state, rt.attention = InstanceState.online, None
        rt.io.culpados = {"processos": [{"nome": "com.microsoft.office.outlook", "cpu_pct": 41.0},
                                        {"nome": "io.nekohasekai.sfa", "cpu_pct": 12.5},
                                        {"nome": "system_server", "cpu_pct": 3.0}],
                          "primeiro_plano": "com.microsoft.office.outlook"}
        rt.io.pressure = {"load1": 12.0, "mem_total_mb": 2979.0, "mem_available_mb": 1700.0, "ncpu": 2.0}
        await d.conferir_saude(rt)
        assert rt.attention is None                                     # uma sonda só não basta
        await d.conferir_saude(rt)                                      # 2ª sonda: o aviso do episódio
        rt.io.pressure = {"load1": 13.5, "mem_total_mb": 2979.0, "mem_available_mb": 1700.0, "ncpu": 2.0}
        await d.conferir_saude(rt)                                      # mesmo episódio, número novo
        avisos = _avisos(h, "android-01")
        assert len(avisos) == 2
        assert avisos[0]["data"]["pressao"] == rt.io.culpados          # type: ignore[index]
        assert "pressao" not in avisos[1]["data"]                      # type: ignore[operator]
        assert rt.io.leituras_de_culpados == 1                         # uma leitura por episódio, não por aviso

        rt.io.pressure = None                                           # o episódio acaba
        await d.conferir_saude(rt)
        assert rt.attention is None
        rt.io.pressure = {"load1": 14.0, "mem_total_mb": 2979.0, "mem_available_mb": 1700.0, "ncpu": 2.0}
        await d.conferir_saude(rt)
        await d.conferir_saude(rt)                                      # episódio novo
        avisos = _avisos(h, "android-01")
        assert len(avisos) == 3 and "pressao" in avisos[2]["data"]      # type: ignore[operator]
        assert rt.io.leituras_de_culpados == 2
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_sem_a_leitura_o_aviso_sai_igual_e_nada_quebra(tmp_path: Path) -> None:
    """O adb que cala na hora da leitura dos culpados não derruba o aviso: ele sai sem o campo."""
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        d = h.state.devices
        rt = d.get("android-01")
        rt.state, rt.attention = InstanceState.online, None
        rt.io.pressure = {"load1": 12.0, "mem_total_mb": 2979.0, "mem_available_mb": 1700.0, "ncpu": 2.0}
        rt.io.culpados_mudo = True                                      # só o dumpsys cala; a leitura de /proc segue
        await d.conferir_saude(rt)
        await d.conferir_saude(rt)
        avisos = _avisos(h, "android-01")
        assert len(avisos) == 1 and "pressao" not in avisos[0]["data"]  # type: ignore[operator]
        assert rt.attention and rt.attention.startswith("Convidado sob pressão")
    finally:
        await h.state.stop()
