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

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_ARROBA = re.compile(r"(?<![\w@])@[\w.]{2,}")
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
#: Oito dígitos ou mais com separadores de telefone: ano, hora e valor em US$ não chegam a isso.
_TELEFONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)")


def _sem_acento_minusculo(t: str) -> str:
    # Um caractere por caractere (NFD sem as marcas), para as posições do texto original continuarem valendo.
    return "".join(unicodedata.normalize("NFD", ch.lower())[0] for ch in t)


def _alvos(nomes: Iterable[str], minimo: int, poupar_ana: bool) -> list[str]:
    limpos = {n.strip().lstrip("@") for n in nomes if n}
    return sorted({n for n in limpos if len(n) >= minimo
                   and not (poupar_ana and _sem_acento_minusculo(n) == "ana")}, key=len, reverse=True)


def _trocas(texto: str, alvos: list[str]) -> list[tuple[int, int]]:
    base = _sem_acento_minusculo(texto)
    trocas: list[tuple[int, int]] = []
    for nome in alvos:
        for m in re.finditer(rf"(?<![\w@])@?{re.escape(_sem_acento_minusculo(nome))}(?!\w)", base):
            if not any(a < m.end() and m.start() < b for a, b in trocas):
                trocas.append((m.start(), m.end()))
    return trocas


def sem_nome_de_persona(texto: str, nomes: Iterable[str], *, minimo: int = 3, poupar_ana: bool = True) -> str:
    """Troca nome, primeiro nome e @ de persona cadastrada por `PERSONA_OCULTA`, palavra inteira e sem diferença de
    maiúscula ou acento. O padrão é o da conversa (28.28): a partir de 3 letras, e "ANA" poupada, porque é o nome da IA
    da Central (decisão do dono, 03/10) e uma persona chamada Ana não apaga a ANA das respostas. O aviso usa
    `minimo=2, poupar_ana=False`: o texto dele é do fato, não da IA."""
    alvos = _alvos(nomes, minimo, poupar_ana)
    if not alvos:
        return texto
    for a, b in sorted(_trocas(texto, alvos), reverse=True):
        texto = texto[:a] + PERSONA_OCULTA + texto[b:]
    return texto


def menciona_persona(texto: str, nomes: Iterable[str]) -> bool:
    """A régua estrita: algum nome de persona (2 letras ou mais, "ana" inclusive) aparece como palavra inteira."""
    alvos = _alvos(nomes, 2, False)
    return bool(alvos) and bool(_trocas(texto, alvos))


def sem_contato(texto: str) -> str:
    """E-mail, @ de conta, IP e número de telefone viram `CONTATO_OCULTO`. O e-mail vem antes do @ (o @ do e-mail não é
    conta) e o IP antes do telefone (quatro grupos de dígitos)."""
    for padrao in (_EMAIL, _ARROBA, _IP, _TELEFONE):
        texto = padrao.sub(CONTATO_OCULTO, texto)
    return texto


def texto_livre(texto: str, nomes: Iterable[str], redigir: Callable[[str], str]) -> str:
    """O texto livre de um aviso (a pergunta da execução, o resumo da aprovação): redigido, sem contato e sem persona
    pela régua estrita. Corta o que é sensível e deixa o resto legível."""
    return sem_nome_de_persona(sem_contato(redigir(texto)), nomes, minimo=2, poupar_ana=False)


def texto_seguro(texto: str | None, nomes: Iterable[str], redigir: Callable[[str], str] | None) -> str | None:
    """O texto como está, ou `None` se o redator, o filtro de contato ou a régua estrita de persona mudariam qualquer
    coisa nele. Sem redator, ou com erro em qualquer filtro, também `None`: na dúvida, a reserva."""
    if not texto or redigir is None:
        return None
    try:
        limpo = texto.strip()
        if not limpo or redigir(limpo).strip() != limpo or sem_contato(limpo) != limpo:
            return None
        return None if menciona_persona(limpo, nomes) else limpo
    except Exception:  # noqa: BLE001 - filtro com erro nunca deixa o texto cru sair
        return None
