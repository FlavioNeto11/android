"""31.111 F4: o ensino que nasce de uma falha começa com a causa provável da tentativa (diagnóstico do 30.13).

`GET /api/training/{id}` leva `origin.diagnostico` `{causa, rotulo, pergunta, fatos, proposta, amostra}` da tentativa
que falhou, pela mesma regra do relatório de falhas (o tipo da falha, relido pelo texto quando não gravado, e o
contexto daquela tentativa). `POST /api/training/from-run` sem intenção escrita sugere a intenção com a causa, quando
ela é conhecida; a da pessoa vence. Nenhuma IA é chamada: `indeterminada` é dita como tal, e a pergunta genérica pede
o que a etapa devia ter feito. Sem tentativa, ou com o diagnóstico em erro, a sessão abre com `diagnostico: null`.

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

from app.modules.learning.application.falhas import ServicoDeFalhas
from app.modules.learning.domain import ensino_da_falha as ef
from app.modules.learning.domain.vocabulario import CausaProvavel

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente
from .test_treino_a_partir_da_falha import _com_controle, _execucao


def _tipo(st, step: str, tipo: str) -> None:
    st.db.execute("UPDATE attempts SET failure_kind=? WHERE step_id=?", (tipo, step))


def test_toda_causa_tem_rotulo_e_pergunta() -> None:
    assert set(ef.ROTULO) == set(CausaProvavel) == set(ef.PERGUNTA)
    assert ef.intencao_sugerida("Enviar", None) == "Corrigir a etapa «Enviar»"
    assert ef.intencao_sugerida("Enviar", {"causa": "indeterminada", "rotulo": "x"}) == "Corrigir a etapa «Enviar»"
    assert ef.intencao_sugerida("Enviar", {"causa": "teto_de_ia", "rotulo": "y"}) == "Corrigir a etapa «Enviar»: y"


async def test_a_causa_conhecida_vai_ao_detalhe_e_a_intencao(harness: Harness) -> None:
    st, _rt, lease = await _com_controle(harness)
    run, step = _execucao(st, tentativas=2)
    _tipo(st, step, "ia_orcamento")
    chamadas = st.db.scalar("SELECT COUNT(*) FROM ai_calls")
    async with _cliente(harness) as c:
        r = await c.post("/api/training/from-run", json={"run_id": run, "step_id": step, "lease_id": lease})
        assert r.status_code == 201, r.text
        sessao = r.json()
        d = sessao["origin"]["diagnostico"]
        assert d["causa"] == CausaProvavel.TETO_DE_IA.value and d["pergunta"] == ef.PERGUNTA[CausaProvavel.TETO_DE_IA]
        assert {"codigo": "tipo", "valor": "ia_orcamento"} in d["fatos"]
        assert sessao["intent"] == f"Corrigir a etapa «Enviar a mensagem»: {ef.ROTULO[CausaProvavel.TETO_DE_IA]}"
        lido = (await c.get(f"/api/training/{sessao['id']}")).json()
        assert lido["origin"]["diagnostico"] == d
        lista = (await c.get("/api/training", params={"instance_id": "android-01"})).json()
        assert "diagnostico" not in lista[0]["origin"]                       # a lista fica leve, como o contexto
    assert st.db.scalar("SELECT COUNT(*) FROM ai_calls") == chamadas         # nenhuma IA


async def test_indeterminada_e_dita_e_a_intencao_da_pessoa_vence(harness: Harness) -> None:
    st, _rt, lease = await _com_controle(harness)
    run, step = _execucao(st, tentativas=1)
    _tipo(st, step, "outro")
    async with _cliente(harness) as c:
        r = await c.post("/api/training/from-run", json={"run_id": run, "step_id": step, "lease_id": lease,
                                                         "intent": "Tocar em Enviar e esperar"})
    assert r.status_code == 201, r.text
    d = r.json()["origin"]["diagnostico"]
    assert d["causa"] == "indeterminada" and d["pergunta"] == ef.PERGUNTA[CausaProvavel.INDETERMINADA]
    assert r.json()["intent"] == "Tocar em Enviar e esperar"


async def test_sem_tentativa_ou_com_o_diagnostico_em_erro_a_sessao_abre_sem_ele(harness: Harness,
                                                                               monkeypatch) -> None:  # type: ignore[no-untyped-def]
    st, _rt, lease = await _com_controle(harness)
    run, step = _execucao(st, "uncertain", tentativas=0)
    async with _cliente(harness) as c:
        r = await c.post("/api/training/from-run", json={"run_id": run, "step_id": step, "lease_id": lease})
    assert r.status_code == 201, r.text
    assert r.json()["origin"]["diagnostico"] is None and r.json()["intent"] == "Corrigir a etapa «Enviar a mensagem»"

    falhas = st.learning.extensao(ServicoDeFalhas)
    assert falhas is not None

    def quebra(_aid: str) -> None:
        raise RuntimeError("fonte fora")

    monkeypatch.setattr(falhas, "diagnostico_da_tentativa", quebra)
    assert falhas.diagnostico_para_o_ensino("qualquer") is None             # nunca derruba a sessão
