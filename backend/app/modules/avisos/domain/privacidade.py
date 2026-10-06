"""O que nunca sai pelos canais de fora (regra do dono: nome de persona, conta, e-mail, telefone, IP; C-02 e 28.31).

Domínio puro: os nomes de persona chegam como lista de texto (a porta `nomes_de_persona` os lê do banco) e o redator de
credencial chega como função. Três ferramentas, de rigor crescente:

    sem_nome_de_persona  troca o nome de persona por `PERSONA_OCULTA` (28.28; as respostas da conversa).
    sem_contato          troca e-mail, @, telefone e IP por marcadores (o texto livre que um aviso carrega).
    texto_seguro         o texto INTEIRO ou nada: devolve `None` se qualquer filtro mudaria algo. É o do rótulo do
                         pedido e da manchete (decisão (a) da orquestradora, 04/10 19:22Z): um rótulo com um nome
                         trocado no meio ainda diz demais, então vira a reserva ("pedido #…").

O rótulo usa a régua ESTRITA (`menciona_persona`): nome de 2 letras conta e "ana" também. A troca branda da conversa
poupa a "ANA" porque ela é o nome da IA nas respostas; num rótulo escrito pela pessoa, uma persona chamada Ana é Ana.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable

#: No lugar de um nome de persona em texto que sai pelo canal (28.28; regra C-02: nome de persona não vai ao canal).
PERSONA_OCULTA = "<persona>"
CONTATO_OCULTO = "<contato>"
#: No lugar de um DADO da persona (cidade, empregador, profissão…: o valor de um marcador `{perfil_*}` da biografia) num
#: texto de EVENTO que sai pelo canal (31.87 F2). Marcador próprio: não é nome, e a conversa nunca o usa.
DADO_OCULTO = "<dado da persona>"


class DadoDaPersona(str):
    """Um valor de dado da persona na lista de `nomes` do filtro: é trocado por `DADO_OCULTO`, e não por `PERSONA_OCULTA`.
    Só o caminho de EVENTO (aviso e pergunta que o Telegram carrega) o recebe; a resposta composta pela ANA e o eco do
    Trello usam só nomes, porque o dado da persona é texto comum ("São Paulo", "música") e encheria a conversa."""

    __slots__ = ()

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_ARROBA = re.compile(r"(?<![\w@])@[\w.]{2,}")
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
#: Candidato a telefone: dígitos com separadores de telefone. Quem decide é `_e_telefone`.
_TELEFONE = re.compile(r"(?<!\w)[+(]?\d[\d\s().-]{6,}\d(?!\w)")
_DATA = re.compile(r"\d{4}-\d{2}-\d{2}")


_FIXO = re.compile(r"\d{4}-\d{4}")
#: No lugar de cada caractere de uma data ISO enquanto o telefone é procurado: não é dígito nem separador de telefone.
_MASCARA = "\x00"


def _e_telefone(trecho: str) -> bool:
    """Telefone tem 9 dígitos ou mais ("98888-7777"), ou começa com "+" ou "(" e tem 8 ou mais, ou é "dddd-dddd" (fixo
    de 8 dígitos; decisão da orquestradora, 04/10 20:06Z). Hora, versão e o número de item ("28.12-05") ficam: são o que
    diz QUAL pedido é. A data ISO nem chega aqui: `sem_contato` a mascara antes."""
    digitos = sum(ch.isdigit() for ch in trecho)
    return digitos >= 9 or (trecho[0] in "+(" and digitos >= 8) or bool(_FIXO.fullmatch(trecho))


def _sem_acento_minusculo(t: str) -> str:
    # Um caractere por caractere (NFD sem as marcas), para as posições do texto original continuarem valendo.
    return "".join(unicodedata.normalize("NFD", ch.lower())[0] for ch in t)


def _alvos(nomes: Iterable[str], minimo: int, poupar_ana: bool) -> list[str]:
    limpos = {n.strip().lstrip("@") for n in nomes if n}
    return sorted({n for n in limpos if len(n) >= minimo
                   and not (poupar_ana and _sem_acento_minusculo(n) == "ana")}, key=len, reverse=True)


def _dados(nomes: Iterable[str]) -> frozenset[str]:
    """Os alvos (já limpos, como `_alvos` os deixa) que são DADO da persona e não nome. Se o mesmo texto também é um nome,
    o nome vence: sai como `PERSONA_OCULTA`."""
    limpos = [(n.strip().lstrip("@"), isinstance(n, DadoDaPersona)) for n in nomes if n]
    nomes_puros = {t for t, dado in limpos if not dado}
    return frozenset(t for t, dado in limpos if dado and t not in nomes_puros)


def _trocas(texto: str, alvos: list[str]) -> list[tuple[int, int, str]]:
    base = _sem_acento_minusculo(texto)
    trocas: list[tuple[int, int, str]] = []
    for nome in alvos:
        for m in re.finditer(rf"(?<![\w@])@?{re.escape(_sem_acento_minusculo(nome))}(?!\w)", base):
            if not any(a < m.end() and m.start() < b for a, b, _n in trocas):
                trocas.append((m.start(), m.end(), nome))
    return trocas


def sem_nome_de_persona(texto: str, nomes: Iterable[str], *, minimo: int = 3, poupar_ana: bool = True) -> str:
    """Troca nome, primeiro nome e @ de persona cadastrada por `PERSONA_OCULTA`, palavra inteira e sem diferença de
    maiúscula ou acento. O padrão é o da conversa (28.28): a partir de 3 letras, e "ANA" poupada, porque é o nome da IA
    da Central (decisão do dono, 03/10) e uma persona chamada Ana não apaga a ANA das respostas. O aviso usa
    `minimo=2, poupar_ana=False`: o texto dele é do fato, não da IA."""
    nomes = list(nomes)
    alvos = _alvos(nomes, minimo, poupar_ana)
    if not alvos:
        return texto
    dados = _dados(nomes)
    for a, b, alvo in sorted(_trocas(texto, alvos), reverse=True):
        texto = texto[:a] + (DADO_OCULTO if alvo in dados else PERSONA_OCULTA) + texto[b:]
    return texto


def menciona_persona(texto: str, nomes: Iterable[str]) -> bool:
    """A régua estrita: algum nome de persona (2 letras ou mais, "ana" inclusive) aparece como palavra inteira."""
    alvos = _alvos(nomes, 2, False)
    return bool(alvos) and bool(_trocas(texto, alvos))


def sem_contato(texto: str) -> str:
    """E-mail, @ de conta, IP e número de telefone viram `CONTATO_OCULTO`. O e-mail vem antes do @ (o @ do e-mail não é
    conta) e o IP antes do telefone (quatro grupos de dígitos)."""
    for padrao in (_EMAIL, _ARROBA, _IP):
        texto = padrao.sub(CONTATO_OCULTO, texto)
    # A data ISO fica (diz qual pedido é), mas não pode esconder o telefone colado nela ("2026-10-05 11 91234-5678",
    # revisão do #310): procura o telefone no texto com a data mascarada, do mesmo tamanho, e troca no original.
    mascarado = _DATA.sub(lambda m: _MASCARA * len(m.group(0)), texto)
    trocas = [(m.start(), m.end()) for m in _TELEFONE.finditer(mascarado) if _e_telefone(m.group(0))]
    for a, b in reversed(trocas):
        texto = texto[:a] + CONTATO_OCULTO + texto[b:]
    return texto


def texto_livre(texto: str, nomes: Iterable[str], redigir: Callable[[str], str]) -> str:
    """O texto livre de um aviso (a pergunta da execução, o resumo da aprovação): redigido, sem contato e sem persona
    pela régua estrita. Corta o que é sensível e deixa o resto legível."""
    return sem_nome_de_persona(sem_contato(redigir(texto)), nomes, minimo=2, poupar_ana=False)


#: Link em texto livre: o texto inteiro fica de fora (`texto_seguro`).
_LINK = re.compile(r"https?://|\bwww\.", re.IGNORECASE)


def texto_seguro(texto: str | None, nomes: Iterable[str], redigir: Callable[[str], str] | None) -> str | None:
    """O texto como está, ou `None` se o redator, o filtro de contato ou a régua estrita de persona mudariam qualquer
    coisa nele, ou se ele tem um link (o endereço de um perfil diz de quem se trata; revisão da #314). Sem redator, ou com
    erro em qualquer filtro, também `None`: na dúvida, a reserva."""
    if not texto or redigir is None:
        return None
    try:
        limpo = texto.strip()
        if not limpo or _LINK.search(limpo) or redigir(limpo).strip() != limpo or sem_contato(limpo) != limpo:
            return None
        return None if menciona_persona(limpo, nomes) else limpo
    except Exception:  # noqa: BLE001 - filtro com erro nunca deixa o texto cru sair
        return None
