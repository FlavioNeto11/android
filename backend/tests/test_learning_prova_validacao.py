"""30.37 (o serviço de validação): o pedido de FLUXO vira execução de prova, com teto de gasto por pedido, `sem_caminho`
quando o comando não cabe no molde, fechamento sem `execucao_falhou` para o que é infra, e a reabertura do que fechou
`sem_evidencia`/`divergencia_de_forma` antes da prova. Banco migrado (SQLite, ou PostgreSQL com `TEST_DATABASE_URL`),
parque e fila falsos. Nível de prova: `simulated`."""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import ValidacaoCfg
from app.db import Database
from app.events import EventBus
from app.modules.learning.application.validacao import AjustesDaValidacao, NovoPedido, ServicoDeValidacao
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.livro import EntradaDoLivro
from app.modules.learning.domain.validacao import teto_da_prova
from app.modules.learning.domain.vocabulario import LivroKind, Modo, Origem
from app.modules.learning.infrastructure.ligar_validacao import ajustes_da_validacao
from app.modules.learning.infrastructure.validacoes_sql import FontesDaValidacaoSql, RegistroDeValidacoesSql
from app.taskqueue.repository import Repository
from app.util import to_iso

from .fake_skills import banco as banco_migrado
from .test_learning_validacao_sql import (B, COMANDO, MOLDE, PEDE, PLANO_DO_FLUXO, QA, Parque, Relogio, _ap, _linha)

ORIGEM = "r-20260925014520-91f907"


class ParqueDeProva(Parque):
    """O parque falso que grava `runs.prova_fluxo_id` como o `RunService.create(..., prova=)` faz."""

    def enfileirar(self, comando: str, aparelho: str, chave: str, prova: str | None = None) -> str:
        run_id = super().enfileirar(comando, aparelho, chave, prova)
        if prova:
            self.db.execute("UPDATE runs SET prova_fluxo_id=? WHERE id=?", (prova, run_id))
        return run_id


Mundo = tuple[Database, ServicoDeValidacao, ParqueDeProva, Relogio, dict[str, object]]


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    db = banco_migrado(tmp_path, "prova.sqlite3")
    db.execute("INSERT INTO apps(id, name, package, builtin, category) VALUES ('qa-messenger','QA Messenger',?,1,'qa')",
               (QA,))
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES (?,'k-origem',?,'execute','completed',0,'[\"android-01\"]',?)",
               (ORIGEM, COMANDO, to_iso(datetime.now())))
    ajustes: dict[str, object] = {"modo": Modo.ON}
    parque = ParqueDeProva(db)
    parque.lista = [_ap("android-10")]
    relogio = Relogio()
    fontes = FontesDaValidacaoSql(db, precos=lambda: {"m": [3.0, 0.3, 3.75, 15.0]},
                                  fluxo_ativo_para=lambda c: c == COMANDO, vetado=lambda e: False)
    servico = ServicoDeValidacao(RegistroDeValidacoesSql(db), fontes, parque, triagem=lambda t: "senha" in t,
                                 ajustes=lambda: AjustesDaValidacao(**ajustes),  # type: ignore[arg-type]
                                 relogio=relogio)
    yield db, servico, parque, relogio, ajustes
    db.close()


def _fluxo(db: Database, fid: str = "f-qa", *, molde: str = MOLDE, status: str = "candidate") -> EntradaDoLivro:
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at)"
               " VALUES (?,?,?,?,?,?,?,?)", (fid, fid, f"cmd {fid}", molde, PLANO_DO_FLUXO, "qa-messenger", status,
                                             to_iso(datetime.now())))
    db.execute("INSERT INTO flow_required_apps(flow_id, app_id) VALUES (?,?)", (fid, "qa-messenger"))
    return EntradaDoLivro(kind=LivroKind.FLUXO, ref=fid, state=SkillState.PUBLISHED, native_status=status, title=fid,
                          app=QA, origin=Origem.EXECUCAO, side_effect=True, nasceu_de=ORIGEM, apps=(QA,))


def _fechar_sem_prova(db: Database, servico: ServicoDeValidacao, parque: ParqueDeProva, pid: str, status: str) -> str:
    """Despacha o pedido, tira a marca de prova da execução (como as de antes da 084) e a assenta."""
    run_id = servico.uma_volta(lambda: 1)
    assert run_id is not None
    db.execute("UPDATE runs SET prova_fluxo_id=NULL, status=? WHERE id=?", (status, run_id))
    assert servico.minerar(run_id) == 1
    return run_id


# ------------------------------------------------------------------ o teto por pedido
def test_o_teto_por_pedido_vem_do_config_e_o_pedido_o_grava(mundo: Mundo) -> None:
    db, servico, _parque, _relogio, ajustes = mundo
    assert ValidacaoCfg().teto_por_pedido_usd == pytest.approx(0.10)
    assert ajustes_da_validacao(ValidacaoCfg(teto_por_pedido_usd=0.25)).teto_por_pedido_usd == pytest.approx(0.25)
    assert AjustesDaValidacao().teto_por_pedido_usd == pytest.approx(0.10)
    pid = servico.ao_parecer(_fluxo(db), "lr-1", PEDE, B)
    assert pid is not None and float(str(_linha(db, pid)["teto_usd"])) == pytest.approx(0.10)
    ajustes["teto_por_pedido_usd"] = 0.25
    pid2 = servico.ao_parecer(_fluxo(db, "f-outro"), "lr-2", PEDE, B)
    assert pid2 is not None and float(str(_linha(db, pid2)["teto_usd"])) == pytest.approx(0.25)


def test_o_teto_da_execucao_e_o_menor_entre_o_da_validacao_e_o_do_pedido(mundo: Mundo, tmp_path: Path) -> None:
    db, servico, _parque, _relogio, ajustes = mundo
    repo = Repository(db, EventBus(db), tmp_path / "evidencias")
    assert repo.teto_usd_da_execucao(ORIGEM) is None                       # execução comum: nada muda
    assert repo.teto_usd_da_execucao(None) is None
    ajustes["teto_por_pedido_usd"] = 0.30
    pid = servico.ao_parecer(_fluxo(db), "lr-1", PEDE, B)
    run_id = servico.uma_volta(lambda: 1)
    assert pid and run_id
    # 30.41: a prova de fluxo leva o teto proporcional ao plano (o do teste não tem etapas: o piso), não o da config
    assert repo.teto_usd_da_execucao(run_id) == pytest.approx(teto_da_prova(0))
    db.execute("UPDATE learning_validations SET teto_usd=0.30 WHERE id=?", (pid,))
    assert repo.teto_usd_da_execucao(run_id) == pytest.approx(0.30)        # só o da validação
    # O pedido do 28.6 com orçamento por ocorrência MENOR: vale o menor dos dois.
    db.execute("INSERT INTO pedidos(id, titulo, objetivo, orcamento_ocorrencia_usd, criado_em, atualizado_em)"
               " VALUES ('p1','t','o',0.12,'2026-10-03T12:00:00.000Z','2026-10-03T12:00:00.000Z')")
    db.execute("INSERT INTO pedido_ocorrencias(id, pedido_id, pedido_versao, previsto_para, chave, origem, criada_em)"
               " VALUES ('o1','p1',1,'2026-10-03T12:00:00Z','c1','manual','2026-10-03T12:00:00.000Z')")
    db.execute("UPDATE runs SET ocorrencia_id='o1' WHERE id=?", (run_id,))
    assert repo.teto_usd_da_execucao(run_id) == pytest.approx(0.12)
    db.execute("UPDATE pedidos SET orcamento_ocorrencia_usd=0.90 WHERE id='p1'")
    assert repo.teto_usd_da_execucao(run_id) == pytest.approx(0.30)
    # Pedido de antes da 084 (sem teto): só o do 28.6.
    db.execute("UPDATE learning_validations SET teto_usd=NULL WHERE id=?", (pid,))
    assert repo.teto_usd_da_execucao(run_id) == pytest.approx(0.90)


def test_o_pedido_legado_sem_teto_ganha_teto_ao_despachar(mundo: Mundo, tmp_path: Path) -> None:
    """30.40: o pedido de antes do 30.37 (`teto_usd` NULL) ganha teto no despacho, no mesmo UPDATE que liga a execução;
    a execução nunca fica ligada sem teto. 30.41: na prova de fluxo é o proporcional ao plano (a receita herda o da
    config: `test_learning_prova_teto.py`)."""
    db, servico, _parque, _relogio, ajustes = mundo
    repo = Repository(db, EventBus(db), tmp_path / "evidencias")
    pid = servico.ao_parecer(_fluxo(db), "lr-1", PEDE, B)
    assert pid is not None
    db.execute("UPDATE learning_validations SET teto_usd=NULL WHERE id=?", (pid,))         # o legado
    ajustes["teto_por_pedido_usd"] = 0.15
    run_id = servico.uma_volta(lambda: 1)
    assert run_id is not None
    linha = _linha(db, pid)
    assert linha["estado"] == "rodando" and linha["run_id"] == run_id
    assert float(str(linha["teto_usd"])) == pytest.approx(teto_da_prova(0))
    assert repo.teto_usd_da_execucao(run_id) == pytest.approx(teto_da_prova(0))


def test_na_prova_de_fluxo_o_teto_proporcional_vence_o_gravado(mundo: Mundo) -> None:
    """30.41: o pedido de fluxo que nasceu com o teto fixo (0,10, ou o 0,15 gravado à mão em 03/10) despacha com o
    proporcional ao plano. O "teto gravado não muda" do 30.40 segue valendo para a receita (`test_learning_prova_teto`)."""
    db, servico, _parque, _relogio, ajustes = mundo
    pid = servico.ao_parecer(_fluxo(db), "lr-1", PEDE, B)                                   # nasce com 0,10
    ajustes["teto_por_pedido_usd"] = 0.15
    assert pid is not None and servico.uma_volta(lambda: 1) is not None
    assert float(str(_linha(db, pid)["teto_usd"])) == pytest.approx(teto_da_prova(0))


# ------------------------------------------------------------------ o fluxo vira prova
def test_o_pedido_de_fluxo_enfileira_a_execucao_de_prova_e_o_de_receita_nao(mundo: Mundo) -> None:
    db, servico, parque, _relogio, _ = mundo
    pid = servico.ao_parecer(_fluxo(db), "lr-1", PEDE, B)
    assert pid is not None and _linha(db, pid)["estado"] == "pendente"
    run_id = servico.uma_volta(lambda: 1)
    assert run_id is not None and parque.provas == ["f-qa"]
    assert db.scalar("SELECT prova_fluxo_id FROM runs WHERE id=?", (run_id,)) == "f-qa"
    # A receita segue pela chamada de antes (sem `prova`).
    receita = EntradaDoLivro(kind=LivroKind.RECEITA, ref="78", state=SkillState.PUBLISHED, native_status="active",
                             title="send_message (v1)", app=QA, origin=Origem.EXECUCAO, side_effect=True,
                             nasceu_de=ORIGEM)
    db.execute("UPDATE learning_validations SET estado='feita' WHERE id=?", (pid,))
    assert servico.ao_parecer(receita, "lr-2", PEDE, B) is not None
    assert servico.uma_volta(lambda: 1) is not None and parque.provas == ["f-qa", None]


def test_o_fluxo_fora_do_molde_nasce_sem_caminho_e_nao_volta_ao_curador(mundo: Mundo) -> None:
    db, servico, parque, _relogio, _ = mundo
    fluxo = _fluxo(db, molde="Abra o QA Messenger e leia a conversa {contato}")
    pid = servico.ao_parecer(fluxo, "lr-1", PEDE, B)
    assert pid is not None
    assert (_linha(db, pid)["estado"], _linha(db, pid)["motivo"]) == ("recusada", "sem_caminho")
    assert servico.chegadas() == []                    # sem a marca do dossiê, o curador pediria de novo (laço pago)
    assert servico.uma_volta(lambda: 1) is None and parque.enfileiradas == []


def test_o_fluxo_que_perde_o_molde_antes_de_despachar_fecha_sem_caminho_sem_gastar(mundo: Mundo) -> None:
    db, servico, parque, _relogio, _ = mundo
    pid = servico.ao_parecer(_fluxo(db), "lr-1", PEDE, B)
    assert pid is not None and _linha(db, pid)["estado"] == "pendente"
    db.execute("UPDATE flows SET command_template='Outro comando {x}' WHERE id='f-qa'")
    assert servico.uma_volta(lambda: 1) is None and parque.enfileiradas == []
    assert (_linha(db, pid)["estado"], _linha(db, pid)["motivo"]) == ("recusada", "sem_caminho")
    assert servico.chegadas() == []
    # Fluxo desligado também não tem molde (`plano_em_prova` recusa `disabled`).
    db.execute("UPDATE flows SET command_template=?, status='disabled' WHERE id='f-qa'", (MOLDE,))
    pid2 = servico.ao_parecer(_fluxo(db, "f-b"), "lr-2", PEDE, B)
    assert pid2 is not None and _linha(db, pid2)["estado"] == "pendente"


@pytest.mark.parametrize("status", ["failed", "cancelled", "completed"])
def test_a_prova_de_fluxo_sem_posicao_fecha_sem_evidencia_nunca_execucao_falhou(mundo: Mundo, status: str) -> None:
    db, servico, parque, _relogio, _ = mundo
    pid = servico.ao_parecer(_fluxo(db), "lr-1", PEDE, B)
    run_id = servico.uma_volta(lambda: 1)
    assert pid and run_id
    db.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))
    assert servico.minerar(run_id) == 1
    assert (_linha(db, pid)["estado"], _linha(db, pid)["motivo"]) == ("recusada", "sem_evidencia")


def test_a_evidencia_contra_o_fluxo_ainda_fecha_com_o_motivo_dela(mundo: Mundo) -> None:
    db, servico, parque, _relogio, _ = mundo
    pid = servico.ao_parecer(_fluxo(db), "lr-1", PEDE, B)
    run_id = servico.uma_volta(lambda: 1)
    assert pid and run_id
    db.execute("UPDATE runs SET status='failed' WHERE id=?", (run_id,))
    db.execute("INSERT INTO learning_evidence(item_ref, run_id, stance, origin_ref, simulated, detail, observed_at)"
               " VALUES (?,?,?,?,0,?,?)",
               ("fluxo:f-qa", run_id, "against", f"run:{run_id}", "etapa falhou", to_iso(datetime.now())))
    assert servico.minerar(run_id) == 1
    assert _linha(db, pid)["motivo"] == "evidencia_contra"


# ------------------------------------------------------------------ a reabertura
def _vivos(db: Database, item: str = "fluxo:f-qa") -> int:
    return int(db.scalar("SELECT COUNT(*) FROM learning_validations WHERE item_ref=? AND estado IN ('pendente','rodando')",
                         (item,)) or 0)


def _total(db: Database, item: str = "fluxo:f-qa") -> int:
    return int(db.scalar("SELECT COUNT(*) FROM learning_validations WHERE item_ref=?", (item,)) or 0)


def test_reabre_uma_unica_vez_o_pedido_de_fluxo_fechado_antes_da_prova(mundo: Mundo) -> None:
    db, servico, parque, relogio, _ = mundo
    pid = servico.ao_parecer(_fluxo(db), "lr-1", PEDE, B)
    assert pid
    _fechar_sem_prova(db, servico, parque, pid, "completed")
    assert (_linha(db, pid)["estado"], _linha(db, pid)["motivo"]) == ("recusada", "sem_evidencia")
    relogio.agora += timedelta(hours=1)
    assert servico.executar(relogio.agora) == 1
    assert _total(db) == 2 and _vivos(db) == 1
    novo = db.one("SELECT * FROM learning_validations WHERE estado='pendente'")
    assert novo is not None and novo["id"] != pid and novo["review_id"] == "lr-1" and novo["comando"] == COMANDO
    assert float(novo["teto_usd"]) == pytest.approx(0.10) and novo["motivo"] is None and novo["run_id"] is None
    assert json.loads(str(novo["falta"])) == json.loads(str(_linha(db, pid)["falta"]))
    assert novo["aparelho_excluido"] == "android-01" and novo["expira_em"] > to_iso(relogio.agora)
    # Idempotente: o pedido novo é posterior ao antigo.
    relogio.agora += timedelta(hours=1)
    assert servico.executar(relogio.agora) == 0 and _total(db) == 2
    # A prova que fecha `sem_evidencia` (execução com `prova_fluxo_id`) não reabre de novo.
    run_id = servico.uma_volta(lambda: 1)
    assert run_id is not None and parque.provas[-1] == "f-qa"
    db.execute("UPDATE runs SET status='completed' WHERE id=?", (run_id,))
    assert servico.minerar(run_id) == 1
    relogio.agora += timedelta(hours=1)
    assert servico.executar(relogio.agora) == 0 and _total(db) == 2 and _vivos(db) == 0


def test_a_divergencia_de_forma_tambem_reabre_mas_o_contra_e_o_a_favor_nao(mundo: Mundo) -> None:
    db, servico, parque, relogio, _ = mundo
    pid = servico.ao_parecer(_fluxo(db), "lr-1", PEDE, B)
    assert pid
    _fechar_sem_prova(db, servico, parque, pid, "completed")
    db.execute("UPDATE learning_validations SET motivo='evidencia_contra' WHERE id=?", (pid,))
    relogio.agora += timedelta(hours=1)
    assert servico.executar(relogio.agora) == 0
    db.execute("UPDATE learning_validations SET motivo='divergencia_de_forma' WHERE id=?", (pid,))
    assert servico.executar(relogio.agora) == 1


def test_nao_reabre_com_o_modo_off_nem_fluxo_desligado_nem_com_pedido_posterior_nem_receita(mundo: Mundo) -> None:
    db, servico, parque, relogio, ajustes = mundo
    pid = servico.ao_parecer(_fluxo(db), "lr-1", PEDE, B)
    assert pid
    _fechar_sem_prova(db, servico, parque, pid, "completed")
    relogio.agora += timedelta(hours=1)
    ajustes["modo"] = Modo.OFF
    assert servico.executar(relogio.agora) == 0 and _total(db) == 1
    ajustes["modo"] = Modo.ON
    db.execute("UPDATE flows SET status='disabled' WHERE id='f-qa'")
    assert servico.executar(relogio.agora) == 0 and _total(db) == 1
    db.execute("UPDATE flows SET status='candidate' WHERE id='f-qa'")
    # Pedido POSTERIOR do mesmo item (aqui, expirado à mão): o antigo já foi respondido por ele.
    posterior = NovoPedido(review_id="lr-2", item_ref="fluxo:f-qa", item_kind="fluxo", scope_app=QA, grupo="qa",
                           falta=("execucao_real",), run_origem=ORIGEM, comando=COMANDO, aparelho_excluido=None,
                           estado="pendente", motivo=None, expira_em=to_iso(relogio.agora + timedelta(hours=72)))
    assert RegistroDeValidacoesSql(db).criar(posterior, relogio.agora) is not None
    db.execute("UPDATE learning_validations SET estado='expirada', motivo='expirou' WHERE id <> ?", (pid,))
    assert servico.executar(relogio.agora) == 0 and _total(db) == 2
    # Receita: a execução comum dela mede a receita; não se reabre.
    receita = EntradaDoLivro(kind=LivroKind.RECEITA, ref="78", state=SkillState.PUBLISHED, native_status="active",
                             title="send_message (v1)", app=QA, origin=Origem.EXECUCAO, side_effect=True,
                             nasceu_de=ORIGEM)
    rid = servico.ao_parecer(receita, "lr-3", PEDE, B)
    assert rid
    _fechar_sem_prova(db, servico, parque, rid, "completed")
    relogio.agora += timedelta(hours=1)
    assert servico.executar(relogio.agora) == 0 and _total(db, "receita:78") == 1
