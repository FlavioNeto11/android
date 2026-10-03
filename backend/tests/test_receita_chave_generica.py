"""RA-20 fatia B: a receita que serve a qualquer valor da etapa mora na chave GENÉRICA (sem a pós-condição escrita).

Prova `simulated`: banco migrado de teste e o Instagram falso, sem aparelho. As ações dos casos do classificador são
as das receitas reais medidas no central em 03/10/2026 (25 e 73: `open_profile` com o toque em "nasa"; 38, 40, 43 e
45: `open_thread_1` com "Options"/"Send message" e "Message"), com os @ de terceiros trocados por fictícios.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from app.db import Database, dumps
from app.metricas import metricas
from app.models import Plan, PlanStep, Postcondition
from app.planning.provider import PlanRequest, Usage
from app.taskqueue.receitas_genericas import semear
from app.taskqueue.recipes import (RecipeStore, eh_generica, hash_generico, hash_generico_da_etapa,
                                   hash_generico_da_linha, step_template_hash)

from .conftest import CountingProvider, Harness
from .fake_instagram import AtorDoInstagram, FakeInstagram
from .fake_skills import banco as banco_migrado

IG = "com.instagram.android"


def _toque(*seletores: dict[str, str]) -> dict[str, Any]:
    return {"tool": "tap", "commit": False, "why": "abrir", "args": {}, "selectors": list(seletores)}


# ================================================================== o classificador, nas receitas reais
NASA_25 = [_toque({"kind": "rid+text", "rid": f"{IG}:id/row_search_user_username", "text": "nasa"},
                  {"kind": "rid", "rid": f"{IG}:id/row_search_user_username"})]
NASA_73 = [_toque({"kind": "rid", "rid": f"{IG}:id/row_feed_photo_profile_name"}),
           _toque({"kind": "rid+text", "rid": f"{IG}:id/row_user_primary_name", "text": "nasa"})]
OPTIONS_38 = [_toque({"kind": "desc", "desc": "Options"}),
              _toque({"kind": "rid+desc", "rid": f"{IG}:id/context_menu_item", "desc": "Send message"},
                     {"kind": "desc", "desc": "Send message"})]
MESSAGE_40 = [_toque({"kind": "rid+desc", "rid": f"{IG}:id/button_container", "desc": "Message"},
                     {"kind": "rid", "rid": f"{IG}:id/button_container"}, {"kind": "desc", "desc": "Message"})]


@pytest.mark.parametrize(("acoes", "post", "params", "generica"), [
    (NASA_25, "perfil de @nasa aberto", {"username": "@nasa", "max_likes": "10"}, False),
    (NASA_73, "perfil de @nasa aberto", {"username": "@nasa", "target": "segunda publicação da grade"}, False),
    (OPTIONS_38, "conversa com @perfil_um aberta", {"target_username": "@perfil_um"}, True),
    (MESSAGE_40, "conversa com @perfil_dois aberta", {"username": "@perfil_dois"}, True),
])
def test_classificador_nas_receitas_reais(acoes: list[dict[str, Any]], post: str, params: dict[str, str],
                                          generica: bool) -> None:
    assert eh_generica(acoes, post, params) is generica


def test_classificador_normaliza_caixa_acento_e_arroba() -> None:
    joao = [_toque({"kind": "text", "text": "Joao"})]
    assert not eh_generica(joao, "conversa aberta", {"alvo": "@João"})          # o valor do parâmetro no seletor
    assert not eh_generica(joao, "conversa com @JOÃO aberta", {})               # o literal na pós-condição escrita
    assert eh_generica(joao, "conversa com @bia aberta", {"alvo": "@bia"})


def test_classificador_ignora_o_valor_da_vez_e_o_que_nao_identifica_o_alvo() -> None:
    digita = [{"tool": "type_text", "commit": False, "why": "Preencher nome com Ana", "selectors": [],
               "args": {"text": "{nome}", "clear_first": True, "press_enter": False}}]
    assert eh_generica(digita, "o campo contém Ana", {"nome": "Ana"})           # `{nome}` é o valor da vez; `why` não conta
    assert not eh_generica([{**digita[0], "args": {"text": "Ana Souza"}}], "campo preenchido", {"nome": "Ana Souza"})
    # os parâmetros de execução nunca identificam a etapa (a conta, o aparelho, a execução)
    conta = [_toque({"kind": "text", "text": "qa-user-09"})]
    assert eh_generica(conta, "tela inicial", {"account_label": "qa-user-09", "instance_id": "android-09"})


def test_classificador_conservador_com_rotulo_na_pos_condicao() -> None:
    """O rótulo do campo citado na pós-condição ("Nome") deixa a receita específica: errar para cá só adia o ganho."""
    nome = [{"tool": "type_text", "commit": False, "why": "nome", "args": {"text": "{nome}"},
             "selectors": [{"kind": "rid+text", "rid": "app:id/profile_name", "text": "Nome"}]}]
    assert not eh_generica(nome, 'o campo Nome contém exatamente "Robo"', {"nome": "Robo"})


# ================================================================== o hash
def _etapa(**kw: Any) -> PlanStep:
    post = Postcondition(kind=kw.pop("kind", "model_judged"), value=kw.pop("value", "conversa com @bia aberta"),
                         description="d")
    return PlanStep(key=kw.pop("key", "abrir_conversa"), title="t", goal="g", postcondition=post, **kw)


def test_so_a_etapa_julgada_sem_efeito_e_sem_guarda_tem_chave_generica() -> None:
    assert hash_generico_da_etapa(_etapa()) is not None
    assert hash_generico_da_etapa(_etapa(side_effect=True)) is None
    assert hash_generico_da_etapa(_etapa(commit_guard=["{username}"])) is None
    assert hash_generico_da_etapa(_etapa(kind="text_visible")) is None
    assert hash_generico_da_etapa(_etapa(kind="app_foreground", value="com.x")) is None
    assert hash_generico(None, False, "model_judged", None, []) is None          # campo ausente: específica
    assert hash_generico("k", None, "model_judged", None, []) is None
    assert hash_generico("k", False, "model_judged", None, None) is None


def test_a_redacao_da_pos_condicao_nao_muda_a_chave_generica() -> None:
    a, b = _etapa(value="conversa com @bia aberta"), _etapa(value="a conversa de @bia está aberta na tela")
    assert step_template_hash(a) != step_template_hash(b)
    assert hash_generico_da_etapa(a) == hash_generico_da_etapa(b)
    # sem texto, as duas chaves são a mesma (a consulta faz uma só)
    assert hash_generico_da_etapa(_etapa(value="")) == step_template_hash(_etapa(value=""))
    # a cópia do for_each tem a chave da etapa-modelo
    copia = _etapa(key="abrir_conversa_i2", value="outra redação").model_copy(update={"template_key": "abrir_conversa"})
    assert hash_generico_da_etapa(copia) == hash_generico_da_etapa(a)


def test_paridade_entre_a_etapa_e_a_linha_de_steps() -> None:
    e = _etapa(key="abrir_conversa_i1").model_copy(update={"template_key": "abrir_conversa"})
    linha = {"template_key": "abrir_conversa", "key": "abrir_conversa_i1", "side_effect": 0,
             "postcondition": e.postcondition.model_dump_json(), "commit_guard": "[]"}
    assert hash_generico_da_linha(linha) == hash_generico_da_etapa(e) is not None
    assert hash_generico_da_linha({**linha, "commit_guard": None}) == hash_generico_da_etapa(e)
    assert hash_generico_da_linha({**linha, "side_effect": 1}) is None
    assert hash_generico_da_linha({**linha, "postcondition": "não é json"}) is None
    assert hash_generico_da_linha({"template_key": None, "key": "k"}) is None    # coluna ausente: específica


# ================================================================== a consulta em duas chaves e o save
PKG = "com.pocqa.messenger"
V = "1.0(1)"
ESP, GEN = "h-especifica", "h-generica"
ACOES = [_toque({"kind": "rid", "rid": "app:id/x"})]


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "chave_generica.sqlite3")
    yield d
    d.close()


@pytest.fixture(autouse=True)
def _metricas_limpas() -> None:
    metricas.limpar()


def _receita(db: Database, step_hash: str, status: str, *, versao: str = V) -> int:
    return int(db.inserted_id(
        "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version, status,"
        " actions, learned_from_step, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (PKG, versao, "", "", step_hash, "abrir", 1, status, dumps(ACOES), "run-x:a1:v1:abrir",
         "2026-10-03T08:00:00Z")) or 0)


def _acha(store: RecipeStore, **kw: Any) -> Any:
    return store.find(PKG, V, ESP, step_hash_generico=GEN, **kw)


@pytest.mark.parametrize(("vivas", "esperada"), [
    ({ESP: "active", GEN: "active"}, (ESP, "active")),           # a específica vence a genérica
    ({ESP: "candidate", GEN: "active"}, (GEN, "active")),        # mas a ativa genérica vence a candidata específica
    ({ESP: "candidate", GEN: "candidate"}, (ESP, "candidate")),
    ({GEN: "candidate"}, (GEN, "candidate")),
])
def test_ordem_da_consulta(db: Database, vivas: dict[str, str], esperada: tuple[str, str]) -> None:
    for h, status in vivas.items():
        _receita(db, h, status)
    row = _acha(RecipeStore(db))
    assert (row["step_hash"], row["status"]) == esperada


def test_uma_consulta_uma_contagem_e_o_rotulo_so_na_generica(db: Database) -> None:
    store = RecipeStore(db)
    assert _acha(store) is None
    _receita(db, GEN, "active")
    assert _acha(store)["step_hash"] == GEN
    _receita(db, ESP, "active")
    assert _acha(store)["step_hash"] == ESP
    assert metricas.total("receita.consulta") == 3
    assert metricas.valor("receita.consulta", resultado="ausente") == 1
    assert metricas.valor("receita.consulta", resultado="encontrada", chave="generica") == 1
    assert metricas.valor("receita.consulta", resultado="encontrada") == 1        # a específica: a série de antes
    assert metricas.total("receita.ausente") == 1                                 # a causa, uma vez por consulta


def test_sem_generica_ou_igual_a_especifica_e_a_consulta_de_antes(db: Database) -> None:
    _receita(db, ESP, "active")
    store = RecipeStore(db)
    assert store.find(PKG, V, ESP)["step_hash"] == ESP
    assert store.find(PKG, V, ESP, step_hash_generico=ESP)["step_hash"] == ESP
    assert metricas.valor("receita.consulta", resultado="encontrada") == 2


def test_quarentena_em_qualquer_das_chaves_e_quarentena(db: Database) -> None:
    _receita(db, GEN, "quarantined")
    assert _acha(RecipeStore(db, herdar=lambda: True)) is None
    assert metricas.valor("receita.consulta", resultado="quarentena") == 1


def test_a_generica_herda_de_outra_versao_pela_chave_generica(db: Database) -> None:
    doadora = _receita(db, GEN, "active", versao="0.9(1)")
    store = RecipeStore(db, herdar=lambda: True)
    row = _acha(store)
    assert row is not None and (row["step_hash"], row["status"]) == (GEN, "candidate")
    assert row["app_version"] == V and doadora != row["id"]
    assert metricas.valor("receita.consulta", resultado="herdada", chave="generica") == 1
    assert metricas.total("receita.ausente") == 1                                 # medida pela específica, uma vez


def test_candidata_especifica_que_divergiu_sai_quando_o_caminho_vai_para_a_generica(db: Database) -> None:
    velha = _receita(db, ESP, "candidate")
    store = RecipeStore(db)
    novo = store.save(package=PKG, app_version=V, step_hash=GEN, step_key="abrir", actions=ACOES,
                      learned_from="run-y:a1:v1:abrir", candidate=True, replaces=velha)
    assert novo
    status = {r["id"]: (r["step_hash"], r["status"]) for r in db.query("SELECT id, step_hash, status FROM recipes")}
    assert status == {velha: (ESP, "superseded"), novo: (GEN, "candidate")}


def test_a_especifica_sai_mesmo_quando_a_generica_ja_tem_a_sua_em_prova(db: Database) -> None:
    velha = _receita(db, ESP, "candidate")
    em_prova = _receita(db, GEN, "candidate")
    assert RecipeStore(db).save(package=PKG, app_version=V, step_hash=GEN, step_key="abrir", actions=ACOES,
                                learned_from="run-y:a1:v1:abrir", candidate=True, replaces=velha) is None
    status = {r["id"]: r["status"] for r in db.query("SELECT id, status FROM recipes")}
    assert status == {velha: "superseded", em_prova: "candidate"}                 # a genérica segue em prova


# ================================================================== de ponta a ponta, no Instagram falso
class RedacaoVariavel(AtorDoInstagram):
    """O planejador reescreve a pós-condição julgada pelo modelo a cada plano — o que foi medido no central."""

    redacao = "{alvo} na tela"

    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]:
        plano, uso = await super().plan(req)
        alvo = plano.parameters.get("username", "")
        plano.steps = [s.model_copy(update={"postcondition": s.postcondition.model_copy(
            update={"value": self.redacao.format(alvo=alvo)})}) if s.postcondition.kind == "model_judged" else s
            for s in plano.steps]
        return plano, uso


@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> AsyncIterator[Harness]:
    h = Harness(tmp_path, 1, factory=lambda rt: FakeInstagram(account="eu.teste", screen="feed"))
    h.ai = CountingProvider(RedacaoVariavel())
    h.cfg.file.ai.recipes = "replay"
    h.cfg.file.ai.flows = False
    await h.boot()
    assert h.state is not None
    h.state.db.execute("UPDATE instances SET app_id='instagram' WHERE id='android-01'")
    h.state.devices.get("android-01").app_id = "instagram"
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


async def _executa(h: Harness, comando: str) -> Any:
    fake = h.fakes["android-01"]
    assert isinstance(fake, FakeInstagram)
    fake.screen, fake.thread_with, fake.search_query = "feed", None, ""
    run = h.run(["android-01"], command=comando)
    detalhe = await h.wait_run(run.id, timeout=60)
    assert detalhe.status == "completed", (detalhe.status, detalhe.status_detail)
    assert h.state is not None
    return h.state.repo.run_row(run.id)


async def test_outra_redacao_e_outro_valor_reproduzem_pela_chave_generica(parque: Harness) -> None:
    assert parque.state is not None
    db = parque.state.db
    primeira = await _executa(parque, "abra a conversa com @ana no instagram")
    etapas = db.query("SELECT * FROM steps WHERE run_id=? ORDER BY seq", (primeira["id"],))
    plano = Plan.model_validate_json(primeira["plan"])
    # paridade: a chave genérica pela linha de `steps` (o executor) e pela etapa do plano (consumidores)
    assert [hash_generico_da_linha(e) for e in etapas] == [hash_generico_da_etapa(p) for p in plano.steps]
    genericas = {hash_generico_da_linha(e) for e in etapas} - {None}
    assert genericas and {r["step_hash"] for r in db.query("SELECT step_hash FROM recipes WHERE status='active'")} \
        == genericas

    RedacaoVariavel.redacao = "a conversa de {alvo} está aberta e a busca sumiu"
    try:
        antes = len(parque.ai.calls)
        segunda = await _executa(parque, "abra a conversa com @bia no instagram")
    finally:
        RedacaoVariavel.redacao = "{alvo} na tela"
    novas = db.query("SELECT template_hash, driven_by FROM steps WHERE run_id=?", (segunda["id"],))
    assert not {e["template_hash"] for e in novas} & {e["template_hash"] for e in etapas}   # outra chave específica
    assert {e["driven_by"] for e in novas} == {"recipe"}
    assert sum(1 for c in parque.ai.calls[antes:] if c["role"] == "decide") == 0
    assert metricas.valor("receita.consulta", resultado="encontrada", chave="generica") == len(novas)
    trilha = [str(r["message"]) for r in db.query(
        "SELECT message FROM events WHERE kind='decision' AND run_id=?", (segunda["id"],))]
    assert any("pela chave genérica" in t for t in trilha), trilha


# ================================================================== o passe que semeia a chave genérica
async def test_semear_e_nao_destrutivo_idempotente_e_deixa_de_fora_a_especifica(parque: Harness) -> None:
    assert parque.state is not None
    db = parque.state.db
    await _executa(parque, "abra a conversa com @ana no instagram")
    # o banco de antes do RA-20 B: as receitas na chave específica da etapa de origem
    db.execute("UPDATE recipes SET step_hash=(SELECT template_hash FROM steps WHERE steps.id=recipes.learned_from_step)")
    receitas = {r["step_key"]: dict(r) for r in db.query("SELECT * FROM recipes")}
    assert set(receitas) == {"abrir_inbox", "abrir_conversa"}
    # a da conversa vira específica: o toque grava o literal do valor ("ana", de "@ana")
    db.execute("UPDATE recipes SET actions=? WHERE step_key='abrir_conversa'",
               (dumps([_toque({"kind": "text", "text": "ana"})]),))
    antes = {r["id"]: (r["step_hash"], r["status"], r["version"], r["actions"]) for r in db.query("SELECT * FROM recipes")}

    ensaio = semear(db, parque.state.scheduler.executor.recipes, gravar=False)
    assert ensaio.contagem["especificas"] == 1 and ensaio.contagem["grupos"] == 1
    assert db.scalar("SELECT COUNT(*) FROM recipes") == len(antes)                # o ensaio não grava

    rel = semear(db, parque.state.scheduler.executor.recipes, gravar=True)
    [g] = rel.grupos
    assert g.escolhida == receitas["abrir_inbox"]["id"] and g.semeada
    nova = db.one("SELECT * FROM recipes WHERE id=?", (g.semeada,))
    assert (nova["status"], nova["step_hash"], nova["step_key"]) == ("candidate", g.chave[4], "abrir_inbox")
    assert nova["actions"] == receitas["abrir_inbox"]["actions"]
    # as de antes não mudam: nem chave, nem status, nem versão, nem ações
    assert {r["id"]: (r["step_hash"], r["status"], r["version"], r["actions"])
            for r in db.query("SELECT * FROM recipes WHERE id<>?", (g.semeada,))} == antes
    # a trilha do livro registra a semeada, pelo sistema, com o motivo
    assert db.scalar("SELECT COUNT(*) FROM learning_transitions WHERE item_ref=? AND reason LIKE '%semeada da receita%'",
                     (f"receita:{g.semeada}",)) == 1

    de_novo = semear(db, parque.state.scheduler.executor.recipes, gravar=True)
    assert de_novo.contagem["semeadas"] == 0 and de_novo.contagem["grupos_com_viva"] == 1


async def test_semear_deixa_open_app_de_fora(parque: Harness) -> None:
    assert parque.state is not None
    db = parque.state.db
    await _executa(parque, "abra a conversa com @ana no instagram")
    db.execute("UPDATE recipes SET step_hash=(SELECT template_hash FROM steps WHERE steps.id=recipes.learned_from_step)")
    db.execute("UPDATE recipes SET step_key='open_app' WHERE step_key='abrir_inbox'")
    rel = semear(db, parque.state.scheduler.executor.recipes, gravar=False)
    assert rel.contagem["fora"] == 1 and [g.receitas[0]["step_key"] for g in rel.grupos] == ["abrir_conversa"]
