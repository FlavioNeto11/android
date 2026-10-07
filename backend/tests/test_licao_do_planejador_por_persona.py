"""31.218: a lição do planejador que nasce de uma correção ensinada (31.149) é da PERSONA que errou (adendo v1.118).

O 31.149 fazia da correção uma lição do planejador do app inteiro: o que uma persona errou ia ao plano de todas. Agora
a lição é da persona do objetivo que falhou (`scope_profile_id`), só vai ao planejamento dela, e o alcance (31.181)
mostra, por persona, o que ela erra e como se corrige.

O que estes testes protegem:
* a correção nasce com a persona (id, nunca nome) no escopo e na proveniência; sem persona, segue do app;
* a lição com persona só vai ao pedido da mesma persona (`nivel`); o pedido sem persona (várias, ou nenhuma) não a
  recebe; a lição sem persona segue indo a todos; a prévia aceita `persona=`;
* a mesma correção de duas personas são dois itens (a identidade inclui a persona);
* o planejamento de UMA persona manda a persona na costura; o de nenhuma manda '';
* `GET /api/aprendizado/alcance`: cada persona traz as `correcoes` dela, com o escopo (`persona` ou, na lição anterior
  ao 31.218, `app`, achada pela etapa que falhou).

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

from typing import Any

import pytest

from app.modules.learning.domain.licoes import Nivel, Pedido, licao_da_correcao, nivel
from app.modules.learning.domain.vocabulario import Papel
from app.modules.learning.infrastructure.ligar_licoes import licoes_de
from app.modules.learning.infrastructure.sql_repository import item_da_linha
from app.taskqueue import service as servico_de_execucoes
from app.util import now_iso

from .conftest import Harness
from .test_alcance_por_persona import _cliente
from .test_correcao_vira_licao_do_planejador import PKG, _c
from .test_correcao_volta_ao_comando import _corrigir, _falha


def _pedido(persona: str = "") -> Pedido:
    return Pedido(papel=Papel.PLANNER, unidade="plan:r", run_id="r", app=PKG, capability="", step_hash="",
                  simulated=False, profile_id=persona)


def test_a_correcao_e_da_persona_e_so_vai_a_ela() -> None:
    novo = licao_da_correcao(_c(persona="p-ana"))
    assert not isinstance(novo, Exception) and novo.escopo.profile_id == "p-ana"           # type: ignore[union-attr]
    assert novo.provenance["persona"] == "p-ana"                                             # type: ignore[union-attr]
    sem = licao_da_correcao(_c())
    assert sem.escopo.profile_id == ""                                                       # type: ignore[union-attr]
    assert novo.content_hash == sem.content_hash                     # o conteúdo é o mesmo; a persona é do escopo


async def test_no_livro_no_prompt_e_no_alcance(harness: Harness) -> None:
    st = harness.state
    db = st.db
    for pid in ("p-ana", "p-bia"):
        db.execute("INSERT INTO instagram_profiles(id, username, created_at, updated_at) VALUES (?,?,?,?)",
                   (pid, pid, now_iso(), now_iso()))
    for pid, inst in (("p-ana", "android-01"), ("p-bia", "android-02")):
        db.execute("INSERT INTO device_profile_bindings(profile_id, instance_id, active, bound_at, app_id)"
                   " VALUES (?,?,?,?,?)", (pid, inst, 1, now_iso(), "qa-messenger"))
    falha = _falha(st, chave="abrir_conversa", parametros="{}")
    db.execute("UPDATE objectives SET profile_id='p-ana' WHERE run_id=?", (falha[0],))
    salvo = await _corrigir(harness, falha=falha, chaves=("tocar_busca", "digitar_nome"),
                            parametros=[{"name": "nome", "example": "QA-001", "description": ""}])
    licao = salvo["correcao"]["licao"]
    linha = db.one("SELECT * FROM learning_items WHERE id=?", (licao["id"],))
    assert (linha["scope_profile_id"], linha["scope_role"], linha["human_origin"]) == ("p-ana", "planner", 1)
    # publicada pelo dono (direto no banco), só vai ao plano da p-ana
    db.execute("UPDATE learning_items SET state='published', state_detail='em_prova' WHERE id=?", (licao["id"],))
    item = item_da_linha(db.one("SELECT * FROM learning_items WHERE id=?", (licao["id"],)))
    assert nivel(item, _pedido("p-ana")) is Nivel.APP
    assert nivel(item, _pedido("p-bia")) is None and nivel(item, _pedido()) is None
    licoes = licoes_de(st.learning)
    assert licoes is not None
    assert [e.item.id for e in licoes.previa(app=PKG, papel=Papel.PLANNER, persona="p-ana").escolha.escolhidas] == [
        licao["id"]]
    assert licoes.previa(app=PKG, papel=Papel.PLANNER).escolha.escolhidas == ()
    # a lição anterior ao 31.218 (sem persona no escopo) aparece na persona da etapa que falhou, como do app
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at)"
               " VALUES ('r-b','k-r-b','c','execute','failed','[\"android-02\"]',?)", (now_iso(),))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, profile_id)"
               " VALUES ('r-b:o1','r-b','android-02','failed',1,'p-bia')")
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " postcondition, timeout_s, max_attempts, status) VALUES ('r-b:s1','r-b','r-b:o1','android-02',1,1,"
               "'abrir_perfil','t','g','{}',60,3,'failed')")
    antiga = licao_da_correcao(_c(chave="abrir_perfil", step_id="r-b:s1"))
    st.learning.propor(antiga)                                                               # type: ignore[arg-type]
    async with _cliente(harness) as c:
        corpo = (await c.get("/api/aprendizado/alcance", params={"app": "qa-messenger"})).json()
        previa = (await c.get("/api/aprendizado/licoes/previa",
                              params={"app": PKG, "papel": "planner", "persona": "p-ana"})).json()
    por = {p["profile_id"]: p["correcoes"] for p in corpo["personas"]}
    assert [(x["acao"], x["escopo"], x["estado"], x["caminho"]) for x in por["p-ana"]] == [
        ("abrir_conversa", "persona", "published", ["tocar_busca", "digitar_nome"])]
    assert [(x["acao"], x["escopo"]) for x in por["p-bia"]] == [("abrir_perfil", "app")]
    assert "QA-001" not in str(corpo)
    assert previa.get("bloco") and "abrir_conversa" in str(previa["bloco"]), previa


def test_a_mesma_correcao_de_duas_personas_sao_dois_itens(harness: Harness) -> None:
    st = harness.state
    a = st.learning.propor(licao_da_correcao(_c(persona="p-ana")))                         # type: ignore[arg-type]
    b = st.learning.propor(licao_da_correcao(_c(persona="p-bia")))                         # type: ignore[arg-type]
    assert a.id != b.id and a.content_hash == b.content_hash
    assert st.learning.propor(licao_da_correcao(_c(persona="p-ana"))).id == a.id            # type: ignore[arg-type]


async def test_o_planejamento_de_uma_persona_manda_a_persona(harness: Harness,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    pedidos: list[Any] = []
    original = servico_de_execucoes.pedir_licoes

    def espiao(costuras: Any, pedido: Any) -> list[str]:
        pedidos.append(pedido)
        return original(costuras, pedido)

    monkeypatch.setattr(servico_de_execucoes, "pedir_licoes", espiao)
    sem = harness.run(["android-01"], mode="plan", command="abra as configurações")
    await harness.wait_run(sem.id, statuses=("planned", "needs_input", "failed"), timeout=30)
    from .test_treino_escopo_ao_provar import _persona
    _persona(st, "persona.do.plano")
    com = harness.run(["android-01"], mode="plan", command="abra as configurações de novo")
    await harness.wait_run(com.id, statuses=("planned", "needs_input", "failed"), timeout=30)
    por_run = {p.run_id: p.profile_id for p in pedidos}
    assert por_run.get(sem.id) == ""
    persona = st.db.scalar("SELECT id FROM instagram_profiles WHERE username='persona.do.plano'")
    assert por_run.get(com.id) == persona, por_run
