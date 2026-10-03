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
2. o que esconde e-mail, telefone ou documento RECUSA o texto inteiro (`None`): endereço, documento (CPF, RG, cartão...),
   e-mail por extenso ou ofuscado (`arroba`, `(at)`, `(a)`, `{dot}`, `ponto com`, `correio ponto net`, `arr0ba`,
   `a r r o b a`), sobra de `@` ou `://` e DOIS ou mais numerais por extenso (telefone, documento ou PIN ditado). UM numeral
   vira `[numero]`;
3. o resto passa como está: palavra comum, nome de pessoa, nome de app.

Desde a reverificação B do 31.9 (03/10, NO-GO em 97f35fac) são DUAS passadas. A recusa por forma escondida roda primeiro,
sobre o texto normalizado e ainda sem máscara (`_RECUSA_NO_ORIGINAL`): mascarar antes apagava o que ela precisava ver (o
`742` do endereço em inglês, o `0` de `arr0ba`). Depois vêm as máscaras, com o token de letras E dígitos (`_MISTO`) ANTES do
número: com o número primeiro, `limao77` virava `limao[numero]` e a palavra da senha saía inteira (15 dos 22 vazamentos de
C7). `remover_entidades_com_motivo` diz POR QUE recusou (`MotivoDoFiltro`), para a linha da sombra.

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
from typing import Final, Literal

#: Marcadores fixos, minúsculos e entre colchetes: nenhum é palavra do vocabulário, e a conferência os ignora.
M_URL: Final = "[link]"
M_EMAIL: Final = "[email]"
M_HANDLE: Final = "[usuario]"
M_TELEFONE: Final = "[telefone]"
M_NUMERO: Final = "[numero]"
M_TERMO: Final = "[termo]"
M_TEXTO: Final = "[texto]"

_MARCADORES: Final = (M_URL, M_EMAIL, M_HANDLE, M_TELEFONE, M_NUMERO, M_TERMO, M_TEXTO)

#: Por que o filtro recusou a C3 (`decisao_fechada_sombra.motivo_privacidade`, migração 079). Vocabulário fechado.
MotivoDoFiltro = Literal["nao_texto", "vazio", "alfabetos", "simbolo_colado", "email_ofuscado", "endereco", "documento",
                         "ditado", "numerais", "sobra_de_forma"]

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
_RECUSA_EMAIL = re.compile(
    r"@|://|(?i:\bwww\b)|(?i:\barroba\b)|(?i:\bponto\s*com\b)|(?i:\bdot\s*com\b)"
    r"|(?i:[(\[{<]\s*(?:at|dot|arroba|ponto)\s*[)\]}>])")
#: Endereço: o logradouro seguido de qualquer termo (o nome da rua é dado de lugar de uma pessoa).
_RECUSA_ENDERECO = re.compile(
    r"(?i:\b(?:rua|r\.|avenida|av\.?|travessa|tv\.|alameda|al\.|pra[çc]a|rodovia|estrada|largo|viela|cep|bairro"
    r"|apartamento|apto|bloco|condom[ií]nio|quadra|lote|casa|vila|calle|plaza|paseo|carretera|barrio)\b)"
    r"|(?i:\b(?:street|st\.|avenue|ave\.|road|rd\.|lane|blvd|boulevard|zip)\b)")
#: Documento: o número que vem junto (em algarismo ou por extenso) identifica a pessoa.
_RECUSA_DOCUMENTO = re.compile(r"(?i:\b(?:cpf|cnpj|rg|cnh|passaporte|passport|ssn|dni|nif)\b)")
_SOBRA_DIGITOS = re.compile(r"\d\D{0,2}\d\D{0,2}\d")

#: Domínio de topo do e-mail soletrado ou ofuscado ("correio ponto net", "dot co", "punto org").
_TLD: Final = (r"(?:com|net|org|br|io|co|gov|edu|info|biz|me|app|dev|test|pt|es|uk|us|de|fr|it|mx|ar|cl|uy|rf|online"
               r"|site)")
#: A recusa que roda ANTES das máscaras (passada 1), sobre o texto sem acento, em casefold e com o algarismo trocado pela
#: letra parecida dentro de palavra (`arr0ba`). (motivo, padrão), na ordem do diagnóstico.
_RECUSA_NO_ORIGINAL: Final[tuple[tuple[MotivoDoFiltro, re.Pattern[str]], ...]] = (
    ("email_ofuscado", re.compile(
        # arroba por extenso, soletrada ou hifenizada ("a r r o b a", "a-r-r-o-b-a"); "(a)", "(a t)", "at-sign"
        r"(?<![^\W\d_])a[\W_]{0,2}r[\W_]{0,2}r[\W_]{0,2}o[\W_]{0,2}b[\W_]{0,2}a(?![^\W\d_])"
        r"|[(\[{<]\s*a\s*t?\s*[)\]}>]|\bat[\s\-]?sign\b"
        # domínio de topo depois de "ponto", "dot" ou "punto": "correio ponto net", "dot co"
        rf"|\b(?:ponto|dot|punto)\s*{_TLD}\b"
        # "marina at correio.net", "zilda at correio dot org"
        rf"|\b\w+\s+at\s+\w+(?:\s*(?:\.|ponto|dot|punto)\s*\w+)*\s*(?:\.|ponto|dot|punto)\s*{_TLD}\b"
        # `@` sem a parte local colada (separada por espaço ou tabulação), seguido de domínio com topo
        rf"|(?<![\w.+\-])@[\w\-]+(?:\.[\w\-]+)*\.{_TLD}\b")),
    ("endereco", re.compile(r"\bcaixa\s+postal\b|\bp\.?\s*o\.?\s+box\b|\bapartado\s+postal\b")),
    # cartão e CVV só com o número perto ("o cartão de visita do perfil" passa); título de eleitor e "ce pe efe" sempre
    ("documento", re.compile(r"\b(?:cartao|card|cvv|cvc)\b\D{0,20}\d{3}|\btitulo\s+de\s+eleitor\b|\bce\s+pe\s+efe\b")),
)
#: Endereço em inglês: número, uma a três palavras com maiúscula e o tipo de logradouro, no texto SEM casefold ("on the
#: way" não é endereço; "742 Evergreen Terrace" é).
_ENDERECO_EN: Final = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][\w'\-]*\s+){1,3}(?:Terrace|Drive|Court|Place|Way|Circle|Parkway|Highway|Square|Lane|Road"
    r"|Street|Avenue|Boulevard|Blvd|Ave|St|Rd|Dr|Ct|Pl|Ln)\b")
#: O algarismo que faz as vezes de letra dentro de uma palavra (leet). Só na passada 1 e na conferência da C7.
_LEET: Final = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "$": "s"})
_TOKEN_COM_LETRA: Final = re.compile(r"[\w$]*[^\W\d_][\w$]*")


def sem_leet(texto: str) -> str:
    """Troca algarismo por letra só nas palavras que já têm letra (`arr0ba`, `s3nh4`, `pa$$word`); número sozinho fica."""
    return _TOKEN_COM_LETRA.sub(lambda m: m.group().translate(_LEET), texto)
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
#: Token com letra E (dígito ou `_`), também ligado por hífen: handle sem `@`, identificador, a senha `limao77`, `kiwi-77`,
#: `lagoa-azul-3`. Vira `[termo]` INTEIRO; roda antes de `_NUMERO` (com o número primeiro, sobrava `limao[numero]`).
_MISTO = re.compile(r"(?<![\w\-])(?=[\w\-]*[^\W\d_])(?=[\w\-]*[\d_])\w+(?:-\w+)*(?![\w\-])")



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
    """Link, e-mail, `@handle`, domínio, telefone, token misto e número viram marcador, do mais específico ao mais geral."""
    texto = _URL.sub(M_URL, texto)
    texto = _EMAIL.sub(M_EMAIL, texto)
    texto = _HANDLE.sub(M_HANDLE, texto)
    texto = _DOMINIO.sub(M_URL, texto)
    texto = _TELEFONE.sub(M_TELEFONE, texto)
    texto = _MISTO.sub(M_TERMO, texto)
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


def _recusa_no_original(texto: str) -> MotivoDoFiltro | None:
    """Passada 1: a forma escondida que a máscara apagaria (e-mail ofuscado, caixa postal, cartão, endereço em inglês)."""
    plano = sem_leet(sem_acento(texto))
    for motivo, padrao in _RECUSA_NO_ORIGINAL:
        if padrao.search(plano):
            return motivo
    return "endereco" if _ENDERECO_EN.search(texto) else None


def _recusa(resto: str) -> MotivoDoFiltro | None:
    """O que sobrou sem os marcadores ainda tem forma que não se mascara com segurança? O motivo, ou `None`."""
    if _RECUSA_EMAIL.search(resto):
        return "email_ofuscado"
    if _RECUSA_ENDERECO.search(resto):
        return "endereco"
    if _RECUSA_DOCUMENTO.search(resto):
        return "documento"
    if _DITADO.search(resto):
        return "ditado"
    if _SOBRA_DIGITOS.search(resto) or _PARENTESES_COM_DIGITO.search(resto):
        return "sobra_de_forma"
    return None


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
    (`None`). Nome e palavra comum passam como estão (ADR-069 item 10). O motivo da recusa: `remover_entidades_com_motivo`.

    Devolve `None` quando: a entrada não é texto; alguma palavra mistura alfabetos; há forma escondida no texto ainda sem
    máscara (e-mail ofuscado, caixa postal, cartão, endereço em inglês); sobra símbolo colado entre letras; sobra forma que
    não se mascara com segurança (endereço, documento, e-mail por extenso, `@`, `://`); ou há dois ou mais numerais por
    extenso. Idempotente."""
    return remover_entidades_com_motivo(texto)[0]


def remover_entidades_com_motivo(texto: object) -> tuple[str | None, MotivoDoFiltro | None]:
    """`(comando limpo, None)` ou `(None, motivo)`. O texto que sobra vazio também é recusa (`vazio`)."""
    if not isinstance(texto, str):
        return None, "nao_texto"
    texto = normalizar(texto)
    if mistura_alfabetos(texto):
        return None, "alfabetos"
    trocado = _ASPAS.sub(M_TEXTO, texto)
    sobra = _primeira_aspa_sobrando(trocado)
    if sobra is not None:                        # a aspa que abre e não fecha: o resto é o texto a escrever
        trocado = f"{trocado[:sobra].rstrip()} {M_TEXTO}"
    # Passada 1, antes de qualquer máscara. O que estava entre aspas já é `[texto]`: é o que a pessoa manda ESCREVER.
    if (motivo := _recusa_no_original(trocado)) is not None:
        return None, motivo
    marcado = _simbolos(_formas(trocado), recusar_colado=True)
    if marcado is None:
        return None, "simbolo_colado"
    if (motivo := _recusa(_sem_marcadores(marcado))) is not None:
        return None, motivo
    marcado, numerais = _numerais(marcado)
    if numerais >= 2:
        return None, "numerais"
    limpo = _limpar(marcado)
    return (limpo, None) if limpo else (None, "vazio")


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
    if _recusa_no_original(t) is not None:
        return ""
    t = _simbolos(_formas(t), recusar_colado=False) or ""
    if _recusa(_sem_marcadores(t)) is not None:
        return ""
    return _limpar(_numerais(t)[0])


__all__ = ["M_EMAIL", "M_HANDLE", "M_NUMERO", "M_TELEFONE", "M_TERMO", "M_TEXTO", "M_URL", "MotivoDoFiltro",
           "mascarar_catalogo", "mistura_alfabetos", "normalizar", "remover_entidades", "remover_entidades_com_motivo",
           "sem_acento", "sem_leet"]
