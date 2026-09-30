"""Item 24.5 (ADR-058): o roteamento de um comando entre apps olha o CONJUNTO de apps, não um app só.

O que se prova:
- `RunService._app_do_comando` devolve o conjunto: os apps dos alvos, os do plano da habilidade casada (na ordem do
  plano) e, sem nada casado, os apps citados — todos quando são dois ou mais; um só quando é app de conta;
- `alvos.Mundo.serve` exige, NO MESMO aparelho, vínculo com todos os apps de conta do conjunto (a união dos vínculos
  por app do par); app sem conta (Chrome) não tira ninguém; um app só segue com a regra de antes;
- `resolver_alvos` escolhe o aparelho e a persona que servem ao conjunto, e cada alvo leva `app_ids`;
- `_mundo`: apto é o aparelho sem nenhum app do conjunto sabidamente fora de pronto; sessão pronta é a dos apps de
  login gerenciado do conjunto;
- `_mistura_de_apps` não recusa aparelhos de apps diferentes quando o comando diz os apps;
- `_incompativeis` e o pré-voo do aplicativo conferem cada app do conjunto além do principal; `start` confere os
  `required_apps` do plano;
- o desbravador compara a chave de CADA app das etapas que faltam;
- o modo Automático só sugere a persona com conta em todos os apps de conta do pedido.

Nível de prova: `simulated` (harness na porta 5640, aparelhos falsos, planejador e orquestrador simulados).
"""
from __future__ import annotations

import json
import secrets as pysecrets
from typing import Any

import pytest

from app.models import (PersonaDeviceBody, Plan, PlannerInfo, PlanStep, Postcondition, ProfileCreate, RunCreate,
                        RunTarget, SessionStatus)
from app.modules.execution.application.alvos import (AlvoPedido, DicasDoTexto, Mundo, PedidoDeAlvos, Vinculo,
                                                     resolver_alvos)
from app.taskqueue.orquestrador import Orquestrador, RunTargetsSuggestBody
from app.taskqueue.scheduler import _Desbravador
from app.taskqueue.service import RunError

from .conftest import COMMAND, Harness

QA = "com.pocqa.messenger"
OUTLOOK = "com.microsoft.office.outlook"
ENTRE_APPS = "leia o último e-mail no Outlook e curta o post de @marca no Instagram"


def _outlook(h: Harness) -> None:
    """O Outlook cadastrado na instalação (o manifesto dele já existe: conta da persona, com login gerenciado desde o
    23.8)."""
    assert h.state is not None
    if h.state.db.one("SELECT 1 FROM apps WHERE id='outlook'") is None:
        h.state.db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES "
                           "('outlook','Microsoft Outlook',?,'.Main',0)", (OUTLOOK,))


def _persona(h: Harness, nome: str, instance: str, *, outlook: bool = False) -> str:
    """Persona com a conta do Instagram (a do cadastro) no aparelho; com `outlook`, também a conta e o vínculo do
    Outlook NO MESMO aparelho — o vínculo é por app (051)."""
    assert h.state is not None
    pid = h.state.social.create_profile(ProfileCreate(
        username=f"{nome.lower()}.{pysecrets.token_hex(3)}", instance_id=instance, first_name=nome,
        last_name="Teste")).id
    if outlook:
        h.state.social_repo.create_account(pid, app_id="outlook", handle=f"{nome.lower()}@exemplo.test")
        h.state.social.bind_device(pid, PersonaDeviceBody(instance_id=instance, app_id="outlook"))
    return pid


# ==================================================================== o resolvedor, puro
_VINCULOS = (
    Vinculo("ana", "android-01", frozenset({"instagram"}), True),
    Vinculo("ana", "android-01", frozenset({"outlook"}), False),      # um vínculo por app no mesmo aparelho
    Vinculo("bia", "android-01", frozenset({"instagram"}), True),
    Vinculo("caio", "android-02", frozenset({"instagram"}), True),
    Vinculo("caio", "android-03", frozenset({"instagram", "outlook"}), False),
)
_MUNDO = Mundo(_VINCULOS, aptos=frozenset({"android-01", "android-02", "android-03"}), sem_conta=frozenset({"chrome"}))


def test_serve_exige_todos_os_apps_de_conta_no_mesmo_aparelho() -> None:
    assert _MUNDO.serve("ana", "android-01", ("outlook", "instagram"))       # a união dos vínculos do par
    assert not _MUNDO.serve("bia", "android-01", ("outlook", "instagram"))   # só o Instagram
    assert _MUNDO.serve("bia", "android-01", ("chrome", "instagram"))        # o Chrome não pede conta
    assert _MUNDO.serve("bia", "android-01", "instagram")                    # um app só: a chamada de antes
    assert not _MUNDO.serve("bia", "android-01", ("chrome",))                # só apps sem conta: a regra de antes
    assert not _MUNDO.serve("ana", "android-02", ("instagram",))             # sem vínculo naquele aparelho
    assert _MUNDO.serve("ana", "android-01", ())                             # nada a exigir: basta o vínculo


def test_resolvedor_escolhe_quem_serve_ao_conjunto_e_leva_os_apps() -> None:
    entre = ("outlook", "instagram")
    # Duas personas no aparelho: a única com as duas contas NELE faz (antes, sem app que desempatasse, pergunta).
    r = resolver_alvos(PedidoDeAlvos(instance_ids=("android-01",), app_ids=entre), DicasDoTexto(), _MUNDO)
    assert not r.perguntas
    assert [(a.instance_id, a.profile_id, a.app_id, a.app_ids) for a in r.alvos] == [
        ("android-01", "ana", "outlook", entre)]
    assert r.app_ids == ["outlook", "instagram"]
    assert r.alvos[0].as_dict()["app_ids"] == ["outlook", "instagram"]
    # Persona em dois aparelhos: vai onde estão as duas contas, não no principal (que só tem o Instagram).
    r = resolver_alvos(PedidoDeAlvos(profile_ids=("caio",), app_ids=entre), DicasDoTexto(), _MUNDO)
    assert [(a.instance_id, a.origem) for a in r.alvos] == [("android-03", "vinculo")]
    # Com um app só, o principal, como antes.
    r = resolver_alvos(PedidoDeAlvos(profile_ids=("caio",), app_id="instagram"), DicasDoTexto(), _MUNDO)
    assert [a.instance_id for a in r.alvos] == ["android-02"]
    # O app do alvo explícito vem à frente do conjunto do comando.
    r = resolver_alvos(PedidoDeAlvos(targets=(AlvoPedido("caio", app_id="instagram"),), app_ids=("outlook",)),
                       DicasDoTexto(), _MUNDO)
    assert [(a.instance_id, a.app_id, a.app_ids) for a in r.alvos] == [
        ("android-03", "instagram", ("instagram", "outlook"))]


# ==================================================================== o conjunto do comando
async def test_app_do_comando_devolve_o_conjunto(harness: Harness) -> None:
    assert harness.state is not None
    runs = harness.state.runs
    _outlook(harness)
    assert runs._app_do_comando(ENTRE_APPS, []) == ["outlook", "instagram"]                  # noqa: SLF001
    # Um app de conta citado sozinho entra (a conta do Outlook); o Instagram do aparelho não é lido do texto.
    assert runs._app_do_comando("leia o último e-mail no Outlook", []) == ["outlook"]        # noqa: SLF001
    # Um app SEM conta citado sozinho não vira conjunto: o comando de um app de sempre.
    assert runs._app_do_comando(COMMAND, []) == []                                           # noqa: SLF001
    # Os alvos da interface vêm primeiro.
    alvo = RunTarget(profile_id="p-x", app_id="instagram")
    assert runs._app_do_comando(ENTRE_APPS, [alvo]) == ["instagram", "outlook"]              # noqa: SLF001
    # A habilidade (aqui, um fluxo) casada: os apps do plano dela, o do plano primeiro — antes, com dois apps
    # exigidos, o comando roteava sem app nenhum.
    harness.state.cfg.file.ai.flows = True
    db = harness.state.db
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES (?,?,?,?,?)",
               ("configuracoes", "Configurações", "com.android.settings", ".Settings", 0))
    db.execute(
        "INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        ("fl-conj", "copiar e colar", "copie o codigo das configuracoes e cole no qa messenger",
         "Copie o código das Configuracoes e cole no QA Messenger",
         '{"summary":"copiar","app_id":"qa-messenger","parameters":{},"steps":[],'
         '"planner":{"provider":"x","model":"y","simulated":true}}',
         "qa-messenger", "active", "2026-09-29T10:00:00Z"))
    harness.state.scheduler.flows.set_required_apps("fl-conj", ["qa-messenger", "configuracoes"])
    assert runs._app_do_comando("Copie o código das Configuracoes e cole no QA Messenger", []) == [  # noqa: SLF001
        "qa-messenger", "configuracoes"]


async def test_mundo_por_conjunto_aptos_e_sessao_pronta(harness: Harness) -> None:
    assert harness.state is not None
    st = harness.state
    _outlook(harness)
    ana = _persona(harness, "Ana", "android-01", outlook=True)
    bia = _persona(harness, "Bia", "android-02")
    repo = st.social_repo
    for pid, iid in ((ana, "android-01"), (bia, "android-02")):
        repo.set_account_session(pid, repo.conta_ancora(pid)["id"], iid, status=SessionStatus.session_ready,
                                 verified_at="2026-09-29T00:00:00Z")
    # Desde o 23.8 o Outlook também tem login gerenciado: a sessão pronta do conjunto é a dos DOIS apps no aparelho.
    # Só o Instagram pronto ainda não basta para o conjunto; basta para o Instagram sozinho.
    assert (ana, "android-01") not in st.runs._mundo(["outlook", "instagram"]).sessoes_prontas  # noqa: SLF001
    assert {(ana, "android-01"), (bia, "android-02")} <= st.runs._mundo(["instagram"]).sessoes_prontas  # noqa: SLF001
    conta_outlook = st.db.one("SELECT id FROM profile_accounts WHERE profile_id=? AND app_id='outlook'", (ana,))
    repo.set_account_session(ana, conta_outlook["id"], "android-01", status=SessionStatus.session_ready,
                             verified_at="2026-09-29T00:00:00Z")
    mundo = st.runs._mundo(["outlook", "instagram"])                                          # noqa: SLF001
    assert (ana, "android-01") in mundo.sessoes_prontas
    assert (bia, "android-02") not in mundo.sessoes_prontas                                  # Bia não tem Outlook
    assert mundo.sem_conta == frozenset()
    assert mundo.serve(ana, "android-01", ["outlook", "instagram"])
    assert not mundo.serve(bia, "android-02", ["outlook", "instagram"])
    assert st.runs._mundo(["qa-messenger", "instagram"]).sem_conta == frozenset({"qa-messenger"})  # noqa: SLF001
    # Um app do conjunto sabidamente fora de pronto tira o aparelho dos aptos; só com o outro app, não.
    st.release_repo.upsert_app_state("android-02", OUTLOOK, state="missing", detail="o aparelho não tem o pacote")
    assert "android-02" not in st.runs._mundo(["outlook", "instagram"]).aptos                 # noqa: SLF001
    assert "android-02" in st.runs._mundo(["instagram"]).aptos                                # noqa: SLF001


# ==================================================================== mistura, compatibilidade e pré-voo
async def test_aparelhos_de_apps_diferentes_nao_sao_mistura_quando_o_comando_diz_os_apps(harness: Harness) -> None:
    assert harness.state is not None
    st = harness.state
    _outlook(harness)
    st.db.execute("UPDATE instances SET app_id='instagram' WHERE id='android-02'")   # android-01 segue no QA
    assert st.runs._mistura_de_apps(["android-01", "android-02"]) is not None               # noqa: SLF001
    assert st.runs._mistura_de_apps(["android-01", "android-02"], ["outlook", "instagram"]) is None  # noqa: SLF001
    # Pela criação: o comando entre apps não é recusado como mistura; um comando sem app, sim (a regra de antes).
    run = st.runs.create(RunCreate(command=ENTRE_APPS, instance_ids=["android-01", "android-02"], mode="plan",
                                   idempotency_key=f"conj-{pysecrets.token_hex(4)}"))
    assert run.id
    with pytest.raises(RunError) as exc:
        st.runs.create(RunCreate(command="Abra o app e diga oi.", instance_ids=["android-01", "android-02"],
                                 mode="plan", idempotency_key=f"conj-{pysecrets.token_hex(4)}"))
    assert exc.value.code == "mixed_apps" and "cite no comando" in exc.value.message


async def test_incompatibilidade_de_um_app_do_conjunto_recusa_antes_de_agendar(harness: Harness) -> None:
    assert harness.state is not None
    st = harness.state
    _outlook(harness)
    rt = st.devices.get("android-02")
    st.devices.registrar_capacidades(rt, {"api_level": 28, "abis": ["x86_64"]}, fonte="teste")
    st.db.execute(
        "INSERT INTO app_releases(id, package_name, version_name, version_code, artifact_type, signature_sha256,"
        " min_sdk, target_sdk, supported_abis, catalog_dir, source_type, imported_at, status, channel,"
        " requires_gms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("rel-outlook", OUTLOOK, "5.0", 500, "single", "b" * 64, 34, 34, '["x86_64"]', "apks/o", "inbox",
         "2026-09-29T00:00:00Z", "installable", "promoted", 0))
    st.release_repo.upsert_app_state("android-02", OUTLOOK, desired_release_id="rel-outlook")
    # O app principal do android-02 é o QA: antes, só ele era conferido e o Outlook passava.
    assert st.runs._incompativeis(["android-02"]) == []                                      # noqa: SLF001
    motivos = st.runs._incompativeis(["android-02"], ["outlook", "instagram"])               # noqa: SLF001
    assert len(motivos) == 1 and "android-02" in motivos[0] and "34" in motivos[0]
    with pytest.raises(RunError) as exc:
        st.runs.create(RunCreate(command=ENTRE_APPS, instance_ids=["android-02"], mode="plan",
                                 idempotency_key=f"conj-{pysecrets.token_hex(4)}"))
    assert exc.value.code == "app_incompativel"


async def test_pre_voo_confere_cada_app_do_conjunto(harness: Harness) -> None:
    assert harness.state is not None
    st = harness.state
    _outlook(harness)
    st.release_repo.upsert_app_state("android-01", OUTLOOK, state="missing", detail="o aparelho não tem o pacote")
    assert st.runs.pre_voo(["android-01"]) == {}                   # só o principal (QA): nada a recusar
    recusa = st.runs.pre_voo(["android-01"], app_ids=["outlook", "instagram"])["android-01"]
    assert recusa["code"] == "app_missing" and recusa["motivo"].startswith("Outlook: ")
    with pytest.raises(RunError) as exc:
        st.runs.create(RunCreate(command=ENTRE_APPS, instance_ids=["android-01"], mode="plan",
                                 idempotency_key=f"conj-{pysecrets.token_hex(4)}"))
    assert exc.value.code == "preflight" and "Outlook" in exc.value.message


async def test_inicio_confere_os_apps_do_plano(harness: Harness) -> None:
    """No `start` o plano existe: o pré-voo usa os `required_apps` dele, não uma leitura do texto."""
    assert harness.state is not None
    st = harness.state
    db = st.db
    _outlook(harness)
    run_id, oid = "run-conj", "run-conj:android-01"
    pos = {"kind": "model_judged", "value": "x", "description": "y"}
    plano = Plan(summary="entre apps", app_id="instagram", required_apps=["outlook", "instagram"],
                 planner=PlannerInfo(provider="fake", model="t", simulated=True),
                 steps=[PlanStep(key="ler_email", title="ler", goal="ler", postcondition=Postcondition(**pos),
                                 app_id="outlook")])
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
               " plan) VALUES (?,?,'um comando sem app','execute','planned',1,'[\"android-01\"]',"
               "'2026-09-29T12:00:00Z',?)", (run_id, f"k-{run_id}", plano.model_dump_json()))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters)"
               " VALUES (?,?,'android-01','pending',1,'{}')", (oid, run_id))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, bindings,"
        " app_id) VALUES (?,?,?,'android-01',1,1,'ler_email','ler','ler','[]',0,'[]',?,180,1,'pending',NULL,'{}',"
        "'outlook')", (f"{oid}:v1:ler_email", run_id, oid, json.dumps(pos)))
    st.release_repo.upsert_app_state("android-01", OUTLOOK, state="missing", detail="o aparelho não tem o pacote")
    st.runs.start(run_id)
    obj = st.repo.objective_row(oid)
    assert obj["status"] == "waiting_user" and "Outlook" in (obj["blocked_reason"] or "")


# ==================================================================== desbravador
async def test_desbravador_compara_cada_app_das_etapas(harness: Harness) -> None:
    """Mesma versão do QA e OUTRA do Outlook: não espera o líder (a receita do Outlook dele não serviria). Antes, só
    o primeiro pacote contava e o aparelho esperava."""
    assert harness.state is not None
    st = harness.state
    sched = st.scheduler
    harness.cfg.file.ai.recipes = "replay"
    harness.cfg.file.ai.pathfinder_wait_s = 240
    devs = st.devices.devices
    devs["android-01"].app_versions.update({QA: "1.0(1)", OUTLOOK: "5.0(500)"})
    devs["android-02"].app_versions.update({QA: "1.0(1)", OUTLOOK: "6.0(600)"})
    run_id = "run-desbravador-conj"
    sched._pathfinders[run_id] = [_Desbravador("android-01", f"{run_id}:android-01", sched.relogio())]  # noqa: SLF001
    try:
        obj: dict[str, Any] = {"id": f"{run_id}:android-02", "run_id": run_id, "status": "pending",
                               "plan_version": 1}
        # Os dois apps: incompatível no Outlook → sem líder, vira candidato a líder do próprio grupo.
        assert sched._waits_for_pathfinder(obj, devs["android-02"], [QA, OUTLOOK]) is False   # noqa: SLF001
        assert obj["id"] in sched._candidatos                                                 # noqa: SLF001
        # Só o QA (o que o código antigo olhava): compatível → tem líder, não vira candidato.
        sched._candidatos.pop(obj["id"], None)                                                 # noqa: SLF001
        sched._waits_for_pathfinder(obj, devs["android-02"], [QA])                            # noqa: SLF001
        assert obj["id"] not in sched._candidatos                                             # noqa: SLF001
    finally:
        sched._pathfinders.pop(run_id, None)                                                  # noqa: SLF001
        sched._candidatos.pop(f"{run_id}:android-02", None)                                   # noqa: SLF001


# ==================================================================== modo Automático
async def test_automatico_so_sugere_quem_tem_conta_em_todos_os_apps(harness: Harness) -> None:
    assert harness.state is not None
    st = harness.state
    _outlook(harness)
    alice = _persona(harness, "Alice", "android-01")                    # só o Instagram; pelo nome, viria primeiro
    beatriz = _persona(harness, "Beatriz", "android-02", outlook=True)
    orq = Orquestrador(st.runs, st.social_repo)
    s = await orq.sugerir(RunTargetsSuggestBody(command=ENTRE_APPS))
    assert s.modo == "ia" and s.app_ids == ["outlook", "instagram"] and s.app_id == "outlook"
    assert [e.profile_id for e in s.escolhidas] == [beatriz]
    assert alice not in {d.profile_id for d in s.descartadas} | {n.profile_id for n in s.nao_avaliaveis}
    assert [(t.instance_id, t.profile_id, t.app_ids) for t in s.targets] == [
        ("android-02", beatriz, ["outlook", "instagram"])]


async def test_automatico_sem_persona_com_todas_as_contas_diz_quais_apps(harness: Harness) -> None:
    assert harness.state is not None
    st = harness.state
    _outlook(harness)
    _persona(harness, "Alice", "android-01")                            # sem conta no Outlook
    s = await Orquestrador(st.runs, st.social_repo).sugerir(RunTargetsSuggestBody(command=ENTRE_APPS))
    assert s.modo == "nenhuma" and s.app_ids == ["outlook", "instagram"] and not s.targets
    assert any("outlook, instagram" in w and "em todos eles" in w for w in s.warnings)
