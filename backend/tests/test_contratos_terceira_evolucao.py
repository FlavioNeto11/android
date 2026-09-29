"""Contratos da Onda 0 da terceira evolução (C1–C5): só a FORMA, compatível para trás.

O que se prova aqui, nos dois bancos (o PostgreSQL quando `TEST_DATABASE_URL` existe):
- as migrações 056 e 057 aplicam do zero e sobre um banco em 055, com o mesmo esquema nos dois caminhos, e os CHECKs
  dos vocabulários recusam o que está fora;
- C1: `account_id` é o primeiro parâmetro nomeado de `ensure_session`, com padrão `None` (a forma nos dublês é do
  `test_dubles_cumprem_as_portas.py`);
- C2: `PlanStep.saidas` validado e fora do JSON quando vazio; `saidas` sobrevive ao ciclo plano → linha → versão;
  `save_step_output`/`step_outputs` com a última escrita vencendo e as recusas de nome, tamanho e tipo;
- C3: os DTOs de rede recusam valor fora dos vocabulários, campo desconhecido e segredo em `params`; `device.network`
  é verbo de app, recusado na quarentena;
- C4: `rede_gate` com motivo segura o objetivo em `pending` com `wait_reason='rede'` sem tocar o aparelho; `None`
  (no portão ou na resposta dele) não muda nada;
- C5: `app_ids` ao lado de `app_id`, que segue sendo o primeiro.

Nível de prova: `simulated` (banco de teste, aparelho e provedor falsos).
"""
from __future__ import annotations

import inspect
import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from app.commands.despacho import APP_COMMAND_VERBS, VERBOS_DE_APP_NA_QUARENTENA
from app.db import INTEGRITY_ERRORS, Database
from app.events import EventBus
from app.integrations.app_declarado.sessao import SessaoDeclarada
from app.models import (DeviceNetworkDTO, NetworkMeasurementDTO, NetworkPolicy, NetworkProfileDTO, NetworkState, Plan,
                        PlannerInfo, PlanStep, Postcondition, ResolvedTargetDTO, RunCreate)
from app.modules.identity.application.ports import SessionProvider
from app.taskqueue.executor import Outcome, StepOutcome
from app.taskqueue.orquestrador import RunTargetsSuggestion
from app.taskqueue.repository import Repository

from .conftest import Harness
from .test_db import _banco, _copia_das_migracoes, _tem_indice

NOVAS = ("056_saidas_de_etapa", "057_rede_por_aparelho")
TABELAS_NOVAS = ("step_outputs", "network_profiles", "device_network", "network_measurements")
INDICES_NOVOS = ("ix_step_outputs_step", "ix_network_measurements_instance")
TS = "2026-09-29T12:00:00Z"


# ================================================================== migrações 056 e 057
def _esquema(db: Database) -> dict[str, list[str]]:
    return {t: sorted(db.columns(t)) for t in (*TABELAS_NOVAS, "steps")}


def test_migracoes_aplicam_do_zero_e_sobre_055_com_o_mesmo_esquema(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate="055_aprendizado_continuo")
    atualizado = _banco(tmp_path, "atualizado.sqlite3")
    novo: Database | None = None
    try:
        atualizado.migrate()
        origem = Path(__file__).resolve().parents[1] / "migrations"
        for nome in NOVAS:
            shutil.copy2(origem / f"{nome}.sql", destino / f"{nome}.sql")
        assert atualizado.migrate() == list(NOVAS)
        assert atualizado.divergencias() == [] and atualizado.migrate() == []
        for nome in NOVAS:
            linha = atualizado.one("SELECT checksum FROM schema_migrations WHERE version=?", (nome,))
            assert linha is not None and len(linha["checksum"]) == 64
        novo = _banco(tmp_path, "novo.sqlite3")
        assert novo.migrate()[-2:] == list(NOVAS)
        assert _esquema(novo) == _esquema(atualizado)
        for db in (novo, atualizado):
            assert set(TABELAS_NOVAS) <= db.tables()
            assert "saidas" in db.columns("steps")
            assert all(_tem_indice(db, i) for i in INDICES_NOVOS), [i for i in INDICES_NOVOS if not _tem_indice(db, i)]
            # as tabelas da 041 ficam
            assert {"proxy_profiles", "device_proxy_state"} <= db.tables()
    finally:
        atualizado.close()
        if novo is not None:
            novo.close()


@pytest.fixture
def banco(tmp_path: Path) -> Iterator[Database]:
    db = _banco(tmp_path)
    db.migrate()
    yield db
    db.close()


def test_os_checks_da_057_batem_com_os_vocabularios_dos_dtos(banco: Database) -> None:
    banco.execute("INSERT INTO network_profiles(id, name, kind, protocol, endpoint_host, endpoint_port, created_at)"
                  " VALUES ('np-1','vpn-a','vpn','wireguard','vpn.exemplo.test',51820,?)", (TS,))
    for n, politica in enumerate(get_args(NetworkPolicy)):
        for m, estado in enumerate(get_args(NetworkState)):
            banco.execute("INSERT INTO device_network(instance_id, vpn_profile_id, policy, state, updated_at)"
                          " VALUES (?,?,?,?,?)", (f"android-{n}{m}", "np-1", politica, estado, TS))
    banco.execute("INSERT INTO device_network(instance_id, updated_at) VALUES ('android-99', ?)", (TS,))
    assert banco.one("SELECT policy, state, desired_rev, applied_rev FROM device_network WHERE instance_id='android-99'"
                     ) == {"policy": "livre", "state": "pendente", "desired_rev": 0, "applied_rev": None}
    recusas = [
        ("INSERT INTO device_network(instance_id, state, updated_at) VALUES ('x1','ok',?)", (TS,)),
        ("INSERT INTO device_network(instance_id, policy, updated_at) VALUES ('x2','obrigatoria',?)", (TS,)),
        ("INSERT INTO network_profiles(id, name, kind, protocol, endpoint_host, endpoint_port, created_at)"
         " VALUES ('np-2','b','ipsec','wireguard','h',1,?)", (TS,)),
        ("INSERT INTO network_profiles(id, name, kind, protocol, endpoint_host, endpoint_port, created_at)"
         " VALUES ('np-3','c','proxy','openvpn','h',1,?)", (TS,)),
        ("INSERT INTO network_profiles(id, name, kind, protocol, endpoint_host, endpoint_port, created_at)"
         " VALUES ('np-4','d','proxy','http','h',70000,?)", (TS,)),
        ("INSERT INTO network_profiles(id, name, kind, protocol, endpoint_host, endpoint_port, created_at)"
         " VALUES ('np-5','vpn-a','proxy','http','h',8080,?)", (TS,)),                    # nome repetido
        ("INSERT INTO network_measurements(instance_id, measured_at, method, udp_ok) VALUES ('a',?, 'x', 2)", (TS,)),
    ]
    for sql, params in recusas:
        with pytest.raises(INTEGRITY_ERRORS):
            banco.execute(sql, params)


def test_perfil_em_uso_nao_se_apaga(banco: Database) -> None:
    banco.execute("INSERT INTO network_profiles(id, name, kind, protocol, endpoint_host, endpoint_port, created_at)"
                  " VALUES ('np-1','vpn-a','vpn','wireguard','vpn.exemplo.test',51820,?)", (TS,))
    banco.execute("INSERT INTO device_network(instance_id, vpn_profile_id, updated_at) VALUES ('android-01','np-1',?)",
                  (TS,))
    with pytest.raises(INTEGRITY_ERRORS):
        banco.execute("DELETE FROM network_profiles WHERE id='np-1'")


# ================================================================== C1 — sessão por conta
def test_account_id_e_o_primeiro_nomeado_de_ensure_session_com_padrao_none() -> None:
    for fn in (SessionProvider.ensure_session, SessaoDeclarada.ensure_session):
        nomeados = [p for p in inspect.signature(fn).parameters.values() if p.kind is inspect.Parameter.KEYWORD_ONLY]
        assert [p.name for p in nomeados] == ["account_id", "force_login", "automatic", "observe_only"], fn
        assert nomeados[0].default is None


# ================================================================== C2 — saídas de etapa
def _etapa(key: str = "ler_codigo", **kw: object) -> PlanStep:
    base: dict[str, object] = {"title": "Ler o código", "goal": "Ler o código recebido.",
                               "postcondition": Postcondition(kind="text_visible", value="Código",
                                                              description="código visível")}
    base.update(kw)
    return PlanStep(key=key, **base)  # type: ignore[arg-type]


def test_plan_step_saidas_validado_e_fora_do_json_quando_vazio() -> None:
    assert "saidas" not in _etapa().model_dump(mode="json")
    assert "saidas" not in json.loads(_etapa().model_dump_json())
    assert _etapa(saidas=["codigo", "link_2"]).model_dump(mode="json")["saidas"] == ["codigo", "link_2"]
    for ruim in (["Codigo"], ["1abc"], ["com-hifen"], ["a" * 41], [""], ["codigo", "codigo"]):
        with pytest.raises(ValidationError):
            _etapa(saidas=ruim)
    assert _etapa(saidas=["a" * 40]).saidas == ["a" * 40]


def _repo_com_etapa(db: Database, tmp_path: Path) -> tuple[Repository, str, str, str]:
    repo = Repository(db, EventBus(db), tmp_path / "evidencias")
    run, _ = repo.create_run(RunCreate(command="leia o código", instance_ids=["android-01"],
                                       idempotency_key="k-saidas"), simulated=True)
    plano = Plan(summary="ler", steps=[_etapa(saidas=["codigo"]),
                                       _etapa("abrir", title="Abrir", goal="Usar {{saida:codigo}} em {instance_id}")],
                 planner=PlannerInfo(provider="teste", model="teste", simulated=True))
    repo.save_plan(run["id"], plano)
    repo.materialize(run["id"], plano, [{"instance_id": "android-01", "variables": {}}])
    oid = f"{run['id']}:android-01"
    return repo, run["id"], oid, f"{run['id']}:android-01:v1:ler_codigo"


def test_saidas_sobrevivem_ao_ciclo_do_plano(banco: Database, tmp_path: Path) -> None:
    repo, run_id, oid, sid = _repo_com_etapa(banco, tmp_path)
    assert json.loads(repo.step_row(sid)["saidas"]) == ["codigo"]
    assert repo.step_row(f"{oid}:v1:abrir")["saidas"] is None                     # etapa sem saída: NULL, como o legado
    # a referência do contrato atravessa a gravação intacta (quem a resolve é o executor, 24.3); a variável comum não
    assert repo.step_row(f"{oid}:v1:abrir")["goal"] == "Usar {{saida:codigo}} em android-01"
    detalhe = repo.run_detail(run_id)
    assert detalhe is not None and detalhe.plan is not None
    assert detalhe.plan.steps[0].saidas == ["codigo"] and detalhe.plan.steps[1].saidas == []
    assert detalhe.plan_versions[0].steps[0].saidas == ["codigo"]


def test_save_step_output_ultima_escrita_vence_e_recusa_o_que_esta_fora(banco: Database, tmp_path: Path) -> None:
    repo, _run_id, oid, sid = _repo_com_etapa(banco, tmp_path)
    assert repo.step_outputs(oid) == {}
    repo.save_step_output(sid, "codigo", "123456", value_kind="number", app_id="outlook")
    repo.save_step_output(sid, "codigo", "654321", value_kind="number", app_id="outlook")
    repo.save_step_output(sid, "link", "x" * 2000, value_kind="url")
    assert repo.step_outputs(oid) == {"codigo": "654321", "link": "x" * 2000}
    assert banco.scalar("SELECT COUNT(*) FROM step_outputs WHERE objective_id=?", (oid,)) == 2
    linha = banco.one("SELECT value_kind, app_id, step_id FROM step_outputs WHERE name='codigo'")
    assert linha == {"value_kind": "number", "app_id": "outlook", "step_id": sid}
    for nome, valor, tipo in (("Codigo", "1", "text"), ("1a", "1", "text"), ("a" * 41, "1", "text"),
                              ("longo", "x" * 2001, "text"), ("tipo", "1", "bool")):
        with pytest.raises(ValueError):
            repo.save_step_output(sid, nome, valor, value_kind=tipo)
    with pytest.raises(KeyError):
        repo.save_step_output(f"{oid}:v1:nao_existe", "codigo", "1")
    assert repo.step_outputs(oid) == {"codigo": "654321", "link": "x" * 2000}     # nada das recusas foi gravado


def test_step_outcome_outputs_nasce_nulo() -> None:
    assert StepOutcome(Outcome.succeeded).outputs is None
    assert StepOutcome(Outcome.succeeded, outputs={"codigo": "1"}).outputs == {"codigo": "1"}


# ================================================================== C3 — rede por aparelho
def _perfil(**kw: object) -> NetworkProfileDTO:
    base: dict[str, object] = {"id": "np-1", "name": "vpn-a", "kind": "vpn", "protocol": "wireguard",
                               "endpoint_host": "vpn.exemplo.test", "endpoint_port": 51820, "created_at": TS}
    base.update(kw)
    return NetworkProfileDTO(**base)  # type: ignore[arg-type]


def test_dtos_de_rede_recusam_valor_fora_do_vocabulario() -> None:
    assert _perfil().has_secret is False
    for ruim in ({"kind": "ipsec"}, {"protocol": "openvpn"}, {"endpoint_port": 0}, {"endpoint_port": 70000}):
        with pytest.raises(ValidationError):
            _perfil(**ruim)
    assert DeviceNetworkDTO(instance_id="android-01", updated_at=TS).policy == "livre"
    assert DeviceNetworkDTO(instance_id="android-01", updated_at=TS).state == "pendente"
    for ruim in ({"policy": "obrigatoria"}, {"state": "ok"}, {"state": "verificado"}):
        with pytest.raises(ValidationError):
            DeviceNetworkDTO(instance_id="android-01", updated_at=TS, **ruim)  # type: ignore[arg-type]
    medida = NetworkMeasurementDTO(id=1, instance_id="android-01", measured_at=TS, method="dentro_do_aparelho")
    assert medida.udp_ok is None and medida.leak_blocked is None and medida.per_app == {}


def test_dtos_de_rede_nao_tem_campo_de_segredo() -> None:
    segredo = ("secret", "senha", "password", "private", "preshared", "token", "credential", "key")
    for dto in (NetworkProfileDTO, DeviceNetworkDTO, NetworkMeasurementDTO):
        suspeitos = [c for c in dto.model_fields if c != "has_secret" and any(s in c for s in segredo)]
        assert suspeitos == [], (dto.__name__, suspeitos)
        assert dto.model_config.get("extra") == "forbid"
    # nem a referência do cofre sai (campo desconhecido é recusado), nem segredo entra por `params`
    with pytest.raises(ValidationError):
        _perfil(secret_ref="cofre:np-1")
    for chave in ("private_key", "PresharedKey", "password", "senha", "token", "psk"):
        with pytest.raises(ValidationError):
            _perfil(params={chave: "valor-de-teste"})
    assert _perfil(params={"mtu": 1280, "public_key": "chave-publica-de-teste"}).params["mtu"] == 1280


def test_device_network_e_verbo_de_app_recusado_na_quarentena() -> None:
    assert "device.network" in APP_COMMAND_VERBS
    assert "device.network" not in VERBOS_DE_APP_NA_QUARENTENA


# ================================================================== C4 — portão de rede
async def test_rede_gate_com_motivo_segura_o_objetivo_e_sem_motivo_libera(harness: Harness) -> None:
    st = harness.state
    assert st is not None and st.scheduler.rede_gate is None                     # o padrão: sem efeito
    consultas: list[str] = []
    motivo = "aguardando a rede verificada do aparelho (política exigida)"

    def portao(iid: str) -> str | None:
        consultas.append(iid)
        return motivo if iid == "android-01" else None

    st.scheduler.rede_gate = portao
    run = harness.run(["android-01"])
    oid = f"{run.id}:android-01"
    def esperando_a_rede() -> bool:
        linha = st.db.one("SELECT wait_reason FROM objectives WHERE id=?", (oid,))   # nasce depois do planejamento
        return linha is not None and linha["wait_reason"] == "rede"

    await harness.wait(esperando_a_rede, what="espera pela rede")
    await harness.ticks(2)
    obj = st.repo.objective_row(oid)
    assert obj["status"] == "pending" and obj["status_detail"] == motivo
    assert st.db.scalar("SELECT COUNT(*) FROM attempts a JOIN steps s ON s.id=a.step_id WHERE s.run_id=?",
                        (run.id,)) == 0                                           # o aparelho não foi tocado
    assert "android-01" in consultas
    st.scheduler.rede_gate = lambda iid: None                                     # rede verificada: segue
    detalhe = await harness.wait_run(run.id)
    assert detalhe.status == "completed", [(s.key, s.status, s.status_detail) for s in detalhe.steps]


async def test_rede_gate_que_devolve_none_nao_muda_nada(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    consultas: list[str] = []
    st.scheduler.rede_gate = lambda iid: consultas.append(iid) or None  # type: ignore[func-returns-value]
    run = harness.run(["android-02"])
    detalhe = await harness.wait_run(run.id)
    assert detalhe.status == "completed", [(s.key, s.status, s.status_detail) for s in detalhe.steps]
    assert "android-02" in consultas
    assert st.repo.objective_row(f"{run.id}:android-02")["wait_reason"] != "rede"


# ================================================================== C5 — conjunto de apps
def test_app_ids_ao_lado_de_app_id_que_segue_sendo_o_primeiro() -> None:
    assert ResolvedTargetDTO(instance_id="a", origem="ui").app_ids == []
    so_um = ResolvedTargetDTO(instance_id="a", app_id="instagram", origem="ui")
    assert so_um.app_ids == ["instagram"] and so_um.model_dump()["app_ids"] == ["instagram"]
    lista = ResolvedTargetDTO(instance_id="a", app_ids=["outlook", "instagram"], origem="texto")
    assert lista.app_id == "outlook" and lista.app_ids == ["outlook", "instagram"]
    os_dois = ResolvedTargetDTO(instance_id="a", app_id="instagram", app_ids=["outlook", "instagram"], origem="ui")
    assert os_dois.app_id == "instagram" and os_dois.app_ids == ["instagram", "outlook"]
    sugestao = RunTargetsSuggestion(modo="nenhuma", app_id="instagram")
    assert sugestao.app_ids == ["instagram"] and sugestao.model_dump(mode="json")["app_ids"] == ["instagram"]
    assert RunTargetsSuggestion(modo="nenhuma").model_dump(mode="json")["app_ids"] == []
