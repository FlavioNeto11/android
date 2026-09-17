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
import time
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


# Modelos anteriores à geração 4.6 não aceitam thinking adaptativo nem output_config.effort. É só um ponto de
# partida: qualquer outro 400 que cite um desses parâmetros ensina o provedor em tempo de execução (_create).
_KNOWN_UNSUPPORTED = {"claude-haiku-4-5": ("thinking", "effort")}
_TUNABLE = ("thinking", "effort", "strict")


def _family(model: str) -> str:
    return re.sub(r"-\d{8}$", "", model)


def _norm_key(key: str) -> str:
    """O schema estrito não carrega o `pattern` da chave; normaliza 'Open-App' → 'open_app' em vez de rejeitar o plano."""
    k = re.sub(r"[^a-z0-9_]+", "_", key.strip().lower()).strip("_")[:40]
    return k if re.match(r"^[a-z]", k) and len(k) >= 2 else f"step_{k or 'x'}"


class AnthropicProvider:
    name = "anthropic"
    simulated = False

    def __init__(self, cfg: Config):
        self.cfg = cfg
        env = cfg.env
        base = env.ai_model
        self.models = {"plan": env.ai_model_planner or base, "decide": env.ai_model_actor or base,
                       "verify": env.ai_model_verifier or env.ai_model_actor or base}
        self.models["escalation"] = env.ai_model_escalation or self.models["plan"]
        self.model = self.models["decide"]            # o que o painel mostra como "modelo" (faz ~90 % das chamadas)
        # parâmetros que um modelo recusou (400) deixam de ser enviados a ELE; cada modelo aprende sozinho
        self._unsupported: dict[str, set[str]] = {m: set(_KNOWN_UNSUPPORTED.get(_family(m), ())) for m in self.models.values()}
        key = env.anthropic_api_key.get_secret_value() if env.anthropic_api_key else ""
        self.configured = bool(key.strip())
        self._use_fallback = cfg.env.ai_refusal_fallback
        self._client = anthropic.AsyncAnthropic(api_key=key, max_retries=2, timeout=180.0) if self.configured else None
        self._tools = tool_definitions()
        self._tools_loose = tool_definitions(strict=False)

    def status(self) -> AiStatus:
        if self.configured:
            notice = ("Provedor externo: screenshots e textos das telas são enviados à API da Anthropic para "
                      "planejar, agir e verificar. Telas com campo de senha nunca são enviadas.")
        else:
            notice = ("Chave ANTHROPIC_API_KEY ausente no .env. Gerenciamento e controle manual seguem disponíveis; "
                      "planejar/executar com IA fica pendente até configurar a chave e reiniciar o backend.")
        m = self.models
        roles = (f"plano: {m['plan']} · ação: {m['decide']} · verificação: {m['verify']} · escalonamento: {m['escalation']}")
        return AiStatus(provider=self.name, model=self.model, configured=self.configured, simulated=False,
                        sends_data_externally=True, notice=f"{notice} Modelos por função — {roles}.",
                        effort=self.cfg.env.ai_effort_actor, models=dict(m), recipes=self.cfg.file.ai.recipes,
                        flows=self.cfg.file.ai.flows, image_policy=self.cfg.file.ai.image_policy)

    # ------------------------------------------------------------------ chamada base
    def _kwargs(self, *, model: str, system: str, content: list[dict[str, Any]], effort: str, max_tokens: int,
                tools: bool, schema: dict[str, Any] | None) -> dict[str, Any]:
        """Monta a requisição respeitando o que ESTE modelo aceita (ver _KNOWN_UNSUPPORTED e o aprendizado em _create)."""
        off = self._unsupported.setdefault(model, set(_KNOWN_UNSUPPORTED.get(_family(model), ())))
        # ferramentas + system são idênticos em todas as decisões: o ponto de cache no system reaproveita esse prefixo
        system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        kwargs: dict[str, Any] = dict(model=model, max_tokens=max_tokens, system=system_blocks,
                                      messages=[{"role": "user", "content": content}])
        if "thinking" not in off:
            kwargs["thinking"] = {"type": "adaptive"}
        output_config: dict[str, Any] = {}
        if "effort" not in off:
            output_config["effort"] = effort
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if output_config:
            kwargs["output_config"] = output_config
        if tools:
            kwargs["tools"] = self._tools if "strict" not in off else self._tools_loose
            kwargs["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
        return kwargs

    def _learn(self, model: str, exc: anthropic.BadRequestError) -> bool:
        """Um 400 que cita um parâmetro ajustável ensina o provedor a não mandá-lo mais a este modelo."""
        msg = str(exc).lower()
        hints = {"thinking": ("thinking", "adaptive"), "effort": ("effort",), "strict": ("too complex", "strict"),
                 "fallback": ("fallback",)}
        off = self._unsupported.setdefault(model, set())
        learned = [p for p, words in hints.items() if p not in off and any(w in msg for w in words)]
        if learned:
            off.update(learned)
            log.warning("Modelo %s recusou %s (%s); seguindo sem.", model, ", ".join(learned), exc.message)
        return bool(learned)

    async def _create(self, *, role: str, model: str, system: str, content: list[dict[str, Any]], effort: str,
                      max_tokens: int, tools: bool = False, schema: dict[str, Any] | None = None, tier: int = 0,
                      with_image: bool = False) -> tuple[Any, Usage]:
        if self._client is None:
            raise AIError("Provedor de IA sem chave configurada (ANTHROPIC_API_KEY).", kind="not_configured")
        t0 = time.monotonic()
        try:
            for _ in range(len(_TUNABLE) + 2):
                kwargs = self._kwargs(model=model, system=system, content=content, effort=effort,
                                      max_tokens=max_tokens, tools=tools, schema=schema)
                try:
                    resp = await self._send(model, kwargs)
                    break
                except anthropic.BadRequestError as exc:
                    if not self._learn(model, exc):
                        raise
            else:  # pragma: no cover - só se o provedor recusar tudo em sequência
                raise AIError("O provedor recusou todas as variações da requisição.")
        except anthropic.AuthenticationError as exc:
            raise AIError("Chave da Anthropic inválida ou sem permissão.", kind="not_configured") from exc
        except anthropic.PermissionDeniedError as exc:
            raise AIError(f"Acesso negado pelo provedor: {exc.message}", kind="not_configured") from exc
        except anthropic.NotFoundError as exc:
            raise AIError(f"Modelo '{model}' não encontrado para esta chave.", kind="not_configured") from exc
        except anthropic.RateLimitError as exc:
            raise AIError("Limite de requisições do provedor atingido.", retryable=True) from exc
        except anthropic.BadRequestError as exc:
            raise AIError(f"Requisição rejeitada pelo provedor: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise AIError(f"Erro {exc.status_code} do provedor.", retryable=exc.status_code >= 500) from exc
        except anthropic.APIConnectionError as exc:
            raise AIError("Falha de rede ao contatar o provedor de IA.", retryable=True) from exc
        u = resp.usage
        read = getattr(u, "cache_read_input_tokens", 0) or 0
        write = getattr(u, "cache_creation_input_tokens", 0) or 0
        usage = Usage(calls=1, input_tokens=(u.input_tokens or 0) + read + write, output_tokens=u.output_tokens or 0,
                      cache_read_tokens=read, cache_write_tokens=write, role=role, model=getattr(resp, "model", None) or model,
                      tier=tier, with_image=with_image, ms=round((time.monotonic() - t0) * 1000))
        log.info("uso[%s/%s]: entrada=%s cache_lido=%s cache_gravado=%s saida=%s imagem=%s %sms", role, usage.model,
                 u.input_tokens, read, write, u.output_tokens, with_image, usage.ms)
        return resp, usage

    async def _send(self, model: str, kwargs: dict[str, Any]) -> Any:
        assert self._client is not None
        if self._use_fallback and "fallback" not in self._unsupported.get(model, ()):
            return await self._client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        return await self._client.messages.create(**kwargs)

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
        resp, usage = await self._create(role="plan", model=self.models["plan"], system=prompts.PLANNER_SYSTEM,
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
        return plan, usage

    # ------------------------------------------------------------------ decisão
    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        model = self.models["escalation"] if req.tier > 0 else self.models["decide"]
        with_image = bool(req.screen.jpeg) and not req.screen.sensitive
        resp, usage = await self._create(role="decide", model=model, system=prompts.ACTOR_SYSTEM,
                                         content=self._screen_content(req.screen, prompts.actor_user_text(req)),
                                         effort=self.cfg.env.ai_effort_actor, max_tokens=4000, tools=True,
                                         tier=req.tier, with_image=with_image)
        self._check_stop(resp)
        text = " ".join(b.text for b in resp.content if b.type == "text").strip() or None
        call = next((b for b in resp.content if b.type == "tool_use"), None)
        if call is None:
            raise AIError("O modelo respondeu sem chamar nenhuma ferramenta.", retryable=True, kind="invalid_output")
        args = call.input if isinstance(call.input, dict) else {}
        return Decision(tool=call.name, args=dict(args), raw_text=text), usage

    # ------------------------------------------------------------------ verificação
    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        s = req.screen
        with_image = bool(s.jpeg) and not s.sensitive
        desc = ("tela com campo de senha (imagem omitida)" if s.sensitive
                else f"app em primeiro plano: {s.package or 'desconhecido'}; "
                     + (f"imagem {s.width}x{s.height}" if with_image else "imagem não enviada (julgue pela lista de elementos)"))
        text = prompts.verifier_user_text(req.ctx, desc, s.elements, req.ctx.required_delivery_level)
        resp, usage = await self._create(role="verify", model=self.models["verify"], system=prompts.VERIFIER_SYSTEM,
                                         content=self._screen_content(s, text),
                                         effort=self.cfg.env.ai_effort_verifier or self.cfg.env.ai_effort_actor,
                                         max_tokens=3000, schema=strict_schema(Verdict), with_image=with_image)
        self._check_stop(resp)
        raw = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return Verdict.model_validate(json.loads(raw)), usage
        except (json.JSONDecodeError, ValidationError) as exc:
            raise AIError(f"Veredito inválido devolvido pelo modelo: {exc}", kind="invalid_output") from exc
