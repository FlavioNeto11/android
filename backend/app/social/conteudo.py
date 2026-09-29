"""Regra de conteúdo do redator da persona (ADR-055): a persona fala só por si.

Caso real (r-20260919220216-7cfa59, 19/09): sete contas mandaram à mesma pessoa, em oito minutos, variações de "seu
marido mandou um oi aqui pra você" e "recebi um oi do seu marido pra te repassar". Nenhum marido mandou nada: o texto
inventava um recado de um terceiro REAL, e é o tipo de mensagem que faz a pessoa denunciar a conta. Cinco das oito
contas estão bloqueadas hoje.

A regra está escrita no papel do sistema (`SOCIAL_SYSTEM`), mas regra de prompt é pedido, não garantia — o mesmo
raciocínio do descarte de memória em `SocialService.draft_response`. Isto aqui é a trava em código: reconhece a família
de frases do caso real (recado, "mandou um oi", "pediu pra te avisar", "fulano disse que…") e quem chama reescreve uma
vez ou recusa. Não tenta entender a língua inteira: um falso positivo custa uma reescrita (ou a recusa, que espera uma
pessoa); um falso negativo pode custar uma conta.

Sujeito em primeira ou segunda pessoa ("eu disse", "você mandou um oi") não é terceiro: é a própria persona ou a
pessoa com quem ela fala, e responder ao que a pessoa disse é conversa normal.
"""
from __future__ import annotations

import re
import unicodedata

# Quem NÃO é terceiro: a própria persona e a pessoa com quem ela fala. Sem acento (o texto é normalizado antes).
# "nos" fica de fora de propósito: quase sempre é objeto ("ele nos disse que…"), e aí quem fala é um terceiro.
_PRIMEIRA_OU_SEGUNDA = frozenset({"eu", "voce", "voces", "vc", "vcs", "tu", "gente", "i", "you", "we"})
# Na fala RELATADA ("minha mãe sempre disse que…") o círculo da própria persona é lembrança dela, não recado a
# ninguém. Não vale para saudação e pedido de aviso: "minha amiga mandou um oi pra você" é o mesmo recado inventado.
_CIRCULO_DA_PERSONA = _PRIMEIRA_OU_SEGUNDA | {"meu", "minha", "meus", "minhas", "my"}
_NINGUEM = frozenset[str]()

# Parentes e relações: "seu marido quer…", "sua mãe mandou…" — é o formato exato do caso real.
_RELACAO = (r"(?:marido|esposa|mulher|noiv[oa]|namorad[oa]|mae|pai|filh[oa]|irma|irmao|avo|ti[oa]|prim[oa]|amig[oa]|"
            r"chefe|colega|vizinh[oa]|sogr[oa]|cunhad[oa]|parceir[oa]|companheir[oa]|patra[o]?|patroa|ex|"
            r"husband|wife|mom|mother|dad|father|boyfriend|girlfriend|friend|boss)")
_VERBO_DE_FALA_OU_INTENCAO = (r"(?:mandou|mandaram|manda|pediu|pediram|pede|disse|diz|falou|fala|avisou|contou|"
                              r"comentou|lembrou|quer|queria|quis|gostaria|acha|achou|enviou|deixou|perguntou|"
                              r"garantiu|prometeu|esta com saudade|sente|sentiu|"
                              r"said|says|asked|wants|wanted|sent|told)")
_SAUDACAO = (r"(?:oi|ola|alo|beijo|beijos|beijinho|abraco|abracos|recado|recadinho|lembranca|lembrancas|mensagem|"
             r"hi|hello)")

_PADROES: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = (
    # (padrão, sujeitos que NÃO são terceiro antes do verbo) — o grupo `verbo` marca onde o sujeito termina; conjunto
    # vazio = o próprio padrão já diz de quem é a fala, não há sujeito a conferir.
    # "seu marido mandou um oi", "sua mãe acha que…", "your husband asked me…"
    (re.compile(rf"\b(?:seu|sua|teu|tua|your)\s+{_RELACAO}\b[^.!?\n]{{0,30}}?"
                rf"\b(?P<verbo>{_VERBO_DE_FALA_OU_INTENCAO})\b"), _NINGUEM),
    # "mandou um oi", "pediu um oi pra você", "deixou um beijo" (terceira pessoa relatando saudação de alguém). O
    # presente "manda" fica de fora: "manda um beijo pra sua mãe" é a persona pedindo, não relatando.
    (re.compile(rf"\b(?P<verbo>mandou|mandaram|pediu|pediram|deixou|enviou|sent|sends)\s+"
                rf"(?:um|uma|o|a|seus|suas|meus|minhas|his|her|their)?\s*{_SAUDACAO}\b"), _PRIMEIRA_OU_SEGUNDA),
    # "pediu para eu te avisar", "mandou pra te dizer", "asked me to tell you"
    (re.compile(r"\b(?P<verbo>pediu|pediram|mandou|mandaram|asked)\s+(?:pra|para|p/|me\s+to|to)\s+"
                r"(?:eu\s+|mim\s+)?(?:te|lhe|voce|vc)?\s*(?:avisar|dizer|falar|mandar|dar|repassar|entregar|contar|"
                r"lembrar|tell|say|give|pass)\b"), _PRIMEIRA_OU_SEGUNDA),
    # "recebi um oi do seu marido", "trago um recado da Ana"
    (re.compile(rf"\b(?:recebi|trago|trouxe|tenho)\s+(?:um|uma|o|a)?\s*{_SAUDACAO}\s+(?:d[oa]s?|de)\s+\S+"), _NINGUEM),
    # "recado do fulano", "recadinho da Ana"
    (re.compile(r"\b(?:recado|recadinho)\s+(?:d[oa]s?|de)\s+\S+"), _NINGUEM),
    # "a Joana disse que…", "ele falou pra…": fala de alguém que não é a persona nem a pessoa com quem ela fala
    (re.compile(r"\b(?P<verbo>disse|falou|contou|comentou|avisou|garantiu|afirmou|prometeu)\s+(?:que|pra|para)\b"),
     _CIRCULO_DA_PERSONA),
)
_ESPACOS = re.compile(r"\s+")
_PALAVRA = re.compile(r"[a-z]+")


def _normalizado(texto: str) -> str:
    """Minúsculas, sem acento, espaço simples: "Você" e "voce", "Mãe" e "mae" são a mesma palavra aqui."""
    sem_acento = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return _ESPACOS.sub(" ", sem_acento.lower())


def _sujeito_nao_e_terceiro(texto: str, inicio_do_verbo: int, quem: frozenset[str]) -> bool:
    """As até três palavras antes do verbo trazem "eu"/"você" (ou o círculo da persona, quando vale)? Então quem fala
    é a persona ou a pessoa com quem ela conversa ("você me disse que…", "eu disse que…"), não um terceiro."""
    antes = _PALAVRA.findall(texto[max(0, inicio_do_verbo - 40):inicio_do_verbo])[-3:]
    return any(p in quem for p in antes)


def fala_atribuida_a_terceiro(texto: str | None) -> str | None:
    """O trecho que atribui fala, intenção ou recado a um terceiro; `None` quando não há.

    Devolve o trecho (e não só sim/não) para o motivo da recusa dizer À PESSOA o que foi barrado."""
    if not texto or not texto.strip():
        return None
    norm = _normalizado(texto)
    for padrao, nao_sao_terceiro in _PADROES:
        for achado in padrao.finditer(norm):
            if nao_sao_terceiro and _sujeito_nao_e_terceiro(norm, achado.start("verbo"), nao_sao_terceiro):
                continue
            return achado.group(0).strip()
    return None
