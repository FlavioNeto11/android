"""31.243: o usuário da conta da persona entra no mapa do registro inteiro e no contexto da falha do treino.

O 31.242 tirou o usuário da conta da NOTA da evidência; eventos, ações e o detalhe da etapa ainda o gravavam, porque o
mapa do registro (31.113 F1) só levava nome, sobrenome, exibição, e-mail e nascimento. Agora `mascara_da_persona.mapa`
leva também o usuário de cada conta da persona (`conta_<app>[_<host>]_usuario`), com e sem a arroba, com a regra de
sempre: o valor que está num parâmetro do comando fica. O contexto da falha do treino (`training/origem.py`) lê o banco
direto; ele passa a mascarar pelo barramento que o `Repository` liga (`EventBus.mascara` e `mascara_da_nota`), e a
linha antiga, gravada em claro, sai mascarada. As leituras e o treino seguem casando pela chave: o marcador volta ao
valor com as variáveis da persona (`resolver_texto`).

Nível de prova: `simulated` (banco de teste, valores sintéticos inventados; nenhum aparelho nem IA). Os asserts de
ausência usam `not in`.
"""
from __future__ import annotations

import json

from app.models import ActionStatus, StepStatus
from app.security import mascara_da_persona as m
from app.taskqueue.dado_da_persona import resolver_texto
from app.training.origem import contexto_da_falha

from .conftest import Harness
from .test_nota_do_juiz_mascarada import USUARIO, _usuario_da_conta
from .test_registro_mascarado_da_persona import _execucao

CONTA = "conta_instagram_usuario"


def test_o_mapa_leva_o_usuario_da_conta_com_e_sem_arroba() -> None:
    variaveis = {"perfil_nome": "Zelda", CONTA: "@usuario.inventado", "conta_instagram_usuario_2": "outro.inventado",
                 "conta_instagram_senha_nao_existe": "x"}
    trocas = m.mapa(variaveis, [], {})
    assert trocas["usuario.inventado"] == "{" + CONTA + "}" and trocas["@usuario.inventado"] == "@{" + CONTA + "}"
    assert trocas["@outro.inventado"] == "@{conta_instagram_usuario_2}"
    assert m.no_texto("@usuario.inventado said oi; usuario.inventado curtiu", trocas) == \
        "@{conta_instagram_usuario} said oi; {conta_instagram_usuario} curtiu"
    assert "usuario.inventado" not in m.mapa(variaveis, [], {"alvo": "@usuario.inventado"})   # o parâmetro vence
    assert "ab" not in m.mapa({CONTA: "ab"}, [], {})                                          # curto demais


async def test_evento_acao_e_detalhe_saem_sem_o_usuario_da_conta(harness: Harness) -> None:
    run_id, aid = _execucao(harness)
    conta = _usuario_da_conta(harness, run_id)
    st = harness.state
    assert st is not None
    sid = aid.rsplit(":", 1)[0]
    acao = st.repo.log_intent(aid, "tap", {"element_id": "e3", "texto": f"@{USUARIO}"}, f"Abrir o perfil @{USUARIO}",
                              side_effect=False)
    st.repo.finish_action(acao, ActionStatus.done, result={"visto": f"perfil de {USUARIO}"})
    st.repo.decision(f"o perfil @{USUARIO} abriu", run_id=run_id, instance_id="android-01", step_id=sid)
    st.repo.transition_step(sid, StepStatus.failed, detail=f"o perfil {USUARIO} não abriu")
    linhas = {
        "events": [str(r["message"]) + str(r["data"]) for r in st.db.query("SELECT message, data FROM events WHERE run_id=?",
                                                                           (run_id,))],
        "actions": [str(r["args"]) + str(r["rationale"]) + str(r["result"])
                    for r in st.db.query("SELECT args, rationale, result FROM actions WHERE attempt_id=?", (aid,))],
        "steps": [str(st.db.scalar("SELECT status_detail FROM steps WHERE id=?", (sid,)))],
    }
    for fonte, textos in linhas.items():
        assert textos and all(USUARIO not in t for t in textos), fonte
    assert any("@{" + conta + "}" in t for t in linhas["actions"]) and "{" + conta + "}" in linhas["steps"][0]
    # a leitura casa pela chave: o marcador volta ao valor com as variáveis da persona
    pid = st.db.scalar("SELECT profile_id FROM objectives WHERE run_id=?", (run_id,))
    args = json.loads(str(st.db.scalar("SELECT args FROM actions WHERE attempt_id=?", (aid,))))
    assert resolver_texto(args["texto"], st.repo.variaveis_da_persona(str(pid))) == f"@{USUARIO}"


async def test_o_contexto_do_treino_mascara_a_linha_antiga(harness: Harness) -> None:
    run_id, aid = _execucao(harness)
    st = harness.state
    assert st is not None
    sid = aid.rsplit(":", 1)[0]
    # linhas gravadas antes, em claro, direto no banco
    st.db.execute("UPDATE steps SET status='failed', status_detail=? WHERE id=?", (f"{USUARIO} não achado", sid))
    st.db.execute("UPDATE attempts SET status='failed', error=? WHERE id=?", (f"@{USUARIO} sem perfil", aid))
    st.db.execute("INSERT INTO evidence(run_id, instance_id, step_id, attempt_id, ts, kind, note, path, redacted)"
                  " VALUES (?,?,?,?,?,?,?,?,?)", (run_id, "android-01", sid, aid, "2026-10-07T10:10:00.000Z", "text",
                                                  f"@{USUARIO} said algo", None, 0))
    cru = json.dumps(contexto_da_falha(st.db, run_id, sid, aid), ensure_ascii=False)
    assert USUARIO in cru                                            # sem os mapas, como antes
    bus = st.bus
    assert bus.mascara is not None and bus.mascara_da_nota is not None
    mascarado = json.dumps(contexto_da_falha(st.db, run_id, sid, aid, trocas=bus.mascara(run_id, None, sid, aid),
                                             trocas_da_nota=bus.mascara_da_nota(run_id, sid, aid)), ensure_ascii=False)
    assert USUARIO not in mascarado and "_usuario}" in mascarado
