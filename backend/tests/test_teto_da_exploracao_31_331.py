"""31.331: o teto da exploração conta só a exploração, olha a próxima chamada, a etapa exploratória não sofre o orçamento da
etapa livre e o aviso `efeito_liberado` sai uma vez por execução.

Achados da prova real do P-046 (10/10/2026, Outlook, `android-01`, duas execuções do mesmo pedido de efeito):
* US$ 0,1075 contra o teto de 0,10 e US$ 0,2615 contra 0,25: a checagem era `gasto >= teto` ANTES de cada chamada, então a
  chamada que cruza o teto sempre passava; e o planejamento (US$ 0,038, sempre antes) comia o teto da exploração;
* a etapa exploratória falhou na v1 pelo orçamento genérico da etapa livre ("o normal é 3 a 8 chamadas, para em 16") antes de
  achar a linha da mensagem, embora tenha o teto próprio;
* o aviso `exploracao.efeito_liberado` saía a cada versão do plano (a recuperação automática refaz a etapa), 2 por execução.

Nível de prova: `simulated` (harness com provedor e aparelhos falsos). `real`: `not_run`.
"""
from __future__ import annotations

from typing import Any

from app.planning import costs
from app.planning import exploracao as ex
from app.planning.provider import Usage
from app.util import now_iso

from .conftest import Harness
from .test_etapas_descobertas import _semear
from .test_exploracao_de_efeito import _avisos, _porta
from .test_exploracao_de_efeito import _semear as _semear_efeito

RUN = "r-20261010150000-aaaaaa"
MODELO = "claude-sonnet-5-5"


def _preparar(harness: Harness) -> tuple[Any, Any, str, str]:
    st = harness.state
    assert st is not None
    passo = ex.passo_da_exploracao("ver a caixa de lixo", ex.classificar("ver a caixa de lixo"), app_id=None,
                                   nome_do_app="QA")
    _semear(st, RUN, passo)
    return st, st.scheduler.executor, f"{RUN}:android-01:v1:{passo.key}", f"{RUN}:o"


def _chamadas(st: Any, n: int, step: str | None, *, saida: int) -> None:
    for _ in range(n):
        st.db.execute("INSERT INTO ai_calls(ts, run_id, step_id, role, model, provider, output_tokens)"
                      " VALUES (?,?,?,?,?,?,?)", (now_iso(), RUN, step, "decide", MODELO, "anthropic", saida))


def test_o_gasto_da_exploracao_nao_inclui_o_planejamento_nem_as_etapas_do_catalogo(harness: Harness) -> None:
    st, _exec, sid, _oid = _preparar(harness)
    prices = st.cfg.file.ai.prices
    _chamadas(st, 2, None, saida=100_000)                              # o planejamento: sem etapa
    st.db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, postcondition,"
                  " timeout_s, max_attempts, status, app_id) VALUES (?,?,?,?,1,2,'ver_ajuda','t','g',?,60,3,'succeeded','qa-messenger')",
                  (f"{RUN}:android-01:v1:ver_ajuda", RUN, f"{RUN}:o", "android-01",
                   '{"kind":"model_judged","value":"v","description":"d"}'))
    _chamadas(st, 2, f"{RUN}:android-01:v1:ver_ajuda", saida=100_000)  # uma etapa do catálogo
    assert costs.spent_usd(st.db, prices, run_id=RUN, so_exploratorias=True) == 0
    _chamadas(st, 1, sid, saida=100_000)
    total = costs.spent_usd(st.db, prices, run_id=RUN)
    so_ela = costs.spent_usd(st.db, prices, run_id=RUN, so_exploratorias=True)
    assert 0 < so_ela < total and abs(so_ela * 5 - total) < 1e-6      # 1 de 5 chamadas iguais


def test_o_planejamento_nao_consome_o_teto_da_exploracao(harness: Harness) -> None:
    st, exec_, sid, _oid = _preparar(harness)
    s = st.scheduler.get_settings()
    s.exploracao_max_chamadas_ia, s.exploracao_max_usd = 500, 0.60
    _chamadas(st, 3, None, saida=1_000_000)                            # o planejamento sozinho passa de US$ 0,60
    assert exec_._teto_da_exploracao(RUN, sid, s) is None              # noqa: SLF001


def test_a_conferencia_considera_a_proxima_chamada(harness: Harness) -> None:
    st, exec_, sid, _oid = _preparar(harness)
    s = st.scheduler.get_settings()
    s.exploracao_max_chamadas_ia = 500
    _chamadas(st, 3, sid, saida=10_000)                                 # 3 chamadas iguais, a média é a da próxima
    gasto = costs.spent_usd(st.db, st.cfg.file.ai.prices, run_id=RUN, so_exploratorias=True)
    assert gasto > 0
    s.exploracao_max_usd = round(gasto * 1.34, 6)                       # cabe a próxima (gasto + gasto/3 = 1,333 gasto)
    assert exec_._teto_da_exploracao(RUN, sid, s) is None              # noqa: SLF001
    s.exploracao_max_usd = round(gasto * 1.30, 6)                       # a próxima não cabe: para ANTES de cruzar o teto
    motivo = exec_._teto_da_exploracao(RUN, sid, s)                     # noqa: SLF001
    assert motivo is not None and motivo.startswith("Teto da exploração: US$ ")
    assert "próxima chamada custaria cerca de US$" in motivo and f"limite US$ {s.exploracao_max_usd:.2f}" in motivo


def test_a_primeira_chamada_nao_tem_media_e_passa(harness: Harness) -> None:
    st, exec_, sid, _oid = _preparar(harness)
    s = st.scheduler.get_settings()
    s.exploracao_max_chamadas_ia, s.exploracao_max_usd = 500, 0.01
    assert exec_._teto_da_exploracao(RUN, sid, s) is None              # sem chamada feita: nada a estimar  # noqa: SLF001


async def test_a_etapa_exploratoria_nao_sofre_o_orcamento_da_etapa_livre(harness: Harness,
                                                                        monkeypatch: Any) -> None:
    st, exec_, sid, oid = _preparar(harness)
    s = st.scheduler.get_settings()
    s.exploracao_max_chamadas_ia, s.exploracao_max_usd = 500, 0
    st.cfg.file.ai.step_budget.enabled = True
    conferidas: list[str] = []
    monkeypatch.setattr(exec_, "_conferir_orcamento_da_etapa",
                        lambda run_id, objective_id, step_id, *a, **k: conferidas.append(step_id))

    async def chamar() -> tuple[str, Usage]:
        return "ok", Usage(calls=1)

    assert await exec_._ai(RUN, oid, chamar, step_id=sid) == "ok"      # noqa: SLF001
    assert conferidas == []                                             # a exploratória tem o teto próprio
    comum = f"{RUN}:android-01:v1:ver_ajuda"
    st.db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, postcondition,"
                  " timeout_s, max_attempts, status, app_id) VALUES (?,?,?,?,1,2,'ver_ajuda','t','g',?,60,3,'running','qa-messenger')",
                  (comum, RUN, oid, "android-01", '{"kind":"model_judged","value":"v","description":"d"}'))
    assert await exec_._ai(RUN, oid, chamar, step_id=comum) == "ok"    # noqa: SLF001
    assert conferidas == [comum]                                        # a etapa comum segue conferida


async def test_o_aviso_de_efeito_liberado_sai_uma_vez_por_execucao_e_chave(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    _semear_efeito(st)
    assert await _porta(st) is None and len(_avisos(st)) == 1
    assert await _porta(st) is None and await _porta(st) is None       # a recuperação refaz a etapa: a porta passa de novo
    assert len(_avisos(st)) == 1
    # outra chave na mesma execução é outro efeito: avisa
    st.db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on, side_effect,"
        " commit_guard, postcondition, timeout_s, max_attempts, status, capability, bindings, exploratoria)"
        " VALUES (?,?,?,'android-01',1,2,'explorar_marcar_mensagem','t','g','[]',1,'[]',"
        "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',300,1,'ready',NULL,'{}',1)",
        ("run-x:android-01:v1:explorar_marcar_mensagem", "run-x", "run-x:android-01"))
    assert await _porta(st, "explorar_marcar_mensagem") is None
    assert [a["chave"] for a in _avisos(st)] == ["explorar_enviar_mensagem", "explorar_marcar_mensagem"]
