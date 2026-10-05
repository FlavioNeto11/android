"""30.83: a referência pública do fluxo é aleatória e não sai do resumo literal.

Achado S1 da leitura do 30.80 B (herdado do 30.21): o id do fluxo era o slug do `plan.summary`, e um resumo como
"Enviar mensagem para @maria_souza" virava `enviar-mensagem-para-maria-souza…`, que saía em evento, `href` e log.
O desenho aprovado pela orquestradora pede `f-` mais 12 hex ALEATÓRIOS (nada derivável do nome), o fluxo novo com
`id = ref_publico`, e os que já existem preenchidos uma vez na subida (migração 116 só cria a coluna).
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from functools import partial
from pathlib import Path

import httpx
import pytest

from app.db import Database
from app.main import create_app
from app.models import Plan, PlannerInfo, PlanStep, Postcondition
from app.modules.learning.domain.espera import AvisoDeEspera, Faixa
from app.modules.learning.infrastructure.eventos import EventosNoBarramento
from app.taskqueue.flows import FlowStore, id_do_fluxo, preencher_refs_publicas, ref_publica_do_fluxo
from app.util import now_iso

from .conftest import Harness
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


# ------------------------------------------------------------------ fatia 2: o que sai e o que entra
class _Bus:
    def __init__(self) -> None:
        self.eventos: list[tuple[str, str, dict[str, object]]] = []

    def emit(self, kind: str, message: str, *, level: str = "info", instance_id: str | None = None,
             data: dict[str, object] | None = None) -> object:
        self.eventos.append((kind, message, dict(data or {})))
        return None


def test_as_duas_traducoes_aceitam_o_id_e_a_referencia(db: Database) -> None:
    _legado(db, "enviar-mensagem-para-maria-souza", "mande uma mensagem a maria")
    preencher_refs_publicas(db)
    ref = ref_publica_do_fluxo(db, "enviar-mensagem-para-maria-souza")
    assert FORMA.fullmatch(ref)
    antigo = "enviar-mensagem-para-maria-souza"
    assert id_do_fluxo(db, ref) == id_do_fluxo(db, antigo) == antigo
    assert id_do_fluxo(db, "f-ffffffffffff") == "f-ffffffffffff"                 # desconhecida: o 404 de sempre
    assert FORMA.fullmatch(ref_publica_do_fluxo(db, "sem-linha"))               # N2: nunca cai no id


def test_o_fluxo_sem_referencia_ganha_na_hora_e_nunca_sai_o_id(db: Database) -> None:
    _legado(db, "enviar-mensagem-para-maria-souza", "mande uma mensagem a maria")   # réplica no código antigo
    ref = ref_publica_do_fluxo(db, "enviar-mensagem-para-maria-souza")
    assert FORMA.fullmatch(ref) and "maria" not in ref
    assert db.scalar("SELECT ref_publico FROM flows WHERE id=?", ("enviar-mensagem-para-maria-souza",)) == ref
    assert ref_publica_do_fluxo(db, "enviar-mensagem-para-maria-souza") == ref      # a mesma nas próximas


def test_o_aviso_do_fluxo_sai_com_a_referencia_publica_e_sem_o_id_na_mensagem(db: Database) -> None:
    _legado(db, "enviar-mensagem-para-maria-souza", "mande uma mensagem a maria")
    preencher_refs_publicas(db)
    bus = _Bus()
    porta = EventosNoBarramento(bus, partial(ref_publica_do_fluxo, db))
    porta.esperando_a_pessoa(AvisoDeEspera(kind="fluxo", ref="enviar-mensagem-para-maria-souza", app="instagram",
                                           faixa=Faixa.C, aguardando=True, motivo="classe_c", desde=now_iso()))
    porta.esperando_a_pessoa(AvisoDeEspera(kind="receita", ref="42", app="instagram", faixa=Faixa.B,
                                           aguardando=True, motivo="classe_b", desde=now_iso()))
    (_, msg_fluxo, dados_fluxo), (_, msg_receita, dados_receita) = bus.eventos
    publica = ref_publica_do_fluxo(db, "enviar-mensagem-para-maria-souza")
    assert dados_fluxo["ref"] == publica and str(dados_fluxo["href"]).endswith(f"item=fluxo:{publica}")
    assert "maria" not in msg_fluxo and "maria" not in str(dados_fluxo)        # nem na mensagem, que vai ao log
    assert dados_receita["ref"] == "42" and "receita 42" in msg_receita       # a receita não muda (id só dígitos)


async def test_a_rota_do_livro_abre_o_fluxo_pela_referencia_publica(harness: Harness) -> None:
    db = harness.state.db
    _legado(db, "enviar-mensagem-para-maria-souza", "mande uma mensagem a maria")
    preencher_refs_publicas(db)
    publica = ref_publica_do_fluxo(db, "enviar-mensagem-para-maria-souza")
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        pelo_id = await c.get("/api/aprendizado/fluxo/enviar-mensagem-para-maria-souza")
        pela_ref = await c.get(f"/api/aprendizado/fluxo/{publica}")
        sem = await c.get("/api/aprendizado/fluxo/f-ffffffffffff")
    assert pelo_id.status_code == pela_ref.status_code == 200, (pelo_id.text, pela_ref.text)
    assert pela_ref.json()["item"]["ref"] == pelo_id.json()["item"]["ref"]
    assert sem.status_code == 404
