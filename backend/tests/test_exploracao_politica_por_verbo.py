"""31.325 (ADR-091): a política da exploração de efeito tem um nível por VERBO e um padrão por verbo.

Regra do dono (10/10): "executar sem pedir aprovação por request, salvo ação destrutiva". Então:
* a ordem da busca é a do pedido (`explorar_<verbo>_<objeto>`), a do verbo (`explorar_<verbo>`) e, só para o verbo comum, a genérica
  `explorar_efeito`;
* o destrutivo (lista única em `contracts/efeito_destrutivo.py`) mantém `approval_required` e NÃO herda a genérica: liberar tudo de
  uma vez não libera apagar nem comprar; só uma escolha sobre o verbo ou o pedido o libera;
* o verbo comum roda sem pedir o sim por padrão (e o dono continua avisado na hora).

Nível de prova: `simulated`. `real`: `not_run`.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.contracts.efeito_destrutivo import VERBOS_DESTRUTIVOS, e_verbo_destrutivo
from app.planning import exploracao as ex

from .conftest import Harness
from .test_exploracao_de_efeito import _avisos, _porta, _semear


# ------------------------------------------------------------------ puro
@pytest.mark.parametrize(("pedido", "verbo", "destrutivo"), [
    ("apagar a pasta de spam", "apagar", True), ("excluir a mensagem", "apagar", True), ("remover o contato", "apagar", True),
    ("esvaziar a lixeira", "apagar", True), ("deletar os arquivos", "apagar", True), ("limpar o histórico", "apagar", True),
    ("comprar um plano", "comprar", True), ("pagar a fatura", "comprar", True), ("assinar o plano", "comprar", True),
    ("transferir o valor", "transferir", True), ("encerrar a conta", "encerrar", True), ("desinstalar o app", "desinstalar", True),
    ("enviar uma mensagem", "enviar", False), ("mandar um e-mail", "enviar", False), ("publicar uma foto", "publicar", False),
    ("marcar como lida", "marcar", False), ("mover a mensagem para a pasta", "mover", False), ("curtir o post", "curtir", False),
])
def test_a_lista_de_destrutivos_e_pelo_verbo_canonico(pedido: str, verbo: str, destrutivo: bool) -> None:
    e = ex.classificar(pedido)
    assert e.destino is ex.Destino.EFEITO and not e.de_credencial and e.verbo == verbo
    assert ex.e_destrutiva(e.chave) is destrutivo and e_verbo_destrutivo(e.verbo) is destrutivo


def test_todo_verbo_destrutivo_existe_como_verbo_de_efeito_e_nunca_e_credencial() -> None:
    assert VERBOS_DESTRUTIVOS <= ex.VERBOS_DE_EFEITO               # senão a lista tem verbo que nunca chega à chave
    assert not (VERBOS_DESTRUTIVOS & ex.FORMAS_DE_CREDENCIAL)


def test_desconectar_nao_e_destrutivo_e_sim_credencial_e_nunca_explora() -> None:
    e = ex.classificar("desconectar a conta")
    assert e.de_credencial and ex.verbo_da_chave(e.chave) is None


@pytest.mark.parametrize(("chave", "esperado"), [
    ("explorar_enviar_email", ("explorar_enviar_email", "explorar_enviar", "explorar_efeito")),
    ("explorar_enviar", ("explorar_enviar", "explorar_efeito")),
    ("explorar_apagar_pasta_spam", ("explorar_apagar_pasta_spam", "explorar_apagar")),      # sem a genérica
    ("explorar_apagar", ("explorar_apagar",)),
    ("explorar_efeito", ("explorar_efeito",)),
    ("explorar_ver_caixa_lixo", ("explorar_ver_caixa_lixo",)),                            # leitura: só a própria
    ("LIKE_POST", ("LIKE_POST",)),
])
def test_a_ordem_das_chaves_de_politica(chave: str, esperado: tuple[str, ...]) -> None:
    assert ex.chaves_da_politica(chave) == esperado


def test_a_chave_do_verbo_e_configuravel() -> None:
    assert ex.chave_de_politica_valida("explorar_apagar") and ex.chave_de_politica_valida("explorar_enviar")
    assert not ex.chave_de_politica_valida("explorar_entrar") and not ex.chave_de_politica_valida("explorar_ver")


# ------------------------------------------------------------------ a porta
APAGAR = "explorar_apagar_mensagens"
ENVIAR = "explorar_enviar_mensagem"


@pytest.mark.parametrize(("chave", "politica", "libera"), [
    (ENVIAR, {}, True),                                                                        # comum: sem pedir
    (APAGAR, {}, False),                                                                       # destrutivo: aprovação
    (APAGAR, {"explorar_efeito": "autonomous"}, False),                                        # a genérica NÃO libera o destrutivo
    (APAGAR, {"explorar_apagar": "autonomous"}, True),                                         # a do verbo libera
    (APAGAR, {"explorar_apagar_mensagens": "autonomous"}, True),                               # a do pedido libera
    (APAGAR, {"explorar_apagar": "autonomous", "explorar_apagar_mensagens": "manual_only"}, False),   # pedido vence verbo
    (ENVIAR, {"explorar_efeito": "autonomous", "explorar_enviar": "manual_only"}, False),     # verbo vence genérica
    (ENVIAR, {"explorar_efeito": "manual_only", "explorar_enviar": "autonomous"}, True),
    (ENVIAR, {"explorar_efeito": "approval_required"}, False),                                 # o dono volta a pedir o sim
])
async def test_a_porta_segue_o_padrao_e_a_ordem_das_chaves(harness: Harness, chave: str, politica: dict[str, Any],
                                                          libera: bool) -> None:
    state = harness.state
    assert state is not None
    _semear(state, chave=chave, politica={"capabilities": politica} if politica else {})
    veredito = await _porta(state, chave)
    if libera:
        assert veredito is None and len(_avisos(state)) == 1
    else:
        assert veredito is not None and not veredito.allowed and _avisos(state) == []
