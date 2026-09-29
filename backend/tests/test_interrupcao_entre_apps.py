"""Item 24.7 — interrupção e retomada no meio da troca de app (ADR-058; R3, R4 e R8 de `docs/design/terceira-evolucao.md`).

O comando atravessa dois apps: no primeiro, abre e LÊ o contato (`read_contact`, saída `contato`); no segundo, confere a
conta da persona naquele app e manda a mensagem ao contato lido (`{{saida:contato}}`), com o toque de Enviar como
efeito externo. A interrupção cai na TROCA — com a primeira etapa do segundo app em curso, ou com ela segurada pela
porta da troca (rede do aparelho, contrato C4) — e em cada caso se prova:

- **reinício do backend** (`reconcile_after_restart`): a leitura comprovada não se repete (uma tentativa, um
  `read_value`), a saída gravada é a que chega ao segundo app, e o efeito disparado antes da queda é reconciliado pela
  tela, nunca reenviado;
- **pausa e retomada**: a etapa do segundo app cede no ponto seguro sem gastar tentativa e, retomada, segue dali;
- **cancelamento**: nada do segundo app começa ou continua, o concluído e a saída ficam registrados (e no relatório),
  e nenhum efeito sai;
- **sucessora** (ADR-047): só nasce de uma execução em `needs_input`, que nunca executou etapa. No meio da troca a
  execução está `running` (ou `cancelling`/`cancelled` depois do cancelamento), então a resposta é recusada sem criar
  execução nenhuma — nada do que já foi feito é refeito por uma segunda execução. Continuar numa execução nova com o
  que a antiga leu é decisão de produto em aberto (ver `docs/dominios/execution.md`).

Nível de prova: `simulated` (provedor por regras, aparelho falso de QA com dois registros de app no mesmo pacote,
banco de teste; o "reinício" é `Harness.crash` + `Harness.boot` sobre o mesmo banco e o mesmo aparelho). Um caso usa o
dublê com DOIS pacotes (`FakeQaDevice.pacotes_extras`): a retomada depois do reinício abre o pacote do segundo app.
Outlook para Instagram num aparelho real fica com o 24.9.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any, Iterator

import pytest

from app.models import PlanStep, Postcondition
from app.modules.applications.domain.definition import AppDefinition
from app.planning.catalog import register, unregister
from app.planning.provider import Decision, Usage
from app.taskqueue.assistente import ComandoAssistido, RunSuccessorBody
from app.taskqueue.service import RunError

from .conftest import Harness
from .fake_device import PKG as QA
from .test_conta_do_app_da_etapa import CONTA_NO_SEGUNDO_APP, IID, _etapas, _objetivo, _persona, _segundo_app
from .test_valor_entre_etapas import LIDO, _decide_leitura

REF = "{{saida:contato}}"
PKG_CONTAS = "com.pocqa.contas"          # o segundo app com pacote próprio (dublê de dois pacotes)


def _post(kind: str, value: str, description: str = "d") -> Postcondition:
    return Postcondition(kind=kind, value=value, description=description)  # type: ignore[arg-type]


def _plano_que_troca_de_app(inner: Any) -> Any:
    """Primeiro app (o do plano): abrir e ler o contato. Segundo (`qa-contas`): confirmar a conta, abrir a conversa com o
    contato lido, escrever, ENVIAR (efeito externo) e conferir o envio."""
    plan0 = inner.plan

    async def plan(req: Any) -> Any:
        p, u = await plan0(req)
        por = {s.key: s for s in p.steps}
        outro = {"app_id": "qa-contas"}
        passos = [
            por["open_app"],
            PlanStep(key="read_contact", title="Ler o contato da lista", goal="Ler o nome do contato na lista.",
                     depends_on=["open_app"], saidas=["contato"],
                     postcondition=_post("element_present", "id=conversation_list", "A lista está visível.")),
            por["confirm_account"].model_copy(update={**outro, "depends_on": ["read_contact"]}),
            *[por[k].model_copy(update={**outro, "variables": {"recipient": REF}})
              for k in ("open_conversation", "compose_message", "send_message", "verify_sent")],
        ]
        return p.model_copy(update={"steps": passos, "app_id": "qa-messenger"}), u

    return plan


def _preparar(h: Harness) -> tuple[Any, Any]:
    """Os dois apps, a persona com conta no segundo e o plano que troca de app. Devolve (provedor, decisão com a
    leitura) — a leitura já sabe o que ler (sem a recusa do `step_done` da primeira decisão)."""
    _segundo_app(h)
    _persona(h, contas={"qa-contas": CONTA_NO_SEGUNDO_APP})
    inner = h.ai.inner
    inner.plan = _plano_que_troca_de_app(inner)
    return inner, _decide_leitura(inner, {"decisoes": 1})


def _leituras(h: Harness, objective_id: str) -> tuple[int, int]:
    """(tentativas da etapa de leitura, `read_value` concluídos) no objetivo — as duas medidas de "não releu"."""
    db = h.state.db                                                                # type: ignore[union-attr]
    tentativas = db.scalar("SELECT COUNT(*) FROM attempts a JOIN steps s ON s.id=a.step_id"
                           " WHERE s.objective_id=? AND s.key='read_contact'", (objective_id,))
    lidas = db.scalar("SELECT COUNT(*) FROM actions a JOIN attempts t ON t.id=a.attempt_id JOIN steps s"
                      " ON s.id=t.step_id WHERE s.objective_id=? AND a.tool='read_value' AND a.status='done'",
                      (objective_id,))
    return int(tentativas or 0), int(lidas or 0)


def _concluido_sem_reler(h: Harness, objective_id: str) -> None:
    """O fecho comum da retomada: uma leitura só, o valor gravado é o que chegou ao segundo app, uma mensagem só."""
    st = h.state
    assert st is not None
    assert _leituras(h, objective_id) == (1, 1)
    assert st.repo.step_outputs(objective_id) == {"contato": LIDO}
    # nenhuma versão nova do plano trouxe a leitura de volta
    assert st.db.scalar("SELECT COUNT(*) FROM steps WHERE objective_id=? AND key='read_contact'", (objective_id,)) == 1
    conversa = [r for k, r in _etapas(h, objective_id).items() if k.endswith(":open_conversation")][-1]
    assert conversa["status"] == "succeeded" and LIDO in conversa["title"] and REF not in conversa["title"]
    assert [m.contact for m in h.fakes[IID].messages] == [LIDO]


# ================================================================== reinício do backend no meio da troca
async def test_reinicio_com_a_etapa_do_segundo_app_em_curso_retoma_sem_reler(harness: Harness) -> None:
    inner, ler = _preparar(harness)
    na_troca = asyncio.Event()

    async def decide(req: Any) -> Any:
        if req.ctx.step_key == "confirm_account" and not na_troca.is_set():
            na_troca.set()
            await asyncio.sleep(60)          # a etapa do 2º app fica em curso até a queda do processo
        return await ler(req)

    inner.decide = decide
    run = harness.run([IID])
    await asyncio.wait_for(na_troca.wait(), 60)
    obj = _objetivo(harness, run.id)
    etapas = _etapas(harness, obj["id"])
    assert etapas["v1:read_contact"]["status"] == "succeeded" and etapas["v1:confirm_account"]["status"] == "running"
    await harness.crash()

    st2 = await harness.boot()                                   # mesmo banco, mesmo aparelho
    assert (await harness.wait_run(run.id, timeout=90)).status == "completed"
    _concluido_sem_reler(harness, obj["id"])
    # a tentativa cortada pela queda é `interrupted` e devolvida (não consome o teto da etapa)
    confirma = _etapas(harness, obj["id"])["v1:confirm_account"]
    status = [r["status"] for r in st2.db.query("SELECT status FROM attempts WHERE step_id=? ORDER BY number",
                                                (confirma["id"],))]
    assert status[0] == "interrupted" and status[-1] == "succeeded" and confirma["attempts"] == 1


async def test_reinicio_depois_do_efeito_no_segundo_app_reconcilia_sem_reenviar_nem_reler(harness: Harness) -> None:
    inner, ler = _preparar(harness)
    inner.decide = ler
    fake = harness.fakes[IID]
    fake.action_delay_s = 0.05
    run = harness.run([IID])
    # derruba o backend no instante em que o toque de Enviar (2º app) acabou de chegar ao aparelho
    await harness.wait(lambda: len(fake.messages) == 1, 60, "mensagem enviada no segundo app")
    obj = _objetivo(harness, run.id)
    await harness.crash()
    fake.action_delay_s = 0

    await harness.boot()
    detalhe = await harness.wait_run(run.id, timeout=90)
    assert detalhe.status == "completed", [(s.key, s.status, s.status_detail) for s in detalhe.steps]
    _concluido_sem_reler(harness, obj["id"])                     # reconciliou pela tela: uma mensagem, uma leitura
    envio = _etapas(harness, obj["id"])["v1:send_message"]
    assert envio["status"] == "succeeded" and LIDO in json.loads(envio["commit_guard"])


async def test_reinicio_com_a_troca_segurada_pela_porta_retoma_do_segundo_app(harness: Harness) -> None:
    inner, ler = _preparar(harness)
    st = harness.state
    assert st is not None
    rede = {"exigida_sem_verificar": False}
    st.scheduler.rede_gate = lambda iid: "aguardando a rede exigida" if rede["exigida_sem_verificar"] else None

    async def decide(req: Any) -> Any:
        if req.ctx.step_key == "read_contact":
            rede["exigida_sem_verificar"] = True        # cai enquanto o 1º app trabalha: a troca é que segura
        return await ler(req)

    inner.decide = decide
    run = harness.run([IID])
    await harness.wait(lambda: (o := _objetivo(harness, run.id)) is not None and o["wait_reason"] == "rede", 60,
                       "troca segurada pela rede")
    obj = _objetivo(harness, run.id)
    etapas = _etapas(harness, obj["id"])
    assert etapas["v1:read_contact"]["status"] == "succeeded"
    assert etapas["v1:confirm_account"]["status"] == "ready" and etapas["v1:confirm_account"]["attempts"] == 0
    await harness.crash()

    # O backend novo nasce sem a porta (C4 é ligado pelo 25.6): a rede "voltou" e o despacho retoma do 2º app.
    await harness.boot()
    assert (await harness.wait_run(run.id, timeout=90)).status == "completed"
    _concluido_sem_reler(harness, obj["id"])


@pytest.fixture
def contas_em_outro_pacote() -> Iterator[None]:
    """O segundo app com PACOTE PRÓPRIO, declarando conta da persona (como o Outlook: `precisa_de_perfil`)."""
    register(PKG_CONTAS, None, AppDefinition(package=PKG_CONTAS, name="QA Contas", needs_profile=True,
                                             label="QA Contas"))
    try:
        yield
    finally:
        unregister(PKG_CONTAS)


async def test_reinicio_retoma_no_segundo_app_de_outro_pacote(harness: Harness, contas_em_outro_pacote: None) -> None:
    """O que ficou `not_run` com o dublê de um pacote só: depois do reinício, a etapa seguinte é de OUTRO app. A queda
    vem com a primeira etapa do segundo app em curso e o PRIMEIRO app ainda à frente; o backend novo retoma a etapa com
    o contexto do segundo app (é o pacote dele que o ator abre e que o foco confere), confere a conta da persona NELE
    e termina lá, sem reler o valor do primeiro.

    O ator por regras do provedor simulado só conhece as telas do QA; o segundo pacote encena as mesmas telas, e o
    dublê abaixo faz o que o ator real faz com o app da etapa no contexto: abre-o quando a tela é de outro app."""
    st = harness.state
    assert st is not None
    qa = st.db.one("SELECT activity FROM apps WHERE package=?", (QA,))
    st.db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('qa-contas','QA Contas',?,?,0)",
                  (PKG_CONTAS, qa["activity"]))
    _persona(harness, contas={"qa-contas": CONTA_NO_SEGUNDO_APP})
    fake = harness.fakes[IID]
    fake.pacotes_extras = (PKG_CONTAS,)
    inner = harness.ai.inner
    inner.plan = _plano_que_troca_de_app(inner)
    ler = _decide_leitura(inner, {"decisoes": 1})
    na_troca = asyncio.Event()

    async def decide(req: Any) -> Any:
        if req.ctx.app.package != PKG_CONTAS:
            return await ler(req)
        if req.ctx.step_key == "confirm_account" and not na_troca.is_set():
            na_troca.set()
            await asyncio.sleep(60)          # a etapa do 2º app fica em curso, com o 1º app à frente, até a queda
        if req.screen.package != PKG_CONTAS:
            return Decision(tool="open_app", args={"rationale": "abrir o app desta etapa", "package": None}), Usage()
        return await ler(replace(req, screen=replace(req.screen, package=QA)))

    inner.decide = decide
    run = harness.run([IID])
    await asyncio.wait_for(na_troca.wait(), 60)
    obj = _objetivo(harness, run.id)
    assert _etapas(harness, obj["id"])["v1:confirm_account"]["status"] == "running"
    assert fake.foreground == QA and not any(c.startswith(f"open_app:{PKG_CONTAS}") for c in fake.calls)
    await harness.crash()
    antes = len(fake.calls)

    await harness.boot()                                         # mesmo banco, mesmo aparelho (o QA ainda à frente)
    assert (await harness.wait_run(run.id, timeout=90)).status == "completed"
    _concluido_sem_reler(harness, obj["id"])
    assert fake.calls[antes:].count(f"open_app:{PKG_CONTAS}") == 1   # o app da etapa, aberto uma vez, depois da queda
    assert fake.foreground == PKG_CONTAS                             # a conversa e o envio foram no segundo app
    confirma = _etapas(harness, obj["id"])["v1:confirm_account"]
    assert confirma["status"] == "succeeded" and confirma["app_id"] == "qa-contas"


async def test_etapa_de_outro_app_nao_conclui_com_o_primeiro_app_na_frente(harness: Harness,
                                                                          contas_em_outro_pacote: None) -> None:
    """Falha ou incerteza nunca contam como sucesso: a etapa do `qa-contas` não fecha enquanto a tela é do QA.

    As duas telas são iguais (o dublê encena as mesmas no outro pacote), então a pós-condição "Conta: …" vale na tela
    do QA também. Antes, o `step_done` do ator com o QA à frente fechava a etapa do segundo app como comprovada — a
    conta conferida era a do app errado. Agora o executor recusa a conclusão fora do app da etapa, diz ao ator qual
    app abrir, e a etapa só fecha depois que o pacote dela chega à frente."""
    st = harness.state
    assert st is not None
    qa = st.db.one("SELECT activity FROM apps WHERE package=?", (QA,))
    st.db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('qa-contas','QA Contas',?,?,0)",
                  (PKG_CONTAS, qa["activity"]))
    _persona(harness, contas={"qa-contas": CONTA_NO_SEGUNDO_APP})
    fake = harness.fakes[IID]
    fake.pacotes_extras = (PKG_CONTAS,)
    inner = harness.ai.inner
    inner.plan = _plano_que_troca_de_app(inner)
    ler = _decide_leitura(inner, {"decisoes": 1})
    fora: list[tuple[str, list[str]]] = []           # (etapa, histórico) de cada decisão do 2º app com o QA à frente

    async def decide(req: Any) -> Any:
        if req.ctx.app.package != PKG_CONTAS:
            return await ler(req)
        if req.screen.package != PKG_CONTAS:
            fora.append((req.ctx.step_key, list(req.history)))
            if len(fora) == 1:                         # conclui sem abrir o app da etapa: a tela do QA "comprova"
                return Decision(tool="step_done", args={"rationale": "a conta aparece", "evidence": "Conta: …",
                                                        "delivery_level": None}), Usage()
            return Decision(tool="open_app", args={"rationale": "abrir o app desta etapa", "package": None}), Usage()
        return await ler(replace(req, screen=replace(req.screen, package=QA)))

    inner.decide = decide
    run = harness.run([IID])
    assert (await harness.wait_run(run.id, timeout=90)).status == "completed"
    obj = _objetivo(harness, run.id)
    # a mesma etapa viu o QA à frente DUAS vezes: a conclusão foi recusada, e o ator soube qual app abrir
    assert [k for k, _ in fora] == ["confirm_account", "confirm_account"]
    assert any("step_done REJEITADA" in h and PKG_CONTAS in h for h in fora[1][1])
    confirma = _etapas(harness, obj["id"])["v1:confirm_account"]
    acoes = st.db.query("SELECT a.tool, a.status FROM actions a JOIN attempts t ON t.id=a.attempt_id"
                        " WHERE t.step_id=? ORDER BY a.rowid", (confirma["id"],))
    assert [(r["tool"], r["status"]) for r in acoes][:2] == [("step_done", "rejected"), ("open_app", "done")]
    assert confirma["status"] == "succeeded" and confirma["attempts"] == 1
    assert fake.foreground == PKG_CONTAS and [m.contact for m in fake.messages] == [LIDO]


# ================================================================== pausa no meio da troca
async def test_pausar_com_a_etapa_do_segundo_app_em_curso_e_retomar_nao_rele(harness: Harness) -> None:
    """A pausa é a interrupção que volta sozinha: a etapa do 2º app cede no ponto seguro (`yielded`, tentativa
    devolvida) e, retomada, segue do 2º app com o valor gravado."""
    inner, ler = _preparar(harness)
    st = harness.state
    assert st is not None
    na_troca = asyncio.Event()
    run_id: dict[str, str] = {}

    async def decide(req: Any) -> Any:
        if req.ctx.step_key == "confirm_account" and not na_troca.is_set():
            na_troca.set()
            await harness.wait(lambda: bool(st.repo.run_row(run_id["id"])["pause_requested"]), 30, "pausa")
            return Decision(tool="observe_screen", args={"rationale": "a tela ainda carrega"}), Usage()
        return await ler(req)

    inner.decide = decide
    run = harness.run([IID])
    run_id["id"] = run.id
    await asyncio.wait_for(na_troca.wait(), 60)
    obj = _objetivo(harness, run.id)
    st.runs.pause(run.id)
    await harness.wait(lambda: IID not in st.scheduler.workers, 30, "worker largou o aparelho")
    confirma = _etapas(harness, obj["id"])["v1:confirm_account"]
    assert confirma["status"] == "ready" and confirma["attempts"] == 0         # cedeu, sem gastar tentativa
    st.runs.resume(run.id)
    assert (await harness.wait_run(run.id, timeout=60)).status == "completed"
    _concluido_sem_reler(harness, obj["id"])


# ================================================================== cancelamento no meio da troca
def _cancelado_sem_efeito(h: Harness, run_id: str, objective_id: str) -> None:
    """O fecho comum do cancelamento: o concluído do 1º app e a saída ficam; nada do 2º app conclui; nenhum efeito."""
    st = h.state
    assert st is not None
    obj = st.db.one("SELECT * FROM objectives WHERE id=?", (objective_id,))
    assert obj["status"] == "cancelled"
    assert "Nenhuma ação com efeito externo foi realizada" in obj["status_detail"]
    etapas = _etapas(h, objective_id)
    assert etapas["v1:open_app"]["status"] == "succeeded" and etapas["v1:read_contact"]["status"] == "succeeded"
    for chave in ("confirm_account", "open_conversation", "compose_message", "send_message", "verify_sent"):
        assert etapas[f"v1:{chave}"]["status"] == "cancelled", chave
    assert not h.fakes[IID].messages
    assert st.repo.step_outputs(objective_id) == {"contato": LIDO}
    assert _leituras(h, objective_id) == (1, 1)
    # o relatório diz o que foi lido e de onde, também na execução cancelada
    rel = st.runs.report(run_id)
    [valor] = rel["per_instance"][0]["values_read"]
    assert (valor["name"], valor["value"], valor["step_title"]) == ("contato", LIDO, "Ler o contato da lista")
    assert "## Valores lidos entre etapas" in rel["markdown"]


async def test_cancelar_com_a_etapa_do_segundo_app_em_curso_para_no_ponto_seguro(harness: Harness) -> None:
    inner, ler = _preparar(harness)
    st = harness.state
    assert st is not None
    na_troca = asyncio.Event()
    run_id: dict[str, str] = {}

    async def decide(req: Any) -> Any:
        if req.ctx.step_key == "confirm_account":
            na_troca.set()
            # espera o cancelamento e devolve uma ação que não conclui: a próxima volta do executor é o ponto seguro
            await harness.wait(lambda: bool(st.repo.run_row(run_id["id"])["cancel_requested"]), 30, "cancelamento")
            return Decision(tool="observe_screen", args={"rationale": "a tela ainda carrega"}), Usage()
        return await ler(req)

    inner.decide = decide
    run = harness.run([IID])
    run_id["id"] = run.id
    await asyncio.wait_for(na_troca.wait(), 60)
    obj = _objetivo(harness, run.id)
    st.runs.cancel(run.id)
    assert (await harness.wait_run(run.id, timeout=60)).status == "cancelled"
    _cancelado_sem_efeito(harness, run.id, obj["id"])
    confirma = _etapas(harness, obj["id"])["v1:confirm_account"]
    assert st.db.scalar("SELECT status FROM attempts WHERE step_id=?", (confirma["id"],)) == "cancelled"


async def test_cancelar_com_a_troca_segurada_pela_porta_nao_comeca_o_segundo_app(harness: Harness) -> None:
    inner, ler = _preparar(harness)
    st = harness.state
    assert st is not None
    rede = {"exigida_sem_verificar": False}
    st.scheduler.rede_gate = lambda iid: "aguardando a rede exigida" if rede["exigida_sem_verificar"] else None

    async def decide(req: Any) -> Any:
        if req.ctx.step_key == "read_contact":
            rede["exigida_sem_verificar"] = True
        return await ler(req)

    inner.decide = decide
    run = harness.run([IID])
    await harness.wait(lambda: (o := _objetivo(harness, run.id)) is not None and o["wait_reason"] == "rede", 60,
                       "troca segurada pela rede")
    obj = _objetivo(harness, run.id)
    st.runs.cancel(run.id)
    assert (await harness.wait_run(run.id, timeout=60)).status == "cancelled"
    _cancelado_sem_efeito(harness, run.id, obj["id"])
    assert _etapas(harness, obj["id"])["v1:confirm_account"]["attempts"] == 0


# ================================================================== sucessora no meio da troca
async def test_sucessora_no_meio_da_troca_e_recusada_e_nada_se_repete(harness: Harness) -> None:
    """A sucessora (ADR-047) nasce só de `needs_input`, que nunca executou etapa. No meio da troca, e depois de
    cancelada ali, a resposta é recusada sem criar execução — e a execução em curso segue do ponto em que estava."""
    inner, ler = _preparar(harness)
    st = harness.state
    assert st is not None
    rede = {"exigida_sem_verificar": False}
    st.scheduler.rede_gate = lambda iid: "aguardando a rede exigida" if rede["exigida_sem_verificar"] else None

    async def decide(req: Any) -> Any:
        if req.ctx.step_key == "read_contact":
            rede["exigida_sem_verificar"] = True
        return await ler(req)

    inner.decide = decide
    run = harness.run([IID])
    await harness.wait(lambda: (o := _objetivo(harness, run.id)) is not None and o["wait_reason"] == "rede", 60,
                       "troca segurada pela rede")
    obj = _objetivo(harness, run.id)
    assistente = ComandoAssistido(st.runs)
    corpo = RunSuccessorBody(command="Abra o QA Messenger e envie 'oi' para QA-001", mode="execute")
    with pytest.raises(RunError) as recusa:
        assistente.sucessora(run.id, corpo)
    assert recusa.value.code == "invalid_state"
    assert st.db.scalar("SELECT COUNT(*) FROM runs") == 1 and st.repo.run_row(run.id)["status"] == "running"

    # a porta solta: a execução original conclui do 2º app, sem reler e com uma mensagem só
    rede["exigida_sem_verificar"] = False
    st.scheduler.wake()
    assert (await harness.wait_run(run.id, timeout=60)).status == "completed"
    _concluido_sem_reler(harness, obj["id"])


async def test_sucessora_de_execucao_cancelada_na_troca_e_recusada(harness: Harness) -> None:
    inner, ler = _preparar(harness)
    st = harness.state
    assert st is not None
    rede = {"exigida_sem_verificar": False}
    st.scheduler.rede_gate = lambda iid: "aguardando a rede exigida" if rede["exigida_sem_verificar"] else None

    async def decide(req: Any) -> Any:
        if req.ctx.step_key == "read_contact":
            rede["exigida_sem_verificar"] = True
        return await ler(req)

    inner.decide = decide
    run = harness.run([IID])
    await harness.wait(lambda: (o := _objetivo(harness, run.id)) is not None and o["wait_reason"] == "rede", 60,
                       "troca segurada pela rede")
    st.runs.cancel(run.id)
    assert (await harness.wait_run(run.id, timeout=60)).status == "cancelled"
    with pytest.raises(RunError) as recusa:
        ComandoAssistido(st.runs).sucessora(run.id, RunSuccessorBody(command="Continue de onde parou", mode="execute"))
    assert recusa.value.code == "invalid_state"
    assert st.db.scalar("SELECT COUNT(*) FROM runs") == 1 and not harness.fakes[IID].messages
