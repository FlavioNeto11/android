"""prova30 A3: o aprendizado de UMA operação nas 10 perguntas do dono (`GET /api/operacoes/{id}/aprendizado`).

Cobre:
    * a ligação de cada fonte às execuções da operação: evidência e transição por `run_id`, receita pelo prefixo de
      `learned_from_step`, fluxo por `source_run_id`, lição pela `provenance`, memória da persona pela interação, sinal de
      falha e o backlog INFERIDO por (app, ação, tipo);
    * a régua única de confiança (estado do Livro e confiança 0–1 traduzidos), o reutilizável e o "revisar" por código;
    * fato da operação com a fonte que o sustenta (a evidência é a observação; a fonte cita a mesma observação);
    * filtro por persona (o dela mais o da operação), simulado fora por padrão, texto redigido, 404 sem operação.

Nível de prova: `simulated` (banco de teste, sem aparelho nem IA). A 124 (a operação, da Jev) é imitada só no que esta
parte lê: a coluna `runs.operacao_id`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.modules.pedidos.domain import aprendizado_da_operacao as dominio
from app.modules.pedidos.infrastructure.aprendizado_da_operacao import LeitorDoAprendizadoDaOperacao
from app.modules.pedidos.presentation.aprendizado_da_operacao import router

from .test_pedidos_modelo import _banco, _run

T0 = "2026-10-06T17:00:00.000Z"
AGORA = "2026-10-06T18:00:00.000Z"
SEGREDO = "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _ins(db: Database, tabela: str, **cols: object) -> None:
    db.execute(f"INSERT INTO {tabela}({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",  # noqa: S608
               tuple(cols.values()))


@pytest.fixture
def banco(tmp_path: Path) -> Database:
    db = _banco(tmp_path)
    db.migrate()
    return db


def _mundo(db: Database) -> None:
    """Uma operação com duas execuções (P1 no 01, P2 no 03), uma execução fora dela e o que cada uma deixou."""
    if "operacao_id" not in db.columns("runs"):
        db.execute("ALTER TABLE runs ADD COLUMN operacao_id TEXT")
    for r in ("r-a", "r-b", "r-fora"):
        _run(db, r)
    db.execute("UPDATE runs SET operacao_id='op-1' WHERE id IN ('r-a','r-b')")
    for p in ("p1", "p2"):
        _ins(db, "instagram_profiles", id=p, username=f"u-{p}", created_at=T0, updated_at=T0)
    for r, p, inst in (("r-a", "p1", "android-01"), ("r-b", "p2", "android-03"), ("r-fora", "p1", "android-01")):
        _ins(db, "objectives", id=f"{r}:o1", run_id=r, instance_id=inst, status="succeeded", plan_version=1,
             parameters="{}", profile_id=p)
    receita = dict(app_package="com.instagram.android", app_version="447", app_signature="sig", variant="v",
                   version=1, actions="[]", created_at=T0)
    _ins(db, "recipes", **receita, step_hash="h1", step_key="open_profile_1", status="active",
         learned_from_step="r-a:android-01:v1:open_profile_1")                      # nasceu na operação
    _ins(db, "recipes", **receita, step_hash="h2", step_key="open_comments_1", status="quarantined",
         learned_from_step="r-velha:android-06:v2:open_comments_1")                 # reforçada pela operação
    _ins(db, "recipes", **receita, step_hash="h3", step_key="open_post_1", status="active",
         learned_from_step="r-fora:android-01:v1:open_post_1")                      # de fora: não entra
    ids = {r["step_key"]: r["id"] for r in db.query("SELECT id, step_key FROM recipes")}
    for ref, stance, run, sim in ((f"receita:{ids['open_profile_1']}", "for", "r-a", 0),
                                  (f"receita:{ids['open_comments_1']}", "for", "r-b", 0),
                                  (f"receita:{ids['open_comments_1']}", "against", "r-b", 1)):     # simulada
        _ins(db, "learning_evidence", item_ref=ref, stance=stance, origin_ref=f"run:{run}", run_id=run,
             instance_id="android-01", simulated=sim, detail="x", observed_at=T0)
    _ins(db, "learning_transitions", item_ref=f"receita:{ids['open_comments_1']}", item_kind="receita",
         from_state="active", to_state="quarantined", reason="3 falhas seguidas", decided_by="sistema",
         decided_at=T0, run_id="r-b")
    _ins(db, "flows", id="f-op", name="Fluxo", match_key="k", command_template="abra o perfil {username}", plan="{}",
         app_id="instagram", source_run_id="r-a", status="candidate", uses=0, created_at=T0, source="run")
    _ins(db, "learning_items", id="li-1", kind="licao", state="active", scope_app="instagram",
         scope_capability="OPEN_POST", content="{}", content_hash="c1", summary="Em OPEN_POST: role até a legenda.",
         tokens=10, source_kind="recovery", provenance=json.dumps({"execucoes": ["r-b"]}), evidence_for=1,
         evidence_against=0, distinct_runs=1, distinct_devices=1, created_by="sistema", created_at=T0,
         updated_at=T0, state_at=T0)
    _ins(db, "social_interactions", id="si-1", profile_id="p1", instance_id="android-01", run_id="r-a",
         occurred_at=T0, type="comment_replied", direction="outgoing", counterparty="loja", status="confirmed",
         created_at=T0, updated_at=T0)
    _ins(db, "memory_items", id="mem-1", profile_id="p1", subject="loja", content=f"comentei na loja; chave {SEGREDO}",
         source="interaction", interaction_id="si-1", confidence=0.9, fingerprint="fp1", created_at=T0, updated_at=T0)
    _ins(db, "memory_items", id="mem-2", profile_id="p1", subject="loja", content="talvez goste de outono",
         source="interaction", interaction_id="si-1", confidence=0.4, fingerprint="fp2", created_at=T0, updated_at=T0)
    _ins(db, "learning_signals", kind="feedback", polarity="negative", source_ref="s1", created_by="sistema",
         run_id="r-b", profile_id="p2", app_package="com.instagram.android", capability="OPEN_COMMENTS",
         failure_kind="wrong_screen", simulated=0, created_at=T0, updated_at=T0)
    _ins(db, "memory_items", id="mem-obs", profile_id="p1", subject="tela", content="sem execução",
         source="observation", confidence=0.9, fingerprint="fp3", created_at=T0, updated_at=T0)   # sem vínculo
    _ins(db, "learning_backlog", id="bk-1", category="defeito", cluster_key="ck", app_package="com.instagram.android",
         capability="OPEN_COMMENTS", failure_kind="wrong_screen", title="Folha de comentários do post errado",
         state="open", first_seen=T0, last_seen=T0)
    # a memória da operação: a leitura do alvo, uma fonte da pesquisa e dois fatos (um sustentado por ela)
    _ins(db, "pedido_observacoes", id="ob-url", operacao_id="op-1", ocorrencia_id="r-a", run_id="r-a", alvo="",
         nome="fonte_1", tipo="url", situacao="observado", valor="https://exemplo.org/a", capturado_em=T0)
    _ins(db, "pedido_observacoes", id="ob-inc", operacao_id="op-1", ocorrencia_id="r-b", run_id="r-b",
         alvo="vencida:x", nome="leitura_do_alvo", situacao="incerto", trecho="outra legenda", capturado_em=T0)
    for chave, tipo, valor, origem, conf, evid, frescor in (
            ("fonte.1", "fonte", "Exemplo — https://exemplo.org/a", "pesquisa", "confirmado", ["ob-url"], None),
            ("pesquisa.1", "descoberta", "a coleção usa tecido reciclado", "pesquisa", "hipotese", ["ob-url"], None),
            ("alvo.conteudo", "descoberta", "Lançamento da coleção", "leitura", "confirmado", ["ob-x"],
             "2026-10-06T23:00:00.000Z"),
            ("velho", "descoberta", "preço antigo", "operador", "confirmado", [], "2026-10-06T10:00:00.000Z"),
            ("pesquisa.estado", "progresso", "1 fato, 1 fonte", "pesquisa", "confirmado", [], None)):
        _ins(db, "pedido_memoria", id=f"m-{chave}", operacao_id="op-1", chave=chave, tipo=tipo, valor=valor,
             atualizada_em=T0, origem=origem, confianca=conf, evidencia=json.dumps(evid), frescor_ate=frescor)


def _resposta(db: Database, **kw: object) -> dict[str, object]:
    itens = LeitorDoAprendizadoDaOperacao(db).ler("op-1", simulados=bool(kw.pop("simulados", False)))
    assert itens is not None
    return dominio.responder(itens, agora=AGORA, persona=kw.get("persona"))  # type: ignore[arg-type]


def _refs(resp: dict[str, object], chave: str) -> set[str]:
    perguntas = resp["perguntas"]
    assert isinstance(perguntas, list)
    itens = next(p["itens"] for p in perguntas if p["chave"] == chave)
    return {str(i["ref"]) for i in itens}


def test_as_10_perguntas_saem_das_execucoes_da_operacao_e_so_delas(banco: Database) -> None:
    _mundo(banco)
    resp = _resposta(banco)
    assert [p["chave"] for p in resp["perguntas"]] == [k for k, _ in dominio.PERGUNTAS]     # type: ignore[union-attr]
    ids = {r["step_key"]: r["id"] for r in banco.query("SELECT id, step_key FROM recipes")}
    plataforma = _refs(resp, "plataforma_aprendeu")
    assert {f"receita:{ids['open_profile_1']}", f"receita:{ids['open_comments_1']}", "fluxo:f-op",
            "licao:li-1"} == plataforma                                  # a receita de fora (r-fora) não entra
    assert _refs(resp, "do_app") == {f"receita:{ids['open_profile_1']}", f"receita:{ids['open_comments_1']}"}
    assert _refs(resp, "do_processo") == {"fluxo:f-op", "licao:li-1"}     # a lição é de uma ação
    assert _refs(resp, "persona_aprendeu") == {"interacao:si-1", "memoria:mem-1", "memoria:mem-2"}  # mem-obs fora
    assert _refs(resp, "conhecimento_geral") == {"fato:alvo.conteudo"}    # hipótese, vencido e registro fora
    assert _refs(resp, "fontes_externas") == {"fonte:fonte.1"}
    sust = {s["ref"]: s for s in resp["perguntas"][6]["itens"]}           # type: ignore[index]
    assert sust["fato:pesquisa.1"]["fontes"] == [{"ref": "fonte:fonte.1", "resumo": "Exemplo — https://exemplo.org/a",
                                                  "observacao": "ob-url"}]
    assert sust["fato:alvo.conteudo"]["fontes"] == [{"ref": "ob-x"}]
    falhas = _refs(resp, "falhas_que_geraram_aprendizado")
    assert {f"queda:receita:{ids['open_comments_1']}", "backlog:bk-1"} <= falhas
    assert any(r.startswith("sinal:") for r in falhas)
    backlog = next(i for i in resp["perguntas"][9]["itens"] if i["ref"] == "backlog:bk-1")  # type: ignore[index]
    assert backlog["inferida"] is True and backlog["confianca"] == "hipotese"


def test_reutilizavel_e_revisar_sao_regras_de_codigo_com_uma_regua_so(banco: Database) -> None:
    _mundo(banco)
    resp = _resposta(banco)
    ids = {r["step_key"]: r["id"] for r in banco.query("SELECT id, step_key FROM recipes")}
    reuso = _refs(resp, "reutilizavel")
    # a receita nascida aqui (ativa, a favor) e a lição (ativa, a favor); a reforçada caiu em quarentena nesta operação
    assert f"receita:{ids['open_profile_1']}" in reuso and "licao:li-1" in reuso
    assert f"receita:{ids['open_comments_1']}" not in reuso and "fluxo:f-op" not in reuso      # candidato
    assert "memoria:mem-1" in reuso and "memoria:mem-2" not in reuso       # 0,9 confirmada; 0,4 hipótese
    revisar = {i["ref"]: i["motivo"] for i in resp["perguntas"][8]["itens"]}  # type: ignore[index]
    assert revisar[f"receita:{ids['open_comments_1']}"] == "no Livro em quarantined"
    assert revisar["fluxo:f-op"] == "no Livro em candidate"
    assert revisar["fato:velho"] == "vencido: passou do frescor"
    assert revisar["fato:pesquisa.1"] == "hipótese: não confirmada"
    assert revisar["memoria:mem-2"] == "hipótese: não confirmada"
    assert revisar["observacao:ob-inc"].startswith("leitura incerta")
    for p in resp["perguntas"]:                                            # type: ignore[union-attr]
        for i in p["itens"]:
            if "confianca" in i:
                assert i["confianca"] in ("confirmado", "hipotese")


def test_persona_simulado_redacao_e_404(banco: Database) -> None:
    _mundo(banco)
    so_p2 = _resposta(banco, persona="p2")
    assert _refs(so_p2, "persona_aprendeu") == set()                       # a memória é da P1
    assert "fato:alvo.conteudo" in _refs(so_p2, "conhecimento_geral")      # o da operação fica
    assert "licao:li-1" in _refs(so_p2, "plataforma_aprendeu")             # citada só pela execução da P2
    ids = {r["step_key"]: r["id"] for r in banco.query("SELECT id, step_key FROM recipes")}
    ref = f"receita:{ids['open_comments_1']}"
    sem = next(i for i in _resposta(banco)["perguntas"][0]["itens"] if i["ref"] == ref)      # type: ignore[index]
    com = next(i for i in _resposta(banco, simulados=True)["perguntas"][0]["itens"]          # type: ignore[index]
               if i["ref"] == ref)
    assert (sem["contra"], com["contra"]) == (0, 1)                        # a evidência simulada só com o pedido
    texto = json.dumps(_resposta(banco), ensure_ascii=False)
    assert SEGREDO not in texto and "comentei na loja" in texto
    assert all(len(str(i["resumo"])) <= 200 for p in _resposta(banco)["perguntas"]  # type: ignore[union-attr]
               for i in p["itens"] if "resumo" in i)
    assert LeitorDoAprendizadoDaOperacao(banco).ler("op-nenhuma") is None


def test_a_rota_responde_e_diz_404_sem_a_124(banco: Database, tmp_path: Path) -> None:
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    app.state.poc = type("Poc", (), {"db": banco})()
    cliente = TestClient(app)
    assert cliente.get("/api/operacoes/op-1/aprendizado").status_code == 404   # sem a coluna (124 fora do banco)
    _mundo(banco)
    r = cliente.get("/api/operacoes/op-1/aprendizado", params={"persona": "p1"})
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["operacao_id"] == "op-1" and corpo["persona"] == "p1" and len(corpo["perguntas"]) == 10
    assert {n["chave"] for n in corpo["nao_coberto"]} >= {"conhecimento_geral", "pedido_relatorios", "persona_aprendeu"}
    assert cliente.get("/api/operacoes/op-x/aprendizado").json()["detail"]["code"] == "operacao_desconhecida"


def test_o_dominio_recusa_item_fora_do_vocabulario() -> None:
    with pytest.raises(ValueError):
        dominio.responder([dominio.Item("x", "fato", "operacao", "r", "operador", "talvez")], agora=AGORA)
