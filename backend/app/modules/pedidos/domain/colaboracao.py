"""Colaboração entre pedidos, a ESTRUTURA (item 28.10, fatia F1; `docs/design/pedidos-persistentes.md` §9).

Um pedido pai com sub-pedidos (`pai_id`), dependências entre eles (`pedido_dependencias(de, para, tipo)`, migração 096)
e um papel opcional por pedido. Este módulo decide só se a ESTRUTURA é válida; quem grava e quem lê é a API
(`infrastructure/servico.py`). O laço de ocorrências NÃO olha para nada daqui na F1; a F2 (fim deste módulo) é o que ele lê:
a dependência que segura a ocorrência `devida` e a reserva dos filhos no orçamento do pai. A F3 é o teto de autonomia de
cada papel (`TETO_DO_PAPEL`): a API recusa o pedido acima dele e o laço decide com o mais restrito dos dois.

As recusas voltam como `Recusa(codigo, mensagem, campo)`, com o mesmo formato do `Bloqueio` da prévia: a prévia mostra o
que a criação devolveria (422 com `code`). A ordem das conferências é a ordem em que a pessoa corrige: primeiro o pai,
depois o tamanho da árvore, depois as dependências, por último o dinheiro.

Direção da dependência: `(de, para)` quer dizer "`para` depende de `de`". A seta vai de quem vem antes para quem espera.

Puro: stdlib e `estados` (o vocabulário da ocorrência). Sem banco, sem relógio.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.modules.pedidos.domain.estados import AUTONOMIAS, OCORRENCIA_TERMINAIS

#: O que cada pedido da família faz (§9). `porta_voz` é o único que age para fora, e só há um por família.
PAPEIS: tuple[str, ...] = ("pesquisador", "checador", "redator", "porta_voz")
PORTA_VOZ = "porta_voz"
#: O teto de autonomia de cada papel (F3, §9): quem pesquisa ou checa só observa, o redator prepara (efeito vira rascunho
#: com aprovação) e só o porta-voz pode agir. Sem papel, vale a autonomia do pedido.
TETO_DO_PAPEL: Mapping[str, str] = {"pesquisador": "observar", "checador": "observar", "redator": "preparar",
                                    "porta_voz": "agir"}
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


# ------------------------------------------------------------------ F2: a dependência no despacho
#: Estados da ocorrência do `de` que valem como "comprovado" para cada tipo. `precisa_de_resultado` só aceita `concluida`:
#: falha ou incerteza nunca contam como sucesso. `depois_de` só pede a ORDEM: qualquer fim serve (o `para` não usa o que o
#: `de` produziu, só não pode vir antes dele).
ESTADOS_QUE_COMPROVAM: Mapping[str, tuple[str, ...]] = {
    "precisa_de_resultado": ("concluida",),
    "depois_de": OCORRENCIA_TERMINAIS,
}
#: Prefixo do motivo da ocorrência `pulada` pela espera vencida. Só o id do pedido `de` o segue: nunca título, objetivo
#: nem texto de comando (o motivo é lido no painel e vai a avisos).
MOTIVO_DEPENDENCIA = "dependência não comprovada: "


def inicio_da_janela(fim_da_ultima_terminada: datetime | None, criado_em: datetime) -> datetime:
    """Onde começa a janela do filho: o fim da ocorrência anterior que terminou (qualquer estado terminal) ou, sem
    nenhuma, a criação do pedido. A prova do `de` só vale se for DEPOIS disto: o resultado que o filho já consumiu (ou
    deixou passar) numa ocorrência anterior não vale de novo para a seguinte."""
    return fim_da_ultima_terminada if fim_da_ultima_terminada is not None else criado_em


def pendentes(dependencias: Iterable[tuple[str, str]], fins_por_de: Mapping[str, Mapping[str, datetime | None]],
              inicio: datetime) -> list[str]:
    """Os ids dos pedidos `de` sem prova na janela que abre em `inicio`, em ordem alfabética (estável para o motivo).

    * `dependencias`: `(de, tipo)` do pedido que espera;
    * `fins_por_de[de][tipo]`: o `terminada_em` MAIS RECENTE de uma ocorrência do `de` num estado que comprova aquele
      tipo (`ESTADOS_QUE_COMPROVAM`), ou `None`/ausente se não há nenhuma. É estritamente maior que `inicio` que vale:
      o que terminou no mesmo instante em que a janela abriu é o que a abriu.

    Tipo desconhecido nunca comprova (a F1 só grava os dois tipos; um valor estranho segura a ocorrência em vez de
    liberá-la)."""
    sem_prova: set[str] = set()
    for de, tipo in dependencias:
        fim = (fins_por_de.get(de) or {}).get(tipo)
        if tipo not in ESTADOS_QUE_COMPROVAM or fim is None or fim <= inicio:
            sem_prova.add(de)
    return sorted(sem_prova)


def dependencias_atendidas(dependencias: Iterable[tuple[str, str]],
                           fins_por_de: Mapping[str, Mapping[str, datetime | None]], inicio: datetime) -> bool:
    """Todo `de` está comprovado na janela (sem dependência, está). Ver `pendentes`."""
    return not pendentes(dependencias, fins_por_de, inicio)


def espera_vencida(previsto_para: datetime, agora: datetime, espera_s: float) -> bool:
    """A ocorrência esperou mais que `espera_s` desde o `previsto_para`: deixa de esperar e vira `pulada`. Estritamente
    maior: no limite exato ela ainda espera."""
    return (agora - previsto_para).total_seconds() > espera_s


def motivo_da_dependencia(de: str) -> str:
    """O motivo da `pulada` pela espera vencida: só o id do pedido `de` (ver `MOTIVO_DEPENDENCIA`)."""
    return f"{MOTIVO_DEPENDENCIA}{de}"


# ------------------------------------------------------------------ F2: a reserva dos filhos no orçamento do pai
@dataclass(frozen=True)
class FilhoNoOrcamento:
    """O que o orçamento do pai precisa saber de um filho direto, já lido do banco."""
    vivo: bool                           # não terminal: `concluido`, `encerrado` e `cancelado` não são vivos
    reservado_usd: float | None          # o `orcamento_total_usd` do filho (a reserva que saiu do pai)
    gasto_usd: float                     # o que o filho (e o que ele, por sua vez, reservou aos filhos) já consumiu


def reservado_aos_filhos(filhos: Iterable[FilhoNoOrcamento]) -> float:
    """O que o pai NÃO pode usar por causa dos filhos (F2), em US$.

    * filho VIVO: a reserva inteira (`orcamento_total_usd`), ou o que já gastou se passou dela (o gasto é dinheiro que
      saiu de verdade, e a reserva só limita o filho entre uma ocorrência e outra);
    * filho TERMINADO: só o que gastou. A reserva que ele não usou (`reservado − gasto`) volta ao saldo do pai: o gasto
      ficou, o resto foi liberado.

    Soma sem teto: é o laço que subtrai isto do saldo do pai (`orcamento.restante`)."""
    total = 0.0
    for f in filhos:
        gasto = max(0.0, f.gasto_usd)
        total += max(float(f.reservado_usd or 0.0), gasto) if f.vivo else gasto
    return total


# ------------------------------------------------------------------ F3: o papel limita a autonomia
#: O teto de quem NÃO é o porta-voz numa família que tem um (F5, §9 "para fora"): só o porta-voz toca um alvo. O pedido sem
#: papel (a raiz, ou um irmão comum) observa e consolida; efeito é do porta-voz. O redator segue com o `preparar` do papel
#: (rascunho com aprovação), que já é mais restrito que o de quem age.
TETO_FORA_DO_PORTA_VOZ = "observar"


def autonomia_efetiva(autonomia: str, papel: str | None, familia_com_porta_voz: bool = False) -> str:
    """A mais restrita entre a autonomia do pedido e o teto do papel (§6.4: o pedido nunca afrouxa; aqui o papel também
    não). Papel ou autonomia desconhecidos não afrouxam nada: devolvem a autonomia como veio.

    F5: numa família com porta-voz, quem tem papel comum (sem papel) não age fora dele: o teto é `observar`. O papel que já
    tem teto próprio (`TETO_DO_PAPEL`) mantém o dele; o porta-voz também."""
    teto = TETO_DO_PAPEL.get(papel or "")
    if teto is None and familia_com_porta_voz and papel is None:
        teto = TETO_FORA_DO_PORTA_VOZ
    if teto is None or autonomia not in AUTONOMIAS:
        return autonomia
    return min(autonomia, teto, key=AUTONOMIAS.index)


def validar_autonomia(autonomia: str, papel: str | None) -> Recusa | None:
    """`autonomia_acima_do_papel`: o pedido pede mais do que o papel dele permite. A pessoa corrige baixando a autonomia
    (ou trocando o papel, num pedido novo: o papel não muda depois de criado)."""
    teto = TETO_DO_PAPEL.get(papel or "")
    if teto is None or autonomia_efetiva(autonomia, papel) == autonomia:
        return None
    return Recusa("autonomia_acima_do_papel", f"O papel `{papel}` vai no máximo até `{teto}`; o pedido pede "
                  f"`{autonomia}`.", "autonomia")


def nota_de_rebaixamento(autonomia: str, papel: str | None, familia_com_porta_voz: bool = False) -> str | None:
    """O que a ocorrência registra quando o laço decide abaixo da autonomia gravada (pedido do legado, ou gravado com a
    colaboração desligada): só os nomes do papel e das autonomias, nunca o texto do pedido."""
    efetiva = autonomia_efetiva(autonomia, papel, familia_com_porta_voz)
    if efetiva == autonomia:
        return None
    if papel is None:
        return f"autonomia rebaixada: a família tem porta-voz e só ele age para fora: {autonomia} → {efetiva}"
    return f"autonomia rebaixada ao teto do papel {papel}: {autonomia} → {efetiva}"
