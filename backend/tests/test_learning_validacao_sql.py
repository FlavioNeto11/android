"""30.31 (laço inteiro, banco migrado): o parecer `pedir_evidencia` vira pedido em `learning_validations` (082), o
despachante enfileira a execução de validação noutro aparelho ocioso e sem conta real, o digest fecha o pedido com a
evidência que ela deixou, e o curador recebe a chegada. O parque e a fila são falsos; o banco é o de verdade (SQLite,
ou PostgreSQL com `TEST_DATABASE_URL`). Nível de prova: `simulated`."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.db import Database
from app.modules.learning.application.validacao import AjustesDaValidacao, ServicoDeValidacao
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.curador import Decisao, Falta, Parecer
from app.modules.learning.domain.livro import EntradaDoLivro
from app.modules.learning.domain.politica_de_risco import ClasseDeRisco, Classificacao, MotivoDeEntrada, Razao
from app.modules.learning.domain.validacao import Ambiente, AparelhoCandidato, EstadoDoPedido
from app.modules.learning.domain.vocabulario import LivroKind, Modo, Origem
from app.modules.learning.infrastructure.ligar_validacao import DespachoDoParque, LacoDaValidacao
from app.modules.learning.infrastructure.validacoes_sql import FontesDaValidacaoSql, RegistroDeValidacoesSql
from app.taskqueue.balanceamento import Candidato
from app.util import to_iso

from .fake_skills import banco as banco_migrado

QA = "com.pocqa.messenger"
COMANDO = 'No QA Messenger, envie "Entrega POC {instance_id} {run_id}" para o contato QA-001'
B = Classificacao(ClasseDeRisco.B, (Razao.COMMIT_SEM_CATALOGO,), MotivoDeEntrada.COMMIT_SEM_CATALOGO)
PEDE = Parecer(decisao=Decisao.PEDIR_EVIDENCIA, evidencias_citadas=("item",),
               falta=(Falta.EXECUCAO_REAL, Falta.REPRODUCAO_EM_OUTRO_APARELHO, Falta.DECISAO_DA_PESSOA))


class Relogio:
    def __init__(self) -> None:
        self.agora = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.agora


class Parque:
    """A fila e o parque falsos: a execução enfileirada vira uma linha `running` em `runs`."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.ambiente_ = Ambiente(saudavel=True, execucoes_em_curso=0)
        self.lista: list[AparelhoCandidato] = []
        self.prontos: set[str] = {QA}                    # os pacotes prontos nos aparelhos de `lista`
        self.pedidos_de_aparelho: list[tuple[str, ...]] = []
        self.enfileiradas: list[tuple[str, str, str]] = []

    def ambiente(self) -> Ambiente:
        return self.ambiente_

    def aparelhos(self, pacotes: Sequence[str]) -> Sequence[AparelhoCandidato]:
        self.pedidos_de_aparelho.append(tuple(pacotes))
        return self.lista if pacotes and set(pacotes) <= self.prontos else []

    def gasto_da_operacao(self, agora: datetime, dias: int) -> float:
        return 18.0

    def enfileirar(self, comando: str, aparelho: str, chave: str) -> str:
        run_id = f"r-20261003120000-{len(self.enfileiradas):06x}"
        self.enfileiradas.append((comando, aparelho, chave))
        self.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
                        " VALUES (?,?,?,?,?,?,?,?)", (run_id, chave, comando, "execute", "running", 0,
                                                       json.dumps([aparelho]), to_iso(datetime.now())))
        return run_id


def _ap(i: str, **kw: bool) -> AparelhoCandidato:
    base = {"online": True, "ocioso": True, "tem_o_app": True, "conta_real": False}
    return AparelhoCandidato(i, **{**base, **kw})


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[tuple[Database, ServicoDeValidacao, Parque, Relogio, dict[str, object]]]:
    db = banco_migrado(tmp_path, "validacao.sqlite3")
    db.execute("INSERT INTO apps(id, name, package, builtin, category) VALUES ('qa-messenger','QA Messenger',?,1,'qa')",
               (QA,))
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('r-20260925014520-91f907','k-origem',?,'execute','completed',0,'[\"android-01\"]',?)",
               (COMANDO, to_iso(datetime.now())))
    ajustes: dict[str, object] = {"modo": Modo.ON}
    parque = Parque(db)
    relogio = Relogio()
    fontes = FontesDaValidacaoSql(db, precos=lambda: {"m": [3.0, 0.3, 3.75, 15.0]},
                                  fluxo_ativo_para=lambda c: c == COMANDO, vetado=lambda e: False)
    servico = ServicoDeValidacao(RegistroDeValidacoesSql(db), fontes, parque, triagem=lambda t: "senha" in t,
                                 ajustes=lambda: AjustesDaValidacao(**ajustes),  # type: ignore[arg-type]
                                 relogio=relogio)
    yield db, servico, parque, relogio, ajustes
    db.close()


def _receita(**kw: object) -> EntradaDoLivro:
    base: dict[str, object] = dict(kind=LivroKind.RECEITA, ref="78", state=SkillState.PUBLISHED, native_status="active",
                                   title="send_message (v1)", app=QA, origin=Origem.EXECUCAO, side_effect=True,
                                   nasceu_de="r-20260925014520-91f907")
    return EntradaDoLivro(**{**base, **kw})  # type: ignore[arg-type]


def _linha(db: Database, pid: str) -> dict[str, object]:
    r = db.one("SELECT * FROM learning_validations WHERE id=?", (pid,))
    assert r is not None
    return dict(r)


def test_o_laco_inteiro_do_pedido_ao_fechamento(mundo: tuple[Database, ServicoDeValidacao, Parque, Relogio,
                                                               dict[str, object]]) -> None:
    db, servico, parque, _relogio, ajustes = mundo
    pid = servico.ao_parecer(_receita(), "lr-1", PEDE, B)
    assert pid is not None
    linha = _linha(db, pid)
    assert linha["estado"] == "pendente" and linha["grupo"] == "qa" and linha["aparelho_excluido"] == "android-01"
    assert json.loads(str(linha["falta"])) == ["reproducao_em_outro_aparelho", "execucao_real"]
    assert linha["comando"] == COMANDO and linha["run_origem"] == "r-20260925014520-91f907"
    # Um pedido vivo por item: o curador que pede de novo (outra volta, outra réplica) não duplica.
    assert servico.ao_parecer(_receita(), "lr-2", PEDE, B) is None
    # O despachante: o de origem e o com conta real ficam de fora; o ocupado também.
    parque.lista = [_ap("android-01"), _ap("android-06", conta_real=True), _ap("android-09", ocioso=False),
                    _ap("android-10")]
    run_id = servico.uma_volta(lambda: 1)
    assert run_id is not None and parque.enfileiradas == [(COMANDO, "android-10", f"validacao:{pid}")]
    assert _linha(db, pid)["estado"] == "rodando"
    # Com a execução de validação em curso, nada mais roda (e não há outro pendente).
    assert servico.uma_volta(lambda: 1) is None
    # Ainda rodando: o digest não fecha.
    assert servico.minerar(run_id) == 0
    # Assentou com a receita conduzindo a etapa e dando certo: o pedido fecha `feita`, com o custo medido.
    db.execute("UPDATE runs SET status='completed' WHERE id=?", (run_id,))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,?)",
               (f"{run_id}:o1", run_id, "android-10", "succeeded", 1))
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " postcondition, timeout_s, max_attempts, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (f"{run_id}:s", run_id, f"{run_id}:o1", "android-10", 1, 1, "send_message", "Enviar", "enviar",
                '{"kind":"text_visible","value":"x","description":"x"}', 60, 3, "succeeded"))
    db.execute("INSERT INTO attempts(id, step_id, number, status, strategy, recipe_id, started_at)"
               " VALUES (?,?,?,?,?,?,?)", (f"{run_id}:s:a1", f"{run_id}:s", 1, "succeeded", "recipe", 78,
                                           to_iso(datetime.now())))
    db.execute("INSERT INTO ai_calls(ts, run_id, role, model, tier, input_tokens, cache_read, cache_write, output_tokens,"
               " with_image, ms, ok) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (to_iso(datetime.now()), run_id, "plan", "m", 0, 10_000, 0, 0, 1_000, 0, 100, 1))
    assert servico.minerar(run_id) == 1
    fechado = _linha(db, pid)
    assert fechado["estado"] == "feita" and fechado["motivo"] is None
    assert abs(float(str(fechado["usd"])) - (10_000 * 3.0 + 1_000 * 15.0) / 1_000_000) < 1e-9
    # A chegada volta ao curador (gatilho `evidencia_chegou`) até ele revisar. Com o despachante pausado (`modo` off,
    # a pausa do P4 de 03/10) também: a evidência já foi paga, e o gasto da revisão é do curador.
    ajustes["modo"] = Modo.OFF
    assert [c.id for c in servico.chegadas()] == [pid]
    ajustes["modo"] = Modo.ON
    servico.revisado(pid, "lr-3")
    assert servico.chegadas() == [] and _linha(db, pid)["revisao_nova_id"] == "lr-3"
    # Fechado o pedido, o item pode pedir de novo.
    assert servico.ao_parecer(_receita(), "lr-4", PEDE, B) is not None


def test_sem_evidencia_ou_com_falha_o_pedido_fecha_recusado_e_conta_o_gasto(
        mundo: tuple[Database, ServicoDeValidacao, Parque, Relogio, dict[str, object]]) -> None:
    db, servico, parque, _relogio, _ = mundo
    parque.lista = [_ap("android-10")]
    pid = servico.ao_parecer(_receita(), "lr-1", PEDE, B)
    run_id = servico.uma_volta(lambda: 1)
    assert pid and run_id
    db.execute("UPDATE runs SET status='failed' WHERE id=?", (run_id,))
    assert servico.minerar(run_id) == 1
    assert (_linha(db, pid)["estado"], _linha(db, pid)["motivo"]) == ("recusada", "execucao_falhou")
    assert servico.chegadas() == []


def test_as_recusas_ficam_registradas_e_o_modo_off_nao_faz_nada(
        mundo: tuple[Database, ServicoDeValidacao, Parque, Relogio, dict[str, object]]) -> None:
    db, servico, parque, _relogio, ajustes = mundo
    # Instagram com efeito: o ensaio de leitura é a fatia 2.
    pid = servico.ao_parecer(_receita(app="com.instagram.android", ref="20"), "lr-1", PEDE, B)
    assert pid is not None and (_linha(db, pid)["estado"], _linha(db, pid)["motivo"]) == ("recusada", "efeito_real")
    # Receita sem fluxo ativo para o comando: a chave da etapa pode mudar.
    db.execute("UPDATE runs SET command='outro comando' WHERE id='r-20260925014520-91f907'")
    pid2 = servico.ao_parecer(_receita(ref="79"), "lr-2", PEDE, B)
    assert pid2 is not None and _linha(db, pid2)["motivo"] == "sem_fluxo_ativo"
    # Sessão ou autenticação fica com a pessoa.
    sessao = Classificacao(ClasseDeRisco.C, (Razao.SESSAO_OU_AUTENTICACAO,), MotivoDeEntrada.SESSAO_OU_AUTENTICACAO)
    pid3 = servico.ao_parecer(_receita(ref="80", kind=LivroKind.FLUXO), "lr-3", PEDE, sessao)
    assert pid3 is not None and _linha(db, pid3)["motivo"] == "sessao_ou_autenticacao"
    # Recusadas não ocupam a fila.
    parque.lista = [_ap("android-10")]
    assert servico.uma_volta(lambda: 1) is None and parque.enfileiradas == []
    # `off`: nada nasce e nada roda.
    ajustes["modo"] = Modo.OFF
    assert servico.ao_parecer(_receita(ref="81", kind=LivroKind.FLUXO), "lr-5", PEDE, B) is None
    assert servico.chegadas() == []


def test_o_despachante_espera_o_lider_o_ambiente_e_o_pedido_preso_expira(
        mundo: tuple[Database, ServicoDeValidacao, Parque, Relogio, dict[str, object]]) -> None:
    db, servico, parque, relogio, _ = mundo
    pid = servico.ao_parecer(_receita(), "lr-1", PEDE, B)
    assert pid is not None
    parque.lista = [_ap("android-10")]
    assert servico.uma_volta(lambda: None) is None                       # outro backend é o líder
    parque.ambiente_ = Ambiente(saudavel=True, execucoes_em_curso=2)     # suíte, deploy ou uso: espera
    assert servico.uma_volta(lambda: 1) is None and _linha(db, pid)["estado"] == "pendente"
    parque.ambiente_ = Ambiente(saudavel=True, execucoes_em_curso=0)
    run_id = servico.uma_volta(lambda: 1)
    assert run_id is not None
    # A execução nunca assentou (ficou em needs_input sem digest): depois de 6 h o pedido expira e o item pode pedir.
    db.execute("UPDATE learning_validations SET updated_at=? WHERE id=?",
               (to_iso(relogio.agora - timedelta(hours=7)), pid))
    servico.uma_volta(lambda: 1)
    assert (_linha(db, pid)["estado"], _linha(db, pid)["motivo"]) == ("expirada", "expirou")


def test_sem_folego_no_beta_so_a_verba_unica_dentro_do_prazo_despacha(
        mundo: tuple[Database, ServicoDeValidacao, Parque, Relogio, dict[str, object]]) -> None:
    """P4 do desenho: `extra_usd` soma ao beta só até `extra_ate` (comparado como data; sem fuso = UTC)."""
    db, servico, parque, relogio, ajustes = mundo
    pid = servico.ao_parecer(_receita(), "lr-1", PEDE, B)
    assert pid is not None
    parque.lista = [_ap("android-10")]
    ajustes.update(beta=0.0, extra_usd=2.5, extra_ate="2026-10-03T11:59:59")         # a verba venceu um segundo antes
    assert servico.uma_volta(lambda: 1) is None and _linha(db, pid)["estado"] == "pendente"
    ajustes.update(extra_ate="2026-10-10T00:00:00.000Z")
    assert servico.uma_volta(lambda: 1) is not None and _linha(db, pid)["estado"] == "rodando"


def test_o_despachante_roda_a_volta_na_thread_do_loop() -> None:
    """`RunService.create` agenda o planejamento com `asyncio.create_task`: a volta tem de rodar no loop. Numa thread
    (`asyncio.to_thread`), `get_running_loop` levanta e a volta morre no `except` do laço."""
    no_loop: list[bool] = []

    class Servico:
        intervalo_s = 0

        def uma_volta(self, lider):  # noqa: ANN001, ANN201
            asyncio.get_running_loop()                        # RuntimeError fora da thread do loop
            no_loop.append(True)
            laco.parar(0)

    laco = LacoDaValidacao(Servico())  # type: ignore[arg-type]
    asyncio.run(asyncio.wait_for(laco.laco(lambda: 1), timeout=5))
    assert no_loop == [True]


# ------------------------------------------------------------------ 30.33-C: o item de mais de um app
QA2, FORA = "com.pocqa.segundo", "com.exemplo.fora"


def _fluxo_multi(db: Database, fid: str, apps: tuple[str, ...]) -> EntradaDoLivro:
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at)"
               " VALUES (?,?,?,?,?,?,?,?)", (fid, fid, f"cmd {fid}", COMANDO, "{}", "qa-messenger", "active",
                                             to_iso(datetime.now())))
    for a in apps:
        db.execute("INSERT INTO flow_required_apps(flow_id, app_id) VALUES (?,?)", (fid, a))
    return _receita(kind=LivroKind.FLUXO, ref=fid, apps=tuple(
        {"qa-messenger": QA, "qa-2": QA2, "fora": FORA}[a] for a in apps))


def test_o_multi_app_so_e_qa_com_todos_os_apps_de_qa_e_pede_aparelho_com_todos(
        mundo: tuple[Database, ServicoDeValidacao, Parque, Relogio, dict[str, object]]) -> None:
    """Mais restritivo (regra do dono): o fluxo que passa pelo QA e por um app de fora não é QA (com efeito, é
    efeito_real, recusado); o de dois apps de QA é QA, e o aparelho precisa dos DOIS prontos, não só do principal."""
    db, servico, parque, _relogio, _ = mundo
    db.execute("INSERT INTO apps(id, name, package, builtin, category) VALUES ('qa-2','QA Dois',?,1,'qa')", (QA2,))
    db.execute("INSERT INTO apps(id, name, package, builtin, category) VALUES ('fora','Fora',?,1,NULL)", (FORA,))
    misto = servico.ao_parecer(_fluxo_multi(db, "f-misto", ("qa-messenger", "fora")), "lr-1", PEDE, B)
    assert misto is not None and (_linha(db, misto)["estado"], _linha(db, misto)["motivo"]) == ("recusada",
                                                                                                "efeito_real")
    pid = servico.ao_parecer(_fluxo_multi(db, "f-qa", ("qa-messenger", "qa-2")), "lr-2", PEDE, B)
    assert pid is not None and (_linha(db, pid)["grupo"], _linha(db, pid)["estado"]) == ("qa", "pendente")
    parque.lista = [_ap("android-10")]
    assert servico.uma_volta(lambda: 1) is None                          # só o QA pronto: espera
    assert set(parque.pedidos_de_aparelho[-1]) == {QA, QA2}
    parque.prontos = {QA, QA2}
    assert servico.uma_volta(lambda: 1) is not None and _linha(db, pid)["aparelho"] == "android-10"


def test_o_parque_so_oferece_aparelho_com_todos_os_apps_prontos(tmp_path: Path) -> None:
    db = banco_migrado(tmp_path, "parque.sqlite3")
    for instancia, pacote, estado in (("android-09", QA, "ready"), ("android-09", QA2, "ready"),
                                      ("android-10", QA, "ready"), ("android-10", QA2, "missing")):
        db.execute("INSERT INTO device_app_state(instance_id, package_name, state) VALUES (?,?,?)",
                   (instancia, pacote, estado))

    class ParqueDeCandidatos:
        def candidatos_de(self, ids: Sequence[str]) -> list[Candidato]:
            return [Candidato(instance_id=i, servidor="central", ligado=True, acordavel=False, ocupado=False)
                    for i in ids]

    despacho = DespachoDoParque(db, None, ParqueDeCandidatos(), saudavel=lambda: True)  # type: ignore[arg-type]
    assert [a.id for a in despacho.aparelhos((QA, QA2))] == ["android-09"]
    assert [a.id for a in despacho.aparelhos((QA,))] == ["android-09", "android-10"]
    assert despacho.aparelhos(()) == [] and despacho.aparelhos(("",)) == []
    db.close()
