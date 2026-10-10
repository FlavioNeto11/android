"""Item 31.314: a persona de TESTE formal (migração 134, `instagram_profiles.teste`).

- API: `teste` aditivo em GET/POST/PATCH de personas (adendo v1.139), padrão falso;
- a migração 134 marca por id a persona que o Portal criou para o 31.283 e não toca em mais ninguém;
- fora do automático: a sugestão de alvos (`Orquestrador._candidatas`), a distribuição por app (`candidatos_do_app`);
- fora do em massa: o pool e a criação das operações em lote, e a contagem de contas por app do painel;
- fora dos avisos ao dono: o evento só de persona de teste não vira aviso, e a pendência dela não entra no Telegram nem no
  espelho do Trello; a execução que MISTURA persona de verdade e de teste segue avisando;
- citada pelo nome ou pelo id, num comando ou numa execução avulsa, ela serve como qualquer outra (de propósito).

Nível de prova: `simulated` (harness, banco de teste, nenhum aparelho real).
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.contracts import persona_de_teste
from app.models import InstanceState, ProfileCreate
from app.modules.avisos.infrastructure.portas_da_central import PortasReais
from app.modules.avisos.infrastructure.servico import ServicoDeAvisos
from app.modules.execution.application.alvos import Mundo, Vinculo
from app.modules.operacoes.infrastructure.servico import PERSONA_DE_TESTE, AlvoPedido
from app.taskqueue.orquestrador import Orquestrador
from app.util import now_iso

from .conftest import Harness
from .test_conta_planejada import _api
from .test_operacoes import APP, _conta, _persona, _servico

MIGRACAO = Path(__file__).resolve().parents[1] / "migrations" / "134_persona_de_teste.sql"
ID_DO_PORTAL = "ig-d3n4tia1rHELrY10"


def _marcar(h: Harness, pid: str, teste: bool = True) -> None:
    assert h.state is not None
    h.state.db.execute("UPDATE instagram_profiles SET teste=? WHERE id=?", (1 if teste else 0, pid))


# ------------------------------------------------------------------------------------------------ API e migração
async def test_a_api_cria_le_e_corrige_a_marca_de_teste(harness: Harness) -> None:
    async with _api(harness) as c:
        r = await c.post("/api/personas", json={"name": "Teste Nome", "teste": True})
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        assert r.json()["teste"] is True
        outra = (await c.post("/api/personas", json={"name": "Pessoa Comum"})).json()
        assert outra["teste"] is False                                           # padrão: pessoa de verdade
        assert (await c.get(f"/api/personas/{pid}")).json()["teste"] is True
        lista = {p["id"]: p["teste"] for p in (await c.get("/api/personas")).json()}
        assert lista[pid] is True and lista[outra["id"]] is False
        r = await c.patch(f"/api/personas/{pid}", json={"teste": False})
        assert r.status_code == 200 and r.json()["teste"] is False
        r = await c.patch(f"/api/personas/{pid}", json={"summary": "x"})              # outro campo não mexe na marca
        assert r.json()["teste"] is False
        r = await c.patch(f"/api/personas/{pid}", json={"teste": True})
        assert r.json()["teste"] is True
        r = await c.patch(f"/api/personas/{pid}", json={"teste": None, "summary": "y"})   # null = não mexer
        assert r.json()["teste"] is True
        r = await c.patch(f"/api/personas/{pid}", json={"teste": "talvez"})
        assert r.status_code == 422


async def test_a_migracao_134_marca_so_a_persona_do_portal(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    texto = MIGRACAO.read_text(encoding="utf-8")
    assert "ADD COLUMN teste INTEGER NOT NULL DEFAULT 0" in texto
    update = re.search(r"^UPDATE instagram_profiles SET teste = 1 WHERE id = '([^']+)';", texto, re.M)
    assert update is not None and update.group(1) == ID_DO_PORTAL
    agora = now_iso()
    for pid in (ID_DO_PORTAL, "ig-outra"):
        st.db.execute("INSERT INTO instagram_profiles(id, username, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                      (pid, "", "active", agora, agora))
    st.db.execute(update.group(0).rstrip(";"))
    assert st.db.scalar("SELECT teste FROM instagram_profiles WHERE id=?", (ID_DO_PORTAL,)) == 1
    assert st.db.scalar("SELECT teste FROM instagram_profiles WHERE id='ig-outra'") == 0


def test_o_fragmento_de_sql_e_a_regra_de_leitura() -> None:
    assert persona_de_teste.sem_teste("p") == "COALESCE(p.teste, 0) = 0"
    assert persona_de_teste.so_teste() == "COALESCE(teste, 0) = 1"
    assert persona_de_teste.e_de_teste({"teste": 1}) and not persona_de_teste.e_de_teste({"teste": 0})
    assert not persona_de_teste.e_de_teste(None) and not persona_de_teste.e_de_teste({})


# ------------------------------------------------------------------------------------------------ seleção automática
def test_a_sugestao_automatica_nao_escolhe_persona_de_teste() -> None:
    vinculos = (Vinculo("p-real", "android-01", frozenset({"x"}), True), Vinculo("p-teste", "android-02", frozenset({"x"}), True))
    mundo = Mundo(vinculos=vinculos, aptos=frozenset({"android-01", "android-02"}), de_teste=frozenset({"p-teste"}))
    assert Orquestrador._candidatas(mundo, []) == {"p-real": ["android-01"]}          # noqa: SLF001
    assert set(Orquestrador._candidatas(Mundo(vinculos=vinculos, aptos=mundo.aptos), [])) == {"p-real", "p-teste"}   # noqa: SLF001


async def test_o_mundo_do_servico_traz_as_personas_de_teste(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    teste, comum = _persona(harness, "Tadeu", "android-01"), _persona(harness, "Clara", "android-02")
    _marcar(harness, teste)
    mundo = st.runs._mundo([])                                                           # noqa: SLF001
    assert mundo.de_teste == frozenset({teste}) and comum not in mundo.de_teste


async def test_aparelho_so_da_persona_de_teste_nao_e_candidato_da_distribuicao_por_app(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    ig = st.db.scalar("SELECT id FROM apps WHERE package='com.instagram.android'")
    if ig is None:
        pytest.skip("catálogo de teste sem o app do Instagram")
    st.db.execute("UPDATE instances SET app_id=?", (ig,))
    for rt in st.devices.devices.values():
        rt.state = InstanceState.online
    pid = _persona(harness, "Teo", "android-02")
    agora = now_iso()
    st.db.execute("INSERT INTO device_profile_bindings(profile_id, instance_id, active, bound_at) VALUES (?,?,1,?)",
                  (pid, "android-02", agora))
    assert [c.instance_id for c in st.scheduler.candidatos_do_app(ig)] == ["android-02"]
    _marcar(harness, pid)
    assert st.scheduler.candidatos_do_app(ig) == []
    _marcar(harness, pid, False)
    assert [c.instance_id for c in st.scheduler.candidatos_do_app(ig)] == ["android-02"]


# ------------------------------------------------------------------------------------------------ em massa
async def test_operacao_em_lote_nao_inclui_persona_de_teste(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    teste, comum = _persona(harness, "Tati", "android-01"), _persona(harness, "Cida", "android-02")
    for pid, handle in ((teste, "qa-user-t1"), (comum, "qa-user-c1")):
        _conta(harness, pid, handle, sessao_em="android-01" if pid == teste else "android-02")
    _marcar(harness, teste)
    s = _servico(harness)
    monkeypatch.setattr(s, "_aparelho_apto", lambda iid: True)
    ids = {i["profile_id"] for i in s.elegiveis(APP)["itens"]}                              # type: ignore[union-attr,index]
    assert comum in ids and teste not in ids
    parada, motivo, conta, _ = s._conferir(AlvoPedido(teste), APP)                           # noqa: SLF001
    assert (parada, motivo, conta) == ("persona", PERSONA_DE_TESTE, None)                     # nem explícita, em lote
    assert s._conferir(AlvoPedido(comum), APP)[0] is None                                    # noqa: SLF001


async def test_a_contagem_de_contas_do_painel_nao_soma_persona_de_teste(harness: Harness) -> None:
    from app.apps_overview import apps_overview

    st = harness.state
    assert st is not None
    teste, comum = _persona(harness, "Tuca", "android-01"), _persona(harness, "Dora", "android-02")
    _conta(harness, teste, "qa-user-t2")
    _conta(harness, comum, "qa-user-c2")
    antes = {a["app_id"]: a["accounts"] for a in apps_overview(st)}[APP]
    _marcar(harness, teste)
    depois = {a["app_id"]: a["accounts"] for a in apps_overview(st)}[APP]
    assert antes - depois == 1


# ------------------------------------------------------------------------------------------------ avisos ao dono
def _avisos(harness: Harness) -> ServicoDeAvisos:
    st = harness.state
    assert st is not None
    servico = object.__new__(ServicoDeAvisos)
    servico.fila = SimpleNamespace(db=st.db)                                                   # type: ignore[assignment]
    return servico


async def test_evento_so_de_persona_de_teste_nao_vira_aviso(harness: Harness) -> None:
    teste, comum = _persona(harness, "Tito"), _persona(harness, "Elis")
    _marcar(harness, teste)
    s = _avisos(harness)
    assert s._e_de_persona_de_teste("session.needs_person", {"profile_id": teste})            # noqa: SLF001
    assert not s._e_de_persona_de_teste("session.needs_person", {"profile_id": comum})        # noqa: SLF001
    assert s._e_de_persona_de_teste("objective.updated", {"objective": {"profile_id": teste}})   # noqa: SLF001
    assert s._e_de_persona_de_teste("approval.pending", {"approval": {"profile_id": teste}})    # noqa: SLF001
    assert not s._e_de_persona_de_teste("pedido.aviso", {"profile_id": teste})                # tipo que nunca olha perfil
    assert not s._e_de_persona_de_teste("session.needs_person", {})                           # sem perfil: avisa
    assert not s._e_de_persona_de_teste("session.needs_person", {"profile_id": "inexistente"})   # noqa: SLF001


async def test_execucao_so_de_teste_cala_e_a_mista_segue_avisando(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    teste, comum = _persona(harness, "Tula", "android-01"), _persona(harness, "Fabi", "android-02")
    _marcar(harness, teste)
    agora = now_iso()
    for run, perfis in (("r-so-teste", [teste]), ("r-misto", [teste, comum]), ("r-real", [comum])):
        st.db.execute("INSERT INTO runs(id, command, mode, status, idempotency_key, instance_ids, created_at)"
                      " VALUES (?,?,'one','needs_input',?,'[]',?)", (run, "x", f"k-{run}", agora))
        for i, p in enumerate(perfis):
            # `id` explícito, como o repositório (`{run}:{instância}`): no PostgreSQL a coluna não tem default.
            st.db.execute("INSERT INTO objectives(id, run_id, instance_id, profile_id, status) VALUES (?,?,?,?,'pending')",
                          (f"{run}:android-0{i + 1}", run, f"android-0{i + 1}", p))
    s = _avisos(harness)
    assert s._e_de_persona_de_teste("run.updated", {"run": {"id": "r-so-teste"}})             # noqa: SLF001
    assert not s._e_de_persona_de_teste("run.updated", {"run": {"id": "r-misto"}})            # noqa: SLF001
    assert not s._e_de_persona_de_teste("run.updated", {"run": {"id": "r-real"}})             # noqa: SLF001
    portas: Any = object.__new__(PortasReais)
    portas.db = st.db
    portas.aprovacoes = SimpleNamespace(list=lambda **kw: [
        {"id": "a-teste", "profile_id": teste, "summary": "s", "capability": "c", "target": "t"},
        {"id": "a-real", "profile_id": comum, "summary": "s", "capability": "c", "target": "t"}])
    assert set(portas.execucoes_esperando()) == {"r-misto", "r-real"}
    quem = {(p.tipo, p.ident) for p in portas.pendencias()}
    assert ("pergunta", "r-so-teste") not in quem and ("pergunta", "r-misto") in quem and ("pergunta", "r-real") in quem
    assert ("aprovacao", "a-teste") not in quem and ("aprovacao", "a-real") in quem


# ------------------------------------------------------------------------------------------------ uso de propósito
async def test_citada_pelo_nome_a_persona_de_teste_serve_como_qualquer_outra(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    teste = _persona(harness, "Zeca", "android-01")
    _marcar(harness, teste)
    mundo = st.runs._mundo([])                                                               # noqa: SLF001
    assert teste in mundo.de_teste
    assert teste in {v.profile_id for v in mundo.vinculos}            # o vínculo segue: o resolvedor de alvos explícito o acha
    assert mundo.aparelhos_de(teste) == ["android-01"]
