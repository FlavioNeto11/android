"""31.61 (A): a etapa de LEITURA julgada, com prova local de tela declarada, sai do laço sem `step_done` quando todas
as saídas foram lidas e a prova vale numa árvore relida DEPOIS da última leitura.

O roteiro é o das 8 decisões da r-20261004232524-2e0775 (caixa do Outlook, `OPEN_MAIL_INBOX`, saídas remetente e
assunto): observar, `step_done` recusado, observar, `step_done` recusado, ler, observar, ler, `step_done`. Aqui no app
de teste (QA Messenger), com o tap trocado por observar (o tap abriria uma conversa); a capacidade é a do Outlook com a
prova de tela trocada por `id=conversation_list`, e a prova local passa pela árvore (`find_selector`), não pela IA.

Nível de prova: `simulated` (provedor por regras, aparelho falso, banco de teste). Nada real; a prova real fica para
uma janela depois do deploy 34.
"""
from __future__ import annotations

import dataclasses
import time
from typing import Any

import pytest

from app.models import PlanStep, Postcondition
from app.planning.capabilities import capability_of
from app.planning.provider import Decision, Usage
from app.taskqueue import executor as modulo_executor

from .conftest import Harness

PROVA = "selector:id=conversation_list"
ROTEIRO = ("observe_screen", "step_done", "observe_screen", "step_done", "read:remetente", "observe_screen",
           "read:assunto", "step_done")


@pytest.fixture
def caixa(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `OPEN_MAIL_INBOX` do Outlook como capacidade da etapa de leitura, com a prova de tela do app de teste."""
    original = modulo_executor.capability_of
    base = capability_of("com.microsoft.office.outlook", "OPEN_MAIL_INBOX")
    assert base is not None and base.post_kind == "model_judged" and not base.side_effect

    def com(app: str | None, key: str | None) -> Any:
        return dataclasses.replace(base, local_proof=PROVA) if key == "OPEN_MAIL_INBOX" else original(app, key)

    async def prova(self: Any, step: Any, capability: Any, obs: Any, *, sem_nivel: bool = False,
                    conta: str | None = None) -> bool:
        return capability is not None and bool(obs.tree.find_selector("id=conversation_list"))

    monkeypatch.setattr(modulo_executor, "capability_of", com)
    monkeypatch.setattr(modulo_executor.StepExecutor, "_prova_local", prova)


def _plano(inner: Any) -> Any:
    plan0 = inner.plan

    async def plan(req: Any) -> Any:
        p, u = await plan0(req)
        ler = PlanStep(key="ler_caixa", title="Ler a caixa", goal="Ler remetente e assunto da primeira conversa.",
                       capability="OPEN_MAIL_INBOX", depends_on=["confirm_account"], saidas=["remetente", "assunto"],
                       postcondition=Postcondition(kind="model_judged", value="caixa", description="A caixa está aberta."))
        passos = []
        for s in p.steps:
            passos.append(s)
            if s.key == "confirm_account":
                passos.append(ler)
        return p.model_copy(update={"steps": passos}), u

    return plan


def _roteiro(harness: Harness, visto: dict[str, Any], *, tela_muda_na_ultima_leitura: bool = False,
             valores_somem_na_ultima_leitura: Any = None) -> Any:
    decide0 = harness.ai.inner.decide

    async def decide(req: Any) -> Any:
        if req.ctx.step_key != "ler_caixa":
            return await decide0(req)
        i = visto.setdefault("decisoes", 0)
        visto["decisoes"] = i + 1
        visto["historico"] = list(req.history)
        passo = ROTEIRO[min(i, len(ROTEIRO) - 1)]
        fake = harness.fakes["android-01"]
        if passo.startswith("read:"):
            nome = passo.split(":", 1)[1]
            el = next(e for e in req.screen.tree.elements if e.text == ("QA-001" if nome == "remetente" else "QA-002"))
            if nome == "assunto" and tela_muda_na_ultima_leitura:
                fake.screen, fake.contact = "chat", "QA-003"     # a árvore relida depois desta leitura não tem a lista
            if nome == "assunto" and valores_somem_na_ultima_leitura is not None:
                valores_somem_na_ultima_leitura(True)            # a mesma lista, com outros contatos
            return Decision(tool="read_value", args={"rationale": "r", "name": nome, "element_id": el.id,
                                                     "value": None, "value_kind": "text"}), Usage()
        if passo == "step_done":
            if tela_muda_na_ultima_leitura and fake.screen == "chat":
                fake.screen, fake.contact = "home", None
            if valores_somem_na_ultima_leitura is not None:
                valores_somem_na_ultima_leitura(False)
            return Decision(tool="step_done", args={"rationale": "pronto", "evidence": "caixa",
                                                    "delivery_level": None}), Usage()
        return Decision(tool="observe_screen", args={"rationale": "olhar"}), Usage()

    return decide


async def _rodar(harness: Harness, **kw: Any) -> tuple[dict[str, Any], float, Any]:
    visto: dict[str, Any] = {}
    harness.cfg.file.ai.relacao_do_valor = False         # a relação do valor (31.41) tem teste próprio; aqui, o fecho
    harness.ai.inner.plan, harness.ai.inner.decide = _plano(harness.ai.inner), _roteiro(harness, visto, **kw)
    t0 = time.monotonic()
    run = harness.run(["android-01"])
    final = await harness.wait_run(run.id, timeout=90)
    return visto, time.monotonic() - t0, final


@pytest.mark.parametrize("ligado", [False, True], ids=["antes", "depois"])
async def test_roteiro_da_2e0775_antes_e_depois(harness: Harness, caixa: None, monkeypatch: pytest.MonkeyPatch,
                                                ligado: bool) -> None:
    monkeypatch.setattr(modulo_executor, "LEITURA_FECHA_SEM_STEP_DONE", ligado)
    visto, segundos, final = await _rodar(harness)
    assert final.status == "completed"
    db = harness.state.db                                                     # type: ignore[union-attr]
    assert {r["name"]: r["value"] for r in db.query("SELECT name, value FROM step_outputs")} == {
        "remetente": "QA-001", "assunto": "QA-002"}
    print(f"31.61 {'depois' if ligado else 'antes'}: {visto['decisoes']} decide, {segundos:.2f} s")
    aceitos = db.scalar("SELECT COUNT(*) FROM actions WHERE tool='step_done' AND status='done' AND attempt_id LIKE ?",
                        ("%:ler_caixa:%",))
    if ligado:
        assert visto["decisoes"] == 7 and aceitos == 0            # o último `decide` (só "pronto") não existe mais
        # a instrução direta leva só o NOME da saída, nunca o valor nem o texto da tela
        diretas = [h for h in visto["historico"] if h.startswith("(executor) a tela já está pronta")]
        assert diretas and all("QA-0" not in h for h in diretas)
        assert any("sem step_done" in (r["text"] or "") for r in db.query("SELECT message AS text FROM events"))
    else:
        assert visto["decisoes"] == 8 and aceitos == 1


async def test_prova_local_falsa_na_arvore_relida_nao_fecha(harness: Harness, caixa: None) -> None:
    """A tela da última leitura tinha a lista; a relida depois dela, não. A etapa NÃO sai sem o ator: o `step_done`
    volta a ser pedido (8 decisões), e nenhuma decisão de fecho sem `step_done` é gravada."""
    visto, _s, final = await _rodar(harness, tela_muda_na_ultima_leitura=True)
    assert final.status == "completed"
    db = harness.state.db                                                     # type: ignore[union-attr]
    assert visto["decisoes"] == 8
    assert not any("sem step_done" in (r["text"] or "") for r in db.query("SELECT message AS text FROM events"))


async def test_mesma_tela_com_outros_valores_na_arvore_relida_nao_fecha(harness: Harness, caixa: None) -> None:
    """L1 da revisão do #348: a tela relida ainda casa a prova (a mesma lista), mas os valores lidos não estão mais nela
    (outra tela do mesmo tipo). A etapa NÃO sai sem o ator: o `step_done` volta a ser pedido."""
    from . import fake_device
    originais = list(fake_device.CONTACTS)

    def trocar(outros: bool) -> None:
        fake_device.CONTACTS[:] = ["Equipe Outra", "Suporte Outro", "QA-009"] if outros else originais

    try:
        visto, _s, final = await _rodar(harness, valores_somem_na_ultima_leitura=trocar)
    finally:
        fake_device.CONTACTS[:] = originais
    assert final.status == "completed"
    db = harness.state.db                                                     # type: ignore[union-attr]
    assert visto["decisoes"] == 8
    assert not any("sem step_done" in (r["text"] or "") for r in db.query("SELECT message AS text FROM events"))
