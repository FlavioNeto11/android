"""31.91 (F2): o `propose` do treino recebe as respostas da pessoa às perguntas da proposta anterior.

Prova `simulated`: provedor simulado com espião; nenhuma chamada paga e nenhum aparelho real.
"""
from __future__ import annotations

import json
import logging

import pytest

from app.planning.training import TrainingRequest, trainer_user

from .conftest import Harness
from .test_modo_treinamento import _gravar_mensagem, _no_controle
from .test_perfil_bloqueado_e_capacidades import _cliente

# nomes fictícios: nada de pessoa real, e-mail ou telefone
P_TEXTO = "O texto da mensagem é sempre o mesmo ou muda?"
P_CONTATO = "O contato é sempre o mesmo?"
R_TEXTO = "muda a cada envio, abobora-lilas"
CHAVES_DE_ANTES = {"summary", "command_template", "parameters", "steps", "discarded", "questions", "app_id"}


def _espiar(harness: Harness, perguntas: list[str]) -> list[TrainingRequest]:
    """Troca o `generalize` do simulado por um que guarda o pedido e devolve `perguntas` na proposta."""
    pedidos: list[TrainingRequest] = []
    original = harness.ai.inner.generalize

    async def generalize(req: TrainingRequest):
        pedidos.append(req)
        proposta, uso = await original(req)
        proposta["questions"] = list(perguntas)
        return proposta, uso

    harness.ai.inner.generalize = generalize
    return pedidos


async def _sessao(harness: Harness) -> tuple[object, str]:
    st, rt, lease = await _no_controle(harness)
    sid = await _gravar_mensagem(st, rt, lease, harness.fakes["android-01"])
    return st, sid


def _resp(pergunta: str, resposta: str) -> dict[str, str]:
    return {"question": pergunta, "answer": resposta}


async def test_as_respostas_entram_no_texto_do_provedor_com_uma_chamada(harness: Harness) -> None:
    st, sid = await _sessao(harness)
    pedidos = _espiar(harness, [P_TEXTO, P_CONTATO])
    antes = harness.ai.count("generalize")

    prop = (await st.skills.propose(sid, {"answers": [_resp(P_TEXTO, R_TEXTO)]}))["proposal"]

    assert harness.ai.count("generalize") - antes == 1
    texto = trainer_user(pedidos[0])
    assert "Respostas da pessoa às suas perguntas anteriores (não pergunte de novo):" in texto
    assert f"- {P_TEXTO} → {R_TEXTO}" in texto
    assert prop["answers"] == [_resp(P_TEXTO, R_TEXTO)]
    assert st.training.get(sid)["proposal"]["answers"] == prop["answers"]          # GET devolve a proposta com `answers`


async def test_acumula_em_duas_chamadas_e_a_mesma_pergunta_substitui(harness: Harness) -> None:
    st, sid = await _sessao(harness)
    pedidos = _espiar(harness, [])
    await st.skills.propose(sid, {"answers": [_resp(P_TEXTO, "primeira"), _resp(P_CONTATO, "sim")]})
    # a segunda chamada responde só uma pergunta (a mesma, com caixa e espaços diferentes) e uma nova
    prop = (await st.skills.propose(sid, {"answers": [_resp(f"  {P_TEXTO.upper()} ", "segunda"),
                                                      _resp("Precisa confirmar o envio?", "não")]}))["proposal"]
    respostas = {r["question"].strip().casefold(): r["answer"] for r in prop["answers"]}
    assert len(prop["answers"]) == 3
    assert respostas[P_TEXTO.casefold()] == "segunda" and respostas[P_CONTATO.casefold()] == "sim"
    texto = trainer_user(pedidos[1])
    assert "→ segunda" in texto and "→ primeira" not in texto and f"- {P_CONTATO} → sim" in texto
    # uma terceira chamada SEM corpo mantém o que foi guardado
    prop3 = (await st.skills.propose(sid))["proposal"]
    assert prop3["answers"] == prop["answers"]


async def test_pergunta_respondida_nao_volta_em_questions(harness: Harness) -> None:
    st, sid = await _sessao(harness)
    _espiar(harness, [P_TEXTO, P_CONTATO])
    prop = (await st.skills.propose(sid, {"answers": [_resp(f" {P_TEXTO.lower()}", R_TEXTO)]}))["proposal"]
    assert prop["questions"] == [P_CONTATO]
    # nas chamadas seguintes a IA insiste na pergunta e ela continua fora
    prop = (await st.skills.propose(sid))["proposal"]
    assert prop["questions"] == [P_CONTATO]


@pytest.mark.parametrize("corpo", [
    [],                                                                     # não é objeto
    {"answers": "x"},
    {"answers": [_resp("p", "r")] * 2},                                    # pergunta repetida no mesmo corpo
    {"answers": [_resp("p", "r"), _resp(" P ", "outra")]},                 # repetida por strip/casefold
    {"answers": [_resp(f"q{i}", "r") for i in range(9)]},                  # mais de 8
    {"answers": [_resp("", "r")]},
    {"answers": [_resp("   ", "r")]},
    {"answers": [_resp("p", "")]},
    {"answers": [_resp("p", "  ")]},
    {"answers": [_resp("p" * 301, "r")]},
    {"answers": [_resp("p", "r" * 501)]},
    {"answers": [{"question": "p"}]},
    {"answers": [{"question": "p", "answer": 3}]},
    {"answers": ["texto"]},
    {"answers": [_resp("p", "r")], "outro": 1},
])
async def test_erro_de_forma_e_400_invalid_answers_sem_chamar_a_ia(harness: Harness, corpo: object) -> None:
    st, sid = await _sessao(harness)
    antes = harness.ai.count("generalize")
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/propose", json=corpo)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_answers", r.text
    assert harness.ai.count("generalize") == antes
    assert st.training.get(sid)["status"] == "recorded" and not st.training.get(sid)["proposal"]


async def test_json_quebrado_e_o_teto_de_16_acumuladas_sao_invalid_answers(harness: Harness) -> None:
    st, sid = await _sessao(harness)
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/propose", content=b"{nao e json", headers={"content-type": "application/json"})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_answers"
        for lote in range(2):                                              # 2 x 8 = 16: cabe
            corpo = {"answers": [_resp(f"pergunta {lote}-{i}", "ok") for i in range(8)]}
            assert (await c.post(f"/api/training/{sid}/propose", json=corpo)).status_code == 200
        antes = harness.ai.count("generalize")
        r = await c.post(f"/api/training/{sid}/propose", json={"answers": [_resp("a décima sétima", "ok")]})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_answers"
        assert harness.ai.count("generalize") == antes
        # trocar uma resposta que já existe não aumenta o total
        r = await c.post(f"/api/training/{sid}/propose", json={"answers": [_resp("PERGUNTA 0-0", "mudou")]})
        assert r.status_code == 200 and len(r.json()["proposal"]["answers"]) == 16


@pytest.mark.parametrize("segredo", ["o código é 482913", "minha senha 12345678", "sk-ant-abcdefghijkl123456"])
async def test_resposta_com_formato_de_segredo_e_recusada_antes_da_ia(harness: Harness, segredo: str) -> None:
    st, sid = await _sessao(harness)
    _espiar(harness, [P_TEXTO])
    primeira = (await st.skills.propose(sid, {"answers": [_resp(P_CONTATO, "sim")]}))["proposal"]
    atualizada = st.training.get(sid)["updated_at"]
    antes = harness.ai.count("generalize")
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/propose", json={"answers": [_resp(P_TEXTO, segredo)]})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "resposta_sensivel", r.text
    assert r.json()["detail"]["message"] == "Não escreva senha nem código aqui: a proposta não precisa disso."
    assert segredo not in r.text
    assert harness.ai.count("generalize") == antes                              # nenhuma chamada de IA
    sessao = st.training.get(sid)
    assert sessao["proposal"] == primeira and sessao["updated_at"] == atualizada   # a sessão ficou intacta


async def test_falar_da_senha_sem_o_valor_nao_e_recusado(harness: Harness) -> None:
    st, sid = await _sessao(harness)
    _espiar(harness, [])
    prop = (await st.skills.propose(sid, {"answers": [_resp("Usa a conta salva?", "sim, com a senha da conta")]}))["proposal"]
    assert prop["answers"][0]["answer"] == "sim, com a senha da conta"


async def test_sem_corpo_a_proposta_e_a_de_antes(harness: Harness) -> None:
    st, sid = await _sessao(harness)
    async with _cliente(harness) as c:
        sem_corpo = (await c.post(f"/api/training/{sid}/propose")).json()["proposal"]
        vazia = (await c.post(f"/api/training/{sid}/propose", json={"answers": []})).json()["proposal"]
        objeto = (await c.post(f"/api/training/{sid}/propose", json={})).json()["proposal"]
    assert set(sem_corpo) == CHAVES_DE_ANTES and "answers" not in sem_corpo
    assert vazia == sem_corpo == objeto
    assert sem_corpo["steps"] and sem_corpo["questions"] == []
    # o texto enviado ao provedor também é o de antes
    req = TrainingRequest(intent="x", app_id=None, apps=[], inputs=[])
    assert "Respostas da pessoa" not in trainer_user(req)


@pytest.mark.parametrize("quando,codigo,status", [("gravando", "still_recording", 409), ("descartada", "closed", 409)])
async def test_guardas_de_estado_continuam(harness: Harness, quando: str, codigo: str, status: int) -> None:
    st, rt, lease = await _no_controle(harness)
    s = st.training.start("android-01", intent="x", lease_id=lease)
    if quando == "descartada":
        st.training.stop(s["id"], discard=True)
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{s['id']}/propose", json={"answers": [_resp("p", "r")]})
    assert r.status_code == status and r.json()["detail"]["code"] == codigo


async def test_salvar_e_previa_funcionam_com_answers_e_nada_vai_para_flows_nem_recipes(harness: Harness) -> None:
    st, sid = await _sessao(harness)
    _espiar(harness, [P_CONTATO])
    await st.skills.propose(sid, {"answers": [_resp(P_TEXTO, R_TEXTO)]})
    assert "answers" in st.training.get(sid)["proposal"]

    async with _cliente(harness) as c:
        previa = await c.post(f"/api/training/{sid}/preview", json={})
        assert previa.status_code == 200, previa.text
        salvo = await c.post(f"/api/training/{sid}/save", json={})
    assert salvo.status_code == 200, salvo.text
    flow_id = salvo.json()["flow_id"]
    assert st.training.get(sid)["status"] == "saved"
    flows = json.dumps([dict(r) for r in st.db.query("SELECT * FROM flows WHERE id=?", (flow_id,))], ensure_ascii=False)
    receitas = json.dumps([dict(r) for r in st.db.query("SELECT * FROM recipes")], ensure_ascii=False)
    # a sessão guarda as respostas (é onde elas moram); o que nasce do salvar (fluxo, receitas, etapas) não as leva
    for tabela in (flows, receitas, json.dumps(salvo.json()["steps"], ensure_ascii=False)):
        assert R_TEXTO not in tabela and "abobora-lilas" not in tabela and "answers" not in tabela


async def test_salvar_aceita_a_proposta_revisada_que_ainda_traz_answers(harness: Harness) -> None:
    st, sid = await _sessao(harness)
    prop = (await st.skills.propose(sid, {"answers": [_resp(P_TEXTO, R_TEXTO)]}))["proposal"]
    assert prop["answers"]
    salvo = await st.skills.save(sid, proposal=prop, profile_ids=[], group_ids=[])
    assert salvo["flow_id"]
    assert R_TEXTO not in json.dumps([dict(r) for r in st.db.query("SELECT * FROM flows")], ensure_ascii=False)


async def test_o_texto_da_resposta_nao_vai_a_log_nem_a_evento(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    st, sid = await _sessao(harness)
    _espiar(harness, [])
    with caplog.at_level(logging.DEBUG):
        await st.skills.propose(sid, {"answers": [_resp(P_TEXTO, R_TEXTO)]})
        async with _cliente(harness) as c:
            await c.post(f"/api/training/{sid}/propose", json={"answers": [_resp(P_CONTATO, "dado-sensivel-xyzzy")]})
    assert "abobora-lilas" not in caplog.text and "xyzzy" not in caplog.text
    eventos = json.dumps([dict(r) for r in st.db.query("SELECT * FROM events")], ensure_ascii=False)
    assert "abobora-lilas" not in eventos and "xyzzy" not in eventos
