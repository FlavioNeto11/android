"""A tentativa `interrupted` que parou para esperar a pessoa não é "interrompida" (item 29.74).

O grupo `fk-d0f1c2ed23` do relatório (camada `execucao`, ~77 ocorrências em 04/10) juntava tudo que o scheduler fecha
como `interrupted`: a reconciliação de partida, a pausa, a tomada de controle, mas também o `waiting_user` (o app
pede autenticação, a IA relatou o bloqueio, falta saldo) e a prova de fluxo que encerra sem esperar ninguém. O que
estes testes protegem:

1. os três caminhos: esperou a pessoa (`recovery` "Aguardando o usuário"), prova de fluxo encerrada pelo sistema, e
   pausa/tomada/reconciliação — só os dois primeiros são classificados pelo texto;
2. texto sem regra, de quem esperou a pessoa, é o relato livre da IA (`ia_declarou_bloqueio`), nunca `outro`;
3. a leitura retroativa relê o `interrompida` GRAVADO antes do 29.74 com o `recovery`, sem migração, e conta como
   retroativo; sem o retroativo, fica o gravado;
4. o scheduler escreve exatamente os `recovery` que o classificador reconhece.
Nível de prova: `simulated` (banco migrado em arquivo temporário, sem aparelho).
"""
from __future__ import annotations

import inspect
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.db import Database
from app.modules.learning.domain.aprendido import TentativaDaExecucao, _falhas
from app.modules.learning.domain.falhas import (ESPEROU_A_PESSOA, FailureKind, classificar_falha, esperou_a_pessoa,
                                                tipo_da_tentativa)
from app.taskqueue import scheduler

from .fake_skills import banco as banco_migrado
from .test_learning_backlog import Etapa, T, dias, grupo, montar, semear

F = FailureKind
ESPERA = "Aguardando o usuário"
PROVA = "Prova de fluxo: encerrada pelo sistema"
PAUSA = "Pausado pelo usuário num ponto seguro"
TOMADA = "Usuário assumiu o controle; a etapa será reobservada ao retomar"
RECONCILIACAO = "Reconciliar pelo estado real do aparelho"


def test_os_tres_caminhos_da_interrompida() -> None:
    autenticacao = "O app pede autenticação (senha)."
    # 1) esperou a pessoa: o texto decide
    assert classificar_falha(autenticacao, "interrupted", recovery=ESPERA) is F.AUTENTICACAO
    assert classificar_falha("x", "interrupted", "billing", recovery=ESPERA) is F.IA_SALDO   # o erro de IA vence
    # 2) a prova de fluxo encerrada pelo sistema: idem
    assert classificar_falha(autenticacao, "interrupted", recovery=PROVA) is F.AUTENTICACAO
    # 3) pausa, tomada e reconciliação (e o legado sem recovery): interrompida, qualquer que seja o texto
    for recovery in (PAUSA, TOMADA, RECONCILIACAO, None, ""):
        assert classificar_falha(autenticacao, "interrupted", recovery=recovery) is F.INTERROMPIDA, recovery
    # o recovery só pesa em `interrupted`: em `failed` o texto já decidia
    assert classificar_falha(autenticacao, "failed", recovery=PAUSA) is F.AUTENTICACAO
    # sem acento e sem caixa casa igual (o texto pode chegar normalizado)
    assert esperou_a_pessoa("aguardando o usuario") and not esperou_a_pessoa(PAUSA)


def test_texto_sem_regra_de_quem_esperou_a_pessoa_e_o_relato_da_ia() -> None:
    relato = "A tela de perfil só tem nome, e-mail e cidade; não existe campo de recado para preencher."
    assert classificar_falha(relato, "interrupted", recovery=ESPERA) is F.IA_DECLAROU_BLOQUEIO
    assert classificar_falha(None, "interrupted", recovery=PROVA) is F.IA_DECLAROU_BLOQUEIO
    # o texto antigo da reconciliação, se sobrou na tentativa, não devolve a interrompida
    assert classificar_falha("Tentativa interrompida: backend reiniciado", "interrupted",
                             recovery=ESPERA) is F.IA_DECLAROU_BLOQUEIO
    # o erro ANTERIOR da tentativa (navegação) não vira lição nem grupo de navegação de uma parada pela pessoa
    for anterior in ("Alvo ausente: o botão Seguir", "Pós-condição não comprovada: a conversa não abriu.",
                     "Defeito do plano: a etapa cita {{saida:x}}"):
        assert classificar_falha(anterior, "interrupted", recovery=ESPERA) is F.IA_DECLAROU_BLOQUEIO, anterior
        assert classificar_falha(anterior, "failed") is not F.IA_DECLAROU_BLOQUEIO, anterior   # fora da espera, vale
    # a parada da triagem de valor sensível (executor) pede a pessoa sem passar pela IA
    triagem = ("O valor 'codigo' lido na tela tem formato de código de verificação: código de verificação, senha e "
               "token não passam de uma etapa a outra (ADR-009, ADR-058). Nada foi gravado.")
    assert classificar_falha(triagem, "interrupted", recovery=ESPERA) is F.FALTA_INFORMACAO


def test_tipo_da_tentativa_rele_o_interrompida_gravado() -> None:
    autenticacao = "O app pede autenticação (senha)."
    assert tipo_da_tentativa("interrompida", autenticacao, "interrupted", recovery=ESPERA) == "autenticacao"
    assert tipo_da_tentativa("interrompida", autenticacao, "interrupted", recovery=PAUSA) == "interrompida"
    # outro tipo gravado nunca é relido
    assert tipo_da_tentativa("alvo_ausente", autenticacao, "interrupted", recovery=ESPERA) == "alvo_ausente"
    # sem gravado, o classificador de sempre; status sem falha, nada
    assert tipo_da_tentativa(None, autenticacao, "failed") == "autenticacao"
    assert tipo_da_tentativa(None, "x", "succeeded") is None
    # o aprendido da execução: a relida conta como classificada na leitura
    itens = _falhas([TentativaDaExecucao("interrompida", "interrupted", autenticacao, ESPERA),
                     TentativaDaExecucao("interrompida", "interrupted", autenticacao, PAUSA)])
    assert sorted((i.failure_kind, i.papel) for i in itens) == [
        ("autenticacao", "classificada na leitura (retroativo)"), ("interrompida", None)]


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "interrompida.sqlite3")
    yield d
    d.close()


def test_relatorio_rele_o_legado_que_esperou_a_pessoa(db: Database) -> None:
    autenticacao = "O app pede autenticação (senha)."
    for i in range(3):
        semear(db, f"r-espera{i}", dias(1 + i * 0.1), [Etapa("abrir", "OPEN_POST", "waiting_user",
                                                             [T("interrupted", autenticacao, tipo="interrompida")])])
        semear(db, f"r-pausa{i}", dias(2 + i * 0.1), [Etapa("abrir", "OPEN_POST", "ready",
                                                            [T("interrupted", autenticacao, tipo="interrompida")])])
    db.execute("UPDATE attempts SET recovery=? WHERE id LIKE 'r-espera%'", (ESPERA,))
    db.execute("UPDATE attempts SET recovery=? WHERE id LIKE 'r-pausa%'", (PAUSA,))
    mundo = montar(db)

    relida = grupo(mundo, "autenticacao", dias=14)
    assert relida is not None
    assert (relida.grupo.ocorrencias, relida.grupo.retroativas) == (3, 3)   # type: ignore[attr-defined]
    pausa = grupo(mundo, "interrompida", dias=14)
    assert pausa is not None and pausa.grupo.ocorrencias == 3              # type: ignore[attr-defined]
    # nada é regravado: a releitura é só da leitura
    assert db.scalar("SELECT COUNT(*) FROM attempts WHERE failure_kind='interrompida'") == 6
    # sem o retroativo, fica o que a execução gravou
    so_gravado = mundo.falhas.relatorio(dias=14, retroativo=False)
    assert {linha.grupo.chave.tipo for linha in so_gravado.itens} == {"interrompida"}


def test_o_scheduler_escreve_o_recovery_que_o_classificador_reconhece() -> None:
    fonte = inspect.getsource(scheduler)
    for recovery in ESPEROU_A_PESSOA:
        assert f'recovery="{recovery}"' in fonte, recovery
    # e os da pausa e da tomada não são confundidos com espera pela pessoa
    assert PAUSA in fonte and TOMADA in fonte
    assert not esperou_a_pessoa(PAUSA) and not esperou_a_pessoa(TOMADA)
