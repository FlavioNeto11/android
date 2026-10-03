"""Rótulo de intenção (item 30.25): a execução real e comprovada que a cadeia não resolveu vira uma pergunta cega à pessoa.

Prova `simulated`: banco migrado de teste, cadeia e catálogo falsos (ou a RESOLVE falsa pela composição de verdade,
`ligar_intencao.ligar`). Nada chama IA.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.db import Database
from app.modules.learning.application.apps import Declarado, VisaoPorApp
from app.modules.learning.application.intencao import CadeiaDaExecucao, ServicoDeRotulos
from app.modules.learning.application.ports import Ajustes, NovaRevisao
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import EntradaInvalida
from app.modules.learning.domain.intencao import (NENHUM, Cadeia, DossieDoRotulo, cadeia_a_rotular, conferir_escolha)
from app.modules.learning.domain.vocabulario import SignalKind
from app.modules.learning.infrastructure import ligar_intencao
from app.modules.learning.infrastructure.declarados import LojaSql
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.intencao_sql import RotulosSql
from app.modules.learning.infrastructure.montagem import GuardaDoFluxo
from app.modules.learning.infrastructure.revisoes_sql import RegistroDeRevisoesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router
from app.modules.skills.domain.intent import IntentResolution, ResolutionStatus
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository
from app.planning.decisao_fechada.intencao import EntradaDeCatalogo
from app.util import to_iso

from .conftest import Harness
from .fake_skills import ValidadorFalso
from .fake_skills import banco as banco_migrado

INICIO = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
PACOTE = "com.instagram.android"
PESSOA = "dono@painel"
CATALOGO = (EntradaDeCatalogo("curtir_post", "Curtir o post", "curte"),
            EntradaDeCatalogo("abrir_perfil", "Abrir o perfil", ""),
            EntradaDeCatalogo("fluxo:seguir", "Seguir", ""))


# ------------------------------------------------------------------ o mundo
def semear(db: Database, run_id: str, *, status: str = "completed", simulated: bool = False,
           etapas: Sequence[tuple[str, bool | None]] = (("succeeded", True),), skill_id: str | None = None,
           comando: str = "curta o último post de @fulano") -> None:
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
               " finished_at, app_ids, skill_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (run_id, run_id, comando, "execute", status, int(simulated), json.dumps(["android-01"]),
                to_iso(INICIO), to_iso(INICIO + timedelta(minutes=3)), json.dumps(["instagram"]), skill_id))
    objetivo = f"{run_id}:android-01"
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status) VALUES (?,?,?,?)",
               (objetivo, run_id, "android-01", "succeeded"))
    for seq, (st, verificada) in enumerate(etapas):
        resultado = None if verificada is None else json.dumps({"verified": verificada})
        db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                   " postcondition, timeout_s, max_attempts, status, result) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (f"{objetivo}:v1:e{seq}", run_id, objetivo, "android-01", 1, seq, f"e{seq}", "t", "g", "{}", 60, 3,
                    st, resultado))


class SemDeclarados:
    def declarados(self) -> list[Declarado]:
        return []


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.agora = INICIO
        self.catalogo: list[EntradaDeCatalogo] = list(CATALOGO)
        self.resolucao = IntentResolution(ResolutionStatus.NO_MATCH)
        self.resolvidos: list[str] = []
        self.lidos: list[str] = []
        repo = SqlLearningRepository(db, clock=lambda: to_iso(self.agora),
                                     guarda_do_fluxo=GuardaDoFluxo(db, SqlSkillRepository(db, ValidadorFalso())),
                                     precos=dict)
        self.servico = LearningService(repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes,
                                       relogio=lambda: self.agora, retencao_de_logs_dias=lambda: 14)
        self.servico.anexar(VisaoPorApp(self.servico, SemDeclarados(), LojaSql(db)))   # o nome do app, pela loja
        self.rotulos = ligar_intencao.ligar(self.servico, db, dados=self._dados, resolver=self._resolver,
                                            catalogo=lambda: self.catalogo, relogio=lambda: self.agora)

    def _dados(self, run_id: str) -> tuple[str, list[str | None], str | None] | None:
        """O papel do `RunService.dados_da_intencao`: comando sem destinos, personas e app."""
        self.lidos.append(run_id)
        r = self.db.one("SELECT command FROM runs WHERE id=?", (run_id,))
        return None if r is None else ("curta o último post de <destino>", ["p1"], PACOTE)

    def _resolver(self, comando: str, perfis: Sequence[str | None]) -> IntentResolution:
        self.resolvidos.append(comando)
        return self.resolucao

    def linhas(self) -> list[dict[str, object]]:
        return [dict(r) for r in self.db.query("SELECT * FROM learning_reviews ORDER BY created_at, id")]

    def sinais(self) -> list[dict[str, object]]:
        return [dict(r) for r in self.db.query("SELECT * FROM learning_signals WHERE kind=?",
                                               (SignalKind.PARECER_DECIDIDO.value,))]


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "rotulo.sqlite3")
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


# ------------------------------------------------------------------ domínio
@pytest.mark.parametrize(("resolvida", "sem_casamento", "empatados", "esperado"), [
    (None, True, (), Cadeia.SEM_CASAMENTO),
    (None, False, ("a", "b"), Cadeia.EMPATE),
    ("a", False, ("a", "b"), None),            # resolveu depois do empate: nada a perguntar
    ("a", False, (), None),
    (None, False, ("a",), None),               # um "empatado" só não é empate
    (None, False, ("a", "a"), None),
])
def test_so_a_cadeia_que_nao_resolveu_vira_pergunta(resolvida: str | None, sem_casamento: bool,
                                                     empatados: tuple[str, ...], esperado: Cadeia | None) -> None:
    assert cadeia_a_rotular(resolvida=resolvida, sem_casamento=sem_casamento, empatados=empatados) is esperado


def test_o_dossie_e_canonico_e_so_guarda_ids_do_catalogo() -> None:
    a = DossieDoRotulo.montar(Cadeia.EMPATE, ["b", "a", "c", "a"], ["c", "fora", "a"])
    b = DossieDoRotulo.montar(Cadeia.EMPATE, ["c", "a", "b"], ["a", "c"])
    assert a == b and a.dossie_hash == b.dossie_hash
    assert a.como_dados() == {"cadeia": "empate", "candidatos": ["a", "b", "c"], "empatados": ["a", "c"]}
    assert DossieDoRotulo.de_dados(a.como_dados()) == a
    assert DossieDoRotulo.de_dados({"cadeia": "outra"}) is None
    assert "empatados" not in DossieDoRotulo.montar(Cadeia.SEM_CASAMENTO, ["a"]).como_dados()


def test_a_resposta_e_um_candidato_gravado_ou_nenhum() -> None:
    d = DossieDoRotulo.montar(Cadeia.SEM_CASAMENTO, ["a", "b"])
    assert conferir_escolha(" a ", d) == "a"
    assert conferir_escolha(NENHUM, d) == NENHUM
    with pytest.raises(EntradaInvalida):
        conferir_escolha("c", d)


# ------------------------------------------------------------------ o minerador (pela composição de verdade)
def test_execucao_real_comprovada_sem_casamento_abre_uma_pergunta_sem_texto(mundo: Mundo) -> None:
    semear(mundo.db, "r1", etapas=(("succeeded", True), ("succeeded", True)))
    mundo.servico.digerir_execucao("r1")
    [linha] = mundo.linhas()
    assert {k: linha[k] for k in ("item_ref", "item_kind", "scope_app", "gatilho", "template_id", "template_versao",
                                  "provedor", "modelo", "simulated", "usd", "saida", "validade", "classe_de_risco",
                                  "decisao_final")} == {
        "item_ref": "run:r1", "item_kind": "execucao", "scope_app": PACOTE, "gatilho": "execucao_sem_intencao",
        "template_id": "intencao", "template_versao": "rotulo-v1", "provedor": "", "modelo": "", "simulated": 0,
        "usd": 0.0, "saida": None, "validade": "ok", "classe_de_risco": None, "decisao_final": None}
    assert json.loads(str(linha["dossie"])) == {"cadeia": "sem_casamento",
                                                "candidatos": ["abrir_perfil", "curtir_post", "fluxo:seguir"]}
    assert "fulano" not in str(linha["dossie"]) and "destino" not in str(linha["dossie"])
    assert mundo.resolvidos == ["curta o último post de <destino>"]       # o comando SEM destinos


@pytest.mark.parametrize("caso", ["simulada", "com_problemas", "falhou", "cancelada", "a_mao", "incerta", "pulada",
                                  "sem_etapas", "sem_prova", "casada_no_plano"])
def test_so_sucesso_real_comprovado_entra_e_a_cadeia_nem_e_consultada(mundo: Mundo, caso: str) -> None:
    casos: dict[str, dict[str, object]] = {
        "simulada": {"simulated": True},
        "com_problemas": {"status": "completed_with_issues"},
        "falhou": {"status": "failed", "etapas": (("failed", False),)},
        "cancelada": {"status": "cancelled"},
        "a_mao": {"etapas": (("succeeded", True), ("succeeded", False))},     # `confirm_done` grava verified=false
        "incerta": {"etapas": (("succeeded", True), ("uncertain", None))},
        "pulada": {"etapas": (("succeeded", True), ("skipped", None))},
        "sem_etapas": {"etapas": ()},
        "sem_prova": {"etapas": (("succeeded", None),)},
        "casada_no_plano": {"skill_id": "curtir_post"},
    }
    semear(mundo.db, "r1", **casos[caso])  # type: ignore[arg-type]
    mundo.servico.digerir_execucao("r1")
    assert mundo.linhas() == [] and mundo.lidos == [] and mundo.resolvidos == []


def test_cadeia_que_resolveu_execucao_sumida_ou_catalogo_vazio_nao_perguntam(mundo: Mundo) -> None:
    semear(mundo.db, "r1")
    mundo.catalogo = []
    mundo.servico.digerir_execucao("r1")                 # sem catálogo a resposta só podia ser `nenhum`
    mundo.servico.digerir_execucao("nao-existe")
    assert mundo.linhas() == []


def test_uma_pergunta_por_execucao_mesmo_com_o_catalogo_mudado(mundo: Mundo) -> None:
    semear(mundo.db, "r1")
    mundo.servico.digerir_execucao("r1")
    mundo.catalogo.append(EntradaDeCatalogo("nova", "Nova", ""))
    mundo.agora += timedelta(minutes=5)
    mundo.servico.digerir_execucao("r1")
    assert len(mundo.linhas()) == 1


def test_empate_guarda_o_catalogo_inteiro_e_os_empatados_a_parte(mundo: Mundo) -> None:
    servico = ServicoDeRotulos(mundo.servico, RotulosSql(mundo.db),
                               cadeia=lambda _: CadeiaDaExecucao(None, False, ("curtir_post", "abrir_perfil"), PACOTE),
                               catalogo=lambda: [(e.skill_id, e.nome) for e in CATALOGO], relogio=lambda: mundo.agora)
    semear(mundo.db, "r1")
    assert servico.abrir("r1") == 1
    assert json.loads(str(mundo.linhas()[0]["dossie"])) == {
        "cadeia": "empate", "candidatos": ["abrir_perfil", "curtir_post", "fluxo:seguir"],
        "empatados": ["abrir_perfil", "curtir_post"]}


# ------------------------------------------------------------------ o curador não vê o rótulo
def _do_curador(db: Database, item_ref: str, agora: datetime) -> str:
    rid = RegistroDeRevisoesSql(db).gravar(NovaRevisao(
        item_ref=item_ref, item_kind="licao", scope_app=PACOTE, gatilho="a_revisar", dossie_hash="h-curador",
        dossie={"item": {"estado": "candidate"}}, template_id="curador", template_versao="dossie-v1", provedor="teste",
        modelo="m", simulated=False, validade="ok", saida={"decisao": "aprovar", "evidencias_citadas": []},
        classe_de_risco="B", politica="p"), agora)
    assert rid is not None
    return rid


def _de_intencao(db: Database, item_ref: str, agora: datetime, *, provedor: str) -> str:
    """Uma linha `intencao` forjada com o MESMO item do curador e provedor não vazio: só o filtro do template a separa."""
    rid = RegistroDeRevisoesSql(db).gravar(NovaRevisao(
        item_ref=item_ref, item_kind="execucao", scope_app=PACOTE, gatilho="execucao_sem_intencao",
        dossie_hash="h-intencao", dossie={"cadeia": "sem_casamento", "candidatos": ["a"]}, template_id="intencao",
        template_versao="rotulo-v1", provedor=provedor, modelo="", simulated=False, validade="ok", saida=None,
        classe_de_risco=None, politica=None), agora)
    assert rid is not None
    return rid


def test_os_leitores_do_curador_ignoram_o_rotulo_de_intencao(db: Database) -> None:
    registro = RegistroDeRevisoesSql(db)
    intencao = _de_intencao(db, "li-1", INICIO, provedor="teste")
    assert registro.existe("li-1", "h-intencao") is False
    assert registro.ultima("li-1") is None
    assert registro.uma(intencao) is None
    assert registro.do_item("li-1", 5) == [] and registro.sem_decisao(["li-1"]) == {}
    assert registro.do_dossie("li-1", "h-intencao") is None
    assert registro.decidir(intencao, decisao_final="aceitou", decidido_por=PESSOA, transicao_id=None,
                            override=False, override_motivo=None) is False
    janela = registro.janela(INICIO + timedelta(minutes=1), 7)
    assert janela.tamanhos_sem_medida == () and janela.revisoes_de_hoje == 0     # fora de C_W
    curador = _do_curador(db, "li-1", INICIO)
    assert [r.id for r in registro.do_item("li-1", 5)] == [curador]
    assert registro.janela(INICIO + timedelta(minutes=1), 7).revisoes_de_hoje == 1


def test_o_rotulo_de_usd_zero_e_provedor_vazio_nao_entra_na_janela(mundo: Mundo) -> None:
    semear(mundo.db, "r1")
    mundo.servico.digerir_execucao("r1")
    janela = RegistroDeRevisoesSql(mundo.db).janela(INICIO + timedelta(minutes=1), 7)
    assert (janela.custos_medidos, janela.tamanhos_sem_medida, janela.revisoes_de_hoje) == ((), (), 0)


# ------------------------------------------------------------------ o painel e o gesto
async def test_a_pessoa_ve_a_pergunta_e_responde_uma_vez(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    semear(mundo.db, "r1")
    semear(mundo.db, "r2", comando="siga @beltrano")
    mundo.servico.digerir_execucao("r1")
    mundo.agora += timedelta(minutes=1)
    mundo.servico.digerir_execucao("r2")
    r = await cliente.get("/api/aprendizado/intencao")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["total"] == 2 and [i["run_id"] for i in corpo["itens"]] == ["r2", "r1"]
    item = corpo["itens"][1]
    assert item["comando"] == "curta o último post de @fulano" and item["cadeia"] == "sem_casamento"
    assert item["app"] == PACOTE and item["app_nome"] == "Instagram"
    assert item["candidatos"][1] == {"skill_id": "curtir_post", "nome": "Curtir o post"}
    assert item["terminou_em"] is not None and item["decisao_final"] is None

    r = await cliente.post("/api/aprendizado/execucao/r1/intencao", json={"escolha": "curtir_post"})
    assert r.status_code == 200 and r.json()["decisao_final"] == "curtir_post"
    [sinal] = mundo.sinais()
    dados = json.loads(str(sinal["data"]))
    assert sinal["source_ref"] == f"parecer:{item['review_id']}" and sinal["run_id"] == "r1"
    assert sinal["app_package"] == PACOTE and sinal["polarity"] == "neutral"
    assert dados == {"review_id": item["review_id"], "item_ref": "run:r1", "decisao_final": "curtir_post",
                     "override": False, "viu": False, "template_id": "intencao"}
    assert (await cliente.get("/api/aprendizado/intencao")).json()["total"] == 1

    r = await cliente.post("/api/aprendizado/execucao/r1/intencao", json={"escolha": NENHUM})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "state_conflict"
    assert len(mundo.sinais()) == 1


async def test_nenhum_vale_e_fora_do_catalogo_gravado_nao(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    semear(mundo.db, "r1")
    mundo.servico.digerir_execucao("r1")
    mundo.catalogo.append(EntradaDeCatalogo("nova", "Nova", ""))           # entrou depois: não é candidato
    r = await cliente.post("/api/aprendizado/execucao/r1/intencao", json={"escolha": "nova"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid"
    assert mundo.sinais() == [] and mundo.linhas()[0]["decisao_final"] is None
    r = await cliente.post("/api/aprendizado/execucao/r1/intencao", json={"escolha": NENHUM})
    assert r.status_code == 200 and mundo.linhas()[0]["decisao_final"] == NENHUM
    assert (await cliente.post("/api/aprendizado/execucao/zzz/intencao", json={"escolha": NENHUM})).status_code == 404
    assert (await cliente.post("/api/aprendizado/execucao/r1/intencao", json={"escolha": ""})).status_code == 422


async def test_sem_composicao_a_rota_responde_503(db: Database) -> None:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/aprendizado/intencao")).status_code == 503


# ------------------------------------------------------------------ a composição do AppState (núcleo, suíte 7)
async def test_o_appstate_liga_o_rotulo_e_a_execucao_simulada_nao_pergunta(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    rotulos = st.learning.extensao(ServicoDeRotulos)
    assert rotulos is not None
    run = harness.run(["android-01"])
    await harness.wait_run(run.id)
    st.learning.digerir_execucao(run.id)
    assert st.db.scalar("SELECT COUNT(*) FROM learning_reviews WHERE template_id='intencao'") == 0
    # A cadeia composta (o acessor do RunService e a RESOLVE de verdade) responde sobre uma execução real do harness.
    cadeia = rotulos._cadeia(run.id)  # noqa: SLF001 - a peça que o state.py compõe
    assert isinstance(cadeia, CadeiaDaExecucao)
