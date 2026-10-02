"""As relações derivadas do detalhe do Livro (30.7, `docs/design/aprendizado-vivo.md` §6): substitui, substituída por,
derivado de, absorvida e contradiz, no campo `relacoes` do detalhe, SEM tabela de arestas.

- domínio puro (`domain/relacoes.py`): cada relação pela regra, a `fonte`, e o que NÃO se deriva (lição sem tema, item
  morto, mesmo conteúdo);
- o mundo semeado (SQL): receita em três versões, habilidade com pai e fluxo legado, item com `parent_id`, tela absorvida
  e telas que se contradizem, receita e fluxo com a mesma chave e conteúdo diferente;
- rota `GET /api/aprendizado/{kind}/{ref}`: `relacoes` sempre presente, também na memória.

Nível de prova: `simulated` (banco de teste, sem aparelho nem IA).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.db import Database
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain import relacoes as dominio
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.livro import EntradaDoLivro, Escopo, NovoItem
from app.modules.learning.domain.relacoes import Parente, Sucessora, TipoDeRelacao
from app.modules.learning.domain.vocabulario import LivroKind, Origem, SourceKind, absorvida
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.montagem import GuardaDoFluxo
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository

from .fake_skills import TS, ValidadorFalso, perfil
from .fake_skills import banco as banco_migrado

PACOTE = "com.instagram.android"
AGORA = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
CAMPOS = {"tipo", "kind", "ref", "rotulo", "fonte"}


# ------------------------------------------------------------------ domínio puro
def _entrada(ref: str, *, kind: LivroKind = LivroKind.TELA, state: SkillState | None = SkillState.CANDIDATE,
             scope: str = "tela|app||||", hash_: str | None = "h1", detail: str | None = None,
             title: str = "t") -> EntradaDoLivro:
    return EntradaDoLivro(kind=kind, ref=ref, state=state, native_status=None, title=title, app="app",
                          origin=Origem.SISTEMA, detail=detail, content_hash=hash_, scope_key=scope)


def test_receita_vira_substitui_e_substituida_por_com_fonte() -> None:
    conteudo = {"substitui": {"id": 4, "versao": 1, "estado": "superseded"},
                "substituida_por": {"id": 9, "versao": 3, "estado": "candidate"}}
    r = dominio.da_receita(conteudo)
    assert [(x["tipo"], x["kind"], x["ref"], x["rotulo"]) for x in r] == [
        ("substitui", "receita", "4", "v1 (superseded)"), ("substituida_por", "receita", "9", "v3 (candidate)")]
    assert all(set(x) == CAMPOS and x["fonte"] for x in r)
    assert dominio.da_receita({"substitui": None, "substituida_por": None}) == []
    assert dominio.da_receita(None) == []


def test_habilidade_pai_sucessoras_e_fluxo_legado() -> None:
    r = dominio.da_habilidade({"skill_id": "ig.x", "versao": 2, "parent_version": 1},
                              [Sucessora("ig.x@3", 3, "draft")])
    assert [(x["tipo"], x["ref"]) for x in r] == [("substitui", "ig.x@1"), ("substituida_por", "ig.x@3")]
    assert dominio.da_habilidade({"skill_id": "ig.x", "versao": 1, "parent_version": None}, []) == []
    legado = dominio.da_habilidade({"skill_id": "flow:abre-o-feed", "versao": 1, "parent_version": None}, [])
    assert [(x["tipo"], x["kind"], x["ref"]) for x in legado] == [("derivado_de", "fluxo", "abre-o-feed")]


def test_item_com_pai_e_filhos_e_pai_que_sumiu() -> None:
    pai, filho = _entrada("li-pai", title="pai"), _entrada("li-filho", title="filho")
    r = dominio.do_item(parent_id="li-pai", pai=pai, filhos=[filho])
    assert [(x["tipo"], x["ref"], x["fonte"]) for x in r] == [
        ("substitui", "li-pai", "learning_items.parent_id"), ("substituida_por", "li-filho", "learning_items.parent_id")]
    assert dominio.do_item(parent_id="li-sumiu", pai=None, filhos=[]) == []      # alvo inexistente: sem relação


def test_absorvida_liga_a_regra_pelo_nome_ou_so_o_commit() -> None:
    e = _entrada("li-1", detail=absorvida("abc123"))
    [pelo_nome] = dominio.absorvida(e, "aprendida:feed")
    assert (pelo_nome["tipo"], pelo_nome["kind"], pelo_nome["ref"]) == ("absorvida", "regra_declarada", "aprendida:feed")
    assert "abc123" in str(pelo_nome["rotulo"])
    [so_commit] = dominio.absorvida(e, None)
    assert (so_commit["kind"], so_commit["ref"]) == ("commit", "abc123")
    assert dominio.absorvida(_entrada("li-2", detail="em_prova"), "x") == []
    assert dominio.absorvida(_entrada("li-3", detail=None), "x") == []


def test_contradicao_pede_mesmo_escopo_conteudo_diferente_e_os_dois_vivos() -> None:
    e = _entrada("a")
    outras = [
        Parente(_entrada("b", hash_="h2"), "feed"),                              # contradiz
        Parente(_entrada("c", hash_="h1"), "feed"),                              # mesmo conteúdo: duplicata, não contradição
        Parente(_entrada("d", hash_="h3", state=SkillState.DISABLED), "feed"),   # morta
        Parente(_entrada("e", hash_="h4", scope="tela|outro||||"), "feed"),      # outro escopo
        Parente(_entrada("f", hash_="h5"), "perfil"),                            # outro tema
        Parente(_entrada("a", hash_="h6"), "feed"),                              # ela mesma
    ]
    r = dominio.contradiz(e, "feed", outras)
    assert [(x["tipo"], x["ref"]) for x in r] == [("contradiz", "b")] and set(r[0]) == CAMPOS
    assert dominio.contradiz(e, None, outras) == []                              # sem critério de tema: nada derivado
    assert dominio.contradiz(_entrada("a", state=SkillState.DEPRECATED), "feed", outras) == []
    assert dominio.contradiz(_entrada("a", hash_=None), "feed", outras) == []


def test_ordem_e_estavel_por_tipo_e_alvo() -> None:
    r = [dominio.relacao(TipoDeRelacao.CONTRADIZ, "tela", "z", fonte="f"),
         dominio.relacao(TipoDeRelacao.SUBSTITUIDA_POR, "tela", "b", fonte="f"),
         dominio.relacao(TipoDeRelacao.SUBSTITUI, "tela", "a", fonte="f"),
         dominio.relacao(TipoDeRelacao.SUBSTITUIDA_POR, "tela", "a", fonte="f")]
    assert [(x["tipo"], x["ref"]) for x in dominio.ordenar(r)] == [
        ("substitui", "a"), ("substituida_por", "a"), ("substituida_por", "b"), ("contradiz", "z")]


# ------------------------------------------------------------------ o mundo semeado (SQL)
def _receita(db: Database, *, versao: int = 1, status: str = "active", acoes: str = "tap", step_hash: str = "h-abrir",
             app_version: str = "447") -> int:
    return int(db.inserted_id(
        "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version, status,"
        " actions, replay_ok, replay_fail, consecutive_fail, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (PACOTE, app_version, "sig", "pt/420", step_hash, "abrir", versao, status,
         json.dumps([{"tool": acoes, "args": {}, "selectors": [], "commit": False}]), 3, 0, 0, TS)))


def _tela(mundo: "Mundo", nome: str, ids: list[str], *, estado: SkillState = SkillState.CANDIDATE,
          detalhe: str | None = None, pai: str | None = None) -> str:
    return mundo.repo.criar_item(
        NovoItem(kind=LivroKind.TELA, escopo=Escopo(app=PACOTE),
                 content={"tela": nome, "tipo": "regra_de_tela", "ids_todos": ids, "casa": True}, summary=nome,
                 source_kind=SourceKind.SCREEN_OBSERVATION, side_effect=False, app_version="447", parent_id=pai),
        by="sistema", estado=estado, detalhe=detalhe, reason="teste").id


def _licao(mundo: "Mundo", nota: str) -> str:
    return mundo.repo.criar_item(
        NovoItem(kind=LivroKind.LICAO, escopo=Escopo(app=PACOTE, capability="SEND_MESSAGE", role="actor"),
                 content={"modelo": "nota", "acao": "SEND_MESSAGE", "nota": nota}, summary=nota,
                 source_kind=SourceKind.FEEDBACK_NOTE, side_effect=False, app_version="447"),
        by="sistema", estado=SkillState.CANDIDATE, detalhe=None, reason="teste").id


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',?,0)", (PACOTE,))
        habilidades = SqlSkillRepository(db, ValidadorFalso())
        self.repo = SqlLearningRepository(db, guarda_do_fluxo=GuardaDoFluxo(db, habilidades), precos=dict)
        self.servico = LearningService(self.repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes,
                                       relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14)

    def relacoes(self, kind: LivroKind, ref: str | int) -> list[tuple[str, str, str]]:
        r = self.servico.detalhe(kind, str(ref)).relacoes
        assert all(set(x) == CAMPOS and x["fonte"] for x in r)
        return [(str(x["tipo"]), str(x["kind"]), str(x["ref"])) for x in r]


@pytest.fixture
def mundo(tmp_path: Path) -> Mundo:
    db = banco_migrado(tmp_path, "relacoes.sqlite3")
    yield Mundo(db)
    db.close()


@pytest.fixture
async def cliente(mundo: Mundo) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.servico)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def test_receita_em_tres_versoes_aponta_para_as_vizinhas(mundo: Mundo) -> None:
    v1 = _receita(mundo.db, versao=1, status="superseded")
    v2 = _receita(mundo.db, versao=2, status="superseded")
    v3 = _receita(mundo.db, versao=3, status="active")
    outra_chave = _receita(mundo.db, versao=2, step_hash="h-outra")
    assert mundo.relacoes(LivroKind.RECEITA, v1) == [("substituida_por", "receita", str(v2))]
    assert mundo.relacoes(LivroKind.RECEITA, v2) == [("substitui", "receita", str(v1)),
                                                      ("substituida_por", "receita", str(v3))]
    assert mundo.relacoes(LivroKind.RECEITA, v3) == [("substitui", "receita", str(v2))]
    assert mundo.relacoes(LivroKind.RECEITA, outra_chave) == []


def test_receita_viva_com_a_mesma_chave_e_caminho_diferente_se_contradiz(mundo: Mundo) -> None:
    ativa = _receita(mundo.db, versao=1, status="active", acoes="tap")
    candidata = _receita(mundo.db, versao=2, status="candidate", acoes="swipe")
    morta = _receita(mundo.db, versao=3, status="quarantined", acoes="back")
    igual = _receita(mundo.db, versao=4, status="candidate", acoes="tap")            # mesmo caminho: não é contradição
    por_tipo = {(t, r) for t, _, r in mundo.relacoes(LivroKind.RECEITA, ativa)}
    assert ("contradiz", str(candidata)) in por_tipo
    assert ("contradiz", str(morta)) not in por_tipo and ("contradiz", str(igual)) not in por_tipo
    assert ("contradiz", str(ativa)) in {(t, r) for t, _, r in mundo.relacoes(LivroKind.RECEITA, candidata)}
    # a morta não disputa: sem contradição, só a vizinhança de versão
    assert not any(t == "contradiz" for t, _, _ in mundo.relacoes(LivroKind.RECEITA, morta))


def test_habilidade_com_pai_filha_e_fluxo_legado(mundo: Mundo) -> None:
    mundo.db.execute("INSERT INTO skill_definitions(id, name, app_id, created_at, updated_at) VALUES (?,?,?,?,?)",
                     ("ig.x", "Habilidade x", "instagram", TS, TS))
    for versao, pai, estado in ((1, None, "published"), (2, 1, "draft")):
        mundo.db.execute(
            "INSERT INTO skill_versions(id, skill_id, version, state, content, content_hash, command_template,"
            " source_kind, parent_version, created_at, state_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"ig.x@{versao}", "ig.x", versao, estado, "{}", f"hh{versao}", f"faça {versao}", "teaching", pai, TS, TS))
    assert mundo.relacoes(LivroKind.HABILIDADE, "ig.x@1") == [("substituida_por", "habilidade", "ig.x@2")]
    assert mundo.relacoes(LivroKind.HABILIDADE, "ig.x@2") == [("substitui", "habilidade", "ig.x@1")]
    mundo.db.execute("INSERT INTO skill_definitions(id, name, app_id, created_at, updated_at) VALUES (?,?,?,?,?)",
                     ("flow:f1", "Fluxo legado", "instagram", TS, TS))
    mundo.db.execute(
        "INSERT INTO skill_versions(id, skill_id, version, state, schema_version, content, content_hash,"
        " command_template, source_kind, created_at, state_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("flow:f1@1", "flow:f1", 1, "published", 0, "{}", "hf", "mande tudo", "legacy_flow", TS, TS))
    assert mundo.relacoes(LivroKind.HABILIDADE, "flow:f1@1") == [("derivado_de", "fluxo", "f1")]


def test_fluxo_e_habilidade_nao_derivam_contradicao(mundo: Mundo) -> None:
    """`flows.match_key` é único (dois fluxos vivos na mesma chave não existem) e as versões de uma habilidade dividem
    o comando (seria acusar a edição de contradizer o original): sem critério seguro, nada sai."""
    for ident, passo in (("a1", "abrir"), ("a2", "enviar")):
        mundo.db.execute(
            "INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at, source)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (ident, ident, f"mande-{ident}", "mande {contato}", json.dumps({"steps": [{"key": passo}]}),
             "instagram", "active", TS, "run"))
    assert mundo.relacoes(LivroKind.FLUXO, "a1") == [] and mundo.relacoes(LivroKind.FLUXO, "a2") == []
    mundo.db.execute("INSERT INTO skill_definitions(id, name, app_id, created_at, updated_at) VALUES (?,?,?,?,?)",
                     ("ig.y", "Habilidade y", "instagram", TS, TS))
    for versao, estado in ((1, "published"), (2, "validated")):
        mundo.db.execute(
            "INSERT INTO skill_versions(id, skill_id, version, state, content, content_hash, command_template,"
            " source_kind, created_at, state_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"ig.y@{versao}", "ig.y", versao, estado, "{}", f"hy{versao}", "faça y", "teaching", TS, TS))
    assert mundo.relacoes(LivroKind.HABILIDADE, "ig.y@1") == []
    assert mundo.relacoes(LivroKind.HABILIDADE, "ig.y@2") == []


def test_item_editado_tem_pai_e_o_pai_tem_filho(mundo: Mundo) -> None:
    pai = _tela(mundo, "aprendida:feed", ["a"], estado=SkillState.DEPRECATED)
    filho = _tela(mundo, "aprendida:feed", ["a", "b"], pai=pai)
    assert mundo.relacoes(LivroKind.TELA, filho) == [("substitui", "tela", pai)]
    assert mundo.relacoes(LivroKind.TELA, pai) == [("substituida_por", "tela", filho)]


def test_tela_absorvida_liga_a_regra_pelo_nome(mundo: Mundo) -> None:
    t = _tela(mundo, "aprendida:feed", ["a"], estado=SkillState.DEPRECATED, detalhe=absorvida("c0ffee1"))
    assert mundo.relacoes(LivroKind.TELA, t) == [("absorvida", "regra_declarada", "aprendida:feed")]
    assert mundo.servico.detalhe(LivroKind.TELA, t).relacoes[0]["rotulo"] == (
        "regra 'aprendida:feed' declarada no commit c0ffee1")


def test_telas_do_mesmo_app_so_se_contradizem_com_o_mesmo_nome(mundo: Mundo) -> None:
    feed_a = _tela(mundo, "aprendida:feed", ["a"])
    feed_b = _tela(mundo, "aprendida:feed", ["b"])
    perfil_ = _tela(mundo, "aprendida:perfil", ["p"])                              # mesmo app, outra tela: não contradiz
    feed_morta = _tela(mundo, "aprendida:feed", ["c"], estado=SkillState.DISABLED)
    assert mundo.relacoes(LivroKind.TELA, feed_a) == [("contradiz", "tela", feed_b)]
    assert mundo.relacoes(LivroKind.TELA, feed_b) == [("contradiz", "tela", feed_a)]
    assert mundo.relacoes(LivroKind.TELA, perfil_) == []
    assert mundo.relacoes(LivroKind.TELA, feed_morta) == []


def test_licoes_do_mesmo_passo_nao_se_acusam_sem_criterio_de_tema(mundo: Mundo) -> None:
    a, b = _licao(mundo, "confira o botão"), _licao(mundo, "confira o campo")
    assert mundo.relacoes(LivroKind.LICAO, a) == [] and mundo.relacoes(LivroKind.LICAO, b) == []


async def test_rota_sempre_devolve_relacoes_inclusive_na_memoria(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    v1 = _receita(mundo.db, versao=1, status="superseded")
    v2 = _receita(mundo.db, versao=2)
    r = await cliente.get(f"/api/aprendizado/receita/{v1}")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["relacoes"] == [{"tipo": "substituida_por", "kind": "receita", "ref": str(v2), "rotulo": "v2 (active)",
                                  "fonte": "recipes: mesma chave, versão vizinha"}]
    assert corpo["conteudo"]["tipo"] == "receita" and "versao" in corpo         # os campos anteriores seguem ao lado
    licao = await cliente.get(f"/api/aprendizado/licao/{_licao(mundo, 'x')}")
    assert licao.json()["relacoes"] == []
    perfil(mundo.db, "p1")
    mundo.db.execute("INSERT INTO memory_items(id, profile_id, subject, content, source, fingerprint, created_at,"
                     " updated_at) VALUES (?,?,?,?,?,?,?,?)", ("m0", "p1", "@ana", "x", "operator", "fp", TS, TS))
    m = await cliente.get("/api/aprendizado/memoria/p1")
    assert m.status_code == 200 and m.json()["relacoes"] == []
