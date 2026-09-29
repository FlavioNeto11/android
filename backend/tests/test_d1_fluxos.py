"""D1 nos fluxos (ADR-054, pacote A5): o fluxo aprendido de execução nasce `candidate` — inerte, `match` não o casa — e
é validado por SOMBRA no digest: a próxima execução real do mesmo comando (que já chamou o planejador) tem o plano
comparado ao do candidato. Concordou: sem efeito externo, o sistema publica; com efeito, para em `validated` e vai
para o dono. Duas discordâncias reais desligam, e a linha desligada pelo sistema renasce do próximo plano comprovado
(a `match_key` é única e a linha nunca é apagada), com a contagem recomeçada. Execução simulada deixa evidência e não
conta; o que uma pessoa desligou não renasce; o conteúdo vetado não volta; o treino publica na hora.

Nível de prova: `simulated` (banco de teste migrado pela fábrica da suíte; o último teste no Harness da porta 5640,
aparelho falso e IA simulada).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.db import Database
from app.models import Plan, PlannerInfo, PlanStep, Postcondition, StepResult
from app.modules.learning.application.nativos import AssinaturaDoPlano, PassoAssinado, comparar
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.infrastructure import ligar_nativos
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.skills.domain.refs import SkillRef
from app.modules.skills.infrastructure.legacy_flows import LegacyFlowAdapter
from app.taskqueue.flows import FlowStore
from app.util import now, now_iso

from .conftest import Harness
from .fake_skills import banco as banco_migrado

S = SkillState
DONO = "painel:dono"
MODELO = "abrir o perfil de {username} no instagram"


def _comando(usuario: str) -> str:
    return f"abrir o perfil de {usuario} no instagram"


def _plano(usuario: str, *, extra: bool = False, efeito: bool = False) -> Plan:
    """Plano só de navegação (o caso do android-06), ou com uma etapa a mais, ou com uma etapa de efeito externo."""
    abrir = PlanStep(key="abrir_perfil", title=f"Abrir o perfil de {usuario}", goal=f"abrir {usuario}",
                     capability="OPEN_PROFILE", bindings={"username": usuario},
                     postcondition=Postcondition(kind="text_visible", value=usuario, description="perfil aberto"))
    passos = [abrir]
    if extra:
        passos.insert(0, PlanStep(key="abrir_busca", title="Abrir a busca", goal="buscar", capability="OPEN_SEARCH",
                                  postcondition=Postcondition(kind="element_present", value="busca",
                                                              description="busca aberta")))
    if efeito:
        passos.append(PlanStep(key="seguir", title=f"Seguir {usuario}", goal=f"seguir {usuario}", side_effect=True,
                               capability="FOLLOW", depends_on=["abrir_perfil"], bindings={"username": usuario},
                               postcondition=Postcondition(kind="text_visible", value="Seguindo",
                                                           description="seguindo")))
    return Plan(summary=f"Abrir o perfil de {usuario}", app_id="instagram", parameters={"username": usuario},
                steps=passos, planner=PlannerInfo(provider="anthropic", model="claude", simulated=False))


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.config: dict[str, Any] = {"concordancias": 1, "com_prova": True}
        self.decisoes: list[tuple[str, str]] = []
        self.repo = SqlLearningRepository(db, precos=dict)
        self.servico = LearningService(self.repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes, relogio=now,
                                       retencao_de_logs_dias=lambda: 14)
        self.flows = FlowStore(db)
        ligar_nativos.ligar(self.servico, self.repo, db, fluxos=self.flows,
                            concordancias=lambda: int(self.config["concordancias"]),
                            com_prova=lambda: bool(self.config["com_prova"]),
                            decidir=lambda texto, run_id: self.decisoes.append((run_id, texto)))

    def execucao(self, run_id: str, plano: Plan, comando: str, *, simulada: bool = False,
                 aparelho: str = "android-01", status: str = "completed") -> dict[str, Any]:
        """Uma execução assentada: objetivo e etapas `succeeded`, todas comprovadas pela tela."""
        self.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, plan,"
                        " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (run_id, f"k-{run_id}", comando, "execute", status, int(simulada), json.dumps([aparelho]),
                         plano.model_dump_json(), now_iso()))
        self.db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,?)",
                        (f"{run_id}:o1", run_id, aparelho, "succeeded", 1))
        for seq, passo in enumerate(plano.steps, start=1):
            self.db.execute(
                "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                " postcondition, timeout_s, max_attempts, status, result) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"{run_id}:{passo.key}", run_id, f"{run_id}:o1", aparelho, 1, seq, passo.key, passo.title, passo.goal,
                 passo.postcondition.model_dump_json(), 60, 3, "succeeded",
                 StepResult(verified=True, evidence_text="visto na tela").model_dump_json()))
        linha = self.db.one("SELECT * FROM runs WHERE id=?", (run_id,))
        assert linha is not None
        return dict(linha)

    def roda(self, run_id: str, usuario: str, *, extra: bool = False, efeito: bool = False, simulada: bool = False,
             aparelho: str = "android-01", comando: str | None = None) -> str | None:
        """O que o central faz quando a execução assenta: `_learn_flow` (ai.flows) e, depois, o digest."""
        run = self.execucao(run_id, _plano(usuario, extra=extra, efeito=efeito), comando or _comando(usuario),
                            simulada=simulada, aparelho=aparelho)
        flow_id = self.flows.learn_from_run(run)
        relatorio = self.servico.digerir_execucao(run_id)
        assert not relatorio.falhas, relatorio
        return flow_id

    def status(self, flow_id: str) -> str:
        return str(self.db.scalar("SELECT status FROM flows WHERE id=?", (flow_id,)))

    def trilha(self, flow_id: str) -> list[tuple[str | None, str, str, str | None]]:
        return [(t.from_state.value if t.from_state else None, t.to_state.value, t.decided_by, t.run_id)
                for t in self.repo.trilha(f"fluxo:{flow_id}")]


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    db = banco_migrado(tmp_path, "d1-fluxos.sqlite3")
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram','com.instagram.android',0)")
    yield Mundo(db)
    db.close()


# ------------------------------------------------------------------ comparação pura
def test_comparacao_do_plano_pela_acao_efeito_pos_condicao_e_parametros() -> None:
    passo = PassoAssinado(acao="OPEN_PROFILE", side_effect=False, pos="text_visible")
    modelo = AssinaturaDoPlano(passos=(passo,), parametros={"username": "{username}", "fixo": "x"})
    assert comparar(modelo, AssinaturaDoPlano(passos=(passo,), parametros={"username": "@bia", "fixo": "x"})) is None
    assert comparar(modelo, AssinaturaDoPlano(passos=(passo, passo), parametros={"username": "@b", "fixo": "x"}))
    outro = PassoAssinado(acao="OPEN_THREAD", side_effect=False, pos="text_visible")
    assert "outra ação" in str(comparar(modelo, AssinaturaDoPlano(passos=(outro,), parametros=modelo.parametros)))
    efeito = PassoAssinado(acao="OPEN_PROFILE", side_effect=True, pos="text_visible")
    assert "efeito" in str(comparar(modelo, AssinaturaDoPlano(passos=(efeito,), parametros=modelo.parametros)))
    assert comparar(modelo, AssinaturaDoPlano(passos=(passo,), parametros={"username": "@b", "fixo": "y"}))
    assert comparar(modelo, AssinaturaDoPlano(passos=(passo,), parametros={"username": "@b"}))


# ------------------------------------------------------------------ nascimento
def test_nasce_candidato_e_match_ignora_candidato_e_validado(mundo: Mundo) -> None:
    flow_id = mundo.roda("r-1", "@nasa")
    assert flow_id is not None and mundo.status(flow_id) == "candidate"
    assert mundo.db.scalar("SELECT command_template FROM flows WHERE id=?", (flow_id,)) == MODELO
    assert mundo.flows.match(_comando("@spacex")) is None                    # inerte: o planejador segue sendo chamado
    assert mundo.trilha(flow_id) == [(None, "candidate", "sistema", "r-1")]
    assert any(r == "r-1" and "candidato" in t for r, t in mundo.decisoes)
    mundo.db.execute("UPDATE flows SET status='validated' WHERE id=?", (flow_id,))
    assert mundo.flows.match(_comando("@spacex")) is None
    # a habilidade legada mostra o estado do D1, sem publicar nada
    adaptador = LegacyFlowAdapter(mundo.db, mundo.flows)
    assert adaptador.get(SkillRef.legacy(flow_id)).state is S.VALIDATED and adaptador.published(f"flow:{flow_id}") is None
    mundo.db.execute("UPDATE flows SET status='candidate' WHERE id=?", (flow_id,))
    assert adaptador.get(SkillRef.legacy(flow_id)).state is S.CANDIDATE


def test_sombra_concorda_e_sem_efeito_o_sistema_publica(mundo: Mundo) -> None:
    flow_id = mundo.roda("r-1", "@nasa")
    assert flow_id and mundo.status(flow_id) == "candidate"                  # só a execução que o gerou
    # 2ª execução do mesmo comando (outro valor): o planejador foi chamado, o plano dela concorda com o candidato
    assert mundo.roda("r-2", "@spacex", aparelho="android-02") is None      # nada nasce: o candidato já existe
    assert mundo.status(flow_id) == "active"
    assert mundo.trilha(flow_id)[-2:] == [("candidate", "validated", "sistema", "r-2"),
                                          ("validated", "published", "sistema", "r-2")]
    assert any(r == "r-2" and "publicado pelo sistema" in t for r, t in mundo.decisoes)
    # 3ª: o plano é reaproveitado, sem planejador
    casado = mundo.flows.match(_comando("@esa"))
    assert casado is not None and casado[1].parameters == {"username": "@esa"}
    evidencias = mundo.repo.evidencias(f"fluxo:{flow_id}")
    assert {(e.origin_ref, e.stance.value) for e in evidencias} == {("run:r-1", "for"), ("run:r-2", "for")}


def test_com_efeito_para_em_validated_e_vai_para_o_dono(mundo: Mundo) -> None:
    flow_id = mundo.roda("r-1", "@nasa", efeito=True)
    assert flow_id
    mundo.roda("r-2", "@spacex", efeito=True)
    assert mundo.status(flow_id) == "validated"
    assert mundo.flows.match(_comando("@esa")) is None
    assert ("fluxo", flow_id) in {(e.kind.value, e.ref) for e in mundo.servico.pendentes()}
    assert any(r == "r-2" and "só o dono" in t for r, t in mundo.decisoes)
    # mais concordâncias não publicam: é do dono
    mundo.roda("r-3", "@esa", efeito=True)
    assert mundo.status(flow_id) == "validated"
    mundo.servico.mudar_estado(LivroKind.FLUXO, flow_id, S.PUBLISHED, by=DONO, reason="aprovado em lote")
    assert mundo.status(flow_id) == "active" and mundo.flows.match(_comando("@esa")) is not None


def test_concordancias_zero_publica_com_a_propria_execucao_real(mundo: Mundo) -> None:
    mundo.config["concordancias"] = 0
    flow_id = mundo.roda("r-1", "@nasa")
    assert flow_id and mundo.status(flow_id) == "active"
    # com efeito externo, nem assim: validado, e o dono decide
    seguir = mundo.roda("r-2", "@nasa", efeito=True, comando="seguir o perfil de @nasa no instagram")
    assert seguir and mundo.status(seguir) == "validated"


def test_plano_diferente_conta_contra_e_duas_discordancias_desligam(mundo: Mundo) -> None:
    flow_id = mundo.roda("r-1", "@nasa")
    assert flow_id
    mundo.roda("r-2", "@spacex", extra=True)                                 # a IA fez outro caminho
    assert mundo.status(flow_id) == "candidate"
    [contra] = [e for e in mundo.repo.evidencias(f"fluxo:{flow_id}") if e.stance.value == "against"]
    assert contra.origin_ref == "run:r-2" and "@spacex" not in (contra.detail or "")   # sem valor de parâmetro
    mundo.roda("r-3", "@esa", extra=True)
    assert mundo.status(flow_id) == "disabled"
    assert mundo.trilha(flow_id)[-1] == ("candidate", "disabled", "sistema", "r-3")
    assert any(r == "r-3" and "desligado pelo sistema" in t for r, t in mundo.decisoes)


def test_execucao_simulada_nao_conta(mundo: Mundo) -> None:
    flow_id = mundo.roda("r-1", "@nasa")
    assert flow_id
    for n in (2, 3, 4):
        mundo.roda(f"r-{n}", "@spacex", simulada=True)                       # concorda, mas é simulada
    assert mundo.status(flow_id) == "candidate"
    assert {e.simulated for e in mundo.repo.evidencias(f"fluxo:{flow_id}") if e.origin_ref != "run:r-1"} == {True}
    for n in (5, 6):
        mundo.roda(f"r-{n}", "@esa", extra=True, simulada=True)              # discorda, mas é simulada
    assert mundo.status(flow_id) == "candidate"
    mundo.roda("r-7", "@esa")                                                # a primeira concordância real
    assert mundo.status(flow_id) == "active"


def test_candidato_nascido_de_execucao_simulada_nunca_e_publicado_pelo_sistema(mundo: Mundo) -> None:
    flow_id = mundo.roda("r-1", "@nasa", simulada=True)
    assert flow_id and mundo.status(flow_id) == "candidate"
    assert any("simulada não é prova" in t for _, t in mundo.decisoes)
    mundo.roda("r-2", "@spacex", simulada=True)
    assert mundo.status(flow_id) == "candidate"


def test_execucao_que_nao_serve_de_comparacao_nao_deixa_evidencia(mundo: Mundo) -> None:
    flow_id = mundo.roda("r-1", "@nasa")
    assert flow_id
    # confirmada à mão: completed, mas uma etapa sem prova da tela
    mundo.execucao("r-2", _plano("@spacex"), _comando("@spacex"))
    mundo.db.execute("UPDATE steps SET result=? WHERE run_id='r-2'", (StepResult(verified=False).model_dump_json(),))
    mundo.servico.digerir_execucao("r-2")
    # falhou, e outro comando
    mundo.execucao("r-3", _plano("@esa"), _comando("@esa"), status="failed")
    mundo.servico.digerir_execucao("r-3")
    mundo.execucao("r-4", _plano("@esa"), "abra a conversa com @esa no instagram")
    mundo.servico.digerir_execucao("r-4")
    assert [e.origin_ref for e in mundo.repo.evidencias(f"fluxo:{flow_id}")] == ["run:r-1"]
    # o digest repetido da mesma execução não soma duas vezes
    mundo.servico.digerir_execucao("r-1")
    assert len(mundo.repo.evidencias(f"fluxo:{flow_id}")) == 1


# ------------------------------------------------------------------ a linha refutada renasce; a da pessoa, não
def _refutado(mundo: Mundo) -> str:
    flow_id = mundo.roda("r-1", "@nasa")
    assert flow_id
    mundo.roda("r-2", "@spacex", extra=True)
    mundo.roda("r-3", "@esa", extra=True)
    assert mundo.status(flow_id) == "disabled"
    return flow_id


def test_linha_refutada_pelo_sistema_renasce_e_a_contagem_recomeca(mundo: Mundo) -> None:
    flow_id = _refutado(mundo)
    # o próximo plano comprovado (o caminho com a busca, que a IA vem fazendo) renasce NA MESMA LINHA
    assert mundo.roda("r-4", "@nasa", extra=True) == flow_id
    assert mundo.status(flow_id) == "candidate"
    assert mundo.db.scalar("SELECT source_run_id FROM flows WHERE id=?", (flow_id,)) == "r-4"
    assert mundo.db.scalar("SELECT COUNT(*) FROM flows") == 1
    assert mundo.trilha(flow_id)[-1] == ("disabled", "candidate", "sistema", "r-4")
    # as duas discordâncias eram da encarnação anterior: não desligam esta
    mundo.roda("r-5", "@spacex", extra=True)
    assert mundo.status(flow_id) == "active"


def test_conteudo_vetado_nao_volta(mundo: Mundo) -> None:
    flow_id = _refutado(mundo)
    # a IA volta a fazer exatamente o plano que o sistema refutou: ele não renasce (veto do sistema, 90 dias)
    assert mundo.roda("r-4", "@nasa") is None
    assert mundo.status(flow_id) == "disabled"


def test_o_que_uma_pessoa_desligou_nao_renasce(mundo: Mundo) -> None:
    flow_id = mundo.roda("r-1", "@nasa")
    assert flow_id
    mundo.servico.mudar_estado(LivroKind.FLUXO, flow_id, S.DISABLED, by=DONO, reason="não quero este comando")
    assert mundo.roda("r-2", "@spacex", extra=True) is None                  # outro plano, mesmo assim não
    assert mundo.status(flow_id) == "disabled"
    # desligado pela rota antiga (`PUT /api/flows`, sem trilha): também é decisão de pessoa
    mundo.db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at)"
                     " VALUES ('legado','legado','abra a conversa com {username} no instagram',"
                     "'abra a conversa com {username} no instagram','{}','instagram','disabled',?)", (now_iso(),))
    run = mundo.execucao("r-4", _plano("@ana"), "abra a conversa com @ana no instagram")
    assert mundo.flows.learn_from_run(run) is None
    assert mundo.status("legado") == "disabled"


def test_treino_e_adotado_nao_sao_reaproveitados(mundo: Mundo) -> None:
    flow_id = _refutado(mundo)
    mundo.db.execute("UPDATE flows SET source='training:t-1' WHERE id=?", (flow_id,))
    assert mundo.roda("r-4", "@nasa", extra=True) is None
    mundo.db.execute("UPDATE flows SET source=NULL WHERE id=?", (flow_id,))
    mundo.db.execute("INSERT INTO skill_definitions(id, name, app_id, legacy_flow_id, created_at, updated_at)"
                     " VALUES ('ig.perfil','Perfil','instagram',?,?,?)", (flow_id, now_iso(), now_iso()))
    assert mundo.roda("r-5", "@nasa", extra=True) is None
    assert mundo.status(flow_id) == "disabled"


# ------------------------------------------------------------------ treino e o modo anterior
def test_learn_from_plan_publica_na_hora(mundo: Mundo) -> None:
    plano = _plano("{username}", efeito=True)
    plano.parameters = {"username": "{username}"}
    flow_id = mundo.flows.learn_from_plan(plano, MODELO, source="training:t-1")
    assert mundo.status(flow_id) == "active" and mundo.flows.match(_comando("@ana")) is not None
    assert mundo.trilha(flow_id) == [(None, "published", "training:t-1", None)]
    # com efeito, mas decidido por uma pessoa: não é legado a revisar
    assert ("fluxo", flow_id) not in {(e.kind.value, e.ref) for e in mundo.servico.revisar()}


def test_com_prova_desligado_e_o_modo_anterior(mundo: Mundo) -> None:
    mundo.config["com_prova"] = False
    flow_id = mundo.roda("r-1", "@nasa", efeito=True)
    assert flow_id and mundo.status(flow_id) == "active"
    assert mundo.flows.match(_comando("@spacex")) is not None
    # e nada é reaprendido por cima de uma linha existente
    mundo.db.execute("UPDATE flows SET status='disabled' WHERE id=?", (flow_id,))
    assert mundo.roda("r-2", "@spacex", extra=True) is None


def test_loja_crua_segue_o_modo_anterior(mundo: Mundo) -> None:
    run = mundo.execucao("r-1", _plano("@nasa", efeito=True), _comando("@nasa"))
    crua = FlowStore(mundo.db)
    flow_id = crua.learn_from_run(run)
    assert flow_id and mundo.status(flow_id) == "active" and mundo.trilha(flow_id) == []


# ------------------------------------------------------------------ de ponta a ponta, no central (Harness)
async def test_no_central_o_fluxo_nasce_candidato_e_a_ia_segue_planejando(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    assert state.scheduler.flows.politica is not None and state.scheduler.executor.recipes.ouvinte is not None
    harness.cfg.file.ai.flows = True
    harness.cfg.file.aprendizado.fluxo.com_prova = True                     # o D1 de produção
    first = await harness.wait_run(harness.run(["android-01"]).id)
    assert first.status == "completed" and harness.ai.count("plan") == 1
    [fluxo] = state.db.query("SELECT * FROM flows")
    assert fluxo["status"] == "candidate" and fluxo["source_run_id"] == first.id
    ref = f"fluxo:{fluxo['id']}"
    await harness.wait(lambda: state.db.one("SELECT id FROM learning_evidence WHERE item_ref=?", (ref,)) is not None,
                       10, "evidência do nascimento")
    await harness.wait(lambda: not state._digestoes, 10, "digest encerrado")  # noqa: SLF001
    assert any("candidato" in (e["message"] or "") for e in state.db.query(
        "SELECT message FROM events WHERE run_id=? AND kind='decision'", (first.id,)))
    # 2ª execução do mesmo comando: o candidato não age (o planejador é chamado de novo), e a execução do Harness é
    # simulada: a concordância fica registrada, mas não publica nada
    second = await harness.wait_run(harness.run(["android-02"]).id)
    assert second.status == "completed" and harness.ai.count("plan") == 2
    await harness.wait(lambda: not state._digestoes, 10, "digest encerrado")  # noqa: SLF001
    assert state.db.scalar("SELECT status FROM flows WHERE id=?", (fluxo["id"],)) == "candidate"
    assert state.db.scalar("SELECT COUNT(*) FROM learning_transitions WHERE item_ref=?", (ref,)) == 1
    assert {(r["origin_ref"], r["stance"], r["simulated"]) for r in state.db.query(
        "SELECT origin_ref, stance, simulated FROM learning_evidence WHERE item_ref=?", (ref,))} == {
        (f"run:{first.id}", "for", 1), (f"run:{second.id}", "for", 1)}
