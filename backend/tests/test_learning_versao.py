"""O estado de versão por item do Livro (30.6, `docs/design/aprendizado-vivo.md` §7): em que versões do app a receita
foi validada, quais estão vivas no parque e o estado por versão, no campo `versao` do detalhe.

- domínio puro (`domain/versao.py`): cada estado do §7 pela regra, e o "não sei" explícito (sem versão viva);
- fontes SQL: as versões vivas (aparelho aposentado e app ausente não contam) e a chave da receita em todas as versões;
- o caso do plano: duas versões vivas, receita só na antiga = `nao_testado` na nova;
- rota `GET /api/aprendizado/{kind}/{ref}`: `versao` em receita, tela, fluxo (independente) e memória.

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
from app.modules.learning.domain import versao as dominio
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.livro import Escopo, NovoItem
from app.modules.learning.domain.versao import EstadoDeVersao as E
from app.modules.learning.domain.versao import ReceitaDaChave, VersaoViva
from app.modules.learning.domain.vocabulario import LivroKind, SourceKind
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
ANTES = "2026-09-01T10:00:00+00:00"
DEPOIS = "2026-09-20T10:00:00+00:00"


# ------------------------------------------------------------------ domínio puro
def _r(ref: str, versao_app: str, *, status: str = "active", ok: int = 3, falhas: int = 0, n: int = 1,
       criada: str = ANTES) -> ReceitaDaChave:
    return ReceitaDaChave(ref, versao_app, n, status, ok, falhas, criada)


def _estado(r: ReceitaDaChave, vivas: set[str], outras: list[ReceitaDaChave] | None = None) -> E:
    return dominio.estado_da_receita(r, vivas=frozenset(vivas), da_chave=[r, *(outras or [])])


def test_estados_da_receita_pela_regra_do_desenho() -> None:
    assert _estado(_r("1", "447"), {"447"}) is E.COMPROVADO
    assert _estado(_r("1", "447", ok=0), {"447"}) is E.EM_PROVA
    assert _estado(_r("1", "447", falhas=2), {"447"}) is E.FALHANDO
    assert _estado(_r("1", "447", status="superseded"), {"447"}) is E.SUPERSEDED
    # versão que nenhum aparelho tem mais: fora de uso, não é falha (vale mesmo com falhas ou quarentena)
    assert _estado(_r("1", "447", falhas=3), {"450"}) is E.VERSAO_APOSENTADA
    assert _estado(_r("1", "447", status="quarantined"), {"450"}) is E.VERSAO_APOSENTADA
    # quarentena sem comprovada anterior = falhando; com a da versão anterior comprovada = incompatível
    nova = _r("2", "450", status="quarantined", criada=DEPOIS)
    assert _estado(nova, {"447", "450"}) is E.FALHANDO
    assert _estado(nova, {"447", "450"}, [_r("1", "447")]) is E.INCOMPATIVEL
    # a anterior só vale se estava mesmo comprovada (sem uso ou falhando não é base de comparação)
    assert _estado(nova, {"447", "450"}, [_r("1", "447", ok=0)]) is E.FALHANDO
    assert _estado(nova, {"447", "450"}, [_r("1", "447", falhas=1)]) is E.FALHANDO


def test_sem_nenhuma_versao_viva_nao_se_promete_comprovado() -> None:
    assert _estado(_r("1", "447"), set()) is E.DESCONHECIDO
    assert _estado(_r("1", "447", ok=0), set()) is E.EM_PROVA            # sem prova nenhuma: o fato não depende da viva
    assert _estado(_r("1", "447", falhas=1), set()) is E.FALHANDO


def test_quadro_da_receita_duas_vivas_so_na_antiga_e_nao_testado_na_nova() -> None:
    antiga = _r("1", "447")
    q = dominio.quadro_da_receita(antiga, app=PACOTE, da_chave=[antiga],
                                  vivas=[VersaoViva("447", 2), VersaoViva("450", 1)])
    assert q["estado"] == "comprovado" and q["app"] == PACOTE and q["app_version"] == "447"
    assert q["vivas"] == [{"versao": "447", "aparelhos": 2}, {"versao": "450", "aparelhos": 1}]
    assert q["nao_testada_em"] == ["450"]
    assert q["por_versao"] == [
        {"versao": "447", "viva": True, "aparelhos": 2, "estado": "comprovado", "receita_ref": "1"},
        {"versao": "450", "viva": True, "aparelhos": 1, "estado": "nao_testado", "receita_ref": None}]


def test_quadro_lista_a_versao_aposentada_e_a_trocada_so_se_for_a_unica() -> None:
    v1 = _r("1", "447", status="superseded", n=1)
    v2 = _r("2", "447", n=2)
    antiga = _r("3", "430")
    q = dominio.quadro_da_receita(v1, app=PACOTE, da_chave=[v1, v2, antiga], vivas=[VersaoViva("447", 1)])
    assert q["estado"] == "superseded"                                  # o da PRÓPRIA linha
    por = {p["versao"]: p for p in q["por_versao"]}                      # type: ignore[union-attr]
    assert por["447"]["receita_ref"] == "2" and por["447"]["estado"] == "comprovado"
    assert por["430"]["viva"] is False and por["430"]["estado"] == "versao_aposentada"
    assert q["nao_testada_em"] == []


def test_independente_e_tela() -> None:
    assert dominio.quadro_independente() == {"estado": "independente", "app": None, "app_version": None,
                                             "vivas": [], "nao_testada_em": [], "por_versao": []}
    vivas = [VersaoViva("447", 1), VersaoViva("450", 1)]
    velha = datetime(2026, 9, 1, tzinfo=UTC)

    def tela(versao_da_tela: str | None, *, viva: list[VersaoViva], a_favor: datetime | None) -> str:
        return str(dominio.quadro_da_tela(app=PACOTE, app_version=versao_da_tela, vivas=viva, ultima_a_favor=a_favor,
                                          criada=velha, agora=AGORA)["estado"])

    assert tela("447", viva=vivas, a_favor=velha) == "incompativel"      # dias sem casar + versão nova no parque
    assert tela("447", viva=vivas, a_favor=AGORA) == "desconhecido"      # casou hoje: sem sinal de quebra, sem promessa
    assert tela("430", viva=[VersaoViva("447", 1)], a_favor=AGORA) == "versao_aposentada"
    assert tela(None, viva=vivas, a_favor=velha) == "desconhecido"       # sem versão: não se inventa
    assert tela("447", viva=[], a_favor=velha) == "desconhecido"         # sem aparelho observado


# ------------------------------------------------------------------ o mundo semeado (SQL)
def _receita(db: Database, app_version: str, *, versao: int = 1, status: str = "active", ok: int = 3, falhas: int = 0,
             criada: str = TS, step_hash: str = "h-abrir", pacote: str = PACOTE) -> int:
    return int(db.inserted_id(
        "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version, status,"
        " actions, replay_ok, replay_fail, consecutive_fail, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pacote, app_version, "sig", "pt/420", step_hash, "abrir", versao, status,
         json.dumps([{"tool": "tap", "args": {}, "selectors": [], "commit": False}]), ok, 0, falhas, criada)))


def _observa(db: Database, aparelho: str, versao: str | None, *, estado: str = "installed",
             pacote: str = PACOTE) -> None:
    db.execute("INSERT INTO device_app_state(instance_id, package_name, observed_version_name, state)"
               " VALUES (?,?,?,?)", (aparelho, pacote, versao, estado))


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',?,0)", (PACOTE,))
        habilidades = SqlSkillRepository(db, ValidadorFalso())
        self.repo = SqlLearningRepository(db, guarda_do_fluxo=GuardaDoFluxo(db, habilidades), precos=dict)
        self.fontes = FontesSql(db)
        self.servico = LearningService(self.repo, self.fontes, TriagemDeCredencial(), ajustes=Ajustes,
                                       relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14)

    def versao(self, kind: LivroKind, ref: str | int) -> dict[str, object]:
        v = self.servico.detalhe(kind, str(ref)).versao
        assert v is not None
        return v


@pytest.fixture
def mundo(tmp_path: Path) -> Mundo:
    db = banco_migrado(tmp_path, "versao.sqlite3")
    yield Mundo(db)
    db.close()


@pytest.fixture
async def cliente(mundo: Mundo) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.servico)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def test_vivas_contam_so_aparelho_ativo_com_o_app(mundo: Mundo) -> None:
    db = mundo.db
    db.execute("INSERT INTO instances(id, idx, avd_name, console_port, system_port, mjpeg_port, chromedriver_port,"
               " retired_at) VALUES ('android-09', 9, 'a9', 19009, 19109, 19209, 19309, ?)", (TS,))
    _observa(db, "android-01", "447")
    _observa(db, "android-02", "447")
    _observa(db, "android-03", "450")
    _observa(db, "android-04", "430", estado="missing")                  # o app saiu do aparelho
    _observa(db, "android-05", None)                                      # nunca observado
    _observa(db, "android-09", "300")                                     # aparelho aposentado
    _observa(db, "android-01", "999", pacote="com.outro.app")             # outro pacote
    assert mundo.fontes.vivas(PACOTE) == (VersaoViva("447", 2), VersaoViva("450", 1))
    assert mundo.fontes.vivas("com.sem.aparelho") == ()


def test_duas_vivas_receita_so_na_antiga_e_nao_testado_na_nova(mundo: Mundo) -> None:
    _observa(mundo.db, "android-01", "447")
    _observa(mundo.db, "android-02", "450")
    rid = _receita(mundo.db, "447")
    v = mundo.versao(LivroKind.RECEITA, rid)
    assert v["estado"] == "comprovado" and v["nao_testada_em"] == ["450"]
    por = {p["versao"]: p for p in v["por_versao"]}                      # type: ignore[union-attr]
    assert por["447"]["estado"] == "comprovado" and por["447"]["receita_ref"] == str(rid)
    assert por["450"] == {"versao": "450", "viva": True, "aparelhos": 1, "estado": "nao_testado", "receita_ref": None}


def test_receita_nas_duas_versoes_a_chave_exata_decide(mundo: Mundo) -> None:
    _observa(mundo.db, "android-01", "447")
    _observa(mundo.db, "android-02", "450")
    antiga = _receita(mundo.db, "447", criada=ANTES)
    nova = _receita(mundo.db, "450", status="quarantined", ok=0, falhas=3, criada=DEPOIS)
    outra_etapa = _receita(mundo.db, "450", step_hash="h-outra")        # outra chave: não conta
    assert mundo.versao(LivroKind.RECEITA, nova)["estado"] == "incompativel"
    v = mundo.versao(LivroKind.RECEITA, antiga)
    assert v["estado"] == "comprovado" and v["nao_testada_em"] == []
    assert {p["versao"]: p["estado"] for p in v["por_versao"]} == {"447": "comprovado",   # type: ignore[union-attr]
                                                                  "450": "incompativel"}
    assert mundo.versao(LivroKind.RECEITA, outra_etapa)["nao_testada_em"] == ["447"]


def test_receita_de_versao_que_saiu_do_parque_e_aposentada_nao_falha(mundo: Mundo) -> None:
    _observa(mundo.db, "android-01", "450")
    rid = _receita(mundo.db, "447")
    v = mundo.versao(LivroKind.RECEITA, rid)
    assert v["estado"] == "versao_aposentada" and v["nao_testada_em"] == ["450"]


def test_sem_aparelho_observado_a_receita_fica_desconhecida_e_nao_comprovada(mundo: Mundo) -> None:
    v = mundo.versao(LivroKind.RECEITA, _receita(mundo.db, "447"))
    assert v["estado"] == "desconhecido" and v["vivas"] == [] and v["por_versao"][0]["viva"] is False  # type: ignore[index]


def test_tela_sem_casar_com_versao_nova_e_incompativel(mundo: Mundo) -> None:
    _observa(mundo.db, "android-01", "447")
    _observa(mundo.db, "android-02", "450")
    tela = mundo.repo.criar_item(
        NovoItem(kind=LivroKind.TELA, escopo=Escopo(app=PACOTE),
                 content={"tela": "aprendida:feed", "tipo": "regra_de_tela", "ids_todos": ["a"], "casa": True},
                 summary="feed", source_kind=SourceKind.SCREEN_OBSERVATION, side_effect=False, app_version="447"),
        by="sistema", estado=SkillState.CANDIDATE, detalhe=None, reason="teste")
    mundo.db.execute("UPDATE learning_items SET created_at=? WHERE id=?", (ANTES, tela.id))
    v = mundo.versao(LivroKind.TELA, tela.id)
    assert v["estado"] == "incompativel" and v["app_version"] == "447" and len(v["vivas"]) == 2  # type: ignore[arg-type]


async def test_rota_devolve_versao_em_receita_licao_e_memoria(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    _observa(mundo.db, "android-01", "447")
    _observa(mundo.db, "android-02", "450")
    rid = _receita(mundo.db, "447")
    r = await cliente.get(f"/api/aprendizado/receita/{rid}")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert set(corpo["versao"]) == {"estado", "app", "app_version", "vivas", "nao_testada_em", "por_versao"}
    assert corpo["versao"]["estado"] == "comprovado" and corpo["versao"]["nao_testada_em"] == ["450"]
    assert corpo["conteudo"]["tipo"] == "receita"                        # o `conteudo` do 30.3 segue ao lado
    licao = mundo.repo.criar_item(
        NovoItem(kind=LivroKind.LICAO, escopo=Escopo(app=PACOTE, capability="SEND_MESSAGE", role="actor"),
                 content={"modelo": "nota", "acao": "SEND_MESSAGE", "nota": "confira"}, summary="Confira.",
                 source_kind=SourceKind.FEEDBACK_NOTE, side_effect=False, app_version="447"),
        by="sistema", estado=SkillState.CANDIDATE, detalhe=None, reason="teste")
    assert (await cliente.get(f"/api/aprendizado/licao/{licao.id}")).json()["versao"]["estado"] == "independente"
    perfil(mundo.db, "p1")
    mundo.db.execute("INSERT INTO memory_items(id, profile_id, subject, content, source, fingerprint, created_at,"
                     " updated_at) VALUES (?,?,?,?,?,?,?,?)", ("m0", "p1", "@ana", "x", "operator", "fp", TS, TS))
    m = await cliente.get("/api/aprendizado/memoria/p1")
    assert m.status_code == 200 and m.json()["versao"]["estado"] == "independente"
