"""Redação central de texto sensível.

Esta é uma defesa **secundária**, por desenho. A defesa principal é impedir que o segredo seja registrado na
origem: o canal de entrada sensível não passa pelo caminho normal de digitação, então não existe argumento de ação
para preencher, e o Appium sobe com mascaramento de toda digitação.

Aqui a redação é feita por FORMATO, nunca por uma lista de valores em texto claro: manter um registro dos segredos
ativos para comparar por substring criaria mais uma cópia do segredo em memória, exatamente o que se quer evitar.
"""
from __future__ import annotations

import re
from typing import Any

MASK = "**REDACTED**"

# Cada padrão captura o prefixo (grupo 1) e substitui o que vem depois. São formatos que carregam segredo por
# natureza; nenhum deles depende de conhecer o valor.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # corpo de requisição do Appium: {"script":"mobile: type","args":[{"text":"<segredo>"}]}
    (re.compile(r'("script"\s*:\s*"mobile:\s*type"\s*,\s*"args"\s*:\s*\[\s*\{[^}]*?"text"\s*:\s*")[^"]*'), r"\1" + MASK),
    # envio de teclas do WebDriver: {"text":"<segredo>","value":[...]}
    (re.compile(r'("text"\s*:\s*")[^"]*("\s*,\s*"value"\s*:\s*\[)[^\]]*(\])'), r"\1" + MASK + r'\2"' + MASK + r'"\3'),
    # pares chave/valor evidentes, em JSON ou em texto solto
    # O `[\w.\-]*` antes da palavra-chave não é enfeite. Sem ele, `API_TOKEN=...`, `db_password=...` e
    # `INSTAGRAM_CREDENTIALS_MASTER_KEY=...` saíam EM CLARO: `\b` não casa entre `_` e a letra seguinte (os dois são
    # caractere de palavra), então `\btoken\b` simplesmente não existe dentro de `API_TOKEN`. Nome com hífen
    # (`X-Auth-Token`) já passava, o que tornava a falha ainda menos visível. Descoberto medindo, não lendo.
    (re.compile(r'((?:"|\b)[\w.\-]*(?:password|passwd|senha|pin|secret|segredo|token|api[_-]?key|master[_-]?key)'
                r'(?:"|\b)\s*[:=]\s*"?)(?![,}\s])[^"\s,}]+', re.IGNORECASE), r"\1" + MASK),
    (re.compile(r"(Authorization\s*:\s*Bearer\s+)\S+", re.IGNORECASE), r"\1" + MASK),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), MASK),
)


def redact(text: str | None) -> str | None:
    """Aplica todos os padrões conhecidos. Devolve o próprio valor quando não há nada a mascarar."""
    if not text:
        return text
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


# Nome de chave que, sozinho, já basta para mascarar o valor: numa estrutura o valor chega isolado, sem o contexto
# textual que os padrões acima usam.
_SENSITIVE_KEY = re.compile(r"password|passwd|senha|pin|secret|segredo|token|api[_-]?key|credential|credencial",
                            re.IGNORECASE)


def redact_obj(value: Any) -> Any:
    """Versão recursiva para estruturas que vão para evento, DTO ou log."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: (MASK if isinstance(k, str) and _SENSITIVE_KEY.search(k) and not isinstance(v, (dict, list, tuple))
                    else redact_obj(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact_obj(v) for v in value)
    return value


# Formatos que NÃO podem virar lembrança nem histórico, mesmo sem um nome de campo por perto: código de verificação
# ditado numa conversa, chave de API, sequência longa sem espaço com cara de token.
_SECRET_SHAPES: tuple[re.Pattern[str], ...] = (
    # "o código é 123456", "code: 8421", "seu pin de acesso 9931" — palavra-chave e dígitos a poucos caracteres
    re.compile(r"\b(?:c[oó]digo|code|pin|otp|2fa|verifica[çc][aã]o|verification|senha|password)\b.{0,16}?"
               r"\b[0-9]{4,8}\b", re.IGNORECASE),
    re.compile(r"\b[0-9]{4,8}\b.{0,16}?\b(?:c[oó]digo|code|pin|otp)\b", re.IGNORECASE),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\b(?:eyJ[A-Za-z0-9_\-]{10,}|gh[pousr]_[A-Za-z0-9]{20,})\b"),        # JWT, token do GitHub
    re.compile(r"(?<![\w/])[A-Za-z0-9+/]{32,}={0,2}(?![\w/])"),                       # blobs base64 longos
)


# Menção a credencial. Mais amplo que `looks_secret`: aqui basta o ASSUNTO, mesmo sem um valor reconhecível.
# Existe porque memória e histórico voltam ao modelo depois; uma frase com "minha senha é X" não pode ser guardada
# só porque X não tem cara de segredo.
_CREDENCIAL = re.compile(
    r"\b(senha|password|passwd|credencial|credential|token|api[ _-]?key|otp|2fa|pin|"
    r"c[oó]digo de (?:verifica[çc][aã]o|acesso|seguran[çc]a|confirma[çc][aã]o))\b", re.IGNORECASE)


def mentions_credential(text: str | None) -> bool:
    """Verdadeiro quando o texto FALA de credencial, código ou token — com ou sem o valor junto."""
    return bool(text) and bool(_CREDENCIAL.search(text or ""))


def looks_secret(text: str | None) -> bool:
    """Verdadeiro quando o texto tem FORMATO de segredo. Usado para recusar memória e histórico, não para mascarar.

    É deliberadamente conservador: recusar um fato inofensivo custa pouco; guardar um código de verificação numa
    lembrança que depois vai ao modelo custa caro.
    """
    if not text:
        return False
    if redact(text) != text:                      # já bate num padrão conhecido de credencial
        return True
    return any(p.search(text) for p in _SECRET_SHAPES)


class RedactingFilter:
    """Filtro de logging: redige a mensagem já formatada, antes de ela chegar a qualquer handler."""

    def filter(self, record: Any) -> bool:  # noqa: A003 - assinatura da stdlib
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001 - log nunca pode derrubar a aplicação
            return True
        clean = redact(text)
        if clean != text:
            record.msg = clean
            record.args = ()
        return True
