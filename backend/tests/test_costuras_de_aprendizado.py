"""Costuras do aprendizado nos arquivos quentes (ADR-054, pacote A2; `app/taskqueue/costuras.py`).

O que se prova, com o harness (aparelhos falsos, provedor simulado: toda prova aqui é `simulated`):
- com as costuras no-op, com costuras que lançam e com o livro ligado, os desfechos ficam idênticos — o aprendizado
  nunca decide nada e nunca derruba a operação;
- `licoes_para` é pedida UMA vez por tentativa, na primeira consulta ao ator, e nunca na etapa que a receita conduz;
  a do planejador, uma vez por planejamento; as lições chegam a `DecisionRequest.lessons` e `PlanRequest.lessons`,
  nunca ao verificador (`StepContext` não tem o campo);
- `ao_fechar_tentativa` chega uma vez por tentativa, também quando a etapa sai por exceção, e leva a árvore;
- os gestos viram sinais do livro: `ao_resolver` com a nota (redigida; a que parece credencial não é gravada),
  `repetiu_execucao`, `tomou_controle` só com a IA numa etapa e sem árvore, texto nem coordenada, e
  `respondeu_pergunta` com o campo e o sha256 — nunca o valor;
- os três gestos que faltavam escritor: `cancelou_execucao` só pela rota (a sucessora que cancela a execução
  respondida não conta), `comando_incerto_resolvido` pela rota dos comandos e `correcao_de_ensino` pelo ensino — com
  o operador da SESSÃO como autor (nunca o `requested_by` do corpo), a nota pela triagem de credencial e a falha do
  livro sem derrubar o gesto;
- pela rota, os gestos do A2 (resolver o item, repetir, responder, tomar o controle) também levam o operador da sessão
  (sem sessão, `panel`), e o mesmo evento feito de novo por outra pessoa não vira segunda linha (plano 22.1); a nota
  da resolução pelo painel é só o texto da pessoa, e o `requested_by` passa pela triagem (plano 22.2);
- `aprendizado.enabled: false` desliga tudo, e o modo `off` das lições não pede lição a ninguém.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import fields
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.automation.hierarchy import UiTree
from app.main import create_app
from app.models import CommandState, ControlOwner, InstanceCurrent, ResolveBody
from app.modules.learning.infrastructure.ligar_costuras import extensoes
from app.modules.skills.domain.lifecycle import SYSTEM_ACTOR
from app.planning.provider import DecisionRequest, PlanRequest, StepContext, VerifyRequest
from app.shared import costuras as do_kernel
from app.taskqueue import costuras as da_fila
from app.taskqueue.assistente import ComandoAssistido, RunSuccessorBody
from app.taskqueue.costuras import (SEM_COSTURAS, CancelamentoDeExecucao, CorrecaoDeEnsino, FechamentoDeTentativa,
                                    PedidoDeLicoes, RepeticaoDeExecucao, ResolucaoDeComando, ResolucaoDeItem,
                                    RespostaAPergunta, TomadaDeControle, autor_do_gesto, avisar, pedir_licoes)
from app.util import parse_iso

from .conftest import COMMAND, Harness

PKG = "com.pocqa.messenger"
NOME = "Ana Ribeiro"
HABILIDADE = "qa.abrir_conversa"
INCOMPLETO = "Abra o QA Messenger e envie uma mensagem"
LICAO_DO_ATOR = "Em abrir_conversa: o toque em [row_x] não achou o alvo; o que comprovou foi tocar em [row_y]."
LICAO_DO_PLANO = "Em com.pocqa.messenger: não use a pós-condição texto_na_tela em abrir_conversa."


class Espia:
    """Costuras que registram o que recebem e devolvem lições fixas."""

    def __init__(self, ator: tuple[str, ...] = (), plano: tuple[str, ...] = ()) -> None:
        self.ator, self.plano = ator, plano
        self.pedidos: list[PedidoDeLicoes] = []
        self.fechamentos: list[FechamentoDeTentativa] = []
        self.resolucoes: list[ResolucaoDeItem] = []
        self.repeticoes: list[RepeticaoDeExecucao] = []
        self.respostas: list[RespostaAPergunta] = []
        self.tomadas: list[TomadaDeControle] = []
        self.cancelamentos: list[CancelamentoDeExecucao] = []
        self.comandos: list[ResolucaoDeComando] = []
        self.correcoes: list[CorrecaoDeEnsino] = []

    def licoes_para(self, pedido: PedidoDeLicoes) -> list[str]:
        self.pedidos.append(pedido)
        return list(self.ator if pedido.papel == "actor" else self.plano)

    def cancelou_execucao(self, cancelamento: CancelamentoDeExecucao) -> None:
        self.cancelamentos.append(cancelamento)

    def comando_incerto_resolvido(self, resolucao: ResolucaoDeComando) -> None:
        self.comandos.append(resolucao)

    def correcao_de_ensino(self, correcao: CorrecaoDeEnsino) -> None:
        self.correcoes.append(correcao)

    def ao_fechar_tentativa(self, fechamento: FechamentoDeTentativa) -> None:
        self.fechamentos.append(fechamento)

    def ao_resolver(self, resolucao: ResolucaoDeItem) -> None:
        self.resolucoes.append(resolucao)

    def ao_repetir(self, repeticao: RepeticaoDeExecucao) -> None:
        self.repeticoes.append(repeticao)

    def respondeu_pergunta(self, resposta: RespostaAPergunta) -> None:
        self.respostas.append(resposta)

    def tomou_controle(self, tomada: TomadaDeControle) -> None:
        self.tomadas.append(tomada)


class Explode:
    """Costuras quebradas: toda chamada lança. Nada da operação pode depender delas."""

    def licoes_para(self, pedido: PedidoDeLicoes) -> list[str]:
        raise RuntimeError("costura quebrada")

    def ao_fechar_tentativa(self, fechamento: FechamentoDeTentativa) -> None:
        raise RuntimeError("costura quebrada")

    def ao_resolver(self, resolucao: ResolucaoDeItem) -> None:
        raise RuntimeError("costura quebrada")

    def ao_repetir(self, repeticao: RepeticaoDeExecucao) -> None:
        raise RuntimeError("costura quebrada")

    def respondeu_pergunta(self, resposta: RespostaAPergunta) -> None:
        raise RuntimeError("costura quebrada")

    def tomou_controle(self, tomada: TomadaDeControle) -> None:
        raise RuntimeError("costura quebrada")

    def cancelou_execucao(self, cancelamento: CancelamentoDeExecucao) -> None:
        raise RuntimeError("costura quebrada")

    def comando_incerto_resolvido(self, resolucao: ResolucaoDeComando) -> None:
        raise RuntimeError("costura quebrada")

    def correcao_de_ensino(self, correcao: CorrecaoDeEnsino) -> None:
        raise RuntimeError("costura quebrada")


def _instalar(h: Harness, costuras: Any) -> None:
    """Pendura as costuras onde o `AppState` as pendura — inclusive `state.costuras`, que a rota de comandos lê."""
    assert h.state is not None
    h.state.scheduler.executor.costuras = costuras
    h.state.runs.costuras = costuras
    h.state.devices.costura_de_controle = costuras
    h.state.teaching.costura_de_ensino = costuras
    h.state.costuras = costuras


def _sinais(h: Harness, kind: str) -> list[dict[str, Any]]:
    assert h.state is not None
    return [dict(r) for r in h.state.db.query("SELECT * FROM learning_signals WHERE kind=? ORDER BY id", (kind,))]


def _pedido(**kw: Any) -> PedidoDeLicoes:
    base: dict[str, Any] = dict(papel="actor", unidade="step:s1", run_id="r1", app_package=PKG, capability="*",
                                step_hash="h1", simulated=True)
    return PedidoDeLicoes(**{**base, **kw})


# ---------------------------------------------------------------------------------------------------- contrato
def test_o_padrao_e_no_op_e_a_licao_nunca_chega_ao_verificador() -> None:
    assert SEM_COSTURAS.licoes_para(_pedido()) == []
    assert SEM_COSTURAS.ao_resolver(ResolucaoDeItem("r1", "o1", "retry", "1.1", None, None)) is None
    assert SEM_COSTURAS.tomou_controle(TomadaDeControle("android-01", "r1", None, "s1")) is None
    # os campos novos nascem vazios: sem lição, o pedido ao provedor é o de antes
    padrao = {f.name: f for f in fields(DecisionRequest)}["lessons"].default_factory  # type: ignore[misc]
    assert padrao() == []
    padrao = {f.name: f for f in fields(PlanRequest)}["lessons"].default_factory     # type: ignore[misc]
    assert padrao() == []
    # o verificador recebe o MESMO StepContext do ator: por construção, a lição não tem onde entrar nele
    assert "lessons" not in {f.name for f in fields(StepContext)}
    assert "lessons" not in {f.name for f in fields(VerifyRequest)}


def test_as_costuras_de_gesto_moram_no_kernel_e_a_fila_as_reexporta() -> None:
    # compatibilidade: quem importava da fila recebe os MESMOS objetos do kernel
    for nome in ("TomadaDeControle", "CosturaDeControle", "CosturaDeComando", "CosturaDeEnsino", "ResolucaoDeComando",
                 "CorrecaoDeEnsino", "autor_do_gesto", "avisar"):
        assert getattr(da_fila, nome) is getattr(do_kernel, nome), nome
    # o no-op da fila herda o de gesto: `SEM_COSTURAS` cumpre as duas portas
    assert isinstance(SEM_COSTURAS, do_kernel.SemCosturasDeGesto)
    assert SEM_COSTURAS.cancelou_execucao(CancelamentoDeExecucao("r1", "running", False, "panel",
                                                                 "2026-09-29T10:00:00.000Z")) is None
    for padrao in (SEM_COSTURAS, do_kernel.SEM_COSTURAS_DE_GESTO):
        assert padrao.comando_incerto_resolvido(ResolucaoDeComando("c1", "failed", None, "panel", False)) is None
        assert padrao.correcao_de_ensino(CorrecaoDeEnsino("ens-1", 1, "qa.x", "r1", "s1", "nota", None)) is None
        assert padrao.tomou_controle(TomadaDeControle("android-01", "r1", None, "s1")) is None


def test_autor_do_gesto_e_a_regra_do_livro() -> None:
    assert do_kernel.SISTEMA == SYSTEM_ACTOR                  # repetido no kernel, que não vê as habilidades
    assert autor_do_gesto(None) == autor_do_gesto("") == autor_do_gesto("   ") == autor_do_gesto(42) == "panel"
    assert autor_do_gesto(NOME) == NOME
    assert autor_do_gesto(SYSTEM_ACTOR) == f"painel:{SYSTEM_ACTOR}"   # pela rota decide sempre uma pessoa
    assert autor_do_gesto(autor_do_gesto(SYSTEM_ACTOR)) == f"painel:{SYSTEM_ACTOR}"


def test_costura_que_lanca_vira_nenhuma_licao_e_nenhum_aviso() -> None:
    assert pedir_licoes(Explode(), _pedido()) == []
    avisar(Explode().ao_resolver, ResolucaoDeItem("r1", "o1", "retry", "1.1", None, None))    # não lança
    assert pedir_licoes(Espia(ator=("  ", LICAO_DO_ATOR, "")), _pedido()) == [LICAO_DO_ATOR]


# ---------------------------------------------------------------------------------------------------- desfechos
def _retrato(h: Harness, run_id: str) -> dict[str, Any]:
    """O que a execução DECIDIU, sem ids nem horários: status, etapas, tentativas, tipos de falha e mensagens."""
    assert h.state is not None
    db = h.state.db
    return {
        "run": db.scalar("SELECT status FROM runs WHERE id=?", (run_id,)),
        "objetivos": sorted((r["instance_id"], r["status"]) for r in db.query(
            "SELECT instance_id, status FROM objectives WHERE run_id=?", (run_id,))),
        "etapas": sorted((r["instance_id"], r["key"], r["plan_version"], r["status"], r["failure_kind"], r["attempts"])
                         for r in db.query("SELECT * FROM steps WHERE run_id=?", (run_id,))),
        "tentativas": sorted((r["instance_id"], r["key"], r["number"], r["status"], r["failure_kind"])
                             for r in db.query("SELECT s.instance_id, s.key, a.number, a.status, a.failure_kind"
                                               " FROM attempts a JOIN steps s ON s.id=a.step_id WHERE s.run_id=?",
                                               (run_id,))),
        "mensagens": {iid: [m.body.replace(run_id, "<run>") for m in f.messages] for iid, f in h.fakes.items()},
    }


@pytest.mark.parametrize("quais", ["explode", "livro"])
async def test_com_costuras_no_op_quebradas_ou_ligadas_os_desfechos_ficam_identicos(tmp_path: Path,
                                                                                    quais: str) -> None:
    async def cenario(pasta: str, costuras: Any) -> dict[str, Any]:
        (tmp_path / pasta).mkdir()
        h = Harness(tmp_path / pasta, 3)
        await h.boot()
        h.pular_o_tempo()   # T.2: os dois cenários com o mesmo relógio virtual; o que se compara é o desfecho
        try:
            if costuras is not None:
                _instalar(h, costuras)
            h.fakes["android-02"].require_login = True          # um item para na pessoa, os outros comprovam
            run = h.run(["android-01", "android-02", "android-03"])
            await h.wait_run(run.id)
            return _retrato(h, run.id)
        finally:
            assert h.state is not None
            await h.state.stop()

    base = await cenario("sem", SEM_COSTURAS)
    outro = await cenario(quais, Explode() if quais == "explode" else None)   # None: o livro que o AppState liga
    assert base["run"] == "completed_with_issues"
    assert outro == base


# ---------------------------------------------------------------------------------------------------- lições
async def test_licoes_do_ator_uma_vez_por_tentativa_e_nunca_na_etapa_da_receita(harness: Harness) -> None:
    harness.pular_o_tempo()   # T.2: o assentamento das ferramentas não muda quantas lições cada tentativa recebe
    st = harness.state
    assert st is not None
    harness.cfg.file.ai.recipes = "replay"
    espia = Espia(ator=(LICAO_DO_ATOR,), plano=(LICAO_DO_PLANO,))
    _instalar(harness, espia)
    decisoes: list[tuple[str, list[str]]] = []
    planos: list[list[str]] = []
    decide, plan = harness.ai.decide, harness.ai.plan

    async def decide_espiado(req: DecisionRequest) -> Any:
        decisoes.append((req.ctx.instance_id, list(req.lessons)))
        return await decide(req)

    async def plan_espiado(req: PlanRequest) -> Any:
        planos.append(list(req.lessons))
        return await plan(req)

    harness.ai.decide = decide_espiado          # type: ignore[method-assign]
    harness.ai.plan = plan_espiado              # type: ignore[method-assign]

    r1 = await harness.wait_run(harness.run(["android-01"]).id)              # aprende: a IA conduz tudo
    assert r1.status == "completed"
    pedidos_ator = [p for p in espia.pedidos if p.papel == "actor"]
    por_tentativa = Counter(p.attempt_id for p in pedidos_ator)
    assert por_tentativa and set(por_tentativa.values()) == {1}              # UMA vez por tentativa
    com_ia = {r["attempt_id"] for r in st.db.query(
        "SELECT DISTINCT a.attempt_id FROM actions a JOIN attempts t ON t.id=a.attempt_id JOIN steps s"
        " ON s.id=t.step_id WHERE s.run_id=? AND a.source='ai'", (r1.id,))}
    assert set(por_tentativa) == com_ia                                     # só onde o ator foi consultado
    for p in pedidos_ator:
        etapa = st.db.one("SELECT s.id, s.template_hash FROM steps s JOIN attempts a ON a.step_id=s.id WHERE a.id=?",
                          (p.attempt_id,))
        assert etapa is not None
        assert (p.unidade, p.step_id, p.step_hash) == (f"step:{etapa['id']}", etapa["id"], etapa["template_hash"])
        assert (p.app_package, p.run_id, p.simulated) == (PKG, r1.id, True)
    assert decisoes and all(licoes == [LICAO_DO_ATOR] for _, licoes in decisoes)
    # o planejador: um pedido por planejamento, e as lições dele no PlanRequest
    pedidos_plano = [p for p in espia.pedidos if p.papel == "planner"]
    assert [(p.unidade, p.app_package) for p in pedidos_plano] == [(f"plan:{r1.id}", PKG)]
    assert planos == [[LICAO_DO_PLANO]]

    espia.pedidos.clear()
    r2 = await harness.wait_run(harness.run(["android-02"]).id)              # repete: as receitas conduzem
    assert r2.status == "completed"
    da_receita = {r["id"] for r in st.db.query(
        "SELECT a.id FROM attempts a JOIN steps s ON s.id=a.step_id WHERE s.run_id=? AND s.driven_by='recipe'",
        (r2.id,))}
    assert da_receita, "o cenário precisa de etapas conduzidas pela receita"
    assert not {p.attempt_id for p in espia.pedidos if p.papel == "actor"} & da_receita


# ---------------------------------------------------------------------------------------------------- fechamento
async def test_ao_fechar_tentativa_uma_vez_por_tentativa_e_leva_a_arvore(harness: Harness) -> None:
    harness.pular_o_tempo()   # T.2: sem assentamento em tempo real
    st = harness.state
    assert st is not None
    espia = Espia()
    _instalar(harness, espia)
    harness.fakes["android-02"].require_login = True
    run = harness.run(["android-01", "android-02"])
    await harness.wait_run(run.id)
    tentativas = {r["id"]: r for r in st.db.query(
        "SELECT a.id, a.status, s.instance_id, s.id AS step_id FROM attempts a JOIN steps s ON s.id=a.step_id"
        " WHERE s.run_id=?", (run.id,))}
    assert Counter(f.attempt_id for f in espia.fechamentos) == Counter(list(tentativas))   # cada uma, uma vez
    for f in espia.fechamentos:
        t = tentativas[f.attempt_id]
        assert (f.step_id, f.instance_id, f.run_id, f.simulated) == (t["step_id"], t["instance_id"], run.id, True)
        assert f.app_package == PKG and isinstance(f.arvore, UiTree) and not f.loja
        if t["status"] == "succeeded":
            assert f.status == "succeeded" and f.verified
        else:
            assert not f.verified
    parada = [f for f in espia.fechamentos if f.instance_id == "android-02" and f.status == "waiting_user"]
    assert len(parada) == 1


async def test_ao_fechar_tentativa_tambem_quando_a_etapa_sai_por_excecao(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    espia = Espia()
    _instalar(harness, espia)

    async def quebra(**_: Any) -> Any:
        raise RuntimeError("defeito interno de teste")

    st.scheduler.executor._run_step = quebra     # type: ignore[method-assign]  # noqa: SLF001
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id)
    assert detail.status in ("failed", "completed_with_issues")
    tentativas = [r["id"] for r in st.db.query(
        "SELECT a.id FROM attempts a JOIN steps s ON s.id=a.step_id WHERE s.run_id=?", (run.id,))]
    assert tentativas and Counter(f.attempt_id for f in espia.fechamentos) == Counter(tentativas)
    assert {(f.status, f.verified) for f in espia.fechamentos} == {("erro", False)}


# ---------------------------------------------------------------------------------------------------- gestos
async def test_ao_resolver_grava_o_sinal_com_a_nota_e_cada_gesto_e_uma_linha(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    harness.fakes["android-02"].require_login = True
    harness.fakes["android-03"].require_login = True
    run = harness.run(["android-02", "android-03"])
    detail = await harness.wait_run(run.id)
    by = {o.instance_id: o for o in detail.objectives}
    oid = by["android-02"].id
    assert by["android-02"].status == "waiting_user"

    def parada(objetivo: str) -> dict[str, Any]:
        linha = st.db.one("SELECT s.id, s.seq, s.plan_version FROM steps s JOIN objectives o ON o.id=s.objective_id"
                          " AND o.plan_version=s.plan_version WHERE s.objective_id=? AND s.status='waiting_user'",
                          (objetivo,))
        assert linha is not None
        return dict(linha)

    e1 = parada(oid)
    st.runs.resolve(run.id, oid, ResolveBody(resolution="retry", note="loguei no aparelho"))
    [s1] = _sinais(harness, "repetiu_item")
    assert s1["source_ref"] == f"resolve:{oid}:{e1['plan_version']}.{e1['seq']}"
    assert (s1["created_by"], s1["polarity"], s1["note"], s1["note_refused"]) == ("panel", "neutral",
                                                                                  "loguei no aparelho", 0)
    assert (s1["run_id"], s1["objective_id"], s1["step_id"], s1["instance_id"]) == (run.id, oid, e1["id"],
                                                                                    "android-02")
    assert (s1["app_package"], s1["simulated"], s1["step_verified"]) == (PKG, 1, 0)
    assert s1["capability"]                                                    # a ação da etapa, ou '*' se livre

    # o login continua pedido: o item para de novo, noutra versão do plano — o segundo gesto não é engolido
    await harness.wait(lambda: st.repo.objective_row(oid)["status"] == "waiting_user", what="parar de novo")
    e2 = parada(oid)
    assert e2["plan_version"] == e1["plan_version"] + 1
    st.runs.resolve(run.id, oid, ResolveBody(resolution="abandon", note="senha: segredo123"))
    [s2] = _sinais(harness, "abandonou_item")
    assert s2["source_ref"] == f"resolve:{oid}:{e2['plan_version']}.{e2['seq']}"
    assert s2["polarity"] == "negative"
    assert s2["note"] is None and s2["note_refused"] == 1                       # parecia credencial: não fica
    assert st.db.scalar("SELECT COUNT(*) FROM learning_signals WHERE objective_id=?", (oid,)) == 2

    # confirmar à mão: positivo para a ação, negativo para a verificação — neutro, nunca evidência a favor
    e3 = parada(by["android-03"].id)
    st.runs.resolve(run.id, by["android-03"].id, ResolveBody(resolution="confirm_done", note="abri na mão"))
    [s3] = _sinais(harness, "confirmou_a_mao")
    assert (s3["polarity"], s3["step_id"], s3["step_verified"], s3["note"]) == ("neutral", e3["id"], 0,
                                                                               "abri na mão")


async def test_repetir_itens_grava_um_sinal_por_gesto_e_nenhum_quando_nada_foi_retomado(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    harness.fakes["android-01"].require_login = True
    run = harness.run(["android-01"])
    await harness.wait_run(run.id)
    oid = f"{run.id}:android-01"
    assert st.runs.retry_failed(run.id)["retried"] == [oid]
    [s1] = _sinais(harness, "repetiu_execucao")
    assert s1["run_id"] == run.id and s1["created_by"] == "panel" and s1["polarity"] == "neutral"
    assert json.loads(s1["data"]) == {"itens": 1} and s1["simulated"] == 1
    assert st.runs.retry_failed(run.id)["retried"] == []                        # já voltou à fila: nada retomado
    assert len(_sinais(harness, "repetiu_execucao")) == 1
    await harness.wait(lambda: st.repo.objective_row(oid)["status"] == "waiting_user", what="parar de novo")
    assert st.runs.retry_failed(run.id)["retried"] == [oid]
    assert len({s["source_ref"] for s in _sinais(harness, "repetiu_execucao")}) == 2


def _ia_numa_etapa(h: Harness, iid: str, run_id: str, step_id: str) -> Any:
    """O aparelho como o escalonador o deixa com a IA conduzindo uma etapa (sem rodar execução nenhuma)."""
    assert h.state is not None
    rt = h.state.devices.get(iid)
    rt.control = ControlOwner.ai
    rt.current = InstanceCurrent(run_id=run_id, objective_id=f"{run_id}:{iid}", step_id=step_id)
    return rt


async def test_tomada_de_controle_so_com_a_ia_numa_etapa(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    espia = Espia()
    _instalar(harness, espia)
    ocioso = st.devices.get("android-03")
    status, lease = st.devices.request_control(ocioso)                         # ninguém conduzia: sem sinal
    assert status == "granted" and espia.tomadas == []
    st.devices.release_control(ocioso, lease)

    rt = _ia_numa_etapa(harness, "android-02", "r-x", "r-x:android-02:v1:open_app")
    rt.current = None                                                         # IA sem etapa (trabalho exclusivo)
    assert st.devices.request_control(rt)[0] == "pending" and espia.tomadas == []
    st.devices.release_control(rt, rt.pending_lease_id)

    rt = _ia_numa_etapa(harness, "android-02", "r-x", "r-x:android-02:v1:open_app")
    assert st.devices.request_control(rt)[0] == "pending"
    assert espia.tomadas == [TomadaDeControle("android-02", "r-x", "r-x:android-02", "r-x:android-02:v1:open_app")]
    st.devices.request_control(rt)                                            # o segundo clique não é outra tomada
    assert len(espia.tomadas) == 1
    rt.takeover_requested, rt.pending_lease_id, rt.control, rt.current = False, None, ControlOwner.none, None


async def test_tomada_de_controle_vira_um_sinal_sem_arvore_texto_nem_coordenada(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run = harness.run(["android-01"], mode="plan")
    await harness.wait_run(run.id, statuses=("planned",))
    etapa = f"{run.id}:android-01:v1:open_app"
    st.db.execute("UPDATE steps SET status='running', attempts=1 WHERE id=?", (etapa,))
    st.db.execute("INSERT INTO attempts(id, step_id, number, status, started_at) VALUES (?,?,?,?,?)",
                  (f"{etapa}:a1", etapa, 1, "running", "2026-09-29T10:00:00Z"))
    rt = _ia_numa_etapa(harness, "android-01", run.id, etapa)
    assert st.devices.request_control(rt)[0] == "pending"
    st.devices.request_control(rt)
    [s] = _sinais(harness, "tomou_controle")
    linha = st.db.one("SELECT capability, template_hash FROM steps WHERE id=?", (etapa,))
    assert linha is not None
    assert (s["source_ref"], s["attempt_id"], s["step_id"], s["run_id"]) == (f"takeover:{etapa}:a1", f"{etapa}:a1",
                                                                              etapa, run.id)
    assert (s["polarity"], s["created_by"], s["step_verified"], s["simulated"]) == ("negative", "panel", 0, 1)
    assert (s["app_package"], s["capability"], s["step_hash"]) == (PKG, linha["capability"] or "*",
                                                                   linha["template_hash"])
    assert s["note"] is None and json.loads(s["data"]) == {}                    # nada de árvore, texto ou ponto
    rt.takeover_requested, rt.pending_lease_id, rt.control, rt.current = False, None, ControlOwner.none, None


async def test_respondeu_pergunta_com_o_campo_e_o_sha256_nunca_o_valor(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run = harness.run(["android-01"], command=INCOMPLETO, mode="plan")
    await harness.wait_run(run.id, ("needs_input",))
    assistente = ComandoAssistido(st.runs)
    nova, criada = assistente.sucessora(run.id, RunSuccessorBody(command=COMMAND, mode="plan"))
    assert criada
    sinais = _sinais(harness, "respondeu_pergunta")
    esperado = hashlib.sha256(COMMAND.strip().encode()).hexdigest()
    assert {json.loads(s["data"])["campo"] for s in sinais} == {"recipient", "message"}
    for s in sinais:
        assert json.loads(s["data"]) == {"campo": json.loads(s["data"])["campo"], "resposta_sha256": esperado,
                                         "run_sucessora": nova.id}
        assert (s["run_id"], s["created_by"], s["polarity"], s["simulated"]) == (run.id, "panel", "neutral", 1)
        texto = " ".join(str(v) for v in s.values() if v is not None)
        assert "QA-001" not in texto and "Teste POC" not in texto                # o valor nunca entra
    assistente.sucessora(run.id, RunSuccessorBody(command=COMMAND, mode="plan"))  # duplo clique: nada de novo
    assert len(_sinais(harness, "respondeu_pergunta")) == 2
    # a sucessora cancelou a antiga — consequência da resposta, não gesto de cancelar
    assert st.repo.run_row(run.id)["status"] == "cancelled"
    assert _sinais(harness, "cancelou_execucao") == []


def _cliente(h: Harness) -> httpx.AsyncClient:
    """O painel pelo loopback, com jarra de cookies: o login dá o nome da sessão às rotas."""
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)),
                             base_url="http://127.0.0.1")


def _comando_incerto(h: Harness, cid: str, *, instance_id: str = "android-02", **trilha: str) -> str:
    """Um `reset` que terminou sem se saber o efeito (nenhuma sonda o fecha). Só linha no banco: nada é despachado."""
    assert h.state is not None
    h.state.commands.create(command_id=cid, instance_id=instance_id, verb="reset", idempotency_key=f"chave-{cid}",
                            params=dict(trilha) or None)
    h.state.commands.transition(cid, CommandState.dispatched)
    h.state.commands.transition(cid, CommandState.uncertain, reason="o canal caiu depois do envio")
    return cid


def _habilidade(h: Harness) -> str:
    """Uma habilidade existente: a correção é sempre de uma habilidade que errou."""
    assert h.state is not None
    agora = "2026-09-29T10:00:00Z"
    h.state.db.execute("INSERT INTO skill_definitions(id, name, app_id, created_at, updated_at) VALUES (?,?,?,?,?)"
                       " ON CONFLICT DO NOTHING", (HABILIDADE, "Abrir conversa", "qa-messenger", agora, agora))
    return HABILIDADE


def _etapa_falhada(h: Harness, run_id: str, iid: str) -> str:
    """A etapa de abrir o app, marcada `failed` (só etapa que falhou ou ficou sem prova se corrige)."""
    assert h.state is not None
    etapa = f"{run_id}:{iid}:v1:open_app"
    h.state.db.execute("UPDATE steps SET status='failed' WHERE id=?", (etapa,))
    assert h.state.db.scalar("SELECT status FROM steps WHERE id=?", (etapa,)) == "failed"
    return etapa


async def test_cancelar_pela_rota_grava_o_sinal_com_o_operador_da_sessao(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    planejada = harness.run(["android-01"], mode="plan")
    await harness.wait_run(planejada.id, statuses=("planned",))
    async with _cliente(harness) as c:
        assert (await c.post("/api/login", json={"operator": NOME})).status_code == 200
        r = await c.post(f"/api/runs/{planejada.id}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled", r.text
    [s] = _sinais(harness, "cancelou_execucao")
    # nada tinha rodado: cancelar antes de começar não diz nada de como a IA agiu
    assert (s["run_id"], s["created_by"], s["polarity"]) == (planejada.id, NOME, "neutral")
    # a chave é a do EPISÓDIO: a execução e o instante da transição
    prefixo = f"cancelamento:{planejada.id}:"
    assert s["source_ref"].startswith(prefixo) and parse_iso(s["source_ref"].removeprefix(prefixo)) is not None
    assert json.loads(s["data"]) == {"status_anterior": "planned"}
    assert (s["objective_id"], s["step_id"], s["simulated"], s["note"]) == (None, None, 1, None)

    # com trabalho feito e um item parado na pessoa: fechar o que ficou pendente é negativo; sem sessão, `panel`
    harness.fakes["android-02"].require_login = True
    parada = harness.run(["android-02"])
    assert (await harness.wait_run(parada.id)).status == "completed_with_issues"
    async with _cliente(harness) as c:
        assert (await c.post(f"/api/runs/{parada.id}/cancel")).status_code == 200
    [s2] = [s for s in _sinais(harness, "cancelou_execucao") if s["run_id"] == parada.id]
    assert (s2["created_by"], s2["polarity"], s2["app_package"]) == ("panel", "negative", PKG)
    assert json.loads(s2["data"]) == {"status_anterior": "completed_with_issues"}
    # sem `por` (quem chama por dentro, como a sucessora) não há gesto e não há sinal
    outra = harness.run(["android-03"], mode="plan")
    await harness.wait_run(outra.id, statuses=("planned",))
    assert st.runs.cancel(outra.id).status == "cancelled"
    assert len(_sinais(harness, "cancelou_execucao")) == 2


async def test_cancelamento_e_um_sinal_por_episodio(harness: Harness) -> None:
    """O clique repetido com o cancelamento já valendo não grava; a execução reaberta e cancelada de novo, sim.

    As chamadas a `st.runs` sem `await` entre elas são atômicas diante do escalonador (uma tarefa do mesmo laço): é o
    que prende a execução em `cancelling`, ou em `running` logo depois de reaberta, na hora do clique."""
    st = harness.state
    assert st is not None
    harness.fakes["android-02"].require_login = True
    run = harness.run(["android-02"])
    assert (await harness.wait_run(run.id)).status == "completed_with_issues"
    oid = f"{run.id}:android-02"
    # o item abandonado fica `failed`: a execução segue em aberto, e repetir o item a reabre depois
    st.runs.resolve(run.id, oid, ResolveBody(resolution="abandon", note="sem jeito"))
    assert st.repo.run_row(run.id)["status"] == "completed_with_issues"

    def cancelamentos() -> list[dict[str, Any]]:
        return _sinais(harness, "cancelou_execucao")

    def assentar() -> Any:
        return harness.wait(lambda: st.repo.run_row(run.id)["status"] != "cancelling", what="assentar o cancelamento")

    # episódio 1: o gesto que leva a execução a `cancelling` grava; o segundo clique — de outra pessoa ou da mesma —
    # com a execução ainda em `cancelling` não grava
    st.runs.cancel(run.id, por=NOME)
    assert st.repo.run_row(run.id)["status"] == "cancelling"
    st.runs.cancel(run.id, por="Bruno Lima")
    st.runs.cancel(run.id, por=NOME)
    assert len(cancelamentos()) == 1
    # assentada: o item falho não se cancela (`_finish_cancel` só fecha o que estava aberto), e a execução volta a
    # `completed_with_issues` com o cancelamento valendo — clicar de novo ainda é o mesmo episódio
    await assentar()
    assert st.repo.run_row(run.id)["status"] == "completed_with_issues"
    st.runs.cancel(run.id, por=NOME)
    await assentar()
    assert len(cancelamentos()) == 1

    # episódio 2: repetir o item reabre a execução (`completed_with_issues` → `running`), e cancelar ali é outro gesto
    st.runs.resolve(run.id, oid, ResolveBody(resolution="retry"))
    assert st.repo.run_row(run.id)["status"] == "running"
    st.runs.cancel(run.id, por=NOME)
    sinais = cancelamentos()
    assert len(sinais) == 2 and len({s["source_ref"] for s in sinais}) == 2
    assert [json.loads(s["data"])["status_anterior"] for s in sinais] == ["completed_with_issues", "running"]
    assert {(s["created_by"], s["polarity"], s["run_id"]) for s in sinais} == {(NOME, "negative", run.id)}
    await assentar()


async def _logado(c: httpx.AsyncClient, nome: str) -> httpx.AsyncClient:
    assert (await c.post("/api/login", json={"operator": nome})).status_code == 200
    return c


async def test_gestos_do_a2_pela_rota_levam_o_operador_da_sessao_e_sem_sessao_panel(harness: Harness) -> None:
    """Pendência do A2 (plano 22.1): resolver o item, repetir a execução e responder a pergunta saíam `panel` mesmo com
    sessão — o nome não chegava a `resolve`, `retry_failed` nem ao assistente. Agora a rota o passa (`por=`)."""
    st = harness.state
    assert st is not None
    harness.fakes["android-01"].require_login = True
    harness.fakes["android-02"].require_login = True
    parado = harness.run(["android-02"])
    repetir = harness.run(["android-01"])
    await harness.wait_run(parado.id)
    await harness.wait_run(repetir.id)
    item, item_r = f"{parado.id}:android-02", f"{repetir.id}:android-01"

    def de_novo(oid: str) -> Any:
        return harness.wait(lambda: st.repo.objective_row(oid)["status"] == "waiting_user", what="parar de novo")

    async with _cliente(harness) as c:
        await _logado(c, NOME)
        r = await c.post(f"/api/runs/{parado.id}/objectives/{item}/resolve",
                         json={"resolution": "retry", "note": "loguei no aparelho"})
        assert r.status_code == 200, r.text
        r = await c.post(f"/api/runs/{repetir.id}/retry_failed")
        assert r.status_code == 200 and r.json()["retried"] == [item_r], r.text
    await de_novo(item)
    await de_novo(item_r)
    async with _cliente(harness) as c:                                        # sem sessão: `panel`
        assert (await c.post(f"/api/runs/{parado.id}/objectives/{item}/resolve",
                             json={"resolution": "abandon"})).status_code == 200
        assert (await c.post(f"/api/runs/{repetir.id}/retry_failed")).json()["retried"] == [item_r]
    assert [s["created_by"] for s in _sinais(harness, "repetiu_item")] == [NOME]
    assert [s["created_by"] for s in _sinais(harness, "abandonou_item")] == ["panel"]
    assert [s["created_by"] for s in _sinais(harness, "repetiu_execucao")] == [NOME, "panel"]

    # "abandonar" de novo o item que já falhou é o MESMO ponto de decisão para quem vier depois: a primeira pessoa
    # grava, a segunda não vira outra linha (a régua conta linhas; a chave do gesto é o evento, não a pessoa)
    async with _cliente(harness) as c:
        await _logado(c, NOME)
        assert (await c.post(f"/api/runs/{parado.id}/objectives/{item}/resolve",
                             json={"resolution": "abandon"})).status_code == 200
    async with _cliente(harness) as c:
        await _logado(c, "Bruno Lima")
        assert (await c.post(f"/api/runs/{parado.id}/objectives/{item}/resolve",
                             json={"resolution": "abandon"})).status_code == 200
    abandonos = _sinais(harness, "abandonou_item")
    assert [s["created_by"] for s in abandonos] == ["panel", NOME]
    assert len({s["source_ref"] for s in abandonos}) == 2

    # responder a pergunta: com sessão, o nome; sem sessão, `panel`
    com = harness.run(["android-03"], command=INCOMPLETO, mode="plan")
    await harness.wait_run(com.id, ("needs_input",))
    async with _cliente(harness) as c:
        await _logado(c, NOME)
        r = await c.post(f"/api/runs/{com.id}/successor", json={"command": COMMAND, "mode": "plan"})
        assert r.status_code == 200, r.text
    sem = harness.run(["android-03"], command=INCOMPLETO, mode="plan")
    await harness.wait_run(sem.id, ("needs_input",))
    async with _cliente(harness) as c:
        r = await c.post(f"/api/runs/{sem.id}/successor", json={"command": COMMAND, "mode": "plan"})
        assert r.status_code == 200, r.text
    respostas = _sinais(harness, "respondeu_pergunta")
    assert {s["created_by"] for s in respostas if s["run_id"] == com.id} == {NOME}
    assert {s["created_by"] for s in respostas if s["run_id"] == sem.id} == {"panel"}


async def test_tomada_de_controle_pela_rota_leva_o_operador_e_e_uma_por_tentativa(harness: Harness) -> None:
    """Pendência do A2 (plano 22.1): o pedido de controle nasce na rota do aparelho e passa pelo gerenciador
    (`request_control(por=)`) até a `TomadaDeControle`. Pedir, desistir e outra pessoa pedir na MESMA tentativa não
    vira uma segunda intervenção: a régua conta intervenção por tentativa, e o primeiro autor fica."""
    st = harness.state
    assert st is not None
    run = harness.run(["android-01"], mode="plan")
    await harness.wait_run(run.id, statuses=("planned",))
    etapa = f"{run.id}:android-01:v1:open_app"
    st.db.execute("UPDATE steps SET status='running', attempts=1 WHERE id=?", (etapa,))
    st.db.execute("INSERT INTO attempts(id, step_id, number, status, started_at) VALUES (?,?,?,?,?)",
                  (f"{etapa}:a1", etapa, 1, "running", "2026-09-29T10:00:00Z"))
    rt = _ia_numa_etapa(harness, "android-01", run.id, etapa)
    rota = "/api/instances/android-01/control"
    async with _cliente(harness) as c:
        await _logado(c, NOME)
        pedido = (await c.post(f"{rota}/take")).json()
        assert pedido["status"] == "pending"
        assert (await c.post(f"{rota}/release", json={"lease_id": pedido["lease_id"]})).status_code == 200
    async with _cliente(harness) as c:
        await _logado(c, "Bruno Lima")
        assert (await c.post(f"{rota}/take")).json()["status"] == "pending"
    [s] = _sinais(harness, "tomou_controle")
    assert (s["source_ref"], s["created_by"], s["polarity"]) == (f"takeover:{etapa}:a1", NOME, "negative")

    # outra tentativa, pedida sem sessão: outro sinal, de `panel`
    st.db.execute("UPDATE attempts SET status='failed' WHERE id=?", (f"{etapa}:a1",))
    st.db.execute("INSERT INTO attempts(id, step_id, number, status, started_at) VALUES (?,?,?,?,?)",
                  (f"{etapa}:a2", etapa, 2, "running", "2026-09-29T10:05:00Z"))
    rt.takeover_requested, rt.pending_lease_id = False, None
    async with _cliente(harness) as c:
        assert (await c.post(f"{rota}/take")).json()["status"] == "pending"
    assert [(x["source_ref"], x["created_by"]) for x in _sinais(harness, "tomou_controle")] == [
        (f"takeover:{etapa}:a1", NOME), (f"takeover:{etapa}:a2", "panel")]
    rt.takeover_requested, rt.pending_lease_id, rt.control, rt.current = False, None, ControlOwner.none, None


async def test_comando_incerto_resolvido_vira_sinal_do_operador_com_a_trilha_do_comando(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run = harness.run(["android-02"], mode="plan")
    await harness.wait_run(run.id, statuses=("planned",))
    livre = _comando_incerto(harness, "c-teste-incerto-1")
    da_execucao = _comando_incerto(harness, "c-teste-incerto-2", run_id=run.id, objective_id=f"{run.id}:android-02",
                                   app_id="qa-messenger", profile_id="perfil-teste")
    async with _cliente(harness) as c:
        assert (await c.post("/api/login", json={"operator": NOME})).status_code == 200
        r1 = await c.post(f"/api/commands/{livre}/resolve",
                          json={"outcome": "succeeded", "note": "abri: os dados do app sumiram"})
        # a nota com cara de credencial é RECUSADA, como no voto do D2: nada é gravado, nem a resolução
        recusada = await c.post(f"/api/commands/{da_execucao}/resolve",
                                json={"outcome": "failed", "note": "senha: segredo123"})
        assert recusada.status_code == 409 and recusada.json()["detail"]["code"] == "note_looks_secret"
        registro = st.commands.get(da_execucao)
        assert registro is not None and registro["state"] == "uncertain"
        assert registro["reason"] == "o canal caiu depois do envio"                  # intocado
        assert "segredo123" not in json.dumps(dict(registro), default=str)
        assert not [s for s in _sinais(harness, "comando_incerto_resolvido") if s["source_ref"].endswith(da_execucao)]
        r2 = await c.post(f"/api/commands/{da_execucao}/resolve",
                          json={"outcome": "failed", "note": "o aparelho voltou sem os dados"})
    assert (r1.status_code, r1.json()["state"], r2.status_code, r2.json()["state"]) == (200, "succeeded", 200,
                                                                                          "failed")
    sinais = {s["source_ref"]: s for s in _sinais(harness, "comando_incerto_resolvido")}
    s1 = sinais[f"comando:{livre}"]
    # `succeeded` lê como confirmar à mão: a ação valeu, a prova não veio — neutro, nunca evidência a favor
    assert (s1["created_by"], s1["polarity"], s1["note"], s1["note_refused"]) == (NOME, "neutral",
                                                                                  "abri: os dados do app sumiram", 0)
    # sem execução no comando: o aparelho, e `simulated` do modo da instalação (o harness é simulado)
    assert (s1["instance_id"], s1["run_id"], s1["app_package"], s1["simulated"]) == ("android-02", None, "", 1)
    # o autor que o COMANDO gravou vai em `data`, para cruzar os dois (aqui coincide com a sessão)
    assert json.loads(s1["data"]) == {"verbo": "reset", "resolucao": "succeeded", "resolved_by": NOME}
    s2 = sinais[f"comando:{da_execucao}"]
    assert (s2["polarity"], s2["note"], s2["note_refused"]) == ("negative", "o aparelho voltou sem os dados", 0)
    assert (s2["run_id"], s2["objective_id"], s2["profile_id"], s2["app_package"]) == (
        run.id, f"{run.id}:android-02", "perfil-teste", PKG)

    # o nome do CORPO não entra como autor do sinal: sem sessão é `panel`, mesmo que o corpo diga `sistema`; o que o
    # comando gravou (o corpo) fica em `data`, e só lá
    forjado = _comando_incerto(harness, "c-teste-incerto-3")
    async with _cliente(harness) as c:
        r3 = await c.post(f"/api/commands/{forjado}/resolve",
                          json={"outcome": "cancelled", "requested_by": SYSTEM_ACTOR})
    assert r3.status_code == 200
    registro = st.commands.get(forjado)
    assert registro is not None and json.loads(registro["result"])["resolved_by"] == SYSTEM_ACTOR
    s3 = {s["source_ref"]: s for s in _sinais(harness, "comando_incerto_resolvido")}[f"comando:{forjado}"]
    assert (s3["created_by"], s3["polarity"]) == ("panel", "neutral")
    assert json.loads(s3["data"])["resolved_by"] == SYSTEM_ACTOR

    # a triagem do próprio livro segue de pé para quem chama a costura por dentro: a nota ruim não fica
    por_dentro = _comando_incerto(harness, "c-teste-incerto-4")
    st.costuras.comando_incerto_resolvido(ResolucaoDeComando(por_dentro, "failed", "senha: segredo123", NOME, True))
    s4 = {s["source_ref"]: s for s in _sinais(harness, "comando_incerto_resolvido")}[f"comando:{por_dentro}"]
    assert (s4["note"], s4["note_refused"], s4["created_by"]) == (None, 1, NOME)


async def test_nota_da_resolucao_pelo_painel_e_so_o_texto_da_pessoa(harness: Harness) -> None:
    """Pendência de 29/09 (plano 22.2): o painel prefixava a nota com "decidido no painel a partir de <aparelho>", e a
    triagem de credencial recusava a decisão inteira quando o id do aparelho tinha cara de segredo (um AVD como
    `Pixel_7a-Lab.02`). Agora o painel manda `origin=panel`, o backend compõe o contexto, e a triagem vê só o texto da
    pessoa; o cliente antigo, com o prefixo, continua funcionando. O `requested_by` livre passa pela mesma triagem."""
    st = harness.state
    assert st is not None
    avd = "Pixel_7a-Lab.02"
    novo = _comando_incerto(harness, "c-teste-origem-1", instance_id=avd)
    antigo = _comando_incerto(harness, "c-teste-origem-2", instance_id=avd)
    so_prefixo = _comando_incerto(harness, "c-teste-origem-3", instance_id=avd)
    outro_id = _comando_incerto(harness, "c-teste-origem-4", instance_id=avd)
    async with _cliente(harness) as c:
        assert (await c.post("/api/login", json={"operator": NOME})).status_code == 200
        r1 = await c.post(f"/api/commands/{novo}/resolve",
                          json={"outcome": "succeeded", "origin": "panel", "note": "conferi na máquina"})
        # a aba aberta antes do deploy ainda manda o prefixo: com o id do PRÓPRIO comando, ele sai da nota
        r2 = await c.post(f"/api/commands/{antigo}/resolve",
                          json={"outcome": "failed",
                                "note": f"decidido no painel a partir de {avd}: vi o app sem dados"})
        r3 = await c.post(f"/api/commands/{so_prefixo}/resolve",
                          json={"outcome": "failed", "note": f"decidido no painel a partir de {avd}"})
        # com OUTRO id, o texto inteiro é da pessoa — e é ele que a triagem olha
        r4 = await c.post(f"/api/commands/{outro_id}/resolve",
                          json={"outcome": "failed", "note": "decidido no painel a partir de Pixel_7a-Lab.03: x"})
    assert (r1.status_code, r2.status_code, r3.status_code) == (200, 200, 200), (r1.text, r2.text, r3.text)
    assert r4.status_code == 409 and r4.json()["detail"]["code"] == "note_looks_secret"

    def registro(cid: str) -> tuple[str, dict[str, Any]]:
        linha = st.commands.get(cid)
        assert linha is not None
        return linha["reason"], json.loads(linha["result"]) if linha["result"] else {}

    motivo, dados = registro(novo)
    assert motivo == f"resolvido à mão por {NOME}, no painel a partir de {avd}: conferi na máquina"
    assert (dados["note"], dados["origin"], dados["resolved_by"]) == ("conferi na máquina", "panel", NOME)
    motivo, dados = registro(antigo)
    assert motivo == f"resolvido à mão por {NOME}, no painel a partir de {avd}: vi o app sem dados"
    assert (dados["note"], dados["origin"]) == ("vi o app sem dados", "panel")
    motivo, dados = registro(so_prefixo)
    assert motivo == f"resolvido à mão por {NOME}, no painel a partir de {avd}" and dados["note"] is None
    motivo, dados = registro(outro_id)
    assert motivo == "o canal caiu depois do envio" and "resolved_by" not in dados      # recusado: intocado
    # o sinal leva só o texto da pessoa como nota — o contexto é do comando, não dela
    sinais = {s["source_ref"]: s for s in _sinais(harness, "comando_incerto_resolvido")}
    assert (sinais[f"comando:{novo}"]["note"], sinais[f"comando:{antigo}"]["note"]) == ("conferi na máquina",
                                                                                      "vi o app sem dados")
    assert sinais[f"comando:{so_prefixo}"]["note"] is None and f"comando:{outro_id}" not in sinais

    # `requested_by` (o autor sem sessão, gravado cru no motivo, em `resolved_by` e no evento): mesma triagem, mesma
    # recusa, nada gravado — COM sessão também (ela o ignora como autor, mas a regra é uma só); o rótulo limpo
    # continua valendo
    for rotulo in ("senha: segredo123", "Xy9!abcdEF#2026"):
        for com_sessao in (False, True):
            async with _cliente(harness) as c:
                if com_sessao:
                    await _logado(c, NOME)
                r = await c.post(f"/api/commands/{outro_id}/resolve",
                                 json={"outcome": "failed", "requested_by": rotulo})
            assert r.status_code == 409 and r.json()["detail"]["code"] == "note_looks_secret", (rotulo, com_sessao)
            linha = st.commands.get(outro_id)
            assert linha is not None and linha["state"] == "uncertain"
            assert rotulo not in json.dumps(dict(linha), default=str)
    assert f"comando:{outro_id}" not in {s["source_ref"] for s in _sinais(harness, "comando_incerto_resolvido")}
    async with _cliente(harness) as c:
        r = await c.post(f"/api/commands/{outro_id}/resolve",
                         json={"outcome": "failed", "requested_by": "script-de-carga", "origin": "panel"})
    assert r.status_code == 200, r.text
    assert registro(outro_id)[0] == f"resolvido à mão por script-de-carga, no painel a partir de {avd}"

    # ninguém identificado: o autor já é `panel`, e o contexto não repete o painel
    sem_ninguem = _comando_incerto(harness, "c-teste-origem-5", instance_id=avd)
    async with _cliente(harness) as c:
        r = await c.post(f"/api/commands/{sem_ninguem}/resolve", json={"outcome": "succeeded", "origin": "panel"})
    assert r.status_code == 200, r.text
    motivo, dados = registro(sem_ninguem)
    assert motivo == f"resolvido à mão por panel, a partir de {avd}"
    assert (dados["resolved_by"], dados["origin"], dados["note"]) == ("panel", "panel", None)


async def test_comando_incerto_simulated_vem_da_execucao_senao_da_instalacao(harness: Harness,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """As duas fontes DISCORDANDO: com a execução do `params` no banco vale `runs.simulated`; sem ela (id inexistente
    ou purgado) o sinal não aponta execução nenhuma e vale o modo da instalação no instante do gesto."""
    st = harness.state
    assert st is not None
    run = harness.run(["android-02"], mode="plan")
    await harness.wait_run(run.id, statuses=("planned",))
    casos = {  # comando: (instalação simulada?, runs.simulated forçado, run_id no params)
        "c-sim-1": (False, 1, run.id),
        "c-sim-2": (True, 0, run.id),
        "c-sim-3": (False, None, "r-inexistente"),
        "c-sim-4": (True, None, "r-inexistente"),
    }
    for cid, (instalacao, da_execucao, run_id) in casos.items():
        _comando_incerto(harness, cid, run_id=run_id)
        if da_execucao is not None:
            st.db.execute("UPDATE runs SET simulated=? WHERE id=?", (da_execucao, run.id))
        monkeypatch.setattr(st.provider, "simulated", instalacao)
        async with _cliente(harness) as c:
            assert (await c.post(f"/api/commands/{cid}/resolve", json={"outcome": "failed"})).status_code == 200
    sinais = {s["source_ref"]: s for s in _sinais(harness, "comando_incerto_resolvido")}
    assert {cid: (sinais[f"comando:{cid}"]["run_id"], sinais[f"comando:{cid}"]["simulated"]) for cid in casos} == {
        "c-sim-1": (run.id, 1),                  # a instalação diz real, a execução é simulada: vale a execução
        "c-sim-2": (run.id, 0),
        "c-sim-3": (None, 0),                    # execução inexistente: nada apontado, vale a instalação
        "c-sim-4": (None, 1),
    }


async def test_correcao_de_ensino_vira_sinal_ligado_a_etapa_corrigida(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    st.cfg.file.skills.enabled = True
    habilidade = _habilidade(harness)
    run = harness.run(["android-01"], mode="plan")
    await harness.wait_run(run.id, statuses=("planned",))
    etapa = _etapa_falhada(harness, run.id, "android-01")
    async with _cliente(harness) as c:
        assert (await c.post("/api/login", json={"operator": NOME})).status_code == 200
        r = await c.post("/api/teaching-sessions", json={"instruction": "Abrir a conversa", "skill_id": habilidade})
        assert r.status_code == 201, r.text
        tid = r.json()["id"]
        r = await c.post(f"/api/teaching-sessions/{tid}/corrections",
                         json={"body": "o contato certo é o QA-001", "run_id": run.id, "step_id": etapa})
    assert r.status_code == 200, r.text
    [s] = _sinais(harness, "correcao_de_ensino")
    turno = st.db.scalar("SELECT id FROM teaching_turns WHERE teaching_id=? AND kind='correction'", (tid,))
    linha = st.db.one("SELECT capability, template_hash FROM steps WHERE id=?", (etapa,))
    assert linha is not None
    assert (s["source_ref"], s["created_by"], s["polarity"], s["note"]) == (f"correcao:{tid}:{turno}", NOME,
                                                                            "negative", "o contato certo é o QA-001")
    assert (s["run_id"], s["step_id"], s["objective_id"], s["instance_id"]) == (run.id, etapa, f"{run.id}:android-01",
                                                                                "android-01")
    assert (s["app_package"], s["capability"], s["step_hash"]) == (PKG, linha["capability"] or "*",
                                                                   linha["template_hash"])
    assert (s["step_verified"], s["simulated"]) == (0, 1)
    assert json.loads(s["data"]) == {"teaching_id": tid, "skill_id": habilidade}

    # outra correção na mesma etapa é outro gesto; sem pessoa identificada, `panel` (nunca some por falta de autor)
    st.teaching.add_correction(tid, "e o botão é Enviar", run_id=run.id, step_id=etapa, by=None)
    assert [x["created_by"] for x in _sinais(harness, "correcao_de_ensino")] == [NOME, "panel"]


async def test_correcao_com_credencial_e_recusada_antes_de_gravar_e_a_que_so_fala_dela_entra_sem_nota(
        harness: Harness) -> None:
    """Plano 22.7: a correção nasce na visão da execução (o painel manda o texto cru da pessoa). O texto vai à conversa
    do ensino, ao prompt do generalizador e ao livro, e passa por DUAS triagens: a do ensino (`_person_text`), que
    recusa o VALOR com 400 `credential_in_text` antes de qualquer escrita — nem turno, nem sinal, nem o valor na
    resposta —; e a do livro, que olha também o ASSUNTO: a correção que só fala de senha vale no ensino, e o sinal é
    gravado sem a nota (`note_refused`)."""
    st = harness.state
    assert st is not None
    st.cfg.file.skills.enabled = True
    habilidade = _habilidade(harness)
    run = harness.run(["android-01"], mode="plan")
    await harness.wait_run(run.id, statuses=("planned",))
    etapa = _etapa_falhada(harness, run.id, "android-01")
    async with _cliente(harness) as c:
        assert (await c.post("/api/login", json={"operator": NOME})).status_code == 200
        r = await c.post("/api/teaching-sessions", json={"instruction": f"Corrigir a habilidade {habilidade} (versão 1).",
                                                         "skill_id": habilidade})
        assert r.status_code == 201, r.text
        tid = r.json()["id"]
        for valor, texto in (("Abc12345", "a senha certa era Abc12345"), ("482913", "o código que chegou era 482913")):
            r = await c.post(f"/api/teaching-sessions/{tid}/corrections",
                             json={"body": texto, "run_id": run.id, "step_id": etapa})
            assert r.status_code == 400 and r.json()["detail"]["code"] == "credential_in_text", r.text
            assert valor not in r.text
        assert st.db.scalar("SELECT COUNT(*) FROM teaching_turns WHERE teaching_id=? AND kind='correction'",
                            (tid,)) == 0
        assert _sinais(harness, "correcao_de_ensino") == []

        r = await c.post(f"/api/teaching-sessions/{tid}/corrections",
                         json={"body": "devia ter tocado em Esqueci a senha", "run_id": run.id, "step_id": etapa})
    assert r.status_code == 200, r.text
    assert [t["kind"] for t in r.json()["turns"]].count("correction") == 1
    [s] = _sinais(harness, "correcao_de_ensino")
    assert (s["note"], s["note_refused"], s["created_by"]) == (None, 1, NOME)


async def test_costura_que_lanca_nao_derruba_nenhum_gesto(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    _instalar(harness, Explode())
    harness.fakes["android-01"].require_login = True
    run = harness.run(["android-01"])
    await harness.wait_run(run.id)
    oid = f"{run.id}:android-01"
    assert st.runs.retry_failed(run.id)["retried"] == [oid]
    await harness.wait(lambda: st.repo.objective_row(oid)["status"] == "waiting_user", what="parar de novo")
    assert st.runs.resolve(run.id, oid, ResolveBody(resolution="abandon", note="sem jeito")).status == "failed"
    pergunta = harness.run(["android-02"], command=INCOMPLETO, mode="plan")
    await harness.wait_run(pergunta.id, ("needs_input",))
    assert ComandoAssistido(st.runs).sucessora(pergunta.id, RunSuccessorBody(command=COMMAND, mode="plan"))[1]
    rt = _ia_numa_etapa(harness, "android-03", "r-x", "r-x:android-03:v1:open_app")
    assert st.devices.request_control(rt)[0] == "pending"
    rt.takeover_requested, rt.pending_lease_id, rt.control, rt.current = False, None, ControlOwner.none, None
    # os três gestos que ganharam escritor: cancelar, resolver o comando incerto e corrigir no ensino
    planejada = harness.run(["android-03"], mode="plan")
    await harness.wait_run(planejada.id, statuses=("planned",))
    assert st.runs.cancel(planejada.id, por=NOME).status == "cancelled"
    cid = _comando_incerto(harness, "c-teste-explode")
    async with _cliente(harness) as c:
        r = await c.post(f"/api/commands/{cid}/resolve", json={"outcome": "failed"})
    assert r.status_code == 200 and r.json()["state"] == "failed"
    etapa = _etapa_falhada(harness, planejada.id, "android-03")
    tid = st.teaching.start("Abrir a conversa", skill_id=_habilidade(harness)).session.id
    visao = st.teaching.add_correction(tid, "o botão é outro", run_id=planejada.id, step_id=etapa, by=NOME)
    assert [t.kind.value for t in visao.turns].count("correction") == 1
    assert st.db.scalar("SELECT COUNT(*) FROM learning_signals") == 0


# ---------------------------------------------------------------------------------------------------- o livro
async def test_livro_desligado_nao_grava_nem_pede_licao(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    harness.cfg.file.aprendizado.enabled = False
    chamados: list[PedidoDeLicoes] = []

    class Fornecedor:
        def licoes_para(self, pedido: PedidoDeLicoes) -> list[str]:
            chamados.append(pedido)
            return [LICAO_DO_ATOR]

    extensoes(st.learning).definir_licoes(Fornecedor())
    assert st.costuras.licoes_para(_pedido()) == [] and chamados == []
    harness.fakes["android-01"].require_login = True
    run = harness.run(["android-01"])
    await harness.wait_run(run.id)
    st.runs.resolve(run.id, f"{run.id}:android-01", ResolveBody(resolution="abandon", note="nada"))
    assert st.db.scalar("SELECT COUNT(*) FROM learning_signals") == 0
    # os três escritores novos também se calam: corrigir no ensino, cancelar e resolver o comando pela rota
    planejada = harness.run(["android-03"], mode="plan")
    await harness.wait_run(planejada.id, statuses=("planned",))
    etapa = _etapa_falhada(harness, planejada.id, "android-03")
    tid = st.teaching.start("Abrir a conversa", skill_id=_habilidade(harness)).session.id
    visao = st.teaching.add_correction(tid, "o botão é outro", run_id=planejada.id, step_id=etapa, by=NOME)
    assert [t.kind.value for t in visao.turns].count("correction") == 1
    cid = _comando_incerto(harness, "c-teste-desligado")
    async with _cliente(harness) as c:
        assert (await c.post("/api/login", json={"operator": NOME})).status_code == 200
        r = await c.post(f"/api/runs/{planejada.id}/cancel")
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
        r = await c.post(f"/api/commands/{cid}/resolve", json={"outcome": "failed", "note": "voltou sem os dados"})
        assert r.status_code == 200 and r.json()["state"] == "failed"
    assert st.db.scalar("SELECT COUNT(*) FROM learning_signals") == 0


async def test_licoes_do_livro_seguem_o_modo_e_o_fornecedor_registrado(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    chamados: list[PedidoDeLicoes] = []

    class Fornecedor:
        def licoes_para(self, pedido: PedidoDeLicoes) -> list[str]:
            chamados.append(pedido)
            return [LICAO_DO_ATOR]

    assert st.costuras.licoes_para(_pedido()) == []                             # ninguém fornece ainda (A7)
    extensoes(st.learning).definir_licoes(Fornecedor())
    assert st.costuras.licoes_para(_pedido()) == [LICAO_DO_ATOR]               # `shadow` de fábrica: o A7 decide
    harness.cfg.file.aprendizado.licoes.modo = "off"
    assert st.costuras.licoes_para(_pedido()) == [] and len(chamados) == 1      # `off`: nem pergunta


async def test_observador_registrado_recebe_cada_fechamento_e_um_quebrado_nao_cala_os_outros(
        harness: Harness) -> None:
    harness.pular_o_tempo()   # T.2: sem assentamento em tempo real
    st = harness.state
    assert st is not None
    vistos: list[str] = []

    class Quebrado:
        nome = "quebrado"

        def ao_fechar(self, fechamento: FechamentoDeTentativa) -> None:
            raise RuntimeError("observador quebrado")

    class Anota:
        nome = "anota"

        def ao_fechar(self, fechamento: FechamentoDeTentativa) -> None:
            vistos.append(fechamento.attempt_id)

    extensoes(st.learning).observar(Quebrado())
    extensoes(st.learning).observar(Anota())
    extensoes(st.learning).observar(Anota())                                    # o mesmo nome não entra duas vezes
    run = harness.run(["android-01"])
    assert (await harness.wait_run(run.id)).status == "completed"
    tentativas = [r["id"] for r in st.db.query(
        "SELECT a.id FROM attempts a JOIN steps s ON s.id=a.step_id WHERE s.run_id=?", (run.id,))]
    assert Counter(vistos) == Counter(tentativas)
