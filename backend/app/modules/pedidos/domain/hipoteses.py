"""31.179: a hipótese da pesquisa promovida pela leitura do alvo. Puro: sem banco, sem IA.

Medido na onda 1 (06/10): a pesquisa da operação deixou 6 de 8 fatos como `hipotese` (uma fonte só), e nada os
reavaliava; vencido o frescor, saíam do contexto sem nunca terem sido conferidos. A leitura do alvo (a tela do post que
os agentes leem) é uma fonte independente da web: quando ela traz as mesmas âncoras do fato, ele passa a `confirmado`.

A régua, conservadora (incerteza nunca conta como sucesso):

* âncoras do fato: números de 2 ou mais dígitos, inteiros como aparecem (`12/10`, `10:00`, `15%`, `2026`), `#tag`,
  `@perfil` e palavras com inicial maiúscula de 4 letras ou mais fora do começo da frase;
* âncora FORTE: número de 4 ou mais caracteres, `#tag` ou `@perfil`; nomes próprios sozinhos promovem demais;
* promove quando o fato tem 2 ou mais âncoras, ao menos uma forte, e TODAS aparecem na leitura (sem acento e sem caixa;
  palavra inteira).

Limite conhecido: âncora não lê negação. "A loja fecha dia 12/10" e um post que diz "12/10, inauguração!" têm as mesmas
âncoras. Sem IA não há como separar; por isso o mínimo de duas âncoras com uma forte.

`confianca` só tem `confirmado` e `hipotese` (CHECK da migração 125): não há um terceiro estado ("coerente com a
tela") sem migração nova. A hipótese que a leitura não confirma fica como está e vence no frescor, com o motivo no
"revisar ou descartar" do 31.163.
"""
from __future__ import annotations

import re
import unicodedata

#: Mínimo de âncoras de um fato para a leitura poder confirmá-lo.
ANCORAS_MIN = 2
_NUMERO = re.compile(r"\d[\d/:.,%h-]*\d%?|\d{2,}%?")
_MARCA = re.compile(r"[#@][\w.]{2,}")
_PALAVRA = re.compile(r"[^\W\d_]{4,}")
_FIM_DE_FRASE = re.compile(r"[.!?:]\s*$")


def _normal(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).casefold()


def ancoras(texto: str) -> tuple[frozenset[str], frozenset[str]]:
    """(todas as âncoras, as fortes), normalizadas."""
    todas: set[str] = set()
    fortes: set[str] = set()
    for m in _NUMERO.finditer(texto):
        n = m.group(0).rstrip(".,-")
        if sum(c.isdigit() for c in n) >= 2:
            todas.add(_normal(n))
            if len(n) >= 4:
                fortes.add(_normal(n))
    for m in _MARCA.finditer(texto):
        marca = _normal(m.group(0).rstrip("."))
        todas.add(marca)
        fortes.add(marca)
    for m in _PALAVRA.finditer(texto):
        antes = texto[:m.start()]
        if m.group(0)[0].isupper() and antes.strip() and not _FIM_DE_FRASE.search(antes):
            todas.add(_normal(m.group(0)))
    return frozenset(todas), frozenset(fortes)


def _presente(ancora: str, leitura: str) -> bool:
    return re.search(r"(?<![\w#@])" + re.escape(ancora) + r"(?!\w)", leitura) is not None


def confirmada_pela_leitura(fato: str, leitura: str) -> bool:
    """A leitura do alvo traz todas as âncoras do fato (regra no topo do módulo)."""
    todas, fortes = ancoras(fato)
    if len(todas) < ANCORAS_MIN or not fortes:
        return False
    lida = _normal(leitura)
    return all(_presente(a, lida) for a in todas)


__all__ = ["ANCORAS_MIN", "ancoras", "confirmada_pela_leitura"]
