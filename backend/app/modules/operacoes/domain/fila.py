"""A fila por aparelho de um alvo pendente (31.206, adendo v1.114): a posição e a previsão de início que a Portal mostra.

O aparelho atende um objetivo por vez. A ordem é a do despacho (`Repository.dispatchable_objectives`): o que já roda
primeiro; depois a maior `prioridade` e, entre iguais, a execução mais antiga. Conta o trabalho aberto de QUALQUER
operação ou execução avulsa no mesmo aparelho.

A previsão é estimativa e se diz assim: `a_frente` vezes a duração mediana de trabalho dos alvos desta operação que já
terminaram (do estágio `aparelho` ao último alcançado). Sem nenhum terminado, não há amostra: a previsão é `None`, nunca
um número inventado. Puro: sem banco e sem relógio (o `agora` vem de quem chama).
"""
from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from .latencia import _instante


@dataclass(frozen=True, slots=True)
class Trabalho:
    """Um objetivo aberto num aparelho: o que pode estar na frente de um alvo."""

    run_id: str
    rodando: bool
    prioridade: int
    criado_em: str


def a_frente(run_id: str, prioridade: int, criado_em: str, trabalhos: Sequence[Trabalho]) -> int:
    """Quantos trabalhos do mesmo aparelho passam antes desta execução (o dela não conta)."""
    return sum(1 for t in trabalhos if t.run_id != run_id and (
        t.rodando or t.prioridade > prioridade or (t.prioridade == prioridade and t.criado_em < criado_em)))


def base_ms(alvos: Sequence[Mapping[str, object]]) -> int | None:
    """A duração mediana de trabalho (do `aparelho` ao último estágio) dos alvos que já terminaram, ou None."""
    duracoes = []
    for a in alvos:
        if a.get("estado") not in ("concluido", "bloqueado"):
            continue
        lista = a.get("estagios")
        horas = {str(e.get("estagio")): _instante(str(e.get("em") or "")) for e in lista
                 if isinstance(e, Mapping)} if isinstance(lista, list) else {}
        inicio = horas.get("aparelho")
        fins = [h for h in horas.values() if h is not None]
        if inicio is not None and fins:
            duracoes.append(round((max(fins) - inicio).total_seconds() * 1000))
    return round(statistics.median(duracoes)) if duracoes else None


def previsao(agora: datetime, frente: int, base: int | None) -> str | None:
    if base is None:
        return None
    return (agora + timedelta(milliseconds=frente * base)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
