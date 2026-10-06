"""As amostras do host (31.180, adendo v1.102): a rota só leitura sobre o CSV do amostrador permanente (29.156).

O que se prova (CSV falso, relógio injetado na leitura; `simulated`):
- a janela de N horas atravessa a virada do dia (dois arquivos), em ordem de hora, sem o cabeçalho e sem linha fora dela;
- números vazios viram nulo; o topo de processos e os avisos de pressão viram listas; par malformado fica de fora;
- a linha de falha do amostrador (`ts,erro,<tipo>`) vira `{ts_utc, erro}` (o contrato do painel do Portal);
- sem CSV dos dias da janela: `None` (404 na rota); com arquivo e nada na janela: lista vazia;
- a rota: 200 com o contrato, 404 `sem_amostras`, 422 fora de 1 a 168 horas, e nenhum caminho nem nome de máquina.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.main import create_app
from app.modules.fleet.infrastructure.amostras_do_host import COLUNAS, ler_amostras, pasta_das_amostras

from .conftest import Harness

CABECALHO = ",".join(COLUNAS)
AGORA = datetime(2026, 10, 7, 0, 30, 0, tzinfo=UTC)


def _csv(pasta: Path, dia: str, linhas: list[str]) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / f"{dia}.csv").write_text("﻿" + "\n".join([CABECALHO, *linhas]) + "\n", encoding="utf-8")


def test_a_janela_atravessa_o_dia_e_cada_coluna_vira_dado(tmp_path: Path) -> None:
    _csv(tmp_path, "20261006", [
        "2026-10-06T21:00:00Z,99.0,1.00,100,1.0,1000,10.0,,",                     # fora da janela de 3 h
        "2026-10-06T23:59:00Z,41.5,2.25,5120,30.2,8192,120.5,python:12.3;pwsh:4.1,android-05:3;android-01:1",
        "2026-10-06T23:58:00Z,40.0,,5000,,8000,120.0,python:1.0;malformado,",
    ])
    _csv(tmp_path, "20261007", ["2026-10-07T00:01:00Z,erro,IOException", "2026-10-07T00:31:00Z,1,1,1,1,1,1,,"])
    amostras = ler_amostras(tmp_path, 3, AGORA)
    assert amostras is not None
    assert [a["ts_utc"] for a in amostras] == ["2026-10-06T23:58:00Z", "2026-10-06T23:59:00Z", "2026-10-07T00:01:00Z"]
    meio = amostras[1]
    assert (meio["cpu_host_pct"], meio["vm_convidado_nucleos"], meio["vmmem_ws_mb"], meio["qemu_host_pct"],
            meio["ram_livre_mb"], meio["disco_livre_gb"]) == (41.5, 2.25, 5120.0, 30.2, 8192.0, 120.5)
    assert "erro" not in meio
    assert meio["processos_top"] == [{"nome": "python", "pct": 12.3}, {"nome": "pwsh", "pct": 4.1}]
    assert meio["avisos_pressao"] == [{"instance_id": "android-05", "n": 3}, {"instance_id": "android-01", "n": 1}]
    primeira = amostras[0]
    assert primeira["vm_convidado_nucleos"] is None and primeira["qemu_host_pct"] is None
    assert primeira["processos_top"] == [{"nome": "python", "pct": 1.0}] and primeira["avisos_pressao"] == []
    falha = amostras[2]
    assert falha == {"ts_utc": "2026-10-07T00:01:00Z", "erro": "IOException"}


def test_sem_csv_e_none_e_com_csv_sem_linha_na_janela_e_vazio(tmp_path: Path) -> None:
    assert ler_amostras(tmp_path / "nao-existe", 24, AGORA) is None
    _csv(tmp_path, "20261001", ["2026-10-01T10:00:00Z,1,1,1,1,1,1,,"])
    assert ler_amostras(tmp_path, 24, AGORA) is None                  # o arquivo é de fora da janela
    _csv(tmp_path, "20261007", [])
    assert ler_amostras(tmp_path, 24, AGORA) == []


async def test_rota_das_amostras_do_host(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/host/amostras")
        assert r.status_code == 404 and r.json()["detail"]["code"] == "sem_amostras"
        agora = datetime.now(UTC)
        um_minuto = (agora - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pasta = pasta_das_amostras(st.cfg.data_dir)
        _csv(pasta, agora.strftime("%Y%m%d"), [f"{um_minuto},12.5,0.50,2048,3.0,4096,50.0,python:2.0,"])
        r = await c.get("/api/host/amostras", params={"horas": 2})
        assert r.status_code == 200
        corpo = r.json()
        assert list(corpo) == ["items"] and len(corpo["items"]) == 1
        assert corpo["items"][0]["cpu_host_pct"] == 12.5 and corpo["items"][0]["ts_utc"] == um_minuto
        assert str(st.cfg.data_dir) not in r.text and "observabilidade" not in r.text   # sem caminho
        for fora in (0, 169):
            assert (await c.get("/api/host/amostras", params={"horas": fora})).status_code == 422
