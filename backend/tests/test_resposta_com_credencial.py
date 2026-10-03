"""29.52: a resposta com credencial é recusada pelo CONTEXTO no caminho comum (painel e canais).
Nível de prova: `simulated` (planejador simulado com a pergunta trocada; aparelhos falsos na porta 5640).

O que se prova:
- o vocabulário da pergunta sensível e da resposta recusada é um só, o da `TriagemDeCredencial`;
- a execução que pergunta senha ou código registra `pergunta_sensivel` com o id e o tipo, sem o texto da pergunta;
- pergunta sensível + qualquer resposta → 409 `credencial_na_resposta` na sucessora, sem execução nova e sem cancelar a
  antiga; pergunta comum + "884512" → 409 só pelo formato; pergunta comum + texto normal → segue;
- o refinamento recusa antes de chamar a IA, pela pergunta (que vem no corpo) e pelo formato da resposta;
- as duas leituras públicas (a desta execução; alguma aberta agora, por aparelho) dizem o tipo, ou None;
- com pergunta sensível aberta para o aparelho, o pedido NOVO de uma palavra só é recusado; frase inteira segue.

Os canais (Telegram, Trello) chamam as mesmas leituras; a cobertura do lado deles entra na integração da suíte 14.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.db import loads
from app.main import create_app
from app.models import MissingInfo, RunCreate
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.security.redaction import parece_codigo
from app.taskqueue.perguntas import acrescimo, pergunta_sensivel_aberta, pergunta_sensivel_da_execucao
from app.taskqueue.service import RunError

from .conftest import COMMAND, Harness

pytestmark = pytest.mark.asyncio

INCOMPLETO = "Abra o QA Messenger e envie uma mensagem"
PERGUNTA_DE_SENHA = "Qual é a senha da conta do QA Messenger?"
RESPOSTA_SOLTA = "Xk9mPq2w"          # valor de teste: palavra sem rótulo, que a redação por formato não pega


def _triagem() -> TriagemDeCredencial:
    return TriagemDeCredencial()


async def test_vocabulario_da_pergunta_e_da_resposta() -> None:
    t = _triagem()
    assert t.pergunta_sensivel(PERGUNTA_DE_SENHA) == "senha"
    assert t.pergunta_sensivel("Qual o código que chegou por SMS?") == "codigo"
    assert t.pergunta_sensivel("", "verification_code") == "codigo"
    assert t.pergunta_sensivel("Confirme a autenticação em dois fatores") == "2fa"
    assert t.pergunta_sensivel("Qual é o token da API?") == "token"
    assert t.pergunta_sensivel("Para qual contato a mensagem deve ser enviada?", "recipient") is None
    assert t.pergunta_sensivel("Qual é o texto da mensagem (entre aspas)?", "message") is None
    assert t.resposta_recusada("884512") and t.resposta_recusada("884 512") and t.resposta_recusada("Abc!2345xyz")
    assert t.resposta_recusada(RESPOSTA_SOLTA)
    assert not t.resposta_recusada("Para o contato Ana, com o texto Bom dia")
    assert parece_codigo("2024") and not parece_codigo("12345678901") and not parece_codigo("abc1234")
    assert acrescimo("Abra o app", "Abra o app\n884512") == "884512"
    assert acrescimo("Abra o app e envie", "Objetivo: abra o app e envie para Ana") == "Objetivo: para Ana"


def _pergunta_de_senha(h: Harness) -> None:
    """O planejador simulado passa a pedir a senha, como um planejador real com defeito (ADR-040)."""
    plano0 = h.ai.inner.plan

    async def plan(req: Any) -> Any:
        plano, uso = await plano0(req)
        plano.missing = [MissingInfo(field="password", question=PERGUNTA_DE_SENHA)]
        plano.steps = []
        return plano, uso

    h.ai.inner.plan = plan


async def _em_needs_input(h: Harness, *, sensivel: bool) -> str:
    if sensivel:
        _pergunta_de_senha(h)
    run = h.run(["android-01"], command=INCOMPLETO, mode="plan")
    await h.wait_run(run.id, ("needs_input",))
    return run.id


def _cliente(h: Harness) -> httpx.AsyncClient:
    assert h.state is not None
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_pergunta_sensivel_vira_evento_com_id_e_tipo_sem_o_texto(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id = await _em_needs_input(harness, sensivel=True)
    eventos = st.repo.db.query("SELECT message, data FROM events WHERE run_id=? AND kind='pergunta_sensivel'",
                               (run_id,))
    assert len(eventos) == 1
    assert loads(str(eventos[0]["data"]), {}) == {"tipo": "senha"}
    assert PERGUNTA_DE_SENHA not in str(eventos[0]["message"]) + str(eventos[0]["data"])


async def test_pergunta_comum_nao_vira_evento(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id = await _em_needs_input(harness, sensivel=False)
    assert st.repo.db.scalar("SELECT COUNT(*) FROM events WHERE run_id=? AND kind='pergunta_sensivel'",
                             (run_id,)) == 0


async def test_sucessora_de_pergunta_sensivel_e_recusada_pela_rota_do_painel(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id = await _em_needs_input(harness, sensivel=True)
    antes = st.repo.db.scalar("SELECT COUNT(*) FROM runs")
    async with _cliente(harness) as c:
        for comando in (f"{INCOMPLETO}\n{RESPOSTA_SOLTA}", COMMAND):     # com ou sem a senha: a pergunta decide
            r = await c.post(f"/api/runs/{run_id}/successor", json={"command": comando, "mode": "plan"})
            assert r.status_code == 409, r.text
            detalhe = r.json()["detail"]
            assert detalhe["code"] == "credencial_na_resposta" and detalhe["tipo"] == "senha"
            assert RESPOSTA_SOLTA not in r.text and "Contas e acesso" in detalhe["message"]
    assert st.repo.db.scalar("SELECT COUNT(*) FROM runs") == antes
    assert st.repo.run_row(run_id)["status"] == "needs_input"            # a antiga segue esperando
    assert RESPOSTA_SOLTA not in str(st.repo.db.query("SELECT message, data FROM events"))


async def test_pergunta_comum_com_codigo_solto_e_recusada_so_pelo_formato(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id = await _em_needs_input(harness, sensivel=False)
    async with _cliente(harness) as c:
        r = await c.post(f"/api/runs/{run_id}/successor", json={"command": f"{INCOMPLETO}\n884512", "mode": "plan"})
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "credencial_na_resposta" and r.json()["detail"]["tipo"] == "formato"
        assert st.repo.run_row(run_id)["status"] == "needs_input"
        r = await c.post(f"/api/runs/{run_id}/successor", json={"command": COMMAND, "mode": "plan"})
        assert r.status_code == 200, r.text
        assert r.json()["id"] != run_id


async def test_refinar_recusa_antes_da_ia_pela_pergunta_e_pelo_formato(harness: Harness) -> None:
    run_id = await _em_needs_input(harness, sensivel=False)
    refinos = len([c for c in harness.ai.calls if c.get("kind") == "refine"])
    async with _cliente(harness) as c:
        r = await c.post("/api/commands/refine", json={
            "command": INCOMPLETO, "run_id": run_id,
            "answers": [{"field": "password", "question": PERGUNTA_DE_SENHA, "answer": "minhasenha"}]})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "credencial_na_resposta"
        assert r.json()["detail"]["tipo"] == "senha"
        r = await c.post("/api/commands/refine", json={
            "command": INCOMPLETO, "run_id": run_id,
            "answers": [{"field": "recipient", "question": "Para qual contato?", "answer": "884512"}]})
        assert r.status_code == 409 and r.json()["detail"]["tipo"] == "formato"
        assert len([c for c in harness.ai.calls if c.get("kind") == "refine"]) == refinos   # a IA não foi chamada
        r = await c.post("/api/commands/refine", json={
            "command": INCOMPLETO, "run_id": run_id,
            "answers": [{"field": "recipient", "question": "Para qual contato?", "answer": "Ana"}]})
        assert r.status_code == 200, r.text


async def test_as_duas_leituras_publicas(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    db = st.repo.db
    comum = await _em_needs_input(harness, sensivel=False)
    assert pergunta_sensivel_da_execucao(db, comum) is None
    assert pergunta_sensivel_aberta(db) is None
    sensivel = await _em_needs_input(harness, sensivel=True)
    assert pergunta_sensivel_da_execucao(db, sensivel) == "senha"
    assert pergunta_sensivel_da_execucao(db, "nao-existe") is None
    assert pergunta_sensivel_aberta(db) == "senha"
    assert pergunta_sensivel_aberta(db, ["android-01"]) == "senha"
    assert pergunta_sensivel_aberta(db, ["android-02"]) is None
    st.runs.cancel(sensivel)                                            # respondida ou cancelada: não está mais aberta
    assert pergunta_sensivel_da_execucao(db, sensivel) is None
    assert pergunta_sensivel_aberta(db) is None


async def test_pedido_novo_de_uma_palavra_com_pergunta_sensivel_aberta(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    await _em_needs_input(harness, sensivel=True)
    with pytest.raises(RunError) as exc:
        st.runs.create(RunCreate(command=RESPOSTA_SOLTA, instance_ids=["android-01"], mode="plan",
                                 idempotency_key="palavra-solta-01"))
    assert exc.value.code == "credencial_na_resposta" and exc.value.details == {"tipo": "senha"}
    assert RESPOSTA_SOLTA not in exc.value.message
    # outro aparelho, ou frase inteira: segue
    st.runs.create(RunCreate(command=RESPOSTA_SOLTA, instance_ids=["android-02"], mode="plan",
                             idempotency_key="palavra-solta-02"))
    st.runs.create(RunCreate(command=COMMAND, instance_ids=["android-01"], mode="plan",
                             idempotency_key="frase-inteira-01"))
