"""Execução de UMA etapa em UM aparelho: observar → decidir → validar → agir → observar → verificar.

Regras centrais:
- o retorno do driver só prova que o comando foi aceito; a etapa só conclui com a pós-condição observada;
- etapa com efeito externo dispara no máximo UMA ação "commit" (em todas as tentativas); se o resultado
  ficar desconhecido, reconcilia pela tela e, persistindo a dúvida, marca `uncertain` — nunca reenvia sozinha;
- pontos seguros entre ações permitem pausar, cancelar ou ceder o aparelho ao usuário.
"""
from __future__ import annotations

import asyncio
import io
import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable

from PIL import Image

from ..automation.driver import DriverError, DriverTimeout, DriverUnavailable
from ..automation.hierarchy import UiTree, parse_hierarchy
from ..automation.tools import (CONTROL_TOOLS, EFFECT_CAPABLE, StepBlocked, StepDone, ToolContext,
                                ToolValidationError, execute_tool, looks_like_commit, resolve_point, validate_call)
from ..config import Config
from ..devices.manager import DeviceManager, DeviceRuntime, Limiter, Observation
from ..models import (DELIVERY_ORDER, ActionStatus, AttemptStatus, DeliveryLevel, StepDTO, StepResult, StepStatus)
from ..planning.provider import (AIError, AIProvider, AppContext, DecisionRequest, ScreenInput, StepContext, Usage,
                                 VerifyRequest)
from ..db import loads
from ..util import norm_text, now_iso
from .repository import Repository

log = logging.getLogger("poc.executor")


class Outcome(StrEnum):
    succeeded = "succeeded"
    retry = "retry"
    failed = "failed"
    waiting_user = "waiting_user"
    uncertain = "uncertain"
    yielded = "yielded"          # cedeu num ponto seguro (pausa / controle manual)
    cancelled = "cancelled"
    device_stuck = "device_stuck"


@dataclass(slots=True)
class StepOutcome:
    outcome: Outcome
    detail: str | None = None
    needs: str | None = None
    delivery_level: DeliveryLevel | None = None


class StepExecutor:
    def __init__(self, cfg: Config, repo: Repository, devices: DeviceManager, provider: AIProvider,
                 ai_limiter: Limiter, settings_getter: Callable[[], Any]):
        self.cfg = cfg
        self.repo = repo
        self.devices = devices
        self.provider = provider
        self.ai_limiter = ai_limiter
        self.get_settings = settings_getter

    # ------------------------------------------------------------------ IA com limites
    async def _ai(self, run_id: str, objective_id: str, coro_factory: Callable[[], Any]) -> Any:
        s = self.get_settings()
        obj = self.repo.objective_row(objective_id)
        run = self.repo.run_row(run_id)
        if obj["ai_calls"] >= s.ai_max_calls_per_objective:
            raise AIError(f"Limite de {s.ai_max_calls_per_objective} chamadas de IA por objetivo atingido.", kind="budget")
        if run and (run["ai_input_tokens"] + run["ai_output_tokens"]) >= s.ai_max_tokens_per_run:
            raise AIError(f"Orçamento de {s.ai_max_tokens_per_run} tokens da execução esgotado.", kind="budget")
        last: AIError | None = None
        for attempt in range(3):
            async with self.ai_limiter:       # limite de chamadas simultâneas ao modelo (≠ aparelhos ativos)
                try:
                    result, usage = await coro_factory()
                    self.repo.add_usage(run_id, objective_id, usage if usage.calls or self.provider.simulated
                                        else Usage(calls=1))
                    return result
                except AIError as exc:
                    self.repo.add_usage(run_id, objective_id, Usage(calls=1))
                    last = exc
                    if not exc.retryable:
                        raise
            await asyncio.sleep(2 * (attempt + 1))
        assert last is not None
        raise last

    def _screen(self, obs: Observation) -> tuple[ScreenInput, float]:
        jpeg, w, h, scale = obs.jpeg, obs.width, obs.height, 1.0
        max_side = self.cfg.file.ai.screenshot_max_side
        if jpeg and max(w, h) > max_side:
            scale = max(w, h) / max_side
            img = Image.open(io.BytesIO(jpeg))
            img = img.resize((round(w / scale), round(h / scale)))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=72)
            jpeg, w, h = buf.getvalue(), img.width, img.height
        return ScreenInput(width=w, height=h, jpeg=jpeg, elements=[e.line() for e in obs.tree.elements],
                           package=obs.package, sensitive=obs.sensitive, tree=obs.tree), scale

    # ------------------------------------------------------------------ etapa
    async def run_step(self, *, run: Any, objective: Any, step: StepDTO, attempt_id: str, rt: DeviceRuntime,
                       app: AppContext, account_label: str | None, remaining: list[str],
                       stop_reason: Callable[[], str | None], resumed_after_manual: bool) -> StepOutcome:
        s = self.get_settings()
        repo = self.repo
        run_id, oid, iid = run["id"], objective["id"], rt.id
        params: dict[str, str] = loads(objective["parameters"], {})
        deadline = time.monotonic() + step.timeout_s
        call_timeout = float(s.driver_call_timeout_s)
        fired, unknown = repo.commit_state(step.id)
        history: list[str] = []
        if fired:
            history.append("(tentativa anterior) a ação com efeito externo desta etapa JÁ foi disparada; "
                           "resultado " + ("desconhecido" if unknown else "registrado") + ".")
        need = step.postcondition.required_delivery_level

        def ctx_for() -> StepContext:
            desc = step.postcondition.description + (f" (nível de entrega exigido: {need.value})" if need else "")
            return StepContext(run_id=run_id, instance_id=iid, objective_summary=run["command"], parameters=params,
                               step_key=step.key, step_title=step.title, step_goal=step.goal,
                               side_effect=step.side_effect, commit_done=fired, commit_guard=step.commit_guard,
                               precondition=step.precondition, postcondition_description=desc, remaining_steps=remaining,
                               app=app, account_label=account_label, required_delivery_level=need.value if need else None,
                               resumed_after_manual_control=resumed_after_manual)

        async def call(fn: Callable[..., Any], *args: Any) -> Any:
            return await rt.executor.run(fn, *args, timeout=call_timeout, label=getattr(fn, "__name__", "driver"))

        async def quick_tree() -> UiTree:
            xml = await rt.executor.run(rt.io.page_source, timeout=call_timeout, label="hierarquia")
            return parse_hierarchy(xml, max_elements=self.cfg.file.ai.max_hierarchy_elements)

        def evidence(obs: Observation | None, note: str, kind: str = "screenshot") -> None:
            if obs is None:
                repo.add_evidence(run_id=run_id, instance_id=iid, step_id=step.id, attempt_id=attempt_id, kind="text", note=note)
            elif obs.sensitive or obs.jpeg is None:
                repo.add_evidence(run_id=run_id, instance_id=iid, step_id=step.id, attempt_id=attempt_id, kind=kind,
                                  note=note + " (tela sensível: captura omitida)", redacted=True)
            else:
                repo.add_evidence(run_id=run_id, instance_id=iid, step_id=step.id, attempt_id=attempt_id, kind=kind,
                                  note=note, data=obs.jpeg)

        def fail_or_retry(detail: str, obs: Observation | None = None) -> StepOutcome:
            evidence(obs, f"Falha: {detail}")
            if step.side_effect and fired:
                return StepOutcome(Outcome.uncertain, detail)
            return StepOutcome(Outcome.retry if step.attempts < step.max_attempts else Outcome.failed, detail)

        if not await self.devices.ensure_automation(rt):
            return StepOutcome(Outcome.waiting_user, f"Sessão de automação indisponível: {rt.automation.detail}",
                               needs="Verifique o Appium/UiAutomator2 (Diagnóstico) e retome este item.")

        last_obs: Observation | None = None
        last_sig: tuple[str, str] | None = None
        same_count = 0
        errors_in_row = 0
        declared: StepDone | None = None
        max_actions = int(s.max_actions_per_step)

        for _ in range(max_actions + 1):
            # ---------- ponto seguro
            why = stop_reason()
            if why:
                return StepOutcome(Outcome.cancelled if why == "cancel" else Outcome.yielded, why)
            if time.monotonic() > deadline:
                return fail_or_retry(f"Tempo da etapa esgotado ({step.timeout_s}s).", last_obs)
            # ---------- observar
            try:
                obs = last_obs = await self.devices.observe(rt, timeout=call_timeout)
            except DriverTimeout as exc:
                return await self._stuck(rt, step, fired, str(exc))
            except DriverError as exc:
                errors_in_row += 1
                self.devices.invalidate_automation(rt, str(exc))
                if errors_in_row >= 3 or not await self.devices.ensure_automation(rt):
                    return fail_or_retry(f"Não foi possível observar a tela: {exc}")
                continue
            if obs.sensitive:
                evidence(obs, "Tela de autenticação detectada")
                return StepOutcome(Outcome.waiting_user, "O app pede autenticação (campo de senha na tela).",
                                   needs="Assuma o controle, faça o login manualmente e devolva o controle à IA.")
            # ---------- decidir
            screen, scale = self._screen(obs)
            try:
                decision = await self._ai(run_id, oid, lambda: self.provider.decide(
                    DecisionRequest(ctx=ctx_for(), screen=screen, history=history[-12:])))
            except AIError as exc:
                if exc.kind == "not_configured":
                    return StepOutcome(Outcome.waiting_user, str(exc), needs="Configure a chave do provedor no .env, "
                                       "reinicie o backend e retome este item.")
                if exc.kind == "budget":
                    return StepOutcome(Outcome.failed if not fired else Outcome.uncertain, str(exc))
                return fail_or_retry(f"IA indisponível: {exc}", obs)
            # ---------- validar
            try:
                args = validate_call(decision.tool, decision.args)
            except ToolValidationError as exc:
                aid = repo.log_intent(attempt_id, decision.tool, _safe_args(decision.args), None, side_effect=False)
                repo.finish_action(aid, ActionStatus.rejected, error=str(exc))
                history.append(f"{decision.tool} REJEITADA: {exc}")
                errors_in_row += 1
                if errors_in_row >= 4:
                    return fail_or_retry("A IA insistiu em chamadas inválidas.", obs)
                continue
            rationale = getattr(args, "rationale", None)
            if isinstance(args, StepDone):
                declared = args
                aid = repo.log_intent(attempt_id, "step_done", args.model_dump(mode="json"), rationale, side_effect=False)
                repo.finish_action(aid, ActionStatus.done, result={"declared": True})
                break
            if isinstance(args, StepBlocked):
                aid = repo.log_intent(attempt_id, "step_blocked", args.model_dump(mode="json"), rationale, side_effect=False)
                repo.finish_action(aid, ActionStatus.done, result={"kind": args.kind})
                evidence(obs, f"Bloqueio relatado pela IA ({args.kind}): {args.reason}")
                repo.decision(f"{iid}: etapa '{step.title}' bloqueada — {args.reason}", run_id=run_id, instance_id=iid,
                              step_id=step.id)
                if step.side_effect and fired:
                    return StepOutcome(Outcome.uncertain, args.reason)
                if args.needs_user or args.kind in ("auth_required", "wrong_account", "missing_info"):
                    return StepOutcome(Outcome.waiting_user, args.reason, needs=_needs_for(args.kind))
                return fail_or_retry(args.reason)

            # ---------- guardas de efeito externo
            tool_ctx = ToolContext(io=rt.io, call=call, tree=obs.tree, width=obs.width, height=obs.height,
                                   image_scale=scale, app_package=app.package, app_activity=app.activity,
                                   allowed_packages=self._allowed_packages(), observe=quick_tree)
            is_commit = False
            if step.side_effect and decision.tool in EFFECT_CAPABLE:
                target = None
                try:
                    if decision.tool in ("tap", "long_press"):
                        target = resolve_point(tool_ctx, getattr(args, "element_id", None), getattr(args, "x", None),
                                               getattr(args, "y", None))[2]
                except DriverError:
                    target = None
                is_commit = bool(getattr(args, "is_commit_action", False)) or looks_like_commit(target)
            if is_commit:
                reject: str | None = None
                if fired:
                    reject = "o efeito externo desta etapa já foi disparado; é proibido repetir. Apenas verifique."
                else:
                    missing = [g for g in step.commit_guard if g and not obs.tree.contains_text(g)]
                    if missing:
                        reject = ("antes do efeito, estes textos precisam estar visíveis e não estão: "
                                  + ", ".join(f'"{m}"' for m in missing))
                if reject:
                    aid = repo.log_intent(attempt_id, decision.tool, args.model_dump(mode="json"), rationale, side_effect=True)
                    repo.finish_action(aid, ActionStatus.rejected, error=reject)
                    history.append(f"{decision.tool} REJEITADA pelo executor: {reject}")
                    errors_in_row += 1
                    if errors_in_row >= 4:
                        return fail_or_retry("Pré-condições do efeito externo não foram atendidas.", obs)
                    continue
                evidence(obs, "Conferência antes do efeito externo: " +
                         (", ".join(step.commit_guard) or "sem textos de guarda") + " visíveis")

            # ---------- detectar ciclo sem progresso
            sig = (obs.tree.signature(), f"{decision.tool}:{_target_key(args)}")
            same_count = same_count + 1 if sig == last_sig else 0
            last_sig = sig
            if same_count >= int(s.no_progress_limit) - 1:
                return fail_or_retry("Ciclo sem progresso: a mesma ação não muda a tela.", obs)

            # ---------- agir (intenção gravada ANTES)
            aid = repo.log_intent(attempt_id, decision.tool, args.model_dump(mode="json"), rationale, side_effect=is_commit)
            if is_commit:
                fired = True           # a partir daqui o efeito pode ter ocorrido, aconteça o que acontecer
            t0 = time.monotonic()
            try:
                out = await execute_tool(tool_ctx, decision.tool, args)
            except DriverError as exc:
                possible = exc.effect_possible
                status = ActionStatus.unknown if (possible and (is_commit or isinstance(exc, DriverTimeout))) else ActionStatus.failed
                repo.finish_action(aid, status, error=str(exc), effect_possible=possible)
                if is_commit and not possible:
                    fired = False      # nada chegou ao aparelho: o efeito NÃO foi disparado
                if is_commit and possible:
                    unknown = True
                    repo.add_effect(oid, f"'{step.title}': ação com efeito disparada, resultado desconhecido ({exc})")
                    repo.note_attempt(attempt_id, error=str(exc),
                                      recovery="Reconciliação pela tela antes de qualquer nova tentativa")
                    if isinstance(exc, DriverTimeout) and not await rt.executor.drain(max_wait_s=120):
                        return await self._stuck(rt, step, True, str(exc))
                    break              # vai direto para a verificação (reconciliação)
                if isinstance(exc, DriverTimeout):
                    if not await rt.executor.drain(max_wait_s=120):
                        return await self._stuck(rt, step, fired, str(exc))
                if isinstance(exc, DriverUnavailable) or "session" in str(exc).lower():
                    self.devices.invalidate_automation(rt, str(exc))
                    await self.devices.ensure_automation(rt)
                history.append(f"{decision.tool}({_brief(args)}) FALHOU: {exc}")
                errors_in_row += 1
                if errors_in_row >= 3:
                    return fail_or_retry(f"Falhas consecutivas do driver: {exc}", obs)
                continue
            errors_in_row = 0
            repo.finish_action(aid, ActionStatus.done, effect_possible=decision.tool in EFFECT_CAPABLE,
                               result={**out.result, "ms": round((time.monotonic() - t0) * 1000)})
            if is_commit:
                repo.add_effect(oid, f"'{step.title}': {decision.tool} executado ({rationale or 'ação com efeito'})")
            history.append(f"{decision.tool}({_brief(args)}) → {_brief_result(out.result)}")
            if rationale:
                repo.decision(f"{iid} · {step.title}: {rationale}", run_id=run_id, instance_id=iid, step_id=step.id)
            await asyncio.sleep(0.6)   # deixa a interface assentar antes da próxima observação
        else:
            return fail_or_retry(f"Limite de {max_actions} ações por etapa atingido sem concluir.", last_obs)

        # ================================================================ verificar a pós-condição
        repo.transition_step(step.id, StepStatus.verifying,
                             message=f"Etapa '{step.title}': verificando a pós-condição"
                             + (" (reconciliação após resultado desconhecido)" if unknown else ""))
        try:
            ok, text, level, obs = await self._verify(rt, step, ctx_for, run_id, oid, deadline, call_timeout,
                                                      patient=bool(need) or fired)
        except DriverTimeout as exc:
            return await self._stuck(rt, step, fired, str(exc))
        except (DriverError, AIError) as exc:
            return fail_or_retry(f"Verificação não pôde ser feita: {exc}", last_obs)
        # o nível de entrega declarado pela IA em step_done não vale como prova; só o observado na verificação
        note = f"Pós-condição {'comprovada' if ok else 'NÃO comprovada'}: {text}"
        evidence(obs, note, kind="verifier" if obs is None else "screenshot")
        if ok:
            if account_label and norm_text(account_label) in norm_text(step.postcondition.value + " " + (text or "")):
                repo.db.execute("UPDATE instances SET account_evidence=?, account_evidence_ts=? WHERE id=?",
                                (text or step.postcondition.value, now_iso(), iid))
            repo.transition_step(step.id, StepStatus.succeeded, detail=text,
                                 result=StepResult(verified=True, evidence_text=text, delivery_level=level),
                                 message=f"Etapa '{step.title}' comprovada: {text}")
            repo.finish_attempt(attempt_id, AttemptStatus.succeeded, observed=text,
                                recovery="Resultado confirmado por reconciliação da tela" if unknown else None)
            return StepOutcome(Outcome.succeeded, text, delivery_level=level)
        if step.side_effect and fired:
            return StepOutcome(Outcome.uncertain, f"O efeito foi disparado, mas não foi possível comprová-lo: {text}",
                               delivery_level=level)
        return StepOutcome(Outcome.retry if step.attempts < step.max_attempts else Outcome.failed,
                           f"Pós-condição não comprovada: {text}")

    # ------------------------------------------------------------------ verificação
    async def _verify(self, rt: DeviceRuntime, step: StepDTO, ctx_for: Callable[[], StepContext], run_id: str,
                      objective_id: str, deadline: float, call_timeout: float, *, patient: bool
                      ) -> tuple[bool, str, DeliveryLevel | None, Observation | None]:
        post = step.postcondition
        need = post.required_delivery_level
        budget = min(max(deadline - time.monotonic(), 8.0), 60.0 if patient else 15.0)
        t_end = time.monotonic() + budget
        polls = 0
        text, level, obs = "sem observação", None, None
        while True:
            polls += 1
            obs = await self.devices.observe(rt, timeout=call_timeout)
            if post.kind == "text_visible":
                ok = obs.tree.contains_text(post.value)
                text = f"texto \"{post.value}\" {'visível' if ok else 'não encontrado'} na tela"
            elif post.kind == "app_foreground":
                ok = obs.package == post.value or (obs.package is None and post.value in obs.tree.packages)
                text = f"app em primeiro plano: {obs.package or 'desconhecido'} (esperado {post.value})"
            elif post.kind == "element_present":
                found = obs.tree.find_selector(post.value)
                ok = bool(found)
                text = f"seletor {post.value}: {len(found)} elemento(s)"
            else:
                screen, _ = self._screen(obs)
                verdict = await self._ai(run_id, objective_id,
                                         lambda: self.provider.verify(VerifyRequest(ctx=ctx_for(), screen=screen)))
                level = verdict.delivery_level
                ok = verdict.satisfied == "yes"
                if ok and need and DELIVERY_ORDER[level or DeliveryLevel.none] < DELIVERY_ORDER[need]:
                    ok = False
                text = verdict.evidence + (f" [nível observado: {level.value}]" if level else "")
            if ok or time.monotonic() >= t_end or (post.kind == "model_judged" and polls >= 5):
                return ok, text, level, obs
            await asyncio.sleep(1.5 if post.kind != "model_judged" else min(2.0 * polls, 8.0))

    async def _stuck(self, rt: DeviceRuntime, step: StepDTO, fired: bool, detail: str) -> StepOutcome:
        """Timeout do driver: o aparelho NÃO é liberado enquanto a chamada anterior puder agir."""
        self.repo.bus.emit("log", f"{rt.id}: chamada ao aparelho excedeu o tempo; aguardando ela terminar antes de liberar.",
                           level="warn", instance_id=rt.id, step_id=step.id)
        if await rt.executor.drain(max_wait_s=180):
            out = Outcome.uncertain if (step.side_effect and fired) else (
                Outcome.retry if step.attempts < step.max_attempts else Outcome.failed)
            return StepOutcome(out, f"Tempo esgotado numa chamada ao aparelho: {detail}")
        return StepOutcome(Outcome.device_stuck, f"Chamada ao aparelho travada: {detail}",
                           needs="Reinicie a instância; o aparelho fica retido até a chamada anterior terminar.")

    def _allowed_packages(self) -> set[str]:
        return {r["package"] for r in self.repo.db.query("SELECT package FROM apps")}


def _needs_for(kind: str) -> str:
    return {
        "auth_required": "Assuma o controle, conclua a autenticação no app e devolva o controle à IA.",
        "wrong_account": "Conecte a conta esperada neste aparelho (ou ajuste o rótulo da conta) e retome o item.",
        "missing_info": "Revise o comando/configuração com a informação que falta e retome o item.",
        "app_incompatible": "O app não expõe uma tela automatizável neste emulador; veja as evidências.",
    }.get(kind, "Verifique o aparelho e decida: retomar, confirmar ou abandonar o item.")


def _safe_args(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {"raw": str(raw)[:300]}


def _target_key(args: Any) -> str:
    d = args.model_dump(exclude={"rationale"})
    return ",".join(f"{k}={v}" for k, v in sorted(d.items()) if v is not None)[:200]


def _brief(args: Any) -> str:
    d = args.model_dump(exclude={"rationale"}, exclude_none=True)
    return ", ".join(f"{k}={str(v)[:60]!r}" for k, v in d.items())


def _brief_result(result: dict[str, Any]) -> str:
    return ", ".join(f"{k}={str(v)[:80]}" for k, v in result.items() if k != "ms") or "ok"
