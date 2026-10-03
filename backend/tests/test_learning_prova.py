"""30.37: a EXECUÇÃO DE PROVA valida um fluxo pelo próprio fluxo (adendo v0.97 do contrato; emenda ao ADR-054 D1).

O que se prova, e como: o plano é o do fluxo e o planejador não é chamado; a prova não é reuso (nem `flow_id`, nem
`uses`) nem ensina fluxo; a etapa que pediria uma pessoa encerra a execução PELO SISTEMA, sem sinal, sem pergunta,
sem aviso e sem aprovação aberta; o minerador grava UMA linha de evidência do fluxo provado, pelas etapas; a prova
nunca é comparável na sombra (30.36).

Duas armações. Os casos 1, 3 e 4 rodam no `Harness` (porta 5640): `RunService`, escalonador e `FakeQaDevice` de
verdade, provedor simulado contado (`CountingProvider`). Os casos 2, 5 e 6 rodam sobre o banco migrado e o `Mundo`
do D1 (`test_d1_fluxos`): o digest minera execuções assentadas à mão, com os estados de etapa e tentativa de cada caso.

Os avisos: o aviso fora do painel nasce só de `needs_input`, aprovação e sessão (`aviso_de_evento`), e uma execução
`waiting_user` COMUM também não enfileira nenhum; o teste confere o que a prova poderia soltar (fila, eventos que
viram aviso, pendências), não que a execução comum enfileire. Nível de prova: `simulated` (nenhum aparelho, nenhuma
IA, nenhum central).
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from app.db import Database
from app.models import Plan, PlannerInfo, PlanStep, Postcondition, RunCreate, StepResult
from app.modules.avisos.domain.mensagem import aviso_de_evento
from app.modules.learning.application.nativos import SombraDosFluxos, marca_do_conteudo
from app.modules.learning.infrastructure.ligar_nativos import LeituraSql
from app.util import now_iso

from .conftest import COMMAND, Harness
from .fake_skills import banco as banco_migrado
from .test_d1_fluxos import Mundo, _comando, _plano

MOTIVO = "Prova de fluxo (validação): a etapa precisaria de uma pessoa"


# ===================================================================== armação 1: o Harness (casos 1, 3 e 4)
@dataclass
class Real:
    h: Harness
    flow_id: str
    learn: list[str] = field(default_factory=list)       # `learn_from_run` chamadas, na ordem (id da execução)
    sombra: list[str] = field(default_factory=list)      # `_intencao_em_sombra` chamadas, na ordem

    @property
    def db(self) -> Database:
        assert self.h.state is not None
        return self.h.state.db

    def prova(self, chave: str) -> str:
        """A execução de prova do fluxo, com o comando de origem."""
        assert self.h.state is not None
        return self.h.state.runs.create(RunCreate(command=COMMAND, instance_ids=["android-01"], mode="execute",
                                                  idempotency_key=chave), prova=self.flow_id).id


@pytest_asyncio.fixture
async def real(tmp_path: Path) -> AsyncIterator[Real]:
    """Uma execução comum ensina o fluxo (o planejador simulado é chamado 1 vez); o fluxo vira `candidate`. Os espiões
    ficam instalados ANTES dela, para que cada contagem tenha o controle positivo da execução comum."""
    h = Harness(tmp_path, 1)
    h.cfg.file.ai.flows = True
    await h.boot()
    try:
        s = h.state
        assert s is not None
        h.pular_o_tempo()
        r = Real(h, "")
        aprende = s.scheduler.flows.learn_from_run
        em_sombra = s.runs._intencao_em_sombra                          # noqa: SLF001 - o espião é o ponto

        def espia_aprende(run: Any) -> str | None:
            r.learn.append(str(run["id"]))
            return aprende(run)

        def espia_sombra(run_id: str) -> None:
            r.sombra.append(run_id)
            em_sombra(run_id)

        s.scheduler.flows.learn_from_run = espia_aprende                # type: ignore[method-assign]
        s.runs._intencao_em_sombra = espia_sombra                       # type: ignore[method-assign]
        origem = h.run(["android-01"])
        assert (await h.wait_run(origem.id)).status == "completed"
        assert r.learn == [origem.id] and r.sombra == [origem.id]       # o controle: a execução comum aprende e vira sombra
        [flow_id] = [str(x["id"]) for x in s.db.query("SELECT id FROM flows")]
        s.db.execute("UPDATE flows SET status='candidate' WHERE id=?", (flow_id,))
        r.flow_id = flow_id
        yield r
    finally:
        if h.state is not None:
            await h.state.stop()


def _plano_da_execucao(db: Database, run_id: str) -> Plan:
    return Plan.model_validate_json(str(db.scalar("SELECT plan FROM runs WHERE id=?", (run_id,))))


# ------------------------------------------------------------------ 1. o plano é o do fluxo
async def test_prova_roda_o_plano_do_fluxo_sem_planejador_e_sem_sombra(real: Real) -> None:
    antes = real.h.ai.count("plan")
    run = real.prova("prova-caso-1")
    detalhe = await real.h.wait_run(run)
    assert detalhe.status == "completed"
    assert real.h.ai.count("plan") == antes                              # o provedor falso não recebeu plano nenhum
    assert run not in real.sombra                                        # `_sem_sombra`: a sombra da intenção não a vê
    plano = _plano_da_execucao(real.db, run)
    assert plano.planner.provider == "fluxo-prova" and real.flow_id in plano.planner.model
    decisoes = [str(x["message"]) for x in real.db.query(
        "SELECT message FROM events WHERE run_id=? AND kind='decision'", (run,))]
    assert any(d.startswith("Prova de fluxo (validação): o plano é o do fluxo " + real.flow_id) for d in decisoes)
    assert any("o planejador não é chamado" in d for d in decisoes)
    assert real.db.scalar("SELECT prova_fluxo_id FROM runs WHERE id=?", (run,)) == real.flow_id


# ------------------------------------------------------------------ 3. a prova não é reuso e não ensina
async def test_depois_da_prova_nao_ha_reuso_nem_fluxo_novo(real: Real) -> None:
    antes = real.db.one("SELECT uses, last_used_at FROM flows WHERE id=?", (real.flow_id,))
    assert antes is not None
    run = real.prova("prova-caso-3")
    assert (await real.h.wait_run(run)).status == "completed"
    assert real.db.scalar("SELECT flow_id FROM runs WHERE id=?", (run,)) is None
    depois = real.db.one("SELECT uses, last_used_at FROM flows WHERE id=?", (real.flow_id,))
    assert depois is not None and (depois["uses"], depois["last_used_at"]) == (antes["uses"], antes["last_used_at"])
    assert run not in real.learn                                         # `learn_from_run` nem foi chamado
    assert real.db.scalar("SELECT COUNT(*) FROM flows") == 1
    assert real.db.scalar("SELECT skill_hash FROM runs WHERE id=?", (run,)) is None


# ------------------------------------------------------------------ 4. a prova nunca espera uma pessoa
def _nada_para_a_pessoa(db: Database, run_id: str) -> None:
    """Nenhum rastro que chamaria uma pessoa, nem o gesto que ninguém fez."""
    assert db.scalar("SELECT COUNT(*) FROM learning_signals WHERE kind='cancelou_execucao' AND run_id=?",
                     (run_id,)) == 0
    assert db.scalar("SELECT COUNT(*) FROM avisos_entregas") == 0
    assert db.scalar("SELECT COUNT(*) FROM pending_approvals WHERE run_id=? AND status='pending'", (run_id,)) == 0
    for e in db.query("SELECT id, kind, data FROM events WHERE run_id=?", (run_id,)):
        dados = json.loads(e["data"]) if e["data"] else None
        assert aviso_de_evento(str(e["kind"]), dados, int(e["id"])) is None, e["kind"]
    obj = db.one("SELECT needs, blocked_reason, blocked_kind FROM objectives WHERE run_id=?", (run_id,))
    assert obj is not None and obj["needs"] is None and obj["blocked_kind"] is None
    assert db.scalar("SELECT COUNT(*) FROM events WHERE run_id=? AND kind='question.asked'", (run_id,)) == 0


def _encerrada_pelo_sistema(db: Database, run_id: str) -> None:
    run = db.one("SELECT status, cancel_requested FROM runs WHERE id=?", (run_id,))
    assert run is not None and run["status"] == "cancelled" and run["cancel_requested"] == 1
    obj = db.one("SELECT status, status_detail FROM objectives WHERE run_id=?", (run_id,))
    assert obj is not None and obj["status"] == "cancelled" and str(obj["status_detail"]).startswith(MOTIVO)
    # A etapa que pediu a pessoa é a PRIMEIRA cancelada com o motivo: as seguintes do objetivo fecham com o mesmo texto
    # (`cancel_open_steps`) e sem tentativa. Sem `ORDER BY` o PostgreSQL pode devolver uma delas (suíte 14, PG -n 3).
    cancelada = db.one("SELECT id, status, status_detail FROM steps WHERE run_id=? AND status='cancelled'"
                       " AND status_detail LIKE ? ORDER BY seq", (run_id, MOTIVO + "%"))
    assert cancelada is not None
    tentativa = db.one("SELECT status FROM attempts WHERE step_id=? ORDER BY number DESC", (cancelada["id"],))
    assert tentativa is not None and tentativa["status"] == "interrupted"
    assert db.scalar("SELECT COUNT(*) FROM objectives WHERE run_id=? AND status='waiting_user'", (run_id,)) == 0


async def test_prova_com_etapa_que_pede_pessoa_e_cancelada_pelo_sistema(real: Real) -> None:
    real.h.fakes["android-01"].screen = "launcher"                       # o app reabre do zero: a tela de login aparece
    real.h.fakes["android-01"].require_login = True                      # a etapa daria `waiting_user`
    run = real.prova("prova-caso-4a")
    await real.h.wait_run(run)
    _encerrada_pelo_sistema(real.db, run)
    _nada_para_a_pessoa(real.db, run)
    # O controle: a MESMA situação numa execução comum segue o caminho de sempre (e o gesto de cancelar, sim, é sinal).
    comum = real.h.run(["android-01"])
    await real.h.wait_run(comum.id)
    obj = real.db.one("SELECT status, blocked_reason FROM objectives WHERE run_id=?", (comum.id,))
    assert obj is not None and obj["status"] == "waiting_user" and "autentica" in str(obj["blocked_reason"]).lower()
    assert real.db.scalar("SELECT COUNT(*) FROM learning_signals WHERE kind='cancelou_execucao'") == 0
    assert real.h.state is not None
    real.h.state.runs.cancel(comum.id, por="operador-teste")
    assert real.db.scalar("SELECT COUNT(*) FROM learning_signals WHERE kind='cancelou_execucao' AND run_id=?",
                          (comum.id,)) == 1


async def test_prova_com_resultado_incerto_e_cancelada_pelo_sistema(real: Real) -> None:
    real.h.encurtar_verificacao()
    real.h.fakes["android-01"].screen = "launcher"
    real.h.fakes["android-01"].send_fault = "error_lost"                 # o toque se perde: a tela não prova o envio
    run = real.prova("prova-caso-4b")
    await real.h.wait_run(run, timeout=90)
    _encerrada_pelo_sistema(real.db, run)
    _nada_para_a_pessoa(real.db, run)
    assert real.db.scalar("SELECT COUNT(*) FROM objectives WHERE run_id=? AND status='uncertain'", (run,)) == 0
    real.h.fakes["android-01"].screen = "launcher"
    real.h.fakes["android-01"].send_fault = "error_lost"                 # o defeito injetado é de um disparo só
    comum = real.h.run(["android-01"])                                    # o controle: a execução comum fica incerta
    await real.h.wait_run(comum.id, timeout=90)
    assert real.db.scalar("SELECT status FROM objectives WHERE run_id=?", (comum.id,)) == "uncertain"


async def test_prova_encerrada_pelo_sistema_expira_a_aprovacao_pendente(real: Real) -> None:
    """A aprovação que nasce ANTES de o desfecho chegar (`approval.pending`) sai expirada e não sobra em Pendências."""
    s = real.h.state
    assert s is not None
    original = s.scheduler._prova_sem_pessoa                              # noqa: SLF001

    def com_aprovacao_aberta(run_id: str, objective_id: str, step_id: str, *args: Any, **kw: Any) -> bool:
        s.db.execute("INSERT INTO pending_approvals(id, run_id, objective_id, step_id, capability, summary, status,"
                     " created_at) VALUES (?,?,?,?,?,?,?,?)",
                     ("ap-prova-1", run_id, objective_id, step_id, "SEND_MESSAGE", "enviar mensagem", "pending",
                      now_iso()))
        return bool(original(run_id, objective_id, step_id, *args, **kw))

    s.scheduler._prova_sem_pessoa = com_aprovacao_aberta                  # type: ignore[method-assign]
    real.h.fakes["android-01"].screen = "launcher"
    real.h.fakes["android-01"].require_login = True
    run = real.prova("prova-caso-4c")
    await real.h.wait_run(run)
    _encerrada_pelo_sistema(real.db, run)
    aprovacao = real.db.one("SELECT status, decided_note FROM pending_approvals WHERE id='ap-prova-1'")
    assert aprovacao is not None and aprovacao["status"] == "expired"
    assert str(aprovacao["decided_note"]).startswith("Prova de fluxo (validação)")
    assert s.approvals.list(status="pending", run_id=run) == []


async def test_prova_encerrada_pelo_sistema_nao_deixa_etapa_aberta(real: Real) -> None:
    real.h.fakes["android-01"].screen = "launcher"
    real.h.fakes["android-01"].require_login = True
    run = real.prova("prova-caso-4d")
    await real.h.wait_run(run)
    _encerrada_pelo_sistema(real.db, run)
    abertas = [(str(e["key"]), str(e["status"])) for e in real.db.query(
        "SELECT key, status FROM steps WHERE run_id=? AND status NOT IN ('succeeded','cancelled','failed')", (run,))]
    assert abertas == []


# ===================================================================== armação 2: o banco e o Mundo (casos 2, 5 e 6)
@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    db = banco_migrado(tmp_path, "prova-fluxos.sqlite3")
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram','com.instagram.android',0)")
    yield Mundo(db)
    db.close()


def _fluxo_unico(mundo: Mundo) -> str:
    [linha] = mundo.db.query("SELECT id FROM flows")
    return str(linha["id"])


def _prova(mundo: Mundo, run_id: str, flow_id: str, etapas: list[tuple[str, str]], *, status: str = "completed",
           erro: dict[str, str] | None = None, simulada: bool = False, usuario: str = "@nasa",
           agiu: bool = True) -> None:
    """Uma execução de prova assentada. `etapas`: `(chave, estado)`; `erro`: `chave -> error_kind` da última tentativa.
    O plano gravado é o MESMO de uma execução comum comparável, para que só `prova_fluxo_id` a faça diferente."""
    db = mundo.db
    plano = _plano(usuario)
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, plan, created_at,"
               " prova_fluxo_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (run_id, f"k-{run_id}", _comando(usuario), "execute", status, int(simulada),
                json.dumps(["android-01"]), plano.model_dump_json(), now_iso(), flow_id))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,?)",
               (f"{run_id}:o1", run_id, "android-01", "succeeded" if status == "completed" else "cancelled", 1))
    for seq, (chave, estado) in enumerate(etapas, start=1):
        sid = f"{run_id}:{chave}"
        db.execute(
            "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
            " postcondition, timeout_s, max_attempts, status, status_detail, result) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, run_id, f"{run_id}:o1", "android-01", 1, seq, chave, chave, chave,
             Postcondition(kind="text_visible", value="x", description="x").model_dump_json(), 60, 3, estado,
             "pós-condição não comprovada" if estado == "failed" else None,
             StepResult(verified=estado == "succeeded", evidence_text="visto na tela").model_dump_json()))
        if estado != "pending":
            db.execute("INSERT INTO attempts(id, step_id, number, status, started_at, error_kind) VALUES (?,?,?,?,?,?)",
                       (f"{sid}:a1", sid, 1, {"succeeded": "succeeded", "failed": "failed"}.get(estado, "interrupted"),
                        now_iso(), (erro or {}).get(chave)))
            if agiu:                  # 30.42: a etapa reprovada só conta contra se o ator AGIU (um toque no diário)
                db.execute("INSERT INTO actions(attempt_id, seq, tool, args, status, intent_at) VALUES (?,?,?,?,?,?)",
                           (f"{sid}:a1", 1, "tap", "{}", "done", now_iso()))


def _evidencias(mundo: Mundo, flow_id: str, run_id: str | None = None) -> list[tuple[str, str, str]]:
    q, p = "SELECT stance, origin_ref, detail FROM learning_evidence WHERE item_ref=?", [f"fluxo:{flow_id}"]
    if run_id:
        q += " AND run_id=?"
        p.append(run_id)
    return [(str(r["stance"]), str(r["origin_ref"]), str(r["detail"])) for r in mundo.db.query(q + " ORDER BY id", tuple(p))]


def _digerir(mundo: Mundo, run_id: str) -> None:
    relatorio = mundo.servico.digerir_execucao(run_id)
    assert not relatorio.falhas, relatorio


def _candidato(mundo: Mundo, *, concordancias: int = 1) -> str:
    mundo.config["concordancias"] = concordancias
    mundo.roda("r-1", "@nasa")
    flow_id = _fluxo_unico(mundo)
    assert mundo.status(flow_id) == "candidate"
    return flow_id


# ------------------------------------------------------------------ 2. o match não vê o candidato; a prova vê
def test_match_so_ve_o_ativo_e_a_prova_ve_candidato_validado_e_ativo(mundo: Mundo) -> None:
    flow_id = _candidato(mundo)
    comando = _comando("@bia.2")
    for estado, no_match in (("candidate", False), ("validated", False), ("active", True)):
        mundo.db.execute("UPDATE flows SET status=? WHERE id=?", (estado, flow_id))
        assert (mundo.flows.match(comando) is not None) is no_match, estado
        plano = mundo.flows.plano_em_prova(flow_id, comando)
        assert plano is not None, estado
        assert plano.planner.provider == "fluxo-prova" and plano.parameters == {"username": "@bia.2"}
    mundo.db.execute("UPDATE flows SET status='disabled' WHERE id=?", (flow_id,))
    assert mundo.flows.plano_em_prova(flow_id, comando) is None          # desligado: a prova recusa
    mundo.db.execute("UPDATE flows SET status='candidate' WHERE id=?", (flow_id,))
    assert mundo.flows.plano_em_prova(flow_id, "mande bom dia para @ana no whatsapp") is None   # fora do molde
    assert mundo.flows.plano_em_prova("nao-existe", comando) is None


# ------------------------------------------------------------------ 5. o minerador
def test_prova_toda_comprovada_deixa_uma_linha_a_favor_com_a_marca_do_conteudo(mundo: Mundo) -> None:
    flow_id = _candidato(mundo)
    marca = marca_do_conteudo(mundo_hash(mundo, flow_id))
    antes = len(_evidencias(mundo, flow_id))
    _prova(mundo, "p-1", flow_id, [("abrir_perfil", "succeeded"), ("seguir", "succeeded"), ("ver", "succeeded")])
    _digerir(mundo, "p-1")
    [(stance, origem, detalhe)] = _evidencias(mundo, flow_id, "p-1")
    assert (stance, origem) == ("for", "run:p-1")
    assert detalhe.startswith(marca + " ") and "3/3 etapas comprovadas" in detalhe
    assert len(_evidencias(mundo, flow_id)) == antes + 1
    assert re.fullmatch(r"\[[0-9a-f]{12}\] prova: 3/3 etapas comprovadas", detalhe)
    _digerir(mundo, "p-1")                                               # minerar de novo não duplica
    assert len(_evidencias(mundo, flow_id, "p-1")) == 1


def mundo_hash(mundo: Mundo, flow_id: str) -> str:
    """O hash do conteúdo pelo caminho independente da leitura da prova: o da lista dos fluxos em prova."""
    [fluxo] = [f for f in LeituraSql(mundo.db).fluxos_em_prova() if f.id == flow_id]
    return fluxo.content_hash


def test_etapa_reprovada_sem_erro_de_ia_e_evidencia_contra(mundo: Mundo) -> None:
    flow_id = _candidato(mundo, concordancias=5)                        # só a evidência: nada promove nem desliga
    _prova(mundo, "p-1", flow_id, [("abrir_perfil", "succeeded"), ("seguir", "failed"), ("ver", "pending")],
           status="completed_with_issues")
    _digerir(mundo, "p-1")
    [(stance, _, detalhe)] = _evidencias(mundo, flow_id, "p-1")
    assert stance == "against"
    assert detalhe.startswith(marca_do_conteudo(mundo_hash(mundo, flow_id)))
    assert "etapa 2 (seguir) reprovada" in detalhe


@pytest.mark.parametrize("cenario", ["budget", "cancelada_pelo_sistema"])
def test_infra_e_cancelamento_pelo_sistema_nao_deixam_evidencia(mundo: Mundo, cenario: str) -> None:
    flow_id = _candidato(mundo, concordancias=5)
    antes = _evidencias(mundo, flow_id)
    if cenario == "budget":
        _prova(mundo, "p-1", flow_id, [("abrir_perfil", "succeeded"), ("seguir", "failed")],
               status="completed_with_issues", erro={"seguir": "budget"})
    else:
        _prova(mundo, "p-1", flow_id, [("abrir_perfil", "cancelled"), ("seguir", "pending")], status="cancelled")
    _digerir(mundo, "p-1")
    assert _evidencias(mundo, flow_id) == antes and _evidencias(mundo, flow_id, "p-1") == []


def test_fluxo_ativo_provado_ganha_evidencia_mas_o_d1_nao_roda(mundo: Mundo, monkeypatch: pytest.MonkeyPatch) -> None:
    mundo.config["com_prova"] = False                                    # nasce ativo (o modo anterior)
    mundo.roda("r-1", "@nasa")
    flow_id = _fluxo_unico(mundo)
    assert mundo.status(flow_id) == "active"
    avaliados: list[str] = []
    original = SombraDosFluxos._avaliar
    monkeypatch.setattr(SombraDosFluxos, "_avaliar",
                        lambda self, fluxo, run_id: (avaliados.append(fluxo.id), original(self, fluxo, run_id))[1])
    _prova(mundo, "p-1", flow_id, [("abrir_perfil", "succeeded")])
    _digerir(mundo, "p-1")
    [(stance, _, detalhe)] = _evidencias(mundo, flow_id, "p-1")
    assert stance == "for" and "1/1 etapas comprovadas" in detalhe
    assert avaliados == [] and mundo.status(flow_id) == "active"
    assert all(run != "p-1" for (_, _, _, run) in mundo.trilha(flow_id))


def test_candidato_provado_roda_o_d1_e_promove_ou_desliga(mundo: Mundo, monkeypatch: pytest.MonkeyPatch) -> None:
    avaliados: list[str] = []
    original = SombraDosFluxos._avaliar
    monkeypatch.setattr(SombraDosFluxos, "_avaliar",
                        lambda self, fluxo, run_id: (avaliados.append(fluxo.id), original(self, fluxo, run_id))[1])
    flow_id = _candidato(mundo)                                          # a execução-fonte é a 1ª evidência a favor
    avaliados.clear()
    _prova(mundo, "p-1", flow_id, [("abrir_perfil", "succeeded")])
    _digerir(mundo, "p-1")
    assert avaliados == [flow_id]
    assert mundo.status(flow_id) == "active"                             # 2 reais com o mesmo plano, sem efeito: D1 publica
    assert any(run == "p-1" for (_, _, _, run) in mundo.trilha(flow_id))


def test_duas_provas_reprovadas_desligam_o_candidato_pelo_d1(mundo: Mundo) -> None:
    flow_id = _candidato(mundo, concordancias=5)                        # sem promoção no caminho: só a regra do contra
    for n in (1, 2):
        _prova(mundo, f"p-{n}", flow_id, [("abrir_perfil", "succeeded"), ("seguir", "failed")], status="completed_with_issues")
        _digerir(mundo, f"p-{n}")
    assert mundo.status(flow_id) == "disabled"


# ------------------------------------------------------------------ 6. a prova nunca é comparável
def test_prova_nunca_e_comparavel_e_nao_grava_sombra_em_outros_fluxos(mundo: Mundo) -> None:
    flow_id = _candidato(mundo, concordancias=5)
    # B: outro fluxo do MESMO comando (molde com outro nome de parâmetro, mesma frase), que a sombra compararia.
    plano_b = _plano("@nasa")
    plano_b.parameters = {"alvo": "@nasa"}
    run_b = mundo.execucao("r-b", plano_b, _comando("@nasa"))
    flow_b = mundo.flows.learn_from_run(run_b)
    assert flow_b is not None and flow_b != flow_id
    # o controle: uma execução COMUM, comparável, do mesmo comando deixa linha nos dois fluxos
    mundo.execucao("r-2", _plano("@nasa"), _comando("@nasa"))
    _digerir(mundo, "r-2")
    assert LeituraSql(mundo.db).execucao("r-2").assinatura is not None            # type: ignore[union-attr]
    assert _evidencias(mundo, flow_b, "r-2") and _evidencias(mundo, flow_id, "r-2")
    antes_b = _evidencias(mundo, flow_b)
    # a prova, com o MESMO plano gravado, não é comparável
    _prova(mundo, "p-1", flow_id, [("abrir_perfil", "succeeded")])
    execucao = LeituraSql(mundo.db).execucao("p-1")
    assert execucao is not None and execucao.assinatura is None and execucao.prova is not None
    _digerir(mundo, "p-1")
    assert _evidencias(mundo, flow_b) == antes_b                          # nada de sombra, nem de forma, no outro fluxo
    assert not mundo.db.query("SELECT 1 FROM learning_evidence WHERE stance='forma' AND run_id='p-1'")
    assert [e[1] for e in _evidencias(mundo, flow_id, "p-1")] == ["run:p-1"]       # só a linha da própria prova
