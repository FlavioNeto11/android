"""Provedor compatível com OpenAI (item 7.1, achado #91), sem rede: o que sai na requisição e o que entra dela.

`httpx.MockTransport` responde no lugar do servidor, então isto prova o CONTRATO — formato das ferramentas, saída
estruturada, imagem, uso, mapeamento de erro. NÃO substitui rodar contra um vLLM de verdade: isso exige subir o
servidor em WSL2/Docker com passagem de GPU, e está registrado como pendente no resultado do item.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from app.config import EnvSettings, ModelCaps, ProviderCfg, RoleCfg
from app.planning.openai_provider import OpenAICompatProvider, openai_tools
from app.planning.provider import (AIError, AppContext, DecisionRequest, PlanRequest, ScreenInput, SocialRequest,
                                   StepContext, VerifyRequest)

from .conftest import make_config

APP = AppContext("qa-messenger", "QA Messenger", "com.pocqa.messenger", ".MainActivity", "dicas", None)
SCREEN = ScreenInput(width=720, height=1280, jpeg=b"\xff\xd8jpeg", elements=['e1 | Button | text="Enviar"'],
                     package="com.pocqa.messenger")


def ctx(**over: Any) -> StepContext:
    base = dict(run_id="r1", instance_id="android-01", objective_summary="enviar", parameters={},
                step_key="send", step_title="Enviar", step_goal="tocar em Enviar", side_effect=False,
                commit_done=False, commit_guard=[], precondition=None, postcondition_description="aparece",
                remaining_steps=[], app=APP, account_label="qa-user-01")
    return StepContext(**{**base, **over})


def _resposta(conteudo: str | None = None, *, tool: dict[str, Any] | None = None, finish: str = "stop",
              modelo: str = "qwen-vl") -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": conteudo}
    if tool is not None:
        msg["tool_calls"] = [{"id": "c1", "type": "function", "function": tool}]
    return {"model": modelo, "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 80,
                      "prompt_tokens_details": {"cached_tokens": 200}}}


def provider(tmp: Path, respostas: list[Any], *, caps: ModelCaps | None = None,
             modelo: str = "qwen-vl") -> tuple[OpenAICompatProvider, list[httpx.Request]]:
    cfg = make_config(tmp)
    cfg.file.ai.models[modelo] = caps or ModelCaps(vision=True, tools=True, strict_tools=False,
                                                   structured_output="json_schema", thinking=False, effort=False)
    cfg.file.ai.providers["local"] = ProviderCfg(kind="openai", base_url="http://127.0.0.1:8001/v1",
                                                 sends_data_externally=False)
    cfg.file.ai.roles["decide"] = RoleCfg(provider="local", model=modelo)
    role = cfg.ai_role("decide")
    p = OpenAICompatProvider(cfg, role=role)
    p.models = {papel: modelo for papel in ("plan", "decide", "verify", "escalation", "social")}
    vistos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        r = respostas.pop(0)
        if isinstance(r, httpx.Response):
            return r
        return httpx.Response(200, json=r)

    p.set_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return p, vistos


def test_traducao_das_ferramentas() -> None:
    """As MESMAS 19 ferramentas do ator, no formato do OpenAI — uma tradução, não um segundo catálogo.

    Eram 17; a 18ª é `read_value` (T20, item 24.3), que lê o valor que a etapa entrega às seguintes; a 19ª é `find_row` (31.340)."""
    frouxas = openai_tools(strict=False)
    assert len(frouxas) == 19 and all(t["type"] == "function" for t in frouxas)
    nomes = {t["function"]["name"] for t in frouxas}
    assert {"tap", "type_text", "step_done", "observe_screen", "read_value"} <= nomes
    assert all("parameters" in t["function"] and "strict" not in t["function"] for t in frouxas)
    estritas = {t["function"]["name"] for t in openai_tools(strict=True) if t["function"].get("strict")}
    assert estritas == {"tap", "long_press", "drag", "type_text", "step_done", "step_blocked"}


async def test_decisao_manda_imagem_e_ferramentas(tmp_path: Path) -> None:
    tool = {"name": "tap", "arguments": json.dumps({"rationale": "enviar", "element_id": "e1", "x": None,
                                                    "y": None, "is_commit_action": False})}
    p, vistos = provider(tmp_path, [_resposta(tool=tool, finish="tool_calls")])
    decision, usage = await p.decide(DecisionRequest(ctx=ctx(), screen=SCREEN))
    corpo = json.loads(vistos[0].content)
    assert str(vistos[0].url) == "http://127.0.0.1:8001/v1/chat/completions"
    assert corpo["messages"][0]["role"] == "system" and corpo["tool_choice"] == "auto"
    assert corpo["parallel_tool_calls"] is False
    imagem, texto = corpo["messages"][1]["content"]
    assert imagem["type"] == "image_url"
    prefixo, b64 = imagem["image_url"]["url"].split(",", 1)
    assert prefixo == "data:image/jpeg;base64" and base64.standard_b64decode(b64) == SCREEN.jpeg
    assert "<elementos_da_tela>" in texto["text"]
    # Nada de Anthropic: sem ponto de cache, sem thinking, sem output_config.
    assert "cache_control" not in vistos[0].content.decode() and "thinking" not in corpo
    assert decision.tool == "tap" and decision.args["element_id"] == "e1"
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens) == (1200, 80, 200)
    assert usage.provider == "local" and usage.requested_model == "qwen-vl" and usage.fallback is None
    await p.aclose()


async def test_saida_estruturada_por_json_schema(tmp_path: Path) -> None:
    veredito = {"satisfied": "yes", "evidence": "mensagem na conversa", "delivery_level": "sent"}
    p, vistos = provider(tmp_path, [_resposta(json.dumps(veredito))])
    verdict, _ = await p.verify(VerifyRequest(ctx=ctx(), screen=SCREEN, facts=[]))
    corpo = json.loads(vistos[0].content)
    assert corpo["response_format"]["type"] == "json_schema"
    assert corpo["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
    assert "Responda APENAS com um objeto JSON" not in corpo["messages"][1]["content"][-1]["text"]
    assert verdict.satisfied == "yes" and verdict.evidence == "mensagem na conversa"
    await p.aclose()


async def test_degradacao_declarada_para_json_object(tmp_path: Path) -> None:
    """Modelo declarado sem `json_schema` cai para `json_object` — e o esquema vai no texto. Declarado, não
    descoberto por erro; e a garantia continua sendo o Pydantic, não o servidor (a cerca de código é desfeita)."""
    caps = ModelCaps(structured_output="json_object", thinking=False, effort=False)
    veredito = {"satisfied": "no", "evidence": "nada na tela", "delivery_level": None}
    p, vistos = provider(tmp_path, [_resposta("```json\n" + json.dumps(veredito) + "\n```")], caps=caps)
    verdict, _ = await p.verify(VerifyRequest(ctx=ctx(), screen=SCREEN, facts=[]))
    corpo = json.loads(vistos[0].content)
    assert corpo["response_format"] == {"type": "json_object"}
    assert "Responda APENAS com um objeto JSON" in corpo["messages"][1]["content"][-1]["text"]
    assert verdict.satisfied == "no"
    await p.aclose()


async def test_plano_valida_pelo_pydantic(tmp_path: Path) -> None:
    out = {"summary": "Enviar", "app_id": "qa-messenger", "parameters": [], "success_criteria": ["ok"], "missing": [],
           "steps": [{"key": "Open-App", "title": "Abrir", "goal": "abrir", "depends_on": [], "side_effect": False,
                      "commit_guard": [], "precondition": None, "timeout_s": 60, "max_attempts": 3, "for_each": None,
                      "postcondition": {"kind": "app_foreground", "value": "com.pocqa.messenger",
                                        "description": "aberto", "required_delivery_level": None}}]}
    p, _ = provider(tmp_path, [_resposta(json.dumps(out))])
    plan, usage = await p.plan(PlanRequest(command="envie oi", run_id="r1", instances=[], apps=[APP]))
    assert plan.steps[0].key == "open_app" and plan.planner.provider == "local" and not plan.planner.simulated
    assert usage.role == "plan"
    await p.aclose()

    p2, _ = provider(tmp_path, [_resposta('{"summary": 1}')])
    with pytest.raises(AIError) as e:
        await p2.plan(PlanRequest(command="x", run_id="r1", instances=[], apps=[APP]))
    assert e.value.kind == "invalid_output"
    await p2.aclose()


async def test_modelo_sem_ferramentas_e_erro_de_configuracao(tmp_path: Path) -> None:
    """Um modelo local sem tool calling precisa ser recusado ANTES de agendar, não depois de falhar (#97)."""
    p, _ = provider(tmp_path, [], caps=ModelCaps(tools=False, thinking=False, effort=False))
    with pytest.raises(AIError) as e:
        await p.decide(DecisionRequest(ctx=ctx(), screen=SCREEN))
    assert e.value.kind == "not_configured" and "sem tool calling" in str(e.value)
    await p.aclose()


@pytest.mark.parametrize(("status", "kind", "retryable"), [
    (401, "not_configured", False), (404, "not_configured", False), (402, "billing", False),
    (429, "error", True), (503, "error", True)])
async def test_mapeamento_de_erro(tmp_path: Path, status: int, kind: str, retryable: bool) -> None:
    p, _ = provider(tmp_path, [httpx.Response(status, text="nao")])
    with pytest.raises(AIError) as e:
        await p.verify(VerifyRequest(ctx=ctx(), screen=SCREEN, facts=[]))
    assert e.value.kind == kind and e.value.retryable is retryable
    await p.aclose()


async def test_429_sem_quota_e_falta_de_credito_nao_limite_de_taxa(tmp_path: Path) -> None:
    """A OpenAI manda a falta de crédito como 429 `insufficient_quota` (ADR-051): é cobrança, não se repete."""
    corpo = '{"error": {"message": "You exceeded your current quota", "type": "insufficient_quota"}}'
    p, _ = provider(tmp_path, [httpx.Response(429, text=corpo)])
    with pytest.raises(AIError) as e:
        await p.verify(VerifyRequest(ctx=ctx(), screen=SCREEN, facts=[]))
    assert e.value.kind == "billing" and not e.value.retryable
    await p.aclose()


async def test_truncado_e_recusa(tmp_path: Path) -> None:
    p, _ = provider(tmp_path, [_resposta("{}", finish="length")])
    with pytest.raises(AIError) as e:
        await p.verify(VerifyRequest(ctx=ctx(), screen=SCREEN, facts=[]))
    assert e.value.kind == "invalid_output" and e.value.retryable
    p2, _ = provider(tmp_path, [_resposta("{}", finish="content_filter")])
    with pytest.raises(AIError) as e2:
        await p2.verify(VerifyRequest(ctx=ctx(), screen=SCREEN, facts=[]))
    assert e2.value.kind == "refusal"
    await p.aclose()
    await p2.aclose()


async def test_social_nao_manda_imagem_nem_ferramenta(tmp_path: Path) -> None:
    draft = {"content": "oi, tudo bem?", "rationale": "cumprimento", "refused": False, "memory_candidates": []}
    p, vistos = provider(tmp_path, [_resposta(json.dumps(draft))])
    out, _ = await p.generate_social_response(SocialRequest(profile_id="p1", username="u", kind="dm_reply",
                                                            context_text="", incoming="oi", max_length=5))
    corpo = json.loads(vistos[0].content)
    assert "tools" not in corpo and all(b["type"] == "text" for b in corpo["messages"][1]["content"])
    assert out.content == "oi, t"          # o limite é do app: cortar é mais barato que pedir de novo
    await p.aclose()


async def test_extra_body_chega_ao_corpo_da_chamada(tmp_path: Path) -> None:
    """Item 7.8: `ai.providers.<nome>.extra_body` vai TAL QUAL no corpo — é o que liga `options.num_ctx` do
    Ollama. O provedor não interpreta o dicionário; só repassa, então qualquer chave chega, inclusive uma que
    sobrescreva algo que o `_body` já tinha posto (o dono escreveu por cima de propósito)."""
    cfg = make_config(tmp_path)
    cfg.file.ai.models["qwen-vl"] = ModelCaps(vision=True, tools=True, strict_tools=False,
                                              structured_output="json_schema", thinking=False, effort=False)
    cfg.file.ai.providers["local"] = ProviderCfg(kind="openai", base_url="http://127.0.0.1:11434/v1",
                                                 sends_data_externally=False,
                                                 extra_body={"options": {"num_ctx": 16384}})
    cfg.file.ai.roles["decide"] = RoleCfg(provider="local", model="qwen-vl")
    role = cfg.ai_role("decide")
    assert role.extra_body == {"options": {"num_ctx": 16384}}
    p = OpenAICompatProvider(cfg, role=role)
    p.models = {papel: "qwen-vl" for papel in ("plan", "decide", "verify", "escalation", "social")}
    tool = {"name": "tap", "arguments": json.dumps({"rationale": "enviar", "element_id": "e1", "x": None,
                                                    "y": None, "is_commit_action": False})}
    vistos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        return httpx.Response(200, json=_resposta(tool=tool, finish="tool_calls"))

    p.set_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await p.decide(DecisionRequest(ctx=ctx(), screen=SCREEN))
    corpo = json.loads(vistos[0].content)
    assert corpo["options"] == {"num_ctx": 16384}
    await p.aclose()

    # Sem `extra_body` (o padrão), o corpo não ganha a chave — nada muda para quem já usa vLLM/openai puro.
    p2, vistos2 = provider(tmp_path, [_resposta(tool=tool, finish="tool_calls")])
    await p2.decide(DecisionRequest(ctx=ctx(), screen=SCREEN))
    assert "options" not in json.loads(vistos2[0].content)
    await p2.aclose()


async def test_status_diz_que_os_dados_nao_saem(tmp_path: Path) -> None:
    p, _ = provider(tmp_path, [])
    st = p.status()
    assert st.provider == "local" and not st.sends_data_externally and st.configured
    assert "NÃO saem desta máquina" in st.notice and "127.0.0.1:8001" in st.notice
    await p.aclose()


async def test_parametros_por_modelo_e_chave_pelo_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fase 17: o MODELO declara o nome do teto de saída (`max_completion_tokens` na OpenAI) e o seu `extra_body`,
    que vence o do provedor — o gpt-6-luna só chama ferramenta com `reasoning_effort: none`. E a chave de
    `api_key_env` sai do `EnvSettings`, que lê o `.env`: `os.environ` sozinho não a vê, e a chamada voltaria 401."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cfg = make_config(tmp_path)
    cfg.env = EnvSettings(_env_file=None, GEMINI_API_KEY="chave-de-teste-nao-e-segredo")  # type: ignore[call-arg]
    cfg.file.ai.models["modelo-x"] = ModelCaps(vision=True, tools=True, strict_tools=False,
                                               structured_output="json_object", thinking=False, effort=False,
                                               max_tokens_field="max_completion_tokens",
                                               extra_body={"reasoning_effort": "none"})
    cfg.file.ai.providers["nuvem"] = ProviderCfg(kind="openai", base_url="https://exemplo.invalid/v1",
                                                 api_key_env="GEMINI_API_KEY",
                                                 extra_body={"reasoning_effort": "high", "service_tier": "flex"})
    cfg.file.ai.roles["decide"] = RoleCfg(provider="nuvem", model="modelo-x")
    p = OpenAICompatProvider(cfg, role=cfg.ai_role("decide"))
    tool = {"name": "tap", "arguments": json.dumps({"rationale": "enviar", "element_id": "e1", "x": None,
                                                    "y": None, "is_commit_action": False})}
    vistos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        return httpx.Response(200, json=_resposta(tool=tool, finish="tool_calls", modelo="modelo-x"))

    p.set_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await p.decide(DecisionRequest(ctx=ctx(), screen=SCREEN))
    corpo = json.loads(vistos[0].content)
    assert corpo["max_completion_tokens"] == 4000 and "max_tokens" not in corpo
    assert corpo["reasoning_effort"] == "none" and corpo["service_tier"] == "flex"
    assert vistos[0].headers["authorization"] == "Bearer chave-de-teste-nao-e-segredo"
    await p.aclose()

    # Sem declaração, nada muda para Ollama/vLLM: `max_tokens`, sem campo extra.
    p2, vistos2 = provider(tmp_path, [_resposta(tool=tool, finish="tool_calls")])
    await p2.decide(DecisionRequest(ctx=ctx(), screen=SCREEN))
    corpo2 = json.loads(vistos2[0].content)
    assert corpo2["max_tokens"] == 4000 and "reasoning_effort" not in corpo2
    await p2.aclose()


def test_chave_por_nome_declarado_apelido_e_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    """`EnvSettings.chave`: campo declarado (lido do `.env`), apelido aceito (`GOOGLE_API_KEY`) e, para um nome não
    declarado, `os.environ`. Nome vazio = sem chave, o certo para um endpoint local."""
    monkeypatch.setenv("CHAVE_LOCAL_DE_TESTE", "valor-de-teste")
    env = EnvSettings(_env_file=None, GOOGLE_API_KEY="apelido-de-teste",  # type: ignore[call-arg]
                      DEEPSEEK_API_KEY="declarada-de-teste", VENICE_API_KEY="venice-de-teste")
    assert env.chave("GEMINI_API_KEY") == "apelido-de-teste"
    assert env.chave("GOOGLE_API_KEY") == "apelido-de-teste"
    assert env.chave("DEEPSEEK_API_KEY") == "declarada-de-teste"
    assert env.chave("VENICE_API_KEY") == "venice-de-teste"
    assert "venice-de-teste" not in repr(env)  # SecretStr: a chave não aparece em repr nem em log
    assert env.chave("DASHSCOPE_API_KEY") == ""
    assert env.chave("CHAVE_LOCAL_DE_TESTE") == "valor-de-teste"
    assert env.chave(None) == "" and env.chave("") == ""


# ---------------------------------------------------------------- item 17.8: Flex para trabalho offline
def _flex(tmp: Path, respostas: list[Any], *, max_retries: int = 3,
          timeout_s: float = 900.0) -> tuple[OpenAICompatProvider, list[httpx.Request], Any]:
    """O papel `verify` (o do rejulgamento offline) apontado para uma entrada `openai-flex`; `decide` fica de fora."""
    cfg = make_config(tmp)
    caps = ModelCaps(vision=True, tools=True, strict_tools=False, structured_output="json_object", thinking=False,
                     effort=False)
    cfg.file.ai.models["gpt-teste"] = caps
    cfg.file.ai.providers["openai-flex"] = ProviderCfg(kind="openai", base_url="https://api.openai.com/v1",
                                                       extra_body={"service_tier": "flex"})
    cfg.file.ai.roles["verify"] = RoleCfg(provider="openai-flex", model="gpt-teste", timeout_s=timeout_s,
                                          max_retries=max_retries)
    role = cfg.ai_role("verify")
    p = OpenAICompatProvider(cfg, role=role)
    vistos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        r = respostas.pop(0)
        return r if isinstance(r, httpx.Response) else httpx.Response(200, json=r)

    p.set_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return p, vistos, cfg


_VEREDITO = json.dumps({"satisfied": "yes", "evidence": "ok", "confidence": 0.9})
_REQ = lambda: VerifyRequest(ctx=ctx(), screen=SCREEN, facts=[])  # noqa: E731


@pytest.fixture
def esperas(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """`asyncio.sleep` falso: as esperas do flex (segundos de verdade) não rodam no teste, só ficam registradas."""
    registro: list[float] = []

    async def falso(segundos: float) -> None:
        registro.append(segundos)

    monkeypatch.setattr("app.planning.openai_provider.asyncio.sleep", falso)
    return registro


async def test_flex_manda_service_tier_so_no_papel_apontado(tmp_path: Path, esperas: list[float]) -> None:
    """`service_tier: flex` mora na ENTRADA do provedor e só vale para o papel que aponta para ela: o interativo
    (`decide`, `plan`, ...) não herda nem o campo nem o prazo longo nem as repetições."""
    p, vistos, cfg = _flex(tmp_path, [_resposta(_VEREDITO)])
    await p.verify(_REQ())
    assert json.loads(vistos[0].content)["service_tier"] == "flex"
    assert cfg.ai_role("verify").timeout_s == 900.0            # sem teto de validação: o prazo longo é aceito
    for papel in ("plan", "decide", "escalation", "social"):
        r = cfg.ai_role(papel)
        assert r.extra_body is None and r.max_retries == 0, papel
    assert esperas == []
    await p.aclose()


async def test_flex_429_sem_capacidade_e_repetido_e_respeita_retry_after(tmp_path: Path, esperas: list[float]) -> None:
    sem_capacidade = httpx.Response(429, text='{"error": {"message": "Resource Unavailable"}}',
                                    headers={"retry-after": "17"})
    p, vistos, _ = _flex(tmp_path, [sem_capacidade, httpx.Response(429, text="Resource Unavailable"),
                                    _resposta(_VEREDITO)])
    veredito, _ = await p.verify(_REQ())
    assert veredito.satisfied == "yes" and len(vistos) == 3
    assert esperas == [17.0, 10.0]          # o `Retry-After` manda; sem ele, 5 s dobrando a cada tentativa
    await p.aclose()


async def test_flex_esgota_as_repeticoes_e_o_erro_continua_repetivel(tmp_path: Path, esperas: list[float]) -> None:
    p, vistos, _ = _flex(tmp_path, [httpx.Response(429, text="Resource Unavailable") for _ in range(3)],
                         max_retries=2)
    with pytest.raises(AIError) as e:
        await p.verify(_REQ())
    assert e.value.status == 429 and e.value.retryable and len(vistos) == 3
    assert esperas == [5.0, 10.0]
    await p.aclose()


async def test_espera_cresce_ate_o_teto_de_60s(tmp_path: Path, esperas: list[float]) -> None:
    p, vistos, _ = _flex(tmp_path, [httpx.Response(429, text="x") for _ in range(7)], max_retries=6)
    with pytest.raises(AIError):
        await p.verify(_REQ())
    assert esperas == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0] and len(vistos) == 7
    await p.aclose()


async def test_sem_max_retries_nada_se_repete_e_o_caminho_interativo_segue_igual(tmp_path: Path,
                                                                                 esperas: list[float]) -> None:
    p, vistos, _ = _flex(tmp_path, [httpx.Response(429, text="x")], max_retries=0)
    with pytest.raises(AIError):
        await p.verify(_REQ())
    assert len(vistos) == 1 and esperas == []
    await p.aclose()


async def test_falta_de_credito_e_erro_de_cliente_nao_sao_repetidos(tmp_path: Path, esperas: list[float]) -> None:
    corpo = '{"error": {"type": "insufficient_quota"}}'
    p, vistos, _ = _flex(tmp_path, [httpx.Response(429, text=corpo)])
    with pytest.raises(AIError) as e:
        await p.verify(_REQ())
    assert e.value.kind == "billing" and len(vistos) == 1
    await p.aclose()
    p2, vistos2, _ = _flex(tmp_path, [httpx.Response(400, text="invalido")])
    with pytest.raises(AIError):
        await p2.verify(_REQ())
    assert len(vistos2) == 1 and esperas == []
    await p2.aclose()


async def test_5xx_tambem_e_repetido(tmp_path: Path, esperas: list[float]) -> None:
    p, vistos, _ = _flex(tmp_path, [httpx.Response(503, text="indisponivel"), _resposta(_VEREDITO)])
    veredito, _ = await p.verify(_REQ())
    assert veredito.satisfied == "yes" and len(vistos) == 2 and esperas == [5.0]
    await p.aclose()

async def test_argumentos_ilegiveis_da_ferramenta_viram_erro_sem_o_texto_na_causa(tmp_path: Path) -> None:
    """31.70 (G1): o `.doc` do `JSONDecodeError` é o argumento inteiro; o `AIError` sai sem `__cause__`/`__context__`."""
    segredo = "texto-que-o-ator-ia-digitar-31-70"
    tool = {"name": "type_text", "arguments": '{"text": ["' + segredo + '"]'}   # JSON cortado
    p, _ = provider(tmp_path, [_resposta(tool=tool, finish="tool_calls")])
    with pytest.raises(AIError) as e:
        await p.decide(DecisionRequest(ctx=ctx(), screen=SCREEN))
    assert e.value.kind == "invalid_output" and "Argumentos de ferramenta inválidos" in str(e.value)
    assert segredo not in str(e.value)
    assert e.value.__cause__ is None and e.value.__context__ is None
    await p.aclose()
