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
from typing import Any, Callable, Sequence

from PIL import Image

from ..automation.driver import DriverError, DriverTimeout, DriverUnavailable
from ..automation.hierarchy import MOTIVO_DESAFIO, MOTIVO_SENHA, UiTree
from ..automation.tools import (CONTROL_TOOLS, EFFECT_CAPABLE, StepBlocked, StepDone, ToolContext,
                                ToolValidationError, execute_tool, looks_like_commit, resolve_point, validate_call)
from ..config import Config
from ..devices.manager import DeviceManager, DeviceRuntime, Limiter, Observation
from ..models import (DELIVERY_ORDER, ActionStatus, AttemptStatus, DeliveryLevel, StepDTO, StepResult, StepStatus)
from ..planning.capabilities import capability_of
from ..planning.provider import (AIError, AIProvider, AppContext, Decision, DecisionRequest, ScreenInput, StepContext,
                                 Usage, VerifyRequest)
from ..db import loads
from ..social.approvals import ler_rascunho
from ..util import norm_text, now_iso
from .foreach import sanitize_item
from .proofs import local_proof_holds, variantes_de_arroba
from .recipes import RecipeDiverged, RecipeStore, Replayer, distill, unique_selectors
from .repository import Repository

log = logging.getLogger("poc.executor")


def pede_intervencao_humana(tree: UiTree) -> bool:
    """A tela sensível exige uma PESSOA, ou só exige que a imagem não saia daqui?

    Eram a mesma pergunta enquanto `sensitive` significava apenas "há campo de senha" (achado #127). Deixaram de
    ser: uma tela declarada em `config.yaml: sensitive_screens`, ou qualquer tela da VM-loja, tem a imagem
    omitida — mas parar a etapa nela seria inventar uma falha de autenticação e marcar o perfil como
    `auth_required` toda vez que a IA passasse por ali.

    Função nomeada, e não uma condição embutida no laço, porque é a regra que separa as duas coisas: escondida no
    meio de 900 linhas ela voltaria a ser "sensível = pare", que é de onde ela veio.
    """
    return tree.sensitive and tree.sensitive_reason in (MOTIVO_SENHA, MOTIVO_DESAFIO)


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
    # Item 7.3: motivo ESTRUTURADO do bloqueio, quando o outcome é `waiting_user` por causa da IA (not_configured |
    # billing | refusal) — o scheduler grava isto em `objectives.blocked_kind='ai'` para a interface distinguir
    # "a IA está travando este item" de política/limite/aprovação, em vez de só um texto livre.
    ai_blocked: bool = False


# kinds de AIError que são problema de CONTA (crédito ou credencial), não da etapa: nenhuma tentativa nova
# resolveria, então acionam o disjuntor em vez de reenviar. `_ai` é o ponto único que os classifica assim.
ACCOUNT_ERROR_KINDS = ("billing", "not_configured")

# mensagem mostrada ao usuário — nunca o dicionário cru do provedor (ver achado #90).
_ACCOUNT_ERROR_MESSAGE = {
    "billing": "Sem crédito no provedor de IA — recarregue e retome.",
    "not_configured": "Credencial do provedor de IA inválida ou ausente — corrija e retome.",
}


async def _com_prazo(coro: Any, deadline: float | None, role: str) -> Any:
    """A chamada nunca passa do prazo da ETAPA (achado #96).

    O provedor já tem o seu próprio prazo por função; este aqui é o teto de cima, o que impede uma chamada de
    sobreviver à etapa que a pediu. Cancelar a corrotina solta a vaga de IA e o aparelho na hora.
    """
    if deadline is None:
        return await coro
    restante = deadline - time.monotonic()
    try:
        return await asyncio.wait_for(coro, timeout=max(0.1, restante))
    except asyncio.TimeoutError as exc:
        raise AIError(f"A chamada de IA ({role or 'modelo'}) passou do prazo restante da etapa "
                      f"({max(0.0, restante):.0f} s).", retryable=False) from exc


@dataclass(slots=True)
class AiBreakerTrip:
    """Última vez que o disjuntor de conta de IA disparou: `/api/health` e a aba IA leem isto."""
    kind: str            # billing | not_configured
    message: str
    run_id: str
    at: str               # ISO 8601


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
        self.approvals: Any = None                          # idem: só para ligar a aprovação ao efeito que ela liberou
        #: Login ou desafio apareceu NO MEIO da execução. O estado de sessão é cache do que se observou, e o
        #: executor é quem está olhando a tela naquele instante — antes ele devolvia `waiting_user` e deixava o
        #: perfil dizendo "Conectado". Injetado pelo AppState: (instance_id, kind, detail).
        self.on_auth_needed: Callable[[str, str, str], None] | None = None
        self._effects: dict[str, tuple[str, str]] = {}      # step_id → (perfil, interação em aberto)
        # Disjuntor de conta de IA (achado #90): por execução, a PRIMEIRA falha de cobrança/credencial represa
        # as etapas seguintes sem gastar tentativa — os aparelhos seguintes nem chegam a chamar o provedor.
        self._tripped_runs: dict[str, AiBreakerTrip] = {}
        self.ai_breaker: AiBreakerTrip | None = None        # a mais recente, de qualquer execução — para a saúde

    def account_error_message(self, kind: str) -> str:
        return _ACCOUNT_ERROR_MESSAGE.get(kind, "Provedor de IA indisponível para esta conta.")

    def _trip_ai_breaker(self, run_id: str, exc: "AIError") -> None:
        """Primeira falha de conta nesta execução: registra o disjuntor e pede a pausa (não repete se já disparado)."""
        if run_id in self._tripped_runs:
            return
        trip = AiBreakerTrip(kind=exc.kind, message=self.account_error_message(exc.kind), run_id=run_id, at=now_iso())
        self._tripped_runs[run_id] = trip
        self.ai_breaker = trip
        self.repo.request_pause(run_id, trip.message)
        self.repo.bus.emit("log", f"Disjuntor de conta de IA acionado ({exc.kind}): {trip.message}",
                           level="error", run_id=run_id)

    def clear_ai_breaker(self, run_id: str) -> None:
        """Ao retomar a execução (usuário recarregou o crédito ou corrigiu a credencial), o disjuntor solta:
        a próxima chamada volta a ir ao provedor de verdade em vez de represar sozinha para sempre."""
        self._tripped_runs.pop(run_id, None)
        if self.ai_breaker is not None and self.ai_breaker.run_id == run_id:
            self.ai_breaker = None

    # ------------------------------------------------------------------ IA com limites
    async def _ai(self, run_id: str, objective_id: str | None, coro_factory: Callable[[], Any], *,
                  step_id: str | None = None, role: str = "", deadline: float | None = None) -> Any:
        """Ponto único de toda chamada de IA de uma execução: disjuntor, tetos, limite global e novas tentativas.

        `objective_id=None` é uso ligado à execução mas a objetivo nenhum — é assim que o PLANEJAMENTO passa a
        entrar aqui (achado #96, item 4): ele chamava `provider.plan` direto e ficava fora da repetição com
        espera e da conferência de orçamento.

        `deadline` é o `time.monotonic()` em que a ETAPA vence. A chamada é cortada no que sobra dele: sem isso,
        um provedor pendurado segurava a vaga de IA e o aparelho para além do prazo da etapa, que só era conferido
        no topo do laço.
        """
        s = self.get_settings()
        tripped = self._tripped_runs.get(run_id)
        if tripped is not None:
            # disjuntor já disparado nesta execução: nem chama o provedor — represa sem gastar tentativa nem chamada.
            raise AIError(tripped.message, kind=tripped.kind)
        run = self.repo.run_row(run_id)
        # `status_detail` de ANTES de qualquer anotação de espera desta chamada — para devolvê-lo ao sair
        # (`clear_wait_reason`, achado #68): sem isto, "aguardando resposta do modelo" ficava escrito na tela
        # bem depois de a chamada terminar, até a PRÓXIMA chamada de IA reescrever o texto por cima.
        detalhe_anterior: str | None = None
        if objective_id is not None:
            obj = self.repo.objective_row(objective_id)
            detalhe_anterior = obj["status_detail"]
            if obj["ai_calls"] >= s.ai_max_calls_per_objective:
                exc = AIError(f"Limite de {s.ai_max_calls_per_objective} chamadas de IA por objetivo atingido.",
                              kind="budget")
                self._registrar_orcamento_estourado(run_id, objective_id, step_id, role, exc)
                raise exc
        if run and (run["ai_input_tokens"] + run["ai_output_tokens"]) >= s.ai_max_tokens_per_run:
            exc = AIError(f"Orçamento de {s.ai_max_tokens_per_run} tokens da execução esgotado.", kind="budget")
            self._registrar_orcamento_estourado(run_id, objective_id, step_id, role, exc)
            raise exc
        last: AIError | None = None
        for attempt in range(3):
            restante = None if deadline is None else deadline - time.monotonic()
            if restante is not None and restante <= 0:
                raise AIError("Prazo da etapa esgotado antes da chamada de IA.", kind="budget")
            # Achado #68/#93: a espera pela VAGA (semáforo cheio) e a espera pela RESPOSTA (chamada em voo) são
            # motivos DIFERENTES — a primeira o operador resolve subindo `max_ai_concurrency`; a segunda só espera.
            # `objective_id=None` é o planejamento (achado #96, item 4): sem objetivo ainda, nada para anotar.
            if objective_id is not None:
                cheio = self.ai_limiter.active >= self.ai_limiter.limit
                self.repo.note_waiting(
                    objective_id,
                    f"aguardando vaga de IA ({self.ai_limiter.active} de {self.ai_limiter.limit} em uso)" if cheio
                    else "aguardando vaga de IA", wait_reason="ai_capacity")
            async with self.ai_limiter:       # limite de chamadas simultâneas ao modelo (≠ aparelhos ativos)
                if objective_id is not None:
                    self.repo.note_waiting(objective_id, f"aguardando resposta do modelo ({role or 'ia'})",
                                           wait_reason="model_response")
                try:
                    result, usage = await _com_prazo(coro_factory(), deadline, role)
                    self.repo.add_usage(run_id, objective_id, usage if usage.calls or self.provider.simulated
                                        else Usage(calls=1), step_id=step_id)
                    return result
                except AIError as exc:
                    # Achado #101: o log é o único jeito de casar uma chamada com erro à exceção real quando o
                    # texto gravado não basta (era assim que se sabia que um 5xx do provedor virou "Verificação
                    # não pôde ser feita" — casando horário com data/logs/backend.log). E a linha de custo passa
                    # a gravar o modelo REALMENTE pedido e o tipo do erro — nunca mais o pseudo-modelo '(erro)'.
                    log.warning("%s: chamada de IA (%s) falhou: %s", role or "ia", exc.kind, exc, exc_info=True)
                    self.repo.add_usage(run_id, objective_id,
                                        Usage(calls=1, role=role, model=exc.model or self._role_model(role)),
                                        step_id=step_id, ok=False, error_kind=exc.kind, error_status=exc.status,
                                        error_message=str(exc))
                    last = exc
                    if exc.kind in ACCOUNT_ERROR_KINDS:
                        # erro de conta: nova tentativa (aqui ou noutro aparelho) gastaria igual — dispara o
                        # disjuntor e pausa a execução em vez de deixar cada aparelho descobrir sozinho.
                        self._trip_ai_breaker(run_id, exc)
                        raise AIError(self.account_error_message(exc.kind), kind=exc.kind) from exc
                    if not exc.retryable:
                        raise
                finally:
                    # A chamada terminou (sucesso, erro ou cancelamento pelo prazo): "aguardando resposta do
                    # modelo" deixa de valer aqui, sucesso ou não — senão ficaria preso até a PRÓXIMA chamada de
                    # IA reescrever o motivo, mostrando a etapa "esperando o modelo" enquanto ela já agia na tela.
                    # `restore_detail` devolve o TEXTO de antes desta chamada pelo mesmo motivo.
                    if objective_id is not None:
                        self.repo.clear_wait_reason(objective_id, restore_detail=detalhe_anterior)
            await asyncio.sleep(float(self.get_settings().ai_retry_wait_s) * (attempt + 1))
        assert last is not None
        raise last

    def _registrar_orcamento_estourado(self, run_id: str, objective_id: str | None, step_id: str | None, role: str,
                                       exc: "AIError") -> None:
        """Achado #99: os dois tetos de ORÇAMENTO (chamadas por objetivo, tokens por execução) recusam ANTES de
        entrar no laço de tentativas — nenhum provedor é chamado, de propósito. Sem esta linha, a recusa nunca
        virava uma linha em `ai_calls` e `/api/usage` não mostrava NADA sobre o estouro (nem em `errors_by_kind`
        nem no painel), embora a etapa e o objetivo já tivessem parado por causa dele. `calls=1` conta como
        tentativa recusada (é o mesmo `Usage` que o laço grava para qualquer erro), sem custo (0 tokens)."""
        self.repo.add_usage(run_id, objective_id, Usage(calls=1, role=role, model=self._role_model(role)),
                            step_id=step_id, ok=False, error_kind=exc.kind, error_status=exc.status,
                            error_message=str(exc))

    def _role_model(self, role: str) -> str:
        """Modelo configurado para esta função, para quando o `AIError` não sabia qual era (falha antes de
        resolver o modelo, ex.: endpoint não configurado). Duck-typing de propósito: nem todo `AIProvider` é o
        `RoutingProvider` do hub (achado #101 — a linha de erro precisa do modelo mesmo assim)."""
        roles = getattr(self.provider, "roles", None)
        if roles and role in roles:
            return getattr(roles[role], "model", "") or ""
        models = getattr(self.provider, "models", None)
        if models and role in models:
            return models[role] or ""
        return getattr(self.provider, "model", "") or ""

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

    def _screen(self, obs: Observation, *, with_image: bool = True, protect: tuple[str, ...] = (),
               boost: tuple[str, ...] = ()) -> tuple[ScreenInput, float]:
        jpeg, w, h, scale = (obs.jpeg if with_image else None), obs.width, obs.height, 1.0
        max_side = self.cfg.file.ai.screenshot_max_side
        if max(w, h) > max_side:                      # com ou sem imagem, x,y do modelo vivem no mesmo espaço reduzido
            scale = max(w, h) / max_side
            w, h = round(w / scale), round(h / scale)
            if jpeg:
                buf = io.BytesIO()
                Image.open(io.BytesIO(jpeg)).resize((w, h)).save(buf, "JPEG", quality=72)
                jpeg = buf.getvalue()
        lines = obs.tree.prompt_lines(self.cfg.file.ai.max_hierarchy_elements, scale, protect=protect, boost=boost)
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
        if outcome.outcome == Outcome.succeeded:
            self._remember_screen(objective, step, rt, outcome, app)
        return outcome

    def _remember_screen(self, objective: Any, step: StepDTO, rt: DeviceRuntime, outcome: StepOutcome,
                         app: AppContext | None = None) -> None:
        """Decisão do dono (24/09): o que o perfil VIU vira memória dele. A tela é a da comprovação da etapa, que o
        executor já tinha observado — sem dump extra e sem IA. Tela sensível e aparelho-loja nunca entram."""
        tree = getattr(rt, "last_tree", None)
        if self.social is None or tree is None or tree.sensitive or getattr(rt, "store", False):
            return
        try:
            profile_id = objective["profile_id"]
        except (KeyError, IndexError, TypeError):
            profile_id = None
        if not profile_id:
            return
        try:
            self.social.remember_screen(profile_id, step_title=step.title, bindings=step.bindings,
                                        elements=tree.elements, items=outcome.items,
                                        app_label=(app.name or app.package or "app") if app else "app",
                                        app_id=app.id if app else None)
        except Exception:  # noqa: BLE001 - memória é enriquecimento: nunca derruba a etapa já comprovada
            log.exception("%s: a tela da etapa %s não virou memória", rt.id, step.key)

    # ------------------------------------------------------------------ histórico social do efeito
    def _open_effect(self, objective: Any, step: StepDTO, rt: DeviceRuntime, cap: Any,
                     app_id: str | None = None) -> None:
        """Chamado no instante do commit. Efeito disparado é efeito que conta, mesmo sem resultado observado."""
        if self.social is None or cap is None or not cap.interaction_type or step.id in self._effects:
            return
        profile_id = objective["profile_id"] or None
        if not profile_id:
            return
        try:
            interaction_id = self.social.open_effect(
                profile_id, capability=cap.key, interaction_type=cap.interaction_type, bindings=step.bindings,
                run_id=step.run_id, objective_id=step.objective_id, step_id=step.id, instance_id=rt.id,
                draft_meta=ler_rascunho(self.repo.db, step.id), app_id=app_id)
            self._effects[step.id] = (profile_id, interaction_id)
            if self.approvals is not None:
                self.approvals.link_interaction(step.id, interaction_id)
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

    def _sessao_desmentida(self, instance_id: str, package: str | None, kind: str, detail: str) -> None:
        """A tela contradisse o que o painel afirmava sobre a sessão. O cache passa a dizer a verdade.

        O estado de sessão sempre foi um cache do que se observou uma vez — e que nunca era corrigido por quem
        estava olhando a tela DEPOIS. O painel mostrava "Conectado" para uma conta presa num desafio, a porta de
        sessão deixava despachar, e como o status seguia `session_ready` o autenticador automático nunca era
        acionado: a etapa parava em `waiting_user` pedindo login manual com a senha guardada no cofre.
        """
        if self.on_auth_needed is None:
            return
        if package != self.cfg.file.instagram.package:
            # Só a sessão do app DO PERFIL. Uma tela de login do QA Messenger num aparelho com perfil vinculado
            # não diz nada sobre a conta do Instagram — e marcá-la de `auth_required` gastaria, sozinha, uma das
            # tentativas de autenticação automática daquele perfil.
            return
        try:
            self.on_auth_needed(instance_id, kind, detail)
        except Exception:  # noqa: BLE001 - corrigir o cache nunca pode derrubar a etapa
            log.exception("%s: falha ao atualizar o estado de sessão do perfil", instance_id)

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
        if step.attempts > 1:
            anterior = repo.db.one("SELECT error FROM attempts WHERE step_id=? AND status='failed' AND error IS NOT NULL"
                                   " ORDER BY number DESC LIMIT 1", (step.id,))
            if anterior and anterior["error"]:
                history.append(f"(tentativa anterior desta etapa falhou) {str(anterior['error'])[:400]} — "
                               "não repita o mesmo caminho; procure outro.")
        if fired:
            history.append("(tentativa anterior) a ação com efeito externo desta etapa JÁ foi disparada; "
                           "resultado " + ("desconhecido" if unknown else "registrado") + ".")
        need = step.postcondition.required_delivery_level

        # Item 7.6: só os parâmetros QUE ESTA ETAPA USA, não o objetivo inteiro (que pode ter dezenas de
        # aparelhos/itens de `for_each` resolvidos). Com catálogo, a capability declara exatamente quais —
        # `cap.bindings` (obrigatórios) e `cap.optional_bindings`, mais o que a própria etapa gravou em
        # `step.variables` (ex.: `{item}` da cópia de `for_each`). Sem catálogo (plano livre) mantém tudo: não
        # há como saber de antemão o que o texto livre do plano vai referenciar.
        ctx_params = actor_params(params, cap, step.variables)

        def ctx_for() -> StepContext:
            desc = step.postcondition.description + (f" (nível de entrega exigido: {need.value})" if need else "")
            return StepContext(run_id=run_id, instance_id=iid, objective_summary=run["command"], parameters=ctx_params,
                               step_key=step.key, step_title=step.title, step_goal=step.goal,
                               side_effect=step.side_effect, commit_done=fired, commit_guard=step.commit_guard,
                               precondition=step.precondition, postcondition_description=desc, remaining_steps=remaining,
                               app=app, account_label=account_label, required_delivery_level=need.value if need else None,
                               resumed_after_manual_control=resumed_after_manual)

        async def call(fn: Callable[..., Any], *args: Any) -> Any:
            return await rt.executor.run(fn, *args, timeout=call_timeout, label=getattr(fn, "__name__", "driver"))

        async def quick_tree() -> UiTree:
            xml = await rt.executor.run(rt.io.page_source, timeout=call_timeout, label="hierarquia")
            return self.devices.arvore(rt, xml)      # mesmos critérios de tela sensível da observação completa

        async def evidence(obs: Observation | None, note: str, kind: str = "screenshot") -> None:
            # `add_evidence_async`: a ESCRITA do arquivo sai do laço de eventos (item 5.7). Em disco local isso
            # era inofensivo; com o storage apontado para um bucket, gravar aqui dentro travaria o scheduler
            # inteiro a cada captura de tela.
            if obs is None:
                await repo.add_evidence_async(run_id=run_id, instance_id=iid, step_id=step.id, attempt_id=attempt_id,
                                              kind="text", note=note)
            elif obs.sensitive or obs.jpeg is None:
                await repo.add_evidence_async(run_id=run_id, instance_id=iid, step_id=step.id, attempt_id=attempt_id,
                                              kind=kind, note=note + " (tela sensível: captura omitida)", redacted=True)
            else:
                await repo.add_evidence_async(run_id=run_id, instance_id=iid, step_id=step.id, attempt_id=attempt_id,
                                              kind=kind, note=note, data=obs.jpeg)

        async def fail_or_retry(detail: str, obs: Observation | None = None) -> StepOutcome:
            await evidence(obs, f"Falha: {detail}")
            if step.side_effect and fired:
                return StepOutcome(Outcome.uncertain, detail)
            return StepOutcome(Outcome.retry if step.attempts < step.max_attempts else Outcome.failed, detail)

        for tries in range(3):                     # logo após ligar/acordar o Android às vezes recusa a 1ª sessão (visto:
            if await self.devices.ensure_automation(rt):   # `adb shell settings …` exit 20) e aceita segundos depois
                break
            if tries == 2 or stop_reason():
                return StepOutcome(Outcome.waiting_user, f"Sessão de automação indisponível: {rt.automation.detail}",
                                   needs="Verifique o Appium/UiAutomator2 (Diagnóstico) e retome este item.")
            await asyncio.sleep(float(self.get_settings().session_retry_wait_s))

        last_obs: Observation | None = None
        last_sig: tuple[str, str] | None = None
        same_count = 0
        sigs: list[tuple[str, str, str]] = []                  # (tela exata, tela estrutural, ação)
        errors_in_row = 0
        declared: StepDone | None = None
        max_actions = int(s.max_actions_per_step)
        ai_cfg = self.cfg.file.ai
        judged_step = step.postcondition.kind == "model_judged" or need is not None
        decisions = 0
        image_requested = False
        # Modelo forte (escalonamento) onde errar custa caro ou o barato já tropeçou: etapa com efeito externo
        # (conforme o risco, ver `side_effect_tier`), nova tentativa da mesma etapa, erros seguidos ou ação
        # repetida na mesma tela.
        tier_efeito, motivo_efeito = side_effect_tier(step, cap, ai_cfg.strong_model_for_side_effect)
        base_tier = 1 if (tier_efeito or step.attempts > 1) else 0
        escalated = False                     # a linha do escalonamento sai UMA vez por etapa, não por decisão
        # Item 7.8 (piso de conteúdo): o provedor de `decide` É o do `.env`/YAML, não o desta instância de etapa —
        # ele não muda no meio de uma execução, então resolver uma vez aqui é o mesmo resultado de resolver a cada
        # volta do laço, sem pagar a travessia de config de novo. Só interessa quando NÃO é Anthropic: é o
        # endpoint local (pouco contexto, resposta pode "esquecer" a árvore atual) quem inventa um `element_id`
        # de uma tela que já passou — a Anthropic recebe a árvore inteira e não tropeça nisto.
        decide_kind = self.cfg.ai_role("decide").kind
        forcar_tier_1 = False                  # a decisão anterior foi descartada pelo piso: a PRÓXIMA sobe de tier
        tier = base_tier                       # só existe de verdade dentro do laço (decisão fresca); este é o
                                                # valor antes de qualquer decisão — nunca lido por uma de receita

        for _ in range(max_actions + 1):
            # ---------- ponto seguro
            why = stop_reason()
            if why:
                return StepOutcome(Outcome.cancelled if why == "cancel" else Outcome.yielded, why)
            if time.monotonic() > deadline:
                return await fail_or_retry(f"Tempo da etapa esgotado ({step.timeout_s}s).", last_obs)
            # ---------- observar
            try:
                obs = last_obs = await self.devices.observe(rt, timeout=call_timeout)
            except DriverTimeout as exc:
                return await self._stuck(rt, step, fired, str(exc))
            except DriverError as exc:
                errors_in_row += 1
                self.devices.invalidate_automation(rt, str(exc))
                if errors_in_row >= 3 or not await self.devices.ensure_automation(rt):
                    return await fail_or_retry(f"Não foi possível observar a tela: {exc}")
                continue
            # "Não mandar a imagem" e "parar e chamar uma pessoa" eram a MESMA coisa enquanto `sensitive` só
            # significava campo de senha. Deixaram de ser (achado #127): uma tela declarada em
            # `sensitive_screens` — ou qualquer tela da VM-loja — precisa ter a imagem omitida, mas parar a
            # etapa ali seria inventar uma falha de autenticação e marcar o perfil como `auth_required` toda vez
            # que a IA passasse por ela. A omissão da imagem já aconteceu (aqui em cima e nos provedores); só o
            # campo de senha e o desafio de verificação pedem gente.
            if pede_intervencao_humana(obs.tree):
                porque = obs.tree.sensitive_reason
                await evidence(obs, f"Tela sensível detectada ({porque})")
                # A tela de senha DESMENTE o "Conectado" do painel: a sessão daquele perfil passa a valer como
                # `auth_required` aqui mesmo. É o que faz o autenticador automático (que tem a credencial no
                # cofre) finalmente disparar na próxima passada, em vez de a etapa parar para sempre pedindo
                # login manual enquanto o status continuava `session_ready`.
                self._sessao_desmentida(iid, app.package, "auth_required",
                                        "o app pediu autenticação durante a execução")
                return StepOutcome(Outcome.waiting_user, f"O app pede autenticação ({porque}).",
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
                            await asyncio.sleep(float(self.cfg.file.ai.recipe_settle_s))
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
                piso_forcou = forcar_tier_1    # captura ANTES de zerar: o motivo do escalonamento lê daqui embaixo
                forcar_tier_1 = False          # consumido: só a decisão SEGUINTE ao descarte sobe de tier, não todas
                tier = 1 if (base_tier or errors_in_row >= 2 or same_count >= 1 or piso_forcou) else 0
                if tier and not escalated:
                    # O escalonamento é configuração explícita do dono (AI_MODEL_ESCALATION,
                    # strong_model_for_side_effect) e já aparecia no cartão de custo — o que faltava era a linha
                    # na execução dizendo POR QUE esta etapa passou a decidir no modelo caro (achado #92, item 5).
                    escalated = True
                    motivo = (motivo_efeito if tier_efeito
                              else "nova tentativa da mesma etapa" if step.attempts > 1
                              else "erros seguidos" if errors_in_row >= 2
                              else "alvo inexistente na tela (piso do modelo local, item 7.8)" if piso_forcou
                              else "ação repetida na mesma tela")
                    repo.decision(f"{iid} · {step.title}: decisão escalonada para o modelo de escalonamento "
                                  f"({motivo})", run_id=run_id, instance_id=iid, step_id=step.id)
                screen, scale = self._screen(obs, with_image=self._want_image(
                    obs, judged_step=judged_step, first=decisions == 0, trouble=trouble, requested=image_requested),
                    protect=tuple(step.commit_guard), boost=_boost_terms(step, app))
                image_requested = False
                decisions += 1
                actor_history = compress_history(history, ai_cfg.actor_history_lines)
                try:
                    decision = await self._ai(run_id, oid, lambda: self.provider.decide(
                        DecisionRequest(ctx=ctx_for(), screen=screen, history=actor_history, tier=tier)),
                        step_id=step.id, role="decide", deadline=deadline)
                except AIError as exc:
                    if exc.kind == "not_configured":
                        return StepOutcome(Outcome.waiting_user, str(exc), needs="Configure a chave do provedor no .env, "
                                           "reinicie o backend e retome este item.", ai_blocked=True)
                    if exc.kind == "billing":
                        return StepOutcome(Outcome.waiting_user, str(exc),
                                           needs="Recarregue o crédito do provedor de IA e retome a execução.",
                                           ai_blocked=True)
                    if exc.kind == "budget":
                        return StepOutcome(Outcome.failed if not fired else Outcome.uncertain, str(exc))
                    if exc.kind == "refusal":
                        # Achado #93: recusa do provedor por política NÃO é "IA indisponível" — repetir a etapa
                        # tende a dar a mesma recusa, e `fail_or_retry` gastaria uma tentativa à toa. Efeito já
                        # disparado: `uncertain` (mesma regra de qualquer falha após o commit); senão, espera a
                        # pessoa decidir — reescrever a intenção ou replanejar — sem consumir tentativa.
                        return StepOutcome(Outcome.uncertain if (step.side_effect and fired) else Outcome.waiting_user,
                                           f"O provedor de IA recusou esta requisição por política: {exc}",
                                           needs=None if (step.side_effect and fired) else
                                           "O provedor recusou por política — repetir tende a dar o mesmo resultado. "
                                           "Reescreva a intenção desta etapa (ou o comando) e retome, ou replaneje.",
                                           ai_blocked=True)
                    return await fail_or_retry(f"IA indisponível: {exc}", obs)
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
                    return await fail_or_retry("A IA insistiu em chamadas inválidas.", obs)
                continue
            # ---------- piso de conteúdo (item 7.8): só entra numa decisão FRESCA (não de receita) de tier 0 num
            # provedor não-Anthropic. Um `element_id` que não está em `obs.tree` é o modelo local respondendo com
            # o id de uma tela anterior — nem `validate_call` (só confere a FORMA do argumento) nem
            # `resolve_point` (só roda depois, e só para tap/long_press) pegam isto cedo. Descartar aqui, sem
            # contar como ação nem como erro, e escalar a PRÓXIMA chamada para o tier 1 é mais barato que deixar
            # o driver tentar resolver um id inexistente e "gastar" uma tentativa de verdade nisso.
            alvo_id = getattr(args, "element_id", None)
            if (not from_recipe and tier == 0 and decide_kind != "anthropic"
                    and alvo_id is not None and obs.tree.by_id(alvo_id) is None):
                history.append(f"(executor) o alvo {alvo_id} não existe nesta tela; decisão descartada")
                forcar_tier_1 = True
                continue
            rationale = getattr(args, "rationale", None)
            if isinstance(args, StepDone) and collecting:
                aid = repo.log_intent(attempt_id, "step_done", args.model_dump(mode="json"), rationale, side_effect=False)
                repo.finish_action(aid, ActionStatus.rejected, error="etapa de coleta: use collect_list")
                history.append("step_done REJEITADA: esta é uma etapa de COLETA — chame collect_list na lista; "
                               "os itens têm de ser lidos pelo executor.")
                errors_in_row += 1
                if errors_in_row >= 4:
                    return await fail_or_retry("A IA não usou collect_list na etapa de coleta.", obs)
                continue
            if isinstance(args, StepDone):
                declared = args
                aid = repo.log_intent(attempt_id, "step_done", args.model_dump(mode="json"), rationale, side_effect=False)
                repo.finish_action(aid, ActionStatus.done, result={"declared": True})
                break
            if isinstance(args, StepBlocked):
                aid = repo.log_intent(attempt_id, "step_blocked", args.model_dump(mode="json"), rationale, side_effect=False)
                repo.finish_action(aid, ActionStatus.done, result={"kind": args.kind})
                await evidence(obs, f"Bloqueio relatado pela IA ({args.kind}): {args.reason}")
                repo.decision(f"{iid}: etapa '{step.title}' bloqueada — {args.reason}", run_id=run_id, instance_id=iid,
                              step_id=step.id)
                if step.side_effect and fired:
                    return StepOutcome(Outcome.uncertain, args.reason)
                if args.kind in ("auth_required", "wrong_account"):
                    # A IA viu login ou conta errada na tela. O perfil para de afirmar "Conectado": `wrong_account`
                    # e desafio dependem de pessoa; `auth_required` volta a ser trabalho do autenticador.
                    self._sessao_desmentida(iid, app.package, args.kind, args.reason)
                if args.needs_user or args.kind in ("auth_required", "wrong_account", "missing_info"):
                    return StepOutcome(Outcome.waiting_user, args.reason, needs=_needs_for(args.kind))
                return await fail_or_retry(args.reason)

            # ---------- guardas de efeito externo
            tool_ctx = ToolContext(io=rt.io, call=call, tree=obs.tree, width=obs.width, height=obs.height,
                                   image_scale=scale, app_package=app.package, app_activity=app.activity,
                                   allowed_packages=self._allowed_packages(), observe=quick_tree,
                                   collect_max_items=(min(cap.collect_limit, int(s.for_each_max_items))
                                                      if cap and cap.collect_limit else None),
                                   collect_from_top=cap.collect_from_top if cap else True,
                                   collect_rewind=bool(cap and cap.collect_rewind))
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
                            return await fail_or_retry("O efeito externo foi tentado no elemento errado.", obs)
                        continue
                else:
                    is_commit = alegado
            if is_commit:
                reject: str | None = None
                if fired:
                    reject = "o efeito externo desta etapa já foi disparado; é proibido repetir. Apenas verifique."
                else:
                    missing = [g for g in step.commit_guard
                               if g and not any(obs.tree.contains_text(v) for v in guard_variants(g))]
                    # Guarda de linha: numa lista, o texto tem de estar na MESMA faixa do alvo, não em qualquer lugar.
                    fora_da_faixa = [g for g in step.band_guard
                                     if g and g not in missing
                                     and not (target is not None
                                              and any(obs.tree.text_in_band(v, target.bounds)
                                                      for v in guard_variants(g)))]
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
                        return await fail_or_retry("Pré-condições do efeito externo não foram atendidas.", obs)
                    continue
                await evidence(obs, "Conferência antes do efeito externo: " +
                               (", ".join(step.commit_guard) or "sem textos de guarda") + " visíveis")

            # ---------- detectar ciclo sem progresso
            sig = (obs.tree.signature(), f"{decision.tool}:{_target_key(args)}")
            same_count = same_count + 1 if sig == last_sig else 0
            last_sig = sig
            sigs.append((sig[0], obs.tree.signature(estrutural=True), sig[1]))
            ciclo = ciclo_sem_progresso(sigs, int(s.no_progress_limit))
            if ciclo:
                return await fail_or_retry(ciclo, obs)

            # ---------- agir (intenção gravada ANTES)
            aid = repo.log_intent(attempt_id, decision.tool, args.model_dump(mode="json"), rationale, side_effect=is_commit,
                                  source="recipe" if from_recipe else "ai")
            if is_commit:
                fired = True           # a partir daqui o efeito pode ter ocorrido, aconteça o que acontecer
                self._open_effect(objective, step, rt, cap, app.id)   # o histórico registra a INTENÇÃO, não o sucesso
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
                    return await fail_or_retry(f"Falhas consecutivas do driver: {exc}", obs)
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
                        return await fail_or_retry("A coleta não encontrou nenhum item na lista.", obs)
                    continue
                elif not (out.result.get("at_end") or out.result.get("capped")):
                    return await fail_or_retry("A lista não chegou ao fim dentro do limite de páginas da coleta.", obs)
                elif len(got) > limit:
                    return StepOutcome(Outcome.waiting_user, f"A lista tem {len(got)} itens; o limite é {limit}.",
                                       needs="Aumente “itens por coleta” (for_each_max_items) em Configuração e retome.")
                else:
                    if out.result.get("capped"):
                        await evidence(obs, f"Coleta limitada a {out.result.get('limit')} itens por decisão do "
                                            "catálogo: a lista continua depois deste ponto.", kind="text")
                    collected = got
                    break                  # fato medido pelo executor: dispensa verificador
            if getattr(args, "need_image", False):
                image_requested = True
            await asyncio.sleep(float(self.cfg.file.ai.action_settle_s))   # deixa a interface assentar antes da próxima observação
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
            return await fail_or_retry(f"Limite de {max_actions} ações por etapa atingido sem concluir.", last_obs)

        if collecting and collected is not None:
            text = f"{len(collected)} item(ns) lidos até o fim da lista: " + ", ".join(collected)[:400]
            await evidence(last_obs, f"Coleta comprovada pelo executor: {text}")
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
                                                                  patient=bool(need) or fired, facts=history[-12:],
                                                                  failure_marks=(tuple(cap.failure_marks)
                                                                                 if cap and fired else ()),
                                                                  local_proof=(cap.local_proof if cap else None))
        except DriverTimeout as exc:
            return await self._stuck(rt, step, fired, str(exc))
        except AIError as exc:
            if exc.kind == "not_configured":
                return StepOutcome(Outcome.waiting_user, str(exc), needs="Configure a chave do provedor no .env, "
                                   "reinicie o backend e retome este item.", ai_blocked=True)
            if exc.kind == "billing":
                return StepOutcome(Outcome.waiting_user, str(exc),
                                   needs="Recarregue o crédito do provedor de IA e retome a execução.",
                                   ai_blocked=True)
            if exc.kind == "budget":
                return StepOutcome(Outcome.failed if not fired else Outcome.uncertain, str(exc))
            if exc.kind == "refusal":
                # Mesma regra do achado #93 do lado da decisão: recusa por política não é "não pôde ser feita" —
                # repetir a verificação tende a dar a mesma recusa, sem gastar tentativa à toa.
                return StepOutcome(Outcome.uncertain if fired else Outcome.waiting_user,
                                   f"O provedor de IA recusou verificar esta etapa por política: {exc}",
                                   needs=None if fired else
                                   "O provedor recusou por política — repetir tende a dar o mesmo resultado. "
                                   "Reescreva a intenção desta etapa (ou o comando) e retome, ou replaneje.",
                                   ai_blocked=True)
            return await fail_or_retry(f"Verificação não pôde ser feita: {exc}", last_obs)
        except DriverError as exc:
            return await fail_or_retry(f"Verificação não pôde ser feita: {exc}", last_obs)
        # o nível de entrega declarado pela IA em step_done não vale como prova; só o observado na verificação
        note = f"Pós-condição {'comprovada' if ok else 'NÃO comprovada'}: {text}"
        await evidence(obs, note, kind="verifier" if obs is None else "screenshot")
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
                      facts: list[str] | None = None, failure_marks: tuple[str, ...] = (),
                      local_proof: str | None = None
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
            # o app costuma levar ~1–2 s para sair de "enviando": evita pagar 2 julgamentos
            await asyncio.sleep(float(self.cfg.file.ai.judge_wait_s))
        while True:
            obs = await self.devices.observe(rt, timeout=call_timeout)
            ok, text = self._deterministic(step, obs)
            # Nível de entrega (enviada/entregue/lida) não é comprovável por texto/seletor — o texto já aparece no
            # campo ANTES do envio. Sempre que o plano exigir um nível, o verificador julga a tela também.
            judged = post.kind == "model_judged" or (ok and need is not None)
            # Achado #102: antes de gastar uma chamada de modelo (que só via os 80 primeiros caracteres de cada
            # elemento), confere pela árvore local quando o catálogo declara uma prova determinística para esta
            # pós-condição julgada. `need` de nível de entrega exige o modelo mesmo assim — "enviado" não prova
            # "entregue/lido". Qualquer condição que falhe (sem `content` conhecido, texto só no campo de escrita,
            # texto ausente) devolve `None`/`False` e cai para o modelo — nunca vira reprovação por si só.
            if judged and need is None and local_proof and local_proof_holds(local_proof, step, obs.tree):
                ok, judged = True, False
                text = (f"pós-condição comprovada pela árvore local, sem IA ({local_proof})"
                        if local_proof != "sent_text" else
                        "conteúdo comprovado pela árvore local, sem IA: presente numa mensagem do fio, ausente do campo de escrita")
                self.repo.decision(f"{rt.id} · {step.title}: pós-condição comprovada pela árvore local (sem IA)",
                                   run_id=run_id, instance_id=rt.id, step_id=step.id)
            if judged:
                sig = obs.tree.signature()
                if judged_polls and sig == judged_sig:
                    ok = False             # mesma tela que já foi julgada insuficiente: espera mudar, sem gastar chamada
                else:
                    # 1º julgamento só pela hierarquia quando ela é rica; os seguintes levam a imagem
                    screen, _ = self._screen(obs, with_image=self._want_image(
                        obs, judged_step=False, first=False, trouble=judged_polls >= 1, requested=False),
                        protect=tuple(step.commit_guard))
                    # `t_end` é o orçamento DESTA verificação (nunca além do prazo da etapa): a chamada de
                    # verificação passa a ter limite próprio, que era o que faltava (achado #96).
                    verdict = await self._ai(run_id, objective_id,
                                             lambda: self.provider.verify(VerifyRequest(ctx=ctx_for(), screen=screen,
                                                                                        facts=list(facts or []))),
                                             step_id=step.id, role="verify", deadline=t_end)
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
            if ok and failure_marks:
                # Um "sim" no primeiro retrato é UI otimista: no app de mensagem o balão aparece e o campo
                # limpa ANTES de o servidor confirmar — a marca de falha só chega depois. Assenta e
                # reconfere por TEXTO (sem gastar outra chamada de modelo) antes de dar a etapa por provada.
                await asyncio.sleep(float(self.cfg.file.ai.effect_settle_s))
                obs = await self.devices.observe(rt, timeout=call_timeout)
                achadas = [m for m in failure_marks if m and obs.tree.contains_text(m)]
                if achadas:
                    marcas = ", ".join(f'"{m}"' for m in achadas)
                    text = "; ".join(x for x in (text, f"a tela passou a mostrar {marcas} depois do envio") if x)
                    return False, text, level, obs, False
            if ok or time.monotonic() >= t_end or judged_polls >= max_calls:
                return ok, text, level, obs, False
            await asyncio.sleep(float(self.cfg.file.ai.judge_wait_s))

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


#: Formas aceitas de um texto de guarda (`@usuario` também sem a arroba). A regra mora em `proofs.py`, onde as
#: provas locais a reaproveitam; o nome fica aqui porque é por ele que o executor e os testes a conhecem.
guard_variants = variantes_de_arroba


def compress_history(history: list[str], n: int) -> list[str]:
    """Item 7.6 (dieta do contexto do ator): histórico da tentativa sem gastar chamada de modelo.

    Mantém, em ordem: toda linha que marca o que NÃO repetir (`REJEITADA`, `FALHOU`, linhas do próprio
    `(executor)` — precondição, receita divergida, etc.) mais as últimas `n` linhas quaisquer. Sem duplicar
    quando as duas regras pegam a mesma linha."""
    if n < 0:
        n = 0
    relevantes = {i for i, h in enumerate(history) if "REJEITADA" in h or "FALHOU" in h or h.startswith("(executor)")}
    relevantes |= set(range(max(0, len(history) - n), len(history)))
    return [history[i] for i in sorted(relevantes)]


def actor_params(params: dict[str, str], cap: Any, step_variables: dict[str, str]) -> dict[str, str]:
    """Item 7.6: os parâmetros que vão ao modelo para ESTA etapa, não o objetivo inteiro. Com catálogo
    (`cap`), só o que a capability declara (`bindings` + `optional_bindings`) mais o que a própria etapa
    gravou (`step_variables`, ex.: `{item}` da cópia de `for_each`). Sem catálogo (plano livre) mantém tudo —
    não há como saber de antemão o que o texto livre do plano referencia."""
    if cap is None:
        return params
    permitidos = set(cap.bindings) | set(cap.optional_bindings) | set(step_variables)
    return {k: v for k, v in params.items() if k in permitidos}


def _boost_terms(step: StepDTO, app: AppContext) -> tuple[str, ...]:
    """Item 7.6: textos do ALVO desta etapa, para `UiTree.prompt_lines(boost=…)` não cortar o elemento certo
    de uma tela grande. Bindings passam pelas mesmas variantes de arroba usadas nas guardas; seletores
    (`commit_selector`, `known_selectors`) contribuem só o VALOR de cada parte (o que aparece na tela, não a
    sintaxe `id=`/`desc=`)."""
    termos: list[str] = []
    for v in (step.bindings or {}).values():
        if v:
            termos.extend(guard_variants(str(v)))
    seletores = [step.commit_selector] if step.commit_selector else []
    seletores.extend((app.known_selectors or {}).values())
    for sel in seletores:
        termos.extend(valor for _, valor, _ in UiTree._partes_do_seletor(sel) if valor)
    return tuple(t for t in termos if t)


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


def side_effect_tier(step: Any, cap: Any, modo: Any) -> tuple[int, str]:
    """(nível, motivo) do escalonamento POR EFEITO EXTERNO desta etapa.

    `True` = qualquer efeito sobe (era o único modo: em 19-23/09, 39 % das decisões foram ao Opus, inclusive curtir
    com `commit_selector` declarado — o executor já confere o seletor, as guardas e a faixa antes do toque, e o
    modelo caro não acrescentava nada). `False` = nunca por efeito. `by_risk` = sobe quando errar é caro E o
    software não tem como travar o alvo: risco alto do catálogo, risco médio sem seletor de commit, ou app sem
    catálogo (risco desconhecido). O motivo mantém o prefixo "etapa com efeito externo", que a linha do tempo e
    os testes reconhecem.
    """
    if not getattr(step, "side_effect", False) or modo is False:
        return 0, ""
    if modo is True:
        return 1, "etapa com efeito externo"
    if cap is None:
        return 1, "etapa com efeito externo sem catálogo: risco desconhecido"
    risco = getattr(cap, "risk", "high")
    if risco == "high":
        return 1, f"etapa com efeito externo de risco alto ({getattr(cap, 'key', '?')})"
    if not (getattr(step, "commit_selector", None) or getattr(cap, "commit_selector", None)):
        return 1, "etapa com efeito externo de risco médio sem seletor de commit"
    return 0, ""


def ciclo_sem_progresso(sigs: Sequence[tuple[str, str, str]], limite: int) -> str | None:
    """Laço sem progresso pelas assinaturas (tela exata, tela estrutural, ação) das decisões desta tentativa.

    Período 1 — a mesma ação na mesma tela EXATA `limite` vezes — já era detectado; a tela exata (com texto) é
    de propósito: rolar uma lista longa repete a ação e a estrutura, mas muda o texto, e isso é progresso.
    Período 2 é o caso da execução f41d10: tocar no 1º quadro da grade → post de outro autor → voltar → grade →
    tocar no MESMO quadro…, nove voltas em dois minutos até o dono pausar. Cada decisão "mudava a tela" em relação
    à imediatamente anterior, então a comparação só com a última nunca disparava; e a tela do post traz "há 32
    minutos" e contagens, por isso o par é comparado pela estrutura, não pelo texto.
    """
    limite = max(2, int(limite))
    if len(sigs) >= limite and len({(s[0], s[2]) for s in sigs[-limite:]}) == 1:
        return "Ciclo sem progresso: a mesma ação não muda a tela."
    voltas = max(2, limite - 1)
    n = 2 * voltas
    if len(sigs) >= n:
        janela = [(s[1], s[2]) for s in sigs[-n:]]
        a, b = janela[-2], janela[-1]
        if a != b and janela == [a, b] * voltas:
            return (f"Ciclo sem progresso: '{a[1]}' e '{b[1]}' se alternam há {voltas} voltas e a tela volta sempre "
                    "à mesma — repetir não vai mudar o resultado; a etapa precisa de outro caminho.")
    return None


def _target_key(args: Any) -> str:
    d = args.model_dump(exclude={"rationale"})
    return ",".join(f"{k}={v}" for k, v in sorted(d.items()) if v is not None)[:200]


def _brief(args: Any) -> str:
    d = args.model_dump(exclude={"rationale"}, exclude_none=True)
    return ", ".join(f"{k}={str(v)[:60]!r}" for k, v in d.items())


def _brief_result(result: dict[str, Any]) -> str:
    return ", ".join(f"{k}={str(v)[:80]}" for k, v in result.items() if k != "ms") or "ok"
