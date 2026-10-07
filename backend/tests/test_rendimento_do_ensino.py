"""Rendimento do ensino (`GET /api/training/{id}/rendimento`): o que UMA sessão gerou e quanto disso foi usado.

Medido em 06/10: 8 de 275 execuções foram planejadas por fluxo ensinado, todas lote de prova; 0 uso real. Aqui a
mesma régua vira dado por sessão.

O que estes testes protegem:
* a régua do uso: simulada; prova (prova de fluxo ou chave `lote:`); real (o resto);
* a receita conta a tentativa sem IA (só `recipe`, comprovou), a que caiu na IA (`recipe>…`) e o resto, com o US$ da
  IA das chamadas que ainda estão em `ai_calls`, e diz se está liberada fora de quem ensinou (30.81);
* o fluxo da sessão, as execuções dele por uso e o vizinho aceito em etapa de plano livre;
* sessão desconhecida é 404.

Nível de prova: `simulated` (harness com aparelho falso, sem IA).
"""
from __future__ import annotations

from typing import Any

from app.modules.learning.domain.rendimento import (Contagem, RendimentoDaReceita, TentativaDaReceita, da_receita,
                                                    tipo_de_uso)

from .conftest import Harness
from .test_etapa_pacotes_aceitos import BUSCA, _gravada_com_busca, _proposta
from .test_perfil_bloqueado_e_capacidades import _cliente

TS = "2026-10-06T22:00:00.000Z"


def test_a_regua_do_uso_e_a_soma_por_receita() -> None:
    assert tipo_de_uso(simulated=1, prova_fluxo_id=None, idempotency_key="k") == "simulada"
    assert tipo_de_uso(simulated=0, prova_fluxo_id="f1", idempotency_key="k") == "prova"
    assert tipo_de_uso(simulated=0, prova_fluxo_id=None, idempotency_key="lote:aprendizado:1") == "prova"
    assert tipo_de_uso(simulated=0, prova_fluxo_id=None, idempotency_key="pedido-1") == "real"
    r = da_receita(RendimentoDaReceita(id=7, step_key="buscar", app="x.y", status="active", liberada=False), [
        TentativaDaReceita(7, "real", "succeeded", "recipe"),
        TentativaDaReceita(7, "prova", "succeeded", "recipe"),
        TentativaDaReceita(7, "real", "succeeded", "recipe>ai_actor", usd=0.01),
        TentativaDaReceita(7, "real", "failed", "recipe"),
        TentativaDaReceita(8, "real", "succeeded", "recipe")])                     # outra receita
    assert (r.sem_ia, r.caiu_na_ia, r.outras) == (Contagem(real=1, prova=1), Contagem(real=1), Contagem(real=1))
    assert r.usd_da_ia_na_retencao == 0.01


def _run(st: Any, rid: str, *, chave: str, flow_id: str | None = None, prova: str | None = None) -> None:
    st.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at, flow_id,"
                  " prova_fluxo_id) VALUES (?,?,?,?,?,?,?,?,?)",
                  (rid, chave, "c", "execute", "completed", '["android-01"]', TS, flow_id, prova))
    st.db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,1)",
                  (f"{rid}:o", rid, "android-01", "completed"))


def _etapa(st: Any, rid: str, n: int, *, aceitos: str | None = None) -> str:
    sid = f"{rid}:s{n}"
    st.db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                  " postcondition, timeout_s, max_attempts, status, pacotes_aceitos) VALUES (?,?,?,?,1,?,?,'t','g','{}',"
                  "60,3,'succeeded',?)", (sid, rid, f"{rid}:o", "android-01", n, f"k{n}", aceitos))
    return sid


def _tentativa(st: Any, step: str, recipe: int, strategy: str, status: str = "succeeded") -> str:
    aid = f"{step}:a1"
    st.db.execute("INSERT INTO attempts(id, step_id, number, status, started_at, strategy, recipe_id)"
                  " VALUES (?,?,1,?,?,?,?)", (aid, step, status, TS, strategy, recipe))
    return aid


async def test_a_rota_mostra_o_que_a_sessao_gerou_e_o_uso_real(harness: Harness) -> None:
    st, sid = await _gravada_com_busca(harness)
    salvo = await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    receitas = [int(r["id"]) for r in st.db.query("SELECT id FROM recipes WHERE learned_from_step=? ORDER BY id",
                                                  (f"training:{sid}",))]
    assert receitas, "o ensino de teste deveria gerar ao menos uma receita"
    rec = receitas[0]
    _run(st, "r-real", chave="pedido-1", flow_id=salvo["flow_id"])
    _tentativa(st, _etapa(st, "r-real", 1), rec, "recipe")
    aid = _tentativa(st, _etapa(st, "r-real", 2), rec, "recipe>ai_actor")
    st.db.execute("INSERT INTO ai_calls(ts, run_id, role, model, provider, input_tokens, output_tokens, attempt_id)"
                  " VALUES (?,?,?,?,?,?,?,?)", (TS, "r-real", "decide", "claude-sonnet-5-5", "anthropic", 1000, 100, aid))
    _run(st, "r-lote", chave="lote:aprendizado:1", prova=salvo["flow_id"])
    _tentativa(st, _etapa(st, "r-lote", 1), rec, "recipe")
    _run(st, "r-livre", chave="pedido-2")                                         # plano livre que aceitou o vizinho
    _etapa(st, "r-livre", 1, aceitos=f'["{BUSCA}"]')

    async with _cliente(harness) as c:
        resp = await c.get(f"/api/training/{sid}/rendimento")
        assert (await c.get("/api/training/nao-existe/rendimento")).status_code == 404
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    da = next(r for r in corpo["receitas"] if r["id"] == rec)
    assert da["sem_ia"] == {"real": 1, "prova": 1, "simulada": 0} and da["caiu_na_ia"]["real"] == 1
    assert da["usd_da_ia_na_retencao"] > 0 and da["liberada"] is False             # 30.81: ninguém confirmou
    assert corpo["fluxo"]["id"] == salvo["flow_id"]
    assert corpo["execucoes_do_fluxo"] == {"real": 1, "prova": 1, "simulada": 0}
    assert corpo["vizinhos"] == [{"app": "qa-messenger", "pacote": BUSCA,
                                  "etapas_em_planos_livres": {"real": 1, "prova": 0, "simulada": 0}}]
    assert corpo["resumo"]["usado_de_verdade"] is True and corpo["resumo"]["receitas"] == len(receitas)
