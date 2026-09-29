"""O livro de aprendizado (ADR-054, decisão 3): o mapeamento de estado das fontes existentes, a união na leitura, a
memória só como contagem, as rotas HTTP e o digest encadeado no fim da execução.

- mapeamento: receita (active/candidate/validated/quarantined/superseded) e fluxo (active/candidate/validated/
  disabled) → estados do livro, e de volta; status desconhecido nunca vira publicado;
- rotas: `GET /api/aprendizado` (união, filtros, contagem), `GET /api/aprendizado/{kind}/{ref}`,
  `POST …/status` (CAS no status nativo com trilha; 409 em habilidade com o endereço; guardas da rota antiga),
  `GET /api/aprendizado/pendentes` e `GET /api/aprendizado/revisar`;
- o digest do aprendizado corre quando a execução assenta (`scheduler.on_run_settled`) e um minerador que lança não
  derruba a execução nem os outros.

Nível de prova: `simulated` (banco de teste; o digest no Harness da porta 5640, aparelho falso, IA simulada).
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
from app.modules.learning.domain.ciclo import (ESTADOS, Actor, ConflitoDeEstado, EntradaInvalida, NaoEncontrado,
                                               SkillState, TransicaoProibida, caminho_da_pessoa, permitido)
from app.modules.learning.domain.livro import (ESTADO_DA_RECEITA, ESTADO_DO_FLUXO, EntradaDoLivro, estado_nativo,
                                               receita_tem_efeito, status_nativo)
from app.modules.learning.domain.vocabulario import LivroKind, Origem
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.montagem import GuardaDoFluxo
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository

from .conftest import Harness
from .fake_skills import TS, ValidadorFalso, perfil
from .fake_skills import banco as banco_migrado

S = SkillState
PACOTE = "com.instagram.android"
AGORA = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
LEMBRANCA = "mora-na-rua-das-flores-123"          # conteúdo da memória: NUNCA pode aparecer no livro


# ------------------------------------------------------------------ mapeamento (domínio)
def test_mapeamento_de_estado_das_fontes_nativas() -> None:
    assert dict(ESTADO_DA_RECEITA) == {"active": S.PUBLISHED, "candidate": S.CANDIDATE, "validated": S.VALIDATED,
                                       "quarantined": S.DISABLED, "superseded": S.DEPRECATED}
    assert dict(ESTADO_DO_FLUXO) == {"active": S.PUBLISHED, "candidate": S.CANDIDATE, "validated": S.VALIDATED,
                                     "disabled": S.DISABLED}
    for status, estado in ESTADO_DA_RECEITA.items():
        assert status_nativo(LivroKind.RECEITA, estado) == status
    for status, estado in ESTADO_DO_FLUXO.items():
        assert status_nativo(LivroKind.FLUXO, estado) == status
    assert status_nativo(LivroKind.FLUXO, S.DEPRECATED) is None           # fluxo sai de circulação como disabled
    assert estado_nativo(LivroKind.RECEITA, "estranho") is None            # desconhecido nunca vira publicado
    assert estado_nativo(LivroKind.HABILIDADE, "draft") is S.DRAFT and estado_nativo(LivroKind.MEMORIA, "x") is None
    assert receita_tem_efeito([{"tool": "tap", "commit": True}]) and not receita_tem_efeito([{"tool": "tap"}])
    habilidade = EntradaDoLivro(kind=LivroKind.HABILIDADE, ref="ig.x@1", state=S.VALIDATED, native_status="validated",
                                title="x", app=None, origin=Origem.ENSINO)
    assert habilidade.requires_owner and habilidade.trail_ref == "habilidade:ig.x@1"


# ------------------------------------------------------------------ o mundo semeado
def _receita(db: Database, *, status: str, commit: bool, passo: str, versao: int = 1) -> int:
    acoes = [{"tool": "tap", "args": {}, "selectors": [{"rid": "botao"}], "commit": commit, "why": "a IA explicou"}]
    return int(db.inserted_id(
        "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version, status,"
        " actions, learned_from_step, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (PACOTE, "447", "sig", "pt/420", f"h-{passo}", passo, versao, status, json.dumps(acoes), f"r1:a:v1:{passo}",
         TS)))


def _fluxo(db: Database, fid: str, *, status: str, efeito: bool, fonte: str | None = "run") -> None:
    plano = {"summary": fid, "app_id": "instagram", "parameters": {},
             "steps": [{"key": "a", "title": "a", "goal": "a", "side_effect": efeito,
                        "postcondition": {"kind": "app_foreground", "value": "x", "description": "x"}}]}
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at, source)"
               " VALUES (?,?,?,?,?,?,?,?,?)", (fid, fid, f"cmd {fid}", f"cmd {fid}", json.dumps(plano), "instagram",
                                               status, TS, fonte))


def _habilidade(db: Database, sid: str, estado: str, *, fluxo: str | None = None, chave: str | None = None) -> None:
    if db.one("SELECT id FROM skill_definitions WHERE id=?", (sid,)) is None:
        db.execute("INSERT INTO skill_definitions(id, name, app_id, legacy_flow_id, created_at, updated_at)"
                   " VALUES (?,?,?,?,?,?)", (sid, f"Habilidade {sid}", "instagram", fluxo, TS, TS))
    db.execute("INSERT INTO skill_versions(id, skill_id, version, state, content, content_hash, match_key,"
               " source_kind, created_at, state_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (f"{sid}@1", sid, 1, estado, "{}", "h", chave, "teaching", TS, TS))


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',?,0)", (PACOTE,))
        self.legado_com_commit = _receita(db, status="active", commit=True, passo="curtir")
        self.ativa = _receita(db, status="active", commit=False, passo="abrir")
        self.em_quarentena = _receita(db, status="quarantined", commit=False, passo="abrir", versao=2)
        self.substituida = _receita(db, status="superseded", commit=False, passo="rolar")
        self.candidata = _receita(db, status="candidate", commit=False, passo="voltar")
        self.validada_com_commit = _receita(db, status="validated", commit=True, passo="comentar")
        _fluxo(db, "fluxo-com-efeito", status="active", efeito=True)
        _fluxo(db, "fluxo-desligado", status="disabled", efeito=False, fonte="training:trn-1")
        _fluxo(db, "fluxo-validado", status="validated", efeito=True)
        _fluxo(db, "fluxo-adotado", status="disabled", efeito=False)
        _habilidade(db, "ig.adotou", "published", fluxo="fluxo-adotado", chave="cmd fluxo-adotado")
        _habilidade(db, "ig.validada", "validated")
        perfil(db, "p1")
        for i in range(2):
            db.execute("INSERT INTO memory_items(id, profile_id, subject, content, source, fingerprint, created_at,"
                       " updated_at) VALUES (?,?,?,?,?,?,?,?)",
                       (f"m{i}", "p1", "@ana", f"{LEMBRANCA} {i}", "operator", f"fp{i}", TS, TS))
        habilidades = SqlSkillRepository(db, ValidadorFalso())
        repo = SqlLearningRepository(db, guarda_do_fluxo=GuardaDoFluxo(db, habilidades), precos=dict)
        self.servico = LearningService(repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes,
                                       relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14)


@pytest.fixture
def mundo(tmp_path: Path) -> Mundo:
    db = banco_migrado(tmp_path, "livro.sqlite3")
    yield Mundo(db)
    db.close()


@pytest.fixture
async def cliente(mundo: Mundo) -> AsyncIterator[httpx.AsyncClient]:
    """O roteador do aprendizado sobre um app mínimo (o `create_app` inteiro fica para o teste do Harness)."""
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.servico)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ------------------------------------------------------------------ leitura
async def test_o_livro_une_as_fontes_com_o_estado_mapeado(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    r = await cliente.get("/api/aprendizado")
    assert r.status_code == 200, r.text
    corpo = r.json()
    por_ref = {(i["kind"], i["ref"]): i for i in corpo["itens"]}
    assert por_ref[("receita", str(mundo.legado_com_commit))]["state"] == "published"
    assert por_ref[("receita", str(mundo.legado_com_commit))]["side_effect"] is True
    assert por_ref[("receita", str(mundo.em_quarentena))]["state"] == "disabled"
    assert por_ref[("receita", str(mundo.substituida))]["state"] == "deprecated"
    assert por_ref[("fluxo", "fluxo-desligado")]["origin"] == "treino"
    assert por_ref[("fluxo", "fluxo-com-efeito")]["app"] == PACOTE                  # o pacote, não o id do app
    assert por_ref[("habilidade", "ig.validada@1")]["requires_owner"] is True
    memoria = por_ref[("memoria", "p1")]
    assert memoria["count"] == 2 and memoria["state"] is None
    assert LEMBRANCA not in r.text                                                 # só a contagem
    assert corpo["contagem"]["receita"]["published"] == 2 and corpo["contagem"]["memoria"]["-"] == 2
    so_receitas = (await cliente.get("/api/aprendizado?kind=receita&state=published")).json()
    assert {i["ref"] for i in so_receitas["itens"]} == {str(mundo.legado_com_commit), str(mundo.ativa)}
    assert all(i["app"] == PACOTE for i in (await cliente.get(f"/api/aprendizado?app={PACOTE}")).json()["itens"])
    treino = (await cliente.get("/api/aprendizado?origem=treino")).json()["itens"]
    assert [i["ref"] for i in treino] == ["fluxo-desligado"]
    assert (await cliente.get("/api/aprendizado?kind=nada")).status_code == 422


async def test_item_com_evidencia_e_trilha_e_memoria_sem_conteudo(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    r = await cliente.get(f"/api/aprendizado/receita/{mundo.ativa}")
    assert r.status_code == 200 and r.json()["item"]["title"] == "abrir (v1)"
    assert r.json()["trilha"] == [] and r.json()["evidencias"] == [] and r.json()["exposicoes"] == []
    m = await cliente.get("/api/aprendizado/memoria/p1")
    assert m.status_code == 200 and m.json()["item"]["count"] == 2 and LEMBRANCA not in m.text
    assert (await cliente.get("/api/aprendizado/receita/999999")).json()["detail"]["code"] == "not_found"
    assert (await cliente.get("/api/aprendizado/licao/li-nao-existe")).status_code == 404


async def test_pendentes_e_revisar(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    pendentes = (await cliente.get("/api/aprendizado/pendentes")).json()
    assert {(i["kind"], i["ref"]) for i in pendentes["itens"]} == {
        ("receita", str(mundo.validada_com_commit)), ("fluxo", "fluxo-validado"), ("habilidade", "ig.validada@1")}
    assert pendentes["total"] == 3
    revisar = (await cliente.get("/api/aprendizado/revisar")).json()
    assert {(i["kind"], i["ref"]) for i in revisar["itens"]} == {("receita", str(mundo.legado_com_commit)),
                                                                ("fluxo", "fluxo-com-efeito")}
    # O dono decide pelo livro (aqui: desliga e religa o legado): ele sai de "Revisar", e a trilha diz quem foi.
    base = f"/api/aprendizado/receita/{mundo.legado_com_commit}/status"
    assert (await cliente.post(base, json={"to": "disabled", "reason": "revisão do legado"})).status_code == 200
    assert (await cliente.post(base, json={"to": "published", "reason": "revisado: pode agir"})).status_code == 200
    revisar = (await cliente.get("/api/aprendizado/revisar")).json()
    assert {i["ref"] for i in revisar["itens"]} == {"fluxo-com-efeito"}


# ------------------------------------------------------------------ status
async def test_status_de_receita_com_cas_trilha_e_guardas(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    db = mundo.db
    rota = f"/api/aprendizado/receita/{mundo.ativa}/status"
    r = await cliente.post(rota, json={"to": "disabled", "reason": "fez outra coisa"})
    assert r.status_code == 200, r.text
    assert r.json()["item"]["state"] == "disabled" and db.scalar(
        "SELECT status FROM recipes WHERE id=?", (mundo.ativa,)) == "quarantined"
    assert [(t["from"], t["to"], t["decided_by"]) for t in r.json()["trilha"]] == [("published", "disabled", "panel")]
    # Nunca duas ativas por chave: com a v1 de volta, a v2 (mesma etapa) não se reativa.
    assert (await cliente.post(rota, json={"to": "published", "reason": "reativar"})).status_code == 200
    duas = await cliente.post(f"/api/aprendizado/receita/{mundo.em_quarentena}/status",
                              json={"to": "published", "reason": "reativar a outra"})
    assert duas.status_code == 409 and duas.json()["detail"]["code"] == "state_conflict"
    # A substituída não volta; a transição fora da tabela é recusa; o motivo é obrigatório.
    volta = await cliente.post(f"/api/aprendizado/receita/{mundo.substituida}/status",
                               json={"to": "published", "reason": "x"})
    assert volta.status_code == 409 and volta.json()["detail"]["code"] == "transition_forbidden"
    salto = await cliente.post(f"/api/aprendizado/receita/{mundo.candidata}/status",
                               json={"to": "published", "reason": "x"})
    assert salto.status_code == 409
    assert (await cliente.post(rota, json={"to": "disabled", "reason": ""})).status_code == 422
    assert (await cliente.post(rota, json={"to": "disabled", "reason": "x", "extra": 1})).status_code == 422
    # A pessoa aprova a receita com commit que o D1 parou em `validated`.
    aprova = await cliente.post(f"/api/aprendizado/receita/{mundo.validada_com_commit}/status",
                                json={"to": "published", "reason": "aprovada pelo dono"})
    assert aprova.status_code == 200 and db.scalar("SELECT status FROM recipes WHERE id=?",
                                                   (mundo.validada_com_commit,)) == "active"


async def test_status_de_fluxo_respeita_a_adocao_e_habilidade_aponta_a_rota_dela(
        mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    religa = await cliente.post("/api/aprendizado/fluxo/fluxo-adotado/status",
                                json={"to": "published", "reason": "religar"})
    assert religa.status_code == 409 and "adotado" in religa.json()["detail"]["message"]
    assert mundo.db.scalar("SELECT status FROM flows WHERE id='fluxo-adotado'") == "disabled"
    liga = await cliente.post("/api/aprendizado/fluxo/fluxo-desligado/status",
                              json={"to": "published", "reason": "voltou a servir"})
    assert liga.status_code == 200 and mundo.db.scalar("SELECT status FROM flows WHERE id='fluxo-desligado'") == "active"
    sem_equivalente = await cliente.post("/api/aprendizado/fluxo/fluxo-desligado/status",
                                         json={"to": "deprecated", "reason": "aposentar"})
    assert sem_equivalente.status_code == 409
    habilidade = await cliente.post("/api/aprendizado/habilidade/ig.validada@1/status",
                                    json={"to": "published", "reason": "publicar"})
    assert habilidade.status_code == 409
    assert habilidade.json()["detail"] == {"code": "use_skills_route", "href": "/api/skills/ig.validada/versions/1/status",
                                           "message": habilidade.json()["detail"]["message"]}
    memoria = await cliente.post("/api/aprendizado/memoria/p1/status", json={"to": "disabled", "reason": "x"})
    assert memoria.status_code == 409
    assert (await cliente.post("/api/aprendizado/fluxo/nao-existe/status",
                               json={"to": "disabled", "reason": "x"})).status_code == 404


# ------------------------------------------------------------------ rotas legadas (PUT /api/flows|recipes)
def test_vocabulario_das_rotas_legadas_e_o_caminho_da_pessoa() -> None:
    """O status que `PUT /api/flows/{id}` e `PUT /api/recipes/{id}` aceitam, no estado do livro, e os passos com que a
    pessoa chega lá a partir de cada estado (a rota só diz o destino)."""
    rota = {LivroKind.FLUXO: {"active": S.PUBLISHED, "disabled": S.DISABLED},
            LivroKind.RECEITA: {"active": S.PUBLISHED, "quarantined": S.DISABLED}}
    for kind, valores in rota.items():
        for status, estado in valores.items():
            assert estado_nativo(kind, status) is estado and status_nativo(kind, estado) == status
    assert estado_nativo(LivroKind.FLUXO, "quarantined") is None and estado_nativo(LivroKind.RECEITA, "disabled") is None
    assert caminho_da_pessoa(S.PUBLISHED, S.PUBLISHED) == () and caminho_da_pessoa(S.DISABLED, S.DISABLED) == ()
    assert caminho_da_pessoa(S.CANDIDATE, S.PUBLISHED) == (S.VALIDATED, S.PUBLISHED)   # o interruptor do fluxo em prova
    assert caminho_da_pessoa(S.VALIDATED, S.PUBLISHED) == (S.PUBLISHED,)
    assert caminho_da_pessoa(S.DISABLED, S.PUBLISHED) == (S.PUBLISHED,)
    for de in (S.CANDIDATE, S.VALIDATED, S.PUBLISHED):
        assert caminho_da_pessoa(de, S.DISABLED) == (S.DISABLED,)
    with pytest.raises(TransicaoProibida):
        caminho_da_pessoa(S.DEPRECATED, S.DISABLED)                       # a substituída não vai para quarentena
    # Todo passo devolvido é permitido à pessoa pela tabela (a conferência de cada um continua no serviço).
    for de in ESTADOS:
        for para in (S.PUBLISHED, S.DISABLED):
            try:
                passos = caminho_da_pessoa(de, para)
            except TransicaoProibida:
                continue
            for a, b in zip((de, *passos), passos, strict=False):
                assert permitido(a, b, Actor.PERSON), (a, b)


def test_rotas_legadas_mudam_pelo_livro_com_trilha_e_guardas(mundo: Mundo) -> None:
    sv, db = mundo.servico, mundo.db
    F, R = LivroKind.FLUXO, LivroKind.RECEITA

    def trilha(kind: LivroKind, ref: str | int) -> list[tuple[str | None, str, str, str]]:
        return [(t.from_state.value if t.from_state else None, t.to_state.value, t.decided_by, t.reason)
                for t in sv.detalhe(kind, str(ref)).trilha]

    def muda(kind: LivroKind, ref: str | int, status: str) -> str | None:
        return sv.mudar_status_nativo(kind, str(ref), status, by="panel", reason=f"pelo painel: {status}").native_status

    # Fluxo em prova ligado pelo interruptor: a pessoa valida e publica no mesmo gesto, os dois passos na trilha.
    _fluxo(db, "fluxo-em-prova", status="candidate", efeito=False)
    assert muda(F, "fluxo-em-prova", "active") == "active"
    assert trilha(F, "fluxo-em-prova") == [("candidate", "validated", "panel", "pelo painel: active"),
                                           ("validated", "published", "panel", "pelo painel: active")]
    # Já estar lá não é transição: nada entra na trilha (o painel repete o gesto sem efeito).
    assert muda(F, "fluxo-em-prova", "active") == "active" and len(trilha(F, "fluxo-em-prova")) == 2
    assert muda(F, "fluxo-desligado", "disabled") == "disabled" and trilha(F, "fluxo-desligado") == []
    assert muda(F, "fluxo-com-efeito", "disabled") == "disabled"
    assert trilha(F, "fluxo-com-efeito") == [("published", "disabled", "panel", "pelo painel: disabled")]
    assert muda(F, "fluxo-validado", "active") == "active"                  # com efeito: a pessoa publica
    with pytest.raises(ConflitoDeEstado, match="adotado"):                  # a guarda do livro também vale aqui
        muda(F, "fluxo-adotado", "active")
    assert db.scalar("SELECT status FROM flows WHERE id='fluxo-adotado'") == "disabled"
    with pytest.raises(EntradaInvalida):
        muda(F, "fluxo-validado", "quarantined")
    with pytest.raises(EntradaInvalida):
        muda(R, mundo.ativa, "disabled")
    with pytest.raises(NaoEncontrado):
        muda(F, "nao-existe", "disabled")

    assert muda(R, mundo.ativa, "quarantined") == "quarantined"
    assert trilha(R, mundo.ativa) == [("published", "disabled", "panel", "pelo painel: quarantined")]
    assert muda(R, mundo.candidata, "quarantined") == "quarantined"
    assert trilha(R, mundo.candidata)[-1][:2] == ("candidate", "disabled")
    # Nunca duas ativas por chave: com a v1 religada, a v2 da mesma etapa não se reativa (a rota antiga deixava).
    assert muda(R, mundo.ativa, "active") == "active"
    with pytest.raises(ConflitoDeEstado):
        muda(R, mundo.em_quarentena, "active")
    # A substituída não volta nem vai para a quarentena; a que não existe é 404.
    for status in ("active", "quarantined"):
        with pytest.raises(TransicaoProibida):
            muda(R, mundo.substituida, status)
    assert db.scalar("SELECT status FROM recipes WHERE id=?", (mundo.substituida,)) == "superseded"
    with pytest.raises(NaoEncontrado):
        muda(R, 987654, "quarantined")
    # A com commit que o D1 parou em `validated`: a pessoa a publica.
    assert muda(R, mundo.validada_com_commit, "active") == "active"


async def test_sem_o_aprendizado_composto_a_rota_diz_503() -> None:
    app = FastAPI()
    app.include_router(learning_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/aprendizado/pendentes")
        assert r.status_code == 503 and r.json()["detail"]["code"] == "not_ready"


# ------------------------------------------------------------------ digest no fim da execução (Harness)
class _Gravador:
    nome = "gravador"

    def __init__(self) -> None:
        self.execucoes: list[str] = []

    def minerar(self, run_id: str) -> int:
        self.execucoes.append(run_id)
        return 1


class _Quebrado:
    nome = "quebrado"

    def minerar(self, run_id: str) -> int:
        raise RuntimeError("minerador com defeito")


async def test_digest_encadeado_no_fim_da_execucao_sem_derrubar_nada(harness: Harness) -> None:
    from app.main import create_app

    state = harness.state
    assert state is not None
    gravador = _Gravador()
    state.learning.registrar_minerador(_Quebrado())                # roda primeiro e lança
    state.learning.registrar_minerador(gravador)
    run = harness.run(["android-01"])
    detalhe = await harness.wait_run(run.id)
    assert detalhe.status == "completed"                           # o digest nunca muda o desfecho
    await harness.wait(lambda: run.id in gravador.execucoes, 10, "digest da execução")
    await harness.wait(lambda: not state._digestoes, 10, "digest encerrado")
    # O livro também responde pela aplicação inteira (`create_app`), e a retenção passa pelo aprendizado sem cair.
    app = create_app(harness.cfg, state=state)
    app.state.poc = state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/aprendizado?kind=fluxo")
        assert r.status_code == 200, r.text
    assert state._purgar_demais_tabelas("2026-01-01T00:00:00Z") >= 0
    assert state.learning.curar().falhas == ()
