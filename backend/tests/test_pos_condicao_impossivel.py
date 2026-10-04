"""31.44: a pós-condição com valor vazio falha fechado ANTES de agir (sem tentativa gasta nem IA).

Achados reais (04/10, banco central, só leitura):
- r-20261004111836-fec1a1 (validação do fluxo "enviar a mensagem", android-04): o molde da etapa `check_account` era
  `id=com.pocqa.messenger:id/account_label|text={account_label}`, a etapa não declara app e o aparelho não tinha
  `account_label`; o valor virou "" e a pós-condição ficou `…|text=`. `_partes_do_seletor` degrada `text=` para a busca
  do texto literal "text=": 0 elemento(s) nas 3 tentativas, 3 chamadas `decide` (32 mil tokens) e `failed`.
- r-20261004082521-2f21e2 (planejador real): `id=…message_input|text=Suporte QA`. O valor NÃO é vazio, e antes da ação
  o campo de escrita nem está na tela, então a checagem de entrada não decide; quem fecha é o 31.32 (tela final, 1
  tentativa, sem plano revisado). Aqui se prova só que as duas guardas não se pisam.

Nível de prova: `simulated` (árvores e aparelho falsos, harness com o provedor simulado).
"""
from __future__ import annotations

from typing import Any

from app.automation.hierarchy import UiTree
from app.models import Postcondition
from app.modules.learning.domain.falhas import FailureKind
from app.planning.simulated_provider import _post
from app.taskqueue.executor import parte_vazia_da_pos_condicao

from .conftest import Harness
from .test_seletor_impossivel import _conversa

CONTA = "id=com.pocqa.messenger:id/account_label|text="


def test_a_parte_sem_valor_so_vale_para_chave_conhecida_com_valor_vazio() -> None:
    assert UiTree.parte_sem_valor("id=com.pocqa.messenger:id/account_label|text=") == "text="      # o caso da fec1a1
    assert UiTree.parte_sem_valor("desc==") == "desc=="
    assert UiTree.parte_sem_valor("id=chat_title|") == "<vazia>"
    assert UiTree.parte_sem_valor("") == "<vazia>"
    assert UiTree.parte_sem_valor("text=   ") == "text="
    # com valor: nada a dizer (inclusive o seletor do caso 2, que o 31.32 trata na tela final)
    assert UiTree.parte_sem_valor("id=com.pocqa.messenger:id/message_input|text=Suporte QA") is None
    assert UiTree.parte_sem_valor("id=chat_title|text=QA-001") is None
    assert UiTree.parte_sem_valor("Suporte QA") is None                    # texto puro
    assert UiTree.parte_sem_valor("foo=") is None                          # chave desconhecida é texto literal


def test_so_element_present_e_text_visible_entram_na_guarda() -> None:
    def post(kind: str, valor: str) -> Postcondition:
        return Postcondition(kind=kind, value=valor, description="d")  # type: ignore[arg-type]

    assert parte_vazia_da_pos_condicao(post("element_present", CONTA)) == "text="
    assert parte_vazia_da_pos_condicao(post("text_visible", "  ")) == "texto vazio"
    assert parte_vazia_da_pos_condicao(post("text_visible", "Conta: qa-user-01")) is None
    assert parte_vazia_da_pos_condicao(post("element_present", CONTA + "qa-user-01")) is None   # variável preenchida
    assert parte_vazia_da_pos_condicao(post("model_judged", "")) is None
    assert parte_vazia_da_pos_condicao(post("app_foreground", "com.pocqa.messenger")) is None


def test_o_seletor_do_caso_2_segue_para_o_31_32_na_tela_final() -> None:
    tela = _conversa()
    seletor = "id=com.pocqa.messenger:id/message_input|text=Suporte QA"
    assert UiTree.parte_sem_valor(seletor) is None
    assert tela.partes_em_elementos_diferentes(seletor)


def _plano_com(h: Harness, valor: str, *, parametros: dict[str, str] | None = None) -> None:
    """O plano simulado com a etapa `confirm_account` trocada pelo molde `valor` (um element_present)."""
    inner = h.ai.inner
    plan0 = inner.plan

    async def plan(req: Any) -> Any:
        p, u = await plan0(req)
        passos = [s.model_copy(update={"postcondition": _post("element_present", valor, "A tela mostra a conta.")})
                  if s.key == "confirm_account" else s for s in p.steps]
        return p.model_copy(update={"steps": passos, "parameters": {**p.parameters, **(parametros or {})}}), u

    inner.plan = plan


def _gasto(h: Harness, run_id: str, iid: str) -> tuple[int, int, int]:
    """(chamadas de IA de decisão/verificação, tentativas gastas na etapa, ações no aparelho)."""
    db = h.state.db                                                            # type: ignore[union-attr]
    ia = db.scalar("SELECT COUNT(*) FROM ai_calls WHERE run_id=? AND role IN ('decide','verify')", (run_id,))
    etapa = db.one("SELECT attempts FROM steps WHERE run_id=? AND instance_id=? AND key='confirm_account'",
                   (run_id, iid))
    acoes = db.scalar("SELECT COUNT(*) FROM actions a JOIN attempts t ON t.id=a.attempt_id JOIN steps s ON s.id=t.step_id"
                      " WHERE s.run_id=? AND s.key='confirm_account'", (run_id,))
    return int(ia or 0), int(etapa["attempts"]), int(acoes or 0)


async def test_caso_1_aparelho_sem_conta_para_sem_tentativa_e_sem_ia(harness: Harness) -> None:
    """A fec1a1: `text={account_label}` num aparelho sem rótulo. Antes: 3 tentativas, 3 `decide`, `failed`. Agora o
    item para com o motivo (o do 24.4, de conta), a tentativa é devolvida e nenhuma IA nem gesto é gasto."""
    iid = "android-01"
    harness.state.db.execute("UPDATE instances SET account_label='' WHERE id=?", (iid,))   # type: ignore[union-attr]
    _plano_com(harness, "id=com.pocqa.messenger:id/account_label|text={account_label}")
    run = harness.run([iid])

    def parado() -> bool:
        o = harness.state.db.one("SELECT status FROM objectives WHERE run_id=?", (run.id,))   # type: ignore[union-attr]
        return o is not None and o["status"] in ("waiting_user", "failed", "succeeded")

    await harness.wait(parado, 60, "objetivo parado pela pós-condição vazia")
    db = harness.state.db                                                      # type: ignore[union-attr]
    obj = db.one("SELECT * FROM objectives WHERE run_id=?", (run.id,))
    assert obj["status"] == "waiting_user" and "sem valor" in obj["blocked_reason"]
    assert obj["plan_version"] == 1                                            # nada de plano revisado
    etapa = db.one("SELECT attempts, failure_kind, postcondition FROM steps WHERE objective_id=? AND key='confirm_account'",
                   (obj["id"],))
    assert '|text=","description"' in etapa["postcondition"]                  # o molde virou "" (como na fec1a1)
    assert etapa["failure_kind"] == FailureKind.CONTA_ERRADA.value
    assert _gasto(harness, run.id, iid) == (0, 0, 0)
    assert not harness.fakes[iid].messages


async def test_variavel_do_plano_vazia_com_conta_conhecida_e_defeito_do_plano(harness: Harness) -> None:
    """Parte vazia que NÃO é falta de conta (o aparelho tem rótulo): defeito do plano na 1ª tentativa, sem plano
    revisado e sem IA; nenhuma ação no aparelho."""
    iid = "android-01"
    _plano_com(harness, "id=com.pocqa.messenger:id/account_label|text={tag}", parametros={"tag": ""})
    run = harness.run([iid])
    await harness.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed", "waiting_user"))
    db = harness.state.db                                                      # type: ignore[union-attr]
    obj = db.one("SELECT * FROM objectives WHERE run_id=?", (run.id,))
    assert obj["status"] == "failed" and "Defeito do plano" in obj["status_detail"]
    assert obj["plan_version"] == 1
    etapa = db.one("SELECT failure_kind FROM steps WHERE objective_id=? AND key='confirm_account'", (obj["id"],))
    assert etapa["failure_kind"] == FailureKind.DEFEITO_DO_PLANO.value
    ia, tentativas, acoes = _gasto(harness, run.id, iid)
    assert (ia, tentativas, acoes) == (0, 1, 0)
    assert not harness.fakes[iid].messages


async def test_variavel_preenchida_segue_o_caminho_normal(harness: Harness) -> None:
    """Controle: com o rótulo no aparelho, o mesmo molde da fec1a1 comprova na tela e a execução fecha."""
    _plano_com(harness, "id=com.pocqa.messenger:id/account_label|text={account_label}")
    run = harness.run(["android-01"])
    detalhe = await harness.wait_run(run.id, timeout=60)
    assert detalhe.status == "completed"
