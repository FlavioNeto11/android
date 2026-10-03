"""I2 da validação do deploy 7 (adendo v0.87): o `GET /api/ai` diz o esquema do plano e os perfis de IA.

A tela Configuração › IA não mostrava o que o 17.13 e o 17.14 mudaram: o `esquema_do_plano` (LT-4b) só existia no YAML e
o perfil `planejador-sonnet` só aparecia quando uma execução o usava. Agora o status traz os dois, e cada perfil diz só as
funções que muda, a fatia do canário e os ajustes de imagem e árvore.

Prova `simulated`: a configuração de teste e o `GET /api/ai` do harness, sem chamada de modelo.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from app.config import AiCanaryCfg, AiProfileCfg
from app.main import create_app
from app.planning.routing import RoutingProvider, perfis_para_o_painel

from .conftest import Harness
from .test_hub_de_ia import com_hub

PROVEDORES = {"anthropic": {"kind": "anthropic"}}


def _cfg(tmp: Path, perfis: dict[str, Any], roles: dict[str, Any] | None = None) -> Any:
    cfg = com_hub(tmp, providers=PROVEDORES, roles=roles or {})
    cfg.file.ai.profiles = {nome: AiProfileCfg.model_validate(p) for nome, p in perfis.items()}
    return cfg


def test_sem_perfis_a_lista_vem_vazia(tmp_path: Path) -> None:
    assert perfis_para_o_painel(_cfg(tmp_path, {})) == []


def test_o_perfil_diz_so_as_funcoes_que_muda(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, {
        "planejador-sonnet": {"note": "plano no Sonnet", "roles": {"plan": {"model": "claude-sonnet-5-5"}}},
        "img-768": {"screenshot_max_side": 768, "rich_tree_min_elements": 12},
    })
    cfg.file.ai.canary = AiCanaryCfg(profile="planejador-sonnet", fraction=0.25)
    perfis = {p.name: p for p in perfis_para_o_painel(cfg)}
    sonnet = perfis["planejador-sonnet"]
    assert sonnet.note == "plano no Sonnet" and sonnet.canary_fraction == 0.25
    assert [(f.role, f.model, f.provider) for f in sonnet.roles] == [("plan", "claude-sonnet-5-5", "anthropic")]
    # o mesmo que o roteador usa nas execuções do perfil
    assert sonnet.roles[0].effort == RoutingProvider(cfg).roles_por_perfil["planejador-sonnet"]["plan"].effort
    dieta = perfis["img-768"]
    assert dieta.roles == [] and dieta.canary_fraction is None
    assert (dieta.screenshot_max_side, dieta.rich_tree_min_elements) == (768, 12)


def test_o_perfil_que_escreve_o_social_muda_a_persona_que_o_herda(tmp_path: Path) -> None:
    [perfil] = perfis_para_o_painel(_cfg(tmp_path, {"social-haiku": {"roles": {"social": {"model": "claude-haiku-4-5"}}}}))
    assert {f.role: f.model for f in perfil.roles} == {"social": "claude-haiku-4-5", "persona": "claude-haiku-4-5"}


def test_com_bloco_proprio_a_persona_nao_muda_junto(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, {"social-haiku": {"roles": {"social": {"model": "claude-haiku-4-5"}}}},
               roles={"persona": {"model": "claude-opus-5-5"}})
    [perfil] = perfis_para_o_painel(cfg)
    assert [f.role for f in perfil.roles] == ["social"]


async def test_o_get_api_ai_traz_o_esquema_e_os_perfis(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    st.cfg.file.ai.esquema_do_plano = "curto"
    st.cfg.file.ai.profiles = {"img-768": AiProfileCfg(screenshot_max_side=768, note="dieta de imagem")}
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        corpo = (await c.get("/api/ai")).json()
    assert corpo["esquema_do_plano"] == "curto"
    assert corpo["leitura_visual"] is False                                  # desligada por padrão
    assert corpo["profiles"] == [{"name": "img-768", "note": "dieta de imagem", "roles": [], "canary_fraction": None,
                                  "screenshot_max_side": 768, "rich_tree_min_elements": None}]
