"""31.298 (ADR-084): o aviso no Telegram ao começar e ao concluir a exploração fora do catálogo.

Prova `simulated`: o `data` é o dos eventos `exploracao.iniciada` e `exploracao.concluida`, sem Telegram. Sai na hora (não
na janela da rotina), nunca pede o dono, e leva só ids, contagens e dinheiro: nem o pedido, nem a chave da etapa, nem o
nome de persona. A exploração que só reaproveitou a receita descoberta não avisa o início (não custa IA). O fim sai do
evento que a transição da etapa exploratória emite, e o custo vem do banco.
`real`: `not_run` (o Telegram real depende do canal ligado e do sim do dono para a exploração).
"""
from __future__ import annotations

import json
from typing import Any

from app.models import StepStatus
from app.modules.avisos.domain.mensagem import (AGORA, ROTINA, ROTULOS, ROTULOS_AGRUPADOS, TIPOS_DA_JANELA,
                                                aviso_de_evento, entrega_do_tipo, titulo_agrupado)
from app.modules.avisos.infrastructure.servico import KINDS_QUE_AVISAM
from app.planning import exploracao as ex

from .conftest import Harness
from .test_etapas_descobertas import _semear

PAINEL = "http://painel.local:8000"
RUN = "r-20261010140000-abc123"
INICIADA, CONCLUIDA = "exploracao.iniciada", "exploracao.concluida"


def _inicio(**kw: Any) -> dict[str, object]:
    return {"run_id": RUN, "etapas": ["explorar_ver_caixa_lixo"], "reaproveitadas": [], "app_ids": ["outlook"],
            "tetos": {"acoes": 25, "chamadas_ia": 30, "usd": 0.6}, **kw}


def test_os_dois_tipos_saem_na_hora_sem_pedir_o_dono_e_tem_rotulo_e_plural() -> None:
    for tipo in (INICIADA, CONCLUIDA):
        assert tipo in KINDS_QUE_AVISAM and tipo in ROTULOS and tipo in ROTULOS_AGRUPADOS
        assert entrega_do_tipo(tipo) == AGORA and tipo not in TIPOS_DA_JANELA
    assert "2 explorações começaram" in titulo_agrupado(INICIADA, 2)


def test_o_inicio_diz_os_tetos_e_que_so_le_sem_o_pedido_nem_a_chave() -> None:
    aviso = aviso_de_evento(INICIADA, _inicio(), 9, PAINEL)
    assert aviso is not None and aviso.nivel == ROTINA
    assert aviso.titulo.endswith("🧭 Explorando um app fora do catálogo")
    assert "Tetos: 25 ações, 30 chamadas de IA e US$ 0.60." in aviso.corpo.splitlines()
    assert "Só leitura e navegação" in aviso.corpo and aviso.link == f"{PAINEL}/#/execucoes/{RUN}"
    assert "lixo" not in aviso.titulo + aviso.corpo and "outlook" not in (aviso.titulo + aviso.corpo).lower()


def test_so_reaproveitar_a_receita_nao_avisa_o_inicio_e_id_ruim_nao_avisa_nada() -> None:
    assert aviso_de_evento(INICIADA, _inicio(app_ids=[], reaproveitadas=["explorar_ver_caixa_lixo"]), 9, PAINEL) is None
    assert aviso_de_evento(INICIADA, _inicio(run_id="r-1"), 9, PAINEL) is None
    assert aviso_de_evento(CONCLUIDA, {"resultado": "concluida"}, 9, PAINEL) is None


def test_o_fim_diz_o_resultado_o_custo_e_as_chamadas() -> None:
    def _fim(resultado: str, **kw: Any) -> Any:
        return aviso_de_evento(CONCLUIDA, {"run_id": RUN, "resultado": resultado, **kw}, 11, PAINEL)

    ok = _fim("concluida", custo_usd=0.0421, chamadas=7)
    assert ok is not None and ok.titulo.endswith("✅ Exploração concluída")
    assert "Custo: US$ 0.04 em 7 chamadas de IA." in ok.corpo.splitlines()
    assert "receita candidata" in ok.corpo
    teto = _fim("parou_no_teto", custo_usd=0.61, chamadas=30)
    assert teto is not None and "parou no teto" in teto.titulo and "Parou sem concluir" in teto.corpo
    sem_custo = _fim("falhou")
    assert sem_custo is not None and "Custo: indisponível" in sem_custo.corpo and "não concluiu" in sem_custo.titulo
    inicio = aviso_de_evento(INICIADA, _inicio(), 9, PAINEL)
    assert inicio is not None and inicio.chave != ok.chave                          # início e fim são avisos distintos


async def test_a_etapa_exploratoria_que_termina_emite_o_fim_com_o_resultado(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    passo = ex.passo_da_exploracao("ver a caixa de lixo", ex.classificar("ver a caixa de lixo"), app_id=None,
                                   nome_do_app="QA")
    repo = st.runs.repo
    for n, (alvo, detalhe, esperado) in enumerate([
            (StepStatus.succeeded, None, "concluida"),
            (StepStatus.failed, "Teto da exploração: 30 chamadas de IA (limite 30).", "parou_comp_teto"),
            (StepStatus.failed, "o app fechou", "falhou")]):
        rid = f"r-2026101014000{n}-aaaaaa"
        _semear(st, rid, passo)
        sid = f"{rid}:android-01:v1:{passo.key}"
        st.db.execute("UPDATE steps SET status='verifying' WHERE id=?", (sid,))
        repo.transition_step(sid, alvo, detail=detalhe)
        eventos = [json.loads(r["data"]) for r in st.db.query(
            "SELECT data FROM events WHERE kind=? AND run_id=?", (CONCLUIDA, rid))]
        assert [e["resultado"] for e in eventos] == [esperado.replace("parou_comp_teto", "parou_no_teto")]
        assert eventos[0]["run_id"] == rid and "pedido" not in json.dumps(eventos[0])
    # a etapa que não é exploratória não emite nada
    _semear(st, "r-20261010140009-bbbbbb", passo, exploratoria=False)
    sid = f"r-20261010140009-bbbbbb:android-01:v1:{passo.key}"
    st.db.execute("UPDATE steps SET status='verifying' WHERE id=?", (sid,))
    repo.transition_step(sid, StepStatus.succeeded)
    assert st.db.scalar("SELECT COUNT(*) FROM events WHERE kind=? AND run_id=?", (CONCLUIDA, "r-20261010140009-bbbbbb")) == 0


def test_o_servico_poe_o_custo_e_as_chamadas_da_execucao_no_fim(tmp_path: Any) -> None:
    from .test_avisos_objetivo_parado import _run
    from .test_avisos_servico import AQUI, Relogio, _backend, _cfg
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio())
    _run(banco, RUN, chave="k-explora")
    banco.execute("INSERT INTO ai_calls(ts, run_id, role, model, provider, output_tokens) VALUES (?,?,?,?,?,?)",
                  ("2026-10-10T14:00:00.000Z", RUN, "decide", "claude-sonnet-5-5", "anthropic", 1_000_000))
    dados = servico._com_o_custo_da_exploracao({"run_id": RUN, "resultado": "concluida"})
    assert dados is not None and dados["chamadas"] == 1 and float(dados["custo_usd"]) > 0.5      # type: ignore[arg-type]
    assert servico._com_o_custo_da_exploracao({"resultado": "x"}) == {"resultado": "x"}         # sem run_id, não mexe
