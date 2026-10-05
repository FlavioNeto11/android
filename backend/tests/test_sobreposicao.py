"""Item 31.40: a recusa do juiz por SOBREPOSIÇÃO insere a limpeza opcional antes da etapa, uma vez por objetivo.

Achado real (3894c1, android-09, 04/10 12:00Z, US$ 0,306): sem a etapa do banner, o planejador pôs "lista visível" no
`open_url`; o juiz recusou 3 vezes porque um diálogo do site cobria parte da lista, veio o plano v2 igual e a leitura
gastou mais 3 tentativas. Agora o juiz diz `sobreposicao=true` e, numa etapa SEM efeito, a etapa não se repete: o plano
revisado põe antes dela uma etapa `opcional` de limpeza (31.36) e retoma da tela atual. O juiz não afrouxa.

Três limites travados aqui: (1) uma vez por objetivo; (2) só em etapa sem efeito; (3) o juiz não aceita conteúdo coberto
(o "sim" continua sendo o único que comprova). E o ajuste (b): o orçamento de chamadas da ação (18.3) que corta uma etapa
de LEITURA dá o desfecho do 31.38 (dado ausente, um replano por objetivo), não a falha genérica.

Nível de prova: `simulated` (harness na porta 5640, provedor simulado; nenhuma IA paga).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.planning.provider import Verdict, Usage

from .conftest import Harness

TERMINAIS = ("completed", "completed_with_issues", "failed", "waiting_user", "needs_input", "uncertain")


def _juiz(inner: Any, chave: str, *, cobertas: int) -> list[str]:
    """O verificador da etapa `chave`: as `cobertas` primeiras respostas são "não, algo cobre o alvo"; depois, "sim"."""
    verify0 = inner.verify
    vistos: list[str] = []

    async def verify(req: Any) -> Any:
        if req.ctx.step_key != chave:
            return await verify0(req)
        vistos.append(chave)
        if len(vistos) <= cobertas:
            return Verdict(satisfied="no", evidence="[simulado] um aviso cobre a lista", sobreposicao=True), Usage()
        return await verify0(req)

    inner.verify = verify
    return vistos


def _revisoes(h: Harness, run_id: str, motivo: str) -> int:
    return int(h.state.db.scalar(                                                      # type: ignore[union-attr]
        "SELECT COUNT(*) FROM plan_versions WHERE objective_id IN (SELECT id FROM objectives WHERE run_id=?) "
        "AND reason LIKE ?", (run_id, f"Recuperação automática ({motivo})%")) or 0)


def _etapas(h: Harness, run_id: str, chave: str) -> list[Any]:
    return h.state.db.query("SELECT key, status, attempts, plan_version, opcional, status_detail FROM steps "  # type: ignore[union-attr]
                            "WHERE run_id=? AND key IN (?, ?) ORDER BY plan_version, seq",
                            (run_id, chave, f"limpar_antes_{chave}"))


async def test_recusa_por_sobreposicao_insere_a_limpeza_e_nao_repete_a_etapa(harness: Harness) -> None:
    harness.pular_o_tempo()
    harness.encurtar_verificacao(1.5)
    vistos = _juiz(harness.ai.inner, "verify_sent", cobertas=1)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    etapas = _etapas(harness, run.id, "verify_sent")
    assert _revisoes(harness, run.id, "sobreposição") == 1
    v1 = [e for e in etapas if e["plan_version"] == 1]
    assert [(e["key"], e["status"], e["attempts"]) for e in v1] == [("verify_sent", "failed", 1)]   # sem 2ª tentativa
    assert "(sobreposição)" in v1[0]["status_detail"]
    v2 = {e["key"]: e for e in etapas if e["plan_version"] == 2}
    assert v2["limpar_antes_verify_sent"]["opcional"] == 1
    assert v2["limpar_antes_verify_sent"]["status"] in ("succeeded", "skipped")
    assert v2["verify_sent"]["status"] == "succeeded"                 # comprovada pelo "sim" do juiz, depois da limpeza
    assert len(vistos) >= 2
    assert harness.state.repo.run_row(run.id)["status"] == "completed"                  # type: ignore[union-attr]


async def test_a_limpeza_entra_uma_vez_por_objetivo_e_o_juiz_nao_afrouxa(harness: Harness) -> None:
    harness.pular_o_tempo()
    harness.encurtar_verificacao(1.5)
    _juiz(harness.ai.inner, "verify_sent", cobertas=1000)            # o aviso nunca sai
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert _revisoes(harness, run.id, "sobreposição") == 1             # a segunda recusa segue o caminho de sempre
    assert harness.state.db.scalar("SELECT COUNT(*) FROM steps WHERE run_id=? AND key='verify_sent' "  # type: ignore[union-attr]
                                   "AND status='succeeded'", (run.id,)) == 0      # coberto nunca vira comprovado
    assert harness.state.repo.run_row(run.id)["status"] != "completed"                  # type: ignore[union-attr]


async def test_etapa_com_efeito_nao_ganha_a_limpeza(harness: Harness) -> None:
    harness.pular_o_tempo()
    harness.encurtar_verificacao(1.5)
    _juiz(harness.ai.inner, "send_message", cobertas=1000)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert _revisoes(harness, run.id, "sobreposição") == 0
    assert harness.state.db.scalar("SELECT COUNT(*) FROM steps WHERE run_id=? AND key LIKE 'limpar_antes_%'",  # type: ignore[union-attr]
                                   (run.id,)) == 0


async def test_orcamento_da_acao_numa_leitura_e_dado_ausente(harness: Harness, monkeypatch: Any) -> None:
    """31.40 (b): o 18.3 cortando a etapa de LEITURA dá o desfecho do 31.38 — um replano por objetivo, depois final."""
    from .test_dado_ausente import _ator, _etapa, _plano, catalogo_qa  # noqa: F401 - o catálogo do QA de leitura
    from app.planning.capabilities import Capability, CapabilityCatalog
    from app.planning.catalog import register, unregister
    from .fake_device import PKG as QA

    register(QA, CapabilityCatalog(QA, [
        Capability(key="QA_LER_PROTOCOLO", title="Ler o protocolo", goal="Ler o número do protocolo na caixa.",
                   post_kind="model_judged", post_value="protocolo lido", post_description="O protocolo foi lido.",
                   saidas=("protocolo",), max_attempts=3)]))
    try:
        executor = harness.state.scheduler.executor                                   # type: ignore[union-attr]
        est = SimpleNamespace(chamadas=SimpleNamespace(p50=1, p90=2), amostras=9)

        def orcamento(app: Any, acao: Any, **_: Any) -> Any:
            return (2, est) if acao == "QA_LER_PROTOCOLO" else None

        monkeypatch.setattr(executor.historico, "orcamento_de_chamadas", orcamento)
        inner, historicos = harness.ai.inner, []
        _plano(inner, _etapa())
        _ator(inner, bloqueia=False, historicos=historicos)        # só olha a tela: o orçamento (2) corta antes do teto
        run = harness.run(["android-01"], command="Abra o QA Messenger e leia o protocolo.")
        await harness.wait_run(run.id, statuses=TERMINAIS)
        etapas = harness.state.db.query("SELECT status, plan_version, status_detail FROM steps WHERE run_id=? "  # type: ignore[union-attr]
                                        "AND key='ler' ORDER BY plan_version", (run.id,))
        assert [(e["status"], e["plan_version"]) for e in etapas] == [("failed", 1), ("failed", 2)]
        assert all(e["status_detail"].startswith("Dado ausente:") and "orçamento" in e["status_detail"] for e in etapas)
        assert _revisoes(harness, run.id, "dado ausente") == 1
        assert harness.state.db.scalar("SELECT status FROM objectives WHERE run_id=?", (run.id,)) == "failed"  # type: ignore[union-attr]
    finally:
        unregister(QA)


# ---------------------------------------------------------------- 31.40 b: a limpeza que age e se comprova
# Achado real (95d10f, android-09, 04/10 13:20Z): a limpeza entrou, mas o ator deu step_done sem tocar em nada e ela só
# tinha a prova local (sem juiz), então nunca se comprovava. Agora: (i) com o elemento que cobria conhecido, a árvore
# decide sem IA; sem ele, UM julgamento por limpeza; (ii) o ator recebe o elemento; (iii) step_done sem toque não vale.

def _juiz_com_ref(inner: Any, chave: str, *, cobertas: int) -> dict[str, int]:
    """Como `_juiz`, citando em `cobre` o 1º elemento da tela; conta as chamadas de verificação por etapa."""
    verify0 = inner.verify
    chamadas: dict[str, int] = {}

    async def verify(req: Any) -> Any:
        chamadas[req.ctx.step_key] = chamadas.get(req.ctx.step_key, 0) + 1
        if req.ctx.step_key == chave and chamadas[chave] <= cobertas:
            ref = req.screen.tree.elements[0].id if req.screen.tree is not None and req.screen.tree.elements else None
            return Verdict(satisfied="no", evidence="[simulado] um aviso cobre a lista", sobreposicao=True,
                           cobre=ref), Usage()
        return await verify0(req)

    inner.verify = verify
    return chamadas


def _ator_que_fecha(inner: Any) -> None:
    """O ator da limpeza toca (no "X", aqui um ponto inócuo do aparelho falso) e depois declara a etapa feita."""
    from app.planning.provider import Decision
    decide0 = inner.decide
    vezes: dict[str, int] = {}

    async def decide(req: Any) -> Any:
        if not req.ctx.step_key.startswith("limpar_antes_"):
            return await decide0(req)
        vezes[req.ctx.step_key] = vezes.get(req.ctx.step_key, 0) + 1
        if vezes[req.ctx.step_key] == 1:
            return Decision(tool="tap", args={"x": 1, "y": 1, "is_commit_action": False,
                                              "rationale": "[simulado] fecha o aviso"}), Usage()
        return Decision(tool="step_done", args={"evidence": "[simulado] o aviso saiu",
                                                "rationale": "[simulado] fechado"}), Usage()

    inner.decide = decide


def _limpeza(h: Harness, run_id: str) -> Any:
    return h.state.db.one("SELECT id, status, status_detail, goal, variables FROM steps WHERE run_id=? AND key LIKE "  # type: ignore[union-attr]
                          "'limpar_antes_%' ORDER BY plan_version DESC, seq LIMIT 1", (run_id,))


async def test_o_elemento_que_cobre_vai_a_limpeza_e_a_arvore_decide_sem_juiz(harness: Harness) -> None:
    import json
    harness.pular_o_tempo()
    harness.encurtar_verificacao(1.5)
    chamadas = _juiz_com_ref(harness.ai.inner, "verify_sent", cobertas=1)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    limpeza = _limpeza(harness, run.id)
    variaveis = json.loads(limpeza["variables"])
    assert {"cobre_id", "cobre_texto", "cobre_bounds"} <= set(variaveis)           # (ii) o elemento viaja na etapa
    assert "na área" in limpeza["goal"]                                               # e o ator o recebe no objetivo
    assert chamadas.get("limpar_antes_verify_sent", 0) == 0                           # (i) a árvore decide: nenhum juiz
    # o elemento do aparelho falso não sai da tela: a limpeza não se comprova e fica pulada, sem travar o objetivo
    assert limpeza["status"] == "skipped"
    assert harness.state.db.scalar("SELECT status FROM steps WHERE run_id=? AND key='verify_sent' "  # type: ignore[union-attr]
                                   "ORDER BY plan_version DESC LIMIT 1", (run.id,)) == "succeeded"


async def test_elemento_que_saiu_da_arvore_comprova_a_limpeza_sem_ia(harness: Harness, monkeypatch: Any) -> None:
    from app.taskqueue import executor as modulo
    harness.pular_o_tempo()
    harness.encurtar_verificacao(1.5)
    monkeypatch.setattr(modulo, "ainda_cobre", lambda c, tree: False)                # o diálogo fechou
    chamadas = _juiz_com_ref(harness.ai.inner, "verify_sent", cobertas=1)
    _ator_que_fecha(harness.ai.inner)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    limpeza = _limpeza(harness, run.id)
    assert limpeza["status"] == "succeeded"
    assert chamadas.get("limpar_antes_verify_sent", 0) == 0
    assert harness.state.repo.run_row(run.id)["status"] == "completed"                  # type: ignore[union-attr]


async def test_sem_o_elemento_a_limpeza_tem_no_maximo_um_juiz(harness: Harness, monkeypatch: Any) -> None:
    from app.taskqueue import executor as modulo
    harness.pular_o_tempo()
    harness.encurtar_verificacao(1.5)
    monkeypatch.setattr(modulo, "cobertura_na_arvore", lambda tree, ref: None)       # nem id do juiz nem pista
    chamadas = _juiz_com_ref(harness.ai.inner, "verify_sent", cobertas=1)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    limpeza = _limpeza(harness, run.id)
    assert limpeza["variables"] in (None, "{}")
    assert chamadas.get("limpar_antes_verify_sent", 0) <= 1


async def test_step_done_sem_toque_na_limpeza_e_recusado(harness: Harness, monkeypatch: Any) -> None:
    from app.taskqueue import executor as modulo
    harness.pular_o_tempo()
    harness.encurtar_verificacao(1.5)
    _juiz_com_ref(harness.ai.inner, "verify_sent", cobertas=1)
    inner = harness.ai.inner
    decide0 = inner.decide

    async def decide(req: Any) -> Any:
        if req.ctx.step_key.startswith(modulo.PREFIXO_LIMPEZA):
            from app.planning.provider import Decision
            return Decision(tool="step_done", args={"evidence": "[simulado] nada a fechar",
                                                    "rationale": "[simulado] só olhou"}), Usage()
        return await decide0(req)

    inner.decide = decide
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    limpeza = _limpeza(harness, run.id)
    recusadas = harness.state.db.scalar(                                             # type: ignore[union-attr]
        "SELECT COUNT(*) FROM actions a JOIN attempts t ON t.id=a.attempt_id WHERE t.step_id=? AND a.tool='step_done' "
        "AND a.status='rejected' AND a.error='limpeza sem toque'", (limpeza["id"],))
    assert recusadas >= 1
    assert limpeza["status"] == "skipped"                                             # (iii) não conta como feita


def test_ainda_cobre_pela_identidade_e_pela_area() -> None:
    from app.automation.hierarchy import UiElement, UiTree
    from app.taskqueue.executor import Cobertura, ainda_cobre, cobertura_na_arvore

    def el(i: int, rid: str, texto: str, b: tuple[int, int, int, int], cls: str = "android.view.View") -> UiElement:
        return UiElement(id=f"e{i}", text=texto, desc="", resource_id=rid, class_name=cls, package="p", bounds=b,
                         clickable=True, enabled=True, focused=False, scrollable=False, editable=False, checked=False,
                         password=False)

    dialogo = el(2, "site:id/modal", "Abra o app", (100, 800, 980, 1600), "android.app.Dialog")
    lista = el(1, "site:id/lista", "", (0, 200, 1080, 2200))
    tela = UiTree(elements=[lista, dialogo], packages=["p"], sensitive=False)
    c = cobertura_na_arvore(tela, "e2")
    assert c == Cobertura("site:id/modal", "Abra o app", (100, 800, 980, 1600))
    assert cobertura_na_arvore(tela, None) == c                                       # sem id: a pista "Dialog"/"modal"
    assert cobertura_na_arvore(UiTree(elements=[lista], packages=["p"], sensitive=False), None) is None
    assert ainda_cobre(c, tela)
    assert not ainda_cobre(c, UiTree(elements=[lista], packages=["p"], sensitive=False))        # saiu da árvore
    fora = el(2, "site:id/modal", "Abra o app", (0, 2300, 1080, 2400))
    assert not ainda_cobre(Cobertura("site:id/modal", "Abra o app", (100, 800, 980, 1600)),
                           UiTree(elements=[lista, fora], packages=["p"], sensitive=False))     # deixou a área
    assert Cobertura.das_variaveis(c.variaveis()) == c
    assert Cobertura.das_variaveis({}) is None


async def test_o_teto_de_acoes_numa_limpeza_falha_a_limpeza_sem_juiz(harness: Harness, monkeypatch: Any) -> None:
    """29.90 (L1 da revisão do 29.87): a etapa de limpeza tem voltas a mais no laço (`LIMITE_DE_DIALOGOS`). O ator que
    só observa esgota as ações dela: sai pelo motivo do teto, sem juiz, e a limpeza opcional fica pulada sem travar a
    etapa seguinte."""
    import re
    from app.taskqueue import executor as modulo
    from app.planning.provider import Decision
    harness.pular_o_tempo()
    harness.encurtar_verificacao(1.5)
    chamadas = _juiz_com_ref(harness.ai.inner, "verify_sent", cobertas=1)
    inner = harness.ai.inner
    decide0 = inner.decide

    async def decide(req: Any) -> Any:
        if req.ctx.step_key.startswith(modulo.PREFIXO_LIMPEZA):
            return Decision(tool="observe_screen", args={"rationale": "[simulado] só olha"}), Usage()
        return await decide0(req)

    inner.decide = decide
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    limpeza = _limpeza(harness, run.id)
    assert re.search(r"Limite de \d+ ações por etapa", limpeza["status_detail"] or ""), limpeza["status_detail"]
    assert chamadas.get("limpar_antes_verify_sent", 0) == 0
    assert limpeza["status"] == "skipped"
