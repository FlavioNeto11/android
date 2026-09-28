"""Provisionamento de aparelho pela plataforma, no servidor local (segunda evolução, onda D; migração 050).

O que estes testes travam:

- o teto de APARELHOS por servidor (`worker_limits.max_devices`) entra e sai pela tela Limites como os outros, sem
  ir para o agente (o esquema do fio está congelado, ADR-031).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from app.main import create_app
from app.workers.protocol import Limits

from .conftest import Harness
from .test_rotation_worker import WORKER, _com_worker


async def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------- limites: max_devices
async def test_teto_de_aparelhos_entra_e_sai_pela_tela_limites_sem_ir_para_o_agente(tmp_path: Path) -> None:
    h, reg, agente = await _com_worker(tmp_path, remotos=["android-03"], count=3)
    try:
        assert h.state is not None
        host = h.cfg.owner_id
        async with await _cliente(h) as c:
            lista = (await c.get("/api/servers/limits")).json()
            for linha in lista:
                # Ninguém declara teto de aparelhos; sem decisão, não há teto.
                assert linha["declared"]["max_devices"] is None and linha["effective"]["max_devices"] is None

            r = await c.put(f"/api/servers/{host}/limits", json={"max_devices": 12})
            assert r.status_code == 200, r.text
            assert r.json()["decided"]["max_devices"] == 12 and r.json()["effective"]["max_devices"] == 12
            assert reg.limites_definidos(host) == {"max_devices": 12}

            antes = len(agente.enviados)
            r = await c.put(f"/api/servers/{WORKER}/limits", json={"max_devices": 4, "max_working": 2})
            assert r.status_code == 200, r.text
            assert r.json()["effective"]["max_devices"] == 4
            # A mensagem `limits` que vai para o agente não ganhou campo: ele não cria aparelho.
            assert reg.mensagem_de_limites(WORKER) == Limits(max_slots=None, boot_parallelism=None,
                                                             min_free_ram_mb=None)
            for m in agente.enviados[antes:]:
                assert "max_devices" not in m

            r = await c.put(f"/api/servers/{WORKER}/limits", json={"max_devices": None})
            assert r.json()["effective"]["max_devices"] is None and r.json()["effective"]["max_working"] == 2
            assert (await c.put(f"/api/servers/{host}/limits", json={"max_devices": 0})).status_code == 422
    finally:
        await h.crash()
