"""31.138: o passe que põe `open_app` na 1ª receita dos fluxos ensinados antes do 31.121 (e do 31.139).

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA). O estado "salvo antes do 31.121" é montado
tirando o `open_app` da receita que o `save` de hoje grava.
"""
from __future__ import annotations

import json

from app.training.reparo_da_abertura import abrir_nas_receitas, contar_sem_abertura

from .conftest import Harness
from .test_treino_partida_e_pos_condicao import _gravada_dentro_do_app, _proposta


async def _salva_como_antes_do_31121(harness: Harness) -> tuple[object, str, int]:
    st, sid = await _gravada_dentro_do_app(harness)
    pos = {"kind": "model_judged", "value": "", "description": "conversas abertas"}
    salvo = await st.skills.save(sid, proposal=_proposta(pos), profile_ids=[], group_ids=[])
    rid, acoes = next((int(r["id"]), json.loads(r["actions"])) for r in st.db.query(
        "SELECT id, actions FROM recipes WHERE learned_from_step=? AND step_key='abrir'", (f"training:{sid}",)))
    assert [a["tool"] for a in acoes] == ["open_app", "tap"]                      # o save de hoje já abre o app
    st.db.execute("UPDATE recipes SET actions=? WHERE id=?", (json.dumps(acoes[1:]), rid))
    return st, str(salvo["flow_id"]), rid


async def test_o_ensaio_conta_e_o_passe_troca_a_receita_viva_uma_vez(harness: Harness) -> None:
    st, fid, velha = await _salva_como_antes_do_31121(harness)
    db = st.db
    assert contar_sem_abertura(db) == 1
    ensaio = abrir_nas_receitas(db, lambda _pid: {}, escrever=False)
    assert ensaio["antes"] == 1 and ensaio["trocadas"] == 1 and ensaio["depois"] == 0
    assert ensaio["trocas"] == [{"fluxo": fid, "etapa": "abrir", "de": velha, "para": None}]
    assert db.scalar("SELECT status FROM recipes WHERE id=?", (velha,)) == "active"           # nada gravado

    feito = abrir_nas_receitas(db, lambda _pid: {}, escrever=True)
    (troca,) = feito["trocas"]  # type: ignore[misc]
    nova = troca["para"]
    assert feito["antes"] == 1 and feito["depois"] == 0 and isinstance(nova, int) and nova != velha
    assert [a["tool"] for a in json.loads(db.scalar("SELECT actions FROM recipes WHERE id=?", (nova,)))] == [
        "open_app", "tap"]
    assert db.scalar("SELECT status FROM recipes WHERE id=?", (velha,)) == "superseded"
    assert db.scalar("SELECT status FROM recipes WHERE id=?", (nova,)) == "active"
    assert db.scalar("SELECT COUNT(*) FROM learning_transitions WHERE item_ref=?", (f"receita:{nova}",)) >= 1  # trilha
    assert db.scalar("SELECT status FROM flows WHERE id=?", (fid,)) == "active"                # o fluxo não muda

    de_novo = abrir_nas_receitas(db, lambda _pid: {}, escrever=True)
    assert de_novo["trocadas"] == 0 and de_novo["antes"] == 0                                  # idempotente


async def test_a_receita_que_ja_abre_o_app_e_a_etapa_seguinte_ficam_como_estao(harness: Harness) -> None:
    st, sid = await _gravada_dentro_do_app(harness)
    pos = {"kind": "model_judged", "value": "", "description": "conversas abertas"}
    await st.skills.save(sid, proposal=_proposta(pos), profile_ids=[], group_ids=[])
    antes = [tuple(r) for r in st.db.query("SELECT id, status, actions FROM recipes ORDER BY id")]
    r = abrir_nas_receitas(st.db, lambda _pid: {}, escrever=True)
    assert r["antes"] == 0 and r["trocadas"] == 0
    assert [tuple(x) for x in st.db.query("SELECT id, status, actions FROM recipes ORDER BY id")] == antes
