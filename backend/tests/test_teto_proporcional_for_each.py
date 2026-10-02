"""Item 17.12 — o teto de chamadas de IA por objetivo acompanha o tamanho do `for_each`.

Achado do 7.4 real (r-20261002181642-eff15b, `msg-todos-os-contatos`): 6 de 8 envios comprovados e "Limite de 60 chamadas de
IA por objetivo" no 7º. O rejulgamento do 17.10 soma uma verificação do modelo forte por envio, e o teto fixo não cresce
com a lista. Agora `teto = ai_max_calls_per_objective + ai_max_calls_per_item × (itens − 1)`, limitado por
`ai_max_calls_absolute`; o rejulgamento CONTINUA contando, e os tetos em US$ seguem valendo.

`simulated`: o provedor é o falso do harness; o que se prova é a REGRA do guarda, não o custo real dos modelos.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import LimitsCfg
from app.models import DeliveryLevel
from app.planning.provider import AIError, DecisionRequest, Usage, Verdict
from app.taskqueue.foreach import teto_de_chamadas

from .conftest import Harness
from .fake_device import CONTACTS
from .test_for_each import ALL
from .test_hub_de_ia import SCREEN, FakeProvider, FakeRepo, _banco_com_gasto, com_hub, ctx, roteador

TERMINAIS = ("completed", "completed_with_issues", "failed", "waiting_user")


# ------------------------------------------------------------------ a fórmula (pura)
def test_formula_sem_for_each_ou_com_um_item_e_o_teto_de_sempre() -> None:
    assert teto_de_chamadas(60, 12, 300, 0) == (60, "60")
    assert teto_de_chamadas(60, 12, 300, 1) == (60, "60")                     # um item cabe no base
    assert teto_de_chamadas(60, 0, 300, 8) == (60, "60")                      # por_item=0 desliga a proporção


def test_formula_cresce_por_item_e_o_absoluto_corta() -> None:
    assert teto_de_chamadas(60, 12, 300, 8) == (144, "60 + 12 × 7 itens do for_each")
    teto, origem = teto_de_chamadas(60, 12, 300, 25)                          # 60 + 12 × 24 = 348 > 300
    assert teto == 300 and "limitado ao teto absoluto de 300" in origem
    assert teto_de_chamadas(60, 12, 300, 21)[0] == 300                        # 60 + 12 × 20 = 300: no limite, sem corte
    # o absoluto só limita o CRESCIMENTO: nunca derruba o base que o dono escolheu
    assert teto_de_chamadas(400, 12, 300, 8)[0] == 400


def test_campos_novos_tem_padrao_e_validacao() -> None:
    s = LimitsCfg()
    assert (s.ai_max_calls_per_objective, s.ai_max_calls_per_item, s.ai_max_calls_absolute) == (60, 12, 300)
    with pytest.raises(ValueError):
        LimitsCfg(ai_max_calls_per_item=-1)
    with pytest.raises(ValueError):
        LimitsCfg(ai_max_calls_absolute=0)


# ------------------------------------------------------------------ o guarda, no executor, com um objetivo de verdade
def _chamadas_do_objetivo(harness: Harness, run_id: str) -> int:
    return int(harness.state.db.one("SELECT ai_calls FROM objectives WHERE run_id=?", (run_id,))["ai_calls"])  # type: ignore[union-attr]


def _limites(harness: Harness, **campos: int) -> None:
    harness.state.settings.update(campos)                                      # type: ignore[union-attr]


async def _medir(harness: Harness) -> int:
    """Quantas chamadas a lista de CONTACTS (5) consome SEM teto no caminho (receita desligada: mesma conta a cada vez)."""
    harness.cfg.file.ai.recipes = "off"
    _limites(harness, ai_max_calls_per_objective=1000)
    run = harness.run(["android-01"], command=ALL)
    assert (await harness.wait_run(run.id, timeout=120)).status == "completed"
    return _chamadas_do_objetivo(harness, run.id)


async def test_for_each_de_varios_itens_nao_estoura_onde_o_teto_fixo_estouraria(harness: Harness) -> None:
    usadas = await _medir(harness)
    assert usadas > len(CONTACTS)                                              # sanidade: cada item gasta chamada
    base = usadas - 6                                                          # com o teto FIXO neste valor, estoura
    _limites(harness, ai_max_calls_per_objective=base, ai_max_calls_per_item=0)
    fixo = harness.run(["android-01"], command=ALL)
    await harness.wait_run(fixo.id, timeout=120, statuses=TERMINAIS)
    obj = harness.state.db.one("SELECT * FROM objectives WHERE run_id=?", (fixo.id,))   # type: ignore[union-attr]
    assert obj["status"] in ("failed", "waiting_user")
    erro = harness.state.db.one("SELECT error_message FROM ai_calls WHERE run_id=? AND error_kind='budget'",  # type: ignore[union-attr]
                                (fixo.id,))
    assert erro is not None and erro["error_message"] == f"Limite de {base} chamadas de IA por objetivo atingido."

    # o MESMO base, agora proporcional: cada item a mais soma o suficiente para fechar a lista inteira
    _limites(harness, ai_max_calls_per_objective=base, ai_max_calls_per_item=usadas, ai_max_calls_absolute=5000)
    prop = harness.run(["android-01"], command=ALL)
    assert (await harness.wait_run(prop.id, timeout=120)).status == "completed"
    assert harness.state.db.scalar("SELECT count(*) FROM ai_calls WHERE run_id=? AND error_kind='budget'",  # type: ignore[union-attr]
                                   (prop.id,)) == 0


async def _nao_deve_chamar() -> None:
    raise AssertionError("o teto é conferido ANTES de chamar o modelo")


async def test_sem_for_each_o_teto_e_o_atual_e_a_mensagem_de_sempre(harness: Harness) -> None:
    """Nenhuma etapa com `item_index` (comando sem lista, ou lista de 1 item): o teto é `ai_max_calls_per_objective`,
    com o mesmo texto de antes."""
    run = harness.run(["android-01"])                                          # comando sem for_each
    await harness.wait_run(run.id, timeout=60)
    obj_id = f"{run.id}:android-01"
    ex = harness.state.scheduler.executor                                      # type: ignore[union-attr]
    assert ex._teto_de_chamadas(obj_id, 60, harness.state.settings.get()) == (60, "60")   # type: ignore[union-attr]  # noqa: SLF001
    _limites(harness, ai_max_calls_per_objective=1)
    with pytest.raises(AIError) as e:
        await ex._ai(run.id, obj_id, _nao_deve_chamar)                         # noqa: SLF001
    assert e.value.kind == "budget" and str(e.value) == "Limite de 1 chamadas de IA por objetivo atingido."


async def test_teto_absoluto_corta_mesmo_com_muitos_itens_e_a_mensagem_diz_de_onde_veio(harness: Harness) -> None:
    harness.cfg.file.ai.recipes = "off"
    run = harness.run(["android-01"], command=ALL)
    assert (await harness.wait_run(run.id, timeout=120)).status == "completed"
    obj_id = f"{run.id}:android-01"
    ex = harness.state.scheduler.executor                                      # type: ignore[union-attr]
    # 5 itens expandidos (versão 2 do plano): 10 + 100 × 4 = 410, mas o absoluto é 50
    _limites(harness, ai_max_calls_per_objective=10, ai_max_calls_per_item=100, ai_max_calls_absolute=50)
    harness.state.db.execute("UPDATE objectives SET ai_calls=50 WHERE id=?", (obj_id,))     # type: ignore[union-attr]
    with pytest.raises(AIError) as e:
        await ex._ai(run.id, obj_id, _nao_deve_chamar)                         # noqa: SLF001
    assert e.value.kind == "budget"
    assert "Limite de 50 chamadas" in str(e.value) and "10 + 100 × 4 itens do for_each" in str(e.value)
    assert "limitado ao teto absoluto de 50" in str(e.value)
    # e logo abaixo do absoluto o guarda ainda deixa passar
    harness.state.db.execute("UPDATE objectives SET ai_calls=49 WHERE id=?", (obj_id,))     # type: ignore[union-attr]
    chamado = False

    async def chamar() -> tuple[str, Usage]:
        nonlocal chamado
        chamado = True
        return "ok", Usage(calls=1)

    assert (await ex._ai(run.id, obj_id, chamar)) == "ok" and chamado         # noqa: SLF001


async def test_o_rejulgamento_conta_no_teto(harness: Harness) -> None:
    """17.10: o verificador barato aprova e o modelo forte rejulga. As DUAS chamadas entram em `ai_calls` do objetivo —
    o teto não fica cego à chamada paga do rejulgamento (a alternativa 'rejulgamento fora do teto' foi rejeitada)."""
    harness.cfg.file.ai.recipes = "off"
    harness.cfg.env.ai_model_verifier, harness.cfg.env.ai_model_escalation = "modelo-barato", "modelo-forte"
    vistos: list[bool] = []
    inner = harness.ai.inner
    verify0 = inner.verify

    async def verify(req: Any) -> Any:
        if req.ctx.step_key != "verify_sent":
            return await verify0(req)
        vistos.append(req.escalate)
        return Verdict(satisfied="yes", evidence="x", delivery_level=DeliveryLevel.sent), Usage()

    inner.verify = verify
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert vistos[:2] == [False, True]                                         # barato e depois forte (rejulgamento)
    por_objetivo = harness.state.db.scalar(                                    # type: ignore[union-attr]
        "SELECT count(*) FROM ai_calls WHERE run_id=? AND role='verify' AND step_id LIKE '%verify_sent'", (run.id,))
    assert por_objetivo >= 2                                                   # as duas chamadas gravadas...
    assert _chamadas_do_objetivo(harness, run.id) >= por_objetivo              # ...e somadas ao objetivo que o guarda lê


# ------------------------------------------------------------------ US$: outro guarda, no roteador, intacto
def test_tetos_em_usd_continuam_valendo_com_o_teto_de_chamadas_folgado(tmp_path: Path) -> None:
    """O teto proporcional não toca `ai_max_usd_per_run`: um `for_each` grande, com o teto de chamadas lá em cima, ainda
    é barrado pelo dinheiro (`RoutingProvider._budget`, o mesmo ponto de toda chamada de IA)."""
    db = _banco_com_gasto(tmp_path, [("r9", "claude-opus-5", 1_000_000, 0)])   # US$ 5,00 nesta execução
    cfg = com_hub(tmp_path, providers={"anthropic": {"kind": "anthropic"}}, roles={})
    r = roteador(cfg, {"decide": FakeProvider("anthropic", "claude-opus-5")})
    r.attach(repo=FakeRepo(db), settings_getter=lambda: SimpleNamespace(
        ai_max_usd_per_run=4.0, ai_max_usd_per_day=0.0, ai_max_calls_per_objective=1000, ai_max_calls_per_item=200,
        ai_max_calls_absolute=5000))
    with pytest.raises(AIError) as e:
        asyncio.run(r.decide(DecisionRequest(ctx=ctx("r9"), screen=SCREEN)))
    assert e.value.kind == "budget" and "US$ 5.00 de US$ 4.00" in str(e.value)
