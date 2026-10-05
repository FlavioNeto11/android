"""Regras puras da exclusão a pedido do titular (29.83, ADR-075): como o telefone se compara e o que se aceita do pedido.

A comparação é SEMPRE do número inteiro, nunca por prefixo nem pelos finais: "os últimos 8" casava números de outros
DDDs (revisão do procedimento manual, X2). Dois jeitos de escrever o mesmo número casam:
- o `55` do país é opcional nos números brasileiros completos (DDD + número): `+55 (11) …` e `11 …` são o mesmo;
- o `0` da frente (o de discagem, `011 …`) sai antes de comparar.
Fora disso (número sem DDD, internacional, comprido), vale a igualdade exata de todos os dígitos: o formulário aceita
qualquer telefone com 8 dígitos ou mais (`campos.TELEFONE`), e a exclusão tem de achar tudo o que ele aceitou
(revisão do #342, E1). O mínimo da busca é o mesmo mínimo do formulário, para não virar busca curta.
"""
from __future__ import annotations

#: Por onde o pedido chegou. Sem texto livre: texto livre vira lugar de dado pessoal (decisão da orquestradora, 04/10).
PEDIDO_POR = frozenset({"formulario", "telefone", "outro"})

#: Quantos contatos uma exclusão aceita de uma vez: o pedido de uma pessoa, não uma faxina.
IDS_MAX = 50

#: Quantos dígitos o final mostrado na lista tem: desempata homônimos sem expor o número.
DIGITOS_DO_FINAL = 4

#: O mínimo e o máximo de dígitos da busca: os do formulário (8 dígitos ou mais, em até 30 caracteres).
DIGITOS_MIN = 8
DIGITOS_MAX = 30

_PAIS = "55"


def digitos(texto: str) -> str:
    return "".join(c for c in texto if c.isascii() and c.isdigit())


def chave_do_telefone(texto: str) -> str | None:
    """O número na forma de comparar, ou `None` se não chega a um telefone que o formulário aceitaria. Brasileiro
    completo vira DDD + número (sem o `55`); o resto fica com todos os dígitos, sem os zeros da frente.

    O mínimo e o máximo contam os dígitos ANTES de tirar os zeros, como o formulário conta (`campos.py`): um `0` e
    mais 7 dígitos passa lá e tem de ser achado aqui (revisão do #342, E3-b)."""
    d = digitos(texto)
    if len(d) < DIGITOS_MIN or len(d) > DIGITOS_MAX:
        return None
    d = d.lstrip("0")
    if not d:
        return None
    if len(d) in (12, 13) and d.startswith(_PAIS):
        return "br:" + d[len(_PAIS):]
    if len(d) in (10, 11):
        return "br:" + d
    return "num:" + d


def mesmo_telefone(informado: str, guardado: str) -> bool:
    """Os dois números são o mesmo, inteiros. Telefone guardado vazio (o descarte apaga) nunca casa."""
    a, b = chave_do_telefone(informado), chave_do_telefone(guardado)
    return a is not None and a == b


def nome_do_operador(nome: str | None) -> str:
    """O nome da sessão como se compara nos tetos de busca: sem espaço sobrando e sem diferença de caixa. A mesma regra
    de `pedidos.operadores_do_dono` (`pedidos/domain/autor.py`), para "Dono  Teste " e "dono teste" serem o mesmo dono."""
    return " ".join((nome or "").split()).casefold()


def final(telefone: str) -> str:
    return digitos(telefone)[-DIGITOS_DO_FINAL:]
