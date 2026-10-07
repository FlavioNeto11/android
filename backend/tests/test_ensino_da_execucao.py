"""31.221 (P-014): o ensino a partir de uma execução que deu certo (adendo v1.120).

A operação do Instagram planeja com ações do catálogo, e o 31.153 não as oferece; o que reaproveita a etapa do catálogo
é a receita pela identidade da etapa. A receita que a IA aprende numa execução real nasce candidata. Aqui a pessoa
promove, num gesto, as candidatas das etapas de leitura de uma execução que deu certo, pelo caminho do Livro.

O que estes testes protegem:
* o motivo fechado por etapa, nesta ordem: simulada, com efeito (inclusive só a trava), não concluída, já por receita,
  sem ator, caminho não reproduzível (com as ferramentas), sem receita, receita que já vale, receita fora;
* a lista das ferramentas não reproduzíveis é a mesma do executor (`recipes.UNSAFE_TO_REPLAY`);
* `GET /api/aprendizado/execucao/{run}/ensino`: a candidata da etapa de leitura, o `press_back` como motivo
  (o achado da onda 1), o comentário como `com_efeito`, e nenhum texto da trava na resposta;
* `POST`: promove só a candidata (candidate → published pela pessoa, trilha com `ensino_da_execucao:<run>` e a
  persona); a segunda vez não promove nada (`receita_ja_vale`); a execução simulada não ensina; 404 sem execução.

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA; nenhuma conta real).
"""
from __future__ import annotations

from typing import Any

from app.modules.learning.domain.ensino_da_execucao import (NAO_REPRODUZIVEIS, EtapaDaExecucao, Motivo, ReceitaDaEtapa,
                                                            bloqueadoras, motivo)
from app.taskqueue.recipes import UNSAFE_TO_REPLAY
from app.util import now_iso

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente

TS = "2026-10-07T10:05:00.000Z"
TRAVA = "um texto que nunca sai"


def _e(**k: Any) -> EtapaDaExecucao:
    base: dict[str, Any] = {"step_id": "s", "key": "open_profile_1", "capability": "OPEN_PROFILE",
                            "status": "succeeded", "driven_by": "ai", "side_effect": False, "trava": False,
                            "profile_id": "p", "ferramentas": ("tap",)}
    return EtapaDaExecucao(**(base | k))


def test_o_motivo_por_etapa_e_a_lista_do_executor() -> None:
    assert NAO_REPRODUZIVEIS == UNSAFE_TO_REPLAY
    cand = ReceitaDaEtapa(id=1, status="candidate", replay_ok=0)
    assert motivo(_e(), cand, simulada=False) is None
    assert motivo(_e(), cand, simulada=True) is Motivo.EXECUCAO_SIMULADA
    assert motivo(_e(status="failed"), cand, simulada=False) is Motivo.NAO_CONCLUIDA
    assert motivo(_e(status="ready", side_effect=True), None, simulada=False) is Motivo.COM_EFEITO
    assert motivo(_e(trava=True), cand, simulada=False) is Motivo.COM_EFEITO
    assert motivo(_e(side_effect=True), cand, simulada=False) is Motivo.COM_EFEITO
    assert motivo(_e(driven_by="recipe"), None, simulada=False) is Motivo.JA_POR_RECEITA
    assert motivo(_e(driven_by="sem_ator"), None, simulada=False) is Motivo.SEM_ATOR
    voltou = _e(ferramentas=("press_back", "tap", "press_back"))
    assert motivo(voltou, None, simulada=False) is Motivo.CAMINHO_NAO_REPRODUZIVEL
    assert bloqueadoras(voltou) == ["press_back"]
    assert motivo(_e(), None, simulada=False) is Motivo.SEM_RECEITA
    assert motivo(_e(), ReceitaDaEtapa(1, "active", 3), simulada=False) is Motivo.RECEITA_JA_VALE
    assert motivo(_e(), ReceitaDaEtapa(1, "superseded", 0), simulada=False) is Motivo.RECEITA_FORA


def _execucao(st: Any, rid: str, *, simulada: bool = False) -> int:
    """A forma da onda 1: perfil pela IA (com candidata), post por receita, comentários pela IA começando por voltar
    (sem receita) e o comentário com efeito e trava."""
    db = st.db
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at, simulated)"
               " VALUES (?,?,?,?,?,?,?,?)", (rid, f"k-{rid}", "c", "execute", "awaiting_person", '["android-01"]',
                                             TS, int(simulada)))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, profile_id)"
               " VALUES (?,?,?,?,1,?)", (f"{rid}:o", rid, "android-01", "running", "p-ana"))
    etapas = (("open_profile_1", "OPEN_PROFILE", "ai", 0, "[]", "succeeded", ("tap", "type_text", "tap")),
              ("open_post_1", "OPEN_POST", "recipe", 0, "[]", "succeeded", ("tap",)),
              ("open_comments_1", "OPEN_COMMENTS", "ai", 0, "[]", "succeeded", ("press_back", "tap")),
              ("comment_1", "CREATE_COMMENT", None, 1, f'["{TRAVA}"]', "ready", ()))
    for n, (key, cap, por, efeito, trava, status, ferramentas) in enumerate(etapas, 1):
        sid = f"{rid}:s{n}"
        db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                   " postcondition, timeout_s, max_attempts, status, capability, driven_by, side_effect, commit_guard)"
                   " VALUES (?,?,?,?,1,?,?,'t','g','{}',60,3,?,?,?,?,?)",
                   (sid, rid, f"{rid}:o", "android-01", n, key, status, cap, por, efeito, trava))
        if ferramentas:
            db.execute("INSERT INTO attempts(id, step_id, number, status, started_at) VALUES (?,?,1,'succeeded',?)",
                       (f"{sid}:a1", sid, TS))
        for i, f in enumerate(ferramentas, 1):
            db.execute("INSERT INTO actions(attempt_id, seq, tool, args, status, intent_at, source)"
                       " VALUES (?,?,?,?,?,?,?)", (f"{sid}:a1", i, f, "{}", "done", TS, "ai"))
    db.execute("INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version,"
               " status, actions, learned_from_step, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               ("com.instagram.android", "1", "sig", "v", f"h-{rid}", "open_profile_1", 1, "candidate",
                '[{"tool": "tap", "commit": false}]', f"{rid}:s1", now_iso()))
    return int(db.scalar("SELECT id FROM recipes WHERE learned_from_step=?", (f"{rid}:s1",)))


async def test_a_rota_mostra_e_a_pessoa_ensina(harness: Harness) -> None:
    st = harness.state
    rec = _execucao(st, "r-20261007100500-aaaaaa")
    simulada = _execucao(st, "r-20261007100600-bbbbbb", simulada=True)
    run = "r-20261007100500-aaaaaa"
    async with _cliente(harness) as c:
        antes = (await c.get(f"/api/aprendizado/execucao/{run}/ensino")).json()
        ensinou = (await c.post(f"/api/aprendizado/execucao/{run}/ensino")).json()
        de_novo = (await c.post(f"/api/aprendizado/execucao/{run}/ensino")).json()
        sim = (await c.post("/api/aprendizado/execucao/r-20261007100600-bbbbbb/ensino")).json()
        nao = await c.get("/api/aprendizado/execucao/r-20261007000000-cccccc/ensino")
    por = {e["key"]: e for e in antes["etapas"]}
    assert (por["open_profile_1"]["ensinavel"], por["open_profile_1"]["receita"]["id"]) == (True, rec)
    assert por["open_post_1"]["motivo"] == "ja_por_receita"
    assert por["open_comments_1"]["motivo"] == "caminho_nao_reproduzivel"
    assert por["open_comments_1"]["ferramentas_nao_reproduziveis"] == ["press_back"]
    assert por["comment_1"]["motivo"] == "com_efeito" and por["open_profile_1"]["persona"] == "p-ana"
    assert antes["ensinaveis"] == 1 and TRAVA not in str(antes)
    assert ensinou["promovidas"] == [{"recipe_id": rec, "step_key": "open_profile_1"}] and ensinou["recusadas"] == []
    assert st.db.scalar("SELECT status FROM recipes WHERE id=?", (rec,)) == "active"
    trilha = st.db.query("SELECT * FROM learning_transitions WHERE reason LIKE 'ensino_da_execucao:%' ORDER BY id")
    assert trilha and all(f"ensino_da_execucao:{run} persona:p-ana" == t["reason"] for t in trilha)
    assert de_novo["promovidas"] == [] and {e["key"]: e["motivo"] for e in de_novo["etapas"]}["open_profile_1"] == \
        "receita_ja_vale"
    assert sim["promovidas"] == [] and {e["motivo"] for e in sim["etapas"]} == {"execucao_simulada"}
    assert st.db.scalar("SELECT status FROM recipes WHERE id=?", (simulada,)) == "candidate"
    assert nao.status_code == 404 and nao.json()["detail"]["code"] == "execucao_desconhecida"
