"""28.15 (ADR-071): as portas da conversa do Telegram chamam os MESMOS serviços das rotas do painel.

Prova `simulated`: harness (aparelhos falsos na porta 5640, planejador simulado), nenhuma rede. O que se prova:
- a prévia é a do painel (`previa_de_alvos`): o destino tirado do texto volta com origem `texto`;
- criar ecoa esses alvos (a confirmação que o painel faz) e a execução nasce; a mesma chave não cria outra;
- sem o eco, a mesma criação seria recusada (`alvos_nao_confirmados`), e a recusa volta como `RecusaDaCentral`;
- aprovar/vetar é o `ApprovalService.decide`, e quem decidiu é `telegram:dono` (o ContextVar da sessão);
- responder é a sucessora do painel: a antiga aponta para a nova;
- o desfecho só sai com a execução terminada.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.models import MissingInfo, RunCreate
from app.modules.avisos.infrastructure.entrada import (
    OPERADOR_DO_TELEGRAM,
    RecusaDaCentral,
)
from app.modules.avisos.infrastructure.portas_da_central import PortasReais
from app.security.sessions import OPERADOR

from .conftest import Harness

pytestmark = pytest.mark.asyncio


def _portas(h: Harness) -> PortasReais:
    st = h.state
    assert st is not None
    return st.telegram_entrada.portas  # type: ignore[return-value]


@pytest.fixture
def como_telegram():
    token = OPERADOR.set(OPERADOR_DO_TELEGRAM)
    yield
    OPERADOR.reset(token)


async def test_previa_e_criar_com_eco_dos_alvos(harness: Harness, como_telegram: None) -> None:
    st = harness.state
    assert st is not None
    portas = _portas(harness)
    texto = "abrir o QA Messenger no android-01"
    p = portas.previa(texto)
    assert [(a["instance_id"], a["origem"]) for a in p.alvos] == [("android-01", "texto")]
    assert "android-01" not in p.comando
    # Sem alvo confirmado não há criação (o `RunCreate` exige seleção); com o eco da prévia, que é o que o botão
    # Executar manda, a execução nasce com o texto como veio.
    with pytest.raises(RecusaDaCentral):
        portas.criar(texto, [], "telegram:sem-alvo")
    run_id, curta = portas.criar(texto, p.alvos, "telegram:900")
    assert curta and run_id.endswith(curta[-6:])
    row = st.repo.run_row(run_id)
    assert row is not None and row["command"] == texto and row["idempotency_key"] == "telegram:900"
    assert portas.criar(texto, p.alvos, "telegram:900")[0] == run_id          # a mesma update não cria outra
    await harness.wait_run(run_id)
    assert portas.desfecho(run_id) is not None


async def test_aparelho_desconhecido_volta_como_recusa(harness: Harness, como_telegram: None) -> None:
    portas = _portas(harness)
    with pytest.raises(RecusaDaCentral):
        portas.criar("abrir o QA", [{"instance_id": "android-99", "profile_id": None}], "telegram:901")


async def test_decidir_e_o_servico_do_painel_e_quem_decide_e_telegram_dono(harness: Harness,
                                                                           como_telegram: None) -> None:
    st = harness.state
    assert st is not None
    portas = _portas(harness)
    pedido = st.approvals.open(profile_id=None, capability="SEND_MESSAGE", summary="Responder a QA-001",
                               content="bom dia")
    assert pedido.id in portas.aprovacoes_pendentes()
    assert any(p.ident == pedido.id and "bom dia" in p.resumo for p in portas.pendencias())
    assert portas.decidir(pedido.id, "reject").startswith("Vetado")
    row = st.db.one("SELECT status, decided_by FROM pending_approvals WHERE id=?", (pedido.id,))
    assert row is not None and (row["status"], row["decided_by"]) == ("rejected", OPERADOR_DO_TELEGRAM)
    with pytest.raises(RecusaDaCentral):
        portas.decidir(pedido.id, "approve")                                    # já decidida: a frase do painel


async def test_responder_e_a_sucessora_do_painel(harness: Harness, como_telegram: None) -> None:
    st = harness.state
    assert st is not None
    portas = _portas(harness)
    # Uma execução em needs_input: o texto contradiz a seleção (pergunta estruturada, sem plano).
    run = st.runs.create(RunCreate(command="abrir o QA Messenger no android-02", instance_ids=["android-03"],
                                   idempotency_key="telegram:ni-1"))
    await harness.wait_run(run.id, ("needs_input",))
    assert run.id in portas.execucoes_esperando()
    nova, curta = portas.responder(run.id, "pode ser no android-03")
    assert nova != run.id and curta
    antiga = st.repo.run_row(run.id)
    assert antiga is not None and antiga["status"] == "cancelled"
    assert curta in str(antiga["status_detail"])
    assert portas.desfecho(nova) is None or isinstance(portas.desfecho(nova), str)


async def test_status_resume_o_parque(harness: Harness) -> None:
    texto = _portas(harness).status()
    assert texto.startswith("Central:") and "Aparelhos online:" in texto and "Esperando você:" in texto


async def test_pergunta_sensivel_pelo_id_ou_pelo_fim_e_o_codigo_da_recusa_sobe(harness: Harness,
                                                                              como_telegram: None) -> None:
    st = harness.state
    assert st is not None
    portas = _portas(harness)
    run = st.runs.create(RunCreate(command="abrir o QA Messenger no android-02", instance_ids=["android-03"],
                                   idempotency_key="telegram:ni-2"))
    await harness.wait_run(run.id, ("needs_input",))
    # Pergunta de destino não é credencial, pelo id inteiro e pelo fim dele; execução que não existe, também nada.
    assert portas.pergunta_sensivel(run.id) is None and portas.pergunta_sensivel(run.id[-6:]) is None
    assert portas.pergunta_sensivel("r-nao-existe-000000") is None
    # O código do erro do caminho comum sobe junto da frase (o serviço de entrada trata `credencial_na_resposta`).
    st.runs.cancel(run.id)
    with pytest.raises(RecusaDaCentral) as exc:
        portas.responder(run.id, "pode ser")
    assert exc.value.codigo == "invalid_state"


async def test_pergunta_sensivel_aberta_com_a_porta_real(harness: Harness, como_telegram: None) -> None:
    """E5 da revisão de autora: o caso positivo com as portas REAIS, sobre as leituras públicas do 29.52. E o E3: a
    resposta a essa pergunta volta do caminho comum com o código `credencial_na_resposta`."""
    st = harness.state
    assert st is not None
    portas = _portas(harness)
    assert portas.pergunta_sensivel(None) is None                               # nada espera
    run = st.runs.create(RunCreate(command="abrir o QA Messenger no android-02", instance_ids=["android-03"],
                                   idempotency_key="telegram:ni-3"))
    await harness.wait_run(run.id, ("needs_input",))
    assert portas.pergunta_sensivel(None) is None                               # pergunta de destino, não credencial
    plano0 = harness.ai.inner.plan

    async def plan(req: Any) -> Any:                     # o planejador com defeito pede a senha (ADR-040)
        plano, uso = await plano0(req)
        plano.missing = [MissingInfo(field="password", question="Qual é a senha da conta do QA Messenger?")]
        plano.steps = []
        return plano, uso

    harness.ai.inner.plan = plan
    senha = st.runs.create(RunCreate(command="Abra o QA Messenger e envie uma mensagem", instance_ids=["android-01"],
                                     idempotency_key="telegram:ni-4"))
    await harness.wait_run(senha.id, ("needs_input",))
    assert portas.pergunta_sensivel(None) == "senha"
    assert portas.pergunta_sensivel(senha.id) == "senha" and portas.pergunta_sensivel(senha.id[-6:]) == "senha"
    with pytest.raises(RecusaDaCentral) as exc:
        portas.responder(senha.id, "kiwi2024!")
    assert exc.value.codigo == "credencial_na_resposta" and "kiwi2024" not in str(exc.value)
    assert st.repo.run_row(senha.id)["status"] == "needs_input"
    # A resposta atrasada, pelo id inteiro (o reply ao aviso), depois que a execução saiu do needs_input: ainda é senha.
    st.runs.cancel(senha.id)
    assert st.repo.run_row(senha.id)["status"] != "needs_input"
    assert portas.pergunta_sensivel(senha.id) == "senha" and portas.pergunta_sensivel(None) is None


# ===================================================================== 28.27: a porta do plano pelo canal
async def test_executar_do_canal_cria_so_o_plano_e_a_porta_inicia(harness: Harness, como_telegram: None) -> None:
    """`modo="plan"` para em `planned`; a prévia é a do `GET /runs/{id}/porta`; o "Executar (aprova N)" é o
    `aprovar_plano` (30.61), e só ele inicia."""
    portas = _portas(harness)
    texto = "abrir o QA Messenger no android-01"
    run_id, _ = portas.criar(texto, portas.previa(texto).alvos, "telegram:950", modo="plan")
    await harness.wait_run(run_id, statuses=("planned",))
    assert portas.estado_da_execucao(run_id) == "planned"
    previa = portas.porta(run_id)
    assert isinstance(previa.get("itens"), list) and previa.get("hash_do_plano")
    resposta = portas.aprovar_plano(run_id, [])
    assert resposta["aprovacoes"] == [] and portas.estado_da_execucao(run_id) != "planned"
    with pytest.raises(RecusaDaCentral):
        portas.porta(run_id)                                 # fora de `planned` a porta não se aplica
    assert portas.estado_da_execucao("r-nao-existe") is None


async def test_plano_mudou_volta_com_a_previa_nova_e_nada_inicia(harness: Harness, como_telegram: None) -> None:
    from app.modules.avisos.infrastructure.entrada import PlanoMudou

    portas = _portas(harness)
    texto = "abrir o QA Messenger no android-01"
    run_id, _ = portas.criar(texto, portas.previa(texto).alvos, "telegram:951", modo="plan")
    await harness.wait_run(run_id, statuses=("planned",))
    with pytest.raises(PlanoMudou) as mudou:
        portas.aprovar_plano(run_id, [("etapa-que-nao-existe", "a" * 64)])
    assert mudou.value.codigo == "plano_mudou" and mudou.value.mudaram and "itens" in mudou.value.previa
    assert portas.estado_da_execucao(run_id) == "planned"
    assert portas.imagem_da_etapa(run_id, "etapa-que-nao-existe") is None
    portas.cancelar(run_id)
    await harness.wait_run(run_id, statuses=("cancelled",))


async def test_cancelar_do_canal_nao_cancela_execucao_que_outro_gesto_iniciou(harness: Harness,
                                                                              como_telegram: None) -> None:
    """Nota R1 da revisão do #336: entre a leitura `planned` do canal e o cancelamento, o painel pode iniciar. O
    `cancelar` da porta é condicionado a `planned` num `UPDATE` só: a execução que já seguiu não é tocada."""
    portas = _portas(harness)
    texto = "abrir o QA Messenger no android-01"
    run_id, _ = portas.criar(texto, portas.previa(texto).alvos, "telegram:952", modo="plan")
    await harness.wait_run(run_id, statuses=("planned",))
    portas.iniciar(run_id)                                   # o outro gesto chegou antes
    portas.cancelar(run_id)                                  # o abandono do canal, atrasado
    st = harness.state
    assert st is not None
    assert not st.db.scalar("SELECT cancel_requested FROM runs WHERE id=?", (run_id,))
    assert portas.estado_da_execucao(run_id) not in ("cancelled", "cancelling")


async def test_inicio_depois_da_marca_do_cancelamento_e_recusado(harness: Harness, como_telegram: None) -> None:
    """O outro lado da corrida: o cancelamento condicionado marcou `cancel_requested` e ainda não fechou; o início
    que leu `planned` antes não pode passar por cima (compare-and-set no `start`)."""
    portas = _portas(harness)
    texto = "abrir o QA Messenger no android-01"
    run_id, _ = portas.criar(texto, portas.previa(texto).alvos, "telegram:953", modo="plan")
    await harness.wait_run(run_id, statuses=("planned",))
    st = harness.state
    assert st is not None
    st.db.execute("UPDATE runs SET cancel_requested=1 WHERE id=?", (run_id,))
    with pytest.raises(RecusaDaCentral):
        portas.iniciar(run_id)
    assert portas.estado_da_execucao(run_id) == "planned"
    st.db.execute("UPDATE runs SET cancel_requested=0 WHERE id=?", (run_id,))
    portas.cancelar(run_id)                                  # planned: o condicionado cancela de fato
    await harness.wait_run(run_id, statuses=("cancelled",))


async def test_porta_recusa_execucao_que_esta_sendo_cancelada(harness: Harness, como_telegram: None) -> None:
    """Revisão de `cec9ddca` (S2/S7): `planned` com `cancel_requested` (a marca do cancelamento do canal antes do fecho)
    não oferece a prévia nem grava sim: sem isto, sobravam sins `approved` de origem `plano` numa execução cancelada."""
    portas = _portas(harness)
    texto = "abrir o QA Messenger no android-01"
    run_id, _ = portas.criar(texto, portas.previa(texto).alvos, "telegram:954", modo="plan")
    await harness.wait_run(run_id, statuses=("planned",))
    st = harness.state
    assert st is not None
    st.db.execute("UPDATE runs SET cancel_requested=1 WHERE id=?", (run_id,))
    with pytest.raises(RecusaDaCentral) as previa:
        portas.porta(run_id)
    assert previa.value.codigo == "invalid_state" and "sendo cancelada" in str(previa.value)
    with pytest.raises(RecusaDaCentral) as gesto:
        portas.aprovar_plano(run_id, [])
    assert gesto.value.codigo == "invalid_state" and "sendo cancelada" in str(gesto.value)
    assert not st.db.scalar("SELECT COUNT(*) FROM pending_approvals WHERE run_id=? AND origem='plano'", (run_id,))
    st.db.execute("UPDATE runs SET cancel_requested=0 WHERE id=?", (run_id,))
    portas.cancelar(run_id)
    await harness.wait_run(run_id, statuses=("cancelled",))


async def test_inicio_recusado_depois_do_gesto_vira_recusa_da_porta(harness: Harness, como_telegram: None,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """O cancelamento que chega entre a transação do `aprovar_plano` e o `start`: a resposta é a da porta
    (`invalid_state`), não um `RunError` do serviço."""
    from app.taskqueue.service import RunError

    portas = _portas(harness)
    texto = "abrir o QA Messenger no android-01"
    run_id, _ = portas.criar(texto, portas.previa(texto).alvos, "telegram:955", modo="plan")
    await harness.wait_run(run_id, statuses=("planned",))
    st = harness.state
    assert st is not None

    def cancelado_no_meio(rid: str, *, por: str) -> None:
        raise RunError("invalid_state", "A execução mudou de estado e não pode ser iniciada.")

    monkeypatch.setattr(st.runs, "start", cancelado_no_meio)
    with pytest.raises(RecusaDaCentral) as recusa:
        portas.aprovar_plano(run_id, [])
    assert recusa.value.codigo == "invalid_state"
    monkeypatch.undo()
    portas.cancelar(run_id)
    await harness.wait_run(run_id, statuses=("cancelled",))


async def test_marca_e_fecho_do_cancelamento_na_mesma_transacao(harness: Harness, como_telegram: None,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """Revisão de `cec9ddca` (S1): se o fecho falha, a marca volta junto; a execução não fica `planned` com
    `cancel_requested`, que só o Cancelar do painel destravaria."""
    portas = _portas(harness)
    texto = "abrir o QA Messenger no android-01"
    run_id, _ = portas.criar(texto, portas.previa(texto).alvos, "telegram:956", modo="plan")
    await harness.wait_run(run_id, statuses=("planned",))
    st = harness.state
    assert st is not None

    def fecho_que_quebra(rid: str, detalhe: str, **kw: Any) -> None:
        raise RuntimeError("queda no meio")

    monkeypatch.setattr(st.runs, "_cancelar_antes_de_iniciar", fecho_que_quebra)
    with pytest.raises(RuntimeError):
        st.runs.cancel(run_id, por="telegram:dono", so_se_planejada=True)
    linha = st.db.one("SELECT status, cancel_requested FROM runs WHERE id=?", (run_id,))
    assert linha is not None and linha["status"] == "planned" and not linha["cancel_requested"]
    monkeypatch.undo()
    assert st.runs.cancel(run_id, por="telegram:dono", so_se_planejada=True) is not None
    await harness.wait_run(run_id, statuses=("cancelled",))


async def test_cancelamento_entre_a_transacao_e_o_inicio_expira_os_sins_do_plano(harness: Harness,
                                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Pergunta do Aprendizado na conferência de `d1cdbb2c`: o cancelamento do canal chega DEPOIS de o `aprovar_plano`
    gravar os sins e ANTES do `start`. Os sins de origem `plano` não podem ficar `approved` numa execução cancelada:
    o `_cancelar_antes_de_iniciar` os expira, e o gesto volta como recusa da porta com o código do serviço."""
    from app.porta_do_plano import AprovarPlanoBody, ItemAprovado, PortaIndisponivel, aprovar_plano

    from .test_porta_do_plano import _plano_com_dm

    st = harness.state
    assert st is not None
    itens = _plano_com_dm(st)
    original = st.runs.start

    def cancelado_antes_do_inicio(run_id: str, *, por: str) -> Any:
        assert st.runs.cancel(run_id, por="telegram:dono", so_se_planejada=True) is not None
        return original(run_id, por=por)

    monkeypatch.setattr(st.runs, "start", cancelado_antes_do_inicio)
    corpo = AprovarPlanoBody(aprovar=[ItemAprovado(step_id=itens["dm"]["step_id"], chave=itens["dm"]["chave"])],
                             tirar=[itens["dm2"]["step_id"]])
    with pytest.raises(PortaIndisponivel) as recusa:
        aprovar_plano(st, "run-p", corpo, por="flavio")
    assert recusa.value.codigo == "invalid_state"
    assert st.db.scalar("SELECT status FROM runs WHERE id='run-p'") == "cancelled"
    sins = [dict(r) for r in st.db.query("SELECT status FROM pending_approvals WHERE run_id='run-p' AND origem='plano'")]
    assert sins and all(s["status"] != "approved" for s in sins)
    with pytest.raises(PortaIndisponivel) as depois:                     # quem cancelou lê o motivo certo
        aprovar_plano(st, "run-p", corpo, por="flavio")
    assert "foi cancelada" in depois.value.mensagem


async def test_inicio_que_perde_para_outro_inicio_diz_que_o_gesto_valeu(harness: Harness,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """28.36: OUTRO início (o "Iniciar" do painel, a outra porta) chega entre a transação do `aprovar_plano` e o `start`
    dele. O gesto valeu (os sins ficam `approved` e a execução roda com eles): a recusa segue `invalid_state`, mas com
    texto próprio, e não o de cancelamento."""
    from app.models import RunStatus
    from app.porta_do_plano import (INICIADA_POR_OUTRO_GESTO, AprovarPlanoBody, ItemAprovado, PortaIndisponivel,
                                    aprovar_plano)

    from .test_porta_do_plano import _plano_com_dm

    st = harness.state
    assert st is not None
    itens = _plano_com_dm(st)
    original = st.runs.start

    def outro_inicio_antes(run_id: str, *, por: str) -> Any:
        # O compare-and-set do outro início, sem o agendador: o que se mede é a resposta deste gesto.
        assert st.repo.set_run_status(run_id, RunStatus.running, None, message="outro início",
                                      so_se=(RunStatus.planned,))
        return original(run_id, por=por)

    monkeypatch.setattr(st.runs, "start", outro_inicio_antes)
    corpo = AprovarPlanoBody(aprovar=[ItemAprovado(step_id=itens["dm"]["step_id"], chave=itens["dm"]["chave"])],
                             tirar=[])
    with pytest.raises(PortaIndisponivel) as recusa:
        aprovar_plano(st, "run-p", corpo, por="flavio")
    assert recusa.value.codigo == "invalid_state" and recusa.value.mensagem == INICIADA_POR_OUTRO_GESTO
    assert st.db.scalar("SELECT status FROM runs WHERE id='run-p'") == "running"
    sins = [r["status"] for r in st.db.query("SELECT status FROM pending_approvals WHERE run_id='run-p' AND origem='plano'")]
    assert sins == ["approved"]


async def test_a_porta_trava_a_linha_da_execucao_antes_de_gravar_o_sim(harness: Harness) -> None:
    """28.36: dentro da transação, a conferência da execução é o UPDATE que trava a linha (no PostgreSQL, o cancelamento
    de outro backend espera o COMMIT e enxerga os sins), e vem antes de o primeiro sim ser gravado. A corrida entre dois
    backends não roda num processo só; aqui fica a ordem."""
    from app.porta_do_plano import AprovarPlanoBody, ItemAprovado, aprovar_plano

    from .test_porta_do_plano import _plano_com_dm

    st = harness.state
    assert st is not None
    itens = _plano_com_dm(st)
    vistos: list[str] = []
    execute = st.db.execute

    def espiao(sql: str, params: Any = ()) -> Any:
        vistos.append(" ".join(sql.split()))
        return execute(sql, params)

    st.db.execute = espiao  # type: ignore[method-assign]
    try:
        corpo = AprovarPlanoBody(aprovar=[ItemAprovado(step_id=itens["dm"]["step_id"], chave=itens["dm"]["chave"])],
                                 tirar=[])
        try:
            aprovar_plano(st, "run-p", corpo, por="flavio")
        except Exception:  # noqa: BLE001 - o início pode recusar no harness; o que se mede é a ordem na transação
            pass
    finally:
        st.db.execute = execute  # type: ignore[method-assign]
    trava = next(i for i, s in enumerate(vistos) if s.startswith("UPDATE runs SET cancel_requested=cancel_requested"))
    sim = next(i for i, s in enumerate(vistos) if s.startswith("INSERT INTO pending_approvals"))
    assert trava < sim
