"""A porta `SecretScreen` sobre a regra de credencial do central (`security/redaction.py`) — a mesma da recusa do
comando com senha (`taskqueue/service.py::create`) e a mesma do gravador (`training/recorder.py`).

Dois rigores, de propósito:
- VALOR de documento ou anotação: só FORMATO (`looks_secret`: par `senha: …`, token, JWT, código de verificação,
  blob longo). Uma palavra com dígito e maiúscula num documento é comum (`automation/v1alpha1`) e não é senha;
- TEXTO DA PESSOA (instrução, resposta, correção): o formato, mais cada palavra com cara de senha ou de código
  (`parece_senha_ou_codigo`, a regra do gravador). É o texto que vai ao provedor de IA e fica na conversa; recusar
  uma frase inofensiva custa pouco, guardar uma senha respondida a uma pergunta custa caro.
"""
from __future__ import annotations

import re

from app.security.redaction import looks_secret, parece_senha_ou_codigo

_PONTUACAO = ".,;:!?\"'“”‘’()[]{}<>"


class RedactionSecretScreen:
    def value_is_secret(self, text: str) -> bool:
        return looks_secret(text)

    def text_has_credential(self, text: str) -> bool:
        if looks_secret(text):
            return True
        for palavra in re.split(r"\s+", text):
            p = palavra.strip(_PONTUACAO)
            if p.lower().startswith(("http://", "https://")):
                continue                     # endereço tem maiúscula, dígito e símbolo sem ser senha
            if parece_senha_ou_codigo(p):
                return True
        return False
