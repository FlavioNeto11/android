"""29.79, achado no percurso do painel (05/10): `Approval.to_dict` não levava o `rotulo_ia`, e `GET /approvals` o
perdia. O selo "com/sem rótulo de IA" da aba Textos e da guia Aprovações nunca aparecia — o teste do painel simulava o
campo. O dono aprova a primeira publicação real justamente nessas telas: a rota tem de entregar o rótulo."""
from __future__ import annotations

from pathlib import Path

import httpx

from app.main import create_app

from .conftest import make_config
from .test_porta_do_plano import _plano


async def test_a_rota_das_aprovacoes_entrega_o_rotulo_ia(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    app = create_app(cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            state = app.state.poc
            # Como a central grava ao materializar (`_com_rotulo_ia`): "true" na gerada, "false" no upload; a etapa
            # sem imagem fica sem o argumento.
            pid = _plano(state, [
                {"key": "gerada", "cap": "CREATE_POST",
                 "bindings": {"image_id": "img-g", "content": "praia", "content_verbatim": "true", "rotulo_ia": "true"}},
                {"key": "enviada", "cap": "CREATE_POST",
                 "bindings": {"image_id": "img-u", "content": "praia", "content_verbatim": "true", "rotulo_ia": "false"}},
                {"key": "dm", "cap": "SEND_MESSAGE", "bindings": {"username": "@ana", "content": "oi"}}])
            for chave in ("gerada", "enviada", "dm"):
                state.approvals.open(profile_id=pid, capability="CREATE_POST", summary=chave, run_id="run-p",
                                     objective_id="run-p:android-01", step_id=f"run-p:android-01:v1:{chave}",
                                     content="praia")
            lista = (await c.get("/api/approvals", params={"profile_id": pid})).json()
            assert all("rotulo_ia" in a for a in lista)
            assert {a["summary"]: a["rotulo_ia"] for a in lista} == {"gerada": True, "enviada": False, "dm": None}
