"""30.24, "Confirmar que fica": o legado publicado com efeito de "Revisar" ganha um gesto da pessoa que o mantém (quem,
quando, motivo opcional) e sai da fila até nova evidência contrária real. Aceitar o parecer "manter" num item de
"Revisar" é o mesmo gesto (a pendência A6 do 30.17). Prova `simulated`: banco de teste e curador falso."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.db import Database
from app.modules.learning.application.ports import NovaEvidencia, NovaRevisao
from app.modules.learning.domain.ciclo import (SYSTEM_ACTOR, ConflitoDeEstado, EntradaInvalida, NotaComCaraDeSegredo,
                                               SkillState, TransicaoProibida)
from app.modules.learning.domain.curador import Decisao, Parecer
from app.modules.learning.domain.livro import (CONFIRMADO_QUE_FICA, Transicao, decididos_para_revisar, e_confirmacao,
                                               motivo_da_confirmacao)
from app.modules.learning.domain.politica_de_risco import ClasseDeRisco
from app.modules.learning.domain.promocao import Evidencia
from app.modules.learning.domain.vocabulario import LivroKind, Posicao, SignalKind
from app.modules.learning.infrastructure.revisoes_sql import RegistroDeRevisoesSql
from app.modules.learning.presentation.router import router as learning_router
from app.util import to_iso

from .fake_skills import banco as banco_migrado
from .test_learning_pareceres import PACOTE, Mundo

S = SkillState
PESSOA = "painel:Ana Ribeiro"
TS = "2026-10-01T10:00:00Z"
R = LivroKind.RECEITA


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "confirmar.sqlite3")
    d.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',?,0)", (PACOTE,))
    yield d
    d.close()


@pytest.fixture
def mundo(db: Database) -> Mundo:
    return Mundo(db)


@pytest.fixture
async def cliente(mundo: Mundo) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.servico)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _legado(db: Database, passo: str = "enviar", *, commit: bool = True, status: str = "active") -> str:
    """Receita ativa de antes do D1: sem trilha, e com `commit` ela está em "Revisar"."""
    acoes = [{"tool": "tap", "args": {}, "selectors": [{"rid": "botao"}], "commit": commit, "why": "a IA explicou"}]
    return str(db.inserted_id(
        "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version, status,"
        " actions, learned_from_step, created_at) VALUES (?,?,?,?,?,?,1,?,?,?,?)",
        (PACOTE, "447", "sig", "pt/420", f"h-{passo}", passo, status, json.dumps(acoes), f"r1:a:v1:{passo}", TS)))


def _fluxo_legado(db: Database, fid: str) -> None:
    plano = {"summary": fid, "app_id": "instagram", "parameters": {},
             "steps": [{"key": "a", "title": "a", "goal": "a", "side_effect": True,
                        "postcondition": {"kind": "app_foreground", "value": "x", "description": "x"}}]}
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at, source)"
               " VALUES (?,?,?,?,?,?,'active',?,'run')", (fid, fid, f"cmd {fid}", f"cmd {fid}", json.dumps(plano),
                                                          "instagram", TS))


def _fila(mundo: Mundo) -> set[str]:
    return {e.trail_ref for e in mundo.servico.revisar()}


def _contra(mundo: Mundo, ref: str, origem: str, *, posicao: Posicao = Posicao.AGAINST, simulado: bool = False) -> None:
    assert mundo.repo.registrar_evidencia(NovaEvidencia(item_ref=ref, stance=posicao, origin_ref=origem,
                                                        simulated=simulado, run_id="r-20261002120000-abcdef"))


def _revisao(mundo: Mundo, rid: str, decisao: Decisao) -> str:
    parecer = Parecer(decisao=decisao, evidencias_citadas=(f"receita:{rid}",))
    review = RegistroDeRevisoesSql(mundo.db).gravar(NovaRevisao(
        item_ref=f"receita:{rid}", item_kind="receita", scope_app=PACOTE, gatilho="a_revisar",
        dossie_hash=f"h-{rid}-{decisao.value}", dossie={"item": {"estado": "published"}}, template_id="curador",
        template_versao="dossie-v1", provedor="teste", modelo="m", simulated=False, validade="ok",
        saida=parecer.como_dados(), classe_de_risco=ClasseDeRisco.B.value, politica="teste"), mundo.agora)
    assert review is not None
    return review


# ------------------------------------------------------------------ domínio: a regra da fila
def _t(*, de: SkillState = S.PUBLISHED, por: str = PESSOA, motivo: str = CONFIRMADO_QUE_FICA,
       em: str = "2026-10-02T12:00:00.000Z") -> Transicao:
    return Transicao(id=1, item_ref="receita:1", item_kind=R, content_hash="h", scope_key="k", app_version="447",
                     from_state=de, to_state=S.PUBLISHED, reason=motivo, decided_by=por, decided_at=em, run_id=None)


def _ev(posicao: Posicao, em: str, *, simulado: bool = False) -> Evidencia:
    return Evidencia(item_ref="receita:1", stance=posicao, origin_ref="signal:1", run_id=None, instance_id=None,
                     app_version=None, simulated=simulado, detail=None, observed_at=em)


def test_a_regra_da_fila() -> None:
    conf = _t()
    assert e_confirmacao(conf) and e_confirmacao(_t(motivo="confirmado que fica: conferi"))
    assert not e_confirmacao(_t(por=SYSTEM_ACTOR))
    assert not e_confirmacao(_t(de=S.DISABLED, motivo="volta"))                          # reativar
    assert not e_confirmacao(_t(motivo="conferi"))
    antes, depois = "2026-10-02T11:00:00.000Z", "2026-10-02T13:00:00.000Z"

    def decidida(*evs: Evidencia, t: Transicao = conf) -> bool:
        return "receita:1" in decididos_para_revisar({"receita:1": t}, {"receita:1": evs})

    assert decidida()                                                       # confirmada: fora da fila
    assert not decidida(_ev(Posicao.AGAINST, depois))                       # contestada: volta
    assert not decidida(_ev(Posicao.CONFLICT, depois))
    assert decidida(_ev(Posicao.AGAINST, antes))                            # a de antes, quem confirmou já via
    assert decidida(_ev(Posicao.AGAINST, depois, simulado=True))            # simulada não prova nada
    assert decidida(_ev(Posicao.FOR, depois))
    # Outra decisão de pessoa (reativar) segue como antes: a evidência contrária não a devolve à fila.
    assert decidida(_ev(Posicao.AGAINST, depois), t=_t(de=S.DISABLED, motivo="volta"))
    assert motivo_da_confirmacao("  ") == CONFIRMADO_QUE_FICA
    assert motivo_da_confirmacao(" conferi o alvo ") == "confirmado que fica: conferi o alvo"


# ------------------------------------------------------------------ o verbo
def test_confirmar_tira_da_fila_com_trilha_e_nao_muda_o_item(mundo: Mundo) -> None:
    rid = _legado(mundo.db)
    assert f"receita:{rid}" in _fila(mundo)
    e = mundo.servico.confirmar_que_fica(R, rid, by=PESSOA, motivo=" conferi o alvo ")
    assert (e.state, e.native_status) == (S.PUBLISHED, "active")
    assert f"receita:{rid}" not in _fila(mundo)
    [t] = mundo.repo.trilha(f"receita:{rid}")
    assert (t.from_state, t.to_state, t.decided_by, t.reason, t.decided_at) == (
        S.PUBLISHED, S.PUBLISHED, PESSOA, "confirmado que fica: conferi o alvo", to_iso(mundo.agora))
    assert mundo.db.scalar("SELECT status FROM recipes WHERE id=?", (int(rid),)) == "active"
    sem_motivo = _legado(mundo.db, "outro")
    mundo.servico.confirmar_que_fica(R, sem_motivo, by=PESSOA)
    assert mundo.repo.trilha(f"receita:{sem_motivo}")[-1].reason == CONFIRMADO_QUE_FICA


def test_evidencia_contraria_real_depois_devolve_e_confirmar_de_novo_tira(mundo: Mundo) -> None:
    rid = _legado(mundo.db)
    ref = f"receita:{rid}"
    _contra(mundo, ref, "signal:1")                                         # a de antes: quem confirma já a via
    mundo.agora += timedelta(minutes=5)
    mundo.servico.confirmar_que_fica(R, rid, by=PESSOA)
    assert ref not in _fila(mundo)
    mundo.agora += timedelta(minutes=5)
    _contra(mundo, ref, "signal:2", simulado=True)
    _contra(mundo, ref, "signal:3", posicao=Posicao.FOR)
    assert ref not in _fila(mundo)
    _contra(mundo, ref, "signal:4")
    assert ref in _fila(mundo)
    mundo.agora += timedelta(minutes=5)
    mundo.servico.confirmar_que_fica(R, rid, by=PESSOA, motivo="vi a evidência e fica")
    assert ref not in _fila(mundo)
    assert [t.reason for t in mundo.repo.trilha(ref)] == [CONFIRMADO_QUE_FICA, "confirmado que fica: vi a evidência e fica"]


def test_fluxo_legado_com_efeito_tambem(mundo: Mundo) -> None:
    _fluxo_legado(mundo.db, "enviar-dm")
    assert "fluxo:enviar-dm" in _fila(mundo)
    mundo.servico.confirmar_que_fica(LivroKind.FLUXO, "enviar-dm", by=PESSOA)
    assert "fluxo:enviar-dm" not in _fila(mundo)
    assert mundo.db.scalar("SELECT status FROM flows WHERE id='enviar-dm'") == "active"
    assert e_confirmacao(mundo.repo.trilha("fluxo:enviar-dm")[-1])


def test_so_o_que_esta_em_revisar_e_so_a_pessoa(mundo: Mundo) -> None:
    sem_efeito = _legado(mundo.db, "abrir", commit=False)
    quarentena = _legado(mundo.db, "q", status="quarantined")
    rid = _legado(mundo.db)
    for ref in (sem_efeito, quarentena):
        with pytest.raises(ConflitoDeEstado):
            mundo.servico.confirmar_que_fica(R, ref, by=PESSOA)
    with pytest.raises(TransicaoProibida):
        mundo.servico.confirmar_que_fica(R, rid, by=SYSTEM_ACTOR)
    with pytest.raises(EntradaInvalida):
        mundo.servico.confirmar_que_fica(LivroKind.LICAO, "li-1", by=PESSOA)
    with pytest.raises(NotaComCaraDeSegredo):
        mundo.servico.confirmar_que_fica(R, rid, by=PESSOA, motivo="a senha da conta e abc12345")
    mundo.servico.confirmar_que_fica(R, rid, by=PESSOA)
    with pytest.raises(ConflitoDeEstado):                                   # duas vezes: já saiu da fila
        mundo.servico.confirmar_que_fica(R, rid, by=PESSOA)
    assert int(mundo.db.scalar("SELECT COUNT(*) FROM learning_transitions")) == 1


def test_outra_decisao_da_pessoa_nao_volta_com_evidencia(mundo: Mundo) -> None:
    """Desligar e religar já tirava o item de "Revisar" para sempre; a regra do retorno é só da confirmação."""
    rid = _legado(mundo.db)
    mundo.servico.mudar_estado(R, rid, S.DISABLED, by=PESSOA, reason="testar")
    mundo.servico.mudar_estado(R, rid, S.PUBLISHED, by=PESSOA, reason="volta")
    mundo.agora += timedelta(minutes=5)
    _contra(mundo, f"receita:{rid}", "signal:9")
    assert f"receita:{rid}" not in _fila(mundo)


# ------------------------------------------------------------------ A6: o parecer "manter"
def test_aceitar_manter_em_revisar_confirma_que_fica(mundo: Mundo) -> None:
    rid = _legado(mundo.db)
    review = _revisao(mundo, rid, Decisao.MANTER)
    mundo.pareceres.responder(R, rid, review, aceitar=True, motivo="concordo", by=PESSOA)
    assert f"receita:{rid}" not in _fila(mundo)
    [t] = mundo.repo.trilha(f"receita:{rid}")
    assert e_confirmacao(t) and t.reason == "confirmado que fica: concordo"
    linha = mundo.db.one("SELECT decisao_final, transicao_id FROM learning_reviews WHERE id=?", (review,))
    assert linha is not None and (linha["decisao_final"], linha["transicao_id"]) == ("aceitou", t.id)
    assert len(mundo.sinais(SignalKind.PARECER_DECIDIDO)) == 1


def test_aceitar_manter_fora_de_revisar_so_concorda(mundo: Mundo) -> None:
    rid = _legado(mundo.db, "abrir", commit=False)                          # publicada sem efeito: não é "Revisar"
    review = _revisao(mundo, rid, Decisao.MANTER)
    mundo.pareceres.responder(R, rid, review, aceitar=True, motivo="concordo", by=PESSOA)
    assert mundo.repo.trilha(f"receita:{rid}") == []
    linha = mundo.db.one("SELECT decisao_final, transicao_id FROM learning_reviews WHERE id=?", (review,))
    assert linha is not None and (linha["decisao_final"], linha["transicao_id"]) == ("aceitou", None)


def test_confirmar_com_parecer_de_desativar_a_vista_recusa_o_parecer(mundo: Mundo) -> None:
    rid = _legado(mundo.db)
    review = _revisao(mundo, rid, Decisao.DESATIVAR)
    mundo.pareceres.confirmar_que_fica(R, rid, by=PESSOA, motivo="o alvo está certo", review_id=review)
    linha = mundo.db.one("SELECT decisao_final, override, transicao_id FROM learning_reviews WHERE id=?", (review,))
    [t] = mundo.repo.trilha(f"receita:{rid}")
    assert linha is not None and (linha["decisao_final"], linha["override"], linha["transicao_id"]) == (
        "recusou", 1, t.id)


# ------------------------------------------------------------------ a rota
async def test_rota_confirmar(cliente: httpx.AsyncClient, mundo: Mundo) -> None:
    rid = _legado(mundo.db)
    r = await cliente.post(f"/api/aprendizado/receita/{rid}/confirmar", json={})
    assert r.status_code == 200, r.text
    [t] = r.json()["trilha"]
    assert (t["from"], t["to"], t["tipo"], t["reason"], t["motivo_da_pessoa"]) == (
        "published", "published", "confirmacao", CONFIRMADO_QUE_FICA, None)
    com_motivo = await cliente.post(f"/api/aprendizado/receita/{_legado(mundo.db, 'm')}/confirmar",
                                    json={"motivo": " conferi o alvo "})
    assert com_motivo.json()["trilha"][-1]["motivo_da_pessoa"] == "conferi o alvo"
    fila = await cliente.get("/api/aprendizado/revisar")
    assert rid not in {i["ref"] for i in fila.json()["itens"]}
    de_novo = await cliente.post(f"/api/aprendizado/receita/{rid}/confirmar", json={"motivo": "outra vez"})
    assert de_novo.status_code == 409 and de_novo.json()["detail"]["code"] == "state_conflict"
    licao = await cliente.post(f"/api/aprendizado/licao/{mundo.licao_a()}/confirmar", json={})
    assert licao.status_code == 422                                        # só receita e fluxo
    assert (await cliente.post("/api/aprendizado/receita/99999/confirmar", json={})).status_code == 404
    longo = await cliente.post(f"/api/aprendizado/receita/{_legado(mundo.db, 'x')}/confirmar", json={"motivo": "x" * 501})
    assert longo.status_code == 422
