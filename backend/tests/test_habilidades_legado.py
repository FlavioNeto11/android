"""O fluxo legado como `flow:<id>@1` (ADR-034) e o registro com dois backends.

O mapeamento é GOLDEN: o conteúdo e o hash de um fluxo fixo ficam congelados aqui. Se o formato do conteúdo legado
mudar, o `runs.skill_hash` gravado das execuções por fluxo deixa de bater com a releitura — e este teste é quem avisa.

O `Plan` que sai do adaptador tem de ser IGUAL ao que `FlowStore.match` devolve hoje, para qualquer comando: é o
que torna a troca de `flows.match` por `registry.resolve` no `_plan` (fase G) uma troca sem efeito.

Nível de prova: `simulated` (banco de teste). Nenhum aparelho, nenhuma IA.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.db import Database
from app.modules.skills.application.registry import CompositeSkillRegistry
from app.modules.skills.domain.lifecycle import SkillNotFound, SkillState
from app.modules.skills.domain.refs import SkillRef
from app.modules.skills.domain.versions import SCHEMA_LEGACY_PLAN, Provenance, SourceKind
from app.modules.skills.infrastructure.legacy_flows import LegacyFlowAdapter, legacy_plan
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository
from app.taskqueue.flows import FlowStore

from .fake_skills import PLANO_CURTIR, TS, banco, documento, fluxo, perfil, repositorio

PESSOA = "painel:flavio"
#: Um fluxo cujo modelo casa os mesmos comandos que a habilidade `ig.curtir`, com outro nome de parâmetro.
PLANO_ALVO = {**PLANO_CURTIR, "parameters": {"alvo": "{alvo}"}}
#: sha256 do JSON canônico de `{schema_version: 0, command_template, plan, required_apps}` do fluxo `curtir`.
HASH_GOLDEN = "201bc6e3cf086e21ec4a1078662e2d0cbdf56991267c71b09f07c9a2a5a3be6e"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = banco(tmp_path)
    yield d  # type: ignore[misc]
    d.close()


def test_golden_do_mapeamento_fluxo_para_versao(db: Database) -> None:
    fluxo(db, "curtir", "curtir o post de {perfil}", source="training:trn-1", source_run_id=None)
    adaptador = LegacyFlowAdapter(db)
    v = adaptador.get(SkillRef.parse("flow:curtir@1"))
    assert str(v.ref) == "flow:curtir@1" and v.state is SkillState.PUBLISHED
    assert v.schema_version == SCHEMA_LEGACY_PLAN == 0
    assert v.document() == {"schema_version": 0, "command_template": "curtir o post de {perfil}",
                            "plan": PLANO_CURTIR, "required_apps": ["instagram"]}
    assert v.content_hash == HASH_GOLDEN
    assert v.command_template == "curtir o post de {perfil}" and v.match_key == "curtir o post de {perfil}"
    assert v.app_ids == ("instagram",) and v.parent_version is None
    assert v.provenance == Provenance(SourceKind.LEGACY_FLOW, "curtir", notes=(("source", "training:trn-1"),))
    assert (v.created_at, v.state_at, v.state_by) == (TS, TS, None)
    v.verify_integrity()
    d = adaptador.definition("flow:curtir")
    assert d is not None and (d.id, d.name, d.app_id, d.legacy_flow_id) == ("flow:curtir", "Fluxo curtir",
                                                                            "instagram", "curtir")
    # Desligado: a mesma versão, em `disabled`, e nenhuma publicada.
    db.execute("UPDATE flows SET status='disabled' WHERE id='curtir'")
    assert adaptador.get(v.ref).state is SkillState.DISABLED and adaptador.published("flow:curtir") is None
    assert adaptador.get(v.ref).content_hash == HASH_GOLDEN
    with pytest.raises(SkillNotFound):
        adaptador.get(SkillRef.legacy("sumiu"))


def test_o_plano_do_adaptador_e_o_mesmo_do_flowstore(db: Database) -> None:
    plano_fixo = {**PLANO_CURTIR, "parameters": {"perfil": "{perfil}", "alvo": "{alvo}"}}
    fluxo(db, "curtir", "curtir o post de {perfil}", uses=1)
    fluxo(db, "curtir-muito", "curtir o post de {perfil} muito", uses=5)
    fluxo(db, "precisa-alvo", "seguir {perfil}", plano=plano_fixo, uses=9)    # {alvo} fica sem valor: não casa
    fluxo(db, "so-p1", "comentar em {perfil}", perfis=("p1",))
    perfil(db, "p1")
    flows, adaptador = FlowStore(db), LegacyFlowAdapter(db)
    for comando, perfis in (("curtir o post de @nasa", None), ("Curtir  o POST de @nasa muito", ["p1"]),
                            ("comentar em @nasa", ["p1"]), ("comentar em @nasa", ["p2"]),
                            ("comentar em @nasa", None), ("seguir @nasa", None), ("nada a ver", None)):
        antes = flows.match(comando, perfis)
        achada = adaptador.resolve(comando, perfis)
        if antes is None:
            assert achada is None, comando
            continue
        assert achada is not None and achada.ref == SkillRef.legacy(antes[0]["id"]), comando
        assert legacy_plan(achada) == antes[1], comando
        assert achada.legacy_flow_id == antes[0]["id"] and achada.definition.name == antes[0]["name"]


def test_listagem_do_legado(db: Database) -> None:
    fluxo(db, "a", "a {x}")
    fluxo(db, "b", "b {x}", status="disabled", apps=())
    adaptador = LegacyFlowAdapter(db)
    assert [str(s.ref) for s in adaptador.list()] == ["flow:a@1", "flow:b@1"]
    assert [str(s.ref) for s in adaptador.list(state=SkillState.DISABLED)] == ["flow:b@1"]
    assert [str(s.ref) for s in adaptador.list(app_id="chrome")] == []
    assert all(s.intact and s.schema_version == 0 for s in adaptador.list())


# ------------------------------------------------------------------ registro: skill → fluxo → nada
def _registro(db: Database, *, skills: bool, flows: bool) -> tuple[CompositeSkillRegistry, SqlSkillRepository]:
    repo = repositorio(db)[0]
    return CompositeSkillRegistry(repo, LegacyFlowAdapter(db), skills_enabled=lambda: skills,
                                  flows_enabled=lambda: flows), repo


def _publicar(repo: SqlSkillRepository, skill_id: str, template: str) -> SkillRef:
    v = repo.create_draft(skill_id, documento(skill_id, template), source=Provenance(SourceKind.MANUAL), by=PESSOA)
    repo.transition(v.ref, SkillState.CANDIDATE, by=PESSOA, reason="submeter")
    repo.transition(v.ref, SkillState.VALIDATED, by=PESSOA, reason="teste", manual=True)
    repo.transition(v.ref, SkillState.PUBLISHED, by=PESSOA, reason="publicar")
    return v.ref


@pytest.mark.parametrize("skills, flows, esperado", [
    (True, True, "ig.curtir@1"),         # a habilidade publicada vem antes do fluxo
    (True, False, "ig.curtir@1"),
    (False, True, "flow:curtir-post@1"),  # `skills.enabled: false`: produção de hoje, com `ai.flows` mandando
    (False, False, None),                 # os dois desligados: o planejador fica com o comando
])
def test_precedencia_e_interruptores(db: Database, skills: bool, flows: bool, esperado: str | None) -> None:
    registro, repo = _registro(db, skills=skills, flows=flows)
    fluxo(db, "curtir-post", "curtir o post de {alvo}", plano=PLANO_ALVO)          # outro modelo, casa o mesmo comando
    _publicar(repo, "ig.curtir", "curtir o post de {perfil}")
    achada = registro.resolve("curtir o post de @nasa", None)
    assert (str(achada.ref) if achada else None) == esperado


def test_sem_habilidade_que_case_o_fluxo_responde(db: Database) -> None:
    registro, repo = _registro(db, skills=True, flows=True)
    fluxo(db, "seguir", "seguir {perfil}")
    _publicar(repo, "ig.curtir", "curtir o post de {perfil}")
    achada = registro.resolve("seguir @nasa", None)
    assert achada is not None and str(achada.ref) == "flow:seguir@1" and dict(achada.parameters) == {
        "perfil": "@nasa"}


def test_os_interruptores_sao_lidos_a_cada_chamada(db: Database) -> None:
    estado = {"skills": False, "flows": True}
    repo = repositorio(db)[0]
    registro = CompositeSkillRegistry(repo, LegacyFlowAdapter(db), skills_enabled=lambda: estado["skills"],
                                      flows_enabled=lambda: estado["flows"])
    fluxo(db, "curtir-post", "curtir o post de {alvo}", plano=PLANO_ALVO)
    _publicar(repo, "ig.curtir", "curtir o post de {perfil}")
    cmd = "curtir o post de @nasa"
    assert str(registro.resolve(cmd, None).ref) == "flow:curtir-post@1"  # type: ignore[union-attr]
    estado["skills"] = True
    assert str(registro.resolve(cmd, None).ref) == "ig.curtir@1"  # type: ignore[union-attr]
    estado.update(skills=False, flows=False)
    assert registro.resolve(cmd, None) is None


def test_leitura_por_nome_vai_ao_backend_certo_e_nao_depende_do_interruptor(db: Database) -> None:
    registro, repo = _registro(db, skills=False, flows=False)
    fluxo(db, "seguir", "seguir {perfil}")
    ref = _publicar(repo, "ig.curtir", "curtir o post de {perfil}")
    assert registro.get(ref).ref == ref and registro.get(SkillRef.legacy("seguir")).ref.is_legacy
    assert registro.published("ig.curtir") is not None and registro.published("flow:seguir") is not None
    assert registro.definition("flow:seguir") is not None and registro.definition("ig.curtir") is not None
    assert [str(s.ref) for s in registro.list()] == ["ig.curtir@1", "flow:seguir@1"]
