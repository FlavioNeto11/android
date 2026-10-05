"""30.74: a evidência do fluxo leva a versão do app observada no aparelho da execução.

A medida do 30.72 (05/10, banco do central, só leitura): as 113 evidências reais de fluxo tinham `app_version` nulo; as
de receita, quase todas, a tinham (`recipes.app_version`). O parecer do curador dizia "sem versão do app registrada" e
pedia `reproducao_na_versao_viva`, que nenhuma prova satisfazia. Agora o digest lê a versão do app do fluxo (o
principal, `flows.app_id` → `apps.package`) em `device_app_state` do aparelho da execução, nos três caminhos que gravam
evidência de fluxo: a sombra (`run:`), a prova (`run:`, 30.37) e o uso (`uso:`, 30.51).

Prova `simulated`: o Harness (aparelho falso, porta 5640) e o banco migrado pela fábrica da suíte.
"""
from __future__ import annotations

from app.modules.learning.infrastructure.ligar_nativos import LeituraSql
from app.state import AppState

from .conftest import Harness

VERSAO = "9.8.7"


def _estado(h: Harness) -> AppState:
    assert h.state is not None
    return h.state


def _pacote_do_fluxo(st: AppState, fluxo_id: str) -> str:
    pacote = st.db.scalar("SELECT a.package FROM flows f JOIN apps a ON a.id = f.app_id WHERE f.id=?", (fluxo_id,))
    assert pacote, "o fluxo do Harness precisa de app com pacote"
    return str(pacote)


def _observar(st: AppState, aparelho: str, pacote: str, versao: str | None) -> None:
    st.db.execute("DELETE FROM device_app_state WHERE instance_id=? AND package_name=?", (aparelho, pacote))
    st.db.execute("INSERT INTO device_app_state(instance_id, package_name, observed_version_name, state)"
                  " VALUES (?, ?, ?, 'ready')", (aparelho, pacote, versao))


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
    await harness.wait_run(harness.run(["android-01"]).id)
    [fluxo] = st.db.query("SELECT * FROM flows")
    fluxo_id = str(fluxo["id"])
    pacote = _pacote_do_fluxo(st, fluxo_id)
    leitura = LeituraSql(st.db)

    _observar(st, "android-03", pacote, VERSAO)
    assert leitura.versao_do_fluxo_no_aparelho(fluxo_id, "android-03") == VERSAO
    _observar(st, "android-03", pacote, "")
    assert leitura.versao_do_fluxo_no_aparelho(fluxo_id, "android-03") is None
    _observar(st, "android-03", pacote, None)
    assert leitura.versao_do_fluxo_no_aparelho(fluxo_id, "android-03") is None
    assert leitura.versao_do_fluxo_no_aparelho(fluxo_id, None) is None
    assert leitura.versao_do_fluxo_no_aparelho("fluxo-que-nao-existe", "android-03") is None
