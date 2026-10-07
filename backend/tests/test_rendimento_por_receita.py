"""31.191: o rendimento de UMA receita (`GET /api/aprendizado/receitas/{id}/rendimento`, adendo v1.110).

O rendimento do ensino (31.177) era por sessão; a tela do Livro (31.196, Portal) mostra cada receita, também as de
execução, com o custo de IA que ela evitou.

O que estes testes protegem:
* a contagem por uso (real, prova, simulada) é a régua única do 31.177: sem IA, caiu na IA, outras;
* o custo evitado = etapas reais sem IA × o US$ médio da IA na MESMA etapa (`template_hash`), em execução não
  simulada; sem referência de custo na retenção, os dois campos vêm `null` (nunca inventados), e as contagens vêm 0;
* receita desconhecida dá 404 `receita_desconhecida`.

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

from typing import Any

from app.util import now_iso

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente
from .test_rendimento_do_ensino import TS, _etapa, _run, _tentativa


def _receita(st: Any, step_hash: str) -> int:
    st.db.execute("INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version,"
                  " status, actions, learned_from_step, replay_ok, replay_fail, created_at, last_used_at)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("com.pocqa.messenger", "1", "sig", "v", step_hash, "abrir_conversa", 1, "active", "[]",
                   "r-0:android-01:v1:abrir_conversa", 4, 1, now_iso(), TS))
    return int(st.db.scalar("SELECT id FROM recipes WHERE step_hash=?", (step_hash,)))


def _ia(st: Any, run: str, aid: str, usd: float) -> None:
    st.db.execute("INSERT INTO ai_calls(ts, run_id, role, model, provider, input_tokens, output_tokens, attempt_id, usd)"
                  " VALUES (?,?,?,?,?,?,?,?,?)", (TS, run, "decide", "claude-sonnet-5-5", "anthropic", 1, 1, aid, usd))


async def test_o_rendimento_da_receita_com_o_custo_evitado(harness: Harness) -> None:
    st = harness.state
    rec = _receita(st, "h-conversa")
    sem_ref = _receita(st, "h-sem-referencia")
    # duas etapas reais pela receita, uma de prova, e uma real em que ela caiu na IA
    _run(st, "r-1", chave="pedido-1")
    _tentativa(st, _etapa(st, "r-1", 1), rec, "recipe")
    _tentativa(st, _etapa(st, "r-1", 2), rec, "recipe")
    _ia(st, "r-1", _tentativa(st, _etapa(st, "r-1", 3), rec, "recipe>ai_actor"), 0.01)
    _run(st, "r-lote", chave="lote:aprendizado:1")
    _tentativa(st, _etapa(st, "r-lote", 1), rec, "recipe")
    # a referência de custo: a IA conduzindo a MESMA etapa (template_hash) em execuções reais, sem receita
    _run(st, "r-ia", chave="pedido-2")
    for n, usd in ((1, 0.02), (2, 0.04)):
        passo = _etapa(st, "r-ia", n)
        st.db.execute("UPDATE steps SET template_hash='h-conversa' WHERE id=?", (passo,))
        _ia(st, "r-ia", _tentativa(st, passo, 0, "ai_actor"), usd)
    async with _cliente(harness) as c:
        corpo = (await c.get(f"/api/aprendizado/receitas/{rec}/rendimento")).json()
        vazio = (await c.get(f"/api/aprendizado/receitas/{sem_ref}/rendimento")).json()
        nao = await c.get("/api/aprendizado/receitas/999999/rendimento")
    assert corpo["sem_ia"] == {"real": 2, "prova": 1, "simulada": 0}
    assert corpo["caiu_na_ia"] == {"real": 1, "prova": 0, "simulada": 0}
    assert corpo["origem"] == "execucao" and corpo["sessao"] is None and corpo["liberada"] is True
    assert corpo["reproducoes"] == {"ok": 4, "falha": 1} and corpo["ultimo_uso_em"] == TS
    assert corpo["custo_medio_ia_por_etapa_usd"] == 0.03 and corpo["custo_evitado_usd"] == 0.06
    assert corpo["usd_da_ia_na_retencao"] == 0.01
    assert vazio["custo_medio_ia_por_etapa_usd"] is None and vazio["custo_evitado_usd"] is None
    assert vazio["sem_ia"] == {"real": 0, "prova": 0, "simulada": 0}
    assert nao.status_code == 404 and nao.json()["detail"]["code"] == "receita_desconhecida"
