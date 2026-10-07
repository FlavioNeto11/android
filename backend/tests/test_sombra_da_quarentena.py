"""31.202: o rendimento da receita ensinada sugere, em SOMBRA, liberar ou prender de volta. Nada se aplica.

O que estes testes protegem:
* o parecer (`domain/sombra_da_quarentena.parecer`): "liberaria" só com 3 usos reais sem IA de quem ensinou, em 2
  execuções distintas, e sem falha nas últimas 3; "prenderia de volta" só na liberada cujas 2 últimas tentativas reais
  FORA de quem ensinou falharam; nunca libera a receita com efeito externo nem a que mira a conta da própria persona;
* o passo da curadoria conta só o uso REAL (nem prova, nem lote, nem simulado), pela persona do objetivo da etapa;
* grava um sinal por receita ensinada ativa (sobrescrito a cada passo), e NADA muda na receita nem na trilha;
* o sinal fica fora da aba Sinais e se lê por `?kind=sombra_da_quarentena`;
* a montagem registra o passo.

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.modules.learning.domain.sombra_da_quarentena import (ReceitaEnsinada, Sugestao, TentativaReal,
                                                              mira_a_propria_conta, parecer)
from app.modules.learning.infrastructure.sombra_da_quarentena_sql import SombraDaQuarentena

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente
from .test_rendimento_do_ensino import TS, _etapa, _run, _tentativa

PACOTE = "com.pocqa.messenger"


def _t(run: str, persona: str, sem_ia: bool = True) -> TentativaReal:
    return TentativaReal(run_id=run, persona=persona, sem_ia=sem_ia)


def _r(*tentativas: TentativaReal, liberada: bool = False, com_efeito: bool = False,
       propria_conta: bool = False) -> ReceitaEnsinada:
    return ReceitaEnsinada(id=1, app=PACOTE, quem_ensinou="p1", liberada=liberada, com_efeito=com_efeito,
                           propria_conta=propria_conta, tentativas=tentativas)


def test_o_parecer() -> None:
    rendeu = (_t("r1", "p1"), _t("r1", "p1"), _t("r2", "p1"))
    p = parecer(_r(*rendeu))
    assert p.sugestao is Sugestao.LIBERARIA
    assert p.contagem["sem_ia_de_quem_ensinou"] == 3 and p.contagem["execucoes_sem_ia"] == 2
    assert parecer(_r(_t("r1", "p1"), _t("r1", "p1"), _t("r1", "p1"))).sugestao is Sugestao.NENHUMA   # 1 execução
    assert parecer(_r(*rendeu, _t("r3", "p1", sem_ia=False))).sugestao is Sugestao.NENHUMA         # falha recente
    assert parecer(_r(*rendeu, _t("r3", "p2", sem_ia=False))).sugestao is Sugestao.LIBERARIA       # a outra não conta
    assert parecer(_r(*rendeu, com_efeito=True)).sugestao is Sugestao.NENHUMA
    assert parecer(_r(*rendeu, propria_conta=True)).sugestao is Sugestao.NENHUMA
    caiu = (_t("r4", "p2", sem_ia=False), _t("r5", "p3", sem_ia=False))
    assert parecer(_r(*caiu, liberada=True)).sugestao is Sugestao.PRENDERIA_DE_VOLTA
    assert parecer(_r(*caiu, _t("r6", "p2"), liberada=True)).sugestao is Sugestao.NENHUMA          # voltou a render
    assert parecer(_r(*caiu, liberada=False)).sugestao is Sugestao.NENHUMA                         # já presa
    assert parecer(_r(_t("r1", "p1", sem_ia=False), _t("r2", "p1", sem_ia=False), liberada=True)).sugestao \
        is Sugestao.NENHUMA                                                  # a falha de quem ensinou não prende
    assert mira_a_propria_conta('[{"text": "{conta_instagram_usuario}"}]') and not mira_a_propria_conta('[{"x": 1}]')


def _sessao(st: Any, sid: str, persona: str) -> None:
    st.db.execute("INSERT INTO training_sessions(id, instance_id, profile_id, intent, status, created_at, updated_at)"
                  " VALUES (?,?,?,?,?,?,?)", (sid, "android-01", persona, "ensinar", "saved", TS, TS))


def _receita(st: Any, chave: str, sessao: str, acoes: list[object]) -> int:
    st.db.execute("INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version,"
                  " status, actions, learned_from_step, replay_ok, replay_fail, created_at)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (PACOTE, "1", "sig", "v", f"h-{chave}", chave, 1, "active", json.dumps(acoes),
                   f"training:{sessao}", 0, 0, TS))
    return int(st.db.scalar("SELECT id FROM recipes WHERE step_hash=?", (f"h-{chave}",)))


def _uso(st: Any, run: str, persona: str, receita: int, *etapas: str, chave: str | None = None) -> None:
    _run(st, run, chave=chave or f"pedido-{run}")
    st.db.execute("UPDATE objectives SET profile_id=? WHERE run_id=?", (persona, run))
    for n, strategy in enumerate(etapas, 1):
        _tentativa(st, _etapa(st, run, n), receita, strategy)


async def test_o_passo_grava_a_sombra_e_nada_se_aplica(harness: Harness) -> None:
    st = harness.state
    _sessao(st, "trn-1", "p1")
    rendeu = _receita(st, "abrir_conversa", "trn-1", [{"commit": False}])
    efeito = _receita(st, "mandar", "trn-1", [{"commit": True}])
    propria = _receita(st, "abrir_meu_perfil", "trn-1", [{"text": "{conta_qa_usuario}"}])
    for rec in (rendeu, efeito, propria):
        _uso(st, f"r-a{rec}", "p1", rec, "recipe", "recipe")
        _uso(st, f"r-b{rec}", "p1", rec, "recipe")
    _uso(st, "r-c-lote", "p1", rendeu, "recipe>ai_actor", chave="lote:aprendizado:1")     # lote: não é uso real
    antes = st.db.query("SELECT id, status, consecutive_fail FROM recipes ORDER BY id")
    trilha = st.db.scalar("SELECT COUNT(*) FROM learning_transitions")
    passo = SombraDaQuarentena(st.learning, st.db, liberada=lambda r: False)
    agora = datetime.now(timezone.utc)
    assert passo.executar(agora) == 1
    assert passo.executar(agora) == 1                                        # sobrescreve, não duplica
    sinais = {r["source_ref"]: r for r in st.db.query("SELECT * FROM learning_signals WHERE kind=?",
                                                      ("sombra_da_quarentena",))}
    assert {k: v["reason"] for k, v in sinais.items()} == {
        f"receita:{rendeu}": "liberaria", f"receita:{efeito}": "nenhuma", f"receita:{propria}": "nenhuma"}
    dados = json.loads(sinais[f"receita:{rendeu}"]["data"])
    assert dados["sem_ia_de_quem_ensinou"] == 3 and dados["regra"] == "31.202-v1" and dados["liberada"] is False
    assert st.db.query("SELECT id, status, consecutive_fail FROM recipes ORDER BY id") == antes      # nada se aplica
    assert st.db.scalar("SELECT COUNT(*) FROM learning_transitions") == trilha
    # liberada e falhando fora de quem ensinou: "prenderia de volta"
    _uso(st, "r-d", "p2", rendeu, "recipe>ai_actor")
    _uso(st, "r-e", "p3", rendeu, "recipe>ai_actor")
    SombraDaQuarentena(st.learning, st.db, liberada=lambda r: int(r["id"]) == rendeu).executar(agora)
    assert st.db.scalar("SELECT reason FROM learning_signals WHERE kind=? AND source_ref=?",
                        ("sombra_da_quarentena", f"receita:{rendeu}")) == "prenderia_de_volta"
    async with _cliente(harness) as c:
        padrao = (await c.get("/api/aprendizado/sinais")).json()
        so = (await c.get("/api/aprendizado/sinais", params={"kind": "sombra_da_quarentena"})).json()
    assert "sombra_da_quarentena" not in padrao["contagem"]
    assert so["contagem"]["sombra_da_quarentena"] == 3


def test_a_montagem_registra_o_passo(harness: Harness) -> None:
    assert "sombra_da_quarentena" in {p.nome for p in harness.state.learning._passos}       # type: ignore[attr-defined]
