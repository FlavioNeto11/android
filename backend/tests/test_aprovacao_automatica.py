"""30.55: a aprovação automática por política. A régua pura (cada condição recusa sozinha), o motivo que é contrato com a
Canais e, com o banco migrado, a volta em `shadow` (marca uma vez, não decide) e em `on` (publica o de "Para aprovar" e
confirma que fica o de "Revisar", com `decided_by = plataforma`; o que o dono desfaz não volta a ser decidido). Prova
`simulated` (banco de teste, sem IA nem aparelho)."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.config import AprovacaoAutomaticaCfg, LearningCfg
from app.db import Database
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.aprovacao_automatica import (PLATAFORMA, Acao, FatosDaAprovacao, Fila, Gesto,
                                                              ModoDaAprovacao, MotivoDeFora, ParecerParaAprovar, Regra,
                                                              acao, avaliar, motivo, regra_do_motivo)
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.curador import Decisao
from app.modules.learning.domain.politica_de_risco import ClasseDeRisco
from app.modules.learning.domain.saude import Rotulo
from app.modules.learning.domain.vocabulario import LivroKind, SignalKind
from app.modules.learning.infrastructure import ligar_aprovacao_automatica
from app.modules.learning.infrastructure.feedback_sql import LeituraDoVotoSql
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router
from app.shared import costuras
from app.util import to_iso

from .fake_skills import banco as banco_migrado

QA = "com.pocqa.messenger"
IG = "com.instagram.android"
AGORA = datetime(2026, 10, 4, 17, 0, tzinfo=UTC)
PESSOA = "painel:Flavio"
LIDER = lambda: 1  # noqa: E731
SEM_LIDER = lambda: None  # noqa: E731


# ------------------------------------------------------------------ a régua pura
def _fatos(**mudar: object) -> FatosDaAprovacao:
    base = FatosDaAprovacao(kind="receita", ref="1", fila=Fila.PARA_APROVAR, apps=(QA,), apps_de_teste=frozenset({QA}),
                            classe=ClasseDeRisco.B, a_favor=2, contra=0, falhas_de_reproducao=0, execucoes=0,
                            aparelhos=0, saude=Rotulo.EM_PROVA, parecer=None, reaprendido=False, texto_de_pessoa=False,
                            vetado=False, ja_decidido=False)
    return replace(base, **mudar)  # type: ignore[arg-type]


def _parecer(decisao: Decisao, *, simulado: bool = False, decidido: bool = False) -> ParecerParaAprovar:
    return ParecerParaAprovar(id="lr-1", decisao=decisao, simulado=simulado, decidido=decidido)


def test_a_regua_decide_o_que_cumpre_tudo() -> None:
    a = avaliar(_fatos())
    assert a.decide and (a.regra, a.gesto, a.motivos) == (Regra.QA_PARA_APROVAR, Gesto.PUBLICAR, ())
    r = avaliar(_fatos(fila=Fila.REVISAR, kind="fluxo", saude=Rotulo.SAUDAVEL))
    assert r.decide and (r.regra, r.gesto) == (Regra.QA_REVISAR, Gesto.CONFIRMAR)
    assert avaliar(_fatos(classe=ClasseDeRisco.A)).decide


@pytest.mark.parametrize(("mudar", "esperado"), [
    ({"ja_decidido": True}, MotivoDeFora.JA_DECIDIDO),
    ({"apps": (IG,)}, MotivoDeFora.APP_FORA_DO_QA),
    ({"apps": (QA, IG)}, MotivoDeFora.APP_FORA_DO_QA),                   # o fluxo de dois apps: todos têm de ser qa
    ({"apps": ()}, MotivoDeFora.APP_FORA_DO_QA),                         # sem app resolvido
    ({"classe": ClasseDeRisco.C}, MotivoDeFora.CLASSE_C),
    ({"a_favor": 0}, MotivoDeFora.SEM_A_FAVOR),
    ({"contra": 1}, MotivoDeFora.EVIDENCIA_CONTRA),
    ({"falhas_de_reproducao": 1}, MotivoDeFora.FALHA_DE_REPRODUCAO),
    ({"saude": Rotulo.DEGRADANDO}, MotivoDeFora.SAUDE_REBAIXANDO),
    ({"saude": Rotulo.OBSOLETO_PROVAVEL}, MotivoDeFora.SAUDE_REBAIXANDO),
    ({"parecer": _parecer(Decisao.REBAIXAR)}, MotivoDeFora.PARECER_CONTRA),
    ({"parecer": _parecer(Decisao.DESATIVAR)}, MotivoDeFora.PARECER_CONTRA),
    ({"parecer": _parecer(Decisao.POSSIVELMENTE_OBSOLETO)}, MotivoDeFora.PARECER_CONTRA),
    ({"reaprendido": True}, MotivoDeFora.REAPRENDIDO),
    ({"texto_de_pessoa": True}, MotivoDeFora.TEXTO_DE_PESSOA),
    ({"vetado": True}, MotivoDeFora.VETADO),
])
def test_cada_condicao_recusa_sozinha(mudar: dict[str, object], esperado: MotivoDeFora) -> None:
    a = avaliar(_fatos(**mudar))
    assert not a.decide and a.motivos == (esperado,)


def test_fora_dos_tipos_e_das_filas() -> None:
    assert avaliar(_fatos(kind="licao")).motivos == (MotivoDeFora.TIPO_FORA,)
    assert avaliar(_fatos(fila=None)).motivos == (MotivoDeFora.FORA_DAS_FILAS,)


@pytest.mark.parametrize("p", [None, _parecer(Decisao.PEDIR_EVIDENCIA), _parecer(Decisao.OBSERVAR),
                               _parecer(Decisao.MANTER), _parecer(Decisao.APROVAR),
                               _parecer(Decisao.REBAIXAR, simulado=True), _parecer(Decisao.REBAIXAR, decidido=True)])
def test_o_parecer_que_nao_pesa_contra_nao_barra(p: ParecerParaAprovar | None) -> None:
    """A delegação do dono: sem parecer, ou com `pedir_evidencia` (a R1 do 31.42), decide; o simulado e o que uma
    pessoa já decidiu não pesam."""
    assert avaliar(_fatos(parecer=p)).decide


def test_a_acao_pelo_modo() -> None:
    sim, nao = avaliar(_fatos()), avaliar(_fatos(classe=ClasseDeRisco.C))
    assert [acao(m, sim) for m in ModoDaAprovacao] == [Acao.NADA, Acao.REGISTRAR, Acao.DECIDIR]
    assert {acao(m, nao) for m in ModoDaAprovacao} == {Acao.NADA}


def test_o_motivo_e_contrato_e_passa_na_triagem() -> None:
    f = _fatos(parecer=_parecer(Decisao.PEDIR_EVIDENCIA))
    texto = motivo(f, avaliar(f))
    assert texto.startswith("auto:qa_para_aprovar v1 — classe B; app com.pocqa.messenger (qa); 2 a favor, 0 contra;")
    assert "parecer pedir_evidencia (lr-1)" in texto and "execuções" not in texto   # a receita conta pela fonte
    fluxo = _fatos(kind="fluxo", fila=Fila.REVISAR, execucoes=2, aparelhos=2)
    texto_do_fluxo = motivo(fluxo, avaliar(fluxo))
    assert "2 a favor em 2 execuções e 2 aparelhos" in texto_do_fluxo
    assert "sem parecer que pese (delegação do dono)" in texto_do_fluxo
    assert regra_do_motivo(texto) == ("qa_para_aprovar", 1)
    assert regra_do_motivo(f"confirmado que fica: {texto_do_fluxo}") == ("qa_revisar", 1)
    assert regra_do_motivo("auto:qa_revisar@v1 — x") is None                   # o formato antigo, recusado pela triagem
    assert regra_do_motivo("conferi o alvo") is None
    # A confirmação passa o motivo pela triagem de credencial do livro: o `@` antigo era recusado (ensaio de 04/10).
    triagem = TriagemDeCredencial()
    assert not triagem.recusa(texto) and not triagem.recusa(texto_do_fluxo)
    with pytest.raises(ValueError):
        motivo(_fatos(fila=None), avaliar(_fatos(fila=None)))


def test_o_operador_nao_se_passa_pela_plataforma() -> None:
    assert costuras.PLATAFORMA == PLATAFORMA
    assert costuras.autor_do_gesto("plataforma") == "painel:plataforma"
    assert costuras.autor_do_gesto("Flavio") == "Flavio"


def test_o_config_de_fabrica_e_off() -> None:
    cfg = LearningCfg()
    assert (cfg.aprovacao_automatica.modo, cfg.aprovacao_automatica.intervalo_s) == ("off", 900)


# ------------------------------------------------------------------ com o banco migrado
class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.cfg = AprovacaoAutomaticaCfg(modo="shadow")
        self.repo = SqlLearningRepository(db, clock=lambda: to_iso(AGORA), precos=dict)
        self.servico = LearningService(self.repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes,
                                       relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14)
        # Sem catálogo: o commit em app sem catálogo é B (a regra confirmada pelo dono em 03/10).
        self.ap = ligar_aprovacao_automatica.ligar(self.servico, self.repo, db, config=lambda: self.cfg,
                                                   relogio=lambda: AGORA, catalogo=None)
        [laco] = [x for x in self.servico.lacos if isinstance(x, ligar_aprovacao_automatica.LacoDaAprovacaoAutomatica)]
        self.laco = laco

    def receita(self, passo: str, *, status: str, app: str = QA, replay_ok: int = 0, replay_fail: int = 0,
                sombra: tuple[int, int] = (0, 0)) -> str:
        acoes = [{"tool": "tap", "args": {}, "selectors": [{"rid": "enviar"}], "commit": True, "why": "envia"}]
        return "receita:" + str(self.db.inserted_id(
            "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version, status,"
            " actions, learned_from_step, replay_ok, replay_fail, shadow_agree, shadow_total, created_at)"
            " VALUES (?,?,?,?,?,?,1,?,?,?,?,?,?,?,?)",
            (app, "1", "sig", "pt/420", f"h-{passo}", passo, status, json.dumps(acoes), f"r1:a:v1:{passo}", replay_ok,
             replay_fail, sombra[0], sombra[1], to_iso(AGORA))))

    def trilha_da_plataforma(self) -> list[dict[str, object]]:
        return [dict(r) for r in self.db.query("SELECT * FROM learning_transitions WHERE decided_by=? ORDER BY id",
                                               (PLATAFORMA,))]


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "aprovacao.sqlite3")
    d.execute("INSERT INTO apps(id, name, package, builtin, category) VALUES ('qa','QA Messenger',?,0,'qa')", (QA,))
    d.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',?,0)", (IG,))
    yield d
    d.close()


@pytest.fixture
def mundo(db: Database) -> Mundo:
    return Mundo(db)


def _cenario(m: Mundo) -> dict[str, str]:
    return {
        "aprovar": m.receita("enviar", status="validated", sombra=(2, 2)),          # "Para aprovar", QA, a favor
        "revisar": m.receita("voltar", status="active", replay_ok=3),               # "Revisar", QA, a favor
        "instagram": m.receita("comentar", status="active", app=IG, replay_ok=5),   # conta real: fica com o dono
        "falhou": m.receita("abrir", status="validated", replay_ok=2, replay_fail=1),
        "sem_prova": m.receita("ler", status="validated"),
    }


def test_em_shadow_marca_uma_vez_e_nao_decide(mundo: Mundo) -> None:
    refs = _cenario(mundo)
    r = mundo.ap.uma_volta()
    assert (r.avaliados, set(r.decidiria), set(r.marcados), r.decididos) == (
        5, {refs["aprovar"], refs["revisar"]}, {refs["aprovar"], refs["revisar"]}, ())
    assert dict(r.fora) == {"app_fora_do_qa": 1, "falha_de_reproducao": 1, "sem_a_favor": 1}
    assert mundo.trilha_da_plataforma() == []
    assert {e.trail_ref for e in mundo.servico.pendentes()} >= {refs["aprovar"]}
    assert mundo.ap.uma_volta().marcados == ()                                 # um caso por item
    sinais = [dict(x) for x in mundo.db.query("SELECT * FROM learning_signals WHERE kind=?",
                                               (SignalKind.APROVARIA.value,))]
    assert {s["source_ref"] for s in sinais} == {f"aprovaria:{refs['aprovar']}", f"aprovaria:{refs['revisar']}"}
    assert json.loads(str(sinais[0]["data"]))["motivo"].startswith("auto:")
    # A marca do sistema fica fora da aba Sinais, como a da autopublicação.
    assert all(s.kind is not SignalKind.APROVARIA
               for s in LeituraDoVotoSql(mundo.db).sinais(desde="2026-01-01", kind=None, app=None, limite=50))
    rel = mundo.ap.relatorio(itens=True)
    assert rel["modo"] == "shadow" and rel["casos_na_sombra"] == 2 and rel["decididos_pela_plataforma"] == []
    assert {x["item_ref"]: x["decide"] for x in rel["itens"]} == {  # type: ignore[union-attr]
        refs["aprovar"]: True, refs["revisar"]: True, refs["instagram"]: False, refs["falhou"]: False,
        refs["sem_prova"]: False}


def test_em_on_publica_e_confirma_pela_porta_da_pessoa(mundo: Mundo) -> None:
    refs = _cenario(mundo)
    mundo.cfg = AprovacaoAutomaticaCfg(modo="on")
    r = mundo.ap.uma_volta()
    assert set(r.decididos) == {refs["aprovar"], refs["revisar"]}
    aprovada = mundo.servico.entrada(LivroKind.RECEITA, refs["aprovar"].removeprefix("receita:"))
    assert (aprovada.state, aprovada.native_status) == (SkillState.PUBLISHED, "active")
    trilha = {str(t["item_ref"]): t for t in mundo.trilha_da_plataforma()}
    # Contrato com a Canais (28.25): o desfazer acha o item pelas COLUNAS da linha (item_kind, item_ref, to_state).
    assert {(str(t["item_kind"]), str(t["to_state"])) for t in trilha.values()} == {("receita", "published")}
    assert (trilha[refs["aprovar"]]["from_state"], trilha[refs["aprovar"]]["to_state"]) == ("validated", "published")
    assert regra_do_motivo(str(trilha[refs["aprovar"]]["reason"])) == ("qa_para_aprovar", 1)
    confirmacao = trilha[refs["revisar"]]
    assert (confirmacao["from_state"], confirmacao["to_state"]) == ("published", "published")
    assert str(confirmacao["reason"]).startswith("confirmado que fica: auto:qa_revisar v1 — ")
    # As filas mostram só o que sobra para o dono.
    assert refs["aprovar"] not in {e.trail_ref for e in mundo.servico.pendentes()}
    assert {e.trail_ref for e in mundo.servico.revisar()} == {refs["instagram"]}
    assert mundo.ap.uma_volta().decididos == ()                                # nada a decidir de novo
    assert [d["item_ref"] for d in mundo.ap.relatorio()["decididos_pela_plataforma"]] == [  # type: ignore[index]
        refs["revisar"], refs["aprovar"]]


def test_o_que_o_dono_desfaz_fica_com_ele(mundo: Mundo) -> None:
    """Desfazer é a ação de sempre da pessoa: desligar (`published → disabled`; a tabela não volta a `validated`). E o
    item que a plataforma já decidiu não é decidido por ela de novo, venha de onde vier a volta à fila do dono."""
    refs = _cenario(mundo)
    mundo.cfg = AprovacaoAutomaticaCfg(modo="on")
    mundo.ap.uma_volta()
    rid = refs["aprovar"].removeprefix("receita:")
    e = mundo.servico.mudar_estado(LivroKind.RECEITA, rid, SkillState.DISABLED, by=PESSOA, reason="prefiro olhar")
    assert e.state is SkillState.DISABLED
    passos = [(t.from_state, t.to_state, t.decided_by) for t in mundo.repo.trilha(refs["aprovar"])]
    assert passos[-2:] == [(SkillState.VALIDATED, SkillState.PUBLISHED, PLATAFORMA),
                           (SkillState.PUBLISHED, SkillState.DISABLED, PESSOA)]
    mundo.db.execute("UPDATE recipes SET status='validated' WHERE id=?", (int(rid),))   # de volta à fila do dono
    assert refs["aprovar"] in {x.trail_ref for x in mundo.servico.pendentes()}
    r = mundo.ap.uma_volta()
    assert r.decididos == () and dict(r.fora)["ja_decidido_pela_plataforma"] >= 1
    assert mundo.servico.entrada(LivroKind.RECEITA, rid).state is SkillState.VALIDATED


def test_off_e_sem_lider_nada_roda(mundo: Mundo) -> None:
    _cenario(mundo)
    mundo.cfg = AprovacaoAutomaticaCfg(modo="off")
    assert mundo.laco._volta(LIDER) is None and mundo.ap.uma_volta().avaliados == 0
    mundo.cfg = AprovacaoAutomaticaCfg(modo="on")
    assert mundo.laco._volta(SEM_LIDER) is None
    assert mundo.trilha_da_plataforma() == []
    assert mundo.laco._volta(LIDER) is not None and len(mundo.trilha_da_plataforma()) == 2
    assert mundo.laco.parar(1.0) and mundo.laco._volta(LIDER) is None


@pytest.fixture
async def cliente(mundo: Mundo) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.servico)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_a_rota_de_leitura(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    refs = _cenario(mundo)
    await asyncio.to_thread(mundo.ap.uma_volta)
    r = await cliente.get("/api/aprendizado/aprovacao-automatica")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["modo"] == "shadow" and corpo["ultima_volta"]["avaliados"] == 5 and "itens" not in corpo
    corpo = (await cliente.get("/api/aprendizado/aprovacao-automatica", params={"itens": "true"})).json()
    itens = {x["item_ref"]: x for x in corpo["itens"]}
    assert itens[refs["instagram"]]["fora"] == ["app_fora_do_qa"] and itens[refs["aprovar"]]["decide"] is True


async def test_desfazer_pela_rota_e_o_contrato_da_canais(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    """O desfazer que a Canais (28.25) chama: desligar pela rota de sempre. A segunda vez é 409 `transition_forbidden`
    (o item já está `disabled`): quem chama confere o estado e trata como feito."""
    refs = _cenario(mundo)
    mundo.cfg = AprovacaoAutomaticaCfg(modo="on")
    await asyncio.to_thread(mundo.ap.uma_volta)
    rid = refs["aprovar"].removeprefix("receita:")
    corpo = {"to": "disabled", "reason": "desfeito pelo dono (decisão automática do 30.55)"}
    r = await cliente.post(f"/api/aprendizado/receita/{rid}/status", json=corpo)
    assert r.status_code == 200 and r.json()["item"]["state"] == "disabled"
    de_novo = await cliente.post(f"/api/aprendizado/receita/{rid}/status", json=corpo)
    assert de_novo.status_code == 409 and de_novo.json()["detail"]["code"] == "transition_forbidden"
    assert (await asyncio.to_thread(mundo.ap.uma_volta)).decididos == ()
