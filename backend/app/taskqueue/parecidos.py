"""31.89 F5: "este comando parece com o fluxo tal". Puro: sem banco, sem relógio, sem IA.

Só PERGUNTA. O casamento de verdade (`FlowStore.match`) segue exato e não muda; aqui se compara o comando com o texto
FIXO de cada molde (o que sobra tirando os `{nome}`), sem acento, sem caixa, sem pontuação e sem artigo ou preposição.
Um fluxo "parece" quando as palavras fixas do molde aparecem no comando, na mesma ordem, com pequenas variações
("curta" para "curtir"). Nunca executa: quem decide é a pessoa."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

PLACEHOLDER = re.compile(r"\{(\w+)\}")

#: Artigos, preposições e pronomes que não carregam o sentido do comando ("o meu", "no", "da"...).
PALAVRAS_VAZIAS = frozenset({"o", "a", "os", "as", "um", "uma", "uns", "umas", "de", "do", "da", "dos", "das", "no", "na",
                             "nos", "nas", "em", "para", "pra", "pro", "com", "e", "meu", "minha", "meus", "minhas",
                             "por", "ao", "aos", "à", "às", "que"})

#: Quantas palavras fixas o molde precisa ter para ser comparado: com uma só ("abrir {app}") quase todo comando "parece".
MINIMO_DE_PALAVRAS = 2

#: Quanto cada palavra fixa precisa se parecer com uma do comando (razão de `difflib`, ou prefixo comum de 4 letras).
PARECIDA_A_PARTIR_DE = 0.8

#: Nota mínima (a média da semelhança das palavras fixas) para o molde entrar na lista. Medida só leitura no banco do
#: central em 05/10 (32 moldes ativos, 119 comandos dos últimos 30 dias que nenhum molde casa por inteiro): com 0,85
#: 13 comandos ganhavam sugestão (até 4 por comando), com 0,9 foram 9 (até 2) e com 1,0 foram 7. Fica 0,9: aceita
#: "curta"/"curtir" e não enche a lista.
LIMIAR = 0.9

#: Quantos fluxos a prévia mostra.
MAXIMO = 3


@dataclass(frozen=True, slots=True)
class Parecido:
    indice: int            # a posição do molde na lista que veio de quem chamou
    nota: float            # 0..1: a média da semelhança das palavras fixas
    palavras: int          # quantas palavras fixas o molde tem (desempate: mais específico primeiro)


def palavras(texto: str, *, so_fixo: bool) -> list[str]:
    """As palavras do texto sem acento, caixa, pontuação e as vazias; `so_fixo` tira antes os `{nome}`."""
    if so_fixo:
        texto = PLACEHOLDER.sub(" ", texto)
    base = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", texto))
    base = "".join(c for c in base if not unicodedata.combining(c)).casefold()
    return [p for p in re.findall(r"[a-z0-9]+", base) if p not in PALAVRAS_VAZIAS]


def _semelhanca(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4]:
        return max(0.9, SequenceMatcher(None, a, b).ratio())          # "curta"/"curtir", "mande"/"mandar"
    return SequenceMatcher(None, a, b).ratio()


def nota_do_molde(fixas: list[str], comando: list[str]) -> float:
    """A média da semelhança, palavra fixa a palavra fixa, procurando cada uma DEPOIS da anterior no comando. Uma que
    não se acha (ou só se parece menos que `PARECIDA_A_PARTIR_DE`) vale 0."""
    total, a_partir_de = 0.0, 0
    for fixa in fixas:
        melhor, onde = 0.0, -1
        for i in range(a_partir_de, len(comando)):
            s = _semelhanca(fixa, comando[i])
            if s > melhor:
                melhor, onde = s, i
        if melhor >= PARECIDA_A_PARTIR_DE:
            total += melhor
            a_partir_de = onde + 1
    return total / len(fixas) if fixas else 0.0


def parecidos(comando: str, moldes: list[str], *, limiar: float = LIMIAR, maximo: int = MAXIMO) -> list[Parecido]:
    """Os moldes que o comando parece, do mais parecido ao menos (nota, depois mais palavras fixas)."""
    dele = palavras(comando, so_fixo=False)
    achados: list[Parecido] = []
    for i, molde in enumerate(moldes):
        fixas = palavras(molde, so_fixo=True)
        if len(fixas) < MINIMO_DE_PALAVRAS:
            continue
        nota = nota_do_molde(fixas, dele)
        if nota >= limiar:
            achados.append(Parecido(i, round(nota, 3), len(fixas)))
    achados.sort(key=lambda p: (-p.nota, -p.palavras, p.indice))
    return achados[:maximo]
