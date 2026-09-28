"""Assistente do comando (ADR-047): refinar o texto com a IA e responder às perguntas de uma execução.

Duas operações, nenhuma delas muda o estado de uma execução existente além do cancelamento explícito:

- `refinar`: comando (+ respostas, + as perguntas de uma execução em `needs_input`) → comando estruturado e o que
  ainda falta. Não grava nada além da linha de custo da chamada de IA.
- `sucessora`: a execução em `needs_input` não volta a planejar (a máquina de estados só a deixa ir para
  `cancelled`). Responder cria outra execução com o comando novo e o MESMO pedido de alvos da foto (`runs.targets`),
  e cancela a antiga apontando para a nova. É o "Repetir" com o comando respondido — sem perder personas e alvos,
  que o "Repetir" do painel não carrega.

As perguntas de DESTINO (`profile_id`, `instance_id`) ficam fora: resolvem-se escolhendo alvos, não escrevendo texto
(escrever "no aparelho X" viraria destino tirado do texto, que a criação recusa sem confirmação).
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..db import loads
from ..models import RunCreate, RunStatus, RunSummary
from ..modules.identity.application.available_data import common_data
from ..modules.execution.domain.command_refinement import (AppResumo, CommandRefinement, RefinamentoInvalido,
                                                          RefineAnswer, RefineRequest, normalizar)
from ..planning.provider import AIError
from ..security.redaction import redact
from .service import RunError, RunService

log = logging.getLogger(__name__)

#: Perguntas que são escolha de alvo, não texto: o assistente não as responde (ver docstring do módulo).
CAMPOS_DE_DESTINO = frozenset({"profile_id", "instance_id"})

MENSAGEM_CREDENCIAL = ("O texto contém uma credencial (ex.: \"Senha: …\"). Ele iria ao provedor de IA e ficaria no "
                       "histórico: tire a senha. Ela fica guardada na conta da persona (aba Contas do perfil), com o "
                       "seu consentimento, e a automação a digita de lá sem passar pela IA.")


class RefineAnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(default="", max_length=60)
    question: str = Field(default="", max_length=400)
    answer: str = Field(min_length=1, max_length=1000)


class CommandRefineBody(BaseModel):
    """`POST /api/commands/refine`. Com `run_id`, as perguntas pendentes e os alvos vêm daquela execução."""
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=3, max_length=4000)
    answers: list[RefineAnswerIn] = Field(default_factory=list, max_length=12)
    instance_ids: list[str] = Field(default_factory=list, max_length=64)
    profile_ids: list[str] = Field(default_factory=list, max_length=64)
    run_id: str | None = Field(default=None, max_length=80)


class RunSuccessorBody(BaseModel):
    """`POST /api/runs/{id}/successor`: o comando respondido e o que fazer com ele."""
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=3, max_length=4000)
    mode: Literal["plan", "execute"] = "plan"


def perguntas_da_execucao(runs: RunService, run: Mapping[str, object]) -> list[dict[str, object]]:
    """As perguntas abertas de uma execução em `needs_input`: as do plano (`missing`) ou, sem plano, as do evento
    mais recente com `data.questions` — o mesmo critério do painel (`perguntasDosEventos`)."""
    plano = loads(str(run["plan"]), None) if run["plan"] else None
    if isinstance(plano, dict) and plano.get("missing"):
        return [{"field": m.get("field") or "", "question": m.get("question") or "", "options": []}
                for m in plano["missing"] if isinstance(m, dict)]
    for ev in runs.repo.db.query("SELECT data FROM events WHERE run_id=? AND data IS NOT NULL ORDER BY id DESC",
                                 (run["id"],)):
        d = loads(str(ev["data"]), None)
        if isinstance(d, dict) and isinstance(d.get("questions"), list) and d["questions"]:
            return [q for q in d["questions"] if isinstance(q, dict)]
    return []


class ComandoAssistido:
    def __init__(self, runs: RunService):
        self.runs = runs

    # ------------------------------------------------------------------ refinar
    async def refinar(self, body: CommandRefineBody) -> CommandRefinement:
        runs = self.runs
        # Segredo nunca vai ao modelo: nem no comando, nem numa resposta (a redação dos eventos é a mesma régua).
        if redact(body.command) != body.command or any(redact(a.answer) != a.answer for a in body.answers):
            raise RunError("credencial_no_comando", MENSAGEM_CREDENCIAL, 409)
        destino = sorted({a.field for a in body.answers if a.field in CAMPOS_DE_DESTINO})
        if destino:
            raise RunError("pergunta_de_destino",
                           "Quem faz e em qual aparelho se escolhe no Comando (modo \"Por persona\" ou marcando os "
                           "aparelhos), não por texto: o assistente não responde a " + ", ".join(destino) + ".", 409)
        instance_ids, profile_ids, pendentes, run_id = list(body.instance_ids), list(body.profile_ids), [], None
        comando = body.command.strip()
        if body.run_id:
            run = runs.repo.run_row(body.run_id)
            if run is None:
                raise RunError("not_found", "Execução não encontrada.", 404)
            run_id = run["id"]
            pedido = self._pedido(run)
            instance_ids = instance_ids or list(pedido.get("instance_ids") or loads(run["instance_ids"], []))
            profile_ids = profile_ids or list(pedido.get("profile_ids") or [])
            pendentes = [q for q in perguntas_da_execucao(runs, run) if q.get("field") not in CAMPOS_DE_DESTINO]
            # O destino desta execução já está na foto (alvos ecoados): o texto que vai à IA é o SEM destinos, senão
            # o refinado carregaria "peça para o Lucas…" e a sucessora o leria de novo como destino do texto.
            comando = runs.sem_destinos(comando) or comando
        status = runs.provider.status()
        if not status.configured:
            raise RunError("ai_not_configured", status.notice, 503)
        perfis = self._perfis(instance_ids, profile_ids)
        req = RefineRequest(
            command=comando,
            answers=[RefineAnswer(a.field, a.question, a.answer.strip()) for a in body.answers],
            apps=self._apps(),
            available_data=list(common_data(runs.dados, perfis)) if perfis else [],
            targets=self._alvos(instance_ids, perfis, profile_ids),
            pending=pendentes)
        try:
            refinado, usage = await runs.provider.refine_command(req)  # type: ignore[attr-defined]
        except AIError as exc:
            raise RunError("ai_error", f"O assistente não conseguiu refinar o comando agora: {exc}", 503,
                           {"kind": exc.kind, "retryable": exc.retryable}) from exc
        # Credencial ecoada, texto longo demais, "pronto" com pergunta aberta: corrigidos aqui para QUALQUER
        # provedor, com a mesma redação dos eventos.
        try:
            refinado = normalizar(refinado, redact)
        except RefinamentoInvalido as exc:
            raise RunError("ai_error", f"O assistente devolveu um comando vazio: {exc}", 503,
                           {"kind": "invalid_output", "retryable": True}) from exc
        # Custo fora de execução (ou da execução que está sendo respondida): entra no teto do dia e no relatório.
        try:
            runs.repo.add_usage(run_id, None, usage)
        except Exception:  # noqa: BLE001 - contabilizar nunca derruba a resposta que já custou
            log.exception("não foi possível registrar o custo do refinamento")
        return refinado

    # ------------------------------------------------------------------ sucessora
    def sucessora(self, run_id: str, body: RunSuccessorBody) -> tuple[RunSummary, bool]:
        """Cria a execução respondida e cancela a antiga. Devolve (nova, criada_agora)."""
        runs = self.runs
        run = runs.repo.run_row(run_id)
        if run is None:
            raise RunError("not_found", "Execução não encontrada.", 404)
        pedido = self._pedido(run)
        foto = loads(run["targets"], None) if run["targets"] else None
        politica = (foto or {}).get("device_policy") or "one"
        instance_ids = list(pedido.get("instance_ids") or [])
        profile_ids = list(pedido.get("profile_ids") or [])
        targets = list(pedido.get("targets") or [])
        if not (instance_ids or profile_ids or targets):
            # Execução distribuída (a foto guarda o pedido ANTES do sorteio) ou anterior à foto: os aparelhos dela.
            instance_ids = list(loads(run["instance_ids"], []))
        chave = f"sucessora-{run_id[-40:]}-{hashlib.sha256(body.command.strip().encode()).hexdigest()[:16]}"
        if RunStatus(run["status"]) != RunStatus.needs_input:
            # Duplo clique ou nova aba: a sucessora deste mesmo texto já existe e a antiga já foi cancelada.
            existente = runs.repo.db.one("SELECT id FROM runs WHERE idempotency_key=?", (chave,))
            if existente is not None:
                return runs.repo.run_summary(runs.repo.run_row(existente["id"]), deduplicated=True), False
            raise RunError("invalid_state", "Só uma execução que espera resposta (needs_input) pode ser respondida.")
        # Os alvos vêm da foto; um destino que sobrou no texto seria lido de novo (e recusado sem confirmação).
        texto = runs.sem_destinos(body.command.strip()) or body.command.strip()
        req = RunCreate(command=texto, instance_ids=instance_ids, profile_ids=profile_ids,
                        targets=targets, device_policy=politica, mode=body.mode, idempotency_key=chave)
        nova = runs.create(req)
        if not nova.deduplicated:
            try:
                runs.cancel(run_id)
            except RunError:
                # Mudou de estado entre a leitura e aqui (outra aba cancelou): a sucessora já existe e é o que vale.
                log.warning("execução %s não pôde ser cancelada ao nascer a sucessora %s", run_id, nova.id)
            texto = f"Respondida: continua na execução {nova.short_id}"
            runs.repo.db.execute("UPDATE runs SET status_detail=? WHERE id=?", (texto, run_id))
            runs.repo.bus.emit("log", f"Execução {run_id}: {texto}", run_id=run_id,
                               data={"successor_run_id": nova.id})
        return nova, not nova.deduplicated

    # ------------------------------------------------------------------ apoio
    @staticmethod
    def _pedido(run: Mapping[str, object]) -> dict[str, object]:
        foto = loads(str(run["targets"]), None) if run["targets"] else None
        pedido = foto.get("pedido") if isinstance(foto, dict) else None
        return pedido if isinstance(pedido, dict) else {}

    def _perfis(self, instance_ids: Sequence[str], profile_ids: Sequence[str]) -> list[str]:
        if profile_ids:
            return list(dict.fromkeys(profile_ids))
        return [p for p in dict.fromkeys(self.runs._perfil_unico(i) for i in instance_ids) if p]  # noqa: SLF001

    def _apps(self) -> list[AppResumo]:
        return [AppResumo(a["id"], a["name"] or a["id"], a["package"])
                for a in self.runs.repo.db.query("SELECT id, name, package FROM apps ORDER BY name")]

    def _alvos(self, instance_ids: Sequence[str], perfis: Sequence[str], profile_ids: Sequence[str]) -> list[str]:
        nomes = {}
        for pid in perfis:
            try:
                r = self.runs.repo.db.one("SELECT name FROM personas WHERE id=?", (pid,))
            except Exception:  # noqa: BLE001 - nome é enfeite do contexto; sem ele, o id basta
                r = None
            nomes[pid] = r["name"] if r else pid
        linhas = [f"aparelho {i}" for i in instance_ids]
        linhas += [f"persona {nomes.get(p, p)}" for p in profile_ids]
        if not profile_ids and perfis:
            linhas.append("persona(s) dos aparelhos: " + ", ".join(nomes[p] for p in perfis))
        return linhas[:20]
