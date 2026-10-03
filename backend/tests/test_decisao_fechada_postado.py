"""31.21 (migração 083): a linha da sombra diz se a chamada ao Jev chegou ao POST (`postado`) e qual linha de `ai_calls` ela
gerou (`ai_call_id`).

Até a 083, `fallback_reason = 'rede'` juntava o prazo esgotado antes do POST, a exceção inesperada, o POST cuja linha de gasto
não gravou e o POST respondido acima do prazo da sombra; a leitura só podia cruzar sombra e `ai_calls` por contagem. A
tabela-verdade que estes testes fixam, caso a caso:

| onde                                                              | postado       | ai_call_id      |
|-------------------------------------------------------------------|---------------|-----------------|
| porta: recusa de privacidade                                      | 0             | NULO            |
| `DecisorNulo`                                                     | 0             | NULO            |
| `DecisorJev`: só noul/score, orçamento, prazo esgotado, sem chave | 0             | NULO            |
| `DecisorJev`: erro do transporte (linha `ok=0`)                   | 1             | o id da linha   |
| `DecisorJev`: resposta (linha `ok=1`)                             | 1             | o id da linha   |
| a linha de gasto não gravou ("régua cega")                        | 1             | NULO            |
| sombra acima do prazo (`rede` depois do POST)                     | o do decisor  | o do decisor    |
| exceção inesperada; prazo do `on` com o futuro em voo             | NULO          | NULO            |

Prova `simulated`: o Jev só fala com `httpx.MockTransport` (o `sem_rede` do 31.14 derruba qualquer socket) e o banco é o de
teste (PostgreSQL pela fábrica quando `TEST_DATABASE_URL` existe). Nada aqui prova o serviço real.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config import DecisaoFechadaCfg
from app.planning.decisao_fechada import porta as porta_mod
from app.planning.decisao_fechada import privacidade
from app.planning.decisao_fechada.contrato import (
    FalhaDeDecisao, PedidoDeDecisao, Pergunta, RespostaDeDecisao, pergunta_choice,
)
from app.planning.decisao_fechada.decisores import ChamadaAoJev, DecisorFalso, DecisorJev, DecisorNulo
from app.planning.decisao_fechada.porta import Porta
from app.planning.decisao_fechada.sombra import RepositorioDeSombra, observador_de_sombra
from app.planning.provider import AIError

from .test_decisao_fechada_jev import OPCOES, Servidor, _banco, _jev, _ok, _pedido, sem_rede  # noqa: F401 (fixture)


def _decisor(servidor: Servidor, *, registrar: Any = None, gasto: Any = lambda p: None,
             env: dict[str, str] | None = None) -> tuple[DecisorJev, list[ChamadaAoJev]]:
    """O real, com um registrador que devolve ids como o banco devolveria (101, 102, ...)."""
    anotadas: list[ChamadaAoJev] = []

    def _registrar(c: ChamadaAoJev) -> int:
        anotadas.append(c)
        return 100 + len(anotadas)

    return DecisorJev(_jev(servidor, env=env), conferir_gasto=gasto, registrar=registrar or _registrar), anotadas


def _marca(fn: Any) -> tuple[bool | None, int | None, str | None]:
    """(postado, ai_call_id, motivo) do resultado ou da falha: o decisor real devolve um ou levanta a outra."""
    try:
        r = fn()
    except FalhaDeDecisao as e:
        return e.postado, e.ai_call_id, e.motivo
    return r.postado, r.ai_call_id, r.fallback_reason


def _linhas(db: Any) -> list[tuple[Any, ...]]:
    return [(r["pergunta_id"], r["fallback_reason"], r["postado"], r["ai_call_id"])
            for r in db.query("SELECT * FROM decisao_fechada_sombra ORDER BY id")]


# ---------------------------------------------------------------- o decisor real
def test_resposta_e_erro_do_transporte_levam_postado_e_o_id_da_linha_de_gasto() -> None:
    decisor, anotadas = _decisor(Servidor(_ok()))
    assert _marca(lambda: decisor.decidir(_pedido(), 5.0)) == (True, 101, None)
    decisor, anotadas = _decisor(Servidor({}, status=429))
    with pytest.raises(FalhaDeDecisao) as e:
        decisor.decidir(_pedido(), 5.0)
    assert (e.value.motivo, e.value.postado, e.value.ai_call_id) == ("429", True, 101)
    assert e.value.__context__ is None and e.value.__cause__ is None      # a marca não pendura o erro do transporte
    assert [c.ok for c in anotadas] == [False]


def _barrado(p: PedidoDeDecisao) -> None:
    raise AIError("fatia", kind="budget", motivo="fatia_jev")


@pytest.mark.parametrize(("caso", "motivo"), [
    ("so_score", "desligado"), ("orcamento", "orcamento"), ("prazo_esgotado", "rede"), ("sem_chave", "desligado"),
])
def test_o_que_para_antes_do_post_e_postado_0_sem_linha_de_gasto(caso: str, motivo: str) -> None:
    servidor = Servidor(_ok())
    decisor, anotadas = _decisor(servidor, gasto=_barrado if caso == "orcamento" else (lambda p: None),
                                 env={} if caso == "sem_chave" else None)
    # o `noul` vai ao fio desde o 31.13 (R5); o `score` segue sem consumidor e para antes do POST
    pedido = _pedido(Pergunta("q2", "score", "How much?")) if caso == "so_score" else _pedido()
    prazo = 0.0 if caso == "prazo_esgotado" else 5.0
    assert _marca(lambda: decisor.decidir(pedido, prazo)) == (False, None, motivo)
    assert servidor.corpos == [] and anotadas == []
    assert _marca(lambda: DecisorNulo().decidir(_pedido(), 5.0)) == (False, None, "desligado")


@pytest.mark.parametrize("devolve", ["quebra", None, 0, -3, True, "7"])
def test_linha_de_gasto_que_nao_gravou_e_postado_sem_id(devolve: Any) -> None:
    """A régua cega: o POST aconteceu (postado 1), mas não há id de linha para ligar. Id só inteiro positivo."""
    def registrar(c: ChamadaAoJev) -> Any:
        if devolve == "quebra":
            raise RuntimeError("banco fora")
        return devolve

    decisor, _ = _decisor(Servidor(_ok()), registrar=registrar)
    assert _marca(lambda: decisor.decidir(_pedido(), 5.0)) == (True, None, None)
    decisor, _ = _decisor(Servidor({}, status=503), registrar=registrar)
    assert _marca(lambda: decisor.decidir(_pedido(), 5.0)) == (True, None, "529")


# ---------------------------------------------------------------- ponta a ponta: porta → sombra → ai_calls
@pytest.fixture
def envio_aberto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)


def _porta(decisor: Any, db: Any, modo: str = "shadow") -> Porta:
    cfg = DecisaoFechadaCfg(enabled=True, consumidores={"curador": modo})  # type: ignore[arg-type]
    return Porta(decisor, cfg=cfg, observador=observador_de_sombra(RepositorioDeSombra(db)))


def _duas_perguntas(**kw: Any) -> PedidoDeDecisao:
    return _pedido(pergunta_choice("q1", "Pick one.", OPCOES, limiar=0.8),
                   pergunta_choice("q2", "Pick another.", OPCOES, limiar=0.8), **kw)


def test_a_linha_da_sombra_aponta_a_linha_de_ai_calls_da_mesma_chamada(tmp_path: Path, envio_aberto: None) -> None:
    db = _banco(tmp_path)
    repo = RepositorioDeSombra(db)
    for servidor in (Servidor(_ok()), Servidor({}, status=429)):
        decisor = DecisorJev(_jev(servidor), conferir_gasto=lambda p: None, registrar=repo.registrar_chamada)
        porta = _porta(decisor, db)
        porta.consultar(_duas_perguntas())
        porta.aguardar_sombras()
    ids = [r["id"] for r in db.query("SELECT id FROM ai_calls WHERE provider='jev' ORDER BY id")]
    assert len(ids) == 2
    # as duas perguntas da chamada levam a MESMA marca (como `ms`; o `usd` fica só na primeira)
    assert _linhas(db) == [("q1", None, 1, ids[0]), ("q2", "parse", 1, ids[0]),
                           ("q1", "429", 1, ids[1]), ("q2", "429", 1, ids[1])]
    db.close()


def test_orcamento_e_privacidade_ficam_postado_0_sem_linha_de_gasto(tmp_path: Path, envio_aberto: None,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    db = _banco(tmp_path)
    repo = RepositorioDeSombra(db)
    decisor = DecisorJev(_jev(Servidor(_ok())), conferir_gasto=_barrado, registrar=repo.registrar_chamada)
    porta = _porta(decisor, db)
    porta.consultar(_pedido())
    porta.aguardar_sombras()
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", False)     # a porta recusa antes do decisor
    porta.consultar(_pedido())
    porta.aguardar_sombras()
    assert _linhas(db) == [("q1", "orcamento", 0, None), ("q1", "privacidade", 0, None)]
    assert db.scalar("SELECT COUNT(*) FROM ai_calls WHERE provider='jev'") == 0
    db.close()


def test_rede_acima_do_prazo_da_sombra_guarda_a_marca_do_decisor(tmp_path: Path, envio_aberto: None,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """O POST respondeu, mas tarde: a sombra grava `rede` e, desde a 083, também que houve POST e qual linha de gasto."""
    monkeypatch.setattr(porta_mod, "TIMEOUT_SHADOW_S", 0.01)
    db = _banco(tmp_path)
    lento = DecisorFalso({"q1": RespostaDeDecisao(escolha="opt:a", probabilidades={"opt:a": 0.9}, confianca=0.9)},
                         atraso_s=0.05, postado=True, ai_call_id=42)
    porta = _porta(lento, db)
    porta.consultar(_pedido())
    porta.aguardar_sombras()
    assert _linhas(db) == [("q1", "rede", 1, 42)]
    db.close()


@pytest.mark.parametrize(("falha", "esperado"), [
    (FalhaDeDecisao("529", postado=True, ai_call_id=9), ("529", 1, 9)),
    (FalhaDeDecisao("rede", postado=False), ("rede", 0, None)),
    (RuntimeError("inesperada"), ("rede", None, None)),                    # não se sabe se saiu
])
def test_a_porta_repassa_a_marca_da_falha_e_a_inesperada_fica_nula(tmp_path: Path, envio_aberto: None,
                                                                   falha: Exception, esperado: tuple[Any, ...]) -> None:
    db = _banco(tmp_path)
    porta = _porta(DecisorFalso(falha=falha), db)                           # type: ignore[arg-type]
    porta.consultar(_pedido())
    porta.aguardar_sombras()
    assert _linhas(db) == [("q1", *esperado)]
    db.close()


def test_no_on_o_futuro_em_voo_fica_nulo(tmp_path: Path, envio_aberto: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """No `on`, o prazo estoura com o decisor ainda rodando: ele pode estar antes ou depois do POST."""
    monkeypatch.setattr(porta_mod, "TIMEOUT_ON_S", 0.01)
    db = _banco(tmp_path)
    porta = _porta(DecisorFalso(atraso_s=0.2, postado=True, ai_call_id=5), db, modo="on")
    res = porta.consultar(_pedido(modo="on"))
    assert (res.fallback_reason, res.postado, res.ai_call_id) == ("rede", None, None)
    assert _linhas(db) == [("q1", "rede", None, None)]
    db.close()


def test_resposta_valida_na_sombra_leva_a_marca_do_decisor(tmp_path: Path, envio_aberto: None) -> None:
    db = _banco(tmp_path)
    falso = DecisorFalso({"q1": RespostaDeDecisao(escolha="opt:a", probabilidades={"opt:a": 0.9, "opt:b": 0.1},
                                                  confianca=0.9)}, postado=True, ai_call_id=77)
    porta = _porta(falso, db)
    porta.consultar(_pedido())
    porta.aguardar_sombras()
    assert _linhas(db) == [("q1", None, 1, 77)]
    db.close()
