"""31.154: a operação com N agentes (migração 124, adendo v1.94): criar, ler, cancelar, liberar, teto de custo e a rota.

`simulated`: harness com aparelhos falsos na porta 5640 e provedor simulado. O app dos alvos que ganham execução é o do
aparelho falso (o modelo da operação não tem ramo por app); a prova real no Instagram é a da onda de 07/10.

O que se prova:
- cada alvo = persona + conta + aparelho numa execução PRÓPRIA (marcada com `operacao_id`, chave `op:`, teto `preparar`);
- quem não tem conta para em `conta`, quem não tem sessão para em `sessao`, e o resto segue; a capacidade conta tudo;
- a mesma chave devolve a mesma operação, e com outro corpo é recusada;
- cancelar cancela as execuções dos alvos; liberar exige o eco do texto e respeita o limite de ações executadas;
- o teto de custo da operação barra a IA de qualquer alvo dela (`AIError(kind="budget", motivo="operacao")`);
- `registrar_estagio` marca uma vez e não faz nada fora de operação; a rota HTTP responde, com o filtro em `/api/runs`.
"""
from __future__ import annotations

import secrets as pysecrets
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from app.main import create_app
from app.models import ProfileCreate, SessionStatus
from app.modules.operacoes.domain.estagios import Leitura
from app.modules.operacoes.infrastructure.estagios import operacao_da_execucao, registrar_estagio
from app.modules.operacoes.infrastructure.servico import (AlvoPedido, OperacaoError, PedidoDeOperacao,
                                                          ServicoDeOperacoes, _motivo)
from app.planning.provider import AIError
from app.planning.routing import RoutingProvider
from app.taskqueue.service import RunError
from app.util import now_iso

from .conftest import COMMAND, Harness

pytestmark = pytest.mark.asyncio
APP = "qa-messenger"


def _persona(h: Harness, nome: str, instance: str | None = None) -> str:
    assert h.state is not None
    return h.state.social.create_profile(ProfileCreate(
        username=f"{nome.lower()}.{pysecrets.token_hex(3)}", instance_id=instance, first_name=nome,
        last_name="Teste")).id


def _conta(h: Harness, pid: str, handle: str, sessao_em: str | None = None) -> str:
    assert h.state is not None
    repo = h.state.social_repo
    conta = repo.create_account(pid, app_id=APP, handle=handle)
    if sessao_em:
        repo.set_account_session(pid, conta, sessao_em, status=SessionStatus.session_ready, verified_at=now_iso())
    return conta


def _servico(h: Harness) -> ServicoDeOperacoes:
    st = h.state
    assert st is not None
    return ServicoDeOperacoes(st.db, st.runs, st.social_repo, st.approval_service, st.settings.get, st.bus,
                              st.cfg.file.ai.prices)


def _pedido(alvos: list[AlvoPedido], chave: str = "teste-op-0001", **kw: Any) -> PedidoDeOperacao:
    return PedidoDeOperacao(command=kw.pop("command", COMMAND), app_id=kw.pop("app_id", APP), alvos=alvos,
                            acao_final=kw.pop("acao_final", "preparar"), idempotency_key=chave,
                            max_usd=kw.pop("max_usd", 1.0), **kw)


def _alvo(op: dict[str, Any], pid: str) -> dict[str, Any]:
    return next(a for a in op["alvos"] if a["profile_id"] == pid)


async def test_cada_alvo_e_uma_execucao_propria_e_quem_nao_tem_conta_ou_sessao_para_com_o_motivo(
        harness: Harness) -> None:
    st = harness.state
    assert st is not None
    sem_conta = _persona(harness, "Ana")
    sem_sessao = _persona(harness, "Bia")
    _conta(harness, sem_sessao, "qa-user-02")
    pronta = _persona(harness, "Cris", "android-01")
    _conta(harness, pronta, "qa-user-01", sessao_em="android-01")
    op = _servico(harness).criar(_pedido([AlvoPedido(sem_conta), AlvoPedido(sem_sessao), AlvoPedido(pronta)]))
    a, b, c = _alvo(op, sem_conta), _alvo(op, sem_sessao), _alvo(op, pronta)
    assert (a["estado"], a["motivo"], a["parou_em"], a["run_id"]) == ("bloqueado", "sem conta", "conta", None)
    assert (b["estado"], b["motivo"], b["parou_em"], b["run_id"]) == ("bloqueado", "sem sessão", "sessao", None)
    assert c["run_id"] and c["estado"] in ("pendente", "em_curso") and c["instance_id"] == "android-01"
    run = st.db.one("SELECT * FROM runs WHERE id=?", (c["run_id"],))
    assert run["operacao_id"] == op["id"] and run["teto_de_autonomia"] == "preparar"
    assert run["idempotency_key"].startswith(f"op:{op['id']}:") and pronta not in run["idempotency_key"]
    cap = op["capacidade"]
    assert (cap["solicitados"], cap["contas_existentes"], cap["sessoes_validas"], cap["bloqueadas"]) == (3, 2, 1, 2)
    assert cap["motivos"] == {"sem conta": 1, "sem sessão": 1}
    assert op["status"] == "em_curso" and op["acao_final"] == "preparar"
    # a execução do alvo conta como do sistema: nada de aviso individual por alvo
    assert st.repo.run_summary(run).operacao_id == op["id"]


async def test_mesma_chave_mesma_operacao_e_outro_corpo_e_recusado(harness: Harness) -> None:
    pid = _persona(harness, "Davi")
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(pid)]))
    assert s.criar(_pedido([AlvoPedido(pid)]))["id"] == op["id"]
    with pytest.raises(OperacaoError) as exc:
        s.criar(_pedido([AlvoPedido(pid)], max_usd=2.0))
    assert exc.value.code == "chave_em_uso"


async def test_cancelar_cancela_as_execucoes_dos_alvos(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Eva", "android-02")
    _conta(harness, pid, "qa-user-03", sessao_em="android-02")
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-cancelar"))
    run_id = _alvo(op, pid)["run_id"]
    depois = s.cancelar(op["id"])
    assert depois["status"] == "cancelada"
    assert st.db.one("SELECT cancel_requested FROM runs WHERE id=?", (run_id,))["cancel_requested"] == 1 or \
        st.db.one("SELECT status FROM runs WHERE id=?", (run_id,))["status"] == "cancelled"


async def test_o_teto_de_custo_da_operacao_barra_a_ia_de_qualquer_alvo(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Fabi", "android-03")
    _conta(harness, pid, "qa-user-04", sessao_em="android-03")
    op = _servico(harness).criar(_pedido([AlvoPedido(pid)], chave="teste-op-teto", max_usd=0.001))
    run_id = _alvo(op, pid)["run_id"]
    modelo = next(iter(st.cfg.file.ai.prices))
    st.db.execute("INSERT INTO ai_calls(ts, run_id, role, model, input_tokens, output_tokens, ok) VALUES (?,?,?,?,?,?,?)",
                  (now_iso(), run_id, "plan", modelo, 2_000_000, 100_000, 1))
    roteador = RoutingProvider(harness.cfg)
    roteador.attach(repo=st.repo, settings_getter=st.settings.get)
    with pytest.raises(AIError) as exc:
        roteador._budget(run_id)  # noqa: SLF001
    assert exc.value.kind == "budget" and exc.value.motivo == "operacao"


async def test_o_teto_da_operacao_reserva_as_chamadas_em_voo_dos_alvos_paralelos(harness: Harness) -> None:
    """Achado do Codex (corte 56): o custo só é gravado depois da resposta, e N alvos em paralelo liam o mesmo gasto
    abaixo do teto. Cada chamada em voo conta pelo custo médio das gravadas; `_call` reserva e devolve a vaga."""
    import types

    from app.planning import costs

    st = harness.state
    assert st is not None
    pid = _persona(harness, "Gabi", "android-03")
    _conta(harness, pid, "qa-user-06", sessao_em="android-03")
    op = _servico(harness).criar(_pedido([AlvoPedido(pid)], chave="teste-op-teto-voo", max_usd=100.0))
    run_id = _alvo(op, pid)["run_id"]
    modelo = next(iter(st.cfg.file.ai.prices))
    st.db.execute("INSERT INTO ai_calls(ts, run_id, role, model, input_tokens, output_tokens, ok) VALUES (?,?,?,?,?,?,?)",
                  (now_iso(), run_id, "decide", modelo, 10_000, 1_000, 1))
    uma = costs.spent_usd(st.db, st.cfg.file.ai.prices, run_id=run_id)
    assert uma > 0
    st.db.execute("UPDATE operacoes SET max_usd=? WHERE id=?", (uma * 2.5, op["id"]))
    # Achado da revisão do PR 478: a falha (gasto zero) não entra na média; contá-la baixaria a reserva à metade.
    st.db.execute("INSERT INTO ai_calls(ts, run_id, role, model, input_tokens, output_tokens, ok) VALUES (?,?,?,?,?,?,?)",
                  (now_iso(), run_id, "decide", modelo, 0, 0, 0))
    roteador = RoutingProvider(harness.cfg)
    roteador.attach(repo=st.repo, settings_getter=st.settings.get)
    assert roteador._budget(run_id) == op["id"]                               # gasto 1x, nada em voo  # noqa: SLF001
    roteador._em_voo_da_operacao[op["id"]] = 1                                # 1x + 1x em voo < 2,5x  # noqa: SLF001
    assert roteador._budget(run_id) == op["id"]  # noqa: SLF001
    roteador._em_voo_da_operacao[op["id"]] = 2                                # 1x + 2x em voo >= 2,5x  # noqa: SLF001
    with pytest.raises(AIError) as exc:
        roteador._budget(run_id)  # noqa: SLF001
    assert exc.value.kind == "budget" and exc.value.motivo == "operacao" and "em voo" in str(exc.value)
    # `_call` reserva a vaga durante a chamada e a devolve na volta, também quando a chamada falha.
    roteador._em_voo_da_operacao.clear()  # noqa: SLF001
    vistos: list[int] = []

    uso = types.SimpleNamespace(fallback=None)

    async def _one(*_: Any) -> Any:
        vistos.append(roteador._em_voo_da_operacao.get(op["id"], 0))  # noqa: SLF001
        if len(vistos) == 2:
            raise AIError("falhou", kind="transient")
        return "ok", uso

    roteador._funcao = lambda papel, rid: (types.SimpleNamespace(kind="anthropic", fallback_provider=None), None)  # type: ignore[method-assign,assignment,return-value]
    roteador._saldo = lambda r: None  # type: ignore[method-assign,assignment]
    roteador._one = _one  # type: ignore[method-assign,assignment]
    assert await roteador._call("decide", run_id, lambda p: None) == ("ok", uso)  # noqa: SLF001
    # Achado do Copilot no PR 487: a reserva da chamada que respondeu vale até o custo estar gravado (`add_usage`
    # chama `soltar_reserva`), não só até a volta de `_call`.
    assert roteador._em_voo_da_operacao == {op["id"]: 1}  # noqa: SLF001
    uso.soltar_reserva()
    assert roteador._em_voo_da_operacao == {}  # noqa: SLF001
    with pytest.raises(AIError):
        await roteador._call("decide", run_id, lambda p: None)  # noqa: SLF001
    assert vistos == [1, 1] and roteador._em_voo_da_operacao == {}  # noqa: SLF001
    # A chamada de fora do hub (o POST do Jev) reserva pela conferência, até quem chamou soltar (achado do PR 478).
    solta = roteador.conferir_gasto(run_id=run_id, origem="decisao_fechada", conta="typesafe", reservar=True)
    assert roteador._em_voo_da_operacao == {op["id"]: 1}  # noqa: SLF001
    solta()
    assert roteador._em_voo_da_operacao == {}  # noqa: SLF001
    roteador.conferir_gasto(run_id=run_id, origem="decisao_fechada", conta="typesafe")()    # sem reservar: nada
    assert roteador._em_voo_da_operacao == {}  # noqa: SLF001


async def test_a_conferencia_e_a_reserva_do_teto_sao_uma_secao_critica_so(harness: Harness) -> None:
    """Achado da revisão do PR 479: `_budget` conferia o número em voo, soltava a trava, e só depois `_reserva`
    incrementava. Duas chamadas simultâneas liam o mesmo número e passavam juntas. Aqui cabe UMA chamada nova (1x
    gravado + 1x em voo + 1x nova = 3x >= 2,5x para a segunda); as duas conferem ao mesmo tempo e uma é barrada."""
    import threading

    from app.planning import costs

    st = harness.state
    assert st is not None
    pid = _persona(harness, "Helo", "android-03")
    _conta(harness, pid, "qa-user-07", sessao_em="android-03")
    op = _servico(harness).criar(_pedido([AlvoPedido(pid)], chave="teste-op-teto-secao", max_usd=100.0))
    run_id = _alvo(op, pid)["run_id"]
    modelo = next(iter(st.cfg.file.ai.prices))
    st.db.execute("INSERT INTO ai_calls(ts, run_id, role, model, input_tokens, output_tokens, ok) VALUES (?,?,?,?,?,?,?)",
                  (now_iso(), run_id, "decide", modelo, 10_000, 1_000, 1))
    uma = costs.spent_usd(st.db, st.cfg.file.ai.prices, run_id=run_id)
    st.db.execute("UPDATE operacoes SET max_usd=? WHERE id=?", (uma * 2.5, op["id"]))
    roteador = RoutingProvider(harness.cfg)
    roteador.attach(repo=st.repo, settings_getter=st.settings.get)
    roteador._em_voo_da_operacao[op["id"]] = 1  # noqa: SLF001
    barreira = threading.Barrier(2, timeout=10)
    media = roteador._custo_medio_da_chamada  # noqa: SLF001

    def _media_ao_mesmo_tempo(*args: Any) -> float:
        barreira.wait()                                   # as duas leram o gasto e chegam juntas à conta em voo
        return media(*args)

    roteador._custo_medio_da_chamada = _media_ao_mesmo_tempo  # type: ignore[method-assign]
    resultados: list[Any] = []

    def _conferir() -> None:
        try:
            resultados.append(roteador.conferir_gasto(run_id=run_id, origem="decisao_fechada", conta="typesafe",
                                                      reservar=True))
        except AIError as exc:
            resultados.append(exc)

    linhas = [threading.Thread(target=_conferir) for _ in range(2)]
    for t in linhas:
        t.start()
    for t in linhas:
        t.join(15)
    recusas = [r for r in resultados if isinstance(r, AIError)]
    assert len(resultados) == 2 and len(recusas) == 1 and recusas[0].motivo == "operacao"
    assert roteador._em_voo_da_operacao == {op["id"]: 2}  # noqa: SLF001
    solta = next(r for r in resultados if not isinstance(r, AIError))
    solta()
    solta()                                               # soltar duas vezes não devolve a vaga de outra chamada
    assert roteador._em_voo_da_operacao == {op["id"]: 1}  # noqa: SLF001
    # Barrada por uma régua DEPOIS da operação (o teto da execução), a reserva feita na operação não fica.
    roteador._custo_medio_da_chamada = media  # type: ignore[method-assign]
    roteador._em_voo_da_operacao.clear()  # noqa: SLF001
    st.settings.update({"ai_max_usd_per_run": uma / 2})
    with pytest.raises(AIError) as exc:
        roteador._budget(run_id, reservar=True)  # noqa: SLF001
    assert exc.value.motivo == "execucao" and roteador._em_voo_da_operacao == {}  # noqa: SLF001


async def test_a_reserva_da_chamada_que_respondeu_vale_ate_o_custo_gravado(harness: Harness) -> None:
    """Achado do Copilot no PR 487: `_call` soltava a reserva na volta, e o custo só entra no banco depois, em
    `Executor._ai` (`add_usage`). Nesse intervalo outra chamada passava pelo teto. A reserva vai com o `Usage` e sai no
    `add_usage`."""
    import types

    from app.planning.provider import Usage

    st = harness.state
    assert st is not None
    pid = _persona(harness, "Jana", "android-02")
    _conta(harness, pid, "qa-user-08", sessao_em="android-02")
    op = _servico(harness).criar(_pedido([AlvoPedido(pid)], chave="teste-op-reserva-ate-o-custo", max_usd=100.0))
    run_id = _alvo(op, pid)["run_id"]
    roteador = RoutingProvider(harness.cfg)
    roteador.attach(repo=st.repo, settings_getter=st.settings.get)

    async def _one(*_: Any) -> Any:
        return "ok", Usage(calls=1, input_tokens=1000, output_tokens=100, role="decide",
                           model=next(iter(st.cfg.file.ai.prices)))

    roteador._funcao = lambda papel, rid: (types.SimpleNamespace(kind="anthropic", fallback_provider=None), None)  # type: ignore[method-assign,assignment,return-value]
    roteador._saldo = lambda r: None  # type: ignore[method-assign,assignment]
    roteador._one = _one  # type: ignore[method-assign,assignment]
    _, uso = await roteador._call("decide", run_id, lambda p: None)  # noqa: SLF001
    assert roteador._em_voo_da_operacao == {op["id"]: 1}, "o custo ainda não está no banco"  # noqa: SLF001
    st.repo.add_usage(run_id, None, uso)
    assert roteador._em_voo_da_operacao == {}  # noqa: SLF001
    st.repo.add_usage(run_id, None, uso)                  # gravar de novo não devolve a vaga de outra chamada
    assert roteador._em_voo_da_operacao == {}  # noqa: SLF001


async def test_so_a_coluna_ausente_vira_execucao_sem_operacao() -> None:
    """Achado do Copilot no PR 487: `OPERATIONAL_ERRORS` também pega o banco travado e o SQL inválido; engolir os dois
    apagava em silêncio a marca de estágio de um alvo que É de operação."""
    import sqlite3

    class _Banco:
        def __init__(self, erro: BaseException) -> None:
            self.erro = erro

        def one(self, *_: Any) -> Any:
            raise self.erro

    assert operacao_da_execucao(_Banco(sqlite3.OperationalError("no such column: operacao_id")), "r-1") is None  # type: ignore[arg-type]
    for erro in (sqlite3.OperationalError("database is locked"), sqlite3.OperationalError('near "SELEC": syntax error')):
        with pytest.raises(sqlite3.OperationalError):
            operacao_da_execucao(_Banco(erro), "r-1")  # type: ignore[arg-type]


async def test_registrar_estagio_marca_uma_vez_e_fora_de_operacao_nao_faz_nada(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Gil", "android-01")
    _conta(harness, pid, "qa-user-05", sessao_em="android-01")
    op = _servico(harness).criar(_pedido([AlvoPedido(pid)], chave="teste-op-marca"))
    run_id = _alvo(op, pid)["run_id"]
    assert registrar_estagio(st.db, run_id, "conhecimento_recuperado") is True
    assert registrar_estagio(st.db, run_id, "conhecimento_recuperado") is False
    assert registrar_estagio(st.db, run_id, "resultado_verificado") is False       # não é marcável de fora
    avulsa = harness.run(["android-02"])
    assert registrar_estagio(st.db, avulsa.id, "conteudo_lido") is False


async def test_so_a_coluna_ausente_vira_sem_operacao_e_o_resto_propaga() -> None:
    """Achado do Codex (corte 56): o `except Exception` engolia qualquer erro e apagava a marca em silêncio."""
    import sqlite3

    from app.db import TransacaoAbortada
    from app.modules.operacoes.infrastructure.estagios import operacao_da_execucao

    class _Banco:
        def __init__(self, erro: BaseException) -> None:
            self.erro = erro

        def one(self, *_: Any) -> Any:
            raise self.erro

    assert operacao_da_execucao(_Banco(sqlite3.OperationalError("no such column: operacao_id")), "r") is None  # type: ignore[arg-type]
    with pytest.raises(TransacaoAbortada):
        operacao_da_execucao(_Banco(TransacaoAbortada("abortada")), "r")  # type: ignore[arg-type]


# ------------------------------------------------------------------ liberar (aprovações falsas)
@dataclass
class _Pedido:
    id: str
    status: str = "pending"
    texto: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "generated_content": self.texto}


@dataclass
class _Aprovacoes:
    pedidos: dict[str, _Pedido]
    decididos: list[str] = field(default_factory=list)

    @property
    def store(self) -> Any:
        return self

    def for_step(self, step_id: str) -> _Pedido | None:
        return self.pedidos.get(step_id)

    def na_tela(self, item: dict[str, object]) -> dict[str, object]:
        return item

    def decide(self, approval_id: str, verb: str, **_: Any) -> dict[str, object]:
        assert verb == "approve"
        self.decididos.append(approval_id)
        return {}


async def test_liberar_exige_o_texto_lido_e_respeita_o_limite_de_acoes_executadas(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    st.settings.update({"operacao_max_acoes_executadas": 1})
    pids = []
    for i, iid in enumerate(("android-01", "android-02")):
        pid = _persona(harness, f"Hugo{i}", iid)
        _conta(harness, pid, f"qa-user-1{i}", sessao_em=iid)
        pids.append(pid)
    op = _servico(harness).criar(_pedido([AlvoPedido(p) for p in pids], chave="teste-op-liberar"))
    pedidos = {pid: _Pedido(id=f"apr-{i}", texto=f"texto da persona {i}") for i, pid in enumerate(pids)}
    s = ServicoDeOperacoes(st.db, st.runs, st.social_repo, _Aprovacoes({}), st.settings.get, st.bus,  # type: ignore[arg-type]
                           st.cfg.file.ai.prices)
    # O pedido de aprovação da ação preparada de cada alvo (o que a porta grava depois do rascunho), sem rodar o plano.
    s._pedido_pendente = lambda alvo: pedidos.get(str(alvo["profile_id"])) if alvo else None  # type: ignore[method-assign]
    aprovacoes = s.aprovacoes
    out = s.liberar(op["id"], [(pids[0], "outro texto"), (pids[1], "texto da persona 1"),
                               (pids[0], "texto da persona 0")])
    assert out["liberados"] == [pids[1]]
    assert {r["motivo"] for r in out["recusados"]} == {"texto_divergente", "limite de ações executadas"}
    assert out["operacao"]["acao_final"] == "executar"
    assert aprovacoes.decididos == ["apr-1"]  # type: ignore[attr-defined]


async def test_liberar_na_operacao_que_preparar_ja_fechou_reabre_e_o_liberado_fica_em_curso(harness: Harness) -> None:
    """Achado do Codex (corte 56): em `preparar`, todos os alvos na ação preparada fecham a operação ANTES da leitura dos
    textos, e `liberar` dava 409. Agora a fechada (não cancelada) libera e reabre; e o alvo cuja ação já foi aprovada
    não aparece mais "aguarda liberação" (o que a fecharia de novo antes da execução)."""
    st = harness.state
    assert st is not None
    st.settings.update({"operacao_max_acoes_executadas": 5})
    pids = []
    for i, iid in enumerate(("android-01", "android-02")):
        pid = _persona(harness, f"Lia{i}", iid)
        _conta(harness, pid, f"qa-user-4{i}", sessao_em=iid)
        pids.append(pid)
    op = _servico(harness).criar(_pedido([AlvoPedido(p) for p in pids], chave="teste-op-liberar-fechada"))
    st.db.execute("UPDATE operacoes SET status='concluida', finished_at=? WHERE id=?", (now_iso(), op["id"]))

    class _GravaAprovado(_Aprovacoes):
        """Grava a aprovação como o serviço de aprovações de sempre (`pending_approvals.status='approved'`)."""
        def decide(self, approval_id: str, verb: str, **kw: Any) -> dict[str, object]:
            # Contar e aprovar na mesma transação, com a linha da operação travada (dois backends no mesmo PG).
            assert st.db._tx_depth > 0  # noqa: SLF001
            st.db.execute("INSERT INTO pending_approvals(id, profile_id, run_id, capability, status, created_at)"
                          " VALUES (?,?,?,?,?,?)", (approval_id, pids[0], _alvo(op, pids[0])["run_id"],
                                                    "CREATE_COMMENT", "approved", now_iso()))
            return super().decide(approval_id, verb, **kw)

    s = ServicoDeOperacoes(st.db, st.runs, st.social_repo, _GravaAprovado({}), st.settings.get, st.bus,  # type: ignore[arg-type]
                           st.cfg.file.ai.prices)
    s._pedido_pendente = lambda alvo: (_Pedido(id="apr-l", texto="t")  # type: ignore[method-assign]
                                       if alvo and alvo["profile_id"] == pids[0] else None)
    # Os dois alvos na ação preparada, com a execução retomada (o que `ApprovalService.decide` faz ao aprovar).
    s._ler_alvo = lambda op_, a, d: (Leitura("acao_preparada", "em_curso", None, ()), None)  # type: ignore[method-assign]
    out = s.liberar(op["id"], [(pids[0], "t")])
    assert out["liberados"] == [pids[0]]
    lida = out["operacao"]
    estados = {a["profile_id"]: (a["estado"], a["motivo"]) for a in lida["alvos"]}  # type: ignore[attr-defined]
    assert estados == {pids[0]: ("em_curso", None), pids[1]: ("bloqueado", "aguarda liberação")}
    assert (lida["status"], lida["finished_at"], lida["acao_final"]) == ("em_curso", None, "executar")
    # A cancelada continua recusada.
    st.db.execute("UPDATE operacoes SET status='cancelada' WHERE id=?", (op["id"],))
    with pytest.raises(OperacaoError) as exc:
        s.liberar(op["id"], [(pids[0], "t")])
    assert exc.value.code == "ja_encerrada"


async def test_cancelar_pula_a_execucao_terminada_e_segue_quando_uma_termina_no_meio(harness: Harness) -> None:
    """Achado do Codex (corte 56): o laço chamava `RunService.cancel` em toda execução; a já terminada dá `RunError`, e
    a operação ficava em curso com parte dos alvos cancelada (e a rota dava 500)."""
    st = harness.state
    assert st is not None
    pids = []
    for i, iid in enumerate(("android-01", "android-02", "android-03")):
        pid = _persona(harness, f"Mel{i}", iid)
        _conta(harness, pid, f"qa-user-5{i}", sessao_em=iid)
        pids.append(pid)
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(p) for p in pids], chave="teste-op-cancelar-parcial"))
    runs = [_alvo(op, p)["run_id"] for p in pids]
    st.db.execute("UPDATE runs SET status='completed' WHERE id=?", (runs[0],))

    class _TerminaNoMeio:
        """A execução do 2º alvo termina entre a leitura e o pedido: o `cancel` de verdade recusa com `RunError`."""
        def __getattr__(self, nome: str) -> Any:
            return getattr(st.runs, nome)

        def cancel(self, run_id: str, **kw: Any) -> Any:
            if run_id == runs[1]:
                raise RunError("invalid_state", "A execução já terminou.")
            return st.runs.cancel(run_id, **kw)

    s.runs = _TerminaNoMeio()  # type: ignore[assignment]
    depois = s.cancelar(op["id"])
    assert depois["status"] == "cancelada"
    assert st.db.scalar("SELECT status FROM runs WHERE id=?", (runs[0],)) == "completed"
    r = st.db.one("SELECT status, cancel_requested FROM runs WHERE id=?", (runs[2],))
    assert r["cancel_requested"] == 1 or r["status"] == "cancelled"


async def test_acao_aprovada_por_fora_do_liberar_reabre_e_fecha_na_hora_do_ultimo_estagio(harness: Harness) -> None:
    """Onda 1 de 06/10: a ação foi aprovada no Telegram, não pelo liberar. A operação ficava `preparar`, `concluida`, com o
    `finished_at` da preparação (19:44:58) e a ação verificada depois (19:48:05). Agora vira `executar`, reabre e fecha
    na hora do último estágio, não na da leitura."""
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Nora", "android-01")
    _conta(harness, pid, "qa-user-61", sessao_em="android-01")
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-aprovada-por-fora"))
    st.db.execute("UPDATE operacoes SET status='concluida', finished_at=? WHERE id=?", ("2026-10-06T19:44:58.051Z", op["id"]))
    st.db.execute("INSERT INTO pending_approvals(id, profile_id, run_id, capability, status, created_at)"
                  " VALUES (?,?,?,?,?,?)", ("apr-fora", pid, _alvo(op, pid)["run_id"], "CREATE_COMMENT", "approved",
                                            now_iso()))
    ultimo = "2026-10-06T19:48:05.915Z"
    em_curso = Leitura("acao_preparada", "em_curso", None, (("acao_preparada", "2026-10-06T19:44:40.000Z"),))
    feito = Leitura("resultado_verificado", "concluido", None,
                    (("acao_preparada", "2026-10-06T19:44:40.000Z"), ("resultado_verificado", ultimo)))
    s._ler_alvo = lambda op_, a, d: (em_curso, None)  # type: ignore[method-assign]
    lida = s.ler(op["id"])
    assert (lida["acao_final"], lida["status"], lida["finished_at"]) == ("executar", "em_curso", None)
    s._ler_alvo = lambda op_, a, d: (feito, None)  # type: ignore[method-assign]
    lida = s.ler(op["id"])
    assert (lida["status"], lida["finished_at"]) == ("concluida", ultimo)


async def test_a_reabertura_le_os_alvos_ja_como_executar_e_o_mesmo_get_nao_fecha_de_novo(harness: Harness) -> None:
    """Achado P1 do Codex no PR 483: os alvos eram lidos com `preparar` ANTES de a aprovação por fora reabrir a operação.
    `acao_preparada` contava como concluído, `_status` fechava a operação de novo no mesmo GET e restaurava o
    `finished_at`; o GET seguinte dizia `em_curso` e o cancelar devolvia `ja_encerrada`."""
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Iara", "android-01")
    _conta(harness, pid, "qa-user-63", sessao_em="android-01")
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-reabre-e-le"))
    st.db.execute("UPDATE operacoes SET status='concluida', finished_at=? WHERE id=?", ("2026-10-06T19:44:58.051Z", op["id"]))
    st.db.execute("INSERT INTO pending_approvals(id, profile_id, run_id, capability, status, created_at)"
                  " VALUES (?,?,?,?,?,?)", ("apr-reabre", pid, _alvo(op, pid)["run_id"], "CREATE_COMMENT", "approved",
                                            now_iso()))
    preparada = (("acao_preparada", "2026-10-06T19:44:40.000Z"),)
    # Como o domínio lê: com `preparar`, a ação preparada é o fim; com `executar`, o alvo segue em curso.
    s._ler_alvo = lambda op_, a, d: ((Leitura("acao_preparada", "concluido", None, preparada)  # type: ignore[method-assign]
                                      if op_["acao_final"] == "preparar"
                                      else Leitura("acao_preparada", "em_curso", None, preparada)), None)
    lida = s.ler(op["id"])
    assert (lida["acao_final"], lida["status"], lida["finished_at"]) == ("executar", "em_curso", None)
    assert st.db.one("SELECT status, finished_at FROM operacoes WHERE id=?", (op["id"],))["finished_at"] is None
    assert s.ler(op["id"])["status"] == "em_curso"
    cancelada = s.cancelar(op["id"])                       # em curso de verdade: cancela, não `ja_encerrada`
    assert cancelada["status"] == "cancelada"


async def test_a_reabertura_nao_ressuscita_a_operacao_cancelada_entre_a_leitura_e_o_update(harness: Harness) -> None:
    """Achado do Copilot no PR 487: o `ler` lia a operação `concluida` e, antes do UPDATE que reabre, um cancelar
    concorrente gravava `cancelada`; o UPDATE (só `acao_final='preparar'`) a sobrescrevia com `em_curso`."""
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Kaia", "android-01")
    _conta(harness, pid, "qa-user-64", sessao_em="android-01")
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-reabre-cancelada"))
    st.db.execute("UPDATE operacoes SET status='concluida', finished_at=? WHERE id=?", ("2026-10-06T19:44:58.051Z", op["id"]))
    run_id = _alvo(op, pid)["run_id"]
    aprovados = s._runs_com_acao_aprovada  # noqa: SLF001

    def _cancelada_no_meio(op_id: str) -> set[object]:
        st.db.execute("UPDATE operacoes SET status='cancelada', finished_at=? WHERE id=?", (now_iso(), op_id))
        return {run_id}

    s._runs_com_acao_aprovada = _cancelada_no_meio  # type: ignore[method-assign]
    s._ler_alvo = lambda op_, a, d: (Leitura("acao_preparada", "em_curso", None,  # type: ignore[method-assign]
                                             (("acao_preparada", "2026-10-06T19:44:40.000Z"),)), None)
    s.ler(op["id"])
    linha = st.db.one("SELECT status, acao_final, finished_at FROM operacoes WHERE id=?", (op["id"],))
    assert (linha["status"], linha["acao_final"]) == ("cancelada", "preparar") and linha["finished_at"]
    s._runs_com_acao_aprovada = aprovados  # type: ignore[method-assign]


async def test_o_get_traz_as_fontes_que_a_pesquisa_achou(harness: Harness) -> None:
    """Achado do percurso da Portal (onda 1, 06/10): `pesquisa_usd` 0,0526 e 0 fontes no GET. A pesquisa grava as URLs em
    `pedido_observacoes` (`tipo='url'`, migração 125); `fontes` é só a entrada do pedido."""
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Olivia", "android-02")
    _conta(harness, pid, "qa-user-62", sessao_em="android-02")
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-fontes-da-pesquisa"))
    assert s.ler(op["id"])["fontes_da_pesquisa"] == []
    colunas = {r["name"] for r in st.db.query("PRAGMA table_info(pedido_observacoes)")} if st.db.dialect == "sqlite" else {"operacao_id"}
    if "operacao_id" not in colunas:
        pytest.skip("a migração 125 (pedido_observacoes.operacao_id) vem da main; roda na integração do corte")
    for i, url in enumerate(("https://exemplo.com.br/a", "https://exemplo.com.br/b", "https://exemplo.com.br/a")):
        st.db.execute("INSERT INTO pedido_observacoes(id, operacao_id, ocorrencia_id, alvo, nome, tipo, situacao, valor,"
                      " capturado_em) VALUES (?,?,?,?,?,?,?,?,?)",
                      (f"obs-{i}", op["id"], f"oc-{i}", "", f"fonte.{i}", "url", "observado", url, f"2026-10-06T19:44:0{i}Z"))
    lida = s.ler(op["id"])
    assert lida["fontes_da_pesquisa"] == ["https://exemplo.com.br/a", "https://exemplo.com.br/b"]
    assert lida["fontes"] == []


# ------------------------------------------------------------------ a rota
async def test_rota_http_criar_ler_listar_filtrar_cancelar_liberar(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Iara", "android-01")
    _conta(harness, pid, "qa-user-21", sessao_em="android-01")
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    corpo = {"command": COMMAND, "app_id": APP, "alvos": [{"profile_id": pid}], "idempotency_key": "teste-op-http1",
             "max_usd": 0.5, "assunto": "o lançamento da coleção", "fontes": ["https://exemplo.com.br/noticia"]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/operacoes", json={k: v for k, v in corpo.items() if k != "max_usd"})
        assert r.status_code == 422                                    # max_usd é obrigatório
        for fonte in ("http://inseguro", "https://exemplo.com.br/x?token=abc", "https://eu:senha@exemplo.com.br/"):
            r = await c.post("/api/operacoes", json={**corpo, "fontes": [fonte]})
            assert r.status_code == 422, fonte
        r = await c.post("/api/operacoes", json=corpo)
        assert r.status_code == 201, r.text
        op = r.json()
        assert op["assunto"] == "o lançamento da coleção" and op["fontes"] == ["https://exemplo.com.br/noticia"]
        r = await c.get(f"/api/operacoes/{op['id']}")
        assert r.status_code == 200 and r.json()["alvos"][0]["profile_id"] == pid
        r = await c.get("/api/operacoes")
        assert r.status_code == 200 and r.json()["items"][0]["id"] == op["id"]
        r = await c.get("/api/runs", params={"operacao_id": op["id"]})
        assert r.status_code == 200 and [x["operacao_id"] for x in r.json()["runs"]] == [op["id"]]
        r = await c.post(f"/api/operacoes/{op['id']}/liberar", json={"itens": [{"profile_id": pid, "texto": "x"}]})
        assert r.status_code == 200 and r.json()["recusados"][0]["motivo"] == "sem ação preparada"
        r = await c.post(f"/api/operacoes/{op['id']}/cancelar")
        assert r.status_code == 200 and r.json()["status"] == "cancelada"
        r = await c.post(f"/api/operacoes/{op['id']}/cancelar")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "ja_encerrada"
        r = await c.get("/api/operacoes/nao-existe")
        assert r.status_code == 404 and r.json()["detail"]["code"] == "operacao_inexistente"


async def test_o_limite_conta_a_acao_ja_aprovada_e_uma_segunda_liberacao_nao_passa_do_teto(harness: Harness) -> None:
    """Achado do revisor-segredos (06/10): contar só a ação EXECUTADA deixava uma segunda liberação aprovar de novo antes de a
    primeira rodar."""
    st = harness.state
    assert st is not None
    st.settings.update({"operacao_max_acoes_executadas": 1})
    pids = []
    for i, iid in enumerate(("android-01", "android-02")):
        pid = _persona(harness, f"Jo{i}", iid)
        _conta(harness, pid, f"qa-user-3{i}", sessao_em=iid)
        pids.append(pid)
    op = _servico(harness).criar(_pedido([AlvoPedido(p) for p in pids], chave="teste-op-limite2"))
    st.db.execute("INSERT INTO pending_approvals(id, profile_id, run_id, capability, status, created_at)"
                  " VALUES (?,?,?,?,?,?)", ("apr-ja", pids[0], _alvo(op, pids[0])["run_id"], "CREATE_COMMENT",
                                            "approved", now_iso()))
    s = ServicoDeOperacoes(st.db, st.runs, st.social_repo, _Aprovacoes({}), st.settings.get, st.bus,  # type: ignore[arg-type]
                           st.cfg.file.ai.prices)
    s._pedido_pendente = lambda alvo: _Pedido(id="apr-2", texto="t") if alvo else None  # type: ignore[method-assign]
    out = s.liberar(op["id"], [(pids[1], "t")])
    assert out["liberados"] == [] and out["recusados"] == [{"profile_id": pids[1], "motivo": "limite de ações executadas"}]
    # A ação já aprovada (por fora do liberar) leva a operação a `executar` (28.61, o fim real); esta liberação não
    # aprovou nada a mais.
    assert out["operacao"]["acao_final"] == "executar"


async def test_credencial_no_assunto_ou_na_fonte_e_recusada_e_o_motivo_sai_redigido(harness: Harness) -> None:
    pid = _persona(harness, "Kai")
    s = _servico(harness)
    for kw in ({"assunto": "entre com Senha: segredo123 e leia"}, {"fontes": ("https://x.com.br/Senha: segredo123",)}):
        with pytest.raises(OperacaoError) as exc:
            s.criar(_pedido([AlvoPedido(pid)], chave=f"teste-op-cred-{len(kw)}{list(kw)[0]}", **kw))
        assert exc.value.code == "credencial_no_comando"
    assert "segredo123" not in (_motivo("a tela pediu Senha: segredo123 de novo") or "")
    # O motivo da regra da frota cita o @ do alvo; o da operação diz "o perfil alvo" (achado do percurso da Portal).
    frota = _motivo("1 outra(s) conta(s) da frota já mexeram com @conta.de_teste9 nos últimos 30 dias")
    assert frota is not None and "@" not in frota and "o perfil alvo" in frota
    assert _motivo("escreva para fulano@exemplo.com") == "escreva para fulano@exemplo.com"     # e-mail não é @ de conta


async def test_custo_por_alvo_e_o_da_execucao_dele(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """`custo_usd` do alvo = `spent_usd` da execução dele (nulo sem execução); com texto, vem também em `resultado`."""
    st = harness.state
    assert st is not None
    com = _persona(harness, "Gabi", "android-01")
    _conta(harness, com, "qa-user-05", sessao_em="android-01")
    sem = _persona(harness, "Hugo")
    servico = _servico(harness)
    op = servico.criar(_pedido([AlvoPedido(com), AlvoPedido(sem)], chave="teste-op-custo-alvo"))
    run_id = _alvo(op, com)["run_id"]
    modelo = next(iter(st.cfg.file.ai.prices))
    st.db.execute("INSERT INTO ai_calls(ts, run_id, role, model, input_tokens, output_tokens, ok) VALUES (?,?,?,?,?,?,?)",
                  (now_iso(), run_id, "plan", modelo, 10_000, 1_000, 1))
    monkeypatch.setattr(ServicoDeOperacoes, "_resultado", lambda self, a, efeito, marcas: {"texto": "rascunho"})
    lido = servico.ler(op["id"])
    alvo_com, alvo_sem = _alvo(lido, com), _alvo(lido, sem)
    from app.planning import costs
    esperado = round(costs.spent_usd(st.db, st.cfg.file.ai.prices, run_id=run_id), 4)
    assert esperado > 0 and alvo_com["custo_usd"] == esperado and alvo_com["resultado"]["custo_usd"] == esperado
    assert alvo_sem["custo_usd"] is None
    assert lido["custo"]["total_usd"] == esperado                       # a soma dos alvos é o total da operação


async def test_conta_com_sessao_em_dois_aparelhos_executa_so_no_vinculo_principal(harness: Harness) -> None:
    """Sem `instance_id`, a conta com sessão em dois aparelhos vai ao vínculo PRINCIPAL da persona, não à sessão mais
    recente; sem sessão no principal, para com o motivo. Com `instance_id`, vale o pedido (ramo de sempre)."""
    st = harness.state
    assert st is not None
    repo = st.social_repo
    no_principal = _persona(harness, "Ivo", "android-02")              # principal: android-02
    conta = _conta(harness, no_principal, "qa-user-06", sessao_em="android-02")
    repo.set_account_session(no_principal, conta, "android-01", status=SessionStatus.session_ready,
                             verified_at=now_iso())                      # a mais recente é a do android-01
    fora = _persona(harness, "Juca", "android-03")                      # principal: android-03, sem sessão lá
    conta_fora = _conta(harness, fora, "qa-user-07", sessao_em="android-01")
    repo.set_account_session(fora, conta_fora, "android-02", status=SessionStatus.session_ready, verified_at=now_iso())
    op = _servico(harness).criar(_pedido([AlvoPedido(no_principal), AlvoPedido(fora)], chave="teste-op-principal"))
    a, b = _alvo(op, no_principal), _alvo(op, fora)
    assert a["instance_id"] == "android-02" and a["run_id"]
    assert (b["estado"], b["motivo"], b["parou_em"], b["run_id"]) == (
        "bloqueado", "sessão fora do aparelho principal", "sessao", None)


async def test_o_get_traz_a_latencia_por_estagio_por_alvo_e_da_operacao(harness: Harness) -> None:
    """Latência por estágio e por alvo no GET (métrica de primeira classe do dono, ao lado de custo e sucesso): cada estágio
    com `etapa_ms`, o alvo com a duração e a espera pelo liberar à parte, e a operação com n/p50/p95/máx por estágio."""
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Lia", "android-01")
    _conta(harness, pid, "qa-user-71", sessao_em="android-01")
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-latencia"))
    st.db.execute("UPDATE operacoes SET created_at=? WHERE id=?", ("2026-10-07T10:00:00.000Z", op["id"]))
    lida_do_alvo = Leitura("resultado_verificado", "concluido", None,
                           (("persona", "2026-10-07T10:00:00.000Z"), ("aparelho", "2026-10-07T10:00:30.000Z"),
                            ("acao_preparada", "2026-10-07T10:02:00.000Z"),
                            ("acao_executada", "2026-10-07T10:09:00.000Z"),
                            ("resultado_verificado", "2026-10-07T10:09:00.000Z")),
                           liberado_em="2026-10-07T10:08:00.000Z")
    s._ler_alvo = lambda op_, a, d: (lida_do_alvo, None)  # type: ignore[method-assign]
    lida = s.ler(op["id"])
    alvo = lida["alvos"][0]  # type: ignore[index]
    assert [(e["estagio"], e["etapa_ms"]) for e in alvo["estagios"]] == [
        ("persona", 0), ("aparelho", 30_000), ("acao_preparada", 90_000), ("acao_executada", 60_000),
        ("resultado_verificado", 0)]
    assert alvo["latencia"] == {"duracao_ms": 540_000, "espera_do_liberar_ms": 360_000}
    assert lida["latencia_por_estagio"]["acao_executada"] == {"n": 1, "p50_ms": 60_000, "p95_ms": 60_000,  # type: ignore[index]
                                                              "max_ms": 60_000}


async def test_cancelar_alvos_por_filtro_cancela_so_os_que_casam_e_a_operacao_segue(harness: Harness) -> None:
    """Rodada de 30 alvos: cancelar é tudo ou nada; `cancelar_alvos` cancela só os que casam com TODOS os filtros
    (perfil, estado, estágio, aparelho), pula a execução terminada e deixa a operação seguir com os outros."""
    st = harness.state
    assert st is not None
    pids = []
    for i, iid in enumerate(("android-01", "android-02", "android-03")):
        pid = _persona(harness, f"Zoe{i}", iid)
        _conta(harness, pid, f"qa-user-8{i}", sessao_em=iid)
        pids.append(pid)
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(p) for p in pids], chave="teste-op-cancelar-alvos"))
    runs = [_alvo(op, p)["run_id"] for p in pids]
    st.db.execute("UPDATE runs SET status='completed' WHERE id=?", (runs[0],))
    for vazio in ({}, {"estados": []}):
        with pytest.raises(OperacaoError) as exc:
            s.cancelar_alvos(op["id"], **vazio)
        assert exc.value.code == "filtro_vazio" and exc.value.status == 422
    with pytest.raises(OperacaoError) as exc:
        s.cancelar_alvos(op["id"], estados=["travado"])
    assert exc.value.code == "estado_desconhecido"
    # o filtro por aparelho pega o 1º (já terminado: ignorado) e o 2º; o 3º fica de fora
    feito = s.cancelar_alvos(op["id"], instance_ids=["android-01", "android-02"], quem="Flavio")
    assert feito["cancelados"] == [pids[1]]
    assert feito["ignorados"] == [{"profile_id": pids[0], "motivo": "ja_terminou"}]
    r1 = st.db.one("SELECT status, cancel_requested FROM runs WHERE id=?", (runs[1],))
    assert r1["cancel_requested"] == 1 or r1["status"] == "cancelled"
    assert st.db.scalar("SELECT status FROM runs WHERE id=?", (runs[2],)) not in ("cancelled",)
    assert st.db.scalar("SELECT cancel_requested FROM runs WHERE id=?", (runs[2],)) in (0, None)
    assert feito["operacao"]["status"] != "cancelada"  # type: ignore[index]
    # os filtros se somam: perfil certo com aparelho errado não casa com ninguém
    nada = s.cancelar_alvos(op["id"], profile_ids=[pids[2]], instance_ids=["android-01"])
    assert (nada["cancelados"], nada["ignorados"]) == ([], [])
    st.db.execute("UPDATE operacoes SET status='cancelada' WHERE id=?", (op["id"],))
    with pytest.raises(OperacaoError) as exc:
        s.cancelar_alvos(op["id"], profile_ids=[pids[2]])
    assert exc.value.code == "ja_encerrada"


async def test_rota_http_cancelar_alvos(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Ivy", "android-01")
    _conta(harness, pid, "qa-user-91", sessao_em="android-01")
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    op = _servico(harness).criar(_pedido([AlvoPedido(pid)], chave="teste-op-http-cancelar-alvos"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(f"/api/operacoes/{op['id']}/cancelar-alvos", json={})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "filtro_vazio"
        r = await c.post(f"/api/operacoes/{op['id']}/cancelar-alvos", json={"perfis": [pid]})
        assert r.status_code == 422                                  # campo desconhecido
        r = await c.post(f"/api/operacoes/{op['id']}/cancelar-alvos", json={"profile_ids": [pid]})
        assert r.status_code == 200 and r.json()["cancelados"] == [pid]
        r = await c.post("/api/operacoes/nao-existe/cancelar-alvos", json={"profile_ids": [pid]})
        assert r.status_code == 404


async def test_o_get_com_lote_e_o_mesmo_sem_lote(harness: Harness) -> None:
    """31.194: o GET busca de uma vez, para todos os alvos, execução, objetivo, persona, conta, sessão, trava do aparelho
    e custo (com 30 alvos eram 278 consultas por leitura). A resposta tem de ser a MESMA da leitura de um alvo por vez:
    custo com pesquisa separada, aparelho travado, execução terminada e alvo sem sessão no mesmo exemplo."""
    st = harness.state
    assert st is not None
    pids = []
    for i, iid in enumerate(("android-01", "android-02", "android-03")):
        pid = _persona(harness, f"Lote{i}", iid)
        _conta(harness, pid, f"qa-user-7{i}", sessao_em=iid if i < 2 else None)
        pids.append(pid)
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(p) for p in pids], chave="teste-op-lote"))
    runs = [_alvo(op, p)["run_id"] for p in pids]
    modelo = next(iter(st.cfg.file.ai.prices))
    for rid, origem, tokens in ((runs[0], None, 120_000), (runs[0], "pesquisa", 40_000), (runs[1], None, 7_000)):
        st.db.execute("INSERT INTO ai_calls(ts, run_id, role, model, input_tokens, output_tokens, ok, origem)"
                      " VALUES (?,?,?,?,?,?,?,?)", (now_iso(), rid, "plan", modelo, tokens, tokens // 10, 1, origem))
    st.db.execute("UPDATE runs SET status='completed' WHERE id=?", (runs[1],))
    st.db.execute("INSERT INTO device_locked_accounts(instance_id, handle, origin, since, created_at, resolved_at)"
                  " VALUES (?,?,?,?,?,?)", ("android-02", "qa-user-71", "declarado", now_iso(), now_iso(), None))
    s.ler(op["id"])                                   # a 1ª leitura anota os estágios; as seguintes só leem
    com = s.ler(op["id"])
    s.com_lote = False
    sem = s.ler(op["id"])
    assert com == sem
    assert com["custo"]["pesquisa_usd"] > 0 and com["capacidade"]["contas_disponiveis"] < 2  # type: ignore[index,operator]


async def test_o_relatorio_consolidado_da_operacao(harness: Harness) -> None:
    """31.195 (adendo v1.111): a mesma leitura para a Canais e a Portal. Os 19 critérios (os 16 do dono com 2b, 3b e
    11b), quantas identidades executam hoje, textos repetidos, latência, custo por peça e o aprendizado; "não medido" é
    None ou "nao_medido", nunca zero; nenhum @ de conta sai."""
    st = harness.state
    assert st is not None
    pids = []
    for i, iid in enumerate(("android-01", "android-02")):
        pid = _persona(harness, f"Rel{i}", iid)
        _conta(harness, pid, f"qa-user-6{i}", sessao_em=iid)
        pids.append(pid)
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(p) for p in pids], chave="teste-op-relatorio"))
    st.db.execute("UPDATE operacoes SET created_at=? WHERE id=?", ("2026-10-07T10:00:00.000Z", op["id"]))
    run0 = _alvo(op, pids[0])["run_id"]
    st.db.execute("INSERT INTO ai_calls(ts, run_id, role, model, input_tokens, output_tokens, ok, provider)"
                  " VALUES (?,?,?,?,?,?,?,?)", (now_iso(), run0, "plan", "simulado", 10, 1, 1, "simulated"))
    feito = Leitura("resultado_verificado", "concluido", None,
                    (("persona", "2026-10-07T10:00:00.000Z"), ("post_localizado", "2026-10-07T10:01:00.000Z"),
                     ("resposta_gerada", "2026-10-07T10:02:00.000Z"), ("acao_executada", "2026-10-07T10:03:00.000Z")))
    parado = Leitura("post_localizado", "bloqueado", "a conta @alguem.real não abriu", (
        ("persona", "2026-10-07T10:00:00.000Z"), ("post_localizado", "2026-10-07T10:05:00.000Z")),
        parou_em="conteudo_lido")
    resultados = {pids[0]: (feito, {"texto": "Que bom ver isso, @alguem.real!", "conhecimento_ids": [],
                                    "evidencia_id": 7, "acao_final": {"tipo": "CREATE_COMMENT", "verificada": True,
                                                                      "evidencia_id": 8}}),
                  pids[1]: (parado, None)}
    s._ler_alvo = lambda op_, a, d: resultados[str(a["profile_id"])]  # type: ignore[method-assign]
    r = s.relatorio(op["id"], None)
    assert r["ambiente"] == "simulado"
    assert "@alguem.real" not in str(r)
    criterios = {c["id"]: c for c in r["criterios"]}  # type: ignore[attr-defined]
    assert len(criterios) == 19 and {"2b", "3b", "11b"} <= set(criterios)
    assert criterios["13"]["nesta_operacao"] == "sim" and criterios["13"]["estado"] == "testado_em_simulacao"
    # a base do diagnóstico não desce: o 9 já foi provado em ambiente real, e uma operação simulada não o rebaixa
    assert criterios["9"]["nesta_operacao"] == "sim" and criterios["9"]["estado"] == "provado_real"
    assert criterios["5"]["estado"] == "nao_implementado" and r["criterios_base"].startswith("diagnóstico")  # type: ignore[union-attr]
    assert criterios["2"]["nesta_operacao"] == "nao"                 # 2 alvos, não 20
    assert criterios["12"]["nesta_operacao"] == "nao_medido"         # um texto só não mede diferença
    assert criterios["16"] == {"id": "16", "nome": "Preservar aprendizado", "estado": "testado_em_simulacao",
                               "nesta_operacao": "nao_medido", "evidencia": None}
    assert r["identidades"]["solicitadas"] == 2  # type: ignore[index]
    assert r["identidades"]["deficit"] == 2 - r["identidades"]["executam_hoje"]  # type: ignore[index,operator]
    agentes = {a["profile_id"]: a for a in r["agentes"]}  # type: ignore[attr-defined]
    assert agentes[pids[0]]["acao_final"]["verificada"] == "sim" and agentes[pids[0]]["conhecimento_ids"] == []
    assert agentes[pids[1]]["acao_final"] is None and agentes[pids[1]]["texto"] is None
    assert r["falhas_por_motivo"] == [{"motivo": "a conta @[omitido] não abriu", "parou_em": "conteudo_lido",
                                       "agentes": 1}]
    assert r["latencia"]["mais_lento"] == {"profile_id": pids[1], "duracao_ms": 300_000}  # type: ignore[index]
    assert r["latencia"]["duracao_mediana_ms"] == 240_000  # type: ignore[index]
    assert r["custo"]["por_peca_usd"] == 0.0 and r["custo"]["teto_usd"] == op["max_usd"]  # type: ignore[index]
    assert r["aprendizado"]["disponivel"] is False  # type: ignore[index]
    sem_texto = s.relatorio(op["id"], {"disponivel": True, "perguntas": []})
    assert sem_texto["aprendizado"]["disponivel"] is True  # type: ignore[index]
    assert {c["id"]: c for c in sem_texto["criterios"]}["16"]["nesta_operacao"] == "sim"  # type: ignore[attr-defined]


async def test_rota_http_do_relatorio(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Ana", "android-01")
    _conta(harness, pid, "qa-user-95", sessao_em="android-01")
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    op = _servico(harness).criar(_pedido([AlvoPedido(pid)], chave="teste-op-http-relatorio"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/operacoes/{op['id']}/relatorio")
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["operacao"]["id"] == op["id"] and len(corpo["criterios"]) == 19
        assert set(corpo) >= {"gerado_em", "ambiente", "capacidade", "identidades", "agentes", "falhas_por_motivo",
                              "textos", "aprendizado", "latencia", "custo"}
        r = await c.get("/api/operacoes/nao-existe/relatorio")
        assert r.status_code == 404
