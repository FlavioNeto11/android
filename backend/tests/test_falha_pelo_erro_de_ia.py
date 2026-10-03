"""RA-22 (reavaliação de 03/10): o tipo da falha sai do erro de IA que ENCERROU a tentativa, não do texto.

O que cada bloco prova:

- **Um caminho só.** `classificar_falha(texto, status, error_kind)` decide pelo tipo quando ele decide; o texto do
  executor pode mudar sem mudar a classificação (a mensagem deixa de ser contrato). Sem tipo, as REGRAS de texto (o
  legado). `step_deadline` não decide: o ANR anotado no texto continua ganhando do prazo.
- **A mudança pretendida.** O teto do pedido ("Orçamento do pedido atingido…", `kind='budget'`) caía em `outro`;
  com o tipo, `ia_orcamento`.
- **Na execução.** O executor põe o kind no desfecho, o scheduler o passa adiante e a tentativa grava
  `attempts.error_kind` com o `failure_kind` já pelo tipo; a etapa (`steps.failure_kind`) concorda.
- **Na releitura.** Com `attempts.error_kind`, uma chamada anterior que o roteador contornou (`ai_calls`) não desmente
  o gravado; sem ele (legado), a releitura por `ai_calls` segue como antes.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.db import Database
from app.modules.learning.domain.falhas import FailureKind as F
from app.modules.learning.domain.falhas import classificar_falha, pelo_erro_que_encerrou
from app.planning.provider import AIError
from app.planning.simulated_provider import SimulatedProvider

from .conftest import Harness
from .fake_skills import banco as banco_migrado
from .test_learning_backlog import AGORA, Mundo, montar
from .test_learning_diagnostico import a_linha, falha_de_ia, tres_falhas

TETO_DO_PEDIDO = "Orçamento do pedido atingido nesta ocorrência: US$ 1.00 de US$ 1.00. Ajuste o orçamento do pedido."


# ------------------------------------------------------------------ domínio
@pytest.mark.parametrize(("kind", "tipo"), [
    ("budget", F.IA_ORCAMENTO), ("billing", F.IA_SALDO), ("balance", F.IA_SALDO), ("refusal", F.IA_RECUSA),
    ("not_configured", F.IA_INDISPONIVEL), ("error", F.IA_INDISPONIVEL), ("invalid_output", F.IA_INDISPONIVEL),
])
def test_o_tipo_decide_e_o_texto_reescrito_nao_muda_a_classificacao(kind: str, tipo: F) -> None:
    for texto in ("texto de hoje do executor", "o mesmo motivo escrito de outro jeito", "", None):
        assert classificar_falha(texto, "failed", kind) is tipo
        assert classificar_falha(texto, "uncertain", kind) is tipo
    assert pelo_erro_que_encerrou(kind) is tipo


def test_o_teto_do_pedido_sai_de_outro_pelo_tipo() -> None:
    assert classificar_falha(TETO_DO_PEDIDO, "failed") is F.OUTRO                 # o legado, só pelo texto
    assert classificar_falha(TETO_DO_PEDIDO, "failed", "budget") is F.IA_ORCAMENTO


def test_o_prazo_nao_decide_pelo_tipo_e_o_anr_segue_ganhando() -> None:
    anr = ("Prazo da etapa (180s) esgotado durante a decisão da IA: tempo; o Instagram parou de responder (ANR) "
           "no android-01.")
    assert classificar_falha(anr, "failed", "step_deadline") is F.APP_ANR
    assert classificar_falha("Prazo da etapa (180s) esgotado durante a verificação: x", "failed",
                             "step_deadline") is F.PRAZO_DA_ETAPA
    assert pelo_erro_que_encerrou("step_deadline") is None


def test_o_status_continua_antes_do_tipo_e_kind_desconhecido_fica_com_o_texto() -> None:
    assert classificar_falha("x", "interrupted", "refusal") is F.INTERROMPIDA
    assert classificar_falha("x", "succeeded", "budget") is None
    assert classificar_falha("Teto de gasto de IA do dia atingido.", "failed", "kind_que_ainda_nao_existe") \
        is F.IA_ORCAMENTO
    assert classificar_falha("Elemento não encontrado.", "failed", None) is F.ALVO_AUSENTE


# ------------------------------------------------------------------ na execução
class TetoNaDecisao:
    """`decide()` bate no teto do pedido (`AIError(kind='budget')`, com o texto que a regra nunca pegou)."""

    def __init__(self, inner: SimulatedProvider):
        self.inner = inner
        self.calls = 0
        self.name, self.model, self.simulated = inner.name, inner.model, inner.simulated

    def status(self) -> Any:
        return self.inner.status()

    async def decide(self, req: object) -> object:
        self.calls += 1
        raise AIError(TETO_DO_PEDIDO, kind="budget")

    async def verify(self, req: object) -> object:
        return await self.inner.verify(req)

    async def plan(self, req: object) -> object:
        return await self.inner.plan(req)


async def test_a_execucao_grava_o_kind_e_classifica_tentativa_e_etapa_pelo_tipo(tmp_path: Path) -> None:
    h = Harness(tmp_path, 3)
    provider = TetoNaDecisao(SimulatedProvider())
    h.ai = provider  # type: ignore[assignment]
    await h.boot()
    assert h.state is not None
    try:
        run = h.run(["android-01"])
        await h.wait_run(run.id)
        assert provider.calls >= 1
        db = h.state.repo.db
        tentativas = db.query("SELECT a.status, a.error, a.error_kind, a.failure_kind FROM attempts a"
                              " JOIN steps s ON s.id = a.step_id WHERE s.run_id=? AND a.error_kind IS NOT NULL",
                              (run.id,))
        assert tentativas, "nenhuma tentativa gravou o erro de IA que a encerrou"
        assert {(t["status"], t["error_kind"], t["failure_kind"]) for t in tentativas} \
            == {("failed", "budget", "ia_orcamento")}
        assert all("Orçamento do pedido atingido" in (t["error"] or "") for t in tentativas)  # o texto segue gravado
        etapas = db.query("SELECT failure_kind FROM steps WHERE run_id=? AND status='failed'", (run.id,))
        assert etapas and {e["failure_kind"] for e in etapas} == {"ia_orcamento"}
        # A tentativa que não terminou por erro de IA fica sem o kind (nada é inventado).
        assert not db.query("SELECT 1 FROM attempts a JOIN steps s ON s.id = a.step_id WHERE s.run_id=?"
                            " AND a.status='succeeded' AND a.error_kind IS NOT NULL", (run.id,))
    finally:
        await h.state.stop()  # type: ignore[union-attr]


# ------------------------------------------------------------------ na releitura
@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "ra22.sqlite3")
    yield d
    d.close()


@pytest.fixture
def mundo(db: Database) -> Mundo:
    return montar(db)


def test_com_o_kind_gravado_uma_chamada_contornada_nao_desmente_a_tentativa(mundo: Mundo, db: Database) -> None:
    for i, run in enumerate(tres_falhas(db, erro="IA indisponível: resposta vazia", tipo="ia_indisponivel",
                                        tela="feed")):
        falha_de_ia(db, run, AGORA - timedelta(days=1 + i * 0.3), "refusal")   # uma chamada antes, contornada
    db.execute("UPDATE attempts SET error_kind='error'")
    assert [x.grupo.chave.tipo for x in mundo.falhas.relatorio().itens] == ["ia_indisponivel"]


def test_sem_o_kind_gravado_o_legado_segue_relido_por_ai_calls(mundo: Mundo, db: Database) -> None:
    for i, run in enumerate(tres_falhas(db, erro=TETO_DO_PEDIDO, tipo="outro", tela="feed")):
        falha_de_ia(db, run, AGORA - timedelta(days=1 + i * 0.3), "budget")
    linha = a_linha(mundo, "ia_orcamento")
    assert linha.grupo.retroativas == 3
