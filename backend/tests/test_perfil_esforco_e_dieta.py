"""Item 17.14 (esforço e thinking por função num perfil, sonda "o ator pensa?") e RA-17 (dieta do contexto 2).

O que se prova aqui (simulated: cliente Anthropic falso, banco de teste):
- sem nada escrito, a requisição do ator sai como antes: imagem primeiro, um ponto de cache só no system, `thinking`
  quando o modelo declara;
- `effort`, `thinking: false` e `cache_da_etapa` de uma função valem só para ela, inclusive na `escalation`, que tem
  instância própria mesmo com o ajuste igual ao do `decide`;
- `cache_da_etapa` põe o bloco estável (passo e lições) antes da imagem, com o 2º ponto de cache, e os dois textos
  juntos são exatamente o texto de antes;
- o perfil troca o lado da imagem e o mínimo da árvore rica só das execuções dele.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.automation.hierarchy import UiElement, UiTree
from app.config import AiProfileCfg, ModelCaps, RoleCfg
from app.planning import prompts
from app.planning.anthropic_provider import AnthropicProvider
from app.planning.provider import DecisionRequest, VerifyRequest
from app.planning.routing import RoutingProvider
from app.planning.simulated_provider import SimulatedProvider
from app.taskqueue.executor import StepExecutor as Executor

from .test_anthropic_provider import SCREEN, FakeMessages, _resp, ctx
from .test_hub_de_ia import FakeRepo, com_hub
from .test_perfil_de_ia import _banco

PROVEDORES = {"anthropic": {"kind": "anthropic"}}
OLHAR = SimpleNamespace(type="tool_use", name="observe_screen", id="t1", input={"rationale": "x", "need_image": False})


def _cliente(p: Any, n: int = 4) -> FakeMessages:
    fake = FakeMessages([_resp([OLHAR], stop="tool_use") for _ in range(n)])
    p.configured = True
    p._client = SimpleNamespace(messages=fake, beta=SimpleNamespace(messages=fake))  # noqa: SLF001
    return fake


def _roteador(tmp: Path, roles: dict[str, Any], perfis: dict[str, Any] | None = None) -> RoutingProvider:
    cfg = com_hub(tmp, providers=PROVEDORES, roles=roles)
    cfg.file.ai.profiles = {nome: AiProfileCfg.model_validate(p) for nome, p in (perfis or {}).items()}
    return RoutingProvider(cfg)


# ====================================================================== configuração
def test_campos_novos_vazios_resolvem_como_hoje(tmp_path: Path) -> None:
    cfg = com_hub(tmp_path, providers=PROVEDORES, roles={"decide": {"timeout_s": 33}})
    r = cfg.ai_role("decide")
    assert (r.effort_declarado, r.thinking, r.cache_da_etapa) == (None, None, False)
    assert r.effort == cfg.ai_effort_for("decide")                       # o do .env, como antes
    assert cfg.ai_da_execucao(None) is cfg.file.ai


def test_perfil_escreve_esforco_thinking_e_cache_so_da_funcao_dele(tmp_path: Path) -> None:
    cfg = com_hub(tmp_path, providers=PROVEDORES, roles={})
    cfg.file.ai.profiles = {"rapido": AiProfileCfg(roles={"decide": RoleCfg(effort="low", thinking=False,
                                                                             cache_da_etapa=True)})}
    padrao, rapido = cfg.ai_roles(), cfg.ai_roles("rapido")
    d = rapido["decide"]
    assert (d.effort, d.effort_declarado, d.thinking, d.cache_da_etapa) == ("low", "low", False, True)
    for papel in ("plan", "verify", "escalation", "social", "persona"):
        assert rapido[papel] == padrao[papel], papel


def test_esforco_fora_da_lista_recusa_a_partida() -> None:
    with pytest.raises(ValidationError):
        RoleCfg.model_validate({"effort": "maximo"})


def test_perfil_troca_imagem_e_arvore_so_das_execucoes_dele(tmp_path: Path) -> None:
    cfg = com_hub(tmp_path, providers=PROVEDORES, roles={})
    cfg.file.ai.profiles = {"img-768": AiProfileCfg(screenshot_max_side=768, rich_tree_min_elements=12),
                            "so-modelo": AiProfileCfg(roles={"decide": RoleCfg(model="claude-haiku-4-5")})}
    global_ = cfg.file.ai
    dieta = cfg.ai_da_execucao("img-768")
    assert (dieta.screenshot_max_side, dieta.rich_tree_min_elements) == (768, 12)
    assert dieta is not global_ and global_.screenshot_max_side != 768     # o global não muda
    assert dieta.image_policy == global_.image_policy and dieta.prices == global_.prices
    assert cfg.ai_da_execucao("so-modelo") is global_                       # perfil sem esses campos: o mesmo objeto
    assert cfg.ai_da_execucao("sumido") is global_
    with pytest.raises(ValidationError):
        AiProfileCfg(screenshot_max_side=100)


# ====================================================================== instâncias do hub
def test_sem_ajuste_a_chave_da_instancia_e_a_de_sempre(tmp_path: Path) -> None:
    r = _roteador(tmp_path, roles={})
    rr = r.roles["decide"]
    chave = (rr.provider, rr.kind, rr.model, rr.timeout_s, rr.max_retries, rr.refusal_fallback)
    assert r._por_chave[chave] is r.providers["decide"]                   # noqa: SLF001 - os testes do hub usam essa chave


def test_ajuste_ganha_instancia_propria_por_funcao(tmp_path: Path) -> None:
    """Mesmo ajuste em `decide` e `escalation`: duas instâncias, senão a 2ª perderia o ajuste (o provedor só o aplica
    à função dona da instância)."""
    r = _roteador(tmp_path, roles={"decide": {"thinking": False}, "escalation": {"thinking": False}})
    assert r.providers["decide"] is not r.providers["escalation"]
    assert r.providers["plan"] is not r.providers["decide"]
    assert r.providers["escalation"].role.role == "escalation"


# ====================================================================== a requisição do ator
async def test_padrao_sai_byte_a_byte_como_antes(tmp_path: Path) -> None:
    r = _roteador(tmp_path, roles={})
    p = r.providers["decide"]
    fake = _cliente(p)
    req = DecisionRequest(ctx=ctx(), screen=SCREEN, history=["tap(e1) → ok"])
    await p.decide(req)
    call = fake.calls[0]
    image, text = call["messages"][0]["content"]
    assert image["type"] == "image" and "cache_control" not in image
    assert text == {"type": "text", "text": prompts.actor_user_text(req)}
    assert [b.get("cache_control") for b in call["system"]] == [{"type": "ephemeral"}]
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"]["effort"] == p.cfg.env.ai_effort_actor


async def test_cache_da_etapa_poe_o_bloco_estavel_antes_da_imagem(tmp_path: Path) -> None:
    r = _roteador(tmp_path, roles={"decide": {"cache_da_etapa": True}})
    fake = _cliente(r.providers["decide"])
    req = DecisionRequest(ctx=ctx(), screen=SCREEN, history=["tap(e1) → ok"])
    await r.providers["decide"].decide(req)
    estavel, image, volatil = fake.calls[0]["messages"][0]["content"]
    assert estavel["cache_control"] == {"type": "ephemeral"} and "cache_control" not in volatil
    assert image["type"] == "image"
    assert estavel["text"] + volatil["text"] == prompts.actor_user_text(req)     # nada some, nada muda de lugar no texto
    assert "ETAPA COM EFEITO EXTERNO" in estavel["text"] and "<elementos_da_tela>" in volatil["text"]
    assert "tap(e1) → ok" in volatil["text"]                              # o histórico muda a cada decisão: fora do cache


async def test_cache_da_etapa_sem_imagem_mantem_os_dois_blocos(tmp_path: Path) -> None:
    r = _roteador(tmp_path, roles={"decide": {"cache_da_etapa": True}})
    fake = _cliente(r.providers["decide"])
    sem_imagem = dataclasses.replace(SCREEN, jpeg=None)
    await r.providers["decide"].decide(DecisionRequest(ctx=ctx(), screen=sem_imagem))
    assert [b["type"] for b in fake.calls[0]["messages"][0]["content"]] == ["text", "text"]


def test_estavel_nao_depende_da_observacao() -> None:
    """O 2º ponto de cache só paga se o bloco estável é igual entre as decisões da tentativa."""
    a = prompts.actor_user_partes(DecisionRequest(ctx=ctx(), screen=SCREEN, history=[]))
    outra_tela = type(SCREEN)(width=720, height=1280, jpeg=None, elements=["e9 | Text | text=\"Outra\""],
                              package="com.outro", sensitive=False)
    b = prompts.actor_user_partes(DecisionRequest(ctx=ctx(), screen=outra_tela, history=["a", "b"]))
    assert a[0] == b[0] and a[1] != b[1]


async def test_thinking_false_e_esforco_da_funcao(tmp_path: Path) -> None:
    r = _roteador(tmp_path, roles={"decide": {"thinking": False, "effort": "high"}})
    fake = _cliente(r.providers["decide"])
    await r.providers["decide"].decide(DecisionRequest(ctx=ctx(), screen=SCREEN))
    call = fake.calls[0]
    assert "thinking" not in call and call["output_config"]["effort"] == "high"
    # o planejador continua com o thinking e o esforço de sempre
    assert r.providers["plan"].estado_do_thinking("plan", r.roles["plan"].model) == "adaptive"
    assert r.roles["plan"].effort == r.cfg.ai_effort_for("plan") and r.roles["plan"].effort_declarado is None


async def test_escalonamento_ajustado_vale_pelo_despacho_do_hub(tmp_path: Path) -> None:
    """`thinking: false` só na `escalation`: a decisão escalada sai sem thinking, a de nível 0 com."""
    r = _roteador(tmp_path, roles={"escalation": {"thinking": False}})
    r.repo = FakeRepo(_banco(tmp_path, {"r1": None}))
    fake_d, fake_e = _cliente(r.providers["decide"]), _cliente(r.providers["escalation"])
    await r.decide(DecisionRequest(ctx=ctx(), screen=SCREEN, tier=0))
    await r.decide(DecisionRequest(ctx=ctx(), screen=SCREEN, tier=1))
    assert len(fake_d.calls) == len(fake_e.calls) == 1
    assert fake_d.calls[0]["thinking"] == {"type": "adaptive"} and "thinking" not in fake_e.calls[0]


async def test_rejulgamento_escalado_usa_o_ajuste_da_escalation(tmp_path: Path) -> None:
    r = _roteador(tmp_path, roles={"escalation": {"thinking": False, "effort": "low"}})
    p = r.providers["escalation"]
    verdict = SimpleNamespace(type="text", text=json.dumps({"satisfied": "yes", "evidence": "ok", "delivery_level": None}))
    fake = FakeMessages([_resp([verdict])])
    p.configured = True
    p._client = SimpleNamespace(messages=fake, beta=SimpleNamespace(messages=fake))  # noqa: SLF001
    await p.verify(VerifyRequest(ctx=ctx(), screen=SCREEN, escalate=True))
    assert "thinking" not in fake.calls[0] and fake.calls[0]["output_config"]["effort"] == "low"


# ====================================================================== sonda "o ator pensa?"
def test_sonda_do_thinking_na_aba_ia(tmp_path: Path) -> None:
    r = _roteador(tmp_path, roles={"verify": {"thinking": False}})
    st = {f.role: f.thinking for f in r.status().roles}
    assert st["decide"] == "adaptive" and st["verify"] == "desligado_na_funcao"

    r.cfg.file.ai.models[r.roles["decide"].model] = ModelCaps(thinking=False)
    assert {f.role: f.thinking for f in r.status().roles}["decide"] == "nao_declarado"
    r.cfg.file.ai.models[r.roles["decide"].model] = ModelCaps()
    r.providers["decide"]._unsupported.setdefault(r.roles["decide"].model, set()).add("thinking")  # noqa: SLF001
    assert {f.role: f.thinking for f in r.status().roles}["decide"] == "recusado_pelo_modelo"


def test_sonda_vazia_em_provedor_sem_thinking(tmp_path: Path) -> None:
    r = _roteador(tmp_path, roles={})
    r.providers["decide"] = SimulatedProvider()
    assert {f.role: f.thinking for f in r.status().roles}["decide"] is None


# ====================================================================== executor: o bloco `ai` da execução
def _exec(cfg: Any, db: Any) -> SimpleNamespace:
    ex = SimpleNamespace(cfg=cfg, repo=SimpleNamespace(db=db))
    # `_want_image` deriva de `_motivo_da_imagem` (RA-10, suíte 7): o dublê leva os dois.
    ex._motivo_da_imagem = lambda *a, **k: Executor._motivo_da_imagem(ex, *a, **k)  # type: ignore[arg-type]
    return ex


def test_executor_le_o_perfil_da_execucao(tmp_path: Path) -> None:
    cfg = com_hub(tmp_path, providers=PROVEDORES, roles={})
    db = _banco(tmp_path, {"r-dieta": "img-768", "r-padrao": None})
    sem_perfis = _exec(cfg, None)                                          # sem perfis: nem lê o banco
    assert Executor._ai_da_execucao(sem_perfis, "r-dieta") is cfg.file.ai

    cfg.file.ai.profiles = {"img-768": AiProfileCfg(screenshot_max_side=768, rich_tree_min_elements=12)}
    ex = _exec(cfg, db)
    dieta, padrao = Executor._ai_da_execucao(ex, "r-dieta"), Executor._ai_da_execucao(ex, "r-padrao")
    assert dieta.screenshot_max_side == 768 and padrao is cfg.file.ai

    tela = SimpleNamespace(width=1080, height=2400)
    assert Executor._image_scale(ex, tela, dieta) == pytest.approx(2400 / 768)
    assert Executor._image_scale(ex, tela) == pytest.approx(max(1.0, 2400 / cfg.file.ai.screenshot_max_side))

    # árvore com 8 elementos informativos, política `auto`: com o mínimo 12 do perfil é pobre (vai a imagem); com 8, rica
    arvore = UiTree(elements=[UiElement(id=f"e{i}", text=f"b{i}", desc="", resource_id="", class_name="Button",
                                        package="x", bounds=(0, 0, 1, 1), clickable=True, enabled=True, focused=False,
                                        scrollable=False, editable=False, checked=False, password=False)
                              for i in range(8)], packages=["x"], sensitive=False)
    pede = dict(judged_step=False, first=False, trouble=False, requested=False)
    auto = dieta.model_copy(update={"image_policy": "auto"})
    assert Executor._want_image(ex, arvore, **pede, ai=auto) is True
    rica = auto.model_copy(update={"rich_tree_min_elements": 8})
    assert Executor._want_image(ex, arvore, **pede, ai=rica) is False
