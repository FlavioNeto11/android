"""Apoio dos testes de habilidades: banco migrado pela fábrica da suíte, relógio injetável, validador falso.

O validador verdadeiro (schema `automation/v1alpha1` + compilador) é de outra peça e ainda não existe; este lê os
mesmos caminhos da §12.2 (`metadata.*`, `spec.invocation.command_template`, `spec.requires.apps`) e devolve os
erros que o teste mandar.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

from app.db import Database
from app.modules.skills.domain.document import JsonObject, JsonValue
from app.modules.skills.domain.versions import DocumentFacts
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository
from app.taskqueue.flows import _norm

from .conftest import _dsn_de_teste

TS = "2026-09-20T12:00:00Z"


class Relogio:
    """Um instante novo a cada leitura, em ordem: a trilha de transições fica ordenável sem `sleep`."""

    def __init__(self) -> None:
        self._n = itertools.count(1)

    def __call__(self) -> str:
        return f"2026-09-27T10:{next(self._n):02d}:00Z"


class ValidadorFalso:
    def __init__(self) -> None:
        self.erros: tuple[str, ...] = ()

    def inspect(self, document: JsonObject) -> DocumentFacts:
        meta = _obj(document.get("metadata"))
        spec = _obj(document.get("spec"))
        invocacao = _obj(spec.get("invocation"))
        exige = _obj(spec.get("requires"))
        apps = exige.get("apps")
        return DocumentFacts(skill_id=_txt(meta.get("id")), name=_txt(meta.get("name")),
                             description=_txt(meta.get("description")) or "", app_id=_txt(meta.get("app")),
                             command_template=_txt(invocacao.get("command_template")),
                             app_ids=tuple(a for a in apps if isinstance(a, str)) if isinstance(apps, list) else (),
                             errors=self.erros)


def _obj(v: JsonValue) -> JsonObject:
    return v if isinstance(v, dict) else {}


def _txt(v: JsonValue) -> str | None:
    return v if isinstance(v, str) else None


def documento(skill_id: str, template: str | None, *, nota: str = "v1", app: str = "instagram",
              apps: tuple[str, ...] = ()) -> JsonObject:
    """Um documento na forma da §12.2 (o que o validador falso lê; o resto é conteúdo opaco para o repositório)."""
    invocacao: JsonObject = {"command_template": template} if template else {}
    return {"apiVersion": "automation/v1alpha1", "kind": "Skill",
            "metadata": {"id": skill_id, "name": skill_id.split(".")[-1].replace("_", " ").title(), "app": app},
            "spec": {"invocation": invocacao, "requires": {"apps": list(apps)},
                     "nodes": [{"id": "abrir", "goal": nota}]}}


def banco(tmp_path: Path, nome: str = "hab.sqlite3") -> Database:
    db = Database(_dsn_de_teste() or tmp_path / nome)
    db.migrate()
    return db


def repositorio(db: Database) -> tuple[SqlSkillRepository, ValidadorFalso]:
    validador = ValidadorFalso()
    return SqlSkillRepository(db, validador, clock=Relogio()), validador


PLANO_CURTIR = {
    "summary": "Curtir o post de {perfil}", "app_id": "instagram", "parameters": {"perfil": "{perfil}", "fixo": "x"},
    "steps": [{"key": "abrir", "title": "Abrir o perfil de {perfil}", "goal": "abrir {perfil}",
               "postcondition": {"kind": "app_foreground", "value": "instagram", "description": "app aberto"}}],
    "planner": {"provider": "fluxo", "model": "m", "simulated": True},
}


def fluxo(db: Database, flow_id: str, template: str, *, plano: dict[str, object] | None = None,
          status: str = "active", uses: int = 0, apps: tuple[str, ...] = ("instagram",), source: str = "run",
          source_run_id: str | None = "r-origem", perfis: tuple[str, ...] = (), grupos: tuple[str, ...] = (),
          criado: str = TS) -> None:
    """Um fluxo como `FlowStore` o grava (match_key normalizado), com app exigido e escopo opcionais."""
    if db.one("SELECT id FROM apps WHERE id='instagram'") is None:
        db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',"
                   "'com.instagram.android',0)")
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, source_run_id, status, uses,"
               " created_at, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (flow_id, f"Fluxo {flow_id}", _norm(template), template, json.dumps(plano or PLANO_CURTIR),
                "instagram", source_run_id, status, uses, criado, source))
    for app_id in apps:
        db.execute("INSERT INTO flow_required_apps(flow_id, app_id) VALUES (?,?)", (flow_id, app_id))
    for pid in perfis:
        db.execute("INSERT INTO flow_scope(flow_id, profile_id) VALUES (?,?)", (flow_id, pid))
    for gid in grupos:
        db.execute("INSERT INTO flow_scope(flow_id, group_id) VALUES (?,?)", (flow_id, gid))


def perfil(db: Database, pid: str, grupo: str | None = None) -> None:
    if grupo and db.one("SELECT id FROM policy_groups WHERE id=?", (grupo,)) is None:
        db.execute("INSERT INTO policy_groups(id, name, created_at, updated_at) VALUES (?,?,?,?)",
                   (grupo, grupo, TS, TS))
    db.execute("INSERT INTO instagram_profiles(id, username, created_at, updated_at, policy_group_id)"
               " VALUES (?,?,?,?,?)", (pid, pid, TS, TS, grupo))


def sem_gatilho_046(db: Database) -> None:
    """Tira a segunda camada, para provar que a primeira (o repositório) segura sozinha."""
    if db.dialect == "sqlite":
        db.execute("DROP TRIGGER IF EXISTS skill_versions_congelada")
        db.execute("DROP TRIGGER IF EXISTS skill_versions_sem_apagar")
    else:
        db.execute("DROP TRIGGER IF EXISTS skill_versions_congelada ON skill_versions")
