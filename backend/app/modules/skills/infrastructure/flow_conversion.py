"""Conversão fluxo → habilidade e o caminho de volta (fase J; design §15.2, ADR-037 proposto).

Junta as duas peças: o repositório, que escreve numa transação (adoção + rascunho, ou desfazer), e o descompilador,
que dá o documento da v2 e diz o que não se representa. Mora na infraestrutura porque as duas moram: o repositório é
SQL, e o descompilador confere o documento pelo `SkillPlanCompiler` da execução, que baixa para o `Plan` legado.

Três operações, todas atrás de `skills.enabled` (a rota e, na adoção, o próprio repositório):

- `convert`: v1 publicada = o plano do fluxo (passagem direta), v2 rascunho = o documento descompilado, fluxo
  desligado. Erro da ida e volta recusa a conversão inteira (`InvalidDocument`, 422), e nada muda;
- `undo`: a publicada desabilitada, o fluxo religado exatamente como era, e os rascunhos da conversão apagados;
- `draft_from`: a v1 → v2 de uma habilidade que já tinha adotado um fluxo (fase G) sem o rascunho.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..domain.document import JsonObject
from ..domain.lifecycle import InvalidDocument
from ..domain.refs import SkillRef
from ..domain.versions import Provenance, SkillVersion, SourceKind
from .decompiler import Decompilation, DecompileIssue, PlanDecompiler, suggested_skill_id
from .sql_repository import SqlSkillRepository


@dataclass(frozen=True, slots=True)
class Conversion:
    published: SkillVersion
    draft: SkillVersion
    #: Avisos da ida e volta e do compilador (o que a v2 muda de texto, o que falta preencher): a conversão seguiu.
    warnings: tuple[DecompileIssue, ...]


@dataclass(frozen=True, slots=True)
class ConversionUndone:
    skill_id: str
    discarded_drafts: tuple[SkillRef, ...]


def _recusa(onde: str, resultado: Decompilation) -> InvalidDocument:
    return InvalidDocument(f"{onde} não se converte sem mudar o plano: {len(resultado.errors)} problema(s) na ida e "
                           "volta pelo compilador. Nada foi alterado.", tuple(i.text() for i in resultado.errors))


class FlowConverter:
    def __init__(self, repo: SqlSkillRepository, decompiler: PlanDecompiler) -> None:
        self._repo = repo
        self._decompiler = decompiler

    def skill_id_for(self, flow_id: str) -> str:
        """A habilidade que já adotou o fluxo (readoção depois de desfazer), ou o id sugerido `<app>.<fluxo>`."""
        return self._repo.adopter_id(flow_id) or suggested_skill_id(flow_id, self._repo.flow_app_id(flow_id))

    def convert(self, flow_id: str, *, by: str, skill_id: str | None = None, reason: str = "") -> Conversion:
        alvo = skill_id or self.skill_id_for(flow_id)
        avisos: list[DecompileIssue] = []

        def rascunho(v1: SkillVersion) -> JsonObject:
            definicao = self._repo.definition(alvo)
            r = self._decompiler.decompile_version(v1, skill_id=alvo,
                                                   app_id=definicao.app_id if definicao is not None else None,
                                                   description=f"Convertida do fluxo {flow_id}.")
            if not r.ok or r.document is None:
                raise _recusa(f"O fluxo {flow_id}", r)
            avisos.extend(r.warnings)
            return r.document

        v1, v2 = self._repo.convert_flow(flow_id, skill_id=alvo, by=by, draft_of=rascunho, reason=reason)
        return Conversion(v1, v2, tuple(avisos))

    def undo(self, flow_id: str, *, by: str, reason: str = "") -> ConversionUndone:
        skill_id, apagados = self._repo.undo_conversion(flow_id, by=by, reason=reason)
        return ConversionUndone(skill_id, apagados)

    def draft_from(self, ref: SkillRef, *, by: str) -> tuple[SkillVersion, tuple[DecompileIssue, ...]]:
        """v1 → v2: o rascunho da DSL a partir de uma versão de conteúdo legado já gravada (adotada na fase G)."""
        v = self._repo.get(ref)
        definicao = self._repo.definition(ref.skill_id)
        r = self._decompiler.decompile_version(v, skill_id=ref.skill_id,
                                               app_id=definicao.app_id if definicao is not None else None,
                                               description=(f"Convertida do fluxo {definicao.legacy_flow_id}."
                                                            if definicao is not None and definicao.legacy_flow_id
                                                            else None))
        if not r.ok or r.document is None:
            raise _recusa(str(ref), r)
        origem = v.provenance.ref if v.provenance.kind is SourceKind.LEGACY_FLOW else None
        rascunho = self._repo.create_draft(ref.skill_id, r.document, by=by, parent_version=ref.version,
                                           source=Provenance(kind=SourceKind.LEGACY_FLOW, ref=origem,
                                                             notes=(("decompiled_from", str(ref)),)))
        return rascunho, r.warnings
