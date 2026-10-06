"""31.17: o que o `DecisorJev` põe no fio, byte a byte (a fronteira da medição da rodada D do 31.9 vira prova).

Com o envio aberto no código (31.17), o corpo que chega à TypeSafe é só `{state, model, questions}`:

- **Intenção** (C3, em sombra; `off` no deploy 9): `state` = o comando já sem destinos, redigido e filtrado, mais o app;
  `questions` = as perguntas com as opções (ids opacos e descrições do catálogo do dono, C2). NUNCA o `run_id`, o `ref`, o
  comando original, os nomes do catálogo de destinos (personas, handles, aparelhos) nem o id cru da habilidade.
- **Curador** (C0, o que o deploy 9 liga, com a config dele: `curador: shadow`, `intencao: off`, C0–C1): `state` = só os
  `CAMPOS` (rótulos e contagens); nunca o conteúdo, o app, a capability, ids, o `dossie_hash` (o `ref`) nem datas.

Cada teste afirma primeiro que UM corpo saiu: sem isso, toda ausência passaria com a lista vazia. Os ids que não saem
existem do lado de cá (a linha de `ai_calls` os leva), e isso também é conferido.

Prova `simulated`: transporte `httpx.MockTransport` (o fixture derruba qualquer socket), chave falsa, banco de teste.
Chamada real ao Jev: `not_run` (fica para o deploy 9, no 31.10).
"""
from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import DecisaoFechadaCfg
from app.db import Database
from app.modules.context_retrieval.adapters.jev import JevSemanticProvider
from app.planning.decisao_fechada import privacidade
from app.planning.decisao_fechada.contrato import ID_NENHUMA
from app.planning.decisao_fechada.curador import (
    CAMPOS, OPCOES, PERGUNTA_TRIAGEM, CuradorComTriagemEmSombra, TriagemDoCurador, estado_do_dossie,
)
from app.planning.decisao_fechada.decisores import ChamadaAoJev, DecisorJev
from app.planning.decisao_fechada.intencao import (
    PERGUNTA_CATALOGO, PERGUNTA_DESEMPATE, CadeiaObservada, ConsumidorDeIntencao, EntradaDeCatalogo, id_opaco,
)
from app.planning.decisao_fechada.porta import Porta
from app.planning.decisao_fechada.sombra import RepositorioDeSombra, observador_de_sombra

from .conftest import _dsn_de_teste
from .test_decisao_fechada_curador import HASH, TEXTO_DA_PESSOA, CuradorFalso, Pedido, _dossie

CHAVE_FALSA = "chave-falsa-" + "z" * 16  # só para provar que não vaza; não é credencial
USO = {"input_tokens": 700, "output_tokens": 2}
#: Nenhuma destas chaves pode aparecer em nível nenhum do corpo: são do lado de cá (sombra, `ai_calls`, conferência C7).
#: `origem` fica de fora de propósito: no curador é um dos `CAMPOS` (a origem do ITEM, não o consumidor).
CHAVES_LOCAIS = {"run_id", "ref", "step_id", "original", "destinos", "marcadores", "classe", "modo",
                 "motivo_privacidade", "dossie_hash"}

RUN_ID = "run-payload-3117"
PERSONA, HANDLE = "Marina Linhares", "@marina.linhares"
COMANDO = "curta as 3 ultimas fotos no instagram"                              # o que `sem_destinos` devolve
ORIGINAL = f"curta as 3 ultimas fotos no instagram com a persona {PERSONA} ({HANDLE})"
DESTINOS = (PERSONA, HANDLE, "Quillon Lima", "@quillon.lima", "android-03")         # o catálogo de destinos real
CATALOGO = (EntradaDeCatalogo("ig.curtir_fotos", "Curtir fotos", "Curte as ultimas fotos do perfil"),
            EntradaDeCatalogo("ig.abrir_conversa", "Abrir conversa", "Abre a conversa com uma pessoa"))


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Qualquer conexão de socket aqui é bug: o Jev só fala com o MockTransport."""
    tentativas: list[str] = []

    def proibido(*a: Any, **k: Any) -> None:
        tentativas.append("connect")
        raise AssertionError("REAL_JEV_NETWORK_CALLS: conexao de rede proibida nos testes")

    monkeypatch.setattr(socket.socket, "connect", proibido)
    monkeypatch.setattr(socket, "create_connection", proibido)
    yield
    assert tentativas == [], "houve tentativa de conexao de rede"


@pytest.fixture(autouse=True)
def envio_aberto(monkeypatch: pytest.MonkeyPatch) -> None:
    """O de fábrica desde o 31.17; fixado aqui para o teste não depender de quem mexer no interruptor."""
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)


class Servidor:
    """O lado da TypeSafe, falso: guarda os BYTES de cada corpo (a prova é sobre o que saiu, não sobre o que se montou)."""

    def __init__(self, respostas: dict[str, Any]) -> None:
        self.brutos: list[bytes] = []
        self.respostas = respostas

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.brutos.append(request.content)
        return httpx.Response(200, json={"model": "jev-1.13.0", "answers": self.respostas, "usage": USO})


def _choice(escolha: str) -> dict[str, Any]:
    return {"type": "choice", "choice": escolha, "confidence": 0.9, "probabilities": {escolha: 0.9}}


def _porta(tmp: Path, servidor: Servidor, cfg: DecisaoFechadaCfg
           ) -> tuple[Porta, RepositorioDeSombra, list[ChamadaAoJev], Database]:
    db = Database(_dsn_de_teste() or tmp / "payload.sqlite3")
    db.migrate()
    repo = RepositorioDeSombra(db)
    linhas: list[ChamadaAoJev] = []
    decisor = DecisorJev(JevSemanticProvider(env={"TYPESAFE_API_KEY": CHAVE_FALSA},
                                             transport=httpx.MockTransport(servidor)),
                         conferir_gasto=lambda pedido: None, registrar=linhas.append)
    return Porta(decisor, cfg=cfg, observador=observador_de_sombra(repo)), repo, linhas, db


def _chaves(valor: object) -> set[str]:
    """Todas as chaves de dicionário do corpo, em qualquer nível."""
    if isinstance(valor, dict):
        return set(valor) | {k for v in valor.values() for k in _chaves(v)}
    if isinstance(valor, list):
        return {k for v in valor for k in _chaves(v)}
    return set()


def _um_corpo(servidor: Servidor) -> tuple[str, dict[str, Any]]:
    assert len(servidor.brutos) == 1, "o decisor real não postou: as ausências abaixo não provariam nada"
    texto = servidor.brutos[0].decode("utf-8")
    return texto, json.loads(texto)


def test_intencao_manda_so_o_comando_redigido_o_app_e_as_opcoes(tmp_path: Path) -> None:
    servidor = Servidor({PERGUNTA_CATALOGO: _choice(id_opaco("ig.curtir_fotos"))})
    porta, repo, linhas, db = _porta(tmp_path, servidor,
                                     DecisaoFechadaCfg(enabled=True, consumidores={"intencao": "shadow"}, decisor="jev"))
    consumidor = ConsumidorDeIntencao(porta, repo)
    assert consumidor.ativo()
    consumidor.observar(run_id=RUN_ID, comando=COMANDO, app="instagram", catalogo=CATALOGO,
                        cadeia=CadeiaObservada(empatados=("ig.curtir_fotos", "ig.abrir_conversa")),
                        original=ORIGINAL, destinos=DESTINOS)
    porta.aguardar_sombras()
    texto, corpo = _um_corpo(servidor)

    assert set(corpo) == {"state", "model", "questions"}
    assert corpo["state"] == {"comando": "curta as [numero] ultimas fotos no instagram", "app": "instagram"}
    assert set(corpo["questions"]) == {PERGUNTA_CATALOGO, PERGUNTA_DESEMPATE}
    opcoes = {id_opaco(e.skill_id) for e in CATALOGO} | {ID_NENHUMA}
    for pergunta in corpo["questions"].values():
        assert set(pergunta) == {"type", "instructions", "criteria"} and pergunta["type"] == "choice"
        assert set(pergunta["criteria"]) == opcoes                         # ids opacos + a `nenhuma`
    assert not _chaves(corpo) & CHAVES_LOCAIS
    for proibido in (RUN_ID, ORIGINAL, "persona", "Marina", "Linhares", "marina.linhares", "Quillon", "quillon.lima",
                     "android-03", "3 ultimas", "ig.curtir_fotos", "ig.abrir_conversa", CHAVE_FALSA):
        assert proibido not in texto, proibido
    # do lado de cá os ids existem: a linha de `ai_calls` e a da sombra os levam
    assert [(c.origem, c.run_id, c.ref, c.ok) for c in linhas] == [("intencao", RUN_ID, RUN_ID, True)]
    assert {r["ref"] for r in db.query("SELECT ref FROM decisao_fechada_sombra")} == {RUN_ID}
    db.close()


def test_curador_com_a_config_do_deploy_9_manda_so_rotulos_e_contagens(tmp_path: Path) -> None:
    servidor = Servidor({PERGUNTA_TRIAGEM: _choice("opt:manter")})
    cfg = DecisaoFechadaCfg(enabled=True, consumidores={"curador": "shadow", "intencao": "off"}, decisor="jev",
                            classes_permitidas=["C0", "C1"])
    porta, repo, linhas, db = _porta(tmp_path, servidor, cfg)
    assert not ConsumidorDeIntencao(porta, repo).ativo()                      # a C3 não sai com esta config
    triagem = TriagemDoCurador(porta)
    assert triagem.ativo()
    CuradorComTriagemEmSombra(CuradorFalso(), triagem).revisar(Pedido(_dossie()))
    porta.aguardar_sombras()
    texto, corpo = _um_corpo(servidor)

    assert set(corpo) == {"state", "model", "questions"}
    assert corpo["state"] == estado_do_dossie(_dossie()) and set(corpo["state"]) <= CAMPOS
    assert all(isinstance(v, str) for v in corpo["state"].values())
    [(pid, pergunta)] = corpo["questions"].items()
    assert pid == PERGUNTA_TRIAGEM and set(pergunta) == {"type", "instructions", "criteria"}
    assert set(pergunta["criteria"]) == set(OPCOES) | {ID_NENHUMA}
    assert not _chaves(corpo) & CHAVES_LOCAIS
    for proibido in (TEXTO_DA_PESSOA, HASH, "com.instagram.android", "OPEN_POST", "item:licao", "ev:1", "run:r1",
                     "android-01", "2026-10-02", "alvo_ausente", "texto_de_pessoa", "447", CHAVE_FALSA):
        assert proibido not in texto, proibido
    assert [(c.origem, c.run_id, c.ref, c.ok) for c in linhas] == [("curador", None, HASH, True)]
    db.close()
