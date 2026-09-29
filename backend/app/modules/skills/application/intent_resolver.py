"""`IntentResolver`: a RESOLVE (design §14.1) como uma cadeia explícita de etapas plugáveis (fase I).

A cadeia padrão (`IntentResolver.standard`), em ordem:

1. **modelos** (`TemplateStage`): o casamento de hoje (`domain/matching.py`), perguntado ao registro — habilidade
   publicada (atrás de `skills.enabled`) → fluxo ativo (atrás de `ai.flows`), escopo por perfil e grupo, a mais
   específica primeiro. Devolve TODOS os candidatos da força mais alta, e não "o primeiro por id". Sem nenhum que
   case inteiro, os que casam com um `{nome}` vazio (só habilidade da DSL: é quem tem tipos para perguntar).
2. **tipos** (`TypedStage` + `ParameterExtractor`): cada valor validado pelo tipo declarado no documento. Candidato
   único e válido → resolvido; único e com valor faltando ou inválido → pergunta. Entre empatados, se só UM passa
   nos tipos, ele é a resposta — determinística, não às cegas.
3. **semântica** (`SemanticStage`): classificador, quando os modelos não acharam candidato completo.
4. **preferência** (`PreferenceStage`, só quando a composição dá a fonte — ADR-054): a escolha que a pessoa repetiu
   neste empate, lida do livro de aprendizado. Decide sozinha só quando a habilidade escolhida não tem etapa com
   efeito externo; com efeito, a pergunta continua, com a opção pré-selecionada (`MissingInfo.suggested`).
5. **desambiguação por LLM** (`LlmDisambiguationStage`): quando sobram dois ou mais candidatos válidos.

As etapas 3 e 5 são PORTAS (`intent_ports.py`) cuja única implementação é a nula (`NullSemanticClassifier`,
`NullDisambiguator`): elas não chamam IA — chamada paga exige autorização do dono — e a trilha diz `not_run`. A
cadeia fica pronta para um provedor real sem gasto nenhum hoje. A 4 também é porta, mas sem IA: quem a implementa é
o aprendizado (DAG `learning → skills`), e sem ela a cadeia é exatamente a de antes.

O que sobra sem decisão vira pergunta (`IntentResolution.needs_input`), nunca escolha às cegas nem valor inventado.
A resolução não tem efeito: não grava nada, não chama o planejador, não cria execução.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from app.contracts.skills.v1alpha1 import ParameterSpec
from app.modules.skills.application.intent_ports import (IntentDisambiguator, PreferenceSource,
                                                         SemanticIntentClassifier, SkillCandidates)
from app.modules.skills.domain.compiler import parse_document
from app.modules.skills.domain.errors import CompileIssue
from app.modules.skills.domain.intent import (IntentResolution, ParameterExtraction, ProfileLinkRule, ResolutionMethod,
                                              ResolutionStatus, ResolvedIntent, SkillMatch, StageOutcome, StageTrace,
                                              ambiguity_question, extract_typed)
from app.modules.skills.domain.refs import SkillRef
from app.modules.skills.domain.versions import SCHEMA_LEGACY_PLAN, ResolvedSkill

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IntentRequest:
    command: str
    #: Os perfis dos aparelhos da execução; `None` = prévia sem aparelhos (qualquer escopo casa), como no registro.
    profile_ids: tuple[str | None, ...] | None = None


@dataclass(frozen=True, slots=True)
class IntentCandidate:
    match: SkillMatch
    #: `None` até a etapa de tipos rodar.
    extraction: ParameterExtraction | None = None

    @property
    def valid(self) -> bool:
        """Completo e com todos os valores servindo para o tipo: pode virar plano."""
        return self.match.complete and self.extraction is not None and self.extraction.ok


@dataclass(frozen=True, slots=True)
class IntentState:
    """O que as etapas passam adiante. `candidates` são sempre da mesma força (o topo)."""

    candidates: tuple[IntentCandidate, ...] = ()
    intent: ResolvedIntent | None = None
    trace: tuple[StageTrace, ...] = ()
    #: A opção pré-selecionada de um empate que ninguém pôde decidir sozinho (a etapa de preferência).
    suggested: SkillRef | None = None

    def traced(self, stage: str, outcome: StageOutcome, detail: str = "") -> IntentState:
        return replace(self, trace=(*self.trace, StageTrace(stage, outcome, detail)))


class IntentStage(Protocol):
    @property
    def name(self) -> str: ...

    def apply(self, request: IntentRequest, state: IntentState) -> IntentState: ...


def _refs(matches: Sequence[SkillMatch]) -> str:
    return ", ".join(str(m.ref) for m in matches)


def _intent(candidate: IntentCandidate, extraction: ParameterExtraction, method: ResolutionMethod) -> ResolvedIntent:
    skill = replace(candidate.match.skill, parameters=extraction.command_values())
    return ResolvedIntent(skill, method, extraction.parameters)


# ================================================================== extração tipada
def parameter_specs(skill: ResolvedSkill) -> tuple[ParameterSpec, ...] | None:
    """Os parâmetros tipados da versão (§10.4: o schema tipado mora só em `skill_versions.content`).

    `None` = sem tipo: conteúdo legado, ou documento que nem passa no esquema (a compilação dirá por quê — aqui o
    valor só passa adiante como veio)."""
    versao = skill.version
    if versao.schema_version == SCHEMA_LEGACY_PLAN:
        return None
    problemas: list[CompileIssue] = []
    documento = parse_document(versao.document(), problemas)
    return tuple(documento.spec.parameters) if documento is not None else None


class ParameterExtractor:
    """Os tipos do documento aplicados aos valores que o comando deu (`domain/intent.py::extract_typed`).

    `profile_links(app_id)`: as regras de link de perfil do app da habilidade (o link do Instagram só vira `@nome`
    numa habilidade do Instagram). Quem as conhece é a composição.
    """

    def __init__(self, profile_links: Callable[[str | None], Sequence[ProfileLinkRule]] | None = None) -> None:
        self._links = profile_links

    def extract(self, match: SkillMatch) -> ParameterExtraction:
        skill = match.skill
        links = self._links(skill.definition.app_id) if self._links is not None else ()
        return extract_typed(parameter_specs(skill), skill.parameters, missing=match.missing, links=links,
                             skill=str(skill.ref), skill_name=skill.definition.name)


# ================================================================== etapas
class TemplateStage:
    """Etapa 1: os modelos `{nome}`, pelo registro (precedência, interruptores e escopo de sempre)."""

    name = "template"

    def __init__(self, source: SkillCandidates) -> None:
        self._source = source

    def apply(self, request: IntentRequest, state: IntentState) -> IntentState:
        if state.intent is not None or state.candidates:
            return state.traced(self.name, StageOutcome.SKIPPED)
        achados = tuple(self._source.candidates(request.command, request.profile_ids))
        if not achados:
            return state.traced(self.name, StageOutcome.NO_MATCH)
        saida = (StageOutcome.PARTIAL if not achados[0].complete
                 else StageOutcome.AMBIGUOUS if len(achados) > 1 else StageOutcome.MATCHED)
        return replace(state, candidates=tuple(IntentCandidate(m) for m in achados)).traced(
            self.name, saida, _refs(achados))


class TypedStage:
    """Etapa 2: os tipos. Decide sozinha quando há um só candidato válido; senão, passa adiante o que sobrou."""

    name = "typed"

    def __init__(self, extractor: ParameterExtractor) -> None:
        self._extractor = extractor

    def apply(self, request: IntentRequest, state: IntentState) -> IntentState:
        if state.intent is not None or not state.candidates:
            return state.traced(self.name, StageOutcome.SKIPPED)
        avaliados = tuple(replace(c, extraction=self._extractor.extract(c.match)) for c in state.candidates)
        validos = [c for c in avaliados if c.valid]
        if len(avaliados) == 1 or len(validos) == 1:
            if len(validos) == 1 and validos[0].extraction is not None:
                metodo = ResolutionMethod.TEMPLATE if len(avaliados) == 1 else ResolutionMethod.TYPED
                saida = StageOutcome.VALIDATED if len(avaliados) == 1 else StageOutcome.TIE_BROKEN
                return replace(state, candidates=avaliados,
                               intent=_intent(validos[0], validos[0].extraction, metodo)).traced(
                    self.name, saida, str(validos[0].match.ref))
            campos = ", ".join(q.field for c in avaliados if c.extraction is not None for q in c.extraction.questions)
            return replace(state, candidates=avaliados).traced(self.name, StageOutcome.INVALID, campos)
        if len(validos) > 1:
            return replace(state, candidates=tuple(validos)).traced(
                self.name, StageOutcome.AMBIGUOUS, _refs([c.match for c in validos]))
        return replace(state, candidates=avaliados).traced(self.name, StageOutcome.INVALID,
                                                           _refs([c.match for c in avaliados]))


class NullSemanticClassifier:
    """A ÚNICA implementação de `SemanticIntentClassifier` hoje: não classifica e não chama IA (custo exige
    autorização). Existe para a etapa 3 ter lugar na cadeia e a trilha dizer `not_run`."""

    available = False

    def classify(self, command: str, candidates: Sequence[SkillMatch]) -> SkillMatch | None:
        return None


class NullDisambiguator:
    """A ÚNICA implementação de `IntentDisambiguator` hoje: não desempata e não chama IA. Com ela, o empate vira
    pergunta à pessoa, e a trilha diz `not_run`."""

    available = False

    def choose(self, command: str, candidates: Sequence[SkillMatch]) -> SkillMatch | None:
        return None


class SemanticStage:
    """Etapa 3: classificador, quando nenhum modelo casou inteiro. O que ele propõe passa pelos MESMOS tipos: a
    proposta é dado, e valor que não serve vira pergunta como qualquer outro."""

    name = "semantic"

    def __init__(self, classifier: SemanticIntentClassifier, extractor: ParameterExtractor) -> None:
        self._classifier = classifier
        self._extractor = extractor

    def apply(self, request: IntentRequest, state: IntentState) -> IntentState:
        if state.intent is not None or any(c.match.complete for c in state.candidates):
            return state.traced(self.name, StageOutcome.SKIPPED)
        if not self._classifier.available:
            return state.traced(self.name, StageOutcome.NOT_RUN, "provedor nulo: sem IA sem autorização")
        achado = self._classifier.classify(request.command, tuple(c.match for c in state.candidates))
        if achado is None or not achado.complete:
            return state.traced(self.name, StageOutcome.NO_MATCH)
        candidato = IntentCandidate(achado, self._extractor.extract(achado))
        if candidato.valid and candidato.extraction is not None:
            return replace(state, candidates=(candidato,),
                           intent=_intent(candidato, candidato.extraction, ResolutionMethod.SEMANTIC)).traced(
                self.name, StageOutcome.MATCHED, str(achado.ref))
        return replace(state, candidates=(candidato,)).traced(self.name, StageOutcome.INVALID, str(achado.ref))


class PreferenceStage:
    """Etapa 4 (ADR-054): a escolha que a pessoa repetiu neste empate, lida do livro de aprendizado pela porta.

    Só age com dois ou mais candidatos válidos, e só aceita uma sugestão entre eles. Decide sozinha só quando a fonte
    diz que pode (`decide`: publicada, sem etapa com efeito externo); senão deixa a opção PRÉ-SELECIONADA e a
    pergunta continua — preferência nunca responde por ação com efeito. A fonte que falha é "sem preferência": o
    aprendizado nunca derruba a resolução."""

    name = "preference"

    def __init__(self, source: PreferenceSource) -> None:
        self._source = source

    def apply(self, request: IntentRequest, state: IntentState) -> IntentState:
        validos = [c for c in state.candidates if c.valid]
        if state.intent is not None or len(validos) < 2:
            return state.traced(self.name, StageOutcome.SKIPPED)
        try:
            dica = self._source.preferred(request.command, tuple(c.match for c in validos), request.profile_ids)
        except Exception:  # noqa: BLE001 - preferência é sugestão: sem ela, a pessoa decide como antes
            log.exception("preferências indisponíveis para %r (a pergunta segue sem sugestão)", request.command)
            return state.traced(self.name, StageOutcome.NOT_RUN, "preferências indisponíveis")
        alvo = next((c for c in validos if dica is not None and c.match.ref.skill_id == dica.skill_id), None)
        if dica is None or alvo is None or alvo.extraction is None:
            return state.traced(self.name, StageOutcome.NO_MATCH)
        if dica.decide:
            return replace(state, intent=_intent(alvo, alvo.extraction, ResolutionMethod.PREFERENCE)).traced(
                self.name, StageOutcome.MATCHED, str(alvo.match.ref))
        motivo = dica.detail or "a pessoa confirma"
        return replace(state, suggested=alvo.match.ref).traced(
            self.name, StageOutcome.SUGGESTED, f"{alvo.match.ref} pré-selecionada: {motivo}")


class LlmDisambiguationStage:
    """Etapa 5: desempate por LLM entre candidatos válidos. A resposta só vale se for um deles."""

    name = "llm"

    def __init__(self, disambiguator: IntentDisambiguator) -> None:
        self._disambiguator = disambiguator

    def apply(self, request: IntentRequest, state: IntentState) -> IntentState:
        validos = [c for c in state.candidates if c.valid]
        if state.intent is not None or len(validos) < 2:
            return state.traced(self.name, StageOutcome.SKIPPED)
        if not self._disambiguator.available:
            return state.traced(self.name, StageOutcome.NOT_RUN, "provedor nulo: sem IA sem autorização")
        escolhido = self._disambiguator.choose(request.command, tuple(c.match for c in validos))
        alvo = next((c for c in validos if escolhido is not None and c.match.ref == escolhido.ref), None)
        if alvo is None or alvo.extraction is None:
            return state.traced(self.name, StageOutcome.NO_MATCH)
        return replace(state, intent=_intent(alvo, alvo.extraction, ResolutionMethod.LLM)).traced(
            self.name, StageOutcome.MATCHED, str(alvo.match.ref))


# ================================================================== a cadeia
class IntentResolver:
    def __init__(self, stages: Sequence[IntentStage]) -> None:
        self._stages = tuple(stages)

    @classmethod
    def standard(cls, source: SkillCandidates, extractor: ParameterExtractor, *,
                 classifier: SemanticIntentClassifier | None = None,
                 disambiguator: IntentDisambiguator | None = None,
                 preferences: PreferenceSource | None = None) -> IntentResolver:
        """Modelos → tipos → semântica → [preferência] → LLM. Sem provedor dado, semântica e LLM usam o nulo (não
        chamam IA); a preferência só entra na cadeia quando a composição dá a fonte."""
        preferencia: tuple[IntentStage, ...] = (PreferenceStage(preferences),) if preferences is not None else ()
        return cls((TemplateStage(source), TypedStage(extractor),
                    SemanticStage(classifier if classifier is not None else NullSemanticClassifier(), extractor),
                    *preferencia,
                    LlmDisambiguationStage(disambiguator if disambiguator is not None else NullDisambiguator())))

    @property
    def stages(self) -> tuple[str, ...]:
        return tuple(s.name for s in self._stages)

    def resolve(self, request: IntentRequest) -> IntentResolution:
        estado = IntentState()
        for etapa in self._stages:
            estado = etapa.apply(request, estado)
        return conclude(estado)


def conclude(state: IntentState) -> IntentResolution:
    """O estado final da cadeia vira uma das três respostas. O que não foi decidido é pergunta."""
    if state.intent is not None:
        return IntentResolution(ResolutionStatus.RESOLVED, intent=state.intent, trace=state.trace)
    if not state.candidates:
        return IntentResolution(ResolutionStatus.NO_MATCH, trace=state.trace)
    if len(state.candidates) == 1:
        unico = state.candidates[0]
        skill = unico.match.skill
        extracao = unico.extraction
        if extracao is None:                      # cadeia sem a etapa de tipos: os valores passam como vieram
            extracao = extract_typed(None, skill.parameters, missing=unico.match.missing, skill=str(skill.ref),
                                     skill_name=skill.definition.name)
        if unico.match.complete and extracao.ok:
            return IntentResolution(ResolutionStatus.RESOLVED, trace=state.trace,
                                    intent=_intent(unico, extracao, ResolutionMethod.TEMPLATE))
        return IntentResolution(ResolutionStatus.NEEDS_INPUT, questions=extracao.questions, subject=skill,
                                trace=state.trace)
    skills = tuple(c.match.skill for c in state.candidates)
    return IntentResolution(ResolutionStatus.NEEDS_INPUT, questions=(ambiguity_question(skills, state.suggested),),
                            candidates=skills, trace=state.trace)
