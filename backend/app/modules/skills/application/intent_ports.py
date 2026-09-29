"""Portas da resolução de intenção (fase I). Pertencem a quem CONSOME (o `IntentResolver`); quem implementa as cumpre
por estrutura, sem importá-las.

- `SkillCandidateSource`: um backend do registro que, além de `SkillSource`, lista TODOS os candidatos da força mais
  alta — sem isso, dois candidatos empatados viram "o primeiro por id", uma escolha às cegas. `SqlSkillRepository` e
  `LegacyFlowAdapter` cumprem.
- `SkillCandidates`: o que a etapa de modelos pergunta ao registro (`CompositeSkillRegistry.candidates`), com a
  precedência e os interruptores de sempre.
- `SemanticIntentClassifier` (etapa 3) e `IntentDisambiguator` (etapa 4): as etapas por IA, DECLARADAS para a cadeia
  ficar pronta sem gasto. **A única implementação hoje é a nula** (`intent_resolver.NullSemanticClassifier`,
  `NullDisambiguator`): não chama IA nenhuma, porque chamada paga exige autorização do dono, e a etapa registra
  `not_run` na trilha. Um provedor real vai precisar de duas coisas que a porta ainda não tem: ser assíncrono e rodar
  só no `_plan`, com aviso de custo — nunca na prévia (`/api/flows/match`, `apps_exigidos`, `/api/skills/resolve`),
  que é chamada a cada tecla do painel.
- `PreferenceSource` (etapa entre a semântica e o LLM, ADR-054): a escolha que a pessoa REPETIU neste mesmo empate.
  Quem a guarda é o livro de aprendizado (`modules/learning`), que implementa esta porta — o DAG é `learning →
  skills`, e as habilidades nunca importam o aprendizado. Sem IA e sem gasto: é leitura do livro.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.modules.skills.application.ports import SkillSource
from app.modules.skills.domain.intent import SkillMatch


class SkillCandidateSource(SkillSource, Protocol):
    def candidates(self, command: str, profile_ids: Sequence[str | None] | None) -> Sequence[SkillMatch]: ...


class SkillCandidates(Protocol):
    def candidates(self, command: str, profile_ids: Sequence[str | None] | None) -> Sequence[SkillMatch]: ...


class SemanticIntentClassifier(Protocol):
    """Escolhe entre os candidatos, ou acha um quando os modelos não acharam nenhum. Devolve só um dos candidatos
    completos que recebeu (ou `None`): a etapa não aceita habilidade inventada."""

    @property
    def available(self) -> bool: ...

    def classify(self, command: str, candidates: Sequence[SkillMatch]) -> SkillMatch | None: ...


class IntentDisambiguator(Protocol):
    """Desempata candidatos que o texto e os tipos não separaram. Mesma regra: só um dos que recebeu, ou `None`."""

    @property
    def available(self) -> bool: ...

    def choose(self, command: str, candidates: Sequence[SkillMatch]) -> SkillMatch | None: ...


@dataclass(frozen=True, slots=True)
class PreferenceHint:
    """A escolha que a pessoa repetiu neste empate.

    - `skill_id` sem versão: a preferência sobrevive a uma versão nova da mesma habilidade, mas só como SUGESTÃO;
    - `decide`: pode decidir SOZINHA — só quando a habilidade escolhida não tinha etapa com efeito externo e a
      preferência está publicada; senão a pergunta continua, com ela pré-selecionada;
    - `versions`: as versões em que a falta de efeito foi CONFERIDA (os planos das execuções observadas). O `decide`
      só vale para uma delas: uma versão nova pode ter ganho uma etapa com efeito (seguir, mandar DM), e preferência
      nunca responde por ação com efeito (ADR-054). Vazio: não decide em versão nenhuma.
    """

    skill_id: str
    decide: bool
    detail: str = ""
    versions: tuple[int, ...] = ()


class PreferenceSource(Protocol):
    """A preferência para ESTE empate e ESTES perfis (os dos aparelhos da execução; `None` = prévia sem aparelho).
    `None` quando não há uma que valha para todos: a pessoa decide, como antes. Só uma sugestão entre os candidatos
    que recebeu vale — a etapa confere."""

    def preferred(self, command: str, candidates: Sequence[SkillMatch],
                  profile_ids: tuple[str | None, ...] | None) -> PreferenceHint | None: ...
