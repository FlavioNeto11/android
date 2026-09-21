"""Comandos do painel como entidade: quem pediu, o que foi despachado, o que foi confirmado e o que ficou incerto.

Existe porque a ação de instância era um `202 {"accepted": true}` sem registro: a interface chamava de sucesso o
que só tinha sido aceito, e a recusa no fundo virava um evento que ninguém mostrava.
"""
