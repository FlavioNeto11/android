"""30.31 (fatia 2): o ensaio só de leitura e a conferência do efeito pelo próprio app de QA.

- O ENSAIO (`ensaio:<pedido>`) percorre o fluxo e para ANTES da etapa com efeito fora do aparelho: ela e as seguintes
  ficam `skipped`, o objetivo fecha pelo sistema, nada é enviado, nenhuma ação de commit chega ao aparelho e o veredito
  da prova não deixa evidência; o pedido fecha `ensaio_so_leitura` (nunca `sem_evidencia`, nunca `feita`).
- A CONFERÊNCIA lê o `ContentProvider` do QA ao fim da execução de validação: 1 mensagem desta execução é o efeito
  uma vez (só o diário); 2 ou mais gravam o fato do 29.58 com `fonte: provedor`, e o veredito do 30.42 vira `invalida`.

Armações: o `Real` da prova (Harness na porta 5640, `FakeQaDevice`, provedor simulado) e o `mundo` do 30.37 (banco
migrado, `ServicoDeValidacao` com o parque falso). Nível de prova: `simulated`.
"""
from __future__ import annotations

import json

import pytest

from app.contracts.origem import PREFIXO_ENSAIO, PREFIXO_VALIDACAO, e_execucao_do_sistema, eh_ensaio_de_leitura
from app.modules.learning.domain.validacao import MOTIVO_HUMANO, Motivo
from app.modules.learning.domain.vocabulario import Posicao
from app.modules.learning.infrastructure.ligar_nativos import LeituraSql
from app.taskqueue.oraculo_qa import URI_DAS_MENSAGENS, conferencia_se_aplica, mensagens_da_execucao

from .test_learning_prova import Real, real  # noqa: F401  (a fixture `real` é a armação da prova)
from .test_learning_prova_validacao import B, PEDE, Mundo, _fluxo, _linha, mundo  # noqa: F401

LINHAS = ("Row: 0 _id=1, account=qa-user-10, contact=QA-001, body=Rodizio android-10 r-1-aaaaaa, status=Entregue ✓✓\n"
          "Row: 1 _id=2, account=qa-user-10, contact=QA-001, body=outra r-2-bbbbbb, status=Entregue ✓✓\n"
          "Row: 2 _id=3, account=qa-user-10, contact=QA-001, body=Rodizio android-10 r-1-aaaaaa, status=Enviada ✓\n")


# ------------------------------------------------------------------ o vocabulário e a leitura pura
def test_a_chave_do_ensaio_e_do_sistema_e_nao_e_origem_nova() -> None:
    assert eh_ensaio_de_leitura(f"{PREFIXO_ENSAIO}lv-1") and not eh_ensaio_de_leitura(f"{PREFIXO_VALIDACAO}lv-1")
    assert not eh_ensaio_de_leitura(None)
    assert e_execucao_do_sistema(None, f"{PREFIXO_ENSAIO}lv-1")         # sem aviso individual ao dono (28.19)
    assert Motivo.ENSAIO_SO_LEITURA in MOTIVO_HUMANO


def test_a_conferencia_conta_so_as_linhas_desta_execucao() -> None:
    assert mensagens_da_execucao(LINHAS, "r-1-aaaaaa") == 2
    assert mensagens_da_execucao(LINHAS, "r-2-bbbbbb") == 1
    assert mensagens_da_execucao(LINHAS, "r-3-cccccc") == 0
    assert mensagens_da_execucao("No result found.", "r-1-aaaaaa") == 0
    assert mensagens_da_execucao(LINHAS, "") == 0
    # sem `{run_id}` no comando, a mensagem não se liga à execução: não se aplica
    assert conferencia_se_aplica('envie "Teste {instance_id} {run_id}"')
    assert not conferencia_se_aplica('envie "Bom dia"') and not conferencia_se_aplica(None)


# ------------------------------------------------------------------ o ensaio no Harness
def _decisoes(real: Real, run_id: str) -> list[str]:
    return [str(r["message"]) for r in real.db.query(
        "SELECT message FROM events WHERE run_id=? AND kind='decision' ORDER BY id", (run_id,))]


async def test_o_ensaio_para_antes_do_efeito_e_nada_e_enviado(real: Real) -> None:
    fake = real.h.fakes["android-01"]
    enviadas = len(fake.messages)
    run = real.prova(f"{PREFIXO_ENSAIO}lv-ensaio")
    detalhe = await real.h.wait_run(run)
    assert detalhe.status == "cancelled"                                 # pelo sistema, sem esperar ninguém
    etapas = {str(r["key"]): str(r["status"]) for r in real.db.query(
        "SELECT key, status FROM steps WHERE run_id=? ORDER BY seq, id", (run,))}
    assert etapas["send_message"] == "skipped" and etapas["verify_sent"] == "skipped"
    assert all(s == "succeeded" for k, s in etapas.items() if k not in ("send_message", "verify_sent")), etapas
    assert len(fake.messages) == enviadas                                # nada saiu
    # nenhuma tentativa na etapa de efeito, e nenhuma ação de commit gravada na execução
    assert real.db.scalar("SELECT COUNT(*) FROM attempts a JOIN steps s ON s.id=a.step_id"
                          " WHERE s.run_id=? AND s.key='send_message'", (run,)) == 0
    acoes = [json.loads(str(r["args"] or "{}")) for r in real.db.query(
        "SELECT a.args FROM actions a JOIN attempts t ON t.id=a.attempt_id JOIN steps s ON s.id=t.step_id"
        " WHERE s.run_id=? ORDER BY a.id", (run,))]
    assert not any(isinstance(a, dict) and a.get("is_commit_action") for a in acoes)
    assert any("Ensaio só de leitura: parou antes da etapa" in d and "(send_message)" in d
               for d in _decisoes(real, run)), _decisoes(real, run)
    # o veredito da prova não deixa evidência: nem a favor (contaria para o 30.34) nem contra
    leitura = LeituraSql(real.db).execucao(run)
    assert leitura is not None and leitura.prova is not None and leitura.prova.posicao is None


async def test_a_prova_comum_segue_ate_o_efeito(real: Real) -> None:
    """O controle: a mesma prova, com a chave da validação, envia e comprova."""
    fake = real.h.fakes["android-01"]
    enviadas = len(fake.messages)
    run = real.prova(f"{PREFIXO_VALIDACAO}lv-comum")
    assert (await real.h.wait_run(run)).status == "completed"
    assert len(fake.messages) == enviadas + 1


# ------------------------------------------------------------------ a conferência no app de QA
async def test_a_conferencia_ve_uma_mensagem_e_nao_marca_repeticao(real: Real) -> None:
    run = real.prova(f"{PREFIXO_VALIDACAO}lv-um")
    assert (await real.h.wait_run(run)).status == "completed"
    assert any("Conferência no app de QA (etapa" in d and "o efeito saiu uma vez" in d for d in _decisoes(real, run))
    assert "efeito_repetido" not in str(real.db.scalar("SELECT result FROM steps WHERE run_id=? AND key='send_message'",
                                                       (run,)))
    leitura = LeituraSql(real.db).execucao(run)
    assert leitura is not None and leitura.prova is not None and leitura.prova.posicao is Posicao.FOR


async def test_duas_mensagens_no_app_viram_o_efeito_repetido_do_29_58(real: Real, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = real.h.fakes["android-01"]
    original = fake.consultar_provedor

    def em_dobro(uri: str) -> str:
        """O app viu a mensagem desta execução duas vezes (a tela comprovou uma)."""
        assert uri == URI_DAS_MENSAGENS
        saida = original(uri)
        return saida + "\n" + saida.splitlines()[-1] if saida.strip() else saida

    monkeypatch.setattr(fake, "consultar_provedor", em_dobro)
    run = real.prova(f"{PREFIXO_VALIDACAO}lv-dois")
    assert (await real.h.wait_run(run)).status == "completed"
    resultado = json.loads(str(real.db.scalar("SELECT result FROM steps WHERE run_id=? AND key='send_message'", (run,))))
    assert resultado["efeito_repetido"] == {"copias": 2, "fonte": "provedor"}
    assert any("2 mensagens desta execução no app: o efeito saiu 2 vezes" in d for d in _decisoes(real, run))
    leitura = LeituraSql(real.db).execucao(run)
    assert leitura is not None and leitura.prova is not None and leitura.prova.posicao is Posicao.INVALIDA


async def test_a_execucao_comum_nao_e_conferida(real: Real) -> None:
    """Só a execução de validação: a execução de pessoa não ganha leitura a mais no aparelho."""
    comum = real.h.run(["android-01"])
    assert (await real.h.wait_run(comum.id)).status == "completed"
    assert not any("Conferência no app de QA" in d for d in _decisoes(real, comum.id))


# ------------------------------------------------------------------ o fechamento do pedido
def test_o_pedido_do_ensaio_fecha_ensaio_so_leitura(mundo: Mundo) -> None:  # noqa: F811
    db, servico, _parque, _relogio, _ = mundo
    pid = servico.ao_parecer(_fluxo(db), "lr-1", PEDE, B)
    run_id = servico.uma_volta(lambda: 1)
    assert pid and run_id
    db.execute("UPDATE runs SET status='cancelled', idempotency_key=? WHERE id=?", (f"{PREFIXO_ENSAIO}{pid}", run_id))
    assert servico.minerar(run_id) == 1
    assert (_linha(db, pid)["estado"], _linha(db, pid)["motivo"]) == ("recusada", "ensaio_so_leitura")


def test_o_ensaio_com_linha_invalida_fecha_pela_invalida(mundo: Mundo) -> None:  # noqa: F811
    """A ordem do 30.42 vale também no ensaio: o efeito repetido (ou a abertura que falhou) vence."""
    db, servico, _parque, _relogio, _ = mundo
    pid = servico.ao_parecer(_fluxo(db), "lr-1", PEDE, B)
    run_id = servico.uma_volta(lambda: 1)
    assert pid and run_id
    db.execute("UPDATE runs SET status='cancelled', idempotency_key=? WHERE id=?", (f"{PREFIXO_ENSAIO}{pid}", run_id))
    db.execute("INSERT INTO learning_evidence(item_ref, stance, origin_ref, run_id, simulated, detail, observed_at)"
               " VALUES ('fluxo:f-qa','invalida',?,?,0,'[3251ec6f2171] invalida:ponto_de_partida — x',"
               "'2026-10-04T00:00:00Z')", (f"run:{run_id}", run_id))
    assert servico.minerar(run_id) == 1
    assert _linha(db, pid)["motivo"] == "ponto_de_partida"
