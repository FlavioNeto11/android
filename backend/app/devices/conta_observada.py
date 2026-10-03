"""A conta observada (`instances.account_evidence`) como frase para a pessoa (I2 da validação do deploy 9).

A evidência nasce do texto da verificação da etapa, e parte dele é prova por seletor: "seletor id=…|text=qa-user-10: 1
elemento(s)" (pós-condição `element_present`), o próprio seletor da pós-condição ("id=action_bar_title|text==fulano") ou
o "(selector:…)" de uma prova antiga pela árvore local. Nada disso é texto para a pessoa: com o rótulo dentro, a prova diz
que a conta foi vista na tela; sem ele, não diz nada sobre a conta (o android-06 ficou "conta diferente do rótulo" por uma
evidência de 02/10 que só tinha o seletor). Vale na gravação (`taskqueue/executor.py::evidencia_da_conta`) e na leitura
(`DeviceManager.dto`), então a evidência antiga gravada crua também sai legível, sem migração."""
from __future__ import annotations

import re

from ..util import norm_text

_PROVA_POR_SELETOR = re.compile(r"^seletor\s+(?P<sel>.+?):\s*(?P<n>\d+)\s+elemento\(s\)$", re.S)
_SELETOR_CRU = re.compile(r"^(?:id|text|desc|content-desc|xpath|class|resource[-_]id)\s*={1,2}", re.I)
_SELETOR_ENTRE_PARENTESES = re.compile(r"\s*\((?:selector|xpath|id):[^)]*\)")


def evidencia_legivel(account_label: str | None, evidencia: str | None) -> str | None:
    """A evidência que a pessoa lê, ou None quando ela não é observação de conta.

    - Prova por seletor (`seletor <sel>: N elemento(s)`, ou o seletor cru da pós-condição): com o rótulo no seletor (e
      ao menos um elemento), "<rótulo> visto na tela"; sem o rótulo, None.
    - "(selector:…)" de prova antiga: sai; se o que sobra não traz o rótulo, None.
    - Frase que já nomeia a conta: fica como está."""
    if not evidencia or not evidencia.strip():
        return None
    texto = evidencia.strip()
    rotulo = norm_text(account_label)
    if m := _PROVA_POR_SELETOR.match(texto):
        seletor, achados = m["sel"], int(m["n"])
    elif _SELETOR_CRU.match(texto):
        seletor, achados = texto, 1                       # a pós-condição comprovada é o próprio seletor
    else:
        if not _SELETOR_ENTRE_PARENTESES.search(texto):
            return texto
        limpo = _SELETOR_ENTRE_PARENTESES.sub("", texto).strip()
        return limpo if rotulo and rotulo in norm_text(limpo) else None
    if achados > 0 and rotulo and rotulo in norm_text(seletor):
        return f"{(account_label or '').strip()} visto na tela"
    return None
