"""30.77: a reprodução na versão viva não nasce sem versão (migração 118, `device_app_state.last_update_offset`).

Da leitura do 30.74: o `lastUpdateTime` do `dumpsys` vem no fuso do aparelho, sem fuso escrito, e a regra contava o
pior caso, a lida +12 h. Medido em 05/10: os 12 emuladores estão em America/Sao_Paulo (-0300), e de 3 a 12 h depois de
cada atualização a prova gravava evidência sem versão à toa. Agora a inspeção lê `date +%z` no mesmo shell do `dumpsys`
e guarda o fuso; com ele, a hora em UTC é exata e sobra 1 h de margem. Sem ele (linha antiga, leitura que não o trouxe),
segue a folga de 12 h.

Nível de prova: `simulated` (saída do `dumpsys` falsa, banco de teste). PostgreSQL pela fábrica quando
`TEST_DATABASE_URL` existe.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.db import MIGRATIONS_DIR as MIGRACOES
from app.devices.adb import Adb
from app.modules.learning.infrastructure.ligar_nativos import LeituraSql, versao_estavel_na_execucao

from .conftest import Harness
from .test_db import _banco
from .test_versao_na_evidencia_do_fluxo import VERSAO, _estado, _pacote_do_fluxo

NOME = "118_fuso_da_ultima_atualizacao"
DUMPSYS = ("Packages:\n  Package [com.pocqa.messenger] (abc):\n    versionCode=42 minSdk=24 targetSdk=34\n"
           "    versionName=9.8.7\n    firstInstallTime=2026-09-26 18:41:01\n"
           "    lastUpdateTime=2026-10-05 10:00:00\n")


class _Saida:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def _adb(saida: str) -> tuple[Adb, list[list[str]]]:
    adb = Adb.__new__(Adb)
    chamadas: list[list[str]] = []

    def _run(args: list[str], **_: Any) -> _Saida:
        chamadas.append(args)
        return _Saida(saida)

    adb._run = _run                                     # type: ignore[method-assign]  # noqa: SLF001
    adb.pm_path = lambda _pacote: ["/data/app/base.apk"]  # type: ignore[method-assign]
    return adb, chamadas


# ------------------------------------------------------------------ a leitura no aparelho
def test_a_inspecao_le_o_fuso_no_mesmo_shell_do_dumpsys() -> None:
    adb, chamadas = _adb(DUMPSYS + "FUSO_DO_APARELHO=-0300\n")
    info = adb.package_info("com.pocqa.messenger")
    assert info is not None
    assert (info["last_update_time"], info["last_update_offset"]) == ("2026-10-05 10:00:00", "-0300")
    [args] = chamadas
    assert args[0] == "shell" and "dumpsys package com.pocqa.messenger" in args[1] and "date +%z" in args[1]


@pytest.mark.parametrize("cauda", ["", "FUSO_DO_APARELHO=\n", "FUSO_DO_APARELHO=BRT\n"])
def test_sem_fuso_legivel_a_leitura_segue_sem_ele(cauda: str) -> None:
    """A saída sem a linha do fuso (ou com ela vazia ou ilegível) dá `None`: a regra cai na folga de 12 h."""
    adb, _ = _adb(DUMPSYS + cauda)
    info = adb.package_info("com.pocqa.messenger")
    assert info is not None and info["last_update_offset"] is None and info["version_name"] == "9.8.7"


# ------------------------------------------------------------------ a regra
@pytest.mark.parametrize(("inicio", "fuso", "esperado"), [
    ("2026-10-05T14:30:00.000Z", "-0300", VERSAO),    # 10:00 em -0300 = 13:00Z; +1 h = 14:00Z, antes do início
    ("2026-10-05T13:30:00.000Z", "-0300", None),      # dentro da margem de 1 h: na dúvida, nada
    ("2026-10-05T14:30:00.000Z", None, None),         # sem fuso: a folga de 12 h (até 22:00Z) segura a versão
    ("2026-10-05T14:30:00.000Z", "", None),
    ("2026-10-05T14:30:00.000Z", "BRT", None),        # ilegível: folga
    ("2026-10-05T14:30:00.000Z", "-1300", None),      # fora de -12..+14 h: folga
    ("2026-10-05T14:30:00.000Z", "+0375", None),      # minutos impossíveis: folga
    ("2026-10-05T22:00:01.000Z", "BRT", VERSAO),      # a folga de 12 h segue valendo sem fuso legível
    ("2026-10-05T09:30:00.000Z", "+0900", VERSAO),    # 10:00 em +0900 = 01:00Z; +1 h = 02:00Z
])
def test_com_o_fuso_a_hora_e_exata_e_a_margem_e_1_h(inicio: str, fuso: str | None, esperado: str | None) -> None:
    assert versao_estavel_na_execucao(VERSAO, "2026-10-05 10:00:00", inicio, fuso) == esperado


# ------------------------------------------------------------------ o banco
def test_migracao_118_coluna_nulavel_e_impressao(tmp_path: Path) -> None:
    banco = _banco(tmp_path)
    try:
        banco.migrate()
        assert "last_update_offset" in set(banco.columns("device_app_state"))
        for aparelho, fuso in (("a-1", "-0300"), ("a-2", None)):
            banco.execute("INSERT INTO device_app_state(instance_id, package_name, state, last_update_offset)"
                          " VALUES (?,?,?,?)", (aparelho, "com.x", "ready", fuso))
        lidos = {str(r["instance_id"]): r["last_update_offset"]
                 for r in banco.query("SELECT instance_id, last_update_offset FROM device_app_state")}
        assert lidos == {"a-1": "-0300", "a-2": None}
        linha = banco.one("SELECT checksum FROM schema_migrations WHERE version=?", (NOME,))
        assert linha is not None
        texto = (MIGRACOES / f"{NOME}.sql").read_text(encoding="utf-8")
        assert linha["checksum"] == banco._impressao(banco.render(texto))   # noqa: SLF001
    finally:
        banco.close()


async def test_a_leitura_do_fluxo_usa_o_fuso_gravado_e_a_linha_sem_fuso_cai_na_folga(harness: Harness) -> None:
    """`LeituraSql.versao_do_fluxo_no_aparelho` com o fuso gravado pela inspeção. A linha sem fuso (antiga, ou de uma
    leitura que não o trouxe) segue a folga de 12 h, como no 30.74."""
    st = _estado(harness)
    harness.cfg.file.ai.flows = True
    run_id = (await harness.wait_run(harness.run(["android-01"]).id)).id
    [fluxo] = st.db.query("SELECT * FROM flows")
    fluxo_id = str(fluxo["id"])
    pacote = _pacote_do_fluxo(st, fluxo_id)
    st.db.execute("UPDATE runs SET started_at=? WHERE id=?", ("2026-10-05T14:30:00.000Z", run_id))
    leitura = LeituraSql(st.db)

    def observar(fuso: str | None) -> None:
        st.db.execute("DELETE FROM device_app_state WHERE instance_id='android-03' AND package_name=?", (pacote,))
        st.db.execute("INSERT INTO device_app_state(instance_id, package_name, observed_version_name, last_update_time,"
                      " last_update_offset, state) VALUES ('android-03', ?, ?, ?, ?, 'ready')",
                      (pacote, VERSAO, "2026-10-05 10:00:00", fuso))

    observar("-0300")
    assert leitura.versao_do_fluxo_no_aparelho(fluxo_id, "android-03", run_id) == VERSAO
    observar(None)
    assert leitura.versao_do_fluxo_no_aparelho(fluxo_id, "android-03", run_id) is None
