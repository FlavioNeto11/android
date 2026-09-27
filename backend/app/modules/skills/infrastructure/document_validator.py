"""A porta `DocumentValidator` (fase D) de verdade: o schema `automation/v1alpha1` + o compilador (fase E).

O repositório pergunta duas coisas ao validador, em momentos diferentes:

- ao gravar um rascunho, o que o documento DECLARA (id, nome, app, comando-modelo, apps exigidos). Um rascunho com
  erro ainda se salva — é trabalho em andamento —, então os metadados são lidos do documento cru, sem exigir que ele
  passe no schema; os erros vão em `errors`, e só barram `draft → candidate`;
- ao submeter, se ele COMPILA sem erro. A compilação é a sem valores (§12.1): todos os ramos do `when`, com a baixa
  para `Plan` e o validador do `Plan` como última porta — o mesmo `SkillPlanCompiler` que a execução usa, para que
  "compilou na submissão" e "compila na execução" não possam divergir por caminho.

A composição (`uses:`) precisa das versões travadas das skills filhas: `LockedVersions` as lê do repositório. É
injetada por um `get` tardio porque o repositório também precisa deste validador (a composição liga os dois).
"""
from __future__ import annotations

from collections.abc import Callable

from pydantic import ValidationError

from app.contracts.skills.v1alpha1 import SkillDocument

from ..domain.compiler import LockedSkill, SkillLookup
from ..domain.document import JsonObject, JsonValue
from ..domain.errors import CompileIssue
from ..domain.lifecycle import SkillNotFound, SkillState
from ..domain.refs import InvalidSkillRef, SkillRef
from ..domain.versions import SCHEMA_DSL_V1, DocumentFacts, SkillVersion
from .lowering import SkillPlanCompiler

#: Versão de uma skill filha que não se compõe: `draft` ainda muda de conteúdo (a trava travaria outra coisa), e
#: `disabled` é a parada de emergência — ela vale também para quem a usa.
NAO_SE_COMPOE = frozenset({SkillState.DRAFT, SkillState.DISABLED})


def issue_text(issue: CompileIssue) -> str:
    """`E_CODIGO /caminho: mensagem` — o texto de `DocumentFacts.errors` (a forma estruturada é `as_dict()`)."""
    return f"{issue.code.value} {issue.path}: {issue.message}" if issue.path else f"{issue.code.value}: {issue.message}"


class LockedVersions:
    """`SkillLookup` do compilador sobre as versões gravadas: a versão EXATA, com o hash do conteúdo como trava.

    Fluxo legado (`flow:<id>@1`) não se compõe: o conteúdo dele é um plano congelado, não um documento da DSL.
    Versão adulterada não vira "não encontrada": `ContentTampered` sobe, e a compilação é recusada com o motivo.
    """

    def __init__(self, get: Callable[[SkillRef], SkillVersion]) -> None:
        self._get = get

    def locked(self, skill_id: str, version: int) -> LockedSkill | None:
        try:
            ref = SkillRef(skill_id, version)
        except InvalidSkillRef:
            return None
        if ref.is_legacy:
            return None
        try:
            versao = self._get(ref)
        except SkillNotFound:
            return None
        if versao.state in NAO_SE_COMPOE or versao.schema_version != SCHEMA_DSL_V1:
            return None
        try:
            documento = SkillDocument.model_validate(versao.document())
        except ValidationError:
            return None
        return LockedSkill(documento, versao.content_hash)


class DslDocumentValidator:
    def __init__(self, pacote_do_app: Callable[[str], str | None], skills: SkillLookup | None = None) -> None:
        self._compilador = SkillPlanCompiler(pacote_do_app, skills)

    def inspect(self, document: JsonObject) -> DocumentFacts:
        meta = _objeto(document.get("metadata"))
        spec = _objeto(document.get("spec"))
        exige = _objeto(spec.get("requires"))
        # A versão só entra no `planner.model` do plano de inspeção, que é descartado: os erros não dependem dela.
        r = self._compilador.compilar(document, version=1)
        apps = (r.graph.required_apps if r.graph is not None
                else tuple(a for a in _lista(exige.get("apps")) if isinstance(a, str)))
        return DocumentFacts(
            skill_id=_texto(meta.get("id")), name=_texto(meta.get("name")),
            description=_texto(meta.get("description")) or "", app_id=_texto(meta.get("app")),
            command_template=_texto(_objeto(spec.get("invocation")).get("command_template")),
            app_ids=tuple(apps), errors=tuple(issue_text(i) for i in r.errors), schema_version=SCHEMA_DSL_V1)


def _objeto(v: JsonValue) -> JsonObject:
    return v if isinstance(v, dict) else {}


def _lista(v: JsonValue) -> list[JsonValue]:
    return v if isinstance(v, list) else []


def _texto(v: JsonValue) -> str | None:
    return v if isinstance(v, str) else None
