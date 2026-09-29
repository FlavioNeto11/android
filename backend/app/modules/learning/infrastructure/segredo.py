"""A porta `TriagemDeTexto` sobre a regra de credencial do central (`security/redaction.py`). O domínio e a aplicação
não enxergam `app.security` (catraca de camadas); por isso a regra chega por aqui, como nas habilidades.

O rigor é o do TEXTO DE PESSOA das habilidades, mais o assunto: nada do que o aprendizado grava volta ao modelo com
cara de credencial. Recusa quando o texto tem FORMATO de segredo (`looks_secret`), FALA de credencial
(`mentions_credential`: "minha senha é…", "o código de verificação…") ou tem uma palavra com cara de senha ou de
código (`RedactionSecretScreen.text_has_credential`). Recusar uma frase inofensiva custa pouco; guardar uma senha no
livro — que alimenta prompt e relatório — custa caro.
"""
from __future__ import annotations

from app.modules.skills.infrastructure.secret_screen import RedactionSecretScreen
from app.security.redaction import looks_secret, mentions_credential, redact


class TriagemDeCredencial:
    def __init__(self) -> None:
        self._habilidades = RedactionSecretScreen()

    def recusa(self, texto: str) -> bool:
        return looks_secret(texto) or mentions_credential(texto) or self._habilidades.text_has_credential(texto)

    def redigir(self, texto: str) -> str:
        return redact(texto) or ""
