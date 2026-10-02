"""Papel de IA `persona` (item 17.8): a geração de persona pode ir a outro provedor sem arrastar o `social` da execução.

O que cada bloco prova, e por que ele existe (prova `simulated`: provedores falsos, nada de rede):

- **Sem `ai.roles.persona`, nada muda.** A persona herda o `social` por inteiro (provedor, modelo, prazo, vagas,
  fallback, esforço) e divide as vagas dele: nenhuma instalação muda de comportamento sem mexer na configuração.
- **Com bloco próprio, ela é outra função.** Vai ao provedor dela; o `social` (comentário, DM) continua onde estava,
  e o bloco do `social` deixa de valer para a persona (herdá-lo faria o modelo de um provedor ir para outro).
- **Perfis do 17.7.** Um perfil que só mexe no `social` mexe na persona herdada; um que escreve `persona` separa.
- **A contabilidade registra o papel.** `generate_persona` grava `role="persona"` e a social, `role="social"`.
"""
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.config import AI_ROLES, AiProfileCfg, AppConfigFile
from app.modules.identity.domain.persona_generation import PersonaGenerationRequest
from app.planning.openai_provider import OpenAICompatProvider
from app.planning.provider import Usage
from app.planning.routing import RoutingProvider, _com_provedor
from app.planning.simulated_provider import persona_simulada

from .conftest import make_config
from .test_hub_de_ia import FakeProvider, com_hub

HOJE = date(2026, 10, 2)
FLEX = {"kind": "openai", "base_url": "http://flex.exemplo/v1", "api_key_env": "OPENAI_API_KEY_FLEX"}
PROVEDORES = {"anthropic": {"kind": "anthropic"}, "openai-flex": FLEX}
MODELOS = {"modelo-flex": {"vision": False, "tools": False, "structured_output": "json_object",
                           "thinking": False, "effort": False}}


class PersonaFake(FakeProvider):
    """O `FakeProvider` do hub não gera persona: aqui ela devolve um rascunho de verdade e anota o papel."""

    async def generate_persona(self, req: Any) -> Any:
        self.calls.append("persona")
        return persona_simulada(req), Usage(calls=1, role="persona", model=self.model, provider=self.name)


def _instala(r: RoutingProvider, papel: str, fake: PersonaFake, perfil: str | None = None) -> None:
    rr = r.roles_por_perfil[perfil][papel] if perfil else r.roles[papel]
    r._por_chave[(rr.provider, rr.kind, rr.model, rr.timeout_s,                   # noqa: SLF001
                  rr.max_retries, rr.refusal_fallback)] = fake


def _pedido() -> PersonaGenerationRequest:
    return PersonaGenerationRequest(prompt="uma barista", locale="pt-BR", today=HOJE)


def _com_perfis(cfg: Any, perfis: dict[str, Any]) -> Any:
    cfg.file.ai.profiles = {nome: AiProfileCfg.model_validate(p) for nome, p in perfis.items()}
    return cfg


# ====================================================================== configuração: herança do social
def test_persona_e_um_papel_e_aparece_na_aba_ia(tmp_path: Path) -> None:
    assert "persona" in AI_ROLES and AI_ROLES[-1] == "persona"
    r = RoutingProvider(com_hub(tmp_path, roles={}, providers=PROVEDORES))
    assert [linha.role for linha in r.status().roles] == list(AI_ROLES)
    assert r.status().models["persona"] == r.status().models["social"]


def test_sem_config_a_persona_herda_exatamente_o_social(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.env.ai_provider = "anthropic"
    papeis = cfg.ai_roles()
    assert papeis["persona"].role == "persona"
    # `ResolvedRole` usa slots: compara campo a campo, tudo menos o nome da função.
    campos = [c for c in papeis["social"].__slots__ if c != "role"]
    assert all(getattr(papeis["persona"], c) == getattr(papeis["social"], c) for c in campos)


def test_persona_herda_o_social_configurado_no_yaml(tmp_path: Path) -> None:
    """O social apontado para outro provedor/modelo/prazo leva a persona junto — é o que acontece hoje."""
    cfg = com_hub(tmp_path, providers=PROVEDORES, models=MODELOS,
                  roles={"social": {"provider": "openai-flex", "model": "modelo-flex", "timeout_s": 77,
                                    "concurrency": 2, "fallback_provider": "anthropic"}})
    p, s = cfg.ai_role("persona"), cfg.ai_role("social")
    assert (p.provider, p.model, p.timeout_s, p.concurrency, p.fallback_provider) == (
        "openai-flex", "modelo-flex", 77.0, 2, "anthropic") == (
        s.provider, s.model, s.timeout_s, s.concurrency, s.fallback_provider)


def test_persona_configurada_e_outra_funcao_e_o_social_nao_se_mexe(tmp_path: Path) -> None:
    cfg = com_hub(tmp_path, providers=PROVEDORES, models=MODELOS,
                  roles={"social": {"timeout_s": 45, "fallback_provider": "anthropic"},
                         "persona": {"provider": "openai-flex", "model": "modelo-flex"}})
    p, s = cfg.ai_role("persona"), cfg.ai_role("social")
    assert (p.provider, p.kind, p.model, p.endpoint) == ("openai-flex", "openai", "modelo-flex", "flex.exemplo")
    assert s.provider == "anthropic" and s.timeout_s == 45.0
    # O bloco do social NÃO vaza para a persona com bloco próprio: nem prazo, nem fallback.
    assert p.timeout_s == 60.0 and p.fallback_provider is None


def test_persona_ajusta_um_campo_e_o_resto_vem_dos_padroes(tmp_path: Path) -> None:
    cfg = com_hub(tmp_path, providers=PROVEDORES, roles={"persona": {"timeout_s": 90}})
    p, s = cfg.ai_role("persona"), cfg.ai_role("social")
    assert p.timeout_s == 90.0 and s.timeout_s == 60.0
    assert (p.provider, p.model, p.concurrency) == (s.provider, s.model, s.concurrency)


@pytest.mark.parametrize(("ai", "erro"), [
    ({"roles": {"persona": {"provider": "sumido"}}}, r"ai.roles.persona.provider: provedor 'sumido' não está em ai.providers"),
    ({"providers": {"flex": {"kind": "openai", "base_url": "http://x/v1"}},
      "roles": {"persona": {"provider": "flex", "model": "inexistente"}}},
     r"ai.roles.persona.model: 'inexistente' não está declarado em ai.models"),
    ({"profiles": {"p": {"roles": {"persona": {"provider": "sumido"}}}}},
     r"ai.profiles.p.roles.persona.provider: provedor 'sumido' não está em ai.providers"),
])
def test_validacao_da_partida_confere_a_persona(ai: dict[str, Any], erro: str) -> None:
    with pytest.raises(ValidationError, match=erro):
        AppConfigFile.model_validate({"ai": ai})


def test_persona_so_precisa_de_texto_modelo_sem_visao_e_aceito(tmp_path: Path) -> None:
    """A geração é só texto: ao contrário de decide/verify/escalation, a persona não exige visão do modelo. Não há
    campo de "capacidade de texto" em `ModelCaps` — todo modelo escreve —, então a recusa de capacidade não se aplica."""
    cfg = com_hub(tmp_path, providers=PROVEDORES, models=MODELOS,
                  roles={"persona": {"provider": "openai-flex", "model": "modelo-flex"}})
    assert RoutingProvider(cfg).roles["persona"].model == "modelo-flex"


def test_fallback_da_persona_resolve_contra_o_destino_dela(tmp_path: Path) -> None:
    cfg = com_hub(tmp_path, providers=PROVEDORES, models=MODELOS,
                  roles={"persona": {"provider": "openai-flex", "model": "modelo-flex",
                                     "fallback_provider": "anthropic"}})
    alt = _com_provedor(cfg, "persona", "anthropic")
    assert alt.role == "persona" and alt.provider == "anthropic" and alt.model == cfg.ai_model_for("social")


# ====================================================================== perfis do 17.7
def test_perfil_que_so_mexe_no_social_leva_a_persona_herdada(tmp_path: Path) -> None:
    cfg = _com_perfis(com_hub(tmp_path, providers=PROVEDORES, roles={}),
                      {"cand": {"roles": {"social": {"model": "claude-haiku-4-5"}}}})
    padrao, cand = cfg.ai_roles(), cfg.ai_roles("cand")
    assert cand["social"].model == "claude-haiku-4-5" == cand["persona"].model
    assert padrao["persona"].model == padrao["social"].model != "claude-haiku-4-5"


def test_perfil_com_persona_separa_da_herdada_e_vence_o_social_do_perfil(tmp_path: Path) -> None:
    cfg = _com_perfis(com_hub(tmp_path, providers=PROVEDORES, models=MODELOS, roles={}),
                      {"cand": {"roles": {"social": {"model": "claude-haiku-4-5"},
                                          "persona": {"provider": "openai-flex", "model": "modelo-flex"}}}})
    cand = cfg.ai_roles("cand")
    assert (cand["persona"].provider, cand["persona"].model) == ("openai-flex", "modelo-flex")
    assert cand["social"].model == "claude-haiku-4-5" and cand["social"].provider == "anthropic"
    assert cfg.ai_role("persona").provider == "anthropic"                  # fora do perfil, o padrão


def test_com_persona_no_padrao_o_social_do_perfil_nao_a_alcanca(tmp_path: Path) -> None:
    cfg = _com_perfis(com_hub(tmp_path, providers=PROVEDORES, models=MODELOS,
                              roles={"persona": {"provider": "openai-flex", "model": "modelo-flex"}}),
                      {"cand": {"roles": {"social": {"model": "claude-haiku-4-5"}}}})
    cand = cfg.ai_roles("cand")
    assert cand["persona"].model == "modelo-flex" and cand["social"].model == "claude-haiku-4-5"


def test_roteador_confere_e_expoe_a_persona_do_perfil(tmp_path: Path) -> None:
    cfg = _com_perfis(com_hub(tmp_path, providers=PROVEDORES, models=MODELOS, roles={}),
                      {"cand": {"roles": {"persona": {"provider": "openai-flex", "model": "modelo-flex"}}}})
    r = RoutingProvider(cfg)
    assert r.roles_por_perfil["cand"]["persona"].provider == "openai-flex"
    assert r.roles["persona"].provider == "anthropic"                      # o padrão não muda por causa do perfil


# ====================================================================== roteamento e contabilidade
async def test_geracao_de_persona_usa_o_papel_persona_e_a_social_continua_social(tmp_path: Path) -> None:
    cfg = com_hub(tmp_path, providers=PROVEDORES, models=MODELOS,
                  roles={"persona": {"provider": "openai-flex", "model": "modelo-flex"}})
    r = RoutingProvider(cfg)
    flex, anth = PersonaFake("openai-flex", "modelo-flex"), PersonaFake("anthropic", "padrao")
    _instala(r, "persona", flex)
    _instala(r, "social", anth)
    draft, usage = await r.generate_persona(_pedido())
    assert draft.name and usage.role == "persona" and usage.provider == "openai-flex"
    _, usage_social = await r.generate_social_response(SimpleNamespace())
    assert usage_social.role == "social" and usage_social.provider == "anthropic"
    assert flex.calls == ["persona"] and anth.calls == ["social"]


async def test_sem_config_a_persona_vai_ao_mesmo_provedor_do_social(tmp_path: Path) -> None:
    cfg = com_hub(tmp_path, providers=PROVEDORES, roles={})
    r = RoutingProvider(cfg)
    assert r.providers["persona"] is r.providers["social"]                 # uma instância só, como antes do 17.8
    unico = PersonaFake("anthropic", "padrao")
    _instala(r, "social", unico)
    await r.generate_persona(_pedido())
    await r.generate_social_response(SimpleNamespace())
    assert unico.calls == ["persona", "social"]


async def test_sem_bloco_proprio_persona_e_social_dividem_as_vagas(tmp_path: Path) -> None:
    """Antes a geração de persona disputava as vagas do `social`; um semáforo novo dobraria o teto somado."""
    r = RoutingProvider(com_hub(tmp_path, providers=PROVEDORES, roles={"social": {"concurrency": 1}}))
    assert r._gates["persona"] is r._gates["social"]                       # noqa: SLF001
    ativos, pico = 0, 0

    async def gera() -> None:
        nonlocal ativos, pico
        async with r._gates["persona"]:                                    # noqa: SLF001
            ativos += 1
            pico = max(pico, ativos)
            await asyncio.sleep(0.01)
            ativos -= 1

    async def escreve() -> None:
        nonlocal ativos, pico
        async with r._gates["social"]:                                     # noqa: SLF001
            ativos += 1
            pico = max(pico, ativos)
            await asyncio.sleep(0.01)
            ativos -= 1

    await asyncio.gather(gera(), escreve(), gera(), escreve())
    assert pico == 1


def test_vagas_proprias_so_com_concurrency_escrito_para_a_persona(tmp_path: Path) -> None:
    so_modelo = RoutingProvider(com_hub(tmp_path, providers=PROVEDORES,
                                        roles={"persona": {"timeout_s": 90}}))
    assert so_modelo._gates["persona"] is so_modelo._gates["social"]       # noqa: SLF001
    com_vagas = RoutingProvider(com_hub(tmp_path, providers=PROVEDORES,
                                        roles={"persona": {"concurrency": 2}}))
    assert com_vagas._gates["persona"] is not com_vagas._gates["social"]   # noqa: SLF001
    assert com_vagas.roles["persona"].concurrency == 2 and com_vagas.roles["social"].concurrency == 4


# ====================================================================== provedores reais, sem rede
async def test_openai_registra_o_papel_persona_no_uso(tmp_path: Path) -> None:
    from .test_openai_provider import _resposta, provider as provedor_openai

    rascunho = persona_simulada(_pedido()).model_dump(mode="json")
    import json

    p, _ = provedor_openai(tmp_path, [_resposta(json.dumps(rascunho))])
    assert isinstance(p, OpenAICompatProvider)
    _, usage = await p.generate_persona(_pedido())
    assert usage.role == "persona"


async def test_anthropic_da_instancia_compartilhada_usa_o_modelo_do_papel_persona(tmp_path: Path) -> None:
    """Dentro do hub a instância pode ter sido criada por OUTRO papel de mesma chave: o modelo é o da instância."""
    import json

    from app.planning.anthropic_provider import AnthropicProvider

    from .test_anthropic_provider import _resp, provider as provedor_anthropic

    rascunho = persona_simulada(_pedido()).model_dump(mode="json")
    p, fake = provedor_anthropic(tmp_path, [_resp([SimpleNamespace(type="text", text=json.dumps(rascunho))])])
    assert isinstance(p, AnthropicProvider)
    p.role = SimpleNamespace(role="plan", provider="anthropic", model="modelo-do-plan", api_key_env=None,
                             refusal_fallback=False, timeout_s=10.0, max_retries=0)
    p.model = "modelo-do-plan"
    _, usage = await p.generate_persona(_pedido())
    assert fake.calls[-1]["model"] == "modelo-do-plan" and usage.role == "persona"
