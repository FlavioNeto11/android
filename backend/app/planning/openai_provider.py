"""Provedor compatível com OpenAI: qualquer servidor que fale `/v1/chat/completions` (item 7.1, achado #91).

É o que desacopla o projeto de UM fornecedor. O alvo pretendido é um **vLLM local** (`ai.providers.<nome>` com
`kind: openai` e `base_url`), onde a tela NÃO sai da máquina — mas o mesmo código serve qualquer endpoint
compatível, incluindo um gateway de empresa.

O que muda em relação à Anthropic, e por quê:

- **Ferramentas.** O mesmo `automation/tools.py` é traduzido para o formato `tools` do OpenAI
  (`{"type": "function", "function": {name, description, parameters}}`). A validação de verdade continua sendo a
  de sempre (`validate_call`, Pydantic), então a gramática do provedor é aceleração, não garantia.
- **Saída estruturada.** `response_format: json_schema` quando `ai.models.<modelo>.structured_output` declara que
  o modelo tem; `json_object` quando só tem isso; nenhum quando não tem. A degradação é DECLARADA, nunca
  descoberta por erro — e em todos os casos a resposta é revalidada por Pydantic antes de virar plano ou veredito.
- **Imagem.** `image_url` com data URI base64. Modelo declarado sem visão nem chega aqui: o roteador recusa na
  partida (`ai.models.<modelo>.vision: false`).
- **Repetição.** `ai.roles.<papel>.max_retries` repete 429 (menos falta de crédito) e 5xx com espera crescente e
  `Retry-After`, como o SDK da Anthropic. É o que sustenta o `service_tier: flex` (trabalho offline, item 17.8).
- **O que NÃO existe.** Ponto de cache (`cache_control`), `thinking` e `output_config.effort` são da Anthropic; um
  endpoint compatível recusaria a requisição. Por isso `cache_read_tokens` costuma vir de
  `prompt_tokens_details.cached_tokens` quando o servidor reporta, e zero quando não.

Dependência: `httpx`, que já estava no `requirements.txt`. Nenhum cliente novo.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from ..automation.tools import strict_schema, tool_definitions
from ..config import Config
from ..models import AiStatus, PersonaDraft, Plan, SocialDraftDTO
from ..modules.execution.domain.orquestracao import (OrquestracaoInvalida, OrquestracaoOut, PedidoDeOrquestracao,
                                                     orquestracao_from_json, orquestracao_system,
                                                     orquestracao_user)
from ..modules.execution.domain.command_refinement import (CommandRefinement, RefinamentoInvalido, RefineOut,
                                                          RefineRequest, refine_system, refine_user,
                                                          refinement_from_json)
from ..modules.identity.domain.persona_generation import (MAX_TOKENS_DO_RASCUNHO, PERSONA_GENERATION_SYSTEM,
                                                            PersonaGenerationRequest,
                                                            persona_generation_user_text)
from . import prompts
from .curador import (CURADOR_SYSTEM, ParecerBruto, ParecerIlegivel, PedidoDeParecer, curador_user, esquema_do_parecer,
                      parecer_from_json)
from .parsing import (_CapPlanOut, _MultiPlanCurtoOut, _MultiPlanOut, _PlanCurtoOut, _PlanOut, catalog_plan_from_json,
                      plan_from_json, social_from_json, verdict_from_json)
from .provider import (AVISO_TELA_SENSIVEL, AIError, Decision, DecisionRequest, LeituraRequest, PlanRequest, ScreenInput,
                       SocialRequest, Transcricao, TranscricaoWire, Usage, Verdict, VerifyRequest,
                       modelo_do_papel_leitura, persona_draft_from_json, transcricao_from_json)

if TYPE_CHECKING:
    from ..config import ResolvedRole

log = logging.getLogger("poc.ai")

#: Espera entre as repetições de `ai.roles.<papel>.max_retries`: dobra a cada nova tentativa a partir de 5 s e
#: nunca passa de 60 s. A resposta com `Retry-After` manda (até `_ESPERA_MAXIMA_S`). São constantes de código, não
#: configuração: o prazo que importa é o `timeout_s` da função, que cobre as esperas também (ver `_create`).
_ESPERA_INICIAL_S = 5.0
_ESPERA_TETO_S = 60.0
_ESPERA_MAXIMA_S = 300.0


def openai_tools(strict: bool) -> list[dict[str, Any]]:
    """As MESMAS ferramentas do ator, no formato do OpenAI. Uma tradução, não um segundo catálogo."""
    out: list[dict[str, Any]] = []
    for d in tool_definitions(strict=strict):
        fn: dict[str, Any] = {"name": d["name"], "description": d["description"], "parameters": d["input_schema"]}
        if d.get("strict"):
            fn["strict"] = True
        out.append({"type": "function", "function": fn})
    return out


class OpenAICompatProvider:
    simulated = False

    def __init__(self, cfg: Config, role: "ResolvedRole"):
        self.cfg = cfg
        self.role = role
        self.name = role.provider
        self.model = role.model
        self.models = {role.role: role.model}
        base = (role.base_url or "").rstrip("/")
        self._url = f"{base}/chat/completions"
        # A chave vem pelo NOME da variável declarado em `ai.providers.<nome>.api_key_env`; ela nunca entra no YAML.
        # Resolvida por `EnvSettings.chave`, que lê o `.env` — `os.environ` sozinho não o vê (Fase 17). Um vLLM local
        # costuma aceitar qualquer valor — e "sem chave" também é válido.
        self._key = cfg.env.chave(role.api_key_env)
        self.configured = bool(base)
        self._client: httpx.AsyncClient | None = None

    # `httpx.MockTransport` entra por aqui nos testes: o provedor não abre socket nenhum quando já tem cliente.
    def set_client(self, client: httpx.AsyncClient) -> None:
        self._client = client

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.role.timeout_s)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def status(self) -> AiStatus:
        local = not self.role.sends_data_externally
        notice = (f"Provedor compatível com OpenAI em {self.role.endpoint}. "
                  + ("Os dados NÃO saem desta máquina." if local
                     else "ATENÇÃO: screenshots e textos das telas saem desta máquina para esse endpoint.")
                  + " " + AVISO_TELA_SENSIVEL)
        if not self.configured:
            notice = (f"Provedor '{self.name}' sem `base_url` em ai.providers — planejar/executar com IA fica "
                      "pendente até configurar o endpoint e reiniciar o backend.")
        return AiStatus(provider=self.name, model=self.model, configured=self.configured, simulated=False,
                        sends_data_externally=self.role.sends_data_externally, notice=notice,
                        effort=self.role.effort, models=dict(self.models), recipes=self.cfg.file.ai.recipes,
                        flows=self.cfg.file.ai.flows, image_policy=self.cfg.file.ai.image_policy)

    # ------------------------------------------------------------------ chamada base
    def _body(self, *, model: str, system: str, content: list[dict[str, Any]], max_tokens: int, tools: bool,
              schema: dict[str, Any] | None, schema_name: str) -> dict[str, Any]:
        caps = self.cfg.model_caps(model)
        if tools and not caps.tools:
            raise AIError(f"O modelo '{model}' está declarado sem tool calling em ai.models — "
                          "aponte esta função para um modelo que tenha.", kind="not_configured", model=model)
        if caps.max_output:
            max_tokens = min(max_tokens, caps.max_output)
        body: dict[str, Any] = {"model": model, caps.max_tokens_field: max_tokens,
                                "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": content}]}
        if schema is not None:
            if caps.structured_output == "json_schema":
                body["response_format"] = {"type": "json_schema",
                                           "json_schema": {"name": schema_name, "schema": schema, "strict": True}}
            elif caps.structured_output == "json_object":
                # Degradação DECLARADA: o servidor garante JSON, não o formato. Quem garante o formato é o Pydantic
                # do `parsing.py` — e por isso o esquema vai no texto do pedido.
                body["response_format"] = {"type": "json_object"}
        if tools:
            body["tools"] = openai_tools(strict=caps.strict_tools)
            body["tool_choice"] = "auto"
            body["parallel_tool_calls"] = False
        if self.role.extra_body:
            # Item 7.8: repassado TAL QUAL — é o que liga `options: {num_ctx: ...}` do Ollama. O tamanho de
            # contexto NÃO é uma opção de chamada como as demais: fica no servidor (`OLLAMA_CONTEXT_LENGTH` ou o
            # `Modelfile`); sem um dos dois lá, o padrão do servidor trunca o prompt em silêncio, sem erro aqui.
            body.update(self.role.extra_body)
        if caps.extra_body:
            # Depois do provedor: o que é do MODELO vence (Fase 17). Dois modelos no mesmo endpoint podem pedir
            # coisas opostas — o luna exige `reasoning_effort: none` para chamar ferramenta, outro não o aceita.
            body.update(caps.extra_body)
        return body

    async def _create(self, *, role: str, model: str, system: str, content: list[dict[str, Any]], max_tokens: int,
                      tools: bool = False, schema: dict[str, Any] | None = None, schema_name: str = "saida",
                      tier: int = 0, with_image: bool = False) -> tuple[dict[str, Any], Usage]:
        if not self.configured:
            raise AIError(f"Provedor '{self.name}' sem endpoint configurado (ai.providers.{self.name}.base_url).",
                          kind="not_configured", model=model)
        body = self._body(model=model, system=system, content=content, max_tokens=max_tokens, tools=tools,
                          schema=schema, schema_name=schema_name)
        headers = {"content-type": "application/json"}
        if self._key:
            headers["authorization"] = f"Bearer {self._key}"
        t0 = time.monotonic()
        # `max_retries` da função (0 por padrão: o dono das repetições é o `_ai` do executor, como na Anthropic).
        # O prazo `timeout_s` vale POR requisição aqui e, no roteador, para o TOTAL — esperas inclusas —, então as
        # repetições nunca estendem o prazo da função: uma fila de flex sem capacidade acaba em "passou de N s".
        tentativa = 0
        while True:
            try:
                resp = await self._http().post(self._url, json=body, headers=headers)
            except httpx.TimeoutException as exc:
                raise AIError(f"Tempo esgotado ao contatar {self.role.endpoint}.", retryable=True, model=model) from exc
            except httpx.HTTPError as exc:
                raise AIError(f"Falha de rede ao contatar {self.role.endpoint}.", retryable=True, model=model) from exc
            if tentativa >= self.role.max_retries or not self._repetivel(resp):
                break
            espera = self._espera(resp, tentativa)
            tentativa += 1
            log.warning("%s respondeu %s; nova tentativa %s/%s em %.0f s (ai.roles.%s.max_retries).",
                        self.role.endpoint, resp.status_code, tentativa, self.role.max_retries, espera, role)
            await asyncio.sleep(espera)
        self._raise_for_status(resp, model)
        try:
            data = resp.json()
        except ValueError as exc:
            raise AIError(f"Resposta não-JSON de {self.role.endpoint}.", retryable=True,
                          kind="invalid_output", model=model) from exc
        u = data.get("usage") or {}
        cached = int(((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
        usage = Usage(calls=1, input_tokens=int(u.get("prompt_tokens") or 0),
                      output_tokens=int(u.get("completion_tokens") or 0), cache_read_tokens=cached,
                      cache_write_tokens=0, role=role, model=str(data.get("model") or model), tier=tier,
                      with_image=with_image, ms=round((time.monotonic() - t0) * 1000),
                      requested_model=model, fallback=None, provider=self.name)
        log.info("uso[%s/%s@%s]: entrada=%s cache_lido=%s saida=%s imagem=%s %sms", role, usage.model, self.name,
                 u.get("prompt_tokens"), cached, u.get("completion_tokens"), with_image, usage.ms)
        return self._choice(data, model), usage

    @staticmethod
    def _repetivel(resp: httpx.Response) -> bool:
        """429 e 5xx, como o SDK da Anthropic faz com `max_retries`. FALTA DE CRÉDITO (`insufficient_quota`) NÃO:
        repetir só gasta tempo e esconde o saldo zero (ADR-051). O caso de uso é o `service_tier: flex` da OpenAI,
        que devolve 429 "Resource Unavailable" quando não há capacidade ociosa — esperado, não cobrado, e some
        esperando."""
        if resp.status_code == 429:
            return "insufficient_quota" not in (resp.text or "")[:300]
        return resp.status_code >= 500

    @staticmethod
    def _espera(resp: httpx.Response, tentativa: int) -> float:
        try:
            pedida = float(resp.headers.get("retry-after", ""))
        except ValueError:
            pedida = 0.0
        if pedida > 0:
            return min(pedida, _ESPERA_MAXIMA_S)
        return min(_ESPERA_INICIAL_S * (2 ** tentativa), _ESPERA_TETO_S)

    def _raise_for_status(self, resp: httpx.Response, model: str) -> None:
        if resp.status_code < 400:
            return
        texto = (resp.text or "")[:300]
        if resp.status_code in (401, 403):
            raise AIError(f"Credencial recusada por {self.role.endpoint}.", kind="not_configured",
                          status=resp.status_code, model=model)
        if resp.status_code == 404:
            raise AIError(f"Modelo '{model}' ou rota não encontrada em {self.role.endpoint}.", kind="not_configured",
                          status=resp.status_code, model=model)
        if resp.status_code == 402:
            raise AIError(f"Sem crédito em {self.role.endpoint}.", kind="billing", status=resp.status_code, model=model)
        if resp.status_code == 429 and "insufficient_quota" in texto:
            # A OpenAI devolve a FALTA DE CRÉDITO como 429 com `insufficient_quota`, não como 402. Tratar como
            # limite de taxa repetia a chamada à toa e nunca acionava o disjuntor nem o saldo 0 da conta (ADR-051).
            raise AIError(f"Sem crédito em {self.role.endpoint}.", kind="billing", status=resp.status_code, model=model)
        if resp.status_code == 429:
            raise AIError(f"Limite de requisições de {self.role.endpoint} atingido.", retryable=True,
                          status=resp.status_code, model=model)
        if resp.status_code >= 500:
            raise AIError(f"Erro {resp.status_code} de {self.role.endpoint}.", retryable=True,
                          status=resp.status_code, model=model)
        raise AIError(f"Requisição rejeitada por {self.role.endpoint}: {texto}", status=resp.status_code, model=model)

    def _choice(self, data: dict[str, Any], model: str) -> dict[str, Any]:
        escolhas = data.get("choices") or []
        if not escolhas:
            raise AIError("O provedor respondeu sem nenhuma escolha.", retryable=True, kind="invalid_output", model=model)
        escolha = escolhas[0]
        motivo = escolha.get("finish_reason")
        if motivo == "length":
            raise AIError("Resposta do modelo truncada (max_tokens).", retryable=True, kind="invalid_output", model=model)
        if motivo == "content_filter":
            raise AIError("O provedor recusou a requisição por política de segurança.", kind="refusal", model=model)
        return escolha.get("message") or {}

    @staticmethod
    def _screen_content(screen: ScreenInput, text: str) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = []
        if screen.jpeg and not screen.sensitive:
            b64 = base64.standard_b64encode(screen.jpeg).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        content.append({"type": "text", "text": text})
        return content

    def _texto(self, msg: dict[str, Any]) -> str:
        conteudo = msg.get("content")
        if isinstance(conteudo, list):      # alguns servidores devolvem blocos, como a Anthropic
            return "".join(b.get("text", "") for b in conteudo if isinstance(b, dict))
        return str(conteudo or "")

    def _json_hint(self, model: str, schema: dict[str, Any]) -> str:
        """Sem `json_schema` no servidor, o esquema vai no texto. É pedido, não garantia — o Pydantic é a garantia."""
        if self.cfg.model_caps(model).structured_output == "json_schema":
            return ""
        return ("\n\nResponda APENAS com um objeto JSON que valide contra este esquema, sem texto em volta:\n"
                + json.dumps(schema, ensure_ascii=False))

    # ------------------------------------------------------------------ plano
    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]:
        max_steps = self.cfg.file.limits.max_steps_per_objective
        # Três planejadores, na mesma ordem do Anthropic: entre apps (item 24.1) → por catálogo → livre.
        entre_apps = bool(req.catalogs)
        com_catalogo = entre_apps or req.catalog is not None
        curto = self.cfg.file.ai.esquema_do_plano == "curto"          # LT-4b: só a etapa livre muda
        modelo = self.models.get("plan", self.model)
        if entre_apps:
            esquema = strict_schema(_MultiPlanCurtoOut if curto else _MultiPlanOut)
            sistema = prompts.PLANNER_MULTIAPP_SYSTEM_CURTO if curto else prompts.PLANNER_MULTIAPP_SYSTEM
            texto = prompts.planner_multiapp_user(req, max_steps)
        elif com_catalogo:
            esquema, sistema = strict_schema(_CapPlanOut), prompts.PLANNER_CAPABILITY_SYSTEM
            texto = prompts.planner_capability_user(req, max_steps)
        else:
            esquema = strict_schema(_PlanCurtoOut if curto else _PlanOut)
            sistema = prompts.PLANNER_SYSTEM_CURTO if curto else prompts.PLANNER_SYSTEM
            texto = prompts.planner_user(req, max_steps)
        msg, usage = await self._create(
            role="plan", model=modelo, system=sistema,
            content=[{"type": "text", "text": texto + self._json_hint(modelo, esquema)}],
            # a etapa livre é a longa: o teto de 8000 é só do plano que é todo de ações do catálogo
            max_tokens=8000 if com_catalogo and not entre_apps else 12000,
            schema=esquema, schema_name="plano")
        raw = self._texto(msg)
        converte = catalog_plan_from_json if com_catalogo else plan_from_json
        return converte(raw, req, provider=self.name, model=usage.model, max_steps=max_steps, curto=curto), usage

    # ------------------------------------------------------------------ treinamento (item 13.2)
    async def generalize(self, req: Any) -> tuple[dict[str, Any], Usage]:
        from .training import TRAINER_SYSTEM, _TrainOut, proposal_from_json, trainer_user  # noqa: PLC0415
        modelo = self.models.get("plan", self.model)
        esquema = strict_schema(_TrainOut)
        msg, usage = await self._create(role="plan", model=modelo, system=TRAINER_SYSTEM,
                                        content=[{"type": "text", "text": trainer_user(req) + self._json_hint(modelo, esquema)}],
                                        max_tokens=6000, schema=esquema, schema_name="habilidade")
        return proposal_from_json(self._texto(msg), req), usage

    # ------------------------------------------------------------------ assistente do comando (ADR-047)
    async def refine_command(self, req: RefineRequest) -> tuple[CommandRefinement, Usage]:
        modelo = self.models.get("plan", self.model)
        esquema = strict_schema(RefineOut)
        msg, usage = await self._create(role="plan", model=modelo,
                                        system=refine_system(prompts.UNTRUSTED_RULE, prompts.CONDUCT_RULE),
                                        content=[{"type": "text", "text": refine_user(req) + self._json_hint(modelo, esquema)}],
                                        max_tokens=4000, schema=esquema, schema_name="comando_refinado")
        try:
            return refinement_from_json(self._texto(msg)), usage
        except RefinamentoInvalido as exc:
            raise AIError(str(exc), retryable=True, kind="invalid_output", model=modelo) from exc

    # ------------------------------------------------------------------ quem faz (ADR-050)
    async def orchestrate_targets(self, req: PedidoDeOrquestracao) -> tuple[OrquestracaoOut, Usage]:
        modelo = self.models.get("plan", self.model)
        esquema = strict_schema(OrquestracaoOut)
        msg, usage = await self._create(role="plan", model=modelo, system=orquestracao_system(prompts.UNTRUSTED_RULE),
                                        content=[{"type": "text", "text": orquestracao_user(req) + self._json_hint(modelo, esquema)}],
                                        max_tokens=4000, schema=esquema, schema_name="quem_faz")
        try:
            return orquestracao_from_json(self._texto(msg)), usage
        except OrquestracaoInvalida as exc:
            raise AIError(str(exc), retryable=True, kind="invalid_output", model=modelo) from exc

    # ------------------------------------------------------------------ curador do Livro (30.12)
    async def review_knowledge(self, req: PedidoDeParecer) -> tuple[ParecerBruto, Usage]:
        modelo = self.models.get("plan", self.model)
        esquema = esquema_do_parecer(req.opcoes)
        msg, usage = await self._create(role="plan", model=modelo, system=CURADOR_SYSTEM,
                                        content=[{"type": "text", "text": curador_user(req) + self._json_hint(modelo, esquema)}],
                                        max_tokens=3000, schema=esquema, schema_name="parecer")
        try:
            return ParecerBruto(bruto=parecer_from_json(self._texto(msg)), modelo=modelo), usage
        except ParecerIlegivel as exc:
            raise AIError(str(exc), retryable=True, kind="invalid_output", model=modelo) from exc

    # ------------------------------------------------------------------ decisão
    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        modelo = self.models.get("escalation" if req.tier > 0 else "decide", self.model)
        with_image = bool(req.screen.jpeg) and not req.screen.sensitive
        msg, usage = await self._create(role="decide", model=modelo, system=prompts.ACTOR_SYSTEM,
                                        content=self._screen_content(req.screen, prompts.actor_user_text(req)),
                                        max_tokens=4000, tools=True, tier=req.tier, with_image=with_image)
        chamadas = msg.get("tool_calls") or []
        if not chamadas:
            raise AIError("O modelo respondeu sem chamar nenhuma ferramenta.", retryable=True, kind="invalid_output")
        fn = chamadas[0].get("function") or {}
        crus = fn.get("arguments")
        falha: str | None = None
        try:
            args = json.loads(crus) if isinstance(crus, str) else dict(crus or {})
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            falha = str(exc)
        # 31.70 (G1 da leitura do 31.67): levantado FORA do `except`. Com `from exc`, o `JSONDecodeError` ficava em
        # `__cause__`, e o `.doc` dele é o argumento inteiro que o ator escolheu (o texto a digitar). O `str` diz só
        # linha e coluna.
        if falha is not None:
            raise AIError(f"Argumentos de ferramenta inválidos: {falha}", retryable=True, kind="invalid_output")
        if not isinstance(args, dict):
            raise AIError("Argumentos de ferramenta não são um objeto.", retryable=True, kind="invalid_output")
        return Decision(tool=str(fn.get("name") or ""), args=args, raw_text=self._texto(msg) or None), usage

    # ------------------------------------------------------------------ verificação
    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        s = req.screen
        escalado = bool(getattr(req, "escalate", False))
        modelo = self.models.get("escalation" if escalado else "verify", self.model)
        with_image = bool(s.jpeg) and not s.sensitive
        desc = ("tela sensível (imagem omitida)" if s.sensitive
                else f"app em primeiro plano: {s.package or 'desconhecido'}; "
                     + (f"imagem {s.width}x{s.height}" if with_image
                        else "imagem não enviada (julgue pela lista de elementos)"))
        esquema = strict_schema(Verdict)
        texto = prompts.verifier_user_text(req.ctx, desc, s.elements, req.ctx.required_delivery_level,
                                           req.facts, req.dicas_da_tela) + self._json_hint(modelo, esquema)
        msg, usage = await self._create(role="verify", model=modelo, system=prompts.VERIFIER_SYSTEM,
                                        content=self._screen_content(s, texto), max_tokens=3000,
                                        schema=esquema, schema_name="veredito", tier=int(escalado),
                                        with_image=with_image)     # RA-10: rejulgamento escalado é tier 1
        return verdict_from_json(self._texto(msg)), usage

    # ------------------------------------------------------------------ leitura visual (item 12.5)
    async def transcribe(self, req: LeituraRequest) -> tuple[Transcricao, Usage]:
        """Papel `leitura` (de outra família que o ator, por escolha do dono): transcreve o RECORTE às cegas — só a
        imagem e os nomes das saídas pedidas, nunca o valor do ator, o comando ou a conta. OpenAI e Gemini (pelo endpoint
        compatível) entram por aqui; o recorte é o que sai desta máquina para o provedor escolhido em `ai.roles.leitura`."""
        modelo = modelo_do_papel_leitura(self.cfg, self.role)    # nunca o do ator: sem o papel `leitura`, recusa
        b64 = base64.standard_b64encode(req.recorte).decode()
        esquema = strict_schema(TranscricaoWire)
        content = [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                   {"type": "text", "text": prompts.leitura_user_text(req.saidas) + self._json_hint(modelo, esquema)}]
        msg, usage = await self._create(role="leitura", model=modelo, system=prompts.LEITURA_SYSTEM, content=content,
                                        max_tokens=1200, schema=esquema, schema_name="transcricao", with_image=True)
        return transcricao_from_json(self._texto(msg), list(req.saidas)), usage

    # ------------------------------------------------------------------ geração social
    async def generate_social_response(self, req: SocialRequest) -> tuple[SocialDraftDTO, Usage]:
        modelo = self.models.get("social", self.model)
        esquema = strict_schema(SocialDraftDTO)
        texto = prompts.social_user_text(req) + self._json_hint(modelo, esquema)
        msg, usage = await self._create(role="social", model=modelo, system=prompts.SOCIAL_SYSTEM,
                                        content=[{"type": "text", "text": texto}], max_tokens=2000,
                                        schema=esquema, schema_name="resposta_social")
        return social_from_json(self._texto(msg), req.max_length), usage

    # ------------------------------------------------------------------ geração de persona
    async def generate_persona(self, req: PersonaGenerationRequest) -> tuple[PersonaDraft, Usage]:
        modelo = self.models.get("persona", self.model)
        esquema = strict_schema(PersonaDraft)
        texto = persona_generation_user_text(req) + self._json_hint(modelo, esquema)
        msg, usage = await self._create(role="persona", model=modelo, system=PERSONA_GENERATION_SYSTEM,
                                        content=[{"type": "text", "text": texto}],
                                        max_tokens=MAX_TOKENS_DO_RASCUNHO, schema=esquema, schema_name="persona")
        return persona_draft_from_json(self._texto(msg)), usage
