"""31.122 F2, 31.123 F2 e 31.127 (migração 121, adendo v1.85 e v1.86): achados da prova conjunta de 06/10.

Na reprodução r-20261006102728-1157c6 (android-04, Configurações, fluxo ensinado com a busca):

- 31.122 F2: a recusa da pós-condição que já vale na partida comparava só com `screen_lines` (conteúdo filtrado, 8
  linhas). O id `search_action_bar_title` cai no filtro de interface, então "Search settings" passou, e a etapa
  "comprovou" na tela inicial depois de um press_back. Agora o recorder guarda os elementos compactos da tela
  (`training_inputs.screen_elements`, uso interno, fora do GET), e `ja_valem` aplica a regra do verificador
  (`contains_text` e `find_selector`), inclusive em `element_present`. A recusa e a prévia trazem
  `pos_condicoes_ja_valem` estruturado (v1.86).
- 31.123 F2: a etapa que termina no pacote vizinho (o toque no app abre a busca de outro pacote) também o aceita. A tela
  em que ela termina é a da entrada seguinte.
- 31.127: a etapa ensinada espera a anterior ser comprovada (`depends_on`), salvo `independente: true` (v1.85). Antes, a
  2ª etapa rodou com a 1ª em `retry_wait`.

Nível de prova: `simulated` (funções puras e harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.automation.hierarchy import UiTree
from app.models import Plan, PlannerInfo, PlanStep, Postcondition, RunCreate
from app.modules.learning.presentation.treino import _training_error
from app.training import partida
from app.training.recorder import TrainingError
from app.training.reparo_da_gravacao import marcar_telas
from app.training.skills import em_sequencia

from .conftest import Harness
from .test_modo_treinamento import _no_controle
from .test_treino_segredo_na_gravacao import _arvore, _el
from .test_treino_validacao_do_salvar import _etapa

PKG, BUSCA = "com.pocqa.messenger", "com.google.android.settings.intelligence"
BARRA = f"{PKG}:id/search_action_bar_title"


def _segredo(texto: str | None) -> bool:
    return bool(texto) and "123456" in str(texto)


# ------------------------------------------------------------------ 31.122 F2, funções puras
def test_os_elementos_compactos_tiram_senha_texto_digitado_e_segredo() -> None:
    els = [_el("a", text="Buscar", rid=BARRA, bounds=(0, 0, 10, 10)), _el("b", text="x", password=True),
           _el("c", text="o que a pessoa digitou", editable=True, rid=f"{PKG}:id/campo"),
           _el("d", text="Seu código é 123456"), _el("e", desc="Voltar"), _el("f")]
    assert partida.elementos_compactos(els, _segredo) == [
        {"t": "Buscar", "r": BARRA, "b": [0, 0, 10, 10]}, {"r": f"{PKG}:id/campo", "b": [0, 0, 100, 100]},
        {"d": "Voltar", "b": [0, 0, 100, 100]}]
    muitos = [_el(str(n), text=f"linha {n}") for n in range(partida.ELEMENTOS + 5)]
    assert len(partida.elementos_compactos(muitos, _segredo)) == partida.ELEMENTOS


def _passo(chave: str, entradas: list[int], kind: str, valor: str) -> dict[str, Any]:
    return {"key": chave, "title": chave, "inputs": entradas, "postcondition": {"kind": kind, "value": valor}}


INICIO = {"seq": 1, "type": "tap", "screen_lines": ["Settings", "Network & internet"],
          "screen_elements": [{"t": "Settings"}, {"t": "Search settings", "r": "com.android.settings:id/search_action_bar_title"},
                              {"r": "com.android.settings:id/search_action_bar"}, {"t": "Network & internet"}]}
BUSCANDO = {"seq": 2, "type": "text", "screen_lines": [],
            "screen_elements": [{"d": "Back"}, {"t": "Search settings"}, {"t": "No results"},
                                {"r": f"{BUSCA}:id/open_search_view_edit_text"}]}


def test_o_texto_de_id_de_interface_que_ja_vale_na_partida_e_achado() -> None:
    """O caso da prova: "Search settings" não está nas `screen_lines`, mas está na tela (e o verificador o vê)."""
    (achado,) = partida.ja_valem([_passo("abrir_busca", [1], "text_visible", "Search settings")], [INICIO, BUSCANDO])
    assert achado["kind"] == "text_visible" and achado["valor"] == "Search settings"
    assert achado["sugestoes"] == ["Back", "No results"]               # da tela seguinte, fora o que já valia
    sem_elementos = {k: v for k, v in INICIO.items() if k != "screen_elements"}
    assert partida.ja_valem([_passo("a", [1], "text_visible", "Search settings")], [sem_elementos, BUSCANDO]) == []


def test_element_present_que_ja_vale_na_partida_e_achado_pelo_seletor() -> None:
    for seletor in ("text==Search settings", "id=com.android.settings:id/search_action_bar", "Network"):
        (achado,) = partida.ja_valem([_passo("a", [1], "element_present", seletor)], [INICIO, BUSCANDO])
        assert achado["kind"] == "element_present" and achado["valor"] == seletor
    assert "o elemento “text==Search settings” já está" in partida.aviso(
        partida.ja_valem([_passo("a", [1], "element_present", "text==Search settings")], [INICIO, BUSCANDO]))[0]
    for seletor in (f"id={BUSCA}:id/open_search_view_edit_text", "text==No results", "desc==Back"):
        assert partida.ja_valem([_passo("a", [1], "element_present", seletor)], [INICIO, BUSCANDO]) == []


def test_sessao_antiga_cai_nas_linhas_e_no_titulo() -> None:
    antiga = {"seq": 1, "type": "tap", "screen_lines": ["Network & internet"], "screen_title": "Configurações"}
    assert partida.ja_valem([_passo("a", [1], "text_visible", "Configurações")], [antiga])[0]["valor"] == "Configurações"
    assert partida.ja_valem([_passo("a", [1], "element_present", "text==Network & internet")], [antiga])
    assert partida.ja_valem([_passo("a", [1], "element_present", "desc==Network & internet")], [antiga]) == []


def test_nem_um_pedaco_do_dado_da_persona_vira_sugestao() -> None:
    """Revisão de segredos: o primeiro nome sozinho, de um valor de duas palavras, também não é sugerido."""
    seguinte = {"seq": 2, "type": "tap", "screen_elements": [{"t": "Souza enviou"}, {"t": "Nova mensagem"}]}
    (achado,) = partida.ja_valem([_passo("abrir_busca", [1], "text_visible", "Search settings")], [INICIO, seguinte],
                                 evitar=["Ana Souza"])
    assert achado["sugestoes"] == ["Nova mensagem"]


def test_a_lista_estruturada_leva_a_marca_da_persona() -> None:
    achado = {"key": "abrir", "titulo": "abrir", "kind": "text_visible", "valor": "conversa com Ana",
              "sugestoes": ["Ana respondeu", "Nova mensagem"]}
    (item,) = partida.estruturados([achado], {"perfil_nome": "Ana"})
    assert item == {"etapa": "abrir", "valor": "conversa com {perfil_nome}",
                    "sugestoes": ["{perfil_nome} respondeu", "Nova mensagem"], "message": item["message"]}
    assert "Ana" not in json.dumps(item, ensure_ascii=False) and item["message"].startswith("Etapa “abrir”")
    assert partida.estruturados([]) == []


# ------------------------------------------------------------------ 31.123 F2, função pura
def test_a_entrada_seguinte_pula_a_descartada_e_falta_na_ultima_etapa() -> None:
    entradas = [{"seq": 1}, {"seq": 2}, {"seq": 3, "package": BUSCA}]
    assert partida.entrada_seguinte(entradas, [1]) == {"seq": 2}
    assert partida.entrada_seguinte(entradas, [1], descartadas=[2]) == {"seq": 3, "package": BUSCA}
    assert partida.entrada_seguinte(entradas, [3]) is None and partida.entrada_seguinte(entradas, []) is None


# ------------------------------------------------------------------ 31.127, função pura
def _ps(*chaves: str, deps: dict[str, list[str]] | None = None) -> list[PlanStep]:
    return [PlanStep(key=k, title=k, goal=k, depends_on=(deps or {}).get(k, []),
                     postcondition=Postcondition(kind="model_judged", value="", description=k)) for k in chaves]


def test_cada_etapa_espera_a_anterior_salvo_a_independente() -> None:
    assert [s.depends_on for s in em_sequencia(_ps("aa", "bb", "cc"), [False] * 3)] == [[], ["aa"], ["bb"]]
    assert [s.depends_on for s in em_sequencia(_ps("aa", "bb", "cc"), [False, True, False])] == [[], [], ["bb"]]
    assert [s.depends_on for s in em_sequencia(_ps("aa", "bb", "cc", deps={"cc": ["aa"]}), [False] * 3)] == [
        [], ["aa"], ["aa"]]


# ------------------------------------------------------------------ no ensino (harness)
async def _gravada(harness: Harness) -> tuple[Any, str]:
    """Abre o app, toca na barra de busca (texto de id de interface, fora das `screen_lines`) e digita na busca, que
    é de outro pacote (o vizinho)."""
    st, rt, lease = await _no_controle(harness)
    s = st.training.start("android-01", intent="Buscar no app", lease_id=lease, app_id="qa-messenger")
    st.training.record(rt, {"type": "open_app", "app_id": "qa-messenger"}, None)
    barra = _el("e1", text="Buscar no app", rid=BARRA, clickable=True, bounds=(0, 0, 100, 100))
    st.training.record(rt, {"type": "tap", "x": 50, "y": 50},
                       _arvore(_el("e0", text="Conversas"), barra, _el("e9", text="segredo", password=True)))
    campo = _el("e2", rid=f"{BUSCA}:id/campo", editable=True, focused=True)
    resultado = _el("e3", text="Nenhum resultado", rid=f"{BUSCA}:id/vazio")
    st.training.record(rt, {"type": "text", "text": "wifi"},
                       UiTree(elements=[campo, resultado], packages=[BUSCA], sensitive=False))
    st.training.stop(s["id"], lease_id=lease)
    return st, s["id"]


def _proposta(pos_abrir: dict[str, Any], **extra_buscar: Any) -> dict[str, Any]:
    return {"summary": "buscar", "command_template": "busque wifi no app", "app_id": "qa-messenger",
            "parameters": [], "discarded": [], "questions": [],
            "steps": [_etapa("abrir", [1]), _etapa("abrir_busca", [2], postcondition=pos_abrir),
                      _etapa("buscar", [3], **extra_buscar)]}


async def test_os_elementos_ficam_fora_do_get_e_sem_senha(harness: Harness) -> None:
    st, sid = await _gravada(harness)
    assert all("screen_elements" not in e for e in st.training.get(sid)["inputs"])
    elementos = st.training.elementos_da_tela(sid)
    assert {"t": "Buscar no app", "r": BARRA, "b": [0, 0, 100, 100]} in elementos[2]
    assert "segredo" not in json.dumps(elementos) and "wifi" not in json.dumps(elementos)
    assert 1 not in elementos                                            # open_app sem árvore: nada


async def test_a_previa_e_a_recusa_trazem_a_lista_estruturada(harness: Harness) -> None:
    st, sid = await _gravada(harness)
    ja_estava = _proposta({"kind": "text_visible", "value": "Buscar no app", "description": "x"})
    previa = await st.skills.preview(sid, proposal=ja_estava, profile_ids=[], group_ids=[])
    (item,) = previa["pos_condicoes_ja_valem"]
    assert item["etapa"] == "abrir_busca" and item["valor"] == "Buscar no app" and item["sugestoes"] == ["Nenhum resultado"]
    assert item["message"] in previa["warnings"] and all(isinstance(w, str) for w in previa["warnings"])
    fluxos = st.db.scalar("SELECT COUNT(*) FROM flows")
    with pytest.raises(TrainingError) as exc:
        await st.skills.save(sid, proposal=ja_estava, profile_ids=[], group_ids=[])
    detalhe = _training_error(exc.value).detail
    assert isinstance(detalhe, dict) and detalhe["code"] == "pos_condicao_ja_vale" and exc.value.status == 400
    assert detalhe["pos_condicoes_ja_valem"] == [item] and detalhe["message"] == item["message"]
    assert st.db.scalar("SELECT COUNT(*) FROM flows") == fluxos
    o_seletor = _proposta({"kind": "element_present", "value": f"id={BARRA}", "description": "x"})
    with pytest.raises(TrainingError):
        await st.skills.save(sid, proposal=o_seletor, profile_ids=[], group_ids=[])
    certa = _proposta({"kind": "text_visible", "value": "Nenhum resultado", "description": "x"})
    assert (await st.skills.preview(sid, proposal=certa, profile_ids=[], group_ids=[]))["pos_condicoes_ja_valem"] == []
    salvo = await st.skills.save(sid, proposal=certa, profile_ids=[], group_ids=[])
    plano = json.loads(st.db.scalar("SELECT plan FROM flows WHERE id=?", (salvo["flow_id"],)))
    por_chave = {p["key"]: p for p in plano["steps"]}
    # 31.123 F2: a 1ª etapa termina na busca (o pacote vizinho) e o aceita; 31.127: a 2ª espera a 1ª
    assert por_chave["abrir_busca"]["pacotes_aceitos"] == [BUSCA] and por_chave["buscar"]["pacotes_aceitos"] == [BUSCA]
    assert "pacotes_aceitos" not in por_chave["abrir"]                  # termina no próprio app
    assert [por_chave[k]["depends_on"] for k in ("abrir", "abrir_busca", "buscar")] == [[], ["abrir"], ["abrir_busca"]]


async def test_a_etapa_independente_fica_sem_dependencia_e_o_tipo_errado_e_recusado(harness: Harness) -> None:
    st, sid = await _gravada(harness)
    pos = {"kind": "text_visible", "value": "Nenhum resultado", "description": "x"}
    with pytest.raises(TrainingError) as exc:
        await st.skills.preview(sid, proposal=_proposta(pos, independente="sim"), profile_ids=[], group_ids=[])
    assert exc.value.code == "etapa_invalida" and "independente" in exc.value.message
    salvo = await st.skills.save(sid, proposal=_proposta(pos, independente=True), profile_ids=[], group_ids=[])
    plano = json.loads(st.db.scalar("SELECT plan FROM flows WHERE id=?", (salvo["flow_id"],)))
    assert [p["depends_on"] for p in plano["steps"]] == [[], ["abrir"], []]


async def test_o_save_e_o_reparo_marcam_o_dado_da_persona_nos_elementos(harness: Harness) -> None:
    st, sid = await _gravada(harness)
    st.db.execute("UPDATE training_inputs SET screen_elements=? WHERE session_id=? AND seq=2",
                  (json.dumps([{"t": "Conversa com Ana"}, {"d": "Foto de Ana", "r": BARRA}]), sid))
    assert marcar_telas(st.db, sid, {"perfil_nome": "Ana"}, escrever=False) == 1
    assert marcar_telas(st.db, sid, {"perfil_nome": "Ana"}) == 1
    assert st.training.elementos_da_tela(sid)[2] == [{"t": "Conversa com {perfil_nome}"},
                                                     {"d": "Foto de {perfil_nome}", "r": BARRA}]
    assert marcar_telas(st.db, sid, {"perfil_nome": "Ana"}) == 0                                   # idempotente


# ------------------------------------------------------------------ 31.127, o executor espera a anterior
async def test_a_etapa_seguinte_fica_pendente_enquanto_a_anterior_espera_nova_tentativa(harness: Harness) -> None:
    st = harness.state
    run = st.runs.create(RunCreate(command="buscar no app", instance_ids=["android-01"],
                                   idempotency_key="sequencia-127", mode="plan"))
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "completed", "completed_with_issues", "failed"))
    plano = Plan(summary="s", app_id="qa-messenger", app_package=PKG, steps=em_sequencia(_ps("abrir", "buscar"), [False] * 2),
                 planner=PlannerInfo(provider="t", model="t", simulated=True))
    st.db.execute("DELETE FROM steps WHERE run_id=?", (run.id,))
    st.db.execute("DELETE FROM objectives WHERE run_id=?", (run.id,))
    st.repo.save_plan(run.id, plano)
    st.repo.materialize(run.id, plano, [{"instance_id": "android-01"}])
    abrir, buscar = (f"{run.id}:android-01:v1:{k}" for k in ("abrir", "buscar"))

    def status(sid: str) -> str:
        return str(st.db.scalar("SELECT status FROM steps WHERE id=?", (sid,)))

    st.repo.promote(run.id)
    assert status(abrir) == "ready" and status(buscar) == "pending"
    st.db.execute("UPDATE steps SET status='retry_wait', next_retry_at='2999-01-01T00:00:00.000Z' WHERE id=?", (abrir,))
    st.repo.promote(run.id)
    assert status(buscar) == "pending"                                  # antes do 31.127, ficava pronta aqui
    st.db.execute("UPDATE steps SET status='succeeded' WHERE id=?", (abrir,))
    st.repo.promote(run.id)
    assert status(buscar) == "ready"
