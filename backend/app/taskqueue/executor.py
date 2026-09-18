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
import re
import time
from dataclasses import dataclass, field
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
from ..planning.capabilities import capability_of
from ..planning.provider import (AIError, AIProvider, AppContext, Decision, DecisionRequest, ScreenInput, StepContext,
                                 Usage, VerifyRequest)
from ..db import loads
from ..util import norm_text, now_iso
from .foreach import sanitize_item
from .recipes import RecipeDiverged, RecipeStore, Replayer, distill, unique_selectors
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
    items: list[str] | None = None   # etapa de coleta: itens lidos (o scheduler expande o bloco for_each com eles)
    plan_defect: bool = False        # a pós-condição não é comprovável por tela: repetir ou refazer o MESMO plano não resolve


class StepExecutor:
    def __init__(self, cfg: Config, repo: Repository, devices: DeviceManager, provider: AIProvider,
                 ai_limiter: Limiter, settings_getter: Callable[[], Any]):
        self.cfg = cfg
        self.repo = repo
        self.devices = devices
        self.provider = provider
        self.ai_limiter = ai_limiter
        self.get_settings = settings_getter
        self.recipes = RecipeStore(repo.db)
        # Serviço social (injetado pelo AppState). Sem ele, nada de histórico — e o motor antigo segue igual.
        self.social: Any = None
        self._effects: dict[str, tuple[str, str]] = {}      # step_id → (perfil, interação em aberto)

    # ------------------------------------------------------------------ IA com limites
    async def _ai(self, run_id: str, objective_id: str, coro_factory: Callable[[], Any], *, step_id: str | None = None,
                  role: str = "") -> Any:
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
                                        else Usage(calls=1), step_id=step_id)
                    return result
                except AIError as exc:
                    self.repo.add_usage(run_id, objective_id, Usage(calls=1, role=role, model="(erro)"),
                                        step_id=step_id, ok=False)
                    last = exc
                    if not exc.retryable:
                        raise
            await asyncio.sleep(2 * (attempt + 1))
        assert last is not None
        raise last

    def _want_image(self, obs: Observation, *, judged_step: bool, first: bool, trouble: bool, requested: bool) -> bool:
        """Política `ai.image_policy`. A imagem custa ~1/3 dos tokens novos de cada chamada; a hierarquia quase sempre
        basta. Em `auto` a imagem vai quando a árvore é pobre (WebView/canvas), na 1ª decisão de etapa julgada por
        visão, depois de erro/ciclo, ou quando o próprio modelo pede (observe_screen.need_image)."""
        ai = self.cfg.file.ai
        if obs.jpeg is None or obs.sensitive or ai.image_policy == "never":
            return False
        if ai.image_policy == "always" or requested or trouble or (first and judged_step):
            return True
        informative = sum(1 for e in obs.tree.elements if e.text or e.desc or e.clickable or e.editable)
        return informative < ai.rich_tree_min_elements

    def _image_scale(self, obs: Observation) -> float:
        """Pixels do aparelho por pixel do espaço de coordenadas que o modelo enxerga."""
        return max(1.0, max(obs.width, obs.height) / self.cfg.file.ai.screenshot_max_side)

    def _shadow_compare(self, rr: "_RecipeRun", obs: Observation, decision: Decision) -> None:
        """Modo sombra: a receita diz o que FARIA; só a IA age. A taxa de concordância fica na receita."""
        assert rr.replayer is not None and rr.row is not None
        try:
            would = rr.replayer.next(obs.tree)
        except RecipeDiverged as exc:
            rr.diverged = str(exc)
            self.recipes.shadow(rr.row["id"], False)
            return
        if would is None:
            agreed = decision.tool == "step_done"
        else:
            agreed = would.tool == decision.tool and would.args.get("element_id") == decision.args.get("element_id")
        self.recipes.shadow(rr.row["id"], agreed)
        if not agreed:
            rr.diverged = "a IA escolheu outra ação"

    def _screen(self, obs: Observation, *, with_image: bool = True) -> tuple[ScreenInput, float]:
        jpeg, w, h, scale = (obs.jpeg if with_image else None), obs.width, obs.height, 1.0
        max_side = self.cfg.file.ai.screenshot_max_side
        if max(w, h) > max_side:                      # com ou sem imagem, x,y do modelo vivem no mesmo espaço reduzido
            scale = max(w, h) / max_side
            w, h = round(w / scale), round(h / scale)
            if jpeg:
                buf = io.BytesIO()
                Image.open(io.BytesIO(jpeg)).resize((w, h)).save(buf, "JPEG", quality=72)
                jpeg = buf.getvalue()
        lines = obs.tree.prompt_lines(self.cfg.file.ai.max_hierarchy_elements, scale)
        return ScreenInput(width=w, height=h, jpeg=jpeg, elements=lines,
                           package=obs.package, sensitive=obs.sensitive, tree=obs.tree), scale

    # ------------------------------------------------------------------ etapa
    async def run_step(self, *, run: Any, objective: Any, step: StepDTO, attempt_id: str, rt: DeviceRuntime,
                       app: AppContext, account_label: str | None, remaining: list[str],
                       stop_reason: Callable[[], str | None], resumed_after_manual: bool) -> StepOutcome:
        """Etapa com receitas: procura a receita, executa, e depois contabiliza o replay ou aprende com a IA."""
        mode = self.cfg.file.ai.recipes
        self._effects.pop(step.id, None)
        rr = _RecipeRun(mode=mode)
        fired_at_entry, _ = self.repo.commit_state(step.id)
        if mode != "off" and app.package and not fired_at_entry:
            try:
                rr.app_version = await self.devices.app_version(rt, app.package)
                rr.step_hash = self.repo.step_row(step.id)["template_hash"]
                rr.variables = {**loads(objective["parameters"], {}), "instance_id": rt.id, "run_id": run["id"],
                                "account_label": account_label or "", **step.variables}
                rr.signature = self._installed_signature(rt.id, app.package)
                rr.variant = await self.devices.variant_of(rt)
                rr.row = self.recipes.find(app.package, rr.app_version, rr.step_hash,
                                           signature=rr.signature, variant=rr.variant)
                if rr.row is not None:
                    rr.replayer = self.recipes.replayer(rr.row, rr.variables)
            except Exception as exc:  # noqa: BLE001 - receita é otimização: nunca derruba a etapa
                log.warning("%s: receitas indisponíveis nesta etapa: %s", rt.id, exc)
                rr = _RecipeRun(mode="off")
        outcome = await self._run_step(run=run, objective=objective, step=step, attempt_id=attempt_id, rt=rt, app=app,
                                       account_label=account_label, remaining=remaining, stop_reason=stop_reason,
                                       resumed_after_manual=resumed_after_manual, rr=rr)
        try:
            self._after_step(rr, outcome, run["id"], rt.id, step, attempt_id, app)
        except Exception:  # noqa: BLE001
            log.exception("%s: contabilidade da receita falhou", rt.id)
        self._settle_effect(step, outcome)
        return outcome

    # ------------------------------------------------------------------ histórico social do efeito
    def _open_effect(self, objective: Any, step: StepDTO, rt: DeviceRuntime, cap: Any) -> None:
        """Chamado no instante do commit. Efeito disparado é efeito que conta, mesmo sem resultado observado."""
        if self.social is None or cap is None or not cap.interaction_type or step.id in self._effects:
            return
        profile_id = objective["profile_id"] or None
        if not profile_id:
            return
        try:
            interaction_id = self.social.open_effect(
                profile_id, capability=cap.key, interaction_type=cap.interaction_type, bindings=step.bindings,
                run_id=step.run_id, objective_id=step.objective_id, step_id=step.id, instance_id=rt.id)
            self._effects[step.id] = (profile_id, interaction_id)
        except Exception:  # noqa: BLE001 - histórico nunca derruba a etapa em andamento
            log.exception("%s: não foi possível registrar o efeito no histórico", rt.id)

    def _settle_effect(self, step: StepDTO, outcome: StepOutcome) -> None:
        aberto = self._effects.pop(step.id, None)
        if aberto is None or self.social is None:
            return
        profile_id, interaction_id = aberto
        # `retry` e `yielded` deixam a interação em aberto de propósito: a etapa ainda vai continuar.
        if outcome.outcome in (Outcome.retry, Outcome.yielded):
            self._effects[step.id] = aberto
            return
        try:
            self.social.settle_effect(profile_id, interaction_id, outcome=outcome.outcome.value,
                                      evidence=outcome.detail)
        except Exception:  # noqa: BLE001
            log.exception("não foi possível fechar a interação %s", interaction_id)

    def _after_step(self, rr: "_RecipeRun", outcome: StepOutcome, run_id: str, iid: str, step: StepDTO,
                    attempt_id: str, app: AppContext) -> None:
        # `retry` não é veredito sobre a receita: só o desfecho da etapa (ou a divergência) entra na conta — senão um
        # aparelho com problema próprio poria em quarentena, sozinho, uma receita que funciona nos demais.
        if rr.mode == "off" or outcome.plan_defect or outcome.outcome in (Outcome.yielded, Outcome.cancelled, Outcome.retry):
            return                                     # defeito do plano também não é veredito sobre a receita
        repo = self.repo
        ok = outcome.outcome == Outcome.succeeded
        replayed = rr.mode == "replay" and rr.replayer is not None and rr.replayer.done_actions + int(rr.completed_by_recipe) > 0
        if rr.mode == "replay" and rr.row is not None and (replayed or rr.diverged):
            clean = ok and not rr.diverged
            quarantined = self.recipes.result(rr.row["id"], clean)
            driven = "recipe" if clean else "recipe+ai"
            repo.db.execute("UPDATE steps SET driven_by=? WHERE id=?", (driven, step.id))
            if clean:
                repo.decision(f"{iid} · {step.title}: receita v{rr.row['version']} reproduzida (0 decisões de IA)",
                              run_id=run_id, instance_id=iid, step_id=step.id)
            if quarantined:
                repo.decision(f"{iid} · {step.title}: receita v{rr.row['version']} em quarentena após falhas seguidas; "
                              "a etapa será reaprendida com a IA", run_id=run_id, instance_id=iid, step_id=step.id)
            return
        repo.db.execute("UPDATE steps SET driven_by='ai' WHERE id=?", (step.id,))
        if not ok or rr.row is not None or not (app.package and rr.app_version and rr.step_hash):
            return
        rows = repo.db.query("SELECT * FROM actions WHERE attempt_id=? ORDER BY seq", (attempt_id,))
        actions, why = distill(rows, rr.variables)
        if actions is None:
            log.info("%s: etapa %s não virou receita: %s", iid, step.key, why)
            return
        rid = self.recipes.save(package=app.package, app_version=rr.app_version, step_hash=rr.step_hash,
                                step_key=step.key, actions=actions, learned_from=step.id,
                                signature=rr.signature, variant=rr.variant)
        if rid:
            repo.decision(f"{iid} · {step.title}: receita aprendida ({len(actions)} ação(ões)) — as próximas execuções "
                          "desta etapa dispensam a IA enquanto a tela casar", run_id=run_id, instance_id=iid, step_id=step.id)

    def _installed_signature(self, instance_id: str, package: str) -> str:
        """Assinatura do APK que está NESTE aparelho, quando ele veio de uma release catalogada.

        Versão igual com assinatura diferente não é o mesmo app: a receita aprendida num não vale no outro.
        """
        return self.repo.db.scalar(
            "SELECT r.signature_sha256 FROM device_app_state d JOIN app_releases r ON r.id = d.installed_release_id"
            " WHERE d.instance_id=? AND d.package_name=?", (instance_id, package)) or ""

    async def _run_step(self, *, run: Any, objective: Any, step: StepDTO, attempt_id: str, rt: DeviceRuntime,
                        app: AppContext, account_label: str | None, remaining: list[str],
                        stop_reason: Callable[[], str | None], resumed_after_manual: bool,
                        rr: "_RecipeRun") -> StepOutcome:
        s = self.get_settings()
        repo = self.repo
        run_id, oid, iid = run["id"], objective["id"], rt.id
        params: dict[str, str] = {**loads(objective["parameters"], {}), **step.variables}   # inclui {item} da cópia
        collecting = step.postcondition.kind == "items_collected"
        cap = capability_of(app.package, step.capability)      # None em app sem catálogo: nada muda
        collected: list[str] | None = None
        empty_collects = 0
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
            return parse_hierarchy(xml)

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

        for tries in range(3):                     # logo após ligar/acordar o Android às vezes recusa a 1ª sessão (visto:
            if await self.devices.ensure_automation(rt):   # `adb shell settings …` exit 20) e aceita segundos depois
                break
            if tries == 2 or stop_reason():
                return StepOutcome(Outcome.waiting_user, f"Sessão de automação indisponível: {rt.automation.detail}",
                                   needs="Verifique o Appium/UiAutomator2 (Diagnóstico) e retome este item.")
            await asyncio.sleep(8)

        last_obs: Observation | None = None
        last_sig: tuple[str, str] | None = None
        same_count = 0
        errors_in_row = 0
        declared: StepDone | None = None
        max_actions = int(s.max_actions_per_step)
        ai_cfg = self.cfg.file.ai
        judged_step = step.postcondition.kind == "model_judged" or need is not None
        decisions = 0
        image_requested = False
        # Modelo forte (escalonamento) onde errar custa caro ou o barato já tropeçou: etapa com efeito externo,
        # nova tentativa da mesma etapa, erros seguidos ou ação repetida na mesma tela.
        base_tier = 1 if ((step.side_effect and ai_cfg.strong_model_for_side_effect) or step.attempts > 1) else 0

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
            # ---------- decidir: a receita (se houver e ainda casar) fala primeiro; na divergência a IA assume
            decision: Decision | None = None
            from_recipe = False
            rep = rr.replayer if (rr.mode == "replay" and not rr.diverged and not fired) else None
            if rep is not None:
                try:
                    decision = rep.next(obs.tree)
                    if decision is None:                       # receita esgotada: falta só comprovar
                        if judged_step or self._postcondition_holds(step, obs):
                            rr.completed_by_recipe = True
                            aid = repo.log_intent(attempt_id, "step_done", {"rationale": "[receita] ações reproduzidas"},
                                                  f"[receita v{rep.version}] ações reproduzidas; conferindo a pós-condição",
                                                  side_effect=False, source="recipe")
                            repo.finish_action(aid, ActionStatus.done, result={"declared": True})
                            break
                        rr.settle += 1
                        if rr.settle <= 3:                     # a interface pode estar assentando
                            await asyncio.sleep(1.0)
                            continue
                        raise RecipeDiverged("ações reproduzidas, mas a pós-condição não apareceu")
                    from_recipe = True
                except RecipeDiverged as exc:
                    if rep.done_actions == 0 and not judged_step and self._postcondition_holds(step, obs):
                        rr.completed_by_recipe = True          # o aparelho já estava no estado final desta etapa
                        break
                    rr.diverged = str(exc)
                    decision = None
                    history.append(f"(executor) a receita desta etapa divergiu: {exc}. Continue a partir da tela atual.")
                    repo.decision(f"{iid} · {step.title}: receita divergiu — {exc}; a IA assume esta etapa",
                                  run_id=run_id, instance_id=iid, step_id=step.id)
            scale = self._image_scale(obs)
            if decision is None:
                trouble = errors_in_row >= 1 or same_count >= 1
                tier = 1 if (base_tier or errors_in_row >= 2 or same_count >= 1) else 0
                screen, scale = self._screen(obs, with_image=self._want_image(
                    obs, judged_step=judged_step, first=decisions == 0, trouble=trouble, requested=image_requested))
                image_requested = False
                decisions += 1
                try:
                    decision = await self._ai(run_id, oid, lambda: self.provider.decide(
                        DecisionRequest(ctx=ctx_for(), screen=screen, history=history[-12:], tier=tier)),
                        step_id=step.id, role="decide")
                except AIError as exc:
                    if exc.kind == "not_configured":
                        return StepOutcome(Outcome.waiting_user, str(exc), needs="Configure a chave do provedor no .env, "
                                           "reinicie o backend e retome este item.")
                    if exc.kind == "budget":
                        return StepOutcome(Outcome.failed if not fired else Outcome.uncertain, str(exc))
                    return fail_or_retry(f"IA indisponível: {exc}", obs)
                if rr.mode == "shadow" and rr.replayer is not None and not rr.diverged:
                    self._shadow_compare(rr, obs, decision)     # aprende-se a confiar na receita antes de deixá-la agir
            # ---------- validar
            try:
                args = validate_call(decision.tool, decision.args)
            except ToolValidationError as exc:
                aid = repo.log_intent(attempt_id, decision.tool, _safe_args(decision.args), None, side_effect=False,
                                      source="recipe" if from_recipe else "ai")
                repo.finish_action(aid, ActionStatus.rejected, error=str(exc))
                if from_recipe:
                    rr.diverged = f"ação da receita inválida: {exc}"
                history.append(f"{decision.tool} REJEITADA: {exc}")
                errors_in_row += 1
                if errors_in_row >= 4:
                    return fail_or_retry("A IA insistiu em chamadas inválidas.", obs)
                continue
            rationale = getattr(args, "rationale", None)
            if isinstance(args, StepDone) and collecting:
                aid = repo.log_intent(attempt_id, "step_done", args.model_dump(mode="json"), rationale, side_effect=False)
                repo.finish_action(aid, ActionStatus.rejected, error="etapa de coleta: use collect_list")
                history.append("step_done REJEITADA: esta é uma etapa de COLETA — chame collect_list na lista; "
                               "os itens têm de ser lidos pelo executor.")
                errors_in_row += 1
                if errors_in_row >= 4:
                    return fail_or_retry("A IA não usou collect_list na etapa de coleta.", obs)
                continue
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
                                   allowed_packages=self._allowed_packages(), observe=quick_tree,
                                   collect_max_items=(min(cap.collect_limit, int(s.for_each_max_items))
                                                      if cap and cap.collect_limit else None),
                                   collect_from_top=cap.collect_from_top if cap else True)
            is_commit = False
            if step.side_effect and decision.tool in EFFECT_CAPABLE:
                target = None
                try:
                    if decision.tool in ("tap", "long_press"):
                        target = resolve_point(tool_ctx, getattr(args, "element_id", None), getattr(args, "x", None),
                                               getattr(args, "y", None))[2]
                except DriverError:
                    target = None
                alegado = bool(getattr(args, "is_commit_action", False)) or looks_like_commit(target)
                if step.commit_selector:
                    # Com seletor declarado, o commit é ESTRUTURAL: é este elemento ou não é o efeito da etapa.
                    # O vocabulário de verbos continua valendo só onde não há seletor (planejamento livre).
                    casa = target is not None and any(
                        e.id == target.id for e in obs.tree.find_selector(step.commit_selector))
                    is_commit = casa
                    if alegado and not casa:
                        rejeicao_seletor = (f"o efeito desta etapa é disparado por '{step.commit_selector}'; "
                                            "o elemento escolhido não é ele")
                        aid = repo.log_intent(attempt_id, decision.tool, args.model_dump(mode="json"), rationale,
                                              side_effect=True, source="recipe" if from_recipe else "ai")
                        repo.finish_action(aid, ActionStatus.rejected, error=rejeicao_seletor)
                        if from_recipe:
                            rr.diverged = f"alvo do efeito externo: {rejeicao_seletor}"
                        history.append(f"{decision.tool} REJEITADA pelo executor: {rejeicao_seletor}")
                        errors_in_row += 1
                        if errors_in_row >= 4:
                            return fail_or_retry("O efeito externo foi tentado no elemento errado.", obs)
                        continue
                else:
                    is_commit = alegado
            if is_commit:
                reject: str | None = None
                if fired:
                    reject = "o efeito externo desta etapa já foi disparado; é proibido repetir. Apenas verifique."
                else:
                    missing = [g for g in step.commit_guard if g and not obs.tree.contains_text(g)]
                    # Guarda de linha: numa lista, o texto tem de estar na MESMA faixa do alvo, não em qualquer lugar.
                    fora_da_faixa = [g for g in step.band_guard
                                     if g and g not in missing
                                     and not (target is not None and obs.tree.text_in_band(g, target.bounds))]
                    if missing:
                        reject = ("antes do efeito, estes textos precisam estar visíveis e não estão: "
                                  + ", ".join(f'"{m}"' for m in missing))
                    elif fora_da_faixa:
                        reject = ("o alvo precisa estar na mesma linha de: "
                                  + ", ".join(f'"{m}"' for m in fora_da_faixa)
                                  + " — como está, o efeito pode acertar outro item da lista")
                if reject:
                    aid = repo.log_intent(attempt_id, decision.tool, args.model_dump(mode="json"), rationale, side_effect=True,
                                          source="recipe" if from_recipe else "ai")
                    repo.finish_action(aid, ActionStatus.rejected, error=reject)
                    if from_recipe:            # guarda de commit não atendida: a receita não decide mais nada nesta etapa
                        rr.diverged = f"guarda do efeito externo: {reject}"
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
            aid = repo.log_intent(attempt_id, decision.tool, args.model_dump(mode="json"), rationale, side_effect=is_commit,
                                  source="recipe" if from_recipe else "ai")
            if is_commit:
                fired = True           # a partir daqui o efeito pode ter ocorrido, aconteça o que acontecer
                self._open_effect(objective, step, rt, cap)   # o histórico do perfil registra a INTENÇÃO, não o sucesso
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
                               result={**out.result, "ms": round((time.monotonic() - t0) * 1000)},
                               target=_safe_target(out.target, obs.tree))
            if is_commit:
                repo.add_effect(oid, f"'{step.title}': {decision.tool} executado ({rationale or 'ação com efeito'})")
            history.append(f"{decision.tool}({_brief(args)}) → {_brief_result(out.result)}")
            if rationale:
                repo.decision(f"{iid} · {step.title}: {rationale}", run_id=run_id, instance_id=iid, step_id=step.id)
            if decision.tool == "collect_list":
                got = [t for t in (sanitize_item(x) for x in out.result.get("items", [])) if t]
                if cap and cap.item_key:
                    # O que a lista mostra é uma frase ("fulano said oi"); quem identifica o alvo é a chave dentro
                    # dela. Recortar aqui faz `{item}` — e o `{username}` das etapas do bloco — nascer já limpo.
                    padrao = re.compile(cap.item_key)
                    got = [(m.group(1) if (m := padrao.search(t)) else t) for t in got]
                limit = int(s.for_each_max_items)
                if not collecting:
                    history.append("(executor) collect_list só vale em etapa de coleta; os itens foram ignorados.")
                elif not got:
                    history.append("(executor) nenhum item casou com item_selector dentro da lista; confira o seletor.")
                    empty_collects += 1
                    if empty_collects >= 3:
                        return fail_or_retry("A coleta não encontrou nenhum item na lista.", obs)
                    continue
                elif not (out.result.get("at_end") or out.result.get("capped")):
                    return fail_or_retry("A lista não chegou ao fim dentro do limite de páginas da coleta.", obs)
                elif len(got) > limit:
                    return StepOutcome(Outcome.waiting_user, f"A lista tem {len(got)} itens; o limite é {limit}.",
                                       needs="Aumente “itens por coleta” (for_each_max_items) em Configuração e retome.")
                else:
                    if out.result.get("capped"):
                        evidence(obs, f"Coleta limitada a {out.result.get('limit')} itens por decisão do catálogo: "
                                      "a lista continua depois deste ponto.", kind="text")
                    collected = got
                    break                  # fato medido pelo executor: dispensa verificador
            if getattr(args, "need_image", False):
                image_requested = True
            await asyncio.sleep(0.6)   # deixa a interface assentar antes da próxima observação
            if is_commit:
                break                  # depois do efeito não há mais o que decidir: só comprovar (sem outra chamada)
            if getattr(args, "expect_done", False) and not judged_step:
                # a IA previu que esta ação conclui a etapa: uma conferência determinística poupa o step_done
                try:
                    peek = last_obs = await self.devices.observe(rt, timeout=call_timeout)
                except DriverError:
                    continue
                if not peek.sensitive and self._postcondition_holds(step, peek):
                    break
                history.append("(executor) a pós-condição ainda NÃO vale depois desta ação; continue.")
        else:
            return fail_or_retry(f"Limite de {max_actions} ações por etapa atingido sem concluir.", last_obs)

        if collecting and collected is not None:
            text = f"{len(collected)} item(ns) lidos até o fim da lista: " + ", ".join(collected)[:400]
            evidence(last_obs, f"Coleta comprovada pelo executor: {text}")
            repo.transition_step(step.id, StepStatus.verifying, message=f"Etapa '{step.title}': itens lidos pelo executor")
            repo.transition_step(step.id, StepStatus.succeeded, detail=text,
                                 result=StepResult(verified=True, evidence_text=text, items=collected),
                                 message=f"Etapa '{step.title}' comprovada: {text}")
            repo.finish_attempt(attempt_id, AttemptStatus.succeeded, observed=text)
            return StepOutcome(Outcome.succeeded, text, items=collected)

        # ================================================================ verificar a pós-condição
        repo.transition_step(step.id, StepStatus.verifying,
                             message=f"Etapa '{step.title}': verificando a pós-condição"
                             + (" (reconciliação após resultado desconhecido)" if unknown else ""))
        try:
            ok, text, level, obs, unprovable = await self._verify(rt, step, ctx_for, run_id, oid, deadline, call_timeout,
                                                                  patient=bool(need) or fired, facts=history[-12:])
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
        if unprovable:
            return StepOutcome(Outcome.failed, "Defeito do plano — a pós-condição não é comprovável pela tela (descreve "
                               f"processo/histórico); repetir não resolve: {text}", plan_defect=True)
        return StepOutcome(Outcome.retry if step.attempts < step.max_attempts else Outcome.failed,
                           f"Pós-condição não comprovada: {text}")

    # ------------------------------------------------------------------ verificação
    @staticmethod
    def _deterministic(step: StepDTO, obs: Observation) -> tuple[bool, str]:
        """Parte da pós-condição que dispensa modelo. `model_judged` não tem parte determinística (devolve True)."""
        post = step.postcondition
        if post.kind == "text_visible":
            ok = obs.tree.contains_text(post.value)
            return ok, f"texto \"{post.value}\" {'visível' if ok else 'não encontrado'} na tela"
        if post.kind == "app_foreground":
            ok = obs.package == post.value or (obs.package is None and post.value in obs.tree.packages)
            return ok, f"app em primeiro plano: {obs.package or 'desconhecido'} (esperado {post.value})"
        if post.kind == "element_present":
            found = obs.tree.find_selector(post.value)
            return bool(found), f"seletor {post.value}: {len(found)} elemento(s)"
        if post.kind == "items_collected":         # só o resultado de collect_list comprova (tratado antes de verificar)
            return False, "os itens ainda não foram coletados (collect_list)"
        return True, ""

    def _postcondition_holds(self, step: StepDTO, obs: Observation) -> bool:
        """Conferência barata (sem modelo) usada pelo atalho `expect_done`; nunca vale para etapa julgada por visão."""
        if step.postcondition.kind == "model_judged" or step.postcondition.required_delivery_level is not None:
            return False
        return self._deterministic(step, obs)[0]

    async def _verify(self, rt: DeviceRuntime, step: StepDTO, ctx_for: Callable[[], StepContext], run_id: str,
                      objective_id: str, deadline: float, call_timeout: float, *, patient: bool,
                      facts: list[str] | None = None
                      ) -> tuple[bool, str, DeliveryLevel | None, Observation | None, bool]:
        post = step.postcondition
        need = post.required_delivery_level
        budget = min(max(deadline - time.monotonic(), 8.0), 60.0 if patient else 15.0)
        t_end = time.monotonic() + budget
        max_calls = int(self.cfg.file.ai.verify_max_model_calls)
        judged_polls = 0
        judged_sig: str | None = None
        verdict_text, level, obs = "", None, None
        if patient and (post.kind == "model_judged" or need is not None):
            await asyncio.sleep(1.5)       # o app costuma levar ~1–2 s para sair de "enviando": evita pagar 2 julgamentos
        while True:
            obs = await self.devices.observe(rt, timeout=call_timeout)
            ok, text = self._deterministic(step, obs)
            # Nível de entrega (enviada/entregue/lida) não é comprovável por texto/seletor — o texto já aparece no
            # campo ANTES do envio. Sempre que o plano exigir um nível, o verificador julga a tela também.
            judged = post.kind == "model_judged" or (ok and need is not None)
            if judged:
                sig = obs.tree.signature()
                if judged_polls and sig == judged_sig:
                    ok = False             # mesma tela que já foi julgada insuficiente: espera mudar, sem gastar chamada
                else:
                    # 1º julgamento só pela hierarquia quando ela é rica; os seguintes levam a imagem
                    screen, _ = self._screen(obs, with_image=self._want_image(
                        obs, judged_step=False, first=False, trouble=judged_polls >= 1, requested=False))
                    verdict = await self._ai(run_id, objective_id,
                                             lambda: self.provider.verify(VerifyRequest(ctx=ctx_for(), screen=screen,
                                                                                        facts=list(facts or []))),
                                             step_id=step.id, role="verify")
                    judged_polls += 1
                    judged_sig = sig
                    level = verdict.delivery_level
                    ok = verdict.satisfied == "yes"
                    if ok and need and DELIVERY_ORDER[level or DeliveryLevel.none] < DELIVERY_ORDER[need]:
                        ok = False
                    verdict_text = verdict.evidence + (f" [nível observado: {level.value}]" if level else "")
                    if verdict.satisfied == "unprovable":      # esperar ou rejulgar não muda nada: sai já, sem 2ª chamada
                        return False, "; ".join(t for t in (text, verdict_text) if t), level, obs, True
                text = "; ".join(t for t in (text, verdict_text) if t)
            if ok or time.monotonic() >= t_end or judged_polls >= max_calls:
                return ok, text, level, obs, False
            await asyncio.sleep(1.5)

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


def _safe_target(el: Any, tree: UiTree | None = None) -> dict[str, Any] | None:
    """Alvo resolvido da ação + quais seletores o identificavam SOZINHOS naquela tela (base das receitas).
    Campo de senha nunca é registrado."""
    if el is None or getattr(el, "password", False):
        return None
    d = el.to_dict()
    d.pop("id", None)                      # "e7" só vale naquela observação
    if tree is not None:
        d["unique"] = unique_selectors(tree, el)
    return d


@dataclass
class _RecipeRun:
    """Estado das receitas durante UMA tentativa de etapa."""
    mode: str = "off"
    row: Any = None
    replayer: Replayer | None = None
    variables: dict[str, str] = field(default_factory=dict)
    app_version: str | None = None
    step_hash: str | None = None
    signature: str = ""
    variant: str = ""
    diverged: str | None = None
    completed_by_recipe: bool = False
    settle: int = 0


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
