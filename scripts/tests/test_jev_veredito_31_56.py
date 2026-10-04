"""31.56: o veredito mecânico do A/B da barra tapada (`scripts/jev-veredito-31-56.py`).

Confere, antes de qualquer execução, que o critério pré-registrado decide sozinho:

- sucesso igual e não mais de 20 % de decisões por etapa a mais → `liga`;
- decisões demais ou sucesso pior → `nao_liga`;
- braço vazio ou execução rodando → `inconclusivo`.

Confere também que o script só lê, ignora chaves fora do padrão, conta o custo pela regra do `spent_usd` e não leva
texto à saída.

Nível de prova: `simulated` (banco SQLite montado; nenhum aparelho, nenhuma IA).
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

_spec = importlib.util.spec_from_file_location("jev_veredito_31_56", ROOT / "scripts" / "jev-veredito-31-56.py")
ver = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ver
_spec.loader.exec_module(ver)  # type: ignore[union-attr]

SEGREDO = "https://contas.exemplo/reset?token=nao-pode-vazar"


class _Banco:
    def __init__(self, caminho: Path) -> None:
        self.caminho = caminho
        c = sqlite3.connect(caminho)
        c.executescript(
            "CREATE TABLE runs (id TEXT PRIMARY KEY, idempotency_key TEXT, command TEXT, status TEXT);"
            "CREATE TABLE steps (id TEXT PRIMARY KEY, run_id TEXT, started_at TEXT, finished_at TEXT);"
            "CREATE TABLE attempts (id TEXT PRIMARY KEY, step_id TEXT);"
            "CREATE TABLE ai_calls (id INTEGER PRIMARY KEY, run_id TEXT, role TEXT, model TEXT, ts TEXT, provider TEXT,"
            " origem TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read INTEGER, cache_write INTEGER,"
            " cache_write_1h INTEGER, usd REAL);")
        c.commit()
        c.close()
        self.n = 0

    def execucao(self, chave: str, *, status: str = "completed", etapas: int = 2, decisoes: int = 4,
                 etapa_ms: int = 1000) -> str:
        self.n += 1
        rid = f"r-{self.n:03d}"
        c = sqlite3.connect(self.caminho)
        c.execute("INSERT INTO runs VALUES (?,?,?,?)", (rid, chave, f"abra {SEGREDO}", status))
        for i in range(etapas):
            sid = f"{rid}:s{i}"
            fim = f"2026-10-05T10:00:0{i}.{etapa_ms % 1000:03d}Z" if etapa_ms < 1000 else f"2026-10-05T10:00:0{i + 1}.000Z"
            c.execute("INSERT INTO steps VALUES (?,?,?,?)", (sid, rid, f"2026-10-05T10:00:0{i}.000Z", fim))
            c.execute("INSERT INTO attempts VALUES (?,?)", (f"{sid}:a1", sid))
        c.execute("INSERT INTO steps VALUES (?,?,?,?)", (f"{rid}:nao-rodou", rid, None, None))   # sem tentativa: fora
        for _ in range(decisoes):
            c.execute("INSERT INTO ai_calls(run_id, role, model, ts, provider, input_tokens, output_tokens, cache_read,"
                      " cache_write, usd) VALUES (?, 'decide', 'modelo-x', '2026-10-05T10:00:00Z', 'anthropic', 100, 10,"
                      " 0, 0, 0.01)", (rid,))
        c.execute("INSERT INTO ai_calls(run_id, role, model, ts, provider, input_tokens, output_tokens, cache_read,"
                  " cache_write, usd) VALUES (?, 'verify', 'modelo-x', '2026-10-05T10:00:00Z', 'anthropic', 50, 5, 0, 0,"
                  " 0.005)", (rid,))
        c.commit()
        c.close()
        return rid

    def rodar(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
        r = ver.main(["--db", str(self.caminho), "--config", str(tmp_path / "nao-existe.yaml")])
        saida = capsys.readouterr().out
        assert SEGREDO not in saida and "abra" not in saida
        assert json.loads(saida) == json.loads(json.dumps(r))
        return r


@pytest.fixture
def banco(tmp_path: Path) -> _Banco:
    return _Banco(tmp_path / "poc.sqlite3")


def test_criterio_pre_registrado_no_codigo() -> None:
    assert ver.CRITERIO["decisoes_por_etapa_a_mais_no_maximo"] == 0.20
    assert "antes de qualquer execução" in ver.CRITERIO["pre_registrado"]


def test_sucesso_igual_e_decisoes_iguais_liga(banco: _Banco, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for braco in ("off", "on"):
        for par in ("01", "02", "03"):
            banco.execucao(f"lote:jev:31.56-{braco}-{par}")
    r = banco.rodar(tmp_path, capsys)
    assert r["veredito"] == "liga"
    assert r["desligado"]["decisoes_por_etapa"] == r["ligado"]["decisoes_por_etapa"] == 2.0     # 4 decides ÷ 2 etapas
    assert r["desligado"]["usd"] == pytest.approx(3 * (4 * 0.01 + 0.005))                       # o `usd` declarado
    linha = r["execucoes"][0]
    assert linha["etapas"] == 2 and linha["tokens"] == 4 * 110 + 55 and linha["tokens_do_ator"] == 4 * 110
    assert linha["latencia_por_etapa_ms"]["p50"] == 1000.0


def test_decisoes_demais_no_ligado_nao_liga(banco: _Banco, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco.execucao("lote:jev:31.56-off-01", decisoes=10)
    banco.execucao("lote:jev:31.56-on-01", decisoes=13)          # 6,5 contra 5 por etapa: +30 %
    r = banco.rodar(tmp_path, capsys)
    assert r["veredito"] == "nao_liga" and r["decisoes_por_etapa_a_mais"] == 0.3
    assert r["porque"] == {"sucesso_igual_ou_maior": True, "decisoes_dentro_do_limite": False}


def test_vinte_por_cento_exatos_ainda_liga(banco: _Banco, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco.execucao("lote:jev:31.56-off-01", decisoes=10)
    banco.execucao("lote:jev:31.56-on-01", decisoes=12)
    assert banco.rodar(tmp_path, capsys)["veredito"] == "liga"


def test_sucesso_pior_no_ligado_nao_liga(banco: _Banco, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco.execucao("lote:jev:31.56-off-01")
    banco.execucao("lote:jev:31.56-off-02")
    banco.execucao("lote:jev:31.56-on-01")
    banco.execucao("lote:jev:31.56-on-02", status="completed_with_issues")
    r = banco.rodar(tmp_path, capsys)
    assert r["veredito"] == "nao_liga" and r["porque"]["sucesso_igual_ou_maior"] is False


def test_braco_vazio_ou_execucao_rodando_e_inconclusivo(banco: _Banco, tmp_path: Path,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    banco.execucao("lote:jev:31.56-off-01")
    assert banco.rodar(tmp_path, capsys)["veredito"] == "inconclusivo"
    banco.execucao("lote:jev:31.56-on-01", status="running")
    assert banco.rodar(tmp_path, capsys)["veredito"] == "inconclusivo"


def test_chave_fora_do_padrao_nao_entra(banco: _Banco, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco.execucao("lote:jev:31.56-off-01")
    banco.execucao("lote:jev:31.56-on-01")
    banco.execucao("lote:jev:31.56-on-1", decisoes=99)            # sem os dois dígitos
    banco.execucao("lote:jev:31.56-talvez-01", decisoes=99)
    r = banco.rodar(tmp_path, capsys)
    assert [ln["par"] for ln in r["execucoes"]] == ["01", "01"] and r["veredito"] == "liga"


def test_so_le_o_banco(banco: _Banco, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco.execucao("lote:jev:31.56-off-01")
    antes = banco.caminho.read_bytes()
    banco.rodar(tmp_path, capsys)
    assert banco.caminho.read_bytes() == antes
    with pytest.raises(sqlite3.OperationalError):
        ver.abrir(tmp_path / "nao-existe.sqlite3").execute("SELECT 1 FROM runs")
