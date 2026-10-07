"""A latência por estágio e por alvo da operação (métrica de primeira classe do dono, ao lado de custo e sucesso).

Derivada das horas que `estagios.derivar` já devolve, sem gravar nada. A etapa de um estágio é o tempo desde o evento
ANTERIOR no tempo (não na ordem do dono: o rascunho sai depois de a interface de comentário abrir, e a ordem fixa os
põe ao contrário). Os eventos são a criação da operação, as horas dos estágios do alvo e o liberar. O liberar entra
como evento para a espera pela pessoa (do pedido de aprovação ao liberar) não contar como latência da ação executada:
ela sai à parte, em `espera_do_liberar_ms`.

Puro: sem banco, sem relógio. Hora que não se lê (texto vazio ou fora do ISO) não entra, e a etapa dela fica nula.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from .estagios import ordem


def _instante(hora: str | None) -> datetime | None:
    if not hora:
        return None
    try:
        return datetime.fromisoformat(hora.replace("Z", "+00:00"))
    except ValueError:
        return None


def _ms(depois: datetime, antes: datetime) -> int:
    return max(0, round((depois - antes).total_seconds() * 1000))


@dataclass(frozen=True, slots=True)
class LatenciaDoAlvo:
    #: Por estágio, na ordem de `estagios`: ms desde o evento anterior no tempo (None = hora ilegível).
    etapas_ms: tuple[int | None, ...]
    #: Da criação da operação ao último estágio alcançado.
    duracao_ms: int | None
    #: Do pedido de aprovação (a ação preparada) ao liberar; None sem liberar.
    espera_do_liberar_ms: int | None


def do_alvo(estagios: Sequence[tuple[str, str]], criado_em: str, liberado_em: str | None = None,
            abertura: str = "app_aberto") -> LatenciaDoAlvo:
    inicio = _instante(criado_em)
    liberado = _instante(liberado_em)
    # (instante, desempate pela ordem do dono, índice do estágio ou -1 para o liberar)
    eventos: list[tuple[datetime, int, int]] = []
    for i, (estagio, hora) in enumerate(estagios):
        quando = _instante(hora)
        if quando is not None:
            eventos.append((quando, ordem(estagio, abertura), i))
    if liberado is not None:
        # O liberar vem logo antes da ação executada: no empate de hora, desempata antes dela.
        eventos.append((liberado, ordem("acao_executada", abertura) - 1, -1))
    eventos.sort()
    etapas: list[int | None] = [None] * len(estagios)
    anterior = inicio
    for quando, _, i in eventos:
        if i >= 0:
            etapas[i] = _ms(quando, anterior) if anterior is not None else None
        anterior = quando if anterior is None else max(anterior, quando)
    alcancados = [q for q, _, i in eventos if i >= 0]
    duracao = _ms(max(alcancados), inicio) if alcancados and inicio is not None else None
    preparada = next((_instante(h) for e, h in estagios if e == "acao_preparada"), None)
    espera = _ms(liberado, preparada) if liberado is not None and preparada is not None else None
    return LatenciaDoAlvo(etapas_ms=tuple(etapas), duracao_ms=duracao, espera_do_liberar_ms=espera)


def _pct(ordenados: Sequence[int], p: float) -> int:
    """Percentil pelo posto mais próximo (o mesmo de `scripts/latencia-por-etapa.py`)."""
    return ordenados[min(len(ordenados) - 1, max(0, round(p * (len(ordenados) - 1))))]


def por_estagio(alvos: Iterable[Mapping[str, object]]) -> dict[str, dict[str, int]]:
    """{estagio: {n, p50_ms, p95_ms, max_ms}} entre os alvos, de `alvos[*].estagios[*].etapa_ms`."""
    juntas: dict[str, list[int]] = {}
    for a in alvos:
        lista = a.get("estagios")
        for e in lista if isinstance(lista, list) else ():
            ms = e.get("etapa_ms") if isinstance(e, Mapping) else None
            if isinstance(ms, int) and not isinstance(ms, bool):
                juntas.setdefault(str(e["estagio"]), []).append(ms)
    saida: dict[str, dict[str, int]] = {}
    for estagio, valores in juntas.items():
        valores.sort()
        saida[estagio] = {"n": len(valores), "p50_ms": _pct(valores, 0.5), "p95_ms": _pct(valores, 0.95),
                          "max_ms": valores[-1]}
    return saida
