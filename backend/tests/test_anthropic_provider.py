"""Provedor Anthropic sem rede: formato das requisições (visão, tools estritas, saída estruturada),
interpretação das respostas e tratamento de erros. NÃO substitui a validação com a API real."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.planning.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from app.planning.provider import (AIError, AppContext, DecisionRequest, PlanRequest, ScreenInput, StepContext,
                                   VerifyRequest)

from .conftest import make_config

APP = AppContext("qa-messenger", "QA Messenger", "com.pocqa.messenger", ".MainActivity", "dicas", {"send": "id=send_button"})


def _resp(content: list[Any], stop: str = "end_turn") -> Any:
    return SimpleNamespace(content=content, stop_reason=stop, model="claude-opus-5",
                           usage=SimpleNamespace(input_tokens=1200, output_tokens=80, cache_read_input_tokens=0,
                                                 cache_creation_input_tokens=0))


class FakeMessages:
    def __init__(self, responses: list[Any]):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def provider(tmp: Path, responses: list[Any]) -> tuple[AnthropicProvider, FakeMessages]:
    cfg = make_config(tmp)
    cfg.env.ai_provider = "anthropic"
    p = AnthropicProvider(cfg)
    fake = FakeMessages(responses)
    p.configured = True
    p._client = SimpleNamespace(messages=fake, beta=SimpleNamespace(messages=fake))  # noqa: SLF001
    return p, fake


def ctx(**over: Any) -> StepContext:
    base = dict(run_id="r1", instance_id="android-01", objective_summary="enviar", parameters={"recipient": "QA-001"},
                step_key="send_message", step_title="Enviar", step_goal="Tocar em Enviar", side_effect=True,
                commit_done=False, commit_guard=["QA-001"], precondition=None, postcondition_description="aparece",
                remaining_steps=["Verificar"], app=APP, account_label="qa-user-01", required_delivery_level="sent")
    return StepContext(**{**base, **over})


SCREEN = ScreenInput(width=720, height=1280, jpeg=b"\xff\xd8jpeg", elements=['e1 | Button | text="Enviar" | clickable | [1,2,3,4]'],
                     package="com.pocqa.messenger", sensitive=False)


async def test_sem_chave_informa_pendencia(tmp_path: Path) -> None:
    p = AnthropicProvider(make_config(tmp_path))
    st = p.status()
    assert not st.configured and st.sends_data_externally and "ANTHROPIC_API_KEY" in st.notice
    with pytest.raises(AIError) as e:
        await p.decide(DecisionRequest(ctx=ctx(), screen=SCREEN))
    assert e.value.kind == "not_configured"


async def test_plano_estruturado(tmp_path: Path) -> None:
    out = {"summary": "Enviar mensagem", "app_id": "qa-messenger",
           "parameters": [{"name": "recipient", "value": "QA-001"}, {"name": "message", "value": "Teste {instance_id} {run_id}"}],
           "success_criteria": ["mensagem enviada"], "missing": [],
           # "Open-App": o schema estrito não carrega o pattern da chave — o provedor normaliza em vez de rejeitar
           "steps": [{"key": "Open-App", "title": "Abrir", "goal": "abrir o app", "depends_on": [], "side_effect": False,
                      "commit_guard": [], "precondition": None, "timeout_s": 60, "max_attempts": 3,
                      "postcondition": {"kind": "app_foreground", "value": "com.pocqa.messenger", "description": "app aberto",
                                        "required_delivery_level": None}},
                     {"key": "send_message", "title": "Enviar", "goal": "tocar em enviar", "depends_on": ["Open-App"],
                      "side_effect": True, "commit_guard": ["{recipient}", "{message}"], "precondition": None,
                      "timeout_s": 90, "max_attempts": 3,
                      "postcondition": {"kind": "model_judged", "value": "mensagem na conversa", "description": "aparece",
                                        "required_delivery_level": "sent"}}]}
    p, fake = provider(tmp_path, [_resp([SimpleNamespace(type="thinking", thinking=""),
                                         SimpleNamespace(type="text", text=json.dumps(out))])])
    plan, usage = await p.plan(PlanRequest(command="envie “oi” para QA-001", run_id="r1",
                                          instances=[{"instance_id": "android-01", "account_label": "qa-user-01",
                                                      "app_id": "qa-messenger"}], apps=[APP]))
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5" and call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    assert call["thinking"] == {"type": "adaptive"} and call["output_config"]["effort"] == "medium"
    schema = call["output_config"]["format"]["schema"]
    assert call["output_config"]["format"]["type"] == "json_schema" and schema["additionalProperties"] is False
    assert "envie “oi” para QA-001" in call["messages"][0]["content"][0]["text"]
    assert plan.app_package == "com.pocqa.messenger" and plan.parameters["message"] == "Teste {instance_id} {run_id}"
    assert plan.steps[0].key == "open_app" and plan.steps[1].depends_on == ["open_app"]
    assert plan.steps[1].side_effect and plan.steps[1].max_attempts == 1          # efeito externo: 1 tentativa
    assert plan.steps[1].postcondition.required_delivery_level == "sent" and not plan.planner.simulated
    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (1, 1200, 80)


async def test_decisao_envia_imagem_e_tools_estritas(tmp_path: Path) -> None:
    tool_use = SimpleNamespace(type="tool_use", name="tap", id="t1",
                               input={"rationale": "enviar", "element_id": "e1", "x": None, "y": None, "is_commit_action": True})
    p, fake = provider(tmp_path, [_resp([tool_use], stop="tool_use")])
    decision, _ = await p.decide(DecisionRequest(ctx=ctx(), screen=SCREEN, history=["type_text(...) → ok"]))
    call = fake.calls[0]
    image, text = call["messages"][0]["content"]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/jpeg"
    assert base64.standard_b64decode(image["source"]["data"]) == SCREEN.jpeg
    assert "ETAPA COM EFEITO EXTERNO" in text["text"] and "<elementos_da_tela>" in text["text"]
    assert call["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert all(t["strict"] is True for t in call["tools"]) and "format" not in call["output_config"]
    assert decision.tool == "tap" and decision.args["is_commit_action"] is True


async def test_tela_sensivel_nao_envia_imagem_e_erros_sao_classificados(tmp_path: Path) -> None:
    import anthropic
    import httpx2 as httpx

    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    rate = anthropic.RateLimitError("limite", response=httpx.Response(429, request=req), body=None)
    no_tool = _resp([SimpleNamespace(type="text", text="não sei")])
    refusal = _resp([], stop="refusal")
    verdict = _resp([SimpleNamespace(type="text", text=json.dumps({"satisfied": "yes", "evidence": "Enviada ✓",
                                                                   "delivery_level": "sent"}))])
    p, fake = provider(tmp_path, [no_tool, rate, refusal, verdict])
    sensitive = ScreenInput(width=720, height=1280, jpeg=b"segredo", elements=[], package="x", sensitive=True)
    with pytest.raises(AIError) as e1:
        await p.decide(DecisionRequest(ctx=ctx(), screen=sensitive))
    assert e1.value.kind == "invalid_output" and e1.value.retryable
    assert [b["type"] for b in fake.calls[0]["messages"][0]["content"]] == ["text"]       # sem imagem
    with pytest.raises(AIError) as e2:
        await p.decide(DecisionRequest(ctx=ctx(), screen=SCREEN))
    assert e2.value.retryable
    with pytest.raises(AIError) as e3:
        await p.decide(DecisionRequest(ctx=ctx(), screen=SCREEN))
    assert e3.value.kind == "refusal"
    v, _ = await p.verify(VerifyRequest(ctx=ctx(), screen=SCREEN))
    assert v.satisfied == "yes" and v.delivery_level == "sent"
    assert "Nível de entrega exigido: sent" in fake.calls[-1]["messages"][0]["content"][1]["text"]
