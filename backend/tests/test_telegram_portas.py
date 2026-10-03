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

import pytest

from app.models import RunCreate
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


async def test_pergunta_de_le_a_pergunta_da_execucao_e_o_codigo_da_recusa_sobe(harness: Harness,
                                                                              como_telegram: None) -> None:
    st = harness.state
    assert st is not None
    portas = _portas(harness)
    run = st.runs.create(RunCreate(command="abrir o QA Messenger no android-02", instance_ids=["android-03"],
                                   idempotency_key="telegram:ni-2"))
    await harness.wait_run(run.id, ("needs_input",))
    inteiro, fim = portas.pergunta_de(run.id), portas.pergunta_de(run.id[-6:])
    assert inteiro and inteiro == fim                                           # o id inteiro e o fim dele
    assert portas.pergunta_de("r-nao-existe-000000") == ""
    # O código do erro do caminho comum sobe junto da frase (o serviço de entrada trata `credencial_na_resposta`).
    st.runs.cancel(run.id)
    with pytest.raises(RecusaDaCentral) as exc:
        portas.responder(run.id, "pode ser")
    assert exc.value.codigo == "invalid_state"


async def test_ha_pergunta_sensivel_aberta_le_as_execucoes_esperando(harness: Harness, como_telegram: None) -> None:
    st = harness.state
    assert st is not None
    portas = _portas(harness)
    assert portas.ha_pergunta_sensivel_aberta() is False                        # nada espera
    run = st.runs.create(RunCreate(command="abrir o QA Messenger no android-02", instance_ids=["android-03"],
                                   idempotency_key="telegram:ni-3"))
    await harness.wait_run(run.id, ("needs_input",))
    assert portas.ha_pergunta_sensivel_aberta() is False                        # pergunta de destino, não credencial
