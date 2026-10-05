"""30.69: o que o digest do aprendizado conta durante e depois da espera pela pessoa (`awaiting_person`, 29.93).

O assentamento na saída da espera (o gancho que chama o digest) é do 29.93 (#382); aqui fica o que o digest grava.
A exposição da etapa que espera não congela: `waiting_user` saiu de `_ETAPA_FINAL` (`licoes_sql.py`). A curadoria
(`execucoes_a_preencher`) escolhe as execuções pelo `finished_at`, que a espera já grava, e preenchia a exposição com o
desfecho `waiting_user`, que nunca mais mudava.

Prova `simulated`: o harness com aparelho e provedor simulados.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.modules.learning.application.licoes import PREENCHER_DIAS
from app.modules.learning.domain.backlog import ESPERANDO_A_PESSOA, SEM_CONDUCAO
from app.modules.learning.infrastructure.licoes_sql import SqlLicoesRepository
from app.modules.learning.infrastructure.relatorio_sql import FontesDeFalhaSql
from app.state import AppState
from app.util import now, to_iso

from .conftest import Harness
from .test_aguardando_pessoa import _esperando_login


def _estado(h: Harness) -> AppState:
    assert h.state is not None
    return h.state


async def test_a_exposicao_da_etapa_que_espera_nao_fecha_e_a_cancelada_fecha(harness: Harness) -> None:
    """`waiting_user` não é desfecho do ator: a exposição fica pendente. Quem sai da espera sem retomar vai a
    `cancelled`, e aí sim fecha."""
    st = _estado(harness)
    run_id = await _esperando_login(harness)
    etapa = st.db.one("SELECT id FROM steps WHERE run_id=? AND status='waiting_user' ORDER BY seq LIMIT 1", (run_id,))
    assert etapa is not None
    licoes = SqlLicoesRepository(st.db, precos=dict)
    assert licoes._desfecho_da_etapa(str(etapa["id"]), {}) is None
    # a curadoria (`execucoes_a_preencher`) escolhe pelo `finished_at`, que a espera já grava: é ESTE desfecho nulo que
    # a impede de congelar a exposição
    assert st.repo.run_row(run_id)["finished_at"] is not None
    st.db.execute("UPDATE steps SET status='cancelled' WHERE id=?", (etapa["id"],))
    desfecho = licoes._desfecho_da_etapa(str(etapa["id"]), {})
    assert desfecho is not None and desfecho.outcome == "cancelled"


async def test_a_curadoria_nao_escolhe_a_execucao_que_espera_e_escolhe_quando_ela_sai(harness: Harness) -> None:
    """30.71 (sobra da leitura do #390): `execucoes_a_preencher` escolhe pelo `finished_at`, que a espera já grava. Sem
    o filtro de status, a execução `awaiting_person` entrava em toda passada sem preencher nada, e o `LIMIT 200` por
    `run_id` podia tirar a vez de quem fecha. Quando ela sai da espera (aqui, cancelada), volta a ser escolhida."""
    st = _estado(harness)
    run_id = await _esperando_login(harness)
    etapa = st.db.one("SELECT id FROM steps WHERE run_id=? AND status='waiting_user' ORDER BY seq LIMIT 1", (run_id,))
    assert etapa is not None
    st.db.execute("UPDATE runs SET simulated=0 WHERE id=?", (run_id,))     # a curadoria só olha execução real
    st.db.execute("INSERT INTO learning_exposures(item_id, unit_id, role, arm, run_id, created_at)"
                  " VALUES ('item-30-71', ?, 'ator', 'with', ?, ?)", (f"step:{etapa['id']}", run_id, to_iso(now())))
    licoes = SqlLicoesRepository(st.db, precos=dict)
    desde = to_iso(now() - timedelta(days=1))
    assert st.repo.run_row(run_id)["status"] == "awaiting_person"
    assert run_id not in licoes.execucoes_a_preencher(desde)
    st.runs.cancel(run_id)
    await harness.wait(lambda: st.repo.run_row(run_id)["status"] == "cancelled", what="execução cancelada")
    # o assentamento da saída (#382) já preencheu a exposição pelo digest; desfeito o preenchimento, a curadoria a
    # escolhe de novo, porque a execução não espera mais
    await harness.wait(lambda: st.db.scalar("SELECT filled_at FROM learning_exposures WHERE run_id=?", (run_id,))
                       is not None, what="exposição preenchida na saída da espera")
    st.db.execute("UPDATE learning_exposures SET filled_at=NULL, outcome=NULL WHERE run_id=?", (run_id,))
    assert run_id in licoes.execucoes_a_preencher(desde)


async def test_a_espera_mais_longa_que_a_janela_conta_da_saida(harness: Harness) -> None:
    """30.71, N1 da leitura: o cancelamento (e o vencimento) da espera não limpa o `finished_at`, que guarda a hora da
    ENTRADA. Com uma espera mais longa que `PREENCHER_DIAS` e o digest da saída perdido, a janela pelo `finished_at`
    deixaria a exposição de fora para sempre; a janela conta da saída, a marca `assentada_em` do #382."""
    st = _estado(harness)
    run_id = await _esperando_login(harness)
    etapa = st.db.one("SELECT id FROM steps WHERE run_id=? AND status='waiting_user' ORDER BY seq LIMIT 1", (run_id,))
    assert etapa is not None
    st.db.execute("UPDATE runs SET simulated=0 WHERE id=?", (run_id,))
    st.db.execute("INSERT INTO learning_exposures(item_id, unit_id, role, arm, run_id, created_at)"
                  " VALUES ('item-30-71-n1', ?, 'ator', 'with', ?, ?)", (f"step:{etapa['id']}", run_id, to_iso(now())))
    entrada = to_iso(now() - timedelta(days=PREENCHER_DIAS + 2))       # a espera começou antes da janela
    st.db.execute("UPDATE runs SET finished_at=? WHERE id=?", (entrada, run_id))
    st.runs.cancel(run_id)
    await harness.wait(lambda: st.repo.run_row(run_id)["status"] == "cancelled", what="execução cancelada")
    await harness.wait(lambda: not st._digestoes, what="digests do gancho")   # noqa: SLF001
    linha = st.repo.run_row(run_id)
    assert linha["finished_at"] == entrada                              # a saída não regrava a hora da entrada
    assert linha["assentada_em"] is not None and linha["assentada_em"] > entrada
    # o digest da saída "perdido": a exposição volta a pendente
    st.db.execute("UPDATE learning_exposures SET filled_at=NULL, outcome=NULL WHERE run_id=?", (run_id,))
    licoes = SqlLicoesRepository(st.db, precos=dict)
    desde = to_iso(now() - timedelta(days=PREENCHER_DIAS))
    assert run_id in licoes.execucoes_a_preencher(desde)
    # sem a marca da saída, só o `finished_at` velho: fora da janela (é o defeito que o COALESCE fecha)
    st.db.execute("UPDATE runs SET assentada_em=NULL WHERE id=?", (run_id,))
    assert run_id not in licoes.execucoes_a_preencher(desde)


async def test_cancelar_e_repetir_nao_tocam_etapa_que_ja_teve_veredito(harness: Harness) -> None:
    """D1-N2 da leitura do #374 (a catraca da premissa do 30.70): `cancel_open_steps` e `revise_plan` só pegam etapa
    ABERTA. Se passassem a tocar `failed` ou `uncertain`, o filtro `NOT IN ('waiting_user','cancelled','skipped')` de
    `reproducao_sql._ETAPAS` esconderia um voto de verdade contra a receita."""
    st = _estado(harness)
    run_id = await _esperando_login(harness)
    oid = str(st.db.scalar("SELECT id FROM objectives WHERE run_id=?", (run_id,)))
    etapas = [str(r["id"]) for r in st.db.query("SELECT id FROM steps WHERE objective_id=? ORDER BY seq", (oid,))]
    assert len(etapas) >= 2
    st.db.execute("UPDATE steps SET status='failed' WHERE id=?", (etapas[0],))
    st.db.execute("UPDATE steps SET status='uncertain' WHERE id=?", (etapas[1],))
    st.repo.cancel_open_steps(run_id, objective_id=oid, reason="teste")
    st.repo.revise_plan(oid, "teste", [])
    status = {str(r["id"]): str(r["status"]) for r in st.db.query(
        "SELECT id, status FROM steps WHERE id IN (?, ?)", (etapas[0], etapas[1]))}
    assert status == {etapas[0]: "failed", etapas[1]: "uncertain"}


async def test_a_etapa_que_espera_sem_conducao_aparece_como_esperando_a_pessoa(harness: Harness) -> None:
    """N1 da leitura do #374: desde o 30.70 a espera não grava `driven_by`. No relatório de condução ela é
    `esperando_pessoa`, não "sem condução" (`-`, a etapa anterior à coluna), e fica fora da porcentagem por receita."""
    st = _estado(harness)
    run_id = await _esperando_login(harness)
    etapa = st.db.one("SELECT id FROM steps WHERE run_id=? AND status='waiting_user' ORDER BY seq LIMIT 1", (run_id,))
    assert etapa is not None
    st.db.execute("UPDATE steps SET driven_by=NULL WHERE id=?", (etapa["id"],))
    saude = FontesDeFalhaSql(st.db).saude(to_iso(now() - timedelta(days=1)), to_iso(now() + timedelta(minutes=1)),
                                          simulados=True)
    assert saude.etapas_por_conducao.get(ESPERANDO_A_PESSOA, 0) >= 1, saude.etapas_por_conducao
    assert SEM_CONDUCAO not in saude.etapas_por_conducao, saude.etapas_por_conducao
    assert saude.intervencoes >= 1


#: 30.71 (T1 da leitura): a medida é DA execução, não da tabela inteira. Uma escrita legítima de fora entre o antes e
#: o depois (outra execução, a curadoria de fundo) reprovaria à toa. As tabelas com `run_id` se medem por ele; as de
#: item, pelos itens que esta execução tocou (evidência ou exposição dela).
_POR_EXECUCAO = ("learning_evidence", "learning_exposures", "skill_validation_results", "learning_transitions")
_ITENS_DA_EXECUCAO = ("SELECT item_ref FROM learning_evidence WHERE run_id=?"
                      " UNION SELECT item_id FROM learning_exposures WHERE run_id=?")

#: Além das linhas, os contadores em cache de `learning_items`. Um minerador que somasse de novo no mesmo item (sem linha
#: nova) passaria pela contagem de linhas. `distinct_devices` entra no mesmo `UPDATE` que os outros três.
_SOMAS_DOS_ITENS = ("evidence_for", "evidence_against", "distinct_runs", "distinct_devices")


def _contagens(st: AppState, run_id: str) -> dict[str, int]:
    contagens = {t: int(st.db.scalar(f"SELECT COUNT(*) FROM {t} WHERE run_id=?", (run_id,)) or 0)
                 for t in _POR_EXECUCAO}
    contagens["learning_items"] = int(st.db.scalar(
        f"SELECT COUNT(*) FROM learning_items WHERE id IN ({_ITENS_DA_EXECUCAO})", (run_id, run_id)) or 0)
    contagens["learning_reviews"] = int(st.db.scalar(
        f"SELECT COUNT(*) FROM learning_reviews WHERE item_ref IN ({_ITENS_DA_EXECUCAO})", (run_id, run_id)) or 0)
    for coluna in _SOMAS_DOS_ITENS:
        contagens[f"SUM(learning_items.{coluna})"] = int(st.db.scalar(
            f"SELECT SUM({coluna}) FROM learning_items WHERE id IN ({_ITENS_DA_EXECUCAO})", (run_id, run_id)) or 0)
    return contagens


async def _execucao(h: Harness, caso: str) -> str:
    """A execução do caso, já no estado final. `cancelada_na_espera`: parou esperando a pessoa e foi cancelada, e o
    gancho de saída (#382) já a digeriu uma vez."""
    st = _estado(h)
    if caso == "concluida":
        h.cfg.file.ai.recipes = "replay"
        run_id = str((await h.wait_run(h.run(["android-01"]).id)).id)
        # T1: o assentamento em linha do worker também encadeia digest; sem esperar, ele cai entre o antes e o depois
        await h.wait(lambda: not st._digestoes, what="digests do assentamento")   # noqa: SLF001
        return run_id
    run_id = await _esperando_login(h)
    st.runs.cancel(run_id)
    await h.wait(lambda: st.repo.run_row(run_id)["status"] == "cancelled", what="execução cancelada")
    await h.wait(lambda: not st._digestoes, what="digests do gancho")   # noqa: SLF001 - o que o gancho encadeou
    return run_id


@pytest.mark.parametrize("caso", ["concluida", "cancelada_na_espera"])
async def test_o_digest_rodado_de_novo_na_mesma_execucao_nao_duplica_nada(harness: Harness, caso: str) -> None:
    """A execução que sai da espera é digerida na saída, e a que a migração 111 moveu de `completed_with_issues` para
    `awaiting_person` já tinha sido digerida antes. O segundo digest da MESMA execução não pode somar linha em nenhuma
    tabela do aprendizado (a leitura dos 10 mineradores na nota de desenho do 30.69). É a segunda rede contra o
    assentamento em dobro: o digest lê a execução só pelo `run_id` e pelas linhas dela, então vale para qualquer caminho
    que o chame duas vezes, inclusive o da execução `cancelled`."""
    st = _estado(harness)
    run_id = await _execucao(harness, caso)
    st.learning.digerir_execucao(run_id)
    antes = _contagens(st, run_id)
    assert any(antes.values()), antes      # a medida por execução não pode passar de vazio: o digest gravou algo dela
    relatorio = st.learning.digerir_execucao(run_id)
    assert not relatorio.pulado and not relatorio.falhas, relatorio
    assert _contagens(st, run_id) == antes
