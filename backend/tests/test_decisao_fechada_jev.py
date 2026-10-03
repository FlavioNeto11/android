"""O decisor REAL da porta `DecisaoFechada` (item 31.14): `DecisorJev`, gasto conferido ANTES do POST e a chamada em `ai_calls`.

O que cada bloco prova:

- **Fio:** `choice` e `noul` (este desde o 31.13, a R5) vão ao Jev, no formato oficial (`{state, model, questions}`, `criteria` = as opções enviadas), pelo
  transporte do adaptador de retrieval; a resposta volta com o FORMATO conferido (o resto vira `parse`) e o custo da chamada.
- **Gasto antes do POST:** régua estourada, conferência ausente ou quebrada = `orcamento`, e nada sai nem vira linha.
- **Falha do transporte:** cada erro vira o motivo fechado da porta, a chamada tentada vira linha `ok=0`, e a chave ausente
  não é chamada (`desligado`, sem linha).
- **Régua que enxerga:** a linha em `ai_calls` (provedor `jev`, origem `decisao_fechada`, `usd` declarado) é o que a fatia do
  Jev soma: depois de gastar a fatia, o próximo pedido para ANTES do POST. O saldo bloqueado da conta também barra.
- **Existir não é enviar:** com `JEV_RUNTIME_SEND_APPROVED` falso (o interruptor, aberto de fábrica desde o 31.17) a porta
  recusa antes do decisor, e a composição só liga o real com `ai.decisao_fechada.decisor: jev`.

Prova `simulated`: o Jev só fala com `httpx.MockTransport` (o fixture derruba qualquer socket), banco de teste e hub com
provedor falso. Nada aqui prova o serviço real; chamada real ao Jev: `not_run`.
"""
from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import DecisaoFechadaCfg
from app.db import Database
from app.modules.context_retrieval.adapters.jev import JevSemanticProvider
from app.planning import costs, saldos
from app.planning.decisao_fechada import privacidade, transparencia
from app.planning.decisao_fechada.contrato import FalhaDeDecisao, PedidoDeDecisao, Pergunta, pergunta_choice
from app.planning.decisao_fechada.decisores import ChamadaAoJev, DecisorJev, DecisorNulo
from app.planning.decisao_fechada.porta import Porta
from app.planning.decisao_fechada.sombra import RepositorioDeSombra, observador_de_sombra
from app.planning.provider import AIError

from .conftest import Harness, _dsn_de_teste
from .test_origem_e_rubrica_de_ia import _hub

CHAVE_FALSA = "chave-falsa-" + "y" * 16  # só para provar que não vaza; não é credencial
OPCOES = {"opt:a": "primeira", "opt:b": "segunda"}
#: 1 000 tokens de entrada no Jev = US$ 0,000042 (entrada a US$ 0,042 por milhão; saída grátis).
USO = {"input_tokens": 1000, "output_tokens": 3}
USD_POR_CHAMADA = 1000 * 0.042 / 1_000_000
#: Relógio fixo (regra dos testes): a linha nasce num dia conhecido e a régua do dia olha o mesmo dia.
MEIO_DIA = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
INICIO_DO_DIA = "2026-10-03T00:00:00.000Z"


@pytest.fixture(autouse=True)
def sem_rede(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Qualquer conexão de socket nesta rodada é bug: o Jev só pode falar com o MockTransport. O teste da composição
    (`harness`) fica de fora: no Windows o laço de eventos abre o próprio par de sockets por `connect` em loopback, e
    ele só monta o decisor (nada chama o Jev)."""
    if "harness" in request.fixturenames:
        yield
        return
    tentativas: list[str] = []

    def proibido(*a: Any, **k: Any):
        tentativas.append("connect")
        raise AssertionError("REAL_JEV_NETWORK_CALLS: conexao de rede proibida nos testes")

    monkeypatch.setattr(socket.socket, "connect", proibido)
    monkeypatch.setattr(socket, "create_connection", proibido)
    yield
    assert tentativas == [], "houve tentativa de conexao de rede"


class Servidor:
    """O lado da TypeSafe, falso: guarda cada corpo recebido e responde o que o teste mandar."""

    def __init__(self, resposta: Any = None, *, status: int = 200, erro: Exception | None = None) -> None:
        self.corpos: list[dict[str, Any]] = []
        self.cabecalhos: list[httpx.Headers] = []
        self.resposta = resposta
        self.status = status
        self.erro = erro

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.corpos.append(json.loads(request.content))
        self.cabecalhos.append(request.headers)
        if self.erro is not None:
            raise self.erro
        if isinstance(self.resposta, str):
            return httpx.Response(self.status, content=self.resposta.encode())
        return httpx.Response(self.status, json=self.resposta)


def _ok(escolha: str = "opt:a", confianca: float = 0.9, **extra: Any) -> dict[str, Any]:
    resposta = {"type": "choice", "choice": escolha, "confidence": confianca,
                "probabilities": {"opt:a": confianca, "opt:b": round(1 - confianca, 2), "opt:nenhuma": 0.0}}
    resposta.update(extra)
    return {"model": "jev-1.13.0", "answers": {"q1": resposta}, "usage": USO}


def _jev(servidor: Servidor, *, env: dict[str, str] | None = None) -> JevSemanticProvider:
    return JevSemanticProvider(env={"TYPESAFE_API_KEY": CHAVE_FALSA} if env is None else env,
                               transport=httpx.MockTransport(servidor))


def _pedido(*perguntas: Pergunta, **kw: Any) -> PedidoDeDecisao:
    base: dict[str, Any] = dict(origem="curador", classe="C0", estado={"kind": "licao", "falhas": "2"}, modo="shadow",
                                run_id="run-1", ref="item-1", step_id="st-1",
                                perguntas=perguntas or (pergunta_choice("q1", "Pick one.", OPCOES, limiar=0.8),))
    base.update(kw)
    return PedidoDeDecisao(**base)


def _decisor(servidor: Servidor, *, gasto: Any = lambda p: None, env: dict[str, str] | None = None
             ) -> tuple[DecisorJev, list[ChamadaAoJev]]:
    linhas: list[ChamadaAoJev] = []
    return DecisorJev(_jev(servidor, env=env), conferir_gasto=gasto, registrar=linhas.append), linhas


def _banco(tmp: Path) -> Database:
    db = Database(_dsn_de_teste() or tmp / "jev.sqlite3")
    db.migrate()
    return db


# ---------------------------------------------------------------- fio
def test_choice_vai_ao_fio_oficial_e_volta_com_o_custo_da_chamada() -> None:
    servidor = Servidor(_ok())
    decisor, linhas = _decisor(servidor)
    res = decisor.decidir(_pedido(), 5.0)
    [corpo] = servidor.corpos
    assert corpo["state"] == {"kind": "licao", "falhas": "2"} and corpo["model"] == "jev-1.13.0"
    [(qid, pergunta)] = corpo["questions"].items()
    assert (qid, pergunta["type"], pergunta["instructions"]) == ("q1", "choice", "Pick one.")
    assert set(pergunta["criteria"]) == {"opt:a", "opt:b", "opt:nenhuma"} and pergunta["criteria"]["opt:a"] == "primeira"
    assert set(corpo) == {"state", "model", "questions"}          # run_id, ref e step_id nunca vão no corpo
    assert servidor.cabecalhos[0]["authorization"] == f"Bearer {CHAVE_FALSA}"
    r = res.respostas["q1"]
    assert (r.escolha, r.confianca, r.fallback_reason) == ("opt:a", 0.9, None)
    assert res.tokens == 1003 and res.usd == pytest.approx(USD_POR_CHAMADA) and res.fallback_reason is None
    [linha] = linhas
    assert (linha.ok, linha.motivo, linha.tokens_entrada, linha.tokens_saida, linha.modelo, linha.origem) == (
        True, None, 1000, 3, "jev-1.13.0", "curador")
    assert linha.usd == pytest.approx(USD_POR_CHAMADA) and (linha.run_id, linha.ref, linha.step_id) == (
        "run-1", "item-1", "st-1")
    assert CHAVE_FALSA not in repr(decisor) and CHAVE_FALSA not in repr(linha)


@pytest.mark.parametrize("resposta", [
    {"type": "noul", "noul": 0.9},                                           # tipo errado
    {"type": "choice", "choice": "opt:a", "probabilities": {"opt:a": 0.9}},  # sem confidence
    {"type": "choice", "choice": "opt:a", "confidence": 1.7, "probabilities": {"opt:a": 0.9}},
    {"type": "choice", "choice": "opt:a", "confidence": 0.9, "probabilities": {"opt:a": True}},
    {"type": "choice", "choice": 3, "confidence": 0.9, "probabilities": {"opt:a": 0.9}},
    "nao-e-objeto",
])
def test_resposta_fora_do_formato_vira_parse_e_a_chamada_conta(resposta: Any) -> None:
    servidor = Servidor({"answers": {"q1": resposta}, "usage": USO})
    decisor, linhas = _decisor(servidor)
    res = decisor.decidir(_pedido(), 5.0)
    assert res.respostas["q1"].fallback_reason == "parse" and res.respostas["q1"].escolha is None
    assert [l.ok for l in linhas] == [True]                     # o POST aconteceu e foi cobrado: vira linha


def test_score_nao_sai_e_choice_e_noul_vao_ao_fio() -> None:
    score = Pergunta("q3", "score", "How much?")
    so_score = Servidor(_ok())
    decisor, linhas = _decisor(so_score)
    res = decisor.decidir(_pedido(score), 5.0)
    assert so_score.corpos == [] and linhas == [] and res.fallback_reason == "desligado"
    noul = Pergunta("q2", "noul", "Is it?", {"true": "It is.", "false": "It is not."})
    resposta = _ok()
    resposta["answers"]["q2"] = {"type": "noul", "noul": 0.91}
    misto = Servidor(resposta)
    decisor, _ = _decisor(misto)
    res = decisor.decidir(_pedido(pergunta_choice("q1", "Pick one.", OPCOES), noul, score), 5.0)
    perguntas = misto.corpos[0]["questions"]
    assert list(perguntas) == ["q1", "q2"]
    assert perguntas["q2"] == {"type": "noul", "instructions": "Is it?",
                               "criteria": {"true": "It is.", "false": "It is not."}}
    assert res.respostas["q1"].escolha == "opt:a" and res.respostas["q3"].fallback_reason == "desligado"
    r = res.respostas["q2"]
    assert (r.escolha, r.confianca, dict(r.probabilidades), r.fallback_reason) == ("sim", 0.91, {"sim": 0.91}, None)


def test_noul_sem_criterios_vai_sem_criteria() -> None:
    servidor = Servidor({"answers": {"q2": {"type": "noul", "noul": 0.5}}, "usage": USO})
    decisor, _ = _decisor(servidor)
    decisor.decidir(_pedido(Pergunta("q2", "noul", "Is it?")), 5.0)
    assert servidor.corpos[0]["questions"]["q2"] == {"type": "noul", "instructions": "Is it?"}


@pytest.mark.parametrize("resposta", [
    {"type": "noul", "noul": 1.3}, {"type": "noul", "noul": -0.1}, {"type": "noul", "noul": True}, {"type": "noul"},
    {"type": "choice", "choice": "opt:a", "confidence": 0.9, "probabilities": {"opt:a": 0.9}},   # tipo errado
    "nao-e-objeto",
])
def test_noul_fora_do_formato_vira_parse(resposta: Any) -> None:
    servidor = Servidor({"answers": {"q2": resposta}, "usage": USO})
    decisor, linhas = _decisor(servidor)
    res = decisor.decidir(_pedido(Pergunta("q2", "noul", "Is it?")), 5.0)
    assert res.respostas["q2"].fallback_reason == "parse" and res.respostas["q2"].escolha is None
    assert [l.ok for l in linhas] == [True]


def test_porta_mede_o_limiar_do_noul_e_abaixo_dele_e_sem_resposta(monkeypatch: pytest.MonkeyPatch,
                                                                   tmp_path: Path) -> None:
    """B7 do roteiro: abaixo do limiar o `noul` conta como SEM resposta; o complemento nunca vira `nao`."""
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)
    db = _banco(tmp_path)
    cfg = DecisaoFechadaCfg(enabled=True, consumidores={"curador": "on"})  # type: ignore[arg-type]
    for p, escolha, motivo in ((0.9, "sim", None), (0.85, "sim", None), (0.84, None, "abaixo_do_limiar"),
                               (0.05, None, "abaixo_do_limiar")):
        decisor, _ = _decisor(Servidor({"answers": {"q2": {"type": "noul", "noul": p}}, "usage": USO}))
        r = Porta(decisor, cfg=cfg, observador=observador_de_sombra(RepositorioDeSombra(db))).consultar(
            _pedido(Pergunta("q2", "noul", "Is it?"), modo="on")).respostas["q2"]
        assert (r.escolha, r.fallback_reason) == (escolha, motivo)
    db.close()


# ---------------------------------------------------------------- gasto antes do POST
def test_gasto_barrado_nao_faz_post_nem_linha() -> None:
    def barrado(p: PedidoDeDecisao) -> None:
        raise AIError("fatia", kind="budget", motivo="fatia_jev")

    servidor = Servidor(_ok())
    decisor, linhas = _decisor(servidor, gasto=barrado)
    with pytest.raises(FalhaDeDecisao) as e:
        decisor.decidir(_pedido(), 5.0)
    assert e.value.motivo == "orcamento" and servidor.corpos == [] and linhas == []


@pytest.mark.parametrize("gasto", [None, "quebrado"])
def test_sem_conferencia_de_gasto_ou_com_ela_quebrada_nada_sai(gasto: Any) -> None:
    def quebrado(p: PedidoDeDecisao) -> None:
        raise RuntimeError("banco fora")

    servidor = Servidor(_ok())
    decisor, linhas = _decisor(servidor, gasto=quebrado if gasto == "quebrado" else None)
    with pytest.raises(FalhaDeDecisao) as e:
        decisor.decidir(_pedido(), 5.0)
    assert e.value.motivo == "orcamento" and servidor.corpos == [] and linhas == []


def test_sem_prazo_depois_da_conferencia_nao_ha_post() -> None:
    servidor = Servidor(_ok())
    decisor, linhas = _decisor(servidor)
    with pytest.raises(FalhaDeDecisao) as e:
        decisor.decidir(_pedido(), 0.0)
    assert e.value.motivo == "rede" and servidor.corpos == [] and linhas == []


# ---------------------------------------------------------------- falha do transporte
@pytest.mark.parametrize(("status", "corpo", "motivo"), [
    (401, {}, "401"), (403, {}, "401"), (422, {}, "422"), (429, {}, "429"), (503, {}, "529"), (529, {}, "529"),
    (500, {}, "rede"), (200, "nao-e-json", "parse"), (200, {"sem": "answers"}, "parse"),
])
def test_erro_do_transporte_vira_motivo_fechado_e_linha_ok_0(status: int, corpo: Any, motivo: str) -> None:
    servidor = Servidor(corpo, status=status)
    decisor, linhas = _decisor(servidor)
    with pytest.raises(FalhaDeDecisao) as e:
        decisor.decidir(_pedido(), 5.0)
    assert e.value.motivo == motivo and len(servidor.corpos) == 1
    assert e.value.__context__ is None and e.value.__cause__ is None       # nada do transporte pendurado na falha
    [linha] = linhas
    assert (linha.ok, linha.motivo, linha.tokens_entrada, linha.usd) == (False, motivo, 0, 0.0)
    assert CHAVE_FALSA not in str(e.value)


def test_timeout_do_transporte_e_rede() -> None:
    servidor = Servidor(erro=httpx.ReadTimeout("lento"))
    decisor, linhas = _decisor(servidor)
    with pytest.raises(FalhaDeDecisao) as e:
        decisor.decidir(_pedido(), 5.0)
    assert e.value.motivo == "rede" and [l.motivo for l in linhas] == ["rede"]


def test_sem_chave_nada_sai_e_nao_e_chamada() -> None:
    servidor = Servidor(_ok())
    decisor, linhas = _decisor(servidor, env={})
    with pytest.raises(FalhaDeDecisao) as e:
        decisor.decidir(_pedido(), 5.0)
    assert e.value.motivo == "desligado" and servidor.corpos == [] and linhas == []


def test_registro_quebrado_nao_derruba_a_decisao() -> None:
    def quebra(c: ChamadaAoJev) -> None:
        raise RuntimeError("banco fora")

    decisor = DecisorJev(_jev(Servidor(_ok())), conferir_gasto=lambda p: None, registrar=quebra)
    assert decisor.decidir(_pedido(), 5.0).respostas["q1"].escolha == "opt:a"


# ---------------------------------------------------------------- a linha em ai_calls e as réguas
def test_linha_em_ai_calls_e_o_que_a_fatia_e_o_livro_caixa_somam(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    repo = RepositorioDeSombra(db, relogio=lambda: MEIO_DIA)
    repo.registrar_chamada(ChamadaAoJev("jev-1.13.0", "intencao", "run-9", "st-9", "run-9", 1000, 3, USD_POR_CHAMADA,
                                        412.4, True, None))
    repo.registrar_chamada(ChamadaAoJev("jev-1.13.0", "curador", None, None, "dossie-1", 0, 0, 0.0, 30.0, False, "429"))
    linhas = [dict(r) for r in db.query("SELECT * FROM ai_calls ORDER BY id")]
    assert [(l["provider"], l["origem"], l["role"], l["model"]) for l in linhas] == [
        ("jev", "decisao_fechada", "decisao_fechada", "jev-1.13.0")] * 2
    ok, falha = linhas
    assert (ok["run_id"], ok["step_id"], ok["ref"], ok["ok"], ok["input_tokens"], ok["ms"]) == (
        "run-9", None, "intencao:run-9", 1, 1000, 412)
    assert ok["usd"] == pytest.approx(USD_POR_CHAMADA) and ok["error_kind"] is None
    assert (falha["ok"], falha["error_kind"], falha["error_status"], falha["ref"], falha["run_id"]) == (
        0, "429", 429, "curador:dossie-1", None)
    precos = {"jev-1.13.0": [0.042, 0.0, 0.0, 0.0]}
    assert costs.spent_usd(db, precos, since=INICIO_DO_DIA, origem="decisao_fechada") == pytest.approx(USD_POR_CHAMADA)
    db.close()


def test_a_fatia_do_jev_enxerga_o_gasto_e_barra_antes_do_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ponta a ponta no hub: duas chamadas cabem na fatia; a terceira para na conferência, sem POST e sem linha."""
    monkeypatch.setattr(costs, "day_start_iso", lambda: INICIO_DO_DIA)
    db = _banco(tmp_path)
    hub, _ = _hub(tmp_path, db, limites={"jev_max_usd_per_day": 1.5 * USD_POR_CHAMADA})
    servidor = Servidor(_ok())
    decisor = DecisorJev(
        _jev(servidor), registrar=RepositorioDeSombra(db, relogio=lambda: MEIO_DIA).registrar_chamada,
        conferir_gasto=lambda p: hub.conferir_gasto(run_id=p.run_id, origem="decisao_fechada", conta="typesafe"))
    for _ in range(2):
        decisor.decidir(_pedido(run_id=None), 5.0)
    with pytest.raises(FalhaDeDecisao) as e:
        decisor.decidir(_pedido(run_id=None), 5.0)
    assert e.value.motivo == "orcamento" and len(servidor.corpos) == 2
    assert db.scalar("SELECT COUNT(*) FROM ai_calls WHERE origem='decisao_fechada'") == 2
    db.close()


def test_saldo_bloqueado_da_conta_barra_a_conferencia(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    hub, _ = _hub(tmp_path, db)
    hub.conferir_gasto(run_id=None, origem="decisao_fechada", conta="typesafe")       # sem âncora: não bloqueia
    saldos.ajustar_regra(db, "typesafe", block_below=1.0)
    saldos.registrar_leitura(db, "typesafe", 0.5, source="manual")
    with pytest.raises(AIError) as e:
        hub.conferir_gasto(run_id=None, origem="decisao_fechada", conta="typesafe")
    assert e.value.kind == "balance"
    db.close()


def test_hub_sem_repositorio_nao_confere_e_barra(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    hub, _ = _hub(tmp_path, db)
    hub.repo = None
    with pytest.raises(AIError) as e:
        hub.conferir_gasto(run_id=None, origem="decisao_fechada", conta="typesafe")
    assert e.value.kind == "not_configured"
    db.close()


# ---------------------------------------------------------------- existir não é enviar
def test_com_o_envio_fechado_no_codigo_a_porta_recusa_antes_do_decisor_real(monkeypatch: pytest.MonkeyPatch,
                                                                             tmp_path: Path) -> None:
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", False)
    db = _banco(tmp_path)
    servidor = Servidor(_ok())
    decisor, linhas = _decisor(servidor)
    cfg = DecisaoFechadaCfg(enabled=True, consumidores={"curador": "on"})  # type: ignore[arg-type]
    porta = Porta(decisor, cfg=cfg, observador=observador_de_sombra(RepositorioDeSombra(db)))
    res = porta.consultar(_pedido(modo="on"))
    assert res.fallback_reason == "privacidade" and servidor.corpos == [] and linhas == []
    db.close()


def test_porta_aberta_reconfere_o_que_o_decisor_real_devolve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)
    db = _banco(tmp_path)
    cfg = DecisaoFechadaCfg(enabled=True, consumidores={"curador": "on"})  # type: ignore[arg-type]
    # 31.19: o limiar mede a probabilidade devolvida da escolha, não a `confidence` do fio
    real_de_0310 = {"opt:a": 0.6, "opt:b": 0.35, "opt:nenhuma": 0.05}          # confiança 0,50, maior prob. 0,60
    for resposta, motivo in ((_ok("opt:zzz"), "unknown_choice"), (_ok(confianca=0.5), "abaixo_do_limiar"),
                             (_ok(), None), (_ok(confianca=0.5, probabilities=real_de_0310), "abaixo_do_limiar"),
                             (_ok(confianca=0.5, probabilities={"opt:a": 0.9, "opt:b": 0.1}), None),
                             (_ok(probabilities={"opt:a": 0.05, "opt:b": 0.95}), "abaixo_do_limiar")):
        decisor, _ = _decisor(Servidor(resposta))
        res = Porta(decisor, cfg=cfg, observador=observador_de_sombra(RepositorioDeSombra(db))).consultar(
            _pedido(modo="on"))
        assert res.respostas["q1"].fallback_reason == motivo
    db.close()


async def test_composicao_liga_o_real_so_com_decisor_jev(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    assert st.cfg.file.ai.decisao_fechada.decisor == "nulo" and isinstance(st.decisao_fechada.decisor, DecisorNulo)
    assert st._decisor_da_porta(st.cfg) is None                                         # noqa: SLF001
    monkeypatch.setattr(st.cfg.file.ai.decisao_fechada, "decisor", "jev")
    # provedor sem `conferir_gasto` (o dublê do harness não roteia): o real nasce SEM conferência, isto é, nunca envia
    sem_hub = st._decisor_da_porta(st.cfg)                                               # noqa: SLF001
    assert isinstance(sem_hub, DecisorJev) and sem_hub._conferir_gasto is None           # noqa: SLF001
    # com o hub (o `RoutingProvider` real tem o método): a conferência chega a ele com a origem da fatia e a conta
    chamadas: list[dict[str, object]] = []
    monkeypatch.setattr(st.provider, "conferir_gasto", lambda **kw: chamadas.append(kw), raising=False)
    real = st._decisor_da_porta(st.cfg)                                                  # noqa: SLF001
    assert isinstance(real, DecisorJev) and "jev-1.13.0" in repr(real)
    assert real._conferir_gasto is not None                                              # noqa: SLF001
    real._conferir_gasto(_pedido(run_id="run-7"))                                        # noqa: SLF001
    assert chamadas == [{"run_id": "run-7", "origem": "decisao_fechada", "conta": "typesafe"}]


def test_transparencia_diz_qual_decisor_esta_montado() -> None:
    for decisor in ("nulo", "jev"):
        cfg = DecisaoFechadaCfg(enabled=True, consumidores={"curador": "shadow"}, decisor=decisor)  # type: ignore[arg-type]
        bloco = transparencia.status(cfg, chave_configurada=False)
        assert bloco is not None and bloco["decider"] == decisor and bloco["send_approved"] is True
        assert bloco["sending"] is False                                  # sem a chave, nada sai com decisor nenhum
        assert f"Decisor na porta: {decisor}." in str(transparencia.aviso(cfg, chave_configurada=True))
