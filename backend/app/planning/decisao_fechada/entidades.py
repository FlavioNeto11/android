"""Remoção de entidades que FALHA FECHADA, para a C3 da intenção (item 31.9, ADR-069 item 4, correção 1 do roteiro).

A C3 é o comando do dono depois de `sem_destinos` e de `redact`: texto livre, que pode trazer nome de terceiro, `@handle`,
e-mail, número, telefone e link. `remover_entidades` troca cada um por um marcador FIXO e minúsculo e, em seguida, CONFERE
o resultado com detectores mais largos que os da troca. Se sobrar qualquer indício de entidade, devolve `None` e o pedido
não sai: o chamador registra `fallback_reason='privacidade'` em vez de mandar o texto. Na dúvida, recusa.

O que a heurística NÃO pega (e por isso a classe só vale em sombra, na origem `intencao`): nome próprio em minúsculas, nome
de terceiro como PRIMEIRO termo de uma frase sem vírgula depois dele ("Joana curtiu isso") e dado pessoal escrito por
extenso ("meu número é um dois três"). O que o texto entre aspas disser é trocado inteiro por `[texto]`: é o que a pessoa
manda escrever, e escrever é conteúdo dela ou de outra pessoa.

Função pura: sem banco, sem rede, sem configuração.
"""
from __future__ import annotations

import re
from typing import Final

#: Marcadores fixos. Minúsculos e entre colchetes de propósito: nenhum detector (nome próprio exige maiúscula; os demais exigem
#: `@`, `://`, ponto entre palavras ou dígito) os reconhece de novo na conferência.
M_URL: Final = "[link]"
M_EMAIL: Final = "[email]"
M_HANDLE: Final = "[usuario]"
M_TELEFONE: Final = "[telefone]"
M_NUMERO: Final = "[numero]"
M_NOME: Final = "[nome]"
M_TEXTO: Final = "[texto]"

_MARCADORES: Final = (M_URL, M_EMAIL, M_HANDLE, M_TELEFONE, M_NUMERO, M_NOME, M_TEXTO)

# Troca (do mais específico ao mais geral). `\w` é Unicode no `re` de str: acentos contam.
_ASPAS = re.compile(r'"[^"]*"|“[^”]*”|«[^»]*»|‘[^’]*’')
_URL = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.\-]*://|www\.)\S+")
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)*")
_HANDLE = re.compile(r"@\w[\w.]*")
_DOMINIO = re.compile(r"(?i)\b[\w\-]+(?:\.[\w\-]+)*\.[a-z]{2,}(?:/\S*)?\b")
_TELEFONE = re.compile(r"(?:\+?\d{1,3}[\s.\-])?\(?\d{2}\)?[\s.\-]?\d{4,5}[\s.\-]?\d{4}")
#: Número de 3 dígitos ou mais, também quebrado por UM separador entre dígitos ("12 34 56", "1.234", "11-98").
_NUMERO = re.compile(r"\d(?:[\s.\-/]?\d){2,}")

# Conferência (mais larga que a troca): qualquer sobra é recusa.
_SOBRA_ARROBA = re.compile(r"@|://|(?i:\bwww\b)|(?i:\barroba\b)|(?i:\bponto\s+com\b)|(?i:\bdot\s+com\b)")
_SOBRA_DIGITOS = re.compile(r"\d\D{0,2}\d\D{0,2}\d")
_SOBRA_DOMINIO = re.compile(r"(?i)\b[\w\-]+\.[a-z]{2,}\b")

#: Palavra capitalizada (inclui TUDO MAIÚSCULO, apóstrofo e hífen: "D'Ávila", "Ana-Clara").
_CAPITALIZADA = re.compile(r"[A-ZÀ-ÖØ-Þ][\wÀ-ÿ'’\-]*")

#: Palavras capitalizadas que NÃO são nome (começo de frase em caixa-alta, dias e meses, pronomes e tratamento comum). Lista
#: curta de propósito: cada palavra a mais é um nome de terceiro que passaria.
_COMUNS: Final[frozenset[str]] = frozenset("""
a o as os um uma uns umas de da do das dos em na no nas nos por para com sem sob sobre entre ate e ou mas que se ao aos
eu tu ele ela nos vos eles elas voce voces meu minha meus minhas seu sua seus suas
sim nao
domingo segunda terca quarta quinta sexta sabado
janeiro fevereiro marco abril maio junho julho agosto setembro outubro novembro dezembro
""".split())

_ACENTOS: Final = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ", "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC")


def _comum(palavra: str) -> bool:
    return palavra.translate(_ACENTOS).lower() in _COMUNS


def _inicio_de_frase(texto: str, pos: int) -> bool:
    """A palavra em `pos` abre o texto ou uma frase (depois de `.`, `!`, `?`, `:` ou quebra de linha, e de abre-aspas)."""
    prefixo = texto[:pos].rstrip(" \t([{\"'«“")
    return prefixo == "" or prefixo[-1] in ".!?…:\n"


def _nomes(texto: str) -> str:
    """Troca por `[nome]` a palavra capitalizada fora do começo de frase e fora da lista de comuns.

    A palavra de começo de frase também vira nome quando vem seguida de vírgula ("Maria, abra...") ou de outra palavra
    capitalizada ("Maria Silva abriu..."): é o jeito de um nome aparecer na frente, e custa pouco recusar."""
    saida: list[str] = []
    cursor = 0
    achados = list(_CAPITALIZADA.finditer(texto))
    for i, m in enumerate(achados):
        proxima = achados[i + 1] if i + 1 < len(achados) else None
        colada = proxima is not None and texto[m.end():proxima.start()].strip() == ""
        nome = False
        if not _comum(m.group()):
            nome = (not _inicio_de_frase(texto, m.start())) or texto[m.end():m.end() + 1] == "," or colada
        saida.append(texto[cursor:m.start()])
        saida.append(M_NOME if nome else m.group())
        cursor = m.end()
    saida.append(texto[cursor:])
    # colapsa marcadores de nome consecutivos ("Ana Silva" -> um só)
    return re.sub(r"(?:\[nome\]\s*){2,}", M_NOME + " ", "".join(saida))


def _sem_marcadores(texto: str) -> str:
    for m in _MARCADORES:
        texto = texto.replace(m, " ")
    return texto


def _sobra_alguma_entidade(texto: str) -> bool:
    """A conferência: roda nos restos, SEM os marcadores. Qualquer indício recusa."""
    resto = _sem_marcadores(texto)
    if _SOBRA_ARROBA.search(resto) or _SOBRA_DIGITOS.search(resto) or _SOBRA_DOMINIO.search(resto):
        return True
    # um segundo passe do detector de nome sobre o que sobrou: nada pode mudar
    return _nomes(resto) != resto


def remover_entidades(texto: str) -> str | None:
    """O texto sem entidades, ou `None` se, depois de trocá-las, ainda sobra qualquer indício (falha fechada).

    Ordem: o que está entre aspas, link, e-mail, `@handle`, domínio solto, telefone, número (3 dígitos ou mais) e, por
    fim, nome próprio. Os marcadores são fixos (`[link]`, `[email]`, `[usuario]`, `[telefone]`, `[numero]`, `[nome]`,
    `[texto]`) e a conferência final os ignora. Devolve a string trocada (pode ser igual à de entrada, se não havia nada)."""
    if not isinstance(texto, str):
        return None
    trocado = _ASPAS.sub(M_TEXTO, texto)
    trocado = _URL.sub(M_URL, trocado)
    trocado = _EMAIL.sub(M_EMAIL, trocado)
    trocado = _HANDLE.sub(M_HANDLE, trocado)
    trocado = _DOMINIO.sub(M_URL, trocado)
    trocado = _TELEFONE.sub(M_TELEFONE, trocado)
    trocado = _NUMERO.sub(M_NUMERO, trocado)
    trocado = _nomes(trocado)
    trocado = re.sub(r"[ \t]{2,}", " ", trocado).strip()
    return None if _sobra_alguma_entidade(trocado) else trocado


__all__ = ["M_EMAIL", "M_HANDLE", "M_NOME", "M_NUMERO", "M_TELEFONE", "M_TEXTO", "M_URL", "remover_entidades"]
