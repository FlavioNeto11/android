"""D1 nos fluxos (ADR-054, pacote A5): o fluxo aprendido de execução nasce `candidate` — inerte, `match` não o casa — e
é validado por SOMBRA no digest: a próxima execução real do mesmo comando (que já chamou o planejador) tem o plano
comparado ao do candidato. Concordou: sem efeito externo, o sistema publica; com efeito, para em `validated` e vai
para o dono. Duas discordâncias reais desligam, e a linha desligada pelo sistema renasce do próximo plano comprovado
(a `match_key` é única e a linha nunca é apagada), com a contagem recomeçada. Execução simulada deixa evidência e não
conta; o que uma pessoa desligou não renasce; o conteúdo vetado não volta; o treino publica na hora.

A trilha que a loja grava não derruba o fluxo nem com o aborto de transação do PostgreSQL (22.5, imitado sobre o
SQLite em `aborto_do_postgres`), e a adoção por uma habilidade — adotar, desfazer, publicar a versão de quem adotou —
entra na mesma trilha com a pessoa que decidiu, ou a adoção não acontece (22.4); o sistema não adota nem devolve.

Nível de prova: `simulated` (banco de teste migrado pela fábrica da suíte; o último teste no Harness da porta 5640,
aparelho falso e IA simulada).
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.db import OPERATIONAL_ERRORS, Database
from app.models import Plan, PlannerInfo, PlanStep, Postcondition, StepResult
from app.modules.learning.application.nativos import AssinaturaDoPlano, PassoAssinado, comparar
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import SkillState, TransicaoProibida
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.infrastructure import ligar_nativos
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.skills.domain.lifecycle import SYSTEM_ACTOR, DuplicateCommand, TransitionForbidden
from app.modules.skills.domain.refs import SkillRef
from app.modules.skills.domain.versions import Provenance, SourceKind
from app.modules.skills.infrastructure.legacy_flows import LegacyFlowAdapter
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository
from app.taskqueue.flows import FlowStore
from app.taskqueue.scheduler import Scheduler, texto_do_fluxo_salvo
from app.util import now, now_iso

from .aborto_do_postgres import embrulhar, savepoints
from .conftest import COMMAND, Harness
from .fake_skills import banco as banco_migrado
from .fake_skills import documento, repositorio
from .fake_skills import fluxo as fluxo_gravado

S = SkillState
DONO = "painel:dono"
NOME = "Ana Ribeiro"                                                        # o operador que faz login no painel
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
    def __init__(self, db: Database, *, habilidades: SqlSkillRepository | None = None) -> None:
        self.db = db
        self.config: dict[str, Any] = {"concordancias": 1, "com_prova": True}
        self.decisoes: list[tuple[str, str]] = []
        self.repo = SqlLearningRepository(db, precos=dict)
        self.servico = LearningService(self.repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes, relogio=now,
                                       retencao_de_logs_dias=lambda: 14)
        self.flows = FlowStore(db)
        ligar_nativos.ligar(self.servico, self.repo, db, fluxos=self.flows, habilidades=habilidades,
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


def test_candidato_nascido_de_execucao_simulada_nao_e_publicado_por_execucoes_simuladas(mundo: Mundo) -> None:
    flow_id = mundo.roda("r-1", "@nasa", simulada=True)
    assert flow_id and mundo.status(flow_id) == "candidate"
    assert any("simulada não é prova" in t for _, t in mundo.decisoes)
    mundo.roda("r-2", "@spacex", simulada=True)
    assert mundo.status(flow_id) == "candidate"


def test_candidato_nascido_de_execucao_simulada_vale_depois_das_reais_que_o_texto_promete(mundo: Mundo) -> None:
    """A simulada não conta, nem a que o gerou: faltam as `1 + concordancias` reais inteiras — e é isso que a linha do
    tempo diz (antes dizia "só uma pessoa o publica", e as execuções reais o publicavam)."""
    flow_id = mundo.roda("r-1", "@nasa", simulada=True)
    assert flow_id
    [nascimento] = [t for r, t in mundo.decisoes if r == "r-1"]
    assert "a que o gerou não conta" in nascimento and "depois de mais 2 execução(ões) real(is)" in nascimento
    assert "só uma pessoa" not in nascimento
    mundo.roda("r-2", "@spacex")
    assert mundo.status(flow_id) == "candidate"                              # 1 real: falta outra
    mundo.roda("r-3", "@esa", aparelho="android-02")
    assert mundo.status(flow_id) == "active"


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
    # linha desligada antes da trilha (a rota antiga não a gravava): sem trilha, também é decisão de pessoa
    mundo.db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at)"
                     " VALUES ('legado','legado','abra a conversa com {username} no instagram',"
                     "'abra a conversa com {username} no instagram','{}','instagram','disabled',?)", (now_iso(),))
    run = mundo.execucao("r-4", _plano("@ana"), "abra a conversa com @ana no instagram")
    assert mundo.flows.learn_from_run(run) is None
    assert mundo.status("legado") == "disabled"


def test_devolver_a_prova_tira_o_veto_e_a_prova_recomeca_da_volta(mundo: Mundo) -> None:
    """30.31, item 0: desligar "para validar pela IA" prendia o fluxo (a sombra só olha `candidate`/`validated`, e de
    `disabled` só se saía publicando). Devolver à prova é da pessoa, deixa o fluxo inerte e fora do veto, e só a
    evidência DEPOIS da volta conta."""
    flow_id = mundo.roda("r-1", "@nasa", efeito=True)
    assert flow_id
    mundo.roda("r-2", "@spacex", efeito=True, aparelho="android-02")
    assert mundo.status(flow_id) == "validated"
    mundo.servico.mudar_estado(LivroKind.FLUXO, flow_id, S.DISABLED, by=DONO, reason="validar pela IA antes de valer")
    with pytest.raises(TransicaoProibida):                                     # o sistema não devolve
        mundo.servico.mudar_estado(LivroKind.FLUXO, flow_id, S.CANDIDATE, by=SYSTEM_ACTOR, reason="x")
    with pytest.raises(TransicaoProibida):                                     # e a receita volta pela loja
        mundo.servico.mudar_estado(LivroKind.RECEITA, "1", S.CANDIDATE, by=DONO, reason="x")
    mundo.roda("r-3", "@esa", efeito=True)                                  # desligado: a sombra não olha
    assert mundo.status(flow_id) == "disabled"
    time.sleep(0.005)                                                         # o relógio é de milissegundos
    mundo.servico.mudar_estado(LivroKind.FLUXO, flow_id, S.CANDIDATE, by=DONO, reason="devolver à prova")
    time.sleep(0.005)
    assert mundo.status(flow_id) == "candidate" and mundo.flows.match(_comando("@esa")) is None   # inerte
    assert mundo.trilha(flow_id)[-1] == ("disabled", "candidate", DONO, None)
    # r-1 e r-2 já bastariam (concordâncias=1): não contam mais. Uma execução nova não basta; a segunda valida.
    mundo.roda("r-4", "@esa", efeito=True)
    assert mundo.status(flow_id) == "candidate"
    mundo.roda("r-5", "@roscosmos", efeito=True, aparelho="android-03")
    assert mundo.status(flow_id) == "validated"                               # com efeito, para aqui: é do dono
    assert mundo.trilha(flow_id)[-1] == ("candidate", "validated", "sistema", "r-5")


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


# ------------------------------------------------------------------ a trilha não derruba a loja (22.5)
def test_trilha_que_falha_no_banco_nao_derruba_o_fluxo_nem_com_o_aborto_do_postgres(mundo: Mundo) -> None:
    """O revisor do A5: a trilha é gravada DENTRO da transação da loja, e o erro engolido pelo ouvinte deixava a
    transação abortada no PostgreSQL — o `COMMIT` virava `ROLLBACK`, e `learn_from_run` devolvia o id de um fluxo que
    nunca existiu. Com o savepoint dentro do `try` do ouvinte, só a trilha sai."""
    if mundo.db.dialect != "sqlite":
        pytest.skip("o embrulho imita o PostgreSQL sobre o SQLite")
    run = mundo.execucao("r-1", _plano("@nasa"), _comando("@nasa"))
    pg = embrulhar(mundo.db)
    pg.falhar_em = "INSERT INTO learning_transitions"
    flow_id = mundo.flows.learn_from_run(run)
    assert flow_id and mundo.status(flow_id) == "candidate"                  # o fluxo existe de verdade
    assert pg.commits_perdidos == 0 and mundo.trilha(flow_id) == []          # só a trilha saiu
    assert savepoints(pg) == ["SAVEPOINT sp_1", "ROLLBACK TO SAVEPOINT sp_1", "RELEASE SAVEPOINT sp_1"]
    # o treino também passa pelo ouvinte; e, com a trilha de volta, ela entra junto como sempre
    plano = _plano("{username}")
    plano.parameters = {"username": "{username}"}
    treinado = mundo.flows.learn_from_plan(plano, "abra o perfil de {username}", source="training:t-1")
    assert mundo.status(treinado) == "active" and mundo.trilha(treinado) == [] and pg.commits_perdidos == 0
    pg.falhar_em = None
    outro = _plano("{username}")
    outro.parameters = {"username": "{username}"}
    terceiro = mundo.flows.learn_from_plan(outro, "mostre o perfil de {username}", source="training:t-2")
    assert mundo.trilha(terceiro) == [(None, "published", "training:t-2", None)]


# ------------------------------------------------------------------ adoção por habilidade (22.4)
@pytest.fixture
def adocao(tmp_path: Path) -> Iterator[tuple[Mundo, SqlSkillRepository]]:
    db = banco_migrado(tmp_path, "d1-adocao.sqlite3")
    habilidades = repositorio(db)[0]
    yield Mundo(db, habilidades=habilidades), habilidades
    db.close()


def _trilha_da_adocao(mundo: Mundo, flow_id: str) -> list[tuple[str | None, str, str, str]]:
    return [(t.from_state.value if t.from_state else None, t.to_state.value, t.decided_by, t.reason)
            for t in mundo.repo.trilha(f"fluxo:{flow_id}")]


def test_adotar_e_desfazer_a_adocao_gravam_a_trilha_do_fluxo_com_quem_decidiu(
        adocao: tuple[Mundo, SqlSkillRepository]) -> None:
    mundo, habilidades = adocao
    fluxo_gravado(mundo.db, "curtir", "curtir o post de {perfil}")
    habilidades.adopt_flow("curtir", skill_id="ig.curtir", by=DONO, reason="agora é habilidade")
    assert mundo.status("curtir") == "disabled"
    assert _trilha_da_adocao(mundo, "curtir") == [("published", "disabled", DONO, "adotado pela habilidade ig.curtir")]
    # o mesmo conteúdo e o mesmo escopo que o livro lê: é o que o veto e o "Revisar" procuram
    [t] = mundo.repo.trilha("fluxo:curtir")
    entrada = mundo.servico.entrada(LivroKind.FLUXO, "curtir")
    assert (t.content_hash, t.scope_key) == (entrada.content_hash, entrada.scope_key)
    habilidades.release_flow("ig.curtir", by="painel:Ana Ribeiro", reason="voltar ao fluxo")
    assert mundo.status("curtir") == "active"
    assert _trilha_da_adocao(mundo, "curtir")[1:] == [
        ("disabled", "published", "painel:Ana Ribeiro", "devolvido pela habilidade ig.curtir")]
    assert "fluxo:curtir" in mundo.repo.refs_decididas_por_pessoa((LivroKind.FLUXO,))


def test_publicar_a_versao_de_quem_adotou_desliga_o_fluxo_de_novo_com_trilha(
        adocao: tuple[Mundo, SqlSkillRepository]) -> None:
    """O terceiro lugar que muda `flows.status`: depois de desfeita a adoção, publicar outra versão da habilidade
    que adotou desliga o fluxo na mesma transação (nunca o mesmo comando vivo nos dois lugares)."""
    mundo, habilidades = adocao
    fluxo_gravado(mundo.db, "curtir", "curtir o post de {perfil}")
    habilidades.adopt_flow("curtir", skill_id="ig.curtir", by=DONO)
    habilidades.release_flow("ig.curtir", by=DONO, reason="voltar ao fluxo")
    v2 = habilidades.create_draft("ig.curtir", documento("ig.curtir", "curtir o post de {perfil}", nota="v2"),
                                  source=Provenance(SourceKind.MANUAL), by=DONO)
    habilidades.transition(v2.ref, S.CANDIDATE, by=DONO, reason="submeter")
    habilidades.transition(v2.ref, S.VALIDATED, by=DONO, reason="validada no teste", manual=True)
    habilidades.transition(v2.ref, S.PUBLISHED, by="painel:Ana Ribeiro", reason="publicar a v2")
    assert mundo.status("curtir") == "disabled"
    assert [(de, para, por) for de, para, por, _ in _trilha_da_adocao(mundo, "curtir")] == [
        ("published", "disabled", DONO), ("disabled", "published", DONO),
        ("published", "disabled", "painel:Ana Ribeiro")]
    assert _trilha_da_adocao(mundo, "curtir")[-1][3] == "adotado pela habilidade ig.curtir"


def test_adocao_recusada_ou_com_a_trilha_quebrada_nao_deixa_nada(adocao: tuple[Mundo, SqlSkillRepository],
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """Mesma transação: a adoção que o banco recusa não deixa trilha, e a trilha que falha desfaz a adoção — aqui ela
    é o registro do gesto da pessoa (como no interruptor antigo), não um acessório engolido."""
    mundo, habilidades = adocao
    fluxo_gravado(mundo.db, "curtir", "curtir o post de {perfil}")
    fluxo_gravado(mundo.db, "seguir", "seguir {perfil}")
    habilidades.create_draft("ig.outra", documento("ig.outra", "curtir o post de {perfil}"),
                             source=Provenance(SourceKind.MANUAL), by=DONO)
    ref = SkillRef("ig.outra", 1)
    habilidades.transition(ref, S.CANDIDATE, by=DONO, reason="submeter")
    habilidades.transition(ref, S.VALIDATED, by=DONO, reason="validada no teste", manual=True)
    mundo.db.execute("UPDATE flows SET status='disabled' WHERE id='curtir'")
    habilidades.transition(ref, S.PUBLISHED, by=DONO, reason="publicar")        # o comando agora é da outra
    mundo.db.execute("UPDATE flows SET status='active' WHERE id='curtir'")
    with pytest.raises(DuplicateCommand):
        habilidades.adopt_flow("curtir", skill_id="ig.curtir", by=DONO)
    assert mundo.status("curtir") == "active" and _trilha_da_adocao(mundo, "curtir") == []

    def quebrada(*_: object, **__: object) -> None:
        raise RuntimeError("a trilha caiu")

    assert habilidades.flow_trail is not None
    monkeypatch.setattr(habilidades.flow_trail, "flow_status_changed", quebrada)
    with pytest.raises(RuntimeError, match="a trilha caiu"):
        habilidades.adopt_flow("seguir", skill_id="ig.seguir", by=DONO)
    assert mundo.status("seguir") == "active" and habilidades.definition("ig.seguir") is None
    assert habilidades.published("ig.seguir") is None
    with pytest.raises(TransitionForbidden):                                     # a trilha nunca fica sem autor
        habilidades.release_flow("ig.curtir", by="  ", reason="sem quem")


def test_insert_da_trilha_que_falha_desfaz_a_adocao_e_a_devolucao_sem_engolir(
        adocao: tuple[Mundo, SqlSkillRepository]) -> None:
    """Revisor do 22.4: a regra "a trilha que falha desfaz a adoção" só estava provada com um dublê no lugar da
    `TrilhaDaAdocao` — uma `TrilhaDaAdocao` que engolisse a falha passava. Aqui quem falha é o INSERT de verdade, com o
    aborto do PostgreSQL imitado: o erro sobe, nada fica (nem a definição, nem o fluxo desligado), nenhum savepoint o
    engole e nenhum `COMMIT` se perde. Vale para desfazer a adoção também: o fluxo não religa sem a trilha.

    É a política de hoje (a mesma do interruptor antigo, `mudar_status_nativo`); se o dono decidir que a trilha é
    acessória também aqui, este teste muda junto com ela."""
    mundo, habilidades = adocao
    if mundo.db.dialect != "sqlite":
        pytest.skip("o embrulho imita o PostgreSQL sobre o SQLite")
    fluxo_gravado(mundo.db, "curtir", "curtir o post de {perfil}")
    pg = embrulhar(mundo.db)
    pg.falhar_em = "INSERT INTO learning_transitions"
    with pytest.raises(OPERATIONAL_ERRORS, match="falha de teste"):
        habilidades.adopt_flow("curtir", skill_id="ig.curtir", by=DONO)
    assert mundo.status("curtir") == "active" and habilidades.definition("ig.curtir") is None
    assert mundo.db.one("SELECT id FROM skill_definitions WHERE id='ig.curtir'") is None
    assert pg.commits_perdidos == 0 and savepoints(pg) == [] and not pg.abortada
    pg.falhar_em = None
    habilidades.adopt_flow("curtir", skill_id="ig.curtir", by=DONO)
    pg.falhar_em = "INSERT INTO learning_transitions"
    with pytest.raises(OPERATIONAL_ERRORS, match="falha de teste"):
        habilidades.release_flow("ig.curtir", by=DONO, reason="voltar ao fluxo")
    assert mundo.status("curtir") == "disabled" and habilidades.published("ig.curtir") is not None
    assert pg.commits_perdidos == 0 and savepoints(pg) == []
    assert _trilha_da_adocao(mundo, "curtir") == [("published", "disabled", DONO, "adotado pela habilidade ig.curtir")]


def test_o_sistema_nao_adota_nem_devolve_o_fluxo(adocao: tuple[Mundo, SqlSkillRepository]) -> None:
    """Revisor do 22.4: a fronteira do repositório só conferia `by.strip()`. Pelo sistema, adotar gravaria na trilha
    um desligamento do sistema (veto de 90 dias sobre o conteúdo) e devolver gravaria `disabled → published`, que o
    livro só aceita de pessoa. A devolução é provada com a versão já desabilitada à mão: sem versão publicada, nada
    antes da guarda recusaria o sistema."""
    mundo, habilidades = adocao
    fluxo_gravado(mundo.db, "curtir", "curtir o post de {perfil}")
    with pytest.raises(TransitionForbidden, match="pessoa"):
        habilidades.adopt_flow("curtir", skill_id="ig.curtir", by=SYSTEM_ACTOR)
    assert mundo.status("curtir") == "active" and habilidades.definition("ig.curtir") is None
    habilidades.adopt_flow("curtir", skill_id="ig.curtir", by=DONO)
    habilidades.transition(SkillRef("ig.curtir", 1), S.DISABLED, by=DONO, reason="parada de emergência")
    with pytest.raises(TransitionForbidden, match="pessoa"):
        habilidades.release_flow("ig.curtir", by=SYSTEM_ACTOR, reason="o sistema quis")
    assert mundo.status("curtir") == "disabled"
    assert _trilha_da_adocao(mundo, "curtir") == [("published", "disabled", DONO, "adotado pela habilidade ig.curtir")]
    habilidades.release_flow("ig.curtir", by=DONO, reason="voltar ao fluxo")        # a pessoa, sim
    assert mundo.status("curtir") == "active"


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
    decisoes = [e["message"] or "" for e in state.db.query(
        "SELECT message FROM events WHERE run_id=? AND kind='decision'", (first.id,))]
    # O nascimento é anunciado UMA vez, pela sombra (o `_learn_flow` se cala no candidato com o aprendizado ligado),
    # e diz o status REAL: candidato e inerte, e o que falta (a execução do Harness é simulada) — nunca mais
    # "reaproveitam este plano" para um fluxo que ainda não é reaproveitado.
    assert not any("reaproveitam este plano sem chamar o planejador" in m for m in decisoes)
    [nascimento] = [m for m in decisoes if "aprendido como candidato" in m]
    assert nascimento.startswith(f"Fluxo “{fluxo['id']}” aprendido como candidato (D1): ainda não é reaproveitado")
    assert "Execução simulada não é prova (a que o gerou não conta)" in nascimento
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


async def test_no_central_o_nascimento_e_anunciado_uma_vez_com_a_config_vigente(harness: Harness) -> None:
    """A fiação real, da config ao texto: o `_learn_flow` do scheduler e a sombra que a montagem do central liga, os
    dois lendo o bloco `aprendizado` VIGENTE (`concordancias`, `enabled`, `com_prova`) e o plano do fluxo (efeito
    externo). Cada execução anuncia o nascimento UMA vez, com o texto do caso. As execuções do Harness são simuladas: a
    que gerou o fluxo não conta, e faltam as `1 + concordancias` reais inteiras."""
    state = harness.state
    assert state is not None
    aprendizado = harness.cfg.file.aprendizado
    harness.cfg.file.ai.flows = True
    aprendizado.fluxo.com_prova = True
    aprendizado.fluxo.concordancias = 2

    async def nascimento(comando: str, sinal: str) -> tuple[Any, list[str]]:
        run = await harness.wait_run(harness.run(["android-01"], command=comando).id)
        assert run.status == "completed", run.status

        def decisoes() -> list[str]:
            return [e["message"] or "" for e in state.db.query(
                "SELECT message FROM events WHERE run_id=? AND kind='decision'", (run.id,))]

        # O anúncio esperado saiu → o digest desta execução já foi encadeado (no mesmo `finally` do worker, antes do
        # `_learn_flow`); esperá-lo terminar é o que prova que a outra metade não anunciou de novo.
        await harness.wait(lambda: any(sinal in m for m in decisoes()), 10, f"decisão com “{sinal}”")
        await harness.wait(lambda: not state._digestoes, 10, "digest encerrado")  # noqa: SLF001
        fluxo = state.db.one("SELECT id, status FROM flows WHERE source_run_id=?", (run.id,))
        assert fluxo is not None
        return fluxo, [m for m in decisoes() if f"Fluxo “{fluxo['id']}”" in m]

    # etapa de efeito externo (o envio do comando padrão), concordancias=2: só a sombra anuncia, e o dono publica
    fluxo, [texto] = await nascimento(COMMAND, "aprendido como candidato")
    assert fluxo["status"] == "candidate"
    assert texto.startswith(f"Fluxo “{fluxo['id']}” aprendido como candidato (D1): ainda não é reaproveitado")
    assert "Execução simulada não é prova (a que o gerou não conta)" in texto
    assert texto.endswith("depois de mais 3 execução(ões) real(is) com o mesmo plano ele fica validado, e o dono o "
                          "publica (tem etapa de efeito externo)")
    # sem efeito externo: o sistema publica depois das reais
    fluxo, [texto] = await nascimento("Abra o QA Messenger e confirme a conta conectada.", "aprendido como candidato")
    assert fluxo["status"] == "candidate"
    assert texto.endswith("depois de mais 3 execução(ões) real(is) com o mesmo plano o sistema o publica (sem efeito "
                          "externo)")
    # aprendizado desligado: o digest não roda (nenhuma evidência), e o anúncio é do `_learn_flow`
    aprendizado.enabled = False
    fluxo, [texto] = await nascimento("Abra o QA Messenger e confira a conta de teste.", "só uma pessoa o publica")
    assert fluxo["status"] == "candidate"
    assert texto.startswith(f"Fluxo “{fluxo['id']}” aprendido como candidato (D1): comandos iguais ainda NÃO")
    assert state.db.scalar("SELECT COUNT(*) FROM learning_evidence WHERE item_ref=?", (f"fluxo:{fluxo['id']}",)) == 0
    # o modo anterior (`com_prova: false`): nasce ativo, e só o `_learn_flow` fala (a sombra não o vê)
    aprendizado.enabled, aprendizado.fluxo.com_prova = True, False
    fluxo, [texto] = await nascimento("Abra o QA Messenger e verifique a conta.", "reaproveitam este plano")
    assert fluxo["status"] == "active"
    assert texto == (f"Fluxo “{fluxo['id']}” salvo: comandos iguais (com outros valores) reaproveitam este plano sem "
                     "chamar o planejador")


# ------------------------------------------------------------------ a decisão do `_learn_flow` (texto verdadeiro)
def _learn_flow(mundo: Mundo, run_id: str, *, ligado: bool = True,
                decisao: Callable[[str, str], None] | None = None) -> list[str]:
    """`Scheduler._learn_flow` sobre a loja do mundo, sem subir o central: só o que ele lê (config, execução) e a
    decisão que grava (`decisao` troca quem grava — para provar que a falha dela não sobe)."""
    decisoes: list[str] = []
    aprendizado = SimpleNamespace(enabled=ligado, fluxo=SimpleNamespace(concordancias=mundo.config["concordancias"],
                                                                        com_prova=mundo.config["com_prova"]))
    falso = SimpleNamespace(
        cfg=SimpleNamespace(file=SimpleNamespace(ai=SimpleNamespace(flows=True), aprendizado=aprendizado)),
        repo=SimpleNamespace(run_row=lambda rid: mundo.db.one("SELECT * FROM runs WHERE id=?", (rid,)),
                             decision=decisao or (lambda texto, run_id: decisoes.append(texto))),
        _pathfinders={}, flows=mundo.flows)
    Scheduler._learn_flow(falso, run_id)                                     # type: ignore[arg-type]
    return decisoes


def test_learn_flow_anuncia_so_o_que_a_sombra_nao_anuncia(mundo: Mundo) -> None:
    """Um dono só para o anúncio do nascimento: com o aprendizado ligado, o candidato é da sombra (o digest); o
    `_learn_flow` fala do que nasce ativo e, com o aprendizado desligado (o digest não roda), do candidato."""
    mundo.execucao("r-1", _plano("@nasa"), _comando("@nasa"))
    assert _learn_flow(mundo, "r-1") == []                                   # candidato, aprendizado ligado: calado
    [fluxo] = mundo.db.query("SELECT id, status FROM flows")
    assert fluxo["status"] == "candidate"
    mundo.servico.digerir_execucao("r-1")
    [nascimento] = [t for r, t in mundo.decisoes if r == "r-1"]
    assert nascimento.startswith(f"Fluxo “{fluxo['id']}” aprendido como candidato (D1): ainda não é reaproveitado")
    assert "depois de mais 1 execução(ões) real(is) com o mesmo plano o sistema o publica" in nascimento
    # aprendizado desligado: nenhuma execução o promove, e o anúncio fica com o `_learn_flow`
    mundo.execucao("r-2", _plano("@nasa"), "seguir o perfil de @nasa no instagram")
    [desligado] = _learn_flow(mundo, "r-2", ligado=False)
    assert "aprendido como candidato (D1): comandos iguais ainda NÃO reaproveitam este plano" in desligado
    assert desligado.endswith("nenhuma execução o promove: só uma pessoa o publica")
    # o modo anterior (`com_prova: false`): nasce ativo, e aí sim reaproveita — a sombra não o vê (não está em prova)
    mundo.config["com_prova"] = False
    mundo.execucao("r-3", _plano("@nasa"), "curtir o perfil de @nasa no instagram")
    [ativo] = _learn_flow(mundo, "r-3")
    assert "salvo: comandos iguais (com outros valores) reaproveitam este plano sem chamar o planejador" in ativo
    mundo.servico.digerir_execucao("r-3")
    assert [t for r, t in mundo.decisoes if r == "r-3"] == []
    # nada aprendido (o comando já tem fluxo): nada dito
    assert _learn_flow(mundo, "r-1") == []


def test_learn_flow_que_falha_ao_anunciar_nao_derruba_o_fim_da_execucao(mundo: Mundo) -> None:
    """Roda no `finally` do worker: a leitura do status e a decisão ficam sob o `try` do aprendizado (a exceção que
    subisse dali pularia o `wake()`). O fluxo aprendido fica; só o anúncio se perde, com log."""
    def explode(texto: str, run_id: str) -> None:
        raise RuntimeError("linha do tempo fora do ar")

    mundo.config["com_prova"] = False
    mundo.execucao("r-1", _plano("@nasa"), _comando("@nasa"))
    assert _learn_flow(mundo, "r-1", decisao=explode) == []                  # não levanta
    assert mundo.db.scalar("SELECT status FROM flows") == "active"


def test_texto_do_fluxo_salvo_so_fala_do_que_a_sombra_nao_anuncia() -> None:
    for ligado in (True, False):
        ativo = texto_do_fluxo_salvo("f", "active", aprendizado_ligado=ligado)
        assert ativo and "reaproveitam este plano sem chamar o planejador" in ativo
        outro = texto_do_fluxo_salvo("f", "validated", aprendizado_ligado=ligado)
        assert outro and "não o reaproveitam enquanto ele não estiver ativo" in outro
    assert texto_do_fluxo_salvo("f", "candidate", aprendizado_ligado=True) is None      # é da sombra
    desligado = texto_do_fluxo_salvo("f", "candidate", aprendizado_ligado=False)
    assert desligado and desligado.endswith("nenhuma execução o promove: só uma pessoa o publica")
    assert "sem chamar o planejador" not in desligado and "ainda NÃO reaproveitam" in desligado


# ------------------------------------------------------------------ a rota antiga passa pelo livro (Harness)
def _cliente(h: Harness, *, base: str = "http://test") -> httpx.AsyncClient:
    """`base="http://127.0.0.1"` para a sessão, como em `test_sessao_do_painel`: o login do loopback dispensa token,
    e o jar do cliente devolve o cookie nas requisições seguintes."""
    from app.main import create_app

    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base)


async def test_pela_rota_antiga_a_pessoa_decide_com_trilha_e_o_sistema_nao_reaprende(tmp_path: Path) -> None:
    """`PUT /api/flows/{id}` grava a trilha com a pessoa. Antes não gravava: depois de a pessoa religar e desligar um
    fluxo que o SISTEMA tinha refutado, a última linha da trilha seguia sendo a refutação, e o próximo plano
    comprovado fazia o fluxo renascer candidato (e a sombra podia publicá-lo de novo)."""
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        assert s is not None
        mundo = Mundo(s.db)
        if s.db.one("SELECT id FROM apps WHERE id='instagram'") is None:
            s.db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',"
                         "'com.instagram.android',0)")
        flow_id = _refutado(mundo)                                          # desligado pelo sistema
        async with _cliente(h) as c:
            liga = await c.put(f"/api/flows/{flow_id}", json={"status": "active"})
            assert liga.status_code == 200, liga.text
            assert liga.json()["id"] == flow_id and liga.json()["status"] == "active"   # a resposta de sempre
            desliga = await c.put(f"/api/flows/{flow_id}", json={"status": "disabled"})
            assert desliga.status_code == 200 and desliga.json()["status"] == "disabled"
            assert (await c.put(f"/api/flows/{flow_id}", json={"status": "disabled"})).status_code == 200
            assert (await c.put(f"/api/flows/{flow_id}", json={"status": "quarantined"})).status_code == 400
        assert mundo.trilha(flow_id)[-2:] == [("disabled", "published", "panel", None),
                                              ("published", "disabled", "panel", None)]
        assert s.db.scalar("SELECT reason FROM learning_transitions WHERE item_ref=? ORDER BY id DESC LIMIT 1",
                           (f"fluxo:{flow_id}",)) == "desligado na lista de fluxos do painel"
        # o próximo plano comprovado (outro caminho, o mesmo comando) não o faz renascer: quem desligou foi a pessoa
        assert mundo.roda("r-4", "@nasa", extra=True) is None
        assert mundo.status(flow_id) == "disabled"
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_pela_rota_antiga_a_trilha_diz_o_operador_e_o_gesto_recusado_no_meio_nao_deixa_nada(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Com sessão aberta, quem decide na trilha é o nome do login (o `_quem` do livro), não `panel`. E ligar o fluxo em
    prova são dois passos do livro (candidato → validado → publicado) num gesto só, na transação da rota: recusado o
    segundo, o primeiro também não fica — nem o status, nem a linha na trilha."""
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        assert s is not None
        mundo = Mundo(s.db)
        if s.db.one("SELECT id FROM apps WHERE id='instagram'") is None:
            s.db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',"
                         "'com.instagram.android',0)")
        flow_id = mundo.roda("r-1", "@nasa")
        assert flow_id and mundo.status(flow_id) == "candidate"
        nascimento = mundo.trilha(flow_id)
        # A guarda do livro recusa o passo validado → publicado — o que faria uma habilidade publicada com o mesmo
        # comando que chegasse depois da conferência da rota. O passo candidato → validado não a consulta.
        monkeypatch.setattr(s.learning._repo, "_guarda_do_fluxo", lambda ref: "recusado pelo teste")  # noqa: SLF001
        async with _cliente(h, base="http://127.0.0.1") as c:
            entrada = await c.post("/api/login", json={"operator": NOME})
            assert entrada.status_code == 200, entrada.text
            recusa = await c.put(f"/api/flows/{flow_id}", json={"status": "active"})
            assert recusa.status_code == 409, recusa.text
            assert recusa.json()["detail"]["code"] == "state_conflict"
            assert mundo.status(flow_id) == "candidate" and mundo.trilha(flow_id) == nascimento
            monkeypatch.undo()
            liga = await c.put(f"/api/flows/{flow_id}", json={"status": "active"})
            assert liga.status_code == 200, liga.text
        assert mundo.status(flow_id) == "active"
        assert mundo.trilha(flow_id) == [*nascimento, ("candidate", "validated", NOME, None),
                                         ("validated", "published", NOME, None)]
    finally:
        if h.state is not None:
            await h.state.stop()
