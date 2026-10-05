"""30.75: a prova de fluxo que não deixou evidência fecha pela causa que deixou registrada, e a parada no login tira o
aparelho das próximas provas daquele app.

A leitura de 05/10 (`.claude/handoffs/aprendizado-sem-evidencia.md`): dos 5 pedidos `sem_evidencia`, dois foram o teto do
pedido cortando a execução no meio (US$ 0,157 e 0,159, `attempts.error_kind='budget'`) e um foi o QA Messenger do
android-02 deslogado (a prova parou na tela de senha). Os três diziam só "sem evidência".

O que estes testes guardam:
- a causa vem dos campos estruturados, nunca do texto: `budget` dá `orcamento_da_prova`; `blocked_kind='auth'` dá
  `app_sem_sessao`; o teto vence o login; sem nenhum dos dois, `sem_evidencia` como antes;
- o pedido que fecha agora já fecha com a causa; o do estoque passa a ela pelo passo da curadoria (remotivação);
- nenhum dos dois é chegada do curador;
- a parada no login de uma PROVA grava `blocked_kind='auth'` (o `StepOutcome.pede_login`); a da execução comum não;
- o aparelho da parada sai dos candidatos daquele pacote até a verificação nova do app nele, ou até um objetivo
  concluído num fluxo do mesmo app no mesmo aparelho.

Prova `simulated`: o banco migrado com o despachante falso (`test_learning_validacao_sql`) e o Harness (porta 5640).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.db import Database
from app.modules.learning.application.validacao import ServicoDeValidacao
from app.modules.learning.domain.validacao import MOTIVO_HUMANO, Motivo
from app.modules.learning.infrastructure.ligar_validacao import DespachoDoParque
from app.modules.learning.infrastructure.validacoes_sql import FontesDaValidacaoSql
from app.taskqueue.balanceamento import Candidato
from app.util import to_iso

from .fake_skills import banco as banco_migrado
from .test_learning_forma import Planos, _roda, validacao  # noqa: F401 - a fixture é usada pelo nome
from .test_learning_prova import Real, real  # noqa: F401 - a fixture é usada pelo nome
from .test_learning_validacao_sql import QA, Parque, Relogio, _linha

Validacao = tuple[Database, ServicoDeValidacao, Parque, Relogio, Planos]


def _parada(db: Database, run_id: str, *, orcamento: bool = False, login: bool = False,
            aparelho: str = "android-09", em: str = "2026-10-05T07:36:56.000Z") -> None:
    """O que a execução de prova deixou: um objetivo cancelado (com `blocked_kind='auth'` na parada do login) e uma
    tentativa (com `error_kind='budget'` no corte pelo teto)."""
    oid, sid = f"{run_id}:o1", f"{run_id}:s1"
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, blocked_kind, finished_at)"
               " VALUES (?,?,?,?,?,?,?)", (oid, run_id, aparelho, "cancelled", 1, "auth" if login else None, em))
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " postcondition, timeout_s, max_attempts, status) VALUES (?,?,?,?,1,1,'abrir','Abrir','Abrir','{}',60,3,?)",
               (sid, run_id, oid, aparelho, "failed" if orcamento else "cancelled"))
    db.execute("INSERT INTO attempts(id, step_id, number, status, started_at, error_kind) VALUES (?,?,1,?,?,?)",
               (f"{sid}:a1", sid, "failed" if orcamento else "interrupted", em, "budget" if orcamento else None))
    db.execute("UPDATE runs SET finished_at=? WHERE id=?", (em, run_id))


def _fontes(db: Database) -> FontesDaValidacaoSql:
    return FontesDaValidacaoSql(db, precos=dict, fluxo_ativo_para=lambda c: True, vetado=lambda e: False)


def test_os_motivos_novos_tem_texto_para_a_pessoa() -> None:
    assert MOTIVO_HUMANO[Motivo.ORCAMENTO_DA_PROVA] and MOTIVO_HUMANO[Motivo.APP_SEM_SESSAO]
    assert (Motivo.ORCAMENTO_DA_PROVA.value, Motivo.APP_SEM_SESSAO.value) == ("orcamento_da_prova", "app_sem_sessao")


@pytest.mark.parametrize(("orcamento", "login", "motivo"), [
    (True, False, "orcamento_da_prova"),
    (False, True, "app_sem_sessao"),
    (True, True, "orcamento_da_prova"),          # o corte pelo teto é o que gastou: vence
    (False, False, "sem_evidencia"),
])
def test_o_pedido_fecha_pela_causa_registrada(validacao: Validacao, orcamento: bool, login: bool,  # noqa: F811
                                              motivo: str) -> None:
    db, servico, *_ = validacao
    pid, run_id = _roda(db, servico)
    _parada(db, run_id, orcamento=orcamento, login=login)
    assert servico.minerar(run_id) == 1
    assert (_linha(db, pid)["estado"], _linha(db, pid)["motivo"]) == ("recusada", motivo)
    assert servico.chegadas() == []                # nenhuma das causas diz algo sobre o fluxo


def test_o_estoque_sem_evidencia_passa_a_causa_uma_vez(validacao: Validacao) -> None:  # noqa: F811
    """O pedido que fechou `sem_evidencia` antes do 30.75 (os de 03/10): o passo da curadoria o leva à causa."""
    db, servico, _parque, relogio, _planos = validacao
    pid, run_id = _roda(db, servico)
    assert servico.minerar(run_id) == 1 and _linha(db, pid)["motivo"] == "sem_evidencia"
    _parada(db, run_id, orcamento=True)            # a causa que o pedido não leu ao fechar
    assert servico.executar(relogio.agora) == 1
    assert _linha(db, pid)["motivo"] == "orcamento_da_prova"
    assert servico.executar(relogio.agora) == 0    # só sai de `sem_evidencia`: não mexe de novo
    assert servico.chegadas() == []


def test_a_causa_vem_dos_campos_e_nao_do_texto(tmp_path: Path) -> None:
    db = banco_migrado(tmp_path, "causa.sqlite3")
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('r-x','k-x','cmd','execute','cancelled',0,'[\"android-02\"]',?)", (to_iso(datetime.now(UTC)),))
    _parada(db, "r-x")
    # o texto do motivo da prova cita autenticação, mas sem o campo estruturado não é `app_sem_sessao`
    db.execute("UPDATE objectives SET status_detail='Prova de fluxo (validação): a etapa precisaria de uma pessoa "
               "(O app pede autenticação (campo de senha).).' WHERE run_id='r-x'")
    assert _fontes(db).causa_sem_evidencia("r-x") is None
    assert _fontes(db).causa_sem_evidencia("r-inexistente") is None
    db.close()


def _execucao(db: Database, run_id: str, *, prova: bool) -> None:
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at)"
               " VALUES ('f-x','f-x','k-f-x','cmd','{}',NULL,'candidate',?) ON CONFLICT DO NOTHING",
               (to_iso(datetime.now(UTC)),))
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
               " prova_fluxo_id) VALUES (?,?,'cmd','execute','cancelled',0,'[\"android-02\"]',?,?)",
               (run_id, f"k-{run_id}", to_iso(datetime.now(UTC)), "f-x" if prova else None))


def _tentativa(db: Database, run_id: str, n: int, *, erro: str | None, em: str) -> None:
    sid = f"{run_id}:s{n}"
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " postcondition, timeout_s, max_attempts, status) VALUES (?,?,?,?,1,?,?,'t','g','{}',60,3,'failed')",
               (sid, run_id, f"{run_id}:o1", "android-02", n, f"e{n}"))
    db.execute("INSERT INTO attempts(id, step_id, number, status, started_at, error_kind) VALUES (?,?,1,?,?,?)",
               (f"{sid}:a1", sid, "failed" if erro else "interrupted", em, erro))


def test_execucao_comum_nao_ganha_causa_de_prova(tmp_path: Path) -> None:
    """C1 da leitura do #419: o estoque `sem_evidencia` de antes do 30.37 vem de execução COMUM. Um teto nela não é
    "o teto da prova", e o pedido segue `sem_evidencia` (o caminho da reabertura do 30.37)."""
    db = banco_migrado(tmp_path, "comum.sqlite3")
    _execucao(db, "r-comum", prova=False)
    _parada(db, "r-comum", orcamento=True, login=True)
    assert _fontes(db).causa_sem_evidencia("r-comum") is None
    db.close()


@pytest.mark.parametrize(("ultima", "motivo"), [
    (None, Motivo.APP_SEM_SESSAO),               # um teto que a execução sobreviveu, depois o login que a parou
    ("budget", Motivo.ORCAMENTO_DA_PROVA),       # o teto encerrou a execução: vence o login
])
def test_o_teto_so_conta_quando_encerrou_a_prova(tmp_path: Path, ultima: str | None, motivo: Motivo) -> None:
    """C2 da leitura do #419: `error_kind='budget'` é de todo teto de IA (o da leitura do 31.38, o de uma ação, o do
    dia). Só o da ÚLTIMA tentativa da execução é a causa."""
    db = banco_migrado(tmp_path, "teto.sqlite3")
    _execucao(db, "r-p", prova=True)
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, blocked_kind, finished_at)"
               " VALUES ('r-p:o1','r-p','android-02','cancelled',1,'auth',?)", (to_iso(datetime.now(UTC)),))
    _tentativa(db, "r-p", 1, erro="budget", em="2026-10-05T07:36:00.000Z")
    _tentativa(db, "r-p", 2, erro=ultima, em="2026-10-05T07:36:30.000Z")
    assert _fontes(db).causa_sem_evidencia("r-p") is motivo
    db.close()


class _Parque:
    def candidatos_de(self, ids: Sequence[str]) -> list[Candidato]:
        return [Candidato(instance_id=i, servidor="central", ligado=True, acordavel=False, ocupado=False) for i in ids]


def test_o_aparelho_da_parada_no_login_sai_ate_a_verificacao_nova(tmp_path: Path) -> None:
    db = banco_migrado(tmp_path, "sem-sessao.sqlite3")
    agora = datetime(2026, 10, 5, 7, 36, 56, tzinfo=UTC)
    db.execute("INSERT INTO apps(id, name, package, builtin, category) VALUES ('qa-messenger','QA Messenger',?,1,'qa')",
               (QA,))
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at)"
               " VALUES ('f-qa','f-qa','k-f-qa','cmd','{}','qa-messenger','candidate',?)", (to_iso(agora),))
    for aparelho in ("android-02", "android-09"):
        db.execute("INSERT INTO device_app_state(instance_id, package_name, state, verified_at) VALUES (?,?,?,?)",
                   (aparelho, QA, "ready", to_iso(agora - timedelta(hours=1))))
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
               " prova_fluxo_id) VALUES ('r-prova','validacao:lv-1','cmd','execute','cancelled',0,'[\"android-02\"]',?,"
               " 'f-qa')", (to_iso(agora),))
    despacho = DespachoDoParque(db, None, _Parque(), saudavel=lambda: True)  # type: ignore[arg-type]
    assert [a.id for a in despacho.aparelhos((QA,))] == ["android-02", "android-09"]

    _parada(db, "r-prova", login=True, aparelho="android-02", em=to_iso(agora))
    assert [a.id for a in despacho.aparelhos((QA,))] == ["android-09"]          # só o da parada sai
    assert [a.id for a in despacho.aparelhos(("outro.pacote",))] == []           # nada a ver com outro app

    # um objetivo concluído num fluxo do MESMO app, no MESMO aparelho, depois da parada: a sessão voltou
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
               " flow_id) VALUES ('r-comum','k-comum','cmd','execute','completed',0,'[\"android-02\"]',?, 'f-qa')",
               (to_iso(agora),))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, finished_at)"
               " VALUES ('o-comum','r-comum','android-02','succeeded',1,?)", (to_iso(agora - timedelta(minutes=5)),))
    assert [a.id for a in despacho.aparelhos((QA,))] == ["android-09"]          # antes da parada não conta
    db.execute("UPDATE objectives SET finished_at=? WHERE id='o-comum'", (to_iso(agora + timedelta(minutes=5)),))
    assert [a.id for a in despacho.aparelhos((QA,))] == ["android-02", "android-09"]

    # a verificação nova do app no aparelho também devolve (sem o objetivo concluído)
    db.execute("DELETE FROM objectives WHERE id='o-comum'")
    assert [a.id for a in despacho.aparelhos((QA,))] == ["android-09"]
    db.execute("UPDATE device_app_state SET verified_at=? WHERE instance_id='android-02'",
               (to_iso(agora + timedelta(minutes=1)),))
    assert [a.id for a in despacho.aparelhos((QA,))] == ["android-02", "android-09"]
    db.close()


async def test_a_parada_no_login_da_prova_grava_a_causa_e_a_da_execucao_comum_nao(real: Real) -> None:  # noqa: F811
    """O mesmo cenário do caso 4a do 30.37 (`test_learning_prova`): o app reabre na tela de login."""
    real.h.fakes["android-01"].screen = "launcher"
    real.h.fakes["android-01"].require_login = True
    run = real.prova("prova-30-75")
    await real.h.wait_run(run)
    assert real.db.scalar("SELECT blocked_kind FROM objectives WHERE run_id=?", (run,)) == "auth"
    assert _fontes(real.db).causa_sem_evidencia(run) is Motivo.APP_SEM_SESSAO
    comum = real.h.run(["android-01"])
    await real.h.wait_run(comum.id)
    obj = real.db.one("SELECT status, blocked_kind FROM objectives WHERE run_id=?", (comum.id,))
    assert obj is not None and obj["status"] == "waiting_user" and obj["blocked_kind"] != "auth"
