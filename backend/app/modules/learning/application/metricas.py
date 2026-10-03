"""As métricas do aprendizado (30.8, §10 do desenho): `GET /api/aprendizado/metricas?app=&dias=`.

Tudo é calculado na leitura, a partir das tabelas; nada de contador novo em memória. Cada bloco da resposta é uma
linha da tabela do §10, na mesma ordem. As regras são as do resto do módulo:
- ausente é `None`, nunca zero: taxa sem amostra e tempo sem par de transições saem `None`, sempre com o `n` ao lado;
- o simulado fica fora: a evidência e a etapa de execução simulada não entram, e a revisão simulada é contada à parte
  (`simuladas`), sem entrar nas decisões nem no custo;
- a composição do livro e a saúde vêm das MESMAS funções da visão por app (`servico.livro`, `servico.publicados`):
  a métrica não tem rótulo próprio;
- a economia é a de `taskqueue/aproveitamento.py`, injetada (reaproveitada, não recalculada);
- "falhas evitadas" não é mensurável (não há contrafactual por etapa): sai o PROXY, rotulado como tal, comparando a
  taxa de falha com receita e só com IA nas etapas (`template_hash`) que tiveram as duas conduções na janela;
- o orçamento do curador é global e usa a janela DELE (`janela_dias` do config), não a do pedido.

Quebras de série conhecidas: desde o deploy 8 (03/10/2026 09:06:28Z) a etapa `app_foreground` aberta pelo executor
fecha `sem_ator` (LT-6) e sai do proxy e de `so_ia`; compare só janelas do mesmo lado.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Protocol

from app.modules.learning.application.curador import janela_do_orcamento
from app.modules.learning.application.ports import AjustesDoCurador, LeituraDaJanela
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import SYSTEM_ACTOR, SkillState
from app.modules.learning.domain.livro import apps_do_item, contagem, para_aprovar
from app.modules.learning.domain.orcamento_do_curador import (ParametrosDoOrcamento, custo_medio, estimar_custo,
                                                              orcamento_da_janela)
from app.modules.learning.domain.parecer import RevisaoGravada
from app.modules.learning.domain.saude import Rotulo
from app.modules.learning.domain.vocabulario import LivroKind
from app.util import parse_iso, to_iso

#: O aviso do custo da curadoria (§10): a 80 % do orçamento da janela.
AVISO_DO_ORCAMENTO = 0.8
#: As conduções que o proxy compara; `sem_ator` (LT-6) e `recipe+ai` contam como "com receita" só a segunda.
COM_RECEITA = frozenset({"recipe", "recipe+ai"})
SO_IA = "ai"
#: Os campos somáveis do resumo do aproveitamento, por app.
CAMPOS_DA_ECONOMIA = ("etapas", "elegiveis", "por_receita", "receita_mais_ia", "so_ia", "sem_ator", "sem_cobertura",
                      "chamadas_evitadas_estimadas", "etapas_por_receita_sem_base")
#: Os pares de tempo do §10, na ordem do ciclo.
PARES_DE_TEMPO = (("candidate", "validated"), ("validated", "published"))
_DESLIGADO = frozenset({SkillState.DISABLED.value, SkillState.DEPRECATED.value})


# ------------------------------------------------------------------ o que a infraestrutura lê
@dataclass(frozen=True, slots=True)
class TransicaoLida:
    id: int
    item_ref: str
    from_state: str | None
    to_state: str
    decided_by: str
    decided_at: str


@dataclass(frozen=True, slots=True)
class EvidenciaLida:
    """Evidência REAL; a da execução marcada como evidência inválida (30.23) já sai fora na leitura."""

    item_ref: str
    stance: str                 # for | against | conflict
    observed_at: str


@dataclass(frozen=True, slots=True)
class RevisaoLida:
    """Uma linha do curador em `learning_reviews` (o rótulo de intenção, 30.25, fica fora na leitura)."""

    scope_app: str
    validade: str
    simulated: bool
    usd: float
    decisao: str | None         # a sugestão da IA, só na revisão válida
    decisao_final: str | None
    override: bool
    #: O item revisado (30.33-C): o recorte por app inclui a revisão do fluxo multi-app nos apps dele, não só no
    #: `scope_app` (o principal, gravado na revisão).
    item_ref: str = ""


@dataclass(frozen=True, slots=True)
class ConducaoDaEtapa:
    app: str
    template_hash: str
    driven_by: str
    falhou: bool
    n: int


@dataclass(frozen=True, slots=True)
class RevisaoNaLista:
    revisao: RevisaoGravada
    app: str
    usd: float


@dataclass(frozen=True, slots=True)
class PaginaDeRevisoes:
    revisoes: tuple[RevisaoNaLista, ...]
    proximo: tuple[str, str] | None         # (criada, id) da última linha, quando há mais


class FontesDeMetricas(Protocol):
    def transicoes(self, desde: str, ate: str) -> list[TransicaoLida]: ...
    def trilhas(self, item_refs: Iterable[str], ate: str) -> dict[str, list[TransicaoLida]]: ...
    def evidencias(self, desde: str, ate: str) -> list[EvidenciaLida]: ...
    def revisoes(self, desde: str, ate: str) -> list[RevisaoLida]: ...
    def conducao(self, desde: str, ate: str) -> list[ConducaoDaEtapa]: ...
    def janela_do_curador(self, agora: datetime, dias: int) -> LeituraDaJanela: ...
    def lista_de_revisoes(self, *, app: str | None, decisao: str | None, desde: str | None, limite: int,
                          cursor: tuple[str, str] | None, tambem: Sequence[str] = ()) -> PaginaDeRevisoes: ...


# ------------------------------------------------------------------ a resposta
@dataclass(frozen=True, slots=True)
class Tempo:
    mediana_h: float | None
    p90_h: float | None
    n: int


@dataclass(frozen=True, slots=True)
class Taxa:
    a_favor: int
    contra: int
    taxa: float | None          # a_favor / (a_favor + contra); `None` sem amostra


@dataclass(frozen=True, slots=True)
class LadoDoProxy:
    etapas: int
    falhas: int
    taxa_de_falha: float | None


@dataclass(frozen=True, slots=True)
class MetricasDoAprendizado:
    app: str | None
    janela_dias: int
    desde: str
    ate: str
    itens: dict[str, dict[str, int]]                 # {tipo: {estado: n}}, a contagem do livro
    por_origem: dict[str, int]
    pendentes: int
    aprovacoes: dict[str, dict[str, int]]            # {"sistema"|"pessoa": {to_state: n}}
    curador: dict[str, object]
    refutados_depois_de_promovidos: dict[str, int]
    sucesso_depois_de_promovido: Taxa
    churn: dict[str, int]
    tempos: dict[str, Tempo]
    saude: dict[str, int]
    economia: dict[str, float] | None                # `None` quando o aproveitamento não está ligado
    falhas_evitadas_proxy: dict[str, object]
    orcamento_do_curador: dict[str, object] | None   # global; `None` sem o curador composto
    #: Transições e evidências de itens que não estão mais no livro: sem app conhecido, contadas à parte.
    sem_item: dict[str, int] = field(default_factory=dict)


# ------------------------------------------------------------------ contas puras
def tempos(trilhas: Mapping[str, Sequence[TransicaoLida]], desde: str, ate: str) -> dict[str, Tempo]:
    """Para cada par (a → b) do §10: a chegada a `b` dentro da janela, menos a última chegada a `a` antes dela, no
    mesmo item. O item sem a chegada a `a` na trilha (a receita antiga, de antes da trilha) fica fora, e o `n` diz
    quantos pares entraram."""
    horas: dict[str, list[float]] = {f"{a}_{b}": [] for a, b in PARES_DE_TEMPO}
    for linhas in trilhas.values():
        ordenadas = sorted(linhas, key=lambda t: (t.decided_at, t.id))
        for a, b in PARES_DE_TEMPO:
            inicio: str | None = None
            for t in ordenadas:
                if t.to_state == a:
                    inicio = t.decided_at
                elif t.to_state == b and inicio is not None:
                    fim, comeco = parse_iso(t.decided_at), parse_iso(inicio)
                    if desde <= t.decided_at < ate and fim is not None and comeco is not None:
                        horas[f"{a}_{b}"].append(max(0.0, (fim - comeco).total_seconds()) / 3600)
                    inicio = None
    return {k: _tempo(v) for k, v in horas.items()}


def _tempo(amostra: list[float]) -> Tempo:
    ordenada = sorted(amostra)
    return Tempo(mediana_h=_percentil(ordenada, 50), p90_h=_percentil(ordenada, 90), n=len(ordenada))


def _percentil(ordenada: list[float], p: float) -> float | None:
    """O posto mais próximo, `ceil(p·n/100)`, a mesma regra de `app/metricas.percentil` depois do K-085 — a camada de
    aplicação não importa `app.metricas` (`tests/test_arquitetura.py`), então a fórmula é a mesma e um teste de
    igualdade as prende (`test_learning_metricas.py`). Em aritmética exata: `0.9 * n` em ponto flutuante cai logo
    abaixo do inteiro, e o `round` antigo levava o ,5 ao par (n=2 no p50 dava o maior dos dois)."""
    if not ordenada:
        return None
    k = max(0, min(len(ordenada) - 1, math.ceil(Fraction(str(p)) * len(ordenada) / 100) - 1))
    return round(ordenada[k], 3)


def taxa(a_favor: int, contra: int) -> Taxa:
    total = a_favor + contra
    return Taxa(a_favor, contra, round(a_favor / total, 3) if total else None)


def proxy_de_falhas(linhas: Sequence[ConducaoDaEtapa], app: str | None) -> dict[str, object]:
    """A taxa de falha com receita × só com IA, só nas etapas que tiveram as duas conduções na janela."""
    por_etapa: dict[tuple[str, str], dict[str, list[int]]] = {}
    for x in linhas:
        if app is not None and x.app != app:
            continue
        lado = "com_receita" if x.driven_by in COM_RECEITA else "so_ia" if x.driven_by == SO_IA else None
        if lado is None:
            continue
        conta = por_etapa.setdefault((x.app, x.template_hash), {}).setdefault(lado, [0, 0])
        conta[0] += x.n
        conta[1] += x.n if x.falhou else 0
    comparaveis = [v for v in por_etapa.values() if len(v) == 2]
    lados: dict[str, object] = {}
    for lado in ("com_receita", "so_ia"):
        etapas = sum(v[lado][0] for v in comparaveis)
        falhas = sum(v[lado][1] for v in comparaveis)
        lados[lado] = LadoDoProxy(etapas, falhas, round(falhas / etapas, 3) if etapas else None)
    return {"rotulo": "proxy", "etapas_comparadas": len(comparaveis), **lados}


def orcamento(leitura: LeituraDaJanela, aj: AjustesDoCurador, precos: dict[str, list[float]]) -> dict[str, object]:
    """C_W contra B_W (§8.7) com as revisões JÁ gravadas na janela do curador. A volta soma a isso os elegíveis dela,
    então o B_W daqui é o piso do da próxima volta, e o `uso` é o teto: o aviso sai cedo, nunca tarde. Sem custo medido
    na janela, o c̄ é a média das estimativas das revisões gravadas (sem isso, B_W sairia zero só por falta de medida)."""
    janela = janela_do_orcamento(leitura, precos)
    estimativas = [estimar_custo(t, precos) for t in leitura.tamanhos_sem_medida]
    p = ParametrosDoOrcamento(alfa=aj.alfa, k=aj.k, janela_dias=aj.janela_dias, m_cmax=aj.m_cmax)
    n = janela.revisoes_antes_de_hoje + janela.revisoes_de_hoje
    c_barra = custo_medio(janela.custos_medidos, estimativas)
    b = orcamento_da_janela(janela.gasto_da_operacao, n, c_barra, p)
    uso = round(janela.gasto_da_curadoria / b, 3) if b > 0 else None
    # Os dois ramos do mínimo (validação do deploy 10: o painel chamava o que manda de "piso"). Enquanto o ramo das
    # revisões manda, o `uso` fica perto de 1/k por construção (C_W ≈ N_W·c̄): a barra não mede folga real.
    teto_alfa, pelas_revisoes = p.alfa * janela.gasto_da_operacao, p.k * n * c_barra
    return {"modo": aj.modo.value, "janela_dias": aj.janela_dias, "gasto_da_operacao": round(janela.gasto_da_operacao, 4),
            "gasto_da_curadoria": round(janela.gasto_da_curadoria, 4), "orcamento": round(b, 4),
            "teto_alfa": round(teto_alfa, 4), "pelas_revisoes": round(pelas_revisoes, 4),
            "ramo": "operacao" if teto_alfa <= pelas_revisoes else "revisoes", "k": p.k,
            "revisoes_na_janela": n, "uso": uso, "aviso": uso is not None and uso >= AVISO_DO_ORCAMENTO}


def resumo_do_curador(revisoes: Sequence[RevisaoLida]) -> dict[str, object]:
    reais = [r for r in revisoes if not r.simulated]
    validade = Counter(r.validade.split(":", 1)[0] for r in reais)
    return {"revisoes": len(reais), "simuladas": len(revisoes) - len(reais),
            "validade": {k: validade.get(k, 0) for k in ("ok", "invalida", "recusada")},
            "decisoes": dict(sorted(Counter(r.decisao for r in reais if r.decisao).items())),
            "aplicadas": sum(1 for r in reais if r.decisao_final),
            "overrides": sum(1 for r in reais if r.override),
            "usd": round(sum(r.usd for r in reais), 4)}


# ------------------------------------------------------------------ o serviço
class ServicoDeMetricas:
    def __init__(self, servico: LearningService, fontes: FontesDeMetricas, *,
                 aproveitamento: Callable[[int, datetime], Mapping[str, object]] | None = None,
                 curador: Callable[[], AjustesDoCurador] | None = None,
                 autopublicacao: Callable[[], dict[str, object]] | None = None,
                 precos: Callable[[], dict[str, list[float]]] = dict,
                 relogio: Callable[[], datetime]) -> None:
        self._servico = servico
        self._fontes = fontes
        self._aproveitamento = aproveitamento
        self._curador = curador
        self._autopublicacao = autopublicacao
        self._precos = precos
        self._relogio = relogio

    def metricas(self, *, app: str | None, dias: int) -> MetricasDoAprendizado:
        agora = self._relogio()
        desde, ate = to_iso(agora - timedelta(days=dias)), to_iso(agora)
        livro = self._servico.livro()
        app_da_ref = {e.trail_ref: e.app for e in livro.itens}
        # 30.33-C: o recorte por app é o de `apps_do_item` (o fluxo multi-app entra em cada app dele), como a visão
        # por app; o `scope_app` da revisão segue o principal e só é completado pelos apps do item.
        no_recorte = [e for e in livro.itens if app is None or app in apps_do_item(e)]
        multi = {e.trail_ref: apps_do_item(e) for e in livro.itens if e.apps}
        refs = {e.trail_ref for e in no_recorte}
        sem_item: Counter[str] = Counter()

        def do_recorte(item_ref: str, chave: str) -> bool:
            if item_ref not in app_da_ref:
                sem_item[chave] += 1
                return False
            return item_ref in refs

        transicoes = [t for t in self._fontes.transicoes(desde, ate) if do_recorte(t.item_ref, "transicoes")]
        aprovacoes: dict[str, Counter[str]] = {"sistema": Counter(), "pessoa": Counter()}
        for t in transicoes:
            if t.from_state != t.to_state:
                aprovacoes["sistema" if t.decided_by == SYSTEM_ACTOR else "pessoa"][t.to_state] += 1
        # Refutado: o publicado que foi DESLIGADO (por contradição, voto ou pessoa). O `deprecated` fica fora: é a
        # absorção pelo declarado ou a versão aposentada, não refutação.
        refutados = Counter("sistema" if t.decided_by == SYSTEM_ACTOR else "pessoa" for t in transicoes
                            if t.from_state == SkillState.PUBLISHED.value
                            and t.to_state == SkillState.DISABLED.value)

        publicados = {e.trail_ref: e for e in no_recorte if e.state is SkillState.PUBLISHED}
        a_favor = contra = 0
        contestados: set[str] = set()
        for x in self._fontes.evidencias(desde, ate):
            if not do_recorte(x.item_ref, "evidencias"):
                continue
            e = publicados.get(x.item_ref)
            if e is None or (e.state_at is not None and x.observed_at < e.state_at):
                continue
            # 30.42: a leitura já tirou o `for`/`against` que uma `invalida` corrigiu; a `forma` e a `invalida` não contam
            a_favor += x.stance == "for"
            if x.stance in ("against", "conflict"):
                contra += 1
                contestados.add(x.item_ref)

        tocados = {t.item_ref for t in transicoes if t.to_state in {b for _, b in PARES_DE_TEMPO}}
        saude = Counter(s.rotulo.value for e, s in self._servico.publicados()
                        if s is not None and (app is None or app in apps_do_item(e)))
        revisoes = [r for r in self._fontes.revisoes(desde, ate)
                    if app is None or r.scope_app == app or app in multi.get(r.item_ref, ())]
        return MetricasDoAprendizado(
            app=app, janela_dias=dias, desde=desde, ate=ate,
            itens=contagem(no_recorte), por_origem=dict(sorted(Counter(e.origin.value for e in no_recorte).items())),
            pendentes=sum(1 for e in no_recorte if para_aprovar(e)),
            aprovacoes={k: dict(sorted(v.items())) for k, v in aprovacoes.items()},
            curador=self._do_curador(revisoes),
            refutados_depois_de_promovidos={"desligados_pelo_sistema": refutados["sistema"],
                                            "desligados_por_pessoa": refutados["pessoa"],
                                            "publicados_com_evidencia_contra": len(contestados)},
            sucesso_depois_de_promovido=taxa(a_favor, contra),
            churn={"transicoes": len(transicoes), "itens_com_transicao": len({t.item_ref for t in transicoes}),
                   "criados": sum(1 for e in no_recorte if e.state is not None and e.created_at is not None
                                  and desde <= e.created_at < ate),
                   "desligados": sum(1 for t in transicoes if t.to_state in _DESLIGADO and t.from_state != t.to_state)},
            tempos=tempos(self._fontes.trilhas(tocados, ate), desde, ate),
            saude={r.value: saude.get(r.value, 0) for r in Rotulo},
            economia=self._economia(app, dias, agora, {e.ref: apps_do_item(e) for e in livro.itens
                                                       if e.kind is LivroKind.FLUXO and e.apps}),
            falhas_evitadas_proxy=proxy_de_falhas(self._fontes.conducao(desde, ate), app),
            orcamento_do_curador=(orcamento(self._fontes.janela_do_curador(agora, (aj := self._curador()).janela_dias),
                                            aj, self._precos()) if self._curador is not None else None),
            sem_item=dict(sem_item))

    def _do_curador(self, revisoes: Sequence[RevisaoLida]) -> dict[str, object]:
        """O resumo das revisões e, com a autopublicação composta (30.34), o balanço da sombra dela, global (não
        depende do app nem da janela do pedido: o caso tem a janela própria de 7 dias). Chave nova, aditiva."""
        resumo = resumo_do_curador(revisoes)
        if self._autopublicacao is not None:
            resumo["autopublicacao"] = self._autopublicacao()
        return resumo

    def revisoes(self, *, app: str | None, decisao: str | None, desde: str | None, limite: int,
                 cursor: tuple[str, str] | None) -> PaginaDeRevisoes:
        """`GET /api/aprendizado/revisoes`: os pareceres do curador, do mais novo ao mais velho; `decisao` filtra
        pela sugestão da IA. Com `app`, entram também as revisões dos itens multi-app que usam esse app sem ser o
        principal (30.33-C), pela lista desses itens: o `scope_app` gravado continua o principal."""
        tambem = ([e.trail_ref for e in self._servico.livro().itens if app in e.apps and e.app != app]
                  if app is not None else [])
        return self._fontes.lista_de_revisoes(app=app, decisao=decisao, desde=desde, limite=limite, cursor=cursor,
                                              tambem=tambem)

    def _economia(self, app: str | None, dias: int, agora: datetime,
                  multi: Mapping[str, tuple[str, ...]] | None = None) -> dict[str, float] | None:
        if self._aproveitamento is None:
            return None
        resumo = self._aproveitamento(dias, agora)
        if app is None:
            fonte: Iterable[object] = [resumo.get("totais") or {}]
        else:
            fluxos = resumo.get("fluxos")
            # O fluxo multi-app (30.33-C) conta em cada app dele, como no recorte do livro.
            fonte = [f for f in fluxos if isinstance(f, dict) and (f.get("package") == app or app in (multi or {}).get(
                str(f.get("flow_id") or ""), ()))] if isinstance(fluxos, list) else []
        saida = dict.fromkeys(CAMPOS_DA_ECONOMIA, 0.0)
        for g in fonte:
            if isinstance(g, dict):
                for k in CAMPOS_DA_ECONOMIA:
                    v = g.get(k)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        saida[k] += v
        return {k: round(v, 1) if k == "chamadas_evitadas_estimadas" else int(v) for k, v in saida.items()}


__all__ = ["ConducaoDaEtapa", "EvidenciaLida", "FontesDeMetricas", "MetricasDoAprendizado", "PaginaDeRevisoes",
           "RevisaoLida", "RevisaoNaLista",
           "ServicoDeMetricas", "Taxa", "Tempo", "TransicaoLida", "orcamento", "proxy_de_falhas", "resumo_do_curador",
           "taxa", "tempos"]
