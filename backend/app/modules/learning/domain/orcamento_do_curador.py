"""O curador por IA, a parte de DOMÍNIO do 30.11 (`aprendizado-vivo.md` §8.6-8.7): os gatilhos, a ordem de
prioridade e o orçamento proporcional que decide QUEM vai à IA nesta volta. Puro: recebe números já lidos e devolve
a partilha; quem lê o banco, monta o dossiê e chama a porta `CuradorDeIA` é a aplicação (`application/curador.py`).

O orçamento (decisão do dono, 02/10) é proporcional ao uso, sem teto fixo em US$:
`B_W = min(α·G_W, k·N_W·c̄)`, janela móvel de W dias. `G_W` é o gasto de IA da OPERAÇÃO na janela (sem a curadoria);
`N_W` são os itens que passaram pelos filtros determinísticos; `c̄` é o custo médio MEDIDO por revisão e, antes da
primeira medida, a estimativa pelo tamanho do dossiê. A estimativa só DECIDE se cabe: nunca é gravada como custo
(o custo real é do hub, 30.12).

Quando o orçamento acaba, a ordem é estrita (o que vem depois de um corte também é cortado, mesmo que caiba: uma
revisão barata de prioridade baixa não passa na frente de uma cara de prioridade alta):
1. conflito ou evidência contra em item publicado B ou C (inclui `degradando` e `obsoleto_provavel` de publicado);
2. classe C; 3. falha recorrente do backlog; 4. classe B; 5. classe A, só com sobra (`ia_permitida(A) = so_com_sobra`).

Salvaguardas relativas: gasto da última hora ≤ `B_W / W / 2`; entrada do dia > 3× a média diária da janela → só as
prioridades 1 e 2 (e alerta, de quem chama); revisão mais cara que `c_max = m × mediana` é recusada (`recusada:custo`).
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from statistics import fmean, median

from app.modules.learning.domain.politica_de_risco import ClasseDeRisco, ia_permitida


class Gatilho(StrEnum):
    """Por que o item chegou ao curador (§8.6; vai a `learning_reviews.gatilho`). Vocabulário fechado."""

    NOVA_PENDENCIA_DO_DONO = "nova_pendencia_do_dono"
    A_REVISAR = "a_revisar"
    DEGRADANDO = "degradando"
    OBSOLETO_PROVAVEL = "obsoleto_provavel"
    VERSAO_NOVA = "versao_nova"
    CONFLITO = "conflito"
    GRUPO_DE_FALHA_ACIMA_DO_MINIMO = "grupo_de_falha_acima_do_minimo"
    PEDIDO_DA_PESSOA = "pedido_da_pessoa"


#: Os gatilhos que, num item PUBLICADO, são "conflito ou evidência contra" (prioridade 1).
GATILHOS_CONTRA_PUBLICADO = frozenset({Gatilho.DEGRADANDO, Gatilho.OBSOLETO_PROVAVEL, Gatilho.CONFLITO})
#: Quando o mesmo item chega por dois gatilhos, fica o mais forte (a ordem desta tupla).
FORCA_DO_GATILHO: tuple[Gatilho, ...] = (
    Gatilho.PEDIDO_DA_PESSOA, Gatilho.CONFLITO, Gatilho.OBSOLETO_PROVAVEL, Gatilho.DEGRADANDO,
    Gatilho.GRUPO_DE_FALHA_ACIMA_DO_MINIMO, Gatilho.VERSAO_NOVA, Gatilho.NOVA_PENDENCIA_DO_DONO, Gatilho.A_REVISAR)


class Prioridade(IntEnum):
    CONTRA_EM_PUBLICADO = 1
    CLASSE_C = 2
    FALHA_RECORRENTE = 3
    CLASSE_B = 4
    CLASSE_A = 5


#: No pico de entrada, só estas passam (§8.7).
PRIORIDADES_NO_PICO = frozenset({Prioridade.CONTRA_EM_PUBLICADO, Prioridade.CLASSE_C})
FATOR_DO_PICO = 3.0


class MotivoDoCorte(StrEnum):
    """Por que um item elegível NÃO foi à IA nesta volta. Motivo PRÓPRIO do aprendizado, distinto do `fatia_curador`
    do hub. Não vira linha em `learning_reviews` (a chave única (item, dossiê) seria gasta e a sobra não voltaria na
    próxima janela, §8.7): fica no resultado da volta e no log."""

    ORCAMENTO_DA_JANELA = "orcamento_da_janela"
    GASTO_DA_HORA = "gasto_da_hora"
    PICO_DE_ENTRADA = "pico_de_entrada"
    LOTE_INTERROMPIDO = "lote_interrompido"         # o hub recusou por orçamento (`budget`): o lote para, sem laço
    ERRO_DO_PROVEDOR = "erro_do_provedor"           # outra falha do provedor: tenta de novo numa volta seguinte


def mais_forte(gatilhos: Iterable[Gatilho]) -> Gatilho:
    presentes = set(gatilhos)
    return next(g for g in FORCA_DO_GATILHO if g in presentes)


def prioridade(classe: ClasseDeRisco, gatilho: Gatilho, *, publicado: bool) -> Prioridade:
    """A classe A é sempre a última, mesmo publicada e contestada: a IA só opina sobre ela com sobra (decisão do dono,
    02/10; `ia_permitida(A) = so_com_sobra`) e quem a rebaixa é a regra determinística de hoje."""
    if ia_permitida(classe) == "so_com_sobra":
        return Prioridade.CLASSE_A
    if publicado and gatilho in GATILHOS_CONTRA_PUBLICADO:
        return Prioridade.CONTRA_EM_PUBLICADO
    if classe is ClasseDeRisco.C:
        return Prioridade.CLASSE_C
    if gatilho is Gatilho.GRUPO_DE_FALHA_ACIMA_DO_MINIMO:
        return Prioridade.FALHA_RECORRENTE
    if classe is ClasseDeRisco.B:
        return Prioridade.CLASSE_B
    return Prioridade.CLASSE_A


# ------------------------------------------------------------------ custo estimado (só para DECIDIR)
#: Bytes por token na estimativa do dossiê (JSON em português; conservador para cima).
BYTES_POR_TOKEN = 3.0
#: Tokens de saída supostos por revisão: a resposta é escolha entre rótulos fechados e uma conclusão curta.
TOKENS_DE_SAIDA = 400


def preco_mais_caro(precos: dict[str, list[float]]) -> tuple[float, float]:
    """(entrada, saída) em US$ por milhão de tokens: o MAIS CARO da tabela, a mesma regra conservadora de
    `planning/costs.py` para modelo sem preço (o curador não sabe qual modelo o hub vai usar). Tabela vazia: zero."""
    validos = [p for p in precos.values() if len(p) >= 4]
    if not validos:
        return 0.0, 0.0
    return max(float(p[0]) for p in validos), max(float(p[3]) for p in validos)


def estimar_custo(tamanho_em_bytes: int, precos: dict[str, list[float]]) -> float:
    entrada, saida = preco_mais_caro(precos)
    tokens = max(0, tamanho_em_bytes) / BYTES_POR_TOKEN
    return (tokens * entrada + TOKENS_DE_SAIDA * saida) / 1_000_000


# ------------------------------------------------------------------ o orçamento da janela
@dataclass(frozen=True, slots=True)
class ParametrosDoOrcamento:
    alfa: float = 0.10
    k: float = 1.5
    janela_dias: int = 7
    m_cmax: float = 4.0


@dataclass(frozen=True, slots=True)
class Janela:
    """O que já aconteceu na janela, lido pela infraestrutura."""

    gasto_da_operacao: float                 # G_W: `learning_daily.usd` (sem a curadoria)
    custos_medidos: tuple[float, ...] = ()   # `learning_reviews.usd` MEDIDO (> 0) por revisão; vazio até o 30.12
    gasto_da_curadoria: float = 0.0          # C_W: o medido, ou a estimativa pelo dossiê gravado quando não há medida
    gasto_da_ultima_hora: float = 0.0        # a mesma conta, só na última hora
    revisoes_antes_de_hoje: int = 0          # linhas da janela antes de hoje: a base da média diária do pico
    revisoes_de_hoje: int = 0


@dataclass(frozen=True, slots=True)
class Pretendente:
    chave: str                               # a ref do item na trilha
    prioridade: Prioridade
    custo_estimado: float
    desempate: str = ""                      # ordem estável dentro da mesma prioridade


@dataclass(frozen=True, slots=True)
class Partilha:
    aprovados: tuple[str, ...]
    cortados: dict[str, MotivoDoCorte] = field(default_factory=dict)
    recusados_por_custo: tuple[str, ...] = ()
    orcamento: float = 0.0                   # B_W
    custo_medio: float = 0.0                 # c̄ usado
    custo_maximo: float = 0.0                # c_max usado
    teto_da_hora: float = 0.0
    pico: bool = False


def custo_medio(custos_medidos: Sequence[float], estimativas: Sequence[float]) -> float:
    if custos_medidos:
        return fmean(custos_medidos)
    return fmean(estimativas) if estimativas else 0.0


def custo_maximo(custos_medidos: Sequence[float], estimativas: Sequence[float], m: float) -> float:
    """`c_max = m × mediana(c_rev)` da janela; antes da primeira medida, a mediana das estimativas desta volta."""
    base = custos_medidos or estimativas
    return m * median(base) if base else 0.0


def orcamento_da_janela(gasto_da_operacao: float, n: int, c_barra: float, p: ParametrosDoOrcamento) -> float:
    """Sem operação (G_W = 0) não há curadoria paga: B_W = 0."""
    return max(0.0, min(p.alfa * gasto_da_operacao, p.k * n * c_barra))


def e_pico(entrada_de_hoje: int, janela: Janela, p: ParametrosDoOrcamento) -> bool:
    """Entrada do dia acima de 3× a média diária da janela. Sem histórico (média 0), nunca é pico: senão o primeiro dia
    seria sempre pico."""
    media = janela.revisoes_antes_de_hoje / max(1, p.janela_dias - 1)
    return media > 0 and entrada_de_hoje > FATOR_DO_PICO * media


def repartir(pretendentes: Sequence[Pretendente], janela: Janela, p: ParametrosDoOrcamento) -> Partilha:
    """Quem vai à IA nesta volta, na ordem de prioridade, e por que cada um dos outros ficou de fora."""
    estimativas = [x.custo_estimado for x in pretendentes]
    c_barra = custo_medio(janela.custos_medidos, estimativas)
    # N_W: o que já chegou à curadoria na janela (as revisões gravadas) mais os elegíveis desta volta.
    n_w = janela.revisoes_antes_de_hoje + janela.revisoes_de_hoje + len(pretendentes)
    b = orcamento_da_janela(janela.gasto_da_operacao, n_w, c_barra, p)
    c_max = custo_maximo(janela.custos_medidos, estimativas, p.m_cmax)
    teto_hora = b / max(1, p.janela_dias) / 2
    pico = e_pico(janela.revisoes_de_hoje + len(pretendentes), janela, p)
    gasto, hora = janela.gasto_da_curadoria, janela.gasto_da_ultima_hora
    aprovados: list[str] = []
    cortados: dict[str, MotivoDoCorte] = {}
    recusados: list[str] = []
    parou: MotivoDoCorte | None = None
    for x in sorted(pretendentes, key=lambda x: (x.prioridade, x.desempate, x.chave)):
        if pico and x.prioridade not in PRIORIDADES_NO_PICO:
            cortados[x.chave] = MotivoDoCorte.PICO_DE_ENTRADA
            continue
        if parou is not None:
            cortados[x.chave] = parou
            continue
        if x.custo_estimado > c_max:
            recusados.append(x.chave)       # não se repete até o dossiê mudar (vira linha `recusada:custo`)
            continue
        if gasto + x.custo_estimado > b:
            parou = cortados[x.chave] = MotivoDoCorte.ORCAMENTO_DA_JANELA
            continue
        if hora + x.custo_estimado > teto_hora:
            parou = cortados[x.chave] = MotivoDoCorte.GASTO_DA_HORA
            continue
        aprovados.append(x.chave)
        gasto += x.custo_estimado
        hora += x.custo_estimado
    return Partilha(tuple(aprovados), cortados, tuple(recusados), orcamento=b, custo_medio=c_barra,
                    custo_maximo=c_max, teto_da_hora=teto_hora, pico=pico)


__all__ = ["BYTES_POR_TOKEN", "FATOR_DO_PICO", "FORCA_DO_GATILHO", "GATILHOS_CONTRA_PUBLICADO", "PRIORIDADES_NO_PICO",
           "TOKENS_DE_SAIDA", "Gatilho", "Janela", "MotivoDoCorte", "ParametrosDoOrcamento", "Partilha", "Pretendente",
           "Prioridade", "custo_maximo", "custo_medio", "e_pico", "estimar_custo", "mais_forte", "orcamento_da_janela",
           "preco_mais_caro", "prioridade", "repartir"]
