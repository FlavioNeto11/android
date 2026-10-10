"""Senha de conta gerada no servidor (31.281, ADR-087): aleatoriedade criptográfica, sem IA e sem rede.

O valor sai daqui direto para o cofre (`SecretStore.store_secret`) e nunca volta por API, log nem evento. Entram sempre
as quatro classes de caractere (maiúscula, minúscula, dígito e símbolo), porque os provedores de e-mail as exigem; o
alfabeto evita os caracteres que se confundem na leitura (`0O1lI`) e os que quebram um campo de formulário ou uma
linha de comando (aspas, barra, crase, `<>&`, espaço).
"""
from __future__ import annotations

import secrets

TAMANHO_MIN = 16
TAMANHO_MAX = 64
TAMANHO_PADRAO = 20

_MAIUSCULAS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_MINUSCULAS = "abcdefghijkmnopqrstuvwxyz"
_DIGITOS = "23456789"
_SIMBOLOS = "!#$%*+-=?@^_"
_CLASSES = (_MAIUSCULAS, _MINUSCULAS, _DIGITOS, _SIMBOLOS)
_TODOS = "".join(_CLASSES)


def gerar_senha(tamanho: int = TAMANHO_PADRAO) -> str:
    """Uma senha forte de `tamanho` caracteres (16 a 64), com ao menos um de cada classe, em ordem aleatória."""
    if not TAMANHO_MIN <= tamanho <= TAMANHO_MAX:
        raise ValueError(f"tamanho da senha fora de {TAMANHO_MIN}..{TAMANHO_MAX}")
    caracteres = [secrets.choice(c) for c in _CLASSES]
    caracteres += [secrets.choice(_TODOS) for _ in range(tamanho - len(caracteres))]
    secrets.SystemRandom().shuffle(caracteres)
    return "".join(caracteres)
