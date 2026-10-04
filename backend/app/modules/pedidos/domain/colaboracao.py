"""Colaboração entre pedidos, a ESTRUTURA (item 28.10, fatia F1; `docs/design/pedidos-persistentes.md` §9).

Um pedido pai com sub-pedidos (`pai_id`), dependências entre eles (`pedido_dependencias(de, para, tipo)`, migração 096)
e um papel opcional por pedido. Este módulo decide só se a ESTRUTURA é válida; quem grava e quem lê é a API
(`infrastructure/servico.py`). O laço de ocorrências NÃO olha para nada daqui na F1: dependência que segura a ocorrência
é a F2, papel que limita a autonomia é a F3.

As recusas voltam como `Recusa(codigo, mensagem, campo)`, com o mesmo formato do `Bloqueio` da prévia: a prévia mostra o
que a criação devolveria (422 com `code`). A ordem das conferências é a ordem em que a pessoa corrige: primeiro o pai,
depois o tamanho da árvore, depois as dependências, por último o dinheiro.

Direção da dependência: `(de, para)` quer dizer "`para` depende de `de`". A seta vai de quem vem antes para quem espera.

Puro: stdlib. Sem banco, sem relógio.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

#: O que cada pedido da família faz (§9). `porta_voz` é o único que age para fora, e só há um por família.
PAPEIS: tuple[str, ...] = ("pesquisador", "checador", "redator", "porta_voz")
PORTA_VOZ = "porta_voz"
#: `precisa_de_resultado`: o `para` usa o que o `de` produziu. `depois_de`: só a ordem importa.
TIPOS_DE_DEPENDENCIA: tuple[str, ...] = ("precisa_de_resultado", "depois_de")
#: Estados em que o pedido não volta a andar (os mesmos de `servico.TERMINAIS` e do §6.2).
ESTADOS_TERMINAIS: tuple[str, ...] = ("concluido", "encerrado", "cancelado")
#: Erro de ponto flutuante tolerado ao somar US$ (a soma de centavos nunca estoura por 1e-9).
_FOLGA_USD = 1e-9

Aresta = tuple[str, str]       # (de, para): `para` depende de `de`


@dataclass(frozen=True)
class Recusa:
    """Por que a estrutura é recusada. `codigo` é o `code` do 422."""
    codigo: str
    mensagem: str
    campo: str | None = None


@dataclass(frozen=True)
class DadosDoPai:
    """O que a validação precisa saber do pai, já lido do banco (o domínio não lê nada)."""
    id: str
    estado: str
    pai_id: str | None
    orcamento_total_usd: float | None
    gasto_usd: float                                 # o que o PRÓPRIO pai já gastou (ocorrências dele, não dos filhos)


@dataclass(frozen=True)
class Irmao:
    """Um filho que o pai já tem."""
    id: str
    papel: str | None
    orcamento_total_usd: float | None


@dataclass(frozen=True)
class Limites:
    max_profundidade: int = 2
    max_filhos: int = 5


# ------------------------------------------------------------------ profundidade e linhagem
def profundidade(cadeia_de_pais: Sequence[str]) -> int:
    """Quantos níveis tem o pedido cuja cadeia de ancestrais é `cadeia_de_pais` (do pai até a raiz). Sem pai a cadeia é
    vazia e a profundidade é 1; o filho de um pedido raiz tem a cadeia `(pai,)` e profundidade 2; o neto, 3."""
    return len(cadeia_de_pais) + 1


def cadeia_de_pais(pai_id: str | None, pais: Mapping[str, str | None]) -> tuple[str, ...] | None:
    """Os ancestrais a partir de `pai_id` (ele primeiro, a raiz por último), lidos de `pais` (`id → pai_id`). `None` se a
    cadeia dá a volta (um id repetido): a linhagem gravada está corrompida e nenhum filho novo entra nela. `pais` sem o
    id (pedido apagado) termina a cadeia ali."""
    cadeia: list[str] = []
    atual = pai_id
    while atual is not None:
        if atual in cadeia:
            return None
        cadeia.append(atual)
        atual = pais.get(atual)
    return tuple(cadeia)


def na_linhagem(a: str, b: str, pais: Mapping[str, str | None]) -> bool:
    """`a` e `b` são o mesmo pedido, ou um descende do outro (a linhagem é a cadeia de `pai_id`). É a pergunta da regra
    "quem delega não recebe delegação da própria linhagem": um pedido não pode delegar a um ancestral seu, nem receber de
    um descendente seu, senão a delegação volta para quem a fez."""
    if a == b:
        return True
    return b in (cadeia_de_pais(a, pais) or ()) or a in (cadeia_de_pais(b, pais) or ())


# ------------------------------------------------------------------ dependências
def fecha_ciclo(arestas_existentes: Iterable[Aresta], nova_aresta: Aresta) -> bool:
    """Se somar `nova_aresta = (de, para)` cria um ciclo: já existe um caminho de `para` até `de` (andando na direção
    `de → para` de cada aresta), ou a aresta liga o pedido a ele mesmo."""
    de, para = nova_aresta
    if de == para:
        return True
    adjacentes: dict[str, list[str]] = {}
    for origem, destino in arestas_existentes:
        adjacentes.setdefault(origem, []).append(destino)
    vistos: set[str] = set()
    pilha = [para]
    while pilha:
        atual = pilha.pop()
        if atual == de:
            return True
        if atual in vistos:
            continue
        vistos.add(atual)
        pilha.extend(adjacentes.get(atual, ()))
    return False


# ------------------------------------------------------------------ orçamento
def motivo_orcamento_do_filho(total_do_pai: float | None, gasto_do_pai: float, totais_dos_irmaos: Sequence[float | None],
                              total_do_filho: float | None) -> str | None:
    """Texto do motivo quando o orçamento do filho NÃO pode ser reservado do pai; `None` = pode.

    O `orcamento_total_usd` do filho é RESERVADO do saldo do pai e nunca soma (§9): os totais dos filhos mais o gasto
    do próprio pai não passam do total do pai. O filho tem de declarar o seu, com ou sem teto no pai: sem total, ele
    gastaria o que quisesse e a reserva não teria o que reservar (no pai sem total o teto do filho é o único freio)."""
    if total_do_filho is None:
        return "o pedido filho precisa declarar o próprio orçamento total (`orcamento_total_usd`): ele é reservado do pai"
    if total_do_pai is None:
        return None
    ja_reservado = sum(t for t in totais_dos_irmaos if t is not None)
    comprometido = gasto_do_pai + ja_reservado + total_do_filho
    if comprometido > total_do_pai + _FOLGA_USD:
        return (f"o orçamento do pai (US$ {total_do_pai:.4f}) não comporta o do filho (US$ {total_do_filho:.4f}): "
                f"o pai já gastou US$ {gasto_do_pai:.4f} e os outros filhos reservaram US$ {ja_reservado:.4f}")
    return None


# ------------------------------------------------------------------ a validação do filho
def validar_filho(*, novo_id: str, pai: DadosDoPai | None, pai_id: str, pais: Mapping[str, str | None],
                  irmaos: Sequence[Irmao], papeis_da_familia: Sequence[str | None], papel: str | None, dependencias: Sequence[tuple[str, str]],
                  pai_dos_dependidos: Mapping[str, str | None], arestas_da_familia: Sequence[Aresta],
                  orcamento_total_usd: float | None, limites: Limites) -> Recusa | None:
    """A primeira recusa de um pedido que entra como filho de `pai_id`, ou `None`.

    * `pais`: `id → pai_id` dos ancestrais do pai (e dele), para a cadeia;
    * `irmaos`: os filhos que o pai já tem (sem o novo);
    * `papeis_da_familia`: o papel de cada pedido da árvore (a raiz e todos os descendentes), para o porta-voz único;
    * `dependencias`: `(de, tipo)` pedidos pelo novo (`para` é sempre ele);
    * `pai_dos_dependidos`: `id → pai_id` de cada `de` que EXISTE (o que falta no mapa é pedido inexistente);
    * `arestas_da_familia`: as dependências já gravadas entre pedidos da família.
    """
    if pai is None:
        return Recusa("pai_inexistente", f"O pedido pai {pai_id} não existe.", "pai_id")
    if pai.estado in ESTADOS_TERMINAIS:
        return Recusa("pai_terminal", f"O pedido pai está {pai.estado}: um pedido que terminou não ganha filhos.", "pai_id")
    cadeia = cadeia_de_pais(pai_id, pais)
    if cadeia is None or novo_id in cadeia:
        return Recusa("linhagem", "A linhagem do pedido pai dá a volta (um pedido é ancestral de si mesmo): "
                      "nenhum filho novo entra nela.", "pai_id")
    # `cadeia` já começa no pai: é a cadeia de ancestrais do pedido NOVO, e `profundidade` dá o nível dele
    if profundidade(cadeia) > limites.max_profundidade:
        return Recusa("profundidade_excedida", f"A colaboração vai até {limites.max_profundidade} níveis (pai → filhos): "
                      f"este pedido seria o nível {profundidade(cadeia)}.", "pai_id")
    if len(irmaos) + 1 > limites.max_filhos:
        return Recusa("filhos_demais", f"Um pedido tem no máximo {limites.max_filhos} filhos e este já tem {len(irmaos)}.",
                      "pai_id")
    if papel is not None and papel not in PAPEIS:
        return Recusa("papel_invalido", f"Papel desconhecido: {papel!r} (esperado um de {list(PAPEIS)}).", "papel")
    if papel == PORTA_VOZ and PORTA_VOZ in papeis_da_familia:
        return Recusa("porta_voz_duplicado", "A família já tem um porta-voz: só um pedido age para fora.", "papel")
    return _validar_dependencias(novo_id=novo_id, pai_id=pai_id, dependencias=dependencias,
                                 pai_dos_dependidos=pai_dos_dependidos, arestas=arestas_da_familia) or _validar_orcamento(
        pai, irmaos, orcamento_total_usd)


def _validar_dependencias(*, novo_id: str, pai_id: str, dependencias: Sequence[tuple[str, str]],
                          pai_dos_dependidos: Mapping[str, str | None], arestas: Sequence[Aresta]) -> Recusa | None:
    vistas: set[str] = set()
    todas = list(arestas)
    for de, tipo in dependencias:
        if tipo not in TIPOS_DE_DEPENDENCIA:
            return Recusa("tipo_de_dependencia_invalido", f"Tipo de dependência desconhecido: {tipo!r} (esperado um de "
                          f"{list(TIPOS_DE_DEPENDENCIA)}).", "dependencias")
        if de in vistas:
            return Recusa("dependencia_duplicada", f"O pedido {de} aparece duas vezes nas dependências.", "dependencias")
        vistas.add(de)
        if de == novo_id:
            return Recusa("ciclo", "Um pedido não pode depender de si mesmo.", "dependencias")
        # a MESMA família: o pai, ou um irmão (outro filho do mesmo pai). Pedido que não existe também cai aqui.
        if de != pai_id and not (de in pai_dos_dependidos and pai_dos_dependidos[de] == pai_id):
            return Recusa("dependencia_fora_da_familia", f"A dependência {de} não é do pai nem de um irmão: a "
                          "dependência só liga pedidos da mesma família.", "dependencias")
        if fecha_ciclo(todas, (de, novo_id)):
            return Recusa("ciclo", f"A dependência de {de} fecharia um ciclo.", "dependencias")
        todas.append((de, novo_id))
    return None


def _validar_orcamento(pai: DadosDoPai, irmaos: Sequence[Irmao], total_do_filho: float | None) -> Recusa | None:
    motivo = motivo_orcamento_do_filho(pai.orcamento_total_usd, pai.gasto_usd, [i.orcamento_total_usd for i in irmaos],
                                       total_do_filho)
    return Recusa("orcamento_do_pai", motivo, "orcamento_total_usd") if motivo else None


def validar_raiz(*, papel: str | None, dependencias: Sequence[tuple[str, str]]) -> Recusa | None:
    """Pedido SEM pai: papel é permitido (o pai também pode ser porta-voz), dependência não: não há família para
    depender de ninguém."""
    if papel is not None and papel not in PAPEIS:
        return Recusa("papel_invalido", f"Papel desconhecido: {papel!r} (esperado um de {list(PAPEIS)}).", "papel")
    if dependencias:
        return Recusa("dependencia_fora_da_familia", "Um pedido sem pai não tem família: informe `pai_id` para depender "
                      "de um irmão ou do pai.", "dependencias")
    return None
