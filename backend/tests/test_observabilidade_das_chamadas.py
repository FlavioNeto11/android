"""RA-10 (reavaliação de 03/10, migração 080): a linha de `ai_calls` diz o desfecho, para que a chamada foi feita, por
que subiu de modelo e por que a imagem foi; as linhas sem resposta do provedor têm `provider`; a imagem da persona tem
`origem`; a etapa que a IA conduziu tem `driven_by='ai'`; e `/api/usage` agrupa por isso (origem, escalonamento,
rejulgamento com discordância por app, cascata, motivo da imagem, etapas sem condutor).

`simulated`: provedor e aparelho falsos. A prova real é a consulta de conferência de `docs/ia.md` §9 depois do deploy.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.automation.tools import TOOLS
from app.db import Database
from app.models import DeliveryLevel
from app.modules.identity.infrastructure.persona_images import AiCallsAccounting
from app.planning import costs
from app.planning.provider import (MOTIVOS_DA_CHAMADA, MOTIVOS_DA_IMAGEM, MOTIVOS_DE_ESCALONAMENTO, MarcaDaChamada,
                                   Usage, Verdict)
from app.taskqueue.executor import _IMAGEM_VAI

from .conftest import Harness, _dsn_de_teste
from .test_cascata_ator_barato import TERMINAIS, _decide_que_bloqueia_no_tier0, _modelos_diferentes, _verify_com


def _db(tmp: Path) -> Database:
    db = Database(_dsn_de_teste() or tmp / "obs.sqlite3")
    db.migrate()
    return db


async def _usage(h: Harness, **params: Any) -> dict[str, Any]:
    from app.main import create_app

    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        return (await c.get("/api/usage", params=params)).json()


def _linhas(h: Harness, run_id: str, role: str) -> list[dict[str, Any]]:
    return [dict(r) for r in h.state.db.query(                                           # type: ignore[union-attr]
        "SELECT tier, motivo, escalate, verdict, image_reason, with_image, provider FROM ai_calls"
        " WHERE run_id=? AND role=? ORDER BY id", (run_id, role))]


def test_migracao_080_cria_as_colunas(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert {"verdict", "escalate", "motivo", "image_reason"} <= db.columns("ai_calls")


async def test_add_usage_grava_as_quatro_colunas(harness: Harness) -> None:
    repo = harness.state.repo                                                            # type: ignore[union-attr]
    repo.add_usage(None, None, Usage(calls=1, role="decide", model="m", provider="local", verdict="tap",
                                     escalate="piso", motivo="decisao", image_reason="arvore_pobre"))
    linha = repo.db.one("SELECT verdict, escalate, motivo, image_reason, provider FROM ai_calls WHERE run_id IS NULL")
    assert dict(linha) == {"verdict": "tap", "escalate": "piso", "motivo": "decisao", "image_reason": "arvore_pobre",
                           "provider": "local"}


async def test_execucao_marca_toda_chamada_com_vocabulario_fechado(harness: Harness) -> None:
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert [(p["motivo"], p["verdict"]) for p in _linhas(harness, run.id, "plan")] == [("plano", "plano")]
    decisoes = _linhas(harness, run.id, "decide")
    assert decisoes
    for d in decisoes:
        assert d["motivo"] in ("decisao", "cascata") and d["verdict"] in TOOLS
        assert d["image_reason"] in MOTIVOS_DA_IMAGEM
        assert d["escalate"] is None or d["escalate"] in MOTIVOS_DE_ESCALONAMENTO
        assert (d["escalate"] is not None) == (d["tier"] >= 1)        # subiu de modelo ⇔ diz por quê
    for v in _linhas(harness, run.id, "verify"):
        assert v["motivo"] in MOTIVOS_DA_CHAMADA and v["verdict"] in ("yes", "no", "uncertain", "unprovable")
    for d in decisoes:     # o motivo é o da decisão tomada: a imagem foi se, e só se, o motivo a manda junto
        assert bool(d["with_image"]) == (d["image_reason"] in _IMAGEM_VAI), d
    u = await _usage(harness, run_id=run.id)
    assert u["steps_driven_by_null"] == 0
    db = harness.state.db                                                                # type: ignore[union-attr]
    com_motivo = db.one("SELECT COUNT(*) n FROM ai_calls WHERE run_id=? AND image_reason IS NOT NULL", (run.id,))["n"]
    assert sum(g["calls"] for g in u["image_reasons"].values()) == com_motivo >= len(decisoes)


async def test_efeito_externo_sobe_com_motivo_efeito(harness: Harness) -> None:
    """Com `strong_model_for_side_effect: true` a etapa de envio nasce no modelo de escalonamento: o motivo é `efeito`."""
    harness.cfg.file.ai.strong_model_for_side_effect = True   # o padrão é `by_risk`, e o envio do QA é de risco baixo
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert "efeito" in {d["escalate"] for d in _linhas(harness, run.id, "decide")}
    assert (await _usage(harness, run_id=run.id))["escalations"]["efeito"]["calls"] >= 1


def _verify_que_recusa_nivel_suficiente(h: Harness) -> None:
    """O erro medido do 7.10 (como em `test_verificador_escalado`): o barato recusa "Entregue" onde "Enviada" basta."""
    inner = h.ai.inner
    verify0 = inner.verify

    async def verify(req: Any) -> Any:
        if req.ctx.step_key != "verify_sent":
            return await verify0(req)
        if not req.escalate:
            return Verdict(satisfied="no", evidence="exige Enviada; a tela mostra Entregue",
                           delivery_level=DeliveryLevel.delivered), Usage()
        return Verdict(satisfied="yes", evidence="Entregue é posterior a Enviada",
                       delivery_level=DeliveryLevel.delivered), Usage()

    inner.verify = verify


async def test_rejulgamento_por_nivel_e_tier_1_com_motivo_e_veredito(harness: Harness) -> None:
    _verify_que_recusa_nivel_suficiente(harness)
    run_id = harness.run(["android-01"]).id
    await harness.wait_run(run_id, statuses=TERMINAIS)
    verificacoes = _linhas(harness, run_id, "verify")
    rejulgadas = [v for v in verificacoes if v["motivo"] == "rejulgamento"]
    # antes do RA-10 o rejulgamento era `verify` tier 0, indistinguível do julgamento barato
    assert [(v["tier"], v["escalate"], v["verdict"]) for v in rejulgadas] == [(1, "nivel", "yes")]
    assert any(v["motivo"] == "julgamento" and v["verdict"] == "no" and v["tier"] == 0 for v in verificacoes)

    rej = (await _usage(harness, run_id=run_id))["rejudges"]
    assert (rej["calls"], rej["judged"], rej["disagreements"], rej["disagreement_rate"]) == (1, 1, 1, 1.0)
    assert list(rej["by_kind"]) == ["nivel"]
    (app, por_app), = rej["by_app"].items()
    assert app and por_app == {"judged": 1, "disagreements": 1, "disagreement_rate": 1.0}


async def test_rejulgamento_do_sim_com_efeito_concordando_nao_e_discordancia(harness: Harness) -> None:
    _modelos_diferentes(harness)
    _verify_com(harness, barato="yes", forte="yes", vistos=[])
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    rejulgadas = [v for v in _linhas(harness, run.id, "verify") if v["motivo"] == "rejulgamento"]
    # toda etapa com efeito externo e "sim" barato é rejulgada (o envio e a conferência do envio)
    assert rejulgadas
    assert {(v["tier"], v["escalate"], v["verdict"]) for v in rejulgadas} == {(1, "sim_com_efeito", "yes")}
    rej = (await _usage(harness, run_id=run.id))["rejudges"]
    assert (rej["judged"], rej["disagreements"], rej["disagreement_rate"]) == (len(rejulgadas), 0, 0.0)


async def test_cascata_do_bloqueio_e_marcada_e_conta_como_desbloqueio(harness: Harness) -> None:
    tiers: list[int] = []
    _decide_que_bloqueia_no_tier0(harness, "other", needs_user=True, tiers=tiers)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    cascatas = [d for d in _linhas(harness, run.id, "decide") if d["motivo"] == "cascata"]
    assert [(d["tier"], d["escalate"]) for d in cascatas] == [(1, "bloqueio")]
    assert cascatas[0]["verdict"] != "step_blocked"
    cas = (await _usage(harness, run_id=run.id))["cascades"]
    assert (cas["calls"], cas["unblocked"]) == (1, 1)
    # a decisão que bloqueou não é cascata: ela é o motivo da cascata seguinte
    assert any(d["verdict"] == "step_blocked" and d["motivo"] == "decisao" and d["tier"] == 0
               for d in _linhas(harness, run.id, "decide"))


async def test_receitas_desligadas_a_ia_conduz_e_grava_driven_by(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    h.cfg.file.ai.recipes = "off"
    await h.boot()
    try:
        run = h.run(["android-01"])
        await h.wait_run(run.id, statuses=TERMINAIS)
        db = h.state.db                                                                  # type: ignore[union-attr]
        conduzidas = db.query("SELECT DISTINCT s.id, s.driven_by FROM steps s JOIN ai_calls c ON c.step_id=s.id"
                              " AND c.role='decide' WHERE s.run_id=?", (run.id,))
        assert conduzidas and {r["driven_by"] for r in conduzidas} == {"ai"}
        assert (await _usage(h, run_id=run.id))["steps_driven_by_null"] == 0
    finally:
        await h.state.stop()                                                             # type: ignore[union-attr]


async def test_linha_sem_resposta_tem_provedor_e_modelo_da_funcao(harness: Harness,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    executor = harness.state.scheduler.executor                                          # type: ignore[union-attr]
    funcoes = {"decide": SimpleNamespace(provider="local", model="barato"),
               "escalation": SimpleNamespace(provider="anthropic", model="forte")}
    monkeypatch.setattr(executor, "provider", SimpleNamespace(roles=funcoes, name="hub"))
    escalada = executor._uso_sem_resposta("decide", MarcaDaChamada(motivo="decisao", escalate="piso"))
    assert (escalada.role, escalada.provider, escalada.model, escalada.tier, escalada.escalate) == (
        "decide", "anthropic", "forte", 1, "piso")
    barata = executor._uso_sem_resposta("decide", MarcaDaChamada(motivo="decisao"))
    assert (barata.provider, barata.model, barata.tier, barata.motivo) == ("local", "barato", 0, "decisao")
    sem_funcao = executor._uso_sem_resposta("verify", None)
    assert (sem_funcao.provider, sem_funcao.motivo, sem_funcao.verdict) == ("hub", None, None)


def test_usd_por_segue_a_regra_de_spent_usd(tmp_path: Path) -> None:
    db = _db(tmp_path)
    linhas = [("decide", "m-a", "anthropic", 1_000_000, None, "efeito"),     # 1M de entrada a US$ 3
              ("decide", "m-a", "simulated", 1_000_000, None, "efeito"),     # simulado: conta, mas custa 0
              ("image", "img", "openai", 0, 0.04, None),                      # custo declarado
              ("verify", "m-a", "anthropic", 0, None, None)]
    for role, modelo, provedor, entrada, usd, escalate in linhas:
        db.execute("INSERT INTO ai_calls(ts, role, model, provider, input_tokens, usd, escalate, origem)"
                   " VALUES ('2026-10-03T00:00:00Z',?,?,?,?,?,?,?)",
                   (role, modelo, provedor, entrada, usd, escalate, "persona" if role == "image" else "execucao"))
    precos = {"m-a": [3.0, 15.0, 0.3, 3.75]}
    assert costs.usd_por(db, precos, "escalate", "1=1", ()) == {"efeito": (2, 3.0), None: (2, 0.04)}
    assert costs.usd_por(db, precos, "origem", "1=1", ()) == {"execucao": (3, 3.0), "persona": (1, 0.04)}
    total = costs.spent_usd(db, precos, since="2026-10-01T00:00:00Z")
    assert total == pytest.approx(sum(u for _, u in costs.usd_por(db, precos, "origem", "1=1", ()).values()))
    with pytest.raises(ValueError):
        costs.usd_por(db, precos, "model; DROP TABLE ai_calls", "1=1", ())


def test_imagem_da_persona_grava_origem(tmp_path: Path) -> None:
    db = _db(tmp_path)
    AiCallsAccounting(db, {}, lambda: None).record(provider="openai", model="gpt-image-2", usd=0.04, ms=10, ok=True,
                                                   error=None)
    assert db.one("SELECT origem, role FROM ai_calls")["origem"] == "persona"                 # type: ignore[index]
