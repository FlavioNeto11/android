"""Hub de IA (itens 7.1 e 7.2): provedor por função, capacidade declarada, fallback explícito e teto em US$.

O que cada bloco prova, e por que ele existe:

- **Sem `ai.roles`, nada muda.** O `.env` de produção não tem uma linha nova; se o hub mudasse o despacho por
  existir, ele seria uma regressão disfarçada de recurso.
- **Capacidade é declarada.** Antes, um modelo sem visão só falhava depois de ligar o aparelho e gastar uma
  chamada. Aqui ele impede o backend de subir, com o nome da linha a corrigir.
- **Sem fallback pago silencioso.** A falha do endpoint local NÃO chega ao provedor pago sem `fallback_provider`
  escrito. Quando chega, vira linha da execução e colunas próprias em `ai_calls`.
- **Teto em dinheiro.** Por execução e por dia, conferido no MESMO ponto por onde passam planejamento e prévia
  de persona — os dois caminhos que ficavam fora de qualquer orçamento.
- **Piso de conteúdo (item 7.8).** Num provedor local, tier 0, um `element_id` que não existe na tela é
  descartado sem agir — e a PRÓXIMA decisão sobe para tier 1, pelo mesmo mecanismo de `errors_in_row`/
  `same_count`. Provedor Anthropic não muda: ele recebe a árvore inteira e não inventa id de tela antiga.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.config import AppConfigFile, EnvSettings, ModelCaps
from app.db import Database
from app.planning import costs
from app.planning.anthropic_provider import AnthropicProvider, fallback_info
from app.planning.provider import (AIError, AppContext, Decision, DecisionRequest, ScreenInput, SocialRequest,
                                   StepContext, Usage, build_provider)
from app.planning.routing import RoutingProvider, _com_provedor
from app.planning.simulated_provider import SimulatedProvider

from .conftest import CountingProvider, Harness, _dsn_de_teste, make_config

APP = AppContext("qa-messenger", "QA Messenger", "com.pocqa.messenger", ".MainActivity", None, None)
SCREEN = ScreenInput(width=720, height=1280, jpeg=b"\xff\xd8jpeg", elements=["e1 | Button"],
                     package="com.pocqa.messenger", sensitive=False)


def ctx(run_id: str = "r1") -> StepContext:
    return StepContext(run_id=run_id, instance_id="android-01", objective_summary="enviar", parameters={},
                       step_key="send", step_title="Enviar", step_goal="tocar", side_effect=False,
                       commit_done=False, commit_guard=[], precondition=None, postcondition_description="ok",
                       remaining_steps=[], app=APP, account_label=None)


class FakeProvider:
    """Um provedor que conta chamadas e devolve (ou levanta) o que o teste mandar."""

    simulated = False
    configured = True

    def __init__(self, name: str, model: str, *, erro: Exception | None = None, demora: float = 0.0):
        self.name, self.model, self.erro, self.demora = name, model, erro, demora
        self.calls: list[str] = []

    async def _responde(self, papel: str) -> tuple[Any, Usage]:
        self.calls.append(papel)
        if self.demora:
            await asyncio.sleep(self.demora)
        if self.erro is not None:
            raise self.erro
        return object(), Usage(calls=1, role=papel, model=self.model, provider=self.name)

    async def plan(self, req: Any) -> Any:
        return await self._responde("plan")

    async def decide(self, req: Any) -> Any:
        return await self._responde("decide")

    async def verify(self, req: Any) -> Any:
        return await self._responde("verify")

    async def generate_social_response(self, req: Any) -> Any:
        return await self._responde("social")

    def status(self) -> Any:
        from app.models import AiStatus

        return AiStatus(provider=self.name, model=self.model, configured=True, simulated=False,
                        sends_data_externally=True, notice="fake")


class FakeRepo:
    """O mínimo que o roteador usa: o banco (para o gasto) e o barramento/decisão (para o registro)."""

    def __init__(self, db: Any):
        self.db = db
        self.decisions: list[str] = []
        self.events: list[tuple[str, str]] = []
        self.bus = SimpleNamespace(emit=lambda kind, text, **kw: self.events.append((kind, text)))

    def decision(self, text: str, **kw: Any) -> None:
        self.decisions.append(text)


def com_hub(tmp: Path, roles: dict[str, Any], providers: dict[str, Any] | None = None,
            models: dict[str, Any] | None = None) -> Any:
    cfg = make_config(tmp)
    cfg.env.ai_provider = "anthropic"
    if providers:
        cfg.file.ai.providers = {k: type(cfg.file.ai).model_fields["providers"].annotation.__args__[1]  # noqa: SLF001
                                 .model_validate(v) for k, v in providers.items()}
    if models:
        cfg.file.ai.models.update({k: ModelCaps.model_validate(v) for k, v in models.items()})
    cfg.file.ai.roles = {k: type(cfg.file.ai).model_fields["roles"].annotation.__args__[1].model_validate(v)
                         for k, v in roles.items()}
    return cfg


def roteador(cfg: Any, fakes: dict[str, FakeProvider]) -> RoutingProvider:
    r = RoutingProvider(cfg)
    for papel, fake in fakes.items():
        rr = r.roles[papel]
        r._por_chave[(rr.provider, rr.kind, rr.model, rr.timeout_s,                    # noqa: SLF001
                      rr.max_retries, rr.refusal_fallback)] = fake
        r.providers[papel] = fake
    return r


# ====================================================================== 7.1 — provedor por função
def test_sem_roles_o_hub_e_o_de_sempre(tmp_path: Path) -> None:
    """Sem bloco `ai.roles`, as cinco funções resolvem para UMA instância — o comportamento de antes do hub."""
    cfg = make_config(tmp_path)
    cfg.env.ai_provider = "anthropic"
    cfg.env.ai_model_actor = "claude-sonnet-5"
    r = RoutingProvider(cfg)
    assert {papel: rr.model for papel, rr in r.roles.items()} == {
        "plan": "claude-opus-5", "decide": "claude-sonnet-5", "verify": "claude-sonnet-5",
        "escalation": "claude-opus-5", "social": "claude-opus-5"}
    assert all(rr.provider == "anthropic" and rr.fallback_provider is None for rr in r.roles.values())
    # Uma instância por combinação distinta de (provedor, modelo, prazo): funções com a MESMA resolução
    # compartilham a instância, em vez de cada papel abrir um cliente só por existir.
    distintas = {(rr.provider, rr.model, rr.timeout_s) for rr in r.roles.values()}
    assert len({id(p) for p in r.providers.values()}) == len(distintas)
    for papel in ("plan", "decide", "verify", "escalation", "social"):
        cfg.file.ai.roles[papel] = type(cfg.file.ai).model_fields["roles"].annotation.__args__[1].model_validate(
            {"model": "claude-opus-5", "timeout_s": 60})
    assert len({id(p) for p in RoutingProvider(cfg).providers.values()}) == 1


def test_provedor_por_funcao_e_os_dados_saem_por_funcao(tmp_path: Path) -> None:
    """Ator num endpoint local, planejador na Anthropic — e a aba IA responde "os dados saem?" POR função."""
    cfg = com_hub(
        tmp_path,
        providers={"local": {"kind": "openai", "base_url": "http://127.0.0.1:8001/v1",
                             "sends_data_externally": False},
                   "anthropic": {"kind": "anthropic"}},
        models={"qwen-vl": {"vision": True, "tools": True, "structured_output": "json_object",
                            "thinking": False, "effort": False}},
        roles={"decide": {"provider": "local", "model": "qwen-vl"},
               "verify": {"provider": "local", "model": "qwen-vl"},
               "plan": {"provider": "anthropic"}})
    r = RoutingProvider(cfg)
    assert r.roles["decide"].kind == "openai" and r.roles["decide"].endpoint == "127.0.0.1:8001"
    assert r.roles["plan"].kind == "anthropic" and r.roles["plan"].sends_data_externally
    assert not r.roles["decide"].sends_data_externally
    linhas = {linha.role: linha for linha in r.status().roles}
    assert linhas["decide"].model == "qwen-vl" and not linhas["decide"].sends_data_externally
    assert linhas["plan"].sends_data_externally and linhas["plan"].endpoint == "api.anthropic.com"
    # Preço: o modelo local não está em `ai.prices` — a aba diz isso em vez de somar zero escondido.
    assert not linhas["decide"].priced and linhas["plan"].priced
    assert "os dados saem desta máquina em" in r.status().notice


def test_escalonamento_pode_ser_outro_provedor(tmp_path: Path) -> None:
    """`decide` com tier 1 vai para a função `escalation` — que pode ter provedor próprio, não só outro modelo."""
    cfg = com_hub(tmp_path,
                  providers={"local": {"kind": "openai", "base_url": "http://127.0.0.1:8001/v1"},
                             "anthropic": {"kind": "anthropic"}},
                  models={"qwen-vl": {}},
                  roles={"decide": {"provider": "local", "model": "qwen-vl"},
                         "escalation": {"provider": "anthropic", "model": "claude-opus-5"}})
    local, pago = FakeProvider("local", "qwen-vl"), FakeProvider("anthropic", "claude-opus-5")
    r = roteador(cfg, {"decide": local, "escalation": pago})
    asyncio.run(r.decide(DecisionRequest(ctx=ctx(), screen=SCREEN, tier=0)))
    asyncio.run(r.decide(DecisionRequest(ctx=ctx(), screen=SCREEN, tier=1)))
    assert local.calls == ["decide"] and pago.calls == ["decide"]


# ====================================================================== 7.1 — capacidade declarada
def test_modelo_sem_visao_recusa_na_partida(tmp_path: Path) -> None:
    """Achado #97: a função que manda imagem apontada para modelo sem visão é erro de CONFIGURAÇÃO."""
    cfg = com_hub(tmp_path,
                  providers={"local": {"kind": "openai", "base_url": "http://127.0.0.1:8001/v1"}},
                  models={"texto-puro": {"vision": False}},
                  roles={"decide": {"provider": "local", "model": "texto-puro"}})
    with pytest.raises(ValueError, match="sem visão"):
        build_provider(cfg)
    # Com `image_policy: never` a mesma configuração é legítima: ninguém manda imagem para lá.
    cfg.file.ai.image_policy = "never"
    assert build_provider(cfg) is not None


def test_capacidade_desconhecida_e_conservadora(tmp_path: Path) -> None:
    """Modelo fora de `ai.models` quase sempre é um local pequeno: nada de strict, thinking nem effort."""
    cfg = make_config(tmp_path)
    caps = cfg.model_caps("qwen2.5-vl-7b-awq")
    assert not caps.strict_tools and not caps.thinking and not caps.effort
    assert caps.structured_output == "json_object"
    # Família com sufixo de data casa a declaração da família (é assim que o Haiku de produção é reconhecido).
    assert cfg.model_caps("claude-haiku-4-5-20251001").thinking is False
    assert cfg.model_caps("claude-opus-5").thinking is True
    # Achado #100: prefixo mínimo cacheável DECLARADO por modelo — não monótono entre gerações (Haiku 4.5 exige
    # mais que Opus 5, mesmo sendo o modelo mais novo dos dois nessa dimensão).
    assert cfg.model_caps("claude-opus-5").min_cache_tokens == 512
    assert cfg.model_caps("claude-sonnet-5").min_cache_tokens == 1024
    assert cfg.model_caps("claude-haiku-4-5-20251001").min_cache_tokens == 4096
    assert cfg.model_caps("qwen2.5-vl-7b-awq").min_cache_tokens == 0    # não declarado = tenta sempre (0)


def test_esforco_invalido_falha_na_configuracao() -> None:
    """Achado #97: AI_EFFORT_* era `str` livre; um valor errado virava 400 e o `_learn` o lia como capacidade."""
    with pytest.raises(ValidationError):
        EnvSettings(_env_file=None, AI_EFFORT_ACTOR="baixo")  # type: ignore[call-arg]
    assert EnvSettings(_env_file=None, AI_EFFORT_ACTOR="xhigh").ai_effort_actor == "xhigh"  # type: ignore[call-arg]


def test_role_com_modelo_nao_declarado_recusa() -> None:
    """Apontar uma função para um modelo que ninguém declarou é o que este registro existe para impedir."""
    with pytest.raises(ValidationError, match="não está declarado em ai.models"):
        AppConfigFile.model_validate({"ai": {"providers": {"local": {"kind": "openai", "base_url": "http://x/v1"}},
                                             "roles": {"decide": {"provider": "local", "model": "inexistente"}}}})


def test_provedor_openai_exige_base_url() -> None:
    with pytest.raises(ValidationError, match="exige base_url"):
        AppConfigFile.model_validate({"ai": {"providers": {"local": {"kind": "openai"}}}})


def test_learn_so_aprende_de_erro_que_aponta_o_campo(tmp_path: Path) -> None:
    """A palavra solta era o defeito: um 400 sobre o VALOR virava "o modelo não aceita o parâmetro"."""
    import anthropic
    import httpx2 as httpx

    p = AnthropicProvider(make_config(tmp_path))
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def erro(msg: str) -> Any:
        return anthropic.BadRequestError(msg, response=httpx.Response(400, request=req), body=None)

    # "effort" no meio de uma frase sobre o VALOR não ensina nada.
    assert p._learn("m", erro("Invalid value 'baixo' for effort level")) is False   # noqa: SLF001
    # O campo, escrito como a API o nomeia, ensina.
    assert p._learn("m", erro("output_config.effort: unsupported")) is True         # noqa: SLF001
    assert "effort" in p._unsupported["m"]                                          # noqa: SLF001


# ====================================================================== 7.2 — fallback explícito
def test_sem_declaracao_a_falha_do_local_nao_cai_no_pago(tmp_path: Path) -> None:
    """"Sem fallback pago silencioso": sem `fallback_provider` escrito, o erro do endpoint local SOBE."""
    cfg = com_hub(tmp_path,
                  providers={"local": {"kind": "openai", "base_url": "http://127.0.0.1:8001/v1"},
                             "anthropic": {"kind": "anthropic"}},
                  models={"qwen-vl": {}},
                  roles={"decide": {"provider": "local", "model": "qwen-vl"}})
    local = FakeProvider("local", "qwen-vl", erro=AIError("endpoint fora do ar", retryable=True))
    pago = FakeProvider("anthropic", "claude-opus-5")
    r = roteador(cfg, {"decide": local, "escalation": pago})
    r.repo = FakeRepo(None)
    with pytest.raises(AIError, match="fora do ar"):
        asyncio.run(r.decide(DecisionRequest(ctx=ctx(), screen=SCREEN)))
    assert pago.calls == []                    # o provedor pago não foi tocado


def test_fallback_declarado_registra_evento_e_tarifa(tmp_path: Path) -> None:
    """Declarado, ele cai — e a queda vira linha da execução e colunas próprias em `ai_calls`."""
    cfg = com_hub(tmp_path,
                  providers={"local": {"kind": "openai", "base_url": "http://127.0.0.1:8001/v1"},
                             "anthropic": {"kind": "anthropic", "fallback_model": "claude-sonnet-5"}},
                  models={"qwen-vl": {}},
                  roles={"decide": {"provider": "local", "model": "qwen-vl", "fallback_provider": "anthropic"}})
    local = FakeProvider("local", "qwen-vl", erro=AIError("endpoint fora do ar", retryable=True))
    r = roteador(cfg, {"decide": local})
    destino = _com_provedor(cfg, "decide", "anthropic")
    assert destino.model == "claude-sonnet-5"            # não herda o modelo local (que daria 404 no provedor pago)
    pago = FakeProvider("anthropic", "claude-sonnet-5")
    r._por_chave[(destino.provider, destino.kind, destino.model, destino.timeout_s,        # noqa: SLF001
                  destino.max_retries, destino.refusal_fallback)] = pago
    repo = FakeRepo(None)
    r.repo = repo
    _, usage = asyncio.run(r.decide(DecisionRequest(ctx=ctx("r9"), screen=SCREEN)))
    assert pago.calls == ["decide"]
    assert usage.fallback == "anthropic" and usage.requested_model == "qwen-vl"
    assert usage.model == "claude-sonnet-5" and usage.provider == "anthropic"
    assert repo.decisions and "cobrado na tarifa de claude-sonnet-5" in repo.decisions[0]


def test_fallback_de_recusa_do_servidor_vira_usage(tmp_path: Path) -> None:
    """Achado #92: o sinal de "quem serviu" é `usage.iterations` com `fallback_message` — e agora ele é lido.

    A rota "grudada" (o provedor adere ao fallback por ~1 h) não traz bloco `fallback` nenhum no conteúdo, por
    isso o bloco sozinho não bastaria; e um bloco sem `iterations` também precisa ser reconhecido.
    """
    grudada = SimpleNamespace(content=[SimpleNamespace(type="text", text="{}")], stop_reason="end_turn",
                              usage=SimpleNamespace(iterations=[SimpleNamespace(type="message"),
                                                                SimpleNamespace(type="fallback_message")]))
    assert fallback_info(grudada) == "refusal"
    ponto_de_troca = SimpleNamespace(
        content=[{"type": "fallback", "from": {"model": "claude-opus-5"}, "to": {"model": "claude-opus-4-8"}}],
        usage=SimpleNamespace(iterations=None))
    assert fallback_info(ponto_de_troca) == "refusal"
    assert fallback_info(SimpleNamespace(content=[], usage=SimpleNamespace(iterations=[]))) is None


async def test_anthropic_grava_modelo_pedido_e_fallback(tmp_path: Path) -> None:
    """Resposta falsa com bloco `fallback` + `usage.iterations`: `Usage` distingue pedido de quem respondeu."""
    from .test_anthropic_provider import SCREEN as TELA, ctx as passo, provider as fabrica

    resp = SimpleNamespace(
        content=[SimpleNamespace(type="fallback", **{"from": {"model": "claude-opus-5"}}),
                 SimpleNamespace(type="tool_use", name="tap", id="t1",
                                 input={"rationale": "x", "element_id": "e1", "x": None, "y": None,
                                        "is_commit_action": False})],
        stop_reason="tool_use", model="claude-opus-4-8",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                              cache_creation_input_tokens=0,
                              iterations=[SimpleNamespace(type="message"), SimpleNamespace(type="fallback_message")]))
    p, _fake = fabrica(tmp_path, [resp])
    _decision, usage = await p.decide(DecisionRequest(ctx=passo(), screen=TELA))
    assert usage.model == "claude-opus-4-8" and usage.requested_model == p.models["decide"]
    assert usage.fallback == "refusal" and usage.provider == "anthropic"
    # E o destino tem preço cadastrado — antes a linha do painel vinha "sem preço / Total parcial".
    assert costs.price_for(p.cfg.file.ai.prices, "claude-opus-4-8") == [5.0, 0.5, 6.25, 25.0]


def test_preco_casa_o_modelo_exato_e_depois_o_prefixo_mais_longo() -> None:
    """`claude-opus-5-5` começa com `claude-opus-5`: pelo primeiro prefixo na ordem de inserção o Opus 5.5 seria
    cobrado como Opus 5 ($5/$25 em vez de $4/$20) e nunca apareceria como "sem preço". Exato primeiro; depois o
    prefixo mais longo; o sufixo de data continua casando a família."""
    tabela = {"claude-opus-5": [5.0, 0.5, 6.25, 25.0], "claude-opus-5-5": [4.0, 0.2, 5.0, 20.0],
              "claude-haiku-4-5": [1.0, 0.1, 1.25, 5.0]}
    assert costs.price_for(tabela, "claude-opus-5-5") == [4.0, 0.2, 5.0, 20.0]
    assert costs.price_for(tabela, "claude-opus-5") == [5.0, 0.5, 6.25, 25.0]
    assert costs.price_for(tabela, "claude-opus-5-5-20261001") == [4.0, 0.2, 5.0, 20.0]
    assert costs.price_for(tabela, "claude-haiku-4-5-20251001") == [1.0, 0.1, 1.25, 5.0]
    assert costs.price_for(tabela, "qwen3-vl:4b") is None


def test_refusal_fallback_desligavel_por_funcao(tmp_path: Path) -> None:
    """A decisão de onde aceitar o fallback pago é por função (ex.: nunca no social)."""
    cfg = com_hub(tmp_path, providers={"anthropic": {"kind": "anthropic"}},
                  roles={"social": {"provider": "anthropic", "refusal_fallback": False}})
    r = RoutingProvider(cfg)
    assert r.roles["social"].refusal_fallback is False and r.roles["decide"].refusal_fallback is True
    linhas = {linha.role: linha for linha in r.status().roles}
    assert not linhas["social"].refusal_fallback and linhas["decide"].refusal_fallback
    assert r.status().refusal_fallback and "claude-opus-4-8" in (r.status().refusal_fallback_target or "")


# ====================================================================== 7.2 — prazo por função
def test_prazo_por_funcao_corta_chamada_pendurada(tmp_path: Path) -> None:
    """Achado #96: eram 180 s para as cinco funções, e nada cancelava a chamada em voo."""
    cfg = com_hub(tmp_path, providers={"anthropic": {"kind": "anthropic"}},
                  roles={"verify": {"provider": "anthropic", "timeout_s": 0.05}})
    lento = FakeProvider("anthropic", "claude-haiku-4-5", demora=1.0)
    r = roteador(cfg, {"verify": lento})
    with pytest.raises(AIError, match="passou de 0 s"):
        asyncio.run(r.verify(SimpleNamespace(ctx=ctx(), screen=SCREEN, facts=[])))


def test_prazos_padrao_por_funcao(tmp_path: Path) -> None:
    """Os padrões saem folgados diante do medido (máx. plan 29,5 s · decide 12,2 s · verify 7,6 s)."""
    cfg = make_config(tmp_path)
    assert [cfg.ai_role(p).timeout_s for p in ("plan", "decide", "verify", "social")] == [120.0, 45.0, 30.0, 60.0]
    assert cfg.ai_role("decide").max_retries == 0     # o dono das novas tentativas é o `_ai`, não o SDK


# ====================================================================== 7.2 — teto em US$
def _banco_com_gasto(tmp_path: Path, linhas: list[tuple[str, str, int, int]]) -> Database:
    db = Database(_dsn_de_teste() or tmp_path / "usd.sqlite3")
    db.migrate()
    for run_id, model, entrada, saida in linhas:
        db.execute("INSERT INTO ai_calls(ts, run_id, role, model, input_tokens, output_tokens)"
                   " VALUES (?,?,?,?,?,?)", (costs.day_start_iso(), run_id, "decide", model, entrada, saida))
    return db


def test_modelo_sem_preco_conta_pelo_mais_caro() -> None:
    """Achado #95, item 4: nunca zero. Zero só existe quando alguém DECLAROU `[0,0,0,0]`."""
    prices = {"claude-opus-5": [5.0, 0.5, 6.25, 25.0], "claude-haiku-4-5": [1.0, 0.1, 1.25, 5.0],
              "qwen-local": [0.0, 0.0, 0.0, 0.0]}
    assert costs.usd(prices, "modelo-que-ninguem-cadastrou", [1_000_000, 0, 0, 0]) == 5.0
    assert costs.usd(prices, "qwen-local", [1_000_000, 0, 0, 1_000_000]) == 0.0
    assert costs.usd(prices, "claude-haiku-4-5-20251001", [0, 0, 0, 1_000_000]) == 5.0


def test_teto_em_usd_por_execucao_e_por_dia(tmp_path: Path) -> None:
    """O teto em dinheiro barra ANTES de gastar, e o aviso de 80 % sai uma vez."""
    db = _banco_com_gasto(tmp_path, [("r9", "claude-opus-5", 1_000_000, 0)])   # US$ 5,00 nesta execução e no dia
    cfg = com_hub(tmp_path, providers={"anthropic": {"kind": "anthropic"}}, roles={})
    r = roteador(cfg, {"decide": FakeProvider("anthropic", "claude-opus-5")})
    repo = FakeRepo(db)
    r.attach(repo=repo, settings_getter=lambda: SimpleNamespace(ai_max_usd_per_run=4.0, ai_max_usd_per_day=0.0))
    with pytest.raises(AIError) as e:
        asyncio.run(r.decide(DecisionRequest(ctx=ctx("r9"), screen=SCREEN)))
    assert e.value.kind == "budget" and "US$ 5.00 de US$ 4.00" in str(e.value)

    # 80 %: passa, mas avisa — e avisa UMA vez, não a cada chamada.
    r._avisados.clear()                                                        # noqa: SLF001
    r.get_settings = lambda: SimpleNamespace(ai_max_usd_per_run=6.0, ai_max_usd_per_day=0.0)
    asyncio.run(r.decide(DecisionRequest(ctx=ctx("r9"), screen=SCREEN)))
    asyncio.run(r.decide(DecisionRequest(ctx=ctx("r9"), screen=SCREEN)))
    assert len([e for e in repo.events if "Gasto de IA" in e[1]]) == 1

    # Teto do DIA vale para quem não tem execução nenhuma — é por onde a prévia de persona passava livre.
    r.get_settings = lambda: SimpleNamespace(ai_max_usd_per_run=0.0, ai_max_usd_per_day=3.0)
    r.providers["social"] = FakeProvider("anthropic", "claude-opus-5")
    rr = r.roles["social"]
    r._por_chave[(rr.provider, rr.kind, rr.model, rr.timeout_s,                # noqa: SLF001
                  rr.max_retries, rr.refusal_fallback)] = r.providers["social"]
    with pytest.raises(AIError) as e2:
        asyncio.run(r.generate_social_response(SocialRequest(profile_id="p1", username="u", kind="dm_reply",
                                                             context_text="", incoming="oi")))
    assert e2.value.kind == "budget" and "do dia" in str(e2.value)
    db.close()


def test_gasto_de_hoje_soma_por_modelo(tmp_path: Path) -> None:
    db = _banco_com_gasto(tmp_path, [("r1", "claude-opus-5", 1_000_000, 0),
                                     ("r2", "claude-haiku-4-5", 0, 1_000_000)])
    prices = {"claude-opus-5": [5.0, 0.5, 6.25, 25.0], "claude-haiku-4-5": [1.0, 0.1, 1.25, 5.0]}
    assert costs.spent_today_usd(db, prices) == 10.0
    assert costs.spent_usd(db, prices, run_id="r2") == 5.0
    db.close()


def test_ai_calls_guarda_modelo_pedido_e_provedor(tmp_path: Path) -> None:
    """Migração 032: `requested_model`, `fallback` e `provider` existem e são gravados por `add_usage`."""
    db = _banco_com_gasto(tmp_path, [])
    colunas = set(db.columns("ai_calls"))
    assert {"requested_model", "fallback", "provider"} <= colunas
    db.execute("INSERT INTO ai_calls(ts, run_id, role, model, requested_model, fallback, provider)"
               " VALUES (?,?,?,?,?,?,?)",
               (costs.day_start_iso(), "r1", "decide", "claude-opus-4-8", "claude-opus-5", "refusal", "anthropic"))
    linha = db.one("SELECT * FROM ai_calls WHERE fallback IS NOT NULL")
    assert linha["requested_model"] == "claude-opus-5" and linha["model"] == "claude-opus-4-8"
    db.close()


# ====================================================================== 7.2 — o prazo da etapa corta a chamada
async def test_chamada_nao_sobrevive_ao_prazo_da_etapa() -> None:
    """Achado #96, item 2: nada cancelava a chamada em voo quando o prazo da etapa vencia.

    Era o `_ai` que só conferia o prazo no topo do laço: um provedor pendurado segurava a vaga de IA e o
    aparelho por minutos ALÉM dos 180 s da etapa.
    """
    import time as _time

    from app.taskqueue.executor import _com_prazo

    async def pendurado() -> Any:
        await asyncio.sleep(5)
        return "tarde demais"

    with pytest.raises(AIError, match="passou do prazo restante da etapa"):
        await _com_prazo(pendurado(), _time.monotonic() + 0.05, "decide")
    # Sem prazo, nada muda: é o caminho de quem não tem etapa (planejamento, prévia de persona).
    async def rapido() -> Any:
        return "ok"

    assert await _com_prazo(rapido(), None, "plan") == "ok"


async def test_escalonamento_aparece_na_linha_do_tempo(harness: Any) -> None:
    """Achado #92, item 5: o escalonamento era configuração explícita do dono e já aparecia no cartão de custo —
    faltava a linha na execução dizendo POR QUE esta etapa passou a decidir no modelo caro."""
    run = harness.run(["android-01"])
    await harness.wait_run(run.id)
    linhas = [r["message"] for r in harness.state.db.query(
        "SELECT message FROM events WHERE kind='decision' AND run_id=?", (run.id,))]
    escalonadas = [m for m in linhas if "escalonada para o modelo de escalonamento" in m]
    assert escalonadas, f"nenhuma linha de escalonamento em: {linhas}"
    assert "etapa com efeito externo" in escalonadas[0]
    # Uma vez por etapa, não uma por decisão: a linha do tempo não vira log de laço.
    assert len(escalonadas) == len({m for m in escalonadas})


def test_recusa_do_provedor_vira_linha_da_execucao(tmp_path: Path) -> None:
    """O que o pedido exige e não existia: a troca por recusa na LINHA DO TEMPO da execução.

    Antes ela só aparecia como um modelo diferente no `model` de uma linha de custo — indistinguível de "estava
    configurado assim". O evento tem de sair com o `run_id`, senão vira um log solto que ninguém liga à execução.
    """
    class Recusado(FakeProvider):
        async def _responde(self, papel: str) -> tuple[Any, Usage]:
            self.calls.append(papel)
            return object(), Usage(calls=1, role=papel, model="claude-opus-4-8", provider="anthropic",
                                   requested_model="claude-opus-5", fallback="refusal")

    cfg = com_hub(tmp_path, providers={"anthropic": {"kind": "anthropic"}}, roles={})
    r = roteador(cfg, {"decide": Recusado("anthropic", "claude-opus-5")})
    repo = FakeRepo(None)
    r.repo = repo
    _, usage = asyncio.run(r.decide(DecisionRequest(ctx=ctx("r7"), screen=SCREEN)))
    assert usage.fallback == "refusal"
    assert len(repo.decisions) == 1
    assert "Recusa de claude-opus-5" in repo.decisions[0]
    assert "cobrado na tarifa de claude-opus-4-8" in repo.decisions[0]


def test_modo_simulado_nao_gasta_dinheiro(tmp_path: Path) -> None:
    """Chamada simulada não custa nada — e não pode aparecer como dólar nem contar contra o teto.

    Sem isto, o modelo "simulado" (que não está em `ai.prices`) seria contado pelo preço mais caro da tabela: uma
    bateria de desenvolvimento viraria dinheiro no painel e, com teto apertado, bloquearia a própria bateria.
    """
    db = _banco_com_gasto(tmp_path, [])
    db.execute("INSERT INTO ai_calls(ts, run_id, role, model, provider, input_tokens, output_tokens)"
               " VALUES (?,?,?,?,?,?,?)",
               (costs.day_start_iso(), "r1", "decide", "simulado", "simulated", 1_000_000, 1_000_000))
    prices = {"claude-opus-5": [5.0, 0.5, 6.25, 25.0]}
    assert costs.spent_today_usd(db, prices) == 0.0
    assert costs.spent_usd(db, prices, run_id="r1") == 0.0

    # E o roteador nem consulta o banco quando a função é simulada: teto não se aplica a gasto que não existe.
    cfg = make_config(tmp_path)          # AI_PROVIDER=simulated no harness
    r = roteador(cfg, {"decide": FakeProvider("simulated", "simulado")})
    r.attach(repo=FakeRepo(db), settings_getter=lambda: SimpleNamespace(ai_max_usd_per_run=0.000001,
                                                                       ai_max_usd_per_day=0.000001))
    assert r.roles["decide"].kind == "simulated"
    asyncio.run(r.decide(DecisionRequest(ctx=ctx("r1"), screen=SCREEN)))     # não levanta budget
    db.close()


def test_modelo_com_sufixo_de_data_e_aceito_na_configuracao() -> None:
    """Produção usa `claude-haiku-4-5-20251001`: exigir a chave exata rejeitaria o que o runtime aceita."""
    cfg = AppConfigFile.model_validate({"ai": {"providers": {"api": {"kind": "anthropic"}},
                                               "roles": {"verify": {"provider": "api",
                                                                    "model": "claude-haiku-4-5-20251001"}}}})
    assert cfg.ai.roles["verify"].model == "claude-haiku-4-5-20251001"


# ====================================================================== 7.8 — piso de conteúdo (modelo local)
class _DecideComAlvoFantasma(CountingProvider):
    """Na PRIMEIRA decisão de tier 0 da etapa `open_conversation`, troca o `element_id` de verdade por um que
    não existe na tela — é o que um endpoint local com pouco contexto faz quando "esquece" a árvore atual e
    ecoa um id de uma resposta anterior. As demais chamadas respondem normalmente."""

    def __init__(self, inner: Any) -> None:
        super().__init__(inner)
        self.armou = False

    async def decide(self, req: Any) -> Any:
        decision, usage = await super().decide(req)
        if not self.armou and req.tier == 0 and req.ctx.step_key == "open_conversation":
            self.armou = True
            decision = Decision(tool=decision.tool, args={**decision.args, "element_id": "e-fantasma-999"})
        return decision, usage


async def test_piso_descarta_alvo_inexistente_e_sobe_a_proxima_decisao_para_tier_1(tmp_path: Path) -> None:
    """A suíte inteira roda com `AI_PROVIDER=simulated` — que aqui faz as vezes do provedor LOCAL (nenhum teste
    chama endpoint de verdade): `kind` resolve para `simulated`, que não é `anthropic`, e é exatamente essa a
    condição do piso. Com Anthropic o comportamento não muda (não há checagem nenhuma no meio do caminho)."""
    h = Harness(tmp_path, 3)
    assert h.cfg.ai_role("decide").kind != "anthropic"          # a condição do piso, antes de qualquer chamada
    h.ai = _DecideComAlvoFantasma(SimulatedProvider())
    await h.boot()
    try:
        run = h.run(["android-01"])
        detail = await h.wait_run(run.id)
        assert detail.status == "completed"                    # a etapa se recupera sozinha, sem gastar tentativa
        assert len(h.fakes["android-01"].messages) == 1         # e a mensagem chegou — nada corrompeu o resto do fluxo
        ai = h.ai
        assert ai.armou
        chamadas = [c["tier"] for c in ai.calls if c["role"] == "decide" and c["step"] == "open_conversation"]
        # (1) tier 0 com o alvo fantasma: descartada, não virou ação nem erro contado; (2) a decisão SEGUINTE já
        # sobe para tier 1 — é a mesma escolhida antes, agora com o `element_id` de verdade, e o toque acontece;
        # (3) de volta ao tier 0: a subida vale só para a PRÓXIMA decisão, não para o resto da etapa.
        assert chamadas == [0, 1, 0], chamadas
        # A linha do tempo diz POR QUE escalou — não pode herdar o motivo genérico de "ação repetida".
        linhas = [r["message"] for r in h.state.db.query(  # type: ignore[union-attr]
            "SELECT message FROM events WHERE kind='decision' AND run_id=?", (run.id,))]
        escalonadas = [m for m in linhas if "escalonada para o modelo de escalonamento" in m]
        assert escalonadas and "piso do modelo local" in escalonadas[0], escalonadas
    finally:
        await h.state.stop()


async def test_piso_nao_muda_nada_com_provedor_anthropic(tmp_path: Path) -> None:
    """"Com provedor Anthropic o comportamento atual não muda": o MESMO alvo fantasma, mas com `decide.kind ==
    anthropic`, não é descartado pelo executor — a chamada segue seu caminho de sempre (o driver falso é quem
    reclama do elemento inexistente, como reclamaria de qualquer erro real de mira)."""
    h = Harness(tmp_path, 3)
    h.cfg.env.ai_provider = "anthropic"
    assert h.cfg.ai_role("decide").kind == "anthropic"
    h.ai = _DecideComAlvoFantasma(SimulatedProvider())
    await h.boot()
    try:
        run = h.run(["android-01"])
        detail = await h.wait_run(run.id)
        ai = h.ai
        assert ai.armou
        chamadas = [c["tier"] for c in ai.calls if c["role"] == "decide" and c["step"] == "open_conversation"]
        # Nada de escalonar pelo piso: sem a checagem, `errors_in_row` sobe no máximo 1 por essa falha isolada
        # (precisa de 2 para forçar tier 1 por outro caminho) — as duas primeiras decisões continuam em tier 0.
        assert chamadas[:2] == [0, 0], chamadas
        linhas = [r["message"] for r in h.state.db.query(  # type: ignore[union-attr]
            "SELECT message FROM events WHERE kind='decision' AND run_id=?", (run.id,))]
        assert not any("piso do modelo local" in m for m in linhas)
        assert detail.status in ("completed", "completed_with_issues", "failed")  # só não pode travar a suíte
    finally:
        await h.state.stop()
