"""`scripts/jev-leitura-cache-latencia.py`: cache, entrada e latência antes × depois de um corte, num banco sintético.

Prova `simulated`: banco SQLite temporário migrado e linhas de `ai_calls` montadas aqui. Confere:
- o corte na comparação de TEXTO de `ts`;
- a janela simétrica;
- a entrada fresca contra a total;
- a latência só das chamadas ok;
- o perfil e a escalada;
- o RA-10 presente ou ausente (o mesmo teste vale antes e depois da migração 080 chegar à main);
- as simuladas fora;
- o custo por etapa (declarado ou tokens × preço);
- o banco aberto só para leitura, também na reconexão.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import Database  # noqa: E402

_spec = importlib.util.spec_from_file_location("jev_leitura_cache_latencia", ROOT / "scripts" / "jev-leitura-cache-latencia.py")
lcl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lcl)  # type: ignore[union-attr]

CORTE = datetime(2026, 10, 4, 12, tzinfo=UTC)
AGORA = datetime(2026, 10, 5, 12, tzinfo=UTC)          # depois = 24 h; antes padrão = as 24 h anteriores ao corte
ANTES = "2026-10-04T06:00:00.000Z"
DEPOIS = "2026-10-04T18:00:00.000Z"


class Banco:
    def __init__(self, tmp: Path) -> None:
        self.db = Database(tmp / "cache.sqlite3")
        self.db.migrate()
        self.db.execute("PRAGMA foreign_keys=OFF")   # dados sintéticos: chamada sem objetivo nem etapa cadastrados

    def colunas(self) -> set[str]:
        return {str(l["name"]) for l in self.db.query("PRAGMA table_info(ai_calls)")}

    def com_ra10(self) -> None:
        """As colunas da 080 SÓ se faltam: o mesmo teste vale antes e depois de a migração chegar à main."""
        for coluna in sorted(set(lcl.COLUNAS_RA10) - self.colunas()):
            self.db.execute(f"ALTER TABLE ai_calls ADD COLUMN {coluna} TEXT")

    def sem_ra10(self) -> None:
        for coluna in sorted(set(lcl.COLUNAS_RA10) & self.colunas()):
            self.db.execute(f"ALTER TABLE ai_calls DROP COLUMN {coluna}")

    def execucao(self, run_id: str, perfil: str | None = None) -> None:
        self.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at,"
                        " ai_profile, ai_profile_source) VALUES (?,?,?,?,?,?,?,?,?)",
                        (run_id, f"k-{run_id}", "c", "execute", "completed", "[]", "2026-10-04T00:00:00Z", perfil,
                         "explicit" if perfil else None))

    def chamada(self, ts: str, *, role: str = "decide", tier: int = 0, run_id: str | None = "r-1",
                step_id: str | None = "s-1", entrada: int = 100, lido: int = 0, escrito: int = 0, ms: int = 1000,
                ok: int = 1, usd: float | None = 0.01, provider: str = "anthropic", origem: str = "execucao",
                model: str = "claude-sonnet-5", **ra10: str) -> None:
        colunas = ["ts", "run_id", "step_id", "role", "model", "tier", "input_tokens", "cache_read", "cache_write",
                   "output_tokens", "with_image", "ms", "ok", "usd", "provider", "origem", *ra10]
        valores = [ts, run_id, step_id, role, model, tier, entrada, lido, escrito, 10, 0, ms, ok, usd, provider, origem,
                   *ra10.values()]
        self.db.execute(f"INSERT INTO ai_calls({', '.join(colunas)}) VALUES ({', '.join('?' * len(colunas))})",
                        tuple(valores))


@pytest.fixture
def banco(tmp_path: Path) -> Any:
    b = Banco(tmp_path)
    b.execucao("r-1")
    yield b
    b.db.close()


def _rel(banco: Banco, **kw: Any) -> dict[str, Any]:
    return lcl.montar(banco.db, corte=CORTE, fonte="teste", agora=AGORA, **kw)


def _grupo(rel: dict[str, Any], funcao: str, perfil: str) -> dict[str, Any]:
    return next(g for g in rel["grupos"] if g["funcao"] == funcao and g["perfil"] == perfil)


def test_corte_pela_comparacao_de_texto_e_janela_simetrica(banco: Banco) -> None:
    banco.chamada("2026-10-04T11:59:59.999Z")                   # 1 ms antes do corte
    banco.chamada("2026-10-04T12:00:00.000Z")                   # no corte: depois ("…:00Z" > "…:00.000Z" em texto)
    banco.chamada("2026-10-03T11:59:59.999Z")                   # fora do "antes" simétrico (24 h)
    rel = _rel(banco)
    assert rel["janelas"] == {"antes": ["2026-10-03T12:00:00.000Z", "2026-10-04T12:00:00.000Z"],
                              "depois": ["2026-10-04T12:00:00.000Z", "2026-10-05T12:00:00.000Z"]}
    assert rel["chamadas"] == {"antes": 1, "depois": 1, "simuladas_excluidas": 0}
    with pytest.raises(SystemExit, match="vazia"):
        lcl.janelas(CORTE, None, CORTE - timedelta(hours=1), AGORA)


def test_cache_sobre_a_entrada_total_e_nao_so_a_fresca(banco: Banco) -> None:
    banco.chamada(ANTES, entrada=1000)
    banco.chamada(DEPOIS, entrada=100, lido=800, escrito=100)   # a fresca caiu; a total é a mesma
    g = _grupo(_rel(banco), "decide", "padrão")
    assert g["antes"]["cache_lido"] == 0.0 and g["depois"]["cache_lido"] == 0.8
    assert g["depois"]["cache_escrito"] == 0.1 and g["depois"]["chamadas_com_cache_lido"] == 1.0
    assert g["antes"]["entrada_total_p50"] == g["depois"]["entrada_total_p50"] == 1000
    assert g["delta"]["entrada_fresca_p50"] == -900 and g["delta"]["cache_lido"] == 0.8


def test_latencia_so_das_chamadas_ok(banco: Banco) -> None:
    for ms in (100, 200, 300, 400):
        banco.chamada(DEPOIS, ms=ms)
    banco.chamada(DEPOIS, ms=45000, ok=0)                       # prazo estourado: conta em erros, não na latência
    d = _grupo(_rel(banco), "decide", "padrão")["depois"]
    assert d["erros"] == 1 and d["chamadas"] == 5
    assert d["ms_p50"] == 200 and d["ms_p95"] == 400


def test_perfil_e_escalation(banco: Banco) -> None:
    banco.execucao("r-2", perfil="ator-sem-thinking")
    banco.chamada(DEPOIS, run_id="r-2")
    banco.chamada(DEPOIS, run_id=None, step_id=None, role="social", origem="social")
    banco.chamada(DEPOIS, tier=1)                                # decide escalonado
    rel = _rel(banco)
    assert _grupo(rel, "decide", "ator-sem-thinking")["depois"]["chamadas"] == 1
    assert _grupo(rel, "social", "fora de execução")["depois"]["fora_de_etapa"] == 1
    esc = _grupo(rel, "escalation", "padrão")
    assert esc["depois"]["chamadas"] == 1 and esc["escalation_de"] == {"antes": {}, "depois": {"decide": 1}}
    assert _grupo(rel, lcl.TODAS, lcl.TODOS)["depois"]["chamadas"] == 3
    assert {g["amostra"] for g in rel["grupos"]} == {"pequena"}


def test_simuladas_fora_e_filtro_de_origem(banco: Banco) -> None:
    banco.chamada(DEPOIS, provider="simulated")
    banco.chamada(DEPOIS, origem="curador", run_id=None, step_id=None)
    banco.chamada(DEPOIS)
    assert _rel(banco)["chamadas"] == {"antes": 0, "depois": 2, "simuladas_excluidas": 1}
    assert _rel(banco, origem="execucao")["chamadas"]["depois"] == 1


def test_custo_por_etapa_declarado_ou_pelos_tokens(banco: Banco) -> None:
    banco.chamada(DEPOIS, step_id="s-1", usd=0.01, ms=100)
    banco.chamada(DEPOIS, step_id="s-1", usd=0.02, ms=300, role="verify")
    banco.chamada(DEPOIS, step_id="s-2", usd=0.05, ms=500)
    # O executor não grava `usd`: vale tokens × preço (Sonnet 5 [2; 0,2; 2,5; 10]): 100 × 2 + 10 × 10 = 300 por milhão.
    banco.chamada(DEPOIS, step_id="s-3", usd=None)
    t = _grupo(_rel(banco), lcl.TODAS, "padrão")["depois"]
    assert t["etapas"] == 3 and t["sem_preco"] == 0 and t["custo"] == "completo"
    assert t["usd"] == pytest.approx(0.0803)
    assert t["usd_por_etapa_p50"] == pytest.approx(0.03) and t["usd_por_etapa_media"] == pytest.approx(0.0803 / 3, abs=1e-6)   # 6 casas
    assert t["ms_por_etapa_p50"] == 500                          # etapas de 400, 500 e 1000 ms
    banco.chamada(DEPOIS, step_id=None, role="plan", usd=0.10)   # o plano: fora da etapa, dentro da execução
    t = _grupo(_rel(banco), lcl.TODAS, "padrão")["depois"]
    assert t["etapas"] == 3 and t["execucoes"] == 1 and t["usd_por_execucao_p50"] == pytest.approx(0.1803)


def test_modelo_sem_preco_paga_a_tarifa_mais_cara(banco: Banco) -> None:
    banco.chamada(DEPOIS, usd=None, model="modelo-sem-preco", entrada=1_000_000, escrito=0, lido=0)
    d = _grupo(_rel(banco), "decide", "padrão")["depois"]
    maior_entrada = max(p[0] for p in lcl.AiCfg().prices.values())
    assert d["custo"] == "estimado" and d["sem_preco"] == 1
    assert d["usd"] == pytest.approx(maior_entrada + 10 * max(p[3] for p in lcl.AiCfg().prices.values()) / 1_000_000)


def test_precos_do_yaml_substituem_a_tabela(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("ai:\n  prices:\n    claude-sonnet-5: [1, 0.1, 1.25, 5]\n", encoding="utf-8")
    tabela, fonte = lcl.precos(cfg)
    assert tabela == {"claude-sonnet-5": [1.0, 0.1, 1.25, 5.0]} and "config.yaml" in fonte
    padrao, fonte_padrao = lcl.precos(tmp_path / "nao-existe.yaml")
    assert "claude-sonnet-5" in padrao and fonte_padrao.startswith("padrão do código")


def test_ra10_ausente_e_not_run(banco: Banco) -> None:
    banco.sem_ra10()
    banco.chamada(DEPOIS)
    rel = _rel(banco, por_motivo=True)
    g = _grupo(rel, "decide", "padrão")
    assert rel["ra10"].startswith("ausente") and g["depois"]["ra10"]["nivel"] == "not_run"
    assert "por_motivo" not in g


def test_ra10_presente_conta_carimbo_e_discordancia(banco: Banco) -> None:
    banco.com_ra10()
    banco.chamada(ANTES)                                                         # linha antiga: NULA, sem carimbo
    banco.chamada(DEPOIS, role="verify", motivo="julgamento", verdict="no")
    banco.chamada(DEPOIS, role="verify", tier=1, motivo="rejulgamento", escalate="nivel", verdict="yes")
    banco.chamada(DEPOIS, role="verify", tier=1, motivo="rejulgamento", escalate="sim_com_efeito", verdict="no")
    banco.chamada(DEPOIS, role="verify", tier=1, motivo="rejulgamento", escalate="sim_com_efeito", verdict="yes")
    rel = _rel(banco, por_motivo=True)
    assert rel["ra10"] == "presente"
    esc = _grupo(rel, "escalation", "padrão")["depois"]["ra10"]
    assert esc["discordancias_do_rejulgamento"] == 2 and esc["escalates"] == {"sim_com_efeito": 2, "nivel": 1}
    assert _grupo(rel, "decide", "padrão")["antes"]["ra10"]["sem_carimbo"] == 1
    motivos = _grupo(rel, lcl.TODAS, lcl.TODOS)["por_motivo"]
    assert motivos["sem_carimbo"]["antes"]["chamadas"] == 1 and motivos["rejulgamento"]["depois"]["chamadas"] == 3


def test_corte_por_commit_avisa_que_nao_e_o_deploy() -> None:
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    instante, fonte = lcl.resolver_corte(head[:8], ROOT)
    assert instante.tzinfo is not None and "NÃO do deploy" in fonte
    assert lcl.resolver_corte("2026-10-04T12:00:00Z")[0] == CORTE
    with pytest.raises(SystemExit, match="corte inválido"):
        lcl.resolver_corte("ontem")


def test_cli_so_leitura_tambem_na_reconexao(tmp_path: Path, banco: Banco) -> None:
    with pytest.raises(SystemExit, match="banco não encontrado"):
        lcl.main(["--corte", "2026-10-04T12:00:00Z", "--db", str(tmp_path / "nao-existe.sqlite3")])
    assert not (tmp_path / "nao-existe.sqlite3").exists()
    banco.chamada(DEPOIS)
    saida, md = tmp_path / "r.json", tmp_path / "r.md"
    assert lcl.main(["--corte", "2026-10-04T12:00:00Z", "--ate", "2026-10-05T12:00:00Z", "--db",
                     str(tmp_path / "cache.sqlite3"), "--config", str(tmp_path / "sem-config.yaml"),
                     "--json", str(saida), "--md", str(md)]) == 0
    assert json.loads(saida.read_text(encoding="utf-8"))["chamadas"]["depois"] == 1
    assert "| decide | padrão |" in md.read_text(encoding="utf-8")
    db = lcl._abrir(type("A", (), {"dsn": None, "db": str(tmp_path / "cache.sqlite3")})())
    try:
        with pytest.raises(Exception, match="readonly|read-only|query_only|attempt to write"):
            db.execute("DELETE FROM ai_calls")
        db._reabrir()   # a reconexão também nasce só leitura
        with pytest.raises(Exception, match="readonly|read-only|query_only|attempt to write"):
            db.execute("DELETE FROM ai_calls")
    finally:
        db.close()
