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

from app.config import ModelCaps, ProviderCfg, RoleCfg
from app.planning.openai_provider import OpenAICompatProvider, openai_tools
from app.planning.provider import (AIError, AppContext, DecisionRequest, PlanRequest, ScreenInput, SocialRequest,
                                   StepContext, VerifyRequest)

from .conftest import make_config

APP = AppContext("qa-messenger", "QA Messenger", "com.pocqa.messenger", ".MainActivity", "dicas", None)
SCREEN = ScreenInput(width=720, height=1280, jpeg=b"\xff\xd8jpeg", elements=['e1 | Button | text="Enviar"'],
                     package="com.pocqa.messenger", sensitive=False)


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
    """As MESMAS 15 ferramentas do ator, no formato do OpenAI — uma tradução, não um segundo catálogo."""
    frouxas = openai_tools(strict=False)
    assert len(frouxas) == 15 and all(t["type"] == "function" for t in frouxas)
    nomes = {t["function"]["name"] for t in frouxas}
    assert {"tap", "type_text", "step_done", "observe_screen"} <= nomes
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


async def test_status_diz_que_os_dados_nao_saem(tmp_path: Path) -> None:
    p, _ = provider(tmp_path, [])
    st = p.status()
    assert st.provider == "local" and not st.sends_data_externally and st.configured
    assert "NÃO saem desta máquina" in st.notice and "127.0.0.1:8001" in st.notice
    await p.aclose()
