"""31.149 (P-014 b): a correção ensinada volta ao comando que falhou.

Medido em 06/10: das sessões de correção salvas, nenhuma receita casava com o plano do comando que falhou. A receita
morava na chave da etapa da PROPOSTA, e o planejador livre, na vez seguinte, refazia a etapa que falhou do mesmo jeito.

O que estes testes protegem:
* o `save` de uma sessão de correção grava a demonstração na chave da etapa que FALHOU (`template_hash` e chave dela),
  e a loja de receitas a acha por essa chave;
* o parâmetro da proposta é renomeado para o do objetivo que falhou pelo valor; a resposta diz "esta correção vale para
  o comando <molde>";
* não liga, com o motivo, quando a receita usaria um nome que a execução não tem, ou quando há efeito;
* a sessão comum (sem origem) não ganha `correcao`.

Nível de prova: `simulated` (harness com aparelho falso, sem IA).
"""
from __future__ import annotations

import json
from typing import Any

from .conftest import Harness
from .test_modo_treinamento import _no_controle
from .test_treino_segredo_na_gravacao import _arvore, _el
from .test_treino_validacao_do_salvar import _etapa

TS = "2026-10-06T01:00:00.000Z"
PKG = "com.pocqa.messenger"
HASH_DA_FALHA = "h-da-etapa-que-falhou"


def _falha(st: Any, *, chave: str, parametros: str, efeito: int = 0) -> tuple[str, str]:
    """Uma execução do comando "abra a conversa com QA-001" cuja etapa `chave` falhou, com identidade de receita."""
    run, step = "r-c1", f"r-c1:android-01:v1:{chave}"
    st.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at)"
                  " VALUES (?,?,?,?,?,?,?)", (run, f"k-{run}", "abra a conversa com QA-001", "execute", "failed",
                                              '["android-01"]', TS))
    st.db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters) VALUES (?,?,?,?,1,?)",
                  (f"{run}:o1", run, "android-01", "failed", parametros))
    st.db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                  " postcondition, timeout_s, max_attempts, status, status_detail, template_hash, side_effect, app_id)"
                  " VALUES (?,?,?,?,1,1,?,'Abrir a conversa','abrir a conversa','{}',60,3,'failed','não abriu',?,?,"
                  " 'qa-messenger')", (step, run, f"{run}:o1", "android-01", chave, HASH_DA_FALHA, efeito))
    st.db.execute("INSERT INTO attempts(id, step_id, number, status, started_at, error) VALUES (?,?,1,'failed',?,'x')",
                  (f"{step}:a1", step, TS))
    return run, step


async def _corrigir(harness: Harness, *, falha: tuple[str, str] | None, chaves: tuple[str, str],
                    parametros: list[dict[str, str]], persona: str | None = None, n: int = 1) -> dict[str, Any]:
    """Grava um toque e o texto "QA-001" (a busca do contato), para, e salva a proposta de duas etapas."""
    st, rt, lease = await _no_controle(harness)
    origem = st.training.origem_da_falha(*falha) if falha else None
    sid = st.training.start("android-01", intent="Corrigir a etapa", lease_id=lease, app_id="qa-messenger",
                            origem=origem)["id"]
    busca = _el("e1", text="Buscar", rid=f"{PKG}:id/busca", clickable=True, bounds=(0, 0, 100, 100))
    st.training.record(rt, {"type": "tap", "x": 50, "y": 50}, _arvore(busca))
    campo = _el("e2", rid=f"{PKG}:id/campo", editable=True, focused=True, bounds=(0, 100, 400, 200))
    st.training.record(rt, {"type": "text", "text": "QA-001"}, _arvore(campo))
    st.training.stop(sid, lease_id=lease)
    st.db.execute("UPDATE training_sessions SET profile_id=? WHERE id=?", (persona, sid))     # quem ensinou (30.81)
    proposta = {"summary": "abrir conversa", "command_template": f"abra a conversa {n} pela busca com {{nome}}",
                "app_id": "qa-messenger", "parameters": parametros, "discarded": [], "questions": [],
                "steps": [_etapa(chaves[0], [1]), _etapa(chaves[1], [2])]}
    salvo = await st.skills.save(sid, proposal=proposta, profile_ids=[], group_ids=[])
    st.devices.release_control(rt, lease)
    return {"sid": sid, **salvo}


async def test_a_correcao_vai_para_a_chave_da_etapa_que_falhou_com_o_parametro_do_comando(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    from .test_treino_escopo_ao_provar import _persona
    falha = _falha(st, chave="abrir_conversa", parametros='{"contato": "QA-001"}')
    pid = _persona(st, "ensinou.correcao")
    salvo = await _corrigir(harness, falha=falha, chaves=("tocar_busca", "digitar_nome"),
                            parametros=[{"name": "nome", "example": "QA-001", "description": ""}], persona=pid)
    lig = salvo["correcao"]
    assert lig["ligada"] is True and lig["step_key"] == "abrir_conversa", lig
    assert lig["texto"] == "esta correção vale para o comando “abra a conversa com {contato}”"
    receita = st.db.one("SELECT * FROM recipes WHERE id=?", (lig["recipe_id"],))
    assert receita["step_hash"] == HASH_DA_FALHA and receita["step_key"] == "abrir_conversa"
    assert receita["learned_from_step"] == f"training:{salvo['sid']}"
    assert "{contato}" in receita["actions"] and "{nome}" not in receita["actions"]   # o nome do comando que falhou
    assert '"tap"' in receita["actions"] and "type_text" in receita["actions"]         # as duas etapas, em sequência
    achada = st.scheduler.executor.recipes.find(PKG, receita["app_version"], HASH_DA_FALHA,
                                                signature=receita["app_signature"], variant=receita["variant"],
                                                persona=pid)          # 30.81: vale para quem ensinou até a liberação
    assert achada is not None and achada["id"] == receita["id"]
    ev = st.db.one("SELECT data FROM events WHERE kind='log' AND data LIKE ? ORDER BY id DESC LIMIT 1",
                   (f"%{salvo['sid']}%",))
    assert json.loads(ev["data"])["correcao_ligada"] is True


async def test_nome_que_a_execucao_nao_tem_ou_efeito_nao_ligam_e_dizem_por_que(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    falha = _falha(st, chave="abrir_conversa", parametros="{}")
    salvo = await _corrigir(harness, falha=falha, chaves=("tocar_busca", "digitar_nome"),
                            parametros=[{"name": "nome", "example": "QA-001", "description": ""}])
    assert {k: v for k, v in salvo["correcao"].items() if k != "licao"} == {"ligada": False, "step_key": "abrir_conversa",
                                 "motivo": "a receita usaria {nome}, que a execução que falhou não tem"}
    assert st.db.scalar("SELECT COUNT(*) FROM recipes WHERE step_hash=?", (HASH_DA_FALHA,)) == 0
    st.db.execute("UPDATE steps SET side_effect=1, status='failed' WHERE id=?", (falha[1],))
    salvo = await _corrigir(harness, falha=falha, chaves=("tocar_busca", "digitar_nome"),
                            parametros=[{"name": "nome", "example": "QA-001", "description": ""}], n=2)
    assert salvo["correcao"]["ligada"] is False and "efeito" in salvo["correcao"]["motivo"]
    comum = await _corrigir(harness, falha=None, chaves=("tocar_busca", "digitar_nome"),
                            parametros=[{"name": "nome", "example": "QA-001", "description": ""}], n=3)
    assert "correcao" not in comum
