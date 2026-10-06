"""31.91 T1 (ADR-078): as rotas do ensino v2 estão marcadas como obsoletas e contadas, sem mudar o que respondem.

Nível de prova: `simulated` (harness, aparelho falso). A régua do T2 (14 dias com zero chamadas) só vale se o contador
sobreviver a reinício, contar só essas rotas e nunca derrubar a chamada que conta.
"""
from __future__ import annotations

import pytest

from app.main import create_app
from app.modules.skills.infrastructure.contador_do_ensino_v2 import ContadorDoEnsinoV2

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente

ROTAS_V2 = {("post", "/api/teaching-sessions"), ("get", "/api/teaching-sessions"),
            ("get", "/api/teaching-sessions/{teaching_id}"),
            ("post", "/api/teaching-sessions/{teaching_id}/demonstrations"),
            ("post", "/api/teaching-sessions/{teaching_id}/corrections"),
            ("post", "/api/teaching-sessions/{teaching_id}/candidates"),
            ("post", "/api/teaching-sessions/{teaching_id}/answers"),
            ("post", "/api/teaching-sessions/{teaching_id}/discard"),
            ("get", "/api/skill-candidates/{candidate_id}"), ("post", "/api/skill-candidates/{candidate_id}/compile"),
            ("post", "/api/skill-candidates/{candidate_id}/validate"),
            ("post", "/api/skill-candidates/{candidate_id}/publish")}


async def _chamadas(c) -> dict:
    return (await c.get("/api/health")).json()["features"]["ensino_v2_chamadas"]


async def test_so_as_doze_rotas_do_ensino_v2_saem_marcadas_como_obsoletas(harness: Harness) -> None:
    paths = create_app(harness.cfg, state=harness.state).openapi()["paths"]
    obsoletas = {(m, p) for p, ops in paths.items() for m, op in ops.items() if op.get("deprecated")}
    assert obsoletas == ROTAS_V2
    assert not paths["/api/skills"]["get"].get("deprecated")                 # a lista de habilidades segue viva


async def test_conta_so_as_rotas_do_ensino_v2_e_mostra_na_saude(harness: Harness) -> None:
    harness.state.cfg.file.skills.enabled = True
    async with _cliente(harness) as c:
        assert (await _chamadas(c))["total"] == 0 and (await _chamadas(c))["desde"] is None
        assert (await c.get("/api/skills")).status_code == 200                # fora do ensino v2: não conta
        assert (await c.get("/api/training")).status_code == 200
        assert (await _chamadas(c))["total"] == 0
        assert (await c.get("/api/teaching-sessions")).status_code == 200
        assert (await c.get("/api/teaching-sessions/ens-que-nao-existe")).status_code == 404   # a chamada conta, o erro não
        r = await _chamadas(c)
        assert r["total"] == 2 and r["desde"] and r["ultima"]
        assert r["por_rota"] == {"GET /api/teaching-sessions": 1, "GET /api/teaching-sessions/{teaching_id}": 1}
        assert "ens-que-nao-existe" not in str(r)                             # molde da rota, nunca o id


async def test_com_as_habilidades_desligadas_a_chamada_nao_conta(harness: Harness) -> None:
    assert harness.state.cfg.file.skills.enabled is False
    async with _cliente(harness) as c:
        assert (await c.get("/api/teaching-sessions")).status_code == 404
        assert (await _chamadas(c))["total"] == 0


async def test_o_contador_sobrevive_a_reinicio_e_acumula(harness: Harness) -> None:
    db = harness.state.db
    ContadorDoEnsinoV2(db).registrar("GET", "/api/teaching-sessions")
    primeira = ContadorDoEnsinoV2(db).resumo()
    ContadorDoEnsinoV2(db).registrar("GET", "/api/teaching-sessions")        # outra instância = outro processo
    depois = ContadorDoEnsinoV2(db).resumo()
    assert depois["total"] == 2 and depois["desde"] == primeira["desde"]
    assert depois["por_rota"] == {"GET /api/teaching-sessions": 2}


async def test_falha_do_contador_nao_derruba_a_rota_nem_a_saude(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    harness.state.cfg.file.skills.enabled = True

    def quebrado(self):
        raise RuntimeError("banco indisponível")

    monkeypatch.setattr(ContadorDoEnsinoV2, "_ler", quebrado)
    async with _cliente(harness) as c:
        assert (await c.get("/api/teaching-sessions")).status_code == 200
        r = await _chamadas(c)
    assert r == {"total": 0, "desde": None, "ultima": None, "por_rota": {}}
