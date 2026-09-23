"""Provedor real: Anthropic Claude (visão + tool calling estrito + saída estruturada).

Cada decisão é uma requisição independente (sem histórico de mensagens compartilhado): o contexto é
separado por execução e por dispositivo, e o histórico da tentativa vai como texto.
ATENÇÃO: screenshots e textos das telas são enviados à API da Anthropic (exceto telas com campo de senha).
"""
from __future__ import annotations

import base64
import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any

import anthropic

from ..automation.tools import strict_schema, tool_definitions
from ..config import Config
from ..models import AiStatus, Plan, SocialDraftDTO
from . import prompts
from .parsing import (_CapPlanOut, _PlanOut, catalog_plan_from_json, plan_from_json, social_from_json,
                      verdict_from_json)
from .provider import (AIError, Decision, DecisionRequest, PlanRequest, ScreenInput, SocialRequest, Usage,
                       Verdict, VerifyRequest)

if TYPE_CHECKING:
    from ..config import ResolvedRole

log = logging.getLogger("poc.ai")
FALLBACK_BETA = "server-side-fallback-2026-07-01"


# A capacidade de cada modelo é DECLARADA em `ai.models` (achado #97) e lida por `Config.model_caps`. O que sobra
# aqui é a rede de segurança: um 400 que aponte o campo exato desliga o parâmetro para AQUELA instância e diz, no
# log, qual chave do YAML corrigir. Antes isto era a única fonte da verdade — e era reaprendido a cada reinício,
# pagando uma requisição rejeitada por modelo em todo arranque (17 ocorrências em 3 dias nos logs reais).
_TUNABLE = ("thinking", "effort", "strict")

# Só aprende de 400 que aponte o CAMPO. "casamento de palavra solta" era o defeito: um valor errado em
# AI_EFFORT_ACTOR gerava um 400 citando 'effort' e o provedor concluía que o modelo não aceitava esforço,
# passando a rodar no padrão mais caro com um warning. Os efforts agora são `Literal` e nem chegam aqui.
_LEARN_HINTS: dict[str, tuple[str, ...]] = {
    "thinking": ("thinking.type", "thinking:", "`thinking`", "thinking parameter", "adaptive thinking"),
    "effort": ("output_config.effort", "`effort`", "effort parameter"),
    "strict": ("schema is too complex", "tools.0.strict", "strict tool", "`strict`"),
    "fallback": ("fallbacks", "`fallback`", "server-side-fallback"),
}
#: Chave de `ai.models.<modelo>` que o operador deve corrigir para cada parâmetro aprendido.
_CAPS_KEY = {"thinking": "thinking", "effort": "effort", "strict": "strict_tools", "fallback": "(ai.roles.*.refusal_fallback)"}


def _family(model: str) -> str:
    return re.sub(r"-\d{8}$", "", model)


def fallback_info(resp: Any) -> str | None:
    """Houve troca de modelo por RECUSA nesta resposta? Devolve 'refusal' ou None.

    O sinal de quem SERVIU é uma entrada `fallback_message` em `usage.iterations` — o bloco `fallback` no
    `content` marca os pontos de troca, mas uma conversa "grudada" (o roteamento adere por ~1 h) não traz bloco
    nenhum. Por isso os dois são lidos, com `iterations` mandando.
    """
    usage = getattr(resp, "usage", None)
    for entry in (getattr(usage, "iterations", None) or []):
        tipo = getattr(entry, "type", None) or (entry.get("type") if isinstance(entry, dict) else None)
        if tipo == "fallback_message":
            return "refusal"
    for bloco in (getattr(resp, "content", None) or []):
        if (getattr(bloco, "type", None) or (bloco.get("type") if isinstance(bloco, dict) else None)) == "fallback":
            return "refusal"
    return None


# O SDK instalado (anthropic 1.6.0) não tem uma classe dedicada para 402: `_make_status_error` devolve
# BadRequestError (400) tanto para "requisição malformada" quanto para "sem crédito", e só o texto distingue os
# dois (achado #90). `exc.type` cobre o caso em que o provedor manda o tipo tipado; o texto é o reforço para
# quando ele não manda — testado isoladamente porque é o único sinal que sobra.
_BILLING_MARKERS = ("credit balance", "credit_balance", "insufficient_quota", "billing")


def _is_billing_error(exc: "anthropic.APIStatusError") -> bool:
    if getattr(exc, "type", None) == "billing_error":
        return True
    msg = str(getattr(exc, "message", "") or exc).lower()
    return any(marker in msg for marker in _BILLING_MARKERS)


class AnthropicProvider:
    name = "anthropic"
    simulated = False

    def __init__(self, cfg: Config, role: "ResolvedRole | None" = None):
        """`role=None` é o provedor único de sempre (as cinco funções na mesma instância).

        Com `role`, esta instância é a de UMA função dentro do hub: modelo, prazo, novas tentativas e fallback de
        recusa vêm dela. Os outros papéis continuam preenchidos (a instância sabe planejar mesmo sendo a do ator),
        de propósito: é o que mantém o caminho sem `ai.roles` byte a byte igual ao de antes.
        """
        self.cfg = cfg
        env = cfg.env
        self.role = role
        self.models = {papel: cfg.ai_model_for(papel) for papel in ("plan", "decide", "verify", "escalation", "social")}
        if role is not None:
            self.models[role.role] = role.model
        self.model = self.models[role.role] if role is not None else self.models["decide"]
        # Rede de segurança do aprendizado por 400 — a capacidade DECLARADA vem de `cfg.model_caps` (achado #97).
        self._unsupported: dict[str, set[str]] = {}
        key = env.anthropic_api_key.get_secret_value() if env.anthropic_api_key else ""
        if role is not None and role.api_key_env:
            key = os.environ.get(role.api_key_env, "") or key
        self.configured = bool(key.strip())
        self._use_fallback = role.refusal_fallback if role is not None else env.ai_refusal_fallback
        # O DONO das novas tentativas é o `_ai` do executor (achado #96): `max_retries=0` por padrão evita o
        # produto 3 × 3 que existia (SDK repetia 2 vezes e o `_ai` outras 3). O prazo é por função.
        self._timeout = role.timeout_s if role is not None else 180.0
        self._retries = role.max_retries if role is not None else 2
        self._client = (anthropic.AsyncAnthropic(api_key=key, max_retries=self._retries, timeout=self._timeout)
                        if self.configured else None)
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
        roles = (f"plano: {m['plan']} · ação: {m['decide']} · verificação: {m['verify']} · "
                 f"escalonamento: {m['escalation']} · social: {m['social']}")
        return AiStatus(provider=self.name, model=self.model, configured=self.configured, simulated=False,
                        sends_data_externally=True, notice=f"{notice} Modelos por função — {roles}.",
                        effort=self.cfg.env.ai_effort_actor, models=dict(m), recipes=self.cfg.file.ai.recipes,
                        flows=self.cfg.file.ai.flows, image_policy=self.cfg.file.ai.image_policy)

    # ------------------------------------------------------------------ chamada base
    def _kwargs(self, *, model: str, system: str, content: list[dict[str, Any]], effort: str, max_tokens: int,
                tools: bool, schema: dict[str, Any] | None) -> dict[str, Any]:
        """Monta a requisição respeitando a capacidade DECLARADA deste modelo (`ai.models`) e o que ele já recusou."""
        caps = self.cfg.model_caps(model)
        off = self._unsupported.setdefault(model, set())
        if tools and not caps.tools:
            raise AIError(f"O modelo '{model}' está declarado sem tool calling em ai.models — "
                          "aponte esta função para um modelo que tenha.", kind="not_configured")
        if max_tokens and caps.max_output:
            max_tokens = min(max_tokens, caps.max_output)
        # ferramentas + system são idênticos em todas as decisões: o ponto de cache no system reaproveita esse prefixo
        bloco: dict[str, Any] = {"type": "text", "text": system}
        if len(system) // 4 >= caps.min_cache_tokens:      # abaixo do mínimo do modelo o ponto de cache é recusado
            bloco["cache_control"] = {"type": "ephemeral"}
        kwargs: dict[str, Any] = dict(model=model, max_tokens=max_tokens, system=[bloco],
                                      messages=[{"role": "user", "content": content}])
        if caps.thinking and "thinking" not in off:
            kwargs["thinking"] = {"type": "adaptive"}
        output_config: dict[str, Any] = {}
        if caps.effort and "effort" not in off:
            output_config["effort"] = effort
        if schema is not None and caps.structured_output == "json_schema":
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if output_config:
            kwargs["output_config"] = output_config
        if tools:
            estrito = caps.strict_tools and "strict" not in off
            kwargs["tools"] = self._tools if estrito else self._tools_loose
            kwargs["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
        return kwargs

    def _learn(self, model: str, exc: anthropic.BadRequestError) -> bool:
        """Um 400 que aponta o CAMPO exato ensina esta instância a não mandá-lo mais a este modelo.

        Rede de segurança, não fonte da verdade: o lugar de declarar isto é `ai.models.<modelo>`, e o warning diz
        qual chave corrigir para que o próximo arranque não pague a mesma requisição rejeitada. A exigência de
        casar o NOME DO CAMPO (e não a palavra solta) é o que impede um 400 sobre o VALOR de um parâmetro de ser
        lido como "este modelo não aceita o parâmetro".
        """
        msg = str(exc).lower()
        off = self._unsupported.setdefault(model, set())
        learned = [p for p, marcas in _LEARN_HINTS.items() if p not in off and any(m in msg for m in marcas)]
        if learned:
            off.update(learned)
            chaves = ", ".join(f"ai.models.{_family(model)}.{_CAPS_KEY[p]}" for p in learned)
            log.warning("Modelo %s recusou %s (%s); seguindo sem. Declare em %s para não repetir no próximo arranque.",
                        model, ", ".join(learned), exc.message, chaves)
        return bool(learned)

    async def _create(self, *, role: str, model: str, system: str, content: list[dict[str, Any]], effort: str,
                      max_tokens: int, tools: bool = False, schema: dict[str, Any] | None = None, tier: int = 0,
                      with_image: bool = False) -> tuple[Any, Usage]:
        if self._client is None:
            raise AIError("Provedor de IA sem chave configurada (ANTHROPIC_API_KEY).", kind="not_configured",
                          model=model)
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
                raise AIError("O provedor recusou todas as variações da requisição.", model=model)
        except anthropic.AuthenticationError as exc:
            raise AIError("Chave da Anthropic inválida ou sem permissão.", kind="not_configured",
                          status=exc.status_code, model=model) from exc
        except anthropic.PermissionDeniedError as exc:
            raise AIError(f"Acesso negado pelo provedor: {exc.message}", kind="not_configured",
                          status=exc.status_code, model=model) from exc
        except anthropic.NotFoundError as exc:
            raise AIError(f"Modelo '{model}' não encontrado para esta chave.", kind="not_configured",
                          status=exc.status_code, model=model) from exc
        except anthropic.RateLimitError as exc:
            raise AIError("Limite de requisições do provedor atingido.", retryable=True,
                          status=exc.status_code, model=model) from exc
        except anthropic.BadRequestError as exc:
            if _is_billing_error(exc):
                raise AIError("Sem crédito no provedor de IA.", kind="billing", status=exc.status_code,
                              model=model) from exc
            raise AIError(f"Requisição rejeitada pelo provedor: {exc.message}", status=exc.status_code,
                          model=model) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code == 402 or _is_billing_error(exc):
                raise AIError("Sem crédito no provedor de IA.", kind="billing", status=exc.status_code,
                              model=model) from exc
            raise AIError(f"Erro {exc.status_code} do provedor.", retryable=exc.status_code >= 500,
                          status=exc.status_code, model=model) from exc
        except anthropic.APIConnectionError as exc:
            raise AIError("Falha de rede ao contatar o provedor de IA.", retryable=True, model=model) from exc
        u = resp.usage
        read = getattr(u, "cache_read_input_tokens", 0) or 0
        write = getattr(u, "cache_creation_input_tokens", 0) or 0
        respondeu = getattr(resp, "model", None) or model
        trocou = fallback_info(resp)
        usage = Usage(calls=1, input_tokens=(u.input_tokens or 0) + read + write, output_tokens=u.output_tokens or 0,
                      cache_read_tokens=read, cache_write_tokens=write, role=role, model=respondeu,
                      tier=tier, with_image=with_image, ms=round((time.monotonic() - t0) * 1000),
                      requested_model=model, fallback=trocou, provider=self.role.provider if self.role else self.name)
        if trocou:
            # O que o pedido exige e não existia: a troca deixa de ser "uma linha de um modelo estranho no painel".
            log.warning("Fallback de recusa: %s recusou; respondeu %s (cobrado na tarifa de %s).",
                        model, respondeu, respondeu)
        log.info("uso[%s/%s]: entrada=%s cache_lido=%s cache_gravado=%s saida=%s imagem=%s %sms", role, usage.model,
                 u.input_tokens, read, write, u.output_tokens, with_image, usage.ms)
        return resp, usage

    async def _send(self, model: str, kwargs: dict[str, Any]) -> Any:
        assert self._client is not None
        # Prazo e novas tentativas POR FUNÇÃO (achado #96): um único `AsyncAnthropic(timeout=180)` servia as cinco.
        cliente = self._client
        opcoes = getattr(cliente, "with_options", None)
        if opcoes is not None and self.role is not None:
            cliente = opcoes(timeout=self._timeout, max_retries=self._retries)
        if self._use_fallback and "fallback" not in self._unsupported.get(model, ()):
            return await cliente.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        return await cliente.messages.create(**kwargs)

    @staticmethod
    def _check_stop(resp: Any, model: str = "") -> None:
        if resp.stop_reason == "refusal":
            raise AIError("O provedor recusou a requisição por política de segurança.", kind="refusal",
                          model=model or getattr(resp, "model", ""))
        if resp.stop_reason == "max_tokens":
            raise AIError("Resposta do modelo truncada (max_tokens).", retryable=True, kind="invalid_output",
                          model=model or getattr(resp, "model", ""))

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
        if req.catalog is not None:
            return await self._plan_with_catalog(req)
        max_steps = self.cfg.file.limits.max_steps_per_objective
        resp, usage = await self._create(role="plan", model=self.models["plan"], system=prompts.PLANNER_SYSTEM,
                                         content=[{"type": "text", "text": prompts.planner_user(req, max_steps)}],
                                         effort=self.cfg.env.ai_effort_planner, max_tokens=12000,
                                         schema=strict_schema(_PlanOut))
        self._check_stop(resp, self.models["plan"])
        raw = next((b.text for b in resp.content if b.type == "text"), "")
        plan = plan_from_json(raw, req, provider=self.name, model=resp.model, max_steps=max_steps)
        return plan, usage

    async def _plan_with_catalog(self, req: PlanRequest) -> tuple[Plan, Usage]:
        """App com catálogo: o modelo escolhe ações e argumentos; o backend monta as etapas."""
        max_steps = self.cfg.file.limits.max_steps_per_objective
        resp, usage = await self._create(
            role="plan", model=self.models["plan"], system=prompts.PLANNER_CAPABILITY_SYSTEM,
            content=[{"type": "text", "text": prompts.planner_capability_user(req, max_steps)}],
            effort=self.cfg.env.ai_effort_planner, max_tokens=8000, schema=strict_schema(_CapPlanOut))
        self._check_stop(resp, self.models["plan"])
        raw = next((b.text for b in resp.content if b.type == "text"), "")
        plan = catalog_plan_from_json(raw, req, provider=self.name, model=resp.model, max_steps=max_steps)
        return plan, usage

    # ------------------------------------------------------------------ decisão
    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        model = self.models["escalation"] if req.tier > 0 else self.models["decide"]
        with_image = bool(req.screen.jpeg) and not req.screen.sensitive
        resp, usage = await self._create(role="decide", model=model, system=prompts.ACTOR_SYSTEM,
                                         content=self._screen_content(req.screen, prompts.actor_user_text(req)),
                                         effort=self.cfg.env.ai_effort_actor, max_tokens=4000, tools=True,
                                         tier=req.tier, with_image=with_image)
        self._check_stop(resp, model)
        text = " ".join(b.text for b in resp.content if b.type == "text").strip() or None
        call = next((b for b in resp.content if b.type == "tool_use"), None)
        if call is None:
            raise AIError("O modelo respondeu sem chamar nenhuma ferramenta.", retryable=True, kind="invalid_output",
                          model=model)
        args = call.input if isinstance(call.input, dict) else {}
        return Decision(tool=call.name, args=dict(args), raw_text=text), usage

    # ------------------------------------------------------------------ verificação
    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        s = req.screen
        with_image = bool(s.jpeg) and not s.sensitive
        desc = ("tela com campo de senha (imagem omitida)" if s.sensitive
                else f"app em primeiro plano: {s.package or 'desconhecido'}; "
                     + (f"imagem {s.width}x{s.height}" if with_image else "imagem não enviada (julgue pela lista de elementos)"))
        text = prompts.verifier_user_text(req.ctx, desc, s.elements, req.ctx.required_delivery_level, req.facts)
        resp, usage = await self._create(role="verify", model=self.models["verify"], system=prompts.VERIFIER_SYSTEM,
                                         content=self._screen_content(s, text),
                                         effort=self.cfg.env.ai_effort_verifier or self.cfg.env.ai_effort_actor,
                                         max_tokens=3000, schema=strict_schema(Verdict), with_image=with_image)
        self._check_stop(resp, self.models["verify"])
        raw = next((b.text for b in resp.content if b.type == "text"), "")
        return verdict_from_json(raw), usage

    # ------------------------------------------------------------------ geração social
    async def generate_social_response(self, req: SocialRequest) -> tuple[SocialDraftDTO, Usage]:
        """Papel próprio: escreve o texto, não decide enviar. Nenhuma imagem de tela e nenhuma credencial entram aqui."""
        resp, usage = await self._create(role="social", model=self.models["social"], system=prompts.SOCIAL_SYSTEM,
                                         content=[{"type": "text", "text": prompts.social_user_text(req)}],
                                         effort=self.cfg.env.ai_effort_planner, max_tokens=2000,
                                         schema=strict_schema(SocialDraftDTO))
        self._check_stop(resp, self.models["social"])
        raw = next((b.text for b in resp.content if b.type == "text"), "")
        return social_from_json(raw, req.max_length), usage
