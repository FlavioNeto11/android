"""Porta `DecisaoFechada` do hub de IA (Fase 31, item 31.4, ADR-069): decisão por conjunto fechado, desligada por padrão.

Contrato em `contrato.py`, privacidade que falha fechada em `privacidade.py`, decisores (nulo, falso e o ponto de extensão do
real, item 31.8) em `decisores.py` e modos, timeout e recurso ao caminho atual em `porta.py`. Nada aqui chama a TypeSafe: o
único arquivo que conhece o host é `modules/context_retrieval/adapters/jev.py`.
"""
from .contrato import (
    ID_NENHUMA, MAX_OPCOES, FalhaDeDecisao, PedidoDeDecisao, Pergunta, RespostaDeDecisao, ResultadoDeDecisao,
    pergunta_choice,
)
from .decisores import Decisor, DecisorFalso, DecisorNulo
from .porta import Porta, RegistroDeDecisao, construir_porta, modo_efetivo
from .privacidade import JEV_ALLOWED_CLASSES, JEV_RUNTIME_SEND_APPROVED, Veredito, validar

__all__ = ["Decisor", "DecisorFalso", "DecisorNulo", "FalhaDeDecisao", "ID_NENHUMA", "JEV_ALLOWED_CLASSES",
           "JEV_RUNTIME_SEND_APPROVED", "MAX_OPCOES", "PedidoDeDecisao", "Pergunta", "Porta", "RegistroDeDecisao",
           "RespostaDeDecisao", "ResultadoDeDecisao", "Veredito", "construir_porta", "modo_efetivo",
           "pergunta_choice", "validar"]
