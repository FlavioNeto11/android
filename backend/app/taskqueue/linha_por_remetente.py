"""31.340: achar a LINHA de uma lista cega pelo remetente, lido da imagem às cegas (ADR-070, ADR-009/058).

A lista de mensagens do Outlook é um `ComposeView` sem texto na árvore (`telas.yaml`, `leitura_visual`): o ator só vê as linhas pela
imagem e não consegue ligar "a mensagem do Bruno" ao `element_id` dela (prova real do P-046, 11/10: 5 `observe_screen` e um
`find_element` sem tocar na linha certa, US$ 0,17). Aqui o executor lê o REMETENTE de cada linha candidata pelo mesmo caminho da
`read_value` visual — recorte da linha, leitor que não conhece o nome procurado, concordância às cegas, triagem do ADR-009 — e devolve ao
ator só os `element_id` das linhas cujo remetente concorda com o nome pedido.

O que NÃO volta ao ator: a transcrição, o remetente das outras linhas, o assunto, a prévia. Linha com código, senha ou token na imagem
(triagem) ou ilegível é pulada e só contada; o ator não sabe qual era nem por quê. A leitura de cada linha segue as mesmas barreiras da
`ler_valor_visual` (sensível, fora do app, captura que mudou, a mesma linha nunca duas vezes na mesma tela), e o nome procurado é do
próprio ator: a pergunta "esta linha é de X?" não revela nada que ele já não tenha escrito.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..automation.conhecimento_de_telas import ConhecimentoDeTelas
from ..automation.hierarchy import UiElement, UiTree
from .saidas import LeituraVisual, LeituraVisualRecusada, descendentes_com_texto

#: Quantas linhas candidatas uma busca lê (cada leitura é uma chamada barata do leitor às cegas).
LINHAS_MAX = 8
#: A linha de uma lista ocupa quase a largura do contêiner; botões e filtros dentro dele (chips) ficam de fora.
LARGURA_MIN_DA_LINHA = 0.6
#: Altura mínima (px do aparelho) para ser uma linha inteira: a última pode aparecer cortada, uma fatia de 14 px não se lê.
ALTURA_MIN_DA_LINHA = 80
SAIDA = "remetente"

LerLinha = Callable[[UiElement], Awaitable[LeituraVisual]]


@dataclass
class LinhasAchadas:
    """O que a busca devolve. `linhas`: os elementos cujo remetente concordou com o pedido (de cima para baixo)."""

    linhas: list[UiElement] = field(default_factory=list)
    candidatas: int = 0                  # linhas candidatas na tela
    lidas: int = 0                       # linhas efetivamente lidas (teto `LINHAS_MAX`)
    puladas: int = 0                     # linhas que a triagem, a leitura ou uma barreira não deixou ler
    indisponivel: str | None = None      # por que a busca nem começou (sem região declarada, tela sem linhas)


def linhas_candidatas(arvore: UiTree, conhecimento: ConhecimentoDeTelas | None, tela: str | None) -> list[UiElement]:
    """As linhas da lista cega na tela: clicáveis, sem texto em toda a subárvore, largas e altas como uma linha, dentro do contêiner
    da região declarada para `remetente`. Vazio sem conhecimento do app ou sem região para a tela."""
    if conhecimento is None or tela is None:
        return []
    regioes = [r for r in conhecimento.regioes_visuais if r.tela == tela and SAIDA in r.saidas]
    if not regioes:
        return []
    out: list[UiElement] = []
    for e in arvore.elements:
        if not e.clickable or descendentes_com_texto(arvore, e):
            continue
        x1, y1, x2, y2 = e.bounds
        if y2 - y1 < ALTURA_MIN_DA_LINHA or x2 <= x1:
            continue
        for r in regioes:
            if not r.cobre(arvore, e, SAIDA):
                continue
            conteiner = next(c for c in arvore.elements
                             if c is not e and r._e_o_conteiner(c.resource_id)         # noqa: SLF001
                             and c.bounds[0] <= x1 and c.bounds[1] <= y1 and x2 <= c.bounds[2] and y2 <= c.bounds[3])
            if (x2 - x1) >= LARGURA_MIN_DA_LINHA * (conteiner.bounds[2] - conteiner.bounds[0]):
                out.append(e)
            break
    return sorted(out, key=lambda e: (e.bounds[1], e.bounds[0]))


async def achar_linhas_por_remetente(arvore: UiTree, conhecimento: ConhecimentoDeTelas | None, tela: str | None, *,
                                     ler: LerLinha, teto: int = LINHAS_MAX) -> LinhasAchadas:
    """Lê o remetente de cada linha candidata (de cima para baixo, até `teto`) com `ler`, que já leva o nome procurado e as barreiras
    da leitura visual: uma leitura devolvida é uma concordância; `nao_confere` (outro remetente) não conta como pulada.
    Recusas de barreira de TELA (sensível, fora do app, captura que mudou) encerram a busca; as de LINHA (triagem, ilegível, truncado,
    repetida, leitor_falhou) pulam a linha. `AIError` (orçamento, saldo) sobe: é do executor."""
    candidatas = linhas_candidatas(arvore, conhecimento, tela)
    res = LinhasAchadas(candidatas=len(candidatas))
    if not candidatas:
        res.indisponivel = "nenhuma linha lida da imagem nesta tela"
        return res
    for linha in candidatas[:max(1, teto)]:
        try:
            await ler(linha)
        except LeituraVisualRecusada as rec:
            if rec.codigo in TELA_INTEIRA:
                res.indisponivel = rec.codigo
                return res
            if rec.codigo not in SEM_LEITURA:
                res.lidas += 1
            if rec.codigo not in OUTRO_REMETENTE:
                res.puladas += 1
            continue
        res.lidas += 1
        res.linhas.append(linha)
    return res


#: Recusas que valem para a tela inteira: não adianta tentar a linha seguinte.
TELA_INTEIRA = frozenset({"desligado", "arvore_truncada", "tela_sensivel", "fora_do_app", "captura_mudou", "sem_leitor",
                          "regiao_nao_declarada"})
#: A linha foi lida e o remetente é outro (ou o leitor não concordou): não é falha, é "não é esta".
OUTRO_REMETENTE = frozenset({"nao_confere"})
#: Recusas sem leitura de fato (a linha nem chegou ao leitor).
SEM_LEITURA = frozenset({"sem_ancora", "repetida", "elemento_com_texto", "leitor_falhou"})
