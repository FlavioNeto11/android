"""Item 24.4 — conta e portas do app da etapa (R4 e R8 de `docs/design/terceira-evolucao.md` §2.4).

O que se prova aqui:
- a conta esperada que o ator recebe é a da persona NO APP DA ETAPA (`profile_accounts`), não o rótulo do aparelho;
  numa etapa de outro app sem conta da persona, nenhuma conta é "esperada" (o rótulo do aparelho seria a errada);
- a persona sem conta (ou com a conta desativada) num app que declara conta vira `waiting_user` com o motivo — no
  despacho, antes de qualquer etapa, e no meio do objetivo, na troca de app, sem começar a etapa do app seguinte; o
  app que exige persona sem provedor de sessão (o Outlook) também para quando o item não tem persona nenhuma;
- depois de uma pessoa reativar a conta, "Tentar novamente" conclui sem repetir a leitura já comprovada: o valor lido
  na etapa do primeiro app chega à etapa do segundo;
- as portas do despacho são repassadas na troca de app dentro do worker: a rede do aparelho (contrato C4) segura a
  etapa seguinte com `wait_reason='rede'` (espera, não bloqueio) e ela segue quando a rede volta;
- a porta que pede instalação ou login de dentro do worker anota a espera e devolve o aparelho ao despacho, em vez
  de segurar em silêncio.

Nível de prova: `simulated` (provedor por regras, aparelho falso de QA com dois registros de app no mesmo pacote,
banco de teste). O QA ganha `needs_profile` só dentro dos testes que precisam de conta, e sai no fim.
"""
from __future__ import annotations

import json
from typing import Any, Iterator

import pytest

from app.models import PlanStep, Postcondition, ProfileCreate
from app.taskqueue import executor as executor_mod
from app.modules.applications.domain.definition import AppDefinition
from app.planning.catalog import register, unregister

from .conftest import Harness
from .fake_device import PKG as QA
from .test_valor_entre_etapas import LIDO, _decide_leitura

IID = "android-01"
CONTA_NO_SEGUNDO_APP = "qa-user-01"          # o que o QA falso mostra em "Conta:" no android-01
ROTULO_DO_APARELHO = "rotulo-do-aparelho"    # propositalmente diferente: a etapa do 2º app não pode esperar este


def _post(kind: str, value: str, description: str = "d") -> Postcondition:
    return Postcondition(kind=kind, value=value, description=description)  # type: ignore[arg-type]


@pytest.fixture
def qa_com_conta() -> Iterator[None]:
    """O QA passa a declarar conta da persona (como o Outlook, `precisa_de_perfil`), sem provedor de sessão."""
    register(QA, None, AppDefinition(package=QA, name="QA Messenger", needs_profile=True, label="QA Messenger"))
    try:
        yield
    finally:
        unregister(QA)


def _segundo_app(h: Harness) -> None:
    """Um segundo registro de app, no MESMO pacote do QA falso: o aparelho de teste só sabe encenar um app, e o que
    este item muda é a conta e as portas por app (id), não a tela."""
    db = h.state.db                                                               # type: ignore[union-attr]
    qa = db.one("SELECT * FROM apps WHERE package=?", (QA,))
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('qa-contas','QA Contas',?,?,0)",
               (QA, qa["activity"]))


def _persona(h: Harness, *, contas: dict[str, str]) -> str:
    st = h.state
    assert st is not None
    pid = st.social.create_profile(ProfileCreate(username="pessoa.da.etapa", instance_id=IID)).id
    for app_id, handle in contas.items():
        st.social_repo.create_account(pid, app_id=app_id, handle=handle)
    # O rótulo do aparelho é derivado do vínculo; aqui ele é fixado DIFERENTE da conta do 2º app, para que só a conta
    # do app da etapa faça a etapa "Confirmar a conta" passar.
    st.db.execute("UPDATE instances SET account_label=? WHERE id=?", (ROTULO_DO_APARELHO, IID))
    return pid


def _plano_em_dois_apps(inner: Any) -> Any:
    """Abrir o app e LER o contato no primeiro app (o do plano); confirmar a conta e abrir a conversa com o valor lido
    no segundo (`qa-contas`)."""
    plan0 = inner.plan

    async def plan(req: Any) -> Any:
        p, u = await plan0(req)
        por = {s.key: s for s in p.steps}
        passos = [
            por["open_app"],
            PlanStep(key="read_contact", title="Ler o contato da lista", goal="Ler o nome do contato na lista.",
                     depends_on=["open_app"], saidas=["contato"],
                     postcondition=_post("element_present", "id=conversation_list", "A lista está visível.")),
            por["confirm_account"].model_copy(update={"app_id": "qa-contas", "depends_on": ["read_contact"]}),
            por["open_conversation"].model_copy(update={"app_id": "qa-contas", "depends_on": ["confirm_account"],
                                                         "variables": {"recipient": "{{saida:contato}}"}}),
        ]
        # O app do plano é o QA de sempre: com dois registros no mesmo pacote, o planejador simulado pode escolher
        # qualquer um, e o teste precisa saber qual é o "outro".
        return p.model_copy(update={"steps": passos, "app_id": "qa-messenger"}), u

    return plan


def _conta_vista_pelo_ator(inner: Any, vistas: dict[str, list[str | None]]) -> None:
    decide0 = inner.decide

    async def decide(req: Any) -> Any:
        vistas.setdefault(req.ctx.step_key, []).append(req.ctx.account_label)
        return await decide0(req)

    inner.decide = decide


def _objetivo(h: Harness, run_id: str) -> Any:
    return h.state.db.one("SELECT * FROM objectives WHERE run_id=?", (run_id,))   # type: ignore[union-attr]


def _etapas(h: Harness, objective_id: str) -> dict[str, Any]:
    linhas = h.state.db.query("SELECT * FROM steps WHERE objective_id=? ORDER BY plan_version, seq",  # type: ignore[union-attr]
                              (objective_id,))
    return {f"v{r['plan_version']}:{r['key']}": r for r in linhas}


# ================================================================== R4: conta esperada pelo app da etapa
async def test_conta_esperada_e_a_da_persona_no_app_da_etapa(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    # O teste confere a conta que o ATOR vê na confirmação; com o atalho LT-1 (caminho rápido 1) a etapa cuja conta já
    # está na tela fecha sem ele. Desligado aqui, como em test_hub_de_ia e test_cost_levers.
    monkeypatch.setattr(executor_mod, "ATALHO_ANTES_DO_ATOR", False)
    _segundo_app(harness)
    pid = _persona(harness, contas={"qa-contas": CONTA_NO_SEGUNDO_APP})
    inner = harness.ai.inner
    vistas: dict[str, list[str | None]] = {}
    inner.plan = _plano_em_dois_apps(inner)
    inner.decide = _decide_leitura(inner, {"decisoes": 1})
    _conta_vista_pelo_ator(inner, vistas)
    run = harness.run([IID])
    assert (await harness.wait_run(run.id, timeout=60)).status == "completed"
    obj = _objetivo(harness, run.id)
    assert obj["profile_id"] == pid

    # a etapa do 2º app esperou a conta da persona NAQUELE app e passou porque a tela mostra essa conta; com o rótulo
    # do aparelho ela teria parado em "Conta diferente"
    assert set(vistas["confirm_account"]) == {CONTA_NO_SEGUNDO_APP}
    # e o `{account_label}` da pós-condição dessa etapa foi resolvido com a mesma conta, na materialização
    confirma = _etapas(harness, obj["id"])["v1:confirm_account"]
    assert json.loads(confirma["postcondition"])["value"] == f"Conta: {CONTA_NO_SEGUNDO_APP}"
    # a etapa do app do plano, sem conta da persona nele, segue com o rótulo do aparelho, como antes
    assert set(vistas["read_contact"]) == {ROTULO_DO_APARELHO}
    assert [m.contact for m in harness.fakes[IID].messages] == [] and harness.fakes[IID].contact == LIDO

    sched, st = harness.state.scheduler, harness.state                          # type: ignore[union-attr]
    rt, linha = st.devices.get(IID), st.repo.run_row(run.id)
    assert sched._app_context(linha, rt, "qa-contas", profile_id=pid)[1] == CONTA_NO_SEGUNDO_APP  # noqa: SLF001
    # sem persona, a etapa de OUTRO app não herda a conta do aparelho (é a conta errada), e a do app do plano segue
    assert sched._app_context(linha, rt, "qa-contas")[1] is None               # noqa: SLF001
    assert sched._app_context(linha, rt)[1] == ROTULO_DO_APARELHO              # noqa: SLF001
    # duas contas ativas no app (contas de portal, 049): não há UMA conta esperada
    st.social_repo.create_account(pid, app_id="qa-contas", handle="outra", host="portal.exemplo.test")
    st.db.execute("UPDATE profile_accounts SET host='site.exemplo.test' WHERE profile_id=? AND handle=?",
                  (pid, CONTA_NO_SEGUNDO_APP))
    assert sched._app_context(linha, rt, "qa-contas", profile_id=pid)[1] is None  # noqa: SLF001


async def test_sem_conta_conhecida_no_app_da_etapa_a_conferencia_nao_passa_com_qualquer_conta(
        harness: Harness) -> None:
    """A persona sem conta no app da etapa, e o app sem declaração de conta (a porta de conta não o segura): o molde
    `{account_label}` não pode virar "Conta: " — casaria com qualquer conta na tela, e "confirmar a conta" seria
    comprovada sem conta esperada nenhuma. O item para com o motivo, sem tentativa; com a conta cadastrada, "Tentar
    novamente" resolve a conta no despacho e conclui sem reler."""
    _segundo_app(harness)
    pid = _persona(harness, contas={})
    st = harness.state
    assert st is not None
    inner = harness.ai.inner
    inner.plan = _plano_em_dois_apps(inner)
    inner.decide = _decide_leitura(inner, {"decisoes": 1})
    run = harness.run([IID])

    def parado() -> bool:
        o = _objetivo(harness, run.id)
        return o is not None and o["status"] in ("waiting_user", "failed", "succeeded")

    await harness.wait(parado, 60, "objetivo parado na conta")
    obj = _objetivo(harness, run.id)
    assert obj["status"] == "waiting_user", obj["status"]
    assert "não tem UMA conta" in obj["blocked_reason"] and "Cadastre ou reative a conta" in obj["needs"]
    etapas = _etapas(harness, obj["id"])
    confirma = etapas["v1:confirm_account"]
    assert json.loads(confirma["postcondition"])["value"] != "Conta: "
    assert "{account_label}" in json.loads(confirma["postcondition"])["value"]
    assert confirma["status"] == "ready" and confirma["attempts"] == 0
    assert etapas["v1:read_contact"]["status"] == "succeeded"
    assert st.repo.run_row(run.id)["status"] != "completed"

    # a pessoa cadastra a conta dela no app e usa "Tentar novamente": o despacho resolve a conta e conclui sem reler
    st.social_repo.create_account(pid, app_id="qa-contas", handle=CONTA_NO_SEGUNDO_APP)
    assert st.runs.retry_failed(run.id)["retried"] == [obj["id"]]
    assert (await harness.wait_run(run.id, timeout=60)).status == "completed"
    confirmadas = [r for k, r in _etapas(harness, obj["id"]).items()
                   if k.endswith(":confirm_account") and r["status"] == "succeeded"]
    assert [json.loads(r["postcondition"])["value"] for r in confirmadas] == [f"Conta: {CONTA_NO_SEGUNDO_APP}"]
    assert st.db.scalar("SELECT COUNT(*) FROM attempts a JOIN steps s ON s.id=a.step_id"
                        " WHERE s.objective_id=? AND s.key='read_contact'", (obj["id"],)) == 1


async def test_duas_contas_ativas_no_app_do_aparelho_usam_o_rotulo_do_aparelho_e_sem_ele_param(
        harness: Harness) -> None:
    """Duas contas ativas da persona no app DO APARELHO: não há UMA conta da persona, mas o aparelho diz qual está nele
    (o rótulo, como antes do item 24.4). Sem rótulo, nenhuma conta é esperada — e a etapa não é comprovada com "Conta: "
    vazio: para no despacho."""
    pid = _persona(harness, contas={})
    st = harness.state
    assert st is not None
    st.db.execute("UPDATE instances SET app_id='qa-messenger' WHERE id=?", (IID,))
    st.social_repo.create_account(pid, app_id="qa-messenger", handle="conta.um", host="um.exemplo.test")
    st.social_repo.create_account(pid, app_id="qa-messenger", handle="conta.dois", host="dois.exemplo.test")
    assert st.repo.conta_esperada(pid, "qa-messenger", ROTULO_DO_APARELHO, do_aparelho=True) == ROTULO_DO_APARELHO
    assert st.repo.conta_esperada(pid, "qa-messenger", "", do_aparelho=True) is None
    assert st.repo.conta_esperada(pid, "qa-messenger", ROTULO_DO_APARELHO, do_aparelho=False) is None

    # ponta a ponta: a etapa de confirmar a conta declara o app do aparelho, que mostra "Conta: qa-user-01"
    st.db.execute("UPDATE instances SET account_label=? WHERE id=?", (CONTA_NO_SEGUNDO_APP, IID))
    inner = harness.ai.inner
    plan0 = inner.plan

    async def plan(req: Any) -> Any:
        p, u = await plan0(req)
        passos = [s.model_copy(update={"app_id": "qa-messenger"}) if s.key == "confirm_account" else s
                  for s in p.steps if s.key in ("open_app", "confirm_account")]
        return p.model_copy(update={"steps": passos, "app_id": "qa-messenger"}), u

    inner.plan = plan
    run = harness.run([IID])
    assert (await harness.wait_run(run.id, timeout=60)).status == "completed"
    obj = _objetivo(harness, run.id)
    confirma = _etapas(harness, obj["id"])["v1:confirm_account"]
    assert json.loads(confirma["postcondition"])["value"] == f"Conta: {CONTA_NO_SEGUNDO_APP}"

    # sem rótulo no aparelho: nenhuma conta esperada — o item para, sem tentativa, em vez de conferir contra "Conta: "
    st.db.execute("UPDATE instances SET account_label='' WHERE id=?", (IID,))
    run2 = harness.run([IID])

    def parado() -> bool:
        o = _objetivo(harness, run2.id)
        return o is not None and o["status"] in ("waiting_user", "failed", "succeeded")

    await harness.wait(parado, 60, "objetivo parado sem conta esperada")
    obj2 = _objetivo(harness, run2.id)
    assert obj2["status"] == "waiting_user" and "não tem UMA conta" in obj2["blocked_reason"]
    confirma2 = _etapas(harness, obj2["id"])["v1:confirm_account"]
    assert confirma2["attempts"] == 0 and "{account_label}" in json.loads(confirma2["postcondition"])["value"]


# ================================================================== conta indisponível no app da etapa
async def test_conta_ausente_no_app_de_uma_etapa_segura_o_item_no_despacho(harness: Harness,
                                                                          qa_com_conta: None) -> None:
    _segundo_app(harness)
    _persona(harness, contas={"qa-messenger": "conta.do.qa"})                 # nenhuma no qa-contas
    inner = harness.ai.inner
    inner.plan = _plano_em_dois_apps(inner)
    run = harness.run([IID])
    db = harness.state.db                                                      # type: ignore[union-attr]

    def parado() -> bool:
        o = _objetivo(harness, run.id)
        return o is not None and o["status"] == "waiting_user"

    await harness.wait(parado, 30, "objetivo segurado pela conta")
    obj = _objetivo(harness, run.id)
    assert "não tem conta em QA Messenger" in obj["blocked_reason"] and "pessoa.da.etapa" in obj["blocked_reason"]
    assert "Cadastre ou reative a conta" in obj["needs"]
    # nada começou: o item não gasta a primeira etapa para parar na segunda
    assert db.scalar("SELECT COALESCE(SUM(attempts), 0) FROM steps WHERE objective_id=?", (obj["id"],)) == 0


async def test_app_que_exige_persona_sem_persona_no_aparelho_nao_sai_sem_conta(harness: Harness,
                                                                               qa_com_conta: None) -> None:
    """App que declara conta da persona e não tem provedor de sessão (o Outlook): sem persona nenhuma, nada mais
    perguntaria, e a etapa sairia sem conta esperada. Para com o motivo, sem tentativa."""
    run = harness.run([IID])

    def parado() -> bool:
        o = _objetivo(harness, run.id)
        return o is not None and o["status"] == "waiting_user"

    await harness.wait(parado, 30, "objetivo segurado pela falta de persona")
    obj = _objetivo(harness, run.id)
    assert obj["profile_id"] is None and "nenhuma persona definida" in obj["blocked_reason"]
    assert harness.state.db.scalar("SELECT COALESCE(SUM(attempts), 0) FROM steps WHERE objective_id=?",  # type: ignore[union-attr]
                                   (obj["id"],)) == 0


async def test_conta_desativada_no_meio_para_na_troca_de_app_e_a_retomada_nao_rele(harness: Harness,
                                                                                    qa_com_conta: None) -> None:
    _segundo_app(harness)
    pid = _persona(harness, contas={"qa-messenger": "conta.do.qa", "qa-contas": CONTA_NO_SEGUNDO_APP})
    st = harness.state
    assert st is not None
    inner = harness.ai.inner
    inner.plan = _plano_em_dois_apps(inner)
    ler = _decide_leitura(inner, {"decisoes": 1})

    async def decide(req: Any) -> Any:
        if req.ctx.step_key == "read_contact":
            # a pessoa desativa a conta do 2º app enquanto o 1º trabalha: o despacho já passou, a troca é que confere
            st.db.execute("UPDATE profile_accounts SET status='disabled' WHERE profile_id=? AND app_id='qa-contas'",
                          (pid,))
        return await ler(req)

    inner.decide = decide
    run = harness.run([IID])

    def parado() -> bool:
        o = _objetivo(harness, run.id)
        return o is not None and o["status"] == "waiting_user"

    await harness.wait(parado, 60, "objetivo parado na troca de app")
    obj = _objetivo(harness, run.id)
    assert "está desativada" in obj["blocked_reason"] and "QA Messenger" in obj["blocked_reason"]
    etapas = _etapas(harness, obj["id"])
    assert etapas["v1:open_app"]["status"] == "succeeded" and etapas["v1:read_contact"]["status"] == "succeeded"
    # a etapa do app seguinte não começou (nenhuma tentativa) e o concluído ficou: o valor lido segue gravado
    assert etapas["v1:confirm_account"]["status"] == "ready" and etapas["v1:confirm_account"]["attempts"] == 0
    assert st.repo.step_outputs(obj["id"]) == {"contato": LIDO}

    # a pessoa reativa a conta e usa "Tentar novamente": conclui sem reler o contato
    st.db.execute("UPDATE profile_accounts SET status='active' WHERE profile_id=? AND app_id='qa-contas'", (pid,))
    inner.decide = ler
    assert st.runs.retry_failed(run.id)["retried"] == [obj["id"]]
    assert (await harness.wait_run(run.id, timeout=60)).status == "completed"
    etapas = _etapas(harness, obj["id"])
    assert not any(k.endswith(":read_contact") for k in etapas if not k.startswith("v1:"))
    assert st.db.scalar("SELECT COUNT(*) FROM attempts a JOIN steps s ON s.id=a.step_id"
                        " WHERE s.objective_id=? AND s.key='read_contact'", (obj["id"],)) == 1
    assert harness.fakes[IID].contact == LIDO                                  # a conversa aberta é a do valor lido


# ================================================================== R8: portas repassadas na troca de app
async def test_rede_do_aparelho_segura_a_etapa_do_app_seguinte_e_solta_quando_volta(harness: Harness) -> None:
    _segundo_app(harness)
    _persona(harness, contas={"qa-contas": CONTA_NO_SEGUNDO_APP})
    st = harness.state
    assert st is not None
    sched = st.scheduler
    rede = {"exigida_sem_verificar": False}
    motivo = "aguardando a rede exigida deste aparelho ser verificada"
    sched.rede_gate = lambda iid: motivo if rede["exigida_sem_verificar"] else None
    inner = harness.ai.inner
    inner.plan = _plano_em_dois_apps(inner)
    ler = _decide_leitura(inner, {"decisoes": 1})

    async def decide(req: Any) -> Any:
        if req.ctx.step_key == "read_contact":
            rede["exigida_sem_verificar"] = True       # a rede cai enquanto a etapa do 1º app trabalha
        return await ler(req)

    inner.decide = decide
    run = harness.run([IID])

    def segurado() -> bool:
        o = _objetivo(harness, run.id)
        return o is not None and o["wait_reason"] == "rede"

    await harness.wait(segurado, 60, "etapa do app seguinte segurada pela rede")
    await harness.ticks(2)
    obj = _objetivo(harness, run.id)
    etapas = _etapas(harness, obj["id"])
    # espera, não bloqueio: ninguém decide nada, e a etapa seguinte não começou
    assert obj["status"] == "running" and obj["status_detail"] == motivo
    assert etapas["v1:read_contact"]["status"] == "succeeded"
    assert etapas["v1:confirm_account"]["attempts"] == 0 and IID not in sched.workers

    rede["exigida_sem_verificar"] = False
    sched.wake()
    assert (await harness.wait_run(run.id, timeout=60)).status == "completed"
    assert st.db.scalar("SELECT COUNT(*) FROM attempts a JOIN steps s ON s.id=a.step_id"
                        " WHERE s.objective_id=? AND s.key='read_contact'", (obj["id"],)) == 1


async def test_porta_com_trabalho_chamada_do_worker_anota_a_espera_e_devolve_o_aparelho(harness: Harness) -> None:
    """Dentro do worker `run_device_job` recusa (o aparelho é dele): a espera é anotada e quem autentica é o tick."""
    st = harness.state
    assert st is not None
    sched = st.scheduler
    run = harness.run([IID], mode="plan")
    await harness.wait_run(run.id, statuses=("planned",))
    obj = _objetivo(harness, run.id)
    rt = st.devices.get(IID)
    chamadas: list[str] = []

    async def login() -> None:
        chamadas.append("login")

    gate_antes, worker_antes = sched.session_gate, sched.workers.get(IID)
    sched.session_gate = lambda rt_, pacote, perfil: ("a verificação desta sessão passou da validade", login)
    sched.workers[IID] = object()                    # type: ignore[assignment]  # o worker deste aparelho
    try:
        assert sched._portas_na_troca(obj, rt, "qa-messenger", QA) is True     # noqa: SLF001
    finally:
        sched.session_gate = gate_antes
        if worker_antes is None:
            sched.workers.pop(IID, None)
        else:
            sched.workers[IID] = worker_antes
    o = _objetivo(harness, run.id)
    assert o["status_detail"].startswith("verificando a sessão em") and o["wait_reason"] == "device_slot"
    assert chamadas == []                             # não rodou dentro do worker: é do próximo tick
