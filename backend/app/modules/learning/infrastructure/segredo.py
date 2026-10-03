"""A porta `TriagemDeTexto` sobre a regra de credencial do central (`security/redaction.py`). O domínio e a aplicação
não enxergam `app.security` (catraca de camadas); por isso a regra chega por aqui, como nas habilidades.

O rigor é o do TEXTO DE PESSOA das habilidades, mais o assunto: nada do que o aprendizado grava volta ao modelo com
cara de credencial. Recusa quando o texto tem FORMATO de segredo (`looks_secret`), FALA de credencial
(`mentions_credential`: "minha senha é…", "o código de verificação…") ou tem uma palavra com cara de senha ou de
código (`RedactionSecretScreen.text_has_credential`). Recusar uma frase inofensiva custa pouco; guardar uma senha no
livro — que alimenta prompt e relatório — custa caro.

29.52: é também a fonte ÚNICA da pergunta que pede credencial e da resposta que não pode virar comando — o painel
(`taskqueue/assistente.py`) e os canais (Telegram, Trello) leem as duas daqui, nunca de um vocabulário próprio.
"""
from __future__ import annotations

import re

from app.modules.skills.infrastructure.secret_screen import RedactionSecretScreen
from app.security.redaction import looks_secret, mentions_credential, parece_codigo, redact

#: O ASSUNTO de uma pergunta que pede credencial, por tipo, na ordem em que se decide. Mais largo que
#: `mentions_credential`: numa pergunta, "qual o código que chegou por SMS?" já pede o código sem dizer "de
#: verificação". Na dúvida, recusa: "qual o código do cupom?" também cai aqui, e a pessoa responde pelo painel.
_PERGUNTA: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("senha", re.compile(r"\b(senha|password|passwd|passphrase|frase secreta|passwort|kennwort|wachtwoord|"
                         r"mot de passe|contrase[nñ]a)\b", re.IGNORECASE)),
    ("2fa", re.compile(r"\b(2fa|mfa|dois fatores|duas etapas|two[ -]factor|autentica[çc][aã]o)\b", re.IGNORECASE)),
    ("codigo", re.compile(r"\b(c[oó]digo|code|otp|pin|sms|verifica[çc][aã]o)\b", re.IGNORECASE)),
    ("token", re.compile(r"\b(token|api ?key|chave (?:de api|secreta|de acesso)|segredo|secret|credencial|"
                         r"credential)\b", re.IGNORECASE)),
)


class TriagemDeCredencial:
    def __init__(self) -> None:
        self._habilidades = RedactionSecretScreen()

    def recusa(self, texto: str) -> bool:
        return looks_secret(texto) or mentions_credential(texto) or self._habilidades.text_has_credential(texto)

    def redigir(self, texto: str) -> str:
        return redact(texto) or ""

    def pergunta_sensivel(self, pergunta: str, campo: str = "") -> str | None:
        """O tipo de credencial que a pergunta PEDE (`senha`, `2fa`, `codigo`, `token`, ou `credencial` pelo assunto
        genérico), ou None. O campo conta também: `verification_code` vira "verification code"."""
        texto = f"{pergunta or ''} {re.sub(r'[_.-]+', ' ', campo or '')}"
        for tipo, padrao in _PERGUNTA:
            if padrao.search(texto):
                return tipo
        return "credencial" if mentions_credential(texto) else None

    def resposta_recusada(self, texto: str) -> bool:
        """Uma resposta que não pode virar comando nem ir ao modelo, mesmo sem saber a pergunta: o rigor do texto de
        pessoa (`recusa`) mais o código solto de 4 a 8 dígitos (`parece_codigo`)."""
        return self.recusa(texto) or parece_codigo(texto)
