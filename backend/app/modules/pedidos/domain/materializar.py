"""Materialização da ocorrência: janela de recuperação e coalescência (docs/design/pedidos-laco.md §2.2; §7.5 do
`pedidos-persistentes.md`).

O laço (`application/laco.py`) gera os instantes devidos de um gatilho (`recorrencia.proximas`, hora única, ativação) e
entrega a lista a `materializar`, que devolve, para cada um, o estado em que a ocorrência NASCE e por quê. A pergunta
que esta função responde é só: "dado o relógio de agora, o que fazer com o instante que já passou?". Nada some em
silêncio: o instante fora da janela vira `perdida` com motivo, o coalescido vira `pulada` com motivo.

Regras (as mesmas do desenho, linha a linha):

* `i > agora` → `prevista` (ainda não é a hora);
* atraso maior que a janela `J` → `perdida`;
* o resto são candidatas: com `coalescer`, só a de maior `i` nasce `devida` e as outras `pulada`; sem ele, todas
  `devida` (a sobreposição, `sobreposicao.py`, decide o despacho);
* origem `agenda` se o atraso cabe em duas voltas do laço, senão `recuperacao`. A CHAVE é a mesma nos dois casos
  (`chave.py`): recuperar a ocorrência atrasada é a ocorrência do instante original, não uma segunda.

**Segundo cheio (A5).** `agora` é truncado ao segundo ANTES de qualquer conta, como `formatar_instante` faz com o
`previsto_para` gravado: o instante `12:00:00` já é devido às `12:00:00.500`. Comparar com o relógio sem truncar (ou
como texto, onde `...:00Z` é MAIOR que `...:00.500Z`) deixaria a ocorrência `prevista` por mais uma volta.

Puro: stdlib, `chave.formatar_instante` e o vocabulário de `estados.py`. Sem banco, sem relógio de parede.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.modules.pedidos.domain.chave import formatar_instante

#: Padrão de janela quando o §7.5 não diz outro: recorrência de período de um dia ou mais, `horario` sem efeito e
#: `agora` (decisão D7 do 28.4). Configurável em `pedidos.janela_padrao_s`.
JANELA_PADRAO_S = 1800

#: Período nominal por frequência da regra, em segundos. Mês de 30 dias: é só para escolher o padrão da janela.
_SEGUNDOS_POR_FREQUENCIA = {"HOURLY": 3600, "DAILY": 86_400, "WEEKLY": 7 * 86_400, "MONTHLY": 30 * 86_400}
_UM_DIA_S = 86_400


@dataclass(frozen=True)
class Decisao:
    """Como UM instante nasce: o estado inicial da ocorrência, a origem e o motivo (obrigatório em pulada/perdida)."""
    instante: datetime
    estado: str          # prevista | devida | pulada | perdida (`OCORRENCIA_NASCE_EM`)
    origem: str          # agenda | recuperacao
    motivo: str | None = None


def truncar(instante: datetime) -> datetime:
    """O instante no segundo cheio (fração descartada: piso, nunca arredondamento), como `formatar_instante`."""
    return instante.replace(microsecond=0)


def periodo_nominal_s(frequencia: str, intervalo: int = 1) -> int:
    """FREQ × INTERVAL em segundos. Com BYHOUR/BYMINUTE a lacuna real pode ser menor (aceito; `janela_recuperacao_s`
    do pedido existe para quem quiser fixar)."""
    return _SEGUNDOS_POR_FREQUENCIA[frequencia] * max(1, int(intervalo))


def janela_padrao_s(tipo: str, *, autonomia: str, periodo_s: int | None = None,
                    padrao_s: int = JANELA_PADRAO_S) -> int:
    """O padrão por tipo de gatilho (§7.5), para o pedido que não fixou `janela_recuperacao_s`.

    `recorrencia`: período de um dia ou mais → `padrao_s`; menor → metade do período (HOURLY INTERVAL=n dá
    n × 1800 s). `horario` com autonomia `agir` → 0 (uma hora marcada que passou com efeito externo não se repete
    depois); sem efeito e `agora` → `padrao_s`.
    """
    if tipo == "recorrencia" and periodo_s is not None:
        return padrao_s if periodo_s >= _UM_DIA_S else periodo_s // 2
    if tipo == "horario" and autonomia == "agir":
        return 0
    return padrao_s


def materializar(instantes: Sequence[datetime], *, agora: datetime, janela_s: int, coalescer: bool,
                 tick_s: float) -> list[Decisao]:
    """Uma `Decisao` por instante, na ordem crescente. `instantes` são aware (UTC), sem repetição."""
    agora = truncar(agora)
    ordenados = sorted({truncar(i) for i in instantes})
    prontas: list[tuple[datetime, str, float]] = []        # (instante, origem, atraso) das candidatas
    saida: dict[datetime, Decisao] = {}
    for i in ordenados:
        atraso = (agora - i).total_seconds()
        origem = "agenda" if atraso <= 2 * tick_s else "recuperacao"
        if atraso < 0:
            saida[i] = Decisao(i, "prevista", "agenda")
        elif atraso > janela_s:
            saida[i] = Decisao(i, "perdida", origem,
                               f"fora da janela de recuperação: atraso de {int(atraso)}s > {int(janela_s)}s")
        else:
            prontas.append((i, origem, atraso))
    if prontas:
        ultima = prontas[-1][0]
        for i, origem, _ in prontas:
            if coalescer and i != ultima:
                saida[i] = Decisao(i, "pulada", origem, f"coalescida na de {formatar_instante(ultima)}")
            else:
                saida[i] = Decisao(i, "devida", origem)
    return [saida[i] for i in ordenados]
