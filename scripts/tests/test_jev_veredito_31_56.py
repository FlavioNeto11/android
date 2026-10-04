"""31.56: o veredito mecânico do A/B da barra tapada (`scripts/jev-veredito-31-56.py`).

Confere, antes de qualquer execução, que o critério pré-registrado decide sozinho:

- sucesso igual e não mais de 20 % de decisões por etapa a mais → `liga`, com os 20 % exatos pela razão crua (V2);
- decisões demais, sucesso pior, cancelada, ou desligado em 0 decisão e ligado acima (V1) → `nao_liga`;
- menos de 3 pares completos ou execução rodando → `inconclusivo`; par incompleto fica fora e é listado (V3).

Confere também que o script só lê, conta as chaves fora do padrão, diz de onde vieram os preços, conta o custo pela
regra do `spent_usd`, avisa o banco que não existe e não leva texto à saída.

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
        assert etapas <= 9, "a hora da etapa usa um dígito"
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

    def pares(self, n: int = 3, *, off: dict[str, Any] | None = None, on: dict[str, Any] | None = None) -> None:
        """`n` pares completos, com os mesmos argumentos em cada execução do braço."""
        for i in range(1, n + 1):
            self.execucao(f"lote:jev:31.56-off-{i:02d}", **(off or {}))
            self.execucao(f"lote:jev:31.56-on-{i:02d}", **(on or {}))

    def rodar(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], config: Path | None = None) -> dict[str, Any]:
        r = ver.main(["--db", str(self.caminho), "--config", str(config or tmp_path / "nao-existe.yaml")])
        saida = capsys.readouterr().out
        assert SEGREDO not in saida and "abra" not in saida
        assert json.loads(saida) == json.loads(json.dumps(r))
        return r


@pytest.fixture
def banco(tmp_path: Path) -> _Banco:
    return _Banco(tmp_path / "poc.sqlite3")


def test_criterio_pre_registrado_no_codigo() -> None:
    assert ver.CRITERIO["decisoes_por_etapa_a_mais_no_maximo"] == 0.20
    assert ver.CRITERIO["minimo_de_pares_completos"] == 3
    assert "antes de qualquer execução" in ver.CRITERIO["pre_registrado"]
    assert "insucesso" in ver.CRITERIO["cancelada"] and "somas cruas" in ver.CRITERIO["razao"]
    assert ver.TERMINAIS == {"completed", "completed_with_issues", "failed", "cancelled"}     # o RUN_TERMINAL do backend


def test_sucesso_igual_e_decisoes_iguais_liga(banco: _Banco, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco.pares(3)
    r = banco.rodar(tmp_path, capsys)
    assert r["veredito"] == "liga" and r["pares_completos"] == ["01", "02", "03"] and r["pares_incompletos"] == []
    assert r["desligado"]["decisoes_por_etapa"] == r["ligado"]["decisoes_por_etapa"] == 2.0     # 4 decides ÷ 2 etapas
    assert r["desligado"]["usd"] == pytest.approx(3 * (4 * 0.01 + 0.005))                       # o `usd` declarado
    assert r["precos"] == "padrao" and r["chaves_ignoradas"] == 0
    linha = r["execucoes"][0]
    assert linha["etapas"] == 2 and linha["tokens"] == 4 * 110 + 55 and linha["tokens_do_ator"] == 4 * 110
    assert linha["latencia_por_etapa_ms"]["p50"] == 1000.0


def test_decisoes_demais_no_ligado_nao_liga(banco: _Banco, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco.pares(3, off={"decisoes": 10}, on={"decisoes": 13})          # 6,5 contra 5 por etapa: +30 %
    r = banco.rodar(tmp_path, capsys)
    assert r["veredito"] == "nao_liga" and r["decisoes_por_etapa_a_mais"] == 0.3
    assert r["porque"] == {"sucesso_igual_ou_maior": True, "decisoes_dentro_do_limite": False}


def test_vinte_por_cento_exatos_ainda_liga(banco: _Banco, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco.pares(3, off={"decisoes": 10}, on={"decisoes": 12})
    assert banco.rodar(tmp_path, capsys)["veredito"] == "liga"


def test_v2_vinte_por_cento_exatos_com_denominador_tres_liga(banco: _Banco, tmp_path: Path,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    """1/3 contra 2/5 é +20 % exato; com a média arredondada a 3 casas antes da razão (0,333) dava +20,1 %."""
    banco.pares(3, off={"etapas": 3, "decisoes": 1}, on={"etapas": 5, "decisoes": 2})
    r = banco.rodar(tmp_path, capsys)
    assert r["veredito"] == "liga" and r["decisoes_por_etapa_a_mais"] == 0.2


def test_v1_desligado_em_zero_e_ligado_acima_nao_liga(banco: _Banco, tmp_path: Path,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    banco.pares(3, off={"decisoes": 0}, on={"decisoes": 4})
    r = banco.rodar(tmp_path, capsys)
    assert r["veredito"] == "nao_liga" and r["decisoes_por_etapa_a_mais"] is None
    assert r["porque"]["decisoes_dentro_do_limite"] is False


def test_v1_os_dois_em_zero_e_igual(banco: _Banco, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco.pares(3, off={"decisoes": 0}, on={"decisoes": 0})
    r = banco.rodar(tmp_path, capsys)
    assert r["veredito"] == "liga" and r["decisoes_por_etapa_a_mais"] == 0.0


def test_sucesso_pior_no_ligado_nao_liga(banco: _Banco, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco.pares(2)
    banco.execucao("lote:jev:31.56-off-03")
    banco.execucao("lote:jev:31.56-on-03", status="completed_with_issues")
    r = banco.rodar(tmp_path, capsys)
    assert r["veredito"] == "nao_liga" and r["porque"]["sucesso_igual_ou_maior"] is False


def test_cancelada_conta_como_insucesso_do_braco(banco: _Banco, tmp_path: Path,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    banco.pares(2)
    banco.execucao("lote:jev:31.56-off-03")
    banco.execucao("lote:jev:31.56-on-03", status="cancelled")        # o teto de US$ 0,25, ou à mão
    r = banco.rodar(tmp_path, capsys)
    assert r["veredito"] == "nao_liga" and r["ligado"]["sucessos"] == 2
    assert r["porque"]["sucesso_igual_ou_maior"] is False


def test_menos_de_tres_pares_ou_execucao_rodando_e_inconclusivo(banco: _Banco, tmp_path: Path,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    banco.pares(2)
    assert banco.rodar(tmp_path, capsys)["veredito"] == "inconclusivo"
    banco.execucao("lote:jev:31.56-off-03")
    banco.execucao("lote:jev:31.56-on-03", status="running")
    r = banco.rodar(tmp_path, capsys)
    assert r["veredito"] == "inconclusivo" and r["pares_completos"] == ["01", "02", "03"]


def test_v3_par_incompleto_fica_fora_e_e_listado(banco: _Banco, tmp_path: Path,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    """O on-04 que não rodou (ou a chave repetida, que devolve a execução original) deixa o par 04 desigual: o off-04,
    mesmo com insucesso, não pesa no braço."""
    banco.pares(3)
    banco.execucao("lote:jev:31.56-off-04", status="failed", decisoes=99)
    r = banco.rodar(tmp_path, capsys)
    assert r["pares_incompletos"] == [{"par": "04", "off": 1, "on": 0}]
    assert r["veredito"] == "liga" and r["desligado"]["execucoes"] == 3 and len(r["execucoes"]) == 7


def test_chave_fora_do_padrao_nao_entra_e_e_contada(banco: _Banco, tmp_path: Path,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    banco.pares(3)
    banco.execucao("lote:jev:31.56-on-1", decisoes=99)            # sem os dois dígitos
    banco.execucao("lote:jev:31.56-talvez-01", decisoes=99)
    r = banco.rodar(tmp_path, capsys)
    assert len(r["execucoes"]) == 6 and r["chaves_ignoradas"] == 2 and r["veredito"] == "liga"


def test_precos_do_config_quando_ele_existe(banco: _Banco, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco.pares(3)
    c = sqlite3.connect(banco.caminho)              # uma chamada sem `usd` declarado: o preço do modelo é que conta
    c.execute("INSERT INTO ai_calls(run_id, role, model, ts, provider, input_tokens, output_tokens, cache_read,"
              " cache_write, usd) SELECT id, 'verify', 'modelo-x', '2026-10-05T10:00:00Z', 'anthropic', 1000, 0, 0, 0,"
              " NULL FROM runs")
    c.commit()
    c.close()
    padrao = banco.rodar(tmp_path, capsys)
    config = tmp_path / "config.yaml"
    config.write_text("ai:\n  prices:\n    modelo-x: [1000.0, 0.0, 0.0, 0.0]\n", encoding="utf-8")
    r = banco.rodar(tmp_path, capsys, config=config)
    assert padrao["precos"] == "padrao" and r["precos"] == "config"
    assert r["desligado"]["usd"] != padrao["desligado"]["usd"]


def test_banco_inexistente_avisa_sem_traceback(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as saiu:
        ver.main(["--db", str(tmp_path / "nao-existe.sqlite3")])
    assert saiu.value.code == 2 and "banco não encontrado" in capsys.readouterr().err


def test_so_le_o_banco(banco: _Banco, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco.pares(3)
    antes = banco.caminho.read_bytes()
    banco.rodar(tmp_path, capsys)
    assert banco.caminho.read_bytes() == antes
    with pytest.raises(sqlite3.OperationalError):
        ver.abrir(tmp_path / "nao-existe.sqlite3").execute("SELECT 1 FROM runs")
