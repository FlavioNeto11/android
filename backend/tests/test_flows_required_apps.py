"""29.42: `GET /api/flows` (`FlowStore.list`) devolve `required_apps` NA ORDEM EM QUE O PLANO USA os apps, não a
alfabética da tabela `flow_required_apps` (que não guarda ordem). Simulado: banco SQLite de teste, sem aparelho."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.db import Database
from app.modules.learning.domain.conteudo import fluxo_legivel
from app.modules.learning.domain.livro import apps_na_ordem_do_plano
from app.taskqueue.flows import FlowStore

from .fake_skills import PLANO_CURTIR, banco, fluxo


@pytest.fixture
def db(tmp_path: Path) -> Any:
    d = banco(tmp_path)
    for app_id, nome in (("qa-messenger", "QA Messenger"), ("chrome", "Chrome"), ("notas", "Notas")):
        d.execute("INSERT INTO apps(id, name, package, builtin) VALUES (?,?,?,0)", (app_id, nome, f"pkg.{app_id}"))
    yield d
    d.close()


def _plano(*apps: str | None, ancora: str | None = None, **extra: object) -> dict[str, object]:
    etapas = [{"key": f"e{i}", "title": "t", "goal": "g", **({"app_id": a} if a else {})}
              for i, a in enumerate(apps)]
    return {"summary": "s", "app_id": ancora, "parameters": {}, "steps": etapas,
            "planner": {"provider": "fluxo", "model": "m", "simulated": True}, **extra}


def _por_id(db: Database) -> dict[str, dict[str, Any]]:
    return {f["id"]: f for f in FlowStore(db).list()}


def test_ordem_do_plano_e_nao_a_alfabetica(db: Database) -> None:
    # O plano abre o QA Messenger e DEPOIS o Chrome; alfabeticamente seria ["chrome", "qa-messenger"].
    fluxo(db, "f1", "mande {x}", plano=_plano("qa-messenger", "chrome", "qa-messenger"),
          apps=("chrome", "qa-messenger"))
    assert _por_id(db)["f1"]["required_apps"] == ["qa-messenger", "chrome"]


def test_etapa_sem_app_roda_no_app_ancora_do_plano(db: Database) -> None:
    fluxo(db, "f1", "mande {x}", plano=_plano(None, "chrome", ancora="qa-messenger"), apps=("chrome", "qa-messenger"))
    assert _por_id(db)["f1"]["required_apps"] == ["qa-messenger", "chrome"]


def test_exigido_fora_do_plano_vai_ao_fim_em_ordem_estavel(db: Database) -> None:
    fluxo(db, "f1", "mande {x}", plano=_plano("qa-messenger"), apps=("notas", "chrome", "qa-messenger"))
    assert _por_id(db)["f1"]["required_apps"] == ["qa-messenger", "chrome", "notas"]


def test_app_do_plano_fora_da_tabela_nao_entra(db: Database) -> None:
    fluxo(db, "f1", "mande {x}", plano=_plano("chrome", "qa-messenger"), apps=("qa-messenger",))
    assert _por_id(db)["f1"]["required_apps"] == ["qa-messenger"]


def test_fluxo_sem_apps_devolve_lista_vazia(db: Database) -> None:
    fluxo(db, "f1", "mande {x}", plano=_plano("chrome"), apps=())
    assert _por_id(db)["f1"]["required_apps"] == []


def test_sem_linhas_na_tabela_vale_o_congelado_no_plano(db: Database) -> None:
    fluxo(db, "f1", "mande {x}", plano=_plano("qa-messenger", "chrome", required_apps=["chrome", "qa-messenger"]),
          apps=())
    assert _por_id(db)["f1"]["required_apps"] == ["qa-messenger", "chrome"]


def test_plano_quebrado_nao_derruba_a_lista(db: Database) -> None:
    fluxo(db, "f1", "mande {x}", apps=("qa-messenger", "chrome"))
    db.execute("UPDATE flows SET plan='{nao e json' WHERE id='f1'")
    assert _por_id(db)["f1"]["required_apps"] == ["chrome", "qa-messenger"]    # sem plano: ordem estável da tabela


def test_lista_nao_expoe_o_plano_e_mantem_os_campos_de_antes(db: Database) -> None:
    fluxo(db, "f1", "curtir o post de {perfil}", plano=PLANO_CURTIR, apps=("chrome",))
    f = _por_id(db)["f1"]
    assert f["plan"] is None
    assert {"id", "name", "command_template", "app_id", "source_run_id", "status", "uses", "created_at",
            "last_used_at", "required_apps"} <= set(f)


def test_lista_toda_com_poucas_consultas(db: Database) -> None:
    for i in range(5):
        fluxo(db, f"f{i}", f"mande {{x}} {i}", plano=_plano("qa-messenger", "chrome"), apps=("chrome", "qa-messenger"))
    consultas: list[str] = []
    original = db.query
    db.query = lambda sql, *a, **k: (consultas.append(sql), original(sql, *a, **k))[1]   # type: ignore[method-assign]
    FlowStore(db).list()
    assert len(consultas) == 2


def test_aba_do_curador_usa_a_mesma_ordem() -> None:
    c = fluxo_legivel(_plano("qa-messenger", "chrome"), nome="n", comando_modelo="c", fonte=None, source_run_id=None,
                      apps=["chrome", "qa-messenger"])
    assert c["apps"] == ["qa-messenger", "chrome"]


def test_funcao_pura_ignora_lixo() -> None:
    assert apps_na_ordem_do_plano(None, ["b", "a"]) == ["a", "b"]
    assert apps_na_ordem_do_plano({"steps": "x", "required_apps": 3}, []) == []
    assert apps_na_ordem_do_plano({"steps": [1, {"app_id": "b"}, {"app_id": "a"}]}, ["a", "b", "a"]) == ["b", "a"]
