"""31.113 F1: o registro da execução guarda o marcador da persona, não o valor (achado da prova real do 31.87).

A reprodução levou o nome da persona à tela, como devia, mas o valor ficou em claro nos eventos, no diário de ações, no
erro da tentativa, na nota da evidência e no detalhe da etapa. Agora a fronteira de escrita troca valor → marcador:
`Repository.log_intent`/`finish_action`/`note_attempt`/`finish_attempt`/`add_evidence`/`transition_step` e o
`EventBus.emit` (pela máscara que o `Repository` liga). O executor segue com o valor em memória: a tela não muda.

Regras (as do ensino, 31.87 F2): o valor mais longo primeiro, no mínimo 3 caracteres, como palavra inteira, sem
diferença de maiúscula; os dados que identificam a persona sempre, os outros só quando o plano os cita; o valor igual a
um parâmetro do comando fica (o parâmetro vence). O `target` da ação fica no banco (é o seletor da receita e da lição),
e o evento não o leva. A execução antiga não é reescrita.

Nível de prova: `simulated` (banco de teste, valores sintéticos; nenhum aparelho nem IA).
"""
from __future__ import annotations

import json

from app.models import (ActionStatus, AttemptStatus, Plan, PlannerInfo, PlanStep, Postcondition, ProfileCreate,
                        RunCreate, StepStatus)
from app.security import mascara_da_persona as m

from .conftest import Harness

# Valores SINTÉTICOS
NOME, SOBRENOME = "Zelda", "Sintetica"
EXIBICAO = f"{NOME} {SOBRENOME}"
EMAIL = "zelda.sintetica@exemplo.test"
MARCA = "{perfil_nome_exibicao}"


# ------------------------------------------------------------------ as funções puras
def test_o_mapa_leva_o_que_identifica_e_o_citado_e_o_parametro_vence() -> None:
    variaveis = {"perfil_nome": NOME, "perfil_sobrenome": SOBRENOME, "perfil_nome_exibicao": EXIBICAO,
                 "perfil_email": EMAIL, "perfil_pais": "Brasil", "perfil_cidade": "Rio", "perfil_filhos": "2"}
    trocas = m.mapa(variaveis, [], {})
    assert set(trocas) == {NOME, SOBRENOME, EXIBICAO, EMAIL}                 # "Brasil" só se o plano citar
    assert "Brasil" in m.mapa(variaveis, ["perfil_pais"], {})
    assert "2" not in m.mapa(variaveis, ["perfil_filhos"], {})               # curto demais
    assert NOME not in m.mapa(variaveis, [], {"destinataria": " zelda "})    # o parâmetro vence no mesmo valor


def test_troca_por_palavra_o_mais_longo_primeiro_sem_caixa() -> None:
    trocas = m.mapa({"perfil_nome": NOME, "perfil_sobrenome": SOBRENOME, "perfil_nome_exibicao": EXIBICAO}, [], {})
    assert m.no_texto(f"Busquei {EXIBICAO.upper()} e depois {NOME}.", trocas) == \
        "Busquei {perfil_nome_exibicao} e depois {perfil_nome}."
    assert m.no_texto("Zeldaria e zeldas", trocas) == "Zeldaria e zeldas"     # pedaço de outra palavra fica
    assert m.no_objeto({"a": [EMAIL, 3], "b": {"c": NOME}}, {EMAIL: "{perfil_email}", NOME: "{perfil_nome}"}) == \
        {"a": ["{perfil_email}", 3], "b": {"c": "{perfil_nome}"}}
    assert m.no_texto(NOME, {}) == NOME


def test_digitado_inteiro_de_qualquer_chave_mas_nao_o_parametro() -> None:
    variaveis = {"perfil_cidade": "Campinas", "perfil_nome": NOME}
    assert m.digitado(" campinas ", variaveis, {}) == ("Campinas", "{perfil_cidade}")
    assert m.digitado("Campinas e região", variaveis, {}) is None
    assert m.digitado("Campinas", variaveis, {"cidade": "Campinas"}) is None
    assert m.digitado("Zé", {"perfil_nome": "Zé"}, {}) is None


# ------------------------------------------------------------------ no registro da execução
def _execucao(h: Harness, *, com_persona: bool = True, parametros: dict[str, str] | None = None) -> tuple[str, str]:
    """Execução materializada no android-01 com a persona sintética, e a tentativa da 1ª etapa já aberta."""
    st = h.state
    assert st is not None
    pid = st.social.create_profile(ProfileCreate(
        username="pessoa.sintetica", instance_id="android-01", first_name=NOME, last_name=SOBRENOME,
        display_name=EXIBICAO, email=EMAIL)).id if com_persona else None
    run, _ = st.repo.create_run(RunCreate(command="pesquisar a persona", instance_ids=["android-01"],
                                          idempotency_key=f"k-31113-{com_persona}-{bool(parametros)}"), simulated=True)
    alvo = "{perfil_nome_exibicao}" if com_persona else "a pessoa"     # sem persona, o pré-voo recusaria o marcador
    passo = PlanStep(key="pesquisar", title="Pesquisar", goal=f"Pesquisar {alvo} na busca",
                     postcondition=Postcondition(kind="text_visible", value=alvo, description="d"))
    plano = Plan(summary="s", steps=[passo], parameters=parametros or {},
                 planner=PlannerInfo(provider="t", model="t", simulated=True))
    st.db.execute("UPDATE runs SET plan=? WHERE id=?", (plano.model_dump_json(), run["id"]))
    st.repo.materialize(run["id"], plano, [{"instance_id": "android-01", "profile_id": pid}])
    sid = str(st.db.scalar("SELECT id FROM steps WHERE run_id=? ORDER BY seq LIMIT 1", (run["id"],)))
    aid = f"{sid}:a1"
    st.db.execute("UPDATE steps SET status='running', attempts=1 WHERE id=?", (sid,))
    st.db.execute("INSERT INTO attempts(id, step_id, number, status, started_at) VALUES (?,?,?,?,?)",
                  (aid, sid, 1, AttemptStatus.running.value, "2026-10-06T00:00:00.000Z"))
    return run["id"], aid


def _registrar(h: Harness, run_id: str, aid: str) -> None:
    """O que o executor grava numa tentativa que digitou e citou o nome da persona."""
    st = h.state
    assert st is not None
    sid = aid.rsplit(":", 1)[0]
    acao = st.repo.log_intent(aid, "type_text", {"text": EXIBICAO, "element_id": "e9"},
                              f"Digitar {EXIBICAO} na busca", side_effect=False)
    st.repo.finish_action(acao, ActionStatus.done, result={"visto": f"No results for {EXIBICAO}"},
                          error=f"sem resultado para {EXIBICAO}", target={"text": EXIBICAO, "resource_id": "app:id/q"})
    st.repo.note_attempt(aid, error=f"a busca por {NOME} não achou")
    st.repo.add_evidence(run_id=run_id, instance_id="android-01", step_id=sid, attempt_id=aid, kind="text",
                         note=f"tela com {EXIBICAO}")
    st.repo.decision(f"o ator digitou {EXIBICAO}", run_id=run_id, instance_id="android-01", step_id=sid)
    st.repo.finish_attempt(aid, AttemptStatus.failed, error=f"não achou {EXIBICAO}", observed=f"lista sem {NOME}")
    st.repo.transition_step(sid, StepStatus.failed, detail=f"pesquisar {EXIBICAO} falhou")


def _textos(h: Harness, run_id: str) -> dict[str, str]:
    st = h.state
    assert st is not None
    q = lambda sql: json.dumps([dict(r) for r in st.db.query(sql, (run_id,))], default=str, ensure_ascii=False)  # noqa: E731
    return {
        "events": q("SELECT message, data FROM events WHERE run_id=?"),
        "actions": q("SELECT a.args, a.rationale, a.result, a.error FROM actions a JOIN attempts t ON t.id=a.attempt_id"
                     " JOIN steps s ON s.id=t.step_id WHERE s.run_id=?"),
        "attempts": q("SELECT t.error, t.observed_result FROM attempts t JOIN steps s ON s.id=t.step_id WHERE s.run_id=?"),
        "evidence": q("SELECT note FROM evidence WHERE run_id=?"),
        "steps": q("SELECT status_detail FROM steps WHERE run_id=?"),
    }


async def test_o_registro_guarda_o_marcador_e_nao_o_valor(harness: Harness) -> None:
    run_id, aid = _execucao(harness)
    _registrar(harness, run_id, aid)
    for fonte, texto in _textos(harness, run_id).items():
        assert NOME.casefold() not in texto.casefold() and EMAIL not in texto, fonte
        assert MARCA in texto or "{perfil_nome}" in texto, fonte
    args = json.loads(str(harness.state.db.scalar("SELECT args FROM actions WHERE attempt_id=?", (aid,))))  # type: ignore[union-attr]
    assert args == {"text": MARCA, "element_id": "e9"}


async def test_o_alvo_fica_no_banco_e_nao_vai_ao_evento(harness: Harness) -> None:
    """O `target` é o seletor que a receita e a lição usam para achar o elemento: mascarado, a receita procuraria
    "{perfil_nome_exibicao}" na tela. O evento, que vai ao navegador, não o leva (o `ActionDTO` não tem o campo)."""
    run_id, aid = _execucao(harness)
    _registrar(harness, run_id, aid)
    alvo = json.loads(str(harness.state.db.scalar("SELECT target FROM actions WHERE attempt_id=?", (aid,))))  # type: ignore[union-attr]
    assert alvo["text"] == EXIBICAO
    eventos = _textos(harness, run_id)["events"]
    assert EXIBICAO not in eventos and NOME not in eventos


async def test_o_parametro_do_comando_vence_a_persona_no_mesmo_valor(harness: Harness) -> None:
    """A receita aprendida da execução guarda `{param}` quando o texto digitado é o parâmetro: mascarar com o marcador
    da persona trocaria o parâmetro pelo dado da persona de cada aparelho."""
    run_id, aid = _execucao(harness, parametros={"quem": EXIBICAO})
    harness.state.repo.log_intent(aid, "type_text", {"text": EXIBICAO}, None, side_effect=False)  # type: ignore[union-attr]
    args = json.loads(str(harness.state.db.scalar("SELECT args FROM actions WHERE attempt_id=?", (aid,))))  # type: ignore[union-attr]
    assert args == {"text": EXIBICAO}


async def test_sem_persona_o_registro_fica_como_antes(harness: Harness) -> None:
    run_id, aid = _execucao(harness, com_persona=False)
    harness.state.repo.log_intent(aid, "type_text", {"text": EXIBICAO}, f"Digitar {EXIBICAO}",  # type: ignore[union-attr]
                                  side_effect=False)
    assert EXIBICAO in _textos(harness, run_id)["actions"]


async def test_o_dado_digitado_inteiro_entra_no_mapa_do_objetivo(harness: Harness) -> None:
    """O e-mail não é citado pelo plano, mas identifica a persona: sai mascarado no diário, digitado e na razão."""
    run_id, aid = _execucao(harness)
    st = harness.state
    assert st is not None
    st.repo.log_intent(aid, "type_text", {"text": EMAIL}, f"entrar com {EMAIL}", side_effect=False)
    assert EMAIL not in _textos(harness, run_id)["actions"]
