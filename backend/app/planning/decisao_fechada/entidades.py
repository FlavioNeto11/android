"""Filtro SENSATO da C3 da intenção (item 31.9; ADR-069 itens 4 e 10): lista de BLOQUEIO sobre o piso do dono.

A C3 é o comando do dono depois de `sem_destinos` e de `redact`. Desde a emenda de 03/10 (ADR-069 item 10, decisão do dono
de ~00:15Z), dado pessoal pode ir ao Jev "desde que faça sentido no filtro": nome e `@handle` de pessoa não são vazamento.
O filtro mascara o que não ajuda a escolher a habilidade e deixa o resto (`remover_entidades`):

0. o texto é normalizado (`normalizar`: NFKC, sem marca combinante nem caractere invisível, todo traço como "-"): letra
   circulada, de largura cheia ou matemática vira a letra comum, e "s<ZWSP>enha" vira "senha", que a conferência da C7 do
   consumidor pega. Palavra que mistura alfabetos (o "а" cirílico em "senhа") RECUSA o texto: é o jeito de esconder C7;
1. o que tem forma conhecida vira marcador fixo: o que está entre aspas (`[texto]`: o que a pessoa manda escrever), link
   e domínio, e-mail, `@handle`, telefone, número e token de letras com `_` (`[termo]`: handle sem `@`, identificador).
   A aspa que abre e não fecha leva o resto do texto para `[texto]`. Símbolo (emoji, braille, letra em quadrado negativo)
   vira `[texto]`; colado entre duas letras, recusa ("s★enha" passaria pela conferência da C7);
2. o que esconde e-mail, telefone ou documento RECUSA o texto inteiro (`None`): endereço, documento (CPF, RG...), e-mail
   por extenso ou ofuscado (`arroba`, `(at)`, `{dot}`, `ponto com`), sobra de `@` ou `://` e DOIS ou mais numerais por
   extenso (telefone, documento ou PIN ditado). UM numeral vira `[numero]`;
3. o resto passa como está: palavra comum, nome de pessoa, nome de app.

Até a emenda, o passo 3 era uma lista de PERMISSÃO (palavra fora de um vocabulário fixo virava `[termo]`, nome com
maiúscula também) com recusa do texto inteiro acima de uma proporção de desconhecidas: a remoção que falha fechada da
primeira redação do ADR-069. Medido em 03/10 sobre os 90 comandos reais de 7 dias (só leitura): as 17 recusas que não eram
C7 vinham todas da proporção, e a lista apagava cerca de 8 palavras por comando no qa-messenger. O piso não mudou.

Credencial e 2FA (C7) NÃO são tratados aqui: o consumidor recusa o pedido inteiro antes (`intencao.py`).

Funções puras: sem banco, sem rede, sem configuração.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Final

#: Marcadores fixos, minúsculos e entre colchetes: nenhum é palavra do vocabulário, e a conferência os ignora.
M_URL: Final = "[link]"
M_EMAIL: Final = "[email]"
M_HANDLE: Final = "[usuario]"
M_TELEFONE: Final = "[telefone]"
M_NUMERO: Final = "[numero]"
M_TERMO: Final = "[termo]"
M_TEXTO: Final = "[texto]"

_MARCADORES: Final = (M_URL, M_EMAIL, M_HANDLE, M_TELEFONE, M_NUMERO, M_TERMO, M_TEXTO)

# ------------------------------------------------------------------ 1. troca por forma (do mais específico ao mais geral)
#: Aspas simples ASCII e crase só fora de palavra: o apóstrofo de "D'Ávila" não abre trecho. Depois do NFKC o "″" vira
#: "′′" e as aspas de largura cheia viram ASCII. Aspa que SOBRA (desbalanceada, de outro sistema, pontuação de abrir ou
#: fechar citação, colchete que não é ASCII) abre texto até o fim (`_primeira_aspa_sobrando`): na C3 o resto vira
#: `[texto]`; na C2, é cortado.
_ASPAS = re.compile(r'"[^"]*"|“[^”]*”|„[^“”]*[“”]|«[^»]*»|‹[^›]*›|‘[^’]*’|‚[^‘’]*[‘’]|「[^」]*」|『[^』]*』'
                    r"|〝[^〞〟]*[〞〟]|′+[^′]*′+|(?<!\w)'[^'\n]*'(?!\w)|`[^`]*`")
_CARACTERES_DE_ASPA: Final = frozenset("\"'`′″‴‵‶‷„‚“”‘’«»‹›「」『』〝〞〟❛❜❝❞")
_URL = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.\-]*://|www\.)\S+")
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)*")
_HANDLE = re.compile(r"@\w[\w.]*")
_DOMINIO = re.compile(r"(?i)\b[\w\-]+(?:\.[\w\-]+)*\.[a-z]{2,}(?:/\S*)?\b")
_TELEFONE = re.compile(r"(?:\+?\d{1,3}[\s.\-])?\(?\d{2}\)?[\s.\-]?\d{4,5}[\s.\-]?\d{4}")
#: Qualquer número (também quebrado por UM separador entre dígitos: "12 34 56", "1.234", "11-98"). Até "pedido 12" é
#: identificador; a contagem ("curta 25 posts") perde pouco como `[numero]`.
_NUMERO = re.compile(r"\d(?:[\s.\-/]?\d)*")
#: Três ou mais algarismos por extenso em sequência, também ligados por "e", "y" ou "and" ("nove nove oito sete", "um e
#: um e um"): é telefone ou documento ditado. Recusa. Pega também "um", "uma", "uno" e "dos", que `_NUMERAIS` não conta.
_ALGARISMO_FALADO: Final = (r"(?:zero|um|uma|dois|duas|tr[eê]s|quatro|cinco|seis|meia|sete|oito|nove|one|two|three|four"
                            r"|five|six|seven|eight|nine|cero|uno|una|dos|cuatro|siete|ocho|nueve)")
_DITADO = re.compile(rf"(?i)\b(?:{_ALGARISMO_FALADO}(?:\W+(?:e|y|and)\W+|\W+)){{2,}}{_ALGARISMO_FALADO}\b")

# ------------------------------------------------------------------ 2. recusa (não há máscara segura)
_RECUSA = re.compile(
    r"@|://|(?i:\bwww\b)|(?i:\barroba\b)|(?i:\bponto\s*com\b)|(?i:\bdot\s*com\b)"
    r"|(?i:[(\[{<]\s*(?:at|dot|arroba|ponto)\s*[)\]}>])"
    # endereço: o logradouro seguido de qualquer termo (o nome da rua é dado de lugar de uma pessoa)
    r"|(?i:\b(?:rua|r\.|avenida|av\.?|travessa|tv\.|alameda|al\.|pra[çc]a|rodovia|estrada|largo|viela|cep|bairro"
    r"|apartamento|apto|bloco|condom[ií]nio|quadra|lote|casa|vila|calle|plaza|paseo|carretera|barrio)\b)"
    r"|(?i:\b(?:street|st\.|avenue|ave\.|road|rd\.|lane|blvd|boulevard|zip)\b)"
    # documento: o número que vem junto (em algarismo ou por extenso) identifica a pessoa
    r"|(?i:\b(?:cpf|cnpj|rg|cnh|passaporte|passport|ssn|dni|nif)\b)")
_SOBRA_DIGITOS = re.compile(r"\d\D{0,2}\d\D{0,2}\d")
_PARENTESES_COM_DIGITO = re.compile(r"\(\s*\d")

#: Numerais por extenso (normalizados, sem acento). UM só vira `[numero]`; DOIS ou mais no texto, ligados por qualquer coisa
#: ("nove e oito", "nove oito, depois sete seis", "dez dez"), recusam: é telefone, documento ou PIN ditado
#: (reverificação do 31.9, 03/10). "um", "uma", "one", "uno" e "una" ficam de fora porque são artigo, e "dos" (espanhol)
#: porque em português é "de + os"; o `_DITADO` ainda pega "um um um" e "dos dos dos".
_NUMERAIS: Final[frozenset[str]] = frozenset("""
zero dois duas tres quatro cinco seis meia sete oito nove dez onze doze treze catorze quatorze quinze dezesseis dezasseis
dezessete dezassete dezoito dezenove dezanove vinte trinta quarenta cinquenta sessenta setenta oitenta noventa cem cento
duzentos duzentas trezentos trezentas quatrocentos quatrocentas quinhentos quinhentas seiscentos seiscentas setecentos
setecentas oitocentos oitocentas novecentos novecentas mil milhao milhoes bilhao bilhoes
two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen
twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion
cero cuatro siete ocho nueve diez once doce trece catorce quince dieciseis diecisiete dieciocho diecinueve veinte treinta
cuarenta cincuenta sesenta ochenta cien ciento
""".split())

# ------------------------------------------------------------------ 3. palavras
#: Palavra só de letras (com apóstrofo ou hífen dentro); `\w` sem dígito e sem `_`.
_PALAVRA = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*")
#: Só letras, sem apóstrofo nem hífen: a unidade da conferência de alfabetos.
_LETRAS = re.compile(r"[^\W\d_]+")
#: Token com letra E (dígito ou `_`): handle sem `@`, identificador. Os dígitos já viraram `[numero]` em `_formas`, então
#: na prática é a palavra com `_`. Vira `[termo]`.
_MISTO = re.compile(r"\b(?=\w*[^\W\d_])(?=\w*[\d_])\w+\b")



# ------------------------------------------------------------------ normalização (o passo 0)
#: Somem depois do NFKC: marca combinante que sobrou (zalgo, risco sobre a letra) e caractere invisível de formato (ZWSP,
#: ZWJ, hífen suave, controle de direção). Assim "j̶o̶a̶n̶a̶" e "a<ZWSP>na" viram uma palavra só, julgada inteira.
_SOMEM: Final = frozenset({"Mn", "Mc", "Me", "Cf"})
#: Pontuação que fica no texto (o resto que não é letra, algarismo nem espaço é símbolo). A aspa ASCII que sobrevive a
#: `_primeira_aspa_sobrando` é só o apóstrofo entre letras; os colchetes são dos marcadores.
_PONTUACAO: Final = frozenset("!#$%&()*+,-./:;<=>?@[\\]^_{|}~'’¡¿")


def sem_acento(texto: str) -> str:
    """Para comparar: casefold e sem acento (NFKD sem as marcas combinantes)."""
    decomposto = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in decomposto if not unicodedata.combining(c))


_chave = sem_acento


def normalizar(texto: str) -> str:
    """NFKC; sem marca combinante, caractere invisível de formato nem controle que não seja espaço; todo traço como "-" e
    separador de linha ou parágrafo como quebra de linha. Letra circulada, de largura cheia, matemática ou sobrescrita vira
    a letra comum. Idempotente."""
    saida: list[str] = []
    for c in unicodedata.normalize("NFKC", texto):
        cat = unicodedata.category(c)
        if cat in _SOMEM or (cat == "Cc" and not c.isspace()):
            continue
        saida.append("-" if cat == "Pd" else "\n" if cat in ("Zl", "Zp") else c)
    return unicodedata.normalize("NFKC", "".join(saida))


def _alfabeto(c: str) -> str:
    """O sistema de escrita da letra, pelo começo do nome Unicode. Hiragana, katakana e ideograma contam como um só: a
    escrita japonesa os mistura na mesma palavra."""
    primeiro = unicodedata.name(c, "?").split(" ", 1)[0]
    return "CJK" if primeiro in ("CJK", "HIRAGANA", "KATAKANA", "KATAKANA-HIRAGANA", "IDEOGRAPHIC") else primeiro


def mistura_alfabetos(texto: str) -> bool:
    """Alguma palavra mistura sistemas de escrita (o "а" cirílico em "senhа", "Јoana")? Homóglifo é o jeito de uma palavra
    passar por outra, e não há tabela de confusíveis na biblioteca padrão: quem chama RECUSA."""
    return any(len({_alfabeto(c) for c in m.group()}) > 1 for m in _LETRAS.finditer(texto))


# ------------------------------------------------------------------ peças das duas passadas
def _primeira_aspa_sobrando(texto: str) -> int | None:
    """A posição da primeira aspa que sobrou depois de `_ASPAS` (desbalanceada, de outro sistema, pontuação de abrir ou
    fechar citação, colchete que não é ASCII), ou `None`. O apóstrofo entre duas letras ("D'Ávila", "d’água") não conta."""
    for i, c in enumerate(texto):
        if c in "'’" and 0 < i < len(texto) - 1 and texto[i - 1].isalpha() and texto[i + 1].isalpha():
            continue
        cat = unicodedata.category(c)
        if (c in _CARACTERES_DE_ASPA or cat in ("Pi", "Pf") or (cat in ("Ps", "Pe") and not c.isascii())
                or "QUOTATION" in unicodedata.name(c, "")):
            return i
    return None


def _formas(texto: str) -> str:
    """Link, e-mail, `@handle`, domínio, telefone e número viram marcador, do mais específico ao mais geral."""
    texto = _URL.sub(M_URL, texto)
    texto = _EMAIL.sub(M_EMAIL, texto)
    texto = _HANDLE.sub(M_HANDLE, texto)
    texto = _DOMINIO.sub(M_URL, texto)
    texto = _TELEFONE.sub(M_TELEFONE, texto)
    return _NUMERO.sub(M_NUMERO, texto)


def _e_simbolo(c: str) -> bool:
    return not (c.isalpha() or c.isdigit() or c.isspace() or c in _PONTUACAO)


def _simbolos(texto: str, *, recusar_colado: bool) -> str | None:
    """Cada sequência de símbolos (emoji, indicador regional, braille, letra em quadrado negativo, pontuação que não é
    ASCII) vira `[texto]`. Com `recusar_colado`, `None` se a sequência está entre duas letras ("jo❤ana"): do outro lado,
    a palavra seria lida inteira."""
    partes: list[str] = []
    i, n = 0, len(texto)
    while i < n:
        if not _e_simbolo(texto[i]):
            partes.append(texto[i])
            i += 1
            continue
        j = i
        while j < n and _e_simbolo(texto[j]):
            j += 1
        if recusar_colado and 0 < i and j < n and texto[i - 1].isalpha() and texto[j].isalpha():
            return None
        partes.append(M_TEXTO)
        i = j
    return "".join(partes)


def _sem_marcadores(texto: str) -> str:
    for m in _MARCADORES:
        texto = texto.replace(m, " ")
    return texto


def _recusa(resto: str) -> bool:
    """O que sobrou sem os marcadores ainda tem forma que não se mascara com segurança?"""
    return bool(_RECUSA.search(resto) or _DITADO.search(resto) or _SOBRA_DIGITOS.search(resto)
                or _PARENTESES_COM_DIGITO.search(resto))


def _limpar(texto: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", texto).strip()


def _numerais(texto: str) -> tuple[str, int]:
    """Cada numeral por extenso vira `[numero]`; devolve o texto e quantos eram."""
    quantos = 0

    def _troca(m: re.Match[str]) -> str:
        nonlocal quantos
        if _chave(m.group()) not in _NUMERAIS:
            return m.group()
        quantos += 1
        return M_NUMERO

    return _PALAVRA.sub(_troca, texto), quantos


# ------------------------------------------------------------------ a C3: o comando
def remover_entidades(texto: str) -> str | None:
    """O comando com o piso aplicado: forma conhecida vira marcador, e o que esconde e-mail, telefone ou documento recusa
    (`None`). Nome e palavra comum passam como estão (ADR-069 item 10).

    Devolve `None` quando: a entrada não é texto; alguma palavra mistura alfabetos; sobra símbolo colado entre letras;
    sobra forma que não se mascara com segurança (endereço, documento, e-mail por extenso ou ofuscado, `@`, `://`); ou há
    dois ou mais numerais por extenso. Idempotente."""
    if not isinstance(texto, str):
        return None
    texto = normalizar(texto)
    if mistura_alfabetos(texto):
        return None
    trocado = _ASPAS.sub(M_TEXTO, texto)
    sobra = _primeira_aspa_sobrando(trocado)
    if sobra is not None:                        # a aspa que abre e não fecha: o resto é o texto a escrever
        trocado = f"{trocado[:sobra].rstrip()} {M_TEXTO}"
    trocado = _simbolos(_formas(trocado), recusar_colado=True)
    if trocado is None or _recusa(_sem_marcadores(trocado)):
        return None
    trocado, numerais = _numerais(_MISTO.sub(M_TERMO, trocado))
    if numerais >= 2:
        return None
    return _limpar(trocado)


# ------------------------------------------------------------------ a C2: o texto do catálogo nas opções da R2
def mascarar_catalogo(texto: str) -> str:
    """O texto do catálogo do dono (C2: nome e descrição de habilidade ou fluxo) para ir como descrição de opção da R2.

    O piso da C3 sem recusa (a opção precisa existir): as máscaras de forma (aspas, link, e-mail, handle, domínio,
    telefone, número, símbolo e token com `_`), o trecho a partir de uma aspa que sobra cortado (a legenda que o corte em
    120 caracteres deixou aberta) e numeral como `[numero]`. Nome passa (ADR-069 item 10). Texto com alfabetos misturados,
    endereço, documento ou e-mail por extenso sai vazio: quem chama põe um texto fixo."""
    if not isinstance(texto, str):
        return ""
    t = normalizar(texto)
    if mistura_alfabetos(t):
        return ""
    t = _ASPAS.sub(M_TEXTO, t)
    corte = _primeira_aspa_sobrando(t)
    if corte is not None:
        t = t[:corte]
    t = _simbolos(_formas(t), recusar_colado=False) or ""
    if _recusa(_sem_marcadores(t)):
        return ""
    return _limpar(_numerais(_MISTO.sub(M_TERMO, t))[0])


__all__ = ["M_EMAIL", "M_HANDLE", "M_NUMERO", "M_TELEFONE", "M_TERMO", "M_TEXTO", "M_URL", "mascarar_catalogo",
           "mistura_alfabetos", "normalizar", "remover_entidades", "sem_acento"]
