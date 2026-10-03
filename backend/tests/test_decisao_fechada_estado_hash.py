"""31.22 (migração 086): a linha da sombra guarda o sha256 do estado REDIGIDO que foi ao decisor (`estado_hash`).

É o que o lote offline do 31.11 compara para reenviar só o comando que já saiu IGUAL pela sombra da intenção (ADR-069
item 21; "exposição nova zero", salvaguarda pedida pela orquestradora em 03/10). Fixa-se aqui:

- o hash é do estado DEPOIS do `privacidade.redigir` (o que sai), não do estado cru;
- vai em todas as linhas da chamada, na sombra e no `on`;
- a recusa de privacidade não tem hash (nada foi redigido para sair), e o legado fica NULO;
- o JSON é canônico (a ordem das chaves não muda o hash) e a sombra só grava o formato do sha256.

Prova `simulated`: `DecisorFalso` (sem rede) e o banco de teste (PostgreSQL pela fábrica quando `TEST_DATABASE_URL` existe).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config import DecisaoFechadaCfg
from app.planning.decisao_fechada import privacidade
from app.planning.decisao_fechada.contrato import (PedidoDeDecisao, RespostaDeDecisao, ResultadoDeDecisao,
                                                   pergunta_choice)
from app.planning.decisao_fechada.decisores import DecisorFalso
from app.planning.decisao_fechada.porta import Porta, RegistroDeDecisao, hash_do_estado
from app.planning.decisao_fechada.sombra import RepositorioDeSombra, observador_de_sombra

from .test_decisao_fechada_jev import OPCOES, _banco, sem_rede  # noqa: F401 (fixture)

SEGREDO = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdef"


@pytest.fixture(autouse=True)
def _envio_aberto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)


def _intencao(comando: str, **kw: Any) -> PedidoDeDecisao:
    base: dict[str, Any] = dict(
        origem="intencao", classe="C3", estado={"comando": comando, "app": "qa.messenger"}, modo="shadow",
        run_id="run-1", ref="run-1",
        perguntas=(pergunta_choice("intencao_catalogo", "Pick one.", OPCOES),
                   pergunta_choice("intencao_desempate", "Pick one.", OPCOES)))
    base.update(kw)
    return PedidoDeDecisao(**base)


def _porta(db: Any, modo: str = "shadow") -> Porta:
    respostas = {pid: RespostaDeDecisao(escolha="opt:a", probabilidades={"opt:a": 0.95, "opt:b": 0.05}, confianca=0.95)
                 for pid in ("intencao_catalogo", "intencao_desempate")}
    return Porta(DecisorFalso(respostas), cfg=DecisaoFechadaCfg(enabled=True, consumidores={"intencao": modo}),
                 observador=observador_de_sombra(RepositorioDeSombra(db)))


def _hashes(db: Any) -> list[Any]:
    return [r["estado_hash"] for r in db.query("SELECT estado_hash FROM decisao_fechada_sombra ORDER BY id")]


# ------------------------------------------------------------------ o hash
def test_hash_canonico_e_formato() -> None:
    a = hash_do_estado({"comando": "abrir o feed", "app": "qa.messenger"})
    assert a == hash_do_estado({"app": "qa.messenger", "comando": "abrir o feed"})      # a ordem das chaves não conta
    assert a != hash_do_estado({"comando": "abrir o feed ", "app": "qa.messenger"})
    assert len(a) == 64 and all(c in "0123456789abcdef" for c in a)


# ------------------------------------------------------------------ na sombra e no `on`
def test_sombra_grava_o_hash_do_estado_redigido_em_todas_as_linhas(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    pedido = _intencao(f"abrir o feed com a chave {SEGREDO}")
    porta = _porta(db)
    porta.consultar(pedido)
    porta.aguardar_sombras()
    redigido = privacidade.redigir(pedido).estado
    assert redigido["comando"] != pedido.estado["comando"]                  # a redação mudou o que sai
    esperado = hash_do_estado(redigido)
    assert _hashes(db) == [esperado, esperado]                              # as duas perguntas da MESMA chamada
    assert esperado != hash_do_estado(pedido.estado)                        # nunca o do estado cru


def test_on_tambem_grava_o_hash(tmp_path: Path) -> None:
    """No `on` (só fora da C3: a C3 é só sombra, e a recusa de privacidade não tem hash, como no teste seguinte)."""
    db = _banco(tmp_path)
    pedido = PedidoDeDecisao(origem="curador", classe="C0", estado={"kind": "licao", "falhas": "2"}, modo="on",
                             ref="item-1", perguntas=(pergunta_choice("q1", "Pick one.", OPCOES),))
    porta = Porta(DecisorFalso({"q1": RespostaDeDecisao(escolha="opt:a", probabilidades={"opt:a": 0.95}, confianca=0.95)}),
                  cfg=DecisaoFechadaCfg(enabled=True, consumidores={"curador": "on"}),
                  observador=observador_de_sombra(RepositorioDeSombra(db)))
    porta.consultar(pedido)
    assert _hashes(db) == [hash_do_estado(privacidade.redigir(pedido).estado)]


def test_recusa_de_privacidade_nao_tem_hash(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    porta = _porta(db)
    porta.consultar(_intencao("entrar com a senha", marcadores=frozenset({"credencial"}),
                              motivo_privacidade="credencial"))
    porta.aguardar_sombras()
    linhas = db.query("SELECT fallback_reason, estado_hash FROM decisao_fechada_sombra")
    assert [(r["fallback_reason"], r["estado_hash"]) for r in linhas] == [("privacidade", None)] * 2


# ------------------------------------------------------------------ a sombra só grava o formato
@pytest.mark.parametrize("valor", [None, "xyz", "A" * 64, "a" * 63, "a" * 65, SEGREDO])
def test_sombra_so_grava_sha256(tmp_path: Path, valor: str | None) -> None:
    db = _banco(tmp_path)
    res = ResultadoDeDecisao({"q1": RespostaDeDecisao(escolha="opt:a", probabilidades={"opt:a": 0.9}, confianca=0.9)})
    RepositorioDeSombra(db).registrar(RegistroDeDecisao("curador", "C0", "shadow", None, None, "item-1", res, None,
                                                        valor))
    assert _hashes(db) == [None]


def test_registro_antigo_sem_hash_fica_nulo(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    res = ResultadoDeDecisao({"q1": RespostaDeDecisao(escolha="opt:a", probabilidades={"opt:a": 0.9}, confianca=0.9)})
    RepositorioDeSombra(db).registrar(RegistroDeDecisao("curador", "C0", "shadow", None, None, "item-1", res))
    bom = "b" * 64
    RepositorioDeSombra(db).registrar(RegistroDeDecisao("curador", "C0", "shadow", None, None, "item-2", res, None,
                                                        bom))
    assert _hashes(db) == [None, bom]
