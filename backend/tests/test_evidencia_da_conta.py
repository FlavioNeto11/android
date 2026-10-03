"""A conta observada que a etapa comprovada grava (`instances.account_evidence`) é a frase que nomeia a conta, nunca
o seletor cru da prova pela árvore local (validação do deploy 8: android-06 "diverge" com `selector:…{username}`)."""
from __future__ import annotations

from app.taskqueue.executor import evidencia_da_conta

SELETOR = "pós-condição comprovada pela árvore local, sem IA (selector:id=action_bar_title|text=={username})"


def test_prova_por_seletor_cru_grava_a_pos_condicao_que_nomeia_a_conta() -> None:
    assert evidencia_da_conta("lucas.teste", "Perfil de @lucas.teste aberto", SELETOR) == "Perfil de @lucas.teste aberto"


def test_prova_que_nomeia_a_conta_e_a_evidencia() -> None:
    texto = "a tela mostra o perfil @lucas.teste"
    assert evidencia_da_conta("lucas.teste", "perfil aberto", texto) == texto


def test_sem_rotulo_ou_sem_a_conta_nada_e_gravado() -> None:
    assert evidencia_da_conta(None, "Perfil de @lucas.teste aberto", SELETOR) is None
    assert evidencia_da_conta("bruno", "Perfil de @lucas.teste aberto", SELETOR) is None
