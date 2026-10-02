"""Origem das chamadas de IA e rubrica única de gasto (itens 31.2 e 31.6; docs/design/hub-de-ia-fora-de-execucao.md).

O que cada bloco prova:

- **31.2: cada uso grava a sua origem.** Os seis métodos do hub (plano com `run_id`, ensino, orquestração, assistente,
  social, persona) levam a origem certa de `_call` até `ai_calls.origem`; curador e decisão fechada passam por
  `_call(origem=...)`, que é por onde os métodos futuros entram. Linha sem hub e sem `run_id` fica NULL.
- **31.2: `costs.spent_usd(origem=...)`** soma só as linhas daquela origem, e linha antiga (NULL) nunca entra.
- **31.6: cada régua sai com o seu `AIError.motivo`**, a fatia estourada de uma origem não barra outra, e sem fatia
  configurada nada muda.

Prova `simulated`: provedor falso e banco de teste (SQLite, ou o PostgreSQL de `TEST_DATABASE_URL`).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.db import Database
from app.events import EventBus
from app.planning import costs
from app.planning.provider import AIError, MOTIVOS_DE_ORCAMENTO, ORIGENS_DE_IA, Usage
from app.planning.routing import RoutingProvider
from app.taskqueue.repository import Repository

from .conftest import _dsn_de_teste
from .test_hub_de_ia import FakeProvider, FakeRepo, com_hub, roteador

PRECOS = {"claude-opus-5": [5.0, 0.5, 6.25, 25.0]}
MODELO = "claude-opus-5"


class ProvedorDeTodosOsUsos(FakeProvider):
    """Responde a todo método do hub fora de execução (o `FakeProvider` do hub só tem os da execução e o social)."""

    async def generalize(self, req: Any) -> Any:
        return await self._responde("plan")

    async def orchestrate_targets(self, req: Any) -> Any:
        return await self._responde("plan")

    async def refine_command(self, req: Any) -> Any:
        return await self._responde("plan")

    async def generate_persona(self, req: Any) -> Any:
        return await self._responde("persona")


def _banco(tmp_path: Path) -> Database:
    db = Database(_dsn_de_teste() or tmp_path / "origem.sqlite3")
    db.migrate()
    return db


def _gasta(db: Database, origem: str | None, entrada: int, *, run_id: str | None = None) -> None:
    """Grava uma linha de gasto hoje; 1 000 000 de entrada no Opus de teste = US$ 5,00."""
    db.execute("INSERT INTO ai_calls(ts, run_id, role, model, input_tokens, output_tokens, origem)"
               " VALUES (?,?,?,?,?,?,?)", (costs.day_start_iso(), run_id, "plan", MODELO, entrada, 0, origem))


def _hub(tmp_path: Path, db: Database, *, teto_dia: float = 0.0, teto_execucao: float = 0.0,
         teto_pedido: float | None = None, limites: dict[str, Any] | None = None) -> tuple[RoutingProvider, FakeRepo]:
    cfg = com_hub(tmp_path, providers={"anthropic": {"kind": "anthropic"}}, roles={})
    cfg.file.ai.prices = dict(PRECOS)
    if limites:
        for chave, valor in limites.items():
            setattr(cfg.file.ai.limits, chave, valor)
    fake = ProvedorDeTodosOsUsos("anthropic", MODELO)
    r = roteador(cfg, {papel: fake for papel in ("plan", "social", "persona", "decide")})
    repo = FakeRepo(db)
    repo.teto_usd_da_execucao = lambda run_id: teto_pedido            # type: ignore[method-assign]
    r.attach(repo=repo, settings_getter=lambda: SimpleNamespace(ai_max_usd_per_run=teto_execucao,
                                                                ai_max_usd_per_day=teto_dia))
    return r, repo


def _chama(r: RoutingProvider, origem: str | None, run_id: str | None = None) -> Usage:
    """Uma chamada qualquer pelo `_call` do hub, com a origem pedida."""
    _, usage = asyncio.run(r._call("plan", run_id, lambda p: p.plan(None), origem=origem))   # noqa: SLF001
    return usage


# ====================================================================== 31.2 — a origem é gravada
def test_cada_metodo_do_hub_grava_a_sua_origem(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    cfg = tmp_path / "cfg"
    r, _ = _hub(tmp_path, db)
    cfg.mkdir()
    fila = Repository(db, EventBus(db), cfg)
    pedidos: list[tuple[str, str | None, Any]] = [
        ("execucao", "r1", lambda: r._call("plan", "r1", lambda p: p.plan(None))),             # noqa: SLF001
        ("ensino", None, lambda: r.generalize(object())),
        ("orquestracao", None, lambda: r.orchestrate_targets(object())),                       # type: ignore[arg-type]
        ("assistente", None, lambda: r.refine_command(object())),                              # type: ignore[arg-type]
        ("social", None, lambda: r.generate_social_response(object())),                        # type: ignore[arg-type]
        ("persona", None, lambda: r.generate_persona(object())),                               # type: ignore[arg-type]
    ]
    for origem, run_id, chamada in pedidos:
        _, usage = asyncio.run(chamada())
        assert usage.origem == origem
        fila.add_usage(run_id, None, usage)
    linhas = db.query("SELECT origem, run_id FROM ai_calls ORDER BY id")
    assert [linha["origem"] for linha in linhas] == [p[0] for p in pedidos]
    assert [linha["run_id"] for linha in linhas] == ["r1", None, None, None, None, None]
    db.close()


def test_curador_e_decisao_fechada_entram_por_call_com_origem_e_ref(tmp_path: Path) -> None:
    """Os métodos do 30.12 e da porta de decisão ainda não existem; o contrato é `_call(origem=, ref=)`."""
    db = _banco(tmp_path)
    r, _ = _hub(tmp_path, db)
    (tmp_path / "ev").mkdir()
    fila = Repository(db, EventBus(db), tmp_path / "ev")
    for origem, ref in (("curador", "rev-7"), ("decisao_fechada", None)):
        _, usage = asyncio.run(r._call("plan", None, lambda p: p.plan(None), origem=origem, ref=ref))   # noqa: SLF001
        fila.add_usage(None, None, usage)
    linhas = db.query("SELECT origem, ref FROM ai_calls ORDER BY id")
    assert [(linha["origem"], linha["ref"]) for linha in linhas] == [("curador", "rev-7"), ("decisao_fechada", None)]
    assert {"curador", "decisao_fechada"} <= set(ORIGENS_DE_IA)
    db.close()


def test_linha_sem_hub_fica_nula_fora_de_execucao_e_execucao_com_run_id(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    (tmp_path / "ev").mkdir()
    fila = Repository(db, EventBus(db), tmp_path / "ev")
    fila.add_usage(None, None, Usage(calls=1, role="plan", model=MODELO))
    fila.add_usage("r2", None, Usage(calls=1, role="decide", model=MODELO))     # erro do executor, sem passar pelo hub
    linhas = db.query("SELECT origem FROM ai_calls ORDER BY id")
    assert [linha["origem"] for linha in linhas] == [None, "execucao"]
    db.close()


def test_filtro_por_origem_soma_so_a_origem_e_ignora_linha_antiga(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    _gasta(db, "curador", 200_000)                      # US$ 1,00
    _gasta(db, "curador", 100_000)                      # US$ 0,50
    _gasta(db, "social", 1_000_000)                     # US$ 5,00
    _gasta(db, None, 2_000_000)                         # linha antiga: US$ 10,00 sem origem
    _gasta(db, "execucao", 400_000, run_id="r1")        # US$ 2,00 numa execução
    assert costs.spent_today_usd(db, PRECOS, origem="curador") == 1.5
    assert costs.spent_today_usd(db, PRECOS, origem="social") == 5.0
    assert costs.spent_today_usd(db, PRECOS, origem="persona") == 0.0
    assert costs.spent_today_usd(db, PRECOS) == 18.5                         # sem filtro, nada muda
    assert costs.spent_usd(db, PRECOS, run_id="r1", origem="execucao") == 2.0
    assert costs.spent_usd(db, PRECOS, run_id="r1", origem="curador") == 0.0
    assert costs.spent_usd(db, PRECOS, origem="curador") == 0.0              # origem sozinha não define janela
    db.close()


# ====================================================================== 31.6 — cada régua, o seu motivo
def test_pedido_e_execucao_e_dia_saem_com_o_motivo_certo(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    _gasta(db, "execucao", 1_000_000, run_id="r9")      # US$ 5,00 na execução e no dia

    r, _ = _hub(tmp_path, db, teto_pedido=4.0, teto_execucao=3.0, teto_dia=3.0)     # as três estouradas
    with pytest.raises(AIError) as e:
        _chama(r, None, run_id="r9")
    assert (e.value.kind, e.value.motivo) == ("budget", "pedido") and "Orçamento do pedido" in str(e.value)

    r, _ = _hub(tmp_path, db, teto_execucao=3.0, teto_dia=3.0)
    with pytest.raises(AIError) as e:
        _chama(r, None, run_id="r9")
    assert (e.value.kind, e.value.motivo) == ("budget", "execucao") and "desta execução" in str(e.value)

    r, _ = _hub(tmp_path, db, teto_dia=3.0)
    with pytest.raises(AIError) as e:
        _chama(r, "social")
    assert (e.value.kind, e.value.motivo) == ("budget", "dia") and "do dia" in str(e.value)
    db.close()


def test_fatia_do_curador_e_derivada_do_teto_do_dia(tmp_path: Path) -> None:
    """Teto do dia US$ 10 e α = 10 % dão fatia de US$ 1,00. Estourada, barra o curador com o motivo da fatia."""
    db = _banco(tmp_path)
    _gasta(db, "curador", 200_000)                      # US$ 1,00 = a fatia inteira
    r, _ = _hub(tmp_path, db, teto_dia=10.0)
    with pytest.raises(AIError) as e:
        _chama(r, "curador")
    assert (e.value.kind, e.value.motivo) == ("budget", "fatia_curador") and "fatia do curador" in str(e.value)
    db.close()


def test_fatia_do_curador_explicita_vale_no_lugar_da_fracao(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    _gasta(db, "curador", 200_000)                      # US$ 1,00
    r, _ = _hub(tmp_path, db, teto_dia=10.0, limites={"curador_max_usd_per_day": 3.0})
    assert _chama(r, "curador").origem == "curador"      # a fatia é US$ 3,00: ainda passa
    r, _ = _hub(tmp_path, db, teto_dia=10.0, limites={"curador_max_usd_per_day": 0.5})
    with pytest.raises(AIError) as e:
        _chama(r, "curador")
    assert e.value.motivo == "fatia_curador"
    db.close()


def test_fatia_do_jev_tem_padrao_de_cinquenta_centavos(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    _gasta(db, "decisao_fechada", 120_000)               # US$ 0,60 > US$ 0,50
    r, _ = _hub(tmp_path, db)                            # sem teto do dia: a fatia do Jev vale por si
    with pytest.raises(AIError) as e:
        _chama(r, "decisao_fechada")
    assert (e.value.kind, e.value.motivo) == ("budget", "fatia_jev")
    db.close()


def test_fatia_estourada_nao_bloqueia_outra_origem(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    _gasta(db, "curador", 400_000)                       # US$ 2,00 > fatia do curador (US$ 1,00)
    _gasta(db, "decisao_fechada", 120_000)               # US$ 0,60 > fatia do Jev (US$ 0,50)
    r, _ = _hub(tmp_path, db, teto_dia=10.0)             # o dia (US$ 2,60 de 10) está folgado
    for origem, motivo in (("curador", "fatia_curador"), ("decisao_fechada", "fatia_jev")):
        with pytest.raises(AIError) as e:
            _chama(r, origem)
        assert e.value.motivo == motivo
    # as demais origens, e a execução, seguem
    for origem in ("social", "persona", "ensino", "orquestracao", "assistente", None):
        _chama(r, origem)
    _chama(r, None, run_id="r1")
    db.close()


def test_a_fatia_e_parte_do_dia_e_o_dia_vence_primeiro(tmp_path: Path) -> None:
    """Passar na fatia não basta: o teto do dia estourado barra o curador com `dia`, não com `fatia_curador`."""
    db = _banco(tmp_path)
    _gasta(db, "social", 1_000_000)                      # US$ 5,00 no dia, outra origem
    r, _ = _hub(tmp_path, db, teto_dia=4.0)
    with pytest.raises(AIError) as e:
        _chama(r, "curador")
    assert e.value.motivo == "dia"
    db.close()


def test_sem_fatia_configurada_nada_muda(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    _gasta(db, "curador", 4_000_000)                     # US$ 20,00 gastos pelo curador
    # sem teto do dia e sem valor explícito: a fração é do teto do dia, que não existe -> sem fatia
    r, _ = _hub(tmp_path, db)
    assert _chama(r, "curador").origem == "curador"
    # 0 desliga a fatia mesmo com teto do dia folgado (US$ 20 de 1000)
    r, _ = _hub(tmp_path, db, teto_dia=1000.0, limites={"curador_max_usd_per_day": 0.0})
    assert _chama(r, "curador").origem == "curador"
    db.close()


def test_aviso_de_oitenta_por_cento_da_fatia_sai_uma_vez(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    _gasta(db, "curador", 170_000)                       # US$ 0,85 de US$ 1,00
    r, repo = _hub(tmp_path, db, teto_dia=10.0)
    _chama(r, "curador")
    _chama(r, "curador")
    avisos = [t for _, t in repo.events if "Gasto de IA" in t and "fatia do curador" in t]
    assert len(avisos) == 1
    db.close()


# ====================================================================== o tipo do erro
def test_motivo_so_existe_com_kind_budget_e_e_fechado() -> None:
    for motivo in MOTIVOS_DE_ORCAMENTO:
        assert AIError("x", kind="budget", motivo=motivo).motivo == motivo        # type: ignore[arg-type]
    assert AIError("x", kind="budget").motivo is None       # teto de chamadas/tokens (17.12) fica sem motivo
    assert AIError("x").motivo is None
    with pytest.raises(ValueError):
        AIError("x", kind="refusal", motivo="dia")
    with pytest.raises(ValueError):
        AIError("x", kind="budget", motivo="inventado")     # type: ignore[arg-type]
