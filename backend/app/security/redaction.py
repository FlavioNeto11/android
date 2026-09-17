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
    (re.compile(r'((?:"|\b)(?:password|passwd|senha|pin|secret|segredo|token|api[_-]?key)(?:"|\b)\s*[:=]\s*"?)'
                r'(?![,}\s])[^"\s,}]+', re.IGNORECASE), r"\1" + MASK),
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
