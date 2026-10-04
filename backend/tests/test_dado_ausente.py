"""Item 31.38: a leitura que não acha o dado para cedo, ganha UM plano revisado e, na segunda vez, falha de vez.

Achado real (ba5ebc, `read_parties`): uma tentativa de leitura gastou 21 decisões (tocar, rolar, esperar, olhar) atrás
de um valor que não estava na tela e só parou na verba do pedido (US$ 0,31 de 0,30). Agora:
- o ator diz `step_blocked(kind="dado_ausente")`, ou o teto de decisões da leitura (`ai.max_decisoes_leitura`) estoura;
- a etapa não se repete; o objetivo ganha UM plano revisado, e o ator dele sabe onde já se procurou;
- a segunda vez no mesmo objetivo fecha como falha final, com a frase do que se procurou e onde;
- etapa de efeito não usa o teto, e `dado_ausente` nela é recusado.

Nível de prova: `simulated` (harness na porta 5640, catálogo de teste no QA Messenger falso; nenhuma IA paga).
"""
from __future__ import annotations

from typing import Any, Iterator

import pytest

from app.models import PlanStep, Postcondition
from app.planning.capabilities import Capability, CapabilityCatalog
from app.planning.catalog import register, unregister
from app.planning.provider import Decision, Usage
from app.taskqueue.executor import PREFIXO_DADO_AUSENTE

from .conftest import Harness
from .fake_device import PKG as QA

COMANDO = "Abra o QA Messenger e leia o protocolo."


@pytest.fixture
def catalogo_qa() -> Iterator[None]:
    register(QA, CapabilityCatalog(QA, [
        Capability(key="QA_LER_PROTOCOLO", title="Ler o protocolo", goal="Ler o número do protocolo na caixa.",
                   post_kind="model_judged", post_value="protocolo lido", post_description="O protocolo foi lido.",
                   saidas=("protocolo",), max_attempts=3),
    ]))
    try:
        yield
    finally:
        unregister(QA)


def _plano(inner: Any, passo: PlanStep) -> None:
    plan0 = inner.plan

    async def plan(req: Any) -> Any:
        p, u = await plan0(req)
        base = [PlanStep(key="open_app", title="Abrir o QA Messenger", goal="Trazer o QA Messenger para o primeiro plano.",
                         postcondition=Postcondition(kind="app_foreground", value=QA, description="Em primeiro plano."),
                         timeout_s=90)]
        return p.model_copy(update={"steps": [*base, passo]}), u

    inner.plan = plan


def _ator(inner: Any, *, bloqueia: bool, historicos: list[list[str]], kind: str = "dado_ausente") -> None:
    """O ator da etapa `ler`: relata o bloqueio (`bloqueia`) ou fica só olhando a tela (para o teto estourar)."""
    decide0 = inner.decide

    async def decide(req: Any) -> Any:
        if req.ctx.step_key != "ler":
            return await decide0(req)
        historicos.append(list(req.history))
        if bloqueia:
            return Decision(tool="step_blocked", args={"rationale": "não está aqui", "kind": kind,
                                                       "reason": "procurei na caixa inteira", "needs_user": False}), Usage()
        return Decision(tool="observe_screen", args={"rationale": "procurar de novo", "need_image": False}), Usage()

    inner.decide = decide


def _etapa(**over: Any) -> PlanStep:
    campos: dict[str, Any] = dict(key="ler", title="Ler o protocolo", goal="g", depends_on=["open_app"],
                                  capability="QA_LER_PROTOCOLO", max_attempts=3,
                                  postcondition=Postcondition(kind="model_judged", value="v", description="d"))
    campos.update(over)
    return PlanStep(**campos)


async def _fim(h: Harness, run_id: str) -> Any:
    return await h.wait_run(run_id, timeout=60, statuses=("completed", "completed_with_issues", "failed",
                                                          "waiting_user", "needs_input"))


def _falhou(h: Harness, run_id: str) -> None:
    """Falha nunca conta como sucesso: o objetivo fecha `failed` e a execução não fecha `completed`."""
    assert h.state.db.scalar("SELECT status FROM objectives WHERE run_id=?", (run_id,)) == "failed"  # type: ignore[union-attr]
    assert h.state.repo.run_row(run_id)["status"] != "completed"                   # type: ignore[union-attr]


def _etapas(h: Harness, run_id: str) -> list[Any]:
    return h.state.db.query("SELECT key, status, attempts, plan_version, status_detail FROM steps WHERE run_id=? "  # type: ignore[union-attr]
                            "AND key='ler' ORDER BY plan_version", (run_id,))


async def test_dado_ausente_da_um_replano_com_a_evidencia_e_a_segunda_vez_e_final(harness: Harness,
                                                                                   catalogo_qa: None) -> None:
    inner, historicos = harness.ai.inner, []
    _plano(inner, _etapa())
    _ator(inner, bloqueia=True, historicos=historicos)
    run = harness.run(["android-01"], command=COMANDO)
    await _fim(harness, run.id)
    _falhou(harness, run.id)
    etapas = _etapas(harness, run.id)
    assert [(e["status"], e["attempts"], e["plan_version"]) for e in etapas] == [("failed", 1, 1), ("failed", 1, 2)]
    for e in etapas:                                       # a frase para a pessoa: o que e onde, sem o texto do modelo
        assert e["status_detail"].startswith(PREFIXO_DADO_AUSENTE) and "'protocolo'" in e["status_detail"]
        assert "procurei na caixa inteira" not in e["status_detail"]
    revisoes = harness.state.db.query("SELECT reason FROM plan_versions WHERE objective_id=(SELECT id FROM objectives "  # type: ignore[union-attr]
                                      "WHERE run_id=?) AND reason LIKE 'Recuperação automática%'", (run.id,))
    assert len(revisoes) == 1 and "(dado ausente)" in revisoes[0]["reason"]
    assert len(historicos) == 2                            # uma decisão em cada plano: sem nova tentativa da mesma etapa
    assert any(l.startswith("(plano revisado)") for l in historicos[1])
    assert not any(l.startswith("(plano revisado)") for l in historicos[0])


async def test_teto_de_decisoes_da_leitura_tem_o_mesmo_desfecho(harness: Harness, catalogo_qa: None) -> None:
    harness.cfg.file.ai.max_decisoes_leitura = 3
    inner, historicos = harness.ai.inner, []
    _plano(inner, _etapa())
    _ator(inner, bloqueia=False, historicos=historicos)
    run = harness.run(["android-01"], command=COMANDO)
    await _fim(harness, run.id)
    etapas = _etapas(harness, run.id)
    assert [(e["status"], e["plan_version"]) for e in etapas] == [("failed", 1), ("failed", 2)]
    assert all("teto de 3 decisões" in e["status_detail"] for e in etapas)
    assert len(historicos) == 6                            # 3 por plano, nunca mais que o teto
    _falhou(harness, run.id)


async def test_etapa_que_nao_le_nao_usa_o_teto_e_recusa_dado_ausente(harness: Harness, catalogo_qa: None) -> None:
    """Sem saídas (a etapa de efeito também não lê): o teto não vale (passa de 3 decisões) e `dado_ausente` é recusado,
    não vira o desfecho. A regra é `leitura = saídas declaradas, sem efeito e sem commit_guard` no executor."""
    harness.cfg.file.ai.max_decisoes_leitura = 3
    inner, historicos = harness.ai.inner, []
    _plano(inner, _etapa(capability=None, max_attempts=1))
    _ator(inner, bloqueia=True, historicos=historicos)
    run = harness.run(["android-01"], command=COMANDO)
    await _fim(harness, run.id)
    etapas = _etapas(harness, run.id)
    # recusado 4 vezes seguidas numa tentativa (passa do teto de 3): a falha de sempre, com a recuperação de sempre
    assert len(historicos) >= 4 and all(len(h) >= 1 for h in historicos[1:4])
    assert any("REJEITADO" in l for l in historicos[-1])
    assert not any((e["status_detail"] or "").startswith(PREFIXO_DADO_AUSENTE) for e in etapas)
    assert harness.state.db.scalar("SELECT COUNT(*) FROM plan_versions WHERE reason LIKE ?",   # type: ignore[union-attr]
                                   ("%(dado ausente)%",)) == 0
