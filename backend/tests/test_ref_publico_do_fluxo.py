"""30.83: a referência pública do fluxo é aleatória e não sai do resumo literal.

Achado S1 da leitura do 30.80 B (herdado do 30.21): o id do fluxo era o slug do `plan.summary`, e um resumo como
"Enviar mensagem para @maria_souza" virava `enviar-mensagem-para-maria-souza…`, que saía em evento, `href` e log.
O desenho aprovado pela orquestradora pede `f-` mais 12 hex ALEATÓRIOS (nada derivável do nome), o fluxo novo com
`id = ref_publico`, e os que já existem preenchidos uma vez na subida (migração 116 só cria a coluna).
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.db import Database
from app.models import Plan, PlannerInfo, PlanStep, Postcondition
from app.taskqueue.flows import FlowStore, preencher_refs_publicas
from app.util import now_iso

from .fake_skills import banco

FORMA = re.compile(r"f-[0-9a-f]{12}")


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco(tmp_path, "ref-publico.sqlite3")
    yield d
    d.close()


def _plano(resumo: str) -> Plan:
    passo = PlanStep(key="enviar", title="Enviar", goal="enviar a mensagem", capability="SEND_DM",
                     bindings={"username": "{username}"},
                     postcondition=Postcondition(kind="text_visible", value="Enviada", description="enviada"))
    return Plan(summary=resumo, app_id="instagram", parameters={"username": "{username}"}, steps=[passo],
                planner=PlannerInfo(provider="anthropic", model="claude", simulated=False))


def _legado(db: Database, fid: str, chave: str) -> None:
    """Um fluxo de antes da migração 116: id slug, sem `ref_publico`."""
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, created_at) VALUES (?,?,?,?,?,?)",
               (fid, "legado", chave, chave, _plano("legado").model_dump_json(), now_iso()))


def test_o_fluxo_novo_tem_referencia_aleatoria_sem_nada_do_resumo(db: Database) -> None:
    fid = FlowStore(db).learn_from_plan(_plano("Enviar mensagem para @maria_souza"), "mande uma mensagem a {username}",
                                        source="training:trn-1")
    assert FORMA.fullmatch(fid) and "maria" not in fid
    linha = db.one("SELECT name, ref_publico FROM flows WHERE id=?", (fid,))
    assert linha is not None and linha["ref_publico"] == fid
    assert "maria" in linha["name"]                     # o nome legível fica na coluna `name`, que o painel mostra


def test_o_mesmo_resumo_da_referencias_diferentes(db: Database) -> None:
    flows = FlowStore(db)
    a = flows.learn_from_plan(_plano("Mandar oi"), "mande oi a {username}", source="training:trn-1")
    b = flows.learn_from_plan(_plano("Mandar oi"), "diga oi a {username}", source="training:trn-2")
    assert a != b and FORMA.fullmatch(a) and FORMA.fullmatch(b)


def test_a_subida_preenche_os_fluxos_antigos_uma_vez_sem_mudar_o_id(db: Database) -> None:
    _legado(db, "enviar-mensagem-para-maria-souza", "mande uma mensagem a maria")
    _legado(db, "curtir-post", "curta o post")
    assert preencher_refs_publicas(db) == 2
    linhas = {r["id"]: r["ref_publico"] for r in db.query("SELECT id, ref_publico FROM flows")}
    assert set(linhas) == {"enviar-mensagem-para-maria-souza", "curtir-post"}   # o id antigo fica
    assert all(FORMA.fullmatch(r) for r in linhas.values()) and len(set(linhas.values())) == 2
    assert preencher_refs_publicas(db) == 0                                    # idempotente
    assert {r["id"]: r["ref_publico"] for r in db.query("SELECT id, ref_publico FROM flows")} == linhas


def test_a_referencia_e_unica_no_banco(db: Database) -> None:
    _legado(db, "a", "comando a")
    _legado(db, "b", "comando b")
    db.execute("UPDATE flows SET ref_publico='f-000000000001' WHERE id='a'")
    with pytest.raises(Exception):  # noqa: B017 - SQLite e PostgreSQL levantam classes diferentes
        db.execute("UPDATE flows SET ref_publico='f-000000000001' WHERE id='b'")
