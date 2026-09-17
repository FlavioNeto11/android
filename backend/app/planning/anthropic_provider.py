"""Provedor real: Anthropic Claude (visão + tool calling estrito + saída estruturada).

Cada decisão é uma requisição independente (sem histórico de mensagens compartilhado): o contexto é
separado por execução e por dispositivo, e o histórico da tentativa vai como texto.
ATENÇÃO: screenshots e textos das telas são enviados à API da Anthropic (exceto telas com campo de senha).
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, ValidationError

from ..automation.tools import strict_schema, tool_definitions
from ..config import Config
from ..models import AiStatus, DeliveryLevel, MissingInfo, Plan, PlannerInfo, PlanStep, Postcondition
from . import prompts
from .provider import AIError, Decision, DecisionRequest, PlanRequest, ScreenInput, Usage, Verdict, VerifyRequest

log = logging.getLogger("poc.ai")
FALLBACK_BETA = "server-side-fallback-2026-07-01"


# ---- formatos de saída estruturada (compatíveis com strict) -------------------
class _ParamOut(BaseModel):
    name: str
    value: str


class _PostOut(BaseModel):
    kind: Literal["text_visible", "app_foreground", "element_present", "model_judged"]
    value: str
    description: str
    required_delivery_level: DeliveryLevel | None


class _StepOut(BaseModel):
    key: str
    title: str
    goal: str
    depends_on: list[str]
    side_effect: bool
    commit_guard: list[str]
    precondition: str | None
    postcondition: _PostOut
    timeout_s: int
    max_attempts: int


class _PlanOut(BaseModel):
    summary: str
    app_id: str | None
    parameters: list[_ParamOut]
    success_criteria: list[str]
    steps: list[_StepOut]
    missing: list[MissingInfo]


def _norm_key(key: str) -> str:
    """O schema estrito não carrega o `pattern` da chave; normaliza 'Open-App' → 'open_app' em vez de rejeitar o plano."""
    k = re.sub(r"[^a-z0-9_]+", "_", key.strip().lower()).strip("_")[:40]
    return k if re.match(r"^[a-z]", k) and len(k) >= 2 else f"step_{k or 'x'}"


class AnthropicProvider:
    name = "anthropic"
    simulated = False

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.model = cfg.env.ai_model
        key = cfg.env.anthropic_api_key.get_secret_value() if cfg.env.anthropic_api_key else ""
        self.configured = bool(key.strip())
        self._use_fallback = cfg.env.ai_refusal_fallback
        self._client = anthropic.AsyncAnthropic(api_key=key, max_retries=2, timeout=180.0) if self.configured else None
        self._tools = tool_definitions()

    def status(self) -> AiStatus:
        if self.configured:
            notice = ("Provedor externo: screenshots e textos das telas são enviados à API da Anthropic para "
                      "planejar, agir e verificar. Telas com campo de senha nunca são enviadas.")
        else:
            notice = ("Chave ANTHROPIC_API_KEY ausente no .env. Gerenciamento e controle manual seguem disponíveis; "
                      "planejar/executar com IA fica pendente até configurar a chave e reiniciar o backend.")
        return AiStatus(provider=self.name, model=self.model, configured=self.configured, simulated=False,
                        sends_data_externally=True, notice=notice, effort=self.cfg.env.ai_effort_actor)

    # ------------------------------------------------------------------ chamada base
    async def _create(self, *, system: str, content: list[dict[str, Any]], effort: str, max_tokens: int,
                      tools: list[dict[str, Any]] | None = None, schema: dict[str, Any] | None = None) -> Any:
        if self._client is None:
            raise AIError("Provedor de IA sem chave configurada (ANTHROPIC_API_KEY).", kind="not_configured")
        output_config: dict[str, Any] = {"effort": effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs: dict[str, Any] = dict(model=self.model, max_tokens=max_tokens, system=system,
                                      messages=[{"role": "user", "content": content}],
                                      thinking={"type": "adaptive"}, output_config=output_config)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
        try:
            if self._use_fallback:
                try:
                    return await self._client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
                except anthropic.BadRequestError as exc:
                    if "fallback" not in str(exc).lower():
                        raise
                    log.warning("Fallback de recusa indisponível nesta conta/modelo; seguindo sem ele: %s", exc)
                    self._use_fallback = False
            return await self._client.messages.create(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise AIError("Chave da Anthropic inválida ou sem permissão.", kind="not_configured") from exc
        except anthropic.PermissionDeniedError as exc:
            raise AIError(f"Acesso negado pelo provedor: {exc.message}", kind="not_configured") from exc
        except anthropic.NotFoundError as exc:
            raise AIError(f"Modelo '{self.model}' não encontrado para esta chave.", kind="not_configured") from exc
        except anthropic.RateLimitError as exc:
            raise AIError("Limite de requisições do provedor atingido.", retryable=True) from exc
        except anthropic.BadRequestError as exc:
            raise AIError(f"Requisição rejeitada pelo provedor: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise AIError(f"Erro {exc.status_code} do provedor.", retryable=exc.status_code >= 500) from exc
        except anthropic.APIConnectionError as exc:
            raise AIError("Falha de rede ao contatar o provedor de IA.", retryable=True) from exc

    @staticmethod
    def _usage(resp: Any) -> Usage:
        u = resp.usage
        cached = (getattr(u, "cache_read_input_tokens", 0) or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0)
        return Usage(calls=1, input_tokens=(u.input_tokens or 0) + cached, output_tokens=u.output_tokens or 0)

    @staticmethod
    def _check_stop(resp: Any) -> None:
        if resp.stop_reason == "refusal":
            raise AIError("O provedor recusou a requisição por política de segurança.", kind="refusal")
        if resp.stop_reason == "max_tokens":
            raise AIError("Resposta do modelo truncada (max_tokens).", retryable=True, kind="invalid_output")

    @staticmethod
    def _screen_content(screen: ScreenInput, text: str) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = []
        if screen.jpeg and not screen.sensitive:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                         "data": base64.standard_b64encode(screen.jpeg).decode()}})
        content.append({"type": "text", "text": text})
        return content

    # ------------------------------------------------------------------ plano
    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]:
        max_steps = self.cfg.file.limits.max_steps_per_objective
        resp = await self._create(system=prompts.PLANNER_SYSTEM,
                                  content=[{"type": "text", "text": prompts.planner_user(req, max_steps)}],
                                  effort=self.cfg.env.ai_effort_planner, max_tokens=12000,
                                  schema=strict_schema(_PlanOut))
        self._check_stop(resp)
        raw = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            out = _PlanOut.model_validate(json.loads(raw))
            app = next((a for a in req.apps if a.id == out.app_id), None)
            plan = Plan(
                summary=out.summary, app_id=app.id if app else None, app_package=app.package if app else None,
                parameters={p.name: p.value for p in out.parameters}, success_criteria=out.success_criteria,
                steps=[PlanStep(key=_norm_key(s.key), title=s.title, goal=s.goal,
                                depends_on=[_norm_key(d) for d in s.depends_on],
                                side_effect=s.side_effect, commit_guard=s.commit_guard, precondition=s.precondition,
                                postcondition=Postcondition(**s.postcondition.model_dump()),
                                timeout_s=max(30, min(s.timeout_s, 600)),
                                max_attempts=1 if s.side_effect else max(1, min(s.max_attempts, 5)))
                       for s in out.steps[:max_steps]],
                missing=out.missing, planner=PlannerInfo(provider=self.name, model=resp.model, simulated=False))
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise AIError(f"Plano inválido devolvido pelo modelo: {exc}", kind="invalid_output") from exc
        if out.app_id and app is None:
            plan.missing.append(MissingInfo(field="app", question=f"O app '{out.app_id}' não está configurado. "
                                                                  "Qual aplicativo configurado deve ser usado?"))
        return plan, self._usage(resp)

    # ------------------------------------------------------------------ decisão
    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        resp = await self._create(system=prompts.ACTOR_SYSTEM,
                                  content=self._screen_content(req.screen, prompts.actor_user_text(req)),
                                  effort=self.cfg.env.ai_effort_actor, max_tokens=4000, tools=self._tools)
        self._check_stop(resp)
        usage = self._usage(resp)
        text = " ".join(b.text for b in resp.content if b.type == "text").strip() or None
        call = next((b for b in resp.content if b.type == "tool_use"), None)
        if call is None:
            raise AIError("O modelo respondeu sem chamar nenhuma ferramenta.", retryable=True, kind="invalid_output")
        args = call.input if isinstance(call.input, dict) else {}
        return Decision(tool=call.name, args=dict(args), raw_text=text), usage

    # ------------------------------------------------------------------ verificação
    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        s = req.screen
        desc = ("tela com campo de senha (imagem omitida)" if s.sensitive
                else f"app em primeiro plano: {s.package or 'desconhecido'}; imagem {s.width}x{s.height}")
        text = prompts.verifier_user_text(req.ctx, desc, s.elements, req.ctx.required_delivery_level)
        resp = await self._create(system=prompts.VERIFIER_SYSTEM, content=self._screen_content(s, text),
                                  effort=self.cfg.env.ai_effort_actor, max_tokens=3000, schema=strict_schema(Verdict))
        self._check_stop(resp)
        raw = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return Verdict.model_validate(json.loads(raw)), self._usage(resp)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise AIError(f"Veredito inválido devolvido pelo modelo: {exc}", kind="invalid_output") from exc
