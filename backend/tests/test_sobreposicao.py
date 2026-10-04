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
