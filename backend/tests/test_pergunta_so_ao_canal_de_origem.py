"""31.279 (ADR-086): a pergunta de uma execução vai só ao canal de ORIGEM do comando, e a execução de validação ou
avaliação nunca avisa o dono nem vira sucessora por resposta dele.

Prova `simulated`: canal falso, harness com aparelhos falsos (porta 5640) e planejador simulado, nenhuma rede. O que se prova:
- o contrato: o canal da pergunta sai da chave (`telegram:`, `trello:`, sem marca = painel); validação, avaliação (`eval-`),
  lote, ensaio e operação são do sistema (canal `None`); a sucessora herda o prefixo da respondida;
- o aviso (`run.needs_input`, `objective.waiting_user`): só o comando do Telegram avisa o Telegram; a aprovação segue o canal
  de aprovação; a execução de um pedido persistente segue como antes;
- as portas: cada canal só enxerga (e só responde) as perguntas do seu canal; a de avaliação e a de validação não aparecem em
  canal nenhum e `responder` as recusa, sem sucessora;
- a sucessora de uma execução do Telegram segue sendo do Telegram, e a de uma avaliação segue sendo do sistema.

`real`: `not_run`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.contracts.origem import (PREFIXO_AVALIACAO, canal_da_pergunta, chave_da_sucessora, e_execucao_do_sistema,
                                  eh_execucao_de_validacao)
from app.models import RunCreate
from app.modules.avisos.infrastructure.entrada import OPERADOR_DO_TELEGRAM, RecusaDaCentral
from app.security.sessions import OPERADOR
from app.taskqueue.assistente import ComandoAssistido, RunSuccessorBody
from app.util import now

from .conftest import Harness
from .test_avisos_objetivo_parado import _dados, _obj
from .test_avisos_servico import AQUI, CanalFalso, Relogio, _backend, _cfg


# ===================================================================== 1. o contrato
@pytest.mark.parametrize("chave,prova,esperado", [
    (None, None, "painel"),
    ("k-do-painel-0001", None, "painel"),
    ("telegram:9001", None, "telegram"),
    ("trello:abc", None, "trello"),
    ("validacao:p1", None, None),
    ("eval-0a1b2c", None, None),
    ("lote:jev:1", None, None),
    ("ensaio:p1", None, None),
    ("op:o1:abc", None, None),
    ("telegram:9001", "fluxo-1", None),           # a prova de fluxo ganha da chave
])
def test_o_canal_da_pergunta_sai_da_chave(chave: str | None, prova: str | None, esperado: str | None) -> None:
    assert canal_da_pergunta(prova, chave) == esperado


def test_avaliacao_e_do_sistema_mas_nao_e_validacao_de_fluxo() -> None:
    assert e_execucao_do_sistema(None, f"{PREFIXO_AVALIACAO}0a1b2c") is True
    assert eh_execucao_de_validacao(None, f"{PREFIXO_AVALIACAO}0a1b2c") is False       # outra coisa: não parte da prova
    assert e_execucao_do_sistema(None, "telegram:1") is False and e_execucao_do_sistema(None, None) is False


@pytest.mark.parametrize("respondida", ["telegram:9001", "trello:c1", "validacao:p1", "eval-0a1b", "lote:jev:1", "op:o:a",
                                        "ensaio:p", "k-do-painel", None])
def test_a_sucessora_herda_a_origem_da_respondida(respondida: str | None) -> None:
    nova = chave_da_sucessora(respondida, "run-" + "x" * 36, "0123456789abcdef")
    assert canal_da_pergunta(None, nova) == canal_da_pergunta(None, respondida), (respondida, nova)
    assert e_execucao_do_sistema(None, nova) == e_execucao_do_sistema(None, respondida)
    assert len(nova) <= 120, "RunCreate.idempotency_key aceita até 120"
    assert "sucessora-" in nova
    # a mesma resposta à mesma execução dá a mesma chave (o duplo clique deduplica); outra resposta, outra chave
    assert nova == chave_da_sucessora(respondida, "run-" + "x" * 36, "0123456789abcdef")
    assert nova != chave_da_sucessora(respondida, "run-" + "x" * 36, "fedcba9876543210")


# ===================================================================== 2. o aviso
def _run(banco, run_id: str, chave: str, *, pedido: str | None = None) -> None:
    banco.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at, pedido_id)"
                  " VALUES (?,?,?,?,?,?,?,?)", (run_id, chave, "abrir o app", "single", "needs_input", "[]",
                                                now().isoformat(), pedido))


def test_so_o_comando_do_telegram_avisa_o_telegram(tmp_path: Path) -> None:
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    for rid, chave in (("rt", "telegram:9001"), ("rp", "k-do-painel"), ("rr", "trello:c1"), ("re", "eval-0a1b"),
                       ("rv", "validacao:p1"), ("rl", "lote:jev:1")):
        _run(banco, rid, chave)
    parou = lambda rid: servico.enfileirar_evento(  # noqa: E731
        "run.updated", {"run": {"id": rid, "status": "needs_input"}}, 1)
    assert parou("rt") is True, "o comando do Telegram pergunta no Telegram"
    for rid in ("rp", "rr", "re", "rv", "rl"):
        assert parou(rid) is False, f"{rid}: a pergunta de outro canal ou do sistema foi ao Telegram"
    assert banco.scalar("SELECT COUNT(*) FROM avisos_entregas") == 1


def test_o_objetivo_parado_segue_a_mesma_regra(tmp_path: Path) -> None:
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    for rid, chave in (("rt", "telegram:9001"), ("rp", "k-do-painel"), ("re", "eval-0a1b")):
        _run(banco, rid, chave)

    def parou(rid: str) -> bool:
        return servico.enfileirar_evento("objective.updated", _dados(
            _obj(id=f"{rid}:o1", run_id=rid, instance_id="android-13", finished_at="2026-10-07T22:00:00+00:00")), 1)
    assert parou("rt") is True
    assert parou("rp") is False and parou("re") is False


def test_a_aprovacao_nao_e_pergunta_e_segue_o_canal_de_aprovacao(tmp_path: Path) -> None:
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    _run(banco, "rp", "k-do-painel")
    _run(banco, "re", "eval-0a1b")
    _run(banco, "rv", "validacao:p1")
    aprovacao = lambda aid, rid: servico.enfileirar_evento(  # noqa: E731
        "approval.pending", {"approval": {"id": aid, "run_id": rid}}, 2)
    assert aprovacao("ap-p", "rp") is True, "a aprovação de uma execução do painel continua indo ao canal de aprovação"
    assert aprovacao("ap-e", "re") is False, "a avaliação nunca avisa o dono, nem a aprovação dela"
    assert aprovacao("ap-v", "rv") is False


def test_a_execucao_de_pedido_persistente_segue_avisando_como_antes(tmp_path: Path) -> None:
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    _run(banco, "rped", "ped:p1:g1:2026-10-07T10:00:00Z:t1", pedido="ped_x")
    assert servico.enfileirar_evento("run.updated", {"run": {"id": "rped", "status": "needs_input"}}, 1) is True


def test_falha_ao_conferir_a_origem_o_aviso_segue(tmp_path: Path) -> None:
    """O dono nunca perde um aviso de pessoa por erro de leitura: sem a linha da execução, o aviso sai."""
    servico, _, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    assert servico.enfileirar_evento("run.updated", {"run": {"id": "sem-linha", "status": "needs_input"}}, 1) is True


# ===================================================================== 3. as portas e a sucessora

@pytest.fixture
def como_telegram():
    token = OPERADOR.set(OPERADOR_DO_TELEGRAM)
    yield
    OPERADOR.reset(token)


async def _pergunta(h: Harness, chave: str):
    """Uma execução que para pedindo informação (o texto contradiz a seleção)."""
    st = h.state
    assert st is not None
    run = st.runs.create(RunCreate(command="abrir o QA Messenger no android-02", instance_ids=["android-03"],
                                   idempotency_key=chave))
    await h.wait_run(run.id, ("needs_input",))
    return run


@pytest.mark.asyncio
async def test_cada_canal_so_enxerga_as_perguntas_do_seu_canal(harness: Harness, como_telegram: None) -> None:
    st = harness.state
    assert st is not None
    todas = st.telegram_entrada.portas
    ids = {nome: (await _pergunta(harness, chave)).id for nome, chave in (
        ("telegram", "telegram:q-1"), ("trello", "trello:q-2"), ("painel", "k-do-painel-q3"), ("avaliacao", "eval-q4-0001"),
        ("validacao", "validacao:q5"))}
    do_telegram, do_trello = todas.para("telegram"), todas.para("trello")
    inteira = todas.para("telegram")
    inteira._canal = None
    assert set(do_telegram.execucoes_esperando()) & set(ids.values()) == {ids["telegram"]}
    assert set(do_trello.execucoes_esperando()) & set(ids.values()) == {ids["trello"]}
    assert set(inteira.execucoes_esperando()) & set(ids.values()) == {ids["telegram"], ids["trello"], ids["painel"]}
    assert {p.ident for p in do_trello.pendencias() if p.tipo == "pergunta"} & set(ids.values()) == {ids["trello"]}
    assert {p.ident for p in do_telegram.pendencias() if p.tipo == "pergunta"} & set(ids.values()) == {ids["telegram"]}


@pytest.mark.asyncio
async def test_responder_recusa_avaliacao_e_validacao_sem_criar_sucessora(harness: Harness, como_telegram: None) -> None:
    st = harness.state
    assert st is not None
    portas = st.telegram_entrada.portas
    for chave in ("eval-r-1", "validacao:r-2"):
        run = await _pergunta(harness, chave)
        antes = st.db.scalar("SELECT COUNT(*) FROM runs")
        with pytest.raises(RecusaDaCentral):
            portas.responder(run.id, "pode ser no android-03")
        assert st.repo.run_row(run.id)["status"] == "needs_input", "a recusa não pode tirar a execução da espera"
        assert st.db.scalar("SELECT COUNT(*) FROM runs") == antes, "a resposta do dono criou uma sucessora"


@pytest.mark.asyncio
async def test_a_sucessora_segue_no_canal_e_a_de_avaliacao_segue_do_sistema(harness: Harness, como_telegram: None) -> None:
    st = harness.state
    assert st is not None
    portas = st.telegram_entrada.portas
    run = await _pergunta(harness, "telegram:s-1")
    nova, _ = portas.responder(run.id, "pode ser no android-03")
    chave_nova = st.repo.run_row(nova)["idempotency_key"]
    assert chave_nova.startswith("telegram:") and canal_da_pergunta(None, chave_nova) == "telegram"
    # A avaliação responde pelo painel (quem a disparou): a sucessora existe e continua sendo do sistema.
    avaliacao = await _pergunta(harness, "eval-s-2")
    sucessora, _ = ComandoAssistido(st.runs).sucessora(
        avaliacao.id, RunSuccessorBody(command="abrir o QA Messenger no android-03", mode="execute"), por="harness")
    chave_sucessora = st.repo.run_row(sucessora.id)["idempotency_key"]
    assert chave_sucessora.startswith(PREFIXO_AVALIACAO) and e_execucao_do_sistema(None, chave_sucessora)
