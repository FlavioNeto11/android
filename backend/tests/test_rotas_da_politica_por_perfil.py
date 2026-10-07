"""Rotas da política por perfil e dos grupos de acesso, depois do ADR-083 (31.272).

O refactor do dono de 07/10 tirou tetos, aquecimento e coordenação de frota e renomeou para `_skip_test_*.py` os
arquivos que cobriam essas regras. As rotas abaixo continuam no ar (a política por perfil e por grupo ficou), mas a
única chamada HTTP a elas estava nesses arquivos, e a catraca da cobertura de rotas (achado #166) reprovou. Aqui elas
voltam a ter chamada de verdade, já sem os limites: `limits` vem vazio e qualquer limite pedido é desconhecido.

Nível de prova: `simulated` (app de teste por ASGI, banco de teste, perfis de teste). Nada real.
"""
from __future__ import annotations

from pathlib import Path

import httpx

from app.main import create_app
from app.models import ProfileCreate

from .apoio_politica import IG, SENHA
from .conftest import Harness, make_config
from .test_perfil_bloqueado_e_capacidades import _cliente
from .test_sessao_por_conta import estado


async def test_rotas_da_politica_do_perfil_sem_limites(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    app = create_app(cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            pid = (await c.post("/api/instagram/profiles",
                                json={"username": "luciana.bastos73519", "password": SENHA})).json()["id"]

            politica = (await c.get(f"/api/instagram/profiles/{pid}/policy")).json()
            assert politica["package"] == IG
            assert politica["capabilities"]["LIKE_POST"] == "autonomous"
            assert politica["limits"] == {}                                    # ADR-083: sem teto por hora/dia
            explicita = (await c.get(f"/api/instagram/profiles/{pid}/policy", params={"package": IG})).json()
            assert explicita["capabilities"] == politica["capabilities"]

            mudado = await c.put(f"/api/instagram/profiles/{pid}/policy",
                                 json={"capabilities": {"LIKE_POST": "approval_required"}})
            assert mudado.status_code == 200, mudado.text
            assert mudado.json()["capabilities"]["LIKE_POST"] == "approval_required"
            assert mudado.json()["defaults"]["LIKE_POST"] == "autonomous"      # o padrão continua visível ao lado

            ruim = await c.put(f"/api/instagram/profiles/{pid}/policy", json={"capabilities": {"DANCAR": "autonomous"}})
            assert ruim.status_code == 400 and ruim.json()["detail"]["code"] == "unknown_capability"
            sem_teto = await c.put(f"/api/instagram/profiles/{pid}/policy", json={"limits": {"likes_per_hour": 5}})
            assert sem_teto.status_code == 400 and sem_teto.json()["detail"]["code"] == "unknown_limit"

            assert (await c.get(f"/api/instagram/profiles/{pid}/runs")).json() == []
            assert (await c.get("/api/instagram/profiles/ig-nao-existe/policy")).status_code == 404


async def test_rotas_http_dos_grupos_sem_limites(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        pid = h.state.social.create_profile(ProfileCreate(username="rota.teste", instance_id="android-01")).id
        async with _cliente(h) as c:
            r = await c.post("/api/instagram/policy-groups",
                             json={"name": "Revisados", "profile_ids": [pid],
                                   "capabilities": {"LIKE_POST": "approval_required"}})
            assert r.status_code == 201, r.text
            gid = r.json()["id"]
            assert r.json()["package"] == IG
            explicito = await c.get(f"/api/instagram/policy-groups/{gid}", params={"package": IG})
            assert explicito.json()["package"] == IG
            assert (await c.get("/api/instagram/policy-groups")).json()[0]["members"][0]["id"] == pid
            pol = (await c.get(f"/api/instagram/profiles/{pid}/policy")).json()
            assert pol["capabilities"]["LIKE_POST"] == "approval_required"     # veio do grupo
            assert pol["limits"] == {}
            r = await c.put(f"/api/instagram/profiles/{pid}/policy", json={"capabilities": {"LIKE_POST": None}})
            assert r.status_code == 200, r.text
            assert (await c.put(f"/api/instagram/policy-groups/{gid}", json={"name": "Liberados"})).json()["name"] == "Liberados"
            assert (await c.get(f"/api/instagram/profiles/{pid}")).json()["policy_group_name"] == "Liberados"
            assert (await c.delete(f"/api/instagram/policy-groups/{gid}")).status_code == 204
            assert (await c.get(f"/api/instagram/policy-groups/{gid}")).status_code == 404
            assert (await c.get("/api/instagram/policy-defaults")).json()["limits"] == {}
    finally:
        await h.state.stop()


async def test_rota_retire_e_idempotente_e_a_ancora_sai(harness: Harness) -> None:
    """Conta travada sai da plataforma: proteção de conta que não é limite de taxa, e o ADR-083 não mexe nela."""
    s = estado(harness)
    pid = s.social.create_profile(ProfileCreate(username="rota.teste04", password="Senha#Falsa1")).id
    conta = str(s.social_repo.conta_ancora(pid)["id"])
    app = create_app(harness.cfg, state=s)
    app.state.poc = s
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        url = f"/api/instagram/profiles/{pid}/accounts/{conta}"
        assert (await c.delete(url)).status_code == 409                       # a remoção comum recusa a âncora
        r = await c.post(f"{url}/retire", json={"evidencia": "vi a tela de verificação"})
        assert r.status_code == 200 and r.json()["retirada"] is True and r.json()["status_da_persona"] == "active"
        r2 = await c.post(f"{url}/retire")                                    # sem corpo; segunda vez
        assert r2.status_code == 200 and r2.json()["retirada"] is False
        assert (await c.post(f"/api/instagram/profiles/ig-nao-existe/accounts/{conta}/retire")).status_code == 404
        assert pid not in [p["id"] for p in (await c.get("/api/instagram/profiles")).json()]
