"""Item 31.86 B: prévia do salvar e refazer as receitas do treino.

Simulado (aparelho falso do harness, sem IA): a prévia responde o que o `save` responderia sem escrever nada, e a
sessão salva com o aparelho fora do ar ganha as receitas depois, sem duplicar."""
from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from app.models import InstanceState
from app.taskqueue.recipes import step_template_hash
from app.training.recorder import TrainingError
from app.training.skills import JA_HAVIA_RECEITA, _aviso_sem_persona

from .conftest import Harness
from .test_modo_treinamento import SEGREDO, _entrada, _no_controle
from .test_perfil_bloqueado_e_capacidades import _cliente
from .test_treino_validacao_do_salvar import CASOS, COMANDO, _etapa, _sessao_gravada

PACOTE_DO_APP = "qa-messenger"          # id do app no harness; o pacote sai da tabela `apps`


# 30.81: estes treinos são gravados sem persona no aparelho; a prévia e o salvar avisam do mesmo jeito.
_SEM_PERSONA = _aviso_sem_persona({"profile_id": None})


def _proposta() -> dict[str, Any]:
    """Quatro etapas com destinos diferentes: abrir e conversa viram receita; `escrever` só tem o texto sigiloso e
    `enviar` termina numa tecla (depende do estado de quem ensinou)."""
    return {"summary": "responder DM", "command_template": COMANDO, "app_id": PACOTE_DO_APP,
            "parameters": [{"name": "contato", "example": "QA-001", "description": ""},
                           {"name": "mensagem", "example": "Olá, tudo certo?", "description": ""}],
            "steps": [_etapa("abrir", [1]), _etapa("conversa", [2, 3]), _etapa("escrever", [4]), _etapa("enviar", [5, 6])],
            "discarded": [], "questions": []}


async def _gravar(st: Any, rt: Any, lease: str, fake: Any) -> str:
    s = st.training.start("android-01", intent="Responder uma DM no QA Messenger", lease_id=lease, app_id=PACOTE_DO_APP)
    st.training.record(rt, {"type": "open_app", "app_id": PACOTE_DO_APP}, None)
    fake.screen = "home"
    await _entrada(st, rt, lease, type="tap", x=100, y=200 + 3 * 120 + 30)      # QA-001
    await _entrada(st, rt, lease, type="tap", x=100, y=1200)                    # campo de mensagem
    await _entrada(st, rt, lease, type="text", text=SEGREDO)                    # parece segredo: só has_text
    await _entrada(st, rt, lease, type="tap", x=640, y=1200)                    # Enviar
    await _entrada(st, rt, lease, type="key", key="back")                       # sobra de quem ensinou
    st.training.stop(s["id"], lease_id=lease)
    return s["id"]


async def _sessao_mista(harness: Harness) -> tuple[Any, Any, str, str]:
    st, rt, lease = await _no_controle(harness)
    sid = await _gravar(st, rt, lease, harness.fakes["android-01"])
    return st, rt, lease, sid


def _esquecer_o_aparelho(rt: Any) -> None:
    """Fora do ar e sem nada lembrado da última leitura (versão do app, idioma e densidade)."""
    rt.state = InstanceState.stopped
    rt.app_versions.clear()
    rt.ui_variant = None


def _foto(st: Any, sid: str) -> dict[str, Any]:
    """Tudo que um salvar escreveria: contagens do banco e a linha da sessão."""
    contagens = {t: st.db.scalar(f"SELECT COUNT(*) FROM {t}")
                 for t in ("flows", "recipes", "flow_scope", "flow_required_apps", "skill_versions")}
    sessao = st.db.one("SELECT status, flow_id, proposal, updated_at FROM training_sessions WHERE id=?", (sid,))
    return {**contagens, "sessao": dict(sessao)}


def _por_chave(passos: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {p["key"]: p for p in passos}


async def test_previa_diz_o_mesmo_que_o_save_e_nao_escreve_nada(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st, rt, lease, sid = await _sessao_mista(harness)
    emitidos: list[str] = []
    original = st.bus.emit
    monkeypatch.setattr(st.bus, "emit", lambda *a, **k: (emitidos.append(str(a[1])), original(*a, **k))[1])
    antes = _foto(st, sid)

    previa = await st.skills.preview(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert _foto(st, sid) == antes                                  # nem fluxo, escopo, receita, status nem proposta
    assert not [m for m in emitidos if "Habilidade" in m]           # nem o evento de "habilidade salva"

    assert set(previa) == {"steps", "warnings", "scope", "pos_condicoes_ja_valem"}            # v1.86: a lista ao lado
    assert previa["warnings"] == _SEM_PERSONA and previa["pos_condicoes_ja_valem"] == []    # 30.81: gravado sem persona
    for linha in previa["steps"]:
        assert set(linha) == {"key", "title", "recipe", "reason", "pacotes_aceitos"}    # as ações da receita nunca saem
    dela = _por_chave(previa["steps"])
    assert dela["escrever"]["recipe"] is False and "sigiloso" in dela["escrever"]["reason"]
    assert dela["enviar"]["recipe"] is False and "tecla back" in dela["enviar"]["reason"]
    assert dela["abrir"]["recipe"] is True
    assert SEGREDO not in json.dumps(previa, ensure_ascii=False)

    salvo = await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    do_save = _por_chave(salvo["steps"])
    assert list(dela) == list(do_save)
    for chave, linha in dela.items():
        assert linha["recipe"] == do_save[chave]["recipe"], chave
        if not linha["recipe"]:
            assert linha["reason"] == do_save[chave]["reason"], chave         # o motivo literal, o mesmo
    assert salvo["warnings"] == previa["warnings"]
    assert any("Habilidade" in m for m in emitidos)                           # o save, sim, emite


async def test_previa_conta_a_receita_que_ja_existe_sem_gravar_outra(harness: Harness) -> None:
    st, rt, lease, sid = await _sessao_mista(harness)
    await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    sid2 = await _gravar(st, rt, lease, harness.fakes["android-01"])
    outra = _proposta()
    outra["command_template"] = "mande a DM de {contato} dizendo {mensagem}"       # outro comando, mesma etapa
    antes = _foto(st, sid2)
    previa = _por_chave((await st.skills.preview(sid2, proposal=outra, profile_ids=[], group_ids=[]))["steps"])
    assert previa["abrir"]["recipe"] is False and previa["abrir"]["reason"] == JA_HAVIA_RECEITA
    assert _foto(st, sid2) == antes


async def test_previa_e_save_dizem_que_a_demonstracao_substitui_a_receita_de_outro_caminho(harness: Harness) -> None:
    """30.79 na junção com o 31.86: a etapa que já tem receita de OUTRO caminho ganha a demonstração nova. A prévia
    diz que ela substituirá a viva, o salvar diz o mesmo, e a viva sai como `superseded`."""
    st, rt, lease, sid = await _sessao_mista(harness)
    await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    velha = st.db.one("SELECT id, version, actions FROM recipes WHERE step_key='abrir' AND status='active'")
    acoes = json.loads(velha["actions"])
    acoes[0]["selectors"] = [{"kind": "rid", "rid": "app:id/outro_caminho"}]       # a viva passa a ser outro caminho
    st.db.execute("UPDATE recipes SET actions=? WHERE id=?", (json.dumps(acoes), velha["id"]))
    sid2 = await _gravar(st, rt, lease, harness.fakes["android-01"])
    outra = _proposta()
    outra["command_template"] = "mande a DM de {contato} dizendo {mensagem}"       # outro comando, mesma etapa
    troca = f", substituindo a v{velha['version']} (receita {velha['id']})"
    previa = _por_chave((await st.skills.preview(sid2, proposal=outra, profile_ids=[], group_ids=[]))["steps"])
    assert previa["abrir"] == {**previa["abrir"], "recipe": True, "reason": "receita será gravada ao salvar" + troca}
    assert st.db.scalar("SELECT status FROM recipes WHERE id=?", (velha["id"],)) == "active"     # a prévia não grava
    salvo = _por_chave((await st.skills.save(sid2, proposal=outra, profile_ids=[], group_ids=[]))["steps"])
    assert salvo["abrir"]["recipe"] is True and salvo["abrir"]["reason"] == "receita gravada" + troca
    assert st.db.scalar("SELECT status FROM recipes WHERE id=?", (velha["id"],)) == "superseded"


@pytest.mark.parametrize(("codigo", "estraga"), CASOS, ids=[f"{c}-{f.__name__}" for c, f in CASOS])
async def test_previa_recusa_com_o_mesmo_codigo_do_save_e_sem_escrever(harness: Harness, codigo: str, estraga: Any) -> None:
    st, sid = await _sessao_gravada(harness)
    proposta = copy.deepcopy({"summary": "responder DM", "command_template": COMANDO, "app_id": PACOTE_DO_APP,
                              "parameters": [{"name": "contato", "example": "QA-001", "description": ""},
                                             {"name": "mensagem", "example": "oi", "description": ""}],
                              "steps": [_etapa("abrir", [1]), _etapa("escrever", [2, 3])],
                              "discarded": [{"seq": 4, "why": "engano"}], "questions": []})
    estraga(proposta)
    antes = _foto(st, sid)
    with pytest.raises(TrainingError) as da_previa:
        await st.skills.preview(sid, proposal=proposta, profile_ids=[], group_ids=[])
    with pytest.raises(TrainingError) as do_save:
        await st.skills.save(sid, proposal=proposta, profile_ids=[], group_ids=[])
    assert (da_previa.value.code, da_previa.value.status, da_previa.value.message) == (
        do_save.value.code, do_save.value.status, do_save.value.message) == (codigo, 400, do_save.value.message)
    assert _foto(st, sid) == antes


async def test_previa_recusa_o_que_o_save_recusaria_antes_de_gravar(harness: Harness) -> None:
    st, rt, lease, sid = await _sessao_mista(harness)
    with pytest.raises(TrainingError) as perfil:
        await st.skills.preview(sid, proposal=_proposta(), profile_ids=["nao-existe"], group_ids=[])
    assert (perfil.value.code, perfil.value.status) == ("unknown_profile", 400)
    with pytest.raises(TrainingError) as grupo:
        await st.skills.preview(sid, proposal=_proposta(), profile_ids=[], group_ids=["nao-existe"])
    assert (grupo.value.code, grupo.value.status) == ("unknown_group", 400)
    with pytest.raises(TrainingError) as vazio:
        await st.skills.preview(sid, proposal=None, profile_ids=[], group_ids=[])      # sem proposta guardada
    assert (vazio.value.code, vazio.value.status) == ("no_proposal", 400)

    await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    with pytest.raises(TrainingError) as fechada:
        await st.skills.preview(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert (fechada.value.code, fechada.value.status) == ("closed", 409)

    sid2 = await _gravar(st, rt, lease, harness.fakes["android-01"])
    antes = _foto(st, sid2)
    with pytest.raises(TrainingError) as repetido:                                   # o 409 que só o gravar via
        await st.skills.preview(sid2, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert (repetido.value.code, repetido.value.status) == ("duplicate_command", 409)
    with pytest.raises(TrainingError) as do_save:
        await st.skills.save(sid2, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert do_save.value.code == "duplicate_command"
    assert _foto(st, sid2) == antes


async def test_save_com_o_aparelho_fora_do_ar_e_sem_identidade_lembrada_nao_chuta_receita(harness: Harness) -> None:
    st, rt, lease, sid = await _sessao_mista(harness)
    _esquecer_o_aparelho(rt)
    previa = _por_chave((await st.skills.preview(sid, proposal=_proposta(), profile_ids=[], group_ids=[]))["steps"])
    salvo = await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    do_save = _por_chave(salvo["steps"])
    assert st.db.scalar("SELECT COUNT(*) FROM recipes") == 0
    for chave in ("abrir", "conversa"):
        assert do_save[chave]["recipe"] is False
        assert "fora do ar" in do_save[chave]["reason"] and "versão do app" in do_save[chave]["reason"]
        assert "idioma e a densidade" in do_save[chave]["reason"] and "Refaça as receitas" in do_save[chave]["reason"]
        assert previa[chave] == do_save[chave]                  # a prévia avisa o mesmo, antes de salvar
    assert "sigiloso" in do_save["escrever"]["reason"]          # o motivo da destilação continua o literal


async def test_save_fora_do_ar_usa_a_identidade_lembrada_e_a_receita_nasce_com_ela(harness: Harness) -> None:
    st, rt, lease, sid = await _sessao_mista(harness)
    pacote = st.db.scalar("SELECT package FROM apps WHERE id=?", (PACOTE_DO_APP,))
    _esquecer_o_aparelho(rt)
    rt.app_versions[pacote] = "9.9(99)"                          # o que a última leitura deixou (cache do executor)
    rt.ui_variant = "pt-BR/xhdpi"
    salvo = await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    do_save = _por_chave(salvo["steps"])
    assert do_save["abrir"]["recipe"] and do_save["conversa"]["recipe"], do_save
    linhas = st.db.query("SELECT app_package, app_version, variant, learned_from_step FROM recipes")
    assert len(linhas) == 2
    assert {(r["app_package"], r["app_version"], r["variant"], r["learned_from_step"]) for r in linhas} == {
        (pacote, "9.9(99)", "pt-BR/xhdpi", f"training:{sid}")}


async def test_save_fora_do_ar_com_so_a_versao_lembrada_diz_o_que_falta(harness: Harness) -> None:
    st, rt, lease, sid = await _sessao_mista(harness)
    pacote = st.db.scalar("SELECT package FROM apps WHERE id=?", (PACOTE_DO_APP,))
    _esquecer_o_aparelho(rt)
    rt.app_versions[pacote] = "9.9(99)"
    salvo = await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    razao = _por_chave(salvo["steps"])["abrir"]["reason"]
    assert _por_chave(salvo["steps"])["abrir"]["recipe"] is False
    assert "idioma e a densidade" in razao and "versão do app" not in razao
    assert st.db.scalar("SELECT COUNT(*) FROM recipes") == 0


async def test_refazer_grava_as_receitas_que_faltam_e_a_segunda_chamada_nao_duplica(harness: Harness) -> None:
    st, rt, lease, sid = await _sessao_mista(harness)
    _esquecer_o_aparelho(rt)
    salvo = await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert st.db.scalar("SELECT COUNT(*) FROM recipes") == 0 and not any(s["recipe"] for s in salvo["steps"])

    rt.state = InstanceState.online                               # o aparelho voltou
    primeira = await st.skills.refazer_receitas(sid)
    criadas = [s["key"] for s in primeira["steps"] if s["recipe"]]
    assert {"abrir", "conversa"} <= set(criadas), primeira["steps"]
    assert primeira["created"] == len(criadas) == st.db.scalar("SELECT COUNT(*) FROM recipes")
    assert primeira["flow_id"] == salvo["flow_id"] and primeira["session"]["status"] == "saved"
    por = _por_chave(primeira["steps"])
    assert "sigiloso" in por["escrever"]["reason"] and "tecla back" in por["enviar"]["reason"]
    # a chave da receita é o hash da etapa DO FLUXO, a mesma que o executor calcula na reprodução
    from app.models import Plan
    plano = Plan.model_validate_json(st.db.one("SELECT plan FROM flows WHERE id=?", (salvo["flow_id"],))["plan"])
    hashes = {step_template_hash(p) for p in plano.steps if p.key in criadas}
    linhas = st.db.query("SELECT step_hash, learned_from_step, status FROM recipes")
    assert {r["step_hash"] for r in linhas} == hashes
    assert {r["learned_from_step"] for r in linhas} == {f"training:{sid}"} and {r["status"] for r in linhas} == {"active"}

    segunda = await st.skills.refazer_receitas(sid)
    assert segunda["created"] == 0 and st.db.scalar("SELECT COUNT(*) FROM recipes") == len(criadas)
    assert _por_chave(segunda["steps"])["abrir"]["reason"] == JA_HAVIA_RECEITA
    assert SEGREDO not in json.dumps([primeira["steps"], segunda["steps"]], ensure_ascii=False)


async def test_refazer_respeita_a_receita_ativa_que_ja_existe(harness: Harness) -> None:
    st, rt, lease, sid = await _sessao_mista(harness)
    salvo = await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])     # no ar: já grava
    n = st.db.scalar("SELECT COUNT(*) FROM recipes")
    assert n >= 2 and salvo["steps"][0]["recipe"]
    de_novo = await st.skills.refazer_receitas(sid)
    assert de_novo["created"] == 0 and st.db.scalar("SELECT COUNT(*) FROM recipes") == n


async def test_refazer_numa_sessao_nao_salva_e_409(harness: Harness) -> None:
    st, rt, lease, sid = await _sessao_mista(harness)
    for _ in range(2):                                            # gravada, e depois com proposta (ainda sem salvar)
        with pytest.raises(TrainingError) as erro:
            await st.skills.refazer_receitas(sid)
        assert (erro.value.code, erro.value.status) == ("sessao_nao_salva", 409)
        st.db.execute("UPDATE training_sessions SET status='proposed', proposal=? WHERE id=?", (json.dumps(_proposta()), sid))
    assert st.db.scalar("SELECT COUNT(*) FROM recipes") == 0
    with pytest.raises(TrainingError) as sumiu:
        await st.skills.refazer_receitas("nao-existe")
    assert (sumiu.value.code, sumiu.value.status) == ("not_found", 404)


async def test_rotas_http_previa_e_refazer(harness: Harness) -> None:
    st, rt, lease, sid = await _sessao_mista(harness)
    _esquecer_o_aparelho(rt)
    antes = _foto(st, sid)
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/preview", json={"proposal": _proposta()})
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert set(corpo) == {"steps", "warnings", "scope", "pos_condicoes_ja_valem"} and corpo["warnings"] == _SEM_PERSONA
        assert [s["key"] for s in corpo["steps"]] == ["abrir", "conversa", "escrever", "enviar"]
        assert all(set(s) == {"key", "title", "recipe", "reason", "pacotes_aceitos"} and isinstance(s["recipe"], bool)
                   for s in corpo["steps"])
        assert SEGREDO not in r.text
        assert _foto(st, sid) == antes

        ruim = _proposta()
        ruim["parameters"].append({"name": "assunto", "example": "x", "description": ""})
        r = await c.post(f"/api/training/{sid}/preview", json={"proposal": ruim})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "parametro_fora_do_comando", r.text
        r = await c.post(f"/api/training/{sid}/preview", json={"proposal": _proposta(), "extra": 1})
        assert r.status_code == 422, r.text                                          # extra="forbid", como o save
        r = await c.post("/api/training/nao-existe/preview", json={})
        assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"

        r = await c.post(f"/api/training/{sid}/recipes")                              # ainda não salva
        assert r.status_code == 409 and r.json()["detail"]["code"] == "sessao_nao_salva", r.text

        r = await c.post(f"/api/training/{sid}/save", json={"proposal": _proposta()})
        assert r.status_code == 200 and not any(s["recipe"] for s in r.json()["steps"]), r.text   # fora do ar
        rt.state = InstanceState.online
        r = await c.post(f"/api/training/{sid}/recipes")
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert set(corpo) == {"session", "flow_id", "steps", "created", "ensinado_em_prova"} and corpo["created"] >= 2
        assert all(set(s) == {"key", "title", "recipe", "reason", "pacotes_aceitos"} for s in corpo["steps"])
        r = await c.post(f"/api/training/{sid}/recipes")
        assert r.status_code == 200 and r.json()["created"] == 0
        assert st.db.scalar("SELECT COUNT(*) FROM recipes") == corpo["created"]


@pytest.mark.parametrize("status", ["quarantined", "superseded"])
async def test_refazer_nao_ressuscita_a_chave_que_o_aprendizado_rebaixou_ou_uma_pessoa_desligou(
        harness: Harness, status: str) -> None:
    st, rt, lease, sid = await _sessao_mista(harness)
    await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])     # no ar: já grava
    antes = st.db.scalar("SELECT COUNT(*) FROM recipes")
    assert antes >= 2
    st.db.execute("UPDATE recipes SET status=?", (status,))                          # rebaixada / desligada
    de_novo = await st.skills.refazer_receitas(sid)
    assert de_novo["created"] == 0
    assert st.db.scalar("SELECT COUNT(*) FROM recipes") == antes
    assert st.db.scalar("SELECT COUNT(*) FROM recipes WHERE status='active'") == 0   # nada de ativa nova
    razoes = _por_chave(de_novo["steps"])
    assert f"a chave já teve receita (status {status})" in razoes["abrir"]["reason"]
    assert razoes["abrir"]["recipe"] is False


async def test_refazer_com_o_fluxo_desligado_e_409(harness: Harness) -> None:
    st, rt, lease, sid = await _sessao_mista(harness)
    _esquecer_o_aparelho(rt)
    salvo = await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    st.db.execute("UPDATE flows SET status='disabled' WHERE id=?", (salvo["flow_id"],))
    rt.state = InstanceState.online
    with pytest.raises(TrainingError) as erro:
        await st.skills.refazer_receitas(sid)
    assert (erro.value.code, erro.value.status) == ("fluxo_desligado", 409)
    assert st.db.scalar("SELECT COUNT(*) FROM recipes") == 0


async def test_refazer_respeita_o_veto_da_pessoa_mesmo_com_a_chave_virgem(harness: Harness) -> None:
    """O `save` do treino pula o veto (a pessoa ensina agora); o reparo não: ela já desligou esse caminho."""
    class Veto:
        def __init__(self) -> None:
            self.perguntas: list[str] = []

        def vetada(self, receita: Any) -> bool:
            self.perguntas.append(receita.step_hash)
            return True

        def exige_o_dono(self, recipe_id: int, receita: Any) -> bool | None:
            return False

        def mudou(self, mudanca: Any) -> None:
            return None

    st, rt, lease, sid = await _sessao_mista(harness)
    _esquecer_o_aparelho(rt)
    await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert st.db.scalar("SELECT COUNT(*) FROM recipes") == 0                          # chave virgem
    rt.state = InstanceState.online
    lojas = st.scheduler.executor.recipes
    veto = Veto()
    lojas.ouvinte = veto
    try:
        vetado = await st.skills.refazer_receitas(sid)
    finally:
        lojas.ouvinte = None
    assert vetado["created"] == 0 and st.db.scalar("SELECT COUNT(*) FROM recipes") == 0
    assert veto.perguntas, "o reparo precisa consultar o veto"
    assert _por_chave(vetado["steps"])["abrir"]["reason"] == "a pessoa vetou esta receita: o reparo não a recria"
    sem_veto = await st.skills.refazer_receitas(sid)                                   # sem veto: grava, como antes
    assert sem_veto["created"] >= 2
