"""`ProviderSkillGeneralizer`: a porta `SkillGeneralizer` sobre o `generalize` que o modo treinamento já usa.

Um adaptador só, dois provedores:
- no MODO SIMULADO (`SimulatedProvider`, e o `CountingProvider` dos testes por cima dele) a proposta sai de regras
  fixas (`planning/training.py::proposta_simulada`): determinística e de graça — é o que os testes provam;
- com IA real, o `RoutingProvider.generalize` faz UMA chamada no papel `plan` (mesmo modelo e orçamento do
  planejador) e o custo entra em `ai_calls` por `add_usage`. Essa chamada é PAGA: sem autorização do dono ela fica
  `not_run` — nenhum teste a faz.

A proposta do provedor é dado não confiável: vira JSON conferido (`as_json_object`), depois a candidata
`{document, annotations}` (`domain/generalization.py`), e o documento ainda passa pelo `SkillDocument` e pelo
compilador no serviço. Nada do que o modelo devolve é executado como código.

Mora em `app/training`, e não em `app/modules/skills`, por causa da D15: o contexto de habilidades não importa
módulo de IA (`tests/test_compilador_de_skills.py::test_compilador_nao_avalia_nem_carrega_codigo_por_ast`). Este é
o adaptador da borda — do lado do legado que já fala com o provedor, como `training/skills.py`.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from app.db import Database
from app.modules.applications.infrastructure.app_repository import AppRepository
from app.modules.skills.domain.document import JsonObject, NotJson, as_json_object
from app.modules.skills.domain.generalization import GeneralizationContext, envelope_from_proposal
from app.modules.skills.domain.teaching import Generalization, GeneralizationRequest, GeneralizerFailed, RecordedInput
from app.planning.capabilities import load_catalog
from app.planning.provider import AIError, Usage
from app.planning.training import TrainingRequest


class GeneralizingProvider(Protocol):
    """O pedaço do provedor de IA que generaliza (o `AIProvider` do legado não declara `generalize`: hoje ele é
    chamado por duck typing, §13.2)."""

    @property
    def model(self) -> str: ...

    @property
    def simulated(self) -> bool: ...

    async def generalize(self, req: TrainingRequest) -> tuple[dict[str, object], Usage]: ...


class ProviderSkillGeneralizer:
    def __init__(self, provider: GeneralizingProvider, db: Database, *,
                 record_usage: Callable[[Usage], None] | None = None) -> None:
        self._provider = provider
        self._apps = AppRepository(db)
        self._record_usage = record_usage

    async def generalize(self, request: GeneralizationRequest) -> Generalization:
        apps = [{"id": a["id"], "name": a["name"], "package": a["package"]} for a in self._apps.listar()]
        pacote = next((a["package"] for a in apps if a["id"] == request.app_id), None)
        catalogo = load_catalog(pacote) if pacote else None
        oferta: list[dict[str, object]] = [
            {"key": c.key, "title": c.title, "side_effect": c.side_effect, "bindings": list(c.bindings)}
            for c in (catalogo.offered if catalogo is not None else [])]
        simulado = bool(self._provider.simulated)
        pedido = TrainingRequest(
            # O modo simulado monta o COMANDO a partir da intenção: o contexto extra (respostas, correções)
            # mudaria o comando a cada geração. Na IA real, esse contexto é justamente o que o prompt precisa.
            intent=request.instruction if simulado else _intencao_com_contexto(request),
            app_id=request.app_id, apps=[dict(a) for a in apps], inputs=[_entrada_legada(e) for e in request.inputs],
            catalog=oferta, session_id=request.teaching_id)
        try:
            proposta, usage = await self._provider.generalize(pedido)
        except AIError as exc:
            raise GeneralizerFailed(f"A IA não conseguiu propor a candidata: {exc}") from exc
        if self._record_usage is not None:
            try:
                self._record_usage(usage)
            except Exception:  # noqa: BLE001 - contabilidade nunca derruba a proposta (como no treino v1)
                pass
        try:
            bruta = as_json_object(proposta)
        except NotJson as exc:
            raise GeneralizerFailed(f"A proposta do generalizador não é JSON: {exc}") from exc
        ctx = GeneralizationContext(skill_id=request.skill_id, app_id=request.app_id,
                                    instruction=request.instruction, has_catalog=catalogo is not None,
                                    inputs=request.inputs, answers=request.answers, corrections=request.corrections,
                                    simulated=simulado)
        envelope, perguntas = envelope_from_proposal(bruta, ctx)
        return Generalization(envelope, perguntas, f"ai:{usage.model or self._provider.model}")


def _intencao_com_contexto(request: GeneralizationRequest) -> str:
    partes = [request.instruction]
    if request.base_version:
        partes.append(f"Este ensino melhora a habilidade {request.base_version}: mantenha o id {request.skill_id}.")
    if request.example_commands:
        partes.append("Execuções comprovadas usadas como exemplo:\n" +
                      "\n".join(f"- {c}" for c in request.example_commands))
    if request.corrections:
        partes.append("Correções da pessoa (a habilidade errou aqui):\n" +
                      "\n".join(f"- {c}" for c in request.corrections))
    if request.answers:
        partes.append("Respostas da pessoa às suas perguntas anteriores (não pergunte de novo):\n" +
                      "\n".join(f"- {a.question} → {a.answer}" for a in request.answers))
    return "\n\n".join(p for p in partes if p.strip())


def _entrada_legada(e: RecordedInput) -> dict[str, object]:
    """A entrada no formato de `training_inputs` que `linha_da_entrada` e `proposta_simulada` leem."""
    alvo: JsonObject | None = e.target
    return {"seq": e.seq, "type": e.type, "package": e.package, "app_id": e.app_id, "text": e.text,
            "has_text": e.has_text, "text_len": e.text_len, "target": alvo, "screen_title": e.screen_title,
            "screen_lines": list(e.screen_lines), "x": e.x, "y": e.y, "x2": e.x2, "y2": e.y2,
            "key_name": e.key_name, "sensitive": e.sensitive}
