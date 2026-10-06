"""31.181: quem pode usar o quê hoje num app, por persona (`GET /api/aprendizado/alcance?app=`).

Medido em 06/10 (leitura da onda 2): das 19 receitas ativas do Instagram, 18 vinham de execução e valiam para as três
personas; a do ensino valia só para quem ensinou, e o fluxo ensinado tinha escopo de uma persona. O painel adivinhava
isso pelo rótulo dos alvos.

O que estes testes protegem:
* a receita de execução vale para todas; a do ensino, só para quem ensinou (`presa_a_quem_ensinou`), e o "Confirmar que
  fica" a solta para as outras (a regra é a do `RecipeStore`, não uma cópia);
* o fluxo com escopo diz `fora_do_escopo` a quem não está nele; o candidato diz `fluxo_nao_ligado` a todas;
* a resposta traz as personas vinculadas ao app com os aparelhos e o resumo por persona; app desconhecido dá 404.

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

import httpx

from app.main import create_app
from app.modules.learning.domain.alcance import MotivoDoNao, veredito_do_fluxo
from app.util import now_iso

from .conftest import Harness
from .test_treino_dado_da_persona import _sessao_com_persona

PACOTE = "com.pocqa.messenger"


def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _receita(db, chave: str, origem: str) -> int:      # type: ignore[no-untyped-def]
    db.execute("INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version,"
               " status, actions, learned_from_step, replay_ok, replay_fail, created_at)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (PACOTE, "1", "sig", "v", f"h-{chave}", chave, 1, "active", "[]", origem, 2, 0, now_iso()))
    return int(db.scalar("SELECT id FROM recipes WHERE step_hash=?", (f"h-{chave}",)))


def _fluxo(db, fid: str, status: str, source: str) -> None:      # type: ignore[no-untyped-def]
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, uses, created_at,"
               " source, ref_publico) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (fid, fid, f"k-{fid}", f"comando {fid}", "{}", "qa-messenger", status, 0, now_iso(), source, fid))


def test_o_fluxo_nao_ligado_e_o_fora_do_escopo() -> None:
    assert veredito_do_fluxo("candidate", True).motivo is MotivoDoNao.FLUXO_NAO_LIGADO
    assert veredito_do_fluxo("active", False).motivo is MotivoDoNao.FORA_DO_ESCOPO
    assert veredito_do_fluxo("active", True).pode


async def test_o_alcance_por_persona_segue_a_regra_da_execucao(harness: Harness) -> None:
    st, sid = await _sessao_com_persona(harness)                        # a persona p-ana ensinou
    db = st.db
    db.execute("INSERT INTO instagram_profiles(id, username, first_name, last_name, email, created_at, updated_at)"
               " VALUES (?,?,?,?,?,?,?)", ("p-bia", "", "Bia", "Reis", "bia@exemplo.test", now_iso(), now_iso()))
    for pid, inst in (("p-ana", "android-01"), ("p-bia", "android-02")):
        db.execute("INSERT INTO device_profile_bindings(profile_id, instance_id, active, bound_at, app_id)"
                   " VALUES (?,?,?,?,?)", (pid, inst, 1, now_iso(), "qa-messenger"))
    ensinada = _receita(db, "abrir_busca", f"training:{sid}")
    de_execucao = _receita(db, "abrir_conversa", "r-1:android-01:v1:abrir_conversa")
    _fluxo(db, "f-ensinado", "active", f"training:{sid}")
    db.execute("INSERT INTO flow_scope(flow_id, profile_id) VALUES (?,?)", ("f-ensinado", "p-ana"))
    _fluxo(db, "f-candidato", "candidate", "run")
    async with _cliente(harness) as c:
        r = await c.get("/api/aprendizado/alcance", params={"app": "qa-messenger"})
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert (await c.get("/api/aprendizado/alcance", params={"app": "nao-existe"})).status_code == 404
    assert corpo["pacote"] == PACOTE
    assert corpo["personas"] == [{"profile_id": "p-ana", "aparelhos": ["android-01"]},
                                 {"profile_id": "p-bia", "aparelhos": ["android-02"]}]
    por_id = {(i["tipo"], i["id"]): i["por_persona"] for i in corpo["itens"]}
    assert por_id[("receita", str(de_execucao))] == {"p-ana": {"pode": True, "motivo": None},
                                                     "p-bia": {"pode": True, "motivo": None}}
    assert por_id[("receita", str(ensinada))] == {"p-ana": {"pode": True, "motivo": None},
                                                  "p-bia": {"pode": False, "motivo": "presa_a_quem_ensinou"}}
    assert por_id[("fluxo", "f-ensinado")]["p-bia"] == {"pode": False, "motivo": "fora_do_escopo"}
    assert por_id[("fluxo", "f-ensinado")]["p-ana"]["pode"] is True
    assert {v["motivo"] for v in por_id[("fluxo", "f-candidato")].values()} == {"fluxo_nao_ligado"}
    assert corpo["resumo"] == {"p-ana": {"receita": 2, "fluxo": 1}, "p-bia": {"receita": 1, "fluxo": 0}}
    # "Confirmar que fica" no fluxo do ensino solta a receita para as outras personas (a mesma regra do executor)
    db.execute("INSERT INTO learning_transitions(item_ref, item_kind, from_state, to_state, reason, decided_by,"
               " decided_at) VALUES (?,?,?,?,?,?,?)", ("fluxo:f-ensinado", "fluxo", "published", "published",
                                                      "confirmado que fica: vale para todas", "Flavio", now_iso()))
    async with _cliente(harness) as c:
        depois = (await c.get("/api/aprendizado/alcance", params={"app": "qa-messenger"})).json()
    receita = next(i for i in depois["itens"] if i["tipo"] == "receita" and i["id"] == str(ensinada))
    assert receita["por_persona"]["p-bia"] == {"pode": True, "motivo": None}
