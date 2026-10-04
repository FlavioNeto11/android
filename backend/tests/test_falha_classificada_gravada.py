"""A falha classificada GRAVADA (ADR-054, decisão 4; pacote A2).

Até aqui o motivo de uma falha era só texto livre (`attempts.error`) e o legado é classificado na leitura, marcado
retroativo. Daqui em diante quem fecha a tentativa grava o tipo no vocabulário fechado:

- `repository.finish_attempt` classifica o erro FINAL — o que o `COALESCE` deixa gravado: o texto novo ou, sem ele, o
  que `note_attempt` (ou a reconciliação) já tinha anotado na tentativa;
- `scheduler._reconciliar` (e a pausa, e a tomada) grava `interrompida`, qualquer que seja o texto guardado; a
  tentativa interrompida que esperou a pessoa grava a razão pelo texto (29.74);
- a etapa grava o tipo do SEU desfecho final (`failed`, `uncertain`, `waiting_user`) e o apaga quando sai dele
  (confirmada à mão, comprovada depois): o tipo nunca sobra numa etapa que terminou bem.
"""
from __future__ import annotations

from app.models import AttemptStatus, ResolveBody
from app.modules.learning.domain.falhas import FailureKind, classificar_falha
from app.util import now_iso

from .conftest import Harness

F = FailureKind


async def _etapa_planejada(h: Harness) -> tuple[str, str]:
    """Uma execução só planejada (nada roda): a etapa `open_app` do android-01 e o id da execução."""
    run = h.run(["android-01"], mode="plan")
    await h.wait_run(run.id, statuses=("planned",))
    return run.id, f"{run.id}:android-01:v1:open_app"


def _nova_tentativa(h: Harness, step_id: str, numero: int) -> str:
    assert h.state is not None
    tentativa = f"{step_id}:a{numero}"
    h.state.db.execute("INSERT INTO attempts(id, step_id, number, status, started_at) VALUES (?,?,?,?,?)",
                       (tentativa, step_id, numero, "running", now_iso()))
    return tentativa


def _tipo(h: Harness, tentativa: str) -> str | None:
    assert h.state is not None
    valor = h.state.db.scalar("SELECT failure_kind FROM attempts WHERE id=?", (tentativa,))
    return None if valor is None else str(valor)


async def test_finish_attempt_grava_o_tipo_do_erro_final_depois_do_coalesce(harness: Harness) -> None:
    assert harness.state is not None
    repo = harness.state.repo
    _, etapa = await _etapa_planejada(harness)

    # 1) só `note_attempt` escreveu o erro: fechar sem texto novo classifica o que ficou gravado
    a1 = _nova_tentativa(harness, etapa, 1)
    repo.note_attempt(a1, error="Tempo da etapa esgotado (180s).")
    repo.finish_attempt(a1, AttemptStatus.failed)
    assert _tipo(harness, a1) == F.PRAZO_DA_ETAPA.value

    # 2) o texto novo vence o anotado (é o que o COALESCE grava) — e o tipo é o dele
    a2 = _nova_tentativa(harness, etapa, 2)
    repo.note_attempt(a2, error="Tempo da etapa esgotado (180s).")
    repo.finish_attempt(a2, AttemptStatus.failed, error="A IA insistiu em chamadas inválidas.")
    assert _tipo(harness, a2) == F.IA_CHAMADA_INVALIDA.value
    assert harness.state.db.scalar("SELECT error FROM attempts WHERE id=?", (a2,)) == \
        "A IA insistiu em chamadas inválidas."

    # 3) recuperada: o erro original fica como registro, mas tentativa comprovada não tem tipo de falha
    a3 = _nova_tentativa(harness, etapa, 3)
    repo.note_attempt(a3, error="Pós-condição não comprovada: a conversa não abriu.")
    repo.finish_attempt(a3, AttemptStatus.succeeded)
    assert _tipo(harness, a3) is None

    # 4) incerta: o texto decide
    a4 = _nova_tentativa(harness, etapa, 4)
    repo.finish_attempt(a4, AttemptStatus.uncertain,
                        error="O efeito foi disparado, mas não foi possível comprovar o resultado pela tela.")
    assert _tipo(harness, a4) == F.EFEITO_NAO_COMPROVADO.value

    # 5) interrompida que esperou a pessoa (29.74): o texto é a razão da parada e decide
    a5 = _nova_tentativa(harness, etapa, 5)
    repo.finish_attempt(a5, AttemptStatus.interrupted, error="O app pede autenticação (senha).",
                        recovery="Aguardando o usuário")
    assert _tipo(harness, a5) == F.AUTENTICACAO.value

    # 5b) interrompida pela pausa: o status decide antes do texto (o texto guardado pode ser o erro anterior)
    a5b = _nova_tentativa(harness, etapa, 7)
    repo.note_attempt(a5b, error="O app pede autenticação (senha).")
    repo.finish_attempt(a5b, AttemptStatus.interrupted, recovery="Pausado pelo usuário num ponto seguro")
    assert _tipo(harness, a5b) == F.INTERROMPIDA.value

    # 6) cancelada é decisão, não defeito
    a6 = _nova_tentativa(harness, etapa, 6)
    repo.finish_attempt(a6, AttemptStatus.cancelled, error="Cancelado pelo usuário")
    assert _tipo(harness, a6) is None

    # o mesmo classificador puro da leitura retroativa: gravado e retroativo nunca discordam
    for tentativa in (a1, a2, a4, a5, a5b):
        linha = harness.state.db.one("SELECT error, status, failure_kind, recovery FROM attempts WHERE id=?",
                                     (tentativa,))
        assert linha is not None
        assert classificar_falha(linha["error"], linha["status"], recovery=linha["recovery"]) ==             FailureKind(linha["failure_kind"])


async def test_reconciliar_grava_interrompida_mesmo_com_erro_anotado(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    _, etapa = await _etapa_planejada(harness)
    st.db.execute("UPDATE steps SET status='running', attempts=1 WHERE id=?", (etapa,))
    tentativa = _nova_tentativa(harness, etapa, 1)
    st.repo.note_attempt(tentativa, error="Pós-condição não comprovada: a conversa não abriu.")

    st.scheduler._reconciliar([st.repo.step_row(etapa)], "backend reiniciado")  # noqa: SLF001

    linha = st.db.one("SELECT status, error, failure_kind FROM attempts WHERE id=?", (tentativa,))
    assert linha is not None
    assert linha["status"] == "interrupted"
    assert linha["error"] == "Pós-condição não comprovada: a conversa não abriu."   # o texto anterior fica
    assert linha["failure_kind"] == F.INTERROMPIDA.value                            # o tipo é o do fechamento
    assert st.repo.step_row(etapa)["status"] == "ready"
    assert st.repo.step_row(etapa)["failure_kind"] is None                        # voltou à fila: não é desfecho


async def test_a_etapa_grava_o_tipo_do_desfecho_final_e_o_apaga_ao_sair_dele(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    harness.fakes["android-02"].require_login = True          # o app pede login: a etapa espera uma pessoa
    run = harness.run(["android-01", "android-02"])
    detail = await harness.wait_run(run.id)
    by = {o.instance_id: o for o in detail.objectives}
    assert by["android-01"].status == "succeeded" and by["android-02"].status == "waiting_user"

    # comprovadas: nenhuma etapa nem tentativa com tipo de falha
    assert st.db.scalar("SELECT COUNT(*) FROM steps WHERE run_id=? AND instance_id='android-01'"
                        " AND failure_kind IS NOT NULL", (run.id,)) == 0
    assert st.db.scalar("SELECT COUNT(*) FROM attempts a JOIN steps s ON s.id=a.step_id WHERE s.run_id=?"
                        " AND s.instance_id='android-01' AND a.failure_kind IS NOT NULL", (run.id,)) == 0

    # parada esperando a pessoa: a etapa diz POR QUÊ (autenticação), e a tentativa interrompida também (29.74)
    parada = st.db.one("SELECT id, status, status_detail, failure_kind FROM steps WHERE run_id=?"
                       " AND instance_id='android-02' AND status='waiting_user'", (run.id,))
    assert parada is not None
    assert parada["failure_kind"] == F.AUTENTICACAO.value
    assert classificar_falha(parada["status_detail"], "waiting_user") is F.AUTENTICACAO
    tentativa = st.db.one("SELECT status, failure_kind FROM attempts WHERE step_id=? ORDER BY number DESC LIMIT 1",
                          (parada["id"],))
    assert tentativa is not None
    assert (tentativa["status"], tentativa["failure_kind"]) == ("interrupted", F.AUTENTICACAO.value)

    # a pessoa confirma à mão: a etapa sai do desfecho de falha e o tipo não sobra nela
    st.runs.resolve(run.id, by["android-02"].id, ResolveBody(resolution="confirm_done", note="loguei e abri"))
    confirmada = st.db.one("SELECT status, failure_kind FROM steps WHERE id=?", (parada["id"],))
    assert confirmada is not None
    assert (confirmada["status"], confirmada["failure_kind"]) == ("succeeded", None)
