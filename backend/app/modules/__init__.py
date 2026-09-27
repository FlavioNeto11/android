"""Contextos do monólito modular: um pacote por contexto (`applications`, …), cada um com as suas camadas.

Tudo aqui nasce sob as regras de `tests/test_arquitetura.py`: zero `Any` em anotação, nenhum import dentro de
função, fora de qualquer ciclo, contextos formando um DAG. `domain` e `application` não veem infraestrutura;
`infrastructure` é onde o contexto fala com o banco (`app.db`).
"""
