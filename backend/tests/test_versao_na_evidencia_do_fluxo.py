"""30.74: a evidência do fluxo leva a versão do app observada no aparelho da execução.

A medida do 30.72 (05/10, banco do central, só leitura): as 113 evidências reais de fluxo tinham `app_version` nulo; as
de receita, quase todas, a tinham (`recipes.app_version`). O parecer do curador dizia "sem versão do app registrada" e
pedia `reproducao_na_versao_viva`, que nenhuma prova satisfazia. Agora o digest lê a versão do app do fluxo (o
principal, `flows.app_id` → `apps.package`) em `device_app_state` do aparelho da execução, nos três caminhos que gravam
evidência de fluxo: a sombra (`run:`), a prova (`run:`, 30.37) e o uso (`uso:`, 30.51).

V1 da leitura: a observação é a da hora do DIGEST. A versão só vale se a última atualização do app no aparelho
(`last_update_time`, no fuso do aparelho) foi com certeza antes do início da execução; na dúvida, `None`.

Prova `simulated`: o Harness (aparelho falso, porta 5640) e o banco migrado pela fábrica da suíte.
"""
from __future__ import annotations

import pytest

from app.modules.learning.infrastructure.ligar_nativos import LeituraSql, versao_estavel_na_execucao
from app.state import AppState

from .conftest import Harness

VERSAO = "9.8.7"
#: Uma atualização bem antiga, no formato do `dumpsys` (fuso do aparelho, sem fuso escrito).
ATUALIZADO_HA_MUITO = "2026-01-01 00:00:00"


def _estado(h: Harness) -> AppState:
    assert h.state is not None
    return h.state


def _pacote_do_fluxo(st: AppState, fluxo_id: str) -> str:
    pacote = st.db.scalar("SELECT a.package FROM flows f JOIN apps a ON a.id = f.app_id WHERE f.id=?", (fluxo_id,))
    assert pacote, "o fluxo do Harness precisa de app com pacote"
    return str(pacote)


def _observar(st: AppState, aparelho: str, pacote: str, versao: str | None,
              atualizado: str | None = ATUALIZADO_HA_MUITO) -> None:
    st.db.execute("DELETE FROM device_app_state WHERE instance_id=? AND package_name=?", (aparelho, pacote))
    st.db.execute("INSERT INTO device_app_state(instance_id, package_name, observed_version_name, last_update_time,"
                  " state) VALUES (?, ?, ?, ?, 'ready')", (aparelho, pacote, versao, atualizado))


async def test_a_evidencia_do_fluxo_leva_a_versao_do_aparelho_da_execucao(harness: Harness) -> None:
    """A 2ª execução do mesmo comando, num aparelho com a versão observada, deixa a evidência com essa versão. A leitura
    é do aparelho DA execução: o outro aparelho, sem versão observada, segue nulo."""
    st = _estado(harness)
    harness.cfg.file.ai.flows = True
    harness.cfg.file.aprendizado.fluxo.com_prova = True
    primeira = await harness.wait_run(harness.run(["android-01"]).id)
    assert primeira.status == "completed"
    [fluxo] = st.db.query("SELECT * FROM flows")
    fluxo_id = str(fluxo["id"])
    pacote = _pacote_do_fluxo(st, fluxo_id)
    await harness.wait(lambda: not st._digestoes, 10, "digest encerrado")  # noqa: SLF001
    _observar(st, "android-02", pacote, VERSAO)
    st.db.execute("DELETE FROM device_app_state WHERE instance_id='android-01' AND package_name=?", (pacote,))

    segunda = await harness.wait_run(harness.run(["android-02"]).id)
    assert segunda.status == "completed"
    await harness.wait(lambda: not st._digestoes, 10, "digest encerrado")  # noqa: SLF001
    versoes = {str(r["run_id"]): r["app_version"] for r in st.db.query(
        "SELECT run_id, app_version FROM learning_evidence WHERE item_ref=?", (f"fluxo:{fluxo_id}",))}
    assert versoes.get(segunda.id) == VERSAO, versoes


async def test_a_leitura_da_versao_do_fluxo_no_aparelho(harness: Harness) -> None:
    """`LeituraSql.versao_do_fluxo_no_aparelho`: a versão do pacote do fluxo naquele aparelho; vazia, ausente, sem
    aparelho ou fluxo desconhecido dão `None`."""
    st = _estado(harness)
    harness.cfg.file.ai.flows = True
    run_id = (await harness.wait_run(harness.run(["android-01"]).id)).id
    [fluxo] = st.db.query("SELECT * FROM flows")
    fluxo_id = str(fluxo["id"])
    pacote = _pacote_do_fluxo(st, fluxo_id)
    leitura = LeituraSql(st.db)

    _observar(st, "android-03", pacote, VERSAO)
    assert leitura.versao_do_fluxo_no_aparelho(fluxo_id, "android-03", run_id) == VERSAO
    _observar(st, "android-03", pacote, "")
    assert leitura.versao_do_fluxo_no_aparelho(fluxo_id, "android-03", run_id) is None
    _observar(st, "android-03", pacote, None)
    assert leitura.versao_do_fluxo_no_aparelho(fluxo_id, "android-03", run_id) is None
    assert leitura.versao_do_fluxo_no_aparelho(fluxo_id, None, run_id) is None
    assert leitura.versao_do_fluxo_no_aparelho("fluxo-que-nao-existe", "android-03", run_id) is None
    assert leitura.versao_do_fluxo_no_aparelho(fluxo_id, "android-03", "execucao-que-nao-existe") is None
    # V1: o app atualizado DEPOIS do início da execução (a leitura é a do digest) não dá a versão nova a ela
    inicio = str(st.db.scalar("SELECT started_at FROM runs WHERE id=?", (run_id,)))
    depois = inicio[:10] + " " + inicio[11:19]            # a mesma hora, no formato do `dumpsys`: pode ser posterior
    _observar(st, "android-03", pacote, VERSAO, atualizado=depois)
    assert leitura.versao_do_fluxo_no_aparelho(fluxo_id, "android-03", run_id) is None
    _observar(st, "android-03", pacote, VERSAO, atualizado=None)       # sem a hora da atualização: na dúvida, nada
    assert leitura.versao_do_fluxo_no_aparelho(fluxo_id, "android-03", run_id) is None


@pytest.mark.parametrize(("atualizado", "inicio", "esperado"), [
    ("2026-10-05 08:00:00", "2026-10-05T20:00:01.000Z", VERSAO),   # 12 h e 1 s antes: certo em qualquer fuso
    ("2026-10-05 08:00:00", "2026-10-05T20:00:00.000Z", None),     # na fronteira: no fuso −12 seria o mesmo instante
    ("2026-10-05 08:00:00", "2026-10-05T11:00:00.000Z", None),     # 3 h depois na leitura: no fuso −3 seria o início
    ("2026-10-05 08:00:00", "2026-10-05T07:00:00.000Z", None),     # a leitura é posterior ao início
    ("ontem", "2026-10-05T20:00:00.000Z", None),                  # ilegível: na dúvida, nada
    ("2026-10-05 08:00:00", None, None),                          # a execução não começou
    (None, "2026-10-05T20:00:00.000Z", None),
])
def test_a_versao_so_vale_se_o_app_nao_mudou_depois_do_inicio(atualizado: str | None, inicio: str | None,
                                                            esperado: str | None) -> None:
    assert versao_estavel_na_execucao(VERSAO, atualizado, inicio) == esperado
    assert versao_estavel_na_execucao(None, atualizado, inicio) is None
