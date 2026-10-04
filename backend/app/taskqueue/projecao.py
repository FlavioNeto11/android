"""Projeção e orçamento por AÇÃO, medidos no histórico real (item 18.3; execução r-20260928165254-e31953).

Uma execução de 5 etapas levou 31 chamadas de IA, US$ 0,59 e 18,7 min sem que nada dissesse, antes ou durante, que
isso estava fora do normal. Aqui o normal é medido: para cada (app, ação do catálogo), as etapas CONCLUÍDAS nos
últimos N dias dão a mediana e o p90 de chamadas de IA, de segundos e de US$ por etapa. Etapa sem ação (objetivo
livre) entra como `*` do app.

Três usos, todos sem chamar IA:

* **projeção do plano**, antes de a pessoa iniciar: a soma das medianas e dos p90 das etapas, com as etapas sem base
  marcadas (menos de `minimo` amostras);
* **aviso ao vivo**, quando uma etapa passa do p90 da ação dela;
* **orçamento**, conservador: uma etapa que passa de `max(p90 × fator, p90 + folga)` chamadas para, com o motivo —
  laço descontrolado vira falha explicada, não 18 minutos girando até o prazo.

Não conhece app nenhum: agrupa pelo que as etapas e as execuções já gravam.

**A janela nunca passa da retenção de `ai_calls`** (ADR-054, decisão 8). As etapas vêm de `steps` (que não vence), mas
o custo vem de `ai_calls`, purgado em `log_retention_days` (14 por padrão): com a janela de 30 dias, a partir do 15º
dia as etapas antigas entravam com 0 chamadas e US$ 0 e a régua — projeção, aviso e orçamento — saía subestimada.
A janela EFETIVA é `min(janela_dias, log_retention_days)`, e a etapa só entra se COMEÇOU dentro dela (uma etapa que
começou antes do corte pode ter perdido as primeiras chamadas). O que precisar de mais que isso lê o agregado
durável do aprendizado (`learning_daily`). A projeção diz a janela efetiva e quantas amostras não custaram nada
(receita reproduzindo, fluxo) — número pequeno de verdade, não chamada perdida.
"""
from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ..planning import costs
from ..util import parse_iso

QUALQUER = "*"


@dataclass(slots=True)
class _Etapa:
    chave: tuple[str, str]
    segundos: float | None
    chamadas: int = 0
    usd: float = 0.0


@dataclass(frozen=True, slots=True)
class Faixa:
    p50: float
    p90: float


@dataclass(frozen=True, slots=True)
class EstatisticaDeAcao:
    app: str
    acao: str
    amostras: int
    chamadas: Faixa
    segundos: Faixa
    usd: Faixa
    sem_custo: int = 0                 # etapas da amostra que não fizeram chamada de IA nenhuma


def _pct(valores: list[float], q: float) -> float:
    """Posto mais próximo: com poucas amostras, devolve um valor que aconteceu de verdade."""
    ordenados = sorted(valores)
    return ordenados[max(0, min(len(ordenados), math.ceil(q * len(ordenados))) - 1)]


def app_da_etapa(app_id: object, app_ids: object) -> str:
    if app_id:
        return str(app_id)
    try:
        lista = json.loads(str(app_ids)) if app_ids else []
    except ValueError:
        lista = []
    return str(lista[0]) if isinstance(lista, list) and lista else QUALQUER


class HistoricoDeAcoes:
    """Estatísticas por (app, ação), relidas no máximo a cada `ttl_s` — o executor consulta a cada chamada de IA."""

    def __init__(self, db: object, prices: Callable[[], dict[str, list[float]]], *, janela_dias: int = 30,
                 ttl_s: float = 600.0, relogio: Callable[[], float] = time.monotonic,
                 retencao_dias: Callable[[], int] | None = None) -> None:
        """`retencao_dias`: o `log_retention_days` VIGENTE (muda com o processo no ar); sem ele, só a janela."""
        self._db = db
        self._prices = prices
        self.janela_configurada = janela_dias
        self._retencao = retencao_dias
        self._ttl = ttl_s
        self._relogio = relogio
        self._cache: dict[tuple[str, str], EstatisticaDeAcao] | None = None
        self._lido_em = 0.0

    @property
    def janela_dias(self) -> int:
        """A janela EFETIVA: nunca mais longa que a retenção de `ai_calls`."""
        if self._retencao is None:
            return self.janela_configurada
        return max(1, min(self.janela_configurada, int(self._retencao())))

    def estatisticas(self) -> dict[tuple[str, str], EstatisticaDeAcao]:
        agora = self._relogio()
        if self._cache is None or agora - self._lido_em > self._ttl:
            self._cache = self._ler()
            self._lido_em = agora
        return self._cache

    def de(self, app: str, acao: str | None) -> EstatisticaDeAcao | None:
        return self.estatisticas().get((app, acao or QUALQUER))

    def _ler(self) -> dict[tuple[str, str], EstatisticaDeAcao]:
        corte = _iso_dias_atras(self.janela_dias)
        linhas = self._db.query(  # type: ignore[attr-defined]
            "SELECT s.id sid, s.capability cap, s.app_id app_id, r.app_ids app_ids, s.started_at ini, "
            "s.finished_at fim, a.model modelo, count(a.id) n, COALESCE(sum(a.input_tokens), 0) i, "
            "COALESCE(sum(a.cache_read), 0) cr, COALESCE(sum(a.cache_write), 0) cw, "
            "COALESCE(sum(a.cache_write_1h), 0) cw1h, "
            "COALESCE(sum(a.output_tokens), 0) o, COALESCE(sum(a.usd), 0) usd_decl "
            "FROM steps s JOIN runs r ON r.id = s.run_id LEFT JOIN ai_calls a ON a.step_id = s.id "
            "WHERE r.simulated = 0 AND s.status = 'succeeded' AND s.finished_at >= ? "
            "AND (s.started_at IS NULL OR s.started_at >= ?) "
            "GROUP BY s.id, s.capability, s.app_id, r.app_ids, s.started_at, s.finished_at, a.model",
            (corte, corte))
        precos = self._prices()
        por_etapa: dict[str, _Etapa] = {}
        for bruta in linhas:
            linha = dict(bruta)
            etapa = por_etapa.setdefault(str(linha["sid"]), _Etapa(
                chave=(app_da_etapa(linha["app_id"], linha["app_ids"]), str(linha["cap"] or QUALQUER)),
                segundos=_segundos(linha["ini"], linha["fim"])))
            n = int(linha["n"] or 0)
            etapa.chamadas += n
            if n and linha["modelo"]:
                etapa.usd += float(linha["usd_decl"] or 0) or costs.usd(
                    precos, str(linha["modelo"]), [linha["i"], linha["cr"], linha["cw"], linha["o"]]) \
                    + costs.extra_1h(precos, str(linha["modelo"]), linha.get("cw1h"))   # 31.31
        grupos: dict[tuple[str, str], list[_Etapa]] = defaultdict(list)
        for etapa in por_etapa.values():
            grupos[etapa.chave].append(etapa)
        saida: dict[tuple[str, str], EstatisticaDeAcao] = {}
        for (app, acao), etapas in grupos.items():
            chamadas = [float(e.chamadas) for e in etapas]
            segundos = [e.segundos for e in etapas if e.segundos is not None]
            usd = [e.usd for e in etapas]
            saida[(app, acao)] = EstatisticaDeAcao(
                app=app, acao=acao, amostras=len(etapas),
                chamadas=Faixa(_pct(chamadas, 0.5), _pct(chamadas, 0.9)),
                segundos=Faixa(_pct(segundos, 0.5), _pct(segundos, 0.9)) if segundos else Faixa(0.0, 0.0),
                usd=Faixa(_pct(usd, 0.5), _pct(usd, 0.9)), sem_custo=sum(1 for e in etapas if not e.chamadas))
        return saida

    def orcamento_de_chamadas(self, app: str, acao: str | None, *, fator: float, folga: int,
                              minimo: int) -> tuple[int, EstatisticaDeAcao] | None:
        """Teto de chamadas desta etapa, ou None quando não há base suficiente (aí vale só o teto do objetivo)."""
        est = self.de(app, acao)
        if est is None or est.amostras < minimo:
            return None
        return max(math.ceil(est.chamadas.p90 * fator), int(est.chamadas.p90) + folga), est


def _iso_dias_atras(dias: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _segundos(ini: object, fim: object) -> float | None:
    a, b = parse_iso(str(ini)) if ini else None, parse_iso(str(fim)) if fim else None
    if a is None or b is None:
        return None
    return max(0.0, (b - a).total_seconds())


@dataclass(frozen=True, slots=True)
class PassoProjetado:
    chave: str
    titulo: str
    acao: str
    amostras: int
    chamadas: Faixa
    segundos: Faixa
    usd: Faixa
    sem_base: bool
    sem_custo: int = 0


def projetar(passos: Iterable[tuple[str, str, str, str]], historico: HistoricoDeAcoes, *,
             minimo: int = 5) -> dict[str, object]:
    """`passos` = (chave, título, app, ação). Etapa sem base própria usa o `*` do app e fica marcada `sem_base`;
    sem nem isso, entra com zero e marcada — a projeção nunca inventa número."""
    itens: list[PassoProjetado] = []
    for chave, titulo, app, acao in passos:
        est = historico.de(app, acao)
        sem_base = est is None or est.amostras < minimo
        if sem_base:
            reserva = historico.de(app, QUALQUER)
            est = reserva if reserva is not None and reserva.amostras >= minimo else est
        zero = Faixa(0.0, 0.0)
        itens.append(PassoProjetado(chave=chave, titulo=titulo, acao=acao or QUALQUER,
                                    amostras=est.amostras if est else 0,
                                    chamadas=est.chamadas if est else zero, segundos=est.segundos if est else zero,
                                    usd=est.usd if est else zero, sem_base=sem_base,
                                    sem_custo=est.sem_custo if est else 0))

    def soma(campo: str, q: str) -> float:
        return round(sum(float(getattr(getattr(i, campo), q)) for i in itens), 4)

    return {
        "janela_dias": historico.janela_dias, "janela_configurada": historico.janela_configurada,
        "minimo_de_amostras": minimo, "amostras_sem_custo": sum(i.sem_custo for i in itens),
        "chamadas": {"p50": soma("chamadas", "p50"), "p90": soma("chamadas", "p90")},
        "segundos": {"p50": soma("segundos", "p50"), "p90": soma("segundos", "p90")},
        "usd": {"p50": soma("usd", "p50"), "p90": soma("usd", "p90")},
        "sem_base": [i.chave for i in itens if i.sem_base],
        "etapas": [{"key": i.chave, "title": i.titulo, "action": i.acao, "samples": i.amostras,
                    "calls": {"p50": i.chamadas.p50, "p90": i.chamadas.p90},
                    "seconds": {"p50": i.segundos.p50, "p90": i.segundos.p90},
                    "usd": {"p50": round(i.usd.p50, 4), "p90": round(i.usd.p90, 4)}, "no_baseline": i.sem_base,
                    "samples_without_cost": i.sem_custo}
                   for i in itens],
    }


def resumo(projecao: dict[str, object]) -> str:
    """Uma linha para o evento do plano: "10–17 chamadas de IA, US$ 0,20–0,38, 3–7 min (2 etapas sem base)"."""
    c = projecao["chamadas"]
    u = projecao["usd"]
    s = projecao["segundos"]
    sem = projecao["sem_base"]
    assert isinstance(c, dict) and isinstance(u, dict) and isinstance(s, dict) and isinstance(sem, list)
    etapas = projecao["etapas"]
    if isinstance(etapas, list) and len(sem) == len(etapas) and not c["p90"]:
        return f"sem histórico suficiente para projetar as {len(etapas)} etapa(s) — a primeira execução mede"
    usd = f"{u['p50']:.2f}–{u['p90']:.2f}".replace(".", ",")
    texto = (f"{round(c['p50'])}–{round(c['p90'])} chamadas de IA, US$ {usd}, "
             f"{max(1, round(s['p50'] / 60))}–{max(1, round(s['p90'] / 60))} min")
    return texto + (f" ({len(sem)} etapa(s) sem base)" if sem else "")
