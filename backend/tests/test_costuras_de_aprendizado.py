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
- `aprendizado.enabled: false` desliga tudo, e o modo `off` das lições não pede lição a ninguém.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from app.automation.hierarchy import UiTree
from app.models import ControlOwner, InstanceCurrent, ResolveBody
from app.modules.learning.infrastructure.ligar_costuras import extensoes
from app.planning.provider import DecisionRequest, PlanRequest, StepContext, VerifyRequest
from app.taskqueue.assistente import ComandoAssistido, RunSuccessorBody
from app.taskqueue.costuras import (SEM_COSTURAS, FechamentoDeTentativa, PedidoDeLicoes, RepeticaoDeExecucao,
                                    ResolucaoDeItem, RespostaAPergunta, TomadaDeControle, avisar, pedir_licoes)

from .conftest import COMMAND, Harness

PKG = "com.pocqa.messenger"
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

    def licoes_para(self, pedido: PedidoDeLicoes) -> list[str]:
        self.pedidos.append(pedido)
        return list(self.ator if pedido.papel == "actor" else self.plano)

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


def _instalar(h: Harness, costuras: Any) -> None:
    assert h.state is not None
    h.state.scheduler.executor.costuras = costuras
    h.state.runs.costuras = costuras
    h.state.devices.costura_de_controle = costuras


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
