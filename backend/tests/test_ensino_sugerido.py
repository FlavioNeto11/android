"""31.116 (parte 2): `GET /api/runs/{run_id}/steps/{step_id}/ensino-sugerido` pré-preenche "Ensinar a corrigir".

O painel mostra a intenção sugerida, a pergunta e o rótulo da causa provável ANTES de a sessão existir, pelo mesmo
diagnóstico do 31.111 F4 (`FontesDaTentativa.chave_da_tentativa`). Só leitura: sem IA, sem gravar nada, sem pedir o
controle do aparelho. As recusas são as do `POST /training/from-run`; sem tentativa, `null`; com o diagnóstico em erro,
a intenção de base sem pergunta nem rótulo.

Adendo v1.82 (pedido da leitura de UX da Portal): a resposta ganha `causa`, o código do diagnóstico, e a pergunta passa a
ser a do ESTADO da etapa: em `waiting_user` ela parou esperando a pessoa, não falhou, e a pergunta diz o que ensinar para
seguir (mesmo com o diagnóstico em erro).

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

from app.modules.learning.application.falhas import ServicoDeFalhas
from app.modules.learning.domain import ensino_da_falha as ef
from app.modules.learning.domain.vocabulario import CausaProvavel

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente
from .test_treino_a_partir_da_falha import _execucao

TABELAS = ("training_sessions", "events", "ai_calls", "learning_backlog")


def _contagens(st) -> dict[str, int]:  # type: ignore[no-untyped-def]
    return {t: int(st.db.scalar(f"SELECT COUNT(*) FROM {t}") or 0) for t in TABELAS}


async def test_a_causa_conhecida_preenche_intencao_pergunta_e_rotulo_sem_gravar(harness: Harness) -> None:
    st = harness.state
    run, step = _execucao(st, tentativas=2)
    st.db.execute("UPDATE attempts SET failure_kind='ia_orcamento' WHERE step_id=?", (step,))
    antes = _contagens(st)
    async with _cliente(harness) as c:
        r = await c.get(f"/api/runs/{run}/steps/{step}/ensino-sugerido")
    assert r.status_code == 200, r.text
    rotulo = ef.ROTULO[CausaProvavel.TETO_DE_IA]
    assert r.json() == {"intent": f"Corrigir a etapa «Enviar a mensagem»: {rotulo}",
                        "pergunta": ef.PERGUNTA[CausaProvavel.TETO_DE_IA], "rotulo": rotulo,
                        "causa": CausaProvavel.TETO_DE_IA.value}
    assert _contagens(st) == antes                                   # nada gravado, nenhuma IA, nenhuma sessão


async def test_indeterminada_sugere_a_intencao_de_base_com_a_pergunta_generica(harness: Harness) -> None:
    st = harness.state
    run, step = _execucao(st, tentativas=1)
    st.db.execute("UPDATE attempts SET failure_kind='outro' WHERE step_id=?", (step,))
    async with _cliente(harness) as c:
        corpo = (await c.get(f"/api/runs/{run}/steps/{step}/ensino-sugerido")).json()
    assert corpo["intent"] == "Corrigir a etapa «Enviar a mensagem»"
    assert corpo["pergunta"] == ef.PERGUNTA[CausaProvavel.INDETERMINADA]
    assert corpo["causa"] == CausaProvavel.INDETERMINADA.value


async def test_sem_tentativa_e_null_e_as_recusas_sao_as_do_from_run(harness: Harness) -> None:
    st = harness.state
    run, step = _execucao(st, "uncertain", tentativas=0)
    async with _cliente(harness) as c:
        vazio = await c.get(f"/api/runs/{run}/steps/{step}/ensino-sugerido")
        assert vazio.status_code == 200 and vazio.json() is None, vazio.text
        outra = await c.get(f"/api/runs/{run}/steps/{run}:android-01:v1:nao-existe/ensino-sugerido")
        assert outra.status_code == 404 and "step_not_found" in outra.text, outra.text
        st.db.execute("UPDATE steps SET status='succeeded' WHERE id=?", (step,))
        ok = await c.get(f"/api/runs/{run}/steps/{step}/ensino-sugerido")
        assert ok.status_code == 409 and "step_not_failed" in ok.text, ok.text


async def test_com_o_diagnostico_em_erro_fica_a_intencao_de_base(harness: Harness, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    st = harness.state
    run, step = _execucao(st, tentativas=1)
    falhas = st.learning.extensao(ServicoDeFalhas)
    assert falhas is not None

    def quebra(_aid: str) -> None:
        raise RuntimeError("fonte fora")

    monkeypatch.setattr(falhas, "diagnostico_da_tentativa", quebra)
    async with _cliente(harness) as c:
        corpo = (await c.get(f"/api/runs/{run}/steps/{step}/ensino-sugerido")).json()
    assert corpo == {"intent": "Corrigir a etapa «Enviar a mensagem»", "pergunta": None, "rotulo": None, "causa": None}


async def test_parada_esperando_a_pessoa_tem_a_pergunta_do_estado_e_a_causa(harness: Harness) -> None:
    st = harness.state
    run, step = _execucao(st, "waiting_user", tentativas=1)
    st.db.execute("UPDATE attempts SET failure_kind='ia_orcamento' WHERE step_id=?", (step,))
    async with _cliente(harness) as c:
        corpo = (await c.get(f"/api/runs/{run}/steps/{step}/ensino-sugerido")).json()
    assert corpo["pergunta"] == ef.PERGUNTA_ESPERANDO != ef.PERGUNTA[CausaProvavel.TETO_DE_IA]
    assert corpo["causa"] == CausaProvavel.TETO_DE_IA.value and corpo["rotulo"] == ef.ROTULO[CausaProvavel.TETO_DE_IA]


async def test_parada_esperando_com_o_diagnostico_em_erro_ainda_pergunta_pelo_estado(harness: Harness,
                                                                                  monkeypatch) -> None:  # type: ignore[no-untyped-def]
    st = harness.state
    run, step = _execucao(st, "waiting_user", tentativas=1)
    falhas = st.learning.extensao(ServicoDeFalhas)
    assert falhas is not None

    def quebra(_aid: str) -> None:
        raise RuntimeError("fonte fora")

    monkeypatch.setattr(falhas, "diagnostico_da_tentativa", quebra)
    async with _cliente(harness) as c:
        corpo = (await c.get(f"/api/runs/{run}/steps/{step}/ensino-sugerido")).json()
    assert corpo == {"intent": "Corrigir a etapa «Enviar a mensagem»", "pergunta": ef.PERGUNTA_ESPERANDO,
                     "rotulo": None, "causa": None}


def test_a_pergunta_pelo_estado_e_pura() -> None:
    d = {"causa": "plano", "pergunta": "p", "rotulo": "r"}
    assert ef.pergunta_da_etapa("waiting_user", d) == ef.pergunta_da_etapa("waiting_user", None) == ef.PERGUNTA_ESPERANDO
    assert ef.pergunta_da_etapa("failed", d) == ef.pergunta_da_etapa("uncertain", d) == "p"
    assert ef.pergunta_da_etapa("failed", None) is None and ef.pergunta_da_etapa("failed", {"pergunta": 3}) is None
